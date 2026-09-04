# Production Readiness Checklist — HTTPS / TLS Stack

This document tracks every step required to take the `feature/https-termination`
stack from a self-signed dev environment to a production deployment where
a misconfiguration would not be a security incident. It is intentionally
checklist-shaped so a release manager can tick each item.

**Baseline:** every item below assumes the dev stack is up and the
**32 tests in `backend/app/tests/test_https_termination.py` are green.**

---

## 1. Certs (Critical — Do First)

| # | Action | How to verify | Why |
|---|--------|---------------|-----|
| 1.1 | **Replace self-signed certs with a CA-issued cert.** Self-signed certs are committed to the repo and would be trusted by no one outside dev. | `openssl x509 -in docker/certs/localhost.crt -noout -issuer` must NOT show `CN = localhost` | A cert with `CN=localhost` and no chain is rejected by every browser, every ACME client, every corporate proxy, and most CDNs. |
| 1.2 | **Use a real public hostname** in the cert's SAN list. | `openssl x509 -in docker/certs/localhost.crt -noout -ext subjectAltName` — must include the prod hostname | Browsers ignore CN in favor of SAN; certs without the actual hostname are unusable. |
| 1.3 | **Set up automatic renewal** (Let's Encrypt or equivalent). 90-day Let's Encrypt lifetime; renewal must happen ≥30 days before expiry. | `certbot certificates` shows `VALID: 89 days` and a renewal timer | Cert expiry → entire API goes down → 100% outage for the duration of the renewal. The average 6-hour MTTR for a forgotten cert renewal is unforced downtime. |
| 1.4 | **Move the cert out of the repo for any fork that re-publishes the image.** The current `docker/certs/localhost.{crt,key}` is committed; that key would land in any image built from this Dockerfile. | `git log -- docker/certs/localhost.key` shows the commit, AND your prod deploy bind-mounts a different file at `/etc/nginx/certs/` | A private key in image-layer history is permanently compromised — the only fix is revocation. |
| 1.5 | **Enable OCSP stapling** in the nginx config (currently absent). | `openssl s_client -connect <host>:443 -status` shows `OCSP Response Status: successful` | Without stapling, every TLS handshake makes a fresh OCSP request to the CA. Latency + a private CA = a privacy leak. |

**Cert swap procedure (repeatable):**

```bash
# 1. Issue / renew via ACME
sudo certbot certonly --webroot -w /var/www/letsencrypt \
  -d api.echoflow.example

# 2. Atomically move the live files into the bind-mount path
sudo install -m 0644 /etc/letsencrypt/live/api.echoflow.example/fullchain.pem \
                 /srv/echoflow/certs/localhost.crt
sudo install -m 0600 /etc/letsencrypt/live/api.echoflow.example/privkey.pem \
                 /srv/echoflow/certs/localhost.key
# ^ read-only perms on the key are critical — nginx refuses to start
#   on a 0644 key.

# 3. Zero-downtime reload
docker compose exec nginx nginx -s reload

# 4. Verify on the live cert
echo | openssl s_client -connect api.echoflow.example:443 -servername api.echoflow.example 2>/dev/null \
  | openssl x509 -noout -dates
```

---

## 2. Hostnames & DNS (Critical)

| # | Action | Why |
|---|--------|-----|
| 2.1 | Pick the public hostname (e.g. `api.echoflow.example`). Don't reuse `localhost` in prod. | Browsers reject `localhost` certs for non-loopback connections. |
| 2.2 | Add the hostname to `DJANGO_ALLOWED_HOSTS` in `.env` / `.env.production`. | Without this Django returns 400 to every request, regardless of cert. |
| 2.3 | Add the corresponding `https://` origin to `DJANGO_CORS_ALLOWED_ORIGINS`. | Browsers block credentialed XHR from origins not in the allowlist. |
| 2.4 | If you use a CDN (Cloudflare, CloudFront, Fastly) in front of nginx, **put its hostname** in `ALLOWED_HOSTS` too — Django sees the CDN's `Host` header, not the origin's. | `ALLOWED_HOSTS` check uses `request.get_host()`, which respects `X-Forwarded-Host`. Misconfig = 400 on every request. |
| 2.5 | Frontend origin (wherever you host the React build) MUST be `https://`. HSTS only protects the API origin, not the static site. | Mixed-content blocks the API call before it leaves the browser. |

---

## 3. HSTS Hardening (Before Submitting to Preload List)

| # | Action | Why |
|---|--------|-----|
| 3.1 | Test with `max-age=300` (5 min) in staging for at least one full release cycle. | Once a browser sees HSTS, you cannot undo it within the max-age. Test before locking browsers in for a year. |
| 3.2 | Audit every subdomain and confirm it serves HTTPS correctly. The HSTS `includeSubDomains` flag means a typo elsewhere (`staging.echoflow.example` on plain HTTP) becomes unreachable. | Forgetting one subdomain = permanent outage for that subdomain on every browser that ever saw the header. |
| 3.3 | Audit every external service that points at your domain (status page, monitoring, OAuth callbacks). | Same as above. |
| 3.4 | Submit to [hstspreload.org](https://hstspreload.org) only when you are 100% sure. Removal from the list takes months. | The Chrome preload list is shipped with the browser. Removal requires a fresh Chrome install for affected users. |

**Current HSTS config:** `max-age=31536000; includeSubDomains; preload` (1 year, all subdomains, preload-eligible). This is correct for prod but irreversible for any browser that has seen it.

---

## 4. nginx Hardening

| # | Action | How |
|---|--------|-----|
| 4.1 | **Pin the nginx version** in `docker-compose.yml` (currently `nginx:1.27-alpine`). Alpine tracks mainline nginx, which is fine, but pin a minor version so a surprise security patch doesn't ship on your next `docker compose pull`. | Change to `nginx:1.27.5-alpine` or similar. |
| 4.2 | **Add OCSP stapling** directives to the :443 server block. | `ssl_stapling on; ssl_stapling_verify on; ssl_trusted_certificate /etc/nginx/certs/chain.pem;` |
| 4.3 | **Tighten the cipher list** for prod. The current list is "Mozilla intermediate" minus a few; for prod consider "Mozilla modern" (no TLS 1.2 ciphers, ECDHE-only). | Mozilla's [SSL config generator](https://ssl-config.mozilla.org) is the standard reference. |
| 4.4 | **Set explicit `worker_processes`** in nginx.conf (currently `auto`). On a 4-vCPU node this is fine, but explicit is auditable. | `worker_processes 4; worker_rlimit_nofile 65535;` |
| 4.5 | **Add rate limiting** at the nginx layer (defense-in-depth alongside DRF's `ScopedRateThrottle`). | `limit_req_zone $binary_remote_addr zone=api:10m rate=30r/s;` and apply per location. |
| 4.6 | **Add a `server_tokens off;` equivalent for the error page** (currently we set `server_tokens off` but the default 502 page can leak versions). | Test with `curl -I https://api.echoflow.example/nonexistent/` — the `Server` header should be just `nginx`. |
| 4.7 | **Log to stdout** (currently does) but ensure the log format includes the right correlation_id. | Already done — `proxy_set_header X-Request-ID` is forwarded. |

---

## 5. Django Application Hardening

| # | Action | Why |
|---|--------|-----|
| 5.1 | **`DJANGO_DEBUG=False`** in prod. The existing `if not DEBUG:` block activates every SSL setting; without it, every cookie is insecure and `/health/` 301-loops. | Already enforced by the .env template. Verify with `docker compose exec web python -c "from django.conf import settings; print(settings.DEBUG)"` — must print `False`. |
| 5.2 | **Rotate `DJANGO_SECRET_KEY`** for prod. The dev value is in `.env` and is committed to the repo. | A leaked `SECRET_KEY` allows forging session cookies, password-reset tokens, and CSRF tokens. |
| 5.3 | **Set `DJANGO_CORS_ALL=False`** with an explicit allowlist. The dev env has it `True` for convenience. | `CORS_ALLOW_ALL_ORIGINS=True` is a wildcard `Access-Control-Allow-Origin: *` + credentials, which every browser rejects for credentialed requests. |
| 5.4 | **Verify the prod allowlist** is minimal. `https://*.echoflow.example` is fine; bare `https://*` is not. | Browsers don't enforce that you can have one too-broad entry — they do enforce that your origin IS in the list. |
| 5.5 | **Drop the `8005:8000` host port mapping on the `web` service** in compose. Direct gunicorn access bypasses nginx and lets an attacker spoof `X-Forwarded-Proto: https` to themselves. | With the port published, an attacker on the same network can talk to gunicorn directly and impersonate an HTTPS request. |
| 5.6 | **Enable real CSRF** for any state-changing endpoint served over HTTPS. The `CSRF_COOKIE_SECURE=True` + `CSRF_COOKIE_SAMESITE='Lax'` is already in place. | Cross-site form posts with the user's session cookie are blocked by `SameSite=Lax`. |
| 5.7 | **Set `SESSION_COOKIE_HTTPONLY=True`** (currently implied by Django's default but worth verifying). | Blocks JS from reading the session cookie via XSS. |
| 5.8 | **Set `SECURE_REFERRER_POLICY='strict-origin-when-cross-origin'`** explicitly (currently relies on browser default). | Already set in nginx headers; verify Django doesn't override with something weaker. |

---

## 6. Infrastructure Hardening

| # | Action | Why |
|---|--------|-----|
| 6.1 | **Run nginx as a non-root user inside the container.** The `nginx:1.27-alpine` image already does this, but verify with `docker exec nginx id` (must show `nginx`). | Defense-in-depth. A nginx vulnerability + a writable filesystem = container escape. |
| 6.2 | **Set `read_only: true` on the nginx container's root filesystem** in compose. Mount `/var/cache/nginx`, `/var/run`, and `/tmp` as tmpfs. | Stops an attacker who compromises nginx from writing to the image. |
| 6.3 | **Drop all Linux capabilities** on the nginx container (`cap_drop: [ALL]`) and add only `NET_BIND_SERVICE` (for port 443). | Stops a compromised container from doing `mount`, `ptrace`, etc. |
| 6.4 | **Add `security_opt: [no-new-privileges]`** on every service. | Stops setuid binaries from escalating. |
| 6.5 | **Run all containers with a read-only bind mount for the certs** (already done: `:ro`). | A compromised nginx can't overwrite its own cert. |
| 6.6 | **Tighten resource limits.** Current limits (`cpus: 0.5, memory: 128M` for nginx) are dev defaults. Measure prod traffic, then size for `cpus: 4, memory: 512M` as a starting point. | Prevents an OOM at 3am from cascading into a node-wide failure. |
| 6.7 | **Set up a log aggregator** (Loki, CloudWatch, Splunk) that consumes the stdout JSON logs. | The current `console` handler goes to docker's log driver, which is ephemeral. |
| 6.8 | **Set up metrics** (Prometheus + Grafana). The `/metrics/` endpoint is already exposed; the observability TUI script in `scripts/observability_tui.py` is a stopgap. | You can't debug "the API is slow" without request-rate, latency, and error-rate dashboards. |
| 6.9 | **Set up alerting** on: cert expiry ≤30 days, nginx 5xx rate >1%, HSTS preload check, `SECURE_SSL_REDIRECT` looping (response time on `/health/` >100ms). | Cert expiry is the silent killer; everything else has a slow ramp. |
| 6.10 | **Disable `docker exec` in prod** for the nginx container. Replace with `kubectl exec` (k8s) or a bastion-only SSH path. | An attacker who gets onto the docker socket can attach to any container. |
| 6.11 | **Lock the host firewall** to allow only 80/443 from the public internet, plus whatever your monitoring uses. | If nginx is compromised, the blast radius is the 80/443 surface. |

---

## 7. TLS Protocol Hardening

| # | Action | Why |
|---|--------|-----|
| 7.1 | **Verify only TLS 1.2 and 1.3 are enabled** in the live handshake. | `TestNginxConfig::test_tls_protocols_are_modern` covers this in code, but verify at runtime with `nmap --script ssl-enum-ciphers -p 443 api.echoflow.example` — should show only TLSv1.2 and TLSv1.3. |
| 7.2 | **Test 0-RTT (TLS 1.3 early data) is disabled** unless you have a use case for it. | 0-RTT allows replay attacks against idempotent endpoints. The current nginx config does not enable it. |
| 7.3 | **Run an external scan** (SSL Labs, testssl.sh, observability from `https://www.ssllabs.com/ssltest/`) before going live. | Catches cipher-ordering bugs, weak DH params, OCSP misconfig that local tests miss. Aim for A or A+. |
| 7.4 | **Verify HSTS is served on the FIRST response**, not just the second. The `add_header ... always;` directive in nginx.conf covers error responses; verify with `curl -I https://api.echoflow.example/missing-path/` — HSTS must be present. | Without `always`, error responses don't get HSTS, and a downgrade attack via a forced error response is possible. |

---

## 8. Operational Runbooks

Write the following runbooks BEFORE the first prod incident, not during:

| # | Topic | Required sections |
|---|-------|-------------------|
| 8.1 | Cert renewal failure | Symptoms → Diagnosis (cert age, expiry check) → Mitigation (manual renewal via certbot) → Escalation (CA support) |
| 8.2 | nginx 502s (can't reach upstream) | `docker compose logs nginx` for connection-refused → restart upstream → check upstream healthcheck → check for resource exhaustion |
| 8.3 | HSTS misconfig rollback | Identify the affected host → if HSTS was sent in error and max-age is small (<1h), wait it out; if 1y+, the only fix is a fresh browser install for affected users (and a new domain name) |
| 8.4 | Secret rotation (`SECRET_KEY`, `FIELD_ENCRYPTION_KEY`, `HF_TOKEN`, AWS keys) | Document every secret, its rotation period, the rotation command, and the rollback path |
| 8.5 | DDoS at the edge | Activate Cloudflare (or equivalent) → enable "Under Attack" mode → identify the bad actors in nginx logs → rate-limit at edge |
| 8.6 | Frontend mixed-content break | Symptom: API calls fail with `Mixed Content: ...` in browser console → fix: update `PUBLIC_MEDIA_ENDPOINT_URL` and any other env var to `https://` → restart web |

---

## 9. Compliance / Audit

| # | Action | Why |
|---|--------|-----|
| 9.1 | Generate an SBOM (Software Bill of Materials) for the prod image. | Required by Executive Order 14028 (US federal), NIS2 (EU), and most enterprise procurement. `syft devansh1012007/echoflow-api:GITHUB_SHA` produces CycloneDX. |
| 9.2 | Sign the image with `cosign sign` and verify in the deploy pipeline. | Stops a compromised registry from serving a tampered image. |
| 9.3 | Add a CVE scanner to CI (Trivy, Grype, Snyk). | A new nginx CVE ships every ~2 months; you want to know within hours. |
| 9.4 | Add the HTTPS test suite to the CI pipeline. | Regression: a future PR that removes `SECURE_SSL_REDIRECT` should break CI, not prod. |
| 9.5 | Document the data flow (where PII lives, how it's encrypted in transit + at rest). | GDPR Article 30, SOC 2 CC6.1, HIPAA §164.312. |
| 9.6 | **Do NOT log JWT tokens, session cookies, or auth headers.** The current JSON log format is clean; verify with `grep -r "Bearer" docker-compose logs` and `grep -r "Set-Cookie"`. | Token leakage in logs is a breach reportable under most regulations. |

---

## 10. Pre-Launch Verification Script

Before flipping DNS to the new prod endpoint, run this checklist:

```bash
# Cert chain
echo | openssl s_client -connect api.echoflow.example:443 -servername api.echoflow.example -showcerts 2>/dev/null \
  | openssl x509 -noout -issuer -subject -dates

# HSTS
curl -sI https://api.echoflow.example/health/ | grep -i 'strict-transport-security'
# expect: max-age=31536000; includeSubDomains; preload

# HTTP -> HTTPS redirect
curl -sI http://api.echoflow.example/health/ | head -1
# expect: HTTP/1.1 301 Moved Permanently

# TLS profile
nmap --script ssl-enum-ciphers -p 443 api.echoflow.example | grep -E "TLSv|SSLv"
# expect: only TLSv1.2 and TLSv1.3

# Django sees HTTPS (not redirected)
curl -s -o /dev/null -w "%{http_code}\n" https://api.echoflow.example/health/
# expect: 200

# Cookie is Secure
curl -sI -X POST https://api.echoflow.example/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"username":"x","password":"y"}' | grep -i set-cookie
# expect: Secure; SameSite=Lax

# /metrics/ is NOT exposed publicly
curl -sI https://api.echoflow.example/metrics/
# expect: 401 or 403 (auth required) — NOT 200 with metrics payload
```

If any check fails, do NOT cut over. Fix and re-verify.

---

## 11. Ongoing Maintenance Cadence

| Frequency | Task |
|-----------|------|
| **Daily** | Check `/metrics/` for unexpected 5xx rate. Check nginx error log for `SSL_*` errors. |
| **Weekly** | `certbot certificates` — confirm renewal timer is healthy. |
| **Monthly** | `nmap --script ssl-enum-ciphers` and compare to last month (catches a bad upgrade). |
| **Quarterly** | External SSL Labs scan; review Mozilla's [recommended TLS config](https://wiki.mozilla.org/Security/Server_Side_TLS). |
| **Annually** | Review the `if not DEBUG:` block in `settings.py:529` — are there new Django security settings to add? (`SECURE_PROXY_SSL_HEADER` is the only one we explicitly trust; new flags ship every Django release.) |
| **Per Django upgrade** | Read the release notes' security section; bump `SECURE_HSTS_SECONDS` if Mozilla updates their recommendation. |
| **Per nginx upgrade** | Run `nginx -T` and diff against the previous version's default config for new hardening directives. |

---

## 12. Things This Doc Does NOT Cover

These are out of scope for HTTPS specifically but are required for prod:

- Application-layer auth (OAuth/JWT rotation, rate limits per user) — see `backend/EchoFlow/settings.py:438-459`
- Database backup + point-in-time recovery — see `docs/EXPLAIN/database/`
- Object storage backup + lifecycle — see `docs/EXPLAIN/storage/`
- Celery queue monitoring + dead-letter handling — see `docs/EXPLAIN/redis-celery/`
- DDoS protection at the network layer (Cloudflare, AWS Shield, etc.)
- WAF (Web Application Firewall) rules for SQLi/XSS — Django ORM + DRF serializers cover most of this, but a WAF adds another layer.
- Secrets management (Vault, AWS Secrets Manager, etc.) — current dev approach uses `.env` which is fine for dev but NOT for prod.
- Multi-region deployment + failover

Each of these is a separate project; this doc is the HTTPS-specific subset.
