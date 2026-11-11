"""Load and validate files attached to medical-information emails."""
from __future__ import annotations

import logging
import re
from typing import Optional, Protocol

from digital_msl.summary import document_title

logger = logging.getLogger(__name__)

_PDF_MAGIC = b"%PDF-"
_DOCX_MAGIC = b"PK\x03\x04"


class DocumentBytesClient(Protocol):
    def download_document_with_type(self, document_id: str) -> tuple[bytes, str]: ...

    def render_document_with_type(self, document_id: str) -> tuple[bytes, str]: ...


def _clean_content_type(media: str) -> str:
    return str(media or "application/octet-stream").split(";")[0].strip().lower()


def is_pdf(data: bytes) -> bool:
    return len(data) > 128 and data.startswith(_PDF_MAGIC)


def is_docx(data: bytes) -> bool:
    return len(data) > 128 and data.startswith(_DOCX_MAGIC)


def looks_like_error_payload(data: bytes) -> bool:
    head = data[:240].lstrip()
    if not head:
        return True
    if head.startswith(b"{") or head.startswith(b"["):
        lower = head.lower()
        return b"error" in lower or b'"detail"' in lower or b'"message"' in lower
    if head.startswith(b"<") and b"<html" in head[:80].lower():
        return True
    return False


def safe_attachment_filename(display_name: str, media_type: str) -> str:
    title = document_title(display_name or "evidence")
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title)
    title = re.sub(r"\s+", " ", title).strip(" .")[:100] or "evidence"
    if media_type == "application/pdf":
        return title if title.lower().endswith(".pdf") else f"{title}.pdf"
    if "wordprocessingml" in media_type:
        return title if title.lower().endswith(".docx") else f"{title}.docx"
    return title


def classify_bytes(data: bytes, declared_type: str) -> Optional[str]:
    if looks_like_error_payload(data):
        return None
    if is_pdf(data):
        return "application/pdf"
    if is_docx(data):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    cleaned = _clean_content_type(declared_type)
    if cleaned == "application/pdf" and is_pdf(data):
        return cleaned
    if cleaned in {"application/pdf", "application/octet-stream"} and is_pdf(data):
        return "application/pdf"
    return None


def load_citation_attachment(
    client: DocumentBytesClient,
    document_id: str,
    display_name: str,
) -> Optional[tuple[str, bytes, str]]:
    """Return (filename, bytes, media_type) only when the payload is a real openable document."""
    attempts: list[tuple[str, tuple[bytes, str]]] = []
    for label, loader in (
        ("download", client.download_document_with_type),
        ("render", client.render_document_with_type),
    ):
        try:
            attempts.append((label, loader(document_id)))
        except Exception as exc:
            logger.info("Attachment %s via %s failed for %s: %s", display_name, label, document_id, exc)
    for label, (data, media) in attempts:
        media_type = classify_bytes(data, media)
        if not media_type:
            logger.warning(
                "Skipping attachment for %s (%s): payload is not a valid PDF/DOCX (via %s, %s bytes).",
                document_id,
                display_name,
                label,
                len(data),
            )
            continue
        filename = safe_attachment_filename(display_name, media_type)
        logger.info("Prepared email attachment %s (%s, %s bytes, via %s).", filename, media_type, len(data), label)
        return filename, data, media_type
    return None


def cited_documents(response) -> dict[str, str]:
    """document_id -> display name for every document cited by the answered claims."""
    wanted: dict[str, str] = {}
    if response.decision != "respond":
        return wanted
    for claim in response.claims:
        for evidence in claim.evidence:
            document_id = (evidence.document_id or "").strip()
            if document_id and document_id not in wanted:
                wanted[document_id] = evidence.document_name or f"{document_id}.pdf"
    return wanted


def evidence_attachments(response, *, require_smtp: bool = True, limit: int = 0) -> list[tuple[str, bytes, str]]:
    """Original cited documents; ``require_smtp=False`` for channels other than email."""
    from digital_msl.config import get_dmsl_settings

    if require_smtp and not get_dmsl_settings().smtp_host:
        return []
    wanted = cited_documents(response)
    if limit > 0:
        wanted = dict(list(wanted.items())[:limit])
    if not wanted:
        return []
    try:
        from services.dms_client import DmsClient

        client = DmsClient()
    except Exception as exc:
        logger.warning("Evidence attachment client unavailable: %s", exc)
        return []
    files: list[tuple[str, bytes, str]] = []
    for document_id, name in wanted.items():
        item = load_citation_attachment(client, document_id, name)
        if item:
            files.append(item)
    return files
