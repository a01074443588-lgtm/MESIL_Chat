"use client";

import { useEffect, useRef, useState } from "react";
import type { TouchEvent } from "react";
import { createPortal } from "react-dom";

import { apiBase, apiFetch } from "../api";
import type { Attachment, AttachmentTextExtraction } from "../types";

function formatBytes(value: number) {
  if (value < 1024) return `${value}B`;
  if (value < 1024 * 1024) return `${Math.ceil(value / 1024)}KB`;
  return `${(value / (1024 * 1024)).toFixed(1)}MB`;
}

function kindLabel(attachment: Attachment) {
  if (attachment.mime_type.startsWith("image/")) return "이미지";
  if (attachment.mime_type.startsWith("audio/")) return "음성·음악";
  if (attachment.mime_type.startsWith("video/")) return "동영상";
  if (attachment.mime_type === "application/pdf") return "PDF";
  return "파일";
}

export function AttachmentDisplay({
  attachment,
  compact = false,
  accessScope = "room",
  showExtraction = false,
  canEditExtraction = false,
  onAttachmentChanged,
}: {
  attachment: Attachment;
  compact?: boolean;
  accessScope?: "room" | "workdesk";
  showExtraction?: boolean;
  canEditExtraction?: boolean;
  onAttachmentChanged?: (attachment: Attachment) => void;
}) {
  const downloadPath =
    accessScope === "workdesk"
      ? `/api/workdesk/attachments/${attachment.id}`
      : attachment.download_url;
  const url = `${apiBase()}${downloadPath}`;
  const isImage = attachment.mime_type.startsWith("image/");
  const isAudio = attachment.mime_type.startsWith("audio/");
  const isVideo = attachment.mime_type.startsWith("video/");
  const [imageOpen, setImageOpen] = useState(false);
  const [imageScale, setImageScale] = useState(1);
  const [imageOffset, setImageOffset] = useState({ x: 0, y: 0 });
  const extraction: AttachmentTextExtraction | null = attachment.text_extraction;
  const [editingExtraction, setEditingExtraction] = useState(false);
  const [extractionText, setExtractionText] = useState(
    attachment.text_extraction?.reviewed_text ??
      attachment.text_extraction?.extracted_text ??
      "",
  );
  const [savingExtraction, setSavingExtraction] = useState(false);
  const [extractionError, setExtractionError] = useState("");
  const pinchStartRef = useRef<{ distance: number; scale: number } | null>(null);
  const dragStartRef = useRef<{
    pointerId: number;
    x: number;
    y: number;
    offsetX: number;
    offsetY: number;
  } | null>(null);

  function applyImageScale(nextScale: number) {
    const normalizedScale = Math.min(4, Math.max(1, nextScale));
    setImageScale(normalizedScale);
    if (normalizedScale === 1) {
      setImageOffset({ x: 0, y: 0 });
    }
  }

  function changeImageScale(delta: number) {
    setImageScale((current) => {
      const nextScale = Math.min(4, Math.max(1, current + delta));
      if (nextScale === 1) {
        setImageOffset({ x: 0, y: 0 });
      }
      return nextScale;
    });
  }

  function resetImageView() {
    setImageScale(1);
    setImageOffset({ x: 0, y: 0 });
    pinchStartRef.current = null;
    dragStartRef.current = null;
  }

  useEffect(() => {
    if (!imageOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setImageOpen(false);
        resetImageView();
        return;
      }
      if (event.key === "+" || event.key === "=") {
        event.preventDefault();
        changeImageScale(0.25);
        return;
      }
      if (event.key === "-" || event.key === "_") {
        event.preventDefault();
        changeImageScale(-0.25);
        return;
      }
      if (event.key === "0") {
        event.preventDefault();
        resetImageView();
      }
    };
    document.addEventListener("keydown", closeOnEscape);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", closeOnEscape);
      document.body.style.overflow = previousOverflow;
    };
  }, [imageOpen]);

  function touchDistance(event: TouchEvent) {
    const first = event.touches.item(0);
    const second = event.touches.item(1);
    if (!first || !second) return 0;
    return Math.hypot(first.clientX - second.clientX, first.clientY - second.clientY);
  }

  function handlePinchStart(event: TouchEvent<HTMLImageElement>) {
    if (event.touches.length !== 2) return;
    pinchStartRef.current = {
      distance: touchDistance(event),
      scale: imageScale,
    };
  }

  function handlePinchMove(event: TouchEvent<HTMLImageElement>) {
    if (event.touches.length !== 2 || !pinchStartRef.current) return;
    event.preventDefault();
    const distance = touchDistance(event);
    if (!distance || !pinchStartRef.current.distance) return;
    const nextScale =
      pinchStartRef.current.scale * (distance / pinchStartRef.current.distance);
    applyImageScale(nextScale);
  }

  function closeImage() {
    setImageOpen(false);
    resetImageView();
  }

  const imageLightbox =
    imageOpen && typeof document !== "undefined"
      ? createPortal(
          <div
            className="image-lightbox"
            role="dialog"
            aria-modal="true"
            aria-label="첨부 이미지 크게 보기"
            onClick={closeImage}
          >
            <button
              type="button"
              className="image-lightbox-close"
              aria-label="이미지 닫기"
              onClick={closeImage}
            >
              ×
            </button>
            {/* 권한 검사를 통과한 로그인 사용자에게만 서버가 파일을 제공합니다. */}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              className="image-lightbox-image"
              src={url}
              alt={attachment.original_name}
              onClick={(event) => event.stopPropagation()}
              onDoubleClick={resetImageView}
              onWheel={(event) => {
                event.preventDefault();
                event.stopPropagation();
                changeImageScale(event.deltaY < 0 ? 0.25 : -0.25);
              }}
              onPointerDown={(event) => {
                if (event.pointerType !== "mouse" || imageScale <= 1) return;
                event.preventDefault();
                event.currentTarget.setPointerCapture(event.pointerId);
                dragStartRef.current = {
                  pointerId: event.pointerId,
                  x: event.clientX,
                  y: event.clientY,
                  offsetX: imageOffset.x,
                  offsetY: imageOffset.y,
                };
              }}
              onPointerMove={(event) => {
                const start = dragStartRef.current;
                if (!start || start.pointerId !== event.pointerId) return;
                setImageOffset({
                  x: start.offsetX + event.clientX - start.x,
                  y: start.offsetY + event.clientY - start.y,
                });
              }}
              onPointerUp={(event) => {
                if (dragStartRef.current?.pointerId === event.pointerId) {
                  dragStartRef.current = null;
                }
              }}
              onTouchStart={handlePinchStart}
              onTouchMove={handlePinchMove}
              onTouchEnd={() => {
                pinchStartRef.current = null;
              }}
              style={{
                transform: `translate3d(${imageOffset.x}px, ${imageOffset.y}px, 0) scale(${imageScale})`,
                cursor: imageScale > 1 ? "grab" : "default",
              }}
            />
            <div
              className="image-lightbox-controls"
              aria-label="이미지 확대·축소"
              onClick={(event) => event.stopPropagation()}
            >
              <button
                type="button"
                aria-label="축소"
                disabled={imageScale <= 1}
                onClick={() => changeImageScale(-0.25)}
              >
                −
              </button>
              <strong aria-live="polite">{Math.round(imageScale * 100)}%</strong>
              <button
                type="button"
                aria-label="확대"
                disabled={imageScale >= 4}
                onClick={() => changeImageScale(0.25)}
              >
                +
              </button>
              <button type="button" onClick={resetImageView}>
                원래 크기
              </button>
            </div>
            <span className="image-lightbox-help">
              두 손가락으로 확대·축소 · PC는 버튼이나 마우스 휠 · 확대 후 끌어서 이동
            </span>
          </div>,
          document.body,
        )
      : null;

  function imageButton(className: string) {
    return (
      <>
        <button
          type="button"
          className={className}
          aria-label={`${attachment.original_name} 크게 보기`}
          onClick={(event) => {
            event.stopPropagation();
            resetImageView();
            setImageOpen(true);
          }}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={url} alt={attachment.original_name} />
          <span>눌러서 크게 보기</span>
        </button>
        {imageLightbox}
      </>
    );
  }

  function updateAttachment(next: Attachment) {
    setExtractionText(
      next.text_extraction?.reviewed_text ??
        next.text_extraction?.extracted_text ??
        "",
    );
    onAttachmentChanged?.(next);
  }

  async function saveExtraction() {
    if (!extractionText.trim()) return;
    setSavingExtraction(true);
    setExtractionError("");
    try {
      const next = await apiFetch<Attachment>(
        `/api/attachments/${attachment.id}/text-extraction`,
        {
          method: "PATCH",
          body: JSON.stringify({
            decision: "direct_edit",
            reviewed_text: extractionText.trim(),
          }),
        },
      );
      updateAttachment(next);
      setEditingExtraction(false);
    } catch (reason) {
      setExtractionError(
        reason instanceof Error ? reason.message : "판독 내용을 저장하지 못했습니다.",
      );
    } finally {
      setSavingExtraction(false);
    }
  }

  async function retryExtraction() {
    setSavingExtraction(true);
    setExtractionError("");
    try {
      const next = await apiFetch<Attachment>(
        `/api/attachments/${attachment.id}/text-extraction`,
        { method: "POST", body: "{}" },
      );
      updateAttachment(next);
    } catch (reason) {
      setExtractionError(
        reason instanceof Error ? reason.message : "텍스트 변환을 다시 시작하지 못했습니다.",
      );
    } finally {
      setSavingExtraction(false);
    }
  }

  function extractionPanel() {
    if (!showExtraction || (!isImage && !isAudio) || !extraction) return null;
    const statusLabel =
      extraction.status === "pending"
        ? "변환 대기 중"
        : extraction.status === "processing"
          ? "텍스트로 변환 중…"
          : extraction.status === "reviewed"
            ? "확인된 텍스트"
            : extraction.status === "failed"
              ? "변환 실패"
              : "자동 변환 · 확인 전";
    const displayText =
      extraction.reviewed_text ?? extraction.extracted_text ?? extractionText;
    return (
      <section className={`attachment-extraction status-${extraction.status}`}>
        <div className="attachment-extraction-heading">
          <div>
            <strong>{isAudio ? "음성 받아쓰기" : "이미지 글자 판독"}</strong>
            <span>{statusLabel}</span>
          </div>
          {canEditExtraction &&
          !editingExtraction &&
          (extraction.status === "completed" || extraction.status === "reviewed") ? (
            <button
              type="button"
              className="button button-secondary"
              onClick={() => setEditingExtraction(true)}
            >
              수정
            </button>
          ) : null}
        </div>
        {editingExtraction ? (
          <>
            <textarea
              value={extractionText}
              onChange={(event) => setExtractionText(event.target.value)}
              aria-label={`${isAudio ? "음성 받아쓰기" : "이미지 글자 판독"} 수정`}
            />
            <div className="attachment-extraction-actions">
              <button
                type="button"
                className="button button-primary"
                disabled={savingExtraction || !extractionText.trim()}
                onClick={() => void saveExtraction()}
              >
                {savingExtraction ? "저장 중…" : "확인한 내용 저장"}
              </button>
              <button
                type="button"
                className="button button-secondary"
                disabled={savingExtraction}
                onClick={() => {
                  setExtractionText(displayText);
                  setEditingExtraction(false);
                  setExtractionError("");
                }}
              >
                취소
              </button>
            </div>
          </>
        ) : displayText ? (
          <pre>{displayText}</pre>
        ) : (
          <p>
            {extraction.status === "failed"
              ? extraction.error_message || "텍스트로 변환하지 못했습니다."
              : "변환이 끝나면 이곳에 텍스트가 표시됩니다."}
          </p>
        )}
        {canEditExtraction && extraction.status === "failed" ? (
          <button
            type="button"
            className="button button-secondary"
            disabled={savingExtraction}
            onClick={() => void retryExtraction()}
          >
            {savingExtraction ? "시작 중…" : "다시 변환"}
          </button>
        ) : null}
        {extractionError ? <p className="form-error">{extractionError}</p> : null}
      </section>
    );
  }

  if (compact) {
    if (isImage) {
      return imageButton("attachment-image-button compact");
    }
    return (
      <span className="compact-file">
        <strong>{kindLabel(attachment)}</strong>
        <span>{attachment.original_name}</span>
      </span>
    );
  }
  if (isImage) {
    return (
      <figure className="attachment-card image">
        {imageButton("attachment-image-button")}
        <figcaption>
          <span>{attachment.original_name}</span>
          <small>{formatBytes(attachment.size_bytes)}</small>
        </figcaption>
        {extractionPanel()}
      </figure>
    );
  }
  if (isAudio) {
    return (
      <figure className="attachment-card audio">
        <figcaption>
          <strong>음성·음악 파일</strong>
          <span>{attachment.original_name}</span>
          <small>{formatBytes(attachment.size_bytes)}</small>
        </figcaption>
        <audio controls preload="metadata" src={url}>
          이 브라우저에서는 음성 재생을 지원하지 않습니다.
        </audio>
        {extractionPanel()}
      </figure>
    );
  }
  if (isVideo) {
    return (
      <figure className="attachment-card video">
        <figcaption>
          <strong>동영상</strong>
          <span>{attachment.original_name}</span>
          <small>{formatBytes(attachment.size_bytes)}</small>
        </figcaption>
        <video controls playsInline preload="metadata" src={url}>
          이 브라우저에서는 동영상 재생을 지원하지 않습니다.
        </video>
      </figure>
    );
  }
  return (
    <a className="attachment-card download" href={url} target="_blank" rel="noreferrer">
      <strong>{kindLabel(attachment)} 열기</strong>
      <span>{attachment.original_name}</span>
      <small>{formatBytes(attachment.size_bytes)}</small>
    </a>
  );
}
