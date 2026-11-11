"""
Digital MSL pipeline tests: scope policy, validation engine scoring, and the query agent
routing answers / escalations back to the originating channel. Runs offline with a fake
DMS retrieve client and a fake LLM.
"""
import asyncio
import json

import pytest
from starlette.exceptions import HTTPException

from digital_msl import store
from digital_msl.config import get_dmsl_settings

PASSAGE = (
    "In the VIVID and VISTA trials, intravitreal aflibercept 2 mg every 8 weeks improved "
    "best-corrected visual acuity by 10.7 letters at week 52 in patients with diabetic "
    "macular edema. Ocular adverse events were similar between groups."
)

RAW_RESULT = {
    "chunk_id": "chunk-1",
    "text": PASSAGE,
    "doc_name": "11111111-1111-1111-1111-111111111111.pdf",
    "page": 4,
    "score": 0.82,
    "citation_anchor": {
        "document_id": "doc-1",
        "page_start": 4,
        "page_end": 4,
        "start_line": 120,
        "end_line": 124,
        "parent_section": "RESULTS",
        "section_name": "0.85",
        "source_blocks": [{"index": 0, "reading_order": 41, "line_start": 120, "line_end": 124}],
    },
}


class FakeDms:
    def __init__(self, results=None):
        self.results = [RAW_RESULT] if results is None else results

    def retrieve(self, *, query, top_k=10, repository_id=None, **kwargs):
        return {"results": self.results, "blocked": False}

    def list_repository_documents(self, repository_id, *, limit=500):
        return {"documents": [{"document_id": "doc-1", "original_file_name": "VIVID_VISTA_DME.pdf"}]}


class FakeLLM:
    """Returns a scripted answer for the answer agent and a verdict for the judge."""

    is_configured = True

    def __init__(self, claim_text, quote, verdict="supported"):
        self.claim_text, self.quote, self.verdict = claim_text, quote, verdict

    async def generate_async(self, messages, temperature=0.7, max_tokens=None, model=None):
        system = messages[0]["content"]
        if "evidence auditor" in system:
            return json.dumps({"results": [{"claim_id": "C1", "verdict": self.verdict, "rationale": "test"}]})
        return json.dumps(
            {
                "answer": self.claim_text,
                "claims": [
                    {"claim_text": self.claim_text, "evidence": [{"source_id": "S1", "original_text": self.quote}]}
                ],
            }
        )


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DIGITAL_MSL_DATABASE_URL", f"sqlite:///{tmp_path / 'dmsl.db'}")
    monkeypatch.setenv("DIGITAL_MSL_TRACE_DIR", str(tmp_path / "traces"))
    monkeypatch.setenv("DIGITAL_MSL_MOLECULES", "aflibercept,ranibizumab,faricimab")
    monkeypatch.setenv("DIGITAL_MSL_SMTP_HOST", "")
    monkeypatch.setenv("DIGITAL_MSL_IMAP_HOST", "")
    monkeypatch.setenv("DIGITAL_MSL_DEFAULT_REPOSITORY_ID", "")  # backend/.env sets a real one for local use
    get_dmsl_settings.cache_clear()
    store._session_factory.cache_clear()
    from digital_msl import evidence as evidence_mod

    evidence_mod.clear_document_caches()
    yield tmp_path
    get_dmsl_settings.cache_clear()
    store._session_factory.cache_clear()


def _ask(text, llm, dms=None, channel="web", **extra):
    from digital_msl.agents.query_agent import QueryAgent
    from digital_msl.contracts import InboundMessage

    repository_id = extra.pop("repository_id", "repo-1")
    message = InboundMessage(channel=channel, repository_id=repository_id, message_text=text, **extra)
    return asyncio.run(QueryAgent(retrieve_client=dms or FakeDms(), llm=llm).handle(message))


# ---------------------------------------------------------------- scope
def test_scope_accepts_retina_and_configured_molecules():
    from digital_msl.scope import classify_query

    assert classify_query("aflibercept in diabetic macular edema").in_scope
    assert classify_query("Vabysmo durability").in_scope  # brand name of faricimab
    assert not classify_query("metformin in type 2 diabetes").in_scope
    decision = classify_query("brolucizumab dosing")
    assert not decision.in_scope and decision.unsupported_molecules == ["brolucizumab"]


def test_scope_flags_individual_patient_advice():
    from digital_msl.scope import classify_query

    assert classify_query("Should I prescribe ranibizumab for my patient with wet AMD?").safety_flags


