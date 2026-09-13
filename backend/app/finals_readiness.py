from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import asdict, dataclass
from typing import Any, Literal
from urllib.parse import urlparse

from sqlalchemy import distinct, func, or_, select
from sqlalchemy.orm import Session

from .config import settings
from .database import SessionLocal
from .models import (
    AuditEvent,
    Message,
    MessageResidentLink,
    Resident,
    Room,
    RoomMembership,
    Staff,
    User,
)


ReadinessStatus = Literal["PASS", "WARN", "FAIL"]


SENSITIVE_AUDIT_KEYS = {
    "body",
    "content",
    "display_name",
    "employee_name",
    "extracted_text",
    "full_name",
    "message_body",
    "original_name",
    "recipient_name",
    "resident_name",
    "resident_ref",
    "reviewed_text",
    "staff_name",
    "username",
}


@dataclass(frozen=True)
class FinalsReadinessSnapshot:
    environment: str
    reviewer_access_enabled: bool
    ai_review_external_enabled: bool
    allow_external_real_data: bool
    allow_external_real_image_data: bool
    ocr_endpoint_scope: str
    stt_enabled: bool
    stt_endpoint_scope: str
    ai_review_endpoint_scope: str
    real_staff_count: int
    real_resident_count: int
    real_room_count: int
    real_message_count: int
    test_audit_count: int
    sensitive_audit_event_count: int
    active_test_processor_count: int
    active_test_processor_test_room_link_count: int
    active_test_processor_real_room_link_count: int
    presentation_message_count: int
    presentation_non_test_message_count: int
    presentation_real_author_message_count: int
    presentation_non_test_resident_count: int


@dataclass(frozen=True)
class ReadinessFinding:
    code: str
    status: ReadinessStatus
    title: str
    detail: str


@dataclass(frozen=True)
class FinalsReadinessReport:
    overall: ReadinessStatus
    findings: list[ReadinessFinding]
    snapshot: FinalsReadinessSnapshot


def endpoint_scope(value: str) -> str:
    """Classify an AI endpoint without returning credentials or full URLs."""

    try:
        parsed = urlparse(value)
    except ValueError:
        return "invalid"
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "invalid"

    hostname = parsed.hostname.lower().rstrip(".")
    if hostname in {"localhost", "host.docker.internal"} or hostname.endswith(
        ".local"
    ):
        return "local"
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        # Docker Compose service names and an on-premise host name normally have
        # no public DNS suffix. They stay inside the facility network contract.
        return "internal" if "." not in hostname else "external"
    return "local" if (address.is_loopback or address.is_private) else "external"


def _contains_sensitive_audit_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).strip().lower() in SENSITIVE_AUDIT_KEYS:
                return True
            if _contains_sensitive_audit_key(child):
                return True
    elif isinstance(value, list):
        return any(_contains_sensitive_audit_key(child) for child in value)
    return False


def _count(db: Session, model: type[Any], *conditions: Any) -> int:
    statement = select(func.count()).select_from(model)
    if conditions:
        statement = statement.where(*conditions)
    return int(db.scalar(statement) or 0)


