"""Read a medical-information inbox and answer each new message on the same engine as web chat."""
from __future__ import annotations

import asyncio
import email
import imaplib
import logging
from email.header import decode_header
from email.message import Message

from digital_msl import store
from digital_msl.channels import _mail_secret, email_repository_candidates, plain_email_question
from digital_msl.config import get_dmsl_settings

logger = logging.getLogger(__name__)


def _header(value: str) -> str:
    parts = decode_header(value or "")
    chunks: list[str] = []
    for text, encoding in parts:
        if isinstance(text, bytes):
            chunks.append(text.decode(encoding or "utf-8", errors="replace"))
        else:
            chunks.append(text)
    return "".join(chunks).strip()


def _body(message: Message) -> str:
    if message.is_multipart():
        plain = ""
        for part in message.walk():
            if part.get_content_type() != "text/plain" or part.get_filename():
                continue
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            plain = payload.decode(charset, errors="replace")
            if plain.strip():
                return plain
        return plain
    payload = message.get_payload(decode=True) or b""
    if isinstance(payload, bytes):
        return payload.decode(message.get_content_charset() or "utf-8", errors="replace")
    return str(payload or "")


def parse_rfc822(raw: bytes) -> dict[str, str]:
    message = email.message_from_bytes(raw)
    return {
        "from": _header(message.get("From", "")),
        "subject": _header(message.get("Subject", "")),
        "text": _body(message),
        "message_id": _header(message.get("Message-ID", "")),
        "automated": "1" if _is_machine_mail(message) else "",
    }


def _is_machine_mail(message: Message) -> bool:
    auto = _header(message.get("Auto-Submitted", "")).lower()
    if auto and auto != "no":
        return True
    if _header(message.get("List-Unsubscribe", "")) or _header(message.get("List-Id", "")):
        return True
    precedence = _header(message.get("Precedence", "")).lower()
    return precedence in {"bulk", "list", "junk"}


def _credentials() -> tuple[str, str, str, int, str]:
    settings = get_dmsl_settings()
    host = (settings.imap_host or "").strip()
    user = (settings.imap_user or settings.smtp_user or "").strip()
    password = _mail_secret(settings.imap_password or settings.smtp_password)
    return host, user, password, int(settings.imap_port or 993), settings.imap_mailbox or "INBOX"


def _message_bytes(payload) -> bytes | None:
    blobs: list[bytes] = []

    def walk(item) -> None:
        if isinstance(item, bytes):
            if len(item) > 20 and not item.startswith((b"*", b"(")):
                blobs.append(item)
        elif isinstance(item, (tuple, list)):
            for part in item:
                walk(part)

    walk(payload)
    if not blobs:
        return None
    return max(blobs, key=len)


def _open_mailbox():
    host, user, password, port, mailbox = _credentials()
    if not host or not user or not password:
        return None
    client = imaplib.IMAP4_SSL(host, port, timeout=30)
    client.login(user, password)
    client.select(mailbox)
    return client


def _close_mailbox(client) -> None:
    if client is None:
        return
    try:
        client.logout()
    except Exception:
        try:
            client.shutdown()
        except Exception:
            pass


_SKIP_SENDERS = ("mailer-daemon@", "postmaster@", "noreply@", "no-reply@", "notifications@", "newsletter@")


def list_recent_uids(limit: int = 35) -> list[bytes]:
    client = _open_mailbox()
    if client is None:
        return []
    try:
        status, data = client.uid("SEARCH", None, "ALL")
        if status != "OK" or not data or not data[0]:
            return []
        uids = data[0].split()
        return uids[-limit:]
    finally:
        _close_mailbox(client)


def list_candidate_uids() -> list[bytes]:
    """Unread mail plus recent inbox items we may not have answered yet."""
    ordered: list[bytes] = []
    seen: set[bytes] = set()
    for uid in reversed(list_unseen_uids() + list_recent_uids()):
        if uid in seen:
            continue
        seen.add(uid)
        ordered.append(uid)
    return ordered


def list_unseen_uids() -> list[bytes]:
    """Newest unseen message ids. One short command so Gmail does not drop the socket."""
    client = _open_mailbox()
    if client is None:
        return []
    try:
        status, data = client.uid("SEARCH", None, "UNSEEN")
        if status != "OK" or not data or not data[0]:
            return []
        return data[0].split()[-20:]
    finally:
        _close_mailbox(client)


def _automated_sender(sender: str) -> bool:
    lowered = sender.lower()
    return any(token in lowered for token in _SKIP_SENDERS)


