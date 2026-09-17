from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any


FIXTURE_VERSION = "presentation_story_enrichment_v1"
DISPLAY_NOTICE = "결선 시연용 합성 사건 흐름 · 실제 인물 및 기록과 무관"
STORY_SAFETY_CONTRACT = {
    "synthetic_fixture": True,
    "contains_real_personal_data": False,
    "official_record": False,
    "external_transfer_allowed": False,
}
ALLOWED_ROOMS = {
    "daycare_field": "주간보호 현장케어",
    "professional_collab": "전문직 케어협업",
    "homecare_field": "방문요양 현장지원",
}
RESIDENT_SERVICE_TYPES = {
    "어르0001": "daycare",
    "어르0002": "daycare",
    "어르0003": "daycare",
    "어르2001": "homecare",
}
ROOM_MEMBER_CODES = {
    "daycare_field": {"요보01", "요보02", "간호01", "사복01", "영양01", "작치01"},
    "professional_collab": {"간호01", "사복01", "사복02", "영양01", "작치01"},
    "homecare_field": {"요보03", "요보04", "간호01", "사복02"},
}
PHASES = ["observation", "handover", "confirmation", "follow_up", "result"]
KST = timezone(timedelta(hours=9))


STORY_DEFINITIONS = [
    {
        "story_id": "STORY-20260913-01-HYDRATION",
        "resident_code": "어르0001",
        "room_key": "daycare_field",
        "start": "2026-09-11T09:05:00+09:00",
        "senders": ["요보01", "요보01", "간호01", "요보02", "간호01"],
        "texts": [
            "오전 물 200mL 중 50mL만 드셨고 입술이 평소보다 건조해 보였습니다.",
            "간호01에게 수분 섭취 감소와 입술 건조 관찰을 인계하고 점심 전 재확인을 요청했습니다.",
            "확인 결과 삼킴 불편 호소는 없었습니다. 한 번에 많이 권하지 않고 물 100mL씩 자주 제공하기로 했습니다.",
            "오전 중 물 100mL를 두 차례 나누어 제공했고 모두 드신 것을 확인했습니다.",
            "오후에는 입술 건조가 완화되고 물 150mL를 추가로 드셨습니다. 다음 근무자에게 평소 섭취량 회복 여부를 계속 확인하도록 인계했습니다.",
        ],
    },
    {
        "story_id": "STORY-20260913-02-LEG-SWELLING",
        "resident_code": "어르0001",
        "room_key": "professional_collab",
        "start": "2026-09-12T10:10:00+09:00",
        "senders": ["작치01", "작치01", "간호01", "작치01", "간호01"],
        "texts": [
            "오전 활동 전 오른쪽 발목 양말 자국이 어제보다 선명해 보여 활동을 잠시 멈췄습니다.",
            "간호01에게 발목 관찰 내용을 전달하고 양쪽 상태 비교와 활동 가능 여부 확인을 요청했습니다.",
            "양쪽 발목을 비교했으며 오른쪽 자국이 더 뚜렷했습니다. 통증 호소와 피부 열감은 없었습니다.",
            "다리를 편안히 올리고 30분 뒤 다시 확인했으며 자국이 옅어졌습니다. 무리한 보행 활동은 줄였습니다.",
            "오후 재확인에서 불편 호소 없이 평소 보행을 유지했습니다. 다음 날 같은 시간대에 발목 상태를 다시 비교하기로 했습니다.",
        ],
    },
    {
        "story_id": "STORY-20260913-03-GAIT",
        "resident_code": "어르0002",
        "room_key": "daycare_field",
        "start": "2026-09-11T13:20:00+09:00",
        "senders": ["요보02", "요보02", "작치01", "요보01", "작치01"],
        "texts": [
            "식당에서 생활실로 이동할 때 첫 세 걸음에서 오른쪽으로 몸이 기울어 옆에서 부축했습니다.",
            "작치01에게 이동 때 몸이 기운 상황과 부축 사실을 인계하고 보행 상태 확인을 요청했습니다.",
            "평지 보행을 함께 확인했고 시작할 때만 오른쪽으로 기우는 모습이 한 차례 있었습니다. 어지럼 호소는 없었습니다.",
            "이동 전 의자에서 잠시 앉아 발 위치를 확인한 뒤 일어나도록 안내하고 가까이에서 동행했습니다.",
            "오후 두 차례 이동에서는 몸이 기울지 않았고 안전하게 도착했습니다. 다음 근무에서도 첫 걸음을 관찰하도록 남겼습니다.",
        ],
    },
    {
        "story_id": "STORY-20260913-04-SLEEP",
        "resident_code": "어르0002",
        "room_key": "professional_collab",
        "start": "2026-09-13T09:15:00+09:00",
        "senders": ["사복01", "사복01", "간호01", "사복01", "간호01"],
        "texts": [
            "아침 프로그램 중 평소보다 자주 눈을 감고 이름을 불렀을 때 바로 대답했지만 활동 참여가 줄었습니다.",
            "간호01에게 아침 졸림과 참여 감소를 전달하고 활력 상태와 전날 수면 여부 확인을 요청했습니다.",
            "체온과 맥박을 확인했으며 기관 기준에서 특이 변화는 없었습니다. 본인은 어젯밤 두 번 깼다고 말했습니다.",
            "조용한 자리에서 20분 휴식 후 가벼운 소그룹 활동으로 조정했고 물 100mL를 제공했습니다.",
            "오전 후반에는 대화와 활동 참여가 평소 수준으로 돌아왔습니다. 오후에 졸림이 반복되는지 한 번 더 확인하기로 했습니다.",
        ],
    },
    {
        "story_id": "STORY-20260913-05-MEAL",
        "resident_code": "어르0003",
        "room_key": "daycare_field",
        "start": "2026-09-12T12:05:00+09:00",
        "senders": ["요보01", "요보01", "영양01", "요보02", "영양01"],
        "texts": [
            "점심밥은 절반 정도 드셨고 반찬은 두세 숟가락만 드신 뒤 숟가락을 내려놓았습니다.",
            "영양01에게 점심 섭취 감소와 남긴 양을 인계하고 음식 상태와 선호를 확인해 달라고 요청했습니다.",
            "메뉴 온도와 질감을 확인했고 불편 호소는 없었습니다. 부드러운 반찬을 먼저 소량 제공하기로 했습니다.",
            "부드러운 반찬과 밥을 작은 양으로 다시 제공하니 밥 4분의 1과 반찬 절반을 추가로 드셨습니다.",
            "간식은 모두 드셨고 속이 불편하지 않다고 답했습니다. 저녁 식사량을 다음 근무자가 비교하도록 인계했습니다.",
        ],
    },
    {
        "story_id": "STORY-20260913-06-SKIN",
        "resident_code": "어르0003",
        "room_key": "professional_collab",
        "start": "2026-09-13T10:00:00+09:00",
        "senders": ["간호01", "간호01", "작치01", "간호01", "작치01"],
        "texts": [
            "오전 자세 변경 때 왼쪽 팔꿈치에 동전 크기의 옅은 붉은 부위를 확인했습니다.",
            "작치01에게 팔꿈치 피부 관찰 내용을 전달하고 활동 자세와 접촉 부위 확인을 요청했습니다.",
            "탁자 활동 때 왼쪽 팔꿈치가 모서리에 오래 닿는 자세를 확인했습니다. 통증이나 피부 손상은 없었습니다.",
            "팔꿈치가 직접 닿지 않도록 부드러운 받침을 놓고 1시간 뒤 피부색을 다시 확인했습니다.",
            "재확인에서 붉은 정도가 옅어졌고 피부 손상은 없었습니다. 다음 활동에서도 받침 사용과 피부색 확인을 이어가기로 했습니다.",
        ],
    },
    {
        "story_id": "STORY-20260913-07-MEDICATION-CHECK",
        "resident_code": "어르2001",
        "room_key": "homecare_field",
        "start": "2026-09-11T18:10:00+09:00",
        "senders": ["요보03", "요보03", "간호01", "요보04", "간호01"],
        "texts": [
            "방문 시 저녁 약통의 오늘 칸이 그대로 있어 복용 여부를 임의로 판단하지 않고 확인을 멈췄습니다.",
            "간호01에게 약통 상태와 본인이 복용 시간을 기억하지 못한 점을 인계하고 보호자 확인을 요청했습니다.",
            "보호자와 통화해 아직 복용 전임을 확인했습니다. 처방 내용은 변경하지 않고 기존 안내 시간에 복용을 돕도록 했습니다.",
            "보호자가 지켜보는 가운데 기존 안내에 따라 복용했고 약통의 오늘 칸이 비워진 것을 함께 확인했습니다.",
            "복용 후 불편 호소는 없었습니다. 다음 방문 때 약통 기록과 보호자 확인 여부를 다시 대조하기로 했습니다.",
        ],
    },
    {
        "story_id": "STORY-20260913-08-HOME-SAFETY",
        "resident_code": "어르2001",
        "room_key": "homecare_field",
        "start": "2026-09-13T17:40:00+09:00",
        "senders": ["요보04", "요보04", "사복02", "요보04", "사복02"],
        "texts": [
            "현관에서 방으로 가는 통로에 작은 상자가 놓여 보행 보조기 바퀴가 닿을 뻔해 이동을 멈췄습니다.",
            "사복02에게 통로 장애물과 이동 중 멈춘 상황을 인계하고 보호자와 환경 정리를 상의해 달라고 요청했습니다.",
            "보호자에게 통로를 함께 보여드렸고 상자를 다른 방으로 옮기기로 확인했습니다.",
            "상자를 옮긴 뒤 보행 보조기로 현관부터 방까지 동행하며 통로가 확보된 것을 확인했습니다.",
            "재이동 때 바퀴가 걸리는 곳 없이 안전하게 지나갔습니다. 방문 종료 전 통로가 다시 막히지 않았는지 최종 확인했습니다.",
        ],
    },
]


