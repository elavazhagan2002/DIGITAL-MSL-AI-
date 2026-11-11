# Digital MSL production guide

## Authentication and session lifecycle

Portal login returns:

- `session_id` — login session (`sess_*`)
- `access_token` — **same value as `session_id`**, used as a Bearer token
- `token_type`: `bearer`

Send on protected Digital MSL API calls:

```http
Authorization: Bearer <session_id>
```

Optional legacy headers (must match session user): `X-DMSL-Session-Id`, `X-DMSL-User-Id`.

**Middleware** (`backend/app.py`) enforces Bearer sessions on all `/api/v1/digital-msl/*` routes except:

- `GET /config`, `GET /ops/status`
- `POST /portal/login`, `POST /portal/register`
- `POST /channels/email/inbound`, `POST /channels/whatsapp/inbound`

Logout: `POST /portal/logout` with Bearer token — sets `ended_at` and writes a `logout` audit event.

Identity for authorization is always taken from the **session row**, not from client-supplied `user_id` on protected chat routes.

## Legacy repository upload security

`POST /api/v1/repositories/{repository_id}/documents/upload` (and related repository routes under `/api/v1/repositories/` and `/api/v1/documents/`) require the **same portal Bearer session** via `resolve_api_principal` (`backend/routes/api_principal.py`).

- Default: **`RESEARCH_CHAT_ALLOW_DEV_AUTH` is not enabled** — unauthenticated uploads return **401**.
- Optional local dev only: set `RESEARCH_CHAT_ALLOW_DEV_AUTH=true` to allow the synthetic dev user when no Bearer token is sent (not for production).
- Server checks repository assignment (`assert_repository_access_for_principal`) before DMS upload or folder overlay writes.
- Portal UI uses `portalAuthHeaders()` on both DMSL upload and legacy repository upload helpers.

## Role and repository access

| Role | Access |
|------|--------|
| Admin | TA activation, audit event log, people, assignments |
| MSL | Assigned TAs/repos, folders, queue, portal document upload |
| Doctor | Assigned repos only (max 3 at register); inactive TAs blocked |

`assert_repository_access` checks assignment, MSL membership, and **active** therapeutic area flag.

## Conversation vs session

- **session_id** (`sess_*`): one login until logout.
- **conversation_id** (`conv_*`): one chat thread; **New session chat** creates a new `conv_*` under the same login session.

## Audit events (`dmsl_audit_events`)

Append-only. Admin-only: `GET /audit/events` (requires admin Bearer session).

Recorded actions include: login, logout, doctor_register, ta_activate/deactivate, folder create/rename/delete, document_upload, agent decisions, case_draft_save, case_send, self_assign, and related hooks.

Admin UI: **Sessions** tab (conversations) and **Event log** tab.

## Therapeutic areas and folders

- `PUT /therapeutic-areas/{repository_id}/active` `{ "active": true|false }` (admin).
- Inactive areas: blocked for new doctor registration and repo-scoped doctor/MSL use.
- Nested folders: `parent_folder_id` on create; rename/delete APIs on `/therapeutic-areas/folders/{folder_id}`.
- `GET /therapeutic-areas/{repository_id}/folder-stats` — document counts per folder (overlay table).

### Repo tree UI (`RepoTreePanel.tsx`)

- Root + ancestor **breadcrumbs** (by `parent_folder_id`, clickable navigation).
- Create folder (root or nested), **rename** dialog with validation, **delete** confirmation.
- Delete blocked when subfolders or documents exist (server returns error; UI shows warning).
- Document counts per folder when `docCounts` prop is supplied (`DocumentsPanel`, `TherapeuticAreasPage`).

## Document upload (portal)

Primary route:

`POST /therapeutic-areas/{repository_id}/documents/upload` (multipart `files`, optional `folder_id`):

- Requires Bearer session + repo access.
- Uploads via DMS, assigns `dmsl_document_folders`, writes `document_upload` audit events (metadata only, no file contents).

Legacy route (same auth as above):

