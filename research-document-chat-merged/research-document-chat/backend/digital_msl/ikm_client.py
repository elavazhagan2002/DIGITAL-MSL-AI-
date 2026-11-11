"""Thin AWS-hosted IKM / DMS retrieve adapter for standalone Digital MSL.

Reads ``RAG_BUILDER_*`` from the environment (and ``backend/.env``) without importing
the host application's ``services.dms_client`` or ``core.config``.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

import requests

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_token_lock = threading.Lock()
_cached_access_token: Optional[str] = None
_token_expires_at: float = 0.0
_TOKEN_REFRESH_BUFFER_SECONDS = 60
_ENV_LOADED = False


def _load_env_file() -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    path = _BACKEND_DIR / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env(*names: str, default: str = "") -> str:
    _load_env_file()
    for name in names:
        value = os.environ.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


class IkmClient:
    """Retrieve + list-documents client used when Digital MSL runs on its own."""

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        api_prefix: Optional[str] = None,
        timeout: Optional[int] = None,
    ):
        self.base_url = (base_url or _env("RAG_BUILDER_BASE_URL", default="http://localhost:8088")).rstrip("/")
        self.api_prefix = (api_prefix or _env("RAG_BUILDER_API_PREFIX", default="/api/v1")).rstrip("/") or "/api/v1"
        if not self.api_prefix.startswith("/"):
            self.api_prefix = f"/{self.api_prefix}"
        self.username = username or _env("RAG_BUILDER_USERNAME", "RAG_BUILDER_USER")
        self.password = password if password is not None else _env("RAG_BUILDER_PASSWORD")
        self.api_key = _env("RAG_BUILDER_API_KEY")
        try:
            self.timeout = int(timeout if timeout is not None else _env("RAG_BUILDER_TIMEOUT_SECONDS", default="60"))
        except ValueError:
            self.timeout = 60
        self.connect_timeout = min(15, self.timeout)

    def _req_timeout(self, override: Any = None):
        read = int(override) if override is not None else self.timeout
        return (self.connect_timeout, read)

    def _path(self, suffix: str) -> str:
        if not suffix.startswith("/"):
            suffix = f"/{suffix}"
        return f"{self.api_prefix}{suffix}"

    def _fetch_access_token(self) -> tuple[str, int]:
        if not self.username or not self.password:
            raise RuntimeError("Set RAG_BUILDER_USERNAME and RAG_BUILDER_PASSWORD (or RAG_BUILDER_API_KEY).")
        url = f"{self.base_url}{self._path('/auth/token')}"
        response = requests.post(
            url,
            json={"user_id": self.username, "password": self.password},
            timeout=self._req_timeout(),
        )
        if response.status_code >= 400:
            raise RuntimeError(f"IKM auth failed ({response.status_code}): {response.text[:500]}")
        payload = response.json()
        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise RuntimeError("IKM auth token response did not include access_token.")
        return token, max(int(payload.get("expires_in") or 3600), 60)

    def _headers(self, *, force_refresh: bool = False) -> dict[str, str]:
        global _cached_access_token, _token_expires_at
        if self.api_key:
            return {"Authorization": f"Bearer {self.api_key}", "X-API-Key": self.api_key}
        now = time.time()
        with _token_lock:
            if (
                not force_refresh
                and _cached_access_token
                and now < (_token_expires_at - _TOKEN_REFRESH_BUFFER_SECONDS)
            ):
                token = _cached_access_token
            else:
                token = None
        if token is None:
            token, expires_in = self._fetch_access_token()
            with _token_lock:
                _cached_access_token = token
                _token_expires_at = time.time() + expires_in
        return {"Authorization": f"Bearer {token}"}

    def _request(self, method: str, suffix: str, **kwargs) -> Any:
        url = f"{self.base_url}{self._path(suffix)}"
        headers = {**self._headers(), **(kwargs.pop("headers", {}) or {})}
        timeout = self._req_timeout(kwargs.pop("timeout", None))
        response = requests.request(method, url, timeout=timeout, headers=headers, **kwargs)
        if response.status_code == 401 and not self.api_key:
            headers = {**self._headers(force_refresh=True), **(kwargs.get("headers") or {})}
            response = requests.request(method, url, timeout=timeout, headers=headers, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f"IKM {method} {url} failed ({response.status_code}): {response.text[:500]}")
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError(f"IKM returned invalid JSON for {url}") from exc

    def retrieve(self, *, query: str, top_k: int = 10, repository_id: Optional[str] = None, **kwargs) -> dict:
        payload: dict[str, Any] = {
            "query": query,
            "top_k": max(1, min(int(top_k or 10), 50)),
            "search_mode": kwargs.get("search_mode") or "hybrid",
            "filters": {},
            "explain": False,
        }
        if repository_id:
            payload["repository_id"] = repository_id
            payload["filters"]["repository_id"] = repository_id
        return self._request("POST", "/retrieve", json=payload, timeout=int(self.timeout or 120))

    def list_repository_documents(self, repository_id: str, *, limit: int = 500) -> dict:
        payload = self._request(
            "GET",
            f"/repositories/{repository_id}/documents",
            params={"limit": limit},
        )
        if isinstance(payload, dict) and isinstance(payload.get("documents"), list):
            return payload
        if isinstance(payload, list):
            return {"documents": payload, "count": len(payload)}
        return {"documents": [], "count": 0}
