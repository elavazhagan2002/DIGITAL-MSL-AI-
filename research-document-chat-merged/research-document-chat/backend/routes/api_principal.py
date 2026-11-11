"""Unified API authentication: Digital MSL portal Bearer session or explicit dev fallback."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from fastapi import Header, HTTPException

from digital_msl import auth as portal_auth


@dataclass(frozen=True)
class ApiPrincipal:
    user_id: str
    role: str
    session_id: str = ""
    source: str = "portal"  # portal | dev


def _dev_auth_allowed() -> bool:
    return os.getenv("RESEARCH_CHAT_ALLOW_DEV_AUTH", "false").strip().lower() in {"1", "true", "yes", "on"}


def resolve_api_principal(
    authorization: Optional[str] = Header(None),
    x_dmsl_session_id: Optional[str] = Header(None, alias="X-DMSL-Session-Id"),
) -> ApiPrincipal:
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token:
        token = (x_dmsl_session_id or "").strip()
    if token:
        row = portal_auth.portal_context_from_headers(authorization, x_dmsl_session_id, None)
        return ApiPrincipal(
            user_id=row["user_id"],
            role=row.get("role") or "",
            session_id=row.get("session_id") or token,
            source="portal",
        )
    if not _dev_auth_allowed():
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Send Authorization: Bearer <portal_session_id>.",
        )
    return ApiPrincipal(user_id="usr_default_researcher", role="super_admin", source="dev")


def assert_repository_access_for_principal(principal: ApiPrincipal, repository_id: str) -> None:
    if principal.source == "portal":
        portal_auth.assert_repository_access(principal.user_id, principal.role, repository_id)
        return
    # Dev fallback: existence checked separately via DMS; no cross-user portal rules.
