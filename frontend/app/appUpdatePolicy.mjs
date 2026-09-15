export const APP_UPDATE_CHECK_INTERVAL_MS = 5 * 60 * 1000;

/**
 * @param {number} lastCheckedAt
 * @param {number} now
 * @param {boolean} force
 * @param {number} [intervalMs]
 */
export function shouldRunAppUpdateCheck(
  lastCheckedAt,
  now,
  force,
  intervalMs = APP_UPDATE_CHECK_INTERVAL_MS,
) {
  return force || lastCheckedAt === 0 || now - lastCheckedAt >= intervalMs;
}

/**
 * @param {string} currentBuildId
 * @param {unknown} serverBuildId
 */
export function isNewAppBuild(currentBuildId, serverBuildId) {
  return (
    typeof serverBuildId === "string" &&
    serverBuildId.trim().length > 0 &&
    serverBuildId !== currentBuildId
  );
}

/**
 * @param {boolean} hasUnsavedDraft
 * @param {boolean} allowDraftLoss
 * @param {boolean} [confirmed]
 * @returns {"defer" | "cancel" | "reload"}
 */
export function decideAppUpdateAction(
  hasUnsavedDraft,
  allowDraftLoss,
  confirmed = false,
) {
  if (!hasUnsavedDraft) return "reload";
  if (!allowDraftLoss) return "defer";
  return confirmed ? "reload" : "cancel";
}
