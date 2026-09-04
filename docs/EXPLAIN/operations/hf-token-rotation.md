# HF_TOKEN Rotation Runbook

> **Audience:** operators / on-call engineers.
> **Goal:** a step-by-step procedure for rotating the HuggingFace token without downtime, plus the failure modes that can surface during rotation.
>
> **Status:** Active runbook (B17 in `docs/EXPLAIN/decisions/partial-issues-completion-plan.md`).

---

## When to rotate

Rotate the HuggingFace token in any of these cases:

1. **Scheduled rotation** — every 90 days. This is the HuggingFace default recommendation and a reasonable cadence for a build secret.
2. **Suspected exposure** — the token appeared in a log, paste, screenshot, or terminal scrollback that may have been shared. Rotate immediately, regardless of cadence.
3. **Operator departure** — anyone with the token value (local `.env`, CI secret, production secret store) leaves the team. Rotate so the departing operator's copy is invalidated.
4. **HuggingFace dashboard compromise** — if HuggingFace reports a security event affecting your account, rotate and audit downstream usage.

The token is a **build-time** secret. A rotation does not invalidate previously-built images (the baked model weights are already in the layer). It only changes which tokens can authenticate **future** image builds.

## What HF_TOKEN does

The token is consumed **only** at image build time, exclusively by the `celery_media` stage of `Dockerfile`. It authenticates downloads of three model weight archives:

- **Whisper** (`faster_whisper.WhisperModel('base', ...)`) — speech-to-text, ~1 GB
- **SentenceTransformer** (`sentence_transformers.SentenceTransformer('all-MiniLM-L6-v2')`) — 384-dim embeddings, ~90 MB
- **KeyBERT** (`keybert.KeyBERT()`) — keyword extraction from transcripts, ~tiny

The runtime **never** uses the token. The `celery_media` container runs with `HF_HUB_OFFLINE=1` + `TRANSFORMERS_OFFLINE=1` (`docker-compose.yml`), which forces the HuggingFace SDK to read from the baked-in cache (`HF_HOME=/home/appuser/.cache/huggingface`) and refuse all network calls. There are zero references to `HF_TOKEN` in `backend/` source code.

This is the entire security boundary: the token can authenticate a download during a build. It cannot authenticate any runtime operation. A leaked token can be used to re-build the `celery_media` image with potentially-different model weights (e.g., a supply-chain attack against HuggingFace) but it cannot read or write application data, user accounts, or stored media.

## Where it's used

**Build-time (token needed):**

| File | Lines | Purpose |
|---|---|---|
| `Dockerfile` | 117–124 | `RUN --mount=type=secret,id=hf_token` — the build-time secret mount that consumes the token. |
| `docker-compose.yml` | 411–417 | `secrets: [hf_token]` block on the `celery_media` build — forwards the token to BuildKit. |
| `docker-compose.yml` | 593–596 | Top-level `secrets.hf_token.environment: HF_TOKEN` — sources the secret from the host's `HF_TOKEN` env var. |
| `.github/workflows/docker-image.yml` | 106–111 | CI uses the GitHub Actions `HF_TOKEN` secret, forwarded to BuildKit as `secret id=hf_token`. |
| `.env` / `.env.example` | — | Local-dev source of the env var. Gitignored. |

**Runtime (token NOT needed):**

| File | Lines | Purpose |
|---|---|---|
| `docker-compose.yml` | 455–457 | `HF_HOME=/home/appuser/.cache/huggingface`, `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1` — runtime env on `celery_media`. |
| `backend/` | — | No `HF_TOKEN` references. Verified by grep in `docs/backend-bug-fixs.md:1231`. |

The two-line guard at `Dockerfile:119-120` (`if [ -s /run/secrets/hf_token ]; then export HF_TOKEN="$(cat /run/secrets/hf_token)"; fi`) is the only place the token is consumed. Empty or absent secret = anonymous download. Whisper, SentenceTransformer, and KeyBERT are all public models on HuggingFace Hub, so anonymous download **succeeds** at the model's metadata level. Whether it succeeds at the bandwidth level depends on HuggingFace's anonymous rate limits (see *Failure modes* below).

## Rotation procedure

Six steps. Each step is reversible; the worst case is "no new image builds until you fix it."

1. **Generate the new token.**
   - Go to <https://huggingface.co/settings/tokens>.
   - Click "New token".
   - Name: `echoflow-rotation-YYYY-MM-DD` (include the date so you can identify it later).
   - **Scope: `read`.** The models are public; "write" is not needed. Avoid "write" so a leaked token cannot push models.
   - Save the token value somewhere temporary (password manager, secure scratch file). You will paste it into the secret store in step 2 and then **delete the scratch copy**.

