"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiFetch } from "../api";
import { messageSummary } from "../messagePresentation";
import type {
  Resident,
  RoomMessageSearch,
  RoomSearchSummary,
} from "../types";

function dateValue(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function actionStatusLabel(value: string) {
  if (value === "assigned") return "미확인";
  if (value === "acknowledged") return "확인";
  if (value === "in_progress") return "처리 중";
  if (value === "completed") return "완료";
  if (value === "cancelled") return "취소";
  return "지정";
}

function SummaryEvidenceList({
  evidence,
  onOpenMessage,
}: {
  evidence: RoomSearchSummary["summary_evidence"];
  onOpenMessage: (messageId: string) => void;
}) {
  return (
    <div className="room-search-evidence-list">
      {evidence.map((item) => (
        <button
          type="button"
          key={`${item.message_id}-${item.number}`}
          onClick={() => onOpenMessage(item.message_id)}
        >
          <span>[{item.number}]</span>
          <div>
            <strong>{item.source_label}</strong>
            <p>{item.excerpt}</p>
            <small>{new Date(item.created_at).toLocaleString("ko-KR")}</small>
          </div>
        </button>
      ))}
    </div>
  );
}

export function RoomSearchOverlay({
  roomId,
  roomName,
  residents,
  onOpenMessage,
  onClose,
}: {
  roomId: string;
  roomName: string;
  residents: Resident[];
  onOpenMessage: (messageId: string) => void;
  onClose: () => void;
}) {
  const today = new Date();
  const weekAgo = new Date(today);
  weekAgo.setDate(today.getDate() - 7);
  const [query, setQuery] = useState("");
  const [dateFrom, setDateFrom] = useState(dateValue(weekAgo));
  const [dateTo, setDateTo] = useState(dateValue(today));
  const [residentId, setResidentId] = useState("");
  const [messageType, setMessageType] = useState("");
  const [actionStatus, setActionStatus] = useState("");
  const [result, setResult] = useState<RoomMessageSearch | null>(null);
  const [resultScope, setResultScope] = useState({
    residentId: "",
    dateFrom: "",
    dateTo: "",
    query: "",
    messageType: "",
    actionStatus: "",
  });
  const searchVersion = useRef(0);
  const summaryFlightRef = useRef(false);
  const summaryAbortRef = useRef<AbortController | null>(null);
  const [summary, setSummary] = useState<RoomSearchSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [summaryBusy, setSummaryBusy] = useState(false);
  const [summaryPhase, setSummaryPhase] = useState("");
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [evidenceFilterIds, setEvidenceFilterIds] = useState<string[] | null>(null);
  const [error, setError] = useState("");

  const matchesByMessage = useMemo(() => {
    const grouped = new Map<string, RoomMessageSearch["matches"]>();
    for (const match of result?.matches ?? []) {
      grouped.set(match.message_id, [...(grouped.get(match.message_id) ?? []), match]);
    }
    return grouped;
  }, [result]);

  const search = useCallback(async () => {
    const version = ++searchVersion.current;
    summaryAbortRef.current?.abort();
    summaryAbortRef.current = null;
    summaryFlightRef.current = false;
    setBusy(true);
    setSummaryBusy(false);
    setSummaryPhase("");
    setError("");
    setSummary(null);
    setEvidenceOpen(false);
    setEvidenceFilterIds(null);
    try {
      const params = new URLSearchParams();
      if (query.trim()) params.set("q", query.trim());
      if (dateFrom) params.set("date_from", dateFrom);
      if (dateTo) params.set("date_to", dateTo);
      if (residentId) params.set("resident_id", residentId);
      if (messageType) params.set("message_type", messageType);
      if (actionStatus) params.set("action_status", actionStatus);
      params.set("limit", "200");
      const nextResult = await apiFetch<RoomMessageSearch>(
          `/api/rooms/${roomId}/message-search?${params.toString()}`,
      );
      if (version !== searchVersion.current) return;
      setResult(nextResult);
      setResultScope({
        residentId,
        dateFrom,
        dateTo,
        query: query.trim(),
        messageType,
        actionStatus,
      });
    } catch (reason) {
      if (version !== searchVersion.current) return;
      setResult(null);
      setError(reason instanceof Error ? reason.message : "대화를 검색하지 못했습니다.");
    } finally {
      if (version === searchVersion.current) setBusy(false);
    }
  }, [actionStatus, dateFrom, dateTo, messageType, query, residentId, roomId]);

  useEffect(() => {
    const timer = window.setTimeout(() => void search(), 0);
    return () => window.clearTimeout(timer);
    // 검색창을 처음 열 때만 기본 기간을 조회합니다.
    // 입력·필터 변경 중에는 창 크기와 결과가 흔들리지 않도록 검색 버튼으로만 다시 조회합니다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roomId]);

  useEffect(() => {
    const changed = () => { setSummary(null); setResult(null); void search(); };
    window.addEventListener("mesil-resident-links-changed", changed);
    return () => window.removeEventListener("mesil-resident-links-changed", changed);
  }, [search]);

  useEffect(
    () => () => {
      summaryAbortRef.current?.abort();
    },
    [],
  );

  async function summarize() {
    if (!result?.messages.length || busy || summaryFlightRef.current) return;
    const version = searchVersion.current;
    const controller = new AbortController();
    summaryAbortRef.current = controller;
    summaryFlightRef.current = true;
    setSummaryBusy(true);
    setSummaryPhase("검색 기록을 정리하고 있습니다.");
    setEvidenceOpen(false);
    setEvidenceFilterIds(null);
    setError("");
    const phaseTimers = [
      window.setTimeout(
        () => setSummaryPhase("중요한 변화와 경과를 찾고 있습니다."),
        700,
      ),
      window.setTimeout(
        () => setSummaryPhase("요약과 근거를 확인하고 있습니다."),
        1800,
      ),
    ];
    try {
      const nextSummary = await apiFetch<RoomSearchSummary>(
        `/api/rooms/${roomId}/message-search/summary`,
        {
          method: "POST",
          signal: controller.signal,
          body: JSON.stringify({
            message_ids: result.messages.map((message) => message.id),
            resident_id: resultScope.residentId || null,
            date_from: resultScope.dateFrom || null,
            date_to: resultScope.dateTo || null,
            query: resultScope.query || null,
            message_type: resultScope.messageType || null,
            action_status: resultScope.actionStatus || null,
          }),
        },
      );
      if (version !== searchVersion.current) return;
      setSummary(nextSummary);
    } catch (reason) {
      if (version !== searchVersion.current) return;
      if (reason instanceof Error && reason.name === "AbortError") return;
      setError(reason instanceof Error ? reason.message : "검색 결과를 요약하지 못했습니다.");
    } finally {
      phaseTimers.forEach((timer) => window.clearTimeout(timer));
      if (summaryAbortRef.current === controller) {
        summaryAbortRef.current = null;
        summaryFlightRef.current = false;
        setSummaryBusy(false);
        setSummaryPhase("");
      }
    }
  }

  function closeOverlay() {
    summaryAbortRef.current?.abort();
    onClose();
  }

  return (
    <div className="detail-layer" role="dialog" aria-modal="true" aria-label="대화 검색">
      <button className="detail-backdrop" onClick={closeOverlay} aria-label="검색 닫기" />
      <section className="room-search-card desktop-resizable-dialog">
        <header className="detail-header">
          <div>
            <span className="eyebrow">대화 검색</span>
            <h2>{roomName}</h2>
          </div>
          <button className="icon-button" onClick={closeOverlay} aria-label="닫기">
            ×
          </button>
        </header>
        <div className="room-search-scroll">
          <section className="room-search-filters">
            <label className="room-search-keyword">
              찾을 말
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="예: 낙상, 보호자, 복약"
                onKeyDown={(event) => {
                  if (event.key === "Enter") void search();
                }}
              />
            </label>
            <label>
              시작일
              <input
                type="date"
                value={dateFrom}
                onChange={(event) => setDateFrom(event.target.value)}
              />
            </label>
            <label>
              종료일
              <input
                type="date"
                value={dateTo}
                onChange={(event) => setDateTo(event.target.value)}
              />
            </label>
            <label>
              어르신
              <select
                value={residentId}
                onChange={(event) => setResidentId(event.target.value)}
              >
                <option value="">전체 어르신</option>
                {residents.map((resident) => (
                  <option key={resident.id} value={resident.id}>
                    {resident.display_name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              대화 종류
              <select
                value={messageType}
                onChange={(event) => setMessageType(event.target.value)}
              >
                <option value="">전체</option>
                <option value="chat">일반 대화</option>
                <option value="notice">공지</option>
                <option value="handover">인수인계</option>
                <option value="work_request">업무 요청</option>
              </select>
            </label>
            <label>
              업무 상태
              <select
                value={actionStatus}
                onChange={(event) => setActionStatus(event.target.value)}
              >
                <option value="">전체</option>
                <option value="none">업무 지정 없음</option>
                <option value="assigned">미확인</option>
                <option value="acknowledged">확인</option>
                <option value="in_progress">처리 중</option>
                <option value="completed">완료</option>
              </select>
            </label>
            <button
              type="button"
              className="button button-primary"
              disabled={busy}
              onClick={() => void search()}
            >
              {busy ? "찾는 중…" : "검색"}
            </button>
          </section>

          {error ? <p className="form-error">{error}</p> : null}
          {result ? (
            <>
              <div className="room-search-result-heading">
                <strong>{result.matched_count}건 찾음</strong>
                <div className="room-search-summary-controls">

                  <button
                    type="button"
                    className="button button-secondary"
                    disabled={busy || summaryBusy || result.messages.length === 0}
                    onClick={() => void summarize()}
                  >
                    {summaryBusy ? "AI 요약 확인 중…" : "검색 결과 AI 요약"}
                  </button>
                </div>
              </div>
              <p className="room-search-provider-note">중앙 설정의 로컬 AI로 요약합니다.</p>
              {summaryBusy ? (
                <div className="room-search-summary-progress" role="status" aria-live="polite">
                  <span aria-hidden="true" />
                  <strong>{summaryPhase}</strong>
                </div>
              ) : null}
              {result.truncated ? (
                <p className="muted-box">
                  검색량이 많아 최근 5,000건을 확인했습니다. 기간을 좁히면 더 정확하게
                  찾을 수 있습니다.
                </p>
              ) : null}
              {summary ? (
                <section className="room-search-summary">
                  <div className="room-search-summary-title">
                    <div>
                      <strong>
                        {summary.display_mode === "overview"
                          ? "전체 흐름 요약"
                          : "상세 경과 요약"}
                      </strong>
                      <span>
                        {summary.generation_verified ? "AI 검색 요약" : "기록 기반 요약"}
                      </span>
                      {summary.generation_verified && summary.generator ? (
                        <small>사용 모델 {summary.generator}</small>
                      ) : null}
                      <small>
                        {summary.cache_hit
                          ? `저장된 요약 · ${(summary.request_elapsed_ms / 1000).toFixed(1)}초`
                          : `AI 처리 ${(summary.ai_elapsed_ms / 1000).toFixed(1)}초 · 전체 ${(summary.request_elapsed_ms / 1000).toFixed(1)}초`}
                      </small>
                    </div>
                    <span>근거를 누르면 원래 대화를 볼 수 있습니다.</span>
                  </div>
                  <ol className="room-search-summary-sentences">
                    {summary.summary_sentences.map((sentence, index) => (
                      <li key={`${sentence.summary_type}-${index}`}>
                        <p>{sentence.text}</p>
                        {sentence.unconfirmed_part ? (
                          <small>{sentence.unconfirmed_part}</small>
                        ) : null}
                        {sentence.evidence_ids.length ? (
                          <button
                            type="button"
                            onClick={() => {
                              setEvidenceFilterIds(sentence.evidence_ids);
                              setEvidenceOpen(true);
                            }}
                          >
                            근거 {sentence.evidence_ids.length}건
                          </button>
                        ) : null}
                      </li>
                    ))}
                  </ol>
                  {summary.summary_evidence.length ? (
                    <section className="room-search-final-evidence">
                      <button
                        type="button"
                        className="button button-secondary"
                        onClick={() => {
                          if (evidenceOpen && evidenceFilterIds === null) {
                            setEvidenceOpen(false);
                            return;
                          }
                          setEvidenceFilterIds(null);
                          setEvidenceOpen(true);
                        }}
                        aria-expanded={evidenceOpen}
                      >
                        {evidenceOpen && evidenceFilterIds === null ? (
                          "근거 기록 접기"
                        ) : (
                          <>근거 기록 {summary.summary_evidence.length}건 보기</>
                        )}
                      </button>
                      {evidenceOpen ? (
                        <SummaryEvidenceList
                          evidence={
                            evidenceFilterIds === null
                              ? summary.summary_evidence
                              : summary.summary_evidence.filter((item) =>
                                  evidenceFilterIds.includes(item.message_id),
                                )
                          }
                          onOpenMessage={onOpenMessage}
                        />
                      ) : null}
                    </section>
                  ) : null}
                  {summary.provider_notice ? <small>{summary.provider_notice}</small> : null}
                  {summary.fallback_reason ? (
                    <small className="room-search-fallback">
                      {summary.fallback_reason}{" "}
                      <button type="button" disabled={summaryBusy} onClick={() => void summarize()}>
                        AI 요약 다시 시도
                      </button>
                    </small>
                  ) : null}
                </section>
              ) : null}
              <div className="room-search-results">
                {result.messages.length === 0 ? (
                  <p className="muted-box">조건에 맞는 대화가 없습니다.</p>
                ) : (
                  result.messages.map((message, index) => (
                    <button
                      type="button"
                      key={message.id}
                      onClick={() => onOpenMessage(message.id)}
                    >
                      <span>{index + 1}</span>
                      <div>
                        <strong>
                          {message.resident?.display_name ?? "일반 대화"} ·{" "}
                          {message.sender_name}
                        </strong>
                        <p>{messageSummary(message.body, message.attachments)}</p>
                        {(matchesByMessage.get(message.id) ?? []).map((match) => (
                          <span className="room-search-match" key={`${match.source_type}-${match.attachment_id ?? "text"}-${match.excerpt}`}>
                            <b>{match.source_label}</b>
                            {match.attachment_name ? ` · ${match.attachment_name}` : ""}
                            {` · ${match.excerpt}`}
                          </span>
                        ))}
                        <small>
                          {new Date(message.created_at).toLocaleString("ko-KR")}
                          {message.comment_count ? ` · 댓글 ${message.comment_count}` : ""}
                          {message.action_item
                            ? ` · 업무 ${actionStatusLabel(message.action_item.status)}`
                            : ""}
                        </small>
                      </div>
                    </button>
                  ))
                )}
              </div>
            </>
          ) : null}
        </div>
      </section>
    </div>
  );
}
