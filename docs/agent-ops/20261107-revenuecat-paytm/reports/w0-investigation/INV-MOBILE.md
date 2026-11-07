# INV-MOBILE — Wave 0 Investigation Report

## ORCH-DIGEST

- **Defect 3.8 (charged-then-error) — CONFIRMED.** `purchasePlan` awaits `Purchases.purchasePackage` then `sync()`; any `sync()` failure (backend sync POST or subsequent GET poll) rejects into `settings.tsx:36`, showing "Purchase could not be completed" even though the store charge succeeded. There is no reconcile/pending state: error is presented as purchase failure, subscription status is not re-polled from a clean state on next failure. `useSubscription.ts:141-146`; `settings.tsx:33-38`.
- **Defect 3.9 (cancel-as-error) — CONFIRMED.** No user-cancel detection anywhere in `useSubscription.ts` or `settings.tsx`. `Purchases.purchasePackage` rejection propagates verbatim; `settings.tsx:36` alerts "Purchase could not be completed" for what is most likely a store cancel. No `code === 'USER_CANCELLED'` / `userCancelled` / `PURCHASE_CANCELLED` check, no silent-return path. T08 (silent cancel) currently fails by inspection.
- **Second consumer `studio.tsx` — CONFIRMED live, but there is a THIRD consumer the plan missed.** `mobile/app/(tabs)/studio.tsx:19` calls `useSubscription()` independently; reads only `subscription.status?.limits` (`:36-37`) to gate upload size/duration (`validate()` `:39-50`) and renders limit text (`:150`). It consumes `isPro`, `plans`, `purchasePlan`, `sync`, `refresh`, `presentPaywall`, `openCustomerPortal` **not at all**. Removing the hook breaks upload validation in Studio (limits fall back to 100 MB hard cap / "…" duration) unless refactored. Additionally `mobile/src/components/RevenueCatHost.tsx:5` calls `useSubscription()` purely for its side effects (identify/logout + refresh on auth) and is mounted unconditionally in `mobile/app/_layout.tsx:129` — plan §C lists only studio.tsx as the missed consumer; this third one is unlisted there. Grep: no other consumers.
- **Entitlement default mismatch — CONFIRMED but mitigated in practice.** Mobile default `'pro'` at `revenuecat.ts:29`; backend `REVENUECAT_ENTITLEMENT_ID=echoflow_pro` (`eas.json:15,27,38` pins it for every EAS profile, development included). So shipped builds pass `echoflow_pro` and match backend. The `'pro'` fallback only fires when `EXPO_PUBLIC_REVENUECAT_ENTITLEMENT_ID` is unset (e.g. local dev without env) — in which case `hasActiveRevenueCatEntitlement` checks the wrong key and returns false even for Pro users (locally configured SDK). Also, `hasActiveRevenueCatEntitlement` is apparently unused in the hook flow — `useSubscription` never calls it; access is decided server-side via `getSubscription()`. Verify usage by grep (pending).
- **OTA / runtimeVersion — NO OTA. CONFIRMED.** `expo-updates` absent from `mobile/package.json` deps; `runtimeVersion` is set in `app.config.ts:81-83,89` and in `eas.json:14,26,37` (all three profiles), but with no `expo-updates` consumer it is inert metadata — it does not deliver JS updates. Every native change (Paytm SDK, RC removal) = new store build. `channels` in `eas.json:11,21,33` are likewise build-time metadata only without `expo-updates`.
- **Cancel distinguishability in installed SDK — [UNKNOWN], verified-uninspectable.** Installed: `react-native-purchases 10.10.2` exactly (lockfile `mobile/package-lock.json:13338-13341`, not the `^10.10.2` range in `package.json:49`). Distinguishing user-cancel requires the SDK's typed error (`PurchasesError` code) — not referenced anywhere in mobile source. `mobile/node_modules/react-native-purchases` does not exist on this machine (no `node_modules` install; verified by read → "File not found"), so the v10.x error shape cannot be inspected. Error types would live in `@revenuecat/purchases-typescript-internal 19.3.1` (lockfile dep), but that package is likewise not on disk. Per brief, [UNKNOWN]. The C06 implementer must verify against SDK docs/types on an installed tree before coding the check.
- **Tests — no `useSubscription` test exists** (glob of all 50 `*.test.*` under mobile confirms). `revenuecat.test.ts` pins `'pro'` default at `mobile/src/lib/__tests__/revenuecat.test.ts:50` (`expect(revenueCatEntitlementId()).toBe('pro')`, env deleted at `:30`) and exercises `hasActiveRevenueCatEntitlement` (`:48-49`). `endpointsSubscription.test.ts` (36 lines) pins `/subscription/`, `/subscription/sync/` POST, `/subscription/manage/` routes and zod rejection of a missing `app_user_id`. `jest` is the mobile test runner (`package.json:14`). Node toolchain: `v24.18.0`.
- **Blockers:** none for this investigation. Removal of the hook (C06/C14) must refactor Studio's limit reads or it silently degrades upload validation.

