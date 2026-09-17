from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
import importlib
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlalchemy import func, select

from app.database import SessionLocal
from app.assessment_file_intake import (
    ParsedAssessmentMaterial,
    build_assessment_evidence_entries,
    build_parsed_materials,
    build_assessment_draft_bundle,
    classify_document_kinds,
    extract_pdf_material,
    extract_pdf_page_texts,
    merge_assessment_materials_into_bundle,
    parse_document_kind_selection,
)
from app.new_admission_form_workspace import build_new_admission_form_workspace
from app.main import app
from app.models import (
    AttachmentTextExtraction,
    HandwritingCorrectionApproval,
    Message,
    MessageAttachment,
    MessageComment,
    MessageResidentLink,
    NewAdmissionDraftRecord,
    NewAdmissionDraftRevision,
    Organization,
    Resident,
    Room,
    User,
)


ORIGIN = {"origin": "http://testserver"}
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "m21_l_actual_file_flow_v1"
TWO_CYCLE_FIXTURE_ROOT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "resident0002_to_0005_two_cycle_v1"
)


def test_resident0002_pdf_text_layer_keeps_the_korean_source_labels():
    pdf_path = (
        TWO_CYCLE_FIXTURE_ROOT
        / "어르0002"
        / "01_new_admission_sources"
        / "long_term_care_certificate.pdf"
    )

    page_count, page_texts = extract_pdf_page_texts(pdf_path.read_bytes())

    assert page_count == 1
    assert "장기요양인정서" in page_texts[0]
    assert "장기요양등급" in page_texts[0]
    assert "합성 장기요양 3등급" in page_texts[0]


def test_missing_fields_are_classified_by_the_kind_of_staff_check_needed():
    material = ParsedAssessmentMaterial(
        source_ref="classification-boundary-001",
        document_kind="care_grade_certificate",
        mime_type="application/pdf",
        page_count=1,
        size_bytes=1,
        extracted_text="장기요양인정서\n장기요양등급: 합성 장기요양 4등급",
        page_numbers=(1,),
    )

    bundle = build_assessment_draft_bundle(
        [material],
        case_ref="classification-boundary-case-001",
        reason="new_admission",
        assessment_date="2026-09-09",
        period_start=None,
        period_end=None,
    ).model_dump(mode="json")

    assert _field(bundle, "benefit_effective_period")["status"] == "admin_missing"
    assert _field(bundle, "admission_room")["status"] == "admin_missing"
    assert _field(bundle, "mobility_support_need")["status"] == (
        "current_observation_required"
    )
    assert _field(bundle, "skin_moisture_current")["status"] == (
        "current_observation_required"
    )
    assert _field(bundle, "cognitive_orientation_result")["status"] == (
        "current_observation_required"
    )
    assert _field(bundle, "fall_score")["status"] == "current_observation_required"
    assert _field(bundle, "medical_diagnosis")["status"] == (
        "current_observation_required"
    )
    assert _field(bundle, "staff_signature")["status"] == "user_decision_required"


def test_new_admission_table_rows_without_colons_map_confirmed_facts():
    materials = [
        ParsedAssessmentMaterial(
            source_ref="certificate-table",
            document_kind="care_grade_certificate",
            mime_type="application/pdf",
            page_count=1,
            size_bytes=1,
            extracted_text=(
                "장기요양인정서\n"
                "대상 코드 어르0002\n"
                "장기요양등급 합성 장기요양 3등급\n"
                "입소 기준일 2026-04-25\n"
                "서비스 구분 시설급여 이용 참고"
            ),
        ),
        ParsedAssessmentMaterial(
            source_ref="care-plan-table",
            document_kind="individual_long_term_care_plan",
            mime_type="application/pdf",
            page_count=1,
            size_bytes=1,
            extracted_text=(
                "개인별장기요양이용계획서\n"
                "이동 실내에서 보행기를 사용하며 가까이 관찰하면 이동함\n"
                "식사 부드러운 일반식을 제공량의 3/4 정도 섭취함\n"
                "의사소통 짧고 분명한 질문에 적절히 답함\n"
                "본인 욕구 익숙한 생활 리듬을 유지하고 싶다고 표현함\n"
                "보호자 요청 이동 시 안전 확인을 요청함"
            ),
        ),
        ParsedAssessmentMaterial(
            source_ref="health-table",
            document_kind="health_submission",
            mime_type="application/pdf",
            page_count=1,
            size_bytes=1,
            extracted_text=(
                "입소 전 건강검진\n"
                "피부 상태 피부 손상 관찰되지 않음\n"
                "배설 화장실 이용, 불편 호소 없음\n"
                "인지 일상 대화와 간단한 지시 이해 가능\n"
                "수분 물 200mL 제공 시 전량 섭취 확인"
            ),
        ),
    ]

    bundle = build_assessment_draft_bundle(
        case_ref="assessment-real-table-text",
        reason="new_admission",
        assessment_date="2026-04-25",
        period_start=None,
        period_end=None,
        materials=materials,
    ).model_dump(mode="json")

    assert _field(bundle, "long_term_care_grade")["value"] == "합성 장기요양 3등급"
    assert "보행기" in _field(bundle, "mobility_support_need")["value"]
    assert "3/4" in _field(bundle, "nutrition_state")["value"]
    assert "짧고 분명한 질문" in _field(bundle, "communication_state")["value"]
    assert "피부 손상 관찰되지 않음" == _field(bundle, "skin_state")["value"]
    assert "화장실 이용" in _field(bundle, "elimination_state")["value"]
    assert "200mL" in _field(bundle, "hydration_support_goal")["value"]
    assert "익숙한 생활 리듬" in _field(bundle, "subjective_need")["value"]


def test_resident0002_four_source_pdfs_map_complete_atomic_facts_without_table_metadata():
    source_directory = (
        TWO_CYCLE_FIXTURE_ROOT / "어르0002" / "01_new_admission_sources"
    )
    materials: list[ParsedAssessmentMaterial] = []
    for index, pdf_path in enumerate(sorted(source_directory.glob("*.pdf")), start=1):
        _page_count, page_texts = extract_pdf_page_texts(pdf_path.read_bytes())
        materials.extend(
            build_parsed_materials(
                page_texts=page_texts,
                source_prefix=f"material-{index:02d}",
                selected_kinds=None,
                reason="new_admission",
                mime_type="application/pdf",
                size_bytes=pdf_path.stat().st_size,
            )
        )

    bundle = build_assessment_draft_bundle(
        case_ref="assessment-resident0002-real-pdfs",
        reason="new_admission",
        assessment_date="2026-04-25",
        period_start=None,
        period_end=None,
        materials=materials,
    ).model_dump(mode="json")

    assert _field(bundle, "long_term_care_grade")["value"] == "합성 장기요양 3등급"
    assert _field(bundle, "benefit_type")["value"] == "시설급여 이용 참고"
    assert _field(bundle, "mobility_support_need")["value"] == (
        "실내에서 보행기를 사용하며 가까이 관찰하면 이동함"
    )
    assert _field(bundle, "nutrition_state")["value"] == (
        "부드러운 일반식을 제공량의 3/4 정도 섭취함"
    )
    assert _field(bundle, "hydration_support_goal")["value"] == (
        "물을 가까이 준비하면 스스로 마심"
    )
    assert _field(bundle, "elimination_state")["value"] == (
        "화장실을 이용하며 이동 시 안전 확인이 필요함"
    )
    assert _field(bundle, "skin_state")["value"] == (
        "피부 손상과 발적이 관찰되지 않음"
    )
    assert _field(bundle, "cognitive_observation")["value"] == (
        "간단한 안내를 이해하고 본인 의사를 표현함"
    )
    assert _field(bundle, "communication_state")["value"] == (
        "짧고 분명한 질문에 적절히 답함"
    )
    assert _field(bundle, "medication_method")["value"].startswith(
        "식후 복용 지원이 필요하며 약명·용량은 합성 처방전에서 확인함"
    )
    assert _field(bundle, "subjective_need")["value"] == (
        "본인: 안전한 이동을 지원하면서 가능한 동작은 스스로 하기를 원함 / "
        "보호자: 이동 안전과 식사 상태 변화를 알려 달라고 요청함"
    )


def _login_admin(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "AdminPass!234"},
        headers=ORIGIN,
    )
    assert response.status_code == 200, response.text


def _create_test_resident(*, display_name: str = "어르0091") -> str:
    with SessionLocal() as db:
        organization = db.scalar(select(Organization).limit(1))
        assert organization is not None
        resident = Resident(
            organization_id=organization.id,
            internal_code=f"M21-L-{uuid4().hex}",
            display_name=display_name,
            service_type="facility",
            is_test_data=True,
        )
        db.add(resident)
        db.commit()
        return str(resident.id)


def _field(bundle: dict, key: str) -> dict:
    return next(item for item in bundle["fields"] if item["field_key"] == key)


def _baseline_field(bundle: dict, key: str) -> dict:
    return next(
        item for item in bundle["baseline_fields"] if item["field_key"] == key
    )


def _comparison_item(bundle: dict, key: str) -> dict:
    return next(
        item for item in bundle["comparison_items"] if item["field_key"] == key
    )