def fetch_uid(uid: bytes) -> dict[str, str] | None:
    """Download one message on its own connection. BODY.PEEK does not mark it read."""
    client = _open_mailbox()
    if client is None:
        return None
    try:
        status, payload = client.uid("FETCH", uid, "(BODY.PEEK[])")
        if status != "OK":
            return None
        raw = _message_bytes(payload)
        if not raw:
            return None
        return parse_rfc822(raw)
    finally:
        _close_mailbox(client)


def mark_seen(uid: bytes) -> None:
    client = _open_mailbox()
    if client is None:
        return
    try:
        client.uid("STORE", uid, "+FLAGS", "(\\Seen)")
    finally:
        _close_mailbox(client)


async def resolve_email_target(sender: str, subject: str = "", body: str = "") -> tuple[str, str]:
    """Choose a therapeutic area without asking the sender for a repository name."""
    candidates, user_id = email_repository_candidates(sender, subject, body)
    if not candidates:
        return "", user_id
    if len(candidates) == 1:
        return candidates[0], user_id
    from digital_msl.agents.query_agent import default_retrieve_client
    from digital_msl.evidence import retrieve_evidence

    client = default_retrieve_client()
    query = plain_email_question(body, subject)
    eye_question = any(term in f"{subject} {query}".lower() for term in ("retina", "retinal", "macular", "dme", "amd", "aflibercept", "visual acuity", "anti-vegf"))
    if eye_question:
        by_id = {str(area.get("repository_id")): str(area.get("name") or "") for area in store.list_therapeutic_areas()}
        named = [item for item in candidates if any(word in by_id.get(item, "").lower() for word in ("retina", "eye", "ophthal"))]
        if named:
            candidates = named
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
            logger.warning("Email retrieval failed for therapeutic area %s.", repository_id)
            continue
        score = max((chunk.retrieval_score for chunk in outcome.chunks), default=0.0)
        if outcome.chunks and score >= best_score:
            best_id, best_score = repository_id, score
    return best_id, user_id


async def ingest_unseen_once() -> int:
    """Answer each unseen inbox message. Returns how many were handled."""
    settings = get_dmsl_settings()
    own = (settings.smtp_from or settings.smtp_user or "").lower()
    uids = await asyncio.to_thread(list_candidate_uids)
    handled = 0
    for uid in uids:
        try:
            payload = await asyncio.to_thread(fetch_uid, uid)
        except imaplib.IMAP4.abort as exc:
            logger.warning("Could not download email %s (%s). It stays unread and will be tried again.", uid.decode(errors="ignore"), exc)
            continue
        if not payload:
            logger.warning("Email %s had no readable body. It stays unread.", uid.decode(errors="ignore"))
            continue
        sender = payload.get("from") or ""
        if (own and own in sender.lower()) or _automated_sender(sender) or payload.get("automated") == "1":
            await asyncio.to_thread(mark_seen, uid)
            continue
        message_id = (payload.get("message_id") or "").strip()
        if message_id and store.email_already_processed(message_id):
            await asyncio.to_thread(mark_seen, uid)
            continue
        repository_id, user_id = await resolve_email_target(sender, payload.get("subject") or "", payload.get("text") or "")
        if not repository_id:
            logger.warning("Email from %s has no active therapeutic area; leaving it unseen.", sender)
            continue
        from digital_msl.agents.query_agent import QueryAgent
        from digital_msl.channels import EmailChannel

        inbound = EmailChannel.normalize(
            {**payload, "user_id": user_id, "repository_id": repository_id},
            repository_id=repository_id,
        )
        if not inbound.message_text:
            await asyncio.to_thread(mark_seen, uid)
            continue
        try:
            result = await QueryAgent().handle(inbound)
            delivery = (result.delivery or {}).get("delivery_status", "")
            logger.info(
                "Email to %s decision=%s delivery=%s detail=%s",
                sender,
                result.decision,
                delivery,
                (result.delivery or {}).get("detail", "")[:200],
            )
        except Exception:
            logger.exception("Email question from %s failed.", sender)
            continue
        await asyncio.to_thread(mark_seen, uid)
        handled += 1
    return handled


async def poll_forever(stop: asyncio.Event) -> None:
    settings = get_dmsl_settings()
    interval = max(15, int(settings.imap_poll_seconds or 45))
    logger.info("Digital MSL inbox polling every %ss.", interval)
    while not stop.is_set():
        try:
            count = await ingest_unseen_once()
            if count:
                logger.info("Answered %s email question(s).", count)
        except imaplib.IMAP4.abort as exc:
            logger.warning("Inbox connection closed by the mail server (%s). The next poll will retry.", exc)
        except Exception:
            logger.exception("Inbox poll failed.")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue
