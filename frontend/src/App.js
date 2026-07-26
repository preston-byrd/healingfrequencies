import React, { useEffect, useState } from 'react';
import '@/App.css';
import { AuthProvider, useAuth } from '@/contexts/AuthContext';
import { SubscriptionProvider } from '@/contexts/SubscriptionContext';
import AuthScreen from '@/components/AuthScreen';
import Dashboard from '@/components/Dashboard';
import AccountDashboard from '@/components/AccountDashboard';
import LandingPage from '@/components/LandingPage';
import PlayDeepLink from '@/components/PlayDeepLink';
import ResetPasswordView from '@/components/ResetPasswordView';

const LANDING_DISMISSED_KEY = 'solarisound:landing_dismissed';

function Shell() {
  const { user, loading } = useAuth();
  const [view, setView] = useState('main'); // 'main' | 'account'
  // Password-reset deep link — if the URL carries `?reset_token=...` we
  // render the reset view regardless of auth state so the user can complete
  // the flow without signing in first.
  const [resetToken, setResetToken] = useState(() => {
    if (typeof window === 'undefined') return null;
    try {
      const p = new URLSearchParams(window.location.search);
      return p.get('reset_token');
    } catch { return null; }
  });
  // Whether to show the unauthenticated landing page. Once a visitor clicks
  // "Start tuning" we remember it for the rest of the session so they don't
  // see the splash again on every reload during signup.
  const [showLanding, setShowLanding] = useState(() => {
    try { return !sessionStorage.getItem(LANDING_DISMISSED_KEY); }
    catch { return true; }
  });

  // Voice-shortcut deep link route — /play opens the minimal player UI
  // regardless of auth state so Siri / Google Assistant flows just work.
  // We track this in state so the "Open full app" button can dismiss it
  // without forcing a navigation/reload (audio engine stays alive).
  const [deepLinkActive, setDeepLinkActive] = useState(() => {
    return typeof window !== 'undefined' && window.location.pathname === '/play';
  });

  // Auto-navigate to account when returning from Stripe checkout
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    if (p.get('stripe_session_id') || p.get('stripe_canceled')) {
      setShowLanding(false);
      setView('account');
      setDeepLinkActive(false);
    }
  }, []);

  const enterAuth = () => {
    try { sessionStorage.setItem(LANDING_DISMISSED_KEY, '1'); } catch (e) { /* private mode */ }
    setShowLanding(false);
  };

  // /play route always wins — works signed-in or signed-out.
  if (deepLinkActive) {
    return (
      <PlayDeepLink
        onOpenApp={() => {
          // Strip the /play path from the URL bar without reloading, then let
          // the normal auth gate take over. Audio keeps playing through the
          // audioEngine singleton.
          try { window.history.replaceState({}, '', '/'); } catch (e) { /* noop */ }
          setDeepLinkActive(false);
        }}
      />
    );
  }

  // Password reset landing page beats every other route so users can complete
  // the flow whether they're signed in or not. On success/dismiss we clear
  // the token param and fall through to the normal shell.
  if (resetToken) {
    return (
      <ResetPasswordView
        token={resetToken}
        onDone={() => {
          try {
            const url = new URL(window.location.href);
            url.searchParams.delete('reset_token');
            window.history.replaceState({}, '', url.pathname + (url.search || ''));
          } catch (e) { /* noop */ }
          setResetToken(null);
        }}
      />
    );
  }

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center text-[#8A9A92]">
        <div className="aurora-bg" />
        <div className="relative font-display text-2xl tracking-wide">Tuning in…</div>
      </div>
    );
  }
  if (!user) {
    if (showLanding) return <LandingPage onStart={enterAuth} />;
    return <AuthScreen />;
  }

  if (view === 'account') return <AccountDashboard onBack={() => setView('main')} />;
  return <Dashboard onOpenAccount={() => setView('account')} />;
}

export default function App() {
  return (
    <AuthProvider>
      <SubscriptionProvider>
        <Shell />
      </SubscriptionProvider>
    </AuthProvider>
  );
}
