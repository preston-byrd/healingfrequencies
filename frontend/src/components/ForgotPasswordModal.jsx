import React, { useEffect, useRef, useState } from 'react';
import api, { formatApiError } from '@/lib/api';

/**
 * A privacy-safe "forgot your password?" dialog. The email field is
 * pre-populated from the current login form when known, but always editable.
 * The server response is intentionally generic (never reveals account
 * existence) — we mirror that in the UI copy.
 */
export default function ForgotPasswordModal({ initialEmail = '', onClose }) {
  const [email, setEmail] = useState(initialEmail);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [sent, setSent] = useState(false);
  const inputRef = useRef(null);

  useEffect(() => {
    // Keep the pre-fill in sync if the login form's email changes while the
    // modal is open (e.g. autofill fires late).
    setEmail(initialEmail);
  }, [initialEmail]);

  useEffect(() => {
    inputRef.current?.focus();
    const onKey = (e) => { if (e.key === 'Escape') onClose?.(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const submit = async (e) => {
    e.preventDefault();
    setErr('');
    setBusy(true);
    try {
      await api.post('/auth/forgot-password', { email });
      setSent(true);
    } catch (e) {
      setErr(formatApiError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      data-testid="forgot-password-modal"
      className="fixed inset-0 z-50 flex items-center justify-center px-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="forgot-password-title"
    >
      <div
        className="absolute inset-0 bg-[#04080B]/80 backdrop-blur-md"
        onClick={onClose}
      />
      <div className="relative z-10 w-full max-w-md glass p-8">
        <div className="label-tiny mb-3">Solarisound</div>
        <h2
          id="forgot-password-title"
          className="font-display text-3xl font-light tracking-tight text-[#E8E3D9]"
        >
          {sent ? 'Check your inbox' : 'Reset your password'}
        </h2>
        <p className="text-[#8A9A92] mt-3 text-sm leading-relaxed">
          {sent
            ? "If an account exists for that email, we've sent a reset link. It will expire in 30 minutes."
            : "Enter the email tied to your account. We'll send you a secure link to choose a new password."}
        </p>

        {!sent && (
          <form onSubmit={submit} className="space-y-4 mt-6">
            <div>
              <label className="label-tiny block mb-2">Email</label>
              <input
                ref={inputRef}
                data-testid="forgot-password-email-input"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full bg-transparent border-b border-[rgba(92,158,140,0.25)] focus:border-[#72C2AC] outline-none py-2 text-[#E8E3D9] transition-colors"
                placeholder="you@example.com"
              />
            </div>
            {err && (
              <div data-testid="forgot-password-error" className="text-[#D96C6C] text-sm">
                {err}
              </div>
            )}
            <div className="flex items-center justify-end gap-3 pt-2">
              <button
                data-testid="forgot-password-cancel-button"
                type="button"
                onClick={onClose}
                className="text-sm text-[#8A9A92] hover:text-[#E8E3D9] transition-colors px-3 py-2"
              >
                Cancel
              </button>
              <button
                data-testid="forgot-password-submit-button"
                type="submit"
                disabled={busy || !email}
                className="px-5 py-2.5 rounded-full bg-[#5C9E8C] hover:bg-[#72C2AC] text-[#08120F] text-sm font-medium tracking-wide transition-colors duration-300 disabled:opacity-50"
              >
                {busy ? 'Sending…' : 'Send reset link'}
              </button>
            </div>
          </form>
        )}

        {sent && (
          <div className="mt-8 flex items-center justify-end">
            <button
              data-testid="forgot-password-close-button"
              type="button"
              onClick={onClose}
              className="px-5 py-2.5 rounded-full bg-[#5C9E8C] hover:bg-[#72C2AC] text-[#08120F] text-sm font-medium tracking-wide transition-colors duration-300"
            >
              Back to sign in
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
