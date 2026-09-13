from __future__ import annotations

import json
from pathlib import Path

from app.care_briefing_contract import (
    CareBriefingV1Input,
    CareBriefingV1Result,
)
from app.care_briefing_provider import (
    NvidiaCareBriefingProvider,
    OllamaCareBriefingProvider,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "care_briefing_v1_cases.json"
)


def _first_case() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["cases"][0]


def _provider_with_content(content: str, captured: dict | None = None):
    def transport(url: str, payload: dict, timeout_seconds: float) -> dict:
        if captured is not None:
            captured.update(
                {"url": url, "payload": payload, "timeout": timeout_seconds}
            )
        return {"message": {"content": content}}

    return OllamaCareBriefingProvider(
        base_url="http://ollama.test",
        model="qwen-test",
        timeout_seconds=17,
        transport=transport,
        skill_instructions="검증용 지침",
    )


def test_ollama_provider_returns_validated_standard_envelope():
    case = _first_case()
    input_data = CareBriefingV1Input.model_validate(case["input"])
    captured: dict = {}
    provider = _provider_with_content(
        json.dumps(case["expected_result"], ensure_ascii=False), captured
    )

    envelope = provider.generate(input_data)

    assert envelope.ok is True
    assert envelope.failure is None
    assert envelope.result is not None
    assert envelope.result.events[0].priority == "first"
    assert envelope.provider == "ollama"
    assert envelope.model == "qwen-test"
    assert captured["url"] == "http://ollama.test/api/chat"
    assert captured["timeout"] == 17
    assert captured["payload"]["format"]["title"] == "CareBriefingV1Result"
    assert "검증용 지침" in captured["payload"]["messages"][0]["content"]


def test_ollama_provider_rejects_invalid_json():
    input_data = CareBriefingV1Input.model_validate(_first_case()["input"])

    envelope = _provider_with_content("```json\n{}\n```").generate(input_data)

    assert envelope.ok is False
    assert envelope.failure is not None
    assert envelope.failure.code == "invalid_json"


def test_ollama_provider_marks_missing_required_output():
    input_data = CareBriefingV1Input.model_validate(_first_case()["input"])

    envelope = _provider_with_content("{}").generate(input_data)

    assert envelope.ok is False
    assert envelope.failure is not None
    assert envelope.failure.code == "missing_required_output"


def test_ollama_provider_diagnostics_omit_rejected_values():
    input_data = CareBriefingV1Input.model_validate(_first_case()["input"])
    diagnostics: list[tuple[str, list[dict[str, str]]]] = []
    provider = OllamaCareBriefingProvider(
        base_url="http://ollama.test",
        model="qwen-test",
        transport=lambda url, payload, timeout: {
            "message": {"content": json.dumps({"secret_value": "do-not-log"})}
        },
        skill_instructions="검증용 지침",
        diagnostics=lambda kind, errors: diagnostics.append((kind, errors)),
    )

    envelope = provider.generate(input_data)

    assert envelope.ok is False
    assert diagnostics
    serialized = json.dumps(diagnostics, ensure_ascii=False)
    assert "do-not-log" not in serialized


def test_provider_invalid_json_diagnostics_report_shape_without_content():
    input_data = CareBriefingV1Input.model_validate(_first_case()["input"])
    diagnostics: list[tuple[str, list[dict[str, str]]]] = []
    secret_content = "```json\n{not-valid-secret-value}\n```"
    provider = OllamaCareBriefingProvider(
        base_url="http://ollama.test",
        model="qwen-test",
        transport=lambda url, payload, timeout: {
            "message": {"content": secret_content}
        },
        skill_instructions="검증용 지침",
        diagnostics=lambda kind, errors: diagnostics.append((kind, errors)),
    )

    envelope = provider.generate(input_data)

    assert envelope.ok is False
    assert envelope.failure is not None
    assert envelope.failure.code == "invalid_json"
    assert diagnostics[0][0] == "invalid_json_shape"
    serialized = json.dumps(diagnostics, ensure_ascii=False)
    assert "markdown_fence=true" in serialized
    assert "not-valid-secret-value" not in serialized


def test_ollama_provider_rejects_unknown_evidence():
    case = _first_case()
    input_data = CareBriefingV1Input.model_validate(case["input"])
    result = case["expected_result"]
    result["events"][0]["evidence"][0]["source_id"] = "invented-source"
    result["events"][0]["importance_reasons"][0]["evidence_source_ids"][0] = (
        "invented-source"
    )

    envelope = _provider_with_content(
        json.dumps(result, ensure_ascii=False)
    ).generate(input_data)

    assert envelope.ok is False
    assert envelope.failure is not None
    assert envelope.failure.code == "unsupported_evidence"


