"use client";

import { FormEvent, type RefObject, useEffect, useMemo, useRef, useState } from "react";
import { apiBase, apiErrorMessage, apiFetch } from "../api";
import { AdminConversationReview } from "./AdminConversationReview";
import {
  readNavigationHistoryState,
  updateNavigationHistoryState,
} from "../navigationHistory";
import type {
  CareforStaffSyncSource,
  CareforRosterStatus,
  JobCode,
  ManagedRoom,
  OrgUnit,
  PositionTitle,
  Resident,
  ResidentSyncBatch,
  ResidentSyncItem,
  StaffApplicationPlan,
  StaffAssignmentAccount,
  StaffAssignmentPreview,
  StaffOrganizationProposal,
  StaffPersonCandidate,
  StaffReviewAccountAssignment,
  StaffReviewIdentityDecision,
  StaffReviewPersonGroup,
  StaffDirectoryEntry,
  StaffServiceAssignment,
  StaffSyncBatch,
  StaffSyncItem,
  StaffSyncReviewDraft,
  UnitType,
  User,
} from "../types";

type Tab =
  | "employees"
  | "new-employee"
  | "staff-sync"
  | "organization"
  | "custom-room"
  | "residents";
type RoomKind = Exclude<ManagedRoom["kind"], "living_space">;
type ResidentScope = ManagedRoom["resident_scope"];
type EmployeeStatusFilter = "all" | StaffDirectoryEntry["employment_status"];
type RoomKindFilter = "all" | RoomKind;
type LivingSpaceRoomDetails = {
  room_id: string;
  room_name: string;
  is_active: boolean;
  member_ids: string[];
  member_count: number;
  message_count: number;
  attachment_count: number;
};
type StaffReviewDraftEditor = {
  expected_revision: number | null;
  identity_decision: StaffReviewIdentityDecision;
  person_groups: StaffReviewPersonGroup[];
  account_assignments: StaffReviewAccountAssignment[];
  note: string;
};

type BulkTransferEntity = "resident" | "staff";
type BulkTransferIssue = { code: string; message: string; field?: string | null };
type BulkTransferItem = {
  id: string;
  row_number: number;
  entity_id: string | null;
  display_code: string | null;
  change_type: "new" | "update" | "deactivate" | "unchanged" | "duplicate" | "conflict" | "error" | "review";
  status: "pending" | "applied" | "failed";
  current_snapshot: Record<string, unknown> | null;
  incoming_payload: Record<string, unknown>;
  issues: BulkTransferIssue[];
};
type BulkTransferBatch = {
  id: string;
  entity_type: BulkTransferEntity;
  status: "preview" | "partially_applied" | "applied";
  original_name: string;
  summary: Record<string, number>;
  apply_result: Record<string, number> | null;
  items: BulkTransferItem[];
};

const bulkTransferChangeLabels: Record<BulkTransferItem["change_type"], string> = {
  new: "새로 추가",
  update: "정보 수정",
  deactivate: "이용 종료·퇴사",
  unchanged: "변경 없음",
  duplicate: "중복",
  conflict: "충돌",
  error: "입력 오류",
  review: "직원 확인 필요",
};

function bulkTransferValues(value: Record<string, unknown> | null) {
  if (!value) return "없음";
  return Object.entries(value)
    .filter(([key]) => key !== "version")
    .map(([, item]) => (item === null || item === "" ? "미지정" : String(item)))
    .join(" · ");
}

