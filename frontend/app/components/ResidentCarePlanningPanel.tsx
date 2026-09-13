"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "../api";
import { AssessmentDraftIntakeWorkspace } from "./AssessmentDraftIntakeWorkspace";
import type {
  AssessmentFormReviewState,
  AssessmentEvidenceSummary,
  CarePlanningDocumentType,
  NewAdmissionCarePlanPendingItem,
  NewAdmissionDraftList,
  NewAdmissionDraftRecord,
  NewAdmissionFormWorkspace,
  NewAdmissionQuestionFlow,
  Resident,
  ResidentCarePlanningProfile,
  ResidentCarePlanningState,
} from "../types";

const assessmentDocumentTypes = [
  "fall_risk_assessment",
  "pressure_ulcer_risk_assessment",
  "cognitive_function_assessment",
  "needs_assessment",
] as const satisfies readonly CarePlanningDocumentType[];

const formReviewStateLabels: Record<AssessmentFormReviewState, string> = {
  "": "검토 상태 선택",
  confirmed: "확인된 값",
  not_applicable: "해당 없음",
  not_confirmed: "현재 확인하지 못함",
  follow_up: "추후 보완",
};

const carePlanCellStateLabels: Record<string, string> = {
  evidence_found: "기초사정에서 반영",
  user_decision_required: "추천 초안 · 직원 확인",
  staff_edited: "직원 수정",
};

const documentLabels: Record<CarePlanningDocumentType, string> = {
  cognitive_function_assessment: "인지기능검사",
  fall_risk_assessment: "낙상위험도",
  pressure_ulcer_risk_assessment: "욕창위험도",
  needs_assessment: "욕구사정",
  long_term_care_service_plan: "장기요양급여 제공계획서",
};

const assessmentReasonLabels: Record<
  NewAdmissionDraftRecord["revisions"][number]["bundle"]["reason"],
  string
> = {
  new_admission: "신규입소",
  periodic_reassessment: "정기 재평가",
  state_change_reassessment: "상태변화",
  staff_review: "직원 재검토",
};

const statusLabels: Record<ResidentCarePlanningState["status"], string> = {
  not_started: "미작성",
  draft: "작성 중",
  needs_review: "직원 확인 필요",
  confirmed: "직원 확인 완료",
};

type PlanningWorkflowStage = 1 | 2 | 3 | 4 | 5;

const planningWorkflowStages: Array<{
  stage: PlanningWorkflowStage;
  label: string;
}> = [
  { stage: 1, label: "1. 자료 제출" },
  { stage: 2, label: "2. 근거·변화 확인" },
  { stage: 3, label: "3. 기초사정 검토" },
  { stage: 4, label: "4. 급여계획 검토" },
  { stage: 5, label: "5. 저장·출력" },
];

const comparisonLabels: Record<
  NewAdmissionDraftRecord["revisions"][number]["bundle"]["comparison_items"][number]["classification"],
  string
> = {
  unchanged: "이전과 동일",
  changed: "변경됨",
  newly_confirmed: "새로 확인됨",
  stale_current_observation_required: "현재 관찰 필요",
  material_conflict: "자료 충돌",
  basis_version_mismatch: "기준 불일치",
  expert_review_required: "전문가 확인 필요",
  care_plan_change_candidate: "급여계획 변경 검토 후보",
};

