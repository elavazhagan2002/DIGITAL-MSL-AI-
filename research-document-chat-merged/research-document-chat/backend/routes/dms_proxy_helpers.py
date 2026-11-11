"""
DMS Pass-Through and Repository Access Helpers for Standalone Extraction.
"""
from __future__ import annotations
import logging
from typing import Any, Optional
from fastapi import HTTPException
from models.models import User
from services.dms_client import DmsClient, DmsError

logger = logging.getLogger(__name__)


def dms() -> DmsClient:
    return DmsClient()


def dms_http_error(exc: DmsError, default_status: int = 502) -> HTTPException:
    from digital_msl.upstream import UpstreamUnavailable

    if not exc.status_code or exc.status_code >= 500:
        return UpstreamUnavailable(exc)  # clean 503 + request id; details go to the log
    status = exc.status_code or default_status
    detail = exc.remote_detail or str(exc)
    return HTTPException(status_code=status, detail=f"DMS Error: {detail}")


def require_repository_access(repository_id: str, current_user: User) -> dict[str, Any]:
    """Validate repository existence and accessibility."""
    try:
        repo = dms().get_repository(repository_id)
        if repo and isinstance(repo, dict):
            return repo
    except DmsError as exc:
        logger.warning(f"Direct repository get failed for {repository_id}: {exc}, falling back to list")
    
    try:
        payload = dms().list_repositories()
        repos = payload.get("repositories") if isinstance(payload, dict) else payload
        if isinstance(repos, list):
            for r in repos:
                if str(r.get("repository_id") or r.get("id") or "").strip() == str(repository_id).strip():
                    return r
    except Exception as exc:
        from digital_msl.upstream import UpstreamUnavailable

        raise UpstreamUnavailable(exc)

    raise HTTPException(status_code=404, detail=f"Repository '{repository_id}' not found.")
