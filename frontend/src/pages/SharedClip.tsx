import React, { useCallback, useEffect, useRef, useState } from "react";
import Hls from "hls.js";
import { ApiError, clipsAPI } from "../api/client";
import { describeApiError } from "../api/messages";
import { PublicShell } from "../components/common/PublicShell";
import { primaryButton } from "../components/common/Dialog";
import { PublicClip } from "../types/echoflow";

interface Props {
  clipId: string;
  /** The `?s=` share token. Without it the clip can be described but not played. */
  shareToken: string | null;
}

function formatDuration(ms?: number): string | null {
  if (!ms || ms <= 0) return null;
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/**
 * `/clip/:id?s=…` — what a recipient of a public link sees, signed out.
 *
 * `GET /clips/{id}/public/` supplies the metadata. `POST /clips/{id}/play/`
 * trades the share token for a short-lived media cookie scoped to `/hls/`, which
 * is why every manifest and segment request goes out with credentials. Playback
 * starts on a tap: browsers block autoplay, and the exchange should not run for
 * someone who only opened the link.
 */
export const SharedClipPage: React.FC<Props> = ({ clipId, shareToken }) => {
  const [clip, setClip] = useState<PublicClip | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [phase, setPhase] = useState<"idle" | "starting" | "playing">("idle");
  const [playError, setPlayError] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const hlsRef = useRef<Hls | null>(null);

  useEffect(() => {
    let live = true;
    clipsAPI
      .getPublicClip(clipId)
      .then((c) => live && setClip(c))
      .catch((err) => {
        if (!live) return;
        setLoadError(err instanceof ApiError && (err.status === 404 || err.status === 403) ? "This clip isn't available." : describeApiError(err, "We couldn't load this clip."));
      });
    return () => {
      live = false;
      hlsRef.current?.destroy();
      hlsRef.current = null;
    };
  }, [clipId]);

  const start = useCallback(async () => {
    if (phase === "starting") return;
    const audio = audioRef.current;
    if (!shareToken || !audio) {
      setPlayError("This link is incomplete, so the clip can't be played. Ask the sender for the full link.");
      return;
    }
    setPlayError(null);
    setPhase("starting");
    try {
      const { hls_playlist_url: url } = await clipsAPI.playSharedClip(clipId, shareToken);
      hlsRef.current?.destroy();
      if (url.includes(".m3u8") && Hls.isSupported()) {
        const hls = new Hls({ xhrSetup: (xhr) => void (xhr.withCredentials = true) });
        hlsRef.current = hls;
        hls.loadSource(url);
        hls.attachMedia(audio);
        hls.on(Hls.Events.MANIFEST_PARSED, () => void audio.play()?.catch?.(() => {}));
        hls.on(Hls.Events.ERROR, (_e, data) => {
          if (!data.fatal) return;
          setPlayError("Playback stopped. The link may have expired.");
          setPhase("idle");
        });
      } else {
        audio.crossOrigin = "use-credentials";
        audio.src = url;
        void audio.play()?.catch?.(() => {});
      }
      setPhase("playing");
    } catch (err) {
      setPhase("idle");
      if (err instanceof ApiError && err.status === 403) setPlayError("This link has expired or isn't valid. Ask the sender for a new one.");
      else if (err instanceof ApiError && err.status === 409) setPlayError("This clip is still processing. Try again in a minute.");
      else setPlayError(describeApiError(err, "We couldn't start playback. Try again."));
    }
  }, [clipId, shareToken, phase]);

  const duration = formatDuration(clip?.duration_ms);

  return (
    <PublicShell contentId="clip-content" skipLabel="Skip to clip" width="narrow">
      {loadError ? (
        <div role="alert" className="space-y-3">
          <h1 className="text-xl font-semibold text-fg">{loadError}</h1>
          <a href="/" className="text-sm text-link hover:underline">
            Go to EchoFlow
          </a>
        </div>
      ) : !clip ? (
        <p role="status" className="text-sm text-fg-muted">
          Loading clip…
        </p>
      ) : (
        <div className="space-y-6">
          <div className="space-y-1">
            <h1 className="text-2xl font-semibold text-fg">{clip.title}</h1>
            <p className="text-sm text-fg-muted">
              {clip.creator_name ? `@${clip.creator_name}` : "EchoFlow"}
              {duration ? <span className="tabular-nums"> · {duration}</span> : null}
            </p>
          </div>

          {phase !== "playing" && (
            <button type="button" className={`${primaryButton} w-full`} onClick={() => void start()} disabled={phase === "starting"}>
              {phase === "starting" ? "Starting…" : "Play"}
            </button>
          )}
          {/* Always mounted so hls.js has an element to attach to; shown once playback begins. */}
          <audio ref={audioRef} controls className={phase === "playing" ? "w-full" : "hidden"} aria-label={`Player for ${clip.title}`} />

          {playError && (
            <p role="alert" className="text-sm text-danger">
              {playError}
            </p>
          )}
          <p className="text-sm text-fg-muted">
            Want more like this?{" "}
            <a href="/" className="text-link hover:underline">
              Open EchoFlow
            </a>
            .
          </p>
        </div>
      )}
    </PublicShell>
  );
};

export default SharedClipPage;
