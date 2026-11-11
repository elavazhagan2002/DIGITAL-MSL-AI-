"""HTTP API for the Digital MSL component (mount with ``app.include_router(router)``)."""
from __future__ import annotations

import logging
import re

import asyncio
import io
import json
import uuid
from typing import Any, Optional

import os
import time

from fastapi import APIRouter, HTTPException, Query, File, UploadFile, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from digital_msl import store
from digital_msl.agents.query_agent import QueryAgent
from digital_msl.agents.validation_agent import ValidationAgent
from digital_msl.channels import EmailChannel, WhatsAppChannel, adapter_for
from digital_msl.config import get_dmsl_settings
from digital_msl.contracts import AgentResponse, InboundMessage, ValidationPayload, new_id
from digital_msl.llm import default_llm
from digital_msl import auth as portal_auth
from digital_msl import compliance, doctor_registry, load_documents, security
from digital_msl.deps import AdminDep, MslDep, PortalDep
from digital_msl.ta_catalog import DEFAULT_THERAPEUTIC_AREAS

router = APIRouter(prefix="/api/v1/digital-msl", tags=["digital-msl"])
logger = logging.getLogger(__name__)


class WebMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    repository_id: str = Field(..., min_length=1)
    conversation_id: Optional[str] = None
    session_id: str = ""
    user_id: str = "web_user"
    assigned_msl_id: str = ""
    conversation_history: list[dict[str, str]] = Field(default_factory=list)
    stream: bool = True
    timezone: str = Field("", max_length=64)
    client_utc_offset_minutes: Optional[int] = Field(None, ge=-840, le=840)


class SessionEndRequest(BaseModel):
    session_id: str
    user_id: str


class PortalLoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    display_name: str = Field("", max_length=120)  # ignored; kept so older clients still validate
    role: Optional[str] = Field(None, pattern="^(admin|doctor|msl)$")  # optional; must match the account's role
    password: str = Field(..., min_length=1, max_length=255)


class PortalUserRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    display_name: str = Field(..., min_length=2, max_length=120)
    role: str = Field(..., pattern="^(admin|doctor|msl)$")
    assigned_msl_id: str = ""
    password: Optional[str] = Field(None, max_length=128)


class AssignmentRequest(BaseModel):
    doctor_id: str
    msl_id: str = ""
    repository_ids: Optional[list[str]] = None


class ResolveCaseRequest(BaseModel):
    resolution: str = Field(..., min_length=1)
    reply_to_requester: bool = True


class SaveDraftRequest(BaseModel):
    author_id: str = "msl_default"
    draft_response: str = ""
    references: list[dict[str, Any]] = Field(default_factory=list)
    claims: list[dict[str, Any]] = Field(default_factory=list)


class ReviewActionRequest(BaseModel):
    reviewer_id: str = "reviewer_default"
    notes: str = ""


class SendResponseRequest(BaseModel):
    sender_id: str = "msl_default"
    reply_to_requester: bool = True


class PolicyUpdateRequest(BaseModel):
    molecules: list[dict[str, Any]] = Field(default_factory=list)
    confidence_threshold: float = 0.90
    claim_min_score: float = 0.70


class TemplateUpsertRequest(BaseModel):
    template_id: Optional[str] = None
    kind: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1)
    channel: str = "all"


def _repository_for(payload: dict[str, Any]) -> str:
    repository_id = str(payload.get("repository_id") or "").strip()
    if not repository_id:
        ids = get_dmsl_settings().default_repository_ids()
        if len(ids) == 1:
            repository_id = ids[0]
    if not repository_id:
        raise HTTPException(
            status_code=422,
            detail="repository_id is required (or set DIGITAL_MSL_DEFAULT_REPOSITORY_ID, or send from a doctor account).",
        )
    return repository_id


def _sse(event_type: str, data: dict) -> str:
    return f"data: {json.dumps({'type': event_type, **data}, ensure_ascii=False)}\n\n"


@router.get("/config")
async def get_config():
    settings = get_dmsl_settings()
    ikm_base = os.environ.get("RAG_BUILDER_BASE_URL") or ""
    try:
        from digital_msl.ikm_client import IkmClient

        ikm_base = IkmClient().base_url
    except Exception:
        pass
    policy = store.get_policy()
    return {
        "molecules": [m["name"] for m in policy["molecules"]],
        "policy": policy,
        "confidence_threshold": policy["confidence_threshold"],
        "claim_min_score": policy["claim_min_score"],
        "use_llm_judge": settings.use_llm_judge,
        "llm_configured": default_llm() is not None,
        "channels": ["web", "email", "whatsapp"],
        "ikm_base_url": ikm_base,
        "whatsapp_configured": bool(settings.whatsapp_api_url and settings.whatsapp_token),
        "whatsapp_msl_notify_count": len(
            [p for p in (settings.whatsapp_msl_notify_phones or "").split(",") if p.strip()]
        ),
        "roles": ["admin", "doctor", "msl"],
    }


@router.get("/ops/status")
async def ops_status():
    """Operator health: IKM reachability, LLM, queues. Never returns secrets."""
    ikm: dict[str, Any] = {
        "reachable": False,
        "latency_ms": None,
        "repository_count": 0,
        "detail": "",
        "base_url": os.environ.get("RAG_BUILDER_BASE_URL") or "",
        "retrieve_probe": {},
    }
    started = time.perf_counter()
    probe_repo = ""
    try:
        from digital_msl.config import get_dmsl_settings
        from digital_msl.ikm_client import IkmClient

        client = IkmClient()
        client.connect_timeout = 5
        client.timeout = 12
        payload = client._request("GET", "/repositories", timeout=12)
        repos = payload.get("repositories") if isinstance(payload, dict) else payload
        ikm["reachable"] = True
        ikm["repository_count"] = len(repos or [])
        ikm["latency_ms"] = int((time.perf_counter() - started) * 1000)
        ikm["detail"] = "IKM authenticated and listed repositories."
        defaults = get_dmsl_settings().default_repository_ids()
        probe_repo = (defaults[0] if defaults else "") or ""
        if probe_repo:
            probe_started = time.perf_counter()
            try:
                client.timeout = 45
                result = client.retrieve(
                    query="563-eye diabetic macular edema treatment switching",
                    top_k=3,
                    repository_id=probe_repo,
                    search_mode="hybrid",
                )
                hits = len(result.get("results") or []) if isinstance(result, dict) else 0
                ikm["retrieve_probe"] = {
                    "ok": True,
                    "repository_id": probe_repo,
                    "hits": hits,
                    "latency_ms": int((time.perf_counter() - probe_started) * 1000),
                }
                if hits == 0:
                    ikm["detail"] = "IKM reachable but retrieve returned zero passages (index/query issue)."
            except Exception as probe_exc:
                ikm["retrieve_probe"] = {
                    "ok": False,
                    "repository_id": probe_repo,
                    "error": str(probe_exc)[:240],
                    "latency_ms": int((time.perf_counter() - probe_started) * 1000),
                }
                ikm["detail"] = f"IKM list OK but retrieve failed: {probe_exc}"[:300]
    except Exception as exc:
        ikm["latency_ms"] = int((time.perf_counter() - started) * 1000)
        ikm["detail"] = str(exc)[:300]

    cases = store.list_cases(limit=200)
    traces = store.list_traces(limit=50)
    policy = store.get_policy()
    return {
        "ikm": ikm,
        "llm_configured": default_llm() is not None,
        "molecules": [m["name"] for m in policy["molecules"]],
        "confidence_threshold": policy["confidence_threshold"],
        "open_cases": sum(1 for c in cases if c.get("status") == "open"),
        "cases": len(cases),
        "traces": len(traces),
        "outbox": len(store.list_outbox(limit=100)),
    }


@router.get("/policy")
async def get_policy():
    return store.get_policy()


@router.put("/policy")
async def put_policy(body: PolicyUpdateRequest, ctx: AdminDep):
    before = store.get_policy()
    saved = store.save_policy(body.model_dump())
    store.record_audit_event(
        action="policy_update", user_id=ctx.user_id, role=ctx.role, entity_type="policy", before=before, after=saved,
    )
    return saved


@router.get("/templates")
async def get_templates(kind: Optional[str] = None):
    return {"templates": store.list_templates(kind=kind)}


@router.post("/templates")
async def post_template(body: TemplateUpsertRequest, ctx: MslDep):
    try:
        saved = store.upsert_template(**body.model_dump())
        store.record_audit_event(
            action="template_update", user_id=ctx.user_id, role=ctx.role, entity_type="template",
            entity_id=str(saved.get("template_id", "")), after={"kind": body.kind, "name": body.name},
        )
        return saved
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/templates/{template_id}")
async def put_template(template_id: str, body: TemplateUpsertRequest, ctx: MslDep):
    try:
        payload = body.model_dump()
        payload["template_id"] = template_id
        before = store.get_template(template_id)
        saved = store.upsert_template(**payload)
        store.record_audit_event(
            action="template_update", user_id=ctx.user_id, role=ctx.role, entity_type="template", entity_id=template_id,
            before={"name": (before or {}).get("name"), "body": (before or {}).get("body")},
            after={"name": body.name, "body": body.body},
        )
        return saved
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/templates/{template_id}")
async def delete_template(template_id: str, ctx: MslDep):
    if not store.delete_template(template_id):
        raise HTTPException(status_code=404, detail="Template not found.")
    store.record_audit_event(action="template_delete", user_id=ctx.user_id, role=ctx.role, entity_type="template", entity_id=template_id)
    return {"ok": True}


