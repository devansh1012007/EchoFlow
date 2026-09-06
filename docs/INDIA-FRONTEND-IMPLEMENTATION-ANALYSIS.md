# EchoFlow — India Regulatory Frontend Implementation Design (FR-IN-*)

> **Purpose:** This document establishes the shared understanding before any file edits. It covers: what will change, which files, why those changes, data/control flows before/after, design decisions with tradeoffs, alternatives, edge cases, failure modes, test impact, and risks. It is grounded in actual files from `docs/FRONTEND-REQUIREMENTS.md`, `backend/app/`, and `frontend/sample_frontend/src/`.

> **Status:** Analysis presented. **Awaiting user approval of final implementation plan before any file edits.**

---

## 1. User Decisions (Locked)

From the discussion above:

| Question | User Choice | Impact on Design |
|---|---|---|
| Grievance form access | **Auth-only** | Grievance endpoint (`/grievance/`) stays `IsAuthenticated`. The UI in Settings/Grievance page must check `authed`. No public grievance link needed in footer (though compliance info can be public). |
| Takedown form placement | **Standalone `/legal/takedown`** | A new route `/legal/takedown` in router.tsx; new `TakedownPage` component; links from footer and from clip report flow. |
| Consent withdrawal UX | **Just set `withdrawn_at`** | The `POST /consent/withdraw/` endpoint creates/updates `ConsentAudit`. The frontend shows a confirmation toast, not a multi-step cooling-off wizard. No data-deletion flow triggered at this step (separate from `/data-subject/erasure/`). |
| Age gate warning copy | **Standard template** | A standard regulatory warning message in `Login.tsx` and `RegisterSerializer` validation: "If you are under 18, a parent or guardian must verify consent before full access is granted." The backend validates `dob`; if minor (`is_minor=True`), requires `parent_email`; sets `minor_consent_verified=False` by default. The frontend displays the soft warning. No hard block at v1 (soft gate per design). |
| Region banner display | **One-time toast** | A single `NetworkBanner`-style toast that appears once per session (or once ever via sessionStorage) when `STORAGES` region is not `ap-south-1`. Not persistent. |

---

## 2. Existing Architecture & Execution Flow (Before Changes)

### 2.1 Registration Flow (`Login.tsx` → `stores/auth.tsx` → `api/client.ts` → backend `RegisterSerializer`)

**Current path:**
```
LoginPage (Login.tsx:113-122)
  → register(email, username, password) (stores/auth.tsx:52-63)
    → authAPI.register() (api/client.ts:93-94)
      → POST /auth/register/ (backend/app/urls.py:61)
        → RegisterView (views/auth.py)
          → RegisterSerializer (serializers.py:405-491)
            → validates: consent_accepted=True, terms_version, dob, parent_email (if minor)
            → creates User + ConsentAudit + Grievance? No.
            → returns User (201)
      → authAPI.login() (client.ts:91-92)
        → POST /auth/login/ → tokens
      → persist() → sessionStorage: ef_access, ef_refresh, ef_user
      → sessionStorage.setItem('ef_new_user', '1')
```

**Current gaps in frontend:**
- `Login.tsx` (line 13): `form` state only has `{email, username, password}` — no `dob`, `consent_accepted`, `terms_version`, `license_type`, `copyright_owner_name`, `copyright_acknowledgement`, `parent_email`.
- The frontend does NOT render any consent checkbox, DOB picker, or copyright acknowledgment.
- `OnboardingModal.tsx` does NOT reference any privacy/grievance/compliance links.

### 2.2 Upload Flow (`Upload.tsx` → `client.ts` → backend `AudioUploadSerializer`)

**Current path:**
```
UploadPage
  → submit() (Upload.tsx:47-75)
    → clipsAPI.uploadClip(fd) (client.ts:114)
      → POST /clips/ (urls.py:55 via router)
        → AudioUploadSerializer.validate()
          → checks file extension, magic bytes, duration (≤300s)
        → AudioUploadSerializer.validate()
          → checks copyright_acknowledgement=True
          → checks license_type
        → AudioUploadSerializer.create()
          → creates AudioClip with license_type, copyright_owner_name, copyright_acknowledgement
          → sets moderation_approved=False
          → triggers process_audio_to_hls (transaction.on_commit)
```

**Current gaps:**
- `Upload.tsx` (line 183-195): Tags input exists but is never submitted (`fd` in `submit()` does not include tags).
- `Upload.tsx`: No `license_type` dropdown, no `copyright_owner_name` input, no `copyright_acknowledgement` checkbox. These fields exist in the serializer (`AudioUploadSerializer`, lines 152-165) but the frontend never sends them. The backend will fail validation (`copyright_acknowledgement` is required) unless the form sends it.

### 2.3 Profile/Settings (`ProfilePage.tsx`, `Settings.tsx` → backend `ProfileSerializer`, `ProfileViewSet`)

**Current state:**
- `ProfilePage.tsx`: Uses `OwnProfileSerializer` (`profile/me/`) for own profile; `PublicProfileSerializer` for others. `OwnProfileSerializer` includes `liked_clips` (line 462-496 in serializers.py) but `ProfilePage` sometimes calls the wrong endpoint (`getProfile` instead of `getMyProfile` for own profile — ISSUE-12).
- `Settings.tsx`: Has dead rows (`Privacy`, `Notifications`, `Language`, `Help & Feedback`, `About`) that navigate nowhere. No grievance, no data export, no consent withdrawal.

### 2.4 Comment & Interaction Flow (`CommentSheet.tsx`, `ReelCard.tsx` → backend `CommentSerializer`, `CommentViewSet`)

**Current state:**
- `CommentSheet.tsx`: Posts comments (`POST /comments/`), supports reply (`POST /comments/` with `parent`), supports edit (`PATCH /comments/{id}/`), supports delete (`DELETE /comments/{id}/`). No `report` button.
- `ReelCard.tsx`: Like (`/interactions/{id}/toggle-like/`), skip (`registerSkip` — actually wired now per ISSUE-22 fix), share (`ShareModal`). No `report` button.

### 2.5 Sharing & Follow (`ShareModal.tsx`, `ReelCard.tsx` → backend `ShareEventSerializer`, `ShareViewSet`)

**Current state:**
- `ShareModal.tsx`: Correct `findUser`, `sendShare`, `copyLink` (uses `clip.hls_playlist_url` or `/public/clips/{id}`). `markRead` uses `PATCH` but backend requires `POST` (broken contract — ISSUE-14).
- `ReelCard.tsx`: `toggleFollow` works. No `report` action.

### 2.6 Auth & Session (`stores/auth.tsx` → backend `TokenObtainPairView`, `RefreshTokenView`)

**Current state:**
- `auth.tsx`: `register()` now calls `login()` after successful registration (fixed ISSUE-21 per agent 4). `logout()` is local-only (does not call `POST /auth/logout/` with refresh token — ISSUE-21 gap). No consent-related state management.

### 2.7 Regulatory Endpoints (Backend — Confirmed by Reading Source)

From reading `backend/app/urls.py`, `views/grievance.py`, `views/data_subject.py`, `views/legal.py`, `services/content_moderation.py`, `models.py`:

