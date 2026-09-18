"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { apiFetch } from "../api";
import {
  activateHandwritingEvidence,
  clearHandwritingStaffDraft,
  createHandwritingDraftState,
  handwritingEvidenceScopeKey,
  recordHandwritingStaffDraft,
  resolveHandwritingWorkspaceDraft,
} from "../handwritingVoiceDraft.mjs";
import type {
  Attachment,
  AttachmentCoordinateReview,
  CoordinateRegion,
  HandwritingCorrectionApprovalHistory,
  HandwritingVoiceCorrectionComparison,
} from "../types";

type CorrectionMode =
  | "direct_typing"
  | "full_reading"
  | "partial_correction"
  | "story_hint";

// Existing attachment API metadata; the server rechecks this on DELETE.
type CorrectionRecordingAttachment = Attachment & {
  correction_recording?: {
    can_remove: boolean;
    image_attachment_id: string | null;
    reason: string;
  } | null;
};

const modeOptions: { value: CorrectionMode; label: string; help: string }[] = [
  { value: "direct_typing", label: "직접 타이핑", help: "OCR 문장을 직원이 직접 고칩니다." },
  { value: "full_reading", label: "전체 읽어주기", help: "손글씨 전체를 읽은 음성과 OCR을 비교합니다." },
  { value: "partial_correction", label: "틀린 부분만 말하기", help: "말한 입력 항목만 변경 제안하고 나머지는 유지합니다." },
  { value: "story_hint", label: "내용 설명으로 보완", help: "설명은 문맥 힌트로만 사용하고 새 사실을 만들지 않습니다." },
];

const MAX_RECORDING_SECONDS = 120;

function recordingExtension(mimeType: string) {
  const normalized = mimeType.toLowerCase();
  if (normalized.includes("ogg")) return "ogg";
  if (normalized.includes("mp4") || normalized.includes("m4a")) return "m4a";
  if (normalized.includes("wav")) return "wav";
  if (normalized.includes("mpeg")) return "mp3";
  if (normalized.includes("aac")) return "aac";
  return "webm";
}

function recordingFilename(mimeType: string) {
  return `handwriting-correction-${Date.now()}.${recordingExtension(mimeType)}`;
}

function safeRecordingError(reason: unknown) {
  const message = reason instanceof Error ? reason.message.trim() : "";
  if (
    message &&
    !/https?:\/\//i.test(message) &&
    !/(opus|codec|decoder|packet|traceback|exception)/i.test(message)
  ) {
    return message;
  }
  return "음성 처리를 완료하지 못했습니다. 다시 녹음하거나 관리자에게 알려 주세요.";
}

function matchingConfirmedRegions(
  review: AttachmentCoordinateReview | null | undefined,
  sentence: string,
) {
  const normalizedSentence = sentence.replace(/\s+/g, "").trim();
  if (!normalizedSentence) return [];
  return (review?.regions ?? []).filter((region) => {
    if (region.placement_status !== "confirmed") return false;
    const regionText = (region.corrected_text || region.raw_text)
      .replace(/\s+/g, "")
      .trim();
    return Boolean(
      regionText &&
        (normalizedSentence.includes(regionText) || regionText.includes(normalizedSentence)),
    );
  });
}

const factLabels = {
  date: "날짜",
  time: "시간",
  quantity: "수량",
  unit: "단위",
  follow_up: "후속 확인",
  completion: "완료 상태",
  negation: "부정 표현",
  official_record: "공식 기록 여부",
} as const;

const factParticles = {
  date: "가",
  time: "이",
  quantity: "이",
  unit: "가",
  follow_up: "이",
  completion: "가",
  negation: "이",
  official_record: "가",
} as const;

const changedFieldLabels = {
  narrative: "문장 정리",
  date: "날짜",
  time: "시간",
  quantity: "수량",
  unit: "단위",
  follow_up: "후속 확인",
  completion: "완료 상태",
  negation: "부정 표현",
  name: "이름",
  medication: "약명",
  diagnosis: "진단",
  official_record: "공식 기록 여부",
} as const;

function completed(attachment: Attachment | undefined) {
  return ["completed", "reviewed"].includes(attachment?.text_extraction?.status ?? "");
}

function initialOcr(attachment: Attachment | undefined) {
  return (
    attachment?.text_extraction?.original_extracted_text ??
    attachment?.text_extraction?.extracted_text ??
    attachment?.text_extraction?.suggested_text ??
    attachment?.text_extraction?.reviewed_text ??
    attachment?.text_extraction?.latest_confirmed_text ??
    ""
  );
}

function editableOcr(attachment: Attachment | undefined) {
  return (
    attachment?.text_extraction?.latest_confirmed_text ??
    attachment?.text_extraction?.reviewed_text ??
    attachment?.text_extraction?.suggested_text ??
    attachment?.text_extraction?.extracted_text ??
    attachment?.text_extraction?.original_extracted_text ??
    ""
  );
}

type DifferenceSegment = { text: string; different: boolean };

function differenceTokens(value: string) {
  return value.match(/\s+|[\p{L}\p{N}]+|[^\s\p{L}\p{N}]+/gu) ?? [];
}

export function buildDifferenceSegments(
  source: string,
  target: string,
  side: "source" | "target",
) {
  const sourceTokens = differenceTokens(source);
  const targetTokens = differenceTokens(target);
  const lengths = Array.from(
    { length: sourceTokens.length + 1 },
    () => new Uint16Array(targetTokens.length + 1),
  );
  for (let sourceIndex = sourceTokens.length - 1; sourceIndex >= 0; sourceIndex -= 1) {
    for (let targetIndex = targetTokens.length - 1; targetIndex >= 0; targetIndex -= 1) {
      lengths[sourceIndex][targetIndex] = sourceTokens[sourceIndex] === targetTokens[targetIndex]
        ? lengths[sourceIndex + 1][targetIndex + 1] + 1
        : Math.max(
            lengths[sourceIndex + 1][targetIndex],
            lengths[sourceIndex][targetIndex + 1],
          );
    }
  }

  const segments: DifferenceSegment[] = [];
  function append(text: string, different: boolean) {
    if (!text) return;
    const previous = segments.at(-1);
    if (previous?.different === different) previous.text += text;
    else segments.push({ text, different });
  }

  let sourceIndex = 0;
  let targetIndex = 0;
  while (sourceIndex < sourceTokens.length || targetIndex < targetTokens.length) {
    if (
      sourceIndex < sourceTokens.length &&
      targetIndex < targetTokens.length &&
      sourceTokens[sourceIndex] === targetTokens[targetIndex]
    ) {
      append(side === "source" ? sourceTokens[sourceIndex] : targetTokens[targetIndex], false);
      sourceIndex += 1;
      targetIndex += 1;
    } else if (
      targetIndex >= targetTokens.length ||
      (sourceIndex < sourceTokens.length &&
        lengths[sourceIndex + 1][targetIndex] >= lengths[sourceIndex][targetIndex + 1])
    ) {
      if (side === "source") append(sourceTokens[sourceIndex], true);
      sourceIndex += 1;
    } else {
      if (side === "target") append(targetTokens[targetIndex], true);
      targetIndex += 1;
    }
  }
  return segments;
}

