import React from "react";
import { Play, Pause, SkipForward, RotateCw } from "lucide-react";
import { usePlayer } from "../../stores/player";

interface MiniPlayerProps {
  onOpenFeed: () => void;
}

export const MiniPlayer: React.FC<MiniPlayerProps> = ({ onOpenFeed }) => {
  const {
    currentClip,
    isPlaying,
    togglePlay,
    progress,
    audioFrequencies,
    skipForward,
    nextClip,
  } = usePlayer();

  if (!currentClip) return null;

  return (
    <div className="fixed bottom-16 md:bottom-6 left-0 right-0 z-30 px-4 pb-1">
      <div className="max-w-2xl mx-auto bg-[#111111]/95 backdrop-blur-xl border border-white/15 rounded-2xl p-3 shadow-2xl flex items-center gap-3 relative overflow-hidden">
        {/* Progress Bar Top Rim */}
        <div className="absolute top-0 left-0 right-0 h-0.5 bg-white/10">
          <div
            className="h-full bg-[#FF6321] transition-all duration-200"
            style={{ width: `${Math.min(100, Math.max(0, progress * 100))}%` }}
          />
        </div>

        {/* Audio Visualizer Box */}
        <div
          onClick={onOpenFeed}
          className="relative w-11 h-11 rounded-lg bg-black border border-white/15 flex items-center justify-center cursor-pointer overflow-hidden flex-shrink-0 group"
        >
          <div className="flex items-end gap-0.5 h-6 px-1">
            {audioFrequencies.slice(0, 5).map((freq, idx) => (
              <span
                key={idx}
                className="w-1 bg-[#FF6321] transition-all duration-75"
                style={{ height: `${Math.max(4, Math.min(22, (freq / 80) * 22))}px` }}
              />
            ))}
          </div>
        </div>

        {/* Clip Info */}
        <div onClick={onOpenFeed} className="flex-1 min-w-0 cursor-pointer">
          <div className="flex items-center gap-1.5">
            <span className="text-[9px] uppercase font-black tracking-widest text-[#FF6321] px-1 py-0.2 rounded bg-[#FF6321]/15">
              {currentClip.category}
            </span>
            <span className="text-[10px] font-mono uppercase text-white/40 truncate">
              @{currentClip.creator_name}
            </span>
          </div>
          <p className="text-xs font-black uppercase tracking-tight text-white truncate mt-0.5">
            {currentClip.title}
          </p>
        </div>

        {/* Controls */}
        <div className="flex items-center gap-1.5">
          {/* Skip 10s */}
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              skipForward(10);
            }}
            className="p-1.5 rounded-full hover:bg-white/10 text-white/60 hover:text-white transition-colors"
            title="Skip 10s"
          >
            <RotateCw className="w-4 h-4" />
          </button>

          {/* Play/Pause */}
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              togglePlay();
            }}
            className="w-9 h-9 rounded-full bg-[#FF6321] hover:bg-[#ff763a] text-black flex items-center justify-center shadow-[0_0_15px_rgba(255,99,33,0.3)] transition-transform active:scale-95"
          >
            {isPlaying ? <Pause className="w-4 h-4 fill-black" /> : <Play className="w-4 h-4 fill-black ml-0.5" />}
          </button>

          {/* Next Reel */}
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              nextClip("manual");
            }}
            className="p-1.5 rounded-full hover:bg-white/10 text-white/60 hover:text-white transition-colors"
            title="Next Reel"
          >
            <SkipForward className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
};
