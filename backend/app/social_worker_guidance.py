from __future__ import annotations

import re
from typing import Any


SAFETY_NOTICE = (
    "이 문안은 대화에서 확인된 사실만 옮긴 편집용 제안입니다. "
    "원문을 대조하고 직원이 확인하기 전에는 공식 기록으로 저장되지 않습니다."
)
PLAN_REVIEW_NOTICE = "아직 공식 계획에는 반영되지 않았습니다."

_REVIEW_PROFILES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        (
            "보행",
            "이동",
            "부축",
            "비틀",
            "미끄",
            "주저앉",
            "넘어",
            "지팡이",
            "보행기",
            "휠체어",
        ),
        "낙상위험도의 이동·보행, 욕구사정의 이동·일상생활, "
        "급여제공계획의 이동지원",
    ),
    (
        ("피부", "붉", "발적", "압박", "욕창", "상처"),
        "욕창위험도의 피부·체위변경, 욕구사정의 피부 상태, "
        "급여제공계획의 피부보호",
    ),
    (
        ("인지", "지남력", "기억", "일정", "의사소통"),
        "인지기능검사의 관찰 내용, 욕구사정의 인지·의사소통, "
        "급여제공계획의 인지지원",
    ),
    (
        ("식사", "섭취량", "영양"),
        "욕구사정의 영양·식사, 욕창위험도의 영양 상태, "
        "급여제공계획의 식사지원",
    ),
    (
        ("배변", "배뇨", "화장실"),
        "낙상위험도의 화장실 이동, 욕구사정의 배설 상태, "
        "급여제공계획의 배설지원",
    ),
)


def _sentences(texts: list[str]) -> list[str]:
    values: list[str] = []
    for text in texts:
        for sentence in re.split(r"(?<=[.!?])\s+|[\r\n]+", text):
            compact = re.sub(r"\s+", " ", sentence).strip(" -")
            if not compact:
                continue
            if any(
                marker in compact.casefold()
                for marker in (
                    "synthetic",
                    "fixture",
                    "dev 시험",
                    "개발 검증",
                    "음성 받아쓰기 실패",
                )
            ):
                continue
            if compact not in values:
                values.append(compact)
    return values


def _first_match(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text)
    return match.group(0) if match else None


def _review_profile(text: str) -> tuple[tuple[str, ...], str] | None:
    for markers, review_target in _REVIEW_PROFILES:
        if any(marker in text for marker in markers):
            return markers, review_target
    return None


def _relevant_sentence(texts: list[str], markers: tuple[str, ...]) -> str | None:
    matching = [
        sentence
        for sentence in _sentences(texts)
        if any(marker in sentence for marker in markers)
    ]
    if not matching:
        return None
    return max(
        matching,
        key=lambda sentence: (
            sum(marker in sentence for marker in markers),
            len(sentence),
        ),
    )


def _korean_observation_label(value: str) -> str:
    match = re.match(
        r"^(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})"
        r"(?:[ T](?P<time>\d{1,2}:\d{2}))?",
        value.strip(),
    )
    if not match:
        return value.strip()
    label = (
        f"{match.group('year')}년 {int(match.group('month'))}월 "
        f"{int(match.group('day'))}일"
    )
    if match.group("time"):
        label += f" {match.group('time')}"
    return label


def _grounded_review_wording(
    *,
    texts: list[str],
    baseline_texts: list[str],
    observed_at: str,
) -> str | None:
    current_sentences = _sentences(texts)
    if not current_sentences:
        return None
    joined = " ".join(current_sentences)
    profile = _review_profile(joined)
    if profile is None:
        return None
    markers, review_target = profile
    current = _relevant_sentence(texts, markers)
    if current is None:
        return None
    previous = _relevant_sentence(baseline_texts, markers)
    lines: list[str] = []
    if previous:
        lines.append(f"이전 자료에는 다음과 같이 기록되어 있습니다: {previous}")
    if observed_at.strip():
        lines.append(
            f"{_korean_observation_label(observed_at)} 관찰에서 다음 내용이 확인됩니다: "
            f"{current}"
        )
    else:
        lines.append(f"이후 관찰에서 다음 내용이 확인됩니다: {current}")
    lines.append(f'근거: "{current}"')
    lines.append(f"수정 검토: {review_target} 부분을 검토할 수 있습니다.")
    return "\n".join(lines)


def _related_documents(text: str, candidates: list[str]) -> list[str]:
    related = list(dict.fromkeys(candidates))
    if any(term in text for term in ("보행", "비틀", "부축", "낙상")):
        related.extend(
            [
                "fall_risk_assessment",
                "needs_assessment",
                "long_term_care_service_plan",
            ]
        )
    if any(term in text for term in ("피부", "붉", "발적", "압박")):
        related.append("pressure_ulcer_risk_assessment")
    if any(term in text for term in ("인지", "지남력", "기억")):
        related.append("cognitive_function_assessment")
    if any(term in text for term in ("욕구", "생활 변화", "상태변경")):
        related.append("needs_assessment")
    return list(dict.fromkeys(related))


