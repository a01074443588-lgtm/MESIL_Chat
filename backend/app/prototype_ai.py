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


DOCUMENT_SOURCE_BLOCK_LABEL = re.compile(
    r"^\[(?:이미지 글자 판독|음성 받아쓰기|보고서 이미지 판독|"
    r"음성파일 받아쓰기|답글|댓글)(?:\s*·[^\]]*)?\]",
    flags=re.IGNORECASE,
)


def _document_primary_text(text: str) -> str:
    """OCR·음성·답글 블록은 감사 근거에 두고 서류 본문 입력에서 분리한다."""

    primary_blocks: list[str] = []
    for block in re.split(r"\n\s*\n+", text):
        primary_lines: list[str] = []
        for line in block.splitlines():
            if DOCUMENT_SOURCE_BLOCK_LABEL.match(line.strip()):
                break
            primary_lines.append(line)
        primary = " ".join(primary_lines).strip()
        if primary:
            primary_blocks.append(primary)
    return "\n".join(primary_blocks)


def _document_evidence_text(text: str) -> str:
    """초안에는 본문에서 확인된 사실만 남기고 기술용 원문은 제외한다."""
    primary_text = _document_primary_text(text)
    without_markers = re.sub(
        r"\[(?=[^\]]*(?:BRIEFING-VERIFY|SYNTHETIC|DEV|합성 비식별|개발 검증))"
        r"[^\]]+\]\s*",
        "",
        primary_text,
        flags=re.IGNORECASE,
    )
    notice_terms = (
        "모든 내용은 합성 시험자료입니다",
        "모든 내용은 실제 인물과 무관한 합성 시험자료입니다",
        "실제 인물이나 기록과 무관한 합성 시험자료입니다",
        "이미지·음성·텍스트는 모두 실제 인물과 무관한 합성 시험자료입니다",
    )
    sentences: list[str] = []
    for sentence in _sentences(without_markers):
        if any(notice in sentence for notice in notice_terms):
            continue
        if re.search(r"\b(?:SYNTHETIC|DEV|FIXTURE)\b", sentence, flags=re.I):
            continue
        if "개발 검증" in sentence or "실제 인물과 무관" in sentence:
            continue
        if sentence not in sentences:
            sentences.append(sentence)
    return " ".join(sentences).strip()


def _join_document_sentences(sentences: list[str], *, limit: int = 2) -> str:
    return " ".join(list(dict.fromkeys(sentences))[:limit]).strip()


def _care_water_facts(text: str) -> dict[str, str]:
    sentences = _sentences(text)
    action_sentence = next(
        (
            sentence
            for sentence in sentences
            if any(term in sentence for term in ("물", "수분"))
            and re.search(r"\d+(?:\.\d+)?\s*(?:mL|ml|밀리리터)", sentence, re.I)
            and any(term in sentence for term in ("제공", "드렸", "드림", "권유"))
        ),
        "",
    )
    if not action_sentence:
        return {}

    clock_match = re.search(r"(?<!\d)(?:[01]?\d|2[0-3]):[0-5]\d(?!\d)", action_sentence)
    amount_match = re.search(
        r"(?P<amount>\d+(?:\.\d+)?)\s*(?:mL|ml|밀리리터)",
        action_sentence,
        re.I,
    )
    if amount_match is None:
        return {}
    clock = clock_match.group(0) if clock_match else ""
    amount = f"{amount_match.group('amount')}mL"
    fraction_match = re.search(r"(?<!\d)(\d)\s*/\s*(\d)(?!\d)", action_sentence)
    meal_label = next(
        (label for label in ("아침", "점심", "저녁") if label in action_sentence),
        "식사" if "식사" in action_sentence else "",
    )
    if fraction_match:
        fraction = f"{fraction_match.group(1)}/{fraction_match.group(2)}"
        meal_name = (
            f"{meal_label} 식사"
            if meal_label in {"아침", "점심", "저녁"}
            else "식사"
        )
        meal_fact = f"{meal_name} {fraction} 섭취·"
    else:
        meal_fact = ""
    provided = f"{clock + ' ' if clock else ''}{meal_fact}물 {amount} 제공함."
    action = f"{meal_fact}물 {amount} 제공".rstrip("·")

    followup_sentence = next(
        (
            sentence
            for sentence in sentences
            if any(term in sentence for term in ("후속", "다시 확인", "재확인", "예정", "미완료"))
            and sentence != action_sentence
        ),
        "",
    )
    followup = ""
    if followup_sentence:
        followup_clocks = re.findall(
            r"(?<!\d)(?:[01]?\d|2[0-3]):[0-5]\d(?!\d)", followup_sentence
        )
        followup_clock = next((value for value in followup_clocks if value != clock), "")
        if followup_clock and any(
            term in followup_sentence for term in ("예정", "다시 확인", "재확인")
        ):
            followup = f"{followup_clock} 섭취 상태 재확인 예정"
        elif "미완료" in followup_sentence:
            followup = "후속 확인 미완료"
        else:
            followup = followup_sentence
    special = (
        "후속 확인 미완료"
        if any("미완료" in sentence for sentence in sentences)
        else ""
    )
    return {
        "provided": provided,
        "action": action,
        "followup": followup,
        "special": special,
    }