## 1. Method

Read-only. Required reading completed in brief order: plan §C/§E(C06)/§H(T07,T08) → `00-mission.md`, `01-work-ledger.md`, `COMMS.md` → code files listed in brief §2. Verification greps/glob/lockfile reads completed (§7). No edits, no installs, no test runs (`node --version` only, v24.18.0).

## 2. Defect 3.8 — charged-then-sync-failed presented as purchase failure

[FACT `mobile/src/hooks/useSubscription.ts:141-146`]: `purchasePlan(productId)`:
```ts
const selected = packages.current.get(productId);
if (!selected) throw new Error('That plan is no longer available. Refresh plans and try again.');
await Purchases.purchasePackage(selected);
await sync();
```
No try/catch around either await. [FACT `mobile/src/hooks/useSubscription.ts:111-135`] `sync()` POSTs `/subscription/sync/` then polls `GET /subscription/` up to 5 times at 500 ms (`SYNC_ATTEMPTS=5`, `SYNC_RETRY_MS=500`, `:33-34`); on any throw it sets `error` state ('Could not synchronize subscription.', `:129`) and rethrows (`:131`). So a charge that succeeded followed by a network error, 500, or zod parse failure on sync/get surfaces through `settings.tsx:36`'s alert.

[FACT `mobile/app/settings.tsx:33-38`]:
```ts
const purchasePlan = async (productId: string) => {
  setPurchasingProductId(productId);
  try { await subscription.purchasePlan(productId); }
  catch (cause) { Alert.alert('Purchase could not be completed', cause instanceof Error ? cause.message : 'Try again.'); }
  finally { setPurchasingProductId(null); }
};
```
Exact user-facing strings: alert title **"Purchase could not be completed"**; body is the raw error `message` (e.g. backend/network string) or "Try again.". Additionally `subscription.error` is rendered inline at `settings.tsx:66` (`styles.error`), so the user can see **two** error surfaces for one failure: the alert plus the inline `subscription.error` text (e.g. "Could not synchronize subscription." or the underlying message) persisting after dismiss.

Distinction the user needs but does not get: after the charge, the app cannot tell "sync failed after charge" from "purchase never happened". `sync()`'s own catch sets `error` but rethrows the original cause, so `purchasePlan`'s caller cannot recover which stage failed. T07 scenario (sync fails after charge → reconcile notice) is not implemented.

[INFERENCE]: Because status polling in `sync()` returns only when `next.is_pro || attempt === SYNC_ATTEMPTS-1` (`:124`), a slow backend (e.g. 6h-poll lag before webhook/celery catch-up) can exhaust 5×500 ms = 2.5 s and return without error and without Pro state — a silent stale-Free state rather than a charged-then-error alert. That is the mirror failure (charged, no error, still Free UI) and is worth noting for the C06 fix design (foreground reconcile, not just honest error text).

## 3. Defect 3.9 — user cancel presented as error alert

