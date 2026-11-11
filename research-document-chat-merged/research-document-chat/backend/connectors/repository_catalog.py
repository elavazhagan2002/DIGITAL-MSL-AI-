"""Repository inventory answers (document count / list) for Medical Literacy Research chat."""

from __future__ import annotations

import re
from typing import Any, Callable

ListRepositoryDocumentsFn = Callable[[str], dict[str, Any] | list[dict[str, Any]]]

_REPOSITORY_CATALOG_RE = re.compile(
    r"\b("
    r"how\s+many\s+documents?|how\s+many\s+docs?|"
    r"document\s+count|number\s+of\s+documents?|count\s+of\s+documents?|"
    r"documents?\s+present|documents?\s+in\s+(?:this|the)\s+repo(?:sitory)?|"
    r"list\s+(?:all\s+)?(?:the\s+)?documents?|show\s+(?:all\s+)?(?:the\s+)?documents?|"
    r"what\s+documents?\s+(?:are\s+)?(?:available|loaded|present|in\s+(?:this|the)\s+repo(?:sitory)?)|"
    r"which\s+documents?\s+(?:are\s+)?(?:available|loaded|present)|"
    r"documents?\s+(?:are\s+)?(?:available|loaded)|"
    r"what\s+is\s+loaded\s+in\s+(?:this|the)\s+repo(?:sitory)?|"
    r"all\s+docs?\s+from\s+(?:this|the)\s+repo(?:sitory)?|"
    r"give\s+(?:me\s+)?(?:all\s+)?(?:the\s+)?documents?|"
    r"show\s+(?:me\s+)?(?:all\s+)?(?:the\s+)?docs?"
    r")\b",
    re.I,
)

_LIST_ALL_RE = re.compile(
    r"\b(list\s+(?:them|all)|show\s+(?:them|all)|name\s+(?:them|all)|what\s+are\s+they)\b",
    re.I,
)


def is_repository_catalog_query(
    user_message: str,
    conversation_history: list[dict[str, Any]] | None = None,
) -> bool:
    text = str(user_message or "").strip()
    if not text:
        return False
    if _REPOSITORY_CATALOG_RE.search(text):
        return True
    if _LIST_ALL_RE.search(text) and conversation_history:
        for message in reversed(conversation_history):
            if not isinstance(message, dict):
                continue
            if str(message.get("role") or "").strip().lower() != "user":
                continue
            prior = str(message.get("content") or "").strip()
            if prior and _REPOSITORY_CATALOG_RE.search(prior):
                return True
            break
    return False


def _document_display_name(document: dict[str, Any]) -> str:
    for key in ("original_file_name", "document_name", "doc_name", "filename", "name"):
        value = str(document.get(key) or "").strip()
        if value:
            return value
    return str(document.get("document_id") or "document")


def _normalize_catalog_payload(payload: dict[str, Any] | list[dict[str, Any]]) -> dict[str, Any]:
    if isinstance(payload, list):
        documents = [doc for doc in payload if isinstance(doc, dict)]
        return {
            "document_count": len(documents),
            "documents": documents,
            "returned": len(documents),
        }
    documents = [doc for doc in (payload.get("documents") or []) if isinstance(doc, dict)]
    count = payload.get("document_count")
    try:
        document_count = int(count) if count is not None else len(documents)
    except (TypeError, ValueError):
        document_count = len(documents)
    return {
        "document_count": document_count,
        "documents": documents,
        "returned": int(payload.get("returned") or len(documents)),
    }


def fetch_repository_catalog(
    *,
    list_documents_fn: ListRepositoryDocumentsFn,
    repository_id: str,
) -> dict[str, Any]:
    payload = list_documents_fn(str(repository_id or "").strip())
    return _normalize_catalog_payload(payload)


def build_repository_catalog_answer(
    query: str,
    *,
    repository_names: list[str],
    catalogs: list[dict[str, Any]],
) -> str:
    del query  # Count/list responses use catalog metadata only.
    if not catalogs:
        scope = ", ".join(repository_names) if repository_names else "the selected repository"
        return f"I could not load the document inventory for **{scope}**."

    lines: list[str] = []
    for index, catalog in enumerate(catalogs):
        repo_name = str(catalog.get("repository_name") or "").strip()
        if not repo_name and index < len(repository_names):
            repo_name = str(repository_names[index] or "").strip()
        repo_label = repo_name or "repository"
        total = int(catalog.get("document_count") or 0)
        documents = [doc for doc in (catalog.get("documents") or []) if isinstance(doc, dict)]
        returned = int(catalog.get("returned") or len(documents))

        if total == 0:
            lines.append(f"**{repo_label}** has **0** documents loaded.")
            continue

        lines.append(f"**{repo_label}** has **{total}** document(s) loaded.")
        if returned < total:
            lines.append(f"Showing **{returned}** of **{total}** document names below.")

        if documents:
            lines.append("")
            lines.append("**Documents:**")
            for doc_index, document in enumerate(documents, 1):
                name = _document_display_name(document)
                status = str(document.get("status") or "").strip()
                suffix = f" ({status})" if status and status.lower() not in {"completed", "ready", "processed"} else ""
                lines.append(f"{doc_index}. {name}{suffix}")
        elif total > 0:
            lines.append("")
            lines.append(
                "Documents are linked to this repository, but filenames are not available in the catalog response."
            )

        if index < len(catalogs) - 1:
            lines.append("")

    return "\n".join(lines).strip()
