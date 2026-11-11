"""FastAPI dependencies for Digital MSL portal authentication."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Optional

from fastapi import Depends, Header, Request

from digital_msl import auth


@dataclass(frozen=True)
class PortalContext:
    session_id: str
    user_id: str
    role: str


def _ctx_from_row(row: dict) -> PortalContext:
    return PortalContext(
        session_id=row.get("session_id") or row.get("session_id", ""),
        user_id=row["user_id"],
        role=row.get("role") or "",
    )


def get_portal_context(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_dmsl_session_id: Optional[str] = Header(None, alias="X-DMSL-Session-Id"),
    x_dmsl_user_id: Optional[str] = Header(None, alias="X-DMSL-User-Id"),
) -> PortalContext:
    cached = getattr(request.state, "dmsl_portal", None)
    if cached:
        return cached
    row = auth.portal_context_from_headers(authorization, x_dmsl_session_id, x_dmsl_user_id)
    return PortalContext(session_id=row["session_id"], user_id=row["user_id"], role=row.get("role") or "")


def get_admin_context(ctx: Annotated[PortalContext, Depends(get_portal_context)]) -> PortalContext:
    auth.require_role({"role": ctx.role}, "admin")
    return ctx


def get_msl_context(ctx: Annotated[PortalContext, Depends(get_portal_context)]) -> PortalContext:
    auth.require_role({"role": ctx.role}, "msl", "admin")
    return ctx


PortalDep = Annotated[PortalContext, Depends(get_portal_context)]
AdminDep = Annotated[PortalContext, Depends(get_admin_context)]
MslDep = Annotated[PortalContext, Depends(get_msl_context)]
