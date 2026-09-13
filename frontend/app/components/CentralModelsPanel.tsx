"use client";

import { useEffect, useRef, useState, type ReactNode, type KeyboardEvent } from "react";
import { apiFetch } from "../api";
import type { AiSystemProviderId, AiSystemStatus } from "../types";

type Selection = string | { provider: AiSystemProviderId; model: string };
type Catalog = { status: "needs_check" | "connected" | "failed" | "rules"; models: string[]; message: string; failure_code?: "address_missing" | "connection_failed" | "catalog_empty" | "catalog_format_invalid" | "catalog_failed" | null; elapsed_ms?: number; current_model_listed?: boolean };
type Policy = {
  default_provider: AiSystemProviderId;
  inheritance_version: 1 | 2;
  execution_environment: "local" | "internal" | "external" | "rules";
  text_default_model: string | null;
  text_fallback_model: string | null;
  text_fallback_candidate: string | null;
  fallback_qualified: boolean;
  vision_default_model: string | null;
  vision_fallback_model: string | null;
  feature_overrides: Record<string, Selection>;
  timeout_seconds: number;
  context_tokens: number;
  max_input_chars: number;
  real_record_logging_verified: boolean;
  environment_override: boolean;
  connection: Catalog;
  image_verified: boolean;
  legacy_image_fallback: { provider: AiSystemProviderId; model: string } | null;
  recent_runs?: Record<string, { processing_method: string; model_used: string | null; ai_elapsed_ms: number }>;
  features: Record<string, { model: string | null; inheritance: string }>;
};
const featureLabels: Record<string, string> = { search_summary: "대화 검색 요약", care_record_question: "돌봄브리핑 질문", document_text: "일반 문서 정리", precision: "보고서·상세 분석", image_reading: "이미지 판독", stt: "음성 받아쓰기" };
const environmentLabels = { local: "이 컴퓨터의 Ollama", internal: "우리 기관의 AI 서버", external: "외부 AI API", rules: "AI 없이 기본 기능 사용" };
const payloadKeys = ["default_provider", "inheritance_version", "execution_environment", "text_default_model", "text_fallback_model", "text_fallback_candidate", "fallback_qualified", "vision_default_model", "vision_fallback_model", "feature_overrides", "timeout_seconds", "context_tokens", "max_input_chars", "real_record_logging_verified"] as const;
function configuration(policy: Policy) { return Object.fromEntries(payloadKeys.map((key) => [key, policy[key]])); }
function selectionModel(value: Selection | undefined) { return typeof value === "string" ? value : value?.model ?? ""; }

