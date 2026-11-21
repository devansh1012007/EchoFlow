import { useState } from "react";
import { Heart, Play, Pause, Trash2, MessageSquare } from "lucide-react";
import { useAuth } from "../stores/auth";
import { useVoicePlayback } from "./VoicePlayback";
import type { VoicePost } from "./types";

interface VoiceCardProps {
  post: VoicePost;
  onToggleLike: (id: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  /** Open this post's thread (replies). Optional: the thread's own cards have no replies. */
  onOpenReplies?: (post: VoicePost) => void;
}

function timeAgo(createdAt: number): string {
  const s = Math.max(1, Math.round((Date.now() - createdAt) / 1000));
  if (s < 60) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} minute${m === 1 ? "" : "s"} ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} hour${h === 1 ? "" : "s"} ago`;
  const d = Math.round(h / 24);
  return `${d} day${d === 1 ? "" : "s"} ago`;
}

function formatDuration(ms: number): string {
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/**
 * One spoken post: its measured waveform (recorded from the mic while it was
 * made, not a decoration), the play control, and the actions.
 *
 * Naming follows the app's conventions: the play control's label names the
 * action ("Play voice post"), the like button keeps a stable name with the
 * count and `aria-pressed`. Nothing here shares a name with the feed's
 * controls.
 *
 * Delete asks first, in words, because there is no undo for audio.
 */
export function VoiceCard({ post, onToggleLike, onDelete, onOpenReplies }: VoiceCardProps) {
  const { user } = useAuth();
  const { activeId, isPlaying, toggle } = useVoicePlayback();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const isMine = user?.id === post.author.id;
  const isThisPlaying = activeId === post.id && isPlaying;
  const playLabel = isThisPlaying ? "Pause voice post" : "Play voice post";
  const likeLabel = `Like voice post, ${post.likeCount} ${post.likeCount === 1 ? "like" : "likes"}`;

  const handleDelete = async () => {
    setDeleting(true);
    setDeleteError(null);
    try {
      await onDelete(post.id);
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : "Could not delete that post.");
    } finally {
      setDeleting(false);
    }
  };

  return (
    <article className="rounded-2xl border border-line bg-panel p-4">
      <div className="flex items-center justify-between gap-3 mb-3">
        <span className="flex items-center gap-2 text-xs">
          <span
            aria-hidden="true"
            className="w-7 h-7 rounded-full bg-canvas border border-line-strong flex items-center justify-center font-bold text-[11px] text-link"
          >
            {post.author.username[0]?.toUpperCase()}
          </span>
          <span className="font-bold text-fg">{post.author.username}</span>
          <span className="text-fg-subtle">· {timeAgo(post.createdAt)}</span>
        </span>
        <span className="text-[11px] tabular-nums text-fg-subtle">
          {formatDuration(post.durationMs)}
        </span>
      </div>

      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => toggle(post)}
          aria-label={playLabel}
          className="w-11 h-11 shrink-0 rounded-full bg-accent text-accent-fg flex items-center justify-center hover:scale-105 active:scale-95 transition-transform"
        >
          {isThisPlaying ? (
            <Pause className="w-5 h-5 fill-black" aria-hidden="true" />
          ) : (
            <Play className="w-5 h-5 fill-black ml-0.5" aria-hidden="true" />
          )}
        </button>

        {/* The recorded waveform: PEAK_COUNT bars measured from the
            microphone while this post was made. Static, decorative, honest. */}
        <div aria-hidden="true" className="flex-1 flex items-center gap-[2px] h-10 overflow-hidden">
          {post.peaks.map((peak, i) => (
            <div
              key={i}
              className={`flex-1 rounded-full ${activeId === post.id ? "bg-link" : "bg-white/25"}`}
              style={{ height: `${Math.max(8, Math.round(peak * 100))}%` }}
            />
          ))}
        </div>
      </div>

      <div className="flex items-center justify-between mt-3 pt-3 border-t border-line">
        <button
          type="button"
          onClick={() => void onToggleLike(post.id)}
          aria-pressed={post.likedByMe}
          aria-label={likeLabel}
          title="Like voice post"
          className="tap-target flex items-center gap-1.5 px-2 rounded-lg text-[11px] font-bold text-fg-muted hover:text-link transition-colors"
        >
          <Heart
            className={`w-4 h-4 ${post.likedByMe ? "fill-link text-link" : ""}`}
            aria-hidden="true"
          />
          <span aria-hidden="true" className="tabular-nums">{post.likeCount}</span>
        </button>

        {onOpenReplies && (
          <button
            type="button"
            onClick={() => onOpenReplies(post)}
            className="tap-target flex items-center gap-1.5 px-2 rounded-lg text-[11px] font-bold text-fg-muted hover:text-link transition-colors"
          >
            <MessageSquare className="w-4 h-4" aria-hidden="true" />
            {post.replyCount} {post.replyCount === 1 ? "reply" : "replies"}
          </button>
        )}

        {isMine && (
          <div className="flex items-center gap-2">
            {confirmDelete ? (
              <>
                <button
                  type="button"
                  onClick={() => void handleDelete()}
                  disabled={deleting}
                  className="px-2.5 py-1 rounded bg-danger-solid text-danger-fg text-[11px] font-bold hover:bg-danger-solid-hover transition-colors disabled:opacity-60"
                >
                  {deleting ? "Deleting…" : "Confirm delete"}
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmDelete(false)}
                  className="px-2.5 py-1 rounded border border-line text-fg-muted text-[11px] font-bold hover:text-fg transition-colors"
                >
                  Keep it
                </button>
              </>
            ) : (
              <button
                type="button"
                onClick={() => setConfirmDelete(true)}
                aria-label="Delete voice post"
                className="tap-target flex items-center gap-1.5 px-2 rounded-lg text-[11px] font-bold text-fg-muted hover:text-danger transition-colors"
              >
                <Trash2 className="w-4 h-4" aria-hidden="true" />
                Delete
              </button>
            )}
          </div>
        )}
      </div>

      {deleteError && (
        <p role="alert" className="mt-2 text-[11px] font-bold text-danger">
          {deleteError}
        </p>
      )}
    </article>
  );
}
