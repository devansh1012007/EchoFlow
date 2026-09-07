// EchoFlow HLS Token Worker — fetch handler
//
// Validates the ef_hls_token cookie on every /hls/* request and proxies
// to R2 via binding if valid. Handles CORS and OPTIONS preflight internally
// since R2's bucket CORS policy is bypassed when using a Worker binding
// (the binding is a server-side call, not an HTTP request from the browser).
//
// Request flow:
//   Browser → Cloudflare Worker (media.echoflow.in)
//     → validatePlaybackToken() [token.ts]
//       → env.MEDIA_BUCKET.get() [R2 binding — free, no egress]
//         → stream response back to browser with CORS headers

import { validatePlaybackToken, extractTokenFromCookie } from "./token";

// ---------------------------------------------------------------------------
// Allowed origins — add localhost variants for local dev
// ---------------------------------------------------------------------------

const ALLOWED_ORIGINS = new Set([
  "https://app.echoflow.in",
  "https://echoflow.in",
  "https://www.echoflow.in",
  "http://localhost:5173",
  "http://localhost:3000",
]);

// ---------------------------------------------------------------------------
// CORS headers
//
// Access-Control-Allow-Credentials MUST be "true" and origin MUST be a
// specific value (not "*") because hls.js sends the cookie as a credential.
// Access-Control-Allow-Headers MUST include "Range" — HLS seeking uses
// byte-range requests; without it, seeking and ABR switching silently break.
// ---------------------------------------------------------------------------

function corsHeaders(origin: string): Record<string, string> {
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
    "Access-Control-Allow-Headers": "Range, If-Match, If-None-Match",
    "Access-Control-Expose-Headers":
      "Content-Length, Content-Range, Accept-Ranges, ETag, Content-Type",
    "Access-Control-Max-Age": "3600",
  };
}

function isAllowedOrigin(origin: string | null): boolean {
  return origin !== null && ALLOWED_ORIGINS.has(origin);
}

// ---------------------------------------------------------------------------
// Main handler
// ---------------------------------------------------------------------------

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    const origin = request.headers.get("Origin");
    const allowed = isAllowedOrigin(origin);

    // --- OPTIONS preflight ---
    // Must be handled explicitly — R2 CORS config is bypassed for Worker bindings.
    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: allowed && origin ? corsHeaders(origin) : {},
      });
    }

    // --- Only serve GET and HEAD ---
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method not allowed", { status: 405 });
    }

    // --- Only serve /hls/* paths ---
    // All other paths (healthcheck, root, etc.) get a 404.
    if (!url.pathname.startsWith("/hls/")) {
      return new Response("Not found", { status: 404 });
    }

    // --- Extract and validate token ---
    const cookieHeader = request.headers.get("Cookie");
    const token = extractTokenFromCookie(cookieHeader);

    if (!token) {
      return errorResponse(403, "Missing playback token", origin, allowed);
    }

    // url.pathname is the request path passed to scope check:
    //   "/hls/<clip_id>/master.m3u8" must start with "/hls/<clip_id>/"
    const payload = await validatePlaybackToken(
      token,
      url.pathname,
      env.MEDIA_TOKEN_SECRET
    );

    if (!payload) {
      return errorResponse(403, "Invalid or expired playback token", origin, allowed);
    }

    // --- Fetch object from R2 via binding ---
    // R2 binding supports Range headers natively — critical for HLS seeking.
    // env.MEDIA_BUCKET.get() is a server-side call, not an HTTP request,
    // so it has no egress cost and no CORS negotiation.
    //
    // The object key is the pathname without the leading "/":
    //   pathname "/hls/abc-123/master.m3u8" → key "hls/abc-123/master.m3u8"
    const objectKey = url.pathname.slice(1);

    const rangeHeader = request.headers.get("Range");
    const object = await env.MEDIA_BUCKET.get(objectKey, {
      range: rangeHeader ? request : undefined,
      onlyIf: request.headers,
    });

    if (object === null) {
      return errorResponse(404, "Not found", origin, allowed);
    }

    // --- Build response headers ---
    const responseHeaders = new Headers();

    // Copy R2 object metadata (Content-Type, ETag, Content-Length, etc.)
    object.writeHttpMetadata(responseHeaders);
    responseHeaders.set("ETag", object.httpEtag);

    // Cache HLS segments aggressively (they are immutable once uploaded).
    // Master and variant playlists are short-lived because they may update.
    const isSegment =
      objectKey.endsWith(".ts") ||
      objectKey.endsWith(".m4s") ||
      objectKey.endsWith(".aac");
    responseHeaders.set(
      "Cache-Control",
      isSegment ? "public, max-age=31536000, immutable" : "public, max-age=5"
    );

    // Add CORS headers to actual response
    if (allowed && origin) {
      for (const [k, v] of Object.entries(corsHeaders(origin))) {
        responseHeaders.set(k, v);
      }
    }

    // Determine status: 206 Partial Content for range requests, 200 otherwise
    const status = rangeHeader && object.range ? 206 : 200;

    return new Response(object.body, {
      status,
      headers: responseHeaders,
    });
  },
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function errorResponse(
  status: number,
  message: string,
  origin: string | null,
  allowed: boolean
): Response {
  const headers = new Headers({ "Content-Type": "text/plain" });
  if (allowed && origin) {
    for (const [k, v] of Object.entries(corsHeaders(origin))) {
      headers.set(k, v);
    }
  }
  return new Response(message, { status, headers });
}

// ---------------------------------------------------------------------------
// Env interface — must match wrangler.toml bindings exactly
// ---------------------------------------------------------------------------

interface Env {
  MEDIA_BUCKET: R2Bucket;
  MEDIA_TOKEN_SECRET: string;
  MEDIA_TOKEN_TTL_SECONDS: string;
}