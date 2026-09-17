export type UnitType =
  | "business"
  | "department"
  | "floor"
  | "team";

export type OrgUnit = {
  id: string;
  parent_unit_id: string | null;
  parent_unit_name: string | null;
  unit_type: UnitType;
  name: string;
  code: string | null;
  is_active: boolean;
  is_test_data: boolean;
  active_staff_count: number;
  active_room_count: number;
  active_resident_count: number;
  active_participant_count: number;
  system_room_count: number;
  system_room_id: string | null;
  reference_count: number;
  can_delete: boolean;
};

export type JobCode = {
  code: string;
  name: string;
  sort_order: number;
  is_active: boolean;
  active_staff_count: number;
  active_room_count: number;
  reference_count: number;
  can_delete: boolean;
};

export type PositionTitle = {
  id: string;
  name: string;
  sort_order: number;
  is_active: boolean;
  active_staff_count: number;
  reference_count: number;
  can_delete: boolean;
};

export type User = {
  id: string;
  username: string;
  full_name: string;
  role: "admin" | "staff";
  can_process_records: boolean;
  employment_status: "active" | "leave" | "retired";
  must_change_password: boolean;
  employee_code: string | null;
  business: OrgUnit | null;
  department: OrgUnit | null;
  job_code: string | null;
  job_name: string | null;
  position_title: string | null;
  floor: OrgUnit | null;
  team: OrgUnit | null;
  terminated_at: string | null;
  is_dev_launcher: boolean;
  is_dev_impersonated: boolean;
  is_reviewer_session: boolean;
  reviewer_experience:
    | "care"
    | "social_worker"
    | "realtime_secondary"
    | "mentor_full"
    | null;
};

export type StaffServiceAssignment = {
  id: string;
  service_type: "facility" | "daycare" | "homecare";
  source_account_id: string;
  service_period_id: string;
  employment_status: "active" | "leave" | "retired" | string;
  business: OrgUnit | null;
  department: OrgUnit | null;
  floor: OrgUnit | null;
  team: OrgUnit | null;
  job_code: string | null;
  job_name: string | null;
  job_title_snapshot: string | null;
  position_code_id: string | null;
  position_title_snapshot: string | null;
  start_date: string;
  end_date: string | null;
  assignment_basis: "carefor_auto" | "admin_confirmed" | "legacy_manual";
  is_current: boolean;
};

export type StaffDirectoryEntry = {
  staff_id: string;
  login_user_id: string | null;
  display_name: string;
  internal_code: string;
  employment_status: "active" | "leave" | "retired";
  is_test_data: boolean;
  login_status: "not_issued" | "enabled" | "disabled";
  username: string | null;
  role: "admin" | "staff" | null;
  can_process_records: boolean;
  must_change_password: boolean;
  terminated_at: string | null;
  legacy_assignment: {
    business: OrgUnit | null;
    department: OrgUnit | null;
    floor: OrgUnit | null;
    team: OrgUnit | null;
    job_code: string | null;
    job_name: string | null;
    position_title: string | null;
  };
  service_assignments: StaffServiceAssignment[];
};

export type Room = {
  id: string;
  name: string;
  kind:
    | "all"
    | "business"
    | "department"
    | "job"
    | "floor"
    | "team"
    | "custom"
    | "self"
    | "ai"
    | "living_space";
  unread_count: number;
  last_message: string | null;
  last_message_at: string | null;
};

export type ActiveStaffDirectoryEntry = {
  id: string;
  staff_id: string;
  full_name: string;
  job_name: string | null;
  position_title: string | null;
  business_name?: string | null;
  department_name?: string | null;
  floor_name?: string | null;
  team_name?: string | null;
};

export type StaffRoomMember = ActiveStaffDirectoryEntry;

export type StaffRoom = {
  id: string;
  name: string;
  is_active: boolean;
  owner_staff_id: string;
  owner_name: string;
  member_ids: string[];
  members: StaffRoomMember[];
  is_owner: boolean;
  can_invite: boolean;
  can_manage: boolean;
  created_at: string;
};

export type ManagedCustomRoom = {
  id: string;
  name: string;
  is_active: boolean;
  member_ids: string[];
  created_at: string;
};

export type ManagedRoom = {
  id: string;
  name: string;
  kind: Room["kind"];
  is_active: boolean;
  scope_unit_id: string | null;
  scope_name: string | null;
  job_code: string | null;
  job_name: string | null;
  member_ids: string[];
  member_count: number;
  resident_scope: "all" | "facility" | "daycare" | "homecare" | "floor";
  resident_scope_unit_id: string | null;
  resident_scope_name: string | null;
  message_count: number;
  attachment_count: number;
  owner_staff_id: string | null;
  owner_name: string | null;
  created_by_name: string | null;
  last_message_at: string | null;
  created_at: string;
};

export type ActionAssignee = {
  id: string;
  full_name: string;
  job_code: string | null;
  job_name: string | null;
  business: OrgUnit | null;
  department: OrgUnit | null;
  floor: OrgUnit | null;
  team: OrgUnit | null;
  can_process_records: boolean;
  is_room_member: boolean;
};

export type ActionItem = {
  id: string;
  source_message_id: string;
  room_id: string;
  room_name: string;
  source_body: string;
  sender_name: string;
  resident_name: string | null;
  comment_count: number;
  action_type: "handover" | "cooperation" | "confirmation";
  assignee_user_id: string | null;
  assignee_user_name: string | null;
  assignee_unit_id: string | null;
  assignee_unit_name: string | null;
  priority: "normal" | "important" | "urgent";
  status: "assigned" | "acknowledged" | "in_progress" | "completed";
  due_at: string | null;
  created_by_id: string;
  created_by_name: string;
  acknowledged_at: string | null;
  completed_at: string | null;
  created_at: string;
};

export type Message = {
  id: string;
  room_id: string;
  sender_id: string;
  sender_name: string;
  message_type:
    | "chat"
    | "notice"
    | "handover"
    | "work_request"
    | "report"
    | "system";
  body: string;
  is_recalled: boolean;
  recalled_at: string | null;
  resident: Resident | null;
  resident_links: MessageResidentLink[];
  resident_ref: string | null;
  attachments: Attachment[];
  comment_count: number;
  unread_comment_count: number;
  latest_comment: MessageComment | null;
  read_count: number;
  reply_user_count: number;
  action_item: ActionItem | null;
  forwarded_from: {
    message_id: string;
    room_name: string;
    sender_name: string;
    created_at: string;
  } | null;
  reply_to: {
    message_id: string;
    sender_name: string;
    body: string;
    created_at: string;
  } | null;
  ai_help: {
    role: "user" | "assistant";
    status: "queued" | "preparing" | "extracting" | "reasoning" | "validating" | "completed" | "failed" | "cancelled";
    turn_id: string | null;
    source_message_id: string | null;
    question_type: "attachment_guidance" | "general_guidance" | "record_search" | "clarification" | null;
    provider: string | null;
    model: string | null;
    processing_location: "rules" | "local" | "internal" | "external" | "unconfigured" | null;
    external_transmission: boolean;
    evidence: { source_no?: number; source_type?: "record" | "attachment"; attachment_id?: string; statement?: string }[];
    error_message: string | null;
  } | null;
  created_at: string;
};

export type MessageResidentLink = {
  automatically_linked?: boolean;
  resident: Resident;
  source: "manual" | "text_exact" | "ocr_exact" | "audio_transcript";
  status: "candidate" | "confirmed" | "rejected";
  reviewed_at: string | null;
  evidence?: { source: string; attachment_id?: string; attempt_number?: number | null }[];
};

export type Resident = {
  id: string;
  display_name: string;
  service_type: "facility" | "daycare" | "homecare" | string;
  floor: OrgUnit | null;
  room_name: string | null;
  sort_order: number;
  is_priority: boolean;
  roster_source: "carefor" | "smcodi" | "manual" | "demo";
  is_test_data: boolean;
};

export type CarePlanningDocumentType =
  | "cognitive_function_assessment"
  | "fall_risk_assessment"
  | "pressure_ulcer_risk_assessment"
  | "needs_assessment"
  | "long_term_care_service_plan";

