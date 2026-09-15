from pathlib import Path
from io import BytesIO
import json
from types import SimpleNamespace

import pytest
from PIL import Image
from pydantic import SecretStr

from app import ocr
from app.ai_assist_schemas import AiAssistResult, AiImageVisibleText


def test_name_region_candidates_are_ordered_and_roster_limited() -> None:
    parsed = ocr.parse_name_region_candidates(
        "설명 문장\n이름후보: 김종숙|가상갯,가상갬\n이름후보: 이애자|가상걻,명단밖\n",
        roster_names=["가상갯", "가상갬", "가상걻"],
    )

    assert [item.slot_index for item in parsed] == [1, 2]
    assert parsed[0].recognized == "김종숙"
    assert parsed[0].candidates == ("가상갯", "가상갬")
    assert parsed[1].candidates == ("가상걻",)


def test_name_region_candidates_keep_explicit_layout_slot_numbers() -> None:
    parsed = ocr.parse_name_region_candidates(
        "이름후보 3: 흐린글자|가나다,라마바\n이름후보 7: 또다른글자|라마바사",
        roster_names=["가나다", "라마바", "라마바사"],
    )

    assert [item.slot_index for item in parsed] == [3, 7]


def test_name_region_candidates_accept_safe_markdown_and_slot_variants() -> None:
    parsed = ocr.parse_name_region_candidates(
        "- **이름 후보 슬롯 3：** 흐린 글자｜가나다，명단외\n"
        "* `슬롯 번호 7: 또 다른 글자|라마바,명단외`\n"
        "1. 이름후보 슬롯번호9: 세 번째 글자|라마바사",
        roster_names=["가나다", "라마바", "라마바사"],
    )

    assert [item.slot_index for item in parsed] == [3, 7, 9]
    assert [item.recognized for item in parsed] == [
        "흐린글자",
        "또다른글자",
        "세번째글자",
    ]
    assert [item.candidates for item in parsed] == [
        ("가나다",),
        ("라마바",),
        ("라마바사",),
    ]


def test_name_region_candidates_reject_prose_and_duplicate_slots() -> None:
    parsed = ocr.parse_name_region_candidates(
        "설명: 흐린글자|가나다\n"
        "슬롯 2: 첫글자|가나다\n"
        "- 이름후보 슬롯 2: 다른글자|라마바\n"
        "슬롯 0: 범위밖|가나다\n"
        "슬롯 41: 범위밖|가나다",
        roster_names=["가나다", "라마바"],
    )

    assert [(item.slot_index, item.candidates) for item in parsed] == [
        (2, ("가나다",)),
    ]


def test_name_region_candidates_keep_repeated_reading_in_different_slots() -> None:
    parsed = ocr.parse_name_region_candidates(
        "이름후보 2: 같은획|가나다\n이름후보 5: 같은획|가나다",
        roster_names=["가나다"],
    )

    assert [item.slot_index for item in parsed] == [2, 5]


def test_name_region_candidates_reject_unlisted_model_output() -> None:
    parsed = ocr.parse_name_region_candidates(
        "이름후보: 가짜이름|없는사람,또없는사람",
        roster_names=["실제이름"],
    )

    assert parsed == []


def test_name_region_candidates_accept_ollama_json_schema_output() -> None:
    parsed = ocr.parse_name_region_candidates(
        '{"slot":3,"recognized":"가나타","candidates":["가나다","라마바"]}',
        roster_names=["가나다", "라마바"],
    )

    assert [(item.slot_index, item.recognized, item.candidates) for item in parsed] == [
        (3, "가나타", ("가나다", "라마바"))
    ]


def test_meal_name_region_boxes_are_normalized_and_slot_limited() -> None:
    parsed = ocr.parse_meal_name_regions(
        "이름영역 슬롯2: 100,200,320,260\n"
        "이름영역 슬롯5: 540,210,760,270\n"
        "이름영역 슬롯7: 100,100,200,200\n"
        "이름영역 슬롯2: 0,0,1000,1000",
        expected_slot_indexes=[2, 5],
    )

    assert [(item.slot_index, item.normalized_bbox) for item in parsed] == [
        (2, (100, 200, 320, 260)),
        (5, (540, 210, 760, 270)),
    ]


