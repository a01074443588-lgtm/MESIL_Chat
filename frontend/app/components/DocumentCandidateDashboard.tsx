"use client";

import { useEffect, useMemo, useState } from "react";
import { apiFetch } from "../api";
import type {
  DocumentCandidateDashboardData,
  RecordClassification,
  RiskLevel,
  WorkItem,
} from "../types";
import { AttachmentDisplay } from "./AttachmentDisplay";

const documentLabels: Record<string, string> = {
  care_service_record: "급여제공기록지",
  integrated_assessment: "통합사정",
  nursing_log: "간호일지",
  care_plan: "급여제공계획",
  care_plan_evaluation: "급여제공결과평가",
  consultation_log: "상담일지",
};

const riskLabels: Record<RiskLevel, string> = {
  low: "낮음",
  medium: "관찰 필요",
  high: "높음",
  urgent: "긴급",
};

const classificationLabels: Record<RecordClassification, string> = {
  daily_care: "일상생활 지원",
  nutrition: "식사·영양",
  health: "건강·간호",
  safety: "안전·사고",
  consultation: "상담·보호자",
  rehabilitation: "재활·치료",
};

const emptyDashboard: DocumentCandidateDashboardData = {
  total_count: 0,
  filtered_count: 0,
  document_counts: {},
  risk_counts: {},
  classification_counts: {},
  items: [],
};

