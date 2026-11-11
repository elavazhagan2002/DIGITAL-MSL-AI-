"""Medical Literature Research Agent orchestration (repository-scoped RAG + LLM)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from connectors.chat import (
    build_greeting_response,
    build_repository_refusal_response,
    citations_for_display,
    generate_mlr_chat_response,
    resolve_answer_citations,
    suggest_follow_up_questions,
)
from connectors.conversation_context import (
    should_allow_history_fallback,
    trim_conversation_history,
)
from connectors.repository_catalog import (
    build_repository_catalog_answer,
    fetch_repository_catalog,
)
from connectors.config import load_chat_retrieval_settings
from connectors.intent_router import ChatTurnPlan, extract_acronym_tokens, plan_chat_turn
from connectors.retrieval import (
    RetrieveFn,
    chunks_have_repository_evidence,
    fetch_document_scoped_chunks,
    merge_retrieval_chunks,
    prioritize_acronym_expansion_chunks,
    resolve_chat_retrieval_queries,
    retrieve_repository_evidence,
)

ChunksFallbackFn = Callable[..., list[dict[str, Any]]]
ListRepositoryDocumentsFn = Callable[[str], dict[str, Any] | list[dict[str, Any]]]
ListDocumentChunksFn = Callable[[str], list[dict[str, Any]]]


@dataclass
class PreparedGeneralChatTurn:
    query: str
    repository_names: list[str]
    history: list[dict[str, str]]
    pipeline: dict[str, Any]
    response_intent: str
    chunks: list[dict[str, Any]] = field(default_factory=list)
    allow_history_only: bool = False
    instant_result: dict[str, Any] | None = None


def _repository_documents(
    list_documents_fn: ListRepositoryDocumentsFn,
    repository_id: str,
) -> list[dict[str, Any]]:
    payload = list_documents_fn(repository_id)
    if isinstance(payload, dict):
        documents = payload.get("documents") or []
        return [document for document in documents if isinstance(document, dict)]
    return [document for document in payload if isinstance(document, dict)]


def _run_retrieval_for_plan(
    *,
    plan: ChatTurnPlan,
    user_message: str,
    conversation_history: list[dict[str, Any]] | None,
    retrieve_fn: RetrieveFn,
    repository_ids: list[str],
    response_intent: str,
    chunks_fallback_fn: ChunksFallbackFn | None,
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    retrieval_settings = load_chat_retrieval_settings()
    pipeline: dict[str, Any] = {}
    queries = [str(item).strip() for item in plan.retrieval_queries if str(item).strip()]
    if not queries and not str(user_message or "").strip():
        return [], pipeline, ""

    acronym_mode = plan.backend_action == "acronym_definition"
    resolved = resolve_chat_retrieval_queries(
        plan_queries=queries,
        user_message=user_message,
        conversation_history=conversation_history,
        backend_action=plan.backend_action,
        router_source=plan.router_source,
        single_retrieve_call=retrieval_settings.single_retrieve_call,
        max_expanded_queries=retrieval_settings.max_expanded_queries,
    )
    retrieval_query = str(resolved.get("primary_query") or "").strip()
    if not retrieval_query:
        return [], pipeline, ""

    pipeline["candidate_queries"] = list(resolved.get("candidate_queries") or [])
    pipeline["single_retrieve_call"] = bool(resolved.get("single_retrieve_call"))

    retrieval = retrieve_repository_evidence(
        retrieve_fn=retrieve_fn,
        query=retrieval_query,
        repository_ids=repository_ids,
        top_k=(
            max(retrieval_settings.initial_top_k, 12)
            if acronym_mode
            else retrieval_settings.detail_top_k
            if response_intent == "detail"
            else retrieval_settings.initial_top_k
        ),
        search_mode="hybrid",
        use_rerank=retrieval_settings.use_rerank,
        expand_query=bool(resolved.get("expand_query")),
        max_expanded_queries=int(resolved.get("max_expanded_queries") or 1),
        parallel=retrieval_settings.parallel_retrieval,
        override_expanded_queries=resolved.get("override_expanded_queries"),
    )

    retrieval_error = retrieval.get("retrieval_error")
    if retrieval_error:
        pipeline.update(dict(retrieval.get("pipeline") or {}))
        pipeline["retrieval_error"] = retrieval_error
        return [], pipeline, retrieval_query

    chunks = list(retrieval.get("chunks") or [])
    if acronym_mode:
        chunks = prioritize_acronym_expansion_chunks(
            chunks,
            extract_acronym_tokens(user_message, conversation_history),
        )
    pipeline.update(dict(retrieval.get("pipeline") or {}))
    pipeline["expanded_queries"] = list(retrieval.get("expanded_queries") or [])
    pipeline["retrieval_query"] = retrieval_query
    if acronym_mode:
        pipeline["acronym_retrieval"] = True

    if not chunks_have_repository_evidence(chunks) and chunks_fallback_fn is not None:
        fallback_chunks: list[dict[str, Any]] = []
        for repo_id in repository_ids:
            try:
                fallback_chunks.extend(
                    chunks_fallback_fn(
                        repository_id=repo_id,
                        query=retrieval_query,
                        limit=retrieval_settings.initial_top_k,
                    )
                )
            except Exception:
                continue
        if fallback_chunks:
            chunks = fallback_chunks[: retrieval_settings.initial_top_k]
            pipeline["fallback"] = "document_chunks"

    return chunks, pipeline, retrieval_query


def prepare_general_chat_turn(
    *,
    query: str,
    repository_ids: list[str],
    repository_names: list[str],
    conversation_history: list[dict[str, Any]] | None,
    retrieve_fn: RetrieveFn,
    chunks_fallback_fn: ChunksFallbackFn | None = None,
    list_documents_fn: ListRepositoryDocumentsFn | None = None,
    list_document_chunks_fn: ListDocumentChunksFn | None = None,
    use_llm_router: bool | None = None,
) -> PreparedGeneralChatTurn | dict[str, Any]:
    """Run routing and retrieval; return instant response or prepared streaming turn."""
    cleaned_query = str(query or "").strip()
    if not cleaned_query:
        return {
            "answer": "Please enter a question about documents in the selected repository.",
            "citations": [],
            "llm_used": False,
            "suggested_questions": [],
            "retrieval_pipeline": {},
        }

    repo_ids = [str(item).strip() for item in repository_ids if str(item).strip()]
    repo_names = [str(item).strip() for item in repository_names if str(item).strip()]
    scope_label = ", ".join(repo_names) if repo_names else "the selected repository"
    history = trim_conversation_history(conversation_history)

    if not repo_ids:
        return {
            "answer": "Select an active repository before I can answer.",
            "citations": [],
            "llm_used": False,
            "suggested_questions": [],
            "retrieval_pipeline": {},
        }

    turn_plan = plan_chat_turn(
        user_message=cleaned_query,
        conversation_history=conversation_history,
        repository_names=repo_names,
        use_llm_router=use_llm_router,
    )
    response_intent = turn_plan.response_mode
    pipeline: dict[str, Any] = turn_plan.to_pipeline_dict()

    if turn_plan.backend_action == "greeting":
        return {
            "answer": build_greeting_response(repo_names, user_message=cleaned_query),
            "citations": [],
            "llm_used": turn_plan.router_source == "llm",
            "suggested_questions": [
                "What documents are available in this repository?",
                "What clinical trial evidence is loaded here?",
            ],
            "retrieval_pipeline": pipeline,
        }

    if turn_plan.backend_action == "out_of_scope":
        return {
            "answer": build_repository_refusal_response(repo_names),
            "citations": [],
            "llm_used": turn_plan.router_source == "llm",
            "suggested_questions": suggest_follow_up_questions(
                cleaned_query,
                repository_names=repo_names,
                found_evidence=False,
            ),
            "retrieval_pipeline": pipeline,
        }

    if turn_plan.backend_action == "repository_catalog":
        if list_documents_fn is None:
            return {
                "answer": (
                    f"I can answer document-count and document-list questions for **{scope_label}**, "
                    "but document inventory is not wired in this environment."
                ),
                "citations": [],
                "llm_used": turn_plan.router_source == "llm",
                "suggested_questions": [
                    "What clinical trial evidence is loaded here?",
                    "Summarize the key findings across the loaded literature.",
                ],
                "retrieval_pipeline": pipeline,
            }
        catalogs: list[dict[str, Any]] = []
        for repo_id, repo_name in zip(repo_ids, repo_names or repo_ids):
            try:
                catalog = fetch_repository_catalog(
                    list_documents_fn=list_documents_fn,
                    repository_id=repo_id,
                )
                catalog["repository_name"] = repo_name
                catalogs.append(catalog)
            except Exception:
                continue
        if not catalogs:
            return {
                "answer": (
                    f"I could not load the document inventory for **{scope_label}**. "
                    "Check that the DMS API is reachable and the repository is active."
                ),
                "citations": [],
                "llm_used": turn_plan.router_source == "llm",
                "suggested_questions": suggest_follow_up_questions(
                    cleaned_query,
                    repository_names=repo_names,
                    found_evidence=False,
                ),
                "retrieval_pipeline": {**pipeline, "catalog_error": True},
            }
        return {
            "answer": build_repository_catalog_answer(
                cleaned_query,
                repository_names=repo_names,
                catalogs=catalogs,
            ),
            "citations": [],
            "llm_used": turn_plan.router_source == "llm",
            "suggested_questions": [
                "Summarize the key findings across the loaded literature.",
                "What clinical trial evidence is loaded here?",
            ],
            "retrieval_pipeline": {
                **pipeline,
                "document_count": sum(int(item.get("document_count") or 0) for item in catalogs),
            },
        }

    chunks: list[dict[str, Any]] = []
    document_identifiers = list(turn_plan.document_identifiers)
    if document_identifiers:
        pipeline["document_identifiers"] = document_identifiers

    if (
        turn_plan.backend_action in {"document_scoped", "acronym_definition"}
        and document_identifiers
        and list_documents_fn is not None
        and list_document_chunks_fn is not None
    ):
        scoped_chunks: list[dict[str, Any]] = []
        retrieval_settings = load_chat_retrieval_settings()
        scoped_limit = max(retrieval_settings.detail_top_k * 3, 24)
        for repo_id in repo_ids:
            try:
                scoped_chunks.extend(
                    fetch_document_scoped_chunks(
                        list_documents_fn=lambda rid, fn=list_documents_fn: _repository_documents(
                            fn, rid
                        ),
                        list_chunks_fn=list_document_chunks_fn,
                        repository_id=repo_id,
                        identifiers=document_identifiers,
                        limit=scoped_limit,
                    )
                )
            except Exception:
                continue
        if scoped_chunks:
            chunks = scoped_chunks[:scoped_limit]
            pipeline["document_scoped"] = True

    run_retrieval = not turn_plan.skip_retrieval and turn_plan.backend_action in {
        "document_search",
        "acronym_definition",
        "document_scoped",
    }
    pipeline["skipped_retrieval"] = not run_retrieval

    if run_retrieval:
        scoped_chunks = list(chunks)
        retrieved, retrieval_pipeline, retrieval_query = _run_retrieval_for_plan(
            plan=turn_plan,
            user_message=cleaned_query,
            conversation_history=conversation_history,
            retrieve_fn=retrieve_fn,
            repository_ids=repo_ids,
            response_intent=response_intent,
            chunks_fallback_fn=chunks_fallback_fn,
        )
        pipeline.update(retrieval_pipeline)
        if retrieval_pipeline.get("retrieval_error"):
            return {
                "answer": (
                    f"Retrieval failed: {retrieval_pipeline['retrieval_error']}\n\n"
                    "If this environment shows `retrieval_disabled`, enable retrieval in platform config "
                    "or restart the DMS service with `features.enable_retrieval=true`."
                ),
                "citations": [],
                "llm_used": False,
                "suggested_questions": [],
                "retrieval_pipeline": pipeline,
            }

        if scoped_chunks and retrieved:
            retrieval_settings = load_chat_retrieval_settings()
            merge_top_k = max(
                retrieval_settings.detail_top_k if response_intent == "detail" else retrieval_settings.initial_top_k,
                len(scoped_chunks),
            )
            chunks = merge_retrieval_chunks(
                [{"chunks": scoped_chunks}, {"chunks": retrieved}],
                top_k=merge_top_k,
            )
            pipeline["merged_scoped_and_retrieved"] = True
            pipeline["use_rerank"] = retrieval_pipeline.get("use_rerank", retrieval_settings.use_rerank)
        elif retrieved:
            chunks = retrieved
        else:
            chunks = scoped_chunks

        if not chunks_have_repository_evidence(chunks):
            allow_history = turn_plan.use_history_only or should_allow_history_fallback(
                cleaned_query, conversation_history
            )
            if allow_history and history:
                pipeline["fallback"] = "conversation_history"
                pipeline["retrieval_empty"] = True
                chunks = []
            else:
                return {
                    "answer": build_repository_refusal_response(repo_names),
                    "citations": [],
                    "llm_used": False,
                    "suggested_questions": suggest_follow_up_questions(
                        cleaned_query,
                        repository_names=repo_names,
                        found_evidence=False,
                    ),
                    "retrieval_pipeline": pipeline,
                }

    allow_history_only = turn_plan.use_history_only or (
        (not run_retrieval or not chunks_have_repository_evidence(chunks))
        and should_allow_history_fallback(cleaned_query, conversation_history)
    )

    return PreparedGeneralChatTurn(
        query=cleaned_query,
        repository_names=repo_names,
        history=history,
        pipeline=pipeline,
        response_intent=response_intent,
        chunks=chunks,
        allow_history_only=allow_history_only,
    )


def finalize_general_chat_turn(prepared: PreparedGeneralChatTurn, answer: str) -> dict[str, Any]:
    """Resolve citations and follow-ups after the assistant answer is generated."""
    default_repo = prepared.repository_names[0] if prepared.repository_names else ""
    raw_citations = resolve_answer_citations(
        answer,
        prepared.chunks,
        default_repository_name=default_repo,
    )
    citations = citations_for_display(answer, raw_citations)
    if prepared.response_intent in {"greeting", "repository_catalog"}:
        citations = []
    suggested = suggest_follow_up_questions(
        prepared.query,
        repository_names=prepared.repository_names,
        found_evidence=bool(prepared.chunks),
        answer=answer,
    )
    pipeline = dict(prepared.pipeline)
    pipeline["response_intent"] = prepared.response_intent
    return {
        "answer": answer,
        "citations": citations,
        "llm_used": True,
        "suggested_questions": suggested,
        "retrieval_pipeline": pipeline,
    }


def process_general_chat_message(
    *,
    query: str,
    repository_ids: list[str],
    repository_names: list[str],
    conversation_history: list[dict[str, Any]] | None,
    retrieve_fn: RetrieveFn,
    chunks_fallback_fn: ChunksFallbackFn | None = None,
    list_documents_fn: ListRepositoryDocumentsFn | None = None,
    list_document_chunks_fn: ListDocumentChunksFn | None = None,
    use_llm_router: bool | None = None,
) -> dict[str, Any]:
    """Run one Medical Literature Research Agent turn."""
    prepared = prepare_general_chat_turn(
        query=query,
        repository_ids=repository_ids,
        repository_names=repository_names,
        conversation_history=conversation_history,
        retrieve_fn=retrieve_fn,
        chunks_fallback_fn=chunks_fallback_fn,
        list_documents_fn=list_documents_fn,
        list_document_chunks_fn=list_document_chunks_fn,
        use_llm_router=use_llm_router,
    )
    if isinstance(prepared, dict):
        return prepared

    response = generate_mlr_chat_response(
        query=prepared.query,
        chunks=prepared.chunks,
        conversation_history=prepared.history,
        repository_names=prepared.repository_names,
        allow_history_only=prepared.allow_history_only,
        response_mode=prepared.response_intent,
    )
    result = finalize_general_chat_turn(prepared, str(response.get("answer") or ""))
    result["llm_used"] = bool(response.get("llm_used"))
    return result
