import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import search_summary_narrative as narrative_module
from app.search_summary_narrative import (
    build_search_fallback,
    choose_summary_mode,
    generate_search_summary,
    preprocess_search_facts,
    search_summary_cache_key,
    validate_search_draft,
)


def fact(*, resident_id=None, resident_name="", text="식사량이 감소했습니다.", day=0, kind="event", event_key="e1"):
    return {
        "message_id": uuid4(),
        "resident_id": resident_id,
        "resident_name": resident_name,
        "occurred_at": datetime(2026, 9, 1, 9, tzinfo=timezone.utc) + timedelta(days=day),
        "summary": text,
        "kind": kind,
        "event_key": event_key,
    }


def test_mode_uses_scope_people_volume_and_topics():
    person = uuid4()
    narrow = [fact(resident_id=person, resident_name="합성대상", day=0)]
    assert choose_summary_mode(narrow, date_span_days=6, selected_resident_id=person) == "detail"

    wide = [
        fact(resident_id=uuid4(), resident_name=f"합성대상{index}", text=text, day=index, event_key=f"e{index}")
        for index, text in enumerate(("식사량 감소", "야간 수면 변화", "이동 중 불편", "보호자 연락 필요"))
    ]
    assert choose_summary_mode(wide, date_span_days=29, selected_resident_id=None) == "overview"


def test_preprocessing_deduplicates_and_keeps_initial_followup_latest():
    person = uuid4()
    first = fact(resident_id=person, resident_name="합성대상", text="보행 중 불편을 확인했습니다.", event_key="chain")
    duplicate = {**first, "message_id": uuid4()}
    followup = fact(resident_id=person, resident_name="합성대상", text="도움 후 불편이 없음을 확인했습니다.", day=2, kind="followup", event_key="chain")
    unrelated_latest = fact(resident_id=person, resident_name="합성대상", text="식사량 절반을 확인했습니다.", day=4, event_key="meal")

    result = preprocess_search_facts([first, duplicate, followup, unrelated_latest], mode="detail", max_facts=32)

    assert result["deduplicated_count"] == 1
    assert [row["event_key"] for row in result["facts"]].count("chain") == 2
    assert result["facts"][-1]["message_id"] == unrelated_latest["message_id"]
    assert result["staged"] is False


def test_structured_validation_keeps_supported_sentence_and_rejects_bad_ids_numbers_and_people():
    first = fact(resident_id=uuid4(), resident_name="합성대상1", text="물 200ml를 제공했습니다.")
    other = fact(resident_id=uuid4(), resident_name="합성대상2", text="통증이 없었습니다.", day=1, event_key="e2")
    prepared = preprocess_search_facts([first, other], mode="overview", max_facts=32)
    records = prepared["records"]
    valid = {
        "sentences": [{
            "sentence": "합성대상1에게 물 200ml를 제공한 기록이 있습니다.",
            "summary_type": "core",
            "resident_key": records[0]["resident_key"],
            "evidence_ids": [records[0]["id"]],
            "first_event_id": records[0]["id"],
            "follow_up_ids": [],
            "latest_record_id": records[0]["id"],
            "needs_follow_up": True,
            "unconfirmed_part": "실제 섭취량은 확인되지 않습니다.",
        }]
    }
    accepted, rejected = validate_search_draft(valid, records)
    assert len(accepted) == 1 and rejected == {}

    invalid = {"sentences": [
        {**valid["sentences"][0], "sentence": "합성대상1에게 물 500ml를 제공했습니다."},
        {**valid["sentences"][0], "evidence_ids": ["S999"]},
        {**valid["sentences"][0], "resident_key": records[1]["resident_key"], "evidence_ids": [records[0]["id"]]},
    ]}
    accepted, rejected = validate_search_draft(invalid, records)
    assert accepted == []
    assert rejected == {"number_or_date": 1, "evidence_id": 1, "resident": 1}


def test_fallback_is_evidence_bound_and_not_a_count_only_summary():
    person = uuid4()
    rows = [
        fact(resident_id=person, resident_name="합성대상", text="오전 식사량이 감소했습니다.", event_key="meal"),
        fact(resident_id=person, resident_name="합성대상", text="도움 후 식사량이 늘었습니다.", day=1, kind="followup", event_key="meal"),
    ]
    prepared = preprocess_search_facts(rows, mode="detail", max_facts=32)
    fallback = build_search_fallback(prepared["facts"], mode="detail")
    assert fallback["sentences"]
    assert fallback["evidence_ids"]
    assert "검색 결과 2건" not in fallback["summary"]
    assert "처음에는" in fallback["summary"]
    assert "이후에는" in fallback["summary"]
    assert fallback["sentences"][0]["first_event_id"] == rows[0]["message_id"]
    assert fallback["sentences"][0]["latest_record_id"] == rows[1]["message_id"]


