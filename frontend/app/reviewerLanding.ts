export type ReviewerDestination = "chat" | "care_briefing";

const destinationKey = "mesil-reviewer-destination";
const roomIdKey = "mesil-reviewer-room-id";

export function saveReviewerLanding(
  destination: ReviewerDestination,
  roomId?: string | null,
) {
  window.sessionStorage.setItem(destinationKey, destination);
  if (roomId) {
    window.sessionStorage.setItem(roomIdKey, roomId);
  } else {
    window.sessionStorage.removeItem(roomIdKey);
  }
}

export function readReviewerLanding() {
  const destination = window.sessionStorage.getItem(destinationKey);
  if (destination !== "chat" && destination !== "care_briefing") return null;
  return {
    destination,
    roomId: window.sessionStorage.getItem(roomIdKey),
  };
}

export function clearReviewerLanding() {
  window.sessionStorage.removeItem(destinationKey);
  window.sessionStorage.removeItem(roomIdKey);
}