# ------------------------------------------------------------------ channels
@router.post("/channels/web/messages")
async def web_message(body: WebMessageRequest, ctx: PortalDep):
    portal_auth.assert_repository_access(ctx.user_id, ctx.role, body.repository_id)
    portal_user = store.get_portal_user(ctx.user_id) or {}
    if compliance.sender_block_reason(portal_user) == "not_verified":
        store.record_audit_event(
            action="agent_blocked", user_id=ctx.user_id, role=ctx.role, session_id=ctx.session_id,
            repository_id=body.repository_id, outcome="not_verified",
        )
        raise HTTPException(status_code=403, detail=compliance.NOT_VERIFIED_MESSAGE)
    if ctx.role == "doctor" and not compliance.check_rate_limit(ctx.user_id):
        store.record_audit_event(
            action="agent_blocked", user_id=ctx.user_id, role=ctx.role, session_id=ctx.session_id,
            repository_id=body.repository_id, outcome="rate_limited",
        )
        raise HTTPException(status_code=429, detail=compliance.RATE_LIMITED_MESSAGE)
    assigned = body.assigned_msl_id.strip()
    if portal_user.get("assigned_msl_id"):
        assigned = assigned or portal_user["assigned_msl_id"]
    session_id = ctx.session_id
    conversation_id = (body.conversation_id or "").strip() or new_id("conv")
    role = ctx.role
    message = InboundMessage(
        channel="web",
        repository_id=body.repository_id,
        message_text=body.message.strip(),
        user_id=ctx.user_id,
        external_sender_id=ctx.user_id,
        assigned_msl_id=assigned,
        role=role,
        consent_and_verification_state="portal_session",
        conversation_history=body.conversation_history,
        session_id=session_id,
        conversation_id=conversation_id,
        timezone=body.timezone,
        client_utc_offset_minutes=body.client_utc_offset_minutes,
    )
    agent = QueryAgent()

    def view(result: AgentResponse) -> dict:
        """Doctors see sources, never internal scores or timings."""
        data = result.model_dump()
        if role == "doctor":
            data["metrics"] = None
            data["timing"] = {}
            data["elapsed_ms"] = 0
            data["confidence_parts"] = {}
        return data

    if not body.stream:
        return view(await agent.handle(message))

    async def events():
        queue: asyncio.Queue = asyncio.Queue()

        async def on_status(stage: str, data: dict) -> None:
            await queue.put(_sse("status", {"stage": stage, **data}))

        async def run() -> None:
            try:
                result = await agent.handle(message, on_status=on_status)
                await queue.put(_sse("result", view(result)))
            except Exception:
                from digital_msl import upstream as _up

                rid = _up.request_id_var.get() or _up.new_request_id()
                logger.exception("Chat processing failed request_id=%s", rid)
                await queue.put(_sse("error", {"detail": "Something went wrong. Please try again.", "request_id": rid}))
            finally:
                await queue.put(None)

        task = asyncio.create_task(run())
        yield _sse("status", {"stage": "received", "correlation_id": message.correlation_id})
        while (item := await queue.get()) is not None:
            yield item
        await task

    return StreamingResponse(events(), media_type="text/event-stream")


def _check_inbound_secret(request: Request) -> None:
    """Shared secret for the unauthenticated email / WhatsApp bridge endpoints (when configured)."""
    import hmac

    secret = (get_dmsl_settings().inbound_secret or "").strip()
    if not secret:
        return
    supplied = request.headers.get("x-dmsl-inbound-secret", "")
    if not hmac.compare_digest(supplied, secret):
        raise HTTPException(status_code=403, detail="Invalid inbound secret.")


class AskMslRequest(BaseModel):
    reason: str = Field("", max_length=300)


@router.post("/messages/{message_id}/ask-msl")
async def ask_physical_msl(message_id: str, body: AskMslRequest, ctx: PortalDep):
    """The doctor asks a Physical MSL about one of their own messages (out-of-area reply, or any answer)."""
    stored = store.get_inbound(message_id)
    if stored is None or stored["user_id"] != ctx.user_id:
        raise HTTPException(status_code=404, detail="Message not found.")
    existing = store.case_for_message(message_id)
    if existing:
        return {"case_id": existing["case_id"], "status": existing["status"], "already_open": True}
    prior_codes = list((stored["response"] or {}).get("reason_codes") or [])
    code = "OUT_OF_AREA" if "OUT_OF_AREA" in prior_codes else "USER_REQUEST"
    user = store.get_portal_user(ctx.user_id) or {}
    cover = store.msls_for_repository(stored["repository_id"]) if stored["repository_id"] else []
    message = InboundMessage(
        message_id=stored["message_id"],
        correlation_id=stored["correlation_id"] or new_id("corr"),
        conversation_id=stored["conversation_id"] or new_id("conv"),
        channel=stored["channel"],
        user_id=ctx.user_id,
        external_sender_id=stored["external_sender_id"],
        repository_id=stored["repository_id"],
        message_text=stored["message_text"],
        assigned_msl_id=(cover[0] if cover else user.get("assigned_msl_id") or get_dmsl_settings().default_msl_id),
    )
    reasons = [code] + ([f"NOTE: {body.reason.strip()}"] if body.reason.strip() else [])
    case_id = store.create_case(message, trace_id="", reason_codes=reasons, priority="normal")
    store.notify_group_new_case(case_id, message.message_text, ctx.user_id)
    store.record_audit_event(
        action="ask_msl", user_id=ctx.user_id, role=ctx.role, conversation_id=message.conversation_id,
        case_id=case_id, entity_type="message", entity_id=message_id, outcome="opened", detail={"reason": code},
    )
    return {"case_id": case_id, "status": "open", "already_open": False}


@router.post("/channels/email/inbound")
async def email_inbound(payload: dict[str, Any], request: Request):
    """Inbound email webhook: {from, subject, text, message_id, repository_id?}."""
    from digital_msl.mailbox import resolve_email_target

    _check_inbound_secret(request)
    payload = {k: v for k, v in payload.items() if k != "user_id"}  # identity comes from the sender, never the body
    sender = str(payload.get("from") or payload.get("sender") or "")
    repository_id = str(payload.get("repository_id") or "").strip()
    user_id = str(payload.get("user_id") or "").strip()
    if not repository_id or not user_id:
        resolved_repo, resolved_user = await resolve_email_target(
            sender,
            str(payload.get("subject") or ""),
            str(payload.get("text") or payload.get("body") or ""),
        )
        repository_id = repository_id or resolved_repo
        user_id = user_id or resolved_user
    if not repository_id:
        raise HTTPException(
            status_code=422,
            detail="repository_id is required (or set DIGITAL_MSL_DEFAULT_REPOSITORY_ID, or send from a doctor account).",
        )
    message = EmailChannel.normalize({**payload, "user_id": user_id}, repository_id=repository_id)
    if not message.message_text:
        raise HTTPException(status_code=422, detail="Email body is empty.")
    return (await QueryAgent().handle(message)).model_dump()


@router.post("/channels/whatsapp/inbound")
async def whatsapp_inbound(payload: dict[str, Any], request: Request):
    """Inbound WhatsApp (manual test or bridge): {from, text, id, group_id?, group_name?, repository_id?}."""
    _check_inbound_secret(request)
    payload = {k: v for k, v in payload.items() if k != "user_id"}
    return (await _handle_whatsapp_payload(payload)).model_dump()


_STOP = re.compile(r"^\s*stop\s*[.!]?\s*$", re.I)


def _whatsapp_stop(payload: dict[str, Any]) -> Optional[AgentResponse]:
    """A registered doctor replying STOP withdraws WhatsApp consent; one confirmation is sent, nothing else."""
    text = payload.get("text")
    if isinstance(text, dict):
        text = text.get("body")
    body = str(text or payload.get("body") or "")
    if not _STOP.match(body):
        return None
    sender = str(payload.get("from") or payload.get("wa_id") or "")
    user = store.get_portal_user_by_whatsapp(sender)
    if user and user.get("role") == "doctor":
        store.set_channel_consents(user["user_id"], {"whatsapp_messaging": False}, source="whatsapp_stop")
        from digital_msl.whatsapp_bridge import send_whatsapp_text

        send_whatsapp_text(
            sender,
            "You have been unsubscribed from Digital MSL WhatsApp messages. You can turn them back on in your profile.",
        )
    return AgentResponse(
        message_id=str(payload.get("id") or new_id("msg")),
        correlation_id=new_id("corr"),
        conversation_id=str(payload.get("conversation_id") or new_id("conv")),
        channel="whatsapp",
        status="answered",
        decision="respond",
        answer="Unsubscribed from WhatsApp messages.",
        overall_score=1.0,
        threshold=get_dmsl_settings().confidence_threshold,
        reason_codes=["CONSENT_WITHDRAWN"] if user else [],
    )


async def _handle_whatsapp_payload(payload: dict[str, Any]) -> AgentResponse:
    stopped = _whatsapp_stop(payload)
    if stopped is not None:
        return stopped
    message = await _whatsapp_inbound_message(payload)
    return await QueryAgent().handle(message)


async def _whatsapp_inbound_message(payload: dict[str, Any]) -> InboundMessage:
    from digital_msl.whatsapp_bridge import resolve_whatsapp_target

    sender = str(payload.get("from") or payload.get("wa_id") or "")
    repository_id = str(payload.get("repository_id") or "").strip()
    user_id = str(payload.get("user_id") or "").strip()
    text = payload.get("text")
    if isinstance(text, dict):
        text = text.get("body")
    body = str(text or payload.get("body") or "").strip()
    if not repository_id or not user_id:
        resolved_repo, resolved_user = await resolve_whatsapp_target(sender, body)
        repository_id = repository_id or resolved_repo
        user_id = user_id or resolved_user
    if not repository_id:
        raise HTTPException(
            status_code=422,
            detail="repository_id is required (or set DIGITAL_MSL_DEFAULT_REPOSITORY_ID, or register sender WhatsApp on doctor profile).",
        )
    message = WhatsAppChannel.normalize({**payload, "text": body, "user_id": user_id}, repository_id=repository_id)
    if not message.message_text:
        raise HTTPException(status_code=422, detail="WhatsApp message text is empty.")
    conv = str(payload.get("conversation_id") or "").strip()
    if conv:
        message.conversation_id = conv
    history = payload.get("conversation_history")
    if isinstance(history, list):
        message.conversation_history = history
    session = str(payload.get("session_id") or "").strip()
    if session:
        message.session_id = session
    return message


