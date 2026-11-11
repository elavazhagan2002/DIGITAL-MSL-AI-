"""Passwords, rate limiting, MFA, cookie sessions + CSRF, security headers and the audit hash chain."""
from __future__ import annotations

import hashlib

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from digital_msl import security, store
from digital_msl.config import get_dmsl_settings

API = "/api/v1/digital-msl"
STRONG = "Qz8!rT4#mW1xLp"
ADMIN = ("admin@digital-msl.local", "AdminPass2026!")
MSL = ("msl@digital-msl.local", "MslPass2026!")
DOCTOR = ("doctor@digital-msl.local", "DoctorPass2026!")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DIGITAL_MSL_DATABASE_URL", f"sqlite:///{tmp_path / 'sec.db'}")
    monkeypatch.setenv("DIGITAL_MSL_TRACE_DIR", str(tmp_path / "traces"))
    get_dmsl_settings.cache_clear()
    store._session_factory.cache_clear()
    store.init_store()
    from app import app

    yield TestClient(app, base_url="http://testserver")
    get_dmsl_settings.cache_clear()
    store._session_factory.cache_clear()


def _set(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_dmsl_settings.cache_clear()


def _login(client, creds, **extra):
    return client.post(f"{API}/portal/login", json={"email": creds[0], "password": creds[1], **extra})


# ------------------------------------------------------------------ passwords
def test_hashes_are_argon2id_and_legacy_pbkdf2_is_upgraded_on_login(client):
    assert security.hash_password(STRONG).startswith("$argon2id$")
    assert not security.verify_password("", security.hash_password(STRONG)) and not security.verify_password(STRONG, "")
    salt = "abcd" * 8
    legacy = f"pbkdf2_sha256${salt}${hashlib.pbkdf2_hmac('sha256', STRONG.encode(), salt.encode(), 100000).hex()}"
    assert security.verify_password(STRONG, legacy) and not security.verify_password("nope", legacy)
    store.upsert_portal_user(email="legacy@example.com", display_name="Legacy", role="msl")
    with store.session_scope() as db:
        db.query(store.PortalUserRow).filter_by(email="legacy@example.com").update({"password_hash": legacy})
    assert _login(client, ("legacy@example.com", STRONG)).status_code == 200
    with store.session_scope() as db:
        assert db.query(store.PortalUserRow).filter_by(email="legacy@example.com").first().password_hash.startswith("$argon2id$")


@pytest.mark.parametrize(
    "password, fragment",
    [
        ("Sh0rt!pw", "at least 12"),
        ("alllowercase123!!", "uppercase"),
        ("ALLUPPERCASE123!!", "lowercase"),
        ("NoDigitsHere!!!!", "digit"),
        ("NoSymbolsHere1234", "symbol"),
        ("Password1234!", "too common"),
        ("Welcome2026!!", "too common"),
        ("Qwerty123456!", "too common"),
    ],
)
def test_password_policy(password, fragment):
    with pytest.raises(ValueError, match=fragment):
        security.validate_password(password)
    security.validate_password(STRONG)


def test_breached_password_check_via_hibp_is_optional(monkeypatch):
    called = []
    monkeypatch.setattr(security, "_hibp_pwned", lambda pw: called.append(pw) or True)
    security.validate_password(STRONG)  # off by default: never calls out
    assert called == []
    _set(monkeypatch, DIGITAL_MSL_HIBP_CHECK="true")
    with pytest.raises(ValueError, match="data breach"):
        security.validate_password(STRONG)
    assert len(called) == 1


def test_password_change_needs_current_password_and_policy(client):
    token = _login(client, MSL).json()["access_token"]
    client.cookies.clear()
    h = {"Authorization": f"Bearer {token}"}
    uid = store.get_portal_user_by_email(MSL[0])["user_id"]
    assert client.put(f"{API}/portal/profile", headers=h, json={"user_id": uid, "password": STRONG}).status_code == 422
    weak = client.put(f"{API}/portal/profile", headers=h, json={"user_id": uid, "password": "weak", "current_password": MSL[1]})
    assert weak.status_code == 422
    ok = client.put(f"{API}/portal/profile", headers=h, json={"user_id": uid, "password": STRONG, "current_password": MSL[1]})
    assert ok.status_code == 200
    assert _login(client, (MSL[0], STRONG)).status_code == 200 and _login(client, MSL).status_code == 401


def test_login_never_creates_accounts_or_changes_role_and_locks_out(client, monkeypatch):
    assert _login(client, ("nobody@example.com", "Whatever123!x")).status_code == 401
    assert store.get_portal_user_by_email("nobody@example.com") is None
    assert _login(client, DOCTOR, role="admin").status_code == 401
    assert client.post(f"{API}/portal/login", json={"email": DOCTOR[0]}).status_code == 422
    _set(monkeypatch, DIGITAL_MSL_AUTH_RATE_LIMIT_PER_MINUTE="1000")
    for _ in range(get_dmsl_settings().max_failed_logins - 1):  # the role mismatch above counted as one failure
        assert _login(client, (DOCTOR[0], "bad")).status_code == 401
    assert _login(client, DOCTOR).status_code == 429


# ------------------------------------------------------------------ rate limiting
def test_auth_endpoints_are_rate_limited_per_route(client, monkeypatch):
    _set(monkeypatch, DIGITAL_MSL_AUTH_RATE_LIMIT_PER_MINUTE="3")
    codes = [client.post(f"{API}/portal/login", json={"email": "x@example.com", "password": "bad"}).status_code for _ in range(5)]
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]
    limited = client.post(f"{API}/portal/login", json={"email": "x@example.com", "password": "bad"})
    assert int(limited.headers["Retry-After"]) >= 1
    # another route has its own bucket
    assert client.post(f"{API}/portal/register/verify", json={"user_id": "u", "channel": "email", "code": "000000"}).status_code == 400
    for _ in range(3):
        client.post(f"{API}/portal/register/resend", json={"user_id": "u", "channel": "email"})
    assert client.post(f"{API}/portal/register/resend", json={"user_id": "u", "channel": "email"}).status_code == 429


