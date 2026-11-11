"""Demo mode: the production guard, demo account seeding, and production reporting."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from digital_msl import store
from digital_msl.config import DemoModeInProduction, check_demo_guard, get_dmsl_settings

API = "/api/v1/digital-msl"
ROOT = Path(__file__).resolve().parent.parent
DEMO_PASSWORD = "DemoMSL#2026"


def _env(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("DIGITAL_MSL_DATABASE_URL", f"sqlite:///{tmp_path / 'demo.db'}")
    monkeypatch.setenv("DIGITAL_MSL_TRACE_DIR", str(tmp_path / "traces"))
    monkeypatch.setenv("DIGITAL_MSL_LEGACY_DEMO_USERS", "false")
    for key in ("DIGITAL_MSL_DEMO_MODE", "DIGITAL_MSL_SEED_DEMO_USERS"):
        monkeypatch.setenv(key, "false")  # explicit, so backend/.env cannot switch demo mode on under the tests
    monkeypatch.setenv("DIGITAL_MSL_ENVIRONMENT", "development")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_dmsl_settings.cache_clear()
    store._session_factory.cache_clear()


@pytest.fixture(autouse=True)
def _reset():
    yield
    get_dmsl_settings.cache_clear()
    store._session_factory.cache_clear()


def test_demo_mode_defaults_off(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    settings = get_dmsl_settings()
    assert settings.demo_enabled is False and settings.environment == "development"
    store.init_store()
    assert store.get_portal_user_by_email("doctor@demo.example") is None
    assert store.get_portal_user_by_email("admin@demo.example") is None


@pytest.mark.parametrize("flag", ["DIGITAL_MSL_DEMO_MODE", "DIGITAL_MSL_SEED_DEMO_USERS"])
def test_app_refuses_to_start_in_production_with_demo_mode(monkeypatch, tmp_path, flag):
    _env(monkeypatch, tmp_path, DIGITAL_MSL_ENVIRONMENT="production", **{flag: "true"})
    with pytest.raises(DemoModeInProduction):
        check_demo_guard()
    with pytest.raises(DemoModeInProduction):
        store.init_store()
    from app import app

    with pytest.raises(DemoModeInProduction):
        with TestClient(app):  # lifespan startup
            pass


def test_demo_mode_is_fine_outside_production(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, DIGITAL_MSL_DEMO_MODE="true", DIGITAL_MSL_ENVIRONMENT="staging")
    check_demo_guard()
    _env(monkeypatch, tmp_path, DIGITAL_MSL_ENVIRONMENT="production")
    check_demo_guard()  # demo off in production: fine


def _guard_script(monkeypatch_env: dict[str, str]) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("DIGITAL_MSL_")}
    env.update(monkeypatch_env)
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_demo_guard.py")], env=env, capture_output=True, text=True, timeout=60
    )


def test_ci_guard_script_exit_codes():
    bad = _guard_script({"DIGITAL_MSL_DEMO_MODE": "true", "DIGITAL_MSL_ENVIRONMENT": "production"})
    assert bad.returncode == 1 and "Refusing to start" in bad.stderr
    bad_alias = _guard_script({"DIGITAL_MSL_SEED_DEMO_USERS": "true", "DIGITAL_MSL_ENVIRONMENT": "production"})
    assert bad_alias.returncode == 1
    assert _guard_script({"DIGITAL_MSL_DEMO_MODE": "true", "DIGITAL_MSL_ENVIRONMENT": "development"}).returncode == 0
    assert _guard_script({"DIGITAL_MSL_DEMO_MODE": "false", "DIGITAL_MSL_ENVIRONMENT": "production"}).returncode == 0


def test_demo_accounts_seeded_in_demo_mode(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, DIGITAL_MSL_DEMO_MODE="true")
    store.init_store()
    store.upsert_therapeutic_area(repository_id="ta-live", name="Live Area", owner_msl_id="", description="d")
    store.seed_default_areas()  # disabled placeholders must not be linked
    store.seed_demo_accounts()  # idempotent re-link
    doctor = store.get_portal_user_by_email("doctor@demo.example")
    msl = store.get_portal_user_by_email("msl@demo.example")
    admin = store.get_portal_user_by_email("admin@demo.example")
    assert (doctor["display_name"], msl["display_name"], admin["display_name"]) == (
        "Demo Doctor", "Demo Physical MSL", "Demo Administrator",
    )
    assert doctor["role"] == "doctor" and msl["role"] == "msl" and admin["role"] == "admin"
    assert doctor["is_demo"] and msl["is_demo"] and admin["is_demo"]
    assert doctor["verification_status"] == "VERIFIED" and doctor["email_verified"] and doctor["mobile_verified"]
    assert doctor["consents"] == {"whatsapp_messaging": True, "email_messaging": True}
    ledger = store.get_consents(doctor["user_id"])
    assert all(ledger[t]["granted"] for t in ("terms_privacy", "hcp_declaration", "whatsapp_messaging", "email_messaging"))
    with store.session_scope() as db:
        sources = {r.source for r in db.query(store.ConsentRow).filter_by(user_id=doctor["user_id"]).all()}
        assert sources == {"demo_seed"}
        profile = db.query(store.DoctorProfileRow).filter_by(user_id=doctor["user_id"]).first()
        assert profile.verification_source == "demo_seed"
    assert "ta-live" in store.repositories_for_msl(msl["user_id"])
    # the two launch areas are switched on for the demo and assigned to the demo doctor (default first)
    launch = ["pending:retina-and-vitreous", "pending:diabetic-retinopathy-and-macular-disease"]
    pending = [r for r in store.repositories_for_msl(msl["user_id"]) if r.startswith("pending:")]
    assert sorted(pending) == sorted(launch)
    assert doctor["assigned_repository_ids"] == launch and doctor["default_repository_id"] == launch[0]
    # re-running changes nothing
    store.seed_demo_accounts()
    with store.session_scope() as db:
        assert db.query(store.PortalUserRow).filter_by(email="doctor@demo.example").count() == 1


def test_demo_password_meets_policy_and_logs_in(monkeypatch, tmp_path):
    from app import app

    assert len(DEMO_PASSWORD) == 12
    _env(monkeypatch, tmp_path, DIGITAL_MSL_DEMO_MODE="true", DIGITAL_MSL_REQUIRE_ADMIN_MFA="true")
    store.init_store()
    client = TestClient(app)
    for email in ("doctor@demo.example", "msl@demo.example"):
        res = client.post(f"{API}/portal/login", json={"email": email, "password": DEMO_PASSWORD})
        assert res.status_code == 200, res.text
        client.cookies.clear()
    # The demo admin skips the mandatory admin MFA, only because it is a demo account in demo mode.
    admin = client.post(f"{API}/portal/login", json={"email": "admin@demo.example", "password": DEMO_PASSWORD})
    assert admin.status_code == 200 and admin.json()["user"]["role"] == "admin" and "mfa_enrollment_required" not in admin.json()
    client.cookies.clear()
    # a real admin still has to enrol
    store.upsert_portal_user(email="real.admin@example.com", display_name="Real Admin", role="admin", password="Qz8!rT4#mW1xLp")
    real = client.post(f"{API}/portal/login", json={"email": "real.admin@example.com", "password": "Qz8!rT4#mW1xLp"})
    assert real.status_code == 200 and real.json().get("mfa_enrollment_required") is True


def test_demo_admin_needs_mfa_once_demo_mode_is_off(monkeypatch, tmp_path):
    from app import app

    _env(monkeypatch, tmp_path, DIGITAL_MSL_DEMO_MODE="true", DIGITAL_MSL_REQUIRE_ADMIN_MFA="true")
    store.init_store()
    _env(monkeypatch, tmp_path, DIGITAL_MSL_DEMO_MODE="false", DIGITAL_MSL_REQUIRE_ADMIN_MFA="true")
    res = TestClient(app).post(f"{API}/portal/login", json={"email": "admin@demo.example", "password": DEMO_PASSWORD})
    assert res.status_code == 200 and res.json().get("mfa_enrollment_required") is True


def test_demo_off_seeds_nothing_and_does_not_touch_real_accounts(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, DIGITAL_MSL_DEMO_MODE="true")
    store.init_store()
    _env(monkeypatch, tmp_path, DIGITAL_MSL_DEMO_MODE="false")
    with pytest.raises(RuntimeError):
        store.seed_demo_accounts()
    # a real person who happens to use a demo address is not overwritten
    _env(monkeypatch, tmp_path, DIGITAL_MSL_DEMO_MODE="true")
    with store.session_scope() as db:
        db.query(store.PortalUserRow).filter_by(email="msl@demo.example").delete()
    store.upsert_portal_user(email="msl@demo.example", display_name="Real Person", role="msl", password="Qz8!rT4#mW1xLp")
    store.seed_demo_accounts()
    assert store.get_portal_user_by_email("msl@demo.example")["display_name"] == "Real Person"
    assert not store.get_portal_user_by_email("msl@demo.example")["is_demo"]


def test_production_reports_demo_and_legacy_accounts(monkeypatch, tmp_path, caplog):
    _env(monkeypatch, tmp_path, DIGITAL_MSL_DEMO_MODE="true")  # create accounts while in demo mode
    store.init_store()
    store.upsert_portal_user(email="old@digital-msl.local", display_name="Legacy", role="msl", password="Qz8!rT4#mW1xLp")
    _env(monkeypatch, tmp_path, DIGITAL_MSL_ENVIRONMENT="production")  # demo off now
    with caplog.at_level("WARNING"):
        store.init_store()
    assert "Demo / legacy seeded accounts exist" in caplog.text
    events = store.list_audit_events(action="demo_accounts_present", limit=5)["events"]
    assert events and "old@digital-msl.local" in events[0]["detail"]["accounts"]
    assert "doctor@demo.example" in events[0]["detail"]["accounts"]
    # no warning outside production
    _env(monkeypatch, tmp_path)
    assert store.report_demo_accounts() == []


def test_seed_demo_script_refuses_without_demo_mode_or_in_production():
    script = str(ROOT / "scripts" / "seed_demo.py")
    base = {k: v for k, v in os.environ.items() if not k.startswith("DIGITAL_MSL_")}
    off = subprocess.run([sys.executable, script], env={**base, "DIGITAL_MSL_DEMO_MODE": "false", "DIGITAL_MSL_SEED_DEMO_USERS": "false"}, capture_output=True, text=True, timeout=60)
    assert off.returncode == 2
    prod = subprocess.run(
        [sys.executable, script],
        env={**base, "DIGITAL_MSL_DEMO_MODE": "true", "DIGITAL_MSL_ENVIRONMENT": "production"},
        capture_output=True, text=True, timeout=60,
    )
    assert prod.returncode == 1