# ----------------------------------------------------- validation engine
def test_verbatim_supported_claim_is_answered_with_citation(isolated_store):
    llm = FakeLLM(
        "Aflibercept 2 mg every 8 weeks improved best-corrected visual acuity by 10.7 letters at week 52 in diabetic macular edema.",
        "intravitreal aflibercept 2 mg every 8 weeks improved best-corrected visual acuity by 10.7 letters at week 52 in patients with diabetic macular edema.",
    )
    response = _ask("What visual acuity gain did aflibercept show in DME?", llm)

    assert response.decision == "respond" and response.status == "answered"
    assert response.overall_score >= 0.90
    evidence = response.claims[0].evidence[0]
    assert evidence.document_name == "VIVID_VISTA_DME.pdf"
    assert evidence.page_no == 4
    assert evidence.line_start == 120
    assert evidence.line_end == 124
    assert "RESULTS" in evidence.paragraph and "Paragraph 42" in evidence.paragraph
    assert "VIVID_VISTA_DME.pdf, page 4" in response.answer or "page 4" in response.answer

    trace = store.get_trace(response.trace_id)
    assert trace["pre_validation"]["validation"] is None
    assert trace["pre_validation"]["claims"][0]["validation"] is None
    retrieval = trace["pre_validation"]["retrieval"]
    assert retrieval["agent"] == "query_agent"
    assert retrieval["user_query"].startswith("What visual acuity")
    assert retrieval["chunks"][0]["document_name"] == "VIVID_VISTA_DME.pdf"
    assert retrieval["chunks"][0]["page_no"] == 4
    assert retrieval["chunks"][0]["original_text"]
    assert "Paragraph" in retrieval["chunks"][0]["paragraph"]
    assert trace["retrieval"]["chunks"][0]["chunk_id"] == "chunk-1"
    assert trace["post_validation"]["claims"][0]["validation"]["verdict"] == "supported"
    assert trace["post_validation"]["validation"]["decision"] == "respond"
    assert (isolated_store / "traces" / f"{response.trace_id}_pre_validation.json").exists()
    assert (isolated_store / "traces" / f"{response.trace_id}_post_validation.json").exists()
    assert (isolated_store / "traces" / f"{response.trace_id}_retrieval.json").exists()


def test_endpoint_label_without_result_escalates_to_msl():
    endpoint_passage = (
        "Item 1 measure: Mean Change From Baseline in Best Corrected Visual Acuity (BCVA) at Week 52. "
        "Item 2 measure: Mean Change From Baseline in Central Retinal Thickness (CRT) at Week 52."
    )
    raw = {
        **RAW_RESULT,
        "text": endpoint_passage,
        "chunk_id": "chunk-endpoint",
    }
    llm = FakeLLM(
        "Aflibercept treatment for diabetic macular edema resulted in a mean change in BCVA at Week 52.",
        endpoint_passage,
    )
    response = _ask(
        "What visual acuity gain did aflibercept show in diabetic macular edema at week 52?",
        llm,
        dms=FakeDms([raw]),
    )
    assert response.decision == "escalate_to_msl"
    assert response.msl_case_id
    assert any(
        code in response.reason_codes
        for code in ("EVIDENCE_LACKS_RESULT_VALUE", "NUMERIC_ANSWER_MISSING", "OUTCOME_LABEL_ONLY", "NO_SUPPORTED_CLAIMS")
    )

    llm = FakeLLM(
        "Aflibercept 2 mg every 8 weeks improved best-corrected visual acuity by 10.7 letters at week 52 in diabetic macular edema.",
        "intravitreal aflibercept 2 mg every 8 weeks improved best-corrected visual acuity by 10.7 letters at week 52 in patients with diabetic macular edema.",
    )
    response = _ask("Should I prescribe aflibercept for my patient with DME?", llm)
    assert response.decision == "escalate_to_msl"
    assert response.msl_case_id
    assert "SAFETY_INDIVIDUAL_ADVICE" in response.reason_codes


def test_wrong_number_is_escalated_to_msl():
    llm = FakeLLM(
        "Aflibercept improved visual acuity by 15.2 letters at week 52.",
        "intravitreal aflibercept 2 mg every 8 weeks improved best-corrected visual acuity by 10.7 letters at week 52",
    )
    response = _ask("What visual acuity gain did aflibercept show in DME?", llm)

    assert response.decision == "escalate_to_msl" and response.msl_case_id
    assert "NUMERIC_MISMATCH" in response.reason_codes
    case = store.get_case(response.msl_case_id)
    assert case["status"] == "open" and case["trace_id"] == response.trace_id


def test_fabricated_quote_and_judge_rejection_escalate():
    llm = FakeLLM(
        "Aflibercept cured diabetic macular edema in all patients.",
        "aflibercept cured diabetic macular edema in all patients",
        verdict="unsupported",
    )
    response = _ask("Does aflibercept cure DME?", llm)

    assert response.decision == "escalate_to_msl"
    assert "CLAIM_BELOW_MIN_SCORE" in response.reason_codes
    trace = store.get_trace(response.trace_id)
    signals = trace["post_validation"]["claims"][0]["validation"]["signals"]
    assert signals["quote_match"] < 0.8 and signals["llm_entailment"] == "unsupported"


