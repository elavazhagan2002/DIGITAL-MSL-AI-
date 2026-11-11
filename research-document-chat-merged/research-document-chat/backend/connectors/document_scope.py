"""Resolve explicit document references for scoped Medical Literacy Research chat."""

from __future__ import annotations

import re
from typing import Any

from connectors.chat import parse_inline_citations

_PUBMED_FILENAME_RE = re.compile(r"pubmed_(\d+)", re.I)
_CLINICALTRIALS_FILENAME_RE = re.compile(r"clinicaltrials_(nct\d+)", re.I)
_NCT_RE = re.compile(r"\b(nct\d+)\b", re.I)
_THIS_DOCUMENT_RE = re.compile(r"\b(?:this|that)\s+document\b", re.I)


def extract_document_identifiers(text: str) -> list[str]:
    """Pull PMID / NCT / connector filename tokens from a user message."""
    raw = str(text or "")
    found: list[str] = []
    seen: set[str] = set()

    def _add(value: str) -> None:
        token = str(value or "").strip()
        if not token:
            return
        key = token.lower()
        if key in seen:
            return
        seen.add(key)
        found.append(token)

    for match in _CLINICALTRIALS_FILENAME_RE.finditer(raw):
        _add(match.group(1).upper())
    for match in _PUBMED_FILENAME_RE.finditer(raw):
        _add(f"pubmed_{match.group(1)}")
        _add(match.group(1))
    for match in _NCT_RE.finditer(raw):
        _add(match.group(1).upper())
    return found


def extract_document_identifiers_from_history(
    user_message: str,
    conversation_history: list[dict[str, Any]] | None,
) -> list[str]:
    """When the user says 'this document', reuse the last cited document name."""
    if not _THIS_DOCUMENT_RE.search(str(user_message or "")):
        return []
    if not conversation_history:
        return []
    for message in reversed(conversation_history):
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "").strip().lower() != "assistant":
            continue
        citations = parse_inline_citations(str(message.get("content") or ""))
        doc_names = [str(item.get("doc") or "").strip() for item in citations if item.get("doc")]
        if doc_names:
            return doc_names
        break
    return []


def document_filename_matches_identifiers(filename: str, identifiers: list[str]) -> bool:
    haystack = str(filename or "").strip().lower()
    if not haystack or not identifiers:
        return False
    for ident in identifiers:
        token = str(ident or "").strip().lower()
        if not token:
            continue
        if token in haystack:
            return True
        if token.startswith("pubmed_"):
            pmid = token.split("_", 1)[-1]
            if pmid and pmid in haystack:
                return True
        if token.startswith("nct") and token in haystack:
            return True
    return False


def resolve_document_identifiers(
    user_message: str,
    conversation_history: list[dict[str, Any]] | None,
) -> list[str]:
    identifiers = extract_document_identifiers(user_message)
    if identifiers:
        return identifiers
    return extract_document_identifiers_from_history(user_message, conversation_history)


def is_document_scoped_request(
    user_message: str,
    conversation_history: list[dict[str, Any]] | None,
) -> bool:
    return bool(resolve_document_identifiers(user_message, conversation_history))
