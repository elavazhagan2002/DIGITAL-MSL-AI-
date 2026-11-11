from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from connectors.clinicaltrials_client import ClinicalTrialRecord, ClinicalTrialsClient
from connectors.config import MedicalLiteracyConfig, load_config
from connectors.connectors import (
    CONNECTOR_CLINICALTRIALS,
    CONNECTOR_PUBMED,
    CONNECTOR_S3,
    CONNECTOR_SHAREPOINT,
    resolve_search_connectors,
)
from connectors.import_registry import (
    LoadedRecordIndex,
    build_loaded_record_index,
    exclude_loaded_connector_previews,
    is_connector_record_loaded,
    is_already_imported,
    new_registry_entry,
)
from connectors.mesh import normalize_mesh_terms, title_matches_query
from connectors.pubmed_client import PubMedClient
from connectors.pubmed_pmc import (
    batch_resolve_pmc_pdf_availability,
    fetch_pmc_document,
    is_pmc_pdf_downloadable,
    resolve_pmcids,
    search_europepmc_medline,
)

_logger = logging.getLogger(__name__)

UploadFn = Callable[..., dict[str, Any]]
UPLOAD_BATCH_SIZE = 50
_PUBMED_SEARCH_PAGE_SIZE = 50
_PUBMED_MAX_CANDIDATES_SCANNED = 2000


@dataclass
class IngestionResult:
    pubmed_count: int
    clinicaltrials_count: int
    uploaded_documents: list[dict[str, Any]]
    errors: list[str]
    skipped_count: int = 0
    registry_entries: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class EvidencePreview:
    """One searchable external record the user can select before upload."""

    source: str
    record_id: str
    title: str
    subtitle: str
    summary: str
    url: str
    published_date: str = ""
    pmcid: str | None = None
    has_pmc_pdf: bool = False
    already_loaded: bool = False
    pdf_urls: tuple[str, ...] = ()
    pmc_url: str | None = None

    @property
    def selection_key(self) -> str:
        return f"{self.source}:{self.record_id}:{self.published_date}"


def _build_clients(
    repository: dict[str, Any],
    config: MedicalLiteracyConfig | None = None,
) -> tuple[MedicalLiteracyConfig, PubMedClient, ClinicalTrialsClient]:
    cfg = config or load_config()
    settings = dict(repository.get("settings") or {})
    sources = dict(settings.get("medical_literacy_sources") or {})
    pubmed_cfg = cfg.resolve_pubmed_sources(sources)
    pubmed_client = PubMedClient(
        base_url=pubmed_cfg["base_url"],
        api_key=pubmed_cfg["api_key"],
        email=pubmed_cfg["email"],
    )
    trials_client = ClinicalTrialsClient(
        base_url=cfg.resolve_clinicaltrials_url(sources),
    )
    return cfg, pubmed_client, trials_client


def _pubmed_fetch_kwargs(pubmed_client: PubMedClient) -> dict[str, Any]:
    return {
        "session": pubmed_client.session,
        "email": pubmed_client.email,
        "api_key": pubmed_client.api_key,
        "tool": pubmed_client.tool,
        "entrez_base_url": pubmed_client.base_url,
    }


def _trial_published_date(record: ClinicalTrialRecord, raw_study: dict[str, Any] | None = None) -> str:
    from connectors.clinicaltrials_client import _trial_published_date_from_status

    if raw_study:
        protocol = raw_study.get("protocolSection") or {}
        status_mod = protocol.get("statusModule") or {}
        if isinstance(status_mod, dict):
            date_value = _trial_published_date_from_status(status_mod)
            if date_value:
                return date_value
    return str(getattr(record, "published_date", "") or "").strip()