2. **Update the secret store.** There are three places the token lives; update all of them:

   - **Local dev:** edit `.env` (gitignored) — replace the `HF_TOKEN=` value.
   - **CI:** GitHub → repo → Settings → Secrets and variables → Actions → `HF_TOKEN` → update value.
   - **Production / managed deploy:** update the deployment platform's secret store (k8s secret, AWS Secrets Manager, GCP Secret Manager, etc.). Reference name varies by platform — match what `docker-compose.yml:594` or the k8s manifest reads.

3. **Rebuild the `celery_media` image with no cache.**
   ```bash
   docker compose build --no-cache celery_media
   ```
   The `--no-cache` is **critical**. Without it, Docker reuses the existing model-weight layer and the new token is never exercised; the build succeeds even with a broken token. With `--no-cache`, the bake step in `Dockerfile:117-124` re-runs, downloads the models fresh, and the new token is the one that authenticates the download.

   Expect 5–10 minutes. The model downloads are the slow part.

4. **Roll the running `celery_media` containers.**
   ```bash
   docker compose up -d celery_media
   ```
   This terminates the old worker and starts the new one. There is a brief window during which HLS-processing tasks queued in Redis cannot be picked up; they will accumulate and drain once the new worker boots.

5. **Verify the new worker started cleanly.**
   ```bash
   docker compose logs --tail=50 celery_media | grep -E "(ready|started|listening|connected to redis)"
   ```
   A successful start prints something like "celery@<hostname> ready." or "Connected to redis://redis_broker:6379/0". If you see repeated retry attempts or "Connection refused" errors, the worker cannot reach Redis — fix the network path before continuing.

6. **No app downtime.** The web tier does not load ML models; only `celery_media` does. Other Celery workers (`celery`, `celery_feed`, `celery_beat`) are not affected. The brief window during the `celery_media` restart will accumulate HLS-processing tasks; they drain as soon as the new worker comes up. No user-facing request fails.

## Failure modes

**Empty HF_TOKEN at build time.** The `if [ -s /run/secrets/hf_token ]` guard at `Dockerfile:119` treats an empty file the same as no file: anonymous download. Whisper, SentenceTransformer, KeyBERT are all public models, so anonymous download **succeeds at the metadata level**. The risk is **rate limiting**: HuggingFace throttles anonymous traffic more aggressively than authenticated. If a build with an empty token fails with HTTP 429 ("Too Many Requests"), the fix is to set the new token and re-run with `--no-cache`.

**Expired or revoked token.** HuggingFace tokens do not have a "valid until" date in the secret itself; the dashboard-side "Revoke" action is what invalidates them. If you set a token in `.env` that was already revoked on the dashboard side, the build's first download attempt fails with HTTP 401 ("Unauthorized"). The same `[ -s ]` guard catches it (the secret exists but is invalid; download fails). The fix: confirm the token is fresh in the dashboard; re-paste; rebuild.

**Secret store propagation delay.** Some secret stores (k8s, AWS Secrets Manager with cached credentials) have a propagation delay of up to 60 seconds. If you set the new token and immediately build, the build may read the old token. Workaround: wait 60 s after step 2 before running step 3.

**Build cache hides a bad token.** This is the most insidious failure. If you skip `--no-cache` in step 3, the bake step is a no-op (model weights are already in a previous layer), and the build succeeds even with a completely invalid token. The token's only purpose is to authenticate the download; if no download happens, no authentication is needed. **Always pass `--no-cache` to `celery_media` after a rotation.**

**CI build fails on a rotated token.** The same `[ -s ]` guard handles this; the build fails with HTTP 401. Update the GitHub Actions secret (step 2); the next CI build will use the new value.

## Audit log template

Document every rotation. Append to `docs/EXPLAIN/operations/rotation-audit.log.md` (or your team's audit log):

```text
## YYYY-MM-DD — HF_TOKEN rotation

- **Operator:** <name>
- **Trigger:** <scheduled 90-day | suspected exposure | operator departure | HF-side event>
- **Old token:** hf_...XXXX (last-4 only; never paste the full token)
- **New token:** hf_YYYY... (first-4 only; never paste the full token)
- **Build result:** <success | failure — see notes>
- **Worker restart:** <time of `docker compose up -d celery_media`>
- **Verification:** <log line confirming worker ready>
- **Notes:** <anything weird: rate limits, secret store delays, etc.>
```

The last-4 / first-4 convention is enough to correlate with the HuggingFace dashboard (which shows full tokens only to the user who created them) without leaking the value into a long-lived document.

---

## Related docs

- `Dockerfile:117-124` — the build-time secret mount
- `docker-compose.yml:411-417, 593-596` — BuildKit secret plumbing
- `docs/EXPLAIN/decisions/01-key-decisions.md:206-216` — *BuildKit Secret for HF_TOKEN* (decision rationale)
- `docs/EXPLAIN/docker/01-multi-stage-dockerfile.md:277-303` — security discussion
- `docs/backend-bug-fixs.md:1221-1271` — original audit verification of HF_TOKEN architecture
- `AGENTS.md` — environment variables reference (HF_TOKEN section)