export function ChangeComparisonPanel({
  draftRecord,
  residentId,
  onSaved,
  relevantTopics,
}: {
  draftRecord: NewAdmissionDraftRecord;
  residentId: string;
  onSaved: (record: NewAdmissionDraftRecord) => void;
  relevantTopics?: string[];
}) {
  const latestRevision = draftRecord.revisions.find(
    (item) => item.revision === draftRecord.current_revision,
  );
  const [showAll, setShowAll] = useState(false);
  const [selectedFieldKeys, setSelectedFieldKeys] = useState<string[]>(
    () => latestRevision?.bundle.selected_change_field_keys ?? [],
  );
  const [evidenceFieldKey, setEvidenceFieldKey] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState("");
  const comparisonItems = latestRevision?.bundle.comparison_items ?? [];
  const topicFields: Record<string, RegExp> = {
    이동: /이동|보행|낙상|균형|mobility|walk|fall/, 식사: /식사|영양|수분|삼킴|nutrition|meal|water/,
    수면: /수면|잠|sleep/, 배설: /배설|배변|배뇨|화장실|elimination|toilet/,
    기분: /기분|정서|불안|인지|mood|emotion|cognition/, 활동: /활동|프로그램|일상|activity/,
    "보호자 상담": /보호자|상담|욕구|희망|guardian|family|desire|need/,
    건강: /피부|통증|혈압|투약|건강|skin|pain|medication|health/,
    돌봄: /돌봄|도움|의사소통|care|support|communication/,
  };
  const visibleItems = relevantTopics ? comparisonItems.filter((item) =>
    (item.previous_value !== null || item.current_value !== null) &&
    relevantTopics.some((topic) => topicFields[topic]?.test(`${item.field_key} ${item.label}`)),
  ) : showAll
    ? comparisonItems
    : comparisonItems.filter((item) => item.classification !== "unchanged");
  const evidenceItem = comparisonItems.find(
    (item) => item.field_key === evidenceFieldKey,
  );
  const evidenceRefs = evidenceItem
    ? Array.from(
        new Set([
          ...evidenceItem.baseline_evidence_refs,
          ...evidenceItem.current_evidence_refs,
        ]),
      )
    : [];
  const evidenceSources = latestRevision?.bundle.evidence_sources.filter(
    (source) =>
      typeof source.source_ref === "string" && evidenceRefs.includes(source.source_ref),
  ) ?? [];

  async function saveSelectedChanges() {
    if (!latestRevision || saving) return;
    setSaving(true);
    setSaveStatus("");
    try {
      const saved = await apiFetch<NewAdmissionDraftRecord>(
        `/api/workdesk/residents/${residentId}/new-admission-drafts/${draftRecord.id}/revisions`,
        {
          method: "POST",
          body: JSON.stringify({
            bundle: {
              ...latestRevision.bundle,
              revision: latestRevision.revision + 1,
              supersedes_revision: latestRevision.revision,
              selected_change_field_keys: selectedFieldKeys,
            },
            confirmed_field_keys: Object.entries(latestRevision.field_confirmations)
              .filter(([, confirmation]) => confirmation.state === "confirmed")
              .map(([fieldKey]) => fieldKey),
            reviewed_document_types: Object.entries(
              latestRevision.bundle.document_reviews,
            )
              .filter(([, review]) => review?.state === "reviewed")
              .map(([documentType]) => documentType),
          }),
        },
      );
      setSaveStatus(
        `검토할 변화 ${selectedFieldKeys.length}건을 수정본 v${saved.current_revision}에 저장했습니다.`,
      );
      onSaved(saved);
    } catch (reason) {
      setSaveStatus(
        reason instanceof Error
          ? reason.message
          : "선택 내용을 저장하지 못했습니다. 최신 수정본을 다시 확인해 주세요.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="care-change-review" aria-label="이전 상태와 현재 확인 내용 비교">
      <header>
        <div>
          <h5>{relevantTopics ? "관련 주제의 문서와 이후 기록" : "무엇이 달라졌나요?"}</h5>
          <p>{relevantTopics ? "저장된 비교 결과입니다. 문서 작성 시점과 관찰 기간을 확인해 주세요." : "직전 확정자료와 이후 근거를 비교했습니다. 선택은 공식 기록을 자동 변경하지 않습니다."}</p>
        </div>
        {!relevantTopics ? <button type="button" onClick={() => setShowAll((current) => !current)}>
          {showAll ? "변경된 항목만 보기" : "전체 항목 보기"}
        </button> : null}
      </header>
      {visibleItems.length ? (
        <div className="care-change-table-wrap">
          <table>
            <thead>
              <tr>
                <th>영역</th>
                <th>이전 상태</th>
                <th>{relevantTopics ? "이후 기록에서 확인한 내용" : "현재 확인 내용"}</th>
                <th>판정</th>
                <th>계획 영향 후보</th>
                <th>근거 보기</th>
                {!relevantTopics ? <th>반영 선택</th> : null}
              </tr>
            </thead>
            <tbody>
              {visibleItems.map((item) => {
                const evidenceCount = new Set([
                  ...item.baseline_evidence_refs,
                  ...item.current_evidence_refs,
                ]).size;
                return (
                  <tr key={item.field_key} data-change={item.classification}>
                    <th>{item.label}</th>
                    <td data-label="이전 상태">{item.previous_value == null ? "기록 없음" : String(item.previous_value)}</td>
                    <td data-label="현재 확인 내용">
                      {item.current_value == null ? "근거 부족" : String(item.current_value)}
                      {item.conflict_values.length ? (
                        <small>충돌 자료: {item.conflict_values.join(" / ")}</small>
                      ) : null}
                    </td>
                    <td data-label="판정"><strong>{comparisonLabels[item.classification]}</strong></td>
                    <td data-label="계획 영향 후보">{item.reason || "자동 변경하지 않음"}</td>
                    <td data-label="근거 보기">
                      <button
                        type="button"
                        onClick={() => setEvidenceFieldKey(item.field_key)}
                      >
                        근거 {evidenceCount}건
                      </button>
                    </td>
                    {!relevantTopics ? <td data-label="반영 선택">
                      <label>
                        <input
                          type="checkbox"
                          checked={selectedFieldKeys.includes(item.field_key)}
                          disabled={item.classification === "material_conflict"}
                          onChange={(event) => setSelectedFieldKeys((current) =>
                            event.target.checked
                              ? Array.from(new Set([...current, item.field_key]))
                              : current.filter((fieldKey) => fieldKey !== item.field_key),
                          )}
                        />
                        {item.classification === "material_conflict" ? "직원 확인 필요" : "이번 작성에서 검토"}
                      </label>
                    </td> : null}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted-box">{relevantTopics ? "선택한 돌봄 주제와 연결되는 저장 비교 내용이 없습니다." : "직전 확정자료와 비교해 달라진 항목이 없습니다."}</p>
      )}
      {!relevantTopics ? <><p className="care-change-selection-status" role="status">
        이번 작성에서 검토할 변화 {selectedFieldKeys.length}건 · 충돌값은 선택할 수 없습니다.
      </p>
      <div className="care-change-save-row">
        <button
          type="button"
          disabled={saving || !latestRevision}
          onClick={() => void saveSelectedChanges()}
        >
          {saving ? "저장 중" : "선택 내용 저장"}
        </button>
        {saveStatus ? <p role="status">{saveStatus}</p> : null}
      </div></> : null}
      {evidenceItem ? (
        <aside className="care-evidence-drawer" aria-label={`${evidenceItem.label} 근거 상세`}>
          <header>
            <div>
              <strong>{evidenceItem.label} 근거</strong>
              <p>이전 기준과 현재 자료를 구분해 보여드립니다.</p>
            </div>
            <button type="button" onClick={() => setEvidenceFieldKey(null)}>
              근거 상세 닫기
            </button>
          </header>
          <p><strong>이전 기준값</strong> {evidenceItem.previous_value == null ? "기록 없음" : String(evidenceItem.previous_value)}</p>
          <p><strong>현재 확인 내용</strong> {evidenceItem.current_value == null ? "근거 부족" : String(evidenceItem.current_value)}</p>
          <div className="care-evidence-list">
            {evidenceSources.length ? evidenceSources.map((source, index) => (
              <article key={String(source.source_ref ?? index)}>
                <strong>{String(source.document_kind ?? "확인 자료")}</strong>
                <span>{String(source.organization_role ?? "출처 구분 확인 필요")} · {String(source.verification_state ?? "직원 확인 필요")}</span>
                <p>{String(source.evidence_summary ?? "근거 요약 없음")}</p>
                <small>{String(source.reference_locator ?? "원본 위치 확인 필요")}</small>
              </article>
            )) : <p className="muted-box">연결된 근거의 원본 위치를 확인해 주세요.</p>}
          </div>
        </aside>
      ) : null}
    </section>
  );
}

function listText(values: string[]) {
  return values.join("\n");
}

function textList(value: string) {
  return Array.from(
    new Set(
      value
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean),
    ),
  );
}

function escapeReportText(value: unknown) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

type FormValues = Partial<
  Record<CarePlanningDocumentType, Record<string, string>>
>;

function workspaceFormValues(workspace: NewAdmissionFormWorkspace): FormValues {
  return Object.fromEntries(
    workspace.documents.map((document) => [
      document.document_type,
      Object.fromEntries(
        document.sections.flatMap((section) =>
          section.rows.flatMap((row) => {
            const values: Array<[string, string]> = [
              [row.row_key, row.value],
              [`${row.row_key}::review_state`, row.review_state],
            ];
            Object.entries(row.cells).forEach(([column, value]) => {
              values.push([`${row.row_key}::${column}`, value]);
            });
            return values;
          }),
        ),
      ),
    ]),
  );
}

function formDocumentText(
  document: NewAdmissionFormWorkspace["documents"][number],
  formValues: FormValues,
) {
  const values = formValues[document.document_type] ?? {};
  return document.sections
    .flatMap((section) => {
      const rows = section.rows.map((row) => {
        if (section.columns.length) {
          return section.columns
            .map((column) => `${column}: ${values[`${row.row_key}::${column}`] ?? ""}`)
            .join(" | ");
        }
        return `${row.label}: ${values[row.row_key] ?? ""}`;
      });
      return [`[${section.title}]`, ...rows, ""];
    })
    .join("\n")
    .trim();
}

function printFormWorkspace(
  workspace: NewAdmissionFormWorkspace,
  residentName: string,
  formValues: FormValues,
) {
  const printDocuments = workspace.documents.map((item) => {
    const sections = item.sections.map((section) => {
      if (section.columns.length) {
        return `<section><h3>${escapeReportText(section.title)}</h3><table><thead><tr>${section.columns.map((column) => `<th>${escapeReportText(column)}</th>`).join("")}</tr></thead><tbody>${section.rows.map((row) => `<tr>${section.columns.map((column) => `<td>${escapeReportText(formValues[item.document_type]?.[`${row.row_key}::${column}`] ?? "")}</td>`).join("")}</tr>`).join("")}</tbody></table></section>`;
      }
      return `<section><h3>${escapeReportText(section.title)}</h3>${section.notice ? `<p class="notice">${escapeReportText(section.notice)}</p>` : ""}<table><tbody>${section.rows.map((row) => `<tr><th>${escapeReportText(row.label)}</th><td>${escapeReportText(formValues[item.document_type]?.[row.row_key] ?? "")}</td></tr>`).join("")}</tbody></table></section>`;
    }).join("");
    const printClass =
      item.document_type === "needs_assessment"
        ? "print-form print-form-needs-assessment"
        : item.document_type === "long_term_care_service_plan"
          ? "print-form print-form-care-plan"
          : "print-form";
    return `<article class="${printClass}"><h2>${escapeReportText(item.title)}</h2>${sections}</article>`;
  }).join("");
  const frame = document.createElement("iframe");
  frame.title = "기초사정·급여계획 검토 결과 인쇄 준비";
  frame.setAttribute("aria-hidden", "true");
  Object.assign(frame.style, {
    position: "fixed",
    width: "0",
    height: "0",
    border: "0",
    visibility: "hidden",
  });
  document.body.appendChild(frame);
  const printWindow = frame.contentWindow;
  const printPage = frame.contentDocument;
  if (!printWindow || !printPage) {
    frame.remove();
    throw new Error("인쇄 내용을 준비하지 못했습니다.");
  }
  printPage.open();
  printPage.write(`<!doctype html><html lang="ko"><head><meta charset="utf-8" />
    <title>${escapeReportText(workspace.comprehensive_report.title)}</title>
    <style>
      @page { size: A4; margin: 16mm 14mm 18mm; }
      body { color:#173447; font-family:"Malgun Gothic","Noto Sans KR",sans-serif; }
      header { border-bottom:2px solid #5a7f10; margin-bottom:8mm; }
      h1 { font-size:22px; } h2 { font-size:17px; margin-top:8mm; } h3 { font-size:13px; margin:5mm 0 2mm; }
      p, li { font-size:12px; line-height:1.65; overflow-wrap:anywhere; }
      article { break-before:page; } article:first-of-type { break-before:auto; }
      section { break-inside:avoid; }
      table { width:100%; border-collapse:collapse; table-layout:fixed; }
      th, td { border:1px solid #9aacab; padding:2.5mm; font-size:10px; line-height:1.45; vertical-align:top; overflow-wrap:anywhere; }
      th { background:#f1f5ee; text-align:left; }
      .notice { color:#536773; font-size:10px; }
      .print-form-needs-assessment h2 { margin-top:4mm; margin-bottom:2mm; }
      .print-form-needs-assessment h3 { margin:2mm 0 1mm; }
      .print-form-needs-assessment th,
      .print-form-needs-assessment td { padding:1.4mm 2mm; line-height:1.3; }
      .print-form-care-plan section { break-inside:auto; }
      .print-form-care-plan h2 { margin:3mm 0 1mm; }
      .print-form-care-plan h3 { margin:1.5mm 0 .7mm; font-size:9px; break-after:avoid; }
      .print-form-care-plan table { break-inside:auto; }
      .print-form-care-plan tr { break-inside:avoid; }
      .print-form-care-plan th,
      .print-form-care-plan td { padding:.7mm; font-size:6.8px; line-height:1.12; }
    </style></head><body>
    <header><h1>${escapeReportText(workspace.comprehensive_report.title)}</h1>
      <p><strong>대상 ${escapeReportText(residentName)}</strong> · 수정 이력 v${workspace.revision}</p>
      <p class="notice">DEV 비공식 자료 · 코드화된 합성 시험자료 · 공식 기록 자동 저장 아님</p>
    </header>
    ${printDocuments}
    </body></html>`);
  printPage.close();
  let removed = false;
  const cleanup = () => {
    if (removed) return;
    removed = true;
    frame.remove();
  };
  printWindow.addEventListener("afterprint", cleanup, { once: true });
  window.setTimeout(() => {
    printWindow.focus();
    printWindow.print();
    window.setTimeout(cleanup, 60_000);
  }, 180);
}

type Props = {
  resident: Resident | null;
  initialWorkflowStage?: PlanningWorkflowStage;
  comparisonTopics?: string[];
};

function AssessmentWorkflowPanel({
  flow,
  activeStep,
  onStepChange,
}: {
  flow: NewAdmissionQuestionFlow;
  activeStep: number;
  onStepChange: (stepNumber: number) => void;
}) {
  const workflow = flow.assessment_workflow;
  const step =
    workflow.steps.find((item) => item.step_number === activeStep) ??
    workflow.steps[0];
  if (!step) return null;

  return (
    <section className="new-admission-assessment-workflow" aria-label="기초사정 4단계 안전 작성 순서">
      <div className="assessment-workflow-heading">
        <div>
          <h5>기초사정 4단계 안전 작성 순서</h5>
          <p>{workflow.workflow_notice}</p>
        </div>
        <strong>
          전체 4단계 중 {step.step_number}번째
        </strong>
      </div>
      <div className="assessment-step-tabs" aria-label="기초사정 단계 선택">
        {workflow.steps.map((item) => (
          <button
            type="button"
            key={item.document_type}
            aria-pressed={item.step_number === step.step_number}
            onClick={() => onStepChange(item.step_number)}
          >
            <span>{item.step_number}</span>
            {item.title}
          </button>
        ))}
      </div>
      <article className="assessment-step-detail" aria-live="polite">
        <header>
          <div>
            <strong>{step.title}</strong>
            <small>{step.official_basis_label}</small>
          </div>
          <span>직원 확인 필요</span>
        </header>
        <p className="assessment-standard-notice">{step.official_basis_notice}</p>
        <div className="assessment-step-columns">
          <section>
            <h6>이전 자료에서 재사용한 사실 {step.reusable_fact_count}건</h6>
            {step.reusable_facts.length ? (
              <ul>
                {step.reusable_facts.map((fact) => (
                  <li key={fact.field_key}>
                    <strong>{fact.label}</strong>: {String(fact.value)}
                  </li>
                ))}
              </ul>
            ) : (
              <p>재사용할 확인 사실이 없습니다.</p>
            )}
          </section>
          <section>
            <h6>이 단계에서 새로 확인할 사항 {step.new_question_count}건</h6>
            {step.new_questions.length ? (
              <ul>
                {step.new_questions.map((question) => (
                  <li key={question.question_key}>
                    <strong>{question.question_type_label}</strong> · {question.prompt}
                  </li>
                ))}
              </ul>
            ) : (
              <p>같은 질문을 반복하지 않습니다.</p>
            )}
            {step.carried_pending_count ? (
              <p className="assessment-carried-pending">
                앞 단계에서 확인 중인 사항 {step.carried_pending_count}건은 다시 묻지 않고
                결과만 이어받습니다.
              </p>
            ) : null}
          </section>
        </div>
        <div className="assessment-score-block">
          <strong>점수·위험등급·진단: 아직 확정하지 않음</strong>
          <span>{step.blocked_result_notice}</span>
        </div>
        <p className="assessment-final-safety">
          직원 최종 확인 전 공식 기록 저장·서명 확정·공단 전송은 하지 않습니다.
          {workflow.deferred_care_plan_questions.length
            ? ` 급여계획에서 결정할 항목 ${workflow.deferred_care_plan_questions.length}건은 다음 단계로 분리했습니다.`
            : ""}
        </p>
      </article>
    </section>
  );
}

function PendingPlanItems({
  title,
  items,
}: {
  title: string;
  items: NewAdmissionCarePlanPendingItem[];
}) {
  return (
    <section>
      <h6>{title} {items.length}건</h6>
      {items.length ? (
        <ul>
          {items.map((item) => (
            <li key={item.field_key}>
              <strong>{item.category_label} · {item.label}</strong>
              <span>{item.reason}</span>
              {item.conflict_values.length ? (
                <small>충돌 자료: {item.conflict_values.join(" / ")} · 어느 값도 선택하지 않음</small>
              ) : null}
              {item.evidence_sources.length ? (
                <small>
                  근거: {item.evidence_sources.map((source) => `${source.document_kind}(${source.usage_label})`).join(" · ")}
                </small>
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <p>현재 확인할 항목이 없습니다.</p>
      )}
    </section>
  );
}

const missingReasonLabels: Record<
  AssessmentEvidenceSummary["missing_items"][number]["reason"],
  string
> = {
  evidence_missing: "제출 자료에 근거 없음",
  current_observation_required: "현재 상태 관찰 필요",
  staff_decision_required: "직원 결정 필요",
};

const evidenceDocumentLabels: Record<string, string> = {
  fall_risk_assessment: "낙상위험도",
  pressure_ulcer_risk_assessment: "욕창위험도",
  cognitive_function_assessment: "인지기능검사",
  needs_assessment: "욕구사정",
  long_term_care_service_plan: "장기요양급여 제공계획서",
};

function evidenceDocumentSummary(documentTypes: string[]) {
  return documentTypes
    .map((documentType) => evidenceDocumentLabels[documentType] ?? documentType)
    .join(" · ");
}

function EvidenceSummaryPanel({ summary }: { summary?: AssessmentEvidenceSummary }) {
  if (!summary?.file_intake_complete) return null;
  return (
    <section className="assessment-evidence-summary" aria-label="제출 자료 근거 정리">
      <header>
        <div>
          <h6>자료에서 확인한 내용</h6>
          <p>저장된 제출 자료만 분류했습니다. 충돌값은 자동 선택하지 않습니다.</p>
        </div>
        <div className="assessment-evidence-counts" aria-label="근거 분류 건수">
          <span>확인된 사실 {summary.counts.confirmed_facts}</span>
          <span>자료 충돌 {summary.counts.conflicts}</span>
          <span>근거 부족 {summary.counts.missing_items}</span>
        </div>
      </header>
      {summary.confirmed_facts.length ? (
        <div className="assessment-evidence-confirmed">
          <strong>확인된 사실</strong>
          <ul>
            {summary.confirmed_facts.map((item) => (
              <li key={item.field_key}>
                <span>{item.label}</span>
                <b>{String(item.value)}</b>
                <small>
                  근거 {item.evidence_refs.length}건 · 반영 양식 {evidenceDocumentSummary(item.document_types)}
                </small>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {summary.conflicts.length ? (
        <div className="assessment-evidence-conflicts" role="alert">
          <strong>자료 충돌 · 직원 확인 필요</strong>
          {summary.conflicts.map((item) => (
            <p key={item.field_key}>
              {item.label}: {item.conflict_values.join(" / ")} · 자동 선택하지 않음
              <small>반영 양식 {evidenceDocumentSummary(item.document_types)}</small>
            </p>
          ))}
        </div>
      ) : null}
      {summary.missing_items.length ? (
        <details className="assessment-evidence-missing">
          <summary>근거가 부족한 항목 {summary.missing_items.length}건</summary>
          <ul>
            {summary.missing_items.map((item) => (
              <li key={item.field_key}>
                <span>{item.label}</span>
                <small>
                  {missingReasonLabels[item.reason]} · 보완 양식 {evidenceDocumentSummary(item.document_types)}
                </small>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}

function FormFirstWorkspace({
  workspace,
  evidenceSummary,
  residentName,
  residentId,
  draftRecord,
  workflowStage,
  onSaved,
}: {
  workspace: NewAdmissionFormWorkspace;
  evidenceSummary?: AssessmentEvidenceSummary;
  residentName: string;
  residentId: string;
  draftRecord: NewAdmissionDraftRecord;
  workflowStage: 3 | 4 | 5;
  onSaved: (draft: NewAdmissionDraftRecord) => void;
}) {
  const [activeType, setActiveType] = useState<CarePlanningDocumentType>(
    workspace.documents[0]?.document_type ?? "fall_risk_assessment",
  );
  const [formValues, setFormValues] = useState<FormValues>(() => workspaceFormValues(workspace));
  const [reviewedDocumentTypes, setReviewedDocumentTypes] = useState<CarePlanningDocumentType[]>(
    () => {
      const latest = draftRecord.revisions.find(
        (item) => item.revision === draftRecord.current_revision,
      );
      return assessmentDocumentTypes.filter(
        (documentType) => latest?.bundle.document_reviews?.[documentType]?.state === "reviewed",
      );
    },
  );
  const [actionStatus, setActionStatus] = useState("");
  const [saving, setSaving] = useState(false);
  const [additionalFiles, setAdditionalFiles] = useState<File[]>([]);
  const [addingMaterials, setAddingMaterials] = useState(false);
  const workspaceIdentity = `${draftRecord.id}:${workspace.revision}`;
  const [syncedWorkspaceIdentity, setSyncedWorkspaceIdentity] = useState(workspaceIdentity);
  if (syncedWorkspaceIdentity !== workspaceIdentity) {
    const latest = draftRecord.revisions.find(
      (item) => item.revision === draftRecord.current_revision,
    );
    setSyncedWorkspaceIdentity(workspaceIdentity);
    setFormValues(workspaceFormValues(workspace));
    setReviewedDocumentTypes(
      assessmentDocumentTypes.filter(
        (documentType) => latest?.bundle.document_reviews?.[documentType]?.state === "reviewed",
      ),
    );
    if (!workspace.documents.some((item) => item.document_type === activeType)) {
      setActiveType(workspace.documents[0]?.document_type ?? "fall_risk_assessment");
    }
  }
  const latestRevision = draftRecord.revisions.find(
    (item) => item.revision === draftRecord.current_revision,
  );
  const linkedChatSourceCount = latestRevision?.bundle.evidence_sources.filter(
    (source) => source.document_kind === "선택 기간 매실챗 확인 기록",
  ).length ?? 0;
  const stageDocuments = workspace.documents.filter((item) =>
    workflowStage === 3
      ? assessmentDocumentTypes.some((documentType) => documentType === item.document_type)
      : workflowStage === 4
        ? item.document_type === "long_term_care_service_plan"
        : true,
  );
  const activeDocument =
    stageDocuments.find((item) => item.document_type === activeType) ??
    stageDocuments[0];
  const activeStepNumber = Math.max(
    1,
    workspace.documents.findIndex(
      (item) => item.document_type === activeDocument?.document_type,
    ) + 1,
  );
  const activeIsAssessment = assessmentDocumentTypes.some(
    (documentType) => documentType === activeDocument?.document_type,
  );
  const activeDocumentReviewed = Boolean(
    activeDocument && reviewedDocumentTypes.includes(activeDocument.document_type),
  );

  function updateFormValue(
    documentType: CarePlanningDocumentType,
    key: string,
    value: string,
  ) {
    setFormValues((current) => ({
      ...current,
      [documentType]: {
        ...(current[documentType] ?? {}),
        [key]: value,
      },
    }));
    if (assessmentDocumentTypes.some((item) => item === documentType)) {
      setReviewedDocumentTypes((current) => current.filter((item) => item !== documentType));
    }
    setActionStatus("수정한 내용은 아직 공식 기록으로 저장되지 않았습니다.");
  }

  async function copyActiveDocument() {
    if (!activeDocument) return;
    try {
      await navigator.clipboard.writeText(
        formDocumentText(activeDocument, formValues),
      );
      setActionStatus(`${activeDocument.title} 초안을 복사했습니다.`);
    } catch {
      setActionStatus("복사하지 못했습니다. 초안 내용을 직접 선택해 복사해 주세요.");
    }
  }

  async function saveRevision() {
    const latest = draftRecord.revisions.find(
      (item) => item.revision === draftRecord.current_revision,
    );
    if (!latest || saving) return;
    setSaving(true);
    setActionStatus("");
    try {
      const saved = await apiFetch<NewAdmissionDraftRecord>(
        `/api/workdesk/residents/${residentId}/new-admission-drafts/${draftRecord.id}/revisions`,
        {
          method: "POST",
          body: JSON.stringify({
            bundle: {
              ...latest.bundle,
              revision: latest.revision + 1,
              supersedes_revision: latest.revision,
              form_values: formValues,
              document_texts: Object.fromEntries(
                workspace.documents.map((document) => [
                  document.document_type,
                  formDocumentText(document, formValues),
                ]),
              ),
            },
            confirmed_field_keys: Object.entries(latest.field_confirmations)
              .filter(([, confirmation]) => confirmation.state === "confirmed")
              .map(([fieldKey]) => fieldKey),
            reviewed_document_types: reviewedDocumentTypes,
          }),
        },
      );
      setActionStatus(
        `양식 수정본 v${saved.current_revision}을 저장했습니다. 기초사정 4종 검토 ${reviewedDocumentTypes.length}/4`,
      );
      onSaved(saved);
    } catch (reason) {
      setActionStatus(
        reason instanceof Error
          ? reason.message
          : "수정본을 저장하지 못했습니다. 최신 초안을 다시 불러와 주세요.",
      );
    } finally {
      setSaving(false);
    }
  }

  async function addMaterials() {
    if (!additionalFiles.length || addingMaterials) return;
    const body = new FormData();
    additionalFiles.forEach((file) => {
      body.append("document_kinds", "auto");
      body.append("files", file);
    });
    setAddingMaterials(true);
    setActionStatus("추가자료를 로컬에서 읽고 기존 근거와 비교하고 있습니다.");
    try {
      const saved = await apiFetch<NewAdmissionDraftRecord>(
        `/api/workdesk/residents/${residentId}/new-admission-drafts/${draftRecord.id}/materials`,
        { method: "POST", body },
      );
      setAdditionalFiles([]);
      setActionStatus(
        `상담일지·전원 관련 서류 등 추가자료를 수정본 v${saved.current_revision}에 반영했습니다.`,
      );
      onSaved(saved);
    } catch (reason) {
      setActionStatus(
        reason instanceof Error
          ? reason.message
          : "추가자료를 반영하지 못했습니다. 자료를 확인한 뒤 다시 시도해 주세요.",
      );
    } finally {
      setAddingMaterials(false);
    }
  }

  return (
    <section className="new-admission-form-workspace" aria-label="기초사정 4종과 급여제공계획 양식 초안">
      <p className="care-planning-pc-only-notice">
        모바일에서는 요약과 근거만 확인할 수 있습니다. 양식 수정·저장·출력은 PC에서 진행해 주세요.
      </p>
      <header>
        <div>
          <h5>기초사정·급여제공계획 편집용 초안</h5>
          <p>대상 {residentName} · 근거로 채운 내용을 확인하고 필요한 부분만 수정해 주세요.</p>
        </div>
        {workflowStage === 5 ? (
          <button
            type="button"
            disabled={!workspace.care_plan_gate.available || workspace.documents.length !== 5}
            onClick={() => {
              printFormWorkspace(workspace, residentName, formValues);
              setActionStatus("검토 결과 인쇄창을 열었습니다. 인쇄 또는 PDF 저장을 선택해 주세요.");
            }}
            title={workspace.care_plan_gate.available ? undefined : workspace.care_plan_gate.reason}
          >
            검토 결과 인쇄·PDF
          </button>
        ) : null}
      </header>
      <div className="form-workspace-progress" aria-label="현재 작성 단계">
        <strong>검토 순서 {activeStepNumber}/{workspace.documents.length}</strong>
        <span>현재 {activeDocument?.title ?? "양식"} 작성 중</span>
        <span>
          {workspace.care_plan_gate.available
            ? "기초사정 검토 완료 · 급여계획 초안 작성 가능"
            : "기초사정 검토 진행 중"}
        </span>
      </div>
      <p className="assessment-chat-link-status" role="status">
        {linkedChatSourceCount
          ? `매실챗 ${latestRevision?.bundle.period_start} ~ ${latestRevision?.bundle.period_end} · 활용할 확인 기록 ${linkedChatSourceCount}건 자동 연결`
          : "연결된 매실챗 기록 없음 · 문서자료로 계속 진행합니다."}
      </p>
      {workflowStage === 4 && !workspace.care_plan_gate.available ? (
        <p className="form-workspace-gate" role="status">
          {workspace.care_plan_gate.reason} 네 기초사정 양식을 차례로 확인·저장하면
          장기요양급여 제공계획서 편집용 초안이 열립니다.
        </p>
      ) : null}
      {workflowStage === 3 ? <details className="form-workspace-sources">
        <summary>제출 자료와 근거 보기</summary>
        <div className="form-workspace-materials" aria-label="제출·연결 자료 상태">
          {workspace.materials.map((item) => (
            <span key={item.source_ref}>
              {item.display_label} · {item.document_kind} · {item.status === "submitted" ? "제출됨" : "기존 자료 연결"}
            </span>
          ))}
        </div>
        <EvidenceSummaryPanel summary={evidenceSummary} />
      </details> : null}
      {workflowStage === 3 ? <details className="form-workspace-additional-materials">
        <summary>상담일지·전원 관련 서류 등 추가자료 반영</summary>
        <p>
          나중에 받은 자료도 전체 페이지를 자동 분류해 기존 초안을 덮어쓰지 않고 새 수정본으로 반영합니다.
          서로 다른 내용은 양식에 넣지 않고 근거 보기에서 비교할 수 있게 남깁니다.
        </p>
        <input
          type="file"
          accept="application/pdf,image/jpeg,image/png,.pdf,.jpg,.jpeg,.png"
          multiple
          aria-label="추가 기초자료 선택"
          onChange={(event) =>
            setAdditionalFiles(Array.from(event.target.files ?? []).slice(0, 20))
          }
        />
        <div className="form-workspace-additional-actions">
          <span>{additionalFiles.length ? `추가자료 ${additionalFiles.length}건 선택됨` : "선택된 추가자료 없음"}</span>
          <button
            type="button"
            disabled={!additionalFiles.length || addingMaterials}
            onClick={addMaterials}
          >
            {addingMaterials ? "추가자료 분석 중…" : "추가자료를 새 수정본에 반영"}
          </button>
        </div>
      </details> : null}
      {workflowStage !== 5 ? <div className="form-workspace-tabs" role="tablist" aria-label="양식 초안 선택">
        {stageDocuments.map((item) => (
          <button
            type="button"
            role="tab"
            aria-selected={item.document_type === activeDocument?.document_type}
            key={item.document_type}
            onClick={() => setActiveType(item.document_type)}
          >
            {item.title}
          </button>
        ))}
      </div> : null}
      {workflowStage !== 5 && activeDocument ? (
        <section className="form-workspace-editor" role="tabpanel">
          <div className="form-workspace-editor-heading">
            <div>
              <h6>{activeDocument.title} 편집용 초안</h6>
              <p>양식 항목을 바로 수정해 주세요. 근거가 없는 칸은 비워 두거나 검토 상태만 남길 수 있습니다.</p>
            </div>
            <button type="button" onClick={copyActiveDocument}>이 양식 복사</button>
          </div>
          <div className="form-structure-preview" aria-label={`${activeDocument.title} 실제 양식 구조 미리보기`}>
            {activeDocument.sections.map((section) => (
              <section key={section.section_key}>
                <h6>{section.title}</h6>
                {section.notice ? <p>{section.notice}</p> : null}
                <div className="form-structure-table-wrap">
                  {section.columns.length ? (
                    <table>
                      <thead>
                        <tr>
                          {section.columns.map((column) => <th key={column}>{column}</th>)}
                        </tr>
                      </thead>
                      <tbody>
                        {section.rows.map((row) => (
                          <tr key={row.row_key}>
                            {section.columns.map((column) => {
                              const cellState = row.cell_states[column] ?? "";
                              const evidenceCount = new Set(
                                row.cell_evidence_refs[column] ?? [],
                              ).size;
                              const stateLabel = carePlanCellStateLabels[cellState];
                              return (
                                <td
                                  key={`${row.row_key}-${column}`}
                                  data-label={column}
                                  data-cell-state={cellState || undefined}
                                >
                                  <textarea
                                    rows={2}
                                    readOnly={column === "장기요양 필요영역"}
                                    value={formValues[activeDocument.document_type]?.[`${row.row_key}::${column}`] ?? ""}
                                    onChange={(event) => updateFormValue(
                                      activeDocument.document_type,
                                      `${row.row_key}::${column}`,
                                      event.target.value,
                                    )}
                                    aria-label={`${row.label} ${column}`}
                                  />
                                  {stateLabel ? (
                                    <small className="form-care-plan-cell-state">
                                      {stateLabel}
                                      {evidenceCount ? ` · 근거 ${evidenceCount}건` : ""}
                                    </small>
                                  ) : null}
                                </td>
                              );
                            })}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <table>
                      <tbody>
                        {section.rows.map((row) => (
                          <tr key={row.row_key}>
                            <th>{row.label}</th>
                            <td>
                              <textarea
                                rows={row.value.length > 80 ? 3 : 2}
                                value={formValues[activeDocument.document_type]?.[row.row_key] ?? ""}
                                placeholder=""
                                onChange={(event) => updateFormValue(
                                  activeDocument.document_type,
                                  row.row_key,
                                  event.target.value,
                                )}
                                aria-label={`${row.label} 입력`}
                              />
                            </td>
                            <td>
                              {activeIsAssessment ? (
                                <select
                                  value={(formValues[activeDocument.document_type]?.[`${row.row_key}::review_state`] ?? "") as AssessmentFormReviewState}
                                  onChange={(event) => updateFormValue(
                                    activeDocument.document_type,
                                    `${row.row_key}::review_state`,
                                    event.target.value,
                                  )}
                                  aria-label={`${row.label} 검토 상태`}
                                >
                                  {Object.entries(formReviewStateLabels).map(([value, label]) => (
                                    <option key={value || "empty"} value={value}>{label}</option>
                                  ))}
                                </select>
                              ) : null}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              </section>
            ))}
          </div>
          {activeDocument.staff_input_count ? (
            <details className="form-workspace-check-list">
              <summary>직원이 확인할 내용</summary>
              <p>자료로 채우지 못한 칸만 상담과 관찰 결과로 보완해 주세요.</p>
              <ul>
                {activeDocument.items
                  .filter((item) => !["confirmed_fact", "evidence_found"].includes(item.state))
                  .map((item) => (
                    <li key={item.field_key}>
                      {item.label}
                    </li>
                  ))}
              </ul>
            </details>
          ) : null}
          <details className="form-workspace-evidence">
            <summary>근거 보기</summary>
            <ul>
              {activeDocument.items.map((item) => (
                <li key={item.field_key}>
                  <strong>{item.label}</strong> · {item.state_label}
                  <small>
                    {item.evidence_sources.length
                      ? item.evidence_sources.map((source) => source.document_kind).join(" · ")
                      : "연결된 근거 없음"}
                    {item.revision_links.length
                      ? ` · 수정 이력 ${item.revision_links.map((link) => `v${link.revision}`).join(" → ")}`
                      : ""}
                  </small>
                </li>
              ))}
            </ul>
          </details>
          {activeIsAssessment ? (
            <label className="form-workspace-document-review">
              <input
                type="checkbox"
                checked={activeDocumentReviewed}
                onChange={(event) => {
                  setReviewedDocumentTypes((current) => event.target.checked
                    ? Array.from(new Set([...current, activeDocument.document_type]))
                    : current.filter((item) => item !== activeDocument.document_type));
                  setActionStatus(
                    event.target.checked
                      ? `${activeDocument.title} 전체를 검토 완료로 표시했습니다. 저장해야 다음 단계에 반영됩니다.`
                      : `${activeDocument.title} 검토 완료 표시를 해제했습니다.`,
                  );
                }}
              />
              이 양식의 내용을 검토했습니다
            </label>
          ) : null}
        </section>
      ) : null}
      {workflowStage === 5 ? (
        <section className="form-workspace-final-review">
          <h6>저장·출력</h6>
          <p>최신 수정본 v{workspace.revision}을 기준으로 검토한 기초사정 4종과 급여제공계획을 확인합니다.</p>
          <ul>
            {workspace.documents.map((document) => (
              <li key={document.document_type}>
                <strong>{document.title}</strong> · {document.status === "needs_confirmation" ? "직원 확인 필요" : "편집용 초안"}
              </li>
            ))}
          </ul>
          <p>이전 수정본은 삭제하지 않으며, 출력에는 기술용 표식과 긴 근거 원문을 넣지 않습니다.</p>
        </section>
      ) : null}
      <p className="form-workspace-safety">{workspace.safety.notice}</p>
      <div className="form-workspace-save">
        <button type="button" disabled={saving} onClick={saveRevision}>
          {saving ? "양식 저장 중…" : "현재 양식과 검토 상태 저장"}
        </button>
        <small>
          기존 초안은 덮어쓰지 않습니다. 저장하면 새 수정 이력으로 추가되며 공식 기록·서명·공단 전송은 실행하지 않습니다.
        </small>
        <span>저장된 수정 이력 {draftRecord.revisions.map((item) => `v${item.revision}`).join(" → ")}</span>
      </div>
      {actionStatus ? <p className="form-success" role="status">{actionStatus}</p> : null}
    </section>
  );
}

function CarePlanDraftPreview({
  flow,
  residentName,
}: {
  flow: NewAdmissionQuestionFlow;
  residentName: string;
}) {
  const preview = flow.care_plan_preview;
  const [edits, setEdits] = useState<Record<string, string>>({});

  if (!preview) return null;

  return (
    <section className="new-admission-care-plan-preview" aria-label="장기요양급여 제공계획 편집용 초안">
      <header>
        <div>
          <h5>장기요양급여 제공계획 편집용 초안</h5>
          <p>
            대상 · {residentName} · 현재 수정 이력 v{preview.revision}의 직원 확인
            사실만 반영했습니다.
          </p>
        </div>
        <span>{preview.output_status === "needs_confirmation" ? "직원 확인 필요" : "편집용 초안"}</span>
      </header>
      <p className="care-plan-draft-safety">{preview.safety_notice}</p>
      <div className="care-plan-draft-summary">
        <span>확인된 기초사정 결과 {preview.confirmed_result_count}건</span>
        <span>초안 반영 {preview.reflected_item_count}건</span>
        <span>직원 질문 {preview.staff_question_count}건</span>
        <span>충돌·현재 관찰 {preview.conflict_or_observation_count}건</span>
      </div>
      <section className="care-plan-confirmed-results">
        <h6>확인된 기초사정 결과</h6>
        {preview.confirmed_assessment_results.length ? (
          <ul>
            {preview.confirmed_assessment_results.map((item) => (
              <li key={item.field_key}>
                <strong>{item.label}</strong>: {String(item.value)}
                <small>
                  직원 확인 완료 · 근거 {item.evidence_refs.length}건 · 수정 이력 {item.revision_links.map((link) => `v${link.revision}`).join(" → ")}
                </small>
              </li>
            ))}
          </ul>
        ) : (
          <p>직원이 확인 완료한 기초사정 사실이 없어 초안에 자동 반영하지 않았습니다.</p>
        )}
      </section>
      <section className="care-plan-editable-items">
        <h6>급여제공계획 초안에 반영된 내용</h6>
        {preview.reflected_plan_items.length ? (
          <div>
            {preview.reflected_plan_items.map((item) => (
              <label key={item.field_key}>
                {item.label}
                <textarea
                  rows={2}
                  value={edits[item.field_key] ?? item.draft_text}
                  onChange={(event) =>
                    setEdits((current) => ({
                      ...current,
                      [item.field_key]: event.target.value,
                    }))
                  }
                  aria-describedby={`care-plan-evidence-${item.field_key}`}
                />
                <small id={`care-plan-evidence-${item.field_key}`}>
                  근거: {item.evidence_sources.map((source) => `${source.document_kind}(${source.usage_label})`).join(" · ")} · 수정 이력 {item.revision_links.map((link) => `v${link.revision}`).join(" → ")}
                </small>
              </label>
            ))}
          </div>
        ) : (
          <p>현재 자동으로 채울 수 있는 직원 확인 사실이 없습니다.</p>
        )}
        <p className="care-plan-unsaved-note">
          이 화면에서 다듬은 문구는 아직 저장되지 않습니다. 저장 전 원문 근거와 변경 내용을 다시 확인해 주세요.
        </p>
      </section>
      <div className="care-plan-pending-grid">
        <PendingPlanItems title="직원에게 물어볼 내용" items={preview.staff_questions} />
        <PendingPlanItems title="자료 충돌·현재 관찰 필요" items={preview.conflicts_and_current_observations} />
        <PendingPlanItems title="전문가·공식 기준 확인 필요" items={preview.expert_review_items} />
      </div>
      <footer>
        <p>
          급여 종류·횟수·시간·점수·진단·목표·서명은 확인된 값 없이 만들지 않습니다.
          직원 확인 전에는 아래 공식 동작을 사용할 수 없습니다.
        </p>
        <div aria-label="직원 확인 전 잠긴 공식 동작">
          <button type="button" disabled title="직원 확인과 공식 저장 절차가 아직 완료되지 않았습니다.">공식 기록 저장</button>
          <button type="button" disabled title="직원이 원문과 초안을 확인하기 전에는 서명할 수 없습니다.">서명 확정</button>
          <button type="button" disabled title="공식 기록과 서명이 확정되지 않아 전송할 수 없습니다.">공단 전송</button>
        </div>
      </footer>
    </section>
  );
}

export function ResidentCarePlanningPanel({ resident, initialWorkflowStage = 1, comparisonTopics }: Props) {
  const [profile, setProfile] = useState<ResidentCarePlanningProfile | null>(null);
  const [draftHistory, setDraftHistory] = useState<NewAdmissionDraftList | null>(null);
  const [questionFlows, setQuestionFlows] = useState<Record<string, NewAdmissionQuestionFlow>>({});
  const [evidenceSummaries, setEvidenceSummaries] = useState<Record<string, AssessmentEvidenceSummary>>({});
  const [draftRecords, setDraftRecords] = useState<Record<string, NewAdmissionDraftRecord>>({});
  const [activeAssessmentSteps, setActiveAssessmentSteps] = useState<Record<string, number>>({});
  const [workflowStage, setWorkflowStage] = useState<PlanningWorkflowStage>(initialWorkflowStage);
  const [loading, setLoading] = useState(Boolean(resident));
  const [savingType, setSavingType] = useState<CarePlanningDocumentType | null>(
    null,
  );
  const [statusMessage, setStatusMessage] = useState("");
  const [error, setError] = useState("");
  const [showFullWorkspace, setShowFullWorkspace] = useState(false);
  const [comparisonDraftId, setComparisonDraftId] = useState("");
  const latestDraftSummary = draftHistory?.items[0] ?? null;
  const previousDraftSummaries = draftHistory?.items.slice(1) ?? [];
  const latestFlow = latestDraftSummary ? questionFlows[latestDraftSummary.id] : undefined;
  const latestDraftRecord = latestDraftSummary ? draftRecords[latestDraftSummary.id] : undefined;
  const latestDraftRevision = latestDraftRecord?.revisions.find(
    (item) => item.revision === latestDraftRecord.current_revision,
  );
  const latestBasisDateByDocument = new Map<CarePlanningDocumentType, string>();
  profile?.items.forEach((item) => {
    if (!item.assessment_date) return;
    const current = latestBasisDateByDocument.get(item.document_type);
    if (!current || item.assessment_date > current) {
      latestBasisDateByDocument.set(item.document_type, item.assessment_date);
    }
  });
  draftHistory?.items.forEach((draft) => {
    if (draft.reviewed_assessment_count !== 4 || !draft.assessment_date) return;
    assessmentDocumentTypes.forEach((documentType) => {
      const current = latestBasisDateByDocument.get(documentType);
      if (!current || draft.assessment_date! > current) {
        latestBasisDateByDocument.set(documentType, draft.assessment_date!);
      }
    });
  });
  const previousBasisDates = [...latestBasisDateByDocument.values()].sort();

  useEffect(() => {
    let disposed = false;
    if (!resident) return () => undefined;
    Promise.resolve().then(() => {
      if (!disposed) {
        setLoading(true);
        setError("");
        setQuestionFlows({});
        setEvidenceSummaries({});
        setActiveAssessmentSteps({});
      }
    });
    Promise.all([
      apiFetch<ResidentCarePlanningProfile>(
        `/api/workdesk/residents/${resident.id}/care-planning`,
      ),
      apiFetch<NewAdmissionDraftList>(
        `/api/workdesk/residents/${resident.id}/new-admission-drafts`,
      ),
    ])
      .then(async ([planningPayload, draftPayload]) => {
        if (!disposed) {
          setProfile(planningPayload);
          setDraftHistory(draftPayload);
        }
        const activeDrafts = draftPayload.items.slice(0, 1);
        const flowEntries = await Promise.all(
          activeDrafts.map(async (draft) => [
            draft.id,
            await apiFetch<NewAdmissionQuestionFlow>(
              `/api/workdesk/residents/${resident.id}/new-admission-drafts/${draft.id}/questions`,
            ),
          ] as const),
        );
        const recordEntries = await Promise.all(
          activeDrafts.map(async (draft) => [
            draft.id,
            await apiFetch<NewAdmissionDraftRecord>(
              `/api/workdesk/residents/${resident.id}/new-admission-drafts/${draft.id}`,
            ),
          ] as const),
        );
        const evidenceEntries = await Promise.all(
          activeDrafts.map(async (draft) => [
            draft.id,
            await apiFetch<AssessmentEvidenceSummary>(
              `/api/workdesk/residents/${resident.id}/new-admission-drafts/${draft.id}/evidence-summary`,
            ),
          ] as const),
        );
        if (!disposed) {
          setQuestionFlows(Object.fromEntries(flowEntries));
          setDraftRecords(Object.fromEntries(recordEntries));
          setEvidenceSummaries(Object.fromEntries(evidenceEntries));
        }
      })
      .catch((reason) => {
        if (!disposed) {
          setError(
            reason instanceof Error
              ? reason.message
              : "기초사정·급여계획 정보를 불러오지 못했습니다.",
          );
        }
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });
    return () => {
      disposed = true;
    };
  }, [resident]);

  async function refreshDraft(draft: NewAdmissionDraftRecord) {
    if (!resident) return;
    const currentRevision = draft.revisions.find(
      (item) => item.revision === draft.current_revision,
    );
    setDraftRecords((current) => ({ ...current, [draft.id]: draft }));
    setDraftHistory((current) => current ? {
      ...current,
      items: [
        {
          id: draft.id,
          case_ref: draft.case_ref,
          current_revision: draft.current_revision,
          status: draft.status,
          reason: currentRevision?.bundle.reason ?? "new_admission",
          assessment_date: currentRevision?.bundle.assessment_date ?? null,
          reviewed_assessment_count: assessmentDocumentTypes.filter(
            (documentType) =>
              currentRevision?.bundle.document_reviews?.[documentType]?.state === "reviewed",
          ).length,
          updated_at: draft.updated_at,
        },
        ...current.items.filter((item) => item.id !== draft.id),
      ],
    } : current);
    try {
      const [flow, evidenceSummary] = await Promise.all([
        apiFetch<NewAdmissionQuestionFlow>(
          `/api/workdesk/residents/${resident.id}/new-admission-drafts/${draft.id}/questions`,
        ),
        apiFetch<AssessmentEvidenceSummary>(
          `/api/workdesk/residents/${resident.id}/new-admission-drafts/${draft.id}/evidence-summary`,
        ),
      ]);
      setQuestionFlows((current) => ({ ...current, [draft.id]: flow }));
      setEvidenceSummaries((current) => ({ ...current, [draft.id]: evidenceSummary }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "저장된 초안을 다시 불러오지 못했습니다.");
    }
  }

  function updateItem(
    documentType: CarePlanningDocumentType,
    patch: Partial<ResidentCarePlanningState>,
  ) {
    setProfile((current) =>
      current
        ? {
            ...current,
            items: current.items.map((item) =>
              item.document_type === documentType ? { ...item, ...patch } : item,
            ),
          }
        : current,
    );
  }

  async function saveItem(item: ResidentCarePlanningState) {
    if (!resident) return;
    setSavingType(item.document_type);
    setStatusMessage("");
    setError("");
    try {
      const saved = await apiFetch<ResidentCarePlanningState>(
        `/api/workdesk/residents/${resident.id}/care-planning/${item.document_type}`,
        {
          method: "PUT",
          body: JSON.stringify({
            assessment_date: item.assessment_date,
            valid_until: item.valid_until,
            next_due_date: item.next_due_date,
            cycle_months: item.cycle_months,
            reassess_on_state_change: item.reassess_on_state_change,
            state_change_triggered: item.state_change_triggered,
            change_reason: item.change_reason,
            status: item.status,
            evidence_refs: item.evidence_refs,
            known_facts: item.known_facts,
            questions_required: item.questions_required,
            professional_review_fields: item.professional_review_fields,
            author_confirmed: item.author_confirmed,
          }),
        },
      );
      updateItem(item.document_type, saved);
      setStatusMessage(
        `${documentLabels[item.document_type]} 상태를 저장하고 다시 불러왔습니다.`,
      );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "기초사정·급여계획 상태를 저장하지 못했습니다.",
      );
    } finally {
      setSavingType(null);
    }
  }

  async function openComparisonDraft(id: string) {
    if (!resident) return;
    setComparisonDraftId(id);
    if (draftRecords[id]) return;
    setLoading(true);
    setError("");
    try {
      const saved = await apiFetch<NewAdmissionDraftRecord>(`/api/workdesk/residents/${resident.id}/new-admission-drafts/${id}`);
      setDraftRecords((current) => ({ ...current, [id]: saved }));
    } catch {
      setError("저장된 비교 자료를 읽지 못했습니다. 다시 시도해 주세요.");
    } finally {
      setLoading(false);
    }
  }

  if (comparisonTopics && !showFullWorkspace) {
    const comparisonRecord = draftRecords[comparisonDraftId || latestDraftSummary?.id || ""];
    const comparisonRevision = comparisonRecord?.revisions.find((item) => item.revision === comparisonRecord.current_revision);
    return <section className="resident-care-planning care-briefing-comparison-view" aria-label="기존 서류 비교 자료">
      {loading ? <p role="status">저장된 비교 자료를 읽고 있습니다…</p> : error ? <div className="form-error" role="alert"><p>{error}</p>{comparisonDraftId ? <button type="button" onClick={() => void openComparisonDraft(comparisonDraftId)}>다시 시도</button> : null}</div> : <>
        {draftHistory?.items.length ? <label>저장된 비교 자료<select aria-label="저장된 비교 자료" value={comparisonDraftId || latestDraftSummary?.id || ""} onChange={(event) => void openComparisonDraft(event.target.value)}>{draftHistory.items.map((draft) => <option key={draft.id} value={draft.id}>{assessmentReasonLabels[draft.reason]} · {draft.assessment_date ?? "작성일 미확인"} · 수정 {draft.current_revision}</option>)}</select></label> : null}
        {comparisonRecord && comparisonRevision && resident ? <>
          <p>문서 검토 기준일: {comparisonRevision.bundle.assessment_date ?? "미확인"}<br />저장된 관찰 기간: {comparisonRevision.bundle.period_start ?? "미확인"} ~ {comparisonRevision.bundle.period_end ?? "미확인"}</p>
          <p>현재 브리핑과 기간이 다를 수 있습니다. 오래된 문서 내용을 현재 상태로 확정하지 않습니다.</p>
          <ChangeComparisonPanel key={comparisonRecord.id} draftRecord={comparisonRecord} residentId={resident.id} relevantTopics={comparisonTopics} onSaved={refreshDraft} />
        </> : <p className="muted-box">아직 연결된 이전 서류가 없습니다. 대화 경과는 그대로 확인할 수 있습니다.</p>}
      </>}
      <button type="button" onClick={() => { setShowFullWorkspace(true); setWorkflowStage(1); }}>기존 자료 연결·파일 첨부</button>
    </section>;
  }

  return (
    <section className="resident-care-planning" aria-label="기초사정·급여제공계획 검토">
      <div className="section-heading">
        <div>
          <h3>기초사정·급여제공계획 검토</h3>
          <p>
            제출자료와 누적 돌봄기록을 근거로 채운 편집용 초안을 검토하고
            필요한 부분만 수정·저장·출력합니다.
          </p>
        </div>
      </div>

      {!resident ? (
        <p className="muted-box">
          위 도우미 작성 대상에서 어르신 한 분을 선택하면 기초사정 4종과
          급여제공계획 상태를 확인할 수 있습니다.
        </p>
      ) : loading ? (
        <p className="muted-box">기초사정·급여계획 상태를 불러오는 중입니다…</p>
      ) : profile ? (
        <>
          <details className="care-planning-notices">
            <summary>작성 기준과 안전 안내</summary>
            <div>
              <p>
                <strong>사용자 제공 운영 원칙</strong> {profile.operating_cycle_notice}
              </p>
              <p>
                <strong>최신 기준 확인 필요</strong> {profile.legal_standard_notice}
              </p>
              <p>
                AI는 점수·진단·급여계획을 자동 확정하지 않습니다. 저장된 사실,
                추가 질문, 전문가 확인 항목을 직원이 직접 구분합니다.
              </p>
            </div>
          </details>
          <nav className="care-planning-workflow-nav" aria-label="기초사정·급여계획 작성 단계">
            {planningWorkflowStages.map((item) => (
              <button
                type="button"
                key={item.stage}
                aria-current={workflowStage === item.stage ? "step" : undefined}
                disabled={item.stage > 1 && !latestDraftSummary}
                onClick={() => setWorkflowStage(item.stage)}
              >
                {item.label}
              </button>
            ))}
          </nav>
          {latestFlow ? (
            <section className="care-planning-workflow-summary" aria-label="현재 작성 요약">
              <span>대상 <strong>{resident.display_name}</strong></span>
              <span>작성 사유 <strong>{assessmentReasonLabels[latestFlow.form_workspace.reason]}</strong></span>
              <span>근거 기간 <strong>{latestFlow.form_workspace.period_start ?? "없음"} ~ {latestFlow.form_workspace.period_end ?? "없음"}</strong></span>
              <details className="care-planning-evidence-summary">
                <summary>근거 요약 보기</summary>
                <div>
                  <span>확인된 사실 <strong>{latestFlow.form_workspace.analysis_summary.confirmed_fact_count}건</strong></span>
                  <span>검토할 변화 <strong>{latestDraftRevision?.bundle.comparison_items.filter((item) => item.classification !== "unchanged").length ?? 0}건</strong></span>
                  <span>자료 충돌 <strong>{latestDraftRevision?.bundle.comparison_items.filter((item) => item.classification === "material_conflict").length ?? 0}건</strong></span>
                  <span>직원 보완 <strong>{latestFlow.form_workspace.analysis_summary.staff_input_count}건</strong></span>
                </div>
                <p>제출자료와 누적 돌봄기록에서 확인된 내용만 현재 편집본에 반영했습니다.</p>
              </details>
            </section>
          ) : null}
          {workflowStage === 1 ? (
            <AssessmentDraftIntakeWorkspace
              key={`${resident.id}-${previousBasisDates.join("-") || "no-basis"}`}
              resident={resident}
              previousBasisDates={previousBasisDates}
              onCreated={(draft) => {
                void refreshDraft(draft);
                setWorkflowStage(2);
              }}
            />
          ) : null}
          {workflowStage > 1 ? <section
            className="new-admission-draft-history"
            aria-label="저장된 기초사정·급여계획 초안"
          >
            <h4>기초사정·급여제공계획 검토 결과</h4>
            <p>
              현재 편집본에서 기초사정과 급여제공계획을 차례로 검토합니다.
              확인되지 않은 칸은 직원 보완으로 남고 이전 수정본은 그대로 보존됩니다.
            </p>
            {latestDraftSummary ? (
              <ul>
                <li key={latestDraftSummary.id}>
                    <strong>
                      현재 편집본 · {draftRecords[latestDraftSummary.id]?.revisions.length
                        ? assessmentReasonLabels[
                            draftRecords[latestDraftSummary.id].revisions[
                              draftRecords[latestDraftSummary.id].revisions.length - 1
                            ].bundle.reason
                          ]
                        : "기초사정·급여계획"}
                    </strong>{" "}
                    · 수정 이력 {latestDraftSummary.current_revision} ·{" "}
                    {latestDraftSummary.status === "needs_confirmation" ? "직원 확인 필요" : "초안"}
                    {questionFlows[latestDraftSummary.id] && draftRecords[latestDraftSummary.id] ? (
                      <>
                        {workflowStage === 2 ? (
                          <ChangeComparisonPanel
                            key={latestDraftSummary.id}
                            draftRecord={draftRecords[latestDraftSummary.id]}
                            residentId={resident.id}
                            onSaved={refreshDraft}
                          />
                        ) : workflowStage === 3 || workflowStage === 4 || workflowStage === 5 ? (
                          <FormFirstWorkspace
                            key={latestDraftSummary.id}
                            workspace={questionFlows[latestDraftSummary.id].form_workspace}
                            evidenceSummary={evidenceSummaries[latestDraftSummary.id]}
                            residentName={resident.display_name}
                            residentId={resident.id}
                            draftRecord={draftRecords[latestDraftSummary.id]}
                            workflowStage={workflowStage}
                            onSaved={refreshDraft}
                          />
                        ) : null}
                        <details className="new-admission-question-flow new-admission-supporting-flow">
                          <summary>
                            작성 순서·확인 항목 상세 보기 · 재사용 사실 {questionFlows[latestDraftSummary.id].reusable_fact_count}건 · 보완 {questionFlows[latestDraftSummary.id].question_count}건
                          </summary>
                          <AssessmentWorkflowPanel
                            flow={questionFlows[latestDraftSummary.id]}
                            activeStep={activeAssessmentSteps[latestDraftSummary.id] ?? 1}
                            onStepChange={(stepNumber) =>
                              setActiveAssessmentSteps((current) => ({
                                ...current,
                                [latestDraftSummary.id]: stepNumber,
                              }))
                            }
                          />
                          {questionFlows[latestDraftSummary.id].care_plan_preview ? (
                            <CarePlanDraftPreview
                              flow={questionFlows[latestDraftSummary.id]}
                              residentName={resident.display_name}
                            />
                          ) : null}
                          <div className="new-admission-source-summary">
                            <span>
                              작성기관 직접 확인 {questionFlows[latestDraftSummary.id].source_counts.authoring_direct_verified ?? 0}건
                            </span>
                            <span>
                              작성기관 확인 필요 {questionFlows[latestDraftSummary.id].source_counts.authoring_needs_review ?? 0}건
                            </span>
                            <span>
                              다른 기관 참고 {questionFlows[latestDraftSummary.id].source_counts.reference_only ?? 0}건
                            </span>
                          </div>
                          <section>
                            <h5>다시 묻지 않고 재사용하는 사실</h5>
                            <ul>
                              {questionFlows[latestDraftSummary.id].reusable_facts.map((fact) => (
                                <li key={fact.field_key}>
                                  <strong>{fact.label}</strong>: {String(fact.value)}
                                  <small>
                                    {fact.status_label} · {fact.source_usage_labels.join(" · ")}
                                  </small>
                                </li>
                              ))}
                            </ul>
                          </section>
                          <section>
                            <h5>직원에게 확인할 항목</h5>
                            <ol>
                              {questionFlows[latestDraftSummary.id].questions.map((question) => (
                                <li key={question.question_key}>
                                  <strong>[{question.question_type_label}]</strong>{" "}
                                  {question.prompt}
                                  {question.conflict_values.length ? (
                                    <small>충돌 자료: {question.conflict_values.join(" / ")}</small>
                                  ) : (
                                    <small>{question.reason}</small>
                                  )}
                                </li>
                              ))}
                            </ol>
                          </section>
                          <p className="new-admission-question-safety">
                            이미 확인된 동일 질문은 반복하지 않습니다. 충돌·현재 상태·점수·진단·서명은
                            자동 확정하지 않으며, 결과는 초안 또는 직원 확인 필요 상태로만 유지됩니다.
                          </p>
                        </details>
                      </>
                    ) : (
                      <small>재사용 사실과 직원 확인 항목을 불러오는 중입니다…</small>
                    )}
                  </li>
              </ul>
            ) : (
              <p className="muted-box">저장된 신규입소 초안 개정 이력이 없습니다.</p>
            )}
            {previousDraftSummaries.length ? (
              <details className="new-admission-previous-history">
                <summary>이전 분석 이력 {previousDraftSummaries.length}건</summary>
                <p>과거 분석 결과는 삭제하지 않고 보존합니다. 현재 초안과 혼동되지 않도록 기본 화면에서는 접어 둡니다.</p>
                <ol>
                  {previousDraftSummaries.map((draft) => (
                    <li key={draft.id}>
                      수정 이력 {draft.current_revision} · {draft.status === "needs_confirmation" ? "검토 필요" : "이전 초안"} · {new Date(draft.updated_at).toLocaleString("ko-KR")}
                    </li>
                  ))}
                </ol>
              </details>
            ) : null}
          </section> : null}
          {statusMessage ? <p className="form-success" role="status">{statusMessage}</p> : null}
          <details className="legacy-care-planning-state">
            <summary>기존 작성 상태 관리 보기</summary>
            <p>이전 저장자료의 호환 상태를 확인할 때만 사용합니다. 새 작성은 위 자료 제출 흐름을 사용해 주세요.</p>
          <div className="care-planning-list">
            {profile.items.map((item) => (
              <details
                className="care-planning-card"
                key={item.document_type}
              >
                <summary>
                  <span>
                    <strong>{documentLabels[item.document_type]}</strong>
                    <small>
                      마지막 {item.assessment_date ?? "미작성"} · 다음{" "}
                      {item.next_due_date ?? "미정"} ·{" "}
                      {item.state_change_triggered
                        ? "상태변경 후보"
                        : item.candidate_reasons.length
                          ? item.candidate_reasons.join(" · ")
                          : "상태변경 없음"}
                    </small>
                  </span>
                  <span className={item.is_candidate ? "is-candidate" : ""}>
                    {statusLabels[item.status]} · v{item.version}
                  </span>
                </summary>
                <div className="care-planning-fields">
                  <label>
                    평가·계획 기준일
                    <input
                      type="date"
                      value={item.assessment_date ?? ""}
                      onInput={(event) =>
                        updateItem(item.document_type, {
                          assessment_date: event.currentTarget.value || null,
                        })
                      }
                    />
                  </label>
                  <label>
                    유효일
                    <input
                      type="date"
                      value={item.valid_until ?? ""}
                      onInput={(event) =>
                        updateItem(item.document_type, {
                          valid_until: event.currentTarget.value || null,
                        })
                      }
                    />
                  </label>
                  <label>
                    다음 예정일
                    <input
                      type="date"
                      value={item.next_due_date ?? ""}
                      onInput={(event) =>
                        updateItem(item.document_type, {
                          next_due_date: event.currentTarget.value || null,
                        })
                      }
                    />
                  </label>
                  <label>
                    운영주기(개월)
                    <input
                      type="number"
                      min={1}
                      max={24}
                      value={item.cycle_months}
                      onChange={(event) =>
                        updateItem(item.document_type, {
                          cycle_months: Number(event.target.value) || 6,
                        })
                      }
                    />
                  </label>
                  <label>
                    작성 상태
                    <select
                      value={item.status}
                      onChange={(event) =>
                        updateItem(item.document_type, {
                          status: event.target.value as ResidentCarePlanningState["status"],
                          author_confirmed:
                            event.target.value === "confirmed"
                              ? item.author_confirmed
                              : false,
                        })
                      }
                    >
                      <option value="not_started">미작성</option>
                      <option value="draft">작성 중</option>
                      <option value="needs_review">직원 확인 필요</option>
                      <option value="confirmed">직원 확인 완료</option>
                    </select>
                  </label>
                </div>
                <div className="care-planning-checks">
                  <label>
                    <input
                      type="checkbox"
                      checked={item.reassess_on_state_change}
                      onChange={(event) =>
                        updateItem(item.document_type, {
                          reassess_on_state_change: event.target.checked,
                        })
                      }
                    />
                    상태 변경 시 즉시 재검토 후보
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={item.state_change_triggered}
                      onChange={(event) =>
                        updateItem(item.document_type, {
                          state_change_triggered: event.target.checked,
                        })
                      }
                    />
                    현재 상태 변경 발생
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={item.author_confirmed}
                      onChange={(event) =>
                        updateItem(item.document_type, {
                          author_confirmed: event.target.checked,
                          status: event.target.checked ? "confirmed" : "needs_review",
                        })
                      }
                    />
                    작성자가 근거와 항목을 확인함
                  </label>
                </div>
                <label className="care-planning-wide-field">
                  상태 변경 사유
                  <textarea
                    value={item.change_reason ?? ""}
                    onChange={(event) =>
                      updateItem(item.document_type, {
                        change_reason: event.target.value || null,
                      })
                    }
                    placeholder="변경이 있을 때 관찰된 사실만 적어 주세요."
                  />
                </label>
                <div className="care-planning-text-grid">
                  <label>
                    저장된 사실
                    <textarea
                      value={listText(item.known_facts)}
                      onChange={(event) =>
                        updateItem(item.document_type, {
                          known_facts: textList(event.target.value),
                        })
                      }
                      placeholder="한 줄에 사실 하나"
                    />
                  </label>
                  <label>
                    추가로 물어볼 항목
                    <textarea
                      value={listText(item.questions_required)}
                      onChange={(event) =>
                        updateItem(item.document_type, {
                          questions_required: textList(event.target.value),
                        })
                      }
                      placeholder="한 줄에 질문 하나"
                    />
                  </label>
                  <label>
                    전문가 확인 항목
                    <textarea
                      value={listText(item.professional_review_fields)}
                      onChange={(event) =>
                        updateItem(item.document_type, {
                          professional_review_fields: textList(event.target.value),
                        })
                      }
                      placeholder="점수·판정·계획처럼 전문가가 확인할 항목"
                    />
                  </label>
                  <label>
                    근거 연결
                    <textarea
                      value={listText(item.evidence_refs)}
                      onChange={(event) =>
                        updateItem(item.document_type, {
                          evidence_refs: textList(event.target.value),
                        })
                      }
                      placeholder="대화·관찰·기존 문서의 비식별 참조"
                    />
                  </label>
                </div>
                <footer>
                  <small>
                    {item.updated_at
                      ? `마지막 저장 ${new Date(item.updated_at).toLocaleString("ko-KR")}`
                      : "아직 저장되지 않음"}
                  </small>
                  <button
                    type="button"
                    className="button-primary"
                    disabled={savingType === item.document_type}
                    onClick={() => void saveItem(item)}
                  >
                    {savingType === item.document_type ? "저장 중…" : "상태 저장"}
                  </button>
                </footer>
              </details>
            ))}
          </div>
          </details>
        </>
      ) : null}
      {error ? <p className="form-error" role="alert">{error}</p> : null}
    </section>
  );
}