ADMIN_MESSAGES = [
    ("daycare_field", "사복01", "2026-09-11T08:35:00+09:00", "오후 프로그램 준비물 수량을 확인해 공용 표에 표시했습니다."),
    ("professional_collab", "사복02", "2026-09-12T08:50:00+09:00", "다음 주 직종회의 시작 시간을 10분 앞당기는 일정안을 공유했습니다."),
    ("homecare_field", "요보03", "2026-09-12T17:20:00+09:00", "내일 방문 순서표의 이동 시간을 확인했습니다."),
    ("daycare_field", "요보02", "2026-09-13T16:20:00+09:00", "공용 물품 점검표를 작성했고 부족 수량은 사무실에 전달했습니다."),
]


ATTACHMENTS = [
    ("STORY-20260913-01-HYDRATION-01", "hydration-check.txt", "합성자료\n수분 섭취 관찰표: 09:05 50mL, 10:20 100mL, 11:10 100mL\n"),
    ("STORY-20260913-03-GAIT-01", "gait-check.txt", "합성자료\n이동 관찰표: 시작 세 걸음 오른쪽 기울임 1회, 부축 후 안전 확보\n"),
    ("STORY-20260913-05-MEAL-01", "meal-check.txt", "합성자료\n점심 섭취표: 밥 1/2, 반찬 2~3숟가락, 추가 제공 후 밥 1/4\n"),
    ("STORY-20260913-06-SKIN-01", "skin-check.txt", "합성자료\n피부 관찰표: 왼쪽 팔꿈치 옅은 붉은 부위, 손상 없음, 받침 적용\n"),
    ("STORY-20260913-07-MEDICATION-CHECK-01", "medication-check.txt", "합성자료\n복용 확인표: 보호자 확인 전 미복용, 기존 안내에 따라 복용 확인\n"),
]


