"""Repository evidence retrieval (AWS-hosted IKM / DMS) with citation anchors.

Each DMS ``/retrieve`` result carries a ``citation_anchor`` with page, section and
source blocks (paragraph order + line numbers). This module turns them into
``EvidenceChunk`` objects so every claim can cite document name, page and paragraph.
"""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from digital_msl import grounding
from digital_msl.config import get_dmsl_settings
from digital_msl.contracts import EvidenceChunk, RetrievalChunkSnapshot, RetrievalSnapshot

logger = logging.getLogger(__name__)

_RETRIEVAL_NOISE = re.compile(
    r"\b(what|which|when|where|how|did|does|do|is|are|was|were|the|a|an|please|tell|me|about|"
    r"question|regarding|from|your|therapeutic|area|documents|repository)\b",
    re.I,
)


def retrieval_search_query(user_text: str) -> str:
    """Short DMS-friendly query: keep cohort/drug/endpoint tokens, drop conversational filler."""
    raw = re.sub(r"\s+", " ", (user_text or "").strip())
    if not raw:
        return raw
    normalized = raw.replace("563-EYE", "563-eye").replace("563 EYE", "563-eye")
    if len(normalized) <= 160:
        return normalized
    kept: list[str] = []
    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-./%]{1,}", normalized):
        low = token.lower()
        if _RETRIEVAL_NOISE.match(low) and low not in {"dme", "amd", "vegf"}:
            continue
        if len(token) >= 3 or token.isdigit():
            kept.append(token)
    compact = " ".join(dict.fromkeys(kept))[:220]
    return compact or normalized[:220]



class RetrieveClient(Protocol):
    def retrieve(self, *, query: str, top_k: int = 10, repository_id: Optional[str] = None, **kwargs) -> dict: ...

    def list_repository_documents(self, repository_id: str, *, limit: int = 500) -> dict: ...


@dataclass
class RetrievalOutcome:
    chunks: list[EvidenceChunk] = field(default_factory=list)
    blocked: bool = False
    block_reason: str = ""
    error: str = ""
    search_mode: str = ""
    elapsed_ms: int = 0
    # Fail-closed bookkeeping
    no_approved_source: bool = False  # repository not on the approved allow-list, or every hit was excluded
    excluded: list[dict[str, str]] = field(default_factory=list)
    unversioned: list[str] = field(default_factory=list)


