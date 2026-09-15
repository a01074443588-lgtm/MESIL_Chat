from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from io import BytesIO
from pathlib import Path
import re
from typing import Any

import pypdfium2 as pdfium
from pypdf import PdfReader

from .new_admission_contract import (
    NEW_ADMISSION_DOCUMENT_TYPES,
    NewAdmissionDraftBundle,
)
from .new_admission_form_workspace import build_new_admission_form_workspace


MAX_ASSESSMENT_FILES = 20
MAX_ASSESSMENT_PDF_PAGES = 100
AUTO_DOCUMENT_KIND = "auto"


DOCUMENT_KIND_LABELS = {
    "care_grade_certificate": "장기요양인정서",
    "individual_long_term_care_plan": "개인별장기요양이용계획서",
    "prescription": "처방전·복약안내",
    "health_submission": "건강 제출자료",
    "transfer_document": "전원 관련 서류",
    "welfare_equipment": "복지용구 자료",
    "consultation_log": "상담일지",
    "administrative_guide": "안내·행정자료",
    "previous_fall_assessment": "이전 낙상위험도",
    "previous_pressure_ulcer_assessment": "이전 욕창위험도",
    "previous_cognitive_assessment": "이전 인지기능검사",
    "previous_needs_assessment": "이전 욕구사정",
    "previous_care_plan": "이전 장기요양급여 제공계획",
    "previous_care_plan_evaluation": "이전 급여제공계획 결과평가",
    "other": "기타 제출 자료",
}


NEW_ADMISSION_INPUT_KINDS = (
    "care_grade_certificate",
    "individual_long_term_care_plan",
    "prescription",
    "health_submission",
    "transfer_document",
    "welfare_equipment",
    "consultation_log",
    "administrative_guide",
    "other",
)


REASSESSMENT_INPUT_KINDS = (
    "previous_fall_assessment",
    "previous_pressure_ulcer_assessment",
    "previous_cognitive_assessment",
    "previous_needs_assessment",
    "previous_care_plan",
    "previous_care_plan_evaluation",
    "health_submission",
    "transfer_document",
    "welfare_equipment",
    "consultation_log",
    "administrative_guide",
    "other",
)


REASSESSMENT_BASELINE_DOCUMENT_KINDS = frozenset(
    {
        "previous_fall_assessment",
        "previous_pressure_ulcer_assessment",
        "previous_cognitive_assessment",
        "previous_needs_assessment",
        "previous_care_plan",
        "previous_care_plan_evaluation",
    }
)


DOCUMENT_KIND_HINTS: dict[str, tuple[str, ...]] = {
    "care_grade_certificate": (
        "장기요양인정서",
        "장기요양 인정서",
        "장기요양등급",
        "인정번호",
    ),
    "individual_long_term_care_plan": (
        "개인별장기요양이용계획서",
        "개인별 장기요양 이용계획서",
        "장기요양이용계획",
        "개인별이용계획",
    ),
    "prescription": (
        "처방전",
        "복약안내문",
        "복약안내",
        "처방의약품",
        "처방 내용",
    ),
    "health_submission": (
        "건강검진 결과서",
        "건강검진결과서",
        "건강진단서",
        "입소 전 건강검진",
    ),
    "transfer_document": (
        "소견서",
        "전원 관련",
        "전원소견",
        "입원기록",
        "향후치료의견",
        "진료의뢰서",
        "진료의뢰",
        "퇴원요약",
    ),
    "welfare_equipment": (
        "복지용구 급여확인서",
        "복지용구급여확인서",
        "복지용구 확인서",
    ),
    "consultation_log": (
        "상담일지",
        "초기 상담일지",
        "상담 내용",
        "상담 결과",
    ),
    "administrative_guide": (
        "작성 안내",
        "발급 안내",
        "이용 안내",
        "관련 법령",
        "발급 절차",
    ),
    "previous_fall_assessment": ("낙상위험도 평가지", "낙상위험도", "낙상 위험"),
    "previous_pressure_ulcer_assessment": (
        "욕창위험도 평가지",
        "욕창위험도",
        "욕창 위험",
    ),
    "previous_cognitive_assessment": (
        "인지기능검사",
        "치매진단검사",
        "인지 기능 검사",
    ),
    "previous_needs_assessment": ("욕구사정 평가지", "욕구사정", "욕구 사정"),
    "previous_care_plan_evaluation": (
        "급여제공계획 결과평가",
        "급여 제공계획 결과평가",
        "계획 결과평가",
    ),
    "previous_care_plan": (
        "장기요양급여 제공계획서",
        "장기요양급여 제공 계획서",
        "장기요양급여 제공계획",
        "급여제공계획서",
    ),
}


@dataclass(frozen=True)
class ParsedAssessmentMaterial:
    source_ref: str
    document_kind: str
    mime_type: str
    page_count: int
    size_bytes: int
    extracted_text: str
    page_numbers: tuple[int, ...] = ()
    classification_method: str = "manual"


_DECLARED_TARGET_CODE_PATTERN = re.compile(
    r"(?:대상\s*코드|수급자\s*코드|어르신\s*코드)\s*[:：]\s*(어르\d{4,})"
)


def extract_declared_target_codes(
    materials: list[ParsedAssessmentMaterial],
) -> tuple[str, ...]:
    """Return only explicit coded resident identifiers from submitted pages.

    Names and surrounding prose are intentionally ignored.  A missing code is
    allowed because ordinary source documents do not necessarily carry the
    synthetic DEV identifier.
    """

    codes = {
        match.group(1)
        for material in materials
        for match in _DECLARED_TARGET_CODE_PATTERN.finditer(material.extracted_text)
    }
    return tuple(sorted(codes))


@dataclass(frozen=True)
class FieldRule:
    key: str
    label: str
    aliases: tuple[str, ...]
    document_types: tuple[str, ...]
    value_kind: str = "fact"


