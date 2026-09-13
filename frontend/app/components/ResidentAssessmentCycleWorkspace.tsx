"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "../api";
import type {
  CarePlanningDocumentType,
  Resident,
  ResidentAssessmentClassification,
  ResidentAssessmentCycle,
  ResidentAssessmentCycleList,
  ResidentAssessmentReason,
} from "../types";


const reasonLabels: Record<ResidentAssessmentReason, string> = {
  new_admission: "신규입소",
  periodic_reassessment: "정기 재평가",
  state_change: "상태변경",
  care_plan_change: "급여제공계획 변경",
  staff_review: "직원 직접 재검토",
};

const reasonOptions: ResidentAssessmentReason[] = [
  "new_admission",
  "periodic_reassessment",
  "state_change",
  "staff_review",
];

const documentLabels: Record<CarePlanningDocumentType, string> = {
  cognitive_function_assessment: "인지기능검사",
  fall_risk_assessment: "낙상위험도",
  pressure_ulcer_risk_assessment: "욕창위험도",
  needs_assessment: "욕구사정",
  long_term_care_service_plan: "장기요양급여 제공계획서",
};

const classificationLabels: Record<ResidentAssessmentClassification, string> = {
  unchanged: "이전과 동일",
  changed: "변경됨",
  newly_confirmed: "새로 확인됨",
  stale_current_observation_required: "현재 관찰 필요",
  material_conflict: "자료 충돌",
  basis_version_mismatch: "기준·서식 불일치",
  expert_review_required: "전문가 확인 필요",
  care_plan_change_candidate: "급여계획 변경 검토 후보",
};

type ManualFact = {
  label: string;
  value: string;
  evidence: string;
};

type CurrentFact = ManualFact & {
  linkedBaselineIndex: number | null;
  state: "confirmed" | "current_observation_required" | "material_conflict" | "expert_review_required";
  conflictValues: string;
  planImpact: string;
};

function localIsoDate() {
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60_000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 10);
}

function splitEvidence(value: string) {
  return Array.from(
    new Set(value.split(/[\n,]/).map((item) => item.trim()).filter(Boolean)),
  );
}

function displayValue(value: unknown) {
  if (value === null || value === undefined || value === "") return "확인 필요";
  return String(value);
}

