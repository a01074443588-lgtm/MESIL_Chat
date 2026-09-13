"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { apiFetch } from "../api";
import type { Message, RoomMessageSearch } from "../types";
import { AttachmentDisplay } from "./AttachmentDisplay";

type FileFilter = "all" | "image" | "audio" | "file";
type FileSort = "newest" | "oldest";

function matchesFilter(message: Message, filter: FileFilter) {
  if (filter === "all") return message.attachments.length > 0;
  return message.attachments.some((attachment) => {
    if (filter === "image") return attachment.mime_type.startsWith("image/");
    if (filter === "audio") return attachment.mime_type.startsWith("audio/");
    return (
      !attachment.mime_type.startsWith("image/") &&
      !attachment.mime_type.startsWith("audio/")
    );
  });
}

function filteredAttachments(message: Message, filter: FileFilter) {
  if (filter === "all") return message.attachments;
  return message.attachments.filter((attachment) => {
    if (filter === "image") return attachment.mime_type.startsWith("image/");
    if (filter === "audio") return attachment.mime_type.startsWith("audio/");
    return (
      !attachment.mime_type.startsWith("image/") &&
      !attachment.mime_type.startsWith("audio/")
    );
  });
}

export function RoomFilesOverlay({
  roomId,
  roomName,
  onOpenMessage,
  onClose,
}: {
  roomId: string;
  roomName: string;
  onOpenMessage: (messageId: string) => void;
  onClose: () => void;
}) {
  const [result, setResult] = useState<RoomMessageSearch | null>(null);
  const [filter, setFilter] = useState<FileFilter>("all");
  const [sort, setSort] = useState<FileSort>("newest");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");

  const loadFiles = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      const params = new URLSearchParams({ limit: "200" });
      if (dateFrom) params.set("date_from", dateFrom);
      if (dateTo) params.set("date_to", dateTo);
      setResult(
        await apiFetch<RoomMessageSearch>(
          `/api/rooms/${roomId}/message-search?${params.toString()}`,
        ),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "파일을 불러오지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }, [dateFrom, dateTo, roomId]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadFiles(), 0);
    return () => window.clearTimeout(timer);
  }, [loadFiles]);

  const messages = useMemo(() => {
    const items = (result?.messages ?? []).filter((message) =>
      matchesFilter(message, filter),
    );
    return items.sort((left, right) => {
      const delta =
        new Date(left.created_at).getTime() - new Date(right.created_at).getTime();
      return sort === "oldest" ? delta : -delta;
    });
  }, [filter, result?.messages, sort]);

  const attachmentCount = messages.reduce(
    (total, message) => total + filteredAttachments(message, filter).length,
    0,
  );

  return (
    <div className="detail-layer" role="dialog" aria-modal="true" aria-label="대화방 파일">
      <button className="detail-backdrop" onClick={onClose} aria-label="파일 창 닫기" />
      <section className="room-files-card desktop-resizable-dialog">
        <header className="detail-header">
          <div>
            <span className="eyebrow">파일 모아보기</span>
            <h2>{roomName}</h2>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="닫기">
            ×
          </button>
        </header>

        <div className="room-files-toolbar">
          <div className="room-files-filters" role="group" aria-label="파일 종류">
            {([
              ["all", "전체"],
              ["image", "사진"],
              ["audio", "음성"],
              ["file", "문서·파일"],
            ] as const).map(([value, label]) => (
              <button
                type="button"
                key={value}
                className={filter === value ? "active" : ""}
                aria-pressed={filter === value}
                onClick={() => setFilter(value)}
              >
                {label}
              </button>
            ))}
          </div>
          <label>
            정렬
            <select value={sort} onChange={(event) => setSort(event.target.value as FileSort)}>
              <option value="newest">최신순</option>
              <option value="oldest">오래된순</option>
            </select>
          </label>
          <div className="room-files-dates" aria-label="날짜별 조회">
            <label>
              시작일
              <input
                type="date"
                value={dateFrom}
                max={dateTo || undefined}
                onChange={(event) => setDateFrom(event.target.value)}
              />
            </label>
            <label>
              종료일
              <input
                type="date"
                value={dateTo}
                min={dateFrom || undefined}
                onChange={(event) => setDateTo(event.target.value)}
              />
            </label>
            {dateFrom || dateTo ? (
              <button
                type="button"
                className="text-button"
                onClick={() => {
                  setDateFrom("");
                  setDateTo("");
                }}
              >
                날짜 지우기
              </button>
            ) : null}
          </div>
        </div>

        <div className="room-files-scroll">
          {busy ? <p className="inline-status">파일을 불러오는 중입니다.</p> : null}
          {error ? (
            <div className="inline-error" role="alert">
              <span>{error}</span>
              <button type="button" className="text-button" onClick={() => void loadFiles()}>
                다시 시도
              </button>
            </div>
          ) : null}
          {!busy && !error ? (
            <div className="room-files-result-heading">
              <strong>{attachmentCount}개</strong>
              {result?.truncated ? <span>최근 200개 대화 범위에서 표시합니다.</span> : null}
            </div>
          ) : null}
          {!busy && !error && messages.length === 0 ? (
            <p className="empty-note">이 종류의 파일이 없습니다.</p>
          ) : null}
          {messages.map((message) => {
            const attachments = filteredAttachments(message, filter);
            return (
              <article className="room-files-message" key={message.id}>
                <header>
                  <div>
                    <strong>{message.sender_name}</strong>
                    <time>{new Date(message.created_at).toLocaleString("ko-KR")}</time>
                  </div>
                  <button
                    type="button"
                    className="text-button"
                    onClick={() => onOpenMessage(message.id)}
                  >
                    대화로 이동
                  </button>
                </header>
                <div className={`room-files-grid ${attachments.length === 1 ? "single" : "multiple"}`}>
                  {attachments.map((attachment) => (
                    <AttachmentDisplay
                      key={attachment.id}
                      attachment={attachment}
                      compact
                      showCompactShare
                      galleryAttachments={attachments}
                      showExtraction={false}
                      showOriginalName={false}
                      canEditExtraction={false}
                    />
                  ))}
                </div>
              </article>
            );
          })}
        </div>
      </section>
    </div>
  );
}
