"""Live IKM check with planted documents (authorised by the product owner, TEST repository only).

Uploads four small SYNTHETIC documents (Approved, Draft, Expired, Superseded), records their approval in Digital MSL's
approval registry, then checks (a) what metadata IKM returns on the document list and on
retrieve hits, (b) which documents Digital MSL's retrieval filter lets through, and always deletes the
planted documents again. Every call is appended to docs/live_ikm/planted_calls.log.

    python scripts/live_ikm_planted.py <repository_id>
"""
from __future__ import annotations

import io
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from docx import Document  # noqa: E402

from digital_msl import evidence  # noqa: E402
from digital_msl.config import get_dmsl_settings  # noqa: E402
from services.dms_client import DmsClient  # noqa: E402

OUT = ROOT / "docs" / "live_ikm"
OUT.mkdir(parents=True, exist_ok=True)
LOG = OUT / "planted_calls.log"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

PLANTED = {
    "APPROVED": {"weeks": 97, "meta": {"approval_status": "APPROVED", "version": "2.0", "effective_date": "2025-01-01", "expiry_date": "2099-12-31"}},
    "DRAFT": {"weeks": 91, "meta": {"approval_status": "DRAFT", "version": "0.3"}},
    "EXPIRED": {"weeks": 83, "meta": {"approval_status": "APPROVED", "version": "1.0", "effective_date": "2019-01-01", "expiry_date": "2020-01-01"}},
    "SUPERSEDED": {"weeks": 71, "meta": {"approval_status": "SUPERSEDED", "version": "1.0", "superseded_by": "ZZPLANTED_APPROVED@2.0"}},
}


def log(line: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"{stamp} {line}\n")
    print(line)