def test_out_of_scope_question_is_not_answered():
    before = len(store.list_cases())
    response = _ask("What is the best statin for cholesterol?", FakeLLM("x", "x"))
    assert response.decision == "refuse"
    assert response.msl_case_id in (None, "")
    # a medical question outside the doctor's areas: fixed reply, offer a Physical MSL, no case yet
    assert response.reason_codes == ["OUT_OF_AREA"]
    assert response.message_type == "refusal" and response.actions == ["send_to_msl"]
    assert "outside your therapeutic areas" in response.answer.lower()
    assert "physical msl" in response.answer.lower()
    assert "case reference" not in response.answer.lower()
    assert len(store.list_cases()) == before
    convs = store.list_conversations(role="admin")["conversations"]
    assert any(c["status"] == "refused" for c in convs)


def test_no_evidence_escalates():
    """Nothing retrieved: never answer, open a case and send the fixed, versioned reply."""
    from digital_msl import standard_replies

    response = _ask("aflibercept in retinopathy of prematurity", FakeLLM("x", "x"), dms=FakeDms(results=[]))
    assert response.decision == "escalate_to_msl" and response.msl_case_id
    assert "NO_PASSAGES" in response.reason_codes
    assert standard_replies.NOT_FOUND in response.answer


# ------------------------------------------------------------- channels
def test_email_reply_routes_back_to_email_outbox():
    llm = FakeLLM(
        "Ocular adverse events were similar between groups.",
        "Ocular adverse events were similar between groups.",
    )
    response = _ask(
        "Were ocular adverse events with aflibercept different between groups?",
        llm,
        channel="email",
        external_sender_id="dr.rao@example.com",
        subject="Aflibercept safety",
    )
    assert response.channel == "email"
    assert response.delivery["channel"] == "email"
    outbox = store.list_outbox(channel="email")
    assert outbox[0]["recipient"] == "dr.rao@example.com"
    assert outbox[0]["subject"] == "Response to Your Medical Information Request – Aflibercept safety"
    assert "Dear Dr. Rao" in outbox[0]["body"]
    assert "Response" in outbox[0]["body"]
    assert "Reference" in outbox[0]["body"]
    assert "Supporting Scientific Evidence" not in outbox[0]["body"]
    assert "Please let us know if you require any further scientific information." in outbox[0]["body"]


def test_email_reply_attaches_cited_document(monkeypatch):
    from email.message import EmailMessage

    from digital_msl.config import get_dmsl_settings

    monkeypatch.setenv("DIGITAL_MSL_SMTP_HOST", "smtp.test")
    monkeypatch.setenv("DIGITAL_MSL_SMTP_PORT", "587")
    monkeypatch.setenv("DIGITAL_MSL_SMTP_USER", "msl@test")
    monkeypatch.setenv("DIGITAL_MSL_SMTP_FROM", "msl@test")
    get_dmsl_settings.cache_clear()
    sent: dict = {}

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self):
            return None

        def login(self, *args):
            return None

        def send_message(self, message: EmailMessage):
            sent["message"] = message

    monkeypatch.setattr("digital_msl.channels.smtplib.SMTP", FakeSMTP)
    monkeypatch.setattr(
        "digital_msl.attachments.load_citation_attachment",
        lambda client, document_id, name: ("VIVID_VISTA_DME.pdf", b"%PDF-1.4\n" + b"x" * 200, "application/pdf"),
    )
    response = _ask(
        "Were ocular adverse events with aflibercept different between groups?",
        FakeLLM(
            "Ocular adverse events were similar between groups.",
            "Ocular adverse events were similar between groups.",
        ),
        channel="email",
        external_sender_id="dr.rao@example.com",
        subject="Aflibercept safety",
    )
    assert response.delivery["delivery_status"] == "sent"
    attached = list(sent["message"].iter_attachments())
    assert attached[0].get_filename() == "VIVID_VISTA_DME.pdf"
    assert "attachments=VIVID_VISTA_DME.pdf" in response.delivery["detail"]


def test_unregistered_sender_does_not_need_a_repository_name():
    from digital_msl.channels import email_repository_candidates

    store.upsert_therapeutic_area(repository_id="repo-retina", name="Eye_Core_Retina-222e", owner_msl_id="usr_msl")
    store.upsert_therapeutic_area(repository_id="repo-other", name="Oncology_Core", owner_msl_id="usr_msl")
    candidates, user_id = email_repository_candidates(
        "santhosh.s@rudhrainfosolutions.com",
        "Question about diabetic macular edema",
        "What percentage of eyes switched treatment?",
    )
    assert user_id == "santhosh.s@rudhrainfosolutions.com"
    assert set(candidates) == {"repo-retina", "repo-other"}


