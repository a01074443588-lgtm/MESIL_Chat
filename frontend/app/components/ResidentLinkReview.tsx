"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ApiError, apiFetch } from "../api";
import type { Message } from "../types";

type Choice = {
  id: string;
  display_name: string;
  floor_name: string | null;
  room_name: string | null;
  internal_code: string;
  service_type: string;
};

function residentReviewErrorMessage(reason: unknown, action: "load" | "save" = "load"): string {
  if (reason instanceof ApiError) {
    if (reason.status === 401) return "로그인이 만료되었습니다. 다시 로그인해 주세요.";
    if (reason.status === 403) return "이 메시지의 어르신 연결을 관리할 권한이 없습니다.";
    if (reason.status === 404) return "이 메시지를 확인할 수 없습니다.";
    if (reason.status > 0 && reason.status < 500) return reason.message;
  }
  return action === "save"
    ? "어르신 연결을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요."
    : "어르신 목록을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.";
}

export function ResidentLinkReview({
  message,
  canEdit,
  onChanged,
  onManagerOpen,
  onManagerClose,
}: {
  message: Message;
  canEdit: boolean;
  onChanged: (message: Message) => void;
  onManagerOpen?: (messageId: string) => void;
  onManagerClose?: (messageId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [choices, setChoices] = useState<Choice[] | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const flight = useRef(false);
  const dialogRef = useRef<HTMLElement | null>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const viewportRef = useRef<{
    area: HTMLElement;
    messageId: string;
    scrollTop: number;
    anchorOffset: number | null;
  } | null>(null);

  const confirmed = useMemo(() => {
    const byId = new Map(
      message.resident_links
        .filter((link) => link.status === "confirmed")
        .map((link) => [link.resident.id, link.resident]),
    );
    if (message.resident && !byId.has(message.resident.id)) {
      byId.set(message.resident.id, message.resident);
    }
    return [...byId.values()];
  }, [message]);
  const candidateIds = useMemo(
    () => new Set(
      message.resident_links
        .filter((link) => link.status === "candidate")
        .map((link) => link.resident.id),
    ),
    [message.resident_links],
  );
  const initialIds = useMemo(() => confirmed.map((resident) => resident.id), [confirmed]);
  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);
  const filteredChoices = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase("ko");
    const rows = choices ?? [];
    return rows
      .filter((choice) => !normalized || [
        choice.display_name,
        choice.floor_name,
        choice.room_name,
        choice.internal_code,
      ].some((value) => value?.toLocaleLowerCase("ko").includes(normalized)))
      .sort((left, right) => Number(candidateIds.has(right.id)) - Number(candidateIds.has(left.id)));
  }, [candidateIds, choices, query]);
  const additions = selectedIds.filter((id) => !initialIds.includes(id)).length;
  const removals = initialIds.filter((id) => !selectedSet.has(id)).length;

  useEffect(() => {
    if (!open) return;
    dialogRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
  }, [open]);

  useLayoutEffect(() => {
    if (!open) return;
    const saved = viewportRef.current;
    if (!saved) return;
    const anchor = saved.area.querySelector<HTMLElement>(
      `[data-message-id="${saved.messageId}"]`,
    );
    if (anchor && saved.anchorOffset !== null) {
      const currentOffset =
        anchor.getBoundingClientRect().top - saved.area.getBoundingClientRect().top;
      saved.area.scrollTop = Math.max(
        0,
        saved.area.scrollTop + currentOffset - saved.anchorOffset,
      );
    } else {
      saved.area.scrollTop = saved.scrollTop;
    }
  }, [message, open]);

  function captureViewport(opener: HTMLElement) {
    const area = opener.closest<HTMLElement>(".message-area");
    const anchor = opener.closest<HTMLElement>("[data-message-id]");
    if (!area) return;
    viewportRef.current = {
      area,
      messageId: message.id,
      scrollTop: area.scrollTop,
      anchorOffset: anchor
        ? anchor.getBoundingClientRect().top - area.getBoundingClientRect().top
        : null,
    };
  }

  function restoreViewport() {
    const saved = viewportRef.current;
    if (!saved) return;
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        if (viewportRef.current !== saved) return;
        const anchor = saved.area.querySelector<HTMLElement>(
          `[data-message-id="${saved.messageId}"]`,
        );
        if (anchor && saved.anchorOffset !== null) {
          const currentOffset =
            anchor.getBoundingClientRect().top - saved.area.getBoundingClientRect().top;
          saved.area.scrollTop = Math.max(
            0,
            saved.area.scrollTop + currentOffset - saved.anchorOffset,
          );
        } else {
          saved.area.scrollTop = saved.scrollTop;
        }
        viewportRef.current = null;
      });
    });
  }

  async function showManager(opener: HTMLElement) {
    if (!canEdit || flight.current) return;
    openerRef.current = opener;
    captureViewport(opener);
    onManagerOpen?.(message.id);
    setOpen(true);
    setSelectedIds(initialIds);
    setChoices(null);
    setQuery("");
    setError("");
    flight.current = true;
    setBusy(true);
    try {
      setChoices(await apiFetch<Choice[]>(`/api/messages/${message.id}/resident-review/options`));
    } catch (reason) {
      setError(residentReviewErrorMessage(reason));
    } finally {
      flight.current = false;
      setBusy(false);
    }
  }

  function closeManager() {
    if (busy) return;
    setOpen(false);
    onManagerClose?.(message.id);
    restoreViewport();
    window.requestAnimationFrame(() => {
      openerRef.current?.focus({ preventScroll: true });
    });
  }

  function toggleResident(residentId: string) {
    setSelectedIds((current) => current.includes(residentId)
      ? current.filter((id) => id !== residentId)
      : [...current, residentId]);
  }

  async function save(decision: "set" | "unrelated") {
    if (flight.current || !canEdit) return;
    flight.current = true;
    setBusy(true);
    setError("");
    try {
      const updated = await apiFetch<Message>(`/api/messages/${message.id}/resident-review`, {
        method: "PATCH",
        body: JSON.stringify({
          decision,
          resident_ids: decision === "set" ? selectedIds : [],
        }),
      });
      onChanged(updated);
      setOpen(false);
      onManagerClose?.(message.id);
      restoreViewport();
      window.requestAnimationFrame(() => {
        openerRef.current?.focus({ preventScroll: true });
      });
      window.dispatchEvent(new CustomEvent("mesil-resident-links-changed", {
        detail: { messageId: message.id },
      }));
    } catch (reason) {
      setError(residentReviewErrorMessage(reason, "save"));
    } finally {
      flight.current = false;
      setBusy(false);
    }
  }

  return <>
    <div className="resident-link-actions" aria-label="연결된 어르신">
      {confirmed.map((resident) => (
        canEdit ? (
          <button
            type="button"
            key={resident.id}
            className="resident-chip resident-choice-button confirmed"
            aria-label={`${resident.display_name} 어르신 연결 관리 열기`}
            aria-haspopup="dialog"
            onClick={(event) => void showManager(event.currentTarget)}
          >
            {resident.display_name}
          </button>
        ) : (
          <span key={resident.id} className="resident-chip confirmed">
            {resident.display_name}
          </span>
        )
      ))}
      {canEdit && confirmed.length === 0 ? (
        <button
          type="button"
          className="resident-choice-button resident-add-button"
          aria-haspopup="dialog"
          onClick={(event) => void showManager(event.currentTarget)}
        >
          어르신 추가
        </button>
      ) : null}
    </div>
    {open ? createPortal(
      <div className="modal-backdrop resident-review-backdrop" onClick={closeManager}>
        <section
          ref={dialogRef}
          role="dialog"
          aria-modal="true"
          aria-labelledby={`resident-manager-title-${message.id}`}
          className="resident-review-dialog resident-link-manager"
          onClick={(event) => event.stopPropagation()}
          onKeyDown={(event) => {
            if (event.key === "Escape" && !busy) closeManager();
            if (event.key === "Tab") {
              const controls = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(
                "button:not(:disabled), input:not(:disabled)",
              ) ?? []);
              const first = controls[0], last = controls[controls.length - 1];
              if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
              if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
            }
          }}
        >
          <h2 id={`resident-manager-title-${message.id}`}>어르신 연결 관리</h2>
          <p>연결할 어르신을 모두 고른 뒤 한 번만 저장해 주세요.</p>
          {!canEdit ? (
            <p>작성자 또는 기록처리 담당자만 연결을 바꿀 수 있습니다.</p>
          ) : (
            <>
              <label className="resident-review-search">
                <span>이름 또는 생활공간 찾기</span>
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="어르신 이름 입력"
                  autoComplete="off"
                />
              </label>
              <div className="resident-review-options" aria-label="어르신 여러 명 선택">
                {filteredChoices.map((choice) => {
                  const selected = selectedSet.has(choice.id);
                  return (
                    <button
                      type="button"
                      key={choice.id}
                      className={selected ? "selected" : ""}
                      aria-pressed={selected}
                      disabled={busy}
                      onClick={() => toggleResident(choice.id)}
                    >
                      <strong>{selected ? "✓ " : ""}{choice.display_name}</strong>
                      <span>{[choice.floor_name, choice.room_name, choice.internal_code].filter(Boolean).join(" · ")}</span>
                      {candidateIds.has(choice.id) ? <small>확인이 필요한 후보</small> : null}
                    </button>
                  );
                })}
              </div>
              <p className="resident-review-change-summary" aria-live="polite">
                최종 연결 {selectedIds.length}명 · 추가 {additions}명 · 제외 {removals}명
              </p>
              <div className="resident-review-save-actions">
                <button type="button" disabled={busy || choices === null} onClick={() => void save("set")}>선택한 연결 저장</button>
                <button type="button" disabled={busy} onClick={() => void save("unrelated")}>어르신과 관련 없음</button>
              </div>
            </>
          )}
          {error ? <p role="alert" className="form-error">{error}</p> : null}
          <button type="button" className="resident-choice-button" disabled={busy} onClick={closeManager}>닫기</button>
        </section>
      </div>,
      document.body,
    ) : null}
  </>;
}
