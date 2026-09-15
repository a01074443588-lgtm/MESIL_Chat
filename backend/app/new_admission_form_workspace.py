from __future__ import annotations

from typing import Any

from .new_admission_contract import (
    ASSESSMENT_DOCUMENT_TYPES,
    NewAdmissionDraftBundle,
)


_DOCUMENT_ORDER = (
    "fall_risk_assessment",
    "pressure_ulcer_risk_assessment",
    "cognitive_function_assessment",
    "needs_assessment",
    "long_term_care_service_plan",
)

_CARE_PLAN_COLUMNS = (
    "장기요양 필요영역",
    "장기요양 세부목표",
    "장기요양 필요내용",
    "세부 제공내용",
    "제공방법",
    "횟수",
    "시간(분)",
    "작성자",
)

_CARE_PLAN_BASIS_ROWS: dict[str, tuple[str, ...]] = {
    "dressing": ("needs_assessment.daily_living",),
    "face_washing": ("needs_assessment.daily_living",),
    "oral_hygiene": ("needs_assessment.oral_state",),
    "bathing": ("needs_assessment.daily_living",),
    "hair_washing": ("needs_assessment.daily_living",),
    "grooming": ("needs_assessment.daily_living",),
    "nail_care": ("needs_assessment.daily_living",),
    "appearance_care": ("needs_assessment.daily_living",),
    "hair_grooming": ("needs_assessment.daily_living",),
    "regular_repositioning": (
        "pressure_ulcer_risk_assessment.mobility",
        "needs_assessment.skin",
    ),
    "repositioning_guidance": (
        "pressure_ulcer_risk_assessment.mobility",
        "needs_assessment.mobility",
    ),
    "sitting_posture": (
        "fall_risk_assessment.gait_balance",
        "needs_assessment.mobility",
    ),
    "wheelchair_mobility": (
        "fall_risk_assessment.activity",
        "fall_risk_assessment.gait_balance",
        "needs_assessment.mobility",
    ),
    "fall_prevention": (
        "fall_risk_assessment.activity",
        "fall_risk_assessment.gait_balance",
        "fall_risk_assessment.fall_history",
        "needs_assessment.mobility",
    ),
    "diaper_change": ("needs_assessment.elimination",),
    "bowel_management": (
        "fall_risk_assessment.elimination",
        "needs_assessment.elimination",
    ),
    "urinary_management": (
        "fall_risk_assessment.elimination",
        "needs_assessment.elimination",
    ),
    "medication_assistance": (
        "fall_risk_assessment.medication",
        "needs_assessment.medication",
    ),
    "communication_support": (
        "cognitive_function_assessment.note",
        "needs_assessment.communication",
    ),
    "needs_observation": (
        "needs_assessment.cognition",
        "needs_assessment.needs",
    ),
    "medication_management": (
        "fall_risk_assessment.medication",
        "needs_assessment.medication",
    ),
    "contracture_prevention": (
        "needs_assessment.mobility",
        "needs_assessment.rehabilitation",
    ),
    "health_observation": (
        "needs_assessment.medical_history",
        "needs_assessment.nutrition",
        "needs_assessment.skin",
    ),
    "health_education": (
        "needs_assessment.medical_history",
        "needs_assessment.medication",
    ),
    "medical_visit_support": ("needs_assessment.medical_history",),
    "dementia_care": (
        "cognitive_function_assessment.note",
        "needs_assessment.cognition",
    ),
    "medical_referral": ("needs_assessment.medical_history",),
    "bowel_nursing": ("needs_assessment.elimination",),
    "oral_nursing": ("needs_assessment.oral_state",),
    "physical_cognitive_program": (
        "needs_assessment.cognition",
        "needs_assessment.rehabilitation",
        "needs_assessment.resources",
    ),
    "physical_function_training": (
        "needs_assessment.mobility",
        "needs_assessment.rehabilitation",
    ),
    "physical_function_detail": (
        "needs_assessment.mobility",
        "needs_assessment.rehabilitation",
    ),
    "basic_movement_training": (
        "needs_assessment.mobility",
        "needs_assessment.daily_living",
    ),
    "basic_movement_detail": (
        "needs_assessment.mobility",
        "needs_assessment.daily_living",
    ),
    "adl_training": ("needs_assessment.daily_living",),
    "adl_training_detail": ("needs_assessment.daily_living",),
    "cognitive_program": (
        "cognitive_function_assessment.note",
        "needs_assessment.cognition",
        "needs_assessment.rehabilitation",
    ),
    "leisure_emotional_program": (
        "needs_assessment.cognition",
        "needs_assessment.needs",
        "needs_assessment.resources",
    ),
    "social_adaptation_training": (
        "needs_assessment.communication",
        "needs_assessment.resources",
    ),
    "family_program": (
        "needs_assessment.family",
        "needs_assessment.support_network",
        "needs_assessment.needs",
    ),
    "occupational_therapy": ("needs_assessment.rehabilitation",),
    "emergency_service": (
        "fall_risk_assessment.fall_history",
        "needs_assessment.medical_history",
    ),
    "living_environment": ("needs_assessment.living_environment",),
}