def _create_reassessment_chat_fixture(resident_id: str) -> None:
    with SessionLocal() as db:
        resident = db.get(Resident, resident_id)
        assert resident is not None
        organization = db.get(Organization, resident.organization_id)
        assert organization is not None
        admin = db.scalar(
            select(User).where(User.organization_id == organization.id).limit(1)
        )
        room = db.scalar(
            select(Room).where(Room.organization_id == organization.id).limit(1)
        )
        assert admin is not None
        assert room is not None
        other = Resident(
            organization_id=organization.id,
            internal_code=f"M21-OTHER-{uuid4().hex}",
            display_name=f"어르{uuid4().hex[:4]}",
            service_type="facility",
            is_test_data=True,
        )
        db.add(other)
        db.flush()
        created_at = datetime(2026, 8, 20, 5, 0, tzinfo=timezone.utc)

        for minute in (0, 1):
            message = Message(
                organization_id=organization.id,
                room_id=room.id,
                sender_id=admin.id,
                message_type="chat",
                body="14:00 물 200mL 제공함.",
                resident_id=resident.id,
                is_test_data=True,
                created_at=created_at.replace(minute=minute),
            )
            db.add(message)
            db.flush()
            db.add(
                MessageComment(
                    organization_id=organization.id,
                    message_id=message.id,
                    author_id=admin.id,
                    body="16:00 전량 섭취 확인함.",
                    is_test_data=True,
                    created_at=created_at.replace(minute=minute, second=30),
                )
            )

        linked_message = Message(
            organization_id=organization.id,
            room_id=room.id,
            sender_id=admin.id,
            message_type="chat",
            body="손글씨 관찰 기록을 첨부함.",
            resident_id=None,
            is_test_data=True,
            created_at=created_at.replace(hour=6),
        )
        db.add(linked_message)
        db.flush()
        db.add(
            MessageResidentLink(
                organization_id=organization.id,
                message_id=linked_message.id,
                resident_id=resident.id,
                source="manual",
                status="confirmed",
                reviewed_by_id=admin.id,
                reviewed_at=created_at.replace(hour=6),
            )
        )
        attachment = MessageAttachment(
            organization_id=organization.id,
            message_id=linked_message.id,
            uploader_id=admin.id,
            storage_key=f"assessment-chat/{uuid4().hex}.jpg",
            original_name="coded-handwriting.jpg",
            mime_type="image/jpeg",
            size_bytes=128,
        )
        db.add(attachment)
        db.flush()
        db.add(
            AttachmentTextExtraction(
                organization_id=organization.id,
                attachment_id=attachment.id,
                status="reviewed",
                provider="internal",
                model_name="synthetic-ocr",
                extracted_text="최초 OCR 잘못된 문장",
                reviewed_text="직원 검토 중간 문장",
                requested_by_id=admin.id,
                reviewed_by_id=admin.id,
            )
        )
        db.add(
            HandwritingCorrectionApproval(
                organization_id=organization.id,
                approval_group_id=uuid4(),
                revision=1,
                image_attachment_id=attachment.id,
                mode="direct_typing",
                status="approved",
                initial_ocr="최초 OCR 잘못된 문장",
                approved_final_text="18:00 보행 시 부축이 필요함.",
                sentence_decisions=[],
                evidence_refs=["synthetic-chat-handwriting"],
                confidence_state={"staff_confirmed": True},
                conflicts_confirmed=True,
                approved_by_id=admin.id,
                approved_at=created_at.replace(hour=6, minute=5),
                idempotency_key=uuid4().hex,
                is_test_data=True,
                official_record_saved=False,
                training_data_adopted=False,
                external_transfer_allowed=False,
            )
        )

        db.add(
            Message(
                organization_id=organization.id,
                room_id=room.id,
                sender_id=admin.id,
                message_type="chat",
                body="다른 어르신의 오염되면 안 되는 기록",
                resident_id=other.id,
                is_test_data=True,
                created_at=created_at.replace(hour=7),
            )
        )
        db.add(
            Message(
                organization_id=organization.id,
                room_id=room.id,
                sender_id=admin.id,
                message_type="notice",
                body="전체 직원 공지로 기초사정 근거가 아님",
                resident_id=resident.id,
                is_test_data=True,
                created_at=created_at.replace(hour=8),
            )
        )
        db.commit()


def test_actual_pdf_upload_builds_exact_five_drafts_and_append_only_staff_revision():
    with TestClient(app) as client:
        _login_admin(client)
        resident_id = _create_test_resident()
        filenames = [
            ("new_admission_certificate.pdf", "care_grade_certificate"),
            ("new_admission_individual_plan.pdf", "individual_long_term_care_plan"),
            ("new_admission_consultation.pdf", "consultation_log"),
            ("new_admission_transfer.pdf", "transfer_document"),
        ]
        file_handles = [open(FIXTURE_ROOT / name, "rb") for name, _ in filenames]
        try:
            created = client.post(
                f"/api/workdesk/residents/{resident_id}/assessment-drafts/analyze",
                headers=ORIGIN,
                data={
                    "reason": "new_admission",
                    "assessment_date": "2026-08-31",
                    "document_kinds": [kind for _, kind in filenames],
                },
                files=[
                    ("files", (name, handle, "application/pdf"))
                    for (name, _), handle in zip(filenames, file_handles, strict=True)
                ],
            )
        finally:
            for handle in file_handles:
                handle.close()
        assert created.status_code == 201, created.text
        payload = created.json()
        assert payload["current_revision"] == 1
        assert len(payload["revisions"]) == 1
        bundle = payload["revisions"][0]["bundle"]
        assert bundle["reason"] == "new_admission"
        assert len(bundle["material_receipts"]) == 4
        assert set(bundle["document_texts"]) == {
            "fall_risk_assessment",
            "pressure_ulcer_risk_assessment",
            "cognitive_function_assessment",
            "needs_assessment",
        }
        assert _field(bundle, "long_term_care_grade")["value"] == "3등급"
        assert _field(bundle, "mobility_support_need")["value"] == "보행 시 부축 필요"
        fall = _field(bundle, "fall_history")
        assert fall["status"] == "material_conflict"
        assert fall["value"] is None
        assert len(fall["conflict_values"]) == 2
        assert _field(bundle, "pressure_ulcer_score")["value"] is None
        assert _field(bundle, "staff_signature")["value"] is None

        questions = client.get(
            f"/api/workdesk/residents/{resident_id}/new-admission-drafts/{payload['id']}/questions"
        )
        assert questions.status_code == 200, questions.text
        workspace = questions.json()["form_workspace"]
        assert len(workspace["documents"]) == 4
        assert workspace["care_plan_gate"]["available"] is False
        assert workspace["materials"][0]["display_label"] == "제출 자료 1"
        assert workspace["safety"]["official_record_saved"] is False

        revision_two = deepcopy(bundle)
        revision_two["revision"] = 2
        revision_two["supersedes_revision"] = 1
        revision_two["document_texts"]["needs_assessment"] += "\n직원 보완: 합성 확인 문구"
        revision_two["form_values"] = {
            "fall_risk_assessment": {
                "fall_risk_assessment.gait_balance": "보행 시 부축 필요",
                "fall_risk_assessment.gait_balance::review_state": "confirmed",
            },
            "pressure_ulcer_risk_assessment": {
                "pressure_ulcer_risk_assessment.moisture::review_state": "not_confirmed",
            },
            "cognitive_function_assessment": {
                "cognitive_function_assessment.decision::review_state": "follow_up",
            },
            "needs_assessment": {
                "needs_assessment.nutrition::review_state": "confirmed",
            },
        }
        saved = client.post(
            f"/api/workdesk/residents/{resident_id}/new-admission-drafts/{payload['id']}/revisions",
            headers=ORIGIN,
            json={
                "bundle": revision_two,
                "confirmed_field_keys": [],
                "reviewed_document_types": [
                    "fall_risk_assessment",
                    "pressure_ulcer_risk_assessment",
                    "cognitive_function_assessment",
                    "needs_assessment",
                ],
            },
        )
        assert saved.status_code == 201, saved.text
        saved_payload = saved.json()
        assert saved_payload["current_revision"] == 2
        assert len(saved_payload["revisions"]) == 2
        assert "합성 확인 문구" not in saved_payload["revisions"][0]["bundle"]["document_texts"]["needs_assessment"]
        assert "합성 확인 문구" in saved_payload["revisions"][1]["bundle"]["document_texts"]["needs_assessment"]
        assert any(
            change["field_key"] == "document_text.needs_assessment"
            for change in saved_payload["revisions"][1]["field_changes"]
        )
        assert saved_payload["revisions"][1]["bundle"]["form_values"] == revision_two["form_values"]
        assert all(
            review["state"] == "reviewed"
            for review in saved_payload["revisions"][1]["bundle"]["document_reviews"].values()
        )

        reviewed_questions = client.get(
            f"/api/workdesk/residents/{resident_id}/new-admission-drafts/{payload['id']}/questions"
        )
        assert reviewed_questions.status_code == 200, reviewed_questions.text
        reviewed_workspace = reviewed_questions.json()["form_workspace"]
        assert reviewed_workspace["care_plan_gate"]["available"] is True
        assert len(reviewed_workspace["documents"]) == 5
        fall_document = next(
            document
            for document in reviewed_workspace["documents"]
            if document["document_type"] == "fall_risk_assessment"
        )
        gait_row = next(
            row
            for section in fall_document["sections"]
            for row in section["rows"]
            if row["row_key"] == "fall_risk_assessment.gait_balance"
        )
        assert gait_row["value"] == "보행 시 부축 필요"
        assert gait_row["review_state"] == "confirmed"
        assert reviewed_questions.json()["care_plan_preview"] is not None

        reopened = client.get(
            f"/api/workdesk/residents/{resident_id}/new-admission-drafts/{payload['id']}"
        )
        assert reopened.status_code == 200
        assert reopened.json() == saved_payload
        with SessionLocal() as db:
            assert db.scalar(
                select(func.count(NewAdmissionDraftRecord.id)).where(
                    NewAdmissionDraftRecord.resident_id == resident_id
                )
            ) == 1
            assert db.scalar(
                select(func.count(NewAdmissionDraftRevision.id))
                .join(NewAdmissionDraftRecord)
                .where(NewAdmissionDraftRecord.resident_id == resident_id)
            ) == 2