FIELD_RULES: tuple[FieldRule, ...] = (
    FieldRule(
        "long_term_care_grade",
        "장기요양등급",
        ("장기요양등급", "인정등급"),
        tuple(NEW_ADMISSION_DOCUMENT_TYPES),
        "administrative",
    ),
    FieldRule(
        "benefit_effective_period",
        "인정·이용계획 적용기간",
        ("적용기간", "유효기간", "급여 적용기간"),
        ("needs_assessment", "long_term_care_service_plan"),
        "administrative",
    ),
    FieldRule(
        "benefit_type",
        "확인된 급여 종류",
        ("급여 종류", "급여종류", "장기요양급여 종류"),
        ("needs_assessment", "long_term_care_service_plan"),
        "administrative",
    ),
    FieldRule(
        "resident_age",
        "연령",
        ("연령", "만 나이"),
        ("fall_risk_assessment", "needs_assessment"),
    ),
    FieldRule(
        "mobility_support_need",
        "이동 상태와 지원 필요",
        ("이동 상태", "보행 상태", "이동지원"),
        ("fall_risk_assessment", "needs_assessment", "long_term_care_service_plan"),
    ),
    FieldRule(
        "fall_history",
        "낙상 이력",
        ("낙상 이력", "최근 낙상"),
        ("fall_risk_assessment", "needs_assessment", "long_term_care_service_plan"),
    ),
    FieldRule(
        "recent_fall_count",
        "최근 낙상 횟수",
        ("최근 낙상 횟수", "낙상 횟수"),
        ("fall_risk_assessment", "needs_assessment", "long_term_care_service_plan"),
    ),
    FieldRule(
        "skin_state",
        "피부·욕창 관련 상태",
        ("피부 상태", "욕창 상태"),
        ("pressure_ulcer_risk_assessment", "needs_assessment", "long_term_care_service_plan"),
    ),
    FieldRule(
        "skin_moisture_current",
        "현재 피부 습기 상태",
        ("피부 습기", "습기 상태"),
        ("pressure_ulcer_risk_assessment", "needs_assessment"),
    ),
    FieldRule(
        "cognitive_observation",
        "인지·의사소통 관찰",
        ("인지 상태", "인지 관찰", "의사소통"),
        ("cognitive_function_assessment", "needs_assessment", "long_term_care_service_plan"),
    ),
    FieldRule(
        "cognitive_assessment_decision",
        "인지기능검사 시행·예외 확인",
        ("검사 시행", "검사 예외", "검사 불가 사유"),
        ("cognitive_function_assessment",),
    ),
    FieldRule(
        "cognitive_orientation_result",
        "지남력 결과",
        ("지남력 결과",),
        ("cognitive_function_assessment",),
    ),
    FieldRule(
        "cognitive_memory_result",
        "기억력 결과",
        ("기억력 결과",),
        ("cognitive_function_assessment",),
    ),
    FieldRule(
        "cognitive_attention_result",
        "주의집중·계산 결과",
        ("주의집중 결과", "계산 결과"),
        ("cognitive_function_assessment",),
    ),
    FieldRule(
        "cognitive_language_result",
        "언어·실행 결과",
        ("언어 결과", "실행 결과"),
        ("cognitive_function_assessment",),
    ),
    FieldRule(
        "nutrition_state",
        "영양·식사 상태",
        ("영양 상태", "식사 상태", "식사량"),
        ("needs_assessment", "long_term_care_service_plan"),
    ),
    FieldRule(
        "hydration_support_goal",
        "수분 섭취와 지원 내용",
        ("수분 상태", "수분 관리", "수분 지원"),
        ("needs_assessment", "long_term_care_service_plan"),
    ),
    FieldRule(
        "elimination_state",
        "배변·배뇨 상태",
        ("배변 상태", "배뇨 상태", "배설 상태"),
        ("needs_assessment", "long_term_care_service_plan"),
    ),
    FieldRule(
        "medication_information",
        "투약 관련 확인 내용",
        ("투약 정보", "복용 약", "처방 내용", "약품명"),
        ("needs_assessment", "long_term_care_service_plan"),
    ),
    FieldRule(
        "medication_name",
        "약품명",
        ("약품명", "의약품명", "제품명"),
        ("fall_risk_assessment", "needs_assessment", "long_term_care_service_plan"),
    ),
    FieldRule(
        "medication_method",
        "복용법·용량",
        ("복용방법", "복용법", "용법", "용량"),
        ("fall_risk_assessment", "needs_assessment", "long_term_care_service_plan"),
    ),
    FieldRule(
        "medication_period",
        "복용기간",
        ("복용기간", "투약일수", "처방일수"),
        ("fall_risk_assessment", "needs_assessment", "long_term_care_service_plan"),
    ),
    FieldRule(
        "oral_state",
        "구강·치아 상태",
        ("구강 상태", "치아 상태", "의치 상태"),
        ("needs_assessment", "long_term_care_service_plan"),
    ),
    FieldRule(
        "medical_diagnosis",
        "진단·질환 관련 확인",
        ("확인된 질환", "질병명", "병명", "의사 소견"),
        ("needs_assessment", "long_term_care_service_plan"),
        "diagnosis",
    ),
    FieldRule(
        "daily_living_state",
        "일상생활 수행 상태",
        ("일상생활 수행", "일상생활 상태", "ADL"),
        ("needs_assessment", "long_term_care_service_plan"),
    ),
    FieldRule(
        "communication_state",
        "의사소통 상태",
        ("의사소통 상태", "의사 표현", "소통 상태"),
        ("cognitive_function_assessment", "needs_assessment", "long_term_care_service_plan"),
    ),
    FieldRule(
        "family_environment",
        "가족·보호자 관계",
        ("가족 관계", "보호자 관계", "가족·환경"),
        ("needs_assessment", "long_term_care_service_plan"),
    ),
    FieldRule(
        "admission_room",
        "생활환경",
        ("생활실", "방 번호", "생활환경"),
        ("needs_assessment",),
        "administrative",
    ),
    FieldRule(
        "resource_use",
        "의료·지역사회 자원 이용",
        ("의료기관 이용", "자원 이용", "지역사회 자원"),
        ("needs_assessment", "long_term_care_service_plan"),
    ),
    FieldRule(
        "rehabilitation_need",
        "재활·기능 유지 필요",
        ("재활 필요", "기능훈련", "기능 유지"),
        ("needs_assessment", "long_term_care_service_plan"),
    ),
    FieldRule(
        "subjective_need",
        "본인·보호자 요청",
        ("본인 욕구", "보호자 요청", "주관적 욕구"),
        ("needs_assessment", "long_term_care_service_plan"),
    ),
    FieldRule(
        "needs_overall_opinion",
        "욕구사정 종합의견",
        ("욕구사정 종합의견", "욕구사정 총평"),
        ("needs_assessment",),
    ),
    FieldRule(
        "service_plan_goal",
        "급여제공계획 목표 참고 내용",
        ("계획 목표", "지원 목표", "급여 목표"),
        ("long_term_care_service_plan",),
    ),
    FieldRule(
        "care_plan_overall_opinion",
        "급여제공계획 종합의견",
        ("급여제공계획 종합의견", "계획 종합의견"),
        ("long_term_care_service_plan",),
    ),
    FieldRule(
        "care_plan_change_reason",
        "급여제공계획 변경사유",
        ("계획 변경사유", "급여제공계획 변경사유"),
        ("long_term_care_service_plan",),
    ),
    FieldRule(
        "fall_score",
        "낙상위험도 점수",
        ("낙상위험도 점수", "낙상 점수"),
        ("fall_risk_assessment",),
        "assessment_score",
    ),
    FieldRule(
        "pressure_ulcer_score",
        "욕창위험도 점수",
        ("욕창위험도 점수", "욕창 점수"),
        ("pressure_ulcer_risk_assessment",),
        "assessment_score",
    ),
    FieldRule(
        "cognitive_score",
        "인지기능검사 점수",
        ("인지기능검사 점수", "인지 점수"),
        ("cognitive_function_assessment",),
        "assessment_score",
    ),
    FieldRule(
        "fall_risk_judgment",
        "낙상위험도 판정",
        ("낙상위험도 판정",),
        ("fall_risk_assessment",),
        "assessment_score",
    ),
    FieldRule(
        "pressure_ulcer_judgment",
        "욕창위험도 판정",
        ("욕창위험도 판정",),
        ("pressure_ulcer_risk_assessment",),
        "assessment_score",
    ),
    FieldRule(
        "cognitive_judgment",
        "인지기능검사 판정",
        ("인지기능검사 판정",),
        ("cognitive_function_assessment",),
        "diagnosis",
    ),
    FieldRule(
        "staff_signature",
        "직원 확인·서명",
        ("직원 서명",),
        tuple(NEW_ADMISSION_DOCUMENT_TYPES),
        "signature",
    ),
)


DOCUMENT_KIND_PRIMARY_TITLES: dict[str, tuple[str, ...]] = {
    "care_grade_certificate": ("장기요양인정서",),
    "individual_long_term_care_plan": ("개인별장기요양이용계획서",),
    "prescription": ("처방전", "복약안내문"),
    "health_submission": ("건강검진결과서", "건강진단서"),
    "transfer_document": ("소견서", "전원소견서", "진료의뢰서", "퇴원요약서"),
    "welfare_equipment": ("복지용구급여확인서", "복지용구확인서"),
    "consultation_log": ("상담일지", "초기상담일지"),
}


NON_FACT_DOCUMENT_KINDS = {"other", "administrative_guide"}


def _clean_text(value: str) -> str:
    return re.sub(r"[ \t]+", " ", value.replace("\x00", " ")).strip()


def allowed_document_kinds(reason: str) -> tuple[str, ...]:
    return (
        NEW_ADMISSION_INPUT_KINDS
        if reason == "new_admission"
        else REASSESSMENT_INPUT_KINDS
    )


def parse_document_kind_selection(
    value: str,
    *,
    reason: str,
) -> tuple[str, ...] | None:
    selected = tuple(
        dict.fromkeys(item.strip() for item in value.split(",") if item.strip())
    )
    if selected == (AUTO_DOCUMENT_KIND,):
        return None
    allowed = set(allowed_document_kinds(reason))
    if not selected or AUTO_DOCUMENT_KIND in selected or any(
        item not in allowed for item in selected
    ):
        raise ValueError("자료 종류를 자동 분류하거나 올바른 종류로 선택해 주세요.")
    return selected


def extract_pdf_page_texts(content: bytes) -> tuple[int, list[str]]:
    if not content.startswith(b"%PDF"):
        raise ValueError("PDF 파일 형식이 아닙니다.")
    try:
        reader = PdfReader(BytesIO(content), strict=False)
    except Exception as error:
        raise ValueError("PDF를 열 수 없습니다. 손상 여부를 확인해 주세요.") from error
    page_count = len(reader.pages)
    if page_count < 1 or page_count > MAX_ASSESSMENT_PDF_PAGES:
        raise ValueError("PDF는 1쪽 이상 100쪽 이하만 제출할 수 있습니다.")
    page_texts: list[str] = []
    for page in reader.pages:
        try:
            page_texts.append(_clean_text(page.extract_text() or ""))
        except Exception:
            page_texts.append("")
    return page_count, page_texts


def render_pdf_pages(
    content: bytes,
    *,
    output_directory: Path,
    page_indexes: list[int],
) -> dict[int, Path]:
    """Render only pages that need local OCR; the source PDF remains unchanged."""

    try:
        document = pdfium.PdfDocument(content)
    except Exception as error:
        raise ValueError("스캔 PDF 페이지를 열 수 없습니다.") from error
    rendered: dict[int, Path] = {}
    try:
        for page_index in page_indexes:
            page = document[page_index]
            try:
                bitmap = page.render(scale=2.0)
                image = bitmap.to_pil().convert("RGB")
                target = output_directory / f"page-{page_index + 1:03d}.png"
                image.save(target, format="PNG", optimize=True)
                rendered[page_index] = target
            finally:
                page.close()
    except Exception as error:
        raise ValueError("스캔 PDF 페이지를 이미지로 준비하지 못했습니다.") from error
    finally:
        document.close()
    return rendered


