from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import main as main_module
from app.database import SessionLocal
from app.main import app
from app.models import (
    AttachmentTextExtraction,
    Message,
    MessageAttachment,
    MessageResidentLink,
    Organization,
    Resident,
    Role,
    Room,
    RoomMembership,
    Staff,
    User,
    WorkItem,
)
from app.security import hash_password


ORIGIN = {"origin": "http://testserver"}
PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDAT\x08\xd7c\xf8\xcf\xc0\x00\x00"
    b"\x03\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _login(client: TestClient) -> dict:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "AdminPass!234"},
        headers=ORIGIN,
    )
    assert response.status_code == 200, response.text
    return response.json()["user"]


def _create_preserved_ai_room_work_item(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        main_module,
        "process_ai_help_message_background",
        lambda *_args, **_kwargs: None,
    )
    me = _login(client)
    ai_room = next(room for room in client.get("/api/rooms").json() if room["kind"] == "ai")
    sent = client.post(
        f"/api/rooms/{ai_room['id']}/messages",
        json={"body": "합성 AI 방 WorkItem 차단 시험", "client_request_id": str(uuid4())},
        headers=ORIGIN,
    )
    assert sent.status_code == 201, sent.text
    with SessionLocal() as db:
        user = db.get(User, me["id"])
        resident = Resident(
            organization_id=user.organization_id,
            internal_code=f"AI-WORKITEM-{uuid4().hex[:8]}",
            display_name="어르신0001",
            service_type="home_care",
            is_test_data=True,
        )
        db.add(resident)
        db.flush()
        item = WorkItem(
            organization_id=user.organization_id,
            source_message_id=sent.json()["id"],
            status="pending",
            source_snapshot={
                "message_id": sent.json()["id"],
                "room_id": ai_room["id"],
                "room_name": ai_room["name"],
                "sender_id": me["id"],
                "sender_name": user.full_name,
                "resident_id": None,
                "resident_name": None,
                "resident_names": [],
                "body": sent.json()["body"],
                "message_type": sent.json()["message_type"],
                "attachment_ids": [],
                "created_at": sent.json()["created_at"],
            },
            is_test_data=True,
        )
        db.add(item)
        db.commit()
        return me["id"], str(item.id), str(resident.id), sent.json()["id"]


def test_preserved_ai_room_work_item_is_hidden_from_processor_inbox(monkeypatch):
    with TestClient(app) as client:
        _, item_id, _, _ = _create_preserved_ai_room_work_item(client, monkeypatch)
        response = client.get("/api/work-items")
        assert response.status_code == 200, response.text
        assert item_id not in {item["id"] for item in response.json()}


def test_preserved_ai_room_work_item_direct_access_is_rejected(monkeypatch):
    expected = "MESIL AI 도움방의 메시지는 기록 검토 및 어르신 연결 대상이 아닙니다."
    with TestClient(app) as client:
        user_id, item_id, _, _ = _create_preserved_ai_room_work_item(client, monkeypatch)
        with SessionLocal() as db:
            processor = db.get(User, user_id)
            with pytest.raises(HTTPException) as caught:
                main_module._work_item_for_processor(db, processor, UUID(item_id))
            assert caught.value.status_code == 422
            assert caught.value.detail == expected


def test_preserved_ai_room_work_item_mutations_are_rejected_without_links(monkeypatch):
    expected = "MESIL AI 도움방의 메시지는 기록 검토 및 어르신 연결 대상이 아닙니다."
    with TestClient(app) as client:
        user_id, item_id, resident_id, source_message_id = _create_preserved_ai_room_work_item(
            client, monkeypatch
        )
        responses = [
            client.patch(
                f"/api/work-items/{item_id}",
                json={"status": "in_review"},
                headers=ORIGIN,
            ),
            client.patch(
                f"/api/work-items/{item_id}/resident",
                json={"resident_id": resident_id},
                headers=ORIGIN,
            ),
            client.post(f"/api/work-items/{item_id}/prototype-suggestion", headers=ORIGIN),
            client.post(f"/api/work-items/{item_id}/ai-review", headers=ORIGIN),
            client.post(
                f"/api/work-items/{item_id}/reopen",
                json={"reason": "합성 차단 시험"},
                headers=ORIGIN,
            ),
        ]
        for response in responses:
            assert response.status_code == 422, response.text
            assert response.json()["detail"] == expected

        with SessionLocal() as db:
            processor = db.get(User, user_id)
            with pytest.raises(HTTPException) as caught:
                main_module.confirm_work_item(UUID(item_id), object(), processor, db)
            assert caught.value.status_code == 422
            assert caught.value.detail == expected
            assert int(
                db.scalar(
                    select(func.count(MessageResidentLink.id)).where(
                        MessageResidentLink.message_id == source_message_id
                    )
                )
                or 0
            ) == 0


def test_existing_session_gets_one_ai_help_room_after_feature_activation(monkeypatch):
    monkeypatch.setattr(main_module.settings, "ai_help_room_enabled", False)
    with TestClient(app) as client:
        _login(client)
        assert all(room["kind"] != "ai" for room in client.get("/api/rooms").json())

        monkeypatch.setattr(main_module.settings, "ai_help_room_enabled", True)
        first = client.get("/api/rooms")
        second = client.get("/api/rooms")
        assert first.status_code == second.status_code == 200
        assert len([room for room in first.json() if room["kind"] == "ai"]) == 1
        assert len([room for room in second.json() if room["kind"] == "ai"]) == 1
        assert first.json()[-1]["kind"] == "ai"


def test_ai_help_room_is_unique_private_and_not_an_admin_managed_room():
    with TestClient(app) as client:
        me = _login(client)
        first = client.get("/api/rooms")
        second = client.get("/api/rooms")
        assert first.status_code == second.status_code == 200
        first_ai = [room for room in first.json() if room["kind"] == "ai"]
        second_ai = [room for room in second.json() if room["kind"] == "ai"]
        assert len(first_ai) == len(second_ai) == 1
        assert first_ai[0]["id"] == second_ai[0]["id"]
        assert first_ai[0]["name"] == "MESIL AI 도움방"

        managed = client.get("/api/admin/rooms")
        assert managed.status_code == 200
        assert all(room["kind"] != "ai" for room in managed.json())

        members = client.get(f"/api/rooms/{first_ai[0]['id']}/members")
        assert members.status_code == 200
        assert [member["id"] for member in members.json()] == [me["id"]]
        elevated = client.post(
            "/api/admin/conversation-access",
            json={"password": "AdminPass!234"},
            headers=ORIGIN,
        )
        assert elevated.status_code == 200, elevated.text
        assert (
            client.get(
                f"/api/admin/conversations/rooms/{first_ai[0]['id']}/messages"
            ).status_code
            == 404
        )

        with SessionLocal() as db:
            owner = db.get(User, me["id"])
            suffix = uuid4().hex[:8]
            staff = Staff(
                organization_id=owner.organization_id,
                internal_code=f"AI-PRIVATE-{suffix}",
                display_name="가상 직원 독립",
                job_title="합성 시험직",
                is_test_data=True,
            )
            db.add(staff)
            db.flush()
            other = User(
                organization_id=owner.organization_id,
                staff_id=staff.id,
                username=f"ai-private-{suffix}",
                display_name="가상 직원 독립",
                password_hash=hash_password("SyntheticPass!234"),
                is_active=True,
            )
            staff_role = db.scalar(select(Role).where(Role.code == "staff"))
            assert staff_role is not None
            other.roles = [staff_role]
            db.add(other)
            db.commit()
            other_username = other.username

        with TestClient(app) as other_client:
            login_response = other_client.post(
                "/api/auth/login",
                json={"username": other_username, "password": "SyntheticPass!234"},
                headers=ORIGIN,
            )
            assert login_response.status_code == 200, login_response.text
            other_ai = next(
                room for room in other_client.get("/api/rooms").json() if room["kind"] == "ai"
            )
            assert other_ai["id"] != first_ai[0]["id"]
            assert (
                other_client.get(f"/api/rooms/{first_ai[0]['id']}/messages").status_code
                == 403
            )


def test_ai_help_room_reactivation_reuses_the_preserved_room():
    with TestClient(app) as client:
        _login(client)
        original = next(room for room in client.get("/api/rooms").json() if room["kind"] == "ai")
        with SessionLocal() as db:
            room = db.get(Room, original["id"])
            assert room is not None
            room.is_active = False
            db.commit()

        rooms = client.get("/api/rooms")
        assert rooms.status_code == 200
        restored = [room for room in rooms.json() if room["kind"] == "ai"]
        assert len(restored) == 1
        assert restored[0]["id"] == original["id"]


def test_ai_help_message_is_immediate_idempotent_and_creates_no_work_item(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "process_ai_help_message_background",
        lambda *_args, **_kwargs: None,
    )
    with TestClient(app) as client:
        me = _login(client)
        ai_room = next(room for room in client.get("/api/rooms").json() if room["kind"] == "ai")
        request_id = str(uuid4())
        payload = {"body": "합성 일정에서 오늘 우선할 일을 알려주세요.", "client_request_id": request_id}
        first = client.post(f"/api/rooms/{ai_room['id']}/messages", json=payload, headers=ORIGIN)
        second = client.post(f"/api/rooms/{ai_room['id']}/messages", json=payload, headers=ORIGIN)
        assert first.status_code == second.status_code == 201
        assert first.json()["id"] == second.json()["id"]
        assert first.json()["sender_id"] == me["id"]
        assert first.json()["ai_help"]["role"] == "user"

        messages = client.get(f"/api/rooms/{ai_room['id']}/messages")
        assert messages.status_code == 200
        user_messages = [item for item in messages.json() if item["id"] == first.json()["id"]]
        ai_messages = [
            item
            for item in messages.json()
            if (item.get("ai_help") or {}).get("source_message_id") == first.json()["id"]
        ]
        assert len(user_messages) == 1
        assert len(ai_messages) == 1
        assert ai_messages[0]["sender_name"] == "MESIL AI"
        assert ai_messages[0]["ai_help"]["status"] == "queued"

        with SessionLocal() as db:
            work_item_count = int(
                db.scalar(
                    select(func.count(WorkItem.id)).where(
                        WorkItem.source_message_id == first.json()["id"]
                    )
                )
                or 0
            )
            assistant_count = int(
                db.scalar(
                    select(func.count(Message.id)).where(
                        Message.room_id == ai_room["id"],
                        Message.extra_data["ai_help"]["source_message_id"].as_string()
                        == first.json()["id"],
                    )
                )
                or 0
            )
        assert work_item_count == 0
        assert assistant_count == 1


def test_ai_help_websocket_announces_user_message_before_assistant_placeholder(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "process_ai_help_message_background",
        lambda *_args, **_kwargs: None,
    )
    announced_roles: list[str] = []

    async def capture_event(_user_ids, payload):
        message = payload.get("message") or {}
        ai_help = message.get("ai_help") or {}
        announced_roles.append(ai_help.get("role", ""))

    monkeypatch.setattr(main_module.manager, "send_to_users", capture_event)
    with TestClient(app) as client:
        _login(client)
        ai_room = next(room for room in client.get("/api/rooms").json() if room["kind"] == "ai")
        sent = client.post(
            f"/api/rooms/{ai_room['id']}/messages",
            json={"body": "실시간 순서 합성 질문", "client_request_id": str(uuid4())},
            headers=ORIGIN,
        )
        assert sent.status_code == 201, sent.text

    assert announced_roles == ["user", "assistant"]


