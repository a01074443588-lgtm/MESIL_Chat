import type { Message } from "./types";

export type NotificationSoundMode = "off" | "important" | "all";

const STORAGE_KEY = "smcodi:notification-sound";
const BRAND_SOUND_URL = "/sounds/mesil-medic-voice-v2.wav";
let audioContext: AudioContext | null = null;
let brandAudio: HTMLAudioElement | null = null;
let callToneTimer: number | null = null;
let callToneGeneration = 0;
const activeCallOscillators = new Set<OscillatorNode>();

type CallToneMode = "incoming" | "outgoing";

export function readNotificationSoundMode(): NotificationSoundMode {
  if (typeof window === "undefined") return "all";
  const saved = window.localStorage.getItem(STORAGE_KEY);
  return saved === "off" || saved === "important" || saved === "all" ? saved : "all";
}

export function saveNotificationSoundMode(mode: NotificationSoundMode) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, mode);
}

export function shouldPlayMessageNotification(
  mode: NotificationSoundMode,
  message: Message,
  currentUserId: string,
) {
  if (mode === "off" || message.sender_id === currentUserId) return false;
  if (mode === "all") return true;
  return (
    message.message_type === "notice" ||
    message.message_type === "handover" ||
    message.message_type === "work_request" ||
    message.message_type === "report" ||
    Boolean(message.action_item)
  );
}

async function runningAudioContext() {
  if (typeof window === "undefined") return false;
  const AudioContextClass =
    window.AudioContext ??
    (
      window as typeof window & {
        webkitAudioContext?: typeof AudioContext;
      }
    ).webkitAudioContext;
  if (!AudioContextClass) return false;

  audioContext ??= new AudioContextClass();
  if (audioContext.state === "suspended") {
    await audioContext.resume();
  }
  if (audioContext.state !== "running") return false;
  return audioContext;
}

async function playFallbackTone() {
  const context = await runningAudioContext();
  if (!context) return false;

  const now = context.currentTime;
  const notes = [
    { frequency: 523.25, start: 0, duration: 0.16 },
    { frequency: 659.25, start: 0.19, duration: 0.16 },
    { frequency: 783.99, start: 0.38, duration: 0.2 },
  ];
  for (const note of notes) {
    const noteStart = now + note.start;
    const noteEnd = noteStart + note.duration;
    const gain = context.createGain();
    gain.gain.setValueAtTime(0.0001, noteStart);
    gain.gain.exponentialRampToValueAtTime(0.24, noteStart + 0.025);
    gain.gain.exponentialRampToValueAtTime(0.0001, noteEnd);
    gain.connect(context.destination);

    const oscillator = context.createOscillator();
    oscillator.type = "sine";
    oscillator.frequency.setValueAtTime(note.frequency, noteStart);
    oscillator.connect(gain);
    oscillator.start(noteStart);
    oscillator.stop(noteEnd);
  }
  return true;
}

function scheduleCallTone(
  context: AudioContext,
  {
    frequency,
    start,
    duration,
    volume,
  }: {
    frequency: number;
    start: number;
    duration: number;
    volume: number;
  },
) {
  const gain = context.createGain();
  const end = start + duration;
  gain.gain.setValueAtTime(0.0001, start);
  gain.gain.exponentialRampToValueAtTime(volume, start + 0.025);
  gain.gain.setValueAtTime(volume, Math.max(start + 0.03, end - 0.04));
  gain.gain.exponentialRampToValueAtTime(0.0001, end);
  gain.connect(context.destination);

  const oscillator = context.createOscillator();
  oscillator.type = "sine";
  oscillator.frequency.setValueAtTime(frequency, start);
  oscillator.connect(gain);
  oscillator.onended = () => activeCallOscillators.delete(oscillator);
  activeCallOscillators.add(oscillator);
  oscillator.start(start);
  oscillator.stop(end);
}

function scheduleCallToneBurst(context: AudioContext, mode: CallToneMode) {
  const now = context.currentTime + 0.02;
  if (mode === "incoming") {
    for (const offset of [0, 0.82]) {
      scheduleCallTone(context, {
        frequency: 440,
        start: now + offset,
        duration: 0.68,
        volume: 0.13,
      });
      scheduleCallTone(context, {
        frequency: 480,
        start: now + offset,
        duration: 0.68,
        volume: 0.11,
      });
    }
    return;
  }

  for (const offset of [0, 0.62]) {
    scheduleCallTone(context, {
      frequency: 425,
      start: now + offset,
      duration: 0.42,
      volume: 0.09,
    });
  }
}

export function stopCallTone() {
  callToneGeneration += 1;
  if (callToneTimer !== null && typeof window !== "undefined") {
    window.clearInterval(callToneTimer);
  }
  callToneTimer = null;
  activeCallOscillators.forEach((oscillator) => {
    try {
      oscillator.stop();
    } catch {
      // 이미 종료된 음은 다시 정지할 필요가 없습니다.
    }
  });
  activeCallOscillators.clear();
}

async function startCallTone(mode: CallToneMode) {
  stopCallTone();
  if (typeof window === "undefined") return false;
  const generation = callToneGeneration;
  const context = await runningAudioContext();
  if (!context || generation !== callToneGeneration) return false;
  brandAudio?.pause();
  scheduleCallToneBurst(context, mode);
  const repeatMs = mode === "incoming" ? 3_800 : 2_800;
  callToneTimer = window.setInterval(() => {
    if (generation === callToneGeneration && context.state === "running") {
      scheduleCallToneBurst(context, mode);
    }
  }, repeatMs);
  return true;
}

export async function prepareCallAudio() {
  return Boolean(await runningAudioContext());
}

export function startIncomingCallRingtone() {
  return startCallTone("incoming");
}

export function startOutgoingCallTone() {
  return startCallTone("outgoing");
}

async function playBrandSound() {
  if (typeof window === "undefined") return false;
  brandAudio ??= new Audio(BRAND_SOUND_URL);
  brandAudio.preload = "auto";
  brandAudio.volume = 1;
  brandAudio.pause();
  brandAudio.currentTime = 0;
  try {
    await brandAudio.play();
    return true;
  } catch {
    return playFallbackTone();
  }
}

export async function playNotificationTest() {
  return playBrandSound();
}

export function playMessageNotification(
  mode: NotificationSoundMode,
  message: Message,
  currentUserId: string,
) {
  if (!shouldPlayMessageNotification(mode, message, currentUserId)) return;
  void playBrandSound().catch(() => {
    // 모바일 브라우저가 아직 소리를 허용하지 않은 경우 다음 사용자 조작 때 다시 시도합니다.
  });
}

export function playCommentNotification(
  mode: NotificationSoundMode,
  currentUserId: string,
  commentAuthorId: string | undefined,
  notificationUserIds: string[] | undefined,
) {
  if (
    mode === "off" ||
    !commentAuthorId ||
    commentAuthorId === currentUserId ||
    !notificationUserIds?.includes(currentUserId)
  ) {
    return;
  }
  void playBrandSound().catch(() => {
    // 브라우저가 아직 소리를 허용하지 않은 경우 잠금화면 푸시 알림으로 안내합니다.
  });
}
