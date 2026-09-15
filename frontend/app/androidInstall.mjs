const APK_FILE_PATTERN = /^MESIL_Chat-[A-Za-z0-9._-]+-staff-test\.apk$/;
const SHA256_PATTERN = /^[a-fA-F0-9]{64}$/;

export function isAndroidWebInstallCandidate(userAgent, isNativeApp) {
  return !isNativeApp && /Android/i.test(String(userAgent ?? ""));
}

export function parseStaffReleaseMetadata(input) {
  if (!input || typeof input !== "object") return null;

  const filename = String(input.filename ?? "");
  const versionName = String(input.version_name ?? "");
  const versionCode = Number(input.version_code);
  const bytes = Number(input.bytes);
  const sha256 = String(input.sha256 ?? "").toLowerCase();

  if (
    !APK_FILE_PATTERN.test(filename) ||
    !versionName ||
    !Number.isSafeInteger(versionCode) ||
    versionCode <= 0 ||
    !Number.isSafeInteger(bytes) ||
    bytes <= 0 ||
    !SHA256_PATTERN.test(sha256)
  ) {
    return null;
  }

  return {
    filename,
    version_name: versionName,
    version_code: versionCode,
    bytes,
    sha256,
    download_url: `/downloads/${encodeURIComponent(filename)}`,
  };
}
