"""Approval registry: IKM carries no approval fields, so Digital MSL records them and the retrieval filter obeys them."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from digital_msl import evidence, store, upstream
from digital_msl.config import get_dmsl_settings

API = "/api/v1/digital-msl"
DEMO_PASSWORD = "DemoMSL#2026"
REPO = "repo-test"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DIGITAL_MSL_DATABASE_URL", f"sqlite:///{tmp_path / 'approvals.db'}")
    monkeypatch.setenv("DIGITAL_MSL_TRACE_DIR", str(tmp_path / "traces"))
    monkeypatch.setenv("DIGITAL_MSL_DEMO_MODE", "true")
    monkeypatch.setenv("DIGITAL_MSL_ENVIRONMENT", "development")
    monkeypatch.setenv("DIGITAL_MSL_LEGACY_DEMO_USERS", "false")
    monkeypatch.setenv("DIGITAL_MSL_APPROVED_REPOSITORY_IDS", REPO)
    monkeypatch.setenv("DIGITAL_MSL_REQUIRE_APPROVAL_METADATA", "true")
    monkeypatch.setenv("DIGITAL_MSL_DEFAULT_REPOSITORY_ID", "")
    get_dmsl_settings.cache_clear()
    store._session_factory.cache_clear()
    evidence.clear_document_caches()
    upstream.reset_ikm_status_cache()
    store.init_store()
    store.seed_demo_accounts()
    store.upsert_therapeutic_area(repository_id=REPO, name="Test repo", owner_msl_id="usr_demo_msl")
    from app import app

    with TestClient(app) as c:
        yield c
    get_dmsl_settings.cache_clear()
    store._session_factory.cache_clear()


def _auth(client, email):
    client.cookies.clear()
    res = client.post(f"{API}/portal/login", json={"email": email, "password": DEMO_PASSWORD})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def approve(doc="d-approved", **over):
    body = dict(approval_status="APPROVED", version="2.0", effective_date="2025-01-01", reason="")
    body.update(over)
    return store.set_document_approval(REPO, doc, actor_id="usr_demo_msl", actor_role="msl", **body)


# ------------------------------------------------------------------ validation and audit
def test_validation_rules(client):
    with pytest.raises(ValueError, match="approval_status"):
        approve(approval_status="MAYBE")
    with pytest.raises(ValueError, match="version"):
        approve(version="")
    with pytest.raises(ValueError, match="effective date"):
        approve(effective_date="not a date")
    with pytest.raises(ValueError, match="Expiry date"):
        approve(expiry_date="soon")
    with pytest.raises(ValueError, match="replaces"):
        approve(approval_status="SUPERSEDED", version="", effective_date="")


def test_leaving_approved_needs_a_reason_and_everything_is_audited(client):
    approve()
    with pytest.raises(ValueError, match="reason"):
        store.set_document_approval(REPO, "d-approved", approval_status="WITHDRAWN", actor_id="usr_demo_msl", actor_role="msl")
    store.set_document_approval(
        REPO, "d-approved", approval_status="WITHDRAWN", reason="Label withdrawn by regulatory", actor_id="usr_demo_msl", actor_role="msl"
    )
    events = store.list_audit_events(action="document_approval_set", limit=10)["events"]
    assert len(events) == 2
    last = events[0]
    assert last["entity_id"] == "d-approved" and last["detail"].get("reason") == "Label withdrawn by regulatory" or last["reason"]
    assert last["before"]["approval_status"] == "APPROVED" and last["after"]["approval_status"] == "WITHDRAWN"


# ------------------------------------------------------------------ the retrieval filter obeys the registry
RESULT = {
    "text": "Aflibercept 2 mg improved visual acuity by 10.7 letters at week 52.",
    "citation_anchor": {"document_id": "x", "page_start": 2, "parent_section": "RESULTS"},
    "score": 0.9,
}


class Dms:
    def __init__(self, docs, ikm_meta=None):
        self.docs, self.ikm_meta = docs, ikm_meta or {}

    def retrieve(self, *, query, top_k=10, repository_id=None, **kw):
        out = []
        for doc in self.docs:
            hit = {**RESULT, "chunk_id": f"c-{doc}", "citation_anchor": {**RESULT["citation_anchor"], "document_id": doc}}
            if doc in self.ikm_meta:
                hit["metadata"] = self.ikm_meta[doc]
            out.append(hit)
        return {"results": out}

    def list_repository_documents(self, repository_id, *, limit=500):
        return {"documents": [{"document_id": d, "original_file_name": f"{d}.pdf"} for d in self.docs]}


def retrieve(docs, **kw):
    evidence.clear_document_caches()
    return evidence.retrieve_evidence(Dms(docs, **kw), query="aflibercept", repository_id=REPO)


def test_unregistered_documents_are_never_used(client):
    outcome = retrieve(["d1", "d2"])
    assert outcome.chunks == [] and outcome.no_approved_source is True
    assert {e["reason"] for e in outcome.excluded} == {"missing_approval_metadata"}


def test_only_registered_approved_in_force_documents_pass(client):
    approve("d-ok", version="3.1", effective_date="2025-02-01", expiry_date="2099-01-01", document_title="Planted label")
    approve("d-draft", approval_status="DRAFT", version="0.2", effective_date="", expiry_date="")
    approve("d-expired", version="1.0", effective_date="2019-01-01", expiry_date="2020-01-01")
    approve("d-old", approval_status="SUPERSEDED", version="1.0", effective_date="", superseded_by="d-ok@3.1")
    approve("d-future", version="4.0", effective_date="2099-01-01")
    approve("d-review", approval_status="IN_REVIEW", version="", effective_date="")
    outcome = retrieve(["d-ok", "d-draft", "d-expired", "d-old", "d-future", "d-review", "d-unregistered"])
    assert [c.document_id for c in outcome.chunks] == ["d-ok"]
    chunk = outcome.chunks[0]
    assert chunk.version == "3.1" and chunk.effective_date == "2025-02-01" and chunk.document_title == "Planted label"
    reasons = {e["document_id"]: e["reason"] for e in outcome.excluded}
    assert reasons == {
        "d-draft": "not_approved:draft",
        "d-expired": "expired",
        "d-old": "not_approved:superseded",
        "d-future": "not_yet_effective",
        "d-review": "not_approved:in_review",
        "d-unregistered": "missing_approval_metadata",
    }


def test_registry_overrides_what_ikm_says(client):
    approve("d1", approval_status="WITHDRAWN", version="1.0", effective_date="", reason="withdrawn by owner") if False else None
    approve("d1")
    store.set_document_approval(REPO, "d1", approval_status="WITHDRAWN", reason="withdrawn by owner", actor_id="usr_demo_msl", actor_role="msl")
    outcome = retrieve(["d1"], ikm_meta={"d1": {"approval_status": "APPROVED", "version": "9"}})
    assert outcome.chunks == [] and outcome.excluded[0]["reason"] == "not_approved:withdrawn"


def test_planted_draft_text_is_never_quoted_by_the_agent(client):
    from digital_msl.agents.query_agent import QueryAgent
    from digital_msl.contracts import InboundMessage

    store.assign_doctor_repositories("usr_demo_doctor", [REPO])
    approve("d-ok", version="1.0", effective_date="2025-01-01")
    approve("d-draft", approval_status="DRAFT", version="0.1", effective_date="")
    dms = Dms(["d-ok", "d-draft"])
    dms.retrieve = lambda **kw: {  # noqa: E731
        "results": [
            {**RESULT, "chunk_id": "c-draft", "text": "DRAFT-ONLY claim: dosing interval is 91 weeks.", "citation_anchor": {"document_id": "d-draft", "page_start": 1}},
        ]
    }
    message = InboundMessage(channel="web", repository_id=REPO, user_id="usr_demo_doctor", message_text="What is the aflibercept dosing interval in diabetic macular edema?")
    response = asyncio.run(QueryAgent(retrieve_client=dms, use_default_llm=False).handle(message))
    assert response.decision == "escalate_to_msl" and "NO_APPROVED_SOURCE" in response.reason_codes
    assert "91 weeks" not in response.answer


# ------------------------------------------------------------------ API
def test_api_roles_and_scope(client, monkeypatch):
    doctor = _auth(client, "doctor@demo.example")
    assert client.get(f"{API}/document-approvals", headers=doctor, params={"repository_id": REPO}).status_code == 403
    assert client.put(f"{API}/document-approvals/{REPO}/d1", headers=doctor, json={"approval_status": "APPROVED"}).status_code == 403

    msl = _auth(client, "msl@demo.example")
    body = {"approval_status": "APPROVED", "version": "1.0", "effective_date": "2025-01-01", "document_title": "Label"}
    ok = client.put(f"{API}/document-approvals/{REPO}/d1", headers=msl, json=body)
    assert ok.status_code == 200 and ok.json()["approved_by"] == "usr_demo_msl"
    assert client.put(f"{API}/document-approvals/{REPO}/d1", headers=msl, json={"approval_status": "APPROVED"}).status_code == 422
    assert client.put(f"{API}/document-approvals/other-repo/d1", headers=msl, json=body).status_code == 403  # outside the MSL's areas

    admin = _auth(client, "admin@demo.example")
    assert client.put(f"{API}/document-approvals/other-repo/d1", headers=admin, json=body).status_code == 200


def test_api_list_merges_ikm_documents_with_the_registry(client, monkeypatch):
    import services.dms_client as dms_module

    class FakeClient:
        def list_repository_documents(self, repository_id, limit=500):
            return {"documents": [{"document_id": "d1", "original_file_name": "a.docx", "status": "completed"}, {"document_id": "d2", "original_file_name": "b.docx", "status": "completed"}]}

    monkeypatch.setattr(dms_module, "DmsClient", FakeClient)
    approve("d1")
    msl = _auth(client, "msl@demo.example")
    body = client.get(f"{API}/document-approvals", headers=msl, params={"repository_id": REPO}).json()
    by_id = {d["document_id"]: d for d in body["documents"]}
    assert by_id["d1"]["approval"]["approval_status"] == "APPROVED" and by_id["d2"]["approval"] is None
    assert body["on_allow_list"] is True and body["require_approval_metadata"] is True
    assert body["counts"] == {"documents": 2, "approved": 1, "not_registered": 1}


def test_api_listing_failure_is_a_clean_503(client, monkeypatch):
    import services.dms_client as dms_module

    class Boom:
        def list_repository_documents(self, repository_id, limit=500):
            raise RuntimeError("tls handshake failure to 10.1.2.3")

    monkeypatch.setattr(dms_module, "DmsClient", Boom)
    msl = _auth(client, "msl@demo.example")
    res = client.get(f"{API}/document-approvals", headers=msl, params={"repository_id": REPO})
    assert res.status_code == 503 and "10.1.2.3" not in res.text and res.json()["code"] == upstream.KNOWLEDGE_SOURCES_UNAVAILABLE
