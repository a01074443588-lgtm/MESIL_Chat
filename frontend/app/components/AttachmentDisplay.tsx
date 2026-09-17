"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent, TouchEvent } from "react";
import { createPortal } from "react-dom";

import { apiBase, apiFetch } from "../api";
import { documentAttachmentInfo } from "../attachmentFormats";
import {
  hasActiveShareGesture,
  openAttachmentShare,
  prepareAttachmentShare,
  savePreparedAttachment,
} from "../attachmentShare";
import type { PreparedAttachmentShare } from "../attachmentShare";
import type {
  Attachment,
  AttachmentCoordinateReview,
  AttachmentTextExtraction,
  CoordinateRegion,
} from "../types";
import {
  EXTRACTION_EDITOR_HISTORY_STATE_KEY,
  IMAGE_HISTORY_STATE_KEY,
  PDF_HISTORY_STATE_KEY,
} from "../navigationHistory";
import { HandwritingVoiceCorrectionPanel } from "./HandwritingVoiceCorrectionPanel";
import { PdfCanvasViewer } from "./PdfCanvasViewer";
import { StaffReviewedAttachment } from "./StaffReviewedAttachment";

const NAME_REVIEW_TOKENS = ["이름확인필요", "이름 확인 필요"] as const;

type TextSelectionRange = {
  start: number;
  end: number;
};

function nameReviewTokenRanges(value: string): TextSelectionRange[] {
  const ranges: TextSelectionRange[] = [];
  for (const token of NAME_REVIEW_TOKENS) {
    let start = value.indexOf(token);
    while (start >= 0) {
      ranges.push({ start, end: start + token.length });
      start = value.indexOf(token, start + token.length);
    }
  }
  return ranges.sort((left, right) => left.start - right.start);
}

function formatBytes(value: number) {
  if (value < 1024) return `${value}B`;
  if (value < 1024 * 1024) return `${Math.ceil(value / 1024)}KB`;
  return `${(value / (1024 * 1024)).toFixed(1)}MB`;
}

function kindLabel(attachment: Attachment) {
  const documentInfo = documentAttachmentInfo(attachment.original_name);
  if (documentInfo) return documentInfo.label;
  if (attachment.mime_type.startsWith("image/")) return "이미지";
  if (attachment.mime_type.startsWith("audio/")) return "음성·음악";
  if (attachment.mime_type.startsWith("video/")) return "동영상";
  if (attachment.mime_type === "application/pdf") return "PDF";
  return "파일";
}

