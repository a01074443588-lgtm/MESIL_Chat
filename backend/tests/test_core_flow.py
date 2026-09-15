from fastapi.testclient import TestClient
from sqlalchemy import select

from app import main as main_module
from app.database import SessionLocal
from app.main import app
from app.models import OrgUnit, Organization, RecipientRoom, Resident, Room
from app.services import _attachment_preview_text


PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDAT\x08\xd7c\xf8\xcf\xc0\x00\x00"
    b"\x03\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
WAV_SILENCE = (
    b"RIFF"
    + (36).to_bytes(4, "little")
    + b"WAVEfmt "
    + (16).to_bytes(4, "little")
    + (1).to_bytes(2, "little")
    + (1).to_bytes(2, "little")
    + (16000).to_bytes(4, "little")
    + (32000).to_bytes(4, "little")
    + (2).to_bytes(2, "little")
    + (16).to_bytes(2, "little")
    + b"data"
    + (0).to_bytes(4, "little")
)


def login(client: TestClient, username: str, password: str):
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
        headers={"origin": "http://testserver"},
    )
    assert response.status_code == 200, response.text
    return response.json()["user"]


def post(client: TestClient, path: str, payload: dict):
    return client.post(path, json=payload, headers={"origin": "http://testserver"})


def test_attachment_preview_text_groups_file_types_in_a_fixed_order():
    assert _attachment_preview_text(
        [
            "video/mp4",
            "image/png",
            "application/pdf",
            "audio/wav",
            "image/jpeg",
            "application/octet-stream",
            "video/webm",
        ]
    ) == "사진 2장 · 음성 1개 · 동영상 2개 · PDF 1개 · 파일 1개"
    assert _attachment_preview_text([]) is None


def test_room_list_summarizes_file_only_message_and_keeps_written_body():
    with TestClient(app) as client:
        login(client, "admin", "AdminPass!234")
        room_id = client.get("/api/rooms").json()[0]["id"]

        file_only = client.post(
            f"/api/rooms/{room_id}/messages-with-files",
            files={"files": ("preview.png", PNG_1X1, "image/png")},
            headers={"origin": "http://testserver"},
        )
        assert file_only.status_code == 201, file_only.text
        assert file_only.json()["body"] == "파일을 첨부했습니다."
        room = next(
            item for item in client.get("/api/rooms").json() if item["id"] == room_id
        )
        assert room["last_message"] == "사진 1장"

        written = client.post(
            f"/api/rooms/{room_id}/messages-with-files",
            data={"body": "직접 적은 첨부 설명입니다."},
            files={"files": ("described.png", PNG_1X1, "image/png")},
            headers={"origin": "http://testserver"},
        )
        assert written.status_code == 201, written.text
        room = next(
            item for item in client.get("/api/rooms").json() if item["id"] == room_id
        )
        assert room["last_message"] == "직접 적은 첨부 설명입니다."


def test_chat_first_messages_without_resident_are_saved_for_later_admin_review():
    with TestClient(app) as client:
        login(client, "admin", "AdminPass!234")
        room_id = client.get("/api/rooms").json()[0]["id"]

        text_response = post(
            client,
            f"/api/rooms/{room_id}/messages",
            {"body": "합성 관찰 내용을 먼저 보냅니다."},
        )
        assert text_response.status_code == 201, text_response.text
        assert text_response.json()["resident"] is None
        assert text_response.json()["resident_links"] == []

        file_response = client.post(
            f"/api/rooms/{room_id}/messages-with-files",
            data={"body": "대상 연결 전 합성 문서를 먼저 보냅니다."},
            files={"files": ("later-review.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")},
            headers={"origin": "http://testserver"},
        )
        assert file_response.status_code == 201, file_response.text
        assert file_response.json()["resident"] is None
        assert file_response.json()["resident_links"] == []

        work_items_response = client.get("/api/work-items")
        assert work_items_response.status_code == 200, work_items_response.text
        by_message_id = {
            item["source_snapshot"]["message_id"]: item
            for item in work_items_response.json()
        }
        for message_id in (text_response.json()["id"], file_response.json()["id"]):
            item = by_message_id[message_id]
            assert item["resident"] is None
            assert item["status"] == "pending"
            assert item["confirmed_at"] is None
            assert item["confirmed_record"] is None
            assert item["document_drafts"] == []

        reopened = client.get(f"/api/rooms/{room_id}/messages")
        assert reopened.status_code == 200, reopened.text
        reopened_ids = {message["id"] for message in reopened.json()}
        assert text_response.json()["id"] in reopened_ids
        assert file_response.json()["id"] in reopened_ids

        review_queue = client.get(
            "/api/work-items", params={"review_queue_only": True}
        )
        assert review_queue.status_code == 200, review_queue.text
        review_message_ids = {
            item["source_snapshot"]["message_id"] for item in review_queue.json()
        }
        assert text_response.json()["id"] in review_message_ids
        assert file_response.json()["id"] in review_message_ids


