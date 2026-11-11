"""Citation pill helpers for compliance / validation reports (matches Chat UI format)."""
from __future__ import annotations

import os
import re
from typing import Any, Optional

from services.retrieval_service import format_citation_label

_AUTO_UPLOAD_NAME_RE = re.compile(r"^sop_[a-f0-9]{6,12}(\.[a-z0-9]+)?$", re.I)
_UUID_PREFIX_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_(.+)$",
    re.I,
)
# LLM prompt examples often emit these instead of the real upload filename.
_PLACEHOLDER_UPLOAD_NAMES = frozenset(
    {
        "user_sop.docx",
        "user_sop.doc",
        "usersop.docx",
        "uploaded_document.docx",
        "uploaded_document.doc",
        "uploaded_filename.docx",
        "uploaded_filename.doc",
        "upload.docx",
        "your_sop.docx",
        "sop.docx",
        "document.docx",
    }
)
_PLAIN_BRACKET_CITATION_RE = re.compile(
    r'\[([^\]]+\.(?:docx?|pdf|txt|md))\]\s*[—\-]\s*"([^"]*)"',
    re.I,
)


def _normalize_quote(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _escape_pill_quote(text: str, *, limit: int = 120) -> str:
    return (text or "see document").replace("|", " / ").replace("\n", " ").replace('"', "'")[:limit].strip()


_HASH_LIKE_STEM_RE = re.compile(r"^(sop|doc|upload|file)_[a-f0-9]{6,}$", re.I)
_GUIDELINE_NAME_RE = re.compile(
    r"(?:"
    r"pi\s*0*0*6|recommendation|validation_master_plan|master_plan|"
    r"\bgmp\b|annex|who\s|apic|ich\b|pic/?s|eu\s*gmp|"
    r"health\s*canada|usfda|fda\b|geneva|risk[- ]based"
    r")",
    re.I,
)


def _name_from_saved_path(path: str) -> str:
    base = os.path.basename(path or "")
    if not base:
        return ""
    match = _UUID_PREFIX_RE.match(base)
    if match:
        return match.group(1).strip()
    return base.strip()


def _looks_like_guideline_document(name: str) -> bool:
    """Heuristic: Knowledge Scope guideline files vs user uploads."""
    stem = re.sub(r"\.[a-z0-9]+$", "", (name or "").strip(), flags=re.I)
    if not stem:
        return False
    if _GUIDELINE_NAME_RE.search(stem):
        return True
    lower = name.lower()
    # Long underscore-heavy PDFs without spaces are usually repository guidelines.
    if lower.endswith(".pdf") and "_" in stem and " " not in stem and len(stem) > 28:
        return True
    return False


def _score_upload_candidate(name: str) -> tuple[int, int, str]:
    stem = re.sub(r"\.[a-z0-9]+$", "", name, flags=re.I)
    score = 0
    if _looks_like_guideline_document(name):
        score -= 8
    if _AUTO_UPLOAD_NAME_RE.match(name) or _HASH_LIKE_STEM_RE.match(stem):
        score -= 4
    if re.match(r"^[0-9a-f-]{36}_", name, re.I):
        score -= 5
    if name.lower().endswith(".docx"):
        score += 4
    elif name.lower().endswith((".doc", ".txt")):
        score += 2
    elif name.lower().endswith(".pdf"):
        score -= 1
    if " " in name or "-" in name:
        score += 2
    if re.search(r"\b(sample|sop|urs|protocol)\b", name, re.I):
        score += 2
    return (score, -len(name), name)


def resolve_upload_display_name(
    uploaded_files: Optional[list[dict[str, Any]]] = None,
) -> tuple[str, str]:
    """Return (display_filename, file_path) from uploaded file metadata."""
    candidates: list[str] = []
    path = ""
    stored_path = ""

    for file_info in uploaded_files or []:
        for key in ("display_filename", "original_filename", "filename"):
            val = str(file_info.get(key) or "").strip()
            if val:
                candidates.append(val)
        candidate_path = str(file_info.get("path") or "").strip()
        if candidate_path and not stored_path:
            stored_path = candidate_path
        if candidate_path and os.path.exists(candidate_path) and not path:
            path = candidate_path
        recovered = _name_from_saved_path(candidate_path)
        if recovered:
            candidates.append(recovered)

    recovery_path = path or stored_path
    if recovery_path:
        recovered = _name_from_saved_path(recovery_path)
        if recovered:
            candidates.append(recovered)

    # De-duplicate while preserving order; pick best human-readable upload name.
    seen: set[str] = set()
    unique_candidates: list[str] = []
    for candidate in candidates:
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        unique_candidates.append(candidate)

    upload_candidates = [
        c for c in unique_candidates if not _looks_like_guideline_document(c)
    ] or unique_candidates
    name = (
        max(upload_candidates, key=_score_upload_candidate)
        if upload_candidates
        else ""
    )

    if path and os.path.exists(path) and (_AUTO_UPLOAD_NAME_RE.match(name) or not name):
        title_name = _docx_display_title(path)
        if title_name and not _looks_like_guideline_document(title_name):
            name = title_name

    if name and not re.search(r"\.[a-z0-9]{2,5}$", name, re.I) and path:
        ext = os.path.splitext(path)[1]
        if ext:
            name = f"{name}{ext}"

    return (name or "Uploaded_Document.docx"), path


def _docx_display_title(file_path: str) -> str:
    """Use Word core title when upload filename is an auto-generated placeholder."""
    try:
        from docx import Document

        doc = Document(file_path)
        title = str(doc.core_properties.title or "").strip()
        if len(title) < 3:
            return ""
        if not re.search(r"\.[a-z0-9]{2,5}$", title, re.I):
            ext = os.path.splitext(file_path)[1] or ".docx"
            title = f"{title}{ext}"
        return title
    except Exception:
        return ""


def resolve_repository_display_name(session_context: Optional[dict[str, Any]]) -> str:
    """Knowledge Scope / repository label for citation pills."""
    try:
        from utils.repository_search import get_repository_search_targets

        targets = get_repository_search_targets(session_context)
        if targets:
            label = str(targets[0].get("repository_name") or "").strip()
            if label:
                return label
    except Exception:
        pass
    repo_id = str((session_context or {}).get("repository_id") or "").strip()
    return repo_id or "Upload"


class DocumentPageIndex:
    """Map document quotes to page numbers for citation pills."""

    def __init__(self, file_path: str = "", fallback_text: str = ""):
        self._units: list[Any] = []
        self._cache: dict[str, Optional[int]] = {}
        if not file_path or not os.path.exists(file_path):
            return
        try:
            from services.document_validation.language_quality_validator import (
                extract_language_units,
            )

            self._units = extract_language_units(file_path, fallback_text=fallback_text)
        except Exception:
            self._units = []

    def page_for_quote(self, quote: str) -> Optional[int]:
        key = _normalize_quote(quote)[:220]
        if not key:
            return None
        if key in self._cache:
            return self._cache[key]

        page = self._lookup_page(key)
        self._cache[key] = page
        return page

    def _lookup_page(self, needle: str) -> Optional[int]:
        if len(needle) < 8 or not self._units:
            return None

        best_page: Optional[int] = None
        best_score = 0
        for unit in self._units:
            hay = _normalize_quote(getattr(unit, "text", "") or "")
            if not hay:
                continue
            if needle in hay or hay in needle:
                score = min(len(needle), len(hay))
                if score > best_score:
                    best_score = score
                    best_page = getattr(unit, "page", None)

        if best_page is not None:
            return best_page

        short = needle[:40]
        if len(short) < 12:
            return None
        for unit in self._units:
            hay = _normalize_quote(getattr(unit, "text", "") or "")
            if short in hay:
                return getattr(unit, "page", None)
        return None


def build_citation_pill_html(
    quote: str,
    *,
    document_name: str,
    repository_name: str = "",
    page_number: Optional[int] = None,
    page_index: Optional[DocumentPageIndex] = None,
    quote_limit: int = 120,
) -> str:
    """Build Chat-compatible citation pill: [Repo:Doc.docx:5] — \"quote\"."""
    doc = (document_name or "Uploaded_Document.docx").strip()
    repo = (repository_name or "Upload").strip()
    page = page_number
    if page is None and page_index is not None:
        page = page_index.page_for_quote(quote)
    label = format_citation_label(repo, doc, page)
    q = _escape_pill_quote(quote, limit=quote_limit)
    return (
        f'<span class="citation"><span class="citation-pill">'
        f'[{label}] — "{q}"</span></span>'
    )


_CITATION_PILL_RE = re.compile(
    r'<span\s+class="citation">\s*<span\s+class="citation-pill">\s*'
    r'\[([^\]]+)\]\s*[—\-]\s*"([^"]*)"\s*</span>\s*</span>',
    re.I,
)


def _citation_label_needs_upgrade(label: str) -> bool:
    """True when pill is legacy `[filename]` instead of `[Repo:Doc:Page]`."""
    inner = (label or "").strip()
    if not inner:
        return True
    parts = inner.split(":")
    if len(parts) < 3:
        return True
    page_token = parts[-1].strip()
    return not (page_token.isdigit() or page_token in {"—", "-"})


def _is_placeholder_upload_name(name: str) -> bool:
    """True for prompt-template filenames like User_SOP.docx."""
    n = (name or "").strip().casefold()
    if not n:
        return False
    if n in _PLACEHOLDER_UPLOAD_NAMES:
        return True
    # Strip repo/page wrappers: AMNEAL:User_SOP.docx:3 → user_sop.docx
    if ":" in n:
        parts = [p.strip() for p in n.split(":") if p.strip()]
        for part in parts:
            if part in _PLACEHOLDER_UPLOAD_NAMES:
                return True
            if re.search(r"\.[a-z0-9]{2,5}$", part) and part in _PLACEHOLDER_UPLOAD_NAMES:
                return True
    return bool(re.fullmatch(r"user[_\s-]?sop(?:\.[a-z0-9]+)?", n))


def _resolve_doc_name_for_pill(head: str, upload_name: str) -> str:
    doc = (head or "").strip()
    upload = (upload_name or "").strip()
    if upload and not _looks_like_guideline_document(upload):
        if (
            not doc
            or _AUTO_UPLOAD_NAME_RE.match(doc)
            or _is_placeholder_upload_name(doc)
            or _looks_like_guideline_document(doc)
            or doc.lower() in {upload.lower(), upload.lower().replace(".docx", "")}
        ):
            return upload
        if ":" in doc:
            parts = doc.split(":")
            if len(parts) >= 3:
                inner_doc = ":".join(parts[1:-1]).strip()
                if inner_doc and (
                    _looks_like_guideline_document(inner_doc)
                    or _is_placeholder_upload_name(inner_doc)
                ):
                    return upload
            elif any(_is_placeholder_upload_name(p.strip()) for p in parts):
                return upload
    if doc and not re.search(r"\.[a-z0-9]{2,5}$", doc, re.I) and upload:
        if _AUTO_UPLOAD_NAME_RE.match(doc) or _is_placeholder_upload_name(doc):
            return upload
    return doc or upload or "Uploaded_Document.docx"


def rewrite_placeholder_upload_citations(
    text: str,
    *,
    upload_name: str,
    repository_name: str = "",
    page_index: Optional[DocumentPageIndex] = None,
) -> str:
    """Replace User_SOP.docx / Uploaded_Document.docx citations with the real upload name."""
    upload = (upload_name or "").strip()
    if not upload or not text:
        return text or ""
    repo = (repository_name or "Upload").strip()

    def _rewrite_plain(match: re.Match[str]) -> str:
        head = match.group(1).strip()
        quote = match.group(2)
        if not _is_placeholder_upload_name(head):
            return match.group(0)
        # Already inside an HTML pill — leave for HTML rewriter
        start = match.start()
        window = text[max(0, start - 80) : start]
        if 'citation-pill">' in window or "citation-pill\">" in window:
            return match.group(0)
        page = page_index.page_for_quote(quote) if page_index else None
        return build_citation_pill_html(
            quote,
            document_name=upload,
            repository_name=repo,
            page_number=page,
            quote_limit=120,
        )

    return _PLAIN_BRACKET_CITATION_RE.sub(_rewrite_plain, text)


def find_stored_upload_path(filename: str = "") -> str:
    """Locate a prior chat upload on disk (uploads/{uuid}_{name})."""
    uploads_dir = os.path.join(os.getcwd(), "uploads")
    if not os.path.isdir(uploads_dir):
        return ""
    name = (filename or "").strip()
    newest = ""
    newest_mtime = 0.0
    for entry in os.listdir(uploads_dir):
        full = os.path.join(uploads_dir, entry)
        if not os.path.isfile(full):
            continue
        if name and not (entry == name or entry.endswith(f"_{name}")):
            continue
        try:
            mtime = os.path.getmtime(full)
        except OSError:
            mtime = 0.0
        if mtime >= newest_mtime:
            newest_mtime = mtime
            newest = full
    return newest


def infer_upload_name_from_report(markdown: str) -> str:
    """Best-effort filename extraction from a stored compliance report."""
    text = markdown or ""

    match = re.search(
        r"Analyzing\s+\*\*([^*]+\.(?:docx|doc|pdf|txt))\*\*",
        text,
        re.I,
    )
    if match:
        candidate = match.group(1).strip()
        if candidate and not _looks_like_guideline_document(candidate):
            return candidate

    match = re.search(r"\[File:\s*([^\]]+\.(?:docx|doc|pdf|txt))\]", text, re.I)
    if match:
        candidate = match.group(1).strip()
        if candidate and not _looks_like_guideline_document(candidate):
            return candidate

    # New-format upload pills: [Repo:Document.docx:12] — prefer docx over pdf.
    pill_candidates: list[str] = []
    for match in _CITATION_PILL_RE.finditer(text):
        head = match.group(1).strip()
        if ":" in head:
            parts = head.split(":")
            if len(parts) >= 3:
                doc = ":".join(parts[1:-1]).strip()
                if doc:
                    pill_candidates.append(doc)
        elif head.endswith((".docx", ".doc", ".pdf", ".txt")):
            pill_candidates.append(head)

    upload_like = [
        c for c in pill_candidates if not _looks_like_guideline_document(c)
    ]
    if upload_like:
        return max(upload_like, key=_score_upload_candidate)

    return ""


def upgrade_citation_pills_in_text(
    text: str,
    *,
    upload_name: str = "",
    repository_name: str = "",
    page_index: Optional[DocumentPageIndex] = None,
) -> str:
    repo = (repository_name or "Upload").strip()
    body = rewrite_placeholder_upload_citations(
        text or "",
        upload_name=upload_name,
        repository_name=repo,
        page_index=page_index,
    )

    def _replace(match: re.Match[str]) -> str:
        head = match.group(1).strip()
        quote = match.group(2)
        needs = _citation_label_needs_upgrade(head) or _is_placeholder_upload_name(head)
        if not needs:
            return match.group(0)
        doc_name = _resolve_doc_name_for_pill(head, upload_name)
        page = page_index.page_for_quote(quote) if page_index else None
        return build_citation_pill_html(
            quote,
            document_name=doc_name,
            repository_name=repo,
            page_number=page,
            quote_limit=120,
        )

    return _CITATION_PILL_RE.sub(_replace, body)


def upgrade_compliance_report_markdown(
    markdown: str,
    *,
    upload_name: str = "",
    repository_name: str = "",
    upload_path: str = "",
) -> str:
    """Upgrade legacy compliance reports for chat-history replay."""
    text = markdown or ""
    if not text.strip():
        return text
    if not re.search(
        r"Compliance Check Report|SUMMARY\s+SCORES|ENGLISH/LANGUAGE\s+QUALITY",
        text,
        re.I,
    ):
        return text

    explicit_name = (upload_name or "").strip()
    inferred_name = infer_upload_name_from_report(text) if not explicit_name else ""
    resolved_name = explicit_name or inferred_name
    if resolved_name and _looks_like_guideline_document(resolved_name):
        resolved_name = explicit_name or ""

    if resolved_name and _AUTO_UPLOAD_NAME_RE.match(resolved_name):
        recovered_path = find_stored_upload_path(resolved_name)
        if recovered_path:
            better, _ = resolve_upload_display_name(
                [{"filename": resolved_name, "path": recovered_path}]
            )
            if better and not _looks_like_guideline_document(better):
                resolved_name = better

    resolved_path = (upload_path or "").strip()
    if not resolved_path and resolved_name:
        resolved_path = find_stored_upload_path(resolved_name)

    page_index = (
        DocumentPageIndex(resolved_path)
        if resolved_path and os.path.exists(resolved_path)
        else None
    )

    # Only inject filename into the analyzing line when we trust the upload name
    # (live session metadata / upload handler). Never rewrite from inferred guideline PDFs.
    if explicit_name and not re.search(
        r"Analyzing\s+\*\*[^*]+\*\*\s*\((?:SOP|URS)\)",
        text,
        re.I,
    ):
        text = re.sub(
            r"Analyzing\s+(?:\*\*)?(SOP|URS)(?:\*\*)?\s+against",
            rf"Analyzing **{explicit_name}** (\1) against",
            text,
            count=1,
            flags=re.I,
        )
    elif explicit_name:
        # Fix previously wrong guideline names in the analyzing header.
        text = re.sub(
            r"Analyzing\s+\*\*[^*]+\*\*\s*\((SOP|URS)\)\s+against",
            rf"Analyzing **{explicit_name}** (\1) against",
            text,
            count=1,
            flags=re.I,
        )

    from agents.compliance_check_agent import ComplianceCheckAgent

    if repository_name:
        ComplianceCheckAgent._citation_repository_name = repository_name
    normalize = getattr(ComplianceCheckAgent, "_normalize_amneal_content_coverage", None)
    if callable(normalize):
        text = normalize(text)
    refresh = getattr(ComplianceCheckAgent, "refresh_summary_scores_in_report", None)
    if callable(refresh):
        text = refresh(text)

    return upgrade_citation_pills_in_text(
        text,
        upload_name=resolved_name,
        repository_name=repository_name or "Upload",
        page_index=page_index,
    )
