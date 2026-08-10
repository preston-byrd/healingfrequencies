import React, { useState, useEffect } from 'react';
import { RefreshCw, Eye, EyeOff } from 'lucide-react';
import { useAuth } from '@/contexts/AuthContext';
import { formatApiError, warmBackend } from '@/lib/api';
import ForgotPasswordModal from '@/components/ForgotPasswordModal';
import { LOGIN } from '@/constants/testIds/auth';

// Axios throws either `Network Error` (browser refused / DNS / TLS died) or
// aborts the request with `ECONNABORTED` when the 45-second timeout hits.
// Both mean "the server was never reached", i.e. safe to retry with the
// same body — as opposed to a 401 (wrong password) or 429 (rate-limited)
// where retrying is the wrong move. Only these two surfaces the Retry chip.
function isNetworkError(err) {
  if (!err) return false;
  if (err.response) return false;
  if (err.code === 'ECONNABORTED') return true;
  if (err.message === 'Network Error') return true;
  return false;
}

export default function AuthScreen() {
  const { login, register } = useAuth();
  const [mode, setMode] = useState('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [err, setErr] = useState('');
  const [canRetry, setCanRetry] = useState(false);
  const [busy, setBusy] = useState(false);
  const [showForgot, setShowForgot] = useState(false);
  // Local toggle for masking/unmasking the password input. Off by default
  // for shoulder-surf safety; a tap on the eye icon reveals the value so
  // the user can double-check what they typed on flaky mobile keyboards.
  const [showPassword, setShowPassword] = useState(false);

  // Warm the backend the moment the sign-in screen mounts so DNS + TLS
  // + any Cloudflare/cold-start latency happens while the user is
  // typing their credentials, not during the actual login POST. On
  // cellular this often turns a would-be "Network Error" (first-POST
  // race with the origin still spinning up) into a successful sign-in.
  useEffect(() => {
    warmBackend();
  }, []);

  const attempt = async () => {
    setErr('');
    setCanRetry(false);
    setBusy(true);
    try {
      if (mode === 'login') await login(email, password);
      else await register(email, password, name);
    } catch (e) {
      setErr(formatApiError(e));
      setCanRetry(isNetworkError(e));
    } finally {
      setBusy(false);
    }
  };

  const submit = (e) => {
    e.preventDefault();
    attempt();
  };

  return (
    <div className="min-h-screen flex items-center justify-center relative px-4">
      <div className="aurora-bg" />
      <div className="grain" />
      <div className="relative z-10 w-full max-w-md glass p-10">
        <div className="text-center mb-8">
          <div className="label-tiny mb-3">Healing Frequencies</div>
          <h1 className="font-display text-5xl font-light tracking-tight text-[#E8E3D9]">
            {mode === 'login' ? 'Welcome back' : 'Begin your journey'}
          </h1>
          <p className="text-[#8A9A92] mt-3 text-sm">
            {mode === 'login' ? 'Tune in. Settle down. Resonate.' : 'A quiet space to listen, breathe, and restore.'}
          </p>
        </div>

        <form onSubmit={submit} className="space-y-4">
          {mode === 'register' && (
            <div>
              <label className="label-tiny block mb-2">Name</label>
              <input
                data-testid="auth-name-input"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full bg-transparent border-b border-[rgba(92,158,140,0.25)] focus:border-[#72C2AC] outline-none py-2 text-[#E8E3D9] transition-colors"
                placeholder="Your name"
              />
            </div>
          )}
          <div>
            <label className="label-tiny block mb-2">Email</label>
            <input
              data-testid="auth-email-input"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full bg-transparent border-b border-[rgba(92,158,140,0.25)] focus:border-[#72C2AC] outline-none py-2 text-[#E8E3D9] transition-colors"
              placeholder="you@example.com"
            />
          </div>
          <div>
            <label className="label-tiny block mb-2">Password</label>
            <div className="relative">
              <input
                data-testid="auth-password-input"
                type={showPassword ? 'text' : 'password'}
                required
                minLength={6}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full bg-transparent border-b border-[rgba(92,158,140,0.25)] focus:border-[#72C2AC] outline-none py-2 pr-9 text-[#E8E3D9] transition-colors"
                placeholder="•••••••"
              />
              <button
                type="button"
                data-testid="auth-password-toggle"
                onClick={() => setShowPassword((v) => !v)}
                aria-label={showPassword ? 'Hide password' : 'Show password'}
                aria-pressed={showPassword}
                tabIndex={-1}
                className="absolute right-1 top-1/2 -translate-y-1/2 p-1.5 text-[#8A9A92] hover:text-[#C9DED6] transition-colors"
              >
                {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </div>

          {err && (
            <div className="space-y-2" data-testid="auth-error-block">
              <div data-testid="auth-error" className="text-[#D96C6C] text-sm">{err}</div>
              {canRetry && (
                <button
                  type="button"
                  onClick={attempt}
                  disabled={busy}
                  data-testid="auth-retry-button"
                  className="inline-flex items-center gap-2 text-xs uppercase tracking-[0.14em] text-[#72C2AC] hover:text-[#C4A67A] transition-colors disabled:opacity-40"
                >
                  <RefreshCw size={12} className={busy ? 'animate-spin' : ''} />
                  {busy ? 'Retrying…' : 'Retry'}
                </button>
              )}
            </div>
          )}

          <button
            data-testid="auth-submit-button"
            type="submit"
            disabled={busy}
            className="w-full mt-6 py-3 rounded-full bg-[#5C9E8C] hover:bg-[#72C2AC] text-[#08120F] font-medium tracking-wide transition-colors duration-300 disabled:opacity-50"
          >
            {busy ? 'One moment…' : mode === 'login' ? 'Sign in' : 'Create account'}
          </button>
        </form>

        {mode === 'login' && (
          <div className="mt-4 text-center text-sm">
            <button
              data-testid={LOGIN.forgotPasswordLink}
              type="button"
              onClick={() => { setErr(''); setShowForgot(true); }}
              className="text-[#8A9A92] hover:text-[#72C2AC] transition-colors"
            >
              Forgot your password?
            </button>
          </div>
        )}

        <div className="mt-6 text-center text-sm text-[#8A9A92]">
          {mode === 'login' ? "New here?" : 'Already have an account?'}{' '}
          <button
            data-testid="auth-mode-toggle"
            type="button"
            onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setErr(''); }}
            className="text-[#72C2AC] hover:text-[#C4A67A] transition-colors"
          >
            {mode === 'login' ? 'Create an account' : 'Sign in'}
          </button>
        </div>
      </div>

      {showForgot && (
        <ForgotPasswordModal
          initialEmail={email}
          onClose={() => setShowForgot(false)}
        />
      )}
    </div>
  );
}
