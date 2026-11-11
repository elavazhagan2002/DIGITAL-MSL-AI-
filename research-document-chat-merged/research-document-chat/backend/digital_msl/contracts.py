"""Data contracts shared by the Digital MSL agents.

``InboundMessage`` is the canonical channel-independent request (architecture doc §4).
``ValidationPayload`` is the JSON handed to the validation engine; the engine returns
the same JSON with scores and a decision added.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Channel = Literal["web", "email", "whatsapp"]
MessageStatus = Literal["received", "processing", "answered", "escalated", "refused", "failed"]
Decision = Literal["respond", "escalate_to_msl", "refuse"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class InboundMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: new_id("msg"))
    idempotency_key: str = ""
    correlation_id: str = Field(default_factory=lambda: new_id("corr"))
    conversation_id: str = Field(default_factory=lambda: new_id("conv"))
    channel: Channel
    external_sender_id: str = ""
    sender_name: str = ""
    user_id: str = ""
    role: str = "doctor"
    assigned_msl_id: str = ""
    repository_id: str
    message_text: str
    subject: str = ""
    received_at: str = Field(default_factory=_now)
    locale: str = "en"
    consent_and_verification_state: str = "unverified"
    status: MessageStatus = "received"
    conversation_history: list[dict[str, str]] = Field(default_factory=list)
    session_id: str = ""
    notice_version: str = ""
    timezone: str = ""  # browser IANA zone, for the audit trail
    client_utc_offset_minutes: Optional[int] = None  # browser Date.getTimezoneOffset(), for the time of day
    intent: str = ""


class EvidenceChunk(BaseModel):
    """One retrieved passage with everything needed to cite it."""

    chunk_id: str
    document_id: str = ""
    document_name: str
    document_content: str  # full retrieved passage
    page_no: int = 0
    page_end: int = 0
    paragraph: str = ""  # human-readable paragraph locator
    section: str = ""
    line_start: int = 0
    line_end: int = 0
    retrieval_score: float = 0.0
    repository_id: str = ""
    original_text: str = ""  # pre-LLM excerpt (passage / first sentence)
    # Document approval metadata (from IKM). Empty string means "not stated".
    document_title: str = ""
    version: str = ""
    approval_status: str = ""
    effective_date: str = ""
    expiry_date: str = ""
    superseded_by: str = ""


class ClaimEvidence(BaseModel):
    """A claim's link to a source passage."""

    chunk_id: str
    document_id: str = ""
    document_name: str = ""
    document_content: str = ""
    page_no: int = 0
    paragraph: str = ""
    section: str = ""
    original_text: str = ""  # verbatim sentence(s) from the passage that support the claim
    line_start: int = 0
    line_end: int = 0


class ClaimValidation(BaseModel):
    score: float
    verdict: Literal["supported", "partially_supported", "unsupported"]
    signals: dict[str, Any] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)


class Claim(BaseModel):
    claim_id: str
    claim_text: str
    evidence: list[ClaimEvidence] = Field(default_factory=list)
    validation: Optional[ClaimValidation] = None


class ValidationSummary(BaseModel):
    overall_score: float
    threshold: float
    claim_min_score: float
    decision: Decision
    reason_codes: list[str] = Field(default_factory=list)
    judge: str = "deterministic"
    validated_at: str = Field(default_factory=_now)
    confidence_parts: dict[str, Any] = Field(default_factory=dict)


class RetrievalChunkSnapshot(BaseModel):
    """Retrieved evidence as seen before the answer LLM runs."""

    chunk_id: str
    document_id: str = ""
    document_name: str = ""
    document_content: str = ""
    page_no: int = 0
    paragraph: str = ""
    original_text: str = ""


class RetrievalSnapshot(BaseModel):
    """Pre-generation evidence JSON (user query + IKM passages)."""

    user_query: str
    agent: str = "query_agent"
    chunks: list[RetrievalChunkSnapshot] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now)
    search_mode: str = ""
    elapsed_ms: int = 0


class ValidationPayload(BaseModel):
    correlation_id: str
    message_id: str
    channel: Channel
    repository_id: str
    user_query: str
    agent: str
    scope: dict[str, Any] = Field(default_factory=dict)
    draft_answer: str = ""
    claims: list[Claim] = Field(default_factory=list)
    chunks: list[EvidenceChunk] = Field(default_factory=list)
    retrieval: Optional[RetrievalSnapshot] = None
    generation_error: str = ""
    created_at: str = Field(default_factory=_now)
    validation: Optional[ValidationSummary] = None


class AgentResponse(BaseModel):
    """What the query agent hands back to the originating channel."""

    message_id: str
    correlation_id: str
    conversation_id: str
    channel: Channel
    status: MessageStatus
    decision: Decision
    answer: str
    overall_score: float
    threshold: float
    claims: list[Claim] = Field(default_factory=list)
    msl_case_id: Optional[str] = None
    reason_codes: list[str] = Field(default_factory=list)
    trace_id: str = ""
    delivery: dict[str, Any] = Field(default_factory=dict)
    elapsed_ms: int = 0
    session_id: str = ""
    documents_found: list[str] = Field(default_factory=list)
    timing: dict[str, int] = Field(default_factory=dict)
    presentation_body: str = ""
    presentation_references: list[str] = Field(default_factory=list)
    confidence_parts: dict[str, Any] = Field(default_factory=dict)
    standard_reply_version: str = ""
    # What kind of message this is. The UI picks its component from message_type, never from the decision.
    intent: str = ""  # greeting|smalltalk|capability|unclear|clinical|out_of_area|off_topic|safety|injection|blocked
    message_type: str = ""  # assistant_note | answer | escalation | refusal | safety
    retrieval_ran: bool = False
    llm_called: bool = False
    actions: list[str] = Field(default_factory=list)  # ask_msl | helpful | send_to_msl
    metrics: Optional[dict[str, Any]] = None  # only when retrieval ran and sources exist; staff view only

    def enforce_display_rules(self) -> "AgentResponse":
        """Scores are honest: shown only when retrieval ran and sources exist; an Answer always has sources."""
        has_sources = bool(self.claims)
        if self.message_type == "answer" and not has_sources:
            self.message_type = "assistant_note"
        if not (self.retrieval_ran and has_sources):
            self.metrics = None
        if self.message_type != "answer":
            self.actions = [a for a in self.actions if a != "helpful"]
        return self