def collect_snapshot(db: Session) -> FinalsReadinessSnapshot:
    test_processor_filter = (
        User.is_active.is_(True),
        User.can_process_records.is_(True),
        Staff.is_active.is_(True),
        Staff.employment_status == "active",
        Staff.is_test_data.is_(True),
    )
    active_test_processor_count = int(
        db.scalar(
            select(func.count())
            .select_from(User)
            .join(Staff, User.staff_id == Staff.id)
            .where(*test_processor_filter)
        )
        or 0
    )

    active_test_processor_test_room_link_count = int(
        db.scalar(
            select(func.count())
            .select_from(User)
            .join(Staff, User.staff_id == Staff.id)
            .join(RoomMembership, RoomMembership.staff_id == Staff.id)
            .join(Room, RoomMembership.room_id == Room.id)
            .where(
                *test_processor_filter,
                RoomMembership.left_at.is_(None),
                Room.is_active.is_(True),
                Room.is_test_data.is_(True),
            )
        )
        or 0
    )
    active_test_processor_real_room_link_count = int(
        db.scalar(
            select(func.count())
            .select_from(User)
            .join(Staff, User.staff_id == Staff.id)
            .join(RoomMembership, RoomMembership.staff_id == Staff.id)
            .join(Room, RoomMembership.room_id == Room.id)
            .where(
                *test_processor_filter,
                RoomMembership.left_at.is_(None),
                Room.is_active.is_(True),
                Room.is_test_data.is_(False),
            )
        )
        or 0
    )

    presentation_room_ids = list(
        db.scalars(
            select(distinct(Room.id))
            .select_from(User)
            .join(Staff, User.staff_id == Staff.id)
            .join(RoomMembership, RoomMembership.staff_id == Staff.id)
            .join(Room, RoomMembership.room_id == Room.id)
            .where(
                *test_processor_filter,
                RoomMembership.left_at.is_(None),
                Room.is_active.is_(True),
                Room.is_test_data.is_(True),
            )
        ).all()
    )

    presentation_message_ids: list[Any] = []
    presentation_message_count = 0
    presentation_non_test_message_count = 0
    presentation_real_author_message_count = 0
    presentation_non_test_resident_count = 0
    if presentation_room_ids:
        presentation_message_ids = list(
            db.scalars(
                select(Message.id).where(Message.room_id.in_(presentation_room_ids))
            ).all()
        )
        presentation_message_count = len(presentation_message_ids)
        presentation_non_test_message_count = _count(
            db,
            Message,
            Message.room_id.in_(presentation_room_ids),
            Message.is_test_data.is_(False),
        )
        presentation_real_author_message_count = int(
            db.scalar(
                select(func.count())
                .select_from(Message)
                .join(User, Message.sender_id == User.id)
                .outerjoin(Staff, User.staff_id == Staff.id)
                .where(
                    Message.room_id.in_(presentation_room_ids),
                    or_(Staff.id.is_(None), Staff.is_test_data.is_(False)),
                )
            )
            or 0
        )
        direct_real_resident_count = int(
            db.scalar(
                select(func.count(distinct(Resident.id)))
                .select_from(Message)
                .join(Resident, Message.resident_id == Resident.id)
                .where(
                    Message.room_id.in_(presentation_room_ids),
                    Resident.is_test_data.is_(False),
                )
            )
            or 0
        )
        linked_real_resident_count = int(
            db.scalar(
                select(func.count(distinct(Resident.id)))
                .select_from(MessageResidentLink)
                .join(Message, MessageResidentLink.message_id == Message.id)
                .join(Resident, MessageResidentLink.resident_id == Resident.id)
                .where(
                    Message.room_id.in_(presentation_room_ids),
                    Resident.is_test_data.is_(False),
                )
            )
            or 0
        )
        presentation_non_test_resident_count = (
            direct_real_resident_count + linked_real_resident_count
        )

    sensitive_audit_event_count = 0
    for is_test_data, before_data, details in db.execute(
        select(
            AuditEvent.is_test_data,
            AuditEvent.before_data,
            AuditEvent.details,
        )
    ):
        # 본선용 가명자료 자체의 가명 이름과 계정은 감사 이력에 남을 수
        # 있다. 이 검사는 실제 데이터로 표시된 감사 이벤트에 원문·이름
        # 필드가 섞였는지만 경고해야 하며, 값은 어떤 경우에도 출력하지
        # 않는다.
        if not is_test_data and (
            _contains_sensitive_audit_key(before_data)
            or _contains_sensitive_audit_key(details)
        ):
            sensitive_audit_event_count += 1

    return FinalsReadinessSnapshot(
        environment=settings.environment,
        reviewer_access_enabled=settings.reviewer_access_enabled,
        ai_review_external_enabled=settings.ai_review_external_enabled,
        allow_external_real_data=os.getenv(
            "AI_ASSIST_ALLOW_EXTERNAL_REAL_DATA", "false"
        ).strip().lower()
        == "true",
        allow_external_real_image_data=(
            settings.ai_assist_allow_external_real_image_data
        ),
        ocr_endpoint_scope=endpoint_scope(settings.ocr_base_url),
        stt_enabled=settings.stt_enabled,
        stt_endpoint_scope=endpoint_scope(settings.stt_service_url),
        ai_review_endpoint_scope=endpoint_scope(settings.ai_review_base_url),
        real_staff_count=_count(db, Staff, Staff.is_test_data.is_(False)),
        real_resident_count=_count(db, Resident, Resident.is_test_data.is_(False)),
        real_room_count=_count(db, Room, Room.is_test_data.is_(False)),
        real_message_count=_count(db, Message, Message.is_test_data.is_(False)),
        test_audit_count=_count(db, AuditEvent, AuditEvent.is_test_data.is_(True)),
        sensitive_audit_event_count=sensitive_audit_event_count,
        active_test_processor_count=active_test_processor_count,
        active_test_processor_test_room_link_count=(
            active_test_processor_test_room_link_count
        ),
        active_test_processor_real_room_link_count=(
            active_test_processor_real_room_link_count
        ),
        presentation_message_count=presentation_message_count,
        presentation_non_test_message_count=presentation_non_test_message_count,
        presentation_real_author_message_count=(
            presentation_real_author_message_count
        ),
        presentation_non_test_resident_count=(
            presentation_non_test_resident_count
        ),
    )