def _portal_default_repository(user_id: str) -> str:
    portal = store.get_portal_user(user_id) or {}
    rid = str(portal.get("default_repository_id") or "").strip()
    if rid:
        return rid
    for item in portal.get("assigned_repository_ids") or []:
        if item:
            return str(item)
    defaults = get_dmsl_settings().default_repository_ids()
    return defaults[0] if defaults else ""


async def _whatsapp_demo_repository(user_id: str, sender: str, text: str) -> str:
    """Pick therapeutic area like web/WhatsApp: env defaults + assigned areas, best retrieval match."""
    from digital_msl.agents.query_agent import default_retrieve_client
    from digital_msl.evidence import retrieve_evidence
    from digital_msl.social import is_social_greeting
    from digital_msl.whatsapp_bridge import resolve_whatsapp_target

    defaults = get_dmsl_settings().default_repository_ids()
    portal = store.get_portal_user(user_id) or {}
    assigned = [str(x) for x in (portal.get("assigned_repository_ids") or []) if x]
    candidates = list(dict.fromkeys([d for d in defaults if d] + assigned))

    if is_social_greeting(text):
        preferred = str(portal.get("default_repository_id") or "").strip()
        if preferred:
            return preferred
        return candidates[0] if candidates else _portal_default_repository(user_id)

    if portal.get("role") == "doctor" and assigned:
        # QueryAgent already searches the doctor's assigned libraries; a second search here only adds latency.
        return str(portal.get("default_repository_id") or "").strip() or assigned[0]

    if len(candidates) > 1:
        client = default_retrieve_client()
        query = (text or "").strip()
        best_id, best_score = candidates[0], -1.0
        for repository_id in candidates:
            try:
                outcome = await asyncio.to_thread(
                    retrieve_evidence,
                    client,
                    query=query,
                    repository_id=repository_id,
                    top_k=4,
                )
            except Exception:
                logger.warning("WhatsApp demo retrieval failed for %s", repository_id)
                continue
            score = max((chunk.retrieval_score for chunk in outcome.chunks), default=0.0)
            if outcome.chunks and score >= best_score:
                best_id, best_score = repository_id, score
        if best_score >= 0:
            return best_id

    if len(candidates) == 1:
        return candidates[0]

    resolved_repo, _user = await resolve_whatsapp_target(sender, text)
    return resolved_repo or _portal_default_repository(user_id)


@router.get("/channels/whatsapp/webhook")
async def whatsapp_webhook_verify(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
    hub_challenge: str = Query("", alias="hub.challenge"),
):
    """Meta WhatsApp Cloud API subscription verification."""
    settings = get_dmsl_settings()
    token = (settings.whatsapp_verify_token or "").strip()
    if hub_mode == "subscribe" and token and hub_verify_token == token:
        # Meta expects the challenge as plain text, not JSON.
        return PlainTextResponse(content=hub_challenge)
    raise HTTPException(status_code=403, detail="WhatsApp webhook verification failed.")


async def _process_whatsapp_payloads(payloads: list[dict[str, Any]]) -> None:
    for item in payloads:
        try:
            await _handle_whatsapp_payload(item)
        except Exception:
            logger.exception("WhatsApp webhook message failed for %s", item.get("id"))


@router.post("/channels/whatsapp/webhook")
async def whatsapp_webhook_inbound(request: Request):
    """Meta WhatsApp Cloud API message webhook (X-Hub-Signature-256 verified when an app secret is set)."""
    from digital_msl.whatsapp_bridge import parse_meta_webhook

    raw = await request.body()
    app_secret = (get_dmsl_settings().whatsapp_app_secret or "").strip()
    if app_secret:
        import hashlib
        import hmac

        expected = "sha256=" + hmac.new(app_secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(request.headers.get("x-hub-signature-256", ""), expected):
            raise HTTPException(status_code=403, detail="Invalid WhatsApp webhook signature.")
    else:
        logger.warning("DIGITAL_MSL_WHATSAPP_APP_SECRET is not set; webhook signatures are not verified.")
    try:
        request_body = json.loads(raw or b"{}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON.") from exc
    if not isinstance(request_body, dict):
        raise HTTPException(status_code=400, detail="Invalid payload.")
    payloads = parse_meta_webhook(request_body)
    if payloads:
        logger.info("WhatsApp webhook: %s inbound text message(s)", len(payloads))
        await _process_whatsapp_payloads(payloads)
    else:
        logger.info(
            "WhatsApp webhook: no text messages (object=%s)",
            request_body.get("object"),
        )
    return {"status": "ok"}


@router.post("/whatsapp/subscribe-waba")
async def whatsapp_subscribe_waba(_ctx: AdminDep, waba_id: Optional[str] = None):
    """Subscribe Digital MSL app to your WhatsApp Business Account (required for inbound POST webhooks)."""
    from digital_msl.whatsapp_bridge import subscribe_app_to_waba

    ok, detail = subscribe_app_to_waba(waba_id or "")
    if not ok:
        raise HTTPException(status_code=400, detail=detail)
    return {"subscribed": True, "detail": detail}


@router.post("/channels/whatsapp/simulate")
async def whatsapp_simulate_inbound(_ctx: AdminDep, payload: dict[str, Any]):
    """Admin-only: simulate a doctor WhatsApp message (optionally tagged with group_name for MSL alerts)."""
    response = await _handle_whatsapp_payload(payload)
    return response.model_dump()


@router.get("/channels/whatsapp/demo-config")
async def whatsapp_demo_config(_ctx: PortalDep):
    """Portal WhatsApp demo pane: mock flags and display number."""
    from digital_msl.whatsapp_bridge import whatsapp_mock_delivery_enabled

    settings = get_dmsl_settings()
    return {
        "mock_mode": whatsapp_mock_delivery_enabled(),
        "global_mock": bool(settings.whatsapp_mock),
        "business_display": (settings.whatsapp_business_display or "+1 555-658-2876").strip(),
        "note": "Demo chat uses mock delivery unless DIGITAL_MSL_WHATSAPP_MOCK=false and live Meta send succeeds.",
    }


@router.post("/channels/whatsapp/demo-chat")
async def whatsapp_demo_chat(_ctx: PortalDep, payload: dict[str, Any]):
    """Two-way WhatsApp-style demo in the portal (mock outbound; same QueryAgent as production)."""
    from digital_msl.channels import render_answer_text
    from digital_msl.whatsapp_bridge import normalize_phone, whatsapp_demo_delivery

    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="text is required")
    sender = str(payload.get("from") or payload.get("phone") or "+917010138776").strip()
    sender = normalize_phone(sender) or sender
    repository_id = await _whatsapp_demo_repository(_ctx.user_id, sender, text)
    if not repository_id:
        raise HTTPException(status_code=422, detail="No therapeutic area configured for WhatsApp demo.")
    inbound = {
        "from": sender,
        "text": text,
        "id": str(payload.get("id") or payload.get("message_id") or f"demo_{uuid.uuid4().hex[:12]}"),
        "group_name": str(payload.get("group_name") or get_dmsl_settings().whatsapp_demo_group_name or ""),
        "user_id": _ctx.user_id,
        "repository_id": repository_id,
        "conversation_id": str(payload.get("conversation_id") or "").strip(),
        "conversation_history": payload.get("conversation_history") if isinstance(payload.get("conversation_history"), list) else [],
        "session_id": _ctx.session_id,
    }
    stream = payload.get("stream", True)
    if stream is False or stream == "false":
        with whatsapp_demo_delivery():
            response = await _handle_whatsapp_payload(inbound)
        body = render_answer_text(response)
        return {**response.model_dump(), "reply_text": body}

    async def events():
        queue: asyncio.Queue = asyncio.Queue()

        async def on_status(stage: str, data: dict) -> None:
            await queue.put(_sse("status", {"stage": stage, **data}))

        async def run() -> None:
            try:
                with whatsapp_demo_delivery():
                    message = await _whatsapp_inbound_message(inbound)
                    result = await QueryAgent().handle(message, on_status=on_status)
                reply_body = render_answer_text(result)
                await queue.put(_sse("result", {**result.model_dump(), "reply_text": reply_body}))
            except Exception as exc:
                await queue.put(_sse("error", {"detail": f"Processing failed: {exc}"}))
            finally:
                await queue.put(None)

        task = asyncio.create_task(run())
        yield _sse("status", {"stage": "received"})
        while (item := await queue.get()) is not None:
            yield item
        await task

    return StreamingResponse(events(), media_type="text/event-stream")


# --------------------------------------------------------- validation engine
@router.post("/validate")
async def validate(payload: ValidationPayload):
    """Run the validation engine alone: pre-validation JSON in, post-validation JSON out."""
    return (await ValidationAgent(default_llm()).validate(payload)).model_dump()


@router.get("/traces")
async def list_traces(limit: int = 50):
    return {"traces": store.list_traces(limit=limit)}


@router.get("/traces/{trace_id}")
async def get_trace(trace_id: str):
    trace = store.get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found.")
    return trace


# ---------------------------------------------------------------- MSL cases
@router.get("/cases")
async def list_cases(
    status: Optional[str] = None,
    doctor_id: Optional[str] = None,
    assigned_msl_id: Optional[str] = None,
    viewer_msl_id: Optional[str] = None,
):
    return {
        "cases": store.list_cases(
            status=status,
            doctor_id=doctor_id,
            assigned_msl_id=assigned_msl_id,
            viewer_msl_id=viewer_msl_id,
        )
    }


# ---------------------------------------------------------------- portal RBAC
def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _cookie_secure(request: Request) -> bool:
    return (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto") == "https"
        or get_dmsl_settings().cookie_secure
    )


