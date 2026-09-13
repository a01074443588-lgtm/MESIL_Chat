type Fetcher = (path: string, options: RequestInit) => Promise<unknown>;
type Options = { preparationTimeoutMs?: number; answerTimeoutMs?: number; pollIntervalMs?: number };

export class RecordPreparationError extends Error {
  constructor(public code: string) {
    const notices: Record<string, string> = {
      model_prepare_timeout: '로컬 AI 모델 준비 제한시간을 초과했습니다. 관리자에게 모델 적재 상태를 확인해 주세요.',
      answer_timeout: 'AI 답변 응답시간이 초과되었습니다. 다시 시도해 주세요.',
      model_missing: '선택한 로컬 AI 모델을 찾지 못했습니다. 관리자에게 알려 주세요.',
      model_connection_error: '로컬 AI 서버에 연결하지 못했습니다. 연결 상태를 확인한 뒤 다시 시도해 주세요.',
      gpu_capacity_unverified: '다른 AI 작업과 함께 실행할 GPU 메모리 여유를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.',
      gpu_memory_insufficient: 'AI 처리에 필요한 GPU 메모리가 부족합니다. 관리자에게 알려 주세요.',
      model_prepare_busy: '다른 AI 작업이 진행 중입니다. 잠시 후 다시 시도해 주세요.',
      model_cold: '모델 준비 상태가 변경되었습니다. 다시 시도해 주세요.',
      summary_not_supported: 'AI 요약이 원문 근거 검사를 통과하지 못했습니다. 원문을 확인하고 다시 시도해 주세요.',
      summary_evidence_incomplete: '선택한 근거 전체를 확인한 요약을 만들지 못했습니다. 원문을 확인해 주세요.',
      summary_review_unavailable: 'AI 요약의 근거를 검토하지 못했습니다. 잠시 후 다시 시도해 주세요.',
      timeout: 'AI 요약 생성·검토 시간이 초과되었습니다. 다시 시도해 주세요.',
    };
    super(notices[code] ?? '로컬 AI 모델을 준비하지 못했습니다. 관리자에게 상태를 확인해 주세요.');
    this.name = 'RecordPreparationError';
  }
}

// Keep the authorized first query and the same cancellation/deadline behavior.
// Only an explicit cold-model response may enter the content-free prepare loop.
export async function runWorkdeskSummary<T>(fetcher: Fetcher, body: string, signal: AbortSignal,
  onPhase: (phase: string) => void, options: Options = {}): Promise<T> {
  const result = await runRecordQuestion<T>(async (path, request) => {
    if (path.endsWith('/model-ready')) {
      return fetcher('/api/workdesk/record-question/model-ready?feature=document_text', request);
    }
    try {
      return await fetcher('/api/workdesk/record-summary', request);
    } catch (error) {
      if (error instanceof Error && (error as Error & { status?: number }).status === 503 &&
          ['model_cold', 'model_not_ready', 'model_preparing'].includes(error.message)) {
        return { error_type: error.message };
      }
      throw error;
    }
  }, body, signal, onPhase, options);
  const pending = (result.answer as { error_type?: string })?.error_type;
  if (pending) throw new RecordPreparationError(pending);
  return result.answer;
}

function delay(ms: number, signal: AbortSignal) {
  signal.throwIfAborted();
  return new Promise<void>((resolve, reject) => {
    const cancel = () => { clearTimeout(timer); reject(signal.reason); };
    const timer = setTimeout(() => { signal.removeEventListener('abort', cancel); resolve(); }, ms);
    signal.addEventListener('abort', cancel, { once: true });
  });
}

// Content-free preparation and answer generation own separate deadlines.
export async function runRecordQuestion<T>(fetcher: Fetcher, body: string, signal: AbortSignal,
  onPhase: (phase: string) => void, options: Options = {}) {
  const controller = new AbortController();
  const cancelled = () => controller.abort(signal.reason);
  signal.addEventListener('abort', cancelled, { once: true });
  if (signal.aborted) cancelled();
  const started = performance.now();
  let timer = setTimeout(() => controller.abort(new RecordPreparationError('answer_timeout')),
    options.answerTimeoutMs ?? 40_000);
  try {
    controller.signal.throwIfAborted();
    onPhase('retrieving');
    const first = await fetcher('/api/workdesk/record-question', { method: 'POST', body, signal: controller.signal }) as T;
    controller.signal.throwIfAborted();
    const readiness = (first as { error_type?: string })?.error_type;
    if (!readiness || !['model_preparing', 'model_not_ready', 'model_cold', 'model_prepare_busy'].includes(readiness)) {
      return { answer: first, preparationMs: 0, totalMs: Math.round(performance.now() - started),
        prepared: { status: 'not_needed', cold_load_ms: undefined } };
    }
    // The first authorized query may already be a rule/clarification answer.
    // Only an explicit model-pending result enters the content-free warm loop.
    clearTimeout(timer);
    const preparationStarted = performance.now();
    timer = setTimeout(() => controller.abort(new RecordPreparationError('model_prepare_timeout')),
      options.preparationTimeoutMs ?? 180_000);
    onPhase('model_preparing');
    let serverBudgetApplied = false;
    let prepared: { status: string; phase?: string; error_type?: string; cold_load_ms?: number; total_ms?: number; preparation_budget_ms?: number };
    while (true) {
      prepared = await fetcher('/api/workdesk/record-question/model-ready',
        { method: 'POST', signal: controller.signal }) as typeof prepared;
      controller.signal.throwIfAborted();
      if (prepared.status === 'ready') break;
      if (prepared.status !== 'preparing') throw new RecordPreparationError(prepared.error_type ?? 'model_response_invalid');
      // Anchor to this request's original preparation start. Polling must not
      // renew the deadline, and an explicit caller cap always wins.
      const budget = prepared.preparation_budget_ms;
      if (!serverBudgetApplied && options.preparationTimeoutMs === undefined &&
          typeof budget === 'number' && Number.isFinite(budget) && budget > 0 && budget <= 480_000) {
        serverBudgetApplied = true;
        clearTimeout(timer);
        const remaining = Math.max(0, budget - (performance.now() - preparationStarted));
        timer = setTimeout(() => controller.abort(new RecordPreparationError('model_prepare_timeout')), remaining);
      }
      onPhase(prepared.phase === 'waiting_capacity' ? 'waiting_capacity' : 'model_preparing');
      await delay(options.pollIntervalMs ?? 1000, controller.signal);
    }
    clearTimeout(timer);
    const preparationMs = Math.round(performance.now() - preparationStarted);
    timer = setTimeout(() => controller.abort(new RecordPreparationError('answer_timeout')), options.answerTimeoutMs ?? 40_000);
    onPhase('retrieving');
    const answer = await fetcher('/api/workdesk/record-question', { method: 'POST', body, signal: controller.signal }) as T;
    controller.signal.throwIfAborted();
    return { answer, preparationMs, totalMs: Math.round(performance.now() - started), prepared };
  } catch (error) {
    if (controller.signal.aborted) throw controller.signal.reason;
    throw error;
  } finally {
    clearTimeout(timer);
    signal.removeEventListener('abort', cancelled);
  }
}
