# Report — INV-BACKEND-B (Wave 0) · 20261107-revenuecat-paytm

## ORCH-DIGEST
STATUS: COMPLETE
BRANCH: feat/revenuecat-paytm-phase1 @ 8765b73 [FACT git rev-parse --short HEAD; git branch --show-current]
SCOPE: Defects 3.3 (webhook fail-open), 3.4 (no-op), 3.6 (duration/HD unenforced), 3.7 (write-on-GET), 3.10 (erasure ignores billing), 3.11 (vendor names). Plan C02, C03, C05, C07; tests T02, T03/T04, T06, T09.
VERDICTS:
- 3.3 CONFIRMED fail-open (dual-cause: condition AND missing settings definition). C02/T02 as scoped in plan is correct but slightly understated.
- 3.4 CONFIRMED no-op (log + 200, no state change). C02/T02 depends on it — fail-closed alone does not fix 3.4; coder must not “enable processing” without Phase 2 design.
- 3.6 CONFIRMED unenforced: 60s/HD settings are advertise-only. Only tier-blind 300s + file-size + daily-count exist. No hidden enforcer in HLS task, feed, or uploads service. C03/T03-T04 correct; decision required (enforce vs unadvertise).
- 3.7 CONFIRMED write-on-GET, but narrow: `_app_user_id()` writes iff `revenuecat_app_user_id` is null/empty, called from two GETs (status + manage). DB now NOT NULL + default uuid4 so production rows rarely hit the branch; must handle the defensive null path, not just normal rows. C05/T06 correct; T06 must construct a null-ID stand-in, not a normal fixture user.
- 3.10 CONFIRMED: erasure.py has literally zero billing handling (no matches for billing/subscription/entitlement/purchase/payment/CanCel semantics); DataSubjectErasureView also has no pre-delete billing check; test_erasure.py has no billing case. C07/T09 correct (warning now, block after Paytm adapter).
- 3.11 CONFIRMED: vendor-named product config in settings.py:1205-1208 (plus full REVENUECAT_* surface — see §7). C03 rename to BILLING_*_FREE with compat fallback is required; scope is larger than just the four free-limit vars (DB column + URL + task name + service module also carry the vendor name — defer column/URL rename to later phases, rename env vars now).
BLOCKERS: None. Backend test baseline NOT capturable here (no Docker); no test runs per brief. All verdicts are static-evidence only; T02/T03/T06/T09 fail-before tests are proposed, not executed.

---

## 1. Method, reading order, evidence standard

**Reading order followed (per brief §2, in order):**
1. Plan `C:\Users\devan\.opencode\plan\2026-11-07-revenuecat-paytm-billing.md` §C, §E (C02, C03, C05, C07), §H (T02, T03/T04, T06, T09) — plus §F touch-sets and §I product gates P1–P4 for context. [FACT plan file read]
2. `docs/agent-ops/20261107-revenuecat-paytm/00-mission.md`, `01-work-ledger.md`, `COMMS.md`. [FACT files read]
3. Code in brief order: `backend/app/views/subscription.py` (full, 168 lines), `backend/app/services/revenuecat.py` (full, 291 lines), `backend/app/serializers.py:380-460` (via full read + focused re-read), `backend/app/views/content.py:198-215` (via full read, create at :198–232), `backend/app/services/erasure.py` (full, 231 lines), `backend/app/urls.py:135-139` (full file), `backend/EchoFlow/settings.py:1190-1208` (focused read + grep), `backend/app/tests/test_revenuecat.py:601-680` (plus :250–349 for status-shape context).

**Additional reads required to close the brief’s “grep every usage” / “truly zero” questions:**
- `backend/app/tasks.py:130–539` (HLS encode + `sync_revenuecat_entitlements`), `backend/app/views/data_subject.py` (full, 129 lines), `backend/app/models.py:57–93` (`is_pro()` + billing columns), plus targeted greps: `MAX_DURATION_SECONDS|CLIP_DURATION|HD_QUALITY`, `REVENUECAT_WEBHOOK_SECRET`, `revenuecat_app_user_id|_app_user_id`, `is_pro\(\)`, and billing-term grep over `erasure.py` + `test_erasure.py`.
- No test runs, no installs, no Docker (per brief; also unavailable — `00-mission.md:6` records backend tests require Docker+Postgres, Python here is 3.12 vs repo 3.11).

**Tagging:** [FACT path:line] = directly observed in code. [INFERENCE] = reasoned conclusion from facts. [UNKNOWN] = not verifiable statically / needs runtime confirmation. Line numbers are HEAD@8765b73 on `feat/revenuecat-paytm-phase1` [FACT git].

**Plan-vs-code note on scope:** Plan §C states the six defects as already-verified [FACT plan:26]. This report independently re-verifies each against current code rather than trusting the plan, per brief. Where the code has moved on since the plan’s line numbers (e.g., `serializers.py` duration probe now at :459–485, not :338), the discrepancy is flagged but does not change the verdict. [INFERENCE]

---

## 2. Defect 3.3 — Webhook verifies only if secret AND header (fail-open)

### Verdict: CONFIRMED — fail-open, with a second cause the plan understates

**Path:line evidence:**
- Webhook condition: `backend/app/views/subscription.py:156` — `if getattr(settings, "REVENUECAT_WEBHOOK_SECRET", "") and signature:` [FACT subscription.py:156]. Verification block (HMAC-SHA256 over `request.body`, `hmac.compare_digest`, 401 on mismatch) exists at :157–166 [FACT subscription.py:157-166]. Else-branch falls through to `return Response({"status": "received"})` at :168 [FACT subscription.py:168].
- **Second cause — the setting is never defined:** grep for `WEBHOOK` in `backend/EchoFlow/settings.py` returns **no matches** [FACT grep settings.py]. The `REVENUECAT_*` block at `settings.py:1190-1208` defines `REVENUECAT_SECRET_KEY` (:1194), `REVENUECAT_PUBLIC_KEY` (:1195), `REVENUECAT_PROJECT_TOKEN` (:1196), `REVENUECAT_ENTITLEMENT_ID` (:1200), `REVENUECAT_SYNC_INTERVAL_MINUTES` (:1201), `REVENUECAT_CUSTOMER_PORTAL_URL` (:1202), and the four free-limit vars (:1205–1208) — but **not** `REVENUECAT_WEBHOOK_SECRET` [FACT settings.py:1190-1208]. The secret is read only via `getattr(settings, "REVENUECAT_WEBHOOK_SECRET", "")` in the view [FACT subscription.py:156,160] and appears only in `.env.example:261`, `.env.vps.example:185`, and tests that set it on the `settings` fixture [FACT grep]. [INFERENCE] In production (settings imported without a test-injected attribute), `getattr` returns `""` always, so the `and` short-circuits false **even if the caller sends `X-RevenueCat-Signature`**. The fail-open is therefore not “header missing ⇒ skip” in one case; it is “normally always skip, plus header missing ⇒ skip” in all cases.
- Existing test pins the fail-open shape: `backend/app/tests/test_revenuecat.py:604-607` — `test_webhook_returns_200_empty_signature` posts without signature and asserts 200 [FACT test_revenuecat.py:604-607]. The other two tests set the secret explicitly and assert 401 on bad sig (:609–620) / 200 on valid sig (:622–634) [FACT test_revenuecat.py:609-634], proving the verify branch works *when the test injects the attribute the production settings never define*. [INFERENCE] The suite therefore passes while production is unverified — exactly the §H baseline note that `test_revenuecat.py` “covers … webhook 200-pin (:604)” [FACT plan:95].