def classify_document_kinds(
    text: str,
    *,
    reason: str,
) -> tuple[str, ...]:
    lines = [
        re.sub(r"\s+", "", line).lower()
        for line in text.splitlines()
        if line.strip()
    ]
    compact = "".join(lines)

    # 안내문은 실제 대상자 서식과 문서 제목을 공유할 수 있다. 발급 절차나
    # 법령 안내가 함께 있으면 대상자 사실 근거가 아닌 안내·행정자료로 먼저
    # 분리해 초안 본문에 섞이지 않게 한다.
    guide_markers = tuple(
        re.sub(r"\s+", "", marker).lower()
        for marker in DOCUMENT_KIND_HINTS["administrative_guide"]
    )
    if any(marker in compact for marker in guide_markers):
        return ("administrative_guide",)

    # 첫 제목줄은 본문에 인용된 다른 서식명보다 우선한다. 한 표지에 인정서와
    # 개인별 이용계획서 제목이 함께 적힌 경우에는 둘 다 보존한다.
    first_line = lines[0] if lines else ""
    primary_matches = [
        kind
        for kind, titles in DOCUMENT_KIND_PRIMARY_TITLES.items()
        if kind in allowed_document_kinds(reason)
        and any(
            re.sub(r"\s+", "", title).lower() in first_line
            for title in titles
        )
    ]
    if primary_matches:
        return tuple(primary_matches)

    scored: list[tuple[int, str]] = []
    for kind in allowed_document_kinds(reason):
        if kind in {"other", "administrative_guide"}:
            continue
        score = 0
        for position, hint in enumerate(DOCUMENT_KIND_HINTS.get(kind, ())):
            normalized_hint = re.sub(r"\s+", "", hint).lower()
            if normalized_hint and normalized_hint in compact:
                score += 5 if position == 0 else 2
        if score:
            scored.append((score, kind))
    if not scored:
        return ("other",)
    maximum = max(score for score, _ in scored)
    # 한 페이지에 두 공식 제목이 함께 있으면 둘 다 보존한다. 일반 키워드만
    # 겹친 경우에는 가장 강한 문서 종류만 선택해 중복 근거를 만들지 않는다.
    selected = [
        kind
        for score, kind in scored
        if score == maximum or score >= 5
    ]
    # 결과평가 문서는 제목 안에 급여제공계획을 함께 포함한다. 더 구체적인
    # 결과평가 표식이 확인되면 같은 페이지를 직전 계획으로 중복 분류하지 않는다.
    if (
        "previous_care_plan_evaluation" in selected
        and "previous_care_plan" in selected
    ):
        selected.remove("previous_care_plan")
    return tuple(dict.fromkeys(selected))


def build_parsed_materials(
    *,
    page_texts: list[str],
    source_prefix: str,
    selected_kinds: tuple[str, ...] | None,
    reason: str,
    mime_type: str,
    size_bytes: int,
) -> list[ParsedAssessmentMaterial]:
    if not any(text.strip() for text in page_texts):
        raise ValueError("자료에서 글자를 확인할 수 없습니다.")
    pages_by_kind: dict[str, list[tuple[int, str]]] = {}
    if selected_kinds is None:
        for page_number, text in enumerate(page_texts, start=1):
            if not text.strip():
                continue
            for kind in classify_document_kinds(text, reason=reason):
                pages_by_kind.setdefault(kind, []).append((page_number, text))
        # 한 파일에서 공식 문서 종류가 하나만 확인된 경우, 제목이 반복되지 않는
        # 이어지는 쪽을 별도 '기타 자료'로 쪼개지 않는다. 여러 문서 종류가 실제로
        # 함께 확인된 복합 PDF에서는 이 보정을 적용하지 않아 페이지별 분류를 유지한다.
        identified_kinds = [kind for kind in pages_by_kind if kind != "other"]
        if len(identified_kinds) == 1 and pages_by_kind.get("other"):
            identified_kind = identified_kinds[0]
            pages_by_kind[identified_kind] = sorted(
                [
                    *pages_by_kind[identified_kind],
                    *pages_by_kind.pop("other"),
                ],
                key=lambda item: item[0],
            )
        classification_method = "automatic"
    else:
        populated_pages = [
            (index, text)
            for index, text in enumerate(page_texts, start=1)
            if text.strip()
        ]
        classification_method = "manual"
        for kind in selected_kinds:
            pages_by_kind[kind] = populated_pages

    materials: list[ParsedAssessmentMaterial] = []
    for material_index, (kind, pages) in enumerate(pages_by_kind.items(), start=1):
        page_numbers = tuple(page_number for page_number, _ in pages)
        extracted_text = "\n".join(
            f"[제출 자료 {page_number}쪽]\n{text}" for page_number, text in pages
        ).strip()
        materials.append(
            ParsedAssessmentMaterial(
                source_ref=(
                    source_prefix
                    if len(pages_by_kind) == 1
                    else f"{source_prefix}-{material_index:02d}"
                ),
                document_kind=kind,
                mime_type=mime_type,
                page_count=max(1, len(page_numbers)),
                size_bytes=size_bytes,
                extracted_text=extracted_text[:100_000],
                page_numbers=page_numbers,
                classification_method=classification_method,
            )
        )
    return materials


def extract_pdf_material(
    content: bytes,
    *,
    source_ref: str,
    document_kind: str,
) -> ParsedAssessmentMaterial:
    page_count, page_texts = extract_pdf_page_texts(content)
    extracted_text = "\n".join(text for text in page_texts if text).strip()
    if not extracted_text:
        raise ValueError(
            "글자를 확인할 수 없는 PDF입니다. 스캔 이미지는 직원 확인 자료로 남기고 "
            "이번 초안에는 자동 반영하지 않습니다."
        )
    return ParsedAssessmentMaterial(
        source_ref=source_ref,
        document_kind=document_kind,
        mime_type="application/pdf",
        page_count=page_count,
        size_bytes=len(content),
        extracted_text=extracted_text[:100_000],
        page_numbers=tuple(range(1, page_count + 1)),
    )


def _source_contract(material: ParsedAssessmentMaterial) -> dict[str, Any]:
    if material.document_kind in {
        "care_grade_certificate",
        "individual_long_term_care_plan",
    }:
        source_type = "public_insurer_data"
        organization_role = "public_insurer"
    elif material.document_kind == "consultation_log":
        source_type = "consultation"
        organization_role = "authoring_organization"
    elif material.document_kind == "administrative_guide":
        source_type = "medical_or_submitted_document"
        organization_role = "not_applicable"
    else:
        source_type = "medical_or_submitted_document"
        organization_role = "reference_organization"
    return {
        "source_ref": material.source_ref,
        "source_type": source_type,
        "document_kind": DOCUMENT_KIND_LABELS[material.document_kind],
        "organization_role": organization_role,
        "reference_locator": (
            "제출 자료 "
            + ", ".join(f"{page}쪽" for page in material.page_numbers)
            if material.page_numbers
            else "제출 자료 내부 확인"
        ),
        "evidence_summary": "제출된 자료의 명시된 항목을 로컬에서 판독했습니다.",
        "verification_state": (
            "needs_review"
            if material.document_kind in NON_FACT_DOCUMENT_KINDS
            else "verified"
        ),
        "contains_sensitive_data": False,
    }


def _material_display_label(
    material: ParsedAssessmentMaterial,
    *,
    fallback_index: int,
) -> str:
    source_match = re.match(r"^material-(\d{2})", material.source_ref)
    file_index = int(source_match.group(1)) if source_match else fallback_index
    base = f"제출 자료 {file_index}"
    if material.classification_method == "manual" and material.source_ref == f"material-{file_index:02d}":
        return base
    page_label = (
        ", ".join(f"{page}쪽" for page in material.page_numbers)
        if material.page_numbers
        else "전체"
    )
    return f"{base} · {page_label}"


def _source_lines(text: str) -> list[str]:
    return [
        _clean_text(line).strip(" .")
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("[제출 자료 ")
    ]


_TABLE_EXTRA_ALIASES: dict[str, tuple[str, ...]] = {
    "benefit_type": ("서비스 구분",),
    "mobility_support_need": ("이동 상태", "이동"),
    "nutrition_state": ("영양·식사", "영양 상태", "식사"),
    "hydration_support_goal": ("수분 상태", "수분"),
    "elimination_state": ("배설 상태", "배설"),
    "skin_state": ("피부 상태",),
    "cognitive_observation": ("인지 상태", "인지"),
    "communication_state": ("의사소통 상태", "의사소통"),
}

_TABLE_STRUCTURAL_PREFIXES = (
    "항목 확인 내용",
    "확인된 이용 목표",
    "제공 참고",
    "확인 범위",
    "확인 제한",
    "기초 건강",
    "생활 기능",
    "복용 참고",
    "대조",
    "대상·유효 정보",
    "이용 참고",
    "안전",
)