def test_rate_limiter_window():
    security.reset_rate_limits()
    assert all(security.rate_limit("k", 3, 60, now=100.0 + i)[0] for i in range(3))
    allowed, retry = security.rate_limit("k", 3, 60, now=103.0)
    assert not allowed and retry >= 1
    assert security.rate_limit("k", 3, 60, now=170.0)[0]


# ------------------------------------------------------------------ MFA
def _enrol_admin(client):
    first = _login(client, ADMIN).json()
    assert first["mfa_enrollment_required"] is True and "access_token" not in first
    assert "dmsl_session" not in client.cookies
    setup = client.post(f"{API}/portal/mfa/setup", json={"mfa_token": first["mfa_token"]}).json()
    code = pyotp.TOTP(setup["secret"]).now()
    done = client.post(f"{API}/portal/mfa/enable", json={"mfa_token": first["mfa_token"], "code": code})
    assert done.status_code == 200, done.text
    return setup["secret"], done.json()


def test_admin_must_enrol_mfa_then_signs_in_with_codes(client, monkeypatch):
    _set(monkeypatch, DIGITAL_MSL_REQUIRE_ADMIN_MFA="true")
    secret, session = _enrol_admin(client)
    assert session["user"]["role"] == "admin" and len(session["recovery_codes"]) == 8 and session["access_token"]
    assert store.mfa_enabled(session["user"]["user_id"])
    with store.session_scope() as db:
        row = db.query(store.PortalUserRow).filter_by(email=ADMIN[0]).first()
        assert secret not in row.mfa_secret_enc  # secret encrypted at rest
        assert session["recovery_codes"][0] not in row.mfa_recovery_json  # recovery codes hashed
    client.cookies.clear()

    step = _login(client, ADMIN).json()
    assert step["mfa_required"] is True and "access_token" not in step
    bad = client.post(f"{API}/portal/login/mfa", json={"mfa_token": step["mfa_token"], "code": "000000"})
    assert bad.status_code == 401
    ok = client.post(f"{API}/portal/login/mfa", json={"mfa_token": step["mfa_token"], "code": pyotp.TOTP(secret).now()})
    assert ok.status_code == 200 and ok.json()["access_token"]
    client.cookies.clear()

    # a recovery code works once
    step2 = _login(client, ADMIN).json()
    code = session["recovery_codes"][0]
    assert client.post(f"{API}/portal/login/mfa", json={"mfa_token": step2["mfa_token"], "code": code}).status_code == 200
    client.cookies.clear()
    step3 = _login(client, ADMIN).json()
    assert client.post(f"{API}/portal/login/mfa", json={"mfa_token": step3["mfa_token"], "code": code}).status_code == 401


