import React, { useCallback, useEffect, useState } from "react";
import { ApiError, dataSubjectAPI, subscriptionAPI } from "../../api/client";
import { describeApiError } from "../../api/messages";
import { DataAccessSummary, ErasureReceipt, SubscriptionStatus } from "../../types/echoflow";
import { Dialog, dangerButton, inputClass, primaryButton, secondaryButton } from "../common/Dialog";

export const ERASURE_CONFIRM_WORD = "DELETE";

const fmtDate = (iso: string | null | undefined): string => {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString(undefined, { dateStyle: "long" });
};

const Row: React.FC<{ label: string; value: React.ReactNode }> = ({ label, value }) => (
  <div className="flex items-baseline justify-between gap-4 border-b border-line py-2 text-sm last:border-b-0">
    <dt className="text-fg-muted">{label}</dt>
    <dd className="text-right tabular-nums text-fg">{value}</dd>
  </div>
);

/* --------------------------------- Plan ---------------------------------- */

const PlanSection: React.FC = () => {
  const [status, setStatus] = useState<SubscriptionStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"sync" | "manage" | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      setStatus(await subscriptionAPI.getStatus());
    } catch (err) {
      setLoadError(describeApiError(err, "We couldn't load your plan."));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const sync = async () => {
    if (busy) return;
    setBusy("sync");
    setNote(null);
    try {
      await subscriptionAPI.sync();
      await load();
      setNote("Plan status refreshed.");
    } catch (err) {
      setNote(err instanceof ApiError && err.status === 503 ? "Billing isn't set up on this server, so there is nothing to refresh." : describeApiError(err, "We couldn't refresh your plan."));
    } finally {
      setBusy(null);
    }
  };

  const manage = async () => {
    if (busy) return;
    setBusy("manage");
    setNote(null);
    try {
      const { url } = await subscriptionAPI.getManageUrl();
      if (url) window.open(url, "_blank", "noopener,noreferrer");
      else setNote("There's no billing portal for your account.");
    } catch (err) {
      setNote(describeApiError(err, "We couldn't open the billing portal."));
    } finally {
      setBusy(null);
    }
  };

  const limits = status?.limits;
  return (
    <section aria-labelledby="acct-plan" className="space-y-3">
      <h2 id="acct-plan" className="text-base font-semibold text-fg">
        Plan
      </h2>
      {loadError ? (
        // A polite status, not an alert: the plan is secondary to the page, and a
        // failed side panel must not claim the page's alert region from a form error.
        <div role="status" className="space-y-2 text-sm">
          <p className="text-danger">{loadError}</p>
          <button type="button" className={secondaryButton} onClick={() => void load()}>
            Try again
          </button>
        </div>
      ) : !status ? (
        <p role="status" className="text-sm text-fg-muted">
          Loading plan…
        </p>
      ) : (
        <>
          <dl>
            <Row label="Current plan" value={status.is_pro ? "Pro" : "Free"} />
            {status.is_pro && <Row label="Active until" value={fmtDate(status.expires_at)} />}
            {status.grace_until && <Row label="Grace period ends" value={fmtDate(status.grace_until)} />}
            <Row label="Last synced" value={fmtDate(status.last_synced)} />
            {limits && (
              <>
                <Row label="Uploads left today" value={limits.daily_uploads_remaining === "unlimited" ? "Unlimited" : limits.daily_uploads_remaining} />
                <Row label="Longest clip" value={`${limits.max_clip_duration_seconds} s`} />
                <Row label="Largest upload" value={`${limits.max_upload_size_mb} MB`} />
                <Row label="HD quality" value={limits.hd_quality_allowed ? "Included" : "Not included"} />
              </>
            )}
          </dl>
          <div className="flex flex-wrap gap-2">
            <button type="button" className={secondaryButton} onClick={sync} disabled={busy !== null}>
              {busy === "sync" ? "Refreshing…" : "Refresh status"}
            </button>
            <button type="button" className={secondaryButton} onClick={manage} disabled={busy !== null}>
              {busy === "manage" ? "Opening…" : "Manage subscription"}
            </button>
          </div>
        </>
      )}
      <p role="status" className="min-h-4 text-xs text-fg-subtle">
        {note ?? ""}
      </p>
    </section>
  );
};

/* ------------------------------- Your data -------------------------------- */

