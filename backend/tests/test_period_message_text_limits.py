from __future__ import annotations

from types import SimpleNamespace
from datetime import datetime, timezone
from uuid import uuid4

from app.main import _period_message_text
from app.prototype_ai import build_prototype_suggestion
from app.schemas import RecordDraft


def test_period_message_text_keeps_context_without_exceeding_record_contract() -> None:
    long_reviewed_text = "이전 기초사정 확인 항목입니다. " * 300
    attachment = SimpleNamespace(
        mime_type="application/pdf",
        original_name="previous-assessment.pdf",
        text_extraction=SimpleNamespace(
            status="reviewed",
            reviewed_by_id=uuid4(),
            reviewed_at=datetime.now(timezone.utc),
            reviewed_text=long_reviewed_text,
            extracted_text=long_reviewed_text,
        ),
    )
    message = SimpleNamespace(
        body="어르0001 이전 기초사정 자료를 확인했습니다.",
        attachments=[attachment],
    )
    comments = [
        SimpleNamespace(
            author=SimpleNamespace(full_name="간호01"),
            body="이후 현재 관찰 내용을 기록하겠습니다.",
        )
    ]

    text = _period_message_text(message, comments)

    assert len(text) <= 2_000
    assert "어르0001 이전 기초사정 자료를 확인했습니다." in text
    assert "이후 현재 관찰 내용을 기록하겠습니다." in text
    assert "previous-assessment.pdf" in text
    RecordDraft.model_validate(
        build_prototype_suggestion(
            {
                "body": text,
                "resident_name": "어르0001",
                "resident_names": ["어르0001"],
            }
        )
    )
