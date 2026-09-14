import AsyncStorage from '@react-native-async-storage/async-storage';
import { Platform } from 'react-native';
import {
  AuthTokens,
  Comment,
  FeedClip,
  OwnProfile,
  PublicProfile,
  ShareEvent,
  User,
} from '../types';

// The Django debug port is exposed as 8005 by the local Docker stack.
// Override this for a physical device or a deployed environment.
export const API_BASE_URL =
  process.env.EXPO_PUBLIC_API_BASE_URL ||
  (Platform.OS === 'web'
    ? 'http://localhost:8005'
    : Platform.OS === 'android'
      ? 'http://10.0.2.2:8005'
      : 'http://localhost:8005');

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
  getFeed: async (): Promise<{ results: FeedClip[]; queue_health?: number; degraded?: boolean }> => {
    return apiFetch('/feed/');
  },
  getSuggestions: async (category?: string): Promise<{ results: FeedClip[] }> => {
    const q = category ? `?category=${encodeURIComponent(category)}` : '';
    return apiFetch(`/suggestions/${q}`);
  },
};

// Interactions API
export const interactionsAPI = {
  toggleLike: async (clipId: string): Promise<{ status: 'liked' | 'unliked' }> => {
    return apiFetch(`/interactions/${clipId}/toggle-like/`, { method: 'POST' });
  },
  registerSkip: async (
    clipId: string,
    data: { listen_duration_ms: number; reel_position_ms: number; reel_id: string }
  ): Promise<{ status: string }> => {
    return apiFetch(`/interactions/${clipId}/register-skip/`, {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },
  logTelemetry: async (
    clipId: string,
    data: { action_type: 'view' | 'like' | 'share' | 'skip'; watch_time_ms: number }
  ): Promise<{ status: string }> => {
    return apiFetch(`/interactions/${clipId}/log-telemetry/`, {
      method: 'POST',
      body: JSON.stringify(data),
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
  getInbox: async (): Promise<ShareEvent[]> => {
    const response = await apiFetch<ShareEvent[] | { results: ShareEvent[] }>('/share/inbox/');
    return Array.isArray(response) ? response : response.results;
  },
  getUnreadCount: async (): Promise<{ unread: number }> => {
    return apiFetch('/share/unread-count/');
  },
  sendShare: async (clipId: string, recipientUsername: string): Promise<{ status: string }> => {
    const recipient = await shareAPI.findUser(recipientUsername.replace(/^@/, ''));
    return apiFetch(`/share/${clipId}/send-share/`, {
      method: 'POST',
      body: JSON.stringify({ receiver_id: recipient.id }),
    });
  },
  findUser: async (username: string): Promise<{ id: number; username: string }> => {
    return apiFetch(`/share/find-user/?username=${encodeURIComponent(username)}`);
  },
};

// Upload API
export const uploadAPI = {
  uploadAudio: async (formData: FormData): Promise<{ clip_id: string; status: string }> => {
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
  toggleFollow: async (userId: number): Promise<{ status: 'followed' | 'unfollowed' }> => {
    return apiFetch(`/follow/${userId}/toggle-follow/`);
  },
};

export const authAPI = {
  logout: async (): Promise<void> => {
    const tokens = await getStoredTokens();
    if (tokens?.refresh) {
      try {
        await apiFetch('/auth/logout/', {
          method: 'POST',
          body: JSON.stringify({ refresh: tokens.refresh }),
        });
      } finally {
        await setStoredTokens(null);
        await setStoredUser(null);
      }
    } else {
      await setStoredTokens(null);
      await setStoredUser(null);
    }
  },
};