function formatReviewedAt(value: string) {
  return new Intl.DateTimeFormat("ko-KR", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function editableExtractionText(extraction: AttachmentTextExtraction | null) {
  if (extraction?.status === "failed" && extraction.preprocessing?.output_blocked) {
    return extraction.latest_confirmed_text ?? extraction.reviewed_text ?? "";
  }
  return (
    extraction?.latest_confirmed_text ??
    extraction?.reviewed_text ??
    extraction?.suggested_text ??
    extraction?.extracted_text ??
    extraction?.original_extracted_text ??
    ""
  );
}

function safeExtractionErrorMessage(message: string | null | undefined) {
  const normalized = message?.trim();
  if (!normalized) return "첨부 판독을 완료하지 못했습니다. 다시 시도해 주세요.";
  if (
    /https?:\/\/|(?:endpoint|decoder|codec|packet|traceback|exception|client\s+error|opus)/i.test(
      normalized,
    )
  ) {
    return "첨부 판독을 완료하지 못했습니다. 다시 판독하거나 관리자에게 알려 주세요.";
  }
  return normalized;
}

export function AttachmentDisplay({
  attachment,
  compact = false,
  showCompactShare = false,
  showCompactConfirmedText = true,
  accessScope = "room",
  showExtraction = false,
  showOriginalName = true,
  canEditExtraction = false,
  onAttachmentChanged,
  galleryAttachments,
}: {
  attachment: Attachment;
  compact?: boolean;
  showCompactShare?: boolean;
  showCompactConfirmedText?: boolean;
  accessScope?: "room" | "workdesk";
  showExtraction?: boolean;
  showOriginalName?: boolean;
  canEditExtraction?: boolean;
  onAttachmentChanged?: (attachment: Attachment) => void;
  galleryAttachments?: Attachment[];
}) {
  const attachmentUrl = useCallback(
    (item: Attachment) => {
      const downloadPath =
        accessScope === "workdesk"
          ? `/api/workdesk/attachments/${item.id}`
          : item.download_url;
      return `${apiBase()}${downloadPath}`;
    },
    [accessScope],
  );
  const url = attachmentUrl(attachment);
  const thumbnailUrl =
    compact && accessScope === "room" && attachment.thumbnail_url
      ? `${apiBase()}${attachment.thumbnail_url}`
      : url;
  const isImage = attachment.mime_type.startsWith("image/");
  const [photoChoiceBusy, setPhotoChoiceBusy] = useState(false);
  const isAudio = attachment.mime_type.startsWith("audio/");
  const isVideo = attachment.mime_type.startsWith("video/");
  const isPdf = attachment.mime_type === "application/pdf";
  const documentInfo = documentAttachmentInfo(attachment.original_name);
  const imageGallery = useMemo(() => {
    const source = galleryAttachments?.length ? galleryAttachments : [attachment];
    const images = source.filter((item) => item.mime_type.startsWith("image/"));
    if (!images.some((item) => item.id === attachment.id) && isImage) {
      return [attachment, ...images];
    }
    return images;
  }, [attachment, galleryAttachments, isImage]);
  const attachmentGalleryIndex = Math.max(
    0,
    imageGallery.findIndex((item) => item.id === attachment.id),
  );
  const [imageOpen, setImageOpen] = useState(false);
  const [loadedImageUrl, setLoadedImageUrl] = useState<string | null>(null);
  const [failedImageUrl, setFailedImageUrl] = useState<string | null>(null);
  const [pdfOpen, setPdfOpen] = useState(false);
  const [activeImageIndex, setActiveImageIndex] = useState(attachmentGalleryIndex);
  const [imageScale, setImageScale] = useState(1);
  const [imageOffset, setImageOffset] = useState({ x: 0, y: 0 });
  const [imageGestureActive, setImageGestureActive] = useState(false);
  const [reviewAttachment, setReviewAttachment] = useState<Attachment | null>(null);
  const [removedCorrectionAudioIds, setRemovedCorrectionAudioIds] = useState<string[]>([]);
  const editorAttachment =
    reviewAttachment?.id === attachment.id ? reviewAttachment : attachment;
  const editorAttachments = useMemo(() => {
    const source = galleryAttachments?.length ? galleryAttachments : [attachment];
    const next = source.filter((item) => !removedCorrectionAudioIds.includes(item.id)).map((item) =>
      item.id === editorAttachment.id ? editorAttachment : item,
    );
    return next.some((item) => item.id === editorAttachment.id)
      ? next
      : [editorAttachment, ...next];
  }, [attachment, editorAttachment, galleryAttachments, removedCorrectionAudioIds]);
  const extraction: AttachmentTextExtraction | null =
    reviewAttachment?.id === attachment.id
      ? reviewAttachment.text_extraction
      : attachment.text_extraction;
  const residentNameCandidates = useMemo(() => {
    const perRecognized = new Map<string, number>();
    const visibleCandidates = [];
    for (const candidate of extraction?.spelling_candidates ?? []) {
      if (candidate.content_type !== "resident_name") continue;
      const candidateGroup =
        candidate.source === "image_name_region" && candidate.slot_index
          ? `${candidate.source}:${candidate.slot_index}`
          : candidate.recognized;
      const count = perRecognized.get(candidateGroup) ?? 0;
      if (count >= 2) continue;
      perRecognized.set(candidateGroup, count + 1);
      visibleCandidates.push({ ...candidate, displayRank: count + 1 });
    }
    return visibleCandidates.slice(0, 40);
  }, [extraction?.spelling_candidates]);
  const nameRegionCandidates = useMemo(
    () =>
      residentNameCandidates.filter(
        (candidate) => candidate.source === "image_name_region",
      ),
    [residentNameCandidates],
  );
  const [editingExtraction, setEditingExtraction] = useState(false);
  const [loadingReviewDetails, setLoadingReviewDetails] = useState(false);
  const [extractionText, setExtractionText] = useState(
    editableExtractionText(attachment.text_extraction),
  );
  const nameReviewRanges = useMemo(
    () => nameReviewTokenRanges(extractionText),
    [extractionText],
  );
  const [savingExtraction, setSavingExtraction] = useState(false);
  const [extractionError, setExtractionError] = useState("");
  const [editorMediaScale, setEditorMediaScale] = useState(1);
  const [handwritingCoordinateReview, setHandwritingCoordinateReview] =
    useState<AttachmentCoordinateReview | null>(null);
  const [activeHandwritingRegion, setActiveHandwritingRegion] =
    useState<CoordinateRegion | null>(null);
  const [sharingFile, setSharingFile] = useState(false);
  const [savingOriginalFile, setSavingOriginalFile] = useState(false);
  const [shareFeedback, setShareFeedback] = useState("");
  const [preparedShare, setPreparedShare] = useState<{
    attachmentId: string;
    value: PreparedAttachmentShare;
  } | null>(null);
  const imageLightboxRef = useRef<HTMLDivElement | null>(null);
  const imageElementRef = useRef<HTMLImageElement | null>(null);
  const imageScaleRef = useRef(1);
  const imageOffsetRef = useRef({ x: 0, y: 0 });
  const pinchStartRef = useRef<{ distance: number; scale: number } | null>(null);
  const swipeStartRef = useRef<{ x: number; y: number } | null>(null);
  const touchPanStartRef = useRef<{
    identifier: number;
    x: number;
    y: number;
    offsetX: number;
    offsetY: number;
  } | null>(null);
  const activeImageIndexRef = useRef(attachmentGalleryIndex);
  const dragStartRef = useRef<{
    pointerId: number;
    x: number;
    y: number;
    offsetX: number;
    offsetY: number;
  } | null>(null);
  const editorAudioRef = useRef<HTMLAudioElement | null>(null);
  const extractionTextareaRef = useRef<HTMLTextAreaElement | null>(null);
  const handwritingImageScrollRef = useRef<HTMLDivElement | null>(null);

  const hasExtraction = Boolean(reviewAttachment?.text_extraction ?? attachment.text_extraction);
  useEffect(() => {
    if (!showExtraction || !isImage || !canEditExtraction || !hasExtraction) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    const loadPreview = async () => {
      try {
        const next = await apiFetch<Attachment>(
          `/api/attachments/${attachment.id}/text-extraction?preview=true`,
          { signal: controller.signal },
        );
        if (cancelled) return;
        setReviewAttachment(next);
        if (next.text_extraction && ["pending", "processing"].includes(next.text_extraction.status)) {
          timer = setTimeout(() => void loadPreview(), 1500);
        }
      } catch {
        if (!cancelled) {
          setReviewAttachment(null);
          setExtractionError("판독 정보를 불러오지 못했습니다. 접근 권한이나 연결을 확인해 주세요.");
        }
      }
    };
    void loadPreview();
    return () => { cancelled = true; controller.abort(); if (timer) clearTimeout(timer); };
  }, [attachment.id, attachment.text_extraction?.status, attachment.text_extraction?.attempt_number,
    attachment.text_extraction?.completed_at, reviewAttachment?.text_extraction?.status, showExtraction, isImage, canEditExtraction, hasExtraction]);

  const selectNameReviewRange = useCallback((range: TextSelectionRange) => {
    const textarea = extractionTextareaRef.current;
    if (!textarea) return;

    textarea.focus({ preventScroll: true });
    textarea.setSelectionRange(range.start, range.end);
    window.requestAnimationFrame(() => {
      textarea.setSelectionRange(range.start, range.end);
      const proportionalTop = textarea.value.length
        ? (range.start / textarea.value.length) * textarea.scrollHeight
        : 0;
      textarea.scrollTop = Math.max(
        0,
        proportionalTop - textarea.clientHeight / 2,
      );
      textarea.scrollIntoView({ block: "nearest" });
    });
  }, []);

  const selectNameReviewRangeAtCurrentSelection = useCallback(
    (textarea: HTMLTextAreaElement) => {
      const selectionStart = textarea.selectionStart;
      const selectionEnd = textarea.selectionEnd;
      const range = nameReviewRanges.find((candidate) => {
        if (selectionStart === selectionEnd) {
          return (
            selectionStart >= candidate.start && selectionStart < candidate.end
          );
        }
        return selectionStart < candidate.end && selectionEnd > candidate.start;
      });
      if (range) selectNameReviewRange(range);
    },
    [nameReviewRanges, selectNameReviewRange],
  );

  const clampImageOffset = useCallback(
    (nextOffset: { x: number; y: number }, scale: number) => {
      const imageElement = imageElementRef.current;
      const lightboxElement = imageLightboxRef.current;
      if (!imageElement || !lightboxElement || scale <= 1) {
        return { x: 0, y: 0 };
      }

      const maxX = Math.max(
        0,
        (imageElement.clientWidth * scale - lightboxElement.clientWidth) / 2,
      );
      const maxY = Math.max(
        0,
        (imageElement.clientHeight * scale - lightboxElement.clientHeight) / 2,
      );
      return {
        x: Math.min(maxX, Math.max(-maxX, nextOffset.x)),
        y: Math.min(maxY, Math.max(-maxY, nextOffset.y)),
      };
    },
    [],
  );

  const updateImageOffset = useCallback(
    (nextOffset: { x: number; y: number }, scale = imageScaleRef.current) => {
      const constrainedOffset = clampImageOffset(nextOffset, scale);
      imageOffsetRef.current = constrainedOffset;
      setImageOffset(constrainedOffset);
    },
    [clampImageOffset],
  );

  const applyImageScale = useCallback(
    (nextScale: number) => {
      const normalizedScale = Math.min(4, Math.max(1, nextScale));
      imageScaleRef.current = normalizedScale;
      setImageScale(normalizedScale);
      if (normalizedScale === 1) {
        updateImageOffset({ x: 0, y: 0 }, normalizedScale);
        touchPanStartRef.current = null;
        dragStartRef.current = null;
        return;
      }
      updateImageOffset(imageOffsetRef.current, normalizedScale);
    },
    [updateImageOffset],
  );

  const changeImageScale = useCallback(
    (delta: number) => {
      applyImageScale(imageScaleRef.current + delta);
    },
    [applyImageScale],
  );

  const resetImageView = useCallback(() => {
    imageScaleRef.current = 1;
    imageOffsetRef.current = { x: 0, y: 0 };
    setImageScale(1);
    setImageOffset({ x: 0, y: 0 });
    setImageGestureActive(false);
    pinchStartRef.current = null;
    swipeStartRef.current = null;
    touchPanStartRef.current = null;
    dragStartRef.current = null;
  }, []);

  const closeExtractionEditor = useCallback(() => {
    const shouldStepBack =
      window.history.state?.[EXTRACTION_EDITOR_HISTORY_STATE_KEY] ===
      attachment.id;
    if (shouldStepBack) {
      window.history.back();
      return;
    }
    setEditingExtraction(false);
  }, [attachment.id]);

  const openExtractionEditor = useCallback(
    (text: string) => {
      setExtractionText(text);
      setExtractionError("");
      setEditorMediaScale(1);
      setActiveHandwritingRegion(null);
      const currentState = window.history.state;
      const stateObject =
        currentState && typeof currentState === "object" ? currentState : {};
      if (
        stateObject[EXTRACTION_EDITOR_HISTORY_STATE_KEY] !== attachment.id
      ) {
        window.history.pushState(
          {
            ...stateObject,
            [EXTRACTION_EDITOR_HISTORY_STATE_KEY]: attachment.id,
          },
          "",
          window.location.href,
        );
      }
      setEditingExtraction(true);
    },
    [attachment.id],
  );

  async function loadAndOpenExtractionEditor() {
    if (loadingReviewDetails) return;
    setLoadingReviewDetails(true);
    setExtractionError("");
    try {
      const next = await apiFetch<Attachment>(
        `/api/attachments/${attachment.id}/text-extraction`,
      );
      const coordinateReview = isImage
        ? await apiFetch<AttachmentCoordinateReview>(
            `/api/attachments/${attachment.id}/coordinate-review`,
          ).catch(() => null)
        : null;
      setReviewAttachment(next);
      setHandwritingCoordinateReview(coordinateReview);
      setExtractionText(editableExtractionText(next.text_extraction));
      openExtractionEditor(editableExtractionText(next.text_extraction));
    } catch (reason) {
      setExtractionError(
        reason instanceof Error
          ? reason.message
          : "판독 정보를 불러오지 못했습니다.",
      );
    } finally {
      setLoadingReviewDetails(false);
    }
  }

  const focusHandwritingRegion = useCallback((region: CoordinateRegion) => {
    if (region.placement_status !== "confirmed") return;
    setActiveHandwritingRegion(region);
    window.requestAnimationFrame(() => {
      const container = handwritingImageScrollRef.current;
      if (!container) return;
      const centerX = (region.bbox.left + region.bbox.width / 2) * container.scrollWidth;
      const centerY = (region.bbox.top + region.bbox.height / 2) * container.scrollHeight;
      container.scrollTo({
        left: Math.max(0, centerX - container.clientWidth / 2),
        top: Math.max(0, centerY - container.clientHeight / 2),
        behavior: "smooth",
      });
    });
  }, []);

  const closeImage = useCallback(
    (event?: MouseEvent<HTMLElement>) => {
      event?.stopPropagation();
      if (window.history.state?.[IMAGE_HISTORY_STATE_KEY] === attachment.id) {
        window.history.back();
        return;
      }
      setImageOpen(false);
      resetImageView();
    },
    [attachment.id, resetImageView],
  );

  const selectGalleryImage = useCallback(
    (nextIndex: number) => {
      if (imageGallery.length < 1) return;
      const normalizedIndex = Math.min(
        imageGallery.length - 1,
        Math.max(0, nextIndex),
      );
      if (normalizedIndex === activeImageIndexRef.current) return;
      activeImageIndexRef.current = normalizedIndex;
      setActiveImageIndex(normalizedIndex);
      resetImageView();
    },
    [imageGallery.length, resetImageView],
  );

  const moveGalleryImage = useCallback(
    (direction: -1 | 1) => {
      selectGalleryImage(activeImageIndexRef.current + direction);
    },
    [selectGalleryImage],
  );

  useEffect(() => {
    if (!imageOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        closeImage();
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
        return;
      }
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        moveGalleryImage(-1);
        return;
      }
      if (event.key === "ArrowRight") {
        event.preventDefault();
        moveGalleryImage(1);
      }
    };
    document.addEventListener("keydown", closeOnEscape);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", closeOnEscape);
      document.body.style.overflow = previousOverflow;
    };
  }, [changeImageScale, closeImage, imageOpen, moveGalleryImage, resetImageView]);

  useEffect(() => {
    if (!isImage) return;
    const synchronizeImageWithHistory = () => {
      const shouldOpen =
        window.history.state?.[IMAGE_HISTORY_STATE_KEY] === attachment.id;
      setImageOpen(shouldOpen);
      if (!shouldOpen) resetImageView();
    };
    synchronizeImageWithHistory();
    window.addEventListener("popstate", synchronizeImageWithHistory);
    return () =>
      window.removeEventListener("popstate", synchronizeImageWithHistory);
  }, [attachment.id, isImage, resetImageView]);

  useEffect(() => {
    if (!imageOpen) return;
    const keepImageInsideViewport = () => {
      updateImageOffset(imageOffsetRef.current, imageScaleRef.current);
    };
    window.addEventListener("resize", keepImageInsideViewport);
    return () => window.removeEventListener("resize", keepImageInsideViewport);
  }, [imageOpen, updateImageOffset]);

  useEffect(() => {
    if (!isPdf) return;
    const synchronizePdfWithHistory = () => {
      const shouldOpen =
        window.history.state?.[PDF_HISTORY_STATE_KEY] === attachment.id;
      setPdfOpen(shouldOpen);
    };
    synchronizePdfWithHistory();
    window.addEventListener("popstate", synchronizePdfWithHistory);
    return () => window.removeEventListener("popstate", synchronizePdfWithHistory);
  }, [attachment.id, isPdf]);

  useEffect(() => {
    if (!pdfOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") closePdf();
    };
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", closeOnEscape);
    };
  });

  useEffect(() => {
    const synchronizeEditorWithHistory = () => {
      if (
        window.history.state?.[EXTRACTION_EDITOR_HISTORY_STATE_KEY] !==
        attachment.id
      ) {
        setEditingExtraction(false);
      }
    };
    window.addEventListener("popstate", synchronizeEditorWithHistory);
    return () =>
      window.removeEventListener("popstate", synchronizeEditorWithHistory);
  }, [attachment.id]);

  useEffect(() => {
    if (!editingExtraction) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeExtractionEditor();
    };
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [closeExtractionEditor, editingExtraction]);

  function touchDistance(event: TouchEvent) {
    const first = event.touches.item(0);
    const second = event.touches.item(1);
    if (!first || !second) return 0;
    return Math.hypot(first.clientX - second.clientX, first.clientY - second.clientY);
  }

  function handlePinchStart(event: TouchEvent<HTMLImageElement>) {
    if (event.touches.length === 2) {
      setImageGestureActive(true);
      swipeStartRef.current = null;
      touchPanStartRef.current = null;
      pinchStartRef.current = {
        distance: touchDistance(event),
        scale: imageScaleRef.current,
      };
      return;
    }
    if (event.touches.length === 1) {
      const touch = event.touches.item(0);
      if (!touch) return;
      if (imageScaleRef.current > 1) {
        setImageGestureActive(true);
        swipeStartRef.current = null;
        touchPanStartRef.current = {
          identifier: touch.identifier,
          x: touch.clientX,
          y: touch.clientY,
          offsetX: imageOffsetRef.current.x,
          offsetY: imageOffsetRef.current.y,
        };
      } else {
        touchPanStartRef.current = null;
        swipeStartRef.current = { x: touch.clientX, y: touch.clientY };
      }
    }
  }

  function handlePinchMove(event: TouchEvent<HTMLImageElement>) {
    if (event.touches.length === 2 && pinchStartRef.current) {
      const distance = touchDistance(event);
      if (!distance || !pinchStartRef.current.distance) return;
      const nextScale =
        pinchStartRef.current.scale * (distance / pinchStartRef.current.distance);
      applyImageScale(nextScale);
      return;
    }
    if (event.touches.length === 1 && imageScaleRef.current > 1) {
      const panStart = touchPanStartRef.current;
      if (!panStart) return;
      let touch = null;
      for (let index = 0; index < event.touches.length; index += 1) {
        const candidate = event.touches.item(index);
        if (candidate?.identifier === panStart.identifier) {
          touch = candidate;
          break;
        }
      }
      if (!touch) return;
      updateImageOffset({
        x: panStart.offsetX + touch.clientX - panStart.x,
        y: panStart.offsetY + touch.clientY - panStart.y,
      });
      return;
    }
  }

  function handleTouchEnd(event: TouchEvent<HTMLImageElement>) {
    if (event.touches.length < 2) {
      pinchStartRef.current = null;
    }
    if (imageScaleRef.current > 1) {
      swipeStartRef.current = null;
      const remainingTouch = event.touches.item(0);
      setImageGestureActive(Boolean(remainingTouch));
      touchPanStartRef.current = remainingTouch
        ? {
            identifier: remainingTouch.identifier,
            x: remainingTouch.clientX,
            y: remainingTouch.clientY,
            offsetX: imageOffsetRef.current.x,
            offsetY: imageOffsetRef.current.y,
          }
        : null;
      return;
    }
    setImageGestureActive(false);
    touchPanStartRef.current = null;
    const swipeStart = swipeStartRef.current;
    if (!swipeStart || event.touches.length > 0) return;
    swipeStartRef.current = null;
    const touch = event.changedTouches.item(0);
    if (!touch) return;
    const deltaX = touch.clientX - swipeStart.x;
    const deltaY = touch.clientY - swipeStart.y;
    if (Math.abs(deltaX) < 48 || Math.abs(deltaX) <= Math.abs(deltaY) * 1.15) {
      return;
    }
    moveGalleryImage(deltaX < 0 ? 1 : -1);
  }

  function handleImageBackdropClick(event: MouseEvent<HTMLDivElement>) {
    event.stopPropagation();
    if (event.target !== event.currentTarget) return;
    closeImage();
  }

  function openPdf(event: MouseEvent<HTMLButtonElement>) {
    event.stopPropagation();
    const currentState = window.history.state;
    const stateObject =
      currentState && typeof currentState === "object" ? currentState : {};
    if (stateObject[PDF_HISTORY_STATE_KEY] !== attachment.id) {
      window.history.pushState(
        { ...stateObject, [PDF_HISTORY_STATE_KEY]: attachment.id },
        "",
        window.location.href,
      );
    }
    setPdfOpen(true);
  }

  function closePdf() {
    if (window.history.state?.[PDF_HISTORY_STATE_KEY] === attachment.id) {
      window.history.back();
      return;
    }
    setPdfOpen(false);
  }

  const openImage = useCallback(
    (event: MouseEvent<HTMLButtonElement>) => {
      event.stopPropagation();
      resetImageView();
      activeImageIndexRef.current = attachmentGalleryIndex;
      setActiveImageIndex(attachmentGalleryIndex);
      const currentState = window.history.state;
      const stateObject =
        currentState && typeof currentState === "object" ? currentState : {};
      if (stateObject[IMAGE_HISTORY_STATE_KEY] !== attachment.id) {
        window.history.pushState(
          { ...stateObject, [IMAGE_HISTORY_STATE_KEY]: attachment.id },
          "",
          window.location.href,
        );
      }
      setImageOpen(true);
    },
    [attachment.id, attachmentGalleryIndex, resetImageView],
  );

  const activeImage = imageGallery[activeImageIndex] ?? attachment;
  const activeImageUrl = attachmentUrl(activeImage);
  const hasPreviousImage = activeImageIndex > 0;
  const hasNextImage = activeImageIndex < imageGallery.length - 1;

  const imageLightbox =
    imageOpen && typeof document !== "undefined"
      ? createPortal(
          <div
            ref={imageLightboxRef}
            className="image-lightbox"
            role="dialog"
            aria-modal="true"
            aria-label="첨부 이미지 크게 보기"
            onClick={handleImageBackdropClick}
          >
            <button
              type="button"
              className="image-lightbox-close"
              aria-label="이미지 닫기"
              onClick={closeImage}
            >
              ×
            </button>
            {imageGallery.length > 1 ? (
              <>
                <span className="image-lightbox-position" aria-live="polite">
                  {activeImageIndex + 1} / {imageGallery.length}
                </span>
                <button
                  type="button"
                  className="image-lightbox-nav previous"
                  aria-label="이전 사진"
                  disabled={!hasPreviousImage}
                  onClick={(event) => {
                    event.stopPropagation();
                    moveGalleryImage(-1);
                  }}
                >
                  ‹
                </button>
                <button
                  type="button"
                  className="image-lightbox-nav next"
                  aria-label="다음 사진"
                  disabled={!hasNextImage}
                  onClick={(event) => {
                    event.stopPropagation();
                    moveGalleryImage(1);
                  }}
                >
                  ›
                </button>
              </>
            ) : null}
            {/* 권한 검사를 통과한 로그인 사용자에게만 서버가 파일을 제공합니다. */}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              ref={imageElementRef}
              className={`image-lightbox-image${
                imageGestureActive ? " is-manipulating" : ""
              }`}
              src={activeImageUrl}
              alt={activeImage.original_name}
              draggable={false}
              onClick={(event) => event.stopPropagation()}
              onDoubleClick={resetImageView}
              onWheel={(event) => {
                event.preventDefault();
                event.stopPropagation();
                changeImageScale(event.deltaY < 0 ? 0.25 : -0.25);
              }}
              onPointerDown={(event) => {
                if (
                  event.pointerType !== "mouse" ||
                  imageScaleRef.current <= 1
                ) {
                  return;
                }
                event.preventDefault();
                event.currentTarget.setPointerCapture(event.pointerId);
                setImageGestureActive(true);
                dragStartRef.current = {
                  pointerId: event.pointerId,
                  x: event.clientX,
                  y: event.clientY,
                  offsetX: imageOffsetRef.current.x,
                  offsetY: imageOffsetRef.current.y,
                };
              }}
              onPointerMove={(event) => {
                const start = dragStartRef.current;
                if (!start || start.pointerId !== event.pointerId) return;
                updateImageOffset({
                  x: start.offsetX + event.clientX - start.x,
                  y: start.offsetY + event.clientY - start.y,
                });
              }}
              onPointerUp={(event) => {
                if (dragStartRef.current?.pointerId === event.pointerId) {
                  dragStartRef.current = null;
                  setImageGestureActive(false);
                }
              }}
              onPointerCancel={(event) => {
                if (dragStartRef.current?.pointerId === event.pointerId) {
                  dragStartRef.current = null;
                  setImageGestureActive(false);
                }
              }}
              onTouchStart={handlePinchStart}
              onTouchMove={handlePinchMove}
              onTouchEnd={handleTouchEnd}
              onTouchCancel={() => {
                pinchStartRef.current = null;
                swipeStartRef.current = null;
                touchPanStartRef.current = null;
                setImageGestureActive(false);
              }}
              onLoad={() => {
                updateImageOffset(imageOffsetRef.current, imageScaleRef.current);
              }}
              style={{
                transform: `translate3d(${imageOffset.x}px, ${imageOffset.y}px, 0) scale(${imageScale})`,
                cursor: imageGestureActive
                  ? "grabbing"
                  : imageScale > 1
                    ? "grab"
                    : "default",
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
              <button
                type="button"
                aria-label="원래 크기로 되돌리기"
                onClick={resetImageView}
              >
                원본
              </button>
              <button
                type="button"
                disabled={sharingFile}
                onClick={() => void shareFile(activeImage)}
              >
                {sharingFile
                  ? "준비 중"
                  : preparedShare?.attachmentId === activeImage.id
                    ? preparedShare.value.nativeShareSupported
                      ? "공유"
                      : "저장"
                    : "공유"}
              </button>
            </div>
            {shareFeedback ? (
              <span className="image-lightbox-share-feedback" role="status">
                {shareFeedback}
              </span>
            ) : null}
            <span className="image-lightbox-help image-lightbox-help-desktop">
              {imageGallery.length > 1 ? "좌우로 밀어 다음 사진 · " : ""}
              마우스 휠·버튼으로 확대·축소 · 확대 후 끌어서 이동
            </span>
            <span className="image-lightbox-help image-lightbox-help-mobile">
              {imageGallery.length > 1 ? "좌우로 밀어 다음 사진 · " : ""}
              두 손가락으로 확대·축소 · 확대 후 끌어서 이동
            </span>
          </div>,
          document.body,
        )
      : null;

  const pdfViewer =
    pdfOpen && typeof document !== "undefined"
      ? createPortal(
          <section
            className="pdf-viewer"
            role="dialog"
            aria-modal="true"
            aria-label={`${attachment.original_name} PDF 보기`}
          >
            <header className="pdf-viewer-header">
              <button
                type="button"
                className="pdf-viewer-close"
                aria-label="PDF 닫기"
                onClick={closePdf}
              >
                ← 닫기
              </button>
              <div className="pdf-viewer-title">
                <strong>PDF 문서</strong>
                <span>{attachment.original_name}</span>
              </div>
              <a href={url} target="_blank" rel="noreferrer">
                다른 앱으로 열기
              </a>
            </header>
            <div className="pdf-viewer-content">
              <PdfCanvasViewer
                key={attachment.id}
                url={url}
                title={attachment.original_name}
              />
            </div>
          </section>,
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
          onClick={openImage}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={thumbnailUrl}
            alt={attachment.original_name}
            loading="lazy"
            decoding="async"
            onLoad={() => {
              setLoadedImageUrl(thumbnailUrl);
              setFailedImageUrl(null);
            }}
            onError={() => setFailedImageUrl(thumbnailUrl)}
          />
          {loadedImageUrl !== thumbnailUrl ? (
            <span className="attachment-image-load-status" role="status">
              {failedImageUrl === thumbnailUrl
                ? "사진을 불러오지 못했습니다"
                : "사진 불러오는 중…"}
            </span>
          ) : null}
        </button>
        {imageLightbox}
      </>
    );
  }

  function updateAttachment(next: Attachment) {
    setReviewAttachment(next);
    setExtractionText(editableExtractionText(next.text_extraction));
    onAttachmentChanged?.(next);
  }

  async function shareFile(item: Attachment = attachment) {
    if (sharingFile || savingOriginalFile) return;
    setSharingFile(true);
    setShareFeedback("");
    try {
      const existingPrepared =
        preparedShare?.attachmentId === item.id ? preparedShare.value : null;
      let nextPrepared = existingPrepared;
      if (!nextPrepared) {
        nextPrepared = await prepareAttachmentShare({
          url: attachmentUrl(item),
          name: item.original_name,
          mimeType: item.mime_type,
        });
        setPreparedShare({ attachmentId: item.id, value: nextPrepared });
      }

      if (!nextPrepared.nativeShareSupported) {
        if (!existingPrepared) {
          setShareFeedback(
            "이 기기는 다른 앱 공유를 지원하지 않습니다. 저장하려면 ‘파일 저장’을 눌러 주세요.",
          );
          return;
        }
        await savePreparedAttachment(nextPrepared);
        setPreparedShare(null);
        setShareFeedback("파일로 저장했습니다.");
        return;
      }
      if (!hasActiveShareGesture()) {
        setShareFeedback("파일 준비가 끝났습니다. ‘공유창 열기’를 한 번 더 눌러 주세요.");
        return;
      }

      const result = await openAttachmentShare(nextPrepared);
      if (result === "shared") {
        setPreparedShare(null);
        setShareFeedback("공유창을 열었습니다.");
      } else {
        setShareFeedback("");
      }
    } catch (reason) {
      setShareFeedback(
        reason instanceof Error ? reason.message : "파일을 공유하지 못했습니다.",
      );
    } finally {
      setSharingFile(false);
    }
  }

  async function saveOriginalFile(item: Attachment = attachment) {
    if (sharingFile || savingOriginalFile) return;
    setSavingOriginalFile(true);
    setShareFeedback("");
    try {
      const ready =
        preparedShare?.attachmentId === item.id
          ? preparedShare.value
          : await prepareAttachmentShare({
              url: attachmentUrl(item),
              name: item.original_name,
              mimeType: item.mime_type,
            });
      await savePreparedAttachment(ready);
      setPreparedShare(null);
      setShareFeedback("원본 파일을 저장했습니다.");
    } catch (reason) {
      setShareFeedback(
        reason instanceof Error ? reason.message : "원본 파일을 저장하지 못했습니다.",
      );
    } finally {
      setSavingOriginalFile(false);
    }
  }

  function shareControl(item: Attachment = attachment) {
    const ready = preparedShare?.attachmentId === item.id ? preparedShare.value : null;
    const buttonLabel = sharingFile
      ? "공유 준비 중…"
      : ready
        ? ready.nativeShareSupported
          ? "공유창 열기"
          : "파일 저장"
        : "다른 앱으로 공유";
    return (
      <div className="attachment-share-row">
        <button
          type="button"
          className="attachment-share-button attachment-save-button"
          disabled={sharingFile || savingOriginalFile}
          aria-label={`${item.original_name} 원본 저장`}
          onClick={() => void saveOriginalFile(item)}
        >
          {savingOriginalFile ? "저장 중…" : "원본 저장"}
        </button>
        <button
          type="button"
          className="attachment-share-button"
          disabled={sharingFile || savingOriginalFile}
          aria-label={`${item.original_name} ${buttonLabel}`}
          onClick={() => void shareFile(item)}
        >
          {buttonLabel}
        </button>
        {shareFeedback ? (
          <span className="attachment-share-feedback" role="status">
            {shareFeedback}
          </span>
        ) : null}
      </div>
    );
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
      window.dispatchEvent(new CustomEvent("mesil-resident-links-changed", {
        detail: { messageId: next.message_id, revision: next.resident_link_revision ?? 0 },
      }));
      closeExtractionEditor();
    } catch (reason) {
      setExtractionError(
        reason instanceof Error ? reason.message : "판독 내용을 저장하지 못했습니다.",
      );
    } finally {
      setSavingExtraction(false);
    }
  }

  async function retryExtraction() {
    if (
      extraction?.status !== "failed" &&
      !window.confirm(
        "이 이미지를 다시 판독합니다. 현재 판독문과 직원이 저장한 수정본은 이전 판독 이력에 보존됩니다.",
      )
    ) {
      return;
    }
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

  function moveEditorAudio(seconds: number) {
    const audio = editorAudioRef.current;
    if (!audio) return;
    audio.currentTime = Math.max(
      0,
      Math.min(Number.isFinite(audio.duration) ? audio.duration : Infinity, audio.currentTime + seconds),
    );
  }

  function moveToNextNameReviewToken() {
    if (!nameReviewRanges.length) return;
    const selectionEnd = extractionTextareaRef.current?.selectionEnd ?? 0;
    const nextRange =
      nameReviewRanges.find((range) => range.start >= selectionEnd) ??
      nameReviewRanges[0];
    selectNameReviewRange(nextRange);
  }

  function handleNameReviewDoubleClick(
    event: MouseEvent<HTMLTextAreaElement>,
  ) {
    selectNameReviewRangeAtCurrentSelection(event.currentTarget);
  }

  function extractionEditor() {
    if (!editingExtraction || typeof document === "undefined") return null;
    if (isImage) {
      return createPortal(
        <section
          className="handwriting-correction-dialog"
          role="dialog"
          aria-modal="true"
          aria-label="손글씨 수정"
        >
          <header className="extraction-editor-header">
            <div>
              <strong>손글씨 수정</strong>
              <span>이미지를 보며 최종 문장만 확인·수정합니다.</span>
            </div>
            <button type="button" aria-label="손글씨 수정 닫기" onClick={closeExtractionEditor}>
              ×
            </button>
          </header>
          <div className="handwriting-correction-dialog-main">
            <section className="handwriting-correction-image" aria-label="원본 손글씨 이미지">
              <header>
                <strong>원본 손글씨</strong>
                <div className="extraction-editor-zoom" aria-label="원본 이미지 확대 축소">
                  <button
                    type="button"
                    disabled={editorMediaScale <= 1}
                    onClick={() => setEditorMediaScale((current) => Math.max(1, current - 0.25))}
                  >
                    −
                  </button>
                  <span>{Math.round(editorMediaScale * 100)}%</span>
                  <button
                    type="button"
                    disabled={editorMediaScale >= 4}
                    onClick={() => setEditorMediaScale((current) => Math.min(4, current + 0.25))}
                  >
                    +
                  </button>
                </div>
              </header>
              <div ref={handwritingImageScrollRef} className="handwriting-correction-image-scroll">
                <div
                  className="handwriting-correction-image-stage"
                  style={{ width: `${editorMediaScale * 100}%` }}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={url} alt={attachment.original_name} />
                  {activeHandwritingRegion?.placement_status === "confirmed" ? (
                    <span
                      className="handwriting-confirmed-region"
                      aria-label="확인된 원본 위치"
                      style={{
                        left: `${activeHandwritingRegion.bbox.left * 100}%`,
                        top: `${activeHandwritingRegion.bbox.top * 100}%`,
                        width: `${activeHandwritingRegion.bbox.width * 100}%`,
                        height: `${activeHandwritingRegion.bbox.height * 100}%`,
                        transform: `rotate(${activeHandwritingRegion.bbox.rotation_degrees ?? 0}deg)`,
                      }}
                    />
                  ) : null}
                </div>
              </div>
              <small>
                {activeHandwritingRegion
                  ? "직원이 확인한 글줄 위치를 강조했습니다."
                  : "확인된 위치가 있는 문장을 누르면 원본에서 강조합니다."}
              </small>
            </section>
            <HandwritingVoiceCorrectionPanel
              attachments={editorAttachments}
              canUse={canEditExtraction}
              initialImageId={attachment.id}
              onRemovedRecording={(id) => setRemovedCorrectionAudioIds((current) => [...current, id])}
              coordinateReview={handwritingCoordinateReview}
              onFocusRegion={focusHandwritingRegion}
            />
          </div>
        </section>,
        document.body,
      );
    }
    const editorTitle = isAudio ? "음성 받아쓰기 수정" : "이미지 판독문 수정";
    return createPortal(
      <section
        className={`extraction-editor${isAudio ? " audio" : " image"}`}
        role="dialog"
        aria-modal="true"
        aria-label={editorTitle}
      >
        <header className="extraction-editor-header">
          <div>
            <strong>{editorTitle}</strong>
            <span>{attachment.original_name}</span>
          </div>
          <button
            type="button"
            aria-label="판독문 수정 닫기"
            onClick={closeExtractionEditor}
          >
            ×
          </button>
        </header>

        <div className="extraction-editor-main">
          <section className="extraction-editor-source" aria-label="원본 파일">
            <header>
              <strong>{isAudio ? "음성 들으며 확인" : "이미지 보며 확인"}</strong>
              {isImage ? (
                <div className="extraction-editor-zoom" aria-label="이미지 확대 축소">
                  <button
                    type="button"
                    disabled={editorMediaScale <= 1}
                    onClick={() =>
                      setEditorMediaScale((current) => Math.max(1, current - 0.25))
                    }
                  >
                    −
                  </button>
                  <span>{Math.round(editorMediaScale * 100)}%</span>
                  <button
                    type="button"
                    disabled={editorMediaScale >= 4}
                    onClick={() =>
                      setEditorMediaScale((current) => Math.min(4, current + 0.25))
                    }
                  >
                    +
                  </button>
                  <button type="button" onClick={() => setEditorMediaScale(1)}>
                    원본
                  </button>
                </div>
              ) : null}
            </header>
            <div className="extraction-editor-media-stage">
              {isImage ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={url}
                  alt={attachment.original_name}
                  style={{ width: `${editorMediaScale * 100}%` }}
                />
              ) : (
                <div className="extraction-editor-audio-player">
                  <audio ref={editorAudioRef} controls preload="metadata" src={url}>
                    이 브라우저에서는 음성 재생을 지원하지 않습니다.
                  </audio>
                  <div>
                    <button type="button" onClick={() => moveEditorAudio(-5)}>
                      5초 전
                    </button>
                    <button type="button" onClick={() => moveEditorAudio(5)}>
                      5초 후
                    </button>
                  </div>
                </div>
              )}
            </div>
          </section>

          <section className="extraction-editor-text">
            <div className="extraction-editor-text-header">
              <span>확인한 내용</span>
              {nameReviewRanges.length ? (
                <button
                  type="button"
                  onClick={moveToNextNameReviewToken}
                  aria-label={`이름확인필요 ${nameReviewRanges.length}건 중 다음 항목 선택`}
                >
                  다음 이름확인필요 ({nameReviewRanges.length})
                </button>
              ) : null}
            </div>
            <textarea
              ref={extractionTextareaRef}
              value={extractionText}
              onChange={(event) => setExtractionText(event.target.value)}
              onDoubleClick={handleNameReviewDoubleClick}
              spellCheck={false}
              aria-label={`${editorTitle} 내용`}
            />
          </section>
        </div>

        <footer className="extraction-editor-footer">
          {extractionError ? (
            <p className="form-error" role="alert">{extractionError}</p>
          ) : <span />}
          <div>
            <button
              type="button"
              className="button button-secondary"
              disabled={savingExtraction}
              onClick={closeExtractionEditor}
            >
              취소
            </button>
            <button
              type="button"
              className="button button-primary"
              disabled={savingExtraction || !extractionText.trim()}
              onClick={() => void saveExtraction()}
            >
              {savingExtraction ? "저장 중…" : "수정 내용 저장"}
            </button>
          </div>
        </footer>
      </section>,
      document.body,
    );
  }

  async function setPhotoReadingChoice(decision: "read" | "not_required") {
    if (photoChoiceBusy) return;
    setPhotoChoiceBusy(true); setExtractionError("");
    try {
      const next = await apiFetch<Attachment>(`/api/attachments/${attachment.id}/photo-reading`, {
        method: "PATCH", body: JSON.stringify({ decision }),
      });
      setReviewAttachment(next); onAttachmentChanged?.(next);
      window.dispatchEvent(new CustomEvent("mesil-resident-links-changed", { detail: { messageId: next.message_id, attachmentId: attachment.id } }));
    } catch (reason) { setExtractionError(reason instanceof Error ? reason.message : "사진 판독 선택을 저장하지 못했습니다."); }
    finally { setPhotoChoiceBusy(false); }
  }

  function extractionPanel() {
    if (!showExtraction || (!isImage && !isAudio)) return null;
    const photoState = reviewAttachment?.photo_reading_status ?? attachment.photo_reading_status ?? (extraction?.status === "reviewed" ? "completed" : extraction?.status ?? "general");
    if (isImage && ["general", "no_text", "not_required"].includes(photoState)) {
      const label = photoState === "no_text" ? "글자를 찾지 못함" : photoState === "not_required" ? "판독 필요 없음" : "일반 사진";
      return <section className="photo-reading-neutral" aria-live="polite"><span>{label}</span>
        {canEditExtraction ? <div className="photo-reading-actions">
          <button type="button" disabled={photoChoiceBusy} onClick={() => void setPhotoReadingChoice("read")}>글자 판독하기</button>
          {photoState !== "not_required" ? <button type="button" disabled={photoChoiceBusy} onClick={() => void setPhotoReadingChoice("not_required")}>판독 필요 없음</button> : null}
        </div> : null}{extractionError ? <p role="alert">{extractionError}</p> : null}</section>;
    }
    if (!extraction) return null;
    const hasLatestConfirmation = Boolean(extraction.latest_confirmed_at);
    const confirmedTextForStaff =
      extraction.latest_confirmed_text?.trim() ||
      (extraction.status === "reviewed" ? extraction.reviewed_text?.trim() : "") ||
      "";
    const needsPreview = isImage && (extraction.content_included === false || !extraction.provider);
    const imageBusy = isImage && ["pending", "processing"].includes(extraction.status);
    const imageFailed = isImage && (extraction.status === "failed" || extraction.preprocessing?.output_blocked ||
      (!needsPreview && extraction.status === "completed" && !editableExtractionText(extraction).trim()));
    const statusLabel = isImage
      ? imageBusy ? extraction.status === "pending" ? "판독 대기 중" : "판독 중" : imageFailed ? "판독 실패"
        : needsPreview ? "판독 결과 불러오는 중…" : extraction.status === "reviewed" || hasLatestConfirmation ? "직원 확인됨" : "직원 확인 전"
      :
      extraction.status === "pending"
        ? "변환 대기 중"
        : extraction.status === "processing"
          ? "텍스트로 변환 중…"
          : extraction.status === "reviewed" || hasLatestConfirmation
            ? "반영됨"
            : extraction.status === "failed"
              ? "변환 실패"
              : "자동 변환 · 확인 전";
    const displayText = isImage
      ? imageBusy || imageFailed || needsPreview ? "" : extraction.latest_confirmed_text ?? extraction.reviewed_text ?? extraction.extracted_text ?? ""
      : editableExtractionText(extraction) || extractionText;
    const serviceLabel =
      extraction.suggested_service_context === "facility"
        ? "시설"
        : extraction.suggested_service_context === "daycare"
          ? "주간보호"
          : extraction.suggested_service_context === "homecare"
            ? "방문요양"
            : null;
    if (!canEditExtraction) {
      return (
        <section
          className={`attachment-extraction attachment-extraction-simple status-${extraction.status}`}
          data-attachment-view="staff-simple"
        >
          <div className="attachment-extraction-heading">
            <strong>{isAudio ? "확인된 받아쓰기" : "확인된 판독문"}</strong>
          </div>
          {confirmedTextForStaff ? (
            <pre>{confirmedTextForStaff}</pre>
          ) : (
            <p>
              {imageFailed ? "판독 결과를 만들지 못했습니다. 관리자에게 판독 상태 확인을 요청해 주세요." : extraction.status === "pending" || extraction.status === "processing"
                ? "텍스트를 확인하고 있습니다."
                : "아직 관리자가 확인한 텍스트가 없습니다."}
            </p>
          )}
        </section>
      );
    }
    return (
      <>
      <section className={`attachment-extraction status-${extraction.status}`}>
        {isImage && canEditExtraction ? <div className="photo-reading-actions">
          {!imageBusy && !imageFailed ? <button type="button" disabled={photoChoiceBusy} onClick={() => void setPhotoReadingChoice("read")}>이 이미지만 다시 판독</button> : null}
          <button type="button" disabled={photoChoiceBusy} onClick={() => void setPhotoReadingChoice("not_required")}>판독 필요 없음</button>
        </div> : null}
        <div className="attachment-extraction-heading">
          <div>
            <strong>{isAudio ? "음성 받아쓰기" : imageBusy || imageFailed || needsPreview ? statusLabel : `이미지 글자 판독 · ${statusLabel}`}</strong>
            {isAudio ? <span>{statusLabel}</span> : null}
            {!isAudio && extraction.preprocessing ? (
              <small className="attachment-extraction-input-variant">
                판독 입력 · {extraction.preprocessing.input_label}
                {extraction.preprocessing.original_preserved
                  ? " · 원본 보존"
                  : ""}
              </small>
            ) : null}
            {extraction.status === "completed" &&
            (extraction.auto_applied_name_count > 0 ||
              extraction.unresolved_name_count > 0) ? (
              <small className="attachment-extraction-suggestion-meta">
                {serviceLabel ? `${serviceLabel} 명단 기준 · ` : "전체 명단 기준 · "}
                {extraction.auto_applied_name_count > 0
                  ? `이름 ${extraction.auto_applied_name_count}곳 초안 반영`
                  : ""}
                {extraction.auto_applied_name_count > 0 &&
                extraction.unresolved_name_count > 0
                  ? " · "
                  : ""}
                {extraction.unresolved_name_count > 0
                  ? `${extraction.unresolved_name_count}곳 직접 확인`
                  : ""}
              </small>
            ) : null}
            {(extraction.latest_confirmed_at || extraction.reviewed_at) ? (
              <small className="attachment-extraction-reviewed-meta">
                {extraction.latest_confirmed_by_name ||
                  extraction.reviewed_by_name ||
                  "직원 확인"} ·{" "}
                {formatReviewedAt(
                  extraction.latest_confirmed_at || extraction.reviewed_at!,
                )}
              </small>
            ) : null}
          </div>
          {canEditExtraction &&
          !imageFailed && !needsPreview && (extraction.status === "completed" || extraction.status === "reviewed") ? (
            <div className="attachment-extraction-heading-actions">
              <button
                type="button"
                className="button button-secondary"
                data-attachment-action="edit-extraction"
                disabled={loadingReviewDetails}
                onClick={() => void loadAndOpenExtractionEditor()}
              >
                {loadingReviewDetails
                  ? "판독 정보 불러오는 중…"
                  : isAudio
                    ? "받아쓰기 확인·수정"
                    : "판독문 확인·수정"}
              </button>
            </div>
          ) : null}
        </div>
        {displayText ? (
          isAudio ? (
            <details className="attachment-transcript-details">
              <summary>음성 전사 원문 보기</summary>
              <pre>{displayText}</pre>
            </details>
          ) : (
            <pre>{displayText}</pre>
          )
        ) : (
          <p>
          {imageFailed
              ? safeExtractionErrorMessage(extraction.error_message)
              : needsPreview ? "판독 결과를 불러오고 있습니다."
              : extraction.status === "failed"
              ? safeExtractionErrorMessage(extraction.error_message)
              : extraction.error_message
                ? safeExtractionErrorMessage(extraction.error_message)
              : "변환이 끝나면 이곳에 텍스트가 표시됩니다."}
          </p>
        )}
        {extraction.status !== "reviewed" && extraction.review_warnings.length ? (
          <div className="attachment-review-warnings" role="note">
            <strong>원본 확인</strong>
            <ul>
              {extraction.review_warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {extraction.status !== "reviewed" && nameRegionCandidates.length ? (
          <details className="attachment-auto-application-details">
            <summary>
              자동 반영 내역 {extraction.auto_applied_name_count}건
              {extraction.unresolved_name_count > 0
                ? ` · 직접 확인 ${extraction.unresolved_name_count}건`
                : ""}
            </summary>
            <ul>
              {nameRegionCandidates.map((candidate) => (
                <li key={candidate.id}>
                  <span>
                    {candidate.recognized} → {candidate.candidate}
                  </span>
                  <small>
                    {candidate.displayRank}순위 · 우선도{" "}
                    {Math.round(candidate.confidence * 100)}%
                    {candidate.applied_to_draft
                      ? " · 수정 초안에 반영"
                      : candidate.application_reason ===
                          "already_matches_top_candidate"
                        ? " · 원문과 일치"
                      : candidate.displayRank === 1
                        ? " · 원문 위치를 찾지 못해 직접 확인"
                        : " · 대안 후보"}
                  </small>
                </li>
              ))}
            </ul>
            <p>
              자동 반영은 수정 초안에만 적용됩니다. 원본 판독문은 보존되며,
              수정 내용 저장 전까지 직원 확정본이 아닙니다.
            </p>
          </details>
        ) : null}
        {extraction.previous_attempts.length ? (
          <details className="attachment-extraction-history">
            <summary>이전 판독 {extraction.previous_attempts.length}건</summary>
            {extraction.previous_attempts.map((attempt) => (
              <section key={attempt.attempt_number}>
                <strong>
                  {attempt.attempt_number}차 판독 · {attempt.status === "reviewed" ? "직원 확인됨" : attempt.status === "failed" ? "실패" : "자동 판독"}
                </strong>
                <small>{new Date(attempt.archived_at).toLocaleString("ko-KR")}</small>
                <pre>
                  {attempt.reviewed_text ||
                    attempt.extracted_text ||
                    (attempt.error_message
                      ? safeExtractionErrorMessage(attempt.error_message)
                      : "저장된 내용 없음")}
                </pre>
              </section>
            ))}
          </details>
        ) : null}
        {canEditExtraction && (extraction.status === "failed" || imageFailed) ? (
          <div className="attachment-extraction-failure-actions">
            <button
              type="button"
              className="button button-secondary"
              data-attachment-action="start-extraction"
              disabled={savingExtraction}
              onClick={() => void retryExtraction()}
            >
              {savingExtraction ? "시작 중…" : "다시 판독"}
            </button>
            {isImage && extraction.preprocessing?.output_blocked ? (
              <button
                type="button"
                className="button button-primary"
                data-attachment-action="edit-extraction"
                disabled={loadingReviewDetails}
                onClick={() => void loadAndOpenExtractionEditor()}
              >
                직접 입력·마이크로 보완
              </button>
            ) : null}
          </div>
        ) : null}
        {extractionError ? <p className="form-error">{extractionError}</p> : null}
      </section>
      {extractionEditor()}
      </>
    );
  }

  if (attachment.can_download_original === false) {
    const confirmedText =
      attachment.text_extraction?.latest_confirmed_text?.trim() ?? "";
    return attachment.can_view_reviewed_text === true && confirmedText ? (
      <p className="staff-text-final">{confirmedText}</p>
    ) : null;
  }

  if (compact) {
    const confirmedText = attachment.text_extraction?.latest_confirmed_text?.trim() ?? "";
    return (
      <div className="compact-attachment-with-share">
        {attachment.resident_candidate_notice ? <p className="attachment-candidate-notice">{attachment.resident_candidate_notice}</p> : null}
        {isImage ? (
          imageButton("attachment-image-button compact")
        ) : isAudio ? (
          <div className="compact-audio-player">
            <strong>음성 파일 재생</strong>
            {showOriginalName ? <span>{attachment.original_name}</span> : null}
            <audio controls preload="metadata" src={url}>
              이 브라우저에서는 음성 재생을 지원하지 않습니다.
            </audio>
          </div>
        ) : documentInfo ? (
          <a
            className="compact-file compact-document-download"
            href={url}
            download={attachment.original_name}
          >
            <span className="document-file-icon" aria-hidden="true">
              {documentInfo.icon}
            </span>
            <strong>{documentInfo.label} 다운로드</strong>
            {showOriginalName ? <span>{attachment.original_name}</span> : null}
            <small>{formatBytes(attachment.size_bytes)}</small>
          </a>
        ) : (
          <span className="compact-file">
            <strong>{kindLabel(attachment)}</strong>
            {showOriginalName ? <span>{attachment.original_name}</span> : null}
            <small>{formatBytes(attachment.size_bytes)}</small>
          </span>
        )}
        {showCompactShare ? shareControl() : null}
        {showCompactConfirmedText && confirmedText ? (
          <p className="attachment-confirmed-text">
            <strong>확인된 판독문</strong>
            <span>{confirmedText}</span>
          </p>
        ) : null}
      </div>
    );
  }
  if (isImage) {
    return (
      <figure className="attachment-card image" data-attachment-id={attachment.id}>
        {attachment.resident_candidate_notice ? <p className="attachment-candidate-notice">{attachment.resident_candidate_notice}</p> : null}
        {imageButton("attachment-image-button")}
        <figcaption>
          <span>{attachment.original_name}</span>
          <small>{formatBytes(attachment.size_bytes)}</small>
        </figcaption>
        {shareControl()}
        {extractionPanel()}
      </figure>
    );
  }
  if (isAudio) {
    return (
      <figure className="attachment-card audio" data-attachment-id={attachment.id}>
        <figcaption>
          <strong>음성·음악 파일</strong>
          <span>{attachment.original_name}</span>
          <small>{formatBytes(attachment.size_bytes)}</small>
        </figcaption>
        <audio controls preload="metadata" src={url}>
          이 브라우저에서는 음성 재생을 지원하지 않습니다.
        </audio>
        {shareControl()}
        {showExtraction ? <StaffReviewedAttachment attachment={editorAttachment} canEdit={canEditExtraction} canRequestReading={attachment.can_request_reading === true} canViewReviewedText={attachment.can_view_reviewed_text === true} url={url} onChanged={updateAttachment} onSaveOriginal={() => saveOriginalFile(editorAttachment)} /> : null}
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
        {shareControl()}
      </figure>
    );
  }
  if (isPdf) {
    return (
      <>
        <div className="attachment-download-group">
          <button
            type="button"
            className="attachment-card download pdf"
            onClick={openPdf}
          >
            <strong>PDF 열기</strong>
            <span>{attachment.original_name}</span>
            <small>{formatBytes(attachment.size_bytes)}</small>
          </button>
          {shareControl()}
        </div>
        {pdfViewer}
        {showExtraction ? <StaffReviewedAttachment attachment={editorAttachment} canEdit={canEditExtraction} canRequestReading={attachment.can_request_reading === true} canViewReviewedText={attachment.can_view_reviewed_text === true} url={url} onChanged={updateAttachment} onOpenOriginal={openPdf} /> : null}
      </>
    );
  }
  if (documentInfo) {
    return (
      <div className="attachment-download-group">
        <a
          className="attachment-card download document-download"
          href={url}
          download={attachment.original_name}
          aria-label={`${attachment.original_name} 다운로드`}
        >
          <span className="document-file-icon" aria-hidden="true">
            {documentInfo.icon}
          </span>
          <strong>{documentInfo.label} 다운로드</strong>
          <span>{attachment.original_name}</span>
          <small>{formatBytes(attachment.size_bytes)}</small>
        </a>
        {shareControl()}
        {showExtraction ? <StaffReviewedAttachment attachment={editorAttachment} canEdit={canEditExtraction} canRequestReading={attachment.can_request_reading === true} canViewReviewedText={attachment.can_view_reviewed_text === true} url={url} onChanged={updateAttachment} onSaveOriginal={() => saveOriginalFile(editorAttachment)} /> : null}
      </div>
    );
  }
  return (
    <div className="attachment-download-group">
      <a className="attachment-card download" href={url} target="_blank" rel="noreferrer">
        <strong>{kindLabel(attachment)} 열기</strong>
        <span>{attachment.original_name}</span>
        <small>{formatBytes(attachment.size_bytes)}</small>
      </a>
      {shareControl()}
    </div>
  );
}
