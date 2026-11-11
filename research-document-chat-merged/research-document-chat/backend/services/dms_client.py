"""Thin HTTP pass-through to DMS Platform Engine (dms_service).

Reference: _references/dms-service-openapi.yaml
"""
from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO, Optional

import requests

from core.config import get_settings

logger = logging.getLogger(__name__)

_TENANTLESS_CHUNK_COLLECTIONS: set[str] = set()
_token_lock = threading.Lock()
_cached_access_token: Optional[str] = None
_token_expires_at: float = 0.0
_TOKEN_REFRESH_BUFFER_SECONDS = 60


class DmsError(RuntimeError):
    """Raised when the DMS service cannot satisfy a request."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        remote_detail: str | None = None,
        error_code: str | None = None,
        error_details: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.remote_detail = remote_detail
        self.error_code = error_code
        self.error_details = error_details


_FIELD_LABELS: dict[str, str] = {
    "embedding_model": "embedding model",
    "chunk_size": "chunk size",
    "chunk_overlap": "chunk overlap",
    "weaviate_collection": "Weaviate collection",
    "document_type_id": "document type",
}


def humanize_dms_field_name(field: str) -> str:
    key = str(field or "").strip()
    if not key:
        return "setting"
    return _FIELD_LABELS.get(key, key.replace("_", " "))


def normalize_document_list_payload(payload: Any) -> dict[str, Any]:
    """Normalize DMS document list responses (dict or bare array) to {documents, count}."""
    if isinstance(payload, list):
        return {"documents": payload, "count": len(payload)}
    if isinstance(payload, dict):
        documents = payload.get("documents")
        if isinstance(documents, list):
            count = payload.get("count")
            return {"documents": documents, "count": count if count is not None else len(documents)}
    return {"documents": [], "count": 0}


def normalize_repository_document_list_payload(payload: Any) -> dict[str, Any]:
    """Normalize DMS repository document list (RepositoryDocumentListResponse)."""
    if isinstance(payload, dict):
        documents = payload.get("documents")
        if isinstance(documents, list):
            return {
                "repository_id": payload.get("repository_id"),
                "document_count": payload.get("document_count", len(documents)),
                "returned": payload.get("returned", len(documents)),
                "documents": documents,
            }
    return normalize_document_list_payload(payload)


def _format_dms_timestamp(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        from datetime import datetime, timezone

        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    if isinstance(value, str):
        return value
    return str(value)


_UUID_FILENAME_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?:\.[a-z0-9]+)?$",
    re.IGNORECASE,
)


def _is_internal_storage_filename(name: str, document_id: str = "") -> bool:
    """True when a filename is the internal stored id (uuid or uuid.ext), not the upload name."""
    normalized = str(name or "").strip()
    if not normalized:
        return True
    doc_id = str(document_id or "").strip()
    if doc_id and normalized == doc_id:
        return True
    if doc_id and Path(normalized).stem == doc_id:
        return True
    return bool(_UUID_FILENAME_RE.match(normalized))


def resolve_portal_document_filename(doc: dict[str, Any]) -> str:
    """Pick the user-facing upload filename, never the internal storage id."""
    document_id = str(
        doc.get("document_id") or doc.get("documentId") or doc.get("id") or ""
    ).strip()
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    processing = doc.get("processing") if isinstance(doc.get("processing"), dict) else {}
    processor_results = (
        processing.get("processor_results") if isinstance(processing.get("processor_results"), dict) else {}
    )
    chunk_result = (
        processor_results.get("chunking_vectorizing")
        if isinstance(processor_results.get("chunking_vectorizing"), dict)
        else {}
    )
    chunk_meta = (
        chunk_result.get("document_metadata")
        if isinstance(chunk_result.get("document_metadata"), dict)
        else {}
    )
    doc_info = metadata.get("document_info") if isinstance(metadata.get("document_info"), dict) else {}
    if not doc_info and isinstance(doc.get("document_info"), dict):
        doc_info = doc["document_info"]

    candidates = [
        doc.get("original_file_name"),
        doc.get("originalFileName"),
        doc.get("original_filename"),
        metadata.get("original_file_name"),
        metadata.get("original_filename"),
        metadata.get("filename"),
        doc_info.get("original_file_name"),
        chunk_meta.get("original_file_name"),
        chunk_meta.get("original_filename"),
        doc.get("documentTitle"),
        metadata.get("title"),
        metadata.get("document_title"),
        doc.get("document_name"),
        doc_info.get("document_name"),
    ]
    for candidate in candidates:
        name = str(candidate or "").strip()
        if name and not _is_internal_storage_filename(name, document_id):
            return name

    if str(doc.get("status") or "").strip().lower() == "missing" and not processing.get("job_status"):
        short_id = document_id[:8] if document_id else "unknown"
        return f"Stale document record ({short_id}…)"
    return "Unknown document"


def chunking_vectorizing_succeeded(chunk_result: dict[str, Any] | None) -> bool:
    """True when DMS chunking/vectorizing payload indicates a finished successful run."""
    if not isinstance(chunk_result, dict) or not chunk_result:
        return False
    status = str(
        chunk_result.get("status")
        or chunk_result.get("job_status")
        or chunk_result.get("state")
        or chunk_result.get("result_status")
        or ""
    ).strip().lower().replace("-", "_")
    if status in {"failed", "error", "timed_out", "cancelled", "canceled"}:
        return False
    if status in {"completed", "complete", "success", "succeeded", "done"}:
        return True
    if chunk_result.get("success") is True:
        return True
    if chunk_result.get("completed_at") or chunk_result.get("finished_at") or chunk_result.get("completion_timestamp"):
        return True
    meta = chunk_result.get("document_metadata") if isinstance(chunk_result.get("document_metadata"), dict) else {}
    for key in ("chunk_count", "embedded_chunk_count", "vector_count", "chunks_created"):
        try:
            if int(meta.get(key) or chunk_result.get(key) or 0) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def map_dms_document_to_portal(doc: dict[str, Any]) -> dict[str, Any]:
    """Map DMS DocumentResponse / RepositoryDocumentListItem to portal camelCase shape."""
    document_id = str(
        doc.get("document_id") or doc.get("documentId") or doc.get("id") or ""
    ).strip()
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    processing = doc.get("processing") if isinstance(doc.get("processing"), dict) else {}
    processor_results = (
        processing.get("processor_results") if isinstance(processing.get("processor_results"), dict) else {}
    )
    chunk_result = (
        processor_results.get("chunking_vectorizing")
        if isinstance(processor_results.get("chunking_vectorizing"), dict)
        else {}
    )
    raw_status = processing.get("job_status") or doc.get("status") or ""
    if str(doc.get("status") or "").strip().lower() == "missing" and processing.get("job_status"):
        raw_status = processing.get("job_status")
    status = map_dms_status(str(raw_status))
    # If chunking/vectorizing finished successfully but job_status still lags, surface completed.
    if status in {"processing", "queued"} and chunking_vectorizing_succeeded(chunk_result):
        status = "completed"
    name = resolve_portal_document_filename(doc)
    return {
        "fileId": document_id,
        "originalFilename": name,
        "fileType": doc.get("document_type"),
        "repositoryId": doc.get("repository_id"),
        "status": status,
        "documentTitle": metadata.get("title") or metadata.get("document_title") or name,
        "documentType": doc.get("document_type"),
        "documentCategory": metadata.get("category") or metadata.get("document_category"),
        "versionNumber": metadata.get("version") or metadata.get("version_number"),
        "createdAt": _format_dms_timestamp(doc.get("upload_timestamp")),
        "updatedAt": _format_dms_timestamp(
            doc.get("processing_completion_timestamp") or doc.get("metadata_updated_at")
        ),
        "processing": processing or None,
        "errorDetails": doc.get("error_details"),
    }


def extract_dms_error_message(payload: Any, *, fallback: str = "") -> str:
    """Extract a human-readable message from DMS/HTTP error payloads."""
    if isinstance(payload, str):
        text = payload.strip()
        return text or fallback

    if isinstance(payload, list):
        parts: list[str] = []
        for item in payload:
            part = extract_dms_error_message(item, fallback="")
            if part:
                parts.append(part)
        return " ".join(parts) or fallback

    if isinstance(payload, dict):
        msg = payload.get("msg")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()

        error = payload.get("error")
        if isinstance(error, dict):
            message = extract_dms_error_message(error.get("message"), fallback="")
            if message:
                return message

        for key in ("message", "detail", "description", "error"):
            message = extract_dms_error_message(payload.get(key), fallback="")
            if message and message != fallback:
                return message

    return fallback


def parse_dms_api_error(payload: Any) -> dict[str, Any]:
    """Parse DMS ApiErrorResponse into code, message, details, and formatted_message."""
    if not isinstance(payload, dict):
        message = extract_dms_error_message(payload)
        return {
            "code": None,
            "message": message,
            "details": None,
            "formatted_message": message,
        }

    error = payload.get("error")
    if isinstance(error, dict):
        code = str(error.get("code") or "").strip() or None
        message = str(error.get("message") or "").strip()
        details = error.get("details")
    else:
        code = None
        message = extract_dms_error_message(payload)
        details = payload.get("details")

    formatted = message
    if isinstance(details, dict):
        missing = details.get("missing")
        if isinstance(missing, list) and missing:
            labels = [humanize_dms_field_name(str(item)) for item in missing if str(item).strip()]
            if labels:
                prefix = message.rstrip(".") if message else "Configuration incomplete"
                formatted = f"{prefix}: {', '.join(labels)}."
        elif details.get("document_count") is not None and code == "repository_in_use":
            count = details.get("document_count")
            formatted = (
                f"{message} ({count} document(s) linked)."
                if message
                else f"Repository has {count} linked document(s)."
            )
        elif details.get("status") is not None and message:
            formatted = f"{message} (status: {details.get('status')})."
    elif details is not None and message:
        extra = extract_dms_error_message(details)
        if extra and extra != message:
            formatted = f"{message} ({extra})"

    if not formatted:
        formatted = extract_dms_error_message(payload)

    return {
        "code": code,
        "message": message,
        "details": details,
        "formatted_message": formatted,
    }


def clear_dms_token_cache() -> None:
    """Drop cached DMS JWT (e.g. after 401)."""
    global _cached_access_token, _token_expires_at
    with _token_lock:
        _cached_access_token = None
        _token_expires_at = 0.0


class DmsClient:
    """HTTP client for DMS consumer APIs (default prefix /api/v1)."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.base_url = self._resolve_base_url(self.settings.rag_builder_base_url)
        self.api_prefix = (self.settings.rag_builder_api_prefix or "/api/v1").rstrip("/")
        self.timeout = self.settings.rag_builder_timeout_seconds

    @staticmethod
    def _resolve_base_url(raw: str) -> str:
        """Prefer the Docker service hostname when running inside a container.

        ``localhost`` / ``host.docker.internal`` often break repository list
        calls from the backend container (connection closed → portal 502).
        """
        base = (raw or "http://localhost:8088").rstrip("/")
        in_docker = Path("/.dockerenv").is_file()
        if not in_docker:
            return base
        lowered = base.lower()
        for host in ("localhost", "127.0.0.1", "host.docker.internal"):
            if f"://{host}" in lowered or f"://{host}:" in lowered:
                # Preserve scheme + port when present.
                try:
                    from urllib.parse import urlparse, urlunparse

                    parsed = urlparse(base)
                    port = parsed.port or 8088
                    return urlunparse(
                        (
                            parsed.scheme or "http",
                            f"rag-dms-service:{port}",
                            "",
                            "",
                            "",
                            "",
                        )
                    ).rstrip("/")
                except Exception:
                    return "http://rag-dms-service:8088"
        return base

    def _consumer_path(self, suffix: str) -> str:
        """Build a DMS consumer API path under the configured prefix."""
        if not suffix.startswith("/"):
            suffix = f"/{suffix}"
        return f"{self.api_prefix}{suffix}"

    def _api_path(self, path: str) -> str:
        """Map legacy /v1/* call sites to the configured consumer prefix."""
        if path.startswith("/v1/") or path == "/v1":
            return self._consumer_path(path[3:])
        if path.startswith(f"{self.api_prefix}/"):
            return path
        return path

    @property
    def enabled(self) -> bool:
        return bool(self.settings.rag_builder_enabled)

    def _uses_password_auth(self) -> bool:
        return bool((self.settings.rag_builder_user or "").strip() and self.settings.rag_builder_password)

    def _uses_static_api_key(self) -> bool:
        return bool((self.settings.rag_builder_api_key or "").strip())

    def _fetch_access_token(self) -> tuple[str, int]:
        user_id = (self.settings.rag_builder_user or "").strip()
        password = self.settings.rag_builder_password or ""
        if not user_id or not password:
            raise DmsError(
                "DMS credentials not configured. Set RAG_BUILDER_USER and RAG_BUILDER_PASSWORD in backend/.env."
            )

        url = f"{self.base_url}{self.api_prefix}/auth/token"
        try:
            response = requests.post(
                url,
                json={"user_id": user_id, "password": password},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise DmsError(f"DMS auth token request failed: {exc}") from exc

        if response.status_code >= 400:
            detail = response.text
            error_code = None
            error_details = None
            try:
                parsed = parse_dms_api_error(response.json())
                detail = parsed.get("formatted_message") or response.text
                error_code = parsed.get("code")
                error_details = parsed.get("details")
            except ValueError:
                detail = response.text.strip()
            raise DmsError(
                f"DMS auth token request failed ({response.status_code}): {detail}",
                status_code=response.status_code,
                remote_detail=detail,
                error_code=error_code,
                error_details=error_details,
            )

        payload = response.json()
        access_token = str(payload.get("access_token") or "").strip()
        if not access_token:
            raise DmsError("DMS auth token response did not include access_token.")
        expires_in = int(payload.get("expires_in") or 3600)
        return access_token, max(expires_in, 60)

    def _get_access_token(self, *, force_refresh: bool = False) -> str:
        global _cached_access_token, _token_expires_at
        now = time.time()
        with _token_lock:
            if (
                not force_refresh
                and _cached_access_token
                and now < (_token_expires_at - _TOKEN_REFRESH_BUFFER_SECONDS)
            ):
                return _cached_access_token

        access_token, expires_in = self._fetch_access_token()
        with _token_lock:
            _cached_access_token = access_token
            _token_expires_at = now + expires_in
        return access_token

    def _headers(self, extra: Optional[dict[str, str]] = None, *, force_refresh: bool = False) -> dict[str, str]:
        headers = dict(extra or {})
        if self._uses_static_api_key():
            api_key = self.settings.rag_builder_api_key.strip()
            headers.setdefault("Authorization", f"Bearer {api_key}")
            headers.setdefault("X-API-Key", api_key)
        elif self._uses_password_auth():
            token = self._get_access_token(force_refresh=force_refresh)
            headers.setdefault("Authorization", f"Bearer {token}")
        return headers

    def _request(self, method: str, path: str, **kwargs) -> Any:
        if not self.enabled:
            raise DmsError("DMS integration is disabled.")
        path = self._api_path(path)
        url = f"{self.base_url}{path}"
        headers = kwargs.pop("headers", {})
        merged_headers = {**self._headers(), **headers}
        response = self._send(method, url, headers=merged_headers, **kwargs)

        if (
            response.status_code == 401
            and self._uses_password_auth()
            and not self._uses_static_api_key()
        ):
            logger.info("DMS returned 401; refreshing JWT and retrying once.")
            clear_dms_token_cache()
            merged_headers = {**self._headers(force_refresh=True), **headers}
            response = self._send(method, url, headers=merged_headers, **kwargs)

        return self._parse_response(method, url, response)

    def _send(self, method: str, url: str, *, headers: dict[str, str], **kwargs) -> requests.Response:
        try:
            timeout = kwargs.pop("timeout", self.timeout)
            return requests.request(
                method,
                url,
                timeout=timeout,
                headers=headers,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise DmsError(f"DMS request failed: {exc}") from exc

    def _parse_response(self, method: str, url: str, response: requests.Response) -> Any:
        if response.status_code >= 400:
            detail = response.text
            error_code = None
            error_details = None
            try:
                payload = response.json()
                parsed = parse_dms_api_error(payload)
                detail = parsed.get("formatted_message") or response.text
                error_code = parsed.get("code")
                error_details = parsed.get("details")
            except ValueError:
                detail = response.text.strip()
            raise DmsError(
                f"DMS {method} {url} failed ({response.status_code}): {detail}",
                status_code=response.status_code,
                remote_detail=detail,
                error_code=error_code,
                error_details=error_details,
            )

        if response.status_code == 204 or not response.content:
            return None
        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type.lower():
            return response.content
        try:
            return response.json()
        except ValueError as exc:
            raise DmsError(f"DMS returned invalid JSON for {url}") from exc

    # ------------------------------------------------------------------ health
    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    # ---------------------------------------------------------------- repositories
    def service_user_id(self) -> str:
        """DMS platform user id used for portal-provisioned repositories (RAG_BUILDER_USER)."""
        user_id = (self.settings.rag_builder_user or "").strip()
        if not user_id:
            raise DmsError(
                "DMS service user not configured. Set RAG_BUILDER_USER in backend/.env."
            )
        return user_id

    def list_repositories(self) -> dict[str, Any]:
        # UI list path — fail fast instead of waiting up to RAG_BUILDER_TIMEOUT_SECONDS.
        return self._request(
            "GET",
            self._consumer_path("/repositories"),
            timeout=min(30, int(self.timeout or 30)),
        )
    def get_repository(self, repository_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/repositories/{repository_id}")

    def create_repository(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/repositories", json=body)

    def patch_repository_settings(self, repository_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", f"/v1/repositories/{repository_id}/settings", json=body)

    def upsert_user_display_profile(self, user_id: str, display_name: str) -> dict[str, Any]:
        return self._request(
            "PUT",
            f"/v1/platform/users/{user_id}/display-profile",
            json={"display_name": display_name},
        )

    def activate_repository(self, repository_id: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/repositories/{repository_id}/activate")

    def delete_repository(self, repository_id: str) -> Any:
        return self._request("DELETE", f"/v1/repositories/{repository_id}")

    def list_embedding_models(self) -> dict[str, Any]:
        return self._request("GET", "/v1/repositories/embedding-models")

    def list_chunking_strategies(self) -> dict[str, Any]:
        return self._request("GET", "/v1/repositories/chunking-strategies")

    def list_document_types(self, *, include_metadata: bool = False) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if include_metadata:
            params["include_metadata"] = True
        payload = self._request("GET", "/v1/document-types", params=params or None)
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            items = payload.get("document_types") or payload.get("items")
            return items if isinstance(items, list) else []
        return []

    def list_repository_documents(
        self,
        repository_id: str,
        *,
        limit: int = 500,
    ) -> dict[str, Any]:
        payload = self._request(
            "GET",
            f"/v1/repositories/{repository_id}/documents",
            params={"limit": limit},
        )
        return normalize_repository_document_list_payload(payload)

    def delete_repository_document(
        self,
        repository_id: str,
        document_id: str,
        *,
        delete_file: bool = True,
    ) -> dict[str, Any]:
        return self._request(
            "DELETE",
            f"/v1/repositories/{repository_id}/documents/{document_id}",
            params={"delete_file": delete_file},
            timeout=min(60, int(self.timeout or 60)),
        )

    def purge_repository_documents(
        self,
        repository_id: str,
        *,
        delete_file: bool = True,
    ) -> dict[str, Any]:
        return self._request(
            "DELETE",
            f"/v1/repositories/{repository_id}/documents",
            params={"delete_file": delete_file},
        )

    # ------------------------------------------------------------------ documents
    def upload_documents(
        self,
        *,
        files: list[tuple[str, BinaryIO, str]],
        repository_id: Optional[str] = None,
        collection_name: Optional[str] = None,
        tenant_id: Optional[str] = None,
        submit_for_processing: bool = True,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        import json

        data: dict[str, str] = {
            "submit_for_processing": "true" if submit_for_processing else "false",
        }
        if repository_id:
            data["repository_id"] = repository_id
        if collection_name:
            data["collection_name"] = collection_name
        if tenant_id:
            data["tenant_id"] = tenant_id
        if metadata:
            data["metadata"] = json.dumps(metadata)
        multipart = [("files", item) for item in files]
        return self._request("POST", "/v1/documents/upload", files=multipart, data=data)

    def list_documents(
        self,
        *,
        repository_id: Optional[str] = None,
        status: Optional[str] = None,
        batch_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if repository_id:
            params["repository_id"] = repository_id
        if status:
            params["status"] = status
        if batch_id:
            params["batch_id"] = batch_id
        payload = self._request("GET", "/v1/documents", params=params)
        return normalize_document_list_payload(payload)

    def get_document(self, document_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/documents/{document_id}")

    def delete_document(self, document_id: str, *, delete_file: bool = True) -> dict[str, Any]:
        return self._request(
            "DELETE",
            f"/v1/documents/{document_id}",
            params={"delete_file": delete_file},
            timeout=min(60, int(self.timeout or 60)),
        )

    def delete_document_post(self, document_id: str, *, delete_file: bool = True) -> dict[str, Any]:
        """Proxy-safe delete alias documented in DMS OpenAPI."""
        return self._request(
            "POST",
            f"/v1/documents/{document_id}/delete",
            params={"delete_file": delete_file},
        )

    def get_document_status(self, document_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/documents/{document_id}/status")

    def get_document_chunks(
        self,
        document_id: str,
        *,
        limit: int = 500,
        offset: int = 0,
        include_text: bool = True,
        include_vector: bool = False,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/v1/documents/{document_id}/chunks",
            params={
                "limit": limit,
                "offset": offset,
                "include_text": include_text,
                "include_vector": include_vector,
            },
        )

    def get_document_metadata_bundle(self, document_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/documents/{document_id}/metadata")

    def patch_document_metadata(self, document_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", f"/v1/documents/{document_id}/metadata", json=body)

    def get_document_info(self, document_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/documents/{document_id}/document-info")

    def get_document_preview(self, document_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/documents/{document_id}/preview")

    def render_document(self, document_id: str) -> bytes:
        content, _ = self.render_document_with_type(document_id)
        return content

    def render_document_with_type(self, document_id: str) -> tuple[bytes, str]:
        """Stream read-only preview bytes from DMS with the response Content-Type."""
        if not self.enabled:
            raise DmsError("DMS integration is disabled.")
        path = self._api_path(f"/v1/documents/{document_id}/render")
        url = f"{self.base_url}{path}"
        headers = self._headers()
        response = self._send("GET", url, headers=headers)
        if response.status_code >= 400:
            detail = response.text
            error_code = None
            error_details = None
            try:
                parsed = parse_dms_api_error(response.json())
                detail = parsed.get("formatted_message") or response.text
                error_code = parsed.get("code")
                error_details = parsed.get("details")
            except ValueError:
                detail = response.text.strip()
            raise DmsError(
                f"DMS GET {url} failed ({response.status_code}): {detail}",
                status_code=response.status_code,
                remote_detail=detail,
                error_code=error_code,
                error_details=error_details,
            )
        media_type = response.headers.get("content-type", "application/octet-stream")
        return response.content, media_type

    def download_document(self, document_id: str) -> bytes:
        content, _media_type = self.download_document_with_type(document_id)
        return content

    def download_document_with_type(self, document_id: str) -> tuple[bytes, str]:
        """Download the original stored document bytes from DMS."""
        if not self.enabled:
            raise DmsError("DMS integration is disabled.")
        path = self._api_path(f"/v1/retrieve/documents/{document_id}/download")
        url = f"{self.base_url}{path}"
        headers = self._headers()
        response = self._send("GET", url, headers=headers)
        if response.status_code >= 400:
            detail = response.text
            error_code = None
            error_details = None
            try:
                parsed = parse_dms_api_error(response.json())
                detail = parsed.get("formatted_message") or response.text
                error_code = parsed.get("code")
                error_details = parsed.get("details")
            except ValueError:
                detail = response.text.strip()
            raise DmsError(
                f"DMS GET {url} failed ({response.status_code}): {detail}",
                status_code=response.status_code,
                remote_detail=detail,
                error_code=error_code,
                error_details=error_details,
            )
        media_type = response.headers.get("content-type", "application/octet-stream")
        return response.content, media_type

    def submit_document(self, document_id: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/documents/{document_id}/submit")

    # ------------------------------------------------------------------- retrieve
    def retrieve(
        self,
        *,
        query: str,
        top_k: int = 10,
        collection_name: Optional[str] = None,
        tenant_id: Optional[str] = None,
        document_name: Optional[str] = None,
        document_id: Optional[str] = None,
        repository_id: Optional[str] = None,
        search_mode: str = "hybrid",
        min_score: Optional[float] = None,
        explain: bool = False,
        model_name: Optional[str] = None,
        model_dir: Optional[str] = None,
        search_all_repositories: bool = False,
        repository_ids: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        filters: dict[str, Any] = {}
        if collection_name and not is_legacy_gxp_collection(collection_name):
            filters["collection_name"] = collection_name
        if document_name:
            filters["document_name"] = document_name
        if document_id:
            filters["document_id"] = document_id
        if repository_id:
            filters["repository_id"] = repository_id
        if tenant_id:
            filters["tenant_id"] = tenant_id

        payload: dict[str, Any] = {
            "query": query,
            "top_k": max(1, min(int(top_k or 10), 50)),
            "search_mode": search_mode if search_mode in {"hybrid", "vector", "keyword"} else "hybrid",
            "filters": filters,
            "explain": explain,
        }
        if repository_id:
            payload["repository_id"] = repository_id
        if search_all_repositories:
            payload["search_all_repositories"] = True
        if repository_ids:
            payload["repository_ids"] = repository_ids
        if model_name:
            payload["model_name"] = model_name
        if model_dir:
            payload["model_dir"] = model_dir
        if min_score is not None:
            payload["min_score"] = min_score
        # Hybrid search against IKM often exceeds 30s; use the configured DMS timeout.
        return self._request("POST", "/v1/retrieve", json=payload, timeout=int(self.timeout or 120))

    def list_retrieve_documents(
        self,
        *,
        repository_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if repository_id:
            params["repository_id"] = repository_id
        if status:
            params["status"] = status
        payload = self._request("GET", "/v1/retrieve/documents", params=params)
        return normalize_document_list_payload(payload)

    # ----------------------------------------------------------- repository admin
    def list_collection_chunks(
        self,
        *,
        collection_name: str,
        tenant_id: Optional[str] = None,
        document_name: Optional[str] = None,
        document_id: Optional[str] = None,
        limit: int = 200,
        include_text: bool = True,
        include_vector: bool = False,
    ) -> dict[str, Any]:
        params = {
            "limit": max(1, min(int(limit or 200), 2000)),
            "include_text": include_text,
            "include_vector": include_vector,
        }
        collection_key = str(collection_name or "").strip()
        skip_tenant = _is_repository_collection(collection_key) or collection_key in _TENANTLESS_CHUNK_COLLECTIONS
        if tenant_id and not skip_tenant:
            params["tenant"] = tenant_id
        if document_name:
            params["document_name"] = document_name
        if document_id:
            params["document_id"] = document_id
        try:
            return self._request(
                "GET",
                f"/v1/repository/collections/{collection_name}/chunks",
                params=params,
            )
        except DmsError as exc:
            if tenant_id and _should_retry_without_tenant(exc):
                if collection_key:
                    _TENANTLESS_CHUNK_COLLECTIONS.add(collection_key)
                params.pop("tenant", None)
                return self._request(
                    "GET",
                    f"/v1/repository/collections/{collection_name}/chunks",
                    params=params,
                )
            raise

    def delete_collection(self, collection_name: str) -> None:
        self._request("DELETE", f"/v1/repository/collections/{collection_name}")

    def delete_collection_document(
        self,
        *,
        collection_name: str,
        document_name: str,
        tenant_id: Optional[str] = None,
    ) -> dict[str, Any]:
        collection_key = str(collection_name or "").strip()
        params = (
            {"tenant": tenant_id}
            if tenant_id and not _is_repository_collection(collection_key) and collection_key not in _TENANTLESS_CHUNK_COLLECTIONS
            else None
        )
        try:
            return self._request(
                "DELETE",
                f"/v1/repository/collections/{collection_name}/documents/{document_name}",
                params=params,
            )
        except DmsError as exc:
            if tenant_id and _should_retry_without_tenant(exc):
                if collection_key:
                    _TENANTLESS_CHUNK_COLLECTIONS.add(collection_key)
                return self._request(
                    "DELETE",
                    f"/v1/repository/collections/{collection_name}/documents/{document_name}",
                )
            raise


# ------------------------------------------------------------------ helpers
def repository_id(repo: dict[str, Any]) -> str:
    return str(repo.get("repository_id") or "")


def weaviate_collection(repo: dict[str, Any]) -> str:
    return str(repo.get("weaviate_collection") or "").strip() or "Document"


def default_tenant_id(repo: dict[str, Any]) -> Optional[str]:
    value = repo.get("default_tenant_id")
    return str(value) if value else None


def repository_settings(repo: dict[str, Any]) -> dict[str, Any]:
    settings = repo.get("settings")
    return settings if isinstance(settings, dict) else {}


def embedding_model_name(repo: dict[str, Any]) -> Optional[str]:
    embedding = repository_settings(repo).get("embedding_model")
    if not isinstance(embedding, dict):
        return None
    return str(embedding.get("model_id") or embedding.get("local_model_dir") or "") or None


def embedding_model_dir(repo: dict[str, Any]) -> Optional[str]:
    embedding = repository_settings(repo).get("embedding_model")
    if not isinstance(embedding, dict):
        return None
    return str(embedding.get("local_model_dir") or "").strip() or None


def is_active_repository(repo: dict[str, Any]) -> bool:
    return str(repo.get("status") or "") == "active"


def is_legacy_gxp_collection(collection_name: str | None) -> bool:
    return (collection_name or "").strip().lower() == "document"


def _is_repository_collection(collection_name: str | None) -> bool:
    return (collection_name or "").strip().lower().startswith("ragrepo_")


def _should_retry_without_tenant(exc: Exception) -> bool:
    message = str(exc).lower()
    tenant_error_markers = (
        "multi-tenancy disabled",
        "multi tenancy disabled",
        "tenant not found",
        "tenant does not exist",
        "no tenant",
        "invalid tenant",
    )
    if any(marker in message for marker in tenant_error_markers):
        return True
    return any(status in message for status in ("(400)", "(404)", "(422)", "(500)", "(502)", "(503)"))


def _stats_from_document_info(doc_info: dict[str, Any]) -> dict[str, Any]:
    file_size = doc_info.get("file_size_bytes")
    chunk_count = doc_info.get("chunk_count") or doc_info.get("embedded_chunk_count")
    line_count = doc_info.get("line_count")
    return {
        "fileSize": int(file_size) if file_size is not None else None,
        "chunkCount": int(chunk_count) if chunk_count is not None else None,
        "paragraphCount": int(line_count) if line_count is not None else None,
    }


def extract_document_stats(doc: dict[str, Any]) -> dict[str, Any]:
    """Derive portal fileSize / chunkCount / paragraphCount from DMS document payloads."""
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    doc_info = metadata.get("document_info") if isinstance(metadata.get("document_info"), dict) else {}
    if not doc_info and isinstance(doc.get("document_info"), dict):
        doc_info = doc["document_info"]

    processing = doc.get("processing") if isinstance(doc.get("processing"), dict) else {}
    processor_results = processing.get("processor_results") if isinstance(processing.get("processor_results"), dict) else {}
    chunk_result = (
        processor_results.get("chunking_vectorizing")
        if isinstance(processor_results.get("chunking_vectorizing"), dict)
        else {}
    )
    chunk_meta = chunk_result.get("document_metadata") if isinstance(chunk_result.get("document_metadata"), dict) else {}

    preview_info = doc.get("preview_document_info") if isinstance(doc.get("preview_document_info"), dict) else {}

    file_size = (
        doc_info.get("file_size_bytes")
        or chunk_meta.get("file_size_bytes")
        or preview_info.get("file_size_bytes")
        or doc.get("file_size_bytes")
    )
    chunk_count = (
        doc_info.get("chunk_count")
        or doc_info.get("embedded_chunk_count")
        or chunk_meta.get("chunk_count")
        or chunk_meta.get("embedded_chunk_count")
        or preview_info.get("chunk_count")
        or preview_info.get("embedded_chunk_count")
    )
    line_count = doc_info.get("line_count") or chunk_meta.get("line_count") or preview_info.get("line_count")

    return {
        "fileSize": int(file_size) if file_size is not None else None,
        "chunkCount": int(chunk_count) if chunk_count is not None else None,
        "paragraphCount": int(line_count) if line_count is not None else None,
    }


def enrich_document_stats_from_dms(client: "DmsClient", doc: dict[str, Any], stats: dict[str, Any]) -> dict[str, Any]:
    """Fill missing stats via DMS document-info when list payloads omit processor metadata."""
    document_id = str(doc.get("document_id") or doc.get("fileId") or "").strip()
    needs_info = (
        stats.get("fileSize") is None
        or stats.get("chunkCount") is None
        or _is_internal_storage_filename(
            str(doc.get("original_file_name") or doc.get("document_name") or ""),
            document_id,
        )
    )
    if not needs_info or not document_id:
        return stats
    try:
        info = client.get_document_info(document_id)
    except DmsError:
        return stats
    if not isinstance(info, dict):
        return stats
    resolved = _stats_from_document_info(info)
    enriched = {
        "fileSize": stats.get("fileSize") if stats.get("fileSize") is not None else resolved.get("fileSize"),
        "chunkCount": stats.get("chunkCount") if stats.get("chunkCount") is not None else resolved.get("chunkCount"),
        "paragraphCount": (
            stats.get("paragraphCount") if stats.get("paragraphCount") is not None else resolved.get("paragraphCount")
        ),
    }
    display_name = str(info.get("original_file_name") or "").strip()
    if display_name and not _is_internal_storage_filename(display_name, document_id):
        enriched["originalFilename"] = display_name
    return enriched


def _extracted_fields_map(type_metadata: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in type_metadata.get("fields") or []:
        if not isinstance(field, dict):
            continue
        name = str(field.get("field_name") or field.get("name") or "").strip()
        if not name:
            continue
        out[name.lower().replace(" ", "_")] = field.get("value")
    return out


def map_dms_chunks_to_portal(
    chunks_payload: dict[str, Any],
    metadata_bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Transform DMS chunk listing into the portal DocumentChunksResponse shape."""
    bundle = metadata_bundle if isinstance(metadata_bundle, dict) else {}
    doc_info = bundle.get("document_info") if isinstance(bundle.get("document_info"), dict) else {}
    if not doc_info and isinstance(chunks_payload.get("document_info"), dict):
        doc_info = chunks_payload["document_info"]
    type_meta = bundle.get("type_metadata") if isinstance(bundle.get("type_metadata"), dict) else {}
    fields = _extracted_fields_map(type_meta)

    document_id = str(chunks_payload.get("document_id") or "")
    original_name = resolve_portal_document_filename(
        {
            "document_id": document_id,
            "original_file_name": chunks_payload.get("original_file_name"),
            "document_name": chunks_payload.get("document_name"),
            "document_info": doc_info,
            "metadata": {"document_info": doc_info},
        }
    )
    chunk_count = doc_info.get("chunk_count") or chunks_payload.get("returned")

    document = {
        "id": 0,
        "fileId": document_id,
        "originalFilename": original_name,
        "fileSize": doc_info.get("file_size_bytes"),
        "fileType": doc_info.get("file_extension") or doc_info.get("document_type"),
        "status": "completed",
        "documentName": doc_info.get("document_name") or chunks_payload.get("document_name"),
        "documentTitle": fields.get("document_title") or fields.get("title") or original_name,
        "documentNumber": fields.get("document_number"),
        "department": fields.get("department"),
        "subOrganization": fields.get("sub_organization"),
        "documentType": doc_info.get("document_type") or fields.get("document_type"),
        "documentCategory": fields.get("document_category") or fields.get("category"),
        "author": fields.get("author"),
        "versionNumber": fields.get("version_number") or fields.get("version"),
        "pageCount": doc_info.get("page_count"),
        "paragraphCount": doc_info.get("line_count"),
        "chunkCount": int(chunk_count) if chunk_count is not None else None,
        "createdAt": doc_info.get("completed_at"),
        "processingCompletedAt": doc_info.get("completed_at"),
    }

    mapped_chunks: list[dict[str, Any]] = []
    for idx, chunk in enumerate(chunks_payload.get("chunks") or []):
        if not isinstance(chunk, dict):
            continue
        chunk_id = chunk.get("chunk_id")
        chunk_index = idx
        if chunk_id is not None:
            try:
                chunk_index = int(chunk_id)
            except (TypeError, ValueError):
                chunk_index = idx
        mapped_chunks.append(
            {
                "content": chunk.get("text") or "",
                "chunkIndex": chunk_index,
                "pageNumber": chunk.get("page"),
                "sectionName": chunk.get("section_name"),
                "sectionPath": chunk.get("section_path"),
                "sourceType": chunk.get("category"),
                "metadata": {key: value for key, value in chunk.items() if key != "text"},
            }
        )

    return {
        "document": document,
        "chunks": mapped_chunks,
        "count": int(chunks_payload.get("returned") or len(mapped_chunks)),
    }


def map_dms_preview_to_portal(payload: dict[str, Any], *, file_id: str) -> dict[str, Any]:
    """Map DMS preview payload to the portal preview JSON contract."""
    return {
        "file_id": payload.get("document_id") or file_id,
        "filename": payload.get("original_file_name") or payload.get("document_name") or file_id,
        "html_content": payload.get("content"),
        "format": payload.get("format"),
        "media_type": payload.get("media_type"),
        "source": payload.get("source"),
        "read_only": payload.get("read_only", True),
    }


def map_dms_status(status: str | None) -> str:
    key = (status or "").strip().lower().replace("-", "_")
    if key in {"completed", "complete", "success", "succeeded"}:
        return "completed"
    if key in {"failed", "timed_out", "queue_failed", "error"}:
        return "failed"
    if key in {"processing", "in_progress", "started", "starting", "assigned", "dispatching"}:
        return "processing"
    if key in {"uploaded", "queued", "queue", "pending", "submitted", "registered", "received"}:
        return "queued"
    if key in {"missing"}:
        return "failed"
    return "queued"
