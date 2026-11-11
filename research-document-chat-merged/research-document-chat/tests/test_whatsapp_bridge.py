"""WhatsApp bridge: phone normalize, Meta webhook parse, MSL notify."""
from __future__ import annotations

import pytest

from digital_msl import store
from digital_msl.config import get_dmsl_settings
from digital_msl.whatsapp_bridge import (
    normalize_phone,
    notify_msl_whatsapp_group,
    parse_meta_webhook,
    whatsapp_repository_candidates,
)


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DIGITAL_MSL_DATABASE_URL", f"sqlite:///{tmp_path / 'wa.db'}")
    monkeypatch.setenv("DIGITAL_MSL_TRACE_DIR", str(tmp_path / "traces"))
    get_dmsl_settings.cache_clear()
    store._session_factory.cache_clear()
    store.init_store()
    yield
    get_dmsl_settings.cache_clear()
    store._session_factory.cache_clear()


def test_normalize_phone_india():
    assert normalize_phone("7010138776") == "+917010138776"
    assert normalize_phone("+91 7010138776") == "+917010138776"


def test_parse_meta_webhook_text_message():
    body = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": "919701013776", "profile": {"name": "Santhosh"}}],
                            "messages": [
                                {
                                    "from": "919701013776",
                                    "id": "wamid.abc",
                                    "type": "text",
                                    "text": {"body": "DME switching rate?"},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }
    msgs = parse_meta_webhook(body)
    assert len(msgs) == 1
    assert msgs[0]["text"] == "DME switching rate?"
    assert msgs[0]["id"] == "wamid.abc"
    assert msgs[0]["profile_name"] == "Santhosh"


def test_whatsapp_repository_uses_default_repo(monkeypatch):
    monkeypatch.setenv("DIGITAL_MSL_DEFAULT_REPOSITORY_ID", "ta-retina")
    get_dmsl_settings.cache_clear()
    repos, user_id = whatsapp_repository_candidates("+919999999999", "macular edema")
    assert repos == ["ta-retina"]
    assert "+919999999999" in user_id or user_id


def test_notify_msl_skips_without_config():
    assert notify_msl_whatsapp_group(case_id="case_x", question="Q?", requester="+911") == 0


def test_send_whatsapp_mock_mode(monkeypatch):
    monkeypatch.setenv("DIGITAL_MSL_WHATSAPP_MOCK", "true")
    monkeypatch.setenv("DIGITAL_MSL_WHATSAPP_API_URL", "https://graph.example.com/messages")
    get_dmsl_settings.cache_clear()
    from digital_msl.whatsapp_bridge import send_whatsapp_text

    status, detail = send_whatsapp_text("+917010138776", "Hello demo")
    assert status == "sent"
    assert detail.startswith("mock:")