def _summaries_to_previews(
    summaries: list[Any],
    *,
    repository: dict[str, Any],
    pmc_map: dict[str, Any] | None = None,
    pdf_availability: dict[str, Any] | None = None,
    loaded_index: LoadedRecordIndex | None = None,
) -> list[EvidencePreview]:
    previews: list[EvidencePreview] = []
    pmc_map = pmc_map or {}
    pdf_availability = pdf_availability or {}
    for item in summaries:
        pmc = pmc_map.get(item.pmid)
        pmcid = pmc.pmcid if pmc else None
        pdf_info = pdf_availability.get(item.pmid)
        has_pdf = bool(pmcid and (pdf_info.has_pdf if pdf_info else False))
        published_date = item.published_date or item.year
        pmc_url = f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/" if pmcid else None
        previews.append(
            EvidencePreview(
                source="PubMed",
                record_id=item.pmid,
                title=item.title,
                subtitle=item.journal,
                summary=_pubmed_preview_summary(
                    pmid=item.pmid,
                    pmcid=pmcid,
                    has_pmc_pdf=has_pdf,
                ),
                url=item.url,
                published_date=published_date,
                pmcid=pmcid,
                has_pmc_pdf=has_pdf,
                pdf_urls=pdf_info.pdf_urls if pdf_info else (),
                pmc_url=pmc_url,
                already_loaded=is_connector_record_loaded(
                    repository,
                    source="PubMed",
                    record_id=item.pmid,
                    published_date=published_date,
                    loaded_index=loaded_index,
                ),
            )
        )
    return previews


def _pubmed_preview_summary(*, pmid: str, pmcid: str | None, has_pmc_pdf: bool) -> str:
    summary = f"PMID {pmid}"
    if pmcid:
        summary += f" | PMC {pmcid}"
    if has_pmc_pdf:
        summary += " | Free PMC article"
    return summary


def enrich_pubmed_previews_with_pmc(
    previews: list[EvidencePreview],
    *,
    pubmed_client: PubMedClient,
) -> list[EvidencePreview]:
    """Resolve missing PMCIDs via NCBI ID Converter and refresh PDF availability."""
    pubmed_previews = [preview for preview in previews if preview.source == "PubMed"]
    if not pubmed_previews:
        return previews

    pmids = [preview.record_id for preview in pubmed_previews]
    pmc_map: dict[str, Any] = {}
    try:
        pmc_map = resolve_pmcids(
            pmids,
            session=pubmed_client.session,
            email=pubmed_client.email,
            tool=pubmed_client.tool,
        )
    except Exception:
        _logger.exception("NCBI ID Converter lookup failed during PubMed preview enrichment")

    pmid_to_pmcid: dict[str, str | None] = {}
    for preview in pubmed_previews:
        resolved = pmc_map.get(preview.record_id)
        pmcid = (resolved.pmcid if resolved else None) or preview.pmcid
        pmid_to_pmcid[preview.record_id] = pmcid

    pdf_availability: dict[str, Any] = {}
    try:
        pdf_availability = batch_resolve_pmc_pdf_availability(
            pmid_to_pmcid,
            session=pubmed_client.session,
            email=pubmed_client.email,
            tool=pubmed_client.tool,
        )
    except Exception:
        _logger.exception("PMC PDF availability lookup failed during PubMed preview enrichment")

    enriched_by_pmid: dict[str, EvidencePreview] = {}
    for preview in pubmed_previews:
        pmcid = pmid_to_pmcid.get(preview.record_id)
        pdf_info = pdf_availability.get(preview.record_id)
        pdf_urls = tuple(
            dict.fromkeys(
                url
                for url in (
                    *(preview.pdf_urls or ()),
                    *(pdf_info.pdf_urls if pdf_info else ()),
                )
                if url
            )
        )
        has_pmc_pdf = is_pmc_pdf_downloadable(
            pmcid,
            pdf_urls=pdf_urls,
        )
        pmc_url = f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/" if pmcid else None
        enriched_by_pmid[preview.record_id] = EvidencePreview(
            source=preview.source,
            record_id=preview.record_id,
            title=preview.title,
            subtitle=preview.subtitle,
            summary=_pubmed_preview_summary(pmid=preview.record_id, pmcid=pmcid, has_pmc_pdf=has_pmc_pdf),
            url=preview.url,
            published_date=preview.published_date,
            pmcid=pmcid,
            has_pmc_pdf=has_pmc_pdf,
            already_loaded=preview.already_loaded,
            pdf_urls=pdf_urls,
            pmc_url=pmc_url,
        )

    return [
        enriched_by_pmid.get(preview.record_id, preview)
        if preview.source == "PubMed"
        else preview
        for preview in previews
    ]