def test_meal_name_region_boxes_accept_ollama_json_schema_output() -> None:
    parsed = ocr.parse_meal_name_regions(
        '{"regions":[{"slot":2,"bbox":[100,200,320,260]},'
        '{"slot":5,"bbox":[540,210,760,270]},'
        '{"slot":7,"bbox":[10,20,30,40]}]}',
        expected_slot_indexes=[2, 5],
    )

    assert [(item.slot_index, item.normalized_bbox) for item in parsed] == [
        (2, (100, 200, 320, 260)),
        (5, (540, 210, 760, 270)),
    ]


def test_report_text_regions_accept_only_expected_bounded_lines() -> None:
    parsed = ocr.parse_report_text_regions(
        "- 텍스트영역 줄번호 1: 100,120,700,170\n"
        "**줄 3： 80,300,920,370**\n"
        "줄 2: 0,0,1000,1000\n"
        "줄 9: 100,200,300,240",
        expected_line_indexes=[1, 2, 3],
    )

    assert [(item.line_index, item.normalized_bbox) for item in parsed] == [
        (1, (100, 120, 700, 170)),
        (3, (80, 300, 920, 370)),
    ]


def test_report_text_regions_reject_one_box_covering_multiple_lines() -> None:
    parsed = ocr.parse_report_text_regions(
        "텍스트영역 줄번호 1: 100,100,900,260",
        expected_line_indexes=[1],
    )

    assert parsed == []


def test_report_text_regions_accept_structured_json_and_reject_duplicates() -> None:
    parsed = ocr.parse_report_text_regions(
        '{"regions":['
        '{"line":1,"bbox":[100,120,700,170]},'
        '{"line":1,"bbox":[100,200,700,250]},'
        '{"line":2,"bbox":[120,210,720,265]}]}',
        expected_line_indexes=[1, 2],
    )

    assert [(item.line_index, item.normalized_bbox) for item in parsed] == [
        (1, (100, 120, 700, 170)),
        (2, (120, 210, 720, 265)),
    ]


def test_report_text_locator_splits_long_editor_draft_and_keeps_global_indexes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "report.png"
    Image.new("RGB", (800, 1000), "white").save(image_path)
    requested_indexes: list[list[int]] = []

    class FakeCodexWorkerClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

    def fake_analyze(
        _client: object,
        *,
        question: str,
        **_kwargs: object,
    ) -> str:
        lines = [
            int(line.split(".", 1)[0])
            for line in question.split("판독 줄 목록:\n", 1)[1].splitlines()
        ]
        requested_indexes.append(lines)
        return json.dumps(
            {
                "regions": [
                    {
                        "line": index,
                        "bbox": [50, index * 10, 700, index * 10 + 8],
                        "angle": -3.5 if index == 1 else 0,
                    }
                    for index in lines
                ]
            }
        )

    monkeypatch.setattr(ocr.settings, "ocr_provider", "codex_worker")
    monkeypatch.setattr(ocr, "CodexWorkerClient", FakeCodexWorkerClient)
    monkeypatch.setattr(ocr, "_analyze_name_image", fake_analyze)

    result = ocr.locate_report_text_regions(
        image_path,
        lines=[f"편집기에 보이는 줄 {index}" for index in range(1, 22)],
    )

    assert requested_indexes == [
        list(range(1, 9)),
        list(range(9, 17)),
        list(range(17, 22)),
    ]
    assert [item.line_index for item in result] == list(range(1, 22))
    assert result[0].rotation_degrees == -3.5
    assert result[1].rotation_degrees == 0


def test_report_text_region_parser_keeps_bounded_angle_and_resets_steep_angle() -> None:
    result = ocr.parse_report_text_regions(
        "\n".join(
            [
                "텍스트영역 줄번호 1: 100,100,700,150; 각도=-4.5",
                "텍스트영역 줄번호 2: 100,200,700,250; angle=24",
            ]
        ),
        expected_line_indexes=[1, 2],
    )

    assert [item.rotation_degrees for item in result] == [-4.5, 0]


def test_coordinate_locator_accepts_only_confirmed_template_name_roles() -> None:
    assert ocr._structured_name_slot_indexes(
        [
            "슬롯1=2행/status_checklist/status_checklist",
            "슬롯2=5행/meal_name_list/meal_name_list",
            "슬롯3=8행/narrative/narrative",
            "슬롯4=9행/일반/inline_name",
        ]
    ) == [1, 2]


