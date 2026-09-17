"""Explicit staff photo/resident decisions, using existing links and audit rows."""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from uuid import UUID

from . import main as m
from .database import get_db
from .dependencies import get_current_user
from .models import AttachmentTextExtraction, Message, MessageAttachment, MessageResidentLink, Room, User, WorkItem, utcnow
from .schemas import AttachmentResponse, MessageResponse, MessageResidentReviewRequest, PhotoReadingChoiceRequest
from .services import active_membership, attachment_response, message_response, record_audit, room_member_user_ids
from .resident_candidate_evidence import resident_link_is_current
from .photo_reading import resident_review_metadata

router=APIRouter()


AI_ROOM_RESIDENT_LINK_ERROR = (
    "MESIL AI 도움방의 메시지는 어르신 기록 연결 대상이 아닙니다."
)


def reject_ai_room_resident_link(db: Session, message: Message) -> None:
    room = message.room if message.room is not None else db.get(Room, message.room_id)
    if room is not None and room.kind == "ai":
        raise HTTPException(422, AI_ROOM_RESIDENT_LINK_ERROR)


def message_for_editor(db, editor, message_id):
    message=db.get(Message,message_id)
    if message is None or message.organization_id != editor.organization_id:
        raise HTTPException(404,"메시지를 찾을 수 없습니다.")
    m._require_active_message(message,"어르신 연결 확인")
    reject_ai_room_resident_link(db, message)
    if getattr(editor,"_reviewer_experience",None) is not None:
        m._message_for_member(db,editor,message_id)
    if editor.role == "admin":
        return message
    if editor.can_process_records:
        # A room member with record-processing permission may manage links in
        # that room even when the message also has a work item assigned across
        # business units. Work-item scope is an additional access path, not a
        # reason to reject an otherwise accessible room member.
        if active_membership(db,editor.id,message.room_id):
            return message
        item=db.scalar(select(WorkItem).where(WorkItem.source_message_id==message.id))
        if item is not None:
            try:
                m._work_item_for_processor(db,editor,item.id)
            except HTTPException as exc:
                if exc.status_code in {403,404}:
                    raise HTTPException(404,"메시지를 찾을 수 없습니다.") from exc
                raise
            return message
        raise HTTPException(404,"메시지를 찾을 수 없습니다.")
    if active_membership(db,editor.id,message.room_id):
        raise HTTPException(403,"이 메시지의 어르신 연결을 관리할 권한이 없습니다.")
    raise HTTPException(404,"메시지를 찾을 수 없습니다.")