def _start_session(user: dict[str, Any], request: Request, response: Response, **extra: Any) -> dict[str, Any]:
    """New session id (any session the request carried is ended), HttpOnly session cookie, CSRF cookie."""
    previous = portal_auth.session_id_from_request(request)
    session_id = store.create_login_session(user["user_id"], user["role"], previous_session_id=previous)
    csrf = security.csrf_token(session_id)
    max_age = get_dmsl_settings().session_ttl_hours * 3600
    secure = _cookie_secure(request)
    response.set_cookie(portal_auth.SESSION_COOKIE, session_id, httponly=True, samesite="lax", secure=secure, max_age=max_age, path="/")
    response.set_cookie(portal_auth.CSRF_COOKIE, csrf, httponly=False, samesite="lax", secure=secure, max_age=max_age, path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"user": user, "session_id": session_id, "access_token": session_id, "token_type": "bearer", "csrf_token": csrf, **extra}


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(portal_auth.SESSION_COOKIE, path="/")
    response.delete_cookie(portal_auth.CSRF_COOKIE, path="/")


MFA_TOKEN_TTL = 300


def _mfa_token(user_id: str, step: str) -> str:
    return security.sign_token({"u": user_id, "s": step}, "mfa", ttl_seconds=MFA_TOKEN_TTL)


def _mfa_user(token: str, step: str) -> str:
    body = security.verify_token(token, "mfa")
    if not body or body.get("s") != step:
        raise HTTPException(status_code=401, detail="This sign-in step has expired. Sign in again.")
    return str(body["u"])


@router.post("/portal/login")
async def portal_login(body: PortalLoginRequest, request: Request, response: Response):
    try:
        user = store.authenticate_portal_user(
            email=body.email,
            password=body.password,
            role=body.role,
            ip_address=_client_ip(request),
        )
    except store.AccountLocked as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if user["role"] == "doctor" and not (user.get("email_verified", True) and user.get("mobile_verified", True)):
        return JSONResponse(
            status_code=403,
            content={
                "detail": "Verify your email and mobile number to continue.",
                "code": "CONTACT_NOT_VERIFIED",
                "user_id": user["user_id"],
                "email_verified": bool(user.get("email_verified", True)),
                "mobile_verified": bool(user.get("mobile_verified", True)),
            },
        )
    if user.get("mfa_enabled"):
        return {"mfa_required": True, "mfa_token": _mfa_token(user["user_id"], "verify")}
    demo_exempt = bool(user.get("is_demo")) and get_dmsl_settings().demo_enabled
    if user["role"] == "admin" and get_dmsl_settings().require_admin_mfa and not demo_exempt:
        return {"mfa_enrollment_required": True, "mfa_token": _mfa_token(user["user_id"], "enroll")}
    return _start_session(user, request, response)


class MfaLoginRequest(BaseModel):
    mfa_token: str = Field(..., max_length=1000)
    code: str = Field(..., min_length=6, max_length=20)


@router.post("/portal/login/mfa")
async def portal_login_mfa(body: MfaLoginRequest, request: Request, response: Response):
    user_id = _mfa_user(body.mfa_token, "verify")
    try:
        ok = store.mfa_check(user_id, body.code)
    except store.AccountLocked as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=401, detail="That code is not valid.")
    user = store.get_portal_user(user_id)
    if not user or (user.get("status") or "active") != "active":
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    return _start_session(user, request, response)


class MfaSetupRequest(BaseModel):
    mfa_token: str = ""


class MfaEnableRequest(BaseModel):
    mfa_token: str = ""
    code: str = Field(..., min_length=6, max_length=20)


def _mfa_subject(request: Request, token: str, *, allow_token_steps: tuple[str, ...]) -> tuple[str, bool]:
    """(user_id, from_token). A step token (enrolment during login) or an authenticated session."""
    if token:
        body = security.verify_token(token, "mfa")
        if not body or body.get("s") not in allow_token_steps:
            raise HTTPException(status_code=401, detail="This sign-in step has expired. Sign in again.")
        return str(body["u"]), True
    row = portal_auth.context_from_request(request)
    return row["user_id"], False


