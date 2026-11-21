import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { usePlayer } from "../stores/player";
import type { VoicePost } from "./types";

/**
 * ONE voice at a time, and never over the feed.
 *
 * The feed's `<audio>` (in `stores/player`) and a voice post cannot play
 * together: two things at once is noise, and the person holding the phone
 * asked for one of them. This provider owns a private `<audio>` element and
 * coordinates both directions:
 *
 * - starting a voice pauses the feed player;
 * - the feed starting a clip (a new `currentClip`, which is what every feed
 *   play path funnels through) stops the voice outright.
 *
 * The feed's element is never routed through Web Audio and never re-routed;
 * this is two elements and one rule, nothing deeper (README rule 8).
 */
interface VoicePlaybackValue {
  /** The id of the voice post currently loaded, or null. */
  activeId: string | null;
  /** True while that voice is actually playing. */
  isPlaying: boolean;
  /** Load `post` and play it; if it is already the active post, toggle. */
  toggle(post: VoicePost): void;
  /** Stop and unload, releasing the object URL. */
  stop(): void;
}

const VoicePlaybackContext = createContext<VoicePlaybackValue | null>(null);

export const VoicePlaybackProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const { currentClip, pause } = usePlayer();
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);

  // The private element. Created once; torn down with the provider.
  useEffect(() => {
    const audio = new Audio();
    audio.preload = "auto";
    audioRef.current = audio;
    const onPlay = () => setIsPlaying(true);
    const onPause = () => setIsPlaying(false);
    const onEnded = () => setIsPlaying(false);
    audio.addEventListener("play", onPlay);
    audio.addEventListener("pause", onPause);
    audio.addEventListener("ended", onEnded);
    return () => {
      audio.pause();
      audio.removeEventListener("play", onPlay);
      audio.removeEventListener("pause", onPause);
      audio.removeEventListener("ended", onEnded);
      if (urlRef.current !== null && typeof URL.revokeObjectURL === "function") {
        URL.revokeObjectURL(urlRef.current);
        urlRef.current = null;
      }
      audioRef.current = null;
    };
  }, []);

  const stop = useCallback(() => {
    const audio = audioRef.current;
    if (audio) {
      audio.pause();
      if (audio.src) audio.removeAttribute("src");
    }
    if (urlRef.current !== null && typeof URL.revokeObjectURL === "function") {
      URL.revokeObjectURL(urlRef.current);
    }
    urlRef.current = null;
    setActiveId(null);
    setIsPlaying(false);
  }, []);

  // The feed wins: any new feed clip means the person chose a reel over the
  // voice they were listening to. `currentClip` is set by every feed play
  // path (autoplay, card tap, deep link), so this one effect covers them all.
  useEffect(() => {
    if (currentClip) stop();
  }, [currentClip, stop]);

  const toggle = useCallback(
    (post: VoicePost) => {
      const audio = audioRef.current;
      if (!audio) return;
      if (activeId === post.id) {
        if (audio.paused) {
          void audio.play().catch(() => setIsPlaying(false));
        } else {
          audio.pause();
        }
        return;
      }
      // A different voice: out with the old URL, in with the new.
      if (urlRef.current !== null && typeof URL.revokeObjectURL === "function") {
        URL.revokeObjectURL(urlRef.current);
      }
      urlRef.current = URL.createObjectURL(post.audio);
      audio.src = urlRef.current;
      // The feed player yields. `pause()` is safe to call unconditionally.
      pause();
      setActiveId(post.id);
      void audio.play().catch(() => setIsPlaying(false));
    },
    [activeId, pause],
  );

  const value = useMemo<VoicePlaybackValue>(
    () => ({ activeId, isPlaying, toggle, stop }),
    [activeId, isPlaying, toggle, stop],
  );

  return <VoicePlaybackContext.Provider value={value}>{children}</VoicePlaybackContext.Provider>;
};

export function useVoicePlayback(): VoicePlaybackValue {
  const ctx = useContext(VoicePlaybackContext);
  if (!ctx) throw new Error("useVoicePlayback must be used inside <VoicePlaybackProvider>");
  return ctx;
}
