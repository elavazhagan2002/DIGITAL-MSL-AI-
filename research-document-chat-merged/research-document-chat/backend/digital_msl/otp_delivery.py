"""Deliver registration one-time codes by email and WhatsApp.

OTPs are transactional and allowed without channel consent. Codes are never written to the outbox
or the logs; when no provider is configured the outbox row records only that a code was issued.
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

import requests

from digital_msl import store
from digital_msl.channels import _mail_secret
from digital_msl.config import get_dmsl_settings
from digital_msl.contracts import InboundMessage

logger = logging.getLogger(__name__)

_EMAIL_SUBJECT = "Your Digital MSL verification code"


def _body(code: str) -> str:
    return (
        f"Your Digital MSL verification code is {code}. It expires in 5 minutes. "
        "If you did not request it, ignore this message."
    )


def _outbox(channel: str, recipient: str, subject: str, status: str, detail: str, user_id: str) -> None:
    message = InboundMessage(channel=channel, repository_id="", message_text="otp", user_id=user_id)  # type: ignore[arg-type]
    store.add_outbox(
        message=message,
        recipient=recipient,
        subject=subject,
        body="[verification code redacted]",
        delivery_status=status,
        provider_detail=detail[:500],
    )


def send_email_otp(user_id: str, email: str, code: str) -> str:
    settings = get_dmsl_settings()
    status, detail = "queued_no_provider", "DIGITAL_MSL_SMTP_HOST not configured"
    if settings.smtp_host and email:
        try:
            msg = EmailMessage()
            msg["From"] = settings.smtp_from or settings.smtp_user
            msg["To"] = email
            msg["Subject"] = _EMAIL_SUBJECT
            msg.set_content(_body(code))
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
                smtp.starttls()
                if settings.smtp_user:
                    smtp.login(settings.smtp_user, _mail_secret(settings.smtp_password))
                smtp.send_message(msg)
            status, detail = "sent", f"smtp://{settings.smtp_host}"
        except Exception as exc:
            logger.warning("OTP email delivery failed: %s", type(exc).__name__)
            status, detail = "failed", type(exc).__name__
    _outbox("email", email, _EMAIL_SUBJECT, status, detail, user_id)
    return status


def send_mobile_otp(user_id: str, mobile: str, code: str) -> str:
    """WhatsApp OTP. With DIGITAL_MSL_WHATSAPP_OTP_TEMPLATE set, uses that approved template (Meta Cloud API)."""
    from digital_msl.whatsapp_bridge import normalize_phone, send_whatsapp_text, whatsapp_mock_delivery_enabled

    settings = get_dmsl_settings()
    template = (settings.whatsapp_otp_template or "").strip()
    if template and settings.whatsapp_api_url and not whatsapp_mock_delivery_enabled():
        to = normalize_phone(mobile).lstrip("+")
        try:
            res = requests.post(
                settings.whatsapp_api_url,
                headers={"Authorization": f"Bearer {settings.whatsapp_token}"},
                json={
                    "messaging_product": "whatsapp",
                    "to": to,
                    "type": "template",
                    "template": {
                        "name": template,
                        "language": {"code": "en"},
                        "components": [
                            {"type": "body", "parameters": [{"type": "text", "text": code}]},
                            {"type": "button", "sub_type": "url", "index": "0", "parameters": [{"type": "text", "text": code}]},
                        ],
                    },
                },
                timeout=30,
            )
            res.raise_for_status()
            status, detail = "sent", "template"
        except Exception as exc:
            logger.warning("OTP WhatsApp template delivery failed: %s", type(exc).__name__)
            status, detail = "failed", type(exc).__name__
    else:
        status, detail = send_whatsapp_text(mobile, _body(code))
    _outbox("whatsapp", mobile, "Verification code", status, detail, user_id)
    return status


def send_codes(user_id: str, codes: dict[str, str]) -> None:
    targets = store.contact_targets(user_id)
    if "email" in codes:
        send_email_otp(user_id, targets["email"], codes["email"])
    if "mobile" in codes and targets["mobile"]:
        send_mobile_otp(user_id, targets["mobile"], codes["mobile"])
