"""Real database and authenticated API checks in conftest's temporary schema."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import main as main_module
from app.database import SessionLocal
from app.main import app
from app.models import (
    FieldCareBriefingHistory,
    Message,
    MessageComment,
    Organization,
    Resident,
    RoomMembership,
    User,
)


ORIGIN = {"origin": "http://testserver"}


def _post(client, url, payload):
    return client.post(url, json=payload, headers=ORIGIN)


def test_stored_questions_period_evidence_and_current_access_boundaries(monkeypatch):
    # Any accidental provider call is a failure, not a successful fake answer.
    def no_provider(*args, **kwargs):
        raise AssertionError("record questions must never call an AI provider")

    monkeypatch.setattr(main_module, "summarize_room_messages", no_provider)
    with TestClient(app) as admin:
        assert (
            _post(
                admin,
                "/api/auth/login",
                {"username": "admin", "password": "AdminPass!234"},
            ).status_code
            == 200
        )
        username = "journey_" + uuid4().hex[:8]
        staff = _post(
            admin,
            "/api/employees",
            {
                "username": username,
                "full_name": "합성 직원0001",
                "password": "SyntheticPass!234",
                "role": "staff",
                "can_process_records": True,
            },
        )
        assert staff.status_code == 201, staff.text
        user_id = UUID(staff.json()["id"])
        allowed = _post(
            admin,
            "/api/admin/rooms",
            {
                "name": "합성 허용방 " + username,
                "kind": "custom",
                "member_ids": [str(user_id)],
                "resident_scope": "all",
            },
        )
        admin_id = admin.get("/api/auth/me").json()["id"]
        hidden = _post(
            admin,
            "/api/admin/rooms",
            {
                "name": "합성 비공개방 " + username,
                "kind": "custom",
                "member_ids": [admin_id],
                "resident_scope": "all",
            },
        )
        assert allowed.status_code == 201, allowed.text
        assert hidden.status_code == 201, hidden.text
        allowed_id, hidden_id = UUID(allowed.json()["id"]), UUID(hidden.json()["id"])
        at = datetime(2026, 9, 2, 1, tzinfo=timezone.utc)
        with SessionLocal() as db:
            db.get(User, user_id).must_change_password = False
            org = db.scalar(select(Organization).limit(1))
            resident = Resident(
                organization_id=org.id,
                internal_code="SYN-JOURNEY-" + uuid4().hex,
                display_name="어르0003",
                service_type="facility",
                is_test_data=True,
            )
            db.add(resident)
            db.flush()
            resident_id = resident.id
            initial = Message(
                organization_id=org.id,
                room_id=allowed_id,
                sender_id=user_id,
                resident_id=resident.id,
                body="09:00 이동 중 비틀거려 부축함.",
                is_test_data=True,
                created_at=at - timedelta(days=2),
            )
            hidden_message = Message(
                organization_id=org.id,
                room_id=hidden_id,
                sender_id=user_id,
                resident_id=resident.id,
                body="비공개 기록 혈압 199/111 측정함.",
                is_test_data=True,
                created_at=at,
            )
            db.add_all([initial, hidden_message])
            db.flush()
            initial_id, hidden_message_id = initial.id, hidden_message.id
            comment = MessageComment(
                organization_id=org.id,
                message_id=initial.id,
                author_id=user_id,
                body="09:30 휴식 후 어지럼 없음. 보행 안정되어 이동 완료함.",
                created_at=at,
            )
            db.add(comment)
            db.commit()
            comment_id = comment.id
        staff_client = TestClient(app)
        assert (
            _post(
                staff_client,
                "/api/auth/login",
                {"username": username, "password": "SyntheticPass!234"},
            ).status_code
            == 200
        )
        scope = {
            "start_date": "2026-09-02",
            "end_date": "2026-09-02",
            "resident_id": str(resident_id),
        }
        review = _post(
            staff_client, "/api/workdesk/period-review", {**scope, "save_history": True}
        )
        assert review.status_code == 200, review.text
        review_data = review.json()
        entries = [
            entry for topic in review_data["care_topics"] for entry in topic["entries"]
        ]
        assert len(entries) == 1
        assert entries[0]["comment_id"] == str(comment_id)
        assert "어지럼 없음" in entries[0]["summary"]
        assert str(hidden_message_id) not in str(review_data)
        history_id = review_data["history_id"]
        assert (
            staff_client.get(f"/api/workdesk/period-reviews/{history_id}").status_code
            == 200
        )
        with SessionLocal() as db:
            histories_before = db.scalar(
                select(func.count()).select_from(FieldCareBriefingHistory)
            )
        answer = _post(
            staff_client,
            "/api/workdesk/record-question",
            {
                **scope,
                "question": "이후 경과는 어땠나요?",
                "enhance_summary": True,
                "save_history": True,
            },
        )
        assert answer.status_code == 200, answer.text
        answer_data = answer.json()
        assert answer_data["period_start"] == scope["start_date"]
        assert answer_data["period_end"] == scope["end_date"]
        assert answer_data["generator"] == "local-ai-unavailable-v1"
        assert answer_data["answer"] == "이번에는 AI 답변을 완성하지 못했습니다."
        assert answer_data["evidence_ids"] == []
        assert str(hidden_message_id) not in str(answer_data)
        assert answer_data["sources"] == []
        with SessionLocal() as db:
            assert (
                db.scalar(select(func.count()).select_from(FieldCareBriefingHistory))
                == histories_before
            )
        for question, selected_resident in [("최근에 어떠셨어?", str(resident_id)), ("어르0003의 대화내용을 요약해주세요", None), ("병원은 왜 다녀왔어?", str(resident_id))]:
            natural = _post(staff_client, "/api/workdesk/record-question", {**scope, "resident_id": selected_resident, "question": question})
            assert natural.status_code == 200
            assert "질문의 주제" not in natural.text
            assert str(hidden_message_id) not in natural.text and "199/111" not in natural.text
            if "최근" in question or "요약" in question:
                assert natural.json()["processing_method"] == "failed"
                assert natural.json()["evidence_ids"] == []
        typed = _post(staff_client, "/api/workdesk/record-question", {**scope, "message_type": "notice", "question": "최근에 어떠셨어?"})
        assert typed.status_code == 200 and typed.json()["evidence_ids"] == []
        denied = _post(
            staff_client,
            "/api/workdesk/record-question",
            {**scope, "room_id": str(hidden_id), "question": "혈압 기록이 있나요?"},
        )
        assert denied.status_code == 403
        empty = _post(
            staff_client,
            "/api/workdesk/record-question",
            {
                **scope,
                "start_date": "2026-09-10",
                "end_date": "2026-09-10",
                "question": "이후 경과는 어땠나요?",
            },
        )
        assert empty.status_code == 200 and empty.json()["matched_count"] == 0
        with SessionLocal() as db:
            staff_record = db.get(User, user_id)
            membership = db.scalar(
                select(RoomMembership).where(
                    RoomMembership.room_id == allowed_id,
                    RoomMembership.staff_id == staff_record.staff_id,
                )
            )
            membership.left_at = datetime.now(timezone.utc)
            db.commit()
        assert (
            staff_client.get(f"/api/workdesk/period-reviews/{history_id}").status_code
            == 404
        )
        assert all(
            item["id"] != history_id
            for item in staff_client.get("/api/workdesk/period-reviews").json()
        )
        revoked = _post(
            staff_client,
            "/api/workdesk/record-question",
            {**scope, "question": "이후 경과는 어땠나요?"},
        )
        assert revoked.status_code == 200 and revoked.json()["evidence_ids"] == []
        assert staff_client.get(f"/api/messages/{initial_id}").status_code == 403
