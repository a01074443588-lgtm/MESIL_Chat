"""Local reading jobs and an atomic, versioned document/audio review boundary."""
from hashlib import sha256
from pathlib import Path
from threading import BoundedSemaphore
from time import monotonic

from fastapi import HTTPException
from sqlalchemy import func, select

from .database import SessionLocal
from .models import AttachmentTextExtraction, AttachmentTextRevision, Message, MessageAttachment, MessageResidentLink, WorkItem, utcnow
from .document_reading import DOCUMENT_MIME_TYPES, ERROR_MESSAGES, DocumentReadError, DocumentReadResult, document_extension, read_document, read_scanned_page

_document_lane = BoundedSemaphore(1)


def current_revision(db, extraction):
    return int(db.scalar(select(func.max(AttachmentTextRevision.revision)).where(
        AttachmentTextRevision.extraction_id == extraction.id)) or 0)


def append_revision(db, attachment, extraction, *, kind, text, reviewer=None, resident_ids=()):
    revision = current_revision(db, extraction) + 1
    db.add(AttachmentTextRevision(
        organization_id=attachment.organization_id, attachment_id=attachment.id,
        extraction_id=extraction.id, revision=revision, kind=kind, text_content=text,
        text_sha256=sha256(text.encode()).hexdigest(), original_file_sha256=attachment.sha256,
        provider=extraction.provider, model_name=extraction.model_name,
        source_metadata={"details": extraction.suggestion_details or [],
            "started_at": extraction.started_at.isoformat() if extraction.started_at else None,
            "completed_at": extraction.completed_at.isoformat() if extraction.completed_at else None},
        reviewed_by_id=reviewer.id if reviewer else None,
        selected_resident_ids=[str(value) for value in resident_ids]))
    extraction.suggestion_details = [
        *(row for row in extraction.suggestion_details or [] if row.get("kind") != "staff_review_revision"),
        {"kind": "staff_review_revision", "revision": revision}]
    db.flush()
    return revision


def run_document_job(attachment_id):
    from . import main as m
    with SessionLocal() as db:
        attachment = db.get(MessageAttachment, attachment_id)
        if attachment is None or attachment.mime_type not in DOCUMENT_MIME_TYPES:
            return False
    with _document_lane, SessionLocal() as db:
        attachment = db.get(MessageAttachment, attachment_id)
        extraction = db.scalar(select(AttachmentTextExtraction).where(
            AttachmentTextExtraction.attachment_id == attachment_id).with_for_update())
        if extraction is None or extraction.status != "pending":
            return True
        extraction.status = "processing"
        extraction.started_at = utcnow()
        started_at = extraction.started_at
        db.commit()
        started = monotonic()
        detail = {"kind": "document_reading", "ocr_page_count": 0,
            "external_transfer": False, "locations": []}
        try:
            upload_dir = Path(m.settings.upload_dir).resolve()
            target = (upload_dir / attachment.storage_key).resolve()
            if target.parent != upload_dir or not target.is_file():
                raise DocumentReadError("invalid_document")
            def ocr_page(path):
                # This resolver enforces the existing private endpoint/central image role.
                runtime_detail = {"kind": "image_ocr_runtime", "model_call_count": 0}
                with m.attachment_image_runtime(runtime_detail) as runtime:
                    detail.update(ocr_provider=runtime.provider, ocr_model=runtime.model)
                    value = read_scanned_page(path)
                if m.is_pathological_handwriting_output(value):
                    raise DocumentReadError("page_read_failed")
                from .ocr import NO_TEXT_RESPONSES
                return "" if value.strip() in NO_TEXT_RESPONSES else value
            if m.settings.environment == "test":
                result = read_document(target, document_extension(attachment), ocr_page=ocr_page)
            else:
                import json, subprocess, sys
                try:
                    process = subprocess.run([sys.executable,"-m","app.document_reader_worker"],
                        input=json.dumps({"path":str(target),"extension":document_extension(attachment)}),
                        capture_output=True,text=True,encoding="utf-8",timeout=180)
                except subprocess.TimeoutExpired:
                    raise DocumentReadError("document_timeout") from None
                if process.returncode or len(process.stdout) > 1_500_000:
                    raise DocumentReadError("document_limit")
                value=json.loads(process.stdout)
                if value.get("error_type"):
                    raise DocumentReadError(value["error_type"])
                result=DocumentReadResult(value["text"],value["locations"],value["ocr_page_count"])
                detail.update(value.get("runtime",{}))
            detail.update(locations=result.locations, ocr_page_count=result.ocr_page_count)
            final_status = "completed" if result.text.strip() else "no_text"
            text, error_code = result.text, None
        except DocumentReadError as exc:
            text, error_code = "", exc.code
            final_status = "unsupported" if error_code == "unsupported_document" else "no_text" if error_code == "empty_document" else "failed"
        except Exception:
            text, error_code, final_status = "", "page_read_failed", "failed"
        db.refresh(extraction, with_for_update=True)
        if extraction.status != "processing" or extraction.started_at != started_at:
            return True
        detail.update(elapsed_ms=round((monotonic()-started)*1000), error_type=error_code, result_char_count=len(text))
        extraction.status = final_status
        extraction.extracted_text = text or None
        if extraction.original_extracted_text is None and text:
            extraction.original_extracted_text = text
        extraction.suggestion_details = [detail]
        extraction.error_message = ERROR_MESSAGES.get(error_code) if error_code else None
        extraction.completed_at = utcnow()
        if text:
            append_revision(db, attachment, extraction, kind="automatic", text=text)
        m.record_audit(db, actor_id=extraction.requested_by_id, action="document_reading.finished",
            target_type="attachment", target_id=attachment.id,
            details={k: detail[k] for k in ("elapsed_ms", "error_type", "result_char_count", "ocr_page_count")})
        db.commit()
    return True


