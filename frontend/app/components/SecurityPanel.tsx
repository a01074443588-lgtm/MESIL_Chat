"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { apiFetch } from "../api";
import type { LoginSession, User } from "../types";

function formatDate(value: string) {
  return new Intl.DateTimeFormat("ko-KR", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function deviceName(userAgent: string | null) {
  if (!userAgent) return "알 수 없는 기기";
  const os = /iPhone|iPad/.test(userAgent)
    ? "아이폰·아이패드"
    : /Android/.test(userAgent)
      ? "안드로이드"
      : /Windows/.test(userAgent)
        ? "윈도우"
        : /Macintosh/.test(userAgent)
          ? "맥"
          : "기타 기기";
  const browser = /Edg\//.test(userAgent)
    ? "Edge"
    : /Chrome\//.test(userAgent)
      ? "Chrome"
      : /Safari\//.test(userAgent)
        ? "Safari"
        : /Firefox\//.test(userAgent)
          ? "Firefox"
          : "브라우저";
  return `${os} · ${browser}`;
}

export function SecurityPanel({
  user,
  mandatory = false,
  onUserChanged,
  onClose,
  onLogout,
}: {
  user: User;
  mandatory?: boolean;
  onUserChanged: (user: User) => void;
  onClose: () => void;
  onLogout: () => void;
}) {
  const [sessions, setSessions] = useState<LoginSession[]>([]);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [saving, setSaving] = useState(false);

  const loadSessions = useCallback(async () => {
    try {
      setSessions(await apiFetch<LoginSession[]>("/api/auth/sessions"));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "로그인 기기를 불러오지 못했습니다.");
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadSessions(), 0);
    return () => window.clearTimeout(timer);
  }, [loadSessions]);

  async function changePassword(event: FormEvent) {
    event.preventDefault();
    setError("");
    setSuccess("");
    if (newPassword !== confirmPassword) {
      setError("새 비밀번호 확인이 일치하지 않습니다.");
      return;
    }
    if (newPassword.length < 12) {
      setError("새 비밀번호는 12자 이상이어야 합니다.");
      return;
    }
    setSaving(true);
    try {
      const updated = await apiFetch<User>("/api/auth/password", {
        method: "POST",
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setSuccess("비밀번호를 변경하고 다른 기기의 로그인을 종료했습니다.");
      onUserChanged(updated);
      await loadSessions();
      if (mandatory) onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "비밀번호를 변경하지 못했습니다.");
    } finally {
      setSaving(false);
    }
  }

  async function revokeSession(sessionId: string) {
    setError("");
    setSuccess("");
    try {
      await apiFetch(`/api/auth/sessions/${sessionId}`, { method: "DELETE" });
      setSuccess("선택한 기기의 로그인을 종료했습니다.");
      await loadSessions();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "로그인 기기를 종료하지 못했습니다.");
    }
  }

  async function revokeOthers() {
    setError("");
    setSuccess("");
    try {
      await apiFetch("/api/auth/sessions/revoke-others", {
        method: "POST",
        body: "{}",
      });
      setSuccess("현재 기기를 제외한 모든 로그인을 종료했습니다.");
      await loadSessions();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "다른 로그인을 종료하지 못했습니다.");
    }
  }

  return (
    <div className={`security-layer ${mandatory ? "mandatory" : ""}`}>
      {!mandatory ? (
        <button className="drawer-backdrop" onClick={onClose} aria-label="보안 설정 닫기" />
      ) : null}
      <section className="security-panel" aria-label="보안 설정">
        <header className="security-header">
          <div>
            <span className="eyebrow">내 계정</span>
            <h2>{mandatory ? "새 비밀번호가 필요합니다" : "비밀번호·로그인 기기"}</h2>
            <p>
              {mandatory
                ? `${user.full_name}님, 임시 비밀번호를 본인만 아는 비밀번호로 바꿔 주세요.`
                : "비밀번호를 바꾸거나 사용하지 않는 기기의 접속을 종료할 수 있습니다."}
            </p>
          </div>
          {!mandatory ? (
            <button className="icon-button" onClick={onClose} aria-label="닫기">
              ×
            </button>
          ) : null}
        </header>

        {success ? <p className="form-success">{success}</p> : null}
        {error ? (
          <p className="form-error" role="alert">
            {error}
          </p>
        ) : null}

        <form className="security-form" onSubmit={changePassword}>
          <h3>비밀번호 변경</h3>
          <input
            className="sr-only"
            name="username"
            autoComplete="username"
            value={user.username}
            readOnly
            tabIndex={-1}
            aria-hidden="true"
          />
          <label>
            현재 또는 임시 비밀번호
            <input
              type="password"
              autoComplete="current-password"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
              required
            />
          </label>
          <label>
            새 비밀번호
            <input
              type="password"
              autoComplete="new-password"
              minLength={12}
              maxLength={200}
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              required
            />
            <small>12자 이상으로 다른 서비스와 겹치지 않게 정해 주세요.</small>
          </label>
          <label>
            새 비밀번호 확인
            <input
              type="password"
              autoComplete="new-password"
              minLength={12}
              maxLength={200}
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              required
            />
          </label>
          <button className="button button-primary button-large" disabled={saving}>
            {saving ? "변경 중…" : "비밀번호 변경"}
          </button>
        </form>

        {!mandatory ? (
          <section className="session-section">
            <div className="section-heading">
              <div>
                <h3>로그인된 기기</h3>
                <p>최근 사용하지 않은 기기는 종료해 주세요.</p>
              </div>
              {sessions.some((item) => !item.is_current) ? (
                <button className="button button-secondary" onClick={() => void revokeOthers()}>
                  다른 기기 모두 종료
                </button>
              ) : null}
            </div>
            <div className="session-list">
              {sessions.map((session) => (
                <article className="session-card" key={session.id}>
                  <span className="session-icon">기기</span>
                  <div>
                    <strong>
                      {deviceName(session.user_agent)}
                      {session.is_current ? <em>현재 기기</em> : null}
                    </strong>
                    <small>최근 사용 {formatDate(session.last_seen_at)}</small>
                    <small>로그인 {formatDate(session.created_at)}</small>
                  </div>
                  {!session.is_current ? (
                    <button
                      className="button button-danger subtle"
                      onClick={() => void revokeSession(session.id)}
                    >
                      종료
                    </button>
                  ) : null}
                </article>
              ))}
            </div>
          </section>
        ) : (
          <button className="text-button security-logout" onClick={onLogout}>
            다른 계정으로 로그인
          </button>
        )}
      </section>
    </div>
  );
}
