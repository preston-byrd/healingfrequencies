import React, { createContext, useContext, useEffect, useState } from 'react';
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

  const login = async (email, password) => {
    const { data } = await api.post('/auth/login', { email, password });
    if (data.token) localStorage.setItem('token', data.token);
    setUser(data);
    return data;
  };

  const register = async (email, password, name) => {
    const { data } = await api.post('/auth/register', { email, password, name });
    if (data.token) localStorage.setItem('token', data.token);
    setUser(data);
    return data;
  };

  const logout = async () => {
    try { await api.post('/auth/logout'); } catch (e) { /* ignore */ }
    localStorage.removeItem('token');
    setUser(false);
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