`POST /api/v1/repositories/{repository_id}/documents/upload` — optional `folder_id` form field; portal clients send Bearer via `repositoryService.ts`.

## Case lifecycle

Open → Assigned → In progress → Resolved (UI labels). MSL reply sets **Resolved** and `case_send` audit.

## OpenAPI security specification

- **Live schema:** `GET /openapi.json` (FastAPI `custom_openapi` in `backend/app.py`).
- **Helper:** `backend/digital_msl/openapi_documentation.py` — `DMSLBearer` scheme, `path_requires_bearer`, `apply_openapi_security`.
- Protected prefixes: `/api/v1/digital-msl/` (except documented public routes), `/api/v1/repositories/`, `/api/v1/documents/`.
- Resource-level authorization (repository assignment, admin role) is described in operation text; OpenAPI `security` alone does not encode those rules.

## Performance

`AgentResponse.timing`: `scope_ms`, `retrieve_ms`, `draft_ms`, `validate_ms`, `total_ms`.

Smoke: `python scripts/smoke_digital_msl.py` (login + **5** scenarios, benchmark in `scripts/smoke_digital_msl_results.json`).

### Last live smoke run (2026-10-28, backend `http://127.0.0.1:8087`)

| Metric | Value |
|--------|--------|
| Scenarios executed | 5 |
| Passed | 4 |
| Failed | 1 (`sourced_single` — expected `respond`, got `escalate_to_msl` / `NO_SUPPORTED_CLAIMS`; IKM retrieval variance, safe escalation not unsafe answer) |
| Safety scenarios | `msl_redirect` passed (`SAFETY_INDIVIDUAL_ADVICE`); `negative_refuse` passed (`OUT_OF_SCOPE`) |
| avg_ms | 44977 |
| median_ms | 46745 |
| p95_ms | 48122 |

## Database / migrations

Schema evolves via `store.init_store()` idempotent `ALTER TABLE` statements (sessions, audit, folders, `active_flag`, document-folder overlay). No destructive reset. Set `DIGITAL_MSL_DATABASE_URL` for production SQLite/Postgres path.

**Folder rename fix:** audit events for `folder_rename` are written outside the folder update transaction to avoid SQLite lock errors under concurrent access.

## Deployment

1. Install `backend/requirements.txt` (includes `email-validator>=2` for HTTP tests/FastAPI).
2. Set env: `DIGITAL_MSL_DATABASE_URL`, IKM/DMS URLs, LLM keys. Do **not** set `RESEARCH_CHAT_ALLOW_DEV_AUTH` in production.
3. Start: `cd backend && uvicorn app:app --host 127.0.0.1 --port 8087`
4. Frontend: `npm run dev` (proxies `/api` to 8087) — portal stores session id and sends Bearer on DMSL calls (`dmslFetch`).

## Tests

```bash
set PYTHONPATH=backend
python -m pytest tests/ -q
```

**Last run (2026-10-28):** **52 passed** — `test_digital_msl.py`, `test_portal_http_auth.py`, `test_openapi_and_upload.py` (OpenAPI bearer alignment, legacy upload 401, folder rename/delete rules, protected-route OpenAPI coverage).

**Frontend:** `npm run build` — succeeded (Vite production build).

## Browser verification

Partial **MCP browser run (2026-10-28)** against `http://127.0.0.1:5173` + backend `:8087`:

| Step | Result |
|------|--------|
| Doctor login (`doctor@digital-msl.local`) | **Pass** — chat shell loads |
| Assigned therapeutic area / repo combobox | **Pass** — `Eye_Core_Retina-222e` |
| Individual-patient chat → Physical MSL handoff | **Pass** — UI shows Physical MSL routing copy (not library answer) |
| Folder breadcrumb / rename / delete / upload in UI | **Not exercised in browser** — use MSL **Load Clinical/Medical Document** or admin TA pages manually |
| Admin Event log / Sessions | **Not exercised in browser** — log in as `admin@digital-msl.local` |
| Logout + revoked session | **Not exercised in browser** |

