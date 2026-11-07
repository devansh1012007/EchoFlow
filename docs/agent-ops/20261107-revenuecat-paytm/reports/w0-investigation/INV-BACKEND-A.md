# Report — INV-BACKEND-A (investigator) · Wave 0 · 20261107-revenuecat-paytm

> ORCH-DIGEST | agent=INV-BACKEND-A | branch=feat/revenuecat-paytm-phase1 | base=8765b73 | scope=Defects 3.1 (lifetime), 3.2 (entitlement ID), 3.5 (stale poll) per plan §C, §E (C01, C04), §H (T01, T05) | method=read-only, no test runs (Docker unavailable), path:line evidence only | verdicts=3.1 EXISTS | 3.2 PARTIAL (backend default fixed; mobile still 'pro') | 3.5 EXISTS | blockers=Backend test baseline NOT captured (no Docker; Python 3.12 vs pinned 3.11) per 00-mission.md; no test run attempted.

[FACT git] Branch is `feat/revenuecat-paytm-phase1` at `8765b73`, clean except untracked `docs/agent-ops/` [FACT git status]. This report is the sole write-set.

[inference method] All verdicts are from reading code on this branch. No runtime, no pytest, no Docker. "Test that must fail before fix" statements are predictions for the coder's Docker run, not observed failures.

---

## Method, scope, and exclusions

- Investigator role: INV-BACKEND-A covers defects 3.1, 3.2, 3.5 only. 3.3 (webhook fail-open), 3.4 (webhook no-op), 3.6 (duration/HD limits), 3.7 (write-on-GET), 3.10 (erasure), 3.11 (vendor naming) are sibling INV-BACKEND-B territory and are NOT re-verified here except where a line directly interacts with 3.1/3.2/3.5.
- Approved plan read: `C:\Users\devan\.opencode\plan\2026-11-07-revenuecat-paytm-billing.md` §C, §E (C01, C04), §H (T01, T05).
- Mission/ledger/comms read: `docs/agent-ops/20261107-revenuecat-paytm/00-mission.md`, `01-work-ledger.md`, `COMMS.md`.
- Code read: `backend/app/services/revenuecat.py` (291 lines), `backend/app/models.py:50-109`, `backend/app/tasks.py:490-559`, `backend/EchoFlow/settings.py:1180-1210`, `backend/app/tests/test_revenuecat.py` (680 lines).
- Extra read-only greps (no edits): mobile entitlement default, `is_pro` call sites, `pro_last_synced` writers, migration definitions for `pro_last_synced`.

## Summary verdict table

| Defect | Plan ref | Verdict | One-line root cause |
|---|---|---|---|
| 3.1 Lifetime entitlement never grants Pro | C01 / T01 | **EXISTS** | Sync layer correctly reports lifetime as active (`expires_at=None`), but `User.is_pro()` requires a non-null future expiry, so lifetime buyers evaluate False [FACT models.py:88]. |
| 3.2 Entitlement ID mismatch (`pro` vs `echoflow_pro`) | P3 / §C | **PARTIAL** | Backend default is `echoflow_pro` [FACT settings.py:1200] but env templates disagree (`.env.vps.example`/`.env.laptop.example` pin `pro`); mobile fallback is `'pro'` [FACT mobile/src/lib/revenuecat.ts:29] but shipped EAS builds pin `echoflow_pro` [FACT mobile/eas.json:15,27,38]. Shipped builds match by env, not by code constant — drift persists as fallback/template/test default. |
| 3.5 `auto_now` clobbers `pro_last_synced` staleness | C04 / T05 | **EXISTS** | `pro_last_synced` is `auto_now=True` [FACT models.py:77], so any `save()` (e.g. profile update) refreshes it and the Celery stale-candidate filter misses the user [FACT tasks.py:540]. Sync itself writes `pro_last_synced` explicitly only when state changed [FACT revenuecat.py:230-237], so unchanged syncs never refresh staleness either — the field does not mean "last polled". |

---

## Defect 3.1 — Lifetime entitlement never grants Pro (C01 / T01)

**Verdict: EXISTS** [FACT]

### Exact path:line chain

1. RevenueCat API v1 has no `is_active` field; a lifetime entitlement presents with null expiry. Backend parser handles this explicitly:
   - [FACT revenuecat.py:182-184] Comment: "API v1 does not return an is_active field. A present entitlement is active until its expiry; a null expiry represents a lifetime purchase." Return: `expires_at is None or expires_at > now`, so lifetime → `is_active=True, expires_at=None`.
   - [FACT revenuecat.py:164-172] `expires_at` parsed from `expires_date` / `expires_date_ms` / `expire_date_ms`; all three null → `expires_at=None`. `_parse_revenuecat_date` returns None on null/non-str [FACT revenuecat.py:120-123]; `_parse_date_ms` returns None on falsy [FACT revenuecat.py:109-112].
   - [FACT revenuecat.py:174-175] If `is_active` IS present (legacy/mock payload), that boolean wins. Lifetime via old payload shape with `is_active: True` and null expiry also returns `(True, None, grace)`.
