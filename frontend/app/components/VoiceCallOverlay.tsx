import { useEffect, useRef, useState } from "react";
import type {
  ActiveVoiceCall,
  IncomingVoiceCall,
  RemoteCallStream,
} from "../voiceCall";

function StreamPlayer({
  stream,
  video,
}: {
  stream: RemoteCallStream;
  video: boolean;
}) {
  const ref = useRef<HTMLMediaElement | null>(null);
  const [playingStreamId, setPlayingStreamId] = useState("");
  useEffect(() => {
    if (!ref.current) return;
    ref.current.srcObject = stream.stream;
    void ref.current.play().catch(() => {
      // iOS가 자동재생을 늦추면 통화 응답 사용자 조작 뒤 다시 재생됩니다.
    });
  }, [stream]);

  if (!video) {
    return (
      <audio
        ref={(node) => {
          ref.current = node;
        }}
        autoPlay
        playsInline
        aria-label={`${stream.name} 음성`}
      />
    );
  }
  const playing = playingStreamId === stream.stream.id;
  return (
    <div className={`video-call-tile ${playing ? "playing" : "preparing"}`}>
      <video
        ref={(node) => {
          ref.current = node;
        }}
        autoPlay
        playsInline
        aria-label={`${stream.name} 영상`}
        onPlaying={() => setPlayingStreamId(stream.stream.id)}
      />
      {!playing ? (
        <span className="video-call-preparing">영상 연결 중…</span>
      ) : null}
      <span>{stream.name}</span>
    </div>
  );
}

function LocalVideo({
  stream,
  facingMode,
  cameraOff,
}: {
  stream: MediaStream | null;
  facingMode: "user" | "environment";
  cameraOff: boolean;
}) {
  const ref = useRef<HTMLVideoElement | null>(null);
  const [playingStreamId, setPlayingStreamId] = useState("");
  useEffect(() => {
    if (!ref.current || !stream) return;
    ref.current.srcObject = stream;
    void ref.current.play().catch(() => undefined);
  }, [stream]);

  const hasVideoTrack = Boolean(stream?.getVideoTracks().length);
  const playing = Boolean(stream && playingStreamId === stream.id);

  return (
    <div
      className={`video-call-local ${cameraOff ? "camera-off" : ""} ${
        !cameraOff && (!hasVideoTrack || !playing) ? "preparing" : ""
      }`}
    >
      <video
        ref={ref}
        autoPlay
        muted
        playsInline
        aria-label="내 카메라"
        className={facingMode === "user" ? "mirror" : ""}
        onPlaying={() => {
          if (stream) setPlayingStreamId(stream.id);
        }}
      />
      {cameraOff ? <span>카메라 꺼짐</span> : <small>나</small>}
      {!cameraOff && (!hasVideoTrack || !playing) ? (
        <span className="video-call-preparing">카메라 준비 중…</span>
      ) : null}
    </div>
  );
}