def test_mfa_tokens_are_single_purpose_and_short_lived(client, monkeypatch):
    _set(monkeypatch, DIGITAL_MSL_REQUIRE_ADMIN_MFA="true")
    enroll = _login(client, ADMIN).json()["mfa_token"]
    assert client.post(f"{API}/portal/login/mfa", json={"mfa_token": enroll, "code": "123456"}).status_code == 401  # enrol token != verify
    assert client.post(f"{API}/portal/mfa/setup", json={"mfa_token": enroll + "x"}).status_code == 401  # tampered
    expired = security.sign_token({"u": "usr_admin", "s": "enroll"}, "mfa", ttl_seconds=-1)
    assert client.post(f"{API}/portal/mfa/setup", json={"mfa_token": expired}).status_code == 401
    wrong_purpose = security.sign_token({"u": "usr_admin", "s": "enroll"}, "unsubscribe", ttl_seconds=60)
    assert client.post(f"{API}/portal/mfa/setup", json={"mfa_token": wrong_purpose}).status_code == 401
    secret, _ = _enrol_admin(client)
    client.cookies.clear()
    verify_token = _login(client, ADMIN).json()["mfa_token"]
    assert client.post(f"{API}/portal/mfa/setup", json={"mfa_token": verify_token}).status_code == 401  # verify token != enrol


def test_mfa_failures_lock_out(client, monkeypatch):
    _set(monkeypatch, DIGITAL_MSL_REQUIRE_ADMIN_MFA="true", DIGITAL_MSL_AUTH_RATE_LIMIT_PER_MINUTE="1000")
    secret, _ = _enrol_admin(client)
    client.cookies.clear()
    token = _login(client, ADMIN).json()["mfa_token"]
    for _ in range(get_dmsl_settings().max_failed_logins):
        assert client.post(f"{API}/portal/login/mfa", json={"mfa_token": token, "code": "000000"}).status_code == 401
    assert client.post(f"{API}/portal/login/mfa", json={"mfa_token": token, "code": pyotp.TOTP(secret).now()}).status_code == 429


def test_msl_mfa_is_optional_and_admin_cannot_disable(client, monkeypatch):
    _set(monkeypatch, DIGITAL_MSL_REQUIRE_ADMIN_MFA="true")
    session = _login(client, MSL).json()
    assert session["access_token"]  # no MFA required for a Physical MSL
    h = {"Authorization": f"Bearer {session['access_token']}"}
    client.cookies.clear()
    setup = client.post(f"{API}/portal/mfa/setup", headers=h, json={}).json()
    code = pyotp.TOTP(setup["secret"]).now()
    enabled = client.post(f"{API}/portal/mfa/enable", headers=h, json={"code": code})
    assert enabled.status_code == 200 and len(enabled.json()["recovery_codes"]) == 8
    assert _login(client, MSL).json()["mfa_required"] is True
    client.cookies.clear()
    # disable: needs password and a current code
    wrong_pw = client.post(f"{API}/portal/mfa/disable", headers=h, json={"password": "bad", "code": pyotp.TOTP(setup["secret"]).now()})
    assert wrong_pw.status_code == 401
    off = client.post(f"{API}/portal/mfa/disable", headers=h, json={"password": MSL[1], "code": pyotp.TOTP(setup["secret"]).now()})
    assert off.status_code == 200 and not store.mfa_enabled(session["user"]["user_id"])
    # an administrator can never turn it off
    _set(monkeypatch, DIGITAL_MSL_REQUIRE_ADMIN_MFA="true")
    _, admin_session = _enrol_admin(client)
    client.cookies.clear()
    ah = {"Authorization": f"Bearer {admin_session['access_token']}"}
    refused = client.post(f"{API}/portal/mfa/disable", headers=ah, json={"password": ADMIN[1], "code": "123456"})
    assert refused.status_code == 403


# ------------------------------------------------------------------ cookie sessions, CSRF, rotation
def test_cookie_session_flags_and_body(client):
    res = _login(client, MSL)
    body = res.json()
    cookies = res.headers.get_list("set-cookie")
    session_cookie = next(c for c in cookies if c.startswith("dmsl_session="))
    csrf_cookie = next(c for c in cookies if c.startswith("dmsl_csrf="))
    assert "HttpOnly" in session_cookie and "SameSite=lax" in session_cookie and "Path=/" in session_cookie
    assert "HttpOnly" not in csrf_cookie and "SameSite=lax" in csrf_cookie
    assert body["session_id"] == body["access_token"] and body["csrf_token"] == security.csrf_token(body["session_id"])
    assert body["csrf_token"] in csrf_cookie
    assert res.headers["cache-control"] == "no-store"