def test_meal_name_slots_use_independent_roi_images_and_position_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "meal-report.png"
    Image.new("RGB", (800, 1000), "white").save(image_path)
    calls: list[tuple[str, tuple[int, int]]] = []
    analyze_calls: list[list[str]] = []

    class FakeCodexWorkerClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def analyze(self, **kwargs: object) -> SimpleNamespace:
            worker_images = kwargs["images"]
            analyze_calls.append([item.filename for item in worker_images])
            for worker_image in worker_images:
                with Image.open(BytesIO(worker_image.content)) as sent_image:
                    calls.append((worker_image.filename, sent_image.size))
            if worker_images[0].filename == "structured-name-locator.jpg":
                answer = (
                    "이름영역 슬롯2: 100,200,320,260\n이름영역 슬롯5: 540,210,760,270"
                )
                image_texts = []
            elif [item.filename for item in worker_images] == [
                "structured-name-slot-2.jpg",
                "structured-name-slot-5.jpg",
            ]:
                answer = "묶음 판독 완료"
                image_texts = [
                    # Even a worker that repeats slot 1 cannot move image 1 away
                    # from the slot selected by the validated locator.
                    AiImageVisibleText(
                        image_no=1,
                        filename="structured-name-slot-2.jpg",
                        text="이름후보 슬롯1: 흐린첫글자|가나다,라마바",
                    ),
                    AiImageVisibleText(
                        image_no=2,
                        filename="structured-name-slot-5.jpg",
                        text="이름후보: 흐린둘째글자|라마바사,명단외",
                    ),
                ]
            else:
                raise AssertionError(
                    "complete ROI coverage must not use full-page fallback"
                )
            return SimpleNamespace(
                result=AiAssistResult(
                    answer=answer,
                    visible_text=None,
                    image_texts=image_texts,
                )
            )

    monkeypatch.setattr(ocr, "CodexWorkerClient", FakeCodexWorkerClient)
    monkeypatch.setattr(ocr.settings, "ocr_provider", "codex_worker")
    monkeypatch.setattr(
        ocr.settings,
        "codex_worker_shared_token",
        SecretStr("test-worker-token-that-is-longer-than-32-characters"),
    )

    result = ocr.extract_report_name_candidates(
        image_path,
        roster_names=["가나다", "라마바", "라마바사"],
        layout_hints=[
            "슬롯2=3행/아침식사/meal_name_list",
            "슬롯5=4행/아침식사/meal_name_list",
        ],
    )

    assert [(item.slot_index, item.candidates) for item in result] == [
        (2, ("가나다", "라마바")),
        (5, ("라마바사",)),
    ]
    assert [item.normalized_bbox for item in result] == [
        (100, 200, 320, 260),
        (540, 210, 760, 270),
    ]
    assert [filename for filename, _size in calls] == [
        "structured-name-locator.jpg",
        "structured-name-slot-2.jpg",
        "structured-name-slot-5.jpg",
    ]
    assert analyze_calls == [
        ["structured-name-locator.jpg"],
        ["structured-name-slot-2.jpg", "structured-name-slot-5.jpg"],
    ]
    assert all(size != calls[0][1] for _filename, size in calls[1:])
    assert all(size[0] > size[1] for _filename, size in calls[1:])


def test_meal_locator_failure_uses_existing_full_page_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "meal-report.png"
    Image.new("RGB", (500, 700), "white").save(image_path)
    filenames: list[str] = []

    class FakeCodexWorkerClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def analyze(self, **kwargs: object) -> SimpleNamespace:
            filename = kwargs["images"][0].filename
            filenames.append(filename)
            answer = (
                "좌표를 찾지 못했습니다."
                if filename == "structured-name-locator.jpg"
                else "이름후보 슬롯3: 흐린글자|가나다,라마바"
            )
            return SimpleNamespace(
                result=AiAssistResult(
                    answer=answer,
                    visible_text=None,
                    image_texts=[],
                )
            )

    monkeypatch.setattr(ocr, "CodexWorkerClient", FakeCodexWorkerClient)
    monkeypatch.setattr(ocr.settings, "ocr_provider", "codex_worker")
    monkeypatch.setattr(
        ocr.settings,
        "codex_worker_shared_token",
        SecretStr("test-worker-token-that-is-longer-than-32-characters"),
    )

    result = ocr.extract_report_name_candidates(
        image_path,
        roster_names=["가나다", "라마바"],
        layout_hints=["슬롯3=3행/아침식사/meal_name_list"],
    )

    assert filenames == ["structured-name-locator.jpg", "name-region.jpg"]
    assert [(item.slot_index, item.candidates) for item in result] == [
        (3, ("가나다", "라마바")),
    ]


