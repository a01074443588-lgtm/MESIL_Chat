from __future__ import annotations

import re
from typing import Literal


AiHelpQuestionType = Literal[
    "attachment_guidance",
    "general_guidance",
    "record_search",
    "clarification",
]

_ATTACHMENT_INTENT = re.compile(
    r"(?:(?:이|첨부한|올린).{0,12}(?:이미지|사진|파일|문서|판독문|음성)|"
    r"첨부\s*(?:이미지|사진|파일|문서|판독문|음성)).{0,24}"
    r"(?:설명|핵심|내용|요약|읽어|알려)"
)
_RECORD_LOOKUP_ACTION = re.compile(r"(?:찾아|검색|조회|보여)")
_RECORD_CONTEXT = re.compile(
    r"(?:기록|대화|보고|지난\s*\d+\s*일|오늘|어제|이번\s*주|최근|근황|상태)"
)
_RECORD_SCOPE = re.compile(
    r"(?:지난\s*\d+\s*일(?:\s*동안|간)?|오늘|어제|이번\s*주|최근)"
)
_PERIOD_RECORD_INTENT = re.compile(
    r"(?:지난\s*\d+\s*일(?:\s*동안|간)?|오늘|어제|이번\s*주).{0,30}(?:기록|대화|보고)"
)
_COUNT_RECORD_INTENT = re.compile(r"(?:몇\s*(?:건|번)|횟수|가장\s*많).{0,30}(?:기록|보고|발생)|(?:기록|보고|발생).{0,30}(?:몇\s*(?:건|번)|횟수|가장\s*많)")
_SUBJECT_RECORD_INTENT = re.compile(r"(?:근황|최근\s*(?:근황|상태)|현재\s*상태)")
_SUBJECT_RECORD_SUMMARY_INTENT = re.compile(
    r"(?:최근.{0,24}(?:기록|보고)|(?:기록|보고).{0,24}(?:뭐|무엇|알려|있|됐))"
)
_SPECIFIC_RESIDENT_RECORD_CONTEXT = re.compile(
    r"(?:상태|근황|달라|변화|기록|보고|근거)"
)
_SPECIFIC_RESIDENT_QUERY_ACTION = re.compile(
    r"(?:알려|보여|찾아|검색|조회|어때|있어|뭐|무엇|몇\s*(?:건|번))"
)
_RESIDENT_GROUP = re.compile(r"(?:어르신\s*중|어르신(?:들|과|이)|분(?:은|이)\s*(?:누구|계신))")
_RECORDED_CHANGE = re.compile(
    r"(?:급격한\s*변화|평소와\s*다른|상태가?.{0,16}(?:달라|변)|식사량이?\s*(?:줄|감소)|"
    r"(?:변화|상태|식사량).{0,24}(?:기록|보고)|(?:기록|보고)(?:된|됐).{0,24}(?:변화|상태|식사량))"
)
_GROUP_RECORD_QUERY = re.compile(r"(?:계신가|있어|누(?:구|가)|근거|알려|찾아|검색|조회|보여|몇\s*건)")
_SPECIFIC_RECORDED_WHEN = re.compile(
    r"(?:언제.{0,30}(?:기록|보고|대화|식사|발생)|(?:기록|보고|대화).{0,30}언제)"
)
_SCHEDULE_RECORD_INTENT = re.compile(
    r"(?:일정|업무).{0,30}(?:오늘\s*할\s*일|우선|예정|확인)"
)
_RESIDENT_CODE = re.compile(r"어르\d{4}")
_CLINICAL_DECISION = re.compile(
    r"(?:진단|투약|약을?\s*(?:중단|변경|추가)|처치|치료|복용량|위험평가).{0,20}(?:결정|확정|판단|중단|변경|추가)"
)
_CURRENT_POLICY = re.compile(r"(?:최신|현재).{0,20}(?:공단|법|고시|지침|기준|제도)")
_TRULY_AMBIGUOUS = re.compile(
    r"^(?:도와\s*줘|이건\s*어때|그거\s*해\s*줘)[?.!]*$"
)
_UNSUPPORTED_SPECIFIC_CAUSE_ASSERTION = re.compile(
    r"(?:왜|원인).{0,40}(?:단정|확정|추정)"
)
_FACILITY_SPECIFIC_INFORMATION = re.compile(
    r"(?:우리|저희)\s*(?:시설|기관).{0,40}(?:급여\s*종류|계약|내부\s*설정|등록\s*정보)"
)
_VERSION_SENSITIVE_INDICATOR = re.compile(
    r"(?:시설\s*)?평가\s*(?:지표|매뉴얼)\s*\d+\s*번"
)


