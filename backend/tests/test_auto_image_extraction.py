import asyncio
from io import BytesIO
from threading import Barrier, Lock
from uuid import UUID

from app import main as main_module
from app import image_ocr_runtime
from app.confirmed_records import ConfirmedWorkRecord
from app.database import SessionLocal
from app.main import app
from app.models import (
    AttachmentCoordinateReview,
    AttachmentTextExtraction,
    OcrCorrectionEvent,
)
from app.ocr import NameRegionCandidate, ReportTextRegion
from app.ocr_corrections import ResidentRosterEntry
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select

ORIGIN = {"origin": "http://testserver"}


def _login_admin(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "AdminPass!234"},
        headers=ORIGIN,
    )
    assert response.status_code == 200, response.text


def _image_bytes(image_format: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (24, 24), "white").save(output, format=image_format)
    return output.getvalue()


def test_every_supported_image_is_general_without_report_image_selection(monkeypatch):
    scheduled: list[UUID] = []
    monkeypatch.setattr(
        main_module,
        "_run_new_attachment_processing",
        lambda attachment_id: scheduled.append(attachment_id),
    )
    supported = {
        "image/jpeg": ("jpg", "JPEG"),
        "image/png": ("png", "PNG"),
        "image/webp": ("webp", "WEBP"),
    }
    assert set(supported) == main_module.IMAGE_MIME_TYPES

    with TestClient(app) as client:
        _login_admin(client)
        room_id = client.get("/api/rooms").json()[0]["id"]
        attachment_ids: list[UUID] = []
        for mime_type, (extension, image_format) in supported.items():
            response = client.post(
                f"/api/rooms/{room_id}/messages-with-files",
                data={
                    "body": f"{extension} 자동 대기열 시험",
                    "message_type": "chat",
                    "report_image": "false",
                },
                files={
                    "files": (
                        f"automatic.{extension}",
                        _image_bytes(image_format),
                        mime_type,
                    )
                },
                headers=ORIGIN,
            )
            assert response.status_code == 201, response.text
            attachment_ids.append(UUID(response.json()["attachments"][0]["id"]))

    assert scheduled == []
    with SessionLocal() as db:
        queued = db.scalars(
            select(AttachmentTextExtraction).where(
                AttachmentTextExtraction.attachment_id.in_(attachment_ids)
            )
        ).all()
        assert queued == []


def test_new_attachment_batch_keeps_each_media_lane_order_and_runs_lanes_together(
    monkeypatch,
):
    image_ids = [UUID(int=101), UUID(int=102)]
    audio_ids = [UUID(int=201), UUID(int=202)]
    first_items = {image_ids[0], audio_ids[0]}
    lane_start = Barrier(2, timeout=2)
    lock = Lock()
    observed: list[UUID] = []

    def run_one(attachment_id: UUID) -> None:
        if attachment_id in first_items:
            lane_start.wait()
        with lock:
            observed.append(attachment_id)

    monkeypatch.setattr(main_module, "_run_new_attachment_processing", run_one)

    asyncio.run(
        main_module._run_new_attachment_processing_batch(
            [
                (image_ids[0], "image/jpeg"),
                (audio_ids[0], "audio/wav"),
                (image_ids[1], "image/png"),
                (audio_ids[1], "audio/wav"),
            ]
        )
    )

    assert [item for item in observed if item in image_ids] == image_ids
    assert [item for item in observed if item in audio_ids] == audio_ids


