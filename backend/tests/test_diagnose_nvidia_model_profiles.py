from types import SimpleNamespace

from scripts.diagnose_nvidia_model_profiles import (
    MODEL_PROFILES,
    _build_diagnostic_payload,
    _contract_failure_code,
    _effective_profile_parameters,
    _iter_sse_data,
    _reconstruct_openai_sse_response,
)


def test_effective_profile_parameters_disables_deepseek_thinking_without_mutation() -> None:
    profile = MODEL_PROFILES["deepseek-ai/deepseek-v4-flash-0731"]

    parameters = _effective_profile_parameters(
        profile,
        stream=False,
        disable_thinking=True,
    )

    assert parameters["chat_template_kwargs"] == {"thinking": False}
    assert parameters["stream"] is False
    assert profile["chat_template_kwargs"] == {
        "thinking": True,
        "reasoning_effort": "high",
    }


def test_effective_profile_parameters_can_override_stream_only() -> None:
    profile = MODEL_PROFILES["deepseek-ai/deepseek-v4-flash-0731"]

    parameters = _effective_profile_parameters(
        profile,
        stream=True,
        disable_thinking=False,
    )

    assert parameters["stream"] is True
    assert parameters["chat_template_kwargs"] == {
        "thinking": True,
        "reasoning_effort": "high",
    }


def test_build_prototype_payload_removes_provider_contract_controls() -> None:
    provider_payload = {
        "model": "deepseek-ai/deepseek-v4-flash-0731",
        "guided_json": {"type": "object"},
        "chat_template_kwargs": {"enable_thinking": False},
    }

    payload, parameters = _build_diagnostic_payload(
        provider_payload,
        MODEL_PROFILES["deepseek-ai/deepseek-v4-flash-0731"],
        stream=False,
        disable_thinking=True,
        provider_contract=False,
    )

    assert "guided_json" not in payload
    assert payload["chat_template_kwargs"] == {"thinking": False}
    assert parameters["chat_template_kwargs"] == {"thinking": False}
    assert provider_payload["guided_json"] == {"type": "object"}


def test_build_provider_contract_payload_keeps_guided_json() -> None:
    provider_payload = {
        "model": "deepseek-ai/deepseek-v4-flash-0731",
        "guided_json": {"type": "object"},
        "chat_template_kwargs": {"enable_thinking": False},
    }

    payload, parameters = _build_diagnostic_payload(
        provider_payload,
        MODEL_PROFILES["deepseek-ai/deepseek-v4-flash-0731"],
        stream=False,
        disable_thinking=True,
        provider_contract=True,
    )

    assert payload["guided_json"] == {"type": "object"}
    assert payload["chat_template_kwargs"] == {"thinking": False}
    assert parameters["chat_template_kwargs"] == {"thinking": False}


def test_build_provider_contract_payload_keeps_provider_thinking_default() -> None:
    provider_payload = {
        "model": "openai/gpt-oss-20b",
        "guided_json": {"type": "object"},
        "chat_template_kwargs": {"enable_thinking": False},
    }

    payload, _ = _build_diagnostic_payload(
        provider_payload,
        MODEL_PROFILES["openai/gpt-oss-20b"],
        stream=False,
        disable_thinking=False,
        provider_contract=True,
    )

    assert payload["guided_json"] == {"type": "object"}
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}


def test_contract_failure_code_preserves_transport_failure() -> None:
    assert (
        _contract_failure_code(
            response=None,
            transport_error="timeout",
            content=None,
            envelope=None,
        )
        == "timeout"
    )


def test_contract_failure_code_marks_reasoning_only_response() -> None:
    assert (
        _contract_failure_code(
            response={"choices": []},
            transport_error=None,
            content=None,
            envelope=None,
        )
        == "empty_final_content"
    )


def test_contract_failure_code_preserves_contract_validation_failure() -> None:
    envelope = SimpleNamespace(
        ok=False,
        failure=SimpleNamespace(code="missing_required_output"),
    )
    assert (
        _contract_failure_code(
            response={"choices": []},
            transport_error=None,
            content="{}",
            envelope=envelope,
        )
        == "missing_required_output"
    )


def test_contract_failure_code_accepts_validated_output() -> None:
    envelope = SimpleNamespace(ok=True, failure=None)
    assert (
        _contract_failure_code(
            response={"choices": []},
            transport_error=None,
            content="{}",
            envelope=envelope,
        )
        is None
    )


def test_iter_sse_data_ignores_comments_and_joins_multiline_data() -> None:
    lines = [
        b": keep-alive\n",
        b'data: {"first":\n',
        b"data: true}\n",
        b"\n",
        b"data: [DONE]\n",
        b"\n",
    ]

    assert list(_iter_sse_data(lines)) == ['{"first":\ntrue}', "[DONE]"]


def test_reconstruct_openai_sse_response_separates_reasoning_and_content() -> None:
    lines = [
        b'data: {"choices":[{"delta":{"reasoning_content":"think "},"finish_reason":null}]}\n',
        b"\n",
        b'data: {"choices":[{"delta":{"reasoning":"again"},"finish_reason":null}]}\n',
        b"\n",
        b'data: {"choices":[{"delta":{"content":"{\\\"events\\\":"},"finish_reason":null}]}\n',
        b"\n",
        b'data: {"choices":[{"delta":{"content":"[]}"},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":4,"total_tokens":14}}\n',
        b"\n",
        b"data: [DONE]\n",
        b"\n",
    ]

    response = _reconstruct_openai_sse_response(lines)

    message = response["choices"][0]["message"]
    assert message["reasoning_content"] == "think again"
    assert message["content"] == '{"events":[]}'
    assert response["choices"][0]["finish_reason"] == "stop"
    assert response["usage"]["total_tokens"] == 14
    assert response["_stream_metadata"]["event_count"] == 4


def test_reconstruct_openai_sse_response_keeps_reasoning_only_as_empty_content() -> None:
    lines = [
        b'data: {"choices":[{"delta":{"reasoning_content":"only"},"finish_reason":"length"}]}\n',
        b"\n",
        b"data: [DONE]\n",
        b"\n",
    ]

    response = _reconstruct_openai_sse_response(lines)

    message = response["choices"][0]["message"]
    assert message["reasoning_content"] == "only"
    assert message["content"] == ""