def filter_previews_with_valid_pmcid(previews: list[EvidencePreview]) -> list[EvidencePreview]:
    """Keep only PubMed records with a PMCID and importable PMC full text."""
    filtered: list[EvidencePreview] = []
    for preview in previews:
        if preview.source != "PubMed":
            filtered.append(preview)
            continue
        pmcid = str(preview.pmcid or "").strip()
        if pmcid and preview.has_pmc_pdf:
            filtered.append(preview)
    return filtered


def _pubmed_previews_from_ncbi(
    pubmed_client: PubMedClient,
    *,
    terms: list[str],
    pubmed_limit: int,
    open_access_only: bool,
    mesh_mode: bool,
    repository: dict[str, Any],
    loaded_index: LoadedRecordIndex | None = None,
) -> list[EvidencePreview]:
    """Fetch title-focused PubMed hits until `pubmed_limit` PMC PDF-ready rows are filled.

    Import only needs downloadable PMC full text, so non-PDF rows never consume the
    requested count. NCBI is searched with free-full-text when the UI asks for it;
    PDF-ready filtering always applies either way.
    """
    target = max(1, min(pubmed_limit, 100))
    collected: list[EvidencePreview] = []
    collected_pmids: set[str] = set()
    scanned_pmids = 0
    retstart = 0
    total_count: int | None = None
    # Prefer free-full-text NCBI results so PDF-ready rows appear sooner; if that
    # pool is exhausted before `target`, widen the search once without the filter.
    oa_passes = (True,) if open_access_only else (True, False)

    for use_oa in oa_passes:
        retstart = 0
        total_count = None
        while len(collected) < target:
            if scanned_pmids >= _PUBMED_MAX_CANDIDATES_SCANNED:
                break
            if total_count is not None and retstart >= total_count:
                break

            page = pubmed_client.search_page(
                terms,
                retmax=_PUBMED_SEARCH_PAGE_SIZE,
                retstart=retstart,
                open_access_only=bool(use_oa),
                mesh_mode=mesh_mode,
            )
            if total_count is None:
                total_count = page.total_count

            page_pmids = [pmid for pmid in page.pmids if pmid not in collected_pmids]
            scanned_pmids += len(page.pmids)
            retstart += len(page.pmids)

            if not page_pmids:
                if not page.pmids:
                    break
                continue

            summaries = pubmed_client.fetch_summaries(page_pmids)
            batch_previews = _summaries_to_previews(
                summaries,
                repository=repository,
                loaded_index=loaded_index,
            )
            batch_previews = enrich_pubmed_previews_with_pmc(
                batch_previews, pubmed_client=pubmed_client
            )
            # Always keep importable PMC PDF-ready rows only.
            candidates = filter_previews_with_valid_pmcid(batch_previews)
            for preview in candidates:
                if preview.record_id in collected_pmids:
                    continue
                collected_pmids.add(preview.record_id)
                # Already-in-repo hits must not consume a slot — keep paging to refill.
                if preview.already_loaded:
                    continue
                # Prefer title matches; skip incidental abstract-only noise if any slip through.
                if not mesh_mode and not title_matches_query(preview.title, terms):
                    continue
                collected.append(preview)
                if len(collected) >= target:
                    return collected[:target]

        if len(collected) >= target:
            break

    return collected[:target]


