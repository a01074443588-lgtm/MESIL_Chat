import json
from pathlib import Path

from app.new_admission_form_workspace import (
    build_new_admission_form_workspace,
)


FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "m21_new_admission_contract_v1.json"
)


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _item(workspace: dict, field_key: str) -> dict:
    return next(
        item
        for item in workspace["comprehensive_report"]["evidence_items"]
        + workspace["comprehensive_report"]["pending_items"]
        if item["field_key"] == field_key
    )


def test_builds_four_assessments_first_and_keeps_blank_cells_clean():
    workspace = build_new_admission_form_workspace(_fixture())

    assert workspace["schema_version"] == "new_admission_form_workspace_v1"
    assert workspace["external_transfer_allowed"] is False
    assert workspace["output_status"] == "needs_confirmation"
    assert [item["document_type"] for item in workspace["documents"]] == [
        "fall_risk_assessment",
        "pressure_ulcer_risk_assessment",
        "cognitive_function_assessment",
        "needs_assessment",
    ]
    assert workspace["care_plan_gate"] == {
        "available": False,
        "reviewed_assessment_count": 0,
        "required_assessment_count": 4,
        "reason": "기초사정 4종을 검토·저장한 뒤 급여제공계획 초안을 만들 수 있습니다.",
    }
    assert _item(workspace, "admission_room")["draft_text"] == ""
    assert _item(workspace, "recent_fall_count")["state"] == (
        "material_conflict"
    )
    assert _item(workspace, "recent_fall_count")["draft_text"] == ""
    visible_field_keys = {
        item["field_key"]
        for item in workspace["comprehensive_report"]["evidence_items"]
        + workspace["comprehensive_report"]["pending_items"]
    }
    assert "service_frequency" not in visible_field_keys
    assert _item(workspace, "staff_signature")["draft_text"] == ""
    assert not any(
        phrase in row["value"]
        for document in workspace["documents"]
        for section in document["sections"]
        for row in section["rows"]
        for phrase in ("근거 부족", "직원 확인 필요", "전문가 확인 필요")
    )
    assert workspace["safety"] == {
        "official_record_saved": False,
        "signature_confirmed": False,
        "public_insurer_transferred": False,
        "restricted_auto_generation_count": 0,
        "notice": (
            "이 결과는 공식 저장 전 편집용 초안입니다. 직원이 원문 근거를 "
            "확인하고 수정한 뒤 사용해 주세요."
        ),
    }


def test_preserves_evidence_and_revision_links_for_staff_confirmed_fact():
    workspace = build_new_admission_form_workspace(
        _fixture(),
        field_confirmations={"long_term_care_grade": {"state": "confirmed"}},
        revision_history=[
            {
                "revision": 2,
                "field_changes": [
                    {
                        "field_key": "long_term_care_grade",
                        "before_value": None,
                        "after_value": "3",
                        "before_status": "admin_missing",
                        "after_status": "confirmed",
                    }
                ],
            }
        ],
    )

    item = _item(workspace, "long_term_care_grade")
    assert item["state"] == "confirmed_fact"
    assert item["evidence_refs"] == ["public-insurer-syn-001"]
    assert item["evidence_sources"][0]["document_kind"] == (
        "합성 장기요양 공단자료"
    )
    assert item["revision_links"] == [
        {
            "revision": 2,
            "before_value": None,
            "after_value": "3",
            "before_status": "admin_missing",
            "after_status": "confirmed",
        }
    ]