export function ResidentAssessmentCycleWorkspace({ resident }: { resident: Resident }) {
  const [today] = useState(() => localIsoDate());
  const [cycleList, setCycleList] = useState<ResidentAssessmentCycleList | null>(null);
  const [activeCycle, setActiveCycle] = useState<ResidentAssessmentCycle | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [reason, setReason] = useState<ResidentAssessmentReason>("periodic_reassessment");
  const [assessmentDate, setAssessmentDate] = useState(today);
  const [periodStart, setPeriodStart] = useState("");
  const [periodEnd, setPeriodEnd] = useState(today);
  const [documentType, setDocumentType] = useState<CarePlanningDocumentType>("needs_assessment");
  const [sourceLabel, setSourceLabel] = useState("직원이 원본을 확인한 기존 기준자료");
  const [organizationRole, setOrganizationRole] = useState<"authoring_organization" | "reference_organization">("authoring_organization");
  const [originalVerified, setOriginalVerified] = useState(true);
  const [reconfirmationRequired, setReconfirmationRequired] = useState(true);
  const [formVersion, setFormVersion] = useState("");
  const [previousCycleId, setPreviousCycleId] = useState("");
  const [baselineFacts, setBaselineFacts] = useState<ManualFact[]>([
    { label: "", value: "", evidence: "" },
  ]);
  const [currentFacts, setCurrentFacts] = useState<CurrentFact[]>([
    {
      label: "",
      value: "",
      evidence: "",
      linkedBaselineIndex: 0,
      state: "confirmed",
      conflictValues: "",
      planImpact: "",
    },
  ]);

  async function loadCycle(cycleId: string) {
    const payload = await apiFetch<ResidentAssessmentCycle>(
      `/api/workdesk/residents/${resident.id}/assessment-cycles/${cycleId}`,
    );
    setActiveCycle(payload);
  }

  useEffect(() => {
    let disposed = false;
    apiFetch<ResidentAssessmentCycleList>(
      `/api/workdesk/residents/${resident.id}/assessment-cycles`,
    )
      .then(async (payload) => {
        if (disposed) return;
        setCycleList(payload);
        const latest = payload.items[0];
        setPreviousCycleId(latest?.id ?? "");
        if (latest) {
          const detail = await apiFetch<ResidentAssessmentCycle>(
            `/api/workdesk/residents/${resident.id}/assessment-cycles/${latest.id}`,
          );
          if (!disposed) setActiveCycle(detail);
        } else {
          setActiveCycle(null);
        }
      })
      .catch((reasonValue) => {
        if (!disposed) {
          setError(
            reasonValue instanceof Error
              ? reasonValue.message
              : "평가 회차를 불러오지 못했습니다.",
          );
        }
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });
    return () => {
      disposed = true;
    };
  }, [resident.id]);

  async function createCycle() {
    const usableBaseline = baselineFacts.filter((fact) => fact.label.trim() && fact.value.trim());
    const usableCurrent = currentFacts.filter((fact) => fact.label.trim() || fact.linkedBaselineIndex !== null);
    if (!assessmentDate || !sourceLabel.trim() || !usableBaseline.length) {
      setError("기준일·기존 자료 이름·기존 핵심 항목을 입력해 주세요.");
      return;
    }
    if (!usableCurrent.length) {
      setError("현재 매실챗 기록 또는 직원 확인 항목을 한 건 이상 입력해 주세요.");
      return;
    }
    setSaving(true);
    setError("");
    setStatus("");
    try {
      const stamp = Date.now();
      const baselinePayload = usableBaseline.map((fact, index) => ({
        field_key: `manual_${index + 1}`,
        label: fact.label.trim(),
        value_kind: "fact",
        value: fact.value.trim(),
        evidence_refs: splitEvidence(fact.evidence || `직원 직접입력 ${assessmentDate}`),
        staff_confirmed: originalVerified,
      }));
      const currentPayload = usableCurrent.map((fact, index) => {
        const linked = fact.linkedBaselineIndex === null
          ? null
          : baselinePayload[fact.linkedBaselineIndex];
        const unresolved = fact.state !== "confirmed";
        return {
          field_key: linked?.field_key ?? `current_${index + 1}`,
          label: (fact.label || linked?.label || `현재 확인 항목 ${index + 1}`).trim(),
          value_kind: "fact",
          value: unresolved ? null : fact.value.trim(),
          state: fact.state,
          source_type: fact.state === "confirmed" ? "staff_confirmation" : "current_observation",
          observed_at: fact.state === "current_observation_required" ? null : assessmentDate,
          evidence_refs: splitEvidence(fact.evidence || `직원 현재 확인 ${assessmentDate}`),
          conflict_values: fact.state === "material_conflict"
            ? fact.conflictValues.split(/[\/,]/).map((item) => item.trim()).filter(Boolean)
            : [],
          basis_version: null,
          plan_impact: fact.planImpact.trim() || null,
          staff_confirmed: fact.state === "confirmed",
        };
      });
      const created = await apiFetch<ResidentAssessmentCycle>(
        `/api/workdesk/residents/${resident.id}/assessment-cycles`,
        {
          method: "POST",
          body: JSON.stringify({
            payload: {
              schema_version: "resident_assessment_cycle_v1",
              revision: 1,
              supersedes_revision: null,
              reason,
              assessment_date: assessmentDate,
              period_start: periodStart || null,
              period_end: periodEnd || null,
              source_provider: "staff_manual",
              previous_cycle_id: previousCycleId || null,
              output_status: "needs_confirmation",
              external_transfer_allowed: false,
              baseline_sources: [
                {
                  source_ref: `staff-manual-${stamp}`,
                  document_type: documentType,
                  assessment_date: assessmentDate,
                  period_start: periodStart || null,
                  period_end: periodEnd || null,
                  source_label: sourceLabel.trim(),
                  organization_role: organizationRole,
                  original_verified_by_staff: originalVerified,
                  current_reconfirmation_required: reconfirmationRequired,
                  form_version: formVersion.trim() || null,
                  facts: baselinePayload,
                },
              ],
              current_facts: currentPayload,
              selected_plan_candidate_keys: [],
            },
          }),
        },
      );
      setActiveCycle(created);
      setPreviousCycleId(created.id);
      const refreshed = await apiFetch<ResidentAssessmentCycleList>(
        `/api/workdesk/residents/${resident.id}/assessment-cycles`,
      );
      setCycleList(refreshed);
      setStatus(`평가 회차 ${created.cycle_number}을 편집용 초안으로 저장했습니다.`);
    } catch (reasonValue) {
      setError(
        reasonValue instanceof Error
          ? reasonValue.message
          : "평가 회차를 저장하지 못했습니다.",
      );
    } finally {
      setSaving(false);
    }
  }

  async function togglePlanCandidate(fieldKey: string) {
    if (!activeCycle || saving) return;
    const latest = activeCycle.revisions.at(-1);
    if (!latest) return;
    const selected = new Set(latest.selected_plan_candidate_keys);
    if (selected.has(fieldKey)) selected.delete(fieldKey);
    else selected.add(fieldKey);
    setSaving(true);
    setError("");
    setStatus("");
    try {
      const updated = await apiFetch<ResidentAssessmentCycle>(
        `/api/workdesk/residents/${resident.id}/assessment-cycles/${activeCycle.id}/revisions`,
        {
          method: "POST",
          body: JSON.stringify({
            payload: {
              schema_version: "resident_assessment_cycle_v1",
              revision: activeCycle.current_revision + 1,
              supersedes_revision: activeCycle.current_revision,
              reason: activeCycle.reason,
              assessment_date: activeCycle.assessment_date,
              period_start: activeCycle.period_start,
              period_end: activeCycle.period_end,
              source_provider: "staff_manual",
              previous_cycle_id: activeCycle.previous_cycle_id,
              output_status: "needs_confirmation",
              external_transfer_allowed: false,
              baseline_sources: latest.baseline_sources,
              current_facts: latest.current_facts,
              selected_plan_candidate_keys: Array.from(selected),
            },
          }),
        },
      );
      setActiveCycle(updated);
      setCycleList((current) => current ? {
        ...current,
        items: current.items.map((item) => item.id === updated.id ? {
          ...item,
          current_revision: updated.current_revision,
          status: updated.status,
          updated_at: updated.updated_at,
        } : item),
      } : current);
      setStatus("선택 변경을 새 수정 이력으로 보존했습니다.");
    } catch (reasonValue) {
      setError(
        reasonValue instanceof Error
          ? reasonValue.message
          : "변경 후보 선택을 저장하지 못했습니다.",
      );
    } finally {
      setSaving(false);
    }
  }

  const latest = activeCycle?.revisions.at(-1) ?? null;

  return (
    <section className="assessment-cycle-workspace" aria-label="모든 어르신 공통 평가 회차">
      <header className="assessment-cycle-heading">
        <div>
          <h4>기초사정·급여계획 작성 도우미</h4>
          <p>선택한 어르신 · <strong>{resident.display_name}</strong></p>
        </div>
        <span>{activeCycle ? `평가 회차 ${activeCycle.cycle_number}` : "첫 평가 회차 준비"}</span>
      </header>
      <div className="assessment-cycle-notice">
        <p>신규입소와 기존 어르신의 정기 재평가·상태변경·직원 재검토를 같은 흐름으로 관리합니다.</p>
        <p>{cycleList?.source_provider_notice ?? "기존 자료는 직원이 원본을 확인해 직접 입력합니다. Carefor 읽기 전용 공급자는 별도 승인 전 연결하지 않습니다."}</p>
        <p>{cycleList?.operating_cycle_notice ?? "6개월 기본 주기는 센터 운영 원칙입니다."}</p>
      </div>

      <details className="assessment-cycle-create">
        <summary>새 평가 회차 만들기</summary>
        <div className="assessment-cycle-form-grid">
          <label>작성 사유
            <select value={reason} onChange={(event) => setReason(event.target.value as ResidentAssessmentReason)}>
              {reasonOptions.map((value) => <option key={value} value={value}>{reasonLabels[value]}</option>)}
            </select>
          </label>
          <label>평가 기준일<input type="date" value={assessmentDate} onChange={(event) => setAssessmentDate(event.target.value)} /></label>
          <label>적용 시작일<input type="date" value={periodStart} onChange={(event) => setPeriodStart(event.target.value)} /></label>
          <label>적용 종료일<input type="date" value={periodEnd} onChange={(event) => setPeriodEnd(event.target.value)} /></label>
          <label>이전 평가 회차
            <select value={previousCycleId} onChange={(event) => setPreviousCycleId(event.target.value)}>
              <option value="">연결하지 않음</option>
              {cycleList?.items.map((item) => <option key={item.id} value={item.id}>회차 {item.cycle_number} · {item.assessment_date}</option>)}
            </select>
          </label>
          <label>기존 문서 종류
            <select value={documentType} onChange={(event) => setDocumentType(event.target.value as CarePlanningDocumentType)}>
              {Object.entries(documentLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <label>기존 자료 이름<input value={sourceLabel} onChange={(event) => setSourceLabel(event.target.value)} /></label>
          <label>자료 구분
            <select value={organizationRole} onChange={(event) => setOrganizationRole(event.target.value as typeof organizationRole)}>
              <option value="authoring_organization">우리 기관 작성자료</option>
              <option value="reference_organization">다른 기관 참고자료</option>
            </select>
          </label>
          <label>서식·기준 버전<input value={formVersion} onChange={(event) => setFormVersion(event.target.value)} placeholder="모르면 비워 두세요" /></label>
        </div>
        <div className="assessment-cycle-checks">
          <label><input type="checkbox" checked={originalVerified} onChange={(event) => setOriginalVerified(event.target.checked)} />직원이 원본을 확인함</label>
          <label><input type="checkbox" checked={reconfirmationRequired} onChange={(event) => setReconfirmationRequired(event.target.checked)} />현재 상태 재확인 필요</label>
        </div>

        <section className="assessment-manual-items">
          <h5>기존 기준자료의 확인된 핵심 항목</h5>
          {baselineFacts.map((fact, index) => (
            <div className="assessment-manual-row" key={`baseline-${index}`}>
              <input aria-label={`기존 항목 ${index + 1} 이름`} value={fact.label} onChange={(event) => setBaselineFacts((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, label: event.target.value } : item))} placeholder="예: 이동 상태" />
              <input aria-label={`기존 항목 ${index + 1} 값`} value={fact.value} onChange={(event) => setBaselineFacts((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, value: event.target.value } : item))} placeholder="예: 실내 독립보행" />
              <input aria-label={`기존 항목 ${index + 1} 근거`} value={fact.evidence} onChange={(event) => setBaselineFacts((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, evidence: event.target.value } : item))} placeholder="원본 쪽·항목 위치" />
            </div>
          ))}
          <button type="button" onClick={() => setBaselineFacts((current) => [...current, { label: "", value: "", evidence: "" }])}>기존 항목 추가</button>
        </section>

        <section className="assessment-manual-items">
          <h5>현재 매실챗 기록·직원 확인 내용</h5>
          {currentFacts.map((fact, index) => (
            <div className="assessment-current-row" key={`current-${index}`}>
              <select aria-label={`현재 항목 ${index + 1} 연결`} value={fact.linkedBaselineIndex ?? -1} onChange={(event) => setCurrentFacts((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, linkedBaselineIndex: Number(event.target.value) < 0 ? null : Number(event.target.value) } : item))}>
                <option value={-1}>새 항목</option>
                {baselineFacts.map((item, itemIndex) => <option key={itemIndex} value={itemIndex}>{item.label || `기존 항목 ${itemIndex + 1}`}</option>)}
              </select>
              <input aria-label={`현재 항목 ${index + 1} 이름`} value={fact.label} onChange={(event) => setCurrentFacts((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, label: event.target.value } : item))} placeholder="새 항목일 때 이름" />
              <select aria-label={`현재 항목 ${index + 1} 상태`} value={fact.state} onChange={(event) => setCurrentFacts((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, state: event.target.value as CurrentFact["state"] } : item))}>
                <option value="confirmed">직원이 현재 확인함</option>
                <option value="current_observation_required">현재 관찰 필요</option>
                <option value="material_conflict">자료 충돌</option>
                <option value="expert_review_required">전문가 확인 필요</option>
              </select>
              <input aria-label={`현재 항목 ${index + 1} 값`} value={fact.value} disabled={fact.state !== "confirmed"} onChange={(event) => setCurrentFacts((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, value: event.target.value } : item))} placeholder={fact.state === "confirmed" ? "현재 확인 값" : "확인 전에는 입력하지 않음"} />
              <input aria-label={`현재 항목 ${index + 1} 근거`} value={fact.evidence} onChange={(event) => setCurrentFacts((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, evidence: event.target.value } : item))} placeholder="대화 날짜·기록 위치" />
              {fact.state === "material_conflict" ? <input aria-label={`현재 항목 ${index + 1} 충돌값`} value={fact.conflictValues} onChange={(event) => setCurrentFacts((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, conflictValues: event.target.value } : item))} placeholder="예: 120/70, 170/90" /> : null}
              <input aria-label={`현재 항목 ${index + 1} 급여계획 영향`} value={fact.planImpact} onChange={(event) => setCurrentFacts((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, planImpact: event.target.value } : item))} placeholder="확인된 변화가 있을 때만 검토 문구" />
            </div>
          ))}
          <button type="button" onClick={() => setCurrentFacts((current) => [...current, { label: "", value: "", evidence: "", linkedBaselineIndex: null, state: "confirmed", conflictValues: "", planImpact: "" }])}>현재 항목 추가</button>
        </section>
        <p className="assessment-cycle-safety">과거 자료는 현재 사실로 자동 확정하지 않습니다. 점수·등급·진단·급여 종류·횟수·시간·목표·서명은 이 입력 화면에서 만들지 않습니다.</p>
        <button type="button" className="button-primary" disabled={saving} onClick={() => void createCycle()}>{saving ? "저장 중…" : "편집용 평가 회차 만들기"}</button>
      </details>

      {loading ? <p className="muted-box">평가 회차를 불러오는 중입니다…</p> : null}
      {cycleList?.items.length ? (
        <nav className="assessment-cycle-history" aria-label="평가 회차 선택">
          {cycleList.items.map((item) => (
            <button key={item.id} type="button" aria-pressed={activeCycle?.id === item.id} onClick={() => void loadCycle(item.id)}>
              회차 {item.cycle_number} · {reasonLabels[item.reason]} · {item.assessment_date} · 수정 이력 {item.current_revision}
            </button>
          ))}
        </nav>
      ) : !loading ? <p className="muted-box">저장된 평가 회차가 없습니다. 기존 자료를 직원이 확인한 뒤 첫 회차를 만들어 주세요.</p> : null}

      {activeCycle && latest ? (
        <article className="assessment-cycle-review" aria-live="polite">
          <header>
            <div>
              <h5>{reasonLabels[activeCycle.reason]} · 평가 회차 {activeCycle.cycle_number}</h5>
              <p>기준일 {activeCycle.assessment_date} · 이전 회차 {activeCycle.previous_cycle_id ? "연결됨" : "없음"}</p>
            </div>
            <span>{activeCycle.status === "needs_confirmation" ? "직원 확인 필요" : "편집용 초안"}</span>
          </header>
          <div className="assessment-cycle-columns">
            <section>
              <h6>이전 자료에서 재사용 가능한 사실</h6>
              <ul>{latest.baseline_sources.flatMap((source) => source.facts.filter((fact) => fact.staff_confirmed).map((fact) => <li key={`${source.source_ref}-${fact.field_key}`}><strong>{fact.label}</strong> · {displayValue(fact.value)}<small>{source.source_label} · {source.organization_role === "authoring_organization" ? "우리 기관 작성자료" : "다른 기관 참고자료"}</small></li>))}</ul>
            </section>
            <section>
              <h6>현재 매실챗 기록·직원 확인</h6>
              <ul>{latest.current_facts.map((fact) => <li key={`${fact.field_key}-${fact.state}`}><strong>{fact.label}</strong> · {fact.state === "confirmed" ? displayValue(fact.value) : "확인 필요"}<small>{fact.source_type === "mesil_chat_confirmed" ? "매실챗 확인 기록" : fact.source_type === "staff_confirmation" ? "직원 현재 확인" : "현재 관찰 필요"}</small></li>)}</ul>
            </section>
          </div>
          <section className="assessment-comparison-list">
            <h6>이전과 현재의 차이</h6>
            {latest.comparison_items.map((item) => (
              <article key={item.field_key} className={`assessment-comparison-item is-${item.classification}`}>
                <header><strong>{item.label}</strong><span>{classificationLabels[item.classification]}</span></header>
                <p>이전: {displayValue(item.previous_value)} · 현재: {displayValue(item.current_value)}</p>
                <p>{item.reason}</p>
                {item.conflict_values.length ? <p>충돌 자료: {item.conflict_values.join(" / ")} · 자동 선택하지 않음</p> : null}
                {item.plan_impact ? <p>급여계획 영향 후보: {item.plan_impact}</p> : null}
                {item.staff_selection_allowed ? (
                  <div className="assessment-plan-review-control">
                    <label><input type="checkbox" checked={item.selected_for_plan_review} disabled={saving} onChange={() => void togglePlanCandidate(item.field_key)} /><span>급여제공계획 검토 목록에 담기</span></label>
                    <small>아직 공식 계획에는 반영되지 않았습니다.</small>
                  </div>
                ) : null}
                <details><summary>근거와 출처 보기 · {item.evidence_refs.length}건</summary><ul>{item.evidence_refs.map((ref) => <li key={ref}>{ref}</li>)}</ul></details>
              </article>
            ))}
          </section>
          <section className="assessment-confirmation-questions">
            <h6>현재 다시 확인할 항목</h6>
            {latest.confirmation_questions.length ? <ul>{latest.confirmation_questions.map((question) => <li key={question.field_key}><strong>{classificationLabels[question.classification]}</strong> · {question.prompt}</li>)}</ul> : <p>추가 확인 질문이 없습니다.</p>}
          </section>
          <details className="assessment-revision-history">
            <summary>근거와 수정 이력 보기 · {activeCycle.revisions.length}개 수정본</summary>
            <ol>{activeCycle.revisions.map((revision) => <li key={revision.id}>수정본 {revision.revision} · {revision.created_by_name} · 변경 항목 {revision.field_changes.length}건 · {new Date(revision.created_at).toLocaleString("ko-KR")}</li>)}</ol>
          </details>
          <footer>
            <p>공식 저장 전 편집용 초안입니다. 변화와 후보는 직원이 선택·확인하기 전 자동 반영하지 않습니다.</p>
            <div aria-label="직원 확인 전 잠긴 공식 동작">
              <button type="button" disabled>공식 기록 저장</button>
              <button type="button" disabled>서명 확정</button>
              <button type="button" disabled>공단 전송</button>
            </div>
          </footer>
        </article>
      ) : null}
      {status ? <p className="form-success" role="status">{status}</p> : null}
      {error ? <p className="form-error" role="alert">{error}</p> : null}
    </section>
  );
}
