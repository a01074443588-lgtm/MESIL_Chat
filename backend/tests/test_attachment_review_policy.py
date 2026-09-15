"""Synthetic source gates: raw automatic text is visible only in its editor."""
from types import SimpleNamespace as NS
from datetime import datetime, timezone
from uuid import uuid4
import pytest

from app.attachment_review_policy import attachment_evidence_text, has_staff_review


def attachment(mime="audio/wav", status="completed", text="합성 받아쓰기", **values):
    extraction = NS(status=status, extracted_text=text, original_extracted_text=text,
        reviewed_text=None, reviewed_at=None, reviewed_by_id=None, suggestion_details=[])
    for key, value in values.items():
        setattr(extraction, key, value)
    return NS(id=uuid4(), mime_type=mime, text_extraction=extraction)


@pytest.mark.parametrize("mime", ["audio/wav", "application/pdf", "text/plain",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"])
def test_automatic_text_cannot_be_official_evidence(mime):
    item=attachment(mime)
    assert attachment_evidence_text(item) == ""
    assert item.text_extraction.extracted_text


@pytest.mark.parametrize("status", ["pending", "processing", "completed", "failed"])
def test_stale_review_text_alone_does_not_prove_review(status):
    item=attachment(status=status, reviewed_text="이전 수정문")
    assert not has_staff_review(item.text_extraction)
    assert attachment_evidence_text(item) == ""


def test_current_staff_review_only_uses_final_text():
    item=attachment(status="reviewed", reviewed_text="확인한 합성 문장",
        reviewed_at=datetime.now(timezone.utc), reviewed_by_id=uuid4())
    assert attachment_evidence_text(item) == "확인한 합성 문장"


def test_legacy_completed_with_explicit_review_metadata_is_compatible():
    item=attachment(status="completed", reviewed_text="확인한 합성 문장",
        reviewed_at=datetime.now(timezone.utc), reviewed_by_id=uuid4())
    assert attachment_evidence_text(item) == "확인한 합성 문장"


def test_image_safety_behavior_is_preserved():
    assert attachment_evidence_text(attachment("image/png")) == "합성 받아쓰기"

