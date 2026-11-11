"""Persistence for the Digital MSL component.

Uses its own SQLAlchemy metadata so the package can be mounted into any host
application (or run standalone) without touching the host's models.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Iterator, Optional

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, UniqueConstraint, create_engine, or_, text
from sqlalchemy.orm import Session, declarative_base, object_session, sessionmaker
from sqlalchemy.sql import func

from digital_msl import doctor_registry as registry
from digital_msl import security
from digital_msl.config import check_demo_guard, get_dmsl_settings
from digital_msl.ta_catalog import DEFAULT_THERAPEUTIC_AREAS, slugify
from digital_msl.contracts import AgentResponse, InboundMessage, ValidationPayload, new_id

logger = logging.getLogger(__name__)


def utc_iso(dt: datetime | None) -> str | None:
    """Serialize DB datetimes as explicit UTC for browser IST conversion."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


Base = declarative_base()


class InboundMessageRow(Base):
    __tablename__ = "dmsl_inbound_messages"

    id = Column(Integer, primary_key=True)
    message_id = Column(String(64), unique=True, nullable=False, index=True)
    idempotency_key = Column(String(255), index=True)
    correlation_id = Column(String(64), index=True)
    conversation_id = Column(String(64), index=True)
    channel = Column(String(20), nullable=False)
    external_sender_id = Column(String(255))
    user_id = Column(String(64))
    repository_id = Column(String(64))
    message_text = Column(Text, nullable=False)
    status = Column(String(20), default="received")
    session_id = Column(String(64), index=True)
    notice_version = Column(String(32), default="")
    response_json = Column(Text)
    received_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class ValidationTraceRow(Base):
    __tablename__ = "dmsl_validation_traces"

    id = Column(Integer, primary_key=True)
    trace_id = Column(String(64), unique=True, nullable=False, index=True)
    correlation_id = Column(String(64), index=True)
    message_id = Column(String(64), index=True)
    channel = Column(String(20))
    user_query = Column(Text)
    pre_validation_json = Column(Text)
    post_validation_json = Column(Text)
    overall_score = Column(Float)
    decision = Column(String(32))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class MSLCaseRow(Base):
    __tablename__ = "dmsl_msl_cases"

    id = Column(Integer, primary_key=True)
    case_id = Column(String(64), unique=True, nullable=False, index=True)
    correlation_id = Column(String(64), index=True)
    source_message_id = Column(String(64), index=True)
    trace_id = Column(String(64))
    conversation_id = Column(String(64), index=True)
    channel = Column(String(20))
    external_sender_id = Column(String(255))
    doctor_id = Column(String(64))
    assigned_msl_id = Column(String(64), index=True)
    question = Column(Text)
    reason_codes = Column(Text)
    status = Column(String(20), default="open")
    priority = Column(String(20), default="normal")
    resolution = Column(Text)
    draft_response = Column(Text)
    draft_status = Column(String(32), default="draft")
    approval_status = Column(String(32), default="pending")
    reviewer_notes = Column(Text)
    references_json = Column(Text)
    claims_json = Column(Text)
    version = Column(Integer, default=1)
    author_id = Column(String(64))
    reviewer_id = Column(String(64))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    queue_notice = Column(Text)
    assigned_group_id = Column(String(64), index=True)
    repository_id = Column(String(64), default="", index=True)