def test_room_with_one_confirmed_resident_reuses_that_resident_without_a_picker():
    with TestClient(app) as client:
        login(client, "admin", "AdminPass!234")
        room_id = client.get("/api/rooms").json()[0]["id"]
        with SessionLocal() as db:
            room = db.get(Room, room_id)
            assert room is not None
            floor = OrgUnit(
                organization_id=room.organization_id,
                unit_type="floor",
                internal_code="TEST-SINGLE-RESIDENT-FLOOR",
                name="단일 어르신 연결 시험 구역",
                is_test_data=True,
            )
            db.add(floor)
            db.flush()
            recipient_room = RecipientRoom(
                organization_id=room.organization_id,
                internal_code="TEST-SINGLE-RESIDENT-ROOM",
                name="단일 어르신 연결 시험 생활실",
                floor_unit_id=floor.id,
            )
            db.add(recipient_room)
            db.flush()
            resident = Resident(
                organization_id=room.organization_id,
                internal_code="TEST-SINGLE-RESIDENT",
                display_name="단일연결시험어르신",
                room_id=recipient_room.id,
                service_type="facility",
                is_test_data=True,
            )
            db.add(resident)
            room.resident_scope = "floor"
            room.resident_scope_unit_id = floor.id
            db.commit()
            resident_id = str(resident.id)

        sent = post(
            client,
            f"/api/rooms/{room_id}/messages",
            {"body": "선택창 없이 합성 관찰 내용을 남깁니다."},
        )
        assert sent.status_code == 201, sent.text
        assert sent.json()["resident"]["id"] == resident_id
        assert len(sent.json()["resident_links"]) == 1
        resident_link = sent.json()["resident_links"][0]
        assert resident_link["resident"] == sent.json()["resident"]
        assert resident_link["source"] == "manual"
        assert resident_link["status"] == "confirmed"
        assert resident_link["reviewed_at"] is not None


def test_room_with_multiple_possible_residents_keeps_message_unassigned():
    with TestClient(app) as client:
        login(client, "admin", "AdminPass!234")
        room_id = client.get("/api/rooms").json()[0]["id"]
        with SessionLocal() as db:
            room = db.get(Room, room_id)
            assert room is not None
            floor = OrgUnit(
                organization_id=room.organization_id,
                unit_type="floor",
                internal_code="TEST-AMBIGUOUS-RESIDENT-FLOOR",
                name="복수 어르신 연결 시험 구역",
                is_test_data=True,
            )
            db.add(floor)
            db.flush()
            recipient_room = RecipientRoom(
                organization_id=room.organization_id,
                internal_code="TEST-AMBIGUOUS-RESIDENT-ROOM",
                name="복수 어르신 연결 시험 생활실",
                floor_unit_id=floor.id,
            )
            db.add(recipient_room)
            db.flush()
            for index in range(2):
                db.add(
                    Resident(
                        organization_id=room.organization_id,
                        internal_code=f"TEST-AMBIGUOUS-RESIDENT-{index}",
                        display_name=f"복수연결시험어르신{index}",
                        room_id=recipient_room.id,
                        service_type="facility",
                        is_test_data=True,
                    )
                )
            room.resident_scope = "floor"
            room.resident_scope_unit_id = floor.id
            db.commit()

        sent = post(
            client,
            f"/api/rooms/{room_id}/messages",
            {"body": "누구인지 확정할 수 없는 합성 관찰입니다."},
        )
        assert sent.status_code == 201, sent.text
        assert sent.json()["resident"] is None
        assert sent.json()["resident_links"] == []


def test_inline_reply_is_saved_separately_from_message_comments():
    with TestClient(app) as client:
        login(client, "admin", "AdminPass!234")
        room_id = client.get("/api/rooms").json()[0]["id"]
        source = post(
            client,
            f"/api/rooms/{room_id}/messages",
            {"body": "인라인 답장의 원문입니다."},
        )
        assert source.status_code == 201, source.text

        reply = post(
            client,
            f"/api/rooms/{room_id}/messages",
            {
                "body": "원문을 인용한 새 메시지입니다.",
                "reply_to_message_id": source.json()["id"],
            },
        )
        assert reply.status_code == 201, reply.text
        assert reply.json()["reply_to"] == {
            "message_id": source.json()["id"],
            "sender_name": source.json()["sender_name"],
            "body": "인라인 답장의 원문입니다.",
            "created_at": source.json()["created_at"],
        }
        assert reply.json()["comment_count"] == 0

        reopened = client.get(f"/api/rooms/{room_id}/messages")
        assert reopened.status_code == 200, reopened.text
        reopened_reply = next(
            item for item in reopened.json() if item["id"] == reply.json()["id"]
        )
        assert reopened_reply["reply_to"] == reply.json()["reply_to"]