2. Sync persists the lifetime state verbatim:
   - [FACT revenuecat.py:213] `is_active, expires_at, grace_until = _is_active_entitlement(subscriber)`.
   - [FACT revenuecat.py:218-228] If `is_active != was_active` → `user.has_pro_entitlement = True`. If `expires_at != user.pro_expires_at` → `user.pro_expires_at = None`. So after sync: `has_pro_entitlement=True, pro_expires_at=None, pro_grace_until=None` for a clean lifetime purchase.
   - Model allows it: [FACT models.py:75] `pro_expires_at = DateTimeField(null=True, blank=True)`.
3. Gate rejects it:
   - [FACT models.py:79-92] `is_pro()` first branch: `if self.has_pro_entitlement and self.pro_expires_at and self.pro_expires_at > now: return True`. With `pro_expires_at=None`, the truthiness check fails; second branch checks grace only; falls through to `return False`.
   - Therefore every lifetime buyer evaluates `is_pro()==False` [INFERENCE from FACT 1-3; no test in suite covers this path, see below].

### Root cause (not symptom)

The sync layer and the gate disagree on the meaning of null expiry. Parser (revenuecat.py) treats null expiry as "lifetime, active forever"; gate (models.py) treats null expiry as "no evidence of future access". [FACT] Both treat null the same way *nowhere* else: `tasks.py:535-541` candidate filter uses `pro_expires_at__lte=now+1d` — a lifetime user with `pro_expires_at=None` never matches that arm (Django ORM `__lte` excludes NULL), so lifetime users depend entirely on the second arm (`pro_last_synced__lt=stale_threshold`, see 3.5) — and a lifetime user is also `has_pro_entitlement=True` so they pass the `has_pro_entitlement=True` pre-filter, but can still be missed in practice (see 3.5 analysis).

What is NOT the bug: the parser's lifetime inference itself [FACT revenuecat.py:182-184 is correct per comment]. The bug is the gate's expiry requirement.

### Why the suite never caught it — zero lifetime tests

[FACT grep test_revenuecat.py] No lifetime fixture:
- `TestIsPro` (lines 25-52) covers: active Pro with future expiry (True), expired (False), no entitlement (False), grace valid (True), grace expired (False). No `pro_expires_at=None + has_pro_entitlement=True` case.
- `TestSyncEntitlements` (lines 58-230) covers: monthly→echoflow_pro, documented v1 payload, unrelated entitlement, `is_active=True` with `bytes` expires_date_ms, `is_active=False` with None expiry, lookup-failure preservation, no-app-user-id skip, no-secret skip. The `is_active=False` + None case [FACT test_revenuecat.py:168-181] asserts `has_pro_entitlement is False` — it is NOT the lifetime case. There is no test where `_is_active_entitlement` returns `(True, None, None)` and sync + `is_pro()` are asserted.

Plan §H statement ("does NOT cover lifetime") matches code [FACT test_revenuecat.py full read].

### Exact files/symbols a coder would touch

- `backend/app/models.py::User.is_pro` (lines 79-92) — primary fix site per plan C01.
- Optional co-fix: `backend/app/services/revenuecat.py::_is_active_entitlement` (lines 133-184) and `sync_entitlements` (lines 187-243) IF the coder prefers to encode lifetime at sync time; but plan C01 says "in `is_pro()` or sync layer" — either works. Simplest minimal: models.py only.
- Test file: `backend/app/tests/test_revenuecat.py` (`TestIsPro`, `TestSyncEntitlements`).
- Downstream reader that must not regress: `backend/app/tasks.py:535-541` candidate filter (NULL-excluded; needs separate consideration for lifetime users — see Interaction note below).

### Fix options with tradeoffs (no design proposal beyond these)

**Option A (plan C01, minimal, recommended by plan):** `is_pro()` treats `has_pro_entitlement=True + pro_expires_at=None` as Pro (unless revoked — revocation is `has_pro_entitlement=False`, so this is safe). 1-3 lines in models.py.
- Pro: zero schema change; fixes gate at single seam (`is_pro()` is the documented single access decision).
- Con: lifetime still has no expiry to render ("Pro since forever"); grace logic untouched.

