from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw

from app.ocr_corrections import (
    CorrectionEvidence,
    build_correction_pairs,
    extract_page_visual_signature,
    suggest_from_confirmed_events,
)


def test_confirmed_event_returns_candidate_without_auto_applying():
    writer_id = uuid4()
    signature = [0.0, 0.6, 0.8]
    candidates = suggest_from_confirmed_events(
        "김성리 어르신 오전 투약 확인",
        context_text="김성리 어르신 오전 투약 확인",
        source_writer_id=writer_id,
        visual_signature=signature,
        evidences=[
            CorrectionEvidence(
                event_id=str(uuid4()),
                recognized_text="김성리",
                corrected_text="김성희",
                content_type="resident_name",
                context_text="김성리 어르신 오전 투약 확인",
                source_writer_id=str(writer_id),
                visual_signature=signature,
            )
        ],
        resident_names=["김성희"],
    )

    assert candidates
    assert candidates[0]["recognized"] == "김성리"
    assert candidates[0]["candidate"] == "김성희"
    assert candidates[0]["is_protected"] is True
    assert candidates[0]["auto_applicable"] is False
    assert "같은 작성자" in candidates[0]["reason"]
    assert "글씨 영역 유사" in candidates[0]["reason"]


def test_candidate_id_stays_stable_when_best_evidence_changes():
    first_event_id = str(uuid4())
    second_event_id = str(uuid4())
    common_evidence = {
        "recognized_text": "김성리",
        "corrected_text": "김성희",
        "content_type": "resident_name",
        "context_text": "김성리 어르신 오전 투약 확인",
        "source_writer_id": None,
        "visual_signature": None,
    }

    first_candidates = suggest_from_confirmed_events(
        "김성리 어르신 오전 투약 확인",
        context_text="김성리 어르신 오전 투약 확인",
        source_writer_id=None,
        visual_signature=None,
        evidences=[
            CorrectionEvidence(event_id=first_event_id, **common_evidence),
        ],
        resident_names=["김성희"],
    )
    reinforced_candidates = suggest_from_confirmed_events(
        "김성리 어르신 오전 투약 확인",
        context_text="김성리 어르신 오전 투약 확인",
        source_writer_id=None,
        visual_signature=None,
        evidences=[
            CorrectionEvidence(event_id=second_event_id, **common_evidence),
            CorrectionEvidence(event_id=first_event_id, **common_evidence),
        ],
        resident_names=["김성희"],
    )

    assert first_candidates[0]["id"] == reinforced_candidates[0]["id"]


def test_review_diff_classifies_name_time_and_keeps_raw_separate():
    raw_text = "김성리 어르신 9:05 투약 확인"
    corrected_text = "김성희 어르신 9:15 투약 확인"

    pairs = build_correction_pairs(
        raw_text,
        corrected_text,
        resident_names=["김성희"],
    )

    assert raw_text == "김성리 어르신 9:05 투약 확인"
    assert {
        (pair["recognized_text"], pair["corrected_text"], pair["content_type"])
        for pair in pairs
    } == {
        ("김성리", "김성희", "resident_name"),
        ("9:05", "9:15", "time"),
    }


def test_page_visual_signature_contains_only_small_numeric_feature(tmp_path: Path):
    image_path = tmp_path / "handwriting.png"
    image = Image.new("RGB", (240, 120), "white")
    draw = ImageDraw.Draw(image)
    draw.line((20, 40, 210, 80), fill="black", width=5)
    image.save(image_path)

    signature = extract_page_visual_signature(image_path)

    assert signature is not None
    assert len(signature) == 48 * 16
    assert all(isinstance(value, float) for value in signature)
