import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import api from '@/lib/api';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null); // null = checking, false = unauth, object = authed
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const { data } = await api.get('/auth/me');
        setUser(data);
      } catch {
        setUser(false);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const login = useCallback(async (email, password) => {
    const { data } = await api.post('/auth/login', { email, password });
    if (data.token) localStorage.setItem('token', data.token);
    setUser(data);
    return data;
  }, []);

  const register = useCallback(async (email, password, name) => {
    const { data } = await api.post('/auth/register', { email, password, name });
    if (data.token) localStorage.setItem('token', data.token);
    setUser(data);
    return data;
  }, []);

  const logout = useCallback(async () => {
    try { await api.post('/auth/logout'); } catch (e) { console.warn('[AuthContext] logout request failed', e); }
    localStorage.removeItem('token');
    setUser(false);
  }, []);

  const setUserName = useCallback((name) => {
    setUser((u) => (u && typeof u === 'object' ? { ...u, name } : u));
  }, []);

  const value = useMemo(
    () => ({ user, loading, login, register, logout, setUserName }),
    [user, loading, login, register, logout, setUserName],
  );

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
