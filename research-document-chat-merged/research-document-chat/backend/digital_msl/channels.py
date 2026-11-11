"""Channel adapters: normalize inbound requests and deliver replies on the same channel.

Each adapter turns a provider payload into the canonical ``InboundMessage`` and knows how
to send an ``AgentResponse`` back. Email and WhatsApp send through a configured provider
(SMTP / WhatsApp Business-style HTTP API); without one, the reply is stored in the outbox
table with status ``queued_no_provider`` so nothing is silently lost.
"""
from __future__ import annotations

import logging
import re
import smtplib
from email.message import EmailMessage
from typing import Any

import requests

from digital_msl import compliance, store
from digital_msl.config import get_dmsl_settings
from digital_msl.contracts import AgentResponse, Claim, InboundMessage

logger = logging.getLogger(__name__)


def _mail_secret(value: str) -> str:
    secret = str(value or "").strip().strip("'\"")
    if re.fullmatch(r"(?:[a-z]{4}\s+){3}[a-z]{4}", secret, re.I):
        return secret.replace(" ", "")
    return secret


def references(claims: list[Claim]) -> list[dict[str, Any]]:
    """Unique sources cited by the claims, numbered in order of first use."""
    refs: list[dict[str, Any]] = []
    index: dict[str, int] = {}
    for claim in claims:
        for ev in claim.evidence:
            key = f"{ev.document_id or ev.document_name}|{ev.page_no}|{ev.paragraph}"
            if key not in index:
                index[key] = len(refs) + 1
                refs.append(
                    {
                        "ref": index[key],
                        "document_name": ev.document_name,
                        "page_no": ev.page_no,
                        "paragraph": ev.paragraph,
                        "key": key,
                    }
                )
    return refs


def _claim_bullets(response: AgentResponse, refs: list[dict[str, Any]]) -> str:
    ref_by_key = {r["key"]: r["ref"] for r in refs}
    lines = []
    for claim in response.claims:
        marks = sorted(
            {ref_by_key[f"{ev.document_id or ev.document_name}|{ev.page_no}|{ev.paragraph}"] for ev in claim.evidence}
        )
        lines.append(f"- {claim.claim_text} " + "".join(f"[{m}]" for m in marks))
    return "\n".join(lines)


def doctor_last_name(message: InboundMessage) -> str:
    portal = None
    if message.user_id and "@" not in message.user_id:
        portal = store.get_portal_user(message.user_id)
    if portal is None and message.external_sender_id and "@" in message.external_sender_id:
        portal = store.get_portal_user_by_email(message.external_sender_id)
    name = str((portal or {}).get("display_name") or message.sender_name or "")
    name = re.sub(r"^dr\.?\s+", "", name, flags=re.I).strip(" ,")
    if name:
        return name.split()[-1]
    local = (message.external_sender_id or "doctor").split("@")[0]
    token = re.split(r"[._+\-]", local)[-1] or "Colleague"
    return token[:1].upper() + token[1:]


def email_topic(message: InboundMessage) -> str:
    subject = re.sub(r"^(re|fw|fwd)\s*:\s*", "", message.subject or "", flags=re.I).strip()
    if subject and subject.lower() not in {"question", "query", "medical information"}:
        return subject[:120]
    text = re.sub(r"\s+", " ", message.message_text or "").strip()
    return text[:80].rstrip(" ?.") or "your question"


_SIGNATURE = re.compile(
    r"(?:^|\n)\s*(?:thanks\s*(?:&|and)\s*regards|thanks and regards|best regards|kind regards|warm regards|sincerely|regards,)\b",
    re.I,
)


