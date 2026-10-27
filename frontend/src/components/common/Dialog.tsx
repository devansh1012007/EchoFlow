import React, { useEffect, useId, useRef } from "react";

interface DialogProps {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  /** `alertdialog` for destructive confirmations: it tells assistive tech the user must respond. */
  role?: "dialog" | "alertdialog";
  /** Block Escape and scrim-click dismissal while a request is in flight. */
  busy?: boolean;
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Modal dialog: focus moves in on open and returns to the opener on close,
 * Tab is trapped, Escape and a scrim click dismiss (unless `busy`).
 */
export const Dialog: React.FC<DialogProps> = ({ title, onClose, children, role = "dialog", busy = false }) => {
  const titleId = useId();
  const ref = useRef<HTMLDivElement>(null);
  const busyRef = useRef(busy);
  busyRef.current = busy;

  useEffect(() => {
    const opener = document.activeElement;
    ref.current?.focus();
    return () => {
      if (opener instanceof HTMLElement) opener.focus();
    };
  }, []);

  const onKeyDown = (e: React.KeyboardEvent) => {
    // A dialog rendered inside a clickable card must not leak keys or clicks to it.
    e.stopPropagation();
    if (e.key === "Escape") {
      if (!busyRef.current) onClose();
      return;
    }
    if (e.key !== "Tab" || !ref.current) return;
    const items = [...ref.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
    if (items.length === 0) {
      e.preventDefault();
      return;
    }
    const first = items[0] as HTMLElement;
    const last = items[items.length - 1] as HTMLElement;
    if (e.shiftKey && (document.activeElement === first || document.activeElement === ref.current)) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  return (
    <div
      className="fixed inset-0 z-[100] flex items-end justify-center bg-black/70 p-0 sm:items-center sm:p-4"
      onClick={(e) => e.stopPropagation()}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <div
        ref={ref}
        role={role}
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onKeyDown={onKeyDown}
        className="w-full max-w-md rounded-t-xl border border-line bg-panel p-5 shadow-float focus:outline-none sm:rounded-xl"
      >
        <h2 id={titleId} className="mb-4 text-base font-semibold text-fg">
          {title}
        </h2>
        {children}
      </div>
    </div>
  );
};

/** Shared control styling: the edge of an input uses `control`, which clears 3:1 (WCAG 1.4.11). */
export const inputClass =
  "w-full rounded-md border border-control bg-canvas px-3 py-2.5 text-sm text-fg placeholder:text-fg-subtle focus:border-link";

export const Field: React.FC<{ label: string; hint?: string; error?: string | null; children: (id: string, describedBy?: string) => React.ReactNode }> = ({
  label,
  hint,
  error,
  children,
}) => {
  const id = useId();
  const descId = `${id}-d`;
  const describedBy = hint || error ? descId : undefined;
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-sm text-fg-muted">
        {label}
      </label>
      {children(id, describedBy)}
      {(hint || error) && (
        <p id={descId} className={`text-xs ${error ? "text-danger" : "text-fg-subtle"}`} role={error ? "alert" : undefined}>
          {error ?? hint}
        </p>
      )}
    </div>
  );
};

export const primaryButton =
  "inline-flex min-h-11 items-center justify-center rounded-md bg-accent px-4 text-sm font-semibold text-accent-fg transition-colors hover:bg-accent-hover disabled:opacity-50 disabled:pointer-events-none";
export const secondaryButton =
  "inline-flex min-h-11 items-center justify-center rounded-md border border-control px-4 text-sm font-medium text-fg transition-colors hover:bg-white/10 disabled:opacity-50 disabled:pointer-events-none";
export const dangerButton =
  "inline-flex min-h-11 items-center justify-center rounded-md bg-danger-solid px-4 text-sm font-semibold text-danger-fg transition-colors hover:bg-danger-solid-hover disabled:opacity-50 disabled:pointer-events-none";