def _int(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _looks_numeric(label: str) -> bool:
    return label.replace(".", "", 1).replace("-", "", 1).isdigit()


_TABLE_HEADING = re.compile(
    r"^(table|figure|fig\.?|item|supplement|appendix)\b|\bmeasure\s*:|\boutcome measure\b",
    re.I,
)


def _usable_section(label: str) -> str:
    """Drop DMS section labels that are page headers, table titles, or column rows."""
    label = re.sub(r"\s+", " ", str(label or "")).strip(" |-")
    if not label or len(label) > 60 or _looks_numeric(label):
        return ""
    if "|" in label or _TABLE_HEADING.search(label):
        return ""
    digits = sum(ch.isdigit() for ch in label)
    if digits / len(label) > 0.15 or label.lower().startswith(("received:", "accepted:", "published:")):
        return ""
    return label


def _paragraph_locator(section: str, paragraph_no: int, line_start: int, line_end: int) -> str:
    parts: list[str] = []
    if section:
        parts.append(f"Section: {section}")
    if paragraph_no:
        parts.append(f"Paragraph {paragraph_no}")
    if line_start:
        parts.append(f"Lines {line_start}-{line_end or line_start}")
    return " | ".join(parts)


def normalize_result(raw: dict[str, Any], *, doc_names: dict[str, str], repository_id: str) -> EvidenceChunk:
    anchor = raw.get("citation_anchor") if isinstance(raw.get("citation_anchor"), dict) else {}
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    blocks = anchor.get("source_blocks") if isinstance(anchor.get("source_blocks"), list) else []
    first_block = blocks[0] if blocks and isinstance(blocks[0], dict) else {}

    document_id = str(anchor.get("document_id") or metadata.get("document_id") or raw.get("document_id") or "")
    raw_name = str(raw.get("document_name") or raw.get("doc_name") or anchor.get("document_name") or "")
    document_name = doc_names.get(document_id) or raw_name or document_id or "Unknown document"

    section = _usable_section(str(anchor.get("parent_section") or ""))
    if not section:
        section = _usable_section(str(anchor.get("section_name") or raw.get("section_name") or ""))

    page = _int(anchor.get("page_start") or anchor.get("start_page") or raw.get("page") or metadata.get("page"))
    page_end = _int(anchor.get("page_end") or anchor.get("end_page") or raw.get("page_end")) or page
    line_start = _int(anchor.get("start_line") or raw.get("line_start") or first_block.get("line_start"))
    line_end = _int(anchor.get("end_line") or raw.get("line_end") or first_block.get("line_end")) or line_start
    reading_order = first_block.get("reading_order")
    paragraph_no = _int(reading_order) + 1 if reading_order is not None else _int(first_block.get("index")) + 1

    return EvidenceChunk(
        chunk_id=str(raw.get("chunk_id") or raw.get("id") or anchor.get("chunk_id") or ""),
        document_id=document_id,
        document_name=document_name,
        document_content=str(raw.get("text") or raw.get("content") or ""),
        page_no=page,
        page_end=page_end,
        paragraph=_paragraph_locator(section, paragraph_no, line_start, line_end),
        section=section,
        line_start=line_start,
        line_end=line_end,
        retrieval_score=float(raw.get("score") or 0.0),
        repository_id=repository_id,
    )


_DOC_NAME_CACHE: dict[str, tuple[float, dict[str, str]]] = {}
_DOC_NAME_TTL_SECONDS = 600.0


_DOC_LISTING_CACHE: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}


def clear_document_caches() -> None:
    _DOC_NAME_CACHE.clear()
    _DOC_LISTING_CACHE.clear()


def document_listing(client: RetrieveClient, repository_id: str) -> dict[str, dict[str, Any]]:
    """document_id -> the repository's own record for it (name, metadata). One cached call per repository."""
    cached = _DOC_LISTING_CACHE.get(repository_id)
    now = time.time()
    if cached and now - cached[0] < _DOC_NAME_TTL_SECONDS:
        return cached[1]
    try:
        payload = client.list_repository_documents(repository_id, limit=200)
    except Exception as exc:  # best-effort: missing metadata is handled by the fail-closed rules
        logger.warning("Could not list repository documents: %s", exc)
        return cached[1] if cached else {}
    listing: dict[str, dict[str, Any]] = {}
    for doc in payload.get("documents") or []:
        doc_id = str(doc.get("document_id") or "")
        if doc_id:
            listing[doc_id] = doc
    _DOC_LISTING_CACHE[repository_id] = (now, listing)
    return listing


def document_name_map(client: RetrieveClient, repository_id: str) -> dict[str, str]:
    cached = _DOC_NAME_CACHE.get(repository_id)
    now = time.time()
    if cached and now - cached[0] < _DOC_NAME_TTL_SECONDS:
        return cached[1]
    names: dict[str, str] = {}
    for doc_id, doc in document_listing(client, repository_id).items():
        name = str(doc.get("original_file_name") or doc.get("document_name") or "")
        if name:
            names[doc_id] = name
    _DOC_NAME_CACHE[repository_id] = (now, names)
    return names


_UNKNOWN_NAMES = frozenset({"", "unknown document", "unknown", "untitled"})
_UUID_FILENAME = re.compile(r"^[0-9a-f-]{32,}\.[a-z0-9]+$", re.I)


def _needs_filename_lookup(name: str, document_id: str) -> bool:
    label = (name or "").strip()
    if not label or label.lower() in _UNKNOWN_NAMES:
        return True
    if document_id and (label == document_id or label.startswith(document_id)):
        return True
    return bool(_UUID_FILENAME.match(label))