def is_multi_resident_record_search(question: str) -> bool:
    """Return true only for a recorded multi-resident observation query.

    A general care question such as how to respond to a change has no recorded
    observation/query combination and therefore stays on general guidance.
    """
    normalized = " ".join((question or "").split())
    return bool(
        _RESIDENT_GROUP.search(normalized)
        and _RECORDED_CHANGE.search(normalized)
        and _GROUP_RECORD_QUERY.search(normalized)
    )


def classify_ai_help_question(
    question: str,
    *,
    known_resident_names: list[str] | tuple[str, ...] = (),
) -> AiHelpQuestionType:
    """Route by user intent, never by model guesswork or record availability."""
    normalized = " ".join((question or "").split())
    if not normalized:
        return "clarification"
    has_specific_resident = bool(_RESIDENT_CODE.search(normalized)) or any(
        name and name in normalized for name in known_resident_names
    )
    if _ATTACHMENT_INTENT.search(normalized):
        return "attachment_guidance"
    if is_multi_resident_record_search(normalized):
        return "record_search"
    has_scoped_lookup = bool(
        _RECORD_LOOKUP_ACTION.search(normalized)
        and _RECORD_CONTEXT.search(normalized)
        and (has_specific_resident or _RECORD_SCOPE.search(normalized))
    )
    has_specific_resident_record_request = bool(
        has_specific_resident
        and _SPECIFIC_RESIDENT_RECORD_CONTEXT.search(normalized)
        and _SPECIFIC_RESIDENT_QUERY_ACTION.search(normalized)
        and (
            _RECORD_SCOPE.search(normalized)
            or _RECORD_LOOKUP_ACTION.search(normalized)
            or re.search(r"(?:기록|보고|근거)", normalized)
        )
    )
    if (
        has_scoped_lookup
        or has_specific_resident_record_request
        or _COUNT_RECORD_INTENT.search(normalized)
        or _SCHEDULE_RECORD_INTENT.search(normalized)
        or (
            has_specific_resident
            and (
                _SUBJECT_RECORD_INTENT.search(normalized)
                or _SUBJECT_RECORD_SUMMARY_INTENT.search(normalized)
                or _SPECIFIC_RECORDED_WHEN.search(normalized)
            )
        )
    ):
        return "record_search"
    if (
        (_RECORD_LOOKUP_ACTION.search(normalized) and _RECORD_CONTEXT.search(normalized))
        or _PERIOD_RECORD_INTENT.search(normalized)
    ):
        return "record_search"
    if has_specific_resident and _UNSUPPORTED_SPECIFIC_CAUSE_ASSERTION.search(normalized):
        return "clarification"
    if _TRULY_AMBIGUOUS.fullmatch(normalized):
        return "clarification"
    # Safe default: any remaining meaningful question goes to the local-only
    # general model. This deliberately avoids another open-ended keyword
    # allowlist that would turn new, valid questions into clarification.
    return "general_guidance"


def apply_general_guidance_boundaries(question: str, answer: str) -> str:
    if _FACILITY_SPECIFIC_INFORMATION.search(question):
        return (
            "현재 MESIL AI에 연결된 자료만으로는 우리 시설의 급여종류나 내부 등록정보를 "
            "확인할 수 없습니다. 기관의 장기요양기관 지정서·급여제공 관련 내부 등록정보 또는 "
            "담당 관리자 설정에서 확인해 주세요."
        )
    if _VERSION_SENSITIVE_INDICATOR.search(question):
        return (
            "평가지표 번호는 연도와 평가판본에 따라 달라질 수 있어 현재 질문만으로 내용을 "
            "확정할 수 없습니다. 적용 연도와 시설급여·재가급여 등 평가판본을 알려주시거나 "
            "공식 평가매뉴얼을 첨부해 주세요."
        )
    notices: list[str] = []
    if _CLINICAL_DECISION.search(question):
        notices.append(
            "진단·투약·처치 같은 결정은 MESIL AI가 자동으로 결정할 수 없습니다. "
            "관찰 사실을 확인한 뒤 담당자와 의료전문가의 판단을 받으세요."
        )
    if _CURRENT_POLICY.search(question):
        notices.append(
            "최신 제도·공단 기준은 최신 원문과 기관 지침을 직접 확인한 뒤 적용하세요."
        )
    return "\n\n".join([answer.strip(), *notices]).strip()
