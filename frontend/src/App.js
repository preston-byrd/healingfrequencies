import React, { useState } from 'react';
import '@/App.css';
import { AuthProvider, useAuth } from '@/contexts/AuthContext';
import { SubscriptionProvider } from '@/contexts/SubscriptionContext';
import AuthScreen from '@/components/AuthScreen';
import Dashboard from '@/components/Dashboard';
import AccountDashboard from '@/components/AccountDashboard';

function Shell() {
  const { user, loading } = useAuth();
  const [view, setView] = useState('main'); // 'main' | 'account'

  // Auto-navigate to account when returning from Stripe checkout
  React.useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    if (p.get('stripe_session_id') || p.get('stripe_canceled')) setView('account');
  }, []);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center text-[#8A9A92]">
        <div className="aurora-bg" />
        <div className="relative font-display text-2xl tracking-wide">Tuning in…</div>
      </div>
    );
  }
  if (!user) return <AuthScreen />;

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
