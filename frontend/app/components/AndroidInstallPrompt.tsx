"use client";

import { useEffect, useState } from "react";
import {
  isAndroidWebInstallCandidate,
  parseStaffReleaseMetadata,
} from "../androidInstall.mjs";

type StaffReleaseMetadata = {
  filename: string;
  version_name: string;
  version_code: number;
  bytes: number;
  sha256: string;
  download_url: string;
};

function isNativeApp() {
  return Boolean(
    typeof window !== "undefined" &&
      (window as Window & { Capacitor?: { isNativePlatform?: () => boolean } }).Capacitor
        ?.isNativePlatform?.(),
  );
}

function formatMegabytes(bytes: number) {
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
}

export function AndroidInstallPrompt({ compact = false }: { compact?: boolean }) {
  const [release, setRelease] = useState<StaffReleaseMetadata | null>(null);

  useEffect(() => {
    if (!isAndroidWebInstallCandidate(window.navigator.userAgent, isNativeApp())) return;

    const controller = new AbortController();
    void fetch("/downloads/staff-test-release.json", {
      cache: "no-store",
      signal: controller.signal,
    })
      .then((response) => (response.ok ? response.json() : null))
      .then((payload) => setRelease(parseStaffReleaseMetadata(payload)))
      .catch(() => undefined);

    return () => controller.abort();
  }, []);

  if (!release) return null;

  return (
    <details className={`android-install-prompt ${compact ? "compact" : ""}`}>
      <summary>Android 앱 설치</summary>
      <div>
        <p>
          직원 시험용 앱 {release.version_name} · {formatMegabytes(release.bytes)}
        </p>
        <a className="button button-secondary" href={release.download_url} download>
          APK 내려받기
        </a>
        <small>파일을 누른 뒤 휴대전화의 안내에 따라 설치해 주세요. 자동으로 설치되지 않습니다.</small>
        <small className="android-install-checksum">
          확인코드 {release.sha256.slice(0, 12)}…
        </small>
      </div>
    </details>
  );
}