const ErasureDialog: React.FC<{ onClose: () => void; onDeleted: () => void }> = ({ onClose, onDeleted }) => {
  const [typed, setTyped] = useState("");
  const [state, setState] = useState<"idle" | "sending">("idle");
  const [error, setError] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<ErasureReceipt | null>(null);
  const [coolingOff, setCoolingOff] = useState<string | null>(null);

  const armed = typed.trim() === ERASURE_CONFIRM_WORD;

  const confirm = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!armed || state === "sending") return;
    setError(null);
    setState("sending");
    try {
      setReceipt(await dataSubjectAPI.requestErasure());
    } catch (err) {
      // 403 during cooling-off is the server describing state, not a failure.
      if (err instanceof ApiError && err.status === 403 && err.data && typeof err.data === "object" && "cooling_off_until" in err.data) {
        setCoolingOff(String((err.data as { cooling_off_until: string }).cooling_off_until));
      } else {
        setError(describeApiError(err, "We couldn't submit your request. Nothing was deleted."));
      }
    } finally {
      setState("idle");
    }
  };

  if (coolingOff) {
    return (
      <Dialog title="Deletion already requested" onClose={onClose}>
        <p role="status" className="text-sm text-fg-muted">
          You've already asked us to delete this account. The cooling-off period ends on {fmtDate(coolingOff)}. Nothing has been deleted yet.
        </p>
        <div className="mt-5 flex justify-end">
          <button type="button" className={primaryButton} onClick={onClose}>
            Close
          </button>
        </div>
      </Dialog>
    );
  }

  if (receipt) {
    const started = receipt.status === "in_progress";
    return (
      <Dialog title={started ? "Deletion started" : "Deletion requested"} onClose={started ? onDeleted : onClose}>
        <div role="status" className="space-y-3 text-sm text-fg-muted">
          <p>{receipt.message}</p>
          {!started && (
            <p>
              Nothing has been deleted yet. Your account stays active until {fmtDate(receipt.cooling_off_until)}. To change your mind, contact us through the{" "}
              <a href="/support" className="text-link hover:underline">
                support page
              </a>{" "}
              before then.
            </p>
          )}
        </div>
        <div className="mt-5 flex justify-end">
          <button type="button" className={primaryButton} onClick={started ? onDeleted : onClose}>
            {started ? "Sign out" : "Done"}
          </button>
        </div>
      </Dialog>
    );
  }

  return (
    <Dialog title="Delete your account" onClose={onClose} role="alertdialog" busy={state === "sending"}>
      <form onSubmit={confirm} className="space-y-4" noValidate>
        <div className="space-y-2 text-sm text-fg-muted">
          <p>This permanently removes your account, clips, comments, shares and uploaded media.</p>
          <p>
            Your first confirmation starts a <strong className="text-fg">30-day cooling-off period</strong>. After that period ends, confirming again here deletes your account{" "}
            <strong className="text-fg">immediately and permanently</strong>. Consent, audit and grievance records are kept with your identity removed, as the law requires.
          </p>
        </div>
        <div className="space-y-1.5">
          <label htmlFor="erasure-confirm" className="block text-sm text-fg-muted">
            Type {ERASURE_CONFIRM_WORD} to confirm
          </label>
          <input
            id="erasure-confirm"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            autoComplete="off"
            autoCapitalize="characters"
            spellCheck={false}
            className={inputClass}
          />
        </div>
        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <button type="button" className={secondaryButton} onClick={onClose} disabled={state === "sending"}>
            Cancel
          </button>
          <button type="submit" className={dangerButton} disabled={!armed || state === "sending"}>
            {state === "sending" ? "Submitting…" : "Delete my account"}
          </button>
        </div>
      </form>
    </Dialog>
  );
};

const DataSection: React.FC<{ onDeleted: () => void }> = ({ onDeleted }) => {
  const [summary, setSummary] = useState<DataAccessSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [erasing, setErasing] = useState(false);

  const view = async () => {
    if (loading) return;
    setLoading(true);
    setError(null);
    try {
      setSummary(await dataSubjectAPI.getAccessSummary());
    } catch (err) {
      setError(describeApiError(err, "We couldn't load your data summary."));
    } finally {
      setLoading(false);
    }
  };

  const p = summary?.categories.profile;
  return (
    <section aria-labelledby="acct-data" className="space-y-3">
      <h2 id="acct-data" className="text-base font-semibold text-fg">
        Your data
      </h2>
      <p className="text-sm text-fg-muted">See what we hold about you, or ask us to delete your account.</p>
      {summary && p && (
        <dl>
          <Row label="Username" value={p.username} />
          <Row label="Email" value={p.email || "—"} />
          <Row label="Date of birth" value={p.dob ? fmtDate(p.dob) : "—"} />
          <Row label="Under 18" value={p.is_minor ? "Yes" : "No"} />
          {p.parent_email && <Row label="Parent or guardian email" value={p.parent_email} />}
          <Row label="Terms accepted" value={p.consent_accepted ? `Yes${p.terms_version ? ` (${p.terms_version})` : ""}` : "No"} />
          <Row label="Clips you created" value={summary.categories.audio_clips_created} />
          <Row label="Comments" value={summary.categories.comments_count} />
          <Row label="Shares sent" value={summary.categories.shares_sent_count} />
          <Row label="Listening interactions" value={summary.categories.interactions_count} />
        </dl>
      )}
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <button type="button" className={secondaryButton} onClick={view} disabled={loading}>
          {loading ? "Loading…" : summary ? "Refresh my data" : "View my data"}
        </button>
        <button type="button" className="inline-flex min-h-11 items-center rounded-md border border-danger/50 px-4 text-sm font-medium text-danger transition-colors hover:bg-danger/10" onClick={() => setErasing(true)}>
          Delete account…
        </button>
      </div>
      {erasing && <ErasureDialog onClose={() => setErasing(false)} onDeleted={onDeleted} />}
    </section>
  );
};

/** Signed-in account tools: plan, data access and deletion, and the legal pages. */
export const AccountPanel: React.FC<{ onSignedOut: () => void }> = ({ onSignedOut }) => (
  <div className="space-y-8 border-t border-line pt-8">
    <PlanSection />
    <DataSection onDeleted={onSignedOut} />
    <nav aria-label="Legal" className="flex gap-5 text-xs">
      <a href="/terms" className="text-link hover:underline">
        Terms
      </a>
      <a href="/support" className="text-link hover:underline">
        Support and grievances
      </a>
    </nav>
  </div>
);

