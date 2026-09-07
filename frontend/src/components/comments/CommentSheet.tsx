import React, { useEffect, useState } from "react";
import { X, Send, CornerDownRight, MessageSquare, Trash2 } from "lucide-react";
import { commentsAPI } from "../../api/client";
import { Comment, FeedClip } from "../../types/echoflow";
import { useAuth } from "../../stores/auth";

interface CommentSheetProps {
  clip: FeedClip | null;
  isOpen: boolean;
  onClose: () => void;
}

export const CommentSheet: React.FC<CommentSheetProps> = ({ clip, isOpen, onClose }) => {
  const { user } = useAuth();
  const [comments, setComments] = useState<Comment[]>([]);
  const [newComment, setNewComment] = useState<string>("");
  const [replyToId, setReplyToId] = useState<string | null>(null);
  const [replyToUser, setReplyToUser] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [isLoading, setIsLoading] = useState<boolean>(false);

  useEffect(() => {
    if (isOpen && clip) {
      loadComments(clip.id);
    } else {
      setComments([]);
      setReplyToId(null);
      setReplyToUser(null);
    }
  }, [isOpen, clip]);

  const loadComments = async (clipId: string) => {
    setIsLoading(true);
    try {
      const data = await commentsAPI.getComments(clipId);
      setComments(data.results || []);
    } catch (err) {
      console.warn("Could not load comments:", err);
    } finally {
      setIsLoading(false);
    }
  };

  const handlePostComment = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!clip || !newComment.trim() || isSubmitting) return;

    setIsSubmitting(true);
    try {
      const res = await commentsAPI.postComment(clip.id, newComment.trim(), replyToId);
      setComments((prev) => [res, ...prev]);
      setNewComment("");
      setReplyToId(null);
      setReplyToUser(null);
    } catch (err) {
      console.warn("Failed to post comment:", err);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDeleteComment = async (commentId: string) => {
    try {
      await commentsAPI.deleteComment(commentId);
      setComments((prev) => prev.filter((c) => c.id !== commentId));
    } catch (err) {
      console.warn("Could not delete comment:", err);
    }
  };

  if (!isOpen || !clip) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-end md:items-center justify-center bg-black/80 backdrop-blur-sm transition-opacity">
      <div
        className="w-full max-w-lg max-h-[85vh] h-[600px] bg-[#111111] border-t md:border border-white/15 rounded-t-3xl md:rounded-3xl flex flex-col shadow-2xl overflow-hidden animate-in slide-in-from-bottom-5 duration-200"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-white/10">
          <div>
            <h3 className="text-sm font-black uppercase tracking-tight text-white flex items-center gap-2">
              <MessageSquare className="w-4 h-4 text-[#FF6321]" />
              Discussions ({comments.length})
            </h3>
            <p className="text-[10px] font-mono uppercase text-white/40 truncate max-w-xs">
              {clip.title}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-full hover:bg-white/10 text-white/40 hover:text-white transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Comment List */}
        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
          {isLoading ? (
            <div className="py-12 text-center text-white/40 font-mono text-xs uppercase">
              Loading thoughts...
            </div>
          ) : comments.length === 0 ? (
            <div className="py-16 text-center space-y-2">
              <p className="text-sm font-black uppercase text-white">No comments yet</p>
              <p className="text-xs font-mono uppercase text-white/40">
                Be the first to share your reaction on this audio reel.
              </p>
            </div>
          ) : (
            comments.map((comment) => {
              const isAuthor = user?.username === comment.author_username;
              return (
                <div key={comment.id} className="space-y-2">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex items-start gap-2.5">
                      <div className="w-7 h-7 rounded-full bg-white/10 border border-white/20 flex items-center justify-center font-black text-xs text-[#FF6321] flex-shrink-0 mt-0.5">
                        {comment.author_username[0]?.toUpperCase()}
                      </div>
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-black uppercase text-white">
                            @{comment.author_username}
                          </span>
                          <span className="text-[9px] font-mono text-white/30">
                            {new Date(comment.created_at).toLocaleTimeString([], {
                              hour: "2-digit",
                              minute: "2-digit",
                            })}
                          </span>
                        </div>
                        <p className="text-xs text-white/80 mt-0.5 leading-relaxed font-sans">
                          {comment.text}
                        </p>

                        <div className="flex items-center gap-3 mt-1">
                          <button
                            type="button"
                            onClick={() => {
                              setReplyToId(comment.id);
                              setReplyToUser(comment.author_username);
                            }}
                            className="text-[10px] font-mono uppercase text-[#FF6321] hover:underline"
                          >
                            Reply
                          </button>
                          {isAuthor && (
                            <button
                              type="button"
                              onClick={() => handleDeleteComment(comment.id)}
                              className="text-[10px] font-mono uppercase text-white/30 hover:text-rose-400"
                            >
                              Delete
                            </button>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* Input Footer */}
        <div className="p-4 bg-black/60 border-t border-white/10">
          {replyToId && (
            <div className="flex items-center justify-between text-[10px] font-mono text-[#FF6321] uppercase mb-2 px-1">
              <span>Replying to @{replyToUser}</span>
              <button
                type="button"
                onClick={() => {
                  setReplyToId(null);
                  setReplyToUser(null);
                }}
                className="hover:underline"
              >
                Cancel
              </button>
            </div>
          )}
          <form onSubmit={handlePostComment} className="flex items-center gap-2">
            <input
              type="text"
              value={newComment}
              onChange={(e) => setNewComment(e.target.value)}
              placeholder="Drop an audio comment or reaction..."
              className="flex-1 bg-[#111111] border border-white/15 rounded-xl px-4 py-2.5 text-xs text-white placeholder-white/30 focus:outline-none focus:border-[#FF6321]"
            />
            <button
              type="submit"
              disabled={!newComment.trim() || isSubmitting}
              className="p-2.5 rounded-xl bg-[#FF6321] text-black hover:bg-[#ff753b] disabled:opacity-40 transition-all"
            >
              <Send className="w-4 h-4" />
            </button>
          </form>
        </div>
      </div>
    </div>
  );
};
