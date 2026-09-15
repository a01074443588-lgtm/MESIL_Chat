import type { Attachment } from "./types";

const automaticAttachmentBodies = new Set([
  "파일을 첨부했습니다.",
  "보고서 이미지를 첨부했습니다.",
]);

type PresentableAttachment = Pick<Attachment, "mime_type">;

export const LONG_MESSAGE_CHARACTER_LIMIT = 500;
export const LONG_MESSAGE_LINE_LIMIT = 8;

export function isAutomaticAttachmentBody(
  body: string,
  attachments: PresentableAttachment[],
) {
  return attachments.length > 0 && automaticAttachmentBodies.has(body.trim());
}

export function messageDisplayBody(
  body: string,
  attachments: PresentableAttachment[],
) {
  return isAutomaticAttachmentBody(body, attachments) ? "" : body;
}

export function isLongMessageBody(body: string) {
  const normalized = body.trim();
  if (!normalized) return false;
  return (
    normalized.length > LONG_MESSAGE_CHARACTER_LIMIT ||
    normalized.split(/\r?\n/).length > LONG_MESSAGE_LINE_LIMIT
  );
}

export function attachmentSummary(attachments: PresentableAttachment[]) {
  if (attachments.length === 0) return "";

  const counts = {
    image: 0,
    audio: 0,
    video: 0,
    pdf: 0,
    file: 0,
  };

  attachments.forEach(({ mime_type: mimeType }) => {
    if (mimeType.startsWith("image/")) counts.image += 1;
    else if (mimeType.startsWith("audio/")) counts.audio += 1;
    else if (mimeType.startsWith("video/")) counts.video += 1;
    else if (mimeType === "application/pdf") counts.pdf += 1;
    else counts.file += 1;
  });

  return [
    counts.image ? `사진 ${counts.image}장` : "",
    counts.audio ? `음성 ${counts.audio}개` : "",
    counts.video ? `동영상 ${counts.video}개` : "",
    counts.pdf ? `PDF ${counts.pdf}개` : "",
    counts.file ? `파일 ${counts.file}개` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

export function messageSummary(
  body: string,
  attachments: PresentableAttachment[],
) {
  return messageDisplayBody(body, attachments).trim() || attachmentSummary(attachments);
}