def _table_row_labels() -> tuple[str, ...]:
    labels = {
        alias
        for rule in FIELD_RULES
        for alias in (*rule.aliases, *_TABLE_EXTRA_ALIASES.get(rule.key, ()))
    }
    labels.update(
        {
            "대상 코드",
            "입소 기준일",
            "유효 상태",
            "처방 항목",
            "복용 지원",
            "복용 시점",
            "용량",
            "변경 여부",
            "진단",
            "현재 관찰",
            "검사 수치",
            "전문가 판정",
            "자동 확정",
            "공식 기록",
            "외부전송",
            "공단 전송",
            "서명",
            "직원 확인",
        }
    )
    return tuple(sorted(labels, key=len, reverse=True))


def _is_table_boundary(line: str) -> bool:
    if line.startswith(_TABLE_STRUCTURAL_PREFIXES):
        return True
    if line.startswith("합성 ") and len(line) <= 40:
        return True
    return any(
        re.match(rf"^{re.escape(label)}(?:\s|[:：]|$)", line, re.IGNORECASE)
        for label in _table_row_labels()
    )


def _strip_synthetic_table_metadata(value: str, *, source_text: str) -> str:
    if "DEV 비공식 자료" not in source_text:
        return value
    synthetic_suffixes = (
        "합성 인정 정보",
        "합성 기준일",
        "합성 문서",
        "합성 이용계획",
        "합성 검진 관찰",
        "합성 문진",
        "합성 상담",
        "합성 처방 정보",
        "합성 코드",
        "실제 약명 아님",
        "이전 평가 관찰",
        "이전 식사 기록",
        "이전 검사 관찰",
    )
    cleaned = value
    for suffix in synthetic_suffixes:
        if cleaned.endswith(f" {suffix}"):
            cleaned = cleaned[: -len(suffix)].rstrip()
            break
    return cleaned


def _table_row_values(text: str, aliases: tuple[str, ...]) -> list[str]:
    """Read explicit label/value rows whose PDF text layer omits colons."""

    lines = _source_lines(text)
    values: list[str] = []
    for index, line in enumerate(lines):
        for alias in sorted(aliases, key=len, reverse=True):
            match = re.match(
                rf"^(?:어르\d{{4}}\s+)?{re.escape(alias)}\s+(.+)$",
                line,
                re.IGNORECASE,
            )
            if not match:
                continue
            parts = [match.group(1).strip()]
            for continuation in lines[index + 1 : index + 4]:
                if _is_table_boundary(continuation):
                    break
                parts.append(continuation)
            value = _clean_text(" ".join(parts)).strip(" .")
            value = re.sub(r"\s*·\s*", "·", value)
            value = _strip_synthetic_table_metadata(value, source_text=text)
            if value and len(value) <= 180:
                values.append(value)
            break
    return list(dict.fromkeys(values))


def _bounded_source_join(
    values: list[str],
    *,
    limit: int = 480,
    item_limit: int | None = None,
) -> str | None:
    unique = list(dict.fromkeys(value for value in values if value))
    selected: list[str] = []
    current_length = 0
    for value in unique:
        if item_limit is not None and len(value) > item_limit:
            continue
        addition = len(value) + (3 if selected else 0)
        if current_length + addition > limit:
            break
        selected.append(value)
        current_length += addition
    return " / ".join(selected) if selected else None


def _label_values(text: str, label: str) -> list[str]:
    values: list[str] = []
    pattern = re.compile(
        rf"(?:^|\n)\s*{re.escape(label)}\s*[:：]\s*([^\n]+)",
        re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        value = _clean_text(match.group(1)).strip(" .")
        if value:
            values.append(value)
    return values


def _first_bounded_label_value(
    text: str,
    labels: tuple[str, ...],
    *,
    limit: int = 180,
) -> str | None:
    """Return one explicit short label value without interpreting narrative text."""

    for label in labels:
        values = _label_values(text, label)
        if not values:
            continue
        value = values[0]
        if len(value) <= limit:
            return value
    return None


def _requires_current_reconfirmation(text: str) -> bool:
    value = _first_bounded_label_value(
        text,
        (
            "현재 상태 재확인 필요",
            "현재 재확인 필요",
            "현재 상태 확인 필요",
        ),
        limit=40,
    )
    if value is None:
        return False
    compact = re.sub(r"\s+", "", value).lower()
    if any(marker in compact for marker in ("아니오", "불필요", "필요없", "false", "no")):
        return False
    return compact in {"예", "네", "y", "yes", "true"} or any(
        marker in compact for marker in ("필요", "재확인", "확인요")
    )


def _source_context_facts(material: ParsedAssessmentMaterial) -> list[dict[str, Any]]:
    """Keep document context separate from form-ready resident facts."""

    facts: list[dict[str, Any]] = []

    def append_fact(field_key: str, label: str, value: str | None) -> None:
        if value is None:
            return
        facts.append(
            {
                "field_key": field_key,
                "label": label,
                "value": value,
                "status": "source_context",
                "document_types": [],
            }
        )

    text = material.extracted_text
    if material.document_kind == "consultation_log":
        context_rules = (
            (
                "consultation_datetime",
                "상담 일시",
                ("상담 일시", "상담일시", "상담 날짜", "상담일"),
            ),
            (
                "consultation_method",
                "상담 방법",
                ("상담 방법", "상담방법", "상담 형태", "상담 경로"),
            ),
            (
                "consultation_counterparty",
                "상담 대상",
                ("상담 대상", "상담대상", "상담 상대", "상담대상자"),
            ),
            (
                "consultation_reason",
                "상담 사유",
                ("상담 사유", "상담사유", "상담 목적"),
            ),
            (
                "consultation_content",
                "상담 내용",
                ("상담 내용", "상담내용"),
            ),
            (
                "consultation_action",
                "상담 후 조치",
                ("상담 조치", "조치 내용", "조치사항", "처리 내용"),
            ),
            (
                "consultation_result",
                "상담 결과",
                ("상담 결과", "상담결과", "처리 결과"),
            ),
            (
                "consultation_follow_up",
                "후속 확인",
                ("추후 계획", "후속 계획", "후속 확인", "향후 계획"),
            ),
        )
        for field_key, label, aliases in context_rules:
            append_fact(
                field_key,
                label,
                _first_bounded_label_value(text, aliases),
            )

    if material.document_kind == "transfer_document":
        append_fact(
            "source_organization",
            "작성기관",
            _first_bounded_label_value(
                text,
                ("작성기관", "전원기관", "이전기관", "의료기관"),
            ),
        )

    if material.document_kind in {"transfer_document", "consultation_log"} and (
        _requires_current_reconfirmation(text)
    ):
        append_fact(
            "source_currentness",
            "자료 현재성",
            "현재 상태 재확인 필요",
        )
    return facts


def _source_clauses(text: str) -> list[str]:
    """OCR 긴 문단을 원문 근거가 유지되는 짧은 문장 단위로 나눈다."""

    clauses: list[str] = []
    for line in _source_lines(text):
        for clause in re.split(
            r"(?<=[.!?。])\s+|(?<=[다함됨음])\s+(?=[가-힣A-Za-z0-9])",
            line,
        ):
            value = _clean_text(clause).strip(" .")
            value = re.sub(
                r"^(?:입원기록|향후치료의견|치료의견|소견)\s*[:：]\s*",
                "",
                value,
            )
            if value:
                clauses.append(value)
    return clauses


def _marker_clauses(
    text: str,
    *,
    markers: tuple[str, ...],
    support_markers: tuple[str, ...] = (),
    limit: int = 180,
) -> str | None:
    candidates = [
        clause
        for clause in _source_clauses(text)
        if any(marker in clause for marker in markers)
        and (
            not support_markers
            or any(marker in clause for marker in support_markers)
        )
    ]
    return _bounded_source_join(candidates, limit=limit, item_limit=limit)


def _pattern_facts(
    text: str,
    *,
    patterns: tuple[str, ...],
    limit: int = 80,
) -> str | None:
    """문장부호가 사라진 OCR에서도 항목 주변의 짧은 사실만 보존한다."""

    source = re.sub(r"\s+", " ", _clean_text(text))
    values: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, source, re.IGNORECASE):
            value = _clean_text(match.group(0)).strip(" ()[]{}.,:;·/~-을를은는이가으로")
            if value and value not in values:
                values.append(value)
    non_overlapping = [
        value
        for value in values
        if not any(value != other and value in other for other in values)
    ]
    return _bounded_source_join(non_overlapping, limit=limit, item_limit=limit)


