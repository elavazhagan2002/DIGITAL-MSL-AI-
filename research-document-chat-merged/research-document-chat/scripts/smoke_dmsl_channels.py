"""Smoke-test Digital MSL web (SSE), email inbound, and WhatsApp demo against a running backend."""
from __future__ import annotations

import json
import sys
import uuid
from urllib import error, request

BASE = "http://127.0.0.1:8087/api/v1/digital-msl"

QUESTIONS = [
    "In the 563-eye diabetic macular edema cohort, what percentage of eyes switched treatment?",
    "What was the mean change in best-corrected visual acuity at week 52 in DME trials?",
    "How often was aflibercept administered in the VIVID trial?",
    "What ocular adverse events were reported with intravitreal anti-VEGF in DME?",
    "What is faricimab's dual mechanism in retinal vascular disease?",
]


def post_json(path: str, body: dict, headers: dict | None = None, timeout: float = 300) -> tuple[int, dict | str]:
    data = json.dumps(body).encode("utf-8")
    h = {"Content-Type": "application/json", **(headers or {})}
    req = request.Request(f"{BASE}{path}", data=data, headers=h, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


def read_sse(path: str, body: dict, headers: dict, timeout: float = 300) -> dict | None:
    data = json.dumps(body).encode("utf-8")
    h = {"Content-Type": "application/json", "Accept": "text/event-stream", **headers}
    req = request.Request(f"{BASE}{path}", data=data, headers=h, method="POST")
    result = None
    with request.urlopen(req, timeout=timeout) as resp:
        buf = ""
        while True:
            chunk = resp.read(4096)
            if not chunk:
                break
            buf += chunk.decode("utf-8", errors="replace")
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                for line in block.splitlines():
                    if line.startswith("data: "):
                        payload = json.loads(line[6:])
                        if payload.get("type") == "result":
                            result = payload
                        if payload.get("type") == "error":
                            raise RuntimeError(payload.get("detail") or payload)
    return result


def login() -> tuple[str, str, str]:
    code, payload = post_json(
        "/portal/login",
        {"email": "doctor@digital-msl.local", "password": "DoctorPass2026!", "role": "doctor"},
    )
    if code != 200 or not isinstance(payload, dict):
        raise SystemExit(f"Login failed ({code}): {payload}")
    user = payload.get("user") or {}
    session_id = payload.get("session_id") or ""
    user_id = user.get("user_id") or "usr_doctor"
    repos = user.get("assigned_repository_ids") or []
    repo_id = repos[0] if repos else "5d13d75d-972c-448d-bf72-efa185bc2b15"
    return session_id, user_id, repo_id


def auth_headers(session_id: str, user_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {session_id}",
        "X-DMSL-Session-Id": session_id,
        "X-DMSL-User-Id": user_id,
    }


def main() -> int:
    session_id, user_id, repo_id = login()
    headers = auth_headers(session_id, user_id)
    print(f"Logged in as {user_id}, repo={repo_id}\n")

    web_ok = 0
    for i, q in enumerate(QUESTIONS, 1):
        print(f"[WEB {i}/5] {q[:70]}…")
        try:
            result = read_sse(
                "/channels/web/messages",
                {
                    "message": q,
                    "repository_id": repo_id,
                    "conversation_id": f"smoke_{uuid.uuid4().hex[:8]}",
                    "stream": True,
                    "conversation_history": [],
                },
                headers,
            )
        except Exception as exc:
            print(f"  FAIL: {exc}\n")
            continue
        if not result:
            print("  FAIL: no result event\n")
            continue
        decision = result.get("decision")
        claims = len(result.get("claims") or [])
        ans = (result.get("answer") or "")[:120].replace("\n", " ")
        print(f"  OK decision={decision} claims={claims} elapsed_ms={result.get('elapsed_ms')} answer={ans!r}\n")
        if decision == "respond" and claims:
            web_ok += 1

    print(f"Web: {web_ok}/5 questions returned evidence-backed answers\n")

    email_q = QUESTIONS[0]
    print(f"[EMAIL] inbound from doctor@digital-msl.local …")
    code, email_resp = post_json(
        "/channels/email/inbound",
        {
            "from": "doctor@digital-msl.local",
            "subject": "DME cohort question",
            "text": email_q,
            "message_id": f"smoke_mail_{uuid.uuid4().hex[:8]}",
        },
        timeout=300,
    )
    if isinstance(email_resp, dict):
        delivery = email_resp.get("delivery") or {}
        print(
            f"  HTTP {code} decision={email_resp.get('decision')} "
            f"delivery={delivery.get('status')} claims={len(email_resp.get('claims') or [])}"
        )
    else:
        print(f"  HTTP {code} {str(email_resp)[:200]}")

    print("\n[WHATSAPP DEMO] …")
    try:
        wa = read_sse(
            "/channels/whatsapp/demo-chat",
            {"text": QUESTIONS[1], "stream": True, "conversation_history": []},
            headers,
        )
    except Exception as exc:
        print(f"  FAIL: {exc}")
        return 1
    if wa:
        print(
            f"  OK decision={wa.get('decision')} reply_len={len(wa.get('reply_text') or wa.get('answer') or '')} "
            f"claims={len(wa.get('claims') or [])}"
        )
    else:
        print("  FAIL: no result")
        return 1

    return 0 if web_ok >= 3 else 1


if __name__ == "__main__":
    sys.exit(main())