def build_story_enrichment() -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    stories: list[dict[str, Any]] = []
    phase_offsets = [0, 12, 30, 150, 330]
    for definition in STORY_DEFINITIONS:
        start = datetime.fromisoformat(definition["start"])
        message_ids: list[str] = []
        previous_id: str | None = None
        for ordinal, (phase, sender, text, offset) in enumerate(
            zip(PHASES, definition["senders"], definition["texts"], phase_offsets),
            start=1,
        ):
            message_id = f"{definition['story_id']}-{ordinal:02d}"
            item = {
                "message_id": message_id,
                "story_id": definition["story_id"],
                "phase": phase,
                "kind": "care",
                "room_key": definition["room_key"],
                "resident_code": definition["resident_code"],
                "service_type": RESIDENT_SERVICE_TYPES[definition["resident_code"]],
                "sender_code": sender,
                "body": text,
                "created_at": (start + timedelta(minutes=offset)).isoformat(),
            }
            if previous_id is not None:
                item["reply_to_message_id"] = previous_id
            messages.append(item)
            message_ids.append(message_id)
            previous_id = message_id
        stories.append(
            {
                "story_id": definition["story_id"],
                "resident_code": definition["resident_code"],
                "room_key": definition["room_key"],
                "message_ids": message_ids,
            }
        )

    for ordinal, (room_key, sender_code, created_at, body) in enumerate(
        ADMIN_MESSAGES, start=1
    ):
        messages.append(
            {
                "message_id": f"STORY-20260913-ADMIN-{ordinal:02d}",
                "story_id": None,
                "phase": "administrative",
                "kind": "administrative",
                "room_key": room_key,
                "resident_code": None,
                "service_type": "administrative",
                "sender_code": sender_code,
                "body": body,
                "created_at": created_at,
            }
        )

    attachments: list[dict[str, Any]] = []
    for ordinal, (message_id, short_name, content) in enumerate(ATTACHMENTS, start=1):
        encoded = content.encode("utf-8")
        attachments.append(
            {
                "attachment_id": f"STORY-ATT-{ordinal:02d}",
                "message_id": message_id,
                "original_name": f"story-synthetic-{short_name}",
                "mime_type": "text/plain",
                "inline_content": content,
                "size_bytes": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest(),
            }
        )

    fixture = {
        "schema_version": FIXTURE_VERSION,
        "display_notice": DISPLAY_NOTICE,
        "safety_contract": dict(STORY_SAFETY_CONTRACT),
        "rooms": dict(ALLOWED_ROOMS),
        "residents": dict(RESIDENT_SERVICE_TYPES),
        "stories": stories,
        "messages": sorted(messages, key=lambda item: (item["created_at"], item["message_id"])),
        "attachments": attachments,
    }
    validate_story_enrichment(fixture)
    return fixture