def _conflicting_blood_pressure_fact(text: str) -> str | None:
    values = list(
        dict.fromkeys(
            re.sub(r"\s+", "", value)
            for value in re.findall(r"(?<!\d)\d{2,3}\s*/\s*\d{2,3}(?!\d)", text)
        )
    )
    if len(values) < 2 or not any(
        term in text for term in ("불명확", "충돌", "인지", "확인 필요", "판독")
    ):
        return None
    return f"혈압 {' 또는 '.join(values[:2])} · 원문 확인 필요"


def _consultation_result_fact(text: str) -> str:
    has_guardian = "보호자" in text or "가족" in text
    has_explanation = any(term in text for term in ("설명", "안내", "공유", "전달"))
    has_confirmation = any(term in text for term in ("확인", "동의"))
    if has_guardian and has_explanation and has_confirmation:
        return "보호자에게 설명하고 확인받음"
    facts: list[str] = []
    if has_explanation:
        facts.append("설명·안내 내용 확인")
    if "요청" in text:
        facts.append("요청 내용 확인")
    if has_confirmation:
        facts.append("확인·동의 결과 기록")
    return " · ".join(facts) or "확인 필요"


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
    recorded_at: str | None = None,
    evidence_count: int = 0,
) -> dict[str, Any]:
    questions: list[str] = []
    draft_fields: dict[str, str] = {}
    missing_fields: list[str] = []
    human_verification_fields: list[str] = []
    text = _document_evidence_text(text)
    if document_type == "care_service_record":
        evidence_sentences = _sentences(text)
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
        followups = _matching_sentences(
            text,
            (
                "후속",
                "예정",
                "다시 확인",
                "재확인",
                "미완료",
                "관찰",
            ),
        )
        category_labels = {
            "daily_care": "일상생활 지원·관찰",
            "nutrition": "식사·수분 지원·관찰",
            "health": "건강·바이탈 관찰",
            "safety": "이동·안전 지원·관찰",
            "consultation": "상담 관련 돌봄 조치",
            "rehabilitation": "기능회복·프로그램 지원",
        }
        followup_set = set(followups)
        observations = [
            sentence for sentence in observations if sentence not in followup_set
        ]
        observation_text = _join_document_sentences(observations)
        action_text = _join_document_sentences(actions)
        response_text = _join_document_sentences(responses)
        followup_text = _join_document_sentences(followups)
        provided_observation_text = _join_document_sentences(
            [
                sentence
                for sentence in evidence_sentences
                if sentence in set(observations) | action_set
            ]
        )
        water_facts = _care_water_facts(text)
        if water_facts:
            provided_observation_text = water_facts["provided"]
            action_text = water_facts["action"]
            followup_text = water_facts["followup"] or followup_text
            observation_text = water_facts["special"] or observation_text
        conflicting_blood_pressure = _conflicting_blood_pressure_fact(text)
        if conflicting_blood_pressure:
            provided_observation_text = conflicting_blood_pressure
            response_text = "확인 필요"
            action_text = (
                "원문 확인 후 혈압 재측정 필요"
                if "재측정" in text
                else "원문 확인 필요"
            )
            followup_text = action_text
            observation_text = "두 혈압 수치가 충돌하여 어느 값도 확정하지 않음"
        draft_fields = {
            "대상": resident_label,
            "일시": recorded_at or "확인 필요",
            "서비스·관찰 구분": category_labels.get(
                classification, "직원 확인 필요"
            ),
            "제공·관찰 내용과 수량": provided_observation_text
            or "확인 필요",
            "대상 상태·반응": response_text or "확인 필요",
            "수행한 조치": action_text or "확인 필요",
            "다음 확인·기한·결과": followup_text or "확인 필요",
            "특이사항": observation_text or "확인 필요",
            "작성 근거": (
                f"근거 대화 {evidence_count}건과 연결"
                if evidence_count
                else "근거 대화 확인 필요"
            ),
            "작성자 최종 확인": "확인 필요",
        }
        missing_fields = [
            field_name
            for field_name, value in draft_fields.items()
            if value.startswith("확인 필요") or value.endswith("확인 필요")
        ]
        human_verification_fields = [
            "실제 제공·관찰 여부와 공식 서비스 분류",
            "일시·수량·수치·신체 부위",
            "작성자와 최종 특이사항 문구",
            "공식 기록 반영 여부와 서명",
        ]
        content = "\n".join(
            [
                f"[대상] {resident_label}",
                f"[일시] {draft_fields['일시']}",
                f"[서비스·관찰 구분] {draft_fields['서비스·관찰 구분']}",
                f"[제공·관찰 내용과 수량] {draft_fields['제공·관찰 내용과 수량']}",
                f"[대상 상태·반응] {draft_fields['대상 상태·반응']}",
                f"[수행한 조치] {draft_fields['수행한 조치']}",
                f"[다음 확인·기한·결과] {draft_fields['다음 확인·기한·결과']}",
                f"[특이사항] {draft_fields['특이사항']}",
                f"[작성 근거] {draft_fields['작성 근거']}",
                "[작성자 최종 확인] 확인 필요",
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
            (
                "동의",
                "요청",
                "약속",
                "예정",
                "추후",
                "확인",
                "전달",
                "안내",
                "공유",
            ),
        )
        followups = _matching_sentences(
            text,
            ("약속", "예정", "추후", "다시", "재확인", "후속"),
        )
        method = "확인 필요"
        if any(term in text for term in ("전화", "통화")):
            method = "전화"
        elif "면담" in text:
            method = "대면 면담"
        elif "상담" in text:
            method = "상담(방법 확인 필요)"
        relationship = (
            "보호자(세부 관계 확인 필요)"
            if "보호자" in text
            else "가족(관계 확인 필요)"
            if "가족" in text
            else "확인 필요"
        )
        consultation_text = _join_document_sentences(consultation)
        result_text = _consultation_result_fact(text)
        followup_text = _join_document_sentences(followups, limit=1)
        consultation_reason = (
            "보호자 연락·설명 기록"
            if "보호자" in text
            and any(term in text for term in ("전화", "통화", "연락", "상담", "면담"))
            else consultation_text
            or "확인 필요"
        )
        draft_fields = {
            "대상": resident_label,
            "상담 일시": recorded_at or "확인 필요",
            "상담 방식": method,
            "상담 상대 관계": relationship,
            "상담 사유": consultation_reason,
            "주요 상담 내용": consultation_text or "확인 필요",
            "설명·요청·동의·반응": result_text,
            "수행한 조치와 결과": result_text,
            "추후 계획·재확인": followup_text or "확인 필요",
            "작성 근거": (
                f"근거 대화 {evidence_count}건과 연결"
                if evidence_count
                else "근거 대화 확인 필요"
            ),
            "상담 직원 최종 확인": "확인 필요",
        }
        missing_fields = [
            field_name
            for field_name, value in draft_fields.items()
            if value == "확인 필요" or value.endswith("확인 필요)")
        ]
        human_verification_fields = [
            "상담 상대의 실제 관계와 상담 방법",
            "요청·동의·설명의 정확한 의미",
            "상담 일시·담당 직원·최종 조치",
            "공식 상담일지 반영 여부와 서명",
        ]
        content = "\n".join(
            [
                f"[대상] {resident_label}",
                f"[상담 일시] {draft_fields['상담 일시']}",
                f"[상담 방식] {draft_fields['상담 방식']}",
                f"[상담 상대 관계] {draft_fields['상담 상대 관계']}",
                f"[상담 사유] {draft_fields['상담 사유']}",
                f"[주요 상담 내용] {draft_fields['주요 상담 내용']}",
                f"[설명·요청·동의·반응] {draft_fields['설명·요청·동의·반응']}",
                f"[추후 계획·재확인] {draft_fields['추후 계획·재확인']}",
                f"[작성 근거] {draft_fields['작성 근거']}",
                "[상담 직원 최종 확인] 확인 필요",
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
        "draft_fields": draft_fields,
        "missing_fields": missing_fields,
        "human_verification_fields": human_verification_fields,
    }


def build_document_proposal(
    document_type: str,
    *,
    resident_label: str,
    text: str,
    classification: str,
    risk_level: str,
    recorded_at: str | None = None,
    evidence_count: int = 0,
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
        recorded_at=recorded_at,
        evidence_count=evidence_count,
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
