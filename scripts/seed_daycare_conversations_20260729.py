"""2026-07-29 주간보호 가상 업무대화를 기존 채팅 흐름으로 추가합니다.

이 스크립트는 기존 메시지를 삭제하거나 수정하지 않습니다. 로컬 개발자 런처로
가상 직원 화면을 전환한 뒤 실제 채팅 API를 호출하고, 같은 시작 문구가 이미 있으면
중복 등록을 중단합니다.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIRMATION = "SEED_DAYCARE_20260729"
START_MARKER = (
    "오늘 주간보호 테스트 업무대화를 시작합니다. "
    "어르신별 관찰·조치·결과를 가명으로 기록하겠습니다."
)
REQUIRED_USERS = ("dcare01", "dcare02", "daysw")
REQUIRED_RESIDENTS = (
    "주간-어르신-02(가명)",
    "주간-어르신-03(가명)",
    "주간-어르신-05(가명)",
    "주간-어르신-06(가명)",
    "주간-어르신-10(가명)",
    "주간-어르신-11(가명)",
    "주간-어르신-12(가명)",
    "주간-어르신-13(가명)",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="주간보호 가상 대화와 이미지 보고 2건을 실제 API로 등록합니다."
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
        "--image",
        action="append",
        type=Path,
        default=[],
        help="이미지 보고 파일. 정확히 두 번 지정합니다.",
    )
    parser.add_argument(
        "--evidence",
        type=Path,
        default=PROJECT_ROOT
        / "output"
        / "validation"
        / "daycare_conversations_20260729.json",
        help="가명 등록·검증 결과 JSON 경로",
    )
    return parser.parse_args()


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def require_ok(response: httpx.Response, label: str) -> dict[str, Any] | list[Any]:
    if response.is_success:
        if not response.content:
            return {}
        return response.json()
    try:
        detail = response.json().get("detail", response.text)
    except (ValueError, AttributeError):
        detail = response.text
    raise RuntimeError(f"{label} 실패 ({response.status_code}): {detail}")


class LauncherApi:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=60,
            follow_redirects=True,
            headers={"user-agent": "SMCODI daycare demo seeder"},
        )
        require_ok(
            self.client.post(
                "/api/auth/login",
                json={"username": username, "password": password},
            ),
            "개발자 런처 로그인",
        )
        users = require_ok(self.client.get("/api/dev/users"), "가상 직원 조회")
        assert isinstance(users, list)
        self.users = {str(user["username"]): user for user in users}

    def close(self) -> None:
        self.client.close()

    def switch(self, username: str) -> dict[str, Any]:
        user = self.users.get(username)
        if user is None:
            raise RuntimeError(f"가상 직원 계정을 찾을 수 없습니다: {username}")
        result = require_ok(
            self.client.post(f"/api/dev/switch/{user['id']}"),
            f"{username} 화면 전환",
        )
        assert isinstance(result, dict)
        return result

    def return_to_launcher(self) -> None:
        require_ok(self.client.post("/api/dev/return"), "개발자 런처 복귀")

    def as_user(self, username: str, callback):
        self.switch(username)
        try:
            return callback()
        finally:
            self.return_to_launcher()


def find_daycare_context(api: LauncherApi) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    def inspect() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        rooms = require_ok(api.client.get("/api/rooms"), "채팅방 조회")
        assert isinstance(rooms, list)
        room = next((item for item in rooms if item["name"] == "주간보호방"), None)
        if room is None:
            business_rooms = [item for item in rooms if item.get("kind") == "business"]
            if len(business_rooms) == 1:
                room = business_rooms[0]
        if room is None:
            raise RuntimeError("주간보호방을 찾을 수 없습니다.")
        residents = require_ok(
            api.client.get(f"/api/rooms/{room['id']}/residents"),
            "주간보호 어르신 조회",
        )
        assert isinstance(residents, list)
        resident_map = {
            str(item["display_name"]): item
            for item in residents
            if item.get("service_type") == "daycare"
        }
        missing = sorted(set(REQUIRED_RESIDENTS) - set(resident_map))
        if missing:
            raise RuntimeError(
                "필요한 주간보호 가명 어르신이 없습니다: " + ", ".join(missing)
            )
        return room, resident_map

    return api.as_user("dcare01", inspect)


def ensure_not_seeded(api: LauncherApi, room_id: str) -> None:
    def inspect() -> None:
        messages = require_ok(
            api.client.get(f"/api/rooms/{room_id}/messages", params={"limit": 100}),
            "기존 메시지 확인",
        )
        assert isinstance(messages, list)
        if any(message.get("body") == START_MARKER for message in messages):
            raise RuntimeError(
                "오늘자 주간보호 가상 대화가 이미 등록되어 중복 등록을 중단합니다."
            )

    api.as_user("dcare01", inspect)


def post_message(
    api: LauncherApi,
    *,
    username: str,
    room_id: str,
    body: str,
    message_type: str = "chat",
    resident_id: str | None = None,
) -> dict[str, Any]:
    def send() -> dict[str, Any]:
        payload: dict[str, Any] = {
            "body": body,
            "message_type": message_type,
        }
        if resident_id:
            payload["resident_id"] = resident_id
        result = require_ok(
            api.client.post(f"/api/rooms/{room_id}/messages", json=payload),
            f"{username} 메시지 등록",
        )
        assert isinstance(result, dict)
        return result

    return api.as_user(username, send)


def post_image(
    api: LauncherApi,
    *,
    username: str,
    room_id: str,
    body: str,
    image_path: Path,
) -> dict[str, Any]:
    def send() -> dict[str, Any]:
        with image_path.open("rb") as image_file:
            result = require_ok(
                api.client.post(
                    f"/api/rooms/{room_id}/messages-with-files",
                    data={
                        "body": body,
                        "message_type": "report",
                        "report_image": "true",
                    },
                    files={
                        "files": (
                            image_path.name,
                            image_file,
                            "image/jpeg",
                        )
                    },
                ),
                f"{username} 이미지 보고 등록",
            )
        assert isinstance(result, dict)
        return result

    return api.as_user(username, send)


def post_comment(
    api: LauncherApi,
    *,
    username: str,
    message_id: str,
    body: str,
) -> dict[str, Any]:
    def send() -> dict[str, Any]:
        result = require_ok(
            api.client.post(
                f"/api/messages/{message_id}/comments",
                json={"body": body},
            ),
            f"{username} 답글 등록",
        )
        assert isinstance(result, dict)
        return result

    return api.as_user(username, send)


def latest_message(api: LauncherApi, room_id: str, message_id: str) -> dict[str, Any]:
    def inspect() -> dict[str, Any]:
        messages = require_ok(
            api.client.get(f"/api/rooms/{room_id}/messages", params={"limit": 100}),
            "이미지 판독 상태 조회",
        )
        assert isinstance(messages, list)
        message = next((item for item in messages if item["id"] == message_id), None)
        if message is None:
            raise RuntimeError(f"등록한 메시지를 다시 찾을 수 없습니다: {message_id}")
        return message

    return api.as_user("daysw", inspect)


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

    return api.as_user("daysw", inspect)


def main() -> int:
    args = parse_args()
    applying = args.apply is not None
    if applying and args.apply != CONFIRMATION:
        print(f"확인 문구가 올바르지 않습니다. 필요한 값: {CONFIRMATION}")
        return 2
    if len(args.image) != 2:
        print("--image 인자는 정확히 두 번 지정해야 합니다.")
        return 2
    image_paths = [path.expanduser().resolve() for path in args.image]
    missing_images = [str(path) for path in image_paths if not path.is_file()]
    if missing_images:
        print("이미지 파일을 찾을 수 없습니다: " + ", ".join(missing_images))
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
                "필요한 가상 직원 계정이 없습니다: " + ", ".join(missing_users)
            )
        room, residents = find_daycare_context(api)
        ensure_not_seeded(api, str(room["id"]))

        print(f"대상 채팅방: {room['name']}")
        print(f"가상 직원: {', '.join(REQUIRED_USERS)}")
        print("가명 어르신: " + ", ".join(REQUIRED_RESIDENTS))
        print("등록 예정: 메시지 17건, 답글 4건, 이미지 보고 2건")
        if not applying:
            print("모의 실행 완료: 실제 대화는 등록하지 않았습니다.")
            return 0

        room_id = str(room["id"])
        resident_id = {
            name: str(residents[name]["id"])
            for name in REQUIRED_RESIDENTS
        }
        entries: list[dict[str, Any]] = []
        message_by_key: dict[str, dict[str, Any]] = {}

        message_specs = [
            {
                "key": "start",
                "username": "dcare01",
                "type": "chat",
                "body": START_MARKER,
            },
            {
                "key": "transport_breath",
                "username": "dcare01",
                "type": "report",
                "resident": "주간-어르신-10(가명)",
                "body": (
                    "주간-어르신-10(가명) 어르신이 아침 송영차에서 내리실 때 허리를 많이 "
                    "구부리고 숨이 차 보였습니다. 의자에 앉혀 5분간 쉬도록 돕고 "
                    "호흡이 안정되는지 관찰했습니다."
                ),
            },
            {
                "key": "shoes",
                "username": "dcare01",
                "type": "report",
                "resident": "주간-어르신-03(가명)",
                "body": (
                    "주간-어르신-03(가명) 어르신이 센터 도착 후 신발을 계속 찾으셨습니다. "
                    "신발을 봉지에 넣어 자리 옆에 두고 위치를 안내드리니 식사와 "
                    "간식에 참여하셨습니다."
                ),
            },
            {
                "key": "toilet",
                "username": "dcare02",
                "type": "report",
                "resident": "주간-어르신-03(가명)",
                "body": (
                    "주간-어르신-03(가명) 어르신이 화장실 이용 후 맞은편 화장실로 다시 "
                    "가려 하셔서 현재 위치를 설명하고 자리까지 동행했습니다."
                ),
            },
            {
                "key": "home_repeat",
                "username": "dcare01",
                "type": "report",
                "resident": "주간-어르신-11(가명)",
                "body": (
                    "주간-어르신-11(가명) 어르신이 오후 휴식 후 이불을 개며 집에 가야 한다는 "
                    "말씀을 여러 번 반복하셨습니다. 저녁 식사 후 귀가한다고 안내했으나 "
                    "잠시 뒤 같은 말씀을 다시 하셨습니다."
                ),
            },
            {
                "key": "resident_conflict",
                "username": "dcare02",
                "type": "report",
                "resident": "주간-어르신-12(가명)",
                "body": (
                    "주간-어르신-12(가명) 어르신이 이동 중 다른 어르신과 부딪힐 뻔한 뒤 "
                    "서로 목소리가 커졌습니다. 두 분의 자리를 잠시 떨어뜨리고 각각 "
                    "상황을 설명했습니다."
                ),
            },
            {
                "key": "walking_risk",
                "username": "dcare01",
                "type": "report",
                "body": (
                    "걷기 운동 중 주간-어르신-05(가명) 어르신이 "
                    "주간-어르신-06(가명) 어르신 뒤에 "
                    "너무 가까이 붙어 옷을 잡으려 했습니다. 낙상 위험이 있어 두 분의 "
                    "간격을 벌리고 옆에서 보행을 도왔습니다."
                ),
            },
            {
                "key": "walking_handover",
                "username": "daysw",
                "type": "handover",
                "body": (
                    "주간-어르신-05(가명)·주간-어르신-06(가명) 어르신은 "
                    "다음 걷기 운동 때 시작 위치를 분리하고 "
                    "직원이 한 분씩 보조해 주세요. 오늘은 동선을 분리한 뒤 추가 충돌 "
                    "없이 마쳤습니다."
                ),
            },
            {
                "key": "medication",
                "username": "dcare02",
                "type": "report",
                "resident": "주간-어르신-13(가명)",
                "body": (
                    "주간-어르신-13(가명) 어르신 오늘 복약은 1일 3회 일정대로 확인했습니다. "
                    "거부 반응은 없었습니다."
                ),
            },
            {
                "key": "fruit",
                "username": "dcare01",
                "type": "chat",
                "resident": "주간-어르신-02(가명)",
                "body": (
                    "주간-어르신-02(가명) 어르신 보호자께서 어르신들과 나눠 드시라고 키위를 "
                    "보내셨습니다. 수량을 확인해 간식 담당자에게 전달했습니다."
                ),
            },
        ]

        for spec in message_specs:
            message = post_message(
                api,
                username=spec["username"],
                room_id=room_id,
                body=spec["body"],
                message_type=spec["type"],
                resident_id=resident_id.get(spec.get("resident", "")),
            )
            message_by_key[spec["key"]] = message
            entries.append(
                {
                    "kind": "message",
                    "key": spec["key"],
                    "username": spec["username"],
                    "message_id": message["id"],
                    "message_type": spec["type"],
                    "resident": spec.get("resident"),
                    "body": spec["body"],
                }
            )

        image_specs = [
            (
                "handwriting_1",
                "오늘 주간보호 수기보고 1/2입니다. 여러 가명 어르신의 특이사항이 포함되어 있습니다.",
                image_paths[0],
            ),
            (
                "handwriting_2",
                "오늘 주간보호 수기보고 2/2입니다. 식사·이동·복약·생활 관찰 내용이 포함되어 있습니다.",
                image_paths[1],
            ),
        ]
        for key, body, image_path in image_specs:
            message = post_image(
                api,
                username="dcare01",
                room_id=room_id,
                body=body,
                image_path=image_path,
            )
            message_by_key[key] = message
            entries.append(
                {
                    "kind": "image_message",
                    "key": key,
                    "username": "dcare01",
                    "message_id": message["id"],
                    "message_type": "report",
                    "image_name": image_path.name,
                    "body": body,
                }
            )

        trailing_specs = [
            {
                "key": "guardian_call",
                "username": "daysw",
                "type": "report",
                "resident": "주간-어르신-11(가명)",
                "body": (
                    "주간-어르신-11(가명) 어르신의 귀가 반복 말씀과 오전 이동 중 언쟁 상황을 "
                    "보호자에게 실제 전화로 안내했습니다. 최근 집에서도 비슷한 반복이 "
                    "있는지 확인을 요청했고 보호자는 오늘 저녁 상태를 살펴 연락 주기로 "
                    "했습니다."
                ),
            },
            {
                "key": "home_repeat_duplicate",
                "username": "dcare02",
                "type": "report",
                "resident": "주간-어르신-11(가명)",
                "body": (
                    "오후 늦게 주간-어르신-11(가명) 어르신이 다시 이불을 정리하며 집에 가야 "
                    "한다고 말씀하셨습니다. 귀가 시간을 안내하고 옆에서 대화를 나누자 "
                    "잠시 안정을 찾으셨습니다."
                ),
            },
            {
                "key": "dinner_support",
                "username": "dcare01",
                "type": "report",
                "resident": "주간-어르신-03(가명)",
                "body": (
                    "저녁 식사 때 주간-어르신-03(가명) 어르신이 양손으로 음식을 집으려 하셔서 "
                    "숟가락을 다시 안내하고 식사를 도왔습니다. 이후 숟가락으로 식사를 "
                    "이어가셨습니다."
                ),
            },
            {
                "key": "breath_followup",
                "username": "dcare02",
                "type": "report",
                "resident": "주간-어르신-10(가명)",
                "body": (
                    "주간-어르신-10(가명) 어르신은 휴식 후 숨참이 줄었고 대화에도 평소처럼 "
                    "반응하셨습니다. 오후 이동 때는 천천히 걷도록 곁에서 보조했습니다."
                ),
            },
            {
                "key": "daily_close",
                "username": "daysw",
                "type": "handover",
                "body": (
                    "오늘은 주간-어르신-10(가명) 숨참, 주간-어르신-11(가명) 귀가 반복 말씀, "
                    "주간-어르신-05(가명)·주간-어르신-06(가명) 보행 간격을 "
                    "내일 오전 다시 확인해 주세요. "
                    "이미 시행한 휴식·동선 분리·보호자 연락 결과도 함께 남겼습니다."
                ),
            },
        ]
        for spec in trailing_specs:
            message = post_message(
                api,
                username=spec["username"],
                room_id=room_id,
                body=spec["body"],
                message_type=spec["type"],
                resident_id=resident_id.get(spec.get("resident", "")),
            )
            message_by_key[spec["key"]] = message
            entries.append(
                {
                    "kind": "message",
                    "key": spec["key"],
                    "username": spec["username"],
                    "message_id": message["id"],
                    "message_type": spec["type"],
                    "resident": spec.get("resident"),
                    "body": spec["body"],
                }
            )

        comment_specs = [
            (
                "dcare02",
                "transport_breath",
                "5분 뒤 숨참이 줄었고 대화에 평소처럼 반응하시는 것을 확인했습니다.",
            ),
            (
                "daysw",
                "home_repeat",
                "보호자에게 오늘 반복 상황을 전화로 안내했고 저녁 상태를 확인해 주시기로 했습니다.",
            ),
            (
                "dcare01",
                "resident_conflict",
                "자리 분리 후 두 분 모두 목소리가 낮아졌고 간식 시간에는 추가 언쟁이 없었습니다.",
            ),
            (
                "daysw",
                "handwriting_1",
                "수기보고 원문과 채팅보고를 함께 확인하겠습니다. 이름·복약·수치는 자동 확정하지 않습니다.",
            ),
        ]
        for username, message_key, body in comment_specs:
            comment = post_comment(
                api,
                username=username,
                message_id=str(message_by_key[message_key]["id"]),
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

        ocr_checks: list[dict[str, Any]] = []
        pending_image_ids = {
            str(message_by_key["handwriting_1"]["id"]),
            str(message_by_key["handwriting_2"]["id"]),
        }
        deadline = time.monotonic() + 30
        while pending_image_ids and time.monotonic() < deadline:
            completed_now: set[str] = set()
            for message_id in sorted(pending_image_ids):
                message = latest_message(api, room_id, message_id)
                attachment = message["attachments"][0]
                extraction = attachment.get("text_extraction") or {}
                status = extraction.get("status", "not_created")
                if status in {"completed", "failed", "reviewed"}:
                    ocr_checks.append(
                        {
                            "message_id": message_id,
                            "image_name": attachment["original_name"],
                            "status": status,
                            "provider": extraction.get("provider"),
                            "model_name": extraction.get("model_name"),
                            "extracted_text_length": len(
                                extraction.get("extracted_text") or ""
                            ),
                            "error_message": extraction.get("error_message"),
                        }
                    )
                    completed_now.add(message_id)
            pending_image_ids -= completed_now
            if pending_image_ids:
                time.sleep(2)
        for message_id in sorted(pending_image_ids):
            message = latest_message(api, room_id, message_id)
            attachment = message["attachments"][0]
            extraction = attachment.get("text_extraction") or {}
            ocr_checks.append(
                {
                    "message_id": message_id,
                    "image_name": attachment["original_name"],
                    "status": extraction.get("status", "not_created"),
                    "provider": extraction.get("provider"),
                    "model_name": extraction.get("model_name"),
                    "extracted_text_length": len(
                        extraction.get("extracted_text") or ""
                    ),
                    "error_message": extraction.get("error_message"),
                }
            )

        period_review = collect_period_review(api, room_id)
        briefing = period_review.get("briefing") or {}
        evidence = {
            "generated_at": datetime.now().astimezone().isoformat(),
            "base_url": args.base_url,
            "room": {"id": room_id, "name": room["name"]},
            "messages_created": sum(
                1 for entry in entries if entry["kind"] in {"message", "image_message"}
            ),
            "comments_created": sum(
                1 for entry in entries if entry["kind"] == "comment"
            ),
            "image_messages_created": 2,
            "entries": entries,
            "ocr_checks": ocr_checks,
            "period_review": {
                "message_count": period_review.get("message_count"),
                "comment_count": period_review.get("comment_count"),
                "resident_count": period_review.get("resident_count"),
                "category_counts": period_review.get("category_counts"),
                "document_counts": period_review.get("document_counts"),
                "record_group_counts": period_review.get("record_group_counts"),
                "briefing": {
                    "comparison_days": briefing.get("comparison_days"),
                    "needs_attention_count": briefing.get("needs_attention_count"),
                    "pending_check_count": briefing.get("pending_check_count"),
                    "document_candidate_count": briefing.get(
                        "document_candidate_count"
                    ),
                    "cards": [
                        {
                            "resident_name": card.get("resident_name"),
                            "priority": card.get("priority"),
                            "change_summary": card.get("change_summary"),
                            "pending_checks": card.get("pending_checks"),
                            "document_types": card.get("document_types"),
                        }
                        for card in briefing.get("cards", [])
                    ],
                },
            },
        }
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(
            "등록 완료: "
            f"메시지 {evidence['messages_created']}건, "
            f"답글 {evidence['comments_created']}건, "
            f"이미지 {evidence['image_messages_created']}건"
        )
        print(
            "오늘 돌봄 브리핑: "
            f"대화 {period_review.get('message_count', 0)}건, "
            f"어르신 {period_review.get('resident_count', 0)}명, "
            f"우선 확인 {briefing.get('needs_attention_count', 0)}명"
        )
        for check in ocr_checks:
            print(
                f"OCR {check['image_name']}: {check['status']} "
                f"({check['extracted_text_length']}자)"
            )
        print(f"검증 기록: {args.evidence}")
        return 0
    finally:
        api.close()


if __name__ == "__main__":
    raise SystemExit(main())
