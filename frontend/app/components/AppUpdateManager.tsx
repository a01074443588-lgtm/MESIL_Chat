"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  APP_UPDATE_CHECK_INTERVAL_MS,
  decideAppUpdateAction,
  isNewAppBuild,
  shouldRunAppUpdateCheck,
} from "../appUpdatePolicy.mjs";

const SERVICE_WORKER_URL = "/mesil-chat-sw-v6.js";
const APP_VERSION_URL = "/app-version.json";
const CURRENT_BUILD_ID = __MESIL_APP_BUILD_ID__;
const RELOAD_GUARD_MS = 60 * 1000;
const LAST_RELOAD_KEY = "mesil-chat:update-reload-at";
const UPDATE_COMPLETE_KEY = "mesil-chat:update-complete";

function hasUnsavedDraft() {
  if (document.querySelector('[data-app-update-dirty="true"]')) return true;

  const textSelectors = [".composer textarea", ".comment-form textarea"];
  const hasText = textSelectors.some((selector) =>
    Array.from(document.querySelectorAll<HTMLTextAreaElement>(selector)).some(
      (field) => field.value.trim().length > 0,
    ),
  );
  return hasText;
}

export function AppUpdateManager() {
  const [updateReady, setUpdateReady] = useState(false);
  const [updateCompleted, setUpdateCompleted] = useState(false);
  const registrationRef = useRef<ServiceWorkerRegistration | null>(null);
  const hadControllerRef = useRef(false);
  const reloadStartedRef = useRef(false);
  const lastCheckedAtRef = useRef(0);
  const updateCheckInFlightRef = useRef(false);

  const reloadWithNewVersion = useCallback((allowDraftLoss = false) => {
    if (reloadStartedRef.current) return;

    const draftPresent = hasUnsavedDraft();
    const confirmed =
      draftPresent && allowDraftLoss
        ? window.confirm(
            "작성 중인 내용이 있습니다. 지금 업데이트하면 작성 중인 내용이 사라질 수 있습니다. 계속할까요?",
          )
        : false;
    const action = decideAppUpdateAction(
      draftPresent,
      allowDraftLoss,
      confirmed,
    );
    if (action === "defer") {
      setUpdateReady(true);
      return;
    }
    if (action === "cancel") return;

    const lastReloadAt = Number(window.sessionStorage.getItem(LAST_RELOAD_KEY) || 0);
    if (Date.now() - lastReloadAt < RELOAD_GUARD_MS) {
      setUpdateReady(false);
      return;
    }

    reloadStartedRef.current = true;
    window.sessionStorage.setItem(LAST_RELOAD_KEY, String(Date.now()));
    window.sessionStorage.setItem(UPDATE_COMPLETE_KEY, "1");
    window.setTimeout(() => {
      reloadStartedRef.current = false;
      window.sessionStorage.removeItem(LAST_RELOAD_KEY);
      window.sessionStorage.removeItem(UPDATE_COMPLETE_KEY);
    }, 2000);
    window.location.reload();
  }, []);

  const checkAppBuild = useCallback(async () => {
    const response = await fetch(APP_VERSION_URL, {
      cache: "no-store",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) return;
    const payload = (await response.json()) as { build_id?: unknown };
    if (isNewAppBuild(CURRENT_BUILD_ID, payload.build_id)) {
      reloadWithNewVersion();
    }
  }, [reloadWithNewVersion]);

  const checkForUpdate = useCallback(async (force = false) => {
    const now = Date.now();
    if (
      updateCheckInFlightRef.current ||
      !shouldRunAppUpdateCheck(lastCheckedAtRef.current, now, force)
    ) {
      return;
    }
    lastCheckedAtRef.current = now;
    updateCheckInFlightRef.current = true;
    try {
      const registration = registrationRef.current;
      await Promise.all([
        checkAppBuild().catch(() => undefined),
        registration?.update().catch(() => undefined) ?? Promise.resolve(),
      ]);
    } finally {
      updateCheckInFlightRef.current = false;
    }
  }, [checkAppBuild]);

  useEffect(() => {
    if (typeof window === "undefined") return;

    const supportsServiceWorker =
      window.isSecureContext && "serviceWorker" in navigator;

    let completionNoticeId: number | undefined;
    if (window.sessionStorage.getItem(UPDATE_COMPLETE_KEY) === "1") {
      window.sessionStorage.removeItem(UPDATE_COMPLETE_KEY);
      completionNoticeId = window.setTimeout(() => setUpdateCompleted(true), 0);
    }

    let disposed = false;
    let removeUpdateFoundListener: (() => void) | undefined;
    hadControllerRef.current = Boolean(
      supportsServiceWorker && navigator.serviceWorker.controller,
    );

    const handleControllerChange = () => {
      if (disposed) return;
      const hadController = hadControllerRef.current;
      hadControllerRef.current = true;
      if (!hadController) return;
      reloadWithNewVersion();
    };

    if (supportsServiceWorker) {
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
    }

    const checkWhenActive = () => {
      if (document.visibilityState === "visible") {
        void checkForUpdate(true);
      }
    };
    const intervalId = window.setInterval(
      () => void checkForUpdate(),
      APP_UPDATE_CHECK_INTERVAL_MS,
    );

    void checkForUpdate(true);

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
      if (supportsServiceWorker) {
        navigator.serviceWorker.removeEventListener(
          "controllerchange",
          handleControllerChange,
        );
      }
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