class PortalUserRow(Base):
    __tablename__ = "dmsl_portal_users"

    id = Column(Integer, primary_key=True)
    user_id = Column(String(64), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    display_name = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, index=True)  # admin | doctor | msl
    assigned_msl_id = Column(String(64), default="")
    assigned_group_id = Column(String(64), default="", index=True)
    assigned_repository_ids = Column(Text, default="[]")
    default_repository_id = Column(String(64), default="")
    status = Column(String(20), default="active")
    password_hash = Column(String(255), nullable=True)
    whatsapp_phone = Column(String(32), default="", index=True)  # legacy plaintext; migrated to *_enc / *_idx
    whatsapp_phone_enc = Column(Text, default="")
    whatsapp_phone_idx = Column(String(64), index=True)
    mfa_secret_enc = Column(Text, default="")
    mfa_enabled_at = Column(DateTime(timezone=True), nullable=True)
    mfa_recovery_json = Column(Text, default="[]")
    is_demo = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class OutboxRow(Base):
    __tablename__ = "dmsl_outbox"

    id = Column(Integer, primary_key=True)
    outbox_id = Column(String(64), unique=True, nullable=False)
    message_id = Column(String(64), index=True)
    correlation_id = Column(String(64), index=True)
    channel = Column(String(20))
    recipient = Column(String(255))
    subject = Column(String(500))
    body = Column(Text)
    delivery_status = Column(String(20), default="queued")
    provider_detail = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PolicyRow(Base):
    __tablename__ = "dmsl_policy"

    id = Column(Integer, primary_key=True)
    payload_json = Column(Text, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class MessageTemplateRow(Base):
    __tablename__ = "dmsl_message_templates"

    id = Column(Integer, primary_key=True)
    template_id = Column(String(64), unique=True, nullable=False, index=True)
    kind = Column(String(64), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    channel = Column(String(20), default="all")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class MslGroupRow(Base):
    __tablename__ = "dmsl_msl_groups"

    id = Column(Integer, primary_key=True)
    group_id = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class MslGroupMemberRow(Base):
    __tablename__ = "dmsl_group_members"

    id = Column(Integer, primary_key=True)
    group_id = Column(String(64), nullable=False, index=True)
    msl_user_id = Column(String(64), nullable=False, index=True)


class NotificationRow(Base):
    __tablename__ = "dmsl_notifications"

    id = Column(Integer, primary_key=True)
    notification_id = Column(String(64), unique=True, nullable=False, index=True)
    user_id = Column(String(64), nullable=False, index=True)
    kind = Column(String(40), nullable=False)
    title = Column(String(255), nullable=False)
    body = Column(Text, default="")
    case_id = Column(String(64), default="")
    read_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class TherapeuticAreaRow(Base):
    __tablename__ = "dmsl_therapeutic_areas"

    id = Column(Integer, primary_key=True)
    repository_id = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, default="")
    owner_msl_id = Column(String(64), default="", index=True)
    default_flag = Column(Integer, default=0)
    active_flag = Column(Integer, default=1)
    scope_keywords = Column(Text, default="[]")
    slug = Column(String(80), default="")
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class TherapeuticAreaMslRow(Base):
    __tablename__ = "dmsl_ta_msls"

    id = Column(Integer, primary_key=True)
    repository_id = Column(String(64), nullable=False, index=True)
    msl_user_id = Column(String(64), nullable=False, index=True)


class TherapeuticAreaFolderRow(Base):
    __tablename__ = "dmsl_ta_folders"

    id = Column(Integer, primary_key=True)
    folder_id = Column(String(64), unique=True, nullable=False, index=True)
    repository_id = Column(String(64), nullable=False, index=True)
    parent_folder_id = Column(String(64), default="", index=True)
    name = Column(String(255), nullable=False)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class DocumentFolderRow(Base):
    __tablename__ = "dmsl_document_folders"

    id = Column(Integer, primary_key=True)
    document_id = Column(String(64), unique=True, nullable=False, index=True)
    repository_id = Column(String(64), nullable=False, index=True)
    folder_id = Column(String(64), default="", index=True)


class AuditEventRow(Base):
    __tablename__ = "dmsl_audit_events"

    id = Column(Integer, primary_key=True)
    event_id = Column(String(64), unique=True, nullable=False, index=True)
    user_id = Column(String(64), default="", index=True)
    role = Column(String(20), default="")
    session_id = Column(String(64), default="", index=True)
    repository_id = Column(String(64), default="")
    conversation_id = Column(String(64), default="")
    case_id = Column(String(64), default="")
    action = Column(String(64), nullable=False, index=True)
    entity_type = Column(String(64), default="")
    entity_id = Column(String(64), default="")
    outcome = Column(String(32), default="ok")
    detail_json = Column(Text, default="{}")
    ip_address = Column(String(64), default="")
    user_agent = Column(String(255), default="")
    before_json = Column(Text, default="")
    after_json = Column(Text, default="")
    reason = Column(Text, default="")
    prev_hash = Column(String(64), default="")
    entry_hash = Column(String(64), default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class LoginSessionRow(Base):
    __tablename__ = "dmsl_login_sessions"

    id = Column(Integer, primary_key=True)
    session_id = Column(String(64), unique=True, nullable=False, index=True)
    user_id = Column(String(64), nullable=False, index=True)
    role = Column(String(20), default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    ended_at = Column(DateTime(timezone=True), nullable=True)
    last_seen = Column(DateTime(timezone=True), nullable=True)


class DoctorProfileRow(Base):
    """Professional identity of a registered doctor (the fields an NMR lookup needs).

    Registration number and mobile are stored encrypted (``*_enc``) with keyed blind indexes
    (``*_idx``) for uniqueness. The legacy plaintext columns are emptied by ``migrate_pii``.
    """

    __tablename__ = "dmsl_doctor_profiles"
    __table_args__ = (UniqueConstraint("registration_council", "registration_number", name="uq_dmsl_doctor_registration"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(String(64), unique=True, nullable=False, index=True)
    mobile = Column(String(32))  # legacy plaintext
    registration_number = Column(String(40))  # legacy plaintext
    mobile_enc = Column(Text, default="")
    mobile_idx = Column(String(64))
    registration_number_enc = Column(Text, default="")
    registration_number_idx = Column(String(64))
    registration_council = Column(String(160))
    council_id = Column(String(64), index=True)
    registration_year = Column(Integer)
    qualification = Column(String(80))  # legacy
    basic_qualification = Column(String(120), default="")
    highest_qualification = Column(String(120), default="")
    speciality = Column(String(80))
    institution = Column(String(160))
    practice_type = Column(String(80))
    city = Column(String(80))
    state = Column(String(80))
    pincode = Column(String(12), default="")
    nmr_id = Column(String(40), default="")
    verification_status = Column(String(40), default="PENDING_CONTACT_VERIFICATION", index=True)
    verification_source = Column(String(20), default="manual")
    verification_notes = Column(Text, default="")
    rejection_reason = Column(Text, default="")
    verified_at = Column(DateTime(timezone=True), nullable=True)
    verified_by = Column(String(64), default="")
    email_verified_at = Column(DateTime(timezone=True), nullable=True)
    mobile_verified_at = Column(DateTime(timezone=True), nullable=True)
    certificate_path = Column(String(255), default="")
    certificate_scan_status = Column(String(20), default="")
    certificate_uploaded_at = Column(DateTime(timezone=True), nullable=True)
    deactivation_reason = Column(Text, default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CouncilRow(Base):
    __tablename__ = "dmsl_councils"

    id = Column(Integer, primary_key=True)
    council_id = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(160), nullable=False)
    sort_order = Column(Integer, default=0)
    enabled = Column(Integer, default=1)


class OtpRow(Base):
    """One-time codes for email / mobile verification (hashed, expiring, attempt-limited)."""

    __tablename__ = "dmsl_otps"

    id = Column(Integer, primary_key=True)
    user_id = Column(String(64), nullable=False, index=True)
    channel = Column(String(10), nullable=False)
    code_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    attempts = Column(Integer, default=0)
    sends = Column(Integer, default=1)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class NoticeSeenRow(Base):
    __tablename__ = "dmsl_notice_seen"
    __table_args__ = (UniqueConstraint("user_id", "channel", "version", name="uq_dmsl_notice_seen"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(String(64), nullable=False, index=True)
    channel = Column(String(20), nullable=False)
    version = Column(String(32), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ConsentRow(Base):
    """Append-only consent ledger; the newest row per (user, type) is the current state."""

    __tablename__ = "dmsl_consents"

    id = Column(Integer, primary_key=True)
    consent_id = Column(String(64), unique=True, nullable=False)
    user_id = Column(String(64), nullable=False, index=True)
    consent_type = Column(String(40), nullable=False, index=True)
    channel = Column(String(20), default="")
    granted = Column(Integer, nullable=False)
    version = Column(String(32), default="")
    text_sha256 = Column(String(64), default="")
    source = Column(String(20), default="registration")
    ip_address = Column(String(64), default="")
    user_agent = Column(String(255), default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class LoginAttemptRow(Base):
    __tablename__ = "dmsl_login_attempts"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), nullable=False, index=True)
    success = Column(Integer, nullable=False)
    ip_address = Column(String(64), default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class DocumentApprovalRow(Base):
    """Approval status per IKM document, kept in Digital MSL because IKM has no approval fields.

    The retrieval filter reads this registry: a document that is not registered, or is not APPROVED and
    in force, is never quoted (see grounding.exclusion_reason).
    """

    __tablename__ = "dmsl_document_approvals"
    __table_args__ = (UniqueConstraint("repository_id", "document_id", name="uq_dmsl_doc_approval"),)

    id = Column(Integer, primary_key=True)
    repository_id = Column(String(64), nullable=False, index=True)
    document_id = Column(String(64), nullable=False, index=True)
    document_title = Column(String(255), default="")
    approval_status = Column(String(20), nullable=False, default="IN_REVIEW")
    version = Column(String(40), default="")
    effective_date = Column(String(20), default="")
    expiry_date = Column(String(20), default="")
    superseded_by = Column(String(120), default="")
    approved_by = Column(String(64), default="")
    approved_at = Column(DateTime(timezone=True), nullable=True)
    updated_by = Column(String(64), default="")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    reason = Column(Text, default="")


@lru_cache()
def _session_factory() -> sessionmaker:
    url = get_dmsl_settings().database_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args)
    Base.metadata.create_all(bind=engine)
    alter_cols = [
        "ALTER TABLE dmsl_portal_users ADD COLUMN password_hash VARCHAR(255)",
        "ALTER TABLE dmsl_msl_cases ADD COLUMN conversation_id VARCHAR(64)",
        "ALTER TABLE dmsl_msl_cases ADD COLUMN draft_response TEXT",
        "ALTER TABLE dmsl_msl_cases ADD COLUMN draft_status VARCHAR(32) DEFAULT 'draft'",
        "ALTER TABLE dmsl_msl_cases ADD COLUMN approval_status VARCHAR(32) DEFAULT 'pending'",
        "ALTER TABLE dmsl_msl_cases ADD COLUMN reviewer_notes TEXT",
        "ALTER TABLE dmsl_msl_cases ADD COLUMN references_json TEXT",
        "ALTER TABLE dmsl_msl_cases ADD COLUMN claims_json TEXT",
        "ALTER TABLE dmsl_msl_cases ADD COLUMN version INTEGER DEFAULT 1",
        "ALTER TABLE dmsl_msl_cases ADD COLUMN author_id VARCHAR(64)",
        "ALTER TABLE dmsl_msl_cases ADD COLUMN reviewer_id VARCHAR(64)",
        "ALTER TABLE dmsl_msl_cases ADD COLUMN queue_notice TEXT",
        "ALTER TABLE dmsl_msl_cases ADD COLUMN assigned_group_id VARCHAR(64)",
        "ALTER TABLE dmsl_portal_users ADD COLUMN assigned_group_id VARCHAR(64)",
        "ALTER TABLE dmsl_portal_users ADD COLUMN assigned_repository_ids TEXT",
        "ALTER TABLE dmsl_portal_users ADD COLUMN default_repository_id VARCHAR(64)",
        "ALTER TABLE dmsl_portal_users ADD COLUMN whatsapp_phone VARCHAR(32)",
        "ALTER TABLE dmsl_inbound_messages ADD COLUMN session_id VARCHAR(64)",
        "ALTER TABLE dmsl_msl_cases ADD COLUMN repository_id VARCHAR(64)",
        "ALTER TABLE dmsl_ta_folders ADD COLUMN parent_folder_id VARCHAR(64)",
        "ALTER TABLE dmsl_ta_folders ADD COLUMN sort_order INTEGER DEFAULT 0",
        "ALTER TABLE dmsl_therapeutic_areas ADD COLUMN description TEXT",
        "ALTER TABLE dmsl_therapeutic_areas ADD COLUMN active_flag INTEGER DEFAULT 1",
        "ALTER TABLE dmsl_therapeutic_areas ADD COLUMN scope_keywords TEXT DEFAULT '[]'",
        "ALTER TABLE dmsl_therapeutic_areas ADD COLUMN slug VARCHAR(80) DEFAULT ''",
        "ALTER TABLE dmsl_therapeutic_areas ADD COLUMN sort_order INTEGER DEFAULT 0",
        "ALTER TABLE dmsl_inbound_messages ADD COLUMN notice_version VARCHAR(32) DEFAULT ''",
        "ALTER TABLE dmsl_portal_users ADD COLUMN whatsapp_phone_enc TEXT DEFAULT ''",
        "ALTER TABLE dmsl_portal_users ADD COLUMN whatsapp_phone_idx VARCHAR(64)",
        "ALTER TABLE dmsl_portal_users ADD COLUMN mfa_secret_enc TEXT DEFAULT ''",
        "ALTER TABLE dmsl_portal_users ADD COLUMN mfa_enabled_at DATETIME",
        "ALTER TABLE dmsl_portal_users ADD COLUMN mfa_recovery_json TEXT DEFAULT '[]'",
        "ALTER TABLE dmsl_portal_users ADD COLUMN is_demo INTEGER DEFAULT 0",
        "ALTER TABLE dmsl_login_sessions ADD COLUMN last_seen DATETIME",
        "ALTER TABLE dmsl_consents ADD COLUMN channel VARCHAR(20) DEFAULT ''",
        "ALTER TABLE dmsl_audit_events ADD COLUMN ip_address VARCHAR(64) DEFAULT ''",
        "ALTER TABLE dmsl_audit_events ADD COLUMN user_agent VARCHAR(255) DEFAULT ''",
        "ALTER TABLE dmsl_audit_events ADD COLUMN before_json TEXT DEFAULT ''",
        "ALTER TABLE dmsl_audit_events ADD COLUMN after_json TEXT DEFAULT ''",
        "ALTER TABLE dmsl_audit_events ADD COLUMN reason TEXT DEFAULT ''",
        "ALTER TABLE dmsl_audit_events ADD COLUMN prev_hash VARCHAR(64) DEFAULT ''",
        "ALTER TABLE dmsl_audit_events ADD COLUMN entry_hash VARCHAR(64) DEFAULT ''",
        "ALTER TABLE dmsl_doctor_profiles ADD COLUMN mobile_enc TEXT DEFAULT ''",
        "ALTER TABLE dmsl_doctor_profiles ADD COLUMN mobile_idx VARCHAR(64)",
        "ALTER TABLE dmsl_doctor_profiles ADD COLUMN registration_number_enc TEXT DEFAULT ''",
        "ALTER TABLE dmsl_doctor_profiles ADD COLUMN registration_number_idx VARCHAR(64)",
        "ALTER TABLE dmsl_doctor_profiles ADD COLUMN council_id VARCHAR(64)",
        "ALTER TABLE dmsl_doctor_profiles ADD COLUMN basic_qualification VARCHAR(120) DEFAULT ''",
        "ALTER TABLE dmsl_doctor_profiles ADD COLUMN highest_qualification VARCHAR(120) DEFAULT ''",
        "ALTER TABLE dmsl_doctor_profiles ADD COLUMN rejection_reason TEXT DEFAULT ''",
        "ALTER TABLE dmsl_doctor_profiles ADD COLUMN email_verified_at DATETIME",
        "ALTER TABLE dmsl_doctor_profiles ADD COLUMN mobile_verified_at DATETIME",
        "ALTER TABLE dmsl_doctor_profiles ADD COLUMN certificate_path VARCHAR(255) DEFAULT ''",
        "ALTER TABLE dmsl_doctor_profiles ADD COLUMN certificate_scan_status VARCHAR(20) DEFAULT ''",
        "ALTER TABLE dmsl_doctor_profiles ADD COLUMN certificate_uploaded_at DATETIME",
        "ALTER TABLE dmsl_doctor_profiles ADD COLUMN deactivation_reason TEXT DEFAULT ''",
    ]
    post_statements = [
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_dmsl_doctor_reg_idx ON dmsl_doctor_profiles (council_id, registration_number_idx)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_dmsl_doctor_mobile_idx ON dmsl_doctor_profiles (mobile_idx)",
        "UPDATE dmsl_doctor_profiles SET verification_status = 'PENDING_REGISTRATION_VERIFICATION' WHERE verification_status = 'pending'",
        "UPDATE dmsl_doctor_profiles SET verification_status = 'VERIFIED' WHERE verification_status = 'verified'",
        "UPDATE dmsl_doctor_profiles SET verification_status = 'REJECTED' WHERE verification_status = 'rejected'",
        "UPDATE dmsl_doctor_profiles SET verification_status = 'NEEDS_INFO' WHERE verification_status = 'needs_info'",
    ]
    if url.startswith("sqlite"):
        # The audit trail is append-only: block UPDATE and DELETE at the database. On PostgreSQL use
        # a BEFORE UPDATE OR DELETE trigger that raises an exception (see docs/DIGITAL_MSL_PRODUCTION.md).
        post_statements += [
            "CREATE TRIGGER IF NOT EXISTS dmsl_audit_no_update BEFORE UPDATE ON dmsl_audit_events "
            "BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END",
            "CREATE TRIGGER IF NOT EXISTS dmsl_audit_no_delete BEFORE DELETE ON dmsl_audit_events "
            "BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END",
        ]
    with engine.connect() as conn:
        for stmt in alter_cols + post_statements:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                conn.rollback()
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    db = _session_factory()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_store() -> None:
    check_demo_guard()
    _session_factory()
    ensure_councils()
    migrate_pii()
    seed_portal_users()
    seed_policy_and_templates()
    seed_default_group()
    seed_demo_doctor_library()
    backfill_doctor_profiles()
    ensure_area_slugs()
    if get_dmsl_settings().demo_enabled:
        seed_demo_accounts()
    else:
        report_demo_accounts()


# ------------------------------------------------------------------ messages
def find_by_idempotency_key(key: str) -> Optional[dict[str, Any]]:
    if not key:
        return None
    with session_scope() as db:
        row = db.query(InboundMessageRow).filter_by(idempotency_key=key).first()
        if row and row.response_json:
            return json.loads(row.response_json)
    return None


def get_inbound(message_id: str) -> Optional[dict[str, Any]]:
    """A stored inbound message with its stored reply (owner checks are the caller's job)."""
    with session_scope() as db:
        row = db.query(InboundMessageRow).filter_by(message_id=message_id).first()
        if row is None:
            return None
        response: dict[str, Any] = {}
        if row.response_json:
            try:
                response = json.loads(row.response_json)
            except ValueError:
                response = {}
        return {
            "message_id": row.message_id,
            "correlation_id": row.correlation_id or "",
            "conversation_id": row.conversation_id or "",
            "channel": row.channel,
            "user_id": row.user_id or "",
            "external_sender_id": row.external_sender_id or "",
            "repository_id": row.repository_id or "",
            "message_text": row.message_text,
            "response": response,
        }


def case_for_message(message_id: str) -> Optional[dict[str, Any]]:
    with session_scope() as db:
        row = db.query(MSLCaseRow).filter_by(source_message_id=message_id).first()
        return _case_to_dict(row) if row else None


def record_inbound(message: InboundMessage) -> None:
    with session_scope() as db:
        db.add(
            InboundMessageRow(
                message_id=message.message_id,
                idempotency_key=message.idempotency_key or None,
                correlation_id=message.correlation_id,
                conversation_id=message.conversation_id,
                channel=message.channel,
                external_sender_id=message.external_sender_id,
                user_id=message.user_id,
                repository_id=message.repository_id,
                message_text=message.message_text,
                status=message.status,
                session_id=message.session_id or "",
                notice_version=getattr(message, "notice_version", "") or "",
            )
        )


def update_inbound(message_id: str, *, status: str, response: Optional[AgentResponse] = None) -> None:
    with session_scope() as db:
        row = db.query(InboundMessageRow).filter_by(message_id=message_id).first()
        if not row:
            return
        row.status = status
        if response is not None:
            row.response_json = response.model_dump_json()


def email_outbox_sent_for_inbound(message_id: str) -> bool:
    mid = (message_id or "").strip()
    if not mid:
        return False
    with session_scope() as db:
        row = (
            db.query(OutboxRow)
            .filter_by(message_id=mid, channel="email", delivery_status="sent")
            .order_by(OutboxRow.id.desc())
            .first()
        )
        return row is not None


def email_already_processed(message_id: str) -> bool:
    key = (message_id or "").strip()
    if not key:
        return False
    with session_scope() as db:
        row = db.query(InboundMessageRow).filter_by(idempotency_key=key).first()
        if row is None:
            return False
        if row.status in {"escalated", "refused"}:
            return True
        if row.status == "failed":
            return False
        if row.status == "answered":
            if row.channel == "email":
                return email_outbox_sent_for_inbound(row.message_id)
            return True
        return False


# ------------------------------------------------------------------- traces
def save_trace(pre: ValidationPayload, post: ValidationPayload) -> str:
    """Persist both validation JSONs to the database and to disk; log them."""
    settings = get_dmsl_settings()
    trace_id = new_id("trace")
    pre_json = pre.model_dump_json(indent=2)
    post_json = post.model_dump_json(indent=2)

    with session_scope() as db:
        db.add(
            ValidationTraceRow(
                trace_id=trace_id,
                correlation_id=pre.correlation_id,
                message_id=pre.message_id,
                channel=pre.channel,
                user_query=pre.user_query,
                pre_validation_json=pre_json,
                post_validation_json=post_json,
                overall_score=post.validation.overall_score if post.validation else None,
                decision=post.validation.decision if post.validation else None,
            )
        )

    try:
        folder = settings.trace_path
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{trace_id}_pre_validation.json").write_text(pre_json, encoding="utf-8")
        (folder / f"{trace_id}_post_validation.json").write_text(post_json, encoding="utf-8")
        if pre.retrieval is not None:
            retrieval_json = pre.retrieval.model_dump_json(indent=2)
            (folder / f"{trace_id}_retrieval.json").write_text(retrieval_json, encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write validation trace files: %s", exc)

    logger.info("[%s] PRE-VALIDATION JSON (%s)\n%s", pre.correlation_id, trace_id, pre_json)
    logger.info("[%s] POST-VALIDATION JSON (%s)\n%s", pre.correlation_id, trace_id, post_json)
    return trace_id


def _trace_to_dict(row: ValidationTraceRow, *, full: bool) -> dict[str, Any]:
    data: dict[str, Any] = {
        "trace_id": row.trace_id,
        "correlation_id": row.correlation_id,
        "message_id": row.message_id,
        "channel": row.channel,
        "user_query": row.user_query,
        "overall_score": row.overall_score,
        "decision": row.decision,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
    if full:
        data["pre_validation"] = json.loads(row.pre_validation_json or "{}")
        data["post_validation"] = json.loads(row.post_validation_json or "{}")
        data["retrieval"] = (data["pre_validation"] or {}).get("retrieval")
    return data


def list_traces(limit: int = 50) -> list[dict[str, Any]]:
    with session_scope() as db:
        rows = db.query(ValidationTraceRow).order_by(ValidationTraceRow.id.desc()).limit(limit).all()
        return [_trace_to_dict(r, full=False) for r in rows]


def get_trace(trace_id: str) -> Optional[dict[str, Any]]:
    with session_scope() as db:
        row = db.query(ValidationTraceRow).filter_by(trace_id=trace_id).first()
        return _trace_to_dict(row, full=True) if row else None


# -------------------------------------------------------------------- cases
def _claims_to_case_json(claims: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for claim in claims or []:
        text = getattr(claim, "claim_text", None) or (claim.get("claim_text") if isinstance(claim, dict) else "")
        if not str(text or "").strip():
            continue
        verdict = "supported"
        validation = getattr(claim, "validation", None)
        if validation is not None:
            verdict = getattr(validation, "verdict", None) or (
                validation.get("verdict") if isinstance(validation, dict) else "supported"
            )
        rows.append({"text": str(text).strip(), "type": verdict or "supported"})
    return rows


def create_case(
    message: InboundMessage,
    *,
    trace_id: str,
    reason_codes: list[str],
    priority: str = "normal",
    queue_notice: str = "",
    draft_response: str = "",
    claims: Optional[list[Any]] = None,
) -> str:
    case_id = new_id("case")
    with session_scope() as db:
        group_id = ""
        if message.user_id:
            doctor = db.query(PortalUserRow).filter_by(user_id=message.user_id).first()
            if doctor:
                group_id = doctor.assigned_group_id or ""
        db.add(
            MSLCaseRow(
                case_id=case_id,
                correlation_id=message.correlation_id,
                source_message_id=message.message_id,
                trace_id=trace_id,
                conversation_id=message.conversation_id,
                channel=message.channel,
                external_sender_id=message.external_sender_id,
                doctor_id=message.user_id,
                assigned_msl_id=message.assigned_msl_id or get_dmsl_settings().default_msl_id,
                assigned_group_id=group_id,
                repository_id=message.repository_id,
                question=message.message_text,
                reason_codes=json.dumps(reason_codes),
                priority=priority,
                status="open",
                draft_status="draft",
                approval_status="pending",
                version=1,
                queue_notice=queue_notice or None,
                draft_response=(draft_response or "").strip() or None,
                claims_json=json.dumps(_claims_to_case_json(claims or [])),
            )
        )
    return case_id


def case_conversation_detail(case: dict[str, Any], *, role: str = "msl") -> Optional[dict[str, Any]]:
    """Load Digital MSL thread for a case (conversation id, then source inbound message)."""
    for key in (case.get("conversation_id"), case.get("source_message_id")):
        token = str(key or "").strip()
        if not token:
            continue
        detail = get_conversation_detail(token, role=role)
        if detail and detail.get("messages"):
            return detail
    return None


def set_case_queue_notice(case_id: str, notice: str) -> None:
    with session_scope() as db:
        row = db.query(MSLCaseRow).filter_by(case_id=case_id).first()
        if row:
            row.queue_notice = notice


def _case_to_dict(row: MSLCaseRow) -> dict[str, Any]:
    refs = []
    if row.references_json:
        try:
            refs = json.loads(row.references_json)
        except Exception:
            pass
    claims = []
    if row.claims_json:
        try:
            claims = json.loads(row.claims_json)
        except Exception:
            pass
    return {
        "case_id": row.case_id,
        "correlation_id": row.correlation_id,
        "source_message_id": row.source_message_id,
        "trace_id": row.trace_id,
        "conversation_id": row.conversation_id or "",
        "channel": row.channel,
        "external_sender_id": row.external_sender_id,
        "doctor_id": row.doctor_id,
        "assigned_msl_id": row.assigned_msl_id,
        "assigned_group_id": getattr(row, "assigned_group_id", None) or "",
        "repository_id": getattr(row, "repository_id", None) or "",
        "question": row.question,
        "reason_codes": json.loads(row.reason_codes or "[]"),
        "status": row.status,
        "priority": row.priority,
        "resolution": row.resolution,
        "draft_response": row.draft_response or "",
        "draft_status": row.draft_status or "draft",
        "approval_status": row.approval_status or "pending",
        "reviewer_notes": row.reviewer_notes or "",
        "references": refs,
        "claims": claims,
        "version": row.version or 1,
        "author_id": row.author_id or "",
        "reviewer_id": row.reviewer_id or "",
        "queue_notice": row.queue_notice or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_cases(
    status: Optional[str] = None,
    doctor_id: Optional[str] = None,
    assigned_msl_id: Optional[str] = None,
    viewer_msl_id: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    with session_scope() as db:
        query = db.query(MSLCaseRow)
        if status:
            query = query.filter_by(status=status)
        if doctor_id:
            query = query.filter_by(doctor_id=doctor_id)
        if viewer_msl_id:
            ta_repos = repositories_for_msl(viewer_msl_id)
            if not ta_repos:
                query = query.filter(MSLCaseRow.assigned_msl_id == viewer_msl_id)
            else:
                query = query.filter(
                    or_(
                        MSLCaseRow.repository_id.in_(ta_repos),
                        MSLCaseRow.assigned_msl_id == viewer_msl_id,
                    )
                )
        elif assigned_msl_id:
            query = query.filter_by(assigned_msl_id=assigned_msl_id)
        return [_case_to_dict(r) for r in query.order_by(MSLCaseRow.id.desc()).limit(limit).all()]


def get_case(case_id: str) -> Optional[dict[str, Any]]:
    with session_scope() as db:
        row = db.query(MSLCaseRow).filter_by(case_id=case_id).first()
        return _case_to_dict(row) if row else None


def resolve_case(case_id: str, resolution: str) -> Optional[dict[str, Any]]:
    with session_scope() as db:
        row = db.query(MSLCaseRow).filter_by(case_id=case_id).first()
        if not row:
            return None
        row.status = "resolved"
        row.draft_status = "sent"
        row.resolution = resolution
        row.updated_at = datetime.now(timezone.utc)
        db.flush()
        return _case_to_dict(row)


def save_case_draft(
    case_id: str,
    *,
    author_id: str,
    draft_response: str,
    references: list[dict[str, Any]],
    claims: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    with session_scope() as db:
        row = db.query(MSLCaseRow).filter_by(case_id=case_id).first()
        if not row:
            return None
        row.draft_response = draft_response
        row.references_json = json.dumps(references)
        row.claims_json = json.dumps(claims)
        row.author_id = author_id
        if row.draft_status != "sent":
            row.draft_status = "draft"
        if (draft_response or "").strip() and row.status in {"open", "assigned"}:
            row.status = "in_progress"
        row.updated_at = datetime.now(timezone.utc)
        db.flush()
        payload = _case_to_dict(row)
    record_audit_event(
        action="case_draft_save",
        user_id=author_id,
        case_id=case_id,
        repository_id=(payload or {}).get("repository_id") or "",
        outcome=(payload or {}).get("status") or "",
    )
    return payload


def submit_case_review(
    case_id: str,
    *,
    author_id: str,
    notes: str = "",
) -> Optional[dict[str, Any]]:
    with session_scope() as db:
        row = db.query(MSLCaseRow).filter_by(case_id=case_id).first()
        if not row:
            return None
        row.draft_status = "submitted"
        row.approval_status = "in_review"
        row.reviewer_notes = notes
        row.status = "in_review"
        row.updated_at = datetime.now(timezone.utc)
        db.flush()
        return _case_to_dict(row)


def approve_case_draft(
    case_id: str,
    *,
    reviewer_id: str,
    notes: str = "",
) -> Optional[dict[str, Any]]:
    with session_scope() as db:
        row = db.query(MSLCaseRow).filter_by(case_id=case_id).first()
        if not row:
            return None
        row.draft_status = "approved"
        row.approval_status = "approved"
        row.reviewer_id = reviewer_id
        if notes:
            row.reviewer_notes = f"{row.reviewer_notes or ''}\nApproval note: {notes}".strip()
        row.updated_at = datetime.now(timezone.utc)
        db.flush()
        return _case_to_dict(row)


def reject_case_draft(
    case_id: str,
    *,
    reviewer_id: str,
    notes: str = "",
) -> Optional[dict[str, Any]]:
    with session_scope() as db:
        row = db.query(MSLCaseRow).filter_by(case_id=case_id).first()
        if not row:
            return None
        row.draft_status = "rejected"
        row.approval_status = "rejected"
        row.reviewer_id = reviewer_id
        row.reviewer_notes = notes
        row.status = "open"
        row.updated_at = datetime.now(timezone.utc)
        db.flush()
        return _case_to_dict(row)


def send_case_response(
    case_id: str,
    *,
    sender_id: str,
) -> Optional[dict[str, Any]]:
    with session_scope() as db:
        row = db.query(MSLCaseRow).filter_by(case_id=case_id).first()
        if not row:
            return None
        if row.approval_status != "approved" and row.draft_status != "approved":
            raise ValueError("Response must be approved by scientific review before sending to the doctor.")
        final_text = (row.draft_response or row.resolution or "").strip()
        if not final_text:
            raise ValueError("Draft response cannot be empty.")
        row.resolution = final_text
        row.draft_status = "sent"
        row.status = "resolved"
        row.updated_at = datetime.now(timezone.utc)
        db.flush()
        payload = _case_to_dict(row)
    record_audit_event(
        action="case_send",
        user_id=sender_id,
        case_id=case_id,
        repository_id=payload.get("repository_id") or "",
        outcome="resolved",
    )
    if payload.get("doctor_id"):
        notify_doctor_reply(payload["doctor_id"], case_id, payload.get("resolution") or "")
    return payload


# -------------------------------------------------------- conversation history
def list_conversations(
    *,
    user_id: Optional[str] = None,
    role: str = "admin",
    doctor_id: Optional[str] = None,
    status: Optional[str] = None,
    channel: Optional[str] = None,
    search: Optional[str] = None,
    session_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    with session_scope() as db:
        doctor_filter = user_id if role == "doctor" else doctor_id
        query = db.query(InboundMessageRow)
        if doctor_filter:
            query = query.filter(InboundMessageRow.user_id == doctor_filter)
        if channel:
            query = query.filter(InboundMessageRow.channel == channel)
        if search:
            s = f"%{search.strip().lower()}%"
            query = query.filter(
                (InboundMessageRow.message_text.ilike(s))
                | (InboundMessageRow.conversation_id.ilike(s))
                | (InboundMessageRow.user_id.ilike(s))
                | (InboundMessageRow.session_id.ilike(s))
            )
        if session_id:
            query = query.filter(InboundMessageRow.session_id == session_id.strip())

        all_rows = query.order_by(InboundMessageRow.id.desc()).all()
        conv_map: dict[str, list[InboundMessageRow]] = {}
        for r in all_rows:
            cid = r.conversation_id or f"conv_{r.message_id}"
            if cid not in conv_map:
                conv_map[cid] = []
            conv_map[cid].append(r)

        users = {u.user_id: u.display_name for u in db.query(PortalUserRow).all()}
        cids = list(conv_map.keys())
        cases_by_conv: dict[str, MSLCaseRow] = {}
        if cids:
            c_rows = db.query(MSLCaseRow).filter(MSLCaseRow.conversation_id.in_(cids)).all()
            for c in c_rows:
                if c.conversation_id:
                    cases_by_conv[c.conversation_id] = c

        conversations = []
        for cid, msgs in conv_map.items():
            sorted_msgs = sorted(msgs, key=lambda m: m.id)
            first_msg = sorted_msgs[0]
            last_msg = sorted_msgs[-1]

            linked_case = cases_by_conv.get(cid)
            last_resp: dict[str, Any] = {}
            if last_msg.response_json:
                try:
                    last_resp = json.loads(last_msg.response_json)
                except Exception:
                    pass
            has_escalation = any(m.status == "escalated" for m in sorted_msgs) or (linked_case is not None)
            is_refused = any(m.status == "refused" for m in sorted_msgs) or last_resp.get("decision") == "refuse"

            if linked_case:
                conv_status = linked_case.status
            elif any(m.status == "escalated" for m in sorted_msgs):
                conv_status = "escalated"
            elif is_refused:
                conv_status = "refused"
            else:
                conv_status = "resolved"

            wanted = "resolved" if status == "answered" else status
            if wanted and conv_status != wanted:
                continue

            doc_id = first_msg.user_id or "web_user"
            conversations.append({
                "conversation_id": cid,
                "doctor_id": doc_id,
                "doctor_name": users.get(doc_id, doc_id),
                "channel": first_msg.channel or "web",
                "message_count": len(sorted_msgs),
                "start_time": utc_iso(first_msg.received_at),
                "last_activity": utc_iso(last_msg.updated_at or last_msg.received_at),
                "latest_question": last_msg.message_text,
                "status": conv_status,
                "has_escalation": has_escalation,
                "msl_case_id": linked_case.case_id if linked_case else (last_resp.get("msl_case_id") or None),
                "msl_case_status": linked_case.status if linked_case else None,
                "validation_outcome": "escalated" if has_escalation else "supported",
                "assigned_msl_id": linked_case.assigned_msl_id if linked_case else None,
                "session_id": getattr(first_msg, "session_id", None) or cid,
            })

        conversations.sort(key=lambda c: c["last_activity"] or "", reverse=True)
        total_count = len(conversations)
        paginated = conversations[offset : offset + limit]

        return {
            "conversations": paginated,
            "total": total_count,
            "limit": limit,
            "offset": offset,
        }


def get_conversation_detail(
    conversation_id: str,
    *,
    user_id: Optional[str] = None,
    role: str = "admin",
) -> Optional[dict[str, Any]]:
    with session_scope() as db:
        rows = (
            db.query(InboundMessageRow)
            .filter_by(conversation_id=conversation_id)
            .order_by(InboundMessageRow.id.asc())
            .all()
        )
        if not rows:
            rows = (
                db.query(InboundMessageRow)
                .filter_by(message_id=conversation_id)
                .all()
            )
        if not rows:
            return None

        first_msg = rows[0]
        if role == "doctor" and user_id and first_msg.user_id != user_id:
            raise PermissionError("Access forbidden: Doctors can only view their own conversation history.")

        users = {u.user_id: u.display_name for u in db.query(PortalUserRow).all()}
        linked_case_row = (
            db.query(MSLCaseRow)
            .filter(
                (MSLCaseRow.conversation_id == conversation_id)
                | (MSLCaseRow.source_message_id.in_([r.message_id for r in rows]))
            )
            .order_by(MSLCaseRow.id.desc())
            .first()
        )
        linked_case = _case_to_dict(linked_case_row) if linked_case_row else None

        messages = []
        for r in rows:
            resp_data = None
            if r.response_json:
                try:
                    resp_data = json.loads(r.response_json)
                except Exception:
                    pass

            trace_summary = None
            if resp_data and resp_data.get("trace_id"):
                trace_summary = get_trace(resp_data["trace_id"])
            if resp_data and resp_data.get("decision") == "escalate_to_msl":
                post = (trace_summary or {}).get("post_validation") or {}
                if not resp_data.get("presentation_body"):
                    resp_data = {
                        **resp_data,
                        "presentation_body": (post.get("draft_answer") or "").strip(),
                    }
                if not resp_data.get("claims") and post.get("claims"):
                    resp_data = {**resp_data, "claims": post.get("claims") or []}

            messages.append({
                "message_id": r.message_id,
                "correlation_id": r.correlation_id,
                "question": r.message_text,
                "received_at": utc_iso(r.received_at),
                "status": r.status,
                "channel": r.channel,
                "is_msl_response": False,
                "response": resp_data,
                "trace": trace_summary,
            })

            # If Physical MSL has provided a resolution, inject as distinct author message
            if linked_case and linked_case.get("resolution") and linked_case.get("status") in {"resolved", "sent"}:
                if r.message_id == linked_case.get("source_message_id"):
                    msl_author = users.get(linked_case.get("assigned_msl_id") or "", "Physical MSL")
                    messages.append({
                        "message_id": f"msl_reply_{linked_case['case_id']}",
                        "correlation_id": linked_case["correlation_id"],
                        "question": None,
                        "received_at": linked_case.get("updated_at") or linked_case.get("created_at"),
                        "status": "resolved",
                        "channel": linked_case["channel"],
                        "is_msl_response": True,
                        "author_name": msl_author,
                        "response": {
                            "answer": linked_case["resolution"],
                            "decision": "msl_resolved",
                            "overall_score": 1.0,
                            "claims": linked_case.get("claims") or [],
                            "references": linked_case.get("references") or [],
                            "msl_case_id": linked_case["case_id"],
                        },
                        "trace": None,
                    })

        doc_id = first_msg.user_id or "web_user"
        return {
            "conversation_id": conversation_id,
            "doctor_id": doc_id,
            "doctor_name": users.get(doc_id, doc_id),
            "channel": first_msg.channel or "web",
            "start_time": utc_iso(first_msg.received_at),
            "last_activity": utc_iso(rows[-1].updated_at or rows[-1].received_at),
            "messages": messages,
            "case": linked_case,
        }


# ------------------------------------------------------------------- outbox
def add_outbox(
    *,
    message: InboundMessage,
    recipient: str,
    subject: str,
    body: str,
    delivery_status: str,
    provider_detail: str = "",
) -> str:
    outbox_id = new_id("out")
    with session_scope() as db:
        db.add(
            OutboxRow(
                outbox_id=outbox_id,
                message_id=message.message_id,
                correlation_id=message.correlation_id,
                channel=message.channel,
                recipient=recipient,
                subject=subject,
                body=body,
                delivery_status=delivery_status,
                provider_detail=provider_detail,
            )
        )
    return outbox_id


def list_outbox(channel: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
    with session_scope() as db:
        query = db.query(OutboxRow)
        if channel:
            query = query.filter_by(channel=channel)
        rows = query.order_by(OutboxRow.id.desc()).limit(limit).all()
        return [
            {
                "outbox_id": r.outbox_id,
                "message_id": r.message_id,
                "correlation_id": r.correlation_id,
                "channel": r.channel,
                "recipient": r.recipient,
                "subject": r.subject,
                "body": r.body,
                "delivery_status": r.delivery_status,
                "provider_detail": r.provider_detail,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


# -------------------------------------------------------------- portal users
def hash_password(password: str) -> str:
    return security.hash_password(password)


def verify_password(password: str, hashed: Optional[str]) -> bool:
    """Strict check: a missing hash or password never verifies."""
    return security.verify_password(password, hashed)


def password_needs_rehash(hashed: Optional[str]) -> bool:
    return security.password_needs_rehash(hashed)


class DuplicateError(ValueError):
    """The email, mobile number or registration already exists."""


class AccountLocked(ValueError):
    """Too many failed sign-in attempts."""


_SEED_USERS = (
    ("usr_admin", "admin@digital-msl.local", "Platform Admin", "admin", "", "AdminPass2026!"),
    ("usr_msl", "msl@digital-msl.local", "Priya Raman, MSL", "msl", "", "MslPass2026!"),
    ("usr_msl_arun", "msl.arun@digital-msl.local", "Arun Krishnan, MSL", "msl", "", "MslPass2026!"),
    ("usr_msl_meera", "msl.meera@digital-msl.local", "Meera Shah, MSL", "msl", "", "MslPass2026!"),
    ("usr_doctor", "doctor@digital-msl.local", "Dr. Arjun Mehta", "doctor", "usr_msl", "DoctorPass2026!"),
)

_PEER_MSL_IDS = ("usr_msl_arun", "usr_msl_meera")


def _repo_ids(row: PortalUserRow) -> list[str]:
    raw = getattr(row, "assigned_repository_ids", None) or "[]"
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        return [str(x) for x in (data or []) if str(x).strip()]
    except Exception:
        return []


def _latest_consents(db: Session, user_id: str) -> dict[str, dict[str, Any]]:
    """Current state per consent type (newest ledger row wins)."""
    state: dict[str, dict[str, Any]] = {}
    rows = db.query(ConsentRow).filter_by(user_id=user_id).order_by(ConsentRow.id.asc()).all()
    for row in rows:
        state[row.consent_type] = {
            "granted": bool(row.granted),
            "version": row.version or "",
            "updated_at": utc_iso(row.created_at),
        }
    return state


def _user_to_dict(row: PortalUserRow) -> dict[str, Any]:
    phone = security.decrypt_text(getattr(row, "whatsapp_phone_enc", None) or "") or (getattr(row, "whatsapp_phone", None) or "")
    data = {
        "user_id": row.user_id,
        "email": row.email,
        "display_name": row.display_name,
        "role": row.role,
        "assigned_msl_id": row.assigned_msl_id or "",
        "assigned_group_id": row.assigned_group_id or "",
        "assigned_repository_ids": _repo_ids(row),
        "default_repository_id": getattr(row, "default_repository_id", None) or (_repo_ids(row)[0] if _repo_ids(row) else ""),
        "whatsapp_phone": phone,
        "status": row.status,
        "mfa_enabled": bool(getattr(row, "mfa_enabled_at", None) and getattr(row, "mfa_secret_enc", None)),
        "is_demo": bool(getattr(row, "is_demo", 0)),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
    db = object_session(row)
    if row.role == "doctor" and db is not None:
        profile = db.query(DoctorProfileRow).filter_by(user_id=row.user_id).first()
        consents = _latest_consents(db, row.user_id)
        data["verification_status"] = registry.normalize_status(profile.verification_status if profile else None)
        data["email_verified"] = bool(profile.email_verified_at) if profile else True
        data["mobile_verified"] = bool(profile.mobile_verified_at) if profile else True
        data["consents"] = {
            "whatsapp_messaging": bool(consents.get("whatsapp_messaging", {}).get("granted")),
            "email_messaging": bool(consents.get("email_messaging", {}).get("granted")),
        }
    return data


def seed_portal_users() -> None:
    if not get_dmsl_settings().legacy_demo_users:
        return
    with session_scope() as db:
        for user_id, email, name, role, msl, pwd in _SEED_USERS:
            row = db.query(PortalUserRow).filter_by(email=email).first()
            if row:
                if not row.password_hash:
                    row.password_hash = hash_password(pwd)
                if not row.display_name:
                    row.display_name = name
                if role == "doctor" and not row.assigned_msl_id:
                    row.assigned_msl_id = msl
                continue
            db.add(
                PortalUserRow(
                    user_id=user_id,
                    email=email,
                    display_name=name,
                    role=role,
                    assigned_msl_id=msl,
                    password_hash=hash_password(pwd),
                )
            )
    _align_peer_msls_with_priya()
    backfill_doctor_profiles()


def _align_peer_msls_with_priya() -> None:
    """Put demo peer MSLs on Priya's therapeutic areas and the default Physical MSL group."""
    priya_repos = repositories_for_msl("usr_msl")
    with session_scope() as db:
        group = db.query(MslGroupRow).order_by(MslGroupRow.id).first()
        group_id = group.group_id if group else ""
        for msl_id in _PEER_MSL_IDS:
            if group_id and not db.query(MslGroupMemberRow).filter_by(group_id=group_id, msl_user_id=msl_id).first():
                if db.query(PortalUserRow).filter_by(user_id=msl_id, role="msl").first():
                    db.add(MslGroupMemberRow(group_id=group_id, msl_user_id=msl_id))
        for repo_id in priya_repos:
            row = db.query(TherapeuticAreaRow).filter_by(repository_id=repo_id).first()
            if row is None:
                continue
            existing = {m.msl_user_id for m in db.query(TherapeuticAreaMslRow).filter_by(repository_id=repo_id).all()}
            for msl_id in _PEER_MSL_IDS:
                if msl_id in existing:
                    continue
                if db.query(PortalUserRow).filter_by(user_id=msl_id, role="msl").first():
                    db.add(TherapeuticAreaMslRow(repository_id=repo_id, msl_user_id=msl_id))


def list_portal_users(role: Optional[str] = None) -> list[dict[str, Any]]:
    with session_scope() as db:
        query = db.query(PortalUserRow)
        if role:
            query = query.filter_by(role=role)
        return [_user_to_dict(r) for r in query.order_by(PortalUserRow.role, PortalUserRow.display_name).all()]


def get_portal_user(user_id: str) -> Optional[dict[str, Any]]:
    with session_scope() as db:
        row = db.query(PortalUserRow).filter_by(user_id=user_id).first()
        return _user_to_dict(row) if row else None


def get_portal_user_by_email(email: str) -> Optional[dict[str, Any]]:
    with session_scope() as db:
        row = db.query(PortalUserRow).filter_by(email=email.strip().lower()).first()
        return _user_to_dict(row) if row else None


def get_portal_user_by_whatsapp(phone: str) -> Optional[dict[str, Any]]:
    from digital_msl.whatsapp_bridge import normalize_phone

    norm = normalize_phone(phone)
    if not norm or norm.endswith("@g.us"):
        return None
    idx = phone_index(phone)
    if not idx:
        return None
    with session_scope() as db:
        row = db.query(PortalUserRow).filter_by(whatsapp_phone_idx=idx).first()
        return _user_to_dict(row) if row else None


def portal_me_payload(user_id: str) -> Optional[dict[str, Any]]:
    user = get_portal_user(user_id)
    if not user:
        return None
    therapeutic_areas: list[dict[str, Any]] = []
    if user.get("role") == "doctor":
        assigned = list(user.get("assigned_repository_ids") or [])
        default_id = user.get("default_repository_id") or ""
        with session_scope() as db:
            for repo_id in assigned:
                if not repo_id:
                    continue
                row = db.query(TherapeuticAreaRow).filter_by(repository_id=repo_id).first()
                therapeutic_areas.append(
                    {
                        "repository_id": repo_id,
                        "name": (row.name if row and row.name else None) or repo_id,
                        "description": (row.description if row else None) or "",
                        "is_default": repo_id == default_id,
                    }
                )
    return {"user": user, "therapeutic_areas": therapeutic_areas}


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


DEMO_PASSWORD = "DemoMSL#2026"  # 12 characters; demo mode only
DEMO_ACCOUNTS = (
    ("usr_demo_doctor", "doctor@demo.example", "Demo Doctor", "doctor"),
    ("usr_demo_msl", "msl@demo.example", "Demo Physical MSL", "msl"),
    ("usr_demo_admin", "admin@demo.example", "Demo Administrator", "admin"),
)
_LEGACY_DEMO_DOMAINS = ("@digital-msl.local", "@demo.example")


DEMO_DOCTOR_AREA_SLUGS = ("retina-and-vitreous", "diabetic-retinopathy-and-macular-disease")


def seed_demo_accounts() -> list[str]:
    """Create / refresh the demo accounts. Runs only in demo mode (never in production: see check_demo_guard)."""
    check_demo_guard()
    if not get_dmsl_settings().demo_enabled:
        raise RuntimeError("Demo accounts can only be seeded when DIGITAL_MSL_DEMO_MODE is on.")
    seed_default_areas(actor_id="system", role="system")  # idempotent local rows, no IKM calls
    now = _now()
    seeded: list[str] = []
    with session_scope() as db:
        # Demo mode only: the two launch areas are switched on so the demo doctor can use them.
        for slug in DEMO_DOCTOR_AREA_SLUGS:
            area = db.query(TherapeuticAreaRow).filter_by(slug=slug).first()
            if area is not None:
                area.active_flag = 1
        db.flush()
        enabled_areas = [
            r.repository_id
            for r in db.query(TherapeuticAreaRow).filter(TherapeuticAreaRow.active_flag == 1).all()
        ]
        demo_doctor_areas = [
            r.repository_id
            for slug in DEMO_DOCTOR_AREA_SLUGS
            for r in [db.query(TherapeuticAreaRow).filter_by(slug=slug).first()]
            if r is not None
        ]
        for user_id, email, name, role in DEMO_ACCOUNTS:
            row = db.query(PortalUserRow).filter_by(email=email).first()
            if row is not None and not row.is_demo:
                continue  # never touch a real account that happens to use a demo address
            if row is None:
                row = PortalUserRow(user_id=user_id, email=email, display_name=name, role=role)
                db.add(row)
            row.is_demo = 1
            row.display_name = name
            row.role = role
            row.status = "active"
            row.password_hash = security.hash_password(DEMO_PASSWORD) if not security.verify_password(DEMO_PASSWORD, row.password_hash) else row.password_hash
            seeded.append(email)
            db.flush()
            if role == "msl":
                for repo_id in enabled_areas:
                    if not db.query(TherapeuticAreaMslRow).filter_by(repository_id=repo_id, msl_user_id=row.user_id).first():
                        db.add(TherapeuticAreaMslRow(repository_id=repo_id, msl_user_id=row.user_id))
                    area = db.query(TherapeuticAreaRow).filter_by(repository_id=repo_id).first()
                    if area is not None and not area.owner_msl_id:
                        area.owner_msl_id = row.user_id
            if role == "doctor":
                row.assigned_msl_id = "usr_demo_msl"
                row.assigned_repository_ids = json.dumps(demo_doctor_areas or enabled_areas[:3])
                row.default_repository_id = (demo_doctor_areas or enabled_areas or [""])[0]
                profile = db.query(DoctorProfileRow).filter_by(user_id=row.user_id).first()
                if profile is None:
                    profile = DoctorProfileRow(user_id=row.user_id)
                    db.add(profile)
                profile.verification_status = registry.VERIFIED
                profile.verification_source = "demo_seed"
                profile.verification_notes = "Demo account."
                profile.verified_at = now
                profile.verified_by = "system"
                profile.email_verified_at = now
                profile.mobile_verified_at = now
                if not db.query(ConsentRow).filter_by(user_id=row.user_id, source="demo_seed").first():
                    for ctype in registry.CONSENT_TYPES:
                        db.add(
                            ConsentRow(
                                consent_id=new_id("cns"),
                                user_id=row.user_id,
                                consent_type=ctype,
                                channel=_consent_channel(ctype),
                                granted=1,
                                version=registry.CONSENT_VERSION,
                                source="demo_seed",
                            )
                        )
    return seeded


def report_demo_accounts() -> list[str]:
    """Demo mode is off: in production, warn (log + audit) about any demo or legacy seeded accounts."""
    settings = get_dmsl_settings()
    if not settings.is_production:
        return []
    with session_scope() as db:
        found = [
            r.email
            for r in db.query(PortalUserRow).all()
            if getattr(r, "is_demo", 0) or (r.email or "").lower().endswith(_LEGACY_DEMO_DOMAINS)
        ]
    if found:
        logger.warning(
            "Demo / legacy seeded accounts exist in a production environment (%s). Disable or delete them: %s",
            len(found),
            ", ".join(found),
        )
        record_audit_event(
            action="demo_accounts_present",
            entity_type="user",
            outcome="warning",
            detail={"accounts": found, "environment": settings.environment},
        )
    return found


def _recent_failed_logins(db: Session, email_n: str) -> int:
    """Failures since the last success, inside the lockout window."""
    from datetime import timedelta

    window_start = datetime.now(timezone.utc) - timedelta(minutes=get_dmsl_settings().lockout_minutes)
    rows = (
        db.query(LoginAttemptRow)
        .filter(LoginAttemptRow.email == email_n)
        .order_by(LoginAttemptRow.id.desc())
        .limit(50)
        .all()
    )
    failures = 0
    for row in rows:
        created = _as_utc(row.created_at)
        if created is None or created < window_start or row.success:
            break
        failures += 1
    return failures


_DUMMY_HASH = ""


def authenticate_portal_user(
    *,
    email: str,
    password: str,
    role: Optional[str] = None,
    ip_address: str = "",
) -> dict[str, Any]:
    """Verify credentials. Never creates accounts and never lets the caller choose a role."""
    global _DUMMY_HASH
    settings = get_dmsl_settings()
    email_n = (email or "").strip().lower()
    outcome_user = ""
    user: dict[str, Any] = {}
    locked = False
    with session_scope() as db:
        if _recent_failed_logins(db, email_n) >= settings.max_failed_logins:
            db.add(LoginAttemptRow(email=email_n, success=0, ip_address=ip_address))
            locked = True
            ok = False
        else:
            row = db.query(PortalUserRow).filter_by(email=email_n).first()
            if row is None:
                if not _DUMMY_HASH:
                    _DUMMY_HASH = hash_password("dummy-password-for-timing")
                verify_password(password or "", _DUMMY_HASH)  # keep timing similar for unknown emails
                ok = False
            else:
                outcome_user = row.user_id
                ok = verify_password(password or "", row.password_hash)
                if ok and role and role != row.role:
                    ok = False
                if ok and (row.status or "active") != "active":
                    ok = False
            db.add(LoginAttemptRow(email=email_n, success=1 if ok else 0, ip_address=ip_address))
            if ok:
                if password_needs_rehash(row.password_hash):
                    row.password_hash = hash_password(password)
                db.flush()
                user = _user_to_dict(row)
    if not ok:
        record_audit_event(
            action="login_failed",
            user_id=outcome_user,
            outcome="locked" if locked else "denied",
            detail={"email": email_n, "ip": ip_address},
        )
        if locked:
            raise AccountLocked(
                f"Too many failed sign-in attempts. Try again in {settings.lockout_minutes} minutes."
            )
        raise ValueError("Invalid email or password.")
    return user


def upsert_portal_user(
    *,
    email: str,
    display_name: str,
    role: str,
    assigned_msl_id: str = "",
    user_id: Optional[str] = None,
    password: Optional[str] = None,
) -> dict[str, Any]:
    """Provision a portal user (admin / seed use). Existing users keep their role."""
    if role not in {"admin", "doctor", "msl"}:
        raise ValueError("role must be admin, doctor, or msl")
    email_n = email.strip().lower()
    with session_scope() as db:
        row = db.query(PortalUserRow).filter_by(email=email_n).first()
        if row:
            if row.role != role:
                raise ValueError("A user with this email already exists with a different role.")
            row.display_name = display_name.strip() or row.display_name
            if assigned_msl_id or role != "doctor":
                row.assigned_msl_id = assigned_msl_id
            if password:
                row.password_hash = hash_password(password)
            db.flush()
            return _user_to_dict(row)
        created = PortalUserRow(
            user_id=user_id or new_id("usr"),
            email=email_n,
            display_name=display_name.strip() or email_n,
            role=role,
            assigned_msl_id=assigned_msl_id,
            password_hash=hash_password(password) if password else None,
        )
        db.add(created)
        db.flush()
        return _user_to_dict(created)


_IKM_LIBRARIES_CACHE: tuple[float, list[dict[str, str]]] | None = None
_IKM_LIBRARIES_CACHE_TTL = 300.0
_IKM_LIST_TIMEOUT_SEC = 8.0


def _fetch_ikm_libraries_sync() -> list[dict[str, str]]:
    try:
        from services.dms_client import DmsClient

        payload = DmsClient().list_repositories() or {}
        items = payload.get("repositories") or payload.get("items") or payload.get("data") or []
        if isinstance(items, dict):
            items = items.get("items") or items.get("repositories") or []
        libraries: list[dict[str, str]] = []
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                repo_id = str(item.get("repository_id") or item.get("id") or "").strip()
                name = str(item.get("name") or repo_id).strip()
                if not repo_id or name.startswith("_temp_"):
                    continue
                libraries.append({"repository_id": repo_id, "name": name})
        return libraries
    except Exception as exc:
        logger.warning("Could not list IKM libraries: %s", exc)
        return []


def list_ikm_libraries(*, timeout_seconds: float = _IKM_LIST_TIMEOUT_SEC) -> list[dict[str, str]]:
    """Repository ids/names from IKM/DMS, used for doctor assignment."""
    global _IKM_LIBRARIES_CACHE
    now = time.time()
    if _IKM_LIBRARIES_CACHE and now - _IKM_LIBRARIES_CACHE[0] < _IKM_LIBRARIES_CACHE_TTL:
        return list(_IKM_LIBRARIES_CACHE[1])
    libraries: list[dict[str, str]] = []
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_fetch_ikm_libraries_sync)
            libraries = future.result(timeout=max(1.0, float(timeout_seconds)))
    except FuturesTimeout:
        logger.warning("IKM list_repositories timed out after %.1fs", timeout_seconds)
        libraries = []
    _IKM_LIBRARIES_CACHE = (now, libraries)
    return libraries


def seed_demo_doctor_library() -> None:
    """Give the demo doctor the first available IKM library, if any."""
    with session_scope() as db:
        doctor = db.query(PortalUserRow).filter_by(user_id="usr_doctor").first()
        if not doctor or _repo_ids(doctor):
            return
    libraries = list_ikm_libraries()
    for lib in libraries:
        upsert_therapeutic_area(
            repository_id=lib["repository_id"],
            name=lib["name"],
            owner_msl_id="usr_msl",
            default_flag=lib == libraries[0],
        )
    first_id = libraries[0]["repository_id"] if libraries else ""
    if first_id:
        assign_doctor_repositories("usr_doctor", [first_id])
        upsert_therapeutic_area(
            repository_id=first_id,
            name=libraries[0]["name"],
            owner_msl_id="usr_msl",
            default_flag=True,
        )


def assign_doctor_repositories(doctor_id: str, repository_ids: list[str]) -> Optional[dict[str, Any]]:
    with session_scope() as db:
        doctor = db.query(PortalUserRow).filter_by(user_id=doctor_id, role="doctor").first()
        if not doctor:
            return None
        cleaned = [str(x).strip() for x in repository_ids if str(x).strip()][:3]
        doctor.assigned_repository_ids = json.dumps(cleaned)
        if not (doctor.default_repository_id or "") or doctor.default_repository_id not in cleaned:
            doctor.default_repository_id = cleaned[0] if cleaned else ""
        db.flush()
        return _user_to_dict(doctor)


def assign_doctor_msl(doctor_id: str, msl_id: str) -> Optional[dict[str, Any]]:
    with session_scope() as db:
        doctor = db.query(PortalUserRow).filter_by(user_id=doctor_id, role="doctor").first()
        msl = db.query(PortalUserRow).filter_by(user_id=msl_id, role="msl").first()
        if not doctor or not msl:
            return None
        doctor.assigned_msl_id = msl.user_id
        db.flush()
        return _user_to_dict(doctor)


def set_doctor_default_ta(doctor_id: str, repository_id: str) -> Optional[dict[str, Any]]:
    with session_scope() as db:
        doctor = db.query(PortalUserRow).filter_by(user_id=doctor_id, role="doctor").first()
        if not doctor:
            return None
        allowed = _repo_ids(doctor)
        if repository_id not in allowed:
            if len(allowed) >= 3:
                allowed = allowed[1:] + [repository_id]
            else:
                allowed.append(repository_id)
            doctor.assigned_repository_ids = json.dumps(allowed)
        doctor.default_repository_id = repository_id
        db.flush()
        return _user_to_dict(doctor)


NEUTRAL_DUPLICATE = (
    "We could not complete registration with these details. "
    "If you already have an account, sign in or contact support."
)


class OtpError(ValueError):
    """Contact-verification problem; ``status_code`` tells the API which HTTP status to use."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _phone_digits(phone: str) -> str:
    from digital_msl.whatsapp_bridge import normalize_phone

    return re.sub(r"\D", "", normalize_phone(phone) or "")


def phone_index(phone: str) -> str:
    digits = _phone_digits(phone)
    return security.blind_index(digits, "phone") if digits else ""


def mask_mobile(mobile: str) -> str:
    digits = re.sub(r"\D", "", mobile or "")
    return f"+{digits[:2]}{'*' * max(0, len(digits) - 6)}{digits[-4:]}" if len(digits) > 6 else "***"


def mask_email(email: str) -> str:
    local, _, domain = (email or "").partition("@")
    return f"{local[:1]}{'*' * max(1, len(local) - 1)}@{domain}" if domain else "***"


# ------------------------------------------------------------------ councils
def ensure_councils() -> None:
    with session_scope() as db:
        if db.query(CouncilRow).count():
            return
        for order, (council_id, name) in enumerate(registry.COUNCIL_SEED):
            db.add(CouncilRow(council_id=council_id, name=name, sort_order=order, enabled=1))


def list_councils() -> list[dict[str, str]]:
    ensure_councils()
    with session_scope() as db:
        rows = db.query(CouncilRow).filter_by(enabled=1).order_by(CouncilRow.sort_order).all()
        return [{"id": r.council_id, "name": r.name} for r in rows]


def council_name_map() -> dict[str, str]:
    return {c["name"]: c["id"] for c in list_councils()}


def migrate_pii() -> None:
    """Encrypt plaintext registration numbers / mobiles left by earlier versions and clear them."""
    names = council_name_map()
    with session_scope() as db:
        for profile in db.query(DoctorProfileRow).all():
            if profile.registration_number and not profile.registration_number_enc:
                profile.registration_number_enc = security.encrypt_text(profile.registration_number)
                profile.registration_number_idx = security.blind_index(profile.registration_number, "regno")
                profile.registration_number = None
            if profile.mobile and not profile.mobile_enc:
                profile.mobile_enc = security.encrypt_text(profile.mobile)
                profile.mobile_idx = phone_index(profile.mobile) or None
                profile.mobile = None
            if not profile.council_id and profile.registration_council:
                profile.council_id = names.get(profile.registration_council)
        for user in db.query(PortalUserRow).filter(PortalUserRow.whatsapp_phone != "").all():
            if user.whatsapp_phone and not user.whatsapp_phone_enc:
                user.whatsapp_phone_enc = security.encrypt_text(user.whatsapp_phone)
                user.whatsapp_phone_idx = phone_index(user.whatsapp_phone) or None
                user.whatsapp_phone = ""


# ------------------------------------------------------------------ registration
def register_doctor(
    *,
    email: str,
    display_name: str,
    password: str,
    repository_ids: list[str],
    default_repository_id: str = "",
    profile: Optional[dict[str, Any]] = None,
    consents: Optional[dict[str, Any]] = None,
    consent_version: str = "",
    ip_address: str = "",
    user_agent: str = "",
) -> dict[str, Any]:
    """Create a doctor account (PENDING_CONTACT_VERIFICATION) with professional details and consent records.

    ``profile`` / ``consents`` are always supplied by the registration API. They are optional only so
    internal callers and tests can create a bare doctor (contact checks skipped, status PENDING_REGISTRATION).
    """
    email_n = email.strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email_n) or len(email_n) > 255:
        raise ValueError("Enter a valid email address.")
    name = registry.validate_display_name(display_name)
    registry.validate_password(password)
    cleaned = list(dict.fromkeys(str(x).strip() for x in repository_ids if str(x).strip()))[:3]
    if not cleaned:
        raise ValueError("Select at least one therapeutic area.")
    for repo_id in cleaned:
        if not is_therapeutic_area_active(repo_id):
            raise ValueError("One or more selected therapeutic areas are not available yet.")
    details: Optional[dict[str, Any]] = None
    granted: Optional[dict[str, bool]] = None
    if profile is not None:
        details = registry.validate_professional_details(profile, council_name_map())
        granted = registry.validate_consents(consents or {}, consent_version)
        for repo_id in cleaned:
            if repo_id.startswith("pending:"):
                raise ValueError("One or more selected therapeutic areas are not available yet.")
    default_id = default_repository_id if default_repository_id in cleaned else cleaned[0]
    cover: list[str] = []
    for repo_id in cleaned:
        cover.extend(msls_for_repository(repo_id))
    cover = list(dict.fromkeys(cover))
    user_id = new_id("usr")
    now = _now()
    with session_scope() as db:
        if db.query(PortalUserRow).filter_by(email=email_n).first():
            raise DuplicateError(NEUTRAL_DUPLICATE)
        fields: dict[str, Any] = {}
        if details:
            reg_idx = security.blind_index(details["registration_number"], "regno")
            mob_idx = phone_index(details["mobile"])
            if db.query(DoctorProfileRow).filter_by(council_id=details["council_id"], registration_number_idx=reg_idx).first():
                raise DuplicateError(NEUTRAL_DUPLICATE)
            if mob_idx and db.query(DoctorProfileRow).filter_by(mobile_idx=mob_idx).first():
                raise DuplicateError(NEUTRAL_DUPLICATE)
            fields = {
                "mobile_enc": security.encrypt_text(details["mobile"]),
                "mobile_idx": mob_idx or None,
                "registration_number_enc": security.encrypt_text(details["registration_number"]),
                "registration_number_idx": reg_idx,
                "registration_council": details["registration_council"],
                "council_id": details["council_id"],
                "registration_year": details["registration_year"],
                "basic_qualification": details["basic_qualification"],
                "highest_qualification": details["highest_qualification"],
                "speciality": details["speciality"],
                "institution": details["institution"],
                "practice_type": details["practice_type"],
                "city": details["city"],
                "state": details["state"],
                "pincode": details["pincode"],
                "nmr_id": details["nmr_id"],
            }
            status, source, notes = registry.PENDING_CONTACT, "manual", "Awaiting email and mobile verification."
        else:
            status, source, notes = registry.PENDING_REGISTRATION, "manual", "No professional details on file; confirm registration."
            fields = {"email_verified_at": now, "mobile_verified_at": now}
        row = PortalUserRow(
            user_id=user_id,
            email=email_n,
            display_name=name,
            role="doctor",
            assigned_msl_id=cover[0] if cover else "usr_msl",
            assigned_repository_ids=json.dumps(cleaned),
            default_repository_id=default_id,
            password_hash=hash_password(password),
        )
        db.add(row)
        db.add(
            DoctorProfileRow(
                user_id=user_id,
                verification_status=status,
                verification_source=source,
                verification_notes=notes,
                **fields,
            )
        )
        if granted is not None:
            for item in registry.CONSENT_ITEMS:
                db.add(
                    ConsentRow(
                        consent_id=new_id("cns"),
                        user_id=user_id,
                        consent_type=item["type"],
                        channel=_consent_channel(item["type"]),
                        granted=1 if granted[item["type"]] else 0,
                        version=registry.CONSENT_VERSION,
                        text_sha256=hashlib.sha256(item["text"].encode("utf-8")).hexdigest(),
                        source="registration",
                        ip_address=ip_address,
                        user_agent=(user_agent or "")[:255],
                    )
                )
        db.flush()
        user_dict = _user_to_dict(row)
    record_audit_event(
        action="doctor_register",
        user_id=user_id,
        role="doctor",
        entity_type="doctor",
        entity_id=user_id,
        after={"verification_status": status, "council_id": (details or {}).get("council_id", "")},
        detail={
            "repository_ids": cleaned,
            "registration_number_tail": ((details or {}).get("registration_number", ""))[-4:],
            "consents": granted or {},
            "consent_version": registry.CONSENT_VERSION if granted is not None else "",
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return user_dict


def _consent_channel(consent_type: str) -> str:
    return {"whatsapp_messaging": "whatsapp", "email_messaging": "email"}.get(consent_type, "")


def backfill_doctor_profiles() -> None:
    """Give doctors that predate the registry a profile row, so verification can track them."""
    with session_scope() as db:
        for doctor in db.query(PortalUserRow).filter_by(role="doctor").all():
            if db.query(DoctorProfileRow).filter_by(user_id=doctor.user_id).first():
                continue
            demo = doctor.user_id == "usr_doctor" and get_dmsl_settings().legacy_demo_users
            now = _now()
            db.add(
                DoctorProfileRow(
                    user_id=doctor.user_id,
                    verification_status=registry.VERIFIED if demo else registry.PENDING_REGISTRATION,
                    verification_source="demo_seed" if demo else "manual",
                    verification_notes=(
                        "Demo account." if demo else "Registered before verification existed; confirm registration details."
                    ),
                    verified_at=now if demo else None,
                    verified_by="system" if demo else "",
                    email_verified_at=now,
                    mobile_verified_at=now,
                )
            )
            if demo:
                for ctype in registry.CONSENT_TYPES:
                    db.add(
                        ConsentRow(
                            consent_id=new_id("cns"),
                            user_id=doctor.user_id,
                            consent_type=ctype,
                            channel=_consent_channel(ctype),
                            granted=1,
                            version=registry.CONSENT_VERSION,
                            source="demo_seed",
                        )
                    )


# ------------------------------------------------------------------ contact verification (OTP)
OTP_TTL_SECONDS = 300
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_COOLDOWN_SECONDS = 60
OTP_MAX_SENDS = 3


def issue_contact_otps(user_id: str, channels: tuple[str, ...] = ("email", "mobile")) -> dict[str, str]:
    """Create fresh codes (older unused ones are voided). Returns plaintext codes for delivery only."""
    codes: dict[str, str] = {}
    now = _now()
    from datetime import timedelta

    with session_scope() as db:
        for channel in channels:
            previous = db.query(OtpRow).filter_by(user_id=user_id, channel=channel).all()
            sends = 0
            for old in previous:
                created = _as_utc(old.created_at)
                if created and now - created < timedelta(hours=1):
                    sends += 1
                if old.consumed_at is None:
                    old.consumed_at = now
            code = security.new_otp()
            db.add(
                OtpRow(
                    user_id=user_id,
                    channel=channel,
                    code_hash=security.hash_otp(user_id, channel, code),
                    expires_at=now + timedelta(seconds=OTP_TTL_SECONDS),
                    sends=sends + 1,
                    created_at=now,
                )
            )
            codes[channel] = code
    return codes


def can_resend_otp(user_id: str, channel: str) -> None:
    """Raise OtpError when the resend cooldown or hourly cap applies."""
    from datetime import timedelta

    now = _now()
    with session_scope() as db:
        created_times = [
            _as_utc(r.created_at) or now
            for r in db.query(OtpRow).filter_by(user_id=user_id, channel=channel).order_by(OtpRow.id.desc()).all()
        ]
    recent = [t for t in created_times if t > now - timedelta(hours=1)]
    if recent and (now - recent[0]).total_seconds() < OTP_RESEND_COOLDOWN_SECONDS:
        raise OtpError("Please wait a minute before requesting another code.", 429)
    if len(recent) >= OTP_MAX_SENDS:
        raise OtpError("Too many codes requested. Try again later.", 429)


def verify_contact_otp(user_id: str, channel: str, code: str) -> dict[str, Any]:
    invalid = OtpError("That code is not valid or has expired.", 400)
    if channel not in ("email", "mobile"):
        raise invalid
    now = _now()
    outcome = "invalid"
    with session_scope() as db:
        user = db.query(PortalUserRow).filter_by(user_id=user_id, role="doctor").first()
        profile = db.query(DoctorProfileRow).filter_by(user_id=user_id).first()
        if user is None or profile is None:
            outcome = "invalid"
        else:
            otp = (
                db.query(OtpRow)
                .filter_by(user_id=user_id, channel=channel, consumed_at=None)
                .order_by(OtpRow.id.desc())
                .first()
            )
            if otp is None or (_as_utc(otp.expires_at) or now) < now:
                outcome = "invalid"
            elif (otp.attempts or 0) >= OTP_MAX_ATTEMPTS:
                outcome = "locked"
            else:
                otp.attempts = (otp.attempts or 0) + 1
                if hmac_equal(otp.code_hash, security.hash_otp(user_id, channel, (code or "").strip())):
                    otp.consumed_at = now
                    outcome = "ok"
                    if channel == "email":
                        profile.email_verified_at = now
                    else:
                        profile.mobile_verified_at = now
                        phone = security.decrypt_text(profile.mobile_enc or "")
                        if phone and has_channel_consent_db(db, user_id, "whatsapp"):
                            user.whatsapp_phone_enc = profile.mobile_enc
                            user.whatsapp_phone_idx = phone_index(phone) or None
                    if profile.email_verified_at and profile.mobile_verified_at and profile.verification_status == registry.PENDING_CONTACT:
                        decision = registry.get_verifier().verify({"user_id": user_id})
                        profile.verification_status = decision.status
                        profile.verification_source = decision.source
                        profile.verification_notes = decision.detail
                    db.flush()
                    result = {
                        "email_verified": bool(profile.email_verified_at),
                        "mobile_verified": bool(profile.mobile_verified_at),
                        "status": profile.verification_status,
                    }
    record_audit_event(
        action="contact_verify",
        user_id=user_id,
        role="doctor",
        entity_type="otp",
        entity_id=channel,
        outcome=outcome,
    )
    if outcome == "locked":
        raise OtpError("Too many attempts. Request a new code.", 429)
    if outcome != "ok":
        raise invalid
    return result


def hmac_equal(a: str, b: str) -> bool:
    return secrets.compare_digest(a or "", b or "")


def has_channel_consent_db(db: Session, user_id: str, channel: str) -> bool:
    ctype = registry.CHANNEL_CONSENT.get(channel)
    row = db.query(ConsentRow).filter_by(user_id=user_id, consent_type=ctype).order_by(ConsentRow.id.desc()).first()
    return bool(row and row.granted)


def contact_state(user_id: str) -> dict[str, Any]:
    with session_scope() as db:
        profile = db.query(DoctorProfileRow).filter_by(user_id=user_id).first()
        if profile is None:
            return {"email_verified": True, "mobile_verified": True}
        return {"email_verified": bool(profile.email_verified_at), "mobile_verified": bool(profile.mobile_verified_at)}


def contact_targets(user_id: str) -> dict[str, str]:
    """Plain email / mobile for OTP delivery (never log these)."""
    with session_scope() as db:
        user = db.query(PortalUserRow).filter_by(user_id=user_id).first()
        profile = db.query(DoctorProfileRow).filter_by(user_id=user_id).first()
        return {
            "email": user.email if user else "",
            "mobile": security.decrypt_text(profile.mobile_enc or "") if profile else "",
        }


# ------------------------------------------------------------------ doctor registry
def _profile_to_dict(profile: Optional[DoctorProfileRow], *, mask: bool = False) -> dict[str, Any]:
    if profile is None:
        return {
            "verification_status": registry.PENDING_REGISTRATION,
            "verification_notes": "",
            "rejection_reason": "",
            "verified_at": None,
        }
    mobile = security.decrypt_text(profile.mobile_enc or "") or (profile.mobile or "")
    reg_no = security.decrypt_text(profile.registration_number_enc or "") or (profile.registration_number or "")
    return {
        "mobile": mask_mobile(mobile) if mask else mobile,
        "registration_number": reg_no,
        "registration_council": profile.registration_council or "",
        "council_id": profile.council_id or "",
        "registration_year": profile.registration_year,
        "qualification": profile.highest_qualification or profile.qualification or "",
        "basic_qualification": profile.basic_qualification or "",
        "highest_qualification": profile.highest_qualification or profile.qualification or "",
        "speciality": profile.speciality or "",
        "institution": profile.institution or "",
        "practice_type": profile.practice_type or "",
        "city": profile.city or "",
        "state": profile.state or "",
        "pincode": profile.pincode or "",
        "nmr_id": profile.nmr_id or "",
        "verification_status": registry.normalize_status(profile.verification_status),
        "verification_notes": profile.verification_notes or "",
        "rejection_reason": profile.rejection_reason or "",
        "verified_at": utc_iso(profile.verified_at),
        "email_verified": bool(profile.email_verified_at),
        "mobile_verified": bool(profile.mobile_verified_at),
    }


def _verifier_name(db: Session, profile: Optional[DoctorProfileRow]) -> str:
    if profile is None or not profile.verified_by:
        return ""
    verifier = db.query(PortalUserRow).filter_by(user_id=profile.verified_by).first()
    return verifier.display_name if verifier else profile.verified_by


def get_doctor_profile(user_id: str) -> Optional[dict[str, Any]]:
    with session_scope() as db:
        profile = db.query(DoctorProfileRow).filter_by(user_id=user_id).first()
        if profile is None:
            return None
        return {
            "user_id": user_id,
            **_profile_to_dict(profile),
            "verified_by_name": _verifier_name(db, profile),
            "certificate": _certificate_meta(profile),
        }


def doctor_verification_status(user_id: str) -> str:
    with session_scope() as db:
        profile = db.query(DoctorProfileRow).filter_by(user_id=user_id).first()
        return registry.normalize_status(profile.verification_status if profile else None)


def _certificate_meta(profile: Optional[DoctorProfileRow]) -> dict[str, Any]:
    present = bool(profile and profile.certificate_path)
    return {
        "present": present,
        "scan_status": (profile.certificate_scan_status if present else "") or "",
        "uploaded_at": utc_iso(profile.certificate_uploaded_at) if present else None,
    }


def _area_names(db: Session, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    rows = db.query(TherapeuticAreaRow).filter(TherapeuticAreaRow.repository_id.in_(ids)).all()
    return {r.repository_id: r.name for r in rows}


def _doctor_entry(
    db: Session,
    user: PortalUserRow,
    *,
    mask: bool = False,
    last_contact: Optional[datetime] = None,
    area_names: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    profile = db.query(DoctorProfileRow).filter_by(user_id=user.user_id).first()
    consents = _latest_consents(db, user.user_id)
    ids = _repo_ids(user)
    names = area_names if area_names is not None else _area_names(db, ids)
    default_id = user.default_repository_id or ""
    return {
        "user_id": user.user_id,
        "display_name": user.display_name,
        "email": user.email,
        "status": user.status or "active",
        "assigned_repository_ids": ids,
        "default_repository_id": default_id,
        "areas": [{"repository_id": r, "name": names.get(r, r), "is_default": r == default_id} for r in ids],
        "assigned_msl_id": user.assigned_msl_id or "",
        "registered_at": utc_iso(user.created_at),
        "last_contact_at": utc_iso(last_contact),
        "consents": {
            "whatsapp_messaging": bool(consents.get("whatsapp_messaging", {}).get("granted")),
            "email_messaging": bool(consents.get("email_messaging", {}).get("granted")),
        },
        **_profile_to_dict(profile, mask=mask),
        "verified_by_name": _verifier_name(db, profile),
        "certificate": _certificate_meta(profile),
    }


def msl_covers_doctor(msl_id: str, doctor: dict[str, Any]) -> bool:
    if doctor.get("assigned_msl_id") == msl_id:
        return True
    mine = set(repositories_for_msl(msl_id))
    return bool(mine.intersection(doctor.get("assigned_repository_ids") or []))


_SORT_KEYS = {
    "display_name": lambda e: (e.get("display_name") or "").lower(),
    "registration_number": lambda e: e.get("registration_number") or "",
    "city": lambda e: (e.get("city") or "").lower(),
    "verification_status": lambda e: e.get("verification_status") or "",
    "registered_at": lambda e: e.get("registered_at") or "",
    "last_contact_at": lambda e: e.get("last_contact_at") or "",
}


def list_doctor_registry(
    *,
    viewer_id: str,
    viewer_role: str,
    status: str = "",
    query: str = "",
    repository_id: str = "",
    city: str = "",
    sort: str = "registered_at",
    order: str = "desc",
    page: int = 1,
    page_size: int = 25,
    paginate: bool = True,
) -> dict[str, Any]:
    mask = viewer_role != "admin"
    with session_scope() as db:
        rows = db.query(PortalUserRow).filter_by(role="doctor").all()
        last = {
            uid: ts
            for uid, ts in db.query(InboundMessageRow.user_id, func.max(InboundMessageRow.received_at))
            .group_by(InboundMessageRow.user_id)
            .all()
        }
        all_ids = list({rid for r in rows for rid in _repo_ids(r)})
        names = _area_names(db, all_ids)
        entries = [_doctor_entry(db, r, mask=mask, last_contact=last.get(r.user_id), area_names=names) for r in rows]
    if viewer_role == "msl":
        entries = [e for e in entries if msl_covers_doctor(viewer_id, e)]
    counts = {"total": len(entries), "verified": 0, "pending": 0, "needs_info": 0, "rejected": 0, "pending_contact": 0}
    for e in entries:
        st = e["verification_status"]
        if st == registry.VERIFIED:
            counts["verified"] += 1
        elif st == registry.REJECTED:
            counts["rejected"] += 1
        elif st == registry.NEEDS_INFO:
            counts["needs_info"] += 1
        elif st == registry.PENDING_CONTACT:
            counts["pending_contact"] += 1
            counts["pending"] += 1
        else:
            counts["pending"] += 1
    needle = (query or "").strip().lower()
    if needle:
        keys = ("display_name", "email", "registration_number", "institution", "city", "speciality")
        entries = [e for e in entries if needle in " ".join(str(e.get(k) or "") for k in keys).lower()]
    if status:
        entries = [e for e in entries if e["verification_status"] == status]
    if repository_id:
        entries = [e for e in entries if repository_id in e["assigned_repository_ids"]]
    if city:
        entries = [e for e in entries if (e.get("city") or "").lower() == city.strip().lower()]
    key = _SORT_KEYS.get(sort, _SORT_KEYS["registered_at"])
    entries.sort(key=key, reverse=(order or "desc").lower() != "asc")
    total = len(entries)
    page = max(1, int(page or 1))
    page_size = min(200, max(1, int(page_size or 25)))
    if paginate:
        entries = entries[(page - 1) * page_size : page * page_size]
    return {"doctors": entries, "counts": counts, "total": total, "page": page, "page_size": page_size}


def get_doctor_entry(user_id: str, *, mask: bool = False) -> Optional[dict[str, Any]]:
    with session_scope() as db:
        row = db.query(PortalUserRow).filter_by(user_id=user_id, role="doctor").first()
        if row is None:
            return None
        last = db.query(func.max(InboundMessageRow.received_at)).filter(InboundMessageRow.user_id == user_id).scalar()
        return _doctor_entry(db, row, mask=mask, last_contact=last)


def get_doctor_detail(user_id: str, *, mask: bool = False) -> Optional[dict[str, Any]]:
    entry = get_doctor_entry(user_id, mask=mask)
    if entry is None:
        return None
    with session_scope() as db:
        convs = (
            db.query(InboundMessageRow.conversation_id)
            .filter(InboundMessageRow.user_id == user_id)
            .distinct()
            .count()
        )
        escalations = db.query(MSLCaseRow).filter_by(doctor_id=user_id).count()
    return {**entry, "conversation_count": convs, "escalation_count": escalations}


def set_doctor_verification(
    doctor_id: str, status: str, reason: str, *, actor_id: str, actor_role: str
) -> dict[str, Any]:
    if status not in registry.ADMIN_DECISIONS:
        raise ValueError("Unknown verification status.")
    reason = (reason or "").strip()[:1000]
    if status in {registry.REJECTED, registry.NEEDS_INFO} and len(reason) < 3:
        raise ValueError("Add a short reason explaining what is missing or why it was rejected.")
    with session_scope() as db:
        doctor = db.query(PortalUserRow).filter_by(user_id=doctor_id, role="doctor").first()
        if doctor is None:
            raise LookupError("Doctor not found.")
        profile = db.query(DoctorProfileRow).filter_by(user_id=doctor_id).first()
        if profile is None:
            profile = DoctorProfileRow(user_id=doctor_id, email_verified_at=_now(), mobile_verified_at=_now())
            db.add(profile)
        if status == registry.VERIFIED and not (profile.email_verified_at and profile.mobile_verified_at):
            raise ValueError("Email and mobile verification must be completed before the registration can be verified.")
        before = {"status": registry.normalize_status(profile.verification_status), "reason": profile.rejection_reason or ""}
        profile.verification_status = status
        profile.verification_notes = reason
        profile.rejection_reason = reason if status in {registry.REJECTED, registry.NEEDS_INFO} else ""
        profile.verification_source = "manual"
        profile.verified_by = actor_id
        profile.verified_at = _now() if status == registry.VERIFIED else None
        db.flush()
        entry = _doctor_entry(db, doctor)
    record_audit_event(
        action="doctor_verification",
        user_id=actor_id,
        role=actor_role,
        entity_type="doctor",
        entity_id=doctor_id,
        outcome=status,
        before=before,
        after={"status": status, "reason": reason},
        reason=reason,
    )
    titles = {
        registry.VERIFIED: "Your registration is verified",
        registry.REJECTED: "Your registration could not be verified",
        registry.NEEDS_INFO: "More information needed for verification",
        registry.PENDING_REGISTRATION: "Your registration is under review",
    }
    add_notification(user_id=doctor_id, kind="verification", title=titles[status], body=reason)
    return entry


def set_doctor_active(doctor_id: str, active: bool, reason: str, *, actor_id: str, actor_role: str) -> dict[str, Any]:
    reason = (reason or "").strip()[:1000]
    if not active and len(reason) < 3:
        raise ValueError("A reason is required to deactivate a doctor.")
    with session_scope() as db:
        doctor = db.query(PortalUserRow).filter_by(user_id=doctor_id, role="doctor").first()
        if doctor is None:
            raise LookupError("Doctor not found.")
        before = {"status": doctor.status or "active"}
        doctor.status = "active" if active else "inactive"
        profile = db.query(DoctorProfileRow).filter_by(user_id=doctor_id).first()
        if profile is not None:
            profile.deactivation_reason = "" if active else reason
        db.flush()
        entry = _doctor_entry(db, doctor)
    if not active:
        end_all_sessions(doctor_id)
    record_audit_event(
        action="doctor_reactivate" if active else "doctor_deactivate",
        user_id=actor_id,
        role=actor_role,
        entity_type="doctor",
        entity_id=doctor_id,
        before=before,
        after={"status": "active" if active else "inactive"},
        reason=reason,
    )
    return entry


def set_certificate(user_id: str, path: str, scan_status: str) -> None:
    with session_scope() as db:
        profile = db.query(DoctorProfileRow).filter_by(user_id=user_id).first()
        if profile is None:
            raise LookupError("Doctor profile not found.")
        profile.certificate_path = path
        profile.certificate_scan_status = scan_status
        profile.certificate_uploaded_at = _now()


def get_certificate_path(user_id: str) -> tuple[str, str]:
    with session_scope() as db:
        profile = db.query(DoctorProfileRow).filter_by(user_id=user_id).first()
        return ((profile.certificate_path or ""), (profile.certificate_scan_status or "")) if profile else ("", "")


# ------------------------------------------------------------------ consents
def get_consents(user_id: str) -> dict[str, dict[str, Any]]:
    with session_scope() as db:
        state = _latest_consents(db, user_id)
    return {t: state.get(t, {"granted": False, "version": "", "updated_at": None}) for t in registry.CONSENT_TYPES}


def has_channel_consent(user_id: str, channel: str) -> bool:
    ctype = registry.CHANNEL_CONSENT.get(channel)
    if not ctype:
        return True
    with session_scope() as db:
        row = (
            db.query(ConsentRow)
            .filter_by(user_id=user_id, consent_type=ctype)
            .order_by(ConsentRow.id.desc())
            .first()
        )
        return bool(row and row.granted)


def set_channel_consents(
    user_id: str,
    changes: dict[str, Optional[bool]],
    *,
    ip_address: str = "",
    user_agent: str = "",
    source: str = "settings",
) -> dict[str, dict[str, Any]]:
    """Grant or withdraw WhatsApp / email consent. Each change is a new ledger row; nothing is overwritten."""
    items = {i["type"]: i for i in registry.CONSENT_ITEMS}
    current = get_consents(user_id)
    for ctype, value in changes.items():
        if value is None:
            continue
        if ctype not in ("whatsapp_messaging", "email_messaging"):
            raise ValueError("Only WhatsApp and email consent can be changed here.")
        if bool(value) == bool(current[ctype]["granted"]):
            continue
        with session_scope() as db:
            db.add(
                ConsentRow(
                    consent_id=new_id("cns"),
                    user_id=user_id,
                    consent_type=ctype,
                    channel=_consent_channel(ctype),
                    granted=1 if value else 0,
                    version=registry.CONSENT_VERSION,
                    text_sha256=hashlib.sha256(items[ctype]["text"].encode("utf-8")).hexdigest(),
                    source=source,
                    ip_address=ip_address,
                    user_agent=(user_agent or "")[:255],
                )
            )
            if ctype == "whatsapp_messaging":
                doctor = db.query(PortalUserRow).filter_by(user_id=user_id).first()
                profile = db.query(DoctorProfileRow).filter_by(user_id=user_id).first()
                if doctor is not None:
                    if value and profile is not None and profile.mobile_verified_at and profile.mobile_enc:
                        doctor.whatsapp_phone_enc = profile.mobile_enc
                        doctor.whatsapp_phone_idx = phone_index(security.decrypt_text(profile.mobile_enc)) or None
                    elif not value:
                        # keep the number for identifying inbound STOP / history, but stop messaging via consent_block
                        pass
        record_audit_event(
            action="consent_granted" if value else "consent_withdrawn",
            user_id=user_id,
            role="doctor",
            entity_type="consent",
            entity_id=ctype,
            detail={"version": registry.CONSENT_VERSION, "source": source},
            ip_address=ip_address,
            user_agent=user_agent,
        )
    return get_consents(user_id)


# ------------------------------------------------------------------ MFA (TOTP)
def mfa_enabled(user_id: str) -> bool:
    with session_scope() as db:
        row = db.query(PortalUserRow).filter_by(user_id=user_id).first()
        return bool(row and row.mfa_enabled_at and row.mfa_secret_enc)


def mfa_begin_setup(user_id: str) -> dict[str, str]:
    with session_scope() as db:
        row = db.query(PortalUserRow).filter_by(user_id=user_id).first()
        if row is None:
            raise LookupError("User not found.")
        if row.mfa_enabled_at:
            raise ValueError("Two-factor authentication is already enabled.")
        secret = security.new_totp_secret()
        row.mfa_secret_enc = security.encrypt_text(secret)
        email = row.email
    return {"secret": secret, "otpauth_uri": security.totp_uri(secret, email)}


def mfa_enable(user_id: str, code: str) -> list[str]:
    with session_scope() as db:
        row = db.query(PortalUserRow).filter_by(user_id=user_id).first()
        if row is None or not row.mfa_secret_enc:
            raise ValueError("Start two-factor setup first.")
        if row.mfa_enabled_at:
            raise ValueError("Two-factor authentication is already enabled.")
        if not security.verify_totp(security.decrypt_text(row.mfa_secret_enc), code):
            raise ValueError("That code is not valid.")
        recovery = security.new_recovery_codes()
        row.mfa_enabled_at = _now()
        row.mfa_recovery_json = json.dumps([security.hash_recovery_code(c) for c in recovery])
        role = row.role
    record_audit_event(action="mfa_enabled", user_id=user_id, role=role, entity_type="user", entity_id=user_id)
    return recovery


def mfa_check(user_id: str, code: str) -> bool:
    """Verify a TOTP or one-time recovery code, with a per-user failure lockout."""
    settings = get_dmsl_settings()
    key = f"mfa:{user_id}"
    with session_scope() as db:
        if _recent_failed_logins(db, key) >= settings.max_failed_logins:
            raise AccountLocked(f"Too many failed attempts. Try again in {settings.lockout_minutes} minutes.")
        row = db.query(PortalUserRow).filter_by(user_id=user_id).first()
        ok = False
        if row is not None and row.mfa_enabled_at and row.mfa_secret_enc:
            if security.verify_totp(security.decrypt_text(row.mfa_secret_enc), code):
                ok = True
            else:
                digest = security.hash_recovery_code(code or "")
                hashes = json.loads(row.mfa_recovery_json or "[]")
                if digest in hashes:
                    hashes.remove(digest)
                    row.mfa_recovery_json = json.dumps(hashes)
                    ok = True
        db.add(LoginAttemptRow(email=key, success=1 if ok else 0))
    if not ok:
        record_audit_event(action="mfa_failed", user_id=user_id, outcome="denied", entity_type="user", entity_id=user_id)
    return ok


def mfa_disable(user_id: str) -> None:
    with session_scope() as db:
        row = db.query(PortalUserRow).filter_by(user_id=user_id).first()
        if row is None:
            raise LookupError("User not found.")
        row.mfa_secret_enc = ""
        row.mfa_enabled_at = None
        row.mfa_recovery_json = "[]"
        role = row.role
    record_audit_event(action="mfa_disabled", user_id=user_id, role=role, entity_type="user", entity_id=user_id)


# ------------------------------------------------------------------ compliance notice tracking
def mark_notice_seen(user_id: str, channel: str, version: str) -> bool:
    """True the first time this doctor sees this notice version on this channel."""
    if not user_id:
        return False
    with session_scope() as db:
        if db.query(NoticeSeenRow).filter_by(user_id=user_id, channel=channel, version=version).first():
            return False
        db.add(NoticeSeenRow(user_id=user_id, channel=channel, version=version))
        return True


def ae_flag_exists(conversation_id: str) -> bool:
    if not conversation_id:
        return False
    with session_scope() as db:
        return (
            db.query(AuditEventRow)
            .filter_by(action="safety_ae_keyword_flag", conversation_id=conversation_id)
            .first()
            is not None
        )


REQUEST_META: ContextVar[dict[str, str]] = ContextVar("dmsl_request_meta", default={})
_audit_lock = threading.Lock()


def create_login_session(user_id: str, role: str, *, previous_session_id: str = "") -> str:
    """Issue a fresh session id. Any session the request already carried is ended (rotation on login)."""
    if previous_session_id:
        end_login_session(previous_session_id, user_id="", audit=False)
    session_id = new_id("sess")
    now = _now()
    with session_scope() as db:
        db.add(LoginSessionRow(session_id=session_id, user_id=user_id, role=role, last_seen=now))
    record_audit_event(
        action="login",
        user_id=user_id,
        role=role,
        session_id=session_id,
        entity_type="session",
        entity_id=session_id,
    )
    return session_id


def get_login_session(session_id: str) -> Optional[dict[str, Any]]:
    if not session_id:
        return None
    from datetime import timedelta

    now = _now()
    idle = timedelta(minutes=get_dmsl_settings().session_idle_minutes)
    with session_scope() as db:
        row = db.query(LoginSessionRow).filter_by(session_id=session_id).first()
        if not row:
            return None
        last_seen = _as_utc(row.last_seen) or _as_utc(row.created_at) or now
        absolute_expired = _session_expired(row.created_at)
        idle_expired = (now - last_seen) > idle
        if not row.ended_at and not absolute_expired and not idle_expired and (now - last_seen).total_seconds() > 20:
            row.last_seen = now
        return {
            "session_id": row.session_id,
            "user_id": row.user_id,
            "role": row.role,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "ended_at": row.ended_at.isoformat() if row.ended_at else None,
            "expired": absolute_expired or idle_expired,
        }


def _session_expired(created_at: Optional[datetime]) -> bool:
    from datetime import timedelta

    created = _as_utc(created_at)
    if created is None:
        return False
    ttl = timedelta(hours=get_dmsl_settings().session_ttl_hours)
    return datetime.now(timezone.utc) - created > ttl


def end_login_session(session_id: str, user_id: str = "", *, audit: bool = True) -> None:
    with session_scope() as db:
        row = db.query(LoginSessionRow).filter_by(session_id=session_id).first()
        if not row or row.ended_at:
            return
        row.ended_at = func.now()
        uid = user_id or row.user_id
        role = row.role or ""
    if audit:
        record_audit_event(
            action="logout",
            user_id=uid,
            role=role,
            session_id=session_id,
            entity_type="session",
            entity_id=session_id,
        )


def end_all_sessions(user_id: str, *, except_session: str = "") -> int:
    count = 0
    with session_scope() as db:
        for row in db.query(LoginSessionRow).filter_by(user_id=user_id, ended_at=None).all():
            if row.session_id != except_session:
                row.ended_at = func.now()
                count += 1
    if count:
        record_audit_event(action="logout_all", user_id=user_id, entity_type="user", entity_id=user_id, detail={"sessions": count})
    return count


def _naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _audit_material(fields: dict[str, Any], created: datetime, prev_hash: str) -> str:
    body = json.dumps([fields[k] for k in _AUDIT_FIELDS] + [created.isoformat(), prev_hash], separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


_AUDIT_FIELDS = (
    "event_id", "user_id", "role", "session_id", "repository_id", "conversation_id", "case_id", "action",
    "entity_type", "entity_id", "outcome", "detail_json", "ip_address", "user_agent", "before_json", "after_json", "reason",
)


def record_audit_event(
    *,
    action: str,
    user_id: str = "",
    role: str = "",
    session_id: str = "",
    repository_id: str = "",
    conversation_id: str = "",
    case_id: str = "",
    entity_type: str = "",
    entity_id: str = "",
    outcome: str = "ok",
    detail: Optional[dict[str, Any]] = None,
    ip_address: str = "",
    user_agent: str = "",
    before: Optional[dict[str, Any]] = None,
    after: Optional[dict[str, Any]] = None,
    reason: str = "",
) -> str:
    """Append a hash-chained audit entry (who, what, when UTC, from where, before / after, reason)."""
    meta = REQUEST_META.get() or {}
    fields = {
        "event_id": new_id("aud"),
        "user_id": user_id or "",
        "role": role or "",
        "session_id": session_id or "",
        "repository_id": repository_id or "",
        "conversation_id": conversation_id or "",
        "case_id": case_id or "",
        "action": action,
        "entity_type": entity_type or "",
        "entity_id": entity_id or "",
        "outcome": outcome,
        "detail_json": json.dumps(detail or {}, sort_keys=True, default=str),
        "ip_address": (ip_address or meta.get("ip", ""))[:64],
        "user_agent": (user_agent or meta.get("ua", ""))[:255],
        "before_json": json.dumps(before, sort_keys=True, default=str) if before is not None else "",
        "after_json": json.dumps(after, sort_keys=True, default=str) if after is not None else "",
        "reason": (reason or "")[:2000],
    }
    with _audit_lock:
        with session_scope() as db:
            last = (
                db.query(AuditEventRow.entry_hash)
                .filter(AuditEventRow.entry_hash != "")
                .order_by(AuditEventRow.id.desc())
                .first()
            )
            prev_hash = last[0] if last and last[0] else ""
            created = datetime.now(timezone.utc).replace(tzinfo=None)
            entry_hash = _audit_material(fields, created, prev_hash)
            db.add(AuditEventRow(**fields, prev_hash=prev_hash, entry_hash=entry_hash, created_at=created))
    return fields["event_id"]


def verify_audit_chain() -> dict[str, Any]:
    """Recompute the hash chain. Rows written before chaining existed (no hash) are skipped."""
    checked = 0
    prev = ""
    with session_scope() as db:
        for row in db.query(AuditEventRow).filter(AuditEventRow.entry_hash != "").order_by(AuditEventRow.id.asc()).yield_per(500):
            fields = {k: getattr(row, k) or "" for k in _AUDIT_FIELDS}
            created = _naive_utc(row.created_at)
            if (row.prev_hash or "") != prev or _audit_material(fields, created, row.prev_hash or "") != row.entry_hash:
                return {"ok": False, "checked": checked, "first_bad_id": row.id}
            prev = row.entry_hash
            checked += 1
    return {"ok": True, "checked": checked, "first_bad_id": None}


def list_audit_events(
    *,
    limit: int = 100,
    offset: int = 0,
    action: Optional[str] = None,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> dict[str, Any]:
    with session_scope() as db:
        query = db.query(AuditEventRow).order_by(AuditEventRow.id.desc())
        if action:
            query = query.filter(AuditEventRow.action == action)
        if session_id:
            query = query.filter(AuditEventRow.session_id == session_id)
        if user_id:
            query = query.filter(AuditEventRow.user_id == user_id)
        total = query.count()
        rows = query.offset(offset).limit(limit).all()
        events = []
        for row in rows:
            detail: dict[str, Any] = {}
            try:
                detail = json.loads(row.detail_json or "{}")
            except Exception:
                pass
            events.append(
                {
                    "event_id": row.event_id,
                    "created_at": utc_iso(row.created_at),
                    "user_id": row.user_id,
                    "role": row.role,
                    "session_id": row.session_id,
                    "repository_id": row.repository_id,
                    "conversation_id": row.conversation_id,
                    "case_id": row.case_id,
                    "action": row.action,
                    "entity_type": row.entity_type,
                    "entity_id": row.entity_id,
                    "outcome": row.outcome,
                    "detail": detail,
                    "ip_address": row.ip_address or "",
                    "user_agent": row.user_agent or "",
                    "before": json.loads(row.before_json) if row.before_json else None,
                    "after": json.loads(row.after_json) if row.after_json else None,
                    "reason": row.reason or "",
                    "entry_hash": row.entry_hash or "",
                }
            )
        return {"events": events, "total": total, "limit": limit, "offset": offset}


def _keywords(row: TherapeuticAreaRow) -> list[str]:
    try:
        data = json.loads(getattr(row, "scope_keywords", None) or "[]")
    except Exception:
        return []
    return [str(x).strip().lower() for x in data if str(x).strip()]


def scope_keywords_for_repositories(repository_ids: list[str]) -> list[str]:
    """Scope terms for the therapeutic areas a doctor can use (used to judge in-scope questions)."""
    ids = [r for r in repository_ids if r]
    if not ids:
        return []
    with session_scope() as db:
        rows = db.query(TherapeuticAreaRow).filter(TherapeuticAreaRow.repository_id.in_(ids)).all()
        words: list[str] = []
        for row in rows:
            words.extend(_keywords(row))
    return list(dict.fromkeys(words))


def _ta_to_dict(row: TherapeuticAreaRow, msl_ids: list[str], folders: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "repository_id": row.repository_id,
        "name": row.name,
        "owner_msl_id": row.owner_msl_id or "",
        "default_flag": bool(row.default_flag),
        "active": bool(getattr(row, "active_flag", 1)),
        "description": getattr(row, "description", None) or "",
        "scope_keywords": _keywords(row),
        "slug": getattr(row, "slug", None) or "",
        "sort_order": getattr(row, "sort_order", 0) or 0,
        "enabled": bool(getattr(row, "active_flag", 1)),
        "coming_soon": row.repository_id.startswith("pending:"),
        "msl_ids": msl_ids,
        "folders": folders,
    }


def list_therapeutic_areas() -> list[dict[str, Any]]:
    """Portal therapeutic areas only (explicit admin-created rows), not every DMS repository."""
    with session_scope() as db:
        rows = db.query(TherapeuticAreaRow).order_by(TherapeuticAreaRow.sort_order, TherapeuticAreaRow.name).all()
        members: dict[str, list[str]] = {}
        for member in db.query(TherapeuticAreaMslRow).all():
            members.setdefault(member.repository_id, []).append(member.msl_user_id)
        folders: dict[str, list[dict[str, str]]] = {}
        for folder in db.query(TherapeuticAreaFolderRow).all():
            folders.setdefault(folder.repository_id, []).append(
                {
                    "folder_id": folder.folder_id,
                    "name": folder.name,
                    "parent_folder_id": folder.parent_folder_id or "",
                    "sort_order": folder.sort_order or 0,
                }
            )
        return [_ta_to_dict(row, members.get(row.repository_id, []), folders.get(row.repository_id, [])) for row in rows]


def ensure_area_slugs() -> None:
    with session_scope() as db:
        for row in db.query(TherapeuticAreaRow).all():
            if not row.slug:
                row.slug = slugify(row.name)


def seed_default_areas(*, actor_id: str = "", role: str = "admin") -> dict[str, Any]:
    """Create the standard eyecare areas as local, disabled placeholders. Idempotent; no IKM calls."""
    created: list[dict[str, Any]] = []
    existing: list[str] = []
    with session_scope() as db:
        have = {(r.slug or slugify(r.name)) for r in db.query(TherapeuticAreaRow).all()}
        have_names = {r.name.strip().lower() for r in db.query(TherapeuticAreaRow).all()}
        base = db.query(func.max(TherapeuticAreaRow.sort_order)).scalar() or 0
        for order, item in enumerate(DEFAULT_THERAPEUTIC_AREAS, start=1):
            slug = slugify(item["name"])
            if slug in have or item["name"].lower() in have_names:
                existing.append(item["name"])
                continue
            row = TherapeuticAreaRow(
                repository_id=f"pending:{slug}",
                name=item["name"],
                slug=slug,
                description=item["description"],
                active_flag=0,
                sort_order=base + order,
                scope_keywords=json.dumps([k.lower() for k in item["keywords"]]),
            )
            db.add(row)
            db.flush()
            created.append(_ta_to_dict(row, [], []))
    record_audit_event(
        action="ta_seed_defaults",
        user_id=actor_id,
        role=role,
        entity_type="therapeutic_area",
        after={"created": [a["name"] for a in created]},
        detail={"existing": existing},
    )
    return {"created": created, "existing": existing, "failed": []}


def get_area(key: str) -> Optional[dict[str, Any]]:
    with session_scope() as db:
        row = db.query(TherapeuticAreaRow).filter(
            or_(TherapeuticAreaRow.repository_id == key, TherapeuticAreaRow.slug == key)
        ).first()
        if row is None:
            return None
        members = [m.msl_user_id for m in db.query(TherapeuticAreaMslRow).filter_by(repository_id=row.repository_id).all()]
        return _ta_to_dict(row, members, [])


def update_area_settings(
    key: str,
    *,
    actor_id: str,
    role: str = "admin",
    name: Optional[str] = None,
    description: Optional[str] = None,
    enabled: Optional[bool] = None,
    sort_order: Optional[int] = None,
    scope_keywords: Optional[list[str]] = None,
    repository_id: Optional[str] = None,
) -> dict[str, Any]:
    """Admin edit of a therapeutic area. Enabling needs a real IKM repository id (linked or supplied)."""
    with session_scope() as db:
        row = db.query(TherapeuticAreaRow).filter(
            or_(TherapeuticAreaRow.repository_id == key, TherapeuticAreaRow.slug == key)
        ).first()
        if row is None:
            raise LookupError("Therapeutic area not found.")
        old_id = row.repository_id
        before = {
            "name": row.name,
            "description": row.description or "",
            "enabled": bool(row.active_flag),
            "sort_order": row.sort_order or 0,
            "repository_id": row.repository_id,
            "scope_keywords": _keywords(row),
        }
        if name is not None:
            clean = name.strip()
            if len(clean) < 3 or len(clean) > 80:
                raise ValueError("Name must be 3-80 characters.")
            row.name = clean
        if description is not None:
            row.description = description.strip()
        if sort_order is not None:
            row.sort_order = int(sort_order)
        if scope_keywords is not None:
            cleaned = list(dict.fromkeys(str(k).strip().lower() for k in scope_keywords if str(k).strip()))
            row.scope_keywords = json.dumps(cleaned[:80])
        if repository_id:
            repo = repository_id.strip()
            clash = db.query(TherapeuticAreaRow).filter(TherapeuticAreaRow.repository_id == repo, TherapeuticAreaRow.id != row.id).first()
            if clash is not None:
                raise ValueError("That repository is already linked to another therapeutic area.")
            row.repository_id = repo
        if enabled is not None:
            if enabled and row.repository_id.startswith("pending:"):
                raise ValueError("Link an IKM repository before enabling this area.")
            row.active_flag = 1 if enabled else 0
        db.flush()
        if row.repository_id != old_id:
            db.query(TherapeuticAreaMslRow).filter_by(repository_id=old_id).update({"repository_id": row.repository_id})
        members = [m.msl_user_id for m in db.query(TherapeuticAreaMslRow).filter_by(repository_id=row.repository_id).all()]
        after = {
            "name": row.name,
            "description": row.description or "",
            "enabled": bool(row.active_flag),
            "sort_order": row.sort_order or 0,
            "repository_id": row.repository_id,
            "scope_keywords": _keywords(row),
        }
        payload = _ta_to_dict(row, members, [])
    record_audit_event(
        action="ta_update",
        user_id=actor_id,
        role=role,
        repository_id=payload["repository_id"],
        entity_type="therapeutic_area",
        entity_id=payload["repository_id"],
        before=before,
        after=after,
    )
    return payload


def is_therapeutic_area_active(repository_id: str) -> bool:
    if not (repository_id or "").strip():
        return True
    with session_scope() as db:
        row = db.query(TherapeuticAreaRow).filter_by(repository_id=repository_id.strip()).first()
        if row is None:
            return True
        return bool(getattr(row, "active_flag", 1))


def set_therapeutic_area_active(repository_id: str, active: bool, *, actor_id: str = "", role: str = "admin") -> dict[str, Any]:
    with session_scope() as db:
        row = db.query(TherapeuticAreaRow).filter_by(repository_id=repository_id).first()
        if row is None:
            raise ValueError("Therapeutic area not found.")
        row.active_flag = 1 if active else 0
        db.flush()
        members = [m.msl_user_id for m in db.query(TherapeuticAreaMslRow).filter_by(repository_id=repository_id).all()]
        folders = [
            {
                "folder_id": f.folder_id,
                "name": f.name,
                "parent_folder_id": f.parent_folder_id or "",
                "sort_order": f.sort_order or 0,
            }
            for f in db.query(TherapeuticAreaFolderRow).filter_by(repository_id=repository_id).all()
        ]
        payload = _ta_to_dict(row, members, folders)
    record_audit_event(
        action="ta_activate" if active else "ta_deactivate",
        user_id=actor_id,
        role=role,
        repository_id=repository_id,
        entity_type="therapeutic_area",
        entity_id=repository_id,
        outcome="active" if active else "inactive",
    )
    return payload


def folder_document_counts(repository_id: str) -> dict[str, int]:
    mapping = document_folder_map(repository_id)
    counts: dict[str, int] = {}
    for folder_id in mapping.values():
        if folder_id:
            counts[folder_id] = counts.get(folder_id, 0) + 1
    return counts


def folder_belongs_to_repository(folder_id: str, repository_id: str) -> bool:
    if not folder_id:
        return True
    with session_scope() as db:
        row = db.query(TherapeuticAreaFolderRow).filter_by(folder_id=folder_id).first()
        return bool(row and row.repository_id == repository_id)


def folder_usage(folder_id: str) -> dict[str, int]:
    with session_scope() as db:
        child_count = db.query(TherapeuticAreaFolderRow).filter_by(parent_folder_id=folder_id).count()
        doc_count = db.query(DocumentFolderRow).filter_by(folder_id=folder_id).count()
        return {"child_folders": child_count, "documents": doc_count}


def upsert_therapeutic_area(
    *,
    repository_id: str,
    name: str,
    owner_msl_id: str = "",
    default_flag: bool = False,
    description: str = "",
    scope_keywords: Optional[list[str]] = None,
) -> dict[str, Any]:
    with session_scope() as db:
        if default_flag:
            for row in db.query(TherapeuticAreaRow).all():
                row.default_flag = 0
        row = db.query(TherapeuticAreaRow).filter_by(repository_id=repository_id).first()
        if row is None:
            row = TherapeuticAreaRow(repository_id=repository_id, name=name.strip() or repository_id)
            db.add(row)
        row.name = name.strip() or row.name
        if description is not None:
            row.description = (description or "").strip()
        if not row.slug:
            row.slug = slugify(row.name)
        if scope_keywords is not None:
            cleaned_kw = list(dict.fromkeys(str(k).strip().lower() for k in scope_keywords if str(k).strip()))
            row.scope_keywords = json.dumps(cleaned_kw[:80])
        if owner_msl_id:
            row.owner_msl_id = owner_msl_id
            exists = (
                db.query(TherapeuticAreaMslRow)
                .filter_by(repository_id=repository_id, msl_user_id=owner_msl_id)
                .first()
            )
            if not exists:
                db.add(TherapeuticAreaMslRow(repository_id=repository_id, msl_user_id=owner_msl_id))
        if default_flag:
            row.default_flag = 1
        db.flush()
        members = [m.msl_user_id for m in db.query(TherapeuticAreaMslRow).filter_by(repository_id=repository_id).all()]
        folders = [
            {"folder_id": f.folder_id, "name": f.name}
            for f in db.query(TherapeuticAreaFolderRow).filter_by(repository_id=repository_id).all()
        ]
        return _ta_to_dict(row, members, folders)


def set_ta_members(repository_id: str, msl_ids: list[str], owner_msl_id: str = "") -> dict[str, Any]:
    cleaned = [str(x).strip() for x in msl_ids if str(x).strip()]
    with session_scope() as db:
        row = db.query(TherapeuticAreaRow).filter_by(repository_id=repository_id).first()
        if row is None:
            row = TherapeuticAreaRow(repository_id=repository_id, name=repository_id)
            db.add(row)
        if owner_msl_id:
            row.owner_msl_id = owner_msl_id
            if owner_msl_id not in cleaned:
                cleaned.append(owner_msl_id)
        db.query(TherapeuticAreaMslRow).filter_by(repository_id=repository_id).delete()
        for msl_id in cleaned:
            db.add(TherapeuticAreaMslRow(repository_id=repository_id, msl_user_id=msl_id))
        db.flush()
        folders = [
            {"folder_id": f.folder_id, "name": f.name}
            for f in db.query(TherapeuticAreaFolderRow).filter_by(repository_id=repository_id).all()
        ]
        return _ta_to_dict(row, cleaned, folders)


def add_ta_folder(repository_id: str, name: str, parent_folder_id: str = "") -> dict[str, str]:
    label = name.strip()
    if not label:
        raise ValueError("Folder name is required.")
    folder_id = new_id("fld")
    with session_scope() as db:
        db.add(
            TherapeuticAreaFolderRow(
                folder_id=folder_id,
                repository_id=repository_id,
                name=label,
                parent_folder_id=parent_folder_id or "",
            )
        )
    record_audit_event(
        action="folder_create",
        repository_id=repository_id,
        entity_type="folder",
        entity_id=folder_id,
        detail={"name": label, "parent_folder_id": parent_folder_id or ""},
    )
    return {"folder_id": folder_id, "name": label, "repository_id": repository_id, "parent_folder_id": parent_folder_id or ""}


def rename_ta_folder(folder_id: str, name: str) -> Optional[dict[str, str]]:
    label = name.strip()
    if not label:
        raise ValueError("Folder name is required.")
    with session_scope() as db:
        row = db.query(TherapeuticAreaFolderRow).filter_by(folder_id=folder_id).first()
        if not row:
            return None
        row.name = label
        repo_id = row.repository_id
        db.flush()
    record_audit_event(
        action="folder_rename",
        repository_id=repo_id,
        entity_type="folder",
        entity_id=folder_id,
        detail={"name": label},
    )
    return {"folder_id": folder_id, "name": label, "repository_id": repo_id}


def delete_ta_folder(folder_id: str) -> bool:
    with session_scope() as db:
        row = db.query(TherapeuticAreaFolderRow).filter_by(folder_id=folder_id).first()
        if not row:
            return False
        child = db.query(TherapeuticAreaFolderRow).filter_by(parent_folder_id=folder_id).first()
        if child:
            raise ValueError("Remove or move subfolders first.")
        linked = db.query(DocumentFolderRow).filter_by(folder_id=folder_id).first()
        if linked:
            raise ValueError("Folder contains documents.")
        repo_id = row.repository_id
        db.delete(row)
    record_audit_event(action="folder_delete", repository_id=repo_id, entity_type="folder", entity_id=folder_id)
    return True


def delete_therapeutic_area(repository_id: str, *, actor_id: str = "", role: str = "admin") -> bool:
    repo_id = (repository_id or "").strip()
    if not repo_id:
        raise ValueError("Therapeutic area id is required.")
    with session_scope() as db:
        row = db.query(TherapeuticAreaRow).filter_by(repository_id=repo_id).first()
        if row is None:
            return False
        name = row.name
        db.query(TherapeuticAreaMslRow).filter_by(repository_id=repo_id).delete()
        db.query(TherapeuticAreaFolderRow).filter_by(repository_id=repo_id).delete()
        db.query(DocumentFolderRow).filter_by(repository_id=repo_id).delete()
        for doctor in db.query(PortalUserRow).filter_by(role="doctor").all():
            ids = _repo_ids(doctor)
            if repo_id not in ids:
                continue
            cleaned = [item for item in ids if item != repo_id]
            doctor.assigned_repository_ids = json.dumps(cleaned)
            if (doctor.default_repository_id or "") == repo_id:
                doctor.default_repository_id = cleaned[0] if cleaned else ""
        db.delete(row)
    record_audit_event(
        action="ta_delete",
        user_id=actor_id,
        role=role,
        repository_id=repo_id,
        entity_type="therapeutic_area",
        entity_id=repo_id,
        detail={"name": name},
    )
    return True


def set_document_folder(document_id: str, repository_id: str, folder_id: str = "") -> None:
    with session_scope() as db:
        row = db.query(DocumentFolderRow).filter_by(document_id=document_id).first()
        if row is None:
            row = DocumentFolderRow(document_id=document_id, repository_id=repository_id, folder_id=folder_id or "")
            db.add(row)
        else:
            row.repository_id = repository_id
            row.folder_id = folder_id or ""


def document_folder_map(repository_id: str) -> dict[str, str]:
    with session_scope() as db:
        rows = db.query(DocumentFolderRow).filter_by(repository_id=repository_id).all()
        return {r.document_id: r.folder_id for r in rows}


def msls_for_repository(repository_id: str) -> list[str]:
    if not repository_id:
        return []
    with session_scope() as db:
        ids = [m.msl_user_id for m in db.query(TherapeuticAreaMslRow).filter_by(repository_id=repository_id).all()]
        row = db.query(TherapeuticAreaRow).filter_by(repository_id=repository_id).first()
        if row and row.owner_msl_id and row.owner_msl_id not in ids:
            ids.insert(0, row.owner_msl_id)
        return ids


APPROVAL_STATUSES = ("APPROVED", "IN_REVIEW", "DRAFT", "SUPERSEDED", "WITHDRAWN", "EXPIRED")


def _approval_to_dict(row: DocumentApprovalRow) -> dict[str, Any]:
    return {
        "repository_id": row.repository_id,
        "document_id": row.document_id,
        "document_title": row.document_title or "",
        "approval_status": row.approval_status,
        "version": row.version or "",
        "effective_date": row.effective_date or "",
        "expiry_date": row.expiry_date or "",
        "superseded_by": row.superseded_by or "",
        "approved_by": row.approved_by or "",
        "approved_at": utc_iso(row.approved_at),
        "updated_by": row.updated_by or "",
        "updated_at": utc_iso(row.updated_at),
        "reason": row.reason or "",
    }


def get_document_approvals(repository_id: str) -> dict[str, dict[str, Any]]:
    """document_id -> approval record for one repository (one query)."""
    with session_scope() as db:
        rows = db.query(DocumentApprovalRow).filter_by(repository_id=repository_id).all()
        return {r.document_id: _approval_to_dict(r) for r in rows}


def set_document_approval(
    repository_id: str,
    document_id: str,
    *,
    approval_status: str,
    version: str = "",
    effective_date: str = "",
    expiry_date: str = "",
    superseded_by: str = "",
    document_title: str = "",
    reason: str = "",
    actor_id: str = "",
    actor_role: str = "",
) -> dict[str, Any]:
    """Create or update the approval record. Validation lives here so every caller gets it."""
    from digital_msl import grounding

    status = (approval_status or "").strip().upper()
    if status not in APPROVAL_STATUSES:
        raise ValueError("approval_status must be one of " + ", ".join(APPROVAL_STATUSES) + ".")
    version, reason = (version or "").strip(), (reason or "").strip()
    effective_date, expiry_date = (effective_date or "").strip(), (expiry_date or "").strip()
    if status == "APPROVED":
        if not version:
            raise ValueError("An approved document needs a version.")
        if grounding.parse_date(effective_date) is None:
            raise ValueError("An approved document needs an effective date (YYYY-MM-DD).")
    if expiry_date and grounding.parse_date(expiry_date) is None:
        raise ValueError("Expiry date must be a date (YYYY-MM-DD).")
    if status == "SUPERSEDED" and not (superseded_by or "").strip():
        raise ValueError("A superseded document needs the document and version that replaces it.")
    with session_scope() as db:
        row = db.query(DocumentApprovalRow).filter_by(repository_id=repository_id, document_id=document_id).first()
        before = _approval_to_dict(row) if row is not None else None
        if row is not None and row.approval_status == "APPROVED" and status != "APPROVED" and len(reason) < 3:
            raise ValueError("Say why an approved document is no longer approved (reason required).")
        if row is None:
            row = DocumentApprovalRow(repository_id=repository_id, document_id=document_id)
            db.add(row)
        row.document_title = (document_title or row.document_title or "").strip()[:255]
        row.approval_status = status
        row.version = version
        row.effective_date = effective_date
        row.expiry_date = expiry_date
        row.superseded_by = (superseded_by or "").strip()
        row.reason = reason
        row.updated_by = actor_id
        if status == "APPROVED":
            row.approved_by = actor_id
            row.approved_at = _now()
        db.flush()
        after = _approval_to_dict(row)
    record_audit_event(
        action="document_approval_set",
        user_id=actor_id,
        role=actor_role,
        repository_id=repository_id,
        entity_type="document",
        entity_id=document_id,
        outcome=status.lower(),
        before=before,
        after=after,
        reason=reason,
    )
    return after


def repositories_for_msl(msl_id: str) -> list[str]:
    with session_scope() as db:
        owned = [r.repository_id for r in db.query(TherapeuticAreaRow).filter_by(owner_msl_id=msl_id).all()]
        member = [m.repository_id for m in db.query(TherapeuticAreaMslRow).filter_by(msl_user_id=msl_id).all()]
        return list(dict.fromkeys(owned + member))


def self_assign_case(case_id: str, msl_id: str) -> Optional[dict[str, Any]]:
    with session_scope() as db:
        row = db.query(MSLCaseRow).filter_by(case_id=case_id).first()
        msl = db.query(PortalUserRow).filter_by(user_id=msl_id, role="msl").first()
        if not row or not msl:
            return None
        current = (row.assigned_msl_id or "").strip()
        if current and current != msl_id and row.status not in {"open", ""}:
            owner = db.query(PortalUserRow).filter_by(user_id=current).first()
            name = owner.display_name if owner else current
            raise ValueError(f"This question is already taken by {name}.")
        row.assigned_msl_id = msl_id
        if row.status == "open":
            row.status = "assigned"
        db.flush()
        payload = _case_to_dict(row)
    record_audit_event(
        action="case_self_assign",
        user_id=msl_id,
        role="msl",
        case_id=case_id,
        repository_id=payload.get("repository_id") or "",
        entity_type="case",
        entity_id=case_id,
    )
    return payload


DEFAULT_ACK_BODY = (
    "{{reason}}\n\n"
    "Case reference: {{case_id}}. You will receive the reply on this same channel ({{channel}})."
)

DEFAULT_NEW_CASE_BODY = (
    "New doctor question from {{doctor_name}} ({{case_id}}): {{question}}\nReasons: {{reason_codes}}"
)

DEFAULT_MSL_REPLY_BODY = """Dear {{doctor_name}},

Thank you for your scientific question (case {{case_id}}).

Scientific summary
{{draft_body}}

Evidence
Please see the cited documents and page numbers in the accompanying sources. Findings apply to the published evidence, not to an individual patient.

Limitations
This is not individual medical advice or a prescribing recommendation.

Follow-up
If you need further literature or a discussion of additional data, reply on this channel.

Kind regards,
{{msl_name}}
"""

_DEFAULT_TEMPLATES = (
    {
        "template_id": "tpl_doctor_escalation_ack",
        "kind": "doctor_escalation_ack",
        "name": "Doctor escalation acknowledgement",
        "body": DEFAULT_ACK_BODY,
        "channel": "all",
    },
    {
        "template_id": "tpl_msl_new_case",
        "kind": "msl_new_case",
        "name": "MSL new-case notice",
        "body": DEFAULT_NEW_CASE_BODY,
        "channel": "all",
    },
    {
        "template_id": "tpl_msl_response_to_doctor",
        "kind": "msl_response_to_doctor",
        "name": "MSL structured reply to doctor",
        "body": DEFAULT_MSL_REPLY_BODY,
        "channel": "all",
    },
)


def default_policy_payload() -> dict[str, Any]:
    from digital_msl.scope import MOLECULE_ALIASES

    settings = get_dmsl_settings()
    molecules = []
    for name in settings.molecule_list:
        aliases = MOLECULE_ALIASES.get(name, (name,))
        molecules.append({"name": name, "aliases": ", ".join(aliases)})
    return {
        "molecules": molecules,
        "confidence_threshold": float(settings.confidence_threshold),
        "claim_min_score": float(settings.claim_min_score),
    }


def _normalize_policy(payload: dict[str, Any]) -> dict[str, Any]:
    molecules = []
    for item in payload.get("molecules") or []:
        if isinstance(item, str):
            name = item.strip().lower()
            aliases = name
        else:
            name = str(item.get("name") or "").strip().lower()
            aliases = str(item.get("aliases") or name)
        if not name:
            continue
        molecules.append({"name": name, "aliases": aliases})
    threshold = float(payload.get("confidence_threshold", get_dmsl_settings().confidence_threshold))
    claim_min = float(payload.get("claim_min_score", get_dmsl_settings().claim_min_score))
    threshold = min(0.99, max(0.5, threshold))
    claim_min = min(0.99, max(0.3, claim_min))
    return {
        "molecules": molecules or default_policy_payload()["molecules"],
        "confidence_threshold": threshold,
        "claim_min_score": claim_min,
    }


def seed_policy_and_templates() -> None:
    with session_scope() as db:
        if not db.query(PolicyRow).first():
            db.add(PolicyRow(payload_json=json.dumps(default_policy_payload())))
        existing = {r.kind for r in db.query(MessageTemplateRow).all()}
        for tpl in _DEFAULT_TEMPLATES:
            if tpl["kind"] not in existing:
                db.add(MessageTemplateRow(**tpl))


def get_policy() -> dict[str, Any]:
    seed_policy_and_templates()
    with session_scope() as db:
        row = db.query(PolicyRow).order_by(PolicyRow.id.asc()).first()
        if not row:
            return default_policy_payload()
        try:
            return _normalize_policy(json.loads(row.payload_json))
        except Exception:
            return default_policy_payload()


def save_policy(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_policy(payload)
    with session_scope() as db:
        row = db.query(PolicyRow).order_by(PolicyRow.id.asc()).first()
        if row:
            row.payload_json = json.dumps(normalized)
        else:
            db.add(PolicyRow(payload_json=json.dumps(normalized)))
    return normalized


def _template_to_dict(row: MessageTemplateRow) -> dict[str, Any]:
    return {
        "template_id": row.template_id,
        "kind": row.kind,
        "name": row.name,
        "body": row.body,
        "channel": row.channel or "all",
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_templates(kind: Optional[str] = None) -> list[dict[str, Any]]:
    seed_policy_and_templates()
    with session_scope() as db:
        query = db.query(MessageTemplateRow)
        if kind:
            query = query.filter_by(kind=kind)
        return [_template_to_dict(r) for r in query.order_by(MessageTemplateRow.kind, MessageTemplateRow.name).all()]


def get_template(template_id: str) -> Optional[dict[str, Any]]:
    seed_policy_and_templates()
    with session_scope() as db:
        row = db.query(MessageTemplateRow).filter_by(template_id=template_id).first()
        return _template_to_dict(row) if row else None


def get_template_by_kind(kind: str, channel: str = "all") -> Optional[dict[str, Any]]:
    seed_policy_and_templates()
    with session_scope() as db:
        rows = db.query(MessageTemplateRow).filter_by(kind=kind).all()
        if not rows:
            return None
        channel_l = (channel or "all").lower()
        for row in rows:
            if (row.channel or "all").lower() == channel_l:
                return _template_to_dict(row)
        for row in rows:
            if (row.channel or "all").lower() == "all":
                return _template_to_dict(row)
        return _template_to_dict(rows[0])


def upsert_template(
    *,
    template_id: Optional[str] = None,
    kind: str,
    name: str,
    body: str,
    channel: str = "all",
) -> dict[str, Any]:
    if kind not in {"doctor_escalation_ack", "msl_new_case", "msl_response_to_doctor"}:
        raise ValueError("Unknown template kind.")
    channel_n = (channel or "all").lower()
    if channel_n not in {"all", "web", "email", "whatsapp"}:
        raise ValueError("channel must be all, web, email, or whatsapp")
    with session_scope() as db:
        row = None
        if template_id:
            row = db.query(MessageTemplateRow).filter_by(template_id=template_id).first()
        if row is None:
            row = MessageTemplateRow(
                template_id=template_id or new_id("tpl"),
                kind=kind,
                name=name.strip() or kind,
                body=body,
                channel=channel_n,
            )
            db.add(row)
        else:
            row.kind = kind
            row.name = name.strip() or kind
            row.body = body
            row.channel = channel_n
        db.flush()
        return _template_to_dict(row)


def delete_template(template_id: str) -> bool:
    with session_scope() as db:
        row = db.query(MessageTemplateRow).filter_by(template_id=template_id).first()
        if not row:
            return False
        db.delete(row)
        return True


def render_template(kind: str, variables: dict[str, Any], channel: str = "all") -> str:
    tpl = get_template_by_kind(kind, channel=channel)
    body = tpl["body"] if tpl else ""
    if not body:
        fallback = {
            "doctor_escalation_ack": DEFAULT_ACK_BODY,
            "msl_new_case": DEFAULT_NEW_CASE_BODY,
            "msl_response_to_doctor": DEFAULT_MSL_REPLY_BODY,
        }
        body = fallback.get(kind, "")
    for key, value in variables.items():
        body = body.replace("{{" + key + "}}", str(value if value is not None else ""))
    return body.strip()


def seed_default_group() -> None:
    with session_scope() as db:
        group = db.query(MslGroupRow).order_by(MslGroupRow.id).first()
        if not group:
            group_id = "grp_retina_default"
            db.add(MslGroupRow(group_id=group_id, name="Physical MSL team"))
            db.flush()
        else:
            group_id = group.group_id
        if not db.query(MslGroupMemberRow).filter_by(group_id=group_id, msl_user_id="usr_msl").first():
            if db.query(PortalUserRow).filter_by(user_id="usr_msl", role="msl").first():
                db.add(MslGroupMemberRow(group_id=group_id, msl_user_id="usr_msl"))
        for msl_id in _PEER_MSL_IDS:
            if not db.query(MslGroupMemberRow).filter_by(group_id=group_id, msl_user_id=msl_id).first():
                if db.query(PortalUserRow).filter_by(user_id=msl_id, role="msl").first():
                    db.add(MslGroupMemberRow(group_id=group_id, msl_user_id=msl_id))
        doctor = db.query(PortalUserRow).filter_by(user_id="usr_doctor").first()
        if doctor and not (doctor.assigned_group_id or "").strip():
            doctor.assigned_group_id = group_id


def list_groups() -> list[dict[str, Any]]:
    with session_scope() as db:
        groups = []
        for g in db.query(MslGroupRow).order_by(MslGroupRow.name).all():
            members = db.query(MslGroupMemberRow).filter_by(group_id=g.group_id).all()
            doctors = db.query(PortalUserRow).filter_by(role="doctor", assigned_group_id=g.group_id).all()
            groups.append(
                {
                    "group_id": g.group_id,
                    "name": g.name,
                    "msl_ids": [m.msl_user_id for m in members],
                    "doctor_ids": [d.user_id for d in doctors],
                }
            )
        return groups


def create_group(name: str) -> dict[str, Any]:
    group_id = new_id("grp")
    with session_scope() as db:
        db.add(MslGroupRow(group_id=group_id, name=name.strip() or "MSL team"))
    return next(g for g in list_groups() if g["group_id"] == group_id)


def set_group_members(group_id: str, msl_ids: list[str]) -> Optional[dict[str, Any]]:
    with session_scope() as db:
        if not db.query(MslGroupRow).filter_by(group_id=group_id).first():
            return None
        db.query(MslGroupMemberRow).filter_by(group_id=group_id).delete()
        for msl_id in msl_ids:
            db.add(MslGroupMemberRow(group_id=group_id, msl_user_id=msl_id))
    return next((g for g in list_groups() if g["group_id"] == group_id), None)


def assign_doctor_group(doctor_id: str, group_id: str) -> Optional[dict[str, Any]]:
    with session_scope() as db:
        doctor = db.query(PortalUserRow).filter_by(user_id=doctor_id, role="doctor").first()
        group = db.query(MslGroupRow).filter_by(group_id=group_id).first()
        if not doctor or not group:
            return None
        doctor.assigned_group_id = group_id
        db.flush()
        return _user_to_dict(doctor)


def update_portal_profile(
    user_id: str,
    *,
    display_name: Optional[str] = None,
    password: Optional[str] = None,
    current_password: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    from digital_msl import doctor_registry as registry

    if password:
        registry.validate_password(password)
    with session_scope() as db:
        row = db.query(PortalUserRow).filter_by(user_id=user_id).first()
        if not row:
            return None
        if password and not verify_password(current_password or "", row.password_hash):
            raise ValueError("Current password is incorrect.")
        if display_name and display_name.strip():
            row.display_name = display_name.strip()
        if password:
            row.password_hash = hash_password(password)
        db.flush()
        user = _user_to_dict(row)
    if password:
        record_audit_event(action="password_change", user_id=user_id, role=user["role"], entity_type="user", entity_id=user_id)
    return user


def add_notification(*, user_id: str, kind: str, title: str, body: str = "", case_id: str = "") -> None:
    if not user_id:
        return
    with session_scope() as db:
        db.add(
            NotificationRow(
                notification_id=new_id("ntf"),
                user_id=user_id,
                kind=kind,
                title=title,
                body=body,
                case_id=case_id or "",
            )
        )


def notify_group_new_case(case_id: str, question: str, doctor_id: str) -> None:
    with session_scope() as db:
        doctor = db.query(PortalUserRow).filter_by(user_id=doctor_id).first() if doctor_id else None
        group_id = (doctor.assigned_group_id if doctor else "") or ""
        msl_ids = [m.msl_user_id for m in db.query(MslGroupMemberRow).filter_by(group_id=group_id).all()] if group_id else []
        case = db.query(MSLCaseRow).filter_by(case_id=case_id).first()
        repo_id = (case.repository_id if case else "") or ""
        if repo_id:
            msl_ids.extend(msls_for_repository(repo_id))
        if doctor:
            for repo in _repo_ids(doctor):
                msl_ids.extend(msls_for_repository(repo))
        msl_ids = list(dict.fromkeys([i for i in msl_ids if i]))
        if not msl_ids:
            msl_ids = ["usr_msl"]
        title = "New doctor question"
        body = (question or "")[:240]
        for msl_id in msl_ids:
            db.add(
                NotificationRow(
                    notification_id=new_id("ntf"),
                    user_id=msl_id,
                    kind="new_question",
                    title=title,
                    body=body,
                    case_id=case_id,
                )
            )


def notify_doctor_reply(doctor_id: str, case_id: str, body: str) -> None:
    add_notification(
        user_id=doctor_id,
        kind="msl_reply",
        title="Your MSL team replied",
        body=(body or "")[:240],
        case_id=case_id,
    )


def backfill_open_case_notifications(user_id: str) -> None:
    """Create missing new_question rows so MSL login can pop open waiting cases."""
    portal = get_portal_user(user_id)
    if not portal or portal.get("role") != "msl":
        return
    open_cases = [c for c in list_cases(viewer_msl_id=user_id, limit=100) if c.get("status") == "open"]
    if not open_cases:
        return
    with session_scope() as db:
        existing = {
            r.case_id
            for r in db.query(NotificationRow).filter_by(user_id=user_id, kind="new_question").all()
            if r.case_id
        }
        for case in open_cases:
            case_id = str(case.get("case_id") or "")
            if not case_id or case_id in existing:
                continue
            db.add(
                NotificationRow(
                    notification_id=new_id("ntf"),
                    user_id=user_id,
                    kind="new_question",
                    title="New doctor question",
                    body=(case.get("question") or "")[:240],
                    case_id=case_id,
                )
            )
            existing.add(case_id)


def list_notifications(user_id: str, unread_only: bool = False) -> list[dict[str, Any]]:
    backfill_open_case_notifications(user_id)
    with session_scope() as db:
        query = db.query(NotificationRow).filter_by(user_id=user_id)
        if unread_only:
            query = query.filter(NotificationRow.read_at.is_(None))
        rows = query.order_by(NotificationRow.id.desc()).limit(50).all()
        case_ids = [r.case_id for r in rows if r.case_id]
        conv_by_case: dict[str, str] = {}
        if case_ids:
            for case in db.query(MSLCaseRow).filter(MSLCaseRow.case_id.in_(case_ids)).all():
                conv_by_case[case.case_id] = case.conversation_id or ""
        return [
            {
                "notification_id": r.notification_id,
                "kind": r.kind,
                "title": r.title,
                "body": r.body or "",
                "case_id": r.case_id or "",
                "conversation_id": conv_by_case.get(r.case_id or "", ""),
                "read": bool(r.read_at),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


def mark_notifications_read(user_id: str, notification_id: Optional[str] = None) -> None:
    with session_scope() as db:
        query = db.query(NotificationRow).filter_by(user_id=user_id)
        if notification_id:
            query = query.filter_by(notification_id=notification_id)
        now = datetime.now(timezone.utc)
        for row in query.filter(NotificationRow.read_at.is_(None)).all():
            row.read_at = now
