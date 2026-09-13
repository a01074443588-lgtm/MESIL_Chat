"use client";

import { FormEvent, useState } from "react";

import { ApiError, apiFetch } from "../api";
import type { Message } from "../types";
import { AttachmentDisplay } from "./AttachmentDisplay";

type ConversationAccessStatus = {
  active: boolean;
  expires_at: string | null;
};

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("ko-KR", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function isConversationAccessDenied(reason: unknown) {
  return reason instanceof ApiError && [401, 403].includes(reason.status);
}

export function AdminConversationReview({
  roomId,
  roomName,
}: {
  roomId: string;
  roomName: string;
}) {
  const [open, setOpen] = useState(false);
  const [access, setAccess] = useState<ConversationAccessStatus | null>(null);
  const [password, setPassword] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function loadMessages() {
    setLoading(true);
    setError("");
    try {
      const next = await apiFetch<Message[]>(
        `/api/admin/conversations/rooms/${roomId}/messages?limit=200`,
      );
      setMessages(next);
    } catch (reason) {
      setMessages([]);
      if (isConversationAccessDenied(reason)) {
        setAccess({ active: false, expires_at: null });
        setError("열람 시간이 끝났습니다. 관리자 비밀번호를 다시 입력해 주세요.");
      } else {
        setError(reason instanceof Error ? reason.message : "업무대화를 불러오지 못했습니다.");
      }
    } finally {
      setLoading(false);
    }
  }

  async function openReview() {
    setOpen(true);
    setLoading(true);
    setError("");
    try {
      const status = await apiFetch<ConversationAccessStatus>(
        "/api/admin/conversation-access",
      );
      setAccess(status);
      if (status.active) await loadMessages();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "관리자 확인 상태를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }

  async function unlock(event: FormEvent) {
    event.preventDefault();
    if (!password || loading) return;
    setLoading(true);
    setError("");
    try {
      const status = await apiFetch<ConversationAccessStatus>(
        "/api/admin/conversation-access",
        {
          method: "POST",
          body: JSON.stringify({ password }),
        },
      );
      setPassword("");
      setAccess(status);
      await loadMessages();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "비밀번호를 확인하지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }

  async function lock() {
    setLoading(true);
    setError("");
    try {
      await apiFetch("/api/admin/conversation-access", { method: "DELETE" });
      setAccess({ active: false, expires_at: null });
      setMessages([]);
      setOpen(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "관리자 열람을 잠그지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="admin-conversation-review">
      <div>
        <strong>업무대화 열람</strong>
        <p>업무관리 목적으로만 열람하며, 열람 사실은 감사기록에 남습니다.</p>
      </div>
      {!open ? (
        <button type="button" className="button button-secondary" onClick={() => void openReview()}>
          대화 확인
        </button>
      ) : (
        <div className="admin-conversation-review-body">
          {!access?.active ? (
            <form onSubmit={unlock} className="admin-conversation-unlock">
              <label>
                <span>관리자 비밀번호를 한 번 더 입력하세요</span>
                <input
                  type="password"
                  value={password}
                  autoComplete="current-password"
                  onChange={(event) => setPassword(event.target.value)}
                />
              </label>
              <button className="button button-primary" disabled={loading || !password}>
                {loading ? "확인 중…" : "15분간 열람"}
              </button>
              <button type="button" className="button button-secondary" onClick={() => setOpen(false)}>
                취소
              </button>
            </form>
          ) : (
            <>
              <div className="admin-conversation-review-heading">
                <span>
                  <strong>{roomName}</strong>
                  <small>
                    {access.expires_at
                      ? `${formatDateTime(access.expires_at)}까지 열람 가능`
                      : "열람 권한 확인됨"}
                  </small>
                </span>
                <span>
                  <button type="button" className="button button-secondary" onClick={() => void loadMessages()}>
                    새로고침
                  </button>
                  <button type="button" className="button button-danger" onClick={() => void lock()}>
                    바로 잠그기
                  </button>
                </span>
              </div>
              <div className="admin-conversation-message-list">
                {loading ? <p>대화를 불러오는 중입니다…</p> : null}
                {!loading && messages.length === 0 ? <p>아직 대화가 없습니다.</p> : null}
                {messages.map((message) => (
                  <article key={message.id} className={message.is_recalled ? "recalled" : ""}>
                    <header>
                      <strong>{message.sender_name}</strong>
                      {message.is_recalled ? <b>회수됨 · 원본 보존</b> : null}
                      <time>{formatDateTime(message.created_at)}</time>
                    </header>
                    <p>{message.body}</p>
                    {message.attachments.length ? (
                      <div className="detail-attachments">
                        {message.attachments.map((attachment) => (
                          <AttachmentDisplay
                            key={attachment.id}
                            attachment={{
                              ...attachment,
                              download_url: `/api/admin/conversations/attachments/${attachment.id}`,
                            }}
                            galleryAttachments={message.attachments.map((item) => ({
                              ...item,
                              download_url: `/api/admin/conversations/attachments/${item.id}`,
                            }))}
                            compact
                          />
                        ))}
                      </div>
                    ) : null}
                  </article>
                ))}
              </div>
            </>
          )}
          {error ? <p className="form-error" role="alert">{error}</p> : null}
        </div>
      )}
    </section>
  );
}
