# IKM change request: document approval metadata for Digital MSL

**To:** IKM owner / QA  **From:** Digital MSL team  **Status:** draft for QA review

## Why

Digital MSL must show doctors only content that is approved and in force. Today IKM returns a processing status (queued, processing, completed), a title, and optionally a category and version. It does not return an approval status, effective or expiry dates, or supersession. Digital MSL therefore cannot tell an approved current document from a draft or a withdrawn one, and has to treat every retrieved passage as unverified. Until this request is delivered, Digital MSL answers only from an allow-list of repositories that QA has declared approved-only.

## 1. Metadata fields

Written through the existing upload `metadata` object, stored with the document, and returned on every retrieve hit and every document-list item.

| Field | Type | Rules |
|---|---|---|
| `document_id` | string | Stable across versions of the same document (a version series id). Distinct from the file or chunk id. |
| `version` | string | Required, for example `3.0`. |
| `approval_status` | enum | `DRAFT`, `IN_REVIEW`, `APPROVED`, `SUPERSEDED`, `WITHDRAWN`, `EXPIRED`. Only `APPROVED` is ever used for answers. |
| `effective_date` | date (ISO 8601) | Required when `APPROVED`. Not used before this date. |
| `expiry_date` | date or null | Not used after this date. |
| `superseded_by` | string or null | `document_id` plus `version` of the replacing document. Setting it must also set `approval_status` to `SUPERSEDED`. |
| `approved_by` | string | User id or name of the approver. Required when `APPROVED`. |
| `approved_at` | datetime (UTC) | Required when `APPROVED`. |
| `document_type` | string | From the existing document-type list. |

## 2. API behaviour needed

1. **Returned on retrieve:** each hit carries the fields above (nested under `document_metadata` or flattened; tell us which).
2. **Filterable on retrieve:** `filters: {approval_status: "APPROVED", not_expired: true, superseded: false}` applied server-side before ranking, so excluded passages never take a top-k slot.
3. **Metadata update without re-upload:** `PATCH /documents/{id}/metadata`, audited, so a status change (for example to `SUPERSEDED`) takes effect immediately in retrieval.
4. **Atomic supersession:** approving a new version of a `document_id` marks the previous approved version `SUPERSEDED` in the same transaction.
5. **Change notification (optional but preferred):** a webhook or a poll endpoint listing documents whose `approval_status` or `expiry_date` changed since a timestamp, so Digital MSL can flag Q&A entries that cite them.

## 3. Rule for approved repositories

- A document with `approval_status` other than `APPROVED` must never be uploaded into, or left in, a repository that Digital MSL lists as approved.
- Drafts and in-review versions live in a separate working repository that Digital MSL is not configured to read.
- When a version is superseded, withdrawn or expired, either mark it as such (preferred, keeps the audit history) or remove it from the approved repository. Never leave it marked `APPROVED`.
- Uploads into an approved repository should be rejected by IKM if `approval_status` is not `APPROVED` or the required fields in section 1 are missing.
- Repository approval is a QA decision recorded by QA, not by Digital MSL. Digital MSL reads the list from `DIGITAL_MSL_APPROVED_REPOSITORY_IDS` and answers nothing when it is empty.

## 4. How Digital MSL will use it

- Server-side filter on retrieve where supported, and a second check in Digital MSL: a hit is dropped unless `approval_status == APPROVED`, `effective_date` is today or earlier, `expiry_date` is empty or in the future, and `superseded_by` is empty. A hit missing these fields is dropped once metadata is enforced.
- Every quotation shows title, version, section or page, and effective date.
- Q&A entries store the source `document_id` and `version`; when the source is superseded, withdrawn or expired the entry is flagged to its author and not reused.

## 5. Acceptance checks (QA)

1. Upload one document in each state (`DRAFT`, `APPROVED`, `SUPERSEDED`, `EXPIRED`, `WITHDRAWN`) to a test repository and confirm that a retrieve with the filter returns only the `APPROVED` one.
2. Change an approved document to `SUPERSEDED` with `PATCH`; the next retrieve must not return it.
3. Retrieve hits include every field in section 1.
