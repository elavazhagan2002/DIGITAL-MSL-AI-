"""
Chat and RAG Routes for Standalone Research Document Chat.
Supports repository-scoped hybrid search retrieval, streaming SSE responses,
inline HTML citations, and message history persistence.
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
import uuid
from typing import Dict, List, Optional, Any

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from models.database import get_db
from models.models import User, ChatSession, ChatMessage
from routes.auth import get_current_user
from services.chat_history_service import ChatHistoryService
from services.retrieval_service import RetrievalService
from services.llm_service import LLMService
from agents.general_agent import GeneralAgent
from utils.citation_validator import CitationValidator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


def sse_event(event_type: str, data: dict) -> str:
    payload = _json.dumps({"type": event_type, **data}, ensure_ascii=False)
    return f"data: {payload}\n\n"


class SimpleChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    repository_id: str = Field(..., min_length=1)
    session_id: Optional[str] = None
    conversation_history: List[Dict[str, str]] = Field(default_factory=list)


@router.post("")
@router.post("/")
async def chat_multipart(
    message: Optional[str] = Form(None),
    conversation_history: str = Form("[]"),
    session_id: Optional[str] = Form(None),
    repository_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Primary chat endpoint compatible with the frontend Chat component.
    Receives form-data, performs repository RAG retrieval, and streams SSE tokens.
    """
    if not message:
        raise HTTPException(status_code=422, detail="Message must be provided.")

    try:
        conv_list = _json.loads(conversation_history) if conversation_history else []
    except Exception:
        conv_list = []

    return await _process_chat_stream(
        user_message=message,
        conversation_history=conv_list,
        session_id=session_id,
        repository_id=repository_id,
        db=db,
        current_user=current_user,
    )


@router.post("/simple")
async def chat_simple_json(
    body: SimpleChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """JSON-based chat endpoint for API consumers."""
    return await _process_chat_stream(
        user_message=body.message,
        conversation_history=body.conversation_history,
        session_id=body.session_id,
        repository_id=body.repository_id,
        db=db,
        current_user=current_user,
    )


async def _process_chat_stream(
    user_message: str,
    conversation_history: List[Dict[str, str]],
    session_id: Optional[str],
    repository_id: Optional[str],
    db: Session,
    current_user: User,
):
    chat_service = ChatHistoryService(db)
    active_session_id = session_id or str(uuid.uuid4())

    # Ensure session exists in database
    db_session = chat_service.get_session(active_session_id, user_id=current_user.user_id)
    if not db_session:
        db_session = chat_service.create_session(
            session_id=active_session_id,
            user_id=current_user.user_id,
            title=user_message[:60] if user_message else "Research Chat",
        )
        if repository_id:
            db_session.repository_id = repository_id
            db.commit()

    # Save user message
    chat_service.add_message(
        session_id=active_session_id,
        user_id=current_user.user_id,
        role="user",
        content=user_message,
        agent_type="general_chat",
    )

    llm = LLMService()
    retrieval = RetrievalService()
    general_agent = GeneralAgent(llm_service=llm, retrieval_service=retrieval)

    session_context = {
        "user_id": current_user.user_id,
        "user_role": current_user.role,
        "repository_id": repository_id or getattr(db_session, "repository_id", ""),
    }

    async def event_generator():
        yield sse_event("thinking", {"status": "Retrieving evidence from repository..."})
        
        full_response = []
        try:
            async for token in general_agent.process(
                user_message=user_message,
                conversation_history=conversation_history,
                session_context=session_context,
            ):
                full_response.append(token)
                yield sse_event("content", {"text": token})

            final_text = "".join(full_response)
            
            # Programmatic citation verification
            retrieved_chunks = getattr(general_agent, "_last_retrieved_chunks", []) or []
            validation = CitationValidator.validate_citations_against_chunks(
                final_text,
                retrieved_chunks,
                target_repository_id=repository_id
            )

            metadata = {
                "repository_id": repository_id,
                "retrieved_chunk_count": len(retrieved_chunks),
                "citations": validation["all_citations"],
                "verified_citations": validation["verified_citations"],
                "unverified_citations": validation["unverified_citations"],
            }

            chat_service.add_message(
                session_id=active_session_id,
                user_id=current_user.user_id,
                role="assistant",
                content=final_text,
                agent_type="general_chat",
                metadata=metadata,
            )

            yield sse_event("done", {
                "session_id": active_session_id,
                "citation_stats": {
                    "total": validation["citation_count"],
                    "verified": len(validation["verified_citations"]),
                    "unverified": len(validation["unverified_citations"])
                }
            })

        except Exception as exc:
            logger.exception("Error during chat generation: %s", exc)
            yield sse_event("error", {"detail": f"Generation failed: {str(exc)}"})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"X-Session-ID": active_session_id}
    )


@router.get("/sessions")
async def list_user_sessions(
    repository_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retrieve chat sessions for user."""
    chat_service = ChatHistoryService(db)
    sessions = chat_service.get_user_sessions(user_id=current_user.user_id)
    if repository_id:
        sessions = [s for s in sessions if getattr(s, "repository_id", None) == repository_id]
    return {"sessions": [s.to_dict() for s in sessions]}


@router.get("/session/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retrieve message history for a specific chat session."""
    chat_service = ChatHistoryService(db)
    messages = chat_service.get_session_messages(session_id, user_id=current_user.user_id)
    return {"messages": [m.to_dict() for m in messages]}
