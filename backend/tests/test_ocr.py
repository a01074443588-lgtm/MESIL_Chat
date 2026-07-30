from pathlib import Path
import json

import pytest
from PIL import Image

from app import ocr


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
                    {"term": f"가명어르신{index}", "count": 1}
                    for index in range(80)
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