def test_processor_review_queue_excludes_ordinary_resident_linked_chat():
    with TestClient(app) as client:
        login(client, "admin", "AdminPass!234")
        room_id = client.get("/api/rooms").json()[0]["id"]
        with SessionLocal() as db:
            organization = db.scalar(select(Organization).limit(1))
            assert organization is not None
            resident = Resident(
                organization_id=organization.id,
                internal_code="TEST-QUEUE-RESIDENT",
                display_name="검토목록시험어르신",
                service_type="facility",
                is_test_data=True,
            )
            db.add(resident)
            db.commit()
            resident_id = str(resident.id)

        sent = post(
            client,
            f"/api/rooms/{room_id}/messages",
            {
                "body": "합성 일상 공유를 남깁니다.",
                "resident_ids": [resident_id],
            },
        )
        assert sent.status_code == 201, sent.text

        all_items = client.get("/api/work-items")
        assert all_items.status_code == 200, all_items.text
        assert any(
            item["source_snapshot"]["message_id"] == sent.json()["id"]
            for item in all_items.json()
        )

        review_queue = client.get(
            "/api/work-items", params={"review_queue_only": True}
        )
        assert review_queue.status_code == 200, review_queue.text
        assert all(
            item["source_snapshot"]["message_id"] != sent.json()["id"]
            for item in review_queue.json()
        )


def test_failed_recognition_keeps_unassigned_attachment_in_admin_review(monkeypatch):
    def fail_recognition(_path):
        raise main_module.OcrError("합성 자동 판독 실패")

    monkeypatch.setattr(main_module, "image_likely_contains_text", fail_recognition)

    with TestClient(app) as client:
        login(client, "admin", "AdminPass!234")
        room_id = client.get("/api/rooms").json()[0]["id"]
        response = client.post(
            f"/api/rooms/{room_id}/messages-with-files",
            data={"body": "판독 실패 뒤 관리자 검토 보존 시험", "report_image": "true"},
            files={"files": ("failed-review.png", PNG_1X1, "image/png")},
            headers={"origin": "http://testserver"},
        )
        assert response.status_code == 201, response.text
        message_id = response.json()["id"]
        assert response.json()["resident"] is None

        detail = client.get(f"/api/messages/{message_id}")
        assert detail.status_code == 200, detail.text
        extraction = detail.json()["message"]["attachments"][0]["text_extraction"]
        assert extraction["status"] == "failed"

        work_items_response = client.get("/api/work-items")
        assert work_items_response.status_code == 200, work_items_response.text
        item = next(
            item
            for item in work_items_response.json()
            if item["source_snapshot"]["message_id"] == message_id
        )
        assert item["resident"] is None
        assert item["status"] == "pending"
        assert item["confirmed_record"] is None
        assert item["document_drafts"] == []


def test_room_messages_support_older_cursor_without_duplicates():
    with TestClient(app) as client:
        login(client, "admin", "AdminPass!234")
        room_id = client.get("/api/rooms").json()[0]["id"]
        created_ids = []
        for index in range(35):
            response = post(
                client,
                f"/api/rooms/{room_id}/messages",
                {"body": f"이전 대화 나눠 보기 {index:02d}"},
            )
            assert response.status_code == 201, response.text
            created_ids.append(response.json()["id"])

        latest = client.get(f"/api/rooms/{room_id}/messages?limit=31")
        assert latest.status_code == 200, latest.text
        latest_messages = latest.json()
        assert len(latest_messages) == 31
        assert latest_messages[-1]["id"] == created_ids[-1]

        older = client.get(
            f"/api/rooms/{room_id}/messages"
            f"?before_id={latest_messages[0]['id']}&limit=31"
        )
        assert older.status_code == 200, older.text
        older_messages = older.json()
        assert older_messages
        assert not (
            {item["id"] for item in latest_messages}
            & {item["id"] for item in older_messages}
        )
        assert older_messages[-1]["id"] == created_ids[3]

        invalid = client.get(
            f"/api/rooms/{room_id}/messages"
            f"?before_id={latest_messages[0]['id']}&after_id={latest_messages[-1]['id']}"
        )
        assert invalid.status_code == 422


def patch(client: TestClient, path: str, payload: dict):
    return client.patch(path, json=payload, headers={"origin": "http://testserver"})


def delete(client: TestClient, path: str):
    return client.delete(path, headers={"origin": "http://testserver"})


