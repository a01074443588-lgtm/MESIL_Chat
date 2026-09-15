import { registerPlugin } from "@capacitor/core";
import { apiFetch } from "./api";
import { isNativeMesilApp } from "./nativeShare";


type NativePushRegistration = {
  installationId: string;
  token: string;
  appVersion: string;
  fullScreenIntentAllowed: boolean;
  microphoneAllowed: boolean;
  cameraAllowed: boolean;
};

type NativePushResult = {
  enabled: boolean;
  active: boolean;
  message: string;
};

export type NativePushReadiness = "ready" | "check" | "recovering" | "failed";
export const NATIVE_PUSH_READINESS_EVENT = "mesil-native-push-readiness";

export type NativePushPermissionStatus = {
  notificationPermission: "granted" | "denied" | "prompt";
  fullScreenIntentAllowed: boolean;
};

export type PendingNativeCallAction = {
  callId: string;
  action: "accept";
  expiresAt: number;
};

type MesilPushPlugin = {
  register(): Promise<NativePushRegistration>;
  getPermissionStatus(): Promise<NativePushPermissionStatus>;
  getPendingCallAction(): Promise<Partial<PendingNativeCallAction>>;
  clearPendingCallAction(options: { callId: string }): Promise<void>;
  acknowledgeWebCall(options: { callId: string }): Promise<void>;
  markRegistrationSynchronized(): Promise<void>;
  openNotificationSettings(): Promise<void>;
  openFullScreenIntentSettings(): Promise<void>;
  setCallScreenActive(options: { active: boolean }): Promise<void>;
  setCallAudioRoute(options: {
    route: "speaker" | "earpiece" | "system";
  }): Promise<{ route: string; applied: boolean }>;
};

const mesilPush = registerPlugin<MesilPushPlugin>("MesilPush");
let registrationFlight: Promise<(NativePushResult & { fullScreenIntentAllowed: boolean }) | null> | null = null;

function publishReadiness(state: NativePushReadiness, message: string) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(NATIVE_PUSH_READINESS_EVENT, {
    detail: { state, message },
  }));
}

export async function readNativePushPermissionStatus() {
  if (!isNativeMesilApp()) return null;
  return mesilPush.getPermissionStatus();
}

export async function openNativeNotificationSettings() {
  if (!isNativeMesilApp()) return;
  await mesilPush.openNotificationSettings();
}

export async function openNativeFullScreenIntentSettings() {
  if (!isNativeMesilApp()) return;
  await mesilPush.openFullScreenIntentSettings();
}

export async function readPendingNativeCallAction() {
  if (!isNativeMesilApp()) return null;
  const result = await mesilPush.getPendingCallAction();
  if (
    !result.callId ||
    result.action !== "accept" ||
    typeof result.expiresAt !== "number" ||
    result.expiresAt <= Date.now()
  ) {
    return null;
  }
  return result as PendingNativeCallAction;
}

export async function clearPendingNativeCallAction(callId: string) {
  if (!isNativeMesilApp() || !callId) return;
  await mesilPush.clearPendingCallAction({ callId });
}

export async function acknowledgeNativeWebCall(callId: string) {
  if (!isNativeMesilApp() || !callId) return;
  await mesilPush.acknowledgeWebCall({ callId });
}

async function performNativePushRegistration() {
  const registration = await mesilPush.register();
  if (
    !registration.installationId ||
    !registration.token ||
    !registration.appVersion
  ) {
    throw new Error("Android 통화 알림 주소를 만들지 못했습니다.");
  }
  const result = await apiFetch<NativePushResult>("/api/mobile-push/devices", {
    method: "POST",
    body: JSON.stringify({
      installation_id: registration.installationId,
      token: registration.token,
      platform: "android",
      app_version: registration.appVersion,
    }),
  });
  if (result.enabled && result.active) {
    await mesilPush.markRegistrationSynchronized();
  }
  return {
    ...result,
    fullScreenIntentAllowed: registration.fullScreenIntentAllowed,
  };
}

export function synchronizeNativePushRegistration() {
  if (!isNativeMesilApp()) return Promise.resolve(null);
  if (registrationFlight) return registrationFlight;
  publishReadiness("recovering", "전화 알림 연결 복구 중");
  registrationFlight = performNativePushRegistration()
    .then((result) => {
      publishReadiness(
        result.active && result.enabled ? "ready" : "check",
        result.active && result.enabled
          ? "전화 수신 준비됨"
          : "전화 알림 설정 확인 필요",
      );
      return result;
    })
    .catch((error) => {
      publishReadiness("failed", "전화 알림 연결 실패 · 다시 확인");
      throw error;
    })
    .finally(() => {
      registrationFlight = null;
    });
  return registrationFlight;
}

export async function setNativeCallScreenActive(active: boolean) {
  if (!isNativeMesilApp()) return;
  await mesilPush.setCallScreenActive({ active });
}

export async function setNativeCallAudioRoute(
  route: "speaker" | "earpiece" | "system",
) {
  if (!isNativeMesilApp()) return null;
  const result = await mesilPush.setCallAudioRoute({ route });
  if (!result.applied) {
    throw new Error(
      route === "earpiece"
        ? "이 기기에는 수화기 통화 장치가 없습니다."
        : "스피커폰으로 바꾸지 못했습니다.",
    );
  }
  return result;
}
