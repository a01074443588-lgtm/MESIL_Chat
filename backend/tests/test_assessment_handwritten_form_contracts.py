from app.new_admission_form_workspace import (
    get_assessment_form_field_dictionary,
)


EXPECTED_DOCUMENT_ORDER = [
    "fall_risk_assessment",
    "pressure_ulcer_risk_assessment",
    "cognitive_function_assessment",
    "needs_assessment",
    "long_term_care_service_plan",
]


def _document(catalog: dict, document_type: str) -> dict:
    return next(
        item
        for item in catalog["documents"]
        if item["document_type"] == document_type
    )


def test_handwritten_reference_forms_keep_page_and_section_order():
    catalog = get_assessment_form_field_dictionary()

    assert catalog["document_order"] == EXPECTED_DOCUMENT_ORDER
    assert [item["page_count"] for item in catalog["documents"]] == [1, 2, 2, 5, 5]
    assert [
        section["title"]
        for section in _document(catalog, "needs_assessment")["sections"]
    ] == [
        "기본정보",
        "1. 영양상태",
        "2. 구강상태",
        "3. 질병상태",
        "4. 신체상태(일상생활 동작 수행능력)",
        "5. 인지상태",
        "6. 의사소통",
        "7. 가족 및 환경상태",
        "8. 자원이용 욕구",
        "9. 재활상태",
        "10. 주관적 욕구",
        "11. 총평",
    ]


def test_cognitive_contract_keeps_domain_score_slots_without_copying_questions():
    catalog = get_assessment_form_field_dictionary()
    cognitive = _document(catalog, "cognitive_function_assessment")
    keys = [
        field["field_key"]
        for section in cognitive["sections"]
        for field in section["fields"]
    ]

    assert keys == [
        "cognitive_function_assessment.care_grade",
        "cognitive_function_assessment.assessment_date",
        "cognitive_function_assessment.assessment_place",
        "cognitive_function_assessment.assessment_reason",
        "cognitive_function_assessment.examiner",
        "cognitive_function_assessment.registration_score",
        "cognitive_function_assessment.time_orientation_score",
        "cognitive_function_assessment.place_orientation_score",
        "cognitive_function_assessment.recall_score",
        "cognitive_function_assessment.attention_calculation_score",
        "cognitive_function_assessment.language_score",
        "cognitive_function_assessment.drawing_score",
        "cognitive_function_assessment.total_score",
        "cognitive_function_assessment.judgment",
        "cognitive_function_assessment.note",
    ]
    assert cognitive["copies_proprietary_questions"] is False
    assert cognitive["score_calculation_policy"] == "직원 직접 입력·자동 계산 안 함"


def test_care_plan_columns_match_reference_form_and_are_staff_confirmed():
    catalog = get_assessment_form_field_dictionary()
    care_plan = _document(catalog, "long_term_care_service_plan")
    service_section = next(
        section
        for section in care_plan["sections"]
        if section["section_key"] == "service_plan"
    )

    assert service_section["columns"] == [
        "장기요양 필요영역",
        "장기요양 세부목표",
        "장기요양 필요내용",
        "세부 제공내용",
        "제공방법",
        "횟수",
        "시간(분)",
        "작성자",
    ]
    assert care_plan["automatic_plan_confirmation"] is False


def test_care_plan_keeps_actual_seven_service_areas_and_detailed_rows():
    catalog = get_assessment_form_field_dictionary()
    care_plan = _document(catalog, "long_term_care_service_plan")
    service_sections = [
        section
        for section in care_plan["sections"]
        if section["section_key"].startswith("service_plan")
    ]

    assert [section["title"] for section in service_sections] == [
        "급여제공계획 · 신체활동지원",
        "급여제공계획 · 정서지원",
        "급여제공계획 · 건강관리",
        "급여제공계획 · 간호처치",
        "급여제공계획 · 기능회복훈련",
        "급여제공계획 · 응급지원",
        "급여제공계획 · 생활 및 환경관리",
    ]
    assert sum(len(section["fields"]) for section in service_sections) == 50
    assert [field["label"] for field in service_sections[0]["fields"][:4]] == [
        "옷 갈아입기",
        "세면 도움",
        "구강청결 도움",
        "몸 씻기 도움",
    ]
    assert [field["label"] for field in service_sections[-1]["fields"]] == [
        "침구·린넨 교환 및 정리",
        "생활환경 관리",
        "물품 관리",
        "세탁물 관리",
    ]
    assert all(
        section["columns"] == service_sections[0]["columns"]
        for section in service_sections
    )


def test_every_form_field_exposes_safe_reuse_and_evidence_metadata():
    catalog = get_assessment_form_field_dictionary()
    required_keys = {
        "field_key",
        "document_type",
        "section_key",
        "label",
        "data_type",
        "required_state",
        "allowed_values",
        "applicable_reasons",
        "evidence_source_types",
        "default_status",
        "supports_previous_value",
        "supports_current_value",
        "ai_write_policy",
        "official_standard_status",
    }

    fields = [
        field
        for document in catalog["documents"]
        for section in document["sections"]
        for field in section["fields"]
    ]
    assert fields
    assert all(required_keys <= set(field) for field in fields)
    assert len({field["field_key"] for field in fields}) == len(fields)
    assert all(
        field["default_status"] in {"draft", "needs_confirmation"}
        for field in fields
    )

    restricted = {
        "assessment_score",
        "diagnosis",
        "service_frequency",
        "service_duration",
        "signature",
    }
    assert all(
        field["ai_write_policy"] == "직원 확인 전 자동 생성·확정 금지"
        for field in fields
        if field["data_type"] in restricted
    )