_CITE_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_CITE_PAGES = re.compile(r"\b\d{1,4}\s*:\s*\d{2,5}\s*[-–]\s*\d{1,5}\b")
_ET_AL = re.compile(r"\bet al\.?", re.I)
_REF_HEADING = re.compile(r"\b(references|bibliography|literature cited)\b", re.I)
_DOI = re.compile(r"\bdoi:\s*10\.", re.I)
_AUTHOR_LINE = re.compile(
    r"^[A-Z][A-Za-z'\-]+(?:\s+[A-Z]\.?){1,3}(?:,\s*[A-Z][A-Za-z'\-]+(?:\s+[A-Z]\.?){1,3}){1,}",
)
_BOILERPLATE = re.compile(
    r"^(consent for publication|competing interests|acknowledgements?|funding|"
    r"data availability|ethics approval|not applicable)\b",
    re.I,
)
_STOPWORDS = frozenset(
    "a an and are as at be been being but by can could did do does for from had has have "
    "in into is it its may might more most no not of on or our than that the their them then "
    "there these they this those to was were which while who will with within would also both "
    "each other such between after before during over under up down out about against among "
    "per via vs versus".split()
)


def _content_terms(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", re.sub(r"[^a-z0-9.%\s]", " ", str(text or "").lower()))
        if token not in _STOPWORDS and len(token) > 2 and not token.isdigit()
    }


def is_citation_line(text: str) -> bool:
    """True for a bibliography entry (author list + year + journal pages), not body prose."""
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned or len(cleaned) > 420:
        return False
    if _BOILERPLATE.match(cleaned):
        return True
    has_year = bool(_CITE_YEAR.search(cleaned))
    has_pages = bool(_CITE_PAGES.search(cleaned) or re.search(r"\b(?:19|20)\d{2};\d", cleaned))
    has_et_al = bool(_ET_AL.search(cleaned))
    looks_authors = bool(_AUTHOR_LINE.match(cleaned))
    return (has_et_al or looks_authors) and has_year and (has_pages or bool(_DOI.search(cleaned)))


def bibliography_density(text: str) -> float:
    cleaned = str(text or "")
    if not cleaned.strip():
        return 0.0
    et_al = len(_ET_AL.findall(cleaned))
    years = len(_CITE_YEAR.findall(cleaned))
    dois = len(_DOI.findall(cleaned))
    heading = 1.0 if _REF_HEADING.search(cleaned) else 0.0
    length = max(len(cleaned), 1)
    return (et_al * 0.18) + (min(years, 12) * 0.04) + (dois * 0.08) + heading + (80 / length if et_al >= 4 else 0)


def is_bibliography_passage(text: str) -> bool:
    return bibliography_density(text) >= 0.85 or (
        len(_ET_AL.findall(text or "")) >= 4 and len(_CITE_YEAR.findall(text or "")) >= 4
    )