def test_assessment_upload_rejects_explicit_target_code_mismatch_without_saving():
    """A coded synthetic packet must not be attached to another resident."""

    with TestClient(app) as client:
        _login_admin(client)
        resident_id = _create_test_resident(display_name="어르0001")
        with SessionLocal() as db:
            before = db.scalar(
                select(func.count())
                .select_from(NewAdmissionDraftRecord)
                .where(NewAdmissionDraftRecord.resident_id == resident_id)
            )

        with open(FIXTURE_ROOT / "new_admission_certificate.pdf", "rb") as handle:
            response = client.post(
                f"/api/workdesk/residents/{resident_id}/assessment-drafts/analyze",
                headers=ORIGIN,
                data={
                    "reason": "new_admission",
                    "assessment_date": "2026-08-31",
                    "document_kinds": ["care_grade_certificate"],
                },
                files=[
                    (
                        "files",
                        ("new_admission_certificate.pdf", handle, "application/pdf"),
                    )
                ],
            )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == (
            "선택한 어르신과 제출 자료에 표시된 대상 코드가 다릅니다. "
            "어르신과 파일을 다시 확인해 주세요."
        )
        with SessionLocal() as db:
            after = db.scalar(
                select(func.count())
                .select_from(NewAdmissionDraftRecord)
                .where(NewAdmissionDraftRecord.resident_id == resident_id)
            )
        assert after == before


def test_additional_material_rejects_explicit_target_code_mismatch_without_revision(
    monkeypatch,
):
    main_module = importlib.import_module("app.main")
    monkeypatch.setattr(
        main_module,
        "extract_assessment_document_text",
        lambda *_args, **_kwargs: "건강 제출자료\n체온: 36.5",
    )
    image = Image.new("RGB", (320, 240), "white")
    image_bytes = BytesIO()
    image.save(image_bytes, format="JPEG")

    with TestClient(app) as client:
        _login_admin(client)
        resident_id = _create_test_resident(display_name="어르0001")
        created = client.post(
            f"/api/workdesk/residents/{resident_id}/assessment-drafts/analyze",
            headers=ORIGIN,
            data={
                "reason": "new_admission",
                "assessment_date": "2026-08-31",
                "document_kinds": ["health_submission"],
            },
            files=[
                (
                    "files",
                    ("health.jpg", image_bytes.getvalue(), "image/jpeg"),
                )
            ],
        )
        assert created.status_code == 201, created.text
        draft = created.json()
        assert draft["current_revision"] == 1

        with open(FIXTURE_ROOT / "new_admission_transfer.pdf", "rb") as handle:
            rejected = client.post(
                (
                    f"/api/workdesk/residents/{resident_id}/new-admission-drafts/"
                    f"{draft['id']}/materials"
                ),
                headers=ORIGIN,
                data={"document_kinds": ["transfer_document"]},
                files=[
                    (
                        "files",
                        ("new_admission_transfer.pdf", handle, "application/pdf"),
                    )
                ],
            )

        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["detail"] == (
            "선택한 어르신과 제출 자료에 표시된 대상 코드가 다릅니다. "
            "어르신과 파일을 다시 확인해 주세요."
        )
        reopened = client.get(
            f"/api/workdesk/residents/{resident_id}/new-admission-drafts/{draft['id']}"
        )
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["current_revision"] == 1
        assert len(reopened.json()["revisions"]) == 1


def test_existing_draft_accepts_additional_transfer_and_consultation_as_new_revision():
    with TestClient(app) as client:
        _login_admin(client)
        resident_id = _create_test_resident()
        with open(FIXTURE_ROOT / "new_admission_certificate.pdf", "rb") as initial_file:
            created = client.post(
                f"/api/workdesk/residents/{resident_id}/assessment-drafts/analyze",
                headers=ORIGIN,
                data={
                    "reason": "new_admission",
                    "assessment_date": "2026-08-31",
                    "document_kinds": ["care_grade_certificate"],
                },
                files=[
                    (
                        "files",
                        (
                            "new_admission_certificate.pdf",
                            initial_file,
                            "application/pdf",
                        ),
                    )
                ],
            )
        assert created.status_code == 201, created.text
        draft = created.json()
        assert draft["current_revision"] == 1

        additional_names = [
            ("new_admission_consultation.pdf", "consultation_log"),
            ("new_admission_transfer.pdf", "transfer_document"),
        ]
        handles = [open(FIXTURE_ROOT / name, "rb") for name, _ in additional_names]
        try:
            appended = client.post(
                (
                    f"/api/workdesk/residents/{resident_id}/new-admission-drafts/"
                    f"{draft['id']}/materials"
                ),
                headers=ORIGIN,
                data={"document_kinds": [kind for _, kind in additional_names]},
                files=[
                    ("files", (name, handle, "application/pdf"))
                    for (name, _), handle in zip(
                        additional_names, handles, strict=True
                    )
                ],
            )
        finally:
            for handle in handles:
                handle.close()

        assert appended.status_code == 201, appended.text
        payload = appended.json()
        assert payload["current_revision"] == 2
        assert len(payload["revisions"]) == 2
        first_bundle = payload["revisions"][0]["bundle"]
        latest_bundle = payload["revisions"][1]["bundle"]
        assert len(first_bundle["material_receipts"]) == 1
        assert len(latest_bundle["material_receipts"]) == 3
        assert {item["document_kind"] for item in latest_bundle["material_receipts"]} == {
            "장기요양인정서",
            "상담일지",
            "전원 관련 서류",
        }
        assert _field(latest_bundle, "skin_state")["value"] == "천골 부위 발적 관찰되지 않음"
        assert _field(latest_bundle, "nutrition_state")["value"] == "일반식 2분의 1 섭취"
        fall_history = _field(latest_bundle, "fall_history")
        assert fall_history["status"] == "material_conflict"
        assert fall_history["value"] is None
        assert fall_history["conflict_values"] == [
            "최근 6개월 낙상 없음",
            "낙상 이력: 최근 6개월 낙상 1회",
        ]

        ledger = client.get(
            (
                f"/api/workdesk/residents/{resident_id}/new-admission-drafts/"
                f"{draft['id']}/evidence-ledger"
            )
        )
        assert ledger.status_code == 200, ledger.text
        ledger_items = ledger.json()["items"]
        assert {item["document_kind"] for item in ledger_items} == {
            "장기요양인정서",
            "상담일지",
            "전원 관련 서류",
        }
        consultation_entry = next(
            item for item in ledger_items if item["document_kind"] == "상담일지"
        )
        transfer_entry = next(
            item for item in ledger_items if item["document_kind"] == "전원 관련 서류"
        )
        consultation_context = {
            fact["field_key"]: fact["value"]
            for fact in consultation_entry["normalized_facts"]
            if fact["status"] == "source_context"
        }
        transfer_context = {
            fact["field_key"]: fact["value"]
            for fact in transfer_entry["normalized_facts"]
            if fact["status"] == "source_context"
        }
        assert consultation_entry["document_date"] == "2026-08-20"
        assert consultation_context == {
            "consultation_datetime": "2026-08-20 11:00",
            "consultation_method": "전화",
            "consultation_counterparty": "보호자",
            "consultation_reason": "입소 초기 돌봄 선호 확인",
            "consultation_content": "식사량과 수분 지원 내용을 확인함",
            "consultation_action": "확인 내용을 돌봄 직원에게 전달함",
            "consultation_result": "보호자가 내용을 확인함",
            "consultation_follow_up": "섭취 상태를 관찰하여 보호자에게 안내 예정",
        }
        assert transfer_entry["document_date"] == "2026-08-18"
        assert transfer_context == {
            "source_organization": "비식별 이전기관",
            "source_currentness": "현재 상태 재확인 필요",
        }
        assert transfer_entry["staff_review_required"] is True
        medication = _field(latest_bundle, "medication_information")
        assert medication["status"] == "current_observation_required"
        assert medication["value"] is None
        assert medication["evidence_refs"]

        reopened = client.get(
            (
                f"/api/workdesk/residents/{resident_id}/new-admission-drafts/"
                f"{draft['id']}"
            )
        )
        assert reopened.status_code == 200
        assert reopened.json() == payload