@router.post("/portal/mfa/setup")
async def portal_mfa_setup(body: MfaSetupRequest, request: Request):
    user_id, _ = _mfa_subject(request, body.mfa_token, allow_token_steps=("enroll",))
    try:
        return store.mfa_begin_setup(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/portal/mfa/enable")
async def portal_mfa_enable(body: MfaEnableRequest, request: Request, response: Response):
    user_id, from_token = _mfa_subject(request, body.mfa_token, allow_token_steps=("enroll",))
    try:
        recovery = store.mfa_enable(user_id, body.code)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if from_token:
        user = store.get_portal_user(user_id) or {}
        return _start_session(user, request, response, recovery_codes=recovery)
    return {"ok": True, "recovery_codes": recovery}


class MfaDisableRequest(BaseModel):
    password: str = Field(..., max_length=255)
    code: str = Field(..., min_length=6, max_length=20)


@router.post("/portal/mfa/disable")
async def portal_mfa_disable(body: MfaDisableRequest, ctx: PortalDep):
    if ctx.role != "msl":
        raise HTTPException(status_code=403, detail="Two-factor authentication cannot be turned off for this role.")
    user = store.get_portal_user(ctx.user_id) or {}
    with store.session_scope() as db:
        row = db.query(store.PortalUserRow).filter_by(user_id=ctx.user_id).first()
        password_ok = bool(row and store.verify_password(body.password, row.password_hash))
    if not password_ok:
        raise HTTPException(status_code=401, detail="Invalid password or code.")
    try:
        ok = store.mfa_check(ctx.user_id, body.code)
    except store.AccountLocked as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    if not ok or not user:
        raise HTTPException(status_code=401, detail="Invalid password or code.")
    store.mfa_disable(ctx.user_id)
    return {"ok": True}


class DoctorConsents(BaseModel):
    terms_privacy: bool = False
    hcp_declaration: bool = False
    whatsapp_messaging: bool = False
    email_messaging: bool = False


class DoctorRegisterRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    display_name: str = Field(..., min_length=1, max_length=120)
    password: str = Field(..., min_length=1, max_length=128)
    mobile: str = Field(..., max_length=32)
    registration_number: str = Field(..., max_length=40)
    registration_council: str = Field(..., max_length=160)
    registration_year: int
    basic_qualification: str = Field("", max_length=120)
    highest_qualification: str = Field("", max_length=120)
    qualification: str = Field("", max_length=120)  # alias of highest_qualification for older clients
    speciality: str = Field("Ophthalmology", max_length=80)
    institution: str = Field(..., max_length=160)
    practice_type: str = Field("Clinic / private practice", max_length=80)
    city: str = Field(..., max_length=80)
    state: str = Field(..., max_length=80)
    pincode: str = Field(..., max_length=12)
    nmr_id: str = Field("", max_length=40)
    repository_ids: list[str] = Field(default_factory=list, max_length=10)
    default_repository_id: str = ""
    consents: DoctorConsents
    consent_version: str = ""


def build_register_options() -> dict[str, Any]:
    areas = store.list_therapeutic_areas()
    return {
        "therapeutic_areas": [
            {
                "repository_id": a["repository_id"],
                "name": a["name"],
                "slug": a.get("slug") or "",
                "description": a.get("description") or "",
                "scope_keywords": a.get("scope_keywords") or [],
                "enabled": bool(a.get("enabled", True)) and not a.get("coming_soon"),
                "coming_soon": bool(a.get("coming_soon")) or not a.get("enabled", True),
                "sort_order": a.get("sort_order", 0),
            }
            for a in areas
        ],
        "councils": store.list_councils(),
        "specialities": list(doctor_registry.SPECIALITIES),
        "qualifications": list(doctor_registry.QUALIFICATIONS),
        "practice_types": list(doctor_registry.PRACTICE_TYPES),
        "consent_documents": {"version": doctor_registry.CONSENT_VERSION, "items": list(doctor_registry.CONSENT_ITEMS)},
        "compliance_notice": compliance.compliance_notice(),
        "nmr_id_required": bool(get_dmsl_settings().nmr_id_required),
        "password_min_length": get_dmsl_settings().password_min_length,
    }


@router.get("/portal/register/options")
async def portal_register_options():
    """Public: therapeutic areas, pick-lists, consent form and compliance notice for registration."""
    return build_register_options()


@router.post("/portal/register")
async def portal_register(body: DoctorRegisterRequest, request: Request):
    """Creates the account (PENDING_CONTACT_VERIFICATION) and sends email + mobile codes. No session is issued."""
    from digital_msl import otp_delivery

    fields = body.model_dump()
    profile_keys = (
        "mobile", "registration_number", "registration_council", "registration_year", "basic_qualification",
        "highest_qualification", "qualification", "speciality", "institution", "practice_type", "city", "state",
        "pincode", "nmr_id",
    )
    try:
        user = store.register_doctor(
            email=body.email,
            display_name=body.display_name,
            password=body.password,
            repository_ids=body.repository_ids,
            default_repository_id=body.default_repository_id,
            profile={k: fields[k] for k in profile_keys},
            consents=fields["consents"],
            consent_version=body.consent_version,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent", ""),
        )
    except store.DuplicateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    codes = store.issue_contact_otps(user["user_id"])
    await asyncio.to_thread(otp_delivery.send_codes, user["user_id"], codes)
    targets = store.contact_targets(user["user_id"])
    result: dict[str, Any] = {
        "status": "verification_required",
        "user_id": user["user_id"],
        "email_masked": store.mask_email(targets["email"]),
        "mobile_masked": store.mask_mobile(targets["mobile"]),
        "otp_expires_in": store.OTP_TTL_SECONDS,
    }
    if get_dmsl_settings().dev_echo_otp:
        result["dev_codes"] = codes
    return result


class OtpVerifyRequest(BaseModel):
    user_id: str = Field(..., max_length=64)
    channel: str = Field(..., pattern="^(email|mobile)$")
    code: str = Field(..., min_length=4, max_length=12)


class OtpResendRequest(BaseModel):
    user_id: str = Field(..., max_length=64)
    channel: str = Field(..., pattern="^(email|mobile)$")


@router.post("/portal/register/verify")
async def portal_register_verify(body: OtpVerifyRequest):
    try:
        return store.verify_contact_otp(body.user_id, body.channel, body.code)
    except store.OtpError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/portal/register/resend")
async def portal_register_resend(body: OtpResendRequest):
    from digital_msl import otp_delivery

    state = store.contact_state(body.user_id)
    profile = store.get_doctor_profile(body.user_id)
    already = state["email_verified"] if body.channel == "email" else state["mobile_verified"]
    if profile is None or already:
        return {"status": "ok"}  # neutral: never confirm whether an account exists
    try:
        store.can_resend_otp(body.user_id, body.channel)
    except store.OtpError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    codes = store.issue_contact_otps(body.user_id, (body.channel,))
    await asyncio.to_thread(otp_delivery.send_codes, body.user_id, codes)
    result: dict[str, Any] = {"status": "ok", "otp_expires_in": store.OTP_TTL_SECONDS}
    if get_dmsl_settings().dev_echo_otp:
        result["dev_codes"] = codes
    return result


class ConsentUpdateRequest(BaseModel):
    whatsapp_messaging: Optional[bool] = None
    email_messaging: Optional[bool] = None


@router.put("/portal/consents")
async def put_portal_consents(body: ConsentUpdateRequest, ctx: PortalDep, request: Request):
    if ctx.role != "doctor":
        raise HTTPException(status_code=403, detail="Only doctors manage messaging consent.")
    return store.set_channel_consents(
        ctx.user_id,
        {"whatsapp_messaging": body.whatsapp_messaging, "email_messaging": body.email_messaging},
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )


@router.get("/portal/unsubscribe")
async def portal_unsubscribe(token: str = Query("", max_length=1000)):
    """Public one-click email unsubscribe (signed link from the footer of every email)."""
    body = security.verify_token(token, "unsubscribe")
    if not body:
        return HTMLResponse("<h3>This unsubscribe link is invalid or has expired.</h3>", status_code=400)
    user_id = str(body.get("u") or "")
    if store.get_portal_user(user_id):
        store.set_channel_consents(user_id, {"email_messaging": False}, source="email_unsubscribe")
    return HTMLResponse("<h3>You have been unsubscribed from Digital MSL emails.</h3><p>You can turn them back on in your profile.</p>")


@router.post("/portal/certificate")
async def upload_certificate(ctx: PortalDep, file: UploadFile = File(...)):
    """Optional registration certificate (PDF / PNG / JPEG, 5 MB). Stored encrypted; scanned when ClamAV is configured."""
    from digital_msl import certificates

    if ctx.role != "doctor":
        raise HTTPException(status_code=403, detail="Only doctors upload a registration certificate.")
    data = await file.read(certificates.MAX_BYTES + 1)
    try:
        stored, scan_status, media = certificates.store_certificate(data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    store.set_certificate(ctx.user_id, stored, scan_status)
    store.record_audit_event(
        action="certificate_upload", user_id=ctx.user_id, role=ctx.role, entity_type="doctor", entity_id=ctx.user_id,
        detail={"media_type": media, "bytes": len(data), "scan_status": scan_status},
    )
    return {"present": True, "scan_status": scan_status}


@router.get("/compliance/notice")
async def get_compliance_notice(_ctx: PortalDep):
    return compliance.compliance_notice()


@router.post("/portal/logout")
async def portal_logout(ctx: PortalDep, response: Response):
    store.end_login_session(ctx.session_id, ctx.user_id)
    _clear_session_cookies(response)
    return {"ok": True}


@router.post("/portal/logout-all")
async def portal_logout_all(ctx: PortalDep, response: Response):
    """End every session of this user, on every device."""
    count = store.end_all_sessions(ctx.user_id)
    _clear_session_cookies(response)
    return {"ok": True, "sessions_ended": count}


@router.get("/load-documents/context")
async def load_documents_context(ctx: MslDep):
    """State of the Load Documents page (Physical MSL / Administrator): repositories, or exactly why not."""
    return await asyncio.to_thread(load_documents.build_context, ctx.user_id, ctx.role)


@router.get("/ikm/status")
async def ikm_status_endpoint(ctx: MslDep):
    """Administrator / Physical MSL health check that separates: not configured, unreachable, unauthorised,
    and an empty approved-repository allow-list. Never returns secrets."""
    diag = await asyncio.to_thread(load_documents.ikm_diagnosis, deep=True)
    return {
        "configured": diag["configured"],
        "reachable": diag["reachable"],
        "unauthorised": diag["unauthorised"],
        "code": diag["code"],
        "checked_at": diag["checked_at"],
        "missing": diag["missing"] if ctx.role == "admin" else [],
        "repository_count": len(diag["repositories"]),
        "allow_list": load_documents.allow_list_info(),
        "demo_fixture_active": load_documents.demo_active() and not (diag["reachable"] and not diag["unauthorised"]),
    }


@router.get("/me/context")
async def me_context(ctx: PortalDep):
    """Everything the doctor chat needs in one call: areas, verification, and how answers are routed.

    The browser never lists repositories. Which sources can answer is decided here from the doctor's
    areas and DIGITAL_MSL_APPROVED_REPOSITORY_IDS (empty = every question goes to a Physical MSL).
    """
    from digital_msl import standard_replies, upstream

    user = store.get_portal_user(ctx.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    settings = get_dmsl_settings()
    approved = settings.approved_repositories()
    by_id = {a["repository_id"]: a for a in store.list_therapeutic_areas()}
    if user["role"] == "doctor":
        wanted = list(user.get("assigned_repository_ids") or [])
    else:
        wanted = [rid for rid, a in by_id.items() if a.get("active", True)]
    default_id = user.get("default_repository_id") or ""
    areas = []
    for rid in wanted:
        area = by_id.get(rid) or {"repository_id": rid, "name": rid, "description": "", "active": True}
        real = bool(rid) and not rid.startswith("pending:")
        areas.append(
            {
                "repository_id": rid,
                "name": area.get("name") or rid,
                "description": area.get("description") or "",
                "is_default": rid == default_id,
                "enabled": bool(area.get("active", True)),
                "coming_soon": not bool(area.get("active", True)),
                "answers_from_sources": real and ("*" in approved or rid in approved) and bool(area.get("active", True)),
            }
        )
    usable = [a for a in areas if a["enabled"]]
    answerable = [a for a in usable if a["answers_from_sources"]]
    if not approved or not answerable:
        sources = {"status": "not_configured", "message": standard_replies.ROUTED_TO_MSL}
    else:
        ikm = await asyncio.to_thread(upstream.ikm_status)
        sources = (
            {"status": "ok", "message": ""}
            if ikm["reachable"]
            else {
                "status": "unavailable",
                "code": upstream.KNOWLEDGE_SOURCES_UNAVAILABLE,
                "message": upstream.FRIENDLY_MESSAGE,
            }
        )
    return {
        "user": {
            "user_id": user["user_id"],
            "display_name": user["display_name"],
            "role": user["role"],
            "verification_status": user.get("verification_status", ""),
        },
        "verification_status": user.get("verification_status", ""),
        "areas": areas,
        "default_repository_id": default_id or (areas[0]["repository_id"] if areas else ""),
        "has_enabled_areas": bool(usable),
        "routed_to_msl": not answerable,
        "knowledge_sources": sources,
    }


# ------------------------------------------------------------- document approval registry
class DocumentApprovalRequest(BaseModel):
    approval_status: str = Field(..., max_length=20)
    version: str = Field("", max_length=40)
    effective_date: str = Field("", max_length=20)
    expiry_date: str = Field("", max_length=20)
    superseded_by: str = Field("", max_length=120)
    document_title: str = Field("", max_length=255)
    reason: str = Field("", max_length=1000)


def _assert_repository_scope(ctx: Any, repository_id: str) -> None:
    """Admins manage any repository; a Physical MSL only the repositories of their therapeutic areas."""
    if ctx.role == "admin":
        return
    if repository_id not in store.repositories_for_msl(ctx.user_id):
        raise HTTPException(status_code=403, detail="This repository is outside your therapeutic areas.")


@router.get("/document-approvals")
async def list_document_approvals(ctx: MslDep, repository_id: str = Query(..., min_length=1)):
    """IKM documents of one repository with their Digital MSL approval record (null = not registered, so never quoted)."""
    from digital_msl import upstream
    from services.dms_client import DmsClient

    _assert_repository_scope(ctx, repository_id)
    try:
        listing = await asyncio.to_thread(lambda: DmsClient().list_repository_documents(repository_id, limit=500))
    except Exception as exc:
        raise upstream.UpstreamUnavailable(exc) from exc
    approvals = store.get_document_approvals(repository_id)
    documents = []
    for doc in listing.get("documents") or []:
        doc_id = str(doc.get("document_id") or "")
        if not doc_id:
            continue
        documents.append(
            {
                "document_id": doc_id,
                "name": doc.get("original_file_name") or doc.get("document_name") or doc_id,
                "document_type": doc.get("document_type"),
                "ingest_status": doc.get("status"),
                "approval": approvals.get(doc_id),
            }
        )
    settings = get_dmsl_settings()
    allowed = settings.approved_repositories()
    return {
        "repository_id": repository_id,
        "on_allow_list": "*" in allowed or repository_id in allowed,
        "require_approval_metadata": settings.require_approval_metadata,
        "documents": documents,
        "counts": {
            "documents": len(documents),
            "approved": sum(1 for d in documents if (d["approval"] or {}).get("approval_status") == "APPROVED"),
            "not_registered": sum(1 for d in documents if d["approval"] is None),
        },
    }


@router.put("/document-approvals/{repository_id}/{document_id}")
async def put_document_approval(repository_id: str, document_id: str, body: DocumentApprovalRequest, ctx: MslDep):
    _assert_repository_scope(ctx, repository_id)
    try:
        return store.set_document_approval(
            repository_id,
            document_id,
            approval_status=body.approval_status,
            version=body.version,
            effective_date=body.effective_date,
            expiry_date=body.expiry_date,
            superseded_by=body.superseded_by,
            document_title=body.document_title,
            reason=body.reason,
            actor_id=ctx.user_id,
            actor_role=ctx.role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/portal/me")
async def portal_me(ctx: PortalDep):
    payload = store.portal_me_payload(ctx.user_id)
    if not payload:
        raise HTTPException(status_code=404, detail="User not found.")
    if payload["user"].get("role") == "doctor":
        payload["profile"] = store.get_doctor_profile(ctx.user_id)
        payload["consents"] = store.get_consents(ctx.user_id)
    return payload


# ------------------------------------------------------------- doctor registry
def _doctor_visible_to(ctx: Any, doctor: dict[str, Any]) -> None:
    if ctx.role == "msl" and not store.msl_covers_doctor(ctx.user_id, doctor):
        raise HTTPException(status_code=403, detail="This doctor is outside your therapeutic areas.")


@router.get("/doctors")
async def list_doctors(
    ctx: MslDep,
    status: str = "",
    q: str = Query("", max_length=100),
    repository_id: str = "",
    city: str = "",
    sort: str = "registered_at",
    order: str = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
):
    """Registered doctors. Physical MSLs (read-only) see doctors in their therapeutic areas; admins see everyone."""
    if status and status not in doctor_registry.VERIFICATION_STATUSES:
        raise HTTPException(status_code=422, detail="Unknown verification status.")
    result = store.list_doctor_registry(
        viewer_id=ctx.user_id,
        viewer_role=ctx.role,
        status=status,
        query=q,
        repository_id=repository_id,
        city=city,
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
    )
    store.record_audit_event(
        action="doctor_list_view", user_id=ctx.user_id, role=ctx.role, entity_type="doctor",
        detail={"status": status, "q": bool(q), "repository_id": repository_id, "city": city, "returned": len(result["doctors"])},
    )
    return result


def _csv_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@router.get("/doctors/export.csv")
async def export_doctors_csv(ctx: AdminDep):
    import csv

    result = store.list_doctor_registry(viewer_id=ctx.user_id, viewer_role="admin", paginate=False)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "name", "email", "mobile", "registration_number", "council", "registration_year", "highest_qualification",
        "speciality", "institution", "city", "state", "pincode", "therapeutic_areas", "default_area",
        "verification_status", "whatsapp_consent", "email_consent", "registered_at", "last_contact_at",
    ])
    for d in result["doctors"]:
        writer.writerow([_csv_cell(v) for v in (
            d["display_name"], d["email"], d.get("mobile"), d.get("registration_number"), d.get("registration_council"),
            d.get("registration_year"), d.get("highest_qualification"), d.get("speciality"), d.get("institution"),
            d.get("city"), d.get("state"), d.get("pincode"), "; ".join(a["name"] for a in d["areas"]),
            next((a["name"] for a in d["areas"] if a["is_default"]), ""), d["verification_status"],
            d["consents"]["whatsapp_messaging"], d["consents"]["email_messaging"], d["registered_at"], d["last_contact_at"],
        )])
    store.record_audit_event(
        action="doctors_export", user_id=ctx.user_id, role=ctx.role, entity_type="doctor",
        detail={"rows": len(result["doctors"])},
    )
    return Response(
        content=out.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="registered-doctors.csv"'},
    )


@router.get("/doctors/{user_id}")
async def get_doctor(user_id: str, ctx: MslDep):
    detail = store.get_doctor_detail(user_id, mask=ctx.role != "admin")
    if detail is None:
        raise HTTPException(status_code=404, detail="Doctor not found.")
    _doctor_visible_to(ctx, detail)
    store.record_audit_event(
        action="doctor_view", user_id=ctx.user_id, role=ctx.role, entity_type="doctor", entity_id=user_id,
    )
    return detail


@router.get("/doctors/{user_id}/certificate")
async def get_doctor_certificate(user_id: str, ctx: AdminDep):
    """Administrator only; every download is audit-logged."""
    from digital_msl import certificates

    stored, scan_status = store.get_certificate_path(user_id)
    if not stored:
        raise HTTPException(status_code=404, detail="No certificate on file.")
    try:
        data, media = certificates.read_certificate(stored)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="The certificate could not be read.") from exc
    store.record_audit_event(
        action="certificate_view", user_id=ctx.user_id, role=ctx.role, entity_type="doctor", entity_id=user_id,
        detail={"scan_status": scan_status},
    )
    return Response(content=data, media_type=media, headers={"Content-Disposition": 'attachment; filename="registration-certificate"'})


class VerificationRequest(BaseModel):
    status: str = Field(..., pattern="^(VERIFIED|REJECTED|NEEDS_INFO|PENDING_REGISTRATION_VERIFICATION)$")
    reason: str = Field("", max_length=1000)
    notes: str = Field("", max_length=1000)  # alias of reason for older clients


@router.post("/doctors/{user_id}/verification")
async def set_doctor_verification(user_id: str, body: VerificationRequest, ctx: AdminDep):
    """Administrator only. Physical MSLs are read-only on the registry."""
    if store.get_doctor_entry(user_id) is None:
        raise HTTPException(status_code=404, detail="Doctor not found.")
    try:
        return store.set_doctor_verification(
            user_id, body.status, body.reason or body.notes, actor_id=ctx.user_id, actor_role=ctx.role
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class DeactivateRequest(BaseModel):
    reason: str = Field("", max_length=1000)


@router.post("/doctors/{user_id}/deactivate")
async def deactivate_doctor(user_id: str, body: DeactivateRequest, ctx: AdminDep):
    try:
        return store.set_doctor_active(user_id, False, body.reason, actor_id=ctx.user_id, actor_role=ctx.role)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/doctors/{user_id}/reactivate")
async def reactivate_doctor(user_id: str, body: DeactivateRequest, ctx: AdminDep):
    try:
        return store.set_doctor_active(user_id, True, body.reason, actor_id=ctx.user_id, actor_role=ctx.role)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/audit/verify-chain")
async def audit_verify_chain(_ctx: AdminDep):
    """Recompute the audit hash chain and report the first broken entry, if any."""
    return store.verify_audit_chain()


@router.get("/audit/events")
async def audit_events(
    ctx: AdminDep,
    session_id: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    if session_id:
        portal_auth.require_login_session(session_id, "")
    return store.list_audit_events(limit=limit, offset=offset, action=action, session_id=session_id)


@router.get("/portal/users")
async def portal_users(ctx: MslDep, role: Optional[str] = None):
    store.seed_portal_users()
    store.seed_default_group()
    users = store.list_portal_users(role=role)
    if ctx.role != "admin":
        users = [{**u, "whatsapp_phone": store.mask_mobile(u["whatsapp_phone"]) if u.get("whatsapp_phone") else ""} for u in users]
    return {"users": users}


@router.get("/libraries")
async def digital_msl_libraries():
    return {"libraries": store.list_ikm_libraries(), "therapeutic_areas": store.list_therapeutic_areas()}


class TherapeuticAreaRequest(BaseModel):
    name: str = Field(..., min_length=3, max_length=80)
    description: str = ""
    owner_msl_id: str = ""
    default_flag: bool = False
    repository_id: str = ""
    msl_ids: list[str] = Field(default_factory=list)
    scope_keywords: Optional[list[str]] = None


@router.get("/therapeutic-areas")
async def get_therapeutic_areas():
    return {"therapeutic_areas": store.list_therapeutic_areas()}


def _create_area_repository(display_name: str, description: str, keywords: Optional[list[str]] = None) -> str:
    """Create the IKM / DMS repository behind a therapeutic area and return its id."""
    from services.dms_client import DmsClient, DmsError

    repo_name = re.sub(r"[^A-Za-z0-9_-]+", "_", display_name.strip()).strip("_")
    if len(repo_name) < 3:
        raise HTTPException(status_code=422, detail="Therapeutic area name must contain at least 3 letters or digits.")
    settings = {
        "therapeutic_scope": repo_name.lower(),
        "description": description,
        "scope_keywords": keywords or [],
        "transient_connectors": {"enabled": True, "connectors": ["pubmed", "clinicaltrials"]},
        "medical_literacy_research": True,
    }
    client = DmsClient()
    repo = client.create_repository({"name": repo_name, "settings": settings})
    repository_id = str((repo or {}).get("repository_id") or (repo or {}).get("id") or "")
    if repository_id and str((repo or {}).get("status") or "").lower() not in {"active", ""}:
        try:
            client.activate_repository(repository_id)
        except DmsError:
            pass
    if not repository_id:
        raise HTTPException(status_code=502, detail="Therapeutic area was created without an id.")
    return repository_id


@router.post("/therapeutic-areas")
async def post_therapeutic_area(body: TherapeuticAreaRequest, ctx: AdminDep):
    repository_id = body.repository_id.strip()
    if not repository_id:
        try:
            repository_id = _create_area_repository(body.name, body.description, body.scope_keywords)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Could not create therapeutic area: {exc}") from exc
    area = store.upsert_therapeutic_area(
        repository_id=repository_id,
        name=body.name,
        owner_msl_id=body.owner_msl_id,
        default_flag=body.default_flag,
        description=body.description,
        scope_keywords=body.scope_keywords,
    )
    if body.msl_ids or body.owner_msl_id:
        area = store.set_ta_members(repository_id, body.msl_ids or area.get("msl_ids") or [], body.owner_msl_id)
    store.record_audit_event(
        action="ta_create", user_id=ctx.user_id, role=ctx.role, repository_id=repository_id,
        entity_type="therapeutic_area", entity_id=repository_id, detail={"name": body.name},
    )
    return area


@router.post("/therapeutic-areas/seed-defaults")
async def seed_default_therapeutic_areas(ctx: AdminDep):
    """Add the standard eyecare areas as local, disabled ("Coming soon") rows. Idempotent; no IKM calls."""
    return store.seed_default_areas(actor_id=ctx.user_id, role=ctx.role)


class AreaSettingsRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=80)
    description: Optional[str] = Field(None, max_length=1000)
    enabled: Optional[bool] = None
    sort_order: Optional[int] = None
    scope_keywords: Optional[list[str]] = None
    repository_id: Optional[str] = Field(None, max_length=64)


@router.put("/therapeutic-areas/{key}/settings")
async def put_area_settings(key: str, body: AreaSettingsRequest, ctx: AdminDep):
    """Admin edit. Enabling a placeholder links ``repository_id`` or creates the IKM repository first."""
    area = store.get_area(key)
    if area is None:
        raise HTTPException(status_code=404, detail="Therapeutic area not found.")
    repository_id = (body.repository_id or "").strip()
    if body.enabled and area["repository_id"].startswith("pending:") and not repository_id:
        try:
            repository_id = await asyncio.to_thread(
                _create_area_repository,
                body.name or area["name"],
                body.description if body.description is not None else area["description"],
                body.scope_keywords if body.scope_keywords is not None else area["scope_keywords"],
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Could not create the IKM repository: {exc}") from exc
    try:
        return store.update_area_settings(
            key,
            actor_id=ctx.user_id,
            role=ctx.role,
            name=body.name,
            description=body.description,
            enabled=body.enabled,
            sort_order=body.sort_order,
            scope_keywords=body.scope_keywords,
            repository_id=repository_id or None,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class TaActiveRequest(BaseModel):
    active: bool


@router.put("/therapeutic-areas/{repository_id}/active")
async def put_therapeutic_area_active(repository_id: str, body: TaActiveRequest, ctx: AdminDep):
    try:
        return store.set_therapeutic_area_active(
            repository_id,
            body.active,
            actor_id=ctx.user_id,
            role=ctx.role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/therapeutic-areas/{repository_id}")
async def delete_therapeutic_area_route(repository_id: str, ctx: AdminDep):
    try:
        if not store.delete_therapeutic_area(repository_id, actor_id=ctx.user_id, role=ctx.role):
            raise HTTPException(status_code=404, detail="Therapeutic area not found.")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "repository_id": repository_id}


@router.get("/therapeutic-areas/{repository_id}/folder-stats")
async def get_folder_stats(repository_id: str, ctx: PortalDep):
    portal_auth.assert_repository_access(ctx.user_id, ctx.role, repository_id)
    return {"document_counts": store.folder_document_counts(repository_id)}


ALLOWED_PORTAL_UPLOAD = {".pdf", ".docx", ".doc", ".txt"}
MAX_PORTAL_UPLOAD_BYTES = 200 * 1024 * 1024


@router.post("/therapeutic-areas/{repository_id}/documents/upload")
async def upload_therapeutic_area_documents(
    repository_id: str,
    ctx: PortalDep,
    folder_id: str = Form(""),
    files: list[UploadFile] = File(...),
):
    demo_fixture = load_documents.is_demo_repository(repository_id)
    if demo_fixture:
        if ctx.role not in {"msl", "admin"}:
            raise HTTPException(status_code=403, detail="Insufficient role for this operation.")
    else:
        portal_auth.assert_repository_access(ctx.user_id, ctx.role, repository_id)
    if folder_id and not demo_fixture and not store.folder_belongs_to_repository(folder_id, repository_id):
        raise HTTPException(status_code=422, detail="folder_id does not belong to this repository.")
    payloads: list[tuple[str, bytes, str]] = []
    import mimetypes

    for upload in files:
        filename = upload.filename or "document"
        ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
        if ext not in ALLOWED_PORTAL_UPLOAD:
            raise HTTPException(status_code=422, detail=f"Unsupported file type: {filename}")
        content = await upload.read()
        if len(content) > MAX_PORTAL_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"File too large: {filename}")
        mime = upload.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        payloads.append((filename, content, mime))
    if demo_fixture:  # demo mode only: kept in memory, labelled as demo data, never sent to IKM
        recorded = [load_documents.record_demo_upload(name, len(data), ctx.user_id) for name, data, _mime in payloads]
        for doc in recorded:
            store.record_audit_event(
                action="document_upload", user_id=ctx.user_id, role=ctx.role, session_id=ctx.session_id,
                repository_id=repository_id, entity_type="document", entity_id=doc["document_id"],
                detail={"demo": True, "file_name": doc["original_file_name"]},
            )
        return {"documents": recorded, "demo": True}
    try:
        from services.dms_client import DmsClient

        result = DmsClient().upload_documents(
            files=[(name, io.BytesIO(data), mime) for name, data, mime in payloads],
            repository_id=repository_id,
            submit_for_processing=True,
            metadata={"uploaded_by": ctx.user_id, "source": "digital_msl_portal"},
        )
    except Exception as exc:
        from digital_msl import upstream as _up

        raise _up.UpstreamUnavailable(
            exc, code="DOCUMENT_UPLOAD_UNAVAILABLE", message="We could not send the file to the document store. Please try again."
        ) from exc
    uploaded = result.get("documents") if isinstance(result, dict) else result
    if isinstance(uploaded, dict):
        uploaded = uploaded.get("documents") or [uploaded]
    for doc in uploaded or []:
        if not isinstance(doc, dict):
            continue
        doc_id = str(doc.get("document_id") or doc.get("id") or "")
        if not doc_id:
            continue
        store.set_document_folder(doc_id, repository_id, folder_id or "")
        store.record_audit_event(
            action="document_upload",
            user_id=ctx.user_id,
            role=ctx.role,
            session_id=ctx.session_id,
            repository_id=repository_id,
            entity_type="document",
            entity_id=doc_id,
            detail={"folder_id": folder_id or "", "file_name": doc.get("original_file_name") or ""},
        )
    return result


@router.put("/therapeutic-areas/{repository_id}/members")
async def put_therapeutic_area_members(repository_id: str, body: TherapeuticAreaRequest, _ctx: AdminDep):
    return store.set_ta_members(repository_id, body.msl_ids, body.owner_msl_id)


class FolderCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    parent_folder_id: str = ""


class FolderRenameRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)


@router.post("/therapeutic-areas/{repository_id}/folders")
async def post_therapeutic_area_folder(
    repository_id: str,
    body: FolderCreateRequest,
    session_id: str = Query(""),
    user_id: str = Query(""),
):
    if session_id and user_id:
        portal_auth.enforce_session_role(session_id, user_id, "admin", "msl", repository_id=repository_id)
    try:
        return store.add_ta_folder(repository_id, body.name, body.parent_folder_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/therapeutic-areas/folders/{folder_id}")
async def put_therapeutic_area_folder(folder_id: str, body: FolderRenameRequest):
    try:
        updated = store.rename_ta_folder(folder_id, body.name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="Folder not found.")
    return updated


@router.delete("/therapeutic-areas/folders/{folder_id}")
async def delete_therapeutic_area_folder(folder_id: str):
    try:
        if not store.delete_ta_folder(folder_id):
            raise HTTPException(status_code=404, detail="Folder not found.")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True}


class DefaultTaRequest(BaseModel):
    repository_id: str


@router.put("/portal/users/{user_id}/default-ta")
async def put_default_ta(user_id: str, body: DefaultTaRequest, ctx: PortalDep):
    if ctx.role != "admin" and ctx.user_id != user_id:
        raise HTTPException(status_code=403, detail="Cannot change another user's default area.")
    if ctx.role == "doctor":
        portal_auth.assert_repository_access(ctx.user_id, ctx.role, body.repository_id)
    user = store.set_doctor_default_ta(user_id, body.repository_id)
    if not user:
        raise HTTPException(status_code=404, detail="Doctor not found.")
    return user


class SelfAssignRequest(BaseModel):
    msl_id: str
    session_id: str = ""
    user_id: str = ""


@router.post("/cases/{case_id}/self-assign")
async def post_self_assign(case_id: str, body: SelfAssignRequest):
    if body.session_id and body.user_id:
        portal_auth.enforce_session_role(body.session_id, body.user_id, "msl")
    elif body.session_id or body.user_id:
        raise HTTPException(status_code=401, detail="session_id and user_id must be supplied together.")
    try:
        case = store.self_assign_case(case_id, body.msl_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not case:
        raise HTTPException(status_code=404, detail="Case or Physical MSL not found.")
    return case


@router.post("/portal/users")
async def create_portal_user(body: PortalUserRequest, ctx: AdminDep):
    """Admin provisioning. Returns a one-time temporary password when none is supplied."""
    import secrets as _secrets
    import string

    password = body.password
    temporary = ""
    if password:
        try:
            doctor_registry.validate_password(password)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        alphabet = string.ascii_letters + string.digits
        temporary = "".join(_secrets.choice(alphabet) for _ in range(12)) + "Aa1!"
        password = temporary
    try:
        user = store.upsert_portal_user(
            email=body.email,
            display_name=body.display_name,
            role=body.role,
            assigned_msl_id=body.assigned_msl_id,
            password=password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    store.record_audit_event(
        action="user_provisioned", user_id=ctx.user_id, role=ctx.role,
        entity_type="user", entity_id=user["user_id"], detail={"role": body.role},
    )
    return {**user, "temporary_password": temporary} if temporary else user


@router.post("/portal/assignments")
async def assign_doctor(body: AssignmentRequest, _ctx: AdminDep):
    user = None
    if body.msl_id.strip():
        user = store.assign_doctor_msl(body.doctor_id, body.msl_id)
        if not user:
            raise HTTPException(status_code=404, detail="Doctor or Physical MSL not found.")
    if body.repository_ids is not None:
        user = store.assign_doctor_repositories(body.doctor_id, body.repository_ids)
        if not user:
            raise HTTPException(status_code=404, detail="Doctor not found.")
    if not user:
        raise HTTPException(status_code=422, detail="Provide msl_id or repository_ids.")
    return user


class GroupCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)


class GroupMembersRequest(BaseModel):
    msl_ids: list[str] = Field(default_factory=list)


class DoctorGroupRequest(BaseModel):
    doctor_id: str
    group_id: str


class ProfileUpdateRequest(BaseModel):
    user_id: str
    display_name: Optional[str] = None
    password: Optional[str] = None
    current_password: Optional[str] = None


@router.get("/groups")
async def get_groups(_ctx: MslDep):
    store.seed_portal_users()
    store.seed_default_group()
    return {"groups": store.list_groups()}


@router.post("/groups")
async def post_group(body: GroupCreateRequest, _ctx: AdminDep):
    return store.create_group(body.name)


@router.put("/groups/{group_id}/members")
async def put_group_members(group_id: str, body: GroupMembersRequest, _ctx: AdminDep):
    group = store.set_group_members(group_id, body.msl_ids)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found.")
    return group


@router.post("/portal/group-assignments")
async def post_doctor_group(body: DoctorGroupRequest, _ctx: AdminDep):
    user = store.assign_doctor_group(body.doctor_id, body.group_id)
    if not user:
        raise HTTPException(status_code=404, detail="Doctor or group not found.")
    return user


class DoctorReposRequest(BaseModel):
    repository_ids: list[str] = Field(default_factory=list)
    doctor_id: str = ""


@router.put("/portal/users/{user_id}/repositories")
async def put_doctor_repositories(user_id: str, body: DoctorReposRequest, _ctx: AdminDep):
    user = store.assign_doctor_repositories(user_id, body.repository_ids)
    if not user:
        raise HTTPException(status_code=404, detail="Doctor not found.")
    return user


@router.post("/portal/repository-assignments")
async def post_doctor_repositories(body: DoctorReposRequest, _ctx: AdminDep):
    doctor_id = body.doctor_id.strip()
    if not doctor_id:
        raise HTTPException(status_code=422, detail="doctor_id is required.")
    user = store.assign_doctor_repositories(doctor_id, body.repository_ids)
    if not user:
        raise HTTPException(status_code=404, detail="Doctor not found.")
    return user


@router.put("/portal/profile")
async def put_profile(body: ProfileUpdateRequest, ctx: PortalDep):
    if body.user_id.strip() != ctx.user_id:
        raise HTTPException(status_code=403, detail="Cannot update another user's profile.")
    try:
        user = store.update_portal_profile(
            body.user_id,
            display_name=body.display_name,
            password=body.password,
            current_password=body.current_password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return user


def _own_or_admin(ctx: Any, user_id: str) -> None:
    if ctx.role != "admin" and ctx.user_id != user_id:
        raise HTTPException(status_code=403, detail="Cannot access another user's notifications.")


@router.get("/notifications")
async def get_notifications(user_id: str, ctx: PortalDep, unread_only: bool = False):
    _own_or_admin(ctx, user_id)
    return {"notifications": store.list_notifications(user_id, unread_only=unread_only)}


@router.post("/notifications/read")
async def post_notifications_read(user_id: str, ctx: PortalDep, notification_id: Optional[str] = None):
    _own_or_admin(ctx, user_id)
    store.mark_notifications_read(user_id, notification_id)
    return {"ok": True}


@router.get("/cases/{case_id}")
async def get_case(case_id: str):
    case = store.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found.")
    trace = store.get_trace(case["trace_id"]) if case.get("trace_id") else None
    conversation = store.case_conversation_detail(case, role="msl")
    return {**case, "trace": trace, "conversation": conversation}


@router.post("/cases/{case_id}/resolve")
async def resolve_case(case_id: str, body: ResolveCaseRequest, _ctx: MslDep):
    """Physical MSL resolves a case; the reply goes back on the doctor's original channel."""
    case = store.resolve_case(case_id, body.resolution)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found.")
    delivery: dict[str, Any] = {}
    if body.reply_to_requester:
        original = InboundMessage(
            message_id=case["source_message_id"],
            correlation_id=case["correlation_id"],
            channel=case["channel"],
            external_sender_id=case.get("external_sender_id") or "",
            user_id=case.get("doctor_id") or "",
            repository_id="",
            message_text=case.get("question") or "",
        )
        reply = AgentResponse(
            message_id=original.message_id,
            correlation_id=original.correlation_id,
            conversation_id=original.conversation_id,
            channel=original.channel,
            status="answered",
            decision="escalate_to_msl",
            answer=body.resolution,
            overall_score=0.0,
            threshold=get_dmsl_settings().confidence_threshold,
            msl_case_id=case_id,
        )
        delivery = adapter_for(original.channel).deliver(original, reply)
        if original.channel == "web":
            delivery["detail"] = "Web replies are shown in the portal case history."
    return {**case, "delivery": delivery}


@router.get("/outbox")
async def list_outbox(_ctx: AdminDep, channel: Optional[str] = None, limit: int = 50):
    return {"messages": store.list_outbox(channel=channel, limit=limit)}


@router.post("/mailbox/poll")
async def poll_mailbox_now(_ctx: AdminDep):
    """Process unread and recent inbox questions immediately (admin)."""
    from digital_msl.mailbox import ingest_unseen_once

    count = await ingest_unseen_once()
    return {"handled": count}


# ------------------------------------------------------------- conversations
@router.get("/conversations")
async def list_conversations(
    ctx: PortalDep,
    user_id: Optional[str] = None,
    role: Optional[str] = None,
    doctor_id: Optional[str] = None,
    status: Optional[str] = None,
    channel: Optional[str] = None,
    search: Optional[str] = None,
    session_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    admin_session_id: str = Query(""),
    admin_user_id: str = Query(""),
):
    """List conversations; portal users are scoped to their session identity."""
    effective_role = ctx.role
    effective_user_id: Optional[str] = ctx.user_id
    effective_doctor_id = doctor_id
    if ctx.role == "admin":
        if admin_session_id and admin_user_id:
            portal_auth.enforce_session_role(admin_session_id, admin_user_id, "admin")
        effective_role = role or "admin"
        effective_user_id = user_id
    elif ctx.role == "doctor":
        effective_role = "doctor"
        effective_user_id = ctx.user_id
        effective_doctor_id = None
    elif ctx.role == "msl":
        effective_role = "msl"
        effective_user_id = ctx.user_id
        effective_doctor_id = None
    return store.list_conversations(
        user_id=effective_user_id,
        role=effective_role or "admin",
        doctor_id=effective_doctor_id,
        status=status,
        channel=channel,
        search=search,
        session_id=session_id,
        limit=limit,
        offset=offset,
    )


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    ctx: PortalDep,
    user_id: Optional[str] = None,
    role: Optional[str] = None,
):
    """Retrieve conversation timeline; doctors may only access their own threads."""
    effective_role = ctx.role if ctx.role in {"doctor", "msl"} else (role or ctx.role or "admin")
    effective_user = ctx.user_id if ctx.role in {"doctor", "msl"} else user_id
    try:
        conv = store.get_conversation_detail(conversation_id, user_id=effective_user, role=effective_role)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return conv


# --------------------------------------------------------- response workspace
@router.post("/cases/{case_id}/draft")
async def save_case_draft(case_id: str, body: SaveDraftRequest, _ctx: MslDep):
    """Save or autosave Physical MSL response draft and references."""
    case = store.save_case_draft(
        case_id,
        author_id=body.author_id,
        draft_response=body.draft_response,
        references=body.references,
        claims=body.claims,
    )
    if not case:
        raise HTTPException(status_code=404, detail="Case not found.")
    return case


@router.post("/cases/{case_id}/submit-review")
async def submit_case_review(case_id: str, body: ReviewActionRequest, _ctx: MslDep):
    """Submit drafted response for scientific/compliance review."""
    case = store.submit_case_review(case_id, author_id=body.reviewer_id, notes=body.notes)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found.")
    return case


@router.post("/cases/{case_id}/approve")
async def approve_case_draft(case_id: str, body: ReviewActionRequest, _ctx: MslDep):
    """Approve MSL draft for sending to the doctor."""
    case = store.approve_case_draft(case_id, reviewer_id=body.reviewer_id, notes=body.notes)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found.")
    return case


@router.post("/cases/{case_id}/reject")
async def reject_case_draft(case_id: str, body: ReviewActionRequest, _ctx: MslDep):
    """Reject MSL draft with review feedback."""
    case = store.reject_case_draft(case_id, reviewer_id=body.reviewer_id, notes=body.notes)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found.")
    return case


@router.post("/cases/{case_id}/send-response")
async def send_case_response(case_id: str, body: SendResponseRequest, _ctx: MslDep):
    """Send approved MSL response to the doctor across their original channel."""
    try:
        case = store.send_case_response(case_id, sender_id=body.sender_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not case:
        raise HTTPException(status_code=404, detail="Case not found.")

    delivery: dict[str, Any] = {}
    if body.reply_to_requester:
        original = InboundMessage(
            message_id=case["source_message_id"],
            correlation_id=case["correlation_id"],
            channel=case["channel"],
            external_sender_id=case.get("external_sender_id") or "",
            user_id=case.get("doctor_id") or "",
            repository_id="",
            message_text=case.get("question") or "",
        )
        reply = AgentResponse(
            message_id=original.message_id,
            correlation_id=original.correlation_id,
            conversation_id=case.get("conversation_id") or original.conversation_id,
            channel=original.channel,
            status="answered",
            decision="escalate_to_msl",
            answer=case["resolution"] or "",
            overall_score=1.0,
            threshold=get_dmsl_settings().confidence_threshold,
            msl_case_id=case_id,
        )
        delivery = adapter_for(original.channel).deliver(original, reply)
        if original.channel == "web":
            delivery["detail"] = "Web replies are shown in the portal case history."
    return {**case, "delivery": delivery}