def test_opted_in_text_image_runs_extraction_after_upload(
    monkeypatch,
):
    with SessionLocal() as db:
        confirmed_record_count_before = db.scalar(
            select(func.count(ConfirmedWorkRecord.id))
        )
    calls: list[str] = []
    monkeypatch.setattr(main_module, "image_likely_contains_text", lambda _path: True)
    monkeypatch.setattr(
        main_module,
        "extract_handwriting_text",
        lambda path, **_kwargs: calls.append(path.name) or "합성 자동 판독 결과",
    )
    monkeypatch.setattr(
        main_module,
        "extract_report_name_candidates",
        lambda *_args, **_kwargs: [],
    )
    locator_calls: list[list[str]] = []
    monkeypatch.setattr(
        main_module,
        "locate_report_text_regions",
        lambda _path, *, lines: (
            locator_calls.append(lines)
            or [
                ReportTextRegion(
                    line_index=1,
                    normalized_bbox=(100, 120, 700, 180),
                    rotation_degrees=-4.5,
                )
            ]
        ),
    )

    with TestClient(app) as client:
        _login_admin(client)
        room_id = client.get("/api/rooms").json()[0]["id"]
        response = client.post(
            f"/api/rooms/{room_id}/messages-with-files",
            data={"report_image": "true", "body": "별도 선택 없는 자동 판독 실행 시험"},
            files={
                "files": (
                    "automatic-text.png",
                    _image_bytes("PNG"),
                    "image/png",
                )
            },
            headers=ORIGIN,
        )
        assert response.status_code == 201, response.text
        message_id = response.json()["id"]
        attachment_id = response.json()["attachments"][0]["id"]
        detail = client.get(f"/api/messages/{message_id}")
        coordinate_draft = client.get(
            f"/api/attachments/{attachment_id}/coordinate-review",
            headers=ORIGIN,
        )
        cached_auto_location = client.post(
            f"/api/attachments/{attachment_id}/coordinate-review/auto-locate",
            headers=ORIGIN,
        )
        review_details = client.get(f"/api/attachments/{attachment_id}/text-extraction")

    assert detail.status_code == 200, detail.text
    assert detail.json()["message"]["attachments"][0]["text_extraction"]["original_extracted_text"] is None
    assert review_details.status_code == 200
    extraction = review_details.json()["text_extraction"]
    assert extraction["status"] == "completed"
    assert extraction["original_extracted_text"] == "합성 자동 판독 결과"
    assert len(calls) == 1
    assert locator_calls == [["합성 자동 판독 결과"]]
    assert coordinate_draft.status_code == 200, coordinate_draft.text
    assert coordinate_draft.json()["regions"][0]["source"] == "auto_locator"
    assert coordinate_draft.json()["regions"][0]["bbox"]["rotation_degrees"] == -4.5
    assert cached_auto_location.status_code == 200, cached_auto_location.text
    assert cached_auto_location.json()["regions"][0]["source"] == "auto_locator"
    assert locator_calls == [["합성 자동 판독 결과"]]
    with SessionLocal() as db:
        attachment_id = UUID(detail.json()["message"]["attachments"][0]["id"])
        stored = db.scalar(
            select(AttachmentTextExtraction).where(
                AttachmentTextExtraction.attachment_id == attachment_id
            )
        )
        assert stored is not None
        location_detail = next(
            item
            for item in stored.suggestion_details or []
            if item.get("kind") == "coordinate_auto_location"
        )
        assert location_detail["status"] == "completed"
        assert location_detail["locations"][0]["line_index"] == 1
        assert location_detail["locations"][0]["bbox"]["rotation_degrees"] == -4.5
        assert (
            db.scalar(
                select(AttachmentCoordinateReview.id).where(
                    AttachmentCoordinateReview.attachment_id == attachment_id
                )
            )
            is None
        )
        assert (
            db.scalar(
                select(OcrCorrectionEvent.id).where(
                    OcrCorrectionEvent.attachment_id == attachment_id
                )
            )
            is None
        )
        assert db.scalar(select(func.count(ConfirmedWorkRecord.id))) == (
            confirmed_record_count_before
        )


