import React, { useState } from "react";
import { X, Radio, ArrowRight } from "lucide-react";
import { useAuth } from "../../stores/auth";

interface AuthModalProps {
  isOpen: boolean;
  onClose: () => void;
  onLoginSuccess: () => void;
}

export const AuthModal: React.FC<AuthModalProps> = ({ isOpen, onClose, onLoginSuccess }) => {
  const [isRegister, setIsRegister] = useState<boolean>(false);
  const [username, setUsername] = useState<string>("");
  const [email, setEmail] = useState<string>("");
  const [password, setPassword] = useState<string>("");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);

  const { login, register } = useAuth();

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg(null);
    setIsLoading(true);

    try {
      if (isRegister) {
        if (!email.trim()) {
          setErrorMsg("Valid email address is required.");
          setIsLoading(false);
          return;
        }
        await register(username.trim(), email.trim(), password);
        onLoginSuccess();
        onClose();
      } else {
        await login(username.trim(), password);
        onLoginSuccess();
        onClose();
      }
    } catch (err: any) {
      setErrorMsg(
        err?.data?.non_field_errors?.[0] ||
        err?.data?.username?.[0] ||
        err?.data?.email?.[0] ||
        err?.message ||
        "Authentication failed. Please verify credentials."
      );
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/85 backdrop-blur-md animate-in fade-in duration-200">
      <div className="w-full max-w-md bg-[#111111] border border-white/15 rounded-3xl p-6 md:p-8 shadow-2xl relative">
        {/* Close Button */}
        <button
          type="button"
          onClick={onClose}
          className="absolute right-6 top-6 text-white/40 hover:text-white transition-colors"
        >
          <X className="w-5 h-5" />
        </button>

        {/* Brand Header */}
        <div className="flex items-center gap-3 mb-6">
          <div className="w-10 h-10 rounded-xl bg-[#FF6321] flex items-center justify-center text-black font-black">
            <Radio className="w-5 h-5 stroke-[2.5]" />
          </div>
          <div>
            <h2 className="text-xl md:text-2xl font-black uppercase tracking-tight text-white">
              {isRegister ? "Join EchoFlow" : "Account Access"}
            </h2>
            <p className="text-[10px] font-mono uppercase text-white/40">
              {isRegister ? "Start publishing and listening" : "Access your continuous audio reels"}
            </p>
          </div>
        </div>

        {errorMsg && (
          <div className="mb-4 p-3 rounded-xl bg-rose-500/15 border border-rose-500/30 text-rose-300 text-xs font-mono">
            {errorMsg}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="text-xs font-mono uppercase text-white/50 block mb-1">
              Username
            </label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="e.g. soundwave"
              required
              className="w-full bg-black border border-white/15 rounded-xl px-4 py-3 text-xs font-mono text-white placeholder-white/20 focus:outline-none focus:border-[#FF6321]"
            />
          </div>

          {isRegister && (
            <div>
              <label className="text-xs font-mono uppercase text-white/50 block mb-1">
                Email Address
              </label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="soundwave@echoflow.fm"
                required
                className="w-full bg-black border border-white/15 rounded-xl px-4 py-3 text-xs font-mono text-white placeholder-white/20 focus:outline-none focus:border-[#FF6321]"
              />
            </div>
          )}

          <div>
            <label className="text-xs font-mono uppercase text-white/50 block mb-1">
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              required
              className="w-full bg-black border border-white/15 rounded-xl px-4 py-3 text-xs font-mono text-white placeholder-white/20 focus:outline-none focus:border-[#FF6321]"
            />
          </div>

          <button
            type="submit"
            disabled={isLoading}
            className="w-full py-3.5 rounded-xl bg-[#FF6321] text-black font-black text-xs uppercase tracking-widest shadow-[0_0_20px_rgba(255,99,33,0.3)] hover:bg-[#ff763a] active:scale-95 transition-all flex items-center justify-center gap-2 mt-6 disabled:opacity-50"
          >
            <span>{isLoading ? "Authenticating..." : isRegister ? "Create Audio Account" : "Enter Feed"}</span>
            <ArrowRight className="w-4 h-4 stroke-[3]" />
          </button>
        </form>

        {/* Mode Toggle */}
        <div className="text-center pt-4 mt-4 border-t border-white/10">
          <button
            type="button"
            onClick={() => {
              setIsRegister(!isRegister);
              setErrorMsg(null);
            }}
            className="text-xs font-mono uppercase text-[#FF6321] hover:underline"
          >
            {isRegister ? "Already registered? Log in here" : "Need an account? Sign up here"}
          </button>
        </div>
      </div>
    </div>
  );
};
