// EchoFlow HLS Token Worker — Token Validation Logic
//
// Validates tokens using the EXACT same algorithm as
// backend/app/services/hls_token.py (Django).
//
// Token format: base64url(payload_json).base64url(hmac_sha256(secret, payload_b64))
//
// Payload (JSON, sorted keys — matches json.dumps(sort_keys=True)):
//   { "c": string, "exp": number, "iat": number, "u": number, "v": 1 }
//   c   = clip key prefix (e.g. "hls/abc-123")
//   exp = expiry epoch (seconds)
//   iat = issued-at epoch (seconds)
//   u   = user_id
//   v   = token version (always 1)
//
// NOTE: Key order in the JSON must match Python's sort_keys=True output:
//   c, exp, iat, u, v  (alphabetical)
// The HMAC is computed over the base64url-encoded payload string,
// NOT over the raw JSON — matches Django's payload_b64.encode("ascii").

const TOKEN_VERSION = 1;
const COOKIE_NAME = "ef_hls_token";

// ---------------------------------------------------------------------------
// Base64url helpers (RFC 4648 §5, no padding — matches Python's rstrip("="))
// ---------------------------------------------------------------------------

function b64urlDecode(input: string): Uint8Array {
  // Add padding back (Python strips it with rstrip(b"="))
  const padded = input + "=".repeat((4 - (input.length % 4)) % 4);
  // Replace URL-safe chars with standard base64 chars
  const b64 = padded.replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

// ---------------------------------------------------------------------------
// HMAC-SHA256 verification using WebCrypto
// Matches: hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).digest()
// ---------------------------------------------------------------------------

async function verifyHmac(
  secret: string,
  payloadB64: string,
  receivedSigB64: string
): Promise<boolean> {
  const enc = new TextEncoder();

  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(secret),         // secret.encode("utf-8") in Python
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["verify"]
  );

  let receivedSig: Uint8Array;
  try {
    receivedSig = b64urlDecode(receivedSigB64);
  } catch {
    return false;
  }

  // WebCrypto verify is timing-safe — matches hmac.compare_digest()
  return crypto.subtle.verify(
    "HMAC",
    key,
    receivedSig,
    enc.encode(payloadB64)      // payload_b64.encode("ascii") in Python
  );
}

// ---------------------------------------------------------------------------
// Token payload type
// ---------------------------------------------------------------------------

interface TokenPayload {
  c: string;    // clip key prefix
  exp: number;  // expiry epoch (seconds)
  iat: number;  // issued-at epoch (seconds)
  u: number;    // user_id
  v: number;    // token version
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Validate a playback token.
 *
 * Matches Django's validate_playback_token() exactly:
 *   1. Split on "." — must have exactly 2 parts
 *   2. HMAC-SHA256 verify (timing-safe)
 *   3. Decode payload
 *   4. Version check (v === 1)
 *   5. Expiry check (now > exp → invalid)
 *   6. Clip-scope check (requestPath must start with "/<c>/")
 *
 * Returns the decoded payload if valid, null otherwise.
 */
export async function validatePlaybackToken(
  token: string,
  requestPath: string,
  secret: string
): Promise<TokenPayload | null> {
  if (!token || !token.includes(".")) return null;

  const parts = token.split(".");
  if (parts.length !== 2) return null;

  const [payloadB64, sigB64] = parts;

  // --- HMAC verification (timing-safe via WebCrypto) ---
  const valid = await verifyHmac(secret, payloadB64, sigB64);
  if (!valid) return null;

  // --- Payload decoding ---
  let payload: TokenPayload;
  try {
    const payloadBytes = b64urlDecode(payloadB64);
    const payloadJson = new TextDecoder().decode(payloadBytes);
    payload = JSON.parse(payloadJson) as TokenPayload;
  } catch {
    return null;
  }

  // --- Version check ---
  if (payload.v !== TOKEN_VERSION) return null;

  // --- Expiry check ---
  // matches: if int(time.time()) > payload["exp"]: return None
  const nowSeconds = Math.floor(Date.now() / 1000);
  if (nowSeconds > payload.exp) return null;

  // --- Clip-scope check ---
  // matches: expected_prefix = "/" + payload["c"] + "/"
  //          if not request_path.startswith(expected_prefix): return None
  const expectedPrefix = "/" + payload.c + "/";
  if (!requestPath.startsWith(expectedPrefix)) return null;

  return payload;
}

/**
 * Extract ef_hls_token value from a Cookie header string.
 * Matches Django's extract_token_from_cookie() exactly.
 */
export function extractTokenFromCookie(cookieHeader: string | null): string | null {
  if (!cookieHeader) return null;
  for (const pair of cookieHeader.split(";")) {
    const trimmed = pair.trim();
    if (trimmed.startsWith(COOKIE_NAME + "=")) {
      return trimmed.slice(COOKIE_NAME.length + 1);
    }
  }
  return null;
}