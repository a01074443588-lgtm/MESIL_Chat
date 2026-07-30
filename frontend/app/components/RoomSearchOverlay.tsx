"use client";

import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "../api";
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

function summaryGeneratorLabel(value: string) {
  if (value.startsWith("nvidia:")) return "Nemotron AI 요약";
  if (value.startsWith("ollama:")) return "로컬 AI 요약";
  if (value.startsWith("stub:")) return "시험용 AI 요약";
  return "간단 요약";
}

function actionStatusLabel(value: string) {
  if (value === "assigned") return "미확인";
  if (value === "acknowledged") return "확인";
  if (value === "in_progress") return "처리 중";
  if (value === "completed") return "완료";
  if (value === "cancelled") return "취소";
  return "지정";
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
  const [summary, setSummary] = useState<RoomSearchSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [summaryBusy, setSummaryBusy] = useState(false);
  const [error, setError] = useState("");

  const search = useCallback(async () => {
    setBusy(true);
    setError("");
    setSummary(null);
    try {
      const params = new URLSearchParams();
      if (query.trim()) params.set("q", query.trim());
      if (dateFrom) params.set("date_from", dateFrom);
      if (dateTo) params.set("date_to", dateTo);
      if (residentId) params.set("resident_id", residentId);
      if (messageType) params.set("message_type", messageType);
      if (actionStatus) params.set("action_status", actionStatus);
      params.set("limit", "200");
      setResult(
        await apiFetch<RoomMessageSearch>(
          `/api/rooms/${roomId}/message-search?${params.toString()}`,
        ),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "대화를 검색하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }, [actionStatus, dateFrom, dateTo, messageType, query, residentId, roomId]);

  useEffect(() => {
    const timer = window.setTimeout(() => void search(), 0);
    return () => window.clearTimeout(timer);
    // 검색창을 처음 열 때만 기본 기간을 조회합니다.
    // 입력·필터 변경 중에는 창 크기와 결과가 흔들리지 않도록 검색 버튼으로만 다시 조회합니다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roomId]);

  async function summarize() {
    if (!result?.messages.length) return;
    setSummaryBusy(true);
    setError("");
    try {
      setSummary(
        await apiFetch<RoomSearchSummary>(
          `/api/rooms/${roomId}/message-search/summary`,
          {
            method: "POST",
            body: JSON.stringify({
              message_ids: result.messages.map((message) => message.id),
            }),
          },
        ),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "검색 결과를 요약하지 못했습니다.");
    } finally {
      setSummaryBusy(false);
    }
  }

  return (
    <div className="detail-layer" role="dialog" aria-modal="true" aria-label="대화 검색">
      <button className="detail-backdrop" onClick={onClose} aria-label="검색 닫기" />
      <section className="room-search-card">
        <header className="detail-header">
          <div>
            <span className="eyebrow">대화 검색</span>
            <h2>{roomName}</h2>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="닫기">
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
                <button
                  type="button"
                  className="button button-secondary"
                  disabled={summaryBusy || result.messages.length === 0}
                  onClick={() => void summarize()}
                >
                  {summaryBusy ? "AI가 정리 중…" : "검색 결과 AI 요약"}
                </button>
              </div>
              {result.truncated ? (
                <p className="muted-box">
                  검색량이 많아 최근 5,000건을 확인했습니다. 기간을 좁히면 더 정확하게
                  찾을 수 있습니다.
                </p>
              ) : null}
              {summary ? (
                <section className="room-search-summary">
                  <div>
                    <strong>{summaryGeneratorLabel(summary.generator)}</strong>
                    <span>아래 번호를 누르면 근거 대화를 볼 수 있습니다.</span>
                  </div>
                  <pre>{summary.summary}</pre>
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
                        <p>{message.body}</p>
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
