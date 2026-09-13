"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { apiFetch } from "../api";
import type { LoginSession, User } from "../types";

const usernamePattern = /^[가-힣a-zA-Z0-9][가-힣a-zA-Z0-9._-]{1,79}$/;

type UsernameAvailability = "current" | "checking" | "available" | "taken" | "invalid" | "error";

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
  reviewOnly = false,
}: {
  user: User;
  mandatory?: boolean;
  onUserChanged: (user: User) => void;
  onClose: () => void;
  onLogout: () => void;
  reviewOnly?: boolean;
}) {
  const [sessions, setSessions] = useState<LoginSession[]>([]);
  const [newUsername, setNewUsername] = useState(user.username);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [saving, setSaving] = useState(false);
  const [usernameAvailability, setUsernameAvailability] =
    useState<UsernameAvailability>("current");
  const hasUnsavedSecurityChanges =
    newUsername.trim() !== user.username ||
    currentPassword.length > 0 ||
    newPassword.length > 0 ||
    confirmPassword.length > 0;

  const loadSessions = useCallback(async () => {
    try {
      setSessions(await apiFetch<LoginSession[]>("/api/auth/sessions"));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "로그인 기기를 불러오지 못했습니다.");
    }
  }, []);

  useEffect(() => {
    if (mandatory || reviewOnly) return;
    const timer = window.setTimeout(() => void loadSessions(), 0);
    return () => window.clearTimeout(timer);
  }, [loadSessions, mandatory, reviewOnly]);

  useEffect(() => {
    const normalizedUsername = newUsername.trim();
    if (
      reviewOnly ||
      normalizedUsername === user.username ||
      !usernamePattern.test(normalizedUsername)
    ) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      const query = new URLSearchParams({ username: normalizedUsername });
      void apiFetch<{ available: boolean; is_current: boolean }>(
        `/api/auth/username-availability?${query.toString()}`,
      )
        .then((result) => {
          if (!cancelled) {
            setUsernameAvailability(
              result.is_current ? "current" : result.available ? "available" : "taken",
            );
          }
        })
        .catch(() => {
          if (!cancelled) setUsernameAvailability("error");
        });
    }, 350);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [newUsername, reviewOnly, user.username]);

  async function changeLoginCredentials(event: FormEvent) {
    event.preventDefault();
    setError("");
    setSuccess("");
    const normalizedUsername = newUsername.trim();
    if (!usernamePattern.test(normalizedUsername)) {
      setError(
        "로그인 아이디는 한글·영문·숫자로 시작하고 전체 2~80자로 입력해 주세요.",
      );
      return;
    }
    const usernameChanged = normalizedUsername !== user.username;
    const passwordChanged = newPassword.length > 0 || confirmPassword.length > 0;
    if (!usernameChanged && !passwordChanged) {
      setError("바꿀 로그인 아이디 또는 새 비밀번호를 입력해 주세요.");
      return;
    }
    if (usernameChanged && usernameAvailability !== "available") {
      setError(
        usernameAvailability === "taken"
          ? "이미 사용 중인 로그인 아이디입니다."
          : "로그인 아이디 중복 확인이 끝난 뒤 저장해 주세요.",
      );
      return;
    }
    if (passwordChanged && newPassword !== confirmPassword) {
      setError("새 비밀번호 확인이 일치하지 않습니다.");
      return;
    }
    if (passwordChanged && newPassword.length < 6) {
      setError("새 비밀번호는 6자 이상이어야 합니다.");
      return;
    }
    setSaving(true);
    try {
      const updated = await apiFetch<User>("/api/auth/password", {
        method: "POST",
        body: JSON.stringify({
          current_password: currentPassword,
          new_username: normalizedUsername,
          new_password: passwordChanged ? newPassword : null,
        }),
      });
      setNewUsername(updated.username);
      setUsernameAvailability("current");
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setSuccess(
        usernameChanged && passwordChanged
          ? `로그인 아이디가 “${updated.username}”로 바뀌었고 비밀번호도 변경했습니다.`
          : usernameChanged
            ? `로그인 아이디가 “${updated.username}”로 바뀌었습니다. 비밀번호는 그대로입니다.`
            : "비밀번호를 변경했습니다.",
      );
      onUserChanged(updated);
      if (mandatory) {
        onClose();
        return;
      }
      await loadSessions();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "로그인 정보를 변경하지 못했습니다.");
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

  if (reviewOnly) {
    return (
      <div className="security-layer">
        <button className="drawer-backdrop" onClick={onClose} aria-label="보안 설정 닫기" />
        <section
          className="security-panel mentor-security-review desktop-resizable-dialog"
          aria-label="보안 설정 검토"
        >
          <header className="security-header">
            <div>
              <span className="eyebrow">DEV 보안 정책 · 읽기 전용</span>
              <h2>현재 적용된 보안 경계</h2>
              <p>비밀번호·세션 식별자·내부 주소·인증 토큰 원문은 표시하지 않습니다.</p>
            </div>
            <button className="icon-button" onClick={onClose} aria-label="닫기">×</button>
          </header>
          <div className="mentor-policy-grid">
            <article><strong>외부 연결</strong><span>HTTPS Tunnel을 사용하며 인터넷 공개 TCP 포트는 없습니다.</span></article>
            <article><strong>사용자 인증</strong><span>DEV 아이디·비밀번호 로그인과 기간제 검토 계정을 사용합니다.</span></article>
            <article><strong>자료 경계</strong><span>명시적인 합성 방·합성 직원·합성 어르신만 이 계정에 표시합니다.</span></article>
            <article><strong>변경 잠금</strong><span>관리 변경·파일 업로드·비밀 설정·공식 저장·서명·전송은 차단됩니다.</span></article>
            <article><strong>AI 외부 전송</strong><span>합성 비식별 정책을 통과한 자료만 허용하며 현재 처리 위치를 결과에 표시합니다.</span></article>
            <article><strong>계정 회수</strong><span>기간 종료 시 계정 비활성화와 활성 세션 회수를 함께 수행합니다.</span></article>
          </div>
          <p className="mentor-review-lock-note">계정 정보나 로그인 기기를 바꾸려면 DEV 관리자가 별도 보호 경로에서 수행해야 합니다.</p>
        </section>
      </div>
    );
  }

  return (
    <div className={`security-layer ${mandatory ? "mandatory" : ""}`}>
      {!mandatory ? (
        <button className="drawer-backdrop" onClick={onClose} aria-label="보안 설정 닫기" />
      ) : null}
      <section
        className={`security-panel ${mandatory ? "" : "desktop-resizable-dialog"}`}
        aria-label="보안 설정"
        data-app-update-dirty={hasUnsavedSecurityChanges ? "true" : undefined}
      >
        <header className="security-header">
          <div>
            <span className="eyebrow">내 계정</span>
            <h2>
              {mandatory
                ? "내 로그인 정보를 확인해 주세요"
                : "로그인 정보·로그인 기기"}
            </h2>
            <p>
              {mandatory
                ? `${user.full_name}님, 로그인 아이디를 확인하고 원하는 경우 비밀번호도 바꿀 수 있습니다.`
                : "로그인 아이디와 비밀번호를 바꾸거나 사용하지 않는 기기의 접속을 종료할 수 있습니다."}
            </p>
          </div>
          {!mandatory ? (
            <button className="icon-button" onClick={onClose} aria-label="닫기">
              ×
            </button>
          ) : null}
        </header>

        {success ? (
          <p className="form-success" role="status" aria-live="polite">
            {success}
          </p>
        ) : null}
        {error ? (
          <p className="form-error" role="alert">
            {error}
          </p>
        ) : null}

        <form className="security-form" onSubmit={changeLoginCredentials}>
          <h3>{mandatory ? "내 로그인 정보 정하기" : "로그인 정보 변경"}</h3>
          <label>
            로그인 아이디
            <input
              name="username"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              minLength={2}
              maxLength={80}
              pattern="[가-힣a-zA-Z0-9][가-힣a-zA-Z0-9._-]{1,79}"
              value={newUsername}
              onChange={(event) => {
                const nextUsername = event.target.value;
                const normalizedUsername = nextUsername.trim();
                setNewUsername(nextUsername);
                setUsernameAvailability(
                  normalizedUsername === user.username
                    ? "current"
                    : usernamePattern.test(normalizedUsername)
                      ? "checking"
                      : "invalid",
                );
              }}
              required
            />
            <small>
              한글·영문·숫자로 시작하고, 전체 2~80자 안에서 . _ - 기호도 사용할 수
              있습니다.
            </small>
            <small
              className={`username-availability ${usernameAvailability}`}
              role="status"
              aria-live="polite"
            >
              {usernameAvailability === "current"
                ? "현재 사용 중인 아이디입니다."
                : usernameAvailability === "checking"
                  ? "아이디 중복 확인 중…"
                  : usernameAvailability === "available"
                    ? "사용할 수 있는 아이디입니다."
                    : usernameAvailability === "taken"
                      ? "이미 다른 직원이 사용 중인 아이디입니다."
                      : usernameAvailability === "invalid"
                        ? "아이디 형식을 확인해 주세요."
                        : "중복 확인을 완료하지 못했습니다. 잠시 후 다시 입력해 주세요."}
            </small>
          </label>
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
              minLength={6}
              maxLength={200}
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              placeholder="바꾸지 않으려면 비워 두세요"
            />
            <small>변경할 때만 입력하세요. 숫자만 또는 문자만 사용해도 되며 6자 이상입니다.</small>
          </label>
          <label>
            새 비밀번호 확인
            <input
              type="password"
              autoComplete="new-password"
              minLength={6}
              maxLength={200}
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              placeholder="새 비밀번호를 한 번 더 입력"
            />
          </label>
          <button
            className="button button-primary button-large"
            disabled={
              saving ||
              (newUsername.trim() !== user.username && usernameAvailability !== "available")
            }
          >
            {saving ? "저장 중…" : "변경 내용 저장"}
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
