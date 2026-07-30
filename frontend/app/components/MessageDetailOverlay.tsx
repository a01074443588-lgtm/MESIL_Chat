"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { apiFetch } from "../api";
import type { Message, MessageComment, MessageDetail, Room } from "../types";
import { AttachmentDisplay } from "./AttachmentDisplay";

const messageNatureLabels: Record<string, string> = {
  chat: "일반 대화",
  notice: "공지",
  handover: "인수인계",
  work_request: "업무협조",
  report: "보고",
};

const legacyActionLabels: Record<string, string> = {
  handover: "인수인계",
  cooperation: "업무협조",
  confirmation: "확인요청",
};

const legacyActionStatusLabels: Record<string, string> = {
  assigned: "미확인",
  acknowledged: "확인",
  in_progress: "처리 중",
  completed: "완료",
};

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("ko-KR", {
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function MessageDetailOverlay({
  messageId,
  refreshVersion,
  rooms,
  currentUserId,
  canProcessRecords,
  onClose,
}: {
  messageId: string;
  refreshVersion: number;
  rooms: Room[];
  currentUserId: string;
  canProcessRecords: boolean;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<MessageDetail | null>(null);
  const [commentBody, setCommentBody] = useState("");
  const [saving, setSaving] = useState(false);
  const [forwardOpen, setForwardOpen] = useState(false);
  const [selectedRoomIds, setSelectedRoomIds] = useState<string[]>([]);
  const [forwarding, setForwarding] = useState(false);
  const [forwardFeedback, setForwardFeedback] = useState("");
  const [error, setError] = useState("");

  const loadDetail = useCallback(
    () => apiFetch<MessageDetail>(`/api/messages/${messageId}`),
    [messageId],
  );

  useEffect(() => {
    let disposed = false;
    loadDetail()
      .then((payload) => {
        if (!disposed) {
          setDetail(payload);
          void apiFetch(`/api/messages/${messageId}/comments/read`, {
            method: "POST",
            body: "{}",
          });
        }
      })
      .catch((reason) => {
        if (!disposed) {
          setError(reason instanceof Error ? reason.message : "메시지를 열지 못했습니다.");
        }
      });
    return () => {
      disposed = true;
    };
  }, [loadDetail, messageId, refreshVersion]);

  useEffect(() => {
    const hasPendingExtraction = detail?.message.attachments.some((attachment) =>
      ["pending", "processing"].includes(attachment.text_extraction?.status ?? ""),
    );
    if (!hasPendingExtraction) return;
    const timer = window.setInterval(() => {
      void loadDetail()
        .then((payload) => setDetail(payload))
        .catch(() => undefined);
    }, 3_000);
    return () => window.clearInterval(timer);
  }, [detail?.message.attachments, loadDetail]);

  const replyAuthorCount = useMemo(
    () => new Set(detail?.comments.map((comment) => comment.author_id) ?? []).size,
    [detail?.comments],
  );

  async function addComment(event: FormEvent) {
    event.preventDefault();
    if (!commentBody.trim() || !detail) return;
    setSaving(true);
    setError("");
    try {
      const comment = await apiFetch<MessageComment>(
        `/api/messages/${messageId}/comments`,
        {
          method: "POST",
          body: JSON.stringify({ body: commentBody.trim() }),
        },
      );
      setDetail({ ...detail, comments: [...detail.comments, comment] });
      setCommentBody("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "답글을 등록하지 못했습니다.");
    } finally {
      setSaving(false);
    }
  }

  async function forwardMessage(toAllJoinedRooms = false) {
    if (!detail || (!toAllJoinedRooms && selectedRoomIds.length === 0)) return;
    setForwarding(true);
    setError("");
    setForwardFeedback("");
    try {
      const forwarded = await apiFetch<Message[]>(
        `/api/messages/${messageId}/forward`,
        {
          method: "POST",
          body: JSON.stringify({
            room_ids: toAllJoinedRooms ? [] : selectedRoomIds,
            to_all_joined_rooms: toAllJoinedRooms,
          }),
        },
      );
      setForwardFeedback(`${forwarded.length}개 채팅방에 전달했습니다.`);
      setSelectedRoomIds([]);
      setForwardOpen(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "메시지를 전달하지 못했습니다.");
    } finally {
      setForwarding(false);
    }
  }

  const forwardRooms = detail
    ? rooms.filter((room) => room.id !== detail.message.room_id)
    : [];
  const detailTitle =
    detail?.message.resident?.display_name ??
    messageNatureLabels[detail?.message.message_type ?? "chat"] ??
    "업무대화";

  return (
    <div className="detail-layer" role="dialog" aria-modal="true" aria-label="업무대화 상세">
      <button className="detail-backdrop" onClick={onClose} aria-label="상세 화면 닫기" />
      <section className="message-detail-card">
        <header className="detail-header">
          <div>
            <span className="eyebrow">업무대화 상세</span>
            <h2>{detailTitle}</h2>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="닫기">
            ×
          </button>
        </header>
        {!detail ? (
          <div className="detail-loading">
            {error || "대화와 읽음 정보를 불러오고 있습니다…"}
          </div>
        ) : (
          <div className="detail-scroll">
            <section className="original-record">
              <div className="detail-meta">
                <strong>{detail.message.sender_name}</strong>
                <time>{formatDateTime(detail.message.created_at)}</time>
              </div>
              <div className="detail-engagement-summary">
                <strong>
                  {messageNatureLabels[detail.message.message_type] ?? "업무대화"}
                </strong>
                <span>읽은 직원 {detail.read_receipts.length}명</span>
                <span>
                  답글한 직원 {replyAuthorCount}명 · 답글 {detail.comments.length}개
                </span>
              </div>
              {detail.message.forwarded_from ? (
                <div className="forwarded-source">
                  전달받은 메시지 · {detail.message.forwarded_from.room_name} ·{" "}
                  {detail.message.forwarded_from.sender_name}
                </div>
              ) : null}
              {detail.message.resident ? (
                <span className="resident-chip">{detail.message.resident.display_name}</span>
              ) : null}
              {detail.message.resident_links
                .filter((link) => link.resident.id !== detail.message.resident?.id)
                .map((link) => (
                  <span
                    className={`resident-chip ${
                      link.status === "candidate" ? "candidate" : ""
                    }`}
                    key={link.resident.id}
                  >
                    {link.resident.display_name}
                    {link.status === "candidate" ? " · 확인 후보" : ""}
                  </span>
                ))}
              {detail.message.action_item ? (
                <div
                  className={`detail-action-summary priority-${detail.message.action_item.priority}`}
                >
                  <strong>
                    이전 방식 업무표시 ·{" "}
                    {legacyActionLabels[detail.message.action_item.action_type] ??
                      detail.message.action_item.action_type}
                  </strong>
                  <span>
                    담당{" "}
                    {detail.message.action_item.assignee_user_name ??
                      detail.message.action_item.assignee_unit_name}{" "}
                    ·{" "}
                    {legacyActionStatusLabels[detail.message.action_item.status] ??
                      detail.message.action_item.status}
                  </span>
                </div>
              ) : null}
              <p>{detail.message.body}</p>
              {detail.message.attachments.length > 0 ? (
                <div className="detail-attachments">
                  {detail.message.attachments.map((attachment) => (
                    <AttachmentDisplay
                      key={`${attachment.id}:${attachment.text_extraction?.status ?? "none"}:${
                        attachment.text_extraction?.reviewed_at ?? ""
                      }`}
                      attachment={attachment}
                      showExtraction
                      canEditExtraction={
                        canProcessRecords || detail.message.sender_id === currentUserId
                      }
                      onAttachmentChanged={(nextAttachment) =>
                        setDetail((current) =>
                          current
                            ? {
                                ...current,
                                message: {
                                  ...current.message,
                                  attachments: current.message.attachments.map(
                                    (currentAttachment) =>
                                      currentAttachment.id === nextAttachment.id
                                        ? nextAttachment
                                        : currentAttachment,
                                  ),
                                },
                              }
                            : current,
                        )
                      }
                    />
                  ))}
                </div>
              ) : null}
            </section>

            <section className="forward-section">
              <div className="section-heading">
                <div>
                  <h3>다른 방에 전달</h3>
                  <p>필요한 대화만 참여 중인 다른 채팅방으로 보냅니다.</p>
                </div>
                <button
                  className="button button-secondary"
                  onClick={() => setForwardOpen((current) => !current)}
                >
                  {forwardOpen ? "선택 닫기" : "전달할 방 선택"}
                </button>
              </div>
              {forwardFeedback ? <p className="form-success">{forwardFeedback}</p> : null}
              {forwardOpen ? (
                <div className="forward-room-picker">
                  {forwardRooms.length === 0 ? (
                    <p className="muted-box">전달할 수 있는 다른 채팅방이 없습니다.</p>
                  ) : (
                    <div className="forward-room-list">
                      {forwardRooms.map((room) => (
                        <label key={room.id}>
                          <input
                            type="checkbox"
                            checked={selectedRoomIds.includes(room.id)}
                            onChange={(event) =>
                              setSelectedRoomIds((current) =>
                                event.target.checked
                                  ? [...current, room.id]
                                  : current.filter((roomId) => roomId !== room.id),
                              )
                            }
                          />
                          <span>
                            <strong>{room.name}</strong>
                            <small>{room.kind === "self" ? "나만 보는 방" : "참여 중인 방"}</small>
                          </span>
                        </label>
                      ))}
                    </div>
                  )}
                  <div className="forward-actions">
                    <button
                      className="button button-primary"
                      disabled={forwarding || selectedRoomIds.length === 0}
                      onClick={() => void forwardMessage(false)}
                    >
                      선택한 방에 전달
                    </button>
                    <button
                      className="button button-secondary"
                      disabled={forwarding || forwardRooms.every((room) => room.kind === "self")}
                      onClick={() => void forwardMessage(true)}
                    >
                      모든 업무방에 전달
                    </button>
                  </div>
                </div>
              ) : null}
            </section>

            <section className="read-section">
              <div className="section-heading">
                <h3>읽은 직원</h3>
                <span>{detail.read_receipts.length}명</span>
              </div>
              <div className="reader-list">
                {detail.read_receipts.length === 0 ? (
                  <span>아직 읽은 직원이 없습니다.</span>
                ) : (
                  detail.read_receipts.map((receipt) => (
                    <span key={receipt.user_id}>
                      {receipt.user_name}
                      <small>{formatDateTime(receipt.read_at)}</small>
                    </span>
                  ))
                )}
              </div>
            </section>

            <section className="comment-section">
              <div className="section-heading">
                <h3>답글</h3>
                <span>
                  {replyAuthorCount}명 · {detail.comments.length}개
                </span>
              </div>
              {detail.comments.length === 0 ? (
                <p className="muted-box">아직 답글이 없습니다.</p>
              ) : (
                <div className="comment-list">
                  {detail.comments.map((comment) => (
                    <article key={comment.id}>
                      <div>
                        <strong>{comment.author_name}</strong>
                        <time>{formatDateTime(comment.created_at)}</time>
                      </div>
                      <p>{comment.body}</p>
                    </article>
                  ))}
                </div>
              )}
              <form className="comment-form" onSubmit={addComment}>
                <textarea
                  value={commentBody}
                  onChange={(event) => setCommentBody(event.target.value)}
                  rows={2}
                  maxLength={1000}
                  placeholder="확인 내용이나 후속조치를 답글로 남기세요."
                />
                <button
                  className="button button-primary"
                  disabled={saving || !commentBody.trim()}
                >
                  답글 등록
                </button>
              </form>
              {error ? <p className="form-error">{error}</p> : null}
            </section>
          </div>
        )}
      </section>
    </div>
  );
}
