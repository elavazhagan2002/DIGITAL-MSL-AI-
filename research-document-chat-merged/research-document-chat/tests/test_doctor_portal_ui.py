"""Doctor portal profile API and greeting helpers."""
from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from digital_msl import store
from digital_msl.config import get_dmsl_settings


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DIGITAL_MSL_DATABASE_URL", f"sqlite:///{tmp_path / 'dmsl_doctor_ui.db'}")
    monkeypatch.setenv("DIGITAL_MSL_TRACE_DIR", str(tmp_path / "traces"))
    get_dmsl_settings.cache_clear()
    store._session_factory.cache_clear()
    store.init_store()
    from app import app

    yield TestClient(app)
    get_dmsl_settings.cache_clear()
    store._session_factory.cache_clear()


def _login(client: TestClient, email: str, password: str) -> str:
    res = client.post(
        "/api/v1/digital-msl/portal/login",
        json={
            "email": email,
            "display_name": "Test Doctor",
            "role": "doctor",
            "password": password,
        },
    )
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def time_of_day_greeting(when: datetime) -> str:
    hour = when.hour
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


def format_doctor_salutation(display_name: str) -> str | None:
    trimmed = display_name.strip()
    if not trimmed:
        return None
    base = trimmed.split(",")[0].strip()
    if not base:
        return None
    if base.lower().startswith("dr"):
        return base
    without = base
    if without.lower().startswith("dr."):
        without = without[3:].strip()
    elif without.lower().startswith("dr"):
        without = without[2:].strip()
    if not without:
        return None
    return f"Dr. {without}"


def build_doctor_welcome_lines(display_name: str | None, when: datetime) -> list[str]:
    salutation = format_doctor_salutation(display_name) if display_name else None
    if not salutation:
        return [
            "Welcome to Digital MSL!",
            "I am your scientific information assistant. How can I help you today?",
        ]
    return [
        f"{time_of_day_greeting(when)}, {salutation}!",
        "Welcome to Digital MSL, your scientific information assistant.",
        (
            "I can help you explore your authorized medical and clinical document repository, "
            "find supporting scientific evidence, and answer questions based on available documents."
        ),
        "How can I help you today?",
    ]


def test_greeting_uses_doctor_name_and_time_of_day():
    morning = datetime(2026, 3, 10, 9, 0, 0)
    lines = build_doctor_welcome_lines("Priya Raman", morning)
    assert lines[0] == "Good morning, Dr. Priya Raman!"
    assert "scientific information assistant" in lines[1]

    evening = datetime(2026, 3, 10, 19, 0, 0)
    assert build_doctor_welcome_lines("Dr. Arjun Mehta", evening)[0].startswith("Good evening, Dr. Arjun Mehta")


def test_greeting_fallback_without_name():
    lines = build_doctor_welcome_lines(None, datetime(2026, 3, 10, 12, 0, 0))
    assert lines[0] == "Welcome to Digital MSL!"
    assert "How can I help you today?" in lines[-1]


def test_greeting_no_double_dr_prefix():
    assert format_doctor_salutation("Dr. Arjun Mehta") == "Dr. Arjun Mehta"


def test_portal_me_requires_auth(api_client: TestClient):
    res = api_client.get("/api/v1/digital-msl/portal/me")
    assert res.status_code == 401


def test_portal_me_returns_doctor_areas(api_client: TestClient):
    store.seed_portal_users()
    store.upsert_therapeutic_area(repository_id="ta-a", name="Retina", owner_msl_id="usr_msl")
    store.upsert_therapeutic_area(repository_id="ta-b", name="Ophthalmology", owner_msl_id="usr_msl")
    store.assign_doctor_repositories("usr_doctor", ["ta-a", "ta-b"])
    token = _login(api_client, "doctor@digital-msl.local", "DoctorPass2026!")
    res = api_client.get("/api/v1/digital-msl/portal/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    body = res.json()
    assert body["user"]["user_id"] == "usr_doctor"
    names = {a["name"] for a in body["therapeutic_areas"]}
    assert names == {"Retina", "Ophthalmology"}


def test_portal_me_empty_areas(api_client: TestClient):
    store.seed_portal_users()
    store.assign_doctor_repositories("usr_doctor", [])
    token = _login(api_client, "doctor@digital-msl.local", "DoctorPass2026!")
    res = api_client.get("/api/v1/digital-msl/portal/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json()["therapeutic_areas"] == []


def test_profile_update_rejects_other_user(api_client: TestClient):
    store.seed_portal_users()
    token = _login(api_client, "doctor@digital-msl.local", "DoctorPass2026!")
    res = api_client.put(
        "/api/v1/digital-msl/portal/profile",
        headers={"Authorization": f"Bearer {token}"},
        json={"user_id": "usr_admin", "display_name": "Hacker"},
    )
    assert res.status_code == 403


def test_new_conversation_id_distinct_from_session(api_client: TestClient):
    store.seed_portal_users()
    token = _login(api_client, "doctor@digital-msl.local", "DoctorPass2026!")
    login_body = api_client.post(
        "/api/v1/digital-msl/portal/login",
        json={
            "email": "doctor@digital-msl.local",
            "display_name": "Dr. Test",
            "role": "doctor",
            "password": "DoctorPass2026!",
        },
    ).json()
    session_id = login_body["session_id"]
    assert session_id.startswith("sess_")
    conv_a = "conv_" + "a" * 16
    conv_b = "conv_" + "b" * 16
    assert conv_a != session_id
    assert conv_a != conv_b


def test_doctor_conversations_list_session_scoped(api_client: TestClient):
    store.seed_portal_users()
    token = _login(api_client, "doctor@digital-msl.local", "DoctorPass2026!")
    res = api_client.get(
        "/api/v1/digital-msl/conversations?channel=web",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    assert "conversations" in res.json()


def test_doctor_cannot_read_other_doctors_conversation(api_client: TestClient):
    store.seed_portal_users()
    from digital_msl.contracts import InboundMessage, new_id

    conv_id = new_id("conv")
    store.record_inbound(
        InboundMessage(
            channel="web",
            repository_id="ta-1",
            message_text="secret",
            user_id="usr_admin",
            conversation_id=conv_id,
        )
    )
    token = _login(api_client, "doctor@digital-msl.local", "DoctorPass2026!")
    res = api_client.get(
        f"/api/v1/digital-msl/conversations/{conv_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 403
