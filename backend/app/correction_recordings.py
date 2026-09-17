"""Persisted correction-recording ownership and reversible removal boundary."""
from sqlalchemy import select

from .models import (
    AuditEvent, AttachmentTextRevision, HandwritingCorrectionApproval,
    OcrCorrectionEvent, OcrQualityRegistryEntry, WorkItem,
)

REMOVED_RECORDING = "removed_handwriting_recording"


def removal_state(db, attachment, viewer_id):
    state = {"can_remove": False, "image_attachment_id": None, "reason": "원본 또는 승인 근거는 제거할 수 없습니다."}
    if db is None or not attachment.mime_type.startswith("audio/") or attachment.uploader_id != viewer_id:
        return state
    marker = db.scalar(select(AuditEvent).where(
        AuditEvent.action == "handwriting_correction.recording_requested",
        AuditEvent.target_type == "attachment", AuditEvent.target_id == attachment.id,
        AuditEvent.actor_id == viewer_id,
    ).limit(1))
    if marker is None or attachment.entity_type == REMOVED_RECORDING:
        return state
    state["image_attachment_id"] = (marker.details or {}).get("image_attachment_id")
    extraction = attachment.text_extraction
    if extraction is not None and (extraction.status in {"pending", "processing", "reviewed"} or extraction.reviewed_text is not None):
        return {**state, "reason": "처리 중이거나 직원 확인에 사용된 음성은 제거할 수 없습니다."}
    references = [
        select(HandwritingCorrectionApproval.id).where(HandwritingCorrectionApproval.audio_attachment_id == attachment.id),
        select(AttachmentTextRevision.id).where(AttachmentTextRevision.attachment_id == attachment.id, AttachmentTextRevision.kind == "staff_review"),
        select(OcrCorrectionEvent.id).where(OcrCorrectionEvent.attachment_id == attachment.id),
        select(OcrQualityRegistryEntry.id).where(OcrQualityRegistryEntry.attachment_id == attachment.id),
        select(WorkItem.id).where(WorkItem.source_message_id == attachment.message_id, WorkItem.confirmed_at.is_not(None)),
    ]
    if any(db.scalar(query.limit(1)) is not None for query in references):
        return state
    return {**state, "can_remove": True, "reason": "본인이 추가한 미승인 교정용 음성입니다."}