def evaluate_snapshot(snapshot: FinalsReadinessSnapshot) -> FinalsReadinessReport:
    findings: list[ReadinessFinding] = []

    def add(
        code: str,
        status: ReadinessStatus,
        title: str,
        detail: str,
    ) -> None:
        findings.append(ReadinessFinding(code, status, title, detail))

    add(
        "FINALS_ENVIRONMENT",
        "PASS" if snapshot.environment in {"development", "test"} else "WARN",
        "검증 환경 분리",
        f"현재 환경 유형: {snapshot.environment}",
    )
    add(
        "PRELIMINARY_REVIEWER_DISABLED",
        "FAIL" if snapshot.reviewer_access_enabled else "PASS",
        "예선 심사위원 자동로그인 비활성화",
        "본선 일반 로그인 흐름과 예선 전용 진입 경로를 분리합니다.",
    )
    external_real_data_enabled = (
        snapshot.ai_review_external_enabled
        or snapshot.allow_external_real_data
        or snapshot.allow_external_real_image_data
    )
    add(
        "EXTERNAL_REAL_DATA_BLOCKED",
        "FAIL" if external_real_data_enabled else "PASS",
        "실제 돌봄자료 외부 AI 전송 차단",
        "외부 AI, 실제 대화, 실제 이미지 허용값이 모두 꺼져 있어야 합니다.",
    )

    endpoint_problems = [
        name
        for name, scope, enabled in (
            ("OCR", snapshot.ocr_endpoint_scope, True),
            ("음성 받아쓰기", snapshot.stt_endpoint_scope, snapshot.stt_enabled),
            ("AI 검토", snapshot.ai_review_endpoint_scope, True),
        )
        if enabled and scope not in {"local", "internal"}
    ]
    add(
        "LOCAL_AI_ENDPOINTS",
        "FAIL" if endpoint_problems else "PASS",
        "시설 내부 AI 연결",
        (
            "외부 또는 잘못된 연결: " + ", ".join(endpoint_problems)
            if endpoint_problems
            else "사용 중인 OCR·음성·AI 연결은 로컬 또는 내부망 범위입니다."
        ),
    )

    real_counts = (
        snapshot.real_staff_count,
        snapshot.real_resident_count,
        snapshot.real_room_count,
        snapshot.real_message_count,
    )
    add(
        "ISOLATED_PSEUDONYM_DATASET",
        "FAIL" if any(real_counts) else "PASS",
        "본선용 가상 데이터 DB 분리",
        (
            "실제 표시 행 수(직원/어르신/방/메시지): "
            + "/".join(str(value) for value in real_counts)
        ),
    )
    has_presentation_access = (
        snapshot.active_test_processor_count > 0
        and snapshot.active_test_processor_test_room_link_count > 0
    )
    add(
        "NORMAL_FINALS_ACCOUNT_READY",
        "PASS" if has_presentation_access else "FAIL",
        "본선용 일반 사회복지사 계정",
        (
            "활성 가상 기록처리 계정/가상 방 연결: "
            f"{snapshot.active_test_processor_count}/"
            f"{snapshot.active_test_processor_test_room_link_count}"
        ),
    )
    add(
        "PRESENTATION_ROOM_BOUNDARY",
        "FAIL" if snapshot.active_test_processor_real_room_link_count else "PASS",
        "본선 계정의 실제 방 접근 차단",
        f"실제 방 연결 수: {snapshot.active_test_processor_real_room_link_count}",
    )
    presentation_content_issues = (
        snapshot.presentation_non_test_message_count
        + snapshot.presentation_real_author_message_count
        + snapshot.presentation_non_test_resident_count
    )
    add(
        "PRESENTATION_CONTENT_BOUNDARY",
        "FAIL" if presentation_content_issues else "PASS",
        "본선 화면의 가상 콘텐츠 경계",
        (
            "전체 메시지/실제 메시지/실제 작성자 메시지/실제 어르신 연결: "
            f"{snapshot.presentation_message_count}/"
            f"{snapshot.presentation_non_test_message_count}/"
            f"{snapshot.presentation_real_author_message_count}/"
            f"{snapshot.presentation_non_test_resident_count}"
        ),
    )
    add(
        "AUDIT_TRAIL_PRESENT",
        "PASS" if snapshot.test_audit_count > 0 else "WARN",
        "가상 데이터 감사 이력",
        f"가상 감사 이벤트 수: {snapshot.test_audit_count}",
    )
    add(
        "AUDIT_DETAIL_MINIMIZED",
        "FAIL" if snapshot.sensitive_audit_event_count else "PASS",
        "감사 이력 식별정보 최소화",
        (
            "민감한 필드명이 포함된 감사 이벤트 수: "
            f"{snapshot.sensitive_audit_event_count}"
        ),
    )

    statuses = {finding.status for finding in findings}
    overall: ReadinessStatus = (
        "FAIL" if "FAIL" in statuses else "WARN" if "WARN" in statuses else "PASS"
    )
    return FinalsReadinessReport(overall, findings, snapshot)


def report_as_dict(report: FinalsReadinessReport) -> dict[str, Any]:
    return {
        "overall": report.overall,
        "findings": [asdict(finding) for finding in report.findings],
        "snapshot": asdict(report.snapshot),
    }


def _print_human(report: FinalsReadinessReport) -> None:
    print(f"MESIL_Chat 본선 개인정보·현장 준비도: {report.overall}")
    for finding in report.findings:
        print(
            f"[{finding.status}] {finding.code} | {finding.title} | {finding.detail}"
        )
    print("※ 이름·인증값·원문·파일명은 이 보고서에 출력하지 않습니다.")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="MESIL_Chat 본선판의 개인정보·가상 데이터 경계를 읽기 전용으로 점검합니다."
    )
    parser.add_argument("--json", action="store_true", help="기계 판독용 JSON 출력")
    args = parser.parse_args()

    with SessionLocal() as db:
        report = evaluate_snapshot(collect_snapshot(db))
    if args.json:
        print(json.dumps(report_as_dict(report), ensure_ascii=False, sort_keys=True))
    else:
        _print_human(report)
    return 1 if report.overall == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
