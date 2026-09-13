export const WEBSOCKET_RECONNECT_BASE_MS = 1_000;
export const WEBSOCKET_RECONNECT_MAX_MS = 30_000;

export function websocketReconnectDelay(attempt, randomValue = Math.random()) {
  const normalizedAttempt = Math.max(0, Math.floor(Number(attempt) || 0));
  const boundedRandom = Math.min(1, Math.max(0, Number(randomValue) || 0));
  const exponentialDelay = Math.min(
    WEBSOCKET_RECONNECT_MAX_MS,
    WEBSOCKET_RECONNECT_BASE_MS * 2 ** normalizedAttempt,
  );
  const jitteredDelay = exponentialDelay * (0.75 + boundedRandom * 0.5);
  return Math.min(WEBSOCKET_RECONNECT_MAX_MS, Math.round(jitteredDelay));
}

export function shouldReconnectWebSocket({
  disposed,
  forcedLogout,
  online,
  visible,
}) {
  return Boolean(!disposed && !forcedLogout && online && visible);
}
