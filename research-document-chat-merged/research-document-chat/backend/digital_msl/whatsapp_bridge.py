"""WhatsApp Cloud API helpers: webhook parsing, routing, and outbound send."""
from __future__ import annotations

import asyncio
import contextvars
import logging
import re
import uuid
from contextlib import contextmanager
from typing import Any, Optional

import requests

from digital_msl import store
from digital_msl.channels import _active_repository_ids
from digital_msl.config import get_dmsl_settings

logger = logging.getLogger(__name__)

_whatsapp_force_mock: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "whatsapp_force_mock", default=False
)


def whatsapp_mock_delivery_enabled() -> bool:
    settings = get_dmsl_settings()
    return bool(settings.whatsapp_mock or _whatsapp_force_mock.get())


@contextmanager
def whatsapp_demo_delivery():
    """Treat outbound WhatsApp as delivered in-portal (no Meta HTTP) for this request."""
    token = _whatsapp_force_mock.set(True)
    try:
        yield
    finally:
        _whatsapp_force_mock.reset(token)


def normalize_phone(phone: str) -> str:
    raw = str(phone or "").strip()
    if not raw:
        return ""
    if raw.endswith("@g.us"):
        return raw
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return raw
    if len(digits) == 10:
        digits = "91" + digits
    return f"+{digits}"


def whatsapp_repository_candidates(sender: str, body: str = "") -> tuple[list[str], str]:
    """Therapeutic areas for a WhatsApp sender (registered doctor phone or active areas)."""
    phone = normalize_phone(sender)
    portal = store.get_portal_user_by_whatsapp(phone) if phone else None
    user_id = str((portal or {}).get("user_id") or phone or sender or "")
    if portal and portal.get("role") == "doctor":
        assigned = [str(item) for item in (portal.get("assigned_repository_ids") or []) if item]
        default_id = str(portal.get("default_repository_id") or "")
        if default_id:
            return [default_id], user_id
        if assigned:
            return assigned, user_id
    settings = get_dmsl_settings()
    configured = settings.default_repository_ids()
    if len(configured) == 1:
        return [configured[0]], user_id
    if len(configured) > 1:
        return configured, user_id
    return _active_repository_ids(), user_id


async def resolve_whatsapp_target(sender: str, body: str = "") -> tuple[str, str]:
    candidates, user_id = whatsapp_repository_candidates(sender, body)
    if not candidates:
        return "", user_id
    if len(candidates) == 1:
        return candidates[0], user_id
    portal = store.get_portal_user_by_whatsapp(normalize_phone(sender)) if sender else None
    if portal and portal.get("role") == "doctor":
        # QueryAgent searches the doctor's assigned libraries itself; skip the extra search.
        return candidates[0], user_id
    from digital_msl.agents.query_agent import default_retrieve_client
    from digital_msl.evidence import retrieve_evidence

    client = default_retrieve_client()
    query = (body or "").strip()
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
            logger.warning("WhatsApp retrieval failed for therapeutic area %s.", repository_id)
            continue
        score = max((chunk.retrieval_score for chunk in outcome.chunks), default=0.0)
        if outcome.chunks and score >= best_score:
            best_id, best_score = repository_id, score
    return best_id, user_id


def send_whatsapp_text(recipient: str, body: str) -> tuple[str, str]:
    """Send a text message via configured WhatsApp HTTP API (Meta Cloud API shape)."""
    settings = get_dmsl_settings()
    to = normalize_phone(recipient)
    if to.endswith("@g.us"):
        return "failed", "WhatsApp Cloud API does not support group JIDs as send targets; notify MSL phones individually."
    text = (body or "")[:4000]
    if whatsapp_mock_delivery_enabled():
        mock_id = f"mock_wamid_{uuid.uuid4().hex[:16]}"
        logger.info("WhatsApp mock send to %s (%s chars)", to, len(text))
        return "sent", f"mock:{mock_id}"
    if not settings.whatsapp_api_url or not to:
        return "queued_no_provider", "DIGITAL_MSL_WHATSAPP_API_URL not configured"
    try:
        reply = requests.post(
            settings.whatsapp_api_url,
            headers={"Authorization": f"Bearer {settings.whatsapp_token}"},
            json={
                "messaging_product": "whatsapp",
                "to": to.lstrip("+"),
                "type": "text",
                "text": {"body": text, "preview_url": False},
            },
            timeout=30,
        )
        reply.raise_for_status()
        return "sent", reply.text[:500]
    except Exception as exc:
        logger.warning("WhatsApp delivery failed to %s: %s", to, exc)
        return "failed", str(exc)


