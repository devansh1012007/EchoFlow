import { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react';
import { User, AuthTokens } from '../types';
import { authAPI, setTokens, clearTokens } from '../api/client';

export function loadUser(): User | null {
  try {
    return JSON.parse(sessionStorage.getItem('ef_user') || 'null');
  } catch {
    return null;
  }
}

export function loadToken(): string | null {
  return sessionStorage.getItem('ef_access');
}

interface AuthContextValue {
  user: User | null;
  authed: boolean;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (email: string, username: string, password: string) => Promise<void>;
  devLogin: () => Promise<void>;
  logout: () => void;
  patchUser: (p: Partial<User>) => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);
export const useAuth = () => useContext(AuthContext)!;

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(loadUser);
  const [authed, setAuthed] = useState<boolean>(!!loadToken());
  const [loading, setLoading] = useState(false);

  const persist = (data: AuthTokens, u: User) => {
    setTokens({ access: data.access, refresh: data.refresh });
    sessionStorage.setItem('ef_user', JSON.stringify(u));
    setUser(u);
    setAuthed(true);
  };

  const login = async (username: string, password: string) => {
    setLoading(true);
    try {
      const d = await authAPI.login(username, password);
      persist(d, d.user);
    } finally {
      setLoading(false);
    }
  };

  const register = async (email: string, username: string, password: string) => {
    setLoading(true);
    try {
      const d = await authAPI.register(email, username, password);
      persist(d, d.user);
      sessionStorage.setItem('ef_new_user', '1');
    } finally {
      setLoading(false);
    }
  };

  const devLogin = async () => {
    setLoading(true);
    try {
      const d = await authAPI.devLogin();
      persist(d, d.user);
    } finally {
      setLoading(false);
    }
  };

  const logout = useCallback(() => {
    clearTokens();
    setUser(null);
    setAuthed(false);
  }, []);

  useEffect(() => {
    const handle = () => logout();
    window.addEventListener('ef_session_expired', handle);
    return () => window.removeEventListener('ef_session_expired', handle);
  }, [logout]);

  const patchUser = (p: Partial<User>) => {
    const u = { ...user, ...p } as User;
    sessionStorage.setItem('ef_user', JSON.stringify(u));
    setUser(u);
  };

  return (
    <AuthContext.Provider value={{ user, authed, loading, login, register, devLogin, logout, patchUser }}>
      {children}
    </AuthContext.Provider>
  );
}
