import React, { useEffect, useState } from "react";
import { authAPI, legalAPI } from "../api/client";
import { describeApiError } from "../api/messages";
import { Field, inputClass, primaryButton } from "../components/common/Dialog";
import { PublicShell } from "../components/common/PublicShell";
import { ComplianceInfo, ContactPerson, GrievanceReceipt } from "../types/echoflow";


export const SUPPORT_PATH = "/support";

const SUBJECT_MAX = 200; // GrievanceCreateSerializer: varchar(200)
const TEXT_MAX = 10_000; // FREE_TEXT_MAX_LENGTH
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const UUID_RE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

/** A takedown names a clip by UUID. People paste a share link, so accept either. */
export function extractClipId(input: string): string | null {
  const m = UUID_RE.exec(input.trim());
  return m ? m[0].toLowerCase() : null;
}

const Officer: React.FC<{ role: string; person?: ContactPerson }> = ({ role, person }) => {
  const published = person && person.email.trim() !== "";
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-line py-3 last:border-b-0">
      <dt className="text-sm text-fg-muted">{role}</dt>
      <dd className="text-right text-sm text-fg">
        {published ? (
          <>
            {person.name && person.name !== "Not configured" ? `${person.name} · ` : ""}
            <a href={`mailto:${person.email}`} className="text-link hover:underline">
              {person.email}
            </a>
          </>
        ) : (
          <span className="text-fg-subtle">Not yet published</span>
        )}
      </dd>
    </div>
  );
};

const GrievanceForm: React.FC = () => {
  const [subject, setSubject] = useState("");
  const [description, setDescription] = useState("");
  const [email, setEmail] = useState("");
  const [state, setState] = useState<"idle" | "sending">("idle");
  const [error, setError] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<GrievanceReceipt | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (state === "sending") return;
    if (!subject.trim()) return setError("Add a subject.");
    if (!description.trim()) return setError("Describe the problem.");
    if (email.trim() && !EMAIL_RE.test(email.trim())) return setError("That email address doesn't look right.");
    setError(null);
    setState("sending");
    try {
      const r = await legalAPI.submitGrievance({
        subject: subject.trim(),
        description: description.trim(),
        ...(email.trim() ? { user_email: email.trim() } : {}),
      });
      setReceipt(r);
    } catch (err) {
      setError(describeApiError(err, "We couldn't submit your grievance. Try again."));
    } finally {
      setState("idle");
    }
  };

  if (receipt) {
    return (
      <div role="status" className="space-y-1 rounded-md border border-line-strong bg-panel p-4 text-sm">
        <p className="font-semibold text-fg">Grievance received</p>
        <p className="text-fg-muted">{receipt.message}</p>
        <p className="tabular-nums text-fg-subtle">Reference: {receipt.grievance_id}</p>
      </div>
    );
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-4">
      <Field label="Subject">
        {(id) => <input id={id} value={subject} maxLength={SUBJECT_MAX} onChange={(e) => setSubject(e.target.value)} className={inputClass} />}
      </Field>
      <Field label="What happened?" hint={`${description.length}/${TEXT_MAX}`}>
        {(id, d) => (
          <textarea id={id} aria-describedby={d} value={description} maxLength={TEXT_MAX} rows={5} onChange={(e) => setDescription(e.target.value)} className={inputClass} />
        )}
      </Field>
      <Field label="Your email (optional)" hint="Where we should send the acknowledgment. If you're signed in and leave this blank, we use your account email.">
        {(id, d) => <input id={id} aria-describedby={d} type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} className={inputClass} />}
      </Field>
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
      <button type="submit" className={primaryButton} disabled={state === "sending"}>
        {state === "sending" ? "Sending…" : "Submit grievance"}
      </button>
    </form>
  );
};

const TakedownForm: React.FC = () => {
  const [clip, setClip] = useState("");
  const [reason, setReason] = useState("");
  const [email, setEmail] = useState("");
  const [state, setState] = useState<"idle" | "sending">("idle");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (state === "sending") return;
    const clipId = extractClipId(clip);
    if (!clipId) return setError("Paste the clip's link or its ID.");
    if (!reason.trim()) return setError("Explain why the clip should be removed.");
    if (email.trim() && !EMAIL_RE.test(email.trim())) return setError("That email address doesn't look right.");
    setError(null);
    setState("sending");
    try {
      await legalAPI.submitTakedown({ clip_id: clipId, reason: reason.trim(), ...(email.trim() ? { requester_email: email.trim() } : {}) });
      setDone(true);
    } catch (err: any) {
      setError(err?.status === 404 ? "We couldn't find that clip. Check the link and try again." : describeApiError(err, "We couldn't submit your request. Try again."));
    } finally {
      setState("idle");
    }
  };

  if (done) {
    return (
      <p role="status" className="rounded-md border border-line-strong bg-panel p-4 text-sm text-fg-muted">
        Takedown request received. We'll review it and contact you at the address provided, if any.
      </p>
    );
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-4">
      <Field label="Clip link or ID">
        {(id) => <input id={id} value={clip} onChange={(e) => setClip(e.target.value)} className={inputClass} />}
      </Field>
      <Field label="Reason for removal" hint={`${reason.length}/${TEXT_MAX}`}>
        {(id, d) => <textarea id={id} aria-describedby={d} value={reason} maxLength={TEXT_MAX} rows={5} onChange={(e) => setReason(e.target.value)} className={inputClass} />}
      </Field>
      <Field label="Your email" hint="Recommended, so we can follow up. Optional.">
        {(id, d) => <input id={id} aria-describedby={d} type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} className={inputClass} />}
      </Field>
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
      <button type="submit" className={primaryButton} disabled={state === "sending"}>
        {state === "sending" ? "Sending…" : "Send takedown request"}
      </button>
    </form>
  );
};

export const SupportPage: React.FC = () => {
  const [info, setInfo] = useState<ComplianceInfo | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    authAPI
      .getCompliance()
      .then((c) => live && setInfo(c))
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, []);

  return (
    <PublicShell contentId="support-content" skipLabel="Skip to support">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold text-fg">Support and grievances</h1>
        <p className="text-sm text-fg-muted">Report a problem with the service or with a clip, or ask for content to be removed.</p>
      </div>

      <section aria-labelledby="s-contacts" className="space-y-3">
        <h2 id="s-contacts" className="text-base font-semibold text-fg">
          Contacts
        </h2>
        {failed ? (
          <p role="status" className="text-sm text-fg-subtle">
            Contact details couldn't be loaded right now. The forms below still work.
          </p>
        ) : (
          <dl aria-busy={info === null}>
            <Officer role="Grievance officer" person={info?.grievance_officer} />
            <Officer role="Compliance officer" person={info?.compliance_officer} />
            <Officer role="Nodal contact" person={info?.nodal_contact} />
          </dl>
        )}
        {info?.physical_address && <p className="text-sm text-fg-muted">{info.physical_address}</p>}
      </section>

      <section aria-labelledby="s-grievance" className="space-y-4">
        <h2 id="s-grievance" className="text-base font-semibold text-fg">
          Report a problem
        </h2>
        <GrievanceForm />
      </section>

      <section aria-labelledby="s-takedown" className="space-y-4">
        <h2 id="s-takedown" className="text-base font-semibold text-fg">
          Request removal of a clip
        </h2>
        <p className="text-sm text-fg-muted">For rights holders and anyone affected by a clip. To flag a clip you are listening to, use the flag button on the clip itself.</p>
        <TakedownForm />
      </section>
    </PublicShell>
  );
};

export default SupportPage;
