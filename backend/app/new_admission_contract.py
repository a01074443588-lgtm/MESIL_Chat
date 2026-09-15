from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


NewAdmissionSourceType = Literal[
    "public_insurer_data",
    "medical_or_submitted_document",
    "consultation",
    "staff_confirmation",
    "current_observation",
]
NewAdmissionFieldStatus = Literal[
    "confirmed",
    "reusable",
    "derivable",
    "current_observation_required",
    "user_decision_required",
    "material_conflict",
    "admin_missing",
]
NewAdmissionDraftStatus = Literal["draft", "needs_confirmation"]
NewAdmissionDocumentType = Literal[
    "cognitive_function_assessment",
    "fall_risk_assessment",
    "pressure_ulcer_risk_assessment",
    "needs_assessment",
    "long_term_care_service_plan",
]
NewAdmissionValueKind = Literal[
    "fact",
    "administrative",
    "assessment_score",
    "diagnosis",
    "service_frequency",
    "service_duration",
    "signature",
]
NewAdmissionProcessingScope = Literal["internal_only", "deidentified_dev"]
NewAdmissionOrganizationRole = Literal[
    "public_insurer",
    "authoring_organization",
    "reference_organization",
    "not_applicable",
]
NewAdmissionVerificationState = Literal["verified", "needs_review"]
AssessmentDraftReason = Literal[
    "new_admission",
    "periodic_reassessment",
    "state_change_reassessment",
    "staff_review",
]
AssessmentMaterialStatus = Literal[
    "submitted",
    "linked_existing_data",
    "not_submitted",
    "staff_review_required",
]
AssessmentBaselineStatus = Literal["baseline_reference", "material_conflict"]
AssessmentChangeClassification = Literal[
    "unchanged",
    "changed",
    "newly_confirmed",
    "stale_current_observation_required",
    "material_conflict",
    "basis_version_mismatch",
    "expert_review_required",
    "care_plan_change_candidate",
]
AssessmentDocumentReviewState = Literal["pending", "reviewed"]
AssessmentFormReviewState = Literal[
    "",
    "confirmed",
    "not_applicable",
    "not_confirmed",
    "follow_up",
]

ASSESSMENT_FORM_REVIEW_STATES: frozenset[str] = frozenset(
    {"", "confirmed", "not_applicable", "not_confirmed", "follow_up"}
)

ASSESSMENT_DOCUMENT_TYPES: tuple[NewAdmissionDocumentType, ...] = (
    "fall_risk_assessment",
    "pressure_ulcer_risk_assessment",
    "cognitive_function_assessment",
    "needs_assessment",
)


NEW_ADMISSION_DOCUMENT_TYPES: tuple[NewAdmissionDocumentType, ...] = (
    "cognitive_function_assessment",
    "fall_risk_assessment",
    "pressure_ulcer_risk_assessment",
    "needs_assessment",
    "long_term_care_service_plan",
)

_RESOLVED_STATUSES = {"confirmed", "reusable", "derivable"}
_HUMAN_VERIFICATION_STATUSES = {
    "current_observation_required",
    "user_decision_required",
    "material_conflict",
}
_RESTRICTED_VALUE_KINDS = {
    "assessment_score",
    "diagnosis",
    "service_frequency",
    "service_duration",
}


def _strip_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


class NewAdmissionEvidenceSource(BaseModel):
    source_ref: str = Field(
        pattern=r"^[a-z0-9][a-z0-9._-]{2,79}$",
        min_length=3,
        max_length=80,
    )
    source_type: NewAdmissionSourceType
    document_kind: str = Field(min_length=1, max_length=120)
    organization_role: NewAdmissionOrganizationRole
    reference_locator: str = Field(min_length=1, max_length=200)
    evidence_summary: str = Field(min_length=1, max_length=1000)
    verification_state: NewAdmissionVerificationState = "verified"
    contains_sensitive_data: bool = True

    @field_validator(
        "document_kind",
        "reference_locator",
        "evidence_summary",
    )
    @classmethod
    def strip_source_text(cls, value: str) -> str:
        return value.strip()


