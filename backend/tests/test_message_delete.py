from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, Message, MessageAttachment, MessageComment


PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDAT\x08\xd7c\xf8\xcf\xc0\x00\x00"
    b"\x03\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
ORIGIN = {"origin": "http://testserver"}


def _login(client: TestClient, username: str, password: str) -> dict:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
        headers=ORIGIN,
    )
    assert response.status_code == 200, response.text
    return response.json()["user"]


def _post(client: TestClient, path: str, payload: dict):
    return client.post(path, json=payload, headers=ORIGIN)


def test_message_recall_retains_evidence_and_masks_regular_access() -> None:
    with TestClient(app) as admin_client:
        _login(admin_client, "admin", "AdminPass!234")
        created = _post(
            admin_client,
            "/api/employees",
            {
                "username": "message-recall-staff",
                "full_name": "회수 시험 직원",
                "password": "654321",
                "role": "staff",
            },
        )
        assert created.status_code == 201, created.text

        with TestClient(app) as staff_client:
            _login(staff_client, "message-recall-staff", "654321")
            changed = _post(
                staff_client,
                "/api/auth/password",
                {"current_password": "654321", "new_password": "abcdef"},
            )
            assert changed.status_code == 200, changed.text
            room = next(
                item for item in staff_client.get("/api/rooms").json()
                if item["kind"] == "all"
            )
            room_id = room["id"]

            upload = staff_client.post(
                f"/api/rooms/{room_id}/messages-with-files",
                data={
                    "body": "실수로 올린 첨부 메시지",
                    "message_type": "chat",
                    "report_image": "false",
                },
                files={"files": ("accident.png", PNG_1X1, "image/png")},
                headers=ORIGIN,
            )
            assert upload.status_code == 201, upload.text
            own_message_id = upload.json()["id"]
            attachment_id = upload.json()["attachments"][0]["id"]
            comment = _post(
                admin_client,
                f"/api/messages/{own_message_id}/comments",
                {"body": "회수 전 작성한 댓글"},
            )
            assert comment.status_code == 201, comment.text

            with SessionLocal() as db:
                attachment = db.get(MessageAttachment, UUID(attachment_id))
                assert attachment is not None
                stored_path = (
                    Path(settings.upload_dir).resolve() / attachment.storage_key
                ).resolve()
                assert stored_path.is_file()

            admin_message = _post(
                admin_client,
                f"/api/rooms/{room_id}/messages",
                {"body": "관리자 메시지", "message_type": "chat"},
            )
            assert admin_message.status_code == 201, admin_message.text
            forbidden = _post(
                staff_client,
                f"/api/messages/{admin_message.json()['id']}/recall",
                {},
            )
            assert forbidden.status_code == 403, forbidden.text

            recalled = _post(
                staff_client,
                f"/api/messages/{own_message_id}/recall",
                {},
            )
            assert recalled.status_code == 200, recalled.text
            assert recalled.json()["message_id"] == own_message_id
            assert own_message_id in recalled.json()["recalled_message_ids"]
            assert stored_path.is_file()

            messages = staff_client.get(
                f"/api/rooms/{room_id}/messages"
            ).json()
            recalled_shell = next(
                item for item in messages if item["id"] == own_message_id
            )
            assert recalled_shell["is_recalled"] is True
            assert recalled_shell["body"] == "작성자가 회수한 메시지입니다."
            assert recalled_shell["attachments"] == []
            assert recalled_shell["resident"] is None
            assert recalled_shell["comment_count"] == 0
            assert staff_client.get(
                f"/api/messages/{own_message_id}"
            ).status_code == 409
            assert staff_client.get(
                f"/api/attachments/{attachment_id}"
            ).status_code == 409
            admin_only_download_url = (
                f"/api/admin/conversations/attachments/{attachment_id}"
            )
            assert staff_client.get(admin_only_download_url).status_code == 403
            assert admin_client.get(admin_only_download_url).status_code == 403
            assert _post(
                admin_client,
                f"/api/messages/{own_message_id}/comments",
                {"body": "회수 후 댓글"},
            ).status_code == 409

            second = _post(
                staff_client,
                f"/api/rooms/{room_id}/messages",
                {"body": "관리자가 회수할 메시지", "message_type": "chat"},
            )
            assert second.status_code == 201, second.text
            blocked_admin_recall = _post(
                admin_client,
                f"/api/messages/{second.json()['id']}/recall",
                {"reason": "업무 기록 정정"},
            )
            assert blocked_admin_recall.status_code == 403

            elevated = _post(
                admin_client,
                "/api/admin/conversation-access",
                {"password": "AdminPass!234"},
            )
            assert elevated.status_code == 200, elevated.text
            admin_recalled = _post(
                admin_client,
                f"/api/messages/{second.json()['id']}/recall",
                {"reason": "업무 기록 정정"},
            )
            assert admin_recalled.status_code == 200, admin_recalled.text

            admin_detail = admin_client.get(
                f"/api/admin/conversations/messages/{own_message_id}"
            )
            assert admin_detail.status_code == 200, admin_detail.text
            assert admin_detail.headers["cache-control"] == "private, no-store"
            assert admin_detail.json()["message"]["body"] == "실수로 올린 첨부 메시지"
            assert admin_detail.json()["message"]["attachments"][0]["id"] == attachment_id
            admin_download_url = admin_detail.json()["message"]["attachments"][0][
                "download_url"
            ]
            assert admin_download_url == (
                admin_only_download_url
            )
            assert admin_detail.json()["comments"][0]["body"] == "회수 전 작성한 댓글"

            admin_attachment = admin_client.get(admin_download_url)
            assert admin_attachment.status_code == 200, admin_attachment.text
            assert admin_attachment.headers["cache-control"] == "private, no-store"
            assert admin_attachment.content == PNG_1X1

            with SessionLocal() as db:
                own_message = db.get(Message, UUID(own_message_id))
                second_message = db.get(Message, UUID(second.json()["id"]))
                assert own_message is not None
                assert second_message is not None
                assert own_message.lifecycle_status == "recalled"
                assert second_message.lifecycle_status == "recalled"
                assert db.get(MessageAttachment, UUID(attachment_id)) is not None
                assert db.scalar(
                    select(MessageComment).where(
                        MessageComment.message_id == UUID(own_message_id)
                    )
                ) is not None
                audits = db.scalars(
                    select(AuditEvent).where(
                        AuditEvent.action == "message.recalled",
                        AuditEvent.target_id.in_(
                            [UUID(own_message_id), UUID(second.json()["id"])]
                        ),
                    )
                ).all()
                assert len(audits) == 2
                assert all("실수로 올린" not in str(audit.details) for audit in audits)
