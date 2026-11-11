"""Thin DMS retrieval wrapper for chat agents."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.dms_client import (
    DmsClient,
    DmsError,
    _is_internal_storage_filename,
    resolve_portal_document_filename,
)
from core.config import get_settings

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class _LocalRerankerService:
    """Backend chunk reranker with cross-encoder support and lexical fallback."""

    def __init__(self) -> None:
        settings = get_settings()
        self._enabled = bool(settings.enable_reranking)
        self._model_name = settings.reranker_model
        self._model = None
        self._load_attempted = False

    def rerank(self, *, query: str, documents: list[str], top_k: int = 10) -> list[tuple[int, float]]:
        candidates = [str(document or "") for document in documents]
        if not candidates:
            return []

        scores = self._cross_encoder_scores(query, candidates) if self._enabled else None
        if scores is None:
            scores = [self._lexical_score(query, document) for document in candidates]

        ranked = sorted(enumerate(scores), key=lambda item: item[1], reverse=True)
        return ranked[: max(1, min(int(top_k or 10), len(ranked)))]

    def _cross_encoder_scores(self, query: str, documents: list[str]) -> list[float] | None:
        model = self._load_cross_encoder()
        if model is None:
            return None
        try:
            pairs = [(query, document) for document in documents]
            raw_scores = model.predict(pairs)
            return [float(score) for score in raw_scores]
        except Exception as exc:
            logger.warning("Cross-encoder reranking failed; using lexical fallback: %s", exc)
            return None

    def _load_cross_encoder(self):
        if not self._enabled:
            return None
        if self._model is not None:
            return self._model
        if self._load_attempted:
            return None
        self._load_attempted = True
        try:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self._model_name)
            return self._model
        except Exception as exc:
            logger.warning(
                "Cross-encoder reranker unavailable (%s); using lexical fallback",
                exc,
            )
            return None

    @staticmethod
    def _lexical_score(query: str, document: str) -> float:
        query_tokens = _meaningful_tokens(query)
        document_tokens = _meaningful_tokens(document)
        if not query_tokens or not document_tokens:
            return 0.0
        query_set = set(query_tokens)
        document_set = set(document_tokens)
        overlap = len(query_set & document_set)
        coverage = overlap / max(len(query_set), 1)
        density = overlap / max(len(document_set), 1)
        phrase_bonus = sum(1 for token in query_set if token in document_set) / max(len(query_set), 1)
        return round((coverage * 0.65) + (density * 0.25) + (phrase_bonus * 0.10), 6)


def _meaningful_tokens(text: str) -> list[str]:
    stopwords = {
        "a", "an", "and", "are", "as", "be", "by", "for", "from", "in", "into",
        "is", "it", "of", "on", "or", "shall", "should", "the", "to", "with",
    }
    return [
        token.lower()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", str(text or ""))
        if token.lower() not in stopwords
    ]


def format_citation_label(
    repository_name: str,
    document_name: str,
    page_number: int | None,
) -> str:
    """Build citation key as repo_name:document_name:page_no."""
    repo = (repository_name or "Repository").strip()
    doc = (document_name or "Document").strip()
    try:
        page = int(page_number or 0)
    except (TypeError, ValueError):
        page = 0
    if page > 0:
        return f"{repo}:{doc}:{page}"
    return f"{repo}:{doc}:—"


def _infer_document_id(*, stored_name: str = "", file_id: str | None = None) -> str:
    """Derive document UUID from Weaviate doc_name or explicit file_id."""
    explicit = str(file_id or "").strip()
    if explicit:
        return explicit
    stored = str(stored_name or "").strip()
    if not stored:
        return ""
    if _UUID_RE.match(stored):
        return stored
    stem = Path(stored).stem
    if _UUID_RE.match(stem):
        return stem
    return ""


def looks_like_document_id(value: str) -> bool:
    token = str(value or "").strip()
    if not token:
        return False
    if _UUID_RE.match(token):
        return True
    stem = Path(token).stem
    return bool(_UUID_RE.match(stem))


class _DocumentCatalog:
    """Lazy DMS catalog lookup: stored doc_name / document_id → display name + repository."""

    def __init__(self, client: DmsClient) -> None:
        self._client = client
        self._by_stored_name: dict[str, dict[str, str]] = {}
        self._by_document_id: dict[str, dict[str, str]] = {}
        self._by_stored_stem: dict[str, dict[str, str]] = {}
        # DMS retrieve often returns original/display filenames with null document_id
        self._by_display_name: dict[str, dict[str, str]] = {}
        self._repo_id_to_name: dict[str, str] = {}
        self._warmed = False

    def warm(self) -> None:
        if self._warmed:
            return
        try:
            repos_payload = self._client.list_repositories()
            repositories = repos_payload.get("repositories") if isinstance(repos_payload, dict) else []
            if not isinstance(repositories, list):
                repositories = []

            repo_ids: list[str | None] = []
            for repo in repositories:
                rid = str(repo.get("repository_id") or "").strip()
                rname = str(repo.get("name") or "Repository").strip()
                if rid:
                    self._repo_id_to_name[rid] = rname
                    repo_ids.append(rid)
            if not repo_ids:
                repo_ids = [None]

            for rid in repo_ids:
                rname = self._repo_id_to_name.get(rid or "", "Repository") if rid else "Repository"
                if rid:
                    try:
                        repo_payload = self._client.list_repository_documents(rid, limit=500)
                        for doc in repo_payload.get("documents") or []:
                            self._index_document(doc, default_repo_name=rname)
                    except DmsError as exc:
                        logger.debug("Repository document catalog warm failed for %s: %s", rid, exc)
                try:
                    payload = self._client.list_retrieve_documents(
                        repository_id=rid,
                        limit=500,
                        offset=0,
                    )
                    for doc in payload.get("documents") or []:
                        self._index_document(doc, default_repo_name=rname)
                except DmsError as exc:
                    logger.debug("Retrieve document catalog warm failed for %s: %s", rid, exc)
        except DmsError as exc:
            logger.warning("Document catalog warm failed: %s", exc)
        self._warmed = True

    def _cache_entry(self, entry: dict[str, str]) -> None:
        document_id = str(entry.get("document_id") or "").strip()
        stored_name = str(entry.get("stored_name") or "").strip()
        display_name = str(entry.get("display_name") or "").strip()
        if document_id:
            self._by_document_id[document_id] = entry
        if stored_name:
            self._by_stored_name[stored_name] = entry
            stem = Path(stored_name).stem
            if stem:
                self._by_stored_stem[stem] = entry
        if display_name and not _is_internal_storage_filename(display_name, document_id):
            self._by_display_name[display_name] = entry
            self._by_display_name[display_name.lower()] = entry
            stem = Path(display_name).stem
            if stem:
                self._by_display_name[stem.lower()] = entry

    def _fetch_document_entry(
        self,
        document_id: str,
        *,
        repository_id: str | None = None,
        repository_name: str | None = None,
    ) -> dict[str, str] | None:
        """Lazy-load display filename for a document id (catalog miss)."""
        doc_id = str(document_id or "").strip()
        if not doc_id:
            return None

        repo_name = str(repository_name or "").strip()
        repo_id = str(repository_id or "").strip()

        try:
            info = self._client.get_document_info(doc_id)
            if isinstance(info, dict):
                display = str(info.get("original_file_name") or "").strip()
                stored = str(info.get("document_name") or "").strip()
                repo_id = repo_id or str(info.get("repository_id") or "").strip()
                if display and not _is_internal_storage_filename(display, doc_id):
                    if not repo_name and repo_id:
                        repo_name = self._repo_id_to_name.get(repo_id, "")
                    entry = {
                        "document_id": doc_id,
                        "display_name": display,
                        "stored_name": stored,
                        "repository_id": repo_id,
                        "repository_name": repo_name or "Repository",
                    }
                    self._cache_entry(entry)
                    return entry
        except DmsError:
            pass

        if repo_id:
            try:
                payload = self._client.list_repository_documents(repo_id, limit=500)
                for doc in payload.get("documents") or []:
                    if str(doc.get("document_id") or "") == doc_id:
                        self._index_document(doc, default_repo_name=repo_name or "Repository")
                        return self._by_document_id.get(doc_id)
            except DmsError:
                pass

        for rid, rname in self._repo_id_to_name.items():
            if repo_id and rid != repo_id:
                continue
            try:
                payload = self._client.list_repository_documents(rid, limit=500)
            except DmsError:
                continue
            for doc in payload.get("documents") or []:
                if str(doc.get("document_id") or "") == doc_id:
                    self._index_document(doc, default_repo_name=rname)
                    return self._by_document_id.get(doc_id)
        return None

    def _index_document(self, doc: dict[str, Any], *, default_repo_name: str) -> None:
        document_id = str(doc.get("document_id") or doc.get("documentId") or "").strip()
        stored_name = str(doc.get("document_name") or doc.get("documentName") or "").strip()
        repository_id = str(doc.get("repository_id") or doc.get("repositoryId") or "").strip()
        repository_name = (
            str(doc.get("repository_name") or doc.get("repositoryName") or "").strip()
            or self._repo_id_to_name.get(repository_id, "")
            or default_repo_name
        )
        display_name = resolve_portal_document_filename(doc)
        entry = {
            "document_id": document_id,
            "display_name": display_name,
            "stored_name": stored_name,
            "repository_id": repository_id,
            "repository_name": repository_name,
        }
        if document_id:
            self._by_document_id[document_id] = entry
        if stored_name:
            self._by_stored_name[stored_name] = entry
            stem = Path(stored_name).stem
            if stem:
                self._by_stored_stem[stem] = entry
        if document_id:
            self._by_stored_stem[document_id] = entry
        if display_name and not _is_internal_storage_filename(display_name, document_id):
            self._by_display_name[display_name] = entry
            self._by_display_name[display_name.lower()] = entry
            stem = Path(display_name).stem
            if stem:
                self._by_display_name[stem.lower()] = entry

    def resolve(
        self,
        *,
        stored_name: str = "",
        document_id: str | None = None,
        repository_name: str | None = None,
        repository_id: str | None = None,
    ) -> dict[str, str]:
        self.warm()
        entry: dict[str, str] | None = None
        doc_id = _infer_document_id(stored_name=stored_name, file_id=document_id)
        stored = str(stored_name or "").strip()

        if doc_id and doc_id in self._by_document_id:
            entry = self._by_document_id[doc_id]
        elif stored and stored in self._by_stored_name:
            entry = self._by_stored_name[stored]
        elif stored and stored in self._by_display_name:
            entry = self._by_display_name[stored]
        elif stored and stored.lower() in self._by_display_name:
            entry = self._by_display_name[stored.lower()]
        elif stored:
            stem = Path(stored).stem
            if stem and stem in self._by_stored_stem:
                entry = self._by_stored_stem[stem]
            elif stem and stem.lower() in self._by_display_name:
                entry = self._by_display_name[stem.lower()]
        elif doc_id and doc_id in self._by_stored_stem:
            entry = self._by_stored_stem[doc_id]

        display_name = stored or "Document"
        repo_name = str(repository_name or "").strip()
        resolved_doc_id = doc_id
        resolved_repo_id = str(repository_id or "").strip()

        if entry:
            display_name = entry.get("display_name") or display_name
            repo_name = entry.get("repository_name") or repo_name
            resolved_doc_id = entry.get("document_id") or resolved_doc_id
            resolved_repo_id = entry.get("repository_id") or resolved_repo_id

        if not repo_name and resolved_repo_id:
            repo_name = self._repo_id_to_name.get(resolved_repo_id, "")

        if _is_internal_storage_filename(display_name, resolved_doc_id):
            fetched = self._fetch_document_entry(
                resolved_doc_id,
                repository_id=resolved_repo_id or repository_id,
                repository_name=repo_name or repository_name,
            )
            if fetched:
                display_name = fetched.get("display_name") or display_name
                repo_name = fetched.get("repository_name") or repo_name
                resolved_doc_id = fetched.get("document_id") or resolved_doc_id
                resolved_repo_id = fetched.get("repository_id") or resolved_repo_id

        if _is_internal_storage_filename(display_name, resolved_doc_id):
            if resolved_doc_id:
                short_id = resolved_doc_id[:8]
                display_name = f"Document ({short_id}…)"
            else:
                display_name = "Document"

        return {
            "display_name": display_name,
            "repository_name": repo_name or "Repository",
            "document_id": resolved_doc_id,
            "repository_id": resolved_repo_id,
        }


class RetrievalService:
    """Pass-through retrieval via DMS /v1/retrieve and /v1/documents/{id}/chunks."""

    def __init__(self) -> None:
        self._client = DmsClient()
        self._catalog = _DocumentCatalog(self._client)
        self._reranker_service: _LocalRerankerService | None = None

    @property
    def reranker_service(self):
        """Return backend reranker for agent-level chunk reranking."""
        if self._reranker_service is None:
            self._reranker_service = _LocalRerankerService()
        return self._reranker_service

    def retrieve_chunks(
        self,
        query: str,
        top_k: int = 10,
        file_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        min_score: Optional[float] = None,
        use_hybrid: bool = True,
        use_reranking: bool = True,
        retrieval_limit: Optional[int] = None,
        alternative_queries: Optional[List[str]] = None,
        alpha: Optional[float] = None,
        collection_name: str = "Document",
        embedding_model: Optional[str] = None,
        repository_id: Optional[str] = None,
        repository_name: Optional[str] = None,
        model_dir: Optional[str] = None,
    ) -> List[Dict]:
        _ = use_reranking, retrieval_limit, alternative_queries, collection_name
        try:
            response = self._client.retrieve(
                query=query,
                top_k=top_k,
                document_id=file_id,
                tenant_id=workspace_id,
                repository_id=repository_id,
                search_mode="hybrid" if use_hybrid else "vector",
                min_score=min_score,
                model_name=embedding_model,
                model_dir=model_dir,
            )
        except DmsError as exc:
            logger.warning("DMS retrieve failed: %s", exc)
            return []
        return [
            _format_chunk(
                chunk,
                file_id=file_id,
                repository_id=repository_id,
                repository_name=repository_name,
                catalog=self._catalog,
            )
            for chunk in response.get("results") or []
        ]

    def retrieve_chunks_scoped(
        self,
        query: str,
        session_context: Optional[Dict] = None,
        *,
        top_k: int = 10,
        file_id: Optional[str] = None,
        use_hybrid: bool = True,
        use_reranking: bool = True,
        alternative_queries: Optional[List[str]] = None,
        min_score: Optional[float] = None,
        alpha: Optional[float] = None,
    ) -> List[Dict]:
        """Retrieve chunks scoped to selected repository or all accessible repositories."""
        from utils.repository_search import get_repository_search_targets, session_repository_id

        ctx = session_context or {}
        workspace_id = ctx.get("workspace_id")
        selected_repo_id = session_repository_id(ctx)
        _ = alternative_queries, alpha

        if file_id:
            return self.retrieve_chunks(
                query=query,
                top_k=top_k,
                file_id=file_id,
                workspace_id=workspace_id,
                repository_id=selected_repo_id or None,
                use_hybrid=use_hybrid,
                use_reranking=use_reranking,
                min_score=min_score,
            )

        if not selected_repo_id:
            try:
                response = self._client.retrieve(
                    query=query,
                    top_k=top_k,
                    tenant_id=workspace_id,
                    search_mode="hybrid" if use_hybrid else "vector",
                    min_score=min_score,
                    search_all_repositories=True,
                )
                raw_results = response.get("results") or []
                if raw_results:
                    return [
                        _format_chunk(chunk, catalog=self._catalog)
                        for chunk in raw_results
                    ]
                logger.info(
                    "DMS search_all_repositories returned 0 chunks for %r; "
                    "falling back to per-repository fan-out",
                    query[:80],
                )
            except DmsError as exc:
                logger.debug("DMS search_all_repositories failed, falling back to fan-out: %s", exc)

        targets = get_repository_search_targets(ctx)
        if not targets:
            if selected_repo_id:
                logger.info(
                    "No repository targets resolved for %s; attempting direct DMS retrieve",
                    selected_repo_id,
                )
                return self.retrieve_chunks(
                    query=query,
                    top_k=top_k,
                    workspace_id=workspace_id,
                    repository_id=selected_repo_id,
                    use_hybrid=use_hybrid,
                    use_reranking=use_reranking,
                    min_score=min_score,
                )
            return self.retrieve_chunks(
                query=query,
                top_k=top_k,
                workspace_id=workspace_id,
                use_hybrid=use_hybrid,
                use_reranking=use_reranking,
                min_score=min_score,
            )

        per_repo_top_k = max(5, min(top_k, 60 // max(len(targets), 1)))
        all_candidates: list[Dict] = []
        for target in targets:
            repo_chunks = self.retrieve_chunks(
                query=query,
                top_k=per_repo_top_k,
                workspace_id=target.get("workspace_id") or workspace_id,
                repository_id=target.get("repository_id"),
                repository_name=target.get("repository_name"),
                use_hybrid=use_hybrid,
                use_reranking=use_reranking,
                embedding_model=target.get("embedding_model"),
                model_dir=target.get("embedding_model_dir"),
                min_score=min_score,
            )
            all_candidates.extend(repo_chunks)

        all_candidates.sort(key=lambda chunk: chunk.get("similarity_score", 0) or 0, reverse=True)
        return all_candidates[:top_k]

    def retrieve_with_context(
        self,
        query: str,
        top_k: int = 5,
        context_chunks: int = 1,
        file_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        use_hybrid: bool = True,
        use_reranking: bool = True,
        alpha: Optional[float] = None,
        collection_name: str = "Document",
        embedding_model: Optional[str] = None,
        repository_id: Optional[str] = None,
        repository_name: Optional[str] = None,
        model_dir: Optional[str] = None,
    ) -> List[Dict]:
        results = self.retrieve_chunks(
            query=query,
            top_k=top_k,
            file_id=file_id,
            workspace_id=workspace_id,
            use_hybrid=use_hybrid,
            use_reranking=use_reranking,
            alpha=alpha,
            collection_name=collection_name,
            embedding_model=embedding_model,
            repository_id=repository_id,
            repository_name=repository_name,
            model_dir=model_dir,
        )
        if context_chunks == 0:
            return results
        return [{**result, "context_before": [], "context_after": []} for result in results]

    def get_document_chunks(
        self,
        *,
        file_id: str,
        workspace_id: Optional[str] = None,
        collection_name: str = "Document",
        limit: int = 500,
    ) -> List[Dict]:
        _ = workspace_id, collection_name
        try:
            response = self._client.get_document_chunks(file_id, limit=limit)
        except DmsError as exc:
            logger.warning("DMS get_document_chunks failed: %s", exc)
            return []
        chunks = response.get("chunks") if isinstance(response, dict) else response
        if not isinstance(chunks, list):
            return []
        return [
            _format_chunk(
                chunk,
                file_id=file_id,
                catalog=self._catalog,
                ordinal=i,
            )
            for i, chunk in enumerate(chunks)
        ]


def _first_present(*sources: dict[str, Any], keys: tuple[str, ...]) -> Any:
    """Return the first non-empty value across DMS response aliases."""
    for key in keys:
        for source in sources:
            if not isinstance(source, dict):
                continue
            value = source.get(key)
            if value not in (None, ""):
                return value
    return None


def _to_positive_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _format_chunk(
    chunk: dict[str, Any],
    *,
    file_id: Optional[str] = None,
    repository_id: Optional[str] = None,
    repository_name: Optional[str] = None,
    catalog: Optional[_DocumentCatalog] = None,
    ordinal: Optional[int] = None,
) -> Dict:
    metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
    properties = chunk.get("properties") if isinstance(chunk.get("properties"), dict) else {}
    stored_name = chunk.get("doc_name") or chunk.get("document_name") or metadata.get("source")
    page = _first_present(
        chunk,
        metadata,
        properties,
        keys=("page_number", "pageNumber", "page_no", "pageNo", "page"),
    )
    page_end = _first_present(
        chunk,
        metadata,
        properties,
        keys=("page_end", "pageEnd", "end_page", "endPage", "page_number", "pageNumber", "page"),
    )
    section_name = str(
        chunk.get("section_name")
        or metadata.get("section_name")
        or properties.get("section_name")
        or ""
    ).strip()
    line_start = chunk.get("line_start") or metadata.get("line_start") or metadata.get("line_number")
    line_end = chunk.get("line_end") or metadata.get("line_end") or metadata.get("line_end_number")
    score = chunk.get("score")
    metadata_doc_id = str(
        metadata.get("document_id") or metadata.get("file_id") or ""
    ).strip()
    metadata_display = str(
        metadata.get("original_file_name") or metadata.get("original_filename") or ""
    ).strip()
    resolved_file_id = _infer_document_id(
        stored_name=str(stored_name or ""),
        file_id=file_id or metadata_doc_id or None,
    )
    page_number = _to_positive_int(page)
    page_end_number = _to_positive_int(page_end) or page_number

    resolved = (
        catalog.resolve(
            stored_name=str(stored_name or ""),
            document_id=resolved_file_id or None,
            repository_name=repository_name,
            repository_id=repository_id,
        )
        if catalog
        else {
            "display_name": metadata_display
            or str(stored_name or "document"),
            "repository_name": str(repository_name or "Repository"),
            "document_id": str(resolved_file_id or ""),
            "repository_id": str(repository_id or ""),
        }
    )
    display_name = resolved["display_name"]
    if metadata_display and not _is_internal_storage_filename(metadata_display, resolved_file_id):
        display_name = metadata_display
    elif looks_like_document_id(display_name) and metadata_display:
        display_name = metadata_display
    repo_name = resolved["repository_name"]
    if not repo_name or repo_name == "Repository":
        meta_repo = str(metadata.get("repository_name") or "").strip()
        if meta_repo:
            repo_name = meta_repo
    citation_label = format_citation_label(repo_name, display_name, page_number)

    # Prefer numeric indexes only. DMS often returns UUID chunk_id/id — never
    # pass those through as chunk_index (downstream GQS code calls int()).
    raw_index = _first_present(
        chunk,
        metadata,
        properties,
        keys=("chunkIndex", "chunk_index", "index", "ordinal"),
    )
    try:
        chunk_index: Any = int(raw_index) if raw_index not in (None, "") else None
    except (TypeError, ValueError):
        chunk_index = None
    if chunk_index is None:
        chunk_index = int(ordinal) if ordinal is not None else 0

    return {
        "content": chunk.get("text") or chunk.get("content") or "",
        "file_id": resolved.get("document_id") or resolved_file_id,
        "file_name": display_name,
        "repository_name": repo_name,
        "repository_id": resolved.get("repository_id") or repository_id or "",
        "chunk_index": chunk_index,
        "total_chunks": chunk.get("total_chunks") or 0,
        "token_count": chunk.get("token_count") or 0,
        "source_type": "dms",
        "uploaded_at": chunk.get("uploaded_at") or "",
        "page_number": page_number,
        "page_end": page_end_number,
        "section_name": section_name,
        "line_start": line_start,
        "line_end": line_end,
        "similarity_score": score,
        "citation": citation_label,
        "citation_label": citation_label,
        "metadata": metadata,
    }
