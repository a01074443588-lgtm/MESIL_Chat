"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { apiFetch } from "../api";
import { defaultAssessmentChatPeriod } from "../assessmentPeriod.mjs";
import type {
  AssessmentChatEvidencePreview,
  NewAdmissionDraftRecord,
  Resident,
} from "../types";

type Reason =
  | "new_admission"
  | "periodic_reassessment"
  | "state_change_reassessment"
  | "staff_review";

type SelectedMaterial = {
  file: File;
  documentKinds: string[];
};

type PeriodSource = "previous_basis" | "six_month_fallback" | "manual";

const newAdmissionKinds = [
  ["care_grade_certificate", "장기요양인정서"],
  ["individual_long_term_care_plan", "개인별장기요양이용계획서"],
  ["prescription", "처방전·복약안내"],
  ["health_submission", "건강 제출자료"],
  ["transfer_document", "전원 관련 서류"],
  ["welfare_equipment", "복지용구 자료"],
  ["consultation_log", "상담일지"],
  ["administrative_guide", "안내·행정자료"],
  ["other", "기타 제출 자료"],
] as const;

const reassessmentKinds = [
  ["previous_fall_assessment", "이전 낙상위험도"],
  ["previous_pressure_ulcer_assessment", "이전 욕창위험도"],
  ["previous_cognitive_assessment", "이전 인지기능검사"],
  ["previous_needs_assessment", "이전 욕구사정"],
  ["previous_care_plan", "이전 장기요양급여 제공계획"],
  ["previous_care_plan_evaluation", "이전 급여제공계획 결과평가"],
  ["health_submission", "건강 제출자료"],
  ["transfer_document", "전원 관련 서류"],
  ["welfare_equipment", "복지용구 자료"],
  ["consultation_log", "상담일지"],
  ["administrative_guide", "안내·행정자료"],
  ["other", "기타 제출 자료"],
] as const;

function localDate() {
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60_000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 10);
}

