from __future__ import annotations

from copy import deepcopy
from collections import Counter
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any


FIXTURE_VERSION = "presentation_five_room_synthetic_v1"
DISPLAY_NOTICE = "결선 시연용 합성자료 · 실제 인물 및 기록과 무관"
SYNTHETIC_ORIGIN = "newly_authored_from_aggregate_patterns_only"
WINDOW = {
    "start": "2026-08-28T07:30:00+09:00",
    "end": "2026-09-10T18:30:00+09:00",
}

ASSET_SOURCE_DIR = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "finalist_coded_synthetic_v1"
)

SAFETY_CONTRACT = {
    "synthetic_fixture": True,
    "official_record": False,
    "external_transfer_allowed": False,
    "contains_real_personal_data": False,
    "real_name_restoration_allowed": False,
    "automatic_signature_or_nhis_transfer": False,
}

STAFF: tuple[dict[str, Any], ...] = (
    {"code": "관리자01", "job_code": "facility_director", "role": "admin", "can_process_records": True},
    {"code": "사복01", "job_code": "social_worker", "role": "staff", "can_process_records": True},
    {"code": "사복02", "job_code": "social_worker", "role": "staff", "can_process_records": True},
    {"code": "간호01", "job_code": "registered_nurse", "role": "staff", "can_process_records": True},
    {"code": "영양01", "job_code": "dietitian", "role": "staff", "can_process_records": False},
    {"code": "작치01", "job_code": "occupational_therapist", "role": "staff", "can_process_records": True},
    {"code": "요보01", "job_code": "caregiver", "role": "staff", "can_process_records": False},
    {"code": "요보02", "job_code": "caregiver", "role": "staff", "can_process_records": False},
    {"code": "요보03", "job_code": "caregiver", "role": "staff", "can_process_records": False},
    {"code": "요보04", "job_code": "caregiver", "role": "staff", "can_process_records": False},
    {"code": "사무01", "job_code": "administrative_staff", "role": "staff", "can_process_records": False},
    {"code": "운전01", "job_code": "driver_assistant", "role": "staff", "can_process_records": False},
)

RESIDENTS: tuple[dict[str, Any], ...] = tuple(
    {
        "code": f"어르{number:04d}",
        "internal_code": f"PRESENTATION-SYNTHETIC-{number:04d}",
        "service_type": service_type,
        "guardians": [f"보호-어르{number:04d}-01"],
    }
    for number, service_type in (
        (1, "daycare"),
        (2, "daycare"),
        (3, "daycare"),
        (4, "daycare"),
        (5, "daycare"),
        (2001, "homecare"),
        (2002, "homecare"),
        (2003, "homecare"),
    )
)

ROOMS: tuple[dict[str, Any], ...] = (
    {
        "key": "daycare_field",
        "name": "주간보호 현장케어",
        "service_scope": "daycare",
        "member_codes": [
            "관리자01", "사복01", "간호01", "영양01", "작치01",
            "요보01", "요보02", "사무01", "운전01",
        ],
    },
    {
        "key": "all_staff",
        "name": "실버메디컬 전체소통",
        "service_scope": "all",
        "member_codes": [item["code"] for item in STAFF],
    },
    {
        "key": "professional_collab",
        "name": "전문직 케어협업",
        "service_scope": "daycare",
        "member_codes": ["관리자01", "사복01", "사복02", "간호01", "영양01", "작치01"],
    },
    {
        "key": "office_admin",
        "name": "사무행정 지원실",
        "service_scope": "administrative",
        "member_codes": ["관리자01", "사복01", "사복02", "간호01", "영양01", "작치01", "사무01"],
    },
    {
        "key": "homecare_field",
        "name": "방문요양 현장지원",
        "service_scope": "homecare",
        "member_codes": ["관리자01", "사복02", "간호01", "요보03", "요보04", "사무01"],
    },
)


STAFF_CODE_RE = re.compile(r"^(?:관리자|사복|간호|영양|작치|요보|사무|운전)\d{2}$")
RESIDENT_CODE_RE = re.compile(r"^어르\d{4}$")
GUARDIAN_CODE_RE = re.compile(r"^보호-어르\d{4}-\d{2}$")

START_AT = datetime.fromisoformat(WINDOW["start"])

ROOM_PREFIX = {
    "daycare_field": "DC",
    "all_staff": "ALL",
    "professional_collab": "PRO",
    "office_admin": "OFF",
    "homecare_field": "HC",
}


def _message_id(room_key: str, day_number: int, slot_number: int) -> str:
    return f"PRES-{ROOM_PREFIX[room_key]}-{day_number:02d}-{slot_number:02d}"


def _at(day_index: int, hour: int, minute: int) -> str:
    value = START_AT.replace(hour=hour, minute=minute) + timedelta(days=day_index)
    return value.isoformat()


def _message(
    *,
    room_key: str,
    day_index: int,
    slot_number: int,
    hour: int,
    minute: int,
    sender_code: str,
    body: str,
    service_type: str,
    resident_code: str | None = None,
    reply_to_message_id: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "message_id": _message_id(room_key, day_index + 1, slot_number),
        "room_key": room_key,
        "sender_code": sender_code,
        "created_at": _at(day_index, hour, minute),
        "body": body,
        "service_type": service_type,
        "synthetic_origin": SYNTHETIC_ORIGIN,
    }
    if resident_code is not None:
        item["resident_code"] = resident_code
    if reply_to_message_id is not None:
        item["reply_to_message_id"] = reply_to_message_id
    return item