export type ResidentCarePlanningState = {
  id: string | null;
  resident_id: string;
  resident_name: string;
  document_type: CarePlanningDocumentType;
  assessment_date: string | null;
  valid_until: string | null;
  next_due_date: string | null;
  cycle_months: number;
  reassess_on_state_change: boolean;
  state_change_triggered: boolean;
  change_reason: string | null;
  status: "not_started" | "draft" | "needs_review" | "confirmed";
  evidence_refs: string[];
  known_facts: string[];
  questions_required: string[];
  professional_review_fields: string[];
  author_confirmed: boolean;
  author_verified_by_name: string | null;
  author_verified_at: string | null;
  version: number;
  is_candidate: boolean;
  candidate_reasons: string[];
  updated_at: string | null;
};

export type ResidentCarePlanningProfile = {
  resident: Resident;
  as_of: string;
  operating_cycle_notice: string;
  legal_standard_notice: string;
  items: ResidentCarePlanningState[];
};

export type ResidentAssessmentReason =
  | "new_admission"
  | "periodic_reassessment"
  | "state_change"
  | "care_plan_change"
  | "staff_review";

export type ResidentAssessmentClassification =
  | "unchanged"
  | "changed"
  | "newly_confirmed"
  | "stale_current_observation_required"
  | "material_conflict"
  | "basis_version_mismatch"
  | "expert_review_required"
  | "care_plan_change_candidate";

export type ResidentAssessmentBaselineSource = {
  source_ref: string;
  document_type: CarePlanningDocumentType;
  assessment_date: string;
  period_start: string | null;
  period_end: string | null;
  source_label: string;
  organization_role: "authoring_organization" | "reference_organization";
  original_verified_by_staff: boolean;
  current_reconfirmation_required: boolean;
  form_version: string | null;
  facts: Array<{
    field_key: string;
    label: string;
    value_kind: string;
    value: string | number | boolean | null;
    evidence_refs: string[];
    staff_confirmed: boolean;
  }>;
};

export type ResidentAssessmentCurrentFact = {
  field_key: string;
  label: string;
  value_kind: string;
  value: string | number | boolean | null;
  state:
    | "confirmed"
    | "current_observation_required"
    | "material_conflict"
    | "expert_review_required";
  source_type:
    | "mesil_chat_confirmed"
    | "staff_confirmation"
    | "current_observation";
  observed_at: string | null;
  evidence_refs: string[];
  conflict_values: string[];
  basis_version: string | null;
  plan_impact: string | null;
  staff_confirmed: boolean;
};

export type ResidentAssessmentComparisonItem = {
  field_key: string;
  label: string;
  classification: ResidentAssessmentClassification;
  previous_value: unknown;
  current_value: unknown;
  conflict_values: string[];
  evidence_refs: string[];
  reason: string;
  plan_impact: string | null;
  staff_selection_allowed: boolean;
  selected_for_plan_review: boolean;
};

export type ResidentAssessmentCycleRevision = {
  id: string;
  revision: number;
  supersedes_revision: number | null;
  status: "draft" | "needs_confirmation";
  baseline_sources: ResidentAssessmentBaselineSource[];
  current_facts: ResidentAssessmentCurrentFact[];
  comparison_items: ResidentAssessmentComparisonItem[];
  selected_plan_candidate_keys: string[];
  confirmation_questions: Array<{
    field_key: string;
    label: string;
    classification: ResidentAssessmentClassification;
    prompt: string;
    evidence_refs: string[];
  }>;
  field_changes: Array<Record<string, unknown>>;
  created_by_name: string;
  created_at: string;
};

export type ResidentAssessmentCycle = {
  id: string;
  resident_id: string;
  resident_name: string;
  reason: ResidentAssessmentReason;
  cycle_number: number;
  assessment_date: string;
  period_start: string | null;
  period_end: string | null;
  source_provider: "staff_manual" | "carefor_readonly";
  previous_cycle_id: string | null;
  current_revision: number;
  status: "draft" | "needs_confirmation";
  external_transfer_allowed: false;
  is_test_data: boolean;
  created_by_name: string;
  created_at: string;
  updated_at: string;
  revisions: ResidentAssessmentCycleRevision[];
};

export type ResidentAssessmentCycleList = {
  resident_id: string;
  resident_name: string;
  source_provider_notice: string;
  operating_cycle_notice: string;
  items: Array<{
    id: string;
    reason: ResidentAssessmentReason;
    cycle_number: number;
    assessment_date: string;
    current_revision: number;
    status: "draft" | "needs_confirmation";
    previous_cycle_id: string | null;
    updated_at: string;
  }>;
};

export type NewAdmissionDraftSummary = {
  id: string;
  case_ref: string;
  current_revision: number;
  status: "draft" | "needs_confirmation";
  reason:
    | "new_admission"
    | "periodic_reassessment"
    | "state_change_reassessment"
    | "staff_review";
  assessment_date: string | null;
  reviewed_assessment_count: number;
  updated_at: string;
};

export type NewAdmissionDraftList = {
  resident_id: string;
  resident_name: string;
  items: NewAdmissionDraftSummary[];
};

export type NewAdmissionDraftBundle = {
  schema_version: "new_admission_draft_v1";
  case_ref: string;
  processing_scope: "internal_only" | "deidentified_dev";
  external_transfer_allowed: false;
  revision: number;
  supersedes_revision: number | null;
  reason:
    | "new_admission"
    | "periodic_reassessment"
    | "state_change_reassessment"
    | "staff_review";
  assessment_date: string | null;
  period_start: string | null;
  period_end: string | null;
  material_receipts: NewAdmissionFormWorkspace["materials"];
  document_texts: Partial<Record<CarePlanningDocumentType, string>>;
  form_values: Partial<Record<CarePlanningDocumentType, Record<string, string>>>;
  document_reviews: Partial<
    Record<
      Exclude<CarePlanningDocumentType, "long_term_care_service_plan">,
      {
        state: "pending" | "reviewed";
        reviewed_by_id: string | null;
        reviewed_by_name: string | null;
        reviewed_at: string | null;
      }
    >
  >;
  evidence_sources: Array<Record<string, unknown>>;
  fields: Array<Record<string, unknown> & { field_key: string }>;
  baseline_fields: Array<{
    field_key: string;
    label: string;
    value_kind: string;
    status: "baseline_reference" | "material_conflict";
    value: string | number | boolean | null;
    evidence_refs: string[];
    conflict_values: string[];
  }>;
  comparison_items: Array<{
    field_key: string;
    label: string;
    classification:
      | "unchanged"
      | "changed"
      | "newly_confirmed"
      | "stale_current_observation_required"
      | "material_conflict"
      | "basis_version_mismatch"
      | "expert_review_required"
      | "care_plan_change_candidate";
    previous_value: string | number | boolean | null;
    current_value: string | number | boolean | null;
    baseline_evidence_refs: string[];
    current_evidence_refs: string[];
    conflict_values: string[];
    reason: string;
  }>;
  selected_change_field_keys: string[];
  drafts: Array<Record<string, unknown>>;
};

export type NewAdmissionDraftRecord = {
  id: string;
  resident_id: string;
  resident_name: string;
  case_ref: string;
  processing_scope: "internal_only" | "deidentified_dev";
  external_transfer_allowed: false;
  current_revision: number;
  status: "draft" | "needs_confirmation";
  is_test_data: boolean;
  created_by_name: string;
  created_at: string;
  updated_at: string;
  revisions: Array<{
    id: string;
    revision: number;
    supersedes_revision: number | null;
    status: "draft" | "needs_confirmation";
    bundle: NewAdmissionDraftBundle;
    field_evidence_refs: Record<string, string[]>;
    field_changes: Array<Record<string, unknown>>;
    field_confirmations: Record<
      string,
      {
        state: "confirmed" | "needs_confirmation";
        confirmed_by_id: string | null;
        confirmed_by_name: string | null;
        confirmed_at: string | null;
      }
    >;
    created_by_name: string;
    created_at: string;
  }>;
};

export type AssessmentEvidenceSummary = {
  draft_id: string;
  resident_id: string;
  case_ref: string;
  file_intake_complete: boolean;
  status: "draft" | "needs_confirmation";
  counts: {
    confirmed_facts: number;
    conflicts: number;
    missing_items: number;
  };
  confirmed_facts: Array<{
    field_key: string;
    label: string;
    value: string | number | boolean;
    status: "confirmed" | "reusable" | "derivable";
    document_types: string[];
    evidence_refs: string[];
  }>;
  conflicts: Array<{
    field_key: string;
    label: string;
    conflict_values: string[];
    selected_value: null;
    document_types: string[];
    evidence_refs: string[];
  }>;
  missing_items: Array<{
    field_key: string;
    label: string;
    value: null;
    reason:
      | "evidence_missing"
      | "current_observation_required"
      | "staff_decision_required";
    document_types: string[];
    evidence_refs: string[];
  }>;
  external_transfer_allowed: false;
};