def test_long_reassessment_keeps_all_chat_evidence_but_uses_one_compact_receipt():
    content = (FIXTURE_ROOT / "existing_fall.pdf").read_bytes()
    material = extract_pdf_material(
        content,
        source_ref="material-001",
        document_kind="previous_fall_assessment",
    )
    linked = [
        {
            "reference_locator": f"합성 채팅 {index}",
            "summary": f"합성 현재 관찰 {index}",
            "text": f"이동 상태: 보행 시 부축 필요\n합성 관찰 번호: {index}",
        }
        for index in range(1, 49)
    ]

    bundle = build_assessment_draft_bundle(
        case_ref="assessment-20260831-long-review",
        reason="periodic_reassessment",
        assessment_date="2026-08-31",
        period_start="2026-06-01",
        period_end="2026-08-31",
        materials=[material],
        linked_evidence=linked,
    )

    assert len(bundle.evidence_sources) == 49
    assert len(bundle.material_receipts) == 2
    receipt = bundle.material_receipts[-1]
    assert receipt.status == "linked_existing_data"
    assert receipt.source_ref == "linked-001"
    assert receipt.display_label == "선택 기간 매실챗 확인 기록 48건"
    assert all(len(field.evidence_refs) <= 30 for field in bundle.fields)
    source_refs = {source.source_ref for source in bundle.evidence_sources}
    assert all(set(field.evidence_refs).issubset(source_refs) for field in bundle.fields)


def test_reassessment_keeps_previous_value_as_baseline_and_uses_current_chat_value():
    previous_material = extract_pdf_material(
        (FIXTURE_ROOT / "existing_fall.pdf").read_bytes(),
        source_ref="material-001",
        document_kind="previous_fall_assessment",
    )

    bundle = build_assessment_draft_bundle(
        case_ref="assessment-20260831-baseline-changed",
        reason="periodic_reassessment",
        assessment_date="2026-08-31",
        period_start="2026-07-01",
        period_end="2026-08-31",
        materials=[previous_material],
        linked_evidence=[
            {
                "reference_locator": "합성 채팅 1",
                "summary": "합성 현재 이동 관찰",
                "text": "이동 상태: 보행 시 부축 필요",
            }
        ],
    ).model_dump(mode="json")

    baseline = _baseline_field(bundle, "mobility_support_need")
    current = _field(bundle, "mobility_support_need")
    comparison = _comparison_item(bundle, "mobility_support_need")

    assert baseline["value"] == "실내 독립보행"
    assert baseline["status"] == "baseline_reference"
    assert current["value"] == "보행 시 부축 필요"
    assert current["status"] == "reusable"
    assert comparison["classification"] == "changed"
    assert comparison["previous_value"] == "실내 독립보행"
    assert comparison["current_value"] == "보행 시 부축 필요"


def test_reassessment_extracts_realistic_staff_wording_for_mobility_and_meal_change():
    previous_material = ParsedAssessmentMaterial(
        source_ref="material-realistic-previous",
        document_kind="previous_needs_assessment",
        mime_type="application/pdf",
        page_count=1,
        size_bytes=1,
        extracted_text=(
            "DEV 비공식 자료\n"
            # 표 형태 PDF는 항목명·값·근거가 콜론 없이 한 줄로 추출될 수 있다.
            "이동 상태 실내에서 지팡이를 사용해 독립보행함 이전 평가 관찰\n"
            "영양 상태 일반식 3/4 이상 섭취함 이전 식사 기록\n"
            "피부 상태 피부 손상과 발적이 관찰되지 않음 이전 평가 관찰\n"
            "인지 상태 일상 대화와 간단한 지시 이해가 가능함 이전 검사 관찰"
        ),
    )

    bundle = build_assessment_draft_bundle(
        case_ref="assessment-20260903-realistic-chat-phrasing",
        reason="periodic_reassessment",
        assessment_date="2026-09-03",
        period_start="2026-06-01",
        period_end="2026-09-02",
        materials=[previous_material],
        linked_evidence=[
            {
                "source_ref": "chat-realistic-mobility",
                "reference_locator": "합성 채팅 이동",
                "summary": "프로그램실 이동 지원",
                "text": "프로그램실 이동 시 다시 몸이 흔들려 한 명이 팔을 잡고 이동을 도왔습니다.",
            },
            {
                "source_ref": "chat-realistic-meal",
                "reference_locator": "합성 채팅 식사",
                "summary": "점심 식사량 확인",
                "text": "점심 일반식 1/2 섭취했고 저녁 상태를 계속 확인했습니다.",
            },
            {
                "source_ref": "chat-realistic-latest-summary",
                "reference_locator": "합성 채팅 최종 확인",
                "summary": "현재 상태를 항목별로 확인",
                "text": (
                        "어르0001 피부 상태: 손상과 발적이 관찰되지 않음.\n"
                    "어르0001 인지 상태: 간단한 의사소통 가능, 최근 일정은 반복 확인이 필요함.\n"
                    "어르0001 수분 상태: 물 200mL 제공 시 전량 섭취 확인. "
                    "배변 상태: 화장실 이용, 불편 호소 없음."
                ),
            },
        ],
    ).model_dump(mode="json")

    mobility = _field(bundle, "mobility_support_need")
    nutrition = _field(bundle, "nutrition_state")
    assert mobility["value"] == "보행 시 한 명 부축 필요"
    assert mobility["evidence_refs"] == ["chat-realistic-mobility"]
    assert "1/2" in nutrition["value"]
    assert nutrition["evidence_refs"] == ["chat-realistic-meal"]
    assert _baseline_field(bundle, "skin_state")["value"] == (
        "피부 손상과 발적이 관찰되지 않음"
    )
    assert "간단한 지시 이해" in _baseline_field(bundle, "cognitive_observation")["value"]
    assert _field(bundle, "skin_state")["value"] == (
        "피부 손상과 발적이 관찰되지 않음"
    )
    assert "최근 일정은 반복 확인" in _field(bundle, "cognitive_observation")["value"]
    assert "200mL" in _field(bundle, "hydration_support_goal")["value"]
    assert "화장실 이용" in _field(bundle, "elimination_state")["value"]
    assert _comparison_item(bundle, "mobility_support_need")["classification"] == "changed"
    assert _comparison_item(bundle, "nutrition_state")["classification"] == "changed"
    assert _comparison_item(bundle, "skin_state")["classification"] == "unchanged"


def test_reassessment_does_not_reuse_previous_only_value_as_current_fact():
    previous_material = extract_pdf_material(
        (FIXTURE_ROOT / "existing_fall.pdf").read_bytes(),
        source_ref="material-001",
        document_kind="previous_fall_assessment",
    )

    bundle = build_assessment_draft_bundle(
        case_ref="assessment-20260831-baseline-stale",
        reason="periodic_reassessment",
        assessment_date="2026-08-31",
        period_start="2026-07-01",
        period_end="2026-08-31",
        materials=[previous_material],
        linked_evidence=[],
    ).model_dump(mode="json")

    baseline = _baseline_field(bundle, "mobility_support_need")
    current = _field(bundle, "mobility_support_need")
    comparison = _comparison_item(bundle, "mobility_support_need")

    assert baseline["value"] == "실내 독립보행"
    assert current["value"] is None
    assert current["status"] == "current_observation_required"
    assert comparison["classification"] == "stale_current_observation_required"
    assert comparison["previous_value"] == "실내 독립보행"
    assert comparison["current_value"] is None


def test_existing_resident_reassessment_auto_links_isolated_deduplicated_chat_and_prefers_staff_approved_attachment_text():
    with TestClient(app) as client:
        _login_admin(client)
        resident_id = _create_test_resident(display_name="어르0001")
        _create_reassessment_chat_fixture(resident_id)
        with open(FIXTURE_ROOT / "existing_fall.pdf", "rb") as handle:
            created = client.post(
                f"/api/workdesk/residents/{resident_id}/assessment-drafts/analyze",
                headers=ORIGIN,
                data={
                    "reason": "periodic_reassessment",
                    "assessment_date": "2026-08-31",
                    "period_start": "2026-07-01",
                    "period_end": "2026-08-31",
                    "document_kinds": ["previous_fall_assessment"],
                },
                files=[
                    (
                        "files",
                        ("existing_fall.pdf", handle, "application/pdf"),
                    )
                ],
            )

    assert created.status_code == 201, created.text
    bundle = created.json()["revisions"][0]["bundle"]
    chat_sources = [
        source
        for source in bundle["evidence_sources"]
        if source["document_kind"] == "선택 기간 매실챗 확인 기록"
    ]
    assert len(chat_sources) == 2
    combined = "\n".join(source["evidence_summary"] for source in chat_sources)
    assert "14:00 물 200mL 제공함" in combined
    assert "16:00 전량 섭취 확인함" in combined
    assert "18:00 보행 시 부축이 필요함" in combined
    assert "최초 OCR 잘못된 문장" not in combined
    assert "직원 검토 중간 문장" not in combined
    assert "다른 어르신" not in combined
    assert "전체 직원 공지" not in combined
    assert bundle["material_receipts"][-1]["display_label"] == (
        "선택 기간 매실챗 확인 기록 2건"
    )


