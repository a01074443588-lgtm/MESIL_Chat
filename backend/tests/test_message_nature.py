from uuid import UUID

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from app import main as main_module
from app.ai_analysis import (
    AiAnalysisEvidence,
    AiAnalysisEvidenceInput,
    AiAnalysisHistoryMutationError,
    AiAnalysisSnapshot,
    append_ai_analysis_snapshot,
    content_fingerprint,
)
from app.database import SessionLocal
from app.main import app
from app.message_nature import classify_message_nature
from app.models import Message


ORIGIN = {"origin": "http://testserver"}


def test_ordinary_conversation_stays_chat_without_a_manual_category():
    decision = classify_message_nature("점심 맛있게 드세요.")

    assert decision.message_type == "chat"
    assert decision.reason_codes == ("ordinary_chat",)


def test_explicit_follow_up_and_request_are_classified_by_meaning():
    handover = classify_message_nature("다음 근무자가 계속 관찰해 주세요.")
    request = classify_message_nature("보호자께 연락 부탁드립니다.")

    assert handover.message_type == "handover"
    assert "explicit_handover" in handover.reason_codes
    assert request.message_type == "work_request"
    assert "explicit_request" in request.reason_codes


def test_speaker_job_context_changes_a_care_observation_classification():
    ordinary = classify_message_nature("기침이 조금 심합니다.")
    caregiver = classify_message_nature(
        "기침이 조금 심합니다.",
        speaker_job_name="요양보호사",
    )

    assert ordinary.message_type == "chat"
    assert caregiver.message_type == "report"
    assert "care_observation_from_care_role" in caregiver.reason_codes


def test_social_worker_case_update_and_report_image_are_reports():
    case_update = classify_message_nature(
        "보호자 상담은 오후 3시로 예정되어 있습니다.",
        speaker_job_name="사회복지사",
    )
    report_image = classify_message_nature(
        "보고서 이미지를 첨부했습니다.",
        has_attachments=True,
        report_image=True,
    )

    assert case_update.message_type == "report"
    assert "case_update_from_social_work_role" in case_update.reason_codes
    assert report_image.message_type == "report"
    assert "report_image_selected" in report_image.reason_codes


def test_server_ignores_legacy_staff_hint_but_keeps_admin_notice():
    with TestClient(app) as client:
        logged_in = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "AdminPass!234"},
            headers=ORIGIN,
        )
        assert logged_in.status_code == 200, logged_in.text
        room_id = client.get("/api/rooms").json()[0]["id"]

        automatic = client.post(
            f"/api/rooms/{room_id}/messages",
            json={
                "body": "점심 맛있게 드세요.",
                "message_type": "work_request",
            },
            headers=ORIGIN,
        )
        notice = client.post(
            f"/api/rooms/{room_id}/messages",
            json={
                "body": "오늘 오후 전체 직원회의가 있습니다.",
                "message_type": "notice",
            },
            headers=ORIGIN,
        )

        assert automatic.status_code == 201, automatic.text
        assert automatic.json()["message_type"] == "chat"
        assert notice.status_code == 201, notice.text
        assert notice.json()["message_type"] == "notice"

        automatic_id = UUID(automatic.json()["id"])
        notice_id = UUID(notice.json()["id"])
        with SessionLocal() as db:
            automatic_message = db.get(Message, automatic_id)
            automatic_snapshot = db.scalar(
                select(AiAnalysisSnapshot).where(
                    AiAnalysisSnapshot.primary_message_id == automatic_id,
                    AiAnalysisSnapshot.analysis_kind == "message_nature",
                )
            )
            notice_snapshot = db.scalar(
                select(AiAnalysisSnapshot).where(
                    AiAnalysisSnapshot.primary_message_id == notice_id,
                    AiAnalysisSnapshot.analysis_kind == "message_nature",
                )
            )
            evidence = db.scalar(
                select(AiAnalysisEvidence).where(
                    AiAnalysisEvidence.snapshot_id == automatic_snapshot.id
                )
            )

            assert automatic_message is not None
            assert automatic_message.extra_data is None
            assert automatic_snapshot is not None
            assert automatic_snapshot.processor_kind == "local_rules"
            assert automatic_snapshot.result_payload["message_type"] == "chat"
            assert automatic_snapshot.transmitted_external is False
            assert automatic_snapshot.sequence_no == 1
            assert len(automatic_snapshot.input_fingerprint) == 64
            assert evidence is not None
            assert evidence.source_message_id == automatic_id
            assert evidence.content_sha256 == content_fingerprint(
                automatic_message.body
            )
            assert evidence.included_in_prompt is False
            assert evidence.source_meta == {}
            assert notice_snapshot is not None
            assert notice_snapshot.processor_kind == "admin_control"