def save_staff_review(db, attachment, payload, editor, background_tasks):
    from . import main as m
    from .resident_review_flow import apply_review
    from .photo_reading import resident_review_metadata
    extraction = db.scalar(select(AttachmentTextExtraction).where(
        AttachmentTextExtraction.attachment_id == attachment.id)
        .execution_options(populate_existing=True).with_for_update())
    if extraction is None or extraction.status not in {"completed", "reviewed"}:
        raise HTTPException(409, "내용 읽기가 완료된 뒤 원본과 비교해 주세요.")
    if payload.decision != "direct_edit" or not payload.reviewed_text:
        raise HTTPException(422, "원본과 비교해 확인한 내용을 입력해 주세요.")
    if payload.resident_ids is None:
        raise HTTPException(422, "관련 어르신을 선택해 주세요. 해당하는 분이 없으면 선택 없이 확인할 수 있습니다.")
    previous = current_revision(db, extraction)
    selected_ids = list(dict.fromkeys(payload.resident_ids))
    # A different editor can change links without creating a text revision.
    # Serialize with that editor and compare current links before declaring a
    # repeated text payload idempotent. Reload the ORM identity after the lock.
    message = db.scalar(select(Message).where(Message.id == attachment.message_id)
        .execution_options(populate_existing=True).with_for_update())
    if message is None or message.organization_id != editor.organization_id:
        raise HTTPException(404, "메시지를 찾을 수 없습니다.")
    m._require_active_message(message, "첨부 내용 확인")
    current_links = list(db.scalars(select(MessageResidentLink).where(
        MessageResidentLink.message_id == message.id, MessageResidentLink.status != "rejected")
        .execution_options(populate_existing=True).with_for_update()))
    selection_current = (
        {link.resident_id for link in current_links} == set(selected_ids)
        and all(link.status == "confirmed" and link.source == "manual" for link in current_links)
        and (message.resident_id in selected_ids if selected_ids else message.resident_id is None)
        and not resident_review_metadata(message).get("unrelated")
    )
    latest = db.scalar(select(AttachmentTextRevision).where(
        AttachmentTextRevision.extraction_id == extraction.id,
        AttachmentTextRevision.revision == previous))
    if (latest is not None and latest.kind == "staff_review" and extraction.status == "reviewed"
        and latest.text_content == payload.reviewed_text
        and set(latest.selected_resident_ids) == {str(x) for x in selected_ids}
        and selection_current):
        return m.attachment_response(attachment, db=db, viewer_id=editor.id)
    expected_selection = getattr(payload, "expected_resident_revision", None)
    if expected_selection is not None and expected_selection != int(resident_review_metadata(message).get("revision", 0) or 0):
        raise HTTPException(409, "다른 직원이 어르신 연결을 바꿨습니다. 확인창을 다시 열어 최신 선택을 확인해 주세요.")
    if payload.expected_revision is not None and previous != payload.expected_revision:
        raise HTTPException(409, "다른 직원이 내용을 확인했습니다. 최신 내용을 불러온 뒤 다시 확인해 주세요.")
    if previous == 0:
        # Preserve legacy first transcript before its first new staff revision.
        original = extraction.original_extracted_text or extraction.extracted_text or ""
        if original:
            append_revision(db, attachment, extraction, kind="automatic", text=original)
    if attachment.mime_type.startswith("audio/"):
        preserve_audio_correction_history(db, attachment, extraction, payload.reviewed_text, editor)
    extraction.reviewed_text = payload.reviewed_text
    extraction.reviewed_by_id = editor.id
    extraction.reviewed_at = utcnow()
    extraction.status = "reviewed"
    revision = append_revision(db, attachment, extraction, kind="staff_review",
        text=payload.reviewed_text, reviewer=editor, resident_ids=selected_ids)
    # One transaction: invalid resident scope rolls back both text and links.
    message = apply_review(
        db,
        editor,
        attachment.message_id,
        "set",
        resident_ids=selected_ids,
        commit=False,
        authorized_message=message,
    )
    m._refresh_work_item_residents(db, message)
    item = db.scalar(select(WorkItem).where(WorkItem.source_message_id == message.id))
    if item is not None:
        item.ai_state = "not_requested"
        item.ai_payload = item.ai_generator = item.ai_generated_at = None
    m._search_summary_cache.clear()
    m._adaptive_search_summary_cache.clear()
    m.record_audit(db, actor_id=editor.id, action="attachment_staff_review.confirmed",
        target_type="attachment", target_id=attachment.id,
        details={"revision": revision, "selected_count": len(selected_ids),
            "text_sha256": sha256(payload.reviewed_text.encode()).hexdigest(),
            "original_file_sha256": attachment.sha256,
            "source": "audio_transcript" if attachment.mime_type.startswith("audio/") else "document_text"})
    db.commit()
    db.refresh(attachment)
    background_tasks.add_task(m.manager.send_to_users, m.room_member_user_ids(db,message.room_id),
        {"event":"message_metadata_changed", "message_id":str(message.id), "room_id":str(message.room_id)})
    return m.attachment_response(attachment, db=db, viewer_id=editor.id)