export type NewAdmissionQuestionSource = {
  source_ref: string;
  source_type: string;
  document_kind: string;
  organization_role: string;
  verification_state: string;
  usage: string;
  usage_label: string;
};

export type NewAdmissionReusableFact = {
  field_key: string;
  label: string;
  status: "confirmed" | "reusable" | "derivable";
  status_label: string;
  value_kind: string;
  value: string | number | boolean | null;
  evidence_refs: string[];
  source_usages: string[];
  source_usage_labels: string[];
  document_types: CarePlanningDocumentType[];
  derivation_note: string | null;
  staff_confirmation_state: "confirmed" | "needs_confirmation";
};

export type NewAdmissionStaffQuestion = {
  question_key: string;
  field_key: string;
  label: string;
  question_type:
    | "material_conflict"
    | "current_observation_required"
    | "user_decision_required"
    | "admin_missing";
  question_type_label: string;
  prompt: string;
  reason: string;
  value_kind: string;
  evidence_refs: string[];
  source_usages: string[];
  source_usage_labels: string[];
  document_types: CarePlanningDocumentType[];
  conflict_values: string[];
  selected_value: null;
};

export type NewAdmissionAssessmentStep = {
  step_number: number;
  step_count: 4;
  document_type:
    | "fall_risk_assessment"
    | "pressure_ulcer_risk_assessment"
    | "cognitive_function_assessment"
    | "needs_assessment";
  title: string;
  local_form_alias: string;
  official_basis_state:
    | "official_reference_found_latest_adoption_required"
    | "latest_official_source_required";
  official_basis_label: string;
  official_basis_notice: string;
  output_status: "draft" | "needs_confirmation";
  reusable_facts: NewAdmissionReusableFact[];
  new_questions: NewAdmissionStaffQuestion[];
  carried_pending_questions: NewAdmissionStaffQuestion[];
  reusable_fact_count: number;
  new_question_count: number;
  carried_pending_count: number;
  score_generation_allowed: false;
  score: null;
  risk_grade: null;
  diagnosis: null;
  blocked_result_notice: string;
};

export type NewAdmissionAssessmentWorkflow = {
  schema_version: "new_admission_assessment_flow_v1";
  case_ref: string;
  revision: number;
  output_status: "draft" | "needs_confirmation";
  allowed_output_statuses: Array<"draft" | "needs_confirmation">;
  processing_scope: "internal_only" | "deidentified_dev";
  external_transfer_allowed: false;
  workflow_notice: string;
  product_workflow_only: true;
  legal_requirement_order_claimed: false;
  steps: NewAdmissionAssessmentStep[];
  assigned_question_count: number;
  assigned_question_keys: string[];
  duplicate_assigned_question_count: number;
  final_human_confirmation_questions: NewAdmissionStaffQuestion[];
  deferred_care_plan_questions: NewAdmissionStaffQuestion[];
  score_generation_attempt_count: 0;
  risk_grade_generation_attempt_count: 0;
  diagnosis_generation_attempt_count: 0;
  official_record_write_count: 0;
  signature_confirmation_count: 0;
  public_insurer_transfer_count: 0;
};

export type NewAdmissionCarePlanEvidence = {
  source_ref: string;
  document_kind: string;
  source_type: string;
  organization_role: string;
  verification_state: string;
  usage_label: string;
};

export type NewAdmissionCarePlanRevisionLink = {
  revision: number;
  before_value: unknown;
  after_value: unknown;
  before_status: string | null;
  after_status: string | null;
  before_evidence_refs: string[];
  after_evidence_refs: string[];
};

export type NewAdmissionCarePlanConfirmedItem = {
  field_key: string;
  label: string;
  value_kind: string;
  value: string | number | boolean | null;
  draft_text: string;
  source_assessment_types: CarePlanningDocumentType[];
  evidence_refs: string[];
  evidence_sources: NewAdmissionCarePlanEvidence[];
  confirmation_state: "confirmed";
  confirmed_by_name: string | null;
  confirmed_at: string | null;
  revision_links: NewAdmissionCarePlanRevisionLink[];
};

export type NewAdmissionCarePlanPendingItem = {
  field_key: string;
  label: string;
  category:
    | "staff_confirmation"
    | "material_conflict"
    | "current_observation_required"
    | "user_decision_required"
    | "admin_missing"
    | "expert_confirmation"
    | "official_basis";
  category_label: string;
  reason: string;
  value_kind: string;
  evidence_refs: string[];
  evidence_sources: NewAdmissionCarePlanEvidence[];
  conflict_values: string[];
  selected_value: null;
  revision_links: NewAdmissionCarePlanRevisionLink[];
};

export type NewAdmissionCarePlanPreview = {
  schema_version: "new_admission_care_plan_preview_v1";
  case_ref: string;
  revision: number;
  output_status: "draft" | "needs_confirmation";
  allowed_output_statuses: Array<"draft" | "needs_confirmation">;
  processing_scope: "internal_only" | "deidentified_dev";
  external_transfer_allowed: false;
  confirmed_assessment_results: NewAdmissionCarePlanConfirmedItem[];
  reflected_plan_items: NewAdmissionCarePlanConfirmedItem[];
  staff_questions: NewAdmissionCarePlanPendingItem[];
  conflicts_and_current_observations: NewAdmissionCarePlanPendingItem[];
  expert_review_items: NewAdmissionCarePlanPendingItem[];
  confirmed_result_count: number;
  reflected_item_count: number;
  staff_question_count: number;
  conflict_or_observation_count: number;
  expert_review_count: number;
  duplicate_question_count: 0;
  blocked_auto_value_kinds: string[];
  auto_generated_restricted_value_count: 0;
  official_record_write_count: 0;
  signature_confirmation_count: 0;
  public_insurer_transfer_count: 0;
  revision_mutation_count: 0;
  safety_notice: string;
};

export type NewAdmissionFormItem = {
  field_key: string;
  label: string;
  value_kind: string;
  document_types: CarePlanningDocumentType[];
  state:
    | "confirmed_fact"
    | "evidence_found"
    | "insufficient_evidence"
    | "material_conflict"
    | "current_observation_required"
    | "expert_review_required"
    | "staff_input_required"
    | "staff_edited";
  state_label: string;
  draft_text: string;
  evidence_refs: string[];
  evidence_sources: Array<{
    source_ref: string;
    document_kind: string;
    verification_state: string;
    organization_role: string;
  }>;
  conflict_values: string[];
  revision_links: Array<{
    revision: number;
    before_value: unknown;
    after_value: unknown;
    before_status: string | null;
    after_status: string | null;
  }>;
};

export type AssessmentFormReviewState =
  | ""
  | "confirmed"
  | "not_applicable"
  | "not_confirmed"
  | "follow_up";

export type NewAdmissionFormWorkspace = {
  schema_version: "new_admission_form_workspace_v1";
  case_ref: string;
  revision: number;
  reason:
    | "new_admission"
    | "periodic_reassessment"
    | "state_change_reassessment"
    | "staff_review";
  assessment_date: string | null;
  period_start: string | null;
  period_end: string | null;
  materials: Array<{
    source_ref: string;
    display_label: string;
    document_kind: string;
    status: "submitted" | "linked_existing_data" | "not_submitted" | "staff_review_required";
    mime_type: "application/pdf";
    size_bytes: number;
    page_count: number;
  }>;
  care_plan_gate: {
    available: boolean;
    reviewed_assessment_count: number;
    required_assessment_count: 4;
    reason: string;
  };
  output_status: "draft" | "needs_confirmation";
  processing_scope: "internal_only" | "deidentified_dev";
  external_transfer_allowed: false;
  analysis_summary: {
    source_count: number;
    confirmed_fact_count: number;
    evidence_found_count: number;
    staff_input_count: number;
    missing_state_counts: Record<string, number>;
  };
  comprehensive_report: {
    title: string;
    summary: string;
    evidence_items: NewAdmissionFormItem[];
    pending_items: NewAdmissionFormItem[];
    source_overview: Array<{
      source_ref: string;
      document_kind: string;
      verification_state: string;
      organization_role: string;
    }>;
  };
  documents: Array<{
    document_type: CarePlanningDocumentType;
    title: string;
    status: "draft" | "needs_confirmation";
    items: NewAdmissionFormItem[];
    sections: Array<{
      section_key: string;
      title: string;
      need_area: string | null;
      columns: string[];
      rows: Array<{
        row_key: string;
        field_key: string;
        label: string;
        value: string;
        value_kind: string;
        state: NewAdmissionFormItem["state"];
        state_label: string;
        review_state: AssessmentFormReviewState;
        evidence_refs: string[];
        cells: Record<string, string>;
        cell_states: Record<string, string>;
        cell_evidence_refs: Record<string, string[]>;
      }>;
      notice: string | null;
    }>;
    draft_text: string;
    confirmed_or_evidence_count: number;
    staff_input_count: number;
  }>;
  safety: {
    official_record_saved: false;
    signature_confirmed: false;
    public_insurer_transferred: false;
    restricted_auto_generation_count: 0;
    notice: string;
  };
};