def test_mailbox_parser_keeps_new_question_only():
    from digital_msl.mailbox import parse_rfc822

    raw = (
        b"From: Dr Arjun Mehta <doctor@digital-msl.local>\r\n"
        b"Subject: DME switching\r\n"
        b"Message-ID: <abc@local>\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"What percentage of eyes switched treatment?\r\n"
        b"\r\n"
        b"On Mon, someone wrote:\r\n"
        b"> older text\r\n"
    )
    parsed = parse_rfc822(raw)
    assert parsed["message_id"] == "<abc@local>"
    assert "percentage of eyes" in parsed["text"]
    assert parsed["subject"] == "DME switching"


def test_whatsapp_off_topic_refuses_without_case():
    before = len(store.list_cases())
    response = _ask("What is the best statin?", FakeLLM("x", "x"), channel="whatsapp", external_sender_id="+919800000000")
    assert response.decision == "refuse"
    assert response.delivery["channel"] == "whatsapp"
    assert not response.msl_case_id
    body = store.list_outbox(channel="whatsapp")[0]["body"]
    assert "therapeutic area" in body.lower()
    assert len(store.list_cases()) == before


def test_missing_citation_metadata_escalates_without_drafting():
    class CountingLLM(FakeLLM):
        def __init__(self):
            super().__init__("should not be used", "should not be used")
            self.answer_calls = 0

        async def generate_async(self, messages, temperature=0.7, max_tokens=None, model=None):
            if "evidence auditor" not in messages[0]["content"]:
                self.answer_calls += 1
            return await super().generate_async(messages, temperature, max_tokens, model)

    llm = CountingLLM()
    unnamed = {
        "chunk_id": "chunk-bad",
        "text": PASSAGE,
        "doc_name": "",
        "page": 0,
        "citation_anchor": {},
    }
    dms = FakeDms(results=[unnamed])
    dms.list_repository_documents = lambda repository_id, *, limit=500: {"documents": []}
    response = _ask("What BCVA gain did aflibercept show in DME?", llm, dms=dms)

    assert llm.answer_calls == 0
    assert response.decision == "escalate_to_msl"
    assert "CITATION_METADATA_MISSING" in response.reason_codes
    trace = store.get_trace(response.trace_id)
    assert trace["pre_validation"]["generation_error"] == "citation_metadata_missing"
    assert trace["pre_validation"]["claims"] == []
    assert trace["pre_validation"]["validation"] is None
    assert trace["post_validation"]["validation"]["decision"] == "escalate_to_msl"
    assert trace["retrieval"]["chunks"]


def test_ikm_client_does_not_import_host_services():
    import inspect

    from digital_msl import ikm_client
    from digital_msl.ikm_client import IkmClient

    source = inspect.getsource(ikm_client)
    assert "from services" not in source
    assert "import services" not in source
    assert "from core.config" not in source
    assert "import core.config" not in source
    assert callable(IkmClient.retrieve)
    assert callable(IkmClient.list_repository_documents)


def test_duplicate_message_is_processed_once():
    llm = FakeLLM("x", "x")
    first = _ask("What is the best statin?", llm, channel="whatsapp", idempotency_key="wamid.1")
    second = _ask("What is the best statin?", llm, channel="whatsapp", idempotency_key="wamid.1")
    assert first.decision == second.decision == "refuse"
    assert first.message_id == second.message_id
    assert len(store.list_cases()) == 0


def test_seed_portal_users_fills_missing_demo_accounts():
    store.upsert_portal_user(email="someone@example.com", display_name="Temp", role="admin")
    store.seed_portal_users()
    emails = {u["email"] for u in store.list_portal_users()}
    assert "admin@digital-msl.local" in emails
    assert "doctor@digital-msl.local" in emails
    assert "msl@digital-msl.local" in emails


def test_portal_roles_and_doctor_msl_assignment():
    store.seed_portal_users()
    users = {u["email"]: u for u in store.list_portal_users()}
    assert users["admin@digital-msl.local"]["role"] == "admin"
    assert users["doctor@digital-msl.local"]["assigned_msl_id"] == "usr_msl"
    msl = store.upsert_portal_user(email="msl2@example.com", display_name="Second MSL", role="msl")
    doctor = store.assign_doctor_msl("usr_doctor", msl["user_id"])
    assert doctor["assigned_msl_id"] == msl["user_id"]
    cases = store.list_cases(assigned_msl_id="usr_msl")
    assert isinstance(cases, list)


