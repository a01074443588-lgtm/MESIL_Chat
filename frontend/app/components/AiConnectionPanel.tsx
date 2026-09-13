"use client";

import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "../api";
import type { AiProviderConnectResult, AiSystemProviderId, AiSystemStatus } from "../types";
import { CentralModelsPanel } from "./CentralModelsPanel";

const externalProviders: AiSystemProviderId[] = ["openai", "gemini", "nvidia", "anthropic", "ollama_cloud"];

export function AiConnectionPanel({ onClose, reviewOnly = false }: { onClose: () => void; reviewOnly?: boolean }) {
  const [status, setStatus] = useState<AiSystemStatus | null>(null);
  const [error, setError] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [notice, setNotice] = useState("");
  const [selectedProvider, setSelectedProvider] = useState<AiSystemProviderId>("openai");
  const [credential, setCredential] = useState("");
  const [dirty, setDirty] = useState(false);
  function closePanel() { if (!dirty || window.confirm("저장하지 않은 AI 설정 변경을 취소하고 닫을까요?")) onClose(); }
  const loadStatus = useCallback(async () => {
    try { setStatus(await apiFetch<AiSystemStatus>("/api/ai/system/status")); setError(""); }
    catch { setError("AI 설정을 불러오지 못했습니다. 잠시 후 다시 열어 주세요."); }
  }, []);
  useEffect(() => { const timer = window.setTimeout(() => void loadStatus(), 0); return () => window.clearTimeout(timer); }, [loadStatus]);
  const readOnly = reviewOnly || status?.settings_write_allowed === false;
  const selected = status?.providers.find((provider) => provider.provider === selectedProvider);
  async function connectExternalProvider() {
    if (readOnly) return;
    setConnecting(true); setNotice("");
    try {
      const result = await apiFetch<AiProviderConnectResult>("/api/ai/system/providers/" + selectedProvider + "/connect", {
        method: "POST", body: JSON.stringify({ api_key: credential.trim() || null, model: selected?.configured_model || null }),
      });
      setCredential("");
      setNotice(result.connected ? "연결과 합성자료 결과 형식을 확인했습니다. 이 환경을 사용하려면 아래에서 저장해 주세요." : "연결을 확인하지 못했습니다. API 키와 사용 한도를 확인해 주세요.");
      await loadStatus();
    } catch { setCredential(""); setNotice("외부 AI 연결을 확인하지 못했습니다. 현재 중앙 기본값은 유지됩니다."); }
    finally { setConnecting(false); }
  }
  return <div className="drawer-layer ai-settings-layer">
    <button className="drawer-backdrop" onClick={closePanel} aria-label="AI 설정 닫기" />
    <section className="security-panel ai-connection-panel ai-settings-simple desktop-resizable-dialog" aria-label="AI 설정">
      <header className="drawer-header"><h2>AI 설정</h2><button className="icon-button" onClick={closePanel} aria-label="닫기">×</button></header>
      {readOnly ? <p role="status">현재 계정에서는 확인만 할 수 있습니다. 설정 변경은 관리자에게 요청해 주세요.</p> : null}
      {error ? <p role="alert">{error}</p> : null}
      {!status && !error ? <p role="status">현재 AI를 확인하고 있습니다…</p> : null}
      {status ? <CentralModelsPanel readOnly={readOnly} status={status} externalProvider={selectedProvider} onChanged={() => void loadStatus()} onDirtyChange={setDirty} onExternalProviderChange={setSelectedProvider}
        externalControls={<>
          <fieldset disabled={readOnly || connecting}><legend>외부 AI 제공자</legend>
            {externalProviders.map((id) => { const provider = status.providers.find((item) => item.provider === id); return provider ? <label className="ai-environment-choice" key={id}><input type="radio" name="external-ai-provider" checked={selectedProvider === id} onChange={() => { setSelectedProvider(id); setCredential(""); setNotice(""); }} />{provider.display_name}</label> : null; })}
          </fieldset>
          <label>API 키<input type="password" autoComplete="new-password" spellCheck={false} disabled={readOnly || connecting} value={credential} onChange={(event) => setCredential(event.target.value)} placeholder={selected?.credential_configured ? "새 키로 바꿀 때만 입력" : "보호된 연결 키 입력"} /></label>
          <p>{selected?.credential_configured ? "저장된 API 키가 있습니다. 값은 표시하지 않습니다." : "API 키 미설정 · 실제 외부 호출 미확인"}</p>
          <button type="button" className="button button-secondary" disabled={readOnly || connecting || (!selected?.credential_configured && credential.trim().length < 8)} onClick={() => void connectExternalProvider()}>{connecting ? "연결 확인 중…" : "외부 연결 확인"}</button>
          {notice ? <p role="status">{notice}</p> : null}
          {selected?.models.length ? <p>최근 확인한 모델 {selected.models.length}개. 환경 저장 후 ‘모델 변경’에서 목록을 다시 확인할 수 있습니다.</p> : null}
        </>}
        diagnostics={<details className="ai-settings-diagnostics"><summary>연결 상세·기술지원 정보</summary>
          <h4>제공자 우선순위와 제한</h4><p>{status.provider_priority.join(" → ")} · 기존 진단/호환 설정</p><p>최대 시도 {status.max_attempts}회 · 일반 AI 전체 제한 {status.total_timeout_seconds}초. 실제 돌봄자료의 외부 자동 전환은 차단됩니다.</p>
          <h4>장비 정보</h4><p>{status.hardware.logical_cpu_count}코어 · RAM {status.hardware.system_ram_gb}GB · {status.hardware.nvidia_gpus.length ? "GPU 감지됨" : "현재 실행 환경에서 GPU 미확인"}</p>
          <p>컨테이너 정보만으로 기관 서버의 실제 장비나 성능을 확정하지 않습니다.</p>
          {status.providers.filter((provider) => provider.provider !== "rules").map((provider) => <details key={provider.provider}><summary>{provider.display_name}</summary>
            <p>연결: {provider.connection_state === "ready" ? "최근 확인됨" : "확인 필요"} · 구조화 출력: {provider.structured_contract_verified ? "확인됨" : "미검증"}</p>
            <p>최근 오류: {provider.last_error_code || "없음"} · 응답시간: {provider.last_latency_ms === null ? "미측정" : String(provider.last_latency_ms) + "ms"}</p>
            <p>모델 목록 {provider.models.length}개 · 자격증명 {provider.credential_configured ? "저장됨" : "미설정 또는 불필요"}</p>
          </details>)}
          <p>API 키, 인증 헤더, 서버 응답 원문은 표시하지 않습니다. 외부 API 연결 관리는 ‘다른 환경 연결 → 외부 AI API’에서 유지합니다.</p>
        </details>}
      /> : null}
    </section>
  </div>;
}
