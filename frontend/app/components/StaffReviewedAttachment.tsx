"use client";

import { useEffect, useRef, useState } from "react";
import type { MouseEvent } from "react";
import { createPortal } from "react-dom";
import { ApiError, apiFetch } from "../api";
import type { Attachment } from "../types";
import "../staffTextReview.css";

type Context = {
  attachment: Attachment;
  revision: number;
  resident_revision: number;
  selected_resident_ids: string[];
  choices: { id: string; display_name: string; room_name: string | null; floor_name: string | null; internal_code: string }[];
  revisions: { revision: number; kind: string; text: string; created_at: string }[];
};

function reviewErrorMessage(reason: unknown, fallback: string) {
  if (!(reason instanceof ApiError)) {
    return reason instanceof Error ? reason.message : fallback;
  }
  if (reason.status === 401) return "로그인 정보를 다시 확인해 주세요.";
  if (reason.status === 403) {
    return "이 문서의 내용을 확인·수정할 권한이 없습니다.";
  }
  if (reason.status === 404) return "현재 이 대화방에 접근할 수 없습니다.";
  return reason.message || fallback;
}

export function StaffReviewedAttachment({ attachment, canEdit, canRequestReading, canViewReviewedText, url, onChanged, onOpenOriginal, onSaveOriginal }: {
  attachment: Attachment; canEdit: boolean; canRequestReading: boolean; canViewReviewedText: boolean; url: string;
  onChanged: (value: Attachment) => void; onOpenOriginal?: (event: MouseEvent<HTMLButtonElement>) => void;
  onSaveOriginal?: () => Promise<void>;
}) {
  const audio = attachment.mime_type.startsWith("audio/");
  const extraction = attachment.text_extraction;
  const [open, setOpen] = useState(false);
  const [context, setContext] = useState<Context | null>(null);
  const [text, setText] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const flight = useRef(false);
  const dialog = useRef<HTMLElement | null>(null);
  const status = extraction?.status;
  const stateLabel = status === "pending" ? (audio ? "받아쓰기 대기 중" : "문서 읽기 대기 중")
    : status === "processing" ? (audio ? "받아쓰는 중…" : "문서 읽는 중…")
    : status === "reviewed" || extraction?.latest_confirmed_text ? "직원 확인 완료"
    : status === "completed" ? (audio ? "받아쓰기 완료 · 직원 확인 전" : "내용을 읽었습니다 · 직원 확인 전")
    : status === "no_text" ? "문서에서 글자를 찾지 못함"
    : status === "unsupported" ? "현재는 문서 내용 읽기를 지원하지 않습니다."
    : status === "failed" ? (audio ? "받아쓰기 실패" : "문서 읽기 실패") : "읽기 요청 전";

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.querySelector<HTMLButtonElement>("button")?.focus();
    return () => previous?.focus();
  }, [open]);

  async function showEditor() {
    if (flight.current || !canEdit) return;
    flight.current = true; setBusy(true); setError(""); setOpen(true); setContext(null);
    try {
      const next = await apiFetch<Context>(`/api/attachments/${attachment.id}/staff-review-context`);
      setContext(next); setSelected(next.selected_resident_ids);
      setText(next.attachment.text_extraction?.reviewed_text ?? next.attachment.text_extraction?.extracted_text ?? "");
      // Keep the editor mounted while loading full review metadata. The parent
      // keys attachments by reviewed_at and may intentionally remount on save.
    } catch (reason) { setError(reviewErrorMessage(reason, "내용을 열지 못했습니다.")); }
    finally { flight.current = false; setBusy(false); }
  }

  async function save() {
    if (flight.current || !context || !text.trim()) return;
    flight.current = true; setBusy(true); setError("");
    try {
      const next = await apiFetch<Attachment>(`/api/attachments/${attachment.id}/text-extraction`, {
        method: "PATCH", body: JSON.stringify({ decision: "direct_edit", reviewed_text: text.trim(),
          resident_ids: selected, expected_revision: context.revision,
          expected_resident_revision: context.resident_revision }),
      });
      onChanged(next); setOpen(false);
      window.dispatchEvent(new CustomEvent("mesil-resident-links-changed", { detail: { messageId: next.message_id } }));
    } catch (reason) { setError(reviewErrorMessage(reason, "저장하지 못했습니다.")); }
    finally { flight.current = false; setBusy(false); }
  }

  async function requestReading() {
    if (flight.current || !canRequestReading) return;
    flight.current = true; setBusy(true); setError("");
    try {
      onChanged(await apiFetch<Attachment>(`/api/attachments/${attachment.id}/text-extraction`, { method: "POST", body: "{}" }));
    } catch (reason) { setError(reviewErrorMessage(reason, "읽기를 시작하지 못했습니다.")); }
    finally { flight.current = false; setBusy(false); }
  }

  return <section className="staff-text-review" aria-label={audio ? "받아쓰기 직원 확인" : "문서 내용 확인"}>
    <p role="status">{stateLabel}</p>
    {status === "failed" ? <p className="form-error">{extraction?.error_message ?? "연결 상태를 확인한 뒤 다시 시도해 주세요."}</p> : null}
    {canEdit && (status === "completed" || status === "reviewed") ? <button type="button" disabled={busy} onClick={() => void showEditor()}>
      {audio ? "받아쓰기 확인·수정" : "문서 내용 확인·수정"}
    </button> : null}
    {canRequestReading && (!status || status === "failed" || status === "no_text") ? <button type="button" disabled={busy} onClick={() => void requestReading()}>{status ? "다시 읽기" : "문서 내용 읽기"}</button> : null}
    {!open && error ? <p className="form-error">{error}</p> : null}
    {canViewReviewedText && extraction?.latest_confirmed_text ? <p className="staff-text-final">{extraction.latest_confirmed_text}</p> : null}
    {open ? createPortal(<div className="staff-text-review-backdrop" onClick={() => { if (!busy) setOpen(false); }}>
      <section ref={dialog} className="staff-text-review-dialog" role="dialog" aria-modal="true" aria-label={audio ? "받아쓰기 확인·수정" : "문서 내용 확인·수정"}
        onClick={event => event.stopPropagation()} onKeyDown={event => {
          if (event.key === "Escape" && !busy) setOpen(false);
          if (event.key === "Tab") {
            const items = [...(dialog.current?.querySelectorAll<HTMLElement>("button:not(:disabled), input, textarea, a[href], audio") ?? [])];
            const first = items[0], last = items.at(-1);
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
          }
        }}>
        <header><h2>{audio ? "받아쓰기 확인·수정" : "문서 내용 확인·수정"}</h2><button type="button" disabled={busy} onClick={() => setOpen(false)}>닫기</button></header>
        {busy && !context ? <p>내용을 불러오고 있습니다.</p> : null}
        {context ? <>
          {audio ? <audio controls src={url} preload="metadata" /> : onOpenOriginal ? <button type="button" onClick={onOpenOriginal}>원본 보기</button> : onSaveOriginal ? <button type="button" onClick={() => void onSaveOriginal()}>원본 다운로드</button> : <a href={url} download={attachment.original_name}>원본 다운로드</a>}
          <div className="staff-text-columns"><details><summary>처음 읽은 내용과 비교</summary><pre>{context.attachment.text_extraction?.original_extracted_text ?? context.attachment.text_extraction?.extracted_text}</pre></details>
            <label>직원이 확인한 내용<textarea aria-label="직원이 확인한 내용" value={text} maxLength={100000} onChange={event => setText(event.target.value)} disabled={busy} /></label></div>
          <fieldset disabled={busy}><legend>관련 어르신 선택 · 여러 명 선택 가능</legend>
            <input aria-label="어르신 찾기" placeholder="이름 또는 생활공간으로 찾기" value={filter} onChange={event => setFilter(event.target.value)} />
            <p>관련된 분이 없으면 선택 없이 확인해 주세요.</p>
            <div className="staff-text-residents">{context.choices.filter(choice => !filter || [choice.display_name, choice.room_name, choice.floor_name, choice.internal_code].some(value => value?.includes(filter))).map(choice =>
              <label key={choice.id}><input type="checkbox" value={choice.id} checked={selected.includes(choice.id)} onChange={() => setSelected(current => current.includes(choice.id) ? current.filter(id => id !== choice.id) : [...current, choice.id])} />
                <span>{choice.display_name}<small>{[choice.floor_name, choice.room_name, choice.internal_code].filter(Boolean).join(" · ")}</small></span></label>)}</div>
          </fieldset>
          {context.revisions.length ? <details><summary>확인 이력 보기</summary>{context.revisions.map(revision => <details key={revision.revision}><summary>{revision.revision}차 · {revision.kind === "staff_review" ? "직원 확인" : "처음 읽은 내용"} · {new Date(revision.created_at).toLocaleString("ko-KR")}</summary><pre>{revision.text}</pre></details>)}</details> : null}
          <footer><button type="button" disabled={busy || !text.trim()} onClick={() => void save()}>{busy ? "저장 중…" : "확인 완료"}</button></footer>
        </> : null}
        {error ? <p className="form-error" role="alert">{error}</p> : null}
      </section>
    </div>, document.body) : null}
  </section>;
}