def _pubmed_previews_from_europe_pmc(
    *,
    terms: list[str],
    pubmed_limit: int,
    open_access_only: bool,
    repository: dict[str, Any],
    email: str = "",
    tool: str = "rag-builder-mlr",
    loaded_index: LoadedRecordIndex | None = None,
) -> list[EvidencePreview]:
    records = search_europepmc_medline(
        terms,
        page_size=pubmed_limit,
        open_access_only=open_access_only,
        email=email,
        tool=tool,
    )
    previews: list[EvidencePreview] = []
    for record in records:
        pmc_url = f"https://pmc.ncbi.nlm.nih.gov/articles/{record.pmcid}/" if record.pmcid else None
        previews.append(
            EvidencePreview(
                source="PubMed",
                record_id=record.pmid,
                title=record.title,
                subtitle=record.journal,
                summary=f"PMID {record.pmid}",
                url=record.url,
                published_date=record.published_date,
                pmcid=record.pmcid or None,
                has_pmc_pdf=False,
                pdf_urls=record.pdf_urls,
                pmc_url=pmc_url,
                already_loaded=is_connector_record_loaded(
                    repository,
                    source="PubMed",
                    record_id=record.pmid,
                    published_date=record.published_date,
                    loaded_index=loaded_index,
                ),
            )
        )
    return previews


def search_external_evidence(
    *,
    mesh_terms: str | list[str],
    repository: dict[str, Any],
    config: MedicalLiteracyConfig | None = None,
    pubmed_max_results: int | None = None,
    clinicaltrials_max_results: int | None = None,
    connectors: list[str] | None = None,
    pubmed_open_access_only: bool = False,
    pubmed_mesh_mode: bool = False,
    repository_documents: list[dict[str, Any]] | None = None,
    exclude_existing: bool = True,
    cancel_event: Any | None = None,
) -> tuple[list[EvidencePreview], list[str]]:
    """Search enabled connectors and return selectable previews (no upload)."""
    cfg, pubmed_client, trials_client = _build_clients(repository, config)
    if cancel_event is not None:
        pubmed_client._cancel_event = cancel_event
        setattr(trials_client, "_cancel_event", cancel_event)
    terms = mesh_terms if isinstance(mesh_terms, list) else normalize_mesh_terms(mesh_terms)
    if not terms:
        raise ValueError("Provide at least one MeSH term.")

    active_connectors = resolve_search_connectors(repository, selected_connectors=connectors)
    if not active_connectors:
        raise ValueError("Enable at least one connector on this repository.")

    loaded_index = build_loaded_record_index(repository, repository_documents)
    errors: list[str] = []
    previews: list[EvidencePreview] = []
    pubmed_limit = pubmed_max_results or cfg.pubmed_max_results
    trials_limit = clinicaltrials_max_results or cfg.clinicaltrials_max_results

    def _cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    if CONNECTOR_PUBMED in active_connectors:
        if _cancelled():
            raise RuntimeError("Literature search cancelled.")
        try:
            previews.extend(
                _pubmed_previews_from_ncbi(
                    pubmed_client,
                    terms=terms,
                    pubmed_limit=pubmed_limit,
                    open_access_only=pubmed_open_access_only,
                    mesh_mode=pubmed_mesh_mode,
                    repository=repository,
                    loaded_index=loaded_index,
                )
            )
        except RuntimeError:
            raise
        except Exception as exc:
            _logger.exception("PubMed search failed via NCBI E-utilities")
            errors.append(f"PubMed: {exc}")

    if CONNECTOR_CLINICALTRIALS in active_connectors:
        if _cancelled():
            raise RuntimeError("Literature search cancelled.")
        try:
            exclude_ncts: set[str] = set()
            if exclude_existing:
                exclude_ncts.update(loaded_index.clinicaltrials_ncts)
                for key in loaded_index.registry_keys:
                    parts = str(key).split(":", 2)
                    if len(parts) >= 2 and parts[0] == "clinicaltrials.gov" and parts[1]:
                        exclude_ncts.add(parts[1].upper())
            for record in trials_client.search(
                terms,
                page_size=trials_limit,
                exclude_nct_ids=exclude_ncts if exclude_existing else None,
            ):
                if _cancelled():
                    raise RuntimeError("Literature search cancelled.")
                previews.append(
                    _trial_preview(
                        record,
                        repository=repository,
                        loaded_index=loaded_index,
                    )
                )
        except RuntimeError:
            raise
        except Exception as exc:
            _logger.exception("ClinicalTrials.gov search failed")
            errors.append(f"ClinicalTrials.gov: {exc}")

    if CONNECTOR_SHAREPOINT in active_connectors:
        errors.append("SharePoint connector search is not implemented yet.")

    if CONNECTOR_S3 in active_connectors:
        errors.append("Amazon S3 connector search is not implemented yet.")

    # Safety net — fill loops already skip loaded records so they don't shrink the count.
    if exclude_existing:
        previews, _excluded_count = exclude_loaded_connector_previews(
            previews,
            repository=repository,
            loaded_index=loaded_index,
        )

    return previews, errors