def test_extract_report_name_candidates_keeps_two_roster_limited_ranks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "report.png"
    Image.new("RGB", (200, 300), "white").save(image_path)

    class FakeCodexWorkerClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def analyze(self, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                result=AiAssistResult(
                    answer=(
                        "이름후보: 흐린글자|가나다,라마바\n"
                        "이름후보: 또다른글자|명단외,라마바사"
                    ),
                    visible_text=None,
                    image_texts=[],
                )
            )

    monkeypatch.setattr(ocr, "CodexWorkerClient", FakeCodexWorkerClient)
    monkeypatch.setattr(ocr.settings, "ocr_provider", "codex_worker")
    monkeypatch.setattr(
        ocr.settings,
        "codex_worker_shared_token",
        SecretStr("test-worker-token-that-is-longer-than-32-characters"),
    )

    result = ocr.extract_report_name_candidates(
        image_path,
        roster_names=["가나다", "라마바", "라마바사"],
    )

    assert [row.candidates for row in result] == [
        ("가나다", "라마바"),
        ("라마바사",),
    ]


def test_validate_model_text_rejects_excessive_repetition() -> None:
    repeated = "\n".join(["같은 문장"] * 12)

    with pytest.raises(ocr.OcrError, match="과도하게 반복"):
        ocr._validate_model_text(repeated)


def test_validate_model_text_ignores_no_text_response() -> None:
    assert ocr._validate_model_text("이미지에는 한글 텍스트가 없습니다.") == ""


def test_validate_model_text_removes_generated_image_explanation() -> None:
    result = ocr._validate_model_text(
        "김성리: 1일 3회 약 복용 잘 하셨음\n"
        "이미지에 표시된 텍스트는 다음과 같습니다:\n"
        "```5/10, 2/3, 4/5```\n"
        "이 텍스트는 분수 예시입니다."
    )

    assert result == "김성리: 1일 3회 약 복용 잘 하셨음"


def test_merge_ocr_band_texts_removes_only_exact_boundary_overlap() -> None:
    result = ocr._merge_ocr_band_texts(
        [
            "첫 줄\n겹친 줄",
            "겹친 줄\n다음 줄",
        ]
    )

    assert result == "첫 줄\n겹친 줄\n다음 줄"


def test_merge_ocr_band_texts_keeps_repeated_line_away_from_boundary() -> None:
    result = ocr._merge_ocr_band_texts(
        [
            "반복 말씀하심\n중간 내용",
            "다음 내용\n반복 말씀하심",
        ]
    )

    assert result == "반복 말씀하심\n중간 내용\n다음 내용\n반복 말씀하심"


