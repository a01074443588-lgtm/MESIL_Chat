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
  disabled: "서버 준비 중",
  "permission-denied": "휴대전화 설정에서 알림 허용 필요",
  ready: "알림을 켜 주세요",
  active: "사용 중",
};

export function NotificationSoundPanel({
  mode,
  onModeChanged,
  onClose,
}: {
  mode: NotificationSoundMode;
  onModeChanged: (mode: NotificationSoundMode) => void;
  onClose: () => void;
}) {
  const [feedback, setFeedback] = useState("");
  const [pushState, setPushState] = useState<PushSupportState>("checking");
  const [pushConfig, setPushConfig] = useState<PushConfig | null>(null);
  const [pushBusy, setPushBusy] = useState(false);
  const pushEnvironment = readPushEnvironment();
  const pushStateLabel =
    pushState === "unsupported" && pushEnvironment.requiresHomeScreenInstall
      ? "홈 화면 설치 필요"
      : pushState === "unsupported" && pushEnvironment.requiresIOSUpgrade
        ? "iOS 업데이트 필요"
        : pushLabels[pushState];

  useEffect(() => {
    let cancelled = false;
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
  }, []);

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
    if (!pushConfig) return;
    setPushBusy(true);
    setFeedback("");
    try {
      await enableWebPush(pushConfig);
      setPushState("active");
      const testResult = await sendWebPushTest();
      setFeedback(`휴대전화 알림을 켰습니다. ${testResult.message}`);
    } catch (error) {
      const message = error instanceof Error ? error.message : "잠금화면 알림을 켜지 못했습니다.";
      setPushState(Notification.permission === "denied" ? "permission-denied" : "ready");
      setFeedback(message);
    } finally {
      setPushBusy(false);
    }
  }

  async function turnOffPush() {
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
      const result = await sendWebPushTest();
      setFeedback(result.message);
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "시험 알림을 보내지 못했습니다.");
    } finally {
      setPushBusy(false);
    }
  }

  return (
    <div className="security-layer">
      <button className="drawer-backdrop" onClick={onClose} aria-label="알림 설정 닫기" />
      <section className="security-panel notification-sound-panel" aria-label="알림 설정">
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
          <div className="push-setting-actions">
            {pushState === "active" ? (
              <>
                <button
                  className="button button-primary"
                  disabled={pushBusy}
                  onClick={() => void testPush()}
                >
                  시험 알림 보내기
                </button>
                <button
                  className="button button-secondary"
                  disabled={pushBusy}
                  onClick={() => void turnOffPush()}
                >
                  이 기기 알림 끄기
                </button>
              </>
            ) : (
              <button
                className="button button-primary button-large"
                disabled={
                  pushBusy ||
                  pushState === "checking" ||
                  pushState === "unsupported" ||
                  pushState === "disabled" ||
                  pushState === "permission-denied"
                }
                onClick={() => void turnOnPush()}
              >
                휴대전화 알림 켜고 시험하기
              </button>
            )}
          </div>
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
