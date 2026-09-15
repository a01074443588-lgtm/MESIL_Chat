"""Bounded relationship loading and timing without logging record content."""
from time import perf_counter
from types import SimpleNamespace
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from .models import Message,MessageAttachment,MessageComment,MessageResidentLink,User,OcrCorrectionEvent
from .schemas import RepliedMessageSource
from .services import attachment_response
from .attachment_review_policy import requires_staff_review, attachment_evidence_text
from .resident_candidate_evidence import resident_link_is_current, current_message_resident


def prefetch_period_relations(db, messages):
    ids=[message.id for message in messages]
    for offset in range(0,len(ids),400):
        list(db.scalars(select(Message).where(Message.id.in_(ids[offset:offset+400])).options(
            selectinload(Message.attachments).selectinload(MessageAttachment.text_extraction),
            selectinload(Message.resident_links).selectinload(MessageResidentLink.resident),
            selectinload(Message.resident),selectinload(Message.action_item),
            selectinload(Message.sender).selectinload(User.staff),
            selectinload(Message.comments).selectinload(MessageComment.author).selectinload(User.staff),
        )).unique())


def briefing_sources(db,messages):
    """Same source facts as full DTOs, without correction candidate generation."""
    ids=[a.text_extraction.id for m in messages for a in m.attachments if a.text_extraction]
    confirmed={}
    for offset in range(0,len(ids),400):
        for event in db.scalars(select(OcrCorrectionEvent).where(OcrCorrectionEvent.extraction_id.in_(ids[offset:offset+400]),OcrCorrectionEvent.confirmed.is_(True)).order_by(OcrCorrectionEvent.created_at.desc())):
            confirmed.setdefault(event.extraction_id,event.corrected_text)
    result=[]
    for message in messages:
        attachments=[]
        for attachment in message.attachments:
            value=attachment_response(attachment,include_review_details=False)
            if not requires_staff_review(attachment) and value.text_extraction and attachment.text_extraction.id in confirmed and (not attachment.mime_type.startswith("image/") or value.photo_reading_status == "completed"):
                value.text_extraction=value.text_extraction.model_copy(update={'latest_confirmed_text':confirmed[attachment.text_extraction.id]})
            attachments.append(value)
        reply=None
        if isinstance(message.extra_data,dict) and isinstance(message.extra_data.get('reply_to'),dict):
            try:reply=RepliedMessageSource.model_validate(message.extra_data['reply_to'])
            except ValueError:pass
        def resident_value(resident):return SimpleNamespace(id=resident.id,display_name=resident.display_name) if resident else None
        result.append(SimpleNamespace(message=SimpleNamespace(id=message.id,room_id=message.room_id,created_at=message.created_at,body=message.body,resident=resident_value(current_message_resident(message)),resident_links=[SimpleNamespace(status=link.status,resident=resident_value(link.resident)) for link in message.resident_links if link.status=='confirmed' and resident_link_is_current(link,message)],attachments=attachments,reply_to=reply,is_recalled=message.lifecycle_status=='recalled'),comments=[SimpleNamespace(id=c.id,body=c.body,created_at=c.created_at) for c in message.comments]))
    return result


class PeriodTiming:
    def __init__(self,request=None):
        self.data=request.scope.setdefault('period_timing',{}) if request else {}
        self.last=self.data.setdefault('started',perf_counter());self.stage='permission'
        self.data['durations']={}
    def phase(self,name):
        now=perf_counter();durations=self.data['durations']
        durations[self.stage]=durations.get(self.stage,0)+(now-self.last)*1000
        self.last=now;self.stage=name
    def finish(self,result):
        self.phase('serialization');self.data['finished']=perf_counter()
        return result


class PeriodTimingMiddleware:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope.get('path')!='/api/workdesk/period-review':
            return await self.app(scope,receive,send)
        scope['period_timing']={'started':perf_counter()}
        async def timed_send(message):
            if message['type']=='http.response.start':
                now=perf_counter();data=scope['period_timing'];durations=dict(data.get('durations',{}))
                durations['serialization']=(now-data.get('finished',now))*1000
                durations['backend_total']=(now-data['started'])*1000
                header=', '.join(f'{key};dur={value:.3f}' for key,value in durations.items())
                message['headers']=[*message.get('headers',[]),(b'server-timing',header.encode()),(b'cache-control',b'no-store')]
            await send(message)
        await self.app(scope,receive,timed_send)