def test_ollama_provider_maps_timeout_to_standard_failure():
    input_data = CareBriefingV1Input.model_validate(_first_case()["input"])

    def timeout_transport(url: str, payload: dict, timeout_seconds: float) -> dict:
        raise TimeoutError

    provider = OllamaCareBriefingProvider(
        base_url="http://ollama.test",
        model="qwen-test",
        transport=timeout_transport,
        skill_instructions="검증용 지침",
    )

    envelope = provider.generate(input_data)

    assert envelope.ok is False
    assert envelope.failure is not None
    assert envelope.failure.code == "timeout"
    assert envelope.failure.retryable is True
    assert envelope.failure.offline_fallback_available is False


def _nvidia_provider_with_content(content: str, captured: dict | None = None):
    def transport(
        url: str,
        payload: dict,
        timeout_seconds: float,
        headers: dict[str, str],
    ) -> dict:
        if captured is not None:
            captured.update(
                {
                    "url": url,
                    "payload": payload,
                    "timeout": timeout_seconds,
                    "headers": headers,
                }
            )
        return {"choices": [{"message": {"content": content}}]}

    return NvidiaCareBriefingProvider(
        base_url="https://nvidia.test/v1",
        model="nvidia/nemotron-test",
        api_key="test-secret-key",
        timeout_seconds=23,
        transport=transport,
        skill_instructions="검증용 지침",
    )


def _nvidia_compact_first_case_result() -> dict:
    return {
        "events": [
            {
                "source_ids": [
                    "msg-safety-1",
                    "reply-safety-1",
                    "photo-safety-1",
                ],
                "subject_id": "subject-a",
                "what_happened": "낙상 의심 상황의 후속 확인이 필요합니다.",
                "final_status": "monitoring",
                "final_status_summary": "상태를 관찰하며 후속 확인 중입니다.",
                "occurred_at": "2026-08-22T14:05:00+09:00",
                "scheduled_at": None,
                "priority": "first",
                "importance_score": 95,
                "importance_reason": "안전 위험의 현재 상태 확인이 필요합니다.",
                "status_source_id": "reply-safety-1",
                "completion_source_id": None,
                "verification_field": "follow_up",
                "verification_question": "이후 상태를 다시 확인했나요?",
                "document_type": "nursing_log",
                "document_reason": "안전 관련 경과 기록 후보입니다.",
                "analysis_confidence": 0.94,
            }
        ],
        "ignored_sources": [],
        "warnings": [],
    }


def test_nvidia_provider_returns_same_validated_standard_envelope():
    case = _first_case()
    input_data = CareBriefingV1Input.model_validate(case["input"])
    captured: dict = {}
    provider = _nvidia_provider_with_content(
        json.dumps(_nvidia_compact_first_case_result(), ensure_ascii=False), captured
    )

    envelope = provider.generate(input_data)

    assert envelope.ok is True
    assert envelope.failure is None
    assert envelope.result is not None
    assert envelope.result.events[0].priority == "first"
    assert envelope.provider == "nvidia"
    assert envelope.model == "nvidia/nemotron-test"
    assert captured["url"] == "https://nvidia.test/v1/chat/completions"
    assert captured["timeout"] == 23
    assert captured["headers"]["Authorization"].startswith("Bearer ")
    assert captured["payload"]["guided_json"]["title"] == "NvidiaCompactResult"
    assert captured["payload"]["max_tokens"] == 4_096
    assert captured["payload"]["guided_json"]["properties"]["events"][
        "maxItems"
    ] == 6
    assert captured["payload"]["guided_json"]["properties"]["ignored_sources"][
        "maxItems"
    ] == 3
    assert captured["payload"]["guided_json"]["$defs"][
        "NvidiaCompactEvent"
    ]["properties"]["source_ids"]["maxItems"] == 3
    assert CareBriefingV1Result.model_json_schema()["properties"]["events"][
        "maxItems"
    ] == 200
    assert "response_format" not in captured["payload"]
    system_prompt = captured["payload"]["messages"][0]["content"]
    assert "검증용 지침" in system_prompt
    assert "특이사항 없음" in system_prompt
    assert "routine_completed" in system_prompt
    assert "일상 활동의 정상 종료를 completed 사건으로 만들지 마세요" in system_prompt
    assert "test-secret-key" not in json.dumps(captured["payload"])


