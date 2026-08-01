import axios from 'axios';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
export const API = `${BACKEND_URL}/api`;

// 45s ceiling so a genuinely dead backend fails eventually — but long enough
// that a mobile LTE cold-start round-trip (DNS → TLS → cold container →
// bcrypt on login) completes without prematurely showing "Network Error".
const REQUEST_TIMEOUT_MS = 45000;

const api = axios.create({
  baseURL: API,
  withCredentials: true,
  timeout: REQUEST_TIMEOUT_MS,
});

// Attach Authorization header from localStorage as a fallback.
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token');
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// One-shot retry for transient network errors (mobile LTE hiccups, cold
// starts, brief DNS failures) AND for transient edge/CDN errors (502/520/522
// = origin briefly unreachable, 503/504 = overloaded). Only retries idempotent
// requests OR the login/register endpoints — never blindly retries
// destructive mutations.
const RETRY_SAFE_METHODS = new Set(['get', 'head', 'options']);
const RETRY_ALLOWLIST_PATHS = ['/auth/login', '/auth/register', '/auth/me'];
const RETRY_EDGE_STATUSES = new Set([502, 503, 504, 520, 521, 522]);

api.interceptors.response.use(
  (r) => r,
  async (error) => {
    const cfg = error && error.config;
    const isNetwork = error && (error.code === 'ECONNABORTED' || error.message === 'Network Error');
    const status = error?.response?.status;
    const isTransientEdge = status && RETRY_EDGE_STATUSES.has(status);
    if (cfg && (isNetwork || isTransientEdge) && !cfg.__retried) {
      const method = (cfg.method || 'get').toLowerCase();
      const path = (cfg.url || '').replace(cfg.baseURL || '', '');
      const canRetry = RETRY_SAFE_METHODS.has(method) ||
        RETRY_ALLOWLIST_PATHS.some((p) => path.startsWith(p));
      if (canRetry) {
        cfg.__retried = true;
        // Slightly longer backoff for edge errors so a cold origin has
        // time to spin up before the second attempt.
        const delay = isTransientEdge ? 1400 : 800;
        await new Promise((r) => setTimeout(r, delay));
        return api.request(cfg);
      }
    }
    return Promise.reject(error);
  },
);

export default api;

export function formatApiError(err) {
  // Network-level failures (no response) get a friendlier, actionable message
  // instead of the bare "Network Error" axios throws by default.
  if (err && !err.response) {
    if (err.code === 'ECONNABORTED') {
      return 'The server took too long to respond. Please try again in a moment.';
    }
    if (err.message === 'Network Error') {
      return "We couldn't reach the server. Check your connection and try again.";
    }
  }
  // Status-code-driven fallbacks for edge/CDN failures where the response
  // body is either an HTML error page (Cloudflare 502/520 etc.) or a stub
  // FastAPI missing-route body. Without this, `err.message` (e.g. "Request
  // failed with status code 502") or a raw HTML string can leak into the UI.
  const status = err?.response?.status;
  const rawData = err?.response?.data;
  const looksLikeHtml = typeof rawData === 'string' &&
    /^\s*<(!doctype html|html|head|body)/i.test(rawData);
  if (status && (looksLikeHtml || (status >= 500 && status !== 502 ? false : false))) {
    // handled below
  }
  if (status === 502 || status === 503 || status === 504 || status === 520 || status === 521 || status === 522) {
    return "The server is having a moment — please try again shortly.";
  }
  if (status === 404 && (looksLikeHtml || typeof rawData !== 'object')) {
    return "That feature isn't available right now. Please refresh and try again.";
  }
  if (looksLikeHtml) {
    // Any other non-JSON HTML body: never leak raw markup.
    return "Something went wrong on the server. Please try again shortly.";
  }
  const d = err?.response?.data?.detail;
  if (d == null) return err?.message || 'Something went wrong';
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) return d.map((e) => e?.msg || JSON.stringify(e)).join(' ');
  if (d?.msg) return d.msg;
  return String(d);
}
