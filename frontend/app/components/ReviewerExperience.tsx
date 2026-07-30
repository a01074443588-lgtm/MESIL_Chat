"use client";

import { useRef, useState } from "react";
import { apiFetch } from "../api";
import { saveReviewerLanding, type ReviewerDestination } from "../reviewerLanding";
import type { User } from "../types";

type ReviewerExperience = "care" | "social_worker" | "realtime_secondary";

type ReviewerSessionResponse = {
  user: User;
  expires_at: string;
  destination: ReviewerDestination;
  room_id?: string | null;
};

const experienceLabels: Record<ReviewerExperience, string> = {
  care: "요양보호사",
  social_worker: "사회복지사",
  realtime_secondary: "실시간 추가",
};

export function ReviewerExperience() {
  const [pending, setPending] = useState<ReviewerExperience | null>(null);
  const [error, setError] = useState("");
  const startingRef = useRef(false);

  async function returnToStaffLogin() {
    setError("");
    try {
      await apiFetch("/api/auth/logout", { method: "POST", body: "{}" });
    } catch {
      // 활성 체험 세션이 없는 경우에도 일반 직원 로그인 화면으로 이동합니다.
    }
    window.location.replace("/");
  }

  async function startExperience(experience: ReviewerExperience) {
    if (startingRef.current) return;
    startingRef.current = true;
    setPending(experience);
    setError("");
    try {
      const result = await apiFetch<ReviewerSessionResponse>(
        "/api/auth/reviewer-session",
        {
          method: "POST",
          body: JSON.stringify({ experience }),
        },
      );
      saveReviewerLanding(result.destination, result.room_id);
      window.location.replace("/");
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "체험 화면을 열지 못했습니다. 잠시 후 다시 시도해 주세요.",
      );
      setPending(null);
    } finally {
      startingRef.current = false;
    }
  }

  return (
    <main className="reviewer-page">
      <section className="reviewer-card" aria-labelledby="reviewer-title">
        <header className="reviewer-brand">
          <span className="brand-mark reviewer-brand-mark" aria-hidden="true">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/brand/silvermedical-logo.jpg" alt="" />
          </span>
          <div>
            <strong>MESIL_Chat</strong>
            <small>AI 챌린지 심사위원 체험</small>
          </div>
        </header>

        <div className="reviewer-intro">
          <span className="eyebrow">약 3분 체험</span>
          <h1 id="reviewer-title">현장 대화가 돌봄 기록으로 이어집니다</h1>
          <p>
            요양보호사의 글·음성·손글씨 보고를 모아, 사회복지사가 어르신의
            변화와 확인할 일을 한눈에 살펴보는 흐름을 체험해 보세요.
          </p>
        </div>

        <aside className="reviewer-safety-note" aria-label="체험 자료 안내">
          <strong>모든 직원과 어르신은 가명입니다.</strong>
          <span>실제 개인정보는 포함되어 있지 않습니다.</span>
        </aside>

        <section className="reviewer-role-section" aria-labelledby="role-title">
          <div className="reviewer-section-heading">
            <h2 id="role-title">어떤 화면을 볼까요?</h2>
            <p>버튼을 누르면 별도 아이디 입력 없이 체험 화면이 열립니다.</p>
          </div>
          <div className="reviewer-role-actions">
            <button
              type="button"
              className="reviewer-role-button care"
              disabled={pending !== null}
              onClick={() => void startExperience("care")}
            >
              <span className="reviewer-role-icon" aria-hidden="true">
                현
              </span>
              <span>
                <strong>요양보호사로 체험하기</strong>
                <small>현장보고 · 첨부파일 · 읽음 · 답글 확인</small>
              </span>
              <b aria-hidden="true">›</b>
            </button>
            <button
              type="button"
              className="reviewer-role-button social-worker"
              disabled={pending !== null}
              onClick={() => void startExperience("social_worker")}
            >
              <span className="reviewer-role-icon" aria-hidden="true">
                AI
              </span>
              <span>
                <strong>사회복지사로 체험하기</strong>
                <small>AI 돌봄 브리핑 · 근거 원문 · 기록 활용</small>
              </span>
              <b aria-hidden="true">›</b>
            </button>
          </div>
          {pending ? (
            <p className="reviewer-progress" role="status">
              {experienceLabels[pending]} 체험 화면을 준비하고 있습니다…
            </p>
          ) : null}
          {error ? (
            <p className="form-error reviewer-error" role="alert">
              {error}
            </p>
          ) : null}
        </section>

        <section className="reviewer-guide" aria-labelledby="guide-title">
          <div className="reviewer-section-heading">
            <h2 id="guide-title">권장 체험 순서</h2>
          </div>
          <ol>
            <li>
              <span>1</span>
              <p>
                <strong>현장보고 확인</strong>
                <small>글·음성·손글씨 보고와 읽음·답글을 봅니다.</small>
              </p>
            </li>
            <li>
              <span>2</span>
              <p>
                <strong>AI 돌봄 브리핑 확인</strong>
                <small>먼저 볼 어르신과 이미 한 일·남은 일을 봅니다.</small>
              </p>
            </li>
            <li>
              <span>3</span>
              <p>
                <strong>원문과 기록 활용 확인</strong>
                <small>근거 대화를 펼쳐 보고 기록별 활용 결과를 확인합니다.</small>
              </p>
            </li>
          </ol>
        </section>

        <details className="reviewer-secondary">
          <summary>실시간 채팅 추가 체험</summary>
          <div>
            <p>
              다른 기기에서 두 번째 요양보호사 화면을 열어 실시간 메시지 전달을
              확인할 때만 사용하세요.
            </p>
            <button
              type="button"
              className="button button-secondary"
              disabled={pending !== null}
              onClick={() => void startExperience("realtime_secondary")}
            >
              실시간 추가 체험 열기
            </button>
          </div>
        </details>

        <p className="reviewer-shared-warning">
          심사 체험 중 작성한 내용은 다른 심사위원에게도 보일 수 있습니다. 실제
          개인정보는 입력하지 마세요.
        </p>

        <button
          type="button"
          className="reviewer-staff-link"
          onClick={() => void returnToStaffLogin()}
        >
          직원 로그인으로 돌아가기
        </button>
      </section>
    </main>
  );
}
