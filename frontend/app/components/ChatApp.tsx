"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { apiFetch, apiUpload, websocketUrl } from "../api";
import type {
  JobCode,
  ManagedRoom,
  Message,
  OrgUnit,
  PositionTitle,
  Resident,
  Room,
  User,
} from "../types";
import { AdminDrawer } from "./AdminDrawer";
import { AttachmentDisplay } from "./AttachmentDisplay";
import { DeveloperLauncher } from "./DeveloperLauncher";
import { LoginScreen } from "./LoginScreen";
import { MessageDetailOverlay } from "./MessageDetailOverlay";
import { NotificationSoundPanel } from "./NotificationSoundPanel";
import { PwaInstallButton } from "./PwaInstallButton";
import { RoomSearchOverlay } from "./RoomSearchOverlay";
import { SecurityPanel } from "./SecurityPanel";
import { PeriodWorkDesk } from "./PeriodWorkDesk";
import {
  playCommentNotification,
  playMessageNotification,
  readNotificationSoundMode,
  shouldPlayMessageNotification,
  type NotificationSoundMode,
} from "../notificationSound";
import { synchronizeWebPushSubscription } from "../pushNotifications";
import {
  clearReviewerLanding,
  readReviewerLanding,
} from "../reviewerLanding";

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
  confirmation: "확인요청",
};

const actionStatusLabels = {
  assigned: "미확인",
  acknowledged: "확인",
  in_progress: "처리 중",
  completed: "완료",
};

const messageNatureLabels = {
  handover: "인수인계",
  work_request: "업무협조",
  report: "보고",
};

type MessageNature = "chat" | keyof typeof messageNatureLabels;

