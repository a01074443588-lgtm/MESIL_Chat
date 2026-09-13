"""Central admin settings: preservation, capability receipts and privacy boundaries."""
import base64
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import Response, HTTPException
from fastapi.testclient import TestClient

from app import ai_system as api
from app.ai_settings_store import (default_ai_settings, central_editable_policy, central_feature_selection,
    central_image_binding, resolved_model_role, effective_provider_settings, load_ai_settings, save_ai_settings)
from app.ai_system_schemas import AiCentralModels, AiCentralImageCheckRequest, AiModelRoleSelection, AiRoutingPreviewRequest
from app.ai_router import build_routing_preview
from app.config import settings
from app.main import app
from test_ai_system import _hardware, _status

ORIGIN = {"origin": "http://testserver"}

@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ai_settings_file", str(tmp_path / "settings.json"))
    monkeypatch.setattr(settings, "ai_central_models_json", None)
    document = default_ai_settings()
    document.central_models = AiCentralModels(text_default_model="fixture:text", base_url="http://127.0.0.1:11434")
    save_ai_settings(document)
    api._CENTRAL_CATALOG.clear()


def test_migration_is_read_only_and_old_roles_cannot_override_saved_central():
    original = load_ai_settings()[0]
    path = Path(settings.ai_settings_file)
    before = path.read_bytes()
    policy = central_editable_policy(original)
    assert path.read_bytes() == before and original.central_models.inheritance_version == 1
    assert policy.inheritance_version == 2
    assert central_feature_selection(policy, "search_summary").model == "fixture:text"
    saved = original.model_copy(update={"central_models": policy})
    assert resolved_model_role(saved, "text") == original.model_roles.text
    policy.feature_overrides.pop("document_text", None)
    assert resolved_model_role(saved, "text").model == "fixture:text"
    saved.model_roles.text = AiModelRoleSelection(provider="openai", model="archived:ignored")
    assert resolved_model_role(saved, "text").model == "fixture:text"
    policy.feature_overrides["document_text"] = AiModelRoleSelection(provider="ollama", model="explicit:override")
    assert resolved_model_role(saved, "text").model == "explicit:override"
    assert effective_provider_settings(saved)["ollama"].model == "fixture:text"
    assert saved.providers["ollama"].model == original.providers["ollama"].model


def test_legacy_local_text_model_becomes_the_visible_central_default(monkeypatch):
    document = default_ai_settings()
    assert document.central_models.text_default_model is None
    assert document.model_roles.text is not None
    assert document.model_roles.text.provider == document.central_models.default_provider

    policy = central_editable_policy(document)

    assert policy.text_default_model == document.model_roles.text.model
    assert "document_text" not in policy.feature_overrides

    monkeypatch.setattr(
        api,
        "local_json_request",
        lambda *_args, **_kwargs: {"models": [{"name": document.model_roles.text.model}]},
    )
    catalog = api._central_catalog(document)
    assert catalog["current_model_listed"] is True


def test_image_inheritance_requires_receipt_bound_to_endpoint_and_exact_tag():
    doc = load_ai_settings()[0]
    doc.central_models.inheritance_version = 2
    chosen = central_feature_selection(doc.central_models, "image_reading")
    assert chosen.model == "fixture:text"
    assert resolved_model_role(doc, "vision") == doc.model_roles.vision
    doc.central_models.image_verification_bindings = [central_image_binding(doc, chosen)]
    assert resolved_model_role(doc, "vision") == chosen
    doc.central_models.text_default_model = "fixture:new"
    assert resolved_model_role(doc, "vision") == doc.model_roles.vision
    doc.central_models.vision_fallback_model = "fixture:text"
    assert resolved_model_role(doc, "vision") == chosen
    doc.central_models.base_url = "http://127.0.0.2:11434"
    assert resolved_model_role(doc, "vision") == doc.model_roles.vision


def test_catalog_failure_or_missing_current_model_never_changes_saved_settings(monkeypatch):
    doc = load_ai_settings()[0]
    before = Path(settings.ai_settings_file).read_bytes()
    monkeypatch.setattr(api, "local_json_request", lambda *args: {"models": [{"name": "fixture:available"}, {"name": "fixture-cloud"}]})
    result = api._central_catalog(doc)
    assert result["models"] == ["fixture:available"] and not result["current_model_listed"]
    assert result["image_capabilities_verified"] is False
    def unavailable(*args):
        raise TimeoutError("not exposed")
    monkeypatch.setattr(api, "local_json_request", unavailable)
    result = api._central_catalog(doc)
    assert result["status"] == "failed" and "not exposed" not in str(result)
    assert before == Path(settings.ai_settings_file).read_bytes()