def test_ai_help_room_rejects_notice_resident_work_and_forwarding(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "process_ai_help_message_background",
        lambda *_args, **_kwargs: None,
    )
    with TestClient(app) as client:
        _login(client)
        rooms = client.get("/api/rooms").json()
        ai_room = next(room for room in rooms if room["kind"] == "ai")
        ordinary_room = next(room for room in rooms if room["kind"] not in {"ai", "self"})

        notice = client.post(
            f"/api/rooms/{ai_room['id']}/messages",
            json={"body": "공지로 오인되면 안 됩니다.", "message_type": "notice"},
            headers=ORIGIN,
        )
        assert notice.status_code == 422

        source = client.post(
            f"/api/rooms/{ordinary_room['id']}/messages",
            json={"body": "AI 방으로 전달되면 안 되는 합성 메시지"},
            headers=ORIGIN,
        )
        assert source.status_code == 201
        forwarded = client.post(
            f"/api/messages/{source.json()['id']}/forward",
            json={"room_ids": [ai_room["id"]]},
            headers=ORIGIN,
        )
        assert forwarded.status_code == 422


def test_ai_help_messages_reject_resident_link_writes_and_stay_out_of_record_review(monkeypatch):
    """Removing only the button must not leave a writable resident-record API."""
    monkeypatch.setattr(
        main_module,
        "process_ai_help_message_background",
        lambda *_args, **_kwargs: None,
    )
    with TestClient(app) as client:
        me = _login(client)
        with SessionLocal() as db:
            owner = db.get(User, me["id"])
            resident = Resident(
                organization_id=owner.organization_id,
                internal_code=f"AI-LINK-{uuid4().hex[:8]}",
                display_name="어르신연결시험0001",
                service_type="daycare",
                is_test_data=True,
            )
            db.add(resident)
            db.commit()
        rooms = client.get("/api/rooms").json()
        ai_room = next(room for room in rooms if room["kind"] == "ai")
        ordinary_room = next(room for room in rooms if room["kind"] not in {"ai", "self"})
        sent = client.post(
            f"/api/rooms/{ai_room['id']}/messages",
            json={"body": "어르신 기록 연결 대상이 아닌 합성 질문", "client_request_id": str(uuid4())},
            headers=ORIGIN,
        )
        assert sent.status_code == 201, sent.text

        ordinary = client.post(
            f"/api/rooms/{ordinary_room['id']}/messages",
            json={"body": "일반방 연결 회귀 확인용 합성 메시지"},
            headers=ORIGIN,
        )
        assert ordinary.status_code == 201, ordinary.text
        choices = client.get(
            f"/api/messages/{ordinary.json()['id']}/resident-review/options"
        )
        assert choices.status_code == 200, choices.text
        resident_id = choices.json()[0]["id"]

        with SessionLocal() as db:
            links_before = int(
                db.scalar(
                    select(func.count(MessageResidentLink.id)).where(
                        MessageResidentLink.message_id == sent.json()["id"]
                    )
                )
                or 0
            )

        for response in (
            client.get(f"/api/messages/{sent.json()['id']}/resident-review/options"),
            client.patch(
                f"/api/messages/{sent.json()['id']}/resident-review",
                json={"decision": "set", "resident_ids": [resident_id]},
                headers=ORIGIN,
            ),
            client.patch(
                f"/api/messages/{sent.json()['id']}/resident-links/{resident_id}",
                json={"status": "confirmed"},
                headers=ORIGIN,
            ),
        ):
            assert response.status_code == 422, response.text
            assert response.json()["detail"] == (
                "MESIL AI 도움방의 메시지는 어르신 기록 연결 대상이 아닙니다."
            )

        ordinary_review = client.patch(
            f"/api/messages/{ordinary.json()['id']}/resident-review",
            json={"decision": "set", "resident_ids": [resident_id]},
            headers=ORIGIN,
        )
        assert ordinary_review.status_code == 200, ordinary_review.text
        assert any(
            link["resident"]["id"] == resident_id and link["status"] == "confirmed"
            for link in ordinary_review.json()["resident_links"]
        )

        today = datetime.now(timezone.utc).date().isoformat()
        review = client.post(
            "/api/workdesk/period-review",
            json={
                "start_date": today,
                "end_date": today,
                "response_mode": "briefing",
                "enhance_summary": False,
                "save_history": False,
            },
            headers=ORIGIN,
        )
        assert review.status_code == 200, review.text
        assert sent.json()["id"] not in review.json()["source_ids"]

        with SessionLocal() as db:
            links_after = int(
                db.scalar(
                    select(func.count(MessageResidentLink.id)).where(
                        MessageResidentLink.message_id == sent.json()["id"]
                    )
                )
                or 0
            )
            ai_message = db.get(Message, sent.json()["id"])
            assert main_module._sync_message_resident_candidates(
                db,
                message=ai_message,
                text="어르신0001",
                source="text_exact",
            ) == []
        assert links_before == links_after == 0


def test_ai_help_background_uses_member_scoped_local_model_without_external_transfer(monkeypatch):
    captured = {}

    def local_record_model(**kwargs):
        captured.update(kwargs)
        assert kwargs["facts"]
        selected = next(
            fact
            for fact in kwargs["facts"]
            if fact["summary"] == "합성 일정은 오전 점검 뒤 담당자에게 인계합니다."
        )
        return {
            "processing_method": "local_ai",
            "model_used": "test-local-record-model",
            "answer": selected["summary"],
            "evidence_ids": [selected["message_id"]],
            "fallback_reason": None,
            "ai_elapsed_ms": 12,
        }

    monkeypatch.setattr(main_module, "run_record_model", local_record_model)
    with TestClient(app) as client:
        _login(client)
        rooms = client.get("/api/rooms").json()
        ai_room = next(room for room in rooms if room["kind"] == "ai")
        ordinary_room = next(room for room in rooms if room["kind"] not in {"ai", "self"})
        source = client.post(
            f"/api/rooms/{ordinary_room['id']}/messages",
            json={"body": "합성 일정은 오전 점검 뒤 담당자에게 인계합니다."},
            headers=ORIGIN,
        )
        assert source.status_code == 201, source.text
        sent = client.post(
            f"/api/rooms/{ai_room['id']}/messages",
            json={
                "body": "합성 일정에서 오늘 할 일을 한 문장으로 알려주세요.",
                "client_request_id": str(uuid4()),
            },
            headers=ORIGIN,
        )
        assert sent.status_code == 201, sent.text
        messages = client.get(f"/api/rooms/{ai_room['id']}/messages").json()
        assistant = next(
            item
            for item in messages
            if (item.get("ai_help") or {}).get("source_message_id") == sent.json()["id"]
        )
        assert assistant["sender_name"] == "MESIL AI"
        assert assistant["ai_help"]["status"] == "completed"
        assert assistant["ai_help"]["external_transmission"] is False
        assert assistant["ai_help"]["processing_location"] == "local"
        assert assistant["ai_help"]["model"] == "test-local-record-model"
        assert assistant["ai_help"]["evidence"][0]["statement"] == source.json()["body"]
        assert captured["feature"] == "care_record_question"
        assert captured["semantic_selection"] is True
        assert assistant["ai_help"]["question_type"] == "record_search"


def test_ai_help_question_classifier_separates_general_record_and_ambiguous_requests():
    classifier = getattr(main_module, "classify_ai_help_question", None)
    assert callable(classifier)
    cases = {
        "어르신 기저귀 케어 시 원칙과 유의사항을 알려줘.": "general_guidance",
        "낙상 예방을 위해 직원이 확인할 점을 알려줘.": "general_guidance",
        "보호자에게 상태를 설명할 때 주의할 점을 알려줘.": "general_guidance",
        "어르0004의 최근 근황을 알려줘.": "record_search",
        "어르0002의 최근 식사 관련 기록을 찾아줘.": "record_search",
        "지난 7일 동안 낙상 위험이 보고된 어르신이 있어?": "record_search",
        "어르0003의 식사 대화가 언제 있었는지 알려줘.": "record_search",
        "어르0004에 대해 알려줘.": "general_guidance",
        "어르신 식사 상태를 알려줘.": "general_guidance",
    }
    for question, expected in cases.items():
        assert classifier(question, known_resident_names=[]) == expected


def test_ai_help_record_search_scans_the_complete_member_period_before_bounding_model_facts(
    monkeypatch,
):
    """A relevant early record must not disappear behind the old latest-26 cap."""
    monkeypatch.setattr(
        main_module,
        "process_ai_help_message_background",
        lambda *_args, **_kwargs: None,
    )
    with TestClient(app) as client:
        me = _login(client)
        rooms = client.get("/api/rooms").json()
        ordinary_room = next(room for room in rooms if room["kind"] not in {"ai", "self"})
        ai_room = next(room for room in rooms if room["kind"] == "ai")
        sent = client.post(
            f"/api/rooms/{ai_room['id']}/messages",
            json={
                "body": "최근에 어르신 중 급격한 변화가 있는 분이 계신가?",
                "client_request_id": str(uuid4()),
            },
            headers=ORIGIN,
        )
        assert sent.status_code == 201, sent.text

        with SessionLocal() as db:
            user = db.get(User, me["id"])
            resident = Resident(
                organization_id=user.organization_id,
                internal_code=f"AI-SCOPE-{uuid4().hex[:8]}",
                display_name=f"어르범위{uuid4().hex[:6]}",
                service_type="daycare",
                is_test_data=True,
            )
            db.add(resident)
            db.flush()
            base_at = datetime.now(timezone.utc) - timedelta(days=1)
            created = []
            for index in range(35):
                item = Message(
                    organization_id=user.organization_id,
                    room_id=UUID(ordinary_room["id"]),
                    sender_id=user.id,
                    message_type="chat",
                    body=(
                        "평소와 달리 식사량이 절반으로 줄었다고 보고했습니다."
                        if index == 0
                        else f"합성 범위 확인 일반 관찰 {index:02d}"
                    ),
                    resident_id=resident.id,
                    is_test_data=True,
                    created_at=base_at + timedelta(seconds=index),
                )
                db.add(item)
                created.append(item)
            db.flush()
            source = db.get(Message, sent.json()["id"])
            expected_id = created[0].id
            context = main_module._prepare_ai_help_record_search(
                db,
                requester=user,
                source=source,
            )

        assert context["scope_message_count"] >= 35
        assert expected_id in context["scope_ids"]
        assert expected_id in {fact["message_id"] for fact in context["facts"]}
        assert context["truncated"] is False
        assert (context["end_date"] - context["start_date"]).days == 6


def test_ai_help_specific_recent_record_search_expands_only_when_seven_days_are_empty(
    monkeypatch,
):
    monkeypatch.setattr(
        main_module,
        "process_ai_help_message_background",
        lambda *_args, **_kwargs: None,
    )
    with TestClient(app) as client:
        me = _login(client)
        rooms = client.get("/api/rooms").json()
        ordinary_room = next(room for room in rooms if room["kind"] not in {"ai", "self"})
        ai_room = next(room for room in rooms if room["kind"] == "ai")
        with SessionLocal() as db:
            user = db.get(User, me["id"])
            resident_name = f"어르확대{uuid4().hex[:6]}"
            resident = Resident(
                organization_id=user.organization_id,
                internal_code=f"AI-EXPAND-{uuid4().hex[:8]}",
                display_name=resident_name,
                service_type="home_care",
                is_test_data=True,
            )
            db.add(resident)
            db.flush()
            old_record = Message(
                organization_id=user.organization_id,
                room_id=UUID(ordinary_room["id"]),
                sender_id=user.id,
                message_type="chat",
                body="최근 범위 밖의 합성 식사 보고입니다.",
                resident_id=resident.id,
                is_test_data=True,
                created_at=datetime.now(timezone.utc) - timedelta(days=20),
            )
            db.add(old_record)
            db.commit()

        sent = client.post(
            f"/api/rooms/{ai_room['id']}/messages",
            json={
                "body": f"{resident_name}의 최근 보고는 뭐가 있어?",
                "client_request_id": str(uuid4()),
            },
            headers=ORIGIN,
        )
        assert sent.status_code == 201, sent.text
        with SessionLocal() as db:
            context = main_module._prepare_ai_help_record_search(
                db,
                requester=db.get(User, me["id"]),
                source=db.get(Message, sent.json()["id"]),
            )

        assert old_record.id in context["scope_ids"]
        assert context["expanded_days"] == 30
        assert "넓혀" in " ".join(context["notes"])


