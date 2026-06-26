import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import api from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';

const SubContext = createContext(null);

export function SubscriptionProvider({ children }) {
  const { user } = useAuth();
  const [sub, setSub] = useState(null);

  const refresh = useCallback(async () => {
    try { const { data } = await api.get('/me/subscription'); setSub(data); }
    catch { setSub(null); }
  }, []);

  useEffect(() => {
    if (user) refresh();
    else setSub(null);
  }, [user, refresh]);

  return (
    <SubContext.Provider value={{ sub, refresh, isPro: !!sub?.pro }}>
      {children}
    </SubContext.Provider>
  );
}

export const useSubscription = () => useContext(SubContext) || { sub: null, refresh: () => {}, isPro: false };