Automated browser E2E is **not** in CI. Repeat the table above manually after deploy.

## Known limits

- IKM retrieval variance (e.g. Protocol T / `sourced_single` may escalate with `NO_SUPPORTED_CLAIMS` instead of answering — treated as acceptable safe behavior when evidence is missing).
- No IKM branch entity — nested repo folders substitute.
- Some acceptance items remain **partial** (global “Repo” branding vs “Therapeutic area” wording in a few screens).
- Chat clarify edge cases for repeated short greetings may still need UX polish.

## Doctor registration, verification and consent

**Registration** (`POST /portal/register`; pick-lists, consent form and compliance notice come from the public `GET /portal/register/options`) collects what an NMR search uses: name as registered, medical registration number, registering council (a DB table, `dmsl_councils`, seeded with NMC and the State Medical Councils), year of registration, optional NMC Unique ID (`DIGITAL_MSL_NMR_ID_REQUIRED=true` once NMR is live), basic and highest qualification, speciality, hospital, city, state, PIN code, mobile (E.164) and an optional registration certificate (`POST /portal/certificate`: PDF/PNG/JPEG by magic bytes, 5 MB, stored Fernet-encrypted under `backend/uploads/certificates`, scanned by ClamAV when `DIGITAL_MSL_CLAMAV_HOST` is set, otherwise recorded as `unscanned`). Registration returns **no session**. Duplicate email, registration number or mobile all give one neutral 409 message.

**Contact verification.** A 6-digit code is sent to the email and to the mobile (WhatsApp; set `DIGITAL_MSL_WHATSAPP_OTP_TEMPLATE` to your approved template in production). Codes are hashed at rest, expire after 5 minutes, allow 5 attempts, and can be resent after 60 s, 3 times per hour. `POST /portal/register/verify` and `/register/resend` are public and rate-limited. Until both are confirmed, a correct-password login returns 403 `CONTACT_NOT_VERIFIED`. The number is linked for WhatsApp only after it is proven. `DIGITAL_MSL_DEV_ECHO_OTP=true` returns the codes in the register response for local development; **never enable it in production**. OTP bodies are never written to the outbox or logs.

**Verification workflow.** `PENDING_CONTACT_VERIFICATION` → `PENDING_REGISTRATION_VERIFICATION` → `VERIFIED | REJECTED | NEEDS_INFO`. Verification goes through a `RegistrationVerifier` (`doctor_registry.py`): `ManualVerifier` (default: an **Administrator** checks the public NMC register and records the decision) and `NmrVerifier` (stub, "not configured"; wire it once NMC API access and terms are agreed; `nmr_query()` already maps a profile to the NMR search fields). Only `VERIFIED` doctors receive clinical answers (web, WhatsApp, email); others see the "Account not verified" state. Reject / needs-info need a reason; deactivating a doctor ends their sessions and blocks every channel.

**Registered Doctors** (Physical MSL and Administrator): `GET /doctors` (page, page_size, sort, order, status, repository_id, city, q), `GET /doctors/{id}`, `GET /doctors/export.csv` (Administrator, formula-injection safe), `POST /doctors/{id}/verification`, `/deactivate`, `/reactivate` (Administrator only), `GET /doctors/{id}/certificate` (Administrator only). A Physical MSL is **read-only**, sees only doctors in their assigned therapeutic areas, and sees masked mobile numbers and no certificate. Every list view, detail view, certificate download and export is audit-logged.

**Consent.** Registration requires four separate, unticked consents: Terms of use and Privacy Notice, the healthcare-professional declaration, WhatsApp messages and email messages. Each is an append-only row in `dmsl_consents` (user, type, channel, version, SHA-256 of the wording, granted, source, IP, user agent, UTC time); withdrawal adds a row and never overwrites. Withdraw in the profile (`PUT /portal/consents`), by replying `STOP` on WhatsApp (exact word; a confirmation is sent), or with the signed unsubscribe link in every email (`GET /portal/unsubscribe?token=`, no login). Consent is enforced server-side before every WhatsApp / email send (`blocked_no_consent`); OTPs and the STOP confirmation are transactional. The wording carries `[LEGAL TO APPROVE]` markers; align with the privacy notice and the DPDP Act 2023 (purpose, data collected, retention, withdrawal, grievance contact).