export function CentralModelsPanel({ readOnly, status, externalControls, externalProvider, diagnostics, onChanged, onDirtyChange, onExternalProviderChange }: { readOnly: boolean; status: AiSystemStatus; externalControls: ReactNode; externalProvider: AiSystemProviderId; diagnostics: ReactNode; onChanged: () => void; onDirtyChange: (dirty: boolean) => void; onExternalProviderChange: (provider: AiSystemProviderId) => void }) {
  const [saved, setSaved] = useState<Policy | null>(null);
  const [draft, setDraft] = useState<Policy | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [environment, setEnvironment] = useState<Policy["execution_environment"] | "">("");
  const [endpoint, setEndpoint] = useState("");
  const [imageNotice, setImageNotice] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  const modelButton = useRef<HTMLButtonElement>(null);
  const [modelOpen, setModelOpen] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    apiFetch<Policy>("/api/ai/system/central-models", { signal: controller.signal }).then((next) => { setSaved(next); setDraft(next); setCatalog(next.connection); }).catch(() => { if (!controller.signal.aborted) setNotice("AI 설정을 불러오지 못했습니다."); });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (modelOpen) dialog.current?.showModal();
    else if (dialog.current?.open) dialog.current.close();
  }, [modelOpen]);
  const dirty = Boolean(saved && draft && JSON.stringify(configuration(saved)) !== JSON.stringify(configuration(draft)));
  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange]);
  useEffect(() => { if (saved?.execution_environment === "external") onExternalProviderChange(saved.default_provider); }, [saved?.execution_environment, saved?.default_provider, onExternalProviderChange]);
  async function refreshModels() {
    setBusy(true);
    try { setCatalog(await apiFetch<Catalog>("/api/ai/system/central-models/catalog", { method: "POST" })); }
    catch { setCatalog({ status: "failed", models: [], message: "모델 목록을 확인하지 못했습니다. 저장된 설정은 유지됩니다.", failure_code: "catalog_failed" }); }
    finally { setBusy(false); }
  }
  function closeModels() { setDraft(saved); setModelOpen(false); setImageNotice(""); modelButton.current?.focus(); }
  function keepDialogFocus(event: KeyboardEvent<HTMLDialogElement>) {
    if (event.key === "Escape") { event.stopPropagation(); return; }
    if (event.key !== "Tab") return;
    const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled),select:not(:disabled),input:not(:disabled),[tabindex="0"]')).filter((element) => element.getClientRects().length > 0);
    const target = event.shiftKey ? controls.at(-1) : controls[0];
    if (!controls.length || (event.shiftKey && document.activeElement === controls[0]) || (!event.shiftKey && document.activeElement === controls.at(-1))) {
      event.preventDefault(); target?.focus();
    }
  }
  function openModels() {
    if (dirty && !window.confirm("고급 설정의 저장하지 않은 변경을 취소하고 모델 선택을 열까요?")) return;
    setDraft(saved); setModelOpen(true); setNotice(""); setImageNotice(""); void refreshModels();
  }
  async function save() {
    if (!draft) return;
    setBusy(true); setNotice("");
    try {
      const next = await apiFetch<Policy>("/api/ai/system/settings/central-models", { method: "PUT", body: JSON.stringify(configuration(draft)) });
      setSaved(next); setDraft(next); setCatalog(next.connection); setNotice("AI 설정을 저장했습니다."); setModelOpen(false); modelButton.current?.focus(); onChanged();
    } catch { setNotice("저장하지 못했습니다. 기존 설정은 유지됩니다. 입력값과 관리자 권한을 확인해 주세요."); }
    finally { setBusy(false); }
  }
  async function saveEnvironment() {
    if (!environment) return;
    if (dirty && !window.confirm("저장하지 않은 모델 변경을 취소하고 실행 환경을 바꿀까요?")) return;
    setBusy(true); setNotice("");
    try {
      const next = await apiFetch<Policy>("/api/ai/system/central-models/connection", { method: "PUT", body: JSON.stringify({ environment, provider: environment === "external" ? externalProvider : "ollama", base_url: endpoint.trim() || null }) });
      setSaved(next); setDraft(next); setCatalog(next.connection); setEndpoint(""); setNotice("실행 환경을 저장했습니다. 연결 확인 후 사용할 모델을 선택해 주세요."); onChanged();
    } catch { setNotice("실행 환경을 저장하지 못했습니다. 주소와 관리자 권한을 확인해 주세요."); }
    finally { setBusy(false); }
  }
  async function verifyImage() {
    const model = draft?.vision_default_model || draft?.text_default_model;
    if (!model) return;
    setBusy(true); setImageNotice("개인정보가 없는 시험 이미지로 확인하고 있습니다.");
    try {
      const result = await apiFetch<{ verified: boolean; message: string }>("/api/ai/system/central-models/verify-image", { method: "POST", body: JSON.stringify({ model }) });
      setImageNotice(result.message);
      const next = await apiFetch<Policy>("/api/ai/system/central-models");
      setSaved(next);
    } catch { setImageNotice("이미지 기능을 확인하지 못했습니다. 기존 판독 경로를 유지합니다."); }
    finally { setBusy(false); }
  }
  async function checkTextContract() {
    if (!saved || saved.default_provider === "rules") return;
    setBusy(true);
    try {
      const result = await apiFetch<{ structured_contract_ok: boolean }>("/api/ai/system/providers/" + saved.default_provider + "/test", { method: "POST", body: JSON.stringify({ run_contract_test: true }) });
      setNotice(result.structured_contract_ok ? "현재 모델의 합성자료 결과 형식을 확인했습니다." : "결과 형식을 확인하지 못했습니다. 저장된 모델은 유지됩니다."); onChanged();
    } catch { setNotice("결과 형식을 확인하지 못했습니다. 저장된 모델은 유지됩니다."); }
    finally { setBusy(false); }
  }
  const disabled = readOnly || busy || Boolean(saved?.environment_override);
  function modelOptions(current: string | null) {
    const values = catalog?.models ?? [];
    return <><option value="">모델을 선택해 주세요</option>{current && !values.includes(current) ? <option value={current}>{current} · 현재 저장값, 목록 미확인</option> : null}{values.map((value) => <option key={value} value={value}>{value}</option>)}</>;
  }
  if (!draft || !saved) return <p role="status">{notice || "AI 설정 확인 중…"}</p>;
  const providerName = status.providers.find((item) => item.provider === saved.default_provider)?.display_name || saved.default_provider;
  return <div className="ai-settings-body">
    {notice ? <p className="ai-settings-notice" role="status">{notice}</p> : null}
    <section className="ai-current-card" aria-label="현재 사용 중인 AI">
      <span className="eyebrow">현재 사용 중</span>
      <h3>{saved.execution_environment === "external" ? providerName : environmentLabels[saved.execution_environment]}</h3>
      <p className="ai-current-model">{saved.default_provider === "rules" ? "기본 검색과 요약" : saved.text_default_model || "기본 모델 선택 필요"}</p>
      <p className={`ai-connection-state state-${catalog?.status ?? "needs_check"}`} role="status">{busy ? "확인하고 있습니다…" : catalog?.status === "connected" ? "연결됨 · 모델 목록 확인" : catalog?.status === "failed" ? catalog.message : catalog?.status === "rules" || saved.default_provider === "rules" ? "AI 없이 기본 기능 사용 중" : "확인 필요"}</p>
      <div className="ai-primary-actions"><button ref={modelButton} type="button" className="button button-primary" disabled={disabled || saved.default_provider === "rules"} onClick={openModels}>모델 변경</button><button type="button" className="button button-secondary" disabled={readOnly || busy} onClick={() => void refreshModels()}>연결 확인</button></div>
    </section>
    <details className="ai-settings-disclosure"><summary>다른 환경 연결</summary>
      <fieldset disabled={disabled}><legend>AI는 어디에서 실행할까요?</legend>
        {Object.entries(environmentLabels).map(([key, label]) => <label className="ai-environment-choice" key={key}><input type="radio" name="ai-environment" value={key} checked={environment === key} onChange={() => { setEnvironment(key as Policy["execution_environment"]); setEndpoint(""); }} />{label}</label>)}
      </fieldset>
      {environment === "internal" ? <label>기관 서버 주소<input type="url" value={endpoint} autoComplete="off" disabled={disabled} placeholder="기존 주소를 유지하려면 비워 두세요" onChange={(event) => setEndpoint(event.target.value)} /></label> : null}
      {environment === "local" ? <p>이 컴퓨터의 Ollama 연결을 사용합니다. 저장 후 연결을 확인하고 실제 모델 목록에서 선택하세요.</p> : null}
      {environment === "external" ? <div className="ai-external-controls">{externalControls}<p>개인정보가 없는 작업용입니다. 실제 돌봄자료는 외부 AI로 자동 전송하지 않으며, 내부 AI를 사용할 수 없으면 기본 결과를 표시합니다.</p></div> : null}
      {environment === "rules" ? <p>AI 연결 없이 규칙 기반 검색과 요약을 사용합니다.</p> : null}
      {environment ? <button type="button" className="button button-primary" disabled={disabled || (environment === "internal" && saved.execution_environment !== "internal" && !endpoint.trim())} onClick={() => void saveEnvironment()}>이 환경 사용</button> : null}
      <p className="ai-setting-help">고성능 PC가 없어도 기관 서버를 연결하거나 기본 기능을 사용할 수 있습니다. 일반 직원은 모델 설정 없이 사용합니다.</p>
    </details>
    <details className="ai-settings-disclosure"><summary>고급 설정</summary>
      {saved.environment_override ? <p>보호된 환경 설정이 적용되어 화면에서는 변경할 수 없습니다.</p> : null}
      <p>명시한 기능만 기본값 대신 별도 모델을 사용합니다. 비우면 중앙 기본값을 상속합니다. 이전 설정은 호환을 위해 보존됩니다.</p>
      <label>텍스트 기본 모델 직접 입력<input disabled={disabled} value={draft.text_default_model ?? ""} onChange={(event) => setDraft({ ...draft, text_default_model: event.target.value || null })} /></label>
      <label>이미지 모델 직접 입력 (비우면 텍스트 상속)<input disabled={disabled} value={draft.vision_default_model ?? ""} onChange={(event) => setDraft({ ...draft, vision_default_model: event.target.value || null })} /></label>
      <label>텍스트 대체 후보<input disabled={disabled} value={draft.text_fallback_candidate ?? ""} onChange={(event) => setDraft({ ...draft, text_fallback_candidate: event.target.value || null })} /></label>
      <p>활성 텍스트 대체 모델: {saved.text_fallback_model || "없음"}. 성능·안정성 검증 전에는 후보만 등록합니다.</p>
      <label>이미지 대체 모델<input disabled={disabled} value={draft.vision_fallback_model ?? ""} onChange={(event) => setDraft({ ...draft, vision_fallback_model: event.target.value || null })} /></label>
      <p>이미지 입력이 검증된 모델만 새 이미지 경로에 사용합니다. 기존 판독 설정: {saved.legacy_image_fallback?.model || "없음"} (읽기 전용 호환 경로).</p>
      {Object.entries(featureLabels).map(([key, label]) => <fieldset key={key} disabled={disabled}><legend>{label}</legend>
        <label>제공자<select aria-label={`${label} 제공자`} value={typeof draft.feature_overrides[key] === "object" ? draft.feature_overrides[key].provider : draft.default_provider} onChange={(event) => { const model = selectionModel(draft.feature_overrides[key]); if (model) setDraft({ ...draft, feature_overrides: { ...draft.feature_overrides, [key]: { provider: event.target.value as AiSystemProviderId, model } } }); }}><option value={draft.default_provider}>중앙 제공자</option>{status.providers.filter((provider) => provider.provider !== draft.default_provider).map((provider) => <option key={provider.provider} value={provider.provider}>{provider.display_name}</option>)}</select></label>
        <label>별도 모델 (비우면 상속)<input aria-label={`${label} 별도 모델`} value={selectionModel(draft.feature_overrides[key])} onChange={(event) => { const overrides = { ...draft.feature_overrides }; const model = event.target.value; const previous = overrides[key]; if (!model.trim()) delete overrides[key]; else overrides[key] = typeof previous === "object" ? { ...previous, model } : model; setDraft({ ...draft, feature_overrides: overrides }); }} /></label>
        {saved.recent_runs?.[key] ? <small>최근 실행: {saved.recent_runs[key].model_used || "규칙 기반"} · {saved.recent_runs[key].ai_elapsed_ms}ms</small> : null}
      </fieldset>)}
      <label>전체 질문 시간 제한 (초)<input type="number" min={2} max={30} disabled={disabled} value={draft.timeout_seconds} onChange={(event) => setDraft({ ...draft, timeout_seconds: Number(event.target.value) })} /></label>
      <p>{saved.real_record_logging_verified ? "실제 기록 로그 보호 확인 설정됨" : "로그 보호 미검증 · 새 기록 질문 모델 경로는 합성자료만 허용"}</p>
      <button type="button" className="button button-secondary" disabled={disabled || saved.default_provider === "rules"} onClick={() => void checkTextContract()}>현재 모델 결과 형식 확인</button>
      <div className="ai-primary-actions"><button type="button" className="button button-primary" disabled={disabled || !dirty} onClick={() => void save()}>고급 설정 저장</button><button type="button" className="button button-secondary" disabled={busy} onClick={() => setDraft(saved)}>변경 취소</button></div>
      {diagnostics}
    </details>
    <dialog ref={dialog} className="ai-model-dialog" aria-labelledby="ai-model-dialog-title" onKeyDown={keepDialogFocus} onCancel={(event) => { event.preventDefault(); if (!busy) closeModels(); }} onClose={() => { setModelOpen(false); modelButton.current?.focus(); }}>
      <header><h3 id="ai-model-dialog-title">모델 변경</h3><button type="button" className="icon-button" aria-label="모델 변경 닫기" disabled={busy} onClick={closeModels}>×</button></header>
      <p>현재 저장된 모델: <strong>{saved.text_default_model || "미설정"}</strong></p>
      <label>텍스트 기본 모델<select aria-label="텍스트 기본 모델" autoFocus value={draft.text_default_model ?? ""} disabled={disabled} onChange={(event) => { setImageNotice(""); setDraft({ ...draft, text_default_model: event.target.value || null }); }}>{modelOptions(saved.text_default_model)}</select></label>
      <div className="ai-model-catalog-state"><p role="status">{busy ? "모델 목록을 확인하고 있습니다…" : catalog?.message}</p><button type="button" className="button button-secondary" disabled={readOnly || busy} onClick={() => void refreshModels()}>모델 목록 새로고침</button></div>
      <fieldset disabled={disabled}><legend>이미지 모델 설정</legend>
        <label className="ai-environment-choice"><input type="radio" name="image-model-mode" checked={draft.vision_default_model === null} onChange={() => { setImageNotice(""); setDraft({ ...draft, vision_default_model: null }); }} />텍스트 기본 모델 사용</label>
        <label className="ai-environment-choice"><input type="radio" name="image-model-mode" checked={draft.vision_default_model !== null} onChange={() => { setImageNotice(""); setDraft({ ...draft, vision_default_model: saved.vision_default_model ?? "" }); }} />이미지 전용 모델 별도 선택</label>
        {draft.vision_default_model !== null ? <label>이미지 전용 모델<select value={draft.vision_default_model} onChange={(event) => { setImageNotice(""); setDraft({ ...draft, vision_default_model: event.target.value }); }}>{modelOptions(saved.vision_default_model)}</select></label> : null}
      </fieldset>
      <p>{imageNotice || (saved.image_verified && (draft.vision_default_model || draft.text_default_model) === (saved.vision_default_model || saved.text_default_model) ? "저장된 이미지 모델의 시험 입력을 확인했습니다." : "이미지 기능 미검증 · 확인 전에는 기존 안전한 판독 경로를 유지합니다.")}</p>
      <button type="button" className="button button-secondary" disabled={disabled || !(draft.vision_default_model || draft.text_default_model)} onClick={() => void verifyImage()}>이미지 기능 확인</button>
      {notice ? <p role="status">{notice}</p> : null}
      <footer><button type="button" className="button button-secondary" disabled={busy} onClick={closeModels}>취소</button><button type="button" className="button button-primary" disabled={disabled || !draft.text_default_model || draft.vision_default_model === ""} onClick={() => void save()}>저장</button></footer>
    </dialog>
  </div>;
}
