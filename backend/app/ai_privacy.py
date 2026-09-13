from __future__ import annotations

import re

from .ai_system_schemas import (
    AiPrivacyClassificationResponse,
    AiPrivacyClassificationRequest,
)


_DIRECT_IDENTIFIER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?<!\d)\d{6}-?[1-4]\d{6}(?!\d)"),
    re.compile(r"(?<!\d)01[016789][ -]?\d{3,4}[ -]?\d{4}(?!\d)"),
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"(?:성명|이름|수급자|입소자|보호자|담당자)\s*[:：]\s*[가-힣]{2,5}"),
    re.compile(r"[가-힣]{2,5}\s*(?:어르신|수급자|보호자)\b"),
)
_CARE_TERMS: frozenset[str] = frozenset(
    {
        "어르신",
        "수급자",
        "입소자",
        "보호자",
        "장기요양",
        "급여제공",
        "돌봄",
        "케어",
        "복약",
        "투약",
        "진단",
        "혈압",
        "혈당",
        "체온",
        "산소포화도",
        "욕창",
        "낙상",
        "배변",
        "배뇨",
        "간호",
        "상담기록",
        "요양보호사",
    }
)


def classify_ai_input(
    request: AiPrivacyClassificationRequest,
) -> AiPrivacyClassificationResponse:
    text = request.text.strip()
    direct_identifier = any(pattern.search(text) for pattern in _DIRECT_IDENTIFIER_PATTERNS)
    lowered = text.lower()
    care_context = any(term.lower() in lowered for term in _CARE_TERMS)

    if request.contains_real_data:
        return AiPrivacyClassificationResponse(
            classification="personal_or_care_record",
            external_allowed=False,
            reasons=[
                "실제 개인정보 또는 돌봄기록으로 표시된 입력은 외부 전송을 차단합니다.",
                "API 키 설정 여부와 관계없이 로컬·내부 서버 또는 규칙 경로만 사용할 수 있습니다.",
            ],
            direct_identifier_detected=direct_identifier,
            care_context_detected=care_context,
            user_confirmation_accepted=False,
        )
    if direct_identifier:
        return AiPrivacyClassificationResponse(
            classification="personal_or_care_record",
            external_allowed=False,
            reasons=[
                "직접 식별정보 형식이 감지되어 외부 전송을 차단합니다.",
                "비식별 확인을 선택했더라도 식별정보가 남아 있으면 승인을 인정하지 않습니다.",
            ],
            direct_identifier_detected=True,
            care_context_detected=care_context,
            user_confirmation_accepted=False,
        )
    if request.deidentified_confirmed_by_user:
        return AiPrivacyClassificationResponse(
            classification="deidentified_confirmed",
            external_allowed=True,
            reasons=[
                "직접 식별정보가 감지되지 않았고 사용자가 비식별 자료임을 확인했습니다.",
                "활성화되고 사전검증을 통과한 외부 공급자만 후보가 될 수 있습니다.",
            ],
            direct_identifier_detected=False,
            care_context_detected=care_context,
            user_confirmation_accepted=True,
        )
    if care_context:
        return AiPrivacyClassificationResponse(
            classification="possibly_sensitive",
            external_allowed=False,
            reasons=[
                "돌봄·건강 관련 문맥이 있으나 비식별 확인이 없어 외부 전송을 차단합니다.",
                "판정이 불확실한 자료는 로컬·내부 서버 또는 규칙 경로만 사용합니다.",
            ],
            direct_identifier_detected=False,
            care_context_detected=True,
            user_confirmation_accepted=False,
        )
    return AiPrivacyClassificationResponse(
        classification="general_non_sensitive",
        external_allowed=True,
        reasons=[
            "직접 식별정보와 돌봄 민감 문맥이 감지되지 않았습니다.",
            "외부 사용은 사용자 설정과 공급자 사전검증을 추가로 통과해야 합니다.",
        ],
        direct_identifier_detected=False,
        care_context_detected=False,
        user_confirmation_accepted=False,
    )
