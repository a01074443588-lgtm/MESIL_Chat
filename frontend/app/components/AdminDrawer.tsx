"use client";

import { FormEvent, type RefObject, useEffect, useMemo, useRef, useState } from "react";
import { apiFetch } from "../api";
import type {
  CareforRosterStatus,
  JobCode,
  ManagedRoom,
  OrgUnit,
  PositionTitle,
  Resident,
  ResidentSyncBatch,
  ResidentSyncItem,
  UnitType,
  User,
} from "../types";

type Tab =
  | "employees"
  | "new-employee"
  | "organization"
  | "custom-room"
  | "residents";
type RoomKind = ManagedRoom["kind"];
type ResidentScope = ManagedRoom["resident_scope"];
type EmployeeStatusFilter = "all" | User["employment_status"];
type RoomKindFilter = "all" | RoomKind;

const unitLabels: Record<UnitType, string> = {
  business: "사업부",
  department: "부서",
  floor: "어르신 생활층",
  team: "팀",
};
const staffUnitTypes = ["business", "department", "team"] as const;

const employeeStatusLabels: Record<User["employment_status"], string> = {
  active: "재직",
  leave: "휴직",
  retired: "퇴사",
};

const roomKindLabels: Record<RoomKind, string> = {
  all: "전체 자동",
  business: "사업부 자동",
  department: "부서 자동",
  floor: "과거 층 자동",
  team: "팀 자동",
  job: "직종 자동",
  custom: "직원 직접 선택",
};

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

function isPracticeResidentSyncBatch(batch: ResidentSyncBatch) {
  const name = batch.original_name.toLocaleLowerCase("ko-KR");
  return name.includes("연습") || name.includes("example");
}

const residentServiceLabels: Record<string, string> = {
  facility: "시설",
  daycare: "주간보호",
  homecare: "방문요양",
};

const legacyPositionByJobCode: Record<string, string> = {
  representative: "대표",
  facility_director: "원장",
  office_director: "사무국장",
};