def test_coordinate_failure_does_not_fail_or_confirm_successful_ocr(monkeypatch):
    monkeypatch.setattr(main_module, "image_likely_contains_text", lambda _path: True)
    monkeypatch.setattr(
        main_module,
        "extract_handwriting_text",
        lambda *_args, **_kwargs: "합성 판독 원문",
    )
    monkeypatch.setattr(
        main_module,
        "extract_report_name_candidates",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        main_module,
        "locate_report_text_regions",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            main_module.OcrError("합성 위치 탐지 실패")
        ),
    )

    with TestClient(app) as client:
        _login_admin(client)
        room_id = client.get("/api/rooms").json()[0]["id"]
        response = client.post(
            f"/api/rooms/{room_id}/messages-with-files",
            data={"report_image": "true", "body": "위치 실패와 OCR 성공 분리 시험"},
            files={
                "files": (
                    "location-failure.png",
                    _image_bytes("PNG"),
                    "image/png",
                )
            },
            headers=ORIGIN,
        )
        assert response.status_code == 201, response.text
        attachment_id = UUID(response.json()["attachments"][0]["id"])

    with SessionLocal() as db:
        stored = db.scalar(
            select(AttachmentTextExtraction).where(
                AttachmentTextExtraction.attachment_id == attachment_id
            )
        )
        assert stored is not None
        assert stored.status == "completed"
        assert stored.original_extracted_text == "합성 판독 원문"
        location_detail = next(
            item
            for item in stored.suggestion_details or []
            if item.get("kind") == "coordinate_auto_location"
        )
        assert location_detail == {
            "kind": "coordinate_auto_location",
            "version": "report-line-locator-2-angle",
            "status": "failed",
            "generated_at": location_detail["generated_at"],
            "locations": [],
        }
        assert (
            db.scalar(
                select(AttachmentCoordinateReview.id).where(
                    AttachmentCoordinateReview.attachment_id == attachment_id
                )
            )
            is None
        )
        assert (
            db.scalar(
                select(OcrCorrectionEvent.id).where(
                    OcrCorrectionEvent.attachment_id == attachment_id
                )
            )
            is None
        )


def test_enabled_combined_checklist_uses_template_draft_in_production_path(
    monkeypatch,
):
    raw = (
        "가나다: 흑인\n라마바: 교체\n\n"
        "<아침식사 하신 분>\n가나디: 라마바\n마바사: 가나다\n\n"
        "09:05 특이사항 원문"
    )
    monkeypatch.setattr(main_module.settings, "environment", "production")
    monkeypatch.setattr(image_ocr_runtime, "resolve_image_runtime", lambda **_kwargs: image_ocr_runtime.ImageOcrRuntime("stub", "test-ocr", ""))
    monkeypatch.setattr(
        main_module.settings,
        "ocr_template_engine_enabled",
        True,
    )
    monkeypatch.setattr(main_module, "image_likely_contains_text", lambda _path: True)
    monkeypatch.setattr(
        main_module,
        "extract_handwriting_text",
        lambda _path, **_kwargs: raw,
    )
    coordinate_calls: list[list[str]] = []

    def coordinate_names(*_args, **kwargs):
        coordinate_calls.append(kwargs["layout_hints"])
        return [
            NameRegionCandidate(
                recognized="가나디",
                candidates=("가나다", "라마바"),
                slot_index=3,
                normalized_bbox=(100, 300, 240, 350),
            )
        ]

    monkeypatch.setattr(
        main_module,
        "extract_report_name_candidates",
        coordinate_names,
    )
    monkeypatch.setattr(
        main_module,
        "locate_report_text_regions",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        main_module,
        "_attachment_roster_evidence",
        lambda *_args, **_kwargs: (
            [
                ResidentRosterEntry(name="가나다", service_type="daycare"),
                ResidentRosterEntry(name="라마바", service_type="daycare"),
                ResidentRosterEntry(name="마바사", service_type="daycare"),
            ],
            [],
        ),
    )

    with TestClient(app) as client:
        _login_admin(client)
        room_id = client.get("/api/rooms").json()[0]["id"]
        response = client.post(
            f"/api/rooms/{room_id}/messages-with-files",
            data={"report_image": "true", "body": "DEV 템플릿 경로 비식별 판독 시험"},
            files={
                "files": (
                    "combined-checklist.png",
                    _image_bytes("PNG"),
                    "image/png",
                )
            },
            headers=ORIGIN,
        )
        assert response.status_code == 201, response.text
        message_id = response.json()["id"]
        detail = client.get(f"/api/messages/{message_id}")
        review_details = client.get(f"/api/attachments/{response.json()['attachments'][0]['id']}/text-extraction")

    assert detail.status_code == 200, detail.text
    assert detail.json()["message"]["attachments"][0]["text_extraction"]["original_extracted_text"] is None
    assert review_details.status_code == 200
    extraction = review_details.json()["text_extraction"]
    assert extraction["original_extracted_text"] == raw
    assert "가나다 (?)" in extraction["suggested_text"]
    assert "가나다: 확인" in extraction["suggested_text"]
    assert "09:05 특이사항 원문" in extraction["suggested_text"]
    assert len(coordinate_calls) == 1
    assert any("/status_checklist" in hint for hint in coordinate_calls[0])
    assert any("/meal_name_list" in hint for hint in coordinate_calls[0])

    with SessionLocal() as db:
        stored = db.scalar(
            select(AttachmentTextExtraction).where(
                AttachmentTextExtraction.attachment_id
                == UUID(detail.json()["message"]["attachments"][0]["id"])
            )
        )
        assert stored is not None
        assert stored.reviewed_text is None
        assert stored.suggestion_details is not None
        assert stored.suggestion_details[0]["kind"] == "versioned_ocr_pipeline"
        assert stored.suggestion_details[0]["content_stage_id"] == (
            "full-page-content-ocr"
        )
        assert stored.suggestion_details[0]["profile_version"] == "1.1.0"
        assert (
            stored.suggestion_details[0]["outside_structured_slots_preserved"] is True
        )
        assert stored.suggestion_details[0]["auto_apply_allowed"] is False


