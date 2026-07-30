from __future__ import annotations

import re
from typing import Any


PROTOTYPE_GENERATOR = "prototype-rule-v1"
DAILY_DOCUMENT_TYPES = {
    "care_service_record",
    "nursing_log",
    "consultation_log",
    "physical_restraint_log",
    "program_log",
}


def _normalize_text(value: str) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    replacements = {
        "식사량이 적으심": "식사량이 적음",
        "드심": "드심",
        "안드심": "안 드심",
        "못드심": "못 드심",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|[\r\n]+", text)
    return [part.strip(" \t-•") for part in parts if part.strip(" \t-•")]


def _matching_sentences(text: str, terms: tuple[str, ...]) -> list[str]:
    return list(
        dict.fromkeys(
            sentence
            for sentence in _sentences(text)
            if any(term in sentence for term in terms)
        )
    )


def _nutrition_matches(text: str) -> list[str]:
    """식사 시간과 물을 실제 섭취 문맥에서만 영양 근거로 인정합니다."""

    direct_terms = ("섭취", "수분", "식욕", "체중")
    meal_terms = ("아침", "점심", "저녁")
    meal_consumption_terms = (
        *direct_terms,
        "드심",
        "드셨",
        "먹",
        "반찬",
        "죽",
        "간식",
        "식판",
        "완식",
        "삼킴",
        "식사량",
        "절반",
        "평소보다",
        "적게",
        "많이",
    )
    matched = [term for term in direct_terms if term in text]
    for sentence in _sentences(text):
        has_meal_consumption = any(
            term in sentence for term in meal_consumption_terms
        ) or bool(re.search(r"(?<!\d)\d\s*/\s*\d(?!\d)", sentence))
        if "식사" in sentence and has_meal_consumption:
            matched.append("식사")
        if has_meal_consumption:
            matched.extend(term for term in meal_terms if term in sentence)
        if re.search(
            r"(?<![가-힣])물(?:을|도|만|은|과|이)?(?=\s|[.,!?]|$|\d)",
            sentence,
        ):
            matched.append("물")
    return list(dict.fromkeys(matched))


def _health_matches(text: str, terms: tuple[str, ...]) -> list[str]:
    """'예약·약속·만약'을 약물 근거로 오인하지 않고 건강 문맥만 찾습니다."""

    matched = [term for term in terms if term != "약" and term in text]
    if "약" not in terms:
        return list(dict.fromkeys(matched))

    medication_pattern = re.compile(
        r"(?<![가-힣])약(?:을|이|은|도|만|과|으로)?(?=\s|[.,!?]|$|\d)"
        r"|(?:아침|점심|저녁)\s*약(?:을|이|은|도|만|과|으로)?"
        r"(?=\s|[.,!?]|$|\d)"
    )
    if medication_pattern.search(text):
        matched.append("약")
    return list(dict.fromkeys(matched))


def _section(
    title: str,
    sentences: list[str],
    *,
    missing: str,
) -> str:
    content = " ".join(sentences).strip()
    return f"[{title}] {content if content else f'확인 필요: {missing}'}"


def _document_proposal(
    document_type: str,
    *,
    resident_label: str,
    text: str,
    classification: str,
    risk_level: str,
) -> dict[str, Any]:
    questions: list[str] = []
    if document_type == "care_service_record":
        actions = _matching_sentences(
            text,
            (
                "도움",
                "도와",
                "제공",
                "안내",
                "부축",
                "전달",
                "연락",
                "말벗",
                "체위",
                "권유",
                "드림",
                "드렸",
                "관찰",
            ),
        )
        responses = _matching_sentences(
            text,
            (
                "안정",
                "협조",
                "거부",
                "드심",
                "섭취",
                "보임",
                "하심",
                "말씀",
                "반응",
                "호전",
                "지속",
            ),
        )
        action_set = set(actions)
        response_set = set(responses)
        observations = [
            sentence
            for sentence in _sentences(text)
            if sentence not in action_set and sentence not in response_set
        ]
        if not observations:
            observations = [
                sentence for sentence in _sentences(text) if sentence not in action_set
            ]
        content = "\n".join(
            [
                f"[대상] {resident_label}",
                _section(
                    "관찰·욕구",
                    observations,
                    missing="관찰한 상태 또는 욕구",
                ),
                _section(
                    "제공한 지원",
                    actions,
                    missing="실제로 제공한 도움",
                ),
                _section(
                    "반응·결과",
                    responses,
                    missing="지원 후 어르신 반응",
                ),
            ]
        )
        if not actions:
            questions.append("실제로 제공한 도움은 무엇인가요?")
        if not responses:
            questions.append("도움 제공 후 어르신 반응은 어땠나요?")
    elif document_type == "nursing_log":
        health_observations = _matching_sentences(
            text,
            (
                "혈압",
                "체온",
                "맥박",
                "혈당",
                "복약",
                "약",
                "통증",
                "상처",
                "피부",
                "발적",
                "붉",
                "부종",
                "출혈",
                "호흡",
                "의식",
                "배변",
                "기침",
                "구토",
                "낙상",
                "넘어",
            ),
        )
        nursing_actions = _matching_sentences(
            text,
            (
                "간호",
                "투약",
                "복약",
                "처치",
                "소독",
                "도포",
                "측정",
                "체위",
                "병원",
                "전달",
                "보고",
                "관찰",
            ),
        )
        content = "\n".join(
            [
                f"[대상] {resident_label}",
                _section(
                    "건강 관찰",
                    health_observations,
                    missing="간호일지에 기록할 증상·수치·신체 상태",
                ),
                _section(
                    "간호 확인·조치",
                    nursing_actions,
                    missing="실제로 확인하거나 시행한 간호 조치",
                ),
                f"[위험도 참고] {risk_level} — 공식 평가점수가 아닌 관찰 우선순위",
            ]
        )
        if not health_observations:
            questions.append("간호일지에 기록할 건강 상태의 근거가 충분한가요?")
        questions.append("이름·수치·약·신체 부위를 원본과 확인해 주세요.")
    elif document_type == "consultation_log":
        consultation = _matching_sentences(
            text,
            ("보호자", "상담", "전화", "통화", "면담", "연락", "설명", "요청"),
        )
        results = _matching_sentences(
            text,
            ("동의", "요청", "약속", "예정", "추후", "확인", "전달", "안내"),
        )
        content = "\n".join(
            [
                f"[대상] {resident_label}",
                _section(
                    "상담·연락 내용",
                    consultation,
                    missing="상담 상대와 상담한 내용",
                ),
                _section(
                    "결과·후속 조치",
                    results,
                    missing="상담 결과 또는 후속 연락 계획",
                ),
            ]
        )
        if not consultation:
            questions.append("상담 상대와 상담 방법이 원문에 있나요?")
        questions.append("상담 일시와 합의한 내용을 확인해 주세요.")
    elif document_type == "physical_restraint_log":
        restraint_evidence = _matching_sentences(
            text,
            ("신체제재", "신체 제재", "억제대", "안전벨트", "휠체어 벨트"),
        )
        content = (
            f"[대상] {resident_label}\n"
            + _section(
                "제재 관련 원문",
                restraint_evidence,
                missing="신체제재를 시행한 직접 근거",
            )
            + "\n"
            "[신체제재 사유와 당시 상태] 확인 필요\n"
            "[다른 방법으로 대체할 수 없는 이유] 확인 필요\n"
            "[방법·시간·상태 관찰·해제 조건] 확인 필요\n"
            "[보호자 통지 일시·대상·방법·내용] 확인 필요"
        )
        questions = [
            "제재 필요성과 대체 불가능성이 기록되어 있나요?",
            "방법, 시작·종료 시간, 상태 관찰과 해제 조건을 확인했나요?",
            "보호자 통지 일시, 대상, 방법과 내용을 확인했나요?",
        ]
    else:
        program_evidence = _matching_sentences(
            text,
            (
                "프로그램",
                "참여",
                "활동",
                "체조",
                "노래",
                "미술",
                "레크리에이션",
                "산책",
            ),
        )
        participation = _matching_sentences(
            text,
            ("참여", "거부", "집중", "반응", "만족", "수행", "즐거", "웃"),
        )
        content = (
            f"[참여 어르신] {resident_label}\n"
            + _section(
                "활동 관찰",
                program_evidence,
                missing="실시한 프로그램과 활동 내용",
            )
            + "\n"
            + _section(
                "참여·반응",
                participation,
                missing="어르신의 참여 여부와 반응",
            )
            + "\n"
            "[프로그램명·일시·장소·진행자] 확인 필요\n"
            "[목표·진행 내용·총평] 확인 필요"
        )
        questions = [
            "프로그램명, 일시, 장소, 진행자와 준비물을 확인했나요?",
            "참석자별 참여 여부와 반응을 서로 섞지 않았나요?",
        ]
    return {
        "document_type": document_type,
        "content": content,
        "verification_questions": questions,
    }


def build_document_proposal(
    document_type: str,
    *,
    resident_label: str,
    text: str,
    classification: str,
    risk_level: str,
) -> dict[str, Any]:
    """검토자가 추가한 당일 서류의 안전한 초안을 만든다."""
    if document_type not in DAILY_DOCUMENT_TYPES:
        raise ValueError("지원하지 않는 당일 서류 유형입니다.")
    return _document_proposal(
        document_type,
        resident_label=resident_label,
        text=_normalize_text(text),
        classification=classification,
        risk_level=risk_level,
    )


def build_prototype_suggestion(snapshot: dict[str, Any]) -> dict[str, Any]:
    """외부 AI 없이 화면·데이터 흐름만 검증하는 결정론적 시험 제안."""
    text = _normalize_text(str(snapshot["body"]))
    resident_names = [
        str(name).strip()
        for name in snapshot.get("resident_names", [])
        if str(name).strip()
    ]
    resident_name = str(snapshot.get("resident_name") or "").strip()
    if resident_name and resident_name not in resident_names:
        resident_names.insert(0, resident_name)
    resident_label = ", ".join(resident_names) or "어르신 확인 필요"
    classification = "daily_care"
    risk_level = "low"
    target_roles = ["caregiver", "social_worker"]
    document_types = ["care_service_record"]
    keywords: list[str] = []

    # 안전사고와 피부손상처럼 즉시 확인이 필요한 표현을 일반 통증·식사보다
    # 먼저 판별한다. 한 문장에 여러 표현이 있어도 핵심 위험이 뒤 규칙에
    # 가려지지 않도록 순서가 곧 업무 우선순위다.
    rules = [
        (
            (
                "낙상",
                "넘어",
                "비틀",
                "미끄러",
                "균형을 잃",
                "주저앉",
                "부딪",
                "출혈",
                "호흡곤란",
                "의식",
            ),
            "safety",
            ["safety"],
            ["nursing_log", "care_service_record"],
            ["nurse", "director", "caregiver", "social_worker"],
        ),
        (
            (
                "상처",
                "피부",
                "붉",
                "발적",
                "진물",
                "욕창",
                "압박",
            ),
            "health",
            ["health", "skin_integrity"],
            ["nursing_log", "care_service_record"],
            ["nurse", "caregiver", "social_worker"],
        ),
        (
            ("식사", "섭취", "수분", "식욕", "체중"),
            "nutrition",
            ["nutrition"],
            ["care_service_record", "nursing_log"],
            ["caregiver", "nurse", "social_worker"],
        ),
        (
            ("혈압", "체온", "맥박", "복약", "약", "통증"),
            "health",
            ["health"],
            ["nursing_log", "care_service_record"],
            ["nurse", "caregiver"],
        ),
        (
            ("반복", "집에 가", "출입문", "불안", "안정", "인지", "배회"),
            "daily_care",
            ["cognition"],
            [
                "care_service_record",
                "nursing_log",
            ],
            ["caregiver", "social_worker", "nurse"],
        ),
        (
            ("보호자", "상담", "전화", "면담"),
            "consultation",
            ["consultation"],
            ["consultation_log"],
            ["social_worker", "director"],
        ),
        (
            ("보행", "운동", "재활", "관절", "작업치료"),
            "rehabilitation",
            ["rehabilitation"],
            ["care_service_record"],
            ["therapist", "caregiver", "social_worker"],
        ),
    ]
    for terms, category, rule_keywords, documents, roles in rules:
        matched = (
            _nutrition_matches(text)
            if category == "nutrition"
            else _health_matches(text, terms)
            if category == "health"
            else [term for term in terms if term in text]
        )
        if matched:
            classification = category
            keywords.extend(rule_keywords + matched)
            document_types = documents
            target_roles = roles
            break

    urgent_terms = [term for term in ("의식", "호흡곤란", "대량 출혈") if term in text]
    high_terms = [
        term
        for term in (
            "낙상",
            "넘어",
            "비틀",
            "균형을 잃",
            "주저앉",
            "부딪",
            "출혈",
            "고열",
            "심한 통증",
        )
        if term in text
    ]
    watch_terms = [
        term
        for term in (
            "평소보다",
            "적음",
            "못 드심",
            "통증",
            "상처",
            "피부",
            "붉",
            "발적",
            "압박",
            "반복",
            "출입문",
            "불안",
        )
        if term in text
    ]
    if urgent_terms:
        risk_level = "urgent"
        keywords.extend(urgent_terms)
    elif high_terms:
        risk_level = "high"
        keywords.extend(high_terms)
    elif watch_terms:
        risk_level = "medium"
        keywords.extend(watch_terms)

    # 한 메시지에 식사·건강 관찰과 보호자 상담이 함께 들어오는 경우가 많다.
    # 대표 분류는 하나만 유지하되, 상담 근거가 실제로 있으면 상담일지 초안도
    # 별도로 만들어 서로 다른 서류가 같은 문장을 반복하지 않게 한다.
    if _contains_any(text, ("보호자", "상담", "전화", "통화", "면담")):
        document_types.append("consultation_log")
        keywords.append("consultation")

    summary = f"{resident_label} 관련 관찰: {text}"
    if len(summary) > 1000:
        summary = summary[:997] + "..."
    if _contains_any(
        text,
        ("신체제재", "신체 제재", "억제대", "안전벨트", "휠체어 벨트"),
    ):
        document_types.append("physical_restraint_log")
    if _contains_any(
        text,
        ("프로그램", "참여", "활동", "체조", "노래", "미술", "레크리에이션"),
    ):
        document_types.append("program_log")
    document_types = [
        document_type
        for document_type in dict.fromkeys(document_types)
        if document_type in DAILY_DOCUMENT_TYPES
    ]
    action_terms = [
        term
        for term in (
            "도움드림",
            "도와드림",
            "제공",
            "안내",
            "관찰",
            "전달",
            "연락",
            "말벗",
            "체위변경",
            "부축",
        )
        if term in text
    ]
    verification_questions = []
    if risk_level in {"high", "urgent"}:
        verification_questions.append(
            "이름, 시간, 수치, 신체 부위와 사건 경과를 원문과 대조했나요?"
        )
    if not action_terms:
        verification_questions.append("실제로 시행한 조치가 무엇인지 확인해 주세요.")
    document_drafts = [
        _document_proposal(
            document_type,
            resident_label=resident_label,
            text=text,
            classification=classification,
            risk_level=risk_level,
        )
        for document_type in document_types
    ]
    return {
        "corrected_text": text,
        "summary": summary,
        "observation_details": text,
        "actions_taken": action_terms,
        "resident_response": "",
        "handover_summary": summary,
        "verification_questions": verification_questions,
        "classification": classification,
        "risk_level": risk_level,
        "target_roles": list(dict.fromkeys(target_roles)),
        "document_types": document_types,
        "keywords": list(dict.fromkeys(keywords))[:12],
        "document_drafts": document_drafts,
    }
