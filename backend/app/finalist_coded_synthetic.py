from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any


FIXTURE_VERSION = "finalist_coded_synthetic_v1"
DISPLAY_NOTICE = "DEV 비공식 자료 · 코드화된 합성 시험자료"
WINDOW_START = datetime(2026, 5, 1, 9, 0, tzinfo=timezone(timedelta(hours=9)))
WINDOW_END = datetime(2026, 8, 28, 18, 0, tzinfo=timezone(timedelta(hours=9)))

STAFF_CODE_RE = re.compile(
    r"^(?:요보|사복|간호|조리|영양|위생|운전|작치|시설|관리자)\d{2}$"
)
RESIDENT_CODE_RE = re.compile(r"^어르\d{4}$")
GUARDIAN_CODE_RE = re.compile(r"^보호-어르\d{4}-\d{2}$")

SAFETY_CONTRACT = {
    "synthetic_fixture": True,
    "official_record": False,
    "external_transfer_allowed": False,
    "contains_real_personal_data": False,
    "real_name_restoration_allowed": False,
    "display_notice": DISPLAY_NOTICE,
}

STAFF: tuple[dict[str, Any], ...] = (
    {"code": "관리자01", "job_code": "facility_director", "role": "admin", "account": "bootstrap", "can_process_records": True},
    {"code": "관리자02", "job_code": "facility_director", "role": "admin", "account": "mentor", "can_process_records": True},
    {"code": "요보01", "job_code": "caregiver", "role": "staff", "can_process_records": False},
    {"code": "요보02", "job_code": "caregiver", "role": "staff", "can_process_records": False},
    {"code": "사복01", "job_code": "social_worker", "role": "staff", "can_process_records": True},
    {"code": "간호01", "job_code": "registered_nurse", "role": "staff", "can_process_records": True},
    {"code": "조리01", "job_code": "cook", "role": "staff", "can_process_records": False},
    {"code": "영양01", "job_code": "dietitian", "role": "staff", "can_process_records": False},
    {"code": "위생01", "job_code": "sanitation_worker", "role": "staff", "can_process_records": False},
    {"code": "운전01", "job_code": "driver_assistant", "role": "staff", "can_process_records": False},
    {"code": "작치01", "job_code": "occupational_therapist", "role": "staff", "can_process_records": True},
    {"code": "시설01", "job_code": "facility_director", "role": "staff", "can_process_records": True},
)

RESIDENTS: tuple[dict[str, Any], ...] = tuple(
    {
        "code": f"어르{index:04d}",
        "internal_code": f"DEV-FINALIST-RESIDENT-{index:04d}",
        "service_type": "facility",
        "guardians": [
            f"보호-어르{index:04d}-01",
            f"보호-어르{index:04d}-02",
        ],
    }
    for index in range(1, 7)
)

ROOMS: tuple[dict[str, Any], ...] = (
    {"key": "care", "name": "본선 합성 돌봄방", "kind": "custom"},
    {"key": "handover", "name": "본선 합성 업무인계방", "kind": "custom"},
    {"key": "consultation", "name": "본선 합성 상담방", "kind": "custom"},
    {"key": "source_01_daily", "name": "합성 일상돌봄 01", "kind": "custom", "source_profile_id": "source_01"},
    {"key": "source_02_health", "name": "합성 건강관찰 02", "kind": "custom", "source_profile_id": "source_02"},
    {"key": "source_03_team", "name": "합성 다직군소통 03", "kind": "custom", "source_profile_id": "source_03"},
    {"key": "source_04_program", "name": "합성 프로그램상담 04", "kind": "custom", "source_profile_id": "source_04"},
    {"key": "source_05_facility", "name": "합성 시설업무 05", "kind": "custom", "source_profile_id": "source_05"},
)

SOURCE_SHAPED_ROOM_KEYS = tuple(
    item["key"] for item in ROOMS if item.get("source_profile_id")
)


def _kst(value: str) -> str:
    return datetime.fromisoformat(f"{value}+09:00").astimezone(timezone.utc).isoformat()


def _comment(author: str, at: str, body: str) -> dict[str, Any]:
    return {"author_code": author, "created_at": _kst(at), "body": body}


