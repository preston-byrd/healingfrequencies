import React, { useState } from 'react';
import { useAuth } from '@/contexts/AuthContext';
import { formatApiError } from '@/lib/api';

export default function AuthScreen() {
  const { login, register } = useAuth();
  const [mode, setMode] = useState('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setErr('');
    setBusy(true);
    try {
      if (mode === 'login') await login(email, password);
      else await register(email, password, name);
    } catch (e) {
      setErr(formatApiError(e));
    } finally {
      setBusy(false);
    }
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
            <input
              data-testid="auth-password-input"
              type="password"
              required
              minLength={6}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full bg-transparent border-b border-[rgba(92,158,140,0.25)] focus:border-[#72C2AC] outline-none py-2 text-[#E8E3D9] transition-colors"
              placeholder="•••••••"
            />
          </div>

          {err && <div data-testid="auth-error" className="text-[#D96C6C] text-sm">{err}</div>}

          <button
            data-testid="auth-submit-button"
            type="submit"
            disabled={busy}
            className="w-full mt-6 py-3 rounded-full bg-[#5C9E8C] hover:bg-[#72C2AC] text-[#08120F] font-medium tracking-wide transition-colors duration-300 disabled:opacity-50"
          >
            {busy ? 'One moment…' : mode === 'login' ? 'Sign in' : 'Create account'}
          </button>
        </form>

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
    </div>
  );
}