[FACT]: Neither `useSubscription.ts` nor `settings.tsx` imports or references any cancel indicator. There is no `PurchasesError`, no `code` property check on the caught value, no `userCancelled` boolean, in either file (full-file read, 166 + 109 lines). The `catch` in `settings.tsx:36` treats every rejection identically: `Alert.alert('Purchase could not be completed', …)`.

[FACT]: `mobile/src/lib/revenuecat.ts` (65 lines, full read) contains no error classification either — it wraps `Purchases.configure`, `logIn`, `getCustomerInfo`, `logOut` only.

Whether user-cancel is distinguishable in the installed SDK version:
- [FACT `mobile/package-lock.json:13338-13341`]: resolved `react-native-purchases` is exactly **10.10.2** (caret in `package.json:49` resolves to the locked 10.10.2). Its error types live in transitive `@revenuecat/purchases-typescript-internal 19.3.1` (`package-lock.json:13351`).
- [FACT]: `mobile/node_modules/react-native-purchases` does not exist on this machine (read → file not found; no `node_modules` install in this environment). Neither can the transitive types package be inspected.
- Therefore [UNKNOWN]: whether `purchasePackage`'s rejection in 10.10.2 carries a machine-readable cancel signal (e.g. a `PurchasesError.code` of `USER_CANCELLED` or a `userCancelled` flag). No code in mobile source reads any error property (`settings.tsx:36` and `useSubscription.ts:81,86,129` all reduce to `cause.message`). **The C06 implementer can resolve this in minutes on an installed tree (`grep -rn "USER_CANCELLED\|userCancelled" node_modules/@revenuecat/purchases-typescript-internal`) or from the v10.10.2 changelog; it cannot be resolved from this repo alone.**

[INFERENCE]: RevenueCat's documented v10 error shape is a `PurchasesError` with a `code` enum including `USER_CANCELLED` — but per investigation discipline this is inference from general knowledge, not verified against the installed types. Do not cite as fact until §7 check lands.

## 4. Consumers of `useSubscription` — THREE, not one

[FACT grep mobile/src + mobile/app]: `useSubscription` is called in exactly three places:
1. `mobile/app/settings.tsx:15` — full consumer (paywall, purchase, portal, refresh, error UI).
2. `mobile/app/(tabs)/studio.tsx:19` — limits-only consumer (see below).
3. `mobile/src/components/RevenueCatHost.tsx:5` — side-effects-only consumer: `useSubscription();` then `return null`. [FACT `mobile/app/_layout.tsx:129`]: `<RevenueCatHost />` is mounted unconditionally as a sibling of `<PlayerHost />` and `<TelemetryHost />`, outside `<Stack>` — "so an auth-status flip cannot unmount it" (comment `:114-117` in the PlayerHost block, same pattern). Its purpose [FACT `RevenueCatHost.tsx:3` comment]: "Keeps RevenueCat identified for the authenticated session."

Consumed surface in Studio (exhaustive, full-file read 170 lines):
- `subscription.status?.limits` → `MaxDurationMs` / `maxSizeMb` via `limitNumber(...)` (`:36-37`).
- Indirectly everything that feeds `status`: the hook's `useEffect` on `authStatus` (`useSubscription.ts:93-109`) triggers `refresh()` → `GET /subscription/` on mount of Studio tab.

Used by `validate()` (`:39-50`):
- Size: `maxBytes = (maxSizeMb ?? 100) * 1024 * 1024`, capped at `MAX_UPLOAD_BYTES = 100 MB` (`:13`). Alert: **"File is too large"** / "Choose audio smaller than {n} MB."
- Duration: if `maxDurationSeconds != null` and exceeded → Alert **"Clip is too long"** / "Your current limit is {n} seconds."
- Rendered always (`:150`): `Limit: {maxDurationSeconds ?? '…'} seconds · {maxSizeMb ?? '…'} MB`.

Unused in Studio: `isPro`, `plans`, `loading`, `refreshing`, `error`, `refresh`, `sync`, `presentPaywall`, `purchasePlan`, `openCustomerPortal` (none referenced in the file).

