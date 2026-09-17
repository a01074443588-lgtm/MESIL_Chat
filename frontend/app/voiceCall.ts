import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "./api";
import type { Room } from "./types";
import {
  prepareCallAudio,
  startIncomingCallRingtone,
  startOutgoingCallTone,
  stopCallTone,
} from "./notificationSound";

export type VoiceCallIceServer = {
  urls: string[];
  username?: string | null;
  credential?: string | null;
};

type VoiceCallConfig = {
  enabled: boolean;
  max_participants: number;
  max_video_participants: number;
  ice_servers: VoiceCallIceServer[];
};

export type CallMode = "audio" | "video";

export type IncomingVoiceCall = {
  callId: string;
  roomId: string;
  roomName: string;
  callerUserId: string;
  callerName: string;
  memberCount: number;
  callMode: CallMode;
  expiresAt: number;
};

export type ActiveVoiceCall = {
  callId: string;
  roomId: string;
  roomName: string;
  callerUserId: string;
  callMode: CallMode;
  status: string;
  expectedMemberCount: number;
  participants: Array<{ userId: string; name: string }>;
};

export type RemoteCallStream = {
  userId: string;
  name: string;
  stream: MediaStream;
};

type VoiceCallOptions = {
  currentUserId: string | null;
  currentUserName: string;
  isReviewer: boolean;
  sendRealtimeEvent: (payload: Record<string, unknown>) => boolean;
  sendRealtimeEventWhenConnected: (
    payload: Record<string, unknown>,
    timeoutMs?: number,
  ) => Promise<boolean>;
};

type VoiceCallRealtimeEvent = Record<string, unknown> & { event?: string };

type PeerEntry = {
  connection: RTCPeerConnection;
  name: string;
  pendingCandidates: RTCIceCandidateInit[];
  videoSender: RTCRtpSender | null;
  failConnection: () => void;
};

const PEER_CONNECTION_TIMEOUT_MS = 25_000;
const PEER_RECONNECT_TIMEOUT_MS = 10_000;

function hasTurnServer(config: VoiceCallConfig | null) {
  return Boolean(
    config?.ice_servers.some((server) =>
      server.urls.some((url) => url.startsWith("turn:") || url.startsWith("turns:")),
    ),
  );
}

function connectionFailureMessage(config: VoiceCallConfig | null) {
  return hasTurnServer(config)
    ? "영상·음성 연결에 실패했습니다. 인터넷 연결을 확인하고 다시 걸어 주세요."
    : "상대방은 받았지만 휴대전화끼리 연결하지 못했습니다. 통화 중계(TURN) 설정이 필요합니다.";
}

function mediaErrorMessage(reason: unknown, callMode: CallMode) {
  if (reason instanceof DOMException) {
    if (reason.name === "NotAllowedError") {
      return callMode === "video"
        ? "카메라와 마이크를 허용해야 영상통화할 수 있습니다."
        : "마이크 사용을 허용해야 통화할 수 있습니다.";
    }
    if (reason.name === "NotFoundError") {
      return callMode === "video"
        ? "사용할 수 있는 카메라나 마이크를 찾지 못했습니다."
        : "사용할 수 있는 마이크를 찾지 못했습니다.";
    }
  }
  return callMode === "video"
    ? "카메라를 열지 못했습니다. 휴대전화의 카메라·마이크 권한을 확인해 주세요."
    : "마이크를 열지 못했습니다. 휴대전화의 마이크 권한을 확인해 주세요.";
}

function callModeValue(payload: VoiceCallRealtimeEvent): CallMode {
  return stringValue(payload, "call_mode") === "video" ? "video" : "audio";
}

function cameraConstraints(
  facingMode: "user" | "environment",
): MediaTrackConstraints {
  return {
    facingMode,
    width: { ideal: 720, max: 1280 },
    height: { ideal: 720, max: 1280 },
    frameRate: { ideal: 15, max: 24 },
  };
}

function stringValue(payload: VoiceCallRealtimeEvent, key: string) {
  const value = payload[key];
  return typeof value === "string" ? value : "";
}

