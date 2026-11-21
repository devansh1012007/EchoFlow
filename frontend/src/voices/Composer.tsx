import { useEffect, useRef, useState } from "react";
import { Mic, Square, Play, Pause, Trash2, Send } from "lucide-react";
import { VoiceRecorder, VoiceRecorderError, type RecorderResult } from "./recorder";
import { MAX_VOICE_MS, MIN_VOICE_MS } from "./types";
import { useVoicePlayback } from "./VoicePlayback";

interface ComposerProps {
  /** Post the finished recording (top-level, or a reply when parentId is set). */
  onPost: (input: { durationMs: number; mimeType: string; peaks: number[]; audio: Blob; parentId?: string | null }) => Promise<unknown>;
  /** Set for the reply composer: smaller, framed as answering. */
  parentId?: string;
}

function formatMs(ms: number): string {
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/**
 * Record, review, post — three states, one button's journey.
 *
 * Every microphone failure the recorder can name has its own words here,
 * because "nothing happened" is the alternative and it is worse. The level
 * meter is the recorder's own smoothed RMS, not decoration: it is how the
 * person knows the mic is live before they commit 60 seconds to it.
 *
 * The review step is deliberate: a spoken post cannot be edited after it is
 * sent, so the one moment it can be discarded is before.
 */
export function Composer({ onPost, parentId }: ComposerProps) {
  const [phase, setPhase] = useState<"idle" | "recording" | "reviewing">("idle");
  const [micError, setMicError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [level, setLevel] = useState(0);
  const [result, setResult] = useState<RecorderResult | null>(null);
  const [postError, setPostError] = useState<string | null>(null);
  const [isPosting, setIsPosting] = useState(false);
  const recorderRef = useRef<VoiceRecorder | null>(null);

  const { activeId, isPlaying, toggle, stop } = useVoicePlayback();
  const reviewingVoiceId = `composer-preview-${parentId ?? "root"}`;

  const startRecording = async () => {
    setMicError(null);
    setPostError(null);
    const recorder = new VoiceRecorder({
      maxMs: MAX_VOICE_MS,
      onLevel: (lvl, ms) => {
        setLevel(lvl);
        setElapsed(ms);
      },
      onAutoStop: (res) => {
        setResult(res);
        setPhase("reviewing");
      },
    });
    recorderRef.current = recorder;
    try {
      await recorder.start();
      setPhase("recording");
      setElapsed(0);
      setLevel(0);
    } catch (err) {
      setMicError(
        err instanceof VoiceRecorderError
          ? err.message
          : "The microphone could not be started.",
      );
      recorderRef.current = null;
    }
  };

  const stopRecording = async () => {
    const recorder = recorderRef.current;
    if (!recorder) return;
    try {
      const res = await recorder.stop();
      if (res.durationMs < MIN_VOICE_MS) {
        setMicError("That was too short — hold the button and speak for at least a second.");
        setResult(null);
        setPhase("idle");
        return;
      }
      setResult(res);
      setPhase("reviewing");
    } catch (err) {
      setMicError(err instanceof VoiceRecorderError ? err.message : "The recording failed.");
      setPhase("idle");
    } finally {
      recorderRef.current = null;
    }
  };

  const discard = () => {
    recorderRef.current?.cancel();
    recorderRef.current = null;
    // The preview uses the playback provider with a synthetic id; make sure it
    // stops before the blob goes away.
    if (activeId === reviewingVoiceId) stop();
    setResult(null);
    setPhase("idle");
    setElapsed(0);
    setLevel(0);
  };

  const post = async () => {
    if (!result) return;
    setIsPosting(true);
    setPostError(null);
    try {
      await onPost({
        durationMs: result.durationMs,
        mimeType: result.mimeType,
        peaks: result.peaks,
        audio: result.blob,
        parentId: parentId ?? null,
      });
      discard();
    } catch (err) {
      setPostError(err instanceof Error ? err.message : "The post could not be saved.");
    } finally {
      setIsPosting(false);
    }
  };

  // A recorder left recording when the composer unmounts (page switch) must
  // not hold the microphone.
  useEffect(() => {
    return () => {
      recorderRef.current?.cancel();
    };
  }, []);

  const tooShort = result !== null && result.durationMs < MIN_VOICE_MS;
  const remaining = Math.max(0, MAX_VOICE_MS - elapsed);

  if (parentId && phase === "idle" && !micError) {
    return (
      <button
        type="button"
        onClick={() => void startRecording()}
        className="tap-target flex items-center gap-2 px-3 rounded-lg border border-line-strong bg-canvas text-fg text-xs font-bold hover:border-accent hover:text-link transition-colors"
      >
        <Mic className="w-3.5 h-3.5 text-link" aria-hidden="true" />
        Record a reply
      </button>
    );
  }

  return (
    <div className={`rounded-2xl border p-4 ${parentId ? "border-line bg-canvas" : "border-line-strong bg-panel"}`}>
      {phase === "idle" && (
        <div className="flex flex-col gap-3">
          <button
            type="button"
            onClick={() => void startRecording()}
            className="tap-target flex items-center justify-center gap-2 w-full px-4 rounded-lg bg-accent text-accent-fg text-xs font-bold hover:bg-accent-hover transition-colors"
          >
            <Mic className="w-4 h-4" aria-hidden="true" />
            Record a voice post
          </button>
          <p className="text-[11px] text-fg-subtle">
            Up to 60 seconds. Speak it, review it, post it.
          </p>
        </div>
      )}

      {phase === "recording" && (
        <div className="flex flex-col gap-3" role="status" aria-live="polite">
          <div className="flex items-center justify-between">
            <span className="text-xs font-bold text-link tabular-nums">
              Recording {formatMs(elapsed)}
            </span>
            <span className="text-[11px] tabular-nums text-fg-subtle">
              {Math.ceil(remaining / 1000)}s left
            </span>
          </div>
          {/* The live level meter: real microphone RMS, one bar, no theatre. */}
          <div aria-hidden="true" className="h-2 w-full rounded-full bg-white/10 overflow-hidden">
            <div
              className="h-full bg-accent transition-[width] duration-75"
              style={{ width: `${Math.round(level * 100)}%` }}
            />
          </div>
          <button
            type="button"
            onClick={() => void stopRecording()}
            className="tap-target flex items-center justify-center gap-2 w-full px-4 rounded-lg bg-danger-solid text-danger-fg text-xs font-bold hover:bg-danger-solid-hover transition-colors"
          >
            <Square className="w-3.5 h-3.5 fill-current" aria-hidden="true" />
            Stop recording
          </button>
        </div>
      )}

      {phase === "reviewing" && result && !tooShort && (
        <div className="flex flex-col gap-3">
          <p className="text-xs font-bold text-fg tabular-nums">
            {formatMs(result.durationMs)} recorded
          </p>
          {/* The preview plays through the same one-voice-at-a-time provider
              as every other voice, synthesised as a pseudo-post. */}
          <button
            type="button"
            onClick={() =>
              void toggle({
                id: reviewingVoiceId,
                author: { id: 0, username: "you" },
                createdAt: 0,
                durationMs: result.durationMs,
                mimeType: result.mimeType,
                peaks: result.peaks,
                parentId: null,
                likedByMe: false,
                likeCount: 0,
                replyCount: 0,
                audio: result.blob,
              })
            }
            className="tap-target flex items-center justify-center gap-2 w-full px-4 rounded-lg border border-line-strong bg-canvas text-fg text-xs font-bold hover:border-accent hover:text-link transition-colors"
          >
            {activeId === reviewingVoiceId && isPlaying ? (
              <>
                <Pause className="w-3.5 h-3.5" aria-hidden="true" />
                Pause playback
              </>
            ) : (
              <>
                <Play className="w-3.5 h-3.5 fill-current" aria-hidden="true" />
                Play back recording
              </>
            )}
          </button>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={discard}
              className="tap-target flex-1 flex items-center justify-center gap-2 px-4 rounded-lg border border-line bg-canvas text-fg-muted text-xs font-bold hover:text-danger hover:border-danger/40 transition-colors"
            >
              <Trash2 className="w-3.5 h-3.5" aria-hidden="true" />
              Discard
            </button>
            <button
              type="button"
              onClick={() => void post()}
              disabled={isPosting}
              className="tap-target flex-1 flex items-center justify-center gap-2 px-4 rounded-lg bg-accent text-accent-fg text-xs font-bold hover:bg-accent-hover transition-colors disabled:opacity-60"
            >
              <Send className="w-3.5 h-3.5" aria-hidden="true" />
              {isPosting ? "Posting…" : "Post voice"}
            </button>
          </div>
        </div>
      )}

      {micError && (
        <p role="alert" className="text-[11px] font-bold text-danger">
          {micError}
        </p>
      )}
      {postError && (
        <p role="alert" className="text-[11px] font-bold text-danger">
          {postError}
        </p>
      )}
    </div>
  );
}
