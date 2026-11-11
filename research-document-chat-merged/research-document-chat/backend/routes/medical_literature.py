"""
Medical Research Routes - PubMed and ClinicalTrials.gov Search & Ingestion.
Extracted from views/medical_literacy_routes.py.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import mimetypes
import threading
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from models.models import User
from routes.auth import get_current_user
from routes.dms_proxy_helpers import dms, dms_http_error, require_repository_access
from services.dms_client import DmsError

from connectors.ingestion import (
    EvidencePreview,
    enrich_pubmed_previews_with_pmc,
    ingest_selected_evidence,
    search_external_evidence,
)
from connectors.connectors import configured_connectors, resolve_search_connectors
from connectors.pubmed_client import PubMedClient
from connectors.config import load_config
from connectors.mesh import query_title_accuracy
from connectors.import_registry import build_registry_patch
from digital_msl.scope import classify_query, is_retina_relevant_record

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/medical-literature", tags=["medical-literature"])

CONNECTOR_CATALOG = [
    {"id": "pubmed", "label": "PubMed"},
    {"id": "clinicaltrials", "label": "ClinicalTrials.gov"},
]


class LiteratureSearchRequest(BaseModel):
    repository_id: str = Field(..., min_length=1)
    query: str = Field(..., min_length=1, max_length=2000)
    connectors: list[str] = Field(default_factory=list)
    free_full_text_only: bool = False
    format_terms_as_mesh: bool = False
    pubmed_max_results: int = Field(50, ge=1, le=100)
    clinicaltrials_max_results: int = Field(25, ge=1, le=100)


class LiteratureImportSelection(BaseModel):
    source: str
    record_id: str
    published_date: str = ""
    title: str = ""
    subtitle: str = ""
    url: str = ""
    pmcid: Optional[str] = None
    has_pmc_pdf: bool = False
    pmc_url: Optional[str] = None
    summary: str = ""
    pdf_urls: list[str] = Field(default_factory=list)


class LiteratureImportRequest(BaseModel):
    repository_id: str = Field(..., min_length=1)
    selections: list[LiteratureImportSelection] = Field(..., min_length=1)


def _preview_to_dict(preview: Any, *, query: str = "") -> dict[str, Any]:
    accuracy = query_title_accuracy(getattr(preview, "title", "") or "", query)
    return {
        "source": preview.source,
        "record_id": preview.record_id,
        "title": preview.title,
        "subtitle": preview.subtitle,
        "summary": preview.summary,
        "url": preview.url,
        "published_date": preview.published_date,
        "pmcid": preview.pmcid,
        "has_pmc_pdf": bool(preview.has_pmc_pdf),
        "already_loaded": bool(getattr(preview, "already_loaded", False)),
        "pdf_urls": list(getattr(preview, "pdf_urls", ()) or ()),
        "pmc_url": preview.pmc_url,
        "selection_key": preview.selection_key,
        "accuracy_pct": accuracy,
    }


def _with_default_connectors(repo: dict[str, Any]) -> dict[str, Any]:
    """Standalone default (matches the frontend): repositories without connector
    settings use PubMed + ClinicalTrials.gov."""
    if configured_connectors(repo):
        return repo
    settings = dict(repo.get("settings") or {})
    settings["transient_connectors"] = {"enabled": True, "connectors": ["pubmed", "clinicaltrials"]}
    return {**repo, "settings": settings}


def _dms_upload_fn(user_id: str):
    def upload_fn(
        file_payloads: list[tuple[str, bytes]],
        *,
        collection_name: str,
        tenant_id: str,
        repository_id: str | None,
        classification_category_id: str | None,
    ) -> dict[str, Any]:
        multipart: list[tuple[str, Any, str]] = []
        for filename, content in file_payloads:
            handle = io.BytesIO(content)
            mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            multipart.append((filename, handle, mime))
        metadata: dict[str, Any] = {"uploaded_by": user_id, "source": "medical_literacy"}
        if classification_category_id:
            metadata["classification_category_id"] = classification_category_id
        return dms().upload_documents(
            files=multipart,
            repository_id=repository_id,
            collection_name=collection_name or None,
            tenant_id=tenant_id or None,
            submit_for_processing=True,
            metadata=metadata,
        )

    return upload_fn


@router.get("/connectors")
async def list_connectors(current_user: User = Depends(get_current_user)):
    return {"connectors": CONNECTOR_CATALOG, "count": len(CONNECTOR_CATALOG)}


@router.post("/search")
async def search_literature(
    body: LiteratureSearchRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    scope = classify_query(body.query)
    if not scope.in_scope:
        raise HTTPException(
            status_code=422,
            detail=f"Search is limited to retina / eye-care. {scope.reason}",
        )
    repo = require_repository_access(body.repository_id, current_user)
    try:
        docs_payload = await asyncio.to_thread(
            lambda: dms().list_repository_documents(body.repository_id, limit=500)
        )
        repository_documents = docs_payload.get("documents") or []
    except Exception:
        repository_documents = []

    repo = _with_default_connectors(repo)
    selected = [c.strip().lower() for c in body.connectors if str(c).strip()]
    active = resolve_search_connectors(repo, selected_connectors=selected or None)
    if not active:
        raise HTTPException(status_code=422, detail="Select at least one connector enabled on this repository.")

    cancel = threading.Event()

    def _run_search() -> tuple[list[Any], list[str]]:
        previews, errors = search_external_evidence(
            mesh_terms=body.query,
            repository=repo,
            pubmed_max_results=body.pubmed_max_results,
            clinicaltrials_max_results=body.clinicaltrials_max_results,
            connectors=active,
            pubmed_open_access_only=body.free_full_text_only,
            pubmed_mesh_mode=body.format_terms_as_mesh,
            repository_documents=repository_documents,
            exclude_existing=True,
            cancel_event=cancel,
        )
        if cancel.is_set():
            raise RuntimeError("Literature search cancelled.")
        
        cfg = load_config()
        settings = dict(repo.get("settings") or {})
        sources = dict(settings.get("medical_literacy_sources") or {})
        pubmed_cfg = cfg.resolve_pubmed_sources(sources)
        pubmed_client = PubMedClient(
            base_url=pubmed_cfg["base_url"],
            api_key=pubmed_cfg["api_key"],
            email=pubmed_cfg["email"],
        )
        pubmed_client._cancel_event = cancel
        previews = enrich_pubmed_previews_with_pmc(previews, pubmed_client=pubmed_client)
        return previews, errors

    search_task = asyncio.create_task(asyncio.to_thread(_run_search))

    async def _watch_disconnect() -> None:
        while not search_task.done():
            if await request.is_disconnected():
                cancel.set()
                logger.info("Medical literature search cancelled — client disconnected")
                return
            await asyncio.sleep(0.35)

    watcher = asyncio.create_task(_watch_disconnect())
    try:
        previews, errors = await search_task
    except Exception as exc:
        if cancel.is_set():
            raise HTTPException(status_code=499, detail="Literature search cancelled.") from exc
        logger.exception("Medical literature search failed")
        raise HTTPException(status_code=502, detail=f"Literature search failed: {exc}") from exc
    finally:
        cancel.set()
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher

    in_scope = [p for p in previews if is_retina_relevant_record(p.title, p.subtitle, p.summary)]
    results = [_preview_to_dict(p, query=body.query) for p in in_scope[:200]]
    results.sort(key=lambda row: (-int(row.get("accuracy_pct") or 0), str(row.get("title") or "")))
    return {
        "results": results,
        "count": len(results),
        "errors": errors,
        "connectors_used": active,
        "scope": {
            **scope.to_dict(),
            "excluded_out_of_scope": len(previews) - len(in_scope),
        },
    }


@router.post("/import-to-repository")
@router.post("/import")
async def import_literature(
    body: LiteratureImportRequest,
    current_user: User = Depends(get_current_user),
):
    repo = _with_default_connectors(require_repository_access(body.repository_id, current_user))

    preview_records: list[EvidencePreview] = []
    pubmed_pmids: list[str] = []
    nct_ids: list[str] = []
    out_of_scope: list[str] = []
    for item in body.selections:
        source = str(item.source or "").strip()
        record_id = str(item.record_id or "").strip()
        if not source or not record_id:
            continue
        if not is_retina_relevant_record(item.title, item.subtitle, item.summary):
            out_of_scope.append(record_id)
            continue
        preview = EvidencePreview(
            source=source,
            record_id=record_id,
            title=item.title or record_id,
            subtitle=item.subtitle or "",
            summary=item.summary or "",
            url=item.url or "",
            published_date=item.published_date or "",
            pmcid=item.pmcid,
            has_pmc_pdf=bool(item.has_pmc_pdf),
            pdf_urls=tuple(item.pdf_urls or ()),
            pmc_url=item.pmc_url,
        )
        preview_records.append(preview)
        if source.lower() == "pubmed":
            pubmed_pmids.append(record_id)
        elif "clinicaltrials" in source.lower():
            nct_ids.append(record_id)

    if not preview_records:
        detail = "No valid publication selections provided."
        if out_of_scope:
            detail = f"Selections are outside retina / eye-care scope: {', '.join(out_of_scope)}"
        raise HTTPException(status_code=400, detail=detail)

    try:
        docs_payload = dms().list_repository_documents(body.repository_id, limit=500)
        repository_documents = docs_payload.get("documents") or []
    except Exception:
        repository_documents = []

    try:
        result = ingest_selected_evidence(
            repository=repo,
            upload_fn=_dms_upload_fn(str(current_user.user_id)),
            pubmed_pmids=pubmed_pmids,
            clinicaltrials_nct_ids=nct_ids,
            preview_records=preview_records,
            repository_documents=repository_documents,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DmsError as exc:
        raise dms_http_error(exc) from exc
    except Exception as exc:
        logger.exception("Medical literature import failed")
        raise HTTPException(status_code=502, detail=f"Literature import failed: {exc}") from exc

    registry_patch = build_registry_patch(repo, result.registry_entries)
    try:
        if registry_patch.get("connector_import_registry"):
            dms().patch_repository_settings(body.repository_id, registry_patch)
    except Exception as exc:
        logger.warning("Failed to persist connector import registry: %s", exc)

    return {
        "status": "success",
        "uploaded_count": len(result.uploaded_documents),
        "uploaded_documents": result.uploaded_documents,
        "pubmed_count": result.pubmed_count,
        "clinicaltrials_count": result.clinicaltrials_count,
        "skipped_count": result.skipped_count,
        "errors": result.errors + [f"{rid}: outside retina scope, not imported" for rid in out_of_scope],
        "registry_entries": result.registry_entries,
    }
