# HTTPS / TLS Termination with nginx

**Files:**
- `docker/nginx.conf` (171 lines) — TLS terminator + reverse proxy
- `docker/certs/localhost.crt` + `localhost.key` — self-signed dev cert
- `docker-compose.yml` — new `nginx` service
- `backend/app/tests/test_https_termination.py` (707 lines, **31 tests**)

**Branch:** `feature/https-termination`

---

## 1. The Problem (Why)

Before this change, the EchoFlow stack spoke **only plain HTTP** to every
client and partner service:

```
Browser ──HTTP──> Gunicorn :8000   (JWTs, cookies, PII in cleartext)
Browser ──HTTP──> MinIO   :9000   (HLS segments — mixed-content blocked)
Curl, scrapers, the seed script ──HTTP──> API
```

The Django settings already had `SECURE_SSL_REDIRECT = True`, HSTS, and
`SECURE_PROXY_SSL_HEADER` configured — but with no TLS terminator in front
of gunicorn, those settings were **dead code**. Every request hit the
plaintext `web:8000` directly.

The concrete consequences:

| What was leaked | Who could see it | Risk |
|-----------------|------------------|------|
| JWT access + refresh tokens | anyone on the LAN | account takeover |
| Session + CSRF cookies | anyone on the LAN | session hijack |
| Audio PII (uploaded voice, transcripts) | anyone on the LAN | privacy |
| `?Range=bytes=...` headers | any Wi-Fi hop | content-fingerprinting |
| Browser CORS for HTTPS origin | always blocked | "works in dev, breaks in prod" |

The browser-side break was the most visible: a Vite dev server on
`http://localhost:3021` calling `http://localhost:8005` worked fine; the
same call from a Vercel-deployed `https://app.echoflow.example` to
`http://api.echoflow.example` was **silently blocked by mixed-content
rules**, with no console error explaining why.

---

## 2. The Fix (What)

Insert an **nginx** reverse proxy in front of `web` and `minio`. nginx
terminates TLS, applies security headers, and forwards plain HTTP to
the in-network backends. Nothing inside the application containers
needs to know TLS exists.

```
                                    ┌──────────────────────┐
Browser / API client  ──HTTPS──>    │   nginx 1.27-alpine  │
                                    │   :80  (→ 301)       │
                                    │   :443 (TLS)  ───────┼──HTTP──>  web:8000  (gunicorn)
                                    │   :9443 (TLS) ───────┼──HTTP──>  minio:9000
                                    └──────────────────────┘
```

Three listeners, three jobs:

| Listener | Purpose |
|----------|---------|
| `:80`   | **HTTP → HTTPS 301 redirect.** A user typing `echoflow.example` lands here, gets bounced to `https://...`, and never sees a plaintext response. |
| `:443`  | **TLS-terminated HTTPS to Django.** The `X-Forwarded-Proto: https` header is added on every upstream request so Django's `SECURE_PROXY_SSL_HEADER` sees `request.is_secure() == True`. |
| `:9443` | **TLS-terminated HTTPS to MinIO.** Exists so `hls.js` can fetch `.ts` segments from a secure origin and the browser doesn't block them as mixed content. |

The cert + key live in `docker/certs/` and are bind-mounted read-only
into the nginx container. They never enter the application image, which
means a cert renewal never requires rebuilding `web` or `celery`.

---

## 3. How It Works (Step by Step)

### 3.1 Request flow — login

```
Browser                  nginx                       Django (gunicorn)
   │                       │                                │
   │──POST /auth/login/──>│                                │
   │  (over TLS, port 443) │                                │
   │                       │──HTTP POST /auth/login/──────>│
   │                       │  X-Forwarded-Proto: https     │
   │                       │  X-Forwarded-For: <client>    │
   │                       │  X-Real-IP: <client>          │
   │                       │                                │
   │                       │<──201 Created + Set-Cookie────│
   │                       │   (Secure, SameSite=Lax)      │
   │<──201 Created────────│                                │
   │   Set-Cookie: ...Secure                              │
   │   Strict-Transport-Security: max-age=31536000;...     │
   │   X-Frame-Options: DENY                               │
```

Three things make this work end-to-end:

1. **nginx sets `X-Forwarded-Proto: https`** on every upstream request.
   Without that header, Django thinks the request was HTTP and either
   redirects (causing a loop) or refuses to set the `Secure` cookie flag.

