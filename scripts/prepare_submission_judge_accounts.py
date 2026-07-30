"""제출 시연용 가명 계정 3개의 비밀번호와 로그인 상태를 준비합니다.

기본 실행은 조회만 수행합니다. 실제 변경에는 확인문구가 필요합니다.
비밀번호는 명령 인자로만 받고 소스코드나 감사기록에 저장하지 않습니다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import delete, select

from submission_accounts import (
    DEFAULT_CREDENTIALS_PATH,
    load_submission_accounts,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    LoginAttempt,
    LoginSession,
    PushSubscription,
    Room,
    RoomMembership,
    User,
    utcnow,
)
from app.security import hash_password, verify_password  # noqa: E402
from app.services import record_audit  # noqa: E402


CONFIRMATION = "PREPARE_SUBMISSION_JUDGE_ACCOUNTS"


def build_targets(credentials_path: Path = DEFAULT_CREDENTIALS_PATH) -> dict:
    accounts = load_submission_accounts(credentials_path)
    return {
        accounts.care_a: {
            "role": "보고 작성자",
            "can_process_records": False,
            "rooms": {
                "나와의 대화",
                "전체 직원방",
                "시설 전체방",
                "3층방",
            },
        },
        accounts.care_b: {
            "role": "실시간 수신자",
            "can_process_records": False,
            "rooms": {
                "나와의 대화",
                "전체 직원방",
                "시설 전체방",
                "3층방",
            },
        },
        accounts.social: {
            "role": "업무함 검토자",
            "can_process_records": True,
            "rooms": {
                "나와의 대화",
                "전체 직원방",
                "시설 전체방",
                "영양·복지·의료방",
                "복지방",
            },
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SMCODI 채팅방 제출 시연용 가명 계정 준비"
    )
    parser.add_argument(
        "--password",
        help="세 가명 계정에 적용할 12자 이상의 제출 시연용 공통 비밀번호",
    )
    parser.add_argument(
        "--apply",
        metavar="CONFIRMATION",
        help=f"실제 변경 확인문구: {CONFIRMATION}",
    )
    parser.add_argument(
        "--credentials",
        type=Path,
        default=DEFAULT_CREDENTIALS_PATH,
        help="Git에서 제외된 제출 시연계정 파일",
    )
    return parser.parse_args()


def _active_room_names(db, user: User) -> set[str]:
    if user.staff_id is None:
        return set()
    return set(
        db.scalars(
            select(Room.name)
            .join(RoomMembership, RoomMembership.room_id == Room.id)
            .where(
                RoomMembership.staff_id == user.staff_id,
                RoomMembership.left_at.is_(None),
                Room.is_active.is_(True),
            )
        ).all()
    )


def _load_and_validate_targets(db, targets: dict) -> list[User]:
    users: list[User] = []
    for username, expected in targets.items():
        user = db.scalar(select(User).where(User.username == username))
        if user is None or user.staff is None:
            raise RuntimeError("역할에 연결된 가명 계정을 찾을 수 없습니다.")
        if not user.is_active or user.employment_status != "active":
            raise RuntimeError("역할에 연결된 가명 계정이 재직 활성 상태가 아닙니다.")
        if user.can_process_records is not expected["can_process_records"]:
            raise RuntimeError("역할에 연결된 가명 계정의 업무함 권한이 다릅니다.")
        actual_rooms = _active_room_names(db, user)
        if actual_rooms != expected["rooms"]:
            raise RuntimeError(
                f"{expected['role']} 역할의 방 배정이 예상과 다릅니다. "
                f"예상={sorted(expected['rooms'])}, 실제={sorted(actual_rooms)}"
            )
        users.append(user)
    return users


def main() -> int:
    args = parse_args()
    targets = build_targets(args.credentials)
    applying = args.apply is not None
    if settings.environment not in {"development", "test"}:
        raise SystemExit("제출용 가명 계정 준비는 development 또는 test에서만 허용됩니다.")
    if applying and args.apply != CONFIRMATION:
        raise SystemExit(f"실제 변경에는 --apply {CONFIRMATION} 문구가 필요합니다.")
    if applying and (args.password is None or len(args.password) < 12):
        raise SystemExit("실제 변경에는 12자 이상의 --password가 필요합니다.")

    with SessionLocal() as db:
        users = _load_and_validate_targets(db, targets)
        print("제출 시연용 계정 점검")
        for user in users:
            expected = targets[user.username]
            print(
                f"- {expected['role']}: {user.full_name} · "
                f"{', '.join(sorted(expected['rooms']))}"
            )

        if not applying:
            print(
                "\n조회만 완료했습니다. 실제 준비는 --password와 "
                f"--apply {CONFIRMATION}을 함께 사용하세요."
            )
            return 0

        admin_username = settings.bootstrap_admin_username
        if not admin_username:
            raise RuntimeError("환경변수에 관리자 계정명이 설정되지 않았습니다.")
        admin = db.scalar(select(User).where(User.username == admin_username))
        if admin is None or admin.role != "admin" or not admin.is_active:
            raise RuntimeError("감사기록을 남길 활성 관리자 계정을 찾을 수 없습니다.")

        now = utcnow()
        revoked_sessions = 0
        disabled_push = 0
        for user in users:
            user.password_hash = hash_password(args.password)
            user.must_change_password = False
            user.password_changed_at = now

            sessions = db.scalars(
                select(LoginSession).where(
                    LoginSession.user_id == user.id,
                    LoginSession.revoked_at.is_(None),
                )
            ).all()
            for login_session in sessions:
                login_session.revoked_at = now
            revoked_sessions += len(sessions)

            subscriptions = db.scalars(
                select(PushSubscription).where(
                    PushSubscription.user_id == user.id,
                    PushSubscription.is_active.is_(True),
                )
            ).all()
            for subscription in subscriptions:
                subscription.is_active = False
                subscription.disabled_at = now
            disabled_push += len(subscriptions)

            record_audit(
                db,
                actor_id=admin.id,
                action="submission.judge_account_prepared",
                target_type="user",
                target_id=user.id,
                details={
                    "username": user.username,
                    "role": targets[user.username]["role"],
                    "revoked_sessions": len(sessions),
                    "disabled_push_subscriptions": len(subscriptions),
                },
            )

        db.execute(
            delete(LoginAttempt).where(LoginAttempt.username.in_(targets))
        )
        db.commit()

        for user in users:
            db.refresh(user)
            if not verify_password(args.password, user.password_hash):
                raise RuntimeError("가명 계정의 비밀번호 저장 검증에 실패했습니다.")
        print(
            "\n준비 완료: "
            f"계정 {len(users)}개, 기존 세션 {revoked_sessions}개 종료, "
            f"기존 푸시 구독 {disabled_push}개 해제"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