def _core_events() -> list[dict[str, Any]]:
    return [
        {
            "event_id": "SYN-EVT-001",
            "category": "식사량과 수분 섭취",
            "resident_code": "어르0001",
            "room_key": "care",
            "sender_code": "요보01",
            "created_at": _kst("2026-05-03T12:15:00"),
            "body": "어르0001 점심은 2/3 섭취했고 물 160mL를 제공했습니다. 15:00에 잔량을 확인할 예정입니다.",
            "state": "pending",
            "record_tags": ["meal", "hydration", "follow_up"],
            "comments": [
                _comment("요보02", "2026-05-03T15:05:00", "15:00 확인 결과 물 160mL를 모두 섭취했습니다."),
                _comment("간호01", "2026-05-03T15:20:00", "불편 호소가 없어 수분 확인을 완료했습니다."),
            ],
            "action": {"type": "confirmation", "priority": "normal", "status": "completed", "due_at": _kst("2026-05-03T15:00:00"), "assignee_code": "요보02"},
            "expected_status": "completed",
        },
        {
            "event_id": "SYN-EVT-002",
            "category": "바이탈",
            "resident_code": "어르0002",
            "room_key": "care",
            "sender_code": "간호01",
            "created_at": _kst("2026-05-11T09:25:00"),
            "body": "어르0002 혈압 146/84를 확인했습니다. 10분 안정 뒤 다시 측정합니다.",
            "state": "pending",
            "record_tags": ["vital", "blood_pressure"],
            "comments": [_comment("간호01", "2026-05-11T09:38:00", "재측정 혈압은 138/80이며 어지럼 호소는 없었습니다.")],
            "action": {"type": "confirmation", "priority": "important", "status": "completed", "due_at": _kst("2026-05-11T09:40:00"), "assignee_code": "간호01"},
            "expected_status": "completed",
            "attachment_case": "coded_handwriting_vital_01",
        },
        {
            "event_id": "SYN-EVT-003",
            "category": "충돌 수치",
            "resident_code": "어르0003",
            "room_key": "handover",
            "sender_code": "요보02",
            "created_at": _kst("2026-05-19T16:10:00"),
            "body": "어르0003 체온을 37.2도로 기록했으나 교대 메모에는 37.8도로 적혀 있어 원본 확인이 필요합니다.",
            "state": "needs_review",
            "record_tags": ["vital", "conflict"],
            "comments": [_comment("간호01", "2026-05-19T16:22:00", "두 기록의 측정 시각이 달라 어느 값을 확정할지 확인 중입니다.")],
            "action": {"type": "confirmation", "priority": "important", "status": "in_progress", "due_at": _kst("2026-05-19T18:00:00"), "assignee_code": "간호01"},
            "expected_status": "in_progress",
        },
        {
            "event_id": "SYN-EVT-004",
            "category": "배변·배뇨",
            "resident_code": "어르0004",
            "room_key": "care",
            "sender_code": "요보01",
            "created_at": _kst("2026-05-28T08:40:00"),
            "body": "어르0004 오전 배변은 없었고 소변 색은 평소와 같았습니다. 오후 상태를 이어서 확인합니다.",
            "state": "pending",
            "record_tags": ["bowel", "urination", "follow_up"],
            "comments": [_comment("요보02", "2026-05-28T17:15:00", "16:50 보통 변을 확인했고 복부 불편 호소는 없었습니다.")],
            "action": {"type": "handover", "priority": "normal", "status": "completed", "due_at": _kst("2026-05-28T18:00:00"), "assignee_code": "요보02"},
            "expected_status": "completed",
        },
        {
            "event_id": "SYN-EVT-005",
            "category": "수면",
            "resident_code": "어르0005",
            "room_key": "handover",
            "sender_code": "요보02",
            "created_at": _kst("2026-06-04T07:35:00"),
            "body": "어르0005 새벽에 두 차례 깨어 복도를 잠시 걸었고 04:20 이후 다시 잠들었습니다.",
            "state": "completed",
            "record_tags": ["sleep", "mobility"],
            "comments": [_comment("요보01", "2026-06-04T09:10:00", "아침에는 피로 호소 없이 식당으로 이동했습니다.")],
            "expected_status": "completed",
        },
        {
            "event_id": "SYN-EVT-006",
            "category": "감정 변화·프로그램",
            "resident_code": "어르0006",
            "room_key": "care",
            "sender_code": "작치01",
            "created_at": _kst("2026-06-12T14:05:00"),
            "body": "어르0006 프로그램 시작 전 표정이 굳어 있었으나 직원과 대화 후 안정되어 색칠 활동에 25분 참여했습니다.",
            "state": "completed",
            "record_tags": ["emotion", "program"],
            "comments": [_comment("사복01", "2026-06-12T15:00:00", "활동 종료 뒤 작품을 보여주며 미소를 보였습니다.")],
            "expected_status": "completed",
        },
        {
            "event_id": "SYN-EVT-007",
            "category": "이동·부축",
            "resident_code": "어르0001",
            "room_key": "care",
            "sender_code": "요보02",
            "created_at": _kst("2026-06-21T10:30:00"),
            "body": "어르0001 복도 이동 중 왼쪽으로 기울어 팔을 잡아 부축했습니다. 이동 뒤 통증 호소는 없었습니다.",
            "state": "completed",
            "record_tags": ["mobility", "assistance"],
            "comments": [_comment("작치01", "2026-06-21T11:15:00", "보행 속도를 낮추고 손잡이 사용을 안내했습니다.")],
            "expected_status": "completed",
        },
        {
            "event_id": "SYN-EVT-008",
            "category": "낙상·병원·보호자 연락",
            "resident_code": "어르0002",
            "room_key": "handover",
            "sender_code": "요보01",
            "created_at": _kst("2026-06-29T17:45:00"),
            "body": "어르0002 의자에서 일어서다 균형을 잃어 바닥에 주저앉았습니다. 오른쪽 손목 불편을 호소해 간호01에게 보고했습니다.",
            "state": "pending",
            "record_tags": ["fall", "hospital", "guardian_contact"],
            "comments": [
                _comment("간호01", "2026-06-29T18:05:00", "붓기가 보여 협력 병원 진료를 준비했습니다."),
                _comment("사복01", "2026-06-29T18:20:00", "보호-어르0002-01에게 상황과 진료 계획을 설명했습니다."),
                _comment("간호01", "2026-06-29T20:10:00", "진료 결과 골절은 없고 염좌 소견으로 귀원했습니다."),
            ],
            "action": {"type": "cooperation", "priority": "urgent", "status": "completed", "due_at": _kst("2026-06-29T20:30:00"), "assignee_code": "간호01"},
            "expected_status": "completed",
        },
        {
            "event_id": "SYN-EVT-009",
            "category": "피부 상태",
            "resident_code": "어르0003",
            "room_key": "care",
            "sender_code": "위생01",
            "created_at": _kst("2026-07-07T09:50:00"),
            "body": "어르0003 옷 교체 중 오른쪽 팔꿈치에 옅은 붉은 부위를 확인해 간호01에게 전달했습니다.",
            "state": "pending",
            "record_tags": ["skin", "handover"],
            "comments": [_comment("간호01", "2026-07-07T10:15:00", "피부 손상은 없고 압박을 줄여 경과를 확인하기로 했습니다.")],
            "action": {"type": "confirmation", "priority": "important", "status": "in_progress", "due_at": _kst("2026-07-08T10:00:00"), "assignee_code": "간호01"},
            "expected_status": "in_progress",
        },
        {
            "event_id": "SYN-EVT-010",
            "category": "투약 전달",
            "resident_code": "어르0004",
            "room_key": "handover",
            "sender_code": "간호01",
            "created_at": _kst("2026-07-15T17:20:00"),
            "body": "어르0004 저녁 복용분은 식후 제공하도록 요보02에게 전달했습니다. 약명과 용량은 처방 기록에서 별도 확인합니다.",
            "state": "pending",
            "record_tags": ["medication", "handover"],
            "comments": [_comment("요보02", "2026-07-15T18:40:00", "저녁 식사 후 간호01 확인 아래 복용을 마쳤습니다.")],
            "action": {"type": "handover", "priority": "important", "status": "completed", "due_at": _kst("2026-07-15T19:00:00"), "assignee_code": "요보02"},
            "expected_status": "completed",
        },
        {
            "event_id": "SYN-EVT-011",
            "category": "보호자 상담",
            "resident_code": "어르0005",
            "room_key": "consultation",
            "sender_code": "사복01",
            "created_at": _kst("2026-07-23T11:05:00"),
            "body": "보호-어르0005-01과 통화해 어르0005의 최근 식사량과 프로그램 참여 상태를 설명했고 보호자가 내용을 확인했습니다.",
            "state": "completed",
            "record_tags": ["consultation", "guardian_contact"],
            "comments": [_comment("관리자01", "2026-07-23T11:30:00", "추가 요청은 없었음을 확인했습니다.")],
            "expected_status": "completed",
        },
        {
            "event_id": "SYN-EVT-012",
            "category": "보호자 회신 대기",
            "resident_code": "어르0006",
            "room_key": "consultation",
            "sender_code": "사복01",
            "created_at": _kst("2026-07-31T16:30:00"),
            "body": "보호-어르0006-02에게 다음 주 외출 일정 확인을 요청했고 회신을 기다리고 있습니다.",
            "state": "pending",
            "record_tags": ["guardian_contact", "pending"],
            "comments": [],
            "action": {"type": "confirmation", "priority": "normal", "status": "assigned", "due_at": _kst("2026-08-03T12:00:00"), "assignee_code": "사복01"},
            "expected_status": "pending",
        },
        {
            "event_id": "SYN-EVT-013",
            "category": "영양 검토",
            "resident_code": "어르0001",
            "room_key": "care",
            "sender_code": "영양01",
            "created_at": _kst("2026-08-06T13:25:00"),
            "body": "어르0001 최근 사흘간 점심 섭취량이 절반 안팎으로 기록되어 간식 제공 시간과 선호 반찬을 확인합니다.",
            "state": "pending",
            "record_tags": ["nutrition", "meal"],
            "comments": [
                _comment("조리01", "2026-08-06T14:10:00", "부드러운 반찬을 먼저 제공하도록 조리 순서를 조정했습니다."),
                _comment("요보01", "2026-08-07T12:45:00", "다음 날 점심은 3/4 섭취했습니다."),
            ],
            "expected_status": "completed",
        },
        {
            "event_id": "SYN-EVT-014",
            "category": "송영",
            "resident_code": "어르0003",
            "room_key": "handover",
            "sender_code": "운전01",
            "created_at": _kst("2026-08-12T08:15:00"),
            "body": "어르0003 병원 이동을 위해 08:15 차량에 탑승했고 안전띠와 보조기 위치를 확인했습니다.",
            "state": "completed",
            "record_tags": ["transport", "safety"],
            "comments": [_comment("요보02", "2026-08-12T10:40:00", "귀원 후 침실까지 부축해 이동했습니다.")],
            "expected_status": "completed",
        },
        {
            "event_id": "SYN-EVT-015",
            "category": "음성 수분 기록",
            "resident_code": "어르0004",
            "room_key": "care",
            "sender_code": "요보01",
            "created_at": _kst("2026-08-18T15:10:00"),
            "body": "어르0004 물 180mL 제공 사실을 음성으로 남겼습니다. 17:00 잔량을 확인합니다.",
            "state": "pending",
            "record_tags": ["hydration", "audio", "follow_up"],
            "comments": [_comment("요보02", "2026-08-18T17:05:00", "17:00 잔량 없이 모두 섭취한 것을 확인했습니다.")],
            "action": {"type": "confirmation", "priority": "normal", "status": "completed", "due_at": _kst("2026-08-18T17:00:00"), "assignee_code": "요보02"},
            "expected_status": "completed",
            "attachment_case": "coded_voice_water_01",
        },
        {
            "event_id": "SYN-EVT-016",
            "category": "시설 점검",
            "resident_code": "어르0006",
            "room_key": "handover",
            "sender_code": "시설01",
            "created_at": _kst("2026-08-24T10:00:00"),
            "body": "어르0006 이동 동선의 손잡이 흔들림을 확인해 사용을 잠시 중지하고 안전 표지를 부착했습니다.",
            "state": "pending",
            "record_tags": ["facility", "safety", "pending"],
            "comments": [_comment("관리자01", "2026-08-24T10:20:00", "수리 요청을 접수했고 완료 연락을 기다리고 있습니다.")],
            "action": {"type": "cooperation", "priority": "urgent", "status": "in_progress", "due_at": _kst("2026-08-28T17:00:00"), "assignee_code": "시설01"},
            "expected_status": "in_progress",
        },
    ]