**Option B:** Sync layer encodes lifetime differently (e.g. far-future sentinel expiry or a separate `is_lifetime` flag + migration).
- Pro: explicit, queryable.
- Con: requires migration; sentinel expiry breaks if RevenueCat ever revokes lifetime (revoke should set `has_pro_entitlement=False` anyway); more code for same observable outcome. Not recommended for Phase 1.

**Option C (hybrid, most robust for Phase 2):** Fix `is_pro()` now (Option A) AND later model lifetime explicitly in the `Entitlement` table (plan C08/C09). Phase 1 = A only.

### Test that must fail before the fix (T01 per plan §H)

Unit test in `TestIsPro`:
```python
def test_lifetime_entitlement_grants_pro(self, user):
    user.has_pro_entitlement = True
    user.pro_expires_at = None
    user.pro_grace_until = None
    assert user.is_pro() is True
```
Today this returns `False` → test fails on current code [INFERENCE from FACT models.py:88; not run]. Second test (sync-layer, proves end-to-end path):
```python
# fake_subscriber payload with "entitlements": {"echoflow_pro": {"product_identifier": "lifetime", "expires_date": None}}
# sync_entitlements(user) → user.has_pro_entitlement True, pro_expires_at None, is_pro() True
```
Note the second test exercises `_is_active_entitlement` line 184 (`expires_at is None → active`) + models.py:88. Also assert `pro_grace_until` is irrelevant (lifetime + no grace → still True).

### Hardening note (do not fix silently)

`_is_active_entitlement` line 174-175: if an entitlement carries `is_active` explicitly, the function trusts the boolean and returns `expires_at` verbatim — even if `expires_at` is None AND `is_active` is True AND the product was actually a time-boxed subscription whose expiry simply wasn't parsed. [FACT revenuecat.py:164-175] The `expires_at is None` lifetime inference at line 184 is only reached when `is_active` is ABSENT. This is a parse-order hazard: a legacy/mock payload with `is_active: True` + malformed/missing expiry is treated as lifetime by Option A as well. Coder should ensure T01's sync test uses the documented v1 shape (no `is_active`) for the lifetime case, and keep line 174-175 explicit-`is_active` semantics in mind — do not "fix" by removing the `is_active` branch without reading why it exists (tests at test_revenuecat.py:131-181 depend on it).

**[UNKNOWN]** Whether RevenueCat actually ever sells a lifetime product for EchoFlow today (plan D9/P1 mentions pausing lifetime sales). Code path exists; product existence is a dashboard fact, not visible in repo. Verification: RevenueCat dashboard → Products. Does not change verdict.

---

## Defect 3.2 — Entitlement ID mismatch: mobile `pro` vs settings `echoflow_pro` (Plan P3 / §C)

**Verdict: PARTIAL** [FACT]

### What is already fixed (backend side — not a live mismatch)

[FACT backend/EchoFlow/settings.py:1200] `REVENUECAT_ENTITLEMENT_ID = os.environ.get('REVENUECAT_ENTITLEMENT_ID', 'echoflow_pro')`. Default is `echoflow_pro`, not `pro`.