def _trial_preview(
    record: ClinicalTrialRecord,
    *,
    repository: dict[str, Any],
    published_date: str | None = None,
    loaded_index: LoadedRecordIndex | None = None,
) -> EvidencePreview:
    conditions = ", ".join(record.conditions[:3]) if record.conditions else ""
    phases = ", ".join(record.phases) if record.phases else ""
    subtitle = " | ".join(part for part in (record.status, phases, conditions) if part)
    summary = (record.summary or "")[:280]
    pub_date = published_date or record.published_date or ""
    return EvidencePreview(
        source="ClinicalTrials.gov",
        record_id=record.nct_id,
        title=record.title,
        subtitle=subtitle,
        summary=summary,
        url=record.url,
        published_date=pub_date,
        already_loaded=is_connector_record_loaded(
            repository,
            source="ClinicalTrials.gov",
            record_id=record.nct_id,
            published_date=pub_date,
            loaded_index=loaded_index,
        ),
    )


def _batch_upload(
    file_payloads: list[tuple[str, bytes]],
    upload_fn: UploadFn,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], list[str]]:
    uploaded: list[dict[str, Any]] = []
    errors: list[str] = []
    for start in range(0, len(file_payloads), UPLOAD_BATCH_SIZE):
        batch = file_payloads[start : start + UPLOAD_BATCH_SIZE]
        try:
            upload_result = upload_fn(batch, **kwargs)
            docs = upload_result.get("documents") or []
            if isinstance(docs, list):
                uploaded.extend(docs)
        except Exception as exc:
            _logger.exception("Document upload batch failed")
            errors.append(f"Upload batch {start // UPLOAD_BATCH_SIZE + 1}: {exc}")
    return uploaded, errors


def _build_pubmed_payloads(
    pubmed_client: PubMedClient,
    pmids: list[str],
    *,
    preview_by_pmid: dict[str, EvidencePreview],
) -> tuple[list[tuple[str, bytes]], list[str], list[tuple[str, str, str]]]:
    """Return PDF file payloads, warnings, and (source, record_id, published_date) tuples."""
    file_payloads: list[tuple[str, bytes]] = []
    warnings: list[str] = []
    record_meta: list[tuple[str, str, str]] = []

    if not pmids:
        return file_payloads, warnings, record_meta

    pmc_map = resolve_pmcids(
        pmids,
        session=pubmed_client.session,
        email=pubmed_client.email,
        tool=pubmed_client.tool,
    )
    pdf_availability = batch_resolve_pmc_pdf_availability(
        {pmid: (pmc_map.get(pmid).pmcid if pmc_map.get(pmid) else None) for pmid in pmids},
        session=pubmed_client.session,
        email=pubmed_client.email,
        tool=pubmed_client.tool,
    )

    for pmid in pmids:
        preview = preview_by_pmid.get(pmid)
        title = preview.title if preview else f"PubMed {pmid}"
        published_date = preview.published_date if preview and preview.published_date else ""
        pmc = pmc_map.get(pmid)
        pdf_info = pdf_availability.get(pmid)
        pmcid = pmc.pmcid if pmc else (preview.pmcid if preview else None)
        if not pmcid and pdf_info:
            pmcid = pdf_info.pmcid
        pdf_urls = list(preview.pdf_urls) if preview and preview.pdf_urls else []
        if pdf_info:
            pdf_urls.extend(list(pdf_info.pdf_urls))
        pdf_urls = list(dict.fromkeys(url for url in pdf_urls if url))

        if not pmcid and not pdf_urls:
            warnings.append(
                f"PubMed {pmid}: no Free PMC article found. "
                "Only articles with a PMC full-text PDF can be imported."
            )
            continue

        pdf_result = fetch_pmc_document(
            pmid=pmid,
            pmcid=pmcid,
            title=title,
            pdf_urls=pdf_urls,
            session=pubmed_client.session,
            email=pubmed_client.email,
            tool=pubmed_client.tool,
        )
        if pdf_result:
            file_payloads.append((pdf_result.filename, pdf_result.content))
            record_meta.append(("PubMed", pmid, published_date))
            continue

        warnings.append(
            f"PubMed {pmid}: open-access PMC PDF download failed."
        )

    return file_payloads, warnings, record_meta


