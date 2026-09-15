export type NavigationHistoryState = {
  roomId?: string;
  messageId?: string;
  returnMessageId?: string;
  returnScrollTop?: number;
  returnAnchorOffset?: number;
  aiAssistOpen?: true;
  adminOpen?: true;
  staffRoomOpen?: true;
  staffContactId?: string;
  staffContactName?: string;
  callChoice?: "room" | "staff";
  settingsPanel?: "security" | "notification" | "ai";
  residentPickerOpen?: true;
};

const navigationHistoryStateKey = "__mesilChatNavigation";
export const PDF_HISTORY_STATE_KEY = "smcodiPdfAttachmentId";
export const IMAGE_HISTORY_STATE_KEY = "smcodiImageAttachmentId";
export const EXTRACTION_EDITOR_HISTORY_STATE_KEY =
  "smcodiExtractionEditorAttachmentId";

const attachmentOverlayHistoryStateKeys = [
  PDF_HISTORY_STATE_KEY,
  IMAGE_HISTORY_STATE_KEY,
  EXTRACTION_EDITOR_HISTORY_STATE_KEY,
] as const;

function asHistoryRecord(value: unknown): Record<string, unknown> {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return { ...(value as Record<string, unknown>) };
  }
  return {};
}

export function readNavigationHistoryState(
  value: unknown,
): NavigationHistoryState {
  const root = asHistoryRecord(value);
  const candidate = root[navigationHistoryStateKey];
  if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) {
    return {};
  }

  const navigation = candidate as Record<string, unknown>;
  return {
    roomId: typeof navigation.roomId === "string" ? navigation.roomId : undefined,
    messageId:
      typeof navigation.messageId === "string" ? navigation.messageId : undefined,
    returnMessageId:
      typeof navigation.returnMessageId === "string"
        ? navigation.returnMessageId
        : undefined,
    returnScrollTop:
      typeof navigation.returnScrollTop === "number" &&
      Number.isFinite(navigation.returnScrollTop) &&
      navigation.returnScrollTop >= 0
        ? navigation.returnScrollTop
        : undefined,
    returnAnchorOffset:
      typeof navigation.returnAnchorOffset === "number" &&
      Number.isFinite(navigation.returnAnchorOffset)
        ? navigation.returnAnchorOffset
        : undefined,
    aiAssistOpen: navigation.aiAssistOpen === true ? true : undefined,
    adminOpen: navigation.adminOpen === true ? true : undefined,
    staffRoomOpen: navigation.staffRoomOpen === true ? true : undefined,
    staffContactId:
      typeof navigation.staffContactId === "string"
        ? navigation.staffContactId
        : undefined,
    staffContactName:
      typeof navigation.staffContactName === "string"
        ? navigation.staffContactName
        : undefined,
    callChoice:
      navigation.callChoice === "room" || navigation.callChoice === "staff"
        ? navigation.callChoice
        : undefined,
    settingsPanel:
      navigation.settingsPanel === "security" ||
      navigation.settingsPanel === "notification" ||
      navigation.settingsPanel === "ai"
        ? navigation.settingsPanel
        : undefined,
    residentPickerOpen:
      navigation.residentPickerOpen === true ? true : undefined,
  };
}

export function updateNavigationHistoryState(
  patch: Partial<NavigationHistoryState>,
  mode: "push" | "replace" = "push",
  options: { clearAttachmentOverlays?: boolean } = {},
) {
  const root = asHistoryRecord(window.history.state);
  if (options.clearAttachmentOverlays) {
    for (const key of attachmentOverlayHistoryStateKeys) delete root[key];
  }
  const next = { ...readNavigationHistoryState(root), ...patch };

  for (const key of Object.keys(next) as (keyof NavigationHistoryState)[]) {
    if (next[key] === undefined) delete next[key];
  }

  if (Object.keys(next).length > 0) {
    root[navigationHistoryStateKey] = next;
  } else {
    delete root[navigationHistoryStateKey];
  }

  const url = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  if (mode === "replace") {
    window.history.replaceState(root, "", url);
  } else {
    window.history.pushState(root, "", url);
  }
}
