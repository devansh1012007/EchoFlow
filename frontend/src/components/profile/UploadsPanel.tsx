import React, { useCallback, useEffect, useState } from "react";
import { clipsAPI } from "../../api/client";
import { describeApiError } from "../../api/messages";
import { OwnClip } from "../../types/echoflow";
import { secondaryButton } from "../common/Dialog";

/** `GET /clips/` has no filter, so finding unfinished uploads means paging. Stop at 5 pages (100 clips). */
export const MAX_UPLOAD_PAGES = 5;

const STATUS_LABEL: Record<string, string> = {
  processing: "Processing",
  failed: "Failed",
  rejected: "Rejected",
};

export const statusLabel = (status: string): string => STATUS_LABEL[status] ?? status.charAt(0).toUpperCase() + status.slice(1);

/**
 * Uploads that are not published yet.
 *
 * Why this exists: the profile's clip list returns only clips that are `ready`
 * and approved, even to their owner, so a clip still processing or failed is
 * otherwise invisible once the upload screen is closed. `GET /clips/` is the
 * owner's own list in every state. The serializer does not expose
 * `moderation_approved`, so a `ready` clip that is awaiting approval cannot be
 * told apart here and is not listed.
 */
export const UploadsPanel: React.FC = () => {
  const [unfinished, setUnfinished] = useState<OwnClip[] | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const found: OwnClip[] = [];
      let page = 1;
      let more = true;
      while (more && page <= MAX_UPLOAD_PAGES) {
        const res = await clipsAPI.listMyClips(page);
        found.push(...res.results.filter((c) => c.status !== "ready"));
        more = Boolean(res.next);
        page += 1;
      }
      setTruncated(more);
      setUnfinished(found);
    } catch (err) {
      setError(describeApiError(err, "We couldn't load your uploads."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section aria-labelledby="acct-uploads" className="space-y-3">
      <h2 id="acct-uploads" className="text-base font-semibold text-fg">
        Uploads in progress
      </h2>
      {error ? (
        <div role="status" className="space-y-2 text-sm">
          <p className="text-danger">{error}</p>
          <button type="button" className={secondaryButton} onClick={() => void load()}>
            Try again
          </button>
        </div>
      ) : unfinished === null ? (
        <p role="status" className="text-sm text-fg-muted">
          Checking your uploads…
        </p>
      ) : unfinished.length === 0 ? (
        <p className="text-sm text-fg-muted">Nothing is processing. Published clips appear above.</p>
      ) : (
        <ul>
          {unfinished.map((c) => (
            <li key={c.id} className="flex items-baseline justify-between gap-4 border-b border-line py-2 text-sm last:border-b-0">
              <span className="min-w-0 truncate text-fg">{c.title}</span>
              <span className={c.status === "processing" ? "text-fg-muted" : "text-danger"}>{statusLabel(c.status)}</span>
            </li>
          ))}
        </ul>
      )}
      {truncated && <p className="text-xs text-fg-subtle">Only your first {MAX_UPLOAD_PAGES * 20} uploads were checked.</p>}
      {unfinished !== null && !error && (
        <button type="button" className={secondaryButton} onClick={() => void load()} disabled={loading}>
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      )}
    </section>
  );
};
