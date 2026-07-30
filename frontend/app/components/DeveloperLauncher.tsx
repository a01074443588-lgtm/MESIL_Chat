"use client";

import { useEffect, useMemo, useState } from "react";
import { apiFetch } from "../api";
import type { User } from "../types";

type LoginResponse = {
  user: User;
  expires_at: string;
};

export function DeveloperLauncher({
  controller,
  onLogout,
}: {
  controller: User;
  onLogout: () => void;
}) {
  const [users, setUsers] = useState<User[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [switchingId, setSwitchingId] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    apiFetch<User[]>("/api/dev/users")
      .then(setUsers)
      .catch((reason) =>
        setError(reason instanceof Error ? reason.message : "사용자 목록을 불러오지 못했습니다."),
      )
      .finally(() => setLoading(false));
  }, []);

  const filteredUsers = useMemo(() => {
    const keyword = query.trim().toLocaleLowerCase("ko-KR");
    if (!keyword) return users;
    return users.filter((user) =>
      [
        user.full_name,
        user.username,
        user.job_name,
        user.position_title,
        user.floor?.name,
        user.team?.name,
      ]
        .filter(Boolean)
        .some((value) => value!.toLocaleLowerCase("ko-KR").includes(keyword)),
    );
  }, [query, users]);

  async function switchUser(user: User) {
    setSwitchingId(user.id);
    setError("");
    try {
      await apiFetch<LoginResponse>(`/api/dev/switch/${user.id}`, {
        method: "POST",
        body: "{}",
      });
      window.location.reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "사용자 화면을 열지 못했습니다.");
      setSwitchingId(null);
    }
  }

  return (
    <main className="dev-launcher-page">
      <section className="dev-launcher-card">
        <header className="dev-launcher-header">
          <div>
            <span className="eyebrow">개발환경 전용</span>
            <h1>사용자 화면 빠른 전환</h1>
            <p>직원 비밀번호를 입력하지 않고 가상 사용자 화면을 확인합니다.</p>
          </div>
          <button className="button button-secondary" onClick={onLogout}>
            런처 종료
          </button>
        </header>

        <div className="dev-warning" role="note">
          <strong>제출용 기능이 아닙니다.</strong>
          <span>
            모든 전환은 기록되며, 퇴사·휴직 계정은 열 수 없습니다. 제출 전 반드시
            비활성화해야 합니다.
          </span>
        </div>

        <label className="dev-user-search">
          사용자 찾기
          <input
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="이름, 아이디, 직종, 층, 팀"
          />
        </label>

        <div className="dev-launcher-summary">
          <strong>{controller.full_name}</strong>
          <span>
            전환 가능한 계정{" "}
            {users.filter((user) => user.employment_status === "active").length}명
          </span>
        </div>

        {error ? (
          <p className="form-error" role="alert">
            {error}
          </p>
        ) : null}

        {loading ? (
          <p className="dev-launcher-empty">사용자 목록을 불러오고 있습니다…</p>
        ) : (
          <div className="dev-user-list">
            {filteredUsers.map((user) => {
              const available = user.employment_status === "active";
              return (
                <article className="dev-user-row" key={user.id}>
                  <span className="avatar">{user.full_name.slice(0, 1)}</span>
                  <div>
                    <strong>{user.full_name}</strong>
                    <small>
                      {[
                        user.username,
                        user.floor?.name,
                        user.team?.name,
                        user.job_name,
                        user.position_title,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </small>
                  </div>
                  <span className={`dev-user-status ${available ? "active" : "blocked"}`}>
                    {available
                      ? "재직"
                      : user.employment_status === "leave"
                        ? "휴직"
                        : "퇴사"}
                  </span>
                  <button
                    className="button button-primary"
                    disabled={!available || switchingId !== null}
                    onClick={() => void switchUser(user)}
                  >
                    {switchingId === user.id ? "여는 중…" : "이 화면 열기"}
                  </button>
                </article>
              );
            })}
            {filteredUsers.length === 0 ? (
              <p className="dev-launcher-empty">조건에 맞는 사용자가 없습니다.</p>
            ) : null}
          </div>
        )}
      </section>
    </main>
  );
}
