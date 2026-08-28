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
//
// Cellular login on iOS Safari is flaky enough (carrier NAT + HTTP/3 quirks
// + bcrypt latency) that /auth/login gets a 3-attempt exponential-backoff
// budget on Network Error before we surface the failure to the user. Other
// paths keep the single-shot behaviour.
const RETRY_SAFE_METHODS = new Set(['get', 'head', 'options']);
const RETRY_ALLOWLIST_PATHS = ['/auth/login', '/auth/register', '/auth/me'];
const RETRY_EDGE_STATUSES = new Set([502, 503, 504, 520, 521, 522]);
const LOGIN_MAX_ATTEMPTS = 3;         // total attempts, incl. initial
const LOGIN_BACKOFFS_MS = [500, 1500]; // between attempts 1→2, 2→3

api.interceptors.response.use(
  (r) => r,
  async (error) => {
    const cfg = error && error.config;
    const isNetwork = error && (error.code === 'ECONNABORTED' || error.message === 'Network Error');
    const status = error?.response?.status;
    const isTransientEdge = status && RETRY_EDGE_STATUSES.has(status);
    if (!cfg || (!isNetwork && !isTransientEdge)) return Promise.reject(error);
    const method = (cfg.method || 'get').toLowerCase();
    const path = (cfg.url || '').replace(cfg.baseURL || '', '');
    const isLoginPath = path.startsWith('/auth/login') || path.startsWith('/auth/register');
    const canRetry = RETRY_SAFE_METHODS.has(method) ||
      RETRY_ALLOWLIST_PATHS.some((p) => path.startsWith(p));
    if (!canRetry) return Promise.reject(error);

    // Multi-attempt path — only for pure Network Error on /auth/login|register,
    // where cellular carriers can drop the first (or second) POST before the
    // TCP/TLS/HTTP round-trip completes. Give it a small budget of 3 total
    // attempts with backoff before surrendering. Edge 5xx and non-auth paths
    // stay on the classic single-retry to avoid amplifying real outages.
    if (isNetwork && isLoginPath) {
      cfg.__loginAttempt = (cfg.__loginAttempt || 1) + 1;
      if (cfg.__loginAttempt <= LOGIN_MAX_ATTEMPTS) {
        const delay = LOGIN_BACKOFFS_MS[cfg.__loginAttempt - 2] || 1500;
        await new Promise((r) => setTimeout(r, delay));
        return api.request(cfg);
      }
      return Promise.reject(error);
    }

    // Classic single-shot retry for everything else that's retry-eligible.
    if (cfg.__retried) return Promise.reject(error);
    cfg.__retried = true;
    const delay = isTransientEdge ? 1400 : 800;
    await new Promise((r) => setTimeout(r, delay));
    return api.request(cfg);
  },
);

// Silent backend warmup — fires a very-cheap GET to /api/health so DNS,
// TLS, and any cold-start container spin-up happen while the user is
// still typing their credentials rather than during the login POST
// itself. On cellular this saves ~1–3 s on the first real request and
// often turns a would-be "Network Error" into a successful sign-in.
// Called once per browser session (see the `_warmed` guard).
let _warmed = false;
export function warmBackend() {
  if (_warmed) return;
  _warmed = true;
  try {
    api.get('/health', { timeout: 8000, __retried: true }).catch(() => { /* silent */ });
  } catch (_) { /* silent */ }
}

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
  // Some endpoints (e.g. /auth/phone/send-code) shape `detail` as a rich
  // object so the client can react to structured hints (retry-by-call,
  // support links, etc.) while still showing a friendly `message`.
  if (d?.message) return d.message;
  return String(d);
}
