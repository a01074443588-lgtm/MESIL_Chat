"use client";

import { useEffect, useRef, useState } from "react";
import { apiFetch, ApiError } from "../api";
import { runRecordQuestion, RecordPreparationError } from "../recordQuestionFlow";
import type { CareRecordAnswer, CareTopic, PeriodWorkdeskSource } from "../types";
import { AttachmentDisplay } from "./AttachmentDisplay";

const kindLabels: Record<CareTopic["kinds"][number], string> = {
  event: "확인된 기록", repeated: "반복 관찰", different: "다른 상태가 기록됨",
  followup: "후속 기록", conflict: "자료 충돌",
};

const BRIEFING_QUESTION = "선택한 기간의 돌봄 기록을 요약해 주세요.";

function recordTime(value: string) {
  return new Intl.DateTimeFormat("ko-KR", {
    month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
  }).format(new Date(value));
}

export function CareTopicList({ topics, residentId, onSelectResident, onEvidence, initialVisibleCount = 12, onVisibleCountChange }: {
  topics: CareTopic[];
  residentId: string;
  onSelectResident: (id: string) => void;
  onEvidence: (ids: string[]) => void;
  initialVisibleCount?: number;
  onVisibleCountChange?: (count: number) => void;
}) {
  const [visibleCount, setVisibleCount] = useState(initialVisibleCount);
  const grouped = residentId
    ? [...new Set(topics.map((topic) => topic.topic))].flatMap((name) => topics.filter((topic) => topic.topic === name))
    : topics;
  const visible = grouped.slice(0, visibleCount);
  if (!topics.length) return <p className="muted-box">선택한 기간에서 확인할 돌봄 기록이 없습니다. 기간을 바꾸어 확인할 수 있습니다.</p>;
  return <>
    <div className="care-topic-list">
      {visible.map((topic, topicIndex) => <article key={topic.key} className="care-topic-row">
        {!residentId || topicIndex === 0 || visible[topicIndex - 1].topic !== topic.topic ? <h4>{!residentId ? <><button type="button" className="care-resident-link" onClick={() => onSelectResident(topic.resident_id)}>{topic.resident_name}</button><span aria-hidden="true"> · </span></> : null}{topic.topic}</h4> : null}
        <p className="care-topic-summary">{topic.summary}</p>
        {residentId ? <ol className="care-topic-timeline">
          {topic.entries.map((entry, index) => <li key={`${entry.message_id}-${entry.comment_id ?? entry.attachment_id ?? "message"}-${index}`}>
            <div><time dateTime={entry.occurred_at}>{recordTime(entry.occurred_at)}</time><small>{kindLabels[entry.kind]}</small></div>
            <p>{entry.summary}</p>
          </li>)}
        </ol> : null}
        <footer>
          <span>{recordTime(topic.first_at)}{topic.first_at !== topic.latest_at ? ` ~ ${recordTime(topic.latest_at)}` : ""}</span>
          <button type="button" onClick={() => onEvidence(topic.evidence_ids)}>관련 기록 {topic.evidence_ids.length}건</button>
        </footer>
      </article>)}
    </div>
    {topics.length > visibleCount ? <button type="button" className="care-reader-more" onClick={() => { const next = visibleCount + 12; setVisibleCount(next); onVisibleCountChange?.(next); }}>남은 {topics.length - visibleCount}개 내용 더 보기</button> : null}
  </>;
}

