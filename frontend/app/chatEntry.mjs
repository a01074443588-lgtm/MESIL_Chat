export const CHAT_WEB_ENTRY_SESSION_KEY = "mesil-chat:web-entry-selected";

export async function loadChatEntryRelease(enabled, fetcher, signal) {
  if (!enabled) return null;
  const response = await fetcher("/downloads/staff-test-release.json", {
    cache: "no-store",
    signal,
  });
  return response.ok ? response.json() : null;
}

export function resolveChatEntryPlatform(userAgent, isNativeApp) {
  if (isNativeApp) return "native";
  return /Android/i.test(String(userAgent ?? "")) ? "android" : "web";
}

export function platformManifestDisplay(userAgent) {
  return /Android/i.test(String(userAgent ?? "")) ? "browser" : "standalone";
}
