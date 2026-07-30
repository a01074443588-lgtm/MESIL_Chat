"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiFetch } from "../api";
import type {
  ActionItem,
  Attachment,
  DailyDocumentType,
  Message,
  RecordClassification,
  RecordDraft,
  Resident,
  RiskLevel,
  RoomDigest,
  TargetRole,
  WorkItem,
  WorkItemStatus,
} from "../types";
import { AttachmentDisplay } from "./AttachmentDisplay";
import { DocumentCandidateDashboard } from "./DocumentCandidateDashboard";

const statusLabels: Record<WorkItemStatus, string> = {
  pending: "확인 필요",
  in_review: "검토 중",
  ready: "승인 완료",
  dismissed: "사용 안 함",
};

const documentLabels: Record<string, string> = {
  care_service_record: "급여제공기록지",
  nursing_log: "간호일지",
  consultation_log: "상담일지",
  physical_restraint_log: "신체제재 기록지",
  program_log: "프로그램 운영기록지",
};

const classificationLabels: Record<RecordClassification, string> = {
  daily_care: "일상생활 지원",
  nutrition: "식사·영양",
  health: "건강·간호",
  safety: "안전·사고",
  consultation: "상담·보호자",
  rehabilitation: "재활·치료",
};

const riskLabels: Record<RiskLevel, string> = {
  low: "낮음",
  medium: "관찰 필요",
  high: "높음",
  urgent: "긴급",
};

function residentServiceLabel(serviceType: string) {
  if (serviceType === "facility") return "시설";
  if (serviceType === "daycare") return "주간보호";
  if (serviceType === "homecare") return "방문요양";
  return serviceType;
}

type OcrReviewDecision =
  | "keep_raw"
  | "apply_candidate"
  | "direct_edit"
  | "needs_review";

const ocrDecisionLabels: Record<OcrReviewDecision, string> = {
  keep_raw: "원문 그대로 확정",
  apply_candidate: "교정 후보 적용",
  direct_edit: "직접 수정 확정",
  needs_review: "확인 필요",
};

const targetRoleLabels: Record<TargetRole, string> = {
  caregiver: "요양보호사",
  nurse: "간호",
  social_worker: "사회복지사",
  director: "시설장",
  therapist: "치료사",
  nutritionist: "영양사",
};

function aiGeneratorLabel(value: string | null) {
  if (!value || value === "prototype-rule-v1") return "기초 분류 규칙";
  if (value.startsWith("nvidia:")) return "Nemotron API";
  if (value === "ollama:qwen3.6:35b") return "로컬 Qwen 35B";
  if (value === "ollama:gemma4:e4b") return "로컬 Gemma 4";
  if (value.startsWith("ollama:")) return "로컬 AI";
  if (value.startsWith("stub:")) return "자동화 시험 AI";
  return value;
}

const emptyDraft: RecordDraft = {
  corrected_text: "",
  summary: "",
  observation_details: "",
  actions_taken: [],
  resident_response: "",
  handover_summary: "",
  verification_questions: [],
  classification: "daily_care",
  risk_level: "low",
  target_roles: ["social_worker"],
  document_types: ["care_service_record"],
  keywords: [],
  document_drafts: [],
};

type WorkdeskStatusFilter = "review" | "ready" | "dismissed" | "all";

function matchesStatusFilter(item: WorkItem, filter: WorkdeskStatusFilter) {
  if (filter === "all") return true;
  if (filter === "review") {
    return item.status === "pending" || item.status === "in_review";
  }
  return item.status === filter;
}

