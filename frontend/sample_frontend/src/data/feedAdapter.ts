import { AudioClip, FeedResponse, UserProfile, ShareEvent } from '../types';
import { feedAPI, profileAPI, clipsAPI, shareAPI } from '../api/client';
import { DEMO_CREATORS, DEMO_ME, DEMO_CLIPS, DEMO_CLIPS_PAGE_2, DEMO_MESSAGES, DEMO_MODE } from './demo';
import { User } from '../types';

let backendAvailable = !DEMO_MODE;
let demoMode = !backendAvailable;

export function setBackendStatus(available: boolean) {
  backendAvailable = available;
  demoMode = !available;
}

export const isDemoMode = (): boolean => demoMode;

function delay(ms: number) { return new Promise(r => setTimeout(r, ms)); }

export async function fetchFeed(): Promise<{ clips: AudioClip[]; hasMore: boolean; err: string | null }> {
  if (demoMode) {
    await delay(300);
    return { clips: [...DEMO_CLIPS], hasMore: DEMO_CLIPS_PAGE_2.length > 0, err: null };
  }
  try {
    const d: FeedResponse = await feedAPI.getFeed();
    return { clips: d.results || [], hasMore: true, err: null };
  } catch {
    setBackendStatus(false);
    return { clips: [...DEMO_CLIPS], hasMore: true, err: null };
  }
}

export async function fetchSuggestions(category: string): Promise<AudioClip[]> {
  if (demoMode) {
    await delay(200);
    return DEMO_CLIPS.filter(c => c.category === category);
  }
  try {
    const d = await feedAPI.getSuggestions(category);
    return (d as FeedResponse).results || (d as AudioClip[]) || [];
  } catch {
    setBackendStatus(false);
    return DEMO_CLIPS.filter(c => c.category === category);
  }
}

function creatorToProfile(u: User): UserProfile {
  return {
    id: u.id, username: u.username, profile_picture: u.profile_picture ?? null,
    followers_count: u.followers_count ?? 0, following_count: u.following_count ?? 0,
    uploads_count: u.uploads_count ?? 0, date_joined: u.date_joined,
    bio: (u as unknown as { bio?: string }).bio,
  };
}

export async function fetchProfile(userId?: number): Promise<{ profile: UserProfile; clips: AudioClip[] }> {
  if (demoMode || !userId) {
    await delay(200);
    const demoCreator = userId ? DEMO_CREATORS[Number(userId) - 1] : DEMO_ME;
    const creator = demoCreator || DEMO_ME;
    return { profile: creatorToProfile(creator), clips: DEMO_CLIPS.filter(c => c.creator_id === creator.id) };
  }
  try {
    const p = await profileAPI.getProfile(userId);
    const cd = await clipsAPI.getUserClips(userId);
    return { profile: p, clips: cd.results || [] };
  } catch {
    setBackendStatus(false);
    return { profile: DEMO_ME, clips: [] };
  }
}

export async function fetchMyProfile(): Promise<UserProfile> {
  if (demoMode) return DEMO_ME as unknown as UserProfile;
  try {
    return await profileAPI.getMyProfile();
  } catch {
    setBackendStatus(false);
    return DEMO_ME as unknown as UserProfile;
  }
}

export async function fetchInbox(): Promise<ShareEvent[]> {
  if (demoMode) {
    await delay(200);
    return DEMO_MESSAGES;
  }
  try {
    return await shareAPI.getInbox();
  } catch {
    setBackendStatus(false);
    return DEMO_MESSAGES;
  }
}