export type AssessmentChatEvidencePreview = {
  resident_id: string;
  period_start: string;
  period_end: string;
  total_count: number;
  items: Array<{
    source_ref: string;
    summary: string;
    reference_locator: string;
  }>;
  external_transfer_allowed: false;
};

export type NewAdmissionQuestionFlow = {
  draft_id: string;
  resident_id: string;
  case_ref: string;
  revision: number;
  output_status: "draft" | "needs_confirmation";
  processing_scope: "internal_only" | "deidentified_dev";
  external_transfer_allowed: false;
  sources: NewAdmissionQuestionSource[];
  source_counts: Record<string, number>;
  reusable_facts: NewAdmissionReusableFact[];
  questions: NewAdmissionStaffQuestion[];
  reusable_fact_count: number;
  question_count: number;
  question_count_by_type: Record<string, number>;
  duplicate_question_count: number;
  assessment_workflow: NewAdmissionAssessmentWorkflow;
  care_plan_preview: NewAdmissionCarePlanPreview | null;
  form_workspace: NewAdmissionFormWorkspace;
  safety_notices: string[];
};

export type ResidentSyncPayload = {
  external_id: string;
  display_name: string;
  service_type: "facility" | "daycare" | "homecare" | string;
  floor: string | null;
  room_name: string | null;
  is_active: boolean;
  status?: string;
  internal_code?: string;
};

export type ResidentSyncItem = {
  id: string;
  external_id: string;
  change_type: "new" | "update" | "deactivate" | "unchanged" | "conflict";
  status: "pending" | "applied" | "not_required" | "blocked";
  current_resident_id: string | null;
  incoming_payload: ResidentSyncPayload;
  current_snapshot: ResidentSyncPayload | null;
  conflict_reason: string | null;
  applied_at: string | null;
};

export type ResidentSyncBatch = {
  id: string;
  source: string;
  original_name: string;
  file_sha256: string;
  source_generated_at: string | null;
  status: "preview" | "partially_applied" | "applied";
  summary: Record<string, number>;
  created_by_name: string;
  applied_by_name: string | null;
  applied_at: string | null;
  created_at: string;
  updated_at: string;
  items: ResidentSyncItem[];
};

export type AiAssistTaskType =
  | "image_text"
  | "image_explain"
  | "audio_summary"
  | "summary"
  | "history_search"
  | "risk_check"
  | "question";

export type AiAssistServiceContext = "facility" | "daycare" | "homecare";

export type AiAssistResidentNameCandidate = {
  recognized?: string;
  candidate?: string;
  recognized_name?: string;
  candidate_name?: string;
  resident_id?: string | null;
  service_type?: AiAssistServiceContext | null;
  confidence?: number | null;
  [key: string]: unknown;
};

export type AiAssistEvidence = {
  source_no: number;
  statement: string;
};

export type AiAssistTurn = {
  id: string;
  status:
    | "queued"
    | "preparing"
    | "extracting"
    | "searching"
    | "reasoning"
    | "validating"
    | "completed"
    | "failed"
    | "cancelled";
  task_type: AiAssistTaskType;
  question: string | null;
  answer: string | null;
  visible_text: string | null;
  evidence: AiAssistEvidence[];
  uncertainties: string[];
  recommended_actions: string[];
  resident_name_candidates?: AiAssistResidentNameCandidate[];
  service_context?: AiAssistServiceContext | null;
  service_context_source?: string | null;
  service_context_notice?: string | null;
  provider: string | null;
  model: string | null;
  processing_location: "rules" | "local" | "internal" | "external" | "unconfigured";
  external_transmission: boolean;
  deidentification_status:
    | "confirmed_deidentified"
    | "protected_local"
    | "not_sent_external"
    | "unknown";
  fallback_active: boolean;
  fallback_state: "primary" | "fallback" | "rules" | "blocked" | "unknown";
  fallback_reason: string | null;
  enhancement_status: "pending" | "completed" | "baseline" | "failed";
  human_review_required: true;
  elapsed_ms: number | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
};

export type AiAssistConversation = {
  id: string;
  message_id: string;
  status: AiAssistTurn["status"];
  turns: AiAssistTurn[];
};

export type AiAssistConfig = {
  enabled: boolean;
  codex_ready: boolean;
  whisper_ready: boolean;
  local_fallback_ready: boolean;
  primary_provider: "codex_worker";
  fallback_provider: "local_deterministic";
  worker_configured: boolean;
  external_real_data_enabled: boolean;
  external_real_image_data_enabled: boolean;
  nemotron_ready: boolean;
  local_ai_ready: boolean;
  local_ai_status_message: string;
  task_types: AiAssistTaskType[];
};

export type AiProviderId =
  | "rules"
  | "ollama"
  | "nvidia"
  | "openai"
  | "gemini"
  | "anthropic"
  | "openai_compatible";

export type AiProviderCheck = {
  name:
    | "configuration"
    | "authentication"
    | "model_list"
    | "capabilities"
    | "structured_contract"
    | "latency";
  status: "passed" | "failed" | "not_required" | "not_run";
  latency_ms: number | null;
  detail: string;
};

export type AiProviderConnection = {
  provider: AiProviderId;
  display_name: string;
  adapter_available: boolean;
  configured: boolean;
  state:
    | "ready"
    | "configured_unverified"
    | "unconfigured"
    | "unavailable"
    | "not_implemented";
  processing_location: "rules" | "local" | "external" | "unconfigured";
  endpoint_scope:
    | "rules"
    | "local"
    | "internal"
    | "external"
    | "invalid"
    | "unconfigured";
  credential_configured: boolean;
  configured_model: string | null;
  discovered_models: string[];
  capabilities: string[];
  enabled_features: string[];
  external_transmission_default_allowed: false;
  requires_deidentified_approval: boolean;
  checks: AiProviderCheck[];
  notice: string;
};

export type AiProviderStatus = {
  contract_version: "ai_provider_status_v1";
  providers: AiProviderConnection[];
  secret_values_included: false;
};

export type AiExecutionMode =
  | "automatic"
  | "api_first"
  | "local_first"
  | "fully_local";

export type AiSystemProviderId =
  | AiProviderId
  | "ollama_cloud"
  | "local_stt";

export type AiCapability =
  | "text"
  | "image"
  | "audio"
  | "video"
  | "structured_output"
  | "speech_to_text";

export type AiSystemProviderStatus = {
  provider: AiSystemProviderId;
  display_name: string;
  api_key_env_var: string | null;
  enabled: boolean;
  adapter_available: boolean;
  credential_configured: boolean;
  endpoint_configured: boolean;
  endpoint_scope:
    | "rules"
    | "local"
    | "internal"
    | "external"
    | "invalid"
    | "unconfigured";
  processing_location:
    | "rules"
    | "local"
    | "internal"
    | "external"
    | "unconfigured";
  connection_state:
    | "ready"
    | "configured_unverified"
    | "unconfigured"
    | "unavailable";
  configured_model: string | null;
  models: string[];
  capabilities: AiCapability[];
  timeout_seconds: number;
  last_latency_ms: number | null;
  last_error_code: string | null;
  last_error_message: string | null;
  structured_contract_verified: boolean;
  external_transmission_default_allowed: false;
  secret_value_included: false;
};

export type AiModelRoleSelection = {
  provider: AiSystemProviderId;
  model: string;
};

export type AiModelRoles = {
  text: AiModelRoleSelection;
  vision: AiModelRoleSelection | null;
  stt: AiModelRoleSelection | null;
  precision: AiModelRoleSelection;
};

