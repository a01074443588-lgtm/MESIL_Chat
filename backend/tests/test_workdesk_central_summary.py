"""Workdesk model routing and warm generation, with synthetic source text."""
import json

import pytest

from app import local_ai
from app import record_narrative


ENTRIES=[{"number":1,"resident":"합성대상","body":"물 200ml를 제공했고 모두 마셨습니다. 불편은 없었습니다."}]
SUMMARY="물 200ml를 제공했고 모두 마셨습니다. 불편은 없었다고 기록되어 있습니다. [1]"


def configure(monkeypatch,**overlay):
    monkeypatch.setattr(local_ai.settings,"ai_review_provider","ollama")
    monkeypatch.setattr(local_ai.settings,"ai_review_base_url","http://legacy.invalid:11434")
    monkeypatch.setattr(local_ai.settings,"ai_review_model","legacy-model")
    monkeypatch.setattr(local_ai.settings,"ai_review_local_models","legacy-model")
    policy={"text_default_model":"central-model","base_url":"http://127.0.0.1:11434",
            "timeout_seconds":30,"context_tokens":4096,"real_record_logging_verified":True}
    policy.update(overlay)
    monkeypatch.setattr(local_ai.settings,"ai_central_models_json",json.dumps(policy))


def transport(monkeypatch, *, loaded=True, remote=False, response_model="central-model", summary=SUMMARY):
    requests=[]
    def response(base,path,body):
        requests.append((base,path,body))
        if path=="/api/ps":return {"models":[{"name":"central-model","context_length":4096}] if loaded else []}
        if path=="/api/show":return {"remote_host":"cloud.invalid"} if remote else {}
        if isinstance(body.get("format"),dict) and "claims" in body["format"].get("properties",{}):
            return {"model":response_model,"done":True,"message":{"content":json.dumps({"claims":[{
                "text":summary,"resident":"합성대상","citations":[1],"section":"overview"}]})}}
        if isinstance(body.get("format"),dict):
            return {"model":response_model,"done":True,"message":{"content":json.dumps({"supported":[True]})}}
        return {"model":response_model,"done":True,"message":{"content":json.dumps({"summary":summary,"references":[1]})}}
    # Old and new external transports are controlled, not the routing/validator.
    def legacy(*,url,payload,**kwargs):
        return response(url.rsplit("/api/",1)[0],"/api/chat",payload)
    async def internal(client,base,path,body,timeout):
        return response(base,path,body)
    monkeypatch.setattr(local_ai,"_request_json",legacy)
    monkeypatch.setattr(record_narrative,"local_request",internal)
    return requests


def test_workdesk_uses_central_model_endpoint_context_and_retention(monkeypatch):
    configure(monkeypatch)
    requests=transport(monkeypatch)
    result=local_ai.summarize_room_messages(entries=ENTRIES,external_allowed=True,central_feature="search_summary")
    assert result.provider=="ollama" and result.model=="central-model"
    chat=[request for request in requests if request[1]=="/api/chat"]
    assert len(chat)==2 and all(row[0]=="http://127.0.0.1:11434" for row in chat)
    assert chat[0][2]["options"]["num_ctx"]==4096
    assert chat[0][2]["keep_alive"]==180
    assert result.summary=="합성대상: "+SUMMARY


@pytest.mark.parametrize("options,reason",[
    ({"loaded":False},"model_cold"),
    ({"remote":True},"remote_model_blocked"),
    ({"response_model":"wrong-model"},"model_response_invalid"),
])
def test_not_ready_or_wrong_model_cannot_be_reported_as_llm_success(monkeypatch,options,reason):
    configure(monkeypatch)
    requests=transport(monkeypatch,**options)
    with pytest.raises(local_ai.LocalAiError,match=reason):
        local_ai.summarize_room_messages(entries=ENTRIES,external_allowed=True,central_feature="document_text")
    assert all(path!="/api/generate" for _,path,_ in requests)
    if reason!="model_response_invalid":assert all(path!="/api/chat" for _,path,_ in requests)


def test_unqualified_external_central_selection_does_not_fallback_to_legacy(monkeypatch):
    configure(monkeypatch,feature_overrides={"document_text":{"provider":"openai","model":"external-model"}})
    requests=transport(monkeypatch)
    with pytest.raises(local_ai.LocalAiError,match="local_model_unconfigured"):
        local_ai.summarize_room_messages(entries=ENTRIES,external_allowed=True,central_feature="document_text")
    assert requests==[]


def test_invented_measurement_still_fails_source_validation(monkeypatch):
    configure(monkeypatch)
    transport(monkeypatch,summary=SUMMARY.replace("200ml","500ml"))
    with pytest.raises(local_ai.LocalAiError,match="summary_not_supported"):
        local_ai.summarize_room_messages(entries=ENTRIES,external_allowed=True,central_feature="document_text")


def test_drinking_wording_is_valid_consumption_but_provision_alone_is_not():
    local_ai._validate_record_summary_faithfulness(
        "물 200ml를 모두 섭취했습니다. [1]", ENTRIES, require_headings=False)
    with pytest.raises(local_ai.LocalAiError,match="원문에 없는 내용"):
        local_ai._validate_record_summary_faithfulness(
            "물 200ml를 모두 섭취했습니다. [1]",
            [{"number":1,"body":"물 200ml를 제공했습니다. 마신 양은 기록하지 않았습니다."}],require_headings=False)


def test_independent_review_can_reject_unsupported_action_without_publishing_summary(monkeypatch):
    configure(monkeypatch)
    requests=transport(monkeypatch,summary=SUMMARY+" 직원이 옆에서 직접 도와 드렸습니다. [1]")
    original=record_narrative.local_request
    async def independent(client,base,path,body,timeout):
        if path=="/api/chat" and "supported" in body.get("format",{}).get("properties",{}):
            return {"model":"central-model","done":True,"message":{"content":json.dumps({"supported":[False]})}}
        return await original(client,base,path,body,timeout)
    monkeypatch.setattr(record_narrative,"local_request",independent)
    with pytest.raises(local_ai.LocalAiError,match="summary_not_supported"):
        local_ai.summarize_room_messages(entries=ENTRIES,external_allowed=True,central_feature="document_text")
