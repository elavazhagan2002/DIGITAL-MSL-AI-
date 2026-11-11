"""
Repository & Document Management Routes for Standalone Research Chat.
Proxies repository create/list, document upload/list/chunks, and document preview to DMS.
"""
from __future__ import annotations
import io
import logging
import mimetypes
import re
from typing import Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from models.models import User
from routes.auth import get_current_user
from routes.api_principal import ApiPrincipal, assert_repository_access_for_principal, resolve_api_principal
from routes.dms_proxy_helpers import dms, dms_http_error, require_repository_access
from services.dms_client import DmsError
from digital_msl import store as dmsl_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["repositories", "documents"])

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt"}


class CreateRepositoryRequest(BaseModel):
    name: str = Field(..., min_length=3, max_length=80)
    description: str = Field("", max_length=500)


@router.get("/repositories")
async def list_repositories(
    current_user: User = Depends(get_current_user),
    principal: ApiPrincipal = Depends(resolve_api_principal),
):
    """List repositories from DMS. Administrator and Physical MSL only: a doctor's sources come from /me/context."""
    if principal.role == "doctor":
        raise HTTPException(status_code=403, detail="Repository listing is not available for this role.")
    try:
        data = dms().list_repositories()
        repos = data.get("repositories") if isinstance(data, dict) else data
        return {"repositories": repos or [], "count": len(repos or [])}
    except DmsError as exc:
        raise dms_http_error(exc) from exc
    except Exception as exc:
        from digital_msl.upstream import UpstreamUnavailable

        raise UpstreamUnavailable(exc)


@router.post("/repositories")
async def create_repository(body: CreateRepositoryRequest, current_user: User = Depends(get_current_user)):
    """Create a retina repository in DMS (the name is also its Weaviate collection name)."""
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", body.name.strip()).strip("_")
    if len(name) < 3:
        raise HTTPException(status_code=422, detail="Repository name must contain at least 3 letters or digits.")
    settings = {
        "therapeutic_scope": "retina",
        "description": body.description,
        "transient_connectors": {"enabled": True, "connectors": ["pubmed", "clinicaltrials"]},
        "medical_literacy_research": True,
    }
    try:
        repo = dms().create_repository({"name": name, "settings": settings})
    except DmsError as exc:
        raise dms_http_error(exc) from exc
    repo_id = str((repo or {}).get("repository_id") or "")
    if repo_id and str((repo or {}).get("status") or "").lower() not in {"active", ""}:
        try:
            repo = dms().activate_repository(repo_id) or repo
        except DmsError as exc:
            logger.warning("Repository %s created but activation failed: %s", repo_id, exc)
    return repo


@router.get("/repositories/{repository_id}")
async def get_repository(repository_id: str, current_user: User = Depends(get_current_user)):
    """Get repository details."""
    repo = require_repository_access(repository_id, current_user)
    return repo


@router.get("/repositories/{repository_id}/documents")
async def list_repository_documents(
    repository_id: str,
    limit: int = 100,
    current_user: User = Depends(get_current_user)
):
    """List documents in a target repository."""
    from digital_msl import load_documents

    if load_documents.is_demo_repository(repository_id):  # demo mode only, labelled demo data
        docs = load_documents.demo_documents()
        return {"documents": docs, "count": len(docs), "demo": True}
    require_repository_access(repository_id, current_user)
    try:
        data = dms().list_repository_documents(repository_id, limit=limit)
        docs = data.get("documents") if isinstance(data, dict) else []
        return {"documents": docs, "count": len(docs)}
    except DmsError as exc:
        raise dms_http_error(exc) from exc


@router.post("/repositories/{repository_id}/documents/upload")
async def upload_repository_documents(
    repository_id: str,
    files: list[UploadFile] = File(...),
    folder_id: str = Form(""),
    current_user: User = Depends(get_current_user),
    principal: ApiPrincipal = Depends(resolve_api_principal),
):
    """Upload PDF/DOCX/TXT files. Requires portal Bearer session unless RESEARCH_CHAT_ALLOW_DEV_AUTH=true."""
    assert_repository_access_for_principal(principal, repository_id)
    require_repository_access(repository_id, current_user)
    if folder_id and not dmsl_store.folder_belongs_to_repository(folder_id, repository_id):
        raise HTTPException(status_code=422, detail="folder_id does not belong to this repository.")
    payloads = []
    for upload in files:
        filename = upload.filename or "document"
        ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
        if ext not in ALLOWED_UPLOAD_EXTENSIONS:
            raise HTTPException(status_code=422, detail=f"Unsupported file type: {filename}")
        content = await upload.read()
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"File too large: {filename}")
        mime = upload.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        payloads.append((filename, content, mime))
    try:
        result = dms().upload_documents(
            files=[(name, io.BytesIO(data), mime) for name, data, mime in payloads],
            repository_id=repository_id,
            submit_for_processing=True,
            metadata={"uploaded_by": principal.user_id, "source": "manual_upload"},
        )
    except DmsError as exc:
        raise dms_http_error(exc) from exc
    uploaded = result.get("documents") if isinstance(result, dict) else result
    if isinstance(uploaded, dict):
        uploaded = uploaded.get("documents") or [uploaded]
    for doc in uploaded or []:
        if not isinstance(doc, dict):
            continue
        doc_id = str(doc.get("document_id") or doc.get("id") or "")
        if not doc_id:
            continue
        dmsl_store.set_document_folder(doc_id, repository_id, folder_id or "")
        if principal.source == "portal":
            dmsl_store.record_audit_event(
                action="document_upload",
                user_id=principal.user_id,
                role=principal.role,
                session_id=principal.session_id,
                repository_id=repository_id,
                entity_type="document",
                entity_id=doc_id,
                detail={"folder_id": folder_id or "", "file_name": doc.get("original_file_name") or ""},
            )
    return result


@router.get("/documents/{document_id}/chunks")
async def get_document_chunks(
    document_id: str,
    limit: int = 200,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
):
    """Extracted chunks for a document: page, section, paragraph and text."""
    try:
        data = dms().get_document_chunks(document_id, limit=limit, offset=offset, include_text=True)
    except DmsError as exc:
        raise dms_http_error(exc) from exc
    chunks = []
    for raw in (data or {}).get("chunks") or []:
        props = raw.get("properties") if isinstance(raw.get("properties"), dict) else {}
        chunks.append(
            {
                "chunk_id": raw.get("chunk_id") or raw.get("uuid"),
                "page": raw.get("page") or props.get("page_label"),
                "section": raw.get("section_path") or raw.get("section_name") or props.get("heading") or "",
                "line_start": props.get("line_start"),
                "line_end": props.get("line_end"),
                "strategy": props.get("strategy_name"),
                "text": raw.get("text") or "",
            }
        )
    return {
        "document_id": document_id,
        "document_name": (data or {}).get("original_file_name") or (data or {}).get("document_name"),
        "chunks": chunks,
        "returned": len(chunks),
    }


@router.get("/documents/{document_id}/preview")
async def get_document_preview(document_id: str, current_user: User = Depends(get_current_user)):
    """Retrieve document preview text and metadata from DMS."""
    try:
        return dms().get_document_preview(document_id)
    except DmsError as exc:
        raise dms_http_error(exc) from exc


@router.get("/documents/{document_id}/render")
async def render_document(document_id: str, current_user: User = Depends(get_current_user)):
    """Render document bytes (HTML or PDF) from DMS."""
    try:
        content, media_type = dms().render_document_with_type(document_id)
        return Response(content=content, media_type=media_type)
    except DmsError as exc:
        raise dms_http_error(exc) from exc
