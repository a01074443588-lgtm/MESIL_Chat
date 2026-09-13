"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "../api";
import type { Message } from "../types";
import { residentConfirmationPresentation } from "../residentConfirmation.mjs";
import { AttachmentDisplay } from "./AttachmentDisplay";
import { ResidentLinkReview } from "./ResidentLinkReview";

export function ResidentConfirmationInbox({ onChanged }: { onChanged?: () => void }) {
  const [data, setData] = useState<{ count: number; messages: Message[]; has_more: boolean } | null>(null);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  const requestRef = useRef<AbortController | null>(null);
  const load = useCallback(async () => {
    requestRef.current?.abort();
    const controller = new AbortController(); requestRef.current = controller;
    try {
      const next = await apiFetch<{ count: number; messages: Message[]; has_more: boolean }>("/api/workdesk/resident-confirmations", { signal: controller.signal });
      if (!controller.signal.aborted) { setData(next); setError(""); }
    }
    catch { if (!controller.signal.aborted) setError("확인할 어르신 목록을 불러오지 못했습니다."); }
  }, []);
  useEffect(() => {
    const initialLoad = window.setTimeout(() => void load(), 0);
    const refresh = () => void load();
    window.addEventListener("mesil-resident-links-changed", refresh);
    window.addEventListener("focus", refresh);
    return () => { window.clearTimeout(initialLoad); requestRef.current?.abort(); window.removeEventListener("mesil-resident-links-changed", refresh); window.removeEventListener("focus", refresh); };
  }, [load]);
  const presentation = residentConfirmationPresentation({ data, error: Boolean(error) });
  return <section className="resident-confirmation-inbox">
    {presentation.status === "ready" ? <button type="button" className="resident-choice-button needs-confirmation"
      aria-expanded={open} onClick={() => { setOpen(!open); void load(); }}>
      {presentation.label}
    </button> : null}
    {presentation.status === "error" ? <div className="resident-confirmation-error" role="alert">
      <span>{presentation.message}</span>
      <button type="button" onClick={() => void load()}>{presentation.retryLabel}</button>
    </div> : null}
    {open && presentation.status === "ready" ? <div className="resident-confirmation-list">
      <p className="resident-confirmation-description">{presentation.description}</p>
      {data?.messages.map(message => <article key={message.id} data-confirmation-message-id={message.id}>
        <strong>{message.sender_name}</strong><time>{new Date(message.created_at).toLocaleString("ko-KR")}</time>
        <p>{message.body.slice(0, 180)}</p>
        {message.attachments.filter(a => a.mime_type.startsWith("image/")).slice(0, 1).map(a =>
          <AttachmentDisplay key={a.id} attachment={a} compact accessScope="workdesk" />)}
        <ResidentLinkReview message={message} canEdit onChanged={() => { void load(); onChanged?.(); }} />
      </article>)}
      {data?.has_more ? <p>확인한 건이 정리되면 다음 사진이 이어서 표시됩니다.</p> : null}
    </div> : null}
  </section>;
}