**Truth table (secret = `getattr`, signature = `HTTP_X_REVENUECAT_SIGNATURE` header):**

| Secret set? | Signature sent? | Current behaviour | Desired (C02) |
|---|---|---|---|
| No (always, prod) | No | 200 `received`, no verify | 200 only if unconfigured — this row is the documented unconfigured case, acceptable **only** if it stays a no-op (see §3) |
| No (always, prod) | Yes | 200 `received`, header ignored | Must **not** be treated as verified; either 200-no-op (unconfigured) or 401 (if we choose fail-closed even when unconfigured — plan says keep 200-no-op only when unconfigured [FACT plan C02]) |
| Yes (test-only today) | No | 200 `received`, **sig absence ignored** | **401** (reject unsigned when secret configured) |
| Yes | Yes (bad) | 401 [FACT :165-166] | 401 — already correct |
| Yes | Yes (good) | 200 [FACT test :622-634] | 200 — already correct |

[INFERENCE] The (Yes, No) row is the security-relevant fail-open: an attacker who omits the header never triggers the `compare_digest` check, so the HMAC code is dead code for the attacker’s cheapest path. The (No, Yes) row is a config defect: even a legitimate RevenueCat retry with a real signature is accepted without verification because production has no secret to verify against.

**Root cause:** Two independent defects combined: (a) the guard is `secret AND signature` instead of “reject if secret configured and signature absent/invalid” — the `and signature` term makes absence a bypass; (b) the secret has no settings definition, so `getattr` default makes the guard’s first term permanently falsy in production. Either alone fails open; together they mean the HMAC path never executes outside tests. [INFERENCE]

**Plan C02 assessment:** Plan says “Fail-closed: reject unsigned when secret configured (401); keep 200-no-op only when unconfigured” and “Gate on secret presence, not header presence; update pinning test” [FACT plan:44]. The plan’s direction and the T02 test (“webhook unsigned with secret set → 401, no state change” [FACT plan:99]) are correct. The plan’s §C one-liner (“webhook verifies only if secret AND header (subscription.py:156)” [FACT plan:26]) is accurate as far as it goes but omits the missing-settings-definition cause, so a coder who only changes the `and` without adding a settings definition will leave production still failing open (new test with `settings.REVENUECAT_WEBHOOK_SECRET` set will pass while prod stays `""`). The coder must add the settings read (and decide whether to also add it to `settings.py` explicitly vs. `getattr` + env templates). [INFERENCE]

**Coder touch-set (C02):**
- `backend/app/views/subscription.py::RevenueCatWebhookView.post` (:149–168) — restructure to: read secret; if secret: require signature and verify, else 401 (do **not** `and signature`); if no secret: log + 200 (preserve no-op contract). Must also stop reading `request.data.get("event_type")` before verification? Currently :151 reads parsed body before auth — harmless for a no-op but a poor pattern to copy to Paytm (B2 in plan §I: “checksum + status re-verify, never fail-open” [FACT plan:121]). At minimum, do not let the unsigned path do anything other than log. Keep `permission_classes = [AllowAny]` (:147) — required for a webhook; fail-closed is inside `post` [INFERENCE].
- `backend/EchoFlow/settings.py:1190-1208` — add `REVENUECAT_WEBHOOK_SECRET = os.environ.get('REVENUECAT_WEBHOOK_SECRET', '')` (or document why `getattr` is intentional). Without this, C02 is test-green, prod-red. [INFERENCE]
- `backend/app/tests/test_revenuecat.py::TestRevenueCatWebhookView` (:601–634) — update `test_webhook_returns_200_empty_signature` to reflect the new contract (it must set empty secret explicitly for the unconfigured case, and a new T02 case must set the secret + omit header → 401). Do not delete the 200-pin; re-pin it to the unconfigured precondition.
- No model/migration, no `tasks.py` change for C02. [FACT plan:44]

**Fail-before test (T02, per plan §H [FACT plan:99]):**
- Fixture: set `settings.REVENUECAT_WEBHOOK_SECRET = "real-secret"` (mirror existing tests :611,624), POST `/webhooks/revenuecat/` with a JSON body and **no** `X-RevenueCat-Signature` header. Assert 401. Assert no state change (User Pro fields unchanged; no `sync_entitlements` call — mock-spy to prove the webhook does not sync — see §3). Today this returns 200 [FACT current code], so the test fails before the fix and passes after. [INFERENCE — proposed, not run]
- Companion: secret set + bad sig → 401 already passes; keep as regression guard. Secret unset + no sig → 200 pins the unconfigured contract. [INFERENCE]

**Unknowns / cautions:**
- [UNKNOWN] Whether any production `.env` sets `REVENUECAT_WEBHOOK_SECRET` such that Django `settings` would pick it up: Django settings **cannot** pick up an env var unless `settings.py` reads it, and it does not (see above). So operator belief (“we configured the webhook secret”) cannot make this verify today. Verify by checking the running container’s `settings` attribute / env interpolation, not just `.env` presence. [INFERENCE]
- [UNKNOWN] RevenueCat’s actual webhook event types / retry behaviour — out of scope for C02 (webhook stays no-op); do not implement event processing in the C02 commit. [INFERENCE from plan:44 + §3 docstring]

---

## 3. Defect 3.4 — Webhook is a no-op (logs + 200, no state change)

### Verdict: CONFIRMED — no-op by design in Phase 1, but the docstring misleads about what “verify” buys

**Path:line evidence:**
- Docstring is explicit: “Phase 1 (free tier): Webhooks are not used — syncing is done via REST API polling … This endpoint exists for forward-compatibility and returns 200” [FACT subscription.py:131-134]; “SECURITY: Even though we don’t process events yet, we must return 200 for any valid POST so RevenueCat doesn’t retry” [FACT subscription.py:140-142]; “Future implementation will … Parse the event type and update the User model accordingly” [FACT subscription.py:144-145, future tense].
- Body does exactly that and nothing more: reads header + `event_type`, logs, optionally verifies HMAC (see §2), returns `{"status": "received"}` [FACT subscription.py:149-168]. There is **no** call to `sync_entitlements`, `get_subscriber_info`, or any User write in `RevenueCatWebhookView.post` [FACT subscription.py:149-168; sync import at :98 is inside `SubscriptionSyncView.post` only]. No `WebhookEvent` persistence (that model is Phase 2, C08 [FACT plan:50]). So every event — renewal, expiry, revoke, refund — is acknowledged and discarded; polling (`tasks.py:502` `sync_revenuecat_entitlements`, 6h beat [FACT tasks.py:501-511; settings.py:703-708]) remains the sole writer of billing state [FACT revenuecat.py:187-243 `sync_entitlements`].
- Tests pin the no-op: all three webhook tests assert either 200 or 401 and `{"status":"received"}` on 200, none assert a User-field change [FACT test_revenuecat.py:604-634]. Plan §H likewise lists webhook coverage as “200-pin (:604)” only [FACT plan:95].