ROUTINE_TEMPLATES: tuple[tuple[str, str, str], ...] = (
    ("meal", "{resident} 아침 식사는 {amount} 섭취했고 불편 호소는 없었습니다.", "요보01"),
    ("hydration", "{resident} 오전 물 {water}mL를 제공했고 제공량을 기록했습니다.", "요보02"),
    ("program", "{resident} 오전 소근육 프로그램에 {minutes}분 참여했습니다.", "작치01"),
    ("emotion", "{resident} 직원 인사에 반응하며 안정된 표정으로 생활실에 머물렀습니다.", "사복01"),
    ("hygiene", "{resident} 침구와 개인 수납공간을 정리했고 오염은 없었습니다.", "위생01"),
    ("meal_support", "{resident} 식사 형태와 배식량을 확인해 조리팀과 공유했습니다.", "영양01"),
)

SOURCE_ROOM_TEMPLATES: dict[str, tuple[dict[str, Any], ...]] = {
    "source_01_daily": (
        {"category": "식사량과 수분 섭취", "sender": "요보01", "body": "{resident} 아침 식사는 {portion} 섭취했고 물 {amount}mL를 제공했습니다. {follow_hour}:00에 잔량을 확인할 예정입니다.", "comment_author": "요보02", "comment": "{follow_hour}:00 확인 결과 제공한 물의 잔량은 없었고 불편 호소도 없었습니다.", "tags": ["meal", "hydration", "follow_up"]},
        {"category": "배변·배뇨", "sender": "요보02", "body": "{resident} 오전 배변 여부와 소변 상태를 확인했으며 평소와 다른 점은 없었습니다.", "comment_author": "간호01", "comment": "오후 인계 때에도 불편 호소가 없음을 확인했습니다.", "tags": ["bowel", "urination"]},
        {"category": "수면", "sender": "요보01", "body": "{resident} 새벽에 한 차례 깨어 물을 마신 뒤 다시 잠들었습니다.", "comment_author": "요보02", "comment": "아침 기상 뒤 피로 호소 없이 생활실로 이동했습니다.", "tags": ["sleep", "hydration"]},
        {"category": "이동·부축", "sender": "요보02", "body": "{resident} 식당 이동 시 손잡이를 잡도록 안내하고 옆에서 부축했습니다.", "comment_author": "작치01", "comment": "이동 뒤 통증이나 어지럼 호소는 없었습니다.", "tags": ["mobility", "assistance"]},
    ),
    "source_02_health": (
        {"category": "바이탈", "sender": "간호01", "body": "{resident} 오전 혈압은 {systolic}/{diastolic}로 측정됐고 10분 안정 뒤 다시 확인했습니다.", "comment_author": "간호01", "comment": "재확인 수치는 {repeat_systolic}/{repeat_diastolic}였고 불편 호소는 없었습니다.", "tags": ["vital", "blood_pressure"]},
        {"category": "피부 상태", "sender": "요보01", "body": "{resident} 세면 보조 중 팔꿈치 피부가 옅게 붉어 보여 간호01에게 전달했습니다.", "comment_author": "간호01", "comment": "피부 손상은 없었고 눌림을 줄인 뒤 경과를 관찰했습니다.", "tags": ["skin", "handover"]},
        {"category": "투약 전달", "sender": "간호01", "body": "{resident} 저녁 복용분은 식후 제공하도록 인계했습니다. 약명과 용량은 처방 기록에서 확인합니다.", "comment_author": "요보02", "comment": "저녁 식사 후 간호01 확인 아래 복용을 마쳤습니다.", "tags": ["medication", "handover"]},
        {"category": "충돌 수치", "sender": "요보02", "body": "{resident} 관찰표와 교대 메모의 체온 수치가 서로 달라 원본 확인이 필요합니다.", "comment_author": "간호01", "comment": "측정 시각이 달라 어느 값도 자동 확정하지 않고 다시 측정하기로 했습니다.", "tags": ["vital", "conflict"]},
    ),
    "source_03_team": (
        {"category": "업무 인계", "sender": "사복01", "body": "{resident}의 오전 식사·이동 관찰 내용을 오후 근무자에게 인계했습니다.", "comment_author": "요보02", "comment": "오후 관찰을 이어받았고 특이사항 발생 시 다시 공유하겠습니다.", "tags": ["handover"]},
        {"category": "송영 안전", "sender": "운전01", "body": "{resident} 승하차 때 발판과 안전벨트를 확인하고 생활실까지 동행했습니다.", "comment_author": "요보01", "comment": "귀원 뒤 보행 상태에 평소와 다른 점은 없었습니다.", "tags": ["transport", "safety"]},
        {"category": "식사 형태 공유", "sender": "영양01", "body": "{resident}의 식사 형태와 최근 섭취량을 확인해 조리01과 돌봄 직원에게 공유했습니다.", "comment_author": "조리01", "comment": "다음 배식부터 확인된 형태로 준비했습니다.", "tags": ["nutrition", "meal_support"]},
        {"category": "확인 요청", "sender": "관리자01", "body": "{resident} 관련 두 인계 기록의 시간이 달라 작성 시각을 확인해 달라고 요청했습니다.", "comment_author": "사복01", "comment": "원문 대조 전에는 어느 시간도 확정하지 않고 확인 목록에 남겼습니다.", "tags": ["conflict", "confirmation"]},
    ),
    "source_04_program": (
        {"category": "프로그램 참여", "sender": "작치01", "body": "{resident} 소근육 활동에 {minutes}분 참여했고 안내에 맞춰 도구를 사용했습니다.", "comment_author": "사복01", "comment": "활동 종료 뒤 안정된 표정으로 작품을 정리했습니다.", "tags": ["program", "response"]},
        {"category": "감정 변화", "sender": "사복01", "body": "{resident} 활동 시작 전 표정이 굳어 있었으나 직원과 대화한 뒤 참여했습니다.", "comment_author": "작치01", "comment": "활동 중 거부 표현 없이 끝까지 자리에 머물렀습니다.", "tags": ["emotion", "program"]},
        {"category": "보호자 상담", "sender": "사복01", "body": "보호-{resident}-01에게 {resident}의 최근 프로그램 참여와 식사 상태를 설명했고 보호자가 내용을 확인했습니다.", "comment_author": "관리자01", "comment": "상담 결과 추가 요청은 없었습니다.", "tags": ["consultation", "guardian_contact"]},
        {"category": "일정 확인", "sender": "사복01", "body": "보호-{resident}-02에게 {resident}의 다음 활동 일정 확인을 요청했고 회신을 기다리고 있습니다.", "comment_author": "관리자01", "comment": "회신 전까지 일정은 확정하지 않습니다.", "tags": ["guardian_contact", "pending"]},
    ),
    "source_05_facility": (
        {"category": "환경 위생", "sender": "위생01", "body": "{resident}이 사용하는 생활공간의 손잡이와 공용 물품을 소독하고 정리했습니다.", "comment_author": "관리자01", "comment": "점검표에 완료 상태를 반영했습니다.", "tags": ["hygiene", "environment"]},
        {"category": "시설 안전", "sender": "시설01", "body": "{resident} 생활 동선의 미끄럼 방지 상태와 조명을 확인했습니다.", "comment_author": "요보01", "comment": "이동 시 방해가 되는 물품은 없었습니다.", "tags": ["facility", "safety"]},
        {"category": "비품 전달", "sender": "관리자01", "body": "{resident} 돌봄에 필요한 소모품 재고를 확인하고 담당자에게 보충을 요청했습니다.", "comment_author": "위생01", "comment": "요청한 소모품을 정리해 지정 위치에 두었습니다.", "tags": ["supplies", "handover"]},
        {"category": "수리 대기", "sender": "시설01", "body": "{resident} 생활공간의 호출 장치 점검을 요청했고 수리 담당자의 회신을 기다리고 있습니다.", "comment_author": "관리자01", "comment": "수리 완료 전까지 대체 호출 방법을 안내했습니다.", "tags": ["facility", "pending"]},
    ),
}