export type AiRoleModelOption = AiModelRoleSelection & {
  processing_location:
    | "rules"
    | "local"
    | "internal"
    | "external"
    | "unconfigured";
  external_transmission: boolean;
  cost_notice: "no_extra_cost" | "cost_possible";
};

export type AiRoleModelOptions = Record<keyof AiModelRoles, AiRoleModelOption[]>;

export type AiSystemStatus = {
  contract_version: "ai_system_status_v1";
  generated_at: string;
  hardware: {
    detection_scope: "application_runtime";
    containerized: boolean;
    os_name: string;
    os_version: string;
    architecture: string;
    cpu_name: string;
    logical_cpu_count: number;
    system_ram_gb: number;
    nvidia_gpus: { name: string; vram_gb: number }[];
    max_nvidia_vram_gb: number;
    ollama_installed_or_reachable: boolean;
    ollama_processing_location: "local" | "internal" | "unconfigured";
    local_models: string[];
    local_vl_models: string[];
    stt_service_ready: boolean;
    stt_model: string | null;
    external_credentials_configured: AiSystemProviderId[];
    notices: string[];
  };
  recommendation: {
    mode: AiExecutionMode;
    reasons: string[];
    local_model_install_required: false;
  };
  runtime: {
    selected_mode: AiExecutionMode;
    recommended_mode: AiExecutionMode;
    current_path: "rules" | "local" | "internal" | "external" | "none";
    provider: AiSystemProviderId;
    model: string;
    processing_location:
      | "rules"
      | "local"
      | "internal"
      | "external"
      | "unconfigured";
    external_transmission: boolean;
    fallback_active: boolean;
    fallback_state: "baseline" | "primary" | "fallback" | "blocked";
    fallback_reason: string | null;
  };
  providers: AiSystemProviderStatus[];
  model_roles: AiModelRoles;
  model_role_options: AiRoleModelOptions;
  provider_priority: AiSystemProviderId[];
  fallback_order: ("external" | "local" | "internal" | "rules")[];
  max_attempts: number;
  total_timeout_seconds: number;
  external_deidentified_enabled: boolean;
  privacy_policy: {
    sensitive_external_default_blocked: true;
    uncertain_external_blocked: true;
    fully_local_external_blocked: true;
    external_requires_deidentified_confirmation: true;
    api_key_does_not_authorize_sensitive_transfer: true;
  };
  settings_saved: boolean;
  settings_write_allowed: boolean;
  secret_values_included: false;
  endpoint_values_included: false;
};

export type AiProviderTestResult = {
  provider: AiSystemProviderId;
  connection_ok: boolean;
  authentication_ok: boolean | null;
  models: string[];
  capabilities: AiCapability[];
  structured_contract_ok: boolean | null;
  latency_ms: number;
  error_code: string | null;
  message: string;
  deidentified_fixture_used: boolean;
  external_call_performed: boolean;
  secret_value_included: false;
};

export type AiProviderConnectResult = {
  provider: AiSystemProviderId;
  connected: boolean;
  enabled: boolean;
  selected_model: string | null;
  structured_contract_ok: boolean;
  latency_ms: number;
  error_code: string | null;
  message: string;
  deidentified_fixture_used: boolean;
  external_call_performed: boolean;
  secret_value_included: false;
};

export type CareforStaffSyncSource = {
  status: "ready" | "missing" | "invalid";
  original_name: string;
  generated_at: string | null;
  row_count: number;
  unique_name_count: number;
  service_counts: Record<string, number>;
  message: string;
};

export type StaffSyncPayload = {
  source_key: string;
  external_id: string;
  display_name: string;
  service_type: "facility" | "daycare" | "homecare" | string;
  job_name: string;
  employment_status: "active" | "leave" | "retired" | "unknown";
  source_status: string;
  start_date: string | null;
  end_date: string | null;
  staff_name?: string | null;
  staff_employment_status?: string | null;
  other_active_source_count?: number;
  other_active_services?: string[];
};

export type StaffSyncItem = {
  id: string;
  source_key: string;
  service_type: "facility" | "daycare" | "homecare" | string;
  external_id: string;
  change_type: "new" | "update" | "leave" | "retire" | "unchanged" | "conflict";
  status: "pending" | "not_required" | "blocked";
  current_staff_id: string | null;
  incoming_payload: StaffSyncPayload;
  current_snapshot: StaffSyncPayload | null;
  conflict_reason: string | null;
};

export type StaffSyncBatch = {
  id: string;
  source: string;
  original_name: string;
  file_sha256: string;
  source_generated_at: string | null;
  status: "preview";
  summary: Record<string, number>;
  created_by_name: string;
  created_at: string;
  updated_at: string;
  items: StaffSyncItem[];
};

export type StaffOrganizationProposal = {
  unit_type: "business" | "department" | "floor" | "team";
  proposed_name: string | null;
  unit_id: string | null;
  match_status:
    | "matched"
    | "reference_only"
    | "not_proposed"
    | "manual"
    | "missing"
    | "ambiguous";
  catalog_is_test_data: boolean | null;
  reason: string;
};

export type StaffJobProposal = {
  source_name: string;
  code: string | null;
  proposed_name: string | null;
  match_status: "matched" | "manual" | "missing";
  required_for_review: boolean;
  reason: string;
};

export type StaffPositionProposal = {
  proposed_name: string | null;
  position_id: string | null;
  match_status: "not_proposed" | "suggested" | "missing";
  manual_confirmation: boolean;
  reason: string;
};

export type StaffAssignmentAccount = {
  source_key: string;
  external_id: string;
  service_type: "facility" | "daycare" | "homecare" | string;
  employment_status: "active" | "leave" | "retired" | "unknown" | string;
  source_status: string;
  source_job_name: string;
  assignment_required?: boolean;
  assignment_ready: boolean;
  organizations: StaffOrganizationProposal[];
  job: StaffJobProposal;
  position: StaffPositionProposal;
  notes: string[];
};

export type StaffReviewIdentityDecision =
  | "pending"
  | "not_required"
  | "same_person"
  | "separate_people"
  | "custom_groups";

export type StaffReviewPersonGroup = {
  source_keys: string[];
  primary_source_key: string | null;
  primary_job_code: string | null;
  primary_position_title: string | null;
};

export type StaffReviewAccountAssignment = {
  source_key: string;
  department_name: string | null;
  job_code: string | null;
  position_title: string | null;
};

export type StaffSyncReviewDraft = {
  id: string;
  batch_id: string;
  candidate_key: string;
  identity_decision: StaffReviewIdentityDecision;
  person_groups: StaffReviewPersonGroup[];
  account_assignments: StaffReviewAccountAssignment[];
  note: string;
  completion_status: "draft" | "complete";
  completion_issues: string[];
  revision: number;
  updated_by_name: string;
  created_at: string;
  updated_at: string;
};

export type StaffPersonCandidate = {
  candidate_key: string;
  display_name: string;
  identity_status:
    | "single_account"
    | "linked_account"
    | "linked_accounts"
    | "verified_identity"
    | "confirmation_required"
    | "data_conflict";
  identity_evidence: "name_birth" | "name_only";
  same_name_candidate_count: number;
  review_status:
    | "proposal_ready"
    | "identity_review"
    | "assignment_review"
    | "blocked";
  account_count: number;
  requires_identity_confirmation: boolean;
  requires_assignment_confirmation: boolean;
  linked_staff_ids: string[];
  accounts: StaffAssignmentAccount[];
  notes: string[];
  review_draft: StaffSyncReviewDraft | null;
};

export type StaffAssignmentPreview = {
  batch_id: string;
  source_generated_at: string | null;
  summary: Record<string, number>;
  candidates: StaffPersonCandidate[];
};

export type StaffApplicationPlanBlocker = {
  code: string;
  message: string;
  affected_people: number;
  affected_accounts: number;
};

export type StaffApplicationPersonPlan = {
  plan_key: string;
  source_keys: string[];
  planned_internal_code: string;
  target_staff_id: string | null;
  staff_action: "create" | "update" | "no_change" | "blocked";
  employment_status: "active" | "leave" | "retired";
  service_types: string[];
  source_account_actions: Record<string, number>;
  service_period_actions: Record<string, number>;
  service_assignment_actions: Record<string, number>;
  current_assignment_account_count: number;
  history_only_account_count: number;
  login_action: "none";
  access_policy: "active_access" | "leave_suspended";
  blocker_codes: string[];
};