class NewAdmissionField(BaseModel):
    field_key: str = Field(
        pattern=r"^[a-z][a-z0-9_]{2,79}$",
        min_length=3,
        max_length=80,
    )
    label: str = Field(min_length=1, max_length=120)
    document_types: list[NewAdmissionDocumentType] = Field(
        min_length=1,
        max_length=5,
    )
    status: NewAdmissionFieldStatus
    value_kind: NewAdmissionValueKind = "fact"
    value: str | int | float | bool | None = None
    evidence_refs: list[str] = Field(default_factory=list, max_length=30)
    derivation_note: str | None = Field(default=None, max_length=500)
    conflict_values: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("label")
    @classmethod
    def strip_label(cls, value: str) -> str:
        return value.strip()

    @field_validator("document_types", "evidence_refs", "conflict_values")
    @classmethod
    def strip_field_lists(cls, values: list[str]) -> list[str]:
        return _strip_unique(values)

    @field_validator("derivation_note")
    @classmethod
    def strip_derivation_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def enforce_evidence_and_human_boundaries(self):
        has_value = self.value is not None and self.value != ""

        if self.status in _RESOLVED_STATUSES:
            if not has_value:
                raise ValueError("resolved fields require an explicit value")
            if not self.evidence_refs:
                raise ValueError("resolved fields require evidence_refs")
        elif has_value:
            raise ValueError("unresolved fields must not select or invent a value")

        if self.status == "derivable" and not self.derivation_note:
            raise ValueError("derivable fields require a derivation_note")

        if self.status == "material_conflict":
            if len(self.evidence_refs) < 2:
                raise ValueError("material_conflict requires at least two evidence_refs")
            if len(self.conflict_values) < 2:
                raise ValueError("material_conflict requires at least two conflict_values")
        elif self.conflict_values:
            raise ValueError("conflict_values are only allowed for material_conflict")

        if self.value_kind == "signature" and has_value:
            raise ValueError("draft contracts must never auto-fill a signature")

        if self.value_kind in _RESTRICTED_VALUE_KINDS and self.status == "derivable":
            raise ValueError(
                "scores, diagnoses, service frequency and duration cannot be derived "
                "before their authoritative rules or source values are verified"
            )
        return self


class AssessmentBaselineField(BaseModel):
    """A prior assessment/plan value kept only as a comparison baseline."""

    field_key: str = Field(
        pattern=r"^[a-z][a-z0-9_]{2,79}$",
        min_length=3,
        max_length=80,
    )
    label: str = Field(min_length=1, max_length=120)
    value_kind: NewAdmissionValueKind = "fact"
    status: AssessmentBaselineStatus = "baseline_reference"
    value: str | int | float | bool | None = None
    evidence_refs: list[str] = Field(default_factory=list, max_length=30)
    conflict_values: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("label")
    @classmethod
    def strip_baseline_label(cls, value: str) -> str:
        return value.strip()

    @field_validator("evidence_refs", "conflict_values")
    @classmethod
    def strip_baseline_lists(cls, values: list[str]) -> list[str]:
        return _strip_unique(values)

    @model_validator(mode="after")
    def enforce_baseline_contract(self):
        has_value = self.value not in (None, "")
        if not self.evidence_refs:
            raise ValueError("기존 비교 기준에는 근거가 필요합니다.")
        if self.status == "baseline_reference":
            if not has_value or self.conflict_values:
                raise ValueError("단일 기존 비교 기준에는 하나의 값만 필요합니다.")
        else:
            if has_value or len(self.conflict_values) < 2 or len(self.evidence_refs) < 2:
                raise ValueError("충돌한 기존 비교 기준에는 둘 이상의 값과 근거가 필요합니다.")
        if self.value_kind == "signature" and has_value:
            raise ValueError("기존 서명도 새 초안에 자동 입력할 수 없습니다.")
        return self


class AssessmentFieldComparison(BaseModel):
    field_key: str = Field(
        pattern=r"^[a-z][a-z0-9_]{2,79}$",
        min_length=3,
        max_length=80,
    )
    label: str = Field(min_length=1, max_length=120)
    classification: AssessmentChangeClassification
    previous_value: str | int | float | bool | None = None
    current_value: str | int | float | bool | None = None
    baseline_evidence_refs: list[str] = Field(default_factory=list, max_length=30)
    current_evidence_refs: list[str] = Field(default_factory=list, max_length=30)
    conflict_values: list[str] = Field(default_factory=list, max_length=10)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("label", "reason")
    @classmethod
    def strip_comparison_text(cls, value: str) -> str:
        return value.strip()

    @field_validator(
        "baseline_evidence_refs",
        "current_evidence_refs",
        "conflict_values",
    )
    @classmethod
    def strip_comparison_lists(cls, values: list[str]) -> list[str]:
        return _strip_unique(values)