def test_one_message_accepts_ten_files_and_rejects_eleven(monkeypatch):
    monkeypatch.setattr(main_module.settings, "max_attachments_per_message", 10)
    with TestClient(app) as client:
        login(client, "admin", "AdminPass!234")
        rooms = client.get("/api/rooms").json()
        assert rooms
        room_id = rooms[0]["id"]

        ten_files = [
            ("files", (f"file-{index}.png", PNG_1X1, "image/png"))
            for index in range(1, 11)
        ]
        accepted = client.post(
            f"/api/rooms/{room_id}/messages-with-files",
            data={"body": "첨부 열 개 시험"},
            files=ten_files,
            headers={"origin": "http://testserver"},
        )
        assert accepted.status_code == 201, accepted.text
        assert len(accepted.json()["attachments"]) == 10

        eleven_files = [
            ("files", (f"overflow-{index}.png", PNG_1X1, "image/png"))
            for index in range(1, 12)
        ]
        rejected = client.post(
            f"/api/rooms/{room_id}/messages-with-files",
            data={"body": "첨부 열한 개 시험"},
            files=eleven_files,
            headers={"origin": "http://testserver"},
        )
        assert rejected.status_code == 422
        assert "최대 10개" in rejected.json()["detail"]


def test_one_message_rejects_files_over_the_total_size_limit(monkeypatch):
    monkeypatch.setattr(main_module.settings, "max_attachments_per_message", 10)
    monkeypatch.setattr(
        main_module.settings,
        "max_attachments_total_bytes",
        len(PNG_1X1) + 1,
    )
    with TestClient(app) as client:
        login(client, "admin", "AdminPass!234")
        room_id = client.get("/api/rooms").json()[0]["id"]
        rejected = client.post(
            f"/api/rooms/{room_id}/messages-with-files",
            data={"body": "전체 용량 제한 시험"},
            files=[
                ("files", ("first.png", PNG_1X1, "image/png")),
                ("files", ("second.png", PNG_1X1, "image/png")),
            ],
            headers={"origin": "http://testserver"},
        )
        assert rejected.status_code == 413
        assert "전체 용량" in rejected.json()["detail"]