**Root cause:** Intentional Phase 1 deferral (free-tier polling), not an accidental stub — but combined with §2 it means a *verified* webhook is still semantically void: even a correct HMAC today reconciles nothing. [INFERENCE]

**Interaction with C02 (coder warning):** C02 only changes *whether* an unsigned call is rejected; it does not make a verified call do anything. A coder who “fixes 3.4” by parsing the event and writing User fields inside `post` would silently introduce a second writer before the Phase 2 single-writer gate (C09 parity + C12 migration [FACT plan:59]) and before the unique-key/idempotency design (plan §I Money M1–M11 [FACT plan:117]). [INFERENCE] The 3.4 fix belongs to C10/C11 (Google/Apple adapters + shadow, C13 for Paytm), not C02. Phase 1 must keep the endpoint a no-op; the correct C02 commit changes status codes, not semantics. If RevenueCat actually sends webhooks today (dashboard URL configured), the system is *losing* events silently — the 6h poll masks this as mere staleness (Q2 in plan §L: “6h stale-Pro cost” [FACT plan:144]). [INFERENCE]

**Coder touch-set (no Phase 1 change beyond C02):**
- None for 3.4 alone. The only Phase 1 file is `subscription.py` via C02 (§2 touch-set). Do **not** add event parsing, User writes, or `WebhookEvent` rows in Phase 1. Later (C08/C10/C11/C13) the touch-set becomes `backend/app/billing/` models + adapters [FACT plan:50-55], which is out of this run’s scope [FACT 00-mission.md:5]. [INFERENCE]
- Optional logging hygiene: current log includes only `type` and `sig_len` [FACT subscription.py:152] — good (no payload/secret). Do not log `request.body` when adding verification. Plan §K: “log order IDs only, never full payloads” [FACT plan:131] applies by analogy. [INFERENCE]

**Fail-before test (for the *future* 3.4 fix, not T02):**
- No Phase 1 fail-before test for 3.4 beyond T02’s “no state change” half (webhook POST, even signed, leaves `has_pro_entitlement`/`pro_expires_at` untouched). That assertion is what stops the C02 coder from “helpfully” adding processing. Propose: signed webhook with an expiry payload → assert User fields unchanged (pins no-op) + assert T02 (unsigned → 401). When Phase 2 lands, the first assertion flips. [INFERENCE — proposed, not run]

**Unknowns:**
- [UNKNOWN] Whether RevenueCat dashboard has a webhook URL configured to `/webhooks/revenuecat/` today, and if so, how many events are being dropped per 6h poll window. Check dashboard + `RevenueCat webhook received` log volume. [INFERENCE]

---

## 4. Defect 3.6 — Free-tier 60s duration and HD-quality limits are advertised but unenforced

### Verdict: CONFIRMED — advertise-only. Only tier-blind 300s, file-size, and daily-count are enforced. No hidden enforcer exists in the HLS task, feed, or uploads service.

**Exhaustive “where is it actually enforced if anywhere” audit (brief’s explicit ask):**

**(a) The advertised limits (the lie the client reads):**
- `backend/app/views/subscription.py:31-37` — `_get_limits()` free branch returns `"max_clip_duration_seconds": getattr(settings, "REVENUECAT_CLIP_DURATION_LIMIT_FREE", 60)` (:34) and `"hd_quality_allowed": not getattr(settings, "REVENUECAT_HD_QUALITY_BLOCKED_FREE", True)` (:36) [FACT subscription.py:31-37]. Pro branch advertises 300s (`MAX_DURATION_SECONDS`, :27) + HD always True (:29) [FACT subscription.py:24-30]. Shape pinned by tests: `test_response_keeps_its_documented_shape` asserts keys `app_user_id, is_pro, expires_at, grace_until, last_synced, limits` [FACT test_revenuecat.py:285-290]; frontend renders HD row from it [FACT AccountPanel.tsx:104]. [INFERENCE] Clients (web AccountPanel, mobile) display a 60s/HD promise the server does not keep — the classic “advertise-only” failure; `docs/EXPLAIN/decisions/2026-09-29-unenforced-subscription-limits.md` already records this and AGENTS.md’s “Enforcement points” list is flagged wrong until commit 19 [FACT AGENTS.md:983].

**(b) The tier-blind enforcer (applies to everyone, Pro and Free alike):**
- `backend/app/serializers.py:459-485` — `validate_original_file` probes duration with pydub and rejects `> max_seconds` where `max_seconds = getattr(django_settings, 'MAX_DURATION_SECONDS', 300)` [FACT serializers.py:460]. The probe is tier-blind: it runs for every authenticated uploader regardless of `is_pro()`; `validate()` (:334–384) calls `_enforce_free_limits` only for non-Pro, but `_enforce_free_limits` itself contains **only** the file-size check (:386–398) [FACT serializers.py:386-398]. So a free user uploading 61s–300s passes the probe (300s cap), and the 60s setting is never read on the upload path. Grep for `CLIP_DURATION_LIMIT_FREE|HD_QUALITY_BLOCKED_FREE` in `backend/**/*.py` returns only the two status-view reads above (plus settings definition and env templates) [FACT grep]. No other reader exists. [INFERENCE]
- File-size is the *only* free-tier upload limit enforced in the serializer: `REVENUECAT_UPLOAD_MAX_SIZE_MB_FREE` (default 10) at :393–398, gated on `not request.user.is_pro()` at :382 [FACT serializers.py:382,393-398]. `MAX_SIZE = 100MB` global (:284) also applies to everyone via :401–402 [FACT serializers.py:284,401-402]. [INFERENCE]

**(c) The HLS task — no tier branch at all (AGENTS.md’s “task enforces duration+quality” is false):**
- `_process_audio_to_hls_impl` (`backend/app/tasks.py:242-491`): moderation gate (:254–260) → decode/normalize (:279–299) → librosa/Whisper/embed (:317–428) → **single hardcoded ffmpeg encode** at :439–446: `'ffmpeg', '-y', '-i', normalized_path, '-c:a', 'aac', '-ar', '44100', '-ac', '2', '-b:a', '128k', ...` [FACT tasks.py:439-446]. No `is_pro`, no `REVENUECAT_HD_QUALITY_BLOCKED_FREE`, no `CLIP_DURATION_LIMIT_FREE`, no 320k branch, no 48kHz/sample-rate branch. Every clip — Free or Pro — is encoded 44.1kHz/128kbps single-variant. Grep for `hd_quality|bitrate|sample_rate|48k|320` in backend returns no tier-gating hit (only docs describing the aspirational table, and diagnostics printing `sample_rate` from ffprobe output) [FACT grep]. [INFERENCE] HD/320kbps enforcement would require a new branch; today Pro users do not receive HD either, so the advertised Pro benefit is also fictional.
- Duration in the task: `clip.duration_ms` is *measured* (`librosa.get_duration` :330–331) and saved — never compared to a free-tier cap [FACT tasks.py:330-333]. No rejection path for over-60s exists in the worker. [INFERENCE]