def ingest_selected_evidence(
    *,
    repository: dict[str, Any],
    upload_fn: UploadFn,
    pubmed_pmids: list[str] | None = None,
    clinicaltrials_nct_ids: list[str] | None = None,
    preview_records: list[EvidencePreview] | None = None,
    config: MedicalLiteracyConfig | None = None,
    repository_documents: list[dict[str, Any]] | None = None,
) -> IngestionResult:
    """Fetch full text for selected PMIDs / NCT IDs and upload to the repository."""
    _, pubmed_client, trials_client = _build_clients(repository, config)
    loaded_index = build_loaded_record_index(repository, repository_documents)
    settings = dict(repository.get("settings") or {})
    repo_id = str(repository.get("repository_id") or "").strip()
    collection = str(repository.get("weaviate_collection") or "").strip()
    tenant_id = str(repository.get("default_tenant_id") or "").strip()
    classification_id = str(settings.get("default_classification_category_id") or "public")

    preview_by_pmid = {
        preview.record_id: preview
        for preview in (preview_records or [])
        if preview.source == "PubMed"
    }
    preview_by_nct = {
        preview.record_id: preview
        for preview in (preview_records or [])
        if preview.source == "ClinicalTrials.gov"
    }

    pmids = [str(item).strip() for item in (pubmed_pmids or []) if str(item).strip()]
    nct_ids = [str(item).strip() for item in (clinicaltrials_nct_ids or []) if str(item).strip()]
    if not pmids and not nct_ids:
        raise ValueError("Select at least one PubMed or ClinicalTrials.gov record.")

    errors: list[str] = []
    skipped = 0
    file_payloads: list[tuple[str, bytes]] = []
    record_meta: list[tuple[str, str, str]] = []

    filtered_pmids: list[str] = []
    for pmid in pmids:
        preview = preview_by_pmid.get(pmid)
        published_date = preview.published_date if preview else ""
        if preview and preview.already_loaded:
            skipped += 1
            continue
        if is_connector_record_loaded(
            repository,
            source="PubMed",
            record_id=pmid,
            published_date=published_date,
            loaded_index=loaded_index,
        ):
            skipped += 1
            continue
        filtered_pmids.append(pmid)

    filtered_nct_ids: list[str] = []
    for nct_id in nct_ids:
        preview = preview_by_nct.get(nct_id)
        published_date = preview.published_date if preview else ""
        if preview and preview.already_loaded:
            skipped += 1
            continue
        if is_connector_record_loaded(
            repository,
            source="ClinicalTrials.gov",
            record_id=nct_id,
            published_date=published_date,
            loaded_index=loaded_index,
        ):
            skipped += 1
            continue
        filtered_nct_ids.append(nct_id)

    pubmed_payloads, pubmed_warnings, pubmed_meta = _build_pubmed_payloads(
        pubmed_client,
        filtered_pmids,
        preview_by_pmid=preview_by_pmid,
    )
    file_payloads.extend(pubmed_payloads)
    record_meta.extend(pubmed_meta)
    errors.extend(pubmed_warnings)

    trial_records: list[ClinicalTrialRecord] = []
    if filtered_nct_ids:
        for nct_id in filtered_nct_ids:
            try:
                study_json = trials_client.fetch_study(nct_id)
                parsed = trials_client.parse_study(study_json)
                if not parsed:
                    errors.append(f"ClinicalTrials.gov {nct_id}: study JSON is missing an NCT ID.")
                    continue
                trial_records.append(parsed)
                preview = preview_by_nct.get(nct_id)
                published_date = preview.published_date if preview else (parsed.published_date or "")
                record_meta.append(("ClinicalTrials.gov", nct_id, published_date))
                filename, docx_bytes = trials_client.build_study_docx(study_json)
                file_payloads.append((filename, docx_bytes))
            except Exception as exc:
                _logger.exception("ClinicalTrials.gov fetch failed for %s", nct_id)
                errors.append(f"ClinicalTrials.gov {nct_id}: {exc}")

    if not file_payloads:
        return IngestionResult(
            pubmed_count=len(filtered_pmids),
            clinicaltrials_count=len(trial_records),
            uploaded_documents=[],
            errors=errors or ["No new document content could be built for the selected records."],
            skipped_count=skipped,
        )

    uploaded, upload_errors = _batch_upload(
        file_payloads,
        upload_fn,
        collection_name=collection,
        tenant_id=tenant_id,
        repository_id=repo_id or None,
        classification_category_id=classification_id or None,
    )
    errors.extend(upload_errors)

    registry_entries: list[dict[str, Any]] = []
    for index, (source, record_id, published_date) in enumerate(record_meta):
        document_id = None
        if index < len(uploaded):
            document_id = str(uploaded[index].get("document_id") or "").strip() or None
        registry_entries.append(
            new_registry_entry(
                source=source,
                record_id=record_id,
                published_date=published_date,
                document_id=document_id,
            )
        )

    return IngestionResult(
        pubmed_count=len(filtered_pmids),
        clinicaltrials_count=len(trial_records),
        uploaded_documents=uploaded,
        errors=errors,
        skipped_count=skipped,
        registry_entries=registry_entries,
    )


