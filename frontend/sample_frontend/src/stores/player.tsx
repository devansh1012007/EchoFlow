import { createContext, useContext, useState, useEffect, useRef, useCallback, ReactNode } from 'react';
import { AudioClip, PlayerState } from '../types';
import { interactionsAPI } from '../api/client';
import Hls from 'hls.js';

const PlayerContext = createContext<PlayerState | null>(null);
export const usePlayer = () => useContext(PlayerContext)!;

interface Props { children: ReactNode; toast?: (msg: string, type?: string) => void; }

export function PlayerProvider({ children }: Props) {
  const [active, setActive] = useState<AudioClip | null>(null);
  const [playing, setPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const [duration, setDuration] = useState(0);
  const [buffered, setBuffered] = useState(0);
  const [isBuffering, setBuffering] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const audioRef = useRef<HTMLAudioElement>(new Audio());
  const hlsRef = useRef<Hls | null>(null);
  const startRef = useRef<number | null>(null);

  const killHLS = useCallback(() => {
    if (hlsRef.current) { hlsRef.current.destroy(); hlsRef.current = null; }
  }, []);

  useEffect(() => {
    const a = audioRef.current;
    a.ontimeupdate = () => {
      if (a.duration) {
        setProgress(a.currentTime / a.duration);
        setDuration(a.duration);
      }
      if (a.buffered.length) setBuffered(a.buffered.end(a.buffered.length - 1) / (a.duration || 1));
    };
    a.onwaiting = () => setBuffering(true);
    a.onplaying = () => setBuffering(false);
    a.onended = () => { setPlaying(false); setProgress(1); };
    a.onerror = () => { setError('Playback error'); setPlaying(false); };
    return () => { killHLS(); };
  }, [killHLS]);

  const loadSource = useCallback((clip: AudioClip) => {
    const a = audioRef.current;
    killHLS();
    a.pause();
    setError(null);
    setProgress(0); setDuration(0); setBuffered(0);
    startRef.current = Date.now();

    const src = clip.hls_playlist_url;
    if (!src) { setError('No stream available'); return; }

    const fullSrc = src.startsWith('http') ? src : (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8005') + src;

    if (Hls.isSupported()) {
      const hls = new Hls({ startLevel: -1, maxBufferLength: 30 });
      hls.loadSource(fullSrc);
      hls.attachMedia(a);
      hls.on(Hls.Events.MANIFEST_PARSED, () => { a.play().catch(() => {}); setPlaying(true); });
      hls.on(Hls.Events.ERROR, (_e, d) => { if (d.fatal && d.type === Hls.ErrorTypes.NETWORK_ERROR) hls.startLoad(); });
      hlsRef.current = hls;
    } else if (a.canPlayType('application/vnd.apple.mpegurl')) {
      a.src = fullSrc; a.play().catch(() => {}); setPlaying(true);
    } else {
      a.src = fullSrc; a.play().catch(() => {}); setPlaying(true);
    }
  }, [killHLS]);

  const play = useCallback(async (clip: AudioClip) => {
    if (active?.id === clip.id) {
      if (playing) { audioRef.current.pause(); setPlaying(false); }
      else { await audioRef.current.play().catch(() => {}); setPlaying(true); startRef.current = Date.now(); }
      return;
    }
    loadSource(clip);
    setActive(clip);
  }, [active, playing, loadSource]);

  const pause = useCallback(() => { audioRef.current.pause(); setPlaying(false); }, []);
  const seek = useCallback((r: number) => { if (audioRef.current && duration) { audioRef.current.currentTime = r * duration; setProgress(r); } }, [duration]);
  const skipForward = useCallback((s: number = 10) => { if (audioRef.current && duration) { audioRef.current.currentTime = Math.min(audioRef.current.currentTime + s, duration); } }, [duration]);
  const skipBackward = useCallback((s: number = 10) => { if (audioRef.current) { audioRef.current.currentTime = Math.max(audioRef.current.currentTime - s, 0); } }, []);
  const listenMs = useCallback(() => startRef.current ? Date.now() - startRef.current : 0, []);

  useEffect(() => {
    if (!active) return;
    return () => {
      const ms = listenMs();
      if (ms > 800) interactionsAPI.logTelemetry(active.id, { action_type: 'view', watch_time_ms: ms }).catch(() => {});
    };
  }, [active, listenMs]);

  const ctx: PlayerState = {
    active, playing, progress, duration, buffered, isBuffering, error,
    play, pause, seek, skipForward, skipBackward, listenMs,
    destroy: () => { killHLS(); audioRef.current.pause(); setActive(null); setPlaying(false); },
    loadHLSIfNeeded: () => {},
  };

  return <PlayerContext.Provider value={ctx}>{children}</PlayerContext.Provider>;
}
