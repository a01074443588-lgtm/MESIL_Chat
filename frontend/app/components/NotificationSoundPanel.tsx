"use client";

import { useEffect, useState } from "react";
import {
  playNotificationTest,
  saveNotificationSoundMode,
  type NotificationSoundMode,
} from "../notificationSound";
import {
  disableWebPush,
  enableWebPush,
  readPushEnvironment,
  readPushStatus,
  sendWebPushTest,
  type PushConfig,
  type PushSupportState,
} from "../pushNotifications";
import {
  NATIVE_PUSH_READINESS_EVENT,
  openNativeFullScreenIntentSettings,
  openNativeNotificationSettings,
  readNativePushPermissionStatus,
  synchronizeNativePushRegistration,
  type NativePushReadiness,
} from "../nativePush";
import { isNativeMesilApp } from "../nativeShare";

const options: Array<{
  value: NotificationSoundMode;
  title: string;
  description: string;
}> = [
  {
    value: "all",
    title: "모든 새 메시지",
    description: "다른 직원이 메시지를 보내면 또렷한 메딕 전용음으로 알려줍니다.",
  },
  {
    value: "important",
    title: "중요한 메시지만",
    description: "공지, 업무지정, 인수인계, 확인요청만 소리로 알려줍니다.",
  },
  {
    value: "off",
    title: "화면을 볼 때 소리 끄기",
    description: "채팅 화면을 보고 있을 때 새 메시지 소리를 내지 않습니다.",
  },
];

const pushLabels: Record<PushSupportState, string> = {
  checking: "확인 중",
  unsupported: "이 기기에서는 사용할 수 없음",
  disabled: "잠금화면 알림 사용 안 함",
  "permission-denied": "휴대전화 설정에서 알림 허용 필요",
  ready: "알림을 켜 주세요",
  active: "사용 중",
};