def test_enabled_non_template_image_preserves_raw_and_skips_legacy_placeholders(
    monkeypatch,
):
    raw = (
        "아침에 설사를 하시고 09:05 상태를 확인함.\n"
        "담당자: 확인 필요\n"
        "일반 서술 첫 단어는 이름 슬롯이 아님."
    )
    monkeypatch.setattr(main_module.settings, "environment", "production")
    monkeypatch.setattr(image_ocr_runtime, "resolve_image_runtime", lambda **_kwargs: image_ocr_runtime.ImageOcrRuntime("stub", "test-ocr", ""))
    monkeypatch.setattr(main_module.settings, "ocr_template_engine_enabled", True)
    monkeypatch.setattr(main_module, "image_likely_contains_text", lambda _path: True)
    monkeypatch.setattr(
        main_module,
        "extract_handwriting_text",
        lambda _path, **_kwargs: raw,
    )
    monkeypatch.setattr(
        main_module,
        "extract_report_name_candidates",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("비템플릿 문서에서 legacy 이름 판독이 실행되면 안 됩니다.")
        ),
    )
    monkeypatch.setattr(
        main_module,
        "locate_report_text_regions",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        main_module,
        "_attachment_roster_evidence",
        lambda *_args, **_kwargs: (
            [
                ResidentRosterEntry(name="가나다", service_type="daycare"),
                ResidentRosterEntry(name="라마바", service_type="daycare"),
            ],
            [],
        ),
    )

    with TestClient(app) as client:
        _login_admin(client)
        room_id = client.get("/api/rooms").json()[0]["id"]
        response = client.post(
            f"/api/rooms/{room_id}/messages-with-files",
            data={"report_image": "true", "body": "비템플릿 원문 보존 시험"},
            files={
                "files": (
                    "narrative.png",
                    _image_bytes("PNG"),
                    "image/png",
                )
            },
            headers=ORIGIN,
        )
        assert response.status_code == 201, response.text
        message_id = response.json()["id"]
        detail = client.get(f"/api/messages/{message_id}")
        review_details = client.get(f"/api/attachments/{response.json()['attachments'][0]['id']}/text-extraction")

    assert detail.json()["message"]["attachments"][0]["text_extraction"]["original_extracted_text"] is None
    assert review_details.status_code == 200
    extraction = review_details.json()["text_extraction"]
    assert extraction["original_extracted_text"] == raw
    assert extraction["suggested_text"] is None
    assert "이름확인필요" not in (extraction["suggested_text"] or "")
