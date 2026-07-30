import type { Message } from "./types";

export type NotificationSoundMode = "off" | "important" | "all";

const STORAGE_KEY = "smcodi:notification-sound";
const BRAND_SOUND_URL = "/sounds/mesil-medic-voice-v2.wav";
let audioContext: AudioContext | null = null;
let brandAudio: HTMLAudioElement | null = null;

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

async function playFallbackTone() {
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

  const now = audioContext.currentTime;
  const notes = [
    { frequency: 523.25, start: 0, duration: 0.16 },
    { frequency: 659.25, start: 0.19, duration: 0.16 },
    { frequency: 783.99, start: 0.38, duration: 0.2 },
  ];
  for (const note of notes) {
    const noteStart = now + note.start;
    const noteEnd = noteStart + note.duration;
    const gain = audioContext.createGain();
    gain.gain.setValueAtTime(0.0001, noteStart);
    gain.gain.exponentialRampToValueAtTime(0.24, noteStart + 0.025);
    gain.gain.exponentialRampToValueAtTime(0.0001, noteEnd);
    gain.connect(audioContext.destination);

    const oscillator = audioContext.createOscillator();
    oscillator.type = "sine";
    oscillator.frequency.setValueAtTime(note.frequency, noteStart);
    oscillator.connect(gain);
    oscillator.start(noteStart);
    oscillator.stop(noteEnd);
  }
  return true;
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