**(d) Feed / suggestions / playback — no HD or duration filter:**
- No feed queryset filters on quality/bitrate/duration-tier. The money-relevant feed filter is licensing (`is_noncommercial=False, requires_share_alike=False`), not billing. Grep `is_pro|hd_quality|CLIP_DURATION` shows `is_pro()` read only in `subscription.py:23,66,108`, `content.py:202` (daily count, see (e)), and `serializers.py:382` (file size, see (b)) [FACT grep]. Feed/views never call `is_pro()`. [INFERENCE] AGENTS.md’s “Feed views — HD quality filtering” enforcement point [FACT AGENTS.md:619] has no corresponding code.

**(e) Daily-upload count — the one billing limit that IS enforced (control case proving the other two are not):**
- `backend/app/views/content.py:198-215` — `create()` checks `if not request.user.is_pro()` then counts today’s clips vs `REVENUECAT_DAILY_UPLOAD_LIMIT_FREE` (default 5) and raises `PermissionDenied` [FACT content.py:202-215]. Test `test_free_user_blocked_after_daily_limit` proves it (5 seeds → 403) [FACT test_revenuecat.py:641-657]. This confirms the codebase *can* enforce a free limit when it wants to — duration/HD simply were never wired. [INFERENCE]
- Note the asymmetry for the coder: `_get_limits()` also advertises `daily_uploads_remaining` via `_free_uploads_remaining` (:40–47) [FACT subscription.py:40-47], and unlike duration/HD, that advertisement *is* backed by enforcement. [INFERENCE]

**(f) Env/config surface (for the C03 rename):**
- `backend/EchoFlow/settings.py:604` — `MAX_DURATION_SECONDS = int(os.getenv('MAX_DURATION_SECONDS', '300'))` (tier-blind global) [FACT settings.py:604]; `settings.py:1207` — `REVENUECAT_CLIP_DURATION_LIMIT_FREE` (default 60) [FACT settings.py:1207]; `settings.py:1208` — `REVENUECAT_HD_QUALITY_BLOCKED_FREE` (default True) [FACT settings.py:1208]; `.env.example:210,265-266`, `.env.vps.example:189-190` carry the same three [FACT grep]. No migration, no model field for duration/HD tier — enforcement would be pure code + settings. [INFERENCE]

**Root cause:** The gating table (docs) was implemented as a status-view DTO (`_get_limits`) without the corresponding upload-path branches. `_enforce_free_limits` was left with only the size check; the duration probe was written as a global safety cap (300s, to stop a 24h WAV wasting bandwidth/storage/worker — see comment at :442–445) and never split by tier; the HLS encoder was written as a single variant. The 60s/HD settings became dead config with an active advertisement. [INFERENCE]

**Plan C03 assessment:** Plan says “Decide enforce vs stop advertising 60s/HD limits; rename `REVENUECAT_*_FREE` → `BILLING_*_FREE` … Enforce in serializer/task OR remove from status view; rename with compat env fallback” [FACT plan:45]. This is the correct remedy and the coder **must not** default to one side alone. [INFERENCE] Constraint: `frontend/src/components/profile/AccountPanel.tsx:104` renders `limits.hd_quality_allowed` and web/mobile Upload flows display the 60s figure (plan §F notes frontend untouched via shim [FACT plan:76]; DECISIONS.md notes “Upload now blocks at the server-advertised free-tier duration (60s)” [FACT grep DECISIONS.md:95]). If the coder stops advertising, the UI copy must be updated (frontend is *not* fully untouched for C03). If the coder enforces, previously-passing 61–300s free uploads become 400s — a breaking change needing operator sign-off (plan G D5 prices / P2 “enforce-or-unadvertise before selling” [FACT plan:122]). The investigation cannot choose; flag as a decision gate.

**Coder touch-set (C03 — depends on the enforce/unadvertise decision):**
- *Enforce path:* `serializers.py::_enforce_free_limits` (:386–398) gains a free-tier duration branch (probe duration already measured in `validate_original_file` :468 — reuse, do not re-probe); `tasks.py` HLS encode (:439–446) gains a tier branch (128k vs 320k / 44.1k vs 48k+) or a documented “encode is tier-blind, HD is a playback concern” alternative; `subscription.py::_get_limits` (:21–37) stays as-is (now truthful). Requires a duration value available at enforcement time — today `duration_seconds` is local to `validate_original_file` (:468), so the coder must thread it to `_enforce_free_limits` or re-derive it. [INFERENCE]
- *Unadvertise path:* `subscription.py::_get_limits` (:21–37) returns the tier-blind 300s for both tiers (or a `BILLING` var) and removes/neutralises `hd_quality_allowed`; frontend/mobile copy updated accordingly (web `AccountPanel.tsx`, mobile upload pre-validation per `docs/EXPLAIN/decisions/2026-09-29-mobile-task-list.md:172`). Serializer/task unchanged. [INFERENCE]
- *Either path (rename, §7):* `settings.py:1205–1208` → `BILLING_*_FREE` with compat fallback (`os.environ.get('BILLING_...', os.environ.get('REVENUECAT_...', default))`); readers in `serializers.py:393`, `content.py:206`, `subscription.py:31-36` updated to the new name; `.env.*.example` + `conftest.py` defaults updated. See §7 for full list. [INFERENCE from plan:45 + grep]

**Fail-before tests (T03/T04, per plan §H [FACT plan:100]):**
- T03 (duration): authenticated free user uploads a >60s (e.g., 61s / 90s) otherwise-valid audio file → today returns 201/202 (probe allows ≤300s) [INFERENCE from code]; after enforce, assert 400 naming the free-tier limit. Must fabricate a real >60s WAV (flat-colour lesson from AGENTS.md: assert fixture duration, not just file presence) — needs ffmpeg/pydub harness, Docker (blocked here). [INFERENCE — proposed]
- T04 (HD): define the observable first. If “HD” means encode bitrate/sample-rate, the test must assert on the HLS output (e.g., ffprobe `sample_rate`/`bit_rate` of the master/variant) for free vs Pro — no such harness exists today (tasks has zero behavioral tests per `missing-test.md:48` [FACT grep]). Simpler Phase 1 alternative: assert the *advertisement* matches enforcement (status `hd_quality_allowed` ↔ actual encode tier). Do not write a test that mocks the answer. [INFERENCE]
- Companion (unadvertise decision): free status returns `max_clip_duration_seconds == 300` and no HD differential — assert against `subscription.py:34,36`. [INFERENCE]

**Unknowns:**
- [UNKNOWN] What “HD” means operationally (48kHz+ per AGENTS.md:613 vs 320kbps per :614 — two different rungs). The coder needs a one-line definition before implementing the HLS branch; the investigation cannot settle it. [FACT AGENTS.md:613-614; INFERENCE]
- [UNKNOWN] Whether any client blocks upload at 60s today (web DECISIONS.md suggests yes for web). If so, backend enforcement is a no-op for web users but still a breaking change for API/mobile callers. Check mobile `formatTime`/`skipSeconds` behaviour (both reference the 60s cap in comments [FACT grep]).

---

## 5. Defect 3.7 — Write-on-GET via `_app_user_id()`

### Verdict: CONFIRMED, but narrow and mostly latent for production rows. C05/T06 correct; T06 must use a null-ID stand-in.

