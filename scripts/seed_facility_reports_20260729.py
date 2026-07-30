"""2026-07-29 시설 가명 어르신 보고를 기존 채팅 흐름으로 추가합니다.

이미지나 기존 메시지는 건드리지 않습니다. 로컬 개발자 런처로 제출용 가상
직원 화면을 전환해 실제 채팅 API를 호출하며, 첫 보고가 이미 있으면 중복
등록을 중단합니다.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

from seed_daycare_conversations_20260729 import (
    LauncherApi,
    load_env,
    post_comment,
    post_message,
    require_ok,
)
from submission_accounts import load_submission_accounts


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIRMATION = "SEED_FACILITY_REPORTS_20260729"
SUBMISSION_ACCOUNTS = load_submission_accounts()
CARE_A = SUBMISSION_ACCOUNTS.care_a
CARE_B = SUBMISSION_ACCOUNTS.care_b
SOCIAL = SUBMISSION_ACCOUNTS.social
FIRST_REPORT = (
    "시설(가명)003 어르신이 아침 식사를 평소의 절반 정도 드셨습니다. "
    "물 100ml를 드렸고 입안 통증이나 삼킴 불편은 없다고 하셨습니다. "
    "점심과 저녁 섭취량을 이어서 확인하겠습니다."
)
REQUIRED_USERS = SUBMISSION_ACCOUNTS.usernames
REQUIRED_RESIDENTS = (
    "시설(가명)003",
    "시설(가명)012",
    "시설(가명)017",
    "시설(가명)031",
    "시설(가명)041",
    "시설(가명)045",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="시설 전체방에 오늘자 가명 어르신 관련 보고를 등록합니다."
    )
    parser.add_argument(
        "--apply",
        metavar="CONFIRMATION",
        help=f"실제 등록 확인 문구: {CONFIRMATION}",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8080",
        help="로컬 게이트웨이 주소",
    )
    parser.add_argument(
        "--evidence",
        type=Path,
        default=PROJECT_ROOT
        / "output"
        / "validation"
        / "facility_reports_20260729.json",
        help="등록·검증 결과 JSON 경로",
    )
    return parser.parse_args()


def find_context(
    api: LauncherApi,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    def inspect() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        rooms = require_ok(api.client.get("/api/rooms"), "채팅방 조회")
        assert isinstance(rooms, list)
        room = next((item for item in rooms if item["name"] == "시설 전체방"), None)
        if room is None:
            raise RuntimeError("시설 전체방을 찾을 수 없습니다.")
        residents = require_ok(
            api.client.get(f"/api/rooms/{room['id']}/residents"),
            "시설 어르신 조회",
        )
        assert isinstance(residents, list)
        resident_map = {
            str(item["display_name"]): item
            for item in residents
            if item.get("service_type") == "facility"
        }
        missing = sorted(set(REQUIRED_RESIDENTS) - set(resident_map))
        if missing:
            raise RuntimeError(
                "필요한 시설 가명 어르신이 없습니다: " + ", ".join(missing)
            )
        return room, resident_map

    return api.as_user(CARE_A, inspect)


def ensure_not_seeded(api: LauncherApi, room_id: str) -> None:
    def inspect() -> None:
        messages = require_ok(
            api.client.get(f"/api/rooms/{room_id}/messages", params={"limit": 100}),
            "기존 메시지 확인",
        )
        assert isinstance(messages, list)
        if any(message.get("body") == FIRST_REPORT for message in messages):
            raise RuntimeError(
                "오늘자 시설 가명 보고가 이미 등록되어 중복 등록을 중단합니다."
            )

    api.as_user(CARE_A, inspect)


def collect_period_review(api: LauncherApi, room_id: str) -> dict[str, Any]:
    def inspect() -> dict[str, Any]:
        today = date.today().isoformat()
        result = require_ok(
            api.client.post(
                "/api/workdesk/period-review",
                json={
                    "start_date": today,
                    "end_date": today,
                    "room_id": room_id,
                    "enhance_summary": False,
                },
            ),
            "오늘 돌봄 브리핑 생성",
        )
        assert isinstance(result, dict)
        return result

    return api.as_user(SOCIAL, inspect)


def main() -> int:
    args = parse_args()
    applying = args.apply is not None
    if applying and args.apply != CONFIRMATION:
        print(f"확인 문구가 올바르지 않습니다. 필요한 값: {CONFIRMATION}")
        return 2

    env = load_env(PROJECT_ROOT / ".env")
    launcher_username = env.get("DEV_LAUNCHER_USERNAME")
    launcher_password = env.get("DEV_LAUNCHER_PASSWORD")
    if not launcher_username or not launcher_password:
        raise RuntimeError(".env의 개발자 런처 설정을 확인해 주세요.")

    api = LauncherApi(
        args.base_url,
        username=launcher_username,
        password=launcher_password,
    )
    try:
        missing_users = sorted(set(REQUIRED_USERS) - set(api.users))
        if missing_users:
            raise RuntimeError(
                f"필요한 가상 직원 계정 {len(missing_users)}개를 찾지 못했습니다."
            )
        room, residents = find_context(api)
        ensure_not_seeded(api, str(room["id"]))

        print(f"대상 채팅방: {room['name']}")
        print(f"가상 직원 역할: {len(REQUIRED_USERS)}명 확인")
        print("가명 어르신: " + ", ".join(REQUIRED_RESIDENTS))
        print("등록 예정: 어르신 관련 보고 10건, 답글 4건, 첨부파일 0건")
        if not applying:
            print("모의 실행 완료: 실제 대화는 등록하지 않았습니다.")
            return 0

        room_id = str(room["id"])
        resident_ids = {
            name: str(residents[name]["id"]) for name in REQUIRED_RESIDENTS
        }
        specs = [
            ("meal_first", CARE_A, "report", "시설(가명)003", FIRST_REPORT),
            (
                "gait",
                CARE_B,
                "report",
                "시설(가명)012",
                "시설(가명)012 어르신이 오전 9시 20분 화장실로 이동하시다가 "
                "잠시 비틀거리셔서 옆에서 부축했습니다. 넘어지지는 않았고 통증도 "
                "없다고 하셨습니다. 의자에서 쉬도록 돕고 간호팀에 전달했습니다.",
            ),
            (
                "skin",
                CARE_A,
                "report",
                "시설(가명)017",
                "시설(가명)017 어르신 기저귀 교환 중 엉치 부위가 동전 크기로 "
                "붉게 보였습니다. 피부 벗겨짐이나 진물은 없었습니다. 체위를 "
                "변경하고 압박이 생기지 않도록 한 뒤 간호팀에 확인을 요청했습니다.",
            ),
            (
                "cognition",
                CARE_B,
                "report",
                "시설(가명)031",
                "시설(가명)031 어르신이 오후에 집에 가야 한다는 말씀을 반복하며 "
                "옷과 가방을 찾으셨습니다. 귀가 시간이 아니라는 점을 차분히 "
                "설명하고 옆에서 대화를 나누자 잠시 안정을 찾으셨습니다.",
            ),
            (
                "guardian",
                SOCIAL,
                "report",
                "시설(가명)041",
                "시설(가명)041 어르신 보호자께서 최근 밤에 잠을 잘 주무시는지 "
                "문의하셨습니다. 야간 관찰내용을 확인한 뒤 오늘 오후 다시 "
                "연락드리기로 했습니다.",
            ),
            (
                "toileting",
                CARE_A,
                "report",
                "시설(가명)045",
                "시설(가명)045 어르신이 점심 식사 후 화장실을 두 차례 "
                "이용하셨습니다. 배변 양상은 보통이었고 어지럼이나 복통은 "
                "없다고 하셨습니다.",
            ),
            (
                "meal_followup",
                CARE_B,
                "report",
                "시설(가명)003",
                "시설(가명)003 어르신은 점심을 3분의 2 정도 드셨고 물 150ml를 "
                "추가로 드셨습니다. 삼킴 불편은 없었으며 저녁 식사량까지 "
                "계속 확인하겠습니다.",
            ),
            (
                "gait_followup",
                CARE_A,
                "handover",
                "시설(가명)012",
                "시설(가명)012 어르신은 이후 이동 시 한 명이 곁에서 보조해 주세요. "
                "현재까지 통증이나 붓기는 없으며 저녁 이동 때 보행 상태를 다시 "
                "확인하겠습니다.",
            ),
            (
                "skin_followup",
                CARE_B,
                "handover",
                "시설(가명)017",
                "시설(가명)017 어르신 엉치 부위는 다음 기저귀 교환 때 발적 범위와 "
                "피부 상태를 다시 확인해 주세요. 체위변경은 계속 시행하겠습니다.",
            ),
            (
                "guardian_followup",
                SOCIAL,
                "report",
                "시설(가명)041",
                "시설(가명)041 어르신의 야간 관찰내용을 확인해 보호자께 안내했습니다. "
                "보호자는 오늘 밤 수면 상태도 이어서 확인해 달라고 요청했습니다.",
            ),
        ]

        created: dict[str, dict[str, Any]] = {}
        entries: list[dict[str, Any]] = []
        for key, username, message_type, resident_name, body in specs:
            message = post_message(
                api,
                username=username,
                room_id=room_id,
                body=body,
                message_type=message_type,
                resident_id=resident_ids[resident_name],
            )
            created[key] = message
            entries.append(
                {
                    "key": key,
                    "username": username,
                    "message_id": message["id"],
                    "message_type": message_type,
                    "resident": resident_name,
                    "body": body,
                }
            )

        comments = [
            (
                "gait",
                SOCIAL,
                "확인했습니다. 저녁 보행 상태와 통증 여부를 다시 남겨 주세요.",
            ),
            (
                "skin",
                SOCIAL,
                "확인했습니다. 발적 범위가 넓어지거나 피부 변화가 있으면 바로 알려 주세요.",
            ),
            (
                "cognition",
                SOCIAL,
                "같은 말씀이 다시 나타나는 시간과 상황을 이어서 관찰하겠습니다.",
            ),
            (
                "meal_first",
                CARE_B,
                "점심 식사량과 수분 섭취량을 이어서 확인했습니다.",
            ),
        ]
        for message_key, username, body in comments:
            comment = post_comment(
                api,
                username=username,
                message_id=str(created[message_key]["id"]),
                body=body,
            )
            entries.append(
                {
                    "kind": "comment",
                    "message_key": message_key,
                    "username": username,
                    "comment_id": comment["id"],
                    "body": body,
                }
            )

        review = collect_period_review(api, room_id)
        source_bodies = {
            str(source["message"]["body"]) for source in review.get("sources", [])
        }
        missing_bodies = [body for *_, body in specs if body not in source_bodies]
        if missing_bodies:
            raise RuntimeError(
                f"돌봄 브리핑에서 등록 보고 {len(missing_bodies)}건을 찾지 못했습니다."
            )

        evidence = {
            "date": date.today().isoformat(),
            "room_id": room_id,
            "room_name": room["name"],
            "message_count": len(specs),
            "comment_count": len(comments),
            "attachment_count": 0,
            "entries": entries,
            "period_review": {
                "message_count": review.get("message_count"),
                "resident_count": review.get("resident_count"),
                "record_event_count": len(review.get("record_events", [])),
                "briefing_card_count": len(
                    (review.get("briefing") or {}).get("cards", [])
                ),
            },
        }
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(
            "등록 완료: "
            f"보고 {len(specs)}건 · 답글 {len(comments)}건 · 첨부파일 0건"
        )
        print(
            "돌봄 브리핑 확인: "
            f"메시지 {review.get('message_count')}건 · "
            f"어르신 {review.get('resident_count')}명 · "
            f"기록 묶음 {len(review.get('record_events', []))}개"
        )
        print(f"검증 기록: {args.evidence}")
        return 0
    finally:
        api.close()


if __name__ == "__main__":
    raise SystemExit(main())
