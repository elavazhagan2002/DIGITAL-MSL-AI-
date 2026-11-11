"""HTTP-level portal auth for Digital MSL routes."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from digital_msl import store
from digital_msl.config import get_dmsl_settings


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DIGITAL_MSL_DATABASE_URL", f"sqlite:///{tmp_path / 'dmsl_http.db'}")
    monkeypatch.setenv("DIGITAL_MSL_TRACE_DIR", str(tmp_path / "traces"))
    get_dmsl_settings.cache_clear()
    store._session_factory.cache_clear()
    store.init_store()
    from app import app

    yield TestClient(app)
    get_dmsl_settings.cache_clear()
    store._session_factory.cache_clear()


def _login(client: TestClient, role: str, email: str, password: str | None = None) -> str:
    payload = {
        "email": email,
        "display_name": "Test User",
        "role": role,
    }
    if password:
        payload["password"] = password
    res = client.post("/api/v1/digital-msl/portal/login", json=payload)
    assert res.status_code == 200, res.text
    body = res.json()
    return body["access_token"]


def test_protected_route_requires_bearer(api_client: TestClient):
    res = api_client.get("/api/v1/digital-msl/cases")
    assert res.status_code == 401


def test_doctor_cannot_read_audit_events(api_client: TestClient):
    store.seed_portal_users()
    token = _login(api_client, "doctor", "doctor@digital-msl.local", "DoctorPass2026!")
    res = api_client.get("/api/v1/digital-msl/audit/events", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403


def test_admin_can_read_audit_events(api_client: TestClient):
    store.seed_portal_users()
    token = _login(api_client, "admin", "admin@digital-msl.local", "AdminPass2026!")
    res = api_client.get("/api/v1/digital-msl/audit/events", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200


def test_logout_ends_session(api_client: TestClient):
    store.seed_portal_users()
    token = _login(api_client, "doctor", "doctor@digital-msl.local", "DoctorPass2026!")
    res = api_client.post("/api/v1/digital-msl/portal/logout", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    res2 = api_client.get("/api/v1/digital-msl/cases", headers={"Authorization": f"Bearer {token}"})
    assert res2.status_code == 403


def test_ta_deactivate_blocks_register(api_client: TestClient):
    store.seed_portal_users()
    store.upsert_therapeutic_area(repository_id="ta-off", name="Off TA", owner_msl_id="usr_msl")
    store.set_therapeutic_area_active("ta-off", False, actor_id="usr_admin", role="admin")
    with pytest.raises(ValueError, match="not available"):
        store.register_doctor(
            email="newdoc@test.local",
            display_name="New",
            password="Vk7#mQ2!zLp9Rw",
            repository_ids=["ta-off"],
        )