def plain_email_question(body: str, subject: str = "") -> str:
    """Keep the new question and drop the signature and quoted reply history."""
    kept: list[str] = []
    for line in str(body or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(">"):
            break
        if re.match(r"^On .+wrote:\s*$", stripped, re.I):
            break
        if stripped.lower().startswith("-----original message-----"):
            break
        if _SIGNATURE.search(stripped):
            break
        kept.append(line)
    text = "\n".join(kept).strip()
    text = _SIGNATURE.split(text, maxsplit=1)[0].strip()
    return text or str(subject or "").strip()


def _active_repository_ids() -> list[str]:
    return [
        str(area["repository_id"])
        for area in store.list_therapeutic_areas()
        if area.get("active", True) and area.get("repository_id")
    ]


def email_repository_candidates(sender: str, subject: str = "", body: str = "") -> tuple[list[str], str]:
    """Libraries an email may be answered from. The sender never has to name a repository.

    A registered doctor is limited to the therapeutic areas on their account.
    Anyone else is answered from the active therapeutic areas.
    """
    portal = store.get_portal_user_by_email(sender) if sender and "@" in sender else None
    user_id = str((portal or {}).get("user_id") or sender or "")
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


def repository_for_email(sender: str, subject: str = "", body: str = "") -> tuple[str, str]:
    """Single library when the choice is already unique. Several libraries need a search."""
    candidates, user_id = email_repository_candidates(sender, subject, body)
    if len(candidates) == 1:
        return candidates[0], user_id
    return "", user_id


def render_medical_information_letter(message: InboundMessage, response: AgentResponse) -> str:
    from digital_msl.summary import ComposedAnswer, format_email_letter, topic_phrase

    if response.decision == "respond" and (response.presentation_body or response.claims):
        composed = ComposedAnswer(
            body=response.presentation_body or response.answer,
            reference_lines=list(response.presentation_references or []),
            topic_phrase=topic_phrase(message.message_text or "", message),
        )
        return format_email_letter(message, response, composed)
    composed = ComposedAnswer(
        body=(response.answer or "").strip(),
        reference_lines=[],
        topic_phrase=topic_phrase(message.message_text or "", message),
    )
    return format_email_letter(message, response, composed)


def evidence_attachments(
    response: AgentResponse, *, require_smtp: bool = True, limit: int = 0
) -> list[tuple[str, bytes, str]]:
    from digital_msl.attachments import evidence_attachments as load_attachments

    return load_attachments(response, require_smtp=require_smtp, limit=limit)


def _attach_files_to_email(email: EmailMessage, files: list[tuple[str, bytes, str]]) -> None:
    for filename, data, media in files:
        media = (media or "application/octet-stream").split(";")[0].strip().lower()
        if media == "application/pdf":
            email.add_attachment(data, maintype="application", subtype="pdf", filename=filename)
        elif "wordprocessingml" in media:
            email.add_attachment(
                data,
                maintype="application",
                subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
                filename=filename,
            )
        else:
            maintype, _, subtype = media.partition("/")
            email.add_attachment(
                data,
                maintype=maintype or "application",
                subtype=subtype or "octet-stream",
                filename=filename,
            )


def render_answer_text(response: AgentResponse) -> str:
    """User-visible answer: synthesized body plus a compact reference block."""
    if response.decision != "respond":
        return response.answer
    from digital_msl.summary import ComposedAnswer, compact_references, format_web_answer

    if response.presentation_body:
        composed = ComposedAnswer(
            body=response.presentation_body,
            reference_lines=list(response.presentation_references or []),
            topic_phrase="",
        )
        return format_web_answer(composed)
    refs = compact_references(response.claims)
    body = " ".join(c.claim_text.strip() for c in response.claims if c.claim_text.strip()) or (response.answer or "").strip()
    return format_web_answer(ComposedAnswer(body=body, reference_lines=refs, topic_phrase=""))


def resolve_recipient(message: InboundMessage, channel: str):
    """The registered portal user behind a message, if any."""
    user = None
    if message.user_id and "@" not in message.user_id:
        user = store.get_portal_user(message.user_id)
    sender = message.external_sender_id or ""
    if user is None and channel == "email" and "@" in sender:
        user = store.get_portal_user_by_email(sender)
    if user is None and channel == "whatsapp" and sender:
        user = store.get_portal_user_by_whatsapp(sender)
    return user


def unsubscribe_url(user_id: str) -> str:
    from digital_msl import security

    token = security.sign_token({"u": user_id}, "unsubscribe", ttl_seconds=180 * 24 * 3600)
    base = get_dmsl_settings().public_base_url.rstrip("/")
    return f"{base}/api/v1/digital-msl/portal/unsubscribe?token={token}"


def consent_block(message: InboundMessage, channel: str):
    """Return a delivery result when a registered doctor has not consented to this channel, else None."""
    user = resolve_recipient(message, channel)
    sender = message.external_sender_id or ""
    if not user or user.get("role") != "doctor" or store.has_channel_consent(user["user_id"], channel):
        return None
    detail = f"Doctor has not consented to {channel} messages; reply not sent."
    outbox_id = store.add_outbox(
        message=message, recipient=sender, subject="", body="", delivery_status="blocked_no_consent", provider_detail=detail
    )
    store.record_audit_event(
        action="delivery_blocked", user_id=user["user_id"], role="doctor", conversation_id=message.conversation_id or "",
        entity_type="outbox", entity_id=outbox_id, outcome="no_consent", detail={"channel": channel},
    )
    return {"channel": channel, "delivery_status": "blocked_no_consent", "outbox_id": outbox_id, "detail": detail}


class ChannelAdapter:
    name = ""

    def deliver(self, message: InboundMessage, response: AgentResponse) -> dict[str, Any]:
        raise NotImplementedError


class WebChannel(ChannelAdapter):
    """The web portal receives the reply in the HTTP / SSE response of the same request."""

    name = "web"

    def deliver(self, message: InboundMessage, response: AgentResponse) -> dict[str, Any]:
        return {"channel": "web", "delivery_status": "returned_in_http_response"}


class EmailChannel(ChannelAdapter):
    name = "email"

    @staticmethod
    def normalize(payload: dict[str, Any], *, repository_id: str) -> InboundMessage:
        raw_from = str(payload.get("from") or payload.get("sender") or "").strip()
        subject = str(payload.get("subject") or "").strip()
        body = plain_email_question(str(payload.get("text") or payload.get("body") or ""), subject)
        sender_addr = re.search(r"[\w.+\-]+@[\w.\-]+", raw_from)
        sender = sender_addr.group(0) if sender_addr else raw_from
        display = raw_from[: sender_addr.start()].strip().strip('"').strip() if sender_addr else ""
        display = re.sub(r"\s+", " ", display).strip(" <>\"'")
        return InboundMessage(
            channel="email",
            idempotency_key=str(payload.get("message_id") or payload.get("Message-ID") or ""),
            external_sender_id=sender,
            sender_name=display,
            user_id=str(payload.get("user_id") or sender),
            repository_id=repository_id,
            subject=subject,
            message_text=body or subject,
        )

    def deliver(self, message: InboundMessage, response: AgentResponse) -> dict[str, Any]:
        blocked = consent_block(message, "email")
        if blocked:
            return blocked
        settings = get_dmsl_settings()
        topic = email_topic(message)
        subject = f"Response to Your Medical Information Request – {topic}"
        body = render_medical_information_letter(message, response)
        recipient_user = resolve_recipient(message, "email")
        footer = ["", "--", compliance.compliance_notice_text()]
        if recipient_user and recipient_user.get("role") == "doctor":
            footer.append(f"Unsubscribe from Digital MSL emails: {unsubscribe_url(recipient_user['user_id'])}")
        body = body.rstrip() + "\n" + "\n".join(footer) + "\n"
        files = evidence_attachments(response)
        names = [name for name, _data, _media in files]
        status, detail = "queued_no_provider", "DIGITAL_MSL_SMTP_HOST not configured"
        if settings.smtp_host and message.external_sender_id:
            try:
                email = EmailMessage()
                email["From"] = settings.smtp_from or settings.smtp_user
                email["To"] = message.external_sender_id
                email["Subject"] = subject
                bcc = (settings.smtp_from or settings.smtp_user or "").strip()
                if bcc:
                    email["Bcc"] = bcc
                email.set_content(body)
                _attach_files_to_email(email, files)
                with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
                    smtp.starttls()
                    if settings.smtp_user:
                        smtp.login(settings.smtp_user, _mail_secret(settings.smtp_password))
                    smtp.send_message(email)
                status, detail = "sent", f"smtp://{settings.smtp_host}"
                logger.info(
                    "Medical information email sent to %s (subject=%s, attachments=%s)",
                    message.external_sender_id,
                    subject[:100],
                    len(files),
                )
            except Exception as exc:
                logger.warning("Email delivery failed: %s", exc)
                status, detail = "failed", str(exc)
        if names:
            detail = f"{detail}; attachments={', '.join(names)}"
        outbox_id = store.add_outbox(
            message=message,
            recipient=message.external_sender_id,
            subject=subject,
            body=body,
            delivery_status=status,
            provider_detail=detail,
        )
        return {"channel": "email", "delivery_status": status, "outbox_id": outbox_id, "detail": detail}


_WHATSAPP_MAX_ATTACHMENTS = 3


class WhatsAppChannel(ChannelAdapter):
    name = "whatsapp"

    @staticmethod
    def normalize(payload: dict[str, Any], *, repository_id: str) -> InboundMessage:
        from digital_msl.whatsapp_bridge import normalize_phone

        sender = normalize_phone(str(payload.get("from") or payload.get("wa_id") or "").strip())
        text = payload.get("text")
        if isinstance(text, dict):
            text = text.get("body")
        group_id = str(payload.get("group_id") or payload.get("chat_id") or "").strip()
        group_name = str(payload.get("group_name") or payload.get("group") or "").strip()
        profile = str(payload.get("profile_name") or payload.get("sender_name") or "").strip()
        return InboundMessage(
            channel="whatsapp",
            idempotency_key=str(payload.get("id") or payload.get("message_id") or ""),
            external_sender_id=sender,
            sender_name=profile,
            user_id=str(payload.get("user_id") or sender),
            repository_id=repository_id,
            message_text=str(text or "").strip(),
            subject=group_name,
            session_id=group_id,
        )

    def deliver(self, message: InboundMessage, response: AgentResponse) -> dict[str, Any]:
        blocked = consent_block(message, "whatsapp")
        if blocked:
            return blocked
        from digital_msl.whatsapp_bridge import send_whatsapp_text

        from digital_msl.attachments import cited_documents, safe_attachment_filename
        from digital_msl.whatsapp_bridge import send_whatsapp_document, whatsapp_mock_delivery_enabled

        body = render_answer_text(response)
        wa_user = resolve_recipient(message, "whatsapp")
        if wa_user and store.mark_notice_seen(wa_user["user_id"], "whatsapp", compliance.COMPLIANCE_NOTICE_VERSION):
            body = f"{compliance.compliance_notice_text()}\n\n{body}"
        body = body[:4000]
        status, detail = send_whatsapp_text(message.external_sender_id, body)
        if status == "queued_no_provider":
            detail = detail or "DIGITAL_MSL_WHATSAPP_API_URL not configured"
        attached: list[str] = []
        if status == "sent":
            if whatsapp_mock_delivery_enabled():
                # Demo: show what would be attached without downloading the files.
                attached = [
                    safe_attachment_filename(name, "application/pdf")
                    for name in list(cited_documents(response).values())[:_WHATSAPP_MAX_ATTACHMENTS]
                ]
            else:
                for filename, data, media in evidence_attachments(
                    response, require_smtp=False, limit=_WHATSAPP_MAX_ATTACHMENTS
                ):
                    doc_status, doc_detail = send_whatsapp_document(
                        message.external_sender_id, filename, data, media, caption=filename
                    )
                    if doc_status == "sent":
                        attached.append(filename)
                    else:
                        logger.warning("WhatsApp evidence %s not sent: %s", filename, doc_detail)
        if attached:
            detail = f"{detail}; attachments={', '.join(attached)}"
        outbox_id = store.add_outbox(
            message=message,
            recipient=message.external_sender_id,
            subject=message.subject or "WhatsApp reply",
            body=body,
            delivery_status=status,
            provider_detail=detail,
        )
        return {
            "channel": "whatsapp",
            "delivery_status": status,
            "outbox_id": outbox_id,
            "detail": detail,
            "attachments": attached,
        }


CHANNELS: dict[str, ChannelAdapter] = {
    "web": WebChannel(),
    "email": EmailChannel(),
    "whatsapp": WhatsAppChannel(),
}


def adapter_for(channel: str) -> ChannelAdapter:
    return CHANNELS[channel]