**Path:line evidence:**
- Write branch: `backend/app/services/revenuecat.py:272-275` — `if not user.revenuecat_app_user_id: user.revenuecat_app_user_id = uuid.uuid4(); user.save(update_fields=["revenuecat_app_user_id"])` [FACT revenuecat.py:272-275]; returns `str(...)` at :275 [FACT revenuecat.py:275]. Docstring (:246–271) justifies it as defence-in-depth for “a `User` instance that was never reloaded from the database — e.g. built in memory, or returned by a `.only()` queryset that omitted the column” [FACT revenuecat.py:261-264], and notes the backfill is safe because “a NULL id means the row has never been sent to RevenueCat” [FACT revenuecat.py:268-270].
- GET callers (both are `GET` methods that mutate):
  1. `backend/app/views/subscription.py:65` — `SubscriptionStatusView.get` (:58–73) builds `{"app_user_id": _app_user_id(user), ...}` [FACT subscription.py:58-73]. So `GET /subscription/` (urls.py:136 [FACT urls.py:136]) writes iff null.
  2. `backend/app/views/subscription.py:120-123` — `SubscriptionManageView.get` calls `get_customer_portal_url(user)` [FACT subscription.py:120-123], which calls `_app_user_id(user)` at `revenuecat.py:284` [FACT revenuecat.py:284]. So `GET /subscription/manage/` (urls.py:138 [FACT urls.py:138]) also writes iff null.
- Why it is (mostly) latent: `User.revenuecat_app_user_id = models.UUIDField(default=uuid.uuid4)` **without** `null=True` [FACT models.py:73]; migration 0008 backfills NULLs [FACT migrations/0008:51-64] and 0009 makes it NOT NULL [FACT migrations/0009:25]. A normal DB row therefore always has an ID at creation and can never become NULL via normal ORM saves. The `if not` branch today only fires for in-memory / `.only()`-omitted instances (the docstring’s stated cases [FACT revenuecat.py:261-266]) or a mock stand-in. [INFERENCE] The defect is real (GET mutates when the branch fires) but production blast radius is near-zero until Phase 2 introduces nullable provider IDs — at which point the write-on-GET would silently mint identities on read. Still worth fixing now (GET must not mutate is a load-bearing API invariant; caches/proxies may also replay GETs). [INFERENCE]
- Interaction with `sync_entitlements` (why the write “helped”): `sync_entitlements` *skips* users with no ID (`if not user.revenuecat_app_user_id: … return False` [FACT revenuecat.py:201-203]), so without the GET write, a null-ID user could never acquire an entitlement. The GET write was the only path that minted the ID for such users (migration 0008 comment: “`sync_entitlements` skips those users, so they can never acquire an entitlement” [FACT migrations/0008:10-11]). [INFERENCE] Removing the write without a minting path on a write op would re-create the “can never acquire” trap for Phase 2’s nullable columns. Plan C05’s “mint ID lazily only on write paths, or tolerate null” [FACT plan:47] covers this.
- Test-shape evidence: `test_sync_no_app_user_id_skipped` uses a stand-in with `revenuecat_app_user_id = None` + mocked `save` [FACT test_revenuecat.py:198,213-217] — proving the null case is only reachable via stand-in today. Status tests (`TestAppUserIdIsSelfScoped` :259–349) use the `user` fixture, which has an ID via model default, and assert stability across 4 GETs (`test_id_is_stable_across_repeated_requests` :316–318) [FACT test_revenuecat.py:316-318] — passing today because the write branch never fires for fixture users. [INFERENCE] A T06 test using the normal fixture would pass *before* the fix too (vacuous) — it must use the stand-in pattern.

**Root cause:** Helper `_app_user_id` conflates “read the identity” with “ensure the identity exists” (read + write in one function name that looks pure). Both GET views call it for a read purpose. The split needs a read-only variant for GETs and a minting variant for write paths (purchase/sync). [INFERENCE]

**Plan C05 assessment:** Plan says “Remove write-on-GET (mint ID lazily only on write paths, or tolerate null) … `_app_user_id` read-only variant for status” [FACT plan:47]. Correct and complete. Note: the *manage* GET (:120) equally needs the read-only variant (it also renders `app_user_id` in the portal URL at :291 [FACT revenuecat.py:291]) — plan’s “status” shorthand must be read as covering both GETs. [INFERENCE]

**Coder touch-set (C05):**
- `backend/app/services/revenuecat.py:246-291` — add `_app_user_id_read(user)` (returns `str(user.revenuecat_app_user_id) or None`, no save) for the two GETs; keep minting `_app_user_id` (or rename to `_ensure_app_user_id`) for `sync_entitlements` / future purchase paths. Do not change `sync_entitlements` skip semantics (:201–203) in this commit. [INFERENCE from plan + code]
- `backend/app/views/subscription.py:65` (status GET) and `:122` via `revenuecat.py:284` (manage GET → `get_customer_portal_url` :278–291) — route both through the read-only variant; decide the null response (omit/`None`) and pin it in the status-shape test (`test_response_keeps_its_documented_shape` :285–290 currently requires `app_user_id` key — the coder must keep the key present with `None` value or update the shape + mobile/web parsers; see `mobile/src/api/schema.ts:377` [FACT grep] and `frontend/src/api/client.ts` consumers). [INFERENCE]
- No migration, no settings, no urls change for C05 (URLs `subscription/` :136 + `subscription/manage/` :138 stay [FACT urls.py:136-138]). [INFERENCE]
- Depends on C01 per plan (:47) only in the sense that lifetime/ID semantics are being touched together; no code dependency between the status-GET read-only split and lifetime handling. [INFERENCE]

**Fail-before test (T06, per plan §H [FACT plan:102]):**
- “`GET /subscription/` (null-ID user) → 200, no DB write” [FACT plan:102]. Implementation must mirror `test_sync_no_app_user_id_skipped`: mock stand-in user with `revenuecat_app_user_id=None` + mocked `save`, authenticated, GET `/subscription/` → 200, assert `save` never called with `revenuecat_app_user_id`, assert `app_user_id` is `None`/absent per the coder’s chosen contract. Today the stand-in path *does* save (revenuecat.py:274), so the test fails before the fix. A fixture-user test would be vacuous (see above) — explicitly forbid it in the task brief. [INFERENCE — proposed, not run]

**Unknowns:**
- [UNKNOWN] Whether any proxy/cache in front of `GET /subscription/` deduplicates or logs writes (grep would show nothing). GET-mutates is the defect regardless of cache behaviour; no runtime check needed. [INFERENCE]

---

## 6. Defect 3.10 — Erasure ignores billing (zero handling)

### Verdict: CONFIRMED — `erasure.py` truly has zero billing handling, and neither the view nor the task compensates.