export function WorkDesk({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [items, setItems] = useState<WorkItem[]>([]);
  const [residents, setResidents] = useState<Resident[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selectedIdRef = useRef<string | null>(null);
  const [draft, setDraft] = useState<RecordDraft>(emptyDraft);
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [ocrBusyId, setOcrBusyId] = useState<string | null>(null);
  const [ocrDrafts, setOcrDrafts] = useState<Record<string, string>>({});
  const [ocrCandidateSelections, setOcrCandidateSelections] = useState<
    Record<string, string>
  >({});
  const [documentDraftEdits, setDocumentDraftEdits] = useState<
    Record<string, string>
  >({});
  const [documentChangeRequests, setDocumentChangeRequests] = useState<
    Record<string, string>
  >({});
  const [documentBusyType, setDocumentBusyType] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [view, setView] = useState<"summary" | "inbox" | "actions" | "candidates">(
    "inbox",
  );
  const [statusFilter, setStatusFilter] =
    useState<WorkdeskStatusFilter>("review");
  const [digests, setDigests] = useState<RoomDigest[]>([]);
  const [digestPeriod, setDigestPeriod] = useState<"day" | "week" | "month">("day");
  const [selectedDigestId, setSelectedDigestId] = useState<string | null>(null);
  const [actionItems, setActionItems] = useState<ActionItem[]>([]);
  const selected = useMemo(
    () => items.find((item) => item.id === selectedId) ?? null,
    [items, selectedId],
  );
  const selectedDigest = useMemo(
    () => digests.find((digest) => digest.id === selectedDigestId) ?? digests[0] ?? null,
    [digests, selectedDigestId],
  );
  const visibleItems = useMemo(
    () => items.filter((item) => matchesStatusFilter(item, statusFilter)),
    [items, statusFilter],
  );
  const applyItem = useCallback((item: WorkItem) => {
    selectedIdRef.current = item.id;
    setSelectedId(item.id);
    const source = item.confirmed_record ?? item.ai_suggestion;
    setDraft(
      source
        ? {
            corrected_text: source.corrected_text,
            summary: source.summary,
            observation_details: source.observation_details,
            actions_taken: source.actions_taken,
            resident_response: source.resident_response,
            handover_summary: source.handover_summary,
            verification_questions: source.verification_questions,
            classification: source.classification,
            risk_level: source.risk_level,
            target_roles: source.target_roles,
            document_types: source.document_types,
            keywords: source.keywords,
            document_drafts: source.document_drafts,
          }
        : {
            ...emptyDraft,
            corrected_text: item.source_snapshot.body,
            summary: "",
          },
    );
    setNotes(item.confirmed_record?.reviewer_notes ?? item.processing_notes ?? "");
    setOcrDrafts(
      Object.fromEntries(
        item.message.attachments
          .filter((attachment) => attachment.text_extraction)
          .map((attachment) => [
            attachment.id,
            attachment.text_extraction?.reviewed_text ??
              attachment.text_extraction?.extracted_text ??
              "",
          ]),
      ),
    );
    setOcrCandidateSelections({});
    setDocumentDraftEdits(
      Object.fromEntries(
        item.document_drafts.map((documentDraft) => [
          documentDraft.document_type,
          documentDraft.content,
        ]),
      ),
    );
    setDocumentChangeRequests({});
    setError("");
    setSuccess("");
  }, []);

  const clearSelectedItem = useCallback(() => {
    selectedIdRef.current = null;
    setSelectedId(null);
    setDraft(emptyDraft);
    setNotes("");
    setOcrDrafts({});
    setOcrCandidateSelections({});
    setDocumentDraftEdits({});
    setDocumentChangeRequests({});
    setError("");
    setSuccess("");
  }, []);

  function replaceItem(updated: WorkItem) {
    setItems((current) =>
      current.map((item) => (item.id === updated.id ? updated : item)),
    );
    applyItem(updated);
  }

  const reloadWorkItems = useCallback(
    async (preferredId = selectedIdRef.current) => {
      const nextItems = await apiFetch<WorkItem[]>("/api/work-items");
      setItems(nextItems);
      const preferred =
        nextItems.find(
          (item) =>
            item.id === preferredId &&
            matchesStatusFilter(item, statusFilter),
        ) ??
        nextItems.find((item) => matchesStatusFilter(item, statusFilter)) ??
        null;
      if (preferred) applyItem(preferred);
      else clearSelectedItem();
      return preferred;
    },
    [applyItem, clearSelectedItem, statusFilter],
  );

  function replaceAttachment(updated: Attachment) {
    setItems((current) =>
      current.map((item) => ({
        ...item,
        message: {
          ...item.message,
          attachments: item.message.attachments.map((attachment) =>
            attachment.id === updated.id ? updated : attachment,
          ),
        },
      })),
    );
    setOcrDrafts((current) => ({
      ...current,
      [updated.id]:
        updated.text_extraction?.reviewed_text ??
        updated.text_extraction?.extracted_text ??
        "",
    }));
    setOcrCandidateSelections((current) => {
      const next = { ...current };
      delete next[updated.id];
      return next;
    });
  }

  async function requestTextExtraction(attachment: Attachment) {
    const isAudio = attachment.mime_type.startsWith("audio/");
    setOcrBusyId(attachment.id);
    setError("");
    setSuccess("");
    try {
      const queued = await apiFetch<Attachment>(
        `/api/attachments/${attachment.id}/text-extraction`,
        { method: "POST", body: "{}" },
      );
      replaceAttachment(queued);
      setSuccess(
        isAudio
          ? "로컬 음성 받아쓰기를 시작했습니다. 채팅 전송과는 별도로 처리됩니다."
          : "로컬 글자 판독을 시작했습니다. 채팅 전송과는 별도로 처리됩니다.",
      );
      for (let attempt = 0; attempt < 45; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 2_000));
        const refreshed = await reloadWorkItems();
        const nextAttachment = refreshed?.message.attachments.find(
          (candidate) => candidate.id === attachment.id,
        );
        if (
          nextAttachment?.text_extraction &&
          !["pending", "processing"].includes(nextAttachment.text_extraction.status)
        ) {
          setSuccess(
            nextAttachment.text_extraction.status === "failed"
              ? `${isAudio ? "받아쓰기" : "판독"}에 실패했습니다. 원인 안내를 확인해 주세요.`
              : isAudio
                ? "음성 받아쓰기가 끝났습니다. 원본 음성을 들으며 확인해 주세요."
                : "글자 판독이 끝났습니다. 원본 이미지와 대조해 확인해 주세요.",
          );
          break;
        }
      }
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : `${isAudio ? "음성 받아쓰기" : "이미지 글자 판독"}를 시작하지 못했습니다.`,
      );
    } finally {
      setOcrBusyId(null);
    }
  }

  function applyOcrCandidate(
    attachment: Attachment,
    candidate: NonNullable<Attachment["text_extraction"]>["spelling_candidates"][number],
  ) {
    const current =
      ocrDrafts[attachment.id] ??
      attachment.text_extraction?.extracted_text ??
      "";
    setOcrDrafts((drafts) => ({
      ...drafts,
      [attachment.id]: current.split(candidate.recognized).join(candidate.candidate),
    }));
    setOcrCandidateSelections((selections) => ({
      ...selections,
      [attachment.id]: candidate.id,
    }));
    setError("");
    setSuccess(
      candidate.is_protected
        ? "보호 항목 후보를 교정문에 넣었습니다. 이름·시간·약·신체 부위·사건은 원본과 반드시 대조해 주세요."
        : "후보를 교정문에 넣었습니다. 원본과 대조한 뒤 확정 저장해 주세요.",
    );
  }

  async function saveReviewedText(
    attachment: Attachment,
    requestedDecision?: OcrReviewDecision,
  ) {
    const isAudio = attachment.mime_type.startsWith("audio/");
    const extraction = attachment.text_extraction;
    const originalText =
      extraction?.original_extracted_text ?? extraction?.extracted_text ?? "";
    const candidateSelection = ocrCandidateSelections[attachment.id];
    const decision: OcrReviewDecision = isAudio
      ? "direct_edit"
      : requestedDecision ??
        (candidateSelection ? "apply_candidate" : "direct_edit");
    const reviewedText =
      decision === "keep_raw"
        ? originalText.trim()
        : decision === "needs_review"
          ? null
          : ocrDrafts[attachment.id]?.trim();
    if (decision !== "needs_review" && !reviewedText) {
      setError(
        isAudio
          ? "확인한 받아쓰기 내용을 입력해 주세요."
          : "확인한 판독문을 입력해 주세요.",
      );
      return;
    }
    setOcrBusyId(attachment.id);
    setError("");
    setSuccess("");
    try {
      const updated = await apiFetch<Attachment>(
        `/api/attachments/${attachment.id}/text-extraction`,
        {
          method: "PATCH",
          body: JSON.stringify({
            reviewed_text: reviewedText,
            decision,
            selected_candidate_id:
              decision === "apply_candidate" ? candidateSelection : null,
          }),
        },
      );
      replaceAttachment(updated);
      await reloadWorkItems();
      setSuccess(
        decision === "needs_review"
          ? "확인 필요로 남겼습니다. 이 내용은 다음 OCR 후보 학습에 사용되지 않습니다."
          : isAudio
          ? "원본 음성과 대조한 받아쓰기 내용을 저장했습니다."
          : `${ocrDecisionLabels[decision]} 결과를 저장했습니다.`,
      );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : `확인한 ${isAudio ? "받아쓰기 내용" : "판독문"}을 저장하지 못했습니다.`,
      );
    } finally {
      setOcrBusyId(null);
    }
  }

  async function reviewResidentLink(
    residentId: string,
    status: "confirmed" | "rejected",
  ) {
    if (!selected) return;
    setSaving(true);
    setError("");
    setSuccess("");
    try {
      await apiFetch<Message>(
        `/api/messages/${selected.message.id}/resident-links/${residentId}`,
        {
          method: "PATCH",
          body: JSON.stringify({ status }),
        },
      );
      await reloadWorkItems(selected.id);
      setSuccess(
        status === "confirmed"
          ? "어르신 연결을 확인했습니다."
          : "잘못 찾은 어르신 후보를 제외했습니다.",
      );
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "어르신 연결을 변경하지 못했습니다.",
      );
    } finally {
      setSaving(false);
    }
  }

  async function replaceResident(residentId: string) {
    if (!selected || !residentId || residentId === selected.resident?.id) return;
    setSaving(true);
    setError("");
    setSuccess("");
    try {
      const updated = await apiFetch<WorkItem>(
        `/api/work-items/${selected.id}/resident`,
        {
          method: "PATCH",
          body: JSON.stringify({ resident_id: residentId }),
        },
      );
      replaceItem(updated);
      setStatusFilter(nextStatus === "dismissed" ? "dismissed" : "review");
      setSuccess(
        `${updated.resident?.display_name ?? "선택한 어르신"}으로 연결을 변경했습니다. 기존 AI 초안은 다시 만들어 주세요.`,
      );
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "연결된 어르신을 변경하지 못했습니다.",
      );
    } finally {
      setSaving(false);
    }
  }

  useEffect(() => {
    if (!open || view !== "summary") return;
    Promise.all([
      apiFetch<RoomDigest[]>(`/api/workdesk/room-digests?period=${digestPeriod}`),
      apiFetch<ActionItem[]>("/api/action-items"),
    ])
      .then(([nextDigests, nextActions]) => {
        setDigests(nextDigests);
        setActionItems(nextActions);
        setSelectedDigestId(nextDigests[0]?.id ?? null);
      })
      .catch((reason) => {
        setError(reason instanceof Error ? reason.message : "업무함을 열지 못했습니다.");
      });
  }, [digestPeriod, open, view]);

  useEffect(() => {
    if (!open || view !== "inbox") return;
    const timer = window.setTimeout(() => {
      void Promise.all([
        reloadWorkItems(),
        apiFetch<Resident[]>("/api/workdesk/residents"),
      ])
        .then(([, nextResidents]) => setResidents(nextResidents))
        .catch((reason) => {
          setError(
            reason instanceof Error ? reason.message : "처리할 대화를 불러오지 못했습니다.",
          );
        });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [open, reloadWorkItems, view]);

  useEffect(() => {
    if (!open || view !== "actions") return;
    void apiFetch<ActionItem[]>("/api/action-items")
      .then(setActionItems)
      .catch((reason) => {
        setError(
          reason instanceof Error ? reason.message : "인수인계 업무를 불러오지 못했습니다.",
        );
      });
  }, [open, view]);

  useEffect(() => {
    if (
      !open ||
      !items.some((item) =>
        item.message.attachments.some((attachment) =>
          ["pending", "processing"].includes(
            attachment.text_extraction?.status ?? "",
          ),
        ),
      )
    ) {
      return;
    }
    const timer = window.setInterval(() => {
      void apiFetch<WorkItem[]>("/api/work-items")
        .then((nextItems) => {
          setItems(nextItems);
          setOcrDrafts((current) => {
            const next = { ...current };
            nextItems.forEach((item) =>
              item.message.attachments.forEach((attachment) => {
                const extraction = attachment.text_extraction;
                if (
                  extraction &&
                  ["completed", "reviewed"].includes(extraction.status) &&
                  !next[attachment.id]
                ) {
                  next[attachment.id] =
                    extraction.reviewed_text ?? extraction.extracted_text ?? "";
                }
              }),
            );
            return next;
          });
        })
        .catch(() => {
          // 자동 갱신 실패는 기존 화면을 유지하고 수동 재시도를 허용합니다.
        });
    }, 2_500);
    return () => window.clearInterval(timer);
  }, [items, open]);

  async function updateActionStatus(
    item: ActionItem,
    status: ActionItem["status"],
  ) {
    setSaving(true);
    setError("");
    try {
      const updated = await apiFetch<ActionItem>(`/api/action-items/${item.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      });
      setActionItems((current) =>
        current.map((candidate) => (candidate.id === updated.id ? updated : candidate)),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "업무 상태를 변경하지 못했습니다.");
    } finally {
      setSaving(false);
    }
  }

  async function prepareAiDraft() {
    if (!selected) return;
    setGenerating(true);
    setError("");
    setSuccess("");
    try {
      for (const attachment of selected.message.attachments) {
        if (
          !attachment.mime_type.startsWith("image/") &&
          !attachment.mime_type.startsWith("audio/")
        ) {
          continue;
        }
        const extraction = attachment.text_extraction;
        if (
          !extraction ||
          ["pending", "processing"].includes(extraction.status)
        ) {
          throw new Error("음성·이미지 판독이 끝난 뒤 다시 눌러 주세요.");
        }
        if (extraction.status === "failed") {
          throw new Error(
            `${attachment.original_name} 판독에 실패했습니다. 아래의 다시 판독 기능을 이용해 주세요.`,
          );
        }
        const reviewedText = ocrDrafts[attachment.id]?.trim();
        if (!reviewedText) {
          throw new Error(
            `${attachment.mime_type.startsWith("audio/") ? "받아쓰기" : "판독문"} 내용을 확인해 주세요.`,
          );
        }
        const candidateSelection = ocrCandidateSelections[attachment.id];
        if (
          extraction.status !== "reviewed" ||
          extraction.reviewed_text?.trim() !== reviewedText ||
          candidateSelection
        ) {
          setOcrBusyId(attachment.id);
          await apiFetch<Attachment>(
            `/api/attachments/${attachment.id}/text-extraction`,
            {
              method: "PATCH",
              body: JSON.stringify({
                reviewed_text: reviewedText,
                decision: candidateSelection ? "apply_candidate" : "direct_edit",
                selected_candidate_id: candidateSelection ?? null,
              }),
            },
          );
        }
      }
      const refreshed = await reloadWorkItems(selected.id);
      if (!refreshed?.resident) {
        throw new Error("위의 어르신 선택에서 대상 어르신을 먼저 지정해 주세요.");
      }
      if (
        refreshed.message.resident_links.some(
          (link) => link.status === "candidate",
        )
      ) {
        throw new Error(
          "새로 찾은 어르신 후보가 있습니다. 위에서 맞음 또는 제외를 선택한 뒤 다시 눌러 주세요.",
        );
      }
      const updated = await apiFetch<WorkItem>(
        `/api/work-items/${refreshed.id}/ai-review`,
        { method: "POST", body: JSON.stringify({}) },
      );
      replaceItem(updated);
      setStatusFilter("ready");
      setSuccess(
        updated.ai_state === "ai_reviewed"
          ? "수정한 판독문과 어르신을 반영해 AI 업무초안을 만들었습니다."
          : "AI 연결이 되지 않아 기초 업무초안을 만들었습니다. 내용을 확인해 주세요.",
      );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "AI로 초안을 정리하지 못했습니다. 기존 내용은 보존됩니다.",
      );
    } finally {
      setOcrBusyId(null);
      setGenerating(false);
    }
  }

  async function updateDocumentDraft(
    documentType: DailyDocumentType,
    action: "regenerate" | "change_request" | "not_used",
  ) {
    if (!selected) return;
    setDocumentBusyType(documentType);
    setError("");
    setSuccess("");
    try {
      const updated = await apiFetch<WorkItem>(
        `/api/work-items/${selected.id}/document-drafts/${documentType}`,
        {
          method: "PATCH",
          body: JSON.stringify({
            action,
            content: null,
            change_request:
              action === "change_request"
                ? documentChangeRequests[documentType]?.trim() || null
                : null,
            verification_acknowledged: true,
          }),
        },
      );
      replaceItem(updated);
      setStatusFilter("review");
      setSuccess(
        action === "not_used"
          ? `${documentLabels[documentType]} 초안을 이번 업무에서 제외했습니다.`
          : `${documentLabels[documentType]} 초안을 새 버전으로 만들었습니다.`,
      );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : `${documentLabels[documentType]} 초안을 처리하지 못했습니다.`,
      );
    } finally {
      setDocumentBusyType(null);
    }
  }

  function printDocumentDraft(documentType: DailyDocumentType) {
    if (!selected) return;
    const documentDraft = selected.document_drafts.find(
      (candidate) => candidate.document_type === documentType,
    );
    if (!documentDraft || documentDraft.status !== "approved") {
      setError("시설장 승인 후 출력할 수 있습니다.");
      return;
    }
    const printWindow = window.open("", "_blank", "width=860,height=900");
    if (!printWindow) {
      setError("출력 창이 차단되었습니다. 브라우저의 팝업 허용을 확인해 주세요.");
      return;
    }
    const documentNode = printWindow.document;
    documentNode.title = `${documentLabels[documentType]} - ${
      selected.resident?.display_name ?? "어르신"
    }`;
    const style = documentNode.createElement("style");
    style.textContent = `
      body { margin: 32px; color: #162f38; font-family: Arial, "Malgun Gothic", sans-serif; }
      h1 { margin: 0 0 10px; font-size: 24px; }
      .meta { margin-bottom: 24px; color: #5d706f; font-size: 13px; line-height: 1.7; }
      pre { margin: 0; border: 1px solid #ccd9d6; border-radius: 12px; padding: 20px;
        font: 15px/1.8 Arial, "Malgun Gothic", sans-serif; white-space: pre-wrap; }
      .approval { margin-top: 24px; border-top: 1px solid #ccd9d6; padding-top: 14px;
        color: #405451; font-size: 13px; }
      @media print { body { margin: 18mm; } }
    `;
    documentNode.head.appendChild(style);
    const title = documentNode.createElement("h1");
    title.textContent = documentLabels[documentType];
    const meta = documentNode.createElement("div");
    meta.className = "meta";
    meta.textContent = [
      `대상: ${selected.resident?.display_name ?? "어르신 확인 필요"}`,
      `원문 대화방: ${selected.room_name}`,
      `원문 작성일: ${new Date(selected.source_snapshot.created_at).toLocaleString(
        "ko-KR",
      )}`,
      `초안 버전: ${documentDraft.version}`,
    ].join("\n");
    meta.style.whiteSpace = "pre-line";
    const content = documentNode.createElement("pre");
    content.textContent = documentDraft.content;
    const approval = documentNode.createElement("div");
    approval.className = "approval";
    approval.textContent = `승인: ${documentDraft.approved_by_name ?? "담당자"} · ${
      documentDraft.approved_at
        ? new Date(documentDraft.approved_at).toLocaleString("ko-KR")
        : ""
    }`;
    documentNode.body.append(title, meta, content, approval);
    printWindow.focus();
    window.setTimeout(() => printWindow.print(), 250);
  }

  async function saveProcessingNotes(nextNotes: string = notes) {
    if (!selected) return;
    setSaving(true);
    setError("");
    try {
      const updated = await apiFetch<WorkItem>(`/api/work-items/${selected.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          processing_notes: nextNotes,
        }),
      });
      setItems((current) =>
        current.map((item) => (item.id === updated.id ? updated : item)),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "업무 항목을 저장하지 못했습니다.");
    } finally {
      setSaving(false);
    }
  }

  async function changeWorkItemStatus(nextStatus: "pending" | "dismissed") {
    if (!selected) return;
    setSaving(true);
    setError("");
    setSuccess("");
    try {
      const updated = await apiFetch<WorkItem>(`/api/work-items/${selected.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          status: nextStatus,
          document_types: draft.document_types,
          processing_notes: notes,
        }),
      });
      replaceItem(updated);
      setSuccess(
        nextStatus === "dismissed"
          ? "이 자료를 업무기록과 서류 작성에서 제외했습니다. 원문은 그대로 보존됩니다."
          : "이 자료를 다시 확인할 수 있도록 되돌렸습니다.",
      );
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "자료의 사용 여부를 변경하지 못했습니다.",
      );
    } finally {
      setSaving(false);
    }
  }

  async function confirmRecord() {
    if (!selected?.ai_suggestion) return;
    setSaving(true);
    setError("");
    setSuccess("");
    try {
      let currentItem = selected;
      for (const documentDraft of selected.document_drafts) {
        if (
          !draft.document_types.includes(documentDraft.document_type) ||
          documentDraft.status === "not_used"
        ) {
          continue;
        }
        const editedContent =
          documentDraftEdits[documentDraft.document_type]?.trim() ?? "";
        if (editedContent && editedContent !== documentDraft.content.trim()) {
          currentItem = await apiFetch<WorkItem>(
            `/api/work-items/${selected.id}/document-drafts/${documentDraft.document_type}`,
            {
              method: "PATCH",
              body: JSON.stringify({
                action: "direct_edit",
                content: editedContent,
              }),
            },
          );
        }
      }
      const updated = await apiFetch<WorkItem>(
        `/api/work-items/${currentItem.id}/confirm`,
        {
          method: "POST",
          body: JSON.stringify({
            ...draft,
            document_drafts: currentItem.document_drafts
              .filter(
                (documentDraft) =>
                  draft.document_types.includes(documentDraft.document_type) &&
                  documentDraft.status !== "not_used",
              )
              .map((documentDraft) => ({
                document_type: documentDraft.document_type,
                content:
                  documentDraftEdits[documentDraft.document_type]?.trim() ||
                  documentDraft.content,
                verification_questions: documentDraft.verification_questions,
            })),
            reviewer_notes: notes || null,
            verification_acknowledged: true,
          }),
        },
      );
      replaceItem(updated);
      setSuccess(
        "최종 승인이 완료되었습니다. 승인된 서류를 바로 보거나 출력할 수 있습니다.",
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "담당자 확정을 저장하지 못했습니다.");
    } finally {
      setSaving(false);
    }
  }

  async function reopenApproval() {
    if (!selected?.confirmed_at) return;
    const reason =
      window.prompt(
        "승인을 취소하는 이유를 간단히 입력해 주세요.",
        "서류 내용을 수정하기 위해 승인을 취소함",
      )?.trim() ?? "";
    if (!reason) return;
    setSaving(true);
    setError("");
    setSuccess("");
    try {
      const updated = await apiFetch<WorkItem>(
        `/api/work-items/${selected.id}/reopen`,
        {
          method: "POST",
          body: JSON.stringify({ reason }),
        },
      );
      replaceItem(updated);
      setSuccess(
        "이전 승인본은 이력으로 보존하고 수정 가능한 새 초안을 만들었습니다.",
      );
    } catch (reasonValue) {
      setError(
        reasonValue instanceof Error
          ? reasonValue.message
          : "승인을 취소하고 수정본을 만들지 못했습니다.",
      );
    } finally {
      setSaving(false);
    }
  }

  async function toggleDocument(value: DailyDocumentType, checked: boolean) {
    if (!selected) return;
    if (!checked) {
      setDraft((current) => ({
        ...current,
        document_types: current.document_types.filter((item) => item !== value),
      }));
      setSuccess(`${documentLabels[value]}를 이번 최종 승인에서 제외했습니다.`);
      return;
    }
    setDocumentBusyType(value);
    setError("");
    setSuccess("");
    try {
      const updated = await apiFetch<WorkItem>(
        `/api/work-items/${selected.id}/document-drafts/${value}`,
        { method: "POST" },
      );
      replaceItem(updated);
      setSuccess(
        `${documentLabels[value]} 초안을 추가했습니다. 아래에서 내용을 확인해 주세요.`,
      );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : `${documentLabels[value]} 초안을 만들지 못했습니다.`,
      );
    } finally {
      setDocumentBusyType(null);
    }
  }

  function toggleRole(value: TargetRole, checked: boolean) {
    setDraft((current) => ({
      ...current,
      target_roles: checked
        ? [...current.target_roles, value]
        : current.target_roles.filter((item) => item !== value),
    }));
  }

  if (!open) return null;

  return (
    <div className="drawer-layer">
      <button className="drawer-backdrop" onClick={onClose} aria-label="업무함 닫기" />
      <aside className="workdesk-drawer" aria-label="AI 업무 정리함">
        <header className="drawer-header">
          <div>
            <span className="eyebrow">업무함</span>
            <h2>업무기록 검토</h2>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="닫기">
            ×
          </button>
        </header>
        <div className="workdesk-note">
          채팅 원문은 그대로 보존됩니다. AI가 업무기록 초안을 만들고 담당자가
          확인합니다. 확인 전에는 공식 서류에 반영되지 않습니다.
        </div>
        <div className="workdesk-status-filters" role="group" aria-label="업무자료 상태">
          {(
            [
              ["review", "검토 필요"],
              ["ready", "승인 완료"],
              ["dismissed", "사용 안 함"],
              ["all", "전체"],
            ] as const
          ).map(([value, label]) => (
            <button
              type="button"
              key={value}
              className={statusFilter === value ? "active" : ""}
              onClick={() => {
                setStatusFilter(value);
                const first = items.find((item) => {
                  return matchesStatusFilter(item, value);
                });
                if (first) applyItem(first);
                else clearSelectedItem();
              }}
            >
              {label}
            </button>
          ))}
        </div>
        <div
          className="workdesk-view-tabs"
          role="tablist"
          aria-label="이전 업무함 보기"
          hidden
        >
          <button
            role="tab"
            aria-selected={view === "summary"}
            className={view === "summary" ? "active" : ""}
            onClick={() => setView("summary")}
          >
            대화방 요약
          </button>
          <button
            role="tab"
            aria-selected={view === "inbox"}
            className={view === "inbox" ? "active" : ""}
            onClick={() => setView("inbox")}
          >
            처리할 대화
          </button>
          <button
            role="tab"
            aria-selected={view === "actions"}
            className={view === "actions" ? "active" : ""}
            onClick={() => setView("actions")}
          >
            인수인계·업무
          </button>
          <button
            role="tab"
            aria-selected={view === "candidates"}
            className={view === "candidates" ? "active" : ""}
            onClick={() => setView("candidates")}
          >
            서류 후보
          </button>
        </div>
        {view === "summary" ? (
          <div className="digest-dashboard">
            <div className="digest-toolbar">
              <strong>채팅방·기간 단위 요약</strong>
              <select
                value={digestPeriod}
                onChange={(event) =>
                  setDigestPeriod(event.target.value as "day" | "week" | "month")
                }
                aria-label="요약 기간"
              >
                <option value="day">오늘</option>
                <option value="week">이번 주</option>
                <option value="month">이번 달</option>
              </select>
            </div>
            <div className="digest-metrics">
              <span>
                <strong>{digests.length}</strong>
                대화방
              </span>
              <span>
                <strong>{digests.reduce((sum, digest) => sum + digest.message_count, 0)}</strong>
                대화
              </span>
              <span>
                <strong>{digests.reduce((sum, digest) => sum + digest.comment_count, 0)}</strong>
                댓글
              </span>
              <span>
                <strong>{actionItems.filter((item) => item.status !== "completed").length}</strong>
                미완료 업무
              </span>
            </div>
            {digests.length === 0 ? (
              <p className="muted-box">선택한 기간에 저장된 대화가 없습니다.</p>
            ) : (
              <div className="digest-layout">
                <nav className="digest-list" aria-label="대화방 요약 목록">
                  {digests.map((digest) => (
                    <button
                      key={digest.id}
                      className={selectedDigest?.id === digest.id ? "selected" : ""}
                      onClick={() => setSelectedDigestId(digest.id)}
                    >
                      <strong>{digest.room_name}</strong>
                      <span>
                        대화 {digest.message_count} · 댓글 {digest.comment_count} · 어르신{" "}
                        {digest.resident_count}
                      </span>
                      <small>{digest.summary}</small>
                    </button>
                  ))}
                </nav>
                {selectedDigest ? (
                  <section className="digest-detail">
                    <h3>{selectedDigest.room_name}</h3>
                    <p>{selectedDigest.summary}</p>
                    <div className="digest-count-groups">
                      <div>
                        <strong>서류 후보</strong>
                        {Object.entries(selectedDigest.document_counts).length ? (
                          Object.entries(selectedDigest.document_counts).map(([key, count]) => (
                            <span key={key}>
                              {documentLabels[key] ?? key} {count}건
                            </span>
                          ))
                        ) : (
                          <span>없음</span>
                        )}
                      </div>
                      <div>
                        <strong>위험도</strong>
                        {Object.entries(selectedDigest.risk_counts).length ? (
                          Object.entries(selectedDigest.risk_counts).map(([key, count]) => (
                            <span key={key}>
                              {riskLabels[key as RiskLevel] ?? key} {count}건
                            </span>
                          ))
                        ) : (
                          <span>분류 없음</span>
                        )}
                      </div>
                    </div>
                    <h4>주요 내용</h4>
                    <div className="digest-points">
                      {selectedDigest.major_points.map((point) => (
                        <article key={point.message_id}>
                          <div>
                            <strong>{point.resident_name ?? "일반 대화"}</strong>
                            <small>
                              {point.sender_name} · 댓글 {point.comment_count}
                            </small>
                          </div>
                          <p>{point.body}</p>
                          {point.action_type ? (
                            <span className="work-status status-in_review">업무 지정</span>
                          ) : null}
                        </article>
                      ))}
                    </div>
                  </section>
                ) : null}
              </div>
            )}
          </div>
        ) : view === "actions" ? (
          <div className="action-inbox">
            <div className="section-heading">
              <div>
                <h3>인수인계·업무협조</h3>
                <p>보내기 단계에서 담당자가 지정된 업무입니다.</p>
              </div>
              <span>{actionItems.length}건</span>
            </div>
            {actionItems.length === 0 ? (
              <p className="muted-box">배정된 인수인계나 업무협조가 없습니다.</p>
            ) : (
              actionItems.map((item) => (
                <article className={`action-card priority-${item.priority}`} key={item.id}>
                  <div>
                    <strong>
                      {item.action_type === "handover"
                        ? "인수인계"
                        : item.action_type === "cooperation"
                          ? "업무협조"
                          : "확인 요청"}
                    </strong>
                    <span>{item.assignee_user_name ?? item.assignee_unit_name}</span>
                  </div>
                  <p>{item.source_body}</p>
                  <small>
                    {item.room_name} · {item.sender_name}
                    {item.resident_name ? ` · ${item.resident_name}` : ""}
                    {item.comment_count ? ` · 댓글 ${item.comment_count}` : ""}
                  </small>
                  <small>
                    등록 {new Date(item.created_at).toLocaleString("ko-KR")} ·{" "}
                    {item.priority === "urgent"
                      ? "긴급"
                      : item.priority === "important"
                        ? "중요"
                        : "일반"}
                  </small>
                  <select
                    value={item.status}
                    disabled={saving}
                    onChange={(event) =>
                      void updateActionStatus(item, event.target.value as ActionItem["status"])
                    }
                    aria-label="업무 처리 상태"
                  >
                    <option value="assigned">미확인</option>
                    <option value="acknowledged">확인</option>
                    <option value="in_progress">처리 중</option>
                    <option value="completed">완료</option>
                  </select>
                </article>
              ))
            )}
            {error ? <p className="form-error">{error}</p> : null}
          </div>
        ) : view === "candidates" ? (
          <DocumentCandidateDashboard />
        ) : (
          <div className="workdesk-body">
          <nav className="work-item-list" aria-label="처리할 업무대화">
            {visibleItems.length === 0 ? (
              <p className="muted-box">이 상태에 해당하는 업무자료가 없습니다.</p>
            ) : (
              visibleItems.map((item) => {
                const previewImage = item.message.attachments.find((attachment) =>
                  attachment.mime_type.startsWith("image/"),
                );
                return (
                  <article
                    key={item.id}
                    className={selectedId === item.id ? "selected" : ""}
                    role="button"
                    tabIndex={0}
                    onClick={() => applyItem(item)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        applyItem(item);
                      }
                    }}
                  >
                    <span className={`work-status status-${item.status}`}>
                      {statusLabels[item.status]}
                    </span>
                    <strong>{item.resident?.display_name ?? "어르신 확인 필요"}</strong>
                    {previewImage ? (
                      <div className="work-item-thumbnail">
                        <AttachmentDisplay
                          attachment={previewImage}
                          compact
                          accessScope="workdesk"
                        />
                      </div>
                    ) : null}
                    <span>{item.source_snapshot.body}</span>
                    <small>
                      {item.source_snapshot.room_name} · {item.source_snapshot.sender_name}
                    </small>
                  </article>
                );
              })
            )}
          </nav>
          {selected ? (
            <form
              className="work-item-editor"
              onSubmit={(event) => event.preventDefault()}
            >
              <div className="review-status-line">
                <strong>원문은 보존됩니다.</strong>
                <span>
                  AI가 업무내용과 당일 서류 초안을 함께 만들고, 담당자가 한 화면에서
                  확인·승인합니다.
                </span>
              </div>

              <section className="work-original">
                <div>
                  {selected.source_snapshot.resident_names.length > 0 ? (
                    selected.source_snapshot.resident_names.map((name) => (
                      <span className="resident-chip" key={name}>
                        {name}
                      </span>
                    ))
                  ) : (
                    <span className="resident-chip candidate">어르신 확인 필요</span>
                  )}
                  <small>
                    {selected.source_snapshot.room_name} ·{" "}
                    {selected.source_snapshot.sender_name}
                  </small>
                </div>
                <label className="work-resident-selector">
                  <span>
                    연결 어르신
                    <small>잘못 지정되었으면 여기서 바로 바꾸세요.</small>
                  </span>
                  <select
                    aria-label="연결 어르신 변경"
                    value={selected.resident?.id ?? ""}
                    disabled={saving || Boolean(selected.confirmed_at)}
                    onChange={(event) => void replaceResident(event.target.value)}
                  >
                    <option value="">어르신을 선택하세요</option>
                    {residents.map((resident) => (
                      <option key={resident.id} value={resident.id}>
                        {residentServiceLabel(resident.service_type)} ·{" "}
                        {resident.display_name}
                      </option>
                    ))}
                  </select>
                  {selected.confirmed_at ? (
                    <small>최종 승인된 기록은 어르신을 변경할 수 없습니다.</small>
                  ) : null}
                </label>
                {selected.message.resident_links.some(
                  (link) => link.status === "candidate",
                ) ? (
                  <div className="resident-candidate-review">
                    <strong>이름이 일치한 어르신 후보</strong>
                    <small>
                      글이나 판독문에서 정확히 같은 이름을 찾았습니다. 원본과 대조해
                      확인하거나 제외해 주세요.
                    </small>
                    {selected.message.resident_links
                      .filter((link) => link.status === "candidate")
                      .map((link) => (
                        <div key={link.resident.id}>
                          <span>{link.resident.display_name}</span>
                          <button
                            type="button"
                            className="button button-secondary"
                            disabled={saving}
                            onClick={() =>
                              void reviewResidentLink(
                                link.resident.id,
                                "confirmed",
                              )
                            }
                          >
                            맞음
                          </button>
                          <button
                            type="button"
                            className="button button-ghost"
                            disabled={saving}
                            onClick={() =>
                              void reviewResidentLink(
                                link.resident.id,
                                "rejected",
                              )
                            }
                          >
                            제외
                          </button>
                        </div>
                      ))}
                  </div>
                ) : null}
                <p>{selected.source_snapshot.body}</p>
                {selected.message.attachments.length > 0 ? (
                  <div className="work-attachments">
                    {selected.message.attachments.map((attachment) => (
                      <div className="work-attachment-item" key={attachment.id}>
                        <AttachmentDisplay
                          attachment={attachment}
                          accessScope="workdesk"
                        />
                        {attachment.mime_type.startsWith("image/") ||
                        attachment.mime_type.startsWith("audio/") ? (
                          <section className="ocr-review-card">
                            <div>
                              <strong>
                                {attachment.mime_type.startsWith("audio/")
                                  ? "음성파일 받아쓰기"
                                  : "보고서 이미지 글자 판독"}
                              </strong>
                              <span>
                                {attachment.text_extraction
                                  ? {
                                      pending: "대기 중",
                                      processing: attachment.mime_type.startsWith("audio/")
                                        ? "로컬 받아쓰기 중"
                                        : "로컬 판독 중",
                                      completed: attachment.mime_type.startsWith("audio/")
                                        ? "받아쓰기 완료 · 확인 필요"
                                        : "판독 완료 · 확인 필요",
                                      failed: attachment.mime_type.startsWith("audio/")
                                        ? "받아쓰기 실패"
                                        : "판독 실패",
                                      reviewed: "담당자 확인 완료",
                                    }[attachment.text_extraction.status]
                                  : "아직 실행하지 않음"}
                              </span>
                            </div>
                            {attachment.text_extraction?.error_message ? (
                              <p className="ocr-error">
                                {attachment.text_extraction.error_message}
                              </p>
                            ) : null}
                            {attachment.mime_type.startsWith("image/") &&
                            attachment.text_extraction?.original_extracted_text ? (
                              <div className="ocr-original-text">
                                <div>
                                  <strong>최초 OCR 원문</strong>
                                  <span>수정·재판독해도 보존</span>
                                </div>
                                <pre>
                                  {attachment.text_extraction.original_extracted_text}
                                </pre>
                              </div>
                            ) : null}
                            {attachment.mime_type.startsWith("image/") &&
                            attachment.text_extraction?.spelling_candidates.length ? (
                              <div className="ocr-spelling-candidates">
                                <div>
                                  <strong>교정 후보</strong>
                                  <small>누르면 아래 교정문에만 넣습니다.</small>
                                </div>
                                <div className="ocr-candidate-list">
                                  {attachment.text_extraction.spelling_candidates.map(
                                    (candidate) => (
                                      <button
                                        type="button"
                                        key={candidate.id}
                                        className={
                                          ocrCandidateSelections[attachment.id] ===
                                          candidate.id
                                            ? "selected"
                                            : ""
                                        }
                                        onClick={() =>
                                          applyOcrCandidate(attachment, candidate)
                                        }
                                      >
                                        <span>
                                          {candidate.recognized} → {candidate.candidate}
                                        </span>
                                        <small>
                                          {candidate.source === "confirmed_history"
                                            ? `과거 확정 ${candidate.support_count}건`
                                            : "기관 어휘"}
                                          {" · "}
                                          {Math.round(candidate.confidence * 100)}%
                                        </small>
                                        {candidate.is_protected ? (
                                          <em>중요 항목 · 자동 변경 금지</em>
                                        ) : null}
                                      </button>
                                    ),
                                  )}
                                </div>
                                <small>
                                  모든 후보는 제안일 뿐이며 담당자가 선택하고 확정해야
                                  저장됩니다.
                                </small>
                              </div>
                            ) : null}
                            {attachment.text_extraction &&
                            ["completed", "reviewed"].includes(
                              attachment.text_extraction.status,
                            ) ? (
                              <>
                                <label>
                                  {attachment.mime_type.startsWith("audio/")
                                    ? "받아쓰기 내용"
                                    : "교정문"}
                                  <textarea
                                    value={ocrDrafts[attachment.id] ?? ""}
                                    onChange={(event) => {
                                      setOcrDrafts((current) => ({
                                        ...current,
                                        [attachment.id]: event.target.value,
                                      }));
                                      setOcrCandidateSelections((current) => {
                                        const next = { ...current };
                                        delete next[attachment.id];
                                        return next;
                                      });
                                    }}
                                    rows={7}
                                  />
                                </label>
                                <small className="ocr-save-guide">
                                  내용을 고친 뒤 아래의 ‘AI 업무초안 만들기’를 누르면
                                  수정문 저장과 AI 정리가 함께 진행됩니다.
                                </small>
                                {attachment.mime_type.startsWith("image/") &&
                                attachment.text_extraction.review_decision ? (
                                  <small className="ocr-last-decision">
                                    마지막 처리:{" "}
                                    {
                                      ocrDecisionLabels[
                                        attachment.text_extraction.review_decision
                                      ]
                                    }
                                    {" · 이력 "}
                                    {attachment.text_extraction.correction_event_count}건
                                  </small>
                                ) : null}
                              </>
                            ) : null}
                            <details className="ocr-more-actions">
                              <summary>판독이 맞지 않을 때</summary>
                              <div className="ocr-actions">
                                {attachment.mime_type.startsWith("image/") &&
                                attachment.text_extraction ? (
                                  <>
                                    <button
                                      type="button"
                                      className="button button-secondary"
                                      disabled={ocrBusyId === attachment.id}
                                      onClick={() =>
                                        void saveReviewedText(attachment, "keep_raw")
                                      }
                                    >
                                      원문으로 되돌리기
                                    </button>
                                    <button
                                      type="button"
                                      className="button button-ghost"
                                      disabled={ocrBusyId === attachment.id}
                                      onClick={() =>
                                        void saveReviewedText(attachment, "needs_review")
                                      }
                                    >
                                      확인 필요로 남기기
                                    </button>
                                  </>
                                ) : null}
                                <button
                                  type="button"
                                  className="button button-ghost"
                                  disabled={
                                    ocrBusyId === attachment.id ||
                                    ["pending", "processing"].includes(
                                      attachment.text_extraction?.status ?? "",
                                    )
                                  }
                                  onClick={() => void requestTextExtraction(attachment)}
                                >
                                  {ocrBusyId === attachment.id
                                    ? "처리 중…"
                                    : attachment.mime_type.startsWith("audio/")
                                      ? "다시 받아쓰기"
                                      : "다시 판독"}
                                </button>
                              </div>
                            </details>
                          </section>
                        ) : null}
                      </div>
                    ))}
                  </div>
                ) : null}
                {selected.comments.length > 0 ? (
                  <div className="workdesk-comments">
                    <strong>댓글 {selected.comments.length}개</strong>
                    {selected.comments.map((comment) => (
                      <article key={comment.id}>
                        <div>
                          <strong>{comment.author_name}</strong>
                          <time>{new Date(comment.created_at).toLocaleString("ko-KR")}</time>
                        </div>
                        <p>{comment.body}</p>
                      </article>
                    ))}
                  </div>
                ) : null}
                <small className="immutable-note">원문은 이 화면에서 수정할 수 없습니다.</small>
              </section>

              {!selected.ai_suggestion ? (
                <section className="prototype-ai-banner">
                  <strong>2단계: AI 업무기록 정리</strong>
                  <span>
                    {selected.resident
                      ? "원문을 오타 교정하고 업무 분류·위험도·관련 서류 후보를 한 번에 정리합니다."
                      : "먼저 위에서 판독된 어르신 후보를 확인해야 AI 정리를 시작할 수 있습니다."}
                  </span>
                  <button
                    type="button"
                    className="button button-secondary"
                    disabled={
                      generating ||
                      selected.status === "dismissed" ||
                      !selected.resident
                    }
                    onClick={prepareAiDraft}
                  >
                    {generating ? "AI 업무초안을 만드는 중…" : "AI 업무초안 만들기"}
                  </button>
                </section>
              ) : (
                <section className="ai-suggestion-card">
                  <div>
                    <strong>
                      {selected.ai_state === "ai_reviewed"
                        ? "AI 자동 정리 초안"
                        : "기초 분류 초안"}
                    </strong>
                    <span>{aiGeneratorLabel(selected.ai_generator)}</span>
                  </div>
                  <p>
                    아래 내용은 제안일 뿐입니다. 담당자가 수정하고 확정해야 사용할 수
                    있습니다.
                  </p>
                  {!selected.confirmed_at ? (
                    <details className="ai-redraft-actions">
                      <summary>AI 초안을 다시 만들고 싶을 때</summary>
                      <button
                        type="button"
                        className="button button-secondary"
                        disabled={generating || selected.status === "dismissed"}
                        onClick={prepareAiDraft}
                      >
                        {generating
                          ? "AI 업무초안을 만드는 중…"
                          : "AI 업무초안 다시 만들기"}
                      </button>
                    </details>
                  ) : null}
                  {draft.keywords.length > 0 ? (
                    <div className="keyword-list">
                      {draft.keywords.map((keyword) => (
                        <span key={keyword}>{keyword}</span>
                      ))}
                    </div>
                  ) : null}
                </section>
              )}

              {selected.ai_suggestion ? (
                <>
                  <label>
                    오타·띄어쓰기 검토문
                    <textarea
                      rows={4}
                      maxLength={2000}
                      value={draft.corrected_text}
                      onChange={(event) =>
                        setDraft((current) => ({
                          ...current,
                          corrected_text: event.target.value,
                        }))
                      }
                    />
                  </label>
                  <label hidden>
                    한눈에 보는 요약
                    <textarea
                      rows={3}
                      maxLength={1000}
                      value={draft.summary}
                      onChange={(event) =>
                        setDraft((current) => ({ ...current, summary: event.target.value }))
                      }
                    />
                  </label>
                  <div className="structured-review-grid" hidden>
                    <label>
                      관찰 내용
                      <textarea
                        rows={4}
                        maxLength={4000}
                        value={draft.observation_details}
                        onChange={(event) =>
                          setDraft((current) => ({
                            ...current,
                            observation_details: event.target.value,
                          }))
                        }
                      />
                    </label>
                    <label>
                      시행한 조치
                      <textarea
                        rows={4}
                        maxLength={2000}
                        value={draft.actions_taken.join("\n")}
                        placeholder="한 줄에 한 가지씩 입력하세요."
                        onChange={(event) =>
                          setDraft((current) => ({
                            ...current,
                            actions_taken: event.target.value
                              .split("\n")
                              .map((value) => value.trim())
                              .filter(Boolean),
                          }))
                        }
                      />
                    </label>
                    <label>
                      어르신 반응·결과
                      <textarea
                        rows={3}
                        maxLength={2000}
                        value={draft.resident_response}
                        placeholder="원문에서 확인되지 않으면 비워 두세요."
                        onChange={(event) =>
                          setDraft((current) => ({
                            ...current,
                            resident_response: event.target.value,
                          }))
                        }
                      />
                    </label>
                    <label>
                      인수인계 요약
                      <textarea
                        rows={3}
                        maxLength={2000}
                        value={draft.handover_summary}
                        onChange={(event) =>
                          setDraft((current) => ({
                            ...current,
                            handover_summary: event.target.value,
                          }))
                        }
                      />
                    </label>
                  </div>
                  <div className="review-selects" hidden>
                    <label>
                      분류
                      <select
                        value={draft.classification}
                        onChange={(event) =>
                          setDraft((current) => ({
                            ...current,
                            classification: event.target.value as RecordClassification,
                          }))
                        }
                      >
                        {Object.entries(classificationLabels).map(([value, label]) => (
                          <option key={value} value={value}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      위험도
                      <select
                        value={draft.risk_level}
                        onChange={(event) =>
                          setDraft((current) => ({
                            ...current,
                            risk_level: event.target.value as RiskLevel,
                          }))
                        }
                      >
                        {Object.entries(riskLabels).map(([value, label]) => (
                          <option key={value} value={value}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                  <fieldset className="document-picker" hidden>
                    <legend>전달 대상</legend>
                    {Object.entries(targetRoleLabels).map(([value, label]) => (
                      <label key={value}>
                        <input
                          type="checkbox"
                          checked={draft.target_roles.includes(value as TargetRole)}
                          onChange={(event) =>
                            toggleRole(value as TargetRole, event.target.checked)
                          }
                        />
                        <span>{label}</span>
                      </label>
                    ))}
                  </fieldset>
                  <fieldset className="document-picker">
                    <legend>이번 기록에서 만들 당일 서류</legend>
                    {Object.entries(documentLabels).map(([value, label]) => (
                      <label key={value}>
                        <input
                          type="checkbox"
                          checked={draft.document_types.includes(value)}
                          disabled={
                            Boolean(selected.confirmed_at) ||
                            documentBusyType === value
                          }
                          onChange={(event) =>
                            void toggleDocument(
                              value as DailyDocumentType,
                              event.target.checked,
                            )
                          }
                        />
                        <span>{label}</span>
                      </label>
                    ))}
                    <p className="document-picker-guide">
                      필요한 서류를 체크하면 초안이 바로 생깁니다. 체크를 풀면
                      이번 최종 승인에서 빠집니다.
                    </p>
                  </fieldset>
                  {selected.document_drafts.length > 0 ? (
                    <section className="daily-document-drafts">
                      <div className="daily-document-drafts-heading">
                        <div>
                          <strong>AI가 만든 당일 서류 초안</strong>
                          <span>
                            필요한 문장만 고친 뒤 아래에서 한 번에 승인할 수 있습니다.
                          </span>
                        </div>
                      </div>
                      {selected.document_drafts.map((documentDraft) => (
                        <article
                          className={`daily-document-card status-${documentDraft.status}`}
                          key={documentDraft.id}
                        >
                          <header>
                            <div>
                              <strong>
                                {documentLabels[documentDraft.document_type] ??
                                  documentDraft.document_type}
                              </strong>
                              <span>초안 {documentDraft.version}판</span>
                            </div>
                            <em>
                              {selected.confirmed_at &&
                              documentDraft.status === "approved"
                                ? "승인 완료"
                                : documentDraft.status === "not_used"
                                  ? "이번 업무에서 제외"
                                  : "최종 승인 전"}
                            </em>
                          </header>
                          <textarea
                            rows={8}
                            value={
                              documentDraftEdits[documentDraft.document_type] ?? ""
                            }
                            disabled={
                              documentDraft.status === "not_used" ||
                              Boolean(selected.confirmed_at)
                            }
                            onChange={(event) =>
                              setDocumentDraftEdits((current) => ({
                                ...current,
                                [documentDraft.document_type]: event.target.value,
                              }))
                            }
                          />
                          {documentDraft.verification_questions.length > 0 ? (
                            <div className="document-verification-list">
                              <strong>승인 전에 확인</strong>
                              <ul>
                                {documentDraft.verification_questions.map((question) => (
                                  <li key={question}>{question}</li>
                                ))}
                              </ul>
                            </div>
                          ) : null}
                          {selected.confirmed_at &&
                          documentDraft.status === "approved" ? (
                            <div className="document-card-actions">
                              <button
                                type="button"
                                className="button button-primary"
                                onClick={() =>
                                  printDocumentDraft(documentDraft.document_type)
                                }
                              >
                                승인본 보기·출력
                              </button>
                            </div>
                          ) : null}
                          {!selected.confirmed_at ? (
                            <details className="document-more-actions">
                            <summary>필요할 때만: 다시 쓰기 또는 이 서류 제외</summary>
                            <input
                              value={
                                documentChangeRequests[
                                  documentDraft.document_type
                                ] ?? ""
                              }
                              placeholder="예: 관찰과 조치를 구분하고 더 짧게 써줘"
                              onChange={(event) =>
                                setDocumentChangeRequests((current) => ({
                                  ...current,
                                  [documentDraft.document_type]: event.target.value,
                                }))
                              }
                            />
                            <div>
                              <button
                                type="button"
                                className="button button-secondary"
                                disabled={
                                  documentBusyType === documentDraft.document_type
                                }
                                onClick={() =>
                                  void updateDocumentDraft(
                                    documentDraft.document_type,
                                    "change_request",
                                  )
                                }
                              >
                                요청대로 다시 작성
                              </button>
                              <button
                                type="button"
                                className="button button-ghost"
                                disabled={
                                  documentBusyType === documentDraft.document_type
                                }
                                onClick={() =>
                                  void updateDocumentDraft(
                                    documentDraft.document_type,
                                    "regenerate",
                                  )
                                }
                              >
                                AI가 다시 작성
                              </button>
                              <button
                                type="button"
                                className="button button-ghost"
                                disabled={
                                  documentBusyType === documentDraft.document_type ||
                                  documentDraft.status === "not_used"
                                }
                                onClick={() =>
                                  void updateDocumentDraft(
                                    documentDraft.document_type,
                                    "not_used",
                                  )
                                }
                              >
                                이 서류 제외
                              </button>
                            </div>
                            </details>
                          ) : null}
                        </article>
                      ))}
                    </section>
                  ) : null}
                </>
              ) : null}

              <label>
                담당자 검토 메모
                <textarea
                  rows={5}
                  maxLength={4000}
                  value={notes}
                  onChange={(event) => setNotes(event.target.value)}
                  onBlur={() => {
                    if (notes !== (selected.processing_notes ?? "")) {
                      void saveProcessingNotes(notes);
                    }
                  }}
                  placeholder="확인한 사실, 추가 확인이 필요한 내용, 후속조치를 기록하세요."
                />
              </label>
              {selected.confirmed_at ? (
                <p className="confirmed-banner">
                  {selected.confirmed_by_name} 담당자가{" "}
                  {new Date(selected.confirmed_at).toLocaleString("ko-KR")}에 확정했습니다.
                </p>
              ) : null}
              {error ? <p className="form-error">{error}</p> : null}
              {success ? <p className="form-success">{success}</p> : null}
              {selected.confirmed_at ? (
                <div className="approval-complete-guide">
                  <p>승인이 완료되었습니다. 위 서류의 ‘승인본 보기·출력’을 이용하세요.</p>
                  <details>
                    <summary>승인 후 수정이 필요할 때</summary>
                    <p>이전 승인본은 이력으로 남고 수정 가능한 새 초안이 생깁니다.</p>
                    <button
                      type="button"
                      className="button button-secondary"
                      disabled={saving}
                      onClick={() => void reopenApproval()}
                    >
                      승인 취소하고 수정
                    </button>
                  </details>
                </div>
              ) : selected.status === "dismissed" ? (
                <p className="dismissed-guide">
                  이 자료는 업무기록과 서류 작성에서 제외되어 있습니다.
                </p>
              ) : (
                <div className="workdesk-actions">
                <button
                  type="button"
                  className="button button-primary button-large"
                  disabled={
                    saving ||
                    !selected.ai_suggestion ||
                    !draft.corrected_text.trim() ||
                    !draft.summary.trim() ||
                    draft.document_types.length === 0
                  }
                  onClick={confirmRecord}
                >
                  확인하고 최종 승인
                </button>
                </div>
              )}
              {!selected.confirmed_at ? (
                <details className="work-item-more-actions">
                  <summary>기타 처리</summary>
                  <p>
                    업무기록에 사용할 필요가 없는 단순 대화나 중복 자료일 때만
                    사용하세요. 원문은 삭제되지 않습니다.
                  </p>
                  <button
                    type="button"
                    className="button work-item-dismiss-button"
                    disabled={saving}
                    onClick={() =>
                      void changeWorkItemStatus(
                        selected.status === "dismissed" ? "pending" : "dismissed",
                      )
                    }
                  >
                    {selected.status === "dismissed"
                      ? "다시 확인하기"
                      : "이 자료 사용하지 않기"}
                  </button>
                </details>
              ) : null}
            </form>
          ) : (
            <div className="workdesk-empty">왼쪽에서 업무대화를 선택하세요.</div>
          )}
          </div>
        )}
      </aside>
    </div>
  );
}