def test_conversations_persistence_and_detail():
    store.seed_portal_users()
    llm = FakeLLM("Aflibercept is effective.", PASSAGE)
    resp = _ask("How effective is aflibercept in DME?", llm, user_id="usr_doctor", conversation_id="conv_test_101")
    assert resp.conversation_id == "conv_test_101"

    convs = store.list_conversations(role="admin")
    assert convs["total"] >= 1
    found = next((c for c in convs["conversations"] if c["conversation_id"] == "conv_test_101"), None)
    assert found is not None
    assert found["doctor_id"] == "usr_doctor"
    assert found["message_count"] == 1

    detail = store.get_conversation_detail("conv_test_101", user_id="usr_doctor", role="doctor")
    assert detail is not None
    assert len(detail["messages"]) == 1
    assert detail["messages"][0]["question"] == "How effective is aflibercept in DME?"
    assert "Aflibercept is effective" in detail["messages"][0]["response"]["answer"]
    assert len(detail["messages"][0]["response"]["claims"]) >= 1


def test_doctor_conversation_rbac_isolation():
    store.seed_portal_users()
    llm = FakeLLM("Faricimab is approved.", PASSAGE)
    _ask("Tell me about faricimab.", llm, user_id="usr_doctor", conversation_id="conv_doctor_priv")

    # Doctor trying to access another doctor's conversation raises PermissionError
    with pytest.raises(PermissionError):
        store.get_conversation_detail("conv_doctor_priv", user_id="usr_other_doc", role="doctor")

    # Admin can access it
    admin_view = store.get_conversation_detail("conv_doctor_priv", role="admin")
    assert admin_view is not None


def test_msl_response_drafting_and_approval_workflow():
    store.seed_portal_users()
    llm = FakeLLM("x", "x")
    # In-scope individual-advice questions still open an MSL case
    resp = _ask(
        "Should I prescribe aflibercept for my patient with DME?",
        llm,
        user_id="usr_doctor",
        conversation_id="conv_esc_1",
    )
    assert resp.decision == "escalate_to_msl"
    case_id = resp.msl_case_id
    assert case_id is not None

    # MSL drafts response with supporting references and claims
    draft = store.save_case_draft(
        case_id,
        author_id="usr_msl",
        draft_response="In response to your query regarding oncology crossover...",
        references=[{"title": "Retina-Oncology Review", "source": "PubMed", "is_external": True}],
        claims=[{"text": "Retina-Oncology Review...", "type": "commentary"}],
    )
    assert draft["draft_status"] == "draft"
    assert draft["draft_response"] == "In response to your query regarding oncology crossover..."

    # Submit for review
    submitted = store.submit_case_review(case_id, author_id="usr_msl", notes="Ready for review")
    assert submitted["draft_status"] == "submitted"
    assert submitted["approval_status"] == "in_review"

    # Cannot send before approved
    with pytest.raises(ValueError):
        store.send_case_response(case_id, sender_id="usr_msl")

    # Approve draft
    approved = store.approve_case_draft(case_id, reviewer_id="usr_admin", notes="Scientific review passed")
    assert approved["draft_status"] == "approved"
    assert approved["approval_status"] == "approved"

    # Send response
    sent = store.send_case_response(case_id, sender_id="usr_msl")
    assert sent["draft_status"] == "sent"
    assert sent["status"] == "resolved"
    assert sent["resolution"] == "In response to your query regarding oncology crossover..."


def test_policy_molecules_affect_classify_query(isolated_store):
    from digital_msl.scope import classify_query

    assert not classify_query("brolucizumab dosing").in_scope
    policy = store.get_policy()
    policy["molecules"].append({"name": "brolucizumab", "aliases": "brolucizumab, beovu"})
    store.save_policy(policy)
    decision = classify_query("brolucizumab dosing")
    assert decision.in_scope and "brolucizumab" in decision.molecules


def test_escalation_uses_ack_template(isolated_store):
    store.upsert_template(
        template_id="tpl_doctor_escalation_ack",
        kind="doctor_escalation_ack",
        name="Ack",
        body="ACK {{case_id}} :: {{reason}}",
        channel="all",
    )
    store.upsert_template(
        template_id="tpl_msl_new_case",
        kind="msl_new_case",
        name="Notice",
        body="QUEUE {{doctor_name}} {{case_id}} {{question}}",
        channel="all",
    )
    response = _ask("Should I prescribe aflibercept for my patient with DME?", FakeLLM("x", "x"))
    assert response.decision == "escalate_to_msl"
    assert response.answer.startswith("ACK ")
    assert response.msl_case_id in response.answer
    case = store.get_case(response.msl_case_id)
    assert "QUEUE" in (case.get("queue_notice") or "")
    assert case["case_id"] in case["queue_notice"]


def test_template_crud_and_render(isolated_store):
    created = store.upsert_template(
        kind="msl_response_to_doctor",
        name="Alt reply",
        body="Hi {{doctor_name}} — {{case_id}}",
        channel="web",
    )
    listed = store.list_templates(kind="msl_response_to_doctor")
    assert any(t["template_id"] == created["template_id"] for t in listed)
    rendered = store.render_template(
        "msl_response_to_doctor",
        {"doctor_name": "Dr X", "question": "q", "draft_body": "", "msl_name": "M", "case_id": "c1"},
        channel="web",
    )
    assert rendered == "Hi Dr X — c1"
    assert store.delete_template(created["template_id"]) is True