**“Truly zero” proof (brief’s explicit ask — three layers, all empty):**
- **`backend/app/services/erasure.py` (231 lines, read whole):** module docstring enumerates retained (`ConsentAudit`, `AuditLog`, `Grievance`, `DataSubjectRequest`) vs deleted (`User` cascade, storage objects, Redis keys) [FACT erasure.py:18-52] — billing appears in neither list. Grep for `billing|pro_|revenuecat|subscription|entitlement|paytm|purchase|cancel` over `erasure.py` returns **no matches** [FACT grep erasure.py]. `execute_erasure` (:176–231) reads the user, deletes `profile_picture` (:201–203), purges Redis (:206), anonymises retained records (:211), then `user.delete()` (:222) — no check of `has_pro_entitlement`/`pro_expires_at`, no RevenueCat cancel call, no `Purchase`/`Entitlement` handling (those models do not exist yet — Phase 2, C08 [FACT plan:50]), no warning to the user [FACT erasure.py:176-231]. [INFERENCE] Deleting a Pro user orphans the remote RevenueCat customer/subscription: the store still considers the account an active subscriber to a deleted `revenuecat_app_user_id`, will keep charging (auto-renewals), and the dashboard/REST polling has no local row to reconcile against — the 6h poller (`tasks.py:535-541` candidate filter [FACT tasks.py:535-541]) simply never selects a deleted `pk` again. If the same human re-registers, they get a *new* `revenuecat_app_user_id` (model default) and the old purchase is unreachable.
- **View layer does not compensate:** `DataSubjectErasureView.post` (`backend/app/views/data_subject.py:52-129`) implements only the 30-day cooling-off + `confirm` gate (:57–66, :69–78) then publishes `execute_data_erasure` (:97) [FACT data_subject.py:52-129]. No `is_pro` check, no response field about active subscription, no instruction to cancel via `GET /subscription/manage/` (which exists at :120–123 but is never referenced here) [FACT subscription.py:120-123 vs data_subject.py:99-111 message — message lists “account, clips, comments, shares, interactions and uploaded media” with no billing sentence]. [INFERENCE]
- **Task layer does not compensate:** `execute_data_erasure` (tasks.py:1773–1776) just forwards to `execute_erasure` [FACT grep tasks.py:1773-1776]. No pre-delete billing hook. [INFERENCE]
- **Retention analysis (why this is not just a missing feature):** `execute_erasure` deliberately retains `ConsentAudit` + `DataSubjectRequest` with `user=None` for DPDP/CERT-In evidence [FACT erasure.py:158-173]. Billing records *should* be similarly retained/anonymised (processor obligation to keep payment evidence, store reconciliation, refund/dispute handling per plan §I M10–M11 [FACT plan:117]) — but there is **no billing table to retain**. Today there is only the four User fields, which die with the row. So erasure currently *destroys* the local evidence of the subscription while leaving the remote subscription live — the opposite of the retained-records design. [INFERENCE] Phase 2’s `Purchase`/`Entitlement` must be modelled as retained/anonymised (like `ConsentAudit`), not deleted — flag for C08, not C07.

**Plan C07 assessment:** Plan says “Block/warn account deletion with active store/Paytm subscription; cancel Paytm via API where possible … Pre-delete check + user instruction for store subscriptions (server cannot cancel)” [FACT plan:49]. Correct. Phase 1 scope is warning-text-now, block-later (“warning now, block after Paytm adapter” [FACT 01-work-ledger.md:12]; plan Depends column: “Paytm adapter (later) — warning text now, block later” [FACT plan:49]). [INFERENCE] A server-side cancel for RevenueCat store subscriptions is indeed impossible (Apple/Google require user action in Settings/Play; RevenueCat offers no server cancel that stops auto-renewal — [FACT plan:49] parenthetical). The Phase 1 deliverable is therefore honest copy + a check, not a cancel integration.

**Coder touch-set (C07/T09):**
- `backend/app/services/erasure.py::execute_erasure` (:176–231) — capture billing state *before* `user.delete()` (:222): `has_pro_entitlement`, `pro_expires_at`, `pro_grace_until`, `is_pro()` result. Phase 1 may only *report* it (add to return dict, e.g., `report["active_subscription_at_erasure"]=bool`); blocking must happen in the *view* (before `publish`), not inside the task, because the task must stay idempotent/redeliverable (“If the user is already gone it is a no-op” [FACT erasure.py:55-57,191-194]). [INFERENCE]
- `backend/app/views/data_subject.py::DataSubjectErasureView.post` (:57–129) — pre-delete check: if `user.is_pro()` (or active entitlement), either (Phase 1) extend the 200/`in_progress` response message with cancel instructions + a dedicated field (e.g., `{"warning": "You have an active subscription. Cancel it via Settings → Subscriptions… and via /subscription/manage/. Erasure does not cancel it."}`) or (later, post-Paytm-adapter) return 409 until `is_pro()` is false. The coder must read the client contract: mobile/web settings screens show the erasure copy — a shape change needs client coordination (plan §F: frontend untouched via shim [FACT plan:76] does **not** cover erasure copy). [INFERENCE]
- `backend/app/tests/test_erasure.py` — no billing test exists today (grep billing terms → no matches [FACT grep test_erasure.py]), so the coder adds the first one (see below). [INFERENCE]
- Out of scope for Phase 1: remote cancel API, `Purchase` retention model (C08), Paytm cancel (C13). [FACT plan:49-55]

**Fail-before test (T09, per plan §H [FACT plan:105]):**
- “delete account with active subscription → blocked or cancelled + instructed” [FACT plan:105]. Phase 1 test: Pro user (`has_pro_entitlement=True`, `pro_expires_at` future) → `POST /data-subject/erasure/` with `confirm=true` past cooling-off → today returns plain `in_progress` with no subscription mention [INFERENCE from data_subject.py:99-111]; after fix, assert response carries the warning/cancel instruction (or 409 if the coder implements the block — plan allows either: “blocked or cancelled + instructed” [FACT plan:105]). Must also assert the non-Pro path is unchanged (no warning). Also assert `execute_erasure`’s report records the billing snapshot even though the row is gone (e.g., capture via the returned dict, since post-delete DB cannot be read). [INFERENCE — proposed, not run]

**Unknowns:**
- [UNKNOWN] Legal retention period for billing/payment evidence (tax, consumer-protection, store obligations) — this is plan D4 (“Tax/legal … Gate pilot code until sign-off” [FACT plan:86]) and C07 touches the same surface. Do not hardcode a retention period in the C07 commit; flag for legal sign-off. [INFERENCE]
- [UNKNOWN] Whether RevenueCat’s `revenuecat_app_user_id` is purgable via API after account deletion (it emails/preserves subscriber data for liveness). Needs dashboard/API check; server cannot cancel the store subscription itself regardless. [INFERENCE]

---

## 7. Defect 3.11 — Vendor names persist in product config

### Verdict: CONFIRMED — broader than just the four free-limit vars; C03 rename scope must cover the whole `REVENUECAT_*` surface (env/config now, DB/URL/module later).