def test_new_admission_auto_links_chat_since_resident_registration_without_period_input():
    with TestClient(app) as client:
        _login_admin(client)
        resident_id = _create_test_resident()
        with SessionLocal() as db:
            resident = db.get(Resident, resident_id)
            assert resident is not None
            resident.created_at = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
            db.commit()
        _create_reassessment_chat_fixture(resident_id)

        with open(FIXTURE_ROOT / "new_admission_certificate.pdf", "rb") as handle:
            created = client.post(
                f"/api/workdesk/residents/{resident_id}/assessment-drafts/analyze",
                headers=ORIGIN,
                data={
                    "reason": "new_admission",
                    "assessment_date": "2026-08-31",
                    "document_kinds": ["care_grade_certificate"],
                },
                files=[
                    (
                        "files",
                        ("new_admission_certificate.pdf", handle, "application/pdf"),
                    )
                ],
            )

    assert created.status_code == 201, created.text
    bundle = created.json()["revisions"][0]["bundle"]
    chat_sources = [
        source
        for source in bundle["evidence_sources"]
        if source["document_kind"] == "선택 기간 매실챗 확인 기록"
    ]
    assert bundle["period_start"] == "2026-08-01"
    assert bundle["period_end"] == "2026-08-31"
    assert len(chat_sources) == 2
    combined = "\n".join(source["evidence_summary"] for source in chat_sources)
    assert "14:00 물 200mL 제공함" in combined
    assert "16:00 전량 섭취 확인함" in combined
    assert "18:00 보행 시 부축이 필요함" in combined
    assert "최초 OCR 잘못된 문장" not in combined
    assert "직원 검토 중간 문장" not in combined
    assert "다른 어르신" not in combined
    assert "전체 직원 공지" not in combined


def test_new_admission_without_chat_keeps_document_only_draft_available():
    with TestClient(app) as client:
        _login_admin(client)
        resident_id = _create_test_resident()
        with SessionLocal() as db:
            resident = db.get(Resident, resident_id)
            assert resident is not None
            resident.created_at = datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc)
            db.commit()

        with open(FIXTURE_ROOT / "new_admission_certificate.pdf", "rb") as handle:
            created = client.post(
                f"/api/workdesk/residents/{resident_id}/assessment-drafts/analyze",
                headers=ORIGIN,
                data={
                    "reason": "new_admission",
                    "assessment_date": "2026-08-31",
                    "document_kinds": ["care_grade_certificate"],
                },
                files=[
                    (
                        "files",
                        ("new_admission_certificate.pdf", handle, "application/pdf"),
                    )
                ],
            )

    assert created.status_code == 201, created.text
    bundle = created.json()["revisions"][0]["bundle"]
    assert bundle["period_start"] == "2026-08-10"
    assert bundle["period_end"] == "2026-08-31"
    assert not any(
        source["document_kind"] == "선택 기간 매실챗 확인 기록"
        for source in bundle["evidence_sources"]
    )
    assert not any(
        receipt["document_kind"] == "선택 기간 매실챗 확인 기록"
        for receipt in bundle["material_receipts"]
    )


def test_staff_can_preview_and_exclude_one_chat_event_before_assessment_draft_save():
    with TestClient(app) as client:
        _login_admin(client)
        resident_id = _create_test_resident(display_name="어르0001")
        _create_reassessment_chat_fixture(resident_id)

        preview = client.get(
            f"/api/workdesk/residents/{resident_id}/assessment-chat-evidence/preview",
            headers=ORIGIN,
            params={
                "reason": "periodic_reassessment",
                "assessment_date": "2026-08-31",
                "period_start": "2026-07-01",
                "period_end": "2026-08-31",
            },
        )

        assert preview.status_code == 200, preview.text
        preview_payload = preview.json()
        assert preview_payload["period_start"] == "2026-07-01"
        assert preview_payload["period_end"] == "2026-08-31"
        assert len(preview_payload["items"]) == 2
        excluded = next(
            item
            for item in preview_payload["items"]
            if "14:00 물 200mL 제공함" in item["summary"]
        )

        with open(FIXTURE_ROOT / "existing_fall.pdf", "rb") as handle:
            created = client.post(
                f"/api/workdesk/residents/{resident_id}/assessment-drafts/analyze",
                headers=ORIGIN,
                data={
                    "reason": "periodic_reassessment",
                    "assessment_date": "2026-08-31",
                    "period_start": "2026-07-01",
                    "period_end": "2026-08-31",
                    "document_kinds": ["previous_fall_assessment"],
                    "excluded_chat_source_refs": [excluded["source_ref"]],
                },
                files=[
                    (
                        "files",
                        ("existing_fall.pdf", handle, "application/pdf"),
                    )
                ],
            )

    assert created.status_code == 201, created.text
    bundle = created.json()["revisions"][0]["bundle"]
    chat_sources = [
        source
        for source in bundle["evidence_sources"]
        if source["document_kind"] == "선택 기간 매실챗 확인 기록"
    ]
    assert len(chat_sources) == 1
    combined = "\n".join(source["evidence_summary"] for source in chat_sources)
    assert "14:00 물 200mL 제공함" not in combined
    assert "16:00 전량 섭취 확인함" not in combined
    assert "18:00 보행 시 부축이 필요함" in combined


def test_staff_review_reason_is_accepted_and_preserved_in_the_saved_draft():
    with TestClient(app) as client:
        _login_admin(client)
        resident_id = _create_test_resident(display_name="어르0001")
        with open(FIXTURE_ROOT / "existing_fall.pdf", "rb") as handle:
            created = client.post(
                f"/api/workdesk/residents/{resident_id}/assessment-drafts/analyze",
                headers=ORIGIN,
                data={
                    "reason": "staff_review",
                    "assessment_date": "2026-08-31",
                    "period_start": "2026-07-01",
                    "period_end": "2026-08-31",
                    "document_kinds": ["previous_fall_assessment"],
                },
                files=[
                    (
                        "files",
                        ("existing_fall.pdf", handle, "application/pdf"),
                    )
                ],
            )

        listed = client.get(
            f"/api/workdesk/residents/{resident_id}/new-admission-drafts",
            headers=ORIGIN,
        )

    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["revisions"][0]["bundle"]["reason"] == "staff_review"
    assert listed.status_code == 200, listed.text
    assert listed.json()["items"][0]["reason"] == "staff_review"


def _image_only_pdf() -> bytes:
    pages = []
    for label in ("scan-one", "scan-two"):
        image = Image.new("RGB", (900, 1200), "white")
        ImageDraw.Draw(image).text((80, 80), label, fill="black")
        pages.append(image)
    output = BytesIO()
    pages[0].save(output, format="PDF", save_all=True, append_images=pages[1:])
    return output.getvalue()


def test_image_only_combined_pdf_is_locally_read_and_split_into_detected_documents(
    monkeypatch,
):
    main_module = importlib.import_module("app.main")
    ocr_results = iter(
        [
            "장기요양인정서\n장기요양등급: 3등급",
            "개인별장기요양이용계획서\n이동 상태: 보행 시 부축 필요",
        ]
    )
    monkeypatch.setattr(
        main_module,
        "extract_assessment_document_text",
        lambda *_args, **_kwargs: next(ocr_results),
    )
    with TestClient(app) as client:
        _login_admin(client)
        resident_id = _create_test_resident()
        response = client.post(
            f"/api/workdesk/residents/{resident_id}/assessment-drafts/analyze",
            headers=ORIGIN,
            data={
                "reason": "new_admission",
                "assessment_date": "2026-08-31",
                "document_kinds": ["auto"],
            },
            files=[
                (
                    "files",
                    ("combined-scan.pdf", _image_only_pdf(), "application/pdf"),
                )
            ],
        )
    assert response.status_code == 201, response.text
    bundle = response.json()["revisions"][0]["bundle"]
    assert [item["document_kind"] for item in bundle["material_receipts"]] == [
        "장기요양인정서",
        "개인별장기요양이용계획서",
    ]
    assert [item["page_count"] for item in bundle["material_receipts"]] == [1, 1]
    assert _field(bundle, "long_term_care_grade")["value"] == "3등급"
    assert _field(bundle, "mobility_support_need")["value"] == "보행 시 부축 필요"


def test_document_kind_auto_and_manual_multiple_selection_contract():
    assert parse_document_kind_selection("auto", reason="new_admission") is None
    assert parse_document_kind_selection(
        "care_grade_certificate,individual_long_term_care_plan",
        reason="new_admission",
    ) == ("care_grade_certificate", "individual_long_term_care_plan")