export function VoiceCallOverlay({
  incoming,
  active,
  muted,
  cameraOff,
  switchingMode,
  facingMode,
  error,
  localStream,
  remoteStreams,
  canChangeAudioRoute,
  speakerphoneOn,
  onAccept,
  onDecline,
  onEnd,
  onToggleMuted,
  onToggleCamera,
  onSwitchCamera,
  onToggleAudioRoute,
  onSwitchCallMode,
  onDismissError,
}: {
  incoming: IncomingVoiceCall | null;
  active: ActiveVoiceCall | null;
  muted: boolean;
  cameraOff: boolean;
  switchingMode: boolean;
  facingMode: "user" | "environment";
  error: string;
  localStream: MediaStream | null;
  remoteStreams: RemoteCallStream[];
  canChangeAudioRoute: boolean;
  speakerphoneOn: boolean;
  onAccept: () => void;
  onDecline: () => void;
  onEnd: () => void;
  onToggleMuted: () => void;
  onToggleCamera: () => void;
  onSwitchCamera: () => void;
  onToggleAudioRoute: () => void;
  onSwitchCallMode: () => void;
  onDismissError: () => void;
}) {
  return (
    <>
      {incoming ? (
        <div className="voice-call-layer" role="presentation">
          <section
            className="voice-call-card incoming"
            role="dialog"
            aria-modal="true"
            aria-labelledby="incoming-call-title"
          >
            <span className="voice-call-icon" aria-hidden="true">
              {incoming.callMode === "video" ? "📹" : "☎"}
            </span>
            <p>{incoming.roomName}</p>
            <h2 id="incoming-call-title">{incoming.callerName}님의 전화</h2>
            <small>
              {incoming.memberCount > 2 ? `${incoming.memberCount}명 ` : ""}
              {incoming.callMode === "video" ? "영상통화" : "음성통화"}
            </small>
            <div className="voice-call-actions">
              <button type="button" className="voice-call-decline" onClick={onDecline}>
                거절
              </button>
              <button type="button" className="voice-call-accept" onClick={onAccept}>
                받기
              </button>
            </div>
          </section>
        </div>
      ) : null}
      {active?.callMode === "video" ? (
        <section
          className="video-call-stage"
          role="dialog"
          aria-modal="true"
          aria-label="진행 중인 영상통화"
        >
          <header>
            <strong>{active.roomName}</strong>
            <span>{active.status}</span>
          </header>
          <div className={`video-call-grid count-${Math.max(1, remoteStreams.length)}`}>
            {remoteStreams.length ? (
              remoteStreams.map((stream) => (
                <StreamPlayer key={stream.userId} stream={stream} video />
              ))
            ) : (
              <div className="video-call-waiting">
                <span aria-hidden="true">👤</span>
                <strong>상대방을 기다리고 있습니다</strong>
              </div>
            )}
          </div>
          <LocalVideo stream={localStream} facingMode={facingMode} cameraOff={cameraOff} />
          <div className="video-call-controls">
            <button type="button" onClick={onToggleMuted}>
              <span aria-hidden="true">{muted ? "🔇" : "🎙"}</span>
              {muted ? "마이크 켜기" : "음소거"}
            </button>
            <button type="button" onClick={onToggleCamera}>
              <span aria-hidden="true">📷</span>
              {cameraOff ? "카메라 켜기" : "카메라 끄기"}
            </button>
            {canChangeAudioRoute ? (
              <button type="button" onClick={onToggleAudioRoute}>
                <span aria-hidden="true">{speakerphoneOn ? "📱" : "🔊"}</span>
                {speakerphoneOn ? "수화기" : "스피커"}
              </button>
            ) : null}
            <button type="button" onClick={onSwitchCamera} disabled={cameraOff}>
              <span aria-hidden="true">🔄</span>
              앞·뒤 전환
            </button>
            <button
              type="button"
              className="voice-call-mode"
              onClick={onSwitchCallMode}
              disabled={switchingMode}
            >
              <span aria-hidden="true">☎</span>
              {switchingMode ? "전환 중…" : "음성으로"}
            </button>
            <button type="button" className="voice-call-end" onClick={onEnd}>
              <span aria-hidden="true">☎</span>
              종료
            </button>
          </div>
        </section>
      ) : null}
      {active?.callMode === "audio" ? (
        <section className="voice-call-bar" role="dialog" aria-label="진행 중인 음성통화">
          <div>
            <span className="voice-call-live" aria-hidden="true" />
            <strong>{active.roomName}</strong>
            <small>{active.status} · {active.participants.map((item) => item.name).join(", ")}</small>
          </div>
          <div className="voice-call-bar-actions">
            <button type="button" onClick={onToggleMuted}>
              {muted ? "마이크 켜기" : "음소거"}
            </button>
            {canChangeAudioRoute ? (
              <button type="button" onClick={onToggleAudioRoute}>
                {speakerphoneOn ? "수화기" : "스피커"}
              </button>
            ) : null}
            <button
              type="button"
              onClick={onSwitchCallMode}
              disabled={switchingMode}
            >
              {switchingMode ? "전환 중…" : "영상으로"}
            </button>
            <button type="button" className="voice-call-end" onClick={onEnd}>
              통화 종료
            </button>
          </div>
          <div className="voice-call-audio">
            {remoteStreams.map((stream) => (
              <StreamPlayer key={stream.userId} stream={stream} video={false} />
            ))}
          </div>
        </section>
      ) : null}
      {error ? (
        <div className="voice-call-error" role="alert">
          <span>{error}</span>
          <button type="button" onClick={onDismissError}>확인</button>
        </div>
      ) : null}
    </>
  );
}