function roomRuleLabel(room: ManagedRoom): string {
  const scope = room.scope_name ?? room.job_name;
  return scope ? `${roomKindLabels[room.kind]} · ${scope}` : roomKindLabels[room.kind];
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
const emptyResidentDraft = {
  display_name: "",
  service_type: "facility" as "facility" | "daycare" | "homecare",
  floor_id: "",
};

function UnitSelect({
  type,
  units,
  value,
  onChange,
}: {
  type: UnitType;
  units: OrgUnit[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label>
      {unitLabels[type]}
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">미지정</option>
        {units
          .filter((unit) => unit.unit_type === type && unit.is_active)
          .map((unit) => (
            <option key={unit.id} value={unit.id}>
              {unit.name}
            </option>
          ))}
      </select>
    </label>
  );
}

function JobSelect({
  jobs,
  value,
  onChange,
  allowUnassigned = false,
}: {
  jobs: JobCode[];
  value: string;
  onChange: (value: string) => void;
  allowUnassigned?: boolean;
}) {
  return (
    <label>
      직종
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        required={!allowUnassigned}
      >
        {allowUnassigned ? <option value="">미지정(확인 전)</option> : null}
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
}: {
  positions: PositionTitle[];
  value: string;
  onChange: (value: string) => void;
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
      >
        <option value="">미지정</option>
        {currentIsInactive ? <option value={value}>{value} (중지됨)</option> : null}
        {positions
          .filter((position) => position.is_active)
          .map((position) => (
            <option key={position.id} value={position.name}>
              {position.name}
            </option>
          ))}
      </select>
    </label>
  );
}

function employeePayload(draft: EmployeeDraft, includeCredentials: boolean) {
  return {
    ...(includeCredentials
      ? {
          username: draft.username.trim(),
          password: draft.password,
        }
      : {}),
    full_name: draft.full_name.trim(),
    employee_code: draft.employee_code.trim() || null,
    role: draft.role,
    can_process_records: draft.can_process_records,
    business_id: draft.business_id || null,
    department_id: draft.department_id || null,
    job_code: draft.job_code || null,
    position_title: draft.position_title.trim() || null,
    floor_id: null,
    team_id: draft.team_id || null,
  };
}

function draftFromEmployee(employee: User): EmployeeDraft {
  return {
    username: employee.username,
    full_name: employee.full_name,
    password: "",
    employee_code: employee.employee_code ?? "",
    role: employee.role,
    can_process_records: employee.can_process_records,
    business_id: employee.business?.id ?? "",
    department_id: employee.department?.id.toString() ?? "",
    job_code: employee.job_code ?? "",
    position_title: employee.position_title ?? "",
    team_id: employee.team?.id.toString() ?? "",
  };
}

export function AdminDrawer({
  open,
  onClose,
  units,
  jobs,
  positionTitles,
  employees,
  managedRooms,
  residents,
  currentUserId,
  onDataChanged,
}: {
  open: boolean;
  onClose: () => void;
  units: OrgUnit[];
  jobs: JobCode[];
  positionTitles: PositionTitle[];
  employees: User[];
  managedRooms: ManagedRoom[];
  residents: Resident[];
  currentUserId: string;
  onDataChanged: () => Promise<void>;
}) {
  const [tab, setTab] = useState<Tab>("employees");
  const [employeeDraft, setEmployeeDraft] = useState<EmployeeDraft>(emptyEmployee);
  const [selectedEmployeeId, setSelectedEmployeeId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<EmployeeDraft>(emptyEmployee);
  const [employeeQuery, setEmployeeQuery] = useState("");
  const [employeeStatusFilter, setEmployeeStatusFilter] =
    useState<EmployeeStatusFilter>("all");
  const [unitType, setUnitType] = useState<UnitType>("business");
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
  const [roomMemberQuery, setRoomMemberQuery] = useState("");
  const [resetPassword, setResetPassword] = useState("");
  const [statusMessage, setStatusMessage] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [showInactiveRooms, setShowInactiveRooms] = useState(false);
  const [showInactiveOrganization, setShowInactiveOrganization] = useState(false);
  const [roomQuery, setRoomQuery] = useState("");
  const [roomKindFilter, setRoomKindFilter] = useState<RoomKindFilter>("all");
  const [residentOrder, setResidentOrder] = useState<string[]>([]);
  const [residentDraft, setResidentDraft] = useState(emptyResidentDraft);
  const [residentSyncBatches, setResidentSyncBatches] = useState<ResidentSyncBatch[]>([]);
  const [residentSyncBatch, setResidentSyncBatch] = useState<ResidentSyncBatch | null>(null);
  const [selectedResidentSyncItemIds, setSelectedResidentSyncItemIds] =
    useState<string[]>([]);
  const [showUnchangedSyncItems, setShowUnchangedSyncItems] = useState(false);
  const [residentSyncLoading, setResidentSyncLoading] = useState(false);
  const [careforRosterStatus, setCareforRosterStatus] =
    useState<CareforRosterStatus | null>(null);
  const employeeDetailRef = useRef<HTMLDivElement>(null);
  const roomDetailRef = useRef<HTMLDivElement>(null);
  const drawerContentRef = useRef<HTMLDivElement>(null);
  const residentListRef = useRef<HTMLElement>(null);
  const residentSyncRef = useRef<HTMLElement>(null);

  useEffect(() => {
    drawerContentRef.current?.scrollTo({ top: 0, behavior: "auto" });
  }, [tab]);

  useEffect(() => {
    if (!open || tab !== "residents") return;
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
  }, [open, tab]);

  const selectedEmployee = useMemo(
    () => employees.find((employee) => employee.id === selectedEmployeeId) ?? null,
    [employees, selectedEmployeeId],
  );
  const positionCleanupCandidates = useMemo(
    () =>
      employees.filter(
        (employee) =>
          employee.employment_status === "active" &&
          employee.job_code !== null &&
          legacyPositionByJobCode[employee.job_code] !== undefined,
      ),
    [employees],
  );
  const selectedLegacyPosition =
    selectedEmployee?.job_code === null || selectedEmployee?.job_code === undefined
      ? null
      : legacyPositionByJobCode[selectedEmployee.job_code] ?? null;
  const selectedRoom = useMemo(
    () => managedRooms.find((room) => room.id === selectedRoomId) ?? null,
    [managedRooms, selectedRoomId],
  );
  const visibleEmployees = useMemo(() => {
    const query = employeeQuery.trim().toLocaleLowerCase("ko-KR");
    return employees.filter((employee) => {
      if (
        employeeStatusFilter !== "all" &&
        employee.employment_status !== employeeStatusFilter
      ) {
        return false;
      }
      if (!query) return true;
      return [
        employee.full_name,
        employee.username,
        employee.employee_code,
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
  }, [employeeQuery, employeeStatusFilter, employees]);
  const visibleRooms = useMemo(() => {
    const query = roomQuery.trim().toLocaleLowerCase("ko-KR");
    return managedRooms.filter((room) => {
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
    return employees
      .filter((employee) => employee.employment_status === "active")
      .filter((employee) => {
        if (!query) return true;
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

  function resetRoomDraft() {
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
          return (
            item?.service_type === resident.service_type &&
            item.roster_source === resident.roster_source
          );
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

  async function addEmployee(event: FormEvent) {
    event.preventDefault();
    await run(async () => {
      await apiFetch("/api/employees", {
        method: "POST",
        body: JSON.stringify(employeePayload(employeeDraft, true)),
      });
      setEmployeeDraft(emptyEmployee);
      setTab("employees");
    }, "직원 계정을 등록했습니다.");
  }

  async function updateEmployee(event: FormEvent) {
    event.preventDefault();
    if (!selectedEmployee) return;
    await run(async () => {
      await apiFetch(`/api/employees/${selectedEmployee.id}`, {
        method: "PATCH",
        body: JSON.stringify(employeePayload(editDraft, false)),
      });
    }, "직원정보와 자동 채팅방 배정을 갱신했습니다.");
  }

  if (!open) return null;

  return (
    <div className="drawer-layer">
      <button className="drawer-backdrop" onClick={onClose} aria-label="관리 화면 닫기" />
      <aside className="admin-drawer" aria-label="관리자 설정">
        <header className="drawer-header">
          <div>
            <span className="eyebrow">관리자</span>
            <h2>직원·조직 관리</h2>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="닫기">
            ×
          </button>
        </header>
        <nav className="admin-tabs" aria-label="관리 메뉴">
          {[
            ["employees", "직원 목록"],
            ["new-employee", "직원 등록"],
            ["organization", "조직정보"],
            ["custom-room", "현재 개설방"],
            ["residents", "어르신 관리"],
          ].map(([value, label]) => (
            <button
              className={tab === value ? "active" : ""}
              key={value}
              onClick={() => {
                setTab(value as Tab);
                setError("");
                setStatusMessage("");
              }}
            >
              {label}
            </button>
          ))}
        </nav>
        <div className="drawer-content" ref={drawerContentRef}>
          {statusMessage ? <p className="form-success">{statusMessage}</p> : null}
          {error ? (
            <p className="form-error" role="alert">
              {error}
            </p>
          ) : null}

          {tab === "employees" ? (
            <section className="management-section">
              <div className="admin-management-grid">
                <div className="admin-master-pane">
                  <div className="section-heading">
                    <div>
                      <h3>직원 목록</h3>
                      <p>직원을 선택하면 오른쪽에서 바로 관리할 수 있습니다.</p>
                    </div>
                    <span>
                      {visibleEmployees.length}/{employees.length}명
                    </span>
                  </div>
                  {positionCleanupCandidates.length > 0 ? (
                    <section className="position-cleanup-box" aria-label="직위 정리 필요">
                      <div>
                        <strong>직위로 옮길 직원 {positionCleanupCandidates.length}명</strong>
                        <p>
                          시설장·대표자·사무국장은 직위로 정리하고 실제 직종을 따로
                          선택합니다.
                        </p>
                      </div>
                      <div className="position-cleanup-list">
                        {positionCleanupCandidates.map((employee) => {
                          const suggestedPosition =
                            legacyPositionByJobCode[employee.job_code ?? ""];
                          return (
                            <button
                              className="button button-secondary"
                              type="button"
                              key={employee.id}
                              onClick={() => {
                                const draft = draftFromEmployee(employee);
                                setSelectedEmployeeId(employee.id);
                                setEditDraft({
                                  ...draft,
                                  job_code: "",
                                  position_title:
                                    draft.position_title || suggestedPosition,
                                });
                                setEmployeeStatusFilter("active");
                                setEmployeeQuery("");
                                revealDetailOnSmallScreen(employeeDetailRef);
                              }}
                            >
                              {employee.full_name}
                              <small>
                                현재 {employee.job_name} → 직위 {suggestedPosition}
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
                          key={employee.id}
                          className={`employee-card ${
                            selectedEmployeeId === employee.id ? "selected" : ""
                          }`}
                          onClick={() => {
                            setSelectedEmployeeId(employee.id);
                            setEditDraft(draftFromEmployee(employee));
                            setResetPassword("");
                            revealDetailOnSmallScreen(employeeDetailRef);
                          }}
                        >
                          <span className="avatar">{employee.full_name.slice(0, 1)}</span>
                          <span className="employee-copy">
                            <strong>{employee.full_name}</strong>
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
                          <span className="employee-statuses">
                            <span
                              className={`status-dot ${employee.employment_status}`}
                            >
                              {employeeStatusLabels[employee.employment_status]}
                            </span>
                            {employee.must_change_password &&
                            employee.employment_status === "active" ? (
                              <span className="password-change-badge">변경 필요</span>
                            ) : null}
                            {employee.can_process_records &&
                            employee.employment_status === "active" ? (
                              <span className="password-change-badge">업무함</span>
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
                      <div className="section-heading">
                        <div>
                          <h3>{selectedEmployee.full_name} 정보수정</h3>
                          <p>
                            {employeeStatusLabels[selectedEmployee.employment_status]} ·{" "}
                            {selectedEmployee.username}
                          </p>
                        </div>
                      </div>
                      {selectedEmployee.id === currentUserId ? (
                        <p className="muted-box">
                          현재 로그인한 관리자입니다. 비밀번호 변경은 보안 설정에서 진행합니다.
                        </p>
                      ) : null}
                      {selectedLegacyPosition ? (
                        <div className="position-cleanup-guide">
                          <strong>직종과 직위를 나눠 주세요.</strong>
                          <p>
                            현재 `{selectedEmployee.job_name}`이 직종으로 저장되어 있습니다.
                            아래 직종에서 실제 자격·업무를 선택하세요. 아직 확인 전이면
                            `미지정`으로 둘 수 있습니다. 직위는
                            `{selectedLegacyPosition}`으로 확인한 뒤 저장해 주세요.
                            기존 대화와 방 기록은 지워지지 않습니다.
                          </p>
                        </div>
                      ) : null}
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
                      <div className="form-grid">
                        {staffUnitTypes.map(
                          (type) => {
                            const key = `${type}_id` as keyof EmployeeDraft;
                            return (
                              <UnitSelect
                                key={type}
                                type={type}
                                units={units}
                                value={editDraft[key]}
                                onChange={(value) =>
                                  setEditDraft({ ...editDraft, [key]: value })
                                }
                              />
                            );
                          },
                        )}
                        <JobSelect
                          jobs={jobs}
                          value={editDraft.job_code}
                          allowUnassigned
                          onChange={(value) =>
                            setEditDraft({ ...editDraft, job_code: value })
                          }
                        />
                        <PositionTitleInput
                          positions={positionTitles}
                          value={editDraft.position_title}
                          onChange={(value) =>
                            setEditDraft({ ...editDraft, position_title: value })
                          }
                        />
                      </div>
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
                          disabled={selectedEmployee.employment_status !== "active"}
                        />
                        <span>
                          업무함 사용
                          <small>AI 판독·분류·서류 후보 정리 화면에 접근합니다.</small>
                        </span>
                      </label>
                      {selectedEmployee.employment_status === "active" ? (
                        <>
                          <div className="form-actions">
                            <button className="button button-primary" disabled={saving}>
                              배정 변경 저장
                            </button>
                            {selectedEmployee.id !== currentUserId ? (
                              <button
                                className="button button-danger"
                                type="button"
                                disabled={saving}
                                onClick={() => {
                                  if (
                                    window.confirm(
                                      `${selectedEmployee.full_name} 직원을 퇴사 처리하고 현재 접속을 즉시 종료할까요?`,
                                    )
                                  ) {
                                    void run(async () => {
                                      await apiFetch(
                                        `/api/employees/${selectedEmployee.id}/terminate`,
                                        { method: "POST", body: "{}" },
                                      );
                                    }, "퇴사 처리와 세션 차단을 완료했습니다.");
                                  }
                                }}
                              >
                                퇴사 처리
                              </button>
                            ) : null}
                          </div>
                          {selectedEmployee.id !== currentUserId ? (
                            <div className="password-reset-box">
                              <div>
                                <strong>임시 비밀번호 발급</strong>
                                <p>저장 즉시 기존 로그인은 모두 종료됩니다.</p>
                              </div>
                              <input
                                type="password"
                                autoComplete="new-password"
                                value={resetPassword}
                                minLength={12}
                                maxLength={200}
                                placeholder="12자 이상 임시 비밀번호"
                                onChange={(event) => setResetPassword(event.target.value)}
                              />
                              <button
                                className="button button-secondary"
                                type="button"
                                disabled={saving || resetPassword.length < 12}
                                onClick={() => {
                                  if (
                                    window.confirm(
                                      `${selectedEmployee.full_name} 직원의 기존 접속을 종료하고 임시 비밀번호를 발급할까요?`,
                                    )
                                  ) {
                                    void run(async () => {
                                      await apiFetch(
                                        `/api/employees/${selectedEmployee.id}/reset-password`,
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
                        </>
                      ) : (
                        <>
                          <p className="muted-box">
                            {selectedEmployee.employment_status === "leave"
                              ? "휴직 계정은 현재 수정하거나 로그인할 수 없습니다."
                              : "퇴사 처리된 계정은 수정하거나 로그인할 수 없습니다."}
                          </p>
                          {selectedEmployee.employment_status === "retired" &&
                          selectedEmployee.id !== currentUserId ? (
                            <div className="form-actions">
                              <button
                                className="button button-secondary"
                                type="button"
                                disabled={saving}
                                onClick={() => {
                                  if (
                                    window.confirm(
                                      `${selectedEmployee.full_name} 직원을 재직 상태로 복구할까요? 자동 채팅방도 다시 배정됩니다.`,
                                    )
                                  ) {
                                    void run(async () => {
                                      await apiFetch(
                                        `/api/employees/${selectedEmployee.id}/restore`,
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
                                      `${selectedEmployee.full_name} 직원을 목록에서 완전히 삭제할까요?\n\n로그인 아이디와 직원번호는 다시 사용할 수 있습니다. 기존 대화가 있으면 작성자 표시는 기록 보존을 위해 남습니다. 이 작업은 화면에서 복구할 수 없습니다.`,
                                    )
                                  ) {
                                    void run(async () => {
                                      await apiFetch(
                                        `/api/employees/${selectedEmployee.id}`,
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

          {tab === "new-employee" ? (
            <form className="admin-form" onSubmit={addEmployee}>
              <h3>가상 직원 등록</h3>
              <p className="muted-box">
                직종은 사회복지사·간호조무사처럼 실제 업무 종류를, 직위는
                원장·선임사회복지사·간호팀장처럼 기관 안의 역할을 적습니다.
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
                  />
                </label>
                <label>
                  로그인 아이디
                  <input
                    value={employeeDraft.username}
                    onChange={(event) =>
                      setEmployeeDraft({ ...employeeDraft, username: event.target.value })
                    }
                    pattern="[a-zA-Z0-9._-]+"
                    required
                  />
                </label>
                <label>
                  임시 비밀번호
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={employeeDraft.password}
                    onChange={(event) =>
                      setEmployeeDraft({ ...employeeDraft, password: event.target.value })
                    }
                    minLength={12}
                    required
                  />
                  <small className="field-help">
                    직원은 첫 로그인에서 본인 비밀번호로 변경합니다.
                  </small>
                </label>
              </div>
              <div className="form-grid">
                {staffUnitTypes.map(
                  (type) => {
                    const key = `${type}_id` as keyof EmployeeDraft;
                    return (
                      <UnitSelect
                        key={type}
                        type={type}
                        units={units}
                        value={employeeDraft[key]}
                        onChange={(value) =>
                          setEmployeeDraft({ ...employeeDraft, [key]: value })
                        }
                      />
                    );
                  },
                )}
                <JobSelect
                  jobs={jobs}
                  value={employeeDraft.job_code}
                  allowUnassigned
                  onChange={(value) =>
                    setEmployeeDraft({ ...employeeDraft, job_code: value })
                  }
                />
                <PositionTitleInput
                  positions={positionTitles}
                  value={employeeDraft.position_title}
                  onChange={(value) =>
                    setEmployeeDraft({ ...employeeDraft, position_title: value })
                  }
                />
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
                  업무함 사용
                  <small>사회복지사·간호조무사·치료사 등 처리 담당자에게 부여합니다.</small>
                </span>
              </label>
              <button className="button button-primary button-large" disabled={saving}>
                직원 등록 및 채팅방 자동 배정
              </button>
            </form>
          ) : null}

          {tab === "organization" ? (
            <section>
              <div className="archive-visibility">
                <div>
                  <strong>현재 사용하는 정보만 표시 중</strong>
                  <small>
                    중지한 조직·직종·직위는 평소에는 숨겨 화면을 깔끔하게 유지합니다.
                  </small>
                </div>
                <button
                  type="button"
                  className="button button-secondary"
                  onClick={() => setShowInactiveOrganization((current) => !current)}
                >
                  {showInactiveOrganization
                    ? "중지된 항목 숨기기"
                    : `중지된 항목 보기 (${
                      units.filter((unit) => !unit.is_active).length +
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
                <h3>조직정보 추가</h3>
                <p className="field-help">
                  직원은 사업부·부서·팀으로 배정합니다. 어르신 생활층은 채팅에서
                  어르신 명단을 층별로 먼저 보여주기 위한 위치 정보입니다.
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
                      unitType === "floor" ? "예: 3층" : "예: 의료, 야간전담팀"
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
                            unit.unit_type === type &&
                            (unit.is_active || showInactiveOrganization),
                        )
                        .map((unit) => {
                          const isInUse =
                            unit.active_staff_count > 0 || unit.active_room_count > 0;
                          const usageLabel = `직원 ${unit.active_staff_count}명 · 방 ${unit.active_room_count}개`;
                          return (
                            <span
                              className={`managed-chip ${unit.is_active ? "" : "inactive"}`}
                              key={unit.id}
                            >
                              <button
                                type="button"
                                className="chip-label"
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
                                      ? `중지됨 · 과거 연결 ${unit.reference_count}건`
                                      : "중지됨 · 완전 삭제 가능"}
                                </small>
                              </button>
                              <button
                                type="button"
                                className="chip-action"
                                disabled={saving || (unit.is_active && isInUse)}
                                title={
                                  unit.is_active && isInUse
                                    ? `${usageLabel}에서 사용 중이라 중지할 수 없습니다.`
                                    : undefined
                                }
                                onClick={() => {
                                  const nextActive = !unit.is_active;
                                  if (
                                    !window.confirm(
                                      nextActive
                                        ? `${unit.name} 조직정보를 다시 사용하시겠습니까?`
                                        : `${unit.name} 조직정보를 사용중지하시겠습니까?`,
                                    )
                                  ) return;
                                  void run(async () => {
                                    await apiFetch(`/api/org-units/${unit.id}`, {
                                      method: "PATCH",
                                      body: JSON.stringify({ is_active: nextActive }),
                                    });
                                  }, nextActive
                                    ? "조직정보를 복구했습니다."
                                    : "조직정보를 사용중지했습니다.");
                                }}
                              >
                                {unit.is_active ? (isInUse ? "사용 중" : "중지") : "복구"}
                              </button>
                              {!unit.is_active ? (
                                <button
                                  type="button"
                                  className="chip-action chip-delete"
                                  disabled={saving || !unit.can_delete}
                                  title={
                                    unit.can_delete
                                      ? "과거 기록과 연결되지 않은 시험 항목을 완전히 삭제합니다."
                                      : `과거 기록 ${unit.reference_count}건과 연결되어 삭제할 수 없습니다.`
                                  }
                                  onClick={() => {
                                    if (
                                      !window.confirm(
                                        `${unit.name} 조직정보를 완전히 삭제할까요?\n` +
                                          "이 작업은 되돌릴 수 없습니다.",
                                      )
                                    ) return;
                                    void run(async () => {
                                      await apiFetch(`/api/org-units/${unit.id}/purge`, {
                                        method: "DELETE",
                                      });
                                    }, "사용하지 않는 조직정보를 완전히 삭제했습니다.");
                                  }}
                                >
                                  {unit.can_delete ? "완전 삭제" : "기록 보관"}
                                </button>
                              ) : null}
                            </span>
                          );
                        })}
                    </div>
                  </div>
                ))}
              </div>
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
                    const isInUse =
                      job.active_staff_count > 0 || job.active_room_count > 0;
                    const usageLabel = `직원 ${job.active_staff_count}명 · 방 ${job.active_room_count}개`;
                    return (
                      <span
                        className={`managed-chip ${job.is_active ? "" : "inactive"}`}
                        key={job.code}
                      >
                        <button
                          type="button"
                          className="chip-label"
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
                                ? `중지됨 · 과거 연결 ${job.reference_count}건`
                                : "중지됨 · 완전 삭제 가능"}
                          </small>
                        </button>
                        <button
                          type="button"
                          className="chip-action"
                          disabled={saving || (job.is_active && isInUse)}
                          title={
                            job.is_active && isInUse
                              ? `${usageLabel}에서 사용 중이라 중지할 수 없습니다.`
                              : undefined
                          }
                          onClick={() => {
                            const nextActive = !job.is_active;
                            if (
                              !window.confirm(
                                nextActive
                                  ? `${job.name} 직종을 다시 사용하시겠습니까?`
                                  : `${job.name} 직종을 사용중지하시겠습니까?`,
                              )
                            ) return;
                            void run(async () => {
                              await apiFetch(`/api/job-codes/${job.code}`, {
                                method: "PATCH",
                                body: JSON.stringify({ is_active: nextActive }),
                              });
                            }, nextActive
                              ? "직종을 복구했습니다."
                              : "직종을 사용중지했습니다.");
                          }}
                        >
                          {job.is_active ? (isInUse ? "사용 중" : "중지") : "복구"}
                        </button>
                        {!job.is_active ? (
                          <button
                            type="button"
                            className="chip-action chip-delete"
                            disabled={saving || !job.can_delete}
                            title={
                              job.can_delete
                                ? "과거 기록과 연결되지 않은 시험 항목을 완전히 삭제합니다."
                                : `과거 기록 ${job.reference_count}건과 연결되어 삭제할 수 없습니다.`
                            }
                            onClick={() => {
                              if (
                                !window.confirm(
                                  `${job.name} 직종을 완전히 삭제할까요?\n` +
                                    "이 작업은 되돌릴 수 없습니다.",
                                )
                              ) return;
                              void run(async () => {
                                await apiFetch(`/api/job-codes/${job.code}/purge`, {
                                  method: "DELETE",
                                });
                              }, "사용하지 않는 직종정보를 완전히 삭제했습니다.");
                            }}
                          >
                            {job.can_delete ? "완전 삭제" : "기록 보관"}
                          </button>
                        ) : null}
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
                      const isInUse = position.active_staff_count > 0;
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
                                  ? `중지됨 · 과거 연결 ${position.reference_count}건`
                                  : "중지됨 · 완전 삭제 가능"}
                            </small>
                          </button>
                          <button
                            type="button"
                            className="chip-action"
                            disabled={saving || (position.is_active && isInUse)}
                            title={
                              position.is_active && isInUse
                                ? `${usageLabel}이 사용 중이라 중지할 수 없습니다.`
                                : undefined
                            }
                            onClick={() => {
                              const nextActive = !position.is_active;
                              if (
                                !window.confirm(
                                  nextActive
                                    ? `${position.name} 직위를 다시 사용하시겠습니까?`
                                    : `${position.name} 직위를 사용중지하시겠습니까?`,
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
                                ? "직위를 복구했습니다."
                                : "직위를 사용중지했습니다.");
                            }}
                          >
                            {position.is_active
                              ? isInUse
                                ? "사용 중"
                                : "중지"
                              : "복구"}
                          </button>
                          {!position.is_active ? (
                            <button
                              type="button"
                              className="chip-action chip-delete"
                              disabled={saving || !position.can_delete}
                              title={
                                position.can_delete
                                  ? "직원이나 과거 기록과 연결되지 않은 직위를 완전히 삭제합니다."
                                  : `직원·과거 기록 ${position.reference_count}건과 연결되어 삭제할 수 없습니다.`
                              }
                              onClick={() => {
                                if (
                                  !window.confirm(
                                    `${position.name} 직위를 완전히 삭제할까요?\n` +
                                      "이 작업은 되돌릴 수 없습니다.",
                                  )
                                ) return;
                                void run(async () => {
                                  await apiFetch(
                                    `/api/position-titles/${position.id}/purge`,
                                    { method: "DELETE" },
                                  );
                                }, "사용하지 않는 직위를 완전히 삭제했습니다.");
                              }}
                            >
                              {position.can_delete ? "완전 삭제" : "기록 보관"}
                            </button>
                          ) : null}
                        </span>
                      );
                    })}
                </div>
              </div>
            </section>
          ) : null}

          {tab === "custom-room" ? (
            <section className="management-section">
              <div className="admin-management-grid room-management-grid">
                <div className="admin-master-pane">
                  <div className="section-heading">
                    <div>
                      <h3>현재 개설방</h3>
                      <p>방을 선택하면 오른쪽에서 배정규칙과 종료 상태를 관리합니다.</p>
                    </div>
                    <button
                      className="button button-secondary button-nowrap"
                      type="button"
                      onClick={() => {
                        resetRoomDraft();
                        revealDetailOnSmallScreen(roomDetailRef);
                      }}
                    >
                      새 방
                    </button>
                  </div>
                  <div className="management-filters">
                    <input
                      type="search"
                      value={roomQuery}
                      onChange={(event) => setRoomQuery(event.target.value)}
                      placeholder="채팅방·연결 조직 검색"
                      aria-label="채팅방 검색"
                    />
                    <select
                      value={roomKindFilter}
                      onChange={(event) =>
                        setRoomKindFilter(event.target.value as RoomKindFilter)
                      }
                      aria-label="채팅방 배정방식 필터"
                    >
                      <option value="all">전체 배정방식</option>
                      {(Object.keys(roomKindLabels) as RoomKind[]).map((kind) => (
                        <option key={kind} value={kind}>
                          {roomKindLabels[kind]}
                        </option>
                      ))}
                    </select>
                  </div>
                  <label className="check-row compact-check">
                    <input
                      type="checkbox"
                      checked={showInactiveRooms}
                      onChange={(event) => setShowInactiveRooms(event.target.checked)}
                    />
                    <span>
                      종료된 방도 보기
                      <small>
                        현재 {visibleRooms.length}/{managedRooms.length}개 표시
                      </small>
                    </span>
                  </label>
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
                          onClick={() => {
                            setSelectedRoomId(room.id);
                            setRoomName(room.name);
                            setRoomKind(room.kind);
                            setRoomScopeUnitId(room.scope_unit_id ?? "");
                            setRoomJobCode(room.job_code ?? "");
                            setRoomResidentScope(room.resident_scope);
                            setRoomResidentScopeUnitId(
                              room.resident_scope_unit_id ?? "",
                            );
                            setRoomMemberIds(room.member_ids);
                            setRoomMemberQuery("");
                            setError("");
                            setStatusMessage("");
                            revealDetailOnSmallScreen(roomDetailRef);
                          }}
                        >
                          <span>
                            <strong>{room.name}</strong>
                            <small>
                              {room.is_active ? "운영 중" : "종료됨"} · 참여자{" "}
                              {room.member_count}명 · 대화 {room.message_count}건
                            </small>
                          </span>
                          <span className="room-kind-badge">{roomRuleLabel(room)}</span>
                        </button>
                      ))
                    )}
                  </div>
                </div>
                <div className="admin-detail-pane" ref={roomDetailRef}>
                  <form
                className="admin-form custom-room-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  void run(async () => {
                    await apiFetch(
                      selectedRoom
                        ? `/api/admin/rooms/${selectedRoom.id}`
                        : "/api/admin/rooms",
                      {
                        method: selectedRoom ? "PATCH" : "POST",
                        body: JSON.stringify(
                          selectedRoom
                            ? {
                                name: roomName,
                                resident_scope: roomResidentScope,
                                resident_scope_unit_id:
                                  roomResidentScope === "floor"
                                    ? roomResidentScopeUnitId || null
                                    : null,
                                member_ids: roomMemberIds,
                              }
                            : {
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
                              },
                        ),
                      },
                    );
                    resetRoomDraft();
                  }, selectedRoom ? "채팅방 정보를 변경했습니다." : "새 채팅방을 만들었습니다.");
                }}
              >
                <h3>{selectedRoom ? "채팅방 설정" : "새 채팅방 만들기"}</h3>
                <label>
                  채팅방 이름
                  <input
                    value={roomName}
                    onChange={(event) => setRoomName(event.target.value)}
                    placeholder="예: 3층 집중관리 협업방"
                    required
                  />
                </label>
                <label>
                  참여자 배정방식
                  <select
                    value={roomKind}
                    disabled={Boolean(selectedRoom)}
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
                    <option value="all">전체 재직 직원 자동</option>
                    <option value="business">사업부 자동</option>
                    <option value="department">부서 자동</option>
                    {selectedRoom && roomKind === "floor" ? (
                      <option value="floor">과거 층 자동(새로 만들지 않음)</option>
                    ) : null}
                    <option value="team">팀 자동</option>
                    <option value="job">직종 자동</option>
                    <option value="custom">직원 직접 선택</option>
                  </select>
                </label>
                {["business", "department", "floor", "team"].includes(roomKind) ? (
                  <label>
                    연결 조직정보
                    <select
                      value={roomScopeUnitId}
                      disabled={Boolean(selectedRoom)}
                      onChange={(event) => setRoomScopeUnitId(event.target.value)}
                    >
                      <option value="">선택</option>
                      {units
                        .filter((unit) => unit.unit_type === roomKind && unit.is_active)
                        .map((unit) => (
                          <option key={unit.id} value={unit.id}>
                            {unit.name}
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
                      disabled={Boolean(selectedRoom)}
                      onChange={(event) => setRoomJobCode(event.target.value)}
                    >
                      <option value="">선택</option>
                      {jobs.filter((job) => job.is_active).map((job) => (
                        <option key={job.code} value={job.code}>
                          {job.name}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : null}
                {roomKind === "custom" || Boolean(selectedRoom) ? (
                  <>
                    <label>
                      참여 직원 찾기
                      <input
                        type="search"
                        value={roomMemberQuery}
                        onChange={(event) => setRoomMemberQuery(event.target.value)}
                    placeholder="이름, 아이디, 팀, 직종 또는 직위"
                      />
                    </label>
                    <fieldset className="member-picker">
                      <legend>참여 직원 · 선택 {roomMemberIds.length}명</legend>
                      {roomCandidates.length === 0 ? (
                        <p className="member-picker-empty">검색된 직원이 없습니다.</p>
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
                  </>
                ) : (
                  <p className="muted-box">
                    방을 만든 뒤 구성원 목록에서 자동 참여자와 추가 참여자를 조정할 수 있습니다.
                  </p>
                )}
                <label>
                  어르신 명단 우선범위
                  <select
                    value={roomKind === "floor" ? "floor" : roomResidentScope}
                    disabled={roomKind === "floor"}
                    onChange={(event) => {
                      const nextScope = event.target.value as ResidentScope;
                      setRoomResidentScope(nextScope);
                      if (nextScope !== "floor") setRoomResidentScopeUnitId("");
                    }}
                  >
                    <option value="all">전체 명단</option>
                    <option value="facility">시설 어르신 우선</option>
                    <option value="daycare">주간보호 어르신 우선</option>
                    <option value="homecare">방문요양 어르신 우선</option>
                    <option value="floor">연결된 층 어르신 우선</option>
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
                            {unit.name}
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
                    roomName.trim().length < 2 ||
                    (!selectedRoom && roomKind === "custom" && roomMemberIds.length === 0) ||
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
                              `"${selectedRoom.name}" 방을 종료할까요?\n참여자 화면에서 즉시 사라집니다.`,
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
                    ) : (
                      <button
                        type="button"
                        className="button button-primary"
                        disabled={saving}
                        onClick={() => {
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
                ) : null}
                  </form>
                </div>
              </div>
            </section>
          ) : null}

          {tab === "residents" ? (
            <section className="resident-order-admin" ref={residentListRef}>
              <div className="section-heading">
                <div>
                  <h3>현재 어르신 명단</h3>
                  <p>
                    채팅에서 선택할 어르신을 확인하고, 새로 추가하거나 이용 종료할 수
                    있습니다.
                  </p>
                </div>
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
                  명단 갱신으로 이동
                </button>
              </div>

              <div className="resident-service-summary" aria-label="현재 어르신 수">
                {(["facility", "daycare", "homecare"] as const).map((serviceType) => {
                  const serviceResidents = residents.filter(
                    (resident) => resident.service_type === serviceType,
                  );
                  const careforCount = serviceResidents.filter(
                    (resident) => resident.roster_source === "carefor",
                  ).length;
                  return (
                    <div key={serviceType}>
                      <strong>{careforCount}명</strong>
                      <span>{residentServiceLabels[serviceType]} 케어포 확인</span>
                    </div>
                  );
                })}
              </div>

              <form className="resident-add-form" onSubmit={addResident}>
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
                    생활 층
                    <select
                      value={residentDraft.floor_id}
                      onChange={(event) =>
                        setResidentDraft((current) => ({
                          ...current,
                          floor_id: event.target.value,
                        }))
                      }
                      required
                    >
                      <option value="">층을 선택하세요</option>
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
                    (residentDraft.service_type !== "homecare" &&
                      !residentDraft.floor_id)
                  }
                >
                  명단에 추가
                </button>
              </form>

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
                const careforResidents = serviceResidents.filter(
                  (resident) => resident.roster_source === "carefor",
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
                      <small>{resident.floor?.name ?? label}</small>
                    </span>
                    <div>
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
                      {label} 케어포 확인 명단 <span>{careforResidents.length}명</span>
                    </h4>
                    {careforResidents.length === 0 ? (
                      <p className="empty-note">
                        {label} 케어포 명단을 아직 확인하지 못했습니다.
                      </p>
                    ) : (
                      careforResidents.map((resident, index) =>
                        residentRow(resident, index, careforResidents),
                      )
                    )}
                  </div>
                );
              })}
            </section>
          ) : null}

          {tab === "residents" ? (
            <section className="resident-sync-admin" ref={residentSyncRef}>
              <div className="section-heading">
                <div>
                  <h3>케어포 명단 갱신</h3>
                  <p>
                    케어포에서 읽기 전용으로 확인한 명단을 가명으로 바꾼 뒤,
                    관리자가 선택한 변경만 채팅방에 반영합니다.
                  </p>
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
                  현재 명단으로 이동
                </button>
              </div>

              <div className="carefor-roster-guide">
                <strong>사용 방법</strong>
                <span>
                  아래에서 시설·주간보호·방문요양 중 준비된 명단의 “최신 명단
                  확인”을 누른 뒤, 바뀐 사람만 체크해 저장합니다.
                </span>
              </div>

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
                            어르신 <b>{source.resident_count}명</b> · 직원{" "}
                            <b>{source.staff_count}명</b>
                          </p>
                          <small>
                            실명은 내부 매칭표에만 보관하고 화면에는 가명만 표시합니다.
                          </small>
                          <button
                            type="button"
                            className="button button-primary"
                            disabled={saving}
                            onClick={() => void previewCareforRoster(serviceType)}
                          >
                            최신 명단 확인
                          </button>
                        </>
                      ) : (
                        <>
                          <p>이 서비스의 케어포 화면에 먼저 로그인해야 합니다.</p>
                          <small>로그인 전에는 기존 명단이 바뀌지 않습니다.</small>
                          <button type="button" className="button button-secondary" disabled>
                            아직 준비 안 됨
                          </button>
                        </>
                      )}
                    </article>
                  );
                })}
              </div>

              {careforRosterStatus &&
              Object.values(careforRosterStatus.sources).some(
                (source) => source.staff_aliases.length > 0,
              ) ? (
                <details className="carefor-staff-aliases">
                  <summary>
                    확보한 가명 직원 명단 확인 (
                    {Object.values(careforRosterStatus.sources).reduce(
                      (sum, source) => sum + source.staff_aliases.length,
                      0,
                    )}
                    명)
                  </summary>
                  <div>
                    <p>
                      직원 실명은 표시하지 않습니다. 이 명단은 케어포 직원과 채팅
                      계정을 나중에 연결하기 위한 내부 가명 기준표입니다.
                    </p>
                    {(["facility", "daycare", "homecare"] as const).map(
                      (serviceType) => {
                        const aliases =
                          careforRosterStatus.sources[serviceType].staff_aliases;
                        if (aliases.length === 0) return null;
                        return (
                          <section key={serviceType}>
                            <h4>
                              {residentServiceLabels[serviceType]} 직원{" "}
                              {aliases.length}명
                            </h4>
                            <div>
                              {aliases.map((staff) => (
                                <span key={staff.display_name}>
                                  <strong>{staff.display_name}</strong>
                                  <small>
                                    {staff.job_name} · {staff.status}
                                  </small>
                                </span>
                              ))}
                            </div>
                          </section>
                        );
                      },
                    )}
                  </div>
                </details>
              ) : null}

              {residentSyncBatches.length > 0 ? (
                <label className="resident-sync-history">
                  지난 작업 보기(필요할 때만)
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
                    <option value="">지난 작업을 선택하세요</option>
                    {residentSyncBatches.map((batch) => (
                      <option value={batch.id} key={batch.id}>
                        {new Date(batch.created_at).toLocaleString("ko-KR")} ·{" "}
                        {batch.original_name} · {residentSyncStatusLabels[batch.status]}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}

              {residentSyncLoading ? (
                <p className="empty-note">동기화 기록을 불러오는 중입니다.</p>
              ) : null}

              {residentSyncBatch ? (
                <div className="resident-sync-preview">
                  <div className="resident-sync-meta">
                    <div>
                      <strong>{residentSyncBatch.original_name}</strong>
                      <span>
                        {residentSyncStatusLabels[residentSyncBatch.status]} ·{" "}
                        {new Date(residentSyncBatch.created_at).toLocaleString("ko-KR")}
                      </span>
                    </div>
                    <span className={`resident-sync-batch-status ${residentSyncBatch.status}`}>
                      {residentSyncStatusLabels[residentSyncBatch.status]}
                    </span>
                  </div>

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
                          <label className="checkbox-row">
                            <input
                              type="checkbox"
                              checked={showUnchangedSyncItems}
                              onChange={(event) =>
                                setShowUnchangedSyncItems(event.target.checked)
                              }
                            />
                            바뀌지 않은 사람도 보기
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

                      {residentSyncBatch.status === "applied" ? (
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
                  지금 확인 중인 변경사항이 없습니다. 위에서 준비된 서비스의 “최신
                  명단 확인”을 눌러 주세요.
                </p>
              )}
            </section>
          ) : null}
        </div>
      </aside>
    </div>
  );
}
