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
// starts, brief DNS failures). Only retries idempotent requests OR the
// login/register endpoints — never blindly retries destructive mutations.
const RETRY_SAFE_METHODS = new Set(['get', 'head', 'options']);
const RETRY_ALLOWLIST_PATHS = ['/auth/login', '/auth/register', '/auth/me'];

api.interceptors.response.use(
  (r) => r,
  async (error) => {
    const cfg = error && error.config;
    const isNetwork = error && (error.code === 'ECONNABORTED' || error.message === 'Network Error');
    if (cfg && isNetwork && !cfg.__retried) {
      const method = (cfg.method || 'get').toLowerCase();
      const path = (cfg.url || '').replace(cfg.baseURL || '', '');
      const canRetry = RETRY_SAFE_METHODS.has(method) ||
        RETRY_ALLOWLIST_PATHS.some((p) => path.startsWith(p));
      if (canRetry) {
        cfg.__retried = true;
        // Short backoff so we don't hammer a struggling backend.
        await new Promise((r) => setTimeout(r, 800));
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
  const d = err?.response?.data?.detail;
  if (d == null) return err?.message || 'Something went wrong';
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) return d.map((e) => e?.msg || JSON.stringify(e)).join(' ');
  if (d?.msg) return d.msg;
  return String(d);
}