def test_nvidia_provider_blocks_sensitive_or_unapproved_external_input():
    case = _first_case()["input"]
    case["transmission_policy"] = {
        "privacy_mode": "local_sensitive",
        "external_ai_allowed": False,
        "contains_direct_identifiers": True,
    }
    input_data = CareBriefingV1Input.model_validate(case)
    called = False

    def transport(url, payload, timeout_seconds, headers):
        nonlocal called
        called = True
        raise AssertionError("privacy-blocked input must not leave the process")

    provider = NvidiaCareBriefingProvider(
        base_url="https://nvidia.test/v1",
        model="nvidia/nemotron-test",
        api_key="test-secret-key",
        transport=transport,
        skill_instructions="검증용 지침",
    )

    envelope = provider.generate(input_data)

    assert called is False
    assert envelope.ok is False
    assert envelope.failure is not None
    assert envelope.failure.code == "privacy_blocked"
    assert envelope.failure.retryable is False


def test_nvidia_provider_requires_non_placeholder_api_key():
    for api_key in (
        "",
        "changeme",
        "placeholder",
        "your_api_key",
        "PASTE_NVIDIA_API_KEY_HERE",
    ):
        try:
            NvidiaCareBriefingProvider(
                base_url="https://nvidia.test/v1",
                model="nvidia/nemotron-test",
                api_key=api_key,
                skill_instructions="검증용 지침",
            )
        except ValueError as error:
            assert "설정되지 않았습니다" in str(error)
        else:
            raise AssertionError("placeholder API key must be rejected")


def test_nvidia_provider_maps_timeout_without_exposing_api_key():
    input_data = CareBriefingV1Input.model_validate(_first_case()["input"])

    def timeout_transport(url, payload, timeout_seconds, headers):
        raise TimeoutError

    provider = NvidiaCareBriefingProvider(
        base_url="https://nvidia.test/v1",
        model="nvidia/nemotron-test",
        api_key="must-never-be-logged",
        transport=timeout_transport,
        skill_instructions="검증용 지침",
    )

    envelope = provider.generate(input_data)

    assert envelope.ok is False
    assert envelope.failure is not None
    assert envelope.failure.code == "timeout"
    assert "must-never-be-logged" not in envelope.model_dump_json()


def test_nvidia_invalid_json_reports_only_allowlisted_response_metadata():
    input_data = CareBriefingV1Input.model_validate(_first_case()["input"])
    diagnostics: list[tuple[str, list[dict[str, str]]]] = []
    secret_content = '{"events":[{"secret":"must-not-be-logged"}'

    def transport(url, payload, timeout_seconds, headers):
        return {
            "choices": [
                {
                    "message": {"content": secret_content},
                    "finish_reason": "length",
                }
            ],
            "usage": {
                "completion_tokens": 2_048,
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        }

    provider = NvidiaCareBriefingProvider(
        base_url="https://nvidia.test/v1",
        model="nvidia/nemotron-test",
        api_key="test-secret-key",
        transport=transport,
        skill_instructions="검증용 지침",
        diagnostics=lambda kind, errors: diagnostics.append((kind, errors)),
    )

    envelope = provider.generate(input_data)

    assert envelope.ok is False
    assert envelope.failure is not None
    assert envelope.failure.code == "invalid_json"
    assert [kind for kind, _ in diagnostics] == [
        "invalid_json_shape",
        "provider_response_metadata",
    ]
    serialized = json.dumps(diagnostics, ensure_ascii=False)
    assert "finish_reason=length" in serialized
    assert "completion_tokens=2048" in serialized
    assert "requested_max_tokens=4096" in serialized
    assert "reasoning_content_present=false" in serialized
    assert "reasoning_tokens=0" in serialized
    assert "must-not-be-logged" not in serialized
    assert "must-not-be-logged" not in serialized


def test_nvidia_adapter_reports_safe_rule_code_for_uncovered_source():
    input_data = CareBriefingV1Input.model_validate(_first_case()["input"])
    diagnostics: list[tuple[str, list[dict[str, str]]]] = []
    provider = NvidiaCareBriefingProvider(
        base_url="https://nvidia.test/v1",
        model="nvidia/nemotron-test",
        api_key="test-secret-key",
        transport=lambda url, payload, timeout, headers: {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "events": [],
                                "ignored_sources": [],
                                "warnings": [],
                            }
                        )
                    }
                }
            ]
        },
        skill_instructions="검증용 지침",
        diagnostics=lambda kind, errors: diagnostics.append((kind, errors)),
    )

    envelope = provider.generate(input_data)

    assert envelope.ok is False
    assert envelope.failure is not None
    assert envelope.failure.code == "unsupported_evidence"
    assert diagnostics == [
        (
            "adapter_grounding_failed",
            [
                {
                    "location": "nvidia_compact_adapter",
                    "type": "adapter_rule",
                    "message": "uncovered_source",
                }
            ],
        )
    ]