function numberValue(payload: VoiceCallRealtimeEvent, key: string) {
  const value = payload[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

export function useVoiceCall({
  currentUserId,
  currentUserName,
  isReviewer,
  sendRealtimeEvent,
  sendRealtimeEventWhenConnected,
}: VoiceCallOptions) {
  const [config, setConfig] = useState<VoiceCallConfig | null>(null);
  const [incoming, setIncoming] = useState<IncomingVoiceCall | null>(null);
  const [active, setActive] = useState<ActiveVoiceCall | null>(null);
  const [remoteStreams, setRemoteStreams] = useState<RemoteCallStream[]>([]);
  const [localStream, setLocalStream] = useState<MediaStream | null>(null);
  const [muted, setMuted] = useState(false);
  const [cameraOff, setCameraOff] = useState(false);
  const [switchingMode, setSwitchingMode] = useState(false);
  const [facingMode, setFacingMode] = useState<"user" | "environment">("user");
  const [error, setError] = useState("");
  const activeRef = useRef<ActiveVoiceCall | null>(null);
  const incomingRef = useRef<IncomingVoiceCall | null>(null);
  const configRef = useRef<VoiceCallConfig | null>(null);
  const localStreamRef = useRef<MediaStream | null>(null);
  const peersRef = useRef<Map<string, PeerEntry>>(new Map());
  const peerTimersRef = useRef<Map<string, number>>(new Map());
  const switchingModeRef = useRef(false);

  useEffect(() => {
    activeRef.current = active;
  }, [active]);

  useEffect(() => {
    incomingRef.current = incoming;
  }, [incoming]);

  useEffect(() => {
    configRef.current = config;
  }, [config]);

  useEffect(() => {
    const prepare = () => {
      void prepareCallAudio();
    };
    window.addEventListener("pointerdown", prepare, { passive: true });
    window.addEventListener("keydown", prepare);
    return () => {
      window.removeEventListener("pointerdown", prepare);
      window.removeEventListener("keydown", prepare);
      stopCallTone();
    };
  }, []);

  useEffect(() => {
    if (!currentUserId || isReviewer) return;
    let cancelled = false;
    void apiFetch<VoiceCallConfig>("/api/voice-calls/config")
      .then((payload) => {
        if (!cancelled) setConfig(payload);
      })
      .catch(() => {
        if (!cancelled) setConfig(null);
      });
    return () => {
      cancelled = true;
    };
  }, [currentUserId, isReviewer]);

  const refreshConfig = useCallback(async () => {
    const payload = await apiFetch<VoiceCallConfig>("/api/voice-calls/config");
    configRef.current = payload;
    setConfig(payload);
    return payload;
  }, []);

  const stopMedia = useCallback(() => {
    peerTimersRef.current.forEach((timer) => window.clearTimeout(timer));
    peerTimersRef.current.clear();
    peersRef.current.forEach(({ connection }) => connection.close());
    peersRef.current.clear();
    localStreamRef.current?.getTracks().forEach((track) => track.stop());
    localStreamRef.current = null;
    setLocalStream(null);
    setRemoteStreams([]);
    setMuted(false);
    setCameraOff(false);
    switchingModeRef.current = false;
    setSwitchingMode(false);
    setFacingMode("user");
  }, []);

  const closeCall = useCallback(
    (notifyServer: boolean) => {
      const current = activeRef.current;
      if (notifyServer && current) {
        const isUnansweredOutgoingCall =
          current.callerUserId === currentUserId && current.participants.length <= 1;
        void sendRealtimeEventWhenConnected({
          event: isUnansweredOutgoingCall
            ? "voice_call_cancel"
            : "voice_call_leave",
          call_id: current.callId,
          room_id: current.roomId,
          call_mode: current.callMode,
        });
      }
      stopCallTone();
      activeRef.current = null;
      setActive(null);
      stopMedia();
    },
    [currentUserId, sendRealtimeEventWhenConnected, stopMedia],
  );

  useEffect(() => {
    if (currentUserId) return;
    const timer = window.setTimeout(() => {
      setIncoming(null);
      closeCall(false);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [closeCall, currentUserId]);

  useEffect(() => {
    if (!incoming) return;
    void startIncomingCallRingtone();
    navigator.vibrate?.([350, 180, 350, 180, 500]);
    const ringTimer = window.setInterval(() => {
      navigator.vibrate?.([350, 180, 350]);
    }, 4_000);
    const expireTimer = window.setTimeout(() => {
      const current = incomingRef.current;
      if (!current) return;
      void sendRealtimeEventWhenConnected({
        event: "voice_call_decline",
        call_id: current.callId,
        room_id: current.roomId,
        call_mode: current.callMode,
      });
      setIncoming(null);
    }, Math.max(0, incoming.expiresAt - Date.now()));
    return () => {
      window.clearInterval(ringTimer);
      window.clearTimeout(expireTimer);
      navigator.vibrate?.(0);
      stopCallTone();
    };
  }, [incoming, sendRealtimeEventWhenConnected]);

  const outgoingCallWaiting = Boolean(
    active?.callerUserId === currentUserId && active.participants.length <= 1,
  );

  useEffect(() => {
    if (!outgoingCallWaiting) {
      stopCallTone();
      return undefined;
    }
    return () => stopCallTone();
  }, [outgoingCallWaiting]);

  useEffect(() => {
    if (
      !active ||
      !outgoingCallWaiting
    ) {
      return;
    }
    const timer = window.setTimeout(() => {
      closeCall(true);
      setError("상대방이 전화를 받지 않았습니다.");
    }, 45_000);
    return () => window.clearTimeout(timer);
  }, [active, closeCall, outgoingCallWaiting]);

  const localMedia = useCallback(async (callMode: CallMode) => {
    if (localStreamRef.current) return localStreamRef.current;
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("이 브라우저에서는 카메라·마이크 통화를 사용할 수 없습니다.");
    }
    const stream = await navigator.mediaDevices.getUserMedia({
      video:
        callMode === "video"
          ? cameraConstraints("user")
          : false,
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    localStreamRef.current = stream;
    setLocalStream(stream);
    setFacingMode("user");
    return stream;
  }, []);

  const sendSignal = useCallback(
    (
      targetUserId: string,
      signal:
        | { kind: "offer" | "answer"; sdp: string }
        | { kind: "ice"; candidate: RTCIceCandidateInit },
    ) => {
      const current = activeRef.current;
      if (!current) return false;
      return sendRealtimeEvent({
        event: "voice_call_signal",
        call_id: current.callId,
        room_id: current.roomId,
        call_mode: current.callMode,
        target_user_id: targetUserId,
        signal,
      });
    },
    [sendRealtimeEvent],
  );

  const peerFor = useCallback(
    (targetUserId: string, targetName: string) => {
      const existing = peersRef.current.get(targetUserId);
      if (existing) return existing;
      const rtcConfig: RTCConfiguration = {
        iceServers: (configRef.current?.ice_servers ?? []).map((server) => ({
          urls: server.urls,
          ...(server.username ? { username: server.username } : {}),
          ...(server.credential ? { credential: server.credential } : {}),
        })),
      };
      const connection = new RTCPeerConnection(rtcConfig);
      const entry: PeerEntry = {
        connection,
        name: targetName,
        pendingCandidates: [],
        videoSender: null,
        failConnection: () => undefined,
      };
      localStreamRef.current?.getTracks().forEach((track) => {
        const sender = connection.addTrack(
          track,
          localStreamRef.current as MediaStream,
        );
        if (track.kind === "video") entry.videoSender = sender;
      });
      peersRef.current.set(targetUserId, entry);

      const clearPeerTimer = () => {
        const timer = peerTimersRef.current.get(targetUserId);
        if (timer !== undefined) window.clearTimeout(timer);
        peerTimersRef.current.delete(targetUserId);
      };
      let failureHandled = false;
      const failConnection = () => {
        if (failureHandled || connection.connectionState === "connected") return;
        failureHandled = true;
        clearPeerTimer();
        peersRef.current.delete(targetUserId);
        connection.close();
        setRemoteStreams((current) =>
          current.filter((item) => item.userId !== targetUserId),
        );
        const anotherPeerIsConnected = [...peersRef.current.values()].some(
          ({ connection: other }) => other.connectionState === "connected",
        );
        if (anotherPeerIsConnected) {
          const current = activeRef.current;
          if (current) {
            const next = {
              ...current,
              participants: current.participants.filter(
                (item) => item.userId !== targetUserId,
              ),
            };
            activeRef.current = next;
            setActive(next);
          }
          setError(`${targetName || "일부 직원"}님과 연결하지 못했습니다.`);
          return;
        }
        closeCall(true);
        setError(connectionFailureMessage(configRef.current));
      };
      entry.failConnection = failConnection;
      const startPeerTimer = (delay: number) => {
        clearPeerTimer();
        peerTimersRef.current.set(
          targetUserId,
          window.setTimeout(failConnection, delay),
        );
      };
      startPeerTimer(PEER_CONNECTION_TIMEOUT_MS);

      connection.onicecandidate = (event) => {
        if (!event.candidate) return;
        sendSignal(targetUserId, {
          kind: "ice",
          candidate: event.candidate.toJSON(),
        });
      };
      connection.ontrack = (event) => {
        const stream = event.streams[0] ?? new MediaStream([event.track]);
        setRemoteStreams((current) => [
          ...current.filter((item) => item.userId !== targetUserId),
          { userId: targetUserId, name: targetName, stream },
        ]);
      };
      connection.onconnectionstatechange = () => {
        if (connection.connectionState === "connected") {
          clearPeerTimer();
          setActive((current) =>
            current ? { ...current, status: "통화 중" } : current,
          );
        }
        if (connection.connectionState === "disconnected") {
          setActive((current) =>
            current ? { ...current, status: "연결을 복구하는 중…" } : current,
          );
          startPeerTimer(PEER_RECONNECT_TIMEOUT_MS);
        }
        if (connection.connectionState === "failed") {
          failConnection();
        }
        if (connection.connectionState === "closed") {
          clearPeerTimer();
          setRemoteStreams((current) =>
            current.filter((item) => item.userId !== targetUserId),
          );
        }
      };
      return entry;
    },
    [closeCall, sendSignal],
  );

  const flushCandidates = useCallback(async (entry: PeerEntry) => {
    const candidates = entry.pendingCandidates.splice(0);
    for (const candidate of candidates) {
      await entry.connection.addIceCandidate(candidate);
    }
  }, []);

  const updateActiveCallMode = useCallback(
    (callMode: CallMode, status = "통화 중") => {
      const current = activeRef.current;
      if (!current) return null;
      const next = { ...current, callMode, status };
      activeRef.current = next;
      setActive(next);
      return next;
    },
    [],
  );

  const renegotiatePeers = useCallback(async () => {
    const peers = [...peersRef.current.entries()];
    if (!peers.length) {
      throw new Error("상대방과 연결된 뒤 통화 종류를 바꿔 주세요.");
    }
    if (peers.some(([, entry]) => entry.connection.signalingState !== "stable")) {
      throw new Error("통화 연결이 안정된 뒤 다시 눌러 주세요.");
    }
    for (const [userId, entry] of peers) {
      const offer = await entry.connection.createOffer();
      await entry.connection.setLocalDescription(offer);
      if (!sendSignal(userId, { kind: "offer", sdp: offer.sdp ?? "" })) {
        throw new Error("통화 전환 신호를 보내지 못했습니다.");
      }
    }
  }, [sendSignal]);

  const ensureLocalVideoTrack = useCallback(async () => {
    const stream = localStreamRef.current;
    if (!stream) throw new Error("통화 중인 마이크 연결을 찾지 못했습니다.");
    const existingTrack = stream
      .getVideoTracks()
      .find((track) => track.readyState === "live");
    if (existingTrack) {
      existingTrack.enabled = true;
      setCameraOff(false);
      setLocalStream(new MediaStream(stream.getTracks()));
      return false;
    }

    const cameraStream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: cameraConstraints("user"),
    });
    const videoTrack = cameraStream.getVideoTracks()[0];
    if (!videoTrack) {
      cameraStream.getTracks().forEach((track) => track.stop());
      throw new Error("사용할 수 있는 카메라를 찾지 못했습니다.");
    }

    stream.addTrack(videoTrack);
    let negotiationNeeded = false;
    for (const entry of peersRef.current.values()) {
      if (entry.videoSender) {
        await entry.videoSender.replaceTrack(videoTrack);
      } else {
        entry.videoSender = entry.connection.addTrack(videoTrack, stream);
        negotiationNeeded = true;
      }
    }
    setLocalStream(new MediaStream(stream.getTracks()));
    setFacingMode("user");
    setCameraOff(false);
    return negotiationNeeded;
  }, []);

  const suspendLocalVideo = useCallback(async () => {
    const stream = localStreamRef.current;
    if (!stream) return;
    await Promise.all(
      [...peersRef.current.values()].map(({ videoSender }) =>
        videoSender ? videoSender.replaceTrack(null) : Promise.resolve(),
      ),
    );
    stream.getVideoTracks().forEach((track) => {
      stream.removeTrack(track);
      track.stop();
    });
    setLocalStream(new MediaStream(stream.getTracks()));
    setFacingMode("user");
    setCameraOff(false);
  }, []);

  const startCall = useCallback(
    async (
      room: Room,
      callMode: CallMode = "audio",
      recipientUserIds?: string[],
    ) => {
      setError("");
      if (!currentUserId || activeRef.current || incomingRef.current) return;
      let callAudioReady = false;
      try {
        callAudioReady = await prepareCallAudio();
      } catch {
        // 대기음 출력 실패가 통화 시도 자체를 막지는 않습니다.
      }
      if (callAudioReady) {
        void startOutgoingCallTone().catch(() => undefined);
      }
      try {
        const currentConfig = await refreshConfig();
        if (!currentConfig.enabled) {
          stopCallTone();
          setError("통화 서버가 아직 준비되지 않았습니다.");
          return;
        }
        await localMedia(callMode);
        const callId = crypto.randomUUID();
        const next: ActiveVoiceCall = {
          callId,
          roomId: room.id,
          roomName: room.name,
          callerUserId: currentUserId,
          callMode,
          status: callMode === "video" ? "영상통화를 거는 중…" : "전화를 거는 중…",
          expectedMemberCount: 0,
          participants: [{ userId: currentUserId, name: currentUserName }],
        };
        activeRef.current = next;
        setActive(next);
        const invitePayload: Record<string, unknown> = {
            event: "voice_call_invite",
            call_id: callId,
            room_id: room.id,
            call_mode: callMode,
        };
        if (recipientUserIds) {
          invitePayload.recipient_user_ids = recipientUserIds;
        }
        if (
          !(await sendRealtimeEventWhenConnected(invitePayload))
        ) {
          closeCall(false);
          setError("채팅 연결이 끊겨 전화를 걸지 못했습니다.");
        }
      } catch (reason) {
        stopCallTone();
        stopMedia();
        setError(
          reason instanceof Error && !(reason instanceof DOMException)
            ? reason.message
            : mediaErrorMessage(reason, callMode),
        );
      }
    },
    [
      closeCall,
      currentUserId,
      currentUserName,
      localMedia,
      refreshConfig,
      sendRealtimeEventWhenConnected,
      stopMedia,
    ],
  );

  const acceptIncoming = useCallback(async () => {
    const current = incomingRef.current;
    if (!current || !currentUserId) return false;
    setError("");
    try {
      const currentConfig = await refreshConfig();
      if (!currentConfig.enabled) {
        setError("통화 서버가 아직 준비되지 않았습니다.");
        return false;
      }
      void prepareCallAudio();
      await localMedia(current.callMode);
      const next: ActiveVoiceCall = {
        callId: current.callId,
        roomId: current.roomId,
        roomName: current.roomName,
        callerUserId: current.callerUserId,
        callMode: current.callMode,
        status: "상대방이 받음 · 연결 중…",
        expectedMemberCount: current.memberCount,
        participants: [
          { userId: currentUserId, name: currentUserName },
          { userId: current.callerUserId, name: current.callerName },
        ],
      };
      incomingRef.current = null;
      setIncoming(null);
      activeRef.current = next;
      setActive(next);
      if (
        !(await sendRealtimeEventWhenConnected({
          event: "voice_call_join",
          call_id: current.callId,
          room_id: current.roomId,
          call_mode: current.callMode,
        }))
      ) {
        closeCall(false);
        setError("채팅 연결이 끊겨 통화에 응답하지 못했습니다.");
        return false;
      }
      return true;
    } catch (reason) {
      stopMedia();
      setError(
        reason instanceof Error && !(reason instanceof DOMException)
          ? reason.message
          : mediaErrorMessage(reason, current.callMode),
      );
      return false;
    }
  }, [
    closeCall,
    currentUserId,
    currentUserName,
    localMedia,
    refreshConfig,
    sendRealtimeEventWhenConnected,
    stopMedia,
  ]);

  const dismissIncoming = useCallback((callId: string) => {
    const current = incomingRef.current;
    if (!current || current.callId !== callId) return false;
    stopCallTone();
    incomingRef.current = null;
    setIncoming(null);
    return true;
  }, []);

  const declineIncoming = useCallback(() => {
    const current = incomingRef.current;
    if (!current) return;
    void sendRealtimeEventWhenConnected({
      event: "voice_call_decline",
      call_id: current.callId,
      room_id: current.roomId,
      call_mode: current.callMode,
    });
    dismissIncoming(current.callId);
  }, [dismissIncoming, sendRealtimeEventWhenConnected]);

  const toggleMuted = useCallback(() => {
    const next = !muted;
    localStreamRef.current?.getAudioTracks().forEach((track) => {
      track.enabled = !next;
    });
    setMuted(next);
  }, [muted]);

  const toggleCamera = useCallback(async () => {
    const current = activeRef.current;
    if (current?.callMode !== "video") return;
    setError("");
    if (cameraOff) {
      try {
        const negotiationNeeded = await ensureLocalVideoTrack();
        if (negotiationNeeded) await renegotiatePeers();
      } catch (reason) {
        setError(mediaErrorMessage(reason, "video"));
      }
      return;
    }
    localStreamRef.current?.getVideoTracks().forEach((track) => {
      track.enabled = false;
    });
    setCameraOff(true);
  }, [cameraOff, ensureLocalVideoTrack, renegotiatePeers]);

  const switchCamera = useCallback(async () => {
    const current = activeRef.current;
    const stream = localStreamRef.current;
    if (current?.callMode !== "video" || !stream) return;
    const previousTrack = stream.getVideoTracks()[0];
    if (!previousTrack) return;
    const nextFacingMode = facingMode === "user" ? "environment" : "user";
    setError("");
    try {
      await previousTrack.applyConstraints({
        facingMode: { exact: nextFacingMode },
      });
      const appliedFacingMode = previousTrack.getSettings().facingMode;
      if (appliedFacingMode === nextFacingMode) {
        setFacingMode(nextFacingMode);
        return;
      }
    } catch {
      // 물리 카메라 전환을 지원하지 않으면 새 트랙으로 교체합니다.
    }

    let replacementStream: MediaStream | null = null;
    try {
      replacementStream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: cameraConstraints(nextFacingMode),
      });
      const replacementTrack = replacementStream.getVideoTracks()[0];
      if (!replacementTrack) throw new Error("전환할 카메라를 찾지 못했습니다.");
      replacementTrack.enabled = !cameraOff;
      await Promise.all(
        [...peersRef.current.values()].map(async (entry) => {
          if (entry.videoSender) await entry.videoSender.replaceTrack(replacementTrack);
        }),
      );
      previousTrack.stop();
      stream.removeTrack(previousTrack);
      stream.addTrack(replacementTrack);
      setLocalStream(new MediaStream(stream.getTracks()));
      setFacingMode(nextFacingMode);
    } catch {
      replacementStream?.getTracks().forEach((track) => track.stop());
      setError("카메라를 전환하지 못했습니다. 카메라를 껐다가 다시 켜 보세요.");
    }
  }, [cameraOff, facingMode]);

  const switchCallMode = useCallback(async () => {
    const current = activeRef.current;
    if (!current || switchingModeRef.current) return;
    if (!peersRef.current.size) {
      setError("상대방과 연결된 뒤 통화 종류를 바꿔 주세요.");
      return;
    }
    const nextMode: CallMode = current.callMode === "audio" ? "video" : "audio";
    if (nextMode === "video") {
      const maxVideoParticipants = configRef.current?.max_video_participants ?? 4;
      const participantCount = Math.max(
        current.expectedMemberCount,
        current.participants.length,
      );
      if (participantCount > maxVideoParticipants) {
        setError(`영상통화는 ${maxVideoParticipants}명 이하에서 사용할 수 있습니다.`);
        return;
      }
    }

    switchingModeRef.current = true;
    setSwitchingMode(true);
    setError("");
    let modeChangeSent = false;
    try {
      const negotiationNeeded = nextMode === "video"
        ? await ensureLocalVideoTrack()
        : false;
      if (
        !(await sendRealtimeEventWhenConnected({
          event: "voice_call_mode",
          call_id: current.callId,
          room_id: current.roomId,
          call_mode: nextMode,
        }))
      ) {
        throw new Error("채팅 연결이 끊겨 통화 종류를 바꾸지 못했습니다.");
      }
      modeChangeSent = true;
      if (nextMode === "audio") {
        await suspendLocalVideo();
      } else if (negotiationNeeded) {
        await renegotiatePeers();
      }
      updateActiveCallMode(nextMode);
    } catch (reason) {
      if (nextMode === "video") {
        await suspendLocalVideo().catch(() => undefined);
        updateActiveCallMode("audio");
        if (modeChangeSent) {
          await sendRealtimeEventWhenConnected({
            event: "voice_call_mode",
            call_id: current.callId,
            room_id: current.roomId,
            call_mode: "audio",
          }).catch(() => false);
        }
      } else if (modeChangeSent) {
        updateActiveCallMode("audio");
      }
      setError(
        reason instanceof Error && !(reason instanceof DOMException)
          ? reason.message
          : mediaErrorMessage(reason, nextMode),
      );
    } finally {
      switchingModeRef.current = false;
      setSwitchingMode(false);
    }
  }, [
    ensureLocalVideoTrack,
    renegotiatePeers,
    sendRealtimeEventWhenConnected,
    suspendLocalVideo,
    updateActiveCallMode,
  ]);

  const presentIncomingCall = useCallback(
    (next: IncomingVoiceCall) => {
      if (!next.callId || !next.roomId || !next.callerUserId) return false;
      if (!next.expiresAt || next.expiresAt <= Date.now()) return false;
      if (
        incomingRef.current?.callId === next.callId &&
        incomingRef.current.roomId === next.roomId
      ) {
        return true;
      }
      if (
        activeRef.current ||
        incomingRef.current ||
        configRef.current?.enabled === false
      ) {
        void sendRealtimeEventWhenConnected({
          event: "voice_call_decline",
          call_id: next.callId,
          room_id: next.roomId,
          call_mode: next.callMode,
        });
        return false;
      }
      incomingRef.current = next;
      setIncoming(next);
      void sendRealtimeEventWhenConnected({
        event: "voice_call_delivery_ack",
        call_id: next.callId,
        room_id: next.roomId,
        call_mode: next.callMode,
        stage: "web_ready",
      });
      return true;
    },
    [sendRealtimeEventWhenConnected],
  );

  const handleRealtimeEvent = useCallback(
    async (payload: VoiceCallRealtimeEvent) => {
      const event = stringValue(payload, "event");
      if (!event.startsWith("voice_call_")) return false;
      if (event === "voice_call_invite") {
        const next: IncomingVoiceCall = {
          callId: stringValue(payload, "call_id"),
          roomId: stringValue(payload, "room_id"),
          roomName: stringValue(payload, "room_name"),
          callerUserId: stringValue(payload, "caller_user_id"),
          callerName: stringValue(payload, "caller_name"),
          memberCount: numberValue(payload, "member_count"),
          callMode: callModeValue(payload),
          expiresAt: numberValue(payload, "expires_at"),
        };
        presentIncomingCall(next);
        return true;
      }
      if (event === "voice_call_delivery_status") {
        const current = activeRef.current;
        if (
          current?.callId === stringValue(payload, "call_id") &&
          payload.delivery_complete === true &&
          payload.delivered !== true
        ) {
          setError(
            "일부 기기에 통화 요청을 전달하지 못했습니다. 잠시 후 다시 시도해 주세요.",
          );
        }
        return true;
      }
      if (event === "voice_call_error") {
        setError(stringValue(payload, "message") || "음성통화를 처리하지 못했습니다.");
        const sourceEvent = stringValue(payload, "source_event");
        const peerScopedError =
          sourceEvent === "voice_call_join" || sourceEvent === "voice_call_signal";
        if (peerScopedError) {
          const targetUserId = stringValue(payload, "target_user_id");
          if (targetUserId) {
            peersRef.current.get(targetUserId)?.failConnection();
          }
          return true;
        }
        if (
          activeRef.current &&
          sourceEvent !== "voice_call_mode"
        ) {
          closeCall(false);
        }
        return true;
      }

      if (event === "voice_call_cancel" || event === "voice_call_leave") {
        const waiting = incomingRef.current;
        const participantUserId = stringValue(payload, "participant_user_id");
        if (
          waiting &&
          waiting.callId === stringValue(payload, "call_id") &&
          waiting.roomId === stringValue(payload, "room_id") &&
          (!participantUserId || participantUserId === waiting.callerUserId)
        ) {
          stopCallTone();
          navigator.vibrate?.(0);
          incomingRef.current = null;
          setIncoming(null);
          return true;
        }
      }

      const current = activeRef.current;
      if (
        !current ||
        current.callId !== stringValue(payload, "call_id") ||
        current.roomId !== stringValue(payload, "room_id")
      ) {
        return true;
      }
      if (event === "voice_call_started") {
        const next = {
          ...current,
          expectedMemberCount: numberValue(payload, "member_count"),
        };
        activeRef.current = next;
        setActive(next);
        return true;
      }
      if (event === "voice_call_mode") {
        const nextMode = callModeValue(payload);
        if (nextMode === current.callMode) return true;
        const participantName = stringValue(payload, "participant_name");
        if (nextMode === "audio") {
          await suspendLocalVideo().catch(() => undefined);
        } else {
          const hasLocalCamera = Boolean(
            localStreamRef.current
              ?.getVideoTracks()
              .some((track) => track.readyState === "live"),
          );
          setCameraOff(!hasLocalCamera);
        }
        updateActiveCallMode(
          nextMode,
          `${participantName || "상대방"}님이 ${
            nextMode === "video" ? "영상" : "음성"
          }으로 전환함`,
        );
        return true;
      }
      if (event === "voice_call_join") {
        const userId = stringValue(payload, "participant_user_id");
        const name = stringValue(payload, "participant_name");
        if (!userId || userId === currentUserId) return true;
        const next = {
          ...current,
          status: "상대방이 받음 · 연결 중…",
          participants: [
            ...current.participants.filter((item) => item.userId !== userId),
            { userId, name },
          ],
        };
        activeRef.current = next;
        setActive(next);
        const entry = peerFor(userId, name);
        try {
          const offer = await entry.connection.createOffer();
          await entry.connection.setLocalDescription(offer);
          sendSignal(userId, { kind: "offer", sdp: offer.sdp ?? "" });
        } catch {
          entry.failConnection();
        }
        return true;
      }
      if (event === "voice_call_signal") {
        const senderUserId = stringValue(payload, "sender_user_id");
        const senderName = stringValue(payload, "sender_name");
        const signal = payload.signal;
        if (!senderUserId || !signal || typeof signal !== "object") return true;
        const typedSignal = signal as Record<string, unknown>;
        const entry = peerFor(senderUserId, senderName);
        try {
          if (typedSignal.kind === "offer" && typeof typedSignal.sdp === "string") {
            await entry.connection.setRemoteDescription({
              type: "offer",
              sdp: typedSignal.sdp,
            });
            await flushCandidates(entry);
            const answer = await entry.connection.createAnswer();
            await entry.connection.setLocalDescription(answer);
            sendSignal(senderUserId, { kind: "answer", sdp: answer.sdp ?? "" });
          } else if (
            typedSignal.kind === "answer" &&
            typeof typedSignal.sdp === "string"
          ) {
            await entry.connection.setRemoteDescription({
              type: "answer",
              sdp: typedSignal.sdp,
            });
            await flushCandidates(entry);
          } else if (
            typedSignal.kind === "ice" &&
            typedSignal.candidate &&
            typeof typedSignal.candidate === "object"
          ) {
            const candidate = typedSignal.candidate as RTCIceCandidateInit;
            if (entry.connection.remoteDescription) {
              await entry.connection.addIceCandidate(candidate);
            } else {
              entry.pendingCandidates.push(candidate);
            }
          }
        } catch {
          entry.failConnection();
        }
        return true;
      }
      if (event === "voice_call_decline") {
        const name = stringValue(payload, "participant_name");
        setError(`${name || "상대방"}님이 통화를 받지 않았습니다.`);
        if (current.expectedMemberCount <= 2) closeCall(false);
        return true;
      }
      if (event === "voice_call_leave") {
        const userId = stringValue(payload, "participant_user_id");
        peersRef.current.get(userId)?.connection.close();
        peersRef.current.delete(userId);
        setRemoteStreams((streams) =>
          streams.filter((item) => item.userId !== userId),
        );
        const nextParticipants = current.participants.filter(
          (item) => item.userId !== userId,
        );
        if (nextParticipants.length <= 1) {
          closeCall(false);
        } else {
          const next = { ...current, participants: nextParticipants };
          activeRef.current = next;
          setActive(next);
        }
        return true;
      }
      return true;
    },
    [
      closeCall,
      currentUserId,
      flushCandidates,
      peerFor,
      presentIncomingCall,
      sendSignal,
      suspendLocalVideo,
      updateActiveCallMode,
    ],
  );

  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    const handleServiceWorkerMessage = (event: MessageEvent) => {
      if (!event.data || typeof event.data !== "object") return;
      void handleRealtimeEvent(event.data as VoiceCallRealtimeEvent);
    };
    navigator.serviceWorker.addEventListener("message", handleServiceWorkerMessage);
    return () => {
      navigator.serviceWorker.removeEventListener("message", handleServiceWorkerMessage);
    };
  }, [handleRealtimeEvent]);

  return {
    available: Boolean(
      currentUserId &&
        config?.enabled &&
        !isReviewer &&
        typeof RTCPeerConnection !== "undefined" &&
        navigator.mediaDevices?.getUserMedia,
    ),
    incoming,
    active,
    muted,
    cameraOff,
    switchingMode,
    facingMode,
    error,
    maxParticipants: config?.max_participants ?? 0,
    maxVideoParticipants: config?.max_video_participants ?? 0,
    localStream,
    remoteStreams,
    startCall,
    acceptIncoming,
    declineIncoming,
    dismissIncoming,
    endCall: () => closeCall(true),
    toggleMuted,
    toggleCamera,
    switchCamera,
    switchCallMode,
    dismissError: () => setError(""),
    presentIncomingCall,
    handleRealtimeEvent,
  };
}
