from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from app import services


def _attachment_with_extraction():
    extraction = SimpleNamespace(
        status="completed",
        provider="test",
        model_name="test-model",
        extracted_text="시험 판독문",
        original_extracted_text="시험 판독문",
        reviewed_text=None,
        reviewed_by=None,
        error_message=None,
        completed_at=datetime.now(timezone.utc),
        reviewed_at=None,
        visual_signature=None,
    )
    message = SimpleNamespace(
        id=uuid4(),
        sender_id=uuid4(),
        resident=None,
        resident_links=[],
    )
    return SimpleNamespace(
        id=uuid4(),
        message_id=message.id,
        uploader_id=message.sender_id,
        original_name="report.png",
        mime_type="image/png",
        size_bytes=128,
        text_extraction=extraction,
        message=message,
    )


def test_message_list_attachment_skips_expensive_spelling_scan(monkeypatch):
    attachment = _attachment_with_extraction()

    def unexpected_scan(*_args, **_kwargs):
        raise AssertionError("목록 응답에서 맞춤법 후보를 계산하면 안 됩니다.")

    monkeypatch.setattr(services, "find_spelling_candidates", unexpected_scan)

    response = services.attachment_response(
        attachment,
        include_review_details=False,
    )

    assert response.text_extraction is not None
    assert response.text_extraction.extracted_text == "시험 판독문"
    assert response.text_extraction.spelling_candidates == []


def test_attachment_detail_keeps_spelling_scan(monkeypatch):
    attachment = _attachment_with_extraction()
    calls = 0

    def spelling_scan(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return []

    monkeypatch.setattr(services, "find_spelling_candidates", spelling_scan)

    services.attachment_response(
        attachment,
        include_review_details=True,
    )

    assert calls == 1


def test_message_detail_attachment_can_skip_expensive_review_scan(monkeypatch):
    attachment = _attachment_with_extraction()

    def unexpected_scan(*_args, **_kwargs):
        raise AssertionError("상세 첫 화면에서 맞춤법 후보를 계산하면 안 됩니다.")

    monkeypatch.setattr(services, "find_spelling_candidates", unexpected_scan)

    response = services.attachment_response(
        attachment,
        include_review_details=False,
    )

    assert response.text_extraction is not None
    assert response.text_extraction.spelling_candidates == []
    assert response.text_extraction.previous_attempts == []


def test_standard_chat_attachment_exposes_only_confirmed_text():
    attachment = _attachment_with_extraction()
    extraction = attachment.text_extraction
    extraction.status = "reviewed"
    extraction.reviewed_text = "직원이 확인한 최종 판독문"
    extraction.suggested_text = "AI 제안문"
    extraction.error_message = "내부 판독 오류 상세"

    response = services.attachment_response(
        attachment,
        include_review_details=False,
        confirmed_text_only=True,
    )

    assert response.text_extraction is not None
    assert response.text_extraction.latest_confirmed_text == "직원이 확인한 최종 판독문"
    assert response.text_extraction.extracted_text is None
    assert response.text_extraction.original_extracted_text is None
    assert response.text_extraction.suggested_text is None
    assert response.text_extraction.reviewed_text is None
    assert response.text_extraction.error_message is None
    assert response.text_extraction.provider == ""
    assert response.text_extraction.model_name == ""


def test_pathological_handwriting_is_hidden_with_safe_preprocessing_metadata():
    attachment = _attachment_with_extraction()
    extraction = attachment.text_extraction
    extraction.status = "failed"
    extraction.extracted_text = "[표식] " * 80
    extraction.original_extracted_text = extraction.extracted_text
    extraction.error_message = "이면지 글자 간섭 또는 반복 출력으로 판독하지 못했습니다."
    extraction.suggestion_details = [
        {
            "kind": "handwriting_preprocessing",
            "applied": True,
            "requires_staff_review": False,
            "reason": "safe_blue_ink_document_isolation",
            "selected_variant": "blue_ink_isolated",
            "input_label": "문서 영역의 파란 필기 정제본",
            "document_crop_applied": True,
            "output_blocked": True,
            "retry_count": 0,
            "model_call_limit": 1,
            "original_preserved": True,
            "external_transfer": False,
            "metrics": {"estimated_front_line_count": 8},
        }
    ]

    response = services.attachment_response(
        attachment,
        include_review_details=False,
    )

    assert response.text_extraction is not None
    assert response.text_extraction.extracted_text is None
    assert response.text_extraction.original_extracted_text is None
    assert response.text_extraction.preprocessing is not None
    assert response.text_extraction.preprocessing.output_blocked is True
    assert response.text_extraction.preprocessing.model_call_limit == 1