export function ChatApp() {
  const [loading, setLoading] = useState(true);
  const [me, setMe] = useState<User | null>(null);
  const [loginNotice, setLoginNotice] = useState("");
  const [rooms, setRooms] = useState<Room[]>([]);
  const [activeRoomId, setActiveRoomId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [residents, setResidents] = useState<Resident[]>([]);
  const [adminResidents, setAdminResidents] = useState<Resident[]>([]);
  const [messageBody, setMessageBody] = useState("");
  const [selectedResidentId, setSelectedResidentId] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [reportImage, setReportImage] = useState(false);
  const [noticeMode, setNoticeMode] = useState(false);
  const [messageNature, setMessageNature] = useState<MessageNature>("chat");
  const [sendFeedback, setSendFeedback] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [connectionState, setConnectionState] = useState<"connecting" | "online" | "offline">(
    "connecting",
  );
  const [error, setError] = useState("");
  const [adminOpen, setAdminOpen] = useState(false);
  const [securityOpen, setSecurityOpen] = useState(false);
  const [notificationSoundOpen, setNotificationSoundOpen] = useState(false);
  const [notificationSoundMode, setNotificationSoundMode] =
    useState<NotificationSoundMode>(() => readNotificationSoundMode());
  const [socketRevision, setSocketRevision] = useState(0);
  const [workdeskOpen, setWorkdeskOpen] = useState(false);
  const [selectedMessageId, setSelectedMessageId] = useState<string | null>(null);
  const [detailRefreshVersion, setDetailRefreshVersion] = useState(0);
  const [roomSearchOpen, setRoomSearchOpen] = useState(false);
  const [units, setUnits] = useState<OrgUnit[]>([]);
  const [jobs, setJobs] = useState<JobCode[]>([]);
  const [positionTitles, setPositionTitles] = useState<PositionTitle[]>([]);
  const [employees, setEmployees] = useState<User[]>([]);
  const [managedRooms, setManagedRooms] = useState<ManagedRoom[]>([]);
  const activeRoomRef = useRef<string | null>(null);
  const roomsRef = useRef<Room[]>([]);
  const messagesRef = useRef<Message[]>([]);
  const selectedMessageRef = useRef<string | null>(null);
  const forcedLogoutRef = useRef(false);
  const sendingRef = useRef(false);
  const wakeSyncInFlightRef = useRef(false);
  const lastWakeSyncAtRef = useRef(0);
  const hiddenAtRef = useRef<number | null>(null);
  const reviewerLandingAttemptedRef = useRef(false);
  const messageEndRef = useRef<HTMLDivElement | null>(null);
  const currentUserId = me?.id ?? null;
  const currentUserRole = me?.role ?? null;
  const passwordChangeRequired =
    (me?.must_change_password ?? false) && !me?.is_reviewer_session;
  const chatViewActive = Boolean(
    currentUserId && !me?.is_dev_launcher && !passwordChangeRequired,
  );

  useEffect(() => {
    if (!chatViewActive || me?.is_reviewer_session) return;
    void synchronizeWebPushSubscription().catch(() => {
      // 최초 권한 허용은 사용자 조작이 필요합니다. 기존 허용 기기만 조용히 복구합니다.
    });
  }, [chatViewActive, currentUserId, me?.is_reviewer_session]);

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
    setSelectedResidentId("");
    setFiles([]);
    setReportImage(false);
    setActiveRoomId(null);
    activeRoomRef.current = null;
    setAdminOpen(false);
    setSecurityOpen(false);
    setWorkdeskOpen(false);
    setSelectedMessageId(null);
    selectedMessageRef.current = null;
    setLoginNotice(message ?? "");
  }, []);

  const refreshRooms = useCallback(async () => {
    const nextRooms = await apiFetch<Room[]>("/api/rooms");
    roomsRef.current = nextRooms;
    setRooms(nextRooms);
    const current = activeRoomRef.current;
    if (current && !nextRooms.some((room) => room.id === current)) {
      activeRoomRef.current = null;
      setActiveRoomId(null);
      setMessages([]);
      setResidents([]);
      setSelectedResidentId("");
      setFiles([]);
      setReportImage(false);
      selectedMessageRef.current = null;
      setSelectedMessageId(null);
    }
    return nextRooms;
  }, []);

  useEffect(() => {
    roomsRef.current = rooms;
  }, [rooms]);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  const refreshAdminData = useCallback(async () => {
    const [
      nextUnits,
      nextJobs,
      nextPositionTitles,
      nextEmployees,
      nextManagedRooms,
      nextResidents,
    ] = await Promise.all([
      apiFetch<OrgUnit[]>("/api/org-units?include_inactive=true"),
      apiFetch<JobCode[]>("/api/job-codes?include_inactive=true"),
      apiFetch<PositionTitle[]>("/api/position-titles?include_inactive=true"),
      apiFetch<User[]>("/api/employees"),
      apiFetch<ManagedRoom[]>("/api/admin/rooms?include_inactive=true"),
      apiFetch<Resident[]>("/api/admin/residents"),
    ]);
    setUnits(nextUnits);
    setJobs(nextJobs);
    setPositionTitles(nextPositionTitles);
    setEmployees(nextEmployees);
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
      await refreshRooms();
    },
    [refreshRooms],
  );

  const openRoom = useCallback(
    async (roomId: string) => {
      activeRoomRef.current = roomId;
      setActiveRoomId(roomId);
      setError("");
      try {
        const [nextMessages, nextResidents] = await Promise.all([
          apiFetch<Message[]>(`/api/rooms/${roomId}/messages`),
          apiFetch<Resident[]>(`/api/rooms/${roomId}/residents`),
        ]);
        setMessages(nextMessages);
        setResidents(nextResidents);
        setSelectedResidentId("");
        setFiles([]);
        setReportImage(false);
        const last = nextMessages.at(-1);
        if (last) await markRead(roomId, last.id);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "채팅방을 열지 못했습니다.");
      }
    },
    [markRead],
  );

  useEffect(() => {
    apiFetch<User>("/api/auth/me")
      .then((user) => setMe(user))
      .catch(() => setMe(null))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const onAuthError = (event: Event) => {
      const detail = (event as CustomEvent<{ status: number; message: string }>).detail;
      if (detail.status === 401) {
        resetSession(detail.message || "로그인 시간이 끝났습니다. 다시 로그인해 주세요.");
      }
    };
    window.addEventListener("smcodi:auth-error", onAuthError);
    return () => window.removeEventListener("smcodi:auth-error", onAuthError);
  }, [resetSession]);

  useEffect(() => {
    if (!currentUserId || passwordChangeRequired) return;
    forcedLogoutRef.current = false;
    const timer = window.setTimeout(() => {
      if (me?.is_reviewer_session) {
        void refreshRooms();
      } else if (currentUserRole === "admin") {
        void refreshAdminData();
      } else {
        void Promise.all([
          apiFetch<OrgUnit[]>("/api/org-units"),
          apiFetch<JobCode[]>("/api/job-codes"),
          refreshRooms(),
        ]).then(([nextUnits, nextJobs]) => {
          setUnits(nextUnits);
          setJobs(nextJobs);
        });
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [
    currentUserId,
    currentUserRole,
    me?.is_reviewer_session,
    passwordChangeRequired,
    refreshAdminData,
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

  const synchronizeAfterWake = useCallback(async () => {
    if (!me || me.must_change_password || wakeSyncInFlightRef.current) return;
    wakeSyncInFlightRef.current = true;
    try {
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
                message.sender_id !== me.id,
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
  }, [markRead, me, notificationSoundMode, refreshRooms, workdeskOpen]);

  useEffect(() => {
    if (!me || me.must_change_password) return;

    const resume = (force = false) => {
      if (document.visibilityState === "hidden") return;
      const now = Date.now();
      if (!force && now - lastWakeSyncAtRef.current < 4_000) return;
      if (!me.is_reviewer_session) {
        void synchronizeWebPushSubscription().catch(() => {
          // 이미 허용된 기기의 만료된 알림 주소만 조용히 복구합니다.
        });
      }
      setSocketRevision((current) => current + 1);
      void synchronizeAfterWake();
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "hidden") {
        hiddenAtRef.current = Date.now();
        return;
      }
      const sleptFor = hiddenAtRef.current ? Date.now() - hiddenAtRef.current : 0;
      hiddenAtRef.current = null;
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
  }, [me, synchronizeAfterWake]);

  useEffect(() => {
    if (!currentUserId || !currentUserRole || passwordChangeRequired) return;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let pingTimer: ReturnType<typeof setInterval> | null = null;
    let disposed = false;
    let lastPongAt = Date.now();

    const connect = () => {
      setConnectionState("connecting");
      socket = new WebSocket(websocketUrl());
      socket.onopen = () => {
        setConnectionState("online");
        lastPongAt = Date.now();
        if (pingTimer) clearInterval(pingTimer);
        pingTimer = setInterval(() => {
          if (socket?.readyState === WebSocket.OPEN) {
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
          room_id?: string;
          user_id?: string;
          comment_count?: number;
          reply_user_count?: number;
          comment?: { author_id: string };
          notification_user_ids?: string[];
          action_item?: Message["action_item"];
        };
        if (payload.event === "pong" || payload.event === "ready") {
          lastPongAt = Date.now();
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
          currentUserRole === "admin"
        ) {
          void refreshAdminData();
          return;
        }
        if (payload.event === "message_created" && payload.message) {
          const incoming = payload.message;
          playMessageNotification(notificationSoundMode, incoming, currentUserId);
          if (activeRoomRef.current === incoming.room_id) {
            setMessages((current) =>
              current.some((item) => item.id === incoming.id)
                ? current
                : [...current, incoming],
            );
            void markRead(incoming.room_id, incoming.id);
          } else {
            void refreshRooms();
          }
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
        setConnectionState("offline");
        if (pingTimer) clearInterval(pingTimer);
        if (!disposed && !forcedLogoutRef.current) {
          reconnectTimer = setTimeout(connect, 2_000);
        }
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
      socket?.close();
    };
  }, [
    currentUserId,
    currentUserRole,
    markRead,
    notificationSoundMode,
    passwordChangeRequired,
    refreshAdminData,
    refreshRooms,
    resetSession,
    socketRevision,
  ]);

  useEffect(() => {
    messageEndRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

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
    const residentId = selectedResidentId || null;
    const selectedFiles = files;
    const selectedReportImage = reportImage;
    const selectedMessageNature = messageNature;
    const messageType = noticeMode ? "notice" : messageNature;
    setMessageBody("");
    setSelectedResidentId("");
    setError("");
    sendingRef.current = true;
    setIsSending(true);
    try {
      let sent: Message;
      if (selectedFiles.length > 0) {
        setUploadProgress(0);
        const formData = new FormData();
        formData.append("body", body);
        formData.append("message_type", messageType);
        if (residentId) formData.append("resident_id", residentId.toString());
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
            message_type: messageType,
            resident_id: residentId,
            action: null,
          }),
        });
      }
      setMessages((current) =>
        current.some((item) => item.id === sent.id) ? current : [...current, sent],
      );
      setFiles([]);
      setReportImage(false);
      await markRead(activeRoomId, sent.id);
      setSendFeedback(
        noticeMode
            ? "공지로 보냈습니다."
            : selectedMessageNature !== "chat"
              ? `${messageNatureLabels[selectedMessageNature]} 글로 보냈습니다.`
            : selectedReportImage
              ? "보고서 이미지를 보냈습니다. 글자 판독은 업무함에서 확인할 수 있습니다."
              : "일반 대화로 보냈습니다.",
      );
      setNoticeMode(false);
      setMessageNature("chat");
    } catch (reason) {
      setMessageBody(body);
      setSelectedResidentId(residentId?.toString() ?? "");
      setFiles(selectedFiles);
      setReportImage(selectedReportImage);
      setMessageNature(selectedMessageNature);
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
    selectedMessageRef.current = messageId;
    setSelectedMessageId(messageId);
    setMessages((current) =>
      current.map((message) =>
        message.id === messageId ? { ...message, unread_comment_count: 0 } : message,
      ),
    );
    setDetailRefreshVersion((current) => current + 1);
  }

  function closeMessageDetail() {
    selectedMessageRef.current = null;
    setSelectedMessageId(null);
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
            <strong>AI 챌린지 심사위원 체험 중</strong>
            <small>모든 직원과 어르신은 가명입니다.</small>
          </span>
          <button onClick={() => void returnToReviewerGuide()}>
            체험 선택으로 돌아가기
          </button>
        </div>
      ) : me.is_dev_impersonated ? (
        <div className="dev-impersonation-banner" role="status">
          <strong>개발 시험 중 · {me.full_name} 화면</strong>
          <button onClick={() => void returnToDevLauncher()}>
            사용자 런처로 돌아가기
          </button>
        </div>
      ) : null}
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
              <small>메디컬 실버 채팅</small>
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
        <section className="profile-strip">
          <span className="avatar large">{me.full_name.slice(0, 1)}</span>
          <div>
            <strong>{me.full_name}</strong>
            <small>
              {[me.floor?.name, me.team?.name, me.job_name, me.position_title]
                .filter(Boolean)
                .join(" · ") || "소속 미지정"}
            </small>
          </div>
          <div className="profile-actions">
            {me.role === "admin" && !me.is_reviewer_session ? (
              <button className="icon-button admin-button" onClick={() => setAdminOpen(true)}>
                관리
              </button>
            ) : null}
            {(me.is_reviewer_session
              ? me.reviewer_experience === "social_worker" &&
                me.can_process_records
              : me.role === "admin" || me.can_process_records) ? (
              <button
                className="icon-button workdesk-button"
                onClick={() => setWorkdeskOpen(true)}
              >
                AI 돌봄 브리핑
              </button>
            ) : null}
          </div>
        </section>
        <div className="room-list-heading">
          <h1>내 채팅방</h1>
          <span>{rooms.length}</span>
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
                  {room.kind === "global" ? "전" : room.name.slice(0, 1)}
                </span>
                <span className="room-copy">
                  <span className="room-title-line">
                    <strong>{room.name}</strong>
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
          {!me.is_reviewer_session ? <PwaInstallButton /> : null}
          {!me.is_reviewer_session ? (
            <>
              <button
                className="text-button"
                onClick={() => setNotificationSoundOpen(true)}
              >
                알림 설정
              </button>
              <button className="text-button" onClick={() => setSecurityOpen(true)}>
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
                onClick={() => {
                  activeRoomRef.current = null;
                  setActiveRoomId(null);
                  setMessages([]);
                  setResidents([]);
                  setSelectedResidentId("");
                  setFiles([]);
                  setReportImage(false);
                }}
                aria-label="채팅방 목록으로"
              >
                ‹
              </button>
              <div>
                <h2>{activeRoom.name}</h2>
                <p>{kindLabels[activeRoom.kind]} 채팅방 · 허용된 직원만 참여</p>
              </div>
              <div className="chat-header-actions">
                <button
                  type="button"
                  className="button button-secondary room-search-open"
                  onClick={() => setRoomSearchOpen(true)}
                >
                  대화 검색
                </button>
              </div>
            </header>
            <div className="message-area" aria-live="polite">
              {messages.length === 0 ? (
                <div className="empty-messages">
                  <span>{activeRoom.name.slice(0, 1)}</span>
                  <h3>첫 업무대화를 시작하세요.</h3>
                  <p>짧고 명확하게 작성하고, 개인정보는 필요한 범위에서만 사용하세요.</p>
                </div>
              ) : (
                messages.map((message) => {
                  const day = new Date(message.created_at).toDateString();
                  const showDay = day !== previousDay;
                  previousDay = day;
                  const mine = message.sender_id === me.id;
                  return (
                    <div key={message.id}>
                      {showDay ? <div className="day-divider">{formatDay(message.created_at)}</div> : null}
                      {message.message_type === "notice" ? (
                        <article className="notice-message">
                          <button
                            className="message-detail-trigger notice-trigger"
                            onClick={() => openMessageDetail(message.id)}
                          >
                            <div>
                              <span>공지</span>
                              <strong>{message.sender_name}</strong>
                              <time>{formatTime(message.created_at)}</time>
                            </div>
                            {message.resident ? (
                              <span className="resident-chip">
                                {message.resident.display_name}
                              </span>
                            ) : null}
                            {message.resident_links
                              .filter(
                                (link) =>
                                  link.resident.id !== message.resident?.id,
                              )
                              .map((link) => (
                                <span
                                  className={`resident-chip ${
                                    link.status === "candidate" ? "candidate" : ""
                                  }`}
                                  key={link.resident.id}
                                >
                                  {link.resident.display_name}
                                  {link.status === "candidate" ? " · 확인 후보" : ""}
                                </span>
                              ))}
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
                            <p>{message.body}</p>
                            {message.action_item ? (
                              <span className={`message-action-badge priority-${message.action_item.priority}`}>
                                업무지정 · {actionLabels[message.action_item.action_type]} ·{" "}
                                {message.action_item.assignee_user_name ??
                                  message.action_item.assignee_unit_name} ·{" "}
                                {actionStatusLabels[message.action_item.status]}
                              </span>
                            ) : null}
                            {message.comment_count > 0 ? (
                              <span className={`comment-count ${message.unread_comment_count ? "new" : ""}`}>
                                댓글 {message.comment_count}
                                {message.unread_comment_count ? ` · 새 댓글 ${message.unread_comment_count}` : ""}
                              </span>
                            ) : null}
                          </button>
                        </article>
                      ) : (
                        <article className={`message-row ${mine ? "mine" : ""}`}>
                          {!mine ? <span className="avatar">{message.sender_name.slice(0, 1)}</span> : null}
                          <div className="message-stack">
                            {!mine ? <strong>{message.sender_name}</strong> : null}
                            <div className="bubble-line">
                              {mine ? (
                                <div className="message-meta">
                                  <span className="message-engagement">
                                    읽음 {message.read_count} · 답글{" "}
                                    {message.reply_user_count}명
                                  </span>
                                  <time>{formatTime(message.created_at)}</time>
                                </div>
                              ) : null}
                              <button
                                className="message-bubble message-detail-trigger"
                                onClick={() => openMessageDetail(message.id)}
                                aria-label={`${message.sender_name} 메시지 상세 열기`}
                              >
                                {message.resident ? (
                                  <span className="resident-chip">
                                    {message.resident.display_name}
                                  </span>
                                ) : null}
                                {message.resident_links
                                  .filter(
                                    (link) =>
                                      link.resident.id !== message.resident?.id,
                                  )
                                  .map((link) => (
                                    <span
                                      className={`resident-chip ${
                                        link.status === "candidate" ? "candidate" : ""
                                      }`}
                                      key={link.resident.id}
                                    >
                                      {link.resident.display_name}
                                      {link.status === "candidate" ? " · 확인 후보" : ""}
                                    </span>
                                  ))}
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
                                <span className="bubble-text">{message.body}</span>
                                {message.attachments.length > 0 ? (
                                  <span className="bubble-attachments">
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
                                {message.action_item ? (
                                  <span
                                    className={`message-action-badge priority-${message.action_item.priority}`}
                                  >
                                    업무지정 · {actionLabels[message.action_item.action_type]} ·{" "}
                                    {message.action_item.assignee_user_name ??
                                      message.action_item.assignee_unit_name} ·{" "}
                                    {actionStatusLabels[message.action_item.status]}
                                  </span>
                                ) : null}
                                {message.comment_count > 0 ? (
                                  <span
                                    className={`comment-count ${
                                      message.unread_comment_count ? "new" : ""
                                    }`}
                                  >
                                    댓글 {message.comment_count}
                                    {message.unread_comment_count
                                      ? ` · 새 댓글 ${message.unread_comment_count}`
                                      : ""}
                                  </span>
                                ) : null}
                                <small className="open-detail-hint">눌러서 크게 보기</small>
                              </button>
                              {!mine ? (
                                <div className="message-meta">
                                  <span className="message-engagement">
                                    읽음 {message.read_count} · 답글{" "}
                                    {message.reply_user_count}명
                                  </span>
                                  <time>{formatTime(message.created_at)}</time>
                                </div>
                              ) : null}
                            </div>
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
            <form
              className="composer"
              onSubmit={sendMessage}
              aria-busy={isSending}
            >
              <div className="composer-tools">
                <label className={`resident-select ${selectedResidentId ? "selected" : ""}`}>
                  <span className="sr-only">어르신 선택</span>
                  <select
                    value={selectedResidentId}
                    onChange={(event) => setSelectedResidentId(event.target.value)}
                    aria-label="어르신 선택"
                  >
                    <option value="">어르신 선택</option>
                    {residents.some((resident) => resident.is_priority) ? (
                      <optgroup label="이 방 어르신">
                        {residents
                          .filter((resident) => resident.is_priority)
                          .map((resident) => (
                            <option key={resident.id} value={resident.id}>
                              {resident.display_name}
                            </option>
                          ))}
                      </optgroup>
                    ) : null}
                    <optgroup
                      label={
                        residents.some((resident) => resident.is_priority)
                          ? "다른 어르신"
                          : "전체 어르신"
                      }
                    >
                      {residents
                        .filter((resident) => !resident.is_priority)
                        .map((resident) => (
                          <option key={resident.id} value={resident.id}>
                            {resident.display_name}
                          </option>
                        ))}
                    </optgroup>
                  </select>
                </label>
                <label className={`file-picker ${files.length ? "selected" : ""}`}>
                  파일{files.length ? ` ${files.length}` : ""}
                  <input
                    type="file"
                    accept="image/jpeg,image/png,image/webp,audio/mpeg,audio/wav,audio/mp4,audio/webm,audio/ogg,audio/aac,video/mp4,video/webm,video/quicktime,application/pdf"
                    multiple
                    disabled={isSending}
                    onChange={(event) => {
                      const selected = Array.from(event.target.files ?? []).slice(0, 4);
                      setFiles(selected);
                      if (!selected.some((file) => file.type.startsWith("image/"))) {
                        setReportImage(false);
                      }
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
                {files.some((file) => file.type.startsWith("image/")) ? (
                  <button
                    type="button"
                    className={`report-image-toggle ${reportImage ? "active" : ""}`}
                    disabled={isSending}
                    onClick={() => setReportImage((current) => !current)}
                    aria-pressed={reportImage}
                  >
                    {reportImage ? "보고서 판독 중" : "보고서 이미지"}
                  </button>
                ) : null}
                <label
                  className={`message-nature-select ${
                    messageNature !== "chat" ? "selected" : ""
                  }`}
                >
                  <span className="sr-only">글 성격 선택</span>
                  <select
                    value={messageNature}
                    disabled={noticeMode || isSending}
                    onChange={(event) =>
                      setMessageNature(event.target.value as MessageNature)
                    }
                    aria-label="글 성격 선택"
                  >
                    <option value="chat">일반 대화</option>
                    <option value="handover">인수인계</option>
                    <option value="work_request">업무협조</option>
                    <option value="report">보고</option>
                  </select>
                </label>
                {me.role === "admin" ? (
                  <button
                    type="button"
                    className={`notice-toggle ${noticeMode ? "active" : ""}`}
                    onClick={() => {
                      setNoticeMode((current) => !current);
                      setMessageNature("chat");
                    }}
                    aria-pressed={noticeMode}
                  >
                    {noticeMode ? "공지 작성 중" : "공지"}
                  </button>
                ) : null}
              </div>
              {files.length > 0 ? (
                <div className="selected-files" aria-live="polite">
                  <span>
                    {files.map((file) => file.name).join(", ")}
                  </span>
                  {reportImage ? (
                    <strong>
                      {selectedResidentId
                        ? "선택한 어르신과 함께 로컬 글자 판독"
                        : "여러 어르신 이름을 판독해 확인 후보로 표시"}
                    </strong>
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
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      event.currentTarget.form?.requestSubmit();
                    }
                  }}
                  maxLength={2000}
                  rows={1}
                  placeholder={
                    noticeMode
                      ? "직원 공지 내용을 입력하세요."
                      : messageNature === "chat"
                        ? "메시지를 입력하세요."
                        : `${messageNatureLabels[messageNature]} 내용을 입력하세요.`
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

      {me.role === "admin" ? (
        <AdminDrawer
          open={adminOpen}
          onClose={() => setAdminOpen(false)}
          units={units}
          jobs={jobs}
          positionTitles={positionTitles}
          employees={employees}
          managedRooms={managedRooms}
          residents={adminResidents}
          currentUserId={me.id}
          onDataChanged={refreshAdminData}
        />
      ) : null}
      {securityOpen && !me.is_reviewer_session ? (
        <SecurityPanel
          user={me}
          onUserChanged={setMe}
          onClose={() => setSecurityOpen(false)}
          onLogout={() => void logout()}
        />
      ) : null}
      {notificationSoundOpen && !me.is_reviewer_session ? (
        <NotificationSoundPanel
          mode={notificationSoundMode}
          onModeChanged={setNotificationSoundMode}
          onClose={() => setNotificationSoundOpen(false)}
        />
      ) : null}
      <PeriodWorkDesk
        key={reviewerInitialDate ? `reviewer-${reviewerInitialDate}` : "standard"}
        open={workdeskOpen}
        rooms={rooms}
        initialDate={reviewerInitialDate}
        onClose={() => setWorkdeskOpen(false)}
      />
      {selectedMessageId ? (
        <MessageDetailOverlay
          messageId={selectedMessageId}
          refreshVersion={detailRefreshVersion}
          rooms={rooms}
          currentUserId={me.id}
          canProcessRecords={me.role === "admin" || me.can_process_records}
          onClose={closeMessageDetail}
        />
      ) : null}
      {roomSearchOpen && activeRoom ? (
        <RoomSearchOverlay
          roomId={activeRoom.id}
          roomName={activeRoom.name}
          residents={residents}
          onOpenMessage={(messageId) => {
            setRoomSearchOpen(false);
            openMessageDetail(messageId);
          }}
          onClose={() => setRoomSearchOpen(false)}
        />
      ) : null}
      </main>
    </>
  );
}