def build_social_worker_guidance(
    *,
    texts: list[str],
    baseline_texts: list[str] | None = None,
    observed_at: str = "",
    pending_checks: list[str],
    document_candidates: list[str],
    final_status: str,
) -> dict[str, Any]:
    sentences = _sentences(texts)
    joined = " ".join(sentences)
    latest = sentences[-1] if sentences else "확인된 사건 문장이 없습니다."
    previous_state: str | None = None
    current_state: str | None = None
    questions = list(dict.fromkeys(pending_checks))[:3]
    title = "확인된 돌봄 사실 정리"
    suggested = latest[:240]

    blood_pressures = list(
        dict.fromkeys(re.findall(r"(?<!\d)\d{2,3}\s*/\s*\d{2,3}(?!\d)", joined))
    )
    if len(blood_pressures) >= 2 and any(term in joined for term in ("다르", "달라", "불명확", "충돌")):
        title = "충돌한 혈압 기록 확인"
        suggested = (
            f"혈압 기록에 {'과 '.join(blood_pressures[:2])}이 함께 있어 "
            "어느 값도 확정하지 않고 원본 확인과 재측정이 필요함."
        )
        questions = ["원본 기록과 재측정 결과 중 확인된 혈압은 무엇인가요?"]
    elif any(term in joined for term in ("물", "수분")):
        title = "수분 제공 및 후속 확인"
        time_values = list(dict.fromkeys(re.findall(r"(?<!\d)(?:[01]?\d|2[0-3]):[0-5]\d(?!\d)", joined)))
        amount = _first_match(r"(?<!\d)\d+(?:\.\d+)?\s*(?:mL|ml|밀리리터)", joined)
        offered_at = time_values[0] if time_values else "시간 확인 필요"
        follow_at = time_values[1] if len(time_values) > 1 else "재확인 시간 확인 필요"
        amount_text = amount.replace(" ", "") if amount else "제공량 확인 필요"
        resolved = any(term in joined for term in ("재확인 결과", "확인 완료", "상태가 양호"))
        suggested = (
            f"{offered_at} 물 {amount_text} 제공함. {follow_at} "
            + ("재확인 결과 섭취 상태 양호함." if resolved else "섭취 상태 재확인 예정임.")
        )
    elif any(term in joined for term in ("보행", "부축", "비틀")):
        title = "이동 상태 변화 확인"
        if "독립보행" in joined:
            previous_state = "이전 기록: 실내 독립보행"
        if "부축" in joined:
            current_state = "현재 기록: 보행 시 부축 필요"
        suggested = (
            "최근 보행 시 부축이 필요했음. 일시적 변화인지 지속되는 상태인지 "
            "추가 관찰 후 이동지원 방법을 검토함."
        )
        if not questions:
            questions = ["보행 시 부축 필요가 일시적인지 계속되는지 확인해 주세요."]
    elif "보호자" in joined and any(term in joined for term in ("통화", "상담", "설명")):
        title = "보호자 연락·설명 결과"
        suggested = latest[:240]
    elif any(term in joined for term in ("식사", "섭취량")):
        title = "식사 섭취와 후속 확인"
        suggested = latest[:240]
    elif any(term in joined for term in ("배변", "배뇨")):
        title = "배설 상태와 확인 결과"
        suggested = latest[:240]
    elif any(term in joined for term in ("붉", "피부", "발적")):
        title = "피부 관찰과 후속 확인"
        suggested = latest[:240]
    elif any(term in joined for term in ("프로그램", "활동")):
        title = "활동 참여와 반응"
        suggested = latest[:240]

    grounded_wording = _grounded_review_wording(
        texts=texts,
        baseline_texts=baseline_texts or [],
        observed_at=observed_at,
    )
    if grounded_wording:
        suggested = grounded_wording

    related_documents = _related_documents(joined, document_candidates)
    plan_review_recommended = "long_term_care_service_plan" in related_documents
    return {
        "title": title,
        "previous_state": previous_state,
        "current_state": current_state,
        "confirmation_questions": questions,
        "suggested_wording": suggested,
        "related_document_types": related_documents,
        "safety_notice": SAFETY_NOTICE,
        "plan_review_recommended": plan_review_recommended,
        "plan_review_notice": PLAN_REVIEW_NOTICE if plan_review_recommended else None,
        "based_on_confirmed_facts": final_status != "needs_confirmation",
    }
