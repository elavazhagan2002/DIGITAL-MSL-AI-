"""Load Documents: which state the page is in, and a clearly labelled demo fixture.

States (each has its own message in the UI, none shows raw server text):
  ok               repositories available (real IKM, or the demo fixture when demo mode is on)
  not_configured   no IKM address or credentials configured
  ikm_unreachable  IKM did not answer (TLS error, timeout, connection refused, 5xx)
  unauthorised     IKM answered 401 / 403 to the service credentials
  no_repositories  IKM works but no repository is linked to the user's therapeutic areas

``allow_list`` is reported separately: an empty DIGITAL_MSL_APPROVED_REPOSITORY_IDS is by design (fail closed),
so nothing is answered from any repository, but loading documents can still work.

The fixture exists only while DIGITAL_MSL_DEMO_MODE is on and only when real IKM cannot be used. It is labelled
"Demo data" everywhere and never appears with demo mode off.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Optional

from digital_msl import store, upstream
from digital_msl.config import get_dmsl_settings

DEMO_REPOSITORY_ID = "demo-test-repository"
DEMO_REPOSITORY_NAME = "Demo data: test repository (Retina and Vitreous)"

ALLOWED_EXTENSIONS = (".pdf", ".docx", ".doc", ".txt")
MAX_UPLOAD_BYTES = 200 * 1024 * 1024

_lock = threading.Lock()
_demo_uploads: list[dict[str, Any]] = []


def demo_active() -> bool:
    return bool(get_dmsl_settings().demo_enabled)


def is_demo_repository(repository_id: str) -> bool:
    return demo_active() and repository_id == DEMO_REPOSITORY_ID


# ------------------------------------------------------------------ demo fixture
_BASE_DOCS = (
    ("demo-doc-1", "Aflibercept prescribing information (sample).pdf", "completed", "Prescribing information", None),
    ("demo-doc-2", "Faricimab prescribing information (sample).pdf", "completed", "Prescribing information", None),
    ("demo-doc-3", "Diabetic macular edema guideline (sample).pdf", "processing", "Guideline", None),
    ("demo-doc-4", "Scanned protocol appendix (sample).pdf", "failed", "Protocol", "Text could not be extracted from this file."),
)


def reset_demo_uploads() -> None:
    with _lock:
        _demo_uploads.clear()


def _upload_status(age_seconds: float) -> str:
    if age_seconds < 3:
        return "queued"
    if age_seconds < 8:
        return "processing"
    return "completed"


def demo_documents() -> list[dict[str, Any]]:
    now = time.time()
    docs = [
        {
            "document_id": doc_id,
            "original_file_name": name,
            "status": status,
            "document_type": dtype,
            "upload_timestamp": now - 86400 * (index + 2),
            "error_details": error,
            "demo": True,
        }
        for index, (doc_id, name, status, dtype, error) in enumerate(_BASE_DOCS)
    ]
    with _lock:
        for item in _demo_uploads:
            docs.append({**item, "status": _upload_status(now - item["upload_timestamp"])})
    return docs


def demo_repository() -> dict[str, Any]:
    return {
        "repository_id": DEMO_REPOSITORY_ID,
        "name": DEMO_REPOSITORY_NAME,
        "therapeutic_area": "Retina and Vitreous",
        "approved": False,
        "document_count": len(demo_documents()),
        "demo": True,
    }


def record_demo_upload(filename: str, size: int, user_id: str) -> dict[str, Any]:
    item = {
        "document_id": f"demo-{uuid.uuid4().hex[:10]}",
        "original_file_name": filename,
        "document_type": "Uploaded in demo",
        "upload_timestamp": time.time(),
        "error_details": None,
        "demo": True,
        "size": size,
        "uploaded_by": user_id,
    }
    with _lock:
        _demo_uploads.append(item)
    return {**item, "status": "queued"}


# ------------------------------------------------------------------ diagnosis
def configured() -> tuple[bool, list[str]]:
    """Is there an IKM address and some credential? Returns the setting names that are missing."""
    from core.config import get_settings

    cfg = get_settings()
    missing: list[str] = []
    if not (cfg.rag_builder_base_url or "").strip():
        missing.append("RAG_BUILDER_BASE_URL")
    has_key = bool((cfg.rag_builder_api_key or "").strip())
    has_login = bool((cfg.rag_builder_user or "").strip()) and bool((cfg.rag_builder_password or "").strip())
    if not (has_key or has_login):
        missing.append("RAG_BUILDER_USERNAME and RAG_BUILDER_PASSWORD (or RAG_BUILDER_API_KEY)")
    return not missing, missing


def allow_list_info() -> dict[str, Any]:
    approved = get_dmsl_settings().approved_repositories()
    return {"empty": not approved, "all": "*" in approved, "approved_count": len([a for a in approved if a != "*"])}


def _list_repositories() -> list[dict[str, Any]]:
    from routes import dms_proxy_helpers

    data = dms_proxy_helpers.dms().list_repositories()
    repos = data.get("repositories") if isinstance(data, dict) else data
    return [r for r in (repos or []) if isinstance(r, dict)]


def ikm_diagnosis(*, deep: bool = False) -> dict[str, Any]:
    """Configured / reachable / unauthorised, without raising and without secrets. ``deep`` also tries to authenticate."""
    from services.dms_client import DmsError

    ok, missing = configured()
    result: dict[str, Any] = {
        "configured": ok,
        "missing": missing,
        "reachable": False,
        "unauthorised": False,
        "code": "NOT_CONFIGURED" if not ok else "",
        "checked_at": "",
        "repositories": [],
    }
    if not ok:
        return result
    status = upstream.ikm_status(force=deep)
    result.update(reachable=bool(status["reachable"]), code=status["code"], checked_at=status["checked_at"])
    if not status["reachable"]:
        return result
    try:
        result["repositories"] = _list_repositories()
    except DmsError as exc:
        if exc.status_code in (401, 403):
            result.update(unauthorised=True, code="UNAUTHORISED")
        else:
            result.update(reachable=False, code="IKM_ERROR")
        upstream.log_cause(exc)
    except Exception as exc:  # network failure after the reachability probe
        result.update(reachable=False, code="IKM_ERROR")
        upstream.log_cause(exc)
    return result


def _area_name(repository_id: str, areas: list[dict[str, Any]]) -> str:
    for area in areas:
        if area.get("repository_id") == repository_id:
            return area.get("name") or ""
    return ""


def build_context(user_id: str, role: str) -> dict[str, Any]:
    """The single answer the Load Documents page needs. Always returns a state; never raw server text."""
    request_id = upstream.request_id_var.get() or upstream.new_request_id()
    settings = get_dmsl_settings()
    approved = settings.approved_repositories()
    diag = ikm_diagnosis()
    areas = store.list_therapeutic_areas()
    out: dict[str, Any] = {
        "state": "ok",
        "code": "",
        "demo": False,
        "request_id": request_id,
        "allow_list": allow_list_info(),
        "ikm": {k: diag[k] for k in ("configured", "reachable", "unauthorised", "code", "checked_at")},
        "missing": diag["missing"] if role == "admin" else [],
        "repositories": [],
    }
    if diag["configured"] and diag["reachable"] and not diag["unauthorised"]:
        mine = set(store.repositories_for_msl(user_id)) if role == "msl" else None
        repos = []
        for repo in diag["repositories"]:
            rid = str(repo.get("repository_id") or repo.get("id") or "")
            name = str(repo.get("name") or rid)
            if not rid or name.startswith("_temp_"):
                continue
            if mine is not None and rid not in mine:
                continue
            repos.append(
                {
                    "repository_id": rid,
                    "name": name,
                    "therapeutic_area": _area_name(rid, areas),
                    "approved": "*" in approved or rid in approved,
                    "document_count": repo.get("document_count"),
                    "demo": False,
                }
            )
        out["repositories"] = repos
        if not repos:
            out.update(state="no_repositories", code="NO_REPOSITORIES")
        return out
    # IKM cannot be used
    if not diag["configured"]:
        out.update(state="not_configured", code="NOT_CONFIGURED")
    elif diag["unauthorised"]:
        out.update(state="unauthorised", code="UNAUTHORISED")
    else:
        out.update(state="ikm_unreachable", code=diag["code"] or "ERROR")
    if demo_active():  # labelled demo data so the page can be shown without IKM
        out.update(state="ok", demo=True, repositories=[demo_repository()])
    return out
