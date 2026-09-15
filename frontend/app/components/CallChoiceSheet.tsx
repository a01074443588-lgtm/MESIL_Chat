"use client";

export function CallChoiceSheet({
  title,
  busy = false,
  loading = false,
  error = "",
  members,
  selectedMemberIds = [],
  maxAudioParticipants = 0,
  maxVideoParticipants = 0,
  onSelectedMemberIdsChange,
  onClose,
  onAudio,
  onVideo,
}: {
  title: string;
  busy?: boolean;
  loading?: boolean;
  error?: string;
  members?: Array<{ id: string; full_name: string; job_name: string | null }>;
  selectedMemberIds?: string[];
  maxAudioParticipants?: number;
  maxVideoParticipants?: number;
  onSelectedMemberIdsChange?: (ids: string[]) => void;
  onClose: () => void;
  onAudio: () => void;
  onVideo: () => void;
}) {
  const memberSelectionVisible = Array.isArray(members);
  const selectedCount = selectedMemberIds.length;
  const participantCount = selectedCount + 1;
  const allSelected = Boolean(members?.length) && selectedCount === members?.length;
  const noRecipient = memberSelectionVisible && selectedCount === 0;
  const audioTooLarge = Boolean(
    memberSelectionVisible &&
      maxAudioParticipants > 0 &&
      participantCount > maxAudioParticipants,
  );
  const videoTooLarge = Boolean(
    memberSelectionVisible &&
      maxVideoParticipants > 0 &&
      participantCount > maxVideoParticipants,
  );

  function toggleAll(checked: boolean) {
    onSelectedMemberIdsChange?.(checked ? (members ?? []).map((member) => member.id) : []);
  }

  function toggleMember(memberId: string, checked: boolean) {
    onSelectedMemberIdsChange?.(
      checked
        ? [...selectedMemberIds, memberId]
        : selectedMemberIds.filter((id) => id !== memberId),
    );
  }

  return (
    <div
      className="quick-action-layer"
      role="dialog"
      aria-modal="true"
      aria-label={`${title} 방식 선택`}
    >
      <button
        type="button"
        className="quick-action-backdrop"
        onClick={onClose}
        aria-label="통화 선택 닫기"
      />
      <section className="quick-action-sheet">
        <header>
          <strong>{title}</strong>
          <button type="button" className="icon-button" onClick={onClose} aria-label="닫기">
            ×
          </button>
        </header>
        {error ? <p className="form-error" role="alert">{error}</p> : null}
        {memberSelectionVisible ? (
          <fieldset className="call-member-picker" disabled={busy || loading}>
            <legend>통화할 직원 선택</legend>
            {loading ? (
              <p aria-live="polite">대화방 직원을 확인하고 있습니다.</p>
            ) : members?.length ? (
              <>
                <label className="call-member-all">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    onChange={(event) => toggleAll(event.target.checked)}
                  />
                  <strong>모두 선택</strong>
                </label>
                <div className="call-member-list">
                  {members.map((member) => (
                    <label key={member.id}>
                      <input
                        type="checkbox"
                        checked={selectedMemberIds.includes(member.id)}
                        onChange={(event) =>
                          toggleMember(member.id, event.target.checked)
                        }
                      />
                      <span>
                        <strong>{member.full_name}</strong>
                        {member.job_name ? <small>{member.job_name}</small> : null}
                      </span>
                    </label>
                  ))}
                </div>
                <p className="call-member-count" aria-live="polite">
                  발신자를 포함해 {participantCount}명
                </p>
              </>
            ) : (
              <p>통화할 다른 직원이 없습니다.</p>
            )}
          </fieldset>
        ) : null}
        {audioTooLarge || videoTooLarge ? (
          <p className="field-help call-limit-help">
            {audioTooLarge
              ? `음성통화는 ${maxAudioParticipants}명까지 가능합니다. `
              : ""}
            {videoTooLarge
              ? `영상통화는 ${maxVideoParticipants}명까지 가능합니다. `
              : ""}
            통화할 직원을 줄여 주세요.
          </p>
        ) : null}
        <div className="quick-action-buttons">
          <button
            type="button"
            disabled={busy || loading || noRecipient || audioTooLarge}
            onClick={onAudio}
          >
            <span aria-hidden="true">☎</span>
            <strong>
              {memberSelectionVisible ? `${participantCount}명 음성통화` : "음성통화"}
            </strong>
          </button>
          <button
            type="button"
            disabled={busy || loading || noRecipient || videoTooLarge}
            onClick={onVideo}
          >
            <span aria-hidden="true">▣</span>
            <strong>
              {memberSelectionVisible ? `${participantCount}명 영상통화` : "영상통화"}
            </strong>
          </button>
        </div>
        <button type="button" className="quick-action-cancel" onClick={onClose}>
          취소
        </button>
      </section>
    </div>
  );
}