def apply_review(db,editor,message_id,decision,resident_id=None,previous_resident_id=None,resident_ids=None,*,commit=True,authorized_message=None):
    if authorized_message is None:
        message=message_for_editor(db,editor,message_id)
    else:
        # Attachment review has already passed its own uploader/processor ACL.
        # Carry only that exact message into this shared transactional writer;
        # direct resident-management endpoints still use message_for_editor.
        message=authorized_message
        if message.id != message_id or message.organization_id != editor.organization_id:
            raise HTTPException(404,"메시지를 찾을 수 없습니다.")
        m._require_active_message(message,"어르신 연결 확인")
        reject_ai_room_resident_link(db, message)
    # Serialize competing decisions, then reload current links before auditing.
    db.execute(select(Message.id).where(Message.id==message.id).with_for_update())
    db.refresh(message)
    item=db.scalar(select(WorkItem).where(WorkItem.source_message_id==message.id))
    if item is not None and item.confirmed_at is not None:
        raise HTTPException(409,"최종 승인한 업무기록은 기록 담당자의 정정 절차가 필요합니다.")
    links=list(db.scalars(select(MessageResidentLink).where(MessageResidentLink.message_id==message.id).with_for_update()))
    snapshot=lambda rows:[{"resident_id":str(r.resident_id),"status":r.status,"source":r.source} for r in rows]
    before=snapshot(links);now=utcnow()
    old_review=resident_review_metadata(message)
    if decision=="set":
        selected_ids=list(dict.fromkeys(resident_ids or []))
        allowed={resident.id:resident for resident in m._active_message_residents(db,message)}
        if any(selected_id not in allowed for selected_id in selected_ids):
            raise HTTPException(422,"선택 범위에서 어르신을 확인할 수 없습니다.")
        for selected_id in selected_ids:
            m._residents_for_room(db,message.room,resident_id=selected_id,resident_ids=None)
        by_resident={link.resident_id:link for link in links}
        selected_set=set(selected_ids)
        for selected_id in selected_ids:
            link=by_resident.get(selected_id)
            if link is None:
                link=MessageResidentLink(organization_id=message.organization_id,message_id=message.id,
                    resident_id=selected_id,source="manual",status="confirmed",
                    reviewed_by_id=editor.id,reviewed_at=now)
                db.add(link);links.append(link);by_resident[selected_id]=link
            elif link.status!="confirmed" or link.source!="manual":
                link.source="manual";link.status="confirmed"
                link.reviewed_by_id=editor.id;link.reviewed_at=now
        explicitly_removed=set()
        for link in links:
            if link.resident_id not in selected_set and link.status!="rejected":
                link.status="rejected";link.reviewed_by_id=editor.id;link.reviewed_at=now
                explicitly_removed.add(link.resident_id)
        prior_excluded={value for value in old_review.get("manual_excluded_resident_ids",[]) if isinstance(value,str)}
        excluded=(prior_excluded|{str(value) for value in explicitly_removed})-{str(value) for value in selected_set}
        db.flush()
        after=snapshot(links)
        metadata_changed=(
            set(old_review.get("selected_resident_ids",[]))!={str(value) for value in selected_set}
            or set(old_review.get("manual_excluded_resident_ids",[]))!=excluded
            or bool(old_review.get("unrelated"))
        )
        if before!=after or metadata_changed:
            revision=int(old_review.get("revision",0) or 0)+1
            message.extra_data={**(message.extra_data or {}),"resident_review":{
                **old_review,"decision":"set","revision":revision,
                "reviewed_by_id":str(editor.id),"reviewed_at":now.isoformat(),
                "selected_resident_ids":[str(value) for value in selected_ids],
                "manual_excluded_resident_ids":sorted(excluded),
                "suppress_automatic":True,"unrelated":False,
            }}
            db.expire(message,["resident_links"]);m._refresh_work_item_residents(db,message)
            record_audit(db,actor_id=editor.id,action="message_resident_links.set",target_type="message",target_id=message.id,
                details={"revision":revision,"selected_count":len(selected_ids),"added_or_confirmed_count":sum(1 for row in after if row not in before),"removed_count":len(explicitly_removed)})
            m._search_summary_cache.clear()
            m._adaptive_search_summary_cache.clear()
        if commit:
            db.commit();db.refresh(message)
        else:
            db.flush()
        return message
    target=None
    if decision in {"confirm","change"}:
        if resident_id is None:raise HTTPException(422,"어르신을 선택해 주세요.")
        target=next((r for r in links if r.resident_id==resident_id),None)
        if decision=="confirm":
            if target is None or not resident_link_is_current(target,message):
                raise HTTPException(409,"현재 판독문과 확인 후보를 먼저 확인해 주세요.")
            if not target.resident.is_active or target.resident.status != "active":
                raise HTTPException(409,"현재 이용 중인 어르신을 선택해 주세요.")
        else:
            allowed={r.id:r for r in m._active_message_residents(db,message)}
            if resident_id not in allowed:raise HTTPException(422,"선택 범위에서 어르신을 확인할 수 없습니다.")
            m._residents_for_room(db,message.room,resident_id=resident_id,resident_ids=None)
            if target is None:
                target=MessageResidentLink(organization_id=message.organization_id,message_id=message.id,
                    resident_id=resident_id,source="manual",status="confirmed")
                db.add(target);links.append(target)
            target.source="manual"
    if decision not in {"confirm","change","unrelated","reject_one"}:
        raise HTTPException(422,"확인 방법을 선택해 주세요.")
    for link in links:
        if link is target:
            link.status="confirmed";link.reviewed_by_id=editor.id;link.reviewed_at=now
        elif (decision in {"change","unrelated"}
              or decision=="reject_one" and link.resident_id==resident_id
              or decision=="confirm" and target is not None and link.resident.display_name==target.resident.display_name):
            link.status="rejected";link.reviewed_by_id=editor.id;link.reviewed_at=now
    if decision=="reject_one" and not any(r.resident_id==resident_id for r in links):
        raise HTTPException(404,"확인 후보를 찾을 수 없습니다.")
    explicit_excluded={value for value in old_review.get("manual_excluded_resident_ids",[]) if isinstance(value,str)}
    if decision in {"change","unrelated","reject_one"}:
        explicit_excluded.update(str(link.resident_id) for link in links if link is not target and link.status=="rejected")
    if target is not None:
        explicit_excluded.discard(str(target.resident_id))
    message.extra_data={**(message.extra_data or {}),"resident_review":{
        **old_review,"decision":decision,"revision":int(old_review.get("revision",0))+1,
        "reviewed_by_id":str(editor.id),"reviewed_at":now.isoformat(),
        "suppress_automatic":decision in {"change","unrelated"} or bool(old_review.get("suppress_automatic")),
        "manual_excluded_resident_ids":sorted(explicit_excluded),
        "unrelated":decision=="unrelated",
    }}
    db.flush();db.expire(message,["resident_links"])
    m._refresh_work_item_residents(db,message)
    record_audit(db,actor_id=editor.id,action="message_resident_link.reviewed",target_type="message",target_id=message.id,
        details={"decision":decision,"before":before,"after":snapshot(links),"previous_resident_id":str(previous_resident_id) if previous_resident_id else None})
    db.commit();db.refresh(message)
    m._search_summary_cache.clear()
    m._adaptive_search_summary_cache.clear()
    return message


