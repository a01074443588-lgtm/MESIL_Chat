"use client";

import { useEffect, useState } from "react";

type InstallPromptEvent = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
};

export function PwaInstallButton() {
  const [promptEvent, setPromptEvent] = useState<InstallPromptEvent | null>(null);
  const [installed, setInstalled] = useState(false);

  useEffect(() => {
    if ("serviceWorker" in navigator && window.isSecureContext) {
      navigator.serviceWorker
        .register("/mesil-chat-sw-v6.js", {
          scope: "/",
          updateViaCache: "none",
        })
        .catch(() => {
          // 설치 지원이 불가능해도 채팅 자체는 계속 사용할 수 있습니다.
        });
    }
    const onPrompt = (event: Event) => {
      event.preventDefault();
      setPromptEvent(event as InstallPromptEvent);
    };
    const onInstalled = () => {
      setInstalled(true);
      setPromptEvent(null);
    };
    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);

  if (installed) {
    return <span className="install-complete">홈 화면 설치 완료</span>;
  }
  if (!promptEvent) return null;

  return (
    <button
      className="button button-secondary install-button"
      type="button"
      onClick={async () => {
        await promptEvent.prompt();
        const choice = await promptEvent.userChoice;
        if (choice.outcome === "accepted") setPromptEvent(null);
      }}
    >
      홈 화면에 설치
    </button>
  );
}