def _transfer_atomic_fact(text: str, field_key: str) -> str | None:
    patterns_by_field: dict[str, tuple[str, ...]] = {
        "mobility_support_need": (
            r"(?:거동|보행|이동)[^,.;()\n\d]{0,28}?(?:문제\s*없(?:음|고|다)?|독립(?:적)?|혼자(?:서)?|부축(?:이|\s)*(?:필요|함)?|도움(?:이|\s)*(?:필요|함)?|휠체어(?:\s*사용)?)",
            r"(?:목욕|세면)[^,.;()\n\d]{0,20}?(?:혼자(?:서)?(?:함|하(?:고|며|심)?)?|도움(?:이|\s)*(?:필요|함)?|부축(?:이|\s)*(?:필요|함)?)",
        ),
        "daily_living_state": (
            r"(?:거동|보행|이동)[^,.;()\n\d]{0,28}?(?:문제\s*없(?:음|고|다)?|독립(?:적)?|혼자(?:서)?|부축(?:이|\s)*(?:필요|함)?|도움(?:이|\s)*(?:필요|함)?|휠체어(?:\s*사용)?)",
            r"(?:목욕|세면)[^,.;()\n\d]{0,20}?(?:혼자(?:서)?(?:함|하(?:고|며|심)?)?|도움(?:이|\s)*(?:필요|함)?|부축(?:이|\s)*(?:필요|함)?)",
        ),
        "nutrition_state": (
            r"(?:식사|섭취)(?:도|는|량|\s*상태)?[^,.;()\n\d]{0,28}?(?:혼자(?:서)?|잘\s*(?:드심|드시|먹|섭취)|도움(?:이|\s)*(?:필요|함)?)",
            r"(?:식사|섭취)[^,.;()\n]{0,20}?\d+(?:/\d+)?\s*(?:정도)?\s*섭취",
        ),
        "resource_use": (
            r"(?:주간보호센터|주간보호|요양시설|의료기관)[^,.;()\n\d]{0,20}?(?:이용(?:\s*중)?|입소|방문|진료)",
        ),
        "skin_state": (
            r"(?:피부|천골|둔부|등|발뒤꿈치)[^,.;()\n\d]{1,60}",
            r"(?:욕창|병변|발적|발진)[^,.;()\n\d]{0,40}",
        ),
    }
    patterns = patterns_by_field.get(field_key)
    if not patterns:
        return None
    return _pattern_facts(text, patterns=patterns)


def _prescription_facts(text: str) -> dict[str, str | None]:
    lines = _source_lines(text)
    excluded_starts = (
        "처방전",
        "복약안내",
        "의약품",
        "제품명",
        "백색",
        "흰색",
        "필름",
        "장방형",
        "원형",
        "진료과",
    )
    medication_names: list[str] = []
    medication_methods: list[str] = []
    medication_periods: list[str] = []
    for line in lines:
        contains_metadata = any(
            marker in line
            for marker in (
                "[효능]",
                "[앞]",
                "[뒤]",
                "효능·효과",
                "성상",
                "저장방법",
            )
        )
        label_match = re.match(
            r"^(?:약품명|제품명)\s*[:：]\s*(.+)$",
            line,
            re.IGNORECASE,
        )
        name_candidate = label_match.group(1) if label_match else line
        name_candidate = re.split(
            r"\s*(?:\[효능\]|\[앞\]|\[뒤\]|효능·효과|성상|저장방법)",
            name_candidate,
            maxsplit=1,
        )[0].strip(" .")
        compact = name_candidate.replace(" ", "")
        appearance_or_admin = bool(
            re.search(
                r"(?:진료과|가정의학과|내과|외과|정형외과|신경과|피부과|"
                r"장방형|원형|백색|흰색|박하향|박하미|필름코팅|정제|제형|성상)",
                name_candidate,
                re.IGNORECASE,
            )
        )
        unsupported_labeled_value = bool(":" in name_candidate or "：" in name_candidate)
        if (
            2 <= len(name_candidate) <= 80
            and not name_candidate.startswith(excluded_starts)
            and not appearance_or_admin
            and not unsupported_labeled_value
            and re.search(
                r"(?:정(?:\d+(?:\.\d+)?(?:mg|㎎|밀리그램))?|캡슐|시럽|액|연고|크림|패치|패취|산)",
                compact,
                re.IGNORECASE,
            )
            and not re.search(r"(?:1일|하루|1회|식전|식후|취침|자기\s*전)", name_candidate)
        ):
            medication_names.append(name_candidate)
        generic_guidance = any(
            marker in line
            for marker in (
                "관계없이",
                "가능하지만",
                "충분한 물",
                "함께 복용",
                "복용하세요",
                "주의사항",
            )
        )
        if not contains_metadata and not generic_guidance and (
            re.search(r"(?:\d+\s*일|하루)\s*\d+\s*회", line)
            or (
                "1회" in line
                and any(unit in line for unit in ("정씩", "캡슐씩", "포씩", "ml"))
            )
            or any(marker in line for marker in ("식전", "식후", "취침", "자기 전", "자기전에"))
        ):
            method_value = re.sub(
                r"^(?:복용방법|복용법|용법|용량)\s*[:：]\s*",
                "",
                line,
                flags=re.IGNORECASE,
            ).strip(" .")
            if 0 < len(method_value) <= 120:
                medication_methods.append(method_value)
        period_label_match = re.match(
            r"^(?:복용기간|투약일수|처방일수)\s*[:：]\s*([^\n]+)$",
            line,
            re.IGNORECASE,
        )
        if period_label_match:
            period_value = _clean_text(period_label_match.group(1)).strip(" .")
            if len(period_value) <= 20:
                medication_periods.append(period_value)
        else:
            medication_periods.extend(
                match.group(0).replace(" ", "")
                for match in re.finditer(r"\d+\s*일간", line)
            )
    explicit_names = _table_row_values(text, ("처방 항목",))
    if explicit_names:
        medication_names = [
            re.sub(r"^어르\d{4,}\s+", "", value).strip()
            for value in explicit_names
        ]
    explicit_support = _table_row_values(text, ("복용 지원",))
    explicit_timing = _table_row_values(text, ("복용 시점",))
    if explicit_support or explicit_timing:
        medication_methods = [*explicit_support, *explicit_timing]
    names = _bounded_source_join(medication_names, limit=240, item_limit=80)
    methods = _bounded_source_join(medication_methods, limit=240, item_limit=120)
    periods = _bounded_source_join(medication_periods, limit=80, item_limit=20)
    # 기존 통합 항목은 호환용으로 유지하되, 원자 항목 전체를 다시 이어 붙여
    # 중복 표시하지 않는다. 양식 화면은 이름·복용법·기간을 각각 사용한다.
    information = names or methods or periods
    return {
        "medication_name": names,
        "medication_method": methods,
        "medication_period": periods,
        "medication_information": information,
    }