def ingest_mesh_query(
    *,
    mesh_terms: str | list[str],
    repository: dict[str, Any],
    upload_fn: UploadFn,
    config: MedicalLiteracyConfig | None = None,
    pubmed_max_results: int | None = None,
    clinicaltrials_max_results: int | None = None,
    connectors: list[str] | None = None,
    pubmed_open_access_only: bool = False,
    pubmed_mesh_mode: bool = False,
    repository_documents: list[dict[str, Any]] | None = None,
) -> IngestionResult:
    """Fetch all connector matches and upload them (bulk mode)."""
    previews, errors = search_external_evidence(
        mesh_terms=mesh_terms,
        repository=repository,
        config=config,
        pubmed_max_results=pubmed_max_results,
        clinicaltrials_max_results=clinicaltrials_max_results,
        connectors=connectors,
        pubmed_open_access_only=pubmed_open_access_only,
        pubmed_mesh_mode=pubmed_mesh_mode,
        repository_documents=repository_documents,
    )
    new_previews = [preview for preview in previews if not preview.already_loaded]
    if not new_previews:
        return IngestionResult(
            pubmed_count=0,
            clinicaltrials_count=0,
            uploaded_documents=[],
            errors=errors or ["No new documents returned from external sources."],
            skipped_count=len(previews),
        )

    pmids = [p.record_id for p in new_previews if p.source == "PubMed"]
    nct_ids = [p.record_id for p in new_previews if p.source == "ClinicalTrials.gov"]
    result = ingest_selected_evidence(
        repository=repository,
        upload_fn=upload_fn,
        pubmed_pmids=pmids,
        clinicaltrials_nct_ids=nct_ids,
        preview_records=new_previews,
        config=config,
        repository_documents=repository_documents,
    )
    result.errors = errors + result.errors
    result.skipped_count += len(previews) - len(new_previews)
    return result
