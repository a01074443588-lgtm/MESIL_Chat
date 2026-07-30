from fastapi.testclient import TestClient
from sqlalchemy import select
from uuid import UUID
from datetime import datetime
from zoneinfo import ZoneInfo

from app import main as main_module
from app.database import SessionLocal
from app.main import app
from app.models import (
    MessageResidentLink,
    OcrCorrectionEvent,
    Organization,
    RecipientRoom,
    Resident,
    WorkItemDocumentDraft,
)


ORIGIN = {"origin": "http://testserver"}
PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00"
    b"\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
)
WAV_SAMPLE = (
    b"RIFF\x24\x00\x00\x00WAVEfmt "
    b"\x10\x00\x00\x00\x01\x00\x01\x00\x40\x1f\x00\x00"
    b"\x80\x3e\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
)
MP4_SAMPLE = (
    b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
    b"\x00\x00\x00\x08free"
)


def test_period_evidence_is_separated_by_resident():
    text = (
        "시설(가명)003: 아침 식사 절반 드심. 물 섭취 권유함.\n"
        "시설(가명)011: 오전 프로그램은 피곤하다고 거부함.\n"
        "시설 018: 화장실 2회 도움드림."
    )
    resident_names = [
        "시설(가명)003",
        "시설(가명)011",
        "시설(가명)018",
    ]

    resident_003 = main_module._resident_specific_period_text(
        text,
        target_name="시설(가명)003",
        resident_names=resident_names,
    )
    resident_011 = main_module._resident_specific_period_text(
        text,
        target_name="시설(가명)011",
        resident_names=resident_names,
    )
    resident_018 = main_module._resident_specific_period_text(
        text,
        target_name="시설(가명)018",
        resident_names=resident_names,
    )

    assert "아침 식사 절반" in resident_003
    assert "오전 프로그램" not in resident_003
    assert "오전 프로그램" in resident_011
    assert "화장실 2회" not in resident_011
    assert "시설 018" in resident_018
    assert "아침 식사 절반" not in resident_018


def test_period_evidence_does_not_assign_unscoped_reply_to_last_resident():
    resident_names = ["시설(가명)003", "시설(가명)011"]
    text = (
        "시설(가명)003: 아침 식사 절반 드심.\n"
        "시설(가명)011: 오전 프로그램은 피곤하다고 거부함.\n"
        "[답글 · 사회복지사] 다음 식사량을 다시 확인해 주세요."
    )

    resident_003 = main_module._resident_specific_period_text(
        text,
        target_name="시설(가명)003",
        resident_names=resident_names,
    )
    resident_011 = main_module._resident_specific_period_text(
        text,
        target_name="시설(가명)011",
        resident_names=resident_names,
    )

    assert "다음 식사량" not in resident_003
    assert "다음 식사량" not in resident_011

    explicit_reply = (
        f"{text}\n"
        "[답글 · 사회복지사] 시설(가명)003 어르신의 다음 식사량을 "
        "다시 확인해 주세요."
    )
    resident_003_with_reply = main_module._resident_specific_period_text(
        explicit_reply,
        target_name="시설(가명)003",
        resident_names=resident_names,
    )
    resident_011_with_reply = main_module._resident_specific_period_text(
        explicit_reply,
        target_name="시설(가명)011",
        resident_names=resident_names,
    )
    assert "다음 식사량" in resident_003_with_reply
    assert "다음 식사량" not in resident_011_with_reply


def test_briefing_uses_specific_pending_checks_without_generic_ai_questions():
    safety_text = [
        "시설(가명)012 어르신이 오전 9시 20분 복도에서 비틀거리셔서 "
        "부축했고 간호팀에 전달했습니다."
    ]
    pending = main_module._briefing_pending_checks(
        safety_text,
        [],
    )
    assert pending == [
        "간호팀 전달 이후 보행상태와 어지럼 여부가 기록되지 않았습니다. "
        "다음 이동 전 담당 요양보호사가 확인하고 결과를 남겨주세요."
    ]
    risk_reason = main_module._briefing_risk_reason(safety_text, "high")
    assert "비틀거림" in risk_reason
    assert "넘어짐" not in risk_reason
    assert "출혈" not in risk_reason

    completed = main_module._briefing_pending_checks(
        [
            *safety_text,
            "오전 10시 10분 재확인 시 어지럼 호소 없고 "
            "휠체어로 이동 지원했습니다.",
        ],
        [],
    )
    assert completed == []


def test_briefing_does_not_confuse_skin_change_or_future_plan_with_completed_work():
    skin_text = [
        "시설(가명)017 어르신 엉치 발적 범위가 넓어지거나 "
        "피부 변화가 있으면 바로 알려 주세요."
    ]
    assert main_module._briefing_risk_reason(skin_text, "high").startswith("발적")

    future_contact = [
        "변화가 반복되면 간호 확인 결과를 보호자에게 함께 안내하기로 했습니다."
    ]
    assert main_module._briefing_completed_actions(future_contact) == []

    completed_contact = [
        "보행 상태를 확인해 보호자에게 안내했습니다."
    ]
    assert main_module._briefing_pending_checks(completed_contact, []) == []

    completed_request = [
        "엉치 압박을 줄이도록 체위를 변경하고 간호팀에 확인을 요청했습니다."
    ]
    assert main_module._briefing_completed_actions(completed_request) == completed_request
    completed_request_pending = main_module._briefing_pending_checks(
        [
            "시설(가명)017 어르신 엉치 부위가 붉게 보였습니다.",
            *completed_request,
        ],
        [],
    )
    assert completed_request_pending == [
        "피부 상태 확인 이후의 변화가 기록되지 않았습니다. "
        "다음 돌봄 전에 담당 요양보호사 또는 간호팀이 같은 부위를 확인해 주세요."
    ]

    future_request = [
        "엉치 압박을 줄이도록 체위변경을 요청할 예정입니다."
    ]
    assert main_module._briefing_completed_actions(future_request) == []

    continuing_plan = [
        "시설(가명)017 어르신 체위변경은 계속 시행하겠습니다."
    ]
    assert main_module._briefing_completed_actions(continuing_plan) == []

    guardian_request = [
        "보호자는 오늘 밤 수면 상태도 이어서 확인해 달라고 요청했습니다."
    ]
    assert main_module._briefing_pending_checks(guardian_request, []) == guardian_request