def _extract_rule_value(
    text: str,
    rule: FieldRule,
    *,
    document_kind: str | None = None,
) -> str | None:
    compact = _clean_text(text)

    if rule.key == "benefit_type" and document_kind == "individual_long_term_care_plan":
        table_match = re.search(
            r"수급자\s*희망급여[\s\S]{0,180}?보이는\s*값\s*[:：]\s*([^\n]+)",
            text,
            re.IGNORECASE,
        )
        if table_match:
            value = _clean_text(table_match.group(1)).split("항목명:", 1)[0].strip(" .")
            if value:
                return value[:500]

    if document_kind == "prescription" and rule.key in {
        "medication_information",
        "medication_name",
        "medication_method",
        "medication_period",
    }:
        return _prescription_facts(text)[rule.key]

    if document_kind is not None and rule.key == "subjective_need":
        subjective_values: list[str] = []
        for label, prefix in (("본인 욕구", "본인"), ("보호자 요청", "보호자")):
            values = _table_row_values(text, (label,))
            if values:
                subjective_values.append(f"{prefix}: {values[0]}")
        combined_subjective = _bounded_source_join(
            subjective_values,
            limit=360,
            item_limit=180,
        )
        if combined_subjective:
            return combined_subjective

    if rule.key == "benefit_effective_period":
        # 일반 항목 파서는 문장 마침표에서 값을 끊지만 날짜의 점(.)은
        # 마침표가 아니다. 적용기간은 문서 종류와 무관하게 두 날짜 전체를
        # 먼저 읽어 연도만 남는 회귀를 막는다.
        period_match = re.search(
            r"(?:^|\n)\s*(?:적용기간|유효기간|급여 적용기간)\s*[:：]\s*"
            r"(20\d{2}[.\-/]\d{1,2}[.\-/]\d{1,2}\s*[~～]\s*"
            r"20\d{2}[.\-/]\d{1,2}[.\-/]\d{1,2})",
            text,
            re.IGNORECASE,
        )
        if period_match:
            return _clean_text(period_match.group(1)).strip(" .")

    if document_kind == "individual_long_term_care_plan":
        if rule.key == "service_plan_goal":
            plan_goals = _bounded_source_join(
                _label_values(text, "장기요양목표"),
                limit=360,
                item_limit=160,
            )
            if plan_goals:
                return plan_goals
        if rule.key in {"rehabilitation_need", "hydration_support_goal"}:
            needed_contents = _label_values(text, "장기요양필요내용")
            markers = (
                ("훈련", "재활", "기능")
                if rule.key == "rehabilitation_need"
                else ("수분", "물 섭취", "음수")
            )
            matched_needs = _bounded_source_join(
                [value for value in needed_contents if any(marker in value for marker in markers)],
                limit=360,
                item_limit=160,
            )
            if matched_needs:
                return matched_needs

    if document_kind == "transfer_document":
        if rule.key in {"mobility_support_need", "daily_living_state"}:
            atomic = _transfer_atomic_fact(text, rule.key)
            if atomic:
                return atomic
            return _marker_clauses(
                text,
                markers=("거동", "보행", "이동", "목욕", "세면"),
                support_markers=("문제없", "혼자", "부축", "도움", "휠체어"),
            )
        if rule.key == "fall_history":
            return _marker_clauses(text, markers=("낙상",))
        if rule.key == "nutrition_state":
            atomic = _transfer_atomic_fact(text, rule.key)
            if atomic:
                return atomic
            return _marker_clauses(
                text,
                markers=("식사", "섭취"),
                support_markers=("혼자", "섭취", "도움"),
            )
        if rule.key == "resource_use":
            atomic = _transfer_atomic_fact(text, rule.key)
            if atomic:
                return atomic
            return _marker_clauses(
                text,
                markers=("주간보호", "요양시설", "의료기관"),
                support_markers=("이용", "입소", "방문", "진료"),
            )
        if rule.key == "skin_state":
            atomic = _transfer_atomic_fact(text, rule.key)
            if atomic:
                return atomic
            return _marker_clauses(
                text,
                markers=("피부", "욕창", "병변", "발적"),
            )

    for alias in rule.aliases:
        pattern = re.compile(
            rf"(?:^|\n|[.!?]\s*)\s*(?:어르\d{{4}}\s+)?"
            rf"{re.escape(alias)}\s*[:：]\s*([^\.\n]+)",
            re.IGNORECASE,
        )
        match = pattern.search(text)
        if match:
            value = _clean_text(match.group(1)).strip(" .")
            if (
                rule.key == "skin_state"
                and "손상" in value
                and any(
                    marker in value
                    for marker in ("관찰되지 않음", "관찰되지 않았", "없음", "없었")
                )
            ):
                if "발적" in value:
                    return "피부 손상과 발적이 관찰되지 않음"
                return "피부 손상 관찰되지 않음"
            # 긴 문단은 양식 항목 값이 아니라 근거 원장에만 보존한다.
            # 별도 문서별 파서가 원자 사실을 만들지 못했다면 근거 부족으로 남긴다.
            if value and len(value) <= 180:
                return value

    # 표 기반 제출 PDF는 글자층이나 로컬 OCR 결과가 `항목명: 값`이 아니라
    # `항목명 값` 한 줄로 나올 수 있다. 확인된 항목 사전에 있는 정확한
    # 라벨로 시작하는 짧은 행만 읽고, 자유 문단이나 다음 행은 추정하지 않는다.
    if document_kind is not None:
        aliases = tuple(
            dict.fromkeys((*rule.aliases, *_TABLE_EXTRA_ALIASES.get(rule.key, ())))
        )
        if rule.key == "cognitive_observation":
            aliases = tuple(alias for alias in aliases if alias != "의사소통")
        values = _table_row_values(text, aliases)
        if values:
            return values[0]

    if rule.key == "mobility_support_need":
        if "독립보행" in compact:
            return "실내 독립보행"
        if "부축" in compact and any(word in compact for word in ("보행", "이동")):
            return "보행 시 부축 필요"
        if any(word in compact for word in ("보행", "이동")) and re.search(
            r"(?:한\s*명(?:이)?|직원(?:이)?)\s*[^.!?\n]{0,24}"
            r"(?:팔(?:을)?\s*잡|손(?:을)?\s*받쳐)",
            compact,
        ):
            return "보행 시 한 명 부축 필요"
    if rule.key == "nutrition_state":
        meal_facts = [
            _clean_text(match.group(0)).strip(" .")
            for match in re.finditer(
                r"(?:아침|점심|저녁)?\s*"
                r"(?:일반식|죽식|연식)?\s*"
                r"(?:식사량(?:이)?|섭취량(?:이)?)?\s*"
                r"\d+\s*/\s*\d+\s*(?:이상|이하|미만|정도|가량)?\s*"
                r"(?:을|를)?\s*섭취",
                compact,
            )
        ]
        meal_value = _bounded_source_join(meal_facts, limit=180, item_limit=80)
        if meal_value:
            return meal_value
    if rule.key == "skin_state":
        if (
            "피부" in compact
            and any(marker in compact for marker in ("손상", "발적"))
            and any(
                marker in compact
                for marker in ("관찰되지 않음", "관찰되지 않았", "없음", "없었")
            )
        ):
            if "발적" in compact:
                return "피부 손상과 발적이 관찰되지 않음"
            return "피부 손상 관찰되지 않음"
    if rule.key == "cognitive_observation":
        if (
            "인지 상태" in compact
            and "간단한 지시" in compact
            and "이해" in compact
        ):
            return "일상 대화와 간단한 지시 이해 가능"
    return None


def _extract_document_date(text: str) -> date | None:
    """자료에 명시된 작성 기준일만 읽고, 업로드일 등은 추정하지 않는다."""
    labels = (
        "자료 작성일",
        "작성일",
        "평가일",
        "기준일",
        "상담 일시",
        "상담일시",
        "전원일",
        "퇴원일",
        "진료일",
    )
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(
        rf"(?:^|\n)\s*(?:{label_pattern})\s*[:：]\s*"
        r"(20\d{2})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})(?:일)?",
        text,
        re.IGNORECASE,
    )
    if match is None:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def build_assessment_evidence_entries(
    materials: list[ParsedAssessmentMaterial],
    bundle: NewAdmissionDraftBundle,
) -> list[dict[str, Any]]:
    """파일별 원문, 정규화 사실, 충돌 및 양식 연결을 보존한다."""
    bundle_fields = {
        field.field_key: field.model_dump(mode="json") for field in bundle.fields
    }
    entries: list[dict[str, Any]] = []
    for material in materials:
        source = _source_contract(material)
        normalized_facts: list[dict[str, Any]] = []
        linked_fields: list[dict[str, Any]] = []
        conflict_groups: list[str] = []
        if material.document_kind not in NON_FACT_DOCUMENT_KINDS:
            for rule in FIELD_RULES:
                value = _extract_rule_value(
                    material.extracted_text,
                    rule,
                    document_kind=material.document_kind,
                )
                if value is None:
                    continue
                field = bundle_fields[rule.key]
                normalized_facts.append(
                    {
                        "field_key": rule.key,
                        "label": rule.label,
                        "value": value,
                        "status": field["status"],
                        "document_types": list(rule.document_types),
                    }
                )
                linked_fields.append(
                    {
                        "field_key": rule.key,
                        "document_types": list(rule.document_types),
                    }
                )
                if field["status"] == "material_conflict":
                    conflict_groups.append(rule.key)
        normalized_facts.extend(_source_context_facts(material))
        conflict_groups = list(dict.fromkeys(conflict_groups))
        staff_review_required = bool(conflict_groups) or (
            source["verification_state"] != "verified"
        ) or _requires_current_reconfirmation(material.extracted_text)
        entries.append(
            {
                "source_ref": material.source_ref,
                "document_kind": source["document_kind"],
                "document_date": _extract_document_date(material.extracted_text),
                "source_type": source["source_type"],
                "organization_role": source["organization_role"],
                "reference_locator": source["reference_locator"],
                "raw_extracted_text": material.extracted_text,
                "normalized_facts": normalized_facts,
                "verification_state": (
                    "material_conflict"
                    if conflict_groups
                    else source["verification_state"]
                ),
                "conflict_groups": conflict_groups,
                "staff_review_required": staff_review_required,
                "linked_fields": linked_fields,
                "external_transfer_allowed": False,
            }
        )
    return entries


def build_assessment_evidence_summary(
    bundle: NewAdmissionDraftBundle,
    *,
    available_source_refs: set[str],
) -> dict[str, Any]:
    """저장된 항목을 확인 사실·충돌·근거 부족으로만 분리한다.

    이 함수는 값을 추론하거나 충돌값 중 하나를 선택하지 않는다. 최신 수정본에
    저장된 항목 상태와 근거 식별번호만 읽어 화면용 공통 결과를 만든다.
    """

    confirmed_facts: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    missing_items: list[dict[str, Any]] = []
    resolved_statuses = {"confirmed", "reusable", "derivable"}

    for field in bundle.fields:
        evidence_refs = [
            source_ref
            for source_ref in field.evidence_refs
            if source_ref in available_source_refs
        ]
        common = {
            "field_key": field.field_key,
            "label": field.label,
            "document_types": list(field.document_types),
            "evidence_refs": evidence_refs,
        }
        if field.status in resolved_statuses and field.value is not None and evidence_refs:
            confirmed_facts.append(
                {
                    **common,
                    "value": field.value,
                    "status": field.status,
                }
            )
            continue
        if field.status == "material_conflict":
            conflicts.append(
                {
                    **common,
                    "conflict_values": list(field.conflict_values),
                    "selected_value": None,
                }
            )
            continue

        reason = (
            "staff_decision_required"
            if field.status == "user_decision_required"
            else "current_observation_required"
            if field.status == "current_observation_required"
            else "evidence_missing"
        )
        missing_items.append(
            {
                **common,
                "value": None,
                "reason": reason,
            }
        )

    return {
        "status": "needs_confirmation" if conflicts or missing_items else "draft",
        "counts": {
            "confirmed_facts": len(confirmed_facts),
            "conflicts": len(conflicts),
            "missing_items": len(missing_items),
        },
        "confirmed_facts": confirmed_facts,
        "conflicts": conflicts,
        "missing_items": missing_items,
        "external_transfer_allowed": False,
    }