**Path:line evidence — the full vendor-named surface in backend code:**
- `backend/EchoFlow/settings.py:1190-1208` (the block brief cites): `REVENUECAT_SECRET_KEY` (:1194) [FACT], `REVENUECAT_PUBLIC_KEY` (:1195) [FACT], `REVENUECAT_PROJECT_TOKEN` (:1196) [FACT], `REVENUECAT_ENTITLEMENT_ID` (:1200, default `echoflow_pro`) [FACT], `REVENUECAT_SYNC_INTERVAL_MINUTES` (:1201) [FACT], `REVENUECAT_CUSTOMER_PORTAL_URL` (:1202) [FACT], `REVENUECAT_DAILY_UPLOAD_LIMIT_FREE` (:1205) [FACT], `REVENUECAT_UPLOAD_MAX_SIZE_MB_FREE` (:1206) [FACT], `REVENUECAT_CLIP_DURATION_LIMIT_FREE` (:1207) [FACT], `REVENUECAT_HD_QUALITY_BLOCKED_FREE` (:1208) [FACT]. Ten vendor-prefixed settings in product config. [INFERENCE] C03 renames the four `*_FREE` vars to `BILLING_*_FREE` [FACT plan:45] — the other six are also vendor-named but belong to the Phase 6 retire (C15: “Remove … `REVENUECAT_*`” [FACT plan:57]), not C03. The coder must not conflate them: C03 is the four limits; the rest stay until the shim/adapter work completes. [INFERENCE]
- **Orphan:** `REVENUECAT_WEBHOOK_SECRET` is **not** in that block (see §2) yet is read in code and listed in `.env.example:261` / `.env.vps.example:185` [FACT grep]. It is both vendor-named *and* settings-undefined — the C02 fix must define it under a neutral name or document the vendor name as interim. Future name per plan §K? Paytm checksum secret is backend-only (B3 [FACT plan:121]); the RevenueCat webhook secret will be deleted at C15. [INFERENCE]
- **Readers of the four C03 vars (grep, complete):** `serializers.py:393` (size) [FACT], `content.py:206` (daily count) [FACT], `subscription.py:31,34,35,36` (advertisement) [FACT], plus `settings.py:1205-1208` definitions and env templates [FACT grep]. No other backend reader. [INFERENCE] Rename is a 3-file + templates change, low blast radius — matches plan risk “med” [FACT plan:45] only because of the compat-fallback decision. [INFERENCE]
- **Vendor-named code beyond settings (do NOT rename in C03 — Phase 6 candidates, for completeness):** DB column `User.revenuecat_app_user_id` [FACT models.py:73]; service module `backend/app/services/revenuecat.py` [FACT]; view `RevenueCatWebhookView` [FACT subscription.py:126]; task `sync_revenuecat_entitlements` [FACT tasks.py:502] + beat key `sync-revenuecat-entitlements` [FACT settings.py:703-707]; URL `webhooks/revenuecat/` [FACT urls.py:139]. Renaming the column needs a migration + backfill (plan C15: “drop … column only after backup verification” [FACT plan:57]); renaming the URL breaks the dashboard webhook URL (must shim ≥2 cycles, C1 [FACT plan:120]). Leave them; record them so a future coder does not mistake “C03 done” for “vendor-free”. [INFERENCE]
- Env templates carrying the vendor names: `.env.example:249-266`, `.env.vps.example:176-190`, `.env.laptop.example:88-98` (laptop notes the four limits are “read by views/content.py, views/subscription.py and serializers.py, all web-only” [FACT .env.laptop.example:96-98]). Hotspot file per 00-mission.md:7 (`.env.*.example` single-writer) [FACT 00-mission.md:7] — coordinate with any sibling touching env files. [INFERENCE]
- AGENTS.md itself documents the vendor names as product config (tables at :438–448, :576–580, :672–675) [FACT grep]. Docs update is Phase 6 (plan §F: “update/retire at Phase 6” [FACT plan:77]); do not bulk-rename docs in the C03 commit — it would churn every diff. [INFERENCE]

**Root cause:** RevenueCat was adopted as the billing layer and its prefix was used for all billing config, including generic free-tier limits that have nothing RevenueCat-specific about them. Removing the vendor means decoupling the limits from the provider name. [INFERENCE]

**Plan C03 assessment:** “Rename `REVENUECAT_*_FREE` → `BILLING_*_FREE` with compat env fallback” [FACT plan:45]. Correct, and the compat fallback is load-bearing: existing deployments set `REVENUECAT_DAILY_UPLOAD_LIMIT_FREE` etc. in `.env`; a hard rename would silently revert to defaults (5/10/60/True) on upgrade. The fallback pattern `os.environ.get('BILLING_DAILY_UPLOAD_LIMIT_FREE', os.environ.get('REVENUECAT_DAILY_UPLOAD_LIMIT_FREE', '5'))` preserves them. [INFERENCE] The plan’s “depends on C01” [FACT plan:45] has no code-level dependency (limits rename does not need lifetime handling) — it is sequencing only. [INFERENCE]

**Coder touch-set (C03 rename half):**
- `backend/EchoFlow/settings.py:1205-1208` — add `BILLING_DAILY_UPLOAD_LIMIT_FREE`, `BILLING_UPLOAD_MAX_SIZE_MB_FREE`, `BILLING_CLIP_DURATION_LIMIT_FREE`, `BILLING_HD_QUALITY_BLOCKED_FREE` with `REVENUECAT_*` fallback; keep old names as deprecated aliases for ≥2 cycles (shim discipline, plan C1 [FACT plan:120]).
- `backend/app/serializers.py:393`, `backend/app/views/content.py:206`, `backend/app/views/subscription.py:31-36` — switch readers to `BILLING_*` (defensively `getattr(settings, "BILLING_...", getattr(... "REVENUECAT_..."))` or a small helper — do not leave two independent `getattr` chains that drift). [INFERENCE]
- `.env.example`, `.env.vps.example`, `.env.laptop.example` — add `BILLING_*` lines, keep `REVENUECAT_*` as commented “legacy, fallback still honoured”. Coordinate via the single-writer hotspot. [FACT 00-mission.md:7]
- Explicitly *not* in C03: `models.py:73` column, `tasks.py:502` task name, `urls.py:139` path, `services/revenuecat.py` filename. [INFERENCE]

**Fail-before test (T03/T04 companion / §H):** No dedicated rename-fail test in plan §H beyond T03/T04 (the behavioural half). Propose a config test: set only `REVENUECAT_DAILY_UPLOAD_LIMIT_FREE=7` (no `BILLING_*`) → status advertises `daily_uploads_remaining` against 7 and upload enforces 7 (compat preserved); then set `BILLING_DAILY_UPLOAD_LIMIT_FREE=3` → 3 wins. Today the `BILLING_*` name does not exist, so the second half fails before the fix. [INFERENCE — proposed, not run]

**Unknowns:** None material. [INFERENCE]

---

## 8. Consolidated touch-sets, disambiguations, and cross-defect interactions

### 8.1 Coder touch-sets at a glance (Phase 1, C02/C03/C05/C07 only)

| Plan ID | Files (backend only) | Change | Test |
|---|---|---|---|
| C02 (3.3) | `views/subscription.py:149-168`, `EchoFlow/settings.py:1190-1208` (add secret), `tests/test_revenuecat.py:601-634` | Reject unsigned when secret configured (401); keep 200-no-op only when unconfigured | T02 (§2) |
| C03 (3.6+3.11) | `serializers.py:386-398` (+ :459-485 threading), `tasks.py:439-446` (if enforce), `views/subscription.py:21-37`, `settings.py:1205-1208`, `content.py:206`, env templates | Enforce *or* unadvertise 60s/HD (operator decision); rename `REVENUECAT_*_FREE`→`BILLING_*_FREE` + fallback | T03/T04 (§4) |
| C05 (3.7) | `services/revenuecat.py:246-291`, `views/subscription.py:65` + manage path via `revenuecat.py:284,291` | Read-only `_app_user_id` variant for both GETs; mint only on write paths | T06 (§5) |
| C07 (3.10) | `services/erasure.py:176-231` (snapshot), `views/data_subject.py:52-129` (check + copy), `tests/test_erasure.py` (new) | Pre-delete `is_pro` check; Phase 1 = warn + cancel instructions; block later | T09 (§6) |