def test_briefing_document_candidates_are_conservative():
    normal_meal = main_module.RecordDraft.model_validate(
        main_module.build_prototype_suggestion(
            {
                "body": "시설(가명)008 어르신 점심 식사와 수분 섭취 양호함.",
                "resident_name": "시설(가명)008",
                "resident_names": ["시설(가명)008"],
            }
        )
    )
    assert main_module._briefing_daily_document_types(
        normal_meal,
        "시설(가명)008 어르신 점심 식사와 수분 섭취 양호함.",
    ) == ["care_service_record"]

    reduced_meal = main_module.RecordDraft.model_validate(
        main_module.build_prototype_suggestion(
            {
                "body": "시설(가명)003 어르신 식사량이 평소보다 적어 절반 드심.",
                "resident_name": "시설(가명)003",
                "resident_names": ["시설(가명)003"],
            }
        )
    )
    assert main_module._briefing_daily_document_types(
        reduced_meal,
        "시설(가명)003 어르신 식사량이 평소보다 적어 절반 드심.",
    ) == ["care_service_record", "nursing_log"]


def test_briefing_extracts_meal_fraction_sequence_only_from_meal_sentences():
    assert main_module._briefing_meal_fractions(
        [
            "시설(가명)003 어르신 식사량 2/3 드심.",
            "체온 37.6 확인됨. 식사량은 1/2 드심.",
            "오늘도 식사량 절반 드심.",
        ]
    ) == ["2/3", "1/2", "1/2"]


def test_briefing_classifies_staggering_as_safety():
    suggestion = main_module.RecordDraft.model_validate(
        main_module.build_prototype_suggestion(
            {
                "body": "시설(가명)012 어르신이 복도에서 비틀거리셔서 "
                "부축했고 간호팀에 전달했습니다.",
                "resident_name": "시설(가명)012",
                "resident_names": ["시설(가명)012"],
            }
        )
    )
    assert suggestion.classification == "safety"
    assert suggestion.risk_level == "high"
    assert main_module._briefing_daily_document_types(
        suggestion,
        "시설(가명)012 어르신이 복도에서 비틀거리셔서 "
        "부축했고 간호팀에 전달했습니다.",
    ) == ["nursing_log", "care_service_record"]


def test_briefing_does_not_create_documents_from_uncertain_values():
    uncertain_text = (
        "시설(가명)010 어르신 혈압 12?에 7?로 보이며 "
        "숫자 판독 확인이 필요합니다."
    )
    suggestion = main_module.RecordDraft.model_validate(
        main_module.build_prototype_suggestion(
            {
                "body": uncertain_text,
                "resident_name": "시설(가명)010",
                "resident_names": ["시설(가명)010"],
            }
        )
    )
    assert main_module._briefing_daily_document_types(
        suggestion,
        uncertain_text,
    ) == []


def test_routine_repositioning_does_not_create_nursing_log():
    text = "시설(가명)020 어르신 체위변경을 도와드렸고 편안하다고 하셨습니다."
    suggestion = main_module.RecordDraft.model_validate(
        main_module.build_prototype_suggestion(
            {
                "body": text,
                "resident_name": "시설(가명)020",
                "resident_names": ["시설(가명)020"],
            }
        )
    )
    assert suggestion.classification == "daily_care"
    assert main_module._briefing_daily_document_types(
        suggestion,
        text,
    ) == ["care_service_record"]


def login(client: TestClient, username: str, password: str):
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
        headers=ORIGIN,
    )
    assert response.status_code == 200, response.text
    return response.json()["user"]


def post(client: TestClient, path: str, payload: dict):
    return client.post(path, json=payload, headers=ORIGIN)