export function AssessmentDraftIntakeWorkspace({
  resident,
  previousBasisDates,
  onCreated,
}: {
  resident: Resident;
  previousBasisDates: string[];
  onCreated: (draft: NewAdmissionDraftRecord) => void;
}) {
  const initialDate = localDate();
  const initialPeriod = defaultAssessmentChatPeriod({
    endDate: initialDate,
    previousBasisDates,
  });
  const [reason, setReason] = useState<Reason>("new_admission");
  const [assessmentDate, setAssessmentDate] = useState(initialDate);
  const [periodStart, setPeriodStart] = useState(initialPeriod.startDate);
  const [periodEnd, setPeriodEnd] = useState(initialPeriod.endDate);
  const [periodSource, setPeriodSource] = useState<PeriodSource>(initialPeriod.source);
  const [materials, setMaterials] = useState<SelectedMaterial[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [chatPreview, setChatPreview] = useState<AssessmentChatEvidencePreview | null>(null);
  const [chatPreviewLoading, setChatPreviewLoading] = useState(false);
  const [chatPreviewError, setChatPreviewError] = useState("");
  const [excludedChatSourceRefs, setExcludedChatSourceRefs] = useState<Set<string>>(
    () => new Set(),
  );
  const fileInputRef = useRef<HTMLInputElement>(null);
  const kindOptions = useMemo(
    () => (reason === "new_admission" ? newAdmissionKinds : reassessmentKinds),
    [reason],
  );

  useEffect(() => {
    if (!assessmentDate || (reason !== "new_admission" && (!periodStart || !periodEnd))) {
      const resetTimer = window.setTimeout(() => {
        setChatPreview(null);
        setChatPreviewError("");
        setExcludedChatSourceRefs(new Set());
      }, 0);
      return () => window.clearTimeout(resetTimer);
    }
    const controller = new AbortController();
    const query = new URLSearchParams({ reason, assessment_date: assessmentDate });
    if (periodStart) query.set("period_start", periodStart);
    if (periodEnd) query.set("period_end", periodEnd);
    const requestTimer = window.setTimeout(() => {
      setChatPreviewLoading(true);
      setChatPreviewError("");
      apiFetch<AssessmentChatEvidencePreview>(
        `/api/workdesk/residents/${resident.id}/assessment-chat-evidence/preview?${query}`,
        { signal: controller.signal },
      )
        .then((payload) => {
          setChatPreview(payload);
          const available = new Set(payload.items.map((item) => item.source_ref));
          setExcludedChatSourceRefs((current) =>
            new Set([...current].filter((sourceRef) => available.has(sourceRef))),
          );
        })
        .catch((previewError) => {
          if (controller.signal.aborted) return;
          setChatPreview(null);
          setChatPreviewError(
            previewError instanceof Error
              ? previewError.message
              : "매실챗 확인 기록을 불러오지 못했습니다.",
          );
        })
        .finally(() => {
          if (!controller.signal.aborted) setChatPreviewLoading(false);
        });
    }, 0);
    return () => {
      window.clearTimeout(requestTimer);
      controller.abort();
    };
  }, [assessmentDate, periodEnd, periodStart, reason, resident.id]);

  function applyDefaultAssessmentPeriod(endDate: string) {
    if (!endDate) {
      setPeriodStart("");
      setPeriodEnd("");
      setPeriodSource("manual");
      return;
    }
    const next = defaultAssessmentChatPeriod({ endDate, previousBasisDates });
    setPeriodStart(next.startDate);
    setPeriodEnd(next.endDate);
    setPeriodSource(next.source);
  }

  function addFiles(files: FileList | null) {
    if (!files?.length) return;
    const added = Array.from(files).map((file) => ({
      file,
      documentKinds: [],
    }));
    setMaterials((current) => [...current, ...added].slice(0, 20));
    setError("");
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  async function submit() {
    setError("");
    setStatus("");
    if (!materials.length) {
      setError("분석할 PDF·이미지 자료를 한 개 이상 선택해 주세요.");
      return;
    }
    if (reason !== "new_admission" && (!periodStart || !periodEnd)) {
      setError("재평가에서 확인할 매실챗 기간을 입력해 주세요.");
      return;
    }
    const body = new FormData();
    body.append("reason", reason);
    body.append("assessment_date", assessmentDate);
    if (periodStart) body.append("period_start", periodStart);
    if (periodEnd) body.append("period_end", periodEnd);
    excludedChatSourceRefs.forEach((sourceRef) =>
      body.append("excluded_chat_source_refs", sourceRef),
    );
    materials.forEach((item) => {
      body.append(
        "document_kinds",
        item.documentKinds.length ? item.documentKinds.join(",") : "auto",
      );
      body.append("files", item.file);
    });
    setSubmitting(true);
    setStatus("PDF 각 페이지와 이미지를 로컬에서 읽고 자료 종류를 분류하고 있습니다.");
    try {
      const draft = await apiFetch<NewAdmissionDraftRecord>(
        `/api/workdesk/residents/${resident.id}/assessment-drafts/analyze`,
        { method: "POST", body },
      );
      setStatus("제출자료와 누적 돌봄기록을 반영한 편집용 초안을 저장했습니다.");
      setMaterials([]);
      onCreated(draft);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "자료를 분석하지 못했습니다.");
      setStatus("");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="assessment-intake" aria-label="기초자료 제출과 양식 초안 만들기">
      <div className="assessment-intake-grid">
        <label>
          <strong>1. 어르신과 작성 사유</strong>
          <span className="assessment-resident">{resident.display_name}</span>
          <select
            value={reason}
            onChange={(event) => {
              const nextReason = event.target.value as Reason;
              setReason(nextReason);
              setMaterials([]);
              if (nextReason !== "new_admission") {
                applyDefaultAssessmentPeriod(assessmentDate);
              }
            }}
          >
            <option value="new_admission">신규입소</option>
            <option value="periodic_reassessment">정기 재평가</option>
            <option value="state_change_reassessment">상태변화</option>
            <option value="staff_review">직원 재검토</option>
          </select>
          <input
            aria-label="평가 기준일"
            type="date"
            value={assessmentDate}
            onChange={(event) => {
              const nextDate = event.target.value;
              setAssessmentDate(nextDate);
              if (reason !== "new_admission" && periodSource !== "manual") {
                applyDefaultAssessmentPeriod(nextDate);
              }
            }}
          />
        </label>
        <div>
          <strong>2. 기초자료 제출</strong>
          <p>
            {reason === "new_admission"
              ? "인정서와 개인별장기요양이용계획서를 기본으로, 처방전·입소 전 건강검진 등 현재 가진 자료를 함께 제출합니다."
              : "직전 기초사정 4종과 직전 급여제공계획을 기준자료로 사용하고, 가능한 경우 결과평가도 함께 제출합니다. 기준일 이후 해당 어르신의 매실챗 확인 기록은 자동 연결합니다."}
          </p>
          <p className="assessment-auto-help">
            한 PDF에 여러 서류가 있으면 페이지별로 자동 분류합니다. 자동 분류가 어려운 자료만 종류를 직접 지정해 주세요.
          </p>
          {reason !== "new_admission" ? (
            <div className="assessment-period">
              <label>확인 시작일<input type="date" value={periodStart} onChange={(event) => { setPeriodStart(event.target.value); setPeriodSource("manual"); }} /></label>
              <label>확인 종료일<input type="date" value={periodEnd} onChange={(event) => { setPeriodEnd(event.target.value); setPeriodSource("manual"); }} /></label>
              <p className="assessment-period-source">
                {periodSource === "previous_basis"
                  ? "직전 기초사정·급여계획 기준일 다음 날부터 자동 연결했습니다. 기간은 바꿀 수 있습니다."
                  : periodSource === "six_month_fallback"
                    ? "직전 기준일이 없어 최근 6개월을 기본으로 연결했습니다. 기간은 바꿀 수 있습니다."
                    : "직원이 선택한 기간의 해당 어르신 매실챗 확인 기록을 연결합니다."}
              </p>
            </div>
          ) : null}
          <details className="assessment-chat-preview">
            <summary>
              {chatPreviewLoading
                ? "매실챗 확인 기록을 찾는 중…"
                : chatPreview?.total_count
                  ? `자동 연결할 매실챗 확인 기록 ${chatPreview.total_count - excludedChatSourceRefs.size}건`
                  : "연결된 매실챗 확인 기록 없음"}
            </summary>
            {chatPreviewError ? (
              <p className="form-error" role="alert">{chatPreviewError}</p>
            ) : chatPreview?.items.length ? (
              <>
                <p>이번 초안에 활용할 사건만 선택해 주세요. 선택을 해제해도 원본 대화와 기록은 삭제되지 않습니다.</p>
                <ul>
                  {chatPreview.items.map((item) => (
                    <li key={item.source_ref}>
                      <label>
                        <input
                          type="checkbox"
                          checked={!excludedChatSourceRefs.has(item.source_ref)}
                          onChange={(event) =>
                            setExcludedChatSourceRefs((current) => {
                              const next = new Set(current);
                              if (event.target.checked) next.delete(item.source_ref);
                              else next.add(item.source_ref);
                              return next;
                            })
                          }
                        />
                        <span>{item.reference_locator} · {item.summary}</span>
                      </label>
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <p>이 기간에는 해당 어르신과 연결된 확인 기록이 없습니다. 제출 문서만으로 계속 진행할 수 있습니다.</p>
            )}
          </details>
          <input
            ref={fileInputRef}
            type="file"
            accept="application/pdf,image/jpeg,image/png,.pdf,.jpg,.jpeg,.png"
            multiple
            onChange={(event) => addFiles(event.target.files)}
            aria-label="PDF 또는 이미지 기초자료 선택"
          />
          {materials.length ? (
            <ul className="assessment-material-list">
              {materials.map((item, index) => (
                <li key={`${item.file.name}-${item.file.lastModified}-${index}`}>
                  <span>
                    제출 자료 {index + 1} · {item.file.type.startsWith("image/") ? "이미지" : "PDF"} · {(item.file.size / 1024).toFixed(0)}KB
                  </span>
                  <details className="assessment-kind-picker">
                    <summary>
                      {item.documentKinds.length
                        ? `${item.documentKinds.length}종 직접 선택됨`
                        : "자동 분류(권장)"}
                    </summary>
                    <fieldset aria-label={`제출 자료 ${index + 1} 종류`}>
                      <legend>이 파일에 들어 있는 자료</legend>
                      {kindOptions.map(([value, label]) => (
                        <label key={value}>
                          <input
                            type="checkbox"
                            checked={item.documentKinds.includes(value)}
                            onChange={(event) =>
                              setMaterials((current) =>
                                current.map((entry, entryIndex) => {
                                  if (entryIndex !== index) return entry;
                                  const documentKinds = event.target.checked
                                    ? [...entry.documentKinds, value]
                                    : entry.documentKinds.filter((kind) => kind !== value);
                                  return { ...entry, documentKinds };
                                }),
                              )
                            }
                          />
                          {label}
                        </label>
                      ))}
                      {item.documentKinds.length ? (
                        <button
                          type="button"
                          onClick={() =>
                            setMaterials((current) =>
                              current.map((entry, entryIndex) =>
                                entryIndex === index ? { ...entry, documentKinds: [] } : entry,
                              ),
                            )
                          }
                        >
                          자동 분류로 되돌리기
                        </button>
                      ) : (
                        <p>현재는 파일 전체를 자동으로 검토합니다.</p>
                      )}
                    </fieldset>
                  </details>
                  <button type="button" onClick={() => setMaterials((current) => current.filter((_, entryIndex) => entryIndex !== index))}>제외</button>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
        <div>
          <strong>3. 분석하고 검토 시작</strong>
          <p>확인된 내용만 양식 칸에 반영하고, 확인할 수 없는 양식 칸은 비워 두어 직원이 직접 보완합니다. 충돌값은 자동 선택하지 않습니다.</p>
          <button type="button" className="primary" disabled={submitting} onClick={submit}>
            {submitting ? "분석 중…" : "자료 분석하고 편집용 초안 열기"}
          </button>
        </div>
      </div>
      {status ? <p className="form-success" role="status">{status}</p> : null}
      {error ? <p className="form-error" role="alert">{error}</p> : null}
    </section>
  );
}