@router.patch("/api/messages/{message_id}/resident-review",response_model=MessageResponse)
async def review(message_id:UUID,payload:MessageResidentReviewRequest,editor:User=Depends(get_current_user),db:Session=Depends(get_db)):
    message=apply_review(db,editor,message_id,payload.decision,payload.resident_id,payload.previous_resident_id,payload.resident_ids)
    response=message_response(message,db=db,viewer_id=editor.id)
    # Each recipient receives only a change notice and re-fetches under its own ACL.
    await m.manager.send_to_users(room_member_user_ids(db,message.room_id),{"event":"message_metadata_changed","message_id":str(message.id),"room_id":str(message.room_id)})
    return response


@router.get("/api/messages/{message_id}/resident-review/options")
def options(message_id:UUID,editor:User=Depends(get_current_user),db:Session=Depends(get_db)):
    message=message_for_editor(db,editor,message_id)
    return [{"id":str(r.id),"display_name":r.display_name,"room_name":r.room.name if r.room else None,
             "floor_name":r.floor.name if r.floor else None,"internal_code":r.internal_code,"service_type":r.service_type}
            for r in m._active_message_residents(db,message)]


@router.get("/api/workdesk/resident-confirmations")
def inbox(processor:User=Depends(m._require_processor),db:Session=Depends(get_db)):
    possible=db.scalars(select(Message).outerjoin(WorkItem,WorkItem.source_message_id==Message.id).where(
        Message.organization_id==processor.organization_id,Message.lifecycle_status=="active",
        WorkItem.confirmed_at.is_(None),Message.resident_links.any(MessageResidentLink.status=="candidate"),
    ).order_by(Message.created_at.desc())).unique()
    messages=[]
    for message in possible:
        try:message_for_editor(db,processor,message.id)
        except HTTPException as exc:
            if exc.status_code in {403,404}:continue
            raise
        if any(link.status=="candidate" and resident_link_is_current(link,message) for link in message.resident_links):
            messages.append(message)
    return {"count":len(messages),"messages":[message_response(message,db=db,viewer_id=processor.id) for message in messages[:100]],"has_more":len(messages)>100}


@router.patch("/api/attachments/{attachment_id}/photo-reading",response_model=AttachmentResponse)
async def photo_choice(attachment_id:UUID,payload:PhotoReadingChoiceRequest,background_tasks:BackgroundTasks,
                 editor:User=Depends(get_current_user),db:Session=Depends(get_db)):
    attachment=m._attachment_for_text_editor(db,editor,attachment_id)
    if attachment.mime_type not in m.IMAGE_MIME_TYPES:raise HTTPException(422,"사진에서만 선택할 수 있습니다.")
    db.execute(select(MessageAttachment.id).where(MessageAttachment.id==attachment.id).with_for_update())
    extraction=db.scalar(select(AttachmentTextExtraction).where(AttachmentTextExtraction.attachment_id==attachment.id).with_for_update())
    before=extraction.status if extraction else "general"
    if payload.decision=="read":
        if extraction and extraction.status in {"pending","processing"}:
            return attachment_response(attachment,db=db,viewer_id=editor.id)
        m._queue_attachment_text_extraction(db,attachment=attachment,requested_by=editor)
    else:
        if extraction is None:
            extraction=AttachmentTextExtraction(organization_id=attachment.organization_id,attachment_id=attachment.id,
                status="not_required",provider="none",model_name="",requested_by_id=editor.id)
            db.add(extraction)
        else:
            extraction.status="not_required"
            extraction.error_message=None
            extraction.completed_at=utcnow()
        # Keep prior text/attempts internally; visibility and candidates are
        # gated by current state. Confirmed manual resident links are preserved.
        extraction.reviewed_by_id=editor.id;extraction.reviewed_at=utcnow()
    record_audit(db,actor_id=editor.id,action="photo_reading.choice",target_type="attachment",target_id=attachment.id,
        details={"before_status":before,"decision":payload.decision})
    db.commit();db.expire(attachment,["text_extraction"])
    m._search_summary_cache.clear()
    m._adaptive_search_summary_cache.clear()
    response=attachment_response(attachment,db=db,viewer_id=editor.id)
    await m.manager.send_to_users(room_member_user_ids(db,attachment.message.room_id),
        {"event":"message_metadata_changed","message_id":str(attachment.message_id),"room_id":str(attachment.message.room_id)})
    if payload.decision=="read":background_tasks.add_task(m._run_attachment_text_extraction,attachment.id)
    return response