def test_msl_group_routing_and_notifications(isolated_store):
    store.init_store()
    msl2 = store.upsert_portal_user(email="msl2@example.com", display_name="Second MSL", role="msl")
    groups = store.list_groups()
    assert groups
    gid = groups[0]["group_id"]
    store.set_group_members(gid, ["usr_msl", msl2["user_id"]])
    store.assign_doctor_group("usr_doctor", gid)
    response = _ask(
        "Should I prescribe aflibercept for my patient with DME?",
        FakeLLM("x", "x"),
        user_id="usr_doctor",
    )
    assert response.decision == "escalate_to_msl" and response.msl_case_id
    visible = store.list_cases(viewer_msl_id=msl2["user_id"])
    assert not any(c["case_id"] == response.msl_case_id for c in visible)
    store.upsert_therapeutic_area(repository_id="repo-1", name="Retina repo", owner_msl_id=msl2["user_id"])
    visible_ta = store.list_cases(viewer_msl_id=msl2["user_id"])
    assert any(c["case_id"] == response.msl_case_id for c in visible_ta)
    notes = store.list_notifications(msl2["user_id"])
    assert any(n.get("case_id") == response.msl_case_id for n in notes)


def test_open_cases_backfill_msl_notifications():
    store.init_store()
    resp = _ask(
        "Should I prescribe aflibercept for my patient with DME?",
        FakeLLM("x", "x"),
        user_id="usr_doctor",
    )
    assert resp.msl_case_id
    with store.session_scope() as db:
        db.query(store.NotificationRow).delete()
    notes = store.list_notifications("usr_msl")
    assert any(n.get("case_id") == resp.msl_case_id and not n["read"] for n in notes)


def test_doctor_assigned_libraries_gate_queries():
    store.seed_portal_users()
    doctor = store.assign_doctor_repositories("usr_doctor", ["repo-allowed"])
    assert doctor["assigned_repository_ids"] == ["repo-allowed"]
    llm = FakeLLM("x", "x")
    blocked = _ask(
        "What visual acuity gain did aflibercept show in DME?",
        llm,
        user_id="usr_doctor",
        repository_id="repo-other",
    )
    assert blocked.decision == "refuse"
    assert "REPOSITORY_NOT_ASSIGNED" in blocked.reason_codes
    assert "thank you" in blocked.answer.lower()
    allowed = _ask(
        "What visual acuity gain did aflibercept show in DME?",
        FakeLLM(
            "Aflibercept 2 mg every 8 weeks improved best-corrected visual acuity by 10.7 letters at week 52 in diabetic macular edema.",
            "intravitreal aflibercept 2 mg every 8 weeks improved best-corrected visual acuity by 10.7 letters at week 52 in patients with diabetic macular edema.",
        ),
        user_id="usr_doctor",
        repository_id="repo-allowed",
    )
    assert allowed.decision == "respond"


def test_bibliography_quote_is_replaced_with_body_paragraph():
    from digital_msl.agents.answer_agent import build_claims
    from digital_msl.contracts import EvidenceChunk
    from digital_msl.evidence import is_bibliography_passage, supporting_paragraph, usable_chunks

    references = (
        "Consent for publication Not applicable. Competing interests The authors declare no competing interests. "
        "References Yau JWY, Rogers SL, Kawasaki R, Lamoureux EL, Kowalski JW, Bek T, et al. "
        "Global prevalence and major risk factors of diabetic retinopathy. Diabetes Care. 2012;35:556-64. "
        "Wells JA, Glassman AR, Ayala AR, Jampol LM, Aiello LP, Antoszyk AN, et al. "
        "Aflibercept, bevacizumab, or ranibizumab for diabetic macular edema. N Engl J Med. 2015;372:1193-203. "
        "In conclusion, in real-world clinical practice, anti-VEGF therapy for diabetic macular edema was associated "
        "with meaningful visual improvement despite a moderate injection frequency."
    )
    body = (
        "DRCR.net Protocol T compared aflibercept, bevacizumab, and ranibizumab for diabetic macular edema "
        "in a randomized trial of visual outcomes. The establishment of multicenter collaborative networks, "
        "such as the DRCR.net series, increased enrollment capacity."
    )
    assert is_bibliography_passage(references)
    excerpt = supporting_paragraph(references, "DRCR.net Protocol T compared aflibercept, bevacizumab, and ranibizumab")
    assert "Wells JA" not in excerpt
    assert "real-world clinical practice" in excerpt

    ref_chunk = EvidenceChunk(
        chunk_id="ref-1",
        document_id="doc-ref",
        document_name="real_world.pdf",
        document_content=references,
        page_no=8,
        paragraph="Section: References | Paragraph 81",
    )
    body_chunk = EvidenceChunk(
        chunk_id="body-1",
        document_id="doc-body",
        document_name="methods.pdf",
        document_content=body,
        page_no=11,
        paragraph="Section: 4 Discussion | Paragraph 363",
        line_start=363,
        line_end=366,
    )
    kept = usable_chunks([ref_chunk, body_chunk])
    assert [chunk.chunk_id for chunk in kept] == ["body-1"]

    claims = build_claims(
        {
            "claims": [
                {
                    "claim_text": "DRCR.net Protocol T compared aflibercept, bevacizumab, and ranibizumab for diabetic macular edema.",
                    "evidence": [
                        {
                            "source_id": "S1",
                            "original_text": "Wells JA, Glassman AR, Ayala AR, Jampol LM, Aiello LP, Antoszyk AN, et al. "
                            "Aflibercept, bevacizumab, or ranibizumab for diabetic macular edema. N Engl J Med. 2015;372:1193-203.",
                        }
                    ],
                }
            ]
        },
        [ref_chunk, body_chunk],
        8,
    )
    evidence = claims[0].evidence[0]
    assert evidence.document_name == "methods.pdf"
    assert evidence.page_no == 11
    assert "Wells JA" not in evidence.original_text
    assert "Protocol T compared aflibercept" in evidence.original_text