function DifferenceText({
  source,
  target,
  side,
}: {
  source: string;
  target: string;
  side: "source" | "target";
}) {
  const segments = useMemo(
    () => buildDifferenceSegments(source, target, side),
    [side, source, target],
  );
  return segments.map((segment, index) =>
    segment.different && /\S/.test(segment.text) ? (
      <mark className="voice-correction-difference" key={`${index}-${segment.text}`}>
        {segment.text}
      </mark>
    ) : (
      <span key={`${index}-${segment.text}`}>{segment.text}</span>
    ),
  );
}

export function criticalWarningLines(
  comparison: HandwritingVoiceCorrectionComparison,
  evidenceLabel: string,
) {
  const warnings = comparison.critical_facts
    .filter((fact) => fact.requires_staff_confirmation)
    .map((fact) => {
      const label = factLabels[fact.category];
      const particle = factParticles[fact.category];
      const imageValues = fact.image_values.join(" · ") || "확인되지 않음";
      const audioValues = fact.audio_values.join(" · ") || "확인되지 않음";
      return fact.status === "different"
        ? `${label}${particle} 서로 다릅니다: OCR ${imageValues} / ${evidenceLabel} ${audioValues}`
        : `${label}${particle} 한쪽에서만 확인됩니다: OCR ${imageValues} / ${evidenceLabel} ${audioValues}`;
    });
  const warningCategories = new Set(
    comparison.critical_facts
      .filter((fact) => fact.requires_staff_confirmation)
      .map((fact) => fact.category),
  );
  for (const field of comparison.changed_fields) {
    if (
      !["needs_confirmation", "blocked"].includes(field.status) ||
      warningCategories.has(field.category as keyof typeof factLabels)
    ) continue;
    const label = changedFieldLabels[field.category];
    const values = field.proposed_values.join(" · ") || field.before_values.join(" · ");
    warnings.push(
      field.status === "blocked"
        ? `${evidenceLabel}의 ${label}은 최종문에 추가하지 않았습니다${values ? `: ${values}` : "."}`
        : `${label}을 원본과 다시 확인해 주세요${values ? `: ${values}` : "."}`,
    );
  }
  return [...new Set(warnings)];
}