def test_cookie_secure_flag_follows_https(client, monkeypatch):
    plain = _login(client, MSL).headers.get_list("set-cookie")
    assert not any("Secure" in c for c in plain)
    client.cookies.clear()
    forwarded = client.post(
        f"{API}/portal/login", json={"email": MSL[0], "password": MSL[1]}, headers={"X-Forwarded-Proto": "https"}
    ).headers.get_list("set-cookie")
    assert all("Secure" in c for c in forwarded if c.startswith(("dmsl_session=", "dmsl_csrf=")))
    client.cookies.clear()
    _set(monkeypatch, DIGITAL_MSL_COOKIE_SECURE="true")
    assert "Secure" in next(c for c in _login(client, MSL).headers.get_list("set-cookie") if c.startswith("dmsl_session="))


def test_cookie_auth_needs_csrf_on_unsafe_methods_but_bearer_does_not(client):
    login = _login(client, DOCTOR).json()
    assert client.get(f"{API}/portal/me").status_code == 200  # cookie alone is enough for safe methods
    assert client.get(f"{API}/portal/me", headers={"X-CSRF-Token": "x"}).status_code == 200
    body = {"whatsapp_messaging": True}
    assert client.put(f"{API}/portal/consents", json=body).status_code == 403  # no token
    assert client.put(f"{API}/portal/consents", json=body, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    ok = client.put(f"{API}/portal/consents", json=body, headers={"X-CSRF-Token": login["csrf_token"]})
    assert ok.status_code == 200
    # another user's valid token is not accepted for this session
    other = security.csrf_token("sess_someone_else")
    forged_cookie = client.put(f"{API}/portal/consents", json=body, headers={"X-CSRF-Token": other})
    assert forged_cookie.status_code == 403
    # Authorization: Bearer is not subject to CSRF (not ambient credentials)
    plain = TestClient(client.app)
    assert plain.put(f"{API}/portal/consents", json=body, headers={"Authorization": f"Bearer {login['access_token']}"}).status_code == 200


def test_login_rotates_session_and_logout_clears_cookies(client):
    first = _login(client, MSL).json()
    second = _login(client, MSL).json()  # same browser: the cookie session is ended
    assert first["session_id"] != second["session_id"]
    h1, h2 = {"Authorization": f"Bearer {first['session_id']}"}, {"Authorization": f"Bearer {second['session_id']}"}
    assert TestClient(client.app).get(f"{API}/portal/me", headers=h1).status_code == 403
    assert TestClient(client.app).get(f"{API}/portal/me", headers=h2).status_code == 200
    out = client.post(f"{API}/portal/logout", headers={"X-CSRF-Token": second["csrf_token"]})
    assert out.status_code == 200
    cleared = out.headers.get_list("set-cookie")
    assert any(c.startswith("dmsl_session=") and ("Max-Age=0" in c or "expires" in c.lower()) for c in cleared)
    assert client.get(f"{API}/portal/me").status_code in (401, 403)


def test_logout_everywhere(client):
    sessions = []
    for _ in range(3):
        sessions.append(_login(client, MSL).json()["session_id"])
        client.cookies.clear()
    bearer = lambda sid: {"Authorization": f"Bearer {sid}"}  # noqa: E731
    res = client.post(f"{API}/portal/logout-all", headers=bearer(sessions[0]))
    assert res.status_code == 200 and res.json()["sessions_ended"] == 3
    assert all(client.get(f"{API}/portal/me", headers=bearer(s)).status_code == 403 for s in sessions)
    other = _login(client, DOCTOR).json()["session_id"]  # other users are untouched
    assert client.get(f"{API}/portal/me", headers=bearer(other)).status_code == 200


def test_idle_and_absolute_timeouts(client, monkeypatch):
    token = _login(client, MSL).json()["session_id"]
    client.cookies.clear()
    h = {"Authorization": f"Bearer {token}"}
    assert client.get(f"{API}/portal/me", headers=h).status_code == 200
    _set(monkeypatch, DIGITAL_MSL_SESSION_IDLE_MINUTES="0")
    assert client.get(f"{API}/portal/me", headers=h).status_code == 401
    _set(monkeypatch, DIGITAL_MSL_SESSION_IDLE_MINUTES="30", DIGITAL_MSL_SESSION_TTL_HOURS="0")
    assert client.get(f"{API}/portal/me", headers=h).status_code == 401


# ------------------------------------------------------------------ headers
def test_security_headers(client):
    res = client.get("/health")
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["x-frame-options"] == "DENY"
    assert res.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "frame-ancestors 'none'" in res.headers["content-security-policy"] and "default-src 'self'" in res.headers["content-security-policy"]
    assert "permissions-policy" in res.headers
    assert "strict-transport-security" not in res.headers  # only over https
    https = client.get("/health", headers={"X-Forwarded-Proto": "https"})
    assert "max-age=" in https.headers["strict-transport-security"]
    api = client.get(f"{API}/config")
    assert api.headers["cache-control"] == "no-store"
    assert "content-security-policy" not in client.get("/openapi.json").headers  # Swagger UI needs its CDN
    rejected = client.get(f"{API}/portal/me")  # even auth failures carry the headers
    assert rejected.status_code == 401 and rejected.headers["x-content-type-options"] == "nosniff"


# ------------------------------------------------------------------ audit trail
def test_audit_entries_are_attributed_and_chained(client):
    _login(client, MSL, display_name="x")
    client.cookies.clear()
    _login(client, (MSL[0], "wrong-password"))
    events = store.list_audit_events(limit=20)["events"]
    login = next(e for e in events if e["action"] == "login")
    assert login["user_id"] and login["ip_address"] == "testclient" and login["created_at"].endswith("Z")
    failed = next(e for e in events if e["action"] == "login_failed")
    assert failed["outcome"] == "denied" and failed["ip_address"] == "testclient"
    ua = client.post(f"{API}/portal/login", json={"email": DOCTOR[0], "password": DOCTOR[1]}, headers={"User-Agent": "unit-test-agent/1.0"})
    assert ua.status_code == 200
    assert store.list_audit_events(action="login", limit=1)["events"][0]["user_agent"] == "unit-test-agent/1.0"
    chain = store.verify_audit_chain()
    assert chain["ok"] is True and chain["checked"] >= 3
    entries = store.list_audit_events(limit=5)["events"]
    assert all(len(e["entry_hash"]) == 64 for e in entries)


def test_audit_log_is_append_only_and_tampering_is_detected(client):
    _login(client, MSL)
    with pytest.raises(Exception, match="append-only"):
        with store.session_scope() as db:
            db.execute(text("UPDATE dmsl_audit_events SET outcome = 'tampered'"))
    with pytest.raises(Exception, match="append-only"):
        with store.session_scope() as db:
            db.execute(text("DELETE FROM dmsl_audit_events"))
    assert store.verify_audit_chain()["ok"] is True
    # an attacker with raw DB rights who drops the trigger still cannot hide the edit
    with store.session_scope() as db:
        db.execute(text("DROP TRIGGER dmsl_audit_no_update"))
        db.execute(text("UPDATE dmsl_audit_events SET user_id = 'someone_else' WHERE id = (SELECT MIN(id) FROM dmsl_audit_events WHERE entry_hash != '')"))
    broken = store.verify_audit_chain()
    assert broken["ok"] is False and broken["first_bad_id"]


def test_verify_chain_endpoint_is_admin_only(client):
    admin = _login(client, ADMIN).json()["access_token"]
    client.cookies.clear()
    msl = _login(client, MSL).json()["access_token"]
    client.cookies.clear()
    assert client.get(f"{API}/audit/verify-chain", headers={"Authorization": f"Bearer {msl}"}).status_code == 403
    res = client.get(f"{API}/audit/verify-chain", headers={"Authorization": f"Bearer {admin}"})
    assert res.status_code == 200 and res.json()["ok"] is True


def test_config_changes_are_audited(client):
    client.cookies.clear()
    admin = _login(client, ADMIN).json()["access_token"]
    client.cookies.clear()
    msl = _login(client, MSL).json()["access_token"]
    client.cookies.clear()
    ah, mh = {"Authorization": f"Bearer {admin}"}, {"Authorization": f"Bearer {msl}"}
    policy = client.get(f"{API}/policy", headers=ah).json()
    policy["claim_min_score"] = 0.65
    assert client.put(f"{API}/policy", headers=ah, json=policy).status_code == 200
    assert client.put(f"{API}/policy", headers=mh, json=policy).status_code == 403  # admin only
    evt = store.list_audit_events(action="policy_update", limit=1)["events"][0]
    assert evt["before"]["claim_min_score"] != evt["after"]["claim_min_score"]
    made = client.post(f"{API}/templates", headers=mh, json={"kind": "msl_response_to_doctor", "name": "T", "body": "Hello"})
    assert made.status_code == 200
    assert store.list_audit_events(action="template_update", limit=1)["total"] == 1


def test_pii_is_encrypted_with_a_blind_index():
    token = security.encrypt_text("TN-12345")
    assert "TN-12345" not in token and security.decrypt_text(token) == "TN-12345"
    assert security.decrypt_text("garbage") == ""
    assert security.blind_index("TN-12345", "regno") == security.blind_index(" TN-12345 ", "regno")
    assert security.blind_index("TN-12345", "regno") != security.blind_index("TN-12345", "phone")
    assert "TN-12345" not in security.blind_index("TN-12345", "regno")
