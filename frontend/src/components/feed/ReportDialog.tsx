import React, { useState } from "react";
import { clipsAPI } from "../../api/client";
import { describeApiError } from "../../api/messages";
import { REPORT_REASONS, ReportReason } from "../../types/echoflow";
import { Dialog, Field, inputClass, primaryButton, secondaryButton } from "../common/Dialog";

interface Props {
  clipId: string;
  clipTitle: string;
  onClose: () => void;
}

export const REPORT_DETAILS_MAX = 1000;

/** `POST /clips/{id}/report/`. A reason and a non-empty note are both required by the backend. */
export const ReportDialog: React.FC<Props> = ({ clipId, clipTitle, onClose }) => {
  const [reason, setReason] = useState<ReportReason | "">("");
  const [details, setDetails] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent">("idle");
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (state !== "idle") return; // no double submission
    if (!reason) return setError("Choose a reason.");
    if (!details.trim()) return setError("Add a short note so a reviewer knows what to look for.");
    setError(null);
    setState("sending");
    try {
      await clipsAPI.reportClip(clipId, reason, details.trim());
      setState("sent");
    } catch (err) {
      setState("idle");
      setError(describeApiError(err, "We couldn't send your report. Try again."));
    }
  };

  if (state === "sent") {
    return (
      <Dialog title="Report received" onClose={onClose}>
        <p role="status" className="text-sm text-fg-muted">
          Thanks. A reviewer will look at “{clipTitle}”. You won't be told the outcome of individual reports.
        </p>
        <div className="mt-5 flex justify-end">
          <button type="button" className={primaryButton} onClick={onClose}>
            Done
          </button>
        </div>
      </Dialog>
    );
  }

  return (
    <Dialog title="Report this clip" onClose={onClose} busy={state === "sending"}>
      <form onSubmit={submit} className="space-y-4" noValidate>
        <p className="text-sm text-fg-muted">“{clipTitle}”</p>
        <Field label="Reason">
          {(id) => (
            <select id={id} value={reason} onChange={(e) => setReason(e.target.value as ReportReason | "")} className={inputClass} required>
              <option value="">Select a reason</option>
              {REPORT_REASONS.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label="What should we know?" hint={`${details.length}/${REPORT_DETAILS_MAX}`}>
          {(id, describedBy) => (
            <textarea
              id={id}
              aria-describedby={describedBy}
              value={details}
              maxLength={REPORT_DETAILS_MAX}
              onChange={(e) => setDetails(e.target.value)}
              rows={4}
              className={inputClass}
            />
          )}
        </Field>
        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <button type="button" className={secondaryButton} onClick={onClose} disabled={state === "sending"}>
            Cancel
          </button>
          <button type="submit" className={primaryButton} disabled={state === "sending"}>
            {state === "sending" ? "Sending…" : "Send report"}
          </button>
        </div>
      </form>
    </Dialog>
  );
};
