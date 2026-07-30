import { apiFetch } from "./api";

export type PushConfig = {
  enabled: boolean;
  public_key: string | null;
};

export type PushResult = {
  enabled: boolean;
  active: boolean;
  message: string;
};

export type PushSupportState =
  | "checking"
  | "unsupported"
  | "disabled"
  | "permission-denied"
  | "ready"
  | "active";

export type PushEnvironment = {
  isIOS: boolean;
  isStandalone: boolean;
  requiresHomeScreenInstall: boolean;
  requiresIOSUpgrade: boolean;
};

export function readPushEnvironment(): PushEnvironment {
  if (typeof window === "undefined" || typeof navigator === "undefined") {
    return {
      isIOS: false,
      isStandalone: false,
      requiresHomeScreenInstall: false,
      requiresIOSUpgrade: false,
    };
  }

  const userAgent = navigator.userAgent;
  const isIOS =
    /iPhone|iPad|iPod/i.test(userAgent) ||
    (/Macintosh/i.test(userAgent) && navigator.maxTouchPoints > 1);
  const isStandalone =
    window.matchMedia("(display-mode: standalone)").matches ||
    (navigator as Navigator & { standalone?: boolean }).standalone === true;
  const versionMatch = userAgent.match(/OS (\d+)[._](\d+)/i);
  const major = versionMatch ? Number(versionMatch[1]) : null;
  const minor = versionMatch ? Number(versionMatch[2]) : null;
  const requiresIOSUpgrade =
    isIOS &&
    major !== null &&
    minor !== null &&
    (major < 16 || (major === 16 && minor < 4));

  return {
    isIOS,
    isStandalone,
    requiresHomeScreenInstall: isIOS && !isStandalone,
    requiresIOSUpgrade,
  };
}

export function supportsWebPush() {
  return (
    typeof window !== "undefined" &&
    window.isSecureContext &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

export async function ensureMesilServiceWorker() {
  if (!supportsWebPush()) {
    throw new Error("이 휴대전화에서는 잠금화면 알림을 사용할 수 없습니다.");
  }
  const registration = await navigator.serviceWorker.register(
    "/mesil-chat-sw-v6.js",
    { scope: "/", updateViaCache: "none" },
  );
  await registration.update().catch(() => undefined);
  const updatingWorker = registration.installing || registration.waiting;
  if (updatingWorker && updatingWorker.state !== "activated") {
    await new Promise<void>((resolve, reject) => {
      const timeoutId = window.setTimeout(
        () => reject(new Error("새 알림 기능을 준비하는 데 시간이 걸리고 있습니다.")),
        10_000,
      );
      const handleStateChange = () => {
        if (updatingWorker.state === "activated") {
          window.clearTimeout(timeoutId);
          resolve();
        } else if (updatingWorker.state === "redundant") {
          window.clearTimeout(timeoutId);
          reject(new Error("새 알림 기능을 적용하지 못했습니다."));
        }
      };
      updatingWorker.addEventListener("statechange", handleStateChange);
      handleStateChange();
    });
  }
  return navigator.serviceWorker.ready;
}

function applicationServerKey(value: string) {
  const padding = "=".repeat((4 - (value.length % 4)) % 4);
  const base64 = (value + padding).replace(/-/g, "+").replace(/_/g, "/");
  const decoded = window.atob(base64);
  const bytes = new Uint8Array(decoded.length);
  for (let index = 0; index < decoded.length; index += 1) {
    bytes[index] = decoded.charCodeAt(index);
  }
  return bytes;
}

async function registerSubscription(subscription: PushSubscription) {
  const json = subscription.toJSON();
  if (!json.endpoint || !json.keys?.p256dh || !json.keys?.auth) {
    throw new Error("휴대전화 알림 정보를 만들지 못했습니다.");
  }
  return apiFetch<PushResult>("/api/push/subscriptions", {
    method: "POST",
    body: JSON.stringify({
      endpoint: json.endpoint,
      expiration_time: json.expirationTime ?? null,
      keys: json.keys,
    }),
  });
}

export async function synchronizeWebPushSubscription() {
  if (!supportsWebPush() || Notification.permission !== "granted") {
    return null;
  }
  const config = await apiFetch<PushConfig>("/api/push/config");
  if (!config.enabled || !config.public_key) {
    return null;
  }
  const registration = await ensureMesilServiceWorker();
  const subscription = await registration.pushManager.getSubscription();
  if (!subscription) {
    return null;
  }
  return registerSubscription(subscription);
}

export async function readPushStatus(): Promise<{
  state: PushSupportState;
  config: PushConfig | null;
}> {
  if (!supportsWebPush()) {
    return { state: "unsupported", config: null };
  }
  const config = await apiFetch<PushConfig>("/api/push/config");
  if (!config.enabled || !config.public_key) {
    return { state: "disabled", config };
  }
  if (Notification.permission === "denied") {
    return { state: "permission-denied", config };
  }
  const registration = await ensureMesilServiceWorker();
  const subscription = await registration.pushManager.getSubscription();
  if (subscription) {
    await registerSubscription(subscription);
  }
  return { state: subscription ? "active" : "ready", config };
}

export async function enableWebPush(config: PushConfig) {
  if (!config.enabled || !config.public_key) {
    throw new Error("휴대전화 알림 서버가 아직 준비되지 않았습니다.");
  }
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    throw new Error("휴대전화 알림 권한을 허용해 주세요.");
  }
  const registration = await ensureMesilServiceWorker();
  let subscription = await registration.pushManager.getSubscription();
  if (!subscription) {
    subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: applicationServerKey(config.public_key),
    });
  }
  return registerSubscription(subscription);
}

export async function disableWebPush() {
  const registration = await ensureMesilServiceWorker();
  const subscription = await registration.pushManager.getSubscription();
  if (!subscription) {
    return {
      enabled: true,
      active: false,
      message: "이 휴대전화의 잠금화면 알림은 꺼져 있습니다.",
    } satisfies PushResult;
  }
  const result = await apiFetch<PushResult>("/api/push/subscriptions", {
    method: "DELETE",
    body: JSON.stringify({ endpoint: subscription.endpoint }),
  });
  await subscription.unsubscribe();
  return result;
}

export async function sendWebPushTest() {
  return apiFetch<PushResult>("/api/push/test", {
    method: "POST",
    body: "{}",
  });
}