export type StaffApplicationCandidatePlan = {
  candidate_key: string;
  display_name: string;
  decision_source:
    | "auto_proposal"
    | "saved_review"
    | "missing_review"
    | "blocked_source";
  outcome: "prepared" | "needs_design" | "blocked";
  account_count: number;
  history_only_account_count: number;
  person_plans: StaffApplicationPersonPlan[];
  blocker_codes: string[];
  changes: string[];
};

export type StaffApplicationPlan = {
  plan_schema_version: 2;
  batch_id: string;
  file_sha256: string;
  read_only: true;
  apply_locked: true;
  can_apply: false;
  plan_fingerprint: string;
  change_fingerprint: string;
  change_manifest_supported: boolean;
  organization_catalog_version: string;
  organization_catalog_fingerprint: string;
  leave_access_policy_version: string;
  leave_access_policy_fingerprint: string;
  leave_access_policy: Record<string, string>;
  summary: Record<string, number>;
  blockers: StaffApplicationPlanBlocker[];
  candidates: StaffApplicationCandidatePlan[];
  safety_notes: string[];
  protected_tables: string[];
};

export type CareforRosterSourceStatus = {
  status: "captured" | "login_required" | "missing";
  captured_at: string | null;
  resident_count: number;
  staff_count: number;
  staff_aliases: {
    display_name: string;
    service_type: "facility" | "daycare" | "homecare";
    status: string;
    job_name: string;
    is_active: boolean;
  }[];
};

export type CareforRosterStatus = {
  generated_at: string | null;
  sources: Record<
    "facility" | "daycare" | "homecare",
    CareforRosterSourceStatus
  >;
};

export type Attachment = {
  photo_reading_status?: "general" | "pending" | "processing" | "completed" | "no_text" | "not_required" | "failed" | null;
  can_download_original?: boolean;
  can_view_reviewed_text?: boolean;
  can_review_text?: boolean;
  can_request_reading?: boolean;
  id: string;
  resident_candidate_notice?: string | null;
  resident_link_revision?: number;
  message_id: string;
  uploader_id: string;
  original_name: string;
  mime_type: string;
  size_bytes: number;
  download_url: string;
  thumbnail_url?: string | null;
  text_extraction: AttachmentTextExtraction | null;
};

export type AttachmentTextExtraction = {
  content_included?: boolean;
  result_char_count?: number | null;
  processing_ms?: number | null;
  error_type?: string | null;
  status: "pending" | "processing" | "completed" | "failed" | "reviewed" | "no_text" | "not_required" | "unsupported";
  review_revision?: number;
  source_locations?: Record<string, unknown>[];
  provider: string;
  model_name: string;
  extracted_text: string | null;
  original_extracted_text: string | null;
  suggested_text: string | null;
  suggested_service_context: "facility" | "daycare" | "homecare" | null;
  suggested_name_correction_count: number;
  auto_applied_name_count: number;
  unresolved_name_count: number;
  review_warnings: string[];
  reviewed_text: string | null;
  reviewed_by_name: string | null;
  latest_confirmed_text: string | null;
  latest_confirmed_by_name: string | null;
  latest_confirmed_at: string | null;
  latest_confirmation_source: string | null;
  error_message: string | null;
  completed_at: string | null;
  reviewed_at: string | null;
  review_decision:
    | "keep_raw"
    | "apply_candidate"
    | "direct_edit"
    | "needs_review"
    | null;
  correction_event_count: number;
  attempt_number: number;
  previous_attempts: {
    attempt_number: number;
    status: "completed" | "failed" | "reviewed";
    provider: string;
    model_name: string;
    extracted_text: string | null;
    reviewed_text: string | null;
    error_message: string | null;
    completed_at: string | null;
    reviewed_at: string | null;
    archived_at: string;
  }[];
  preprocessing: {
    applied: boolean;
    requires_staff_review: boolean;
    reason: string;
    selected_variant: string;
    input_label: string;
    document_crop_applied: boolean;
    output_blocked: boolean;
    retry_count: number;
    model_call_limit: number;
    original_preserved: boolean;
    external_transfer: boolean;
    metrics: Record<string, number | null>;
  } | null;
  spelling_candidates: {
    id: string;
    recognized: string;
    candidate: string;
    confidence: number;
    support_count: number;
    content_type: string;
    is_protected: boolean;
    source: string;
    reason: string;
    source_event_ids: string[];
    auto_applicable: false;
    rank: number | null;
    slot_index: number | null;
    applied_to_draft: boolean;
    application_reason: string | null;
    detected_text: string | null;
    resolved_in_draft: boolean;
  }[];
};

export type HandwritingVoiceCorrectionComparison = {
  schema_version: "handwriting_voice_correction_v1";
  status: "awaiting_staff_approval";
  mode: "full_reading" | "partial_correction" | "story_hint";
  stages: {
    revision_no: number;
    stage:
      | "initial_ocr"
      | "audio_transcript"
      | "ai_correction_suggestion"
      | "staff_approved_final";
    status: "completed" | "needs_confirmation" | "awaiting_staff_approval";
    text: string | null;
    provider: string | null;
    model: string | null;
    evidence_refs: string[];
  }[];
  alignments: {
    row_no: number;
    image_text: string | null;
    audio_text: string | null;
    status: "same" | "different" | "image_only" | "audio_only";
    similarity: number;
  }[];
  critical_facts: {
    category:
      | "date"
      | "time"
      | "quantity"
      | "unit"
      | "follow_up"
      | "completion"
      | "negation"
      | "official_record";
    image_values: string[];
    audio_values: string[];
    status: "same" | "different" | "image_only" | "audio_only" | "not_found";
    requires_staff_confirmation: boolean;
  }[];
  changed_fields: {
    category:
      | "narrative"
      | "date"
      | "time"
      | "quantity"
      | "unit"
      | "follow_up"
      | "completion"
      | "negation"
      | "name"
      | "medication"
      | "diagnosis"
      | "official_record";
    before_values: string[];
    proposed_values: string[];
    status: "unchanged" | "proposed" | "needs_confirmation" | "blocked";
    reason: string;
    evidence_refs: string[];
  }[];
  review_items: {
    review_item_id: string;
    kind: "conflict" | "one_sided_critical";
    category:
      | "date"
      | "time"
      | "quantity"
      | "unit"
      | "follow_up"
      | "completion"
      | "negation"
      | "official_record"
      | "name"
      | "medication"
      | "diagnosis";
    ocr_values: string[];
    whisper_values: string[];
    ocr_row_nos: number[];
    whisper_row_nos: number[];
    ai_recommendation: string | null;
    ai_recommendation_reason: string | null;
    requires_staff_confirmation: true;
  }[];
  ai_combination: {
    status:
      | "applied"
      | "provider_unavailable"
      | "invalid_response"
      | "evidence_validation_failed"
      | "not_requested";
    display_state: "ai_applied" | "basic_comparison" | "important_review_required";
    message: string;
    applied: boolean;
    lexicon_applications: {
      canonical: string;
      source: "ocr" | "whisper";
      source_row: number;
      source_value: string;
      match_type: "canonical" | "spelling_alias" | "speech_alias";
    }[];
  };
  audio_quality: {
    status: "good" | "low" | "unknown";
    reading_pace: "fast" | "normal" | "slow" | "unknown";
    message: string;
    reasons: string[];
    metrics: Record<string, number | string | null>;
  };
  summary: {
    alignment_row_count: number;
    different_row_count: number;
    critical_review_count: number;
    changed_field_count: number;
    proposed_field_count: number;
    blocked_field_count: number;
    review_item_count: number;
  };
  safety: {
    saved_before_approval: false;
    official_record_written: false;
    training_candidate_created: false;
    external_transfer: false;
    direct_typing_supported: true;
    unsupported_fact_generation: false;
    unmentioned_fields_unchanged: true;
    story_hint_is_not_ground_truth: true;
  };
};

export type HandwritingCorrectionSentenceDecision = {
  sentence_no: number;
  source_text: string;
  proposed_text: string;
  final_text: string;
  action: "accepted" | "edited";
  approved: true;
  evidence_refs: string[];
  coordinate_region_ids: string[];
  coordinate_evidence: {
    coordinate_review_id: string;
    region_id: string;
    bbox: CoordinateBox;
    placement_status: "confirmed";
    source: CoordinateRegion["source"];
    raw_text: string;
  }[];
};