def test_resident0002_reassessment_baseline_pdfs_are_all_classified_by_actual_titles():
    baseline_root = (
        TWO_CYCLE_FIXTURE_ROOT
        / "어르0002"
        / "02_initial_staff_confirmed_baseline"
    )
    expected = {
        "initial_fall_assessment.pdf": ("previous_fall_assessment",),
        "initial_pressure_ulcer_assessment.pdf": (
            "previous_pressure_ulcer_assessment",
        ),
        "initial_cognitive_assessment.pdf": (
            "previous_cognitive_assessment",
        ),
        "initial_needs_assessment.pdf": ("previous_needs_assessment",),
        "initial_care_plan.pdf": ("previous_care_plan",),
        "initial_care_plan_evaluation.pdf": (
            "previous_care_plan_evaluation",
        ),
    }

    actual = {}
    for file_name in expected:
        _, page_texts = extract_pdf_page_texts(
            (baseline_root / file_name).read_bytes()
        )
        actual[file_name] = classify_document_kinds(
            "\n".join(page_texts),
            reason="periodic_reassessment",
        )

    assert actual == expected


def test_resident0002_multpage_previous_care_plan_keeps_continuation_pages_together():
    care_plan_path = (
        TWO_CYCLE_FIXTURE_ROOT
        / "어르0002"
        / "02_initial_staff_confirmed_baseline"
        / "initial_care_plan.pdf"
    )
    page_count, page_texts = extract_pdf_page_texts(care_plan_path.read_bytes())

    materials = build_parsed_materials(
        page_texts=page_texts,
        source_prefix="previous-plan",
        selected_kinds=None,
        reason="periodic_reassessment",
        mime_type="application/pdf",
        size_bytes=care_plan_path.stat().st_size,
    )

    assert page_count > 1
    assert [material.document_kind for material in materials] == [
        "previous_care_plan"
    ]
    assert materials[0].page_numbers == tuple(range(1, page_count + 1))
    assert classify_document_kinds(
        "장기요양인정서와 개인별장기요양이용계획서",
        reason="new_admission",
    ) == ("care_grade_certificate", "individual_long_term_care_plan")


def test_page_classification_separates_submitted_forms_from_guides_and_supporting_documents():
    page_texts = [
        "장기요양인정서\n인정번호: 합성-001\n장기요양등급: 3등급\n유효기간: 2026.01.01~2027.12.31",
        "개인별장기요양이용계획서\n작성일: 2026.08.30\n급여 종류: 시설급여\n필요 영역: 이동 지원",
        "복약안내문\n약품명: 합성정 10mg\n복용방법: 1일 2회",
        "건강검진 결과서\n검사일: 2026.08.29\n검사항목: 혈압\n결과: 직원 확인 필요",
        "진료의뢰서\n의뢰일: 2026.08.28\n진료기관 간 전달사항: 비식별 합성 내용",
        "복지용구 급여확인서\n개인별장기요양이용계획서 참고\n품목: 합성 보행 보조도구",
        "초기 상담일지\n상담 일시: 2026.08.30\n상담 대상 관계: 보호자\n상담 결과: 확인함",
        "개인별장기요양이용계획서 작성 안내\n본 서식은 관련 법령과 발급 절차를 안내합니다.",
    ]

    materials = build_parsed_materials(
        page_texts=page_texts,
        source_prefix="material-01",
        selected_kinds=None,
        reason="new_admission",
        mime_type="application/pdf",
        size_bytes=1000,
    )

    assert [material.document_kind for material in materials] == [
        "care_grade_certificate",
        "individual_long_term_care_plan",
        "prescription",
        "health_submission",
        "transfer_document",
        "welfare_equipment",
        "consultation_log",
        "administrative_guide",
    ]
    assert [material.page_numbers for material in materials] == [
        (1,),
        (2,),
        (3,),
        (4,),
        (5,),
        (6,),
        (7,),
        (8,),
    ]


def test_medical_opinion_page_is_classified_as_transfer_supporting_document():
    assert classify_document_kinds(
        (
            "소견서\n"
            "병명: 합성 질환\n"
            "입원기록: 합성 관찰 기록\n"
            "향후치료의견: 직원 확인 필요"
        ),
        reason="new_admission",
    ) == ("transfer_document",)


def test_atomic_fact_extraction_never_uses_the_first_500_characters_as_a_field_value():
    materials = [
        ParsedAssessmentMaterial(
            source_ref="material-01",
            document_kind="welfare_equipment",
            mime_type="application/pdf",
            page_count=1,
            size_bytes=100,
            extracted_text=(
                "복지용구 급여확인서\n"
                "욕창 예방 방석은 복지용구 품목 안내입니다. "
                + "대상 상태가 아닌 공통 안내 문장입니다. " * 60
            ),
            page_numbers=(1,),
        ),
        ParsedAssessmentMaterial(
            source_ref="material-02",
            document_kind="prescription",
            mime_type="application/pdf",
            page_count=1,
            size_bytes=100,
            extracted_text="복약안내문\n약품명: 합성정 10mg\n복용방법: 1일 2회",
            page_numbers=(1,),
        ),
        ParsedAssessmentMaterial(
            source_ref="material-03",
            document_kind="health_submission",
            mime_type="application/pdf",
            page_count=1,
            size_bytes=100,
            extracted_text="건강검진 결과서\n피부 상태: 발적 없음",
            page_numbers=(1,),
        ),
    ]

    bundle = build_assessment_draft_bundle(
        materials,
        case_ref="atomic-facts-001",
        reason="new_admission",
        assessment_date="2026-08-31",
        period_start=None,
        period_end=None,
    ).model_dump(mode="json")

    assert _field(bundle, "skin_state")["value"] == "발적 없음"
    assert _field(bundle, "medication_information")["value"] == "합성정 10mg"
    assert all(
        not isinstance(field["value"], str) or len(field["value"]) < 200
        for field in bundle["fields"]
    )
    assert "복지용구 품목 안내" not in _field(bundle, "skin_state")["value"]


def test_submitted_documents_map_atomic_facts_into_the_five_form_contracts():
    materials = [
        ParsedAssessmentMaterial(
            source_ref="material-01",
            document_kind="care_grade_certificate",
            mime_type="application/pdf",
            page_count=1,
            size_bytes=100,
            extracted_text=(
                "장기요양인정서\n"
                "장기요양등급: 합성 3등급\n"
                "적용기간: 2026.01.01~2027.12.31"
            ),
            page_numbers=(1,),
        ),
        ParsedAssessmentMaterial(
            source_ref="material-02",
            document_kind="individual_long_term_care_plan",
            mime_type="application/pdf",
            page_count=1,
            size_bytes=100,
            extracted_text=(
                "개인별장기요양이용계획서\n"
                "급여 종류: 시설급여\n"
                "이동 상태: 보행 시 부축 필요\n"
                "의사소통 상태: 간단한 의사표현 가능\n"
                "급여 목표: 안전한 이동 지원"
            ),
            page_numbers=(1,),
        ),
        ParsedAssessmentMaterial(
            source_ref="material-03",
            document_kind="prescription",
            mime_type="application/pdf",
            page_count=1,
            size_bytes=100,
            extracted_text=(
                "처방전\n"
                "약품명: 합성정 10mg\n"
                "복용방법: 1일 2회\n"
                "복용기간: 7일"
            ),
            page_numbers=(1,),
        ),
        ParsedAssessmentMaterial(
            source_ref="material-04",
            document_kind="health_submission",
            mime_type="application/pdf",
            page_count=1,
            size_bytes=100,
            extracted_text=(
                "건강진단서\n"
                "피부 상태: 발적 없음\n"
                "구강 상태: 부분 의치 사용\n"
                "일상생활 수행: 세면 시 일부 도움\n"
                "의사 소견: 합성 관찰 소견"
            ),
            page_numbers=(1,),
        ),
    ]

    bundle = build_assessment_draft_bundle(
        materials,
        case_ref="atomic-form-map-001",
        reason="new_admission",
        assessment_date="2026-08-31",
        period_start=None,
        period_end=None,
    ).model_dump(mode="json")

    assert _field(bundle, "benefit_effective_period")["value"] == "2026.01.01~2027.12.31"
    assert _field(bundle, "benefit_type")["value"] == "시설급여"
    assert _field(bundle, "medication_name")["value"] == "합성정 10mg"
    assert _field(bundle, "medication_method")["value"] == "1일 2회"
    assert _field(bundle, "medication_period")["value"] == "7일"
    assert _field(bundle, "oral_state")["value"] == "부분 의치 사용"
    assert _field(bundle, "daily_living_state")["value"] == "세면 시 일부 도움"
    assert _field(bundle, "communication_state")["value"] == "간단한 의사표현 가능"
    assert _field(bundle, "medical_diagnosis")["value"] == "합성 관찰 소견"

    form_workspace = build_new_admission_form_workspace(bundle)
    assert len(form_workspace["documents"]) == 4
    assert form_workspace["care_plan_gate"]["available"] is False
    pressure_ulcer = next(
        document
        for document in form_workspace["documents"]
        if document["document_type"] == "pressure_ulcer_risk_assessment"
    )
    pressure_rows = {
        row["row_key"].rsplit(".", 1)[-1]: row
        for section in pressure_ulcer["sections"]
        for row in section["rows"]
    }
    assert pressure_rows["moisture"]["value"] == ""
    assert pressure_rows["friction_shear"]["value"] == ""
    assert pressure_rows["note"]["value"] == "발적 없음"

    reviewed_at = datetime.now(timezone.utc).isoformat()
    bundle["document_reviews"] = {
        document_type: {
            "state": "reviewed",
            "reviewed_by_id": "synthetic-staff",
            "reviewed_by_name": "합성 직원",
            "reviewed_at": reviewed_at,
        }
        for document_type in (
            "fall_risk_assessment",
            "pressure_ulcer_risk_assessment",
            "cognitive_function_assessment",
            "needs_assessment",
        )
    }
    form_workspace = build_new_admission_form_workspace(bundle)
    assert len(form_workspace["documents"]) == 5
    assert form_workspace["care_plan_gate"]["available"] is True
    needs = next(
        document
        for document in form_workspace["documents"]
        if document["document_type"] == "needs_assessment"
    )
    care_plan = next(
        document
        for document in form_workspace["documents"]
        if document["document_type"] == "long_term_care_service_plan"
    )
    needs_text = "\n".join(
        row["value"]
        for section in needs["sections"]
        for row in section["rows"]
    )
    care_plan_text = "\n".join(
        row["value"]
        for section in care_plan["sections"]
        for row in section["rows"]
    )
    assert "부분 의치 사용" in needs_text
    assert "세면 시 일부 도움" in needs_text
    assert "합성정 10mg" in needs_text
    assert "1일 2회" in needs_text
    assert "7일" in needs_text
    assert "안전한 이동 지원" in care_plan_text
    assert all(
        len(value) < 200
        for text in (needs_text, care_plan_text)
        for value in text.splitlines()
    )


