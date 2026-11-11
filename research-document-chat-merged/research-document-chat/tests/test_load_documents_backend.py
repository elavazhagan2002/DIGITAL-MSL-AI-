"""Load Documents: page state, IKM status, demo fixture (demo mode only) and clean upload errors."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from digital_msl import evidence, load_documents, store, upstream
from digital_msl.config import get_dmsl_settings

API = "/api/v1/digital-msl"
PASSWORD = "DemoMSL#2026"
OK_IKM = {"reachable": True, "code": "OK", "checked_at": "2026-11-09T00:00:00Z"}
BAD_IKM = {"reachable": False, "code": "TLS_ERROR", "checked_at": "2026-11-09T00:00:00Z"}


def _make_client(tmp_path, monkeypatch, *, demo: bool, allow: str = ""):
    monkeypatch.setenv("DIGITAL_MSL_DATABASE_URL", f"sqlite:///{tmp_path / 'ld.db'}")
    monkeypatch.setenv("DIGITAL_MSL_TRACE_DIR", str(tmp_path / "traces"))
    monkeypatch.setenv("DIGITAL_MSL_DEMO_MODE", "true" if demo else "false")
    monkeypatch.setenv("DIGITAL_MSL_SEED_DEMO_USERS", "false")
    monkeypatch.setenv("DIGITAL_MSL_ENVIRONMENT", "development")
    monkeypatch.setenv("DIGITAL_MSL_LEGACY_DEMO_USERS", "true" if not demo else "false")
    monkeypatch.setenv("DIGITAL_MSL_APPROVED_REPOSITORY_IDS", allow)
    get_dmsl_settings.cache_clear()
    store._session_factory.cache_clear()
    evidence.clear_document_caches()
    upstream.reset_ikm_status_cache()
    load_documents.reset_demo_uploads()
    store.init_store()
    if demo:
        store.seed_demo_accounts()
    from app import app

    return TestClient(app)


@pytest.fixture
def demo_client(tmp_path, monkeypatch):
    with _make_client(tmp_path, monkeypatch, demo=True) as c:
        yield c
    get_dmsl_settings.cache_clear()
    store._session_factory.cache_clear()


@pytest.fixture
def real_client(tmp_path, monkeypatch):
    """Demo mode OFF (production behaviour); the older seeded accounts are used."""
    with _make_client(tmp_path, monkeypatch, demo=False) as c:
        yield c
    get_dmsl_settings.cache_clear()
    store._session_factory.cache_clear()


def _auth(client, email, password=PASSWORD):
    res = client.post(f"{API}/portal/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    client.cookies.clear()
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


class FakeDms:
    def __init__(self, repos=None, error=None):
        self.repos, self.error = repos or [], error

    def list_repositories(self):
        if self.error:
            raise self.error
        return {"repositories": self.repos}

    def get_repository(self, repository_id):
        if self.error:
            raise self.error
        return next((r for r in self.repos if r["repository_id"] == repository_id), {})


def _patch_ikm(monkeypatch, *, status=OK_IKM, dms=None):
    from routes import dms_proxy_helpers

    monkeypatch.setattr(load_documents, "configured", lambda: (True, []))  # the suite has no IKM credentials of its own
    monkeypatch.setattr(upstream, "ikm_status", lambda force=False: status)
    monkeypatch.setattr(dms_proxy_helpers, "dms", lambda: dms or FakeDms())


def _legacy_msl(client):
    return _auth(client, "msl@digital-msl.local", "MslPass2026!")


# ------------------------------------------------------------------ states with demo mode OFF (production behaviour)
def test_unreachable_ikm_is_a_clear_state_without_fake_data(real_client, monkeypatch):
    _patch_ikm(monkeypatch, status=BAD_IKM)
    ctx = real_client.get(f"{API}/load-documents/context", headers=_legacy_msl(real_client)).json()
    assert ctx["state"] == "ikm_unreachable" and ctx["code"] == "TLS_ERROR" and ctx["request_id"]
    assert ctx["repositories"] == [] and ctx["demo"] is False
    assert "Bad Gateway" not in str(ctx) and "16.113" not in str(ctx)


def test_not_configured_names_the_missing_settings_only_for_admin(real_client, monkeypatch):
    _patch_ikm(monkeypatch)
    monkeypatch.setattr(load_documents, "configured", lambda: (False, ["RAG_BUILDER_BASE_URL"]))
    msl = real_client.get(f"{API}/load-documents/context", headers=_legacy_msl(real_client)).json()
    assert msl["state"] == "not_configured" and msl["missing"] == []
    admin = real_client.get(f"{API}/load-documents/context", headers=_auth(real_client, "admin@digital-msl.local", "AdminPass2026!")).json()
    assert admin["state"] == "not_configured" and admin["missing"] == ["RAG_BUILDER_BASE_URL"]


def test_unauthorised_is_its_own_state(real_client, monkeypatch):
    from services.dms_client import DmsError

    err = DmsError("auth refused")
    err.status_code = 401
    _patch_ikm(monkeypatch, dms=FakeDms(error=err))
    ctx = real_client.get(f"{API}/load-documents/context", headers=_legacy_msl(real_client)).json()
    assert ctx["state"] == "unauthorised" and ctx["ikm"]["unauthorised"] is True and ctx["repositories"] == []


def test_no_repositories_when_none_is_linked_to_the_msl(real_client, monkeypatch):
    _patch_ikm(monkeypatch, dms=FakeDms(repos=[{"repository_id": "repo-x", "name": "Unlinked", "document_count": 3}]))
    ctx = real_client.get(f"{API}/load-documents/context", headers=_legacy_msl(real_client)).json()
    assert ctx["state"] == "no_repositories" and ctx["code"] == "NO_REPOSITORIES"


def test_working_state_lists_linked_repositories_with_area_approval_and_count(real_client, monkeypatch):
    store.upsert_therapeutic_area(repository_id="repo-1", name="Retina and Vitreous", owner_msl_id="usr_msl")
    store.upsert_therapeutic_area(repository_id="repo-2", name="Glaucoma", owner_msl_id="usr_msl")
    monkeypatch.setenv("DIGITAL_MSL_APPROVED_REPOSITORY_IDS", "repo-1")
    get_dmsl_settings.cache_clear()
    repos = [
        {"repository_id": "repo-1", "name": "Retina library", "document_count": 12},
        {"repository_id": "repo-2", "name": "Glaucoma library", "document_count": 0},
        {"repository_id": "repo-9", "name": "Not mine", "document_count": 5},
        {"repository_id": "repo-t", "name": "_temp_scratch", "document_count": 1},
    ]
    _patch_ikm(monkeypatch, dms=FakeDms(repos=repos))
    ctx = real_client.get(f"{API}/load-documents/context", headers=_legacy_msl(real_client)).json()
    assert ctx["state"] == "ok" and ctx["demo"] is False
    by_id = {r["repository_id"]: r for r in ctx["repositories"]}
    assert set(by_id) == {"repo-1", "repo-2"}
    assert by_id["repo-1"] == {
        "repository_id": "repo-1", "name": "Retina library", "therapeutic_area": "Retina and Vitreous",
        "approved": True, "document_count": 12, "demo": False,
    }
    assert by_id["repo-2"]["approved"] is False
    assert ctx["allow_list"] == {"empty": False, "all": False, "approved_count": 1}


def test_empty_allow_list_is_reported_but_does_not_block_loading(real_client, monkeypatch):
    store.upsert_therapeutic_area(repository_id="repo-1", name="Retina", owner_msl_id="usr_msl")
    _patch_ikm(monkeypatch, dms=FakeDms(repos=[{"repository_id": "repo-1", "name": "Retina library"}]))
    ctx = real_client.get(f"{API}/load-documents/context", headers=_legacy_msl(real_client)).json()
    assert ctx["state"] == "ok" and ctx["allow_list"]["empty"] is True and ctx["repositories"][0]["approved"] is False


def test_doctor_is_not_allowed(real_client):
    headers = _auth(real_client, "doctor@digital-msl.local", "DoctorPass2026!")
    assert real_client.get(f"{API}/load-documents/context", headers=headers).status_code == 403
    assert real_client.get(f"{API}/ikm/status", headers=headers).status_code == 403


def test_ikm_status_separates_the_cases_and_hides_secrets(real_client, monkeypatch):
    _patch_ikm(monkeypatch, status=BAD_IKM)
    admin = _auth(real_client, "admin@digital-msl.local", "AdminPass2026!")
    body = real_client.get(f"{API}/ikm/status", headers=admin).json()
    assert body["configured"] is True and body["reachable"] is False and body["unauthorised"] is False
    assert body["code"] == "TLS_ERROR" and body["allow_list"]["empty"] is True and body["demo_fixture_active"] is False
    assert "password" not in str(body).lower() and "secret" not in str(body).lower()


def test_fixture_never_appears_with_demo_mode_off(real_client, monkeypatch):
    from services.dms_client import DmsError

    _patch_ikm(monkeypatch, status=BAD_IKM, dms=FakeDms(error=DmsError("down")))
    headers = _legacy_msl(real_client)
    assert real_client.get(f"{API}/load-documents/context", headers=headers).json()["repositories"] == []
    res = real_client.get(f"/api/v1/repositories/{load_documents.DEMO_REPOSITORY_ID}/documents", headers=headers)
    assert res.status_code in (503, 404) and "demo" not in res.text.lower().replace("demo-test", "")
    up = real_client.post(
        f"{API}/therapeutic-areas/{load_documents.DEMO_REPOSITORY_ID}/documents/upload",
        headers=headers, files={"files": ("a.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert up.status_code in (403, 503)  # normal access rules: the fixture id is not a real repository


# ------------------------------------------------------------------ demo fixture (demo mode ON and IKM unusable)
def test_demo_mode_serves_a_labelled_fixture_when_ikm_is_unreachable(demo_client, monkeypatch):
    _patch_ikm(monkeypatch, status=BAD_IKM)
    ctx = demo_client.get(f"{API}/load-documents/context", headers=_auth(demo_client, "msl@demo.example")).json()
    assert ctx["state"] == "ok" and ctx["demo"] is True
    assert [r["repository_id"] for r in ctx["repositories"]] == [load_documents.DEMO_REPOSITORY_ID]
    assert ctx["repositories"][0]["name"].startswith("Demo data") and ctx["repositories"][0]["demo"] is True
    assert ctx["ikm"]["reachable"] is False  # the truth is still reported next to the demo data


def test_real_ikm_wins_over_the_fixture_even_in_demo_mode(demo_client, monkeypatch):
    store.upsert_therapeutic_area(repository_id="repo-1", name="Retina", owner_msl_id="usr_demo_msl")
    _patch_ikm(monkeypatch, dms=FakeDms(repos=[{"repository_id": "repo-1", "name": "Retina library"}]))
    ctx = demo_client.get(f"{API}/load-documents/context", headers=_auth(demo_client, "msl@demo.example")).json()
    assert ctx["demo"] is False and [r["repository_id"] for r in ctx["repositories"]] == ["repo-1"]


def test_demo_fixture_documents_and_upload_progression(demo_client, monkeypatch):
    _patch_ikm(monkeypatch, status=BAD_IKM)
    headers = _auth(demo_client, "msl@demo.example")
    rid = load_documents.DEMO_REPOSITORY_ID
    docs = demo_client.get(f"/api/v1/repositories/{rid}/documents", headers=headers).json()
    assert docs["demo"] is True and {d["status"] for d in docs["documents"]} == {"completed", "processing", "failed"}
    failed = next(d for d in docs["documents"] if d["status"] == "failed")
    assert failed["error_details"]

    up = demo_client.post(
        f"{API}/therapeutic-areas/{rid}/documents/upload", headers=headers,
        files={"files": ("My upload.pdf", b"%PDF-1.4 demo", "application/pdf")},
    )
    assert up.status_code == 200 and up.json()["demo"] is True
    doc_id = up.json()["documents"][0]["document_id"]
    first = next(d for d in demo_client.get(f"/api/v1/repositories/{rid}/documents", headers=headers).json()["documents"] if d["document_id"] == doc_id)
    assert first["status"] == "queued"
    for item in load_documents._demo_uploads:
        item["upload_timestamp"] = time.time() - 5
    mid = next(d for d in demo_client.get(f"/api/v1/repositories/{rid}/documents", headers=headers).json()["documents"] if d["document_id"] == doc_id)
    assert mid["status"] == "processing"
    for item in load_documents._demo_uploads:
        item["upload_timestamp"] = time.time() - 20
    done = next(d for d in demo_client.get(f"/api/v1/repositories/{rid}/documents", headers=headers).json()["documents"] if d["document_id"] == doc_id)
    assert done["status"] == "completed"
    events = store.list_audit_events(action="document_upload", limit=5)["events"]
    assert events and events[0]["detail"]["demo"] is True


def test_demo_upload_still_enforces_type_size_and_role(demo_client, monkeypatch):
    _patch_ikm(monkeypatch, status=BAD_IKM)
    rid = load_documents.DEMO_REPOSITORY_ID
    msl = _auth(demo_client, "msl@demo.example")
    bad = demo_client.post(f"{API}/therapeutic-areas/{rid}/documents/upload", headers=msl, files={"files": ("run.exe", b"MZ", "application/octet-stream")})
    assert bad.status_code == 422
    doctor = _auth(demo_client, "doctor@demo.example")
    assert demo_client.post(f"{API}/therapeutic-areas/{rid}/documents/upload", headers=doctor, files={"files": ("a.pdf", b"%PDF", "application/pdf")}).status_code == 403


# ------------------------------------------------------------------ clean upload failure (backend bug fix)
def test_upload_failure_never_leaks_raw_upstream_text(real_client, monkeypatch):
    store.upsert_therapeutic_area(repository_id="repo-1", name="Retina", owner_msl_id="usr_msl")
    from services import dms_client

    def boom(self, **kwargs):
        raise RuntimeError("HTTPSConnectionPool(host='16.113.145.53', port=443): tlsv1 alert internal error")

    monkeypatch.setattr(dms_client.DmsClient, "upload_documents", boom)
    res = real_client.post(
        f"{API}/therapeutic-areas/repo-1/documents/upload", headers=_legacy_msl(real_client),
        files={"files": ("a.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert res.status_code == 503
    body = res.json()
    assert body["code"] == "DOCUMENT_UPLOAD_UNAVAILABLE" and body["request_id"] and res.headers["x-request-id"]
    assert "16.113" not in res.text and "tls" not in res.text.lower() and "Upload failed:" not in res.text
