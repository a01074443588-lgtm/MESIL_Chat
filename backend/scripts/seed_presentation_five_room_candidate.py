from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil

from sqlalchemy import func, select

from app.presentation_five_room_synthetic import (
    DISPLAY_NOTICE,
    FIXTURE_VERSION,
    SAFETY_CONTRACT,
    validate_fixture,
)


TARGET_ENVIRONMENT = "presentation"
TARGET_DATABASE = "mesil_presentation"
TARGET_SCHEMA = "mesil_presentation"
TARGET_COOKIE = "mesil_presentation_20260911_session"
TARGET_ORIGIN = "http://127.0.0.1:18130"
TARGET_UPLOAD_DIR = "/data/presentation-candidate-20260911/uploads"
TARGET_VOLUME = "mesil_presentation_20260911_pgdata"
TARGET_ORGANIZATION_CODE = "mesil-presentation-20260911"
FIXTURE_PATH = Path(
    os.getenv(
        "PRESENTATION_FIXTURE_PATH",
        str(
            Path(__file__).resolve().parents[2]
            / "backend"
            / "tests"
            / "fixtures"
            / FIXTURE_VERSION
            / "fixture.json"
        ),
    )
)

USERNAME_BY_CODE = {
    "관리자01": "pres_admin",
    "사복01": "pres_social_01",
    "사복02": "pres_social_02",
    "간호01": "pres_nurse_01",
    "영양01": "pres_dietitian_01",
    "작치01": "pres_occupational_01",
    "요보01": "pres_caregiver_01",
    "요보02": "pres_caregiver_02",
    "요보03": "pres_caregiver_03",
    "요보04": "pres_caregiver_04",
    "사무01": "pres_office_01",
    "운전01": "pres_driver_01",
}


def application_job_code(fixture_job_code: str) -> str:
    return {
        "administrative_staff": "office_worker",
    }.get(fixture_job_code, fixture_job_code)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="격리된 localhost 발표 후보에 5개 방 합성자료를 적재합니다."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check-only",
        action="store_true",
        help="기본값. 환경·fixture·대상 비어 있음만 검사",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="모든 가드 통과 후 새 발표 전용 대상에 실제 적재",
    )
    mode.add_argument(
        "--repair-work-item-snapshots",
        action="store_true",
        help="현재 fixture가 만든 발표 전용 기록 후보 5개의 API 스냅샷만 복구",
    )
    return parser.parse_args()


def _secret_is_set(value: object) -> bool:
    if value is None:
        return False
    getter = getattr(value, "get_secret_value", None)
    if callable(getter):
        return bool(getter())
    return bool(value)


def validate_runtime_contract(runtime: object) -> None:
    exact_values = {
        "environment": TARGET_ENVIRONMENT,
        "postgres_db": TARGET_DATABASE,
        "database_schema": TARGET_SCHEMA,
        "session_cookie_name": TARGET_COOKIE,
        "upload_dir": TARGET_UPLOAD_DIR,
    }
    for field, expected in exact_values.items():
        if getattr(runtime, field, None) != expected:
            raise RuntimeError(f"발표 후보 전용 {field} 계약과 일치하지 않습니다.")

    if getattr(runtime, "origin_list", None) != [TARGET_ORIGIN]:
        raise RuntimeError("발표 후보 origin은 localhost 전용 주소 하나여야 합니다.")

    disabled_flags = (
        "stt_enabled",
        "ai_review_external_enabled",
        "ai_assist_allow_external_real_image_data",
        "web_push_enabled",
        "voice_call_enabled",
        "self_chat_enabled",
    )
    if any(bool(getattr(runtime, field, False)) for field in disabled_flags):
        raise RuntimeError("발표 후보에서 외부 연동 기능이 활성화되어 있습니다.")

    forbidden_secrets = (
        "firebase_project_id",
        "nvidia_api_key",
        "openai_api_key",
        "gemini_api_key",
        "anthropic_api_key",
        "ollama_cloud_api_key",
        "openai_compatible_api_key",
    )
    if any(_secret_is_set(getattr(runtime, field, None)) for field in forbidden_secrets):
        raise RuntimeError("발표 후보에 외부 서비스 비밀값이 연결되어 있습니다.")

    if os.getenv("PRESENTATION_DOCKER_VOLUME", TARGET_VOLUME) != TARGET_VOLUME:
        raise RuntimeError("발표 후보 Docker 볼륨 계약과 일치하지 않습니다.")
    if getattr(runtime, "organization_code", None) not in (
        None,
        TARGET_ORGANIZATION_CODE,
    ):
        raise RuntimeError("발표 후보 조직 코드 계약과 일치하지 않습니다.")