def _field_contract(
    rule: FieldRule,
    *,
    values: list[tuple[str, str]],
) -> dict[str, Any]:
    unique_values = list(dict.fromkeys(value for _, value in values))
    # 긴 재평가 기간의 원문 근거는 bundle.evidence_sources에 모두 보존한다.
    # 항목 계약에는 서로 다른 값마다 첫 근거를 먼저 포함하고 나머지를
    # 시간순으로 채워 상한(30건)을 지키면서 충돌 양쪽 근거를 유지한다.
    representative_refs: list[str] = []
    for unique_value in unique_values:
        representative_ref = next(
            source_ref for source_ref, value in values if value == unique_value
        )
        if representative_ref not in representative_refs:
            representative_refs.append(representative_ref)
    all_refs = list(dict.fromkeys(source_ref for source_ref, _ in values))
    evidence_refs = (
        representative_refs
        + [source_ref for source_ref in all_refs if source_ref not in representative_refs]
    )[:30]
    if len(unique_values) > 1:
        status = "material_conflict"
        value: str | None = None
        conflict_values = unique_values[:10]
    elif unique_values:
        status = "reusable"
        value = unique_values[0]
        conflict_values = []
    else:
        if rule.value_kind == "administrative":
            status = "admin_missing"
        elif rule.value_kind == "signature":
            status = "user_decision_required"
        else:
            # 값이 없다는 이유만으로 건강·기능·관찰·평가 항목을
            # 행정정보로 분류하지 않는다. 이 항목들은 직원이 현재 상태나
            # 공식 평가 결과를 확인해야 하므로 관찰 필요로 남긴다.
            status = "current_observation_required"
        value = None
        conflict_values = []
    return {
        "field_key": rule.key,
        "label": rule.label,
        "document_types": list(rule.document_types),
        "status": status,
        "value_kind": rule.value_kind,
        "value": value,
        "evidence_refs": evidence_refs,
        "conflict_values": conflict_values,
    }


def _baseline_field_contract(
    rule: FieldRule,
    *,
    values: list[tuple[str, str]],
) -> dict[str, Any] | None:
    if not values:
        return None
    unique_values = list(dict.fromkeys(value for _, value in values))
    representative_refs = [
        next(source_ref for source_ref, value in values if value == unique_value)
        for unique_value in unique_values
    ]
    all_refs = list(dict.fromkeys(source_ref for source_ref, _value in values))
    evidence_refs = (
        representative_refs
        + [source_ref for source_ref in all_refs if source_ref not in representative_refs]
    )[:30]
    if len(unique_values) > 1:
        status = "material_conflict"
        value: str | None = None
        conflict_values = unique_values[:10]
    else:
        status = "baseline_reference"
        value = unique_values[0]
        conflict_values = []
    return {
        "field_key": rule.key,
        "label": rule.label,
        "value_kind": rule.value_kind,
        "status": status,
        "value": value,
        "evidence_refs": evidence_refs,
        "conflict_values": conflict_values,
    }


