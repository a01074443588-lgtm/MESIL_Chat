"""Contract doubles here; actual model evidence is recorded separately."""
import json
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app import record_text_ai as ai
from app.ai_settings_store import default_ai_settings, effective_central_models, load_ai_settings, save_ai_settings
from app.ai_system_schemas import AiCentralModels
from app.config import settings


@pytest.fixture
def policy(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ai_settings_file", str(tmp_path / "central.json"))
    monkeypatch.setattr(settings, "ai_central_models_json", None)
    value = default_ai_settings()
    value.central_models = AiCentralModels(text_default_model="verified-local:small", base_url="http://127.0.0.1:11434")
    save_ai_settings(value)
    return value


def facts():
    message_id = uuid4()
    return [
        {"message_id": message_id, "resident_name": "합성인물", "summary": "점심은 1/2 섭취했고 통증은 없었습니다.", "occurred_at": datetime(2026, 8, 6, 4, tzinfo=timezone.utc), "kind": "event"},
        {"message_id": message_id, "resident_name": "합성인물", "summary": "다음 날 점심은 3/4 섭취했습니다.", "occurred_at": datetime(2026, 8, 7, 4, tzinfo=timezone.utc), "kind": "different"},
    ]


def valid_output(records):
    return {"answer": " ".join(dict.fromkeys([records[0]["text"], records[-1]["text"]])), "timeline": [{"date": row["date"], "fact": row["text"], "citations": [row["id"]]} for row in records], "current_status": records[-1]["text"], "unknowns": ai.required_unknowns(records), "citations": [row["id"] for row in records]}


def transport(captured, mutate=None):
    def call(base, path, body, timeout):
        captured.append((path, deepcopy(body)))
        if path == "/api/show":
            return {"details": {"format": "gguf"}}
        records = json.loads(body["messages"][1]["content"])["records"]
        output = valid_output(records)
        if mutate:
            mutate(output)
        else:
            output = {"answer_sources": list(dict.fromkeys([records[0]["id"], records[-1]["id"]])), "ordered_sources": [row["id"] for row in records], "current_source": records[-1]["id"], "unknown_codes": ["conflict"] if ai.required_unknowns(records) == [ai._CONFLICT] else ["no_followup"] if ai.required_unknowns(records) == [ai._NO_FOLLOWUP] else []}
        return {"model": body["model"], "done": True, "message": {"content": json.dumps(output)}, "load_duration": 1, "prompt_eval_count": 100}
    return call


def run(**overrides):
    return ai.run_record_model(**{"feature": "care_record_question", "question": "이후 경과는 어땠나요?", "facts": facts(), "names": ["합성인물"], "all_synthetic": True, **overrides})


def test_shared_inheritance_override_and_legacy_choices_preserved(policy, monkeypatch):
    calls = []
    monkeypatch.setattr(ai, "local_json_request", transport(calls))
    for feature in ("care_record_question", "search_summary"):
        assert run(feature=feature)["model_used"] == "verified-local:small"
    old_roles = policy.model_roles.model_dump()
    policy.central_models.feature_overrides["search_summary"] = "separate-local:model"
    save_ai_settings(policy)
    assert run(feature="search_summary")["model_used"] == "separate-local:model"
    assert run()["model_used"] == "verified-local:small"
    assert load_ai_settings()[0].model_roles.model_dump() == old_roles
    monkeypatch.setattr(settings, "ai_central_models_json", '{"text_default_model":"environment:model"}')
    assert effective_central_models().text_default_model == "environment:model"
    assert run()["model_used"] == "environment:model"


@pytest.mark.parametrize("mutation", [
    lambda output: output["timeline"][0].update(date="2026-08-09 13:00"),
    lambda output: output["timeline"][0].update(fact="점심은 3/4 섭취했고 통증이 있었습니다."),
    lambda output: output["timeline"][0].update(citations=[str(uuid4())]),
    lambda output: output.update(citations=["S999"]),
    lambda output: output["timeline"].pop(),
    lambda output: output.update(answer="영양불량으로 치료가 필요합니다."),
    lambda output: output.update(current_status="통증이 없어졌습니다."),
    lambda output: output.update(unknowns=["약물 치료가 필요합니다."]),
])
def test_whole_invalid_answer_discarded_with_bounded_retry(policy, monkeypatch, mutation):
    records, _, _ = ai.prepare_evidence(facts(), [])
    altered = valid_output(records)
    mutation(altered)
    with pytest.raises((ai.RecordModelError, ValueError)):
        ai.validate_answer(altered, records)
    calls = []
    monkeypatch.setattr(ai, "local_json_request", transport(calls, mutation))
    result = run()
    assert result["processing_method"] == "rules"
    assert "answer" not in result and "timeline" not in result
    assert len([path for path, _ in calls if path == "/api/chat"]) == 2


@pytest.mark.parametrize("field,value", [("answer_sources", ["S999"]), ("ordered_sources", ["S1"]), ("current_source", "S1"), ("unknown_codes", ["no_followup"])])
def test_compact_selection_rejects_invented_ids_and_missing_event_parts(field, value):
    records, _, _ = ai.prepare_evidence(facts(), [])
    output = {"answer_sources": ["S1", "S2"], "ordered_sources": ["S1", "S2"], "current_source": "S2", "unknown_codes": []}
    output[field] = value
    with pytest.raises((ai.RecordModelError, ValueError)):
        ai.validate_selection(output, records)


def test_identifiers_removed_before_transport_and_ids_server_owned(policy, monkeypatch):
    calls = []
    monkeypatch.setattr(ai, "local_json_request", transport(calls))
    records = facts()
    secrets = ["합성인물", "홍길동", "010-1234-5678", "900101-1234567", "123-456-789012", "서울시 강남구 테헤란로 123 101동 202호", str(records[0]["message_id"])]
    records[0]["summary"] = " | ".join(secrets)
    result = run(facts=records, names=secrets[:2], question="홍길동 010-1234-5678 이후 경과?")
    sent = json.dumps(calls, ensure_ascii=False)
    assert all(secret not in sent for secret in secrets)
    assert result["processing_method"] == "local_ai"
    assert result["evidence_ids"] == [records[0]["message_id"]]


def test_real_records_fail_closed_until_logging_policy_verified(policy, monkeypatch):
    calls = []
    monkeypatch.setattr(ai, "local_json_request", transport(calls))
    assert run(all_synthetic=False)["fallback_reason"] == "logging_policy_unverified"
    assert not calls


def test_large_korean_context_falls_back_without_partial_event_or_network(policy, monkeypatch):
    calls = []
    monkeypatch.setattr(ai, "local_json_request", transport(calls))
    records = facts()
    records[0]["summary"] = "합성 경과 기록입니다. " * 1500
    result = run(facts=records)
    assert result["processing_method"] == "rules"
    assert result["fallback_reason"] == "context_limit"
    assert not calls


def test_transport_timeout_preserves_rules_without_answer_or_external_retry(policy, monkeypatch):
    calls = []
    def timeout(base, path, body, remaining):
        calls.append(path)
        raise TimeoutError("synthetic timeout; no record text")
    monkeypatch.setattr(ai, "local_json_request", timeout)
    result = run()
    assert result["processing_method"] == "rules" and "answer" not in result
    assert calls == ["/api/show"]


def test_general_help_uses_relay_compatible_schema_without_internal_record_fields(policy, monkeypatch):
    calls = []

    def call(_base, path, body, _timeout):
        calls.append((path, deepcopy(body)))
        if path == "/api/show":
            return {"details": {"format": "gguf"}}
        assert body["format"] == {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        }
        serialized = json.dumps(body, ensure_ascii=False)
        assert "evidence_ids" not in serialized and "records" not in serialized
        return {
            "model": body["model"],
            "done": True,
            "message": {"content": json.dumps({"answer": "일반 원칙을 안내합니다."})},
        }

    monkeypatch.setattr(ai, "local_json_request", call)
    result = ai.run_general_help_model(
        question="일반적인 돌봄 원칙을 알려줘.",
        conversation=[],
        names=[],
    )
    assert result["processing_method"] == "local_ai"
    assert result["answer"] == "일반 원칙을 안내합니다."
    assert [path for path, _ in calls] == ["/api/show", "/api/chat"]


def test_external_and_cloud_models_never_receive_record_body(policy, monkeypatch):
    calls = []
    monkeypatch.setattr(ai, "local_json_request", lambda base, path, body, timeout: calls.append(path) or {"remote_model": "cloud:model"})
    assert run()["fallback_reason"] == "remote_model_blocked"
    assert calls == ["/api/show"]
    with pytest.raises(ValueError):
        AiCentralModels(text_default_model="minimax:cloud")
    monkeypatch.setattr(ai.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("8.8.8.8", 80))])
    with pytest.raises(ai.RecordModelError):
        # Use the real URL guard without sending any network request.
        original_transport("http://untrusted.local", "/api/chat", {}, 1)


