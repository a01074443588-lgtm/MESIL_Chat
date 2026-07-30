"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const SERVICE_WORKER_URL = "/mesil-chat-sw-v6.js";
const UPDATE_CHECK_INTERVAL_MS = 30 * 60 * 1000;
const RELOAD_GUARD_MS = 60 * 1000;
const LAST_RELOAD_KEY = "mesil-chat:update-reload-at";
const UPDATE_COMPLETE_KEY = "mesil-chat:update-complete";

function hasUnsavedDraft() {
  const textSelectors = [".composer textarea", ".comment-form textarea"];
  const hasText = textSelectors.some((selector) =>
    Array.from(document.querySelectorAll<HTMLTextAreaElement>(selector)).some(
      (field) => field.value.trim().length > 0,
    ),
  );
  if (hasText) return true;

  return Array.from(
    document.querySelectorAll<HTMLInputElement>('.composer input[type="file"]'),
  ).some((field) => Boolean(field.files?.length));
}

export function AppUpdateManager() {
  const [updateReady, setUpdateReady] = useState(false);
  const [updateCompleted, setUpdateCompleted] = useState(false);
  const registrationRef = useRef<ServiceWorkerRegistration | null>(null);
  const hadControllerRef = useRef(false);
  const reloadStartedRef = useRef(false);
  const lastCheckedAtRef = useRef(0);

  const reloadWithNewVersion = useCallback((allowDraftLoss = false) => {
    if (reloadStartedRef.current) return;

    if (hasUnsavedDraft()) {
      if (!allowDraftLoss) {
        setUpdateReady(true);
        return;
      }
      const confirmed = window.confirm(
        "작성 중인 내용이 있습니다. 지금 업데이트하면 작성 중인 내용이 사라질 수 있습니다. 계속할까요?",
      );
      if (!confirmed) return;
    }

    const lastReloadAt = Number(window.sessionStorage.getItem(LAST_RELOAD_KEY) || 0);
    if (Date.now() - lastReloadAt < RELOAD_GUARD_MS) {
      setUpdateReady(false);
      return;
    }

    reloadStartedRef.current = true;
    window.sessionStorage.setItem(LAST_RELOAD_KEY, String(Date.now()));
    window.sessionStorage.setItem(UPDATE_COMPLETE_KEY, "1");
    window.location.reload();
  }, []);

  const checkForUpdate = useCallback(async (force = false) => {
    const registration = registrationRef.current;
    if (!registration) return;

    const now = Date.now();
    if (!force && now - lastCheckedAtRef.current < UPDATE_CHECK_INTERVAL_MS) {
      return;
    }
    lastCheckedAtRef.current = now;
    await registration.update().catch(() => undefined);
  }, []);

  useEffect(() => {
    if (
      typeof window === "undefined" ||
      !window.isSecureContext ||
      !("serviceWorker" in navigator)
    ) {
      return;
    }

    let completionNoticeId: number | undefined;
    if (window.sessionStorage.getItem(UPDATE_COMPLETE_KEY) === "1") {
      window.sessionStorage.removeItem(UPDATE_COMPLETE_KEY);
      completionNoticeId = window.setTimeout(() => setUpdateCompleted(true), 0);
    }

    let disposed = false;
    let removeUpdateFoundListener: (() => void) | undefined;
    hadControllerRef.current = Boolean(navigator.serviceWorker.controller);

    const handleControllerChange = () => {
      if (disposed) return;
      const hadController = hadControllerRef.current;
      hadControllerRef.current = true;
      if (!hadController) return;
      reloadWithNewVersion();
    };

    navigator.serviceWorker.addEventListener(
      "controllerchange",
      handleControllerChange,
    );

    void navigator.serviceWorker
      .register(SERVICE_WORKER_URL, {
        scope: "/",
        updateViaCache: "none",
      })
      .then((registration) => {
        if (disposed) return;
        registrationRef.current = registration;

        const handleUpdateFound = () => {
          const worker = registration.installing;
          if (!worker) return;
          const handleStateChange = () => {
            if (
              worker.state === "installed" &&
              Boolean(navigator.serviceWorker.controller) &&
              registration.waiting
            ) {
              setUpdateReady(true);
            }
          };
          worker.addEventListener("statechange", handleStateChange);
        };

        registration.addEventListener("updatefound", handleUpdateFound);
        removeUpdateFoundListener = () =>
          registration.removeEventListener("updatefound", handleUpdateFound);

        if (registration.waiting && navigator.serviceWorker.controller) {
          setUpdateReady(true);
        }
        void checkForUpdate(true);
      })
      .catch(() => undefined);

    const checkWhenActive = () => {
      if (document.visibilityState === "visible") {
        void checkForUpdate();
      }
    };
    const intervalId = window.setInterval(
      () => void checkForUpdate(),
      UPDATE_CHECK_INTERVAL_MS,
    );

    window.addEventListener("focus", checkWhenActive);
    window.addEventListener("pageshow", checkWhenActive);
    window.addEventListener("online", checkWhenActive);
    document.addEventListener("visibilitychange", checkWhenActive);

    return () => {
      disposed = true;
      if (completionNoticeId !== undefined) {
        window.clearTimeout(completionNoticeId);
      }
      window.clearInterval(intervalId);
      removeUpdateFoundListener?.();
      navigator.serviceWorker.removeEventListener(
        "controllerchange",
        handleControllerChange,
      );
      window.removeEventListener("focus", checkWhenActive);
      window.removeEventListener("pageshow", checkWhenActive);
      window.removeEventListener("online", checkWhenActive);
      document.removeEventListener("visibilitychange", checkWhenActive);
    };
  }, [checkForUpdate, reloadWithNewVersion]);

  useEffect(() => {
    if (!updateCompleted) return;
    const timeoutId = window.setTimeout(() => setUpdateCompleted(false), 4500);
    return () => window.clearTimeout(timeoutId);
  }, [updateCompleted]);

  function applyUpdate() {
    const waitingWorker = registrationRef.current?.waiting;
    if (waitingWorker) {
      waitingWorker.postMessage({ type: "SKIP_WAITING" });
      window.setTimeout(() => reloadWithNewVersion(true), 1200);
      return;
    }
    reloadWithNewVersion(true);
  }

  return (
    <>
      {updateReady ? (
        <aside
          className="app-update-banner"
          role="status"
          aria-live="polite"
          aria-label="앱 업데이트 안내"
        >
          <div>
            <strong>새 버전이 준비되었습니다.</strong>
            <span>작성 중인 내용을 확인한 뒤 업데이트해 주세요.</span>
          </div>
          <button type="button" onClick={applyUpdate}>
            지금 업데이트
          </button>
        </aside>
      ) : null}
      {updateCompleted ? (
        <div className="app-update-complete" role="status" aria-live="polite">
          최신 버전으로 업데이트되었습니다.
        </div>
      ) : null}
    </>
  );
}
