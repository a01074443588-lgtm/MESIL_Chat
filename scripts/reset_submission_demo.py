"""제출 시연용 채팅 자료를 안전하게 초기화하고 가명 업무대화를 생성합니다.

기본 실행은 조회 전용 모의 실행입니다. 실제 반영에는 아래 확인 문구가 필요합니다.

    python scripts/reset_submission_demo.py --apply RESET_SUBMISSION_DEMO

이 스크립트는 직원·조직·어르신·운영방·개인방·로그인 계정을 보존합니다.
기존 메시지와 메시지 파생자료를 비우고, 중지된 옛 채팅방만 삭제합니다.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select, update


def _configure_import_path() -> None:
    cwd = Path.cwd()
    candidates = (
        cwd / "backend",
        cwd,
        Path(__file__).resolve().parents[1] / "backend",
    )
    for candidate in candidates:
        if (candidate / "app").is_dir():
            sys.path.insert(0, str(candidate))
            return


_configure_import_path()

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    ActionItem,
    AuditEvent,
    Message,
    MessageAttachment,
    MessageComment,
    MessageReadReceipt,
    MessageResidentLink,
    OcrCorrectionMemory,
    Resident,
    Room,
    RoomDigest,
    RoomMembership,
    Staff,
    User,
    WorkItem,
)
from app.prototype_ai import (  # noqa: E402
    PROTOTYPE_GENERATOR,
    build_prototype_suggestion,
)
from submission_accounts import load_submission_accounts


CONFIRMATION = "RESET_SUBMISSION_DEMO"
EXPECTED_ACTIVE_ROOM_NAMES = {
    "전체 직원방",
    "시설 전체방",
    "주간보호방",
    "방문요양방",
    "2층방",
    "3층방",
    "4층방",
    "5층방",
    "영양·복지·의료방",
    "복지방",
}
EXPECTED_STOPPED_ROOM_COUNT = 34
EXPECTED_SELF_ROOM_COUNT = 77


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SMCODI 채팅 제출 시연자료 안전 초기화"
    )
    parser.add_argument(
        "--apply",
        metavar="CONFIRMATION",
        help=f"실제 반영 확인 문구: {CONFIRMATION}",
    )
    return parser.parse_args()


def _one_by_name(items: list[Any], name: str, label: str) -> Any:
    matches = [item for item in items if item.name == name]
    if len(matches) != 1:
        raise RuntimeError(f"{label} '{name}' 조회 결과가 {len(matches)}개입니다.")
    return matches[0]


def _one_user(users: dict[str, User], username: str) -> User:
    try:
        return users[username]
    except KeyError as exc:
        raise RuntimeError("필요한 가명 시험계정을 찾을 수 없습니다.") from exc


def _one_resident(residents: list[Resident], name: str) -> Resident:
    matches = [resident for resident in residents if resident.display_name == name]
    if len(matches) != 1:
        raise RuntimeError(f"가명 어르신 '{name}' 조회 결과가 {len(matches)}개입니다.")
    return matches[0]


def _snapshot(
    *,
    message: Message,
    room: Room,
    sender_name: str,
    resident: Resident,
) -> dict[str, Any]:
    return {
        "message_id": str(message.id),
        "room_id": str(room.id),
        "room_name": room.name,
        "sender_id": str(message.sender_id),
        "sender_name": sender_name,
        "resident_id": str(resident.id),
        "resident_name": resident.display_name,
        "resident_names": [resident.display_name],
        "body": message.body,
        "message_type": message.message_type,
        "attachment_ids": [],
        "created_at": message.created_at.isoformat(),
    }


def _validate_scope(db) -> dict[str, Any]:
    active_rooms = db.scalars(
        select(Room).where(Room.is_active.is_(True), Room.kind != "self")
    ).all()
    stopped_rooms = db.scalars(select(Room).where(Room.is_active.is_(False))).all()
    self_rooms = db.scalars(
        select(Room).where(Room.is_active.is_(True), Room.kind == "self")
    ).all()

    active_names = {room.name for room in active_rooms}
    if active_names != EXPECTED_ACTIVE_ROOM_NAMES:
        missing = sorted(EXPECTED_ACTIVE_ROOM_NAMES - active_names)
        unexpected = sorted(active_names - EXPECTED_ACTIVE_ROOM_NAMES)
        raise RuntimeError(
            "운영방 구성이 예상과 다릅니다. "
            f"누락={missing or '없음'}, 추가={unexpected or '없음'}"
        )
    if len(active_rooms) != len(EXPECTED_ACTIVE_ROOM_NAMES):
        raise RuntimeError("운영방 이름이 중복되어 있습니다.")
    if len(stopped_rooms) != EXPECTED_STOPPED_ROOM_COUNT:
        raise RuntimeError(
            "중지방 수가 예상과 다릅니다. "
            f"예상={EXPECTED_STOPPED_ROOM_COUNT}, 실제={len(stopped_rooms)}"
        )
    if len(self_rooms) != EXPECTED_SELF_ROOM_COUNT:
        raise RuntimeError(
            "개인방 수가 예상과 다릅니다. "
            f"예상={EXPECTED_SELF_ROOM_COUNT}, 실제={len(self_rooms)}"
        )

    return {
        "active_rooms": active_rooms,
        "stopped_rooms": stopped_rooms,
        "self_rooms": self_rooms,
        "messages": db.scalar(select(func.count()).select_from(Message)) or 0,
        "attachments": db.scalar(
            select(func.count()).select_from(MessageAttachment)
        )
        or 0,
        "work_items": db.scalar(select(func.count()).select_from(WorkItem)) or 0,
        "digests": db.scalar(select(func.count()).select_from(RoomDigest)) or 0,
    }


def _print_scope(scope: dict[str, Any], *, applying: bool) -> None:
    mode = "실제 반영" if applying else "모의 실행"
    print(f"[{mode}] 제출 시연자료 초기화")
    print(f"- 보존할 운영방: {len(scope['active_rooms'])}개")
    for room in sorted(scope["active_rooms"], key=lambda item: item.sort_order):
        print(f"  · {room.name}")
    print(f"- 보존할 개인방: {len(scope['self_rooms'])}개")
    print(f"- 완전 삭제할 중지방: {len(scope['stopped_rooms'])}개")
    print(f"- 비울 기존 메시지: {scope['messages']}건")
    print(f"- 비울 기존 첨부 메타데이터: {scope['attachments']}건")
    print(f"- 비울 기존 업무함 자료: {scope['work_items']}건")
    print(f"- 비울 기존 대화방 요약: {scope['digests']}건")


def _clear_chat_data(db, scope: dict[str, Any]) -> None:
    stopped_room_ids = [room.id for room in scope["stopped_rooms"]]
    old_message_ids = db.scalars(select(Message.id)).all()
    old_attachment_ids = db.scalars(select(MessageAttachment.id)).all()
    old_action_ids = db.scalars(select(ActionItem.id)).all()
    old_work_item_ids = db.scalars(select(WorkItem.id)).all()

    db.execute(update(RoomMembership).values(last_read_message_id=None))
    db.execute(delete(RoomDigest))
    db.execute(delete(Message))
    db.execute(delete(OcrCorrectionMemory))

    if stopped_room_ids:
        db.execute(delete(Room).where(Room.id.in_(stopped_room_ids)))

    derived_audit_targets: dict[str, list[UUID]] = {
        "message": old_message_ids,
        "attachment": old_attachment_ids,
        "action_item": old_action_ids,
        "work_item": old_work_item_ids,
        "room": stopped_room_ids,
    }
    for target_type, target_ids in derived_audit_targets.items():
        if target_ids:
            db.execute(
                delete(AuditEvent).where(
                    AuditEvent.target_type == target_type,
                    AuditEvent.target_id.in_(target_ids),
                )
            )
    db.flush()


def _seed_submission_conversations(
    db,
    active_rooms: list[Room],
) -> dict[str, int]:
    submission_accounts = load_submission_accounts()
    care_a = submission_accounts.care_a
    care_b = submission_accounts.care_b
    social = submission_accounts.social
    room_by_name = {room.name: room for room in active_rooms}
    users = {
        user.username: user
        for user in db.scalars(
            select(User).join(Staff, User.staff_id == Staff.id)
        ).all()
    }
    staff_names = {
        user.username: user.staff.display_name
        for user in users.values()
        if user.staff is not None
    }
    residents = db.scalars(
        select(Resident).where(Resident.is_active.is_(True))
    ).all()

    resident_by_case = {
        "fall": _one_resident(residents, "시설(가명)003"),
        "skin": _one_resident(residents, "시설(가명)001"),
        "meal": _one_resident(residents, "시설(가명)016"),
        "cognition": _one_resident(residents, "주간-어르신-01(가명)"),
        "consult": _one_resident(residents, "시설(가명)007"),
    }

    now = datetime.now(UTC)
    base = now - timedelta(hours=7)
    created_messages: list[Message] = []
    room_messages: dict[str, list[Message]] = defaultdict(list)
    work_item_cases: list[tuple[Message, Resident, str]] = []

    def add_message(
        room_name: str,
        username: str,
        body: str,
        *,
        minutes: int,
        message_type: str = "chat",
        resident: Resident | None = None,
        create_work_item: bool = False,
    ) -> Message:
        room = _one_by_name(active_rooms, room_name, "운영방")
        sender = _one_user(users, username)
        message = Message(
            organization_id=room.organization_id,
            room_id=room.id,
            sender_id=sender.id,
            message_type=message_type,
            body=body,
            resident_id=resident.id if resident else None,
            resident_ref=resident.display_name if resident else None,
            extra_data={"submission_demo": True},
            is_test_data=True,
            created_at=base + timedelta(minutes=minutes),
        )
        db.add(message)
        db.flush()
        created_messages.append(message)
        room_messages[room_name].append(message)
        if resident is not None:
            db.add(
                MessageResidentLink(
                    organization_id=room.organization_id,
                    message_id=message.id,
                    resident_id=resident.id,
                    source="manual",
                    status="confirmed",
                    reviewed_by_id=sender.id,
                    reviewed_at=message.created_at,
                    created_at=message.created_at,
                    updated_at=message.created_at,
                )
            )
        if create_work_item and resident is not None:
            work_item_cases.append((message, resident, username))
        return message

    admin_username = settings.bootstrap_admin_username
    if not admin_username:
        raise RuntimeError("환경변수에 관리자 계정명이 설정되지 않았습니다.")

    add_message(
        "전체 직원방",
        admin_username,
        "오늘 14시 소방설비 점검이 있습니다. 이동 시 통로를 비워 주세요.",
        minutes=0,
        message_type="notice",
    )
    add_message(
        "전체 직원방",
        "representative",
        "각 팀은 오전 인수인계 후 변경사항을 채팅방에 간단히 남겨 주세요.",
        minutes=8,
    )
    add_message(
        "시설 전체방",
        social,
        "오늘 보호자 면회는 15시부터 진행합니다. 생활실 일정 확인 부탁드립니다.",
        minutes=15,
    )

    fall = add_message(
        "3층방",
        care_a,
        (
            "시설(가명)003 어르신이 침상에서 일어나 화장실로 가시다가 "
            "균형을 잃어 주저앉으셨습니다. 머리는 부딪치지 않았고 오른쪽 "
            "무릎 통증을 말씀하셔서 움직임을 줄이고 간호 확인을 요청했습니다."
        ),
        minutes=28,
        resident=resident_by_case["fall"],
        create_work_item=True,
    )
    add_message(
        "3층방",
        care_b,
        "시설(가명)003 어르신 현재 의식 명료하고 무릎에 붓기나 출혈은 없습니다.",
        minutes=35,
        resident=resident_by_case["fall"],
    )
    add_message(
        "3층방",
        "fcare3_03",
        "이동 시 혼자 일어나지 않도록 설명드렸고 호출벨을 가까이 두었습니다.",
        minutes=42,
        resident=resident_by_case["fall"],
    )
    db.add(
        MessageComment(
            organization_id=fall.organization_id,
            message_id=fall.id,
            author_id=_one_user(users, care_b).id,
            body="활력징후 확인 후 이상 여부를 다시 댓글로 남기겠습니다.",
            is_test_data=True,
            created_at=fall.created_at + timedelta(minutes=12),
        )
    )
    db.add(
        ActionItem(
            organization_id=fall.organization_id,
            source_message_id=fall.id,
            action_type="confirmation",
            assignee_user_id=_one_user(users, "nurse").id,
            priority="urgent",
            status="assigned",
            due_at=fall.created_at + timedelta(hours=1),
            created_by_id=_one_user(users, care_a).id,
            is_test_data=True,
            created_at=fall.created_at + timedelta(minutes=1),
            updated_at=fall.created_at + timedelta(minutes=1),
        )
    )

    skin = add_message(
        "4층방",
        "care4",
        (
            "시설(가명)001 어르신 기저귀 교환 중 엉치 부위가 동전 크기로 "
            "붉게 보였습니다. 피부가 벗겨지거나 진물은 없고 체위변경 후 "
            "압박이 생기지 않도록 자세를 조정했습니다."
        ),
        minutes=58,
        resident=resident_by_case["skin"],
        create_work_item=True,
    )
    add_message(
        "4층방",
        "fcare4_02",
        "시설(가명)001 어르신 2시간 뒤 다시 확인했을 때 붉은 기는 조금 줄었습니다.",
        minutes=76,
        resident=resident_by_case["skin"],
    )
    db.add(
        MessageComment(
            organization_id=skin.organization_id,
            message_id=skin.id,
            author_id=_one_user(users, "fcare4_02").id,
            body="저녁 근무자에게 체위변경과 피부 상태 재확인을 인계했습니다.",
            is_test_data=True,
            created_at=skin.created_at + timedelta(minutes=20),
        )
    )

    meal = add_message(
        "2층방",
        "fcare2_01",
        (
            "시설(가명)016 어르신 점심을 평소의 절반 정도 드시고 물도 "
            "두 모금만 드셨습니다. 입안 통증은 없다고 하셨으며 좋아하시는 "
            "죽으로 바꾸어 다시 권해 드렸습니다."
        ),
        minutes=92,
        resident=resident_by_case["meal"],
        create_work_item=True,
    )
    add_message(
        "2층방",
        "fcare2_02",
        "시설(가명)016 어르신 오후 간식은 절반 드셨고 수분 150ml 섭취했습니다.",
        minutes=126,
        resident=resident_by_case["meal"],
    )
    db.add(
        MessageComment(
            organization_id=meal.organization_id,
            message_id=meal.id,
            author_id=_one_user(users, "fcare2_02").id,
            body="저녁 식사량도 확인해서 섭취량 변화가 계속되는지 보겠습니다.",
            is_test_data=True,
            created_at=meal.created_at + timedelta(minutes=36),
        )
    )

    cognition = add_message(
        "주간보호방",
        "dcare01",
        (
            "주간-어르신-01(가명) 어르신이 오전 프로그램 중 집에 가야 한다는 "
            "말씀을 반복하며 출입문 쪽으로 세 차례 이동하셨습니다. 달력과 "
            "귀가 시간을 함께 확인하고 익숙한 노래 활동으로 안내하니 안정을 찾으셨습니다."
        ),
        minutes=145,
        resident=resident_by_case["cognition"],
        create_work_item=True,
    )
    add_message(
        "주간보호방",
        "daysw",
        "보호자에게 오늘 반복 질문과 안정된 과정을 귀가 시 설명드리겠습니다.",
        minutes=156,
        resident=resident_by_case["cognition"],
    )
    add_message(
        "주간보호방",
        "daynurse",
        "주간-어르신-01(가명) 어르신 활력징후는 평소 범위이며 통증 호소는 없습니다.",
        minutes=167,
        resident=resident_by_case["cognition"],
    )
    db.add(
        MessageComment(
            organization_id=cognition.organization_id,
            message_id=cognition.id,
            author_id=_one_user(users, "daysw").id,
            body="최근 비슷한 행동이 있었는지 보호자 상담내용과 함께 확인하겠습니다.",
            is_test_data=True,
            created_at=cognition.created_at + timedelta(minutes=14),
        )
    )

    add_message(
        "5층방",
        "fcare5_01",
        "시설(가명)007 어르신 오전 보행 시 평소보다 발을 끄는 모습이 있어 옆에서 부축했습니다.",
        minutes=182,
        resident=resident_by_case["consult"],
    )
    add_message(
        "5층방",
        "fcare5_02",
        "시설(가명)007 어르신 점심 이후에는 평소 보행 모습으로 돌아왔습니다.",
        minutes=201,
        resident=resident_by_case["consult"],
    )

    add_message(
        "방문요양방",
        "hcare01",
        "오늘 오전 방문 일정은 정상 진행 중입니다. 현관 비밀번호 변경 가정은 담당 사회복지사에게 별도로 전달했습니다.",
        minutes=218,
    )
    add_message(
        "방문요양방",
        "homesw",
        "확인했습니다. 보호자와 통화 후 변경된 출입방법을 담당 요양보호사에게 다시 안내하겠습니다.",
        minutes=230,
    )

    add_message(
        "영양·복지·의료방",
        "nurse",
        "3층 낙상 의심 건은 간호 확인 후 활력징후와 관찰사항을 업무함에 정리하겠습니다.",
        minutes=246,
    )
    add_message(
        "영양·복지·의료방",
        "dietitian",
        "2층 식사량 감소 어르신은 저녁 섭취량까지 확인되면 대체식 제공 여부를 검토하겠습니다.",
        minutes=258,
    )
    add_message(
        "영양·복지·의료방",
        "therapist",
        "5층 보행 변화 어르신은 다음 작업치료 시간에 하지 움직임을 함께 확인하겠습니다.",
        minutes=271,
    )

    consult = add_message(
        "복지방",
        social,
        (
            "시설(가명)007 어르신 보호자와 통화했습니다. 최근 보행 상태와 "
            "낮 시간 활동을 설명드렸고, 변화가 반복되면 간호 확인 결과를 "
            "함께 안내하기로 했습니다."
        ),
        minutes=288,
        resident=resident_by_case["consult"],
    )
    add_message(
        "복지방",
        "fsw02",
        "상담내용 확인했습니다. 다음 급여제공계획 점검 때 이동지원 내용을 함께 보겠습니다.",
        minutes=300,
        resident=resident_by_case["consult"],
    )

    db.flush()

    for message, resident, username in work_item_cases:
        room = room_by_name[next(
            name for name, messages in room_messages.items() if message in messages
        )]
        snapshot = _snapshot(
            message=message,
            room=room,
            sender_name=staff_names[username],
            resident=resident,
        )
        db.add(
            WorkItem(
                organization_id=message.organization_id,
                source_message_id=message.id,
                resident_id=resident.id,
                status="pending",
                source_snapshot=snapshot,
                document_types=[],
                ai_state="not_requested",
                is_test_data=True,
                created_at=message.created_at,
                updated_at=message.created_at,
            )
        )

    consult_room = room_by_name["복지방"]
    consult_snapshot = _snapshot(
        message=consult,
        room=consult_room,
        sender_name=staff_names[social],
        resident=resident_by_case["consult"],
    )
    consult_draft = build_prototype_suggestion(consult_snapshot)
    db.add(
        WorkItem(
            organization_id=consult.organization_id,
            source_message_id=consult.id,
            resident_id=resident_by_case["consult"].id,
            status="ready",
            source_snapshot=consult_snapshot,
            document_types=consult_draft["document_types"],
            processing_notes="보호자 상담 경과와 추후 확인사항을 점검함.",
            handled_by_id=_one_user(users, social).id,
            ai_state="prototype_suggested",
            ai_payload=consult_draft,
            ai_generator=PROTOTYPE_GENERATOR,
            ai_generated_at=consult.created_at + timedelta(minutes=5),
            confirmed_payload=consult_draft,
            confirmed_by_id=_one_user(users, social).id,
            confirmed_at=consult.created_at + timedelta(minutes=10),
            is_test_data=True,
            created_at=consult.created_at,
            updated_at=consult.created_at + timedelta(minutes=10),
        )
    )

    for room_name, messages in room_messages.items():
        room = room_by_name[room_name]
        period_start = min(message.created_at for message in messages).replace(
            minute=0, second=0, microsecond=0
        )
        period_end = max(message.created_at for message in messages) + timedelta(minutes=1)
        linked_resident_ids = {
            message.resident_id for message in messages if message.resident_id is not None
        }
        room_comment_count = db.scalar(
            select(func.count())
            .select_from(MessageComment)
            .where(MessageComment.message_id.in_([message.id for message in messages]))
        ) or 0
        summary = {
            "전체 직원방": "시설 점검 공지와 팀별 변경사항 기록 안내가 공유되었습니다.",
            "시설 전체방": "보호자 면회 일정이 시설 직원에게 공유되었습니다.",
            "3층방": "낙상 의심 상황의 초기 관찰, 간호 확인 요청, 안전조치가 기록되었습니다.",
            "4층방": "피부 발적 관찰과 체위변경, 재확인 결과가 공유되었습니다.",
            "2층방": "식사·수분 섭취 감소와 대체식 제공 후 경과가 기록되었습니다.",
            "주간보호방": "반복 귀가 요구에 대한 안정지원과 보호자 안내 계획이 공유되었습니다.",
            "5층방": "일시적인 보행 변화와 이후 회복 상태가 인계되었습니다.",
            "방문요양방": "방문 일정과 변경된 출입방법 확인 업무가 공유되었습니다.",
            "영양·복지·의료방": "간호·영양·재활 분야의 후속 확인 계획이 조율되었습니다.",
            "복지방": "보호자 상담과 급여제공계획 점검 연결사항이 기록되었습니다.",
        }[room_name]
        document_counts = Counter()
        risk_counts = Counter()
        for message, resident, _ in work_item_cases:
            if message.room_id != room.id:
                continue
            draft = build_prototype_suggestion(
                _snapshot(
                    message=message,
                    room=room,
                    sender_name=staff_names[
                        next(
                            username
                            for source_message, _, username in work_item_cases
                            if source_message.id == message.id
                        )
                    ],
                    resident=resident,
                )
            )
            document_counts.update(draft["document_types"])
            risk_counts.update([draft["risk_level"]])
        db.add(
            RoomDigest(
                organization_id=room.organization_id,
                room_id=room.id,
                period_start=period_start,
                period_end=period_end,
                message_count=len(messages),
                comment_count=int(room_comment_count),
                resident_count=len(linked_resident_ids),
                summary=summary,
                major_points=[
                    {
                        "text": messages[-1].body[:180],
                        "message_id": str(messages[-1].id),
                    }
                ],
                document_counts=dict(document_counts),
                risk_counts=dict(risk_counts),
                source_message_ids=[str(message.id) for message in messages],
                generator="submission-demo-seed-v1",
                generated_at=now,
                created_at=now,
                updated_at=now,
            )
        )

    receipt_pairs: set[tuple[UUID, UUID]] = set()
    latest_read_by_room_user: dict[tuple[UUID, UUID], Message] = {}
    for room_name, messages in room_messages.items():
        room = room_by_name[room_name]
        member_user_ids = db.scalars(
            select(User.id)
            .join(Staff, User.staff_id == Staff.id)
            .join(RoomMembership, RoomMembership.staff_id == Staff.id)
            .where(
                RoomMembership.room_id == room.id,
                RoomMembership.left_at.is_(None),
            )
            .order_by(User.username)
            .limit(3)
        ).all()
        for message in messages:
            reader_ids = list(dict.fromkeys([message.sender_id, *member_user_ids]))
            for reader_id in reader_ids:
                pair = (message.id, reader_id)
                if pair in receipt_pairs:
                    continue
                receipt_pairs.add(pair)
                db.add(
                    MessageReadReceipt(
                        organization_id=message.organization_id,
                        message_id=message.id,
                        user_id=reader_id,
                        read_at=message.created_at + timedelta(minutes=3),
                        is_test_data=True,
                    )
                )
                latest_read_by_room_user[(room.id, reader_id)] = message

    for (room_id, user_id), message in latest_read_by_room_user.items():
        staff_id = db.scalar(select(User.staff_id).where(User.id == user_id))
        if staff_id is None:
            continue
        membership = db.scalar(
            select(RoomMembership).where(
                RoomMembership.room_id == room_id,
                RoomMembership.staff_id == staff_id,
                RoomMembership.left_at.is_(None),
            )
        )
        if membership is not None:
            membership.last_read_message_id = message.id
            membership.last_read_at = message.created_at + timedelta(minutes=3)

    db.flush()
    return {
        "messages": len(created_messages),
        "work_items_pending": len(work_item_cases),
        "work_items_confirmed": 1,
        "comments": 4,
        "action_items": 1,
        "digests": len(room_messages),
    }


def _verify_after(db, seeded: dict[str, int]) -> None:
    room_count = db.scalar(select(func.count()).select_from(Room)) or 0
    stopped_count = (
        db.scalar(
            select(func.count()).select_from(Room).where(Room.is_active.is_(False))
        )
        or 0
    )
    active_non_self_count = (
        db.scalar(
            select(func.count())
            .select_from(Room)
            .where(Room.is_active.is_(True), Room.kind != "self")
        )
        or 0
    )
    self_count = (
        db.scalar(
            select(func.count())
            .select_from(Room)
            .where(Room.is_active.is_(True), Room.kind == "self")
        )
        or 0
    )
    message_count = db.scalar(select(func.count()).select_from(Message)) or 0
    pending_work_items = (
        db.scalar(
            select(func.count())
            .select_from(WorkItem)
            .where(WorkItem.status == "pending")
        )
        or 0
    )
    confirmed_work_items = (
        db.scalar(
            select(func.count())
            .select_from(WorkItem)
            .where(WorkItem.confirmed_at.is_not(None))
        )
        or 0
    )
    digest_count = db.scalar(select(func.count()).select_from(RoomDigest)) or 0

    expected_room_count = EXPECTED_SELF_ROOM_COUNT + len(EXPECTED_ACTIVE_ROOM_NAMES)
    checks = {
        "전체 방": (room_count, expected_room_count),
        "중지방": (stopped_count, 0),
        "운영방": (active_non_self_count, len(EXPECTED_ACTIVE_ROOM_NAMES)),
        "개인방": (self_count, EXPECTED_SELF_ROOM_COUNT),
        "시연 메시지": (message_count, seeded["messages"]),
        "AI 검증 대기": (pending_work_items, seeded["work_items_pending"]),
        "담당자 확인 완료": (
            confirmed_work_items,
            seeded["work_items_confirmed"],
        ),
        "대화방 요약": (digest_count, seeded["digests"]),
    }
    failures = [
        f"{label}: 실제={actual}, 예상={expected}"
        for label, (actual, expected) in checks.items()
        if actual != expected
    ]
    if failures:
        raise RuntimeError("반영 후 검증 실패: " + "; ".join(failures))

    print("[반영 후 검증]")
    for label, (actual, _) in checks.items():
        print(f"- {label}: {actual}")


def main() -> int:
    args = parse_args()
    applying = args.apply is not None
    if applying and args.apply != CONFIRMATION:
        print(f"확인 문구가 올바르지 않습니다. 필요한 값: {CONFIRMATION}")
        return 2

    with SessionLocal() as db:
        scope = _validate_scope(db)
        _print_scope(scope, applying=applying)
        if not applying:
            print("\n모의 실행 완료: 데이터는 변경되지 않았습니다.")
            return 0

        try:
            _clear_chat_data(db, scope)
            seeded = _seed_submission_conversations(db, scope["active_rooms"])
            _verify_after(db, seeded)
            db.commit()
        except Exception:
            db.rollback()
            raise

    print("\n제출 시연자료 초기화와 가명 업무대화 생성이 완료되었습니다.")
    print("물리 첨부파일은 별도 백업 확인 후 data/uploads에서 정리해 주세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
