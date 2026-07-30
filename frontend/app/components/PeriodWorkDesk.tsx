"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiFetch } from "../api";
import type {
  CareBriefingCard,
  PeriodRecordEvent,
  PeriodRecordSummary,
  PeriodRecordSummarySelection,
  PeriodWorkdeskReview,
  RecordUsageTag,
  Resident,
  Room,
} from "../types";
import { AttachmentDisplay } from "./AttachmentDisplay";

const messageNatureLabels: Record<string, string> = {
  chat: "일반 대화",
  notice: "공지",
  handover: "인수인계",
  work_request: "업무협조",
  report: "보고",
};

const briefingPriorityLabels = {
  first: "주의해서 보기",
  check: "확인할 변화",
  observe: "경과 기록",
} as const;

const recordUsageLabels: Record<RecordUsageTag, string> = {
  nursing: "간호 기록 후보",
  care_service: "급여제공 기록 후보",
  consultation: "상담 기록 후보",
  program: "프로그램 기록 후보",
  general: "일반 업무",
  needs_review: "확인 필요",
};

const recordUsageDescriptions: Record<RecordUsageTag, string> = {
  nursing: "건강 상태·수치·통증·피부·복약·간호 전달",
  care_service: "식사·수분·위생·이동·배변·정서지원과 반응",
  consultation: "실제 보호자·가족 연락, 통화 또는 면담",
  program: "실제 프로그램·활동 참여와 반응",
  general: "어르신과 직접 연결되지 않은 공지·시설 업무",
  needs_review: "이름·시간·수치·약·신체 부위 등 원본 확인 필요",
};

const recordUsageOrder: RecordUsageTag[] = [
  "nursing",
  "care_service",
  "consultation",
  "program",
  "general",
  "needs_review",
];