class RecordingDms:
    def __init__(self, hybrid=None, vector=None):
        self.calls = []
        self.list_calls = 0
        self.hybrid = [RAW_RESULT] if hybrid is None else hybrid
        self.vector = [RAW_RESULT] if vector is None else vector

    def retrieve(self, *, query, top_k=10, repository_id=None, **kwargs):
        mode = kwargs.get("search_mode") or "hybrid"
        self.calls.append(mode)
        results = self.hybrid if mode != "vector" else self.vector
        return {"results": results, "blocked": False}

    def list_repository_documents(self, repository_id, *, limit=500):
        self.list_calls += 1
        return {"documents": [{"document_id": "doc-1", "original_file_name": "VIVID_VISTA_DME.pdf"}]}


NAMED_RESULT = {
    **RAW_RESULT,
    "doc_name": "Protocol_T.pdf",
    "document_name": "Protocol_T.pdf",
}


def test_hybrid_hits_skip_vector_fallback():
    from digital_msl.evidence import retrieve_evidence

    client = RecordingDms()
    outcome = retrieve_evidence(client, query="aflibercept DME", repository_id="repo-hybrid-hit", top_k=6)
    assert client.calls == ["hybrid"]
    assert outcome.search_mode == "hybrid"
    assert outcome.chunks


def test_empty_hybrid_then_vector():
    from digital_msl.evidence import retrieve_evidence

    client = RecordingDms(hybrid=[], vector=[RAW_RESULT])
    outcome = retrieve_evidence(client, query="aflibercept DME", repository_id="repo-hybrid-miss", top_k=6)
    assert client.calls == ["hybrid", "vector"]
    assert outcome.search_mode == "vector"
    assert outcome.chunks


def test_named_retrieve_hits_use_one_cached_document_list():
    """The document list is read once per repository (approval metadata), then served from cache."""
    from digital_msl import evidence

    evidence.clear_document_caches()
    client = RecordingDms(hybrid=[NAMED_RESULT], vector=[])
    outcome = evidence.retrieve_evidence(client, query="aflibercept DME", repository_id="repo-named", top_k=6)
    assert outcome.chunks
    assert client.list_calls == 1
    evidence.retrieve_evidence(client, query="aflibercept DME again", repository_id="repo-named", top_k=6)
    assert client.list_calls == 1
    assert outcome.chunks[0].document_name == "Protocol_T.pdf"


def test_therapeutic_area_owner_is_in_msl_coverage():
    store.seed_portal_users()
    extra = store.upsert_portal_user(email="msl2@example.com", display_name="Second MSL", role="msl")
    store.upsert_therapeutic_area(repository_id="ta-dme", name="DME", owner_msl_id="usr_msl")
    store.set_ta_members("ta-dme", [extra["user_id"]], owner_msl_id="usr_msl")
    ids = store.msls_for_repository("ta-dme")
    assert "usr_msl" in ids
    assert extra["user_id"] in ids


def test_register_doctor_max_three_therapeutic_areas():
    store.seed_portal_users()
    store.upsert_therapeutic_area(repository_id="ta-1", name="One", owner_msl_id="usr_msl")
    store.upsert_therapeutic_area(repository_id="ta-2", name="Two", owner_msl_id="usr_msl")
    extra = store.upsert_portal_user(email="msl3@example.com", display_name="Third MSL", role="msl")
    store.upsert_therapeutic_area(repository_id="ta-3", name="Three", owner_msl_id=extra["user_id"])
    store.upsert_therapeutic_area(repository_id="ta-4", name="Four", owner_msl_id="usr_msl")
    with pytest.raises(ValueError):
        store.register_doctor(
            email="newdoc@example.com",
            display_name="New Doctor",
            password="Vk7#mQ2!zLp9Rw",
            repository_ids=[],
        )
    doctor = store.register_doctor(
        email="newdoc@example.com",
        display_name="New Doctor",
        password="Vk7#mQ2!zLp9Rw",
        repository_ids=["ta-1", "ta-2", "ta-3", "ta-4"],
        default_repository_id="ta-2",
    )
    assert doctor["assigned_repository_ids"] == ["ta-1", "ta-2", "ta-3"]
    assert doctor["default_repository_id"] == "ta-2"
    assert doctor["assigned_msl_id"] == "usr_msl"