## Compliance notice and safety

The versioned notice (`GET /compliance/notice`, `compliance.COMPLIANCE_NOTICE_VERSION`; text carries `[MLR TO APPROVE]` and `DIGITAL_MSL_PV_CONTACT`) is shown as an always-visible web chat banner, prefixed to the first WhatsApp reply per doctor and version, and appended to every email together with the unsubscribe link. The notice version is stored on each inbound message. Any adverse-event vocabulary (adverse event, side effect, reaction, death, vision loss, endophthalmitis, complaint, defective, ...) appends a pharmacovigilance reminder to the reply and flags the conversation once for the MSLs of that therapeutic area (audit `safety_ae_keyword_flag`). A patient-specific event or product complaint still skips the automated answer and opens an `urgent` case (`SAFETY_ADVERSE_EVENT`).

## Security controls

| Control | Setting |
| --- | --- |
| Passwords: min 12 chars with upper, lower, digit, symbol; bundled common/breached list; optional Have I Been Pwned k-anonymity check; argon2id (legacy PBKDF2 hashes verify and are upgraded on login); changing a password needs the current one | `DIGITAL_MSL_PASSWORD_MIN_LENGTH` (12), `DIGITAL_MSL_HIBP_CHECK` (false) |
| Login lockout; per-IP per-route rate limit on login, MFA, register, verify, resend, unsubscribe (429 + `Retry-After`; in-process, use a shared store if scaled out) | `DIGITAL_MSL_MAX_FAILED_LOGINS` (5), `DIGITAL_MSL_LOCKOUT_MINUTES` (15), `DIGITAL_MSL_AUTH_RATE_LIMIT_PER_MINUTE` (10) |
| TOTP MFA with 8 one-time recovery codes: mandatory for Administrators, optional for Physical MSLs (`/portal/mfa/setup`, `/enable`, `/disable`, `/login/mfa`); 5-minute single-purpose step tokens | `DIGITAL_MSL_REQUIRE_ADMIN_MFA` (true) |
| Sessions: HttpOnly `dmsl_session` cookie (SameSite=Lax, Secure on https), readable `dmsl_csrf` cookie, double-submit `X-CSRF-Token` on cookie-authenticated unsafe methods (Bearer requests exempt), new session id at every login (prior session ended), idle + absolute timeout, `POST /portal/logout-all` | `DIGITAL_MSL_SESSION_IDLE_MINUTES` (30), `DIGITAL_MSL_SESSION_TTL_HOURS` (12), `DIGITAL_MSL_COOKIE_SECURE` |
| Headers on every response: CSP (`frame-ancestors 'none'`), `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, HSTS on https; `Cache-Control: no-store` on the API. Serve the SPA with its own CSP (and HTTPS) at your web tier | |
| PII at rest: registration number, mobile and certificates are Fernet-encrypted with keyed blind indexes for uniqueness; legacy plaintext rows are migrated at startup | `DIGITAL_MSL_DATA_KEY` (**set and back up in production**; otherwise a dev key file `backend/.dmsl_data_key` is generated and a warning is logged) |
| Roles: server decides the role; role checks on user, group, assignment, therapeutic-area, policy, template and case endpoints | |
| Email / WhatsApp bridge endpoints ignore a client `user_id`; optional shared secret; Meta webhook signature verified | `DIGITAL_MSL_INBOUND_SECRET`, `DIGITAL_MSL_WHATSAPP_APP_SECRET` |
| Refuse email / WhatsApp senders who are not registered doctors | `DIGITAL_MSL_ALLOW_UNREGISTERED_SENDERS=false` (default `true`) |
| Per-doctor question rate limit | `DIGITAL_MSL_RATE_LIMIT_PER_MINUTE` (12) |

**Audit trail.** `dmsl_audit_events` is append-only and hash-chained (each row stores `prev_hash` and `entry_hash` = SHA-256 over its fields, UTC time and the previous hash), with who, what, when, IP, user agent, before / after values and reason. SQLite triggers block UPDATE and DELETE; on PostgreSQL add the equivalent: `CREATE FUNCTION dmsl_audit_guard() RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'audit log is append-only'; END $$ LANGUAGE plpgsql; CREATE TRIGGER dmsl_audit_no_change BEFORE UPDATE OR DELETE ON dmsl_audit_events FOR EACH ROW EXECUTE FUNCTION dmsl_audit_guard();` and revoke UPDATE/DELETE from the app role. `GET /audit/verify-chain` (Administrator) recomputes the chain and reports the first broken entry. Logged: sign-in success and failure, registration, OTP verification, consent grants and withdrawals (including STOP and unsubscribe), verification decisions, deactivation, personal-data views, certificate downloads, exports, therapeutic-area, policy and template changes. Rows written before chaining existed are not hashed.

## Demo mode

`DIGITAL_MSL_DEMO_MODE` (default `false`) and `DIGITAL_MSL_ENVIRONMENT` (default `development`). **The app refuses to start** (lifespan and `init_store`) when demo mode is on and the environment is `production`; CI runs `python scripts/check_demo_guard.py`, which exits non-zero in that combination. In demo mode (only) startup seeds `doctor@demo.example` ("Demo Doctor", `VERIFIED`, contacts verified, all consents granted, source `demo_seed`), `msl@demo.example` ("Demo Physical MSL", owner/member of every enabled therapeutic area) and `admin@demo.example` ("Demo Administrator"), all with password `DemoMSL#2026` and `is_demo=true`; `python scripts/seed_demo.py` does the same and re-links the MSL to newly enabled areas. The demo admin skips mandatory admin MFA **only** when `is_demo` is true and demo mode is on. With demo mode off nothing is seeded, and at startup in production any `is_demo` account or legacy `@digital-msl.local` / `@demo.example` account is reported (WARNING log plus an audit event `demo_accounts_present`). `DIGITAL_MSL_SEED_DEMO_USERS` is a deprecated alias of demo mode. `DIGITAL_MSL_LEGACY_DEMO_USERS` seeds the older `@digital-msl.local` accounts and exists for the test-suite only.

