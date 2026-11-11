"""OpenAPI security alignment and legacy upload auth tests."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from digital_msl import store
from digital_msl.config import get_dmsl_settings
from digital_msl.openapi_documentation import path_requires_bearer


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DIGITAL_MSL_DATABASE_URL", f"sqlite:///{tmp_path / 'dmsl_mod.db'}")
    monkeypatch.setenv("DIGITAL_MSL_TRACE_DIR", str(tmp_path / "traces"))
    get_dmsl_settings.cache_clear()
    store._session_factory.cache_clear()
    yield
    get_dmsl_settings.cache_clear()
    store._session_factory.cache_clear()


@pytest.fixture
def api_client(tmp_path, monkeypatch, isolated_store):
    monkeypatch.setenv("RESEARCH_CHAT_ALLOW_DEV_AUTH", "false")
    store.init_store()
    from app import app

    yield TestClient(app)
    get_dmsl_settings.cache_clear()
    store._session_factory.cache_clear()


def test_openapi_marks_protected_repository_upload():
    assert path_requires_bearer("/api/v1/repositories/x/documents/upload", "POST")
    assert not path_requires_bearer("/api/v1/digital-msl/portal/login", "POST")


def test_openapi_schema_has_bearer(api_client: TestClient):
    schema = api_client.app.openapi()
    assert "DMSLBearer" in schema.get("components", {}).get("securitySchemes", {})
    upload_op = schema["paths"]["/api/v1/repositories/{repository_id}/documents/upload"]["post"]
    assert upload_op.get("security") == [{"DMSLBearer": []}]


def test_legacy_upload_rejects_without_auth(api_client: TestClient):
    store.seed_portal_users()
    res = api_client.post(
        "/api/v1/repositories/repo-1/documents/upload",
        files={"files": ("test.txt", io.BytesIO(b"hello"), "text/plain")},
    )
    assert res.status_code == 401


def test_openapi_protected_routes_have_security(api_client: TestClient):
    """Every DMSL/repository/document path in OpenAPI must declare DMSLBearer unless public."""
    schema = api_client.app.openapi()
    paths = schema.get("paths") or {}
    missing = []
    for path, operations in paths.items():
        for method, op in operations.items():
            if method.startswith("x-") or not isinstance(op, dict):
                continue
            if not path_requires_bearer(path, method):
                continue
            sec = op.get("security")
            if sec != [{"DMSLBearer": []}]:
                missing.append(f"{method.upper()} {path}")
    assert not missing, "Missing DMSLBearer security: " + ", ".join(missing[:20])


def test_folder_rename_and_delete_non_empty(isolated_store):
    store.init_store()
    store.upsert_therapeutic_area(repository_id="repo-f", name="F", owner_msl_id="usr_msl")
    root = store.add_ta_folder("repo-f", "Root")
    child = store.add_ta_folder("repo-f", "Child", parent_folder_id=root["folder_id"])
    renamed = store.rename_ta_folder(child["folder_id"], "ChildRenamed")
    assert renamed and renamed["name"] == "ChildRenamed"
    with pytest.raises(ValueError, match="subfolders"):
        store.delete_ta_folder(root["folder_id"])
    store.set_document_folder("doc-1", "repo-f", child["folder_id"])
    with pytest.raises(ValueError, match="documents"):
        store.delete_ta_folder(child["folder_id"])