def test_login_session_distinct_from_conversation_id():
    store.seed_portal_users()
    session_id = store.create_login_session("usr_doctor", "doctor")
    assert session_id.startswith("sess_")
    events = store.list_audit_events(action="login", limit=5)
    assert any(e.get("session_id") == session_id for e in events["events"])
    llm = FakeLLM(
        "Aflibercept 2 mg every 8 weeks improved best-corrected visual acuity by 10.7 letters at week 52 in diabetic macular edema.",
        "intravitreal aflibercept 2 mg every 8 weeks improved best-corrected visual acuity by 10.7 letters at week 52 in patients with diabetic macular edema.",
    )
    response = _ask(
        "What visual acuity gain did aflibercept show in DME?",
        llm,
        user_id="usr_doctor",
        session_id=session_id,
    )
    assert response.session_id == session_id
    assert response.conversation_id != session_id
    assert response.conversation_id.startswith("conv_")
    assert isinstance(response.timing, dict)
    assert response.timing.get("total_ms", 0) >= 0


def test_folder_tree_and_document_folder():
    store.seed_portal_users()
    store.upsert_therapeutic_area(repository_id="repo-tree", name="Tree TA", owner_msl_id="usr_msl")
    root = store.add_ta_folder("repo-tree", "Clinical")
    child = store.add_ta_folder("repo-tree", "Trials", parent_folder_id=root["folder_id"])
    areas = store.list_therapeutic_areas()
    match = next(a for a in areas if a["repository_id"] == "repo-tree")
    folders = match["folders"]
    assert any(f["folder_id"] == child["folder_id"] and f.get("parent_folder_id") == root["folder_id"] for f in folders)
    store.set_document_folder("doc-a", "repo-tree", child["folder_id"])
    mapping = store.document_folder_map("repo-tree")
    assert mapping.get("doc-a") == child["folder_id"]


def test_doctor_cannot_access_unassigned_repo():
    from digital_msl import auth

    store.seed_portal_users()
    with pytest.raises(HTTPException) as exc:
        auth.assert_repository_access("usr_doctor", "doctor", "repo-unassigned-xyz")
    assert exc.value.status_code == 403


def test_conversation_answered_filter_maps_to_resolved():
    store.seed_portal_users()
    llm = FakeLLM(
        "Aflibercept 2 mg every 8 weeks improved best-corrected visual acuity by 10.7 letters at week 52 in diabetic macular edema.",
        "intravitreal aflibercept 2 mg every 8 weeks improved best-corrected visual acuity by 10.7 letters at week 52 in patients with diabetic macular edema.",
    )
    response = _ask(
        "What visual acuity gain did aflibercept show in DME?",
        llm,
        user_id="usr_doctor",
        conversation_id="conv_resolved_1",
    )
    assert response.status == "answered"
    listed = store.list_conversations(role="admin", status="answered")
    found = next((c for c in listed["conversations"] if c["conversation_id"] == "conv_resolved_1"), None)
    assert found is not None
    assert found["status"] == "resolved"


def test_escalation_notifies_all_therapeutic_area_msls():
    store.seed_portal_users()
    extra = store.upsert_portal_user(email="msl-notify@example.com", display_name="Notify MSL", role="msl")
    store.upsert_therapeutic_area(repository_id="ta-notify", name="Notify", owner_msl_id="usr_msl")
    store.set_ta_members("ta-notify", ["usr_msl", extra["user_id"]], owner_msl_id="usr_msl")
    llm = FakeLLM(
        "Aflibercept improved visual acuity by 15.2 letters at week 52.",
        "intravitreal aflibercept 2 mg every 8 weeks improved best-corrected visual acuity by 10.7 letters at week 52",
    )
    response = _ask(
        "What visual acuity gain did aflibercept show in DME?",
        llm,
        user_id="usr_doctor",
        repository_id="ta-notify",
    )
    assert response.decision == "escalate_to_msl"
    owner_notes = store.list_notifications("usr_msl")
    extra_notes = store.list_notifications(extra["user_id"])
    assert any(n.get("case_id") == response.msl_case_id for n in owner_notes)
    assert any(n.get("case_id") == response.msl_case_id for n in extra_notes)