def _comparison_contracts(
    *,
    baseline_fields: list[dict[str, Any]],
    current_fields: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    baseline_by_key = {item["field_key"]: item for item in baseline_fields}
    current_by_key = {item["field_key"]: item for item in current_fields}
    comparisons: list[dict[str, Any]] = []
    for rule in FIELD_RULES:
        baseline = baseline_by_key.get(rule.key)
        current = current_by_key[rule.key]
        has_current_value = current["value"] not in (None, "")
        has_current_conflict = current["status"] == "material_conflict"
        if baseline is None and not has_current_value and not has_current_conflict:
            continue

        previous_value = baseline["value"] if baseline else None
        current_value = current["value"] if has_current_value else None
        baseline_refs = list(baseline["evidence_refs"]) if baseline else []
        current_refs = list(current["evidence_refs"])
        conflict_values: list[str] = []

        if baseline and baseline["status"] == "material_conflict":
            classification = "material_conflict"
            previous_value = None
            current_value = None
            conflict_values = list(baseline["conflict_values"])
            reason = "이전 기준자료끼리 값이 달라 어느 값도 현재 사실로 선택하지 않습니다."
        elif has_current_conflict:
            classification = "material_conflict"
            current_value = None
            conflict_values = list(current["conflict_values"])
            reason = "현재 확인 자료끼리 값이 달라 어느 값도 자동 선택하지 않습니다."
        elif baseline is None:
            classification = "newly_confirmed"
            reason = "이전 기준자료에는 없고 이번 기간 자료에서 새로 확인된 내용입니다."
        elif not has_current_value:
            classification = "stale_current_observation_required"
            current_value = None
            reason = "이전 값은 비교 기준으로만 보존하고 현재 확인 기록이 없어 새 초안에 복사하지 않습니다."
        elif previous_value == current_value:
            classification = "unchanged"
            reason = "이전 값과 이번 기간 자료에서 확인된 값이 같습니다."
        else:
            classification = "changed"
            reason = "이전 값과 이번 기간 자료에서 확인된 값이 다릅니다."

        comparisons.append(
            {
                "field_key": rule.key,
                "label": rule.label,
                "classification": classification,
                "previous_value": previous_value,
                "current_value": current_value,
                "baseline_evidence_refs": baseline_refs,
                "current_evidence_refs": current_refs,
                "conflict_values": conflict_values,
                "reason": reason,
            }
        )
    return comparisons


def _draft_contracts(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    drafts: list[dict[str, Any]] = []
    for document_type in NEW_ADMISSION_DOCUMENT_TYPES:
        document_fields = [
            field for field in fields if document_type in field["document_types"]
        ]
        missing = [
            field["field_key"]
            for field in document_fields
            if field["status"] == "admin_missing"
        ]
        human = [
            field["field_key"]
            for field in document_fields
            if field["status"]
            in {
                "current_observation_required",
                "user_decision_required",
                "material_conflict",
            }
        ]
        drafts.append(
            {
                "document_type": document_type,
                "status": "needs_confirmation" if missing or human else "draft",
                "field_keys": [field["field_key"] for field in document_fields],
                "missing_fields": missing,
                "human_verification_fields": human,
            }
        )
    return drafts


def _saved_contract_value_pairs(item: Any) -> list[tuple[str, str]]:
    evidence_refs = list(item.evidence_refs)
    if item.status == "material_conflict":
        values = [str(value) for value in item.conflict_values]
        pairs = list(zip(evidence_refs, values, strict=False))
        if values:
            pairs.extend(
                (source_ref, values[0])
                for source_ref in evidence_refs[len(pairs) :]
            )
        return pairs
    if item.value in (None, ""):
        return []
    return [(source_ref, str(item.value)) for source_ref in evidence_refs]


def build_assessment_draft_bundle(
    materials: list[ParsedAssessmentMaterial],
    *,
    case_ref: str,
    reason: str,
    assessment_date: str,
    period_start: str | None,
    period_end: str | None,
    linked_evidence: list[dict[str, str]] | None = None,
) -> NewAdmissionDraftBundle:
    evidence_sources = [_source_contract(material) for material in materials]
    # 신규입소 계약은 근거를 최대 100건까지 보존한다. 장기간 재평가에서
    # 대화가 더 많더라도 제출 문서 근거를 먼저 보존하고, 남는 범위 안에서만
    # 시간순 대화 근거를 연결한다.
    linked_evidence = (linked_evidence or [])[: max(0, 100 - len(materials))]
    current_values_by_key: dict[str, list[tuple[str, str]]] = {
        rule.key: [] for rule in FIELD_RULES
    }
    baseline_values_by_key: dict[str, list[tuple[str, str]]] = {
        rule.key: [] for rule in FIELD_RULES
    }
    current_reconfirmation_refs = {
        material.source_ref
        for material in materials
        if material.document_kind in {"transfer_document", "consultation_log"}
        and _requires_current_reconfirmation(material.extracted_text)
    }
    for material in materials:
        if material.document_kind in NON_FACT_DOCUMENT_KINDS:
            continue
        target_values = (
            baseline_values_by_key
            if reason != "new_admission"
            and material.document_kind in REASSESSMENT_BASELINE_DOCUMENT_KINDS
            else current_values_by_key
        )
        for rule in FIELD_RULES:
            value = _extract_rule_value(
                material.extracted_text,
                rule,
                document_kind=material.document_kind,
            )
            if value:
                target_values[rule.key].append((material.source_ref, value))

    for index, evidence in enumerate(linked_evidence, start=1):
        source_ref = evidence.get("source_ref") or f"linked-{index:03d}"
        evidence_sources.append(
            {
                "source_ref": source_ref,
                "source_type": "current_observation",
                "document_kind": "선택 기간 매실챗 확인 기록",
                "organization_role": "authoring_organization",
                "reference_locator": evidence.get("reference_locator", "매실챗 기록"),
                "evidence_summary": evidence.get("summary", "현재 확인 기록")[:1000],
                "verification_state": "verified",
                "contains_sensitive_data": False,
            }
        )
        text = evidence.get("text", "")
        for rule in FIELD_RULES:
            value = _extract_rule_value(text, rule)
            if value:
                current_values_by_key[rule.key].append((source_ref, value))

    fields = [
        _field_contract(rule, values=current_values_by_key[rule.key])
        for rule in FIELD_RULES
    ]
    baseline_fields = [
        item
        for rule in FIELD_RULES
        if (
            item := _baseline_field_contract(
                rule,
                values=baseline_values_by_key[rule.key],
            )
        )
        is not None
    ]
    baseline_keys = {item["field_key"] for item in baseline_fields}
    for field in fields:
        if field["field_key"] in baseline_keys and field["status"] == "admin_missing":
            field["status"] = "current_observation_required"
        if (
            field["status"] != "material_conflict"
            and field["value"] is not None
            and field["evidence_refs"]
            and set(field["evidence_refs"]).issubset(current_reconfirmation_refs)
        ):
            # The candidate remains in the append-only evidence ledger, but a
            # reference document that explicitly requires a current check must
            # not become the resident's current form value by itself.
            field["status"] = "current_observation_required"
            field["value"] = None
            field["conflict_values"] = []
    comparison_items = _comparison_contracts(
        baseline_fields=baseline_fields,
        current_fields=fields,
    )
    drafts = _draft_contracts(fields)

    material_receipts = [
        {
            "source_ref": material.source_ref,
            "display_label": _material_display_label(
                material,
                fallback_index=index,
            ),
            "document_kind": DOCUMENT_KIND_LABELS[material.document_kind],
            "status": (
                "staff_review_required"
                if material.document_kind in NON_FACT_DOCUMENT_KINDS
                else "submitted"
            ),
            "mime_type": material.mime_type,
            "size_bytes": material.size_bytes,
            "page_count": material.page_count,
        }
        for index, material in enumerate(materials, start=1)
    ]
    # 채팅 근거는 항목별 evidence_sources에 모두 보존하되, 화면의 자료 접수표에는
    # 하나의 묶음으로 표시한다. 장기 재평가에서 수십 건의 채팅이 연결되어도
    # 접수표 상한(30건)을 넘거나 같은 문구가 길게 반복되지 않게 하기 위함이다.
    if linked_evidence:
        receipt_source_ref = linked_evidence[0].get("source_ref") or "linked-001"
        material_receipts.append(
            {
                "source_ref": receipt_source_ref,
                "display_label": f"선택 기간 매실챗 확인 기록 {len(linked_evidence)}건",
                "document_kind": "선택 기간 매실챗 확인 기록",
                "status": "linked_existing_data",
                "mime_type": "application/pdf",
                "size_bytes": 1,
                "page_count": 1,
            }
        )
    bundle = NewAdmissionDraftBundle.model_validate(
        {
            "case_ref": case_ref,
            "processing_scope": "deidentified_dev",
            "external_transfer_allowed": False,
            "revision": 1,
            "reason": reason,
            "assessment_date": assessment_date,
            "period_start": period_start,
            "period_end": period_end,
            "material_receipts": material_receipts,
            "evidence_sources": evidence_sources,
            "fields": fields,
            "baseline_fields": baseline_fields,
            "comparison_items": comparison_items,
            "drafts": drafts,
        }
    )
    workspace = build_new_admission_form_workspace(bundle.model_dump(mode="json"))
    payload = bundle.model_dump(mode="json")
    payload["document_texts"] = {
        item["document_type"]: item["draft_text"]
        for item in workspace["documents"]
    }
    return NewAdmissionDraftBundle.model_validate(payload)


def merge_assessment_materials_into_bundle(
    current: NewAdmissionDraftBundle,
    materials: list[ParsedAssessmentMaterial],
) -> NewAdmissionDraftBundle:
    """Add locally parsed material as an append-only draft revision.

    The previous revision remains unchanged.  New evidence can fill a blank or
    reinforce the same value, but a different value becomes a material conflict
    instead of replacing the staff's previous value.  Editable document text is
    regenerated for the new revision; the prior staff-edited text remains in the
    superseded revision and therefore stays recoverable.
    """

    if not materials:
        raise ValueError("추가할 자료가 없습니다.")
    delta = build_assessment_draft_bundle(
        materials,
        case_ref=current.case_ref,
        reason=current.reason,
        assessment_date=current.assessment_date,
        period_start=current.period_start,
        period_end=current.period_end,
    )
    evidence_sources = [
        *[item.model_dump(mode="json") for item in current.evidence_sources],
        *[item.model_dump(mode="json") for item in delta.evidence_sources],
    ]
    receipts = [
        *[item.model_dump(mode="json") for item in current.material_receipts],
        *[item.model_dump(mode="json") for item in delta.material_receipts],
    ]
    if len(evidence_sources) > 100 or len(receipts) > 30:
        raise ValueError(
            "이 초안에 연결할 수 있는 자료 수를 넘었습니다. 새 평가 초안으로 나누어 주세요."
        )

    current_fields = {item.field_key: item for item in current.fields}
    delta_fields = {item.field_key: item for item in delta.fields}
    merged_fields: list[dict[str, Any]] = []
    for rule in FIELD_RULES:
        previous = current_fields[rule.key]
        incoming = delta_fields[rule.key]
        previous_values = (
            list(previous.conflict_values)
            if previous.status == "material_conflict"
            else ([previous.value] if previous.value else [])
        )
        incoming_values = (
            list(incoming.conflict_values)
            if incoming.status == "material_conflict"
            else ([incoming.value] if incoming.value else [])
        )
        values = list(dict.fromkeys([*previous_values, *incoming_values]))
        evidence_refs = list(
            dict.fromkeys([*previous.evidence_refs, *incoming.evidence_refs])
        )[:30]
        if len(values) > 1:
            status = "material_conflict"
            value = None
            conflict_values = values[:10]
        elif values:
            value = values[0]
            conflict_values = []
            status = (
                previous.status
                if previous.value == value
                and previous.status in {"confirmed", "reusable", "derivable"}
                else incoming.status
                if incoming.value == value
                else "reusable"
            )
        else:
            value = None
            conflict_values = []
            status = (
                incoming.status
                if incoming.status == "current_observation_required"
                and incoming.evidence_refs
                else previous.status
            )
        merged_fields.append(
            {
                "field_key": rule.key,
                "label": rule.label,
                "document_types": list(rule.document_types),
                "status": status,
                "value_kind": rule.value_kind,
                "value": value,
                "evidence_refs": evidence_refs,
                "conflict_values": conflict_values,
            }
        )

    current_baseline_fields = {
        item.field_key: item for item in current.baseline_fields
    }
    delta_baseline_fields = {item.field_key: item for item in delta.baseline_fields}
    merged_baseline_fields: list[dict[str, Any]] = []
    for rule in FIELD_RULES:
        baseline_values = [
            *(
                _saved_contract_value_pairs(current_baseline_fields[rule.key])
                if rule.key in current_baseline_fields
                else []
            ),
            *(
                _saved_contract_value_pairs(delta_baseline_fields[rule.key])
                if rule.key in delta_baseline_fields
                else []
            ),
        ]
        baseline = _baseline_field_contract(rule, values=baseline_values)
        if baseline is not None:
            merged_baseline_fields.append(baseline)

    baseline_keys = {item["field_key"] for item in merged_baseline_fields}
    for field in merged_fields:
        if field["field_key"] in baseline_keys and field["status"] == "admin_missing":
            field["status"] = "current_observation_required"

    comparison_items = _comparison_contracts(
        baseline_fields=merged_baseline_fields,
        current_fields=merged_fields,
    )
    drafts = _draft_contracts(merged_fields)

    payload = {
        "case_ref": current.case_ref,
        "processing_scope": current.processing_scope,
        "external_transfer_allowed": False,
        "revision": current.revision + 1,
        "supersedes_revision": current.revision,
        "reason": current.reason,
        "assessment_date": current.assessment_date,
        "period_start": current.period_start,
        "period_end": current.period_end,
        "material_receipts": receipts,
        "evidence_sources": evidence_sources,
        "fields": merged_fields,
        "baseline_fields": merged_baseline_fields,
        "comparison_items": comparison_items,
        "drafts": drafts,
    }
    merged = NewAdmissionDraftBundle.model_validate(payload)
    workspace = build_new_admission_form_workspace(merged.model_dump(mode="json"))
    payload["document_texts"] = {
        item["document_type"]: item["draft_text"]
        for item in workspace["documents"]
    }
    return NewAdmissionDraftBundle.model_validate(payload)


def protected_material_directory(upload_root: str, case_ref: str) -> Path:
    root = (Path(upload_root).resolve() / "assessment_materials").resolve()
    target = (root / case_ref).resolve()
    if target.parent != root:
        raise ValueError("자료 저장 위치가 안전하지 않습니다.")
    return target
