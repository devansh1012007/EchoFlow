/**
 * The "sound wave on/off" setting, stored in a first-party cookie.
 *
 * Why a cookie (the brief asked for one): it survives sessionStorage being
 * cleared (the auth tokens live there) and is readable before React mounts.
 * It is a functional preference, set only when the person flips the switch.
 * The cookie is host-only on the app origin, so it is never sent to the API origin.
 */
export type VizPreference = "on" | "off";

export const VIZ_COOKIE = "ef_viz";
const ONE_YEAR_SECONDS = 60 * 60 * 24 * 365;

type CookieJar = { cookie: string };

export function readVizCookie(doc: CookieJar = document): VizPreference | null {
  for (const part of doc.cookie.split(";")) {
    const [rawName, ...rest] = part.trim().split("=");
    if (rawName === VIZ_COOKIE) {
      const value = decodeURIComponent(rest.join("="));
      return value === "on" || value === "off" ? value : null;
    }
  }
  return null;
}

export function writeVizCookie(
  value: VizPreference,
  doc: CookieJar = document,
  secure: boolean = typeof location !== "undefined" && location.protocol === "https:",
): void {
  doc.cookie =
    `${VIZ_COOKIE}=${value}; Max-Age=${ONE_YEAR_SECONDS}; Path=/; SameSite=Lax` + (secure ? "; Secure" : "");
}

/**
 * A stored choice always wins. With no choice yet, people who asked their
 * system for reduced motion start with the wave off; everyone else starts on.
 */
export function resolveVizPreference(stored: VizPreference | null, prefersReducedMotion: boolean): VizPreference {
  if (stored) return stored;
  return prefersReducedMotion ? "off" : "on";
}