**Production checklist:** `ENVIRONMENT=production`, demo mode off, `ALLOW_UNREGISTERED_SENDERS=false`, `DATA_KEY`, `WHATSAPP_APP_SECRET`, `INBOUND_SECRET`, `DEV_ECHO_OTP` off, `COOKIE_SECURE=true`, approved WhatsApp OTP template, SMTP configured, `PV_CONTACT` and `PUBLIC_BASE_URL` set, legal / MLR approval of the consent and notice wording, restrict CORS origins in `backend/app.py`, run behind HTTPS with `uvicorn --proxy-headers`.

## Therapeutic areas

The managed list (`dmsl_therapeutic_areas`: name, slug, description, enabled, sort_order, scope keywords) is administered at *Settings -> Therapeutic areas*. *Add standard therapeutic areas* (`POST /therapeutic-areas/seed-defaults`) creates ten eyecare areas as local, **disabled "Coming soon"** placeholders (`pending:<slug>` ids, no IKM calls; idempotent): Retina and Vitreous; Diabetic Retinopathy and Macular Disease; Glaucoma; Cornea and External Disease; Cataract and Refractive Surgery; Neuro-ophthalmology; Paediatric Ophthalmology and Strabismus; Oculoplastics and Orbit; Uveitis and Ocular Inflammation; Dry Eye and Ocular Surface. `PUT /therapeutic-areas/{id-or-slug}/settings` edits an area (audited with before / after); enabling a placeholder links a supplied `repository_id` or creates the IKM repository first. Registration accepts only enabled areas with a real repository. A question is in scope when it matches the retina vocabulary or a keyword of one of the doctor's areas.