def test_exposes_actual_five_form_sections_instead_of_generic_label_list():
    fixture = _fixture()
    fixture["document_reviews"] = {
        document_type: {"state": "reviewed"}
        for document_type in (
            "fall_risk_assessment",
            "pressure_ulcer_risk_assessment",
            "cognitive_function_assessment",
            "needs_assessment",
        )
    }
    fixture["form_values"] = {
        "fall_risk_assessment": {
            "fall_risk_assessment.gait_balance": "보행 시 부축 필요",
        },
        "pressure_ulcer_risk_assessment": {},
        "cognitive_function_assessment": {},
        "needs_assessment": {},
    }
    workspace = build_new_admission_form_workspace(fixture)
    documents = {item["document_type"]: item for item in workspace["documents"]}

    assert [section["title"] for section in documents["fall_risk_assessment"]["sections"]] == [
        "평가 기본정보",
        "낙상 위험 평가 항목",
        "점수·판정·확인",
    ]
    assert [row["label"] for row in documents["fall_risk_assessment"]["sections"][1]["rows"]] == [
        "연령",
        "정신상태",
        "배설상태",
        "낙상경험",
        "활동수준",
        "보행·균형",
        "최근 투약",
    ]

    assert [row["label"] for row in documents["pressure_ulcer_risk_assessment"]["sections"][1]["rows"]] == [
        "감각인지",
        "습기",
        "활동",
        "이동",
        "영양",
        "마찰·전단력",
    ]

    needs_titles = [
        section["title"] for section in documents["needs_assessment"]["sections"]
    ]
    assert needs_titles == [
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

    care_plan = documents["long_term_care_service_plan"]
    service_tables = [
        section
        for section in care_plan["sections"]
        if section["section_key"].startswith("service_plan")
    ]
    service_table = service_tables[0]
    assert service_table["columns"] == [
        "장기요양 필요영역",
        "장기요양 세부목표",
        "장기요양 필요내용",
        "세부 제공내용",
        "제공방법",
        "횟수",
        "시간(분)",
        "작성자",
    ]
    assert sum(len(section["rows"]) for section in service_tables) == 50
    mobility_row = next(
        row
        for section in service_tables
        for row in section["rows"]
        if row["row_key"].endswith(".fall_prevention")
    )
    assert mobility_row["cells"]["장기요양 필요영역"] == "신체활동지원"
    assert mobility_row["cells"]["장기요양 필요내용"] == "보행 시 부축 필요"
    assert mobility_row["cells"]["장기요양 세부목표"] == ""
    assert mobility_row["cells"]["세부 제공내용"] == ""
    assert mobility_row["cells"]["제공방법"] == ""
    assert mobility_row["cells"]["횟수"] == "직원 결정 필요"
    assert mobility_row["cells"]["시간(분)"] == "직원 결정 필요"
    assert mobility_row["cells"]["작성자"] == "직원 입력 필요"
    assert mobility_row["cell_states"] == {
        "장기요양 필요영역": "form_structure",
        "장기요양 세부목표": "staff_input_required",
        "장기요양 필요내용": "evidence_found",
        "세부 제공내용": "staff_input_required",
        "제공방법": "staff_input_required",
        "횟수": "staff_input_required",
        "시간(분)": "staff_input_required",
        "작성자": "staff_input_required",
    }
    assert "fall_risk_assessment.gait_balance" in mobility_row[
        "cell_evidence_refs"
    ]["장기요양 필요내용"]
    assert workspace["care_plan_gate"]["available"] is True


def test_care_plan_marks_missing_and_staff_decision_cells_without_inventing_values():
    fixture = _fixture()
    fixture["document_reviews"] = {
        document_type: {"state": "reviewed"}
        for document_type in (
            "fall_risk_assessment",
            "pressure_ulcer_risk_assessment",
            "cognitive_function_assessment",
            "needs_assessment",
        )
    }
    workspace = build_new_admission_form_workspace(fixture)
    care_plan = next(
        document
        for document in workspace["documents"]
        if document["document_type"] == "long_term_care_service_plan"
    )
    tube_care = next(
        row
        for section in care_plan["sections"]
        for row in section["rows"]
        if row["row_key"].endswith(".tube_care")
    )

    assert tube_care["cells"]["장기요양 필요영역"] == "간호처치"
    assert tube_care["cells"]["장기요양 필요내용"] == "근거 부족"
    assert tube_care["cells"]["장기요양 세부목표"] == ""
    assert tube_care["cells"]["세부 제공내용"] == ""
    assert tube_care["cells"]["제공방법"] == ""
    assert list(tube_care["cells"].values()).count("근거 부족") == 1
    assert tube_care["cells"]["횟수"] == "직원 결정 필요"
    assert tube_care["cells"]["시간(분)"] == "직원 결정 필요"
    assert tube_care["cells"]["작성자"] == "직원 입력 필요"
    assert tube_care["cell_states"]["장기요양 필요내용"] == (
        "evidence_insufficient"
    )
    assert tube_care["cell_states"]["장기요양 세부목표"] == (
        "staff_input_required"
    )
    assert tube_care["cell_states"]["세부 제공내용"] == (
        "staff_input_required"
    )
    assert tube_care["cell_states"]["제공방법"] == "staff_input_required"


def test_reopens_staff_edited_care_plan_method_without_inventing_other_plan_values():
    fixture = _fixture()
    fixture["document_reviews"] = {
        document_type: {"state": "reviewed"}
        for document_type in (
            "fall_risk_assessment",
            "pressure_ulcer_risk_assessment",
            "cognitive_function_assessment",
            "needs_assessment",
        )
    }
    fixture["form_values"] = {
        "fall_risk_assessment": {
            "fall_risk_assessment.gait_balance": "보행 시 부축 필요",
        },
        "long_term_care_service_plan": {
            "long_term_care_service_plan.fall_prevention::제공방법": (
                "합성 직원이 직접 확인한 이동 보조 방법"
            ),
        },
    }

    workspace = build_new_admission_form_workspace(fixture)
    care_plan = next(
        document
        for document in workspace["documents"]
        if document["document_type"] == "long_term_care_service_plan"
    )
    row = next(
        row
        for section in care_plan["sections"]
        for row in section["rows"]
        if row["row_key"].endswith(".fall_prevention")
    )

    assert row["cells"]["장기요양 필요내용"] == "보행 시 부축 필요"
    assert row["cells"]["장기요양 세부목표"] == ""
    assert row["cells"]["세부 제공내용"] == ""
    assert row["cells"]["제공방법"] == "합성 직원이 직접 확인한 이동 보조 방법"
    assert row["cell_states"]["제공방법"] == "staff_edited"


def test_restricted_scores_and_signatures_remain_blank_until_staff_inputs_them():
    workspace = build_new_admission_form_workspace(_fixture())
    documents = {item["document_type"]: item for item in workspace["documents"]}

    restricted_rows = [
        row
        for document in documents.values()
        for section in document["sections"]
        for row in section["rows"]
        if row["value_kind"] in {"assessment_score", "diagnosis", "service_frequency", "service_duration", "signature"}
    ]
    assert restricted_rows
    assert all(row["state"] not in {"confirmed_fact", "evidence_found"} for row in restricted_rows if not row["evidence_refs"])
    assert all(row["value"] == "" for row in restricted_rows if not row["evidence_refs"])


def test_reopens_staff_row_review_state_with_saved_form_value():
    fixture = _fixture()
    fixture["form_values"] = {
        "fall_risk_assessment": {
            "fall_risk_assessment.gait_balance": "보행 시 부축 필요",
            "fall_risk_assessment.gait_balance::review_state": "confirmed",
            "fall_risk_assessment.medication::review_state": "follow_up",
        }
    }

    workspace = build_new_admission_form_workspace(fixture)
    fall_document = next(
        document
        for document in workspace["documents"]
        if document["document_type"] == "fall_risk_assessment"
    )
    rows = {
        row["row_key"]: row
        for section in fall_document["sections"]
        for row in section["rows"]
    }

    assert rows["fall_risk_assessment.gait_balance"]["value"] == "보행 시 부축 필요"
    assert rows["fall_risk_assessment.gait_balance"]["review_state"] == "confirmed"
    assert rows["fall_risk_assessment.medication"]["review_state"] == "follow_up"
    assert rows["fall_risk_assessment.mental_state"]["review_state"] == ""