def test_lexicon_keeps_names_terms_and_corrections_separate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lexicon_path = tmp_path / "lexicon.json"
    lexicon_path.write_text(
        json.dumps(
            {
                "resident_name_candidates": [
                    {"term": f"가명어르신{index}", "count": 1} for index in range(80)
                ],
                "organization_terms": [
                    {"term": "기저귀", "count": 45},
                    {"term": "속패드", "count": 4},
                ],
                "long_term_care_terms": ["확인", "교체"],
                "corrections": {
                    "락인": "확인",
                    "고체": "교체",
                    "속때르": "속패드",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ocr.settings, "ocr_lexicon_path", lexicon_path.as_posix())
    monkeypatch.setattr(
        ocr.settings,
        "smcodi_resident_lexicon_path",
        (tmp_path / "missing-residents.json").as_posix(),
    )

    candidates = ocr.find_spelling_candidates("락인 고체 속때르 기저귀")

    assert candidates == [
        {"recognized": "락인", "candidate": "확인"},
        {"recognized": "고체", "candidate": "교체"},
        {"recognized": "속때르", "candidate": "속패드"},
    ]


def test_extract_reviewed_corrections_only_learns_safe_single_tokens() -> None:
    assert ocr.extract_reviewed_corrections(
        "민병균 락인 후 속때르 교체",
        "민병균 확인 후 속패드 교체",
        excluded_terms=["민병균"],
    ) == [
        ("락인", "확인"),
        ("속때르", "속패드"),
    ]
    assert (
        ocr.extract_reviewed_corrections(
            "문장 전체를",
            "완전히 다른 여러 단어로 다시 작성했습니다",
        )
        == []
    )


def test_default_long_term_care_lexicon_is_used_without_local_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ocr.settings,
        "ocr_lexicon_path",
        (tmp_path / "missing-local-lexicon.json").as_posix(),
    )
    monkeypatch.setattr(
        ocr.settings,
        "smcodi_resident_lexicon_path",
        (tmp_path / "missing-residents.json").as_posix(),
    )

    candidates = ocr.find_spelling_candidates("손톱깍이로 깍아드림")

    assert {("손톱깍이로", "손톱깎이로"), ("깍아드림", "깎아드림")} <= {
        (item["recognized"], item["candidate"]) for item in candidates
    }


def test_audio_name_candidate_keeps_active_roster_name_and_filters_common_word(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ocr.settings,
        "ocr_lexicon_path",
        (tmp_path / "missing-local-lexicon.json").as_posix(),
    )
    monkeypatch.setattr(
        ocr.settings,
        "smcodi_resident_lexicon_path",
        (tmp_path / "missing-residents.json").as_posix(),
    )

    roster_name_candidates = ocr.find_spelling_candidates(
        "가상걼 어르신 식사 확인",
        preferred_terms=["가상걽"],
        source_kind="audio",
    )
    kim_busoon_candidates = ocr.find_spelling_candidates(
        "가상갇 어르신 점심 식사 확인",
        preferred_terms=["가상같"],
        source_kind="audio",
    )
    exact_kim_busoon_candidates = ocr.find_spelling_candidates(
        "가상같 어르신 점심 식사 확인",
        preferred_terms=["가상같"],
        source_kind="audio",
    )
    common_word_candidates = ocr.find_spelling_candidates(
        "오늘 기분이 좋다고 말씀하심",
        preferred_terms=["가상같"],
        source_kind="audio",
    )

    assert {"recognized": "가상걼", "candidate": "가상걽"} in roster_name_candidates
    assert {"recognized": "가상갇", "candidate": "가상같"} in kim_busoon_candidates
    assert not any(
        item["recognized"] == "가상같" for item in exact_kim_busoon_candidates
    )
    assert not any(
        item["candidate"].startswith("가상같") for item in common_word_candidates
    )


def test_ai_lexicon_context_includes_only_matching_situation_phrases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ocr.settings,
        "ocr_lexicon_path",
        (tmp_path / "missing-local-lexicon.json").as_posix(),
    )
    context = ocr.get_ai_lexicon_context("목욕 후 발톱 상태 확인")

    matches = context["matched_situations"]
    assert matches
    assert matches[0]["id"] == "nail_hygiene"
    assert "원문에 없는" in context["safety_rule"]


def test_assessment_document_ocr_uses_a_form_field_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "assessment-page.png"
    Image.new("RGB", (800, 1200), "white").save(image_path)
    captured: dict[str, object] = {}

    def fake_extract(
        source_path: Path,
        *,
        room_name: str,
        resident_name: str | None,
        prompt_override: str | None = None,
    ) -> str:
        captured.update(
            {
                "source_path": source_path,
                "room_name": room_name,
                "resident_name": resident_name,
                "prompt_override": prompt_override,
            }
        )
        return "처방전\n약품명: 합성정 10mg"

    monkeypatch.setattr(ocr.settings, "ocr_provider", "ollama")
    monkeypatch.setattr(ocr.settings, "ocr_image_bands", 1)
    monkeypatch.setattr(ocr, "_ollama_extract_single", fake_extract)

    result = ocr.extract_assessment_document_text(
        image_path,
        room_name="기초사정 제출 자료",
        resident_name="어르0001",
    )

    assert result == "처방전\n약품명: 합성정 10mg"
    prompt = str(captured["prompt_override"])
    assert "문서 제목" in prompt
    assert "표 머리글" in prompt
    assert "항목명: 보이는 값" in prompt
    assert "추측" in prompt
    assert "안내문" in prompt


def test_portrait_report_is_split_into_configured_bands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "report.jpg"
    Image.new("RGB", (600, 1200), "white").save(image_path)
    calls: list[str] = []

    def fake_extract(
        crop_path: Path,
        *,
        room_name: str,
        resident_name: str | None,
    ) -> str:
        del room_name, resident_name
        calls.append(crop_path.name)
        return f"{crop_path.name} 판독"

    monkeypatch.setattr(ocr.settings, "ocr_image_bands", 3)
    monkeypatch.setattr(ocr, "_ollama_extract_single", fake_extract)

    result = ocr._ollama_extract(
        image_path,
        room_name="시험방",
        resident_name=None,
    )

    assert calls == ["band-1.jpg", "band-2.jpg", "band-3.jpg"]
    assert result.splitlines() == [
        "band-1.jpg 판독",
        "band-2.jpg 판독",
        "band-3.jpg 판독",
    ]


def test_repetitive_band_is_split_and_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "report.jpg"
    Image.new("RGB", (600, 1200), "white").save(image_path)
    calls: list[str] = []

    def fake_extract(
        crop_path: Path,
        *,
        room_name: str,
        resident_name: str | None,
    ) -> str:
        del room_name, resident_name
        calls.append(crop_path.name)
        if crop_path.name == "band-2.jpg":
            raise ocr.OcrError(
                "판독문에 같은 문장이 과도하게 반복되어 결과를 폐기했습니다."
            )
        return f"{crop_path.name} 판독"

    monkeypatch.setattr(ocr.settings, "ocr_image_bands", 3)
    monkeypatch.setattr(ocr, "_ollama_extract_single", fake_extract)

    result = ocr._ollama_extract(
        image_path,
        room_name="시험방",
        resident_name=None,
    )

    assert calls == [
        "band-1.jpg",
        "band-2.jpg",
        "band-2-retry-1-1.jpg",
        "band-2-retry-1-2.jpg",
        "band-3.jpg",
    ]
    assert "band-2-retry-1-1.jpg 판독" in result
    assert "band-2-retry-1-2.jpg 판독" in result


def test_landscape_report_is_split_into_configured_bands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "landscape-report.jpg"
    Image.new("RGB", (1200, 800), "white").save(image_path)
    calls: list[str] = []

    def fake_extract(
        crop_path: Path,
        *,
        room_name: str,
        resident_name: str | None,
    ) -> str:
        del room_name, resident_name
        calls.append(crop_path.name)
        return f"{crop_path.name} 판독"

    monkeypatch.setattr(ocr.settings, "ocr_image_bands", 3)
    monkeypatch.setattr(ocr, "_ollama_extract_single", fake_extract)

    result = ocr._ollama_extract(
        image_path,
        room_name="시험방",
        resident_name=None,
    )

    assert calls == [
        "band-1.jpg",
        "band-2.jpg",
        "band-3.jpg",
    ]
    assert result.splitlines() == [
        "band-1.jpg 판독",
        "band-2.jpg 판독",
        "band-3.jpg 판독",
    ]


def test_codex_worker_extract_uses_validated_single_image_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "report.jpg"
    Image.new("RGB", (200, 300), "white").save(image_path)
    captured: dict[str, object] = {}

    class FakeCodexWorkerClient:
        def __init__(self, **kwargs: object) -> None:
            captured["client"] = kwargs

        def analyze(self, **kwargs: object) -> SimpleNamespace:
            captured["analysis"] = kwargs
            return SimpleNamespace(
                result=AiAssistResult(
                    answer="판독했습니다.",
                    visible_text="잘못된 합본",
                    image_texts=[
                        AiImageVisibleText(
                            image_no=1,
                            filename="report.jpg",
                            text="원본 판독문",
                        )
                    ],
                )
            )

    monkeypatch.setattr(ocr, "CodexWorkerClient", FakeCodexWorkerClient)
    monkeypatch.setattr(
        ocr.settings,
        "codex_worker_shared_token",
        SecretStr("test-worker-token-that-is-longer-than-32-characters"),
    )
    monkeypatch.setattr(
        ocr.settings,
        "ocr_base_url",
        "http://codex-worker:8767",
    )

    result = ocr._codex_worker_extract(
        image_path,
        room_name="시험방",
        resident_name="시험 어르신",
    )

    assert result == "원본 판독문"
    analysis = captured["analysis"]
    assert isinstance(analysis, dict)
    assert analysis["task_type"] == "image_text"
    assert analysis["images"][0].filename == "report-enhanced.jpg"
    assert analysis["images"][0].mime_type == "image/jpeg"
    assert "시험 어르신" not in analysis["question"]


def test_prepare_codex_report_image_keeps_original_and_limits_long_edge(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "large.png"
    Image.new("RGB", (3200, 4000), "#eeeeee").save(image_path)
    original = image_path.read_bytes()

    prepared = ocr._prepare_codex_report_image(image_path)

    assert image_path.read_bytes() == original
    with Image.open(BytesIO(prepared)) as image:
        assert max(image.size) == 2800
        assert image.format == "JPEG"
