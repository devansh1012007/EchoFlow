import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { AVPlaybackStatus } from 'expo-av';
import * as Haptics from 'expo-haptics';
import { FeedClip } from '../types';
import { mobileAudioPlayer } from '../services/audioPlayer';
import { interactionsAPI } from '../services/api';

interface PlayerContextType {
  currentClip: FeedClip | null;
  isPlaying: boolean;
  positionMillis: number;
  durationMillis: number;
  handsFreeMode: boolean;
  queue: FeedClip[];
  playClip: (clip: FeedClip) => Promise<void>;
  togglePlayPause: () => Promise<void>;
  skipNext: () => Promise<void>;
  skipPrev: () => Promise<void>;
  toggleLike: (clipId: string) => Promise<void>;
  setHandsFreeMode: (enabled: boolean) => void;
  setQueue: (clips: FeedClip[]) => void;
}

const PlayerContext = createContext<PlayerContextType>({
  currentClip: null,
  isPlaying: false,
  positionMillis: 0,
  durationMillis: 1,
  handsFreeMode: true,
  queue: [],
  playClip: async () => {},
  togglePlayPause: async () => {},
  skipNext: async () => {},
  skipPrev: async () => {},
  toggleLike: async () => {},
  setHandsFreeMode: () => {},
  setQueue: () => {},
});

export const PlayerProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [currentClip, setCurrentClip] = useState<FeedClip | null>(null);
  const [isPlaying, setIsPlaying] = useState<boolean>(false);
  const [positionMillis, setPositionMillis] = useState<number>(0);
  const [durationMillis, setDurationMillis] = useState<number>(1);
  const [handsFreeMode, setHandsFreeMode] = useState<boolean>(true);
  const [queue, setQueue] = useState<FeedClip[]>([]);

  const playClip = useCallback(async (clip: FeedClip) => {
    setCurrentClip(clip);
    await mobileAudioPlayer.loadAndPlay(clip);
    setIsPlaying(true);
  }, []);

  const togglePlayPause = useCallback(async () => {
    try {
      await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    } catch {}
    const playing = await mobileAudioPlayer.togglePlayPause();
    setIsPlaying(playing);
  }, []);

  const skipNext = useCallback(async () => {
    try {
      await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    } catch {}

    if (!currentClip || queue.length === 0) return;
    const currentIndex = queue.findIndex((c) => c.id === currentClip.id);
    if (currentIndex >= 0 && currentIndex < queue.length - 1) {
      // Register skip telemetry
      try {
        await interactionsAPI.registerSkip(currentClip.id);
      } catch {}
      await playClip(queue[currentIndex + 1]);
    } else if (queue.length > 0) {
      // Wrap around
      await playClip(queue[0]);
    }
  }, [currentClip, queue, playClip]);

  const skipPrev = useCallback(async () => {
    try {
      await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    } catch {}

    if (!currentClip || queue.length === 0) return;
    const currentIndex = queue.findIndex((c) => c.id === currentClip.id);
    if (currentIndex > 0) {
      await playClip(queue[currentIndex - 1]);
    }
  }, [currentClip, queue, playClip]);

  const toggleLike = useCallback(async (clipId: string) => {
    try {
      await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy);
    } catch {}

    try {
      const res = await interactionsAPI.toggleLike(clipId);
      // Update in queue and current
      setQueue((prev) =>
        prev.map((c) =>
          c.id === clipId
            ? { ...c, is_liked: res.liked, likes: res.likes_count }
            : c
        )
      );
      setCurrentClip((prev) =>
        prev && prev.id === clipId
          ? { ...prev, is_liked: res.liked, likes: res.likes_count }
          : prev
      );
    } catch (err) {
      console.warn('Failed to toggle like:', err);
    }
  }, []);

  // Listen to audio player status
  useEffect(() => {
    mobileAudioPlayer.setStatusCallback((status: AVPlaybackStatus) => {
      if (status.isLoaded) {
        setIsPlaying(status.isPlaying);
        setPositionMillis(status.positionMillis || 0);
        setDurationMillis(status.durationMillis || 1);
      }
    });

    mobileAudioPlayer.setTrackFinishedCallback(() => {
      if (handsFreeMode) {
        skipNext();
      }
    });

    return () => {
      mobileAudioPlayer.setStatusCallback(null);
      mobileAudioPlayer.setTrackFinishedCallback(null);
    };
  }, [handsFreeMode, skipNext]);

  return (
    <PlayerContext.Provider
      value={{
        currentClip,
        isPlaying,
        positionMillis,
        durationMillis,
        handsFreeMode,
        queue,
        playClip,
        togglePlayPause,
        skipNext,
        skipPrev,
        toggleLike,
        setHandsFreeMode,
        setQueue,
      }}
    >
      {children}
    </PlayerContext.Provider>
  );
};

export const usePlayer = () => useContext(PlayerContext);