export function DocumentCandidateDashboard() {
  const [dashboard, setDashboard] =
    useState<DocumentCandidateDashboardData>(emptyDashboard);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [documentType, setDocumentType] = useState("");
  const [riskLevel, setRiskLevel] = useState("");
  const [classification, setClassification] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const selected = useMemo(
    () => dashboard.items.find((item) => item.id === selectedId) ?? null,
    [dashboard.items, selectedId],
  );

  useEffect(() => {
    const query = new URLSearchParams();
    if (documentType) query.set("document_type", documentType);
    if (riskLevel) query.set("risk_level", riskLevel);
    if (classification) query.set("classification", classification);
    apiFetch<DocumentCandidateDashboardData>(
      `/api/document-candidates${query.size ? `?${query.toString()}` : ""}`,
    )
      .then((payload) => {
        setError("");
        setDashboard(payload);
        setSelectedId((current) =>
          payload.items.some((item) => item.id === current)
            ? current
            : (payload.items[0]?.id ?? null),
        );
      })
      .catch((reason) => {
        setError(
          reason instanceof Error ? reason.message : "서류 후보를 불러오지 못했습니다.",
        );
      })
      .finally(() => setLoading(false));
  }, [classification, documentType, riskLevel]);

  function renderCandidate(item: WorkItem) {
    const confirmed = item.confirmed_record;
    if (!confirmed) return null;
    return (
      <button
        key={item.id}
        className={selectedId === item.id ? "selected" : ""}
        onClick={() => setSelectedId(item.id)}
      >
        <div className="candidate-card-top">
          <span className={`risk-badge risk-${confirmed.risk_level}`}>
            {riskLabels[confirmed.risk_level]}
          </span>
          <small>{new Date(item.confirmed_at ?? item.updated_at).toLocaleDateString("ko-KR")}</small>
        </div>
        <strong>{item.resident.display_name}</strong>
        <span>{confirmed.summary}</span>
        <div className="candidate-doc-tags">
          {confirmed.document_types.map((type) => (
            <em key={type}>{documentLabels[type] ?? type}</em>
          ))}
        </div>
      </button>
    );
  }

  return (
    <div className="candidate-dashboard">
      <section className="candidate-summary" aria-label="서류 후보 현황">
        <article>
          <span>확정 후보</span>
          <strong>{dashboard.total_count}</strong>
        </article>
        <article>
          <span>현재 조건</span>
          <strong>{dashboard.filtered_count}</strong>
        </article>
        <article className="risk-summary">
          <span>높음·긴급</span>
          <strong>
            {(dashboard.risk_counts.high ?? 0) + (dashboard.risk_counts.urgent ?? 0)}
          </strong>
        </article>
      </section>

      <section className="candidate-filters" aria-label="서류 후보 필터">
        <label>
          서류 종류
          <select
            value={documentType}
            onChange={(event) => setDocumentType(event.target.value)}
          >
            <option value="">전체 서류</option>
            {Object.entries(documentLabels).map(([value, label]) => (
              <option key={value} value={value}>
                {label} ({dashboard.document_counts[value] ?? 0})
              </option>
            ))}
          </select>
        </label>
        <label>
          업무 분류
          <select
            value={classification}
            onChange={(event) => setClassification(event.target.value)}
          >
            <option value="">전체 분류</option>
            {Object.entries(classificationLabels).map(([value, label]) => (
              <option key={value} value={value}>
                {label} ({dashboard.classification_counts[value] ?? 0})
              </option>
            ))}
          </select>
        </label>
        <label>
          위험도
          <select
            value={riskLevel}
            onChange={(event) => setRiskLevel(event.target.value)}
          >
            <option value="">전체 위험도</option>
            {Object.entries(riskLabels).map(([value, label]) => (
              <option key={value} value={value}>
                {label} ({dashboard.risk_counts[value] ?? 0})
              </option>
            ))}
          </select>
        </label>
      </section>

      {error ? <p className="form-error candidate-error">{error}</p> : null}
      <div className="candidate-dashboard-body">
        <nav className="candidate-list" aria-label="확정된 서류 후보">
          {loading ? (
            <p className="muted-box">서류 후보를 불러오는 중입니다.</p>
          ) : dashboard.items.length === 0 ? (
            <p className="muted-box">선택한 조건에 맞는 확정 후보가 없습니다.</p>
          ) : (
            dashboard.items.map(renderCandidate)
          )}
        </nav>

        {selected?.confirmed_record ? (
          <article className="candidate-detail">
            <header>
              <div>
                <span className="resident-chip">{selected.resident.display_name}</span>
                <h3>{selected.confirmed_record.summary}</h3>
              </div>
              <span
                className={`risk-badge risk-${selected.confirmed_record.risk_level}`}
              >
                {riskLabels[selected.confirmed_record.risk_level]}
              </span>
            </header>

            <section className="candidate-confirmed">
              <div className="candidate-field-row">
                <div>
                  <span>업무 분류</span>
                  <strong>
                    {classificationLabels[selected.confirmed_record.classification]}
                  </strong>
                </div>
                <div>
                  <span>확정 담당자</span>
                  <strong>{selected.confirmed_by_name}</strong>
                </div>
              </div>
              <div>
                <span>검토문</span>
                <p>{selected.confirmed_record.corrected_text}</p>
              </div>
              {selected.confirmed_record.reviewer_notes ? (
                <div>
                  <span>검토 메모</span>
                  <p>{selected.confirmed_record.reviewer_notes}</p>
                </div>
              ) : null}
              <div className="candidate-doc-tags large">
                {selected.confirmed_record.document_types.map((type) => (
                  <em key={type}>{documentLabels[type] ?? type}</em>
                ))}
              </div>
            </section>

            <section className="candidate-source">
              <div>
                <strong>채팅 원문</strong>
                <small>
                  {selected.source_snapshot.room_name} ·{" "}
                  {selected.source_snapshot.sender_name}
                </small>
              </div>
              <p>{selected.source_snapshot.body}</p>
              {selected.message.attachments.length > 0 ? (
                <div className="work-attachments">
                  {selected.message.attachments.map((attachment) => (
                    <AttachmentDisplay
                      key={attachment.id}
                      attachment={attachment}
                    />
                  ))}
                </div>
              ) : null}
            </section>

            <footer>
              읽기 전용 후보입니다. 아직 공식 서류 또는 SMCODI 기록으로 전송되지
              않았습니다.
            </footer>
          </article>
        ) : (
          <div className="workdesk-empty">왼쪽에서 서류 후보를 선택하세요.</div>
        )}
      </div>
    </div>
  );
}
