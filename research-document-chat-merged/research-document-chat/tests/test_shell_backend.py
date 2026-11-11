"""Doctor context, clean upstream errors, approved-source gate and planted-document exclusion."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from digital_msl import evidence, store, upstream
from digital_msl.config import get_dmsl_settings

API = "/api/v1/digital-msl"
DEMO_PASSWORD = "DemoMSL#2026"


@pytest.fixture
def demo_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DIGITAL_MSL_DATABASE_URL", f"sqlite:///{tmp_path / 'shell.db'}")
    monkeypatch.setenv("DIGITAL_MSL_TRACE_DIR", str(tmp_path / "traces"))
    monkeypatch.setenv("DIGITAL_MSL_DEMO_MODE", "true")
    monkeypatch.setenv("DIGITAL_MSL_ENVIRONMENT", "development")
    monkeypatch.setenv("DIGITAL_MSL_LEGACY_DEMO_USERS", "false")
    monkeypatch.setenv("DIGITAL_MSL_APPROVED_REPOSITORY_IDS", "")  # the real default
    get_dmsl_settings.cache_clear()
    store._session_factory.cache_clear()
    evidence.clear_document_caches()
    upstream.reset_ikm_status_cache()
    store.init_store()
    store.seed_demo_accounts()
    from app import app

    with TestClient(app) as client:
        yield client
    get_dmsl_settings.cache_clear()
    store._session_factory.cache_clear()


def _auth(client, email):
    res = client.post(f"{API}/portal/login", json={"email": email, "password": DEMO_PASSWORD})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


# ------------------------------------------------------------------ /me/context
def test_demo_doctor_context_lists_launch_areas_and_routes_to_msl(demo_client):
    ctx = demo_client.get(f"{API}/me/context", headers=_auth(demo_client, "doctor@demo.example")).json()
    names = [a["name"] for a in ctx["areas"]]
    assert names == ["Retina and Vitreous", "Diabetic Retinopathy and Macular Disease"]
    assert ctx["areas"][0]["is_default"] is True and all(a["enabled"] for a in ctx["areas"])
    assert ctx["verification_status"] == "VERIFIED"
    assert ctx["routed_to_msl"] is True  # allow-list is empty
    assert ctx["knowledge_sources"]["status"] == "not_configured"
    assert ctx["knowledge_sources"]["message"] == "Your question will be reviewed by a Physical MSL."
    assert ctx["has_enabled_areas"] is True


def test_context_reports_unavailable_sources_without_raw_text(demo_client, monkeypatch):
    area = demo_client.get(f"{API}/me/context", headers=_auth(demo_client, "doctor@demo.example")).json()["areas"][0]
    monkeypatch.setenv("DIGITAL_MSL_APPROVED_REPOSITORY_IDS", "*")
    get_dmsl_settings.cache_clear()
    # pending:* areas have no repository yet, so they never answer from sources
    ctx = demo_client.get(f"{API}/me/context", headers=_auth(demo_client, "doctor@demo.example")).json()
    assert ctx["routed_to_msl"] is True and area["answers_from_sources"] is False

    store.upsert_therapeutic_area(repository_id="repo-live", name="Live area", owner_msl_id="usr_demo_msl")
    store.assign_doctor_repositories("usr_demo_doctor", ["repo-live"])
    monkeypatch.setattr(upstream, "ikm_status", lambda force=False: {"reachable": False, "code": "TLS_ERROR", "checked_at": "x"})
    ctx = demo_client.get(f"{API}/me/context", headers=_auth(demo_client, "doctor@demo.example")).json()
    assert ctx["routed_to_msl"] is False
    assert ctx["knowledge_sources"]["status"] == "unavailable"
    assert ctx["knowledge_sources"]["message"] == upstream.FRIENDLY_MESSAGE
    assert "TLS" not in str(ctx) and "ssl" not in str(ctx).lower()


def test_doctor_with_no_areas_gets_empty_list(demo_client):
    store.assign_doctor_repositories("usr_demo_doctor", [])
    ctx = demo_client.get(f"{API}/me/context", headers=_auth(demo_client, "doctor@demo.example")).json()
    assert ctx["areas"] == [] and ctx["has_enabled_areas"] is False


# ------------------------------------------------------------------ clean upstream errors
def test_doctor_cannot_list_repositories(demo_client):
    res = demo_client.get("/api/v1/repositories", headers=_auth(demo_client, "doctor@demo.example"))
    assert res.status_code == 403


def test_repository_failure_is_a_clean_503_with_request_id(demo_client, monkeypatch):
    from routes import repositories as repo_routes
    from services.dms_client import DmsError

    class Boom:
        def list_repositories(self):
            raise DmsError("DMS auth token request failed: HTTPSConnectionPool(host='16.113.145.53') SSLError tlsv1 alert internal error")

    monkeypatch.setattr(repo_routes, "dms", lambda: Boom())
    res = demo_client.get("/api/v1/repositories", headers=_auth(demo_client, "msl@demo.example"))
    assert res.status_code == 503
    body = res.json()
    assert body["detail"] == upstream.FRIENDLY_MESSAGE
    assert body["code"] == upstream.KNOWLEDGE_SOURCES_UNAVAILABLE
    assert body["request_id"] and res.headers["x-request-id"]
    raw = res.text.lower()
    assert "bad gateway" not in raw and "ssl" not in raw and "16.113" not in raw and "dms error" not in raw


def test_unexpected_failure_is_also_clean(demo_client, monkeypatch):
    from routes import repositories as repo_routes

    class Boom:
        def list_repositories(self):
            raise RuntimeError("connection reset by peer 10.0.0.5")

    monkeypatch.setattr(repo_routes, "dms", lambda: Boom())
    res = demo_client.get("/api/v1/repositories", headers=_auth(demo_client, "admin@demo.example"))
    assert res.status_code == 503 and "10.0.0.5" not in res.text


def test_health_reports_ikm_separately(demo_client, monkeypatch):
    monkeypatch.setattr(upstream, "ikm_status", lambda force=False: {"reachable": False, "code": "TIMEOUT", "checked_at": "t"})
    body = demo_client.get("/health").json()
    assert body["status"] == "healthy" and body["ikm"]["reachable"] is False and body["ikm"]["code"] == "TIMEOUT"


# ------------------------------------------------------------------ approved-source gate
RESULT = {
    "chunk_id": "c1",
    "text": "Aflibercept 2 mg improved visual acuity by 10.7 letters at week 52 in patients with diabetic macular edema.",
    "citation_anchor": {"document_id": "doc-1", "page_start": 3, "parent_section": "RESULTS"},
    "score": 0.9,
}


class Dms:
    def __init__(self, docs):
        self.docs = docs  # document_id -> metadata dict

    def retrieve(self, *, query, top_k=10, repository_id=None, **kw):
        results = []
        for doc_id, meta in self.docs.items():
            results.append({**RESULT, "chunk_id": f"c-{doc_id}", "citation_anchor": {**RESULT["citation_anchor"], "document_id": doc_id}, "metadata": meta})
        return {"results": results}

    def list_repository_documents(self, repository_id, *, limit=500):
        return {"documents": [{"document_id": d, "original_file_name": f"{d}.pdf"} for d in self.docs]}


PLANTED = {
    "approved": {"approval_status": "APPROVED", "version": "2.0", "effective_date": "2025-01-01", "expiry_date": "2099-01-01"},
    "draft": {"approval_status": "DRAFT", "version": "0.3"},
    "in_review": {"approval_status": "IN_REVIEW", "version": "1.0"},
    "superseded_by": {"approval_status": "APPROVED", "version": "1.0", "superseded_by": "approved@2.0"},
    "superseded_status": {"approval_status": "SUPERSEDED", "version": "1.0"},
    "withdrawn": {"approval_status": "WITHDRAWN", "version": "1.0"},
    "expired": {"approval_status": "APPROVED", "version": "1.0", "expiry_date": "2020-01-01"},
    "future": {"approval_status": "APPROVED", "version": "3.0", "effective_date": "2099-01-01"},
}


def _retrieve(monkeypatch, docs, *, approved="*", require=False):
    monkeypatch.setenv("DIGITAL_MSL_APPROVED_REPOSITORY_IDS", approved)
    monkeypatch.setenv("DIGITAL_MSL_REQUIRE_APPROVAL_METADATA", "true" if require else "false")
    get_dmsl_settings.cache_clear()
    evidence.clear_document_caches()
    return evidence.retrieve_evidence(Dms(docs), query="aflibercept", repository_id="repo-1")


def test_only_the_approved_in_force_document_is_retrieved(monkeypatch):
    outcome = _retrieve(monkeypatch, PLANTED)
    assert [c.document_id for c in outcome.chunks] == ["approved"]
    assert outcome.chunks[0].version == "2.0" and outcome.chunks[0].effective_date == "2025-01-01"
    excluded = {e["document_id"]: e["reason"] for e in outcome.excluded}
    assert excluded == {
        "draft": "not_approved:draft",
        "in_review": "not_approved:in_review",
        "superseded_by": "superseded",
        "superseded_status": "not_approved:superseded",
        "withdrawn": "not_approved:withdrawn",
        "expired": "expired",
        "future": "not_yet_effective",
    }


def test_when_only_unapproved_documents_exist_nothing_is_returned(monkeypatch):
    docs = {k: v for k, v in PLANTED.items() if k != "approved"}
    outcome = _retrieve(monkeypatch, docs)
    assert outcome.chunks == [] and outcome.no_approved_source is True


def test_missing_metadata_is_dropped_only_when_required(monkeypatch):
    docs = {"bare": {}}
    kept = _retrieve(monkeypatch, docs, require=False)
    assert [c.document_id for c in kept.chunks] == ["bare"] and kept.chunks[0].version == "" and kept.unversioned == ["bare"]
    dropped = _retrieve(monkeypatch, docs, require=True)
    assert dropped.chunks == [] and dropped.excluded[0]["reason"] == "missing_approval_metadata"


def test_metadata_from_the_document_listing_is_used(monkeypatch):
    class Listing(Dms):
        def list_repository_documents(self, repository_id, *, limit=500):
            return {"documents": [{"document_id": "d", "original_file_name": "d.pdf", "metadata": {"approval_status": "DRAFT", "version": "9"}}]}

    monkeypatch.setenv("DIGITAL_MSL_APPROVED_REPOSITORY_IDS", "*")
    get_dmsl_settings.cache_clear()
    evidence.clear_document_caches()
    outcome = evidence.retrieve_evidence(Listing({"d": {}}), query="x", repository_id="repo-1")
    assert outcome.chunks == [] and outcome.excluded[0]["reason"] == "not_approved:draft"


def test_empty_allow_list_answers_nothing(monkeypatch):
    outcome = _retrieve(monkeypatch, {"approved": PLANTED["approved"]}, approved="")
    assert outcome.chunks == [] and outcome.no_approved_source is True
    other = _retrieve(monkeypatch, {"approved": PLANTED["approved"]}, approved="some-other-repo")
    assert other.chunks == [] and other.no_approved_source is True
    listed = _retrieve(monkeypatch, {"approved": PLANTED["approved"]}, approved="repo-1")
    assert [c.document_id for c in listed.chunks] == ["approved"]


def test_across_repositories_respects_the_allow_list(monkeypatch):
    monkeypatch.setenv("DIGITAL_MSL_APPROVED_REPOSITORY_IDS", "repo-ok")
    get_dmsl_settings.cache_clear()
    evidence.clear_document_caches()
    outcome, repo = evidence.retrieve_evidence_across_repositories(
        Dms({"approved": PLANTED["approved"]}), query="x", repository_ids=["repo-blocked", "repo-ok"]
    )
    assert repo == "repo-ok" and [c.document_id for c in outcome.chunks] == ["approved"]
    none, _ = evidence.retrieve_evidence_across_repositories(Dms({"a": {}}), query="x", repository_ids=["repo-blocked"])
    assert none.no_approved_source is True and none.chunks == []


def test_planted_documents_are_never_quoted_by_the_agent(demo_client, monkeypatch):
    from digital_msl.agents.query_agent import QueryAgent
    from digital_msl.contracts import InboundMessage

    monkeypatch.setenv("DIGITAL_MSL_APPROVED_REPOSITORY_IDS", "*")
    get_dmsl_settings.cache_clear()
    docs = {k: {**v} for k, v in PLANTED.items() if k != "approved"}
    message = InboundMessage(
        channel="web", repository_id="repo-1", user_id="usr_demo_doctor",
        message_text="What did aflibercept do to visual acuity in diabetic macular edema?",
    )
    store.upsert_therapeutic_area(repository_id="repo-1", name="Repo one", owner_msl_id="usr_demo_msl")
    store.assign_doctor_repositories("usr_demo_doctor", ["repo-1"])
    response = asyncio.run(QueryAgent(retrieve_client=Dms(docs), use_default_llm=False).handle(message))
    assert response.decision == "escalate_to_msl" and response.msl_case_id
    assert "10.7 letters" not in response.answer
    assert "NO_APPROVED_SOURCE" in response.reason_codes


def test_empty_allow_list_escalates_with_the_standard_reply(demo_client):
    from digital_msl import standard_replies
    from digital_msl.agents.query_agent import QueryAgent
    from digital_msl.contracts import InboundMessage

    message = InboundMessage(
        channel="web", repository_id="pending:retina-and-vitreous", user_id="usr_demo_doctor",
        message_text="What did aflibercept do to visual acuity in diabetic macular edema?",
    )
    response = asyncio.run(QueryAgent(retrieve_client=Dms({}), use_default_llm=False).handle(message))
    assert response.decision == "escalate_to_msl" and response.msl_case_id
    assert "NO_APPROVED_SOURCE" in response.reason_codes
    assert standard_replies.NOT_FOUND in response.answer