def validate_seed_fixture(fixture: dict) -> dict:
    validate_fixture(fixture)
    if fixture.get("safety_contract") != SAFETY_CONTRACT:
        raise ValueError("합성 fixture 안전 계약이 변경되었습니다.")
    expected_counts = {
        "staff_count": 12,
        "resident_count": 8,
        "room_count": 5,
        "message_count": 336,
        "attachment_count": 14,
        "scenario_count": 5,
    }
    actual_counts = {
        "staff_count": len(fixture["staff"]),
        "resident_count": len(fixture["residents"]),
        "room_count": len(fixture["rooms"]),
        "message_count": len(fixture["messages"]),
        "attachment_count": len(fixture["attachments"]),
        "scenario_count": len(fixture["scenarios"]),
    }
    if actual_counts != expected_counts:
        raise ValueError("합성 fixture의 고정 발표 수치가 변경되었습니다.")
    return {
        "fixture_version": fixture["schema_version"],
        **actual_counts,
        "external_transfer_count": 0,
        "official_record_write_count": 0,
        "contains_real_personal_data": False,
    }


def resolve_fixture_asset(fixture_dir: Path, relative_name: str) -> Path:
    base = fixture_dir.resolve()
    candidate = (base / relative_name).resolve()
    if candidate.parent != base or not candidate.is_file():
        raise RuntimeError("fixture 디렉터리 밖의 첨부 자산은 사용할 수 없습니다.")
    return candidate


def _load_fixture() -> dict:
    if not FIXTURE_PATH.is_file():
        raise RuntimeError("발표 합성 fixture 파일을 찾을 수 없습니다.")
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    validate_seed_fixture(fixture)
    return fixture