def test_cache_key_separates_user_scope_filters_content_and_model_revision():
    common = dict(
        organization_id="org1", user_id="user1", room_id="room1",
        filters={"q": "식사", "date_from": "2026-09-01", "date_to": "2026-09-07"},
        result_fingerprint="records-v1", model_fingerprint="model-v1",
    )
    base = search_summary_cache_key(**common)
    assert base != search_summary_cache_key(**{**common, "user_id": "user2"})
    assert base != search_summary_cache_key(**{**common, "filters": {**common["filters"], "q": "수면"}})
    assert base != search_summary_cache_key(**{**common, "result_fingerprint": "records-v2"})
    assert base != search_summary_cache_key(**{**common, "model_fingerprint": "model-v2"})


def test_staged_preprocessing_keeps_at_most_one_copy_of_each_record():
    person = uuid4()
    rows = [
        fact(
            resident_id=person,
            resident_name="합성대상",
            text=f"합성 관찰 {index}회",
            day=index,
            kind="followup" if index % 3 == 0 else "event",
            event_key="one-chain",
        )
        for index in range(40)
    ]

    prepared = preprocess_search_facts(rows, mode="detail", max_facts=32)

    message_ids = [row["message_id"] for row in prepared["facts"]]
    assert prepared["staged"] is True
    assert len(message_ids) == 32
    assert len(message_ids) == len(set(message_ids))


def test_staged_preprocessing_keeps_the_latest_record_across_many_event_groups():
    person = uuid4()
    rows = [
        fact(
            resident_id=person,
            resident_name="합성대상",
            text=f"서로 다른 합성 사건 {index}",
            day=index,
            event_key=f"event-{index}",
        )
        for index in range(40)
    ]

    prepared = preprocess_search_facts(rows, mode="overview", max_facts=32)

    assert rows[-1]["message_id"] in {row["message_id"] for row in prepared["facts"]}


def test_preprocessing_deduplicates_the_same_text_behind_attachment_headers():
    person = uuid4()
    body = fact(resident_id=person, resident_name="합성대상", text="식사량이 절반으로 확인됐습니다.")
    attachment = fact(
        resident_id=person,
        resident_name="합성대상",
        text="[음성 받아쓰기 · synthetic.wav]\n식사량이 절반으로 확인됐습니다.",
        day=1,
    )

    prepared = preprocess_search_facts([body, attachment], mode="detail", max_facts=32)

    assert prepared["deduplicated_count"] == 1
    assert len(prepared["facts"]) == 1


def test_common_sentence_cannot_mix_resident_specific_evidence():
    first = fact(resident_id=uuid4(), resident_name="합성대상1", text="식사량이 감소했습니다.")
    second = fact(resident_id=uuid4(), resident_name="합성대상2", text="수면 시간이 줄었습니다.", day=1)
    records = preprocess_search_facts([first, second], mode="overview", max_facts=32)["records"]
    raw = {
        "sentences": [{
            "sentence": "두 대상에게 변화가 확인됐습니다.",
            "summary_type": "core",
            "resident_key": "COMMON",
            "evidence_ids": [records[0]["id"], records[1]["id"]],
            "first_event_id": None,
            "follow_up_ids": [],
            "latest_record_id": None,
            "needs_follow_up": False,
            "unconfirmed_part": "",
        }]
    }

    accepted, rejected = validate_search_draft(raw, records)

    assert accepted == []
    assert rejected == {"resident": 1}


def test_unconfirmed_part_is_subject_to_the_same_fact_validation():
    row = fact(resident_id=uuid4(), resident_name="합성대상", text="물 200ml를 제공했습니다.")
    record = preprocess_search_facts([row], mode="detail", max_facts=32)["records"][0]
    raw = {
        "sentences": [{
            "sentence": "물 200ml를 제공한 기록이 있습니다.",
            "summary_type": "unconfirmed",
            "resident_key": record["resident_key"],
            "evidence_ids": [record["id"]],
            "first_event_id": record["id"],
            "follow_up_ids": [],
            "latest_record_id": record["id"],
            "needs_follow_up": True,
            "unconfirmed_part": "500ml를 섭취했는지는 확인되지 않습니다.",
        }]
    }

    accepted, rejected = validate_search_draft(raw, [record])

    assert accepted == []
    assert rejected == {"number_or_date": 1}


def test_fraction_notation_is_equivalent_without_changing_the_value():
    row = fact(resident_id=uuid4(), resident_name="합성대상", text="식사량이 4분의 3으로 확인됐습니다.")
    record = preprocess_search_facts([row], mode="detail", max_facts=32)["records"][0]
    raw = {
        "sentences": [{
            "sentence": "식사량이 3/4으로 확인됐습니다.",
            "summary_type": "core",
            "resident_key": record["resident_key"],
            "evidence_ids": [record["id"]],
            "first_event_id": record["id"],
            "follow_up_ids": [],
            "latest_record_id": record["id"],
            "needs_follow_up": False,
            "unconfirmed_part": "",
        }]
    }

    accepted, rejected = validate_search_draft(raw, [record])

    assert len(accepted) == 1
    assert rejected == {}


