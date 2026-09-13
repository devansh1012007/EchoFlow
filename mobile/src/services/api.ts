import AsyncStorage from '@react-native-async-storage/async-storage';
import {
  AuthTokens,
  Comment,
  FeedClip,
  OwnProfile,
  PublicProfile,
  ShareEvent,
  User,
} from '../types';

// Default API base URL for EchoFlow backend.
// In Android emulator use 'http://10.0.2.2:3000' or 'http://10.0.2.2:8000'.
// On physical devices, set to your computer's local network IP or Cloud Run URL.
export const API_BASE_URL = 'https://ais-dev-ra6pa3urcinkopihtdgpz3-557708310129.asia-southeast1.run.app';

const STORAGE_KEY_ACCESS = 'ef_mobile_access_token';
const STORAGE_KEY_REFRESH = 'ef_mobile_refresh_token';
const STORAGE_KEY_USER = 'ef_mobile_user';

export async function getStoredTokens(): Promise<AuthTokens | null> {
  try {
    const access = await AsyncStorage.getItem(STORAGE_KEY_ACCESS);
    const refresh = await AsyncStorage.getItem(STORAGE_KEY_REFRESH);
    if (access && refresh) {
      return { access, refresh };
    }
  } catch {
    // Ignore storage errors
  }
  return null;
}

export async function setStoredTokens(tokens: AuthTokens | null): Promise<void> {
  try {
    if (tokens) {
      await AsyncStorage.setItem(STORAGE_KEY_ACCESS, tokens.access);
      await AsyncStorage.setItem(STORAGE_KEY_REFRESH, tokens.refresh);
    } else {
      await AsyncStorage.removeItem(STORAGE_KEY_ACCESS);
      await AsyncStorage.removeItem(STORAGE_KEY_REFRESH);
    }
  } catch {
    // Ignore storage errors
  }
}

export async function getStoredUser(): Promise<User | null> {
  try {
    const raw = await AsyncStorage.getItem(STORAGE_KEY_USER);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export async function setStoredUser(user: User | null): Promise<void> {
  try {
    if (user) {
      await AsyncStorage.setItem(STORAGE_KEY_USER, JSON.stringify(user));
    } else {
      await AsyncStorage.removeItem(STORAGE_KEY_USER);
    }
  } catch {
    // Ignore storage errors
  }
}

let refreshPromise: Promise<string | null> | null = null;

async function refreshAccessToken(): Promise<string | null> {
  const tokens = await getStoredTokens();
  if (!tokens?.refresh) return null;

  if (refreshPromise) return refreshPromise;

  refreshPromise = (async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/auth/token/refresh/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh: tokens.refresh }),
      });

      if (!res.ok) {
        await setStoredTokens(null);
        await setStoredUser(null);
        return null;
      }

      const data = await res.json();
      const newTokens: AuthTokens = {
        access: data.access,
        refresh: data.refresh || tokens.refresh,
      };
      await setStoredTokens(newTokens);
      return newTokens.access;
    } catch {
      await setStoredTokens(null);
      await setStoredUser(null);
      return null;
    } finally {
      refreshPromise = null;
    }
  })();

  return refreshPromise;
}

export async function apiFetch<T = any>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const tokens = await getStoredTokens();
  let headers: Record<string, string> = {
    Accept: 'application/json',
    ...(options.headers as Record<string, string>),
  };

  if (!(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }

  if (tokens?.access) {
    headers.Authorization = `Bearer ${tokens.access}`;
  }

  const url = `${API_BASE_URL}${endpoint}`;
  let response = await fetch(url, { ...options, headers });

  if (response.status === 401 && tokens?.refresh) {
    const newAccess = await refreshAccessToken();
    if (newAccess) {
      headers.Authorization = `Bearer ${newAccess}`;
      response = await fetch(url, { ...options, headers });
    }
  }

  if (!response.ok) {
    const errorBody = await response.text();
    throw new Error(`API error (${response.status}): ${errorBody || response.statusText}`);
  }

  const contentType = response.headers.get('content-type');
  if (contentType && contentType.includes('application/json')) {
    return response.json();
  }
  return response.text() as unknown as T;
}

// Feed API
export const feedAPI = {
  getFeed: async (): Promise<{ results: FeedClip[]; count: number }> => {
    return apiFetch('/feed/');
  },
  getSuggestions: async (category?: string): Promise<{ results: FeedClip[] }> => {
    const q = category ? `?category=${encodeURIComponent(category)}` : '';
    return apiFetch(`/suggestions/${q}`);
  },
};

// Interactions API
export const interactionsAPI = {
  toggleLike: async (clipId: string): Promise<{ liked: boolean; likes_count: number }> => {
    return apiFetch(`/interactions/${clipId}/toggle-like/`, { method: 'POST' });
  },
  registerSkip: async (clipId: string): Promise<void> => {
    return apiFetch(`/interactions/${clipId}/register-skip/`, { method: 'POST' });
  },
  logTelemetry: async (clipId: string, listenDurationSec: number): Promise<void> => {
    return apiFetch(`/interactions/${clipId}/log-telemetry/`, {
      method: 'POST',
      body: JSON.stringify({ listen_duration_sec: listenDurationSec }),
    });
  },
};

// Comments API
export const commentsAPI = {
  getComments: async (clipId: string): Promise<{ results: Comment[] }> => {
    return apiFetch(`/comments/?clip=${encodeURIComponent(clipId)}`);
  },
  postComment: async (clipId: string, text: string, parentId?: string): Promise<Comment> => {
    return apiFetch('/comments/', {
      method: 'POST',
      body: JSON.stringify({ clip: clipId, text, parent: parentId || null }),
    });
  },
};

// Share API
export const shareAPI = {
  getInbox: async (): Promise<{ results: ShareEvent[] }> => {
    return apiFetch('/share/inbox/');
  },
  getUnreadCount: async (): Promise<{ unread: number }> => {
    return apiFetch('/share/unread-count/');
  },
  sendShare: async (clipId: string, recipientUsername: string): Promise<{ id: number }> => {
    return apiFetch(`/share/${clipId}/send-share/`, {
      method: 'POST',
      body: JSON.stringify({ recipient_username: recipientUsername }),
    });
  },
};

// Upload API
export const uploadAPI = {
  uploadAudio: async (formData: FormData): Promise<{ id: string; status: string }> => {
    return apiFetch('/clips/', {
      method: 'POST',
      body: formData,
    });
  },
};

// Profile & Follow API
export const profileAPI = {
  getOwnProfile: async (): Promise<OwnProfile> => {
    return apiFetch('/profile/me/');
  },
  getPublicProfile: async (userId: number): Promise<PublicProfile> => {
    return apiFetch(`/profile/${userId}/`);
  },
  toggleFollow: async (userId: number): Promise<{ following: boolean; followers_count: number }> => {
    return apiFetch(`/follow/${userId}/toggle-follow/`);
  },
};