def test_realistic_ocr_layout_keeps_only_bounded_source_backed_facts():
    materials = [
        ParsedAssessmentMaterial(
            source_ref="material-opinion",
            document_kind="transfer_document",
            mime_type="application/pdf",
            page_count=1,
            size_bytes=100,
            extracted_text=(
                "소견서\n"
                "병명: 합성 치매\n"
                "G000 합성 신경통\n"
                "I000 합성 고혈압\n"
                "입원기록: 거동에 문제없고 목욕도 혼자하고 식사도 혼자 잘 드실정도.\n"
                "어제 새벽 화장실에서 낙상 후 허리통증 호소.\n"
                "주간보호센터 이용중."
            ),
            page_numbers=(2,),
        ),
        ParsedAssessmentMaterial(
            source_ref="material-plan",
            document_kind="individual_long_term_care_plan",
            mime_type="application/pdf",
            page_count=1,
            size_bytes=100,
            extracted_text=(
                "개인별장기요양이용계획서\n"
                "장기요양등급: 4등급\n"
                "유효기간: 2025.03.24 ~ 2029.03.23\n"
                "항목명: 수급자 희망급여 → 보이는 값: 노인요양시설\n"
                "장기요양목표: 개인위생관리 잔존기능 향상\n"
                "장기요양필요내용: 입을 옷 준비하기, 옷갈아입기 도움\n"
                "장기요양목표: 정확한 복약으로 증상 완화\n"
                "장기요양필요내용: 정확한 복약도움(시간, 용량, 용법 등)\n"
                "장기요양목표: 신경·근골격계 후유장애 회복 및 보완\n"
                "장기요양필요내용: 신체기능의 훈련, 일상생활동작 훈련"
            ),
            page_numbers=(4,),
        ),
        ParsedAssessmentMaterial(
            source_ref="material-rx",
            document_kind="prescription",
            mime_type="application/pdf",
            page_count=1,
            size_bytes=100,
            extracted_text=(
                "복약안내문\n"
                "합성가정10mg(합성성분)\n"
                "3일 2회, 1회 1정씩, 7일간\n"
                "아침, 저녁 식후 30분\n"
                "합성나캡슐(합성성분)\n"
                "3일 1회, 1회 1캡슐씩, 5일간\n"
                "하루1회, 자기전에"
            ),
            page_numbers=(1,),
        ),
    ]

    bundle = build_assessment_draft_bundle(
        materials,
        case_ref="realistic-ocr-layout-001",
        reason="new_admission",
        assessment_date="2026-08-31",
        period_start=None,
        period_end=None,
    ).model_dump(mode="json")

    assert _field(bundle, "long_term_care_grade")["value"] == "4등급"
    assert _field(bundle, "benefit_effective_period")["value"] == "2025.03.24 ~ 2029.03.23"
    assert _field(bundle, "benefit_type")["value"] == "노인요양시설"
    assert "거동에 문제없" in _field(bundle, "mobility_support_need")["value"]
    assert "낙상 후 허리통증" in _field(bundle, "fall_history")["value"]
    assert "식사도 혼자" in _field(bundle, "nutrition_state")["value"]
    assert "주간보호센터" in _field(bundle, "resource_use")["value"]
    assert "합성 치매" in _field(bundle, "medical_diagnosis")["value"]
    assert "합성가정10mg" in _field(bundle, "medication_name")["value"]
    assert "1회 1정씩" in _field(bundle, "medication_method")["value"]
    assert "7일간" in _field(bundle, "medication_period")["value"]
    assert "개인위생관리 잔존기능 향상" in _field(bundle, "service_plan_goal")["value"]
    assert "신체기능의 훈련" in _field(bundle, "rehabilitation_need")["value"]
    assert all(
        not isinstance(field["value"], str) or len(field["value"]) < 500
        for field in bundle["fields"]
    )

    workspace = build_new_admission_form_workspace(bundle)
    fall = next(
        document
        for document in workspace["documents"]
        if document["document_type"] == "fall_risk_assessment"
    )
    pressure = next(
        document
        for document in workspace["documents"]
        if document["document_type"] == "pressure_ulcer_risk_assessment"
    )
    assert fall["sections"][0]["rows"][0]["value"] == "4등급"
    assert pressure["sections"][0]["rows"][0]["value"] == "4등급"
    assert workspace["safety"]["restricted_auto_generation_count"] == 0


def test_long_transfer_narrative_is_reduced_to_form_ready_atomic_facts():
    materials = [
        ParsedAssessmentMaterial(
            source_ref="material-opinion-long",
            document_kind="transfer_document",
            mime_type="application/pdf",
            page_count=1,
            size_bytes=100,
            extracted_text=(
                "소견서\n"
                "입원기록: 보호자 설명과 과거 진료 경과가 길게 이어지는 문장입니다. "
                "거동에 문제없고 목욕도 혼자하며 식사도 혼자 잘 드십니다. "
                "이 문장은 양식 항목과 관계없는 행정 안내입니다.\n"
                "향후치료의견: 전일 화장실에서 낙상 후 허리 통증을 호소했습니다. "
                "주간보호센터 이용 중이며 피부 발적은 관찰되지 않았습니다. "
                "추가 행정 설명과 보호자 연락 경위는 양식 항목에 복사하지 않습니다."
            ),
            page_numbers=(1,),
        )
    ]

    bundle = build_assessment_draft_bundle(
        materials,
        case_ref="long-transfer-atomic-001",
        reason="new_admission",
        assessment_date="2026-08-31",
        period_start=None,
        period_end=None,
    ).model_dump(mode="json")

    expected_fragments = {
        "mobility_support_need": "거동에 문제없",
        "fall_history": "낙상 후 허리 통증",
        "nutrition_state": "식사도 혼자",
        "resource_use": "주간보호센터 이용 중",
        "skin_state": "피부 발적은 관찰되지 않았",
    }
    for field_key, fragment in expected_fragments.items():
        value = _field(bundle, field_key)["value"]
        assert fragment in value
        assert len(value) <= 180
        assert "행정 안내" not in value
        assert "보호자 연락 경위" not in value


def test_prescription_metadata_never_contaminates_medication_fields():
    materials = [
        ParsedAssessmentMaterial(
            source_ref="material-rx-metadata",
            document_kind="prescription",
            mime_type="application/pdf",
            page_count=1,
            size_bytes=100,
            extracted_text=(
                "처방전\n"
                "약품명: 합성가정10mg(합성성분) [효능] 합성 효능 안내 [앞] 흰색 [뒤] 표시 없음\n"
                "1일 2회, 1회 1정씩, 7일간\n"
                "[효능] 합성 효능을 설명하는 긴 안내 문장 [앞] 흰색 [뒤] 표시 없음\n"
                "약품명: 합성나캡슐(합성성분)\n"
                "하루 1회, 1회 1캡슐씩, 5일간"
            ),
            page_numbers=(1,),
        )
    ]

    bundle = build_assessment_draft_bundle(
        materials,
        case_ref="prescription-atomic-001",
        reason="new_admission",
        assessment_date="2026-08-31",
        period_start=None,
        period_end=None,
    ).model_dump(mode="json")

    medication_name = _field(bundle, "medication_name")["value"]
    medication_method = _field(bundle, "medication_method")["value"]
    medication_information = _field(bundle, "medication_information")["value"]
    assert "합성가정10mg" in medication_name
    assert "합성나캡슐" in medication_name
    assert "1회 1정씩" in medication_method
    assert "1회 1캡슐씩" in medication_method
    for value in (medication_name, medication_method, medication_information):
        assert len(value) <= 240
        assert "[효능]" not in value
        assert "[앞]" not in value
        assert "[뒤]" not in value

    workspace = build_new_admission_form_workspace(bundle)
    needs = next(
        document
        for document in workspace["documents"]
        if document["document_type"] == "needs_assessment"
    )
    medication_row = next(
        row
        for section in needs["sections"]
        for row in section["rows"]
        if row["row_key"] == "needs_assessment.medication"
    )
    assert medication_row["value"].count("합성가정10mg") == 1
    assert medication_row["value"].count("1회 1정씩") == 1


