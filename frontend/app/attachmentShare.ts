export type PreparedAttachmentShare = {
  file: File;
  nativeShareSupported: boolean;
};

export type AttachmentShareResult = "shared" | "cancelled";

function shareMimeType(name: string, declaredType: string) {
  const extension = name.toLowerCase().match(/\.[^.]+$/)?.[0] ?? "";
  if (extension === ".m4a") return "audio/x-m4a";
  if (extension === ".wav") return "audio/wav";
  if (extension === ".weba") return "audio/webm";
  if (extension === ".pdf") return "application/pdf";
  if (extension === ".xlsx") return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
  if (extension === ".xls") return "application/vnd.ms-excel";
  if (extension === ".hwp") return "application/vnd.hancom.hwp";
  if (extension === ".hwpx") return "application/vnd.hancom.hwpx";
  if (extension === ".docx") return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
  if (extension === ".doc") return "application/msword";
  if (extension === ".pptx") return "application/vnd.openxmlformats-officedocument.presentationml.presentation";
  if (extension === ".ppt") return "application/vnd.ms-powerpoint";
  if (extension === ".txt") return "text/plain";
  if (extension === ".csv") return "text/csv";
  return declaredType || "application/octet-stream";
}

function nativeShareSupported(file: File) {
  try {
    return (
      typeof navigator.share === "function" &&
      typeof navigator.canShare === "function" &&
      navigator.canShare({ files: [file] })
    );
  } catch {
    return false;
  }
}

export function hasActiveShareGesture() {
  const activation = (
    navigator as Navigator & { userActivation?: { isActive: boolean } }
  ).userActivation;
  return !activation || activation.isActive;
}

export async function prepareAttachmentShare({
  url,
  name,
  mimeType,
}: {
  url: string;
  name: string;
  mimeType: string;
}): Promise<PreparedAttachmentShare> {
  const response = await fetch(url, {
    credentials: "include",
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error("공유할 파일을 불러오지 못했습니다.");
  }

  const blob = await response.blob();
  const file = new File([blob], name, {
    type: shareMimeType(name, mimeType || blob.type),
  });
  return { file, nativeShareSupported: nativeShareSupported(file) };
}

function isShareCancelled(error: unknown) {
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    (error as { name?: unknown }).name === "AbortError"
  );
}

export async function openAttachmentShare(
  prepared: PreparedAttachmentShare,
): Promise<AttachmentShareResult> {
  if (!prepared.nativeShareSupported || typeof navigator.share !== "function") {
    throw new Error("이 기기에서는 다른 앱으로 파일을 공유할 수 없습니다.");
  }
  try {
    await navigator.share({
      title: prepared.file.name,
      files: [prepared.file],
    });
    return "shared";
  } catch (error) {
    if (isShareCancelled(error)) return "cancelled";
    throw new Error("공유창을 열지 못했습니다. ‘공유창 열기’를 다시 눌러 주세요.");
  }
}

export async function savePreparedAttachment(prepared: PreparedAttachmentShare) {
  const { Capacitor, registerPlugin } = await import("@capacitor/core");
  if (Capacitor.isNativePlatform()) {
    const plugin = registerPlugin<{ saveDownload(options: { name: string; base64: string }): Promise<{ saved: boolean; bytes: number }> }>("MesilShareReceiver");
    const base64 = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",", 2)[1] ?? "");
      reader.onerror = () => reject(new Error("원본 파일을 읽지 못했습니다."));
      reader.readAsDataURL(prepared.file);
    });
    const result = await plugin.saveDownload({ name: prepared.file.name, base64 }).catch(() => {
      throw new Error("파일 저장을 지원하는 최신 앱인지 확인해 주세요. 저장되지 않았다면 관리자에게 알려 주세요.");
    });
    if (!result.saved || result.bytes !== prepared.file.size) throw new Error("원본 파일 저장을 확인하지 못했습니다.");
    return;
  }
  const objectUrl = URL.createObjectURL(prepared.file);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = prepared.file.name;
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}
