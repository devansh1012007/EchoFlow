export interface FeedClip {
  id: string;
  title: string;
  creator_name: string;
  creator_id: number;
  category: string;
  hls_playlist_url: string | null;
  likes: number;
  shares: number;
  skips: number;
  comment_count: number;
  is_liked: boolean;
  // Added in commit 21846fe. Before this existed, ReelCard initialised its
  // follow state to a hardcoded `false`, so a creator you already followed
  // rendered "Follow" and tapping it called the *toggle* endpoint — silently
  // unfollowing them. The backend field shipped with no consumer; this is the
  // consumer.
  is_following: boolean;
  // Present in FeedClipSerializer (backend/app/serializers.py fields list) but
  // previously missing here, so every read of them type-checked against an
  // incomplete type. `duration_ms` is milliseconds, not seconds.
  duration_ms: number;
  tags: string[];
  cover_image: string | null;
}

export interface User {
  id: number;
  username: string;
  email?: string;
}

export interface OwnProfile {
  id: number;
  username: string;
  email: string;
  profile_picture: string | null;
  followers_count: number;
  following_count: number;
  uploads_count: number;
  liked_clips: FeedClip[];
  date_joined: string;
}

export interface PublicProfile {
  id: number;
  username: string;
  profile_picture: string | null;
  followers_count: number;
  following_count: number;
  uploads_count: number;
  date_joined: string;
}

export interface Comment {
  id: string;
  clip: string;
  author_username: string;
  parent: string | null;
  text: string;
  reply_count: number;
  created_at: string;
}

export interface ShareEvent {
  id: number;
  sender_name: string;
  clip: FeedClip;
  clip_title: string;
  clip_hls_url: string;
  created_at: string;
  is_read: boolean;
}

export interface FeedResponse {
  next?: string;
  queue_health?: number;
  results: FeedClip[];
  message?: string;
  retry_after_ms?: number;
  degraded?: boolean;
}

export interface CursorPaginated<T> {
  next: string | null;
  previous: string | null;
  results: T[];
}

export interface AuthTokens {
  access: string;
  refresh: string;
}

/** Mirrors `Report.REPORT_REASONS` in the backend. The label is what the UI shows. */
export const REPORT_REASONS = [
  { value: "obscene", label: "Obscene or sexually explicit" },
  { value: "hate_speech", label: "Hate speech" },
  { value: "violence", label: "Promotes violence" },
  { value: "csam", label: "Child sexual abuse material" },
  { value: "terrorism", label: "Terrorism or extremism" },
  { value: "copyright", label: "Copyright infringement" },
  { value: "impersonation", label: "Impersonation" },
  { value: "privacy", label: "Invasion of privacy" },
  { value: "spam", label: "Spam or misleading" },
  { value: "other", label: "Other" },
] as const;
export type ReportReason = (typeof REPORT_REASONS)[number]["value"];

export interface ShareLink {
  clip_id: string;
  /** Absolute only when the server has `PUBLIC_APP_BASE_URL` configured. */
  url: string | null;
  path: string;
  token: string;
  expires_in: number;
}

export interface PublicClip {
  id: string;
  title: string;
  creator_name?: string;
  duration_ms?: number;
  category?: string;
  tags?: string[];
  cover_image?: string | null;
}

export interface GrievanceReceipt {
  grievance_id: number | string;
  status: string;
  acknowledgment_due?: string;
  message: string;
}

export interface DataAccessSummary {
  user_id: number;
  categories: {
    profile: {
      username: string;
      email: string;
      dob: string | null;
      is_minor: boolean;
      consent_accepted: boolean;
      terms_version: string | null;
      parent_email: string | null;
    };
    interactions_count: number;
    comments_count: number;
    shares_sent_count: number;
    audio_clips_created: number;
  };
}

export interface ErasureReceipt {
  request_id: number | string;
  status: string;
  cooling_off_until?: string;
  message?: string;
}

export interface SubscriptionStatus {
  app_user_id?: string;
  is_pro: boolean;
  expires_at: string | null;
  grace_until: string | null;
  last_synced: string | null;
  limits?: {
    /** A number as a string, or "unlimited" for Pro. */
    daily_uploads_remaining: string;
    max_clip_duration_seconds: number;
    max_upload_size_mb: number;
    hd_quality_allowed: boolean;
  };
}

export interface ContactPerson {
  name: string;
  email: string;
}

/** `GET /legal/compliance/`. Officers read "Not configured" with an empty email until the operator sets them. */
export interface ComplianceInfo {
  compliance_officer?: ContactPerson;
  grievance_officer?: ContactPerson;
  nodal_contact?: ContactPerson;
  terms_versions: string[];
  current_terms_version: string;
  privacy_version: string;
  physical_address: string;
}
