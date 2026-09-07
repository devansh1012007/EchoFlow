import React, { useEffect, useState, useRef } from "react";
import {
  User as UserIcon,
  Heart,
  Music,
  Edit3,
  Trash2,
  LogOut,
  Play,
  Pause,
  Camera,
  X,
} from "lucide-react";
import { clipsAPI, profileAPI } from "../api/client";
import { useAuth } from "../stores/auth";
import { usePlayer } from "../stores/player";
import { FeedClip, PublicProfile } from "../types/echoflow";

interface ProfilePageProps {
  targetUserId?: number | null;
  onBackToMyProfile?: () => void;
}

export const ProfilePage: React.FC<ProfilePageProps> = ({ targetUserId, onBackToMyProfile }) => {
  const { user, profile, logout, refreshProfile } = useAuth();
  const { currentClip, isPlaying, playClip, togglePlay } = usePlayer();

  const isOwnProfile = !targetUserId || targetUserId === user?.id;

  const [publicProfile, setPublicProfile] = useState<PublicProfile | null>(null);
  const [userClips, setUserClips] = useState<FeedClip[]>([]);
  const [activeTab, setActiveTab] = useState<"uploads" | "liked">("uploads");
  const [isLoading, setIsLoading] = useState<boolean>(true);

  // Edit Profile modal state
  const [isEditingProfile, setIsEditingProfile] = useState<boolean>(false);
  const [editUsername, setEditUsername] = useState<string>("");
  const [avatarFile, setAvatarFile] = useState<File | null>(null);
  const [isUpdatingProfile, setIsUpdatingProfile] = useState<boolean>(false);
  const [profileUpdateError, setProfileUpdateError] = useState<string | null>(null);

  // Edit Clip modal state
  const [editingClip, setEditingClip] = useState<FeedClip | null>(null);
  const [clipTitle, setClipTitle] = useState<string>("");
  const [clipCategory, setClipCategory] = useState<string>("");

  const avatarInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    loadProfileData();
  }, [targetUserId, isOwnProfile]);

  const loadProfileData = async () => {
    setIsLoading(true);
    try {
      if (isOwnProfile) {
        await refreshProfile();
        if (user?.id) {
          const clipsRes = await profileAPI.getUserClips(user.id);
          setUserClips(clipsRes.results);
        }
      } else if (targetUserId) {
        const [pub, clipsRes] = await Promise.all([
          profileAPI.getPublicProfile(targetUserId),
          profileAPI.getUserClips(targetUserId),
        ]);
        setPublicProfile(pub);
        setUserClips(clipsRes.results);
      }
    } catch (err) {
      console.warn("Error loading profile:", err);
    } finally {
      setIsLoading(false);
    }
  };

  const handleUpdateProfile = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsUpdatingProfile(true);
    setProfileUpdateError(null);

    const formData = new FormData();
    if (editUsername.trim()) {
      formData.append("username", editUsername.trim());
    }
    if (avatarFile) {
      formData.append("profile_picture", avatarFile);
    }

    try {
      await profileAPI.updateMyProfile(formData);
      await refreshProfile();
      setIsEditingProfile(false);
      setAvatarFile(null);
    } catch (err: any) {
      setProfileUpdateError(err?.message || "Failed to update profile");
    } finally {
      setIsUpdatingProfile(false);
    }
  };

  const handleSaveClipEdit = async () => {
    if (!editingClip) return;
    try {
      const updated = await clipsAPI.updateClip(editingClip.id, {
        title: clipTitle,
        category: clipCategory,
      });
      setUserClips((prev) => prev.map((c) => (c.id === updated.id ? updated : c)));
      setEditingClip(null);
    } catch (err) {
      console.warn("Failed to update clip:", err);
    }
  };

  const handleDeleteClip = async (clipId: string) => {
    if (!confirm("Delete this audio reel permanently?")) return;
    try {
      await clipsAPI.deleteClip(clipId);
      setUserClips((prev) => prev.filter((c) => c.id !== clipId));
      await refreshProfile();
    } catch (err) {
      console.warn("Failed to delete clip:", err);
    }
  };

  const currentDisplayProfile = isOwnProfile ? profile : publicProfile;

  return (
    <div className="w-full max-w-4xl mx-auto px-4 md:px-8 py-6 pb-28 space-y-6">
      {!isOwnProfile && onBackToMyProfile && (
        <button
          type="button"
          onClick={onBackToMyProfile}
          className="text-xs font-mono uppercase font-black text-[#FF6321] hover:underline flex items-center gap-1"
        >
          ← Back to primary account
        </button>
      )}

      {/* Profile Header Card */}
      <div className="p-6 md:p-8 rounded-2xl md:rounded-3xl bg-[#111111] border border-white/10 shadow-2xl space-y-6">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-5">
            <div className="relative">
              {currentDisplayProfile?.profile_picture ? (
                <img
                  src={currentDisplayProfile.profile_picture}
                  alt={currentDisplayProfile.username}
                  className="w-20 h-20 rounded-2xl object-cover border border-white/20 shadow-md"
                />
              ) : (
                <div className="w-20 h-20 rounded-2xl bg-white/10 border border-white/20 flex items-center justify-center text-[#FF6321] text-3xl font-black">
                  {currentDisplayProfile?.username?.[0]?.toUpperCase() || <UserIcon className="w-10 h-10" />}
                </div>
              )}
            </div>

            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-2xl md:text-3xl font-black uppercase tracking-tight text-white">
                  @{currentDisplayProfile?.username || "listener"}
                </h2>
                <span className="px-2 py-0.5 rounded bg-[#FF6321]/15 text-[#FF6321] text-[10px] font-mono font-bold uppercase border border-[#FF6321]/30">
                  CREATOR
                </span>
              </div>
              {isOwnProfile && profile?.email && (
                <p className="text-xs font-mono text-white/40 mt-1">{profile.email}</p>
              )}
              <p className="text-[10px] font-mono uppercase text-white/30 mt-1">
                Joined: {new Date(currentDisplayProfile?.date_joined || Date.now()).toLocaleDateString()}
              </p>
            </div>
          </div>

          {/* Action buttons */}
          {isOwnProfile && (
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => {
                  setEditUsername(profile?.username || "");
                  setIsEditingProfile(true);
                }}
                className="p-2.5 rounded-xl bg-white/5 hover:bg-white/10 border border-white/15 text-white transition-colors"
                title="Edit Profile"
              >
                <Edit3 className="w-4 h-4" />
              </button>
              <button
                type="button"
                onClick={logout}
                className="p-2.5 rounded-xl bg-white/5 hover:bg-rose-500/20 border border-white/15 text-white/50 hover:text-rose-400 transition-colors"
                title="Log Out"
              >
                <LogOut className="w-4 h-4" />
              </button>
            </div>
          )}
        </div>

        {/* Stats Row with Bold Typography & Monospace Metric */}
        <div className="grid grid-cols-3 gap-3 pt-4 border-t border-white/10 text-center">
          <div className="p-3 rounded-xl bg-black/50 border border-white/10">
            <span className="block text-2xl font-black font-mono text-white">
              {currentDisplayProfile?.followers_count || 0}
            </span>
            <span className="text-[10px] font-mono uppercase text-white/40 tracking-wider">Followers</span>
          </div>
          <div className="p-3 rounded-xl bg-black/50 border border-white/10">
            <span className="block text-2xl font-black font-mono text-white">
              {currentDisplayProfile?.following_count || 0}
            </span>
            <span className="text-[10px] font-mono uppercase text-white/40 tracking-wider">Following</span>
          </div>
          <div className="p-3 rounded-xl bg-black/50 border border-white/10">
            <span className="block text-2xl font-black font-mono text-[#FF6321]">
              {currentDisplayProfile?.uploads_count || 0}
            </span>
            <span className="text-[10px] font-mono uppercase text-white/40 tracking-wider">Audio Reels</span>
          </div>
        </div>
      </div>

      {/* Tabs for Own Profile */}
      {isOwnProfile && (
        <div className="flex items-center gap-2 p-1 rounded-xl bg-[#111111] border border-white/10">
          <button
            type="button"
            onClick={() => setActiveTab("uploads")}
            className={`flex-1 py-2.5 rounded-lg text-xs font-black uppercase tracking-wider transition-all flex items-center justify-center gap-2 ${
              activeTab === "uploads"
                ? "bg-[#FF6321] text-black shadow-[0_0_15px_rgba(255,99,33,0.3)]"
                : "text-white/40 hover:text-white"
            }`}
          >
            <Music className="w-3.5 h-3.5" />
            <span>My Uploads ({userClips.length})</span>
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("liked")}
            className={`flex-1 py-2.5 rounded-lg text-xs font-black uppercase tracking-wider transition-all flex items-center justify-center gap-2 ${
              activeTab === "liked"
                ? "bg-[#FF6321] text-black shadow-[0_0_15px_rgba(255,99,33,0.3)]"
                : "text-white/40 hover:text-white"
            }`}
          >
            <Heart className="w-3.5 h-3.5" />
            <span>Liked Reels ({profile?.liked_clips?.length || 0})</span>
          </button>
        </div>
      )}

      {/* Clips Display */}
      {isLoading ? (
        <div className="py-20 text-center font-mono text-xs uppercase text-white/40">Loading audio library...</div>
      ) : activeTab === "uploads" ? (
        userClips.length === 0 ? (
          <div className="p-12 rounded-3xl bg-[#111111] border border-white/10 text-center text-white/40 font-mono text-xs uppercase">
            No audio reels published to network.
          </div>
        ) : (
          <div className="space-y-3">
            {userClips.map((clip) => {
              const isThisPlaying = currentClip?.id === clip.id && isPlaying;
              return (
                <div
                  key={clip.id}
                  className="p-4 rounded-xl bg-[#111111] border border-white/10 flex items-center justify-between gap-4 group"
                >
                  <button
                    type="button"
                    onClick={() => {
                      if (currentClip?.id === clip.id) togglePlay();
                      else playClip(clip, userClips);
                    }}
                    className={`w-11 h-11 rounded-lg flex items-center justify-center flex-shrink-0 transition-transform ${
                      isThisPlaying
                        ? "bg-[#FF6321] text-black scale-105 shadow-[0_0_15px_rgba(255,99,33,0.35)]"
                        : "bg-white/10 text-white group-hover:bg-[#FF6321] group-hover:text-black"
                    }`}
                  >
                    {isThisPlaying ? <Pause className="w-4 h-4 fill-current" /> : <Play className="w-4 h-4 fill-current ml-0.5" />}
                  </button>

                  <div className="flex-1 min-w-0">
                    <span className="text-[9px] uppercase font-mono font-bold text-[#FF6321] tracking-wider">
                      {clip.category}
                    </span>
                    <h4 className="text-sm font-black uppercase text-white truncate">{clip.title}</h4>
                    <p className="text-[10px] font-mono text-white/40 mt-0.5">
                      ❤️ {clip.likes} • 💬 {clip.comment_count} • 🔄 {clip.shares}
                    </p>
                  </div>

                  {isOwnProfile && (
                    <div className="flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={() => {
                          setEditingClip(clip);
                          setClipTitle(clip.title);
                          setClipCategory(clip.category);
                        }}
                        className="p-2 rounded-lg text-white/40 hover:text-white hover:bg-white/10"
                        title="Edit Details"
                      >
                        <Edit3 className="w-3.5 h-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDeleteClip(clip.id)}
                        className="p-2 rounded-lg text-white/40 hover:text-rose-400 hover:bg-white/10"
                        title="Delete Reel"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )
      ) : (
        profile?.liked_clips?.length === 0 ? (
          <div className="p-12 rounded-3xl bg-[#111111] border border-white/10 text-center text-white/40 font-mono text-xs uppercase">
            No audio reels saved yet. Heart reels in the live feed to archive them here.
          </div>
        ) : (
          <div className="space-y-3">
            {profile?.liked_clips?.map((clip) => {
              const isThisPlaying = currentClip?.id === clip.id && isPlaying;
              return (
                <div
                  key={clip.id}
                  onClick={() => {
                    if (currentClip?.id === clip.id) togglePlay();
                    else playClip(clip, profile.liked_clips);
                  }}
                  className="p-4 rounded-xl bg-[#111111] border border-white/10 flex items-center gap-4 cursor-pointer hover:border-white/20 transition-colors"
                >
                  <div
                    className={`w-11 h-11 rounded-lg flex items-center justify-center flex-shrink-0 ${
                      isThisPlaying ? "bg-[#FF6321] text-black" : "bg-white/10 text-white"
                    }`}
                  >
                    {isThisPlaying ? <Pause className="w-4 h-4 fill-current" /> : <Play className="w-4 h-4 fill-current ml-0.5" />}
                  </div>
                  <div className="flex-1 min-w-0">
                    <span className="text-[9px] uppercase font-mono font-bold text-[#FF6321] tracking-wider">
                      {clip.category} • @{clip.creator_name}
                    </span>
                    <h4 className="text-sm font-black uppercase text-white truncate">{clip.title}</h4>
                  </div>
                  <Heart className="w-4 h-4 text-[#FF6321] fill-[#FF6321] flex-shrink-0" />
                </div>
              );
            })}
          </div>
        )
      )}

      {/* Edit Profile Modal */}
      {isEditingProfile && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm">
          <div className="w-full max-w-sm bg-[#111111] border border-white/15 rounded-2xl p-6 shadow-2xl space-y-4">
            <div className="flex items-center justify-between pb-2 border-b border-white/10">
              <h3 className="text-sm font-black uppercase text-white">Edit Profile Details</h3>
              <button
                type="button"
                onClick={() => setIsEditingProfile(false)}
                className="text-white/40 hover:text-white"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {profileUpdateError && (
              <p className="text-xs font-mono text-rose-400">{profileUpdateError}</p>
            )}

            <form onSubmit={handleUpdateProfile} className="space-y-4">
              <div>
                <label className="text-xs font-mono uppercase text-white/50 block mb-1">Username</label>
                <input
                  type="text"
                  value={editUsername}
                  onChange={(e) => setEditUsername(e.target.value)}
                  className="w-full bg-black border border-white/15 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-[#FF6321]"
                  required
                />
              </div>

              <div>
                <label className="text-xs font-mono uppercase text-white/50 block mb-1">Avatar Image</label>
                <input
                  ref={avatarInputRef}
                  type="file"
                  accept="image/*"
                  onChange={(e) => {
                    if (e.target.files?.[0]) setAvatarFile(e.target.files[0]);
                  }}
                  className="hidden"
                />
                <button
                  type="button"
                  onClick={() => avatarInputRef.current?.click()}
                  className="w-full py-2.5 px-3 rounded-lg bg-black border border-white/15 text-xs font-mono uppercase text-white/60 flex items-center justify-center gap-2 hover:border-white/30"
                >
                  <Camera className="w-4 h-4 text-[#FF6321]" />
                  <span>{avatarFile ? avatarFile.name : "Select Image Asset (Max 5MB)"}</span>
                </button>
              </div>

              <div className="flex justify-end gap-2 pt-2 border-t border-white/10">
                <button
                  type="button"
                  onClick={() => setIsEditingProfile(false)}
                  className="px-3 py-1.5 rounded text-xs font-mono uppercase text-white/50 hover:text-white"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={isUpdatingProfile}
                  className="px-4 py-1.5 rounded bg-[#FF6321] text-black text-xs font-black uppercase tracking-wider"
                >
                  {isUpdatingProfile ? "Saving..." : "Save Profile"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Edit Clip Modal */}
      {editingClip && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm">
          <div className="w-full max-w-sm bg-[#111111] border border-white/15 rounded-2xl p-6 shadow-2xl space-y-4">
            <div className="flex items-center justify-between pb-2 border-b border-white/10">
              <h3 className="text-sm font-black uppercase text-white">Edit Reel</h3>
              <button
                type="button"
                onClick={() => setEditingClip(null)}
                className="text-white/40 hover:text-white"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="space-y-3">
              <div>
                <label className="text-xs font-mono uppercase text-white/50 block mb-1">Title</label>
                <input
                  type="text"
                  value={clipTitle}
                  onChange={(e) => setClipTitle(e.target.value)}
                  className="w-full bg-black border border-white/15 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-[#FF6321]"
                />
              </div>

              <div>
                <label className="text-xs font-mono uppercase text-white/50 block mb-1">Category</label>
                <select
                  value={clipCategory}
                  onChange={(e) => setClipCategory(e.target.value)}
                  className="w-full bg-black border border-white/15 rounded-lg px-3 py-2 text-xs text-white uppercase focus:outline-none focus:border-[#FF6321]"
                >
                  <option value="comedy">comedy</option>
                  <option value="science">science</option>
                  <option value="motivation">motivation</option>
                  <option value="music">music</option>
                  <option value="quotes">quotes</option>
                  <option value="instrumental">instrumental</option>
                </select>
              </div>

              <div className="flex justify-end gap-2 pt-2 border-t border-white/10">
                <button
                  type="button"
                  onClick={() => setEditingClip(null)}
                  className="px-3 py-1.5 text-xs font-mono uppercase text-white/50 hover:text-white"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={handleSaveClipEdit}
                  className="px-4 py-1.5 rounded bg-[#FF6321] text-black text-xs font-black uppercase tracking-wider"
                >
                  Save Changes
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
