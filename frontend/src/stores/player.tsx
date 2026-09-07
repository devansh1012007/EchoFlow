import React, { createContext, useContext, useEffect, useRef, useState } from "react";
import Hls from "hls.js";
import { interactionsAPI } from "../api/client";
import { FeedClip } from "../types/echoflow";

interface PlayerContextType {
  currentClip: FeedClip | null;
  isPlaying: boolean;
  currentTime: number;
  duration: number;
  progress: number;
  playbackRate: number;
  volume: number;
  queue: FeedClip[];
  audioFrequencies: number[];
  handsFreeMode: boolean;
  playClip: (clip: FeedClip, newQueue?: FeedClip[]) => void;
  togglePlay: () => void;
  pause: () => void;
  resume: () => void;
  seek: (seconds: number) => void;
  skipForward: (seconds?: number) => void;
  skipBackward: (seconds?: number) => void;
  nextClip: (reason?: "manual" | "auto") => void;
  prevClip: () => void;
  setRate: (rate: number) => void;
  setHandsFreeMode: (val: boolean) => void;
  setQueue: (clips: FeedClip[]) => void;
}

const PlayerContext = createContext<PlayerContextType | undefined>(undefined);

export const PlayerProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [currentClip, setCurrentClip] = useState<FeedClip | null>(null);
  const [queue, setQueue] = useState<FeedClip[]>([]);
  const [isPlaying, setIsPlaying] = useState<boolean>(false);
  const [currentTime, setCurrentTime] = useState<number>(0);
  const [duration, setDuration] = useState<number>(0);
  const [playbackRate, setPlaybackRate] = useState<number>(1);
  const [volume, _setVolume] = useState<number>(1);
  const [audioFrequencies, setAudioFrequencies] = useState<number[]>(new Array(24).fill(10));
  const [handsFreeMode, setHandsFreeMode] = useState<boolean>(true);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const hlsRef = useRef<Hls | null>(null);
  const watchTimeRef = useRef<number>(0);
  const lastTelemetryRef = useRef<number>(0);
  const animFrameRef = useRef<number | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);

  // Initialize audio element
  useEffect(() => {
    const audio = new Audio();
    audio.crossOrigin = "anonymous";
    audioRef.current = audio;

    const handleTimeUpdate = () => {
      const cur = audio.currentTime;
      const dur = audio.duration || 1;
      setCurrentTime(cur);
      setDuration(dur);
      watchTimeRef.current += 250; // increment watch time

      // Periodic heartbeat telemetry every ~6 seconds (Spec FR-TEL-1)
      const now = Date.now();
      if (now - lastTelemetryRef.current > 6000 && currentClip) {
        lastTelemetryRef.current = now;
        interactionsAPI.logTelemetry(currentClip.id, {
          action_type: "view",
          watch_time_ms: Math.floor(cur * 1000),
        }).catch(() => {});
      }

      // Auto-advance when near end
      if (dur > 0 && cur / dur >= 0.99) {
        handleAutoAdvance();
      }
    };

    const handlePlay = () => setIsPlaying(true);
    const handlePause = () => {
      setIsPlaying(false);
      // Final telemetry on pause
      if (currentClip && audio.currentTime > 0) {
        interactionsAPI.logTelemetry(currentClip.id, {
          action_type: "view",
          watch_time_ms: Math.floor(audio.currentTime * 1000),
        }).catch(() => {});
      }
    };

    const handleEnded = () => {
      handleAutoAdvance();
    };

    audio.addEventListener("timeupdate", handleTimeUpdate);
    audio.addEventListener("play", handlePlay);
    audio.addEventListener("pause", handlePause);
    audio.addEventListener("ended", handleEnded);

    return () => {
      audio.pause();
      audio.removeEventListener("timeupdate", handleTimeUpdate);
      audio.removeEventListener("play", handlePlay);
      audio.removeEventListener("pause", handlePause);
      audio.removeEventListener("ended", handleEnded);
      if (hlsRef.current) {
        hlsRef.current.destroy();
      }
    };
  }, [currentClip]);

  // Synthetic frequency visualizer loop
  useEffect(() => {
    let phase = 0;
    const updateVisualizer = () => {
      if (isPlaying) {
        phase += 0.08;
        const bars: number[] = [];
        for (let i = 0; i < 24; i++) {
          const freq = (Math.sin(phase * 2 + i * 0.4) + Math.cos(phase * 1.5 + i * 0.2) + 2) / 4;
          const peak = Math.max(12, Math.floor(freq * 80 + Math.random() * 15));
          bars.push(peak);
        }
        setAudioFrequencies(bars);
      } else {
        setAudioFrequencies(new Array(24).fill(8));
      }
      animFrameRef.current = requestAnimationFrame(updateVisualizer);
    };

    animFrameRef.current = requestAnimationFrame(updateVisualizer);
    return () => {
      if (animFrameRef.current) {
        cancelAnimationFrame(animFrameRef.current);
      }
    };
  }, [isPlaying]);

  const handleAutoAdvance = () => {
    if (!currentClip) return;
    // Log complete view telemetry
    interactionsAPI.logTelemetry(currentClip.id, {
      action_type: "view",
      watch_time_ms: Math.floor(duration * 1000),
    }).catch(() => {});

    // Advance to next clip
    setTimeout(() => {
      nextClip("auto");
    }, 800);
  };

  const playClip = (clip: FeedClip, newQueue?: FeedClip[]) => {
    if (newQueue) {
      setQueue(newQueue);
    }
    setCurrentClip(clip);
    watchTimeRef.current = 0;
    lastTelemetryRef.current = Date.now();

    const audio = audioRef.current;
    if (!audio) return;

    if (!clip.hls_playlist_url) {
      console.warn("Clip has no playable stream URL yet.");
      return;
    }

    const url = clip.hls_playlist_url;

    if (hlsRef.current) {
      hlsRef.current.destroy();
      hlsRef.current = null;
    }

    if (url.endsWith(".m3u8") && Hls.isSupported()) {
      const hls = new Hls();
      hlsRef.current = hls;
      hls.loadSource(url);
      hls.attachMedia(audio);
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        audio.play().catch(() => {});
      });
    } else {
      audio.src = url;
      audio.playbackRate = playbackRate;
      audio.play().catch((err) => {
        console.warn("Auto-play blocked, waiting for user gesture:", err);
      });
    }
  };

  const togglePlay = () => {
    const audio = audioRef.current;
    if (!audio) return;

    if (isPlaying) {
      audio.pause();
    } else {
      audio.play().catch(() => {});
    }
  };

  const pause = () => audioRef.current?.pause();
  const resume = () => audioRef.current?.play().catch(() => {});

  const seek = (seconds: number) => {
    const audio = audioRef.current;
    if (!audio) return;
    const target = Math.max(0, Math.min(seconds, audio.duration || 100));
    audio.currentTime = target;
  };

  const skipForward = (seconds = 10) => {
    const audio = audioRef.current;
    if (!audio) return;
    seek(audio.currentTime + seconds);
  };

  const skipBackward = (seconds = 10) => {
    const audio = audioRef.current;
    if (!audio) return;
    seek(audio.currentTime - seconds);
  };

  const nextClip = (reason: "manual" | "auto" = "manual") => {
    if (!currentClip || queue.length === 0) return;

    // Spec FR-TEL-2: Send skip telemetry if user manually skipped before completion
    if (reason === "manual" && currentTime < (duration || 20) * 0.9) {
      interactionsAPI.registerSkip(currentClip.id, {
        listen_duration_ms: Math.floor(currentTime * 1000),
        reel_position_ms: Math.floor(currentTime * 1000),
        reel_id: currentClip.id,
      }).catch(() => {});
    }

    const currentIndex = queue.findIndex((c) => c.id === currentClip.id);
    const nextIndex = (currentIndex + 1) % queue.length;
    playClip(queue[nextIndex]);
  };

  const prevClip = () => {
    if (!currentClip || queue.length === 0) return;
    const currentIndex = queue.findIndex((c) => c.id === currentClip.id);
    const prevIndex = (currentIndex - 1 + queue.length) % queue.length;
    playClip(queue[prevIndex]);
  };

  const setRate = (rate: number) => {
    setPlaybackRate(rate);
    if (audioRef.current) {
      audioRef.current.playbackRate = rate;
    }
  };

  const progress = duration > 0 ? currentTime / duration : 0;

  return (
    <PlayerContext.Provider
      value={{
        currentClip,
        isPlaying,
        currentTime,
        duration,
        progress,
        playbackRate,
        volume,
        queue,
        audioFrequencies,
        handsFreeMode,
        playClip,
        togglePlay,
        pause,
        resume,
        seek,
        skipForward,
        skipBackward,
        nextClip,
        prevClip,
        setRate,
        setHandsFreeMode,
        setQueue,
      }}
    >
      {children}
    </PlayerContext.Provider>
  );
};

export const usePlayer = () => {
  const context = useContext(PlayerContext);
  if (!context) {
    throw new Error("usePlayer must be used within a PlayerProvider");
  }
  return context;
};