function BulkTransferPanel({
  entity,
  disabled,
  onApplied,
}: {
  entity: BulkTransferEntity;
  disabled: boolean;
  onApplied: () => Promise<void>;
}) {
  const subject = entity === "resident" ? "어르신" : "직원";
  const inputRef = useRef<HTMLInputElement>(null);
  const [batch, setBatch] = useState<BulkTransferBatch | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState("");
  const [errorMessage, setErrorMessage] = useState("");

  const selectableItems = batch?.items.filter(
    (item) =>
      ["new", "update", "deactivate"].includes(item.change_type) &&
      item.issues.length === 0 &&
      item.status === "pending",
  ) ?? [];

  async function download(path: string, fallbackName: string) {
    setErrorMessage("");
    const response = await fetch(`${apiBase()}${path}`, { credentials: "include" });
    if (!response.ok) {
      let payload: unknown;
      try { payload = await response.json(); } catch { payload = null; }
      throw new Error(apiErrorMessage(payload, response.status));
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = fallbackName;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  }

  async function run(action: () => Promise<void>) {
    setWorking(true);
    setErrorMessage("");
    setMessage("");
    try {
      await action();
    } catch (reason) {
      setErrorMessage(reason instanceof Error ? reason.message : "요청을 처리하지 못했습니다.");
    } finally {
      setWorking(false);
    }
  }

  async function preview(file: File) {
    const form = new FormData();
    form.append("file", file);
    const nextBatch = await apiFetch<BulkTransferBatch>(
      `/api/admin/bulk-transfer/${entity}/preview`,
      { method: "POST", body: form },
    );
    setBatch(nextBatch);
    setSelectedIds(
      nextBatch.items
        .filter((item) => ["new", "update", "deactivate"].includes(item.change_type) && item.issues.length === 0)
        .map((item) => item.id),
    );
    setMessage("파일 검사가 끝났습니다. 아래 변경 내용을 확인해 주세요.");
  }

  async function applySelected() {
    if (!batch) return;
    const nextBatch = await apiFetch<BulkTransferBatch>(
      `/api/admin/bulk-transfer/batches/${batch.id}/apply`,
      { method: "POST", body: JSON.stringify({ item_ids: selectedIds }) },
    );
    setBatch(nextBatch);
    setSelectedIds([]);
    await onApplied();
    setMessage("선택한 변경을 적용했습니다. 적용 결과를 확인해 주세요.");
  }

  return (
    <section className="bulk-transfer-panel" aria-label={`${subject} 엑셀 일괄 등록·수정`}>
      <div className="bulk-transfer-heading">
        <div>
          <strong>{subject} 엑셀 일괄 등록·수정</strong>
          <p>현재 자료를 내려받아 수정한 뒤 다시 올릴 수 있습니다. 올린 내용은 바로 저장되지 않으며, 바뀌는 내용을 확인한 후 적용합니다.</p>
        </div>
      </div>
      <div className="bulk-transfer-steps" aria-label="엑셀 작업 순서">
        <button type="button" className="button button-secondary" disabled={working || disabled} onClick={() => void run(() => download(`/api/admin/bulk-transfer/${entity}/template.xlsx`, `${entity}_input_template.xlsx`))}>1. 예제 엑셀 받기</button>
        <button type="button" className="button button-secondary" disabled={working || disabled} onClick={() => void run(() => download(`/api/admin/bulk-transfer/${entity}/current.xlsx`, `${entity}_current.xlsx`))}>2. 현재 자료 받기</button>
        <button type="button" className="button button-secondary" disabled={working || disabled} onClick={() => inputRef.current?.click()}>3. 수정한 엑셀 올리기</button>
        <input
          ref={inputRef}
          type="file"
          accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.currentTarget.value = "";
            if (file) void run(() => preview(file));
          }}
        />
      </div>
      {message ? <p className="form-success" role="status">{message}</p> : null}
      {errorMessage ? <p className="form-error" role="alert">{errorMessage}</p> : null}
      {batch ? (
        <div className="bulk-transfer-preview">
          <div className="bulk-transfer-summary" aria-label="변경 내용 요약">
            <span><b>{batch.summary.new ?? 0}</b> 새로 추가</span>
            <span><b>{batch.summary.update ?? 0}</b> 정보 수정</span>
            <span><b>{batch.summary.deactivate ?? 0}</b> 이용 종료·퇴사</span>
            <span><b>{batch.summary.unchanged ?? 0}</b> 변경 없음</span>
            <span><b>{(batch.summary.error ?? 0) + (batch.summary.conflict ?? 0) + (batch.summary.review ?? 0)}</b> 오류·충돌</span>
          </div>
          <div className="bulk-transfer-list">
            {batch.items.map((item) => {
              const selectable = selectableItems.some((candidate) => candidate.id === item.id);
              return (
                <article className={`bulk-transfer-row change-${item.change_type}`} key={item.id}>
                  <label>
                    <input
                      type="checkbox"
                      disabled={!selectable || working}
                      checked={selectedIds.includes(item.id)}
                      onChange={(event) => setSelectedIds((current) => event.target.checked ? [...current, item.id] : current.filter((id) => id !== item.id))}
                      aria-label={`${item.row_number}행 ${item.display_code ?? "코드 미입력"} 적용`}
                    />
                    <span>{item.row_number}행</span>
                  </label>
                  <div>
                    <strong>{item.display_code || "코드 자동 생성"}</strong>
                    <span className="bulk-transfer-badge">{bulkTransferChangeLabels[item.change_type]}</span>
                  </div>
                  <div className="bulk-transfer-diff"><small>이전</small><span>{bulkTransferValues(item.current_snapshot)}</span></div>
                  <div className="bulk-transfer-diff"><small>변경</small><span>{bulkTransferValues(item.incoming_payload)}</span></div>
                  {item.issues.length ? <p className="bulk-transfer-issue">{item.issues.map((issue) => issue.message).join(" ")}</p> : null}
                  {item.status === "applied" ? <p className="bulk-transfer-applied">적용 완료</p> : null}
                </article>
              );
            })}
          </div>
          <div className="bulk-transfer-actions">
            <span>{selectedIds.length}개 선택됨</span>
            {(batch.items.some((item) => item.issues.length > 0 || item.status === "failed")) ? (
              <button type="button" className="button button-secondary" disabled={working} onClick={() => void run(() => download(`/api/admin/bulk-transfer/batches/${batch.id}/errors.xlsx`, "bulk_transfer_errors.xlsx"))}>오류 엑셀 받기</button>
            ) : null}
            <button type="button" className="button button-primary" disabled={working || selectedIds.length === 0} onClick={() => void run(applySelected)}>5. 선택한 내용 적용</button>
          </div>
          {batch.apply_result ? (
            <p className="bulk-transfer-result" role="status">
              요청 {batch.apply_result.requested ?? 0} · 추가 {batch.apply_result.added ?? 0} · 수정 {batch.apply_result.updated ?? 0} · 상태 변경 {batch.apply_result.status_changed ?? 0} · 실패 {batch.apply_result.failed ?? 0}
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

const unitLabels: Record<UnitType, string> = {
  business: "사업부",
  department: "부서",
  floor: "어르신 생활실·방·구역",
  team: "팀",
};
const staffUnitTypes = ["business", "department", "team"] as const;

const employeeStatusLabels: Record<StaffDirectoryEntry["employment_status"], string> = {
  active: "재직",
  leave: "휴직",
  retired: "퇴사",
};

const roomKindLabels: Record<RoomKind, string> = {
  self: "나와의 대화",
  all: "모든 재직 직원",
  business: "사업부 기준",
  department: "부서 기준",
  floor: "생활실·방·구역 기준",
  team: "팀 기준",
  job: "직종 기준",
  custom: "직원 직접 선택",
  ai: "개인 MESIL AI",
};

const approvedOrganizationDisplayOrder: Record<string, number> = {
  "business.facility": 0,
  "business.daycare": 1,
  "business.homecare": 2,
  "department.facility.welfare": 10,
  "department.facility.medical": 11,
  "department.facility.care": 12,
  "department.facility.nutrition": 13,
  "floor.facility.2": 20,
  "floor.facility.3": 21,
  "floor.facility.4": 22,
  "floor.facility.5": 23,
  "department.daycare.welfare": 30,
  "department.daycare.medical": 31,
  "department.daycare.care": 32,
  "department.daycare.nutrition": 33,
  "department.homecare.welfare": 40,
  "department.homecare.care": 41,
};

function compareApprovedOrganization(left: OrgUnit, right: OrgUnit) {
  const leftOrder = approvedOrganizationDisplayOrder[left.code ?? ""] ?? 999;
  const rightOrder = approvedOrganizationDisplayOrder[right.code ?? ""] ?? 999;
  return leftOrder - rightOrder || left.name.localeCompare(right.name, "ko-KR");
}

const residentSyncChangeLabels: Record<ResidentSyncItem["change_type"], string> = {
  new: "새로 추가",
  update: "정보 바뀜",
  deactivate: "이용 중지",
  unchanged: "그대로",
  conflict: "직접 확인",
};

const residentSyncStatusLabels: Record<ResidentSyncBatch["status"], string> = {
  preview: "저장 전",
  partially_applied: "일부 저장",
  applied: "저장 완료",
};

const loginStatusLabels: Record<StaffDirectoryEntry["login_status"], string> = {
  not_issued: "로그인 미발급",
  enabled: "로그인 가능",
  disabled: "로그인 중지",
};

const staffSyncChangeLabels: Record<StaffSyncItem["change_type"], string> = {
  new: "새 계정 후보",
  update: "계정 정보 변경",
  leave: "휴직 계정",
  retire: "퇴사 계정",
  unchanged: "변경 없음",
  conflict: "직접 확인",
};

const staffAssignmentReviewLabels: Record<
  StaffPersonCandidate["review_status"],
  string
> = {
  proposal_ready: "기본안 준비",
  identity_review: "동일인 확인 필요",
  assignment_review: "배정 확인 필요",
  blocked: "자료 확인 필요",
};

const staffOrganizationProposalLabels: Record<
  StaffOrganizationProposal["unit_type"],
  string
> = {
  business: "사업부",
  department: "부서",
  floor: "근무층",
  team: "팀",
};

function isPracticeResidentSyncBatch(batch: ResidentSyncBatch) {
  const name = batch.original_name.toLocaleLowerCase("ko-KR");
  return name.includes("연습") || name.includes("example");
}

const residentServiceLabels: Record<string, string> = {
  facility: "시설",
  daycare: "주간보호",
  homecare: "방문요양",
};
const staffServiceDisplayOrder = ["facility", "daycare", "homecare"] as const;

const staffAssignmentBasisLabels: Record<
  StaffServiceAssignment["assignment_basis"],
  string
> = {
  carefor_auto: "기존 자료 연결",
  admin_confirmed: "관리자 확인",
  legacy_manual: "기존 수동 배정",
};

function serviceAssignmentJobLabel(assignment: StaffServiceAssignment) {
  return (
    assignment.job_name?.trim() ||
    assignment.job_title_snapshot?.trim() ||
    "직종 확인 필요"
  );
}

function currentServiceAssignments(employee: StaffDirectoryEntry) {
  return employee.service_assignments
    .filter((assignment) => assignment.is_current)
    .sort(
      (left, right) =>
        staffServiceDisplayOrder.indexOf(left.service_type) -
        staffServiceDisplayOrder.indexOf(right.service_type),
    );
}

function employeeOrganizationSummary(employee: StaffDirectoryEntry) {
  const currentServiceSummary = currentServiceAssignments(employee)
    .map(
      (assignment) =>
        `${residentServiceLabels[assignment.service_type] ?? assignment.service_type} · ${serviceAssignmentJobLabel(assignment)}`,
    )
    .join(" / ");
  if (currentServiceSummary) return currentServiceSummary;

  return (
    [
      employee.legacy_assignment.business?.name,
      employee.legacy_assignment.department?.name,
      employee.legacy_assignment.team?.name,
      employee.legacy_assignment.job_name,
      employee.legacy_assignment.position_title,
    ]
      .filter(Boolean)
      .join(" · ") || "소속 미지정"
  );
}

const legacyPositionByJobCode: Record<string, string> = {
  representative: "대표",
  office_director: "사무국장",
};

function staffAccountNeedsCurrentAssignment(account: StaffAssignmentAccount) {
  return account.assignment_required ?? account.employment_status !== "retired";
}

function staffReviewEditorFromCandidate(
  candidate: StaffPersonCandidate,
): StaffReviewDraftEditor {
  const saved = candidate.review_draft;
  if (saved) {
    return {
      expected_revision: saved.revision,
      identity_decision: saved.identity_decision,
      person_groups: saved.person_groups,
      account_assignments: saved.account_assignments,
      note: saved.note,
    };
  }
  return {
    expected_revision: null,
    identity_decision: candidate.requires_identity_confirmation
      ? "pending"
      : "not_required",
    person_groups: candidate.requires_identity_confirmation
      ? []
      : [
        {
          source_keys: candidate.accounts.map((account) => account.source_key),
          primary_source_key: null,
          primary_job_code: null,
          primary_position_title: null,
        },
      ],
    account_assignments: candidate.accounts.map((account) => ({
      source_key: account.source_key,
      department_name: null,
      job_code: null,
      position_title: staffAccountNeedsCurrentAssignment(account) && account.position.manual_confirmation
        ? account.position.proposed_name
        : null,
    })),
    note: "",
  };
}

function staffReviewEditorsFromPreview(preview: StaffAssignmentPreview) {
  return Object.fromEntries(
    preview.candidates
      .filter((candidate) =>
        ["identity_review", "assignment_review"].includes(candidate.review_status),
      )
      .map((candidate) => [
        candidate.candidate_key,
        staffReviewEditorFromCandidate(candidate),
      ]),
  );
}

function staffReviewEditorIsDirty(
  candidate: StaffPersonCandidate,
  editor: StaffReviewDraftEditor,
) {
  const decision = (value: StaffReviewDraftEditor) => ({
    identity_decision: value.identity_decision,
    person_groups: value.person_groups,
    account_assignments: value.account_assignments,
    note: value.note,
  });
  return (
    JSON.stringify(decision(editor)) !==
    JSON.stringify(decision(staffReviewEditorFromCandidate(candidate)))
  );
}

function staffCandidateNeedsAttention(
  candidate: StaffPersonCandidate,
  editor?: StaffReviewDraftEditor,
) {
  if (candidate.review_status === "blocked") return true;
  if (!["identity_review", "assignment_review"].includes(candidate.review_status)) {
    return false;
  }
  return (
    candidate.review_draft?.completion_status !== "complete" ||
    (editor ? staffReviewEditorIsDirty(candidate, editor) : false)
  );
}

function staffReviewAccountLabel(account: StaffAssignmentAccount) {
  return `${residentServiceLabels[account.service_type] ?? account.service_type} · ${
    account.source_job_name || "직종 미확인"
  }`;
}

function fetchStaffApplicationPlan(batchId: string) {
  return apiFetch<StaffApplicationPlan>(
    `/api/admin/carefor-staff-sync/batches/${batchId}/application-plan`,
  );
}

function roomRuleLabel(room: ManagedRoom): string {
  const scope = room.scope_name ?? room.job_name;
  const kindLabel = room.kind === "living_space" ? "생활공간" : roomKindLabels[room.kind];
  return scope ? `${kindLabel} · ${scope}` : kindLabel;
}

function orgUnitDisplayName(unit: OrgUnit): string {
  return unit.parent_unit_name
    ? `${unit.parent_unit_name} · ${unit.name}`
    : unit.name;
}

function sameIdSet(left: string[], right: string[]): boolean {
  if (left.length !== right.length) return false;
  const rightIds = new Set(right);
  return left.every((id) => rightIds.has(id));
}

function sameIdOrder(left: string[], right: string[]): boolean {
  return (
    left.length === right.length &&
    left.every((id, index) => id === right[index])
  );
}

const emptyEmployee = {
  username: "",
  full_name: "",
  password: "",
  employee_code: "",
  role: "staff",
  can_process_records: false,
  business_id: "",
  department_id: "",
  job_code: "caregiver",
  position_title: "",
  team_id: "",
};

type EmployeeDraft = typeof emptyEmployee;
type StaffServiceType = (typeof staffServiceDisplayOrder)[number];
type ServiceAssignmentDraft = {
  enabled: boolean;
  job_code: string;
  position_title: string;
};

const emptyLoginIssue = {
  username: "",
  temporary_password: "",
  can_process_records: false,
};
type ServiceAssignmentDrafts = Record<StaffServiceType, ServiceAssignmentDraft>;

function emptyServiceAssignmentDrafts(): ServiceAssignmentDrafts {
  return {
    facility: { enabled: false, job_code: "", position_title: "" },
    daycare: { enabled: false, job_code: "", position_title: "" },
    homecare: { enabled: false, job_code: "", position_title: "" },
  };
}

function serviceAssignmentDraftsFromEmployee(
  employee: StaffDirectoryEntry,
): ServiceAssignmentDrafts {
  const drafts = emptyServiceAssignmentDrafts();
  currentServiceAssignments(employee).forEach((assignment) => {
    drafts[assignment.service_type] = {
      enabled: true,
      job_code: assignment.job_code ?? "",
      position_title: assignment.position_title_snapshot ?? "",
    };
  });
  return drafts;
}

function serviceAssignmentsPayload(drafts: ServiceAssignmentDrafts) {
  return {
    assignments: staffServiceDisplayOrder
      .filter((serviceType) => drafts[serviceType].enabled)
      .map((serviceType) => ({
        service_type: serviceType,
        job_code: drafts[serviceType].job_code.trim(),
        position_title: drafts[serviceType].position_title.trim() || null,
      })),
  };
}

const emptyResidentDraft = {
  display_name: "",
  service_type: "facility" as "facility" | "daycare" | "homecare",
  floor_id: "",
};

// eslint-disable-next-line @typescript-eslint/no-unused-vars -- 조직 편집 재연결 시 사업부-부서 종속·시험조직 제외 계약을 보존합니다.
function UnitSelect({
  type,
  units,
  value,
  onChange,
  businessId,
  emptyLabel,
  helpText,
  disabled = false,
}: {
  type: UnitType;
  units: OrgUnit[];
  value: string;
  onChange: (value: string) => void;
  businessId?: string;
  emptyLabel?: string;
  helpText?: string;
  disabled?: boolean;
}) {
  const currentUnit = units.find(
    (unit) => unit.id === value && unit.unit_type === type,
  );
  const selectableUnits = units.filter(
    (unit) =>
      unit.unit_type === type &&
      unit.is_active &&
      !unit.is_test_data &&
      (type !== "department" ||
        (Boolean(businessId) && unit.parent_unit_id === businessId)),
  );
  const currentUnitIsFiltered =
    currentUnit !== undefined &&
    !selectableUnits.some((unit) => unit.id === currentUnit.id);
  const waitingForBusiness =
    type === "department" && !businessId && value === "";
  const emptyOptionLabel = waitingForBusiness
    ? "사업부를 먼저 선택하세요"
    : emptyLabel ?? "미지정";

  return (
    <label>
      {unitLabels[type]}
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled || waitingForBusiness}
      >
        <option value="">{emptyOptionLabel}</option>
        {currentUnitIsFiltered ? (
          <option value={currentUnit.id}>
            {orgUnitDisplayName(currentUnit)} {currentUnit.is_test_data
              ? "(기존 시험·예외 연결)"
              : "(기존 연결·사업부 확인 필요)"}
          </option>
        ) : null}
        {selectableUnits.map((unit) => (
            <option key={unit.id} value={unit.id}>
              {orgUnitDisplayName(unit)}
            </option>
          ))}
      </select>
      {helpText ? <small className="field-help">{helpText}</small> : null}
    </label>
  );
}

function JobSelect({
  jobs,
  value,
  onChange,
  allowUnassigned = false,
  disabled = false,
}: {
  jobs: JobCode[];
  value: string;
  onChange: (value: string) => void;
  allowUnassigned?: boolean;
  disabled?: boolean;
}) {
  const currentJob = jobs.find((job) => job.code === value);
  const currentIsInactive = Boolean(currentJob && !currentJob.is_active);
  return (
    <label>
      직종
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        required={!allowUnassigned}
        disabled={disabled}
      >
        {allowUnassigned ? <option value="">미지정(확인 전)</option> : null}
        {currentIsInactive ? (
          <option value={currentJob?.code}>
            {currentJob?.name} (현재 목록에서 삭제됨)
          </option>
        ) : null}
        {jobs.filter((job) => job.is_active).map((job) => (
          <option key={job.code} value={job.code}>
            {job.name}
          </option>
        ))}
      </select>
    </label>
  );
}

function PositionTitleInput({
  positions,
  value,
  onChange,
  helpText,
  disabled = false,
}: {
  positions: PositionTitle[];
  value: string;
  onChange: (value: string) => void;
  helpText?: string;
  disabled?: boolean;
}) {
  const currentIsInactive =
    value !== "" &&
    !positions.some((position) => position.name === value && position.is_active);
  return (
    <label>
      직위
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
      >
        <option value="">미지정</option>
        {currentIsInactive ? (
          <option value={value}>{value} (현재 목록에서 삭제됨)</option>
        ) : null}
        {positions
          .filter((position) => position.is_active)
          .map((position) => (
            <option key={position.id} value={position.name}>
              {position.name}
            </option>
          ))}
      </select>
      {helpText ? <small className="field-help">{helpText}</small> : null}
    </label>
  );
}

function StaffReviewDraftForm({
  candidate,
  draft,
  units,
  jobs,
  positions,
  locked,
  saving,
  onIdentityChange,
  onCustomPairChange,
  onAssignmentChange,
  onNoteChange,
  onSave,
}: {
  candidate: StaffPersonCandidate;
  draft: StaffReviewDraftEditor;
  units: OrgUnit[];
  jobs: JobCode[];
  positions: PositionTitle[];
  locked: boolean;
  saving: boolean;
  onIdentityChange: (value: StaffReviewIdentityDecision) => void;
  onCustomPairChange: (value: string) => void;
  onAssignmentChange: (
    sourceKey: string,
    field: "department_name" | "job_code" | "position_title",
    value: string,
  ) => void;
  onNoteChange: (value: string) => void;
  onSave: () => void;
}) {
  const activeJobs = jobs.filter((job) => job.is_active);
  const departmentNames = Array.from(
    new Set(
      units
        .filter((unit) => unit.is_active && unit.unit_type === "department")
        .map((unit) => unit.name),
    ),
  ).sort((left, right) => left.localeCompare(right, "ko-KR"));
  const saved = candidate.review_draft;
  const hasUnsavedChanges = staffReviewEditorIsDirty(candidate, draft);
  const customPairGroup =
    draft.identity_decision === "custom_groups"
      ? draft.person_groups.find((group) => group.source_keys.length > 1)
      : undefined;
  const customPairIndexes = customPairGroup
    ? customPairGroup.source_keys
        .map((sourceKey) =>
          candidate.accounts.findIndex((account) => account.source_key === sourceKey),
        )
        .filter((index) => index >= 0)
        .sort((left, right) => left - right)
    : [];
  const customPairValue =
    customPairIndexes.length === 2
      ? `${customPairIndexes[0]}-${customPairIndexes[1]}`
      : "";
  const identityStructureReady =
    !candidate.requires_identity_confirmation ||
    (draft.identity_decision !== "pending" &&
      !(draft.identity_decision === "custom_groups" && draft.person_groups.length === 0));
  const currentCompletionIssues: string[] = [];
  if (candidate.requires_identity_confirmation && draft.identity_decision === "pending") {
    currentCompletionIssues.push("같은 이름 계정의 동일인 여부를 선택해 주세요.");
  }
  if (draft.identity_decision === "custom_groups" && draft.person_groups.length === 0) {
    currentCompletionIssues.push("같은 사람인 두 계정을 선택해 주세요.");
  }
  if (identityStructureReady) {
    candidate.accounts.forEach((account) => {
      if (!staffAccountNeedsCurrentAssignment(account)) return;
      const assignment = draft.account_assignments.find(
        (item) => item.source_key === account.source_key,
      );
      const department = account.organizations.find(
        (proposal) => proposal.unit_type === "department",
      );
      const label = residentServiceLabels[account.service_type] ?? account.service_type;
      if (
        department?.match_status === "manual" &&
        account.job.match_status === "matched" &&
        !assignment?.department_name
      ) {
        currentCompletionIssues.push(`${label} 계정의 부서를 선택해 주세요.`);
      }
      if (account.job.required_for_review && !assignment?.job_code) {
        currentCompletionIssues.push(`${label} 계정의 실제 자격 직종을 선택해 주세요.`);
      }
      if (
        account.position.manual_confirmation &&
        !assignment?.position_title
      ) {
        currentCompletionIssues.push(`${label} 계정의 직위를 선택해 주세요.`);
      }
    });
  }
  const uniqueCompletionIssues = Array.from(new Set(currentCompletionIssues));
  const savedStatus = hasUnsavedChanges
    ? "저장하지 않은 변경"
    : saved?.completion_status === "complete"
      ? "검토 완료"
      : saved
        ? `저장됨 · 추가 확인 ${uniqueCompletionIssues.length}개`
        : uniqueCompletionIssues.length > 0
          ? `확인 ${uniqueCompletionIssues.length}개 남음`
          : "저장 전";

  return (
    <section className="staff-review-draft" aria-label="관리자 검토 결정안">
      <div className="staff-review-draft-heading">
        <div>
          <strong>관리자 검토 결정안</strong>
          <span>확인한 판단만 저장할 수 있으며 실제 직원·로그인·권한에는 반영하지 않습니다.</span>
        </div>
        <span
          className={`staff-review-draft-status ${
            hasUnsavedChanges ? "dirty" : saved?.completion_status ?? "unsaved"
          }`}
        >
          {savedStatus}
        </span>
      </div>

      {candidate.requires_identity_confirmation ? (
        <div className="staff-review-identity">
          <label>
            동일인 여부
            <select
              disabled={locked}
              value={draft.identity_decision}
              onChange={(event) =>
                onIdentityChange(event.target.value as StaffReviewIdentityDecision)
              }
            >
              <option value="pending">선택 전</option>
              <option value="same_person">
                {candidate.account_count === 2 ? "두 계정은 같은 사람" : "모두 같은 사람"}
              </option>
              <option value="separate_people">
                {candidate.account_count === 2
                  ? "두 계정은 서로 다른 사람"
                  : "모두 각각 다른 사람"}
              </option>
              {candidate.account_count === 3 ? (
                <option value="custom_groups">일부 계정만 같은 사람</option>
              ) : null}
            </select>
          </label>

          {draft.identity_decision === "custom_groups" && candidate.account_count === 3 ? (
            <label>
              같은 사람인 두 계정
              <select
                disabled={locked}
                value={customPairValue}
                onChange={(event) => onCustomPairChange(event.target.value)}
              >
                <option value="">계정 묶음 선택</option>
                {[0, 1, 2].flatMap((left) =>
                  [0, 1, 2]
                    .filter((right) => right > left)
                    .map((right) => (
                      <option key={`${left}-${right}`} value={`${left}-${right}`}>
                        {residentServiceLabels[candidate.accounts[left].service_type] ??
                          candidate.accounts[left].service_type}
                        {" + "}
                        {residentServiceLabels[candidate.accounts[right].service_type] ??
                          candidate.accounts[right].service_type}
                      </option>
                    )),
                )}
              </select>
            </label>
          ) : null}
        </div>
      ) : null}

      <div className="staff-review-manual-assignments">
        {candidate.accounts.map((account) => {
          if (!identityStructureReady) return null;
          if (!staffAccountNeedsCurrentAssignment(account)) return null;
          const department = account.organizations.find(
            (proposal) => proposal.unit_type === "department",
          );
          const departmentRequired =
            department?.match_status === "manual" && account.job.match_status === "matched";
          const jobRequired = account.job.required_for_review;
          const positionRequired = account.position.manual_confirmation;
          if (!departmentRequired && !jobRequired && !positionRequired) return null;
          const assignment = draft.account_assignments.find(
            (item) => item.source_key === account.source_key,
          );
          const positionNames = Array.from(
            new Set(
              [
                ...positions
                  .filter((position) => position.is_active)
                  .map((position) => position.name),
                account.position.proposed_name,
              ].filter((value): value is string => Boolean(value)),
            ),
          );
          return (
            <fieldset key={account.source_key}>
              <legend>{staffReviewAccountLabel(account)}</legend>
              <div>
                {departmentRequired ? (
                  <label>
                    부서
                    <select
                      disabled={locked}
                      value={assignment?.department_name ?? ""}
                      onChange={(event) =>
                        onAssignmentChange(
                          account.source_key,
                          "department_name",
                          event.target.value,
                        )
                      }
                    >
                      <option value="">선택 전</option>
                      {departmentNames.map((name) => (
                        <option key={name} value={name}>{name}</option>
                      ))}
                    </select>
                  </label>
                ) : null}
                {jobRequired ? (
                  <label>
                    실제 자격 직종
                    <select
                      disabled={locked}
                      value={assignment?.job_code ?? ""}
                      onChange={(event) =>
                        onAssignmentChange(
                          account.source_key,
                          "job_code",
                          event.target.value,
                        )
                      }
                    >
                      <option value="">선택 전</option>
                      {activeJobs.map((job) => (
                        <option key={job.code} value={job.code}>{job.name}</option>
                      ))}
                    </select>
                  </label>
                ) : null}
                {positionRequired ? (
                  <label>
                    직위
                    <select
                      disabled={locked}
                      value={assignment?.position_title ?? ""}
                      onChange={(event) =>
                        onAssignmentChange(
                          account.source_key,
                          "position_title",
                          event.target.value,
                        )
                      }
                    >
                      <option value="">선택 전</option>
                      {positionNames.map((name) => (
                        <option key={name} value={name}>
                          {name === account.position.proposed_name ? `${name} (제안)` : name}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : null}
              </div>
            </fieldset>
          );
        })}
      </div>

      <label className="staff-review-note">
        관리자 메모(선택)
        <textarea
          disabled={locked}
          value={draft.note}
          onChange={(event) => onNoteChange(event.target.value)}
          maxLength={1000}
          rows={2}
          placeholder="판단 근거나 다음 확인사항을 적어 두세요."
        />
      </label>

      {uniqueCompletionIssues.length > 0 ? (
        <ul className="staff-review-issues">
          {uniqueCompletionIssues.map((issue) => <li key={issue}>{issue}</li>)}
        </ul>
      ) : null}

      <button
        type="button"
        className="button button-primary staff-review-save"
        disabled={
          locked ||
          (draft.identity_decision === "custom_groups" && draft.person_groups.length === 0)
        }
        onClick={onSave}
      >
        {saving ? "저장 중…" : "이 판단 저장"}
      </button>
    </section>
  );
}

function employeeLoginPayload(
  draft: EmployeeDraft,
  includeLegacyAssignment = true,
) {
  const accountPayload = {
    role: draft.role,
    can_process_records: draft.can_process_records,
  };
  if (!includeLegacyAssignment) return accountPayload;

  return {
    ...accountPayload,
    business_id: draft.business_id || null,
    department_id: draft.department_id || null,
    job_code: draft.job_code || null,
    position_title: draft.position_title.trim() || null,
    floor_id: null,
    team_id: draft.team_id || null,
  };
}

function draftFromEmployee(employee: StaffDirectoryEntry): EmployeeDraft {
  return {
    username: employee.username ?? "",
    full_name: employee.display_name,
    password: "",
    employee_code: employee.internal_code,
    role: employee.role ?? "staff",
    can_process_records: employee.can_process_records,
    business_id: employee.legacy_assignment.business?.id ?? "",
    department_id: employee.legacy_assignment.department?.id.toString() ?? "",
    job_code: employee.legacy_assignment.job_code ?? "",
    position_title: employee.legacy_assignment.position_title ?? "",
    team_id: employee.legacy_assignment.team?.id.toString() ?? "",
  };
}

function directStaffRegistrationPayload(draft: EmployeeDraft, issueLogin = false) {
  return {
    full_name: draft.full_name.trim(),
    employee_code: draft.employee_code.trim() || null,
    issue_login: issueLogin,
    ...(issueLogin
      ? {
          username: draft.username.trim(),
          temporary_password: draft.password,
          can_process_records: draft.can_process_records,
        }
      : {}),
  };
}

function staffIdentityPayload(draft: EmployeeDraft) {
  return {
    full_name: draft.full_name.trim(),
    employee_code: draft.employee_code.trim(),
  };
}

export function AdminDrawer({
  open,
  onClose,
  units,
  jobs,
  positionTitles,
  employees,
  staffDirectory,
  managedRooms,
  residents,
  currentUserId,
  onDataChanged,
  initialTab = "employees",
  reviewOnly = false,
  codedSyntheticMode = false,
}: {
  open: boolean;
  onClose: () => void;
  units: OrgUnit[];
  jobs: JobCode[];
  positionTitles: PositionTitle[];
  employees: User[];
  staffDirectory: StaffDirectoryEntry[];
  managedRooms: ManagedRoom[];
  residents: Resident[];
  currentUserId: string;
  onDataChanged: () => Promise<void>;
  initialTab?: "employees" | "organization";
  reviewOnly?: boolean;
  codedSyntheticMode?: boolean;
}) {
  const [tab, setTab] = useState<Tab>(initialTab);
  const [mobilePane, setMobilePane] = useState<"list" | "employee" | "room">("list");
  const [employeeDraft, setEmployeeDraft] = useState<EmployeeDraft>(emptyEmployee);
  const [issueLoginOnCreate, setIssueLoginOnCreate] = useState(false);
  const [loginIssueDraft, setLoginIssueDraft] = useState(emptyLoginIssue);
  const [selectedEmployeeId, setSelectedEmployeeId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<EmployeeDraft>(emptyEmployee);
  const [serviceAssignmentDrafts, setServiceAssignmentDrafts] =
    useState<ServiceAssignmentDrafts>(emptyServiceAssignmentDrafts);
  const [employeeQuery, setEmployeeQuery] = useState("");
  const [employeeStatusFilter, setEmployeeStatusFilter] =
    useState<EmployeeStatusFilter>("active");
  const [unitType, setUnitType] = useState<UnitType>("floor");
  const [unitName, setUnitName] = useState("");
  const [jobName, setJobName] = useState("");
  const [positionName, setPositionName] = useState("");
  const [roomName, setRoomName] = useState("");
  const [roomKind, setRoomKind] = useState<RoomKind>("custom");
  const [roomScopeUnitId, setRoomScopeUnitId] = useState("");
  const [roomJobCode, setRoomJobCode] = useState("");
  const [roomResidentScope, setRoomResidentScope] = useState<ResidentScope>("all");
  const [roomResidentScopeUnitId, setRoomResidentScopeUnitId] = useState("");
  const [roomMemberIds, setRoomMemberIds] = useState<string[]>([]);
  const [selectedRoomId, setSelectedRoomId] = useState<string | null>(null);
  const [roomEditorOpen, setRoomEditorOpen] = useState(false);
  const [roomMemberQuery, setRoomMemberQuery] = useState("");
  const [resetPassword, setResetPassword] = useState("");
  const [statusMessage, setStatusMessage] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [showInactiveRooms, setShowInactiveRooms] = useState(false);
  const [showInactiveOrganization, setShowInactiveOrganization] = useState(false);
  const [selectedLivingSpaceUnitId, setSelectedLivingSpaceUnitId] = useState<string | null>(null);
  const [livingSpaceDetails, setLivingSpaceDetails] = useState<LivingSpaceRoomDetails | null>(null);
  const [livingSpaceMemberIds, setLivingSpaceMemberIds] = useState<string[]>([]);
  const [roomQuery, setRoomQuery] = useState("");
  const [roomKindFilter, setRoomKindFilter] = useState<RoomKindFilter>("all");
  const [residentOrder, setResidentOrder] = useState<string[]>([]);
  const [residentDraft, setResidentDraft] = useState(emptyResidentDraft);
  const [residentSyncBatches, setResidentSyncBatches] = useState<ResidentSyncBatch[]>([]);
  const [residentSyncBatch, setResidentSyncBatch] = useState<ResidentSyncBatch | null>(null);
  const [selectedResidentSyncItemIds, setSelectedResidentSyncItemIds] =
    useState<string[]>([]);
  const [showUnchangedSyncItems, setShowUnchangedSyncItems] = useState(false);
  const [residentSyncLoading, setResidentSyncLoading] = useState(true);
  const [careforRosterStatus, setCareforRosterStatus] =
    useState<CareforRosterStatus | null>(null);
  const [staffSyncSource] = useState<CareforStaffSyncSource | null>(null);
  const [staffSyncBatches, setStaffSyncBatches] = useState<StaffSyncBatch[]>([]);
  const [staffSyncBatch, setStaffSyncBatch] = useState<StaffSyncBatch | null>(null);
  const [staffAssignmentPreview, setStaffAssignmentPreview] =
    useState<StaffAssignmentPreview | null>(null);
  const [staffApplicationPlan, setStaffApplicationPlan] =
    useState<StaffApplicationPlan | null>(null);
  const [staffReviewDrafts, setStaffReviewDrafts] = useState<
    Record<string, StaffReviewDraftEditor>
  >({});
  const [staffReviewSavingKey, setStaffReviewSavingKey] = useState<string | null>(null);
  const staffReviewSaveInFlightRef = useRef<string | null>(null);
  const [expandedStaffReviewCandidateKeys, setExpandedStaffReviewCandidateKeys] =
    useState<string[]>([]);
  const [staffAssignmentQuery, setStaffAssignmentQuery] = useState("");
  const [staffAssignmentReviewFilter, setStaffAssignmentReviewFilter] =
    useState<
      "all" | "pending_review" | "review_required" | StaffPersonCandidate["review_status"]
    >(
      "pending_review",
    );
  const [showUnchangedStaffSyncItems, setShowUnchangedStaffSyncItems] = useState(false);
  const [staffSyncQuery, setStaffSyncQuery] = useState("");
  const [staffSyncServiceFilter, setStaffSyncServiceFilter] =
    useState<"all" | "facility" | "daycare" | "homecare">("all");
  const [staffSyncStatusFilter, setStaffSyncStatusFilter] =
    useState<"all" | StaffSyncItem["incoming_payload"]["employment_status"]>("all");
  const [staffSyncJobFilter, setStaffSyncJobFilter] = useState("all");
  const [staffSyncLoading, setStaffSyncLoading] = useState(true);
  const employeeDetailRef = useRef<HTMLDivElement>(null);
  const roomListRef = useRef<HTMLDivElement>(null);
  const roomDetailRef = useRef<HTMLDivElement>(null);
  const drawerContentRef = useRef<HTMLDivElement>(null);
  const residentListRef = useRef<HTMLElement>(null);
  const residentSyncRef = useRef<HTMLElement>(null);
  const requestAdminCloseRef = useRef<() => void>(() => undefined);
  const closeAdminFromHistoryRef = useRef<() => void>(() => undefined);

  function installStaffAssignmentPreview(preview: StaffAssignmentPreview | null) {
    const previewEditors = preview ? staffReviewEditorsFromPreview(preview) : {};
    setStaffAssignmentPreview(preview);
    setStaffReviewDrafts(previewEditors);
    const reviewCandidates = preview?.candidates.filter((candidate) =>
      staffCandidateNeedsAttention(
        candidate,
        previewEditors[candidate.candidate_key],
      ),
    );
    const firstReviewCandidate =
      reviewCandidates?.find(
        (candidate) => candidate.review_draft?.completion_status !== "complete",
      ) ?? reviewCandidates?.[0];
    setExpandedStaffReviewCandidateKeys(
      firstReviewCandidate ? [firstReviewCandidate.candidate_key] : [],
    );
  }

  useEffect(() => {
    drawerContentRef.current?.scrollTo({ top: 0, behavior: "auto" });
  }, [tab]);

  useEffect(() => {
    if (!selectedEmployeeId) return;
    employeeDetailRef.current?.scrollTo({ top: 0, behavior: "auto" });
  }, [selectedEmployeeId]);

  useEffect(() => {
    if (!roomEditorOpen) return;
    roomDetailRef.current?.scrollTo({ top: 0, behavior: "auto" });
  }, [roomEditorOpen, selectedRoomId]);

  useEffect(() => {
    if (!open || tab !== "residents" || reviewOnly) return;
    if (codedSyntheticMode) return;
    let cancelled = false;
    void Promise.all([
      apiFetch<ResidentSyncBatch[]>("/api/admin/resident-sync/batches?limit=10"),
      apiFetch<CareforRosterStatus>("/api/admin/carefor-roster/status"),
    ])
      .then(([batches, rosterStatus]) => {
        if (cancelled) return;
        setResidentSyncBatches(batches.filter((batch) => !isPracticeResidentSyncBatch(batch)));
        setCareforRosterStatus(rosterStatus);
        setResidentSyncBatch(null);
        setSelectedResidentSyncItemIds([]);
      })
      .catch((reason) => {
        if (!cancelled) {
          setError(
            reason instanceof Error
              ? reason.message
              : "케어포 명단 상태를 불러오지 못했습니다.",
          );
        }
      })
      .finally(() => {
        if (!cancelled) setResidentSyncLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [codedSyntheticMode, open, reviewOnly, tab]);

  const selectedEmployee = useMemo(
    () =>
      staffDirectory.find((employee) => employee.staff_id === selectedEmployeeId) ?? null,
    [staffDirectory, selectedEmployeeId],
  );
  const selectedCurrentServiceAssignments = useMemo(
    () => (selectedEmployee ? currentServiceAssignments(selectedEmployee) : []),
    [selectedEmployee],
  );
  const selectedPastServiceAssignments = useMemo(
    () =>
      selectedEmployee?.service_assignments
        .filter((assignment) => !assignment.is_current)
        .sort((left, right) => right.start_date.localeCompare(left.start_date)) ?? [],
    [selectedEmployee],
  );
  const hasActiveRealTeam = useMemo(
    () =>
      units.some(
        (unit) =>
          unit.unit_type === "team" && unit.is_active && !unit.is_test_data,
      ),
    [units],
  );
  // eslint-disable-next-line @typescript-eslint/no-unused-vars -- 조직 편집 재연결 시 실제 팀만 노출하는 계약을 보존합니다.
  const creatableStaffUnitTypes = useMemo(
    () =>
      staffUnitTypes.filter(
        (type) => type !== "team" || hasActiveRealTeam,
      ),
    [hasActiveRealTeam],
  );
  const positionCleanupCandidates = useMemo(
    () =>
      staffDirectory.filter(
        (employee) =>
          employee.login_user_id !== null &&
          employee.employment_status === "active" &&
          employee.legacy_assignment.job_code !== null &&
          legacyPositionByJobCode[employee.legacy_assignment.job_code] !== undefined,
      ),
    [staffDirectory],
  );
  const selectedRoom = useMemo(
    () => managedRooms.find((room) => room.id === selectedRoomId) ?? null,
    [managedRooms, selectedRoomId],
  );
  const roomHasMemberChanges = useMemo(
    () =>
      selectedRoom
        ? !sameIdSet(roomMemberIds, selectedRoom.member_ids)
        : roomMemberIds.length > 0,
    [roomMemberIds, selectedRoom],
  );
  const roomAddedMemberCount = useMemo(
    () =>
      selectedRoom
        ? roomMemberIds.filter((id) => !selectedRoom.member_ids.includes(id)).length
        : roomMemberIds.length,
    [roomMemberIds, selectedRoom],
  );
  const roomRemovedMemberCount = useMemo(
    () =>
      selectedRoom
        ? selectedRoom.member_ids.filter((id) => !roomMemberIds.includes(id)).length
        : 0,
    [roomMemberIds, selectedRoom],
  );
  const roomEmployeeById = useMemo(
    () => new Map(employees.map((employee) => [employee.id, employee])),
    [employees],
  );
  const selectedRoomMembers = useMemo(
    () =>
      roomMemberIds.flatMap((id) => {
        const employee = roomEmployeeById.get(id);
        return employee ? [employee] : [];
      }),
    [roomEmployeeById, roomMemberIds],
  );
  const addedRoomMembers = useMemo(
    () =>
      selectedRoom
        ? roomMemberIds
            .filter((id) => !selectedRoom.member_ids.includes(id))
            .flatMap((id) => {
              const employee = roomEmployeeById.get(id);
              return employee ? [employee] : [];
            })
        : [],
    [roomEmployeeById, roomMemberIds, selectedRoom],
  );
  const removedRoomMembers = useMemo(
    () =>
      selectedRoom
        ? selectedRoom.member_ids
            .filter((id) => !roomMemberIds.includes(id))
            .flatMap((id) => {
              const employee = roomEmployeeById.get(id);
              return employee ? [employee] : [];
            })
        : [],
    [roomEmployeeById, roomMemberIds, selectedRoom],
  );
  const hasUnsavedRoomChanges = useMemo(() => {
    if (!roomEditorOpen) return false;
    if (!selectedRoom) {
      return (
        roomName.trim().length > 0 ||
        roomKind !== "custom" ||
        roomScopeUnitId !== "" ||
        roomJobCode !== "" ||
        roomResidentScope !== "all" ||
        roomResidentScopeUnitId !== "" ||
        roomMemberIds.length > 0
      );
    }
    return (
      roomName !== selectedRoom.name ||
      roomResidentScope !== selectedRoom.resident_scope ||
      roomResidentScopeUnitId !== (selectedRoom.resident_scope_unit_id ?? "") ||
      roomHasMemberChanges
    );
  }, [
    roomEditorOpen,
    roomHasMemberChanges,
    roomJobCode,
    roomKind,
    roomMemberIds.length,
    roomName,
    roomResidentScope,
    roomResidentScopeUnitId,
    roomScopeUnitId,
    selectedRoom,
  ]);
  const hasUnsavedOrganizationDraft =
    unitName.trim().length > 0 ||
    jobName.trim().length > 0 ||
    positionName.trim().length > 0;
  const visibleEmployees = useMemo(() => {
    const query = employeeQuery.trim().toLocaleLowerCase("ko-KR");
    return staffDirectory.filter((employee) => {
      if (
        employeeStatusFilter !== "all" &&
        employee.employment_status !== employeeStatusFilter
      ) {
        return false;
      }
      if (!query) return true;
      return [
        employee.display_name,
        employee.username,
        employee.internal_code,
        employee.legacy_assignment.business?.name,
        employee.legacy_assignment.department?.name,
        employee.legacy_assignment.floor?.name,
        employee.legacy_assignment.team?.name,
        employee.legacy_assignment.job_name,
        employee.legacy_assignment.position_title,
        ...employee.service_assignments.flatMap((assignment) => [
          residentServiceLabels[assignment.service_type],
          assignment.business?.name,
          assignment.department?.name,
          assignment.team?.name,
          assignment.job_name,
          assignment.job_title_snapshot,
          assignment.position_title_snapshot,
        ]),
      ]
        .filter(Boolean)
        .some((value) => value!.toLocaleLowerCase("ko-KR").includes(query));
    });
  }, [employeeQuery, employeeStatusFilter, staffDirectory]);
  const visibleRooms = useMemo(() => {
    const query = roomQuery.trim().toLocaleLowerCase("ko-KR");
    return managedRooms.filter((room) => {
      if (room.kind === "living_space") return false;
      if (!showInactiveRooms && !room.is_active) return false;
      if (roomKindFilter !== "all" && room.kind !== roomKindFilter) return false;
      if (!query) return true;
      return [room.name, room.scope_name, room.job_name, roomRuleLabel(room)]
        .filter(Boolean)
        .some((value) => value!.toLocaleLowerCase("ko-KR").includes(query));
    });
  }, [managedRooms, roomKindFilter, roomQuery, showInactiveRooms]);
  const roomCandidates = useMemo(() => {
    const query = roomMemberQuery.trim().toLocaleLowerCase("ko-KR");
    if (!query) return [];
    return employees
      .filter((employee) => employee.employment_status === "active")
      .filter((employee) => {
        return [
          employee.full_name,
          employee.username,
          employee.business?.name,
          employee.department?.name,
          employee.floor?.name,
          employee.team?.name,
          employee.job_name,
          employee.position_title,
        ]
          .filter(Boolean)
          .some((value) => value!.toLocaleLowerCase("ko-KR").includes(query));
      });
  }, [employees, roomMemberQuery]);
  const effectiveResidentOrder = useMemo(() => {
    const residentIds = new Set(residents.map((resident) => resident.id));
    const retained = residentOrder.filter((id) => residentIds.has(id));
    const retainedIds = new Set(retained);
    return [
      ...retained,
      ...residents
        .map((resident) => resident.id)
        .filter((id) => !retainedIds.has(id)),
    ];
  }, [residentOrder, residents]);
  const visibleResidentSyncItems = useMemo(() => {
    if (!residentSyncBatch) return [];
    return residentSyncBatch.items.filter(
      (item) => showUnchangedSyncItems || item.change_type !== "unchanged",
    );
  }, [residentSyncBatch, showUnchangedSyncItems]);
  const pendingResidentSyncItems = useMemo(
    () =>
      residentSyncBatch?.items.filter(
        (item) =>
          item.status === "pending" &&
          ["new", "update", "deactivate"].includes(item.change_type),
      ) ?? [],
    [residentSyncBatch],
  );
  const residentSyncHasNoChanges =
    residentSyncBatch !== null &&
    residentSyncBatch.status !== "applied" &&
    pendingResidentSyncItems.length === 0 &&
    (residentSyncBatch.summary.conflict ?? 0) === 0;
  const residentAttentionCount =
    pendingResidentSyncItems.length + (residentSyncBatch?.summary.conflict ?? 0);
  const residentSyncIsFullyComplete =
    residentSyncBatch?.status === "applied" &&
    (residentSyncBatch.summary.conflict ?? 0) === 0;
  const staffSyncJobOptions = useMemo(
    () =>
      Array.from(
        new Set(
          staffSyncBatch?.items
            .map((item) => item.incoming_payload.job_name)
            .filter(Boolean) ?? [],
        ),
      ).sort((left, right) => left.localeCompare(right, "ko-KR")),
    [staffSyncBatch],
  );
  const staffSyncNameCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of staffSyncBatch?.items ?? []) {
      const name = item.incoming_payload.display_name;
      counts.set(name, (counts.get(name) ?? 0) + 1);
    }
    return counts;
  }, [staffSyncBatch]);
  const staffSyncServiceSummary = useMemo(() => {
    const summary = {
      facility: { active: 0, leave: 0, retired: 0, unknown: 0 },
      daycare: { active: 0, leave: 0, retired: 0, unknown: 0 },
      homecare: { active: 0, leave: 0, retired: 0, unknown: 0 },
    };
    for (const item of staffSyncBatch?.items ?? []) {
      if (!(item.service_type in summary)) continue;
      const service = item.service_type as keyof typeof summary;
      const status = item.incoming_payload.employment_status;
      summary[service][status] += 1;
    }
    return summary;
  }, [staffSyncBatch]);
  const visibleStaffSyncItems = useMemo(() => {
    if (!staffSyncBatch) return [];
    const query = staffSyncQuery.trim().toLocaleLowerCase("ko-KR");
    return staffSyncBatch.items
      .filter((item) => showUnchangedStaffSyncItems || item.change_type !== "unchanged")
      .filter(
        (item) =>
          staffSyncServiceFilter === "all" || item.service_type === staffSyncServiceFilter,
      )
      .filter(
        (item) =>
          staffSyncStatusFilter === "all" ||
          item.incoming_payload.employment_status === staffSyncStatusFilter,
      )
      .filter(
        (item) =>
          staffSyncJobFilter === "all" || item.incoming_payload.job_name === staffSyncJobFilter,
      )
      .filter((item) => {
        if (!query) return true;
        return [
          item.incoming_payload.display_name,
          item.incoming_payload.job_name,
          item.external_id,
        ].some((value) => value.toLocaleLowerCase("ko-KR").includes(query));
      })
      .sort((left, right) => {
        const nameOrder = left.incoming_payload.display_name.localeCompare(
          right.incoming_payload.display_name,
          "ko-KR",
        );
        return nameOrder || left.service_type.localeCompare(right.service_type, "ko-KR");
      });
  }, [
    showUnchangedStaffSyncItems,
    staffSyncBatch,
    staffSyncJobFilter,
    staffSyncQuery,
    staffSyncServiceFilter,
    staffSyncStatusFilter,
  ]);
  const visibleStaffAssignmentCandidates = useMemo(() => {
    if (!staffAssignmentPreview) return [];
    const query = staffAssignmentQuery.trim().toLocaleLowerCase("ko-KR");
    return staffAssignmentPreview.candidates
      .filter(
        (candidate) => {
          if (staffAssignmentReviewFilter === "all") return true;
          if (staffAssignmentReviewFilter === "pending_review") {
            const editor = staffReviewDrafts[candidate.candidate_key];
            return staffCandidateNeedsAttention(candidate, editor);
          }
          if (staffAssignmentReviewFilter === "review_required") {
            return ["identity_review", "assignment_review"].includes(
              candidate.review_status,
            );
          }
          return candidate.review_status === staffAssignmentReviewFilter;
        },
      )
      .filter((candidate) => {
        if (!query) return true;
        const values = [
          candidate.display_name,
          ...candidate.accounts.flatMap((account) => [
            account.external_id,
            account.source_job_name,
            residentServiceLabels[account.service_type] ?? account.service_type,
            ...account.organizations.map((proposal) => proposal.proposed_name ?? ""),
          ]),
        ];
        return values.some((value) =>
          value.toLocaleLowerCase("ko-KR").includes(query),
        );
      });
  }, [
    staffAssignmentPreview,
    staffAssignmentQuery,
    staffAssignmentReviewFilter,
    staffReviewDrafts,
  ]);
  const staffAttentionCandidates = useMemo(
    () =>
      staffAssignmentPreview?.candidates.filter((candidate) =>
        staffCandidateNeedsAttention(
          candidate,
          staffReviewDrafts[candidate.candidate_key],
        ),
      ) ?? [],
    [staffAssignmentPreview, staffReviewDrafts],
  );
  const hasUnsavedStaffReviewChanges = useMemo(() => {
    if (!staffAssignmentPreview) return false;
    return staffAssignmentPreview.candidates.some((candidate) => {
      const current = staffReviewDrafts[candidate.candidate_key];
      if (!current) return false;
      return staffReviewEditorIsDirty(candidate, current);
    });
  }, [staffAssignmentPreview, staffReviewDrafts]);
  const hasUnsavedEmployeeChanges = useMemo(() => {
    if (!selectedEmployee) return false;
    const includeLegacyAssignment = selectedEmployee.service_assignments.length === 0;
    const identityChanged =
      JSON.stringify(staffIdentityPayload(editDraft)) !==
      JSON.stringify(staffIdentityPayload(draftFromEmployee(selectedEmployee)));
    const accountChanged =
      selectedEmployee.login_user_id !== null &&
      JSON.stringify(employeeLoginPayload(editDraft, includeLegacyAssignment)) !==
      JSON.stringify(
        employeeLoginPayload(
          draftFromEmployee(selectedEmployee),
          includeLegacyAssignment,
        ),
      );
    const serviceAssignmentsChanged =
      selectedEmployee.employment_status === "active" &&
      JSON.stringify(serviceAssignmentsPayload(serviceAssignmentDrafts)) !==
        JSON.stringify(
          serviceAssignmentsPayload(
            serviceAssignmentDraftsFromEmployee(selectedEmployee),
          ),
        );
    return identityChanged || accountChanged || serviceAssignmentsChanged;
  }, [editDraft, selectedEmployee, serviceAssignmentDrafts]);
  const hasUnsavedNewEmployeeChanges =
    issueLoginOnCreate ||
    JSON.stringify(directStaffRegistrationPayload(employeeDraft, issueLoginOnCreate)) !==
      JSON.stringify(directStaffRegistrationPayload(emptyEmployee));
  const hasUnsavedResidentOrderChanges =
    residentOrder.length > 0 &&
    !sameIdOrder(
      effectiveResidentOrder,
      residents.map((resident) => resident.id),
    );
  const hasUnsavedResidentDraft =
    residentDraft.display_name.trim().length > 0 ||
    residentDraft.service_type !== emptyResidentDraft.service_type ||
    residentDraft.floor_id !== emptyResidentDraft.floor_id;
  const hasUnsavedAdminChanges =
    hasUnsavedEmployeeChanges ||
    hasUnsavedNewEmployeeChanges ||
    hasUnsavedResidentOrderChanges ||
    hasUnsavedResidentDraft ||
    hasUnsavedStaffReviewChanges ||
    hasUnsavedRoomChanges ||
    (tab === "organization" && hasUnsavedOrganizationDraft);

  useEffect(() => {
    if (!hasUnsavedAdminChanges) return;
    const protectUnsavedAdminChanges = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", protectUnsavedAdminChanges);
    return () => window.removeEventListener("beforeunload", protectUnsavedAdminChanges);
  }, [hasUnsavedAdminChanges]);

  function resetRoomDraft() {
    setMobilePane("list");
    setRoomEditorOpen(false);
    setSelectedRoomId(null);
    setRoomName("");
    setRoomKind("custom");
    setRoomScopeUnitId("");
    setRoomJobCode("");
    setRoomResidentScope("all");
    setRoomResidentScopeUnitId("");
    setRoomMemberIds([]);
    setRoomMemberQuery("");
  }

  function discardAdminDrafts() {
    resetRoomDraft();
    setUnitName("");
    setJobName("");
    setPositionName("");
    setEditDraft(
      selectedEmployee ? draftFromEmployee(selectedEmployee) : emptyEmployee,
    );
    setServiceAssignmentDrafts(
      selectedEmployee
        ? serviceAssignmentDraftsFromEmployee(selectedEmployee)
        : emptyServiceAssignmentDrafts(),
    );
    setResetPassword("");
    setEmployeeDraft(emptyEmployee);
    setIssueLoginOnCreate(false);
    setLoginIssueDraft(emptyLoginIssue);
    setResidentOrder([]);
    setResidentDraft(emptyResidentDraft);
    setStaffReviewDrafts(
      staffAssignmentPreview
        ? staffReviewEditorsFromPreview(staffAssignmentPreview)
        : {},
    );
  }

  function openNewRoomDraft() {
    if (!confirmDiscardRoomChanges()) return;
    resetRoomDraft();
    setRoomEditorOpen(true);
    setMobilePane("room");
    revealDetailOnSmallScreen(roomDetailRef);
  }

  function openManagedRoom(room: ManagedRoom) {
    if (room.kind === "living_space") return;
    if (roomEditorOpen && selectedRoomId === room.id) {
      revealDetailOnSmallScreen(roomDetailRef);
      return;
    }
    if (!confirmDiscardRoomChanges()) return;
    setRoomEditorOpen(true);
    setMobilePane("room");
    setSelectedRoomId(room.id);
    setRoomName(room.name);
    setRoomKind(room.kind);
    setRoomScopeUnitId(room.scope_unit_id ?? "");
    setRoomJobCode(room.job_code ?? "");
    setRoomResidentScope(room.resident_scope);
    setRoomResidentScopeUnitId(room.resident_scope_unit_id ?? "");
    setRoomMemberIds(room.member_ids);
    setRoomMemberQuery("");
    setError("");
    setStatusMessage("");
    revealDetailOnSmallScreen(roomDetailRef);
  }

  function revealDetailOnSmallScreen(ref: RefObject<HTMLDivElement | null>) {
    if (!window.matchMedia("(max-width: 720px)").matches) return;
    window.requestAnimationFrame(() => {
      ref.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  function moveResident(residentId: string, direction: -1 | 1) {
    const resident = residents.find((item) => item.id === residentId);
    if (!resident) return;
    setResidentOrder((current) => {
      const currentIds = current.length > 0 ? current : residents.map((item) => item.id);
      const sameGroup = currentIds.filter(
        (id) => {
          const item = residents.find((candidate) => candidate.id === id);
          return item?.service_type === resident.service_type;
        },
      );
      const groupIndex = sameGroup.indexOf(residentId);
      const targetId = sameGroup[groupIndex + direction];
      if (!targetId) return current;
      const next = [...currentIds];
      const currentIndex = next.indexOf(residentId);
      const targetIndex = next.indexOf(targetId);
      [next[currentIndex], next[targetIndex]] = [next[targetIndex], next[currentIndex]];
      return next;
    });
  }

  async function run(action: () => Promise<void>, successMessage: string) {
    if (reviewOnly) {
      setStatusMessage("");
      setError(
        "멘토 검토 계정에서는 실제 관리 구조를 볼 수 있지만 추가·변경·삭제는 안전하게 잠겨 있습니다.",
      );
      return;
    }
    setSaving(true);
    setError("");
    setStatusMessage("");
    try {
      await action();
      await onDataChanged();
      setStatusMessage(successMessage);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "요청을 처리하지 못했습니다.");
    } finally {
      setSaving(false);
    }
  }

  async function refreshResidentSyncHistory() {
    const batches = await apiFetch<ResidentSyncBatch[]>(
      "/api/admin/resident-sync/batches?limit=10",
    );
    setResidentSyncBatches(batches.filter((batch) => !isPracticeResidentSyncBatch(batch)));
  }

  async function previewCareforRoster(
    serviceType: "facility" | "daycare" | "homecare",
  ) {
    await run(async () => {
      const formData = new FormData();
      formData.append("service_type", serviceType);
      const batch = await apiFetch<ResidentSyncBatch>(
        "/api/admin/carefor-roster/preview",
        {
          method: "POST",
          body: formData,
        },
      );
      setResidentSyncBatch(batch);
      setSelectedResidentSyncItemIds(
        batch.items
          .filter(
            (item) =>
              item.status === "pending" && ["new", "update"].includes(item.change_type),
          )
          .map((item) => item.id),
      );
      await refreshResidentSyncHistory();
    }, `${residentServiceLabels[serviceType]} 최신 명단을 불러왔습니다. 바뀐 사람만 확인해 주세요.`);
  }

  async function addResident(event: FormEvent) {
    event.preventDefault();
    await run(async () => {
      await apiFetch("/api/admin/residents", {
        method: "POST",
        body: JSON.stringify({
          display_name: residentDraft.display_name.trim(),
          service_type: residentDraft.service_type,
          floor_id: residentDraft.floor_id || null,
        }),
      });
      setResidentDraft(emptyResidentDraft);
    }, "어르신을 현재 명단에 추가했습니다.");
  }

  async function openResidentSyncBatch(batchId: string) {
    setResidentSyncLoading(true);
    setError("");
    try {
      const batch = await apiFetch<ResidentSyncBatch>(
        `/api/admin/resident-sync/batches/${batchId}`,
      );
      setResidentSyncBatch(batch);
      setSelectedResidentSyncItemIds([]);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "동기화 미리보기를 불러오지 못했습니다.",
      );
    } finally {
      setResidentSyncLoading(false);
    }
  }

  async function applySelectedResidentSyncItems() {
    if (!residentSyncBatch || selectedResidentSyncItemIds.length === 0) {
      setError("반영할 변경사항을 먼저 선택해 주세요.");
      return;
    }
    const selectedItems = residentSyncBatch.items.filter((item) =>
      selectedResidentSyncItemIds.includes(item.id),
    );
    const deactivationCount = selectedItems.filter(
      (item) => item.change_type === "deactivate",
    ).length;
    if (
      deactivationCount > 0 &&
      !window.confirm(
        `${deactivationCount}명의 이용 중지를 포함해 선택한 변경을 반영할까요?\n` +
          "이용 중지는 삭제가 아니며 채팅의 어르신 선택 목록에서 숨겨집니다.",
      )
    ) {
      return;
    }
    await run(async () => {
      const batch = await apiFetch<ResidentSyncBatch>(
        `/api/admin/resident-sync/batches/${residentSyncBatch.id}/apply`,
        {
          method: "POST",
          body: JSON.stringify({ item_ids: selectedResidentSyncItemIds }),
        },
      );
      setResidentSyncBatch(batch);
      setSelectedResidentSyncItemIds([]);
      await refreshResidentSyncHistory();
    }, "선택한 어르신 변경사항을 승인하고 반영했습니다.");
  }

  async function openLivingSpaceMembers(unit: OrgUnit) {
    setSaving(true);
    setError("");
    setStatusMessage("");
    try {
      const details = await apiFetch<LivingSpaceRoomDetails>(
        `/api/org-units/${unit.id}/living-space-members`,
      );
      setSelectedLivingSpaceUnitId(unit.id);
      setLivingSpaceDetails(details);
      setLivingSpaceMemberIds(details.member_ids);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "참여 직원을 불러오지 못했습니다.");
    } finally {
      setSaving(false);
    }
  }

  async function refreshStaffSyncHistory() {
    const batches = await apiFetch<StaffSyncBatch[]>(
      "/api/admin/carefor-staff-sync/batches?limit=10",
    );
    setStaffSyncBatches(batches);
  }

  async function previewCareforStaff() {
    await run(async () => {
      const batch = await apiFetch<StaffSyncBatch>(
        "/api/admin/carefor-staff-sync/preview",
        { method: "POST", body: "{}" },
      );
      const [assignmentPreview, applicationPlan] = await Promise.all([
        apiFetch<StaffAssignmentPreview>(
          `/api/admin/carefor-staff-sync/batches/${batch.id}/assignment-preview`,
        ),
        fetchStaffApplicationPlan(batch.id),
      ]);
      setStaffSyncBatch(batch);
      installStaffAssignmentPreview(assignmentPreview);
      setStaffApplicationPlan(applicationPlan);
      await refreshStaffSyncHistory();
    }, "케어포 직원 변경 미리보기를 만들었습니다. 아직 실제 직원정보에는 반영되지 않았습니다.");
  }

  async function openStaffSyncBatch(batchId: string) {
    setStaffSyncLoading(true);
    setError("");
    try {
      const [batch, assignmentPreview, applicationPlan] = await Promise.all([
        apiFetch<StaffSyncBatch>(`/api/admin/carefor-staff-sync/batches/${batchId}`),
        apiFetch<StaffAssignmentPreview>(
          `/api/admin/carefor-staff-sync/batches/${batchId}/assignment-preview`,
        ),
        fetchStaffApplicationPlan(batchId),
      ]);
      setStaffSyncBatch(batch);
      installStaffAssignmentPreview(assignmentPreview);
      setStaffApplicationPlan(applicationPlan);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "직원 동기화 미리보기를 불러오지 못했습니다.",
      );
    } finally {
      setStaffSyncLoading(false);
    }
  }

  function updateStaffReviewDraft(
    candidateKey: string,
    updater: (current: StaffReviewDraftEditor) => StaffReviewDraftEditor,
  ) {
    setStaffReviewDrafts((current) => {
      const draft = current[candidateKey];
      if (!draft) return current;
      return { ...current, [candidateKey]: updater(draft) };
    });
  }

  function setStaffReviewIdentity(
    candidate: StaffPersonCandidate,
    identityDecision: StaffReviewIdentityDecision,
  ) {
    let personGroups: StaffReviewPersonGroup[] = [];
    if (identityDecision === "same_person") {
      personGroups = [
        {
          source_keys: candidate.accounts.map((account) => account.source_key),
          primary_source_key: null,
          primary_job_code: null,
          primary_position_title: null,
        },
      ];
    } else if (identityDecision === "separate_people") {
      personGroups = candidate.accounts.map((account) => ({
        source_keys: [account.source_key],
        primary_source_key: null,
        primary_job_code: null,
        primary_position_title: null,
      }));
    }
    updateStaffReviewDraft(candidate.candidate_key, (current) => ({
      ...current,
      identity_decision: identityDecision,
      person_groups: personGroups,
    }));
  }

  function setStaffReviewCustomPair(
    candidate: StaffPersonCandidate,
    pairValue: string,
  ) {
    const pairIndexes = pairValue
      .split("-")
      .map((value) => Number.parseInt(value, 10))
      .filter((value) => Number.isInteger(value));
    const pairSourceKeys = pairIndexes
      .map((index) => candidate.accounts[index]?.source_key)
      .filter((value): value is string => Boolean(value));
    const personGroups: StaffReviewPersonGroup[] =
      pairSourceKeys.length === 2
        ? [
            {
              source_keys: pairSourceKeys,
              primary_source_key: null,
              primary_job_code: null,
              primary_position_title: null,
            },
            ...candidate.accounts
              .filter((account) => !pairSourceKeys.includes(account.source_key))
              .map((account) => ({
                source_keys: [account.source_key],
                primary_source_key: null,
                primary_job_code: null,
                primary_position_title: null,
              })),
          ]
        : [];
    updateStaffReviewDraft(candidate.candidate_key, (current) => ({
      ...current,
      person_groups: personGroups,
    }));
  }

  function updateStaffReviewAssignment(
    candidateKey: string,
    sourceKey: string,
    field: "department_name" | "job_code" | "position_title",
    value: string,
  ) {
    updateStaffReviewDraft(candidateKey, (current) => ({
      ...current,
      account_assignments: current.account_assignments.map((assignment) =>
        assignment.source_key === sourceKey
          ? { ...assignment, [field]: value || null }
          : assignment,
      ),
    }));
  }

  async function saveStaffReviewDraft(candidate: StaffPersonCandidate) {
    if (reviewOnly) {
      setError(
        "멘토 검토 계정에서는 직원·권한 구조를 볼 수 있지만 검토 결정 저장은 잠겨 있습니다.",
      );
      return;
    }
    if (!staffSyncBatch) return;
    if (staffReviewSaveInFlightRef.current) return;
    const draft = staffReviewDrafts[candidate.candidate_key];
    if (!draft) return;
    staffReviewSaveInFlightRef.current = candidate.candidate_key;
    setStaffReviewSavingKey(candidate.candidate_key);
    setError("");
    setStatusMessage("");
    try {
      const saved = await apiFetch<StaffSyncReviewDraft>(
        `/api/admin/carefor-staff-sync/batches/${staffSyncBatch.id}/review-drafts/${encodeURIComponent(
          candidate.candidate_key,
        )}`,
        {
          method: "PUT",
          body: JSON.stringify(draft),
        },
      );
      const [refreshed, applicationPlan] = await Promise.all([
        apiFetch<StaffAssignmentPreview>(
          `/api/admin/carefor-staff-sync/batches/${staffSyncBatch.id}/assignment-preview`,
        ),
        fetchStaffApplicationPlan(staffSyncBatch.id),
      ]);
      setStaffAssignmentPreview(refreshed);
      setStaffApplicationPlan(applicationPlan);
      setStaffReviewDrafts((current) => {
        const hydrated = staffReviewEditorsFromPreview(refreshed);
        return Object.fromEntries(
          Object.entries(hydrated).map(([candidateKey, editor]) => [
            candidateKey,
            candidateKey === candidate.candidate_key
              ? editor
              : current[candidateKey] ?? editor,
          ]),
        );
      });
      setStatusMessage(
        saved.completion_status === "complete"
          ? "확인한 판단을 저장했습니다. 필요한 확인 항목이 모두 채워졌습니다."
          : "확인한 판단을 저장했습니다. 남은 항목은 나중에 이어서 입력할 수 있습니다.",
      );
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "확인한 판단을 저장하지 못했습니다.",
      );
    } finally {
      staffReviewSaveInFlightRef.current = null;
      setStaffReviewSavingKey(null);
    }
  }

  async function addEmployee(event: FormEvent) {
    event.preventDefault();
    await run(async () => {
      const created = await apiFetch<StaffDirectoryEntry>("/api/admin/staff-directory", {
        method: "POST",
        body: JSON.stringify(
          directStaffRegistrationPayload(employeeDraft, issueLoginOnCreate),
        ),
      });
      setSelectedEmployeeId(created.staff_id);
      setEditDraft(draftFromEmployee(created));
      setServiceAssignmentDrafts(emptyServiceAssignmentDrafts());
      setEmployeeDraft(emptyEmployee);
      setIssueLoginOnCreate(false);
      setTab("employees");
      setMobilePane("employee");
    }, issueLoginOnCreate
      ? "직원과 로그인 계정을 함께 등록했습니다. 임시 비밀번호는 처음 로그인할 때 변경해야 합니다."
      : "직원을 등록했습니다. 근무 서비스와 직종·직위는 직원 상세에서 설정해 주세요.");
  }

  async function issueEmployeeLogin() {
    if (!selectedEmployee) return;
    await run(async () => {
      const updated = await apiFetch<StaffDirectoryEntry>(
        `/api/admin/staff-directory/${selectedEmployee.staff_id}/login`,
        {
          method: "POST",
          body: JSON.stringify(loginIssueDraft),
        },
      );
      setEditDraft(draftFromEmployee(updated));
      setServiceAssignmentDrafts(serviceAssignmentDraftsFromEmployee(updated));
      setLoginIssueDraft(emptyLoginIssue);
    }, "로그인 계정을 발급했습니다. 임시 비밀번호는 처음 로그인할 때 변경해야 합니다.");
  }

  async function updateEmployee(event: FormEvent) {
    event.preventDefault();
    if (!selectedEmployee) return;
    const includeLegacyAssignment = selectedEmployee.service_assignments.length === 0;
    const identityChanged =
      JSON.stringify(staffIdentityPayload(editDraft)) !==
      JSON.stringify(staffIdentityPayload(draftFromEmployee(selectedEmployee)));
    const accountChanged =
      selectedEmployee.login_user_id !== null &&
      JSON.stringify(employeeLoginPayload(editDraft, includeLegacyAssignment)) !==
        JSON.stringify(
          employeeLoginPayload(
            draftFromEmployee(selectedEmployee),
            includeLegacyAssignment,
          ),
        );
    const assignmentsPayload = serviceAssignmentsPayload(serviceAssignmentDrafts);
    const serviceAssignmentsChanged =
      selectedEmployee.employment_status === "active" &&
      JSON.stringify(assignmentsPayload) !==
        JSON.stringify(
          serviceAssignmentsPayload(
            serviceAssignmentDraftsFromEmployee(selectedEmployee),
          ),
        );
    if (serviceAssignmentsChanged && assignmentsPayload.assignments.length === 0) {
      setError("근무 중인 서비스를 한 개 이상 선택해 주세요.");
      return;
    }
    if (!identityChanged && !accountChanged && !serviceAssignmentsChanged) {
      setStatusMessage("변경된 직원정보가 없습니다.");
      return;
    }
    await run(async () => {
      if (identityChanged) {
        await apiFetch(`/api/admin/staff-directory/${selectedEmployee.staff_id}`, {
          method: "PATCH",
          body: JSON.stringify(staffIdentityPayload(editDraft)),
        });
      }
      if (accountChanged && selectedEmployee.login_user_id !== null) {
        await apiFetch(`/api/employees/${selectedEmployee.login_user_id}`, {
          method: "PATCH",
          body: JSON.stringify(
            employeeLoginPayload(editDraft, includeLegacyAssignment),
          ),
        });
      }
      if (serviceAssignmentsChanged) {
        await apiFetch(
          `/api/admin/staff-directory/${selectedEmployee.staff_id}/service-assignments`,
          {
            method: "PATCH",
            body: JSON.stringify(assignmentsPayload),
          },
        );
      }
    }, "서비스별 직종·직위와 직원정보를 저장했습니다.");
  }

  function confirmDiscardStaffReviewChanges() {
    return (
      !hasUnsavedStaffReviewChanges ||
      window.confirm(
        "아직 저장하지 않은 판단이 있습니다. 저장하지 않고 이동할까요?",
      )
    );
  }

  function confirmDiscardEmployeeChanges() {
    return (
      !hasUnsavedEmployeeChanges ||
      window.confirm(
        "직원정보에 저장하지 않은 변경이 있습니다. 저장하지 않고 이동할까요?",
      )
    );
  }

  function confirmDiscardNewEmployeeChanges() {
    return (
      !hasUnsavedNewEmployeeChanges ||
      window.confirm(
        "직원 등록에 아직 저장하지 않은 내용이 있습니다. 지우고 이동할까요?",
      )
    );
  }

  function confirmDiscardResidentChanges() {
    if (
      hasUnsavedResidentOrderChanges &&
      !window.confirm(
        "어르신 표시 순서에 저장하지 않은 변경이 있습니다. 저장하지 않고 이동할까요?",
      )
    ) return false;
    return (
      !hasUnsavedResidentDraft ||
      window.confirm(
        "어르신 추가에 아직 저장하지 않은 내용이 있습니다. 지우고 이동할까요?",
      )
    );
  }

  function confirmDiscardRoomChanges() {
    return (
      !hasUnsavedRoomChanges ||
      window.confirm(
        "채팅방에 저장하지 않은 변경이 있습니다. 저장하지 않고 이동할까요?",
      )
    );
  }

  function confirmDiscardAdminChanges() {
    if (!confirmDiscardEmployeeChanges()) return false;
    if (!confirmDiscardNewEmployeeChanges()) return false;
    if (!confirmDiscardResidentChanges()) return false;
    if (!confirmDiscardStaffReviewChanges()) return false;
    if (!confirmDiscardRoomChanges()) return false;
    return (
      tab !== "organization" ||
      !hasUnsavedOrganizationDraft ||
      window.confirm(
        "아직 추가하지 않은 조직·직종·직위 이름이 있습니다. 지우고 이동할까요?",
      )
    );
  }

  function closeAdminDrawer() {
    if (readNavigationHistoryState(window.history.state).adminOpen) {
      window.history.back();
      return;
    }
    if (!confirmDiscardAdminChanges()) return;
    discardAdminDrafts();
    onClose();
  }

  function closeAdminDrawerFromHistory() {
    if (!confirmDiscardAdminChanges()) {
      updateNavigationHistoryState({ adminOpen: true }, "push");
      return;
    }
    discardAdminDrafts();
    onClose();
  }

  function changeAdminTab(nextTab: Tab) {
    if (nextTab === tab) return;
    if (!confirmDiscardAdminChanges()) return;
    discardAdminDrafts();
    setTab(nextTab);
    setMobilePane("list");
    setError("");
    setStatusMessage("");
  }

  useEffect(() => {
    requestAdminCloseRef.current = closeAdminDrawer;
    closeAdminFromHistoryRef.current = closeAdminDrawerFromHistory;
  });

  useEffect(() => {
    if (!open) return;
    const navigation = readNavigationHistoryState(window.history.state);
    if (!navigation.adminOpen) {
      updateNavigationHistoryState({ adminOpen: true }, "push");
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const closeOnHistoryBack = (event: PopStateEvent) => {
      if (readNavigationHistoryState(event.state).adminOpen) return;
      closeAdminFromHistoryRef.current();
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") requestAdminCloseRef.current();
    };
    window.addEventListener("popstate", closeOnHistoryBack);
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("popstate", closeOnHistoryBack);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  if (!open) return null;

  return (
    <div className="drawer-layer">
      <button
        className="drawer-backdrop"
        onClick={closeAdminDrawer}
        aria-label="관리 화면 닫기"
      />
      <aside
        className={`admin-drawer ${reviewOnly ? "mentor-review-mode" : ""}`}
        aria-label="관리자 설정"
        role="dialog"
        aria-modal="true"
        data-app-update-dirty={hasUnsavedAdminChanges ? "true" : undefined}
      >
        {reviewOnly ? (
          <div className="mentor-review-readonly-banner" role="note">
            <strong>멘토 전체 기능 검토 · 합성자료 읽기 전용</strong>
            <span>
              합성 직원·권한·방·어르신 구조만 표시합니다. 추가·변경·삭제·Carefor 동기화는 서버에서도 차단됩니다.
            </span>
          </div>
        ) : null}
        <header className="drawer-header">
          <div>
            <span className="eyebrow">관리자</span>
            <h2>관리</h2>
          </div>
          <button className="icon-button" onClick={closeAdminDrawer} aria-label="닫기">
            ×
          </button>
        </header>
        <nav className="admin-tabs" aria-label="관리 메뉴">
          {[
            ["employees", "직원"],
            ["residents", "어르신"],
            ["organization", "기관 설정"],
            ["custom-room", "채팅방"],
          ].map(([value, label]) => {
            const active =
              tab === value ||
              (value === "employees" && ["staff-sync", "new-employee"].includes(tab));
            return (
              <button
                className={active ? "active" : ""}
                key={value}
                onClick={() => changeAdminTab(value as Tab)}
                aria-current={active ? "page" : undefined}
              >
                {label}
              </button>
            );
          })}
        </nav>
        <div
          className={`drawer-content ${
            tab === "employees" || tab === "custom-room" ? "drawer-content-two-pane" : ""
          }`}
          ref={drawerContentRef}
        >
          {statusMessage ? <p className="form-success">{statusMessage}</p> : null}
          {error ? (
            <p className="form-error" role="alert">
              {error}
            </p>
          ) : null}

          {tab === "employees" ? (
            <section className="management-section">
              <div className="staff-management-toolbar" role="region" aria-label="직원 관리 도구">
                <strong>직원 {staffDirectory.length}명</strong>
                <button
                  type="button"
                  className="button button-primary"
                  onClick={() => changeAdminTab("new-employee")}
                >
                  직원 등록
                </button>
                <details className="staff-excel-tools">
                  <summary>엑셀 관리</summary>
                  <div className="staff-excel-tools-panel">
                    <p>예제나 현재 자료를 받은 뒤 수정본을 올리고, 변경 내용을 확인한 후 적용합니다.</p>
                    <BulkTransferPanel
                      entity="staff"
                      disabled={reviewOnly}
                      onApplied={onDataChanged}
                    />
                  </div>
                </details>
              </div>
              <div
                className={`admin-management-grid ${
                  mobilePane === "employee" ? "mobile-detail-open" : ""
                }`}
              >
                <div className="admin-master-pane">
                  <div className="section-heading">
                    <div>
                      <h3>직원 목록</h3>
                      <p>직원을 선택하면 오른쪽에서 바로 관리할 수 있습니다.</p>
                    </div>
                    <span>
                      {visibleEmployees.length}/{staffDirectory.length}명
                    </span>
                  </div>
                  {positionCleanupCandidates.length > 0 ? (
                    <section className="position-cleanup-box" aria-label="직위 정리 필요">
                      <div>
                        <strong>직위로 옮길 직원 {positionCleanupCandidates.length}명</strong>
                        <p>
                          대표자·사무국장은 직위로 정리하고 실제 직종을 따로
                          선택합니다.
                        </p>
                      </div>
                      <div className="position-cleanup-list">
                        {positionCleanupCandidates.map((employee) => {
                          const suggestedPosition =
                            legacyPositionByJobCode[
                              employee.legacy_assignment.job_code ?? ""
                            ];
                          return (
                            <button
                              className="button button-secondary"
                              type="button"
                              key={employee.staff_id}
                              onClick={() => {
                                if (!confirmDiscardEmployeeChanges()) return;
                                const draft = draftFromEmployee(employee);
                                setSelectedEmployeeId(employee.staff_id);
                                setEditDraft({
                                  ...draft,
                                  job_code: "",
                                  position_title:
                                    draft.position_title || suggestedPosition,
                                });
                                setServiceAssignmentDrafts(
                                  serviceAssignmentDraftsFromEmployee(employee),
                                );
                                setEmployeeStatusFilter("active");
                                setEmployeeQuery("");
                                setMobilePane("employee");
                                revealDetailOnSmallScreen(employeeDetailRef);
                              }}
                            >
                              {employee.display_name}
                              <small>
                                현재 {employee.legacy_assignment.job_name} → 직위{" "}
                                {suggestedPosition}
                              </small>
                            </button>
                          );
                        })}
                      </div>
                    </section>
                  ) : null}
                  <div className="management-filters">
                    <input
                      type="search"
                      value={employeeQuery}
                      onChange={(event) => setEmployeeQuery(event.target.value)}
                      placeholder="이름·아이디·소속·직종·직위 검색"
                      aria-label="직원 검색"
                    />
                    <select
                      value={employeeStatusFilter}
                      onChange={(event) =>
                        setEmployeeStatusFilter(event.target.value as EmployeeStatusFilter)
                      }
                      aria-label="재직 상태 필터"
                    >
                      <option value="all">전체 상태</option>
                      <option value="active">재직</option>
                      <option value="leave">휴직</option>
                      <option value="retired">퇴사</option>
                    </select>
                  </div>
                  <div className="employee-list admin-scroll-list">
                    {visibleEmployees.length === 0 ? (
                      <p className="empty-note">조건에 맞는 직원이 없습니다.</p>
                    ) : (
                      visibleEmployees.map((employee) => (
                        <button
                          key={employee.staff_id}
                          className={`employee-card ${
                            selectedEmployeeId === employee.staff_id ? "selected" : ""
                          }`}
                          onClick={() => {
                            if (!confirmDiscardEmployeeChanges()) return;
                            setSelectedEmployeeId(employee.staff_id);
                            setEditDraft(draftFromEmployee(employee));
                            setServiceAssignmentDrafts(
                              serviceAssignmentDraftsFromEmployee(employee),
                            );
                            setResetPassword("");
                            setLoginIssueDraft(emptyLoginIssue);
                            setMobilePane("employee");
                            revealDetailOnSmallScreen(employeeDetailRef);
                          }}
                        >
                          <span className="avatar">
                            {employee.display_name.slice(0, 1)}
                          </span>
                          <span className="employee-copy">
                            <strong>{employee.display_name}</strong>
                            <small>{employeeOrganizationSummary(employee)}</small>
                            <small>
                              {loginStatusLabels[employee.login_status]}
                              {employee.username ? ` · ${employee.username}` : ""}
                            </small>
                          </span>
                          <span className="employee-statuses">
                            <span
                              className={`status-dot ${employee.employment_status}`}
                            >
                              {employeeStatusLabels[employee.employment_status]}
                            </span>
                            {employee.must_change_password &&
                            employee.employment_status === "active" ? (
                              <span className="password-change-badge">
                                첫 로그인 비밀번호 변경
                              </span>
                            ) : null}
                            {employee.can_process_records &&
                            employee.employment_status === "active" ? (
                              <span className="password-change-badge">업무함</span>
                            ) : null}
                            {employee.is_test_data ? (
                              <span className="password-change-badge">시험자료</span>
                            ) : null}
                            <span className="employee-manage-hint">관리 ›</span>
                          </span>
                        </button>
                      ))
                    )}
                  </div>
                </div>
                <div className="admin-detail-pane" ref={employeeDetailRef}>
                  {selectedEmployee ? (
                    <form className="admin-form employee-editor" onSubmit={updateEmployee}>
                      <button
                        type="button"
                        className="employee-list-back"
                        onClick={() => setMobilePane("list")}
                      >
                        ← 직원 목록으로
                      </button>
                      <div className="section-heading">
                        <div>
                          <h3>{selectedEmployee.display_name} 직원정보</h3>
                          <p>
                            {employeeStatusLabels[selectedEmployee.employment_status]} ·{" "}
                            {loginStatusLabels[selectedEmployee.login_status]}
                            {selectedEmployee.username
                              ? ` · ${selectedEmployee.username}`
                              : ""}
                          </p>
                        </div>
                      </div>
                      {selectedEmployee.login_user_id === currentUserId ? (
                        <p className="muted-box">
                          현재 로그인한 관리자입니다. 비밀번호 변경은 보안 설정에서 진행합니다.
                        </p>
                      ) : null}
                      {selectedEmployee.login_user_id === null ? (
                        <section className="login-issue-box" aria-label="로그인 계정 발급">
                          <div>
                            <strong>로그인 계정 없음</strong>
                            <p>계정을 발급하면 이 직원이 로그인하고 허용된 업무 기능을 사용할 수 있습니다.</p>
                          </div>
                          <label>
                            로그인 아이디
                            <input
                              value={loginIssueDraft.username}
                              autoComplete="off"
                              placeholder="2자 이상"
                              onChange={(event) =>
                                setLoginIssueDraft({
                                  ...loginIssueDraft,
                                  username: event.target.value,
                                })
                              }
                            />
                          </label>
                          <label>
                            임시 비밀번호
                            <input
                              type="password"
                              value={loginIssueDraft.temporary_password}
                              autoComplete="new-password"
                              minLength={6}
                              maxLength={200}
                              placeholder="6자 이상"
                              onChange={(event) =>
                                setLoginIssueDraft({
                                  ...loginIssueDraft,
                                  temporary_password: event.target.value,
                                })
                              }
                            />
                          </label>
                          <label className="check-row">
                            <input
                              type="checkbox"
                              checked={loginIssueDraft.can_process_records}
                              onChange={(event) =>
                                setLoginIssueDraft({
                                  ...loginIssueDraft,
                                  can_process_records: event.target.checked,
                                })
                              }
                            />
                            <span>돌봄기록 처리·AI 브리핑 사용</span>
                          </label>
                          <button
                            type="button"
                            className="button button-primary"
                            disabled={
                              saving ||
                              loginIssueDraft.username.trim().length < 2 ||
                              loginIssueDraft.temporary_password.length < 6
                            }
                            onClick={() => void issueEmployeeLogin()}
                          >
                            로그인 계정 발급
                          </button>
                          <small>임시 비밀번호는 처음 로그인할 때 반드시 변경합니다.</small>
                        </section>
                      ) : null}
                      <section
                        className="service-assignment-summary service-assignment-editor"
                        aria-label="서비스별 직종과 직위 편집"
                      >
                        <div className="service-assignment-heading">
                          <strong>서비스별 직종·직위</strong>
                          <small>
                            근무 중인 서비스를 선택하고 각 서비스에서 맡는 직종과
                            직위를 저장합니다. 변경 전 배정은 지난 기록으로 보존됩니다.
                          </small>
                        </div>
                        <div className="service-assignment-editor-grid">
                          {staffServiceDisplayOrder.map((serviceType) => {
                            const serviceLabel = residentServiceLabels[serviceType];
                            const draft = serviceAssignmentDrafts[serviceType];
                            const legacyJobCode =
                              selectedEmployee.legacy_assignment.job_code;
                            const fallbackJobCode =
                              legacyJobCode &&
                              jobs.some(
                                (job) => job.code === legacyJobCode && job.is_active,
                              )
                                ? legacyJobCode
                                : "caregiver";
                            const currentAssignment =
                              selectedCurrentServiceAssignments.find(
                                (assignment) =>
                                  assignment.service_type === serviceType,
                              );
                            const editorDisabled =
                              selectedEmployee.employment_status !== "active";
                            return (
                              <article
                                key={serviceType}
                                className={`service-assignment-editor-card ${
                                  draft.enabled ? "selected" : ""
                                }`}
                              >
                                <label className="service-assignment-toggle">
                                  <input
                                    type="checkbox"
                                    checked={draft.enabled}
                                    disabled={editorDisabled}
                                    aria-label={`${serviceLabel} 근무 중`}
                                    onChange={(event) =>
                                      setServiceAssignmentDrafts({
                                        ...serviceAssignmentDrafts,
                                        [serviceType]: {
                                          ...draft,
                                          enabled: event.target.checked,
                                          job_code:
                                            draft.job_code || fallbackJobCode,
                                        },
                                      })
                                    }
                                  />
                                  <span>
                                    <strong>{serviceLabel}</strong>
                                    <small>
                                      {draft.enabled ? "근무 중" : "현재 근무하지 않음"}
                                    </small>
                                  </span>
                                </label>
                                {draft.enabled ? (
                                  <div className="service-assignment-fields">
                                    <JobSelect
                                      jobs={jobs}
                                      value={draft.job_code}
                                      disabled={editorDisabled}
                                      onChange={(value) =>
                                        setServiceAssignmentDrafts({
                                          ...serviceAssignmentDrafts,
                                          [serviceType]: {
                                            ...draft,
                                            job_code: value,
                                          },
                                        })
                                      }
                                    />
                                    <PositionTitleInput
                                      positions={positionTitles}
                                      value={draft.position_title}
                                      disabled={editorDisabled}
                                      helpText={
                                        draft.job_code === "facility_director"
                                          ? "시설장은 직종이며, 대표는 직위로 선택합니다."
                                          : undefined
                                      }
                                      onChange={(value) =>
                                        setServiceAssignmentDrafts({
                                          ...serviceAssignmentDrafts,
                                          [serviceType]: {
                                            ...draft,
                                            position_title: value,
                                          },
                                        })
                                      }
                                    />
                                  </div>
                                ) : null}
                                {currentAssignment ? (
                                  <small className="service-assignment-evidence">
                                    현재 저장: {serviceAssignmentJobLabel(currentAssignment)}
                                    {currentAssignment.position_title_snapshot
                                      ? ` · ${currentAssignment.position_title_snapshot}`
                                      : ""}
                                    {` · ${staffAssignmentBasisLabels[currentAssignment.assignment_basis]}`}
                                  </small>
                                ) : (
                                  <small className="service-assignment-evidence">
                                    현재 저장된 배정 없음
                                  </small>
                                )}
                              </article>
                            );
                          })}
                        </div>
                        {selectedPastServiceAssignments.length > 0 ? (
                          <details className="service-assignment-history">
                            <summary>
                              지난 서비스 배정 {selectedPastServiceAssignments.length}건
                            </summary>
                            <div className="service-assignment-list">
                              {selectedPastServiceAssignments.map((assignment) => (
                                <div key={assignment.id} className="service-assignment-card">
                                  <span>
                                    {residentServiceLabels[assignment.service_type] ??
                                      assignment.service_type}
                                  </span>
                                  <strong>
                                    {[
                                      serviceAssignmentJobLabel(assignment),
                                      assignment.position_title_snapshot,
                                    ]
                                      .filter(Boolean)
                                      .join(" · ")}
                                  </strong>
                                  <small className="service-assignment-evidence">
                                    {staffAssignmentBasisLabels[assignment.assignment_basis]}
                                    {` · ${assignment.start_date}~${
                                      assignment.end_date ?? "현재"
                                    }`}
                                  </small>
                                </div>
                              ))}
                            </div>
                          </details>
                        ) : null}
                      </section>
                      <label>
                        이름
                        <input
                          value={editDraft.full_name}
                          onChange={(event) =>
                            setEditDraft({ ...editDraft, full_name: event.target.value })
                          }
                          disabled={selectedEmployee.employment_status !== "active"}
                          required
                        />
                      </label>
                      <label>
                        직원번호
                        <input
                          value={editDraft.employee_code}
                          onChange={(event) =>
                            setEditDraft({
                              ...editDraft,
                              employee_code: event.target.value,
                            })
                          }
                          disabled={selectedEmployee.employment_status !== "active"}
                          required
                        />
                        <small>
                          로그인 계정 유무와 관계없이 기관 직원 원본에 저장합니다.
                        </small>
                      </label>
                      <label className="check-row">
                        <input
                          type="checkbox"
                          checked={editDraft.can_process_records}
                          onChange={(event) =>
                            setEditDraft({
                              ...editDraft,
                              can_process_records: event.target.checked,
                            })
                          }
                          disabled={selectedEmployee.login_status !== "enabled"}
                        />
                        <span>
                          돌봄기록 처리·AI 브리핑 사용
                          <small>
                            {selectedEmployee.login_status !== "enabled"
                              ? "로그인 계정을 먼저 발급해야 이 권한을 사용할 수 있습니다."
                              : "직원·어르신·기관·채팅방 관리는 관리자 계정만 사용할 수 있습니다."}
                          </small>
                        </span>
                      </label>
                      {selectedEmployee.employment_status === "active" ? (
                        <>
                          <div className="form-actions">
                            <button className="button button-primary" disabled={saving}>
                              직원정보 저장
                            </button>
                            {selectedEmployee.login_user_id !== currentUserId ? (
                              <button
                                className="button button-danger"
                                type="button"
                                disabled={saving}
                                onClick={() => {
                                  if (
                                    window.confirm(
                                      `${selectedEmployee.display_name} 직원을 퇴사 처리할까요? 로그인 계정이 있으면 현재 접속도 즉시 종료됩니다.`,
                                    )
                                  ) {
                                    void run(async () => {
                                      await apiFetch(
                                        `/api/admin/staff-directory/${selectedEmployee.staff_id}/terminate`,
                                        { method: "POST" },
                                      );
                                    }, "직원을 퇴사 처리했습니다. 로그인 계정이 있으면 현재 접속도 종료했습니다.");
                                  }
                                }}
                              >
                                퇴사 처리
                              </button>
                            ) : null}
                          </div>
                          {selectedEmployee.login_status === "enabled" &&
                          selectedEmployee.login_user_id !== null &&
                          selectedEmployee.login_user_id !== currentUserId ? (
                            <div className="password-reset-box">
                              <div>
                                <strong>임시 비밀번호 발급</strong>
                                <p>저장 즉시 기존 로그인은 모두 종료됩니다.</p>
                              </div>
                              <input
                                type="password"
                                autoComplete="new-password"
                                value={resetPassword}
                                minLength={6}
                                maxLength={200}
                                placeholder="6자 이상 임시 비밀번호"
                                onChange={(event) => setResetPassword(event.target.value)}
                              />
                              <button
                                className="button button-secondary"
                                type="button"
                                disabled={saving || resetPassword.length < 6}
                                onClick={() => {
                                  if (
                                    window.confirm(
                                      `${selectedEmployee.display_name} 직원의 기존 접속을 종료하고 임시 비밀번호를 발급할까요?`,
                                    )
                                  ) {
                                    void run(async () => {
                                      await apiFetch(
                                        `/api/employees/${selectedEmployee.login_user_id}/reset-password`,
                                        {
                                          method: "POST",
                                          body: JSON.stringify({
                                            temporary_password: resetPassword,
                                          }),
                                        },
                                      );
                                      setResetPassword("");
                                    }, "임시 비밀번호를 발급하고 기존 접속을 종료했습니다.");
                                  }
                                }}
                              >
                                비밀번호 초기화
                              </button>
                            </div>
                          ) : null}
                          {selectedEmployee.login_user_id === null ? (
                            <p className="muted-box">
                              로그인 계정이 없어도 서비스별 직종·직위는 저장할 수
                              있습니다.
                            </p>
                          ) : selectedEmployee.login_status !== "enabled" ? (
                            <p className="muted-box">
                              로그인이 중지된 직원도 서비스별 직종·직위는 저장할 수
                              있습니다.
                            </p>
                          ) : null}
                        </>
                      ) : (
                        <>
                          <p className="muted-box">
                            {selectedEmployee.employment_status === "leave"
                                ? "휴직 계정은 현재 수정하거나 로그인할 수 없습니다."
                                : selectedEmployee.employment_status === "retired"
                                  ? "퇴사 처리된 계정은 수정하거나 로그인할 수 없습니다."
                                  : "로그인이 중지된 계정은 현재 수정할 수 없습니다."}
                          </p>
                          {selectedEmployee.employment_status === "retired" &&
                          selectedEmployee.login_user_id !== null &&
                          selectedEmployee.login_user_id !== currentUserId ? (
                            <div className="form-actions">
                              <button
                                className="button button-secondary"
                                type="button"
                                disabled={saving}
                                onClick={() => {
                                  if (
                                    window.confirm(
                                      `${selectedEmployee.display_name} 직원을 재직 상태로 복구할까요? 자동 채팅방도 다시 배정됩니다.`,
                                    )
                                  ) {
                                    void run(async () => {
                                      await apiFetch(
                                        `/api/employees/${selectedEmployee.login_user_id}/restore`,
                                        { method: "POST", body: "{}" },
                                      );
                                    }, "직원을 재직 상태로 복구하고 채팅방을 다시 배정했습니다.");
                                  }
                                }}
                              >
                                재직 복구
                              </button>
                              <button
                                className="button button-danger"
                                type="button"
                                disabled={saving}
                                onClick={() => {
                                  if (
                                    window.confirm(
                                      `${selectedEmployee.display_name} 직원을 목록에서 완전히 삭제할까요?\n\n로그인 아이디와 직원번호는 다시 사용할 수 있습니다. 기존 대화가 있으면 작성자 표시는 기록 보존을 위해 남습니다. 이 작업은 화면에서 복구할 수 없습니다.`,
                                    )
                                  ) {
                                    void run(async () => {
                                      await apiFetch(
                                        `/api/employees/${selectedEmployee.login_user_id}`,
                                        { method: "DELETE" },
                                      );
                                      setSelectedEmployeeId(null);
                                    }, "직원을 목록과 로그인 대상에서 삭제했습니다.");
                                  }
                                }}
                              >
                                직원 삭제
                              </button>
                            </div>
                          ) : null}
                        </>
                      )}
                    </form>
                  ) : (
                    <div className="detail-placeholder">
                      <strong>관리할 직원을 선택해 주세요.</strong>
                      <p>선택한 직원의 조직·직종·직위·업무함 권한을 이곳에서 변경합니다.</p>
                    </div>
                  )}
                </div>
              </div>
            </section>
          ) : null}

          {tab === "staff-sync" ? (
            <section className="resident-sync-admin staff-sync-admin">
              <div className="section-heading">
                <div>
                  <h3>직원정보 갱신</h3>
                  <p>새로 들어온 직원정보와 직접 확인할 직원만 보여드립니다.</p>
                </div>
                <button
                  type="button"
                  className="button button-secondary"
                  onClick={() => changeAdminTab("employees")}
                >
                  직원 목록으로
                </button>
              </div>

              <details className="admin-advanced-details">
                <summary>연결 기준과 안전정책</summary>
                <div className="admin-advanced-details-body">
                  <div className="carefor-roster-guide">
                    <strong>안전 안내</strong>
                    <p>
                      이 화면은 비교 결과와 관리자 판단만 저장합니다. 직원 계정,
                      비밀번호, 채팅방 권한, 재직 상태는 바꾸지 않습니다.
                    </p>
                  </div>
                  <div className="staff-sync-policy" aria-label="직원정보 연결 승인 기준">
                    <strong>연결 승인 기준</strong>
                    <ol>
                      <li>
                        <b>기존 연결:</b> 급여종별과 직원 ID가 모두 같은 계정만 같은 직원으로 봅니다.
                      </li>
                      <li>
                        <b>가명 시험직원:</b> 실제 직원의 연결 후보로 사용하지 않습니다.
                      </li>
                      <li>
                        <b>이름만 같은 직원:</b> 자동 연결하지 않고 관리자가 직접 확인합니다.
                      </li>
                      <li>
                        <b>신규 직원:</b> 직원정보를 먼저 만들고 로그인 발급은 별도로 승인합니다.
                      </li>
                      <li>
                        <b>휴직·퇴사:</b> 휴직은 기록과 권한자료를 보존하지만 로그인과 업무 접근은 중지합니다. 퇴사는 다른 서비스의 재직 여부를 먼저 확인합니다.
                      </li>
                    </ol>
                    <p>기존 대화와 근무 이력은 직원정보를 바꾸더라도 삭제하지 않습니다.</p>
                  </div>
                </div>
              </details>

              <div className="carefor-roster-sources">
                <article className={`carefor-roster-source ${staffSyncSource?.status ?? "missing"}`}>
                  <div>
                    <span className="information-update-source">가져온 곳: 케어포</span>
                    <strong>
                      {staffSyncSource?.status === "ready"
                        ? "최신 직원정보를 확인할 수 있습니다."
                        : staffSyncSource?.status === "invalid"
                          ? "직원정보를 다시 확인해 주세요."
                          : "가져올 직원정보가 없습니다."}
                    </strong>
                    <p>
                      {staffSyncSource?.message ?? "직원 명단 상태를 확인하고 있습니다."}
                    </p>
                  </div>
                  <button
                    type="button"
                    className="button button-primary"
                    disabled={saving || staffSyncLoading || staffSyncSource?.status !== "ready"}
                    onClick={() => {
                      if (confirmDiscardStaffReviewChanges()) {
                        void previewCareforStaff();
                      }
                    }}
                  >
                    최신 직원정보 확인
                  </button>
                </article>
              </div>

              {staffSyncBatches.length > 0 ? (
                <details className="admin-advanced-details">
                  <summary>지난 갱신 기록</summary>
                  <label className="resident-sync-history admin-advanced-details-body">
                    확인할 기록
                    <select
                      value={staffSyncBatch?.id ?? ""}
                      onChange={(event) => {
                        if (
                          event.target.value &&
                          confirmDiscardStaffReviewChanges()
                        ) {
                          void openStaffSyncBatch(event.target.value);
                        }
                      }}
                    >
                      <option value="">기록 선택</option>
                      {staffSyncBatches.map((batch) => (
                        <option key={batch.id} value={batch.id}>
                          {new Date(batch.created_at).toLocaleString("ko-KR")}
                        </option>
                      ))}
                    </select>
                  </label>
                </details>
              ) : null}

              {staffSyncLoading ? <p className="empty-note">직원 명단을 비교하고 있습니다.</p> : null}

              {staffSyncBatch ? (
                <div className="resident-sync-preview">
                  <div className="information-result-summary">
                    <div>
                      <strong>
                        {staffAttentionCandidates.length > 0
                          ? `확인 필요한 직원 ${staffAttentionCandidates.length}명`
                          : "따로 확인할 직원이 없습니다."}
                      </strong>
                      <span>
                        마지막 확인: {new Date(staffSyncBatch.created_at).toLocaleString("ko-KR")}
                      </span>
                    </div>
                    <span className={staffAttentionCandidates.length > 0 ? "attention" : "current"}>
                      {staffAttentionCandidates.length > 0 ? "확인 필요" : "확인 완료"}
                    </span>
                  </div>
                  {staffAssignmentPreview ? (
                    <section
                      className="staff-assignment-preview"
                      aria-label="사람 기준 직원 등록 배정 미리보기"
                    >
                      <div className="staff-assignment-heading">
                        <div>
                          <h4>
                            {staffAttentionCandidates.length > 0
                              ? "확인이 필요한 직원"
                              : "직원정보가 정리되어 있습니다."}
                          </h4>
                          <p>
                            동명이인, 겸직, 조직·직종·직위를 자동으로 정할 수 없는 경우만
                            직접 확인합니다.
                          </p>
                        </div>
                        <span className="staff-assignment-readonly">
                          {staffAttentionCandidates.length > 0 ? "확인 필요" : "확인 완료"}
                        </span>
                      </div>

                      <details className="admin-advanced-details">
                        <summary>갱신 판단의 상세정보</summary>
                        <div className="admin-advanced-details-body">
                          <div className="staff-assignment-reference-note">
                            <strong>조직은 참고안입니다.</strong>
                            <span>
                              현재 개발 시험 조직표의 이름만 참고합니다. 확인한 판단은 저장해도 실제 조직
                              ID, 직원정보, 로그인 계정에는 반영되지 않습니다.
                            </span>
                          </div>

                          <div className="resident-sync-summary staff-assignment-summary">
                        <span>
                          <strong>{staffAssignmentPreview.summary.candidate_count ?? 0}</strong>
                          사람 후보
                        </span>
                        <span className="summary-new">
                          <strong>{staffAssignmentPreview.summary.proposal_ready ?? 0}</strong>
                          기본안 준비
                        </span>
                        <span className="summary-update">
                          <strong>{staffAssignmentPreview.summary.identity_review ?? 0}</strong>
                          동일인 확인
                        </span>
                        <span className="summary-conflict">
                          <strong>{staffAssignmentPreview.summary.assignment_review ?? 0}</strong>
                          배정 확인
                        </span>
                        <span className="summary-conflict">
                          <strong>{staffAssignmentPreview.summary.blocked ?? 0}</strong>
                          자료 확인
                        </span>
                          </div>

                          <div className="staff-review-progress" aria-label="관리자 결정안 저장 현황">
                        <span>
                          검토대상 <strong>{staffAssignmentPreview.summary.review_required_total ?? 0}</strong>명
                        </span>
                        <span>
                          판단저장 <strong>{staffAssignmentPreview.summary.decision_saved ?? 0}</strong>명
                        </span>
                        <span>
                          확인완료 <strong>{staffAssignmentPreview.summary.decision_complete ?? 0}</strong>명
                        </span>
                          </div>

                          {staffApplicationPlan ? (
                        <section
                          className="staff-application-plan"
                          aria-label="직원 실제 반영 전 영향 확인"
                        >
                          <div className="staff-application-plan-heading">
                            <div>
                              <span className="eyebrow">읽기 전용 3단계</span>
                              <h5>실제 반영 전 영향 확인</h5>
                              <p>
                                저장된 결정안과 현재 개발 구조를 함께 계산한 결과입니다.
                                이 표는 직원정보를 바꾸지 않습니다.
                              </p>
                            </div>
                            <span className="staff-application-fingerprint">
                              계획 지문 <code>{staffApplicationPlan.plan_fingerprint.slice(0, 10)}</code>
                              {" · "}변경 지문 <code>{staffApplicationPlan.change_fingerprint.slice(0, 10)}</code>
                            </span>
                          </div>

                          <div
                            className="staff-application-state-summary"
                            aria-label="반영 준비 상태 요약"
                          >
                            <article className="prepared">
                              <strong>
                                {staffApplicationPlan.summary.prepared_candidates ?? 0}
                              </strong>
                              <span>개별계획 준비</span>
                            </article>
                            <article className="needs-design">
                              <strong>
                                {staffApplicationPlan.summary.needs_design_candidates ?? 0}
                              </strong>
                              <span>설계 필요</span>
                            </article>
                            <article className="blocked">
                              <strong>
                                {staffApplicationPlan.summary.blocked_candidates ?? 0}
                              </strong>
                              <span>차단</span>
                            </article>
                          </div>

                          <dl className="staff-application-change-summary">
                            <div>
                              <dt>직원정보</dt>
                              <dd>
                                신규 {staffApplicationPlan.summary.staff_create ?? 0} · 갱신{" "}
                                {staffApplicationPlan.summary.staff_update ?? 0}
                              </dd>
                            </div>
                            <div>
                              <dt>케어포 계정</dt>
                              <dd>
                                신규 {staffApplicationPlan.summary.source_account_create ?? 0} · 갱신{" "}
                                {staffApplicationPlan.summary.source_account_update ?? 0}
                              </dd>
                            </div>
                            <div>
                              <dt>근무이력</dt>
                              <dd>
                                신규 {staffApplicationPlan.summary.service_period_create ?? 0} · 갱신{" "}
                                {staffApplicationPlan.summary.service_period_update ?? 0}
                              </dd>
                            </div>
                            <div>
                              <dt>서비스별 배정</dt>
                              <dd>
                                신규 {staffApplicationPlan.summary.service_assignment_create ?? 0}
                                {" · "}갱신{" "}
                                {staffApplicationPlan.summary.service_assignment_update ?? 0}
                              </dd>
                            </div>
                            <div>
                              <dt>현재 배정 계정</dt>
                              <dd>{staffApplicationPlan.summary.current_assignment_accounts ?? 0}건</dd>
                            </div>
                            <div>
                              <dt>과거 이력 계정</dt>
                              <dd>{staffApplicationPlan.summary.history_only_accounts ?? 0}건</dd>
                            </div>
                          </dl>

                          <p className="staff-application-decision-summary">
                            결정안: 자동 기본안 {staffApplicationPlan.summary.auto_decision_count ?? 0}명
                            {" · "}저장 완료 {staffApplicationPlan.summary.complete_review_count ?? 0}명
                            {" · "}미완료 {staffApplicationPlan.summary.incomplete_review_count ?? 0}명
                          </p>

                          <p className="staff-application-no-access-change">
                            로그인 계정 변경 {staffApplicationPlan.summary.login_account_changes ?? 0}건
                            {" · "}채팅방 자동참여 변경 {staffApplicationPlan.summary.room_membership_changes ?? 0}건
                            {" · "}휴직 접근정책 승인됨
                          </p>

                          {staffApplicationPlan.blockers.length > 0 ? (
                            <div className="staff-application-blockers">
                              <strong>먼저 해결할 조건</strong>
                              <ul>
                                {staffApplicationPlan.blockers.map((blocker) => (
                                  <li key={blocker.code}>
                                    <span>
                                      {blocker.code === "rollback_manifest_missing" &&
                                      staffApplicationPlan.change_manifest_supported
                                        ? "행별 변경계획은 준비됐지만 실제 반영·복구 실행기는 아직 잠겨 있습니다."
                                        : blocker.message}
                                    </span>
                                    <small>
                                      영향 {blocker.affected_people}명 · {blocker.affected_accounts}개 계정
                                    </small>
                                  </li>
                                ))}
                              </ul>
                            </div>
                          ) : null}
                        </section>
                          ) : null}
                        </div>
                      </details>

                      {staffAttentionCandidates.length > 0 ? (
                        <>
                          <div className="staff-assignment-filters">
                        <label>
                          사람 검색
                          <input
                            type="search"
                            value={staffAssignmentQuery}
                            onChange={(event) => setStaffAssignmentQuery(event.target.value)}
                            placeholder="이름·직종·서비스"
                          />
                        </label>
                        <label>
                          확인 상태
                          <select
                            value={staffAssignmentReviewFilter}
                            onChange={(event) =>
                              setStaffAssignmentReviewFilter(
                                event.target.value as typeof staffAssignmentReviewFilter,
                              )
                            }
                          >
                            <option value="all">전체 후보</option>
                            <option value="pending_review">미완료 확인대상</option>
                            <option value="review_required">전체 검토대상</option>
                            <option value="proposal_ready">기본안 준비</option>
                            <option value="identity_review">동일인 확인 필요</option>
                            <option value="assignment_review">배정 확인 필요</option>
                            <option value="blocked">자료 확인 필요</option>
                          </select>
                        </label>
                        <span>
                          {visibleStaffAssignmentCandidates.length}/
                          {staffAssignmentPreview.candidates.length}명 후보 표시
                        </span>
                          </div>

                          <div className="staff-assignment-candidates">
                        {visibleStaffAssignmentCandidates.map((candidate) => {
                          const services = Array.from(
                            new Set(candidate.accounts.map((account) => account.service_type)),
                          )
                            .map((service) => residentServiceLabels[service] ?? service)
                            .join(" · ");
                          const reviewDraft = staffReviewDrafts[candidate.candidate_key];
                          const reviewDraftDirty = reviewDraft
                            ? staffReviewEditorIsDirty(candidate, reviewDraft)
                            : false;
                          const reviewComplete =
                            !reviewDraftDirty &&
                            candidate.review_draft?.completion_status === "complete";
                          return (
                            <details
                              className={`staff-assignment-candidate status-${candidate.review_status}`}
                              key={candidate.candidate_key}
                              open={expandedStaffReviewCandidateKeys.includes(
                                candidate.candidate_key,
                              )}
                              onToggle={(event) => {
                                const candidateKey = candidate.candidate_key;
                                const isOpen = event.currentTarget.open;
                                setExpandedStaffReviewCandidateKeys((current) => {
                                  const alreadyOpen = current.includes(candidateKey);
                                  if (isOpen === alreadyOpen) return current;
                                  return isOpen
                                    ? [...current, candidateKey]
                                    : current.filter((key) => key !== candidateKey);
                                });
                              }}
                            >
                              <summary>
                                <div>
                                  <strong>{candidate.display_name}</strong>
                                  <span>{services} · {candidate.account_count}개 계정</span>
                                </div>
                                <span
                                  className={`staff-assignment-badge ${
                                    reviewComplete ? "complete" : candidate.review_status
                                  }`}
                                >
                                  {reviewComplete
                                    ? "검토 완료"
                                    : staffAssignmentReviewLabels[candidate.review_status]}
                                </span>
                              </summary>
                              <div className="staff-assignment-detail">
                                {candidate.notes.map((note) => (
                                  <p className="staff-assignment-note" key={note}>{note}</p>
                                ))}
                                <div className="staff-identity-evidence">
                                  <strong>
                                    {candidate.identity_evidence === "name_birth"
                                      ? "이름·생년 확인됨"
                                      : "생년 확인 필요"}
                                  </strong>
                                  <span>
                                    {candidate.identity_evidence === "name_birth"
                                      ? "생년 원문은 표시하지 않고 동일인 구분에만 사용했습니다."
                                      : "이름만으로 자동 병합하지 않고 관리자 판단을 기다립니다."}
                                  </span>
                                </div>
                                <div className="staff-assignment-accounts">
                                  {candidate.accounts.map((account) => {
                                    const assignmentRequired =
                                      staffAccountNeedsCurrentAssignment(account);
                                    const visibleOrganizations = account.organizations.filter(
                                      (proposal) =>
                                        proposal.proposed_name !== null ||
                                        proposal.match_status === "manual",
                                    );
                                    return (
                                      <article key={account.source_key}>
                                        <div className="staff-assignment-account-heading">
                                          <strong>
                                            {residentServiceLabels[account.service_type] ??
                                              account.service_type}
                                          </strong>
                                          <span>
                                            {account.source_status || account.employment_status}
                                            {" · "}케어포 ID {account.external_id}
                                          </span>
                                        </div>
                                        {assignmentRequired ? (
                                          <>
                                            <dl>
                                              {visibleOrganizations.map((proposal) => (
                                                <div key={proposal.unit_type}>
                                                  <dt>
                                                    {staffOrganizationProposalLabels[proposal.unit_type]}
                                                  </dt>
                                                  <dd>
                                                    {proposal.proposed_name ?? "관리자 확인"}
                                                    {proposal.match_status === "reference_only" ? (
                                                      <small>시험 조직 참고</small>
                                                    ) : null}
                                                    {["manual", "missing", "ambiguous"].includes(
                                                      proposal.match_status,
                                                    ) ? (
                                                      <small>확인 필요</small>
                                                    ) : null}
                                                  </dd>
                                                </div>
                                              ))}
                                              <div>
                                                <dt>직종</dt>
                                                <dd>
                                                  {account.job.proposed_name ??
                                                    (account.job.required_for_review
                                                      ? "관리자 확인"
                                                      : "미지정 가능")}
                                                  <small>
                                                    케어포: {account.source_job_name || "미확인"}
                                                  </small>
                                                  {account.job.match_status !== "matched" &&
                                                  !account.job.required_for_review ? (
                                                    <small>자격 직종은 선택 사항</small>
                                                  ) : null}
                                                </dd>
                                              </div>
                                              {account.position.proposed_name ||
                                              account.position.manual_confirmation ? (
                                                <div>
                                                  <dt>직위</dt>
                                                  <dd>
                                                    {account.position.proposed_name ?? "관리자 확인"}
                                                    <small>확인 후 선택</small>
                                                  </dd>
                                                </div>
                                              ) : null}
                                            </dl>
                                            {account.service_type === "facility" ? (
                                              <p>
                                                근무층·시설팀은 케어포 원본에 없어 제안하지 않습니다.
                                              </p>
                                            ) : null}
                                          </>
                                        ) : (
                                          <div className="staff-assignment-history-notice">
                                            <strong>과거 근무 이력</strong>
                                            <span>
                                              당시 케어포 직종: {account.source_job_name || "미확인"}
                                            </span>
                                            <span>
                                              현재 조직·직종·직위 배정 대상이 아니며 별도 입력하지 않습니다.
                                            </span>
                                          </div>
                                        )}
                                        {account.notes.map((note) => (
                                          <p className="staff-assignment-account-note" key={note}>
                                            {note}
                                          </p>
                                        ))}
                                      </article>
                                    );
                                  })}
                                </div>
                                {reviewDraft &&
                                ["identity_review", "assignment_review"].includes(
                                  candidate.review_status,
                                ) ? (
                                  <StaffReviewDraftForm
                                    candidate={candidate}
                                    draft={reviewDraft}
                                    units={units}
                                    jobs={jobs}
                                    positions={positionTitles}
                                    locked={staffReviewSavingKey !== null}
                                    saving={staffReviewSavingKey === candidate.candidate_key}
                                    onIdentityChange={(value) =>
                                      setStaffReviewIdentity(candidate, value)
                                    }
                                    onCustomPairChange={(value) =>
                                      setStaffReviewCustomPair(candidate, value)
                                    }
                                    onAssignmentChange={(sourceKey, field, value) =>
                                      updateStaffReviewAssignment(
                                        candidate.candidate_key,
                                        sourceKey,
                                        field,
                                        value,
                                      )
                                    }
                                    onNoteChange={(value) =>
                                      updateStaffReviewDraft(
                                        candidate.candidate_key,
                                        (current) => ({ ...current, note: value }),
                                      )
                                    }
                                    onSave={() => void saveStaffReviewDraft(candidate)}
                                  />
                                ) : null}
                              </div>
                            </details>
                          );
                        })}
                        {visibleStaffAssignmentCandidates.length === 0 ? (
                          <p className="empty-note">선택한 조건에 맞는 사람 후보가 없습니다.</p>
                        ) : null}
                          </div>
                        </>
                      ) : (
                        <div className="information-empty-state" role="status">
                          <strong>확인할 직원이 없습니다.</strong>
                          <span>변경 없는 직원정보는 화면에 펼치지 않았습니다.</span>
                        </div>
                      )}
                    </section>
                  ) : null}
                  <details className="admin-advanced-details">
                    <summary>원본 직원정보와 기술정보</summary>
                    <div className="admin-advanced-details-body">
                      <p className="field-help">
                        원본: {staffSyncBatch.original_name} · 확인 시각:{" "}
                        {new Date(staffSyncBatch.created_at).toLocaleString("ko-KR")}
                      </p>
                      <div className="resident-sync-summary" aria-label="직원 변경사항 요약">
                    {(
                      ["new", "update", "leave", "retire", "conflict", "unchanged"] as const
                    ).map((changeType) => (
                      <span key={changeType}>
                        <strong>{staffSyncBatch.summary[changeType] ?? 0}</strong>
                        {staffSyncChangeLabels[changeType]}
                      </span>
                    ))}
                      </div>
                      <div className="staff-sync-service-summary" aria-label="급여종별 재직상태 요약">
                    {(["facility", "daycare", "homecare"] as const).map((serviceType) => {
                      const service = staffSyncServiceSummary[serviceType];
                      const total = service.active + service.leave + service.retired + service.unknown;
                      return (
                        <article key={serviceType}>
                          <strong>{residentServiceLabels[serviceType]} {total}개 계정</strong>
                          <span>
                            재직 {service.active} · 휴직 {service.leave} · 퇴사 {service.retired}
                          </span>
                        </article>
                      );
                    })}
                      </div>
                  {(staffSyncBatch.summary.missing_not_retired ?? 0) > 0 ? (
                    <p className="field-help">
                      기존 연결 계정 중 이번 파일에 없는 {staffSyncBatch.summary.missing_not_retired}건은
                      퇴사로 판단하지 않았습니다.
                    </p>
                  ) : null}
                      <div className="staff-sync-filters" aria-label="직원정보 원본 필터">
                    <label>
                      직원 검색
                      <input
                        type="search"
                        value={staffSyncQuery}
                        onChange={(event) => setStaffSyncQuery(event.target.value)}
                        placeholder="이름·직종·케어포 ID"
                      />
                    </label>
                    <label>
                      급여종별
                      <select
                        value={staffSyncServiceFilter}
                        onChange={(event) =>
                          setStaffSyncServiceFilter(
                            event.target.value as typeof staffSyncServiceFilter,
                          )
                        }
                      >
                        <option value="all">전체</option>
                        <option value="facility">시설</option>
                        <option value="daycare">주간보호</option>
                        <option value="homecare">방문요양</option>
                      </select>
                    </label>
                    <label>
                      재직상태
                      <select
                        value={staffSyncStatusFilter}
                        onChange={(event) =>
                          setStaffSyncStatusFilter(
                            event.target.value as typeof staffSyncStatusFilter,
                          )
                        }
                      >
                        <option value="all">전체</option>
                        <option value="active">재직</option>
                        <option value="leave">휴직</option>
                        <option value="retired">퇴사</option>
                      </select>
                    </label>
                    <label>
                      직종
                      <select
                        value={staffSyncJobFilter}
                        onChange={(event) => setStaffSyncJobFilter(event.target.value)}
                      >
                        <option value="all">전체 직종</option>
                        {staffSyncJobOptions.map((job) => (
                          <option key={job} value={job}>{job}</option>
                        ))}
                      </select>
                    </label>
                    <span className="staff-sync-filter-count">
                      {visibleStaffSyncItems.length}/{staffSyncBatch.items.length}개 계정 표시
                    </span>
                      </div>
                      <label className="check-row compact-check">
                    <input
                      type="checkbox"
                      checked={showUnchangedStaffSyncItems}
                      onChange={(event) => setShowUnchangedStaffSyncItems(event.target.checked)}
                    />
                    <span>변경 없는 직원도 보기</span>
                      </label>
                      <div className="resident-sync-list">
                    {visibleStaffSyncItems.map((item) => {
                      const current = item.current_snapshot;
                      const otherActive = current?.other_active_source_count ?? 0;
                      const sameNameAccountCount =
                        staffSyncNameCounts.get(item.incoming_payload.display_name) ?? 1;
                      return (
                        <article
                          className={`resident-sync-item change-${item.change_type}`}
                          key={item.id}
                        >
                          <div className="resident-sync-item-body">
                            <div className="resident-sync-item-title">
                              <strong>{item.incoming_payload.display_name}</strong>
                              <span className={`resident-sync-change-badge ${item.change_type}`}>
                                {staffSyncChangeLabels[item.change_type]}
                              </span>
                            </div>
                            <p>
                              {residentServiceLabels[item.service_type] ?? item.service_type}
                              {" · "}{item.incoming_payload.job_name || "직종 미확인"}
                              {" · "}{item.incoming_payload.source_status || "상태 미확인"}
                            </p>
                            <small>
                              입사일 {item.incoming_payload.start_date ?? "미확인"}
                              {" · "}케어포 ID {item.external_id}
                            </small>
                            {sameNameAccountCount > 1 ? (
                              <p className="field-help">
                                같은 이름의 케어포 계정 {sameNameAccountCount}개가 있습니다. 생년 근거가 없으면 관리자가 확인합니다.
                              </p>
                            ) : null}
                            {current?.staff_name ? (
                              <p>현재 연결 직원: {current.staff_name}</p>
                            ) : null}
                            {otherActive > 0 && ["leave", "retire"].includes(item.change_type) ? (
                              <p className="field-help">
                                다른 급여종별 재직 계정 {otherActive}개가 있어 직원 전체 사용중지 대상이 아닙니다.
                              </p>
                            ) : null}
                            {item.conflict_reason ? (
                              <p className="resident-sync-conflict">{item.conflict_reason}</p>
                            ) : null}
                          </div>
                        </article>
                      );
                    })}
                    {visibleStaffSyncItems.length === 0 ? (
                      <p className="empty-note">선택한 조건에 맞는 직원이 없습니다.</p>
                    ) : null}
                      </div>
                    </div>
                  </details>
                </div>
              ) : (
                <p className="empty-note resident-sync-idle">
                  위의 “최신 직원정보 확인”을 누르면 확인이 필요한 직원만 보여드립니다.
                </p>
              )}
            </section>
          ) : null}

          {tab === "new-employee" ? (
            <form className="admin-form" onSubmit={addEmployee}>
              <div className="section-heading">
                <div>
                  <h3>직원 직접 등록</h3>
                  <p className="field-help">기관 직원을 이곳에서 직접 등록합니다.</p>
                </div>
                <button
                  type="button"
                  className="button button-secondary"
                  onClick={() => changeAdminTab("employees")}
                >
                  직원 목록으로
                </button>
              </div>
              <p className="field-help">
                직원번호를 비워두면 시스템이 자동으로 발급합니다.
              </p>
              <div className="form-grid two">
                <label>
                  이름
                  <input
                    value={employeeDraft.full_name}
                    onChange={(event) =>
                      setEmployeeDraft({ ...employeeDraft, full_name: event.target.value })
                    }
                    required
                  />
                </label>
                <label>
                  직원번호
                  <input
                    value={employeeDraft.employee_code}
                    onChange={(event) =>
                      setEmployeeDraft({ ...employeeDraft, employee_code: event.target.value })
                    }
                    placeholder="선택 입력 · 비워두면 자동 발급"
                  />
                </label>
              </div>
              <label className="check-row login-create-toggle">
                <input
                  type="checkbox"
                  checked={issueLoginOnCreate}
                  onChange={(event) => setIssueLoginOnCreate(event.target.checked)}
                />
                <span>
                  로그인 계정도 발급
                  <small>직원 등록과 계정 발급을 한 번에 저장합니다.</small>
                </span>
              </label>
              {issueLoginOnCreate ? (
                <section className="login-create-fields" aria-label="새 직원 로그인 계정">
                  <div className="form-grid two">
                    <label>
                      로그인 아이디
                      <input
                        value={employeeDraft.username}
                        autoComplete="off"
                        minLength={2}
                        maxLength={80}
                        required
                        onChange={(event) =>
                          setEmployeeDraft({ ...employeeDraft, username: event.target.value })
                        }
                      />
                    </label>
                    <label>
                      임시 비밀번호
                      <input
                        type="password"
                        value={employeeDraft.password}
                        autoComplete="new-password"
                        minLength={6}
                        maxLength={200}
                        required
                        onChange={(event) =>
                          setEmployeeDraft({ ...employeeDraft, password: event.target.value })
                        }
                      />
                    </label>
                  </div>
                  <label className="check-row">
                    <input
                      type="checkbox"
                      checked={employeeDraft.can_process_records}
                      onChange={(event) =>
                        setEmployeeDraft({
                          ...employeeDraft,
                          can_process_records: event.target.checked,
                        })
                      }
                    />
                    <span>
                      돌봄기록 처리·AI 브리핑 사용
                      <small>계정 발급과 동시에 이 업무 권한을 부여합니다.</small>
                    </span>
                  </label>
                  <p className="field-help">임시 비밀번호는 처음 로그인할 때 반드시 변경합니다.</p>
                </section>
              ) : (
                <p className="muted-box">
                  로그인 없이 직원 명단과 서비스 배정정보만 먼저 관리할 수 있습니다.
                </p>
              )}
              <button className="button button-primary button-large" disabled={saving}>
                {issueLoginOnCreate ? "직원과 로그인 계정 등록" : "직원 등록"}
              </button>
            </form>
          ) : null}

          {tab === "organization" ? (
            <section className="organization-admin">
              <div className="section-heading">
                <div>
                  <h3>기관 기본 설정</h3>
                  <p>처음 사용할 때 생활공간·직종·직위를 기관에 맞게 확인합니다.</p>
                </div>
              </div>

              <div className="information-update-card" aria-label="기관 기본 설정 상태">
                <div>
                  <span className="information-update-source">초기 설정</span>
                  <strong>
                    {units.some((unit) => unit.unit_type === "floor" && unit.is_active) &&
                    jobs.some((job) => job.is_active) &&
                    positionTitles.some((position) => position.is_active)
                      ? "현재 기관에서 사용할 기본 항목이 준비되어 있습니다."
                      : "사용 전에 비어 있는 기본 항목을 등록해 주세요."}
                  </strong>
                  <span>
                    생활실·방·구역 {units.filter((unit) => unit.unit_type === "floor" && unit.is_active).length}개
                    {" · "}직종 {jobs.filter((job) => job.is_active).length}개
                    {" · "}직위 {positionTitles.filter((position) => position.is_active).length}개
                  </span>
                  <span>
                    현재 값은 유지됩니다. 필요 없는 항목은 목록에서 삭제하고 언제든 다시 사용할 수 있습니다.
                  </span>
                </div>
              </div>

              <div className="organization-tree" aria-label="실제 조직표">
                {units
                  .filter(
                    (unit) =>
                      unit.is_active &&
                      !unit.is_test_data &&
                      unit.unit_type === "business",
                  )
                  .sort(compareApprovedOrganization)
                  .map((business) => {
                    const children = units.filter(
                      (unit) =>
                        unit.is_active &&
                        !unit.is_test_data &&
                        unit.parent_unit_id === business.id,
                    );
                    return (
                      <article className="organization-tree-card" key={business.id}>
                        <h4>{business.name}</h4>
                        {(["department", "floor", "team"] as UnitType[]).map((type) => {
                          const typeChildren = children.filter(
                            (unit) => unit.unit_type === type,
                          ).sort(compareApprovedOrganization);
                          if (typeChildren.length === 0) return null;
                          return (
                            <div className="organization-tree-row" key={type}>
                              <strong>{unitLabels[type]}</strong>
                              <div>
                                {typeChildren.map((unit) => (
                                  <span key={unit.id}>{unit.name}</span>
                                ))}
                              </div>
                            </div>
                          );
                        })}
                      </article>
                    );
                  })}
              </div>

              <details className="admin-advanced-details">
                <summary>직종과 직위 보기</summary>
                <div className="admin-advanced-details-body organization-reference-grid">
                  <div>
                    <strong>직종</strong>
                    <div className="simple-name-list">
                      {jobs.filter((job) => job.is_active).map((job) => (
                        <span key={job.code}>{job.name}</span>
                      ))}
                    </div>
                  </div>
                  <div>
                    <strong>직위</strong>
                    <div className="simple-name-list">
                      {positionTitles
                        .filter((position) => position.is_active)
                        .map((position) => (
                          <span key={position.id}>{position.name}</span>
                        ))}
                    </div>
                  </div>
                </div>
              </details>

              <details className="admin-advanced-details organization-maintenance">
                <summary>생활공간·직종·직위 추가·수정</summary>
                <div className="admin-advanced-details-body">
                  <p className="muted-box">
                    생활실·방·구역과 직종·직위는 기관에서 직접 관리합니다. 목록에서
                    삭제해도 기존 대화·기록은 지워지지 않으며, 현재 배정은 직원·어르신
                    관리에서 다른 항목이나 미지정으로 바꿀 수 있습니다.
                  </p>
              <div className="archive-visibility">
                <div>
                  <strong>현재 사용하는 정보만 표시 중</strong>
                  <small>
                    목록에서 삭제한 항목은 평소에는 숨기고 필요할 때 다시 사용할 수 있습니다.
                  </small>
                </div>
                <button
                  type="button"
                  className="button button-secondary"
                  onClick={() => setShowInactiveOrganization((current) => !current)}
                >
                  {showInactiveOrganization
                    ? "삭제된 항목 숨기기"
                    : `삭제된 항목 보기 (${
                      units.filter((unit) => (unit.unit_type === "floor" || unit.is_test_data) && !unit.is_active).length +
                        jobs.filter((job) => !job.is_active).length +
                        positionTitles.filter((position) => !position.is_active).length
                      })`}
                </button>
              </div>
              <form
                className="admin-form compact-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  void run(async () => {
                    await apiFetch("/api/org-units", {
                      method: "POST",
                      body: JSON.stringify({ unit_type: unitType, name: unitName }),
                    });
                    setUnitName("");
                  }, "조직정보를 추가했습니다.");
                }}
              >
                <h3>생활공간·조직정보 추가</h3>
                <p className="field-help">
                  생활실·방 번호·구역은 기관이 실제 사용하는 이름으로 자유롭게 등록합니다.
                  사업부·부서·팀의 수동 추가는 기존 시험·예외 항목 계약을 유지합니다.
                </p>
                <div className="inline-fields">
                  <select
                    value={unitType}
                    onChange={(event) => setUnitType(event.target.value as UnitType)}
                    aria-label="조직 종류"
                  >
                    {(Object.keys(unitLabels) as UnitType[]).map((type) => (
                      <option key={type} value={type}>
                        {unitLabels[type]}
                      </option>
                    ))}
                  </select>
                  <input
                    value={unitName}
                    onChange={(event) => setUnitName(event.target.value)}
                    placeholder={
                      unitType === "floor" ? "예: 201호, 2층 A구역" : "예: 의료, 야간전담팀"
                    }
                    required
                  />
                  <button className="button button-primary" disabled={saving}>
                    추가
                  </button>
                </div>
              </form>
              <div className="unit-groups">
                {(Object.keys(unitLabels) as UnitType[]).map((type) => (
                  <div className="unit-group" key={type}>
                    <strong>{unitLabels[type]}</strong>
                    <div>
                      {units
                        .filter(
                          (unit) =>
                            (unit.unit_type === "floor" || unit.is_test_data) &&
                            unit.unit_type === type &&
                            (unit.is_active || showInactiveOrganization),
                        )
                        .map((unit) => {
                          const usageLabel =
                            unit.unit_type === "floor"
                              ? `어르신 ${unit.active_resident_count}명 · 참여 직원 ${unit.active_participant_count}명 · 시스템 채팅방 ${unit.system_room_count}개`
                              : `직원 ${unit.active_staff_count}명 · 채팅방 ${unit.active_room_count}개`;
                          return (
                            <span
                              className={`managed-chip ${unit.is_active ? "" : "inactive"}`}
                              key={unit.id}
                            >
                              <button
                                type="button"
                                className="chip-label"
                                aria-label={`${unit.name} 이름 수정`}
                                onClick={() => {
                                  const nextName = window.prompt(
                                    "조직정보 이름을 변경합니다.",
                                    unit.name,
                                  );
                                  if (!nextName?.trim() || nextName.trim() === unit.name) return;
                                  void run(async () => {
                                    await apiFetch(`/api/org-units/${unit.id}`, {
                                      method: "PATCH",
                                      body: JSON.stringify({ name: nextName.trim() }),
                                    });
                                  }, "조직정보 이름을 변경했습니다.");
                                }}
                              >
                                <span>{unit.name}</span>
                                <small>
                                  {unit.is_active
                                    ? usageLabel
                                    : unit.reference_count > 0
                                      ? `목록에서 삭제됨 · 기록 연결 ${unit.reference_count}건`
                                      : "목록에서 삭제됨"}
                                </small>
                              </button>
                              {unit.unit_type === "floor" && unit.is_active ? (
                                <button
                                  type="button"
                                  className="chip-action"
                                  disabled={saving}
                                  onClick={() => void openLivingSpaceMembers(unit)}
                                >
                                  참여 직원 관리
                                </button>
                              ) : null}
                              <button
                                type="button"
                                className="chip-action"
                                disabled={saving}
                                onClick={() => {
                                  const nextActive = !unit.is_active;
                                  if (
                                    !window.confirm(
                                      nextActive
                                        ? `${unit.name} 항목을 다시 사용하시겠습니까?`
                                        : `${unit.name} 항목을 현재 선택 목록에서 삭제할까요?\n기존 대화와 기록은 삭제되지 않습니다.`,
                                    )
                                  ) return;
                                  void run(async () => {
                                    await apiFetch(`/api/org-units/${unit.id}`, {
                                      method: "PATCH",
                                      body: JSON.stringify({ is_active: nextActive }),
                                    });
                                  }, nextActive
                                    ? "항목을 다시 사용할 수 있게 했습니다."
                                    : "현재 선택 목록에서 삭제했습니다. 기존 기록은 유지됩니다.");
                                }}
                              >
                                {unit.is_active ? "삭제" : "다시 사용"}
                              </button>
                            </span>
                          );
                        })}
                    </div>
                  </div>
                ))}
              </div>
              {selectedLivingSpaceUnitId && livingSpaceDetails ? (
                <section className="admin-form living-space-member-editor" aria-label="생활공간 참여 직원 관리">
                  <div className="section-heading">
                    <div>
                      <h3>
                        {units.find((unit) => unit.id === selectedLivingSpaceUnitId)?.name ?? "생활공간"} 참여 직원
                      </h3>
                      <p>
                        {livingSpaceDetails.room_name} · 현재 {livingSpaceMemberIds.length}명 · 대화 {livingSpaceDetails.message_count}건 · 첨부 {livingSpaceDetails.attachment_count}건
                      </p>
                    </div>
                    <button
                      type="button"
                      className="button button-secondary"
                      onClick={() => {
                        setSelectedLivingSpaceUnitId(null);
                        setLivingSpaceDetails(null);
                        setLivingSpaceMemberIds([]);
                      }}
                    >
                      닫기
                    </button>
                  </div>
                  <p className="field-help">
                    이 선택은 직원의 사업부·부서·직종·직위를 바꾸지 않습니다. 로그인 계정이 있어야 채팅방에 참여할 수 있습니다.
                  </p>
                  <div className="living-space-member-list">
                    {staffDirectory
                      .filter((employee) => employee.employment_status === "active")
                      .map((employee) => {
                        const unavailable =
                          employee.login_user_id === null || employee.login_status !== "enabled";
                        const userId = employee.login_user_id;
                        return (
                          <label className={`check-row ${unavailable ? "disabled" : ""}`} key={employee.staff_id}>
                            <input
                              type="checkbox"
                              disabled={saving || unavailable}
                              checked={Boolean(userId && livingSpaceMemberIds.includes(userId))}
                              onChange={(event) => {
                                if (!userId) return;
                                setLivingSpaceMemberIds((current) =>
                                  event.target.checked
                                    ? Array.from(new Set([...current, userId]))
                                    : current.filter((id) => id !== userId),
                                );
                              }}
                            />
                            <span>
                              {employee.display_name}
                              <small>
                                {unavailable
                                  ? "로그인 계정 없음 · 참여 불가"
                                  : [
                                      employee.legacy_assignment.job_name,
                                      employee.legacy_assignment.position_title,
                                    ].filter(Boolean).join(" · ") || "재직 직원"}
                              </small>
                            </span>
                          </label>
                        );
                      })}
                  </div>
                  <button
                    type="button"
                    className="button button-primary"
                    disabled={saving}
                    onClick={() => void run(async () => {
                      const next = await apiFetch<LivingSpaceRoomDetails>(
                        `/api/org-units/${selectedLivingSpaceUnitId}/living-space-members`,
                        {
                          method: "PUT",
                          body: JSON.stringify({ member_ids: livingSpaceMemberIds }),
                        },
                      );
                      setLivingSpaceDetails(next);
                      setLivingSpaceMemberIds(next.member_ids);
                    }, "생활공간 참여 직원을 저장했습니다.")}
                  >
                    참여 직원 저장
                  </button>
                </section>
              ) : null}
              <form
                className="admin-form compact-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  void run(async () => {
                    await apiFetch("/api/job-codes", {
                      method: "POST",
                      body: JSON.stringify({ name: jobName }),
                    });
                    setJobName("");
                  }, "직종정보를 추가했습니다.");
                }}
              >
                <h3>직종(자격·업무) 추가</h3>
                <p className="field-help">
                  사회복지사·간호조무사·요양보호사처럼 실제 업무 종류만 관리합니다.
                  원장·팀장·선임 같은 기관 내부 역할은 아래 직위에서 관리합니다.
                </p>
                <div className="inline-fields">
                  <input
                    value={jobName}
                    onChange={(event) => setJobName(event.target.value)}
                    placeholder="예: 음악치료사, 치과위생사"
                    required
                  />
                  <button className="button button-primary" disabled={saving}>
                    추가
                  </button>
                </div>
              </form>
              <div className="unit-group">
                <strong>직종</strong>
                <div>
                  {jobs
                    .filter((job) => job.is_active || showInactiveOrganization)
                    .map((job) => {
                    const usageLabel = `직원 ${job.active_staff_count}명 · 방 ${job.active_room_count}개`;
                    return (
                      <span
                        className={`managed-chip ${job.is_active ? "" : "inactive"}`}
                        key={job.code}
                      >
                        <button
                          type="button"
                          className="chip-label"
                          aria-label={`${job.name} 이름 수정`}
                          onClick={() => {
                            const nextName = window.prompt(
                              "직종 이름을 변경합니다.",
                              job.name,
                            );
                            if (!nextName?.trim() || nextName.trim() === job.name) return;
                            void run(async () => {
                              await apiFetch(`/api/job-codes/${job.code}`, {
                                method: "PATCH",
                                body: JSON.stringify({ name: nextName.trim() }),
                              });
                            }, "직종 이름을 변경했습니다.");
                          }}
                        >
                          <span>{job.name}</span>
                          <small>
                            {job.is_active
                              ? usageLabel
                              : job.reference_count > 0
                                ? `목록에서 삭제됨 · 기록 연결 ${job.reference_count}건`
                                : "목록에서 삭제됨"}
                          </small>
                        </button>
                        <button
                          type="button"
                          className="chip-action"
                          disabled={saving}
                          onClick={() => {
                            const nextActive = !job.is_active;
                            if (
                              !window.confirm(
                                nextActive
                                  ? `${job.name} 직종을 다시 사용하시겠습니까?`
                                  : `${job.name} 직종을 현재 선택 목록에서 삭제할까요?\n기존 직원·대화 기록은 삭제되지 않습니다.`,
                              )
                            ) return;
                            void run(async () => {
                              await apiFetch(`/api/job-codes/${job.code}`, {
                                method: "PATCH",
                                body: JSON.stringify({ is_active: nextActive }),
                              });
                            }, nextActive
                              ? "직종을 다시 사용할 수 있게 했습니다."
                              : "직종을 현재 선택 목록에서 삭제했습니다. 기존 기록은 유지됩니다.");
                          }}
                        >
                          {job.is_active ? "삭제" : "다시 사용"}
                        </button>
                      </span>
                    );
                  })}
                </div>
              </div>
              <form
                className="admin-form compact-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  void run(async () => {
                    await apiFetch("/api/position-titles", {
                      method: "POST",
                      body: JSON.stringify({ name: positionName }),
                    });
                    setPositionName("");
                  }, "직위를 추가했습니다.");
                }}
              >
                <h3>직위 추가</h3>
                <p className="field-help">
                  대표·원장·사무국장·팀장·선임처럼 기관 안에서 사용하는 직위를
                  관리합니다. 직원 등록과 수정에서는 이 목록 중 하나를 선택합니다.
                </p>
                <div className="inline-fields">
                  <input
                    value={positionName}
                    onChange={(event) => setPositionName(event.target.value)}
                    placeholder="예: 부원장, 행정팀장"
                    required
                  />
                  <button className="button button-primary" disabled={saving}>
                    추가
                  </button>
                </div>
              </form>
              <div className="unit-group">
                <strong>직위</strong>
                <div>
                  {positionTitles
                    .filter(
                      (position) =>
                        position.is_active || showInactiveOrganization,
                    )
                    .map((position) => {
                      const usageLabel = `재직 직원 ${position.active_staff_count}명`;
                      return (
                        <span
                          className={`managed-chip ${
                            position.is_active ? "" : "inactive"
                          }`}
                          key={position.id}
                        >
                          <button
                            type="button"
                            className="chip-label"
                            aria-label={`${position.name} 이름 수정`}
                            onClick={() => {
                              const nextName = window.prompt(
                                "직위 이름을 변경합니다. 해당 직원의 직위도 함께 변경됩니다.",
                                position.name,
                              );
                              if (
                                !nextName?.trim() ||
                                nextName.trim() === position.name
                              ) return;
                              void run(async () => {
                                await apiFetch(
                                  `/api/position-titles/${position.id}`,
                                  {
                                    method: "PATCH",
                                    body: JSON.stringify({
                                      name: nextName.trim(),
                                    }),
                                  },
                                );
                              }, "직위 이름과 해당 직원정보를 함께 변경했습니다.");
                            }}
                          >
                            <span>{position.name}</span>
                            <small>
                              {position.is_active
                                ? usageLabel
                                : position.reference_count > 0
                                  ? `목록에서 삭제됨 · 기록 연결 ${position.reference_count}건`
                                  : "목록에서 삭제됨"}
                            </small>
                          </button>
                          <button
                            type="button"
                            className="chip-action"
                            disabled={saving}
                            onClick={() => {
                              const nextActive = !position.is_active;
                              if (
                                !window.confirm(
                                  nextActive
                                    ? `${position.name} 직위를 다시 사용하시겠습니까?`
                                    : `${position.name} 직위를 현재 선택 목록에서 삭제할까요?\n기존 직원·대화 기록은 삭제되지 않습니다.`,
                                )
                              ) return;
                              void run(async () => {
                                await apiFetch(
                                  `/api/position-titles/${position.id}`,
                                  {
                                    method: "PATCH",
                                    body: JSON.stringify({
                                      is_active: nextActive,
                                    }),
                                  },
                                );
                              }, nextActive
                                ? "직위를 다시 사용할 수 있게 했습니다."
                                : "직위를 현재 선택 목록에서 삭제했습니다. 기존 기록은 유지됩니다.");
                            }}
                          >
                            {position.is_active ? "삭제" : "다시 사용"}
                          </button>
                        </span>
                      );
                  })}
                </div>
              </div>
                </div>
              </details>
            </section>
          ) : null}

          {tab === "custom-room" ? (
            <section className="management-section">
              <div
                className={`admin-management-grid room-management-grid ${
                  mobilePane === "room" ? "mobile-detail-open" : ""
                }`}
              >
                <div className="admin-master-pane" ref={roomListRef}>
                  <div className="section-heading">
                    <div>
                      <h3>채팅방</h3>
                      <p>운영할 방을 선택하거나 필요한 방만 새로 만듭니다.</p>
                    </div>
                    <button
                      className="button button-primary button-nowrap"
                      type="button"
                      onClick={openNewRoomDraft}
                    >
                      채팅방 만들기
                    </button>
                  </div>
                  <input
                    className="room-list-search"
                    type="search"
                    value={roomQuery}
                    onChange={(event) => setRoomQuery(event.target.value)}
                    placeholder="채팅방 이름 검색"
                    aria-label="채팅방 검색"
                  />
                  <details className="admin-advanced-details room-filter-details">
                    <summary>검색 조건·종료된 방</summary>
                    <div className="admin-advanced-details-body">
                      <label>
                        참여 기준
                        <select
                          value={roomKindFilter}
                          onChange={(event) =>
                            setRoomKindFilter(event.target.value as RoomKindFilter)
                          }
                          aria-label="채팅방 참여 기준 필터"
                        >
                          <option value="all">모든 참여 기준</option>
                          {(Object.keys(roomKindLabels) as RoomKind[]).map((kind) => (
                            <option key={kind} value={kind}>
                              {roomKindLabels[kind]}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="check-row compact-check">
                        <input
                          type="checkbox"
                          checked={showInactiveRooms}
                          onChange={(event) => setShowInactiveRooms(event.target.checked)}
                        />
                        <span>종료된 채팅방 기록도 보기</span>
                      </label>
                    </div>
                  </details>
                  <div className="managed-room-list admin-scroll-list">
                    {visibleRooms.length === 0 ? (
                      <p className="empty-note">조건에 맞는 채팅방이 없습니다.</p>
                    ) : (
                      visibleRooms.map((room) => (
                        <button
                          type="button"
                          className={`${selectedRoomId === room.id ? "selected" : ""} ${
                            room.is_active ? "" : "inactive"
                          }`}
                          key={room.id}
                          aria-current={selectedRoomId === room.id ? "true" : undefined}
                          onClick={() => openManagedRoom(room)}
                        >
                          <span>
                            <strong>{room.name}</strong>
                            <small>
                              {room.is_active ? "운영 중" : "종료됨"} · {room.member_count}명 참여
                            </small>
                          </span>
                          <span className="room-kind-badge">{roomRuleLabel(room)}</span>
                        </button>
                      ))
                    )}
                  </div>
                </div>
                <div className="admin-detail-pane" ref={roomDetailRef}>
              {roomEditorOpen ? (
                <>
                <form
                className="admin-form custom-room-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  if (
                    selectedRoom &&
                    roomHasMemberChanges &&
                    !window.confirm(
                      `참여 직원을 변경할까요?\n추가 ${roomAddedMemberCount}명 · 제외 ${roomRemovedMemberCount}명`,
                    )
                  ) return;
                  void run(async () => {
                    if (selectedRoom) {
                      const changes: Record<string, unknown> = {};
                      if (roomName !== selectedRoom.name) changes.name = roomName;
                      if (
                        roomResidentScope !== selectedRoom.resident_scope ||
                        roomResidentScopeUnitId !==
                          (selectedRoom.resident_scope_unit_id ?? "")
                      ) {
                        changes.resident_scope = roomResidentScope;
                        changes.resident_scope_unit_id =
                          roomResidentScope === "floor"
                            ? roomResidentScopeUnitId || null
                            : null;
                      }
                      if (roomHasMemberChanges) changes.member_ids = roomMemberIds;
                      await apiFetch(`/api/admin/rooms/${selectedRoom.id}`, {
                        method: "PATCH",
                        body: JSON.stringify(changes),
                      });
                    } else {
                      await apiFetch("/api/admin/rooms", {
                        method: "POST",
                        body: JSON.stringify({
                          name: roomName,
                          kind: roomKind,
                          scope_unit_id:
                            ["business", "department", "floor", "team"].includes(roomKind)
                              ? roomScopeUnitId || null
                              : null,
                          job_code: roomKind === "job" ? roomJobCode || null : null,
                          member_ids: roomKind === "custom" ? roomMemberIds : [],
                          resident_scope:
                            roomKind === "floor" ? "floor" : roomResidentScope,
                          resident_scope_unit_id:
                            roomKind === "floor"
                              ? roomScopeUnitId || null
                              : roomResidentScope === "floor"
                                ? roomResidentScopeUnitId || null
                                : null,
                        }),
                      });
                    }
                    resetRoomDraft();
                  }, selectedRoom ? "채팅방 정보를 변경했습니다." : "새 채팅방을 만들었습니다.");
                }}
              >
                <button
                  type="button"
                  className="room-list-back"
                  onClick={() => {
                    setMobilePane("list");
                  }}
                >
                  ← 채팅방 목록으로
                </button>
                <div className="room-editor-heading">
                  <h3>{selectedRoom ? selectedRoom.name : "새 채팅방 만들기"}</h3>
                  {selectedRoom ? (
                    <span>
                      {selectedRoom.is_active ? "운영 중" : "종료됨"} · {selectedRoom.member_count}명 참여 · 대화 {selectedRoom.message_count}건 · 첨부 {selectedRoom.attachment_count}건
                      {selectedRoom.owner_name ? ` · 방장 ${selectedRoom.owner_name}` : ""}
                    </span>
                  ) : (
                    <span>이름과 참여할 직원을 정하면 바로 만들 수 있습니다.</span>
                  )}
                </div>
                <label>
                  채팅방 이름
                  <input
                    value={roomName}
                    onChange={(event) => setRoomName(event.target.value)}
                    placeholder="예: 3층 집중관리 협업방"
                    required
                  />
                </label>
                {selectedRoom ? (
                  <div className="room-rule-summary">
                    <span>참여 직원</span>
                    <strong>{roomRuleLabel(selectedRoom)}</strong>
                    <small>{selectedRoom.member_count}명이 참여하도록 설정되어 있습니다.</small>
                  </div>
                ) : (
                  <>
                <label>
                  참여 직원 정하기
                  <select
                    value={roomKind}
                    onChange={(event) => {
                      const nextKind = event.target.value as RoomKind;
                      setRoomKind(nextKind);
                      setRoomScopeUnitId("");
                      setRoomJobCode("");
                      if (nextKind === "floor") {
                        setRoomResidentScope("floor");
                        setRoomResidentScopeUnitId("");
                      }
                    }}
                  >
                    <option value="all">모든 재직 직원</option>
                    <option value="business">사업부 기준으로 자동 참여</option>
                    <option value="department">부서 기준으로 자동 참여</option>
                    <option value="team">팀 기준으로 자동 참여</option>
                    <option value="job">직종 기준으로 자동 참여</option>
                    <option value="custom">직원 직접 선택</option>
                  </select>
                </label>
                {["business", "department", "floor", "team"].includes(roomKind) ? (
                  <label>
                    연결 조직정보
                    <select
                      value={roomScopeUnitId}
                      onChange={(event) => setRoomScopeUnitId(event.target.value)}
                    >
                      <option value="">선택</option>
                      {units
                        .filter((unit) => unit.unit_type === roomKind && unit.is_active)
                        .map((unit) => (
                          <option key={unit.id} value={unit.id}>
                            {orgUnitDisplayName(unit)}
                          </option>
                        ))}
                    </select>
                  </label>
                ) : null}
                {roomKind === "job" ? (
                  <label>
                    연결 직종
                    <select
                      value={roomJobCode}
                      onChange={(event) => setRoomJobCode(event.target.value)}
                    >
                      <option value="">선택</option>
                      {roomJobCode &&
                      jobs.some(
                        (job) => job.code === roomJobCode && !job.is_active,
                      ) ? (
                        <option value={roomJobCode}>
                          {jobs.find((job) => job.code === roomJobCode)?.name}
                          {" (현재 목록에서 삭제됨)"}
                        </option>
                      ) : null}
                      {jobs.filter((job) => job.is_active).map((job) => (
                        <option key={job.code} value={job.code}>
                          {job.name}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : null}
                {roomKind !== "custom" ? (
                  <p className="muted-box">
                    해당 기준에 맞는 재직 직원이 자동으로 참여합니다.
                  </p>
                ) : null}
                  </>
                )}
                {roomKind === "custom" || Boolean(selectedRoom) ? (
                  <details className="admin-advanced-details room-member-details">
                    <summary>
                      {selectedRoom && roomKind !== "custom"
                        ? `참여자 예외 조정 · 현재 ${roomMemberIds.length}명`
                        : `참여 직원 선택 · 현재 ${roomMemberIds.length}명`}
                    </summary>
                    <div className="admin-advanced-details-body">
                      <p className="field-help">
                        {selectedRoom && roomKind !== "custom"
                          ? "자동 참여 기준은 그대로 두고 특별히 추가하거나 제외할 직원만 이름으로 찾습니다."
                          : "이름이나 소속을 검색한 뒤 참여할 직원을 선택합니다."}
                      </p>
                      {roomKind === "custom" ? (
                        <div className="room-member-change-summary">
                          <strong>선택한 직원 {selectedRoomMembers.length}명</strong>
                          {selectedRoomMembers.length === 0 ? (
                            <p>아직 선택한 직원이 없습니다.</p>
                          ) : (
                            <div className="selected-member-list">
                              {selectedRoomMembers.map((employee) => (
                                <button
                                  type="button"
                                  className="selected-member-chip"
                                  key={employee.id}
                                  onClick={() =>
                                    setRoomMemberIds((current) =>
                                      current.filter((id) => id !== employee.id),
                                    )
                                  }
                                  aria-label={`${employee.full_name} 선택 해제`}
                                >
                                  {employee.full_name} ×
                                </button>
                              ))}
                            </div>
                          )}
                        </div>
                      ) : selectedRoom ? (
                        <div className="room-member-change-summary">
                          <strong>
                            이번 변경 · 추가 {addedRoomMembers.length}명 · 제외{" "}
                            {removedRoomMembers.length}명
                          </strong>
                          {addedRoomMembers.length === 0 &&
                          removedRoomMembers.length === 0 ? (
                            <p>아직 참여자를 변경하지 않았습니다.</p>
                          ) : (
                            <div className="selected-member-list">
                              {addedRoomMembers.map((employee) => (
                                <button
                                  type="button"
                                  className="selected-member-chip added"
                                  key={`added-${employee.id}`}
                                  onClick={() =>
                                    setRoomMemberIds((current) =>
                                      current.filter((id) => id !== employee.id),
                                    )
                                  }
                                  aria-label={`${employee.full_name} 추가 취소`}
                                >
                                  + {employee.full_name} 취소
                                </button>
                              ))}
                              {removedRoomMembers.map((employee) => (
                                <button
                                  type="button"
                                  className="selected-member-chip removed"
                                  key={`removed-${employee.id}`}
                                  onClick={() =>
                                    setRoomMemberIds((current) =>
                                      current.includes(employee.id)
                                        ? current
                                        : [...current, employee.id],
                                    )
                                  }
                                  aria-label={`${employee.full_name} 제외 취소`}
                                >
                                  − {employee.full_name} 취소
                                </button>
                              ))}
                            </div>
                          )}
                        </div>
                      ) : null}
                      <label>
                        직원 찾기
                        <input
                          type="search"
                          value={roomMemberQuery}
                          onChange={(event) => setRoomMemberQuery(event.target.value)}
                          placeholder="이름, 아이디, 소속, 직종 또는 직위"
                        />
                      </label>
                      <fieldset className="member-picker">
                        <legend>선택 {roomMemberIds.length}명</legend>
                        {roomCandidates.length === 0 ? (
                          <p className="member-picker-empty">
                            {roomMemberQuery.trim()
                              ? "검색된 직원이 없습니다."
                              : "직원 이름이나 소속을 먼저 검색해 주세요."}
                          </p>
                        ) : (
                          roomCandidates.map((employee) => (
                            <label key={employee.id}>
                              <input
                                type="checkbox"
                                checked={roomMemberIds.includes(employee.id)}
                                onChange={(event) =>
                                  setRoomMemberIds((current) =>
                                    event.target.checked
                                      ? [...current, employee.id]
                                      : current.filter((id) => id !== employee.id),
                                  )
                                }
                              />
                              <span>
                                {employee.full_name}
                                <small>
                                  {[
                                    employee.business?.name,
                                    employee.department?.name,
                                    employee.team?.name,
                                    employee.job_name,
                                    employee.position_title,
                                  ]
                                    .filter(Boolean)
                                    .join(" · ") || "소속 미지정"}
                                </small>
                              </span>
                            </label>
                          ))
                        )}
                      </fieldset>
                    </div>
                  </details>
                ) : null}
                <label>
                  이 방에서 먼저 보여줄 어르신
                  <select
                    value={roomKind === "floor" ? "floor" : roomResidentScope}
                    disabled={roomKind === "floor"}
                    onChange={(event) => {
                      const nextScope = event.target.value as ResidentScope;
                      setRoomResidentScope(nextScope);
                      if (nextScope !== "floor") setRoomResidentScopeUnitId("");
                    }}
                  >
                    <option value="all">모든 어르신</option>
                    <option value="facility">시설 어르신 먼저</option>
                    <option value="daycare">주간보호 어르신 먼저</option>
                    <option value="homecare">방문요양 어르신 먼저</option>
                    <option value="floor">선택한 층 어르신 먼저</option>
                  </select>
                </label>
                {roomResidentScope === "floor" && roomKind !== "floor" ? (
                  <label>
                    우선 표시할 층
                    <select
                      value={roomResidentScopeUnitId}
                      onChange={(event) => setRoomResidentScopeUnitId(event.target.value)}
                    >
                      <option value="">선택</option>
                      {units
                        .filter((unit) => unit.unit_type === "floor" && unit.is_active)
                        .map((unit) => (
                          <option key={unit.id} value={unit.id}>
                            {orgUnitDisplayName(unit)}
                          </option>
                        ))}
                    </select>
                  </label>
                ) : null}
                <button
                  className="button button-primary button-large"
                  disabled={
                    saving ||
                    Boolean(selectedRoom && !selectedRoom.is_active) ||
                    Boolean(selectedRoom && !hasUnsavedRoomChanges) ||
                    roomName.trim().length < 2 ||
                    (roomKind === "custom" && roomMemberIds.length === 0) ||
                    (!selectedRoom &&
                      ["business", "department", "floor", "team"].includes(roomKind) &&
                      !roomScopeUnitId) ||
                    (!selectedRoom && roomKind === "job" && !roomJobCode) ||
                    (roomKind !== "floor" &&
                      roomResidentScope === "floor" &&
                      !roomResidentScopeUnitId)
                  }
                >
                  {selectedRoom ? "변경 저장" : "채팅방 만들기"}
                </button>
                {selectedRoom ? (
                  <details className="admin-advanced-details room-danger-details">
                    <summary>
                      {selectedRoom.is_active ? "이 방 종료" : "종료된 방 관리"}
                    </summary>
                  <div className="room-close-panel">
                    <strong>{selectedRoom.is_active ? "채팅방 종료" : "종료된 채팅방"}</strong>
                    <p>종료해도 기존 대화·사진·댓글 기록은 삭제되지 않습니다.</p>
                    {selectedRoom.is_active ? (
                      <button
                        type="button"
                        className="button button-danger"
                        disabled={saving}
                        onClick={() => {
                          if (
                            !window.confirm(
                              `"${selectedRoom.name}" 방을 종료할까요?\n참여자 화면에서 즉시 사라집니다.${
                                hasUnsavedRoomChanges
                                  ? "\n저장하지 않은 변경은 사라집니다."
                                  : ""
                              }`,
                            )
                          ) return;
                          void run(async () => {
                            await apiFetch(`/api/admin/rooms/${selectedRoom.id}`, {
                              method: "DELETE",
                            });
                            resetRoomDraft();
                          }, "채팅방을 종료했습니다. 기존 기록은 보관됩니다.");
                        }}
                      >
                        이 방 종료
                      </button>
                    ) : selectedRoom.kind === "custom" ? (
                      <p>
                        직접 선택 방은 과거 참여자가 잘못 복구되지 않도록 다시 열지
                        않습니다. 필요한 참여자를 확인해 새 채팅방을 만들어 주세요.
                      </p>
                    ) : (
                      <button
                        type="button"
                        className="button button-primary"
                        disabled={saving}
                        onClick={() => {
                          if (
                            !window.confirm(
                              `"${selectedRoom.name}" 방을 다시 열까요?\n현재 참여 기준에 맞는 직원에게 즉시 다시 표시됩니다.${
                                hasUnsavedRoomChanges
                                  ? "\n저장하지 않은 변경은 사라집니다."
                                  : ""
                              }`,
                            )
                          ) return;
                          void run(async () => {
                            await apiFetch(`/api/admin/rooms/${selectedRoom.id}/restore`, {
                              method: "POST",
                              body: "{}",
                            });
                            resetRoomDraft();
                          }, "채팅방을 복구했습니다.");
                        }}
                      >
                        이 방 복구
                      </button>
                    )}
                  </div>
                  </details>
                ) : null}
                    </form>
                    {selectedRoom ? (
                      <AdminConversationReview
                        key={selectedRoom.id}
                        roomId={selectedRoom.id}
                        roomName={selectedRoom.name}
                      />
                    ) : null}
                </>
                  ) : (
                    <div className="detail-placeholder room-detail-placeholder">
                      <strong>관리할 채팅방을 선택해 주세요.</strong>
                      <p>새 채팅방은 위의 ‘채팅방 만들기’를 누르면 됩니다.</p>
                    </div>
                  )}
                </div>
              </div>
            </section>
          ) : null}

          {tab === "residents" ? (
            <section className="resident-order-admin" ref={residentListRef}>
              <div className="section-heading">
                <div>
                  <h3>어르신</h3>
                  <p>현재 명단을 확인하고 필요한 정보만 갱신합니다.</p>
                </div>
              </div>

              <BulkTransferPanel
                entity="resident"
                disabled={reviewOnly}
                onApplied={onDataChanged}
              />

              {codedSyntheticMode ? (
                <div className="information-update-card" aria-label="본선용 DEV 어르신 자료 안내">
                  <div>
                    <strong>
                      본선용 DEV는 코드화된 합성 어르신 자료만 사용합니다. 케어포 명단
                      불러오기는 비활성화되어 있습니다.
                    </strong>
                  </div>
                </div>
              ) : (
                <div className="information-update-card" aria-label="어르신정보 갱신 상태">
                  <div>
                    <span className="information-update-source">가져온 곳: 케어포</span>
                    <strong>
                      {careforRosterStatus &&
                      Object.values(careforRosterStatus.sources).every(
                        (source) => source.status === "captured",
                      )
                        ? "시설·주간보호·방문요양 자료를 확인할 수 있습니다."
                        : "준비된 서비스의 어르신정보를 확인해 주세요."}
                    </strong>
                    <span>
                      {careforRosterStatus?.generated_at
                        ? `마지막 자료: ${new Date(careforRosterStatus.generated_at).toLocaleString("ko-KR")}`
                        : "아직 확인한 자료가 없습니다."}
                    </span>
                  </div>
                  <div className="information-update-actions">
                    <button
                      type="button"
                      className="button button-primary"
                      onClick={() =>
                        residentSyncRef.current?.scrollIntoView({
                          behavior: "smooth",
                          block: "start",
                        })
                      }
                    >
                      최신 어르신정보 확인
                    </button>
                  </div>
                </div>
              )}

              <details className="admin-advanced-details">
                <summary>직접 어르신 추가</summary>
                <form className="resident-add-form admin-advanced-details-body" onSubmit={addResident}>
                <div>
                  <h4>새 어르신 직접 추가</h4>
                  <p>화면에는 실명 대신 가명만 입력해 주세요.</p>
                </div>
                <label>
                  가명
                  <input
                    value={residentDraft.display_name}
                    onChange={(event) =>
                      setResidentDraft((current) => ({
                        ...current,
                        display_name: event.target.value,
                      }))
                    }
                    placeholder="예: 시설(가명)050"
                    minLength={2}
                    maxLength={100}
                    required
                  />
                </label>
                <label>
                  이용 서비스
                  <select
                    value={residentDraft.service_type}
                    onChange={(event) =>
                      setResidentDraft((current) => ({
                        ...current,
                        service_type: event.target.value as
                          | "facility"
                          | "daycare"
                          | "homecare",
                        floor_id:
                          event.target.value === "homecare" ? "" : current.floor_id,
                      }))
                    }
                  >
                    <option value="facility">시설</option>
                    <option value="daycare">주간보호</option>
                    <option value="homecare">방문요양</option>
                  </select>
                </label>
                {residentDraft.service_type !== "homecare" ? (
                  <label>
                    생활실·방·구역
                    <select
                      value={residentDraft.floor_id}
                      onChange={(event) =>
                        setResidentDraft((current) => ({
                          ...current,
                          floor_id: event.target.value,
                        }))
                      }
                      required={residentDraft.service_type === "facility"}
                    >
                      <option value="">
                        {residentDraft.service_type === "facility"
                          ? "생활공간을 선택하세요"
                          : "미지정"}
                      </option>
                      {units
                        .filter(
                          (unit) => unit.unit_type === "floor" && unit.is_active,
                        )
                        .map((unit) => (
                          <option key={unit.id} value={unit.id}>
                            {unit.name}
                          </option>
                        ))}
                    </select>
                  </label>
                ) : null}
                <button
                  className="button button-secondary"
                  disabled={
                    saving ||
                    residentDraft.display_name.trim().length < 2 ||
                    (residentDraft.service_type === "facility" &&
                      !residentDraft.floor_id)
                  }
                >
                  명단에 추가
                </button>
                </form>
              </details>

              <details className="admin-advanced-details resident-list-details">
                <summary>현재 어르신 명단과 순서 관리 ({residents.length}명)</summary>
                <div className="admin-advanced-details-body resident-order-admin">
              <div className="resident-order-toolbar">
                <div>
                  <strong>표시 순서 바꾸기</strong>
                  <span>같은 서비스 안에서 화살표로 옮긴 뒤 저장하세요.</span>
                </div>
                <button
                  type="button"
                  className="button button-secondary"
                  disabled={saving || effectiveResidentOrder.length === 0}
                  onClick={() => {
                    void run(async () => {
                      await apiFetch("/api/admin/residents/order", {
                        method: "PATCH",
                        body: JSON.stringify({ resident_ids: effectiveResidentOrder }),
                      });
                    }, "어르신 명단 순서를 저장했습니다.");
                  }}
                >
                  순서 저장
                </button>
              </div>

              {(["facility", "daycare", "homecare"] as const).map((serviceType) => {
                const serviceResidents = effectiveResidentOrder
                  .map((id) => residents.find((resident) => resident.id === id))
                  .filter(
                    (resident): resident is Resident =>
                      resident !== undefined && resident.service_type === serviceType,
                  );
                const label = {
                  facility: "시설",
                  daycare: "주간보호",
                  homecare: "방문요양",
                }[serviceType];
                const residentRow = (resident: Resident, index: number, list: Resident[]) => (
                  <div className="resident-order-row" key={resident.id}>
                    <span>
                      <strong>{resident.display_name}</strong>
                      <small>{resident.floor?.name ?? `${label} · 생활공간 미지정`}</small>
                    </span>
                    <div>
                      {resident.service_type !== "homecare" ? (
                        <label className="resident-space-select">
                          <span className="sr-only">
                            {resident.display_name} 생활실·방·구역
                          </span>
                          <select
                            value={resident.floor?.id ?? ""}
                            disabled={saving}
                            aria-label={`${resident.display_name} 생활실·방·구역 변경`}
                            onChange={(event) => {
                              const floorId = event.target.value || null;
                              void run(async () => {
                                await apiFetch(`/api/admin/residents/${resident.id}`, {
                                  method: "PATCH",
                                  body: JSON.stringify({ floor_id: floorId }),
                                });
                              }, floorId
                                ? "어르신 생활공간을 변경했습니다."
                                : "어르신 생활공간을 미지정으로 변경했습니다.");
                            }}
                          >
                            <option value="">미지정</option>
                            {resident.floor && !resident.floor.is_active ? (
                              <option value={resident.floor.id}>
                                {resident.floor.name} (현재 목록에서 삭제됨)
                              </option>
                            ) : null}
                            {units
                              .filter(
                                (unit) => unit.unit_type === "floor" && unit.is_active,
                              )
                              .map((unit) => (
                                <option key={unit.id} value={unit.id}>
                                  {unit.name}
                                </option>
                              ))}
                          </select>
                        </label>
                      ) : null}
                      <button
                        type="button"
                        disabled={index === 0}
                        onClick={() => moveResident(resident.id, -1)}
                        aria-label={`${resident.display_name} 위로`}
                      >
                        ↑
                      </button>
                      <button
                        type="button"
                        disabled={index === list.length - 1}
                        onClick={() => moveResident(resident.id, 1)}
                        aria-label={`${resident.display_name} 아래로`}
                      >
                        ↓
                      </button>
                      <button
                        type="button"
                        className="resident-end-button"
                        disabled={saving}
                        onClick={() => {
                          if (
                            !window.confirm(
                              `${resident.display_name} 어르신을 이용 종료할까요?\n` +
                                "채팅 선택 목록에서는 숨겨지지만 기존 대화 기록은 보존됩니다.",
                            )
                          ) {
                            return;
                          }
                          void run(async () => {
                            await apiFetch(
                              `/api/admin/residents/${resident.id}`,
                              { method: "DELETE" },
                            );
                          }, "어르신을 이용 종료했습니다. 기존 기록은 보존됩니다.");
                        }}
                      >
                        이용 종료
                      </button>
                    </div>
                  </div>
                );
                return (
                  <div className="resident-order-group" key={serviceType}>
                    <h4>
                      {label} 어르신 <span>{serviceResidents.length}명</span>
                    </h4>
                    {serviceResidents.length === 0 ? (
                      <p className="empty-note">
                        현재 이용 중인 {label} 어르신이 없습니다.
                      </p>
                    ) : (
                      serviceResidents.map((resident, index) =>
                        residentRow(resident, index, serviceResidents),
                      )
                    )}
                  </div>
                );
              })}
                </div>
              </details>
            </section>
          ) : null}

          {tab === "residents" && !codedSyntheticMode ? (
            <section className="resident-sync-admin" ref={residentSyncRef}>
              <div className="section-heading">
                <div>
                  <h3>어르신정보 갱신</h3>
                  <p>
                    준비된 서비스의 최신 자료를 비교하고, 바뀐 어르신만 확인합니다.
                  </p>
                </div>
              </div>

              <details className="admin-advanced-details">
                <summary>갱신 방법과 개인정보 안내</summary>
                <div className="carefor-roster-guide admin-advanced-details-body">
                  <strong>사용 방법</strong>
                  <span>
                    시설·주간보호·방문요양 중 준비된 자료를 확인한 뒤, 바뀐 어르신만
                    선택해 저장합니다. 화면에는 가명만 표시합니다.
                  </span>
                </div>
              </details>

              <div className="carefor-roster-sources">
                {(["facility", "daycare", "homecare"] as const).map((serviceType) => {
                  const source = careforRosterStatus?.sources[serviceType];
                  const ready = source?.status === "captured";
                  return (
                    <article className={ready ? "ready" : "waiting"} key={serviceType}>
                      <div>
                        <strong>{residentServiceLabels[serviceType]}</strong>
                        <span className={ready ? "ready" : "waiting"}>
                          {ready ? "명단 준비됨" : "케어포 로그인 필요"}
                        </span>
                      </div>
                      {ready ? (
                        <>
                          <p>
                            확인 가능한 어르신 <b>{source.resident_count}명</b>
                          </p>
                          <small>
                            {source.captured_at
                              ? `자료 시각: ${new Date(source.captured_at).toLocaleString("ko-KR")}`
                              : "자료 시각 미확인"}
                          </small>
                          <button
                            type="button"
                            className="button button-primary"
                            disabled={saving}
                            onClick={() => void previewCareforRoster(serviceType)}
                          >
                            최신정보 확인
                          </button>
                        </>
                      ) : (
                        <p>이 서비스의 자료를 먼저 준비해 주세요. 기존 명단은 바뀌지 않습니다.</p>
                      )}
                    </article>
                  );
                })}
              </div>

              {residentSyncBatches.length > 0 ? (
                <details className="admin-advanced-details">
                  <summary>지난 갱신 기록</summary>
                  <label className="resident-sync-history admin-advanced-details-body">
                    확인할 기록
                    <select
                      value={residentSyncBatch?.id ?? ""}
                      disabled={residentSyncLoading}
                      onChange={(event) => {
                        if (event.target.value) {
                          void openResidentSyncBatch(event.target.value);
                        } else {
                          setResidentSyncBatch(null);
                        }
                      }}
                    >
                      <option value="">지난 기록을 선택하세요</option>
                      {residentSyncBatches.map((batch) => (
                        <option value={batch.id} key={batch.id}>
                          {new Date(batch.created_at).toLocaleString("ko-KR")} ·{" "}
                          {residentSyncStatusLabels[batch.status]}
                        </option>
                      ))}
                    </select>
                  </label>
                </details>
              ) : null}

              {residentSyncLoading ? (
                <p className="empty-note">동기화 기록을 불러오는 중입니다.</p>
              ) : null}

              {residentSyncBatch ? (
                <div className="resident-sync-preview">
                  <div className="information-result-summary">
                    <div>
                      <strong>
                        {residentSyncIsFullyComplete
                          ? "저장이 끝났습니다."
                          : residentAttentionCount > 0
                            ? `확인할 어르신 ${residentAttentionCount}명`
                            : "바뀐 어르신이 없습니다."}
                      </strong>
                      <span>
                        가져온 곳: 케어포 · 확인 시각:{" "}
                        {new Date(residentSyncBatch.created_at).toLocaleString("ko-KR")}
                      </span>
                    </div>
                    <span
                      className={
                        residentSyncIsFullyComplete || residentAttentionCount === 0
                          ? "current"
                          : "attention"
                      }
                    >
                      {residentSyncIsFullyComplete
                        ? "저장 완료"
                        : residentAttentionCount > 0
                          ? "확인 필요"
                          : "최신"}
                    </span>
                  </div>

                  <details className="admin-advanced-details">
                    <summary>변경 요약과 원본 정보</summary>
                    <div className="admin-advanced-details-body">
                      <p className="field-help">원본: {residentSyncBatch.original_name}</p>
                      <div className="resident-sync-summary" aria-label="변경사항 요약">
                        {(
                          [
                            ["new", "새로 추가"],
                            ["update", "정보 바뀜"],
                            ["deactivate", "이용 중지"],
                            ["conflict", "직접 확인"],
                            ["unchanged", "그대로"],
                          ] as const
                        ).map(([key, label]) => (
                          <div className={`summary-${key}`} key={key}>
                            <strong>{residentSyncBatch.summary[key] ?? 0}</strong>
                            <span>{label}</span>
                          </div>
                        ))}
                      </div>
                      <label className="check-row compact-check">
                        <input
                          type="checkbox"
                          checked={showUnchangedSyncItems}
                          onChange={(event) =>
                            setShowUnchangedSyncItems(event.target.checked)
                          }
                        />
                        <span>바뀌지 않은 어르신도 보기</span>
                      </label>
                    </div>
                  </details>

                  {residentSyncHasNoChanges ? (
                    <div className="resident-sync-no-changes" role="status">
                      <div>
                        <strong>바뀐 어르신이 없습니다.</strong>
                        <span>
                          현재 명단과 같은 내용입니다. 저장하거나 선택할 것은 없습니다.
                        </span>
                      </div>
                      <button
                        type="button"
                        className="button button-secondary"
                        onClick={() => setResidentSyncBatch(null)}
                      >
                        확인
                      </button>
                    </div>
                  ) : (
                    <>
                      {residentSyncBatch.status !== "applied" ? (
                        <div className="resident-sync-controls">
                          <label className="checkbox-row">
                            <input
                              type="checkbox"
                              checked={
                                pendingResidentSyncItems.length > 0 &&
                                pendingResidentSyncItems.every((item) =>
                                  selectedResidentSyncItemIds.includes(item.id),
                                )
                              }
                              disabled={pendingResidentSyncItems.length === 0}
                              onChange={(event) =>
                                setSelectedResidentSyncItemIds(
                                  event.target.checked
                                    ? pendingResidentSyncItems.map((item) => item.id)
                                    : [],
                                )
                              }
                            />
                            저장할 사람 모두 선택
                          </label>
                        </div>
                      ) : null}

                      <div className="resident-sync-list">
                        {visibleResidentSyncItems.length === 0 ? (
                          <p className="empty-note">표시할 변경사항이 없습니다.</p>
                        ) : (
                          visibleResidentSyncItems.map((item) => {
                            const current = item.current_snapshot;
                            const incoming = item.incoming_payload;
                            const selectable =
                              item.status === "pending" &&
                              ["new", "update", "deactivate"].includes(item.change_type);
                            const currentLocation = current
                              ? [
                                  residentServiceLabels[current.service_type] ??
                                    current.service_type,
                                  current.floor,
                                  current.room_name,
                                ]
                                  .filter(Boolean)
                                  .join(" · ")
                              : "현재 명단 없음";
                            const incomingLocation = [
                              residentServiceLabels[incoming.service_type] ??
                                incoming.service_type,
                              incoming.floor,
                              incoming.room_name,
                            ]
                              .filter(Boolean)
                              .join(" · ");
                            return (
                              <article
                                className={`resident-sync-item change-${item.change_type}`}
                                key={item.id}
                              >
                                <label className="resident-sync-item-check">
                                  <input
                                    type="checkbox"
                                    disabled={!selectable}
                                    checked={selectedResidentSyncItemIds.includes(item.id)}
                                    onChange={(event) =>
                                      setSelectedResidentSyncItemIds((currentIds) =>
                                        event.target.checked
                                          ? [...currentIds, item.id]
                                          : currentIds.filter((id) => id !== item.id),
                                      )
                                    }
                                  />
                                  <span
                                    className={`resident-sync-change-badge ${item.change_type}`}
                                  >
                                    {residentSyncChangeLabels[item.change_type]}
                                  </span>
                                </label>
                                <div className="resident-sync-item-body">
                                  <div>
                                    <strong>
                                      {incoming.display_name ||
                                        current?.display_name ||
                                        "이름 확인 필요"}
                                    </strong>
                                    <small>{item.external_id}</small>
                                  </div>
                                  {item.change_type === "new" ? (
                                    <p>{incomingLocation || "소속 정보 없음"}</p>
                                  ) : (
                                    <div className="resident-sync-diff">
                                      <span>
                                        <small>현재</small>
                                        {current?.display_name ?? "없음"} ·{" "}
                                        {currentLocation}
                                      </span>
                                      <b aria-hidden="true">→</b>
                                      <span>
                                        <small>
                                          {item.change_type === "deactivate"
                                            ? "중지 후"
                                            : "반영 후"}
                                        </small>
                                        {item.change_type === "deactivate"
                                          ? "채팅 선택 목록에서 숨김"
                                          : `${incoming.display_name} · ${
                                              incomingLocation || "소속 정보 없음"
                                            }`}
                                      </span>
                                    </div>
                                  )}
                                  {item.conflict_reason ? (
                                    <p className="resident-sync-conflict">
                                      {item.conflict_reason}
                                    </p>
                                  ) : null}
                                  {item.status === "applied" ? (
                                    <p className="resident-sync-applied">승인·반영 완료</p>
                                  ) : null}
                                </div>
                              </article>
                            );
                          })
                        )}
                      </div>

                      {residentSyncIsFullyComplete ? (
                        <div className="resident-sync-complete" role="status">
                          <div>
                            <strong>이 작업은 이미 끝났습니다.</strong>
                            <span>더 누를 것은 없습니다. 저장된 명단을 확인해 보세요.</span>
                          </div>
                          <button
                            type="button"
                            className="button button-secondary"
                            onClick={() =>
                              residentListRef.current?.scrollIntoView({
                                behavior: "smooth",
                                block: "start",
                              })
                            }
                          >
                            저장된 어르신 보기
                          </button>
                        </div>
                      ) : (
                        <div className="resident-sync-approval">
                          <div>
                            <strong>{selectedResidentSyncItemIds.length}명 선택됨</strong>
                            <span>체크한 어르신만 저장됩니다.</span>
                          </div>
                          <button
                            type="button"
                            className="button button-primary"
                            disabled={saving || selectedResidentSyncItemIds.length === 0}
                            onClick={() => void applySelectedResidentSyncItems()}
                          >
                            선택한 {selectedResidentSyncItemIds.length}명 저장하기
                          </button>
                        </div>
                      )}
                    </>
                  )}
                </div>
              ) : (
                <p className="empty-note resident-sync-idle">
                  지금 확인 중인 변경사항이 없습니다. 준비된 서비스의 “최신정보 확인”을
                  눌러 주세요.
                </p>
              )}
            </section>
          ) : null}
        </div>
      </aside>
    </div>
  );
}