2. **Django has `SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')`**
   in the `if not DEBUG:` block (`backend/EchoFlow/settings.py:539`).
   This is a security-sensitive setting: it tells Django "trust this
   header when it comes from a known upstream". If you ever put a
   different proxy in front of Django (e.g. an ALB that doesn't set
   that header), Django will silently think every request is HTTP
   and either loop or drop secure cookies.

3. **nginx DOES NOT terminate TLS at gunicorn.** gunicorn speaks plain
   HTTP on the docker-internal network. The TLS overhead is paid
   exactly once — at the edge.

### 3.2 Cert rotation (production)

```
# One-time per cert lifetime (~90 days for Let's Encrypt)
certbot renew --webroot -w /var/www/letsencrypt
cp /etc/letsencrypt/live/api.echoflow.example/fullchain.pem docker/certs/localhost.crt
cp /etc/letsencrypt/live/api.echoflow.example/privkey.pem    docker/certs/localhost.key
docker compose exec nginx nginx -s reload
```

No app-side rebuild. The bind-mount picks up the new files on the next
reload. Zero-downtime rollover because nginx keeps the old workers
serving existing connections while the new workers handle the new cert.

### 3.3 Why HSTS preload matters

`Strict-Transport-Security: max-age=31536000; includeSubDomains; preload`
tells the browser "for the next year, refuse to speak HTTP to this
host *or any of its subdomains*, even if the user types `http://`."

- `max-age=31536000` — 1 year, the minimum for the preload list.
- `includeSubDomains` — a typo elsewhere (`api.echoflow.example` on
  HTTP) won't accidentally be reachable.
