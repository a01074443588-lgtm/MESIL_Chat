"use client";

export type StaffContactTarget = {
  id: string;
  name: string;
};

export function StaffContactSheet({
  target,
  busy,
  error,
  callAvailable,
  onClose,
  onChat,
  onCall,
}: {
  target: StaffContactTarget;
  busy: boolean;
  error: string;
  callAvailable: boolean;
  onClose: () => void;
  onChat: () => void;
  onCall: () => void;
}) {
  return (
    <div
      className="quick-action-layer"
      role="dialog"
      aria-modal="true"
      aria-label={`${target.name} 직원 메뉴`}
    >
      <button
        type="button"
        className="quick-action-backdrop"
        onClick={onClose}
        aria-label="직원 메뉴 닫기"
      />
      <section className="quick-action-sheet staff-contact-sheet">
        <header>
          <span className="avatar large" aria-hidden="true">
            {target.name.slice(0, 1)}
          </span>
          <strong>{target.name}</strong>
          <button type="button" className="icon-button" onClick={onClose} aria-label="닫기">
            ×
          </button>
        </header>
        {error ? <p className="form-error" role="alert">{error}</p> : null}
        <div className="quick-action-buttons">
          <button type="button" disabled={busy} onClick={onChat}>
            <span aria-hidden="true">…</span>
            <strong>1:1채팅</strong>
          </button>
          {callAvailable ? (
            <button type="button" disabled={busy} onClick={onCall}>
              <span aria-hidden="true">☎</span>
              <strong>통화</strong>
            </button>
          ) : null}
        </div>
      </section>
    </div>
  );
}