export type HandwritingCorrectionApproval = {
  id: string;
  approval_group_id: string;
  revision: number;
  supersedes_approval_id: string | null;
  image_attachment_id: string;
  audio_attachment_id: string | null;
  mode: "direct_typing" | "full_reading" | "partial_correction" | "story_hint";
  status: "approved";
  initial_ocr: string;
  audio_or_explanation_text: string | null;
  ai_suggestion: string | null;
  approved_final_text: string;
  sentence_decisions: HandwritingCorrectionSentenceDecision[];
  evidence_refs: string[];
  confidence_state: {
    requires_confirmation?: boolean;
    critical_review_count?: number;
    blocked_field_count?: number;
    source?: string;
    approval_scope?: "whole_document" | "sentence_by_sentence";
    confirmed_coordinate_region_count?: number;
    review_item_count?: number;
    conflict_resolutions?: Array<{
      review_item_id: string;
      kind: "conflict" | "one_sided_critical";
      category: string;
      selected_source: "ocr" | "whisper" | "ai_recommendation" | "staff_manual";
      selected_value: string;
      evidence_source: "ocr" | "whisper" | "ai_recommendation" | "staff_manual";
    }>;
  };
  conflicts_confirmed: boolean;
  approved_by_id: string;
  approved_by_name: string;
  approved_at: string;
  is_test_data: boolean;
  official_record_saved: false;
  training_data_adopted: false;
  external_transfer_allowed: false;
};

export type HandwritingCorrectionApprovalHistory = {
  status: "reviewing" | "partially_approved" | "approved";
  versions: HandwritingCorrectionApproval[];
  current_revision: number;
  resident_link_revision?: number;
  safety: {
    partial_state_persisted: false;
    official_record_written: false;
    training_candidate_created: false;
    external_transfer: false;
    append_only_versions: true;
  };
};

export type CoordinateBox = {
  left: number;
  top: number;
  width: number;
  height: number;
  /** Clockwise rotation around the box centre. Older saved versions omit it. */
  rotation_degrees?: number;
};

export type CoordinateRegion = {
  client_id: string;
  bbox: CoordinateBox;
  raw_text: string;
  corrected_text: string;
  role: "name" | "status" | "time" | "general";
  document_template: string | null;
  section_role: string | null;
  resident_id: string | null;
  position_confidence: number;
  review_required: boolean;
  placement_status: "auto" | "confirmed" | "needs_position";
  source:
    | "ocr_bbox"
    | "auto_locator"
    | "confirmed_history"
    | "legacy_alignment"
    | "manual";
};

export type AttachmentCoordinateReview = {
  id: string | null;
  version_number: number;
  image_width: number | null;
  image_height: number | null;
  editor_version: string;
  document_template: string | null;
  regions: CoordinateRegion[];
  editor_name: string | null;
  created_at: string | null;
  text_confirmation_id: string | null;
  text_confirmed_at: string | null;
  is_bootstrap: boolean;
  raw_source_text: string;
  confirmed_source_text: string;
  resident_options: {
    id: string;
    display_name: string;
    service_type: string;
  }[];
};

export type ReadReceipt = {
  user_id: string;
  user_name: string;
  read_at: string;
};

export type MessageComment = {
  id: string;
  author_id: string;
  author_name: string;
  body: string;
  created_at: string;
};

export type MessageDetail = {
  message: Message;
  read_receipts: ReadReceipt[];
  comments: MessageComment[];
};

export type RoomMessageSearch = {
  matched_count: number;
  messages: Message[];
  matches: RoomMessageSearchMatch[];
  truncated: boolean;
};

export type RoomMessageSearchMatch = {
  message_id: string;
  source_type: "message" | "comment" | "image" | "audio" | "resident" | "sender";
  source_label: string;
  excerpt: string;
  attachment_id: string | null;
  attachment_name: string | null;
};

export type RoomSearchSummary = {
  summary: string;
  source_message_ids: string[];
  generator: string;
  provider_notice: string | null;
  fallback_reason: string | null;
  ai_elapsed_ms: number;
  request_elapsed_ms: number;
  cache_hit: boolean;
  processing_method: "local_ai" | "fallback_model" | "rules";
  generation_verified: boolean;
  summary_sentences: Array<{
    text: string;
    summary_type:
      | "core"
      | "change"
      | "action"
      | "follow_up"
      | "latest"
      | "handover"
      | "unconfirmed";
    resident_id: string | null;
    evidence_ids: string[];
    first_event_id: string | null;
    follow_up_ids: string[];
    latest_record_id: string | null;
    needs_follow_up: boolean;
    unconfirmed_part: string;
  }>;
  summary_evidence: Array<{
    number: number;
    message_id: string;
    created_at: string;
    source_label: string;
    excerpt: string;
  }>;
  staged: boolean;
  performance: Record<string, number | string | boolean | null>;
  display_mode: "overview" | "direct";
  period_granularity: "day" | "week" | "month";
  counts: {
    total: number;
    general: number;
    attention: number;
    unresolved: number;
  };
  highlights: string[];
  timeline: Array<{
    label: string;
    message_count: number;
    attention_count: number;
    unresolved_count: number;
    summary: string;
  }>;
  resident_summaries: Array<{
    resident_id: string | null;
    resident_name: string;
    message_count: number;
    attention_count: number;
    unresolved_count: number;
    summary: string;
    evidence: Array<{
      number: number;
      message_id: string;
      created_at: string;
      source_label: string;
      excerpt: string;
    }>;
  }>;
  general_evidence: Array<{
    number: number;
    message_id: string;
    created_at: string;
    source_label: string;
    excerpt: string;
  }>;
};

export type WorkItemStatus = "pending" | "in_review" | "ready" | "dismissed";
export type RecordClassification =
  | "daily_care"
  | "nutrition"
  | "health"
  | "safety"
  | "consultation"
  | "rehabilitation";
export type RiskLevel = "low" | "medium" | "high" | "urgent";
export type TargetRole =
  | "caregiver"
  | "nurse"
  | "social_worker"
  | "director"
  | "therapist"
  | "nutritionist";

export type DailyDocumentType =
  | "care_service_record"
  | "nursing_log"
  | "consultation_log"
  | "physical_restraint_log"
  | "program_log";
export type RecordUsageTag =
  | "nursing"
  | "care_service"
  | "consultation"
  | "program"
  | "general"
  | "needs_review";
export type CareDocumentCandidateType =
  | "care_service_record"
  | "consultation_log"
  | "cognitive_function_assessment"
  | "fall_risk_assessment"
  | "pressure_ulcer_risk_assessment"
  | "needs_assessment"
  | "long_term_care_service_plan";

export type DocumentDraftProposal = {
  document_type: DailyDocumentType;
  content: string;
  verification_questions: string[];
};

export type WorkItemSourceSnapshot = {
  message_id: string;
  room_id: string;
  room_name: string;
  sender_id: string;
  sender_name: string;
  resident_id: string | null;
  resident_name: string | null;
  resident_names: string[];
  body: string;
  message_type: string;
  attachment_ids: string[];
  created_at: string;
};

export type RecordDraft = {
  corrected_text: string;
  summary: string;
  observation_details: string;
  actions_taken: string[];
  resident_response: string;
  handover_summary: string;
  verification_questions: string[];
  classification: RecordClassification;
  risk_level: RiskLevel;
  target_roles: TargetRole[];
  document_types: string[];
  keywords: string[];
  document_drafts: DocumentDraftProposal[];
};

export type ConfirmedRecord = RecordDraft & {
  reviewer_notes: string | null;
  verification_acknowledged: boolean;
};

export type WorkItemDocumentDraft = {
  id: string;
  document_type: DailyDocumentType;
  content: string;
  verification_questions: string[];
  status: "draft" | "approved" | "not_used";
  version: number;
  generator: string;
  change_request: string | null;
  approved_by_name: string | null;
  approved_at: string | null;
  created_at: string;
  updated_at: string;
};

export type WorkItem = {
  id: string;
  status: WorkItemStatus;
  source_snapshot: WorkItemSourceSnapshot;
  message: Message;
  comments: MessageComment[];
  room_name: string;
  resident: Resident | null;
  document_types: string[];
  processing_notes: string | null;
  handled_by_name: string | null;
  ai_state: string;
  ai_suggestion: RecordDraft | null;
  ai_generator: string | null;
  ai_generated_at: string | null;
  confirmed_record: ConfirmedRecord | null;
  confirmed_by_name: string | null;
  confirmed_at: string | null;
  document_drafts: WorkItemDocumentDraft[];
  created_at: string;
  updated_at: string;
};

