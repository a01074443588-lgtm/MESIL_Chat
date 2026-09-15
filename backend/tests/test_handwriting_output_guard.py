from __future__ import annotations

import pytest

from app.ocr import (
    HANDWRITING_SAFE_FAILURE_MESSAGE,
    OcrError,
    is_pathological_handwriting_output,
    validate_handwriting_model_text,
)


def test_rejects_dominant_repeated_marker_without_returning_raw_text() -> None:
    raw = "[표식] " * 80 + "정상처럼 보이는 마지막 줄"

    with pytest.raises(OcrError, match="이면지 글자 간섭 또는 반복 출력") as error:
        validate_handwriting_model_text(raw, expected_line_count=8)

    assert str(error.value) == HANDWRITING_SAFE_FAILURE_MESSAGE
    assert "[표식]" not in str(error.value)
    assert is_pathological_handwriting_output(raw) is True


def test_rejects_repeated_date_viewer_text_and_line_relative_overflow() -> None:
    repeated_date = "2026년 8월 28일\n기록\n2026년 8월 28일"
    viewer_text = "사진을 확대하려면 마우스 휠 버튼으로 확대"
    overflow = "\n".join(f"서로 다른 기록 {index}" for index in range(40))

    for value in (repeated_date, viewer_text, overflow):
        with pytest.raises(OcrError):
            validate_handwriting_model_text(value, expected_line_count=4)


def test_accepts_short_unique_handwriting_result() -> None:
    result = "오후 2시 물 200mL 제공\n오후 4시 섭취 상태 확인 예정"

    assert (
        validate_handwriting_model_text(result, expected_line_count=8)
        == result
    )
    assert is_pathological_handwriting_output(result) is False