_DOCUMENT_LABELS = {
    "fall_risk_assessment": "낙상위험도",
    "pressure_ulcer_risk_assessment": "욕창위험도",
    "cognitive_function_assessment": "인지기능검사",
    "needs_assessment": "욕구사정",
    "long_term_care_service_plan": "장기요양급여 제공계획서",
}

_RESTRICTED_VALUE_KINDS = {
    "assessment_score",
    "diagnosis",
    "service_frequency",
    "service_duration",
    "signature",
}


_FORM_SECTION_CONTRACTS: dict[str, tuple[dict[str, Any], ...]] = {
    "fall_risk_assessment": (
        {
            "section_key": "basic",
            "title": "평가 기본정보",
            "rows": (
                ("care_grade", "장기요양등급", ("long_term_care_grade",), "administrative"),
                ("assessment_date", "평가 기준일", (), "administrative"),
                ("assessment_reason", "평가 사유", (), "administrative"),
                ("examiner", "평가자", (), "administrative"),
            ),
        },
        {
            "section_key": "risk_items",
            "title": "낙상 위험 평가 항목",
            "rows": (
                ("age", "연령", ("resident_age",), "fact"),
                ("mental_state", "정신상태", ("cognitive_observation",), "fact"),
                ("elimination", "배설상태", ("elimination_state",), "fact"),
                ("fall_history", "낙상경험", ("fall_history", "recent_fall_count"), "fact"),
                ("activity", "활동수준", ("mobility_support_need",), "fact"),
                ("gait_balance", "보행·균형", ("mobility_support_need",), "fact"),
                ("medication", "최근 투약", ("medication_name", "medication_method", "medication_period", "medication_information"), "fact"),
            ),
        },
        {
            "section_key": "result",
            "title": "점수·판정·확인",
            "rows": (
                ("score", "총점", ("fall_score",), "assessment_score"),
                ("judgment", "위험도 판정", ("fall_risk_judgment",), "assessment_score"),
                ("note", "특이사항·판단근거", ("fall_history", "mobility_support_need"), "fact"),
                ("signature", "작성자 확인·서명", ("staff_signature",), "signature"),
            ),
        },
    ),
    "pressure_ulcer_risk_assessment": (
        {
            "section_key": "basic",
            "title": "평가 기본정보",
            "rows": (
                ("care_grade", "장기요양등급", ("long_term_care_grade",), "administrative"),
                ("assessment_date", "평가 기준일", (), "administrative"),
                ("assessment_reason", "평가 사유", (), "administrative"),
                ("examiner", "평가자", (), "administrative"),
            ),
        },
        {
            "section_key": "risk_items",
            "title": "욕창 위험 평가 항목",
            "rows": (
                ("sensory", "감각인지", ("cognitive_observation",), "fact"),
                ("moisture", "습기", ("skin_moisture_current",), "fact"),
                ("activity", "활동", ("mobility_support_need",), "fact"),
                ("mobility", "이동", ("mobility_support_need",), "fact"),
                ("nutrition", "영양", ("nutrition_state",), "fact"),
                ("friction_shear", "마찰·전단력", (), "fact"),
            ),
        },
        {
            "section_key": "result",
            "title": "점수·판정·확인",
            "rows": (
                ("score", "총점", ("pressure_ulcer_score",), "assessment_score"),
                ("judgment", "위험도 판정", ("pressure_ulcer_judgment",), "assessment_score"),
                ("note", "특이사항·판단근거", ("skin_state",), "fact"),
                ("signature", "작성자 확인·서명", ("staff_signature",), "signature"),
            ),
        },
    ),
    "cognitive_function_assessment": (
        {
            "section_key": "basic",
            "title": "검사 기본정보",
            "rows": (
                ("care_grade", "장기요양등급", ("long_term_care_grade",), "administrative"),
                ("assessment_date", "검사 기준일", (), "administrative"),
                ("assessment_place", "검사 장소", (), "administrative"),
                ("assessment_reason", "검사 사유", ("cognitive_assessment_decision",), "fact"),
                ("examiner", "검사자", (), "administrative"),
            ),
        },
        {
            "section_key": "domain_results",
            "title": "인지 영역별 결과",
            "notice": "검사 문항은 복제하지 않고 직원이 실시·확인한 결과만 기록합니다.",
            "rows": (
                ("registration_score", "기억등록 점수", (), "assessment_score"),
                ("time_orientation_score", "시간 지남력 점수", ("cognitive_orientation_result",), "assessment_score"),
                ("place_orientation_score", "장소 지남력 점수", ("cognitive_orientation_result",), "assessment_score"),
                ("recall_score", "기억회상 점수", ("cognitive_memory_result",), "assessment_score"),
                ("attention_calculation_score", "주의집중·계산 점수", ("cognitive_attention_result",), "assessment_score"),
                ("language_score", "언어기능 점수", ("cognitive_language_result",), "assessment_score"),
                ("drawing_score", "그리기 점수", (), "assessment_score"),
            ),
        },
        {
            "section_key": "result",
            "title": "검사 결과·판정·확인",
            "rows": (
                ("total_score", "총점", ("cognitive_score",), "assessment_score"),
                ("judgment", "검사 판정", ("cognitive_judgment",), "diagnosis"),
                ("note", "관찰·판단근거", ("cognitive_observation",), "fact"),
            ),
        },
    ),
    "needs_assessment": (
        {
            "section_key": "basic",
            "title": "기본정보",
            "rows": (
                ("care_grade", "장기요양등급", ("long_term_care_grade",), "administrative"),
                ("assessment_date", "평가 기준일", (), "administrative"),
                ("assessment_reason", "평가 사유", (), "administrative"),
                ("examiner", "평가자", (), "administrative"),
            ),
        },
        {"section_key": "nutrition", "title": "1. 영양상태", "rows": (("nutrition", "식사·영양 상태", ("nutrition_state",), "fact"), ("hydration", "수분 섭취 상태", ("hydration_support_goal",), "fact"), ("elimination", "배변·배뇨 상태", ("elimination_state",), "fact"))},
        {"section_key": "oral", "title": "2. 구강상태", "rows": (("oral_state", "구강·치아·의치 상태", ("oral_state",), "fact"),)},
        {"section_key": "disease", "title": "3. 질병상태", "rows": (("diagnosis", "진단·질환 관련 확인", ("medical_diagnosis", "hypertension_diagnosis"), "diagnosis"), ("medical_history", "과거력·현재 치료", ("medical_history",), "fact"), ("medication", "투약 관련 확인", ("medication_name", "medication_method", "medication_period", "medication_information"), "fact"))},
        {"section_key": "physical_adl", "title": "4. 신체상태(일상생활 동작 수행능력)", "rows": (("mobility", "이동·보행", ("mobility_support_need",), "fact"), ("daily_living", "일상생활 동작 수행", ("daily_living_state",), "fact"), ("skin", "피부 상태", ("skin_state",), "fact"))},
        {"section_key": "cognition", "title": "5. 인지상태", "rows": (("cognition", "인지·행동 상태", ("cognitive_observation",), "fact"),)},
        {"section_key": "communication", "title": "6. 의사소통", "rows": (("communication", "의사소통 상태", ("communication_state", "cognitive_observation"), "fact"),)},
        {"section_key": "family_environment", "title": "7. 가족 및 환경상태", "rows": (("family", "가족·보호자 관계", ("family_environment",), "fact"), ("living_environment", "생활환경", ("admission_room",), "administrative"), ("support_network", "수발·지지체계", ("support_network",), "fact"))},
        {"section_key": "resources", "title": "8. 자원이용 욕구", "rows": (("resources", "의료·복지·지역사회 자원", ("resource_use",), "fact"),)},
        {"section_key": "rehabilitation", "title": "9. 재활상태", "rows": (("rehabilitation", "재활·기능 유지 필요", ("rehabilitation_need", "mobility_support_need"), "fact"),)},
        {"section_key": "subjective_needs", "title": "10. 주관적 욕구", "rows": (("needs", "본인·보호자 요청", ("subjective_need", "hydration_support_goal"), "fact"),)},
        {"section_key": "overall_opinion", "title": "11. 총평", "rows": (("opinion", "확인 사실 종합", ("needs_overall_opinion",), "fact"), ("staff_confirmation", "작성자 확인", ("staff_signature",), "signature"))},
    ),
    "long_term_care_service_plan": (
        {
            "section_key": "basic",
            "title": "계획 기본정보",
            "rows": (
                ("care_grade", "장기요양등급", ("long_term_care_grade",), "administrative"),
                ("assessment_date", "계획 기준일", (), "administrative"),
                ("period", "적용 기간", (), "administrative"),
                ("consenter", "동의자", (), "administrative"),
                ("institution", "작성 기관", (), "administrative"),
            ),
        },
        {
            "section_key": "goals",
            "title": "급여제공 목표",
            "rows": (("goal", "확인된 목표 참고 내용", ("service_plan_goal",), "fact"),),
        },
        {
            "section_key": "service_plan",
            "title": "급여제공계획 · 신체활동지원",
            "need_area": "신체활동지원",
            "columns": _CARE_PLAN_COLUMNS,
            "rows": (
                ("dressing", "옷 갈아입기", ("daily_living_state",), "fact"),
                ("face_washing", "세면 도움", ("daily_living_state",), "fact"),
                ("oral_hygiene", "구강청결 도움", ("oral_state",), "fact"),
                ("bathing", "몸 씻기 도움", ("daily_living_state",), "fact"),
                ("hair_washing", "머리감기 도움", ("daily_living_state",), "fact"),
                ("grooming", "몸단장 준비와 자기관리", ("daily_living_state",), "fact"),
                ("nail_care", "손발톱 관리", ("daily_living_state",), "fact"),
                ("appearance_care", "외모관리", ("daily_living_state",), "fact"),
                ("hair_grooming", "머리단장", ("daily_living_state",), "fact"),
                ("tube_feeding_prepare", "경관영양 준비", (), "fact"),
                ("tube_feeding_cleanup", "경관영양 제공 후 뒷정리", (), "fact"),
                ("regular_repositioning", "규칙적인 체위변경", ("skin_state", "mobility_support_need"), "fact"),
                ("repositioning_guidance", "체위변경 지시 및 지켜보기", ("mobility_support_need",), "fact"),
                ("sitting_posture", "올바른 앉은 자세 유지", ("mobility_support_need",), "fact"),
                ("wheelchair_mobility", "휠체어 이용 이동 도움", ("mobility_support_need",), "fact"),
                ("fall_prevention", "위험요소 확인과 낙상예방", ("mobility_support_need", "fall_history"), "fact"),
                ("diaper_change", "기저귀 교환 도움", ("elimination_state",), "fact"),
                ("bowel_management", "배변문제 관리", ("elimination_state",), "fact"),
                ("urinary_management", "배뇨문제 관리", ("elimination_state",), "fact"),
                ("medication_assistance", "복약 도움", ("medication_name", "medication_method", "medication_period", "medication_information"), "fact"),
            ),
        },
        {
            "section_key": "service_plan_emotional",
            "title": "급여제공계획 · 정서지원",
            "need_area": "정서지원",
            "columns": _CARE_PLAN_COLUMNS,
            "rows": (
                ("communication_support", "의사소통 도움", ("communication_state", "cognitive_observation"), "fact"),
                ("needs_observation", "욕구 파악을 위한 관찰", ("subjective_need", "cognitive_observation"), "fact"),
            ),
        },
        {
            "section_key": "service_plan_health",
            "title": "급여제공계획 · 건강관리",
            "need_area": "건강관리",
            "columns": _CARE_PLAN_COLUMNS,
            "rows": (
                ("medication_management", "투약관리", ("medication_name", "medication_method", "medication_period", "medication_information"), "fact"),
                ("contracture_prevention", "관절구축 예방", ("rehabilitation_need", "mobility_support_need"), "fact"),
                ("health_observation", "건강상태 관찰 및 측정", ("medical_history", "skin_state", "nutrition_state"), "fact"),
                ("health_education", "건강교육 및 상담", ("medical_history", "medication_information"), "fact"),
                ("medical_visit_support", "의사진료 지원", ("medical_history",), "fact"),
                ("dementia_care", "치매 관련 돌봄", ("cognitive_observation",), "fact"),
                ("medical_referral", "의료기관 의뢰", ("medical_history",), "fact"),
            ),
        },
        {
            "section_key": "service_plan_nursing",
            "title": "급여제공계획 · 간호처치",
            "need_area": "간호처치",
            "columns": _CARE_PLAN_COLUMNS,
            "rows": (
                ("tube_care", "비위관·위루관 관리", (), "fact"),
                ("perineal_care", "회음부 간호", (), "fact"),
                ("bowel_nursing", "배변문제 간호", ("elimination_state",), "fact"),
                ("oral_nursing", "구강간호", ("oral_state",), "fact"),
            ),
        },
        {
            "section_key": "service_plan_rehabilitation",
            "title": "급여제공계획 · 기능회복훈련",
            "need_area": "기능회복훈련",
            "columns": _CARE_PLAN_COLUMNS,
            "rows": (
                ("physical_cognitive_program", "신체·인지기능 향상 프로그램", ("cognitive_observation", "rehabilitation_need", "resource_use"), "fact"),
                ("physical_function_training", "신체기능 훈련", ("rehabilitation_need", "mobility_support_need"), "fact"),
                ("physical_function_detail", "신체기능 훈련 세부내용", ("rehabilitation_need", "mobility_support_need"), "fact"),
                ("basic_movement_training", "기본동작 훈련", ("mobility_support_need", "daily_living_state"), "fact"),
                ("basic_movement_detail", "기본동작 훈련 세부내용", ("mobility_support_need", "daily_living_state"), "fact"),
                ("adl_training", "일상생활동작 훈련", ("daily_living_state",), "fact"),
                ("adl_training_detail", "일상생활동작 훈련 세부내용", ("daily_living_state",), "fact"),
                ("cognitive_program", "인지기능 프로그램", ("cognitive_observation", "rehabilitation_need"), "fact"),
                ("leisure_emotional_program", "여가·정서 프로그램", ("cognitive_observation", "subjective_need", "resource_use"), "fact"),
                ("social_adaptation_training", "사회적응훈련", ("communication_state", "resource_use"), "fact"),
                ("family_program", "가족대상 프로그램", ("family_environment", "support_network", "subjective_need"), "fact"),
                ("occupational_therapy", "작업치료", ("rehabilitation_need",), "fact"),
            ),
        },
        {
            "section_key": "service_plan_emergency",
            "title": "급여제공계획 · 응급지원",
            "need_area": "응급지원",
            "columns": _CARE_PLAN_COLUMNS,
            "rows": (
                ("emergency_service", "응급서비스", ("fall_history", "medical_history"), "fact"),
            ),
        },
        {
            "section_key": "service_plan_environment",
            "title": "급여제공계획 · 생활 및 환경관리",
            "need_area": "생활 및 환경관리",
            "columns": _CARE_PLAN_COLUMNS,
            "rows": (
                ("bedding_linen", "침구·린넨 교환 및 정리", (), "fact"),
                ("living_environment", "생활환경 관리", ("admission_room",), "administrative"),
                ("supplies_management", "물품 관리", (), "fact"),
                ("laundry_management", "세탁물 관리", (), "fact"),
            ),
        },
        {
            "section_key": "overall",
            "title": "종합의견·변경사유·확인",
            "rows": (
                ("opinion", "종합의견", ("care_plan_overall_opinion",), "fact"),
                ("change_reason", "계획 변경사유", ("care_plan_change_reason",), "fact"),
                ("signature", "작성자 확인·서명", ("staff_signature",), "signature"),
            ),
        },
    ),
}


