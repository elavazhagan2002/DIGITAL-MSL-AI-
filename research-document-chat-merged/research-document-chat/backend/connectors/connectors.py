"""Transient connector resolution for medical literature repositories."""

from __future__ import annotations

from typing import Any

CONNECTOR_PUBMED = "pubmed"
CONNECTOR_CLINICALTRIALS = "clinicaltrials"
CONNECTOR_SHAREPOINT = "sharepoint"
CONNECTOR_S3 = "s3"

ALL_CONNECTORS: tuple[str, ...] = (
    CONNECTOR_PUBMED,
    CONNECTOR_CLINICALTRIALS,
    CONNECTOR_SHAREPOINT,
    CONNECTOR_S3,
)

CONNECTOR_LABELS: dict[str, str] = {
    CONNECTOR_PUBMED: "PubMed",
    CONNECTOR_CLINICALTRIALS: "ClinicalTrials.gov",
    CONNECTOR_SHAREPOINT: "SharePoint",
    CONNECTOR_S3: "Amazon S3",
}

SOURCE_BY_CONNECTOR: dict[str, str] = {
    CONNECTOR_PUBMED: "PubMed",
    CONNECTOR_CLINICALTRIALS: "ClinicalTrials.gov",
    CONNECTOR_SHAREPOINT: "SharePoint",
    CONNECTOR_S3: "Amazon S3",
}


def _settings_dict(repository: dict[str, Any] | None) -> dict[str, Any]:
    if not repository:
        return {}
    settings = repository.get("settings") or {}
    return dict(settings) if isinstance(settings, dict) else {}


def transient_connectors_settings(repository: dict[str, Any] | None) -> dict[str, Any]:
    settings = _settings_dict(repository)
    raw = settings.get("transient_connectors") or {}
    return dict(raw) if isinstance(raw, dict) else {}


def repository_transient_connectors_enabled(repository: dict[str, Any] | None) -> bool:
    connectors = transient_connectors_settings(repository)
    if bool(connectors.get("enabled")):
        return True
    settings = _settings_dict(repository)
    return bool(settings.get("medical_literacy_research"))


def configured_connectors(repository: dict[str, Any] | None) -> list[str]:
    """Connectors enabled on the repository (repo settings)."""
    connectors = transient_connectors_settings(repository)
    selected = connectors.get("connectors") or []
    if isinstance(selected, list) and selected:
        return [str(item).strip().lower() for item in selected if str(item).strip()]
    settings = _settings_dict(repository)
    if settings.get("medical_literacy_research"):
        return [CONNECTOR_PUBMED, CONNECTOR_CLINICALTRIALS]
    return []


def resolve_search_connectors(
    repository: dict[str, Any] | None,
    *,
    selected_connectors: list[str] | None = None,
) -> list[str]:
    """Intersect user-selected connectors with those configured on the repository."""
    allowed = set(configured_connectors(repository))
    if not allowed:
        return []
    if not selected_connectors:
        return sorted(allowed)
    picked = {str(item).strip().lower() for item in selected_connectors if str(item).strip()}
    return [connector for connector in ALL_CONNECTORS if connector in allowed and connector in picked]


def connector_label(connector_id: str) -> str:
    return CONNECTOR_LABELS.get(connector_id, connector_id)
