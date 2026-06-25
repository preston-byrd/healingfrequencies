import React from 'react';
import '@/App.css';
import { AuthProvider, useAuth } from '@/contexts/AuthContext';
import AuthScreen from '@/components/AuthScreen';
import Dashboard from '@/components/Dashboard';

function Shell() {
  const { user, loading } = useAuth();
  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center text-[#8A9A92]">
        <div className="aurora-bg" />
        <div className="relative font-display text-2xl tracking-wide">Tuning in…</div>
      </div>
    );
  }
  if (!user) return <AuthScreen />;
  return <Dashboard />;
}

export default function App() {
  return (
    <AuthProvider>
      <Shell />
    </AuthProvider>
  );
}