export function HandwritingVoiceCorrectionPanel({
  attachments,
  canUse,
  initialImageId,
  onRecordedAttachment,
  onRemovedRecording,
  coordinateReview,
  onFocusRegion,
}: {
  attachments: Attachment[];
  canUse: boolean;
  initialImageId?: string;
  onRecordedAttachment?: (attachment: Attachment) => void;
  onRemovedRecording?: (attachmentId: string) => void;
  coordinateReview?: AttachmentCoordinateReview | null;
  onFocusRegion?: (region: CoordinateRegion) => void;
}) {
  const [recordedAttachments, setRecordedAttachments] = useState<Attachment[]>([]);
  const [removingAudioId, setRemovingAudioId] = useState("");
  const [removedAudioIds, setRemovedAudioIds] = useState<string[]>([]);
  const availableAttachments = useMemo(
    () => [...attachments, ...recordedAttachments].filter((item) => !removedAudioIds.includes(item.id)),
    [attachments, recordedAttachments, removedAudioIds],
  );
  const images = useMemo(
    () => availableAttachments.filter((item) => item.mime_type.startsWith("image/")),
    [availableAttachments],
  );
  const audioFiles = useMemo(
    () => availableAttachments.filter((item) => item.mime_type.startsWith("audio/")),
    [availableAttachments],
  );
  const [mode, setMode] = useState<CorrectionMode>("direct_typing");
  const [imageId, setImageId] = useState(initialImageId ?? "");
  const [audioId, setAudioId] = useState("");
  const [comparison, setComparison] =
    useState<HandwritingVoiceCorrectionComparison | null>(null);
  const [directEdit, setDirectEdit] = useState(() =>
    editableOcr(images.find((item) => item.id === (initialImageId ?? ""))),
  );
  const [loading, setLoading] = useState(false);
  const [refining, setRefining] = useState(false);
  const [error, setError] = useState("");
  const [recordingState, setRecordingState] = useState<
    "idle" | "requesting" | "recording" | "uploading" | "transcribing" | "ready" | "error"
  >("idle");
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [recordingMessage, setRecordingMessage] = useState("");
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const recordingChunksRef = useRef<Blob[]>([]);
  const recordingTimerRef = useRef<number | null>(null);
  const recordingLimitRef = useRef<number | null>(null);
  const recordingRequestRef = useRef(0);
  const recordingCancelledRef = useRef(false);
  const uploadingRef = useRef(false);
  const fallbackFileRef = useRef<HTMLInputElement | null>(null);
  const comparisonRequestRef = useRef(0);
  const [staffDraftState, setStaffDraftState] = useState(createHandwritingDraftState);
  const staffDraftStateRef = useRef(staffDraftState);

  useEffect(() => () => {
    comparisonRequestRef.current += 1;
    recordingRequestRef.current += 1;
    recordingCancelledRef.current = true;
    if (recordingTimerRef.current !== null) {
      window.clearInterval(recordingTimerRef.current);
    }
    if (recordingLimitRef.current !== null) {
      window.clearTimeout(recordingLimitRef.current);
    }
    mediaRecorderRef.current?.stream.getTracks().forEach((track) => track.stop());
    mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
  }, []);

  if (!images.length) return null;

  const selectedImage = images.find((item) => item.id === imageId);
  const selectedAudio = audioFiles.find((item) => item.id === audioId);
  const recordingRemoval = (selectedAudio as CorrectionRecordingAttachment | undefined)?.correction_recording;
  const isCorrectionRecording = recordingRemoval
    ? recordingRemoval.image_attachment_id === selectedImage?.id
    : recordedAttachments.some((item) => item.id === selectedAudio?.id);
  const canRemoveRecording = Boolean(
    canUse && isCorrectionRecording && (recordingRemoval?.can_remove ?? true),
  );
  const evidenceScopeKey = handwritingEvidenceScopeKey({
    messageId: selectedImage?.message_id ?? "",
    imageId: selectedImage?.id ?? "",
    audioId,
  });
  const imageReady = completed(selectedImage);
  const audioReady = completed(selectedAudio);
  const audioRequired = mode !== "direct_typing";
  const microphoneSupported =
    typeof window !== "undefined" &&
    window.isSecureContext &&
    typeof navigator !== "undefined" &&
    Boolean(navigator.mediaDevices?.getUserMedia) &&
    typeof MediaRecorder !== "undefined";
  const selectedMode = modeOptions.find((item) => item.value === mode)!;
  const evidenceLabel = mode === "story_hint" ? "내용 설명 전사본" : "음성 전사본";
  const audioSelectLabel =
    mode === "partial_correction"
      ? "부분 수정 음성"
      : mode === "story_hint"
        ? "내용 설명 음성"
        : "전체 읽기 음성";
  const selectedAudioFailed = selectedAudio?.text_extraction?.status === "failed";
  const recordingActionLabel = recordingState === "error"
    ? "다시 녹음"
    : recordingState === "ready"
      ? "새로 녹음"
      : "마이크 시작";
  const processingLabel = recordingState === "error"
    ? "음성 처리가 종료됐습니다. 안내를 확인하고 다시 녹음해 주세요."
    : !selectedImage
    ? "먼저 손글씨 이미지를 선택해 주세요."
    : !imageReady
      ? "이미지 OCR 접수·처리 중입니다. 완료되면 교정할 수 있습니다."
      : mode === "direct_typing"
        ? "원본 OCR을 직접 수정할 수 있습니다. 아직 저장되지 않습니다."
        : loading
          ? "최종문을 정리하고 있습니다. OCR과 음성 근거는 그대로 확인할 수 있습니다."
        : refining
          ? "OCR과 음성 비교 결과를 먼저 보여드립니다. 내부 AI가 문장을 다듬는 중입니다."
        : !selectedAudio
          ? `${audioSelectLabel}을 선택해 주세요.`
          : selectedAudioFailed
            ? "음성 받아쓰기를 완료하지 못했습니다. 다시 녹음해 주세요."
          : !audioReady
            ? "음성 전사 접수·처리 중입니다. 화면을 막지 않고 백그라운드에서 계속됩니다."
            : comparison
              ? "교정 제안 준비 완료 · 직원 승인 대기"
              : "OCR과 음성 전사가 준비됐습니다. 교정 제안을 자동으로 준비합니다.";

  function resetResult() {
    comparisonRequestRef.current += 1;
    setComparison(null);
    setRefining(false);
    setError("");
  }

  function activateEvidenceScope(targetAudioId: string) {
    const scopeKey = handwritingEvidenceScopeKey({
      messageId: selectedImage?.message_id ?? "",
      imageId: selectedImage?.id ?? "",
      audioId: targetAudioId,
    });
    const nextState = activateHandwritingEvidence(
      staffDraftStateRef.current,
      scopeKey,
    );
    staffDraftStateRef.current = nextState;
    setStaffDraftState(nextState);
    return scopeKey;
  }

  function clearStaffDraft() {
    const nextState = clearHandwritingStaffDraft(
      staffDraftStateRef.current,
    );
    staffDraftStateRef.current = nextState;
    setStaffDraftState(nextState);
  }

  function selectMode(nextMode: CorrectionMode) {
    if (recordingState === "recording") return;
    setMode(nextMode);
    resetResult();
    setAudioId("");
    setRecordingState("idle");
    setRecordingMessage("");
    setDirectEdit(nextMode === "direct_typing" ? editableOcr(selectedImage) : "");
  }

  function recordingMimeType() {
    for (const mimeType of [
      "audio/webm;codecs=opus",
      "audio/webm",
      "audio/ogg;codecs=opus",
      "audio/mp4",
    ]) {
      if (MediaRecorder.isTypeSupported(mimeType)) return mimeType;
    }
    return "";
  }

  function stopRecordingResources() {
    if (recordingTimerRef.current !== null) {
      window.clearInterval(recordingTimerRef.current);
      recordingTimerRef.current = null;
    }
    if (recordingLimitRef.current !== null) {
      window.clearTimeout(recordingLimitRef.current);
      recordingLimitRef.current = null;
    }
    mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
    mediaStreamRef.current = null;
  }

  async function waitForTranscription(attachmentId: string, requestNo: number) {
    for (let attempt = 0; attempt < 60; attempt += 1) {
      if (recordingRequestRef.current !== requestNo) return;
      const next = await apiFetch<Attachment>(
        `/api/attachments/${attachmentId}/text-extraction`,
      );
      const status = next.text_extraction?.status;
      if (["completed", "reviewed"].includes(status ?? "")) {
        setRecordedAttachments((current) => [
          ...current.filter((item) => item.id !== next.id),
          next,
        ]);
        setAudioId(next.id);
        setRecordingState("ready");
        setRecordingMessage("내부 음성 받아쓰기가 완료됐습니다. 원본과 자동으로 대조합니다.");
        onRecordedAttachment?.(next);
        return next;
      }
      if (status === "failed") {
        throw new Error(
          next.text_extraction?.error_message || "내부 음성 받아쓰기에 실패했습니다.",
        );
      }
      await new Promise((resolve) => window.setTimeout(resolve, 1500));
    }
    throw new Error("음성 받아쓰기가 오래 걸리고 있습니다. 잠시 뒤 다시 확인해 주세요.");
  }

  async function uploadRecording(blob: Blob, mimeType: string) {
    if (!selectedImage || uploadingRef.current || blob.size < 256) {
      if (blob.size < 256) {
        setRecordingState("error");
        setRecordingMessage("녹음된 소리가 없습니다. 마이크를 확인하고 다시 녹음해 주세요.");
      }
      return;
    }
    uploadingRef.current = true;
    const requestNo = recordingRequestRef.current + 1;
    recordingRequestRef.current = requestNo;
    setRecordingState("uploading");
    setRecordingMessage("녹음을 내부 처리 경로에 접수하고 있습니다.");
    const form = new FormData();
    form.set("mode", mode);
    form.set(
      "file",
      new File([blob], recordingFilename(mimeType), {
        type: mimeType.split(";")[0] || "audio/webm",
      }),
    );
    try {
      const created = await apiFetch<Attachment>(
        `/api/attachments/${selectedImage.id}/voice-correction-recordings`,
        { method: "POST", body: form },
      );
      setRecordedAttachments((current) => [...current, created]);
      setAudioId(created.id);
      setRecordingState("transcribing");
      setRecordingMessage("격리된 내부 음성 받아쓰기에서 처리 중입니다. 화면을 닫지 않아도 다른 작업을 할 수 있습니다.");
      const transcribed = await waitForTranscription(created.id, requestNo);
      if (!transcribed || recordingRequestRef.current !== requestNo) return;
      await compareEvidence(transcribed.id, mode);
      if (recordingRequestRef.current === requestNo) {
        setRecordingMessage("받아쓰기와 OCR 대조가 끝났습니다. 수정 제안을 확인해 주세요.");
      }
    } catch (reason) {
      if (recordingRequestRef.current !== requestNo) return;
      setRecordingState("error");
      setRecordingMessage(
        safeRecordingError(reason),
      );
    } finally {
      uploadingRef.current = false;
    }
  }

  async function startRecording() {
    if (!canUse || !selectedImage || !audioRequired || recordingState === "recording") return;
    if (!microphoneSupported) {
      setRecordingState("error");
      setRecordingMessage(
        window.isSecureContext
          ? "이 브라우저는 즉석 녹음을 지원하지 않습니다. 시험용 음성파일 선택을 이용해 주세요."
          : "마이크는 HTTPS 또는 이 컴퓨터의 localhost에서만 사용할 수 있습니다.",
      );
      return;
    }
    setRecordingState("requesting");
    setRecordingMessage("내부 음성 받아쓰기가 준비됐는지 확인하고 있습니다.");
    setError("");
    recordingCancelledRef.current = false;
    try {
      const readiness = await apiFetch<{
        ready: boolean;
        status: "ready" | "disabled" | "configuration_error" | "unavailable" | "timeout" | "starting";
        message: string;
      }>(`/api/attachments/${selectedImage.id}/voice-correction-readiness`);
      if (!readiness.ready) {
        setRecordingState("error");
        setRecordingMessage(readiness.message);
        return;
      }
    } catch (reason) {
      setRecordingState("error");
      setRecordingMessage(
        reason instanceof Error
          ? reason.message
          : "내부 음성 받아쓰기 준비 상태를 확인하지 못했습니다.",
      );
      return;
    }
    setRecordingMessage("마이크 권한을 확인하고 있습니다.");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
      const mimeType = recordingMimeType();
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      mediaStreamRef.current = stream;
      mediaRecorderRef.current = recorder;
      recordingChunksRef.current = [];
      recorder.addEventListener("dataavailable", (event) => {
        if (event.data.size > 0) recordingChunksRef.current.push(event.data);
      });
      recorder.addEventListener("error", () => {
        stopRecordingResources();
        setRecordingState("error");
        setRecordingMessage("녹음 도중 오류가 발생했습니다. 다시 녹음해 주세요.");
      });
      recorder.addEventListener("stop", () => {
        stopRecordingResources();
        const blob = new Blob(recordingChunksRef.current, {
          type: recorder.mimeType || "audio/webm",
        });
        recordingChunksRef.current = [];
        if (recordingCancelledRef.current) {
          setRecordingState("idle");
          setRecordingMessage("녹음을 취소했습니다. 저장된 교정자료는 없습니다.");
          return;
        }
        void uploadRecording(blob, recorder.mimeType || "audio/webm");
      }, { once: true });
      recorder.start();
      setRecordingSeconds(0);
      setRecordingState("recording");
      setRecordingMessage("녹음 중입니다. 이미지를 보며 읽거나 설명해 주세요.");
      recordingTimerRef.current = window.setInterval(
        () => setRecordingSeconds((current) => current + 1),
        1000,
      );
      recordingLimitRef.current = window.setTimeout(() => {
        if (mediaRecorderRef.current?.state === "recording") {
          recordingCancelledRef.current = false;
          mediaRecorderRef.current.stop();
          setRecordingMessage(
            `${MAX_RECORDING_SECONDS}초 제한에 도달해 녹음을 마치고 내부 처리합니다.`,
          );
        }
      }, MAX_RECORDING_SECONDS * 1000);
    } catch (reason) {
      stopRecordingResources();
      setRecordingState("error");
      setRecordingMessage(
        reason instanceof DOMException && reason.name === "NotAllowedError"
          ? "마이크 권한이 거부됐습니다. 브라우저 주소창의 마이크 권한을 허용한 뒤 다시 시도해 주세요."
          : "마이크를 시작하지 못했습니다. 다른 앱이 마이크를 사용 중인지 확인해 주세요.",
      );
    }
  }

  function stopRecording() {
    if (mediaRecorderRef.current?.state !== "recording") return;
    recordingCancelledRef.current = false;
    mediaRecorderRef.current.stop();
  }

  function cancelRecording() {
    recordingCancelledRef.current = true;
    recordingRequestRef.current += 1;
    if (mediaRecorderRef.current?.state === "recording") {
      mediaRecorderRef.current.stop();
    } else {
      stopRecordingResources();
      setRecordingState("idle");
      setRecordingMessage("녹음 처리를 취소했습니다. 승인 최종문은 저장되지 않았습니다.");
    }
  }

  async function handleFallbackAudio(file: File | undefined) {
    if (!file) return;
    await uploadRecording(file, file.type || "audio/webm");
    if (fallbackFileRef.current) fallbackFileRef.current.value = "";
  }

  async function removeRecordedAudio(attachment: Attachment) {
    if (
      removingAudioId ||
      !canRemoveRecording || attachment.id !== selectedAudio?.id ||
      !window.confirm("추가한 음성을 제거할까요? 저장된 원본이나 승인 근거는 제거되지 않습니다.")
    ) return;
    setRemovingAudioId(attachment.id);
    setError("");
    try {
      await apiFetch(`/api/attachments/${attachment.id}/voice-correction-recording`, {
        method: "DELETE",
      });
      setRecordedAttachments((current) => current.filter((item) => item.id !== attachment.id));
      setRemovedAudioIds((current) => [...current, attachment.id]);
      onRemovedRecording?.(attachment.id);
      if (audioId === attachment.id) {
        recordingRequestRef.current += 1;
        setAudioId("");
        setRecordingState("idle");
        setRecordingMessage("추가한 음성을 제거했습니다.");
        resetResult();
        clearStaffDraft();
        setDirectEdit("");
      }
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "추가한 음성을 제거하지 못했습니다. 음성과 직원 수정문은 그대로 유지됩니다.",
      );
    } finally {
      setRemovingAudioId("");
    }
  }

  async function compareEvidence(targetAudioId = audioId, targetMode = mode) {
    if (
      !selectedImage ||
      !targetAudioId ||
      loading ||
      refining ||
      targetMode === "direct_typing"
    ) return;
    activateEvidenceScope(targetAudioId);
    const requestNo = comparisonRequestRef.current + 1;
    comparisonRequestRef.current = requestNo;
    const parameters = new URLSearchParams({
      audio_attachment_id: targetAudioId,
      mode: targetMode,
      refine: "false",
    });
    setLoading(true);
    setRefining(false);
    setError("");
    try {
      const result = await apiFetch<HandwritingVoiceCorrectionComparison>(
        `/api/attachments/${selectedImage.id}/voice-correction-comparison?${parameters.toString()}`,
      );
      if (comparisonRequestRef.current !== requestNo) return;
      setComparison(result);
      const suggestion = result.stages.find(
        (stage) => stage.stage === "ai_correction_suggestion",
      );
      const initialSuggestion = suggestion?.text ?? "";
      setDirectEdit(initialSuggestion);
      setLoading(false);

      if (targetMode !== "full_reading") return;
      setRefining(true);
      parameters.set("refine", "true");
      try {
        const refined = await apiFetch<HandwritingVoiceCorrectionComparison>(
          `/api/attachments/${selectedImage.id}/voice-correction-comparison?${parameters.toString()}`,
        );
        if (comparisonRequestRef.current !== requestNo) return;
        setComparison(refined);
        const refinedText = refined.stages.find(
          (stage) => stage.stage === "ai_correction_suggestion",
        )?.text ?? initialSuggestion;
        setDirectEdit(refinedText);
      } catch {
        if (comparisonRequestRef.current === requestNo) {
          setError("기본 교정 제안을 표시했습니다. 내부 문장 다듬기는 완료하지 못했습니다.");
        }
      } finally {
        if (comparisonRequestRef.current === requestNo) setRefining(false);
      }
    } catch (reason) {
      if (comparisonRequestRef.current !== requestNo) return;
      setError(
        reason instanceof Error
          ? reason.message
          : "받아쓰기와 원본을 대조하지 못했습니다.",
      );
    } finally {
      if (comparisonRequestRef.current === requestNo) setLoading(false);
    }
  }

  const originalStage = comparison?.stages.find((stage) => stage.stage === "initial_ocr");
  const audioStage = comparison?.stages.find((stage) => stage.stage === "audio_transcript");

  return (
    <section className="handwriting-voice-correction" aria-labelledby="voice-correction-title">
      <header>
        <div>
          <strong id="voice-correction-title">손글씨 수정</strong>
          <p>
            원본과 보조 근거를 나누어 확인합니다. 직원 승인 전에는 최종문·공식
            기록·학습자료로 저장되지 않습니다.
          </p>
        </div>
        <span className="correction-mode-badge">내부 처리</span>
      </header>

      <div className="voice-correction-primary-actions" role="group" aria-label="손글씨 수정 방법">
        <button
          type="button"
          className={mode === "direct_typing" ? "selected" : ""}
          aria-pressed={mode === "direct_typing"}
          onClick={() => selectMode("direct_typing")}
        >
          직접 고치기
        </button>
        <button
          type="button"
          className={mode !== "direct_typing" ? "selected" : ""}
          aria-pressed={mode !== "direct_typing"}
          onClick={() => selectMode(mode === "direct_typing" ? "full_reading" : mode)}
        >
          말로 고치기
        </button>
      </div>

      {audioRequired ? (
        <label className="voice-correction-voice-mode">
          말하는 방법
          <select
            value={mode}
            disabled={recordingState === "recording"}
            onChange={(event) => selectMode(event.target.value as CorrectionMode)}
          >
            {modeOptions.filter((item) => item.value !== "direct_typing").map((item) => (
              <option key={item.value} value={item.value}>{item.label}</option>
            ))}
          </select>
        </label>
      ) : null}

      <p className="voice-correction-mode-help" role="note">
        현재 방식: <strong>{selectedMode.label}</strong> · {selectedMode.help}
      </p>

      <div className="voice-correction-selectors">
        {!initialImageId ? (
          <label>
            손글씨 이미지
            <select
              value={imageId}
              disabled={!canUse || loading}
              onChange={(event) => {
                const nextId = event.target.value;
                const nextDraftState = createHandwritingDraftState();
                staffDraftStateRef.current = nextDraftState;
                setStaffDraftState(nextDraftState);
                setImageId(nextId);
                resetResult();
                if (mode === "direct_typing") {
                  setDirectEdit(editableOcr(images.find((item) => item.id === nextId)));
                } else {
                  setDirectEdit("");
                }
              }}
            >
              <option value="">선택해 주세요</option>
              {images.map((item) => (
                <option key={item.id} value={item.id}>{item.original_name}</option>
              ))}
            </select>
          </label>
        ) : null}
      </div>

      {audioRequired ? (
        <section className="voice-correction-recorder" aria-labelledby="voice-recorder-title">
          <div>
            <strong id="voice-recorder-title">지금 마이크로 말하기</strong>
            <span>
              {mode === "full_reading"
                ? "이미지를 보며 전체 내용을 읽어 주세요."
                : mode === "partial_correction"
                  ? "틀린 시간·수량·문장만 짧게 말해 주세요."
                  : "기록의 줄거리만 설명해 주세요. 새 사실은 만들지 않습니다."}
            </span>
            <small>숫자와 단위는 문장 사이를 잠시 쉬며 천천히 또박또박 읽어 주세요.</small>
          </div>
          <div className="voice-recorder-actions">
            {recordingState !== "recording" ? (
              <button
                type="button"
                className="button button-primary"
                disabled={!canUse || !selectedImage || ["requesting", "uploading", "transcribing"].includes(recordingState)}
                onClick={() => void startRecording()}
              >
                {recordingActionLabel}
              </button>
            ) : (
              <>
                <button type="button" className="button button-primary" onClick={stopRecording}>
                  녹음 마치기 · {recordingSeconds}초
                </button>
                <button type="button" className="button button-secondary" onClick={cancelRecording}>
                  녹음 취소
                </button>
              </>
            )}
            {["requesting", "uploading", "transcribing"].includes(recordingState) ? (
              <button type="button" className="button button-secondary" onClick={cancelRecording}>
                처리 취소
              </button>
            ) : null}
          </div>
          <p className={recordingState === "error" ? "form-error" : ""} role="status" aria-live="polite">
            {recordingMessage || (microphoneSupported
              ? "녹음은 내부 격리 음성 받아쓰기에서만 처리됩니다."
              : "이 환경에서는 HTTPS 또는 localhost와 마이크 지원 여부를 확인해야 합니다.")}
          </p>
          <details className="voice-correction-fallback-file">
            <summary>시험·예외용 음성파일 사용</summary>
            <p>평소에는 마이크를 사용합니다. 기존 파일 선택은 시험이나 녹음 불가 상황에서만 사용합니다.</p>
            <label>
              새 음성파일
              <input
                ref={fallbackFileRef}
                type="file"
                accept="audio/wav,audio/x-wav,audio/webm,audio/ogg,audio/mp4,audio/x-m4a,audio/mpeg,audio/aac"
                disabled={!canUse || ["uploading", "transcribing"].includes(recordingState)}
                onChange={(event) => void handleFallbackAudio(event.currentTarget.files?.[0])}
              />
            </label>
            {audioFiles.length ? (
              <label>
                이미 첨부된 {audioSelectLabel}
                <select
                  value={audioId}
                  disabled={!canUse || loading}
                  onChange={(event) => {
                    const nextAudioId = event.target.value;
                    activateEvidenceScope(nextAudioId);
                    setAudioId(nextAudioId);
                    setDirectEdit("");
                    resetResult();
                    setRecordingState(nextAudioId ? "ready" : "idle");
                    const nextAudio = audioFiles.find((item) => item.id === nextAudioId);
                    if (nextAudio && completed(nextAudio)) {
                      void compareEvidence(nextAudioId, mode);
                    }
                  }}
                >
                  <option value="">선택해 주세요</option>
                  {audioFiles.map((item) => (
                    <option key={item.id} value={item.id}>{item.original_name}</option>
                  ))}
                </select>
              </label>
            ) : null}
            {selectedAudio && isCorrectionRecording ? (
              <>
              <button
                type="button"
                className="button button-secondary"
                disabled={!canRemoveRecording || Boolean(removingAudioId) || recordingState === "recording"}
                onClick={() => void removeRecordedAudio(selectedAudio)}
              >
                {removingAudioId === selectedAudio.id ? "음성 제거 중…" : "추가한 음성 제거"}
              </button>
              {recordingRemoval && !recordingRemoval.can_remove ? (
                <p role="note">{recordingRemoval.reason}</p>
              ) : null}
              </>
            ) : null}
          </details>
        </section>
      ) : null}

      <div className="voice-correction-status" role="status" aria-live="polite">
        <span className={selectedImage ? "done" : ""}>접수</span>
        <span className={recordingState === "error" ? "error" : audioRequired && audioReady ? "done" : ""}>
          {recordingState === "error"
            ? "음성 처리 종료"
            : mode === "story_hint" ? "설명 전사" : "음성 전사"}
        </span>
        <span className={comparison ? "done" : ""}>
          {refining ? "정밀 보강 중" : "교정 제안"}
        </span>
        <span className={comparison || mode === "direct_typing" ? "waiting" : ""}>승인 대기</span>
        <p>{processingLabel}</p>
      </div>

      {audioRequired && audioReady && !comparison && !loading ? (
        <button
          type="button"
          className="button button-secondary voice-correction-retry"
          disabled={!canUse}
          aria-describedby="voice-correction-help"
          onClick={() => void compareEvidence(audioId, mode)}
        >
          교정 제안 다시 만들기
        </button>
      ) : null}
      <small id="voice-correction-help">
        {canUse
          ? "직접 타이핑은 항상 유지되며, 승인 직전까지만 준비합니다."
          : "작성자 또는 기록 담당 직원만 교정 비교를 준비할 수 있습니다."}
      </small>
      {error ? <p className="form-error">{error}</p> : null}
      {comparison ? (
        <p
          className={comparison.audio_quality.status === "low" ? "form-error" : "voice-correction-quality-ready"}
          role="status"
        >
          {comparison.audio_quality.message}
        </p>
      ) : null}
      {comparison ? (
        <div className="voice-correction-ai-state" role="status" aria-live="polite">
          <strong>
            {comparison.ai_combination.status === "applied"
              ? "AI 조합 완료"
              : "AI 조합 미적용 · 기본 비교안"}
          </strong>
          <p>
            {comparison.ai_combination.status === "applied"
              ? "OCR과 검증된 음성 근거를 조합한 초안입니다. 직원 확인 전에는 확정되지 않습니다."
              : "OCR과 음성의 차이를 기본 규칙으로 표시했습니다. 원문과 음성을 확인해 최종문을 수정해 주세요."}
          </p>
          {comparison.review_items.length ? (
            <p className="voice-correction-review-required">
              중요 항목 확인 필요 · 아래 항목을 각각 확인해야 저장할 수 있습니다.
            </p>
          ) : null}
        </div>
      ) : null}

      {mode === "direct_typing" && selectedImage && imageReady ? (
        <div className="voice-correction-result">
          <ApprovalWorkspace
            key={`direct_typing-${imageId}`}
            imageId={imageId}
            messageId={selectedImage.message_id}
            audioId={null}
            mode="direct_typing"
            sourceText={initialOcr(selectedImage)}
            evidenceText={null}
            evidenceLabel={null}
            proposedText={directEdit}
            warningLines={[]}
            requiresConfirmation={false}
            reviewItems={[]}
            canUse={canUse}
            coordinateReview={coordinateReview}
            onFocusRegion={onFocusRegion}
          />
        </div>
      ) : null}

      {comparison ? (
        <div className="voice-correction-result">
          <ApprovalWorkspace
            key={evidenceScopeKey}
            imageId={imageId}
            messageId={selectedImage?.message_id}
            audioId={audioId}
            mode={mode}
            sourceText={originalStage?.text || ""}
            evidenceText={audioStage?.text || ""}
            evidenceLabel={evidenceLabel}
            proposedText={directEdit}
            warningLines={criticalWarningLines(comparison, evidenceLabel)}
            requiresConfirmation={
              comparison.critical_facts.some((fact) => fact.requires_staff_confirmation) ||
              comparison.changed_fields.some((field) =>
                ["needs_confirmation", "blocked"].includes(field.status),
              )
            }
            reviewItems={comparison.review_items}
            canUse={canUse}
            coordinateReview={coordinateReview}
            onFocusRegion={onFocusRegion}
            preservedDraft={resolveHandwritingWorkspaceDraft(
              staffDraftState,
              evidenceScopeKey,
              directEdit,
            )}
            onUserEdit={(value) => {
              const nextState = recordHandwritingStaffDraft(
                staffDraftStateRef.current,
                evidenceScopeKey,
                value,
              );
              staffDraftStateRef.current = nextState;
              setStaffDraftState(nextState);
            }}
            onSaveSuccess={clearStaffDraft}
          />
        </div>
      ) : null}
    </section>
  );
}