original_transport = ai.local_json_request


def test_candidate_cannot_be_activated_without_qualification(policy, monkeypatch):
    with pytest.raises(ValueError):
        AiCentralModels(text_fallback_model="candidate:model")
    policy.central_models = AiCentralModels(text_default_model="missing:model", text_fallback_model="qualified:model", fallback_qualified=True, base_url="http://127.0.0.1:11434")
    save_ai_settings(policy)
    calls = []
    successful = transport(calls)
    def call(base, path, body, timeout):
        if body["model"] == "missing:model":
            raise OSError("unavailable")
        return successful(base, path, body, timeout)
    monkeypatch.setattr(ai, "local_json_request", call)
    assert run()["processing_method"] == "fallback_model"


def test_event_expansion_keeps_other_topics_same_resident_and_all_followups():
    record = facts()[0]
    topics = [
        {"resident_id": "r1", "resident_name": "합성1", "topic": "이동", "summary": "넘어짐", "entries": [{**record, "summary": "넘어짐", "event_key": "e1"}]},
        {"resident_id": "r1", "resident_name": "합성1", "topic": "건강", "summary": "골절 없음", "entries": [{**record, "summary": "골절 없음", "event_key": "e1", "kind": "followup"}]},
        {"resident_id": "r2", "resident_name": "합성2", "topic": "건강", "summary": "다른 어르신", "entries": [{**record, "summary": "다른 어르신", "event_key": "e1"}]},
    ]
    selected = ai.select_event_facts("이동 이후 경과", topics)
    assert {row["summary"] for row in selected} == {"넘어짐", "골절 없음"}