def test_ai_help_specific_recent_report_uses_existing_seven_day_scope_without_expansion(
    monkeypatch,
):
    """Generic Korean interrogatives must not hide an in-scope resident report."""
    monkeypatch.setattr(
        main_module,
        "process_ai_help_message_background",
        lambda *_args, **_kwargs: None,
    )
    with TestClient(app) as client:
        me = _login(client)
        rooms = client.get("/api/rooms").json()
        ordinary_room = next(room for room in rooms if room["kind"] not in {"ai", "self"})
        ai_room = next(room for room in rooms if room["kind"] == "ai")
        with SessionLocal() as db:
            user = db.get(User, me["id"])
            resident_name = f"어르최근{uuid4().hex[:6]}"
            resident = Resident(
                organization_id=user.organization_id,
                internal_code=f"AI-RECENT-{uuid4().hex[:8]}",
                display_name=resident_name,
                service_type="home_care",
                is_test_data=True,
            )
            db.add(resident)
            db.flush()
            recent_record = Message(
                organization_id=user.organization_id,
                room_id=UUID(ordinary_room["id"]),
                sender_id=user.id,
                message_type="chat",
                body="오늘 식사와 수분 섭취 상태를 확인했습니다.",
                resident_id=resident.id,
                is_test_data=True,
                created_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
            db.add(recent_record)
            db.commit()

        sent = client.post(
            f"/api/rooms/{ai_room['id']}/messages",
            json={
                "body": f"{resident_name}의 최근 보고는 뭐가 있어?",
                "client_request_id": str(uuid4()),
            },
            headers=ORIGIN,
        )
        assert sent.status_code == 201, sent.text
        with SessionLocal() as db:
            context = main_module._prepare_ai_help_record_search(
                db,
                requester=db.get(User, me["id"]),
                source=db.get(Message, sent.json()["id"]),
            )

        assert recent_record.id in context["scope_ids"]
        assert recent_record.id in {fact["message_id"] for fact in context["facts"]}
        assert context["expanded_days"] is None
        assert (context["end_date"] - context["start_date"]).days == 6


def test_specific_resident_status_question_uses_only_that_residents_records(monkeypatch):
    captured = {}

    def local_record_model(**kwargs):
        captured.update(kwargs)
        assert kwargs["facts"]
        return {
            "processing_method": "local_ai",
            "model_used": "test-local-record-model",
            "answer": "최근 기록에서 식사와 활동 상태를 확인했습니다.",
            "evidence_ids": [kwargs["facts"][0]["message_id"]],
            "selected_facts": [kwargs["facts"][0]],
            "focus_facts": [kwargs["facts"][0]],
            "fallback_reason": None,
            "ai_elapsed_ms": 11,
        }

    monkeypatch.setattr(main_module, "run_record_model", local_record_model)
    monkeypatch.setattr(
        main_module,
        "run_general_help_model",
        lambda **_kwargs: {
            "processing_method": "local_ai",
            "model_used": "wrong-general-model",
            "answer": "일반 업무 안내",
            "fallback_reason": None,
        },
    )
    with TestClient(app) as client:
        me = _login(client)
        rooms = client.get("/api/rooms").json()
        ordinary_room = next(room for room in rooms if room["kind"] not in {"ai", "self"})
        ai_room = next(room for room in rooms if room["kind"] == "ai")
        with SessionLocal() as db:
            user = db.get(User, me["id"])
            target = Resident(
                organization_id=user.organization_id,
                internal_code=f"AI-TARGET-{uuid4().hex[:8]}",
                display_name="어르0002",
                service_type="home_care",
                is_test_data=True,
            )
            other = Resident(
                organization_id=user.organization_id,
                internal_code=f"AI-OTHER-{uuid4().hex[:8]}",
                display_name="어르0099",
                service_type="home_care",
                is_test_data=True,
            )
            db.add_all([target, other])
            db.flush()
            target_message = Message(
                organization_id=user.organization_id,
                room_id=UUID(ordinary_room["id"]),
                sender_id=user.id,
                body="식사와 활동 상태를 확인한 합성 기록입니다.",
                resident_id=target.id,
                is_test_data=True,
                created_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
            other_message = Message(
                organization_id=user.organization_id,
                room_id=UUID(ordinary_room["id"]),
                sender_id=user.id,
                body="다른 어르신의 합성 상태 기록입니다.",
                resident_id=other.id,
                is_test_data=True,
                created_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
            db.add_all([target_message, other_message])
            db.commit()
            target_id = str(target.id)
            other_id = str(other.id)

        sent = client.post(
            f"/api/rooms/{ai_room['id']}/messages",
            json={
                "body": "어르0002의 최근 7일 상태와 근거를 알려줘.",
                "client_request_id": str(uuid4()),
            },
            headers=ORIGIN,
        )
        assert sent.status_code == 201, sent.text
        assistant = next(
            item
            for item in client.get(f"/api/rooms/{ai_room['id']}/messages").json()
            if (item.get("ai_help") or {}).get("source_message_id") == sent.json()["id"]
        )
        assert assistant["ai_help"]["status"] == "completed"
        assert assistant["ai_help"]["question_type"] == "record_search"
        assert assistant["ai_help"]["model"] == "test-local-record-model"
        assert "어르0002" in assistant["body"]
        assert "어르0099" not in assistant["body"]
        assert all(str(item.get("resident_id")) == target_id for item in captured["facts"]), [
            (str(item.get("message_id")), str(item.get("resident_id")))
            for item in captured["facts"]
        ]
        assert all(str(item.get("resident_id")) != other_id for item in captured["facts"])
        assert assistant["ai_help"]["evidence"]
        assert all(
            item["statement"].startswith("어르0002 · ")
            for item in assistant["ai_help"]["evidence"]
        )


@pytest.mark.parametrize(
    "question",
    ["어르9998의 최근 상태를 알려줘.", "어르9997의 오늘 기록을 보여줘."],
)
def test_unresolved_resident_reference_never_expands_to_all_records(monkeypatch, question):
    called = False

    def forbidden_record_model(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("unresolved resident must not call the record model")

    monkeypatch.setattr(main_module, "run_record_model", forbidden_record_model)
    with TestClient(app) as client:
        _login(client)
        ai_room = next(room for room in client.get("/api/rooms").json() if room["kind"] == "ai")
        sent = client.post(
            f"/api/rooms/{ai_room['id']}/messages",
            json={"body": question, "client_request_id": str(uuid4())},
            headers=ORIGIN,
        )
        assert sent.status_code == 201, sent.text
        assistant = next(
            item
            for item in client.get(f"/api/rooms/{ai_room['id']}/messages").json()
            if (item.get("ai_help") or {}).get("source_message_id") == sent.json()["id"]
        )
        assert called is False
        assert assistant["ai_help"]["question_type"] == "record_search"
        assert assistant["ai_help"]["evidence"] == []
        assert "확인 가능한 어르신" in assistant["body"]


def test_specific_resident_answer_fails_closed_when_evidence_identity_differs(monkeypatch):
    with TestClient(app) as client:
        me = _login(client)
        rooms = client.get("/api/rooms").json()
        ai_room = next(room for room in rooms if room["kind"] == "ai")
        ordinary_room = next(room for room in rooms if room["kind"] not in {"ai", "self"})
        with SessionLocal() as db:
            user = db.get(User, me["id"])
            target = Resident(
                organization_id=user.organization_id,
                internal_code=f"AI-MISMATCH-TARGET-{uuid4().hex[:8]}",
                display_name="어르0002",
                service_type="home_care",
                is_test_data=True,
            )
            other = Resident(
                organization_id=user.organization_id,
                internal_code=f"AI-MISMATCH-OTHER-{uuid4().hex[:8]}",
                display_name="어르0099",
                service_type="home_care",
                is_test_data=True,
            )
            db.add_all([target, other])
            db.flush()
            wrong_source = Message(
                organization_id=user.organization_id,
                room_id=UUID(ordinary_room["id"]),
                sender_id=user.id,
                body="다른 대상의 합성 기록입니다.",
                resident_id=other.id,
                is_test_data=True,
                created_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
            db.add(wrong_source)
            db.commit()
            target_id = target.id
            other_id = other.id
            source_id = wrong_source.id

        monkeypatch.setattr(
            main_module,
            "_prepare_ai_help_record_search",
            lambda *_args, **_kwargs: {
                "facts": [
                    {
                        "message_id": source_id,
                        "resident_id": str(other_id),
                        "summary": "다른 대상의 합성 기록입니다.",
                    }
                ],
                "names": ["어르0002", "어르0099"],
                "all_synthetic": True,
                "scope_ids": {source_id},
                "comparison": False,
                "start_date": "2026-09-09",
                "end_date": "2026-09-15",
                "expanded_days": None,
                "model_candidate_reduced": False,
                "unresolved_resident_reference": False,
                "target_resident_id": target_id,
                "target_resident_label": "어르0002",
            },
        )
        monkeypatch.setattr(
            main_module,
            "run_record_model",
            lambda **kwargs: {
                "processing_method": "local_ai",
                "model_used": "test-local-record-model",
                "answer": "잘못된 대상의 답변",
                "evidence_ids": [source_id],
                "selected_facts": kwargs["facts"],
                "focus_facts": kwargs["facts"],
                "fallback_reason": None,
            },
        )
        sent = client.post(
            f"/api/rooms/{ai_room['id']}/messages",
            json={
                "body": "어르0002의 최근 7일 상태와 근거를 알려줘.",
                "client_request_id": str(uuid4()),
            },
            headers=ORIGIN,
        )
        assert sent.status_code == 201, sent.text
        assistant = next(
            item
            for item in client.get(f"/api/rooms/{ai_room['id']}/messages").json()
            if (item.get("ai_help") or {}).get("source_message_id") == sent.json()["id"]
        )
        assert assistant["ai_help"]["status"] == "failed"
        assert assistant["ai_help"]["evidence"] == []


def test_ai_help_record_scope_excludes_self_ai_and_nonmember_rooms_and_rechecks_membership(
    monkeypatch,
):
    monkeypatch.setattr(
        main_module,
        "process_ai_help_message_background",
        lambda *_args, **_kwargs: None,
    )
    with TestClient(app) as client:
        me = _login(client)
        rooms = client.get("/api/rooms").json()
        ordinary_room = next(room for room in rooms if room["kind"] not in {"ai", "self"})
        self_room = next(room for room in rooms if room["kind"] == "self")
        ai_room = next(room for room in rooms if room["kind"] == "ai")
        sent = client.post(
            f"/api/rooms/{ai_room['id']}/messages",
            json={
                "body": "최근에 어르신 중 급격한 변화가 있는 분이 계신가?",
                "client_request_id": str(uuid4()),
            },
            headers=ORIGIN,
        )
        assert sent.status_code == 201, sent.text

        with SessionLocal() as db:
            user = db.get(User, me["id"])
            resident = Resident(
                organization_id=user.organization_id,
                internal_code=f"AI-ACL-{uuid4().hex[:8]}",
                display_name=f"어르권한{uuid4().hex[:6]}",
                service_type="daycare",
                is_test_data=True,
            )
            other_staff = Staff(
                organization_id=user.organization_id,
                internal_code=f"AI-ACL-STAFF-{uuid4().hex[:8]}",
                display_name="가상 직원 비참여",
                job_title="합성 시험직",
                is_test_data=True,
            )
            db.add_all([resident, other_staff])
            db.flush()
            private_room = Room(
                organization_id=user.organization_id,
                kind="custom",
                name="합성 비참여 개인방",
                owner_staff_id=other_staff.id,
                is_test_data=True,
            )
            db.add(private_room)
            db.flush()
            db.add(
                RoomMembership(
                    organization_id=user.organization_id,
                    room_id=private_room.id,
                    staff_id=other_staff.id,
                    source="manual",
                )
            )
            now = datetime.now(timezone.utc)
            allowed = Message(
                organization_id=user.organization_id,
                room_id=UUID(ordinary_room["id"]),
                sender_id=user.id,
                body="평소와 다른 합성 변화가 보고됐습니다.",
                resident_id=resident.id,
                is_test_data=True,
                created_at=now,
            )
            self_only = Message(
                organization_id=user.organization_id,
                room_id=UUID(self_room["id"]),
                sender_id=user.id,
                body="나와의 대화에만 있는 합성 변화 기록입니다.",
                resident_id=resident.id,
                is_test_data=True,
                created_at=now,
            )
            ai_only = Message(
                organization_id=user.organization_id,
                room_id=UUID(ai_room["id"]),
                sender_id=user.id,
                body="AI 도움방에만 있는 합성 변화 기록입니다.",
                resident_id=resident.id,
                is_test_data=True,
                created_at=now,
            )
            other_only = Message(
                organization_id=user.organization_id,
                room_id=private_room.id,
                sender_id=user.id,
                body="비참여 방에만 있는 합성 변화 기록입니다.",
                resident_id=resident.id,
                is_test_data=True,
                created_at=now,
            )
            db.add_all([allowed, self_only, ai_only, other_only])
            db.flush()
            context = main_module._prepare_ai_help_record_search(
                db,
                requester=user,
                source=db.get(Message, sent.json()["id"]),
            )
            scope_ids = context["scope_ids"]
            assert allowed.id in scope_ids
            assert self_only.id not in scope_ids
            assert ai_only.id not in scope_ids
            assert other_only.id not in scope_ids

            membership = db.scalar(
                select(RoomMembership).where(
                    RoomMembership.room_id == UUID(ordinary_room["id"]),
                    RoomMembership.staff_id == user.staff_id,
                    RoomMembership.left_at.is_(None),
                )
            )
            membership.left_at = now
            db.flush()
            with pytest.raises(HTTPException) as caught:
                main_module._recheck_ai_help_record_scope_access(
                    db,
                    user,
                    {allowed.id},
                )
            assert caught.value.status_code == 403


def test_ai_help_comparison_response_uses_trusted_resident_labels_for_body_and_evidence(
    monkeypatch,
):
    """A model-selected change cannot reach the UI without its DB-owned subject label."""
    unique_direct = f"평소보다 식사량이 줄었다는 합성 관찰 {uuid4().hex[:8]}"
    unique_linked = f"평소보다 활동 참여가 줄었다는 합성 관찰 {uuid4().hex[:8]}"
    unique_unlinked = f"평소보다 수면 시간이 달라졌다는 합성 관찰 {uuid4().hex[:8]}"
    unselected = f"선택되면 안 되는 합성 관찰 {uuid4().hex[:8]}"
    captured = {}

    def local_record_model(**kwargs):
        captured.update(kwargs)
        selected = [
            fact
            for fact in kwargs["facts"]
            if fact["summary"] in {unique_direct, unique_linked, unique_unlinked}
        ]
        assert len(selected) == 3
        return {
            "processing_method": "local_ai",
            "model_used": "test-local-record-model",
            "answer": "모델이 대상자 이름을 모두 생략한 답변입니다.",
            "evidence_ids": [fact["message_id"] for fact in selected],
            "selected_facts": selected,
            "focus_facts": selected,
            "fallback_reason": None,
            "ai_elapsed_ms": 12,
        }

    monkeypatch.setattr(main_module, "run_record_model", local_record_model)
    with TestClient(app) as client:
        me = _login(client)
        rooms = client.get("/api/rooms").json()
        ordinary_room = next(room for room in rooms if room["kind"] not in {"ai", "self"})
        ai_room = next(room for room in rooms if room["kind"] == "ai")
        with SessionLocal() as db:
            user = db.get(User, me["id"])
            direct_resident = Resident(
                organization_id=user.organization_id,
                internal_code=f"AI-COMPARE-DIRECT-{uuid4().hex[:8]}",
                display_name=f"어르직접{uuid4().hex[:5]}",
                service_type="home_care",
                is_test_data=True,
            )
            linked_resident = Resident(
                organization_id=user.organization_id,
                internal_code=f"AI-COMPARE-LINK-{uuid4().hex[:8]}",
                display_name=f"어르연결{uuid4().hex[:5]}",
                service_type="home_care",
                is_test_data=True,
            )
            not_selected_resident = Resident(
                organization_id=user.organization_id,
                internal_code=f"AI-COMPARE-OTHER-{uuid4().hex[:8]}",
                display_name=f"어르제외{uuid4().hex[:5]}",
                service_type="home_care",
                is_test_data=True,
            )
            db.add_all([direct_resident, linked_resident, not_selected_resident])
            db.flush()

            direct_message = Message(
                organization_id=user.organization_id,
                room_id=UUID(ordinary_room["id"]),
                sender_id=user.id,
                message_type="chat",
                body=unique_direct,
                resident_id=direct_resident.id,
                is_test_data=True,
                created_at=datetime.now(timezone.utc) - timedelta(hours=3),
            )
            linked_message = Message(
                organization_id=user.organization_id,
                room_id=UUID(ordinary_room["id"]),
                sender_id=user.id,
                message_type="chat",
                body=unique_linked,
                is_test_data=True,
                created_at=datetime.now(timezone.utc) - timedelta(hours=2),
            )
            unlinked_message = Message(
                organization_id=user.organization_id,
                room_id=UUID(ordinary_room["id"]),
                sender_id=user.id,
                message_type="chat",
                body=unique_unlinked,
                is_test_data=True,
                created_at=datetime.now(timezone.utc) - timedelta(hours=1),
            )
            not_selected_message = Message(
                organization_id=user.organization_id,
                room_id=UUID(ordinary_room["id"]),
                sender_id=user.id,
                message_type="chat",
                body=unselected,
                resident_id=not_selected_resident.id,
                is_test_data=True,
                created_at=datetime.now(timezone.utc) - timedelta(minutes=30),
            )
            db.add_all(
                [direct_message, linked_message, unlinked_message, not_selected_message]
            )
            db.flush()
            db.add(
                MessageResidentLink(
                    organization_id=user.organization_id,
                    message_id=linked_message.id,
                    resident_id=linked_resident.id,
                    source="manual",
                    status="confirmed",
                    reviewed_by_id=user.id,
                    reviewed_at=datetime.now(timezone.utc),
                )
            )
            db.commit()
            expected_direct = direct_resident.display_name
            expected_linked = linked_resident.display_name
            unexpected = not_selected_resident.display_name

        sent = client.post(
            f"/api/rooms/{ai_room['id']}/messages",
            json={
                "body": "최근 7일간 상태가 달라진 어르신과 근거를 알려줘.",
                "client_request_id": str(uuid4()),
            },
            headers=ORIGIN,
        )
        assert sent.status_code == 201, sent.text
        assistant = next(
            item
            for item in client.get(f"/api/rooms/{ai_room['id']}/messages").json()
            if (item.get("ai_help") or {}).get("source_message_id") == sent.json()["id"]
        )

        assert captured["force_resident_labels"] is True
        assert assistant["ai_help"]["question_type"] == "record_search"
        assert expected_direct in assistant["body"]
        assert expected_linked in assistant["body"]
        assert "어르신 확인 필요" in assistant["body"]
        assert unexpected not in assistant["body"]
        statements = [item["statement"] for item in assistant["ai_help"]["evidence"]]
        assert any(item.startswith(f"{expected_direct} · {unique_direct}") for item in statements)
        assert any(item.startswith(f"{expected_linked} · {unique_linked}") for item in statements)
        assert any(item.startswith(f"어르신 확인 필요 · {unique_unlinked}") for item in statements)
        assert all(unselected not in item and unexpected not in item for item in statements)


def test_ai_help_resident_label_uses_only_current_same_organization_identity():
    """Rejected, unconfirmed, or cross-organization identity must fail closed."""
    with TestClient(app) as client:
        me = _login(client)
        ordinary_room = next(
            room
            for room in client.get("/api/rooms").json()
            if room["kind"] not in {"ai", "self"}
        )
        with SessionLocal() as db:
            requester = db.get(User, me["id"])
            suffix = uuid4().hex[:8]
            same_org = [
                Resident(
                    organization_id=requester.organization_id,
                    internal_code=f"AI-LABEL-{suffix}-{index}",
                    display_name=f"어르검증{index}",
                    service_type="home_care",
                    is_test_data=True,
                )
                for index in range(1, 5)
            ]
            other_org = Organization(
                internal_code=f"AI-LABEL-OTHER-{suffix}",
                name="합성 타기관",
            )
            db.add_all([*same_org, other_org])
            db.flush()
            other_resident = Resident(
                organization_id=other_org.id,
                internal_code=f"AI-LABEL-FOREIGN-{suffix}",
                display_name="타기관어르검증",
                service_type="home_care",
                is_test_data=True,
            )
            db.add(other_resident)
            db.flush()

            def message(*, resident=None, body: str) -> Message:
                item = Message(
                    organization_id=requester.organization_id,
                    room_id=UUID(ordinary_room["id"]),
                    sender_id=requester.id,
                    body=body,
                    resident_id=resident.id if resident is not None else None,
                    is_test_data=True,
                )
                db.add(item)
                db.flush()
                return item

            valid_primary = message(resident=same_org[0], body="정상 기본 연결")
            valid_confirmed = message(body="정상 확인 연결")
            rejected_primary = message(resident=same_org[1], body="거절된 과거 연결")
            candidate_primary = message(resident=same_org[2], body="미확정 후보 연결")
            cross_org_primary = message(resident=other_resident, body="다른 기관 기본 연결")
            cross_org_confirmed = message(body="다른 기관 확인 형태 연결")
            no_identity = message(body="연결 정보 없음")
            db.add_all(
                [
                    MessageResidentLink(
                        organization_id=requester.organization_id,
                        message_id=valid_confirmed.id,
                        resident_id=same_org[3].id,
                        source="manual",
                        status="confirmed",
                        reviewed_by_id=requester.id,
                        reviewed_at=datetime.now(timezone.utc),
                    ),
                    MessageResidentLink(
                        organization_id=requester.organization_id,
                        message_id=rejected_primary.id,
                        resident_id=same_org[1].id,
                        source="manual",
                        status="rejected",
                        reviewed_by_id=requester.id,
                        reviewed_at=datetime.now(timezone.utc),
                    ),
                    MessageResidentLink(
                        organization_id=requester.organization_id,
                        message_id=candidate_primary.id,
                        resident_id=same_org[2].id,
                        source="text_exact",
                        status="candidate",
                    ),
                    MessageResidentLink(
                        organization_id=requester.organization_id,
                        message_id=cross_org_confirmed.id,
                        resident_id=other_resident.id,
                        source="manual",
                        status="confirmed",
                        reviewed_by_id=requester.id,
                        reviewed_at=datetime.now(timezone.utc),
                    ),
                ]
            )
            db.flush()
            db.expire_all()
            requester = db.get(User, me["id"])

            def label(item: Message, **fact_values) -> str:
                return main_module._ai_help_fact_resident_label(
                    db,
                    requester=requester,
                    fact={"message_id": item.id, "summary": item.body, **fact_values},
                )

            assert label(valid_primary) == same_org[0].display_name
            assert label(valid_confirmed) == same_org[3].display_name
            assert (
                label(rejected_primary, resident_name=same_org[1].display_name)
                == "어르신 확인 필요"
            )
            assert (
                label(candidate_primary, resident_name=same_org[2].display_name)
                == "어르신 확인 필요"
            )
            assert (
                label(cross_org_primary, resident_name=other_resident.display_name)
                == "어르신 확인 필요"
            )
            assert (
                label(cross_org_confirmed, resident_name=other_resident.display_name)
                == "어르신 확인 필요"
            )
            assert label(no_identity) == "어르신 확인 필요"
            assert (
                label(
                    no_identity,
                    resident_id=str(same_org[0].id),
                    resident_name="모델이 만든 이름",
                )
                == "어르신 확인 필요"
            )
            assert label(no_identity, resident_name="검증되지 않은 이름") == "어르신 확인 필요"
            rejected_fact = {
                "message_id": rejected_primary.id,
                "resident_name": same_org[1].display_name,
                "summary": "거절된 이름이 재사용되면 안 됩니다.",
            }
            rejected_answer = main_module._ai_help_comparison_answer(
                db,
                requester=requester,
                selected_facts=[rejected_fact],
            )
            rejected_evidence = main_module._ai_help_comparison_evidence(
                db,
                requester=requester,
                selected_facts=[rejected_fact],
                evidence_ids=[rejected_primary.id],
            )
            assert same_org[1].display_name not in rejected_answer
            assert same_org[1].display_name not in str(rejected_evidence)
            assert "어르신 확인 필요" in rejected_answer
            assert rejected_evidence[0]["statement"].startswith("어르신 확인 필요 ·")


def test_ai_help_comparison_evidence_preserves_selected_residents_per_source_message():
    """One source message may contribute multiple selected residents and facts."""
    with TestClient(app) as client:
        me = _login(client)
        ordinary_room = next(
            room
            for room in client.get("/api/rooms").json()
            if room["kind"] not in {"ai", "self"}
        )
        with SessionLocal() as db:
            requester = db.get(User, me["id"])
            suffix = uuid4().hex[:8]
            resident_a = Resident(
                organization_id=requester.organization_id,
                internal_code=f"AI-MULTI-A-{suffix}",
                display_name="어르복수A",
                service_type="home_care",
                is_test_data=True,
            )
            resident_b = Resident(
                organization_id=requester.organization_id,
                internal_code=f"AI-MULTI-B-{suffix}",
                display_name="어르복수B",
                service_type="home_care",
                is_test_data=True,
            )
            db.add_all([resident_a, resident_b])
            db.flush()
            source = Message(
                organization_id=requester.organization_id,
                room_id=UUID(ordinary_room["id"]),
                sender_id=requester.id,
                body="어르복수A는 식사량이 줄었고 어르복수B는 이동이 불편했습니다.",
                is_test_data=True,
            )
            db.add(source)
            db.flush()
            db.add_all(
                [
                    MessageResidentLink(
                        organization_id=requester.organization_id,
                        message_id=source.id,
                        resident_id=resident.id,
                        source="manual",
                        status="confirmed",
                        reviewed_by_id=requester.id,
                        reviewed_at=datetime.now(timezone.utc),
                    )
                    for resident in (resident_a, resident_b)
                ]
            )
            db.flush()
            facts = [
                {
                    "message_id": source.id,
                    "resident_id": str(resident_a.id),
                    "resident_name": resident_a.display_name,
                    "summary": "식사량이 줄었습니다.",
                },
                {
                    "message_id": source.id,
                    "resident_id": str(resident_b.id),
                    "resident_name": resident_b.display_name,
                    "summary": "이동이 불편했습니다.",
                },
                {
                    "message_id": source.id,
                    "resident_id": str(resident_a.id),
                    "resident_name": resident_a.display_name,
                    "summary": "식사량이 줄었습니다.",
                },
                {
                    "message_id": source.id,
                    "resident_id": str(resident_a.id),
                    "resident_name": resident_a.display_name,
                    "summary": "간식도 남겼습니다.",
                },
            ]

            selected = main_module._ai_help_comparison_facts(
                {"focus_facts": facts, "selected_facts": facts},
                facts,
                [source.id],
            )
            answer = main_module._ai_help_comparison_answer(
                db,
                requester=requester,
                selected_facts=selected,
            )
            evidence = main_module._ai_help_comparison_evidence(
                db,
                requester=requester,
                selected_facts=selected,
                evidence_ids=[source.id],
            )
            assert answer.count("어르복수A") == 1
            assert answer.count("어르복수B") == 1
            assert answer.count("식사량이 줄었습니다.") == 1
            assert "이동이 불편했습니다." in answer
            assert "간식도 남겼습니다." in answer
            assert [item["statement"] for item in evidence] == [
                "어르복수A · 식사량이 줄었습니다.",
                "어르복수B · 이동이 불편했습니다.",
                "어르복수A · 간식도 남겼습니다.",
            ]
            assert [item["source_no"] for item in evidence] == [1, 1, 1]
            assert {item["source_message_id"] for item in evidence} == {str(source.id)}

            only_a = main_module._ai_help_comparison_evidence(
                db,
                requester=requester,
                selected_facts=[facts[0]],
                evidence_ids=[source.id],
            )
            assert [item["statement"] for item in only_a] == [
                "어르복수A · 식사량이 줄었습니다."
            ]
            assert all("어르복수B" not in item["statement"] for item in only_a)


def test_ai_help_comparison_keeps_verified_homonyms_as_distinct_safe_identities():
    """Equal display text is not a resident identity or a safe deduplication key."""
    with TestClient(app) as client:
        me = _login(client)
        ordinary_room = next(
            room
            for room in client.get("/api/rooms").json()
            if room["kind"] not in {"ai", "self"}
        )
        with SessionLocal() as db:
            requester = db.get(User, me["id"])
            suffix = uuid4().hex[:8]
            same_name = "어르동명이인"
            residents = [
                Resident(
                    organization_id=requester.organization_id,
                    internal_code=f"AI-HOMONYM-{suffix}-{index}",
                    display_name=same_name,
                    service_type="home_care",
                    is_test_data=True,
                )
                for index in (1, 2)
            ]
            other_org = Organization(
                internal_code=f"AI-HOMONYM-OTHER-{suffix}",
                name="합성 타기관",
            )
            db.add_all([*residents, other_org])
            db.flush()
            other_resident = Resident(
                organization_id=other_org.id,
                internal_code=f"AI-HOMONYM-FOREIGN-{suffix}",
                display_name=same_name,
                service_type="home_care",
                is_test_data=True,
            )
            db.add(other_resident)
            db.flush()
            source = Message(
                organization_id=requester.organization_id,
                room_id=UUID(ordinary_room["id"]),
                sender_id=requester.id,
                body="동명이인 두 명의 같은 변화가 한 원문에 기록된 합성 경계 시험",
                is_test_data=True,
            )
            db.add(source)
            db.flush()
            db.add_all(
                [
                    MessageResidentLink(
                        organization_id=requester.organization_id,
                        message_id=source.id,
                        resident_id=resident.id,
                        source="manual",
                        status="confirmed",
                        reviewed_by_id=requester.id,
                        reviewed_at=datetime.now(timezone.utc),
                    )
                    for resident in residents
                ]
                + [
                    MessageResidentLink(
                        organization_id=requester.organization_id,
                        message_id=source.id,
                        resident_id=other_resident.id,
                        source="manual",
                        status="confirmed",
                        reviewed_by_id=requester.id,
                        reviewed_at=datetime.now(timezone.utc),
                    )
                ]
            )
            db.flush()
            common_summary = "같은 변화가 기록되었습니다."
            facts = [
                {
                    "message_id": source.id,
                    "resident_id": str(resident.id),
                    "resident_name": same_name,
                    "summary": common_summary,
                }
                for resident in residents
            ]
            selected = main_module._ai_help_comparison_facts(
                {"focus_facts": [*facts, facts[0]], "selected_facts": [*facts, facts[0]]},
                facts,
                [source.id],
            )
            answer = main_module._ai_help_comparison_answer(
                db,
                requester=requester,
                selected_facts=selected,
            )
            evidence = main_module._ai_help_comparison_evidence(
                db,
                requester=requester,
                selected_facts=selected,
                evidence_ids=[source.id],
            )

            assert len(selected) == 2
            assert len(evidence) == 2
            assert answer.count(common_summary) == 2
            assert answer.count("동명이인 확인 필요") == 2
            assert all("동명이인 확인 필요" in item["statement"] for item in evidence)
            assert len({item["statement"] for item in evidence}) == 2
            serialized = answer + str(evidence)
            assert all(str(resident.id) not in serialized for resident in residents)
            assert all(resident.internal_code not in serialized for resident in residents)

            only_first = main_module._ai_help_comparison_evidence(
                db,
                requester=requester,
                selected_facts=[facts[0]],
                evidence_ids=[source.id],
            )
            assert len(only_first) == 1
            assert "동명이인 확인 필요" not in only_first[0]["statement"]

            different = [facts[0], {**facts[1], "summary": "서로 다른 변화입니다."}]
            different_evidence = main_module._ai_help_comparison_evidence(
                db,
                requester=requester,
                selected_facts=different,
                evidence_ids=[source.id],
            )
            assert len(different_evidence) == 2
            assert common_summary in different_evidence[0]["statement"]
            assert "서로 다른 변화입니다." in different_evidence[1]["statement"]
            label_for_first = different_evidence[0]["statement"].rsplit(" · ", 1)[0]
            label_for_second = different_evidence[1]["statement"].rsplit(" · ", 1)[0]
            reversed_answer = main_module._ai_help_comparison_answer(
                db,
                requester=requester,
                selected_facts=list(reversed(different)),
            )
            assert f"{label_for_first}\n- {common_summary}" in reversed_answer
            assert f"{label_for_second}\n- 서로 다른 변화입니다." in reversed_answer

            cross_org = main_module._ai_help_comparison_evidence(
                db,
                requester=requester,
                selected_facts=[
                    {
                        "message_id": source.id,
                        "resident_id": str(other_resident.id),
                        "resident_name": same_name,
                        "summary": "다른 기관 대상은 표시하면 안 됩니다.",
                    }
                ],
                evidence_ids=[source.id],
            )
            assert cross_org[0]["statement"].startswith("어르신 확인 필요 ·")
            assert str(other_resident.id) not in str(cross_org)
            assert other_resident.internal_code not in str(cross_org)


def test_ai_help_response_keeps_two_selected_residents_from_one_message(monkeypatch):
    """The API response must not collapse a shared source message to its first fact."""
    selected_resident_ids: set[str] = set()
    source_message_id = None

    def local_record_model(**kwargs):
        matches = [
            fact
            for fact in kwargs["facts"]
            if fact["message_id"] == source_message_id
            and str(fact.get("resident_id") or "") in selected_resident_ids
        ]
        chosen = []
        for resident_id in selected_resident_ids:
            chosen.append(
                next(
                    fact
                    for fact in matches
                    if str(fact.get("resident_id") or "") == resident_id
                )
            )
        assert len(chosen) == 2
        return {
            "processing_method": "local_ai",
            "model_used": "test-local-record-model",
            "answer": "모델이 대상자를 생략한 합성 답변입니다.",
            "evidence_ids": [source_message_id],
            "selected_facts": chosen,
            "focus_facts": chosen,
            "fallback_reason": None,
            "ai_elapsed_ms": 11,
        }

    monkeypatch.setattr(main_module, "run_record_model", local_record_model)
    with TestClient(app) as client:
        me = _login(client)
        rooms = client.get("/api/rooms").json()
        ordinary_room = next(room for room in rooms if room["kind"] not in {"ai", "self"})
        ai_room = next(room for room in rooms if room["kind"] == "ai")
        with SessionLocal() as db:
            requester = db.get(User, me["id"])
            suffix = uuid4().hex[:8]
            residents = [
                Resident(
                    organization_id=requester.organization_id,
                    internal_code=f"AI-MULTI-API-{suffix}-{index}",
                    display_name=f"어르API{index}",
                    service_type="home_care",
                    is_test_data=True,
                )
                for index in (1, 2)
            ]
            db.add_all(residents)
            db.flush()
            source = Message(
                organization_id=requester.organization_id,
                room_id=UUID(ordinary_room["id"]),
                sender_id=requester.id,
                body=(
                    f"{residents[0].display_name}는 평소보다 식사량이 줄었습니다. "
                    f"{residents[1].display_name}는 평소보다 활동 참여가 줄었습니다."
                ),
                is_test_data=True,
                created_at=datetime.now(timezone.utc) - timedelta(hours=1),
            )
            db.add(source)
            db.flush()
            db.add_all(
                [
                    MessageResidentLink(
                        organization_id=requester.organization_id,
                        message_id=source.id,
                        resident_id=resident.id,
                        source="manual",
                        status="confirmed",
                        reviewed_by_id=requester.id,
                        reviewed_at=datetime.now(timezone.utc),
                    )
                    for resident in residents
                ]
            )
            db.commit()
            source_message_id = source.id
            selected_resident_ids.update(str(resident.id) for resident in residents)
            expected_names = {resident.display_name for resident in residents}

        sent = client.post(
            f"/api/rooms/{ai_room['id']}/messages",
            json={
                "body": "최근 7일간 상태가 달라진 어르신과 근거를 알려줘.",
                "client_request_id": str(uuid4()),
            },
            headers=ORIGIN,
        )
        assert sent.status_code == 201, sent.text
        assistant = next(
            item
            for item in client.get(f"/api/rooms/{ai_room['id']}/messages").json()
            if (item.get("ai_help") or {}).get("source_message_id") == sent.json()["id"]
        )
        statements = [item["statement"] for item in assistant["ai_help"]["evidence"]]
        assert all(name in assistant["body"] for name in expected_names)
        assert {statement.split(" · ", 1)[0] for statement in statements} == expected_names
        assert {item["source_message_id"] for item in assistant["ai_help"]["evidence"]} == {
            str(source_message_id)
        }


def test_ai_help_response_preserves_every_subject_after_final_2000_character_budget(
    monkeypatch,
):
    """Final persistence must not trim a later selected resident from the answer."""
    source_message_ids: list[UUID] = []
    selected_facts: list[dict] = []

    def fixed_record_context(_db, *, requester, source):
        del requester, source
        return {
            "facts": selected_facts,
            "names": [fact["resident_name"] for fact in selected_facts],
            "all_synthetic": True,
            "scope_ids": set(source_message_ids),
            "comparison": True,
            "start_date": "2026-09-09",
            "end_date": "2026-09-15",
            "expanded_days": None,
            "model_candidate_reduced": False,
        }

    def local_record_model(**_kwargs):
        return {
            "processing_method": "local_ai",
            "model_used": "test-local-record-model",
            "answer": "모델 중간 답변에는 두 대상이 모두 있습니다.",
            "evidence_ids": source_message_ids,
            "selected_facts": selected_facts,
            "focus_facts": selected_facts,
            "fallback_reason": None,
            "ai_elapsed_ms": 11,
        }

    monkeypatch.setattr(
        main_module,
        "_prepare_ai_help_record_search",
        fixed_record_context,
    )
    monkeypatch.setattr(main_module, "run_record_model", local_record_model)
    with TestClient(app) as client:
        me = _login(client)
        rooms = client.get("/api/rooms").json()
        ordinary_room = next(room for room in rooms if room["kind"] not in {"ai", "self"})
        ai_room = next(room for room in rooms if room["kind"] == "ai")
        with SessionLocal() as db:
            requester = db.get(User, me["id"])
            suffix = uuid4().hex[:8]
            residents = [
                Resident(
                    organization_id=requester.organization_id,
                    internal_code=f"AI-BUDGET-{suffix}-{index}",
                    display_name=f"어르예산{index}",
                    service_type="home_care",
                    is_test_data=True,
                )
                for index in (1, 2)
            ]
            db.add_all(residents)
            db.flush()
            summaries = ["가" * 1953, "짧은 변화 내용이 확인되었습니다."]
            for resident, summary in zip(residents, summaries, strict=True):
                message = Message(
                    organization_id=requester.organization_id,
                    room_id=UUID(ordinary_room["id"]),
                    sender_id=requester.id,
                    body=summary,
                    is_test_data=True,
                    created_at=datetime.now(timezone.utc) - timedelta(hours=1),
                )
                db.add(message)
                db.flush()
                db.add(
                    MessageResidentLink(
                        organization_id=requester.organization_id,
                        message_id=message.id,
                        resident_id=resident.id,
                        source="manual",
                        status="confirmed",
                        reviewed_by_id=requester.id,
                        reviewed_at=datetime.now(timezone.utc),
                    )
                )
                source_message_ids.append(message.id)
                selected_facts.append(
                    {
                        "message_id": message.id,
                        "resident_id": str(resident.id),
                        "resident_name": resident.display_name,
                        "summary": summary,
                    }
                )
            expected_names = {resident.display_name for resident in residents}
            db.commit()

        sent = client.post(
            f"/api/rooms/{ai_room['id']}/messages",
            json={
                "body": "최근 7일간 상태가 달라진 어르신과 근거를 알려줘.",
                "client_request_id": str(uuid4()),
            },
            headers=ORIGIN,
        )
        assert sent.status_code == 201, sent.text
        assistant = next(
            item
            for item in client.get(f"/api/rooms/{ai_room['id']}/messages").json()
            if (item.get("ai_help") or {}).get("source_message_id") == sent.json()["id"]
        )
        evidence_labels = {
            item["statement"].split(" · ", 1)[0]
            for item in assistant["ai_help"]["evidence"]
        }
        assert assistant["ai_help"]["status"] == "completed"
        assert len(assistant["body"]) <= 2000
        assert all(name in assistant["body"] for name in expected_names)
        assert evidence_labels == expected_names


@pytest.mark.parametrize("body_length", [1999, 2000])
def test_ai_help_comparison_budget_preserves_exact_boundary_content(body_length):
    period_notice = "기간"
    safety_notice = "\n\n사람 확인"
    label = "어르경계"
    fixed = len(period_notice) + 2 + len(label) + 3 + len(safety_notice)
    summary = "가" * (body_length - fixed)
    answer = f"{label}\n- {summary}"

    body = main_module._fit_ai_help_comparison_body(
        period_notice=period_notice,
        answer=answer,
        safety_notice=safety_notice,
        subject_sections=[("resident-a", label, [summary])],
    )

    assert len(body) == body_length
    assert body == f"{period_notice}\n\n{answer}{safety_notice}"


def test_ai_help_comparison_budget_trims_only_description_over_2000():
    label = "어르경계"
    period_notice = "2026-09-09~2026-09-15 기록"
    safety_notice = "\n\n사람이 원문을 확인해야 합니다."
    fixed = len(period_notice) + 2 + len(label) + 3 + len(safety_notice)
    summary = "가" * (2001 - fixed)
    answer = f"{label}\n- {summary}"
    assert len(f"{period_notice}\n\n{answer}{safety_notice}") == 2001
    body = main_module._fit_ai_help_comparison_body(
        period_notice=period_notice,
        answer=answer,
        safety_notice=safety_notice,
        subject_sections=[("resident-a", label, [summary])],
    )

    assert len(body) <= 2000
    assert f"\n\n{label}\n- " in body
    assert body.endswith("사람이 원문을 확인해야 합니다.")
    assert "…" in body


def test_ai_help_comparison_budget_preserves_b_c_d_after_very_long_a():
    sections = [
        ("resident-a", "어르예산A", ["가" * 1953]),
        ("resident-b", "어르예산B", ["짧은 B 변화"]),
        ("resident-c", "어르예산C", ["짧은 C 변화"]),
        ("resident-d", "어르예산D", ["짧은 D 변화"]),
    ]
    answer = "\n".join(
        f"{label}\n" + "\n".join(f"- {summary}" for summary in summaries)
        for _, label, summaries in sections
    )

    body = main_module._fit_ai_help_comparison_body(
        period_notice="최근 7일 기록",
        answer=answer,
        safety_notice="\n\n사람 확인 필요",
        subject_sections=sections,
    )

    assert len(body) <= 2000
    assert all(label in body for _, label, _ in sections)
    assert all(summaries[0][:4] in body for _, _, summaries in sections)


def test_ai_help_comparison_budget_preserves_homonym_item_labels_without_internal_ids():
    hidden_ids = [str(uuid4()), str(uuid4())]
    sections = [
        (hidden_ids[0], "어르동명 (동명이인 확인 필요 · 항목 1)", ["가" * 1600]),
        (hidden_ids[1], "어르동명 (동명이인 확인 필요 · 항목 2)", ["짧은 변화"]),
    ]
    answer = "\n".join(
        f"{label}\n- {summaries[0]}" for _, label, summaries in sections
    )

    body = main_module._fit_ai_help_comparison_body(
        period_notice="최근 7일 기록",
        answer=answer,
        safety_notice="\n\n사람 확인 필요",
        subject_sections=sections,
    )

    assert all(label in body for _, label, _ in sections)
    assert all(hidden_id not in body for hidden_id in hidden_ids)


def test_ai_help_comparison_budget_reserves_long_period_and_safety_notices():
    period_notice = "조회 기간 " + "기" * 320
    safety_notice = "\n\n필수 안전 안내 " + "안" * 320
    sections = [
        ("resident-a", "어르안전A", ["가" * 1200]),
        ("resident-b", "어르안전B", ["짧은 변화"]),
    ]
    answer = "\n".join(
        f"{label}\n- {summaries[0]}" for _, label, summaries in sections
    )

    body = main_module._fit_ai_help_comparison_body(
        period_notice=period_notice,
        answer=answer,
        safety_notice=safety_notice,
        subject_sections=sections,
    )

    assert len(body) <= 2000
    assert body.startswith(f"{period_notice}\n\n")
    assert body.endswith(safety_notice)
    assert all(label in body for _, label, _ in sections)


def test_ai_help_comparison_budget_fails_when_required_subject_minimum_cannot_fit():
    sections = [
        (f"resident-{index}", f"어르신 확인 필요 · 항목 {index} " + "라" * 230, ["변화 내용"])
        for index in range(1, 10)
    ]
    answer = "\n".join(
        f"{label}\n- {summaries[0]}" for _, label, summaries in sections
    )

    with pytest.raises(RuntimeError, match="모든 대상"):
        main_module._fit_ai_help_comparison_body(
            period_notice="최근 7일 기록",
            answer=answer,
            safety_notice="\n\n사람 확인 필요",
            subject_sections=sections,
        )


def test_ai_help_final_body_subject_check_fails_closed_after_budgeting():
    sections = [
        ("resident-a", "어르검증A", ["첫 변화"]),
        ("resident-b", "어르검증B", ["둘째 변화"]),
    ]

    with pytest.raises(RuntimeError, match="최종 저장 답변"):
        main_module._require_ai_help_comparison_body_subjects(
            body="최근 7일 기록\n\n어르검증A\n- 첫 변화",
            subject_sections=sections,
        )


@pytest.mark.parametrize(
    "sections",
    [
        [("resident-a", "어르짧음A", ["짧은 변화"])],
        [
            ("resident-a", "어르짧음A", ["첫 변화", "후속 변화"]),
            ("resident-b", "어르짧음B", ["다른 변화"]),
        ],
    ],
)
def test_ai_help_comparison_budget_keeps_short_existing_output_unchanged(sections):
    answer = "\n".join(
        f"{label}\n" + "\n".join(f"- {summary}" for summary in summaries)
        for _, label, summaries in sections
    )
    expected = f"최근 7일 기록\n\n{answer}\n\n사람 확인 필요"

    body = main_module._fit_ai_help_comparison_body(
        period_notice="최근 7일 기록",
        answer=answer,
        safety_notice="\n\n사람 확인 필요",
        subject_sections=sections,
    )

    assert body == expected


def test_ai_help_response_fails_closed_when_answer_and_evidence_subject_sets_differ(
    monkeypatch,
):
    """A completed comparison may not hide a subject that remains in evidence."""
    selected_resident_ids: list[str] = []
    source_message_id = None

    def inconsistent_record_model(**kwargs):
        chosen = [
            fact
            for fact in kwargs["facts"]
            if fact["message_id"] == source_message_id
            and str(fact.get("resident_id") or "") in selected_resident_ids
        ]
        assert len(chosen) == 2
        return {
            "processing_method": "local_ai",
            "model_used": "test-local-record-model",
            "answer": "모델이 한 대상을 누락한 합성 답변입니다.",
            "evidence_ids": [source_message_id],
            "selected_facts": chosen,
            "focus_facts": chosen[:1],
            "fallback_reason": None,
            "ai_elapsed_ms": 11,
        }

    monkeypatch.setattr(main_module, "run_record_model", inconsistent_record_model)
    with TestClient(app) as client:
        me = _login(client)
        rooms = client.get("/api/rooms").json()
        ordinary_room = next(room for room in rooms if room["kind"] not in {"ai", "self"})
        ai_room = next(room for room in rooms if room["kind"] == "ai")
        with SessionLocal() as db:
            requester = db.get(User, me["id"])
            suffix = uuid4().hex[:8]
            residents = [
                Resident(
                    organization_id=requester.organization_id,
                    internal_code=f"AI-SUBJECT-MISMATCH-{suffix}-{index}",
                    display_name=f"어르불일치{index}",
                    service_type="home_care",
                    is_test_data=True,
                )
                for index in (1, 2)
            ]
            db.add_all(residents)
            db.flush()
            source = Message(
                organization_id=requester.organization_id,
                room_id=UUID(ordinary_room["id"]),
                sender_id=requester.id,
                body="두 대상의 변화가 함께 기록된 합성 검증 원문",
                is_test_data=True,
                created_at=datetime.now(timezone.utc) - timedelta(hours=1),
            )
            db.add(source)
            db.flush()
            db.add_all(
                [
                    MessageResidentLink(
                        organization_id=requester.organization_id,
                        message_id=source.id,
                        resident_id=resident.id,
                        source="manual",
                        status="confirmed",
                        reviewed_by_id=requester.id,
                        reviewed_at=datetime.now(timezone.utc),
                    )
                    for resident in residents
                ]
            )
            db.commit()
            source_message_id = source.id
            selected_resident_ids.extend(str(resident.id) for resident in residents)

        sent = client.post(
            f"/api/rooms/{ai_room['id']}/messages",
            json={
                "body": "최근 7일간 상태가 달라진 어르신과 근거를 알려줘.",
                "client_request_id": str(uuid4()),
            },
            headers=ORIGIN,
        )
        assert sent.status_code == 201, sent.text
        assistant = next(
            item
            for item in client.get(f"/api/rooms/{ai_room['id']}/messages").json()
            if (item.get("ai_help") or {}).get("source_message_id") == sent.json()["id"]
        )
        assert assistant["ai_help"]["status"] == "failed"
        assert assistant["ai_help"]["evidence"] == []
        assert "완료하지 못했습니다" in assistant["body"]


def test_ai_help_comparison_keeps_focus_for_answer_and_selected_event_for_evidence():
    first_id = uuid4()
    second_id = uuid4()
    focus = {
        "message_id": first_id,
        "resident_id": str(uuid4()),
        "resident_name": "어르비교01",
        "summary": "최초 변화",
    }
    followup = {
        "message_id": second_id,
        "resident_id": focus["resident_id"],
        "resident_name": "어르비교01",
        "summary": "후속 확인",
    }
    result = {"focus_facts": [focus], "selected_facts": [focus, followup]}

    assert main_module._ai_help_comparison_facts(
        result, [focus, followup], [first_id, second_id], include_event_facts=False
    ) == [focus]
    assert main_module._ai_help_comparison_facts(
        result, [focus, followup], [first_id, second_id], include_event_facts=True
    ) == [focus, followup]


def test_ai_help_general_guidance_uses_no_internal_records_and_has_no_fake_evidence(monkeypatch):
    captured = {}

    def general_model(**kwargs):
        captured.update(kwargs)
        return {
            "processing_method": "local_ai",
            "model_used": "test-local-general-model",
            "answer": "피부 상태를 먼저 확인하고 청결과 건조를 유지하며 불편 여부를 관찰합니다.",
            "fallback_reason": None,
            "ai_elapsed_ms": 15,
        }

    monkeypatch.setattr(main_module, "run_general_help_model", general_model, raising=False)
    monkeypatch.setattr(
        main_module,
        "run_record_model",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("일반질문은 기록 모델을 호출하면 안 됩니다.")),
    )
    with TestClient(app) as client:
        _login(client)
        rooms = client.get("/api/rooms").json()
        ai_room = next(room for room in rooms if room["kind"] == "ai")
        ordinary_room = next(room for room in rooms if room["kind"] not in {"ai", "self"})
        ordinary = client.post(
            f"/api/rooms/{ordinary_room['id']}/messages",
            json={"body": "일반 업무질문에 섞이면 안 되는 내부 합성 기록"},
            headers=ORIGIN,
        )
        assert ordinary.status_code == 201, ordinary.text
        sent = client.post(
            f"/api/rooms/{ai_room['id']}/messages",
            json={
                "body": "어르신 기저귀 케어 시 원칙과 유의사항을 알려줘.",
                "client_request_id": str(uuid4()),
            },
            headers=ORIGIN,
        )
        assert sent.status_code == 201, sent.text
        assistant = next(
            item
            for item in client.get(f"/api/rooms/{ai_room['id']}/messages").json()
            if (item.get("ai_help") or {}).get("source_message_id") == sent.json()["id"]
        )
        assert assistant["ai_help"]["status"] == "completed"
        assert assistant["ai_help"]["question_type"] == "general_guidance"
        assert assistant["ai_help"]["evidence"] == []
        assert assistant["ai_help"]["external_transmission"] is False
        assert assistant["ai_help"]["processing_location"] == "local"
        assert "피부 상태" in assistant["body"]
        assert "일반 업무질문에 섞이면 안 되는 내부 합성 기록" not in str(captured)
        assert captured["conversation"] == []


def test_ai_help_ambiguous_and_high_stakes_questions_keep_human_decision_boundaries(monkeypatch):
    calls = []

    def general_model(**kwargs):
        calls.append(kwargs["question"])
        return {
            "processing_method": "local_ai",
            "model_used": "test-local-general-model",
            "answer": "일반적인 확인 순서를 정리합니다.",
            "fallback_reason": None,
            "ai_elapsed_ms": 10,
        }

    monkeypatch.setattr(main_module, "run_general_help_model", general_model, raising=False)
    with TestClient(app) as client:
        _login(client)
        ai_room = next(room for room in client.get("/api/rooms").json() if room["kind"] == "ai")

        ambiguous = client.post(
            f"/api/rooms/{ai_room['id']}/messages",
            json={"body": "도와줘.", "client_request_id": str(uuid4())},
            headers=ORIGIN,
        )
        ambiguous_answer = next(
            item for item in client.get(f"/api/rooms/{ai_room['id']}/messages").json()
            if (item.get("ai_help") or {}).get("source_message_id") == ambiguous.json()["id"]
        )
        assert ambiguous_answer["ai_help"]["question_type"] == "clarification"
        assert "무엇을 알고 싶으신지" in ambiguous_answer["body"]
        assert "한 문장" in ambiguous_answer["body"]
        assert "확인 가능한 기록 근거" not in ambiguous_answer["body"]
        assert calls == []

        clinical = client.post(
            f"/api/rooms/{ai_room['id']}/messages",
            json={"body": "투약을 중단할지 결정해 줘.", "client_request_id": str(uuid4())},
            headers=ORIGIN,
        )
        clinical_answer = next(
            item for item in client.get(f"/api/rooms/{ai_room['id']}/messages").json()
            if (item.get("ai_help") or {}).get("source_message_id") == clinical.json()["id"]
        )
        assert "자동으로 결정할 수 없습니다" in clinical_answer["body"]
        assert "담당자" in clinical_answer["body"]

        policy = client.post(
            f"/api/rooms/{ai_room['id']}/messages",
            json={"body": "최신 공단 기준을 확정해 줘.", "client_request_id": str(uuid4())},
            headers=ORIGIN,
        )
        policy_answer = next(
            item for item in client.get(f"/api/rooms/{ai_room['id']}/messages").json()
            if (item.get("ai_help") or {}).get("source_message_id") == policy.json()["id"]
        )
        assert "최신 원문" in policy_answer["body"]
        assert "기관 지침" in policy_answer["body"]


def test_ai_help_record_selection_miss_is_not_reported_as_an_ai_server_failure(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "run_record_model",
        lambda **_kwargs: {
            "processing_method": "rules",
            "model_used": "test-local-record-model",
            "fallback_reason": "question_selection_invalid",
            "evidence_ids": [],
            "ai_elapsed_ms": 12,
        },
    )
    with TestClient(app) as client:
        me = _login(client)
        rooms = client.get("/api/rooms").json()
        ai_room = next(room for room in rooms if room["kind"] == "ai")
        with SessionLocal() as db:
            user = db.get(User, me["id"])
            db.add(
                Resident(
                    organization_id=user.organization_id,
                    internal_code=f"AI-SELECTION-MISS-{uuid4().hex[:8]}",
                    display_name="어르9999",
                    service_type="home_care",
                    is_test_data=True,
                )
            )
            db.commit()
        sent = client.post(
            f"/api/rooms/{ai_room['id']}/messages",
            json={
                "body": "어르9999의 최근 식사 관련 기록을 찾아줘.",
                "client_request_id": str(uuid4()),
            },
            headers=ORIGIN,
        )
        assistant = next(
            item for item in client.get(f"/api/rooms/{ai_room['id']}/messages").json()
            if (item.get("ai_help") or {}).get("source_message_id") == sent.json()["id"]
        )
        assert assistant["ai_help"]["status"] == "completed"
        assert assistant["ai_help"]["question_type"] == "record_search"
        assert assistant["ai_help"]["evidence"] == []
        assert "현재 참여 중인 일반 채팅방 기록을 확인" in assistant["body"]
        assert "관련 내용을 찾지 못했습니다" in assistant["body"]


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("timeout", "시간 안에"),
        ("busy", "다른 AI 작업"),
        ("local_connection_failed", "로컬 AI 서버에 연결"),
    ],
)
def test_ai_help_model_failures_keep_precise_retry_guidance(monkeypatch, reason, expected):
    monkeypatch.setattr(
        main_module,
        "run_general_help_model",
        lambda **_kwargs: {
            "processing_method": "rules",
            "model_used": None,
            "fallback_reason": reason,
            "ai_elapsed_ms": 12,
        },
    )
    with TestClient(app) as client:
        _login(client)
        ai_room = next(room for room in client.get("/api/rooms").json() if room["kind"] == "ai")
        sent = client.post(
            f"/api/rooms/{ai_room['id']}/messages",
            json={
                "body": "낙상 예방을 위해 직원이 확인할 점을 알려줘.",
                "client_request_id": str(uuid4()),
            },
            headers=ORIGIN,
        )
        assistant = next(
            item for item in client.get(f"/api/rooms/{ai_room['id']}/messages").json()
            if (item.get("ai_help") or {}).get("source_message_id") == sent.json()["id"]
        )
        assert assistant["ai_help"]["status"] == "failed"
        assert assistant["ai_help"]["error_message"] == reason
        assert expected in assistant["body"]


def test_ai_help_does_not_disguise_a_local_model_failure_as_an_ai_answer(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "run_record_model",
        lambda **_kwargs: {
            "processing_method": "rules",
            "model_used": None,
            "fallback_reason": "model_not_ready",
            "ai_elapsed_ms": 30000,
        },
    )
    with TestClient(app) as client:
        _login(client)
        ai_room = next(room for room in client.get("/api/rooms").json() if room["kind"] == "ai")
        sent = client.post(
            f"/api/rooms/{ai_room['id']}/messages",
            json={"body": "합성 기록에서 실패 경로를 찾아줘", "client_request_id": str(uuid4())},
            headers=ORIGIN,
        )
        assert sent.status_code == 201, sent.text
        assistant = next(
            item
            for item in client.get(f"/api/rooms/{ai_room['id']}/messages").json()
            if (item.get("ai_help") or {}).get("source_message_id") == sent.json()["id"]
        )
        assert assistant["ai_help"]["status"] == "failed"
        assert assistant["ai_help"]["external_transmission"] is False
        assert assistant["ai_help"]["evidence"] == []
        assert assistant["ai_help"]["error_message"] == "model_not_ready"


def test_ai_help_queued_request_can_be_cancelled_and_retried(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "process_ai_help_message_background",
        lambda *_args, **_kwargs: None,
    )
    with TestClient(app) as client:
        _login(client)
        ai_room = next(room for room in client.get("/api/rooms").json() if room["kind"] == "ai")
        sent = client.post(
            f"/api/rooms/{ai_room['id']}/messages",
            json={"body": "취소·재시도 합성 질문", "client_request_id": str(uuid4())},
            headers=ORIGIN,
        )
        assistant = next(
            item
            for item in client.get(f"/api/rooms/{ai_room['id']}/messages").json()
            if (item.get("ai_help") or {}).get("source_message_id") == sent.json()["id"]
        )
        cancelled = client.post(
            f"/api/ai-help/messages/{assistant['id']}/cancel",
            headers=ORIGIN,
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["ai_help"]["status"] == "cancelled"

        retried = client.post(
            f"/api/ai-help/messages/{assistant['id']}/retry",
            headers=ORIGIN,
        )
        assert retried.status_code == 200, retried.text
        assert retried.json()["id"] == assistant["id"]
        assert retried.json()["ai_help"]["status"] == "queued"


def test_ai_help_clipboard_image_explains_completed_text_without_work_item(monkeypatch):
    unique_text = "합성 관찰표에서 수분 섭취 확인과 오후 재확인이 완료됐습니다."

    def local_record_model(**kwargs):
        selected = next(fact for fact in kwargs["facts"] if unique_text in fact["summary"])
        return {
            "processing_method": "local_ai",
            "model_used": "test-local-record-model",
            "answer": f"판독 결과의 핵심은 {unique_text}",
            "evidence_ids": [selected["message_id"]],
            "fallback_reason": None,
            "ai_elapsed_ms": 12,
        }

    monkeypatch.setattr(main_module, "process_ai_help_message_background", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_module, "run_record_model", local_record_model)
    with TestClient(app) as client:
        _login(client)
        ai_room = next(room for room in client.get("/api/rooms").json() if room["kind"] == "ai")
        sent = client.post(
            f"/api/rooms/{ai_room['id']}/messages-with-files",
            data={
                "body": "이 합성 이미지를 설명해 주세요.",
                "client_request_id": str(uuid4()),
            },
            files={"files": ("clipboard-synthetic.png", PNG_1X1, "image/png")},
            headers=ORIGIN,
        )
        assert sent.status_code == 201, sent.text
        assert len(sent.json()["attachments"]) == 1
        attachment_id = sent.json()["attachments"][0]["id"]
        with SessionLocal() as db:
            extraction = db.scalar(
                select(AttachmentTextExtraction).where(
                    AttachmentTextExtraction.attachment_id == attachment_id
                )
            )
            extraction.status = "completed"
            extraction.extracted_text = unique_text
            assistant = db.scalar(
                select(Message).where(
                    Message.room_id == ai_room["id"],
                    Message.extra_data["ai_help"]["source_message_id"].as_string()
                    == sent.json()["id"],
                )
            )
            db.commit()
            assistant_id = assistant.id

        main_module._process_ai_help_message_sync(
            sent.json()["id"], assistant_id, _login(client)["id"]
        )
        assistant = next(
            item
            for item in client.get(f"/api/rooms/{ai_room['id']}/messages").json()
            if (item.get("ai_help") or {}).get("source_message_id") == sent.json()["id"]
        )
        assert assistant["ai_help"]["status"] == "completed"
        assert assistant["ai_help"]["question_type"] == "attachment_guidance"
        assert unique_text in assistant["body"]
        assert assistant["ai_help"]["evidence"] == [
            {
                "source_no": 1,
                "source_type": "attachment",
                "attachment_id": attachment_id,
                "statement": f"첨부 clipboard-synthetic.png: {unique_text}",
            }
        ]
        assert assistant["ai_help"]["external_transmission"] is False
        with SessionLocal() as db:
            assert int(
                db.scalar(
                    select(func.count(WorkItem.id)).where(
                        WorkItem.source_message_id == sent.json()["id"]
                    )
                )
                or 0
            ) == 0


def test_ai_help_completed_document_extraction_does_not_create_work_item(monkeypatch):
    unique_document_text = "합성 문서에서 오후 재확인 완료 상태를 확인했습니다."

    def local_record_model(**kwargs):
        selected = next(
            fact for fact in kwargs["facts"] if unique_document_text in fact["summary"]
        )
        return {
            "processing_method": "local_ai",
            "model_used": "test-local-record-model",
            "answer": "합성 문서에는 후속조치 완료 상태가 기록돼 있습니다.",
            "evidence_ids": [selected["message_id"]],
            "fallback_reason": None,
            "ai_elapsed_ms": 9,
        }

    monkeypatch.setattr(
        main_module,
        "process_ai_help_message_background",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(main_module, "run_record_model", local_record_model)
    with TestClient(app) as client:
        _login(client)
        ai_room = next(room for room in client.get("/api/rooms").json() if room["kind"] == "ai")
        sent = client.post(
            f"/api/rooms/{ai_room['id']}/messages-with-files",
            data={
                "body": "첨부한 합성 문서의 완료 상태를 알려주세요.",
                "client_request_id": str(uuid4()),
            },
            files={
                "files": (
                    "synthetic-status.txt",
                    unique_document_text.encode("utf-8"),
                    "text/plain",
                )
            },
            headers=ORIGIN,
        )
        assert sent.status_code == 201, sent.text
        with SessionLocal() as db:
            source_message = db.get(Message, sent.json()["id"])
            attachment = db.scalar(
                select(MessageAttachment).where(
                    MessageAttachment.message_id == sent.json()["id"]
                )
            )
            extraction = db.scalar(
                select(AttachmentTextExtraction).where(
                    AttachmentTextExtraction.attachment_id == attachment.id
                )
            )
            assert extraction.status == "completed"
            assert unique_document_text in extraction.extracted_text
            extraction.status = "reviewed"
            extraction.reviewed_text = unique_document_text
            db.commit()
            assert main_module._ensure_work_item(db, source_message, force=True) is None
            assistant = db.scalar(
                select(Message).where(
                    Message.room_id == ai_room["id"],
                    Message.extra_data["ai_help"]["source_message_id"].as_string()
                    == sent.json()["id"],
                )
            )
            assistant_id = assistant.id
            assert int(
                db.scalar(
                    select(func.count(WorkItem.id)).where(
                        WorkItem.source_message_id == sent.json()["id"]
                    )
                )
                or 0
            ) == 0

        main_module._process_ai_help_message_sync(
            sent.json()["id"], assistant_id, _login(client)["id"]
        )
        assistant_payload = next(
            item
            for item in client.get(f"/api/rooms/{ai_room['id']}/messages").json()
            if (item.get("ai_help") or {}).get("source_message_id") == sent.json()["id"]
        )
        assert assistant_payload["ai_help"]["status"] == "completed"
        assert assistant_payload["ai_help"]["question_type"] == "attachment_guidance"
        assert "후속조치 완료" in assistant_payload["body"]
        assert assistant_payload["ai_help"]["evidence"][0]["source_type"] == "attachment"
        assert assistant_payload["ai_help"]["evidence"][0]["attachment_id"] == str(attachment.id)


def test_ai_help_failed_attachment_is_not_presented_as_read(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "process_ai_help_message_background",
        lambda *_args, **_kwargs: None,
    )
    with TestClient(app) as client:
        me = _login(client)
        ai_room = next(room for room in client.get("/api/rooms").json() if room["kind"] == "ai")
        sent = client.post(
            f"/api/rooms/{ai_room['id']}/messages-with-files",
            data={
                "body": "첨부 문서의 핵심 내용을 알려줘.",
                "client_request_id": str(uuid4()),
            },
            files={"files": ("failed-synthetic.txt", b"SYNTHETIC", "text/plain")},
            headers=ORIGIN,
        )
        with SessionLocal() as db:
            attachment = db.scalar(
                select(MessageAttachment).where(
                    MessageAttachment.message_id == sent.json()["id"]
                )
            )
            attachment.text_extraction.status = "failed"
            attachment.text_extraction.error_message = "synthetic failure"
            assistant = db.scalar(
                select(Message).where(
                    Message.room_id == ai_room["id"],
                    Message.extra_data["ai_help"]["source_message_id"].as_string()
                    == sent.json()["id"],
                )
            )
            db.commit()
            assistant_id = assistant.id

        main_module._process_ai_help_message_sync(sent.json()["id"], assistant_id, me["id"])
        payload = next(
            item
            for item in client.get(f"/api/rooms/{ai_room['id']}/messages").json()
            if (item.get("ai_help") or {}).get("source_message_id") == sent.json()["id"]
        )
        assert payload["ai_help"]["status"] == "failed"
        assert payload["ai_help"]["evidence"] == []
        assert "판독에 실패" in payload["body"]
        assert "읽지 않았" in payload["body"]


def test_ai_help_attachment_evidence_is_owned_and_separate_from_record_facts(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "process_ai_help_message_background",
        lambda *_args, **_kwargs: None,
    )
    with TestClient(app) as client:
        me = _login(client)
        ai_room = next(room for room in client.get("/api/rooms").json() if room["kind"] == "ai")
        sent = client.post(
            f"/api/rooms/{ai_room['id']}/messages-with-files",
            data={
                "body": "첨부 문서의 핵심 내용을 알려줘.",
                "client_request_id": str(uuid4()),
            },
            files={
                "files": (
                    "owned-synthetic.txt",
                    "합성 전용 첨부 근거".encode("utf-8"),
                    "text/plain",
                )
            },
            headers=ORIGIN,
        )
        assert sent.status_code == 201, sent.text
        with SessionLocal() as db:
            source = db.get(Message, sent.json()["id"])
            owner = db.get(User, me["id"])
            attachment = source.attachments[0]
            attachment.text_extraction.status = "completed"
            attachment.text_extraction.extracted_text = "합성 전용 첨부 근거"

            suffix = uuid4().hex[:8]
            staff = Staff(
                organization_id=owner.organization_id,
                internal_code=f"AI-ATTACH-{suffix}",
                display_name="가상 직원 첨부차단",
                job_title="합성 시험직",
                is_test_data=True,
            )
            db.add(staff)
            db.flush()
            other = User(
                organization_id=owner.organization_id,
                staff_id=staff.id,
                username=f"ai-attach-{suffix}",
                display_name="가상 직원 첨부차단",
                password_hash=hash_password("SyntheticPass!234"),
                is_active=True,
            )
            staff_role = db.scalar(select(Role).where(Role.code == "staff"))
            other.roles = [staff_role]
            db.add(other)
            db.commit()

            facts, _, _ = main_module._ai_help_attachment_facts(
                db, requester=owner, source=source
            )
            assert [fact["message_id"] for fact in facts] == [attachment.id]
            main_module._recheck_ai_help_attachment_access(
                db,
                owner,
                source_message_id=source.id,
                attachment_ids={attachment.id},
            )
            with pytest.raises(HTTPException) as cross_user:
                main_module._recheck_ai_help_attachment_access(
                    db,
                    other,
                    source_message_id=source.id,
                    attachment_ids={attachment.id},
                )
            assert cross_user.value.status_code == 403
            with pytest.raises(HTTPException) as manipulated:
                main_module._recheck_ai_help_attachment_access(
                    db,
                    owner,
                    source_message_id=source.id,
                    attachment_ids={uuid4()},
                )
            assert manipulated.value.status_code == 403

            record_facts, _, _ = main_module._ai_help_member_scoped_facts(
                db, requester=owner, source=source
            )
            assert attachment.id not in {fact["message_id"] for fact in record_facts}
            assert source.id not in {fact["message_id"] for fact in record_facts}