def _daycare_messages(day_index: int) -> list[dict[str, Any]]:
    residents = [f"어르{1 + ((day_index + offset) % 5):04d}" for offset in range(4)]
    water = 120 + (day_index % 4) * 20
    portions = ("절반 정도", "2/3 정도", "대부분", "1/3 정도")
    minutes = 15 + (day_index % 4) * 5
    day_number = day_index + 1
    first_id = _message_id("daycare_field", day_number, 1)
    third_id = _message_id("daycare_field", day_number, 3)
    fourth_id = _message_id("daycare_field", day_number, 4)
    seventh_id = _message_id("daycare_field", day_number, 7)
    return [
        _message(
            room_key="daycare_field",
            day_index=day_index,
            slot_number=1,
            hour=8,
            minute=5,
            sender_code="운전01",
            resident_code=residents[0],
            service_type="daycare",
            body=f"{residents[0]} 등원했습니다. 차량에서 내릴 때 옆에서 부축했습니다.",
        ),
        _message(
            room_key="daycare_field",
            day_index=day_index,
            slot_number=2,
            hour=8,
            minute=20,
            sender_code="요보01",
            resident_code=residents[0],
            service_type="daycare",
            body=f"{residents[0]} 자리 안내했습니다. 현재 불편 호소는 없습니다.",
            reply_to_message_id=first_id,
        ),
        _message(
            room_key="daycare_field",
            day_index=day_index,
            slot_number=3,
            hour=9,
            minute=35,
            sender_code="요보02",
            resident_code=residents[1],
            service_type="daycare",
            body=f"{residents[1]} 오전 간식은 {portions[day_index % 4]} 드셨고 물 {water}mL 제공했습니다.",
        ),
        _message(
            room_key="daycare_field",
            day_index=day_index,
            slot_number=4,
            hour=11,
            minute=50,
            sender_code="작치01",
            resident_code=residents[2],
            service_type="daycare",
            body=f"{residents[2]} 오전 활동에 {minutes}분 참여했고 불편 호소는 없었습니다.",
        ),
        _message(
            room_key="daycare_field",
            day_index=day_index,
            slot_number=5,
            hour=13,
            minute=40,
            sender_code="요보01",
            resident_code=residents[2],
            service_type="daycare",
            body=f"확인했습니다. {residents[2]} 오후 이동 때 손잡이 사용을 안내하겠습니다.",
            reply_to_message_id=fourth_id,
        ),
        _message(
            room_key="daycare_field",
            day_index=day_index,
            slot_number=6,
            hour=15,
            minute=10,
            sender_code="간호01",
            resident_code=residents[1],
            service_type="daycare",
            body=f"{residents[1]} 오후 상태 확인했습니다. 평소와 다른 호소는 없습니다.",
            reply_to_message_id=third_id,
        ),
        _message(
            room_key="daycare_field",
            day_index=day_index,
            slot_number=7,
            hour=16,
            minute=25,
            sender_code="사복01",
            resident_code=residents[3],
            service_type="daycare",
            body=f"{residents[3]} 보호자 전달사항 확인했습니다. 추가 요청은 없습니다.",
        ),
        _message(
            room_key="daycare_field",
            day_index=day_index,
            slot_number=8,
            hour=17,
            minute=35,
            sender_code="요보02",
            resident_code=residents[3],
            service_type="daycare",
            body=f"오늘 {residents[3]} 관련 인계 내용 확인했습니다.",
            reply_to_message_id=seventh_id,
        ),
    ]


def _all_staff_messages(day_index: int) -> list[dict[str, Any]]:
    notices = (
        "오늘 16시 교육자료 확인 부탁드립니다.\n참석이 어려운 분은 사무행정 지원실에 남겨주세요.",
        "공용 비품 입고 내용을 공유드립니다. 필요한 수량은 오늘 중 알려주세요.",
        "내일 오전 회의 안내드립니다. 담당 업무 메모를 미리 확인해주세요.",
        "공용공간 안전점검 예정입니다. 통행에 불편한 물품이 없는지 확인해주세요.",
        "이번 주 근무 안내를 올렸습니다. 변경이 필요한 분은 담당자에게 알려주세요.",
    )
    day_number = day_index + 1
    notice_id = _message_id("all_staff", day_number, 1)
    responders = ["요보01", "요보03", "간호01", "작치01", "영양01"]
    return [
        _message(
            room_key="all_staff",
            day_index=day_index,
            slot_number=1,
            hour=8,
            minute=0,
            sender_code="관리자01" if day_index % 2 == 0 else "사무01",
            service_type="all",
            body=notices[day_index % len(notices)],
        ),
        _message(
            room_key="all_staff",
            day_index=day_index,
            slot_number=2,
            hour=8,
            minute=12,
            sender_code=responders[day_index % len(responders)],
            service_type="all",
            body="확인했습니다.",
            reply_to_message_id=notice_id,
        ),
        _message(
            room_key="all_staff",
            day_index=day_index,
            slot_number=3,
            hour=8,
            minute=18,
            sender_code=responders[(day_index + 2) % len(responders)],
            service_type="all",
            body="네, 일정 확인했습니다.",
            reply_to_message_id=notice_id,
        ),
    ]


