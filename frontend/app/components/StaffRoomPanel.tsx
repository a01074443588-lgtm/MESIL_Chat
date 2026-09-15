"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { apiFetch } from "../api";
import type {
  ActiveStaffDirectoryEntry,
  StaffRoom,
  StaffRoomMember,
} from "../types";

type StaffRoomStartAction = "chat" | "audio" | "video";

function staffDescription(staff: ActiveStaffDirectoryEntry | StaffRoomMember) {
  return [
    staff.business_name,
    staff.department_name,
    staff.floor_name,
    staff.team_name,
    staff.job_name,
    staff.position_title,
  ]
    .filter(Boolean)
    .join(" · ");
}

export function StaffRoomPanel({
  open,
  initialRoomId,
  currentUserId,
  onClose,
  onRoomsChanged,
  onOpenRoom,
}: {
  open: boolean;
  initialRoomId: string | null;
  currentUserId: string;
  onClose: () => void;
  onRoomsChanged: () => Promise<unknown>;
  onOpenRoom: (roomId: string, action: StaffRoomStartAction) => Promise<void>;
}) {
  const [directory, setDirectory] = useState<ActiveStaffDirectoryEntry[]>([]);
  const [staffRooms, setStaffRooms] = useState<StaffRoom[]>([]);
  const [selectedRoomId, setSelectedRoomId] = useState<string | null>(null);
  const [creating, setCreating] = useState(true);
  const [roomName, setRoomName] = useState("");
  const [query, setQuery] = useState("");
  const [selectedUserIds, setSelectedUserIds] = useState<string[]>([]);
  const [transferStaffId, setTransferStaffId] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [callChoiceOpen, setCallChoiceOpen] = useState(false);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");

  const load = useCallback(async (
    preferredRoomId: string | null = initialRoomId,
    resetView = true,
  ) => {
    setLoading(true);
    setError("");
    try {
      const [nextDirectory, nextRooms] = await Promise.all([
        apiFetch<ActiveStaffDirectoryEntry[]>("/api/staff-directory/active"),
        apiFetch<StaffRoom[]>("/api/staff-rooms"),
      ]);
      setDirectory(nextDirectory);
      setStaffRooms(nextRooms);
      const requestedRoom = preferredRoomId
        ? nextRooms.find((room) => room.id === preferredRoomId)
        : null;
      if (requestedRoom) {
        setSelectedRoomId(requestedRoom.id);
        setCreating(false);
        setRoomName(requestedRoom.name);
      } else if (resetView) {
        setSelectedRoomId(null);
        setCreating(true);
        setRoomName("");
      }
      setSelectedUserIds([]);
      setCallChoiceOpen(false);
      setTransferStaffId("");
      setQuery("");
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "직원 대화방 정보를 불러오지 못했습니다.",
      );
    } finally {
      setLoading(false);
    }
  }, [initialRoomId]);

  useEffect(() => {
    if (!open) return;
    const timer = window.setTimeout(() => void load(initialRoomId, true), 0);
    return () => window.clearTimeout(timer);
  }, [initialRoomId, load, open]);

  const selectedRoom = useMemo(
    () => staffRooms.find((room) => room.id === selectedRoomId) ?? null,
    [selectedRoomId, staffRooms],
  );
  const normalizedQuery = query.trim().toLocaleLowerCase("ko-KR");
  const visibleDirectory = useMemo(() => {
    const existing = new Set(selectedRoom?.member_ids ?? []);
    return directory.filter((staff) => {
      if (staff.id === currentUserId || existing.has(staff.id)) return false;
      if (!normalizedQuery) return true;
      return `${staff.full_name} ${staffDescription(staff)}`
        .toLocaleLowerCase("ko-KR")
        .includes(normalizedQuery);
    });
  }, [currentUserId, directory, normalizedQuery, selectedRoom?.member_ids]);

  function toggleSelected(userId: string) {
    setSelectedUserIds((current) =>
      current.includes(userId)
        ? current.filter((id) => id !== userId)
        : [...current, userId],
    );
  }

  async function run(action: () => Promise<unknown>, success: string) {
    if (saving) return;
    setSaving(true);
    setError("");
    setFeedback("");
    try {
      const result = await action();
      const returnedRoomId =
        result && typeof result === "object" && "id" in result
          ? String((result as { id: unknown }).id)
          : selectedRoomId;
      await Promise.all([load(returnedRoomId, false), onRoomsChanged()]);
      setFeedback(success);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "요청을 처리하지 못했습니다.");
    } finally {
      setSaving(false);
    }
  }

  async function startSelected(action: StaffRoomStartAction) {
    if (selectedUserIds.length === 0) {
      setError("함께 대화할 직원을 한 명 이상 선택해 주세요.");
      return;
    }
    if (saving) return;
    setSaving(true);
    setError("");
    setFeedback("");
    try {
      const expectedMemberIds = new Set([currentUserId, ...selectedUserIds]);
      let room = staffRooms.find(
        (candidate) =>
          candidate.member_ids.length === expectedMemberIds.size &&
          candidate.member_ids.every((memberId) => expectedMemberIds.has(memberId)),
      );
      if (!room) {
        room = await apiFetch<StaffRoom>("/api/staff-rooms", {
          method: "POST",
          body: JSON.stringify({
            name: roomName.trim() || null,
            member_ids: selectedUserIds,
          }),
        });
      }
      await onRoomsChanged();
      await onOpenRoom(room.id, action);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "대화방을 열지 못했습니다.");
    } finally {
      setSaving(false);
    }
  }

  function createRoom(event: FormEvent) {
    event.preventDefault();
    void startSelected("chat");
  }

  function selectRoom(room: StaffRoom) {
    setSelectedRoomId(room.id);
    setCreating(false);
    setRoomName(room.name);
    setSelectedUserIds([]);
    setCallChoiceOpen(false);
    setTransferStaffId("");
    setFeedback("");
    setError("");
  }

  if (!open) return null;

  return (
    <div className="staff-room-layer" role="dialog" aria-modal="true" aria-label="직원 대화·통화">
      <button className="staff-room-backdrop" onClick={onClose} aria-label="직원 대화방 닫기" />
      <section className="staff-room-panel">
        <header>
          <div>
            <h2>{creating ? "직원 대화·통화" : "대화방 관리"}</h2>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="닫기">×</button>
        </header>

        <div className="staff-room-tabs" role="tablist" aria-label="직원 대화·통화 메뉴">
          <button
            className={creating ? "active" : ""}
            onClick={() => {
              setCreating(true);
              setSelectedRoomId(null);
              setRoomName("");
              setSelectedUserIds([]);
              setCallChoiceOpen(false);
              setError("");
            }}
          >
            직원 선택
          </button>
          <button
            className={!creating ? "active" : ""}
            disabled={staffRooms.length === 0}
            onClick={() => {
              const room = selectedRoom ?? staffRooms[0];
              if (room) selectRoom(room);
            }}
          >
            참여 중인 방 {staffRooms.length}
          </button>
        </div>

        {loading ? <p className="staff-room-status">직원 목록을 불러오는 중입니다…</p> : null}
        {error ? <p className="form-error" role="alert">{error}</p> : null}
        {feedback ? <p className="form-success" role="status">{feedback}</p> : null}

        {!loading && creating ? (
          <form className="staff-room-create" onSubmit={createRoom}>
            <label>
              <span>방 이름 (선택)</span>
              <input
                value={roomName}
                maxLength={120}
                onChange={(event) => setRoomName(event.target.value)}
                placeholder="입력하지 않아도 됩니다"
              />
            </label>
            <div className="staff-room-selection-heading">
              <strong>함께할 직원</strong>
              <span>{selectedUserIds.length}명 선택</span>
            </div>
            <input
              className="staff-room-search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="직원 이름 또는 직종 검색"
              aria-label="직원 검색"
            />
            <div className="staff-room-directory">
              {visibleDirectory.map((staff) => (
                <label key={staff.id} className={selectedUserIds.includes(staff.id) ? "selected" : ""}>
                  <input
                    type="checkbox"
                    checked={selectedUserIds.includes(staff.id)}
                    onChange={() => toggleSelected(staff.id)}
                  />
                  <span className="avatar">{staff.full_name.slice(0, 1)}</span>
                  <span>
                    <strong>{staff.full_name}</strong>
                    {staffDescription(staff) ? (
                      <small>{staffDescription(staff)}</small>
                    ) : null}
                  </span>
                </label>
              ))}
            </div>
            {!callChoiceOpen ? (
              <div className="staff-room-start-actions">
                <button
                  type="submit"
                  className="button button-primary"
                  disabled={saving || selectedUserIds.length === 0}
                >
                  {saving
                    ? "여는 중…"
                    : selectedUserIds.length === 0
                      ? "직원을 먼저 선택하세요"
                      : "대화"}
                </button>
                <button
                  type="button"
                  className="button button-primary"
                  disabled={saving || selectedUserIds.length === 0}
                  onClick={() => setCallChoiceOpen(true)}
                >
                  통화
                </button>
              </div>
            ) : (
              <div className="staff-room-call-choice">
                <strong>통화 방식을 선택하세요.</strong>
                <div>
                  <button
                    type="button"
                    className="button button-primary"
                    disabled={saving}
                    onClick={() => void startSelected("audio")}
                  >
                    음성통화
                  </button>
                  <button
                    type="button"
                    className="button button-primary"
                    disabled={saving}
                    onClick={() => void startSelected("video")}
                  >
                    영상통화
                  </button>
                </div>
                <button
                  type="button"
                  className="button button-secondary"
                  disabled={saving}
                  onClick={() => setCallChoiceOpen(false)}
                >
                  취소
                </button>
              </div>
            )}
          </form>
        ) : null}

        {!loading && !creating ? (
          <div className="staff-room-manage">
            <div className="staff-room-owned-list">
              {staffRooms.map((room) => (
                <button
                  key={room.id}
                  className={room.id === selectedRoomId ? "selected" : ""}
                  onClick={() => selectRoom(room)}
                >
                  <strong>{room.name}</strong>
                  <small>{room.members.length}명 · 방장 {room.owner_name}</small>
                </button>
              ))}
            </div>
            {selectedRoom ? (
              <section className="staff-room-detail">
                <div className="staff-room-detail-title">
                  <div>
                    <h3>{selectedRoom.name}</h3>
                    <p>{selectedRoom.members.length}명 참여 · {selectedRoom.is_owner ? "내가 방장" : `방장 ${selectedRoom.owner_name}`}</p>
                  </div>
                </div>

                {selectedRoom.can_manage ? (
                  <form
                    className="staff-room-rename"
                    onSubmit={(event) => {
                      event.preventDefault();
                      if (!roomName.trim() || roomName.trim() === selectedRoom.name) return;
                      void run(
                        () => apiFetch(`/api/staff-rooms/${selectedRoom.id}`, {
                          method: "PATCH",
                          body: JSON.stringify({ name: roomName.trim() }),
                        }),
                        "방 이름을 바꿨습니다.",
                      );
                    }}
                  >
                    <input value={roomName} onChange={(event) => setRoomName(event.target.value)} maxLength={120} />
                    <button className="button button-secondary" disabled={saving || roomName.trim() === selectedRoom.name}>이름 저장</button>
                  </form>
                ) : null}

                <div className="staff-room-member-list">
                  {selectedRoom.members.map((member) => (
                    <article key={member.staff_id}>
                      <span className="avatar">{member.full_name.slice(0, 1)}</span>
                      <span>
                        <strong>{member.full_name}</strong>
                        {staffDescription(member) ? (
                          <small>{staffDescription(member)}</small>
                        ) : null}
                      </span>
                      {member.staff_id === selectedRoom.owner_staff_id ? <b>방장</b> : null}
                      {selectedRoom.can_manage && member.staff_id !== selectedRoom.owner_staff_id ? (
                        <button
                          type="button"
                          disabled={saving}
                          onClick={() => {
                            if (!window.confirm(`${member.full_name} 직원을 이 방에서 제외할까요?`)) return;
                            void run(
                              () => apiFetch(`/api/staff-rooms/${selectedRoom.id}/members/${member.staff_id}`, { method: "DELETE" }),
                              "참여자를 변경했습니다.",
                            );
                          }}
                        >제외</button>
                      ) : null}
                    </article>
                  ))}
                </div>

                {selectedRoom.can_invite ? (
                  <div className="staff-room-invite">
                    <div className="staff-room-selection-heading">
                      <strong>직원 초대</strong>
                      <span>{selectedUserIds.length}명 선택</span>
                    </div>
                    <input
                      className="staff-room-search"
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                      placeholder="초대할 직원 검색"
                    />
                    <div className="staff-room-directory compact">
                      {visibleDirectory.map((staff) => (
                        <label key={staff.id} className={selectedUserIds.includes(staff.id) ? "selected" : ""}>
                          <input type="checkbox" checked={selectedUserIds.includes(staff.id)} onChange={() => toggleSelected(staff.id)} />
                          <span>
                            <strong>{staff.full_name}</strong>
                            {staffDescription(staff) ? (
                              <small>{staffDescription(staff)}</small>
                            ) : null}
                          </span>
                        </label>
                      ))}
                    </div>
                    <button
                      className="button button-secondary"
                      disabled={saving || selectedUserIds.length === 0}
                      onClick={() => void run(
                        () => apiFetch(`/api/staff-rooms/${selectedRoom.id}/members`, {
                          method: "POST",
                          body: JSON.stringify({ member_ids: selectedUserIds }),
                        }),
                        "선택한 직원을 초대했습니다.",
                      )}
                    >선택한 직원 초대</button>
                  </div>
                ) : null}

                {selectedRoom.is_owner && selectedRoom.members.length > 1 ? (
                  <div className="staff-room-transfer">
                    <label>
                      <span>방장 넘기기</span>
                      <select value={transferStaffId} onChange={(event) => setTransferStaffId(event.target.value)}>
                        <option value="">새 방장 선택</option>
                        {selectedRoom.members
                          .filter((member) => member.staff_id !== selectedRoom.owner_staff_id)
                          .map((member) => <option key={member.staff_id} value={member.staff_id}>{member.full_name}</option>)}
                      </select>
                    </label>
                    <button
                      className="button button-secondary"
                      disabled={saving || !transferStaffId}
                      onClick={() => void run(
                        () => apiFetch(`/api/staff-rooms/${selectedRoom.id}/transfer-owner`, {
                          method: "POST",
                          body: JSON.stringify({ new_owner_staff_id: transferStaffId }),
                        }),
                        "새 방장에게 관리 권한을 넘겼습니다.",
                      )}
                    >방장 변경</button>
                  </div>
                ) : null}

                <div className="staff-room-exit-actions">
                  {selectedRoom.is_owner ? (
                    <button
                      className="button button-danger"
                      disabled={saving}
                      onClick={() => {
                        if (!window.confirm("이 대화방을 종료할까요? 기존 업무 기록은 보관됩니다.")) return;
                        void run(
                          () => apiFetch(`/api/staff-rooms/${selectedRoom.id}/close`, { method: "POST", body: "{}" }),
                          "대화방을 종료했습니다.",
                        );
                      }}
                    >대화방 종료</button>
                  ) : (
                    <button
                      className="button button-secondary"
                      disabled={saving}
                      onClick={() => {
                        if (!window.confirm("이 대화방에서 나갈까요? 기존 업무 기록은 보관됩니다.")) return;
                        void run(
                          () => apiFetch(`/api/staff-rooms/${selectedRoom.id}/leave`, { method: "POST", body: "{}" }),
                          "대화방에서 나왔습니다.",
                        );
                      }}
                    >대화방 나가기</button>
                  )}
                </div>
              </section>
            ) : <p className="staff-room-status">관리할 대화방을 선택해 주세요.</p>}
          </div>
        ) : null}
      </section>
    </div>
  );
}