@pytest.mark.parametrize("capability", [False, True])
def test_image_check_uses_only_synthetic_image_and_does_not_select_model(monkeypatch, capability):
    old = load_ai_settings()[0]
    calls = []
    def transport(url, route, payload, timeout):
        calls.append(route)
        if route == "/api/show":
            return {"capabilities": ["vision"] if capability else ["completion"]}
        image = base64.b64decode(payload["messages"][0]["images"][0])
        assert image.startswith(b"\x89PNG") and "sources" not in payload
        return {"model": "fixture:vision", "done": True, "message": {"content": '{"digits":"582"}'}}
    monkeypatch.setattr(api, "local_json_request", transport)
    response = api.central_image_check(AiCentralImageCheckRequest(model="fixture:vision"), Response(), None)
    assert response["verified"] is capability
    new = load_ai_settings()[0]
    assert new.central_models.inheritance_version == 1
    assert new.central_models.text_default_model == old.central_models.text_default_model
    assert new.central_models.vision_default_model is None and new.model_roles == old.model_roles
    assert len(new.central_models.image_verification_bindings) == int(capability)
    assert len(calls) == 1 + int(capability)


def test_slow_image_check_does_not_overwrite_new_environment(monkeypatch):
    def transport(url, route, payload, timeout):
        if route == "/api/show":
            return {"capabilities": ["vision"]}
        newer = load_ai_settings()[0]
        newer.central_models.base_url = "http://127.0.0.2:11434"
        save_ai_settings(newer)
        return {"model": "fixture:vision", "done": True, "message": {"content": '{"digits":"582"}'}}
    monkeypatch.setattr(api, "local_json_request", transport)
    with pytest.raises(HTTPException) as error:
        api.central_image_check(AiCentralImageCheckRequest(model="fixture:vision"), Response(), None)
    assert error.value.status_code == 409
    assert load_ai_settings()[0].central_models.base_url == "http://127.0.0.2:11434"
    assert not load_ai_settings()[0].central_models.image_verification_bindings


@pytest.mark.parametrize("real", [False, True])
def test_explicit_external_route_respects_real_data_gate_and_rules_mode(real):
    doc = load_ai_settings()[0]
    doc.central_models = AiCentralModels(inheritance_version=2, default_provider="openai", execution_environment="external", text_default_model="fixture:external")
    statuses = [_status("openai", location="external", capabilities=["text"], model="fixture:external"), _status("rules", location="rules", capabilities=["text"])]
    request = AiRoutingPreviewRequest(model_role="text", capability="text", contains_real_data=real, deidentified_confirmed_by_user=not real, text="합성 시험 자료")
    result = build_routing_preview(request, doc, statuses, _hardware())
    assert any(item.provider == "openai" for item in result.candidates) is (not real)
    doc.central_models.default_provider = "rules"
    assert all(item.provider == "rules" for item in build_routing_preview(request, doc, statuses, _hardware()).candidates)


def test_new_settings_endpoints_reject_staff_and_ignore_forged_receipts(monkeypatch):
    with TestClient(app) as admin:
        assert admin.post("/api/auth/login", headers=ORIGIN, json={"username":"admin", "password":"AdminPass!234"}).status_code == 200
        old = load_ai_settings()[0]
        policy = central_editable_policy(old).model_dump(mode="json")
        policy.update(image_verification_bindings=["forged"], real_record_logging_verified=True, base_url="http://127.0.0.2:11434", default_provider="openai", execution_environment="external")
        saved = admin.put("/api/ai/system/settings/central-models", headers=ORIGIN, json=policy)
        assert saved.status_code == 200
        current = load_ai_settings()[0]
        assert not current.central_models.image_verification_bindings and not current.central_models.real_record_logging_verified
        assert current.central_models.base_url == old.central_models.base_url
        assert current.central_models.default_provider == "ollama" and current.model_roles == old.model_roles
        compat = admin.put("/api/ai/system/providers/ollama/settings", headers=ORIGIN, json={"model":"fixture:changed"})
        assert compat.status_code == 200
        assert load_ai_settings()[0].central_models.text_default_model == "fixture:changed"
        assert load_ai_settings()[0].providers["ollama"].model == old.providers["ollama"].model
        monkeypatch.setattr(settings, "ai_central_models_json", '{"text_default_model":"fixture:locked"}')
        assert admin.put("/api/ai/system/central-models/connection", headers=ORIGIN, json={"environment":"rules"}).status_code == 409
        name="central-staff-"+uuid4().hex[:8]
        assert admin.post("/api/employees", headers=ORIGIN, json={"username":name, "password":"123456", "full_name":"설정 합성직원", "role":"staff"}).status_code == 201
    with TestClient(app) as staff:
        assert staff.post("/api/auth/login", headers=ORIGIN, json={"username":name,"password":"123456"}).status_code == 200
        for method, route, payload in [("post","catalog",{}),("post","verify-image",{"model":"fixture:text"}),("put","connection",{"environment":"rules"})]:
            assert getattr(staff,method)("/api/ai/system/central-models/"+route,headers=ORIGIN,json=payload).status_code == 403
