export const SESSION_RETRY_INTERVAL_MS = 5_000;

export function isSessionAuthenticationFailure(status) {
  return status === 401 || status === 403;
}

export function shouldRetrySessionCheck({ unavailable, online, visible }) {
  return Boolean(unavailable && online && visible);
}

export function shouldAcknowledgeNativeWebCall({ native, visibilityState }) {
  return Boolean(native && visibilityState === "visible");
}
