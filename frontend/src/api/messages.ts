import { ApiError } from "./client";

/**
 * One place that turns a failed request into a sentence a person can act on.
 *
 * `ApiError.kind` separates "you are offline" from "the server said no", so a
 * dead connection is never reported as a validation problem (see the note on
 * `ApiError`). A server-supplied `detail` or `error` string is shown when there
 * is one, because for 400/403/404 it is the only text that names the cause.
 */
export function serverDetail(data: unknown): string | null {
  if (!data || typeof data !== "object") return null;
  const d = data as Record<string, unknown>;
  for (const key of ["detail", "error", "message"]) {
    const v = d[key];
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  // DRF field errors: { field: ["message", ...] }
  for (const v of Object.values(d)) {
    if (Array.isArray(v) && typeof v[0] === "string") return v[0];
  }
  return null;
}

export function describeApiError(err: unknown, fallback: string): string {
  if (!(err instanceof ApiError)) return fallback;
  if (err.kind === "network") return "Can't reach EchoFlow. Check your connection and try again.";
  if (err.kind === "timeout") return "The request took too long. Try again in a moment.";
  if (err.status === 429) {
    const wait = err.retryAfterSeconds;
    return wait && wait > 0
      ? `Too many attempts. Try again in ${wait < 90 ? `${wait} seconds` : `${Math.ceil(wait / 60)} minutes`}.`
      : "Too many attempts. Please wait a bit and try again.";
  }
  if (err.status === 401) return "Your session has expired. Sign in again.";
  if (err.status && err.status >= 500) return "Something went wrong on our side. Try again shortly.";
  return serverDetail(err.data) ?? fallback;
}