def test_classification_failure_does_not_block_message_delivery(monkeypatch):
    def fail_classification(*args, **kwargs):
        raise RuntimeError("simulated classifier failure")

    monkeypatch.setattr(main_module, "classify_message_nature", fail_classification)

    with TestClient(app) as client:
        logged_in = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "AdminPass!234"},
            headers=ORIGIN,
        )
        assert logged_in.status_code == 200, logged_in.text
        room_id = client.get("/api/rooms").json()[0]["id"]

        sent = client.post(
            f"/api/rooms/{room_id}/messages",
            json={"body": "분류가 실패해도 이 메시지는 전송되어야 합니다."},
            headers=ORIGIN,
        )

        assert sent.status_code == 201, sent.text
        assert sent.json()["message_type"] == "chat"

        with SessionLocal() as db:
            snapshot = db.scalar(
                select(AiAnalysisSnapshot).where(
                    AiAnalysisSnapshot.primary_message_id == UUID(sent.json()["id"]),
                    AiAnalysisSnapshot.analysis_kind == "message_nature",
                )
            )
            assert snapshot is not None
            assert snapshot.processor_kind == "system_fallback"
            assert snapshot.confidence == 0.0
            assert snapshot.uncertainties
            assert snapshot.transmitted_external is False


def test_analysis_history_failure_does_not_block_message_delivery(monkeypatch):
    def fail_history(*args, **kwargs):
        raise RuntimeError("simulated analysis history failure")

    monkeypatch.setattr(main_module, "append_ai_analysis_snapshot", fail_history)

    with TestClient(app) as client:
        logged_in = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "AdminPass!234"},
            headers=ORIGIN,
        )
        assert logged_in.status_code == 200, logged_in.text
        room_id = client.get("/api/rooms").json()[0]["id"]

        sent = client.post(
            f"/api/rooms/{room_id}/messages",
            json={"body": "분석 이력 저장 장애가 있어도 전송되어야 합니다."},
            headers=ORIGIN,
        )

        assert sent.status_code == 201, sent.text
        message_id = UUID(sent.json()["id"])
        with SessionLocal() as db:
            assert db.get(Message, message_id) is not None
            snapshot = db.scalar(
                select(AiAnalysisSnapshot).where(
                    AiAnalysisSnapshot.primary_message_id == message_id,
                    AiAnalysisSnapshot.analysis_kind == "message_nature",
                )
            )
            assert snapshot is None


def test_analysis_history_adds_versions_and_rejects_mutation():
    with TestClient(app) as client:
        logged_in = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "AdminPass!234"},
            headers=ORIGIN,
        )
        assert logged_in.status_code == 200, logged_in.text
        room_id = client.get("/api/rooms").json()[0]["id"]
        sent = client.post(
            f"/api/rooms/{room_id}/messages",
            json={"body": "보호자께 연락 부탁드립니다."},
            headers=ORIGIN,
        )
        assert sent.status_code == 201, sent.text

    message_id = UUID(sent.json()["id"])
    with SessionLocal() as db:
        message = db.get(Message, message_id)
        assert message is not None
        first = db.scalar(
            select(AiAnalysisSnapshot).where(
                AiAnalysisSnapshot.primary_message_id == message_id,
                AiAnalysisSnapshot.analysis_kind == "message_nature",
            )
        )
        assert first is not None
        second = append_ai_analysis_snapshot(
            db,
            organization_id=message.organization_id,
            subject_type="message",
            subject_key=str(message.id),
            primary_message_id=message.id,
            analysis_kind="message_nature",
            processor_kind="local_rules",
            processor_version="message-nature-v2-test",
            input_payload={"body": message.body, "test_revision": 2},
            result_payload={"message_type": "work_request", "test_revision": 2},
            evidence=[
                AiAnalysisEvidenceInput(
                    source_type="message",
                    source_message_id=message.id,
                    evidence_role="primary",
                    content_sha256=content_fingerprint(message.body),
                )
            ],
            transmitted_external=False,
            is_test_data=message.is_test_data,
        )
        db.commit()
        assert second.sequence_no == 2

        first.result_payload = {"message_type": "report"}
        with pytest.raises(AiAnalysisHistoryMutationError):
            db.commit()
        db.rollback()