def _professional_messages(day_index: int) -> list[dict[str, Any]]:
    resident = f"어르{1 + (day_index % 5):04d}"
    observations = (
        "오늘 오전 식사량이 평소보다 적었다는 현장 보고가 있어 함께 확인 부탁드립니다.",
        "이동할 때 보행 속도가 느렸다는 보고가 있어 활동 전 상태 확인 부탁드립니다.",
        "오전 활동 참여를 잠시 거부했다가 직원과 대화 후 참여했다는 보고입니다.",
        "생활 중 손등이 건조해 보였다는 관찰이 있어 원문 확인 부탁드립니다.",
        "오전에는 피곤해 보였으나 휴식 뒤 표정이 안정됐다는 보고입니다.",
    )
    day_number = day_index + 1
    parent_id = _message_id("professional_collab", day_number, 1)
    return [
        _message(
            room_key="professional_collab",
            day_index=day_index,
            slot_number=1,
            hour=9,
            minute=10,
            sender_code="사복01",
            resident_code=resident,
            service_type="daycare",
            body=f"{resident} {observations[day_index % len(observations)]}",
        ),
        _message(
            room_key="professional_collab",
            day_index=day_index,
            slot_number=2,
            hour=9,
            minute=35,
            sender_code="간호01",
            resident_code=resident,
            service_type="daycare",
            body="현장 원문과 현재 상태를 확인하겠습니다. 확인 전에는 상태를 확정하지 않겠습니다.",
            reply_to_message_id=parent_id,
        ),
        _message(
            room_key="professional_collab",
            day_index=day_index,
            slot_number=3,
            hour=10,
            minute=10,
            sender_code="영양01",
            resident_code=resident,
            service_type="daycare",
            body="식사량 기록을 확인하고 다음 배식 때 섭취 상태를 이어서 보겠습니다.",
            reply_to_message_id=parent_id,
        ),
        _message(
            room_key="professional_collab",
            day_index=day_index,
            slot_number=4,
            hour=13,
            minute=20,
            sender_code="작치01",
            resident_code=resident,
            service_type="daycare",
            body="오후 활동은 부담이 적은 순서로 안내하고 참여 반응을 남기겠습니다.",
            reply_to_message_id=parent_id,
        ),
        _message(
            room_key="professional_collab",
            day_index=day_index,
            slot_number=5,
            hour=15,
            minute=40,
            sender_code="사복01",
            resident_code=resident,
            service_type="daycare",
            body="오후 현장 보고까지 확인한 뒤 보호자 전달이 필요한 내용을 구분하겠습니다.",
            reply_to_message_id=parent_id,
        ),
    ]


def _office_messages(day_index: int) -> list[dict[str, Any]]:
    subjects = (
        "합성 교육 확인서",
        "가상 상담 접수 메모",
        "합성 근무 변경 신청",
        "가상 비품 요청서",
        "합성 일정 확인표",
    )
    subject = subjects[day_index % len(subjects)]
    day_number = day_index + 1
    first_id = _message_id("office_admin", day_number, 1)
    return [
        _message(
            room_key="office_admin",
            day_index=day_index,
            slot_number=1,
            hour=8,
            minute=45,
            sender_code="사복01" if day_index % 2 == 0 else "사복02",
            service_type="administrative",
            body=f"{subject} 접수 여부 확인 부탁드립니다.",
        ),
        _message(
            room_key="office_admin",
            day_index=day_index,
            slot_number=2,
            hour=10,
            minute=5,
            sender_code="사무01",
            service_type="administrative",
            body="네, 접수 내역 확인했습니다.",
            reply_to_message_id=first_id,
        ),
        _message(
            room_key="office_admin",
            day_index=day_index,
            slot_number=3,
            hour=14,
            minute=20,
            sender_code="관리자01",
            service_type="administrative",
            body="담당자 검토 상태를 확인해 결과를 남겨주세요.",
            reply_to_message_id=first_id,
        ),
        _message(
            room_key="office_admin",
            day_index=day_index,
            slot_number=4,
            hour=16,
            minute=50,
            sender_code="사무01",
            service_type="administrative",
            body="처리 상태를 반영했습니다. 추가 회신이 필요한 항목은 대기 상태로 남겼습니다.",
            reply_to_message_id=first_id,
        ),
    ]


def _homecare_messages(day_index: int) -> list[dict[str, Any]]:
    resident = f"어르{2001 + (day_index % 3)}"
    day_number = day_index + 1
    notice_id = _message_id("homecare_field", day_number, 1)
    report_id = _message_id("homecare_field", day_number, 3)
    caregiver = "요보03" if day_index % 2 == 0 else "요보04"
    return [
        _message(
            room_key="homecare_field",
            day_index=day_index,
            slot_number=1,
            hour=7,
            minute=50,
            sender_code="사복02",
            service_type="homecare",
            body="오늘 방문 일정과 기록 제출 안내를 확인해주세요.",
        ),
        _message(
            room_key="homecare_field",
            day_index=day_index,
            slot_number=2,
            hour=8,
            minute=5,
            sender_code=caregiver,
            service_type="homecare",
            body="네, 확인했습니다.",
            reply_to_message_id=notice_id,
        ),
        _message(
            room_key="homecare_field",
            day_index=day_index,
            slot_number=3,
            hour=13,
            minute=30,
            sender_code=caregiver,
            resident_code=resident,
            service_type="homecare",
            body=f"{resident} 방문을 마쳤습니다. 일정 변경 없이 제공 내용을 기록했습니다.",
        ),
        _message(
            room_key="homecare_field",
            day_index=day_index,
            slot_number=4,
            hour=17,
            minute=45,
            sender_code="사복02",
            resident_code=resident,
            service_type="homecare",
            body="기록 제출 확인했습니다. 추가 확인사항은 없습니다.",
            reply_to_message_id=report_id,
        ),
    ]


