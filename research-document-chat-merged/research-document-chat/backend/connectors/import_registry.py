"""Track connector-imported records to avoid duplicate fetches and uploads."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


_PUBMED_FILENAME_RE = re.compile(r"pubmed_(\d+)", re.I)
_CLINICALTRIALS_FILENAME_RE = re.compile(r"clinicaltrials_(nct\d+)", re.I)
_NCT_FILENAME_RE = re.compile(r"\b(nct\d+)\b", re.I)

@dataclass(frozen=True)
class ImportedRecord:
    source: str
    record_id: str
    published_date: str
    document_id: str | None = None
    imported_at: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "source": self.source,
            "record_id": self.record_id,
            "published_date": self.published_date,
            "document_id": self.document_id,
            "imported_at": self.imported_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ImportedRecord | None:
        source = str(raw.get("source") or "").strip()
        record_id = str(raw.get("record_id") or "").strip()
        if not source or not record_id:
            return None
        return cls(
            source=source,
            record_id=record_id,
            published_date=str(raw.get("published_date") or "").strip(),
            document_id=str(raw.get("document_id") or "").strip() or None,
            imported_at=str(raw.get("imported_at") or "").strip() or None,
        )


def registry_key(*, source: str, record_id: str, published_date: str) -> str:
    return f"{source.strip().lower()}:{record_id.strip()}:{published_date.strip()}"


def load_registry(repository: dict[str, Any] | None) -> list[ImportedRecord]:
    if not repository:
        return []
    settings = repository.get("settings") or {}
    raw_entries = settings.get("connector_import_registry") or []
    if not isinstance(raw_entries, list):
        return []
    records: list[ImportedRecord] = []
    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        parsed = ImportedRecord.from_dict(item)
        if parsed:
            records.append(parsed)
    return records


def infer_connector_source_from_filename(filename: str) -> str:
    """Return PubMed or ClinicalTrials.gov when the filename matches connector imports."""
    name = str(filename or "")
    if _PUBMED_FILENAME_RE.search(name):
        return "PubMed"
    if _CLINICALTRIALS_FILENAME_RE.search(name) or _NCT_FILENAME_RE.search(name):
        return "ClinicalTrials.gov"
    return ""


def connector_source_for_document(
    document: dict[str, Any],
    repository: dict[str, Any] | None,
) -> str:
    doc_id = str(document.get("document_id") or "").strip()
    if doc_id:
        for entry in load_registry(repository):
            if entry.document_id == doc_id and entry.source:
                return entry.source
    return infer_connector_source_from_filename(_document_display_name(document))


def load_registry_keys(repository: dict[str, Any] | None) -> set[str]:
    return {
        registry_key(
            source=item.source,
            record_id=item.record_id,
            published_date=item.published_date,
        )
        for item in load_registry(repository)
    }


def is_already_imported(
    repository: dict[str, Any] | None,
    *,
    source: str,
    record_id: str,
    published_date: str,
) -> bool:
    key = registry_key(source=source, record_id=record_id, published_date=published_date)
    return key in load_registry_keys(repository)


def _document_display_name(document: dict[str, Any]) -> str:
    for key in ("original_file_name", "document_name", "doc_name", "filename", "name"):
        value = str(document.get(key) or "").strip()
        if value:
            return value
    metadata = document.get("metadata") if isinstance(document.get("metadata"), dict) else {}
    for key in ("original_file_name", "document_name", "doc_name", "filename", "name"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    return ""


@dataclass(frozen=True)
class LoadedRecordIndex:
    """Connector record IDs already present via import registry and/or repository files."""

    pubmed_pmids: frozenset[str]
    clinicaltrials_ncts: frozenset[str]
    registry_keys: frozenset[str]

    @classmethod
    def empty(cls) -> LoadedRecordIndex:
        return cls(frozenset(), frozenset(), frozenset())


def build_loaded_record_index(
    repository: dict[str, Any] | None,
    repository_documents: list[dict[str, Any]] | None = None,
) -> LoadedRecordIndex:
    pubmed_pmids: set[str] = set()
    clinicaltrials_ncts: set[str] = set()
    for document in repository_documents or []:
        if not isinstance(document, dict):
            continue
        name = _document_display_name(document)
        if not name:
            continue
        pubmed_match = _PUBMED_FILENAME_RE.search(name)
        if pubmed_match:
            pubmed_pmids.add(pubmed_match.group(1))
        trial_match = _CLINICALTRIALS_FILENAME_RE.search(name)
        if trial_match:
            clinicaltrials_ncts.add(trial_match.group(1).upper())
        else:
            nct_match = _NCT_FILENAME_RE.search(name)
            if nct_match:
                clinicaltrials_ncts.add(nct_match.group(1).upper())

    return LoadedRecordIndex(
        pubmed_pmids=frozenset(pubmed_pmids),
        clinicaltrials_ncts=frozenset(clinicaltrials_ncts),
        registry_keys=frozenset(load_registry_keys(repository)),
    )


def is_connector_record_loaded(
    repository: dict[str, Any] | None,
    *,
    source: str,
    record_id: str,
    published_date: str,
    loaded_index: LoadedRecordIndex | None = None,
    repository_documents: list[dict[str, Any]] | None = None,
) -> bool:
    """True when a connector record is already tracked or present in repository documents."""
    normalized_source = str(source or "").strip()
    normalized_record_id = str(record_id or "").strip()
    if not normalized_source or not normalized_record_id:
        return False

    index = loaded_index or build_loaded_record_index(repository, repository_documents)
    if normalized_source == "PubMed" and normalized_record_id in index.pubmed_pmids:
        return True
    if normalized_source == "ClinicalTrials.gov":
        if normalized_record_id.upper() in index.clinicaltrials_ncts:
            return True

    registry_key_value = registry_key(
        source=normalized_source,
        record_id=normalized_record_id,
        published_date=str(published_date or "").strip(),
    )
    if registry_key_value in index.registry_keys:
        return True

    # Registry entries may exist without published_date variants; also match by record_id only.
    for key in index.registry_keys:
        parts = key.split(":", 2)
        if len(parts) >= 2 and parts[0] == normalized_source.lower() and parts[1] == normalized_record_id:
            return True
    return False


def exclude_loaded_connector_previews(
    previews: list[Any],
    *,
    repository: dict[str, Any] | None,
    loaded_index: LoadedRecordIndex | None = None,
    repository_documents: list[dict[str, Any]] | None = None,
) -> tuple[list[Any], int]:
    """Drop previews whose connector record already exists in the repository."""
    index = loaded_index or build_loaded_record_index(repository, repository_documents)
    kept: list[Any] = []
    excluded = 0
    for preview in previews:
        if is_connector_record_loaded(
            repository,
            source=str(getattr(preview, "source", "") or ""),
            record_id=str(getattr(preview, "record_id", "") or ""),
            published_date=str(getattr(preview, "published_date", "") or ""),
            loaded_index=index,
        ):
            excluded += 1
            continue
        kept.append(preview)
    return kept, excluded


def merge_registry_entries(
    existing: list[dict[str, Any]],
    new_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in existing + new_entries:
        if not isinstance(item, dict):
            continue
        parsed = ImportedRecord.from_dict(item)
        if not parsed:
            continue
        key = registry_key(
            source=parsed.source,
            record_id=parsed.record_id,
            published_date=parsed.published_date,
        )
        merged[key] = parsed.to_dict()
    return list(merged.values())


def build_registry_patch(
    repository: dict[str, Any] | None,
    new_entries: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    settings = dict((repository or {}).get("settings") or {})
    existing = settings.get("connector_import_registry") or []
    if not isinstance(existing, list):
        existing = []
    return {"connector_import_registry": merge_registry_entries(existing, new_entries)}


def new_registry_entry(
    *,
    source: str,
    record_id: str,
    published_date: str,
    document_id: str | None,
) -> dict[str, str | None]:
    return ImportedRecord(
        source=source,
        record_id=record_id,
        published_date=published_date,
        document_id=document_id,
        imported_at=datetime.now(timezone.utc).isoformat(),
    ).to_dict()