def create_unit(client: TestClient, unit_type: str, name: str) -> str:
    response = post(
        client,
        "/api/org-units",
        {"unit_type": unit_type, "name": name},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_floor_resident_photo_detail_and_processor_workdesk(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "transcribe_audio",
        lambda path, *, mime_type: (
            "가상 어르신 2-01 점심 식사량 확인 부탁드립니다."
        ),
    )
    with TestClient(app) as admin_client:
        login(admin_client, "admin", "AdminPass!234")
        business_id = create_unit(admin_client, "business", "가상 시설")
        department_id = create_unit(admin_client, "department", "가상 돌봄부")
        floor_2_id = create_unit(admin_client, "floor", "가상 2층")
        floor_3_id = create_unit(admin_client, "floor", "가상 3층")
        team_id = create_unit(admin_client, "team", "가상 돌봄팀")
        processor_team_id = create_unit(admin_client, "team", "가상 업무지원팀")
        for room_name, floor_id in [
            ("가상 2층 직원방", floor_2_id),
            ("가상 3층 직원방", floor_3_id),
        ]:
            room_response = post(
                admin_client,
                "/api/admin/rooms",
                {
                    "name": room_name,
                    "kind": "floor",
                    "scope_unit_id": floor_id,
                    "resident_scope": "floor",
                },
            )
            assert room_response.status_code == 201, room_response.text

        with SessionLocal() as db:
            organization = db.scalar(select(Organization).limit(1))
            room_2 = RecipientRoom(
                organization_id=organization.id,
                internal_code="TEST-ROOM-2",
                name="가상 2층 생활실",
                floor="가상 2층",
                floor_unit_id=UUID(floor_2_id),
            )
            room_3 = RecipientRoom(
                organization_id=organization.id,
                internal_code="TEST-ROOM-3",
                name="가상 3층 생활실",
                floor="가상 3층",
                floor_unit_id=UUID(floor_3_id),
            )
            db.add_all([room_2, room_3])
            db.flush()
            resident_2 = Resident(
                organization_id=organization.id,
                internal_code="TEST-R-2-01",
                display_name="가상 어르신 2-01",
                service_type="facility",
                room_id=room_2.id,
                is_test_data=True,
            )
            resident_3 = Resident(
                organization_id=organization.id,
                internal_code="TEST-R-3-01",
                display_name="가상 어르신 3-01",
                service_type="facility",
                room_id=room_3.id,
                is_test_data=True,
            )
            db.add_all([resident_2, resident_3])
            db.commit()
            resident_2_id = resident_2.id
            resident_3_id = resident_3.id

        staff_payload = {
            "username": "resident_writer",
            "full_name": "가상 작성자",
            "password": "WriterPass!234",
            "role": "staff",
            "business_id": business_id,
            "department_id": department_id,
            "job_code": "caregiver",
            "floor_id": floor_2_id,
            "team_id": team_id,
        }
        processor_payload = {
            "username": "record_processor",
            "full_name": "가상 처리담당자",
            "password": "ProcessPass!234",
            "role": "staff",
            "can_process_records": True,
            "business_id": business_id,
            "department_id": department_id,
            "job_code": "social_worker",
            "floor_id": floor_2_id,
            "team_id": processor_team_id,
        }
        outside_nursing_payload = {
            "username": "outside_nursing_assistant",
            "full_name": "가상 시설 간호조무사",
            "password": "NursingPass!234",
            "role": "staff",
            "can_process_records": True,
            "business_id": business_id,
            "department_id": department_id,
            "job_code": "nursing_assistant",
            "floor_id": floor_3_id,
            "team_id": processor_team_id,
        }
        third_party_payload = {
            "username": "resident_observer",
            "full_name": "가상 제3자 직원",
            "password": "ObserverPass!234",
            "role": "staff",
            "business_id": business_id,
            "department_id": department_id,
            "job_code": "caregiver",
            "floor_id": floor_2_id,
            "team_id": team_id,
        }
        staff_response = post(admin_client, "/api/employees", staff_payload)
        processor_response = post(admin_client, "/api/employees", processor_payload)
        outside_nursing_response = post(
            admin_client,
            "/api/employees",
            outside_nursing_payload,
        )
        third_party_response = post(
            admin_client,
            "/api/employees",
            third_party_payload,
        )
        assert staff_response.status_code == 201, staff_response.text
        assert processor_response.status_code == 201, processor_response.text
        assert outside_nursing_response.status_code == 201, outside_nursing_response.text
        assert third_party_response.status_code == 201, third_party_response.text
        priority_room_response = post(
            admin_client,
            "/api/admin/rooms",
            {
                "name": "가상 3층 어르신 우선 지정방",
                "kind": "custom",
                "member_ids": [staff_response.json()["id"]],
                "resident_scope": "floor",
                "resident_scope_unit_id": floor_3_id,
            },
        )
        assert priority_room_response.status_code == 201, priority_room_response.text

        writer = TestClient(app)
        processor = TestClient(app)
        outside_processor = TestClient(app)
        third_party = TestClient(app)
        login(writer, "resident_writer", "WriterPass!234")
        login(processor, "record_processor", "ProcessPass!234")
        login(
            outside_processor,
            "outside_nursing_assistant",
            "NursingPass!234",
        )
        login(third_party, "resident_observer", "ObserverPass!234")
        assert (
            post(
                writer,
                "/api/auth/password",
                {
                    "current_password": "WriterPass!234",
                    "new_password": "WriterReady!234",
                },
            ).status_code
            == 200
        )
        assert (
            post(
                processor,
                "/api/auth/password",
                {
                    "current_password": "ProcessPass!234",
                    "new_password": "ProcessReady!234",
                },
            ).status_code
            == 200
        )
        assert (
            post(
                outside_processor,
                "/api/auth/password",
                {
                    "current_password": "NursingPass!234",
                    "new_password": "NursingReady!234",
                },
            ).status_code
            == 200
        )
        assert (
            post(
                third_party,
                "/api/auth/password",
                {
                    "current_password": "ObserverPass!234",
                    "new_password": "ObserverReady!234",
                },
            ).status_code
            == 200
        )

        writer_rooms = writer.get("/api/rooms").json()
        floor_room = next(room for room in writer_rooms if room["name"] == "가상 2층 직원방")
        room_id = floor_room["id"]
        action_assignees_response = writer.get(
            f"/api/rooms/{room_id}/action-assignees"
        )
        assert action_assignees_response.status_code == 200
        action_assignee_ids = {
            candidate["id"] for candidate in action_assignees_response.json()
        }
        assert processor_response.json()["id"] in action_assignee_ids
        assert outside_nursing_response.json()["id"] not in action_assignee_ids
        residents = writer.get(f"/api/rooms/{room_id}/residents").json()
        residents_by_id = {resident["id"]: resident for resident in residents}
        assert residents[0]["id"] == str(resident_2_id)
        assert residents_by_id[str(resident_2_id)]["is_priority"]
        assert not residents_by_id[str(resident_3_id)]["is_priority"]
        priority_room_id = priority_room_response.json()["id"]
        priority_residents = writer.get(
            f"/api/rooms/{priority_room_id}/residents"
        ).json()
        priority_residents_by_id = {
            resident["id"]: resident for resident in priority_residents
        }
        assert priority_residents[0]["id"] == str(resident_3_id)
        assert priority_residents_by_id[str(resident_3_id)]["is_priority"]
        assert not priority_residents_by_id[str(resident_2_id)]["is_priority"]

        workdesk_residents = processor.get("/api/workdesk/residents")
        assert workdesk_residents.status_code == 200, workdesk_residents.text
        assert {
            resident["id"] for resident in workdesk_residents.json()
        } >= {str(resident_2_id), str(resident_3_id)}
        assert writer.get("/api/workdesk/residents").status_code == 403

        sent = writer.post(
            f"/api/rooms/{room_id}/messages-with-files",
            data={
                "body": "점심 식사량이 평소보다 적어 확인을 부탁드립니다.",
                "message_type": "chat",
                "resident_id": str(resident_2_id),
                "report_image": "true",
            },
            files={"files": ("meal.png", PNG_1X1, "image/png")},
            headers=ORIGIN,
        )
        assert sent.status_code == 201, sent.text
        message = sent.json()
        assert message["resident"]["id"] == str(resident_2_id)
        assert len(message["attachments"]) == 1
        assert message["attachments"][0]["text_extraction"]["status"] == "pending"

        processor_floor_rooms = processor.get("/api/rooms").json()
        processor_floor_room = next(
            room for room in processor_floor_rooms if room["name"] == "가상 2층 직원방"
        )
        messages = processor.get(
            f"/api/rooms/{processor_floor_room['id']}/messages"
        ).json()
        assert messages[-1]["attachments"][0]["original_name"] == "meal.png"
        assert (
            post(
                processor,
                f"/api/rooms/{room_id}/read",
                {"message_id": message["id"]},
            ).status_code
            == 204
        )
        comment = post(
            processor,
            f"/api/messages/{message['id']}/comments",
            {"body": "사회복지사가 식사기록을 함께 확인하겠습니다."},
        )
        assert comment.status_code == 201, comment.text
        writer_messages = writer.get(f"/api/rooms/{room_id}/messages").json()
        commented_message = next(item for item in writer_messages if item["id"] == message["id"])
        assert commented_message["comment_count"] == 1
        assert commented_message["unread_comment_count"] == 1
        assert commented_message["read_count"] == 2
        assert commented_message["reply_user_count"] == 1
        detail = writer.get(f"/api/messages/{message['id']}").json()
        assert {item["user_name"] for item in detail["read_receipts"]} == {
            "가상 작성자",
            "가상 처리담당자",
        }
        assert detail["comments"][0]["body"].startswith("사회복지사")
        assert (
            post(
                writer,
                f"/api/messages/{message['id']}/comments/read",
                {},
            ).status_code
            == 204
        )
        reread = writer.get(f"/api/rooms/{room_id}/messages").json()
        assert next(item for item in reread if item["id"] == message["id"])[
            "unread_comment_count"
        ] == 0
        photo = processor.get(message["attachments"][0]["download_url"])
        assert photo.status_code == 200
        assert photo.content == PNG_1X1
        outside_room_photo = outside_processor.get(
            message["attachments"][0]["download_url"]
        )
        assert outside_room_photo.status_code == 403
        workdesk_photo = outside_processor.get(
            f"/api/workdesk/attachments/{message['attachments'][0]['id']}"
        )
        assert workdesk_photo.status_code == 200
        assert workdesk_photo.content == PNG_1X1

        outside_room_action = post(
            writer,
            f"/api/rooms/{room_id}/messages",
            {
                "body": "방 밖 직원을 직접 지정하면 안 됩니다.",
                "message_type": "work_request",
                "resident_id": str(resident_2_id),
                "action": {
                    "action_type": "cooperation",
                    "assignee_user_id": outside_nursing_response.json()["id"],
                    "priority": "normal",
                },
            },
        )
        assert outside_room_action.status_code == 422
        assert "현재 채팅방에 참여한 직원" in outside_room_action.json()["detail"]

        action_message = post(
            writer,
            f"/api/rooms/{room_id}/messages",
            {
                "body": "다음 근무자가 식사량을 한 번 더 확인해 주세요.",
                "message_type": "handover",
                "resident_id": str(resident_2_id),
                "action": {
                    "action_type": "handover",
                    "assignee_user_id": processor_response.json()["id"],
                    "priority": "important",
                },
            },
        )
        assert action_message.status_code == 201, action_message.text
        assert action_message.json()["action_item"]["status"] == "assigned"
        assigned_actions = processor.get("/api/action-items")
        assert assigned_actions.status_code == 200, assigned_actions.text
        action_item = assigned_actions.json()[0]
        assert action_item["action_type"] == "handover"
        acknowledged = processor.patch(
            f"/api/action-items/{action_item['id']}",
            json={"status": "acknowledged"},
            headers=ORIGIN,
        )
        assert acknowledged.status_code == 200, acknowledged.text
        assert acknowledged.json()["status"] == "acknowledged"

        digests = processor.get("/api/workdesk/room-digests?period=day")
        assert digests.status_code == 200, digests.text
        floor_digest = next(item for item in digests.json() if item["room_id"] == room_id)
        assert floor_digest["message_count"] == 2
        assert floor_digest["comment_count"] == 2
        assert floor_digest["document_counts"]["care_service_record"] >= 1

        work_items = processor.get("/api/work-items").json()
        assert len(work_items) == 2
        work_item = next(
            item for item in work_items if item["source_snapshot"]["message_id"] == message["id"]
        )
        assert work_item["status"] == "in_review"
        assert work_item["source_snapshot"]["body"] == (
            "점심 식사량이 평소보다 적어 확인을 부탁드립니다."
        )
        assert work_item["source_snapshot"]["attachment_ids"] == [
            message["attachments"][0]["id"]
        ]
        extraction = work_item["message"]["attachments"][0]["text_extraction"]
        assert extraction["status"] == "completed"
        assert extraction["extracted_text"] == "시험용 손글씨 판독 결과"
        assert extraction["original_extracted_text"] == "시험용 손글씨 판독 결과"
        assert extraction["correction_event_count"] == 0
        assert work_item["ai_state"] == "prototype_suggested"
        assert work_item["ai_suggestion"]["classification"] == "nutrition"
        assert work_item["comments"][0]["body"].startswith("사회복지사")
        third_party_image_review = third_party.patch(
            f"/api/attachments/{message['attachments'][0]['id']}/text-extraction",
            json={
                "reviewed_text": "제3자가 바꾸면 안 되는 이미지 판독문",
                "decision": "direct_edit",
            },
            headers=ORIGIN,
        )
        assert third_party_image_review.status_code == 403
        assert (
            third_party_image_review.json()["detail"]
            == "작성자 본인 또는 업무 담당자만 판독 내용을 수정할 수 있습니다."
        )
        reviewed_extraction = processor.patch(
            f"/api/attachments/{message['attachments'][0]['id']}/text-extraction",
            json={
                "reviewed_text": (
                    "시험용 손글씨 확인 결과\n"
                    "점심 식사량 감소, 간호팀 확인 필요"
                ),
                "decision": "direct_edit",
            },
            headers=ORIGIN,
        )
        assert reviewed_extraction.status_code == 200, reviewed_extraction.text
        reviewed_payload = reviewed_extraction.json()["text_extraction"]
        assert reviewed_payload["status"] == "reviewed"
        assert reviewed_payload["review_decision"] == "direct_edit"
        assert reviewed_payload["correction_event_count"] == 1
        assert (
            reviewed_payload["original_extracted_text"]
            == "시험용 손글씨 판독 결과"
        )
        with SessionLocal() as db:
            event = db.scalar(select(OcrCorrectionEvent))
            assert event is not None
            assert event.raw_text == "시험용 손글씨 판독 결과"
            assert event.corrected_text.startswith("시험용 손글씨 확인 결과")
            assert event.confirmed is True
            assert any(
                pair["recognized_text"] == "판독"
                and pair["corrected_text"] == "확인"
                for pair in event.correction_pairs
            )
        empty_candidates = processor.get("/api/document-candidates")
        assert empty_candidates.status_code == 200
        assert empty_candidates.json()["total_count"] == 0
        premature_ready = processor.patch(
            f"/api/work-items/{work_item['id']}",
            json={"status": "ready"},
            headers=ORIGIN,
        )
        assert premature_ready.status_code == 422
        updated = processor.patch(
            f"/api/work-items/{work_item['id']}",
            json={
                "status": "in_review",
                "document_types": [
                    "care_service_record",
                    "nursing_log",
                ],
                "processing_notes": "가명 시험자료로 검토 중",
            },
            headers=ORIGIN,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["handled_by_name"] == "가상 처리담당자"
        assert updated.json()["document_types"] == [
            "care_service_record",
            "nursing_log",
        ]

        ai_reviewed = post(
            processor,
            f"/api/work-items/{work_item['id']}/ai-review",
            {},
        )
        assert ai_reviewed.status_code == 200, ai_reviewed.text
        proposed = ai_reviewed.json()
        assert proposed["status"] == "in_review"
        assert proposed["ai_state"] == "ai_reviewed"
        assert proposed["ai_generator"] == "stub:test-ai-review"
        assert proposed["ai_generated_at"] is not None
        assert proposed["ai_suggestion"]["classification"] == "nutrition"
        assert proposed["ai_suggestion"]["risk_level"] == "medium"
        assert "점심 식사량 감소, 간호팀 확인 필요" in proposed["ai_suggestion"][
            "corrected_text"
        ]
        assert proposed["source_snapshot"]["body"] == work_item["source_snapshot"]["body"]
        assert proposed["confirmed_at"] is None
        assert {
            document_draft["document_type"]
            for document_draft in proposed["document_drafts"]
        } == {"care_service_record", "nursing_log"}
        added_document = post(
            processor,
            (
                f"/api/work-items/{work_item['id']}/document-drafts/"
                "consultation_log"
            ),
            {},
        )
        assert added_document.status_code == 200, added_document.text
        proposed = added_document.json()
        assert "consultation_log" in proposed["ai_suggestion"]["document_types"]
        consultation_draft = next(
            document_draft
            for document_draft in proposed["document_drafts"]
            if document_draft["document_type"] == "consultation_log"
        )
        assert consultation_draft["status"] == "draft"
        assert consultation_draft["generator"] == "reviewer-added-rule-v1"
        assert "[상담·연락 내용]" in consultation_draft["content"]
        added_document_again = post(
            processor,
            (
                f"/api/work-items/{work_item['id']}/document-drafts/"
                "consultation_log"
            ),
            {},
        )
        assert added_document_again.status_code == 200
        assert (
            next(
                document_draft
                for document_draft in added_document_again.json()["document_drafts"]
                if document_draft["document_type"] == "consultation_log"
            )["version"]
            == consultation_draft["version"]
        )
        first_care_draft = next(
            document_draft
            for document_draft in proposed["document_drafts"]
            if document_draft["document_type"] == "care_service_record"
        )
        edited_document = processor.patch(
            (
                f"/api/work-items/{work_item['id']}/document-drafts/"
                "care_service_record"
            ),
            json={
                "action": "direct_edit",
                "content": first_care_draft["content"]
                + "\n담당자가 식사량 감소 내용을 확인함",
            },
            headers=ORIGIN,
        )
        assert edited_document.status_code == 200, edited_document.text
        edited_care_draft = next(
            document_draft
            for document_draft in edited_document.json()["document_drafts"]
            if document_draft["document_type"] == "care_service_record"
        )
        assert edited_care_draft["version"] == first_care_draft["version"] + 1
        assert "담당자가" in edited_care_draft["content"]

        confirmation_payload = {
            **proposed["ai_suggestion"],
            "summary": "가상 어르신의 점심 식사량 감소를 담당자가 확인함",
            "document_types": [
                "care_service_record",
                "nursing_log",
                "consultation_log",
            ],
            "reviewer_notes": "시험 제안을 검토했으며 실제 서류에는 아직 반영하지 않음",
            "verification_acknowledged": True,
        }
        confirmed = post(
            processor,
            f"/api/work-items/{work_item['id']}/confirm",
            confirmation_payload,
        )
        assert confirmed.status_code == 200, confirmed.text
        confirmed_item = confirmed.json()
        assert confirmed_item["status"] == "ready"
        assert confirmed_item["confirmed_by_name"] == "가상 처리담당자"
        assert confirmed_item["confirmed_at"] is not None
        assert confirmed_item["confirmed_record"]["summary"] == confirmation_payload["summary"]
        assert confirmed_item["processing_notes"] == confirmation_payload["reviewer_notes"]
        assert confirmed_item["source_snapshot"] == work_item["source_snapshot"]
        assert all(
            document_draft["status"] == "approved"
            for document_draft in confirmed_item["document_drafts"]
        )
        assert all(
            document_draft["approved_by_name"] == "가상 처리담당자"
            for document_draft in confirmed_item["document_drafts"]
        )
        approved_versions = {
            document_draft["document_type"]: document_draft["version"]
            for document_draft in confirmed_item["document_drafts"]
        }

        dashboard = processor.get("/api/document-candidates")
        assert dashboard.status_code == 200, dashboard.text
        dashboard_payload = dashboard.json()
        assert dashboard_payload["total_count"] == 1
        assert dashboard_payload["filtered_count"] == 1
        assert dashboard_payload["document_counts"] == {
            "care_service_record": 1,
            "nursing_log": 1,
            "consultation_log": 1,
        }
        assert dashboard_payload["risk_counts"] == {"medium": 1}
        assert dashboard_payload["classification_counts"] == {"nutrition": 1}
        assert dashboard_payload["items"][0]["id"] == work_item["id"]
        care_record_candidates = processor.get(
            "/api/document-candidates?document_type=care_service_record"
        )
        assert care_record_candidates.json()["filtered_count"] == 1
        nursing_candidates = processor.get(
            "/api/document-candidates?document_type=nursing_log"
        )
        assert nursing_candidates.json()["total_count"] == 1
        assert nursing_candidates.json()["filtered_count"] == 1
        medium_candidates = processor.get(
            "/api/document-candidates?risk_level=medium&classification=nutrition"
        )
        assert medium_candidates.json()["filtered_count"] == 1
        assert (
            processor.get("/api/document-candidates?risk_level=invalid").status_code
            == 422
        )

        regenerate = post(
            processor,
            f"/api/work-items/{work_item['id']}/prototype-suggestion",
            {},
        )
        assert regenerate.status_code == 409

        regular_staff_forbidden = writer.get("/api/work-items")
        assert regular_staff_forbidden.status_code == 403
        assert writer.get("/api/document-candidates").status_code == 403
        forbidden_prototype = post(
            writer,
            f"/api/work-items/{work_item['id']}/prototype-suggestion",
            {},
        )
        assert forbidden_prototype.status_code == 403
        writer_own_review = writer.patch(
            f"/api/attachments/{message['attachments'][0]['id']}/text-extraction",
            json={
                "reviewed_text": "작성자가 직접 확인한 손글씨 판독 결과",
                "decision": "direct_edit",
            },
            headers=ORIGIN,
        )
        assert writer_own_review.status_code == 200, writer_own_review.text
        assert (
            writer_own_review.json()["text_extraction"]["reviewed_text"]
            == "작성자가 직접 확인한 손글씨 판독 결과"
        )

        audio_sent = writer.post(
            f"/api/rooms/{room_id}/messages-with-files",
            data={"body": "", "message_type": "chat"},
            files={"files": ("voice.wav", WAV_SAMPLE, "audio/wav")},
            headers=ORIGIN,
        )
        assert audio_sent.status_code == 201, audio_sent.text
        assert audio_sent.json()["body"] == "파일을 첨부했습니다."
        assert audio_sent.json()["attachments"][0]["mime_type"] == "audio/wav"
        assert (
            audio_sent.json()["attachments"][0]["text_extraction"]["status"]
            == "pending"
        )
        audio_work_item = next(
            item
            for item in processor.get("/api/work-items").json()
            if item["source_snapshot"]["message_id"] == audio_sent.json()["id"]
        )
        audio_extraction = audio_work_item["message"]["attachments"][0][
            "text_extraction"
        ]
        assert audio_extraction["status"] == "completed"
        assert audio_extraction["model_name"] == "test-whisper"
        assert audio_extraction["extracted_text"] == (
            "가상 어르신 2-01 점심 식사량 확인 부탁드립니다."
        )
        third_party_audio_review = third_party.patch(
            f"/api/attachments/{audio_sent.json()['attachments'][0]['id']}/text-extraction",
            json={
                "reviewed_text": "제3자가 바꾸면 안 되는 음성 받아쓰기",
                "decision": "direct_edit",
            },
            headers=ORIGIN,
        )
        assert third_party_audio_review.status_code == 403
        assert (
            third_party_audio_review.json()["detail"]
            == "작성자 본인 또는 업무 담당자만 판독 내용을 수정할 수 있습니다."
        )
        writer_audio_review = writer.patch(
            f"/api/attachments/{audio_sent.json()['attachments'][0]['id']}/text-extraction",
            json={
                "reviewed_text": "가상 어르신 2-01 점심 식사량을 확인했습니다.",
                "decision": "direct_edit",
            },
            headers=ORIGIN,
        )
        assert writer_audio_review.status_code == 200, writer_audio_review.text
        assert writer_audio_review.json()["text_extraction"]["status"] == "reviewed"

        multi_resident_message = post(
            writer,
            f"/api/rooms/{room_id}/messages",
            {
                "body": (
                    "가상 어르신 2-01 어르신은 식사량을 확인하고, "
                    "가상 어르신 3-01 어르신은 귀가 시간을 확인해 주세요."
                ),
                "message_type": "chat",
            },
        )
        assert multi_resident_message.status_code == 201, multi_resident_message.text
        multi_payload = multi_resident_message.json()
        assert multi_payload["resident"] is None
        assert {
            link["resident"]["id"] for link in multi_payload["resident_links"]
        } == {str(resident_2_id), str(resident_3_id)}
        assert {
            link["status"] for link in multi_payload["resident_links"]
        } == {"candidate"}

        candidate_items = processor.get("/api/work-items").json()
        multi_item = next(
            item
            for item in candidate_items
            if item["source_snapshot"]["message_id"] == multi_payload["id"]
        )
        assert multi_item["resident"] is None
        assert multi_item["ai_suggestion"] is None
        blocked_prototype = post(
            processor,
            f"/api/work-items/{multi_item['id']}/prototype-suggestion",
            {},
        )
        assert blocked_prototype.status_code == 422

        first_reviewed_link = processor.patch(
            (
                f"/api/messages/{multi_payload['id']}/resident-links/"
                f"{resident_2_id}"
            ),
            json={"status": "confirmed"},
            headers=ORIGIN,
        )
        assert first_reviewed_link.status_code == 200, first_reviewed_link.text
        partially_confirmed_item = next(
            item
            for item in processor.get("/api/work-items").json()
            if item["id"] == multi_item["id"]
        )
        assert partially_confirmed_item["resident"]["id"] == str(resident_2_id)
        assert partially_confirmed_item["ai_suggestion"] is None
        pending_candidate_block = post(
            processor,
            f"/api/work-items/{multi_item['id']}/prototype-suggestion",
            {},
        )
        assert pending_candidate_block.status_code == 422
        assert "남아 있는 어르신 후보" in pending_candidate_block.json()["detail"]

        second_reviewed_link = processor.patch(
            (
                f"/api/messages/{multi_payload['id']}/resident-links/"
                f"{resident_3_id}"
            ),
            json={"status": "confirmed"},
            headers=ORIGIN,
        )
        assert second_reviewed_link.status_code == 200, second_reviewed_link.text
        confirmed_multi_item = next(
            item
            for item in processor.get("/api/work-items").json()
            if item["id"] == multi_item["id"]
        )
        assert confirmed_multi_item["resident"]["id"] == str(resident_2_id)
        assert set(confirmed_multi_item["source_snapshot"]["resident_names"]) == {
            "가상 어르신 2-01",
            "가상 어르신 3-01",
        }
        assert confirmed_multi_item["ai_suggestion"] is not None
        assert "가상 어르신 2-01" in confirmed_multi_item["ai_suggestion"]["summary"]
        assert "가상 어르신 3-01" in confirmed_multi_item["ai_suggestion"]["summary"]

        multi_resident_digests = processor.get(
            "/api/workdesk/room-digests?period=day"
        )
        assert multi_resident_digests.status_code == 200
        updated_floor_digest = next(
            item
            for item in multi_resident_digests.json()
            if item["room_id"] == room_id
        )
        assert updated_floor_digest["resident_count"] == 2
        multi_point = next(
            point
            for point in updated_floor_digest["major_points"]
            if point["message_id"] == multi_payload["id"]
        )
        assert "가상 어르신 2-01" in multi_point["resident_name"]
        assert "가상 어르신 3-01" in multi_point["resident_name"]

        unselected_report = writer.post(
            f"/api/rooms/{room_id}/messages-with-files",
            data={
                "body": "",
                "message_type": "chat",
                "report_image": "true",
            },
            files={"files": ("multi-report.png", PNG_1X1, "image/png")},
            headers=ORIGIN,
        )
        assert unselected_report.status_code == 201, unselected_report.text
        unselected_payload = unselected_report.json()
        assert unselected_payload["resident"] is None
        assert (
            unselected_payload["attachments"][0]["text_extraction"]["status"]
            == "pending"
        )
        unselected_item = next(
            item
            for item in processor.get("/api/work-items").json()
            if item["source_snapshot"]["message_id"] == unselected_payload["id"]
        )
        assert unselected_item["resident"] is None
        second_extraction = unselected_item["message"]["attachments"][0][
            "text_extraction"
        ]
        learned_candidate = next(
            candidate
            for candidate in second_extraction["spelling_candidates"]
            if candidate["recognized"] == "판독"
            and candidate["candidate"] == "확인"
        )
        assert learned_candidate["source"] == "confirmed_history"
        assert learned_candidate["auto_applicable"] is False
        needs_review = processor.patch(
            (
                f"/api/attachments/"
                f"{unselected_payload['attachments'][0]['id']}/text-extraction"
            ),
            json={"decision": "needs_review"},
            headers=ORIGIN,
        )
        assert needs_review.status_code == 200, needs_review.text
        needs_payload = needs_review.json()["text_extraction"]
        assert needs_payload["review_decision"] == "needs_review"
        assert needs_payload["correction_event_count"] == 1
        assert needs_payload["status"] == "completed"
        assert needs_payload["reviewed_text"] is None
        applied_candidate = processor.patch(
            (
                f"/api/attachments/"
                f"{unselected_payload['attachments'][0]['id']}/text-extraction"
            ),
            json={
                "reviewed_text": "시험용 손글씨 확인 결과",
                "decision": "apply_candidate",
                "selected_candidate_id": learned_candidate["id"],
            },
            headers=ORIGIN,
        )
        assert applied_candidate.status_code == 200, applied_candidate.text
        applied_payload = applied_candidate.json()["text_extraction"]
        assert applied_payload["review_decision"] == "apply_candidate"
        assert applied_payload["correction_event_count"] == 2
        assert applied_payload["reviewed_text"] == "시험용 손글씨 확인 결과"
        assert applied_payload["original_extracted_text"] == "시험용 손글씨 판독 결과"

        correction_message = post(
            writer,
            f"/api/rooms/{room_id}/messages",
            {
                "body": "음성을 다시 확인하니 다른 어르신에 대한 보고였습니다.",
                "message_type": "chat",
                "resident_id": str(resident_2_id),
            },
        )
        assert correction_message.status_code == 201, correction_message.text
        correction_item = next(
            item
            for item in processor.get("/api/work-items").json()
            if item["source_snapshot"]["message_id"]
            == correction_message.json()["id"]
        )
        assert correction_item["resident"]["id"] == str(resident_2_id)
        assert correction_item["ai_suggestion"] is not None

        corrected_resident = processor.patch(
            f"/api/work-items/{correction_item['id']}/resident",
            json={"resident_id": str(resident_3_id)},
            headers=ORIGIN,
        )
        assert corrected_resident.status_code == 200, corrected_resident.text
        corrected_item = corrected_resident.json()
        assert corrected_item["resident"]["id"] == str(resident_3_id)
        assert corrected_item["source_snapshot"]["resident_name"] == "가상 어르신 3-01"
        assert corrected_item["source_snapshot"]["resident_names"] == [
            "가상 어르신 3-01"
        ]
        assert corrected_item["ai_suggestion"] is None
        links_by_resident = {
            link["resident"]["id"]: link
            for link in corrected_item["message"]["resident_links"]
        }
        assert links_by_resident[str(resident_3_id)]["status"] == "confirmed"
        with SessionLocal() as db:
            rejected_previous_link = db.scalar(
                select(MessageResidentLink).where(
                    MessageResidentLink.message_id
                    == UUID(correction_message.json()["id"]),
                    MessageResidentLink.resident_id == resident_2_id,
                )
            )
            assert rejected_previous_link is not None
            assert rejected_previous_link.status == "rejected"

        confirmed_resident_change = processor.patch(
            f"/api/work-items/{work_item['id']}/resident",
            json={"resident_id": str(resident_3_id)},
            headers=ORIGIN,
        )
        assert confirmed_resident_change.status_code == 409

        reopened = post(
            processor,
            f"/api/work-items/{work_item['id']}/reopen",
            {"reason": "승인 후 급여제공기록지 문구 수정 필요"},
        )
        assert reopened.status_code == 200, reopened.text
        reopened_item = reopened.json()
        assert reopened_item["status"] == "in_review"
        assert reopened_item["confirmed_at"] is None
        assert reopened_item["confirmed_record"] is None
        assert all(
            document_draft["status"] == "draft"
            for document_draft in reopened_item["document_drafts"]
        )
        assert {
            document_draft["document_type"]: document_draft["version"]
            for document_draft in reopened_item["document_drafts"]
        } == {
            document_type: version + 1
            for document_type, version in approved_versions.items()
        }
        with SessionLocal() as db:
            stored_drafts = db.scalars(
                select(WorkItemDocumentDraft)
                .where(
                    WorkItemDocumentDraft.work_item_id
                    == UUID(work_item["id"])
                )
                .order_by(
                    WorkItemDocumentDraft.document_type,
                    WorkItemDocumentDraft.version,
                )
            ).all()
            assert any(
                draft.status == "approved" and not draft.is_current
                for draft in stored_drafts
            )
            assert any(
                draft.status == "draft" and draft.is_current
                for draft in stored_drafts
            )
        assert (
            post(
                processor,
                f"/api/work-items/{work_item['id']}/reopen",
                {"reason": "중복 승인 취소"},
            ).status_code
            == 409
        )

        action_from_detail = post(
            writer,
            f"/api/messages/{multi_payload['id']}/action-item",
            {
                "action_type": "handover",
                "assignee_user_id": processor_response.json()["id"],
                "priority": "important",
            },
        )
        assert action_from_detail.status_code == 201, action_from_detail.text
        assert action_from_detail.json()["created_by_id"] == staff_response.json()["id"]
        detail_after_action = writer.get(
            f"/api/messages/{multi_payload['id']}"
        )
        assert detail_after_action.status_code == 200
        assert detail_after_action.json()["message"]["action_item"]["status"] == "assigned"
        assert any(
            comment["body"].startswith("업무로 전달했습니다.")
            for comment in detail_after_action.json()["comments"]
        )
        action_completed = processor.patch(
            f"/api/action-items/{action_from_detail.json()['id']}",
            json={"status": "completed"},
            headers=ORIGIN,
        )
        assert action_completed.status_code == 200, action_completed.text
        assert action_completed.json()["status"] == "completed"
        detail_after_complete = writer.get(
            f"/api/messages/{multi_payload['id']}"
        ).json()
        assert any(
            comment["body"] == "업무 처리를 완료했습니다."
            for comment in detail_after_complete["comments"]
        )

        completed_search = writer.get(
            f"/api/rooms/{room_id}/message-search",
            params={
                "q": "업무 처리를 완료했습니다",
                "action_status": "completed",
            },
        )
        assert completed_search.status_code == 200, completed_search.text
        assert completed_search.json()["matched_count"] == 1
        assert completed_search.json()["messages"][0]["id"] == multi_payload["id"]
        ocr_search = writer.get(
            f"/api/rooms/{room_id}/message-search",
            params={"q": "작성자가 직접 확인한 손글씨 판독 결과"},
        )
        assert ocr_search.status_code == 200, ocr_search.text
        assert message["id"] in {
            searched["id"] for searched in ocr_search.json()["messages"]
        }
        summarized = post(
            writer,
            f"/api/rooms/{room_id}/message-search/summary",
            {"message_ids": [multi_payload["id"]]},
        )
        assert summarized.status_code == 200, summarized.text
        assert summarized.json()["generator"] == "stub:test-ai-review"
        assert "[1]" in summarized.json()["summary"]

        optional_report = post(
            writer,
            f"/api/rooms/{room_id}/messages",
            {
                "body": "시설장에게 전달할 간단한 보고입니다.",
                "message_type": "report",
            },
        )
        assert optional_report.status_code == 201, optional_report.text
        assert optional_report.json()["message_type"] == "report"
        assert optional_report.json()["action_item"] is None

        video_sent = writer.post(
            f"/api/rooms/{room_id}/messages-with-files",
            data={
                "body": "보행 모습을 촬영한 시험 동영상입니다.",
                "message_type": "chat",
            },
            files={"files": ("walking.mp4", MP4_SAMPLE, "video/mp4")},
            headers=ORIGIN,
        )
        assert video_sent.status_code == 201, video_sent.text
        video_payload = video_sent.json()
        assert video_payload["attachments"][0]["mime_type"] == "video/mp4"
        assert video_payload["attachments"][0]["text_extraction"] is None

        today = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
        period_review = post(
            processor,
            "/api/workdesk/period-review",
            {
                "start_date": today,
                "end_date": today,
                "room_id": room_id,
            },
        )
        assert period_review.status_code == 200, period_review.text
        period_payload = period_review.json()
        assert period_payload["message_count"] >= 2
        assert period_payload["generator"] == "quick-period-summary-v1"
        assert optional_report.json()["id"] in {
            source["message"]["id"] for source in period_payload["sources"]
        }
        assert message["id"] in {
            source["message"]["id"] for source in period_payload["sources"]
        }
        original_source = next(
            source
            for source in period_payload["sources"]
            if source["message"]["id"] == message["id"]
        )
        assert original_source["read_count"] >= 2
        assert original_source["reply_count"] >= 1
        assert original_source["reply_user_count"] >= 1
        assert period_payload["record_events"]
        assert set(period_payload["record_group_counts"]) == {
            "nursing",
            "care_service",
            "consultation",
            "program",
            "general",
            "needs_review",
        }
        first_record_event = period_payload["record_events"][0]
        assert first_record_event["event_group_id"]
        assert first_record_event["evidence_ids"]
        assert first_record_event["record_usage_tags"]
        record_summary = post(
            processor,
            "/api/workdesk/record-summary",
            {
                "record_usage_tag": first_record_event["record_usage_tags"][0],
                "record_usage_tags": first_record_event["record_usage_tags"],
                "selections": [
                    {
                        "resident_id": first_record_event["resident_id"],
                        "evidence_ids": first_record_event["evidence_ids"],
                    }
                ],
            },
        )
        assert record_summary.status_code == 200, record_summary.text
        assert record_summary.json()["evidence_ids"] == first_record_event["evidence_ids"]
        assert record_summary.json()["record_usage_tags"] == first_record_event[
            "record_usage_tags"
        ]
        assert record_summary.json()["generator"] == "stub:test-ai-review"
        assert record_summary.json()["summary"]

        legacy_general_summary = post(
            processor,
            "/api/workdesk/record-summary",
            {
                "record_usage_tag": "general",
                "record_usage_tags": ["general"],
                "evidence_ids": [optional_report.json()["id"]],
            },
        )
        assert legacy_general_summary.status_code == 200, legacy_general_summary.text
        assert legacy_general_summary.json()["evidence_ids"] == [
            optional_report.json()["id"]
        ]

        legacy_single_resident_summary = post(
            processor,
            "/api/workdesk/record-summary",
            {
                "record_usage_tag": "care_service",
                "record_usage_tags": ["care_service"],
                "evidence_ids": [message["id"]],
            },
        )
        assert (
            legacy_single_resident_summary.status_code == 200
        ), legacy_single_resident_summary.text
        assert legacy_single_resident_summary.json()["evidence_ids"] == [message["id"]]

        mixed_contract_summary = post(
            processor,
            "/api/workdesk/record-summary",
            {
                "record_usage_tag": first_record_event["record_usage_tags"][0],
                "record_usage_tags": first_record_event["record_usage_tags"],
                "selections": [
                    {
                        "resident_id": first_record_event["resident_id"],
                        "evidence_ids": first_record_event["evidence_ids"],
                    }
                ],
                "evidence_ids": first_record_event["evidence_ids"],
            },
        )
        assert mixed_contract_summary.status_code == 422
        assert "동시에 사용할 수 없습니다" in mixed_contract_summary.text

        legacy_multi_resident_summary = post(
            processor,
            "/api/workdesk/record-summary",
            {
                "record_usage_tag": "care_service",
                "record_usage_tags": ["care_service"],
                "evidence_ids": [multi_payload["id"]],
            },
        )
        assert legacy_multi_resident_summary.status_code == 422
        assert "여러 어르신이 연결된 근거" in legacy_multi_resident_summary.json()[
            "detail"
        ]

        assert any(
            draft["resident_id"] == str(resident_2_id)
            for draft in period_payload["document_drafts"]
        )
        assert period_payload["briefing"]["comparison_days"] == 3
        assert period_payload["briefing"]["document_candidate_count"] >= 1
        resident_briefing = next(
            card
            for card in period_payload["briefing"]["cards"]
            if card["resident_id"] == str(resident_2_id)
        )
        assert resident_briefing["source_message_ids"]
        assert resident_briefing["change_summary"]
        assert "식사량" in resident_briefing["change_summary"]
        assert resident_briefing["check_reasons"]
        assert resident_briefing["priority"] in {"first", "check", "observe"}
        assert set(resident_briefing["document_types"]).issubset(
            {
                "care_service_record",
                "nursing_log",
                "consultation_log",
                "physical_restraint_log",
                "program_log",
            }
        )
        enhanced_period_review = post(
            processor,
            "/api/workdesk/period-review",
            {
                "start_date": today,
                "end_date": today,
                "room_id": room_id,
                "enhance_summary": True,
            },
        )
        assert enhanced_period_review.status_code == 200, enhanced_period_review.text
        assert enhanced_period_review.json()["generator"] == "stub:test-ai-review"
        period_video = processor.get(
            f"/api/workdesk/attachments/"
            f"{video_payload['attachments'][0]['id']}"
        )
        assert period_video.status_code == 200, period_video.text
        assert period_video.content == MP4_SAMPLE

        with SessionLocal() as db:
            inactive_linked_resident = db.get(Resident, resident_3_id)
            inactive_linked_resident.is_active = False
            db.commit()
        inactive_link_summary = post(
            processor,
            "/api/workdesk/record-summary",
            {
                "record_usage_tag": "care_service",
                "record_usage_tags": ["care_service"],
                "selections": [
                    {
                        "resident_id": str(resident_2_id),
                        "evidence_ids": [multi_payload["id"]],
                    }
                ],
            },
        )
        legacy_inactive_link_summary = post(
            processor,
            "/api/workdesk/record-summary",
            {
                "record_usage_tag": "care_service",
                "record_usage_tags": ["care_service"],
                "evidence_ids": [correction_message.json()["id"]],
            },
        )
        with SessionLocal() as db:
            inactive_linked_resident = db.get(Resident, resident_3_id)
            inactive_linked_resident.is_active = True
            db.commit()
        assert inactive_link_summary.status_code == 422
        assert "현재 확인할 수 없는 명단" in inactive_link_summary.json()["detail"]
        assert legacy_inactive_link_summary.status_code == 422
        assert "현재 확인할 수 없는 명단" in legacy_inactive_link_summary.json()[
            "detail"
        ]

        assert (
            outside_processor.get(
                f"/api/rooms/{room_id}/message-search",
                params={"q": "식사량"},
            ).status_code
            == 403
        )