| Endpoint | Method | Auth? | Source File | Status | Frontend Consumer? |
|---|---|---|---|---|---|
| `/auth/register/` | POST | AllowAny | `urls.py`, `views/auth.py`, `serializers.py` (RegisterSerializer) | **Implemented** (includes consent, dob, parent_email, license fields) | Missing in `Login.tsx` |
| `/auth/login/` | POST | AllowAny | `urls.py`, `ThrottledTokenObtainPairView` | Implemented | Implemented |
| `/auth/token/refresh/` | POST | AllowAny (needs refresh) | `urls.py`, `TokenRefreshView` | Implemented | Implemented |
| `/auth/logout/` | POST | IsAuthenticated | `urls.py`, `LogoutView` | Implemented | **Missing** (`stores/auth.tsx` doesn't call it) |
| `/grievance/` | POST | AllowAny | `urls.py`, `GrievanceCreateView` | Implemented (direct creation) | **Missing** (no UI) |
| `/legal/compliance/` | GET | AllowAny | `urls.py`, `ComplianceContactView` | Implemented (returns JSON) | **Missing** (no link in footer or settings) |
| `/legal/takedown/` | POST | AllowAny | `urls.py`, `TakedownRequestView` | Implemented | **Missing** (standalone page needed) |
| `/data-subject/access/` | GET | IsAuthenticated | `urls.py`, `DataSubjectAccessView` | Implemented | **Missing** (no Settings link) |
| `/data-subject/erasure/` | POST | IsAuthenticated | `urls.py`, `DataSubjectErasureView` | Implemented (30-day cooling-off) | **Missing** (no Settings link) |
| `/clips/{id}/report/` | POST | Not in urls.py — **MISSING** | `views/content.py` — no `report` endpoint exists | **Not implemented** | **Missing** (need endpoint + frontend) |
| `/comments/{id}/report/` | POST | Not in urls.py — **MISSING** | `views/comments.py` — no `report` endpoint exists | **Not implemented** | **Missing** (need endpoint + frontend) |
| `/consent/withdraw/` | POST | Not in urls.py — **MISSING** | `models.py` — `ConsentAudit` has `withdrawn_at` (nullable) | **Not implemented** | **Missing** (need endpoint + frontend) |

**Critical finding from reading source:** The `Report` model (`models.py:339-346`) exists but there is **NO endpoint** exposing it. The `TakedownRequest` model (`models.py:330-337`) exists and the `/legal/takedown/` endpoint exists. The `Grievance` model (`models.py:268-288`) exists and `/grievance/` endpoint exists.

---

## 3. Design Decisions & Tradeoffs

### 3.1 Design Decision: Auth-Only Grievance (Not Public)

**Choice (per user):** Grievance form requires authentication (`IsAuthenticated`).

**Reasoning:** The user selected "Auth-only" explicitly. The `GrievanceCreateView` (`grievance.py`) uses `permission_classes = [permissions.AllowAny]`, so the backend supports public access. Changing the frontend to auth-only aligns with the user's directive.

**Tradeoff:** The IT Rules 2021 (Rule 4) requires every intermediary to have a grievance mechanism accessible to all users. Making it auth-only narrows access but satisfies the user's design choice. The `ComplianceContactView` (`legal/compliance/`) remains public (`AllowAny`), which provides public grievance contact info. The actual grievance submission requires auth, which the backend allows either way (`AllowAny` in view, but we can enforce auth at the frontend layer).

**Implementation:** In the frontend, the grievance link/page checks `authed` before rendering the form. If unauthenticated, redirect to `/login`.

---

### 3.2 Design Decision: Standalone Takedown Page (`/legal/takedown`)

**Choice (per user):** Takedown form lives at its own route/page, not embedded in Settings or clip actions.

**Reasoning:** Copyright takedown is a legal process with different actors (copyright owners, not platform users). A standalone page (`/legal/takedown`) makes it discoverable by external parties (search engines, legal scanners) and keeps it separate from user-facing settings.

**Implementation:**
- New route in `router.tsx`: `/legal/takedown`
- New `TakedownPage` component in `pages/`
- Link from footer (`AppShell` or `BottomNav` or a new footer component) and from `ReelCard` report action
- Uses backend endpoint `/legal/takedown/` (already exists in `urls.py`)

---

### 3.3 Design Decision: Consent Withdrawal — Only Set `withdrawn_at` (No Soft-Delete)

**Choice (per user):** `POST /consent/withdraw/` only updates `ConsentAudit.withdrawn_at`. No user deletion, no 30-day cooling-off triggered by this endpoint.

**Reasoning:** The user explicitly selected this. The `ConsentAudit` model (`models.py:60-76`) has `withdrawn_at` (nullable DateTimeField). There is no endpoint in `urls.py` for consent withdrawal — it must be added. The `DataSubjectErasureView` (`data_subject.py`) handles the 30-day erasure separately.

**Implementation:**
- Add endpoint `POST /auth/consent/withdraw/` (or `/consent/withdraw/`)
- The endpoint creates/updates `ConsentAudit` with `withdrawn_at = timezone.now()`
- Frontend: Settings page button → confirmation dialog → API call → toast confirmation
- No cascading effects (user account stays active, data stays in place)

---

### 3.4 Design Decision: Soft Age Gate (Standard Template Warning)

**Choice (per user):** Standard regulatory warning text. No hard block.

**Reasoning:** The user selected "soft gate (warning)". The backend (`RegisterSerializer.validate()`, `serializers.py:434-456`) validates age and requires `parent_email` for minors (`is_minor=True`). The `minor_consent_verified` defaults to `False`. The backend does NOT block registration for unverified minors (the `RegisterSerializer` creates the user but sets flags) — it only requires `parent_email` if `dob` indicates `< 18`. This is a soft gate at the backend level (requires parent email but doesn't block the account). The frontend should display a standard warning but not block submission if the user chooses to proceed.

**Implementation:**
- In `Login.tsx`: Add DOB picker (optional). If DOB entered, compute age in `submit()` before calling API. If `< 18`, show a standard template warning: "If you are under 18, a parent or guardian must provide their email (`parent_email`) for verification. Access to behavioral tracking features may be restricted until verification is complete."
- The `RegisterSerializer` handles the rest. No additional frontend enforcement needed (soft gate = backend validates fields but doesn't hard-block; frontend shows warning for transparency).

---

### 3.5 Design Decision: Region Banner — One-Time Toast

**Choice (per user):** One-time toast, not persistent banner.

**Reasoning:** The region enforcement (`AWS_S3_REGION_NAME = ap-south-1`) is an operational deployment concern. A one-time toast is sufficient to inform the user/developer without disrupting the UI permanently.

**Implementation:**
- New component `RegionToast.tsx` (similar to `NetworkBanner.tsx`)
- Checks `STORAGES` region from an environment variable or a new backend endpoint `/system/region/` (not needed — can check via `VITE_S3_REGION` env or just assume the backend enforces it)
- Shows once per session (`sessionStorage.setItem('ef_region_checked', '1')`)
- Appears in `AppShell` after first page load if region is not `ap-south-1`
- Toast text: "Note: This instance is deployed outside the India region (`ap-south-1`). Some regulatory features may be unavailable in production deployments outside this region."

---

## 4. Data & Control Flow Changes

### 4.1 Registration Flow (After Changes)

```
LoginPage (Login.tsx)
  → form state expanded: {email, username, password, dob?, consent_accepted, terms_version, parent_email?, license_type?, copyright_owner_name?, copyright_acknowledgement?}
  → register(email, username, password, dob?, consent_accepted=True, terms_version='v1.0', parent_email?, ...)
    → authAPI.register() (client.ts: extended parameters)
      → POST /auth/register/
        → RegisterSerializer (serializers.py)
          → validates consent_accepted, terms_version, dob/parent_email (if minor)
          → creates User (is_minor flag, minor_consent_verified=False)
          → creates ConsentAudit
          → returns User (201)
      → authAPI.login() (same)
      → persist() in auth store
```

**Key data changes:**
- `Login.tsx` `form` type expanded
- `authAPI.register()` parameters expanded
- `RegisterSerializer` (already updated per Issue-01) handles the new fields

---

### 4.2 Upload Flow (After Changes)

```
UploadPage (Upload.tsx)
  → submit()
    → fd expanded: original_file, title, category, license_type, copyright_owner_name, copyright_acknowledgement
    → clipsAPI.uploadClip(fd)
      → POST /clips/
        → AudioUploadSerializer (already has fields)
          → validates copyright_acknowledgement=True
          → validates license_type
          → creates AudioClip with new fields
```

**Key data changes:**
- `UploadPage` form state expanded (`license_type`, `copyright_owner_name`, `copyright_acknowledgement`)
- Tags input removed (`tags` not submitted)
- `client.ts`: `uploadClip` sends expanded `FormData`

---

### 4.3 Settings / Regulatory Actions (New Flow)

```
SettingsPage (Settings.tsx)
  → new rows: Grievance (auth-only), Data Access (GET /data-subject/access/),
    Data Erasure (POST /data-subject/erasure/), Consent Withdrawal (POST /consent/withdraw/),
    Login History (GET /profile/me/ + audit?), Compliance Contacts (GET /legal/compliance/)
  → click Grievance → opens GrievanceForm (modal or page at /grievance/)
    → POST /grievance/ (subject, description, user_email)
    → response: grievance_id, status='received', acknowledgment_due
  → click Consent Withdrawal → POST /consent/withdraw/
    → response: withdrawn_at set, confirmation message
  → click Data Access → GET /data-subject/access/
    → response: categories (profile, interactions, comments, shares, clips)
  → click Data Erasure → POST /data-subject/erasure/ (confirm=true)
    → response: cooling_off_until, message (30-day period)
```

**Key data changes:**
- `SettingsPage`: Replace dead rows with regulatory rows; remove `Privacy` row (replaced by consent/grievance); `Notifications` and `Language` stay dead or are removed.
- New `GrievanceForm` component (or inline modal in Settings)
- `client.ts`: Add `grievanceAPI`, `consentAPI`, `dataSubjectAPI` exports
- `types/index.ts`: Add `Grievance`, `DataSubjectRequest`, `ConsentAudit`, `ComplianceContact`, `Report`, `TakedownRequest` interfaces

---

### 4.4 Report Flow (New)

```
ReelCard.tsx (clip card in feed)
  → new button: Report (next to Like, Comment, Share buttons, or in overflow menu)
    → click → opens ReportModal (similar to ShareModal)
      → ReportForm: select reason (dropdown: CSAM, Hate Speech, Copyright, Other), optional description, optional user_email
      → POST /clips/{clip.id}/report/ (new endpoint needed — see Risks section)
      → response: report submitted

CommentSheet.tsx (comment sheet)
  → new button: Report (next to Edit/Delete for comments, or a small icon next to comment text)
    → POST /comments/{comment.id}/report/
```

**Key finding from source:** The `Report` model exists (`models.py:339-346`) but **NO endpoint exists** (`urls.py` does not register it). A new endpoint **must** be added to the backend. The user must approve this before I implement it — I will note it in the Implementation Plan.

---

### 4.5 Footer / Global Navigation (New)

```
AppShell (AppShell.tsx)
  → add footer component or modify BottomNav to include legal links
  → new links in footer (visible at bottom of all pages):
    /legal/compliance/ → ComplianceContactView
    /legal/takedown/ → TakedownRequestView (standalone page)
    /grievance/ → GrievanceCreateView (auth-only — redirect to login if unauthenticated)
    Privacy Notice (new page or link to /legal/privacy/ — could reference backend settings/privacy_version)
```

---

## 5. File-by-File Implementation Details

### 5.1 `docs/FRONTEND-REQUIREMENTS.md` (New Section 12)

The document will receive a new **Section 12: India Regulatory Requirements** with 40+ `FR-IN-*` items. Each item follows the same format as existing requirements (§3.1-§3.10):

```
### FR-IN-[CODE]-[NUMBER] — [Title]
- **Law:** [DPDP §X / IT Rules 2021 Rule X / Copyright Act §X / CERT-In]
- **Backend endpoint / model:** [Endpoint or model reference]
- **Required UI behavior:** [What the user sees and does]
- **Data flows:** [Request/response shapes]
- **Edge cases / errors:** [Error handling]
- **Status:** [Implemented / Partially implemented / Missing / Not applicable]
- **Files:** [Files to modify]
```

The existing `FRONTEND-REQUIREMENTS.md` format uses sections 3.1 (Auth), 3.2 (Feed), 3.3 (Explore), etc. Section 12 will be organized differently — by regulatory area (Consent, Age Gate, Grievance, Data Subject Rights, Content Moderation, Copyright, Identity/Audit) — to match the audit document structure, rather than by page/component.

---

### 5.2 `frontend/sample_frontend/src/pages/Login.tsx`

**Changes:**
1. Add `dob` Date input (optional, `type="date"`)
2. Add `consent_accepted` checkbox (required, default `false`)
3. Add `terms_version` hidden/select (default `v1.0`, validated against env `TERMS_VERSIONS` — but for simplicity, just a hidden input with `v1.0` or a visible dropdown showing the allowed versions)
4. If `dob` entered and age < 18: show `parent_email` input; show standard template warning message; set `parent_email` in form
5. Add `copyright_acknowledgement` checkbox (required) — but this is for upload, so maybe keep it out of login and add to upload only
6. `form` interface expanded: `{email, username, password, dob?, parent_email?, consent_accepted: boolean, terms_version: string}`

**Implementation note:** The `RegisterSerializer` already validates these fields (`serializers.py:434-491`). The frontend must send them. No new backend endpoint needed.

---

### 5.3 `frontend/sample_frontend/src/pages/Upload.tsx`

**Changes:**
1. Remove tags input (lines 183-195) — `tags` not accepted by backend
2. Add `license_type` dropdown (`Owned`, `CC0`, `CC-BY`, `CC-BY-SA`, `CC-BY-NC`, `Public_Domain`, `Unknown`) — maps to `AudioUploadSerializer.LICENSE_CHOICES`
3. Add `copyright_owner_name` text input (optional, 255 chars max)
4. Add `copyright_acknowledgement` checkbox (required) — must be checked before submit
5. `FormData` in `submit()` expanded with these new fields
6. Add standard regulatory note above the form: "By uploading, you confirm you have the right to upload this audio and it does not infringe any third-party rights. See `/legal/compliance/` for details."

---

### 5.4 `frontend/sample_frontend/src/pages/Settings.tsx`

**Changes:**
1. Replace dead rows (`Privacy`, `Notifications`, `Language`, `Help & Feedback`, `About`) with:
   - **Grievance** (`Shield` or `AlertTriangle` icon): Links to `/grievance/` (auth-only). If not authenticated, redirect to login. Shows a brief description: "File a grievance about content, privacy, or account issues. We respond within 24 hours."
   - **Data Access** (`Download` icon): Links to `/data-subject/access/` (auth-only). Returns JSON export of user data.
   - **Data Erasure** (`Trash2` icon): Opens `/data-subject/erasure/` confirmation. Shows standard message: "Submitting an erasure request initiates a 30-day cooling-off period. After that, your data will be deleted."
   - **Consent Withdrawal** (`X` or `Ban` icon): Opens `/consent/withdraw/` confirmation dialog. Shows: "Withdrawing consent stops future data processing. Existing data remains unless you also submit an erasure request."
   - **Compliance Contacts** (`Mail` or `Phone` icon): Links to `/legal/compliance/`. Shows officer info returned by backend.
2. Keep `Profile` (existing) and `Theme` (existing) as-is.
3. Keep `Sign out` at bottom.

---

### 5.5 `frontend/sample_frontend/src/app/router.tsx`

**Changes:**
1. Add new routes:
   - `/grievance/` → `GrievancePage` (auth-only; redirect to login if unauthenticated)
   - `/legal/compliance/` → `CompliancePage` (public; uses `ComplianceContactView` data from `/legal/compliance/` endpoint)
   - `/legal/takedown/` → `TakedownPage` (public; standalone page with form)
   - `/data-subject/access/` → `DataSubjectAccessPage` (auth-only)
   - `/data-subject/erasure/` → `DataSubjectErasurePage` (auth-only)
2. Note: `/public/clips/:id` route exists but redirects to `/feed`. This is fine — the public clip endpoint is handled by the backend (`/public/clips/{id}/` does not exist in `urls.py` either — `PublicClipRedirect` in router redirects to feed, which aligns with the backend design that public playback uses the HLS URL directly, not a deep-link page).

---

### 5.6 `frontend/sample_frontend/src/pages/Profile.tsx`

**Changes:**
- No major changes needed for regulatory requirements. The profile page uses `OwnProfileSerializer` (correct for own profile) and `PublicProfileSerializer` (correct for others). `profile_picture_url` is handled. No grievance/data-subject links needed here — those belong in Settings.

---

### 5.7 `frontend/sample_frontend/src/components/audio/ReelCard.tsx`

**Changes:**
1. Add **Report** button to the vertical interaction stack (like the existing Like/Comment/Share buttons). It should appear as a small text button or overflow menu item.
2. Report flow: Click → `ReportModal` (new component similar to `ShareModal`) → form with reason dropdown (`CSAM`, `Hate Speech`, `Obscenity`, `Copyright Infringement`, `Other`) + optional description + optional file attachment (optional) → POST `/clips/{clip.id}/report/` (new endpoint required — see Risks).
3. Note: The `Report` model (`models.py:339-346`) exists but there is no endpoint. The design must include the endpoint or the frontend flow is incomplete.

---

### 5.8 `frontend/sample_frontend/src/components/comments/CommentSheet.tsx`

**Changes:**
1. Add **Report** button next to Edit/Delete for comments (or as a small icon next to the comment author's name).
2. Click → `ReportCommentModal` (new component) → form with reason dropdown + description → POST `/comments/{comment.id}/report/` (new endpoint required).

---

### 5.9 `frontend/sample_frontend/src/components/feed/OnboardingModal.tsx`

**Changes:**
1. Add standard regulatory links at the bottom of the welcome screen:
   - "By continuing, you agree to our Terms (`terms_version`) and Privacy Notice."
   - Link to `/legal/compliance/` for grievance officer info.
2. Add standard template message for age gate: If the user hasn't provided `dob` yet (the onboarding is the first interaction after registration, so DOB should have been collected at registration — but if not, show the message):
   - Standard text: "This platform complies with the Digital Personal Data Protection Act 2023 (DPDP Act). By using this service, you consent to the processing of your data as described in our Privacy Notice. If you are under 18, please provide a parent or guardian's email for verification."

---

### 5.10 `frontend/sample_frontend/src/app/AppShell.tsx`

**Changes:**
1. Add `RegionToast` component (new, one-time toast) shown after first page load if `STORAGES` region is not `ap-south-1`.
2. Add footer component with links: `Compliance` (to `/legal/compliance/`), `Grievance` (to `/grievance/` — auth redirect handled by route), `Takedown` (to `/legal/takedown/`), `Privacy`, `Terms`.
3. The footer should be minimal (not a full page footer — just a small text section at the bottom of the page content, or integrated into `AppShell` as a fixed footer below `BottomNav`).

---

### 5.11 `frontend/sample_frontend/src/components/common/atoms.tsx`

No changes needed. The `Avatar` component handles `profile_picture_url` correctly (line 36: `resolveMediaUrl(src)`). The `CatBadge` works. The `Spinner`, `Waves`, and `inputStyle` are fine.

---

### 5.12 `frontend/sample_frontend/src/api/client.ts`

**Changes:**
1. Add `consentAPI` export:
   ```typescript
   export const consentAPI = {
     withdrawConsent: () => api('/auth/consent/withdraw/', { method: 'POST' }),
   };
   ```
2. Add `grievanceAPI` export:
   ```typescript
   export const grievanceAPI = {
     createGrievance: (data: { subject: string; description: string; user_email?: string }) =>
       api('/grievance/', { method: 'POST', body: JSON.stringify(data) }),
   };
   ```
3. Add `dataSubjectAPI` export:
   ```typescript
   export const dataSubjectAPI = {
     accessData: () => api('/data-subject/access/'),
     requestErasure: (confirm: boolean) => api('/data-subject/erasure/', { method: 'POST', body: JSON.stringify({ confirm }) }),
   };
   ```
4. Add `legalAPI` export (if needed for compliance details):
   ```typescript
   export const legalAPI = {
     getComplianceContacts: () => api('/legal/compliance/'),
     submitTakedown: (data: { clip_id: string; reason: string; requester_email?: string }) =>
       api('/legal/takedown/', { method: 'POST', body: JSON.stringify(data) }),
   };
   ```
5. Note: `authAPI.register()` parameters need to be expanded to include the new optional fields (`dob`, `parent_email`, `consent_accepted`, `terms_version`). However, since these are optional or have defaults at the backend, the existing call can be extended without breaking existing behavior.

---

### 5.13 `frontend/sample_frontend/src/types/index.ts`

**Changes:**
Add new interface definitions:

```typescript
export interface Grievance {
  grievance_id: number;
  status: 'received' | 'acknowledged' | 'under_review' | 'resolved';
  acknowledgment_due: string;
  message: string;
}

export interface DataSubjectAccessResponse {
  user_id: number;
  categories: Record<string, unknown>;
}

export interface DataSubjectErasureResponse {
  request_id: number;
  status: string;
  cooling_off_until: string;
  message: string;
}

export interface ConsentWithdrawalResponse {
  withdrawn_at: string;
  status: string;
}

export interface ComplianceContactResponse {
  compliance_officer: { name: string; email: string };
  grievance_officer: { name: string; email: string };
  nodal_contact: { name: string; email: string };
}

export interface TakedownResponse {
  status: string;
  message: string;
  clip_id: string;
}
```

---

### 5.14 `frontend/sample_frontend/src/stores/auth.tsx`

**Changes:**
1. `register()` already handles `login()` after registration (fixed ISSUE-21). No additional change needed for consent flow, but verify that `register()` passes the expanded form data correctly.
2. `logout()` should call `authAPI.logout()` (new or existing endpoint `POST /auth/logout/`). Currently it only clears local storage. Add:
   ```typescript
   await authAPI.logout();
   ```
   Before clearing tokens. This ensures refresh token blacklisting.

---

### 5.15 `frontend/sample_frontend/src/pages/Settings.tsx`

**Changes (detailed):**
Replace the `rows` array (lines 23-30) with regulatory-focused rows:

```typescript
const rows: { label: string; icon: React.ElementType; action: () => void; danger?: boolean }[] = [
  { label: 'Profile', icon: User, action: () => go('profile') },
  { label: 'Grievance', icon: AlertTriangle, action: () => { if (!authed) { go('login'); } else { go('grievance'); } } },
  { label: 'Data Access', icon: Download, action: () => { if (!authed) { go('login'); } else { go('data-subject/access'); } } },
  { label: 'Data Erasure', icon: Trash2, action: () => { if (!authed) { go('login'); } else { go('data-subject/erasure'); } } },
  { label: 'Consent Withdrawal', icon: Ban, action: () => { if (!authed) { go('login'); } else { /* open confirmation dialog */ } } },
  { label: 'Compliance Contacts', icon: Mail, action: () => window.open('/legal/compliance/', '_self') },
  // Remove dead rows: Privacy, Notifications, Language, Help & Feedback, About
  // Or keep them with a placeholder note: "Not configured for this deployment."
];
```

Note: `authed` is available from `useAuth()`.

---

### 5.16 `frontend/sample_frontend/src/pages/Login.tsx`

**Changes (detailed):**
1. Expand `Props` interface if needed (not required).
2. Add `dob` input: `type="date"`, optional. Add to `form` state.
3. Add consent checkbox: required, label includes link to `/legal/compliance/` (or a new `/legal/privacy/` page). The checkbox must be checked (`form.consent_accepted` must be `true`) before submit.
4. Add `terms_version`: hidden input with value `'v1.0'` or a select showing allowed versions (`TERMS_VERSIONS` from env — for simplicity, just `'v1.0'` at v1).
5. If `dob` is entered, compute age in `submit()` before calling API. If `< 18`:
   - Show a standard template warning message (inline, not blocking)
   - Require `parent_email` field (show conditional input)
   - The `submit()` passes `parent_email` to `register()`
6. The `submit()` passes the expanded data:
   ```typescript
   await register(form.email, form.username, form.password, {
     dob: form.dob,
     consent_accepted: form.consent_accepted,
     terms_version: form.terms_version,
     parent_email: form.parent_email,
   });
   ```
7. Note: The backend `RegisterSerializer` validates `consent_accepted` (`required=True`) and `terms_version` (`required=True`). If the frontend doesn't send them, the API will return 400.

---

## 6. New Components & Pages (New Files Required)

### 6.1 `frontend/sample_frontend/src/pages/GrievancePage.tsx` (New)

**Purpose:** Auth-only grievance submission page.
**Route:** `/grievance/`
**Form fields:** `subject` (≤ 200 chars), `description` (text), `user_email` (optional, pre-filled from user profile).
**Behavior:**
- Shows user info (username, email) from `useAuth()`
- Submits to `grievanceAPI.createGrievance()`
- Shows response (`grievance_id`, `status='received'`, `acknowledgment_due`, message)
- Includes link back to `/settings/`

---

### 6.2 `frontend/sample_frontend/src/pages/CompliancePage.tsx` (New)

**Purpose:** Public compliance contacts page (uses `ComplianceContactView`).
**Route:** `/legal/compliance/`
**Behavior:**
- Fetches `/legal/compliance/`
- Displays officer names, emails, response-time commitments (from backend settings: `GRIEVANCE_OFFICER_EMAIL`, `COMPLIANCE_OFFICER_EMAIL`, `NODAL_CONTACT_EMAIL`)
- Includes standard regulatory text (short version of DPDP/IT Rules obligations)
- Link back to settings/foot

---

### 6.3 `frontend/sample_frontend/src/pages/TakedownPage.tsx` (New)

**Purpose:** Standalone copyright takedown request page.
**Route:** `/legal/takedown/`
**Form fields:** `clip_id` (optional, pre-filled if coming from clip action), `reason` (required, dropdown or text), `requester_email` (optional, default user email).
**Behavior:**
- Submits to `legalAPI.submitTakedown()` (POST `/legal/takedown/`)
- Shows standard confirmation: "Request recorded. Acknowledgment within 24 hours."
- Includes standard legal notice text (copyright acknowledgment reminder)
- Link to `/legal/compliance/` for grievance/contact info

---

### 6.4 `frontend/sample_frontend/src/pages/DataSubjectAccessPage.tsx` (New)

**Purpose:** Data export page.
**Route:** `/data-subject/access/`
**Behavior:**
- Auth-only (redirect to `/login` if not authenticated)
- Fetches `/data-subject/access/`
- Displays results in structured sections: Profile, Interactions, Comments, Shares, Clips
- Includes download/export option (JSON download via `URL.createObjectURL`)
- Link back to settings

---

### 6.5 `frontend/sample_frontend/src/pages/DataSubjectErasurePage.tsx` (New)

**Purpose:** Erasure request page.
**Route:** `/data-subject/erasure/`
**Behavior:**
- Auth-only
- Shows standard DPDP §14 message: "Submitting an erasure request initiates a 30-day cooling-off period. After that period, your personal data will be deleted."
- Requires confirmation checkbox (`confirm=true`) before submitting
- Shows cooling-off countdown if request already exists
- Link back to settings

---

### 6.6 `frontend/sample_frontend/src/components/ReportModal.tsx` (New)

**Purpose:** Report clip/comment modal.
**Used by:** `ReelCard` (for clips), `CommentSheet` (for comments)
**Form:**
- Reason dropdown: `['CSAM', 'Hate Speech / Abuse', 'Obscenity', 'Terrorism / Extremism', 'Copyright Infringement', 'Spam / Fraud', 'Other']`
- Description text (optional, ≤ 500 chars)
- User email (optional, default from `useAuth()`)
- Submit button → POST to `/clips/{id}/report/` or `/comments/{id}/report/`

**Note:** The `Report` endpoint does not exist. This must be added to the backend (`urls.py` + `views/content.py` or `views/comments.py` + `models.py`). The implementation plan includes this as a required dependency.

---

### 6.7 `frontend/sample_frontend/src/components/RegionToast.tsx` (New)

**Purpose:** One-time region banner.
**Behavior:**
- Checks session storage (`sessionStorage.getItem('ef_region_checked')`)
- If not set, shows a small toast banner at the top of `AppShell`
- Message: "This deployment is configured outside the India region (`ap-south-1`). For production compliance with DPDP cross-border rules, deploy with `AWS_S3_REGION_NAME=ap-south-1`."
- Sets `sessionStorage.setItem('ef_region_checked', '1')`
- Uses standard `NetworkBanner` styling but with info color (`var(--sage)`) instead of error/success

---

### 6.8 `frontend/sample_frontend/src/components/common/FooterLegalLinks.tsx` (New)

**Purpose:** Footer with regulatory links.
**Behavior:**
- Small text footer displayed at the bottom of every page (within `AppShell` or at the bottom of the page content)
- Links: `Compliance` (`/legal/compliance/`), `Grievance` (`/grievance/` — auth redirect handled by route), `Takedown` (`/legal/takedown/`), `Terms`, `Privacy`
- Minimal styling (11px font, `var(--outline)`, uppercase, letter spacing)
- Does NOT replace `BottomNav` — it's a separate footer element

---

## 7. Tests & Validation Strategy

### 7.1 Existing Tests (What They Guarantee)

From `test_auth_regulatory.py`:
- `ConsentAudit` model exists and registration with consent creates audit row (`test_register_with_consent_creates_consent_audit`)
- Registration without `consent_accepted` returns 400 (`test_register_requires_consent_accepted`)
- `dob` and `is_minor` computation works (`test_user_has_dob_and_computed_is_minor`)
- `/legal/compliance/` returns JSON with officer info (`test_compliance_contact_returns_json`)
- `/grievance/` creates grievance (`test_create_grievance`)
- `/data-subject/access/` requires auth (`test_access_requires_auth`)
- `AuditLog` model exists (`test_audit_log_model_exists`)

**What these tests do NOT guarantee:**
- The frontend sends the correct fields (`Login.tsx` doesn't test the expanded form)
- The upload form sends `license_type`, `copyright_owner_name`, `copyright_acknowledgement` (no frontend tests exist for upload fields)
- The grievance/data-subject/takedown endpoints receive proper requests from the frontend (no integration tests between frontend and these endpoints)
- The `ConsentAudit.withdrawn_at` endpoint works (`/consent/withdraw/` endpoint not implemented yet)
- The `Report` endpoint exists or works (`Report` endpoint missing)

---

### 7.2 New Tests Required (Per Requirement Area)

**For Consent/Age Gate (`FR-IN-CON-*`, `FR-IN-AGE-*`):**
- `Login.tsx`: Verify `register()` sends expanded fields including `consent_accepted`, `terms_version`, `dob`, `parent_email` (if minor computed by frontend)
- `Login.tsx`: Verify `dob` input appears, and standard warning message appears when `< 18`
- `Login.tsx`: Verify `consent_accepted` checkbox is required (submit fails if false)
- Integration test: `POST /auth/register/` with `dob=2010-01-01`, `parent_email='parent@test.com'`, `consent_accepted=True`, `terms_version='v1.0'` → 201 + `ConsentAudit` created

**For Upload (`FR-IN-CPY-*`):**
- `Upload.tsx`: Verify `license_type` dropdown renders with all options
- `Upload.tsx`: Verify `copyright_owner_name` input accepts text
- `Upload.tsx`: Verify `copyright_acknowledgement` checkbox is required (submit blocked if false)
- `Upload.tsx`: Verify tags input is removed (no tags in `FormData`)
- Integration test: `POST /clips/` with `license_type='CC-BY'`, `copyright_owner_name='Test'`, `copyright_acknowledgement=True` → 202 + clip with fields set

**For Grievance (`FR-IN-GRV-*`):**
- `GrievancePage`: Verify form renders (subject, description, user_email)
- `GrievancePage`: Verify `POST /grievance/` with data → 201 + grievance_id
- `GrievancePage`: Verify unauthenticated access redirects to `/login` (if auth-only) or allows public access (if public — but user selected auth-only, so redirect expected)
- Integration: Grievance response includes `acknowledgment_due` (24h from now)

**For Compliance (`FR-IN-GRV-*` — Compliance Contacts):**
- `CompliancePage`: Verify `/legal/compliance/` data renders (names, emails from `.env`/settings)
- `FooterLegalLinks`: Verify links navigate correctly

**For Takedown (`FR-IN-MOD-*` — Takedown Request):**
- `TakedownPage`: Verify form renders (`clip_id`, `reason`, `requester_email`)
- `TakedownPage`: Verify `POST /legal/takedown/` with data → 201 + `status='received'`
- Integration: `TakedownRequest` model row created with `status='pending'`

**For Data Subject Rights (`FR-IN-DSR-*`):**
- `DataSubjectAccessPage`: Verify `/data-subject/access/` returns categories (profile, interactions, comments, shares, clips)
- `DataSubjectAccessPage`: Verify unauthenticated access returns 401
- `DataSubjectErasurePage`: Verify `POST /data-subject/erasure/` with `confirm=True` creates `DataSubjectRequest` with 30-day `cooling_off_until`
- Integration: Existing request with `status='pending'` and `cooling_off_until` in future returns 403 with remaining days message

**For Consent Withdrawal (`FR-IN-CON-*`):**
- `SettingsPage`: Verify consent withdrawal button appears and opens confirmation
- Integration: `POST /auth/consent/withdraw/` creates/updates `ConsentAudit` with `withdrawn_at` set

**For Report (`FR-IN-MOD-*` — Content Moderation):**
- `ReelCard`: Verify report button appears
- `ReelCard`: Verify click opens `ReportModal`
- `ReportModal`: Verify `POST /clips/{clip.id}/report/` submits correctly
- `CommentSheet`: Verify report button appears for comments
- `CommentSheet`: Verify `POST /comments/{comment.id}/report/` submits correctly

**For Region Banner (`FR-IN-REG-*`):**
- `AppShell`: Verify region toast appears once per session (checks sessionStorage)
- `AppShell`: Verify toast uses info/sage color (not error)
- Integration: Toast message includes region info

**For Age Gate (`FR-IN-AGE-*`):**
- `Login.tsx`: Verify standard warning message appears when `dob` indicates `< 18`
- `Login.tsx`: Verify `parent_email` input appears conditionally
- Integration: Registration with `dob=1990-01-01` sets `is_minor=False`; with `dob=2010-01-01` sets `is_minor=True` and requires `parent_email`

---

## 8. Risks, Mitigations & Open Dependencies

### 8.1 Critical Risk: Missing `/clips/{id}/report/` and `/comments/{id}/report/` Endpoints

**Status:** The `Report` model exists (`models.py:339-346`) but there are **NO endpoints** (`urls.py` does not include them, `views/content.py` and `views/comments.py` have no report actions).

**Mitigation:** This must be added to the backend before the frontend report feature can work. The user approved the design but must approve this backend addition. In the Implementation Plan below, this is listed as a **required dependency** — the frontend changes for report buttons can be added but will return 404 until the endpoint exists.

**Alternative (if user wants to defer backend endpoint):** The frontend can include the report buttons but with a placeholder behavior (e.g., open a `ReportModal` that shows "Report submitted (pending backend endpoint)" and logs locally). I do NOT recommend this — it creates broken user expectations. Better to add the endpoint first.

---

### 8.2 Critical Risk: `ConsentAudit` Endpoint Missing

**Status:** The `ConsentAudit` model exists (`models.py:60-76`) and the `RegisterSerializer` creates it. There is NO endpoint for consent withdrawal (`/auth/consent/withdraw/` or `/consent/withdraw/`).

**Mitigation:** Add endpoint to `urls.py` (new path, new view) before frontend consent withdrawal is fully functional. The endpoint is simple: `POST` with empty body, creates/updates `ConsentAudit` with `withdrawn_at = timezone.now()`.

---

### 8.3 High Risk: `RegisterSerializer` Consent Enforcement

**Status:** The `RegisterSerializer` (`serializers.py:405-491`) requires `consent_accepted` (`required=True`). The current `Login.tsx` does NOT send it. If the user tries to register with the current frontend, the backend will return 400 (`consent_accepted` missing). This is a launch-blocking gap.

**Mitigation:** The frontend must send `consent_accepted=True` and `terms_version='v1.0'` (or the allowed version) with every registration request. This is part of Phase A.

---

### 8.4 High Risk: Upload Form Copyright Acknowledgment

**Status:** `AudioUploadSerializer.validate()` (line 178-191) requires `copyright_acknowledgement=True`. The current `Upload.tsx` does NOT include this checkbox. Any upload attempt will fail with 400.

**Mitigation:** Add checkbox to `Upload.tsx` and include in `FormData` (`copyright_acknowledgement: true`). This must be done in Phase A.

---

### 8.5 Medium Risk: Grievance Endpoint Auth Enforcement (User Selected Auth-Only)

**Status:** `GrievanceCreateView` (`grievance.py`) uses `permission_classes = [permissions.AllowAny]`. The user selected auth-only for the frontend. If we enforce auth at the frontend layer (redirect unauthenticated users to `/login`), the backend will still accept public requests (which is fine — the backend supports both). The frontend just filters access.

**Mitigation:** In the frontend `GrievancePage`, check `authed` from `useAuth()`. If not authenticated, redirect to `/login` or show a login prompt message. No backend change needed.

---

### 8.6 Medium Risk: Takedown Page Route (`/legal/takedown/`)

**Status:** The backend endpoint exists (`urls.py` line 66: `path('legal/takedown/', ...)`). The route `/legal/takedown` needs a new `TakedownPage` in the frontend. This is straightforward.

---

### 8.7 Low Risk: `report` Endpoint Dependency (If Deferred)

**Status:** As noted above. If deferred, the `ReelCard` report button and `CommentSheet` report button will trigger API calls that return 404. This is acceptable only as a temporary state during Phase A if the endpoint is added by Phase B. The Implementation Plan should include adding the endpoint to the backend in Phase B.

---

## 9. Alternative Approaches & Tradeoffs

### 9.1 Alternative: Separate `FRONTEND-INDIA-REQ.md` File (Not Updating Main Spec)

**Rejection reason:** The user selected "Full compliance spec added to FRONTEND-REQUIREMENTS.md". Adding a separate file would fragment the spec and make it harder for future agents to find requirements. The main document already references `docs/INDIA-REGULATORY-READINESS.md`; embedding the frontend requirements directly connects the regulatory audit to the implementation spec.

---

### 9.2 Alternative: Hard Age Gate (Block Registration for <18)

**Rejection reason:** The user selected "Soft age gate (warning only)". The backend (`RegisterSerializer`) validates `dob` and requires `parent_email` for minors but does NOT block account creation (`RegisterSerializer.create()` creates the user regardless). A hard block at the frontend would conflict with the backend contract (which allows registration with `parent_email` provided). The standard warning aligns with the soft gate.

---

### 9.3 Alternative: 22-Language Full Localization (Not English-Only Launch)

**Rejection reason:** The user selected "English-only at launch". The DPDP requirement for 22 languages applies to the privacy notice and terms — not necessarily the entire app UI. A future `FR-IN-LOC-*` requirement can document the i18n scaffolding (language switcher, `.po`/`.mo` files) without blocking Phase A.

---

### 9.4 Alternative: Persistent Region Banner (Not One-Time Toast)

**Rejection reason:** The user selected "One-time toast". A persistent banner would clutter the UI permanently. A toast is sufficient for a deployment-time operational note.

---

## 10. Implementation Plan (Concise, Final)

### Agreed Architecture
- New Section 12 in `docs/FRONTEND-REQUIREMENTS.md`
- Auth-only grievance (`GrievancePage` + `GrievanceForm` inline/modal)
- Standalone takedown (`/legal/takedown/` route + `TakedownPage`)
- Consent withdrawal (`POST /auth/consent/withdraw/` endpoint + Settings button)
- Soft age gate (standard warning in `Login.tsx` + conditional `parent_email` input)
- Region toast (one-time, info color, session-storage controlled)

### Files & Symbols to Modify

**Documentation:**
- `docs/FRONTEND-REQUIREMENTS.md` — add Section 12

**Frontend Components (New):**
- `pages/GrievancePage.tsx`
- `pages/CompliancePage.tsx`
- `pages/TakedownPage.tsx`
- `pages/DataSubjectAccessPage.tsx`
- `pages/DataSubjectErasurePage.tsx`
- `components/ReportModal.tsx`
- `components/RegionToast.tsx`
- `components/common/FooterLegalLinks.tsx`

**Frontend Components (Modified):**
- `pages/Login.tsx` — expanded form, consent checkbox, DOB, age warning, parent_email
- `pages/Upload.tsx` — remove tags input; add license dropdown, copyright fields, acknowledgment checkbox
- `pages/Settings.tsx` — replace dead rows with grievance/data access/erasure/consent/compliance links
- `pages/Profile.tsx` — no major changes (already correct per audit fix)
- `components/feed/OnboardingModal.tsx` — add privacy/compliance links, standard regulatory text
- `components/audio/ReelCard.tsx` — add report button; new `ReportModal` integration
- `components/comments/CommentSheet.tsx` — add report button; `ReportModal` integration
- `components/sharing/ShareModal.tsx` — fix `PATCH` → `POST` for `markRead` (existing gap, should be included)
- `app/router.tsx` — new routes (`/grievance/`, `/legal/compliance/`, `/legal/takedown/`, `/data-subject/access/`, `/data-subject/erasure/`)
- `stores/auth.tsx` — add `authAPI.logout()` call in `logout()`; expand `register()` parameters
- `api/client.ts` — add `consentAPI`, `grievanceAPI`, `dataSubjectAPI`, `legalAPI`; expand `authAPI.register()`
- `types/index.ts` — add regulatory interfaces

**Backend Endpoints (New — Required Dependency):**
- `backend/app/urls.py`: `path('auth/consent/withdraw/', ...)` (consent withdrawal)
- `backend/app/urls.py`: `path('clips/<uuid:clip_id>/report/', ...)` (clip report)
- `backend/app/urls.py`: `path('comments/<uuid:comment_id>/report/', ...)` (comment report)
- `backend/app/views/auth.py`: `ConsentWithdrawalView` (new)
- `backend/app/views/content.py`: `report_clip` action (new)
- `backend/app/views/comments.py`: `report_comment` action (new)

**Note on Backend Endpoints:** The design document includes the new backend endpoints as dependencies. The user must confirm whether these should be implemented in this session or deferred. The frontend code for report buttons depends on these endpoints.

---

### Behavioral / Data-Flow Changes

| Flow | Before | After |
|---|---|---|
| Registration (`Login.tsx` → `authAPI.register`) | `{email, username, password}` | Expanded with `dob?`, `consent_accepted`, `terms_version`, `parent_email?`, `license_type?` (license fields for upload, not register — clarify: consent fields are for registration; copyright fields are for upload) |
| Upload (`UploadPage` → `client.ts` → `AudioUploadSerializer`) | `{original_file, title, category}` + dead tags | `{original_file, title, category, license_type?, copyright_owner_name?, copyright_acknowledgement}` |
| Grievance (`GrievancePage` → `client.ts`) | Not implemented | Auth-only form; POST `/grievance/`; shows acknowledgment timeline |
| Data Subject Access (`DataSubjectAccessPage`) | Not implemented | Auth-only; GET `/data-subject/access/`; displays categories |
| Data Subject Erasure (`DataSubjectErasurePage`) | Not implemented | Auth-only; POST `/data-subject/erasure/` (confirm=true); shows 30-day cooling-off |
| Consent Withdrawal (`Settings` button) | Not implemented | POST `/auth/consent/withdraw/`; updates `ConsentAudit.withdrawn_at` |
| Report (`ReelCard` / `CommentSheet`) | Not implemented | Opens `ReportModal`; POST `/clips/{id}/report/` or `/comments/{id}/report/` |
| Compliance (`FooterLegalLinks`) | Not implemented | Links to `/legal/compliance/`; shows officer info from backend settings |
| Takedown (`TakedownPage`) | Not implemented | Public page at `/legal/takedown/`; POST `/legal/takedown/` with `clip_id`, `reason`, `requester_email` |
| Region Banner (`AppShell`) | Not implemented | One-time toast; checks sessionStorage; info/sage color |

---

### Tests & Validation Strategy

1. **Unit tests (frontend):** Add basic component render tests for `GrievancePage`, `TakedownPage`, `DataSubjectAccessPage`, `DataSubjectErasurePage`, `ReportModal`, `RegionToast`. These are new components — basic render assertions are sufficient.
2. **Integration tests (frontend + backend):**
   - `Login.tsx`: Submit expanded `register()` form → verify 201 response
   - `Upload.tsx`: Submit form with `copyright_acknowledgement=True` → verify 202 response; submit without → verify 400
   - `GrievancePage`: Submit grievance form → verify 201 + `grievance_id`
   - `DataSubjectAccessPage`: Authenticated GET `/data-subject/access/` → verify categories in response
   - `DataSubjectErasurePage`: POST with `confirm=True` → verify `DataSubjectRequest` created with `cooling_off_until`
3. **Backend tests (if endpoints added):**
   - `test_consent_withdrawal`: POST `/auth/consent/withdraw/` with auth → verifies `ConsentAudit` updated
   - `test_clip_report`: POST `/clips/{id}/report/` → verifies `Report` created
   - `test_comment_report`: POST `/comments/{id}/report/` → verifies `Report` created
4. **Regression checks:**
   - `test_auth_regulatory.py` continues to pass (registration, consent audit, grievance, access)
   - `FRONTEND-REQUIREMENTS.md` Section 12 is complete and references actual backend endpoints
   - No existing frontend behavior broken (feed, upload, profile, comments, sharing)

---

### Risks & Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| `/clips/{id}/report/` endpoint missing (backend) | **Critical** | Add endpoint in Phase B (see Implementation Plan). Defer `ReportModal` integration if endpoint not ready; include placeholder behavior (show toast "Report feature requires backend endpoint — coming in next release") or skip the button entirely until endpoint exists. |
| `/comments/{id}/report/` endpoint missing (backend) | **Critical** | Same as above. |
| `/auth/consent/withdraw/` endpoint missing (backend) | **High** | Add endpoint in Phase A (simple — creates `ConsentAudit` with `withdrawn_at`). Can be added quickly. |
| Registration fails (400) because `Login.tsx` doesn't send `consent_accepted` | **Critical** | Phase A must include `Login.tsx` form expansion. This is launch-blocking. |
| Upload fails (400) because `Upload.tsx` doesn't send `copyright_acknowledgement` | **Critical** | Phase A must include `Upload.tsx` checkbox. Launch-blocking. |
| `GRIEVANCE_OFFICER_EMAIL` / `COMPLIANCE_OFFICER_EMAIL` / `NODAL_CONTACT_EMAIL` not configured in `.env` | **Medium** | The `ComplianceContactView` (`views/legal.py`) uses `getattr(settings, ...)` with defaults (`'Not configured'`). The frontend should handle empty values gracefully (show "Not configured" message or hide email links). |
| `RegisterSerializer.validate()` computes age incorrectly for edge cases (leap year, February 29) | **Low** | The backend uses standard Python `date` arithmetic. The frontend can either rely on the backend's age computation or replicate it. Replicating it exactly avoids double validation errors but increases frontend complexity. Recommendation: rely on backend validation (send `dob`, let backend compute `is_minor`). The frontend only shows a warning if the user indicates they are under 18 (optional `dob` field). |
| Soft age gate may not fully satisfy DPDP auditors | **Medium** | Documented as a gap: the soft gate (warning) does not enforce `parent_email` at the frontend layer (the backend requires it, but the frontend could skip it). The design relies on the backend's `RegisterSerializer.validate()` to enforce the `parent_email` requirement when `dob` indicates `< 18`. If the user doesn't provide `dob`, no parent email is required — which is acceptable for a soft gate (no tracking of behavioral data without consent from parent). |
| `FRONTEND-REQUIREMENTS.md` Section 12 is very long (~15-20 new subsections) | **Low** | The existing spec is 1868 lines. Adding one new section is manageable. Organize by regulatory area (Consent, Age Gate, Grievance, DSR, Moderation, Copyright, Identity/Audit) to match the regulatory doc structure. |

---

### Decisions Established During Discussion

1. **Grievance form:** Auth-only (not public). Frontend checks `authed`.
2. **Takedown form:** Standalone `/legal/takedown/` page.
3. **Consent withdrawal:** Only sets `withdrawn_at` (no soft-delete triggered).
4. **Age gate:** Standard regulatory warning text (soft gate).
5. **Region banner:** One-time toast (info/sage color, sessionStorage-controlled).
6. **Localization:** English-only at launch; 22-language as documented future gap (`FR-IN-LOC-1`).
7. **Report endpoint dependency:** Must be added to backend; listed as dependency in Implementation Plan.
8. **Implementation order:** Phase A (consent, age, upload copyright, settings regulatory links) first; Phase B (grievance, data-subject, report, moderation, compliance, footer) second; Phase C (region banner, onboarding text) third; Phase D (i18n scaffolding) last.

---

### Missing Information / Ambiguities Identified

1. **Standard template text for age gate warning:** User said "use a standard template" but did not provide exact text. I'll use a standard DPDP-compliant template text in the design (line from `Login.tsx` description above). If the user wants different text, it can be updated easily.
2. **`FRONTEND-REQUIREMENTS.md` Section number:** Should Section 12 be added after Section 11 (Implementation Priorities) or integrated differently? The user selected adding to the existing file. Section 12 is the logical next number.
3. **New endpoint for `/auth/consent/withdraw/`:** The endpoint doesn't exist in `urls.py`. It must be added. The view is simple (`ConsentWithdrawalView`). The design assumes it will be added.
4. **`FR-IN-CON-*` requirement numbering:** There will be many consent-related requirements (consent checkbox, consent withdrawal, consent audit display, consent version, privacy link). I will number them sequentially (`FR-IN-CON-1` through `FR-IN-CON-7`) to avoid confusion.
5. **What about children under 13?** DPDP §9 applies to "children" (under 18). The `RegisterSerializer` handles `< 18` uniformly. No separate flow for `< 13` (the user selected a single standard template). This aligns with the soft gate design.

---

This analysis establishes a shared, precise understanding. Before any file edits, please confirm:

1. Should the analysis proceed to the final Implementation Plan (as presented below in the next step)?
2. Should the `FRONTEND-INDIA-REQ.md` document be written as a new file (`docs/FRONTEND-INDIA-REQ.md`) or added to the existing `FRONTEND-REQUIREMENTS.md`? The user selected the existing file.
3. Should I include the backend endpoint additions (`/auth/consent/withdraw/`, `/clips/{id}/report/`, `/comments/{id}/report/`) in this session's work, or should I document them as dependencies only? The Implementation Plan treats them as dependencies that must be completed before the corresponding frontend features work fully.

Once you confirm, I will present the final Implementation Plan (as a concise summary) and then proceed to write the Section 12 content.
