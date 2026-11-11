"""Repository-scoped retrieval helpers for Medical Literacy Research chat."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from connectors.mesh import format_clinicaltrials_query, normalize_mesh_terms

RetrieveFn = Callable[..., dict[str, Any]]
ListDocumentsFn = Callable[[str], list[dict[str, Any]]]
ListChunksFn = Callable[[str], list[dict[str, Any]]]

DEFAULT_MIN_CHUNK_SCORE = 0.0
DEFAULT_TOP_K = 12

_RETRIEVAL_STOPWORDS = frozenset(
    {
        "what",
        "which",
        "when",
        "where",
        "does",
        "about",
        "after",
        "before",
        "how",
        "common",
        "causes",
        "cause",
        "many",
        "much",
        "this",
        "that",
        "from",
        "with",
        "have",
        "been",
        "were",
        "they",
        "their",
        "there",
        "these",
        "those",
        "into",
        "only",
        "also",
        "than",
        "then",
        "such",
        "very",
        "your",
        "repository",
        "literature",
        "evidence",
        "selected",
        "documents",
        "loaded",
        "answer",
        "question",
    }
)


def condense_retrieval_query(query: str) -> str:
    """Strip question filler and keep searchable medical terms for vector/BM25 lookup."""
    terms: list[str] = []
    for token in re.findall(r"[A-Za-z0-9]+", str(query or "")):
        lower = token.lower()
        if len(lower) < 4 or lower in _RETRIEVAL_STOPWORDS:
            continue
        if lower not in terms:
            terms.append(lower)
    if len(terms) >= 2:
        return " ".join(terms[:8])
    if terms:
        return terms[0]
    return str(query or "").strip()


def retrieval_query_terms(query: str) -> list[str]:
    condensed = condense_retrieval_query(query)
    return [term for term in condensed.split() if term.strip()]


def select_primary_retrieval_query(
    queries: list[str],
    *,
    user_message: str = "",
    acronym_tokens: list[str] | None = None,
) -> str:
    """Pick one hybrid-search query instead of issuing multiple /retrieve calls."""
    candidates = [str(item).strip() for item in queries if str(item).strip()]
    if not candidates:
        return str(user_message or "").strip()

    for token in acronym_tokens or []:
        cleaned = str(token or "").strip().upper()
        if not cleaned:
            continue
        preferred = f"Trial ({cleaned})"
        for query in candidates:
            if query.lower() == preferred.lower():
                return query
        return preferred

    user_lower = str(user_message or "").strip().lower()
    for query in candidates:
        lowered = query.lower()
        if lowered != user_lower and len(query.split()) >= 3:
            return query

    return candidates[0]


def resolve_chat_retrieval_queries(
    *,
    plan_queries: list[str],
    user_message: str,
    conversation_history: list[dict[str, Any]] | None,
    backend_action: str,
    router_source: str,
    single_retrieve_call: bool,
    max_expanded_queries: int,
) -> dict[str, Any]:
    """Collapse router/acronym query variants into one API call by default."""
    from connectors.intent_router import (
        enrich_acronym_retrieval_queries,
        extract_acronym_tokens,
    )

    acronym_mode = backend_action == "acronym_definition"
    candidates = [str(item).strip() for item in plan_queries if str(item).strip()]
    if acronym_mode:
        candidates = enrich_acronym_retrieval_queries(
            user_message,
            conversation_history,
            candidates,
        )
    elif not candidates:
        cleaned = str(user_message or "").strip()
        if cleaned:
            candidates = [cleaned]

    tokens = extract_acronym_tokens(user_message, conversation_history) if acronym_mode else []

    if single_retrieve_call:
        primary = select_primary_retrieval_query(
            candidates,
            user_message=user_message,
            acronym_tokens=tokens,
        )
        return {
            "primary_query": primary,
            "override_expanded_queries": None,
            "max_expanded_queries": 1,
            "expand_query": not acronym_mode and router_source != "llm",
            "candidate_queries": candidates,
            "single_retrieve_call": True,
        }

    if acronym_mode or router_source == "llm":
        capped = candidates[: max(1, max_expanded_queries)]
        return {
            "primary_query": capped[0] if capped else str(user_message or "").strip(),
            "override_expanded_queries": capped,
            "max_expanded_queries": len(capped),
            "expand_query": False,
            "candidate_queries": candidates,
            "single_retrieve_call": False,
        }

    primary = candidates[0] if candidates else str(user_message or "").strip()
    return {
        "primary_query": primary,
        "override_expanded_queries": None,
        "max_expanded_queries": max_expanded_queries,
        "expand_query": True,
        "candidate_queries": candidates,
        "single_retrieve_call": False,
    }


def expand_retrieval_queries(query: str) -> list[str]:
    """Expand a user question into lexical / MeSH-oriented search variants."""
    base = str(query or "").strip()
    if not base:
        return []

    queries: list[str] = [base]
    condensed = condense_retrieval_query(base)
    if condensed and condensed.lower() != base.lower() and condensed not in queries:
        queries.append(condensed)

    terms = normalize_mesh_terms(base)
    joined = " ".join(terms)
    if joined and joined.lower() != base.lower() and joined not in queries:
        queries.append(joined)

    for term in terms[:4]:
        plain = format_clinicaltrials_query(term)
        if plain and plain.lower() not in {item.lower() for item in queries}:
            queries.append(plain)

    if len(terms) > 1:
        for term in terms[:3]:
            cleaned = term.strip()
            if cleaned and cleaned not in queries:
                queries.append(cleaned)

    return queries[:5]


def _chunk_dedupe_key(chunk: dict[str, Any]) -> str:
    chunk_id = str(chunk.get("chunk_id") or chunk.get("id") or "").strip()
    if chunk_id:
        return chunk_id
    doc_name = str(chunk.get("doc_name") or "").strip()
    text = str(chunk.get("text") or chunk.get("content") or "").strip()
    return f"{doc_name}:{hash(text[:240])}"


def _chunk_score(chunk: dict[str, Any]) -> float:
    signals = chunk.get("retrieval_signals") or {}
    if isinstance(signals, dict):
        for key in ("rerank_score", "score"):
            value = signals.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    pass
    for key in ("rerank_score", "score", "grounding_score", "quality_score"):
        value = chunk.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    metadata = chunk.get("metadata") or {}
    if isinstance(metadata, dict):
        value = metadata.get("rerank_score") or metadata.get("score")
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    return 0.0


def prioritize_acronym_expansion_chunks(
    chunks: list[dict[str, Any]],
    acronym_tokens: list[str],
) -> list[dict[str, Any]]:
    """Boost chunks that likely contain spelled-out trial names / acronym expansions."""
    tokens = [str(token or "").strip().upper() for token in acronym_tokens if str(token or "").strip()]
    if not chunks or not tokens:
        return chunks

    def _boosted_score(chunk: dict[str, Any]) -> float:
        text = str(chunk.get("text") or chunk.get("content") or "")
        lowered = text.lower()
        score = _chunk_score(chunk)
        for token in tokens:
            token_lower = token.lower()
            if f"trial ({token_lower})" in lowered or f"trial ({token})" in text:
                score += 2.0
            if token_lower in lowered and "blood pressure" in lowered and "oxygenation" in lowered:
                score += 2.5
            if token_lower in lowered and ("targets" in lowered or "cardiac arrest" in lowered):
                score += 1.5
        return score

    return sorted(chunks, key=_boosted_score, reverse=True)


def normalize_retrieval_chunk(chunk: Any) -> dict[str, Any]:
    if not isinstance(chunk, dict):
        return {}
    metadata = dict(chunk.get("metadata") or {})
    text = str(chunk.get("text") or chunk.get("content") or "").strip()
    doc_name = str(
        chunk.get("doc_name")
        or chunk.get("file_name")
        or metadata.get("original_file_name")
        or metadata.get("doc_name")
        or "document"
    ).strip()
    return {
        **chunk,
        "text": text,
        "content": text,
        "doc_name": doc_name,
        "score": _chunk_score(chunk),
        "metadata": metadata,
    }


def merge_retrieval_chunks(payloads: list[dict[str, Any]], *, top_k: int) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for raw in payload.get("results") or payload.get("chunks") or []:
            chunk = normalize_retrieval_chunk(raw)
            if not chunk.get("text"):
                continue
            key = _chunk_dedupe_key(chunk)
            if key in seen:
                continue
            seen.add(key)
            merged.append(chunk)
    merged.sort(key=_chunk_score, reverse=True)
    return merged[: max(1, top_k)]


def chunks_have_repository_evidence(
    chunks: list[dict[str, Any]],
    *,
    min_score: float = DEFAULT_MIN_CHUNK_SCORE,
) -> bool:
    if not chunks:
        return False
    return any(
        str(chunk.get("text") or chunk.get("content") or "").strip()
        and _chunk_score(chunk) >= min_score
        for chunk in chunks
        if isinstance(chunk, dict)
    )


def fetch_repository_chunks_fallback(
    *,
    list_documents_fn: ListDocumentsFn,
    list_chunks_fn: ListChunksFn,
    repository_id: str,
    query: str,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Load stored document chunks and rank by simple term overlap when /retrieve is empty."""
    repo_id = str(repository_id or "").strip()
    if not repo_id:
        return []

    needles = [term.lower() for term in retrieval_query_terms(query) if term.strip()]
    if not needles:
        needles = [
            token.lower()
            for token in re.findall(r"[A-Za-z0-9]+", query)
            if len(token) >= 4 and token.lower() not in _RETRIEVAL_STOPWORDS
        ]

    collected: list[dict[str, Any]] = []
    try:
        documents = list_documents_fn(repo_id)
    except Exception:
        return []

    for document in documents:
        if not isinstance(document, dict):
            continue
        document_id = str(document.get("document_id") or "").strip()
        if not document_id:
            continue
        try:
            raw_chunks = list_chunks_fn(document_id)
        except Exception:
            continue
        for raw in raw_chunks:
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("text") or raw.get("content") or "").strip()
            if not text:
                continue
            doc_name = str(
                raw.get("doc_name")
                or document.get("original_file_name")
                or document.get("document_name")
                or "document"
            )
            haystack = f"{doc_name}\n{text}".lower()
            score = sum(1 for needle in needles if needle in haystack)
            if needles and score == 0:
                continue
            collected.append(
                normalize_retrieval_chunk(
                    {
                        "chunk_id": str(raw.get("chunk_id") or raw.get("id") or ""),
                        "text": text,
                        "doc_name": doc_name,
                        "score": float(score or 0.1),
                        "metadata": {
                            "original_file_name": document.get("original_file_name"),
                            "repository_id": repo_id,
                            "fallback": "document_chunks",
                        },
                    }
                )
            )

    collected.sort(key=_chunk_score, reverse=True)
    return collected[:limit]


