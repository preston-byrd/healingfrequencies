import React, { useEffect, useState } from 'react';
import '@/App.css';
import { AuthProvider, useAuth } from '@/contexts/AuthContext';
import { SubscriptionProvider } from '@/contexts/SubscriptionContext';
import AuthScreen from '@/components/AuthScreen';
import Dashboard from '@/components/Dashboard';
import AccountDashboard from '@/components/AccountDashboard';
import LandingPage from '@/components/LandingPage';

const LANDING_DISMISSED_KEY = 'solarisound:landing_dismissed';

function Shell() {
  const { user, loading } = useAuth();
  const [view, setView] = useState('main'); // 'main' | 'account'
  // Whether to show the unauthenticated landing page. Once a visitor clicks
  // "Start tuning" we remember it for the rest of the session so they don't
  // see the splash again on every reload during signup.
  const [showLanding, setShowLanding] = useState(() => {
    try { return !sessionStorage.getItem(LANDING_DISMISSED_KEY); }
    catch { return true; }
  });

  // Auto-navigate to account when returning from Stripe checkout
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    if (p.get('stripe_session_id') || p.get('stripe_canceled')) {
      setShowLanding(false);
      setView('account');
    }
  }, []);

  const enterAuth = () => {
    try { sessionStorage.setItem(LANDING_DISMISSED_KEY, '1'); } catch (e) { /* private mode */ }
    setShowLanding(false);
  };

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