_FORM_REFERENCE_METADATA: dict[str, dict[str, Any]] = {
    "fall_risk_assessment": {
        "page_count": 1,
        "score_calculation_policy": "직원 직접 입력·자동 계산 안 함",
    },
    "pressure_ulcer_risk_assessment": {
        "page_count": 2,
        "score_calculation_policy": "직원 직접 입력·자동 계산 안 함",
    },
    "cognitive_function_assessment": {
        "page_count": 2,
        "score_calculation_policy": "직원 직접 입력·자동 계산 안 함",
        "copies_proprietary_questions": False,
    },
    "needs_assessment": {"page_count": 5},
    "long_term_care_service_plan": {
        "page_count": 5,
        "automatic_plan_confirmation": False,
    },
}

_FORM_ALLOWED_VALUES: dict[str, tuple[str, ...]] = {
    "assessment_reason": (
        "신규입소",
        "정기 재평가",
        "상태변화",
        "직원 재검토",
    ),
}


def get_assessment_form_field_dictionary() -> dict[str, Any]:
    """Return the stable, privacy-safe field contract for the five draft forms.

    This describes the reference-form structure only. It intentionally neither
    reproduces cognitive test questions nor encodes unverified score and risk
    grade rules.
    """

    documents: list[dict[str, Any]] = []
    for document_type in _DOCUMENT_ORDER:
        sections: list[dict[str, Any]] = []
        for contract in _FORM_SECTION_CONTRACTS[document_type]:
            fields: list[dict[str, Any]] = []
            for row_key, label, source_field_keys, data_type in contract["rows"]:
                restricted = data_type in _RESTRICTED_VALUE_KINDS
                fields.append(
                    {
                        "field_key": f"{document_type}.{row_key}",
                        "document_type": document_type,
                        "section_key": contract["section_key"],
                        "label": label,
                        "data_type": data_type,
                        "required_state": "직원 확인 필요",
                        "allowed_values": list(_FORM_ALLOWED_VALUES.get(row_key, ())),
                        "applicable_reasons": [
                            "new_admission",
                            "regular_reassessment",
                            "state_change_review",
                        ],
                        "evidence_source_types": [
                            "submitted_document",
                            "previous_confirmed_revision",
                            "chat_record",
                            "current_staff_observation",
                        ],
                        "default_status": "needs_confirmation",
                        "supports_previous_value": True,
                        "supports_current_value": True,
                        "ai_write_policy": (
                            "직원 확인 전 자동 생성·확정 금지"
                            if restricted
                            else "확인된 근거만 편집용 초안에 반영"
                        ),
                        "official_standard_status": (
                            "최신 공식 기준 확인 필요"
                            if data_type in {"assessment_score", "diagnosis"}
                            else "참고 양식 구조 확인됨"
                        ),
                        "source_field_keys": list(source_field_keys),
                    }
                )
            sections.append(
                {
                    "section_key": contract["section_key"],
                    "title": contract["title"],
                    "need_area": contract.get("need_area"),
                    "columns": list(contract.get("columns", ())),
                    "notice": contract.get("notice"),
                    "fields": fields,
                }
            )
        documents.append(
            {
                "document_type": document_type,
                "title": _DOCUMENT_LABELS[document_type],
                **_FORM_REFERENCE_METADATA[document_type],
                "sections": sections,
            }
        )

    return {
        "schema_version": "assessment_form_field_dictionary_v1",
        "source_scope": "사용자 제공 비식별 참고 양식의 표 구조",
        "document_order": list(_DOCUMENT_ORDER),
        "documents": documents,
        "safety": {
            "allowed_output_statuses": ["draft", "needs_confirmation"],
            "official_record_auto_saved": False,
            "signature_auto_confirmed": False,
            "public_insurer_auto_transferred": False,
            "unverified_score_or_diagnosis_generated": False,
        },
    }


