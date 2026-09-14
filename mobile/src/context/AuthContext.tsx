import React, { createContext, useContext, useState, useEffect } from 'react';
import { User, AuthTokens } from '../types';
import {
  apiFetch,
  authAPI,
  getStoredTokens,
  getStoredUser,
  setStoredTokens,
  setStoredUser,
} from '../services/api';

interface AuthContextType {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (tokens: AuthTokens, user: User) => Promise<void>;
  logout: () => Promise<void>;
  refreshProfile: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType>({
  user: null,
  isAuthenticated: false,
  isLoading: true,
  login: async () => {},
  logout: async () => {},
  refreshProfile: async () => {},
});

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    async function loadAuth() {
      try {
        const storedTokens = await getStoredTokens();
        if (storedTokens?.access) {
          const storedUser = await getStoredUser();
          if (storedUser) setUser(storedUser);
          await refreshProfile();
        }
      } finally {
        setIsLoading(false);
      }
    }
    loadAuth();
  }, []);

  const login = async (tokens: AuthTokens, userData: User) => {
    await setStoredTokens(tokens);
    await setStoredUser(userData);
    setUser(userData);
  };

  const logout = async () => {
    try {
      await authAPI.logout();
    } finally {
      setUser(null);
    }
  };

  const refreshProfile = async () => {
    const tokens = await getStoredTokens();
    if (!tokens?.access) return;
    const data = await apiFetch<{ id: number; username: string; email: string }>('/profile/me/');
    const updated: User = { id: data.id, username: data.username, email: data.email };
    await setStoredUser(updated);
    setUser(updated);
  };

  return (
    <AuthContext.Provider
      value={{ user, isAuthenticated: !!user, isLoading, login, logout, refreshProfile }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => useContext(AuthContext);