[INFERENCE] what removing/changing the hook breaks, per consumer:
- **Studio** (see above): loses `status.limits`; size falls back to 100 MB cap, duration check skipped, limit line shows "… seconds". Silent free-tier enforcement regression, not a crash.
- **RevenueCatHost**: it is the only *always-mounted* instance — its `useEffect` on `authStatus` (`useSubscription.ts:93-109`) is the mechanism that identifies the SDK user (`identifyRevenueCat` → `Purchases.logIn(appUserId)`) and logs out on sign-out (`logoutRevenueCat` → `Purchases.logOut`). If `useSubscription` is deleted, this host must be re-pointed at whatever replaces it or SDK identity goes stale: the store account would retain the previous user's identity, and a purchase could be attributed to the wrong app user. This is the highest-risk consumer to break, and it is invisible in the UI (renders null) — a deletion refactor that only greps for visible UI would strand it.
- **Settings**: loses its entire billing screen (plans, purchase, portal, refresh, error text). C06/C14 must provide a replacement hook with the same `SubscriptionState` shape (`useSubscription.ts:17-29`).
- Three mounted instances (RevenueCatHost + Settings + Studio tab) each `refresh()` on auth → up to 3× `GET /subscription/` + `Purchases.logIn` + `getOfferings()` per auth change; wasteful but idempotent (`configure` is once-per-key via `configuredKeys`, `revenuecat.ts:43-46`).

## 5. Entitlement default mismatch (3.2, mobile side)

[FACT `mobile/src/lib/revenuecat.ts:28-30`]:
```ts
export function revenueCatEntitlementId(): string {
  return process.env.EXPO_PUBLIC_REVENUECAT_ENTITLEMENT_ID?.trim() || 'pro';
}
```
[FACT `mobile/eas.json:15,27,38`]: every profile (`development`, `preview`, `production`) sets `"EXPO_PUBLIC_REVENUECAT_ENTITLEMENT_ID": "echoflow_pro"`. [FACT `mobile/app.config.ts`]: does not set this var (searched full file, 194 lines) — it flows via EAS env.
[FACT plan §C] backend default `REVENUECAT_ENTITLEMENT_ID=echoflow_pro` (`settings.py:1200`) — cite as plan fact; settings.py not re-read in this pass.

Where `revenueCatEntitlementId()` is used: [FACT `mobile/src/lib/revenuecat.ts:58-60`] only in `hasActiveRevenueCatEntitlement(info)`, which checks `info.entitlements.active[revenueCatEntitlementId()]`. [FACT grep mobile/src + mobile/app, completed]: `hasActiveRevenueCatEntitlement` has exactly one caller — `mobile/src/lib/__tests__/revenuecat.test.ts:48-49`. It is **dead code in production** (no screen, no hook calls it). `useSubscription.ts` imports only `getRevenueCatCustomerInfo`, `identifyRevenueCat`, `logoutRevenueCat` (`:10-14`), and `getRevenueCatCustomerInfo` is awaited at `useSubscription.ts:77` with its return value discarded — its result feeds nothing; the access decision is `status.is_pro` from the API (`:156`).

Verdict: mismatch exists as a fallback default (`'pro'` vs `echoflow_pro`) but shipped EAS builds always override it, and the hook's access decision is server-side (`status.is_pro`, `useSubscription.ts:156`), so the mismatch does not affect the production purchase gate today. It matters only for local dev without the env var (wrong key → false negative in the dead helper) and for the P3 single-constant drift test planned in §I P3.

## 6. OTA / runtimeVersion

