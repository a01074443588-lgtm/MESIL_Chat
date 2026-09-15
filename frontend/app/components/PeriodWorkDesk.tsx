"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ApiError, apiFetch } from "../api";
import { RecordPreparationError, runWorkdeskSummary } from "../recordQuestionFlow";
import {
  openDocumentDraftWorkspace,
  periodDocumentLabels,
} from "../documentDraftWorkspace";
import { messageSummary } from "../messagePresentation";
import type {
  CareDocumentCandidateType,
  CareBriefingCard,
  DailyDocumentType,
  FieldCareBriefingHistorySummary,
  PeriodDocumentDraft,
  PeriodRecordEvent,
  PeriodRecordSummary,
  PeriodRecordSummarySelection,
  PeriodWorkdeskReview,
  Resident,
  Room,
} from "../types";
import { AttachmentDisplay } from "./AttachmentDisplay";
import { ResidentCarePlanningPanel } from "./ResidentCarePlanningPanel";
import { CareBriefingReader } from "./CareBriefingReader";
import { ResidentConfirmationInbox } from "./ResidentConfirmationInbox";

const messageNatureLabels: Record<string, string> = {
  chat: "일반 대화",
  notice: "공지",
  handover: "인수인계",
  work_request: "업무협조",
  report: "보고",
};

const briefingDisplayGroupLabels = {
  today_schedule: "오늘 예정·기한",
  attention: "지금 먼저 확인",
  carryover: "계속 확인할 일",
  background: "경과 참고",
  completed: "처리 완료",
} as const;

const briefingFinalStatusLabels = {
  needs_confirmation: "확인 필요",
  in_progress: "진행 중",
  completed: "완료",
  monitoring: "경과 관찰",
} as const;

const documentCandidateLabels: Record<CareDocumentCandidateType, string> = {
  care_service_record: "급여제공기록지",
  consultation_log: "상담일지",
  cognitive_function_assessment: "인지기능검사",
  fall_risk_assessment: "낙상위험도",
  pressure_ulcer_risk_assessment: "욕창위험도",
  needs_assessment: "욕구사정",
  long_term_care_service_plan: "장기요양급여 제공계획서",
};

const documentCandidateDescriptions: Record<CareDocumentCandidateType, string> = {
  care_service_record:
    "프로그램·바이탈·건강·간호·식사·수분·배설·이동·안전·반응 사실",
  consultation_log: "보호자 상담·전화·요청·설명·동의·결과 공유 사실",
  cognitive_function_assessment: "반기 도래 또는 상태변경 시 직원 실시·채점 후보",
  fall_risk_assessment: "반기 도래 또는 낙상·보행 상태변경 시 재평가 후보",
  pressure_ulcer_risk_assessment: "반기 도래 또는 피부·활동 상태변경 시 재평가 후보",
  needs_assessment: "반기 도래 또는 전반적 욕구·상태변경 시 재사정 후보",
  long_term_care_service_plan: "반기 도래 또는 상태변경 시 계획 재수립 후보",
};

const documentCandidateOrder: CareDocumentCandidateType[] = [
  "care_service_record",
  "consultation_log",
  "cognitive_function_assessment",
  "fall_risk_assessment",
  "pressure_ulcer_risk_assessment",
  "needs_assessment",
  "long_term_care_service_plan",
];

const alwaysAvailableDocumentCandidates: CareDocumentCandidateType[] = [
  "care_service_record",
  "consultation_log",
];

function dateInputValue(value: Date) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function rollingWeekStartValue() {
  const value = new Date();
  value.setDate(value.getDate() - 6);
  return dateInputValue(value);
}

function parseDateInput(value: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return null;
  const parsed = new Date(
    Number(match[1]),
    Number(match[2]) - 1,
    Number(match[3]),
  );
  return dateInputValue(parsed) === value ? parsed : null;
}

function addCalendarMonths(value: Date, months: number) {
  const monthIndex = value.getMonth() + months;
  const year = value.getFullYear() + Math.floor(monthIndex / 12);
  const month = ((monthIndex % 12) + 12) % 12;
  const lastDay = new Date(year, month + 1, 0).getDate();
  return new Date(year, month, Math.min(value.getDate(), lastDay));
}

function periodRangeError(startValue: string, endValue: string) {
  const start = parseDateInput(startValue);
  const end = parseDateInput(endValue);
  if (!start || !end) return "시작일과 종료일을 모두 선택해 주세요.";
  if (end < start) return "종료일은 시작일보다 빠를 수 없습니다.";
  if (end >= addCalendarMonths(start, 6)) {
    return "한 번에 최대 6개월까지 정리할 수 있습니다. 시작일을 늦추거나 종료일을 앞당겨 주세요.";
  }
  return "";
}

function periodDayCount(startValue: string, endValue: string) {
  const start = parseDateInput(startValue);
  const end = parseDateInput(endValue);
  if (!start || !end || end < start) return 0;
  return Math.round((end.getTime() - start.getTime()) / 86_400_000) + 1;
}

