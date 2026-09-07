import React, { useState } from "react";
import { X, Check, Copy, Send, Radio, Search } from "lucide-react";
import { shareAPI } from "../../api/client";
import { FeedClip } from "../../types/echoflow";

interface ShareModalProps {
  clip: FeedClip | null;
  isOpen: boolean;
  onClose: () => void;
}

interface PeerUser {
  id: number;
  username: string;
}

const DEFAULT_PEERS: PeerUser[] = [
  { id: 1, username: "alex" },
  { id: 2, username: "roastmaster" },
  { id: 3, username: "curiosity_lab" },
  { id: 4, username: "stoic_focus" },
];

export const ShareModal: React.FC<ShareModalProps> = ({ clip, isOpen, onClose }) => {
  const [copied, setCopied] = useState<boolean>(false);
  const [sentUsers, setSentUsers] = useState<Record<number, boolean>>({});
  const [searchUsername, setSearchUsername] = useState<string>("");
  const [foundUser, setFoundUser] = useState<PeerUser | null>(null);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [isSearching, setIsSearching] = useState<boolean>(false);

  const handleCopyLink = () => {
    if (!clip) return;
    navigator.clipboard.writeText(`${window.location.origin}/?clip=${clip.id}`);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleSearchUser = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!searchUsername.trim()) return;
    setIsSearching(true);
    setSearchError(null);
    try {
      const u = await shareAPI.findUser(searchUsername.trim());
      setFoundUser(u);
    } catch (err: any) {
      setSearchError("Peer listener not found in directory.");
      setFoundUser(null);
    } finally {
      setIsSearching(false);
    }
  };

  const handleSendToUser = async (recipientId: number) => {
    if (!clip || sentUsers[recipientId]) return;

    try {
      await shareAPI.sendShare(clip.id, recipientId);
      setSentUsers((prev) => ({ ...prev, [recipientId]: true }));
    } catch (err) {
      console.warn("Failed to share with user:", err);
    }
  };

  if (!isOpen || !clip) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm">
      <div className="w-full max-w-sm bg-[#111111] border border-white/15 rounded-3xl p-6 shadow-2xl space-y-5 animate-in zoom-in-95 duration-150">
        {/* Header */}
        <div className="flex items-center justify-between pb-3 border-b border-white/10">
          <div className="flex items-center gap-2">
            <Radio className="w-4 h-4 text-[#FF6321]" />
            <h3 className="text-sm font-black uppercase tracking-tight text-white">
              Dispatch to Peer Queue
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded-full text-white/40 hover:text-white"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Clip Summary Preview */}
        <div className="p-3.5 rounded-xl bg-black/60 border border-white/10 flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-[#FF6321] text-black flex items-center justify-center font-black text-xs font-mono">
            {clip.category.slice(0, 2).toUpperCase()}
          </div>
          <div className="flex-1 min-w-0">
            <h4 className="text-xs font-black uppercase text-white truncate">{clip.title}</h4>
            <p className="text-[10px] font-mono text-white/40 uppercase truncate">
              @{clip.creator_name} • {clip.category}
            </p>
          </div>
        </div>

        {/* Copy Link Button */}
        <div>
          <button
            type="button"
            onClick={handleCopyLink}
            className="w-full py-3 px-4 rounded-xl bg-white/5 hover:bg-white/10 border border-white/15 text-xs font-mono font-bold uppercase text-white flex items-center justify-between transition-colors"
          >
            <div className="flex items-center gap-2">
              <Copy className="w-4 h-4 text-[#FF6321]" />
              <span>{copied ? "Direct Stream URL Copied" : "Copy Audio Reel Link"}</span>
            </div>
            {copied && <Check className="w-4 h-4 text-green-400" />}
          </button>
        </div>

        {/* Find Peer Input */}
        <form onSubmit={handleSearchUser} className="space-y-2">
          <label className="text-[10px] font-mono uppercase text-white/40 block">
            Find Listener by Username
          </label>
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={searchUsername}
              onChange={(e) => setSearchUsername(e.target.value)}
              placeholder="e.g. roastmaster"
              className="flex-1 bg-black border border-white/15 rounded-xl px-3 py-2 text-xs font-mono text-white placeholder-white/20 focus:outline-none focus:border-[#FF6321]"
            />
            <button
              type="submit"
              disabled={isSearching}
              className="p-2.5 rounded-xl bg-white/10 hover:bg-white/20 text-white transition-colors"
            >
              <Search className="w-4 h-4" />
            </button>
          </div>
          {searchError && (
            <p className="text-[10px] font-mono text-rose-400">{searchError}</p>
          )}
        </form>

        {/* Search Result or Default Peers */}
        <div className="space-y-2">
          <span className="text-[10px] font-mono uppercase text-white/40 block">
            {foundUser ? "Discovered Listener" : "Network Peers"}
          </span>
          <div className="max-h-40 overflow-y-auto space-y-2 pr-1">
            {(foundUser ? [foundUser] : DEFAULT_PEERS).map((peer) => {
              const isSent = sentUsers[peer.id];
              return (
                <div
                  key={peer.id}
                  className="flex items-center justify-between p-2.5 rounded-xl bg-white/5 border border-white/10"
                >
                  <div className="flex items-center gap-2.5">
                    <div className="w-7 h-7 rounded-full bg-white/10 flex items-center justify-center font-black text-xs text-[#FF6321]">
                      {peer.username[0]?.toUpperCase()}
                    </div>
                    <span className="text-xs font-black uppercase text-white">
                      @{peer.username}
                    </span>
                  </div>

                  <button
                    type="button"
                    onClick={() => handleSendToUser(peer.id)}
                    disabled={isSent}
                    className={`px-3 py-1 rounded text-[10px] font-black uppercase tracking-wider transition-all flex items-center gap-1 ${
                      isSent
                        ? "bg-green-500/20 text-green-400 border border-green-500/30"
                        : "bg-[#FF6321] text-black hover:bg-[#ff753b]"
                    }`}
                  >
                    {isSent ? (
                      <>
                        <Check className="w-3 h-3" />
                        Sent
                      </>
                    ) : (
                      <>
                        <Send className="w-3 h-3" />
                        Stream
                      </>
                    )}
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
};
