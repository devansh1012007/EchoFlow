import React, { useEffect, useState } from "react";
import { Inbox as InboxIcon, Play, Pause, Trash2, Radio } from "lucide-react";
import { shareAPI } from "../api/client";
import { usePlayer } from "../stores/player";
import { ShareEvent } from "../types/echoflow";

interface InboxPageProps {
  onRefreshUnread: () => void;
}

export const InboxPage: React.FC<InboxPageProps> = ({ onRefreshUnread }) => {
  const [shares, setShares] = useState<ShareEvent[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const { currentClip, isPlaying, playClip, togglePlay } = usePlayer();

  useEffect(() => {
    loadInbox();
  }, []);

  const loadInbox = async () => {
    setIsLoading(true);
    setErrorMsg(null);
    try {
      const data = await shareAPI.getInbox();
      setShares(data);
      onRefreshUnread();
    } catch (err: any) {
      setErrorMsg(err?.message || "Failed to load audio inbox");
    } finally {
      setIsLoading(false);
    }
  };

  const handlePlayShare = async (item: ShareEvent) => {
    if (!item.is_read) {
      try {
        await shareAPI.markRead(item.id);
        setShares((prev) =>
          prev.map((s) => (s.id === item.id ? { ...s, is_read: true } : s))
        );
        onRefreshUnread();
      } catch (err) {
        console.warn("Could not mark share as read:", err);
      }
    }

    if (currentClip?.id === item.clip.id) {
      togglePlay();
    } else {
      playClip(item.clip);
    }
  };

  const handleDeleteShare = async (e: React.MouseEvent, shareId: number) => {
    e.stopPropagation();
    try {
      await shareAPI.deleteShare(shareId);
      setShares((prev) => prev.filter((s) => s.id !== shareId));
      onRefreshUnread();
    } catch (err) {
      console.warn("Could not delete share:", err);
    }
  };

  return (
    <div className="w-full max-w-4xl mx-auto px-4 md:px-8 py-6 pb-28 space-y-6">
      <div className="border-b border-white/10 pb-4 flex items-center justify-between">
        <div>
          <h1 className="text-3xl md:text-4xl font-black uppercase tracking-tighter text-[#F5F5F5] flex items-center gap-3">
            <InboxIcon className="w-7 h-7 text-[#FF6321]" />
            Audio Inbox
          </h1>
          <p className="text-xs font-mono uppercase text-white/40 mt-1">
            Audio reels sent directly to your queue by network peers
          </p>
        </div>
        <span className="text-xs font-mono text-[#FF6321] font-bold uppercase">
          {shares.filter((s) => !s.is_read).length} UNREAD
        </span>
      </div>

      {isLoading ? (
        <div className="py-24 flex flex-col items-center justify-center text-white/40 font-mono text-xs uppercase gap-2">
          <div className="w-8 h-8 border-2 border-[#FF6321] border-t-transparent rounded-full animate-spin" />
          <span>Polling audio stream messages...</span>
        </div>
      ) : errorMsg ? (
        <div className="p-6 rounded-2xl bg-[#111111] border border-white/10 text-center text-rose-400 text-xs font-mono">
          {errorMsg}
        </div>
      ) : shares.length === 0 ? (
        <div className="p-16 rounded-3xl bg-[#111111] border border-white/10 text-center space-y-3">
          <Radio className="w-12 h-12 text-white/20 mx-auto" />
          <p className="text-base font-black uppercase text-white">Audio Inbox Clear</p>
          <p className="text-xs font-mono uppercase text-white/40 max-w-xs mx-auto">
            Directly shared audio reels from creators and friends will appear here for hands-free listening.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {shares.map((item) => {
            const isThisPlaying = currentClip?.id === item.clip.id && isPlaying;

            return (
              <div
                key={item.id}
                onClick={() => handlePlayShare(item)}
                className={`p-4 rounded-2xl border transition-all cursor-pointer flex items-center justify-between gap-4 group ${
                  !item.is_read
                    ? "bg-[#111111] border-[#FF6321] shadow-[0_0_20px_rgba(255,99,33,0.1)] ring-1 ring-[#FF6321]"
                    : "bg-[#111111]/80 hover:bg-[#111111] border-white/10"
                }`}
              >
                {/* Play button */}
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
                    <span className="text-xs font-black uppercase text-[#FF6321]">
                      @{item.sender_name}
                    </span>
                    <span className="text-[10px] font-mono uppercase text-white/40">shared audio reel</span>
                    {!item.is_read && (
                      <span className="px-1.5 py-0.2 rounded bg-[#FF6321] text-black text-[9px] font-mono font-black uppercase">
                        NEW
                      </span>
                    )}
                  </div>
                  <h3 className="text-sm font-black uppercase tracking-tight text-white truncate group-hover:text-[#FF6321] transition-colors">
                    {item.clip_title}
                  </h3>
                  <p className="text-[10px] font-mono uppercase text-white/40">
                    BY @{item.clip.creator_name} • {item.clip.category}
                  </p>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={(e) => handleDeleteShare(e, item.id)}
                    className="p-2 rounded-lg text-white/30 hover:text-rose-400 hover:bg-white/10 transition-colors"
                    title="Remove from Inbox"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
