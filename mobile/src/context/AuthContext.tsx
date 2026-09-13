import React, { createContext, useContext, useState, useEffect } from 'react';
import { User, AuthTokens } from '../types';
import {
  getStoredTokens,
  getStoredUser,
  setStoredTokens,
  setStoredUser,
  API_BASE_URL,
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
  const [isLoading, setIsLoading] = useState<boolean>(true);

  useEffect(() => {
    async function loadAuth() {
      try {
        const storedUser = await getStoredUser();
        const storedTokens = await getStoredTokens();
        if (storedTokens?.access && storedUser) {
          setUser(storedUser);
        }
      } catch {
        // Ignore load error
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
    await setStoredTokens(null);
    await setStoredUser(null);
    setUser(null);
  };

  const refreshProfile = async () => {
    // If logged in, fetch profile
    const tokens = await getStoredTokens();
    if (!tokens?.access) return;
    try {
      const res = await fetch(`${API_BASE_URL}/profile/me/`, {
        headers: { Authorization: `Bearer ${tokens.access}` },
      });
      if (res.ok) {
        const data = await res.json();
        const updated: User = { id: data.id, username: data.username, email: data.email };
        await setStoredUser(updated);
        setUser(updated);
      }
    } catch {
      // Profile fetch
    }
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: !!user,
        isLoading,
        login,
        logout,
        refreshProfile,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => useContext(AuthContext);