def make_docx(label: str, weeks: int) -> bytes:
    doc = Document()
    doc.add_heading(f"ZZPLANTED {label} test sheet", level=1)
    doc.add_paragraph(
        "SYNTHETIC TEST DOCUMENT. It contains no real clinical content. "
        f"Zorvantel is a fictional product. The planted Zorvantel dosing interval is {weeks} weeks "
        f"according to the {label} planted test sheet."
    )
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: live_ikm_planted.py <repository_id>", file=sys.stderr)
        return 2
    repo = sys.argv[1]
    approved = get_dmsl_settings().approved_repositories()
    if repo not in approved:
        print("Refusing: the repository is not on DIGITAL_MSL_APPROVED_REPOSITORY_IDS.", file=sys.stderr)
        return 2
    client = DmsClient()
    result: dict = {"repository_id": repo, "started": datetime.now(timezone.utc).isoformat(), "uploads": {}, "listing": {}, "retrieve": {}, "filter": {}, "cleanup": {}}
    ids: dict[str, str] = {}
    try:
        for label, spec in PLANTED.items():
            name = f"ZZPLANTED_{label}.docx"
            payload = {**spec["meta"], "document_title": f"ZZPLANTED {label}"}
            log(f"CALL upload_documents repo={repo} file={name} (no metadata: the IKM upload form has no metadata field)")
            resp = client.upload_documents(
                files=[(name, io.BytesIO(make_docx(label, spec["weeks"])), DOCX)],
                repository_id=repo,
            )
            docs = resp.get("documents") or resp.get("items") or resp.get("results") or []
            doc_id = ""
            if docs and isinstance(docs[0], dict):
                doc_id = str(docs[0].get("document_id") or docs[0].get("id") or "")
            doc_id = doc_id or str(resp.get("document_id") or "")
            ids[label] = doc_id
            result["uploads"][label] = {"document_id": doc_id, "response_keys": sorted(resp.keys())}
            log(f"  -> response keys={sorted(resp.keys())} document_id={doc_id}")

        # wait for processing
        deadline = time.time() + 420
        pending = set(ids)
        while pending and time.time() < deadline:
            time.sleep(10)
            listing = client.list_repository_documents(repo, limit=500).get("documents") or []
            by_id = {str(d.get("document_id")): d for d in listing}
            for label in list(pending):
                doc = by_id.get(ids[label])
                state = (doc or {}).get("status")
                log(f"POLL {label} status={state}")
                if state in {"completed", "failed", "error"}:
                    pending.discard(label)
        result["unprocessed_after_wait"] = sorted(pending)

        # IKM has no approval fields (PATCH /metadata answers 400 'Unknown metadata fields'), so approval is
        # recorded in Digital MSL's own registry, exactly as a Physical MSL does through PUT /document-approvals.
        from digital_msl import store

        store.init_store()
        for label, doc_id in ids.items():
            meta = PLANTED[label]["meta"]
            log(f"REGISTRY set_document_approval repo={repo} document_id={doc_id} ({label}) -> {json.dumps(meta)}")
            store.set_document_approval(
                repo, doc_id,
                approval_status=meta["approval_status"], version=meta.get("version", ""),
                effective_date=meta.get("effective_date", ""), expiry_date=meta.get("expiry_date", ""),
                superseded_by=meta.get("superseded_by", ""), document_title=f"ZZPLANTED {label}",
                actor_id="usr_live_check", actor_role="msl",
            )
        evidence.clear_document_caches()

        listing = client.list_repository_documents(repo, limit=500).get("documents") or []
        by_id = {str(d.get("document_id")): d for d in listing}
        for label, doc_id in ids.items():
            doc = by_id.get(doc_id) or {}
            md = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
            result["listing"][label] = {
                "status": doc.get("status"),
                "approval_fields_returned": {k: md.get(k) for k in ("approval_status", "version", "effective_date", "expiry_date", "superseded_by", "document_title") if k in md},
                "all_metadata_keys": sorted(md.keys()),
                "top_level_keys": sorted(doc.keys()),
            }
            log(f"LISTING {label}: approval fields in metadata = {result['listing'][label]['approval_fields_returned']}")

        query = "What is the planted Zorvantel dosing interval in weeks?"
        log(f"CALL retrieve repo={repo} query={query!r} search_mode=hybrid top_k=12")
        raw = client.retrieve(query=query, top_k=12, repository_id=repo, search_mode="hybrid")
        hits = [h for h in (raw.get("results") or []) if isinstance(h, dict)]
        planted_ids = {v: k for k, v in ids.items()}
        for h in hits:
            anchor = h.get("citation_anchor") if isinstance(h.get("citation_anchor"), dict) else {}
            did = str(anchor.get("document_id") or h.get("document_id") or "")
            if did in planted_ids:
                result["retrieve"].setdefault(planted_ids[did], {"score": h.get("score"), "hit_keys": sorted(h.keys()), "metadata_on_hit": h.get("metadata"), "document_metadata_on_hit": h.get("document_metadata")})
        log(f"  -> {len(hits)} hits; planted documents among them: {sorted(result['retrieve'])}")

        evidence.clear_document_caches()
        outcome = evidence.retrieve_evidence(client, query=query, repository_id=repo, top_k=12)
        survived = {planted_ids[c.document_id] for c in outcome.chunks if c.document_id in planted_ids}
        excluded = {planted_ids[e["document_id"]]: e["reason"] for e in outcome.excluded if e["document_id"] in planted_ids}
        result["filter"] = {"survived_filter": sorted(survived), "excluded_with_reason": excluded}
        log(f"FILTER survived={sorted(survived)} excluded={excluded}")
        for c in outcome.chunks:
            if c.document_id in planted_ids:
                log(f"  surviving chunk from {planted_ids[c.document_id]}: version={c.version!r} status={c.approval_status!r} text={c.document_content[:120]!r}")
    finally:
        for label, doc_id in ids.items():
            if not doc_id:
                continue
            try:
                log(f"CALL delete_repository_document repo={repo} document_id={doc_id} ({label})")
                client.delete_repository_document(repo, doc_id, delete_file=True)
                result["cleanup"][label] = "deleted"
            except Exception as exc:  # report, never hide
                result["cleanup"][label] = f"DELETE FAILED: {str(exc)[:200]}"
                log(f"  -> delete failed: {str(exc)[:200]}")
        left = [d for d in (client.list_repository_documents(repo, limit=500).get("documents") or []) if str(d.get("original_file_name") or d.get("document_name") or "").startswith("ZZPLANTED")]
        result["planted_documents_left_in_repository"] = len(left)
        log(f"CLEANUP check: ZZPLANTED documents still in repository = {len(left)}")
    (OUT / "planted_results.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print("Wrote", OUT / "planted_results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