function splitCorrectionSentences(value: string) {
  return value
    .split(/(?:\r?\n)+|(?<=[.!?])\s+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function ApprovalWorkspace({
  imageId,
  messageId,
  audioId,
  mode,
  sourceText,
  evidenceText,
  evidenceLabel,
  proposedText,
  warningLines,
  requiresConfirmation,
  reviewItems,
  canUse,
  coordinateReview,
  onFocusRegion,
  preservedDraft,
  onUserEdit,
  onSaveSuccess,
}: {
  imageId: string;
  messageId?: string;
  audioId: string | null;
  mode: CorrectionMode;
  sourceText: string;
  evidenceText: string | null;
  evidenceLabel: string | null;
  proposedText: string;
  warningLines: string[];
  requiresConfirmation: boolean;
  reviewItems: HandwritingVoiceCorrectionComparison["review_items"];
  canUse: boolean;
  coordinateReview?: AttachmentCoordinateReview | null;
  onFocusRegion?: (region: CoordinateRegion) => void;
  preservedDraft?: { text: string; dirty: boolean };
  onUserEdit?: (value: string) => void;
  onSaveSuccess?: () => void;
}) {
  const proposedSentences = useMemo(
    () => splitCorrectionSentences(proposedText),
    [proposedText],
  );
  const sourceSentences = useMemo(
    () => splitCorrectionSentences(sourceText),
    [sourceText],
  );
  const confirmedRegionsBySentence = useMemo(
    () =>
      sourceSentences.map((sentence) =>
        matchingConfirmedRegions(coordinateReview, sentence),
      ),
    [coordinateReview, sourceSentences],
  );
  const shouldLoadHistory = Boolean(
    canUse && imageId && (mode === "direct_typing" || audioId),
  );
  const [finalDraftText, setFinalDraftText] = useState(
    () => preservedDraft?.text ?? proposedSentences.join("\n"),
  );
  const [finalConfirmed, setFinalConfirmed] = useState(false);
  const [history, setHistory] = useState<HandwritingCorrectionApprovalHistory | null>(null);
  const [loadingHistory, setLoadingHistory] = useState(shouldLoadHistory);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [idempotencyKey, setIdempotencyKey] = useState("");
  const [reviewResolutions, setReviewResolutions] = useState<Record<string, {
    review_item_id: string;
    selected_source: "ocr" | "whisper" | "ai_recommendation" | "staff_manual";
    selected_value: string;
  }>>({});
  const savingRef = useRef(false);
  const finalDirtyRef = useRef(Boolean(preservedDraft?.dirty));

  useEffect(() => {
    if (!finalDirtyRef.current) {
      setFinalDraftText(proposedText.trim());
      setFinalConfirmed(false);
      setReviewResolutions({});
    }
  }, [proposedText]);

  useEffect(() => {
    if (!shouldLoadHistory) return;
    let active = true;
    const parameters = new URLSearchParams({ mode });
    if (audioId) parameters.set("audio_attachment_id", audioId);
    void apiFetch<HandwritingCorrectionApprovalHistory>(
      `/api/attachments/${imageId}/voice-correction-approvals?${parameters.toString()}`,
    )
      .then((result) => {
        if (!active) return;
        setHistory(result);
        const latest = result.versions.at(-1);
        if (latest && !finalDirtyRef.current) {
          setFinalDraftText(latest.approved_final_text);
          setFinalConfirmed(true);
        }
      })
      .catch((reason) => {
        if (active) {
          setSaveError(
            reason instanceof Error ? reason.message : "저장된 승인본을 불러오지 못했습니다.",
          );
        }
      })
      .finally(() => {
        if (active) setLoadingHistory(false);
      });
    return () => {
      active = false;
    };
  }, [audioId, imageId, mode, shouldLoadHistory]);

  const latestApproval = history?.versions.at(-1) ?? null;
  const finalText = finalDraftText.trim();
  const matchesLatest = Boolean(
    latestApproval && finalConfirmed && latestApproval.approved_final_text === finalText,
  );
  const reviewStatus = matchesLatest
    ? "승인 완료"
    : finalConfirmed
      ? "저장 대기"
      : "검토 중";
  const effectiveWarnings = requiresConfirmation
    ? warningLines.length
      ? warningLines
      : ["충돌하거나 확인되지 않은 값이 있습니다. 원본과 다시 대조해 주세요."]
    : [];
  const allReviewItemsResolved = reviewItems.every(
    (item) => Boolean(reviewResolutions[item.review_item_id]),
  );
  const confirmedRegions = useMemo(() => {
    const unique = new Map<string, CoordinateRegion>();
    for (const regions of confirmedRegionsBySentence) {
      for (const region of regions) unique.set(region.client_id, region);
    }
    return [...unique.values()];
  }, [confirmedRegionsBySentence]);
  const canSave = Boolean(
    canUse &&
      finalConfirmed &&
      allReviewItemsResolved &&
      finalText.trim() &&
      !matchesLatest &&
      !saving,
  );

  function resetSubmission() {
    setIdempotencyKey("");
    setSaveError("");
  }

  function updateFinalDraft(value: string) {
    onUserEdit?.(value);
    finalDirtyRef.current = true;
    setFinalDraftText(value);
    setFinalConfirmed(false);
    setReviewResolutions({});
    resetSubmission();
  }

  function confirmReviewItem(
    item: HandwritingVoiceCorrectionComparison["review_items"][number],
  ) {
    if (!canUse || !finalText) return;
    // Confirm the employee-reviewed document, never substitute a value from
    // a category-wide list into an unrelated event in that document.
    setReviewResolutions((current) => ({
      ...current,
      [item.review_item_id]: {
        review_item_id: item.review_item_id,
        selected_source: "staff_manual",
        // The existing API limits this confirmation field to 500 characters.
        // The complete, employee-reviewed text is stored in sentences.final_text.
        selected_value: `직원 최종문 대조 확인 · ${changedFieldLabels[item.category]} (확정 내용은 함께 저장한 최종문 참조)`,
      },
    }));
    setFinalConfirmed(false);
    resetSubmission();
  }

  async function saveApprovedCorrection() {
    if (!canSave || savingRef.current) return;
    savingRef.current = true;
    setSaving(true);
    setSaveError("");
    const requestKey = idempotencyKey || crypto.randomUUID();
    setIdempotencyKey(requestKey);
    try {
      const result = await apiFetch<HandwritingCorrectionApprovalHistory>(
        `/api/attachments/${imageId}/voice-correction-approvals`,
        {
          method: "POST",
          body: JSON.stringify({
            audio_attachment_id: audioId,
            mode,
            idempotency_key: requestKey,
            conflicts_confirmed: finalConfirmed,
            conflict_resolutions: reviewItems.map((item) => {
              const resolution = reviewResolutions[item.review_item_id];
              // Keep both contracts while deployed backends use choice/value.
              return {
                ...resolution,
                choice: resolution.selected_source,
                value: resolution.selected_value,
              };
            }),
            supersedes_approval_id: latestApproval?.id ?? null,
            sentences: [{
              sentence_no: 1,
              source_text: sourceText.trim() || "원본 OCR 확인 필요",
              proposed_text: proposedText.trim(),
              final_text: finalText,
              action: finalText === proposedText.trim() ? "accepted" : "edited",
              approved: true,
              evidence_refs: [
                `attachment:${imageId}`,
                ...(audioId ? [`attachment:${audioId}`] : []),
              ],
              coordinate_region_ids: confirmedRegions.map((region) => region.client_id),
            }],
          }),
        },
      );
      setHistory(result);
      finalDirtyRef.current = false;
      onSaveSuccess?.();
      window.dispatchEvent(new CustomEvent("mesil-resident-links-changed", {
        detail: {
          messageId,
          attachmentId: imageId,
          revision: result.resident_link_revision ?? 0,
        },
      }));
    } catch (reason) {
      setSaveError(
        reason instanceof Error ? reason.message : "승인한 교정자료를 저장하지 못했습니다.",
      );
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }

  return (
    <section className="voice-correction-approval-workspace" aria-labelledby="correction-approval-title">
      <header>
        <div>
          <strong id="correction-approval-title">원본·음성·최종문 비교</strong>
          <p>달라진 부분만 확인하고 최종문을 한 곳에서 직접 고쳐 주세요.</p>
        </div>
        <span className={`approval-review-status status-${reviewStatus.replaceAll(" ", "-")}`} aria-live="polite">
          {reviewStatus}
        </span>
      </header>

      <div className={`voice-correction-three-way ${evidenceText === null ? "without-audio" : ""}`}>
        <section aria-labelledby="original-ocr-title">
          <strong id="original-ocr-title">원본 OCR</strong>
          <p className="voice-correction-readonly-text">
            <DifferenceText source={sourceText} target={finalDraftText} side="source" />
          </p>
        </section>

        {evidenceText !== null ? (
          <section aria-labelledby="voice-transcript-title">
            <strong id="voice-transcript-title">{evidenceLabel || "음성 전사본"}</strong>
            <p className="voice-correction-readonly-text">
              <DifferenceText source={evidenceText} target={finalDraftText} side="source" />
            </p>
          </section>
        ) : null}

        <section className="voice-correction-final-section" aria-labelledby="final-suggestion-title">
          <strong id="final-suggestion-title">최종문 제안</strong>
          <p className="voice-correction-final-preview" aria-label="변경 부분 강조 미리보기">
            <DifferenceText source={sourceText} target={finalDraftText} side="target" />
          </p>
          <label className="voice-correction-final-editor">
            <span className="sr-only">직원 최종문 편집</span>
            <textarea
              rows={Math.min(12, Math.max(5, proposedSentences.length * 2))}
              value={finalDraftText}
              disabled={!canUse}
              onChange={(event) => updateFinalDraft(event.target.value)}
            />
          </label>
          {effectiveWarnings.length ? (
            <p className="voice-correction-inline-warning" role="alert">
              확인 필요: {effectiveWarnings.join(" · ")}
            </p>
          ) : null}
        </section>
      </div>

      {reviewItems.length ? (
        <section className="voice-correction-review-items" aria-labelledby="review-items-title">
          <header>
            <strong id="review-items-title">중요 항목 확인 필요</strong>
            <span>{Object.keys(reviewResolutions).length}/{reviewItems.length}개 확인</span>
          </header>
          <p>아래 차이를 원본과 대조해 주세요. 최종문이 맞으면 항목별로 확인하고, 틀리면 위 최종문에서 직접 고쳐 주세요. 확인 버튼은 문장을 바꾸지 않습니다.</p>
          <ol>
            {reviewItems.map((item) => {
              const resolved = reviewResolutions[item.review_item_id];
              return (
                <li key={item.review_item_id} className={resolved ? "resolved" : ""}>
                  <div className="voice-correction-review-heading">
                    <strong>{changedFieldLabels[item.category]}</strong>
                    <span>{item.kind === "conflict" ? "근거 충돌" : "한쪽 근거 중요 항목"}</span>
                  </div>
                  <div className="voice-correction-review-values">
                    <div>
                      <span>OCR</span>
                      <p>{item.ocr_values.join(" · ") || "확인되지 않음"}</p>
                    </div>
                    <div>
                      <span>음성</span>
                      <p>{item.whisper_values.join(" · ") || "확인되지 않음"}</p>
                    </div>
                    <div>
                      <span>AI 추천</span>
                      <p>{item.ai_recommendation || "추천값 없음 · 직원이 근거를 확인해 주세요."}</p>
                      {item.ai_recommendation ? (
                        <>
                          {item.ai_recommendation_reason ? <small>{item.ai_recommendation_reason}</small> : null}
                        </>
                      ) : null}
                    </div>
                  </div>
                  <div className="voice-correction-manual-resolution">
                    <button
                      type="button"
                      className="button button-secondary"
                      disabled={!canUse || !finalText}
                      aria-pressed={Boolean(resolved)}
                      onClick={() => confirmReviewItem(item)}
                    >
                      {resolved ? "이 항목 확인 완료" : "최종문에서 이 항목 확인"}
                    </button>
                  </div>
                  {resolved ? (
                    <p className="voice-correction-resolution-result">
                      최종문을 대조해 확인했습니다. 문장은 변경하지 않았습니다.
                    </p>
                  ) : (
                    <p className="voice-correction-resolution-pending">위 최종문에서 이 항목이 맞는지 확인해 주세요. 본문을 수정하면 확인 표시가 해제됩니다.</p>
                  )}
                </li>
              );
            })}
          </ol>
        </section>
      ) : null}

      <div className="voice-correction-compact-review">
        {confirmedRegions.length ? (
          <button
            type="button"
            className="link-button sentence-coordinate-link"
            onClick={() => onFocusRegion?.(confirmedRegions[0])}
          >
            원본 위치 보기 · 확인된 글줄 {confirmedRegions.length}개
          </button>
        ) : (
          <small className="sentence-coordinate-pending">
            원본 위치 확인 필요 · 임의 좌표를 만들지 않습니다.
          </small>
        )}
        <label className="approval-final-confirmation">
          <input
            type="checkbox"
            checked={finalConfirmed}
            disabled={!canUse || !finalText.trim() || !allReviewItemsResolved}
            onChange={(event) => {
              setFinalConfirmed(event.target.checked);
              resetSubmission();
            }}
          />
          최종문 전체를 확인했습니다.
        </label>
      </div>

      <div className="approval-save-panel" role="note">
        <strong>{finalConfirmed ? "최종문 검토 완료" : "최종문을 확인해 주세요."}</strong>
        <p>
          확인 전·취소·화면 이탈 상태는 저장되지 않습니다. 저장해도 공식 기록·서명·공단 전송·모델 학습에는 반영되지 않습니다.
        </p>
        {latestApproval && !matchesLatest ? (
          <p>저장하면 이전 승인본을 덮어쓰지 않고 새 수정본으로 추가합니다.</p>
        ) : null}
        <button
          type="button"
          className="button button-primary"
          disabled={!canSave}
          aria-describedby="approval-save-help"
          onClick={() => void saveApprovedCorrection()}
        >
          {saving ? "승인본 저장 중…" : matchesLatest ? "승인 완료" : "승인한 최종문 저장"}
        </button>
        <small id="approval-save-help">
          {!canUse
            ? "작성자 또는 기록 담당 직원만 승인할 수 있습니다."
            : !allReviewItemsResolved
              ? "중요 항목을 각각 확인한 뒤 최종문을 승인할 수 있습니다."
            : !finalConfirmed
                ? "최종문 전체 확인 후 교정자료로 저장할 수 있습니다."
                : "교정자료에만 새 버전으로 저장되며 공식 기록과 분리됩니다."}
        </small>
        {saveError ? <p className="form-error" role="alert">{saveError}</p> : null}
      </div>

      <details className="approval-version-history">
        <summary>
          저장된 승인본 {history?.versions.length ?? 0}개
          {loadingHistory ? " · 확인 중" : ""}
        </summary>
        {history?.versions.length ? (
          <ol>
            {history.versions.map((version) => (
              <li key={version.id}>
                <strong>수정본 {version.revision}</strong>
                <span>{version.approved_by_name} 직원 확인 · {new Date(version.approved_at).toLocaleString("ko-KR")}</span>
                <p>{version.approved_final_text}</p>
                <small>
                  근거 {version.evidence_refs.length}건 · 공식 기록 저장 안 됨 · 외부 전송 안 함
                </small>
              </li>
            ))}
          </ol>
        ) : (
          <p>아직 저장된 승인본이 없습니다.</p>
        )}
      </details>
    </section>
  );
}
