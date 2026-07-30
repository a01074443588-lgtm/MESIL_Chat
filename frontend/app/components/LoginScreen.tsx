"use client";

import { FormEvent, useState } from "react";
import { apiFetch } from "../api";
import type { User } from "../types";
import { PwaInstallButton } from "./PwaInstallButton";

type LoginResponse = {
  user: User;
  expires_at: string;
};

export function LoginScreen({
  onLogin,
  sessionNotice = "",
}: {
  onLogin: (user: User) => void;
  sessionNotice?: string;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const result = await apiFetch<LoginResponse>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      onLogin(result.user);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "로그인하지 못했습니다.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-page">
      <section className="login-card" aria-labelledby="login-title">
        <div className="login-brand">
          <span className="brand-mark" aria-hidden="true">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/brand/silvermedical-logo.jpg" alt="" />
          </span>
          <div>
            <strong>MESIL_Chat</strong>
            <small>메디컬 실버 채팅</small>
          </div>
        </div>
        <div className="login-heading">
          <div className="login-context">
            <span className="eyebrow">직원 전용</span>
            <span className="login-demo-label">내부 데모(가명 자료)</span>
          </div>
          <h1 id="login-title">MESIL_Chat</h1>
          <p>업무대화와 공지를 빠르고 안전하게 확인하세요.</p>
        </div>
        <form className="login-form" onSubmit={submit}>
          {sessionNotice ? (
            <p className="form-notice" role="status">
              {sessionNotice}
            </p>
          ) : null}
          <label>
            로그인 아이디
            <input
              autoComplete="username"
              autoFocus
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              placeholder="직원 아이디"
              required
            />
          </label>
          <label>
            <span>비밀번호</span>
            <span className="password-field">
              <input
                autoComplete="current-password"
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="비밀번호"
                required
              />
              <button
                className="password-toggle"
                type="button"
                aria-pressed={showPassword}
                onClick={() => setShowPassword((current) => !current)}
              >
                {showPassword ? "숨기기" : "보기"}
              </button>
            </span>
          </label>
          {error ? (
            <p className="form-error" role="alert">
              {error}
            </p>
          ) : null}
          <button className="button button-primary button-large" disabled={submitting}>
            {submitting ? "확인 중…" : "로그인"}
          </button>
        </form>
        <div className="login-support">
          <PwaInstallButton />
          <p>계정 문제는 관리자에게 문의해 주세요.</p>
        </div>
        <a className="reviewer-entry-link" href="/reviewer">
          AI 챌린지 심사위원 체험 안내
        </a>
      </section>
      <p className="privacy-note">실제 어르신 개인정보를 일반 채팅에 입력하지 마세요.</p>
    </main>
  );
}
