import React, { useEffect, useState } from 'react';
import api, { formatApiError } from '@/lib/api';

/**
 * Reset-password landing view. Rendered when a `?reset_token=...` query
 * parameter is present in the URL. Presents a fresh-password form,
 * validates it matches the app's password rules (min 8 chars), and
 * calls POST /api/auth/reset-password. On success we strip the token
 * from the URL and hand control back to the parent (auth screen).
 *
 * Any server-side error (expired / reused / tampered token) is surfaced
 * as a clear inline message — the password is never changed unless the
 * server returns 200.
 */
export default function ResetPasswordView({ token, onDone }) {
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!token) {
      setErr('This reset link is invalid.');
    }
  }, [token]);

  const submit = async (e) => {
    e.preventDefault();
    setErr('');
    if (password.length < 8) {
      setErr('Password must be at least 8 characters.');
      return;
    }
    if (password !== confirm) {
      setErr('Passwords do not match.');
      return;
    }
    setBusy(true);
    try {
      await api.post('/auth/reset-password', { token, new_password: password });
      setDone(true);
      // Strip the token from the URL so a refresh won't re-attempt the flow
      // (and to keep the token out of browser history).
      try {
        const url = new URL(window.location.href);
        url.searchParams.delete('reset_token');
        window.history.replaceState({}, '', url.pathname + (url.search || ''));
      } catch (_) { /* noop */ }
    } catch (e) {
      setErr(formatApiError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      data-testid="reset-password-view"
      className="min-h-screen flex items-center justify-center relative px-4"
    >
      <div className="aurora-bg" />
      <div className="grain" />
      <div className="relative z-10 w-full max-w-md glass p-10">
        <div className="text-center mb-8">
          <div className="label-tiny mb-3">Solarisound</div>
          <h1 className="font-display text-4xl font-light tracking-tight text-[#E8E3D9]">
            {done ? 'Password reset' : 'Choose a new password'}
          </h1>
          <p className="text-[#8A9A92] mt-3 text-sm leading-relaxed">
            {done
              ? 'Your password has been updated. You can now sign in with your new password.'
              : 'Enter a new password below. Must be at least 8 characters.'}
          </p>
        </div>

        {!done && (
          <form onSubmit={submit} className="space-y-4">
            <div>
              <label className="label-tiny block mb-2">New password</label>
              <input
                data-testid="reset-password-new-input"
                type="password"
                required
                minLength={8}
                autoFocus
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full bg-transparent border-b border-[rgba(92,158,140,0.25)] focus:border-[#72C2AC] outline-none py-2 text-[#E8E3D9] transition-colors"
                placeholder="At least 8 characters"
              />
            </div>
            <div>
              <label className="label-tiny block mb-2">Confirm password</label>
              <input
                data-testid="reset-password-confirm-input"
                type="password"
                required
                minLength={8}
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                className="w-full bg-transparent border-b border-[rgba(92,158,140,0.25)] focus:border-[#72C2AC] outline-none py-2 text-[#E8E3D9] transition-colors"
                placeholder="Repeat your new password"
              />
            </div>

            {err && (
              <div data-testid="reset-password-error" className="text-[#D96C6C] text-sm">
                {err}
              </div>
            )}

            <button
              data-testid="reset-password-submit-button"
              type="submit"
              disabled={busy || !token}
              className="w-full mt-6 py-3 rounded-full bg-[#5C9E8C] hover:bg-[#72C2AC] text-[#08120F] font-medium tracking-wide transition-colors duration-300 disabled:opacity-50"
            >
              {busy ? 'Resetting…' : 'Reset password'}
            </button>
          </form>
        )}

        {done && (
          <button
            data-testid="reset-password-continue-button"
            type="button"
            onClick={onDone}
            className="w-full mt-6 py-3 rounded-full bg-[#5C9E8C] hover:bg-[#72C2AC] text-[#08120F] font-medium tracking-wide transition-colors duration-300"
          >
            Continue to sign in
          </button>
        )}

        {!done && err && (
          <div className="mt-6 text-center text-sm text-[#8A9A92]">
            <button
              data-testid="reset-password-back-link"
              type="button"
              onClick={onDone}
              className="text-[#72C2AC] hover:text-[#C4A67A] transition-colors"
            >
              Back to sign in
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