def _revision_links(
    field_key: str,
    *,
    revision_history: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    for revision in revision_history:
        for change in revision.get("field_changes", []):
            if change.get("field_key") != field_key:
                continue
            links.append(
                {
                    "revision": int(revision["revision"]),
                    "before_value": change.get("before_value"),
                    "after_value": change.get("after_value"),
                    "before_status": change.get("before_status"),
                    "after_status": change.get("after_status"),
                }
            )
    return links


def _field_draft(field, *, staff_confirmed: bool) -> tuple[str, str, str]:
    if field.status in {"confirmed", "reusable", "derivable"}:
        state = "confirmed_fact" if staff_confirmed else "evidence_found"
        label = "직원 확인 사실" if staff_confirmed else "근거 확인됨·직원 최종 확인 전"
        return state, label, f"{field.label}: {field.value}"
    if field.status == "material_conflict":
        return (
            "material_conflict",
            "자료 충돌",
            "",
        )
    if field.status == "current_observation_required":
        return (
            "current_observation_required",
            "현재 상태 확인 필요",
            "",
        )
    if field.value_kind in {"assessment_score", "diagnosis"}:
        return (
            "expert_review_required",
            "전문가 확인 필요",
            "",
        )
    if field.value_kind == "signature":
        return (
            "staff_input_required",
            "직원 직접 입력",
            "",
        )
    if field.status == "admin_missing":
        return (
            "insufficient_evidence",
            "직원 입력",
            "",
        )
    return (
        "staff_input_required",
        "직원 입력",
        "",
    )


def _missing_form_row(
    *,
    document_type: str,
    row_key: str,
    label: str,
    value_kind: str,
    review_state: str = "",
) -> dict[str, Any]:
    state = (
        "expert_review_required"
        if value_kind in {"assessment_score", "diagnosis"}
        else "staff_input_required"
    )
    state_label = "직원 입력"
    value = ""
    return {
        "row_key": f"{document_type}.{row_key}",
        "field_key": f"{document_type}.{row_key}",
        "label": label,
        "value": value,
        "value_kind": value_kind,
        "state": state,
        "state_label": state_label,
        "review_state": review_state,
        "evidence_refs": [],
        "cells": {},
        "cell_states": {},
        "cell_evidence_refs": {},
    }


def _form_row(
    *,
    document_type: str,
    row_key: str,
    label: str,
    field_keys: tuple[str, ...],
    value_kind: str,
    item_by_key: dict[str, dict[str, Any]],
    assessment_date: str | None,
    period_start: str | None,
    period_end: str | None,
    columns: tuple[str, ...],
    need_area: str | None,
    saved_values: dict[str, str],
    assessment_form_rows: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    full_row_key = f"{document_type}.{row_key}"
    saved_value = saved_values.get(full_row_key)
    review_state = saved_values.get(f"{full_row_key}::review_state", "")
    if row_key == "assessment_date" and assessment_date:
        return {
            "row_key": full_row_key,
            "field_key": full_row_key,
            "label": label,
            "value": saved_value if saved_value is not None else assessment_date,
            "value_kind": value_kind,
            "state": "evidence_found",
            "state_label": "작성 기준일",
            "review_state": review_state,
            "evidence_refs": [],
            "cells": {},
            "cell_states": {},
            "cell_evidence_refs": {},
        }
    if row_key == "period" and (period_start or period_end):
        period_value = f"{period_start or ''} ~ {period_end or ''}".strip()
        return {
            "row_key": full_row_key,
            "field_key": full_row_key,
            "label": label,
            "value": saved_value if saved_value is not None else period_value,
            "value_kind": value_kind,
            "state": "evidence_found",
            "state_label": "입력 기간",
            "review_state": review_state,
            "evidence_refs": [],
            "cells": {},
            "cell_states": {},
            "cell_evidence_refs": {},
        }

    matched_items = [item_by_key[key] for key in field_keys if key in item_by_key]
    usable_items = [
        item for item in matched_items
        if item["state"] in {"confirmed_fact", "evidence_found"}
    ]
    conflict_items = [item for item in matched_items if item["state"] == "material_conflict"]
    source_item = conflict_items[0] if conflict_items else usable_items[0] if usable_items else matched_items[0] if matched_items else None
    if source_item is None:
        row = _missing_form_row(
            document_type=document_type,
            row_key=row_key,
            label=label,
            value_kind=value_kind,
            review_state=review_state,
        )
    else:
        selected_items = conflict_items if conflict_items else usable_items or [source_item]
        # 처방전은 약품명·복용법·기간을 원자 항목으로 이미 분리한다. 이 값들이
        # 있으면 같은 내용을 합친 보조 필드를 다시 이어 붙이지 않는다.
        if (
            "medication_information" in field_keys
            and any(
                item["field_key"]
                in {"medication_name", "medication_method", "medication_period"}
                for item in selected_items
            )
        ):
            selected_items = [
                item
                for item in selected_items
                if item["field_key"] != "medication_information"
            ]
        raw_values = list(
            dict.fromkeys(
                item["draft_text"].split(":", 1)[-1].strip()
                for item in selected_items
                if item["draft_text"].split(":", 1)[-1].strip()
            )
        )
        raw_value = " / ".join(raw_values)
        evidence_refs = list(
            dict.fromkeys(
                reference
                for item in selected_items
                for reference in item["evidence_refs"]
            )
        )
        row = {
            "row_key": f"{document_type}.{row_key}",
            "field_key": source_item["field_key"],
            "label": label,
            "value": raw_value,
            "value_kind": source_item["value_kind"],
            "state": source_item["state"],
            "state_label": source_item["state_label"],
            "review_state": review_state,
            "evidence_refs": evidence_refs,
            "cells": {},
            "cell_states": {},
            "cell_evidence_refs": {},
        }

    if columns:
        basis_candidates: list[tuple[str, str, list[str], bool]] = []
        if document_type == "long_term_care_service_plan":
            for basis_key in _CARE_PLAN_BASIS_ROWS.get(row_key, ()):
                basis_document, _, basis_row = basis_key.partition(".")
                basis = assessment_form_rows.get(basis_document, {}).get(
                    f"{basis_document}.{basis_row}"
                )
                value = str((basis or {}).get("value") or "").strip()
                if value:
                    basis_candidates.append(
                        (
                            value,
                            basis_key,
                            list((basis or {}).get("evidence_refs", [])),
                            (basis or {}).get("state") == "staff_edited",
                        )
                    )
        staff_confirmed_candidates = [
            candidate for candidate in basis_candidates if candidate[3]
        ]
        selected_basis = staff_confirmed_candidates or basis_candidates
        confirmed_basis: list[str] = []
        basis_refs: list[str] = []
        for value, basis_key, evidence_refs, _ in selected_basis:
            if value not in confirmed_basis:
                confirmed_basis.append(value)
            basis_refs.append(basis_key)
            basis_refs.extend(evidence_refs)
        basis_value = " / ".join(confirmed_basis)
        has_basis = bool(basis_value)
        row["cells"] = {
            "장기요양 필요영역": need_area or label,
            "장기요양 세부목표": "",
            "장기요양 필요내용": basis_value if has_basis else "근거 부족",
            "세부 제공내용": "",
            "제공방법": "",
            "횟수": "직원 결정 필요",
            "시간(분)": "직원 결정 필요",
            "작성자": "직원 입력 필요",
        }
        row["cell_states"] = {
            "장기요양 필요영역": "form_structure",
            "장기요양 세부목표": "staff_input_required",
            "장기요양 필요내용": (
                "evidence_found" if has_basis else "evidence_insufficient"
            ),
            "세부 제공내용": "staff_input_required",
            "제공방법": "staff_input_required",
            "횟수": "staff_input_required",
            "시간(분)": "staff_input_required",
            "작성자": "staff_input_required",
        }
        row["cell_evidence_refs"] = {
            "장기요양 필요영역": [],
            "장기요양 세부목표": [],
            "장기요양 필요내용": list(dict.fromkeys(basis_refs)),
            "세부 제공내용": [],
            "제공방법": [],
            "횟수": [],
            "시간(분)": [],
            "작성자": [],
        }
        for column in columns:
            saved_key = f"{document_type}.{row_key}::{column}"
            if saved_key in saved_values:
                row["cells"][column] = saved_values[saved_key]
                row["cell_states"][column] = "staff_edited"
    if saved_value is not None:
        row["value"] = saved_value
        row["state"] = "staff_edited"
        row["state_label"] = "직원 입력"
    return row


def _build_form_sections(
    document_type: str,
    *,
    item_by_key: dict[str, dict[str, Any]],
    assessment_date: str | None,
    period_start: str | None,
    period_end: str | None,
    saved_values: dict[str, str],
    assessment_form_rows: dict[str, dict[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for contract in _FORM_SECTION_CONTRACTS[document_type]:
        columns = tuple(contract.get("columns", ()))
        rows = [
            _form_row(
                document_type=document_type,
                row_key=row_key,
                label=label,
                field_keys=field_keys,
                value_kind=value_kind,
                item_by_key=item_by_key,
                assessment_date=assessment_date,
                period_start=period_start,
                period_end=period_end,
                columns=columns,
                need_area=contract.get("need_area"),
                saved_values=saved_values,
                assessment_form_rows=assessment_form_rows,
            )
            for row_key, label, field_keys, value_kind in contract["rows"]
        ]
        sections.append(
            {
                "section_key": contract["section_key"],
                "title": contract["title"],
                "need_area": contract.get("need_area"),
                "columns": list(columns),
                "rows": rows,
                "notice": contract.get("notice"),
            }
        )
    return sections


def _sections_to_text(sections: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for section in sections:
        lines.append(f"[{section['title']}]")
        for row in section["rows"]:
            if row["cells"]:
                lines.append(
                    " | ".join(
                        f"{column}: {row['cells'][column]}"
                        for column in section["columns"]
                    )
                )
            else:
                lines.append(f"{row['label']}: {row['value']}")
        if section.get("notice"):
            lines.append(section["notice"])
        lines.append("")
    return "\n".join(lines).strip()


def build_new_admission_form_workspace(
    bundle_payload: dict[str, Any],
    *,
    field_confirmations: dict[str, dict[str, Any]] | None = None,
    revision_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a form-first, read-only workspace from one saved draft revision."""

    bundle = NewAdmissionDraftBundle.model_validate(bundle_payload)
    confirmations = field_confirmations or {}
    history = revision_history or []
    source_by_ref = {
        source.source_ref: source for source in bundle.evidence_sources
    }
    field_by_key = {field.field_key: field for field in bundle.fields}
    draft_by_type = {draft.document_type: draft for draft in bundle.drafts}

    documents: list[dict[str, Any]] = []
    all_items: dict[str, dict[str, Any]] = {}
    missing_state_counts: dict[str, int] = {}
    confirmed_count = 0
    evidence_found_count = 0
    restricted_auto_generation_count = 0
    assessment_form_rows: dict[str, dict[str, dict[str, Any]]] = {}

    reviewed_assessments = {
        document_type
        for document_type, review in bundle.document_reviews.items()
        if review.state == "reviewed"
    }
    care_plan_available = reviewed_assessments == set(ASSESSMENT_DOCUMENT_TYPES)
    visible_document_order = (
        _DOCUMENT_ORDER
        if care_plan_available
        else tuple(ASSESSMENT_DOCUMENT_TYPES)
    )

    for document_type in visible_document_order:
        draft = draft_by_type[document_type]
        items: list[dict[str, Any]] = []
        for field_key in draft.field_keys:
            field = field_by_key[field_key]
            confirmation = confirmations.get(field_key, {})
            staff_confirmed = confirmation.get("state") == "confirmed"
            state, state_label, draft_text = _field_draft(
                field,
                staff_confirmed=staff_confirmed,
            )
            if state == "confirmed_fact":
                confirmed_count += field_key not in all_items
            elif state == "evidence_found":
                evidence_found_count += field_key not in all_items
            else:
                if field_key not in all_items:
                    missing_state_counts[state] = missing_state_counts.get(state, 0) + 1
            if field.value_kind in _RESTRICTED_VALUE_KINDS and field.value is None:
                restricted_auto_generation_count += 0
            evidence_sources = [
                {
                    "source_ref": reference,
                    "document_kind": source_by_ref[reference].document_kind,
                    "verification_state": source_by_ref[reference].verification_state,
                    "organization_role": source_by_ref[reference].organization_role,
                }
                for reference in field.evidence_refs
                if reference in source_by_ref
            ]
            item = {
                "field_key": field_key,
                "label": field.label,
                "value_kind": field.value_kind,
                "document_types": list(field.document_types),
                "state": state,
                "state_label": state_label,
                "draft_text": draft_text,
                "evidence_refs": list(field.evidence_refs),
                "evidence_sources": evidence_sources,
                "conflict_values": list(field.conflict_values),
                "revision_links": _revision_links(
                    field_key,
                    revision_history=history,
                ),
            }
            all_items.setdefault(field_key, item)
            items.append(item)
        sections = _build_form_sections(
            document_type,
            item_by_key={item["field_key"]: item for item in items},
            assessment_date=bundle.assessment_date,
            period_start=bundle.period_start,
            period_end=bundle.period_end,
            saved_values=bundle.form_values.get(document_type, {}),
            assessment_form_rows=assessment_form_rows,
        )
        if document_type in ASSESSMENT_DOCUMENT_TYPES:
            assessment_form_rows[document_type] = {
                row["row_key"]: row
                for section in sections
                for row in section["rows"]
            }
        documents.append(
            {
                "document_type": document_type,
                "title": _DOCUMENT_LABELS[document_type],
                "status": draft.status,
                "items": items,
                "sections": sections,
                "draft_text": bundle.document_texts.get(
                    document_type,
                    _sections_to_text(sections),
                ),
                "confirmed_or_evidence_count": sum(
                    item["state"] in {"confirmed_fact", "evidence_found"}
                    for item in items
                ),
                "staff_input_count": sum(
                    item["state"] not in {"confirmed_fact", "evidence_found"}
                    for item in items
                ),
            }
        )

    unique_items = list(all_items.values())
    evidence_items = [
        item
        for item in unique_items
        if item["state"] in {"confirmed_fact", "evidence_found"}
    ]
    pending_items = [
        item
        for item in unique_items
        if item["state"] not in {"confirmed_fact", "evidence_found"}
    ]
    source_overview = [
        {
            "source_ref": source.source_ref,
            "document_kind": source.document_kind,
            "organization_role": source.organization_role,
            "verification_state": source.verification_state,
        }
        for source in bundle.evidence_sources
    ]

    return {
        "schema_version": "new_admission_form_workspace_v1",
        "case_ref": bundle.case_ref,
        "revision": bundle.revision,
        "reason": bundle.reason,
        "assessment_date": bundle.assessment_date,
        "period_start": bundle.period_start,
        "period_end": bundle.period_end,
        "materials": [item.model_dump(mode="json") for item in bundle.material_receipts],
        "output_status": "needs_confirmation" if pending_items else "draft",
        "processing_scope": bundle.processing_scope,
        "external_transfer_allowed": False,
        "analysis_summary": {
            "source_count": len(source_overview),
            "confirmed_fact_count": confirmed_count,
            "evidence_found_count": evidence_found_count,
            "staff_input_count": len(pending_items),
            "missing_state_counts": missing_state_counts,
        },
        "comprehensive_report": {
            "title": "기초사정·급여제공계획 종합보고서",
            "summary": (
                f"확인 자료 {len(source_overview)}건에서 양식에 반영할 근거 항목 "
                f"{len(evidence_items)}건과 직원이 보완할 항목 {len(pending_items)}건을 "
                "구분했습니다. 근거가 부족하거나 충돌한 값은 자동 확정하지 않았습니다."
            ),
            "evidence_items": evidence_items,
            "pending_items": pending_items,
            "source_overview": source_overview,
        },
        "documents": documents,
        "care_plan_gate": {
            "available": care_plan_available,
            "reviewed_assessment_count": len(reviewed_assessments),
            "required_assessment_count": len(ASSESSMENT_DOCUMENT_TYPES),
            "reason": (
                ""
                if care_plan_available
                else "기초사정 4종을 검토·저장한 뒤 급여제공계획 초안을 만들 수 있습니다."
            ),
        },
        "safety": {
            "official_record_saved": False,
            "signature_confirmed": False,
            "public_insurer_transferred": False,
            "restricted_auto_generation_count": restricted_auto_generation_count,
            "notice": (
                "이 결과는 공식 저장 전 편집용 초안입니다. 직원이 원문 근거를 "
                "확인하고 수정한 뒤 사용해 주세요."
            ),
        },
    }