[INFERENCE — summary; detail in §§2–7]

### 8.2 Interactions the plan does not state explicitly (disambiguations)

1. **C02 needs a settings definition or it is a test-only fix (§2).** The plan’s “gate on secret presence” presumes the secret can be present; `settings.py` never defines it, so presence is permanently false in prod. Coder: add the read in the same commit as the condition change. [INFERENCE]
2. **C05 must cover *both* GETs, not just status.** `SubscriptionManageView.get` (:120–123) writes via `get_customer_portal_url` → `_app_user_id` (:284). Plan’s “read-only variant for status” [FACT plan:47] read literally would leave the manage GET mutating. [INFERENCE]
3. **T06 cannot use the `user` fixture.** With `default=uuid.uuid4` + NOT NULL, the fixture user never triggers the write branch; the test would be vacuously green before and after. It must use a None-ID stand-in like `test_sync_no_app_user_id_skipped` [FACT test_revenuecat.py:198-217]. [INFERENCE]
4. **C03 direction changes who breaks.** Enforce = 61–300s free uploads flip 201→400 (breaking); unadvertise = web/mobile copy must change (frontend *is* touched). The plan routes frontend as “no change (shim)” [FACT plan:76] for Phase 1 generally, but C03-unadvertise contradicts that for the Upload/AccountPanel copy. Flag for operator decision before dispatching the coder. [INFERENCE]
5. **C07 warning needs a client contract decision.** Adding a response field/message in `data_subject.py:99-111` changes the shape mobile/web settings rely on. Plan shim D8 (“Keep `/subscription/*` shim” [FACT plan:90]) does not cover `/data-subject/*`. The coder should add fields additively (keep existing keys) and confirm readers tolerate them. [INFERENCE]

### 8.3 What is *not* in Phase 1 (do not dispatch)

- Webhook event processing / `WebhookEvent` model (C08+), Google/Apple adapters (C10/C11), backfill (C12), Paytm (C13/C14), RevenueCat retire (C15) [FACT plan:50-57]. In particular, do not implement 3.4 processing inside C02, do not rename the DB column or the webhook URL inside C03, and do not attempt remote billing cancel inside C07. [INFERENCE]
- Lifetime handling (3.1/C01) is INV-BACKEND-A’s scope per `01-work-ledger.md:17` [FACT 01-work-ledger.md:17]. No overlap with this report’s six defects except shared files (`models.py:is_pro`, `revenuecat.py`). Coordinate on `revenuecat.py` to avoid merge conflict between INV-BACKEND-A (3.1/3.2/3.5) and INV-BACKEND-B (3.7) touch-sets in the same module. [INFERENCE]

### 8.4 Baseline honesty

- Backend suite NOT run (no Docker; brief forbids it; `00-mission.md:6` records the constraint) [FACT 00-mission.md:6]. No test, below, is claimed green. All “fails before / passes after” statements are code-reasoned predictions for the coder to execute on a Docker host with `TEST_DB_NAME` + `TEST_REDIS_CACHE_DB` isolation (plan §H baseline note [FACT plan:113]). A coder must also confirm `docker ps` health before trusting any result (AGENTS.md session learning: corrupt-AOF made cache asserts red [FACT AGENTS.md:753-755]). [INFERENCE]
- Line numbers drift quickly in this tree (e.g., duration probe moved since the frontend audit’s :338 citation [FACT RECON-05:265] to :460 today). Coders must grep for symbols, not trust copied line numbers. [INFERENCE]

### 8.5 Deliberate non-severity inflation

- 3.3 today is fail-open into a no-op (inert), not fail-open into a grant — severity is “landmine for Phase 2” (plan pre-mortem (4) [FACT plan:146]; RECON-05 R5-23 “Live-shaped, inert” [FACT grep RECON-05:41]). The urgency is preventing the pattern’s copy to Paytm (plan §I B2 [FACT plan:121]), not an active exploit today. [INFERENCE]
- 3.7 today is a latent write (production rows cannot be NULL), not a hot-path mutation on every GET. Severity rises when Phase 2 adds nullable provider columns. [INFERENCE]
- 3.6’s “worse half is the advertisement” (RECON-05:265) is agreed: a free user is *promised* 60s and given 300s (over-delivery, not under-delivery). The harm is revenue leakage + copy dishonesty, not user-facing rejection. [INFERENCE]

---

## 9. Per-defect verdict summary (copy-paste for orchestrator)

| # | Defect | Verdict | Key evidence | Plan item | Verdict on plan |
|---|---|---|---|---|---|
| 3.3 | Webhook fail-open | CONFIRMED | subscription.py:156 `and signature`; settings.py has no WEBHOOK def; test :604 pins 200 | C02/T02 | Correct, add settings definition |
| 3.4 | Webhook no-op | CONFIRMED | subscription.py:149-168 (log+200 only); docstring future-tense :131-145 | none in Phase 1 | Correct to leave as no-op; fix in C10/C11/C13 |
| 3.6 | Duration/HD unenforced | CONFIRMED | serializers.py:460 (300s all tiers), :386-398 (size only); tasks.py:439-446 (128k fixed, no branch); subscription.py:34,36 (advertise only) | C03/T03-T04 | Correct; direction (enforce/unadvertise) needs operator call |
| 3.7 | Write-on-GET | CONFIRMED (narrow/latent) | revenuecat.py:272-275 (save); callers subscription.py:65 + :122 via :284; models.py:73 + migrations 0008/0009 (NOT NULL) | C05/T06 | Correct; cover both GETs; T06 needs null stand-in |
| 3.10 | Erasure ignores billing | CONFIRMED (zero) | erasure.py grep no-match + :176-231 no check; data_subject.py:52-129 no check; test_erasure.py no-match | C07/T09 | Correct (warn now, block later) |
| 3.11 | Vendor names | CONFIRMED | settings.py:1194-1208 (10 vars); models.py:73; urls.py:139; tasks.py:502; env templates | C03 rename | Correct for 4 `*_FREE` vars; rest to C15 |

---

## 10. References (all read)

- Plan: `C:\Users\devan\.opencode\plan\2026-11-07-revenuecat-paytm-billing.md` §C, §E, §F, §H, §I.
- Mission: `docs/agent-ops/20261107-revenuecat-paytm/00-mission.md`, `01-work-ledger.md`, `COMMS.md`.
- Code: `backend/app/views/subscription.py`, `backend/app/services/revenuecat.py`, `backend/app/serializers.py`, `backend/app/views/content.py`, `backend/app/services/erasure.py`, `backend/app/views/data_subject.py`, `backend/app/urls.py`, `backend/app/models.py`, `backend/app/tasks.py`, `backend/EchoFlow/settings.py`, `backend/app/tests/test_revenuecat.py`, `backend/app/tests/test_erasure.py` (grep only).
- Supporting: `docs/EXPLAIN/decisions/2026-09-29-unenforced-subscription-limits.md`, `docs/frontend/RECON-05-security-abuse.md`, `AGENTS.md` (RevenueCat section + session learnings).
