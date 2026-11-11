"""Resolve repository scope for agent retrieval (single repo or all accessible repos)."""
from __future__ import annotations

import contextvars
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_active_retrieval_session: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "active_retrieval_session",
    default=None,
)


def set_active_retrieval_session(session_context: Optional[Dict[str, Any]]):
    """Bind session context for tool calls during a single chat request."""
    return _active_retrieval_session.set(dict(session_context or {}))


def reset_active_retrieval_session(token) -> None:
    _active_retrieval_session.reset(token)


def get_active_retrieval_session() -> Dict[str, Any]:
    return dict(_active_retrieval_session.get() or {})


def session_repository_id(session_context: Optional[Dict[str, Any]]) -> str:
    return str((session_context or {}).get("repository_id") or "").strip()


def _user_can_access_repository(repo: dict[str, Any], ctx: dict[str, Any]) -> bool:
    role = str(ctx.get("user_role") or "").strip().lower()
    user_id = str(ctx.get("user_id") or "").strip()
    manager_roles = {
        "super_admin",
        "superadmin",
        "workspace_admin",
        "contributor",
        "consumer",
        "administrator",
        "admin",
    }
    if role in manager_roles:
        return True
    return str(repo.get("owner_user_id") or "") == user_id


def _repository_target(repo: dict[str, Any], *, workspace_id: str = "") -> dict[str, Any]:
    from services.dms_client import (
        default_tenant_id,
        embedding_model_dir,
        embedding_model_name,
        weaviate_collection,
    )

    _ = workspace_id  # retained for call-site compatibility; not used as DMS tenant
    # Use only the repository's DMS tenant. Do not fall back to the GxP
    # workspace id — that is a different ID space and can filter retrieve wrong.
    repo_tenant = default_tenant_id(repo)
    return {
        "collection_name": weaviate_collection(repo),
        "embedding_model": embedding_model_name(repo),
        "embedding_model_dir": embedding_model_dir(repo),
        "workspace_id": repo_tenant or "",
        "repository_id": str(repo.get("repository_id") or ""),
        "repository_name": repo.get("name"),
    }


def get_repository_search_targets(session_context: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Return DMS repositories to search for the current chat session.

    When ``repository_id`` is set in session context, only that repository is returned
    (after access and active-status checks). When unset, all accessible active
    repositories are returned.
    """
    ctx = session_context or {}
    selected_id = session_repository_id(ctx)
    workspace_id = str(ctx.get("workspace_id") or "").strip()

    try:
        from services.dms_client import DmsClient, DmsError, is_active_repository

        payload = DmsClient().list_repositories()
        repositories = payload.get("repositories") if isinstance(payload, dict) else []
        if not isinstance(repositories, list):
            return []

        if selected_id:
            for repo in repositories:
                if str(repo.get("repository_id") or "") != selected_id:
                    continue
                if not is_active_repository(repo):
                    return []
                if _user_can_access_repository(repo, ctx):
                    return [_repository_target(repo, workspace_id=workspace_id)]
                # Explicit repository selection — defer ACL to DMS; still attempt retrieval.
                logger.info(
                    "Local repository access check failed for %s (user=%s role=%s); "
                    "deferring to DMS retrieval ACL.",
                    selected_id,
                    ctx.get("user_id"),
                    ctx.get("user_role"),
                )
                return [_repository_target(repo, workspace_id=workspace_id)]
            return []

        targets: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for repo in repositories:
            if not _user_can_access_repository(repo, ctx):
                continue
            if not is_active_repository(repo):
                continue
            target = _repository_target(repo, workspace_id=workspace_id)
            key = (target["collection_name"], target.get("workspace_id") or "")
            if key in seen:
                continue
            seen.add(key)
            targets.append(target)
        return targets
    except DmsError:
        return []
    except Exception:
        return []