def send_whatsapp_document(
    recipient: str, filename: str, data: bytes, media_type: str, *, caption: str = ""
) -> tuple[str, str]:
    """Upload a file to the Cloud API media endpoint and send it as a document message."""
    settings = get_dmsl_settings()
    to = normalize_phone(recipient)
    if whatsapp_mock_delivery_enabled():
        return "sent", f"mock:{uuid.uuid4().hex[:16]}"
    api_url = (settings.whatsapp_api_url or "").rstrip("/")
    if not api_url.endswith("/messages") or not to or to.endswith("@g.us"):
        return "queued_no_provider", "WhatsApp document send needs a Cloud API .../messages URL and a phone recipient"
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}
    try:
        upload = requests.post(
            f"{api_url[: -len('/messages')]}/media",
            headers=headers,
            data={"messaging_product": "whatsapp", "type": media_type},
            files={"file": (filename, data, media_type)},
            timeout=60,
        )
        upload.raise_for_status()
        media_id = str(upload.json().get("id") or "")
        if not media_id:
            return "failed", f"media upload returned no id: {upload.text[:200]}"
        sent = requests.post(
            api_url,
            headers=headers,
            json={
                "messaging_product": "whatsapp",
                "to": to.lstrip("+"),
                "type": "document",
                "document": {"id": media_id, "filename": filename, "caption": caption[:1000]},
            },
            timeout=30,
        )
        sent.raise_for_status()
        return "sent", sent.text[:500]
    except Exception as exc:
        logger.warning("WhatsApp document delivery failed to %s: %s", to, exc)
        return "failed", str(exc)


def msl_notify_phones() -> list[str]:
    settings = get_dmsl_settings()
    raw = (settings.whatsapp_msl_notify_phones or "").strip()
    if not raw:
        return []
    return [normalize_phone(item) for item in raw.split(",") if normalize_phone(item)]


def notify_msl_whatsapp_group(
    *,
    case_id: str,
    question: str,
    requester: str,
    group_label: str = "",
) -> int:
    """Ping configured Physical MSL WhatsApp numbers when a WhatsApp case is escalated."""
    phones = msl_notify_phones()
    if not phones:
        return 0
    label = group_label or get_dmsl_settings().whatsapp_demo_group_name or "Digital MSL team"
    preview = (question or "").strip().replace("\n", " ")[:220]
    body = (
        f"[{label}] New WhatsApp question escalated to Physical MSL.\n"
        f"Case: {case_id}\n"
        f"From: {normalize_phone(requester) or requester}\n"
        f"Question: {preview}\n\n"
        "Open the Digital MSL portal → Questions queue to review Digital MSL draft and send your reply."
    )
    sent = 0
    for phone in phones:
        status, _detail = send_whatsapp_text(phone, body)
        if status == "sent":
            sent += 1
    return sent


def parse_meta_webhook(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a Meta WhatsApp Business webhook body into simple inbound dicts."""
    messages: list[dict[str, Any]] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            for item in value.get("messages") or []:
                if item.get("type") != "text":
                    continue
                text_obj = item.get("text") or {}
                body = str(text_obj.get("body") or "").strip()
                sender = str(item.get("from") or "")
                msg_id = str(item.get("id") or "")
                if not body or not sender:
                    continue
                messages.append(
                    {
                        "from": sender,
                        "text": body,
                        "id": msg_id,
                        "message_id": msg_id,
                        "profile_name": _contact_name(value.get("contacts") or [], sender),
                    }
                )
    return messages


def _contact_name(contacts: list[dict[str, Any]], wa_id: str) -> str:
    for contact in contacts:
        if str(contact.get("wa_id") or "") == wa_id:
            profile = contact.get("profile") or {}
            return str(profile.get("name") or "").strip()
    return ""


def subscribe_app_to_waba(waba_id: str = "") -> tuple[bool, str]:
    """Link this Meta app to the WABA so inbound message webhooks are delivered."""
    settings = get_dmsl_settings()
    account_id = (waba_id or settings.whatsapp_waba_id or "").strip()
    token = (settings.whatsapp_token or "").strip()
    if not account_id:
        return False, "DIGITAL_MSL_WHATSAPP_WABA_ID not set (use 1666070088555455 from your webhook entry id)"
    if not token:
        return False, "DIGITAL_MSL_WHATSAPP_TOKEN not set"
    url = f"https://graph.facebook.com/v21.0/{account_id}/subscribed_apps"
    try:
        reply = requests.post(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        reply.raise_for_status()
        return True, reply.text[:500]
    except Exception as exc:
        body = ""
        if hasattr(exc, "response") and exc.response is not None:
            body = getattr(exc.response, "text", "")[:500]
        return False, f"{exc} {body}".strip()


def verify_meta_signature(raw_body: bytes, signature_header: str) -> bool:
    """Optional HMAC verify (X-Hub-Signature-256) when app secret is configured."""
    secret = (get_dmsl_settings().whatsapp_app_secret or "").strip()
    if not secret or not signature_header:
        return True
    import hashlib
    import hmac

    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    provided = signature_header.replace("sha256=", "").strip()
    return hmac.compare_digest(expected, provided)
