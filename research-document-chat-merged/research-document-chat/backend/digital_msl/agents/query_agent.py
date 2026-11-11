"""Query Agent: the channel-facing orchestrator.

    channel request -> InboundMessage -> scope & safety -> repository retrieval
      -> Retina Answer Agent (claims)          => pre-validation JSON
      -> Validation Agent (scores + decision)  => post-validation JSON
      -> respond on the originating channel, refuse off-topic questions without
         a case, or open a Physical MSL case and acknowledge on the originating channel.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Awaitable, Callable, Optional

from digital_msl import compliance, standard_replies, store
from digital_msl.agents.answer_agent import RetinaAnswerAgent
from digital_msl.agents.validation_agent import ValidationAgent
from digital_msl.channels import adapter_for
from digital_msl.summary import compose_validated_answer, format_web_answer
from digital_msl.config import get_dmsl_settings
from digital_msl.contracts import AgentResponse, InboundMessage, ValidationPayload
from digital_msl.evidence import (
    RetrieveClient,
    first_sentence,
    retrieval_snapshot,
    retrieve_evidence,
    retrieve_evidence_across_repositories,
    usable_chunks,
)
from digital_msl.llm import LLMClient, default_llm
from digital_msl.scope import classify_query
from digital_msl.intent import build_reply, classify_intent

logger = logging.getLogger(__name__)

StatusCallback = Callable[[str, dict], Awaitable[None]]


def default_retrieve_client() -> RetrieveClient:
    """Host DmsClient when this package is mounted in the app; IKM adapter otherwise."""
    try:
        from services.dms_client import DmsClient

        return DmsClient()
    except Exception:
        from digital_msl.ikm_client import IkmClient

        return IkmClient()


POLITE_REFUSE = (
    "Thank you for the question. Digital MSL is limited to your assigned therapeutic areas. "
    "Questions outside them cannot be answered here. "
    "Please ask a scientific question within your assigned areas, or your Physical MSL team can help with anything else."
)

POLITE_UNASSIGNED_LIBRARY = (
    "Thank you for using Digital MSL. Your account does not yet have access to this therapeutic area. "
    "Please ask an administrator to assign one, then try again."
)

POLITE_RETRIEVAL_UNAVAILABLE = (
    "We could not reach the document search service (IKM/DMS) in time, or it did not respond. "
    "Your question was not passed to the MSL queue — please wait a moment and try again. "
    "If this keeps happening, check network/VPN access to the document platform or contact your administrator."
)

POLITE_NO_PASSAGES = (
    "Thank you for the question. We searched your assigned library but no indexed passages matched closely enough. "
    "Try rephrasing with study names or endpoints (for example “563-eye DME treatment switching”), "
    "or your Physical MSL can help."
)


def _format_msl_name(msl_id: str) -> str:
    raw = _display_name(msl_id, "").strip()
    if not raw:
        return "your Physical MSL"
    if re.search(r"\bmsl\b", raw, re.I):
        return raw
    return f"{raw}, MSL"


def _reason_copy(reason_codes: list[str], msl_name: str) -> str:
    who = msl_name or "your Physical MSL"
    if "OUT_OF_SCOPE" in reason_codes:
        return (
            "Thank you for the question. Digital MSL is configured for your assigned therapeutic areas only. "
            "This question is outside that scope, "
            f"so it has been passed to {who} for review."
        )
    if "SAFETY_INDIVIDUAL_ADVICE" in reason_codes:
        return (
            "Thank you for the question. Individual patient decisions are best handled by a person, "
            f"so your question has been passed to {who} for review."
        )
    if "INSUFFICIENT_EVIDENCE" in reason_codes:
        return (
            "Thank you for the question. We searched your therapeutic-area documents but could not "
            f"confirm a fully sourced answer. {who} can review this for you."
        )
    if "RETRIEVAL_FAILED" in reason_codes:
        return POLITE_RETRIEVAL_UNAVAILABLE
    if "NO_PASSAGES" in reason_codes or "NO_APPROVED_SOURCE" in reason_codes:
        return standard_replies.NOT_FOUND
    return (
        "Thank you for the question. We could not fully confirm an answer from the approved therapeutic area, "
        f"so your question has been passed to {who} for review."
    )


def _display_name(user_id: str, fallback: str) -> str:
    if not user_id:
        return fallback
    user = store.get_portal_user(user_id)
    return (user or {}).get("display_name") or fallback


def repository_candidates_for_message(message: InboundMessage, portal: dict | None) -> list[str]:
    """Repository IDs to search — doctors: assigned libraries only (avoids slow multi-repo DMS fan-out)."""
    defaults = get_dmsl_settings().default_repository_ids()
    assigned = [str(x) for x in (portal or {}).get("assigned_repository_ids") or [] if x]
    if portal and portal.get("role") == "doctor" and assigned:
        return list(dict.fromkeys(assigned))
    rid = (message.repository_id or "").strip()
    if rid:
        return list(dict.fromkeys([rid, *defaults]))
    if defaults:
        return list(dict.fromkeys(defaults))
    return []


class QueryAgent:
    name = "query_agent"

    def __init__(
        self,
        retrieve_client: Optional[RetrieveClient] = None,
        llm: Optional[LLMClient] = None,
        *,
        use_default_llm: bool = True,
    ):
        self.settings = get_dmsl_settings()
        self.retrieve_client = retrieve_client or default_retrieve_client()
        self.llm = llm if llm is not None else (default_llm() if use_default_llm else None)
        self.answer_agent = RetinaAnswerAgent(self.llm)
        self.validation_agent = ValidationAgent(self.llm)

    async def handle(self, message: InboundMessage, on_status: Optional[StatusCallback] = None) -> AgentResponse:
        response = await self._handle_inner(message, on_status)
        return self._stamp(message, response)

    def _stamp(self, message: InboundMessage, response: AgentResponse) -> AgentResponse:
        """Give every reply an intent and a message type, and record them (audit + statistics hygiene)."""
        fresh = response.message_id == message.message_id
        if not response.intent:
            codes = set(response.reason_codes or [])
            if "SAFETY_ADVERSE_EVENT" in codes:
                response.intent, response.message_type = "safety", "safety"
            elif codes & {"DOCTOR_NOT_VERIFIED", "RATE_LIMITED", "UNREGISTERED", "DISABLED", "REPOSITORY_NOT_ASSIGNED"}:
                response.intent, response.message_type = "blocked", "refusal"
            else:
                response.intent = "clinical"
                timing = response.timing or {}
                response.retrieval_ran = "retrieve_ms" in timing
                response.llm_called = "draft_ms" in timing
        if not response.message_type:
            if response.decision == "respond":
                response.message_type = "answer" if response.claims else "assistant_note"
            elif response.decision == "escalate_to_msl":
                response.message_type = "escalation"
            else:
                response.message_type = "refusal"
        if response.message_type == "answer" and "ask_msl" not in response.actions:
            response.actions = [*response.actions, "ask_msl", "helpful"]
        if response.metrics is not None:
            response.metrics = {**response.metrics, "response_time_ms": response.elapsed_ms}
        response.enforce_display_rules()
        if fresh:
            portal = store.get_portal_user(message.user_id) if message.user_id else None
            store.record_audit_event(
                action="chat_intent",
                user_id=message.user_id,
                role=str((portal or {}).get("role") or ""),
                session_id=message.session_id or "",
                repository_id=message.repository_id or "",
                conversation_id=message.conversation_id or "",
                entity_type="message",
                entity_id=message.message_id,
                outcome=response.message_type,
                detail={
                    "intent": response.intent,
                    "message_type": response.message_type,
                    "retrieval_ran": response.retrieval_ran,
                    "llm_called": response.llm_called,
                    "decision": response.decision,
                    "timezone": message.timezone,
                    "notice_version": message.notice_version,
                },
            )
        return response

    async def _intent_response(
        self, message: InboundMessage, portal: Optional[dict], started: float
    ) -> Optional[AgentResponse]:
        """Fixed-text replies for everything that is not a clinical question in the doctor's areas."""
        by_id = {a["repository_id"]: a for a in store.list_therapeutic_areas()}
        assigned = list((portal or {}).get("assigned_repository_ids") or [])
        if not assigned and message.repository_id:
            assigned = [message.repository_id]
        areas = [by_id[r] for r in assigned if r in by_id]
        names = [a["name"] for a in areas]
        area_terms = self._scope_terms(message, portal)
        all_terms = [kw for a in by_id.values() for kw in (a.get("scope_keywords") or [])]
        intent = classify_intent(message.message_text, area_terms=area_terms, all_area_terms=all_terms)
        message.intent = intent.kind
        if intent.kind == "clinical":
            return None
        reply = build_reply(
            intent,
            display_name=_display_name(message.user_id, ""),
            area_names=names,
            utc_offset_minutes=message.client_utc_offset_minutes,
        )
        refusal = intent.kind in {"injection", "out_of_area"}
        codes = {"injection": ["PROMPT_INJECTION"], "out_of_area": ["OUT_OF_AREA"]}.get(intent.kind, [])
        elapsed = int((time.perf_counter() - started) * 1000)
        response = AgentResponse(
            message_id=message.message_id,
            correlation_id=message.correlation_id,
            conversation_id=message.conversation_id,
            channel=message.channel,
            status="refused" if refusal else "answered",
            decision="refuse" if refusal else "respond",
            answer=reply,
            overall_score=0.0,
            threshold=self.settings.confidence_threshold,
            claims=[],
            reason_codes=codes,
            session_id=message.session_id or "",
            elapsed_ms=elapsed,
            intent=intent.kind,
            message_type="refusal" if refusal else "assistant_note",
            retrieval_ran=False,
            llm_called=False,
            actions=list(intent.actions),
        )
        store.update_inbound(message.message_id, status=response.status, response=response)
        if intent.kind == "injection":
            store.record_audit_event(
                action="chat_injection_attempt",
                user_id=message.user_id,
                session_id=message.session_id or "",
                conversation_id=message.conversation_id or "",
                entity_type="message",
                entity_id=message.message_id,
                outcome="refused",
                detail={"channel": message.channel},
            )
        if message.channel != "web":
            response.delivery = await asyncio.to_thread(adapter_for(message.channel).deliver, message, response)
            store.update_inbound(message.message_id, status=response.status, response=response)
        return response

    async def _handle_inner(self, message: InboundMessage, on_status: Optional[StatusCallback] = None) -> AgentResponse:
        started = time.perf_counter()

        async def status(stage: str, **data) -> None:
            if on_status is not None:
                await on_status(stage, data)

        cached = store.find_by_idempotency_key(message.idempotency_key)
        if cached:
            prior = AgentResponse(**cached)
            if (
                message.channel == "email"
                and prior.decision == "respond"
                and not store.email_outbox_sent_for_inbound(message.message_id)
            ):
                logger.info("Retrying email delivery for message %s.", message.message_id)
                prior.delivery = await asyncio.to_thread(
                    adapter_for(message.channel).deliver, message, prior
                )
                store.update_inbound(message.message_id, status=prior.status, response=prior)
                return prior
            logger.info("Duplicate inbound message %s ignored (idempotency key).", message.idempotency_key)
            return prior

        if not message.conversation_id:
            from digital_msl.contracts import new_id

            message.conversation_id = new_id("conv")
        portal = store.get_portal_user(message.user_id) if message.user_id else None
        if not message.assigned_msl_id:
            cover = store.msls_for_repository(message.repository_id) if message.repository_id else []
            if cover:
                message.assigned_msl_id = cover[0]
            elif portal and portal.get("assigned_msl_id"):
                message.assigned_msl_id = portal["assigned_msl_id"]
        message.assigned_msl_id = message.assigned_msl_id or self.settings.default_msl_id
        message.status = "processing"
        message.notice_version = compliance.COMPLIANCE_NOTICE_VERSION
        store.record_inbound(message)

        gated = await self._compliance_gate(message, portal, started)
        if gated is not None:
            return gated

        if portal and portal.get("role") == "doctor":
            allowed = list(portal.get("assigned_repository_ids") or [])
            if allowed and message.repository_id and message.repository_id not in allowed:
                response = AgentResponse(
                    message_id=message.message_id,
                    correlation_id=message.correlation_id,
                    conversation_id=message.conversation_id,
                    channel=message.channel,
                    status="refused",
                    decision="refuse",
                    answer=POLITE_UNASSIGNED_LIBRARY,
                    overall_score=0.0,
                    threshold=self.settings.confidence_threshold,
                    claims=[],
                    reason_codes=["REPOSITORY_NOT_ASSIGNED"],
                    session_id=message.session_id or message.conversation_id,
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                )
                store.update_inbound(message.message_id, status=response.status, response=response)
                return response

        routed = await self._intent_response(message, portal, started)
        if routed is not None:
            return routed

        timing: dict[str, int] = {}
        try:
            pre, timing = await self._draft(message, status, timing)
            validate_t0 = time.perf_counter()
            await status("validating", detail="Validation engine scoring claims")
            post = await self.validation_agent.validate(pre)
            timing["validate_ms"] = int((time.perf_counter() - validate_t0) * 1000)
            trace_id = store.save_trace(pre, post)
            response = self._finalize(message, post, trace_id)
            if response.decision == "respond":
                composed = await compose_validated_answer(
                    message.message_text,
                    response,
                    self.llm,
                    message=message,
                )
                response.presentation_body = composed.body
                response.presentation_references = composed.reference_lines
                response.answer = format_web_answer(composed)
                if "SAFETY_INDIVIDUAL_ADVICE" in response.reason_codes:
                    response.answer += (
                        "\n\nThis is therapeutic-area science, not a personal treatment plan. "
                        "Your Physical MSL team can help with decisions for a specific patient."
                    )
            ae_term = compliance.detect_ae_keywords(message.message_text)
            if ae_term and "SAFETY_ADVERSE_EVENT" not in response.reason_codes:
                self._append_pv_reminder(response)
                self._flag_ae_conversation(message, portal, ae_term)
            await status("routing", detail=f"Routing reply to {message.channel}")
            response.delivery = await asyncio.to_thread(adapter_for(message.channel).deliver, message, response)
            response.elapsed_ms = int((time.perf_counter() - started) * 1000)
            response.session_id = message.session_id or ""
            timing["total_ms"] = response.elapsed_ms
            response.timing = timing
            names: list[str] = []
            for claim in response.claims:
                for ev in claim.evidence:
                    label = ev.document_name or ev.document_id
                    if label and label not in names:
                        names.append(label)
            response.documents_found = names
            store.update_inbound(message.message_id, status=response.status, response=response)
            store.record_audit_event(
                action=f"agent_{response.decision}",
                user_id=message.user_id,
                role=str((portal or {}).get("role") or ""),
                session_id=message.session_id or "",
                repository_id=message.repository_id or "",
                conversation_id=message.conversation_id or "",
                case_id=response.msl_case_id or "",
                entity_type="message",
                entity_id=message.message_id,
                detail={
                    "status": response.status,
                    "reason_codes": response.reason_codes,
                    "timing": timing,
                },
            )
            return response
        except Exception:
            store.update_inbound(message.message_id, status="failed")
            raise

    @staticmethod
    def _append_pv_reminder(response: AgentResponse) -> None:
        reminder = compliance.pv_reminder()
        response.answer = f"{(response.answer or '').rstrip()}\n\n{reminder}"
        if response.presentation_body:
            response.presentation_body = f"{response.presentation_body.rstrip()}\n\n{reminder}"

    @staticmethod
    def _flag_ae_conversation(message: InboundMessage, portal: Optional[dict], term: str) -> None:
        """Flag the conversation for the Physical MSL once, and tell the MSLs of the therapeutic area."""
        if store.ae_flag_exists(message.conversation_id):
            return
        store.record_audit_event(
            action="safety_ae_keyword_flag",
            user_id=message.user_id,
            role=str((portal or {}).get("role") or ""),
            session_id=message.session_id or "",
            repository_id=message.repository_id or "",
            conversation_id=message.conversation_id or "",
            entity_type="message",
            entity_id=message.message_id,
            outcome="flagged",
            detail={"keyword": term, "channel": message.channel},
        )
        recipients = store.msls_for_repository(message.repository_id) if message.repository_id else []
        if message.assigned_msl_id and message.assigned_msl_id not in recipients:
            recipients.append(message.assigned_msl_id)
        for msl_id in recipients:
            store.add_notification(
                user_id=msl_id,
                kind="safety_flag",
                title="Conversation flagged: possible adverse event or complaint",
                body="A doctor's message mentions adverse-event vocabulary. Review the conversation and follow your pharmacovigilance SOP.",
            )

    @staticmethod
    def _scope_terms(message: InboundMessage, portal: Optional[dict]) -> list[str]:
        """Scope keywords of the therapeutic areas this sender can use."""
        ids = repository_candidates_for_message(message, portal)
        return store.scope_keywords_for_repositories(ids)

    async def _compliance_gate(
        self, message: InboundMessage, portal: Optional[dict], started: float
    ) -> Optional[AgentResponse]:
        """Sender verification, rate limit and adverse-event screening, before any retrieval or drafting."""
        sender = portal
        if sender is None and message.channel == "email" and "@" in (message.external_sender_id or ""):
            sender = store.get_portal_user_by_email(message.external_sender_id)
        elif sender is None and message.channel == "whatsapp" and message.external_sender_id:
            sender = store.get_portal_user_by_whatsapp(message.external_sender_id)
        role = str((sender or {}).get("role") or "")
        user_id = (sender or {}).get("user_id") or message.user_id

        def refuse(code: str, answer: str, audit: str) -> AgentResponse:
            response = AgentResponse(
                message_id=message.message_id,
                correlation_id=message.correlation_id,
                conversation_id=message.conversation_id,
                channel=message.channel,
                status="refused",
                decision="refuse",
                answer=answer,
                overall_score=0.0,
                threshold=self.settings.confidence_threshold,
                claims=[],
                reason_codes=[code],
                session_id=message.session_id or "",
                elapsed_ms=int((time.perf_counter() - started) * 1000),
            )
            store.update_inbound(message.message_id, status=response.status, response=response)
            store.record_audit_event(
                action=audit,
                user_id=user_id or "",
                role=role,
                session_id=message.session_id or "",
                repository_id=message.repository_id or "",
                conversation_id=message.conversation_id or "",
                entity_type="message",
                entity_id=message.message_id,
                outcome=code.lower(),
                detail={"channel": message.channel},
            )
            return response

        block = compliance.sender_block_reason(sender)
        if block:
            answer = (
                compliance.NOT_VERIFIED_MESSAGE
                if block == "not_verified"
                else "Your account is not active. Please contact your administrator."
                if block == "disabled"
                else "Digital MSL is available to registered doctors. Please register on the portal first."
            )
            code = "DOCTOR_NOT_VERIFIED" if block == "not_verified" else block.upper()
            response = refuse(code, answer, "agent_blocked")
            if message.channel != "web":
                response.delivery = await asyncio.to_thread(adapter_for(message.channel).deliver, message, response)
            return response

        if role == "doctor" and not compliance.check_rate_limit(user_id):
            response = refuse("RATE_LIMITED", compliance.RATE_LIMITED_MESSAGE, "agent_blocked")
            if message.channel != "web":
                response.delivery = await asyncio.to_thread(adapter_for(message.channel).deliver, message, response)
            return response

        reason = compliance.detect_adverse_event(message.message_text)
        if reason:
            return await self._escalate_adverse_event(message, role, user_id, reason, started)
        return None

    async def _escalate_adverse_event(
        self, message: InboundMessage, role: str, user_id: str, reason: str, started: float
    ) -> AgentResponse:
        """Patient-specific adverse events and product complaints go straight to a person."""
        reason_codes = ["SAFETY_ADVERSE_EVENT"]
        case_id = store.create_case(message, trace_id="", reason_codes=reason_codes, priority="urgent")
        msl_name = _format_msl_name(message.assigned_msl_id or "")
        vars_common = {
            "reason": compliance.ADVERSE_EVENT_ACK,
            "channel": message.channel,
            "msl_name": msl_name,
            "question": message.message_text,
            "doctor_name": _display_name(message.user_id, "Doctor"),
            "reason_codes": ", ".join(reason_codes),
            "draft_body": "",
            "case_id": case_id,
        }
        store.set_case_queue_notice(
            case_id,
            "POSSIBLE ADVERSE EVENT / PRODUCT COMPLAINT - forward to pharmacovigilance within your SOP timeline. "
            + store.render_template("msl_new_case", vars_common, channel=message.channel),
        )
        store.notify_group_new_case(case_id, message.message_text, message.user_id)
        response = AgentResponse(
            message_id=message.message_id,
            correlation_id=message.correlation_id,
            conversation_id=message.conversation_id,
            channel=message.channel,
            status="escalated",
            decision="escalate_to_msl",
            answer=compliance.ADVERSE_EVENT_ACK,
            overall_score=0.0,
            threshold=self.settings.confidence_threshold,
            claims=[],
            reason_codes=reason_codes,
            msl_case_id=case_id,
            session_id=message.session_id or "",
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            intent="safety",
            message_type="safety",
        )
        if message.channel != "web":
            response.delivery = await asyncio.to_thread(adapter_for(message.channel).deliver, message, response)
        store.update_inbound(message.message_id, status=response.status, response=response)
        store.record_audit_event(
            action="safety_adverse_event_flagged",
            user_id=user_id or "",
            role=role,
            session_id=message.session_id or "",
            repository_id=message.repository_id or "",
            conversation_id=message.conversation_id or "",
            case_id=case_id,
            entity_type="message",
            entity_id=message.message_id,
            outcome="escalated",
            detail={"trigger": reason, "channel": message.channel},
        )
        logger.warning("[%s] Possible adverse event escalated: case %s (%s)", message.correlation_id, case_id, reason)
        return response

    async def _draft(self, message: InboundMessage, status, timing: dict[str, int]) -> tuple[ValidationPayload, dict[str, int]]:
        scope_t0 = time.perf_counter()
        await status("scope", detail="Checking therapeutic-area scope and safety policy")
        area_terms = self._scope_terms(message, store.get_portal_user(message.user_id) if message.user_id else None)
        scope = classify_query(message.message_text, area_terms)
        if not scope.in_scope and message.conversation_history:
            # Follow-ups ("what about its safety?") inherit scope from the previous question.
            previous = [m.get("content", "") for m in message.conversation_history if m.get("role") == "user"]
            if previous:
                follow_up = classify_query(f"{previous[-1]} {message.message_text}", area_terms)
                if follow_up.in_scope:
                    follow_up.safety_flags = scope.safety_flags
                    scope = follow_up
        scope_dict = scope.to_dict()
        timing["scope_ms"] = int((time.perf_counter() - scope_t0) * 1000)

        def blocked_payload(error: str, *, search_mode: str = "", elapsed_ms: int = 0) -> ValidationPayload:
            return ValidationPayload(
                correlation_id=message.correlation_id,
                message_id=message.message_id,
                channel=message.channel,
                repository_id=message.repository_id,
                user_query=message.message_text,
                agent=self.answer_agent.name,
                scope=scope_dict,
                generation_error=error,
                retrieval=retrieval_snapshot(
                    message.message_text, [], search_mode=search_mode, elapsed_ms=elapsed_ms
                ),
            )

        if not scope.in_scope:
            return blocked_payload(""), timing

        await status("retrieving", detail="Searching your therapeutic-area documents")
        from digital_msl.evidence import retrieval_search_query

        query = retrieval_search_query(message.message_text or "")
        retrieve_t0 = time.perf_counter()
        portal_row = store.get_portal_user(message.user_id) if message.user_id else None
        repo_ids = repository_candidates_for_message(message, portal_row)
        if len(repo_ids) > 1:
            outcome, primary_repo = await asyncio.to_thread(
                retrieve_evidence_across_repositories,
                self.retrieve_client,
                query=query,
                repository_ids=repo_ids,
                top_k=self.settings.retrieval_top_k,
            )
            if primary_repo:
                message.repository_id = primary_repo
        else:
            outcome = await asyncio.to_thread(
                retrieve_evidence,
                self.retrieve_client,
                query=query,
                repository_id=message.repository_id or (repo_ids[0] if repo_ids else ""),
                top_k=self.settings.retrieval_top_k,
            )
        timing["retrieve_ms"] = outcome.elapsed_ms or int((time.perf_counter() - retrieve_t0) * 1000)
        if outcome.no_approved_source:
            return blocked_payload("no_approved_source", search_mode=outcome.search_mode, elapsed_ms=outcome.elapsed_ms), timing
        if outcome.blocked:
            return (
                blocked_payload(
                    f"retrieval_blocked: {outcome.block_reason}",
                    search_mode=outcome.search_mode,
                    elapsed_ms=outcome.elapsed_ms,
                ),
                timing,
            )
        if outcome.error:
            return (
                blocked_payload(
                    f"retrieval_failed: {outcome.error}",
                    search_mode=outcome.search_mode,
                    elapsed_ms=outcome.elapsed_ms,
                ),
                timing,
            )
        if not outcome.chunks:
            return (
                blocked_payload(
                    "no_evidence",
                    search_mode=outcome.search_mode,
                    elapsed_ms=outcome.elapsed_ms,
                ),
                timing,
            )

        for chunk in outcome.chunks:
            if not chunk.original_text:
                chunk.original_text = first_sentence(chunk.document_content) or chunk.document_content.strip()
        snapshot = retrieval_snapshot(
            message.message_text,
            outcome.chunks,
            search_mode=outcome.search_mode,
            elapsed_ms=outcome.elapsed_ms,
        )
        chunks = usable_chunks(outcome.chunks)
        await status(
            "evidence",
            detail=f"Captured retrieval snapshot ({len(chunks)} cited of {len(outcome.chunks)} passage(s))",
        )
        if not chunks:
            payload = blocked_payload("citation_metadata_missing")
            payload.retrieval = snapshot
            payload.chunks = outcome.chunks
            return payload, timing

        draft_t0 = time.perf_counter()
        await status("drafting", detail=f"Drafting answer from {len(chunks)} passages")
        draft = await self.answer_agent.draft(message, chunks, scope_dict)
        timing["draft_ms"] = int((time.perf_counter() - draft_t0) * 1000)
        draft.retrieval = snapshot
        return draft, timing

    def _finalize(self, message: InboundMessage, post: ValidationPayload, trace_id: str) -> AgentResponse:
        summary = post.validation
        assert summary is not None
        common = dict(
            message_id=message.message_id,
            correlation_id=message.correlation_id,
            conversation_id=message.conversation_id,
            channel=message.channel,
            overall_score=summary.overall_score,
            threshold=summary.threshold,
            reason_codes=summary.reason_codes,
            trace_id=trace_id,
        )
        if summary.decision == "respond":
            response = AgentResponse(
                status="answered",
                decision="respond",
                answer=post.draft_answer or "",
                claims=post.claims,
                retrieval_ran=True,
                **common,
            )
            response.metrics = {
                "retrieval_confidence": max((c.retrieval_score for c in post.chunks), default=None),
                "groundedness": summary.overall_score,
            }
            return response

        if summary.decision == "refuse":
            if "RETRIEVAL_FAILED" in summary.reason_codes:
                answer = POLITE_RETRIEVAL_UNAVAILABLE
            elif "NO_PASSAGES" in summary.reason_codes:
                answer = POLITE_NO_PASSAGES
            elif "OUT_OF_SCOPE" in summary.reason_codes:
                answer = POLITE_REFUSE
            else:
                answer = POLITE_REFUSE
            return AgentResponse(
                status="refused",
                decision="refuse",
                answer=answer,
                claims=[],
                **common,
            )

        priority = "high" if "SAFETY_INDIVIDUAL_ADVICE" in summary.reason_codes else "normal"
        doctor_name = _display_name(message.user_id, "Doctor")
        msl_name = _format_msl_name(message.assigned_msl_id or "")
        reason = _reason_copy(summary.reason_codes, msl_name)
        case_id = store.create_case(
            message,
            trace_id=trace_id,
            reason_codes=summary.reason_codes,
            priority=priority,
            draft_response=post.draft_answer or "",
            claims=post.claims,
        )
        vars_common = {
            "reason": reason,
            "channel": message.channel,
            "msl_name": msl_name,
            "question": message.message_text,
            "doctor_name": doctor_name,
            "reason_codes": ", ".join(summary.reason_codes),
            "draft_body": "",
            "case_id": case_id,
        }
        store.set_case_queue_notice(
            case_id, store.render_template("msl_new_case", vars_common, channel=message.channel)
        )
        store.notify_group_new_case(case_id, message.message_text, message.user_id)
        if message.channel == "whatsapp":
            from digital_msl.whatsapp_bridge import notify_msl_whatsapp_group

            notify_msl_whatsapp_group(
                case_id=case_id,
                question=message.message_text,
                requester=message.external_sender_id or message.user_id,
                group_label=message.subject or "",
            )
        ack = store.render_template("doctor_escalation_ack", vars_common, channel=message.channel)
        logger.info("[%s] Escalated to Physical MSL: case %s (%s)", message.correlation_id, case_id, summary.reason_codes)
        return AgentResponse(
            status="escalated",
            decision="escalate_to_msl",
            answer=ack,
            claims=post.claims,
            presentation_body=(post.draft_answer or "").strip(),
            msl_case_id=case_id,
            **common,
        )
