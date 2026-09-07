import React, { useEffect, useState } from "react";
import { Compass, Play, Pause, Heart, Headphones } from "lucide-react";
import { feedAPI } from "../api/client";
import { usePlayer } from "../stores/player";
import { FeedClip } from "../types/echoflow";

interface ExplorePageProps {
  onOpenFeed: () => void;
}

const CATEGORIES = [
  { id: "all", label: "All Hubs" },
  { id: "comedy", label: "Comedy & Roasts" },
  { id: "science", label: "Science Bites" },
  { id: "motivation", label: "Motivation" },
  { id: "music", label: "Beat Loops" },
  { id: "quotes", label: "Deep Quotes" },
  { id: "instrumental", label: "Focus Waves" },
];

export const ExplorePage: React.FC<ExplorePageProps> = ({ onOpenFeed: _onOpenFeed }) => {
  const [selectedCategory, setSelectedCategory] = useState<string>("all");
  const [clips, setClips] = useState<FeedClip[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const { currentClip, isPlaying, playClip, togglePlay } = usePlayer();

  useEffect(() => {
    loadSuggestions(selectedCategory);
  }, [selectedCategory]);

  const loadSuggestions = async (cat: string) => {
    setIsLoading(true);
    setErrorMsg(null);
    try {
      const res = await feedAPI.getSuggestions(cat);
      setClips(res.results);
    } catch (err: any) {
      setErrorMsg(err?.message || "Failed to load discovery suggestions");
    } finally {
      setIsLoading(false);
    }
  };

  const handlePlayClip = (clip: FeedClip) => {
    if (currentClip?.id === clip.id) {
      togglePlay();
    } else {
      playClip(clip, clips);
    }
  };

  return (
    <div className="w-full max-w-5xl mx-auto px-4 md:px-8 py-6 pb-28 space-y-6">
      {/* Title */}
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-2 border-b border-white/10 pb-4">
        <div>
          <h1 className="text-3xl md:text-4xl font-black uppercase tracking-tighter text-[#F5F5F5] flex items-center gap-3">
            <Compass className="w-7 h-7 text-[#FF6321]" />
            Vector Hubs
          </h1>
          <p className="text-xs font-mono uppercase tracking-wider text-white/40 mt-1">
            Browse audio reels clustered by semantic embeddings and acoustic vectors
          </p>
        </div>
        <span className="text-[10px] font-mono text-[#FF6321] uppercase">
          CLUSTER_INDEX: PGVECTOR_384D
        </span>
      </div>

      {/* Category Pills Slider */}
      <div className="flex items-center gap-2 overflow-x-auto pb-2 scrollbar-none -mx-4 px-4">
        {CATEGORIES.map((cat) => {
          const isSelected = selectedCategory === cat.id;
          return (
            <button
              key={cat.id}
              type="button"
              onClick={() => setSelectedCategory(cat.id)}
              className={`px-4 py-2 rounded-lg text-xs font-black uppercase tracking-wider whitespace-nowrap transition-all border ${
                isSelected
                  ? "bg-[#FF6321] text-black border-[#FF6321] shadow-[0_0_15px_rgba(255,99,33,0.3)]"
                  : "bg-white/5 text-white/50 hover:text-white border-white/10 hover:bg-white/10"
              }`}
            >
              {cat.label}
            </button>
          );
        })}
      </div>

      {/* Content Grid */}
      {isLoading ? (
        <div className="py-24 flex flex-col items-center justify-center text-white/40 font-mono text-xs uppercase gap-2">
          <div className="w-8 h-8 border-2 border-[#FF6321] border-t-transparent rounded-full animate-spin" />
          <span>Clustering acoustic embeddings...</span>
        </div>
      ) : errorMsg ? (
        <div className="p-6 rounded-2xl bg-[#111111] border border-white/10 text-center text-rose-400 text-xs font-mono">
          {errorMsg}
        </div>
      ) : clips.length === 0 ? (
        <div className="p-12 rounded-3xl bg-[#111111] border border-white/10 text-center text-white/40 text-xs uppercase font-mono">
          No audio reels found in this category cluster.
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {clips.map((clip) => {
            const isThisPlaying = currentClip?.id === clip.id && isPlaying;
            const isThisSelected = currentClip?.id === clip.id;

            return (
              <div
                key={clip.id}
                onClick={() => handlePlayClip(clip)}
                className={`p-5 rounded-2xl border transition-all cursor-pointer flex items-center gap-4 group ${
                  isThisSelected
                    ? "bg-[#111111] border-[#FF6321] shadow-[0_0_20px_rgba(255,99,33,0.15)] ring-1 ring-[#FF6321]"
                    : "bg-[#111111]/80 hover:bg-[#111111] border-white/10 hover:border-white/25"
                }`}
              >
                {/* Play Button */}
                <div
                  className={`w-12 h-12 rounded-xl flex items-center justify-center flex-shrink-0 transition-transform ${
                    isThisPlaying
                      ? "bg-[#FF6321] text-black scale-105 shadow-[0_0_15px_rgba(255,99,33,0.35)]"
                      : "bg-white/10 text-white group-hover:bg-[#FF6321] group-hover:text-black"
                  }`}
                >
                  {isThisPlaying ? (
                    <Pause className="w-5 h-5 fill-current" />
                  ) : (
                    <Play className="w-5 h-5 fill-current ml-0.5" />
                  )}
                </div>

                {/* Info */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-[9px] uppercase font-black tracking-widest text-[#FF6321] px-1.5 py-0.2 rounded bg-[#FF6321]/15">
                      {clip.category}
                    </span>
                    <span className="text-[10px] font-mono uppercase text-white/40 truncate">
                      @{clip.creator_name}
                    </span>
                  </div>
                  <h3 className="text-sm font-black uppercase tracking-tight text-white leading-snug line-clamp-2 group-hover:text-[#FF6321] transition-colors">
                    {clip.title}
                  </h3>

                  <div className="flex items-center gap-4 text-[10px] font-mono uppercase text-white/40 mt-2">
                    <span className="flex items-center gap-1">
                      <Heart className="w-3 h-3 text-[#FF6321]" />
                      {clip.likes}
                    </span>
                    <span className="flex items-center gap-1">
                      <Headphones className="w-3 h-3" />
                      {Math.max(15, clip.likes + clip.shares * 2)} Listens
                    </span>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