CORE_OVERRIDES: dict[str, dict[str, Any]] = {
    "PRES-DC-07-01": {
        "scenario_id": "SCN-MEAL-ACTIVITY",
        "sender_code": "요보01",
        "resident_code": "어르0001",
        "body": "어르0001 아침 식사는 절반 정도 드셨고 평소보다 말수가 적었습니다. 오전 상태 확인 부탁드립니다.",
    },
    "PRES-PRO-07-01": {
        "scenario_id": "SCN-MEAL-ACTIVITY",
        "resident_code": "어르0001",
        "body": "어르0001 식사량과 활동 반응이 함께 달라졌다는 현장 보고입니다. 직종별 확인 의견 부탁드립니다.",
    },
    "PRES-PRO-07-02": {
        "scenario_id": "SCN-MEAL-ACTIVITY",
        "resident_code": "어르0001",
        "body": "현재 불편 호소 여부와 기초 상태를 확인하겠습니다. 확인 전에는 원인을 판단하지 않겠습니다.",
    },
    "PRES-PRO-07-03": {
        "scenario_id": "SCN-MEAL-ACTIVITY",
        "resident_code": "어르0001",
        "body": "점심은 부담이 적은 구성으로 제공하고 실제 섭취량을 다시 기록하겠습니다.",
    },
    "PRES-PRO-07-04": {
        "scenario_id": "SCN-MEAL-ACTIVITY",
        "resident_code": "어르0001",
        "body": "오후 활동은 선택지를 먼저 보여드리고 참여 의사를 다시 확인하겠습니다.",
    },
    "PRES-DC-07-06": {
        "scenario_id": "SCN-MEAL-ACTIVITY",
        "sender_code": "요보02",
        "resident_code": "어르0001",
        "body": "어르0001 점심은 2/3 정도 드셨고 직원과 대화 후 오후 활동에 20분 참여했습니다.",
        "reply_to_message_id": "PRES-DC-07-01",
    },
    "PRES-PRO-07-05": {
        "scenario_id": "SCN-MEAL-ACTIVITY",
        "resident_code": "어르0001",
        "body": "오후 현장 보고까지 확인했습니다. 보호자에게는 관찰 사실과 오늘의 지원 내용을 구분해 전달하겠습니다.",
    },
    "PRES-HC-08-01": {
        "scenario_id": "SCN-SCHEDULE-CORRECTION",
        "body": "어르2001 방문 시작 시각 변경 요청이 접수되었습니다. 정확한 시각을 확인 중입니다.",
        "resident_code": "어르2001",
    },
    "PRES-HC-08-02": {
        "scenario_id": "SCN-SCHEDULE-CORRECTION",
        "body": "확정 안내 전까지 기존 일정으로 알고 있겠습니다.",
        "reply_to_message_id": "PRES-HC-08-01",
    },
    "PRES-OFF-08-01": {
        "scenario_id": "SCN-SCHEDULE-CORRECTION",
        "sender_code": "사복02",
        "body": "어르2001 방문 일정 변경 요청을 접수했습니다. 보호자 확인 시각과 담당자 가능 시간을 대조해주세요.",
        "resident_code": "어르2001",
    },
    "PRES-OFF-08-02": {
        "scenario_id": "SCN-SCHEDULE-CORRECTION",
        "body": "담당자 가능 시간을 확인했습니다. 최종 시각은 회신 후 안내하겠습니다.",
        "reply_to_message_id": "PRES-OFF-08-01",
    },
    "PRES-HC-08-03": {
        "scenario_id": "SCN-SCHEDULE-CORRECTION",
        "sender_code": "사복02",
        "resident_code": "어르2001",
        "body": "어르2001 내일 방문은 13시 30분으로 최종 확인됐습니다. 이전 안내 대신 이 메시지를 확인해주세요.",
    },
    "PRES-OFF-08-03": {
        "scenario_id": "SCN-SCHEDULE-CORRECTION",
        "body": "최종 방문 시각을 일정표에 반영했고 담당자에게 다시 전달했습니다.",
        "reply_to_message_id": "PRES-OFF-08-01",
    },
    "PRES-HC-08-04": {
        "scenario_id": "SCN-SCHEDULE-CORRECTION",
        "body": "13시 30분 방문 일정 확인했습니다.",
        "reply_to_message_id": "PRES-HC-08-03",
    },
    "PRES-DC-09-02": {
        "scenario_id": "SCN-ATTACHMENT-HANDOVER",
        "resident_code": "어르0002",
        "body": "어르0002 합성 관찰 메모를 첨부했습니다. 흐린 숫자는 원본을 보고 다시 확인해주세요.",
    },
    "PRES-PRO-09-01": {
        "scenario_id": "SCN-ATTACHMENT-HANDOVER",
        "sender_code": "간호01",
        "resident_code": "어르0002",
        "body": "어르0002 첨부 메모의 수치와 작성 시각을 확인하겠습니다. 판독 전에는 확정하지 않겠습니다.",
    },
    "PRES-PRO-09-02": {
        "scenario_id": "SCN-ATTACHMENT-HANDOVER",
        "resident_code": "어르0002",
        "body": "원본에서 확인되는 관찰 사실만 정리하고 불확실한 값은 재확인 항목으로 남기겠습니다.",
    },
    "PRES-PRO-09-03": {
        "scenario_id": "SCN-ATTACHMENT-HANDOVER",
        "resident_code": "어르0002",
        "body": "식사 관련 표기는 현장 기록과 대조한 뒤 다음 배식 참고사항으로 구분하겠습니다.",
    },
    "PRES-DC-09-04": {
        "scenario_id": "SCN-ATTACHMENT-HANDOVER",
        "sender_code": "요보02",
        "resident_code": "어르0002",
        "body": "원본 메모를 다시 확인했습니다. 흐린 부분은 담당자가 직접 수정해서 남겼습니다.",
        "reply_to_message_id": "PRES-DC-09-02",
    },
    "PRES-PRO-09-05": {
        "scenario_id": "SCN-ATTACHMENT-HANDOVER",
        "resident_code": "어르0002",
        "body": "직원 확인이 끝난 문장과 아직 판단할 수 없는 항목을 구분해 기록 초안으로 넘기겠습니다.",
    },
    "PRES-ALL-11-01": {
        "scenario_id": "SCN-OFFICE-DOCUMENT",
        "body": "합성 교육 참석 확인서 제출 안내입니다. 오늘 16시까지 사무행정 지원실로 전달해주세요.",
    },
    "PRES-OFF-11-01": {
        "scenario_id": "SCN-OFFICE-DOCUMENT",
        "body": "합성 교육 참석 확인서가 접수되었습니다. 파일명과 제출자 코드를 확인해주세요.",
    },
    "PRES-OFF-11-02": {
        "scenario_id": "SCN-OFFICE-DOCUMENT",
        "body": "접수 파일 확인했습니다. 한 건은 서명란이 비어 있어 회신 대기로 두겠습니다.",
        "reply_to_message_id": "PRES-OFF-11-01",
    },
    "PRES-OFF-11-03": {
        "scenario_id": "SCN-OFFICE-DOCUMENT",
        "body": "완료된 건과 회신 대기 건을 구분해 표시해주세요.",
        "reply_to_message_id": "PRES-OFF-11-01",
    },
    "PRES-OFF-11-04": {
        "scenario_id": "SCN-OFFICE-DOCUMENT",
        "body": "완료 3건, 회신 대기 1건으로 정리했습니다. 실제 제출 자료가 아닌 합성 시연 문서입니다.",
        "reply_to_message_id": "PRES-OFF-11-01",
    },
    "PRES-ALL-12-01": {
        "scenario_id": "SCN-HOMECARE-NOTICE",
        "body": "방문요양 기록 제출 안내입니다. 현장 업무가 끝난 뒤 당일 기록 여부를 확인해주세요.",
    },
    "PRES-ALL-12-02": {
        "scenario_id": "SCN-HOMECARE-NOTICE",
        "sender_code": "요보03",
        "body": "확인했습니다.",
        "reply_to_message_id": "PRES-ALL-12-01",
    },
    "PRES-ALL-12-03": {
        "scenario_id": "SCN-HOMECARE-NOTICE",
        "sender_code": "요보04",
        "body": "네, 오늘 기록 확인하겠습니다.",
        "reply_to_message_id": "PRES-ALL-12-01",
    },
    "PRES-HC-12-03": {
        "scenario_id": "SCN-HOMECARE-NOTICE",
        "resident_code": "어르2003",
        "body": "어르2003 방문을 마쳤고 제공 내용을 기록했습니다. 일정 변경은 없었습니다.",
    },
    "PRES-HC-12-04": {
        "scenario_id": "SCN-HOMECARE-NOTICE",
        "resident_code": "어르2003",
        "body": "기록 제출 확인했습니다. 추가 확인사항은 없습니다.",
        "reply_to_message_id": "PRES-HC-12-03",
    },
}

