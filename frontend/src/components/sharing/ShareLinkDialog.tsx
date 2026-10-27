import React, { useEffect, useRef, useState } from "react";
import { clipsAPI } from "../../api/client";
import { describeApiError } from "../../api/messages";
import { ShareLink } from "../../types/echoflow";
import { Dialog, inputClass, primaryButton, secondaryButton } from "../common/Dialog";

interface Props {
  clipId: string;
  clipTitle: string;
  onClose: () => void;
}

/** Turn the server's relative `/clip/{id}?s=…` into something pasteable when no public base URL is configured. */
export function absoluteShareUrl(link: ShareLink, origin: string): string {
  return link.url ?? `${origin}${link.path}`;
}

function describeExpiry(seconds: number): string {
  const days = Math.round(seconds / 86400);
  return days >= 1 ? `${days} day${days === 1 ? "" : "s"}` : "a short time";
}

/**
 * `POST /clips/{id}/share-link/` — creator-only. Anyone holding the link can listen
 * without an account until it expires, so the dialog says so before the person copies it.
 */
export const ShareLinkDialog: React.FC<Props> = ({ clipId, clipTitle, onClose }) => {
  const [link, setLink] = useState<ShareLink | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<"idle" | "copied" | "manual">("idle");
  const inputRef = useRef<HTMLInputElement>(null);
  const requested = useRef(false);

  useEffect(() => {
    if (requested.current) return; // StrictMode runs effects twice; mint one link, not two
    requested.current = true;
    clipsAPI
      .createShareLink(clipId)
      .then(setLink)
      .catch((err: any) => {
        setError(
          err?.status === 409
            ? "This clip is still processing. Try again once it's ready to play."
            : describeApiError(err, "We couldn't create a link. Try again."),
        );
      });
  }, [clipId]);

  const url = link ? absoluteShareUrl(link, window.location.origin) : "";

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied("copied");
    } catch {
      inputRef.current?.select();
      setCopied("manual");
    }
  };

  return (
    <Dialog title="Share a public link" onClose={onClose}>
      <p className="mb-4 text-sm text-fg-muted">“{clipTitle}”</p>
      {!link && !error && (
        <p role="status" className="text-sm text-fg-muted">
          Creating link…
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
      {link && (
        <div className="space-y-3">
          <p className="text-sm text-fg-muted">
            Anyone with this link can listen without signing in for {describeExpiry(link.expires_in)}.
          </p>
          <label htmlFor="share-link-url" className="sr-only">
            Public link
          </label>
          <input id="share-link-url" ref={inputRef} readOnly value={url} onFocus={(e) => e.currentTarget.select()} className={inputClass} />
          <p role="status" className="min-h-4 text-xs text-fg-subtle">
            {copied === "copied" ? "Link copied." : copied === "manual" ? "Press Ctrl+C or ⌘C to copy." : ""}
          </p>
        </div>
      )}
      <div className="mt-5 flex justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onClose}>
          Close
        </button>
        {link && (
          <button type="button" className={primaryButton} onClick={copy}>
            Copy link
          </button>
        )}
      </div>
    </Dialog>
  );
};