def _source_shaped_messages() -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    portions = ("전량", "3/4", "2/3", "1/2")
    for room_index, room_key in enumerate(SOURCE_SHAPED_ROOM_KEYS):
        templates = SOURCE_ROOM_TEMPLATES[room_key]
        for ordinal in range(48):
            template = templates[ordinal % len(templates)]
            resident = RESIDENTS[(ordinal + room_index) % len(RESIDENTS)]["code"]
            local_at = WINDOW_START + timedelta(
                days=(ordinal * 5 + room_index * 2) % 118,
                hours=room_index + ordinal % 8,
                minutes=(ordinal * 7 + room_index * 3) % 55,
            )
            values = {
                "resident": resident,
                "portion": portions[(ordinal + room_index) % len(portions)],
                "amount": 110 + 10 * ((ordinal + room_index) % 9),
                "follow_hour": 14 + (ordinal % 4),
                "systolic": 124 + 2 * ((ordinal + room_index) % 8),
                "diastolic": 70 + 2 * ((ordinal + room_index) % 6),
                "repeat_systolic": 120 + 2 * ((ordinal + room_index) % 7),
                "repeat_diastolic": 68 + 2 * ((ordinal + room_index) % 5),
                "minutes": 20 + 5 * ((ordinal + room_index) % 5),
            }
            unresolved = ordinal % 17 == 0
            needs_review = "conflict" in template["tags"]
            state = "needs_review" if needs_review else ("pending" if unresolved else "completed")
            comments = [] if unresolved else [
                {
                    "author_code": template["comment_author"],
                    "created_at": (local_at + timedelta(minutes=35)).astimezone(timezone.utc).isoformat(),
                    "body": template["comment"].format(**values),
                }
            ]
            messages.append(
                {
                    "event_id": f"SYN-SOURCE-{room_index + 1:02d}-{ordinal + 1:03d}",
                    "category": template["category"],
                    "resident_code": resident,
                    "room_key": room_key,
                    "source_profile_id": f"source_{room_index + 1:02d}",
                    "sender_code": template["sender"],
                    "created_at": local_at.astimezone(timezone.utc).isoformat(),
                    "body": template["body"].format(**values),
                    "state": state,
                    "record_tags": list(template["tags"]),
                    "comments": comments,
                    "expected_status": "in_progress" if state in {"pending", "needs_review"} else "completed",
                    "synthetic_origin": "newly_authored_from_structure_only",
                }
            )
    return messages


