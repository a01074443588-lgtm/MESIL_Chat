import pytest

from app import local_ai


VALID_DRAFT = {
    "corrected_text": "시설(가명)001 어르신이 점심을 절반 섭취했습니다.",
    "summary": "점심 식사량 감소를 관찰했습니다.",
    "classification": "nutrition",
    "risk_level": "medium",
    "target_roles": ["caregiver", "nurse"],
    "document_types": ["care_service_record", "nursing_log"],
    "keywords": ["점심", "식사량"],
}


def test_chain_uses_nemotron_first_for_test_data(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(local_ai.settings, "ai_review_provider", "chain")
    monkeypatch.setattr(local_ai.settings, "ai_review_external_enabled", True)
    monkeypatch.setattr(
        local_ai.settings,
        "nvidia_nemotron_model",
        "nvidia/test-nemotron",
    )
    monkeypatch.setattr(
        local_ai,
        "_review_with_nvidia",
        lambda **kwargs: calls.append(f"nvidia:{kwargs['model']}") or VALID_DRAFT,
    )
    monkeypatch.setattr(
        local_ai,
        "_review_with_ollama",
        lambda **kwargs: calls.append(f"ollama:{kwargs['model']}") or VALID_DRAFT,
    )

    result = local_ai.refine_record_draft(
        source_snapshot={"body": VALID_DRAFT["corrected_text"]},
        current_draft=VALID_DRAFT,
        lexicon_context={},
        external_allowed=True,
    )

    assert calls == ["nvidia:nvidia/test-nemotron"]
    assert result.provider == "nvidia"
    assert result.model == "nvidia/test-nemotron"


def test_chain_falls_back_from_nemotron_to_local_models(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(local_ai.settings, "ai_review_provider", "chain")
    monkeypatch.setattr(local_ai.settings, "ai_review_external_enabled", True)
    monkeypatch.setattr(
        local_ai.settings,
        "nvidia_nemotron_model",
        "nvidia/test-nemotron",
    )
    monkeypatch.setattr(
        local_ai.settings,
        "ai_review_local_models",
        "qwen3.6:35b,gemma4:e4b",
    )

    def fail_nvidia(**kwargs):
        calls.append(f"nvidia:{kwargs['model']}")
        raise local_ai.LocalAiError("Nemotron 시험 실패")

    def local_result(**kwargs):
        model = kwargs["model"]
        calls.append(f"ollama:{model}")
        if model == "qwen3.6:35b":
            raise local_ai.LocalAiError("Qwen 응답 지연")
        return VALID_DRAFT

    monkeypatch.setattr(local_ai, "_review_with_nvidia", fail_nvidia)
    monkeypatch.setattr(local_ai, "_review_with_ollama", local_result)

    result = local_ai.refine_record_draft(
        source_snapshot={"body": VALID_DRAFT["corrected_text"]},
        current_draft=VALID_DRAFT,
        lexicon_context={},
        external_allowed=True,
    )

    assert calls == [
        "nvidia:nvidia/test-nemotron",
        "ollama:qwen3.6:35b",
        "ollama:gemma4:e4b",
    ]
    assert result.provider == "ollama"
    assert result.model == "gemma4:e4b"
    assert [attempt["status"] for attempt in result.attempts] == [
        "failed",
        "failed",
        "completed",
    ]


def test_chain_never_sends_non_test_data_to_external_api(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(local_ai.settings, "ai_review_provider", "chain")
    monkeypatch.setattr(local_ai.settings, "ai_review_external_enabled", True)
    monkeypatch.setattr(local_ai.settings, "ai_review_local_models", "qwen3.6:35b")
    monkeypatch.setattr(
        local_ai,
        "_review_with_nvidia",
        lambda **kwargs: calls.append(f"nvidia:{kwargs['model']}") or VALID_DRAFT,
    )
    monkeypatch.setattr(
        local_ai,
        "_review_with_ollama",
        lambda **kwargs: calls.append(f"ollama:{kwargs['model']}") or VALID_DRAFT,
    )

    result = local_ai.refine_record_draft(
        source_snapshot={"body": VALID_DRAFT["corrected_text"]},
        current_draft=VALID_DRAFT,
        lexicon_context={},
        external_allowed=False,
    )

    assert calls == ["ollama:qwen3.6:35b"]
    assert result.provider == "ollama"
    assert result.attempts[0]["status"] == "skipped"


def test_ai_cannot_remove_existing_safety_candidates(monkeypatch):
    reduced = {
        **VALID_DRAFT,
        "risk_level": "low",
        "target_roles": ["caregiver"],
        "document_types": ["care_service_record"],
    }
    monkeypatch.setattr(local_ai.settings, "ai_review_provider", "ollama")
    monkeypatch.setattr(local_ai.settings, "ai_review_model", "qwen3.6:35b")
    monkeypatch.setattr(
        local_ai,
        "_review_with_ollama",
        lambda **kwargs: reduced,
    )

    result = local_ai.refine_record_draft(
        source_snapshot={"body": VALID_DRAFT["corrected_text"]},
        current_draft=VALID_DRAFT,
        lexicon_context={},
        external_allowed=False,
    )

    assert result.draft["risk_level"] == "medium"
    assert result.draft["target_roles"] == ["caregiver", "nurse"]
    assert result.draft["document_types"] == [
        "care_service_record",
        "nursing_log",
    ]


def test_ai_cannot_replace_skin_health_classification_with_daily_care(monkeypatch):
    current = {
        **VALID_DRAFT,
        "classification": "health",
        "keywords": ["health", "skin_integrity"],
    }
    reviewed = {
        **VALID_DRAFT,
        "classification": "daily_care",
        "keywords": ["일상생활"],
    }
    monkeypatch.setattr(local_ai.settings, "ai_review_provider", "ollama")
    monkeypatch.setattr(local_ai.settings, "ai_review_model", "qwen3.6:35b")
    monkeypatch.setattr(
        local_ai,
        "_review_with_ollama",
        lambda **kwargs: reviewed,
    )

    result = local_ai.refine_record_draft(
        source_snapshot={"body": "엉치 피부가 붉게 보였습니다."},
        current_draft=current,
        lexicon_context={},
        external_allowed=False,
    )

    assert result.draft["classification"] == "health"


def test_ai_cannot_drop_daily_document_drafts_or_safety_questions():
    current = {
        **VALID_DRAFT,
        "risk_level": "high",
        "verification_questions": [
            "이름, 시간, 수치, 신체 부위와 사건 경과를 원문과 대조했나요?"
        ],
        "document_drafts": [
            {
                "document_type": "care_service_record",
                "content": "급여제공기록지 안전 초안",
                "verification_questions": ["제공한 지원을 확인했나요?"],
            },
            {
                "document_type": "nursing_log",
                "content": "간호일지 안전 초안",
                "verification_questions": ["간호 조치를 확인했나요?"],
            },
        ],
    }
    reviewed = {
        **VALID_DRAFT,
        "risk_level": "low",
        "document_types": ["care_service_record"],
        "verification_questions": [],
        "document_drafts": [
            {
                "document_type": "care_service_record",
                "content": "AI가 다듬은 급여제공기록지",
                "verification_questions": [],
            }
        ],
    }

    protected = local_ai._protect_safety_candidates(current, reviewed)

    assert protected["risk_level"] == "high"
    assert protected["document_types"] == [
        "care_service_record",
        "nursing_log",
    ]
    assert {
        proposal["document_type"]: proposal["content"]
        for proposal in protected["document_drafts"]
    } == {
        "care_service_record": "AI가 다듬은 급여제공기록지",
        "nursing_log": "간호일지 안전 초안",
    }
    assert protected["verification_questions"] == current["verification_questions"]


def test_room_summary_marks_references_outside_the_search_results():
    summary = "주요 내용 [1]\n완료된 내용 [3]"

    assert local_ai._safe_room_summary_references(summary, 2) == (
        "주요 내용 [1]\n완료된 내용 [근거 확인 필요]"
    )


def test_room_summary_rejects_too_short_or_unreferenced_results():
    for content in (
        '{"summary":"완료"}',
        '{"summary":"주요 내용은 확인했지만 근거 번호를 반환하지 않은 긴 요약입니다."}',
    ):
        try:
            local_ai._validated_room_summary(content, source_count=2)
        except local_ai.LocalAiError:
            continue
        raise AssertionError("품질 기준에 못 미치는 요약이 승인되었습니다.")


def test_room_summary_uses_declared_references_when_summary_has_no_markers():
    summary = local_ai._validated_room_summary(
        (
            '{"summary":"주요 내용은 점심 식사량을 확인했고 수분 섭취를 도운 뒤 '
            '상태를 계속 관찰할 예정입니다.",'
            '"references":[2]}'
        ),
        source_count=2,
    )

    assert summary.endswith("근거 대화: [2]")


def test_record_summary_accepts_declared_evidence_and_restores_visible_reference():
    content = (
        '{"summary":"[대상] 시설(가명)003\\n'
        '점심 식사량과 수분 섭취를 확인했습니다. [1][3]",'
        '"references":[1,2,3]}'
    )

    summary = local_ai._validated_room_summary(
        content,
        source_count=3,
        require_all_references=True,
    )

    assert summary.endswith("참고한 근거: [2]")


def test_record_summary_accepts_all_selected_evidence_grouped_by_resident():
    content = (
        '{"summary":"[대상] 시설(가명)003\\n'
        '점심 식사는 절반 섭취했고 물 150ml를 추가 제공했습니다. [1][2]\\n\\n'
        '[대상] 시설(가명)012\\n'
        '보행 시 비틀거려 부축하고 간호팀에 전달했습니다. [3]",'
        '"references":[1,2,3]}'
    )

    summary = local_ai._validated_room_summary(
        content,
        source_count=3,
        require_all_references=True,
    )

    assert "시설(가명)003" in summary
    assert "시설(가명)012" in summary
    assert all(f"[{number}]" in summary for number in (1, 2, 3))


def test_record_summary_prompt_requires_briefing_sections_and_every_evidence():
    prompt = local_ai._room_summary_prompt("급여제공 기록")

    assert "[한눈에 보기]" in prompt
    assert "[먼저 확인]" in prompt
    assert "[이미 한 일]" in prompt
    assert "[다음 업무 제안]" in prompt
    assert "원문을 사람별로 길게 다시 쓰지 마세요" in prompt
    assert "마지막 번호까지 모든 번호를 references 배열에" in prompt
    assert "혈압 168/91" in prompt
    assert "원문에 '안정', '양호', '호전'이 없으면" in prompt


def test_nemotron_room_summary_disables_thinking(monkeypatch, tmp_path):
    captured: dict = {}
    request_options: dict = {}
    key_file = tmp_path / "nvidia.key"
    key_file.write_text("test-key", encoding="utf-8")
    monkeypatch.setattr(local_ai.settings, "nvidia_api_key", None)
    monkeypatch.setattr(local_ai.settings, "nvidia_api_key_file", str(key_file))

    def request_json(**kwargs):
        request_options.update(kwargs)
        captured.update(kwargs["payload"])
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"summary":"주요 내용: RESIDENT_001의 식사량을 '
                            '확인했습니다. [1]\\n확인 또는 후속 조치: '
                            '계속 관찰합니다. [1]","references":[1]}'
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(local_ai, "_request_json", request_json)

    summary = local_ai._room_summary_with_nvidia(
        [
            {
                "number": 1,
                "resident": "시설(가명)001",
                "body": "시설(가명)001 어르신이 점심 식사를 절반 드셨습니다.",
            }
        ]
    )

    assert "식사량" in summary
    assert "시설(가명)001" in summary
    assert "시설(가명)001" not in captured["messages"][1]["content"]
    assert "RESIDENT_001" in captured["messages"][1]["content"]
    assert captured["chat_template_kwargs"] == {"enable_thinking": False}
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["temperature"] == 0.0
    assert "top_k" not in captured
    assert request_options["retry_transient"] is True


def test_selected_record_summary_has_short_budget_and_no_retry(
    monkeypatch,
    tmp_path,
):
    captured: dict = {}
    request_options: dict = {}
    key_file = tmp_path / "nvidia.key"
    key_file.write_text("test-key", encoding="utf-8")
    monkeypatch.setattr(local_ai.settings, "nvidia_api_key", None)
    monkeypatch.setattr(local_ai.settings, "nvidia_api_key_file", str(key_file))
    monkeypatch.setattr(local_ai.settings, "nvidia_api_timeout_seconds", 120)
    monkeypatch.setattr(local_ai.settings, "nvidia_summary_timeout_seconds", 33)

    def request_json(**kwargs):
        request_options.update(kwargs)
        captured.update(kwargs["payload"])
        return {
            "choices": [
                {
                    "message": {
                        "content": local_ai.json.dumps(
                            {
                                "summary": (
                                    "[한눈에 보기]\n"
                                    "RESIDENT_001의 식사 기록입니다. [1]\n"
                                    "[먼저 확인]\n"
                                    "- 저녁 식사량을 이어서 확인합니다. [1]\n"
                                    "[이미 한 일]\n"
                                    "- 점심 식사 후 물을 제공했습니다. [1]\n"
                                    "[다음 업무 제안]\n"
                                    "- 저녁 식사량을 확인합니다. [1]"
                                ),
                                "references": [1],
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(local_ai, "_request_json", request_json)

    result = local_ai._room_summary_with_nvidia(
        [
            {
                "number": 1,
                "resident": "시설(가명)001",
                "body": (
                    "시설(가명)001 어르신이 점심 식사를 절반 드신 뒤 "
                    "물을 제공했고 저녁 식사량을 이어서 확인합니다."
                ),
            }
        ],
        purpose="간호 기록",
    )

    assert "시설(가명)001" in result
    assert request_options["timeout_seconds"] == 33
    assert request_options["retry_transient"] is False
    assert captured["max_tokens"] == 700


def test_nemotron_work_review_uses_json_mode_and_restores_resident_alias(
    monkeypatch,
    tmp_path,
):
    captured: dict = {}
    request_options: dict = {}
    key_file = tmp_path / "nvidia.key"
    key_file.write_text("test-key", encoding="utf-8")
    monkeypatch.setattr(local_ai.settings, "nvidia_api_key", None)
    monkeypatch.setattr(local_ai.settings, "nvidia_api_key_file", str(key_file))

    def request_json(**kwargs):
        request_options.update(kwargs)
        captured.update(kwargs["payload"])
        aliased_draft = {
            **VALID_DRAFT,
            "corrected_text": VALID_DRAFT["corrected_text"].replace(
                "시설(가명)001",
                "RESIDENT_001",
            ),
        }
        return {
            "choices": [
                {
                    "message": {
                        "content": local_ai.json.dumps(
                            aliased_draft,
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(local_ai, "_request_json", request_json)

    result = local_ai._review_with_nvidia(
        model="nvidia/test-ultra",
        user_payload={"current_draft": VALID_DRAFT},
    )

    assert result["corrected_text"].startswith("시설(가명)001")
    assert "시설(가명)001" not in captured["messages"][1]["content"]
    assert "RESIDENT_001" in captured["messages"][1]["content"]
    assert captured["chat_template_kwargs"] == {"enable_thinking": False}
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["temperature"] == 0.0
    assert request_options["retry_transient"] is True


def test_resident_alias_supports_daycare_display_name():
    value = {
        "resident": "주간-어르신-01(가명)",
        "body": "주간-어르신-01(가명) 어르신이 프로그램에 참여했습니다.",
    }

    aliased, replacements = local_ai._alias_resident_names(value)

    assert aliased["resident"] == "RESIDENT_001"
    assert "주간-어르신-01(가명)" not in aliased["body"]
    restored = local_ai._restore_resident_names(
        local_ai.json.dumps(aliased, ensure_ascii=False),
        replacements,
    )
    assert "주간-어르신-01(가명)" in restored


def test_general_room_summary_rejects_invented_critical_fact_without_headings():
    entries = [
        {
            "number": 1,
            "resident": "주간-어르신-01(가명)",
            "body": "주간-어르신-01(가명) 어르신이 프로그램에 참여했습니다.",
        }
    ]
    summary = (
        "주간-어르신-01(가명) 어르신이 프로그램에 참여했으며 "
        "낙상 위험이 있어 확인이 필요합니다. [1]"
    )

    with pytest.raises(local_ai.LocalAiError, match="원문에 없는 내용"):
        local_ai._validate_record_summary_faithfulness(
            summary,
            entries,
            require_headings=False,
        )


def test_record_summary_accepts_message_type_that_was_sent_to_ai():
    entries = [
        {
            "number": 1,
            "resident": "시설(가명)007",
            "type": "상담",
            "body": "보호자에게 최근 보행 상태와 낮 시간 활동을 설명했습니다.",
        }
    ]
    summary = (
        "[한눈에 보기]\n시설(가명)007 보호자 상담을 진행했습니다. [1]\n"
        "[먼저 확인]\n- 즉시 확인할 변화 없음\n"
        "[이미 한 일]\n- 보호자에게 보행 상태와 활동을 설명했습니다. [1]\n"
        "[다음 업무 제안]\n- 원문에 명시된 후속 요청은 없습니다."
    )

    local_ai._validate_record_summary_faithfulness(summary, entries)


def test_room_summary_rejects_output_longer_than_display_limit():
    content = local_ai.json.dumps(
        {
            "summary": "가" * 6001 + " [1]",
            "references": [1],
        },
        ensure_ascii=False,
    )

    with pytest.raises(local_ai.LocalAiError, match="너무 길어"):
        local_ai._validated_room_summary(content, source_count=1)


def test_request_json_retries_one_transient_connection_error(monkeypatch):
    calls: list[int] = []
    delays: list[float] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(request, timeout):
        del request, timeout
        calls.append(1)
        if len(calls) == 1:
            raise local_ai.URLError("temporary")
        return FakeResponse()

    monkeypatch.setattr(local_ai, "urlopen", fake_urlopen)
    monkeypatch.setattr(local_ai, "sleep", delays.append)

    result = local_ai._request_json(
        url="https://example.invalid/v1/chat/completions",
        payload={"model": "test"},
        timeout_seconds=10,
        retry_transient=True,
    )

    assert result == {"ok": True}
    assert len(calls) == 2
    assert delays == [1.0]


def test_room_summary_chain_falls_back_when_nemotron_quality_fails(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(local_ai.settings, "ai_review_provider", "chain")
    monkeypatch.setattr(local_ai.settings, "ai_review_external_enabled", True)
    monkeypatch.setattr(local_ai.settings, "ai_review_local_models", "qwen3.6:35b")

    def fail_nemotron(*args, **kwargs):
        calls.append("nvidia")
        raise local_ai.LocalAiError("근거 번호 없음")

    def local_summary(*args, **kwargs):
        calls.append(f"ollama:{kwargs['model']}")
        return "주요 내용: 식사량을 확인했습니다. [1]"

    monkeypatch.setattr(local_ai, "_room_summary_with_nvidia", fail_nemotron)
    monkeypatch.setattr(local_ai, "_room_summary_with_ollama", local_summary)

    result = local_ai.summarize_room_messages(
        entries=[{"number": 1, "body": "점심 식사를 절반 드셨습니다."}],
        external_allowed=True,
    )

    assert calls == ["nvidia", "ollama:qwen3.6:35b"]
    assert result.provider == "ollama"
    assert result.model == "qwen3.6:35b"


def test_record_summary_rejects_invented_measurement_and_fact():
    entries = [
        {
            "number": 1,
            "resident": "시설(가명)003",
            "body": "점심 식사는 3분의 2 정도 섭취했고 물 150ml를 추가 제공했습니다.",
        }
    ]
    summary = (
        "[한눈에 보기]\n시설(가명)003의 식사와 수분 기록입니다. [1]\n"
        "[먼저 확인]\n- 30분 안에 소변을 보지 못했습니다. [1]\n"
        "[이미 한 일]\n- 물 150ml를 제공했습니다. [1]\n"
        "[다음 업무 제안]\n- 경과를 확인합니다. [1]"
    )

    try:
        local_ai._validate_record_summary_faithfulness(summary, entries)
    except local_ai.LocalAiError as exc:
        assert "원문에 없는" in str(exc)
        return
    raise AssertionError("원문에 없는 수치와 배설 내용이 승인되었습니다.")


def test_record_summary_allows_concise_compression_with_required_briefing_sections():
    entries = [
        {
            "number": 1,
            "resident": "시설(가명)012",
            "body": "보행 중 비틀거려 부축하고 간호팀에 전달했습니다.",
        }
    ]
    summary = (
        "[한눈에 보기]\n시설(가명)012의 이동 안전 확인이 필요합니다. [1]\n"
        "[먼저 확인]\n- 이동 상태를 확인합니다. [1]\n"
        "[이미 한 일]\n- 부축 후 간호팀에 전달했습니다. [1]\n"
        "[다음 업무 제안]\n- 추가 지시 없음. [1]"
    )

    local_ai._validate_record_summary_faithfulness(summary, entries)


def test_record_summary_allows_proven_field_synonyms_without_relaxing_other_facts():
    entries = [
        {
            "number": 1,
            "resident": "시설(가명)003",
            "body": (
                "시설(가명)003 어르신께 물 150ml를 제공했고 "
                "엉치 부위가 붉게 보여 간호팀에 전달했습니다."
            ),
        }
    ]
    summary = (
        "[한눈에 보기]\n"
        "시설(가명)003의 수분 제공과 엉치 발적을 확인했습니다. [1]\n"
        "[먼저 확인]\n- 엉치 발적 변화를 확인합니다. [1]\n"
        "[이미 한 일]\n- 수분 150ml를 제공하고 간호팀에 전달했습니다. [1]\n"
        "[다음 업무 제안]\n- 엉치 발적 변화를 이어서 확인합니다. [1]"
    )

    local_ai._validate_record_summary_faithfulness(summary, entries)


def test_record_summary_synonyms_do_not_allow_unrelated_critical_fact():
    entries = [
        {
            "number": 1,
            "resident": "시설(가명)003",
            "body": "시설(가명)003 어르신께 물 150ml를 제공했습니다.",
        }
    ]
    summary = (
        "[한눈에 보기]\n시설(가명)003의 수분 기록입니다. [1]\n"
        "[먼저 확인]\n- 탈수 여부를 확인합니다. [1]\n"
        "[이미 한 일]\n- 수분 150ml를 제공했습니다. [1]\n"
        "[다음 업무 제안]\n- 수분 섭취를 확인합니다. [1]"
    )

    with pytest.raises(local_ai.LocalAiError, match="탈수"):
        local_ai._validate_record_summary_faithfulness(summary, entries)


def test_safe_record_summary_keeps_every_selected_entry_without_system_metadata():
    entries = [
        {
            "number": 1,
            "resident": "시설(가명)003",
            "body": (
                "[SUBMISSION-MEDIA-20260729] 보고서 이미지\n"
                "[이미지 글자 판독 · sample.jpg]\n"
                "점심 식사는 절반 섭취했습니다."
            ),
        },
        {
            "number": 2,
            "resident": "시설(가명)012",
            "body": "보행 중 비틀거려 부축했습니다.",
        },
    ]

    summary = local_ai._safe_selected_record_summary(
        entries,
        purpose="급여제공 기록",
    )

    assert "시설(가명)003" in summary
    assert "시설(가명)012" in summary
    assert "[1]" in summary
    assert "[2]" in summary
    assert "SUBMISSION-MEDIA" not in summary
    assert "sample.jpg" not in summary


def test_safe_record_summary_shows_risk_and_completed_action_in_both_sections():
    entries = [
        {
            "number": 1,
            "resident": "시설(가명)012",
            "body": (
                "복도에서 비틀거려 부축했습니다. "
                "의자에서 쉬도록 도왔습니다. "
                "저녁 이동 때 보행 상태를 다시 확인하겠습니다."
            ),
        }
    ]

    summary = local_ai._safe_selected_record_summary(
        entries,
        purpose="간호 기록",
    )
    first = summary.split("[먼저 확인]", 1)[1].split("[이미 한 일]", 1)[0]
    completed = summary.split("[이미 한 일]", 1)[1].split("[다음 업무 제안]", 1)[0]
    pending = summary.split("[다음 업무 제안]", 1)[1]

    assert "비틀거려 부축했습니다" in first
    assert "비틀거려 부축했습니다" in completed
    assert "의자에서 쉬도록 도왔습니다" in completed
    assert "저녁 이동 때 보행 상태를 다시 확인하겠습니다" in pending
    assert "확인하겠습니다" not in completed
    assert "[1]" in first
    assert "[1]" in completed
    assert summary.count("비틀거려 부축했습니다") == 2
    assert summary.count("의자에서 쉬도록 도왔습니다") == 1
    assert summary.count("저녁 이동 때 보행 상태를 다시 확인하겠습니다") == 1


def test_safe_record_summary_reports_overflow_count_and_evidence_numbers():
    entries = [
        {
            "number": index,
            "resident": f"시설(가명){index:03d}",
            "body": f"{index}번 어르신이 통증을 호소했습니다.",
        }
        for index in range(1, 9)
    ]

    summary = local_ai._safe_selected_record_summary(
        entries,
        purpose="간호 기록",
    )
    first = summary.split("[먼저 확인]", 1)[1].split("[이미 한 일]", 1)[0]

    assert "추가 2건이 있습니다." in first
    assert "근거 [7][8]" in first


def test_freeform_ai_summary_is_normalized_to_actionable_briefing():
    entries = [
        {
            "number": 1,
            "resident": "시설(가명)017",
            "body": (
                "기저귀 교환 중 엉치 부위가 붉게 보여 체위를 변경하고 "
                "간호팀에 전달했습니다. 다음 교환 때 발적 범위를 다시 확인해 주세요."
            ),
        }
    ]

    summary = local_ai._ensure_record_briefing_sections(
        "시설(가명)017의 엉치 부위 발적을 확인하고 체위를 변경했습니다. [1]",
        entries,
        purpose="간호 기록",
    )

    assert summary.startswith("[한눈에 보기]")
    assert "[먼저 확인]" in summary
    assert "[이미 한 일]" in summary
    assert "[다음 업무 제안]" in summary
    assert "발적 범위" in summary


def test_record_summary_chain_uses_complete_safe_result_after_bad_nemotron(
    monkeypatch,
):
    calls: list[str] = []
    monkeypatch.setattr(local_ai.settings, "ai_review_provider", "chain")
    monkeypatch.setattr(local_ai.settings, "ai_review_external_enabled", True)
    monkeypatch.setattr(local_ai.settings, "ai_review_local_models", "qwen3.6:35b")

    def fail_nemotron(*args, **kwargs):
        calls.append("nvidia")
        raise local_ai.LocalAiError("원문에 없는 사실")

    def local_summary(*args, **kwargs):
        calls.append("ollama")
        raise AssertionError("기록 정리는 느린 2차 AI를 기다리지 않아야 합니다.")

    monkeypatch.setattr(local_ai, "_room_summary_with_nvidia", fail_nemotron)
    monkeypatch.setattr(local_ai, "_room_summary_with_ollama", local_summary)
    entries = [
        {
            "number": 1,
            "resident": "시설(가명)003",
            "body": "점심 식사는 절반 섭취했습니다.",
        },
        {
            "number": 2,
            "resident": "시설(가명)012",
            "body": "보행 중 비틀거려 부축했습니다.",
        },
    ]

    result = local_ai.summarize_room_messages(
        entries=entries,
        external_allowed=True,
        purpose="급여제공 기록",
    )

    assert calls == ["nvidia"]
    assert result.provider == "safe"
    assert result.model == "safe-briefing-v2"
    assert all(f"[{number}]" in result.summary for number in (1, 2))
    assert all(entry["resident"] in result.summary for entry in entries)
    assert all(
        heading in result.summary
        for heading in (
            "[한눈에 보기]",
            "[먼저 확인]",
            "[이미 한 일]",
            "[다음 업무 제안]",
        )
    )


def test_record_summary_rejects_invented_diagnosis():
    entries = [
        {
            "number": 1,
            "resident": "시설(가명)036",
            "body": "혈압 160/88 확인 후 간호팀에 전달했습니다.",
        }
    ]
    summary = (
        "[한눈에 보기]\n시설(가명)036의 고혈압 진단 관련 기록입니다. [1]\n"
        "[먼저 확인]\n- 혈압 160/88로 확인됐습니다. [1]\n"
        "[이미 한 일]\n- 간호팀에 전달했습니다. [1]\n"
        "[다음 업무 제안]\n- 원문을 확인합니다. [1]"
    )

    try:
        local_ai._validate_record_summary_faithfulness(summary, entries)
    except local_ai.LocalAiError as exc:
        assert "원문에 없는" in str(exc)
        return
    raise AssertionError("원문에 없는 진단이 승인되었습니다.")


def test_record_summary_rejects_invented_slash_measurement():
    entries = [
        {
            "number": 1,
            "resident": "시설(가명)036",
            "body": "혈압 160/88 확인 후 간호팀에 전달했습니다.",
        }
    ]
    summary = (
        "[한눈에 보기]\n시설(가명)036의 혈압 확인 기록입니다. [1]\n"
        "[먼저 확인]\n- 혈압 168/91로 확인됐습니다. [1]\n"
        "[이미 한 일]\n- 간호팀에 전달했습니다. [1]\n"
        "[다음 업무 제안]\n- 원문을 확인합니다. [1]"
    )

    try:
        local_ai._validate_record_summary_faithfulness(summary, entries)
    except local_ai.LocalAiError as exc:
        assert "수치" in str(exc)
        return
    raise AssertionError("원문에 없는 혈압 수치가 승인되었습니다.")


def test_record_summary_rejects_reversing_negative_pain_and_fall_facts():
    entries = [
        {
            "number": 1,
            "resident": "시설(가명)012",
            "body": (
                "복도에서 잠시 비틀거려 부축했습니다. "
                "넘어지지는 않았고 통증 호소도 없었습니다."
            ),
        }
    ]
    summary = (
        "[한눈에 보기]\n시설(가명)012가 넘어졌고 통증이 있습니다. [1]\n"
        "[먼저 확인]\n- 넘어짐과 통증을 확인합니다. [1]\n"
        "[이미 한 일]\n- 부축했습니다. [1]\n"
        "[다음 업무 제안]\n- 상태를 다시 확인합니다. [1]"
    )

    try:
        local_ai._validate_record_summary_faithfulness(summary, entries)
    except local_ai.LocalAiError as exc:
        assert "부정 표현" in str(exc)
        assert "통증" in str(exc)
        assert "넘어짐" in str(exc)
        return
    raise AssertionError("원문의 통증 없음·넘어지지 않음이 반대로 승인되었습니다.")