def test_unpunctuated_transfer_ocr_is_split_into_field_specific_atomic_facts():
    materials = [
        ParsedAssessmentMaterial(
            source_ref="material-transfer-unpunctuated",
            document_kind="transfer_document",
            mime_type="application/pdf",
            page_count=1,
            size_bytes=100,
            extracted_text=(
                "소견서\n"
                "입원기록: (거동에 문제없고 목욕도 혼자하고 식사도 혼자 잘 드실정도) "
                "가족 집에 머물며 주간보호센터 이용중 "
                "7월19일경 시작된 좌측가슴 상부의 발진 및 통증으로 "
                "7월22일 집근처 개인의원에서 대상포진 진단받음"
            ),
            page_numbers=(1,),
        )
    ]

    bundle = build_assessment_draft_bundle(
        materials,
        case_ref="transfer-unpunctuated-atomic-001",
        reason="new_admission",
        assessment_date="2026-08-31",
        period_start=None,
        period_end=None,
    ).model_dump(mode="json")

    mobility = _field(bundle, "mobility_support_need")["value"]
    nutrition = _field(bundle, "nutrition_state")["value"]
    resource = _field(bundle, "resource_use")["value"]
    skin = _field(bundle, "skin_state")["value"]

    assert "거동에 문제없" in mobility
    assert "식사도 혼자" in nutrition
    assert "주간보호센터 이용중" in resource
    assert "발진 및 통증" in skin
    assert "주간보호센터" not in mobility
    assert "발진" not in mobility
    assert "대상포진" not in mobility
    assert "거동" not in nutrition
    assert "발진" not in resource
    assert "거동" not in skin
    for value in (mobility, nutrition, resource, skin):
        assert len(value) <= 80


def test_prescription_department_and_pill_appearance_are_not_medication_facts():
    materials = [
        ParsedAssessmentMaterial(
            source_ref="material-rx-realistic-metadata",
            document_kind="prescription",
            mime_type="application/pdf",
            page_count=1,
            size_bytes=100,
            extracted_text=(
                "처방전\n"
                "진료과: 가정의학과\n"
                "합성가정10mg(합성성분)\n"
                "인 장방형 정제\n"
                "박하향이 있는 백색의 원형 정제\n"
                "1일 2회, 1회 1정씩, 7일간\n"
                "식전식후 관계없이 복용 가능하지만 충분한 물과 함께 복용하세요"
            ),
            page_numbers=(1,),
        )
    ]

    bundle = build_assessment_draft_bundle(
        materials,
        case_ref="prescription-realistic-metadata-001",
        reason="new_admission",
        assessment_date="2026-08-31",
        period_start=None,
        period_end=None,
    ).model_dump(mode="json")

    medication_name = _field(bundle, "medication_name")["value"]
    medication_method = _field(bundle, "medication_method")["value"]

    assert "합성가정10mg" in medication_name
    assert "진료과" not in medication_name
    assert "가정의학과" not in medication_name
    assert "장방형 정제" not in medication_name
    assert "백색의 원형 정제" not in medication_name
    assert medication_method == "1일 2회, 1회 1정씩, 7일간"
    assert "식전식후 관계없이" not in medication_method


def test_consultation_log_separates_event_context_from_form_ready_facts():
    material = ParsedAssessmentMaterial(
        source_ref="material-consultation-detail",
        document_kind="consultation_log",
        mime_type="application/pdf",
        page_count=1,
        size_bytes=100,
        extracted_text=(
            "상담일지\n"
            "상담 일시: 2026-08-20 11:00\n"
            "상담 방법: 전화\n"
            "상담 대상: 보호자\n"
            "보호자 관계: 딸\n"
            "상담 사유: 입소 초기 돌봄 선호 확인\n"
            "상담 내용: 식사량과 수분 지원 내용을 확인함\n"
            "식사 상태: 일반식 2분의 1 섭취\n"
            "보호자 요청: 식사 사이 물 200mL 제공 후 섭취 상태 확인\n"
            "상담 조치: 요청 내용을 돌봄 직원에게 전달함\n"
            "상담 결과: 보호자가 내용을 확인함\n"
            "추후 계획: 섭취 상태를 관찰하여 보호자에게 안내 예정"
        ),
        page_numbers=(1,),
    )

    bundle = build_assessment_draft_bundle(
        [material],
        case_ref="consultation-detail-001",
        reason="new_admission",
        assessment_date="2026-08-31",
        period_start=None,
        period_end=None,
    )
    payload = bundle.model_dump(mode="json")
    assert _field(payload, "subjective_need")["value"] == (
        "식사 사이 물 200mL 제공 후 섭취 상태 확인"
    )
    assert _field(payload, "family_environment")["value"] == "딸"
    assert _field(payload, "nutrition_state")["value"] == "일반식 2분의 1 섭취"

    entry = build_assessment_evidence_entries([material], bundle)[0]
    assert entry["document_date"].isoformat() == "2026-08-20"
    context = {
        fact["field_key"]: fact["value"]
        for fact in entry["normalized_facts"]
        if fact["status"] == "source_context"
    }
    assert context == {
        "consultation_datetime": "2026-08-20 11:00",
        "consultation_method": "전화",
        "consultation_counterparty": "보호자",
        "consultation_reason": "입소 초기 돌봄 선호 확인",
        "consultation_content": "식사량과 수분 지원 내용을 확인함",
        "consultation_action": "요청 내용을 돌봄 직원에게 전달함",
        "consultation_result": "보호자가 내용을 확인함",
        "consultation_follow_up": "섭취 상태를 관찰하여 보호자에게 안내 예정",
    }
    assert all(len(value) <= 180 for value in context.values())


def test_transfer_document_requiring_current_recheck_does_not_confirm_past_state():
    material = ParsedAssessmentMaterial(
        source_ref="material-transfer-stale",
        document_kind="transfer_document",
        mime_type="application/pdf",
        page_count=1,
        size_bytes=100,
        extracted_text=(
            "전원기록지\n"
            "전원일: 2026-08-18\n"
            "작성기관: 비식별 이전기관\n"
            "현재 상태 재확인 필요: 예\n"
            "이동 상태: 실내 보행 시 부축 필요\n"
            "식사 상태: 일반식 2분의 1 섭취"
        ),
        page_numbers=(1,),
    )

    bundle = build_assessment_draft_bundle(
        [material],
        case_ref="transfer-currentness-001",
        reason="new_admission",
        assessment_date="2026-09-01",
        period_start=None,
        period_end=None,
    )
    payload = bundle.model_dump(mode="json")
    for field_key in ("mobility_support_need", "nutrition_state"):
        field = _field(payload, field_key)
        assert field["status"] == "current_observation_required"
        assert field["value"] is None
        assert field["evidence_refs"] == ["material-transfer-stale"]

    entry = build_assessment_evidence_entries([material], bundle)[0]
    assert entry["document_date"].isoformat() == "2026-08-18"
    context = {
        fact["field_key"]: fact["value"]
        for fact in entry["normalized_facts"]
        if fact["status"] == "source_context"
    }
    assert context == {
        "source_organization": "비식별 이전기관",
        "source_currentness": "현재 상태 재확인 필요",
    }
    assert entry["staff_review_required"] is True


def test_additional_stale_transfer_keeps_candidate_evidence_in_new_revision():
    base_material = ParsedAssessmentMaterial(
        source_ref="material-certificate-base",
        document_kind="care_grade_certificate",
        mime_type="application/pdf",
        page_count=1,
        size_bytes=100,
        extracted_text="장기요양인정서\n장기요양등급: 4등급",
        page_numbers=(1,),
    )
    base = build_assessment_draft_bundle(
        [base_material],
        case_ref="transfer-additional-currentness-001",
        reason="new_admission",
        assessment_date="2026-09-01",
        period_start=None,
        period_end=None,
    )
    material = ParsedAssessmentMaterial(
        source_ref="material-transfer-additional-stale",
        document_kind="transfer_document",
        mime_type="application/pdf",
        page_count=1,
        size_bytes=100,
        extracted_text=(
            "전원기록지\n"
            "기준일: 2026-08-18\n"
            "작성기관: 비식별 이전기관\n"
            "현재 상태 재확인 필요: 예\n"
            "이동 상태: 실내 보행 시 부축 필요"
        ),
        page_numbers=(1,),
    )

    merged = merge_assessment_materials_into_bundle(base, [material]).model_dump(
        mode="json"
    )
    mobility = _field(merged, "mobility_support_need")
    assert merged["revision"] == 2
    assert merged["supersedes_revision"] == 1
    assert mobility["status"] == "current_observation_required"
    assert mobility["value"] is None
    assert mobility["evidence_refs"] == ["material-transfer-additional-stale"]