def build_fixture() -> dict[str, Any]:
    messages = list(_core_events())
    amounts = ("전량", "3/4", "2/3", "1/2")
    waters = (120, 140, 170, 190)
    for week in range(17):
        for resident_index, resident in enumerate(RESIDENTS):
            category, body_template, sender = ROUTINE_TEMPLATES[(week + resident_index) % len(ROUTINE_TEMPLATES)]
            local_at = WINDOW_START + timedelta(days=week * 7 + resident_index % 3, hours=resident_index, minutes=(week * 7) % 50)
            if local_at > WINDOW_END:
                continue
            event_id = f"SYN-ROUTINE-{week + 1:02d}-{resident_index + 1:02d}"
            messages.append(
                {
                    "event_id": event_id,
                    "category": category,
                    "resident_code": resident["code"],
                    "room_key": "care" if resident_index % 2 == 0 else "handover",
                    "sender_code": sender,
                    "created_at": local_at.astimezone(timezone.utc).isoformat(),
                    "body": body_template.format(
                        resident=resident["code"],
                        amount=amounts[(week + resident_index) % len(amounts)],
                        water=waters[(week + resident_index) % len(waters)],
                        minutes=20 + 5 * ((week + resident_index) % 4),
                    ),
                    "state": "completed",
                    "record_tags": [category],
                    "comments": (
                        [_comment("간호01", local_at.astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%dT%H:%M:%S"), "기록 내용을 확인했습니다.")]
                        if (week + resident_index) % 11 == 0
                        else []
                    ),
                    "expected_status": "completed",
                }
            )
    messages.extend(_source_shaped_messages())
    messages.sort(key=lambda item: (item["created_at"], item["event_id"]))
    fixture = {
        "schema_version": FIXTURE_VERSION,
        "window": {"start": WINDOW_START.isoformat(), "end": WINDOW_END.isoformat()},
        "safety_contract": SAFETY_CONTRACT,
        "staff": list(STAFF),
        "residents": list(RESIDENTS),
        "rooms": list(ROOMS),
        "messages": messages,
        "attachments": [
            {
                "case_id": "coded_handwriting_vital_01",
                "event_id": "SYN-EVT-002",
                "kind": "image",
                "original_name": "coded-handwriting-vital-01.png",
                "mime_type": "image/png",
                "extracted_text": "어르0002\n8월 18일 09:25\n혈압 146/84 확인\n10분 안정 후 138/80 재확인\n어지럼 호소 없음",
            },
            {
                "case_id": "coded_voice_water_01",
                "event_id": "SYN-EVT-015",
                "kind": "audio",
                "original_name": "coded-voice-water-01.wav",
                "mime_type": "audio/wav",
                "extracted_text": "어르0004 오후 3시 10분 물 180밀리리터 제공했습니다. 오후 5시 잔량 없이 모두 섭취한 것을 확인했습니다.",
            },
            {
                "case_id": "coded_handwriting_source_01",
                "event_id": "SYN-SOURCE-01-045",
                "kind": "image",
                "original_name": "coded-handwriting-source-01.jpg",
                "mime_type": "image/jpeg",
                "extracted_text": "어르0003\n오후 2시 10분 물 200mL 제공\n오후 4시 다시 확인 예정\n오후 4시 5분 잔량 없이 200mL 섭취 완료",
            },
            {
                "case_id": "coded_handwriting_source_02",
                "event_id": "SYN-SOURCE-02-045",
                "kind": "image",
                "original_name": "coded-handwriting-source-02.jpg",
                "mime_type": "image/jpeg",
                "extracted_text": "어르0004\n오전 9시 10분 혈압 170/90 측정\n10분 안정 후 오전 9시 20분 재측정 120/70\n일어날 때 잠깐 휘청하여 이동 시 부축함\n넘어지지는 않음",
            },
            {
                "case_id": "coded_handwriting_source_03",
                "event_id": "SYN-SOURCE-03-047",
                "kind": "image",
                "original_name": "coded-handwriting-source-03.jpg",
                "mime_type": "image/jpeg",
                "extracted_text": "어르0001\n아침밥 1/2 정도 섭취\n국물은 조금 섭취하고 입맛이 없다고 말함\n오전에는 표정이 어둡고 프로그램 참여를 거부함\n11시 50분 점심은 소량 섭취\n직원과 대화 후 표정이 안정되고 오후 프로그램 참여",
            },
            {
                "case_id": "coded_handwriting_source_04",
                "event_id": "SYN-SOURCE-04-046",
                "kind": "image",
                "original_name": "coded-handwriting-source-04.jpg",
                "mime_type": "image/jpeg",
                "extracted_text": "어르0001\n오전 프로그램 참여를 거부함\n11시 50분 점심은 소량 섭취\n직원과 대화 후 표정이 안정되고 오후 프로그램 참여",
            },
            {
                "case_id": "coded_handwriting_source_05",
                "event_id": "SYN-SOURCE-05-046",
                "kind": "image",
                "original_name": "coded-handwriting-source-05.jpg",
                "mime_type": "image/jpeg",
                "extracted_text": "어르0002\n오전 9시 이틀째 배변 없음\n배가 아프지는 않다고 말함\n물을 제공하고 점심 식사 후 복도를 10분 정도 걸음\n오후 3시 20분 배변 확인\n배변 후 불편감 없다고 말함\n계속 관찰하기로 함",
            },
        ],
    }
    validate_fixture(fixture)
    return fixture


def validate_fixture(fixture: dict[str, Any]) -> None:
    if fixture.get("schema_version") != FIXTURE_VERSION:
        raise ValueError("합성자료 계약 버전이 올바르지 않습니다.")
    staff_codes = [item["code"] for item in fixture["staff"]]
    resident_codes = [item["code"] for item in fixture["residents"]]
    guardian_codes = [code for item in fixture["residents"] for code in item["guardians"]]
    if len(staff_codes) != len(set(staff_codes)) or not all(STAFF_CODE_RE.fullmatch(code) for code in staff_codes):
        raise ValueError("직원 코드가 중복되었거나 계약과 다릅니다.")
    if len(resident_codes) != len(set(resident_codes)) or not all(RESIDENT_CODE_RE.fullmatch(code) for code in resident_codes):
        raise ValueError("어르신 코드가 중복되었거나 계약과 다릅니다.")
    if len(guardian_codes) != len(set(guardian_codes)) or not all(GUARDIAN_CODE_RE.fullmatch(code) for code in guardian_codes):
        raise ValueError("보호자 코드가 중복되었거나 계약과 다릅니다.")
    staff_set = set(staff_codes)
    resident_set = set(resident_codes)
    room_keys = {item["key"] for item in fixture["rooms"]}
    event_ids: set[str] = set()
    timestamps: list[datetime] = []
    for item in fixture["messages"]:
        if item["event_id"] in event_ids:
            raise ValueError("합성 사건 ID가 중복되었습니다.")
        event_ids.add(item["event_id"])
        if item["sender_code"] not in staff_set or item["resident_code"] not in resident_set:
            raise ValueError("합성 메시지의 인물 관계가 올바르지 않습니다.")
        if item["room_key"] not in room_keys:
            raise ValueError("합성 메시지의 대화방 관계가 올바르지 않습니다.")
        timestamps.append(datetime.fromisoformat(item["created_at"]))
        for comment in item.get("comments", []):
            if comment["author_code"] not in staff_set:
                raise ValueError("합성 답글 작성자 관계가 올바르지 않습니다.")
    if not timestamps or (max(timestamps) - min(timestamps)).days < 90:
        raise ValueError("합성 대화 기간은 3개월 이상이어야 합니다.")
    if any(value is not expected for value, expected in (
        (fixture["safety_contract"].get("synthetic_fixture"), True),
        (fixture["safety_contract"].get("official_record"), False),
        (fixture["safety_contract"].get("external_transfer_allowed"), False),
        (fixture["safety_contract"].get("contains_real_personal_data"), False),
    )):
        raise ValueError("합성자료 개인정보 안전 계약이 올바르지 않습니다.")
    attachment_case_ids: set[str] = set()
    for item in fixture["attachments"]:
        if item["case_id"] in attachment_case_ids:
            raise ValueError("합성 첨부 사례 ID가 중복되었습니다.")
        attachment_case_ids.add(item["case_id"])
        if item["event_id"] not in event_ids:
            raise ValueError("합성 첨부가 존재하지 않는 사건을 참조합니다.")
        if not item["original_name"].startswith("coded-"):
            raise ValueError("합성 첨부 파일명은 코드화된 이름만 사용할 수 있습니다.")


def public_manifest(fixture: dict[str, Any]) -> dict[str, Any]:
    categories = sorted({item["category"] for item in fixture["messages"]})
    comments = sum(len(item.get("comments", [])) for item in fixture["messages"])
    return {
        "schema_version": "finalist_coded_synthetic_manifest_v1",
        "fixture_version": fixture["schema_version"],
        "window": fixture["window"],
        "staff_codes": [item["code"] for item in fixture["staff"]],
        "resident_codes": [item["code"] for item in fixture["residents"]],
        "guardian_codes": [code for item in fixture["residents"] for code in item["guardians"]],
        "message_count": len(fixture["messages"]),
        "comment_count": comments,
        "room_count": len(fixture["rooms"]),
        "source_shaped_room_count": len(SOURCE_SHAPED_ROOM_KEYS),
        "source_shaped_message_count": sum(
            item.get("source_profile_id") is not None for item in fixture["messages"]
        ),
        "event_categories": categories,
        "attachment_count": len(fixture["attachments"]),
        "safety_contract": fixture["safety_contract"],
        "code_rules": {
            "staff": STAFF_CODE_RE.pattern,
            "resident": RESIDENT_CODE_RE.pattern,
            "guardian": GUARDIAN_CODE_RE.pattern,
            "contains_birthdate_floor_room_or_real_name": False,
            "code_reuse_allowed": False,
            "real_name_restoration_allowed": False,
        },
    }
