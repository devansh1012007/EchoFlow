import React, { useEffect, useState, useCallback } from "react";
import { Headphones, AlertTriangle, RefreshCw, ChevronDown, ChevronUp, Sparkles, Radio, Disc3 } from "lucide-react";
import { feedAPI } from "../api/client";
import { usePlayer } from "../stores/player";
import { FeedClip, FeedResponse } from "../types/echoflow";
import { ReelCard } from "../components/feed/ReelCard";
import { CommentSheet } from "../components/comments/CommentSheet";
import { ShareModal } from "../components/sharing/ShareModal";

interface FeedPageProps {
  onOpenCreatorProfile: (creatorId: number) => void;
  onOpenOnboarding: () => void;
}

export const FeedPage: React.FC<FeedPageProps> = ({ onOpenCreatorProfile, onOpenOnboarding }) => {
  const [clips, setClips] = useState<FeedClip[]>([]);
  const [activeIndex, setActiveIndex] = useState<number>(0);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isColdPreparing, setIsColdPreparing] = useState<boolean>(false);
  const [retryCountdown, setRetryCountdown] = useState<number>(0);
  const [isDegraded, setIsDegraded] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Modals state
  const [selectedClipForComments, setSelectedClipForComments] = useState<FeedClip | null>(null);
  const [selectedClipForShare, setSelectedClipForShare] = useState<FeedClip | null>(null);

  const { currentClip, playClip, setQueue, handsFreeMode } = usePlayer();

  const loadFeed = useCallback(async (retryCount = 0) => {
    setIsLoading(true);
    setErrorMsg(null);

    try {
      const data: FeedResponse = await feedAPI.getFeed();

      // Spec FR-FEED-1: Handle 202 cold queue response
      if (data.retry_after_ms && data.results.length === 0) {
        setIsColdPreparing(true);
        const waitMs = data.retry_after_ms || 1500;
        setRetryCountdown(Math.ceil(waitMs / 1000));

        if (retryCount < 5) {
          setTimeout(() => {
            loadFeed(retryCount + 1);
          }, waitMs);
        } else {
          setIsColdPreparing(false);
          setIsLoading(false);
        }
        return;
      }

      setIsColdPreparing(false);
      setIsDegraded(!!data.degraded);
      setClips(data.results);
      setQueue(data.results);

      // Auto-play the first reel if none playing
      if (data.results.length > 0 && !currentClip) {
        playClip(data.results[0], data.results);
      }
    } catch (err: any) {
      setErrorMsg(err?.message || "Failed to load audio feed");
    } finally {
      setIsLoading(false);
    }
  }, [currentClip, playClip, setQueue]);

  useEffect(() => {
    loadFeed();
  }, [loadFeed]);

  // Keep active index in sync with player's current clip
  useEffect(() => {
    if (currentClip && clips.length > 0) {
      const idx = clips.findIndex((c) => c.id === currentClip.id);
      if (idx !== -1 && idx !== activeIndex) {
        setActiveIndex(idx);
      }
    }
  }, [currentClip, clips, activeIndex]);

  const goToNextReel = () => {
    if (clips.length === 0) return;
    const nextIdx = (activeIndex + 1) % clips.length;
    setActiveIndex(nextIdx);
    playClip(clips[nextIdx], clips);
  };

  const goToPrevReel = () => {
    if (clips.length === 0) return;
    const prevIdx = (activeIndex - 1 + clips.length) % clips.length;
    setActiveIndex(prevIdx);
    playClip(clips[prevIdx], clips);
  };

  const selectClipByIndex = (index: number) => {
    if (index >= 0 && index < clips.length) {
      setActiveIndex(index);
      playClip(clips[index], clips);
    }
  };

  if (isColdPreparing) {
    return (
      <div className="min-h-[70vh] flex flex-col items-center justify-center p-6 text-center space-y-4">
        <div className="w-16 h-16 rounded-2xl bg-[#FF6321]/20 border border-[#FF6321]/40 flex items-center justify-center text-[#FF6321] animate-spin">
          <RefreshCw className="w-8 h-8" />
        </div>
        <div className="space-y-1">
          <h2 className="text-xl font-black uppercase tracking-tight text-white">Synthesizing Audio Feed...</h2>
          <p className="text-xs font-mono uppercase text-white/40 max-w-sm">
            Populating pgvector queue with cosine similarity matrix (retrying in {retryCountdown}s)
          </p>
        </div>
      </div>
    );
  }

  if (isLoading && clips.length === 0) {
    return (
      <div className="min-h-[70vh] flex flex-col items-center justify-center p-6 text-center text-white/40 space-y-3 font-mono text-xs uppercase">
        <div className="w-10 h-10 border-2 border-[#FF6321] border-t-transparent rounded-full animate-spin" />
        <p>Loading 384-dimensional audio stream...</p>
      </div>
    );
  }

  if (errorMsg && clips.length === 0) {
    return (
      <div className="min-h-[70vh] flex flex-col items-center justify-center p-6 text-center space-y-4">
        <div className="w-12 h-12 rounded-2xl bg-rose-500/20 text-rose-400 flex items-center justify-center">
          <AlertTriangle className="w-6 h-6" />
        </div>
        <div className="space-y-1">
          <h2 className="text-lg font-black uppercase text-white">Connection Interrupted</h2>
          <p className="text-xs text-white/50">{errorMsg}</p>
        </div>
        <button
          type="button"
          onClick={() => loadFeed(0)}
          className="px-6 py-2.5 rounded bg-[#FF6321] text-black text-xs font-black uppercase tracking-wider"
        >
          Retry Connection
        </button>
      </div>
    );
  }

  const activeClip = clips[activeIndex] || clips[0];

  return (
    <div className="w-full max-w-7xl mx-auto px-4 md:px-8 py-4 pb-28">
      {/* Degraded mode banner */}
      {isDegraded && (
        <div className="mb-3 p-3 rounded-xl bg-[#FF6321]/15 border border-[#FF6321]/40 text-xs font-mono uppercase text-[#FF6321] flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 flex-shrink-0" />
          <span>Cold fallback mode active — Serving trending reels until vector queue warms up</span>
        </div>
      )}

      {/* Top Controls Bar */}
      <div className="flex items-center justify-between px-1 mb-3">
        <div className="flex items-center gap-2 text-xs font-mono uppercase text-white/40">
          <Headphones className={`w-4 h-4 ${handsFreeMode ? "text-[#FF6321]" : "text-white/30"}`} />
          <span>
            {handsFreeMode ? "Hands-Free Auto-advance: ON" : "Manual Navigation Mode"}
          </span>
        </div>

        <button
          type="button"
          onClick={onOpenOnboarding}
          className="flex items-center gap-1.5 px-3 py-1 rounded bg-white/5 hover:bg-white/10 border border-white/15 text-[10px] font-mono font-bold uppercase tracking-wider text-white transition-colors"
        >
          <Sparkles className="w-3.5 h-3.5 text-[#FF6321]" />
          <span>Tune Vector Vibes</span>
        </button>
      </div>

      {/* Two-Column Bold Typography Layout on Large Screens */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
        {/* Left / Main Section: Active Reel Card */}
        <div className="lg:col-span-7 xl:col-span-8 relative">
          {activeClip ? (
            <ReelCard
              clip={activeClip}
              isActive={true}
              onOpenComments={(c) => setSelectedClipForComments(c)}
              onOpenShare={(c) => setSelectedClipForShare(c)}
              onCreatorClick={(id) => onOpenCreatorProfile(id)}
            />
          ) : (
            <div className="h-96 rounded-3xl bg-[#111111] border border-white/10 flex flex-col items-center justify-center p-6 text-center space-y-2">
              <p className="text-base font-black uppercase text-white">All Reels Caught Up</p>
              <p className="text-xs text-white/40">Upload an audio reel or tune your vibes for more.</p>
            </div>
          )}

          {/* Quick Reel Nav Arrows */}
          <div className="absolute -right-3 top-1/2 -translate-y-1/2 hidden md:flex flex-col gap-2 z-20">
            <button
              type="button"
              onClick={goToPrevReel}
              className="w-10 h-10 rounded-full bg-[#0A0A0A] hover:bg-[#111111] border border-white/20 text-white flex items-center justify-center shadow-xl hover:border-[#FF6321] transition-all"
              title="Previous Reel"
            >
              <ChevronUp className="w-5 h-5" />
            </button>
            <button
              type="button"
              onClick={goToNextReel}
              className="w-10 h-10 rounded-full bg-[#0A0A0A] hover:bg-[#111111] border border-white/20 text-white flex items-center justify-center shadow-xl hover:border-[#FF6321] transition-all"
              title="Next Reel"
            >
              <ChevronDown className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Right Section: Vector Feed Queue from Design Spec */}
        <div className="lg:col-span-5 xl:col-span-4 bg-[#111111] border border-white/10 rounded-2xl md:rounded-3xl p-6 flex flex-col shadow-2xl">
          <div className="flex items-center justify-between pb-4 mb-4 border-b border-white/10">
            <h3 className="text-xs font-black uppercase tracking-widest text-white/40">
              Vector Feed Queue
            </h3>
            <span className="text-[10px] font-mono text-[#FF6321] font-bold">
              {clips.length} REELS PRE-FETCHED
            </span>
          </div>

          <div className="space-y-3 overflow-y-auto max-h-[580px] pr-1">
            {clips.map((clip, idx) => {
              const isCurrent = idx === activeIndex;
              const formatSec = Math.round(clip.duration_ms / 1000);
              const numStr = (idx + 1).toString().padStart(2, "0");

              return (
                <div
                  key={clip.id}
                  onClick={() => selectClipByIndex(idx)}
                  className={`p-3.5 rounded-xl border transition-all cursor-pointer flex items-center gap-3.5 group ${
                    isCurrent
                      ? "bg-white/10 border-white/20 shadow-[0_0_20px_rgba(255,99,33,0.1)] ring-1 ring-[#FF6321]"
                      : idx === activeIndex + 1
                      ? "bg-white/5 border-white/10 opacity-90 hover:opacity-100"
                      : idx === activeIndex + 2
                      ? "bg-white/5 border-white/5 opacity-60 hover:opacity-100"
                      : "bg-white/2 border-white/5 opacity-40 hover:opacity-80"
                  }`}
                >
                  {/* Number Badge */}
                  <div
                    className={`w-10 h-10 rounded-lg flex items-center justify-center font-black text-xs font-mono flex-shrink-0 transition-transform ${
                      isCurrent
                        ? "bg-[#FF6321] text-black scale-105"
                        : "bg-white/10 text-white/50 group-hover:text-white"
                    }`}
                  >
                    {numStr}
                  </div>

                  {/* Clip Info */}
                  <div className="flex-1 min-w-0">
                    <h4 className="font-black uppercase text-xs tracking-tight text-[#F5F5F5] truncate group-hover:text-[#FF6321] transition-colors">
                      {clip.title}
                    </h4>
                    <p className="text-[10px] font-mono text-white/40 uppercase mt-0.5 truncate">
                      {formatSec}s • @{clip.creator_name} • {clip.category}
                    </p>
                  </div>

                  {/* Active Playing Indicator */}
                  {isCurrent && (
                    <div className="w-2 h-2 rounded-full bg-[#FF6321] animate-pulse" />
                  )}
                </div>
              );
            })}
          </div>

          <div className="mt-6 pt-4 border-t border-white/10 text-[10px] font-mono text-white/30 flex items-center justify-between">
            <span>Cosine Threshold: &gt; 0.85</span>
            <span className="text-green-400 font-bold">HNSW Index Active</span>
          </div>
        </div>
      </div>

      {/* Comment Sheet Drawer */}
      <CommentSheet
        clip={selectedClipForComments}
        isOpen={!!selectedClipForComments}
        onClose={() => setSelectedClipForComments(null)}
      />

      {/* Share Modal */}
      <ShareModal
        clip={selectedClipForShare}
        isOpen={!!selectedClipForShare}
        onClose={() => setSelectedClipForShare(null)}
      />
    </div>
  );
};