def test_end_to_end_staff_chat_and_termination(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "transcribe_audio",
        lambda path, mime_type, **kwargs: "가상 직원 음성보고 받아쓰기 시험",
    )
    custom_room_id = 0
    old_floor_room_id = 0
    new_floor_room_id = 0
    with TestClient(app) as admin_client:
        admin = login(admin_client, "admin", "AdminPass!234")
        assert admin["role"] == "admin"
        initial_job_codes = admin_client.get("/api/job-codes?include_inactive=true")
        assert initial_job_codes.status_code == 200, initial_job_codes.text
        caregiver_staff_baseline = next(
            job for job in initial_job_codes.json() if job["code"] == "caregiver"
        )["active_staff_count"]

        actual_units = [
            unit
            for unit in admin_client.get("/api/org-units").json()
            if not unit["is_test_data"]
        ]
        assert actual_units
        protected_unit = patch(
            admin_client,
            f"/api/org-units/{actual_units[0]['id']}",
            {"name": "변경하면 안 되는 실제 조직"},
        )
        assert protected_unit.status_code == 409, protected_unit.text
        assert "실제 조직표" in protected_unit.json()["detail"]

        units = {}
        for unit_type, name in [
            ("business", "요양사업부"),
            ("department", "돌봄부"),
            ("floor", "시험 2층"),
            ("floor", "시험 3층"),
            ("team", "주간 A팀"),
            ("team", "주간 B팀"),
        ]:
            response = post(
                admin_client,
                "/api/org-units",
                {"unit_type": unit_type, "name": name},
            )
            assert response.status_code == 201, response.text
            units[(unit_type, name)] = response.json()["id"]

        unused_unit = post(
            admin_client,
            "/api/org-units",
            {"unit_type": "department", "name": "시험용 미사용 부서"},
        )
        assert unused_unit.status_code == 201, unused_unit.text
        unused_unit_id = unused_unit.json()["id"]
        deactivated_unit = delete(
            admin_client,
            f"/api/org-units/{unused_unit_id}",
        )
        assert deactivated_unit.status_code == 200, deactivated_unit.text
        assert not deactivated_unit.json()["is_active"]
        restored_unit = patch(
            admin_client,
            f"/api/org-units/{unused_unit_id}",
            {"is_active": True},
        )
        assert restored_unit.status_code == 200, restored_unit.text
        assert restored_unit.json()["is_active"]

        purge_unit = post(
            admin_client,
            "/api/org-units",
            {"unit_type": "department", "name": "완전 삭제 시험 부서"},
        )
        assert purge_unit.status_code == 201, purge_unit.text
        purge_unit_id = purge_unit.json()["id"]
        assert delete(
            admin_client,
            f"/api/org-units/{purge_unit_id}",
        ).status_code == 200
        inactive_units = admin_client.get("/api/org-units?include_inactive=true")
        purge_unit_preview = next(
            unit for unit in inactive_units.json() if unit["id"] == purge_unit_id
        )
        assert purge_unit_preview["reference_count"] == 0
        assert purge_unit_preview["can_delete"]
        purged_unit = delete(
            admin_client,
            f"/api/org-units/{purge_unit_id}/purge",
        )
        assert purged_unit.status_code == 204, purged_unit.text
        assert not any(
            unit["id"] == purge_unit_id
            for unit in admin_client.get(
                "/api/org-units?include_inactive=true"
            ).json()
        )

        purge_job = post(
            admin_client,
            "/api/job-codes",
            {"code": "temporary_job", "name": "완전 삭제 시험 직종"},
        )
        assert purge_job.status_code == 201, purge_job.text
        assert delete(admin_client, "/api/job-codes/temporary_job").status_code == 200
        inactive_jobs = admin_client.get("/api/job-codes?include_inactive=true")
        purge_job_preview = next(
            job for job in inactive_jobs.json() if job["code"] == "temporary_job"
        )
        assert purge_job_preview["reference_count"] == 0
        assert purge_job_preview["can_delete"]
        purged_job = delete(admin_client, "/api/job-codes/temporary_job/purge")
        assert purged_job.status_code == 204, purged_job.text
        assert not any(
            job["code"] == "temporary_job"
            for job in admin_client.get(
                "/api/job-codes?include_inactive=true"
            ).json()
        )
        generated_job = post(
            admin_client,
            "/api/job-codes",
            {"name": "음악치료사"},
        )
        assert generated_job.status_code == 201, generated_job.text
        assert generated_job.json()["code"].startswith("custom_")

        for room_payload in [
            {
                "name": "요양사업부 전체방",
                "kind": "business",
                "scope_unit_id": units[("business", "요양사업부")],
            },
            {
                "name": "돌봄부 전체방",
                "kind": "department",
                "scope_unit_id": units[("department", "돌봄부")],
            },
            {
                "name": "2층 직원방",
                "kind": "floor",
                "scope_unit_id": units[("floor", "시험 2층")],
                "resident_scope": "floor",
            },
            {
                "name": "3층 직원방",
                "kind": "floor",
                "scope_unit_id": units[("floor", "시험 3층")],
                "resident_scope": "floor",
            },
            {
                "name": "주간 A팀방",
                "kind": "team",
                "scope_unit_id": units[("team", "주간 A팀")],
            },
            {
                "name": "주간 B팀방",
                "kind": "team",
                "scope_unit_id": units[("team", "주간 B팀")],
            },
            {
                "name": "요양보호사방",
                "kind": "job",
                "job_code": "caregiver",
            },
        ]:
            room_response = post(admin_client, "/api/admin/rooms", room_payload)
            assert room_response.status_code == 201, room_response.text

        employee_base = {
            "role": "staff",
            "business_id": units[("business", "요양사업부")],
            "department_id": units[("department", "돌봄부")],
            "job_code": "caregiver",
            "floor_id": units[("floor", "시험 2층")],
            "team_id": units[("team", "주간 A팀")],
        }
        alice_response = post(
            admin_client,
            "/api/employees",
            {
                **employee_base,
                "username": "alice",
                "full_name": "가상 직원 가람",
                "password": "StaffPass!234",
                "employee_code": "T-001",
                "position_title": "요양팀장",
            },
        )
        bob_response = post(
            admin_client,
            "/api/employees",
            {
                **employee_base,
                "username": "bob",
                "full_name": "가상 직원 나래",
                "password": "StaffPass!345",
                "employee_code": "T-002",
            },
        )
        assert alice_response.status_code == 201, alice_response.text
        assert bob_response.status_code == 201, bob_response.text
        assert alice_response.json()["position_title"] == "요양팀장"
        alice_id = alice_response.json()["id"]
        bob_id = bob_response.json()["id"]

        unit_usage_response = admin_client.get("/api/org-units?include_inactive=true")
        assert unit_usage_response.status_code == 200, unit_usage_response.text
        business_usage = next(
            unit
            for unit in unit_usage_response.json()
            if unit["id"] == units[("business", "요양사업부")]
        )
        assert business_usage["active_staff_count"] == 2
        assert business_usage["active_room_count"] == 1
        assert business_usage["reference_count"] > 0
        assert not business_usage["can_delete"]
        unused_usage = next(
            unit
            for unit in unit_usage_response.json()
            if unit["id"] == unused_unit_id
        )
        assert unused_usage["active_staff_count"] == 0
        assert unused_usage["active_room_count"] == 0
        blocked_unit = delete(
            admin_client,
            f"/api/org-units/{units[('business', '요양사업부')]}",
        )
        assert blocked_unit.status_code == 409, blocked_unit.text

        job_usage_response = admin_client.get("/api/job-codes?include_inactive=true")
        assert job_usage_response.status_code == 200, job_usage_response.text
        caregiver_usage = next(
            job for job in job_usage_response.json() if job["code"] == "caregiver"
        )
        assert caregiver_usage["active_staff_count"] == caregiver_staff_baseline + 2
        assert caregiver_usage["active_room_count"] == 1
        assert caregiver_usage["reference_count"] > 0
        assert not caregiver_usage["can_delete"]

        custom_response = post(
            admin_client,
            "/api/rooms/custom",
            {"name": "낙상예방 지정방", "member_ids": [alice_id, bob_id]},
        )
        assert custom_response.status_code == 201, custom_response.text
        custom_room_id = custom_response.json()["id"]
        empty_custom_room = patch(
            admin_client,
            f"/api/admin/rooms/{custom_room_id}",
            {"member_ids": []},
        )
        assert empty_custom_room.status_code == 422, empty_custom_room.text
        assert "1명 이상" in empty_custom_room.json()["detail"]

        alice_client = TestClient(app)
        bob_client = TestClient(app)
        assert login(alice_client, "alice", "StaffPass!234")["must_change_password"]
        assert login(bob_client, "bob", "StaffPass!345")["must_change_password"]
        alice_password = post(
            alice_client,
            "/api/auth/password",
            {
                "current_password": "StaffPass!234",
                "new_password": "StaffReady!234",
            },
        )
        bob_password = post(
            bob_client,
            "/api/auth/password",
            {
                "current_password": "StaffPass!345",
                "new_password": "StaffReady!345",
            },
        )
        assert alice_password.status_code == 200, alice_password.text
        assert bob_password.status_code == 200, bob_password.text
        assert not alice_password.json()["must_change_password"]
        assert not bob_password.json()["must_change_password"]

        alice_rooms = alice_client.get("/api/rooms").json()
        room_names = {room["name"] for room in alice_rooms}
        assert "전체 직원방" in room_names
        assert "2층 직원방" in room_names
        assert "요양보호사방" in room_names
        assert "주간 A팀방" in room_names
        assert "낙상예방 지정방" in room_names
        assert "나와의 대화" in room_names
        alice_self_room = next(
            room for room in alice_rooms if room["kind"] == "self"
        )
        bob_self_room = next(
            room for room in bob_client.get("/api/rooms").json() if room["kind"] == "self"
        )
        assert alice_self_room["id"] != bob_self_room["id"]
        assert (
            bob_client.get(
                f"/api/rooms/{alice_self_room['id']}/messages"
            ).status_code
            == 403
        )
        self_message = post(
            alice_client,
            f"/api/rooms/{alice_self_room['id']}/messages",
            {"body": "나만 보는 시험 메모", "message_type": "chat"},
        )
        assert self_message.status_code == 201, self_message.text
        old_floor_room_id = next(
            room["id"] for room in alice_rooms if room["name"] == "2층 직원방"
        )
        override_members = patch(
            admin_client,
            f"/api/admin/rooms/{old_floor_room_id}",
            {"member_ids": [alice_id, admin["id"]]},
        )
        assert override_members.status_code == 200, override_members.text
        assert set(override_members.json()["member_ids"]) == {
            alice_id,
            admin["id"],
        }
        assert (
            bob_client.get(f"/api/rooms/{old_floor_room_id}/messages").status_code
            == 403
        )
        assert any(
            room["id"] == old_floor_room_id
            for room in admin_client.get("/api/rooms").json()
        )
        restored_auto_members = patch(
            admin_client,
            f"/api/admin/rooms/{old_floor_room_id}",
            {"member_ids": [alice_id, bob_id]},
        )
        assert restored_auto_members.status_code == 200, restored_auto_members.text
        assert set(restored_auto_members.json()["member_ids"]) == {
            alice_id,
            bob_id,
        }
        assert (
            bob_client.get(f"/api/rooms/{old_floor_room_id}/messages").status_code
            == 200
        )

        with bob_client.websocket_connect(
            "/api/ws", headers={"origin": "http://testserver"}
        ) as websocket:
            assert websocket.receive_json()["event"] == "ready"
            sent = post(
                alice_client,
                f"/api/rooms/{custom_room_id}/messages",
                {"body": "가상 보고: 2층 순회 완료했습니다.", "message_type": "chat"},
            )
            assert sent.status_code == 201, sent.text
            realtime = websocket.receive_json()
            assert realtime["event"] == "message_created"
            assert realtime["message"]["body"] == "가상 보고: 2층 순회 완료했습니다."
            source_message_id = sent.json()["id"]

        forwarded = post(
            alice_client,
            f"/api/messages/{source_message_id}/forward",
            {"room_ids": [alice_self_room["id"]]},
        )
        assert forwarded.status_code == 201, forwarded.text
        assert len(forwarded.json()) == 1
        assert forwarded.json()[0]["body"] == "가상 보고: 2층 순회 완료했습니다."
        assert (
            forwarded.json()[0]["forwarded_from"]["room_name"]
            == "낙상예방 지정방"
        )
        forwarded_self_messages = alice_client.get(
            f"/api/rooms/{alice_self_room['id']}/messages"
        ).json()
        assert any(
            message["forwarded_from"]
            and message["forwarded_from"]["message_id"] == source_message_id
            for message in forwarded_self_messages
        )

        forbidden_forward = post(
            bob_client,
            f"/api/messages/{source_message_id}/forward",
            {"room_ids": [alice_self_room["id"]]},
        )
        assert forbidden_forward.status_code == 403

        attachment_source = alice_client.post(
            f"/api/rooms/{custom_room_id}/messages-with-files",
            data={"body": "첨부파일 전달 시험"},
            files={"files": ("forward.png", PNG_1X1, "image/png")},
            headers={"origin": "http://testserver"},
        )
        assert attachment_source.status_code == 201, attachment_source.text
        attachment_forward = post(
            alice_client,
            f"/api/messages/{attachment_source.json()['id']}/forward",
            {"room_ids": [alice_self_room["id"]]},
        )
        assert attachment_forward.status_code == 201, attachment_forward.text
        forwarded_attachment = attachment_forward.json()[0]["attachments"][0]
        downloaded_forward = alice_client.get(forwarded_attachment["download_url"])
        assert downloaded_forward.status_code == 200
        assert downloaded_forward.content == PNG_1X1

        audio_source = alice_client.post(
            f"/api/rooms/{custom_room_id}/messages-with-files",
            data={"body": "음성파일 받아쓰기 시험"},
            files={"files": ("voice.wav", WAV_SILENCE, "audio/wav")},
            headers={"origin": "http://testserver"},
        )
        assert audio_source.status_code == 201, audio_source.text
        work_items = admin_client.get("/api/work-items")
        assert work_items.status_code == 200, work_items.text
        audio_work_item = next(
            item
            for item in work_items.json()
            if item["message"]["id"] == audio_source.json()["id"]
        )
        audio_attachment = audio_work_item["message"]["attachments"][0]
        assert audio_attachment["text_extraction"]["status"] == "completed"
        assert (
            audio_attachment["text_extraction"]["extracted_text"]
            == "가상 직원 음성보고 받아쓰기 시험"
        )

        bob_rooms = bob_client.get("/api/rooms").json()
        custom_room = next(room for room in bob_rooms if room["id"] == custom_room_id)
        assert custom_room["unread_count"] == 3
        messages = bob_client.get(f"/api/rooms/{custom_room_id}/messages").json()
        assert messages[-1]["sender_name"] == "가상 직원 가람"
        read_response = post(
            bob_client,
            f"/api/rooms/{custom_room_id}/read",
            {"message_id": messages[-1]["id"]},
        )
        assert read_response.status_code == 204

        update_response = patch(
            admin_client,
            f"/api/employees/{alice_id}",
            {
                "floor_id": units[("floor", "시험 3층")],
                "team_id": units[("team", "주간 B팀")],
            },
        )
        assert update_response.status_code == 200, update_response.text
        changed_rooms = alice_client.get("/api/rooms").json()
        changed_names = {room["name"] for room in changed_rooms}
        assert "3층 직원방" in changed_names
        assert "주간 B팀방" in changed_names
        assert "2층 직원방" not in changed_names
        assert "주간 A팀방" not in changed_names
        assert "낙상예방 지정방" in changed_names
        new_floor_room_id = next(
            room["id"] for room in changed_rooms if room["name"] == "3층 직원방"
        )
        assert (
            alice_client.get(f"/api/rooms/{old_floor_room_id}/messages").status_code
            == 403
        )
        assert (
            bob_client.get(f"/api/rooms/{new_floor_room_id}/messages").status_code
            == 403
        )

        clear_bob_job = patch(
            admin_client,
            f"/api/employees/{bob_id}",
            {
                "job_code": None,
                "position_title": "요양팀장",
            },
        )
        assert clear_bob_job.status_code == 200, clear_bob_job.text
        assert clear_bob_job.json()["job_code"] is None
        assert clear_bob_job.json()["job_name"] is None
        assert clear_bob_job.json()["position_title"] == "요양팀장"
        bob_rooms_without_job = bob_client.get("/api/rooms").json()
        assert "요양보호사방" not in {
            room["name"] for room in bob_rooms_without_job
        }
        assert "2층 직원방" in {
            room["name"] for room in bob_rooms_without_job
        }
        assert "낙상예방 지정방" in {
            room["name"] for room in bob_rooms_without_job
        }

        with alice_client.websocket_connect(
            "/api/ws", headers={"origin": "http://testserver"}
        ) as websocket:
            assert websocket.receive_json()["event"] == "ready"
            terminate_response = post(
                admin_client,
                f"/api/employees/{alice_id}/terminate",
                {},
            )
            assert terminate_response.status_code == 200, terminate_response.text
            forced = websocket.receive_json()
            assert forced["event"] == "force_logout"
            assert "퇴사" in forced["reason"]

        assert alice_client.get("/api/auth/me").status_code in {401, 403}
        blocked_login = post(
            TestClient(app),
            "/api/auth/login",
            {"username": "alice", "password": "StaffReady!234"},
        )
        assert blocked_login.status_code == 403

        restore_response = post(
            admin_client,
            f"/api/employees/{alice_id}/restore",
            {},
        )
        assert restore_response.status_code == 200, restore_response.text
        assert restore_response.json()["employment_status"] == "active"
        restored_alice = TestClient(app)
        login(restored_alice, "alice", "StaffReady!234")

        active_delete_response = admin_client.delete(f"/api/employees/{alice_id}")
        assert active_delete_response.status_code == 409
        assert "먼저 퇴사 처리" in active_delete_response.json()["detail"]

        terminate_again = post(
            admin_client,
            f"/api/employees/{alice_id}/terminate",
            {},
        )
        assert terminate_again.status_code == 200, terminate_again.text
        delete_response = admin_client.delete(f"/api/employees/{alice_id}")
        assert delete_response.status_code == 204, delete_response.text
        employee_ids_after_delete = {
            employee["id"] for employee in admin_client.get("/api/employees").json()
        }
        assert alice_id not in employee_ids_after_delete
        assert admin_client.delete(f"/api/employees/{alice_id}").status_code == 404
        assert restored_alice.get("/api/auth/me").status_code in {401, 403}

    # 앱 수명주기를 다시 시작해도 SQLite의 조직·직원·방·메시지가 유지되어야 합니다.
    with TestClient(app) as restarted_client:
        login(restarted_client, "bob", "StaffReady!345")
        persisted_rooms = restarted_client.get("/api/rooms").json()
        assert any(room["id"] == custom_room_id for room in persisted_rooms)
        persisted_messages = restarted_client.get(
            f"/api/rooms/{custom_room_id}/messages"
        ).json()
        assert any(
            message["body"] == "가상 보고: 2층 순회 완료했습니다."
            for message in persisted_messages
        )

        admin_again = TestClient(app)
        login(admin_again, "admin", "AdminPass!234")
        managed_rooms = admin_again.get("/api/rooms/custom")
        assert managed_rooms.status_code == 200, managed_rooms.text
        existing_managed = next(
            room for room in managed_rooms.json() if room["id"] == custom_room_id
        )
        assert existing_managed["member_ids"] == [bob_id]
        assert restarted_client.get("/api/rooms/custom").status_code == 403

        temporary_room = post(
            admin_again,
            "/api/rooms/custom",
            {"name": "가상 임시 협업방", "member_ids": [bob_id]},
        )
        assert temporary_room.status_code == 201, temporary_room.text
        temporary_room_id = temporary_room.json()["id"]
        updated_room = patch(
            admin_again,
            f"/api/rooms/custom/{temporary_room_id}",
            {"name": "가상 임시 협업방 수정", "member_ids": [bob_id]},
        )
        assert updated_room.status_code == 200, updated_room.text
        assert updated_room.json()["name"] == "가상 임시 협업방 수정"
        assert updated_room.json()["member_ids"] == [bob_id]

        default_room_id = next(
            room["id"] for room in persisted_rooms if room["name"] == "전체 직원방"
        )
        closed_default = delete(admin_again, f"/api/admin/rooms/{default_room_id}")
        assert closed_default.status_code == 204, closed_default.text
        assert not any(
            room["id"] == default_room_id
            for room in restarted_client.get("/api/rooms").json()
        )
        restored_default = post(
            admin_again,
            f"/api/admin/rooms/{default_room_id}/restore",
            {},
        )
        assert restored_default.status_code == 200, restored_default.text
        assert any(
            room["id"] == default_room_id
            for room in restarted_client.get("/api/rooms").json()
        )
        assert (
            delete(
                restarted_client,
                f"/api/rooms/custom/{temporary_room_id}",
            ).status_code
            == 403
        )

        with restarted_client.websocket_connect(
            "/api/ws", headers={"origin": "http://testserver"}
        ) as websocket:
            assert websocket.receive_json()["event"] == "ready"
            closed = delete(
                admin_again,
                f"/api/rooms/custom/{temporary_room_id}",
            )
            assert closed.status_code == 204, closed.text
            changed = websocket.receive_json()
            assert changed["event"] == "rooms_changed"
            assert changed["room_id"] == temporary_room_id

        blocked_custom_restore = post(
            admin_again,
            f"/api/admin/rooms/{temporary_room_id}/restore",
            {},
        )
        assert blocked_custom_restore.status_code == 409
        assert "새 채팅방" in blocked_custom_restore.json()["detail"]

        assert not any(
            room["id"] == temporary_room_id
            for room in restarted_client.get("/api/rooms").json()
        )
        assert (
            restarted_client.get(
                f"/api/rooms/{temporary_room_id}/messages"
            ).status_code
            == 403
        )
        closed_rooms = admin_again.get("/api/rooms/custom?include_inactive=true")
        assert closed_rooms.status_code == 200, closed_rooms.text
        closed_room = next(
            room for room in closed_rooms.json() if room["id"] == temporary_room_id
        )
        assert not closed_room["is_active"]
