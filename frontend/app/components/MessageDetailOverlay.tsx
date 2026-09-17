"use client";

import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "../api";
import { messageDisplayBody } from "../messagePresentation";
import type { Message, MessageDetail } from "../types";
import { AttachmentDisplay } from "./AttachmentDisplay";
import { ResidentLinkReview } from "./ResidentLinkReview";

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
  confirmation: "확인 요청",
};

const legacyActionStatusLabels: Record<string, string> = {
  assigned: "담당자 확인 전",
  acknowledged: "담당자 확인",
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
  canProcessRecords,
  onMessageChanged,
  onClose,
}: {
  messageId: string;
  refreshVersion: number;
  canProcessRecords: boolean;
  onMessageChanged: () => void;
  onClose: () => void;
}) {
  const detailRequestKey = `${messageId}:${refreshVersion}`;
  const [loadedDetail, setLoadedDetail] = useState<{
    requestKey: string;
    value: MessageDetail;
  } | null>(null);
  const detail =
    loadedDetail?.requestKey === detailRequestKey ? loadedDetail.value : null;
  const [readOpen, setReadOpen] = useState(false);
  const [imageBatchBusy, setImageBatchBusy] = useState(false);
  const [loadError, setLoadError] = useState("");

  const loadDetail = useCallback(
    () => apiFetch<MessageDetail>(`/api/messages/${messageId}`),
    [messageId],
  );

  useEffect(() => {
    let disposed = false;
    loadDetail()
      .then((payload) => {
        if (!disposed) {
          setLoadedDetail({ requestKey: detailRequestKey, value: payload });
        }
      })
      .catch((reason) => {
        if (!disposed) {
          setLoadedDetail((current) =>
            current?.requestKey === detailRequestKey ? null : current,
          );
          setLoadError(
            reason instanceof Error ? reason.message : "메시지를 열지 못했습니다.",
          );
        }
      });
    return () => {
      disposed = true;
    };
  }, [detailRequestKey, loadDetail, messageId]);

  useEffect(() => {
    const hasPendingExtraction = detail?.message.attachments.some((attachment) =>
      ["pending", "processing"].includes(attachment.text_extraction?.status ?? ""),
    );
    if (!hasPendingExtraction) return;
    const timer = window.setInterval(() => {
      void loadDetail()
        .then((payload) =>
          setLoadedDetail({ requestKey: detailRequestKey, value: payload }),
        )
        .catch(() => undefined);
    }, 3_000);
    return () => window.clearInterval(timer);
  }, [detail?.message.attachments, detailRequestKey, loadDetail]);

  const detailResidents = (() => {
    if (!detail) return [];
    const residents = new Map<
      string,
      {
        resident: Message["resident_links"][number]["resident"];
        status: "confirmed" | "candidate";
      }
    >();
    if (detail.message.resident) {
      residents.set(detail.message.resident.id, {
        resident: detail.message.resident,
        status: "confirmed",
      });
    }
    for (const link of detail.message.resident_links) {
      if (link.status === "rejected") continue;
      const current = residents.get(link.resident.id);
      if (current?.status === "confirmed") continue;
      residents.set(link.resident.id, {
        resident: link.resident,
        status: link.status,
      });
    }
    return [...residents.values()];
  })();
  const confirmedResidentCount = detailResidents.filter(
    (item) => item.status === "confirmed",
  ).length;
  const candidateResidentCount = detailResidents.filter(
    (item) => item.status === "candidate",
  ).length;
  const detailTitle = (() => {
    const confirmedResidents = detailResidents.filter(
      (item) => item.status === "confirmed",
    );
    if (confirmedResidents.length === 0 && candidateResidentCount > 1) {
      return `어르신 확인 후보 ${candidateResidentCount}명`;
    }
    if (confirmedResidents.length === 0 && candidateResidentCount === 1) {
      return "어르신 확인 후보";
    }
    if (detailResidents.length > 1) {
      return `관련 어르신 ${detailResidents.length}명`;
    }
    if (confirmedResidents.length === 1) {
      return confirmedResidents[0].resident.display_name;
    }
    return (
      messageNatureLabels[detail?.message.message_type ?? "chat"] ?? "업무대화"
    );
  })();
  const detailBody = detail
    ? messageDisplayBody(detail.message.body, detail.message.attachments)
    : "";
  const imageAttachments =
    detail?.message.attachments.filter((attachment) =>
      attachment.mime_type.startsWith("image/"),
    ) ?? [];
  const retryableImageAttachments = imageAttachments.filter((attachment) => {
    const status = attachment.text_extraction?.status;
    return !status || status === "failed";
  });
  const processingImageCount = imageAttachments.filter((attachment) =>
    ["pending", "processing"].includes(attachment.text_extraction?.status ?? ""),
  ).length;
  const completedImageCount = imageAttachments.filter((attachment) =>
    ["completed", "reviewed"].includes(attachment.text_extraction?.status ?? ""),
  ).length;
  const canUseImageActions = imageAttachments.some(
    (attachment) =>
      attachment.can_review_text === true ||
      attachment.can_request_reading === true,
  );
  const fileActionCount = Number(imageAttachments.length > 1 && canUseImageActions);

  function imageActionLabel() {
    if (imageBatchBusy) return "시작 중…";
    if (processingImageCount > 0) {
      return imageAttachments.length === 1
        ? "글자 읽는 중…"
        : `이미지 ${processingImageCount}장 판독 중…`;
    }
    if (retryableImageAttachments.length > 0) {
      const failedCount = retryableImageAttachments.filter(
        (attachment) => attachment.text_extraction?.status === "failed",
      ).length;
      if (failedCount === retryableImageAttachments.length && failedCount > 0) {
        return failedCount === 1
          ? "이미지 다시 읽기"
          : `실패 이미지 ${failedCount}장 다시 읽기`;
      }
      return imageAttachments.length === 1
        ? "이미지 글자 읽기"
        : `남은 이미지 ${retryableImageAttachments.length}장 글자 읽기`;
    }
    return "전체 이미지 판독 완료";
  }

  async function runImageBatchAction(force = false) {
    const firstImage = imageAttachments[0];
    if (!firstImage) return;
    if (!force && retryableImageAttachments.length === 0) {
      const root = document.querySelector<HTMLElement>(
        `[data-attachment-id="${firstImage.id}"]`,
      );
      root?.scrollIntoView({ behavior: "smooth", block: "start" });
      if (imageAttachments.length === 1) {
        root
          ?.querySelector<HTMLButtonElement>(
            '[data-attachment-action="edit-extraction"]',
          )
          ?.click();
      }
      return;
    }
    if (
      force &&
      !window.confirm(
        `이미지 ${imageAttachments.length}장을 모두 다시 판독할까요? 현재 판독문과 직원 수정본은 이전 판독 이력에 보존됩니다.`,
      )
    ) {
      return;
    }
    setImageBatchBusy(true);
    setLoadError("");
    try {
      await apiFetch(`/api/messages/${messageId}/image-text-extractions`, {
        method: "POST",
        body: JSON.stringify({ force }),
      });
      const payload = await loadDetail();
      setLoadedDetail({ requestKey: detailRequestKey, value: payload });
      onMessageChanged();
    } catch (reason) {
      setLoadError(
        reason instanceof Error
          ? reason.message
          : "이미지 글자 판독을 시작하지 못했습니다.",
      );
    } finally {
      setImageBatchBusy(false);
    }
  }

  return (
    <div className="detail-layer" role="dialog" aria-modal="true" aria-label="업무대화 상세">
      <button className="detail-backdrop" onClick={onClose} aria-label="상세 화면 닫기" />
      <section className="message-detail-card desktop-resizable-dialog">
        <header className="detail-header">
          <div>
            <h2>{detailTitle}</h2>
            {detailResidents.length > 1 ? (
              <small className="detail-title-status">
                확인됨 {confirmedResidentCount}명
                {candidateResidentCount
                  ? ` · 확인 후보 ${candidateResidentCount}명`
                  : ""}
              </small>
            ) : null}
          </div>
          <button className="icon-button" onClick={onClose} aria-label="닫기">
            ×
          </button>
        </header>
        {!detail ? (
          <div className="detail-loading">
            {loadError || "대화와 읽음 정보를 불러오고 있습니다…"}
          </div>
        ) : (
          <>
          <div className="detail-scroll">
            <section className="original-record">
              <div className="detail-meta">
                <strong>{detail.message.sender_name}</strong>
                <time>{formatDateTime(detail.message.created_at)}</time>
              </div>
              {detail.message.forwarded_from ? (
                <div className="forwarded-source">
                  전달받은 메시지 · {detail.message.forwarded_from.room_name} ·{" "}
                  {detail.message.forwarded_from.sender_name}
                </div>
              ) : null}
              <ResidentLinkReview message={detail.message} canEdit={canProcessRecords}
                onChanged={message => { setLoadedDetail({ requestKey: detailRequestKey, value: { ...detail, message } }); onMessageChanged(); }} />
              {detail.message.action_item ? (
                <div
                  className={`detail-action-summary priority-${detail.message.action_item.priority}`}
                  title="업무 상태는 메시지 읽음 표시와 별개입니다."
                >
                  <strong>업무 정보</strong>
                  <span>
                    유형 ·{" "}
                    {legacyActionLabels[detail.message.action_item.action_type] ??
                      detail.message.action_item.action_type}
                  </span>
                  <span>
                    담당 ·{" "}
                    {detail.message.action_item.assignee_user_name ??
                      detail.message.action_item.assignee_unit_name ??
                      "담당 미지정"}
                  </span>
                  <span>
                    상태 ·{" "}
                    {legacyActionStatusLabels[detail.message.action_item.status] ??
                      detail.message.action_item.status}
                  </span>
                  <small className="detail-action-help">
                    ‘담당자 확인 전’은 지정된 업무를 아직 확인하지 않은 상태이며,
                    메시지 읽음 수와는 다릅니다.
                  </small>
                </div>
              ) : null}
              {detailBody ? <p>{detailBody}</p> : null}
              {detail.message.attachments.length > 0 ? (
                <div className="detail-attachments">
                  {detail.message.attachments.map((attachment) => (
                    <AttachmentDisplay
                      key={`${attachment.id}:${attachment.text_extraction?.status ?? "none"}:${
                        attachment.text_extraction?.reviewed_at ?? ""
                      }`}
                      attachment={attachment}
                      galleryAttachments={detail.message.attachments}
                      showExtraction
                      canEditExtraction={attachment.can_review_text === true}
                      onAttachmentChanged={(nextAttachment) => {
                        setLoadedDetail((current) =>
                          current?.requestKey === detailRequestKey
                            ? {
                                ...current,
                                value: {
                                  ...current.value,
                                  message: {
                                    ...current.value.message,
                                    attachments: current.value.message.attachments.map(
                                      (currentAttachment) =>
                                        currentAttachment.id === nextAttachment.id
                                          ? nextAttachment
                                          : currentAttachment,
                                    ),
                                  },
                                },
                              }
                            : current,
                        );
                        void loadDetail()
                          .then((payload) =>
                            setLoadedDetail({
                              requestKey: detailRequestKey,
                              value: payload,
                            }),
                          )
                          .catch(() => undefined);
                        onMessageChanged();
                      }}
                    />
                  ))}
                </div>
              ) : null}
            </section>

            {readOpen ? <section className="read-section">
              <div className="section-heading">
                <h3>읽은 직원 {detail.read_receipts.length}명</h3>
                <button className="button button-secondary" onClick={() => setReadOpen(false)}>
                  닫기
                </button>
              </div>
                {detail.read_receipts.length > 0 ? (
                  <div className="reader-list">
                    {detail.read_receipts.map((receipt) => (
                      <span key={receipt.user_id}>
                        {receipt.user_name}
                        <small>{formatDateTime(receipt.read_at)}</small>
                      </span>
                    ))}
                  </div>
                ) : <p className="muted-box">아직 읽은 직원이 없습니다.</p>}
            </section> : null}
          </div>
          <footer className={`detail-action-footer${fileActionCount ? " has-file-actions" : ""}`}>
            <button
              type="button"
              className="button button-secondary desktop-secondary-action"
              onClick={() => setReadOpen((current) => !current)}
            >
              읽은 직원 {detail.read_receipts.length}명
            </button>
            {imageAttachments.length > 1 &&
            canUseImageActions &&
            retryableImageAttachments.length > 0 ? (
              <button
                type="button"
                className="button button-secondary"
                disabled={imageBatchBusy || processingImageCount > 0}
                onClick={() => void runImageBatchAction()}
              >
                {imageActionLabel()}
              </button>
            ) : null}
            {imageAttachments.length > 1 &&
            canUseImageActions &&
            completedImageCount > 0 &&
            processingImageCount === 0 ? (
              <button
                type="button"
                className="button button-secondary desktop-secondary-action"
                disabled={imageBatchBusy}
                onClick={() => void runImageBatchAction(true)}
              >
                전체 이미지 다시 판독
              </button>
            ) : null}
            {fileActionCount ? (
              <details className="mobile-action-more">
                <summary>더보기</summary>
                <div>
                  <button type="button" onClick={() => setReadOpen((current) => !current)}>
                    읽은 직원 {detail.read_receipts.length}명
                  </button>
                  {imageAttachments.length > 1 && completedImageCount > 0 ? (
                    <button
                      type="button"
                      disabled={imageBatchBusy || processingImageCount > 0}
                      onClick={() => void runImageBatchAction(true)}
                    >
                      전체 이미지 다시 판독
                    </button>
                  ) : null}
                </div>
              </details>
            ) : null}
          </footer>
          </>
        )}
      </section>
    </div>
  );
}