class AssessmentDocumentReview(BaseModel):
    """Server-authenticated review state for one of the four assessments."""

    state: AssessmentDocumentReviewState = "pending"
    reviewed_by_id: str | None = Field(default=None, max_length=80)
    reviewed_by_name: str | None = Field(default=None, max_length=120)
    reviewed_at: str | None = Field(default=None, max_length=40)

    @field_validator("reviewed_by_id", "reviewed_by_name", "reviewed_at")
    @classmethod
    def strip_review_metadata(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def enforce_review_metadata(self):
        metadata = (self.reviewed_by_id, self.reviewed_by_name, self.reviewed_at)
        if self.state == "reviewed" and not all(metadata):
            # Old fixtures and safely imported bundles may only carry the state.
            # The API replaces it with authenticated reviewer metadata on save.
            if any(metadata):
                raise ValueError("reviewed assessment metadata must be complete")
        elif self.state == "pending" and any(metadata):
            raise ValueError("pending assessment must not carry reviewer metadata")
        return self


class NewAdmissionDraftDocument(BaseModel):
    document_type: NewAdmissionDocumentType
    status: NewAdmissionDraftStatus
    field_keys: list[str] = Field(min_length=1, max_length=200)
    missing_fields: list[str] = Field(default_factory=list, max_length=100)
    human_verification_fields: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("field_keys", "missing_fields", "human_verification_fields")
    @classmethod
    def strip_draft_lists(cls, values: list[str]) -> list[str]:
        return _strip_unique(values)


class AssessmentMaterialReceipt(BaseModel):
    source_ref: str = Field(
        pattern=r"^[a-z0-9][a-z0-9._-]{2,79}$",
        min_length=3,
        max_length=80,
    )
    display_label: str = Field(min_length=1, max_length=80)
    document_kind: str = Field(min_length=1, max_length=120)
    status: AssessmentMaterialStatus
    mime_type: Literal["application/pdf", "image/jpeg", "image/png"] = "application/pdf"
    size_bytes: int = Field(ge=1, le=30 * 1024 * 1024)
    page_count: int = Field(ge=1, le=100)

    @field_validator("display_label", "document_kind")
    @classmethod
    def strip_material_text(cls, value: str) -> str:
        return value.strip()


class NewAdmissionDraftBundle(BaseModel):
    schema_version: Literal["new_admission_draft_v1"] = "new_admission_draft_v1"
    case_ref: str = Field(
        pattern=r"^[a-z0-9][a-z0-9._-]{2,79}$",
        min_length=3,
        max_length=80,
    )
    processing_scope: NewAdmissionProcessingScope
    external_transfer_allowed: Literal[False] = False
    revision: int = Field(ge=1)
    supersedes_revision: int | None = Field(default=None, ge=1)
    reason: AssessmentDraftReason = "new_admission"
    assessment_date: str | None = Field(default=None, max_length=10)
    period_start: str | None = Field(default=None, max_length=10)
    period_end: str | None = Field(default=None, max_length=10)
    material_receipts: list[AssessmentMaterialReceipt] = Field(
        default_factory=list,
        max_length=30,
    )
    document_texts: dict[NewAdmissionDocumentType, str] = Field(
        default_factory=dict,
    )
    form_values: dict[NewAdmissionDocumentType, dict[str, str]] = Field(
        default_factory=dict,
    )
    document_reviews: dict[NewAdmissionDocumentType, AssessmentDocumentReview] = Field(
        default_factory=dict,
    )
    evidence_sources: list[NewAdmissionEvidenceSource] = Field(
        min_length=1,
        max_length=100,
    )
    fields: list[NewAdmissionField] = Field(min_length=1, max_length=500)
    baseline_fields: list[AssessmentBaselineField] = Field(
        default_factory=list,
        max_length=500,
    )
    comparison_items: list[AssessmentFieldComparison] = Field(
        default_factory=list,
        max_length=500,
    )
    selected_change_field_keys: list[str] = Field(
        default_factory=list,
        max_length=500,
    )
    drafts: list[NewAdmissionDraftDocument] = Field(min_length=5, max_length=5)

    @field_validator("selected_change_field_keys")
    @classmethod
    def strip_selected_change_field_keys(cls, values: list[str]) -> list[str]:
        return _strip_unique(values)

    @model_validator(mode="after")
    def enforce_bundle_contract(self):
        if (
            self.supersedes_revision is not None
            and self.supersedes_revision >= self.revision
        ):
            raise ValueError("supersedes_revision must be lower than revision")

        source_by_ref = {
            source.source_ref: source for source in self.evidence_sources
        }
        if len(source_by_ref) != len(self.evidence_sources):
            raise ValueError("evidence source_ref values must be unique")

        material_refs = [item.source_ref for item in self.material_receipts]
        if len(material_refs) != len(set(material_refs)):
            raise ValueError("material receipt source_ref values must be unique")
        if set(material_refs) - source_by_ref.keys():
            raise ValueError("material receipts must reference saved evidence sources")

        unknown_document_texts = set(self.document_texts) - set(
            NEW_ADMISSION_DOCUMENT_TYPES
        )
        if unknown_document_texts:
            raise ValueError("document_texts contains an unknown document type")
        if any(not text.strip() for text in self.document_texts.values()):
            raise ValueError("saved document text must not be blank")

        unknown_form_documents = set(self.form_values) - set(
            NEW_ADMISSION_DOCUMENT_TYPES
        )
        if unknown_form_documents:
            raise ValueError("form_values contains an unknown document type")
        for document_type, values in self.form_values.items():
            # The actual long-term-care service plan contains 50 rows across
            # eight visible columns.  Each row also keeps its plain value and
            # review state, plus a small number of header/opinion fields.
            # Keep a finite safety bound while allowing one complete form to
            # be preserved in a single append-only revision.
            if len(values) > 600:
                raise ValueError(f"too many saved form values for {document_type}")
            for key, value in values.items():
                if not key.strip() or len(key) > 180 or len(value) > 4000:
                    raise ValueError("saved form value is outside the safe size limit")
                if (
                    key.endswith("::review_state")
                    and value not in ASSESSMENT_FORM_REVIEW_STATES
                ):
                    raise ValueError("saved form review state is not supported")

        unknown_review_documents = set(self.document_reviews) - set(
            ASSESSMENT_DOCUMENT_TYPES
        )
        if unknown_review_documents:
            raise ValueError("only the four assessments can be marked reviewed")

        if self.processing_scope == "deidentified_dev" and any(
            source.contains_sensitive_data for source in self.evidence_sources
        ):
            raise ValueError(
                "deidentified_dev cannot contain a sensitive evidence source"
            )

        field_by_key = {field.field_key: field for field in self.fields}
        if len(field_by_key) != len(self.fields):
            raise ValueError("field_key values must be unique")

        for field in self.fields:
            unknown_refs = set(field.evidence_refs) - source_by_ref.keys()
            if unknown_refs:
                raise ValueError(
                    f"field {field.field_key} has unknown evidence_refs: "
                    f"{sorted(unknown_refs)}"
                )

        baseline_by_key = {field.field_key: field for field in self.baseline_fields}
        if len(baseline_by_key) != len(self.baseline_fields):
            raise ValueError("기존 비교 기준 입력 항목 이름은 중복될 수 없습니다.")
        for field in self.baseline_fields:
            unknown_refs = set(field.evidence_refs) - source_by_ref.keys()
            if unknown_refs:
                raise ValueError(
                    f"baseline field {field.field_key} has unknown evidence_refs: "
                    f"{sorted(unknown_refs)}"
                )

        comparison_by_key = {
            item.field_key: item for item in self.comparison_items
        }
        if len(comparison_by_key) != len(self.comparison_items):
            raise ValueError("변화 비교 입력 항목 이름은 중복될 수 없습니다.")
        for item in self.comparison_items:
            unknown_refs = (
                set(item.baseline_evidence_refs)
                | set(item.current_evidence_refs)
            ) - source_by_ref.keys()
            if unknown_refs:
                raise ValueError(
                    f"comparison {item.field_key} has unknown evidence_refs: "
                    f"{sorted(unknown_refs)}"
                )

        unknown_selected_changes = set(self.selected_change_field_keys) - set(
            comparison_by_key
        )
        if unknown_selected_changes:
            raise ValueError("존재하지 않는 변화 항목은 반영 대상으로 선택할 수 없습니다.")
        if any(
            comparison_by_key[field_key].classification == "material_conflict"
            for field_key in self.selected_change_field_keys
        ):
            raise ValueError("충돌 항목은 반영 대상으로 선택할 수 없습니다.")

        draft_by_type = {draft.document_type: draft for draft in self.drafts}
        if len(draft_by_type) != len(self.drafts):
            raise ValueError("document_type values must be unique")
        if set(draft_by_type) != set(NEW_ADMISSION_DOCUMENT_TYPES):
            raise ValueError(
                "drafts must contain the four basic assessments and care plan"
            )

        for document_type, draft in draft_by_type.items():
            expected_field_keys = {
                field.field_key
                for field in self.fields
                if document_type in field.document_types
            }
            if set(draft.field_keys) != expected_field_keys:
                raise ValueError(
                    f"draft {document_type} field_keys do not match field mappings"
                )

            expected_missing = {
                field.field_key
                for field in self.fields
                if document_type in field.document_types
                and field.status == "admin_missing"
            }
            expected_human = {
                field.field_key
                for field in self.fields
                if document_type in field.document_types
                and field.status in _HUMAN_VERIFICATION_STATUSES
            }
            if set(draft.missing_fields) != expected_missing:
                raise ValueError(
                    f"draft {document_type} missing_fields do not match field states"
                )
            if set(draft.human_verification_fields) != expected_human:
                raise ValueError(
                    f"draft {document_type} human_verification_fields do not match "
                    "field states"
                )

            has_pending = bool(expected_missing or expected_human)
            expected_status = "needs_confirmation" if has_pending else "draft"
            if draft.status != expected_status:
                raise ValueError(
                    f"draft {document_type} must use status {expected_status}"
                )
        return self