export function NotificationSoundPanel({
  mode,
  onModeChanged,
  onClose,
  reviewOnly = false,
}: {
  mode: NotificationSoundMode;
  onModeChanged: (mode: NotificationSoundMode) => void;
  onClose: () => void;
  reviewOnly?: boolean;
}) {
  const [feedback, setFeedback] = useState("");
  const [pushState, setPushState] = useState<PushSupportState>("checking");
  const [pushConfig, setPushConfig] = useState<PushConfig | null>(null);
  const [pushBusy, setPushBusy] = useState(false);
  const [fullScreenIntentAllowed, setFullScreenIntentAllowed] = useState<boolean | null>(null);
  const [nativeReadiness, setNativeReadiness] = useState<NativePushReadiness>("recovering");
  const nativeApp = isNativeMesilApp();
  const pushEnvironment = readPushEnvironment();
  const pushStateLabel = nativeApp
    ? nativeReadiness === "ready"
      ? "전화 수신 준비됨"
      : nativeReadiness === "check"
        ? "전화 알림 설정 확인 필요"
        : nativeReadiness === "recovering"
          ? "전화 알림 연결 복구 중"
          : "전화 알림 연결 실패 · 다시 확인"
    : pushState === "unsupported" && pushEnvironment.requiresHomeScreenInstall
      ? "홈 화면 설치 필요"
      : pushState === "unsupported" && pushEnvironment.requiresIOSUpgrade
        ? "iOS 업데이트 필요"
        : pushLabels[pushState];

  useEffect(() => {
    let cancelled = false;
    if (nativeApp) {
      void readNativePushPermissionStatus()
        .then(async (status) => {
          if (cancelled || !status) return;
          setFullScreenIntentAllowed(status.fullScreenIntentAllowed);
          setPushConfig(null);
          if (status.notificationPermission !== "granted") {
            setNativeReadiness("check");
            setPushState(status.notificationPermission === "denied" ? "permission-denied" : "ready");
            return;
          }
          setNativeReadiness("recovering");
          setPushState("checking");
          const result = await synchronizeNativePushRegistration();
          if (cancelled || !result) return;
          setNativeReadiness(result.enabled && result.active ? "ready" : "check");
          setPushState(result.enabled && result.active ? "active" : "disabled");
        })
        .catch(() => {
          if (!cancelled) {
            setNativeReadiness("failed");
            setPushState("disabled");
          }
        });
      return () => {
        cancelled = true;
      };
    }
    void readPushStatus()
      .then(({ state, config }) => {
        if (cancelled) return;
        setPushState(state);
        setPushConfig(config);
      })
      .catch(() => {
        if (!cancelled) setPushState("disabled");
      });
    return () => {
      cancelled = true;
    };
  }, [nativeApp]);

  useEffect(() => {
    if (!nativeApp) return;
    const update = (event: Event) => {
      const detail = (event as CustomEvent<{ state: NativePushReadiness }>).detail;
      if (detail?.state) setNativeReadiness(detail.state);
    };
    window.addEventListener(NATIVE_PUSH_READINESS_EVENT, update);
    return () => window.removeEventListener(NATIVE_PUSH_READINESS_EVENT, update);
  }, [nativeApp]);

  useEffect(() => {
    if (!nativeApp) return;
    const refreshAfterSettings = () => {
      void readNativePushPermissionStatus()
        .then(async (status) => {
          if (!status) return;
          setFullScreenIntentAllowed(status.fullScreenIntentAllowed);
          if (status.notificationPermission !== "granted") {
            setNativeReadiness("check");
            setPushState(status.notificationPermission === "denied" ? "permission-denied" : "ready");
            return;
          }
          setNativeReadiness("recovering");
          const result = await synchronizeNativePushRegistration();
          if (!result) return;
          setNativeReadiness(result.enabled && result.active ? "ready" : "check");
          setPushState(result.enabled && result.active ? "active" : "disabled");
        })
        .catch(() => {
          setNativeReadiness("failed");
          setPushState("disabled");
        });
    };
    window.addEventListener("focus", refreshAfterSettings);
    return () => window.removeEventListener("focus", refreshAfterSettings);
  }, [nativeApp]);

  async function selectMode(nextMode: NotificationSoundMode) {
    saveNotificationSoundMode(nextMode);
    onModeChanged(nextMode);
    if (nextMode === "off") {
      setFeedback("화면을 볼 때 나는 알림 소리를 껐습니다.");
      return;
    }
    const played = await playNotificationTest().catch(() => false);
    setFeedback(
      played
        ? "메딕 전용 시험 소리를 재생했습니다."
        : "브라우저에서 소리를 재생하지 못했습니다. 휴대전화 음량을 확인해 주세요.",
    );
  }

  async function testSound() {
    const played = await playNotificationTest().catch(() => false);
    setFeedback(
      played
        ? "메딕 전용 시험 소리를 재생했습니다."
        : "휴대전화 무음 설정과 브라우저 소리 권한을 확인해 주세요.",
    );
  }

  async function turnOnPush() {
    setPushBusy(true);
    setFeedback("");
    try {
      if (nativeApp) {
        const result = await synchronizeNativePushRegistration();
        if (!result?.active) {
          throw new Error(result?.message || "Android 앱 알림을 연결하지 못했습니다.");
        }
        setPushState("active");
        setNativeReadiness("ready");
        setFullScreenIntentAllowed(result.fullScreenIntentAllowed);
        setFeedback(result.message);
        return;
      }
      if (!pushConfig) return;
      await enableWebPush(pushConfig);
      setPushState("active");
      const testResult = await sendWebPushTest();
      setFeedback(`휴대전화 알림을 켰습니다. ${testResult.message}`);
    } catch (error) {
      const message = error instanceof Error ? error.message : "잠금화면 알림을 켜지 못했습니다.";
      const permissionDenied = nativeApp
        ? message.includes("알림 권한")
        : typeof Notification !== "undefined" && Notification.permission === "denied";
      setPushState(permissionDenied ? "permission-denied" : "ready");
      if (nativeApp) setNativeReadiness(permissionDenied ? "check" : "failed");
      setFeedback(message);
    } finally {
      setPushBusy(false);
    }
  }

  async function turnOffPush() {
    if (nativeApp) {
      await openNotificationSettingsSafely();
      return;
    }
    setPushBusy(true);
    setFeedback("");
    try {
      const result = await disableWebPush();
      setPushState("ready");
      setFeedback(result.message);
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "잠금화면 알림을 끄지 못했습니다.");
    } finally {
      setPushBusy(false);
    }
  }

  async function testPush() {
    setPushBusy(true);
    setFeedback("");
    try {
      if (nativeApp) {
        const result = await synchronizeNativePushRegistration();
        if (!result?.active) {
          throw new Error(result?.message || "Android 앱 알림 연결을 확인하지 못했습니다.");
        }
        setFullScreenIntentAllowed(result.fullScreenIntentAllowed);
        setNativeReadiness("ready");
        setFeedback("Android 앱 알림 연결을 다시 확인했습니다.");
        return;
      }
      const result = await sendWebPushTest();
      setFeedback(result.message);
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "시험 알림을 보내지 못했습니다.");
    } finally {
      setPushBusy(false);
    }
  }

  async function openNotificationSettingsSafely() {
    setPushBusy(true);
    setFeedback("");
    try {
      await openNativeNotificationSettings();
      setFeedback("휴대전화 설정에서 MESIL_Chat 알림을 허용한 뒤 앱으로 돌아와 주세요.");
    } catch (error) {
      setFeedback(
        error instanceof Error ? error.message : "휴대전화 알림 설정을 열지 못했습니다.",
      );
    } finally {
      setPushBusy(false);
    }
  }

  async function openFullScreenIntentSettingsSafely() {
    setPushBusy(true);
    setFeedback("");
    try {
      await openNativeFullScreenIntentSettings();
      setFeedback("휴대전화 설정에서 전체 화면 알림을 허용한 뒤 앱으로 돌아와 주세요.");
    } catch (error) {
      setFeedback(
        error instanceof Error ? error.message : "통화 알림 설정을 열지 못했습니다.",
      );
    } finally {
      setPushBusy(false);
    }
  }

  return (
    <div className="security-layer">
      <button className="drawer-backdrop" onClick={onClose} aria-label="알림 설정 닫기" />
      <section
        className="security-panel notification-sound-panel desktop-resizable-dialog"
        aria-label="알림 설정"
      >
        <header className="security-header">
          <div>
            <span className="eyebrow">내 기기</span>
            <h2>알림 설정</h2>
            <p>화면이 꺼졌을 때의 알림과 채팅 화면의 소리를 따로 설정합니다.</p>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="닫기">
            ×
          </button>
        </header>
        {reviewOnly ? (
          <p className="mentor-review-lock-note" role="note">
            현재 기기의 알림 지원 상태와 소리 선택 구조를 검토할 수 있습니다. 푸시 등록과 설정 변경은 멘토 계정에서 잠겨 있습니다.
          </p>
        ) : null}

        <section className="push-setting-card">
          <div className="push-setting-heading">
            <div>
              <strong>화면이 꺼졌을 때도 알림 받기</strong>
              <small>새 메시지를 휴대전화 잠금화면에 바로 알려줍니다.</small>
            </div>
            <span className={`push-state ${pushState}`}>{pushStateLabel}</span>
          </div>
          {pushState === "unsupported" && pushEnvironment.requiresHomeScreenInstall ? (
            <div className="push-guidance" role="status">
              <strong>아이폰에서는 홈 화면의 MESIL_Chat 앱으로 열어 주세요.</strong>
              <ol>
                <li>Safari 하단의 공유 버튼을 누릅니다.</li>
                <li>‘홈 화면에 추가’를 선택합니다.</li>
                <li>Safari를 닫고 홈 화면의 MESIL_Chat 아이콘으로 다시 엽니다.</li>
              </ol>
            </div>
          ) : null}
          {pushState === "unsupported" && pushEnvironment.requiresIOSUpgrade ? (
            <div className="push-guidance push-guidance-warning" role="status">
              <strong>아이폰 알림은 iOS 16.4 이상에서 사용할 수 있습니다.</strong>
              <p>아이폰 설정에서 소프트웨어 업데이트를 확인해 주세요.</p>
            </div>
          ) : null}
          {pushState === "unsupported" &&
          !pushEnvironment.requiresHomeScreenInstall &&
          !pushEnvironment.requiresIOSUpgrade ? (
            <div className="push-guidance push-guidance-warning" role="status">
              <strong>현재 브라우저에서는 잠금화면 알림을 지원하지 않습니다.</strong>
              <p>최신 Safari 또는 Chrome에서 다시 확인해 주세요.</p>
            </div>
          ) : null}
          {pushState === "disabled" ? (
            <div className="push-guidance push-guidance-warning" role="status">
              <strong>이 발표환경에서는 잠금화면 알림을 제공하지 않습니다.</strong>
              <p>
                채팅 화면을 보고 있을 때 나는 메딕 소리는 아래에서 선택하고 시험할 수
                있습니다.
              </p>
            </div>
          ) : null}
          {nativeApp && pushState === "permission-denied" ? (
            <div className="push-guidance push-guidance-warning" role="status">
              <strong>MESIL_Chat 알림이 꺼져 있습니다.</strong>
              <p>휴대전화 설정에서 알림을 허용한 뒤 앱으로 돌아와 주세요.</p>
            </div>
          ) : null}
          <div className="push-setting-actions">
            {nativeApp && pushState === "permission-denied" ? (
              <button
                className="button button-primary button-large"
                disabled={pushBusy || reviewOnly}
                onClick={() => void openNotificationSettingsSafely()}
              >
                휴대전화 알림 설정 열기
              </button>
            ) : pushState === "active" ? (
              <>
                <button
                  className="button button-primary"
                  disabled={pushBusy || reviewOnly}
                  onClick={() => void testPush()}
                >
                  {nativeApp ? "연결 상태 다시 확인" : "시험 알림 보내기"}
                </button>
                <button
                  className="button button-secondary"
                  disabled={pushBusy || reviewOnly}
                  onClick={() => void turnOffPush()}
                >
                  {nativeApp ? "휴대전화 알림 설정 열기" : "이 기기 알림 끄기"}
                </button>
              </>
            ) : pushState === "disabled" ? null : (
              <button
                className="button button-primary button-large"
                disabled={
                  pushBusy ||
                  reviewOnly ||
                  pushState === "checking" ||
                  pushState === "unsupported"
                }
                onClick={() => void turnOnPush()}
              >
                {nativeApp ? "Android 앱 알림 켜기" : "휴대전화 알림 켜고 시험하기"}
              </button>
            )}
          </div>
          {nativeApp ? (
            <div
              className={`push-guidance ${
                fullScreenIntentAllowed === false ? "push-guidance-warning" : ""
              }`}
              role="status"
            >
              <div className="push-setting-heading">
                <div>
                  <strong>전화가 오면 바로 받기</strong>
                  <small>휴대전화가 잠겨 있어도 수신 화면에서 바로 받거나 거절합니다.</small>
                </div>
                <span className={`push-state ${fullScreenIntentAllowed ? "active" : "ready"}`}>
                  {fullScreenIntentAllowed === null
                    ? "확인 중"
                    : fullScreenIntentAllowed
                      ? "사용 중"
                      : "설정 필요"}
                </span>
              </div>
              {fullScreenIntentAllowed === false ? (
                <button
                  className="button button-primary button-large"
                  disabled={pushBusy || reviewOnly}
                  onClick={() => void openFullScreenIntentSettingsSafely()}
                >
                  통화 알림 켜기
                </button>
              ) : null}
            </div>
          ) : null}
          <p>
            잠금화면에는 개인정보를 표시하지 않고 “새 메시지가 도착했습니다”만 보여줍니다.
            알림음과 진동은 휴대전화의 MESIL_Chat 알림 설정을 따릅니다.
          </p>
        </section>

        <section className="foreground-sound-section">
          <div className="section-heading">
            <div>
              <strong>채팅 화면을 볼 때 나는 소리</strong>
              <p>크고 또렷한 MESIL_Chat 전용 “메딕” 소리로 재생합니다.</p>
            </div>
          </div>
          <div className="sound-option-list">
            {options.map((option) => (
              <button
                className={`sound-option ${mode === option.value ? "active" : ""}`}
                key={option.value}
                disabled={reviewOnly}
                onClick={() => void selectMode(option.value)}
                aria-pressed={mode === option.value}
              >
                <span className="sound-option-check" aria-hidden="true">
                  {mode === option.value ? "✓" : ""}
                </span>
                <span>
                  <strong>{option.title}</strong>
                  <small>{option.description}</small>
                </span>
              </button>
            ))}
          </div>
          <button className="button button-secondary button-large" onClick={() => void testSound()}>
            메딕 시험 소리 듣기
          </button>
        </section>
        {feedback ? <p className="form-success">{feedback}</p> : null}
      </section>
    </div>
  );
}