export type RoomMember = {
  id: string;
  full_name: string;
  job_name: string | null;
  floor: OrgUnit | null;
  team: OrgUnit | null;
};

export type RoomDigestPoint = {
  message_id: string;
  resident_name: string | null;
  body: string;
  sender_name: string;
  created_at: string;
  comment_count: number;
  action_type: string | null;
};

export type RoomDigest = {
  id: string;
  room_id: string;
  room_name: string;
  period_start: string;
  period_end: string;
  message_count: number;
  comment_count: number;
  resident_count: number;
  summary: string;
  major_points: RoomDigestPoint[];
  document_counts: Record<string, number>;
  risk_counts: Record<string, number>;
  generator: string;
  generated_at: string;
};

export type PeriodWorkdeskSource = {
  message: Message;
  room_name: string;
  resident_names: string[];
  comments: MessageComment[];
  read_count: number;
  reply_count: number;
  reply_user_count: number;
};

export type PeriodDocumentDraft = {
  key: string;
  event_group_id: string;
  resident_id: string;
  resident_name: string;
  document_type: DailyDocumentType;
  trigger: "event";
  status: "draft" | "needs_confirmation";
  content: string;
  draft_fields: Record<string, string>;
  missing_fields: string[];
  human_verification_fields: string[];
  verification_questions: string[];
  source_message_ids: string[];
};

export type PeriodRecordEvent = {
  event_group_id: string;
  resident_id: string | null;
  resident_name: string | null;
  summary: string;
  record_usage_tags: RecordUsageTag[];
  document_candidate_types: CareDocumentCandidateType[];
  document_candidate_reasons: Partial<Record<CareDocumentCandidateType, string>>;
  document_candidate_review_types: CareDocumentCandidateType[];
  document_candidate_review_reasons: Partial<
    Record<CareDocumentCandidateType, string>
  >;
  evidence_ids: string[];
  room_names: string[];
  sender_names: string[];
  occurred_at: string;
  latest_at: string;
};

export type PeriodRecordSummarySelection = {
  resident_id: string | null;
  evidence_ids: string[];
};

export type PeriodRecordSummary = {
  record_usage_tag: RecordUsageTag | null;
  record_usage_tags?: RecordUsageTag[];
  document_candidate_types: CareDocumentCandidateType[];
  summary: string;
  evidence_ids: string[];
  generator: string;
  elapsed_ms: number;
  document_drafts: PeriodDocumentDraft[];
};

export type CareBriefingPriority = "first" | "check" | "observe";
export type CareBriefingDisplayGroup =
  | "today_schedule"
  | "attention"
  | "carryover"
  | "background"
  | "completed";
export type CareBriefingFinalStatus =
  | "needs_confirmation"
  | "in_progress"
  | "completed"
  | "monitoring";

export type SocialWorkerWritingGuidance = {
  title: string;
  previous_state: string | null;
  current_state: string | null;
  confirmation_questions: string[];
  suggested_wording: string;
  related_document_types: CareDocumentCandidateType[];
  safety_notice: string;
  plan_review_recommended: boolean;
  plan_review_notice: string | null;
  based_on_confirmed_facts: boolean;
};

export type CareBriefingCard = {
  event_group_id: string | null;
  resident_id: string;
  resident_name: string;
  priority: CareBriefingPriority;
  display_group: CareBriefingDisplayGroup;
  importance_score: number;
  due_at: string | null;
  change_summary: string;
  final_status: CareBriefingFinalStatus;
  final_status_summary: string;
  check_reasons: string[];
  completed_actions: string[];
  pending_checks: string[];
  document_types: DailyDocumentType[];
  record_usage_tags: RecordUsageTag[];
  document_candidate_types: CareDocumentCandidateType[];
  source_message_ids: string[];
  current_message_count: number;
  baseline_message_count: number;
  occurred_at: string;
  latest_at: string;
  social_worker_guidance: SocialWorkerWritingGuidance | null;
};

export type CareBriefingSummary = {
  comparison_days: number;
  today_schedule_count: number;
  needs_attention_count: number;
  carryover_count: number;
  background_count: number;
  completed_count: number;
  pending_check_count: number;
  document_candidate_count: number;
  cards: CareBriefingCard[];
};

export type FieldCareBriefingEntry = {
  event_group_id: string;
  occurred_at: string;
  time_label: string;
  summary: string;
  evidence_ids: string[];
  explicit_pending: boolean;
  conflict_note: string | null;
};

export type FieldCareBriefingDay = {
  date: string;
  residents: Array<{
    resident_id: string;
    resident_name: string;
    entries: FieldCareBriefingEntry[];
  }>;
};

export type FieldCareConsultationReference = {
  event_group_id: string;
  resident_id: string;
  resident_name: string;
  occurred_at: string;
  time_label: string;
  summary: string;
  evidence_ids: string[];
};

export type FieldCareBriefingHistorySummary = {
  id: string;
  revision: number;
  period_start: string;
  period_end: string;
  resident_id: string | null;
  overall_summary: string;
  care_reference_count: number;
  consultation_reference_count: number;
  created_at: string;
};

export type CareTopic = {
  key: string;
  resident_id: string;
  resident_name: string;
  topic: string;
  summary: string;
  kinds: Array<"event" | "repeated" | "different" | "followup" | "conflict">;
  evidence_ids: string[];
  first_at: string;
  latest_at: string;
  entries: Array<{
    message_id: string;
    comment_id: string | null;
    attachment_id?: string | null;
    source_kind?: "message" | "comment" | "attachment";
    occurred_at: string;
    summary: string;
    kind: CareTopic["kinds"][number];
    event_key: string;
  }>;
};

export type CareRecordAnswer = {
  structured_facts?: { kind: "hydration"; resident_id: string | null; provided_ml: string | null;
    consumed_ml: string | null; remaining_ml: string | null; remaining_basis: string;
    offered_at: string; completed_at: string | null; evidence_ids: string[]; unknowns: string[] }[];
  ai_enhancement_available?: boolean;
  answer_sentences?: { text: string; evidence_ids: string[] }[];
  generation_verified?: boolean;
  resolved_resident_name?: string | null;
  error_type?: string | null;
  performance?: Record<string, number | boolean | string | null>;
  question: string;
  period_start: string;
  period_end: string;
  resident_id: string | null;
  answer: string;
  evidence_ids: string[];
  sources: PeriodWorkdeskSource[];
  matched_count: number;
  truncated: boolean;
  generator: string;
  limitation: string | null;
  processing_method?: "local_ai" | "fallback_model" | "rules" | "failed" | "clarification" | "no_records";
  timeline?: { date: string; fact: string; evidence_ids: string[]; background: boolean; resident_name?: string }[];
  current_status?: string | null;
  unknowns?: string[];
  fallback_notice?: string | null;
  ai_elapsed_ms?: number;
};

export type PeriodWorkdeskReview = {
  source_ids?: string[];
  attachment_count?: number;
  response_mode?: "full" | "briefing";
  care_topics?: CareTopic[];
  period_start: string;
  period_end: string;
  summary: string;
  generator: string;
  message_count: number;
  comment_count: number;
  resident_count: number;
  category_counts: Record<string, number>;
  document_counts: Record<string, number>;
  sources: PeriodWorkdeskSource[];
  document_drafts: PeriodDocumentDraft[];
  record_events: PeriodRecordEvent[];
  record_group_counts: Record<string, number>;
  document_candidate_counts: Record<string, number>;
  briefing: CareBriefingSummary;
  truncated: boolean;
  processed_periods: string[];
  truncated_periods: string[];
  scenario_label: string | null;
  scenario_notice: string | null;
  overall_summary: string;
  daily_care_references: FieldCareBriefingDay[];
  consultation_references: FieldCareConsultationReference[];
  care_reference_count: number;
  consultation_reference_count: number;
  history_id: string | null;
  history_revision: number | null;
  generated_at: string | null;
};

export type DocumentCandidateDashboardData = {
  total_count: number;
  filtered_count: number;
  document_counts: Record<string, number>;
  risk_counts: Record<string, number>;
  classification_counts: Record<string, number>;
  items: WorkItem[];
};

export type LoginSession = {
  id: string;
  created_at: string;
  expires_at: string;
  last_seen_at: string;
  user_agent: string | null;
  is_current: boolean;
};
