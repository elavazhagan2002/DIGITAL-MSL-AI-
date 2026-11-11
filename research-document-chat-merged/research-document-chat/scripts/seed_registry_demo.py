"""Seed realistic Registered Doctors rows for screenshots and e2e runs (DEMO / TEST DATABASES ONLY).

    DIGITAL_MSL_DEMO_MODE=true DIGITAL_MSL_DATABASE_URL=sqlite:///C:/Windows/Temp/dmsl_fork1.db python scripts/seed_registry_demo.py

Creates, through the real store functions: several enabled therapeutic areas, a dozen doctors (two share a name with
different emails; some without a registration number; every verification status; consents granted and withdrawn;
one deactivated). Refuses to run unless demo mode is on and the environment is not production. Safe to re-run.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from digital_msl import doctor_registry as registry  # noqa: E402
from digital_msl import store  # noqa: E402
from digital_msl.config import check_demo_guard, get_dmsl_settings  # noqa: E402

PASSWORD = "Registry#Demo2026"
ENABLED = ["retina-and-vitreous", "diabetic-retinopathy-and-macular-disease", "glaucoma", "cornea-and-external-disease"]

# (name, email, areas (slugs, first = default), registration number or None, council index, city, status, whatsapp, email)
DOCTORS = [
    ("Dr. Anita Rao", "anita.rao@lotuseye.example", ["retina-and-vitreous", "glaucoma"], "KA/12345/2009", 12, "Bengaluru", "VERIFIED", True, True),
    ("Dr. Anita Rao", "a.rao@citysight.example", ["cornea-and-external-disease"], "MH-77821", 14, "Pune", "PENDING_REGISTRATION_VERIFICATION", True, True),
    ("Dr. Rohan Iyer", "rohan.iyer@vision-first.example", ["retina-and-vitreous", "diabetic-retinopathy-and-macular-disease", "glaucoma"], "TN-55120", 20, "Chennai", "VERIFIED", True, False),
    ("Dr. Meera Kulkarni", "meera.kulkarni@retinacare.example", ["diabetic-retinopathy-and-macular-disease"], None, 0, "", "PENDING_REGISTRATION_VERIFICATION", True, True),
    ("Dr. Sameer Khan", "sameer.khan@eyeinstitute.example", ["glaucoma"], "DL-90311", 4, "New Delhi", "NEEDS_INFO", False, True),
    ("Dr. Priya Nair", "priya.nair@aravind-style.example", ["retina-and-vitreous"], "KL-31877", 13, "Kochi", "VERIFIED", True, True),
    ("Dr. Vikram Shah", "vikram.shah@clearview.example", ["cornea-and-external-disease", "glaucoma"], "GJ-20457", 7, "Ahmedabad", "REJECTED", False, False),
    ("Dr. Kavya Menon", "kavya.menon@sightline.example", ["retina-and-vitreous"], "KA-88410", 12, "Mysuru", "VERIFIED", True, True),
    ("Dr. Arvind Pillai", "arvind.pillai@macula-clinic.example", ["diabetic-retinopathy-and-macular-disease", "retina-and-vitreous"], "KL-42010", 13, "Thiruvananthapuram", "PENDING_REGISTRATION_VERIFICATION", True, True),
    ("Dr. Lakshmi Narayanan Venkataraman", "lakshmi.narayanan.venkataraman@longname-eye-hospital.example", ["glaucoma"], "TN-61002", 20, "Coimbatore", "VERIFIED", True, True),
    ("Dr. Tanvi Desai", "tanvi.desai@visionworks.example", ["cornea-and-external-disease"], None, 0, "", "PENDING_CONTACT_VERIFICATION", True, True),
    ("Dr. Imran Qureshi", "imran.qureshi@sharpsight.example", ["retina-and-vitreous", "glaucoma"], "TS-18455", 21, "Hyderabad", "VERIFIED", False, False),
]


def main() -> int:
    check_demo_guard()
    settings = get_dmsl_settings()
    if not settings.demo_enabled or settings.is_production:
        print("Refusing: demo mode must be on and the environment must not be production.", file=sys.stderr)
        return 2
    store.init_store()
    store.seed_default_areas(actor_id="seed", role="system")
    for slug in ENABLED:
        store.set_therapeutic_area_active(f"pending:{slug}", True, actor_id="seed", role="system")
    store.seed_demo_accounts()  # re-run so the demo Physical MSL joins the areas enabled above
    # registration only accepts areas with a real repository; give the seed a stand-in, then assign the real areas
    store.upsert_therapeutic_area(repository_id="repo-seed-standin", name="Seed stand-in", owner_msl_id="usr_demo_msl")
    store.set_therapeutic_area_active("repo-seed-standin", True, actor_id="seed", role="system")
    councils = store.list_councils()
    existing = {u["email"] for u in store.list_portal_users(role="doctor")}
    now = datetime.now(timezone.utc)
    created = 0
    for number, (name, email, slugs, reg, council_idx, city, status, wa, em) in enumerate(DOCTORS, start=1):
        if email in existing:
            continue
        repos = [f"pending:{s}" for s in slugs]
        profile = None
        consents = None
        if reg:
            profile = {
                "mobile": f"+9198{number:08d}",
                "registration_number": reg,
                "registration_council": councils[council_idx % len(councils)]["name"],
                "registration_year": 2005 + number,
                "basic_qualification": "MBBS",
                "highest_qualification": "MS Ophthalmology",
                "speciality": "Ophthalmology",
                "institution": f"{city} Eye Hospital",
                "practice_type": "Hospital (private)",
                "city": city,
                "state": "India",
                "pincode": f"5600{number:02d}",
                "nmr_id": "",
            }
            consents = {t: True for t in registry.CONSENT_TYPES}
        user = store.register_doctor(
            email=email,
            display_name=name,
            password=PASSWORD,
            repository_ids=["repo-seed-standin"],
            default_repository_id="repo-seed-standin",
            profile=profile,
            consents=consents,
            consent_version=registry.CONSENT_VERSION if consents else "",
        )
        uid = user["user_id"]
        store.assign_doctor_repositories(uid, repos)
        store.set_doctor_default_ta(uid, repos[0])
        with store.session_scope() as db:
            prof = db.query(store.DoctorProfileRow).filter_by(user_id=uid).first()
            if prof is not None and status != "PENDING_CONTACT_VERIFICATION":
                prof.email_verified_at = now
                prof.mobile_verified_at = now
        if status in ("VERIFIED", "NEEDS_INFO", "REJECTED"):
            reason = {"VERIFIED": "", "NEEDS_INFO": "Registration certificate is not legible.", "REJECTED": "Number not found on the register."}[status]
            store.set_doctor_verification(uid, status, reason, actor_id="usr_demo_admin", actor_role="admin")
        elif status == "PENDING_REGISTRATION_VERIFICATION":
            store.set_doctor_verification(uid, status, "", actor_id="usr_demo_admin", actor_role="admin")
        changes = {}
        if profile is not None:
            changes = {"whatsapp_messaging": wa, "email_messaging": em}
            store.set_channel_consents(uid, changes)
        created += 1
    # Legacy rows: areas that older versions created from IKM repository names. The page must never show them.
    legacy = {}
    for rid, label in (("legacy-repo-1", "Eye_Core_Retina-222e"), ("legacy-repo-2", "General_Research_Repository"), ("legacy-repo-3", "Retina-Product-A")):
        store.upsert_therapeutic_area(repository_id=rid, name=label, owner_msl_id="usr_demo_msl")
        legacy[label] = rid
    by_email = {u["email"]: u for u in store.list_portal_users(role="doctor")}
    mix = by_email.get("imran.qureshi@sharpsight.example")
    if mix:
        store.assign_doctor_repositories(mix["user_id"], ["pending:retina-and-vitreous", "pending:glaucoma", legacy["Retina-Product-A"]])
    only_legacy = by_email.get("vikram.shah@clearview.example")
    if only_legacy:
        store.assign_doctor_repositories(only_legacy["user_id"], [legacy["Eye_Core_Retina-222e"], legacy["General_Research_Repository"]])
    print(f"Seeded {created} doctors ({len(DOCTORS)} defined).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