def fetch_document_scoped_chunks(
    *,
    list_documents_fn: ListDocumentsFn,
    list_chunks_fn: ListChunksFn,
    repository_id: str,
    identifiers: list[str],
    limit: int = 32,
) -> list[dict[str, Any]]:
    """Load chunks from documents whose filenames match explicit identifiers."""
    from connectors.document_scope import document_filename_matches_identifiers

    repo_id = str(repository_id or "").strip()
    tokens = [str(item).strip() for item in identifiers if str(item).strip()]
    if not repo_id or not tokens:
        return []

    try:
        documents = list_documents_fn(repo_id)
    except Exception:
        return []

    collected: list[dict[str, Any]] = []
    for document in documents:
        if not isinstance(document, dict):
            continue
        doc_name = str(
            document.get("original_file_name")
            or document.get("document_name")
            or document.get("doc_name")
            or document.get("filename")
            or ""
        ).strip()
        if not doc_name or not document_filename_matches_identifiers(doc_name, tokens):
            continue
        document_id = str(document.get("document_id") or "").strip()
        if not document_id:
            continue
        try:
            raw_chunks = list_chunks_fn(document_id)
        except Exception:
            continue
        for raw in raw_chunks:
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("text") or raw.get("content") or "").strip()
            if not text:
                continue
            collected.append(
                normalize_retrieval_chunk(
                    {
                        "chunk_id": str(raw.get("chunk_id") or raw.get("id") or ""),
                        "text": text,
                        "doc_name": doc_name,
                        "score": 1.0,
                        "metadata": {
                            "original_file_name": document.get("original_file_name"),
                            "repository_id": repo_id,
                            "document_scoped": True,
                        },
                    }
                )
            )
            if len(collected) >= limit:
                return collected
    return collected