def test_external_central_provider_falls_back_without_network_call(monkeypatch):
    network_called = False

    async def blocked_network(*_args, **_kwargs):
        nonlocal network_called
        network_called = True
        raise AssertionError("external network must not be called")

    monkeypatch.setattr(narrative_module, "load_ai_settings", lambda: (object(), None))
    monkeypatch.setattr(
        narrative_module,
        "effective_central_models",
        lambda _document: SimpleNamespace(),
    )
    monkeypatch.setattr(
        narrative_module,
        "central_feature_selection",
        lambda _policy, _feature: SimpleNamespace(provider="external", model="blocked-model"),
    )
    monkeypatch.setattr(narrative_module, "local_request", blocked_network)
    row = fact(text="합성 확인 기록")

    result = asyncio.run(
        generate_search_summary(
            facts=[row],
            names=["합성대상"],
            mode="detail",
            all_synthetic=True,
            request_key=uuid4().hex,
            deadline=10**9,
        )
    )

    assert result["processing_method"] == "rules"
    assert result["generation_verified"] is False
    assert result["error_type"] == "local_model_unconfigured"
    assert network_called is False


@pytest.mark.parametrize("mode", ["detail", "overview"])
def test_local_summary_uses_bounded_output_for_short_work_summary(monkeypatch, mode):
    predictions = []
    user_payloads = []
    system_prompts = []

    async def fake_local_request(_client, _base, path, payload, _timeout):
        if path == "/api/show":
            return {}
        predictions.append(payload["options"]["num_predict"])
        system_prompts.append(payload["messages"][0]["content"])
        user_payloads.append(__import__("json").loads(payload["messages"][1]["content"]))
        if len(predictions) == 1:
            content = {
                "a": [{
                    "s": "합성 확인 기록이 있습니다.",
                    "t": "core",
                    "r": "COMMON",
                    "e": ["S1"],
                    "n": False,
                    "x": "",
                }]
            }
        else:
            content = {"supported": [True], "useful_summary": True}
        return {
            "model": "local-test",
            "done": True,
            "done_reason": "stop",
            "message": {"content": __import__("json").dumps(content, ensure_ascii=False)},
        }

    policy = SimpleNamespace(
        base_url="http://local.test",
        context_tokens=2048,
        max_input_chars=18000,
        timeout_seconds=30,
        real_record_logging_verified=True,
    )
    monkeypatch.setattr(narrative_module, "load_ai_settings", lambda: (object(), None))
    monkeypatch.setattr(narrative_module, "effective_central_models", lambda _document: policy)
    monkeypatch.setattr(
        narrative_module,
        "central_feature_selection",
        lambda _policy, _feature: SimpleNamespace(provider="ollama", model="local-test"),
    )
    monkeypatch.setattr(narrative_module, "model_retention", lambda *_args: "3m")
    monkeypatch.setattr(narrative_module, "local_request", fake_local_request)

    result = asyncio.run(
        generate_search_summary(
            facts=[fact(text="합성 확인 기록")],
            names=[],
            mode=mode,
            all_synthetic=True,
            request_key=uuid4().hex,
            deadline=10**9,
        )
    )

    assert result["generation_verified"] is True
    assert predictions == [300]
    assert set(user_payloads[0]) == {"m", "z"}
    assert set(user_payloads[0]["z"][0]) == {"i", "d", "r", "p", "s", "k", "e", "t"}
    schema = narrative_module.CompactSearchSummaryDraft.model_json_schema()
    assert schema["properties"]["a"]["maxItems"] == 3
    assert schema["$defs"]["CompactSearchSummarySentence"]["properties"]["s"]["maxLength"] == 72
    assert set(schema["$defs"]["CompactSearchSummarySentence"]["properties"]) == {"s", "t", "r", "e", "n", "x"}
    three_schema = narrative_module.CompactSearchSummaryDraftThree.model_json_schema()
    assert three_schema["properties"]["a"]["minItems"] == 3
    assert result["sentences"][0]["first_event_id"] == result["sentences"][0]["latest_record_id"]
    # Regression from the actual synthetic Qwen3.8 candidate run: do not turn
    # calendar dates into elapsed days or observations into medical conclusions.
    for required_guidance in ("YYYY-MM-DD", "경과 일수", "원문의 표현", "무증상", "관찰 중"):
        assert (required_guidance in system_prompts[0]) is (mode == "detail")


@pytest.mark.parametrize(("sentence", "reason"), [
    ("식사량 회복과 무증상 상태가 확인되어 추가 조치 없이 경과 관찰 중입니다.", "medical_claim"),
    ("도움 이후 3일째 식사량이 4분의 3으로 늘었습니다.", "number_or_date"),
    ("도움 후 식사량이 5분의 4로 늘었습니다.", "number_or_date"),
])
def test_observed_model_errors_remain_rejected_after_prompt_guidance(sentence, reason):
    source = fact(resident_id=uuid4(), resident_name="합성대상", text="도움 후 식사량이 4분의 3으로 늘었습니다.", day=2)
    record = preprocess_search_facts([source], mode="detail")["records"][0]
    raw = {"sentences": [{"sentence": sentence, "summary_type": "core",
                          "resident_key": record["resident_key"], "evidence_ids": [record["id"]]}]}
    accepted, rejected = validate_search_draft(raw, [record])
    assert accepted == []
    assert rejected == {reason: 1}
