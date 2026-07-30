export type UnitType =
  | "business"
  | "department"
  | "floor"
  | "team";

export type OrgUnit = {
  id: string;
  unit_type: UnitType;
  name: string;
  code: string | null;
  is_active: boolean;
  active_staff_count: number;
  active_room_count: number;
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
    | null;
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
    | "self";
  unread_count: number;
  last_message: string | null;
  last_message_at: string | null;
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
  message_type: "chat" | "notice" | "handover" | "work_request" | "report";
  body: string;
  resident: Resident | null;
  resident_links: MessageResidentLink[];
  resident_ref: string | null;
  attachments: Attachment[];
  comment_count: number;
  unread_comment_count: number;
  read_count: number;
  reply_user_count: number;
  action_item: ActionItem | null;
  forwarded_from: {
    message_id: string;
    room_name: string;
    sender_name: string;
    created_at: string;
  } | null;
  created_at: string;
};

export type MessageResidentLink = {
  resident: Resident;
  source: "manual" | "text_exact" | "ocr_exact" | "audio_transcript";
  status: "candidate" | "confirmed" | "rejected";
  reviewed_at: string | null;
};

export type Resident = {
  id: string;
  display_name: string;
  service_type: "facility" | "daycare" | "homecare" | string;
  floor: OrgUnit | null;
  sort_order: number;
  is_priority: boolean;
  roster_source: "carefor" | "smcodi" | "manual" | "demo";
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
  id: string;
  original_name: string;
  mime_type: string;
  size_bytes: number;
  download_url: string;
  text_extraction: AttachmentTextExtraction | null;
};

export type AttachmentTextExtraction = {
  status: "pending" | "processing" | "completed" | "failed" | "reviewed";
  provider: string;
  model_name: string;
  extracted_text: string | null;
  original_extracted_text: string | null;
  reviewed_text: string | null;
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
  truncated: boolean;
};

export type RoomSearchSummary = {
  summary: string;
  source_message_ids: string[];
  generator: string;
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
  read_count: number;
  reply_count: number;
  reply_user_count: number;
};

export type PeriodDocumentDraft = {
  key: string;
  resident_id: string;
  resident_name: string;
  document_type: DailyDocumentType;
  content: string;
  verification_questions: string[];
  source_message_ids: string[];
};

export type PeriodRecordEvent = {
  event_group_id: string;
  resident_id: string | null;
  resident_name: string | null;
  summary: string;
  record_usage_tags: RecordUsageTag[];
  evidence_ids: string[];
  room_names: string[];
  sender_names: string[];
  latest_at: string;
};

export type PeriodRecordSummarySelection = {
  resident_id: string | null;
  evidence_ids: string[];
};

export type PeriodRecordSummary = {
  record_usage_tag: RecordUsageTag;
  record_usage_tags?: RecordUsageTag[];
  summary: string;
  evidence_ids: string[];
  generator: string;
  elapsed_ms: number;
};

export type CareBriefingPriority = "first" | "check" | "observe";

export type CareBriefingCard = {
  event_group_id: string | null;
  resident_id: string;
  resident_name: string;
  priority: CareBriefingPriority;
  change_summary: string;
  check_reasons: string[];
  completed_actions: string[];
  pending_checks: string[];
  document_types: DailyDocumentType[];
  record_usage_tags: RecordUsageTag[];
  source_message_ids: string[];
  current_message_count: number;
  baseline_message_count: number;
  latest_at: string;
};

export type CareBriefingSummary = {
  comparison_days: number;
  needs_attention_count: number;
  pending_check_count: number;
  document_candidate_count: number;
  cards: CareBriefingCard[];
};

export type PeriodWorkdeskReview = {
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
  briefing: CareBriefingSummary;
  truncated: boolean;
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
