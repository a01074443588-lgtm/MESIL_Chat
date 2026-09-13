from __future__ import annotations

from calendar import monthrange
from datetime import date


CARE_PLANNING_DOCUMENT_TYPES = (
    "cognitive_function_assessment",
    "fall_risk_assessment",
    "pressure_ulcer_risk_assessment",
    "needs_assessment",
    "long_term_care_service_plan",
)

CARE_PLANNING_DOCUMENT_LABELS = {
    "cognitive_function_assessment": "인지기능검사",
    "fall_risk_assessment": "낙상위험도",
    "pressure_ulcer_risk_assessment": "욕창위험도",
    "needs_assessment": "욕구사정",
    "long_term_care_service_plan": "장기요양급여 제공계획서",
}

OPERATING_CYCLE_NOTICE = (
    "센터 운영 원칙은 기본 6개월 주기이며 상태 변경이 있으면 즉시 재검토 후보로 "
    "올립니다. 문서별 주기는 1~24개월 범위에서 조정할 수 있습니다."
)
LEGAL_STANDARD_NOTICE = (
    "이 주기는 사용자 제공 운영 원칙이며 법령·공단 의무를 뜻하지 않습니다. "
    "공식 적용 전 최신 원문 기준을 별도로 확인해야 합니다."
)


def add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def normalized_due_dates(
    *,
    assessment_date: date | None,
    valid_until: date | None,
    next_due_date: date | None,
    cycle_months: int,
) -> tuple[date | None, date | None]:
    if assessment_date is None:
        return valid_until, next_due_date
    calculated = add_months(assessment_date, cycle_months)
    return valid_until or calculated, next_due_date or calculated


def candidate_reasons(
    *,
    as_of: date,
    assessment_date: date | None,
    next_due_date: date | None,
    reassess_on_state_change: bool,
    state_change_triggered: bool,
    status: str,
) -> list[str]:
    reasons: list[str] = []
    if reassess_on_state_change and state_change_triggered:
        reasons.append("상태 변경으로 즉시 재검토")
    if assessment_date is None:
        reasons.append("작성 이력이 없어 기초 입력 필요")
    elif next_due_date is not None and next_due_date <= as_of:
        reasons.append("사용자 설정 운영주기 도래")
    if status in {"draft", "needs_review"}:
        reasons.append("직원 확인 필요")
    return reasons
