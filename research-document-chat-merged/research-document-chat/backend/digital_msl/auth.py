"""Portal session validation and therapeutic-area / repo access checks."""
from __future__ import annotations

from starlette.exceptions import HTTPException

from typing import Optional

from digital_msl import store


def require_login_session(session_id: str, user_id: str = "") -> dict[str, str]:
    if not (session_id or "").strip():
        raise HTTPException(status_code=401, detail="session_id is required.")
    row = store.get_login_session(session_id.strip())
    if not row:
        raise HTTPException(status_code=403, detail="Invalid or expired session.")
    if user_id and user_id.strip() and row.get("user_id") != user_id.strip():
        raise HTTPException(status_code=403, detail="Invalid or expired session.")
    if row.get("ended_at"):
        raise HTTPException(status_code=403, detail="Session has ended.")
    if row.get("expired"):
        raise HTTPException(status_code=401, detail="Session expired. Please sign in again.")
    user = store.get_portal_user(row.get("user_id") or "")
    if not user or (user.get("status") or "active") != "active":
        raise HTTPException(status_code=403, detail="Invalid or expired session.")
    return row


def portal_context_from_headers(
    authorization: Optional[str] = None,
    x_dmsl_session_id: Optional[str] = None,
    x_dmsl_user_id: Optional[str] = None,
) -> dict[str, str]:
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token:
        token = (x_dmsl_session_id or "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required (Authorization: Bearer <session_id>).")
    row = require_login_session(token, "")
    if (x_dmsl_user_id or "").strip() and x_dmsl_user_id.strip() != row.get("user_id"):
        raise HTTPException(status_code=403, detail="user_id does not match authenticated session.")
    return {**row, "session_id": token}


def require_role(session_row: dict[str, str], *roles: str) -> None:
    if session_row.get("role") not in roles:
        raise HTTPException(status_code=403, detail="Insufficient role for this operation.")


def assert_repository_access(user_id: str, role: str, repository_id: str) -> None:
    if not (repository_id or "").strip():
        return
    if not store.is_therapeutic_area_active(repository_id):
        raise HTTPException(status_code=403, detail="This therapeutic area is inactive.")
    role = (role or "").strip()
    if role == "admin":
        return
    if role == "doctor":
        user = store.get_portal_user(user_id) or {}
        allowed = list(user.get("assigned_repository_ids") or [])
        if repository_id not in allowed:
            raise HTTPException(status_code=403, detail="This repo is not assigned to your account.")
        return
    if role == "msl":
        if repository_id not in store.repositories_for_msl(user_id):
            raise HTTPException(status_code=403, detail="This repo is outside your therapeutic areas.")
        return
    raise HTTPException(status_code=403, detail="Access denied.")


def optional_admin_session(admin_session_id: str, admin_user_id: str) -> dict[str, str] | None:
    if not (admin_session_id or "").strip() or not (admin_user_id or "").strip():
        return None
    row = require_login_session(admin_session_id.strip(), admin_user_id.strip())
    require_role(row, "admin")
    return row


def enforce_session_role(
    session_id: str,
    user_id: str,
    *roles: str,
    repository_id: str = "",
) -> dict[str, str]:
    row = require_login_session(session_id, user_id)
    require_role(row, *roles)
    if repository_id:
        assert_repository_access(user_id, row.get("role") or "", repository_id)
    return row


# ------------------------------------------------------------------ cookie / bearer request auth
SESSION_COOKIE = "dmsl_session"
CSRF_COOKIE = "dmsl_csrf"
_UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def credentials_from_request(request) -> tuple[str, str]:
    """(session_id, via) where via is 'bearer' or 'cookie'; empty when unauthenticated."""
    authz = request.headers.get("authorization") or ""
    if authz.lower().startswith("bearer ") and authz[7:].strip():
        return authz[7:].strip(), "bearer"
    header_session = (request.headers.get("x-dmsl-session-id") or "").strip()
    if header_session:
        return header_session, "bearer"
    cookie = (request.cookies.get(SESSION_COOKIE) or "").strip()
    if cookie:
        return cookie, "cookie"
    return "", ""


def session_id_from_request(request) -> str:
    return credentials_from_request(request)[0]


def check_csrf(request, session_id: str) -> None:
    """Cookie-authenticated unsafe requests must echo the CSRF token in X-CSRF-Token."""
    import secrets as _secrets

    from digital_msl import security

    header = request.headers.get("x-csrf-token") or ""
    cookie = request.cookies.get(CSRF_COOKIE) or ""
    expected = security.csrf_token(session_id)
    if not header or not _secrets.compare_digest(header, cookie) or not _secrets.compare_digest(header, expected):
        raise HTTPException(status_code=403, detail="CSRF validation failed.")


def context_from_request(request) -> dict[str, str]:
    token, via = credentials_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required (cookie session or Authorization: Bearer <session_id>).")
    row = require_login_session(token, "")
    declared = (request.headers.get("x-dmsl-user-id") or "").strip()
    if declared and declared != row.get("user_id"):
        raise HTTPException(status_code=403, detail="user_id does not match authenticated session.")
    if via == "cookie" and request.method.upper() in _UNSAFE:
        check_csrf(request, token)
    return {**row, "session_id": token, "via": via}
