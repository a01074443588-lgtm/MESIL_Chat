"use client";

import {
  FormEvent,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { ApiError, apiFetch, apiUpload, websocketUrl } from "../api";
import {
  CHAT_ATTACHMENT_ACCEPT,
  attachmentNameError,
} from "../attachmentFormats";
import type {
  JobCode,
  ManagedRoom,
  Message,
  MessageComment,
  MessageDetail,
  OrgUnit,
  PositionTitle,
  Resident,
  Room,
  RoomMember,
  StaffDirectoryEntry,
  StaffRoom,
  User,
} from "../types";
import { AiConnectionPanel } from "./AiConnectionPanel";
import { AiAssistPanel } from "./AiAssistPanel";
import { AdminDrawer } from "./AdminDrawer";
import { AttachmentDisplay } from "./AttachmentDisplay";
import {
  prepareAttachmentShare,
  savePreparedAttachment,
} from "../attachmentShare";
import { DeveloperLauncher } from "./DeveloperLauncher";
import { LoginScreen } from "./LoginScreen";
import { MessageDetailOverlay } from "./MessageDetailOverlay";
import { ResidentLinkReview } from "./ResidentLinkReview";
import { NotificationSoundPanel } from "./NotificationSoundPanel";
import { RoomFilesOverlay } from "./RoomFilesOverlay";
import { RoomSearchOverlay } from "./RoomSearchOverlay";
import { SecurityPanel } from "./SecurityPanel";
import { StaffRoomPanel } from "./StaffRoomPanel";
import { CallChoiceSheet } from "./CallChoiceSheet";
import {
  StaffContactSheet,
  type StaffContactTarget,
} from "./StaffContactSheet";
import { VoiceCallOverlay } from "./VoiceCallOverlay";
import { PeriodWorkDesk } from "./PeriodWorkDesk";
import { WorkDesk } from "./WorkDesk";
import { ResidentPickerDialog } from "./ResidentPickerDialog";
import {
  useVoiceCall,
  type CallMode,
} from "../voiceCall";
import {
  playCommentNotification,
  playMessageNotification,
  readNotificationSoundMode,
  shouldPlayMessageNotification,
  type NotificationSoundMode,
} from "../notificationSound";
import { synchronizeWebPushSubscription } from "../pushNotifications";
import {
  acknowledgeNativeWebCall,
  clearPendingNativeCallAction,
  readPendingNativeCallAction,
  setNativeCallAudioRoute,
  setNativeCallScreenActive,
  synchronizeNativePushRegistration,
} from "../nativePush";
import { consumeSharedTargetFiles } from "../sharedTarget";
import { institutionSetupState } from "../institutionSetup.mjs";
import {
  consumeNativeSharedFiles,
  isNativeMesilApp,
  listenForNativeShares,
} from "../nativeShare";
import {
  isLongMessageBody,
  messageDisplayBody,
} from "../messagePresentation";
import { isRecentWakeNotification } from "../wakeNotification.mjs";
import {
  isSessionAuthenticationFailure,
  SESSION_RETRY_INTERVAL_MS,
  shouldAcknowledgeNativeWebCall,
  shouldRetrySessionCheck,
} from "../sessionRecovery.mjs";
import {
  shouldReconnectWebSocket,
  websocketReconnectDelay,
} from "../websocketReconnect.mjs";
import {
  clearReviewerLanding,
  readReviewerLanding,
} from "../reviewerLanding";
import {
  readNavigationHistoryState,
  updateNavigationHistoryState,
} from "../navigationHistory";
import { incrementRoomUnreadCount } from "../unreadRooms.mjs";

const codedSyntheticStaffPattern = /^(?:요보|사복|간호|조리|영양|위생|운전|작치|시설|관리자)\d{2}$/;

function formatTime(value: string) {
  return new Intl.DateTimeFormat("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatDay(value: string) {
  return new Intl.DateTimeFormat("ko-KR", {
    month: "long",
    day: "numeric",
    weekday: "short",
  }).format(new Date(value));
}

function dateInputValue(value: string) {
  const date = new Date(value);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function residentOptionLabel(resident: Resident) {
  if (resident.roster_source !== "carefor") return resident.display_name;
  if (resident.service_type === "facility") {
    return ["시설", resident.room_name, resident.display_name].filter(Boolean).join(" ");
  }
  if (resident.service_type === "daycare") {
    return `주간보호 ${resident.display_name}`;
  }
  if (resident.service_type === "homecare") {
    return `방문요양 ${resident.display_name}`;
  }
  return resident.display_name;
}

function selectedResidentButtonLabel(
  selectedIds: string[],
  residents: Resident[],
) {
  if (selectedIds.length === 0) return "어르신";
  if (selectedIds.length > 1) return `${selectedIds.length}명 선택`;
  const selected = residents.find((resident) => resident.id === selectedIds[0]);
  return selected ? residentOptionLabel(selected) : "1명 선택";
}

const kindLabels: Record<Room["kind"], string> = {
  all: "전체",
  business: "사업부",
  department: "부서",
  job: "직종",
  floor: "층",
  team: "팀",
  custom: "지정",
  self: "개인",
};

const actionLabels = {
  handover: "인수인계",
  cooperation: "업무협조",
  confirmation: "확인 요청",
};

const actionStatusLabels = {
  assigned: "담당자 확인 전",
  acknowledged: "담당자 확인",
  in_progress: "처리 중",
  completed: "완료",
};

const messageNatureLabels = {
  handover: "인수인계",
  work_request: "업무협조",
  report: "보고",
};

function MessageActionBadge({
  actionItem,
}: {
  actionItem: NonNullable<Message["action_item"]>;
}) {
  const actionType = actionLabels[actionItem.action_type];
  const assignee =
    actionItem.assignee_user_name ?? actionItem.assignee_unit_name ?? "담당 미지정";
  const actionStatus = actionStatusLabels[actionItem.status];
  const explanation =
    `업무 표식입니다. 유형은 ${actionType}, 담당자는 ${assignee}, ` +
    `업무 상태는 ${actionStatus}입니다. 메시지 읽음 표시와는 별개입니다.`;

  return (
    <span
      className={`message-action-badge priority-${actionItem.priority}`}
      aria-label={explanation}
      title={explanation}
    >
      <strong className="action-badge-kicker">업무</strong>
      <span className="action-badge-part">
        <small>유형</small>
        {actionType}
      </span>
      <span className="action-badge-part">
        <small>담당</small>
        {assignee}
      </span>
      <span className="action-badge-part">
        <small>상태</small>
        {actionStatus}
      </span>
    </span>
  );
}

function roomDisplayName(room: Room, user: User) {
  if (room.kind !== "custom") return room.name;
  const participantNames = room.name
    .split(",")
    .map((name) => name.trim())
    .filter(Boolean);
  if (participantNames.length !== 2 || !participantNames.includes(user.full_name)) {
    return room.name;
  }
  return participantNames.find((name) => name !== user.full_name) ?? room.name;
}

function engagementLabel(message: Message) {
  return `읽음 ${message.read_count}${
    message.reply_user_count > 0 ? ` · 답글 ${message.reply_user_count}명` : ""
  }`;
}

function maskRecalledMessage(message: Message, recalledAt?: string): Message {
  return {
    ...message,
    body: "작성자가 회수한 메시지입니다.",
    is_recalled: true,
    recalled_at: recalledAt ?? message.recalled_at ?? new Date().toISOString(),
    resident: null,
    resident_links: [],
    resident_ref: null,
    attachments: [],
    comment_count: 0,
    unread_comment_count: 0,
    latest_comment: null,
    read_count: 0,
    reply_user_count: 0,
    action_item: null,
    forwarded_from: null,
    reply_to: null,
  };
}

function LatestCommentPreview({
  message,
  onOpen,
}: {
  message: Message;
  onOpen: () => void;
}) {
  const latestComment = message.latest_comment;
  if (!latestComment) {
    return message.comment_count > 0 ? (
      <span className={`comment-count ${message.unread_comment_count ? "new" : ""}`}>
        답글 {message.comment_count}
        {message.unread_comment_count ? ` · 새 답글 ${message.unread_comment_count}` : ""}
      </span>
    ) : null;
  }
  return (
    <button
      type="button"
      className={`latest-comment-preview ${message.unread_comment_count ? "new" : ""}`}
      aria-label={`${latestComment.author_name}님의 최신 답글과 전체 답글 열기`}
      onClick={onOpen}
    >
      <span className="latest-comment-preview-heading">
        <strong>최신 답글 · {latestComment.author_name}</strong>
        <time>{formatTime(latestComment.created_at)}</time>
      </span>
      <span className="latest-comment-preview-body">{latestComment.body}</span>
      <span className="latest-comment-preview-footer">
        답글 {message.comment_count}개
        {message.unread_comment_count ? ` · 새 답글 ${message.unread_comment_count}개` : ""}
        {" · 전체 보기"}
      </span>
    </button>
  );
}

type NotificationNavigationTarget = {
  roomId: string | null;
  messageId: string | null;
  callId: string | null;
  callAction: "accept" | "decline" | null;
};

type PendingVoiceCall = {
  call_id: string;
  call_state: "ringing";
  room_id: string;
  room_name: string;
  caller_user_id: string;
  caller_name: string;
  call_mode: CallMode;
  member_count: number;
  expires_at: string;
};

const uuidPattern =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const MAX_ATTACHMENTS_PER_MESSAGE = 10;
const MAX_ATTACHMENT_BYTES = 30 * 1024 * 1024;
const MAX_ATTACHMENTS_TOTAL_BYTES = 100 * 1024 * 1024;

function attachmentSelectionKey(file: File) {
  return `${file.name}:${file.size}:${file.lastModified}:${file.type}`;
}

function mergeAttachmentSelections(currentFiles: File[], addedFiles: File[]) {
  const knownFiles = new Set(currentFiles.map(attachmentSelectionKey));
  return [
    ...currentFiles,
    ...addedFiles.filter((file) => {
      const key = attachmentSelectionKey(file);
      if (knownFiles.has(key)) return false;
      knownFiles.add(key);
      return true;
    }),
  ];
}

function attachmentSelectionError(selectedFiles: File[]) {
  if (selectedFiles.length > MAX_ATTACHMENTS_PER_MESSAGE) {
    return `파일은 한 메시지에 최대 ${MAX_ATTACHMENTS_PER_MESSAGE}개까지 첨부할 수 있습니다. 현재 ${selectedFiles.length}개를 선택했습니다.`;
  }
  const emptyFile = selectedFiles.find((file) => file.size <= 0);
  if (emptyFile) {
    return `빈 파일은 첨부할 수 없습니다: ${emptyFile.name}`;
  }
  for (const file of selectedFiles) {
    const formatError = attachmentNameError(file.name);
    if (formatError) return formatError;
  }
  const oversizedFiles = selectedFiles.filter(
    (file) => file.size > MAX_ATTACHMENT_BYTES,
  );
  if (oversizedFiles.length > 0) {
    const names = oversizedFiles
      .slice(0, 3)
      .map((file) => file.name)
      .join(", ");
    const remainder = oversizedFiles.length > 3
      ? ` 외 ${oversizedFiles.length - 3}개`
      : "";
    return `파일 하나의 최대 크기는 30MB입니다. 다시 선택해 주세요: ${names}${remainder}`;
  }
  const totalBytes = selectedFiles.reduce((sum, file) => sum + file.size, 0);
  if (totalBytes > MAX_ATTACHMENTS_TOTAL_BYTES) {
    return "한 메시지의 파일 전체 용량은 100MB 이하여야 합니다.";
  }
  return "";
}

function sharedTargetErrorMessage(code: string) {
  if (code === "too-many") {
    return `한 번에 최대 ${MAX_ATTACHMENTS_PER_MESSAGE}개까지 공유할 수 있습니다.`;
  }
  if (code === "too-large") {
    return "파일 하나는 30MB 이하여야 합니다.";
  }
  if (code === "total-too-large") {
    return "한 번에 공유하는 파일의 전체 용량은 100MB 이하여야 합니다.";
  }
  if (code === "unsupported") {
    return "지원하지 않는 파일 형식입니다. 사진·음성·동영상·PDF 파일을 선택해 주세요.";
  }
  if (code === "no-file") {
    return "공유할 파일을 받지 못했습니다. 원래 앱에서 파일을 다시 선택해 주세요.";
  }
  return "공유한 파일을 받는 중 문제가 생겼습니다. 원래 앱에서 다시 공유해 주세요.";
}

function totalAttachmentMegabytes(selectedFiles: File[]) {
  const bytes = selectedFiles.reduce((total, file) => total + file.size, 0);
  return (bytes / (1024 * 1024)).toFixed(2);
}

function clearNotificationNavigationTarget() {
  const url = new URL(window.location.href);
  url.searchParams.delete("room");
  url.searchParams.delete("message");
  url.searchParams.delete("call");
  url.searchParams.delete("caller");
  url.searchParams.delete("caller_name");
  url.searchParams.delete("call_mode");
  url.searchParams.delete("member_count");
  url.searchParams.delete("call_expires");
  url.searchParams.delete("call_action");
  window.history.replaceState(
    window.history.state,
    "",
    `${url.pathname}${url.search}${url.hash}`,
  );
}

function readNotificationNavigationTarget(): NotificationNavigationTarget | null {
  const searchParams = new URLSearchParams(window.location.search);
  const roomId = searchParams.get("room")?.trim() ?? "";
  const messageId = searchParams.get("message")?.trim() || null;
  const callId = searchParams.get("call")?.trim() ?? "";
  const callAction = searchParams.get("call_action")?.trim() ?? "";
  const hasIncomingCall = searchParams.has("call");
  const hasNotificationTarget =
    searchParams.has("room") || searchParams.has("message") || hasIncomingCall;

  if (!hasNotificationTarget) return null;
  if (
    (roomId !== "" && !uuidPattern.test(roomId)) ||
    (messageId !== null && roomId === "") ||
    (messageId !== null && !uuidPattern.test(messageId)) ||
    (hasIncomingCall && (
      !uuidPattern.test(callId) ||
      (callAction && !["accept", "decline"].includes(callAction))
    ))
  ) {
    clearNotificationNavigationTarget();
    return null;
  }
  return {
    roomId: roomId || null,
    messageId,
    callId: hasIncomingCall ? callId : null,
    callAction: hasIncomingCall && callAction
      ? (callAction as "accept" | "decline")
      : null,
  };
}

export function ChatApp() {
  const [loading, setLoading] = useState(true);
  const [me, setMe] = useState<User | null>(null);
  const [sessionCheckUnavailable, setSessionCheckUnavailable] = useState(false);
  const [loginNotice, setLoginNotice] = useState("");
  const [rooms, setRooms] = useState<Room[]>([]);
  const [activeRoomId, setActiveRoomId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [hasOlderMessages, setHasOlderMessages] = useState(false);
  const [loadingOlderMessages, setLoadingOlderMessages] = useState(false);
  const [residents, setResidents] = useState<Resident[]>([]);
  const [adminResidents, setAdminResidents] = useState<Resident[]>([]);
  const [messageBody, setMessageBody] = useState("");
  const [replyTarget, setReplyTarget] = useState<Message | null>(null);
  const [selectedResidentIds, setSelectedResidentIds] = useState<string[]>([]);
  const [residentPickerOpen, setResidentPickerOpen] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [pendingSharedFiles, setPendingSharedFiles] = useState<File[]>([]);
  const [sharedTargetError, setSharedTargetError] = useState("");
  const [sharedRoomOpeningId, setSharedRoomOpeningId] = useState<string | null>(null);
  const [reportImage, setReportImage] = useState(false);
  const [noticeMode, setNoticeMode] = useState(false);
  const [composerOptionsOpen, setComposerOptionsOpen] = useState(false);
  const [sendFeedback, setSendFeedback] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [recallingMessageId, setRecallingMessageId] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [connectionState, setConnectionState] = useState<"connecting" | "online" | "offline">(
    "connecting",
  );
  const [error, setError] = useState("");
  const [roomLoading, setRoomLoading] = useState(false);
  const [adminOpen, setAdminOpen] = useState(false);
  const [adminInitialTab, setAdminInitialTab] = useState<
    "employees" | "organization"
  >("employees");
  const [organizationSetupChecked, setOrganizationSetupChecked] = useState(false);
  const [aiConnectionOpen, setAiConnectionOpen] = useState(false);
  const [securityOpen, setSecurityOpen] = useState(false);
  const [notificationSoundOpen, setNotificationSoundOpen] = useState(false);
  const [notificationSoundMode, setNotificationSoundMode] =
    useState<NotificationSoundMode>(() => readNotificationSoundMode());
  const [socketRevision, setSocketRevision] = useState(0);
  const [workdeskOpen, setWorkdeskOpen] = useState(false);
  const [recordReviewOpen, setRecordReviewOpen] = useState(false);
  const [selectedMessageId, setSelectedMessageId] = useState<string | null>(null);
  const [messageActionMenuId, setMessageActionMenuId] = useState<string | null>(null);
  const [copySelectionActive, setCopySelectionActive] = useState(false);
  const [copySelectionIds, setCopySelectionIds] = useState<string[]>([]);
  const [copySelectionBusy, setCopySelectionBusy] = useState(false);
  const [commentTarget, setCommentTarget] = useState<Message | null>(null);
  const [commentBody, setCommentBody] = useState("");
  const [commentThread, setCommentThread] = useState<MessageComment[]>([]);
  const [commentDialogBusy, setCommentDialogBusy] = useState(false);
  const [commentDialogError, setCommentDialogError] = useState("");
  const [forwardTarget, setForwardTarget] = useState<Message | null>(null);
  const [selectedForwardRoomIds, setSelectedForwardRoomIds] = useState<string[]>([]);
  const [forwardDialogBusy, setForwardDialogBusy] = useState(false);
  const [forwardDialogError, setForwardDialogError] = useState("");
  const [detailRefreshVersion, setDetailRefreshVersion] = useState(0);
  const [aiAssistMessage, setAiAssistMessage] = useState<Message | null>(null);
  const [roomFilesOpen, setRoomFilesOpen] = useState(false);
  const [roomSearchOpen, setRoomSearchOpen] = useState(false);
  const [staffRoomOpen, setStaffRoomOpen] = useState(false);
  const [staffRoomInitialRoomId, setStaffRoomInitialRoomId] = useState<string | null>(null);
  const [staffContactTarget, setStaffContactTarget] =
    useState<StaffContactTarget | null>(null);
  const [staffContactBusy, setStaffContactBusy] = useState(false);
  const [staffContactError, setStaffContactError] = useState("");
  const [staffContactCallChoiceOpen, setStaffContactCallChoiceOpen] =
    useState(false);
  const [roomCallChoiceOpen, setRoomCallChoiceOpen] = useState(false);
  const [roomCallMembers, setRoomCallMembers] = useState<RoomMember[]>([]);
  const [selectedRoomCallMemberIds, setSelectedRoomCallMemberIds] = useState<string[]>([]);
  const [roomCallMembersLoading, setRoomCallMembersLoading] = useState(false);
  const [roomCallChoiceError, setRoomCallChoiceError] = useState("");
  const [units, setUnits] = useState<OrgUnit[]>([]);
  const [jobs, setJobs] = useState<JobCode[]>([]);
  const [positionTitles, setPositionTitles] = useState<PositionTitle[]>([]);
  const [employees, setEmployees] = useState<User[]>([]);
  const [staffDirectory, setStaffDirectory] = useState<StaffDirectoryEntry[]>([]);
  const [managedRooms, setManagedRooms] = useState<ManagedRoom[]>([]);
  const activeRoomRef = useRef<string | null>(null);
  const adminOpenRef = useRef(false);
  const roomsRef = useRef<Room[]>([]);
  const messagesRef = useRef<Message[]>([]);
  const selectedMessageRef = useRef<string | null>(null);
  const aiAssistMessageRef = useRef<Message | null>(null);
  const forcedLogoutRef = useRef(false);
  const sessionCheckInFlightRef = useRef(false);
  const socketRef = useRef<WebSocket | null>(null);
  const metadataRefreshesRef = useRef<Map<string, Promise<void>>>(new Map());
  const metadataRefreshedAtRef = useRef<Map<string, number>>(new Map());
  const sendingRef = useRef(false);
  const wakeSyncInFlightRef = useRef(false);
  const roomRefreshRequestRef = useRef(0);
  const lastWakeSyncAtRef = useRef(0);
  const hiddenAtRef = useRef<number | null>(null);
  const reviewerLandingAttemptedRef = useRef(false);
  const notificationNavigationAttemptedRef = useRef(false);
  const sharedTargetAttemptedRef = useRef(false);
  const messageEndRef = useRef<HTMLDivElement | null>(null);
  const messageAreaRef = useRef<HTMLDivElement | null>(null);
  const messageActionLongPressRef = useRef<number | null>(null);
  const keepRoomAtLatestRef = useRef(false);
  const messageReturnPositionRef = useRef<{
    roomId: string;
    messageId: string;
    scrollTop: number;
    anchorOffset: number | null;
  } | null>(null);
  const residentLinkPositionRef = useRef<{
    roomId: string;
    messageId: string;
    scrollTop: number;
    anchorOffset: number | null;
  } | null>(null);
  const currentUserId = me?.id ?? null;
  const currentUserRole = me?.role ?? null;
  const mentorFullReview = Boolean(
    me?.is_reviewer_session && me.reviewer_experience === "mentor_full",
  );
  const codedSyntheticMode = Boolean(
    me?.full_name && codedSyntheticStaffPattern.test(me.full_name),
  );
  const canViewAdmin = me?.role === "admin" || mentorFullReview;
  const organizationSetup = institutionSetupState({
    checked: organizationSetupChecked,
    isAdmin: me?.role === "admin",
    isReviewerSession: Boolean(me?.is_reviewer_session),
    isCodedSynthetic: codedSyntheticMode,
    units,
  });
  const passwordChangeRequired =
    (me?.must_change_password ?? false) && !me?.is_reviewer_session;
  const chatViewActive = Boolean(
    currentUserId && !me?.is_dev_launcher && !passwordChangeRequired,
  );
  const sendRealtimeEvent = useCallback((payload: Record<string, unknown>) => {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) return false;
    socket.send(JSON.stringify(payload));
    return true;
  }, []);
  const sendRealtimeEventWhenConnected = useCallback(
    async (payload: Record<string, unknown>, timeoutMs = 8_000) => {
      const deadline = Date.now() + timeoutMs;
      let reconnectRequested = false;

      while (Date.now() < deadline) {
        const socket = socketRef.current;
        if (socket?.readyState === WebSocket.OPEN) {
          try {
            socket.send(JSON.stringify(payload));
            return true;
          } catch {
            socket.close();
          }
        }
        if (
          !reconnectRequested &&
          (!socket ||
            socket.readyState === WebSocket.CLOSING ||
            socket.readyState === WebSocket.CLOSED)
        ) {
          reconnectRequested = true;
          setSocketRevision((current) => current + 1);
        }
        await new Promise<void>((resolve) => window.setTimeout(resolve, 100));
      }
      return false;
    },
    [],
  );
  const voiceCall = useVoiceCall({
    currentUserId,
    currentUserName: me?.full_name ?? "",
    isReviewer: Boolean(me?.is_reviewer_session),
    sendRealtimeEvent,
    sendRealtimeEventWhenConnected,
  });
  const presentIncomingCall = voiceCall.presentIncomingCall;
  const acceptIncomingCall = voiceCall.acceptIncoming;
  const declineIncomingCall = voiceCall.declineIncoming;
  const dismissIncomingCall = voiceCall.dismissIncoming;
  const startVoiceCall = voiceCall.startCall;
  const voiceCallEventHandlerRef = useRef(voiceCall.handleRealtimeEvent);

  useEffect(() => {
    adminOpenRef.current = adminOpen;
  }, [adminOpen]);

  useEffect(() => {
    if (!messageActionMenuId) return;
    const closeOnOutsidePointer = (event: PointerEvent) => {
      const target = event.target;
      if (
        target instanceof Element &&
        target.closest(`[data-message-actions="${messageActionMenuId}"]`)
      ) {
        return;
      }
      setMessageActionMenuId(null);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMessageActionMenuId(null);
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsidePointer);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [messageActionMenuId]);
  voiceCallEventHandlerRef.current = voiceCall.handleRealtimeEvent;
  const [speakerphoneOn, setSpeakerphoneOn] = useState(false);
  const activeCallId = voiceCall.active?.callId ?? null;
  const activeCallMode = voiceCall.active?.callMode ?? null;

  useEffect(() => {
    if (!isNativeMesilApp()) return;
    void setNativeCallScreenActive(Boolean(voiceCall.incoming || voiceCall.active)).catch(() => {
      // 잠금화면 표시를 지원하지 않는 기기에서도 통화 자체는 계속 진행합니다.
    });
  }, [voiceCall.active, voiceCall.incoming]);

  useEffect(() => {
    if (!voiceCall.incoming?.callId || !isNativeMesilApp()) return;
    const callId = voiceCall.incoming.callId;
    const acknowledgeIfVisible = () => {
      if (!shouldAcknowledgeNativeWebCall({
        native: true,
        visibilityState: document.visibilityState,
      })) return;
      void acknowledgeNativeWebCall(callId).catch(() => {
        // 확인 전달이 실패하면 네이티브 알림이 수신 안전망 역할을 유지합니다.
      });
    };
    acknowledgeIfVisible();
    document.addEventListener("visibilitychange", acknowledgeIfVisible);
    return () => {
      document.removeEventListener("visibilitychange", acknowledgeIfVisible);
    };
  }, [voiceCall.incoming?.callId]);

  useEffect(() => {
    if (!isNativeMesilApp()) return;
    const route = !activeCallId
      ? "system"
      : activeCallMode === "video"
        ? "speaker"
        : "earpiece";
    void setNativeCallAudioRoute(route)
      .then(() => setSpeakerphoneOn(route === "speaker"))
      .catch(() => setSpeakerphoneOn(route === "earpiece"));
  }, [activeCallId, activeCallMode]);

  const toggleCallAudioRoute = useCallback(() => {
    const next = !speakerphoneOn;
    setSpeakerphoneOn(next);
    void setNativeCallAudioRoute(next ? "speaker" : "earpiece").catch(() => {
      setSpeakerphoneOn(!next);
    });
  }, [speakerphoneOn]);

  useEffect(() => {
    if (!chatViewActive || me?.is_reviewer_session) return;
    const synchronization = isNativeMesilApp()
      ? synchronizeNativePushRegistration()
      : synchronizeWebPushSubscription();
    void synchronization.catch(() => {
      // 최초 권한 허용은 사용자 조작이 필요합니다. 기존 허용 기기만 조용히 복구합니다.
    });
  }, [chatViewActive, currentUserId, me?.is_reviewer_session]);

  useEffect(() => {
    if (!chatViewActive || sharedTargetAttemptedRef.current) return;
    const url = new URL(window.location.href);
    const token = url.searchParams.get("shared");
    const shareError = url.searchParams.get("share_error");
    if (!token && !shareError) return;
    sharedTargetAttemptedRef.current = true;

    const clearShareQuery = () => {
      url.searchParams.delete("shared");
      url.searchParams.delete("share_error");
      window.history.replaceState(
        window.history.state,
        "",
        `${url.pathname}${url.search}${url.hash}`,
      );
    };

    if (shareError) {
      clearShareQuery();
      const timer = window.setTimeout(
        () => setSharedTargetError(sharedTargetErrorMessage(shareError)),
        0,
      );
      return () => window.clearTimeout(timer);
    }

    void consumeSharedTargetFiles(token ?? "")
      .then((sharedFiles) => {
        if (sharedFiles.length === 0) {
          setSharedTargetError(
            "공유한 파일을 불러오지 못했습니다. 임시 파일이 만료되었을 수 있으니 원래 앱에서 다시 공유해 주세요.",
          );
          return;
        }
        setSharedTargetError("");
        setPendingSharedFiles(sharedFiles);
      })
      .catch(() => {
        setSharedTargetError(
          "공유한 파일을 불러오지 못했습니다. 임시 파일이 만료되었을 수 있으니 원래 앱에서 다시 공유해 주세요.",
        );
      })
      .finally(clearShareQuery);
  }, [chatViewActive]);

  useEffect(() => {
    if (!chatViewActive) return;
    let disposed = false;

    const receiveNativeShare = async () => {
      try {
        const sharedFiles = await consumeNativeSharedFiles();
        if (disposed || sharedFiles.length === 0) return;
        const selectionError = attachmentSelectionError(sharedFiles);
        if (selectionError) {
          setSharedTargetError(selectionError);
          return;
        }
        setSharedTargetError("");
        setPendingSharedFiles(sharedFiles);
      } catch {
        if (!disposed) {
          setSharedTargetError(
            "공유한 파일을 불러오지 못했습니다. 원래 앱에서 다시 공유해 주세요.",
          );
        }
      }
    };

    void receiveNativeShare();
    const listener = listenForNativeShares(() => {
      void receiveNativeShare();
    });
    return () => {
      disposed = true;
      void listener.then((handle) => handle?.remove());
    };
  }, [chatViewActive]);

  useEffect(() => {
    const root = document.documentElement;
    const viewport = window.visualViewport;
    const pendingTimers = new Set<number>();
    let animationFrame = 0;

    const applyVisibleHeight = () => {
      window.cancelAnimationFrame(animationFrame);
      animationFrame = window.requestAnimationFrame(() => {
        const visibleHeight = viewport?.height ?? window.innerHeight;
        if (visibleHeight > 0) {
          root.style.setProperty(
            "--app-viewport-height",
            `${Math.round(visibleHeight)}px`,
          );
        }
      });
    };

    const settleVisibleHeight = () => {
      applyVisibleHeight();
      [80, 220, 450].forEach((delay) => {
        const timer = window.setTimeout(() => {
          pendingTimers.delete(timer);
          applyVisibleHeight();
        }, delay);
        pendingTimers.add(timer);
      });
    };

    const onFieldFocus = (event: Event) => {
      if (
        event.target instanceof HTMLTextAreaElement ||
        event.target instanceof HTMLInputElement
      ) {
        settleVisibleHeight();
      }
    };

    applyVisibleHeight();
    viewport?.addEventListener("resize", applyVisibleHeight);
    viewport?.addEventListener("scroll", applyVisibleHeight);
    window.addEventListener("resize", applyVisibleHeight);
    window.addEventListener("orientationchange", settleVisibleHeight);
    document.addEventListener("focusin", onFieldFocus);
    document.addEventListener("focusout", onFieldFocus);

    return () => {
      window.cancelAnimationFrame(animationFrame);
      pendingTimers.forEach((timer) => window.clearTimeout(timer));
      viewport?.removeEventListener("resize", applyVisibleHeight);
      viewport?.removeEventListener("scroll", applyVisibleHeight);
      window.removeEventListener("resize", applyVisibleHeight);
      window.removeEventListener("orientationchange", settleVisibleHeight);
      document.removeEventListener("focusin", onFieldFocus);
      document.removeEventListener("focusout", onFieldFocus);
      root.style.removeProperty("--app-viewport-height");
    };
  }, []);

  useEffect(() => {
    if (!chatViewActive) return;
    document.documentElement.classList.add("chat-app-active");
    document.body.classList.add("chat-app-active");
    return () => {
      document.documentElement.classList.remove("chat-app-active");
      document.body.classList.remove("chat-app-active");
    };
  }, [chatViewActive]);

  const resetSession = useCallback((message?: string) => {
    forcedLogoutRef.current = true;
    setMe(null);
    setRooms([]);
    setMessages([]);
    setResidents([]);
    setAdminResidents([]);
    setSelectedResidentIds([]);
    setFiles([]);
    setPendingSharedFiles([]);
    setSharedTargetError("");
    setSharedRoomOpeningId(null);
    setReportImage(false);
    setComposerOptionsOpen(false);
    setActiveRoomId(null);
    activeRoomRef.current = null;
    setAdminOpen(false);
    setAdminInitialTab("employees");
    setOrganizationSetupChecked(false);
    setStaffRoomOpen(false);
    setStaffRoomInitialRoomId(null);
    setStaffContactTarget(null);
    setStaffContactBusy(false);
    setStaffContactError("");
    setStaffContactCallChoiceOpen(false);
    setRoomCallChoiceOpen(false);
    setSecurityOpen(false);
    setNotificationSoundOpen(false);
    setWorkdeskOpen(false);
    setSelectedMessageId(null);
    selectedMessageRef.current = null;
    setAiAssistMessage(null);
    aiAssistMessageRef.current = null;
    updateNavigationHistoryState(
      {
        roomId: undefined,
        messageId: undefined,
        returnMessageId: undefined,
        returnScrollTop: undefined,
        returnAnchorOffset: undefined,
        aiAssistOpen: undefined,
        adminOpen: undefined,
        staffRoomOpen: undefined,
        staffContactId: undefined,
        staffContactName: undefined,
        callChoice: undefined,
        settingsPanel: undefined,
      },
      "replace",
    );
    setLoginNotice(message ?? "");
  }, []);

  const refreshRooms = useCallback(async () => {
    const requestId = ++roomRefreshRequestRef.current;
    const nextRooms = await apiFetch<Room[]>("/api/rooms");
    if (requestId !== roomRefreshRequestRef.current) return roomsRef.current;
    roomsRef.current = nextRooms;
    setRooms(nextRooms);
    const current = activeRoomRef.current;
    if (current && !nextRooms.some((room) => room.id === current)) {
      activeRoomRef.current = null;
      setActiveRoomId(null);
      setMessages([]);
      setResidents([]);
      setSelectedResidentIds([]);
      setFiles([]);
      setReportImage(false);
      setComposerOptionsOpen(false);
      selectedMessageRef.current = null;
      setSelectedMessageId(null);
      aiAssistMessageRef.current = null;
      setAiAssistMessage(null);
    }
    return nextRooms;
  }, []);

  useEffect(() => {
    roomsRef.current = rooms;
  }, [rooms]);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    const openedMessage = aiAssistMessageRef.current;
    if (!openedMessage) return;
    const latestMessage = messages.find((message) => message.id === openedMessage.id);
    if (!latestMessage?.is_recalled) return;
    aiAssistMessageRef.current = null;
    setAiAssistMessage(null);
    if (readNavigationHistoryState(window.history.state).aiAssistOpen) {
      updateNavigationHistoryState({ aiAssistOpen: undefined }, "replace");
    }
  }, [messages]);

  const refreshAdminData = useCallback(async () => {
    const [
      nextUnits,
      nextJobs,
      nextPositionTitles,
      nextEmployees,
      nextStaffDirectory,
      nextManagedRooms,
      nextResidents,
    ] = await Promise.all([
      apiFetch<OrgUnit[]>("/api/org-units?include_inactive=true"),
      apiFetch<JobCode[]>("/api/job-codes?include_inactive=true"),
      apiFetch<PositionTitle[]>("/api/position-titles?include_inactive=true"),
      apiFetch<User[]>("/api/employees"),
      apiFetch<StaffDirectoryEntry[]>("/api/admin/staff-directory"),
      apiFetch<ManagedRoom[]>("/api/admin/rooms?include_inactive=true"),
      apiFetch<Resident[]>("/api/admin/residents"),
    ]);
    setUnits(nextUnits);
    setOrganizationSetupChecked(true);
    setJobs(nextJobs);
    setPositionTitles(nextPositionTitles);
    setEmployees(nextEmployees);
    setStaffDirectory(nextStaffDirectory);
    setManagedRooms(nextManagedRooms);
    setAdminResidents(nextResidents);
    await refreshRooms();
  }, [refreshRooms]);

  const markRead = useCallback(
    async (roomId: string, messageId: string) => {
      await apiFetch(`/api/rooms/${roomId}/read`, {
        method: "POST",
        body: JSON.stringify({ message_id: messageId }),
      });
      setRooms((current) => {
        const next = current.map((room) =>
          room.id === roomId ? { ...room, unread_count: 0 } : room,
        );
        roomsRef.current = next;
        return next;
      });
    },
    [],
  );

  const openRoom = useCallback(
    async (roomId: string, synchronizeHistory = true) => {
      if (activeRoomRef.current !== roomId) {
        messageReturnPositionRef.current = null;
      }
      keepRoomAtLatestRef.current = !messageReturnPositionRef.current;
      if (synchronizeHistory) {
        const navigation = readNavigationHistoryState(window.history.state);
        updateNavigationHistoryState(
          {
            roomId,
            messageId: undefined,
            returnMessageId: undefined,
            returnScrollTop: undefined,
            returnAnchorOffset: undefined,
            aiAssistOpen: undefined,
            residentPickerOpen: undefined,
          },
          navigation.roomId ? "replace" : "push",
          { clearAttachmentOverlays: true },
        );
        selectedMessageRef.current = null;
        setSelectedMessageId(null);
        aiAssistMessageRef.current = null;
        setAiAssistMessage(null);
      }
      activeRoomRef.current = roomId;
      setRoomFilesOpen(false);
      setReplyTarget(null);
      setCopySelectionActive(false);
      setCopySelectionIds([]);
      setActiveRoomId(roomId);
      setError("");
      setRoomLoading(true);
      try {
        const [nextMessages, nextResidents] = await Promise.all([
          apiFetch<Message[]>(`/api/rooms/${roomId}/messages?limit=31`),
          apiFetch<Resident[]>(`/api/rooms/${roomId}/residents`),
        ]);
        const visibleMessages =
          nextMessages.length > 30 ? nextMessages.slice(-30) : nextMessages;
        setHasOlderMessages(nextMessages.length > 30);
        messagesRef.current = visibleMessages;
        setMessages(visibleMessages);
        setResidents(nextResidents);
        setSelectedResidentIds([]);
        setFiles([]);
        setReportImage(false);
        setComposerOptionsOpen(false);
        const last = visibleMessages.at(-1);
        if (last) await markRead(roomId, last.id);
        return visibleMessages;
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "채팅방을 열지 못했습니다.");
        return null;
      } finally {
        setRoomLoading(false);
      }
    },
    [markRead],
  );

  const loadOlderMessages = useCallback(async () => {
    const roomId = activeRoomRef.current;
    const oldest = messagesRef.current[0];
    if (!roomId || !oldest || loadingOlderMessages || !hasOlderMessages) return;
    setLoadingOlderMessages(true);
    const area = messageAreaRef.current;
    const previousHeight = area?.scrollHeight ?? 0;
    try {
      const fetched = await apiFetch<Message[]>(
        `/api/rooms/${roomId}/messages?before_id=${oldest.id}&limit=31`,
      );
      const older = fetched.length > 30 ? fetched.slice(-30) : fetched;
      setHasOlderMessages(fetched.length > 30);
      setMessages((current) => {
        const existing = new Set(current.map((message) => message.id));
        const next = [
          ...older.filter((message) => !existing.has(message.id)),
          ...current,
        ];
        messagesRef.current = next;
        return next;
      });
      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(() => {
          if (area) area.scrollTop += area.scrollHeight - previousHeight;
        });
      });
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "이전 대화를 불러오지 못했습니다.",
      );
    } finally {
      setLoadingOlderMessages(false);
    }
  }, [hasOlderMessages, loadingOlderMessages]);

  useLayoutEffect(() => {
    updateNavigationHistoryState(
      {
        roomId: undefined,
        messageId: undefined,
        returnMessageId: undefined,
        returnScrollTop: undefined,
        returnAnchorOffset: undefined,
        aiAssistOpen: undefined,
        adminOpen: undefined,
        staffRoomOpen: undefined,
        staffContactId: undefined,
        staffContactName: undefined,
        callChoice: undefined,
        settingsPanel: undefined,
        residentPickerOpen: undefined,
      },
      "replace",
      { clearAttachmentOverlays: true },
    );
  }, []);

  useEffect(() => {
    const synchronizeNavigation = (event: PopStateEvent) => {
      const navigation = readNavigationHistoryState(event.state);
      const nextRoomId = navigation.roomId ?? null;
      const nextMessageId = nextRoomId ? navigation.messageId ?? null : null;
      if (
        nextRoomId &&
        !nextMessageId &&
        navigation.returnMessageId &&
        navigation.returnScrollTop !== undefined
      ) {
        messageReturnPositionRef.current = {
          roomId: nextRoomId,
          messageId: navigation.returnMessageId,
          scrollTop: navigation.returnScrollTop,
          anchorOffset: navigation.returnAnchorOffset ?? null,
        };
      }
      const nextAiAssistOpen = Boolean(
        nextMessageId &&
          navigation.aiAssistOpen &&
          (!me?.is_reviewer_session || mentorFullReview),
      );
      setSecurityOpen(navigation.settingsPanel === "security");
      setNotificationSoundOpen(navigation.settingsPanel === "notification");
      setAiConnectionOpen(navigation.settingsPanel === "ai");
      setStaffRoomOpen(navigation.staffRoomOpen === true);
      if (!navigation.staffRoomOpen) setStaffRoomInitialRoomId(null);
      setStaffContactTarget(
        navigation.staffContactId && navigation.staffContactName
          ? {
              id: navigation.staffContactId,
              name: navigation.staffContactName,
            }
          : null,
      );
      setStaffContactCallChoiceOpen(navigation.callChoice === "staff");
      setRoomCallChoiceOpen(navigation.callChoice === "room");
      setResidentPickerOpen(navigation.residentPickerOpen === true);
      if (!navigation.staffContactId) {
        setStaffContactBusy(false);
        setStaffContactError("");
      }

      if (nextMessageId !== selectedMessageRef.current) {
        selectedMessageRef.current = nextMessageId;
        setSelectedMessageId(nextMessageId);
        if (nextMessageId) {
          setDetailRefreshVersion((current) => current + 1);
        }
      }

      if (!nextAiAssistOpen) {
        const closedAiMessageId = aiAssistMessageRef.current?.id ?? null;
        aiAssistMessageRef.current = null;
        setAiAssistMessage(null);
        if (closedAiMessageId && closedAiMessageId === nextMessageId) {
          setDetailRefreshVersion((current) => current + 1);
        }
      } else if (
        nextMessageId &&
        aiAssistMessageRef.current?.id !== nextMessageId
      ) {
        const cachedMessage = messagesRef.current.find(
          (message) => message.id === nextMessageId && !message.is_recalled,
        );
        if (cachedMessage) {
          aiAssistMessageRef.current = cachedMessage;
          setAiAssistMessage(cachedMessage);
        } else {
          void apiFetch<MessageDetail>(`/api/messages/${nextMessageId}`)
            .then((payload) => {
              const currentNavigation = readNavigationHistoryState(
                window.history.state,
              );
              if (
                currentNavigation.aiAssistOpen &&
                currentNavigation.messageId === payload.message.id &&
                !payload.message.is_recalled
              ) {
                aiAssistMessageRef.current = payload.message;
                setAiAssistMessage(payload.message);
              }
            })
            .catch(() => {
              if (readNavigationHistoryState(window.history.state).aiAssistOpen) {
                updateNavigationHistoryState(
                  { aiAssistOpen: undefined },
                  "replace",
                );
              }
            });
        }
      }

      if (nextRoomId === activeRoomRef.current) return;
      if (nextRoomId) {
        void openRoom(nextRoomId, false);
        return;
      }

      activeRoomRef.current = null;
      setActiveRoomId(null);
      setMessages([]);
      setResidents([]);
      setSelectedResidentIds([]);
      setReplyTarget(null);
      setFiles([]);
      setReportImage(false);
      setComposerOptionsOpen(false);
      aiAssistMessageRef.current = null;
      setAiAssistMessage(null);
    };

    window.addEventListener("popstate", synchronizeNavigation);
    return () => window.removeEventListener("popstate", synchronizeNavigation);
  }, [me?.is_reviewer_session, mentorFullReview, openRoom]);

  const checkCurrentSession = useCallback(async () => {
    if (sessionCheckInFlightRef.current) return;
    sessionCheckInFlightRef.current = true;
    try {
      const user = await apiFetch<User>("/api/auth/me");
      setMe(user);
      setSessionCheckUnavailable(false);
    } catch (reason) {
      if (
        reason instanceof ApiError &&
        isSessionAuthenticationFailure(reason.status)
      ) {
        setMe(null);
        setSessionCheckUnavailable(false);
      } else {
        setSessionCheckUnavailable(true);
      }
    } finally {
      sessionCheckInFlightRef.current = false;
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void checkCurrentSession(), 0);
    return () => window.clearTimeout(timer);
  }, [checkCurrentSession]);

  useEffect(() => {
    if (!sessionCheckUnavailable) return;

    const retryIfAvailable = () => {
      if (
        shouldRetrySessionCheck({
          unavailable: sessionCheckUnavailable,
          online: navigator.onLine,
          visible: document.visibilityState === "visible",
        })
      ) {
        void checkCurrentSession();
      }
    };
    const onPageShow = () => retryIfAvailable();
    const onVisibilityChange = () => retryIfAvailable();
    const retryTimer = window.setInterval(
      retryIfAvailable,
      SESSION_RETRY_INTERVAL_MS,
    );

    window.addEventListener("online", retryIfAvailable);
    window.addEventListener("pageshow", onPageShow);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      window.clearInterval(retryTimer);
      window.removeEventListener("online", retryIfAvailable);
      window.removeEventListener("pageshow", onPageShow);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [checkCurrentSession, sessionCheckUnavailable]);

  useEffect(() => {
    const onAuthError = (event: Event) => {
      const detail = (event as CustomEvent<{ status: number; message: string }>).detail;
      if (detail.status === 401) {
        resetSession("로그인이 만료되었습니다. 다시 로그인해 주세요.");
      }
    };
    window.addEventListener("smcodi:auth-error", onAuthError);
    return () => window.removeEventListener("smcodi:auth-error", onAuthError);
  }, [resetSession]);

  useEffect(() => {
    if (!currentUserId || passwordChangeRequired) return;
    forcedLogoutRef.current = false;
    const timer = window.setTimeout(() => {
      if (me?.is_reviewer_session && !mentorFullReview) {
        void refreshRooms();
      } else {
        void Promise.all([
          apiFetch<OrgUnit[]>("/api/org-units"),
          apiFetch<JobCode[]>("/api/job-codes"),
          refreshRooms(),
        ]).then(([nextUnits, nextJobs]) => {
          setUnits(nextUnits);
          setJobs(nextJobs);
          if (currentUserRole === "admin") setOrganizationSetupChecked(true);
        });
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [
    currentUserId,
    currentUserRole,
    me?.is_reviewer_session,
    mentorFullReview,
    passwordChangeRequired,
    refreshRooms,
  ]);

  useEffect(() => {
    if (
      !me?.is_reviewer_session ||
      passwordChangeRequired ||
      reviewerLandingAttemptedRef.current
    ) {
      return;
    }
    const landing = readReviewerLanding() ?? {
      destination:
        me.reviewer_experience === "social_worker"
          ? ("care_briefing" as const)
          : ("chat" as const),
      roomId: null,
    };

    if (landing.destination === "care_briefing") {
      if (rooms.length === 0) return;
      reviewerLandingAttemptedRef.current = true;
      clearReviewerLanding();
      if (me.role === "admin" || me.can_process_records) {
        window.queueMicrotask(() => setWorkdeskOpen(true));
      }
      return;
    }

    if (rooms.length === 0) return;
    const targetRoom = landing.roomId
      ? rooms.find((room) => room.id === landing.roomId)
      : rooms[0];
    reviewerLandingAttemptedRef.current = true;
    clearReviewerLanding();
    if (targetRoom) {
      window.queueMicrotask(() => void openRoom(targetRoom.id));
    }
  }, [me, openRoom, passwordChangeRequired, rooms]);

  useEffect(() => {
    if (
      !currentUserId ||
      me?.is_reviewer_session ||
      passwordChangeRequired ||
      rooms.length === 0 ||
      notificationNavigationAttemptedRef.current
    ) {
      return;
    }

    notificationNavigationAttemptedRef.current = true;

    window.queueMicrotask(() => {
      void (async () => {
        const urlTarget = readNotificationNavigationTarget();
        const nativeCallAction = await readPendingNativeCallAction().catch(
          () => null,
        );
        const target: NotificationNavigationTarget | null = nativeCallAction
          ? {
              roomId: null,
              messageId: null,
              callId: nativeCallAction.callId,
              callAction: nativeCallAction.action,
            }
          : urlTarget;
        let pendingCall: PendingVoiceCall | null = null;
        if (!target || target.callId) {
          try {
            const pendingCalls = await apiFetch<PendingVoiceCall[]>(
              "/api/voice-calls/pending",
            );
            pendingCall = target?.callId
              ? pendingCalls.find((call) => call.call_id === target.callId) ?? null
              : pendingCalls[0] ?? null;
          } catch {
            // 로그인이 복구되는 동안 실패하면 다음 앱 재진입 또는 WebSocket
            // 초대에서 다시 확인합니다.
          }
        }

        if (pendingCall) {
          const pendingRoom = rooms.find((room) => room.id === pendingCall.room_id);
          if (!pendingRoom) {
            clearNotificationNavigationTarget();
            return;
          }
          const nextMessages = await openRoom(pendingRoom.id);
          if (!nextMessages) return;
          const presented = presentIncomingCall({
            callId: pendingCall.call_id,
            roomId: pendingCall.room_id,
            callerUserId: pendingCall.caller_user_id,
            callerName: pendingCall.caller_name,
            memberCount: pendingCall.member_count,
            callMode: pendingCall.call_mode,
            roomName: pendingCall.room_name,
            expiresAt: Date.parse(pendingCall.expires_at),
          });
          if (presented && target?.callAction === "accept") {
            const accepted = await acceptIncomingCall();
            if (!accepted) {
              // 통화 참여 신호가 실제로 전송되지 않았다면 Android의 받기
              // 동작을 남겨 다음 로그인·재진입에서 다시 복구한다.
              return;
            }
            await clearPendingNativeCallAction(pendingCall.call_id).catch(
              () => undefined,
            );
          } else if (presented && target?.callAction === "decline") {
            window.setTimeout(() => declineIncomingCall(), 0);
          }
          clearNotificationNavigationTarget();
          return;
        }

        if (!target?.roomId) {
          if (target?.callId) {
            await clearPendingNativeCallAction(target.callId).catch(
              () => undefined,
            );
          }
          clearNotificationNavigationTarget();
          return;
        }
        const targetRoom = rooms.find((room) => room.id === target.roomId);
        if (!targetRoom) {
          clearNotificationNavigationTarget();
          return;
        }
        const nextMessages = await openRoom(targetRoom.id);
        if (!nextMessages) return;
        if (target.messageId) {
          let targetMessage = nextMessages.find(
            (message) =>
              message.id === target.messageId &&
              message.room_id === target.roomId,
          );
          if (!targetMessage) {
            try {
              const detail = await apiFetch<MessageDetail>(
                `/api/messages/${target.messageId}`,
              );
              if (detail.message.room_id === target.roomId) {
                targetMessage = detail.message;
              }
            } catch {
              // 잘못되었거나 접근 권한이 없는 알림 주소는 조용히 무시합니다.
            }
          }
          if (!targetMessage) {
            clearNotificationNavigationTarget();
            return;
          }
          openMessageDetail(targetMessage.id);
        }
        clearNotificationNavigationTarget();
      })();
    });
  }, [
    currentUserId,
    me?.is_reviewer_session,
    openRoom,
    passwordChangeRequired,
    rooms,
    acceptIncomingCall,
    declineIncomingCall,
    presentIncomingCall,
  ]);

  const synchronizeAfterWake = useCallback(async () => {
    if (!me || me.must_change_password || wakeSyncInFlightRef.current) return;
    wakeSyncInFlightRef.current = true;
    const wakeSyncStartedAt = Date.now();
    try {
      const currentIncomingCallId = voiceCall.incoming?.callId;
      if (currentIncomingCallId) {
        try {
          const pendingCalls = await apiFetch<PendingVoiceCall[]>(
            "/api/voice-calls/pending",
          );
          if (
            !pendingCalls.some((call) => call.call_id === currentIncomingCallId)
          ) {
            dismissIncomingCall(currentIncomingCallId);
          }
        } catch {
          // 서버 상태를 확인하지 못했으면 현재 수신 화면을 임의로 지우지 않는다.
        }
      }
      const previousRooms = roomsRef.current;
      const previousById = new Map(previousRooms.map((room) => [room.id, room]));
      const nextRooms = await refreshRooms();
      const changedRooms = nextRooms.filter((room) => {
        const previous = previousById.get(room.id);
        return (
          previous &&
          (previous.last_message_at !== room.last_message_at ||
            previous.unread_count !== room.unread_count)
        );
      });

      const activeRoomId = activeRoomRef.current;
      const messageSets = new Map<string, Message[]>();
      if (activeRoomId) {
        const [nextMessages, nextResidents] = await Promise.all([
          apiFetch<Message[]>(`/api/rooms/${activeRoomId}/messages`),
          apiFetch<Resident[]>(`/api/rooms/${activeRoomId}/residents`),
        ]);
        messageSets.set(activeRoomId, nextMessages);
        messagesRef.current = nextMessages;
        setMessages(nextMessages);
        setResidents(nextResidents);
        const last = nextMessages.at(-1);
        if (last && !workdeskOpen && !selectedMessageRef.current) {
          await markRead(activeRoomId, last.id);
        }
      }

      if (notificationSoundMode !== "off" && changedRooms.length > 0) {
        const missedMessages: Message[] = [];
        for (const room of changedRooms.slice(0, 6)) {
          const previous = previousById.get(room.id);
          const previousMessageAt = previous?.last_message_at
            ? new Date(previous.last_message_at).getTime()
            : 0;
          const roomMessages =
            messageSets.get(room.id) ??
            (await apiFetch<Message[]>(`/api/rooms/${room.id}/messages`));
          missedMessages.push(
            ...roomMessages.filter(
              (message) =>
                new Date(message.created_at).getTime() > previousMessageAt &&
                message.sender_id !== me.id &&
                isRecentWakeNotification(message.created_at, wakeSyncStartedAt),
            ),
          );
        }
        const newestAudibleMessage = missedMessages
          .sort(
            (left, right) =>
              new Date(right.created_at).getTime() - new Date(left.created_at).getTime(),
          )
          .find((message) =>
            shouldPlayMessageNotification(notificationSoundMode, message, me.id),
          );
        if (newestAudibleMessage) {
          playMessageNotification(notificationSoundMode, newestAudibleMessage, me.id);
        }
      }
    } catch {
      // 복귀 동기화가 실패하면 실시간 연결 재시도가 계속 진행됩니다.
    } finally {
      lastWakeSyncAtRef.current = Date.now();
      wakeSyncInFlightRef.current = false;
    }
  }, [
    dismissIncomingCall,
    markRead,
    me,
    notificationSoundMode,
    refreshRooms,
    voiceCall.incoming?.callId,
    workdeskOpen,
  ]);

  useEffect(() => {
    if (!me || me.must_change_password) return;

    const resume = (force = false) => {
      if (document.visibilityState === "hidden") return;
      const now = Date.now();
      if (!force && now - lastWakeSyncAtRef.current < 4_000) return;
      if (!me.is_reviewer_session) {
        const synchronization = isNativeMesilApp()
          ? synchronizeNativePushRegistration()
          : synchronizeWebPushSubscription();
        void synchronization.catch(() => {
          // 이미 허용된 기기의 만료된 알림 주소만 조용히 복구합니다.
        });
      }
      if (force || socketRef.current?.readyState !== WebSocket.OPEN) {
        setSocketRevision((current) => current + 1);
      }
      void synchronizeAfterWake();
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "hidden") {
        hiddenAtRef.current = Date.now();
        return;
      }
      const sleptFor = hiddenAtRef.current ? Date.now() - hiddenAtRef.current : 0;
      hiddenAtRef.current = null;
      if (sleptFor > 0 && !workdeskOpen && !selectedMessageRef.current) {
        const activeRoomId = activeRoomRef.current;
        const activeRoom = roomsRef.current.find((room) => room.id === activeRoomId);
        const lastMessage = messagesRef.current.at(-1);
        if (activeRoomId && (activeRoom?.unread_count ?? 0) > 0 && lastMessage) {
          void markRead(activeRoomId, lastMessage.id);
        }
      }
      if (sleptFor >= 3_000) resume(true);
    };
    const onPageShow = (event: PageTransitionEvent) => resume(event.persisted);
    const onFocus = () => resume();
    const onOnline = () => resume(true);

    document.addEventListener("visibilitychange", onVisibilityChange);
    window.addEventListener("pageshow", onPageShow);
    window.addEventListener("focus", onFocus);
    window.addEventListener("online", onOnline);
    return () => {
      document.removeEventListener("visibilitychange", onVisibilityChange);
      window.removeEventListener("pageshow", onPageShow);
      window.removeEventListener("focus", onFocus);
      window.removeEventListener("online", onOnline);
    };
  }, [markRead, me, synchronizeAfterWake, workdeskOpen]);

  const refreshMessageMetadata = useCallback((messageId: string, notifyListeners = false) => {
    const existing = metadataRefreshesRef.current.get(messageId);
    if (existing) return existing;

    const refreshedAt = metadataRefreshedAtRef.current.get(messageId) ?? 0;
    if (Date.now() - refreshedAt < 750) return Promise.resolve();

    const request = apiFetch<MessageDetail>(`/api/messages/${messageId}`)
      .then((detail) => {
        setMessages((current) => current.map((message) =>
          message.id === detail.message.id ? detail.message : message,
        ));
        if (selectedMessageRef.current === messageId) {
          setDetailRefreshVersion((current) => current + 1);
        }
        metadataRefreshedAtRef.current.set(messageId, Date.now());
        if (notifyListeners) {
          window.dispatchEvent(new CustomEvent("mesil-resident-links-changed", {
            detail: { messageId },
          }));
        }
      })
      .catch(() => {
        setMessages((current) => current.filter((message) => message.id !== messageId));
      })
      .finally(() => {
        metadataRefreshesRef.current.delete(messageId);
      });
    metadataRefreshesRef.current.set(messageId, request);
    return request;
  }, []);

  useEffect(() => {
    const changed = (event: Event) => {
      const messageId = (event as CustomEvent<{ messageId?: string }>).detail?.messageId;
      if (messageId) void refreshMessageMetadata(messageId);
    };
    window.addEventListener("mesil-resident-links-changed", changed);
    return () => window.removeEventListener("mesil-resident-links-changed", changed);
  }, [refreshMessageMetadata]);

  useEffect(() => {
    if (!currentUserId || !currentUserRole || passwordChangeRequired) return;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let pingTimer: ReturnType<typeof setInterval> | null = null;
    let disposed = false;
    let lastPongAt = Date.now();
    let reconnectAttempt = 0;

    const scheduleReconnect = () => {
      if (
        !shouldReconnectWebSocket({
          disposed,
          forcedLogout: forcedLogoutRef.current,
          online: navigator.onLine,
          visible: document.visibilityState === "visible",
        })
      ) {
        return;
      }
      if (reconnectTimer) clearTimeout(reconnectTimer);
      const delay = websocketReconnectDelay(reconnectAttempt);
      reconnectAttempt += 1;
      reconnectTimer = setTimeout(connect, delay);
    };

    const connect = () => {
      if (
        !shouldReconnectWebSocket({
          disposed,
          forcedLogout: forcedLogoutRef.current,
          online: navigator.onLine,
          visible: document.visibilityState === "visible",
        })
      ) {
        setConnectionState("offline");
        return;
      }
      setConnectionState("connecting");
      socket = new WebSocket(websocketUrl());
      socketRef.current = socket;
      socket.onopen = () => {
        reconnectAttempt = 0;
        setConnectionState("online");
        lastPongAt = Date.now();
        if (pingTimer) clearInterval(pingTimer);
        pingTimer = setInterval(() => {
          if (
            socket?.readyState === WebSocket.OPEN &&
            document.visibilityState === "visible"
          ) {
            if (
              document.visibilityState === "visible" &&
              Date.now() - lastPongAt > 70_000
            ) {
              socket.close(4000, "연결 상태 다시 확인");
              return;
            }
            socket.send(JSON.stringify({ event: "ping" }));
          }
        }, 25_000);
      };
      socket.onmessage = (event) => {
        const payload = JSON.parse(event.data) as {
          event: string;
          reason?: string;
          message?: Message;
          message_id?: string;
          message_ids?: string[];
          room_ids?: string[];
          room_id?: string;
          user_id?: string;
          comment_count?: number;
          reply_user_count?: number;
          comment?: Message["latest_comment"];
          notification_user_ids?: string[];
          action_item?: Message["action_item"];
        };
        if (payload.event === "pong" || payload.event === "ready") {
          lastPongAt = Date.now();
          return;
        }
        if (payload.event.startsWith("voice_call_")) {
          void voiceCallEventHandlerRef.current(
            payload as unknown as Record<string, unknown> & { event?: string },
          );
          return;
        }
        if (payload.event === "force_logout") {
          resetSession(payload.reason ?? "접속이 종료되었습니다.");
          return;
        }
        if (payload.event === "rooms_changed") {
          void refreshRooms();
          void apiFetch<User>("/api/auth/me").then(setMe);
          return;
        }
        if (
          (payload.event === "employees_changed" ||
            payload.event === "organization_changed") &&
          currentUserRole === "admin" && adminOpenRef.current
        ) {
          void refreshAdminData();
          return;
        }
        if (payload.event === "message_metadata_changed" && payload.message_id) {
          void refreshMessageMetadata(payload.message_id, true);
          return;
        }
        if (payload.event === "message_created" && payload.message) {
          const incoming = payload.message;
          playMessageNotification(notificationSoundMode, incoming, currentUserId);
          const isActiveRoomVisible =
            activeRoomRef.current === incoming.room_id &&
            document.visibilityState === "visible";
          if (activeRoomRef.current === incoming.room_id) {
            const area = messageAreaRef.current;
            if (!residentLinkPositionRef.current) {
              keepRoomAtLatestRef.current = Boolean(
                area && area.scrollHeight - area.scrollTop - area.clientHeight <= 80,
              );
            }
            setMessages((current) =>
              current.some((item) => item.id === incoming.id)
                ? current
                : [...current, incoming],
            );
          }
          if (isActiveRoomVisible) {
            void markRead(incoming.room_id, incoming.id);
          } else {
            if (incoming.sender_id !== currentUserId) {
              setRooms((current) => {
                const next = incrementRoomUnreadCount(current, incoming.room_id);
                roomsRef.current = next;
                return next;
              });
            }
            void refreshRooms();
          }
        }
        if (payload.event === "message_recalled" && payload.message_ids?.length) {
          const recalledIds = new Set(payload.message_ids);
          setMessages((current) => {
            const next = current.map((message) =>
              recalledIds.has(message.id)
                ? maskRecalledMessage(message)
                : message,
            );
            messagesRef.current = next;
            return next;
          });
          if (selectedMessageRef.current && recalledIds.has(selectedMessageRef.current)) {
            closeMessageDetail();
          }
          void refreshRooms();
          return;
        }
        if (payload.event === "message_commented" && payload.message_id) {
          playCommentNotification(
            notificationSoundMode,
            currentUserId,
            payload.comment?.author_id,
            payload.notification_user_ids,
          );
          setMessages((current) =>
            current.map((message) =>
              message.id === payload.message_id
                ? {
                    ...message,
                    comment_count: payload.comment_count ?? message.comment_count + 1,
                    reply_user_count:
                      payload.reply_user_count ?? message.reply_user_count,
                    latest_comment: payload.comment ?? message.latest_comment,
                    unread_comment_count:
                      payload.comment?.author_id === currentUserId ||
                      selectedMessageRef.current === message.id
                        ? 0
                        : message.unread_comment_count + 1,
                  }
                : message,
            ),
          );
        }
        if (
          payload.event === "messages_read" &&
          payload.message_ids?.length
        ) {
          const newlyReadMessageIds = new Set(payload.message_ids);
          setMessages((current) =>
            current.map((message) =>
              newlyReadMessageIds.has(message.id)
                ? { ...message, read_count: message.read_count + 1 }
                : message,
            ),
          );
        }
        if (payload.event === "action_item_changed" && payload.message_id) {
          setMessages((current) =>
            current.map((message) =>
              message.id === payload.message_id
                ? { ...message, action_item: payload.action_item ?? message.action_item }
                : message,
            ),
          );
        }
        if (
          (
            payload.event === "messages_read" ||
            payload.event === "message_commented" ||
            payload.event === "action_item_changed"
          ) &&
          selectedMessageRef.current
        ) {
          setDetailRefreshVersion((current) => current + 1);
        }
      };
      socket.onclose = () => {
        if (socketRef.current === socket) socketRef.current = null;
        setConnectionState("offline");
        if (pingTimer) clearInterval(pingTimer);
        scheduleReconnect();
      };
      socket.onerror = () => {
        socket?.close();
      };
    };

    connect();
    return () => {
      disposed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (pingTimer) clearInterval(pingTimer);
      if (socketRef.current === socket) socketRef.current = null;
      socket?.close();
    };
  }, [
    currentUserId,
    currentUserRole,
    markRead,
    notificationSoundMode,
    passwordChangeRequired,
    refreshAdminData,
    refreshMessageMetadata,
    refreshRooms,
    resetSession,
    socketRevision,
  ]);

  useLayoutEffect(() => {
    if (
      roomLoading ||
      selectedMessageRef.current ||
      messageReturnPositionRef.current ||
      residentLinkPositionRef.current ||
      !keepRoomAtLatestRef.current
    ) {
      return;
    }
    const area = messageAreaRef.current;
    if (!area || messages.length === 0) return;
    area.scrollTop = area.scrollHeight;
  }, [activeRoomId, messages, roomLoading]);

  useEffect(() => {
    if (
      roomLoading ||
      !keepRoomAtLatestRef.current ||
      selectedMessageRef.current ||
      messageReturnPositionRef.current ||
      residentLinkPositionRef.current
    ) {
      return;
    }
    const area = messageAreaRef.current;
    if (!area || messages.length === 0) return;

    let frame = 0;
    const scrollToLatest = () => {
      if (!keepRoomAtLatestRef.current) return;
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        if (keepRoomAtLatestRef.current) area.scrollTop = area.scrollHeight;
      });
    };
    const stopKeepingLatest = () => {
      keepRoomAtLatestRef.current = false;
    };
    const handleDeferredMedia = (event: Event) => {
      if (
        event.target instanceof HTMLImageElement ||
        event.target instanceof HTMLVideoElement ||
        event.target instanceof HTMLAudioElement
      ) {
        scrollToLatest();
      }
    };

    scrollToLatest();
    area.addEventListener("load", handleDeferredMedia, true);
    area.addEventListener("loadedmetadata", handleDeferredMedia, true);
    area.addEventListener("wheel", stopKeepingLatest, { passive: true });
    area.addEventListener("pointerdown", stopKeepingLatest, { passive: true });
    area.addEventListener("touchstart", stopKeepingLatest, { passive: true });
    const stabilizationTimer = window.setTimeout(stopKeepingLatest, 5_000);

    return () => {
      window.cancelAnimationFrame(frame);
      window.clearTimeout(stabilizationTimer);
      area.removeEventListener("load", handleDeferredMedia, true);
      area.removeEventListener("loadedmetadata", handleDeferredMedia, true);
      area.removeEventListener("wheel", stopKeepingLatest);
      area.removeEventListener("pointerdown", stopKeepingLatest);
      area.removeEventListener("touchstart", stopKeepingLatest);
    };
  }, [activeRoomId, messages, roomLoading]);

  useLayoutEffect(() => {
    const saved = messageReturnPositionRef.current;
    if (selectedMessageId || !saved) return;
    if (saved.roomId !== activeRoomId) {
      messageReturnPositionRef.current = null;
      keepRoomAtLatestRef.current = false;
      const navigation = readNavigationHistoryState(window.history.state);
      if (navigation.returnMessageId === saved.messageId) {
        updateNavigationHistoryState(
          {
            returnMessageId: undefined,
            returnScrollTop: undefined,
            returnAnchorOffset: undefined,
          },
          "replace",
        );
      }
      return;
    }

    const frame = window.requestAnimationFrame(() => {
      const area = messageAreaRef.current;
      if (!area) return;
      const anchor = Array.from(
        area.querySelectorAll<HTMLElement>("[data-message-id]"),
      ).find((element) => element.dataset.messageId === saved.messageId);
      if (anchor && saved.anchorOffset !== null) {
        const currentOffset =
          anchor.getBoundingClientRect().top - area.getBoundingClientRect().top;
        area.scrollTop = Math.max(
          0,
          area.scrollTop + currentOffset - saved.anchorOffset,
        );
      } else {
        area.scrollTop = saved.scrollTop;
      }
      messageReturnPositionRef.current = null;
      keepRoomAtLatestRef.current = false;
      const navigation = readNavigationHistoryState(window.history.state);
      if (navigation.returnMessageId === saved.messageId) {
        updateNavigationHistoryState(
          {
            returnMessageId: undefined,
            returnScrollTop: undefined,
            returnAnchorOffset: undefined,
          },
          "replace",
        );
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, [activeRoomId, messages, selectedMessageId]);

  useLayoutEffect(() => {
    const saved = residentLinkPositionRef.current;
    if (!saved || saved.roomId !== activeRoomId) return;
    const area = messageAreaRef.current;
    if (!area) return;
    const anchor = Array.from(
      area.querySelectorAll<HTMLElement>("[data-message-id]"),
    ).find((element) => element.dataset.messageId === saved.messageId);
    if (anchor && saved.anchorOffset !== null) {
      const currentOffset =
        anchor.getBoundingClientRect().top - area.getBoundingClientRect().top;
      area.scrollTop = Math.max(
        0,
        area.scrollTop + currentOffset - saved.anchorOffset,
      );
    } else {
      area.scrollTop = saved.scrollTop;
    }
  }, [activeRoomId, messages]);

  useEffect(() => {
    if (!sendFeedback) return;
    const timer = window.setTimeout(() => setSendFeedback(""), 4_000);
    return () => window.clearTimeout(timer);
  }, [sendFeedback]);

  async function sendMessage(event: FormEvent) {
    event.preventDefault();
    if (sendingRef.current) return;
    if (!activeRoomId || (!messageBody.trim() && files.length === 0)) return;
    const body = messageBody.trim();
    const residentIds = selectedResidentIds;
    const selectedFiles = files;
    const selectedReplyTarget = replyTarget;
    const fileSelectionError = attachmentSelectionError(selectedFiles);
    if (fileSelectionError) {
      setError(fileSelectionError);
      return;
    }
    const selectedReportImage = reportImage;
    setMessageBody("");
    setReplyTarget(null);
    setSelectedResidentIds([]);
    setError("");
    sendingRef.current = true;
    setIsSending(true);
    try {
      let sent: Message;
      if (selectedFiles.length > 0) {
        setUploadProgress(0);
        const formData = new FormData();
        formData.append("body", body);
        if (noticeMode) formData.append("message_type", "notice");
        residentIds.forEach((residentId) =>
          formData.append("resident_ids", residentId),
        );
        if (selectedReplyTarget) {
          formData.append("reply_to_message_id", selectedReplyTarget.id);
        }
        formData.append("report_image", selectedReportImage ? "true" : "false");
        selectedFiles.forEach((file) => formData.append("files", file));
        sent = await apiUpload<Message>(
          `/api/rooms/${activeRoomId}/messages-with-files`,
          formData,
          setUploadProgress,
        );
      } else {
        sent = await apiFetch<Message>(`/api/rooms/${activeRoomId}/messages`, {
          method: "POST",
          body: JSON.stringify({
            body,
            ...(noticeMode ? { message_type: "notice" } : {}),
            resident_id: residentIds[0] ?? null,
            resident_ids: residentIds,
            reply_to_message_id: selectedReplyTarget?.id ?? null,
            action: null,
          }),
        });
      }
      keepRoomAtLatestRef.current = true;
      setMessages((current) =>
        current.some((item) => item.id === sent.id) ? current : [...current, sent],
      );
      setFiles([]);
      setReportImage(false);
      await markRead(activeRoomId, sent.id);
      setSendFeedback(
        noticeMode
          ? "공지로 보냈습니다."
          : selectedFiles.some((file) => file.type.startsWith("image/"))
            ? "이미지를 보냈습니다. 글자가 있으면 자동으로 판독합니다."
            : "메시지를 보냈습니다. 내용은 자동으로 분류됩니다.",
      );
      setNoticeMode(false);
      setComposerOptionsOpen(false);
    } catch (reason) {
      setMessageBody(body);
      setReplyTarget(selectedReplyTarget);
      setSelectedResidentIds(residentIds);
      setFiles(selectedFiles);
      setReportImage(selectedReportImage);
      setError(reason instanceof Error ? reason.message : "메시지를 보내지 못했습니다.");
    } finally {
      sendingRef.current = false;
      setIsSending(false);
      setUploadProgress(null);
    }
  }

  async function logout() {
    if (me?.is_dev_impersonated) {
      await returnToDevLauncher();
      return;
    }
    try {
      await apiFetch("/api/auth/logout", { method: "POST", body: "{}" });
    } catch {
      // 서버 세션이 이미 끝난 경우에도 화면에서는 로그아웃합니다.
    }
    resetSession();
  }

  async function returnToDevLauncher() {
    try {
      await apiFetch("/api/dev/return", { method: "POST", body: "{}" });
      window.location.reload();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "개발자 런처로 돌아가지 못했습니다.",
      );
    }
  }

  async function returnToReviewerGuide() {
    clearReviewerLanding();
    window.location.replace("/reviewer");
  }

  function openMessageDetail(messageId: string) {
    if (
      messagesRef.current.some(
        (message) => message.id === messageId && message.is_recalled,
      )
    ) return;
    const area = messageAreaRef.current;
    const anchor = area
      ? Array.from(
          area.querySelectorAll<HTMLElement>("[data-message-id]"),
        ).find((element) => element.dataset.messageId === messageId)
      : null;
    if (area && activeRoomRef.current) {
      messageReturnPositionRef.current = {
        roomId: activeRoomRef.current,
        messageId,
        scrollTop: area.scrollTop,
        anchorOffset: anchor
          ? anchor.getBoundingClientRect().top - area.getBoundingClientRect().top
          : null,
      };
    }
    const navigation = readNavigationHistoryState(window.history.state);
    const saved = messageReturnPositionRef.current;
    if (saved) {
      updateNavigationHistoryState(
        {
          roomId: saved.roomId,
          messageId: undefined,
          returnMessageId: saved.messageId,
          returnScrollTop: saved.scrollTop,
          returnAnchorOffset: saved.anchorOffset ?? undefined,
        },
        "replace",
      );
    }
    updateNavigationHistoryState(
      {
        roomId: activeRoomRef.current ?? navigation.roomId,
        messageId,
        aiAssistOpen: undefined,
        returnMessageId: saved?.messageId,
        returnScrollTop: saved?.scrollTop,
        returnAnchorOffset: saved?.anchorOffset ?? undefined,
      },
      navigation.messageId ? "replace" : "push",
    );
    selectedMessageRef.current = messageId;
    setSelectedMessageId(messageId);
    aiAssistMessageRef.current = null;
    setAiAssistMessage(null);
    setMessages((current) =>
      current.map((message) =>
        message.id === messageId ? { ...message, unread_comment_count: 0 } : message,
      ),
    );
    setDetailRefreshVersion((current) => current + 1);
  }

  function captureResidentLinkPosition(messageId: string) {
    const area = messageAreaRef.current;
    const roomId = activeRoomRef.current;
    if (!area || !roomId) return;
    const anchor = Array.from(
      area.querySelectorAll<HTMLElement>("[data-message-id]"),
    ).find((element) => element.dataset.messageId === messageId);
    residentLinkPositionRef.current = {
      roomId,
      messageId,
      scrollTop: area.scrollTop,
      anchorOffset: anchor
        ? anchor.getBoundingClientRect().top - area.getBoundingClientRect().top
        : null,
    };
    keepRoomAtLatestRef.current = false;
  }

  function restoreResidentLinkPosition(messageId: string) {
    const saved = residentLinkPositionRef.current;
    if (!saved || saved.messageId !== messageId) return;
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        if (residentLinkPositionRef.current !== saved) return;
        const area = messageAreaRef.current;
        if (area && activeRoomRef.current === saved.roomId) {
          const anchor = Array.from(
            area.querySelectorAll<HTMLElement>("[data-message-id]"),
          ).find((element) => element.dataset.messageId === saved.messageId);
          if (anchor && saved.anchorOffset !== null) {
            const currentOffset =
              anchor.getBoundingClientRect().top - area.getBoundingClientRect().top;
            area.scrollTop = Math.max(
              0,
              area.scrollTop + currentOffset - saved.anchorOffset,
            );
          } else {
            area.scrollTop = saved.scrollTop;
          }
        }
        residentLinkPositionRef.current = null;
        keepRoomAtLatestRef.current = false;
      });
    });
  }

  async function openWorkdeskSource(roomId: string, messageId: string) {
    if (!rooms.some((room) => room.id === roomId)) {
      setError("이 근거 대화에 접근할 수 있는 채팅방을 찾지 못했습니다.");
      return;
    }
    setWorkdeskOpen(false);
    const nextMessages = await openRoom(roomId);
    if (!nextMessages) return;
    openMessageDetail(messageId);
  }

  function closeMessageDetail() {
    const navigation = readNavigationHistoryState(window.history.state);
    const closingMessageId = selectedMessageRef.current;
    selectedMessageRef.current = null;
    setSelectedMessageId(null);
    if (navigation.messageId === closingMessageId) {
      window.history.back();
    }
  }

  function closeAiAssist() {
    const navigation = readNavigationHistoryState(window.history.state);
    if (navigation.aiAssistOpen) {
      window.history.back();
      return;
    }
    const closedAiMessageId = aiAssistMessageRef.current?.id ?? null;
    aiAssistMessageRef.current = null;
    setAiAssistMessage(null);
    if (closedAiMessageId && closedAiMessageId === selectedMessageRef.current) {
      setDetailRefreshVersion((current) => current + 1);
    }
  }

  async function refreshRoomAfterAiShare() {
    const roomId = activeRoomRef.current;
    if (roomId) {
      await openRoom(roomId, false);
      return;
    }
    await refreshRooms();
  }

  function clearMessageActionLongPress() {
    if (messageActionLongPressRef.current === null) return;
    window.clearTimeout(messageActionLongPressRef.current);
    messageActionLongPressRef.current = null;
  }

  function beginMessageActionLongPress(messageId: string) {
    clearMessageActionLongPress();
    messageActionLongPressRef.current = window.setTimeout(() => {
      setMessageActionMenuId(messageId);
      messageActionLongPressRef.current = null;
    }, 520);
  }

  function openMessageActionMenu(messageId: string) {
    clearMessageActionLongPress();
    setMessageActionMenuId((current) => (current === messageId ? null : messageId));
  }

  function prepareInlineReply(message: Message) {
    setMessageActionMenuId(null);
    setReplyTarget(message);
    window.requestAnimationFrame(() => {
      document.querySelector<HTMLTextAreaElement>(".composer-field textarea")?.focus();
    });
  }

  function messageTextForSharing(message: Message) {
    const body = messageDisplayBody(message.body, message.attachments).trim();
    return body || (message.attachments.length ? `첨부 ${message.attachments.length}개` : "");
  }

  function messagesForCopySelection(ids = copySelectionIds) {
    const selectedIds = new Set(ids);
    return messages
      .filter((message) => selectedIds.has(message.id) && !message.is_recalled)
      .sort(
        (left, right) =>
          new Date(left.created_at).getTime() - new Date(right.created_at).getTime(),
      );
  }

  function conversationTransferText(selectedMessages: Message[]) {
    return selectedMessages
      .map((message) => {
        const body = message.body.trim();
        const attachmentCount = message.attachments.length;
        const attachmentLabel = attachmentCount
          ? `[첨부 ${attachmentCount}개]`
          : "";
        const content = [body, attachmentLabel].filter(Boolean).join("\n");
        return `${formatDay(message.created_at)} ${formatTime(message.created_at)} · ${message.sender_name}\n${content}`;
      })
      .join("\n\n");
  }

  function startConversationCopy(message: Message) {
    setMessageActionMenuId(null);
    setCopySelectionActive(true);
    setCopySelectionIds([message.id]);
    setError("");
    setSendFeedback("복사하거나 공유할 대화를 더 선택할 수 있습니다.");
  }

  function toggleCopySelection(messageId: string) {
    setCopySelectionIds((current) =>
      current.includes(messageId)
        ? current.filter((id) => id !== messageId)
        : [...current, messageId],
    );
  }

  function cancelCopySelection() {
    setCopySelectionActive(false);
    setCopySelectionIds([]);
    setCopySelectionBusy(false);
  }

  async function prepareSelectedAttachments(selectedMessages: Message[]) {
    return Promise.all(
      selectedMessages.flatMap((message) =>
        message.attachments.map((attachment) =>
          prepareAttachmentShare({
            url: attachment.download_url,
            name: attachment.original_name,
            mimeType: attachment.mime_type,
          }),
        ),
      ),
    );
  }

  async function copySelectedConversation() {
    if (copySelectionBusy) return;
    const selectedMessages = messagesForCopySelection();
    if (selectedMessages.length === 0) {
      setError("복사할 대화를 한 개 이상 선택해 주세요.");
      return;
    }
    setCopySelectionBusy(true);
    setError("");
    const attachments = selectedMessages.flatMap((message) => message.attachments);
    const hasWrittenText = selectedMessages.some((message) => message.body.trim());
    try {
      if (
        selectedMessages.length === 1 &&
        attachments.length === 1 &&
        !hasWrittenText &&
        attachments[0].mime_type.startsWith("image/")
      ) {
        const prepared = (await prepareSelectedAttachments(selectedMessages))[0];
        if (
          prepared &&
          typeof ClipboardItem === "function" &&
          typeof navigator.clipboard?.write === "function"
        ) {
          await navigator.clipboard.write([
            new ClipboardItem({ [prepared.file.type]: prepared.file }),
          ]);
          setSendFeedback("이미지를 복사했습니다.");
          cancelCopySelection();
          return;
        }
        if (prepared?.nativeShareSupported && navigator.share) {
          await navigator.share({ title: "매실챗 첨부", files: [prepared.file] });
          cancelCopySelection();
          return;
        }
        if (prepared) {
          await savePreparedAttachment(prepared);
          setSendFeedback("이 기기에서는 이미지 복사를 지원하지 않아 파일로 저장했습니다.");
          cancelCopySelection();
          return;
        }
      }

      const text = conversationTransferText(selectedMessages);
      await navigator.clipboard.writeText(text);
      setSendFeedback(
        attachments.length
          ? "대화 문장은 복사했습니다. 첨부파일은 눌러 열거나 ‘다른 앱 공유’를 사용해 주세요."
          : `${selectedMessages.length}개 대화를 시간순으로 복사했습니다.`,
      );
      cancelCopySelection();
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      setError("대화 내용을 복사하지 못했습니다. 다시 시도해 주세요.");
    } finally {
      setCopySelectionBusy(false);
    }
  }

  async function shareConversationSelection(selectedMessages: Message[]) {
    const text = conversationTransferText(selectedMessages);
    const prepared = await prepareSelectedAttachments(selectedMessages);
    const shareFiles = prepared.map((item) => item.file);
    let nativeShareFailed = false;
    const canShareFiles =
      shareFiles.length > 0 &&
      typeof navigator.canShare === "function" &&
      navigator.canShare({ files: shareFiles });

    if (typeof navigator.share === "function" && (shareFiles.length === 0 || canShareFiles)) {
      try {
        await navigator.share({
          title: "매실챗 대화",
          text: text || undefined,
          files: canShareFiles ? shareFiles : undefined,
        });
        return;
      } catch (reason) {
        if (reason instanceof DOMException && reason.name === "AbortError") throw reason;
        nativeShareFailed = true;
      }
    }

    let copiedText = false;
    if (text) {
      await navigator.clipboard.writeText(text);
      copiedText = true;
    }
    if (prepared.length === 1) {
      await savePreparedAttachment(prepared[0]);
      setSendFeedback(
        nativeShareFailed && copiedText
          ? "공유 창을 열 수 없어 대화 문장은 복사하고 첨부파일은 저장했습니다."
          : copiedText
          ? "대화 문장은 복사하고 첨부파일은 저장했습니다."
          : "이 기기에서는 파일 공유를 지원하지 않아 첨부파일을 저장했습니다.",
      );
      return;
    }
    setSendFeedback(
      nativeShareFailed && copiedText
        ? "공유 창을 열 수 없어 대화 문장은 복사했습니다. 첨부파일은 하나씩 열어 저장해 주세요."
        : copiedText
        ? "대화 문장은 복사했습니다. 이 기기에서는 여러 첨부파일 공유를 지원하지 않습니다."
        : "이 기기에서는 여러 첨부파일 공유를 지원하지 않습니다. 파일을 하나씩 열어 저장해 주세요.",
    );
  }

  async function shareSelectedConversation() {
    if (copySelectionBusy) return;
    const selectedMessages = messagesForCopySelection();
    if (selectedMessages.length === 0) {
      setError("공유할 대화를 한 개 이상 선택해 주세요.");
      return;
    }
    setCopySelectionBusy(true);
    setError("");
    try {
      await shareConversationSelection(selectedMessages);
      cancelCopySelection();
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      setError("다른 앱 공유를 시작하지 못했습니다. 대화 복사를 사용해 주세요.");
    } finally {
      setCopySelectionBusy(false);
    }
  }

  async function shareMessageToAnotherApp(message: Message) {
    setMessageActionMenuId(null);
    try {
      await shareConversationSelection([message]);
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      setError("다른 앱 공유를 시작하지 못했습니다. 대화 복사를 사용해 주세요.");
    }
  }

  async function openCommentDialog(message: Message) {
    if (me?.role !== "admin") return;
    setMessageActionMenuId(null);
    setCommentTarget(message);
    setCommentBody("");
    setCommentThread([]);
    setCommentDialogError("");
    setCommentDialogBusy(true);
    try {
      const detail = await apiFetch<MessageDetail>(`/api/messages/${message.id}`);
      setCommentThread(detail.comments);
      await apiFetch(`/api/messages/${message.id}/comments/read`, {
        method: "POST",
        body: "{}",
      });
    } catch (reason) {
      setCommentDialogError(
        reason instanceof Error ? reason.message : "코멘트를 불러오지 못했습니다.",
      );
    } finally {
      setCommentDialogBusy(false);
    }
  }

  async function submitComment(event: FormEvent) {
    event.preventDefault();
    if (!commentTarget || commentDialogBusy || !commentBody.trim()) return;
    setCommentDialogBusy(true);
    setCommentDialogError("");
    try {
      const comment = await apiFetch<MessageComment>(
        `/api/messages/${commentTarget.id}/comments`,
        {
          method: "POST",
          body: JSON.stringify({ body: commentBody.trim() }),
        },
      );
      setCommentThread((current) => [...current, comment]);
      setCommentBody("");
      await refreshRoomAfterAiShare();
    } catch (reason) {
      setCommentDialogError(
        reason instanceof Error ? reason.message : "코멘트를 등록하지 못했습니다.",
      );
    } finally {
      setCommentDialogBusy(false);
    }
  }

  function openForwardDialog(message: Message) {
    setMessageActionMenuId(null);
    setForwardTarget(message);
    setSelectedForwardRoomIds([]);
    setForwardDialogError("");
  }

  async function submitForward(event: FormEvent) {
    event.preventDefault();
    if (!forwardTarget || forwardDialogBusy || selectedForwardRoomIds.length === 0) return;
    setForwardDialogBusy(true);
    setForwardDialogError("");
    try {
      const forwarded = await apiFetch<Message[]>(
        `/api/messages/${forwardTarget.id}/forward`,
        {
          method: "POST",
          body: JSON.stringify({
            room_ids: selectedForwardRoomIds,
            to_all_joined_rooms: false,
          }),
        },
      );
      setForwardTarget(null);
      setSelectedForwardRoomIds([]);
      setSendFeedback(`${forwarded.length}개 채팅방에 전달했습니다.`);
    } catch (reason) {
      setForwardDialogError(
        reason instanceof Error ? reason.message : "다른 방에 전달하지 못했습니다.",
      );
    } finally {
      setForwardDialogBusy(false);
    }
  }

  function renderMessageActions(message: Message, mine: boolean) {
    if (message.is_recalled) return null;
    const menuOpen = messageActionMenuId === message.id;
    return (
      <div
        className={`message-row-actions ${menuOpen ? "open" : ""}`}
        data-message-actions={message.id}
      >
        <button
          type="button"
          className="message-more-button"
          aria-label="메시지 메뉴 열기"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onClick={() => openMessageActionMenu(message.id)}
        >
          ⋮
        </button>
        {menuOpen ? (
          <div className="message-action-menu" role="menu" aria-label="메시지 작업">
            <button type="button" role="menuitem" onClick={() => prepareInlineReply(message)}>
              답장
            </button>
            <button
              type="button"
              role="menuitem"
              onClick={() => openForwardDialog(message)}
            >
              다른 방 전달
            </button>
            <button
              type="button"
              role="menuitem"
              onClick={() => startConversationCopy(message)}
            >
              대화내용 복사
            </button>
            {me?.role === "admin" ? (
              <>
              <button type="button" role="menuitem" onClick={() => void openCommentDialog(message)}>
                코멘트
              </button>
              <button type="button" role="menuitem" onClick={() => void shareMessageToAnotherApp(message)}>
                다른 앱 공유
              </button>
              </>
            ) : null}
            {mine && me && !me.is_reviewer_session ? (
              <button
                type="button"
                role="menuitem"
                className="danger"
                disabled={recallingMessageId === message.id}
                onClick={() => {
                  setMessageActionMenuId(null);
                  void recallMessage(message);
                }}
              >
                {recallingMessageId === message.id ? "회수 중" : "회수"}
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
    );
  }

  async function recallMessage(message: Message) {
    if (recallingMessageId || message.is_recalled) return;
    const confirmed = window.confirm(
      "이 글을 회수할까요?\n\n직원 화면에는 회수 표시만 남고, 업무 기록과 첨부 원본은 관리자 감사기록에 보존됩니다.",
    );
    if (!confirmed) return;
    setRecallingMessageId(message.id);
    setError("");
    try {
      await apiFetch<void>(`/api/messages/${message.id}/recall`, {
        method: "POST",
        body: JSON.stringify({ reason: "작성자 회수" }),
      });
      setMessages((current) =>
        current.map((item) =>
          item.id === message.id
            ? maskRecalledMessage(item)
            : item,
        ),
      );
      setSendFeedback("글을 회수했습니다. 업무 감사기록에는 안전하게 보존됩니다.");
      await refreshRooms();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "글을 회수하지 못했습니다.");
    } finally {
      setRecallingMessageId(null);
    }
  }

  async function chooseRoomForSharedFiles(roomId: string) {
    if (pendingSharedFiles.length === 0 || sharedRoomOpeningId) return;
    const sharedFiles = pendingSharedFiles;
    const selectionError = attachmentSelectionError(sharedFiles);
    if (selectionError) {
      setPendingSharedFiles([]);
      setSharedTargetError(selectionError);
      return;
    }
    setSharedRoomOpeningId(roomId);
    setSharedTargetError("");
    try {
      const opened = await openRoom(roomId);
      if (!opened) {
        setSharedTargetError("선택한 채팅방을 열지 못했습니다. 다른 방을 선택해 주세요.");
        return;
      }
      setFiles(sharedFiles);
      setReportImage(false);
      setPendingSharedFiles([]);
      setSendFeedback(
        sharedFiles.length === 1
          ? "공유한 파일 1개를 첨부했습니다. 확인한 뒤 보내기를 눌러 주세요."
          : `공유한 파일 ${sharedFiles.length}개를 첨부했습니다. 확인한 뒤 보내기를 눌러 주세요.`,
      );
    } finally {
      setSharedRoomOpeningId(null);
    }
  }

  function openSettingsPanel(panel: "security" | "notification" | "ai") {
    const navigation = readNavigationHistoryState(window.history.state);
    updateNavigationHistoryState(
      { settingsPanel: panel },
      navigation.settingsPanel ? "replace" : "push",
    );
    setSecurityOpen(panel === "security");
    setNotificationSoundOpen(panel === "notification");
    setAiConnectionOpen(panel === "ai");
  }

  function openResidentPicker() {
    const navigation = readNavigationHistoryState(window.history.state);
    updateNavigationHistoryState(
      {
        roomId: activeRoomRef.current ?? navigation.roomId,
        residentPickerOpen: true,
      },
      navigation.residentPickerOpen ? "replace" : "push",
    );
    setResidentPickerOpen(true);
  }

  function openAdminPanel(initialTab: "employees" | "organization" = "employees") {
    setAdminInitialTab(initialTab);
    setAdminOpen(true);
    void refreshAdminData();
  }

  function closeResidentPicker() {
    const navigation = readNavigationHistoryState(window.history.state);
    if (navigation.residentPickerOpen) {
      window.history.back();
      return;
    }
    setResidentPickerOpen(false);
  }

  function openStaffRoomPanel(initialRoomId: string | null = null) {
    const navigation = readNavigationHistoryState(window.history.state);
    setStaffRoomInitialRoomId(initialRoomId);
    setStaffRoomOpen(true);
    updateNavigationHistoryState(
      { staffRoomOpen: true },
      navigation.staffRoomOpen ? "replace" : "push",
    );
  }

  function closeStaffRoomPanel() {
    const navigation = readNavigationHistoryState(window.history.state);
    if (navigation.staffRoomOpen) {
      window.history.back();
      return;
    }
    setStaffRoomOpen(false);
    setStaffRoomInitialRoomId(null);
  }

  const openStaffRoomContact = useCallback(
    async (roomId: string, action: "chat" | CallMode) => {
      const nextRooms = await refreshRooms();
      const room = nextRooms.find((candidate) => candidate.id === roomId);
      if (!room) throw new Error("대화방을 찾지 못했습니다.");
      setStaffRoomOpen(false);
      setStaffRoomInitialRoomId(null);
      updateNavigationHistoryState({ staffRoomOpen: undefined }, "replace");
      const opened = await openRoom(room.id);
      if (!opened) throw new Error("대화방을 열지 못했습니다.");
      if (action !== "chat") {
        await startVoiceCall(room, action);
      }
    },
    [openRoom, refreshRooms, startVoiceCall],
  );

  function openStaffContact(target: StaffContactTarget) {
    if (me?.is_reviewer_session || target.id === me?.id) return;
    const navigation = readNavigationHistoryState(window.history.state);
    setStaffContactTarget(target);
    setStaffContactBusy(false);
    setStaffContactError("");
    setStaffContactCallChoiceOpen(false);
    setRoomCallChoiceOpen(false);
    updateNavigationHistoryState(
      {
        staffContactId: target.id,
        staffContactName: target.name,
        callChoice: undefined,
      },
      navigation.staffContactId ? "replace" : "push",
    );
  }

  function closeStaffContact() {
    const navigation = readNavigationHistoryState(window.history.state);
    if (navigation.staffContactId) {
      window.history.back();
      return;
    }
    setStaffContactTarget(null);
    setStaffContactCallChoiceOpen(false);
    setStaffContactError("");
  }

  function openStaffContactCallChoice() {
    if (!staffContactTarget) return;
    const navigation = readNavigationHistoryState(window.history.state);
    setStaffContactCallChoiceOpen(true);
    setStaffContactError("");
    updateNavigationHistoryState(
      { callChoice: "staff" },
      navigation.callChoice === "staff" ? "replace" : "push",
    );
  }

  function closeStaffContactCallChoice() {
    const navigation = readNavigationHistoryState(window.history.state);
    if (navigation.callChoice === "staff") {
      window.history.back();
      return;
    }
    setStaffContactCallChoiceOpen(false);
  }

  async function openDirectStaffContact(action: "chat" | CallMode) {
    const target = staffContactTarget;
    if (!target || staffContactBusy) return;
    setStaffContactBusy(true);
    setStaffContactError("");
    try {
      const room = await apiFetch<StaffRoom>("/api/staff-rooms", {
        method: "POST",
        body: JSON.stringify({ name: null, member_ids: [target.id] }),
      });
      await openStaffRoomContact(room.id, action);
      setStaffContactTarget(null);
      setStaffContactCallChoiceOpen(false);
      updateNavigationHistoryState(
        {
          staffContactId: undefined,
          staffContactName: undefined,
          callChoice: undefined,
        },
        "replace",
      );
    } catch (reason) {
      setStaffContactError(
        reason instanceof Error ? reason.message : "직원 대화방을 열지 못했습니다.",
      );
    } finally {
      setStaffContactBusy(false);
    }
  }

  async function openRoomCallChoice() {
    const navigation = readNavigationHistoryState(window.history.state);
    setRoomCallChoiceOpen(true);
    setRoomCallMembersLoading(true);
    setRoomCallChoiceError("");
    setRoomCallMembers([]);
    setSelectedRoomCallMemberIds([]);
    updateNavigationHistoryState(
      { callChoice: "room" },
      navigation.callChoice === "room" ? "replace" : "push",
    );
    if (!activeRoom) {
      setRoomCallMembersLoading(false);
      setRoomCallChoiceError("대화방을 다시 선택해 주세요.");
      return;
    }
    try {
      const members = await apiFetch<RoomMember[]>(`/api/rooms/${activeRoom.id}/members`);
      const recipients = members.filter((member) => member.id !== currentUserId);
      setRoomCallMembers(recipients);
      setSelectedRoomCallMemberIds(recipients.map((member) => member.id));
      if (!recipients.length) {
        setRoomCallChoiceError("통화할 다른 직원이 없습니다.");
      }
    } catch (reason) {
      setRoomCallChoiceError(
        reason instanceof Error ? reason.message : "대화방 직원을 확인하지 못했습니다.",
      );
    } finally {
      setRoomCallMembersLoading(false);
    }
  }

  function closeRoomCallChoice() {
    const navigation = readNavigationHistoryState(window.history.state);
    if (navigation.callChoice === "room") {
      window.history.back();
      return;
    }
    setRoomCallChoiceOpen(false);
    setRoomCallChoiceError("");
  }

  function startActiveRoomCall(room: Room, mode: CallMode) {
    const participantCount = selectedRoomCallMemberIds.length + 1;
    const callLabel = mode === "video" ? "영상통화" : "음성통화";
    if (
      !window.confirm(
        `${participantCount}명이 참여하는 ${callLabel}를 시작할까요?`,
      )
    ) {
      return;
    }
    setRoomCallChoiceOpen(false);
    if (readNavigationHistoryState(window.history.state).callChoice === "room") {
      window.history.back();
    }
    void startVoiceCall(room, mode, selectedRoomCallMemberIds);
  }

  function closeSettingsPanel(panel: "security" | "notification" | "ai") {
    const navigation = readNavigationHistoryState(window.history.state);
    if (navigation.settingsPanel === panel) {
      window.history.back();
      return;
    }
    if (panel === "security") {
      setSecurityOpen(false);
    } else if (panel === "notification") {
      setNotificationSoundOpen(false);
    } else {
      setAiConnectionOpen(false);
    }
  }

  function closeActiveRoom() {
    const navigation = readNavigationHistoryState(window.history.state);
    if (navigation.roomId === activeRoomRef.current && !navigation.messageId) {
      window.history.back();
      return;
    }
    activeRoomRef.current = null;
    setActiveRoomId(null);
    setMessages([]);
    setResidents([]);
    setSelectedResidentIds([]);
    setFiles([]);
    setReportImage(false);
    setRoomFilesOpen(false);
    setComposerOptionsOpen(false);
  }

  if (loading) {
    return (
      <main className="loading-page">
        <span className="loading-mark" aria-hidden="true">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/brand/silvermedical-logo.jpg" alt="" />
        </span>
        <p>채팅방을 준비하고 있습니다…</p>
      </main>
    );
  }
  if (sessionCheckUnavailable) {
    return (
      <main className="loading-page session-recovery-page">
        <span className="loading-mark" aria-hidden="true">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/brand/silvermedical-logo.jpg" alt="" />
        </span>
        <strong>서버에 다시 연결하고 있습니다…</strong>
        <button
          type="button"
          className="button button-primary button-large"
          onClick={() => void checkCurrentSession()}
        >
          다시 연결
        </button>
      </main>
    );
  }
  if (!me) {
    return (
      <LoginScreen
        sessionNotice={loginNotice}
        onLogin={(user) => {
          setLoginNotice("");
          setMe(user);
        }}
      />
    );
  }
  if (me.is_dev_launcher) {
    return <DeveloperLauncher controller={me} onLogout={() => void logout()} />;
  }
  if (me.must_change_password) {
    return (
      <SecurityPanel
        user={me}
        mandatory
        onUserChanged={setMe}
        onClose={() => undefined}
        onLogout={() => void logout()}
      />
    );
  }

  const activeRoom = rooms.find((room) => room.id === activeRoomId) ?? null;
  const profileDescription = [
    me.floor?.name,
    me.team?.name,
    me.job_name,
    me.position_title,
  ]
    .filter(Boolean)
    .join(" · ");
  const reviewerInitialDate =
    me.is_reviewer_session && rooms[0]?.last_message_at
      ? dateInputValue(rooms[0].last_message_at)
      : undefined;
  let previousDay = "";

  return (
    <>
      {me.is_reviewer_session ? (
        <div className="reviewer-session-banner" role="status">
          <span>
            <strong>
              {mentorFullReview
                ? "멘토 전체 기능 검토 · DEV 가상자료"
                : "AI 챌린지 심사위원 체험 중"}
            </strong>
            <small>
              {mentorFullReview
                ? "실제 DEV 메뉴를 검토할 수 있으며 관리 변경·비밀 설정·공식 저장은 잠겨 있습니다."
                : "모든 직원과 어르신은 가명입니다."}
            </small>
          </span>
          {!mentorFullReview ? (
            <button onClick={() => void returnToReviewerGuide()}>
              체험 선택으로 돌아가기
            </button>
          ) : null}
        </div>
      ) : me.is_dev_impersonated ? (
        <div className="dev-impersonation-banner" role="status">
          <strong>개발 시험 중 · {me.full_name} 화면</strong>
          <button onClick={() => void returnToDevLauncher()}>
            사용자 런처로 돌아가기
          </button>
        </div>
      ) : null}
      {pendingSharedFiles.length > 0 || sharedTargetError ? (
        <div className="share-room-layer" role="presentation">
          <section
            className="share-room-picker"
            role="dialog"
            aria-modal="true"
            aria-labelledby="share-room-title"
          >
            <header className="share-room-header">
              <div>
                <span>파일 공유</span>
                <h2 id="share-room-title">
                  {sharedTargetError
                    ? "파일을 받지 못했습니다"
                    : `파일 ${pendingSharedFiles.length}개를 어디에 보낼까요?`}
                </h2>
              </div>
              <button
                type="button"
                className="share-room-close"
                aria-label="파일 공유 취소"
                onClick={() => {
                  setPendingSharedFiles([]);
                  setSharedTargetError("");
                }}
              >
                ×
              </button>
            </header>
            {sharedTargetError ? (
              <div className="share-room-error">
                <p>{sharedTargetError}</p>
                <button type="button" onClick={() => setSharedTargetError("")}>
                  확인
                </button>
              </div>
            ) : (
              <>
                <p className="share-room-guide">
                  보낼 방을 선택하면 파일이 첨부됩니다. 아직 전송되지는 않습니다.
                </p>
                <div className="share-room-list">
                  {rooms.length === 0 ? (
                    <p className="share-room-loading">채팅방을 불러오는 중입니다…</p>
                  ) : (
                    rooms.map((room) => (
                      <button
                        key={room.id}
                        type="button"
                        className="share-room-option"
                        disabled={sharedRoomOpeningId !== null}
                        onClick={() => void chooseRoomForSharedFiles(room.id)}
                      >
                        <span className={`room-icon kind-${room.kind}`}>
                          {room.kind === "all"
                            ? "전"
                            : roomDisplayName(room, me).slice(0, 1)}
                        </span>
                        <span>
                          <strong>{roomDisplayName(room, me)}</strong>
                          <small>{kindLabels[room.kind]} 채팅방</small>
                        </span>
                        <b>{sharedRoomOpeningId === room.id ? "여는 중…" : "선택"}</b>
                      </button>
                    ))
                  )}
                </div>
                <button
                  type="button"
                  className="share-room-cancel"
                  disabled={sharedRoomOpeningId !== null}
                  onClick={() => setPendingSharedFiles([])}
                >
                  공유 취소
                </button>
              </>
            )}
          </section>
        </div>
      ) : null}
      <VoiceCallOverlay
        incoming={voiceCall.incoming}
        active={voiceCall.active}
        muted={voiceCall.muted}
        cameraOff={voiceCall.cameraOff}
        switchingMode={voiceCall.switchingMode}
        facingMode={voiceCall.facingMode}
        error={voiceCall.error}
        localStream={voiceCall.localStream}
        remoteStreams={voiceCall.remoteStreams}
        canChangeAudioRoute={isNativeMesilApp()}
        speakerphoneOn={speakerphoneOn}
        onAccept={() => void voiceCall.acceptIncoming()}
        onDecline={voiceCall.declineIncoming}
        onEnd={voiceCall.endCall}
        onToggleMuted={voiceCall.toggleMuted}
        onToggleCamera={() => void voiceCall.toggleCamera()}
        onSwitchCamera={() => void voiceCall.switchCamera()}
        onToggleAudioRoute={toggleCallAudioRoute}
        onSwitchCallMode={() => void voiceCall.switchCallMode()}
        onDismissError={voiceCall.dismissError}
      />
      <main
        className={`chat-shell ${activeRoom ? "room-open" : ""} ${
          me.is_dev_impersonated ? "dev-impersonating" : ""
        } ${me.is_reviewer_session ? "reviewer-session" : ""}`}
      >
      <aside className="room-sidebar">
        <header className="sidebar-header">
          <div className="compact-brand">
            <span className="brand-mark" aria-hidden="true">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src="/brand/silvermedical-logo.jpg" alt="" />
            </span>
            <div>
              <strong>MESIL_Chat</strong>
              <small>메실채팅</small>
              {codedSyntheticMode ? (
                <small className="coded-synthetic-badge">
                  발표 · 합성자료
                </small>
              ) : null}
            </div>
          </div>
          <div className={`connection ${connectionState}`}>
            <span />
            {connectionState === "online"
              ? "연결됨"
              : connectionState === "connecting"
                ? "연결 중"
                : "재연결 중"}
          </div>
        </header>
        {codedSyntheticMode ? (
          <div className="coded-synthetic-notice" role="status">
            결선 시연용 합성자료 · 실제 인물 및 기록과 무관
          </div>
        ) : null}
        <section className="profile-strip">
          <div className="profile-identity-row">
            <span className="avatar large">{me.full_name.slice(0, 1)}</span>
            <div className="profile-copy">
              <strong>{me.full_name}</strong>
              {profileDescription ? <small>{profileDescription}</small> : null}
            </div>
            {canViewAdmin ? (
              <button
                className="icon-button admin-button"
                onClick={() => openAdminPanel()}
              >
                관리
              </button>
            ) : null}
          </div>
          <div className="profile-task-row">
            {(me.is_reviewer_session
              ? (me.reviewer_experience === "social_worker" || mentorFullReview) &&
                me.can_process_records
              : me.role === "admin" || me.can_process_records) ? (
              <button
                className="icon-button workdesk-button"
                onClick={() => setWorkdeskOpen(true)}
              >
                AI 돌봄 브리핑
              </button>
            ) : null}
            {codedSyntheticMode && canViewAdmin ? (
              <button
                className="icon-button record-review-button"
                onClick={() => setRecordReviewOpen(true)}
              >
                기록 검토
              </button>
            ) : null}
          </div>
        </section>
        {organizationSetup.required ? (
          <section
            className="institution-setup-checklist"
            aria-labelledby="institution-setup-title"
            role="status"
          >
            <span>처음 시작하기</span>
            <strong id="institution-setup-title">기관 기본 설정이 필요합니다.</strong>
            <p>
              생활실·방·구역을 1개 이상 등록하면 직원과 어르신을 배정할 수 있습니다.
              기본 직종·직위는 기관에 맞게 그대로 쓰거나 수정할 수 있습니다.
            </p>
            <button type="button" onClick={() => openAdminPanel("organization")}>
              기관 설정 시작
            </button>
          </section>
        ) : null}
        <div className="room-list-heading">
          <h1>내 채팅방</h1>
          <span>{rooms.length}</span>
          {!me.is_reviewer_session ? (
            <button
              type="button"
              className="new-staff-room-button"
              onClick={() => openStaffRoomPanel()}
            >
              직원 대화·통화
            </button>
          ) : null}
        </div>
        <div className="room-list">
          {rooms.length === 0 ? (
            <div className="empty-room-list">
              <strong>배정된 채팅방이 없습니다.</strong>
              <p>관리자에게 소속정보를 확인해 달라고 요청하세요.</p>
            </div>
          ) : (
            rooms.map((room) => (
              <button
                key={room.id}
                className={`room-row ${activeRoomId === room.id ? "selected" : ""}`}
                onClick={() => void openRoom(room.id)}
              >
                <span className={`room-icon kind-${room.kind}`}>
                  {room.kind === "all"
                    ? "전"
                    : roomDisplayName(room, me).slice(0, 1)}
                </span>
                <span className="room-copy">
                  <span className="room-title-line">
                    <strong>{roomDisplayName(room, me)}</strong>
                    <small>{room.last_message_at ? formatTime(room.last_message_at) : ""}</small>
                  </span>
                  <span className="room-preview">
                    {room.last_message ?? `${kindLabels[room.kind]} 채팅방`}
                  </span>
                </span>
                {room.unread_count > 0 ? (
                  <span className="unread-badge">{Math.min(room.unread_count, 99)}</span>
                ) : null}
              </button>
            ))
          )}
        </div>
        <footer className="sidebar-footer">
          {!me.is_reviewer_session || mentorFullReview ? (
            <>
              {canViewAdmin ? (
                <button
                  className="text-button"
                  onClick={() => openSettingsPanel("ai")}
                >
                  AI 설정 및 연결
                </button>
              ) : null}
              <button
                className="text-button"
                onClick={() => openSettingsPanel("notification")}
              >
                알림 설정
              </button>
              <button
                className="text-button"
                onClick={() => openSettingsPanel("security")}
              >
                보안 설정
              </button>
            </>
          ) : null}
          <button className="text-button" onClick={() => void logout()}>
            {me.is_dev_impersonated ? "런처로 돌아가기" : "로그아웃"}
          </button>
        </footer>
      </aside>

      <section className="chat-panel">
        {activeRoom ? (
          <>
            <header className="chat-header">
              <button
                className="icon-button mobile-back"
                onClick={closeActiveRoom}
                aria-label="채팅방 목록으로"
              >
                ‹
              </button>
              <div>
                <h2>{roomDisplayName(activeRoom, me)}</h2>
                <p>{kindLabels[activeRoom.kind]} 채팅방</p>
              </div>
              <div className="chat-header-actions">
                <button
                  type="button"
                  className="button button-secondary room-files-open"
                  onClick={() => setRoomFilesOpen(true)}
                >
                  파일
                </button>
                {voiceCall.available && activeRoom.kind === "custom" ? (
                  <button
                    type="button"
                    className="button button-primary voice-call-start"
                    disabled={Boolean(voiceCall.active || voiceCall.incoming)}
                    onClick={() => void openRoomCallChoice()}
                  >
                    통화
                  </button>
                ) : mentorFullReview && activeRoom.kind === "custom" ? (
                  <button
                    type="button"
                    className="button button-secondary voice-call-start"
                    disabled
                    title="멘토 검토 계정에서는 통화 상태와 설정을 볼 수 있지만 실제 통화 시작은 잠겨 있습니다."
                  >
                    통화 · 검토 잠금
                  </button>
                ) : null}
                {activeRoom.kind === "custom" && !me.is_reviewer_session ? (
                  <button
                    type="button"
                    className="button button-secondary room-search-open"
                    onClick={() => openStaffRoomPanel(activeRoom.id)}
                  >
                    참여자
                  </button>
                ) : null}
                <button
                  type="button"
                  className="button button-secondary room-search-open"
                  onClick={() => setRoomSearchOpen(true)}
                >
                  대화 검색
                </button>
              </div>
            </header>
            <div ref={messageAreaRef} className="message-area" aria-live="polite">
              {!roomLoading && messages.length > 0 && hasOlderMessages ? (
                <button
                  type="button"
                  className="older-messages-button"
                  disabled={loadingOlderMessages}
                  onClick={() => void loadOlderMessages()}
                >
                  {loadingOlderMessages ? "이전 대화 불러오는 중…" : "이전 대화 보기"}
                </button>
              ) : null}
              {roomLoading ? (
                <div className="empty-messages" role="status">
                  <span>…</span>
                  <h3>대화를 불러오는 중입니다.</h3>
                </div>
              ) : messages.length === 0 ? (
                <div className="empty-messages">
                  <span>{roomDisplayName(activeRoom, me).slice(0, 1)}</span>
                  <h3>첫 업무대화를 시작하세요.</h3>
                  <p>짧고 명확하게 작성하고, 개인정보는 필요한 범위에서만 사용하세요.</p>
                </div>
              ) : (
                messages.map((message) => {
                  const day = new Date(message.created_at).toDateString();
                  const showDay = day !== previousDay;
                  previousDay = day;
                  const mine = message.sender_id === me.id;
                  if (message.is_recalled) {
                    return (
                      <div
                        key={message.id}
                        className="message-item-shell recalled-message-shell"
                        data-message-id={message.id}
                      >
                        {showDay ? <div className="day-divider">{formatDay(message.created_at)}</div> : null}
                        <article className={`message-row recalled-message-row ${mine ? "mine" : ""}`}>
                          <div className="message-stack recalled-message-stack">
                            <div className="bubble-line recalled-bubble-line">
                              {mine ? (
                                <time className="recalled-message-time">{formatTime(message.created_at)}</time>
                              ) : null}
                              <div className="message-bubble recalled" role="status">
                                <span>회수한 메시지입니다.</span>
                              </div>
                              {!mine ? (
                                <time className="recalled-message-time">{formatTime(message.created_at)}</time>
                              ) : null}
                            </div>
                          </div>
                        </article>
                      </div>
                    );
                  }
                  const displayBody = messageDisplayBody(
                    message.body,
                    message.attachments,
                  );
                  const longMessage = isLongMessageBody(displayBody);
                  const hasImageAttachment = message.attachments.some((attachment) =>
                    attachment.mime_type.startsWith("image/"),
                  );
                  const hasAudioAttachment = message.attachments.some((attachment) =>
                    attachment.mime_type.startsWith("audio/"),
                  );
                  const canProcessAttachmentText =
                    (mine && hasImageAttachment) || me.role === "admin" || me.can_process_records;
                  const attachmentReviewLabel = canProcessAttachmentText
                    ? hasImageAttachment && hasAudioAttachment
                      ? "이미지·음성 텍스트 확인·수정"
                      : hasImageAttachment
                        ? "이미지 글자 판독·수정"
                        : hasAudioAttachment
                          ? "음성 받아쓰기 확인·수정"
                          : message.attachments.length > 0
                            ? "첨부 보기·저장"
                            : ""
                    : message.attachments.length > 0
                      ? "첨부 보기·저장"
                      : "";
                  return (
                    <div
                      key={message.id}
                      className={`message-item-shell ${
                        copySelectionActive ? "copy-selecting" : ""
                      }`}
                      data-message-id={message.id}
                      onContextMenu={(event) => {
                        if (message.is_recalled) return;
                        event.preventDefault();
                        openMessageActionMenu(message.id);
                      }}
                      onPointerDown={(event) => {
                        if (!message.is_recalled && event.pointerType !== "mouse") {
                          beginMessageActionLongPress(message.id);
                        }
                      }}
                      onPointerMove={clearMessageActionLongPress}
                      onPointerUp={clearMessageActionLongPress}
                      onPointerCancel={clearMessageActionLongPress}
                    >
                      {copySelectionActive && !message.is_recalled ? (
                        <label className="message-copy-select">
                          <input
                            type="checkbox"
                            checked={copySelectionIds.includes(message.id)}
                            onChange={() => toggleCopySelection(message.id)}
                            aria-label={`${formatTime(message.created_at)} ${message.sender_name} 대화 선택`}
                          />
                          <span aria-hidden="true">✓</span>
                        </label>
                      ) : null}
                      {showDay ? <div className="day-divider">{formatDay(message.created_at)}</div> : null}
                      {message.message_type === "notice" ? (
                        <article className="notice-message">
                          <div className="notice-trigger">
                            <div>
                              <span>공지</span>
                              {!mine && !me.is_reviewer_session ? (
                                <button
                                  type="button"
                                  className="message-sender-name"
                                  onClick={() =>
                                    openStaffContact({
                                      id: message.sender_id,
                                      name: message.sender_name,
                                    })
                                  }
                                >
                                  {message.sender_name}
                                </button>
                              ) : (
                                <strong>{message.sender_name}</strong>
                              )}
                              <time>{formatTime(message.created_at)}</time>
                            </div>
                            <ResidentLinkReview
                              message={message}
                              canEdit={me.role === "admin" || me.can_process_records}
                              onManagerOpen={captureResidentLinkPosition}
                              onManagerClose={restoreResidentLinkPosition}
                              onChanged={updated => setMessages(current => current.map(item => item.id === updated.id ? updated : item))}
                            />
                            {message.reply_to ? (
                              <button
                                type="button"
                                className="reply-quote"
                                onClick={() => openMessageDetail(message.reply_to!.message_id)}
                                aria-label={`${message.reply_to.sender_name}님의 원문 열기`}
                              >
                                <strong>{message.reply_to.sender_name}</strong>
                                <span>{message.reply_to.body}</span>
                              </button>
                            ) : null}
                            {message.forwarded_from ? (
                              <span className="forwarded-label">
                                전달 · {message.forwarded_from.room_name}
                              </span>
                            ) : null}
                            {message.message_type in messageNatureLabels ? (
                              <span
                                className={`message-nature-badge nature-${message.message_type}`}
                              >
                                {
                                  String(messageNatureLabels[
                                    message.message_type as keyof typeof messageNatureLabels
                                  ])
                                }
                              </span>
                            ) : null}
                            {displayBody ? (
                              longMessage ? (
                                <button
                                  type="button"
                                  className="long-message-preview notice-long-message-preview"
                                  aria-label="긴 글 전체 내용 열기"
                                  onClick={() => openMessageDetail(message.id)}
                                >
                                  <span className="long-message-preview-body">{displayBody}</span>
                                </button>
                              ) : (
                                <p>{displayBody}</p>
                              )
                            ) : null}
                            {message.attachments.length > 0 ? (
                              <div
                                className={`bubble-attachments ${
                                  message.attachments.length === 1 ? "single" : ""
                                }`}
                              >
                                {message.attachments.map((attachment) => (
                                  <AttachmentDisplay
                                    key={attachment.id}
                                    attachment={attachment}
                                    galleryAttachments={message.attachments}
                                    compact
                                  />
                                ))}
                              </div>
                            ) : null}
                            {attachmentReviewLabel ? (
                              <button
                                type="button"
                                className="message-detail-trigger attachment-review-entry"
                                data-attachment-review-entry={message.id}
                                onClick={() => openMessageDetail(message.id)}
                              >
                                {attachmentReviewLabel}
                              </button>
                            ) : null}
                            {message.action_item ? (
                              <MessageActionBadge actionItem={message.action_item} />
                            ) : null}
                            {me.role === "admin" ? (
                              <LatestCommentPreview
                                message={message}
                                onOpen={() => void openCommentDialog(message)}
                              />
                            ) : null}
                          </div>
                          {renderMessageActions(message, mine)}
                        </article>
                      ) : (
                        <article className={`message-row ${mine ? "mine" : ""}`}>
                          {!mine && !me.is_reviewer_session ? (
                            <button
                              type="button"
                              className="avatar message-sender-avatar"
                              onClick={() =>
                                openStaffContact({
                                  id: message.sender_id,
                                  name: message.sender_name,
                                })
                              }
                              aria-label={`${message.sender_name} 직원 메뉴`}
                            >
                              {message.sender_name.slice(0, 1)}
                            </button>
                          ) : !mine ? (
                            <span className="avatar">{message.sender_name.slice(0, 1)}</span>
                          ) : null}
                          <div className="message-stack">
                            {!mine && !me.is_reviewer_session ? (
                              <button
                                type="button"
                                className="message-sender-name"
                                onClick={() =>
                                  openStaffContact({
                                    id: message.sender_id,
                                    name: message.sender_name,
                                  })
                                }
                              >
                                {message.sender_name}
                              </button>
                            ) : !mine ? (
                              <strong>{message.sender_name}</strong>
                            ) : null}
                            <div className="bubble-line">
                              {mine ? (
                                <div className="message-meta">
                                  <span className="message-engagement">
                                    {engagementLabel(message)}
                                  </span>
                                  <time>{formatTime(message.created_at)}</time>
                                </div>
                              ) : null}
                              <div
                                className={`message-bubble ${
                                  message.is_recalled ? "recalled" : ""
                                }`}
                              >
                                <ResidentLinkReview
                                  message={message}
                                  canEdit={me.role === "admin" || me.can_process_records}
                                  onManagerOpen={captureResidentLinkPosition}
                                  onManagerClose={restoreResidentLinkPosition}
                                  onChanged={updated => setMessages(current => current.map(item => item.id === updated.id ? updated : item))}
                                />
                                {message.reply_to ? (
                                  <button
                                    type="button"
                                    className="reply-quote"
                                    onClick={() => openMessageDetail(message.reply_to!.message_id)}
                                    aria-label={`${message.reply_to.sender_name}님의 원문 열기`}
                                  >
                                    <strong>{message.reply_to.sender_name}</strong>
                                    <span>{message.reply_to.body}</span>
                                  </button>
                                ) : null}
                                {message.forwarded_from ? (
                                  <span className="forwarded-label">
                                    전달 · {message.forwarded_from.room_name}
                                  </span>
                                ) : null}
                                {message.message_type in messageNatureLabels ? (
                                  <span
                                    className={`message-nature-badge nature-${message.message_type}`}
                                  >
                                    {
                                      messageNatureLabels[
                                        message.message_type as keyof typeof messageNatureLabels
                                      ]
                                    }
                                  </span>
                                ) : null}
                                {displayBody ? (
                                  longMessage ? (
                                    <button
                                      type="button"
                                      className="bubble-text long-message-preview"
                                      aria-label="긴 글 전체 내용 열기"
                                      onClick={() => openMessageDetail(message.id)}
                                    >
                                      <span className="long-message-preview-body">{displayBody}</span>
                                    </button>
                                  ) : (
                                    <span className="bubble-text">{displayBody}</span>
                                  )
                                ) : null}
                                {message.attachments.length > 0 ? (
                                  <span
                                    className={`bubble-attachments ${
                                      message.attachments.length === 1 ? "single" : ""
                                    }`}
                                  >
                                    {message.attachments.map((attachment) => (
                                      <AttachmentDisplay
                                        key={attachment.id}
                                        attachment={attachment}
                                        galleryAttachments={message.attachments}
                                        compact
                                      />
                                    ))}
                                  </span>
                                ) : null}
                                {attachmentReviewLabel ? (
                                  <button
                                    type="button"
                                    className="message-detail-trigger attachment-review-entry"
                                    data-attachment-review-entry={message.id}
                                    onClick={() => openMessageDetail(message.id)}
                                  >
                                    {attachmentReviewLabel}
                                  </button>
                                ) : null}
                                {message.action_item ? (
                                  <MessageActionBadge actionItem={message.action_item} />
                                ) : null}
                                {me.role === "admin" ? (
                                  <LatestCommentPreview
                                    message={message}
                                    onOpen={() => void openCommentDialog(message)}
                                  />
                                ) : null}
                              </div>
                              {!mine ? (
                                <div className="message-meta">
                                  <span className="message-engagement">
                                    {engagementLabel(message)}
                                  </span>
                                  <time>{formatTime(message.created_at)}</time>
                                </div>
                              ) : null}
                            </div>
                            {renderMessageActions(message, mine)}
                          </div>
                        </article>
                      )}
                    </div>
                  );
                })
              )}
              <div ref={messageEndRef} />
            </div>
            {error ? (
              <p className="chat-error" role="alert">
                {error}
              </p>
            ) : null}
            {sendFeedback ? (
              <p className="composer-feedback" role="status">
                {sendFeedback}
              </p>
            ) : null}
            {copySelectionActive ? (
              <section
                className="message-copy-toolbar"
                role="region"
                aria-label="대화내용 복사"
              >
                <strong>선택한 대화 {copySelectionIds.length}개</strong>
                <span>여러 개를 고르면 시간순으로 정리합니다.</span>
                <div>
                  <button
                    type="button"
                    onClick={() => void copySelectedConversation()}
                    disabled={copySelectionBusy || copySelectionIds.length === 0}
                    aria-label="선택한 대화 복사"
                  >
                    {copySelectionBusy ? "처리 중…" : "복사"}
                  </button>
                  <button
                    type="button"
                    onClick={() => void shareSelectedConversation()}
                    disabled={copySelectionBusy || copySelectionIds.length === 0}
                    aria-label="선택한 대화 다른 앱 공유"
                  >
                    다른 앱 공유
                  </button>
                  <button type="button" onClick={cancelCopySelection}>
                    취소
                  </button>
                </div>
              </section>
            ) : null}
            <form
              className="composer"
              onSubmit={sendMessage}
              aria-busy={isSending}
              data-app-update-dirty={
                messageBody.trim().length > 0 || files.length > 0 || isSending
                  ? "true"
                  : undefined
              }
            >
              {replyTarget ? (
                <div className="composer-reply-preview" role="status">
                  <span>
                    <strong>{replyTarget.sender_name}님에게 답장</strong>
                    <small>{messageDisplayBody(replyTarget.body, replyTarget.attachments)}</small>
                  </span>
                  <button
                    type="button"
                    onClick={() => setReplyTarget(null)}
                    aria-label="답장 취소"
                  >
                    ×
                  </button>
                </div>
              ) : null}
              <div className="composer-tools">
                <label
                  className={`file-picker ${files.length ? "selected" : ""} ${
                    mentorFullReview ? "review-locked" : ""
                  }`}
                  title={
                    mentorFullReview
                      ? "멘토 검토 계정은 기존 가상 첨부를 볼 수 있지만 새 파일 업로드는 잠겨 있습니다."
                      : undefined
                  }
                >
                  {mentorFullReview
                    ? "새 파일 업로드 · 검토 잠금"
                    : `사진·음성·파일${files.length ? ` 추가 (${files.length})` : ""}`}
                  <input
                    type="file"
                    accept={CHAT_ATTACHMENT_ACCEPT}
                    multiple
                    disabled={isSending || mentorFullReview}
                    onChange={(event) => {
                      const added = Array.from(event.target.files ?? []);
                      const selected = mergeAttachmentSelections(files, added);
                      const selectionError = attachmentSelectionError(selected);
                      if (selectionError) {
                        setError(selectionError);
                        event.currentTarget.value = "";
                        return;
                      }
                      setError("");
                      setFiles(selected);
                      if (!selected.some((file) => file.type.startsWith("image/"))) {
                        setReportImage(false);
                      }
                      event.currentTarget.value = "";
                    }}
                  />
                </label>
                {files.length > 0 ? (
                  <button
                    type="button"
                    className="clear-files"
                    disabled={isSending}
                    onClick={() => {
                      setFiles([]);
                      setReportImage(false);
                    }}
                  >
                    파일 취소
                  </button>
                ) : null}
                {me.role === "admin" || me.can_process_records ? (
                  <>
                    <button
                      type="button"
                      className={`composer-options-toggle ${
                        composerOptionsOpen ? "active" : ""
                      }`}
                      onClick={() => setComposerOptionsOpen((current) => !current)}
                      aria-expanded={composerOptionsOpen}
                    >
                      {composerOptionsOpen ? "설정 닫기" : "추가 설정"}
                    </button>
                    {composerOptionsOpen ||
                    selectedResidentIds.length > 0 ||
                    noticeMode ? (
                      <div className="composer-optional-tools">
                        <button
                          type="button"
                          className={`resident-picker-button ${
                            selectedResidentIds.length ? "selected" : ""
                          }`}
                          disabled={isSending}
                          onClick={openResidentPicker}
                          aria-haspopup="dialog"
                        >
                          {selectedResidentButtonLabel(selectedResidentIds, residents)}
                        </button>
                        {me.role === "admin" ? (
                          <button
                            type="button"
                            className={`notice-toggle ${noticeMode ? "active" : ""}`}
                            onClick={() => setNoticeMode((current) => !current)}
                            aria-pressed={noticeMode}
                          >
                            {noticeMode ? "공지 작성 중" : "공지"}
                          </button>
                        ) : null}
                      </div>
                    ) : null}
                  </>
                ) : (
                  <small className="composer-chat-first-note">
                    어르신을 먼저 고르지 않아도 바로 보낼 수 있습니다. 보낸 직원이나 담당자가 나중에 확인할 수 있습니다.
                  </small>
                )}
              </div>
              {mentorFullReview ? (
                <p className="mentor-review-lock-note" role="note">
                  기존 가상 사진·음성·첨부와 처리 결과는 열람할 수 있습니다. 실제 자료 업로드는 개인정보 보호를 위해 잠겨 있습니다.
                </p>
              ) : null}
              {files.length > 0 ? (
                <div className="selected-files" aria-live="polite">
                  <div className="selected-files-summary">
                    <strong>
                      보낼 파일 {files.length}개 · 총 {totalAttachmentMegabytes(files)}MB
                    </strong>
                    <span>파일당 최대 30MB · 전체 최대 100MB</span>
                  </div>
                  <ul className="selected-file-list" aria-label="선택한 첨부파일">
                    {files.map((file, index) => (
                      <li key={attachmentSelectionKey(file)}>
                        <span title={file.name}>{file.name}</span>
                        <button
                          type="button"
                          disabled={isSending}
                          aria-label={`${file.name} 첨부 취소`}
                          onClick={() => {
                            const remaining = files.filter(
                              (_selectedFile, selectedIndex) => selectedIndex !== index,
                            );
                            setFiles(remaining);
                            if (
                              !remaining.some((selectedFile) =>
                                selectedFile.type.startsWith("image/"),
                              )
                            ) {
                              setReportImage(false);
                            }
                          }}
                        >
                          빼기
                        </button>
                      </li>
                    ))}
                  </ul>
                  {files.some((file) => file.type.startsWith("image/")) ? (
                    <div className="photo-reading-selection">
                      <span>{reportImage ? "사진을 보내면 글자도 읽습니다." : "일반 사진으로 보냅니다."}</span>
                      <button type="button" className="photo-reading-choice" aria-pressed={reportImage} disabled={isSending}
                        onClick={() => setReportImage(value => !value)}>{reportImage ? "글자 읽기 취소" : "글자도 읽기"}</button>
                    </div>
                  ) : null}
                </div>
              ) : null}
              {uploadProgress !== null ? (
                <div className="upload-progress" role="status" aria-live="polite">
                  <div>
                    <strong>
                      {uploadProgress < 100
                        ? "파일을 보내는 중"
                        : "서버에 안전하게 저장하는 중"}
                    </strong>
                    <span>{uploadProgress}%</span>
                  </div>
                  <progress max={100} value={uploadProgress}>
                    {uploadProgress}%
                  </progress>
                  <small>
                    {uploadProgress < 100
                      ? "이 화면을 닫지 말고 잠시 기다려 주세요."
                      : "큰 PDF는 저장과 확인에 몇 초 더 걸릴 수 있습니다."}
                  </small>
                </div>
              ) : null}
              <label className="composer-field">
                <span className="sr-only">메시지</span>
                <textarea
                  value={messageBody}
                  onChange={(event) => {
                    setMessageBody(event.target.value);
                    if (sendFeedback) setSendFeedback("");
                  }}
                  onKeyDown={(event) => {
                    if (
                      event.key === "Enter" &&
                      !event.shiftKey &&
                      !event.nativeEvent.isComposing
                    ) {
                      event.preventDefault();
                      event.currentTarget.form?.requestSubmit();
                    }
                  }}
                  maxLength={2000}
                  rows={1}
                  placeholder={
                    noticeMode
                      ? "직원 공지 내용을 입력하세요."
                      : "메시지를 입력하세요."
                  }
                />
              </label>
              <button
                className="send-button"
                disabled={
                  isSending ||
                  (!messageBody.trim() && files.length === 0) ||
                  connectionState === "offline"
                }
                    aria-label="메시지 보내기"
                  >
                    {uploadProgress !== null
                      ? uploadProgress < 100
                        ? `${uploadProgress}%`
                        : "저장 중"
                      : isSending
                        ? "전송 중"
                        : "보내기"}
                  </button>
            </form>
          </>
        ) : (
          <div className="chat-placeholder">
            <div className="placeholder-symbol">
              <span />
              <span />
              <span />
            </div>
            <h2>채팅방을 선택해 주세요.</h2>
            <p>내 소속과 지정 권한에 맞는 채팅방만 표시됩니다.</p>
          </div>
        )}
      </section>

      {canViewAdmin ? (
        <AdminDrawer
          key={adminOpen ? `open-${adminInitialTab}` : "closed"}
          currentUserId={me.id}
          open={adminOpen}
          onClose={() => {
            setAdminOpen(false);
            setAdminInitialTab("employees");
          }}
          initialTab={adminInitialTab}
          units={units}
          jobs={jobs}
          positionTitles={positionTitles}
          employees={employees}
          staffDirectory={staffDirectory}
          managedRooms={managedRooms}
          residents={adminResidents}
          onDataChanged={refreshAdminData}
          reviewOnly={mentorFullReview}
          codedSyntheticMode={codedSyntheticMode}
        />
      ) : null}
      {residentPickerOpen ? (
        <ResidentPickerDialog
          open
          residents={residents}
          selectedIds={selectedResidentIds}
          onApply={setSelectedResidentIds}
          onClose={closeResidentPicker}
        />
      ) : null}
      {aiConnectionOpen && canViewAdmin ? (
        <AiConnectionPanel
          onClose={() => closeSettingsPanel("ai")}
          reviewOnly={mentorFullReview}
        />
      ) : null}
      {securityOpen && (!me.is_reviewer_session || mentorFullReview) ? (
        <SecurityPanel
          user={me}
          reviewOnly={mentorFullReview}
          onUserChanged={setMe}
          onClose={() => closeSettingsPanel("security")}
          onLogout={() => void logout()}
        />
      ) : null}
      {notificationSoundOpen && (!me.is_reviewer_session || mentorFullReview) ? (
        <NotificationSoundPanel
          mode={notificationSoundMode}
          onModeChanged={setNotificationSoundMode}
          onClose={() => closeSettingsPanel("notification")}
          reviewOnly={mentorFullReview}
        />
      ) : null}
      {roomCallChoiceOpen && activeRoom ? (
        <CallChoiceSheet
          title={`${roomDisplayName(activeRoom, me)} 통화`}
          busy={Boolean(voiceCall.active || voiceCall.incoming)}
          loading={roomCallMembersLoading}
          error={roomCallChoiceError}
          members={roomCallMembers}
          selectedMemberIds={selectedRoomCallMemberIds}
          maxAudioParticipants={voiceCall.maxParticipants}
          maxVideoParticipants={voiceCall.maxVideoParticipants}
          onSelectedMemberIdsChange={setSelectedRoomCallMemberIds}
          onClose={closeRoomCallChoice}
          onAudio={() => startActiveRoomCall(activeRoom, "audio")}
          onVideo={() => startActiveRoomCall(activeRoom, "video")}
        />
      ) : null}
      {staffContactTarget && !staffContactCallChoiceOpen ? (
        <StaffContactSheet
          target={staffContactTarget}
          busy={staffContactBusy}
          error={staffContactError}
          callAvailable={voiceCall.available}
          onClose={closeStaffContact}
          onChat={() => void openDirectStaffContact("chat")}
          onCall={openStaffContactCallChoice}
        />
      ) : null}
      {staffContactTarget && staffContactCallChoiceOpen ? (
        <CallChoiceSheet
          title={`${staffContactTarget.name}님과 통화`}
          busy={staffContactBusy}
          error={staffContactError}
          onClose={closeStaffContactCallChoice}
          onAudio={() => void openDirectStaffContact("audio")}
          onVideo={() => void openDirectStaffContact("video")}
        />
      ) : null}
      {!me.is_reviewer_session ? (
        <StaffRoomPanel
          open={staffRoomOpen}
          initialRoomId={staffRoomInitialRoomId}
          currentUserId={me.id}
          onClose={closeStaffRoomPanel}
          onRoomsChanged={refreshRooms}
          onOpenRoom={openStaffRoomContact}
        />
      ) : null}
      {codedSyntheticMode && canViewAdmin ? (
        <WorkDesk
          open={recordReviewOpen}
          reviewQueueOnly={false}
          onClose={() => setRecordReviewOpen(false)}
        />
      ) : null}
      <PeriodWorkDesk
        key={reviewerInitialDate ? `reviewer-${reviewerInitialDate}` : "standard"}
        open={workdeskOpen}
        rooms={rooms}
        initialDate={reviewerInitialDate}
        onClose={() => setWorkdeskOpen(false)}
        onOpenSource={openWorkdeskSource}
      />
      {roomFilesOpen && activeRoom ? (
        <RoomFilesOverlay
          roomId={activeRoom.id}
          roomName={roomDisplayName(activeRoom, me)}
          onOpenMessage={(messageId) => {
            setRoomFilesOpen(false);
            openMessageDetail(messageId);
          }}
          onClose={() => setRoomFilesOpen(false)}
        />
      ) : null}
      {roomSearchOpen && activeRoom ? (
        <RoomSearchOverlay
          roomId={activeRoom.id}
          roomName={activeRoom.name}
          residents={residents}
          onOpenMessage={(messageId) => {
            openMessageDetail(messageId);
          }}
          onClose={() => setRoomSearchOpen(false)}
        />
      ) : null}
      {commentTarget ? (
        <div
          className="detail-layer"
          role="dialog"
          aria-modal="true"
          aria-label="관리자 코멘트"
        >
          <button
            className="detail-backdrop"
            onClick={() => setCommentTarget(null)}
            aria-label="코멘트 창 닫기"
          />
          <section className="message-compact-dialog message-comment-dialog">
            <header>
              <div>
                <strong>관리자 코멘트</strong>
                <small>{commentTarget.sender_name}님의 대화</small>
              </div>
              <button
                type="button"
                className="icon-button"
                onClick={() => setCommentTarget(null)}
                aria-label="닫기"
              >
                ×
              </button>
            </header>
            <div className="message-dialog-source">
              {messageTextForSharing(commentTarget)}
            </div>
            {commentDialogBusy && commentThread.length === 0 ? (
              <p className="muted-box" role="status">코멘트를 불러오고 있습니다…</p>
            ) : null}
            {commentThread.length > 0 ? (
              <div className="message-comment-thread">
                {commentThread.map((comment) => (
                  <article key={comment.id}>
                    <strong>{comment.author_name}</strong>
                    <p>{comment.body}</p>
                  </article>
                ))}
              </div>
            ) : null}
            <form onSubmit={submitComment}>
              <textarea
                value={commentBody}
                onChange={(event) => setCommentBody(event.target.value)}
                onKeyDown={(event) => {
                  if (
                    event.key === "Enter" &&
                    !event.shiftKey &&
                    !event.nativeEvent.isComposing
                  ) {
                    event.preventDefault();
                    event.currentTarget.form?.requestSubmit();
                  }
                }}
                rows={3}
                maxLength={1000}
                placeholder="관리자 코멘트를 입력하세요."
                aria-describedby="comment-dialog-keyboard-hint"
              />
              <small id="comment-dialog-keyboard-hint">
                Enter로 등록 · Shift+Enter로 줄바꿈
              </small>
              {commentDialogError ? <p className="form-error">{commentDialogError}</p> : null}
              <button
                type="submit"
                className="button button-primary"
                disabled={commentDialogBusy || !commentBody.trim()}
              >
                {commentDialogBusy ? "등록 중…" : "코멘트 등록"}
              </button>
            </form>
          </section>
        </div>
      ) : null}
      {forwardTarget ? (
        <div
          className="detail-layer"
          role="dialog"
          aria-modal="true"
          aria-label="다른 방 전달"
        >
          <button
            className="detail-backdrop"
            onClick={() => setForwardTarget(null)}
            aria-label="전달 창 닫기"
          />
          <section className="message-compact-dialog message-forward-dialog">
            <header>
              <div>
                <strong>다른 방 전달</strong>
                <small>전달할 채팅방을 선택해 주세요.</small>
              </div>
              <button
                type="button"
                className="icon-button"
                onClick={() => setForwardTarget(null)}
                aria-label="닫기"
              >
                ×
              </button>
            </header>
            <div className="message-dialog-source">
              {messageTextForSharing(forwardTarget)}
            </div>
            <form onSubmit={submitForward}>
              <div className="message-forward-room-list">
                {rooms
                  .filter((room) => room.id !== forwardTarget.room_id)
                  .map((room) => (
                    <label key={room.id}>
                      <input
                        type="checkbox"
                        checked={selectedForwardRoomIds.includes(room.id)}
                        onChange={(event) =>
                          setSelectedForwardRoomIds((current) =>
                            event.target.checked
                              ? [...current, room.id]
                              : current.filter((roomId) => roomId !== room.id),
                          )
                        }
                      />
                      <span>{roomDisplayName(room, me)}</span>
                    </label>
                  ))}
              </div>
              {forwardDialogError ? <p className="form-error">{forwardDialogError}</p> : null}
              <button
                type="submit"
                className="button button-primary"
                disabled={forwardDialogBusy || selectedForwardRoomIds.length === 0}
              >
                {forwardDialogBusy ? "전달 중…" : "선택한 방에 전달"}
              </button>
            </form>
          </section>
        </div>
      ) : null}
      {selectedMessageId ? (
        <MessageDetailOverlay
          messageId={selectedMessageId}
          refreshVersion={detailRefreshVersion}
          canProcessRecords={me.role === "admin" || me.can_process_records}
          onMessageChanged={() => void refreshRoomAfterAiShare()}
          onClose={closeMessageDetail}
        />
      ) : null}
      {aiAssistMessage &&
      (me.role === "admin" || me.can_process_records) &&
      (!me.is_reviewer_session || mentorFullReview) ? (
        <AiAssistPanel
          key={aiAssistMessage.id}
          showTechnicalDetails={me.role === "admin"}
          message={aiAssistMessage}
          onClose={closeAiAssist}
          onShared={refreshRoomAfterAiShare}
          reviewOnly={mentorFullReview}
        />
      ) : null}
      </main>
    </>
  );
}
