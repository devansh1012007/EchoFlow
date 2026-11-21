import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "../stores/auth";
import { createVoiceBackend } from "./store";
import { VoiceStoreError, type NewVoicePost, type VoiceBackend, type VoicePost } from "./types";

/**
 * The page's state machine over a `VoiceBackend`: load the list, create,
 * like, delete — with every failure surfaced rather than swallowed, because a
 * spoken post that vanishes on a storage error looks exactly like one that
 * worked.
 *
 * The backend is created once per mount (`createVoiceBackend()`), so nothing
 * mid-session re-opens the IndexedDB handle. The components never see the
 * backend itself, only this hook's results — which is the whole point of the
 * seam: swapping in an HTTP implementation later changes this file and
 * nothing above it.
 */
export interface UseVoices {
  /** Top-level posts, newest first. `null` while the first load is in flight. */
  posts: VoicePost[] | null;
  /** A load or refresh that found nothing to show (distinct from "never loaded"). */
  error: string | null;
  /** True while (re)loading the list. */
  loading: boolean;
  refresh(): Promise<void>;
  /** Create a top-level post or a reply. Returns the created post. */
  create(input: { durationMs: number; mimeType: string; peaks: number[]; audio: Blob; parentId?: string | null }): Promise<VoicePost>;
  toggleLike(id: string): Promise<void>;
  /** Delete one of the signed-in user's own posts (and its replies). */
  remove(id: string): Promise<void>;
  /** Replies of one post, oldest first, fetched on demand for the thread view. */
  repliesOf(parentId: string): Promise<VoicePost[]>;
}

export function useVoices(): UseVoices {
  const { user } = useAuth();
  const backendRef = useRef<VoiceBackend | null>(null);
  if (!backendRef.current) backendRef.current = createVoiceBackend();
  const backend = backendRef.current;

  const [posts, setPosts] = useState<VoicePost[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const list = await backend.list();
      setPosts(list);
      setError(null);
    } catch (err) {
      setError(err instanceof VoiceStoreError ? err.message : "Voice posts could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [backend]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const create = useCallback<UseVoices["create"]>(
    async (input) => {
      if (!user) throw new VoiceStoreError("forbidden", "You need to be signed in to post.");
      const payload: NewVoicePost = {
        author: { id: user.id, username: user.username },
        durationMs: input.durationMs,
        mimeType: input.mimeType,
        peaks: input.peaks,
        parentId: input.parentId ?? null,
        audio: input.audio,
      };
      const post = await backend.create(payload);
      // A reply does not join the top-level list; the parent's replyCount does.
      if (!payload.parentId) setPosts((prev) => (prev ? [post, ...prev] : [post]));
      else await refresh();
      return post;
    },
    [backend, user, refresh],
  );

  const toggleLike = useCallback(
    async (id: string) => {
      try {
        const next = await backend.toggleLike(id);
        setPosts((prev) => prev?.map((p) => (p.id === next.id ? next : p)) ?? prev);
      } catch (err) {
        // Optimistic UI never lies here: the toggle is awaited, so a failure
        // means the button never moved. Saying why is the least we owe.
        setError(err instanceof VoiceStoreError ? err.message : "Could not update that like.");
      }
    },
    [backend],
  );

  const remove = useCallback(
    async (id: string) => {
      if (!user) return;
      await backend.remove(id, user.id);
      // Replies under a deleted parent go with it; a refresh is the honest way
      // to learn what the store actually kept.
      await refresh();
    },
    [backend, user, refresh],
  );

  const repliesOf = useCallback(async (parentId: string) => {
    return backend.list({ parentId });
  }, [backend]);

  return { posts, error, loading, refresh, create, toggleLike, remove, repliesOf };
}