export function CareEvidenceSources({ sources, onOpenSource, startDate, endDate }: {
  sources: PeriodWorkdeskSource[];
  onOpenSource: (roomId: string, messageId: string) => void | Promise<void>;
  startDate?: string;
  endDate?: string;
}) {
  function outsidePeriod(value: string) {
    if (!startDate || !endDate) return false;
    const day = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(value));
    return day < startDate || day > endDate;
  }
  return <div className="care-evidence-sources">
    {sources.length ? sources.map((source) => <article key={source.message.id}>
      <header><strong>{source.message.sender_name}</strong><time dateTime={source.message.created_at}>{recordTime(source.message.created_at)}</time></header>
      <small>{source.room_name}</small>
      {outsidePeriod(source.message.created_at) ? <p className="care-source-outside">선택 기간 밖의 원문 · 후속 기록의 배경입니다.</p> : null}
      <p className="care-source-original">{source.message.body || "첨부 기록"}</p>
      {source.message.attachments.map((attachment) => <AttachmentDisplay key={attachment.id} attachment={attachment} accessScope="workdesk" showExtraction canEditExtraction={false} />)}
      {source.comments.length ? <section className="care-source-replies" aria-label="관련 후속 답글"><strong>후속 답글</strong>{source.comments.map((comment) => <article key={comment.id}><header><strong>{comment.author_name}</strong><time dateTime={comment.created_at}>{recordTime(comment.created_at)}</time></header>{outsidePeriod(comment.created_at) ? <p className="care-source-outside">선택 기간 밖의 답글 · 이번 요약에 포함하지 않았습니다.</p> : null}<p className="care-source-original">{comment.body}</p></article>)}</section> : null}
      <button type="button" className="care-source-open" onClick={() => void onOpenSource(source.message.room_id, source.message.id)}>원래 대화로 이동</button>
    </article>) : <p className="muted-box">표시할 원문을 찾지 못했습니다. 브리핑을 다시 조회해 주세요.</p>}
  </div>;
}