def preserve_audio_correction_history(db, attachment, extraction, final_text, editor):
    """Keep the pre-existing, staff-confirmed spelling suggestions and audit trail."""
    from . import main as m
    original = extraction.original_extracted_text or extraction.extracted_text or ""
    previous_text = extraction.reviewed_text if extraction.status == "reviewed" and extraction.reviewed_text is not None else original
    previous_event = db.scalar(select(m.OcrCorrectionEvent).where(
        m.OcrCorrectionEvent.extraction_id == extraction.id,
        m.OcrCorrectionEvent.confirmed.is_(True), m.OcrCorrectionEvent.coordinate_review_id.is_(None))
        .order_by(m.OcrCorrectionEvent.created_at.desc(), m.OcrCorrectionEvent.id.desc()).limit(1))
    names = [resident.display_name for resident in m._active_message_residents(db, attachment.message)]
    pairs = m.build_correction_pairs(original, final_text, resident_names=names)
    previous_pairs = previous_event.correction_pairs or [] if previous_event is not None else m.build_correction_pairs(original, previous_text, resident_names=names)
    types = {str(pair.get("content_type", "general")) for pair in pairs}
    db.add(m.OcrCorrectionEvent(organization_id=attachment.organization_id, extraction_id=extraction.id,
        attachment_id=attachment.id, source_message_id=attachment.message_id, source_writer_id=attachment.message.sender_id,
        reviewed_by_id=editor.id, decision="direct_edit", raw_text=previous_text, corrected_text=final_text,
        correction_pairs=pairs, content_type=next(iter(types)) if len(types)==1 else "mixed" if types else "general",
        context_text=original[:4000], provider=extraction.provider, model_name=extraction.model_name, confirmed=True))
    before={(str(pair['recognized_text']), str(pair['corrected_text'])) for pair in previous_pairs}
    after={(str(pair['recognized_text']), str(pair['corrected_text'])) for pair in pairs}
    for recognized, corrected in before ^ after:
        memory=db.scalar(select(m.OcrCorrectionMemory).where(m.OcrCorrectionMemory.organization_id==attachment.organization_id,
            m.OcrCorrectionMemory.recognized_text==recognized,m.OcrCorrectionMemory.corrected_text==corrected))
        if (recognized,corrected) in after:
            if memory is None:
                db.add(m.OcrCorrectionMemory(organization_id=attachment.organization_id, recognized_text=recognized,
                    corrected_text=corrected,last_reviewed_by_id=editor.id))
            else:
                memory.occurrence_count+=1;memory.last_reviewed_by_id=editor.id
        elif memory is not None:
            if memory.occurrence_count<=1:db.delete(memory)
            else:memory.occurrence_count-=1;memory.last_reviewed_by_id=editor.id
