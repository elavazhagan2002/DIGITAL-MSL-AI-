"""Grounded-answer controls: which documents may be used, and what text may be sent.

Rules enforced here:
* only approved, in-force documents are quoted (fail closed);
* doctor-facing text is assembled from verbatim quotes plus a citation, never written by an LLM;
* the text that will actually be sent is checked again (quotes, numbers, units, doses, trial names)
  against the retrieved passages, and a failure escalates the whole answer.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Iterable, Optional

from digital_msl.contracts import Claim, EvidenceChunk

logger = logging.getLogger(__name__)

META_KEYS = ("approval_status", "version", "effective_date", "expiry_date", "superseded_by", "document_title")
NOT_STATED = "not stated"


# ------------------------------------------------------------------ document status
def meta_value(raw: dict[str, Any], key: str) -> str:
    """Find a metadata field on a retrieve hit, wherever IKM nests it."""
    anchor = raw.get("citation_anchor") if isinstance(raw.get("citation_anchor"), dict) else {}
    holders = [
        raw,
        raw.get("metadata"),
        raw.get("document_metadata"),
        anchor,
        anchor.get("document_metadata") if isinstance(anchor.get("document_metadata"), dict) else None,
    ]
    aliases = {"version": ("version", "version_number"), "document_title": ("document_title", "title")}.get(key, (key,))
    for holder in holders:
        if not isinstance(holder, dict):
            continue
        for alias in aliases:
            value = holder.get(alias)
            if value not in (None, "", [], {}):
                return str(value).strip()
    return ""


def merge_metadata(raw: dict[str, Any], listed: Optional[dict[str, Any]]) -> dict[str, str]:
    """Hit metadata first, then whatever the document-list endpoint says about that document."""
    out = {key: meta_value(raw, key) for key in META_KEYS}
    if listed:
        for key in META_KEYS:
            if not out[key]:
                out[key] = meta_value(listed, key)
    return out


def parse_date(value: str) -> Optional[date]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def exclusion_reason(meta: dict[str, str], *, require_metadata: bool, today: Optional[date] = None) -> str:
    """Why a document may not be used ('' when it may)."""
    today = today or datetime.now(timezone.utc).date()
    status = (meta.get("approval_status") or "").strip().upper()
    if not status:
        has_other = any(meta.get(k) for k in ("effective_date", "expiry_date", "superseded_by"))
        if require_metadata:
            return "missing_approval_metadata"
        if not has_other:
            return ""  # IKM carries no approval metadata for this document yet
    elif status != "APPROVED":
        return f"not_approved:{status.lower()}"
    if meta.get("superseded_by"):
        return "superseded"
    expiry = parse_date(meta.get("expiry_date", ""))
    if meta.get("expiry_date") and expiry is None:
        return "unreadable_expiry_date"
    if expiry and expiry < today:
        return "expired"
    effective = parse_date(meta.get("effective_date", ""))
    if meta.get("effective_date") and effective is None:
        return "unreadable_effective_date"
    if effective and effective > today:
        return "not_yet_effective"
    return ""


# ------------------------------------------------------------------ extractive composition
def _norm(text: str) -> str:
    text = str(text or "").replace("µ", "u").replace("μ", "u").replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"').replace("–", "-").replace("—", "-")
    text = re.sub(r"(\w)-\s+(\w)", r"\1\2", text)  # hyphenation at line breaks
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def occurs_exactly(quote: str, passage: str) -> bool:
    q, p = _norm(quote), _norm(passage)
    return bool(q) and q in p


@dataclass
class Quote:
    text: str
    chunk: Optional[EvidenceChunk]
    document_name: str
    page_no: int
    section: str
    claim_id: str = ""


@dataclass
class Extractive:
    body: str
    reference_lines: list[str]
    quotes: list[Quote] = field(default_factory=list)
    dropped_claims: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.quotes)


def _title(chunk: Optional[EvidenceChunk], name: str) -> str:
    from digital_msl.summary import document_title

    if chunk is not None and chunk.document_title:
        return chunk.document_title
    return document_title(name)


def reference_line(index: int, quote: Quote) -> str:
    chunk = quote.chunk
    parts = [f"version {(chunk.version if chunk and chunk.version else NOT_STATED)}"]
    if quote.page_no:
        parts.append(f"page {quote.page_no}")
    if quote.section:
        parts.append(f"section {quote.section}")
    parts.append(f"effective {(chunk.effective_date if chunk and chunk.effective_date else NOT_STATED)}")
    return f"[{index}] {_title(chunk, quote.document_name)} — " + ", ".join(parts) + "."


INTRO = "From the approved sources:"


def compose_extractive(claims: Iterable[Claim], chunks: Optional[Iterable[EvidenceChunk]] = None) -> Extractive:
    """Verbatim quotes with numbered citations. No sentence here is written by a model."""
    by_id = {c.chunk_id: c for c in (chunks or [])}
    quotes: list[Quote] = []
    seen: set[tuple[str, str]] = set()
    dropped: list[str] = []
    for claim in claims:
        used = False
        for ev in claim.evidence:
            chunk = by_id.get(ev.chunk_id)
            passage = (chunk.document_content if chunk else "") or ev.document_content
            quote = (ev.original_text or "").strip()
            if quote and not occurs_exactly(quote, passage):
                from digital_msl.evidence import verbatim_excerpt

                quote = verbatim_excerpt(passage, quote, claim.claim_text).strip()
            if not quote or not occurs_exactly(quote, passage):
                continue
            key = (ev.chunk_id or ev.document_id, _norm(quote))
            if key in seen:
                used = True
                continue
            seen.add(key)
            quotes.append(
                Quote(
                    text=quote,
                    chunk=chunk,
                    document_name=ev.document_name or (chunk.document_name if chunk else ""),
                    page_no=ev.page_no or (chunk.page_no if chunk else 0),
                    section=(ev.section or (chunk.section if chunk else "")),
                    claim_id=claim.claim_id,
                )
            )
            used = True
        if not used:
            dropped.append(claim.claim_id)
    if not quotes:
        return Extractive(body="", reference_lines=[], dropped_claims=dropped)
    lines = [INTRO, ""]
    refs: list[str] = []
    for index, quote in enumerate(quotes, start=1):
        lines.append(f"“{quote.text}” [{index}]")
        lines.append("")
        refs.append(reference_line(index, quote))
    return Extractive(body="\n".join(lines).strip(), reference_lines=refs, quotes=quotes, dropped_claims=dropped)


# ------------------------------------------------------------------ final-text groundedness
_UNITS = (
    r"%|mg|mcg|ug|g|ml|mm|um|mmhg|letters?|weeks?|months?|years?|days?|hours?|minutes?|"
    r"eyes?|patients?|participants?|injections?|doses?|ie|iu"
)
_NUMBER = re.compile(rf"(?<![\w.])(\d+(?:\.\d+)?)(?:\s*({_UNITS})(?![a-z]))?", re.I)
_ACRONYM = re.compile(r"\b[A-Z][A-Z0-9\-]{2,}\b")


def _numbers_with_units(text: str) -> list[str]:
    found: list[str] = []
    for number, unit in _NUMBER.findall(_norm(text)):
        found.append(f"{number} {unit.lower()}".strip())
    return found


@dataclass
class GroundednessResult:
    passed: bool
    problems: list[str] = field(default_factory=list)
    checked_quotes: int = 0
    checked_numbers: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "problems": self.problems,
            "checked_quotes": self.checked_quotes,
            "checked_numbers": self.checked_numbers,
        }


def check_final_text(final_text: str, extractive: Extractive, chunks: Iterable[EvidenceChunk]) -> GroundednessResult:
    """Check the exact text that will be sent against the retrieved passages.

    * every quote must appear in the passage it cites;
    * after removing the fixed template words and the citation lines, every number (with its unit),
      dose, percentage and acronym / trial name left in the text must appear in the retrieved passages.
    """
    problems: list[str] = []
    passages = [c.document_content for c in chunks]
    corpus = _norm(" ".join(passages))
    for quote in extractive.quotes:
        if f"“{quote.text}”" not in final_text:
            problems.append(f"quote_missing_from_text:{quote.text[:50]}")
        cited = quote.chunk.document_content if quote.chunk else ""
        if not (occurs_exactly(quote.text, cited) or (not cited and occurs_exactly(quote.text, " ".join(passages)))):
            problems.append(f"quote_not_in_cited_passage:{quote.text[:50]}")

    residue = final_text.replace(INTRO, "")
    for ref in extractive.reference_lines:
        residue = residue.replace(ref, "")
    residue = re.sub(r"\[\d+\]", "", residue)
    numbers = _numbers_with_units(residue)
    for token in numbers:
        number, _, unit = token.partition(" ")
        if token not in corpus and not (not unit and re.search(rf"(?<![\w.]){re.escape(number)}(?![\w.])", corpus)):
            problems.append(f"number_not_in_sources:{token}")
    for acronym in set(_ACRONYM.findall(residue)):
        if acronym.lower() not in corpus:
            problems.append(f"term_not_in_sources:{acronym}")
    return GroundednessResult(
        passed=not problems, problems=problems, checked_quotes=len(extractive.quotes), checked_numbers=len(numbers)
    )