def split_sentences(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        return []
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", cleaned)
    return [part.strip() for part in parts if part.strip()]


def body_sentences(text: str) -> list[str]:
    """Keep prose sentences; drop reference-list entries and publication boilerplate."""
    kept: list[str] = []
    remainder = str(text or "")
    split = _REF_HEADING.split(remainder, maxsplit=1)
    prefix = split[0] if split else remainder
    tail = split[-1] if len(split) > 2 else (split[1] if len(split) == 2 else "")
    candidates = split_sentences(prefix)
    # After a References heading, keep only trailing conclusion/body sentences.
    if tail:
        for sentence in split_sentences(tail):
            if not is_citation_line(sentence) and not _BOILERPLATE.match(sentence) and len(sentence) > 60:
                candidates.append(sentence)
    for sentence in candidates:
        if is_citation_line(sentence) or _BOILERPLATE.match(sentence):
            continue
        if len(sentence) < 40:
            continue
        kept.append(sentence)
    return kept


def supporting_paragraph(passage: str, claim: str, limit: int = 700) -> str:
    """Pick the body paragraph that actually states the claim, not a citation title."""
    sentences = body_sentences(passage)
    if not sentences:
        cleaned = re.sub(r"\s+", " ", str(passage or "")).strip()
        return cleaned[:limit]
    claim_terms = _content_terms(claim)
    scored: list[tuple[float, int, str]] = []
    for index, sentence in enumerate(sentences):
        overlap = len(claim_terms & _content_terms(sentence)) / max(len(claim_terms), 1)
        scored.append((overlap, index, sentence))
    scored.sort(key=lambda item: (-item[0], item[1]))
    best_score, best_index, best = scored[0]
    if best_score < 0.15:
        excerpt = " ".join(sentences[:2])
        return excerpt[:limit]
    neighbors = [best]
    if best_index + 1 < len(sentences) and len(best) < 280:
        neighbors.append(sentences[best_index + 1])
    return " ".join(neighbors)[:limit]


def first_sentence(text: str, limit: int = 400) -> str:
    """Verbatim original-text stand-in before the answer LLM quotes a claim."""
    sentences = body_sentences(text)
    if sentences:
        return sentences[0][:limit]
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        return ""
    match = re.search(r".+?[.!?](?=\s|$)", cleaned)
    excerpt = match.group(0).strip() if match else cleaned
    return excerpt[:limit]


def _compact(text: str) -> str:
    text = str(text or "").lower()
    text = re.sub(r"(\w)-\s+(\w)", r"\1\2", text)
    text = re.sub(r"[^a-z0-9.%\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def quote_occurs_in_passage(quote: str, passage: str) -> bool:
    q_norm, p_norm = _compact(quote), _compact(passage)
    if not q_norm or not p_norm:
        return False
    if q_norm in p_norm:
        return True
    q_terms = [t for t in q_norm.split() if len(t) > 2]
    if len(q_terms) < 6:
        return False
    window = " ".join(q_terms[:12])
    return window in p_norm


def verbatim_excerpt(passage: str, quote: str, claim: str) -> str:
    """Return a contiguous span of the passage that contains the quote."""
    sentences = body_sentences(passage) or split_sentences(passage)
    if quote and sentences:
        for sentence in sentences:
            if quote_occurs_in_passage(quote, sentence) or quote_occurs_in_passage(sentence, quote):
                return sentence
    if quote and quote_occurs_in_passage(quote, passage):
        return supporting_paragraph(passage, quote or claim)
    return supporting_paragraph(passage, claim)


def align_line_span(passage: str, excerpt: str, line_start: int, line_end: int) -> tuple[int, int]:
    """Map the excerpt onto the chunk's line range without inventing a new page."""
    start = line_start or 0
    end = line_end or start
    if start <= 0 or not passage or not excerpt:
        return start, end
    span = max(end - start, 0)
    if span == 0:
        return start, end
    compact_passage = _compact(passage)
    compact_excerpt = _compact(excerpt)
    idx = compact_passage.find(compact_excerpt[:80]) if compact_excerpt else -1
    if idx < 0:
        return start, end
    coverage = len(compact_excerpt) / max(len(compact_passage), 1)
    if idx <= 2 and coverage >= 0.7:
        return start, end
    ratio = idx / max(len(compact_passage), 1)
    width = max(1, round(span * coverage))
    aligned_start = start + int(ratio * span)
    aligned_end = min(end, aligned_start + width)
    if aligned_end < aligned_start:
        aligned_end = aligned_start
    return aligned_start, aligned_end


def _snap_excerpt(chunk: EvidenceChunk, quote: str, claim_text: str) -> EvidenceChunk:
    selected = chunk.model_copy()
    excerpt = verbatim_excerpt(selected.document_content, quote, claim_text)
    selected.original_text = excerpt
    selected.line_start, selected.line_end = align_line_span(
        selected.document_content, excerpt, selected.line_start, selected.line_end
    )
    if selected.line_start and selected.line_end:
        section = selected.section
        paragraph_no = 0
        match = re.search(r"Paragraph\s+(\d+)", selected.paragraph or "")
        if match:
            paragraph_no = int(match.group(1))
        selected.paragraph = _paragraph_locator(section, paragraph_no, selected.line_start, selected.line_end)
    return selected


def resolve_evidence_quote(claim_text: str, quote: str, chunk: EvidenceChunk, chunks: list[EvidenceChunk]) -> EvidenceChunk:
    """Point the citation at the passage that actually contains the quote, with that page and lines."""
    quote = (quote or "").strip()
    usable_quote = bool(quote) and not is_citation_line(quote) and not is_bibliography_passage(quote)
    if usable_quote and quote_occurs_in_passage(quote, chunk.document_content):
        return _snap_excerpt(chunk, quote, claim_text)

    if usable_quote:
        for candidate in chunks:
            if is_bibliography_passage(candidate.document_content):
                continue
            if quote_occurs_in_passage(quote, candidate.document_content):
                return _snap_excerpt(candidate, quote, claim_text)
        # A quote that is not in any passage must stay unchanged so validation can reject it.
        selected = chunk.model_copy()
        selected.original_text = quote
        return selected

    selected = chunk.model_copy()
    if is_bibliography_passage(chunk.document_content):
        claim_terms = _content_terms(claim_text)
        ranked: list[tuple[float, EvidenceChunk]] = []
        for candidate in chunks:
            if is_bibliography_passage(candidate.document_content):
                continue
            overlap = len(claim_terms & _content_terms(candidate.document_content)) / max(len(claim_terms), 1)
            ranked.append((overlap, candidate))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if ranked and ranked[0][0] >= 0.25:
            selected = ranked[0][1].model_copy()
    selected.original_text = supporting_paragraph(selected.document_content, claim_text)
    selected.line_start, selected.line_end = align_line_span(
        selected.document_content, selected.original_text, selected.line_start, selected.line_end
    )
    return selected


def citation_complete(chunk: EvidenceChunk) -> bool:
    name = (chunk.document_name or "").strip()
    if not chunk.document_content.strip():
        return False
    if not name or name.lower() in _UNKNOWN_NAMES:
        return False
    return True


def usable_chunks(chunks: list[EvidenceChunk]) -> list[EvidenceChunk]:
    kept: list[EvidenceChunk] = []
    for chunk in chunks:
        if not citation_complete(chunk):
            logger.info(
                "Dropping retrieved chunk %s: missing citation metadata (name=%r page=%s)",
                chunk.chunk_id,
                chunk.document_name,
                chunk.page_no,
            )
            continue
        if chunk.page_no <= 0:
            chunk.page_no = 1
        if not (chunk.paragraph or "").strip():
            chunk.paragraph = "Document"
        if is_citation_line(chunk.original_text) or is_bibliography_passage(chunk.original_text):
            chunk.original_text = ""
        if not chunk.original_text:
            chunk.original_text = first_sentence(chunk.document_content) or chunk.document_content.strip()
        kept.append(chunk)
    body = [chunk for chunk in kept if not is_bibliography_passage(chunk.document_content)]
    return body or kept


def retrieval_snapshot(
    user_query: str,
    chunks: list[EvidenceChunk],
    *,
    search_mode: str = "",
    elapsed_ms: int = 0,
) -> RetrievalSnapshot:
    return RetrievalSnapshot(
        user_query=user_query,
        agent="query_agent",
        search_mode=search_mode,
        elapsed_ms=elapsed_ms,
        chunks=[
            RetrievalChunkSnapshot(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                document_name=chunk.document_name,
                document_content=chunk.document_content,
                page_no=chunk.page_no,
                paragraph=chunk.paragraph,
                original_text=chunk.original_text or first_sentence(chunk.document_content),
            )
            for chunk in chunks
        ],
    )


def retrieve_evidence(
    client: RetrieveClient,
    *,
    query: str,
    repository_id: str,
    top_k: int = 6,
    search_modes: tuple[str, ...] = ("hybrid", "vector"),
    max_attempts: int = 2,
) -> RetrievalOutcome:
    approved = get_dmsl_settings().approved_repositories()
    if "*" not in approved and repository_id not in approved:
        logger.warning("Repository %s is not on DIGITAL_MSL_APPROVED_REPOSITORY_IDS; no answer will be given from it.", repository_id)
        return RetrievalOutcome(no_approved_source=True)
    last_error = ""
    response: dict[str, Any] | None = None
    used_mode = ""
    started = time.perf_counter()
    modes = search_modes or ("hybrid", "vector")
    for search_mode in modes:
        for attempt in range(max(1, max_attempts)):
            try:
                candidate = client.retrieve(
                    query=query,
                    top_k=top_k,
                    repository_id=repository_id,
                    search_mode=search_mode,
                )
                results = [raw for raw in (candidate.get("results") or []) if isinstance(raw, dict)]
                if results or search_mode == modes[-1]:
                    response = candidate
                    used_mode = search_mode
                    last_error = ""
                    break
                if "hybrid" in modes and search_mode == "hybrid":
                    logger.info("Hybrid retrieve returned no hits; trying vector once")
                break  # empty answer, not an error: move to the next mode instead of repeating this one
            except Exception as exc:
                last_error = str(exc)
                logger.warning(
                    "Evidence retrieval (%s) attempt %s failed: %s",
                    search_mode,
                    attempt + 1,
                    exc,
                )
                response = None
                if attempt + 1 < max_attempts and (
                    "timed out" in last_error.lower() or "timeout" in last_error.lower()
                ):
                    time.sleep(1.5)
                    continue
                break
        if response is not None:
            break
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    if response is None:
        return RetrievalOutcome(error=last_error or "retrieval failed", search_mode=used_mode, elapsed_ms=elapsed_ms)

    if response.get("blocked"):
        return RetrievalOutcome(
            blocked=True,
            block_reason=str(response.get("block_reason") or "blocked by DMS prompt guard"),
            search_mode=used_mode,
            elapsed_ms=elapsed_ms,
        )

    raw_results = [raw for raw in (response.get("results") or []) if isinstance(raw, dict)]
    preview = [normalize_result(raw, doc_names={}, repository_id=repository_id) for raw in raw_results]
    needs_names = any(_needs_filename_lookup(c.document_name, c.document_id) for c in preview)
    doc_names = document_name_map(client, repository_id) if needs_names else {}
    chunks: list[EvidenceChunk] = []
    seen: set[str] = set()
    excluded: list[dict[str, str]] = []
    unversioned: list[str] = []
    require_metadata = get_dmsl_settings().require_approval_metadata
    listing: Optional[dict[str, dict[str, Any]]] = None
    from digital_msl import store as _store

    registry = _store.get_document_approvals(repository_id)
    for raw, preview_chunk in zip(raw_results, preview):
        chunk = normalize_result(raw, doc_names=doc_names, repository_id=repository_id) if doc_names else preview_chunk
        if not chunk.document_content.strip():
            continue
        meta = grounding.merge_metadata(raw, None)
        if not (meta["approval_status"] and meta["version"]):
            if listing is None:
                listing = document_listing(client, repository_id)
            meta = grounding.merge_metadata(raw, listing.get(chunk.document_id))
        record = registry.get(chunk.document_id)
        if record is not None:
            # Digital MSL's own approval record is authoritative: it overrides whatever IKM carries.
            for key in ("approval_status", "version", "effective_date", "expiry_date", "superseded_by", "document_title"):
                meta[key] = record.get(key, "") or ""
        reason = grounding.exclusion_reason(meta, require_metadata=require_metadata)
        if reason:
            excluded.append({"chunk_id": chunk.chunk_id, "document_id": chunk.document_id, "reason": reason})
            logger.warning("Excluded %s (document %s): %s", chunk.chunk_id or "chunk", chunk.document_id, reason)
            continue
        chunk.document_title = meta["document_title"]
        chunk.version = meta["version"]
        chunk.approval_status = meta["approval_status"]
        chunk.effective_date = meta["effective_date"]
        chunk.expiry_date = meta["expiry_date"]
        chunk.superseded_by = meta["superseded_by"]
        if not chunk.version:
            unversioned.append(chunk.document_id or chunk.document_name)
            logger.warning("Document without version in use: %s", chunk.document_id or chunk.document_name)
        key = chunk.chunk_id or f"{chunk.document_id}:{chunk.page_no}:{chunk.line_start}"
        if key in seen:
            continue
        seen.add(key)
        if not chunk.chunk_id:
            chunk.chunk_id = key
        chunks.append(chunk)
    chunks.sort(key=lambda item: (is_bibliography_passage(item.document_content), -item.retrieval_score))
    return RetrievalOutcome(
        chunks=chunks,
        search_mode=used_mode,
        elapsed_ms=elapsed_ms,
        no_approved_source=bool(excluded) and not chunks,
        excluded=excluded,
        unversioned=sorted(set(unversioned)),
    )


def retrieve_evidence_across_repositories(
    client: RetrieveClient,
    *,
    query: str,
    repository_ids: list[str],
    top_k: int = 6,
) -> tuple[RetrievalOutcome, str]:
    """Search repositories in order; stop when passages are found (one DMS call at a time when possible)."""
    repo_list = [rid for rid in repository_ids if rid]
    if not repo_list:
        return RetrievalOutcome(error="no repositories configured"), ""
    approved = get_dmsl_settings().approved_repositories()
    if "*" not in approved:
        allowed = [rid for rid in repo_list if rid in approved]
        if not allowed:
            logger.warning("None of %s is on DIGITAL_MSL_APPROVED_REPOSITORY_IDS.", repo_list)
            return RetrievalOutcome(no_approved_source=True), repo_list[0]
        repo_list = allowed

    merged: list[EvidenceChunk] = []
    elapsed_total = 0
    modes_used: list[str] = []
    errors: list[str] = []
    primary_repo = ""

    def search(repository_id: str) -> RetrievalOutcome:
        return retrieve_evidence(
            client,
            query=query,
            repository_id=repository_id,
            top_k=top_k,
            search_modes=("hybrid", "vector"),
            max_attempts=2,
        )

    # Each DMS call can take 30+ s, so search every library at once and keep the priority order.
    with ThreadPoolExecutor(max_workers=min(len(repo_list), 4)) as pool:
        outcomes = list(pool.map(search, repo_list))
    elapsed_total = max((o.elapsed_ms or 0 for o in outcomes), default=0)

    for repository_id, outcome in zip(repo_list, outcomes):
        if outcome.search_mode:
            modes_used.append(outcome.search_mode)
        if outcome.error:
            errors.append(outcome.error)
        if outcome.blocked:
            return outcome, repository_id
        if outcome.chunks:
            primary_repo = repository_id
            merged = outcome.chunks
            break

    mode = modes_used[0] if modes_used else "hybrid"
    all_excluded = [item for o in outcomes for item in o.excluded]
    if not merged:
        if all_excluded and not errors:
            return (
                RetrievalOutcome(search_mode=mode, elapsed_ms=elapsed_total, no_approved_source=True, excluded=all_excluded),
                repo_list[0],
            )
        # No hits and no failures is "no passages", not a retrieval error.
        err = errors[-1] if errors else ""
        return (
            RetrievalOutcome(
                error=err,
                search_mode=mode,
                elapsed_ms=elapsed_total,
            ),
            primary_repo or repo_list[0],
        )
    unversioned = sorted({u for o in outcomes for u in o.unversioned})
    return (
        RetrievalOutcome(chunks=merged[:top_k], search_mode=mode, elapsed_ms=elapsed_total, excluded=all_excluded, unversioned=unversioned),
        primary_repo,
    )
