export const WAKE_NOTIFICATION_MAX_AGE_MS = 60_000;

const WAKE_NOTIFICATION_CLOCK_SKEW_MS = 10_000;

export function isRecentWakeNotification(createdAt, now = Date.now()) {
  const createdAtMs = Date.parse(createdAt);
  if (!Number.isFinite(createdAtMs)) return false;

  const ageMs = now - createdAtMs;
  return (
    ageMs >= -WAKE_NOTIFICATION_CLOCK_SKEW_MS &&
    ageMs <= WAKE_NOTIFICATION_MAX_AGE_MS
  );
}