def _target_fingerprint() -> str:
    from app.config import settings

    source = "|".join(
        (
            settings.postgres_host,
            str(settings.postgres_port),
            settings.postgres_db,
            settings.database_schema,
            settings.organization_code,
            os.getenv("PRESENTATION_DOCKER_VOLUME", ""),
        )
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _business_counts(db) -> dict[str, int]:
    from app.models import (
        Message,
        MessageAttachment,
        Organization,
        Resident,
        Room,
        Staff,
        User,
        WorkItem,
    )

    models = {
        "organizations": Organization,
        "staff": Staff,
        "users": User,
        "residents": Resident,
        "rooms": Room,
        "messages": Message,
        "attachments": MessageAttachment,
        "work_items": WorkItem,
    }
    return {
        name: int(db.scalar(select(func.count()).select_from(model)) or 0)
        for name, model in models.items()
    }


def _assert_target_is_empty(db) -> dict[str, int]:
    counts = _business_counts(db)
    if any(counts.values()):
        raise RuntimeError("발표 후보 대상이 비어 있지 않아 적재를 중단했습니다.")
    return counts


def _fixture_attachment_bytes(fixture_dir: Path, item: dict) -> bytes:
    inline = item.get("inline_content")
    if inline is not None:
        content = inline.encode("utf-8")
    else:
        source = resolve_fixture_asset(fixture_dir / "assets", item["original_name"])
        content = source.read_bytes()
    if len(content) != item["size_bytes"]:
        raise RuntimeError("합성 첨부 자산 크기가 fixture와 다릅니다.")
    if hashlib.sha256(content).hexdigest() != item["sha256"]:
        raise RuntimeError("합성 첨부 자산 해시가 fixture와 다릅니다.")
    return content


def _create_user(db, organization, item: dict, password: str, actor_id=None):
    from app.models import Staff, User
    from app.security import hash_password
    from app.services import set_staff_job, set_user_role

    code = item["code"]
    staff = Staff(
        organization_id=organization.id,
        internal_code=code,
        display_name=code,
        job_title="직종 미지정",
        employment_status="active",
        is_test_data=True,
        is_active=True,
    )
    db.add(staff)
    db.flush()
    user = User(
        organization_id=organization.id,
        staff_id=staff.id,
        staff=staff,
        username=USERNAME_BY_CODE[code],
        display_name=code,
        password_hash=hash_password(password),
        is_active=True,
        can_process_records=bool(item.get("can_process_records")),
        must_change_password=False,
    )
    db.add(user)
    db.flush()
    set_user_role(db, user, item["role"])
    set_staff_job(
        db,
        staff,
        application_job_code(item["job_code"]),
        actor_id or user.id,
    )
    return user


def _reply_snapshot(message) -> dict[str, str]:
    return {
        "message_id": str(message.id),
        "sender_name": message.sender.full_name,
        "body": message.body,
        "created_at": message.created_at.astimezone(timezone.utc).isoformat(),
    }


def presentation_work_item_source_snapshot(
    message,
    *,
    room_name: str,
    sender_name: str,
    resident_name: str | None,
    attachment_ids: list,
    scenario_id: str,
) -> dict:
    created_at = message.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    else:
        created_at = created_at.astimezone(timezone.utc)
    return {
        "message_id": str(message.id),
        "room_id": str(message.room_id),
        "room_name": room_name,
        "sender_id": str(message.sender_id),
        "sender_name": sender_name,
        "resident_id": str(message.resident_id) if message.resident_id else None,
        "resident_name": resident_name,
        "resident_names": [resident_name] if resident_name else [],
        "body": message.body,
        "message_type": message.message_type,
        "attachment_ids": [str(attachment_id) for attachment_id in attachment_ids],
        "created_at": created_at.isoformat(),
        "fixture_message_id": (message.extra_data or {}).get("fixture_message_id"),
        "scenario_id": scenario_id,
        "display_notice": DISPLAY_NOTICE,
        "official_record": False,
        "external_transfer_allowed": False,
    }


def _seed(db, fixture: dict, password: str) -> dict:
    from app.config import settings
    from app.models import (
        AttachmentTextExtraction,
        Message,
        MessageAttachment,
        MessageResidentLink,
        Resident,
        Room,
        RoomMembership,
        WorkItem,
    )
    from app.services import ensure_reference_data

    before = _assert_target_is_empty(db)
    organization = ensure_reference_data(db)
    organization.internal_code = TARGET_ORGANIZATION_CODE
    organization.name = "MESIL Chat 결선 발표 합성기관"
    organization.service_type = "daycare"

    users_by_code: dict[str, object] = {}
    admin_item = next(item for item in fixture["staff"] if item["code"] == "관리자01")
    admin = _create_user(db, organization, admin_item, password)
    users_by_code["관리자01"] = admin
    for item in fixture["staff"]:
        if item["code"] == "관리자01":
            continue
        users_by_code[item["code"]] = _create_user(
            db, organization, item, password, admin.id
        )

    rooms_by_key: dict[str, object] = {}
    for index, item in enumerate(fixture["rooms"], start=1):
        room = Room(
            organization_id=organization.id,
            kind="all" if item["key"] == "all_staff" else "custom",
            name=item["name"],
            resident_scope="all",
            sort_order=index * 10,
            is_active=True,
            is_test_data=True,
            created_by_id=admin.id,
        )
        db.add(room)
        db.flush()
        rooms_by_key[item["key"]] = room
        for code in item["member_codes"]:
            db.add(
                RoomMembership(
                    organization_id=organization.id,
                    room_id=room.id,
                    staff_id=users_by_code[code].staff_id,
                    source="manual",
                    created_by=admin.id,
                )
            )

    residents_by_code: dict[str, object] = {}
    for index, item in enumerate(fixture["residents"], start=1):
        resident = Resident(
            organization_id=organization.id,
            internal_code=item["internal_code"],
            display_name=item["code"],
            status="active",
            service_type=item["service_type"],
            sort_order=index,
            is_test_data=True,
            is_active=True,
        )
        db.add(resident)
        db.flush()
        residents_by_code[item["code"]] = resident

    messages_by_fixture_id: dict[str, object] = {}
    message_items_by_fixture_id = {
        item["message_id"]: item for item in fixture["messages"]
    }
    for item in fixture["messages"]:
        created_at = datetime.fromisoformat(item["created_at"]).astimezone(timezone.utc)
        resident = residents_by_code.get(item.get("resident_code"))
        extra_data = {
            "fixture_version": FIXTURE_VERSION,
            "fixture_message_id": item["message_id"],
            "scenario_id": item.get("scenario_id"),
            "service_type": item["service_type"],
            "display_notice": DISPLAY_NOTICE,
            **SAFETY_CONTRACT,
        }
        if reply_id := item.get("reply_to_message_id"):
            extra_data["reply_to"] = _reply_snapshot(messages_by_fixture_id[reply_id])
        message = Message(
            organization_id=organization.id,
            room_id=rooms_by_key[item["room_key"]].id,
            sender_id=users_by_code[item["sender_code"]].id,
            message_type="chat",
            body=item["body"],
            resident_id=resident.id if resident else None,
            resident_ref=resident.display_name if resident else None,
            extra_data=extra_data,
            lifecycle_status="active",
            is_test_data=True,
            created_at=created_at,
        )
        db.add(message)
        db.flush()
        if resident is not None:
            db.add(
                MessageResidentLink(
                    organization_id=organization.id,
                    message_id=message.id,
                    resident_id=resident.id,
                    source="manual",
                    status="confirmed",
                    reviewed_by_id=admin.id,
                    reviewed_at=created_at,
                    created_at=created_at,
                )
            )
        messages_by_fixture_id[item["message_id"]] = message

    fixture_dir = FIXTURE_PATH.parent
    copied_paths: list[Path] = []
    attachment_ids_by_message_id: dict[object, list] = {}
    try:
        for ordinal, item in enumerate(fixture["attachments"]):
            content = _fixture_attachment_bytes(fixture_dir, item)
            suffix = Path(item["original_name"]).suffix.lower()
            storage_key = f"presentation-{item['attachment_id'].lower()}{suffix}"
            destination = Path(settings.upload_dir) / storage_key
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            copied_paths.append(destination)
            message = messages_by_fixture_id[item["message_id"]]
            attachment = MessageAttachment(
                organization_id=organization.id,
                owner_module_code="staff_hub",
                entity_type="staff_hub_message",
                message_id=message.id,
                uploader_id=admin.id,
                storage_key=storage_key,
                original_name=item["original_name"],
                mime_type=item["mime_type"],
                size_bytes=len(content),
                upload_ordinal=ordinal,
                sha256=item["sha256"],
                created_at=message.created_at,
            )
            db.add(attachment)
            db.flush()
            attachment_ids_by_message_id.setdefault(message.id, []).append(attachment.id)
            pending_staff_review = (
                (message.extra_data or {}).get("scenario_id")
                == "SCN-ATTACHMENT-HANDOVER"
            )
            db.add(
                AttachmentTextExtraction(
                    organization_id=organization.id,
                    attachment_id=attachment.id,
                    status="completed" if pending_staff_review else "reviewed",
                    provider="synthetic_fixture",
                    model_name=FIXTURE_VERSION,
                    extracted_text=item["extracted_text"],
                    original_extracted_text=item["extracted_text"],
                    suggested_text=item["extracted_text"],
                    reviewed_text=None if pending_staff_review else item["extracted_text"],
                    suggestion_details=[
                        {
                            "synthetic_fixture": True,
                            "official_record": False,
                            "external_transfer_allowed": False,
                            "requires_staff_review": pending_staff_review,
                        }
                    ],
                    requested_by_id=admin.id,
                    reviewed_by_id=None if pending_staff_review else admin.id,
                    started_at=message.created_at,
                    completed_at=message.created_at + timedelta(seconds=1),
                    reviewed_at=(
                        None
                        if pending_staff_review
                        else message.created_at + timedelta(seconds=2)
                    ),
                    created_at=message.created_at,
                )
            )

        for scenario in fixture["scenarios"]:
            source = messages_by_fixture_id[scenario["message_ids"][0]]
            source_item = message_items_by_fixture_id[scenario["message_ids"][0]]
            source_resident = residents_by_code.get(source_item.get("resident_code"))
            db.add(
                WorkItem(
                    organization_id=organization.id,
                    source_message_id=source.id,
                    resident_id=source.resident_id,
                    status="pending",
                    source_snapshot=presentation_work_item_source_snapshot(
                        source,
                        room_name=rooms_by_key[source_item["room_key"]].name,
                        sender_name=users_by_code[
                            source_item["sender_code"]
                        ].display_name,
                        resident_name=(
                            source_resident.display_name if source_resident else None
                        ),
                        attachment_ids=attachment_ids_by_message_id.get(source.id, []),
                        scenario_id=scenario["scenario_id"],
                    ),
                    document_types=(
                        ["care_service_record"] if source.resident_id else None
                    ),
                    ai_state="not_requested",
                    is_test_data=True,
                    created_at=source.created_at,
                )
            )

        db.commit()
    except Exception:
        db.rollback()
        for path in copied_paths:
            path.unlink(missing_ok=True)
        raise

    after = _business_counts(db)
    return {
        "status": "applied",
        "database_fingerprint": _target_fingerprint(),
        "docker_volume": TARGET_VOLUME,
        "before": before,
        "after": after,
        **validate_seed_fixture(fixture),
        "reply_reference_count": sum(
            bool(item.get("reply_to_message_id")) for item in fixture["messages"]
        ),
        "membership_count": sum(
            len(item["member_codes"]) for item in fixture["rooms"]
        ),
        "pending_attachment_review_count": sum(
            (messages_by_fixture_id[item["message_id"]].extra_data or {}).get(
                "scenario_id"
            )
            == "SCN-ATTACHMENT-HANDOVER"
            for item in fixture["attachments"]
        ),
    }


def _repair_work_item_snapshots(db) -> dict:
    from app.models import (
        Message,
        MessageAttachment,
        Organization,
        Resident,
        Room,
        User,
        WorkItem,
    )

    organization = db.scalar(
        select(Organization).where(
            Organization.internal_code == TARGET_ORGANIZATION_CODE
        )
    )
    if organization is None:
        raise RuntimeError("발표 전용 합성기관을 찾을 수 없습니다.")

    candidates = db.scalars(
        select(WorkItem).where(
            WorkItem.organization_id == organization.id,
            WorkItem.is_test_data.is_(True),
        )
    ).all()
    repairable: list[tuple] = []
    for item in candidates:
        message = db.get(Message, item.source_message_id)
        if message is None:
            continue
        metadata = message.extra_data or {}
        if metadata.get("fixture_version") != FIXTURE_VERSION:
            continue
        repairable.append((item, message, metadata))

    if len(repairable) != 5:
        raise RuntimeError("복구 대상은 현재 fixture가 만든 기록 후보 5개여야 합니다.")

    for item, message, metadata in repairable:
        room = db.get(Room, message.room_id)
        sender = db.get(User, message.sender_id)
        resident = db.get(Resident, message.resident_id) if message.resident_id else None
        attachment_ids = db.scalars(
            select(MessageAttachment.id)
            .where(MessageAttachment.message_id == message.id)
            .order_by(MessageAttachment.created_at, MessageAttachment.id)
        ).all()
        if room is None or sender is None:
            raise RuntimeError("기록 후보의 발표 전용 원문 관계가 손상되었습니다.")
        item.source_snapshot = presentation_work_item_source_snapshot(
            message,
            room_name=room.name,
            sender_name=sender.display_name,
            resident_name=resident.display_name if resident else None,
            attachment_ids=list(attachment_ids),
            scenario_id=metadata.get("scenario_id") or "presentation-scenario",
        )

    db.commit()
    return {
        "status": "repaired-work-item-snapshots",
        "database_fingerprint": _target_fingerprint(),
        "fixture_version": FIXTURE_VERSION,
        "repaired_count": len(repairable),
        "official_record_write_count": 0,
        "external_transfer_count": 0,
    }


def main() -> None:
    from app.config import settings
    from app.database import SessionLocal

    args = _parse_args()
    validate_runtime_contract(settings)
    fixture = _load_fixture()
    plan = {
        "status": "check-only",
        "database_fingerprint": _target_fingerprint(),
        "docker_volume": TARGET_VOLUME,
        "database": TARGET_DATABASE,
        "schema": TARGET_SCHEMA,
        "origin": TARGET_ORIGIN,
        "display_notice": DISPLAY_NOTICE,
        **validate_seed_fixture(fixture),
    }
    with SessionLocal() as db:
        if args.repair_work_item_snapshots:
            result = _repair_work_item_snapshots(db)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return
        plan["target_counts"] = _assert_target_is_empty(db)
        if not args.apply:
            print(json.dumps(plan, ensure_ascii=False, sort_keys=True))
            return
        password = os.getenv("PRESENTATION_LOGIN_PASSWORD", "")
        if len(password) < 16:
            raise RuntimeError("발표 전용 로그인 비밀번호가 안전하게 생성되지 않았습니다.")
        result = _seed(db, fixture, password)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
