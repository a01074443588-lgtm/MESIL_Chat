export const CHAT_ATTACHMENT_ACCEPT = [
  "image/jpeg", "image/png", "image/webp",
  "audio/mpeg", "audio/wav", "audio/x-wav", "audio/mp4", "audio/x-m4a",
  "audio/webm", "audio/ogg", "audio/aac",
  "video/mp4", "video/webm", "video/quicktime", "application/pdf",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.ms-excel", "application/vnd.hancom.hwp",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/msword",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  "application/vnd.ms-powerpoint", "text/plain", "text/csv",
  ".jpg", ".jpeg", ".png", ".webp", ".mp3", ".wav", ".m4a", ".aac",
  ".ogg", ".webm", ".mp4", ".mov", ".pdf", ".xlsx", ".xls", ".hwp",
  ".hwpx", ".docx", ".doc", ".pptx", ".ppt", ".txt", ".csv",
].join(",");

export const SUPPORTED_ATTACHMENT_GUIDE =
  "지원: 사진·음성·동영상·PDF · Excel(XLSX·XLS) · 한글(HWP·HWPX) · Word(DOCX·DOC) · PowerPoint(PPTX·PPT) · TXT·CSV";

const SUPPORTED_EXTENSIONS = new Set([
  ".jpg", ".jpeg", ".png", ".webp", ".mp3", ".wav", ".m4a", ".aac",
  ".ogg", ".webm", ".mp4", ".mov", ".pdf", ".xlsx", ".xls", ".hwp",
  ".hwpx", ".docx", ".doc", ".pptx", ".ppt", ".txt", ".csv",
]);

const DISGUISE_EXTENSIONS = new Set([
  ".exe", ".com", ".bat", ".cmd", ".ps1", ".js", ".jse", ".vbs",
  ".vbe", ".wsf", ".sh", ".py", ".php", ".jar", ".msi", ".scr",
  ".dll", ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz",
  ".html", ".htm", ".svg", ".xlsm", ".docm", ".pptm",
  ...SUPPORTED_EXTENSIONS,
]);

const DOCUMENT_INFO: Record<string, { label: string; icon: string }> = {
  ".xlsx": { label: "Excel 문서", icon: "X" },
  ".xls": { label: "Excel 문서", icon: "X" },
  ".hwp": { label: "한글 문서", icon: "한" },
  ".hwpx": { label: "한글 문서", icon: "한" },
  ".docx": { label: "Word 문서", icon: "W" },
  ".doc": { label: "Word 문서", icon: "W" },
  ".pptx": { label: "PowerPoint 문서", icon: "P" },
  ".ppt": { label: "PowerPoint 문서", icon: "P" },
  ".txt": { label: "텍스트 문서", icon: "T" },
  ".csv": { label: "CSV 문서", icon: "C" },
};

export function attachmentExtension(name: string) {
  const match = name.toLocaleLowerCase("en-US").match(/\.[^.\s]+$/);
  return match?.[0] ?? "";
}

export function attachmentNameError(name: string) {
  const lowered = name.toLocaleLowerCase("en-US");
  const suffixes = lowered.match(/\.[^.\s]+/g) ?? [];
  const extension = suffixes.at(-1) ?? "";
  if ([".xlsm", ".docm", ".pptm"].includes(extension)) {
    return "매크로 문서(XLSM·DOCM·PPTM)는 첨부할 수 없습니다.";
  }
  if (!SUPPORTED_EXTENSIONS.has(extension)) {
    return `지원하지 않는 파일입니다. ${SUPPORTED_ATTACHMENT_GUIDE}`;
  }
  if (suffixes.slice(0, -1).some((suffix) => DISGUISE_EXTENSIONS.has(suffix))) {
    return "이중 확장자로 위장된 파일은 첨부할 수 없습니다.";
  }
  return "";
}

export function documentAttachmentInfo(name: string) {
  return DOCUMENT_INFO[attachmentExtension(name)] ?? null;
}