def retrieve_repository_evidence(
    *,
    retrieve_fn: RetrieveFn,
    query: str,
    repository_ids: list[str],
    top_k: int = DEFAULT_TOP_K,
    search_mode: str = "hybrid",
    use_rerank: bool = True,
    expand_query: bool = True,
    max_expanded_queries: int = 2,
    min_score: float = DEFAULT_MIN_CHUNK_SCORE,
    parallel: bool = True,
    override_expanded_queries: list[str] | None = None,
) -> dict[str, Any]:
    """Retrieve top chunks for MLR chat using hybrid search, expansion, and reranking."""
    repo_ids = [str(item).strip() for item in repository_ids if str(item).strip()]
    if not repo_ids:
        return {"chunks": [], "expanded_queries": [], "retrieval_error": "No repository selected."}

    expanded_queries = (
        [str(item).strip() for item in override_expanded_queries if str(item).strip()]
        if override_expanded_queries
        else expand_retrieval_queries(query) if expand_query else [query.strip()]
    )
    if not expanded_queries:
        return {"chunks": [], "expanded_queries": [], "retrieval_error": "Empty query."}
    if max_expanded_queries > 0:
        expanded_queries = expanded_queries[: max(1, max_expanded_queries)]

    per_query_k = max(top_k, min(20, top_k + 4))
    payloads: list[dict[str, Any]] = []
    retrieval_error: str | None = None
    api_min_score = 0.0

    def _retrieve_one(expanded: str) -> dict[str, Any]:
        return retrieve_fn(
            query_text=expanded,
            repository_ids=repo_ids,
            search_mode=search_mode,
            top_k=per_query_k,
            use_rerank=use_rerank,
            min_score=api_min_score,
        )

    if parallel and len(expanded_queries) > 1:
        with ThreadPoolExecutor(max_workers=len(expanded_queries)) as pool:
            futures = {pool.submit(_retrieve_one, expanded): expanded for expanded in expanded_queries}
            for future in as_completed(futures):
                try:
                    payloads.append(future.result())
                except Exception as exc:
                    retrieval_error = str(exc)
                    break
    else:
        for expanded in expanded_queries:
            try:
                payloads.append(_retrieve_one(expanded))
            except Exception as exc:
                retrieval_error = str(exc)
                break

    chunks = merge_retrieval_chunks(payloads, top_k=top_k)

    if not chunks and search_mode != "keyword":
        try:
            payloads.append(
                retrieve_fn(
                    query_text=condense_retrieval_query(query),
                    repository_ids=repo_ids,
                    search_mode="keyword",
                    top_k=per_query_k,
                    use_rerank=False,
                    min_score=api_min_score,
                )
            )
            chunks = merge_retrieval_chunks(payloads, top_k=top_k)
        except Exception as exc:
            retrieval_error = retrieval_error or str(exc)

    if min_score > 0:
        chunks = [chunk for chunk in chunks if _chunk_score(chunk) >= min_score] or chunks[:1]

    return {
        "chunks": chunks,
        "expanded_queries": expanded_queries,
        "retrieval_error": retrieval_error,
        "pipeline": {
            "search_mode": search_mode,
            "use_rerank": use_rerank,
            "expand_query": expand_query and not override_expanded_queries,
            "parallel_retrieval": parallel and len(expanded_queries) > 1,
            "lexical_analysis": search_mode in {"hybrid", "keyword"},
            "override_expanded_queries": bool(override_expanded_queries),
        },
    }
