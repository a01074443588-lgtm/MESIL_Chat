export function handwritingEvidenceScopeKey({ messageId, imageId, audioId }) {
  if (!messageId || !imageId || !audioId) return "";
  return JSON.stringify([messageId, imageId, audioId]);
}

export function createHandwritingDraftState() {
  return { activeScopeKey: "", draft: null };
}

export function activateHandwritingEvidence(state, scopeKey) {
  if (!scopeKey || state.activeScopeKey === scopeKey) return state;
  return { activeScopeKey: scopeKey, draft: null };
}

export function recordHandwritingStaffDraft(state, scopeKey, text) {
  if (!scopeKey) return state;
  return {
    activeScopeKey: scopeKey,
    draft: { scopeKey, text, dirty: true },
  };
}

export function resolveHandwritingWorkspaceDraft(state, scopeKey, proposedText) {
  if (scopeKey && state.draft?.dirty && state.draft.scopeKey === scopeKey) {
    return { text: state.draft.text, dirty: true };
  }
  return { text: proposedText, dirty: false };
}

export function clearHandwritingStaffDraft(state) {
  return { activeScopeKey: state.activeScopeKey, draft: null };
}