- `preload` — declares the host eligible for the browser-shipped
  preload list. Submission to [hstspreload.org](https://hstspreload.org)
  is a separate, manual step.

The catch: once a browser has seen this header, you cannot roll it
back for that browser within the max-age. Don't enable preload until
you're certain the HTTPS endpoint is permanent.

### 3.4 Why a separate :9443 for MinIO

hls.js loads `.ts` segments from the same origin as `master.m3u8`. If
that origin is HTTP and the page is HTTPS, the browser blocks every
segment fetch with `Mixed Content: The site at https://... was loaded
over HTTPS, but requested an insecure video`. Two options:

| Option | Tradeoff |
|--------|----------|
| **Run MinIO with native TLS** | Doubles the cert/keystore management surface. MinIO needs a special config block. Renewal is per-service. |
| **Put nginx in front of MinIO too** ✅ | One cert, one rotation, one set of HSTS rules. Internal hops stay HTTP on the docker bridge. |

We chose the second. `media_urls.get_hls_playback_url()` produces
`https://localhost:9443/<bucket>/<key>` URLs, the browser fetches
them, nginx terminates, and the request goes plain HTTP to MinIO's
`:9000`.

---

## 4. Pros

### Security
- **End-to-end encryption** for tokens, cookies, headers, request
  bodies, response bodies. A Wi-Fi observer sees only ciphertext +
  SNI hostname + cert metadata.
- **HSTS preload eligibility** — once submitted, browsers will refuse
  HTTP for the host *before* the first request, eliminating the
  active-network-attacker case where a malicious AP rewrites the
  redirect.
- **Defense-in-depth on cookies** — `Secure` + `SameSite=Lax` together
  protect against both passive sniffing and CSRF over cross-site POSTs.
- **Cert is not in the app image** — even if the registry or the
  `web` image leaks, the cert doesn't.

### Operations
- **One cert, one rotation, one place to renew.** Compare to the
  alternative where gunicorn, MinIO, and any future service each
  carry their own cert.
- **nginx reload is non-disruptive** — old workers drain, new workers
  pick up the new cert. No "deploy window" needed for cert rotation.
- **`docker compose up --build` is unchanged** — no new env var
  contract, no new `EXPOSE` instruction, no app code change.
- **Live integration tests** in `TestLiveNginxTerminator` catch what
  static analysis can't (wrong port published, cert not mounted,
  HSTS header missing on the real response, redirect doesn't fire).

### Dev/prod parity
- **No "works in dev, breaks in prod" surprises.** A request that
  succeeds over `https://localhost` will succeed over
  `https://api.echoflow.example` because the wire shape is identical.
- **Mixed-content bugs surface in dev** instead of in the staging
  browser console.
- **Secure-cookie behavior matches prod.** A cookie set during a dev
  login will round-trip through the same browser code path as prod.

### Observability
- nginx's `access_log` format includes `rt`, `uct`, `uht`, `urt` —
  total request time, upstream connect time, upstream header time,
  upstream response time. gunicorn's access log gives only the
  request-to-Django time; nginx's gives the full client-perceived
  latency including TLS handshake.

---

## 5. Cons

### Operational complexity
- **One more service to operate.** 11 services instead of 10. The
  healthcheck, log volume, and resource budget all grow.
- **nginx config is now a critical artifact.** A typo in
  `docker/nginx.conf` takes the whole public surface down. Mitigated
  by the static-analysis tests in `TestNginxConfig` and the
  `nginx -t` test that runs when nginx is on PATH.
- **Cert rotation is a new operational ritual** that didn't exist
  before. Needs a renewal alarm, a runbook, and a way to test
  renewals in staging.

### Dev workflow
- **Self-signed certs require one-time trust** on the host machine
  (`update-ca-certificates` on Linux, Keychain on macOS). Without
  this, every browser tab shows a "Not Secure" warning, every
  `requests`/`curl` call needs `-k` or `--insecure`, and some
  libraries refuse to connect at all.
- **HSTS preload is sticky.** You cannot back it out for any browser
  that has seen the header within the last year. Test in staging
  with `max-age=300` first; only bump to `max-age=31536000` once
  you're confident the HTTPS endpoint is permanent.
- **Frontend `VITE_API_BASE_URL` change** is a breaking change for
  any local dev that was working before. The old `http://localhost:8005`
  still works (the `web` container still publishes that port for
  escape-hatch direct access) but is not the supported path.

### Performance
- **One extra hop in the request path.** TLS handshake + nginx parse +
  proxy pass. For a single small request this is dominated by RTT
  (single-digit ms on localhost), but at 10K concurrent connections
  you pay ~5-10% extra CPU on the nginx container for SSL termination.
  Resource limit set to `cpus: 0.5, memory: 128M` in `docker-compose.yml`
  as a starting point; raise in prod.
- **HTTP/2 is per-listener, not per-upstream.** Browsers can speak
  HTTP/2 to `:443` (the `http2 on;` directive enables this) and
  multiplex many requests over one connection. The `gunicorn` upstream
  still speaks HTTP/1.1, so the multiplexing benefit is
  half-realized. A future swap to uvicorn/uvloop on the upstream
  side would unlock the other half.

### Security (regressions to watch for)
- **`X-Forwarded-Proto` trust is a security boundary.** If you ever
  expose `web:8000` directly to the internet (e.g. for debugging),
  attackers can set `X-Forwarded-Proto: https` themselves and trick
  Django into thinking the request was secure. The `ALLOWED_HOSTS`
  check still catches that, but the cookie-secure check is now
  attacker-influenceable in that misconfig.
- **The cert is committed to the repo.** For dev this is intentional
  (a fresh clone should "just work"), but for production forks that
  re-publish this image, the private key is in the layer history.
  Mitigation: the production cert is meant to be bind-mounted
  *over* the file in `docker/certs/`, replacing it at deploy time.
  Verify with `docker exec nginx ls -la /etc/nginx/certs/` after
  any prod deploy.
- **HSTS is set on the API origin, not on the static frontend
  origin.** If the frontend (`vite dev` or the future static-site
  deploy) is on HTTP, the browser will downgrade the page load and
  then block the API call as mixed content. The fix is at the
  frontend hosting layer, not here.

---

## 6. Failure Modes

| Symptom | Likely cause | Where to look |
|---------|-------------|---------------|
| `502 Bad Gateway` on every request | nginx can't reach `web:8000` | `docker compose logs nginx` — usually `connection refused` on the upstream. Check `docker compose ps web` for healthcheck. |
| `SSL_CTX_use_PrivateKey_file` in nginx logs | cert + key don't match (e.g. cert renewed, key forgotten) | `openssl x509 -in cert.pem -noout -modulus \| openssl md5` and same for the key. They must match. |
| Redirect loop on `/health/` | `X-Forwarded-Proto` not being set by nginx, or `SECURE_PROXY_SSL_HEADER` missing from Django | `curl -kI https://localhost/health/` — should NOT 301. If it does, nginx config block missing the `proxy_set_header X-Forwarded-Proto https;` line. |
| Browsers still show "Not Secure" | self-signed cert not in the OS trust store | `sudo cp docker/certs/localhost.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates`. Browser restart required. |
| HLS playback fails on HTTPS page | `PUBLIC_MEDIA_ENDPOINT_URL` is still `http://` | Check `.env` and the `media_urls.get_hls_playback_url()` output. Must be `https://localhost:9443` in this setup. |
| Cert expired 30 days ago | cert was never rotated | `openssl x509 -in docker/certs/localhost.crt -noout -dates`. The dev cert is 365 days; replace with Let's Encrypt material in prod (renewal is automatic via `certbot`). |
| HSTS test fails after fresh boot | nginx hasn't read the new config yet (warm-up) | `docker compose logs nginx` — should show `reload` lines. If not, `docker compose exec nginx nginx -t`. |

---

## 7. Production Swap-Out

To move from self-signed dev to a real cert:

```bash
# 1. Get a cert (any ACME client works; certbot is the standard one)
sudo certbot certonly --standalone -d api.echoflow.example

# 2. Replace the bind-mounted files
sudo cp /etc/letsencrypt/live/api.echoflow.example/fullchain.pem \
       docker/certs/localhost.crt
sudo cp /etc/letsencrypt/live/api.echoflow.example/privkey.pem \
       docker/certs/localhost.key

# 3. Reload nginx (zero-downtime: old workers drain, new workers pick up)
docker compose exec nginx nginx -s reload

# 4. Verify
curl -I https://api.echoflow.example/health/
# expect: 200, no -k flag needed, Strict-Transport-Security present
```

No `docker compose build` needed. No app restart needed. The bind-mount
in `docker-compose.yml:550-552` (`./docker/certs:/etc/nginx/certs:ro`)
picks up the new files on the next reload.

For cert auto-renewal, run certbot as a system cron, and have the
`post-hook` do steps 2-3 above. The 90-day Let's Encrypt lifetime
gives ample headroom for any temporary renewal failure.

---

## 8. Test Coverage

`backend/app/tests/test_https_termination.py` — 32 tests across 6 classes:

| Class | What it verifies | # tests |
|-------|------------------|---------|
| `TestCertFiles` | The cert + key exist, parse, are matched, are not expired, and cover `localhost` + `127.0.0.1` in the SAN list. | 6 |
| `TestNginxConfig` | `:80` 301-redirects, `:443` + `:9443` use TLS, only TLS 1.2/1.3 enabled, HSTS 1y+includeSubDomains+preload, `X-Forwarded-Proto https` on every upstream block, cert paths match, `client_max_body_size >= 5M`, and (when nginx is on PATH) a real `nginx -t` parse. | 10 |
| `TestProductionSslSettings` | `settings.py`'s `if not DEBUG:` block contains every required knob with the right value: `SECURE_SSL_REDIRECT=True`, `SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https')`, `SESSION/CSRF_COOKIE_SECURE=True`, HSTS year-plus, `SameSite=Lax`, at module level. | 7 |
| `TestProxyHeaderBehavior` | In-process WSGI: `X-Forwarded-Proto: https` makes `request.is_secure() == True`; no redirect is issued when the header is present; in-container `HEALTHCHECK` directive sends the same header. | 3 |
| `TestPublicMediaEndpoint` | `PUBLIC_MEDIA_ENDPOINT_URL` in `.env.example` is `https://...` so browser HLS playback isn't mixed-content. | 1 |
| `TestLiveNginxTerminator` | Real running stack: `/health/` returns 200 over HTTPS, HSTS header is on the live response, all security headers present, HTTP→HTTPS redirect fires, MinIO on `:9443` is reachable. Skips cleanly when the stack is down. | 5 |

Run:

```bash
docker compose exec -u root -e PYTHONPATH=/app web \
  pytest backend/app/tests/test_https_termination.py -v
```

Last run: **32 passed in 0.37s**.

---

## 9. Related Documents

- `docs/EXPLAIN/docker/02-docker-compose.md` — service inventory
- `docs/EXPLAIN/docker/01-multi-stage-dockerfile.md` — image structure
- `docs/minio-s3-architecture.md` — why MinIO exists, why `hls/` is public
- `docs/stateful-media-storage-at-scale.md` — S3 vs MinIO vs local disk
- `backend/EchoFlow/settings.py:529-539` — the `if not DEBUG:` block
  that this whole setup depends on
- `backend/app/media_urls.py` — the URL generator whose output URL
  must be HTTPS or the browser will block segment fetches
