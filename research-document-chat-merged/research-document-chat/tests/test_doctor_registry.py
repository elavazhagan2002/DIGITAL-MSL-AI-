"""Doctor registration, contact verification, verification workflow, consent and the compliance gates."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from digital_msl import channels, compliance, doctor_registry, security, store
from digital_msl.config import get_dmsl_settings
from digital_msl.contracts import InboundMessage
from digital_msl.scope import classify_query

API = "/api/v1/digital-msl"
STRONG = "Vk7#mQ2!zLp9Rw"
NEUTRAL = store.NEUTRAL_DUPLICATE


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DIGITAL_MSL_DATABASE_URL", f"sqlite:///{tmp_path / 'dmsl_registry.db'}")
    monkeypatch.setenv("DIGITAL_MSL_TRACE_DIR", str(tmp_path / "traces"))
    monkeypatch.setenv("DIGITAL_MSL_SMTP_HOST", "")
    monkeypatch.setenv("DIGITAL_MSL_WHATSAPP_MOCK", "true")
    get_dmsl_settings.cache_clear()
    store._session_factory.cache_clear()
    store.init_store()
    store.upsert_therapeutic_area(repository_id="ta-retina", name="Retina", owner_msl_id="usr_msl", scope_keywords=["retina"])
    store.upsert_therapeutic_area(repository_id="ta-glc", name="Pilot Area", owner_msl_id="usr_msl_arun", scope_keywords=["pilotkw"])
    from app import app

    yield TestClient(app)
    get_dmsl_settings.cache_clear()
    store._session_factory.cache_clear()


def _payload(**over):
    body = {
        "email": "dr.rao@hospital.org",
        "display_name": "Dr. Kavya Rao",
        "password": STRONG,
        "mobile": "+919876543210",
        "registration_number": "tn-12345",
        "registration_council": "Tamil Nadu Medical Council",
        "registration_year": 2012,
        "basic_qualification": "MBBS",
        "highest_qualification": "MS Ophthalmology",
        "speciality": "Ophthalmology",
        "institution": "City Eye Hospital",
        "practice_type": "Hospital (private)",
        "city": "Chennai",
        "state": "Tamil Nadu",
        "pincode": "600001",
        "repository_ids": ["ta-retina"],
        "default_repository_id": "ta-retina",
        "consents": {
            "terms_privacy": True,
            "hcp_declaration": True,
            "whatsapp_messaging": True,
            "email_messaging": True,
        },
        "consent_version": doctor_registry.CONSENT_VERSION,
    }
    body.update(over)
    return body


def _login(client, email, password, role=None):
    body = {"email": email, "password": password}
    if role:
        body["role"] = role
    res = client.post(f"{API}/portal/login", json=body)
    client.cookies.clear()  # behave like a token client: a later login must not end an earlier session
    return res


def _token(client, email, password):
    res = _login(client, email, password)
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _register(client, **over):
    return client.post(f"{API}/portal/register", json=_payload(**over))


def _verify_contacts(client, registered):
    for channel, key in (("email", "email"), ("mobile", "mobile")):
        res = client.post(
            f"{API}/portal/register/verify",
            json={"user_id": registered["user_id"], "channel": channel, "code": registered["dev_codes"][key]},
        )
        assert res.status_code == 200, res.text
    return res.json()


def _doctor(client, n=0, verify=True, **over):
    """Register (and by default contact-verify) a doctor; returns (user_id, email)."""
    email = over.pop("email", f"dr{n}@hospital.org")
    res = _register(
        client,
        email=email,
        registration_number=over.pop("registration_number", f"TN-{1000 + n}"),
        mobile=over.pop("mobile", f"+9198765{43210 + n}"),
        **over,
    )
    assert res.status_code == 200, res.text
    reg = res.json()
    if verify:
        _verify_contacts(client, reg)
    return reg["user_id"], email


def _admin(client):
    return _token(client, "admin@digital-msl.local", "AdminPass2026!")


def _msl(client):
    return _token(client, "msl@digital-msl.local", "MslPass2026!")


# ------------------------------------------------------------------ options
def test_register_options_are_public(api_client):
    res = api_client.get(f"{API}/portal/register/options")
    assert res.status_code == 200
    body = res.json()
    by_id = {t["repository_id"]: t for t in body["therapeutic_areas"]}
    assert by_id["ta-retina"]["enabled"] is True and by_id["ta-retina"]["coming_soon"] is False
    assert {"id": "nmc", "name": "National Medical Commission (NMC)"} in body["councils"]
    assert [i["type"] for i in body["consent_documents"]["items"]] == [
        "terms_privacy", "hcp_declaration", "whatsapp_messaging", "email_messaging",
    ]
    assert all(i["required"] for i in body["consent_documents"]["items"])
    assert body["consent_documents"]["version"] == doctor_registry.CONSENT_VERSION
    assert body["compliance_notice"]["version"] == compliance.COMPLIANCE_NOTICE_VERSION
    assert "[MLR TO APPROVE]" in body["compliance_notice"]["text"]


def test_councils_come_from_the_database(api_client):
    with store.session_scope() as db:
        db.add(store.CouncilRow(council_id="test-council", name="Test Council", sort_order=99, enabled=1))
    names = [c["name"] for c in api_client.get(f"{API}/portal/register/options").json()["councils"]]
    assert "Test Council" in names
    assert _register(api_client, registration_council="Test Council").status_code == 200


# ------------------------------------------------------------------ registration and validation
def test_register_issues_no_session_and_stores_pii_encrypted(api_client):
    res = _register(api_client)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "verification_required"
    assert body["otp_expires_in"] == 300
    assert body["email_masked"].endswith("@hospital.org") and "*" in body["email_masked"]
    assert body["mobile_masked"].endswith("3210") and "9876" not in body["mobile_masked"]
    assert "access_token" not in body and "session_id" not in body
    assert "dmsl_session" not in res.cookies
    profile = store.get_doctor_profile(body["user_id"])
    assert profile["verification_status"] == doctor_registry.PENDING_CONTACT
    assert profile["registration_number"] == "TN-12345"  # normalised, upper-cased
    with store.session_scope() as db:
        row = db.query(store.DoctorProfileRow).filter_by(user_id=body["user_id"]).first()
        assert "TN-12345" not in (row.registration_number_enc or "") and row.registration_number is None
        assert row.mobile is None and "9876543210" not in row.mobile_enc
        assert row.registration_number_idx and row.mobile_idx
        user = db.query(store.PortalUserRow).filter_by(user_id=body["user_id"]).first()
        assert not user.whatsapp_phone and not user.whatsapp_phone_enc  # set only after the mobile is verified


def test_dev_codes_are_off_by_default(api_client, monkeypatch):
    monkeypatch.setenv("DIGITAL_MSL_DEV_ECHO_OTP", "false")
    get_dmsl_settings.cache_clear()
    body = _register(api_client).json()
    assert "dev_codes" not in body


@pytest.mark.parametrize(
    "override, fragment",
    [
        ({"password": "Sh0rt!pw"}, "at least 12"),
        ({"password": "alllowercase123!!"}, "uppercase"),
        ({"password": "Password1234!"}, "too common"),
        ({"mobile": "98765"}, "international format"),
        ({"registration_number": "!!"}, "Registration number"),
        ({"registration_council": "Made Up Council"}, "council"),
        ({"registration_year": 1900}, "Year of registration"),
        ({"pincode": ""}, "PIN code"),
        ({"institution": ""}, "Hospital or clinic"),
        ({"basic_qualification": ""}, "Basic qualification"),
        ({"highest_qualification": ""}, "Highest qualification"),
        ({"display_name": "A"}, "Full name"),
        ({"repository_ids": []}, "therapeutic area"),
        ({"repository_ids": ["pending:glaucoma"]}, "not available"),
        ({"consent_version": "old"}, "consent form has changed"),
    ],
)
def test_register_validation(api_client, override, fragment):
    res = _register(api_client, **override)
    assert res.status_code == 422, res.text
    assert fragment.lower() in res.json()["detail"].lower()


@pytest.mark.parametrize("missing", ["terms_privacy", "hcp_declaration", "whatsapp_messaging", "email_messaging"])
def test_every_consent_is_required(api_client, missing):
    consents = {"terms_privacy": True, "hcp_declaration": True, "whatsapp_messaging": True, "email_messaging": True}
    consents[missing] = False
    res = _register(api_client, consents=consents)
    assert res.status_code == 422, res.text


def test_disabled_area_is_rejected(api_client):
    store.seed_default_areas()
    res = _register(api_client, repository_ids=["pending:glaucoma-2"])
    assert res.status_code == 422
    store.set_therapeutic_area_active("ta-glc", False, actor_id="usr_admin")
    assert _register(api_client, repository_ids=["ta-glc"]).status_code == 422


def test_duplicates_get_one_neutral_message(api_client):
    assert _register(api_client).status_code == 200
    same_email = _register(api_client, registration_number="TN-777", mobile="+919800000001")
    same_reg = _register(api_client, email="o1@hospital.org", mobile="+919800000002")
    same_mobile = _register(api_client, email="o2@hospital.org", registration_number="TN-778")
    for res in (same_email, same_reg, same_mobile):
        assert res.status_code == 409 and res.json()["detail"] == NEUTRAL


def test_consent_records_are_complete_and_append_only(api_client):
    uid = _register(api_client).json()["user_id"]
    with store.session_scope() as db:
        rows = db.query(store.ConsentRow).filter_by(user_id=uid).all()
        assert sorted(r.consent_type for r in rows) == [
            "email_messaging", "hcp_declaration", "terms_privacy", "whatsapp_messaging",
        ]
        for r in rows:
            assert r.granted == 1 and r.version == doctor_registry.CONSENT_VERSION and r.source == "registration"
            assert len(r.text_sha256) == 64 and r.created_at is not None
        assert {r.channel for r in rows if r.consent_type == "whatsapp_messaging"} == {"whatsapp"}


# ------------------------------------------------------------------ contact verification (OTP)
def test_login_blocked_until_both_contacts_verified(api_client):
    reg = _register(api_client).json()
    wrong = _login(api_client, "dr.rao@hospital.org", "Wrong-pass-123!")
    assert wrong.status_code == 401  # nothing about contact state before the password is right
    res = _login(api_client, "dr.rao@hospital.org", STRONG)
    assert res.status_code == 403
    assert res.json()["code"] == "CONTACT_NOT_VERIFIED" and res.json()["user_id"] == reg["user_id"]

    first = api_client.post(
        f"{API}/portal/register/verify",
        json={"user_id": reg["user_id"], "channel": "email", "code": reg["dev_codes"]["email"]},
    )
    assert first.json() == {"email_verified": True, "mobile_verified": False, "status": doctor_registry.PENDING_CONTACT}
    assert _login(api_client, "dr.rao@hospital.org", STRONG).status_code == 403
    done = api_client.post(
        f"{API}/portal/register/verify",
        json={"user_id": reg["user_id"], "channel": "mobile", "code": reg["dev_codes"]["mobile"]},
    )
    assert done.json()["status"] == doctor_registry.PENDING_REGISTRATION
    assert _login(api_client, "dr.rao@hospital.org", STRONG).status_code == 200
    # the number is linked for WhatsApp only once it is proven
    assert store.get_portal_user_by_whatsapp("+919876543210")["user_id"] == reg["user_id"]


def test_otp_wrong_code_attempt_limit_and_expiry(api_client):
    reg = _register(api_client).json()
    uid = reg["user_id"]
    bad = {"user_id": uid, "channel": "email", "code": "000000" if reg["dev_codes"]["email"] != "000000" else "111111"}
    for _ in range(store.OTP_MAX_ATTEMPTS):
        assert api_client.post(f"{API}/portal/register/verify", json=bad).status_code == 400
    locked = api_client.post(
        f"{API}/portal/register/verify", json={**bad, "code": reg["dev_codes"]["email"]}
    )
    assert locked.status_code == 429  # even the right code is refused after too many attempts

    with store.session_scope() as db:  # past the resend cooldown
        from datetime import timedelta

        for otp in db.query(store.OtpRow).filter_by(user_id=uid, channel="mobile"):
            otp.created_at = store._now() - timedelta(minutes=5)
    reg2 = api_client.post(f"{API}/portal/register/resend", json={"user_id": uid, "channel": "mobile"})
    assert reg2.status_code == 200
    with store.session_scope() as db:  # expire the newest mobile code
        from datetime import timedelta

        otp = db.query(store.OtpRow).filter_by(user_id=uid, channel="mobile", consumed_at=None).first()
        otp.expires_at = store._now() - timedelta(seconds=1)
    expired = api_client.post(
        f"{API}/portal/register/verify",
        json={"user_id": uid, "channel": "mobile", "code": reg2.json()["dev_codes"]["mobile"]},
    )
    assert expired.status_code == 400


def test_otp_is_hashed_and_never_in_outbox(api_client):
    reg = _register(api_client).json()
    with store.session_scope() as db:
        hashes = [o.code_hash for o in db.query(store.OtpRow).filter_by(user_id=reg["user_id"]).all()]
        assert len(hashes) == 2 and reg["dev_codes"]["email"] not in hashes
        outbox = [o.body for o in db.query(store.OutboxRow).all()]
    assert outbox and all(reg["dev_codes"]["email"] not in (b or "") for b in outbox)
    assert all(reg["dev_codes"]["mobile"] not in (b or "") for b in outbox)


def test_otp_resend_cooldown_cap_and_neutral_unknown(api_client):
    reg = _register(api_client).json()
    body = {"user_id": reg["user_id"], "channel": "email"}
    assert api_client.post(f"{API}/portal/register/resend", json=body).status_code == 429  # cooldown
    with store.session_scope() as db:  # age the previous codes past the cooldown
        from datetime import timedelta

        for otp in db.query(store.OtpRow).filter_by(user_id=reg["user_id"], channel="email"):
            otp.created_at = store._now() - timedelta(minutes=5)
    assert api_client.post(f"{API}/portal/register/resend", json=body).status_code == 200
    with store.session_scope() as db:
        from datetime import timedelta

        for otp in db.query(store.OtpRow).filter_by(user_id=reg["user_id"], channel="email"):
            otp.created_at = store._now() - timedelta(minutes=5)
    assert api_client.post(f"{API}/portal/register/resend", json=body).status_code == 200
    with store.session_scope() as db:
        from datetime import timedelta

        for otp in db.query(store.OtpRow).filter_by(user_id=reg["user_id"], channel="email"):
            otp.created_at = store._now() - timedelta(minutes=5)
    assert api_client.post(f"{API}/portal/register/resend", json=body).status_code == 429  # hourly cap of 3
    unknown = api_client.post(f"{API}/portal/register/resend", json={"user_id": "usr_nope", "channel": "email"})
    assert unknown.status_code == 200 and unknown.json() == {"status": "ok"}


# ------------------------------------------------------------------ verification workflow and RBAC
def test_only_verified_doctors_get_clinical_answers(api_client):
    uid, email = _doctor(api_client)
    token = _token(api_client, email, STRONG)
    chat = {"message": "What is the effect of aflibercept on retinal thickness?", "repository_id": "ta-retina", "stream": False}
    blocked = api_client.post(f"{API}/channels/web/messages", headers=_auth(token), json=chat)
    assert blocked.status_code == 403 and blocked.json()["detail"].startswith(compliance.NOT_VERIFIED_PREFIX)
    assert store.doctor_verification_status(uid) == doctor_registry.PENDING_REGISTRATION

    res = api_client.post(
        f"{API}/doctors/{uid}/verification", headers=_auth(_admin(api_client)),
        json={"status": "VERIFIED", "reason": "Matched on the NMC register"},
    )
    assert res.status_code == 200 and res.json()["verification_status"] == "VERIFIED"
    assert compliance.sender_block_reason(store.get_portal_user(uid)) is None
    assert any(n["kind"] == "verification" for n in store.list_notifications(uid))


def test_verification_is_admin_only_and_needs_reason(api_client):
    uid, _ = _doctor(api_client)
    msl = _msl(api_client)
    r = api_client.post(f"{API}/doctors/{uid}/verification", headers=_auth(msl), json={"status": "VERIFIED"})
    assert r.status_code == 403
    assert store.doctor_verification_status(uid) == doctor_registry.PENDING_REGISTRATION
    admin = _auth(_admin(api_client))
    assert api_client.post(f"{API}/doctors/{uid}/verification", headers=admin, json={"status": "REJECTED"}).status_code == 422
    assert api_client.post(f"{API}/doctors/{uid}/verification", headers=admin, json={"status": "bogus"}).status_code == 422
    ok = api_client.post(f"{API}/doctors/{uid}/verification", headers=admin, json={"status": "NEEDS_INFO", "reason": "Certificate unreadable"})
    assert ok.status_code == 200 and ok.json()["verification_status"] == "NEEDS_INFO"
    evt = store.list_audit_events(action="doctor_verification", limit=5)["events"][0]
    assert evt["before"]["status"] == doctor_registry.PENDING_REGISTRATION and evt["after"]["status"] == "NEEDS_INFO"
    assert evt["reason"] == "Certificate unreadable"


def test_cannot_verify_before_contact_verification(api_client):
    reg = _register(api_client).json()
    res = api_client.post(
        f"{API}/doctors/{reg['user_id']}/verification", headers=_auth(_admin(api_client)), json={"status": "VERIFIED"}
    )
    assert res.status_code == 422


def test_doctor_cannot_reach_registered_doctors(api_client):
    _, email = _doctor(api_client)
    h = _auth(_token(api_client, email, STRONG))
    for path in ("/doctors", "/doctors/export.csv", "/doctors/usr_doctor", "/doctors/usr_doctor/certificate"):
        assert api_client.get(f"{API}{path}", headers=h).status_code == 403
    assert api_client.post(f"{API}/doctors/usr_doctor/deactivate", headers=h, json={"reason": "x"}).status_code == 403


def test_msl_is_read_only_and_scoped(api_client):
    uid, _ = _doctor(api_client)
    msl = _auth(_msl(api_client))
    listing = api_client.get(f"{API}/doctors", headers=msl).json()
    entry = next(d for d in listing["doctors"] if d["user_id"] == uid)
    assert entry["registration_number"] == "TN-1000" and entry["verification_status"] == doctor_registry.PENDING_REGISTRATION
    assert "9876" not in entry["mobile"] and entry["mobile"].endswith("3210")  # masked for the MSL
    assert entry["consents"] == {"whatsapp_messaging": True, "email_messaging": True}
    assert entry["areas"][0] == {"repository_id": "ta-retina", "name": "Retina", "is_default": True}
    detail = api_client.get(f"{API}/doctors/{uid}", headers=msl)
    assert detail.status_code == 200 and detail.json()["conversation_count"] == 0
    # read-only: every write and the certificate / export are refused
    assert api_client.post(f"{API}/doctors/{uid}/verification", headers=msl, json={"status": "VERIFIED"}).status_code == 403
    assert api_client.post(f"{API}/doctors/{uid}/deactivate", headers=msl, json={"reason": "no"}).status_code == 403
    assert api_client.get(f"{API}/doctors/{uid}/certificate", headers=msl).status_code == 403
    assert api_client.get(f"{API}/doctors/export.csv", headers=msl).status_code == 403
    # an MSL with no overlap sees nothing and cannot open the record
    store.upsert_portal_user(email="far.msl@example.com", display_name="Far MSL", role="msl", password=STRONG)
    far = _auth(_token(api_client, "far.msl@example.com", STRONG))
    assert all(d["user_id"] != uid for d in api_client.get(f"{API}/doctors", headers=far).json()["doctors"])
    assert api_client.get(f"{API}/doctors/{uid}", headers=far).status_code == 403
    admin_detail = api_client.get(f"{API}/doctors/{uid}", headers=_auth(_admin(api_client))).json()
    assert admin_detail["mobile"] == "+919876543210"


def test_registry_pagination_sort_filter_and_audit(api_client):
    ids = [_doctor(api_client, n, city="Chennai" if n % 2 == 0 else "Pune")[0] for n in range(5)]
    admin = _auth(_admin(api_client))
    page1 = api_client.get(f"{API}/doctors", headers=admin, params={"page": 1, "page_size": 2, "sort": "display_name", "order": "asc"}).json()
    page3 = api_client.get(f"{API}/doctors", headers=admin, params={"page": 3, "page_size": 2}).json()
    assert page1["total"] >= 5 and len(page1["doctors"]) == 2 and page1["page"] == 1 and page1["page_size"] == 2
    assert len(page3["doctors"]) >= 1 and page3["page"] == 3
    names = [d["display_name"] for d in page1["doctors"]]
    assert names == sorted(names, key=str.lower)
    pune = api_client.get(f"{API}/doctors", headers=admin, params={"city": "Pune", "page_size": 50}).json()
    assert pune["total"] == 2 and all(d["city"] == "Pune" for d in pune["doctors"])
    assert api_client.get(f"{API}/doctors", headers=admin, params={"status": "VERIFIED"}).json()["counts"]["verified"] >= 1
    assert api_client.get(f"{API}/doctors", headers=admin, params={"q": "TN-1003"}).json()["total"] == 1
    assert ids and api_client.get(f"{API}/doctors", headers=admin, params={"status": "nope"}).status_code == 422
    assert store.list_audit_events(action="doctor_list_view", limit=50)["total"] >= 5


def test_csv_export_admin_only_audited_and_formula_safe(api_client):
    _doctor(api_client, 0, display_name="=cmd|' /C calc'!A0")
    admin = _auth(_admin(api_client))
    res = api_client.get(f"{API}/doctors/export.csv", headers=admin)
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/csv")
    assert "TN-1000" in res.text and "'=cmd" in res.text
    assert store.list_audit_events(action="doctors_export", limit=5)["total"] == 1


def test_doctor_detail_views_are_audited(api_client):
    uid, _ = _doctor(api_client)
    api_client.get(f"{API}/doctors/{uid}", headers=_auth(_msl(api_client)))
    events = store.list_audit_events(action="doctor_view", limit=5)["events"]
    assert events and events[0]["entity_id"] == uid and events[0]["role"] == "msl"


def test_deactivate_and_reactivate(api_client):
    uid, email = _doctor(api_client)
    token = _token(api_client, email, STRONG)
    admin = _auth(_admin(api_client))
    assert api_client.post(f"{API}/doctors/{uid}/deactivate", headers=admin, json={"reason": ""}).status_code == 422
    assert api_client.post(f"{API}/doctors/{uid}/deactivate", headers=admin, json={"reason": "Left practice"}).status_code == 200
    assert api_client.get(f"{API}/portal/me", headers=_auth(token)).status_code in (401, 403)  # sessions ended
    assert _login(api_client, email, STRONG).status_code == 401
    assert api_client.post(f"{API}/doctors/{uid}/reactivate", headers=admin, json={"reason": "Returned"}).status_code == 200
    assert _login(api_client, email, STRONG).status_code == 200
    actions = [e["action"] for e in store.list_audit_events(limit=50)["events"]]
    assert "doctor_deactivate" in actions and "doctor_reactivate" in actions


# ------------------------------------------------------------------ certificate
def test_certificate_upload_encrypted_and_admin_only(api_client):
    from digital_msl import certificates

    _, email = _doctor(api_client)
    uid = store.get_portal_user_by_email(email)["user_id"]
    token = _token(api_client, email, STRONG)
    pdf = b"%PDF-1.4\n%secret-certificate-content\n" + b"x" * 200
    res = api_client.post(f"{API}/portal/certificate", headers=_auth(token), files={"file": ("cert.pdf", pdf, "application/pdf")})
    assert res.status_code == 200 and res.json()["scan_status"] in {"unscanned", "clean"}
    stored, _scan = store.get_certificate_path(uid)
    on_disk = (certificates.UPLOAD_DIR / stored.split("|", 1)[1]).read_bytes()
    assert b"secret-certificate-content" not in on_disk
    admin = _auth(_admin(api_client))
    got = api_client.get(f"{API}/doctors/{uid}/certificate", headers=admin)
    assert got.status_code == 200 and got.content == pdf
    assert store.list_audit_events(action="certificate_view", limit=3)["total"] == 1
    assert api_client.get(f"{API}/doctors/{uid}/certificate", headers=_auth(_msl(api_client))).status_code == 403
    assert api_client.get(f"{API}/doctors/{uid}", headers=_auth(_msl(api_client))).json()["certificate"]["present"] is True


def test_certificate_rejects_wrong_type_and_size(api_client):
    _, email = _doctor(api_client)
    h = _auth(_token(api_client, email, STRONG))
    bad = api_client.post(f"{API}/portal/certificate", headers=h, files={"file": ("x.pdf", b"not a pdf", "application/pdf")})
    big = api_client.post(f"{API}/portal/certificate", headers=h, files={"file": ("x.png", b"\x89PNG\r\n\x1a\n" + b"0" * (5 * 1024 * 1024 + 10), "image/png")})
    assert bad.status_code == 422 and big.status_code == 422


# ------------------------------------------------------------------ consent withdrawal, STOP, unsubscribe
def test_withdrawal_appends_and_blocks_delivery(api_client):
    uid, email = _doctor(api_client)
    h = _auth(_token(api_client, email, STRONG))
    msg = InboundMessage(channel="whatsapp", repository_id="ta-retina", message_text="hi", user_id=uid, external_sender_id="+919876543210")
    assert channels.consent_block(msg, "whatsapp") is None
    res = api_client.put(f"{API}/portal/consents", headers=h, json={"whatsapp_messaging": False})
    assert res.status_code == 200 and res.json()["whatsapp_messaging"]["granted"] is False
    blocked = channels.adapter_for("whatsapp").deliver(msg, None)  # type: ignore[arg-type]
    assert blocked["delivery_status"] == "blocked_no_consent"
    with store.session_scope() as db:
        rows = [
            (r.granted, r.source)
            for r in db.query(store.ConsentRow).filter_by(user_id=uid, consent_type="whatsapp_messaging").order_by(store.ConsentRow.id).all()
        ]
    assert rows == [(1, "registration"), (0, "settings")]  # history kept, nothing overwritten
    api_client.put(f"{API}/portal/consents", headers=h, json={"whatsapp_messaging": True})
    assert channels.consent_block(msg, "whatsapp") is None


def test_whatsapp_stop_withdraws_consent(api_client):
    uid, _ = _doctor(api_client)
    assert store.has_channel_consent(uid, "whatsapp")
    for text in ("STOP", " stop. "):
        store.set_channel_consents(uid, {"whatsapp_messaging": True})
        res = api_client.post(f"{API}/channels/whatsapp/inbound", json={"from": "+919876543210", "text": text, "id": "m1"})
        assert res.status_code == 200 and res.json()["reason_codes"] == ["CONSENT_WITHDRAWN"]
        assert not store.has_channel_consent(uid, "whatsapp")
    with store.session_scope() as db:
        last = db.query(store.ConsentRow).filter_by(user_id=uid, consent_type="whatsapp_messaging").order_by(store.ConsentRow.id.desc()).first()
        last_source = last.source
    assert last_source == "whatsapp_stop"
    assert store.has_channel_consent(uid, "email")  # only the stopped channel is affected


def test_stop_must_be_the_exact_word(api_client):
    uid, _ = _doctor(api_client)
    assert channels is not None
    from digital_msl.router import _STOP

    assert _STOP.match("STOP") and _STOP.match("stop!") and not _STOP.match("please stop the bleeding") and not _STOP.match("stopping")


def test_email_unsubscribe_link(api_client):
    uid, _ = _doctor(api_client)
    url = channels.unsubscribe_url(uid)
    token = url.split("token=", 1)[1]
    bad = api_client.get(f"{API}/portal/unsubscribe", params={"token": token + "x"})
    assert bad.status_code == 400 and store.has_channel_consent(uid, "email")
    ok = api_client.get(f"{API}/portal/unsubscribe", params={"token": token})  # public, no login
    assert ok.status_code == 200 and not store.has_channel_consent(uid, "email")
    assert store.has_channel_consent(uid, "whatsapp")
    with store.session_scope() as db:
        last = db.query(store.ConsentRow).filter_by(user_id=uid, consent_type="email_messaging").order_by(store.ConsentRow.id.desc()).first()
        last_state = (last.source, last.granted)
    assert last_state == ("email_unsubscribe", 0)


def test_consent_endpoint_is_doctor_only_and_channel_only(api_client):
    assert api_client.put(f"{API}/portal/consents", headers=_auth(_msl(api_client)), json={"email_messaging": True}).status_code == 403
    with pytest.raises(ValueError):
        store.set_channel_consents("usr_doctor", {"terms_privacy": False})


# ------------------------------------------------------------------ compliance notice and safety
def test_notice_endpoint_and_per_channel_delivery(api_client):
    uid, email = _doctor(api_client)
    notice = api_client.get(f"{API}/compliance/notice", headers=_auth(_token(api_client, email, STRONG))).json()
    assert notice["version"] == compliance.COMPLIANCE_NOTICE_VERSION and "Reply STOP" in notice["text"]
    wa = InboundMessage(channel="whatsapp", repository_id="ta-retina", message_text="q", user_id=uid, external_sender_id="+919876543210")
    reply = _fake_reply(wa)
    channels.adapter_for("whatsapp").deliver(wa, reply)
    channels.adapter_for("whatsapp").deliver(wa, reply)
    with store.session_scope() as db:
        bodies = [o.body for o in db.query(store.OutboxRow).filter_by(channel="whatsapp").all() if o.subject != "Verification code"]
    assert sum("Digital MSL provides scientific" in b for b in bodies) == 1  # first reply per channel and version only
    em = InboundMessage(channel="email", repository_id="ta-retina", message_text="q", user_id=uid, external_sender_id=email, subject="Q")
    channels.adapter_for("email").deliver(em, _fake_reply(em))
    with store.session_scope() as db:
        mail = [o.body for o in db.query(store.OutboxRow).filter_by(channel="email").all() if o.subject != "Your Digital MSL verification code"][-1]
    assert "Digital MSL provides scientific" in mail and "/portal/unsubscribe?token=" in mail


def _fake_reply(message):
    from digital_msl.contracts import AgentResponse

    return AgentResponse(
        message_id=message.message_id, correlation_id=message.correlation_id, conversation_id=message.conversation_id,
        channel=message.channel, status="answered", decision="escalate_to_msl", answer="An answer.", overall_score=1.0, threshold=0.9,
    )


def test_notice_version_is_stored_with_each_message(api_client):
    from tests.test_digital_msl import FakeLLM, _ask

    llm = FakeLLM("x", "x")
    response = _ask("hello", llm)
    with store.session_scope() as db:
        row = db.query(store.InboundMessageRow).filter_by(message_id=response.message_id).first()
        assert row.notice_version == compliance.COMPLIANCE_NOTICE_VERSION


@pytest.mark.parametrize(
    "text",
    [
        "My patient developed endophthalmitis after the second injection of aflibercept",
        "A patient experienced severe vision loss following intravitreal faricimab",
        "We received a defective syringe from batch 4471",
        "Possible medication error - wrong dose given to a patient",
    ],
)
def test_patient_adverse_events_are_detected(text):
    assert compliance.detect_adverse_event(text)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("What adverse events were reported in the VIEW 1 trial?", True),
        ("Any side effects or death reports with faricimab?", True),
        ("There was a complaint about the product", True),
        ("How does aflibercept compare with ranibizumab for visual acuity?", False),
    ],
)
def test_ae_keyword_screen(text, expected):
    assert bool(compliance.detect_ae_keywords(text)) is expected
    assert compliance.detect_adverse_event("What adverse events were reported in the VIEW 1 trial?") is None


def test_ae_keyword_adds_reminder_and_flags_once(api_client):
    from tests.test_digital_msl import FakeLLM, _ask

    llm = FakeLLM("Aflibercept improved BCVA.", "intravitreal aflibercept 2 mg every 8 weeks improved best-corrected visual acuity")
    conv = "conv_flag_test"
    first = _ask("What side effects did aflibercept show in DME?", llm, conversation_id=conv)
    assert "Pharmacovigilance reminder" in first.answer
    second = _ask("Any other adverse events with aflibercept?", llm, conversation_id=conv)
    assert "Pharmacovigilance reminder" in second.answer
    flags = [e for e in store.list_audit_events(action="safety_ae_keyword_flag", limit=10)["events"] if e["conversation_id"] == conv]
    assert len(flags) == 1  # flagged once per conversation


def test_patient_adverse_event_creates_urgent_case(api_client):
    from digital_msl.agents.query_agent import QueryAgent

    msg = InboundMessage(
        channel="web", repository_id="ta-retina", user_id="usr_doctor",
        message_text="My patient developed endophthalmitis after the injection of aflibercept",
    )
    response = asyncio.run(QueryAgent(retrieve_client=object(), use_default_llm=False).handle(msg))  # type: ignore[arg-type]
    assert response.decision == "escalate_to_msl" and response.reason_codes == ["SAFETY_ADVERSE_EVENT"]
    assert store.get_case(response.msl_case_id)["priority"] == "urgent"
    assert store.list_audit_events(action="safety_adverse_event_flagged", limit=5)["total"] == 1


def test_unregistered_senders_can_be_refused(monkeypatch):
    monkeypatch.setenv("DIGITAL_MSL_ALLOW_UNREGISTERED_SENDERS", "false")
    get_dmsl_settings.cache_clear()
    assert compliance.sender_block_reason(None) == "unregistered"
    monkeypatch.delenv("DIGITAL_MSL_ALLOW_UNREGISTERED_SENDERS")
    get_dmsl_settings.cache_clear()
    assert compliance.sender_block_reason(None) is None


# ------------------------------------------------------------------ verifier interface
def test_verifier_interface():
    manual = doctor_registry.ManualVerifier().verify({})
    assert manual.status == doctor_registry.PENDING_REGISTRATION and manual.source == "manual"
    nmr = doctor_registry.NmrVerifier().verify({})
    assert nmr.status == doctor_registry.PENDING_REGISTRATION and "not configured" in nmr.detail
    assert doctor_registry.nmr_query({"display_name": "A", "registration_number": "X", "registration_council": "C", "registration_year": 2000})["council"] == "C"
    assert doctor_registry.normalize_status("verified") == "VERIFIED" and doctor_registry.normalize_status("pending") == doctor_registry.PENDING_REGISTRATION


def test_legacy_status_rows_are_migrated(tmp_path, monkeypatch):
    from sqlalchemy import text

    monkeypatch.setenv("DIGITAL_MSL_DATABASE_URL", f"sqlite:///{tmp_path / 'legacy.db'}")
    get_dmsl_settings.cache_clear()
    store._session_factory.cache_clear()
    store.init_store()
    with store.session_scope() as db:
        db.execute(text("UPDATE dmsl_doctor_profiles SET verification_status = 'needs_info', mobile = '+919812345678', registration_number = 'KA-9'"))
    store._session_factory.cache_clear()
    store.init_store()
    assert store.doctor_verification_status("usr_doctor") == doctor_registry.NEEDS_INFO
    with store.session_scope() as db:
        row = db.query(store.DoctorProfileRow).filter_by(user_id="usr_doctor").first()
        assert row.mobile is None and security.decrypt_text(row.mobile_enc) == "+919812345678"
        assert security.decrypt_text(row.registration_number_enc) == "KA-9"


# ------------------------------------------------------------------ therapeutic areas
def test_seed_default_areas_are_local_disabled_and_idempotent(api_client):
    admin = _auth(_admin(api_client))
    first = api_client.post(f"{API}/therapeutic-areas/seed-defaults", headers=admin).json()
    assert len(first["created"]) == 10 and first["failed"] == []
    names = [a["name"] for a in first["created"]]
    assert names[0] == "Retina and Vitreous" and "Dry Eye and Ocular Surface" in names
    assert all(a["repository_id"].startswith("pending:") and a["enabled"] is False and a["coming_soon"] for a in first["created"])
    assert all(a["scope_keywords"] for a in first["created"])
    second = api_client.post(f"{API}/therapeutic-areas/seed-defaults", headers=admin).json()
    assert second["created"] == [] and len(second["existing"]) == 10
    assert api_client.post(f"{API}/therapeutic-areas/seed-defaults", headers=_auth(_msl(api_client))).status_code == 403
    listed = [a for a in api_client.get(f"{API}/therapeutic-areas", headers=admin).json()["therapeutic_areas"] if a["coming_soon"]]
    assert [a["sort_order"] for a in listed] == sorted(a["sort_order"] for a in listed)


def test_area_settings_enable_requires_a_repository_and_is_audited(api_client, monkeypatch):
    from digital_msl import router as router_module

    admin = _auth(_admin(api_client))
    api_client.post(f"{API}/therapeutic-areas/seed-defaults", headers=admin)
    bad = api_client.put(f"{API}/therapeutic-areas/glaucoma/settings", headers=admin, json={"description": "d", "enabled": False})
    assert bad.status_code == 200
    # enabling a placeholder with an explicit repository id links it
    linked = api_client.put(f"{API}/therapeutic-areas/glaucoma/settings", headers=admin, json={"enabled": True, "repository_id": "repo-glaucoma"})
    assert linked.status_code == 200 and linked.json()["repository_id"] == "repo-glaucoma" and linked.json()["enabled"] is True
    assert _register(api_client, repository_ids=["repo-glaucoma"]).status_code == 200  # now registrable
    # enabling another placeholder without an id creates the IKM repository first
    monkeypatch.setattr(router_module, "_create_area_repository", lambda name, description, keywords=None: "repo-new-cornea")
    created = api_client.put(f"{API}/therapeutic-areas/cornea-and-external-disease/settings", headers=admin, json={"enabled": True})
    assert created.status_code == 200 and created.json()["repository_id"] == "repo-new-cornea"
    evt = store.list_audit_events(action="ta_update", limit=5)["events"][0]
    assert evt["before"]["enabled"] is False and evt["after"]["enabled"] is True
    with pytest.raises(ValueError):
        store.update_area_settings("neuro-ophthalmology", actor_id="x", enabled=True)
    assert api_client.put(f"{API}/therapeutic-areas/glaucoma/settings", headers=_auth(_msl(api_client)), json={"enabled": False}).status_code == 403
    assert api_client.put(f"{API}/therapeutic-areas/nope/settings", headers=admin, json={"enabled": False}).status_code == 404


def test_other_areas_use_their_scope_keywords():
    assert classify_query("Which glaucoma drops lower IOP the most?").in_scope is False  # not in the built-in vocabulary
    assert classify_query("Which glaucoma drops lower IOP the most?", ["glaucoma"]).in_scope is True
    assert classify_query("How do checkpoint inhibitors affect overall survival?").in_scope is False
    assert classify_query("How do checkpoint inhibitors affect overall survival?", ["checkpoint inhibitor"]).in_scope is True


def test_inbound_channels_ignore_client_supplied_identity(api_client, monkeypatch):
    monkeypatch.setenv("DIGITAL_MSL_INBOUND_SECRET", "s3cret")
    get_dmsl_settings.cache_clear()
    res = api_client.post(f"{API}/channels/email/inbound", json={"from": "a@b.com", "text": "hi"})
    assert res.status_code == 403