function careBriefingErrorMessage(reason: unknown) {
  if (reason instanceof Error) {
    const message = reason.message.trim();
    if (
      message &&
      message !== "[object Object]" &&
      !/failed to fetch|networkerror|load failed/i.test(message)
    ) {
      return message;
    }
  }
  return "서버와 연결하지 못했습니다. 네트워크를 확인한 뒤 다시 시도해 주세요.";
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

function formatCompactDateTime(value: string) {
  return new Intl.DateTimeFormat("ko-KR", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatBriefingDay(value: string) {
  const [year, month, day] = value.split("-").map(Number);
  if (!year || !month || !day) return value;
  return `${year}년 ${month}월 ${day}일`;
}

function compactBriefingText(value: string, residentName?: string) {
  let compact = value
    .replace(/\[[A-Z0-9_-]+:[^\]]+\]\s*/gi, "")
    .replace(
      /^비교할 최근 \d+일 기록이 없어 이번 관찰을 첫 기준으로 표시합니다\.\s*/,
      "",
    )
    .replace(/모든 내용은 실제 인물과 무관한 합성 시험자료입니다\.?/g, "")
    .replace(/모든 내용은 합성 시험자료입니다\.?/g, "")
    .replace(/\s+/g, " ")
    .trim();
  if (residentName) {
    for (const prefix of [
      `${residentName}님의 `,
      `${residentName}님이 `,
      `${residentName}님에게 `,
    ]) {
      if (compact.startsWith(prefix)) {
        compact = compact.slice(prefix.length);
        break;
      }
    }
  }
  return compact;
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
          <p><strong>DEV 비공식 자료 · 코드화된 합성 시험자료</strong></p>
          <p>기간 ${escapePrintText(periodLabel)} · 자동 요약과 원문 근거를 구분하여 확인하세요.</p>
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
  onOpenSource,
}: {
  open: boolean;
  rooms: Room[];
  initialDate?: string;
  onClose: () => void;
  onOpenSource: (roomId: string, messageId: string) => void | Promise<void>;
}) {
  const today = useMemo(() => dateInputValue(new Date()), []);
  const defaultStartDate = useMemo(() => rollingWeekStartValue(), []);
  const [startDate, setStartDate] = useState(initialDate || defaultStartDate);
  const [endDate, setEndDate] = useState(initialDate || today);
  const [roomId, setRoomId] = useState("");
  const [residentId, setResidentId] = useState("");
  const [planningResidentId, setPlanningResidentId] = useState("");
  const keyword = "";
  const messageType = "";
  const [residents, setResidents] = useState<Resident[]>([]);
  const [residentLoadError, setResidentLoadError] = useState("");
  const [residentLoading, setResidentLoading] = useState(false);
  const [review, setReview] = useState<PeriodWorkdeskReview | null>(null);
  const [reviewHistory, setReviewHistory] = useState<
    FieldCareBriefingHistorySummary[]
  >([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [summaryMode, setSummaryMode] = useState<"overall" | "resident">(
    "overall",
  );
  const [showPlanningHelper, setShowPlanningHelper] = useState(false);
  const [reviewScope, setReviewScope] = useState("");
  const [comparisonMode, setComparisonMode] = useState(false);
  const [visibleCounts, setVisibleCounts] = useState<Record<string, number>>({});
  const [expandedSourceIds, setExpandedSourceIds] = useState<string[]>([]);
  const [selectedDocumentCandidates, setSelectedDocumentCandidates] = useState<
    CareDocumentCandidateType[]
  >([]);
  const [selectedEventIds, setSelectedEventIds] = useState<string[]>([]);
  const [recordSummary, setRecordSummary] = useState<PeriodRecordSummary | null>(
    null,
  );
  const [recordSummaryLoading, setRecordSummaryLoading] = useState(false);
  const recordSummaryControllerRef = useRef<AbortController | null>(null);
  const [recordSelectionStatus, setRecordSelectionStatus] = useState("");
  const [recordActionStatus, setRecordActionStatus] = useState("");
  const [error, setError] = useState("");
  const [reviewStatus, setReviewStatus] = useState("");
  const [guidanceEdits, setGuidanceEdits] = useState<Record<string, string>>({});
  const [planReviewEventIds, setPlanReviewEventIds] = useState<string[]>([]);
  const [confirmedGuidanceIds, setConfirmedGuidanceIds] = useState<string[]>([]);
  const requestVersionRef = useRef(0);
  const residentRequestRef = useRef(0);
  const recordAiResultRef = useRef<HTMLElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const planningRef = useRef<HTMLElement | null>(null);
  const scrollPositionRef = useRef(0);
  const overallScrollRef = useRef(0);
  const restoreScrollRef = useRef<number | null>(null);
  const loadedScope = `${startDate}|${endDate}|${residentId}|${roomId}`;

  const periodLabel =
    startDate === endDate ? startDate : `${startDate} ~ ${endDate}`;
  const selectedPeriodDays = periodDayCount(startDate, endDate);
  const periodDescription = selectedPeriodDays
    ? `${periodLabel} · ${selectedPeriodDays}일 범위`
    : "선택한 기간";
  const parsedRecordSummary = recordSummary
    ? parseRecordSummary(recordSummary.summary)
    : null;
  const recordSummaryDocumentDrafts = (recordSummary?.document_drafts ?? []).filter(
    (draft) =>
      draft.document_type === "care_service_record" ||
      draft.document_type === "consultation_log",
  );
  const planningResident =
    residents.find((resident) => resident.id === planningResidentId) ?? null;
  const legacyDocumentSelectionVisible = false;

  const loadReviewHistory = useCallback(async () => {
    try {
      const payload = await apiFetch<FieldCareBriefingHistorySummary[]>(
        "/api/workdesk/period-reviews",
      );
      setReviewHistory(payload);
    } catch {
      setReviewHistory([]);
    }
  }, []);

  const loadResidents = useCallback(async () => {
    const version = ++residentRequestRef.current;
    setResidentLoading(true);
    setResidentLoadError("");
    try {
      const payload = await apiFetch<Resident[]>("/api/workdesk/residents");
      if (residentRequestRef.current === version) setResidents(payload);
    } catch {
      if (residentRequestRef.current === version) setResidentLoadError("어르신 목록을 불러오지 못했습니다. 이전 목록은 유지됩니다.");
    } finally {
      if (residentRequestRef.current === version) setResidentLoading(false);
    }
  }, []);

  const loadReview = useCallback(
    async (
      override?: Partial<{
        startDate: string;
        endDate: string;
        roomId: string;
        residentId: string;
        keyword: string;
        messageType: string;
        preservePlanningHelper: boolean;
        saveHistory: boolean;
      }>,
    ) => {
        const saveHistory = override?.saveHistory ?? false;
      const next = {
        startDate: override?.startDate ?? startDate,
        endDate: override?.endDate ?? endDate,
        roomId: override?.roomId ?? roomId,
        residentId: override?.residentId ?? residentId,
        keyword: override?.keyword ?? keyword,
        messageType: override?.messageType ?? messageType,
      };
      const validationMessage = periodRangeError(
        next.startDate,
        next.endDate,
      );
      if (validationMessage) {
        setReviewStatus("");
        setError(validationMessage);
        return;
      }
      const version = requestVersionRef.current + 1;
      requestVersionRef.current = version;
      setLoading(true);
      setShowPlanningHelper(false);
      setError("");
      setReviewStatus("기록 조회 중 → 경과 정리 중 → 화면 준비 중");
      const reviewStarted = performance.now();
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
            body: JSON.stringify({
              ...requestBody,
              enhance_summary: false,
              response_mode: "briefing",
              save_history: saveHistory,
            }),
          },
        );
        if (requestVersionRef.current !== version) return;
        setReview(payload);
        setReviewScope(`${next.startDate}|${next.endDate}|${next.residentId}|${next.roomId}`);
        setSummaryMode(next.residentId ? "resident" : "overall");
        setExpandedSourceIds([]);
        setSelectedDocumentCandidates([]);
        setSelectedEventIds([]);
        setRecordSummary(null);
        setRecordSelectionStatus("");
        setRecordActionStatus("");
        setGuidanceEdits({});
        setPlanReviewEventIds([]);
        setConfirmedGuidanceIds([]);
        setReviewStatus("화면 준비 중");
        setLoading(false);
        requestAnimationFrame(() => requestAnimationFrame(() => {
          if (requestVersionRef.current !== version) return;
          setReviewStatus("");
          const resources = performance.getEntriesByType("resource") as PerformanceResourceTiming[];
          const entry = resources.filter((item) => item.name.endsWith("/api/workdesk/period-review") && item.startTime >= reviewStarted).at(-1);
          const residentEntry = resources.filter((item) => item.name.endsWith("/api/workdesk/residents")).at(-1);
          // Numeric telemetry only: never include dates, IDs, names or content.
          window.dispatchEvent(new CustomEvent("mesil:period-review-timing", { detail: {
            total_ms: performance.now() - reviewStarted,
            response_transfer_ms: entry ? entry.responseEnd - entry.responseStart : null,
            frontend_render_ms: entry ? performance.now() - entry.responseEnd : null,
            resident_request_ms: residentEntry ? residentEntry.duration : null,
            server_ms: Object.fromEntries((entry?.serverTiming ?? []).map((item) => [item.name, item.duration])),
            message_count: payload.message_count, comment_count: payload.comment_count,
            attachment_count: payload.attachment_count ?? payload.sources.reduce((count, source) => count + source.message.attachments.length, 0),
          } }));
        }));
        if (payload.history_id) void loadReviewHistory();
      } catch (reason) {
        if (requestVersionRef.current !== version) return;
        setReviewStatus("");
        setError(careBriefingErrorMessage(reason));
      } finally {
        if (requestVersionRef.current === version) setLoading(false);
      }
    },
    [endDate, keyword, loadReviewHistory, messageType, residentId, roomId, startDate],
  );

  useEffect(() => {
    const changed = () => { setReview(null); void loadReview({ saveHistory: false }); };
    window.addEventListener("mesil-resident-links-changed", changed);
    return () => window.removeEventListener("mesil-resident-links-changed", changed);
  }, [loadReview]);

  async function openSavedReview(history: FieldCareBriefingHistorySummary) {
    setHistoryLoading(true);
    setError("");
    setReviewStatus("저장된 브리핑을 불러오고 있습니다…");
    try {
      const payload = await apiFetch<PeriodWorkdeskReview>(
        `/api/workdesk/period-reviews/${history.id}`,
      );
      setReview(payload);
      setReviewScope(`${history.period_start}|${history.period_end}|${history.resident_id ?? ""}|`);
      setRoomId("");
      setStartDate(history.period_start);
      setEndDate(history.period_end);
      setResidentId(history.resident_id ?? "");
      if (history.resident_id) {
        setPlanningResidentId(history.resident_id);
      }
      setSummaryMode(history.resident_id ? "resident" : "overall");
      setExpandedSourceIds([]);
      setReviewStatus(
        `저장된 브리핑 ${history.revision}번을 다시 열었습니다. 공식 급여제공기록에는 저장되지 않은 참고 브리핑입니다.`,
      );
    } catch (reason) {
      setReviewStatus("");
      setError(careBriefingErrorMessage(reason));
    } finally {
      setHistoryLoading(false);
    }
  }

  function changeResidentScope(nextResidentId: string) {
    if (!residentId && nextResidentId) overallScrollRef.current = scrollRef.current?.scrollTop ?? 0;
    restoreScrollRef.current = nextResidentId ? 0 : overallScrollRef.current;
    setResidentId(nextResidentId);
    if (nextResidentId) {
      setPlanningResidentId(nextResidentId);
    }
    void loadReview({ residentId: nextResidentId });
  }

  function togglePlanningHelper() {
    setShowPlanningHelper((current) => !current);
  }

  useEffect(() => {
    if (!open) return;
    const residentTimer = window.setTimeout(() => void loadResidents(), 0);
    const timer = window.setTimeout(() => { if (!review) void loadReview(); }, 0);
    const historyTimer = window.setTimeout(() => void loadReviewHistory(), 0);
    return () => {
      residentRequestRef.current += 1;
      window.clearTimeout(residentTimer);
      window.clearTimeout(timer);
      window.clearTimeout(historyTimer);
      requestVersionRef.current += 1;
    };
    // 업무함을 여는 순간 한 번만 현재 선택 기간을 불러옵니다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useLayoutEffect(() => {
    if (!open || loading || !scrollRef.current) return;
    scrollRef.current.scrollTop = restoreScrollRef.current ?? scrollPositionRef.current;
    restoreScrollRef.current = null;
  }, [open, loading, review]);

  useEffect(() => {
    return () => recordSummaryControllerRef.current?.abort();
  }, [open, selectedDocumentCandidates, selectedEventIds]);

  useEffect(() => {
    if (showPlanningHelper) planningRef.current?.scrollIntoView({ block: "start" });
  }, [showPlanningHelper]);

  useEffect(() => {
    if (!open) return;
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !event.defaultPrevented) onClose();
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

  const documentCandidateReviewCounts = useMemo(() => {
    const counts = new Map<CareDocumentCandidateType, number>();
    for (const event of review?.record_events ?? []) {
      for (const candidateType of event.document_candidate_review_types ?? []) {
        counts.set(candidateType, (counts.get(candidateType) ?? 0) + 1);
      }
    }
    return counts;
  }, [review]);

  const activeEvents = useMemo(() => {
    if (!selectedDocumentCandidates.length) return [];
    const selectedManualTypes = selectedDocumentCandidates.filter(
      (candidateType) =>
        (review?.document_candidate_counts[candidateType] ?? 0) === 0,
    );
    return (review?.record_events ?? []).filter(
      (event) =>
        event.document_candidate_types.some((candidateType) =>
          selectedDocumentCandidates.includes(candidateType),
        ) ||
        (event.document_candidate_review_types ?? []).some((candidateType) =>
          selectedDocumentCandidates.includes(candidateType),
        ) ||
        selectedManualTypes.length > 0,
    );
  }, [review, selectedDocumentCandidates]);

  const briefingResidentGroups = useMemo(() => {
    const groups = new Map<
      string,
      { residentId: string; residentName: string; cards: CareBriefingCard[] }
    >();
    for (const card of review?.briefing.cards ?? []) {
      const key = card.resident_id || card.resident_name || "general";
      const group = groups.get(key);
      if (group) {
        group.cards.push(card);
      } else {
        groups.set(key, {
          residentId: card.resident_id,
          residentName: card.resident_name || "공통 업무",
          cards: [card],
        });
      }
    }
    return [...groups.values()];
  }, [review]);

  function applyQuickRange(
    kind: "today" | "week" | "month" | "quarter" | "half-year",
  ) {
    const end = new Date();
    const start = new Date(end);
    if (kind === "week") {
      start.setDate(start.getDate() - 6);
    } else if (kind !== "today") {
      const months = kind === "month" ? 1 : kind === "quarter" ? 3 : 6;
      const calendarStart = addCalendarMonths(end, -months);
      calendarStart.setDate(calendarStart.getDate() + 1);
      start.setTime(calendarStart.getTime());
    }
    const nextStart = dateInputValue(start);
    const nextEnd = dateInputValue(end);
    setStartDate(nextStart);
    setEndDate(nextEnd);
    void loadReview({
      startDate: nextStart,
      endDate: nextEnd,
      saveHistory: false,
    });
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

  function toggleDocumentCandidate(candidateType: CareDocumentCandidateType) {
    setSelectedDocumentCandidates((current) => {
      const selected = !current.includes(candidateType);
      const next = selected
        ? documentCandidateOrder.filter(
            (currentType) =>
              current.includes(currentType) || currentType === candidateType,
          )
        : current.filter((currentType) => currentType !== candidateType);
      setRecordSelectionStatus(
        selected
          ? `1단계: ${documentCandidateLabels[candidateType]}를 선택했습니다. 2단계: 이 서류에 넣을 대화와 기록을 아래에서 선택해 주세요.`
          : `${documentCandidateLabels[candidateType]} 선택을 해제했습니다.${
              next.length ? " 다른 서류 선택은 유지됩니다." : ""
            } 선택을 해제해도 원본 대화와 기록은 삭제되지 않습니다.`,
      );
      return next;
    });
    setSelectedEventIds([]);
    setRecordSummary(null);
    setRecordActionStatus("");
  }

  function setAllDocumentCandidates(selected: boolean) {
    setSelectedDocumentCandidates(
      selected ? [...alwaysAvailableDocumentCandidates] : [],
    );
    setSelectedEventIds([]);
    setRecordSummary(null);
    setRecordSelectionStatus(
      selected
        ? "1단계: 작성할 서류 2종을 선택했습니다. 2단계: 이 서류에 넣을 대화와 기록을 아래에서 선택해 주세요."
        : "작성할 서류 선택을 모두 해제했습니다. 선택을 해제해도 원본 대화와 기록은 삭제되지 않습니다.",
    );
    setRecordActionStatus("");
  }

  function selectedRecordLabel() {
    return selectedDocumentCandidates
      .map((candidateType) => documentCandidateLabels[candidateType])
      .join(" · ");
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
    if (recordSummaryControllerRef.current) return;
    if (!selectedDocumentCandidates.length) {
      setRecordActionStatus("1단계에서 작성할 서류를 먼저 선택해 주세요.");
      return;
    }
    const selections = selectedEventSelections();
    if (!selections.some((selection) => selection.evidence_ids.length > 0)) {
      setRecordActionStatus("2단계에서 서류에 넣을 대화와 기록을 한 건 이상 선택해 주세요.");
      return;
    }
    setRecordSummary(null);
    setRecordSummaryLoading(true);
    setRecordActionStatus("선택한 내용의 업무 요약을 만들고 있습니다…");
    const controller = new AbortController();
    recordSummaryControllerRef.current = controller;
    try {
      const result = await runWorkdeskSummary<PeriodRecordSummary>(
        apiFetch,
        JSON.stringify({
            document_candidate_types: selectedDocumentCandidates,
            selections,
        }),
        controller.signal,
        (phase) => {
          if (!controller.signal.aborted) setRecordActionStatus(
            phase === "waiting_capacity"
              ? "다른 AI 작업이 끝나거나 GPU 여유가 확보되기를 기다리고 있습니다."
              : phase === "model_preparing"
              ? "로컬 AI 모델을 준비하고 있습니다. 준비가 끝나면 요약을 생성합니다."
              : "선택한 원문에서 요약을 만들고 근거를 검토하고 있습니다…",
          );
        },
      );
      controller.signal.throwIfAborted();
      if (recordSummaryControllerRef.current !== controller) return;
      setRecordSummary(result);
      setRecordActionStatus(
        "선택한 내용 요약을 만들었습니다. 자동 저장되지 않았습니다. ‘결과 확인하기’를 눌러 확인해 주세요.",
      );
    } catch (reason) {
      if (recordSummaryControllerRef.current !== controller) return;
      const failureMessage =
        reason instanceof DOMException && reason.name === "AbortError"
          ? "요약 요청을 취소했습니다. 다시 시도할 수 있습니다."
          : reason instanceof ApiError && reason.status === 503
          ? new RecordPreparationError(reason.message).message
          : reason instanceof Error
          ? reason.message
          : "선택한 내용을 요약하지 못했습니다.";
      setRecordActionStatus(
        `요약을 만들지 못했습니다. ${failureMessage} 선택한 내용과 원본 대화·기록은 그대로 유지됩니다.`,
      );
    } finally {
      if (recordSummaryControllerRef.current === controller) {
        recordSummaryControllerRef.current = null;
        setRecordSummaryLoading(false);
      }
    }
  }

  function eventDocumentSelectionReasons(event: PeriodRecordEvent) {
    const reasons = selectedDocumentCandidates.flatMap((candidateType) => {
      const recommendedReason = event.document_candidate_reasons?.[candidateType];
      if (recommendedReason) {
        if (recommendedReason.startsWith("확인 필요")) {
          return [
            `${documentCandidateLabels[candidateType]} · ${recommendedReason}`,
          ];
        }
        return [
          `${documentCandidateLabels[candidateType]} 추천 · ${recommendedReason}`,
        ];
      }
      const reviewReason = event.document_candidate_review_reasons?.[candidateType];
      if (reviewReason) {
        return [
          `${documentCandidateLabels[candidateType]} 여부 확인 필요 · ${reviewReason}`,
        ];
      }
      if ((review?.document_candidate_counts[candidateType] ?? 0) === 0) {
        return [
          `${documentCandidateLabels[candidateType]} 직원 수동 선택 · 자동 추천 근거가 아니므로 원문 확인이 필요합니다.`,
        ];
      }
      return [];
    });
    return [...new Set(reasons)];
  }

  function focusRecordSummaryResult() {
    const target = recordAiResultRef.current;
    if (!target) {
      setRecordActionStatus("확인할 요약 결과가 없습니다. 먼저 요약을 만들어 주세요.");
      return;
    }
    target.scrollIntoView({ behavior: "smooth", block: "start" });
    target.focus({ preventScroll: true });
    setRecordActionStatus(
      "선택한 내용 요약 결과로 이동했습니다. 원문과 대조해 수정한 뒤 직원이 최종 확인해 주세요.",
    );
  }

  function openRecordSummaryDraft(draft: PeriodDocumentDraft) {
    const selected = selectedEvents().filter(
      (event) =>
        event.resident_id === draft.resident_id &&
        event.evidence_ids.some((evidenceId) =>
          draft.source_message_ids.includes(evidenceId),
        ),
    );
    const draftSources = draft.source_message_ids
      .map((sourceId) => sourceById.get(sourceId))
      .filter((source) => source !== undefined);
    const occurredValues = selected.map((event) => event.occurred_at).sort();
    const latestValues = selected.map((event) => event.latest_at).sort();
    const fallbackTimestamp = draftSources[0]?.message.created_at ?? new Date().toISOString();
    const candidateType = draft.document_type as CareDocumentCandidateType;
    const card: CareBriefingCard = {
      social_worker_guidance: null,
      event_group_id: draft.event_group_id,
      resident_id: draft.resident_id,
      resident_name: draft.resident_name,
      priority: "check",
      display_group: "attention",
      importance_score: 0,
      due_at: null,
      change_summary:
        selected
          .map((event) => compactBriefingText(event.summary, event.resident_name ?? undefined))
          .filter(Boolean)
          .join(" · ") || parsedRecordSummary?.overview || "선택한 근거 내용",
      final_status: "needs_confirmation",
      final_status_summary: "직원이 원문과 대조하고 확인하기 전인 편집용 초안입니다.",
      check_reasons: [
        "선택한 근거만 사용한 편집용 초안이며 공식 기록으로 자동 저장되지 않습니다.",
      ],
      completed_actions: [],
      pending_checks: ["원문 대조·항목별 수정·직원 최종 확인"],
      document_types: [draft.document_type as DailyDocumentType],
      record_usage_tags: [],
      document_candidate_types: [candidateType],
      source_message_ids: draft.source_message_ids,
      current_message_count: draft.source_message_ids.length,
      baseline_message_count: 0,
      occurred_at: occurredValues[0] ?? fallbackTimestamp,
      latest_at: latestValues.at(-1) ?? fallbackTimestamp,
    };
    try {
      openDocumentDraftWorkspace({
        card,
        drafts: [draft],
        sources: draftSources,
        periodLabel,
        onOpenSource,
      });
      setRecordActionStatus(
        `${periodDocumentLabels[draft.document_type]} 편집 초안을 별도 창으로 열었습니다. 원문 대조와 직원 확인 전에는 인쇄·PDF가 잠겨 있습니다.`,
      );
    } catch (reason) {
      setRecordActionStatus(careBriefingErrorMessage(reason));
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
      <p>${event.document_candidate_types
        .map(
          (candidateType) =>
            `<span class="stamp">${escapePrintText(
              documentCandidateLabels[candidateType],
            )}</span>`,
        )
        .join("")}</p>
      ${sources
        .map(
          (source, sourceIndex) => `<h3>근거 ${sourceIndex + 1}</h3>
            <pre>${escapePrintText(
              messageSummary(source.message.body, source.message.attachments),
            )}</pre>
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
    setReviewStatus(
      "브리핑 인쇄 미리보기를 열었습니다. Chrome 인쇄창에서 인쇄·PDF 저장 또는 취소를 선택하세요.",
    );
    const sections = [
      `<article><h2>선택 기간 전체 요약</h2><p>${escapePrintText(
        review.overall_summary,
      )}</p></article>`,
      ...review.daily_care_references.map(
        (day) => `<article class="allow-break">
          <h2>${escapePrintText(formatBriefingDay(day.date))}</h2>
          ${day.residents
            .map(
              (resident) => `<h3>${escapePrintText(resident.resident_name)}</h3>
                ${resident.entries
                  .map(
                    (entry) => `<p><strong>${escapePrintText(
                      entry.time_label,
                    )}</strong> ${escapePrintText(entry.summary)}${
                      entry.conflict_note
                        ? ` <span class="stamp">${escapePrintText(entry.conflict_note)}</span>`
                        : ""
                    }</p>`,
                  )
                  .join("")}`,
            )
            .join("")}
        </article>`,
      ),
      ...(review.consultation_references.length
        ? [
            `<article class="allow-break"><h2>상담일지 참고 내용</h2>${review.consultation_references
              .map(
                (entry) => `<p><strong>${escapePrintText(
                  entry.resident_name,
                )}</strong> · ${escapePrintText(
                  formatBriefingDay(entry.occurred_at.slice(0, 10)),
                )} ${escapePrintText(entry.time_label)} ${escapePrintText(
                  entry.summary,
                )}</p>`,
              )
              .join("")}</article>`,
          ]
        : []),
    ];
    printDocument("돌봄 브리핑", periodLabel, sections);
  }

  function printSelectedRaw() {
    if (!selectedDocumentCandidates.length) {
      setRecordActionStatus("1단계에서 작성할 서류를 먼저 선택해 주세요.");
      return;
    }
    const events = selectedEvents();
    if (events.length === 0) {
      setRecordActionStatus("인쇄할 대화와 기록을 한 건 이상 선택해 주세요.");
      return;
    }
    setRecordActionStatus(
      "선택한 내용 원문 인쇄 미리보기를 열었습니다. Chrome 인쇄창에서 인쇄·PDF 저장 또는 취소를 선택하세요.",
    );
    printDocument(
      `${selectedRecordLabel()} 원문 모음`,
      periodLabel,
      events.map(eventPrintSection),
    );
  }

  function printAiSummary() {
    if (!selectedDocumentCandidates.length || !recordSummary) {
      setRecordActionStatus("먼저 선택한 내용 요약을 만들어 주세요.");
      return;
    }
    setRecordActionStatus(
      "요약 인쇄 미리보기를 열었습니다. Chrome 인쇄창에서 인쇄·PDF 저장 또는 취소를 선택하세요.",
    );
    printDocument(
      `${selectedRecordLabel()} 선택 내용 요약`,
      periodLabel,
      [
        `<article class="allow-break"><h2>선택한 내용으로 만든 업무 요약</h2>
          <pre>${escapePrintText(recordSummary.summary)}</pre>
          <p class="evidence">선택한 원문 ${recordSummary.evidence_ids.length}건</p></article>`,
      ],
    );
  }

  function renderBriefingCard(card: CareBriefingCard) {
    const evidenceOpen = card.source_message_ids.some((sourceId) =>
      expandedSourceIds.includes(sourceId),
    );
    const eventDrafts = card.event_group_id
      ? (review?.document_drafts ?? []).filter(
          (draft) => draft.event_group_id === card.event_group_id,
        )
      : [];
    const eventSources = card.source_message_ids
      .map((sourceId) => sourceById.get(sourceId))
      .filter((source) => source !== undefined);
    const compactSummary = compactBriefingText(
      card.change_summary,
      card.resident_name,
    );
    const compactFollowUp = card.pending_checks[0]
      ? compactBriefingText(card.pending_checks[0])
      : "";
    const guidanceKey =
      card.event_group_id ?? `${card.resident_id}-${card.occurred_at}`;
    const guidance = card.social_worker_guidance;
    const guidanceText = guidanceEdits[guidanceKey] ?? guidance?.suggested_wording ?? "";
    const inPlanReview = planReviewEventIds.includes(guidanceKey);
    const staffConfirmed = confirmedGuidanceIds.includes(guidanceKey);

    return (
      <details
        key={`${card.event_group_id ?? card.resident_id}-${card.occurred_at}`}
        className={`care-briefing-row priority-${card.priority}`}
      >
        <summary>
          <span className={`care-briefing-row-status status-${card.final_status}`}>
            {briefingFinalStatusLabels[card.final_status]}
          </span>
          <span className="care-briefing-row-copy">
            <strong>{compactSummary}</strong>
            {compactFollowUp ? (
              <small>
                다음: {compactFollowUp}
                {card.due_at ? ` · ${formatCompactDateTime(card.due_at)}` : ""}
              </small>
            ) : card.due_at ? (
              <small>재확인 {formatCompactDateTime(card.due_at)}</small>
            ) : null}
          </span>
          <span className="care-briefing-row-meta">
            근거 {card.source_message_ids.length}건 · 세부·근거 보기
          </span>
        </summary>
        <article className={`care-briefing-card priority-${card.priority}`}>
        <header>
          <span className="care-priority-badge">
            {briefingDisplayGroupLabels[card.display_group]}
          </span>
          <div>
            <h3>사건 상세</h3>
            <small>근거 대화 {card.source_message_ids.length}건</small>
          </div>
          <div className="care-briefing-times">
            <span>발생 {formatDateTime(card.occurred_at)}</span>
            {card.latest_at !== card.occurred_at ? (
              <span>최근 갱신 {formatDateTime(card.latest_at)}</span>
            ) : null}
            {card.due_at ? <span>확인 기한 {formatDateTime(card.due_at)}</span> : null}
          </div>
        </header>
        {guidance ? (
          <section className="social-worker-guidance" aria-label="사회복지사 작성 안내">
            <header>
              <div>
                <span>사회복지사 작성 안내</span>
                <h4>{guidance.title}</h4>
              </div>
              <span className={guidance.based_on_confirmed_facts ? "is-grounded" : "needs-check"}>
                {guidance.based_on_confirmed_facts ? "확정 사실 기반" : "직원 확인 필요"}
              </span>
            </header>
            {(guidance.previous_state || guidance.current_state) ? (
              <dl className="social-worker-state-change">
                {guidance.previous_state ? <div><dt>이전</dt><dd>{guidance.previous_state}</dd></div> : null}
                {guidance.current_state ? <div><dt>현재</dt><dd>{guidance.current_state}</dd></div> : null}
              </dl>
            ) : null}
            {guidance.confirmation_questions.length ? (
              <div className="social-worker-questions">
                <strong>직원에게 확인할 내용</strong>
                <ul>{guidance.confirmation_questions.map((question) => <li key={question}>{question}</li>)}</ul>
              </div>
            ) : null}
            <label className="social-worker-writing-field">
              이렇게 작성해 보세요
              <textarea
                value={guidanceText}
                rows={3}
                onChange={(event) => setGuidanceEdits((current) => ({ ...current, [guidanceKey]: event.target.value }))}
                aria-describedby={`${guidanceKey}-safety`}
              />
            </label>
            <div className="social-worker-related-documents">
              <strong>관련 서류</strong>
              {guidance.related_document_types.length ? guidance.related_document_types.map((type) => (
                <span key={type}>{documentCandidateLabels[type]}</span>
              )) : <span>직원 판단</span>}
            </div>
            <p id={`${guidanceKey}-safety`} className="social-worker-safety-notice">
              {guidance.safety_notice}
            </p>
            {guidance.plan_review_notice ? <p className="social-worker-plan-notice">{guidance.plan_review_notice}</p> : null}
            <div className="social-worker-guidance-actions">
              {guidance.plan_review_recommended ? (
                <button
                  type="button"
                  aria-pressed={inPlanReview}
                  onClick={() => setPlanReviewEventIds((current) => current.includes(guidanceKey) ? current.filter((id) => id !== guidanceKey) : [...current, guidanceKey])}
                >
                  {inPlanReview ? "검토 목록에서 빼기" : "급여제공계획 검토 목록에 담기"}
                </button>
              ) : null}
              <button type="button" onClick={() => toggleEvidence(card.source_message_ids)}>
                {evidenceOpen ? "근거 접기" : "근거 보기"}
              </button>
              <button
                type="button"
                aria-pressed={staffConfirmed}
                disabled={!guidance.based_on_confirmed_facts}
                title={!guidance.based_on_confirmed_facts ? "확인 질문을 해결한 뒤 직원 확인할 수 있습니다." : undefined}
                onClick={() => setConfirmedGuidanceIds((current) => current.includes(guidanceKey) ? current.filter((id) => id !== guidanceKey) : [...current, guidanceKey])}
              >
                {staffConfirmed ? "직원 확인 취소" : "직원 확인 표시"}
              </button>
            </div>
          </section>
        ) : null}
        <details className="care-briefing-comparison">
          <summary>상태 비교와 판단 근거 보기</summary>
        <div className="record-usage-stamps">
          {card.document_candidate_types.map((candidateType) => (
            <span key={candidateType}>
              {documentCandidateLabels[candidateType]}
            </span>
          ))}
        </div>
        <dl className="care-briefing-glance">
          <div className="care-briefing-glance-wide">
            <dt>무슨 일</dt>
            <dd>{card.change_summary}</dd>
          </div>
          <div className="care-briefing-glance-wide">
            <dt>현재 상태</dt>
            <dd className={`care-final-status status-${card.final_status}`}>
              <span>{briefingFinalStatusLabels[card.final_status]}</span>
              {card.final_status_summary}
            </dd>
          </div>
          <div>
            <dt>이미 한 조치</dt>
            <dd>
              {card.completed_actions.length ? (
                <ul>
                  {card.completed_actions.map((action) => (
                    <li key={action}>{action}</li>
                  ))}
                </ul>
              ) : (
                "기록된 조치 없음"
              )}
            </dd>
          </div>
          <div className={card.pending_checks.length ? "has-pending" : ""}>
            <dt>다음 조치·확인</dt>
            <dd>
              {card.pending_checks.length ? (
                <ul>
                  {card.pending_checks.map((check) => (
                    <li key={check}>{check}</li>
                  ))}
                </ul>
              ) : (
                "현재 남은 확인 없음"
              )}
            </dd>
          </div>
          <div>
            <dt>기한·재확인</dt>
            <dd>{card.due_at ? formatDateTime(card.due_at) : "지정된 기한 없음"}</dd>
          </div>
          <div className={card.final_status === "needs_confirmation" ? "has-pending" : ""}>
            <dt>확인 필요</dt>
            <dd>
              {card.final_status === "needs_confirmation"
                ? "원문과 후속 결과를 직원이 확인한 뒤 확정"
                : card.pending_checks.length
                  ? "후속 확인 결과 기록 필요"
                  : "추가 확인 없음"}
            </dd>
          </div>
        </dl>
        {card.check_reasons.length ? (
          <details className="care-briefing-reasons">
            <summary>확인 필요 판단 근거 {card.check_reasons.length}건 보기</summary>
            <ul>
              {card.check_reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          </details>
        ) : null}
        </details>
        {eventDrafts.length ? (
          <div className="care-briefing-documents">
            <strong>AI가 연결한 기록·서류 후보</strong>
            <p>
              이 사건의 근거만 사용한 초안입니다. 새 창에서 원문·답글·사진을
              대조하고 직원 확인 후 활용합니다.
            </p>
            <div>
              {eventDrafts.map((draft) => (
                <span key={draft.key}>
                  {periodDocumentLabels[draft.document_type]}
                </span>
              ))}
            </div>
          </div>
        ) : null}
        <footer className={eventDrafts.length ? "has-document-draft" : ""}>
          <button
            type="button"
            aria-expanded={evidenceOpen}
            onClick={() => toggleEvidence(card.source_message_ids)}
          >
            {evidenceOpen
              ? "근거 대화 접기"
              : `근거 대화 ${card.source_message_ids.length}건 보기`}
          </button>
          {eventDrafts.length ? (
            <button
              type="button"
              onClick={() => {
                try {
                  openDocumentDraftWorkspace({
                    card,
                    drafts: eventDrafts,
                    sources: eventSources,
                    periodLabel,
                    onOpenSource,
                  });
                  setReviewStatus(
                    "기록·서류 초안 창을 열었습니다. 새 창에서 근거를 확인하고 직원 확인 후 활용하세요.",
                  );
                } catch (reason) {
                  setError(careBriefingErrorMessage(reason));
                }
              }}
            >
              기록·서류 초안 만들기
            </button>
          ) : null}
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
                  <p>
                    {messageSummary(
                      source.message.body,
                      source.message.attachments,
                    )}
                  </p>
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
                  {source.comments.length ? (
                    <div className="care-briefing-evidence-replies">
                      <strong>답글로 확인한 진행상황</strong>
                      {source.comments.map((comment) => (
                        <div key={comment.id}>
                          <span>
                            {comment.author_name} · {formatDateTime(comment.created_at)}
                          </span>
                          <p>{comment.body}</p>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="care-briefing-evidence-no-reply">
                      아직 답글로 확인된 진행상황이 없습니다.
                    </p>
                  )}
                  <button
                    type="button"
                    className="care-briefing-evidence-open"
                    onClick={() =>
                      void onOpenSource(source.message.room_id, source.message.id)
                    }
                  >
                    대화에서 확인·답글하기
                  </button>
                </article>
              );
            })}
          </div>
        ) : null}
        </article>
      </details>
    );
  }

  function renderCareBriefingOverview() {
    if (!review) return null;
    const selectedResident = residents.find(
      (resident) => resident.id === residentId,
    );
    return (
      <section className="care-briefing-overview">
        <div className="section-heading care-briefing-title">
          <div>
            <h3>
              {summaryMode === "resident" && selectedResident
                ? `${selectedResident.display_name} 요약`
                : "업무 전체 요약"}
            </h3>
            <p>
              {periodDescription}의 대화·답글·사진에서 미완료·충돌·후속조치를
              사건별로 짧게 정리합니다.
            </p>
          </div>
          {review.briefing.cards.length ? (
            <button type="button" className="button" onClick={printBriefing}>
              브리핑 인쇄·PDF
            </button>
          ) : null}
        </div>
        <div className="care-briefing-metrics">
          <span>
            <strong>
              {review.briefing.today_schedule_count +
                review.briefing.needs_attention_count}
            </strong>
            오늘 꼭 볼 것
          </span>
          <span>
            <strong>{review.briefing.pending_check_count}</strong>미완료·후속
          </span>
          <span>
            <strong>
              {
                review.briefing.cards.filter(
                  (card) => card.final_status === "needs_confirmation",
                ).length
              }
            </strong>
            충돌·확인 필요
          </span>
          <span>
            <strong>{review.briefing.completed_count}</strong>완료
          </span>
        </div>
        {review.truncated ? (
          <p className="form-warning">
            정리 대상이 많아 구간별 최대 500건까지만 분석했습니다.
            {review.truncated_periods.length
              ? ` 일부 생략 구간: ${review.truncated_periods.join(" · ")}`
              : " 선택 기간 이전 이월 또는 비교 기록 일부가 생략됐습니다."}
          </p>
        ) : null}
        {review.processed_periods.length > 1 ? (
          <p className="period-chunk-notice">
            {review.processed_periods.length}개 내부 구간의 구조화 사건을 병합하고
            동일 근거를 중복 제거했습니다. 장기 범위 원문 전체를 외부 AI에 한
            번에 보내지 않습니다.
          </p>
        ) : null}
        {review.briefing.cards.length === 0 ? (
          <p className="muted-box">
            {review.message_count === 0 && review.comment_count === 0
              ? "선택한 기간과 어르신에 연결된 대화·답글이 0건입니다. 오류가 아닙니다. 범위를 넓히거나 모든 어르신을 선택해 주세요."
              : "선택한 범위에서 돌봄 사건으로 분류된 기록이 없습니다. 아래 작성할 서류 선택에서 일반 업무를 확인할 수 있습니다."}
          </p>
        ) : (
          <>
            {briefingResidentGroups.map((group) => {
              const needsConfirmation = group.cards.filter(
                (card) => card.final_status === "needs_confirmation",
              ).length;
              const inProgress = group.cards.filter(
                (card) => card.final_status === "in_progress",
              ).length;
              const completed = group.cards.filter(
                (card) => card.final_status === "completed",
              ).length;
              return (
                <section
                  key={group.residentId || group.residentName}
                  className="care-briefing-resident-group"
                >
                  <div className="care-briefing-resident-heading">
                    <h4>{group.residentName}</h4>
                    <p>
                      확인 필요 {needsConfirmation} · 진행 중 {inProgress} · 완료{" "}
                      {completed}
                    </p>
                  </div>
                  <div className="care-briefing-row-list">
                    {group.cards.map(renderBriefingCard)}
                  </div>
                </section>
              );
            })}
          </>
        )}
      </section>
    );
  }

  function renderFieldEvidence(evidenceIds: string[]) {
    const evidence = evidenceIds
      .map((evidenceId) => sourceById.get(evidenceId))
      .filter((source) => source !== undefined);
    if (!evidence.length) return null;
    return (
      <details className="field-briefing-evidence">
        <summary>근거 보기 · {evidence.length}건</summary>
        <div>
          {evidence.map((source) => (
            <article key={source.message.id}>
              <p>
                {messageSummary(
                  source.message.body,
                  source.message.attachments,
                )}
              </p>
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
              <button
                type="button"
                className="care-briefing-evidence-open"
                onClick={() =>
                  void onOpenSource(source.message.room_id, source.message.id)
                }
              >
                대화에서 확인·답글하기
              </button>
            </article>
          ))}
        </div>
      </details>
    );
  }

  function renderFieldCareBriefing() {
    if (!review) return null;
    return (
      <section className="field-care-briefing" aria-label="현장 기록형 돌봄 브리핑">
        <header className="field-briefing-overall">
          <div>
            <span className="eyebrow">선택 기간 전체 요약</span>
            <h3>{review.overall_summary}</h3>
            <p>
              급여제공기록지 작성에 참고할 확인 내용을 정리한 브리핑입니다.
              실제 공식 기록은 직원이 기존 업무 방식대로 직접 작성합니다.
            </p>
          </div>
          {review.care_reference_count ? (
            <button type="button" className="button" onClick={printBriefing}>
              브리핑 인쇄·PDF
            </button>
          ) : null}
        </header>
        <div className="field-briefing-counts" aria-label="브리핑 참고 내용 수">
          <span>급여제공기록지 참고 {review.care_reference_count}건</span>
          {review.consultation_reference_count ? (
            <span>상담일지 참고 {review.consultation_reference_count}건</span>
          ) : null}
          {review.history_revision ? (
            <span>저장 이력 {review.history_revision}번</span>
          ) : null}
        </div>
        {review.truncated ? (
          <p className="form-warning">
            정리 대상이 많아 일부 구간은 최대 500건까지만 확인했습니다.
            {review.truncated_periods.length
              ? ` 생략 가능 구간: ${review.truncated_periods.join(" · ")}`
              : ""}
          </p>
        ) : null}
        {review.daily_care_references.length ? (
          <div className="field-briefing-days">
            {review.daily_care_references.map((day) => (
              <section key={day.date} className="field-briefing-day">
                <h3>{formatBriefingDay(day.date)}</h3>
                {day.residents.map((resident) => (
                  <section
                    key={resident.resident_id}
                    className="field-briefing-resident"
                  >
                    <h4>{resident.resident_name}</h4>
                    <div className="field-briefing-lines">
                      {resident.entries.map((entry) => (
                        <article
                          key={`${entry.event_group_id}-${entry.occurred_at}`}
                          className="field-briefing-line"
                        >
                          <p>
                            <time dateTime={entry.occurred_at}>
                              {entry.time_label}
                            </time>
                            <span>{entry.summary}</span>
                          </p>
                          {entry.conflict_note ? (
                            <small className="field-briefing-conflict">
                              {entry.conflict_note}
                            </small>
                          ) : null}
                          {renderFieldEvidence(entry.evidence_ids)}
                        </article>
                      ))}
                    </div>
                  </section>
                ))}
              </section>
            ))}
          </div>
        ) : (
          <p className="muted-box">
            선택한 기간과 범위에는 급여제공기록지 작성에 참고할 돌봄 내용이
            없습니다. 오류가 아니며 기간·채팅방·어르신 범위를 변경할 수 있습니다.
          </p>
        )}
        {review.consultation_references.length ? (
          <section className="field-briefing-consultations">
            <h3>상담일지 참고 내용</h3>
            <p>보호자와 실제 연락·설명·확인한 근거가 있는 내용만 표시합니다.</p>
            {review.consultation_references.map((entry) => (
              <article
                key={`${entry.event_group_id}-${entry.occurred_at}`}
                className="field-briefing-consultation-line"
              >
                <p>
                  <strong>{entry.resident_name}</strong>
                  <span>
                    {formatBriefingDay(entry.occurred_at.slice(0, 10))}{" "}
                    {entry.time_label} {entry.summary}
                  </span>
                </p>
                {renderFieldEvidence(entry.evidence_ids)}
              </article>
            ))}
          </section>
        ) : null}
        {reviewHistory.length ? (
          <details className="field-briefing-history">
            <summary>저장된 브리핑 다시 열기 · {reviewHistory.length}건</summary>
            <div>
              {reviewHistory.map((history) => (
                <button
                  type="button"
                  key={history.id}
                  disabled={historyLoading}
                  onClick={() => void openSavedReview(history)}
                >
                  <strong>
                    {history.period_start} ~ {history.period_end} · 이력{" "}
                    {history.revision}번
                  </strong>
                  <span>{history.overall_summary}</span>
                  <small>{formatDateTime(history.created_at)} 생성</small>
                </button>
              ))}
            </div>
          </details>
        ) : null}
        <p className="field-briefing-safety" role="note">
          브리핑과 생성 이력은 DEV에 참고자료로 저장됩니다. 급여제공기록지·상담일지
          공식 입력, 서명 또는 공단 전송은 실행하지 않습니다.
        </p>
      </section>
    );
  }

  if (!open) return null;

  return (
    <div
      className="drawer-layer period-workdesk-layer"
      role="dialog"
      aria-modal="true"
      aria-label="돌봄브리핑"
    >
      <button
        className="drawer-backdrop"
        onClick={onClose}
        aria-label="AI 돌봄 브리핑 닫기"
      />
      <aside className="workdesk-drawer period-workdesk-card desktop-resizable-dialog">
        <header className="drawer-header period-workdesk-header">
          <div>
            <h2>돌봄브리핑</h2>
            <p>중요한 돌봄 내용과 이후 경과를 확인합니다.</p>
          </div>
          <button
            className="icon-button"
            onClick={onClose}
            aria-label="AI 돌봄 브리핑 닫기"
          >
            ×
          </button>
        </header>

        <div className="period-workdesk-scroll" ref={scrollRef} onScroll={(event) => { scrollPositionRef.current = event.currentTarget.scrollTop; }}>
          <ResidentConfirmationInbox />
          <div className="care-reader-filters">
            <details className="care-period-selector">
              <summary><span>기간 선택</span><strong>{startDate === defaultStartDate && endDate === today ? "최근 7일" : periodLabel}</strong></summary>
              <div className="care-period-fields">
                <div className="period-quick-buttons">
                  <button type="button" disabled={loading} onClick={() => applyQuickRange("today")}>오늘</button>
                  <button type="button" disabled={loading} onClick={() => applyQuickRange("week")}>최근 7일</button>
                  <button type="button" disabled={loading} onClick={() => applyQuickRange("month")}>최근 1개월</button>
                  <button type="button" disabled={loading} onClick={() => applyQuickRange("quarter")}>최근 3개월</button>
                  <button type="button" disabled={loading} onClick={() => applyQuickRange("half-year")}>최근 6개월</button>
                </div>
                <label>시작일<input type="date" value={startDate} onInput={(event) => setStartDate(event.currentTarget.value)} onChange={(event) => setStartDate(event.target.value)} /></label>
                <label>종료일<input type="date" value={endDate} onInput={(event) => setEndDate(event.currentTarget.value)} onChange={(event) => setEndDate(event.target.value)} /></label>
                <label>채팅방<select value={roomId} onChange={(event) => setRoomId(event.target.value)}><option value="">모든 채팅방</option>{rooms.filter((room) => room.kind !== "self").map((room) => <option key={room.id} value={room.id}>{room.name}</option>)}</select></label>
                <button type="button" className="button button-primary" disabled={loading} onClick={() => void loadReview()}>이 기간으로 조회</button>
              </div>
            </details>
            <label className="care-resident-selector"><span>어르신 선택</span><select aria-label="어르신 선택" value={residentId} disabled={loading} onChange={(event) => changeResidentScope(event.target.value)}><option value="">전체 어르신</option>{residents.map((resident) => <option key={resident.id} value={resident.id}>{resident.display_name}</option>)}</select></label>
          </div>
          {residentLoadError ? <div className="form-error care-resident-load-error" role="alert"><p>{residentLoadError}</p><button type="button" disabled={residentLoading} onClick={() => void loadResidents()}>{residentLoading ? "목록 확인 중…" : "어르신 목록 다시 불러오기"}</button></div> : null}
          {reviewStatus && !error ? (
            <p className="period-review-status" role="status" aria-live="polite">
              {reviewStatus}
            </p>
          ) : null}

          {error ? (
            <div className="form-error period-review-error" role="alert">
              <p>{error}</p>
              <button
                type="button"
                disabled={loading}
                onClick={() => void loadReview()}
              >
                다시 시도
              </button>
            </div>
          ) : null}
          {review && reviewScope === loadedScope && !loading ? (
            <>
              {review.care_topics ? <CareBriefingReader
                key={loadedScope}
                topics={review.care_topics}
                sources={review.sources}
                sourceIds={review.source_ids}
                startDate={startDate}
                endDate={endDate}
                rangeMode={startDate === defaultStartDate && endDate === today ? "default" : "fixed"}
                roomId={roomId}
                messageType={messageType}
                residentId={residentId}
                residentName={residents.find((resident) => resident.id === residentId)?.display_name ?? review.care_topics.find((topic) => topic.resident_id === residentId)?.resident_name ?? ""}
                truncated={review.truncated}
                initialVisibleCount={visibleCounts[loadedScope] ?? 12}
                onVisibleCountChange={(count) => setVisibleCounts((current) => ({ ...current, [loadedScope]: count }))}
                onSelectResident={changeResidentScope}
                onOpenSource={onOpenSource}
                onCompare={() => { setPlanningResidentId(residentId); setComparisonMode(true); setShowPlanningHelper(true); }}
              /> : <p className="muted-box">이 저장본은 이전 형식입니다. <button type="button" onClick={() => void loadReview()}>현재 기간의 경과 조회</button></p>}
              {legacyDocumentSelectionVisible ? (
                <>
              {/* Retained only with the disabled legacy workflow. Stored records
                  and planning remain available through the comparison flow. */}
              {summaryMode === "resident" ? renderCareBriefingOverview() : renderFieldCareBriefing()}
              <section className="record-groups-section always-record-tools">
                <div className="section-heading">
                  <div>
                    <span className="record-workflow-step">1단계</span>
                    <h3>작성할 서류 선택</h3>
                    <p>
                      급여제공기록지 또는 상담일지를 선택하세요. 후보가 0건이어도
                      직원이 직접 선택할 수 있습니다.
                    </p>
                  </div>
                  <div className="record-filter-actions">
                    <button
                      type="button"
                      onClick={() => setAllDocumentCandidates(true)}
                    >
                      서류 모두 선택
                    </button>
                    <button
                      type="button"
                      onClick={() => setAllDocumentCandidates(false)}
                    >
                      서류 선택 해제
                    </button>
                  </div>
                </div>
                <div className="record-group-grid">
                  {alwaysAvailableDocumentCandidates.map((candidateType) => {
                    const recommendationCount =
                      review.document_candidate_counts[candidateType] ?? 0;
                    const reviewCount =
                      documentCandidateReviewCounts.get(candidateType) ?? 0;
                    const selected =
                      selectedDocumentCandidates.includes(candidateType);
                    return (
                      <button
                        type="button"
                        key={candidateType}
                        role="checkbox"
                        aria-checked={selected}
                        aria-label={`${documentCandidateLabels[candidateType]} · 추천 근거 ${recommendationCount}건${
                          reviewCount ? ` · 여부 확인 필요 ${reviewCount}건` : ""
                        }${recommendationCount === 0 ? " · 직원 수동 선택 가능" : ""}`}
                        className={selected ? "is-active" : ""}
                        onClick={() => toggleDocumentCandidate(candidateType)}
                      >
                        <span className="record-filter-check" aria-hidden="true">
                          {selected ? "✓" : ""}
                        </span>
                        <strong>{documentCandidateLabels[candidateType]}</strong>
                        <span>추천 근거 {recommendationCount}건</span>
                        <small>{documentCandidateDescriptions[candidateType]}</small>
                        {reviewCount ? (
                          <small className="record-candidate-review-count">
                            여부 확인 필요 {reviewCount}건
                          </small>
                        ) : recommendationCount === 0 && selected ? (
                          <small className="record-candidate-manual-status">
                            직원 수동 선택 · 자동 추천 아님
                          </small>
                        ) : null}
                      </button>
                    );
                  })}
                </div>
                {recordSelectionStatus ? (
                  <p className="record-selection-status" role="status" aria-live="polite">
                    {recordSelectionStatus}
                  </p>
                ) : null}
                <p className="record-selection-safety">
                  선택을 해제해도 원본 대화와 기록은 삭제되지 않습니다.
                </p>
              </section>

              {selectedDocumentCandidates.length ? (
                <section id="record-group-detail" className="record-group-detail">
                  <div className="section-heading">
                    <div>
                      <span className="record-workflow-step">2단계</span>
                      <h3>서류에 넣을 내용 선택</h3>
                      <p>
                        1단계에서 {selectedRecordLabel()}를 선택했습니다. 이 서류에
                        넣을 대화와 기록을 아래에서 선택해 주세요.
                      </p>
                    </div>
                    <span>{activeEvents.length}건</span>
                  </div>
                  <div className="record-group-actions">
                    <button
                      type="button"
                      onClick={() => {
                        setSelectedEventIds(
                          activeEvents.map((event) => event.event_group_id),
                        );
                        setRecordSummary(null);
                        setRecordActionStatus(
                          `서류에 넣을 내용 ${activeEvents.length}건을 모두 선택했습니다. 내용 요약 또는 원문 인쇄를 실행할 수 있습니다.`,
                        );
                      }}
                    >
                      내용 모두 선택
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setSelectedEventIds([]);
                        setRecordSummary(null);
                        setRecordActionStatus(
                          "서류에 넣을 내용 선택을 해제했습니다. 선택을 해제해도 원본 대화와 기록은 삭제되지 않습니다.",
                        );
                      }}
                    >
                      내용 선택 해제
                    </button>
                    <button
                      type="button"
                      className="button-primary"
                      disabled={!selectedEventIds.length || recordSummaryLoading}
                      aria-describedby="record-group-action-status record-summary-safety-note"
                      onClick={() => void summarizeSelectedEvents()}
                    >
                      {recordSummaryLoading ? "요약 만드는 중…" : "선택한 내용 요약 만들기"}
                    </button>
                    {recordSummaryLoading ? (
                      <button type="button" onClick={() => recordSummaryControllerRef.current?.abort()}>
                        요약 요청 취소
                      </button>
                    ) : null}
                    <button
                      type="button"
                      disabled={!selectedEventIds.length}
                      aria-describedby="record-group-action-status record-selection-safety-note"
                      onClick={printSelectedRaw}
                    >
                      선택한 내용 원문 인쇄·PDF
                    </button>
                    {recordSummary ? (
                      <button
                        type="button"
                        className="record-result-button"
                        aria-controls="record-ai-result"
                        onClick={focusRecordSummaryResult}
                      >
                        결과 확인하기
                      </button>
                    ) : null}
                  </div>
                  <p id="record-selection-safety-note" className="record-selection-safety">
                    선택을 해제해도 원본 대화와 기록은 삭제되지 않습니다.
                  </p>
                  <p id="record-summary-safety-note" className="record-summary-safety">
                    이 버튼은 선택한 내용의 업무 요약을 만듭니다. 서류 초안이나
                    공식 기록에 자동 저장되지 않습니다. 직원이 원문과 대조해
                    수정하고 최종 확인해 주세요.
                  </p>
                  <p
                    id="record-group-action-status"
                    className="record-action-status"
                    role="status"
                    aria-live="polite"
                  >
                    {recordActionStatus ||
                      (selectedEventIds.length
                        ? `서류에 넣을 내용 ${selectedEventIds.length}건을 선택했습니다. 내용 요약 또는 원문 인쇄를 실행할 수 있습니다.`
                        : "서류에 넣을 대화와 기록을 한 건 이상 선택하면 내용 요약과 원문 인쇄 버튼이 활성화됩니다.")}
                  </p>
                  {activeEvents.length === 0 ? (
                    <p className="muted-box">이 기록에 해당하는 대화가 없습니다.</p>
                  ) : (
                    <div className="record-event-list">
                      {activeEvents.map((event, eventIndex) => {
                        const eventSummary = compactBriefingText(
                          event.summary,
                          event.resident_name ?? undefined,
                        );
                        const selectionReasons = eventDocumentSelectionReasons(event);
                        const needsSourceConfirmation = selectionReasons.some(
                          (reason) => reason.includes("확인 필요"),
                        );
                        return (
                        <details key={event.event_group_id} className="record-event-card">
                          <summary>
                            <input
                              type="checkbox"
                              aria-label={`${eventIndex + 1}번 · ${formatCompactDateTime(
                                event.latest_at,
                              )} · ${eventSummary || "사건 요약 확인 필요"} 선택`}
                              checked={selectedEventIds.includes(event.event_group_id)}
                              onClick={(clickEvent) => clickEvent.stopPropagation()}
                              onChange={(changeEvent) =>
                                setSelectedEventIds((current) => {
                                  const next = changeEvent.target.checked
                                    ? [...new Set([...current, event.event_group_id])]
                                    : current.filter((id) => id !== event.event_group_id);
                                  setRecordSummary(null);
                                  setRecordActionStatus(
                                    next.length
                                      ? `서류에 넣을 내용 ${next.length}건을 선택했습니다. 내용 요약 또는 원문 인쇄를 실행할 수 있습니다.`
                                      : "서류에 넣을 내용 선택을 해제했습니다. 선택을 해제해도 원본 대화와 기록은 삭제되지 않습니다.",
                                  );
                                  return next;
                                })
                              }
                            />
                            <span className="record-event-number">{eventIndex + 1}</span>
                            <span className="record-event-copy">
                              <strong>{event.resident_name ?? "일반 업무"}</strong>
                              <span className="record-event-summary">
                                {eventSummary || "사건 요약을 펼쳐 확인해 주세요."}
                              </span>
                              <small>
                                {event.room_names.join(", ")} ·{" "}
                                {formatDateTime(event.latest_at)}
                              </small>
                              {selectionReasons.length ? (
                                <small className="record-event-selection-reason">
                                  {selectionReasons.join(" / ")}
                                </small>
                              ) : null}
                              {needsSourceConfirmation ? (
                                <small className="record-event-review-state">
                                  확인되지 않은 수치는 기록에 확정하지 않습니다. 원문 확인 후
                                  작성해 주세요.
                                </small>
                              ) : null}
                            </span>
                            <span className="record-event-evidence-count">
                              근거 {event.evidence_ids.length}건
                            </span>
                          </summary>
                          <div className="record-event-body">
                            <p>{event.summary}</p>
                            <div className="record-usage-stamps">
                              {event.document_candidate_types.map((candidateType) => (
                                <span key={candidateType}>
                                  {documentCandidateLabels[candidateType]}
                                </span>
                              ))}
                            </div>
                            {event.evidence_ids.map((evidenceId, evidenceIndex) => {
                              const source = sourceById.get(evidenceId);
                              if (!source) return null;
                              return (
                                <article key={evidenceId} className="record-evidence">
                                  <strong>근거 {evidenceIndex + 1}</strong>
                                  <p>
                                    {messageSummary(
                                      source.message.body,
                                      source.message.attachments,
                                    )}
                                  </p>
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
                        );
                      })}
                    </div>
                  )}
                  {recordSummary ? (
                    <article
                      id="record-ai-result"
                      ref={recordAiResultRef}
                      tabIndex={-1}
                      aria-labelledby="record-ai-result-title"
                      className="record-ai-result"
                    >
                      <header>
                        <div>
                          <span className="eyebrow">AI 도움 결과</span>
                          <h3 id="record-ai-result-title">선택한 내용 요약</h3>
                        </div>
                        <button type="button" onClick={printAiSummary}>
                          요약 인쇄·PDF
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
                      <section
                        className="record-ai-draft-links"
                        aria-labelledby="record-ai-draft-links-title"
                      >
                        <strong id="record-ai-draft-links-title">
                          3단계 · 서류 초안에서 수정·확인
                        </strong>
                        <p>
                          위 결과는 일반 업무 요약입니다. 아래 문서별 편집 초안에서
                          원문을 대조하고 수정한 뒤 직원이 최종 확인하세요. 두 서류를
                          선택한 경우에도 각각 별도 초안으로 엽니다.
                        </p>
                        {recordSummaryDocumentDrafts.length ? (
                          <div>
                            {recordSummaryDocumentDrafts.map((draft) => (
                              <button
                                type="button"
                                key={draft.key}
                                aria-label={`${draft.resident_name} ${periodDocumentLabels[draft.document_type]} 편집 초안 열기`}
                                onClick={() => openRecordSummaryDraft(draft)}
                              >
                                {recordSummaryDocumentDrafts.filter(
                                  (candidate) =>
                                    candidate.document_type === draft.document_type,
                                ).length > 1
                                  ? `${draft.resident_name} · `
                                  : ""}
                                {periodDocumentLabels[draft.document_type]} 편집 초안 열기
                              </button>
                            ))}
                          </div>
                        ) : (
                          <small>
                            어르신과 연결된 근거를 선택해야 문서별 편집 초안을 만들 수
                            있습니다. 요약 자체는 수정 가능한 공식 서류가 아닙니다.
                          </small>
                        )}
                        <small>
                          초안은 공식 기록에 자동 저장되지 않으며 직원 확인 전
                          인쇄·PDF 버튼이 잠겨 있습니다.
                        </small>
                      </section>
                      <small>
                        근거 {recordSummary.evidence_ids.length}건 · 원문에 없는
                        사실과 공식 평가점수는 만들지 않습니다.
                      </small>
                      <small>
                        공식 기록에 자동 저장되지 않습니다. 직원이 원문 대조·수정·최종
                        확인해 주세요.
                      </small>
                      <small>{recordSummaryExecutionLabel(recordSummary)}</small>
                    </article>
                  ) : null}
                </section>
              ) : null}
                </>
              ) : null}

              {showPlanningHelper ? <section className="care-planning-launcher" ref={planningRef}>
                <div className="section-heading"><h3>{comparisonMode ? "기존 서류와 비교" : "기존 작성본·수정 이력"}</h3><button type="button" onClick={togglePlanningHelper}>닫기</button></div>
                <label className="care-planning-resident-picker"><span>서류의 어르신</span><select aria-label="도우미 작성 대상" value={planningResidentId} onChange={(event) => setPlanningResidentId(event.target.value)}><option value="">어르신을 선택해 주세요</option>{residents.map((resident) => <option key={resident.id} value={resident.id}>{resident.display_name}</option>)}</select></label>
                <ResidentCarePlanningPanel key={`${planningResidentId}-${comparisonMode}`} resident={planningResident} initialWorkflowStage={comparisonMode ? 2 : 1} comparisonTopics={comparisonMode ? [...new Set((review.care_topics ?? []).map((topic) => topic.topic))] : undefined} />
              </section> : null}

            </>
          ) : (
            <p className="muted-box">
              {loading ? "기록 조회 중 → 경과 정리 중 → 화면 준비 중" : "기간을 변경했습니다. ‘이 기간으로 조회’를 눌러 주세요."}
            </p>
          )}
        </div>
      </aside>
    </div>
  );
}
