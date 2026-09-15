"use client";

import {
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { apiFetch } from "../api";
import { messageSummary } from "../messagePresentation";
import type {
  AiAssistConfig,
  AiAssistConversation,
  AiAssistResidentNameCandidate,
  AiAssistServiceContext,
  AiAssistTaskType,
  AiAssistTurn,
  Message,
} from "../types";

const taskLabels: Record<AiAssistTaskType, string> = {
  image_text: "이미지 글자 읽기",
  image_explain: "이미지 내용 설명",
  audio_summary: "음성 받아쓰기 정리",
  summary: "핵심 요약",
  history_search: "관련 과거기록 찾기",
  risk_check: "위험·누락 확인",
  question: "직접 질문",
};

const progressLabels: Record<AiAssistTurn["status"], string> = {
  queued: "차례를 기다리는 중",
  preparing: "업무자료 준비 중",
  extracting: "이미지·음성 확인 중",
  searching: "관련 기록 검색 중",
  reasoning: "답변 작성 중",
  validating: "근거와 수치 확인 중",
  completed: "완료",
  failed: "처리하지 못함",
  cancelled: "취소됨",
};

const pendingStatuses = new Set<AiAssistTurn["status"]>([
  "queued",
  "preparing",
  "extracting",
  "searching",
  "reasoning",
  "validating",
]);

const providerLabels: Record<string, string> = {
  codex_worker: "Codex",
  codex: "Codex",
  local_ollama: "내부 AI",
  ollama: "내부 AI",
  nvidia: "Nemotron",
  local_deterministic: "내부 기본 분석",
};

const processingLocationLabels: Record<AiAssistTurn["processing_location"], string> = {
  rules: "기기 내부 규칙",
  local: "현재 기기",
  internal: "내부 서버",
  external: "외부 공급자",
  unconfigured: "위치 미확인",
};

const enhancementLabels: Record<AiAssistTurn["enhancement_status"], string> = {
  pending: "AI 보강 진행 중",
  completed: "AI 보강 완료",
  baseline: "규칙 기반 즉시 결과",
  failed: "AI 보강 실패 · 기본 결과 유지",
};

const ALL_AUDIO_FILES = "all-audio-files";

function hasCompletedAudioTranscript(
  attachment: Message["attachments"][number],
) {
  const extraction = attachment.text_extraction;
  if (!extraction || !["completed", "reviewed"].includes(extraction.status)) {
    return false;
  }
  return Boolean(
    extraction.reviewed_text?.trim() ||
      extraction.extracted_text?.trim() ||
      extraction.original_extracted_text?.trim(),
  );
}

function isServiceContext(value: string): value is AiAssistServiceContext {
  return value === "facility" || value === "daycare" || value === "homecare";
}

function inferServiceContext(message: Message): AiAssistServiceContext | "" {
  const values = [
    message.resident?.service_type,
    ...message.resident_links
      .filter((link) => link.status === "confirmed")
      .map((link) => link.resident.service_type),
  ].filter((value): value is AiAssistServiceContext =>
    typeof value === "string" && isServiceContext(value),
  );
  const uniqueValues = [...new Set(values)];
  return uniqueValues.length === 1 ? uniqueValues[0] : "";
}

function conversationFingerprint(conversation: AiAssistConversation | null) {
  if (!conversation) return "none";
  return JSON.stringify(conversation);
}

function engineLabel(turn: AiAssistTurn) {
  const provider = turn.provider
    ? providerLabels[turn.provider] ?? turn.provider
    : null;
  return [provider, turn.model].filter(Boolean).join(" · ");
}

function residentCandidateLabel(candidate: AiAssistResidentNameCandidate) {
  const recognized =
    typeof candidate.recognized === "string"
      ? candidate.recognized
      : typeof candidate.recognized_name === "string"
        ? candidate.recognized_name
        : "";
  const matched =
    typeof candidate.candidate === "string"
      ? candidate.candidate
      : typeof candidate.candidate_name === "string"
        ? candidate.candidate_name
        : "";
  return recognized.trim() && matched.trim()
    ? { recognized: recognized.trim(), matched: matched.trim() }
    : null;
}

export function AiAssistPanel({
  message,
  onClose,
  onShared,
  reviewOnly = false,
  showTechnicalDetails = false,
}: {
  message: Message;
  onClose: () => void;
  onShared: () => Promise<unknown>;
  reviewOnly?: boolean;
  showTechnicalDetails?: boolean;
}) {
  const [config, setConfig] = useState<AiAssistConfig | null>(null);
  const [conversation, setConversation] = useState<AiAssistConversation | null>(null);
  const [restoringConversation, setRestoringConversation] = useState(true);
  const [selectedAudioId, setSelectedAudioId] = useState(() => {
    const initialAudioFiles = message.attachments.filter((item) =>
      item.mime_type.startsWith("audio/"),
    );
    return initialAudioFiles.length > 1
      ? ALL_AUDIO_FILES
      : initialAudioFiles[0]?.id ?? "";
  });
  const [question, setQuestion] = useState("");
  const [deidentifiedConfirmed, setDeidentifiedConfirmed] = useState(false);
  const serviceContext = useMemo(() => inferServiceContext(message), [message]);
  const [newAnalysisMode, setNewAnalysisMode] = useState(false);
  const [expandedTurnIds, setExpandedTurnIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [starting, setStarting] = useState(false);
  const [sharingTurnId, setSharingTurnId] = useState<string | null>(null);
  const [shareNotice, setShareNotice] = useState<{
    turnId: string;
    tone: "success" | "error";
    message: string;
  } | null>(null);
  const [feedback, setFeedback] = useState("");
  const [error, setError] = useState("");
  const actionsRef = useRef<HTMLDivElement | null>(null);
  const latestTurnRef = useRef<HTMLElement | null>(null);
  const newAnalysisModeRef = useRef(false);
  const conversationSyncGenerationRef = useRef(0);

  const images = useMemo(
    () => message.attachments.filter((item) => item.mime_type.startsWith("image/")),
    [message.attachments],
  );
  const audioFiles = useMemo(
    () => message.attachments.filter((item) => item.mime_type.startsWith("audio/")),
    [message.attachments],
  );
  const selectedAudioFiles = useMemo(
    () =>
      selectedAudioId === ALL_AUDIO_FILES
        ? audioFiles
        : audioFiles.filter((item) => item.id === selectedAudioId),
    [audioFiles, selectedAudioId],
  );
  const pendingAudioTranscriptNames = useMemo(
    () =>
      selectedAudioFiles
        .filter((item) => !hasCompletedAudioTranscript(item))
        .map((item) => item.original_name),
    [selectedAudioFiles],
  );

  const activeTurn = conversation?.turns.at(-1) ?? null;
  const isWorking = Boolean(activeTurn && pendingStatuses.has(activeTurn.status));
  const showAnalysisChoices = !conversation || newAnalysisMode;
  const latestResultKey = activeTurn
    ? activeTurn.id
    : "none";

  const refreshLatestConversation = useCallback(async () => {
    const syncGeneration = conversationSyncGenerationRef.current;
    const next = await apiFetch<AiAssistConversation | null>(
      `/api/messages/${message.id}/ai-conversations/latest`,
    );
    if (
      newAnalysisModeRef.current ||
      syncGeneration !== conversationSyncGenerationRef.current
    ) {
      return next;
    }
    setConversation((current) =>
      conversationFingerprint(current) === conversationFingerprint(next)
        ? current
        : next,
    );
    return next;
  }, [message.id]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void apiFetch<AiAssistConfig>("/api/ai-assist/config")
        .then(setConfig)
        .catch((reason) =>
          setError(reason instanceof Error ? reason.message : "AI 준비상태를 확인하지 못했습니다."),
        );
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    let disposed = false;
    const timer = window.setTimeout(() => {
      void refreshLatestConversation()
        .catch((reason) => {
          if (!disposed) {
            setError(
              reason instanceof Error
                ? reason.message
                : "이전에 확인한 AI 내용을 불러오지 못했습니다.",
            );
          }
        })
        .finally(() => {
          if (!disposed) setRestoringConversation(false);
        });
    }, 0);
    return () => {
      disposed = true;
      window.clearTimeout(timer);
    };
  }, [refreshLatestConversation]);

  useEffect(() => {
    if (restoringConversation || newAnalysisMode) return;
    const intervalMs = isWorking ? 2_000 : 5_000;
    const timer = window.setInterval(() => {
      void refreshLatestConversation().catch(() => {
        // 일시적인 동기화 실패는 현재 결과를 유지하고 다음 주기에 다시 확인합니다.
      });
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [isWorking, newAnalysisMode, refreshLatestConversation, restoringConversation]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      latestTurnRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [latestResultKey]);

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  async function requestTurn(
    taskType: AiAssistTaskType,
    requestedQuestion = "",
  ) {
    if (starting || restoringConversation || isWorking || !config?.enabled) return;
    if (message.is_recalled) {
      setError("회수된 메시지는 AI 도움에 사용할 수 없습니다.");
      return;
    }
    if (!config.task_types.includes(taskType)) {
      setError("현재 서버에서 이 AI 도움 기능을 준비 중입니다.");
      return;
    }
    const needsImage = taskType === "image_text" || taskType === "image_explain";
    const needsAudio = taskType === "audio_summary";
    if (needsAudio && !config.whisper_ready) {
      setError("음성 받아쓰기는 아직 준비 중입니다. 글이나 이미지 도움을 먼저 이용해 주세요.");
      return;
    }
    if (needsAudio && pendingAudioTranscriptNames.length > 0) {
      setError(
        `아직 글자 변환이 끝나지 않은 음성파일이 있습니다: ${pendingAudioTranscriptNames.join(", ")}. 모든 변환이 끝난 뒤 다시 눌러 주세요.`,
      );
      return;
    }
    const attachmentId = needsImage
      ? images[0]?.id
      : needsAudio
        ? selectedAudioId === ALL_AUDIO_FILES
          ? null
          : audioFiles.some((item) => item.id === selectedAudioId)
            ? selectedAudioId
            : audioFiles[0]?.id
        : null;
    if (needsImage && !attachmentId) {
      setError(needsImage ? "확인할 이미지가 없습니다." : "확인할 음성파일이 없습니다.");
      return;
    }
    if (needsAudio && selectedAudioFiles.length === 0) {
      setError("확인할 음성파일이 없습니다.");
      return;
    }
    setStarting(true);
    setError("");
    setFeedback("");
    const startNewConversation = !conversation || newAnalysisMode;
    if (startNewConversation) conversationSyncGenerationRef.current += 1;
    try {
      const body = JSON.stringify({
        task_type: taskType,
        question: requestedQuestion.trim() || null,
        attachment_id: attachmentId,
        service_context: serviceContext || null,
        deidentified_confirmed_by_user: deidentifiedConfirmed,
      });
      const next = !startNewConversation && conversation
        ? await apiFetch<AiAssistConversation>(
            `/api/ai-conversations/${conversation.id}/turns`,
            { method: "POST", body },
          )
        : await apiFetch<AiAssistConversation>(
            `/api/messages/${message.id}/ai-conversations`,
            { method: "POST", body },
          );
      setConversation(next);
      conversationSyncGenerationRef.current += 1;
      newAnalysisModeRef.current = false;
      setNewAnalysisMode(false);
      setQuestion("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "AI 도움을 시작하지 못했습니다.");
    } finally {
      setStarting(false);
    }
  }

  async function askQuestion(event: FormEvent) {
    event.preventDefault();
    if (!question.trim()) return;
    await requestTurn("question", question);
  }

  async function shareTurn(turn: AiAssistTurn) {
    if (turn.status !== "completed" || sharingTurnId || message.is_recalled) return;
    setSharingTurnId(turn.id);
    setShareNotice(null);
    try {
      await apiFetch(`/api/ai-turns/${turn.id}/share`, {
        method: "POST",
        body: "{}",
      });
      setShareNotice({
        turnId: turn.id,
        tone: "success",
        message: "현재 대화방에 공유했습니다.",
      });
      try {
        await onShared();
      } catch {
        setShareNotice({
          turnId: turn.id,
          tone: "success",
          message: "공유했습니다. 대화방 화면은 잠시 뒤 새로고침해 주세요.",
        });
      }
    } catch (reason) {
      setShareNotice({
        turnId: turn.id,
        tone: "error",
        message:
          reason instanceof Error ? reason.message : "AI 답변을 공유하지 못했습니다.",
      });
    } finally {
      setSharingTurnId(null);
    }
  }

  function toggleNewAnalysisMode() {
    const nextMode = !newAnalysisModeRef.current;
    conversationSyncGenerationRef.current += 1;
    newAnalysisModeRef.current = nextMode;
    setNewAnalysisMode(nextMode);
    setQuestion("");
    setError("");
    setShareNotice(null);
    setFeedback(
      nextMode
        ? "새 분석 종류를 선택하세요. 기존 결과는 지워지지 않습니다."
        : "",
    );
    window.requestAnimationFrame(() => {
      actionsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  function togglePreviousTurn(turnId: string) {
    setExpandedTurnIds((current) => {
      const next = new Set(current);
      if (next.has(turnId)) next.delete(turnId);
      else next.add(turnId);
      return next;
    });
  }

  return (
    <div
      className="ai-assist-layer"
      role="dialog"
      aria-modal="true"
      aria-labelledby="ai-assist-title"
      aria-describedby="ai-assist-description"
    >
      <section className="ai-assist-panel desktop-resizable-dialog">
        <header>
          <div>
            <span className="eyebrow">대화 근거로 확인</span>
            <h2 id="ai-assist-title">AI 업무 도움</h2>
            <p id="ai-assist-description">
              AI 답변은 참고자료입니다. 이름·수치·약·진단은 원문과 다시 확인하세요.
            </p>
          </div>
          <button
            className="icon-button"
            type="button"
            onClick={onClose}
            aria-label="AI 업무 도움 닫기"
          >
            ×
          </button>
        </header>

        <div className="ai-assist-source">
          <strong>{message.sender_name}님의 업무대화</strong>
          <p>{messageSummary(message.body, message.attachments)}</p>
        </div>

        {!config ? <p className="ai-assist-status">AI 준비상태를 확인하는 중입니다…</p> : null}
        {restoringConversation ? (
          <p className="ai-assist-status">이전에 확인한 AI 내용을 불러오는 중입니다…</p>
        ) : null}
        {config && !config.enabled ? (
          <div className="ai-assist-disabled">
            <strong>AI 도움 서버를 준비 중입니다.</strong>
            <p>대화와 첨부는 전송되지 않았습니다. 관리자가 안전한 연결을 준비한 뒤 사용할 수 있습니다.</p>
          </div>
        ) : null}
        {config?.enabled ? (
          <div className="ai-assist-provider-status">
            <span className={config.codex_ready ? "ready" : "standby"}>
              {config.codex_ready
                ? "정밀 AI 연결 준비됨"
                : "정밀 AI 연결 준비 전 · 기본 도움 사용"}
            </span>
            <span className={config.whisper_ready ? "ready" : "standby"}>
              음성 받아쓰기 {config.whisper_ready ? "사용 가능" : "준비 중"}
            </span>
            <span className="safe">민감자료 외부 전송 기본 차단</span>
          </div>
        ) : null}
        {config?.enabled ? (
          <label className="ai-assist-deidentified-confirmation">
            <input
              type="checkbox"
              checked={deidentifiedConfirmed}
              disabled={starting || restoringConversation || isWorking}
              onChange={(event) =>
                setDeidentifiedConfirmed(event.currentTarget.checked)
              }
            />
            <span>
              <strong>비식별 자료임을 확인하고 허용된 외부 AI 사용</strong>
              <small>
                체크하지 않으면 내부 경로만 사용합니다. 직접 식별정보가
                감지되면 체크해도 외부 전송을 차단합니다.
              </small>
            </span>
          </label>
        ) : null}

        {config?.enabled ? (
          <>
            <div className="ai-assist-action-heading" ref={actionsRef}>
              <div>
                <strong>
                  {newAnalysisMode
                    ? "새 분석을 선택하세요"
                    : conversation
                      ? "계속 확인하거나 추가로 질문하세요"
                      : "무엇을 확인할까요?"}
                </strong>
                {conversation ? (
                  <span>
                    {newAnalysisMode
                      ? "기존 결과는 그대로 보관됩니다."
                      : "아래에 기존 확인 내용이 함께 보입니다."}
                  </span>
                ) : null}
              </div>
              {conversation && !isWorking ? (
                <button
                  className="button button-secondary"
                  type="button"
                  onClick={toggleNewAnalysisMode}
                >
                  {newAnalysisMode ? "취소" : "새 분석 시작"}
                </button>
              ) : null}
            </div>

            {showAnalysisChoices ? (
              <>
                {images.length > 1 ? (
                  <p className="ai-assist-capability-note" role="status">
                    첨부된 사진 {images.length}장을 함께 분석합니다.
                  </p>
                ) : null}

                {audioFiles.length > 1 ? (
                  <label className="ai-assist-attachment-select">
                    <span>정리할 음성</span>
                    <select
                      value={selectedAudioId}
                      onChange={(event) => setSelectedAudioId(event.target.value)}
                    >
                      <option value={ALL_AUDIO_FILES}>
                        전체 {audioFiles.length}개 종합 정리
                      </option>
                      {audioFiles.map((attachment) => (
                        <option key={attachment.id} value={attachment.id}>
                          {attachment.original_name}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : null}

                {audioFiles.length > 1 ? (
                  <p className="ai-assist-capability-note" role="status">
                    전체 정리는 완료된 받아쓰기를 파일 순서와 파일명별로 구분해 사용합니다.
                  </p>
                ) : null}
                {config.whisper_ready && pendingAudioTranscriptNames.length > 0 ? (
                  <p className="form-error" role="alert">
                    글자 변환 대기 중: {pendingAudioTranscriptNames.join(", ")}
                  </p>
                ) : null}

                <div className="ai-assist-quick-actions">
                  <button
                    type="button"
                    disabled={starting || restoringConversation || isWorking || images.length === 0}
                    onClick={() => void requestTurn("image_text")}
                  >
                    이미지 글자 읽기
                  </button>
                  <button
                    type="button"
                    disabled={starting || restoringConversation || isWorking || images.length === 0}
                    onClick={() => void requestTurn("image_explain")}
                  >
                    이미지 내용 설명
                  </button>
                  <button
                    type="button"
                    disabled={
                      starting ||
                      restoringConversation ||
                      isWorking ||
                      audioFiles.length === 0 ||
                      !config.whisper_ready
                    }
                    onClick={() => void requestTurn("audio_summary")}
                  >
                    {selectedAudioId === ALL_AUDIO_FILES && audioFiles.length > 1
                      ? "전체 음성 종합 정리"
                      : "음성 받아쓰기 정리"}
                  </button>
                  <button
                    type="button"
                    disabled={starting || restoringConversation || isWorking}
                    onClick={() => void requestTurn("summary")}
                  >
                    핵심 요약
                  </button>
                  <button
                    type="button"
                    disabled={starting || restoringConversation || isWorking}
                    onClick={() => void requestTurn("history_search")}
                  >
                    관련 과거기록 찾기
                  </button>
                  <button
                    type="button"
                    disabled={starting || restoringConversation || isWorking}
                    onClick={() => void requestTurn("risk_check")}
                  >
                    위험·누락 확인
                  </button>
                </div>

                {!config.whisper_ready ? (
                  <p className="ai-assist-capability-note" role="status">
                    음성 받아쓰기는 현재 준비 중입니다. 글 요약과 이미지 확인은 이용할 수 있습니다.
                  </p>
                ) : null}
              </>
            ) : null}

            <form className="ai-assist-question" onSubmit={askQuestion}>
              <label htmlFor={`ai-assist-question-${message.id}`}>
                {conversation && !newAnalysisMode ? "추가 질문" : "직접 질문"}
              </label>
              <textarea
                id={`ai-assist-question-${message.id}`}
                value={question}
                rows={2}
                maxLength={4000}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder={
                  conversation && !newAnalysisMode
                    ? "앞의 결과에서 더 확인할 내용을 적어 주세요."
                    : "이 대화와 관련해 AI에게 물어보세요."
                }
              />
              <button
                className="button button-primary"
                disabled={
                  starting || restoringConversation || isWorking || !question.trim()
                }
              >
                질문하기
              </button>
            </form>
          </>
        ) : null}

        {error ? <p className="form-error" role="alert">{showTechnicalDetails ? error : "요청을 확인하지 못했습니다. 잠시 후 다시 시도해 주세요."}</p> : null}
        {feedback ? <p className="form-success" role="status">{feedback}</p> : null}

        {conversation?.turns.length ? (
          <div className="ai-assist-turns">
            {conversation.turns.map((turn, index) => {
              const isLatest = index === conversation.turns.length - 1;
              const isFollowUp = turn.task_type === "question" && index > 0;
              const sameKindNumber = conversation.turns
                .slice(0, index + 1)
                .filter((item, itemIndex) =>
                  isFollowUp
                    ? item.task_type === "question" && itemIndex > 0
                    : item.task_type !== "question" || itemIndex === 0,
                ).length;
              const isExpanded = isLatest || expandedTurnIds.has(turn.id);
              const residentCandidates = (turn.resident_name_candidates ?? []).flatMap(
                (candidate) => {
                  const label = residentCandidateLabel(candidate);
                  return label ? [label] : [];
                },
              );
              const resultPreview = (
                turn.answer ??
                turn.visible_text ??
                (turn.error_message ? "AI 연결을 확인하지 못했습니다. 관련 원문을 확인해 주세요." : null) ??
                progressLabels[turn.status]
              ).replace(/\s+/g, " ");
              const turnEngine = showTechnicalDetails ? engineLabel(turn) : "";
              const currentShareNotice =
                shareNotice?.turnId === turn.id ? shareNotice : null;
              const contentId = `ai-assist-turn-${turn.id}`;

              return (
                <article
                  key={turn.id}
                  ref={isLatest ? latestTurnRef : undefined}
                  tabIndex={-1}
                  className={`status-${turn.status} ${
                    isFollowUp ? "follow-up" : "analysis"
                  } ${isLatest ? "latest" : "previous"}`}
                >
                  <header>
                    <div className="ai-assist-turn-heading">
                      <span>{isFollowUp ? `추가 질문 ${sameKindNumber}` : `분석 ${sameKindNumber}`}</span>
                      <strong>{taskLabels[turn.task_type]}</strong>
                    </div>
                    <div className="ai-assist-turn-controls">
                      <span aria-live={isLatest ? "polite" : "off"}>
                        {progressLabels[turn.status]}
                      </span>
                      {!isLatest ? (
                        <button
                          type="button"
                          aria-expanded={isExpanded}
                          aria-controls={contentId}
                          onClick={() => togglePreviousTurn(turn.id)}
                        >
                          {isExpanded ? "접기" : "이전 내용 보기"}
                        </button>
                      ) : null}
                    </div>
                  </header>

                  {!isExpanded ? (
                    <p className="ai-assist-collapsed-preview">
                      {resultPreview.length > 100
                        ? `${resultPreview.slice(0, 100)}…`
                        : resultPreview}
                    </p>
                  ) : (
                    <div className="ai-assist-turn-content" id={contentId}>
                      {turn.question ? (
                        <p className="ai-assist-user-question">{turn.question}</p>
                      ) : null}
                      {pendingStatuses.has(turn.status) ? (
                        <div className="ai-assist-working" role="status">
                          <span />
                          <p>{progressLabels[turn.status]}…</p>
                        </div>
                      ) : null}
                      {turn.service_context_notice ? (
                        <p className="ai-assist-context-notice">
                          {turn.service_context_notice}
                        </p>
                      ) : null}
                      {turn.visible_text ? (
                        <section>
                          <h3>읽은 글자</h3>
                          <pre>{turn.visible_text}</pre>
                        </section>
                      ) : null}
                      {residentCandidates.length ? (
                        <section className="ai-assist-resident-candidates">
                          <h3>현재 이용자 명단 대조 후보</h3>
                          <p>자동 확정이 아니므로 원문을 확인해 주세요.</p>
                          <ul>
                            {residentCandidates.map((candidate, candidateIndex) => (
                              <li key={`${turn.id}-resident-${candidateIndex}`}>
                                <span>{candidate.recognized}</span>
                                <strong aria-hidden="true">→</strong>
                                <span>{candidate.matched}</span>
                              </li>
                            ))}
                          </ul>
                        </section>
                      ) : null}
                      {turn.answer ? (
                        <section>
                          <h3>
                            {pendingStatuses.has(turn.status)
                              ? "규칙 기반 즉시 결과"
                              : "답변"}
                          </h3>
                          <p>{turn.answer}</p>
                        </section>
                      ) : null}
                      {turn.evidence.length ? (
                        <section>
                          <h3>근거</h3>
                          <ol>
                            {turn.evidence.map((item) => (
                              <li key={`${turn.id}-${item.source_no}-${item.statement}`}>
                                [{item.source_no}] {item.statement}
                              </li>
                            ))}
                          </ol>
                        </section>
                      ) : null}
                      {turn.uncertainties.length ? (
                        <section className="ai-assist-uncertainties">
                          <h3>직접 확인할 부분</h3>
                          <ul>
                            {turn.uncertainties.map((item) => (
                              <li key={item}>{item}</li>
                            ))}
                          </ul>
                        </section>
                      ) : null}
                      {turn.recommended_actions.length ? (
                        <section>
                          <h3>다음 업무 제안</h3>
                          <ul>
                            {turn.recommended_actions.map((item) => (
                              <li key={item}>{item}</li>
                            ))}
                          </ul>
                        </section>
                      ) : null}
                      {turn.error_message ? (
                        <p className="form-error">{showTechnicalDetails ? turn.error_message : "AI 연결을 확인하지 못했습니다. 관련 원문을 확인해 주세요."}</p>
                      ) : null}
                      <footer>
                        <div className="ai-assist-result-meta">
                          <small>
                            {turn.human_review_required
                              ? "AI 참고답변 · 사람의 최종 확인 필요"
                              : "AI 참고답변"}
                          </small>
                          {turnEngine ? (
                            <small className="ai-assist-engine">
                              분석 엔진: {turnEngine}
                            </small>
                          ) : null}
                        </div>
                        {showTechnicalDetails ? <div className="ai-assist-route-meta" aria-label="AI 처리 정보">
                          <span>{processingLocationLabels[turn.processing_location]}</span>
                          <span>
                            외부 전송 {turn.external_transmission ? "있음" : "없음"}
                          </span>
                          <span>{enhancementLabels[turn.enhancement_status]}</span>
                          <span>
                            {turn.fallback_active
                              ? "Fallback 적용"
                              : turn.fallback_state === "rules"
                                ? "규칙 기본 경로"
                                : "기본 경로"}
                          </span>
                        </div> : <p>답변은 근거 원문과 함께 확인해 주세요.</p>}
                        {turn.fallback_reason ? (
                          <p className="ai-assist-fallback-reason" role="status">
                            {showTechnicalDetails ? "전환 사유: " + turn.fallback_reason : "AI 연결이 늦거나 결과를 확인하지 못해 기본 결과를 표시했습니다."}
                          </p>
                        ) : null}
                        {turn.status === "completed" ? (
                          <div className="ai-assist-share-action">
                            <button
                              className="button button-secondary"
                              type="button"
                              disabled={sharingTurnId === turn.id || reviewOnly}
                              onClick={() => void shareTurn(turn)}
                            >
                              {reviewOnly
                                ? "대화방 공유 · 검토 잠금"
                                : sharingTurnId === turn.id
                                  ? "공유 중…"
                                  : "대화방에 공유"}
                            </button>
                            <span
                              className={
                                currentShareNotice
                                  ? `share-${currentShareNotice.tone}`
                                  : ""
                              }
                              role={
                                currentShareNotice?.tone === "error"
                                  ? "alert"
                                  : "status"
                              }
                              aria-live={
                                currentShareNotice?.tone === "error"
                                  ? "assertive"
                                  : "polite"
                              }
                            >
                              {currentShareNotice?.message ?? ""}
                            </span>
                          </div>
                        ) : null}
                      </footer>
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        ) : null}

      </section>
    </div>
  );
}