@pytest.mark.parametrize(
    ("chosen", "expected_labels", "unexpected_label"),
    [
        (["S1"], ["어르비교01"], "어르비교02"),
        (["S1", "S2"], ["어르비교01", "어르비교02"], None),
    ],
)
def test_comparison_answer_labels_every_model_selected_resident_even_when_model_returns_only_ids(
    policy,
    monkeypatch,
    chosen,
    expected_labels,
    unexpected_label,
):
    """Removing forced labels must make a selected resident anonymous again."""
    records = [
        {
            "message_id": uuid4(),
            "resident_id": "resident-1",
            "resident_name": "어르비교01",
            "summary": "아침 식사량이 평소보다 줄었다고 기록했습니다.",
            "occurred_at": datetime(2026, 9, 14, 1, tzinfo=timezone.utc),
            "kind": "different",
            "event_key": "event-1",
        },
        {
            "message_id": uuid4(),
            "resident_id": "resident-2",
            "resident_name": "어르비교02",
            "summary": "오후 활동 참여가 평소보다 줄었다고 기록했습니다.",
            "occurred_at": datetime(2026, 9, 14, 2, tzinfo=timezone.utc),
            "kind": "different",
            "event_key": "event-2",
        },
    ]

    def call(_base, path, body, _timeout):
        if path == "/api/show":
            return {"details": {"format": "gguf"}}
        return {
            "model": body["model"],
            "done": True,
            "message": {"content": json.dumps({"relevant_sources": chosen})},
        }

    monkeypatch.setattr(ai, "local_json_request", call)
    result = ai.run_record_model(
        feature="care_record_question",
        question="최근 7일간 상태가 달라진 어르신과 근거를 알려줘.",
        facts=records,
        names=["어르비교01", "어르비교02"],
        all_synthetic=True,
        semantic_selection=True,
        force_resident_labels=True,
    )

    assert result["processing_method"] == "local_ai"
    for label in expected_labels:
        assert label in result["answer"]
    if unexpected_label:
        assert unexpected_label not in result["answer"]
    assert result["focus_facts"] == [
        records[int(token.removeprefix("S")) - 1] for token in chosen
    ]