def validate_story_enrichment(fixture: dict[str, Any]) -> None:
    if fixture.get("schema_version") != FIXTURE_VERSION:
        raise ValueError("합성 사건 fixture 버전이 일치하지 않습니다.")
    if fixture.get("safety_contract") != STORY_SAFETY_CONTRACT:
        raise ValueError("합성 사건 fixture 안전 계약이 변경되었습니다.")
    if fixture.get("rooms") != ALLOWED_ROOMS:
        raise ValueError("허용된 세 방 이외의 방이 포함되었습니다.")
    if fixture.get("residents") != RESIDENT_SERVICE_TYPES:
        raise ValueError("허용된 네 명의 합성 어르신 범위가 변경되었습니다.")

    stories = fixture.get("stories", [])
    messages = fixture.get("messages", [])
    attachments = fixture.get("attachments", [])
    if len(stories) != 8 or len(messages) != 44 or len(attachments) != 5:
        raise ValueError("사건·메시지·첨부 고정 수치가 변경되었습니다.")

    message_by_id = {item["message_id"]: item for item in messages}
    if len(message_by_id) != len(messages):
        raise ValueError("중복 메시지 ID가 있습니다.")
    if any(not message_id.startswith("STORY-20260913-") for message_id in message_by_id):
        raise ValueError("사건 메시지 ID가 전용 namespace를 벗어났습니다.")

    for item in messages:
        room_key = item.get("room_key")
        if room_key not in ALLOWED_ROOMS:
            raise ValueError("허용되지 않은 방 메시지가 있습니다.")
        if item.get("sender_code") not in ROOM_MEMBER_CODES[room_key]:
            raise ValueError("해당 방 구성원이 아닌 합성 작성자가 있습니다.")
        datetime.fromisoformat(item["created_at"])
        if item.get("kind") == "care":
            if item.get("resident_code") not in RESIDENT_SERVICE_TYPES:
                raise ValueError("돌봄 메시지의 합성 어르신 연결이 없습니다.")
            if item.get("service_type") != RESIDENT_SERVICE_TYPES[item["resident_code"]]:
                raise ValueError("돌봄 메시지의 서비스 범위가 맞지 않습니다.")
        elif item.get("kind") == "administrative":
            if item.get("resident_code") is not None or item.get("story_id") is not None:
                raise ValueError("일반 행정 메시지에 어르신 또는 사건이 연결되었습니다.")
        else:
            raise ValueError("메시지 종류가 올바르지 않습니다.")

    if Counter(item["kind"] for item in messages) != {"care": 40, "administrative": 4}:
        raise ValueError("돌봄·행정 메시지 수가 변경되었습니다.")

    story_ids = {story["story_id"] for story in stories}
    if len(story_ids) != 8:
        raise ValueError("중복 사건 ID가 있습니다.")
    if Counter(story["resident_code"] for story in stories) != {
        code: 2 for code in RESIDENT_SERVICE_TYPES
    }:
        raise ValueError("합성 어르신별 사건 수가 두 건이 아닙니다.")
    for story in stories:
        linked = [message_by_id[message_id] for message_id in story["message_ids"]]
        if [item["phase"] for item in linked] != PHASES:
            raise ValueError("사건의 5단계 흐름이 완전하지 않습니다.")
        timestamps = [datetime.fromisoformat(item["created_at"]) for item in linked]
        if timestamps != sorted(timestamps) or len(set(timestamps)) != 5:
            raise ValueError("사건 메시지 시간 순서가 올바르지 않습니다.")
        if any(item["story_id"] != story["story_id"] for item in linked):
            raise ValueError("사건 메시지 연결이 다른 사건을 가리킵니다.")
        if any(item["resident_code"] != story["resident_code"] for item in linked):
            raise ValueError("사건 메시지의 어르신 연결이 일관되지 않습니다.")
        if linked[0].get("reply_to_message_id") is not None:
            raise ValueError("사건 최초 관찰은 답글일 수 없습니다.")
        if [item.get("reply_to_message_id") for item in linked[1:]] != [
            item["message_id"] for item in linked[:-1]
        ]:
            raise ValueError("사건 답글 흐름이 직전 단계와 연결되지 않았습니다.")

    attachment_ids = {item["attachment_id"] for item in attachments}
    if len(attachment_ids) != 5:
        raise ValueError("중복 첨부 ID가 있습니다.")
    for item in attachments:
        message = message_by_id.get(item["message_id"])
        if message is None or message["kind"] != "care":
            raise ValueError("합성 첨부가 돌봄 메시지에 연결되지 않았습니다.")
        content = item["inline_content"].encode("utf-8")
        if not item["original_name"].startswith("story-synthetic-"):
            raise ValueError("합성 첨부 파일명이 안전 namespace를 벗어났습니다.")
        if item["mime_type"] != "text/plain":
            raise ValueError("합성 첨부 형식이 허용 범위를 벗어났습니다.")
        if len(content) != item["size_bytes"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValueError("합성 첨부 크기 또는 해시가 일치하지 않습니다.")


def public_manifest(fixture: dict[str, Any] | None = None) -> dict[str, Any]:
    fixture = fixture or build_story_enrichment()
    validate_story_enrichment(fixture)
    return {
        "fixture_version": fixture["schema_version"],
        "story_count": len(fixture["stories"]),
        "message_count": len(fixture["messages"]),
        "care_message_count": sum(item["kind"] == "care" for item in fixture["messages"]),
        "administrative_message_count": sum(item["kind"] == "administrative" for item in fixture["messages"]),
        "attachment_count": len(fixture["attachments"]),
        "resident_count": len(fixture["residents"]),
        "room_message_counts": dict(Counter(item["room_key"] for item in fixture["messages"])),
        "official_record_write_count": 0,
        "external_transfer_count": 0,
    }
