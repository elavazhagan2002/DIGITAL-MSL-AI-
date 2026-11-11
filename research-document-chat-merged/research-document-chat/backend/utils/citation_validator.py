"""
Citation validation and extraction utilities for research document RAG.
Verifies that citations generated in LLM outputs correspond to real retrieved evidence chunks.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

CITATION_HTML_REGEX = re.compile(
    r'<span\s+class="citation">\s*<span\s+class="citation-pill">\s*'
    r'\[([^\]]+)\](?:\s*[—\-]\s*"([^"]*)")?\s*</span>\s*</span>',
    re.IGNORECASE
)

PLAIN_BRACKET_REGEX = re.compile(
    r'\[([A-Za-z0-9_\-\.\s]+:[A-Za-z0-9_\-\.\s]+:[0-9—\-]*)\](?:\s*[—\-]\s*"([^"]*)")?',
    re.IGNORECASE
)


class CitationValidator:
    """Validates citations against context chunks retrieved from DMS."""

    @staticmethod
    def extract_citations(text: str) -> List[Dict[str, Any]]:
        """Extract all HTML and bracket citations from text."""
        citations = []
        # Find HTML spans
        for match in CITATION_HTML_REGEX.finditer(text):
            label = match.group(1).strip()
            quote = match.group(2).strip() if match.group(2) else ""
            parts = [p.strip() for p in label.split(":")]
            repo = parts[0] if len(parts) > 0 else ""
            doc = parts[1] if len(parts) > 1 else ""
            page = parts[2] if len(parts) > 2 else ""
            citations.append({
                "raw_match": match.group(0),
                "label": label,
                "repository": repo,
                "document": doc,
                "page": page,
                "quote": quote,
                "is_html": True
            })

        # Also find bracket citations if any
        for match in PLAIN_BRACKET_REGEX.finditer(text):
            label = match.group(1).strip()
            if any(c["label"] == label for c in citations):
                continue
            quote = match.group(2).strip() if match.group(2) else ""
            parts = [p.strip() for p in label.split(":")]
            citations.append({
                "raw_match": match.group(0),
                "label": label,
                "repository": parts[0] if len(parts) > 0 else "",
                "document": parts[1] if len(parts) > 1 else "",
                "page": parts[2] if len(parts) > 2 else "",
                "quote": quote,
                "is_html": False
            })

        return citations

    @classmethod
    def validate_citations_against_chunks(
        cls,
        text: str,
        retrieved_chunks: List[Dict[str, Any]],
        target_repository_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Validate that every citation matches at least one retrieved chunk.
        Checks:
        1. Repository matches target repository.
        2. Document name matches a chunk in context.
        3. Page number matches if available.
        """
        citations = cls.extract_citations(text)
        validated_citations = []
        unverified_citations = []

        # Keep all chunks for a document: one file can contribute evidence from many pages.
        chunk_docs: dict[str, list[dict[str, Any]]] = {}
        for chunk in retrieved_chunks:
            filename = str(chunk.get("file_name") or chunk.get("fileName") or "").strip().lower()
            if filename:
                chunk_docs.setdefault(filename, []).append(chunk)

        for cit in citations:
            doc_key = cit["document"].lower()
            candidate_chunks: list[dict[str, Any]] = []
            for fname, chunks in chunk_docs.items():
                if fname == doc_key or doc_key in fname or fname in doc_key:
                    candidate_chunks.extend(chunks)

            matched_chunk = candidate_chunks[0] if candidate_chunks else None
            is_valid = bool(candidate_chunks)

            # When the DMS exposes repository IDs, enforce the requested repository.
            # Otherwise the retrieval request itself is repository-scoped and the
            # citation label may contain a display name rather than the repository ID.
            if is_valid and target_repository_id:
                repository_ids = {
                    str(chunk.get("repository_id") or "").strip().lower()
                    for chunk in candidate_chunks
                    if str(chunk.get("repository_id") or "").strip()
                }
                if repository_ids and target_repository_id.strip().lower() not in repository_ids:
                    is_valid = False

            # A numeric page citation is verified only when the cited page exists in
            # the retrieved evidence metadata. Never silently certify an invented page.
            requested_page = str(cit.get("page") or "").strip()
            if is_valid and requested_page.isdigit():
                page_matches = []
                for chunk in candidate_chunks:
                    try:
                        page_start = int(chunk.get("page_number") or 0)
                        page_end = int(chunk.get("page_end") or page_start or 0)
                    except (TypeError, ValueError):
                        continue
                    if page_start > 0 and page_start <= int(requested_page) <= max(page_end, page_start):
                        page_matches.append(chunk)
                if not page_matches:
                    is_valid = False
                else:
                    matched_chunk = page_matches[0]

            cit_info = {
                **cit,
                "verified": is_valid,
                "matched_chunk_id": matched_chunk.get("chunk_index") if is_valid and matched_chunk else None,
                "source_file_id": matched_chunk.get("file_id") if is_valid and matched_chunk else None,
            }

            if is_valid:
                validated_citations.append(cit_info)
            else:
                unverified_citations.append(cit_info)

        return {
            "all_citations": citations,
            "verified_citations": validated_citations,
            "unverified_citations": unverified_citations,
            "is_fully_traceable": len(unverified_citations) == 0,
            "citation_count": len(citations),
        }