SCENARIO_TITLES = {
    "SCN-MEAL-ACTIVITY": "식사와 활동 변화의 직종별 후속 확인",
    "SCN-SCHEDULE-CORRECTION": "방문 일정 변경과 최종 정정",
    "SCN-ATTACHMENT-HANDOVER": "합성 첨부 메모의 판독과 직원 확인",
    "SCN-OFFICE-DOCUMENT": "사무서류 접수와 회신 대기 구분",
    "SCN-HOMECARE-NOTICE": "방문요양 공지와 현장 회신",
}


ATTACHMENT_SPECS: tuple[dict[str, str], ...] = (
    {
        "attachment_id": "PRES-ATT-001",
        "message_id": "PRES-DC-02-03",
        "kind": "image",
        "original_name": "presentation-coded-daycare-water-note-01.jpg",
        "source_asset_name": "coded-handwriting-source-01.jpg",
        "mime_type": "image/jpeg",
        "extracted_text": "어르0003\n오후 2시 10분 물 200mL 제공\n오후 4시 다시 확인 예정\n오후 4시 5분 잔량 없이 200mL 섭취 완료",
    },
    {
        "attachment_id": "PRES-ATT-002",
        "message_id": "PRES-PRO-04-01",
        "kind": "image",
        "original_name": "presentation-coded-professional-vital-01.jpg",
        "source_asset_name": "coded-handwriting-source-02.jpg",
        "mime_type": "image/jpeg",
        "extracted_text": "어르0004\n오전 9시 10분 혈압 170/90 측정\n10분 안정 후 오전 9시 20분 재측정 120/70\n일어날 때 잠깐 휘청하여 이동 시 부축함\n넘어지지는 않음",
    },
    {
        "attachment_id": "PRES-ATT-003",
        "message_id": "PRES-DC-07-06",
        "kind": "image",
        "original_name": "presentation-coded-daycare-meal-01.jpg",
        "source_asset_name": "coded-handwriting-source-03.jpg",
        "mime_type": "image/jpeg",
        "extracted_text": "어르0001\n아침밥 1/2 정도 섭취\n국물은 조금 섭취하고 입맛이 없다고 말함\n오전에는 표정이 어둡고 프로그램 참여를 거부함\n11시 50분 점심은 소량 섭취\n직원과 대화 후 표정이 안정되고 오후 프로그램 참여",
    },
    {
        "attachment_id": "PRES-ATT-004",
        "message_id": "PRES-DC-04-04",
        "kind": "image",
        "original_name": "presentation-coded-daycare-activity-01.jpg",
        "source_asset_name": "coded-handwriting-source-04.jpg",
        "mime_type": "image/jpeg",
        "extracted_text": "어르0001\n오전 프로그램 참여를 거부함\n11시 50분 점심은 소량 섭취\n직원과 대화 후 표정이 안정되고 오후 프로그램 참여",
    },
    {
        "attachment_id": "PRES-ATT-005",
        "message_id": "PRES-DC-01-03",
        "kind": "image",
        "original_name": "presentation-coded-daycare-bowel-01.jpg",
        "source_asset_name": "coded-handwriting-source-05.jpg",
        "mime_type": "image/jpeg",
        "extracted_text": "어르0002\n오전 9시 이틀째 배변 없음\n배가 아프지는 않다고 말함\n물을 제공하고 점심 식사 후 복도를 10분 정도 걸음\n오후 3시 20분 배변 확인\n배변 후 불편감 없다고 말함\n계속 관찰하기로 함",
    },
    {
        "attachment_id": "PRES-ATT-006",
        "message_id": "PRES-DC-03-03",
        "kind": "audio",
        "original_name": "presentation-coded-daycare-water-01.wav",
        "source_asset_name": "coded-voice-water-01.wav",
        "mime_type": "audio/wav",
        "extracted_text": "어르0004 오후 3시 10분 물 180밀리리터 제공했습니다. 오후 5시 잔량 없이 모두 섭취한 것을 확인했습니다.",
    },
    {
        "attachment_id": "PRES-ATT-007",
        "message_id": "PRES-PRO-02-01",
        "kind": "image",
        "original_name": "presentation-coded-professional-observation-01.png",
        "source_asset_name": "coded-handwriting-vital-01.png",
        "mime_type": "image/png",
        "extracted_text": "어르0002\n8월 18일 09:25\n혈압 146/84 확인\n10분 안정 후 138/80 재확인\n어지럼 호소 없음",
    },
    {
        "attachment_id": "PRES-ATT-008",
        "message_id": "PRES-PRO-09-05",
        "kind": "document",
        "original_name": "presentation-coded-professional-review-01.txt",
        "mime_type": "text/plain",
        "inline_content": "[합성 검토 메모]\n대상자: 어르0002\n확인 완료: 식사 관찰 문장\n재확인 필요: 흐린 수치 1건\n공식 기록 여부: 아니요\n",
        "extracted_text": "어르0002 확인된 문장과 재확인 항목을 분리한 합성 검토 메모",
    },
    {
        "attachment_id": "PRES-ATT-009",
        "message_id": "PRES-ALL-04-01",
        "kind": "document",
        "original_name": "presentation-coded-allstaff-safety-01.txt",
        "mime_type": "text/plain",
        "inline_content": "[합성 공용공간 안전점검]\n통행로: 확인\n손잡이 주변: 확인\n이동 보조기구 위치: 확인\n",
        "extracted_text": "공용공간 안전점검 합성 체크 메모",
    },
    {
        "attachment_id": "PRES-ATT-010",
        "message_id": "PRES-ALL-11-01",
        "kind": "document",
        "original_name": "presentation-coded-allstaff-training-01.txt",
        "mime_type": "text/plain",
        "inline_content": "[합성 교육 안내]\n제출 기한: 오늘 16시\n제출 위치: 사무행정 지원실\n미제출 시 담당자에게 회신\n",
        "extracted_text": "합성 교육 참석 확인서 제출 안내",
    },
    {
        "attachment_id": "PRES-ATT-011",
        "message_id": "PRES-OFF-06-01",
        "kind": "document",
        "original_name": "presentation-coded-office-register-01.txt",
        "mime_type": "text/plain",
        "inline_content": "[합성 서류 접수 목록]\nPRES-DOC-001: 완료\nPRES-DOC-002: 회신 대기\nPRES-DOC-003: 완료\n",
        "extracted_text": "합성 서류 접수 목록, 완료와 회신 대기 구분",
    },
    {
        "attachment_id": "PRES-ATT-012",
        "message_id": "PRES-OFF-11-04",
        "kind": "document",
        "original_name": "presentation-coded-office-training-01.txt",
        "mime_type": "text/plain",
        "inline_content": "[합성 교육 확인서 집계]\n완료: 3건\n회신 대기: 1건\n실제 제출 자료: 아니요\n",
        "extracted_text": "합성 교육 확인서 완료 3건, 회신 대기 1건",
    },
    {
        "attachment_id": "PRES-ATT-013",
        "message_id": "PRES-HC-08-03",
        "kind": "document",
        "original_name": "presentation-coded-homecare-schedule-01.txt",
        "mime_type": "text/plain",
        "inline_content": "[합성 방문 일정 정정]\n대상자: 어르2001\n최종 방문 시각: 13시 30분\n이전 안내: 사용하지 않음\n",
        "extracted_text": "어르2001 내일 방문은 13시 30분으로 최종 확인됐습니다.",
    },
    {
        "attachment_id": "PRES-ATT-014",
        "message_id": "PRES-HC-12-03",
        "kind": "document",
        "original_name": "presentation-coded-homecare-log-01.txt",
        "mime_type": "text/plain",
        "inline_content": "[합성 방문 기록 확인]\n대상자: 어르2003\n방문 완료: 예\n일정 변경: 없음\n추가 확인: 없음\n",
        "extracted_text": "어르2003 방문 완료 및 제공 내용 기록 확인",
    },
)


