# Item C: Load Documents does not work. Diagnosis (written before any change)

Reproduced 2026-11-09 on a private backend (port 8100, demo mode, throw-away DB) with the demo Physical MSL. Nothing was sent to IKM except the read-only repository listing the page itself makes.

## 1. What the page is meant to do, and what it calls

Nav item **Load Documents** (role Physical MSL), `frontend/src/App.tsx` (`page === 'literature'`):

| Step | Code | Backend endpoint |
| --- | --- | --- |
| Which repositories exist | `useRepositories()` -> `listRepositories()` (`services/repositoryService.ts`) | `GET /api/v1/repositories` (`routes/repositories.py:35`), which calls IKM `list_repositories` through `DmsClient` |
| Which repositories this MSL may use | `App.tsx` filters the IKM list by the MSL's therapeutic areas (`GET /api/v1/digital-msl/therapeutic-areas`) | portal DB |
| Search literature and import (PubMed / ClinicalTrials.gov) | `MedicalLiteratureImport.tsx` | `POST /api/v1/medical-literature/search`, `/import` |
| Documents already loaded | `DocumentsPanel.tsx` | `GET /api/v1/repositories/{id}/documents` (IKM) |
| Upload | `DocumentsPanel.tsx` | `POST /api/v1/digital-msl/therapeutic-areas/{id}/documents/upload` (`digital_msl/router.py:1582`) -> IKM `/v1/documents/upload`; only `uploaded_by` and `source` are sent as metadata |

The page cannot render anything until `GET /api/v1/repositories` succeeds: on failure it shows one error card, on success it needs an IKM repository that matches one of the MSL's therapeutic areas.

## 2. The failing call (evidence)

```
GET http://127.0.0.1:8100/api/v1/repositories   (Authorization: Bearer <msl session>)
HTTP/1.1 503 Service Unavailable
retry-after: 5
x-request-id: 9576596dc1a2
{"detail":"We can't load your knowledge sources right now. Please try again.","code":"KNOWLEDGE_SOURCES_UNAVAILABLE","request_id":"9576596dc1a2"}
```

Backend log for the same call (request id `f4a54f0bc6da` in the run that produced the log line):

```
[WARNING] digital_msl.upstream: Upstream unavailable [KNOWLEDGE_SOURCES_UNAVAILABLE] request_id=f4a54f0bc6da
cause=DmsError: DMS auth token request failed: HTTPSConnectionPool(host='16.113.145.53', port=443):
Max retries exceeded with url: /api/v1/auth/token (Caused by SSLError(... '[SSL: TLSV1_ALERT_INTERNAL_ERROR]
tlsv1 alert internal error'))
```

`GET /health` on the same run: `"ikm":{"reachable":false,"code":"TLS_ERROR",...}`.

The reference id in the user's screenshot (`19b95da5a2c5`) belongs to the user's own server session; its log line is in their backend window. The failure pattern is identical.

**Is the 503 ours or IKM's?** Ours. Our API never reaches IKM: the token request to IKM fails during the TLS handshake, and the route maps that to a clean 503. (The older "Bad Gateway" text was the same failure, wrapped as 502 before the clean-error change.)

## 3. Configuration and reachability

- `RAG_BUILDER_BASE_URL=http://16.113.145.53` (plain HTTP, port 80), prefix `/api/v1`, timeout 600 s. Username and password are set; API key is empty. (Values for secrets deliberately not printed.)
- From this machine: `http://16.113.145.53/` -> **308 redirect to `https://16.113.145.53/`**; the HTTPS handshake then fails (`curl` exit 35, `tlsv1 alert internal error`: the server has no usable TLS configuration for that IP). `http://16.113.145.53:8088/` -> **timeout** (`curl` exit 28), so the older direct port is no longer open to this machine.
- `DIGITAL_MSL_APPROVED_REPOSITORY_IDS` is **empty** (fail closed by design). Independently of IKM this means no repository is approved for answers, and the page should say so.
- Second, independent finding (data): the demo Physical MSL's therapeutic areas are local placeholders (`pending:retina-and-vitreous`, ...) with **no IKM repository linked**. Even with IKM reachable, "no repository is linked to your therapeutic areas yet" would be the honest result until an administrator enables an area with a real repository id.

## 4. Verdict

**Configuration / environment, not a code bug.** IKM is unreachable from this machine (wrong base URL scheme or host for its TLS setup, `:8088` closed). Contributing: empty allow-list (by design) and no repository linked to the demo areas (missing data).

Code defects found while reading the page's endpoints (fixed in step 2, listed under Backend changes):
1. `POST /therapeutic-areas/{id}/documents/upload` returns `502 "Upload failed: <raw exception text>"` (`digital_msl/router.py:1615`): leaks raw upstream text and bypasses the clean 503 mapping.
2. The page cannot tell "IKM unreachable" from "unauthorised" from "empty allow-list" from "no repositories": every failure is one generic card.
3. The upload sends no document metadata (title, version, effective/expiry date, approval status) to IKM: **NOT PRESENT** in the backend; the page shows the form disabled with an explanation. (Setting `approval_status` at upload is a QA policy decision per `docs/IKM_METADATA_REQUEST.md`, so it is not wired here.)

## 5. What is needed from the IKM owner / user

1. The correct IKM base URL for this environment: either an HTTPS **hostname** with a valid certificate (the IP over HTTPS fails the handshake), or the HTTP port that is open to this machine (previously `:8088`, now timing out). Set `RAG_BUILDER_BASE_URL` in `backend/.env`.
2. Confirmation that the service account (`RAG_BUILDER_USERNAME`) is valid on that instance (cannot be tested until the host is reachable).
3. QA's list of approved repository ids for `DIGITAL_MSL_APPROVED_REPOSITORY_IDS`.
4. An administrator to enable the therapeutic areas with a real repository (Settings, Therapeutic areas).
