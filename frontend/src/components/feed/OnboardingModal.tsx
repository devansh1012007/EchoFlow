import React, { useState } from "react";
import { Sparkles, Check, Headphones } from "lucide-react";
import { feedAPI } from "../../api/client";

interface OnboardingModalProps {
  isOpen: boolean;
  onClose: () => void;
  onInitialized: () => void;
}

const AVAILABLE_TAGS = [
  { id: "comedy", label: "Comedy & Roasts", emoji: "🎙️", desc: "Tech roasts & standup" },
  { id: "science", label: "Science Bites", emoji: "🔬", desc: "Quantum, space & biology" },
  { id: "motivation", label: "Daily Motivation", emoji: "⚡", desc: "Stoicism & discipline" },
  { id: "music", label: "Beat Snippets", emoji: "🎧", desc: "Lo-fi, vinyl & synth loops" },
  { id: "quotes", label: "Deep Quotes", emoji: "📜", desc: "Philosophers & thinkers" },
  { id: "tech", label: "Coding & Startups", emoji: "💻", desc: "Architecture & dev humor" },
  { id: "instrumental", label: "Focus Waves", emoji: "🌊", desc: "Binaural & ambient drones" },
  { id: "mindset", label: "Psychology & Flow", emoji: "🧠", desc: "Cognition & habits" },
];

export const OnboardingModal: React.FC<OnboardingModalProps> = ({ isOpen, onClose, onInitialized }) => {
  const [selectedTags, setSelectedTags] = useState<string[]>(["comedy", "science"]);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  if (!isOpen) return null;

  const toggleTag = (id: string) => {
    if (selectedTags.includes(id)) {
      setSelectedTags(selectedTags.filter((t) => t !== id));
    } else {
      setSelectedTags([...selectedTags, id]);
    }
  };

  const handleInitialize = async () => {
    if (selectedTags.length === 0) {
      setErrorMsg("Please select at least one vibe tag.");
      return;
    }

    setIsSubmitting(true);
    setErrorMsg(null);
    try {
      await feedAPI.initializeTags(selectedTags);
      sessionStorage.removeItem("ef_new_user");
      onInitialized();
      onClose();
    } catch (err: any) {
      setErrorMsg(err?.message || "Failed to initialize recommendation engine.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/85 backdrop-blur-md animate-in fade-in duration-200">
      <div className="w-full max-w-lg bg-[#111111] border border-white/15 rounded-3xl p-6 md:p-8 shadow-2xl relative">
        <div className="flex items-center gap-3 mb-3">
          <div className="w-10 h-10 rounded-xl bg-[#FF6321] flex items-center justify-center text-black font-black">
            <Sparkles className="w-5 h-5 stroke-[2.5]" />
          </div>
          <div>
            <h2 className="text-xl md:text-2xl font-black uppercase tracking-tight text-white">
              Vector Cold-Start
            </h2>
            <p className="text-[10px] font-mono uppercase text-white/40">
              Initialize 384-dimensional cosine preference weights
            </p>
          </div>
        </div>

        <p className="text-xs font-mono uppercase text-white/60 mb-4 leading-relaxed">
          EchoFlow learns what you love through continuous listening. Select your favorite audio vibes to seed your recommendation index:
        </p>

        {errorMsg && (
          <div className="mb-4 p-3 rounded-xl bg-rose-500/15 border border-rose-500/30 text-rose-300 text-xs font-mono">
            {errorMsg}
          </div>
        )}

        {/* Tag Grid */}
        <div className="grid grid-cols-2 gap-2.5 max-h-72 overflow-y-auto pr-1 mb-6">
          {AVAILABLE_TAGS.map((tag) => {
            const isSelected = selectedTags.includes(tag.id);
            return (
              <button
                key={tag.id}
                type="button"
                onClick={() => toggleTag(tag.id)}
                className={`p-3.5 rounded-xl text-left border transition-all flex items-start justify-between gap-2 ${
                  isSelected
                    ? "bg-white/10 border-[#FF6321] text-white ring-1 ring-[#FF6321] shadow-[0_0_15px_rgba(255,99,33,0.15)]"
                    : "bg-black/50 border-white/10 text-white/60 hover:text-white hover:border-white/20"
                }`}
              >
                <div>
                  <div className="flex items-center gap-1.5 font-black uppercase text-xs">
                    <span>{tag.emoji}</span>
                    <span>{tag.label}</span>
                  </div>
                  <p className="text-[10px] font-mono text-white/40 mt-1 leading-snug uppercase">
                    {tag.desc}
                  </p>
                </div>
                <div
                  className={`w-5 h-5 rounded flex items-center justify-center flex-shrink-0 transition-colors ${
                    isSelected ? "bg-[#FF6321] text-black font-black" : "border border-white/20"
                  }`}
                >
                  {isSelected && <Check className="w-3 h-3 stroke-[3]" />}
                </div>
              </button>
            );
          })}
        </div>

        {/* Action Button */}
        <div className="flex items-center justify-between gap-3 pt-4 border-t border-white/10">
          <span className="text-[11px] font-mono uppercase text-white/40 flex items-center gap-1.5">
            <Headphones className="w-3.5 h-3.5 text-[#FF6321]" />
            {selectedTags.length} Vibes Armed
          </span>

          <div className="flex gap-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-xs font-mono uppercase font-bold text-white/40 hover:text-white transition-colors"
            >
              Skip
            </button>
            <button
              type="button"
              disabled={isSubmitting || selectedTags.length === 0}
              onClick={handleInitialize}
              className="px-6 py-2.5 rounded-xl bg-[#FF6321] text-black font-black text-xs uppercase tracking-wider shadow-[0_0_20px_rgba(255,99,33,0.3)] hover:bg-[#ff763a] active:scale-95 transition-all disabled:opacity-40"
            >
              {isSubmitting ? "Synthesizing..." : "Initialize Feed →"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