def _build_messages() -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for day_index in range(14):
        messages.extend(_daycare_messages(day_index))
        messages.extend(_all_staff_messages(day_index))
        messages.extend(_professional_messages(day_index))
        messages.extend(_office_messages(day_index))
        messages.extend(_homecare_messages(day_index))
    for item in messages:
        override = CORE_OVERRIDES.get(item["message_id"])
        if override:
            item.update(override)
    return sorted(messages, key=lambda item: (item["created_at"], item["message_id"]))


def _build_scenarios(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scenarios = []
    for scenario_id, title in SCENARIO_TITLES.items():
        linked = [item for item in messages if item.get("scenario_id") == scenario_id]
        linked.sort(key=lambda item: (item["created_at"], item["message_id"]))
        scenarios.append(
            {
                "scenario_id": scenario_id,
                "title": title,
                "message_ids": [item["message_id"] for item in linked],
                "synthetic_origin": SYNTHETIC_ORIGIN,
            }
        )
    return scenarios


def _build_attachments() -> list[dict[str, Any]]:
    return [
        {
            **item,
            "synthetic_origin": SYNTHETIC_ORIGIN,
        }
        for item in ATTACHMENT_SPECS
    ]


def build_fixture() -> dict[str, Any]:
    messages = _build_messages()
    return deepcopy(
        {
            "schema_version": FIXTURE_VERSION,
            "display_notice": DISPLAY_NOTICE,
            "window": WINDOW,
            "safety_contract": SAFETY_CONTRACT,
            "staff": STAFF,
            "residents": RESIDENTS,
            "rooms": ROOMS,
            "messages": messages,
            "attachments": _build_attachments(),
            "scenarios": _build_scenarios(messages),
        }
    )


def validate_fixture(fixture: dict[str, Any]) -> None:
    if fixture.get("schema_version") != FIXTURE_VERSION:
        raise ValueError("발표 합성자료 계약 버전이 올바르지 않습니다.")
    if fixture.get("display_notice") != DISPLAY_NOTICE:
        raise ValueError("발표 합성자료 안내 문구가 올바르지 않습니다.")
    if fixture.get("safety_contract") != SAFETY_CONTRACT:
        raise ValueError("발표 합성자료 안전 계약이 올바르지 않습니다.")

    staff_codes = [item["code"] for item in fixture["staff"]]
    resident_codes = [item["code"] for item in fixture["residents"]]
    guardian_codes = [code for item in fixture["residents"] for code in item["guardians"]]
    if len(staff_codes) != len(set(staff_codes)) or not all(STAFF_CODE_RE.fullmatch(code) for code in staff_codes):
        raise ValueError("직원 코드가 중복되었거나 허용된 코드 형식이 아닙니다.")
    if len(resident_codes) != len(set(resident_codes)) or not all(RESIDENT_CODE_RE.fullmatch(code) for code in resident_codes):
        raise ValueError("대상자 코드가 중복되었거나 허용된 코드 형식이 아닙니다.")
    if len(guardian_codes) != len(set(guardian_codes)) or not all(GUARDIAN_CODE_RE.fullmatch(code) for code in guardian_codes):
        raise ValueError("보호자 코드가 중복되었거나 허용된 코드 형식이 아닙니다.")

    rooms = {item["key"]: item for item in fixture["rooms"]}
    if len(rooms) != 5:
        raise ValueError("발표 합성자료에는 정확히 5개 방이 필요합니다.")
    staff_set = set(staff_codes)
    for room in rooms.values():
        if not set(room["member_codes"]).issubset(staff_set):
            raise ValueError("대화방에 존재하지 않는 직원 코드가 연결되었습니다.")

    residents = {item["code"]: item for item in fixture["residents"]}
    messages = {item["message_id"]: item for item in fixture["messages"]}
    if len(messages) != len(fixture["messages"]):
        raise ValueError("합성 메시지 ID가 중복되었습니다.")
    if not 300 <= len(messages) <= 450:
        raise ValueError("발표 합성 메시지는 300건 이상 450건 이하여야 합니다.")
    window_start = datetime.fromisoformat(fixture["window"]["start"])
    window_end = datetime.fromisoformat(fixture["window"]["end"])
    for item in messages.values():
        room = rooms.get(item["room_key"])
        if room is None:
            raise ValueError("합성 메시지가 존재하지 않는 방을 참조합니다.")
        if item["sender_code"] not in room["member_codes"]:
            raise ValueError("합성 메시지 발신자가 대화방 참여자가 아닙니다.")
        if item.get("synthetic_origin") != SYNTHETIC_ORIGIN:
            raise ValueError("합성 메시지 출처 계약이 올바르지 않습니다.")
        created_at = datetime.fromisoformat(item["created_at"])
        if not window_start <= created_at <= window_end:
            raise ValueError("합성 메시지 시각이 허용된 발표 기간 밖입니다.")
        resident_code = item.get("resident_code")
        if resident_code is not None and resident_code not in residents:
            raise ValueError("합성 메시지가 존재하지 않는 대상자를 참조합니다.")
        if item["room_key"] in {"daycare_field", "professional_collab"}:
            if resident_code is None or residents[resident_code]["service_type"] != "daycare":
                raise ValueError("주간보호 메시지의 대상자 서비스 유형이 올바르지 않습니다.")
        if item["room_key"] == "homecare_field" and resident_code is not None:
            if residents[resident_code]["service_type"] != "homecare":
                raise ValueError("방문요양 메시지의 대상자 서비스 유형이 올바르지 않습니다.")
        reply_id = item.get("reply_to_message_id")
        if reply_id:
            parent = messages.get(reply_id)
            if parent is None:
                raise ValueError("합성 답글이 존재하지 않는 원문을 참조합니다.")
            if parent["room_key"] != item["room_key"]:
                raise ValueError("합성 답글과 원문이 서로 다른 방에 있습니다.")
            if datetime.fromisoformat(parent["created_at"]) >= created_at:
                raise ValueError("합성 답글은 원문보다 뒤에 작성되어야 합니다.")

    room_counts = Counter(item["room_key"] for item in messages.values())
    required_minimums = {
        "daycare_field": 90,
        "all_staff": 30,
        "professional_collab": 60,
        "office_admin": 30,
        "homecare_field": 30,
    }
    if any(room_counts[key] < minimum for key, minimum in required_minimums.items()):
        raise ValueError("대화방별 합성 메시지 최소 분량을 충족하지 못했습니다.")

    scenario_ids = [item["scenario_id"] for item in fixture["scenarios"]]
    if len(scenario_ids) != len(set(scenario_ids)) or set(scenario_ids) != set(SCENARIO_TITLES):
        raise ValueError("합성 시나리오 구성이 올바르지 않습니다.")
    for scenario in fixture["scenarios"]:
        linked = [messages.get(message_id) for message_id in scenario["message_ids"]]
        if len(linked) < 3 or any(item is None for item in linked):
            raise ValueError("합성 시나리오에 필요한 메시지 연결이 부족합니다.")
        assert all(item is not None for item in linked)
        if any(item.get("scenario_id") != scenario["scenario_id"] for item in linked):
            raise ValueError("합성 시나리오와 메시지 연결이 일치하지 않습니다.")
        if linked != sorted(linked, key=lambda item: (item["created_at"], item["message_id"])):
            raise ValueError("합성 시나리오 메시지가 시간순으로 정렬되지 않았습니다.")

    attachments = fixture["attachments"]
    if not 12 <= len(attachments) <= 18:
        raise ValueError("발표 합성 첨부파일은 12건 이상 18건 이하여야 합니다.")
    attachment_ids = [item["attachment_id"] for item in attachments]
    original_names = [item["original_name"] for item in attachments]
    if len(attachment_ids) != len(set(attachment_ids)):
        raise ValueError("합성 첨부파일 ID가 중복되었습니다.")
    if len(original_names) != len(set(original_names)):
        raise ValueError("합성 첨부파일 이름이 중복되었습니다.")
    allowed_kinds = {
        "image": {"image/jpeg", "image/png"},
        "audio": {"audio/wav"},
        "document": {"text/plain"},
    }
    for item in attachments:
        if item["message_id"] not in messages:
            raise ValueError("합성 첨부파일이 존재하지 않는 메시지를 참조합니다.")
        if not item["original_name"].startswith("presentation-coded-"):
            raise ValueError("합성 첨부파일 이름은 발표용 코드 접두어를 사용해야 합니다.")
        if item.get("synthetic_origin") != SYNTHETIC_ORIGIN:
            raise ValueError("합성 첨부파일 출처 계약이 올바르지 않습니다.")
        if item["kind"] not in allowed_kinds or item["mime_type"] not in allowed_kinds[item["kind"]]:
            raise ValueError("합성 첨부파일 형식과 MIME 유형이 일치하지 않습니다.")
        has_source = bool(item.get("source_asset_name"))
        has_inline = bool(item.get("inline_content"))
        if has_source == has_inline:
            raise ValueError("합성 첨부파일은 재사용 자산 또는 인라인 문서 중 하나만 사용해야 합니다.")


def public_manifest(fixture: dict[str, Any]) -> dict[str, Any]:
    room_counts = Counter(item["room_key"] for item in fixture["messages"])
    service_counts = Counter(item["service_type"] for item in fixture["messages"])
    manifest = {
        "schema_version": "presentation_five_room_synthetic_manifest_v1",
        "fixture_version": fixture["schema_version"],
        "window": fixture["window"],
        "room_names": [item["name"] for item in fixture["rooms"]],
        "staff_count": len(fixture["staff"]),
        "resident_count": len(fixture["residents"]),
        "message_count": len(fixture["messages"]),
        "room_message_counts": dict(sorted(room_counts.items())),
        "service_type_counts": dict(sorted(service_counts.items())),
        "scenario_count": len(fixture["scenarios"]),
        "attachment_count": len(fixture["attachments"]),
        "safety_contract": fixture["safety_contract"],
    }
    asset_rows = []
    for item in fixture["attachments"]:
        if "size_bytes" in item and "sha256" in item:
            asset_rows.append(
                {
                    "name": item["original_name"],
                    "size_bytes": item["size_bytes"],
                    "sha256": item["sha256"],
                }
            )
    if asset_rows:
        manifest["assets"] = sorted(asset_rows, key=lambda item: item["name"])
    return manifest


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def export_fixture(output_dir: str | Path) -> dict[str, Path]:
    destination = Path(output_dir).resolve()
    assets_dir = destination / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    fixture = build_fixture()
    for item in fixture["attachments"]:
        source_asset_name = item.get("source_asset_name")
        if source_asset_name:
            source_path = ASSET_SOURCE_DIR / source_asset_name
            if not source_path.is_file():
                raise FileNotFoundError(f"합성 원본 자산이 없습니다: {source_asset_name}")
            content = source_path.read_bytes()
        else:
            content = item["inline_content"].encode("utf-8")
        item["size_bytes"] = len(content)
        item["sha256"] = hashlib.sha256(content).hexdigest()
        destination_path = assets_dir / item["original_name"]
        if source_asset_name:
            shutil.copyfile(source_path, destination_path)
        else:
            destination_path.write_bytes(content)

    validate_fixture(fixture)
    fixture_path = destination / "fixture.json"
    manifest_path = destination / "manifest.json"
    fixture_bytes = _json_bytes(fixture)
    fixture_path.write_bytes(fixture_bytes)

    manifest = public_manifest(fixture)
    manifest["fixture_sha256"] = hashlib.sha256(fixture_bytes).hexdigest()
    manifest_path.write_bytes(_json_bytes(manifest))
    return {
        "output_dir": destination,
        "fixture_path": fixture_path,
        "manifest_path": manifest_path,
    }