function dateInputValue(value: Date) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("ko-KR", {
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function escapePrintText(value: string) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

const recordSummaryHeadings = [
  "한눈에 보기",
  "먼저 확인",
  "이미 한 일",
  "다음 업무 제안",
] as const;

function parseRecordSummary(value: string) {
  const sections = recordSummaryHeadings.map((heading, index) => {
    const marker = `[${heading}]`;
    const start = value.indexOf(marker);
    if (start < 0) return null;
    const contentStart = start + marker.length;
    const nextStarts = recordSummaryHeadings
      .slice(index + 1)
      .map((nextHeading) => value.indexOf(`[${nextHeading}]`, contentStart))
      .filter((nextStart) => nextStart >= 0);
    const end = nextStarts.length ? Math.min(...nextStarts) : value.length;
    return {
      heading,
      content: value.slice(contentStart, end).trim(),
    };
  });
  const overview = sections.find(
    (section) => section?.heading === "한눈에 보기",
  )?.content;
  return {
    overview: overview || value.trim(),
    details: sections.filter(
      (
        section,
      ): section is {
        heading: (typeof recordSummaryHeadings)[number];
        content: string;
      } => Boolean(section && section.heading !== "한눈에 보기"),
    ),
  };
}

function printDocument(
  title: string,
  periodLabel: string,
  sections: string[],
) {
  const frame = document.createElement("iframe");
  frame.title = `${title} 인쇄 준비`;
  frame.setAttribute("aria-hidden", "true");
  Object.assign(frame.style, {
    position: "fixed",
    right: "0",
    bottom: "0",
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
  printPage.write(`<!doctype html>
    <html lang="ko">
      <head>
        <meta charset="utf-8" />
        <title>${escapePrintText(title)}</title>
        <style>
          @page {
            size: A4;
            margin: 16mm 14mm 18mm;
            @bottom-right {
              content: "페이지 " counter(page) " / " counter(pages);
              color: #536773;
              font-family: "Malgun Gothic", "Noto Sans KR", sans-serif;
              font-size: 9px;
            }
          }
          * { box-sizing: border-box; }
          html, body { margin: 0; color: #173447; font-family: "Malgun Gothic", "Noto Sans KR", sans-serif; }
          header { border-bottom: 2px solid #5a7f10; margin-bottom: 12mm; padding-bottom: 4mm; }
          header h1 { font-size: 22px; margin: 0 0 2mm; }
          header p { color: #536773; font-size: 11px; }
          article { break-inside: avoid; border: 1px solid #cbd8d8; border-radius: 8px; margin: 0 0 7mm; padding: 5mm; }
          article.allow-break { break-inside: auto; }
          h2 { font-size: 17px; margin: 0 0 3mm; }
          h3 { font-size: 14px; margin: 4mm 0 2mm; }
          p, li, pre { font-size: 12px; line-height: 1.65; overflow-wrap: anywhere; word-break: keep-all; }
          pre { white-space: pre-wrap; margin: 0; font-family: inherit; }
          ul { margin: 0; padding-left: 5mm; }
          .stamp { display: inline-block; margin: 0 1.5mm 1.5mm 0; padding: 1mm 2mm; border-radius: 999px; background: #e9f3ed; font-size: 10px; }
          .evidence { color: #536773; font-size: 10px; }
        </style>
      </head>
      <body>
        <header>
          <h1>${escapePrintText(title)}</h1>
          <p>기간 ${escapePrintText(periodLabel)} · AI 정리와 원문 근거를 구분하여 확인하세요.</p>
        </header>
        ${sections.join("")}
      </body>
    </html>`);
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

export function PeriodWorkDesk({
  open,
  rooms,
  initialDate,
  onClose,
}: {
  open: boolean;
  rooms: Room[];
  initialDate?: string;
  onClose: () => void;
}) {
  const today = useMemo(() => dateInputValue(new Date()), []);
  const [startDate, setStartDate] = useState(initialDate || today);
  const [endDate, setEndDate] = useState(initialDate || today);
  const [roomId, setRoomId] = useState("");
  const [residentId, setResidentId] = useState("");
  const [keyword, setKeyword] = useState("");
  const [messageType, setMessageType] = useState("");
  const [residents, setResidents] = useState<Resident[]>([]);
  const [review, setReview] = useState<PeriodWorkdeskReview | null>(null);
  const [loading, setLoading] = useState(false);
  const [showOtherBriefing, setShowOtherBriefing] = useState(false);
  const [expandedSourceIds, setExpandedSourceIds] = useState<string[]>([]);
  const [selectedRecordTags, setSelectedRecordTags] = useState<RecordUsageTag[]>([]);
  const [selectedEventIds, setSelectedEventIds] = useState<string[]>([]);
  const [recordSummary, setRecordSummary] = useState<PeriodRecordSummary | null>(
    null,
  );
  const [recordSummaryLoading, setRecordSummaryLoading] = useState(false);
  const [error, setError] = useState("");
  const requestVersionRef = useRef(0);

  const periodLabel =
    startDate === endDate ? startDate : `${startDate} ~ ${endDate}`;
  const parsedRecordSummary = recordSummary
    ? parseRecordSummary(recordSummary.summary)
    : null;

  const loadReview = useCallback(
    async (
      override?: Partial<{
        startDate: string;
        endDate: string;
        roomId: string;
        residentId: string;
        keyword: string;
        messageType: string;
      }>,
    ) => {
      const next = {
        startDate: override?.startDate ?? startDate,
        endDate: override?.endDate ?? endDate,
        roomId: override?.roomId ?? roomId,
        residentId: override?.residentId ?? residentId,
        keyword: override?.keyword ?? keyword,
        messageType: override?.messageType ?? messageType,
      };
      const version = requestVersionRef.current + 1;
      requestVersionRef.current = version;
      setLoading(true);
      setError("");
      try {
        const requestBody = {
          start_date: next.startDate,
          end_date: next.endDate,
          room_id: next.roomId || null,
          resident_id: next.residentId || null,
          keyword: next.keyword || null,
          message_type: next.messageType || null,
        };
        const payload = await apiFetch<PeriodWorkdeskReview>(
          "/api/workdesk/period-review",
          {
            method: "POST",
            body: JSON.stringify({ ...requestBody, enhance_summary: false }),
          },
        );
        if (requestVersionRef.current !== version) return;
        setReview(payload);
        setShowOtherBriefing(false);
        setExpandedSourceIds([]);
        setSelectedRecordTags([]);
        setSelectedEventIds([]);
        setRecordSummary(null);
      } catch (reason) {
        if (requestVersionRef.current !== version) return;
        setError(
          reason instanceof Error
            ? reason.message
            : "돌봄 브리핑을 만들지 못했습니다.",
        );
      } finally {
        if (requestVersionRef.current === version) setLoading(false);
      }
    },
    [endDate, keyword, messageType, residentId, roomId, startDate],
  );

  useEffect(() => {
    if (!open) return;
    let disposed = false;
    apiFetch<Resident[]>("/api/workdesk/residents")
      .then((payload) => {
        if (!disposed) setResidents(payload);
      })
      .catch(() => {
        if (!disposed) setResidents([]);
      });
    const timer = window.setTimeout(() => void loadReview(), 0);
    return () => {
      disposed = true;
      window.clearTimeout(timer);
      requestVersionRef.current += 1;
    };
    // 업무함을 여는 순간 한 번만 현재 선택 기간을 불러옵니다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onEscape);
    return () => window.removeEventListener("keydown", onEscape);
  }, [onClose, open]);

  const sourceById = useMemo(
    () =>
      new Map(
        (review?.sources ?? []).map((source) => [source.message.id, source]),
      ),
    [review],
  );

  const activeEvents = useMemo(
    () =>
      selectedRecordTags.length
        ? (review?.record_events ?? []).filter((event) =>
            event.record_usage_tags.some((tag) =>
              selectedRecordTags.includes(tag),
            ),
          )
        : [],
    [review, selectedRecordTags],
  );

  const attentionCards = useMemo(
    () =>
      (review?.briefing.cards ?? []).filter(
        (card) => card.priority === "first" || card.priority === "check",
      ),
    [review],
  );

  const otherCards = useMemo(
    () =>
      (review?.briefing.cards ?? []).filter(
        (card) => card.priority === "observe",
      ),
    [review],
  );

  function applyQuickRange(kind: "today" | "yesterday" | "week") {
    const end = new Date();
    if (kind === "yesterday") end.setDate(end.getDate() - 1);
    const start = new Date(end);
    if (kind === "week") {
      const day = start.getDay() || 7;
      start.setDate(start.getDate() - day + 1);
    }
    const nextStart = dateInputValue(start);
    const nextEnd = dateInputValue(end);
    setStartDate(nextStart);
    setEndDate(nextEnd);
    void loadReview({ startDate: nextStart, endDate: nextEnd });
  }

  function toggleEvidence(sourceIds: string[]) {
    const isOpen = sourceIds.some((sourceId) =>
      expandedSourceIds.includes(sourceId),
    );
    setExpandedSourceIds((current) =>
      isOpen
        ? current.filter((sourceId) => !sourceIds.includes(sourceId))
        : [...new Set([...current, ...sourceIds])],
    );
  }

  function toggleRecordGroup(tag: RecordUsageTag) {
    setSelectedRecordTags((current) =>
      current.includes(tag)
        ? current.filter((currentTag) => currentTag !== tag)
        : recordUsageOrder.filter(
            (currentTag) => current.includes(currentTag) || currentTag === tag,
          ),
    );
    setSelectedEventIds([]);
    setRecordSummary(null);
    window.requestAnimationFrame(() =>
      document
        .getElementById("record-group-detail")
        ?.scrollIntoView({ behavior: "smooth", block: "start" }),
    );
  }

  function setAllRecordGroups(selected: boolean) {
    setSelectedRecordTags(selected ? [...recordUsageOrder] : []);
    setSelectedEventIds([]);
    setRecordSummary(null);
  }

  function selectedRecordLabel() {
    return selectedRecordTags.map((tag) => recordUsageLabels[tag]).join(" · ");
  }

  function selectedEvents() {
    return activeEvents.filter((event) =>
      selectedEventIds.includes(event.event_group_id),
    );
  }

  function selectedEventSelections(): PeriodRecordSummarySelection[] {
    return selectedEvents().map((event) => ({
      resident_id: event.resident_id,
      evidence_ids: [...new Set(event.evidence_ids)],
    }));
  }

  async function summarizeSelectedEvents() {
    if (!selectedRecordTags.length) return;
    const selections = selectedEventSelections();
    if (!selections.some((selection) => selection.evidence_ids.length > 0)) return;
    setRecordSummaryLoading(true);
    setError("");
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), 55_000);
    try {
      const result = await apiFetch<PeriodRecordSummary>(
        "/api/workdesk/record-summary",
        {
          method: "POST",
          body: JSON.stringify({
            record_usage_tag: selectedRecordTags[0],
            record_usage_tags: selectedRecordTags,
            selections,
          }),
          signal: controller.signal,
        },
      );
      setRecordSummary(result);
    } catch (reason) {
      setError(
        reason instanceof DOMException && reason.name === "AbortError"
          ? "AI 정리가 75초 안에 끝나지 않아 중단했습니다. 잠시 후 다시 시도해 주세요."
          : reason instanceof Error
          ? reason.message
          : "선택한 대화를 AI로 정리하지 못했습니다.",
      );
    } finally {
      window.clearTimeout(timeoutId);
      setRecordSummaryLoading(false);
    }
  }

  function recordSummaryExecutionLabel(summary: PeriodRecordSummary) {
    const elapsed = `${(summary.elapsed_ms / 1000).toFixed(1)}초`;
    if (summary.generator.startsWith("nvidia:")) {
      return `AI 처리: Nemotron Ultra 550B · ${elapsed}`;
    }
    if (summary.generator.startsWith("safe:")) {
      return `AI 응답 지연·검증 실패로 안전 정리 사용 · ${elapsed}`;
    }
    return `AI 처리: ${summary.generator} · ${elapsed}`;
  }

  function eventPrintSection(event: PeriodRecordEvent, index: number) {
    const sources = event.evidence_ids
      .map((id) => sourceById.get(id))
      .filter((source) => source !== undefined);
    return `<article>
      <h2>${index}. ${escapePrintText(event.resident_name ?? "일반 업무")}</h2>
      <p>${escapePrintText(event.summary)}</p>
      <p>${event.record_usage_tags
        .map((tag) => `<span class="stamp">${escapePrintText(recordUsageLabels[tag])}</span>`)
        .join("")}</p>
      ${sources
        .map(
          (source, sourceIndex) => `<h3>근거 ${sourceIndex + 1}</h3>
            <pre>${escapePrintText(source.message.body)}</pre>
            <p class="evidence">${escapePrintText(source.room_name)} · ${escapePrintText(
              source.message.sender_name,
            )} · ${escapePrintText(formatDateTime(source.message.created_at))} · 답글 ${
              source.reply_count
            }개</p>`,
        )
        .join("")}
    </article>`;
  }

  function printBriefing() {
    if (!review) return;
    const sections = review.briefing.cards.map(
      (card, index) => `<article>
        <h2>${index + 1}. ${escapePrintText(card.resident_name)}</h2>
        <p>${card.record_usage_tags
          .map((tag) => `<span class="stamp">${escapePrintText(recordUsageLabels[tag])}</span>`)
          .join("")}</p>
        <h3>무엇이 달라졌나요?</h3><p>${escapePrintText(card.change_summary)}</p>
        <h3>왜 확인해야 하나요?</h3><ul>${card.check_reasons
          .map((value) => `<li>${escapePrintText(value)}</li>`)
          .join("")}</ul>
        <h3>이미 한 일</h3><ul>${card.completed_actions
          .map((value) => `<li>${escapePrintText(value)}</li>`)
          .join("")}</ul>
        <h3>아직 확인할 일</h3><ul>${card.pending_checks
          .map((value) => `<li>${escapePrintText(value)}</li>`)
          .join("")}</ul>
        <p class="evidence">근거 ${card.source_message_ids.length}건</p>
      </article>`,
    );
    printDocument("돌봄 브리핑", periodLabel, sections);
  }

  function printSelectedRaw() {
    if (!selectedRecordTags.length) return;
    const events = selectedEvents();
    if (events.length === 0) return;
    printDocument(
      `${selectedRecordLabel()} 원문 모음`,
      periodLabel,
      events.map(eventPrintSection),
    );
  }

  function printAiSummary() {
    if (!selectedRecordTags.length || !recordSummary) return;
    printDocument(
      `${selectedRecordLabel()} AI 정리`,
      periodLabel,
      [
        `<article class="allow-break"><h2>AI가 선택 근거로 만든 정리</h2>
          <pre>${escapePrintText(recordSummary.summary)}</pre>
          <p class="evidence">근거 대화 ${recordSummary.evidence_ids.length}건</p></article>`,
      ],
    );
  }

  function renderBriefingCard(card: CareBriefingCard) {
    const evidenceOpen = card.source_message_ids.some((sourceId) =>
      expandedSourceIds.includes(sourceId),
    );

    return (
      <article
        key={card.event_group_id ?? card.resident_id}
        className={`care-briefing-card priority-${card.priority}`}
      >
        <header>
          <span className="care-priority-badge">
            {briefingPriorityLabels[card.priority]}
          </span>
          <div>
            <h3>{card.resident_name}</h3>
            <small>근거 대화 {card.source_message_ids.length}건</small>
          </div>
          <time>{formatDateTime(card.latest_at)}</time>
        </header>
        <div className="record-usage-stamps">
          {card.record_usage_tags.map((tag) => (
            <span key={tag}>{recordUsageLabels[tag]}</span>
          ))}
        </div>
        <div className="care-briefing-question">
          <strong>무엇이 달라졌나요?</strong>
          <p>{card.change_summary}</p>
        </div>
        <div className="care-briefing-question">
          <strong>왜 확인해야 하나요?</strong>
          <ul>
            {card.check_reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </div>
        <div className="care-briefing-action-grid">
          <div>
            <strong>이미 한 일</strong>
            {card.completed_actions.length ? (
              <ul>
                {card.completed_actions.map((action) => (
                  <li key={action}>{action}</li>
                ))}
              </ul>
            ) : (
              <p>기록된 시행 조치가 없습니다.</p>
            )}
          </div>
          <div className={card.pending_checks.length ? "has-pending" : ""}>
            <strong>아직 확인할 일</strong>
            {card.pending_checks.length ? (
              <ul>
                {card.pending_checks.map((check) => (
                  <li key={check}>{check}</li>
                ))}
              </ul>
            ) : (
              <p>현재 남은 확인사항이 없습니다.</p>
            )}
          </div>
        </div>
        <footer>
          <button
            type="button"
            aria-expanded={evidenceOpen}
            onClick={() => toggleEvidence(card.source_message_ids)}
          >
            {evidenceOpen
              ? "근거 대화 접기"
              : `근거 대화 ${card.source_message_ids.length}건 보기`}
          </button>
        </footer>
        {evidenceOpen ? (
          <div className="care-briefing-evidence-list">
            {card.source_message_ids.map((sourceId, sourceIndex) => {
              const source = sourceById.get(sourceId);
              if (!source) return null;
              return (
                <article key={sourceId} className="care-briefing-evidence">
                  <header>
                    <span className="message-nature-badge">
                      {messageNatureLabels[source.message.message_type] ??
                        "일반 대화"}
                    </span>
                    <strong>근거 {sourceIndex + 1}</strong>
                  </header>
                  <p>{source.message.body}</p>
                  <small>
                    {source.room_name} · {source.message.sender_name} ·{" "}
                    {formatDateTime(source.message.created_at)} · 읽음{" "}
                    {source.read_count}명 · 답글 {source.reply_user_count}명
                  </small>
                  {source.message.attachments.length ? (
                    <div className="detail-attachments">
                      {source.message.attachments.map((attachment) => (
                        <AttachmentDisplay
                          key={attachment.id}
                          attachment={attachment}
                          accessScope="workdesk"
                        />
                      ))}
                    </div>
                  ) : null}
                </article>
              );
            })}
          </div>
        ) : null}
      </article>
    );
  }

  function renderCareBriefingOverview() {
    if (!review) return null;
    return (
      <section className="care-briefing-overview">
        <div className="section-heading care-briefing-title">
          <div>
            <h3>먼저 확인할 어르신</h3>
            <p>위험 신호나 아직 확인할 일이 있는 어르신을 먼저 보여드립니다.</p>
          </div>
          {review.briefing.cards.length ? (
            <button type="button" className="button" onClick={printBriefing}>
              브리핑 인쇄·PDF
            </button>
          ) : null}
        </div>
        <div className="care-briefing-metrics">
          <span>
            <strong>{review.message_count}</strong>수집된 대화
          </span>
          <span>
            <strong>{review.record_events.length}</strong>어르신별 사건
          </span>
          <span>
            <strong>{attentionCards.length}</strong>먼저 확인
          </span>
          <span>
            <strong>{review.briefing.pending_check_count}</strong>남은 확인
          </span>
        </div>
        {review.truncated ? (
          <p className="form-warning">
            대화가 많아 최근 범위 중 500건까지만 정리했습니다.
          </p>
        ) : null}
        {review.briefing.cards.length === 0 ? (
          <p className="muted-box">
            어르신과 연결된 보고가 없습니다. 아래 기록 활용 후보에서 일반 업무를
            확인할 수 있습니다.
          </p>
        ) : (
          <>
            {attentionCards.length ? (
              <div className="care-briefing-list">
                {attentionCards.map(renderBriefingCard)}
              </div>
            ) : (
              <p className="muted-box">
                지금 바로 확인하도록 분류된 어르신은 없습니다.
              </p>
            )}

            {otherCards.length ? (
              <details
                className="care-briefing-others"
                open={showOtherBriefing}
                onToggle={(event) =>
                  setShowOtherBriefing(event.currentTarget.open)
                }
              >
                <summary>
                  <span>
                    <strong>그밖의 어르신 {otherCards.length}명</strong>
                    <small>경과가 기록된 어르신을 펼쳐 봅니다.</small>
                  </span>
                  <span>{showOtherBriefing ? "접기" : "보기"}</span>
                </summary>
                <div className="care-briefing-others-list">
                  {otherCards.map(renderBriefingCard)}
                </div>
              </details>
            ) : null}
          </>
        )}
      </section>
    );
  }

  if (!open) return null;

  return (
    <div
      className="drawer-layer period-workdesk-layer"
      role="dialog"
      aria-modal="true"
      aria-label="오늘의 돌봄 브리핑"
    >
      <button
        className="drawer-backdrop"
        onClick={onClose}
        aria-label="AI 돌봄 브리핑 닫기"
      />
      <aside className="workdesk-drawer period-workdesk-card">
        <header className="drawer-header period-workdesk-header">
          <div>
            <span className="eyebrow">AI 돌봄 브리핑</span>
            <h2>오늘의 돌봄 브리핑</h2>
            <p>달라진 점, 확인 이유, 이미 한 일과 남은 일을 먼저 보여드립니다.</p>
          </div>
          <button
            className="icon-button"
            onClick={onClose}
            aria-label="AI 돌봄 브리핑 닫기"
          >
            ×
          </button>
        </header>

        <div className="period-workdesk-scroll">
          <details className="period-filter-card" aria-label="정리 범위 선택">
            <summary className="period-filter-summary">
              <span>
                <strong>{periodLabel}</strong>
                <small>
                  {roomId
                    ? rooms.find((room) => room.id === roomId)?.name
                    : "모든 채팅방"}
                  {" · "}
                  {residentId
                    ? residents.find((resident) => resident.id === residentId)
                        ?.display_name
                    : "모든 어르신"}
                </small>
              </span>
              <span>범위 변경</span>
            </summary>
            <div className="period-filter-fields">
              <div className="period-quick-buttons">
                <button type="button" onClick={() => applyQuickRange("today")}>
                  오늘
                </button>
                <button type="button" onClick={() => applyQuickRange("yesterday")}>
                  어제
                </button>
                <button type="button" onClick={() => applyQuickRange("week")}>
                  이번 주
                </button>
              </div>
              <div className="period-filter-grid">
                <label>
                  시작일
                  <input
                    type="date"
                    value={startDate}
                    onChange={(event) => setStartDate(event.target.value)}
                  />
                </label>
                <label>
                  종료일
                  <input
                    type="date"
                    value={endDate}
                    onChange={(event) => setEndDate(event.target.value)}
                  />
                </label>
                <label>
                  채팅방
                  <select
                    value={roomId}
                    onChange={(event) => setRoomId(event.target.value)}
                  >
                    <option value="">모든 채팅방</option>
                    {rooms
                      .filter((room) => room.kind !== "self")
                      .map((room) => (
                        <option key={room.id} value={room.id}>
                          {room.name}
                        </option>
                      ))}
                  </select>
                </label>
                <label>
                  어르신
                  <select
                    value={residentId}
                    onChange={(event) => setResidentId(event.target.value)}
                  >
                    <option value="">모든 어르신</option>
                    {residents.map((resident) => (
                      <option key={resident.id} value={resident.id}>
                        {resident.display_name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  글 성격
                  <select
                    value={messageType}
                    onChange={(event) => setMessageType(event.target.value)}
                  >
                    <option value="">모든 글</option>
                    <option value="handover">인수인계</option>
                    <option value="work_request">업무협조</option>
                    <option value="report">보고</option>
                    <option value="notice">공지</option>
                    <option value="chat">일반 대화</option>
                  </select>
                </label>
                <label className="period-keyword-field">
                  검색어
                  <input
                    value={keyword}
                    maxLength={100}
                    placeholder="이름, 증상, 업무 내용"
                    onChange={(event) => setKeyword(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") void loadReview();
                    }}
                  />
                </label>
              </div>
              <button
                type="button"
                className="button button-primary period-review-button"
                disabled={loading}
                onClick={() => void loadReview()}
              >
                {loading ? "대화 찾는 중…" : "이 범위로 브리핑 만들기"}
              </button>
            </div>
          </details>

          {error ? <p className="form-error">{error}</p> : null}
          {review ? (
            <>
              {renderCareBriefingOverview()}

              <section className="record-groups-section">
                <div className="section-heading">
                  <div>
                    <h3>기록 활용 후보</h3>
                    <p>
                      AI가 대화에서 찾은 활용 후보입니다. 필요한 후보를 여러 개
                      골라 함께 확인할 수 있습니다.
                    </p>
                  </div>
                  <div className="record-filter-actions">
                    <button type="button" onClick={() => setAllRecordGroups(true)}>
                      모두 선택
                    </button>
                    <button type="button" onClick={() => setAllRecordGroups(false)}>
                      선택 해제
                    </button>
                  </div>
                </div>
                <div className="record-group-grid">
                  {recordUsageOrder.map((tag) => (
                    <button
                      type="button"
                      key={tag}
                      role="checkbox"
                      aria-checked={selectedRecordTags.includes(tag)}
                      className={
                        selectedRecordTags.includes(tag) ? "is-active" : ""
                      }
                      onClick={() => toggleRecordGroup(tag)}
                    >
                      <span className="record-filter-check" aria-hidden="true">
                        {selectedRecordTags.includes(tag) ? "✓" : ""}
                      </span>
                      <strong>{recordUsageLabels[tag]}</strong>
                      <span>{review.record_group_counts[tag] ?? 0}건</span>
                      <small>{recordUsageDescriptions[tag]}</small>
                    </button>
                  ))}
                </div>
              </section>

              {selectedRecordTags.length ? (
                <section id="record-group-detail" className="record-group-detail">
                  <div className="section-heading">
                    <div>
                      <h3>선택한 기록 후보</h3>
                      <p>{selectedRecordLabel()}</p>
                    </div>
                    <span>{activeEvents.length}건</span>
                  </div>
                  <div className="record-group-actions">
                    <button
                      type="button"
                      onClick={() =>
                        setSelectedEventIds(
                          activeEvents.map((event) => event.event_group_id),
                        )
                      }
                    >
                      모두 선택
                    </button>
                    <button type="button" onClick={() => setSelectedEventIds([])}>
                      선택 해제
                    </button>
                    <button
                      type="button"
                      className="button-primary"
                      disabled={!selectedEventIds.length || recordSummaryLoading}
                      onClick={() => void summarizeSelectedEvents()}
                    >
                      {recordSummaryLoading ? "AI 정리 중…" : "선택 대화 AI 정리"}
                    </button>
                    <button
                      type="button"
                      disabled={!selectedEventIds.length}
                      onClick={printSelectedRaw}
                    >
                      선택 원문 인쇄·PDF
                    </button>
                  </div>
                  {activeEvents.length === 0 ? (
                    <p className="muted-box">이 기록에 해당하는 대화가 없습니다.</p>
                  ) : (
                    <div className="record-event-list">
                      {activeEvents.map((event, eventIndex) => (
                        <details key={event.event_group_id} className="record-event-card">
                          <summary>
                            <input
                              type="checkbox"
                              aria-label={`${event.resident_name ?? "일반 업무"} 선택`}
                              checked={selectedEventIds.includes(event.event_group_id)}
                              onClick={(clickEvent) => clickEvent.stopPropagation()}
                              onChange={(changeEvent) =>
                                setSelectedEventIds((current) =>
                                  changeEvent.target.checked
                                    ? [...new Set([...current, event.event_group_id])]
                                    : current.filter((id) => id !== event.event_group_id),
                                )
                              }
                            />
                            <span className="record-event-number">{eventIndex + 1}</span>
                            <span>
                              <strong>{event.resident_name ?? "일반 업무"}</strong>
                              <small>
                                {event.room_names.join(", ")} ·{" "}
                                {formatDateTime(event.latest_at)}
                              </small>
                            </span>
                            <span>근거 {event.evidence_ids.length}건</span>
                          </summary>
                          <div className="record-event-body">
                            <p>{event.summary}</p>
                            <div className="record-usage-stamps">
                              {event.record_usage_tags.map((tag) => (
                                <span key={tag}>{recordUsageLabels[tag]}</span>
                              ))}
                            </div>
                            {event.evidence_ids.map((evidenceId, evidenceIndex) => {
                              const source = sourceById.get(evidenceId);
                              if (!source) return null;
                              return (
                                <article key={evidenceId} className="record-evidence">
                                  <strong>근거 {evidenceIndex + 1}</strong>
                                  <p>{source.message.body}</p>
                                  <small>
                                    {source.room_name} · {source.message.sender_name} ·{" "}
                                    {formatDateTime(source.message.created_at)} · 답글{" "}
                                    {source.reply_count}개
                                  </small>
                                  {source.message.attachments.length ? (
                                    <div className="detail-attachments">
                                      {source.message.attachments.map((attachment) => (
                                        <AttachmentDisplay
                                          key={attachment.id}
                                          attachment={attachment}
                                          accessScope="workdesk"
                                        />
                                      ))}
                                    </div>
                                  ) : null}
                                </article>
                              );
                            })}
                          </div>
                        </details>
                      ))}
                    </div>
                  )}
                  {recordSummary ? (
                    <article className="record-ai-result">
                      <header>
                        <div>
                          <span className="eyebrow">AI 정리</span>
                          <h3>한눈에 보는 업무 브리핑</h3>
                        </div>
                        <button type="button" onClick={printAiSummary}>
                          AI 정리 인쇄·PDF
                        </button>
                      </header>
                      <section className="record-ai-overview">
                        <strong>전체 요약</strong>
                        <p>{parsedRecordSummary?.overview}</p>
                      </section>
                      {parsedRecordSummary?.details.length ? (
                        <div className="record-ai-details">
                          {parsedRecordSummary.details.map((section) => (
                            <section key={section.heading}>
                              <strong>{section.heading}</strong>
                              <p>{section.content}</p>
                            </section>
                          ))}
                        </div>
                      ) : null}
                      <small>
                        근거 {recordSummary.evidence_ids.length}건 · 원문에 없는
                        사실과 공식 평가점수는 만들지 않습니다.
                      </small>
                      <small>{recordSummaryExecutionLabel(recordSummary)}</small>
                    </article>
                  ) : null}
                </section>
              ) : null}

            </>
          ) : (
            <p className="muted-box">
              {loading ? "오늘의 대화를 확인하고 있습니다…" : "브리핑을 불러오세요."}
            </p>
          )}
        </div>
      </aside>
    </div>
  );
}
