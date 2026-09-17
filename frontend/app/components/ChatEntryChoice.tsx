"use client";

import { useEffect, useState } from "react";
import {
  CHAT_WEB_ENTRY_SESSION_KEY,
  loadChatEntryRelease,
  resolveChatEntryPlatform,
} from "../chatEntry.mjs";
import { parseStaffReleaseMetadata } from "../androidInstall.mjs";

type EntryPlatform = "android" | "web";

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

export function ChatEntryChoice({
  onContinueWeb,
  apkDownloadsEnabled = false,
}: {
  onContinueWeb: () => void;
  apkDownloadsEnabled?: boolean;
}) {
  const [platform, setPlatform] = useState<EntryPlatform | null>(null);
  const [release, setRelease] = useState<StaffReleaseMetadata | null>(null);
  const [releaseChecked, setReleaseChecked] = useState(false);
  const [webAddress, setWebAddress] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    window.queueMicrotask(() => {
      if (!active) return;

      let webEntrySelected = false;
      try {
        webEntrySelected =
          window.sessionStorage.getItem(CHAT_WEB_ENTRY_SESSION_KEY) === "1";
      } catch {
        // 브라우저 저장소를 사용할 수 없어도 현재 화면에서 계속 선택할 수 있습니다.
      }

      const nextPlatform = resolveChatEntryPlatform(
        window.navigator.userAgent,
        isNativeApp(),
      );
      if (!apkDownloadsEnabled || nextPlatform === "native" || webEntrySelected) {
        onContinueWeb();
        return;
      }

      setPlatform(nextPlatform);
      setWebAddress(window.location.origin);
      void loadChatEntryRelease(
        apkDownloadsEnabled,
        window.fetch.bind(window),
        controller.signal,
      )
        .then((payload) => {
          if (active) setRelease(parseStaffReleaseMetadata(payload));
        })
        .catch(() => undefined)
        .finally(() => {
          if (active) setReleaseChecked(true);
        });
    });

    return () => {
      active = false;
      controller.abort();
    };
  }, [apkDownloadsEnabled, onContinueWeb]);

  function continueInBrowser() {
    try {
      window.sessionStorage.setItem(CHAT_WEB_ENTRY_SESSION_KEY, "1");
    } catch {
      // 저장되지 않아도 현재 로그인은 계속 진행합니다.
    }
    onContinueWeb();
  }

  if (!platform) {
    return (
      <main className="login-page">
        <p className="chat-entry-loading" role="status">
          접속 방법을 확인하고 있습니다…
        </p>
      </main>
    );
  }

  const webOption = (
    <section className={`chat-entry-option ${platform === "web" ? "recommended" : ""}`}>
      <div>
        <span className="chat-entry-tag">PC·아이폰·앱을 설치하지 않을 때</span>
        <h2>설치 없이 웹으로 사용</h2>
        <p>현재 브라우저에서 바로 로그인합니다.</p>
        {webAddress ? <small>{webAddress}</small> : null}
      </div>
      <button
        className={`button ${platform === "web" ? "button-primary" : "button-secondary"}`}
        type="button"
        onClick={continueInBrowser}
      >
        웹으로 로그인
      </button>
    </section>
  );

  const androidOption = (
    <section className={`chat-entry-option ${platform === "android" ? "recommended" : ""}`}>
      <div>
        <span className="chat-entry-tag">Android 휴대전화·태블릿</span>
        <h2>Android 통화 알림 앱 받기</h2>
        <p>휴대전화 잠금 상태에서도 통화 알림을 받으려면 이 앱을 사용하세요.</p>
        {release ? (
          <small>
            직원 시험용 {release.version_name} · {formatMegabytes(release.bytes)}
          </small>
        ) : null}
      </div>
      {release ? (
        <a
          className={`button ${platform === "android" ? "button-primary" : "button-secondary"}`}
          href={release.download_url}
          download
        >
          APK 바로 내려받기
        </a>
      ) : (
        <button className="button button-secondary" type="button" disabled>
          {releaseChecked ? "앱 파일을 확인하지 못했습니다" : "앱 파일 확인 중…"}
        </button>
      )}
    </section>
  );

  return (
    <main className="login-page">
      <section className="login-card chat-entry-choice" aria-labelledby="chat-entry-title">
        <div className="login-brand">
          <span className="brand-mark" aria-hidden="true">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/brand/silvermedical-logo.jpg" alt="" />
          </span>
          <div>
            <strong>MESIL_Chat</strong>
            <small>메실채팅</small>
          </div>
        </div>
        <div className="login-heading">
          <span className="eyebrow">직원 전용</span>
          <h1 id="chat-entry-title">사용할 방법을 선택해 주세요</h1>
          <p>Android 앱과 브라우저 바로가기를 구분해 한 가지만 사용합니다.</p>
        </div>
        <div className="chat-entry-options">
          {platform === "android" ? (
            <>
              {androidOption}
              {webOption}
            </>
          ) : (
            <>
              {webOption}
              {androidOption}
            </>
          )}
        </div>
        <p className="chat-entry-help">
          Android 앱을 받는 경우 브라우저의 ‘홈 화면에 설치’를 먼저 누르지 마세요. 휴대전화의
          보안 안내에 따라 직원 배포 앱 설치를 허용해 주세요.
        </p>
      </section>
      <p className="privacy-note">업무에 필요한 최소한의 어르신 정보만 입력해 주세요.</p>
    </main>
  );
}