export function CareBriefingReader({ topics, sources, sourceIds, startDate, endDate, rangeMode = "fixed", residentId, residentName, roomId, messageType = "", truncated, onSelectResident, onOpenSource, onCompare, initialVisibleCount, onVisibleCountChange }: {
  topics: CareTopic[];
  sources: PeriodWorkdeskSource[];
  sourceIds?: string[];
  startDate: string;
  endDate: string;
  rangeMode?: "default" | "fixed";
  residentId: string;
  residentName: string;
  roomId: string;
  messageType?: string;
  truncated: boolean;
  onSelectResident: (id: string) => void;
  onOpenSource: (roomId: string, messageId: string) => void | Promise<void>;
  onCompare: () => void;
  initialVisibleCount?: number;
  onVisibleCountChange?: (count: number) => void;
}) {
  const [evidence, setEvidence] = useState<PeriodWorkdeskSource[] | null>(null);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [evidenceError, setEvidenceError] = useState("");
  const evidenceRequestRef = useRef<AbortController | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<CareRecordAnswer | null>(null);
  const [questionLoading, setQuestionLoading] = useState(false);
  const [questionPhase, setQuestionPhase] = useState("retrieving");
  const [questionError, setQuestionError] = useState("");
  const [copyStatus, setCopyStatus] = useState("");
  const evidenceRef = useRef<HTMLElement | null>(null);
  const evidenceTriggerRef = useRef<HTMLElement | null>(null);
  const requestRef = useRef<AbortController | null>(null);
  const questionFlightRef = useRef(false);
  const questionRef = useRef<HTMLTextAreaElement | null>(null);
  const questionSectionRef = useRef<HTMLElement | null>(null);
  const lastRequestRef = useRef<{ question: string; rangeMode: "default" | "fixed" } | null>(null);
  useEffect(() => {
    const changed = () => { requestRef.current?.abort(); evidenceRequestRef.current?.abort(); setAnswer(null); setEvidence(null); setQuestionLoading(false); setQuestionError("어르신 연결이 바뀌었습니다. 다시 질문해 주세요."); };
    window.addEventListener("mesil-resident-links-changed", changed);
    return () => window.removeEventListener("mesil-resident-links-changed", changed);
  }, []);
  const scopeLabel = `${residentName || "전체 어르신"} · ${startDate} ~ ${endDate}`;

  useEffect(() => () => { requestRef.current?.abort(); evidenceRequestRef.current?.abort(); }, []);
  useEffect(() => {
    const controller = new AbortController();
    // No resident, scope, question or record content is sent for prewarming.
    void apiFetch("/api/workdesk/record-question/model-ready", { method: "POST", signal: controller.signal }).catch(() => {});
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (evidence) evidenceRef.current?.focus({ preventScroll: true });
  }, [evidence]);

  async function openEvidence(ids: string[], scopeStart = startDate, scopeEnd = endDate, scopeResident: string | null = residentId || null) {
    // Evidence is always fetched again with the current scope and permissions.
    evidenceTriggerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    evidenceRequestRef.current?.abort();
    setEvidenceError("");
    const controller = new AbortController(); evidenceRequestRef.current = controller;
    setEvidence([]); setEvidenceLoading(true);
    try {
      const result: PeriodWorkdeskSource[] = [];
      for (let offset = 0; offset < ids.length; offset += 100) {
        result.push(...await apiFetch<PeriodWorkdeskSource[]>("/api/workdesk/period-review/evidence", { method: "POST", signal: controller.signal,
          body: JSON.stringify({ start_date: scopeStart, end_date: scopeEnd, resident_id: scopeResident, room_id: roomId || null, message_type: messageType || null, message_ids: ids.slice(offset, offset + 100) }),
        }));
      }
      if (!controller.signal.aborted) setEvidence(result);
    } catch {
      if (!controller.signal.aborted) setEvidenceError("현재 권한과 기록을 확인하지 못했습니다. 브리핑을 다시 조회해 주세요.");
    } finally { if (!controller.signal.aborted) setEvidenceLoading(false); }
  }
  function closeEvidence() {
    evidenceRequestRef.current?.abort();
    setEvidenceLoading(false);
    setEvidence(null);
    evidenceTriggerRef.current?.focus({ preventScroll: true });
  }

  async function askRecords(retry = false, briefing = false) {
    const requestedQuestion = briefing ? BRIEFING_QUESTION
      : retry ? lastRequestRef.current?.question ?? answer?.question ?? question.trim() : question.trim();
    const requestedRangeMode = briefing ? "fixed"
      : retry ? lastRequestRef.current?.rangeMode ?? rangeMode : rangeMode;
    if (!requestedQuestion || questionFlightRef.current) return;
    lastRequestRef.current = { question: requestedQuestion, rangeMode: requestedRangeMode };
    questionFlightRef.current = true;
    const controller = new AbortController();
    requestRef.current = controller;
    setQuestionLoading(true);
    setQuestionPhase("retrieving");
    setQuestionError("");
    // A failed answer remains available throughout a retry and connection errors.
    if (!retry) setAnswer(null);
    setCopyStatus("");
    let answerStarted = false;
    let polling = false;
    const poll = window.setInterval(async () => {
      if (!answerStarted || polling || controller.signal.aborted) return;
      polling = true;
      try {
        const status = await apiFetch<{ phase: string }>("/api/workdesk/record-question/progress", { signal: controller.signal });
        if (!controller.signal.aborted && ["retrieving", "preparing", "verifying"].includes(status.phase)) setQuestionPhase(status.phase);
      } catch { /* The answer request remains authoritative. */ }
      finally { polling = false; }
    }, 1000);
    try {
      const result = await runRecordQuestion<CareRecordAnswer>(apiFetch,
        JSON.stringify({ start_date: startDate, end_date: endDate, range_mode: requestedRangeMode, resident_id: residentId || null, room_id: roomId || null, message_type: messageType || null, question: requestedQuestion }),
        controller.signal, phase => { answerStarted = !["model_preparing", "waiting_capacity"].includes(phase); setQuestionPhase(phase); });
      if (!controller.signal.aborted) {
        evidenceRequestRef.current?.abort();
        setEvidence(null);
        setAnswer({ ...result.answer, performance: { ...result.answer.performance,
          client_model_preparation_ms: result.preparationMs, client_total_ms: result.totalMs,
          client_preparation_cold_load_ms: result.prepared.cold_load_ms ?? null } });
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 403) {
        evidenceRequestRef.current?.abort();
        setEvidence(null);
        setAnswer(null);
      }
      if (!controller.signal.aborted) setQuestionError(error instanceof RecordPreparationError ? error.message : error instanceof ApiError
        ? error.status >= 500 ? "서버 오류로 답변을 받지 못했습니다. 잠시 후 다시 시도해 주세요." : error.message
        : "서버와 연결이 끊겼습니다. 연결 상태를 확인하고 다시 시도해 주세요.");
    } finally {
      window.clearInterval(poll);
      questionFlightRef.current = false;
      setQuestionLoading(false);
    }
  }

  async function copyAnswer() {
    if (!answer) return;
    const text = `${answer.resolved_resident_name || residentName || "전체 어르신"} · ${answer.period_start} ~ ${answer.period_end}\n질문: ${answer.question}\n${answer.answer}${answer.unknowns?.length ? `\n미확인: ${answer.unknowns.join(" ")}` : ""}${answer.limitation ? `\n${answer.limitation}` : ""}`;
    try {
      await navigator.clipboard.writeText(text);
      setCopyStatus("복사했습니다.");
    } catch {
      setCopyStatus("복사하지 못했습니다. 답변을 선택해 복사해 주세요.");
    }
  }

  return <section className={`care-reader${evidence ? " has-evidence" : ""}`} aria-label="돌봄 내용과 경과">
    <div className="care-reader-main">
      <header className="care-reader-heading">
        {residentId ? <button type="button" className="care-reader-back" onClick={() => onSelectResident("")}>← 전체 어르신으로 돌아가기</button> : null}
        <h3>{residentId ? `${residentName}의 경과` : "최근에 알아둘 내용"}</h3>
        <button type="button" disabled={questionLoading} onClick={() => {
          void askRecords(false, true);
          questionSectionRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
        }}>AI 브리핑 만들기</button>
        <small>선택 기간의 기록을 바탕으로 요약합니다. 확인 범위와 근거는 결과에서 확인해 주세요.</small>
      </header>
      {truncated ? <p className="form-warning">기록이 많아 일부 구간은 500건까지 확인했습니다. 전체 확인이 필요하면 기간을 나누어 조회해 주세요.</p> : null}
      <CareTopicList topics={topics} residentId={residentId} onSelectResident={onSelectResident} onEvidence={openEvidence} initialVisibleCount={initialVisibleCount} onVisibleCountChange={onVisibleCountChange} />
      {residentId && (sourceIds?.length || sources.length) ? <button type="button" className="care-reader-more" onClick={() => void openEvidence(sourceIds ?? sources.map((source) => source.message.id))}>이 기간의 기록 더 보기</button> : null}
      <section className="care-record-question" aria-label="기록에 질문하기" ref={questionSectionRef}>
        <h3>기록에 질문하기</h3>
        <p className="care-question-scope">{scopeLabel} 기록에서 찾습니다.</p>
        {!answer ? <div className="care-question-examples">{["최근에 어떠셨어?", "전보다 달라진 게 있어?", "보호자에게 말할 내용 있어?"].map((example) => <button type="button" key={example} onClick={() => { setQuestion(example); questionRef.current?.focus(); }}>{example}</button>)}</div> : null}
        <form onSubmit={(event) => { event.preventDefault(); void askRecords(); }}>
          <label htmlFor="care-record-question">궁금한 내용</label>
          <textarea id="care-record-question" ref={questionRef} value={question} disabled={questionLoading} maxLength={500} rows={2} placeholder="궁금한 내용을 평소 말투로 자유롭게 물어보세요." onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => {
            const nativeEvent = event.nativeEvent;
              const isComposing = ("isComposing" in event && event.isComposing === true) || nativeEvent.isComposing || nativeEvent.keyCode === 229;
            if (event.key !== "Enter" || event.shiftKey || isComposing) return;
            event.preventDefault();
            if (!question.trim() || questionLoading || questionFlightRef.current) return;
            event.currentTarget.form?.requestSubmit();
          }} aria-describedby="care-record-question-keyboard-hint" />
          <small id="care-record-question-keyboard-hint" className="care-question-keyboard-hint">Enter로 질문 · Shift+Enter로 줄바꿈</small>
          <button type="submit" disabled={questionLoading || !question.trim()}>{questionLoading ? "답변 확인 중…" : "기록에서 답 찾기"}</button>
        </form>
        {questionLoading ? <div><p role="status" className="care-answer-progress">{questionPhase === "waiting_capacity" ? "다른 AI 작업이 끝나거나 GPU 여유가 확보되기를 기다리고 있습니다. 기존 작업은 중단하지 않습니다." : questionPhase === "model_preparing" ? "로컬 AI 모델을 준비하고 있습니다. 처음 실행할 때는 시간이 걸릴 수 있습니다." : questionPhase === "retrieving" ? "관련 기록을 찾고 있습니다." : questionPhase === "preparing" ? "답변을 정리하고 있습니다." : "답변과 근거를 확인하고 있습니다."}</p><button type="button" onClick={() => { requestRef.current?.abort(); setQuestionError("질문 대기를 취소했습니다. 다시 질문할 수 있습니다. 공유 모델 준비는 다른 사용자를 위해 계속될 수 있습니다."); }}>질문 취소</button></div> : null}
        {questionError ? <div className="form-error" role="alert"><p>{questionError}</p>{!answer ? <button type="button" disabled={questionLoading} onClick={() => void askRecords(true)}>질문 다시 시도</button> : null}</div> : null}
        {answer ? <article className="care-record-answer" aria-live="polite">
          <small>{answer.resolved_resident_name || residentName || "전체 어르신"} · {answer.period_start} ~ {answer.period_end}</small>
          <p className="care-answer-method">{answer.processing_method === "local_ai" && answer.generation_verified ? "로컬 AI" : answer.processing_method === "failed" ? "AI 답변 미완성" : "확인 결과"}</p>
          <h4>{answer.question}</h4>
          {answer.answer_sentences?.length ? <p>{answer.answer_sentences.map((sentence, index) => <span key={index}>{sentence.text} <button type="button" aria-label={`답변 ${index + 1}번 문장 근거`} onClick={() => void openEvidence(sentence.evidence_ids, answer.period_start, answer.period_end, answer.resident_id)}>[{index + 1}]</button>{" "}</span>)}</p> : <p>{answer.answer || "이번에는 AI 답변을 완성하지 못했습니다."}</p>}
          {answer.unknowns?.length ? <div className="care-unknowns"><strong>아직 확인되지 않은 내용</strong><ul>{answer.unknowns.map((item) => <li key={item}>{item}</li>)}</ul></div> : null}
          {answer.fallback_notice ? <p className="care-answer-limit">{answer.fallback_notice} <button type="button" disabled={questionLoading} onClick={() => void askRecords(true)}>다시 질문하기</button></p> : null}
          {answer.limitation ? <p className="care-answer-limit">{answer.limitation}</p> : null}
          <div>{answer.evidence_ids.length ? <button type="button" onClick={() => openEvidence(answer.evidence_ids, answer.period_start, answer.period_end, answer.resident_id)}>근거 기록 {answer.evidence_ids.length}건 보기</button> : null}<button type="button" onClick={() => void copyAnswer()}>내용 복사</button><span role="status">{copyStatus}</span></div>
        </article> : null}
      </section>
      {residentId ? <button type="button" className="care-reader-compare" onClick={onCompare}>기존 서류와 비교</button> : null}
    </div>
    {evidence ? <aside className="care-reader-evidence" ref={evidenceRef} tabIndex={-1} aria-label="관련 기록 원문" onKeyDown={(event) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeEvidence(); }
      if (event.key === "Tab") {
        const elements = evidenceRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input, textarea, select, [tabindex="0"]');
        const first = elements?.[0]; const last = elements?.[elements.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === evidenceRef.current)) { event.preventDefault(); last?.focus(); }
        if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    }}>
      <header className="care-evidence-heading"><h3>관련 기록 원문</h3><button type="button" aria-label="근거 닫기" onClick={closeEvidence}>닫기</button></header>
      {evidenceLoading ? <p role="status">근거 기록 조회 중…</p> : evidenceError ? <p role="alert">{evidenceError}</p> : <CareEvidenceSources sources={evidence} onOpenSource={onOpenSource} startDate={startDate} endDate={endDate} />}
    </aside> : null}
  </section>;
}