[FACT `mobile/package.json:18-58`] full deps read: `expo-updates` is **absent**. Present and relevant: `expo ~57.0.26`, `expo-dev-client ~57.0.19`, `expo-router ~57.0.24`, `react-native-purchases ^10.10.2`, `react-native-purchases-ui ^10.10.2`.
[FACT `mobile/app.config.ts:81-83,89`]:
```ts
const runtimeVersion = env.EXPO_PUBLIC_RUNTIME_VERSION?.trim()
  || (releaseChannel === 'production' ? APP_VERSION : `${APP_VERSION}-${releaseChannel}`);
...
runtimeVersion,
```
[FACT `mobile/eas.json:14,26,37`]: `EXPO_PUBLIC_RUNTIME_VERSION` pinned per profile (`1.0.0-development`, `1.0.0-preview`, `1.0.0`) — so `app.config.ts`'s fallback (`1.0.0-${channel}`) is not what EAS builds use; the env value wins. `appVersionSource: "remote"` (`eas.json:4`) + `autoIncrement: true` on production (`:34`) interact with the remote version, not `APP_VERSION`.
[FACT]: no other `runtimeVersion` consumer found in required files; `app.config.ts:139-140` is the dev-CA plugin comment (not runtimeVersion).

Verdict: no OTA delivery path exists. `runtimeVersion` and EAS `channel` values are inert for code delivery without `expo-updates` — removing or renaming them changes nothing at runtime, which is good news for Phase 6 (C15 bump is a no-op for users), but any native change (Paytm SDK add, RC SDK remove) requires a full store build + review. Matches plan §C "no OTA" and §I C2.

## 7. Checks completed

- [x] `grep -rn "hasActiveRevenueCatEntitlement|revenueCatEntitlementId|getRevenueCatCustomerInfo|useSubscription" mobile/src mobile/app` — 18 matches. `useSubscription` callers: settings.tsx, studio.tsx, RevenueCatHost.tsx. `hasActiveRevenueCatEntitlement` callers: only revenuecat.test.ts (dead in prod). `getRevenueCatCustomerInfo`: defined revenuecat.ts:50, called useSubscription.ts:77, return discarded.
- [x] `grep -rn "RevenueCatHost" mobile/src mobile/app` — defined RevenueCatHost.tsx:4, imported+mounted `_layout.tsx:27,129`.
- [x] `glob mobile/**/*.test.*` — 50 test files; no `useSubscription`/`studio`/`settings` test; `revenuecat.test.ts` + `endpointsSubscription.test.ts` read (see digest).
- [x] `node_modules/react-native-purchases` — absent (not installed). Cancel distinguishability stays [UNKNOWN] necessarily; resolved SDK version is 10.10.2 exact via package-lock.
- [x] `grep -rn "expo-updates|runtimeVersion" mobile` — `expo-updates`: zero matches anywhere. `runtimeVersion`: only app.config.ts:81,83,89. No hidden OTA consumer.
- [x] `node --version` → **v24.18.0**.

## 8. Implications for C06 (fix design inputs)

- C06 must split `purchasePlan` into two stages (SDK purchase vs backend sync) so 3.8 can be told from 3.9 and from pre-charge failure. Suggested shape (inference, not prescribed): catch `purchasePackage` rejection → inspect cancel code → silent return on cancel (no alert, reset button state); on sync failure after successful purchase → distinct reconcile notice + retry/foreground-reconcile path, not "Purchase could not be completed".
- Cancel-code check depends on §7 node_modules verdict. If the installed SDK types expose no cancel code, the fallback is heuristic (no charge known → treat as cancel only if error message matches) — flag as UNKNOWN until verified.
- Studio (`studio.tsx:36-37`) reads `status.limits`; any replacement hook MUST keep returning `status` with `limits` or Studio silently loses free-tier duration/size enforcement. Add a Studio-facing contract test.

## 9. Facts vs inferences log

All `[FACT]` entries above carry path:line. `[INFERENCE]` items: §2 mirror-failure (silent stale-Free), §3 permissive remark about v10 error shape (superseded by UNKNOWN verdict — not relied upon), §4 removal impact. `[UNKNOWN]`: §3 cancel distinguishability in SDK 10.10.2 (node_modules not installed; unresolvable from repo alone — implementer check specified).

## ORCH-DIGEST (finalised)

See top of file. Status: COMPLETE. All §7 checks landed. Two verdicts carry [UNKNOWN]/caveats: cancel distinguishability (node_modules absent, unresolvable from repo), and the backend-side entitlement constant is cited from plan §C rather than re-read (out of INV-MOBILE scope; INV-BACKEND-A owns settings.py 3.2).
