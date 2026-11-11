"""Clean handling of upstream (IKM / DMS) failures.

Users never see raw upstream errors. The API answers with HTTP 503, a stable error code and a request
id; the technical detail goes to the log under the same request id.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from contextvars import ContextVar
from typing import Any, Optional

import requests
from fastapi import HTTPException

logger = logging.getLogger(__name__)

request_id_var: ContextVar[str] = ContextVar("dmsl_request_id", default="")

KNOWLEDGE_SOURCES_UNAVAILABLE = "KNOWLEDGE_SOURCES_UNAVAILABLE"
FRIENDLY_MESSAGE = "We can't load your knowledge sources right now. Please try again."


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


class UpstreamUnavailable(HTTPException):
    """503 with a stable code. ``detail`` is the user-safe message, never the upstream text."""

    def __init__(self, cause: Optional[BaseException] = None, *, code: str = KNOWLEDGE_SOURCES_UNAVAILABLE, message: str = FRIENDLY_MESSAGE):
        super().__init__(status_code=503, detail=message, headers={"Retry-After": "5"})
        self.code = code
        self.request_id = request_id_var.get() or new_request_id()
        logger.warning("Upstream unavailable [%s] request_id=%s cause=%s", code, self.request_id, _scrub(cause))


def _scrub(cause: Optional[BaseException]) -> str:
    text = f"{type(cause).__name__}: {cause}" if cause else "unknown"
    return re.sub(r"(?i)(password|token|secret|api[_-]?key)=[^&\s]+", r"\1=***", text)[:500]


def log_cause(cause: Optional[BaseException]) -> None:
    """Log an upstream failure's details (scrubbed) under the current request id."""
    logger.warning("Upstream detail request_id=%s cause=%s", request_id_var.get() or "-", _scrub(cause))


def error_body(exc: UpstreamUnavailable) -> dict[str, Any]:
    return {"detail": exc.detail, "code": exc.code, "request_id": exc.request_id}


# --------------------------------------------------------------------- IKM reachability
_cache: dict[str, Any] = {"at": 0.0, "value": None}
_lock = threading.Lock()
_TTL_SECONDS = 30.0


def _classify(exc: BaseException) -> str:
    text = str(exc).lower()
    if "ssl" in text or "tls" in text or "certificate" in text:
        return "TLS_ERROR"
    if isinstance(exc, requests.Timeout) or "timed out" in text or "timeout" in text:
        return "TIMEOUT"
    if isinstance(exc, requests.ConnectionError):
        return "CONNECTION_ERROR"
    return "ERROR"


def ikm_status(*, force: bool = False) -> dict[str, Any]:
    """Is IKM reachable? Cached for 30 s, short timeout, never raises."""
    now = time.monotonic()
    with _lock:
        if not force and _cache["value"] is not None and now - _cache["at"] < _TTL_SECONDS:
            return _cache["value"]
    base = ""
    code, reachable = "NOT_CONFIGURED", False
    try:
        from core.config import get_settings

        cfg = get_settings()
        base = (cfg.rag_builder_base_url or "").rstrip("/")
        prefix = (getattr(cfg, "rag_builder_api_prefix", "") or "").rstrip("/")
        if base:
            response = requests.get(f"{base}{prefix}/health", timeout=4, allow_redirects=True)
            reachable = response.status_code < 500
            code = "OK" if reachable else f"HTTP_{response.status_code}"
    except Exception as exc:
        code = _classify(exc)
        logger.warning("IKM reachability check failed (%s): %s", code, _scrub(exc))
    value = {"reachable": reachable, "code": code, "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    with _lock:
        _cache.update(at=now, value=value)
    return value


def reset_ikm_status_cache() -> None:
    with _lock:
        _cache.update(at=0.0, value=None)