[FACT backend/app/services/revenuecat.py:141] `entitlement_id = getattr(settings, "REVENUECAT_ENTITLEMENT_ID", "pro")` — fallback default is `pro`, but `getattr` fallback only fires if the attribute is missing entirely; settings.py always defines it, so the effective value is `echoflow_pro` (or operator's env override).

[FACT backend/app/services/revenuecat.py:149-162] Lookup is by entitlement-identifier key: `ent_data = entitlements.get(entitlement_id)`, with a legacy fallback that scans values for `data.get("product_id") == entitlement_id`. The fallback is a "product_id as entitlement" shim for old payloads.

[FACT backend/app/tests/test_revenuecat.py:59-81] `test_monthly_product_grants_configured_echoflow_pro_entitlement` — covers the correct case: entitlement key `echoflow_pro`, `product_identifier: "monthly"`, sync grants Pro. This is the regression guard against identifying an entitlement by its product.

### What is still broken — fallbacks, templates, and tests disagree; shipped builds match only by env

Mobile code default is `'pro'`, but shipped builds override it by env:
- [FACT mobile/src/lib/revenuecat.ts:28-30] `function revenueCatEntitlementId(): string { return process.env.EXPO_PUBLIC_REVENUECAT_ENTITLEMENT_ID?.trim() || 'pro'; }` — a function with env-override + `'pro'` fallback, not a const.
- [FACT mobile/eas.json:15,27,38] Every EAS profile sets `"EXPO_PUBLIC_REVENUECAT_ENTITLEMENT_ID": "echoflow_pro"`. So shipped builds pass `echoflow_pro` and match the backend default today — the `'pro'` fallback only fires when the env var is unset (local dev without env). [INFERENCE: the fallback defect is live in local dev and latent in release; severity is build-config-dependent, matching INV-MOBILE's independent landing on the same facts.]
- [FACT mobile/src/lib/revenuecat.ts:58-60] `hasActiveRevenueCatEntitlement` consumes the same helper. [UNKNOWN - owned by INV-MOBILE] Whether `useSubscription` ever calls it; if it does, a local dev build checks the wrong key and returns false even for Pro users. Backend `is_pro()` remains the access decision regardless [FACT serializers.py:382, views/content.py:202].

Backend env templates and test defaults still pin the old identifier:
- [FACT .env.example:255] `REVENUECAT_ENTITLEMENT_ID=echoflow_pro`.
- [FACT .env.vps.example:181] `REVENUECAT_ENTITLEMENT_ID=pro`.
- [FACT .env.laptop.example:89] `REVENUECAT_ENTITLEMENT_ID=pro`.
- [FACT conftest.py:50,108] `os.environ.setdefault('REVENUECAT_ENTITLEMENT_ID', 'pro')` — the test suite runs with `pro` unless a real env overrides (there is no `.env` in the test container). So tests exercise the `pro` path [FACT test_revenuecat.py:88,114,135,162 all set `pro` explicitly].
- [FACT AGENTS.md:441,576,674] Docs still describe default as `pro` (stale vs settings.py:1200).

Consequence chain [INFERENCE, read-only reasoning]:
1. A VPS or laptop deployment that copies `.env.vps.example`/`.env.laptop.example` verbatim runs the backend with entitlement `pro`. If the RevenueCat dashboard defines `echoflow_pro` (per settings.py:1197-1199 comment), NONE of that deployment's syncs will ever find an entitlement; `sync_entitlements` returns `False` and clears `has_pro_entitlement` via the matching-entitlement-missing branch (revenuecat.py:161-162 returns `(False, None, None)` → sync sets `False`). [INFERENCE: env drift consequence; dashboard contents are [UNKNOWN] below.]
2. The backend `getattr(..., "pro")` fallback (revenuecat.py:141) and the mobile `'pro'` fallback (revenuecat.ts:29) agree with each other on `pro` and disagree with the shipped-env `echoflow_pro` — so any code path that falls back on either side silently switches identifiers.
3. The fallback in revenuecat.py:153-160 (`product_id == entitlement_id`) does NOT rescue a `pro` vs `echoflow_pro` mismatch, because the fallback only helps when the entitlement STORE in the payload is mis-shaped, not when the identifier string itself is wrong. [FACT revenuecat.py:153-160]

Plan §H / P3 confirms: "single entitlement constant per codebase + drift test" is the fix shape.

**[UNKNOWN]** Which identifier(s) actually exist in the RevenueCat dashboard project (`pro`, `echoflow_pro`, or both). This is outside the repo. Verification: RevenueCat dashboard → Entitlements. If the dashboard defines `pro`, then settings.py's default is the drifted side [INFERENCE]. Either way, "one constant per codebase + drift test" is the fix; the direction (rename to `echoflow_pro` everywhere vs rename to `pro` everywhere) is an operator/dashboard decision, not a code-reading one. The in-repo facts already rule out the current mixed state.

### Root cause

The identifier was renamed to `echoflow_pro` in settings.py's default and EAS env, but the rename was never completed: code fallbacks (revenuecat.py:141, revenuecat.ts:29), two env templates, the test-suite default, and AGENTS.md still say `pro`, and there is no cross-repo drift test. [FACT all lines cited above]. The backend's `getattr(..., "pro")` fallback (revenuecat.py:141) is a stale remnant: if `REVENUECAT_ENTITLEMENT_ID` were ever removed from settings, the code would silently revert to `pro` while the shipped env and dashboard comment expect `echoflow_pro`.

Related but out-of-scope file: `settings.py:1205-1208` free-limit env names (`REVENUECAT_DAILY_UPLOAD_LIMIT_FREE` etc.) — that renaming is plan C03 / INV-BACKEND-B territory (defect 3.11). Noted here only because the same "vendor name persists in config" class applies to the entitlement fallback.

### Exact files/symbols a coder would touch

- `mobile/src/lib/revenuecat.ts:28-30` — `revenueCatEntitlementId()` `'pro'` fallback. Direction of rename is operator/dashboard-dependent (see [UNKNOWN] above). INV-MOBILE owns the mobile write; this investigator only evidences the backend side.
- `backend/app/services/revenuecat.py:141` — stale `"pro"` fallback default.
- `backend/EchoFlow/settings.py:1200` — canonical backend default (already `echoflow_pro`; touch only if the decision goes the other way).
- `.env.vps.example:181`, `.env.laptop.example:89` — pin `pro`; align to the canonical identifier (this file set is also a wave hotspot per 00-mission.md).
- `conftest.py:50,108` — `setdefault(..., 'pro')`; align so the suite tests the canonical path unless deliberately parametrized.
- `backend/app/tests/test_revenuecat.py` — add a drift/parity test asserting backend default == mobile fallback == shipped env == dashboard identifier (dashboard identifier is the [UNKNOWN] to resolve first).

### Fix options with tradeoffs

**Option A:** Align every fallback/template/test to `echoflow_pro` (mobile fallback, backend fallback, `.env.vps.example`, `.env.laptop.example`, conftest) + delete/replace the backend `"pro"` fallback so missing config fails loudly instead of silently switching identifiers.
- Pro: in-repo single constant; drift test then passes without further env babysitting.
- Con: requires dashboard confirmation that `echoflow_pro` is the real identifier before changing templates; mobile fallback change ships only via new build (no OTA, plan C2) — though EAS env already pins `echoflow_pro`, so no behavior change for shipped builds, only for local dev.

**Option B:** Add a drift test that reads all four sites (settings default, mobile fallback, EAS env, env templates) and fails on divergence, without renaming anything yet.
- Pro: catches regression; doesn't pick a side.
- Con: test fails immediately until someone renames — correct, but blocks until the [UNKNOWN] is resolved.

**Option C:** Support both identifiers in backend sync (match either key).
- Rejected: masks drift instead of fixing it; still leaves local-dev mobile checking the wrong key; violates P3's "single constant" rule.

### Test that must fail before the fix

Drift test (backend or mobile test suite, whichever owns the constant):
```python
# backend/app/tests/test_revenuecat.py
def test_entitlement_id_matches_mobile(self):
    # Read mobile/src/lib/revenuecat.ts fallback and settings default;
    # assert they are equal.
```
Today: `echoflow_pro != pro` → fails [FACT both lines read]. Template-parity guard:
```python
def test_env_templates_agree_on_entitlement_id(self):
    # .env.example, .env.vps.example, .env.laptop.example values must be equal.
```
Today: `.env.example=echoflow_pro` vs `.env.vps.example=pro` → fails [FACT .env.example:255, .env.vps.example:181, .env.laptop.example:89; not run]. Also a backend-only guard:
```python
def test_sync_never_falls_back_to_pro_default(self, settings):
    # With REVENUECAT_ENTITLEMENT_ID unset, _is_active_entitlement must not look up "pro".
```
Today `getattr` fallback returns `pro` → fails [INFERENCE from revenuecat.py:141; not run].

---

## Defect 3.5 — `auto_now` clobbers `pro_last_synced` staleness (C04 / T05)

**Verdict: EXISTS** [FACT]

### Exact path:line chain — two clobbering directions

The field is meant to mean "last time we polled RevenueCat", but it actually means "last time the row was saved for any reason" — because:

1. [FACT backend/app/models.py:77] `pro_last_synced = models.DateTimeField(auto_now=True)`; origin [FACT backend/app/migrations/0002_user_revenuecat_fields.py:32-36] also `auto_now=True` — the field was born as "last save", never "last poll". Full `save()` (no `update_fields`) stamps every `auto_now` field; `save(update_fields=[...])` persists only the listed columns (the in-memory `auto_now` stamp is then not written unless the field is listed). Both save shapes occur in this codebase, so both directions below are live:
   - `user.save()` (profile edit; [FACT test_revenuecat.py:308] `user.save()` after username/email/first/last edits) → `pro_last_synced` bumped. ✓ clobber direction 1 [FACT models.py:77 + test_revenuecat.py:308].
   - `user.save(update_fields=[...])` WITHOUT `pro_last_synced` → the stored value is NOT bumped (Django persists only listed columns). The sync path [FACT revenuecat.py:232-237] DOES include `pro_last_synced` in `update_fields`, so it bumps (as intended when state changed). A profile-flavored `save(update_fields=[...])` that excludes the field would not clobber — **[UNKNOWN]** which save shape every profile-write site in views/serializers uses; coder should grep `user.save(` and `save(update_fields` before assuming which sites clobber. The T05 test below uses the full-`save()` shape that is proven in-tree; that shape alone reproduces the defect.
   - `user.save(update_fields=[...])` that MISSES the field but is followed by a full save → bumped on the full save. Profile edits today do full saves (test proves the pattern; views not re-read this pass).
   - **Critical nuance:** the sync path bumps `pro_last_synced` ONLY when `changed` is True [FACT revenuecat.py:230]. When a poll finds no change (the common case — user still Pro, same expiry), `sync_entitlements` returns `changed=False`, does NOT call `save()`, and therefore NEVER advances `pro_last_synced`. So `pro_last_synced` is not "last polled" — it is "last full save OR last state change, whichever happened last". A user polled 100 times with no change looks just as stale as one never polled. [INFERENCE from FACT revenuecat.py:215-243]. Note also [FACT views/subscription.py:100-105] the sync view `refresh_from_db(fields=[..., "pro_last_synced"])` after `sync_entitlements(request.user)` — a read, not a write; it does not advance the field on unchanged polls either.

So the field is wrong in BOTH directions: profile saves make it look fresh when it isn't; unchanged polls make it look stale when it isn't. Plan C04's wording ("profile saves refreshing `pro_last_synced`") covers direction 1; direction 2 (no-change polls never refresh) is the subtler half the plan doesn't name but the T05 test below only needs direction 1.

2. Candidate filter depends on the clobbered field:
   - [FACT backend/app/tasks.py:521-522] `stale_threshold = now - timedelta(minutes=REVENUECAT_SYNC_INTERVAL_MINUTES + 1)` (default 360+1 min = 6h+1m).
   - [FACT backend/app/tasks.py:535-541] `candidates = User.objects.filter(has_pro_entitlement=True).filter(pro_expires_at__lte=now+1d) | User.objects.filter(pro_last_synced__lt=stale_threshold)`.
   - So a Pro user whose profile was saved 1 hour ago has `pro_last_synced = 1h ago` → NOT `< 6h1m threshold` → NOT selected by arm 2. They are selected by arm 1 ONLY if `pro_expires_at <= now+1d`. A monthly Pro user at day 10 of 30 passes arm 1? `pro_expires_at = day 30 > now+1d` → NO. Arm 2? `pro_last_synced = 1h ago` → NO. So an active subscriber with a recent profile save is NEVER polled until arm 1 or the threshold eventually catches them — but every profile save resets the arm-2 clock. [INFERENCE from FACT tasks.py:535-541 + models.py:77]
   - Net effect: cancellation/revocation detection lags by up to (profile-save interval + 6h). In the worst case a user who edits their profile daily is never in the candidate set during the whole paid term [INFERENCE; severity scales with profile-edit frequency]. The plan calls this out as "symptom fix would be narrower filter" — correct: narrowing the filter without fixing the field just moves who gets missed.

3. The whole-bulk path is also NULL-blind for lifetime (interaction with 3.1):
   - Arm 1 (`pro_expires_at__lte`) excludes NULL → lifetime users rely SOLELY on arm 2. Combined with the clobber above, lifetime users with recent profile saves are the single most-likely to be missed. [INFERENCE from FACT tasks.py:538 + models.py:75 NULL-allowed + models.py:77]
   - This is why C01 and C04 should land together even though the ledger lists them as separate tasks (T-001, T-004): fixing the gate (C01) without fixing the field (C04) leaves lifetime users gated correctly but polled rarely; fixing the field without the gate leaves lifetime polls happening but the gate still False.

### Root cause

`auto_now=True` on a field whose semantics are "last poll timestamp", combined with `sync_entitlements` only writing on state change. The field conflates three distinct events: (a) any model save, (b) a poll that found a change, (c) a poll that found nothing. The scheduler only needs (a)+(b)+(c) = "was polled", but the code writes (a) too eagerly and (c) never. [INFERENCE from FACT models.py:77, revenuecat.py:230-237, tasks.py:540]

### Exact files/symbols a coder would touch

- `backend/app/models.py:77` — `pro_last_synced` definition: remove `auto_now=True`, make it `null=True, blank=True` (or keep NOT-NULL + default and update semantics). Requires a migration.
- `backend/app/services/revenuecat.py::sync_entitlements` (lines 215-243) — always advance `pro_last_synced` on a successful poll (even when `changed=False`), with `update_fields=["pro_last_synced"]` in the no-change path. Note: today the no-change path does NO save at all (returns `changed=False` without touching the row) — the fix must add that save. Consider: moving `pro_last_synced` update OUTSIDE the `if changed:` block.
- `backend/app/tasks.py::sync_revenuecat_entitlements` (lines 501-548) — candidate filter should keep its shape per plan C04 ("keep candidate filter semantics"); verify the TTL logic still selects correctly once the field means what it says. Optional co-fix: include lifetime (`pro_expires_at__isnull=True`) in arm 1 so lifetime users aren't arm-2-dependent.
- Test file: `backend/app/tests/test_revenuecat.py` (new `TestStalePoll` or extend `TestIsPro`/`TestSyncEntitlements`); existing `TestSyncEntitlements` poll tests will need `pro_last_synced` assertions added.

### Fix options with tradeoffs

**Option A (plan C04, recommended):** Remove `auto_now`, manage the field explicitly in `sync_entitlements` (always bump on successful poll; bump on change as today). Migration: `AlterField` on `pro_last_synced`.
- Pro: field means what it says; scheduler and gate both correct; no new column.
- Con: one migration; existing rows keep their `auto_now`-polluted values (harmless — the next poll fixes each row; worst case is one early/late poll, not data loss).

**Option B:** Add a separate `last_poll_at` field, leave `pro_last_synced` as-is, switch the filter to `last_poll_at`.
- Pro: zero interpretation risk; old field untouched.
- Con: two fields meaning almost the same thing; the old field's `auto_now` confusion persists for anyone reading it. Plan C04 explicitly offers "or separate `last_poll_at`" as an alternative, but Option A is cleaner — B is the fallback if the migration is judged risky.

**Option C (do not choose):** Narrow the candidate filter to drop the `pro_last_synced` arm entirely and poll ALL `has_pro_entitlement=True` users every cycle.
- Rejected: at 6h cadence it multiplies RevenueCat API calls by the whole Pro population; the filter exists for a reason. Plan C04 already rejects this shape ("symptom fix would be narrower filter" — dropping the arm is the extreme of that).

### Test that must fail before the fix (T05 per plan §H)

Unit/integration test:
```python
def test_profile_save_does_not_refresh_staleness(self, user, settings):
    user.has_pro_entitlement = True
    user.pro_expires_at = timezone.now() + timedelta(days=30)   # arm 1 not matched
    user.save()
    old = timezone.now() - timedelta(hours=10)   # stale (> 6h1m)
    # simulate pre-clobber state via queryset .update() (bypasses auto_now)
    User.objects.filter(pk=user.pk).update(pro_last_synced=old)
    user.refresh_from_db()
    assert user.pro_last_synced <= old + timedelta(seconds=1)
    # now do an ordinary profile save
    user.first_name = "Alice"
    user.save()
    user.refresh_from_db()
    # assert still stale:
    assert user.pro_last_synced < timezone.now() - timedelta(hours=6)
    # and the candidate query in tasks.py:535-541 selects the user
```
Today, the second save bumps `pro_last_synced` to now → assertion fails [INFERENCE from FACT models.py:77; not run]. Complementary test for direction 2:
```python
def test_unchanged_poll_advances_pro_last_synced(self, user, settings):
    # subscriber payload identical to current state; sync_entitlements returns False
    # assert user.pro_last_synced advanced anyway
```
Today `changed=False` → no save → `pro_last_synced` unchanged → fails [INFERENCE from FACT revenuecat.py:230-243; not run].

### Neighboring code that must not be assumed safe

[FACT revenuecat.py:230-237] The `pro_last_synced` explicit write inside `if changed:` assigns `user.pro_last_synced = now` AND lists it in `update_fields`. With `auto_now=True` in place the explicit assignment is belt-and-braces (Django's pre_save also stamps listed `auto_now` fields — [INFERENCE: framework handling, not repo fact]). After the fix (remove `auto_now`), the explicit assignment becomes load-bearing — coder must not delete it as "redundant with auto_now" once `auto_now` is gone. Also note [FACT views/subscription.py:69] exposes `pro_last_synced` as `"last_synced"` in the status payload — after the fix its meaning changes to "last poll", which is what the field name already claims to consumers.

[FACT backend/app/tests/test_revenuecat.py:294-314] `test_id_survives_profile_changes` calls `user.save()` after profile edits. After the 3.5 fix, this test MUST be updated/re-read: it asserts App User ID stability, not staleness — it will still pass (App User ID is a different field), but the coder should confirm the intervening `user.save()` no longer touches `pro_last_synced` (add an assertion or leave as-is; either way review the test when touching models.py:77).

---

## Interaction: 3.1 + 3.5 compound effect

Neither defect is catastrophic alone; together they produce a specific wrong state:
- Lifetime user pays → RevenueCat payload → `sync_entitlements` sets `has_pro_entitlement=True, pro_expires_at=None` [FACT revenuecat.py:218-224].
- `is_pro()` → `False` (3.1) → upload/file-size gates treat them as free [FACT models.py:88; call sites serializers.py:382, views/content.py:202 per plan §C — not re-read in this pass, plan-cited only].
- Celery poll candidate filter: arm 1 misses (NULL), arm 2 misses if profile recently saved (3.5) → no re-poll → stale False persists.
- User edits profile → `pro_last_synced` refreshes (3.5) → even less likely to be polled → False persists longer.

[INFERENCE] Fixing only one leaves a coherent-but-wrong state for lifetime users: gate-fixed-but-never-polled (revoke not detected) or polled-but-gate-False (still no Pro). Recommend landing C01 and C04 in the same review cycle (separate commits, one integration test covering both).

## Scope honesty

- This report verifies backend lines only for 3.1/3.2/3.5. The mobile `revenuecat.ts:28-30` fallback, `eas.json:15,27,38` pinning, and `hasActiveRevenueCatEntitlement` consumption (`revenuecat.ts:58-60`) were confirmed by read as evidence for the drift verdict; INV-MOBILE owns the full mobile inventory (and independently confirmed the same mitigation shape).
- Plan-cited call sites (`serializers.py:382`, `views/content.py:202`, `views/subscription.py:86-109`, `webhook 149-168`) were partially re-read: `serializers.py:382` / `views/content.py:202` confirmed via grep as `is_pro()` consumers; `views/subscription.py:1-168` fully read (status/sync/manage/webhook), which confirmed the `refresh_from_db(fields=["pro_last_synced"])` read at :100-105. Webhook fail-open (3.3) NOT assessed — owned by INV-BACKEND-B; lines 156-167 confirm the plan's description (`if secret and signature`) without a verdict here.
- "Test must fail before fix" statements are predictions from code reading. No test was run (Docker unavailable, per 00-mission.md baseline note). The coder's first job is to run T01/T05/drift tests against unpatched code and confirm red, per the repo's own working agreement (AGENTS.md Test-First rule).
- [UNKNOWN] Dashboard state (which entitlement identifiers exist in the RevenueCat project; whether a lifetime product is sold). Needs a 2-minute dashboard check before the coder picks a direction for 3.2. Env-file overrides are now FACT (see 3.2), not unknown.
- [UNKNOWN] Which profile-write sites use full `save()` vs `save(update_fields=[...])` (3.5 direction 1 breadth). The proven in-tree shape (full save) reproduces the defect; a repo-wide grep is needed to size the blast radius before the migration decision.

## Read-only confirmation

- [FACT git status] Only untracked `docs/agent-ops/` tree present; no source file modified by this agent. This report file is the sole write.
- Branch still `feat/revenuecat-paytm-phase1` at `8765b73` at report time.

---

## Appendix — line-evidence index (all FACT, all on branch feat/revenuecat-paytm-phase1 @ 8765b73)

| # | Claim | Path:line |
|---|---|---|
| 1 | Lifetime parser returns active on null expiry | backend/app/services/revenuecat.py:182-184 |
| 2 | `expires_at` None when all three keys null | backend/app/services/revenuecat.py:164-172 |
| 3 | Sync writes `has_pro_entitlement=True, pro_expires_at=None` for lifetime | backend/app/services/revenuecat.py:213-228, models.py:75 |
| 4 | `is_pro()` requires non-null future expiry | backend/app/models.py:88 |
| 5 | `pro_last_synced` is `auto_now=True` (born so in 0002) | backend/app/models.py:77, backend/app/migrations/0002_user_revenuecat_fields.py:32-36 |
| 6 | Sync only saves when `changed` | backend/app/services/revenuecat.py:230-237 |
| 7 | Celery stale filter arms | backend/app/tasks.py:535-541, 521-522 |
| 8 | Backend entitlement default `echoflow_pro` | backend/EchoFlow/settings.py:1200 |
| 9 | Backend sync stale `pro` fallback | backend/app/services/revenuecat.py:141 |
| 10 | Entitlement-keyed lookup | backend/app/services/revenuecat.py:149-162 |
| 11 | Mobile fallback `'pro'`; EAS pins `echoflow_pro` | mobile/src/lib/revenuecat.ts:28-30, mobile/eas.json:15,27,38 |
| 12 | Env templates disagree; test default `pro` | .env.example:255, .env.vps.example:181, .env.laptop.example:89, conftest.py:50,108 |
| 13 | Correct-case test exists; lifetime test does not | backend/app/tests/test_revenuecat.py:59-81, 25-52 |
| 14 | Status exposes `pro_last_synced` as `last_synced`; sync view refreshes it (read-only) | backend/app/views/subscription.py:69, 100-105 |
| 15 | `is_pro()` gate consumers (cited, grep-confirmed) | backend/app/serializers.py:382, backend/app/views/content.py:202 |
