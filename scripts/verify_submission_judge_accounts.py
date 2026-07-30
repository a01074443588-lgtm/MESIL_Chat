"""공개 주소에서 제출 시연용 계정과 방 권한을 실제 로그인으로 확인합니다."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

from prepare_submission_judge_accounts import build_targets  # noqa: E402
from submission_accounts import load_submission_password  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="제출 시연용 계정 공개 로그인 확인")
    parser.add_argument(
        "--base-url",
        default="https://chat.silvermedical.kr",
        help="검증할 MESIL_Chat 주소",
    )
    parser.add_argument(
        "--credentials",
        type=Path,
        default=PROJECT_ROOT / "data" / "SUBMISSION_JUDGE_CREDENTIALS.txt",
        help="Git에서 제외된 제출 시연계정 파일",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_url = args.base_url.rstrip("/")
    password = load_submission_password(args.credentials)
    targets = build_targets(args.credentials)
    role_by_username = {
        username: expected["role"] for username, expected in targets.items()
    }
    failures: list[str] = []

    health = httpx.get(f"{base_url}/api/health", timeout=20)
    if health.status_code != 200 or health.json().get("status") != "ok":
        raise RuntimeError(f"공개 상태 확인 실패: HTTP {health.status_code}")
    print(f"공개 상태: {base_url} · ok")

    for username, expected in targets.items():
        role_label = role_by_username[username]
        with httpx.Client(
            base_url=base_url,
            headers={"Origin": base_url},
            timeout=20,
            follow_redirects=True,
        ) as client:
            login = client.post(
                "/api/auth/login",
                json={"username": username, "password": password},
            )
            if login.status_code != 200:
                failures.append(f"{role_label}: 로그인 HTTP {login.status_code}")
                continue

            current = client.get("/api/auth/me")
            rooms_response = client.get("/api/rooms")
            work_items = client.get("/api/work-items")
            if current.status_code != 200:
                failures.append(
                    f"{role_label}: 현재 사용자 HTTP {current.status_code}"
                )
            if rooms_response.status_code != 200:
                failures.append(
                    f"{role_label}: 채팅방 HTTP {rooms_response.status_code}"
                )
                continue

            actual_rooms = {room["name"] for room in rooms_response.json()}
            if actual_rooms != expected["rooms"]:
                failures.append(
                    f"{role_label}: 방 권한 불일치 "
                    f"예상={sorted(expected['rooms'])}, 실제={sorted(actual_rooms)}"
                )
            expected_workdesk_status = (
                200 if expected["can_process_records"] else 403
            )
            if work_items.status_code != expected_workdesk_status:
                failures.append(
                    f"{role_label}: 업무함 HTTP {work_items.status_code}, "
                    f"예상 {expected_workdesk_status}"
                )
            if role_label in {"보고 작성자", "실시간 수신자"}:
                room = next(
                    (
                        candidate
                        for candidate in rooms_response.json()
                        if candidate["name"] == "3층방"
                    ),
                    None,
                )
                if room is None:
                    failures.append(f"{role_label}: 3층방을 찾을 수 없습니다.")
                else:
                    residents = client.get(f"/api/rooms/{room['id']}/residents")
                    messages = client.get(f"/api/rooms/{room['id']}/messages")
                    if residents.status_code != 200:
                        failures.append(
                            f"{role_label}: 3층 어르신 목록 HTTP {residents.status_code}"
                        )
                    elif not any(
                        resident["display_name"] == "시설(가명)004"
                        for resident in residents.json()
                    ):
                        failures.append(
                            f"{role_label}: 시연 어르신 시설(가명)004가 없습니다."
                        )
                    if messages.status_code != 200:
                        failures.append(
                            f"{role_label}: 3층 메시지 조회 HTTP {messages.status_code}"
                        )
            elif role_label == "업무함 검토자" and work_items.status_code == 200:
                if len(work_items.json()) < 4:
                    failures.append(
                        f"{role_label}: 접근 가능한 업무함 자료가 4건보다 적습니다."
                    )
            logout = client.post("/api/auth/logout", json={})
            if logout.status_code != 204:
                failures.append(f"{role_label}: 로그아웃 HTTP {logout.status_code}")
            print(
                f"- {role_label}: 로그인 200 · 방 {len(actual_rooms)}개 · "
                f"업무함 {work_items.status_code} · 로그아웃 {logout.status_code}"
            )

    if failures:
        print("\n실패")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("\n제출 시연용 계정 3개의 공개 로그인·방 권한·업무함 권한을 확인했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