@pytest.mark.parametrize(
    ("resident_names", "chosen", "expected_focus", "shared_message"),
    [
        (["어르선택A", "어르선택A", "어르선택A", "어르선택B"], ["S1", "S2", "S3", "S4"], ["S1", "S4", "S2", "S3"], True),
        (["어르선택1", "어르선택2", "어르선택3", "어르선택4"], ["S1", "S2", "S3", "S4"], ["S1", "S2", "S3", "S4"], False),
        (["어르선택A", "어르선택A", "어르선택A", "어르선택B", "어르선택A"], ["S1", "S2", "S3", "S4", "S5"], ["S1", "S4", "S2", "S3", "S5"], True),
    ],
)
def test_comparison_selection_preserves_every_model_selected_subject_beyond_three(
    policy,
    monkeypatch,
    resident_names,
    chosen,
    expected_focus,
    shared_message,
):
    """A global three-fact slice must not silently remove a selected subject."""
    resident_ids = {
        resident_name: f"resident-{index}"
        for index, resident_name in enumerate(dict.fromkeys(resident_names), 1)
    }
    shared_message_id = uuid4()
    records = [
        {
            "message_id": shared_message_id if shared_message else uuid4(),
            "resident_id": resident_ids[resident_name],
            "resident_name": resident_name,
            "summary": f"선택된 변화 {index}",
            "occurred_at": datetime(2026, 9, 15, index, tzinfo=timezone.utc),
            "kind": "different",
            "event_key": f"event-{index}",
        }
        for index, resident_name in enumerate(resident_names, 1)
    ]

    def call(_base, path, body, _timeout):
        if path == "/api/show":
            return {"details": {"format": "gguf"}}
        return {
            "model": body["model"],
            "done": True,
            "message": {"content": json.dumps({"relevant_sources": chosen})},
        }

    monkeypatch.setattr(ai, "local_json_request", call)
    result = ai.run_record_model(
        feature="care_record_question",
        question="최근 7일간 상태가 달라진 어르신과 근거를 알려줘.",
        facts=records,
        names=list(dict.fromkeys(resident_names)),
        all_synthetic=True,
        semantic_selection=True,
        force_resident_labels=True,
    )

    assert result["processing_method"] == "local_ai"
    assert result["focus_facts"] == [
        records[int(token.removeprefix("S")) - 1] for token in expected_focus
    ]
    assert {
        fact["resident_name"] for fact in result["focus_facts"]
    } == set(resident_names)


def test_noncomparison_semantic_selection_keeps_existing_three_fact_answer_limit(
    policy,
    monkeypatch,
):
    """The v8 comparison fix must not expand ordinary record answers."""
    records = [
        {
            "message_id": uuid4(),
            "resident_id": "resident-one",
            "resident_name": "어르일반01",
            "summary": f"일반 기록 {index}",
            "occurred_at": datetime(2026, 9, 15, index, tzinfo=timezone.utc),
            "kind": "different",
            "event_key": f"event-{index}",
        }
        for index in range(1, 5)
    ]

    def call(_base, path, body, _timeout):
        if path == "/api/show":
            return {"details": {"format": "gguf"}}
        return {
            "model": body["model"],
            "done": True,
            "message": {"content": json.dumps({"relevant_sources": ["S1", "S2", "S3", "S4"]})},
        }

    monkeypatch.setattr(ai, "local_json_request", call)
    result = ai.run_record_model(
        feature="care_record_question",
        question="최근 기록을 알려줘.",
        facts=records,
        names=["어르일반01"],
        all_synthetic=True,
        semantic_selection=True,
        force_resident_labels=False,
    )

    assert result["processing_method"] == "local_ai"
    assert result["focus_facts"] == records[:3]
