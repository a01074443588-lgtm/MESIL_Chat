"""Actual synthetic browser failure: meal outcome attached to a separate fall."""
from datetime import datetime,timezone
from uuid import uuid4
from app.record_text_ai import prepare_evidence
from app.record_answer_quality import guarded_sentences

def test_separate_events_cannot_be_joined_as_a_followup_outcome():
    person=uuid4();first=uuid4();second=uuid4()
    records,_,_=prepare_evidence([
        {'resident_id':person,'resident_name':'합성대상','message_id':first,'event_key':'fall',
         'occurred_at':datetime(2026,9,12,1,tzinfo=timezone.utc),'summary':'이동 중 낙상 발생함. 직원이 부축하여 의자에서 휴식함.'},
        {'resident_id':person,'resident_name':'합성대상','message_id':second,'event_key':'meal',
         'occurred_at':datetime(2026,9,12,3,tzinfo=timezone.utc),'summary':'점심 식사를 절반 먹었고 물 150ml 마심. 이후 불편감 없음.'},
    ],[])
    diagnostics={}
    valid,rejected=guarded_sentences({'sentences':[{
        'text':'인물1은 직원의 부축으로 의자에서 휴식한 후 불편감이 없었습니다.',
        'citations':['S1','S2'],'role':'progress'}]},records,'선택한 기간의 돌봄 기록을 요약해 주세요.',diagnostics)
    assert valid==[]
    assert rejected==1
    assert diagnostics.get('event_relation')==1

def test_explicit_same_event_reply_can_supply_followup():
    person=uuid4();message=uuid4()
    records,_,_=prepare_evidence([
        {'resident_id':person,'resident_name':'합성대상','message_id':message,'event_key':'fall',
         'occurred_at':datetime(2026,9,12,1,tzinfo=timezone.utc),'summary':'직원이 부축하여 의자에서 휴식함.'},
        {'resident_id':person,'resident_name':'합성대상','message_id':message,'event_key':'fall',
         'occurred_at':datetime(2026,9,12,2,tzinfo=timezone.utc),'summary':'이후 불편감 없음.'},
    ],[])
    assert records[0].get('event_group')==records[1].get('event_group')=='E1'
    valid,rejected=guarded_sentences({'sentences':[{
        'text':'인물1은 직원의 부축으로 의자에서 휴식한 후 불편감이 없었습니다.',
        'citations':['S1','S2'],'role':'progress'}]},records,'후속 경과는?')
    assert len(valid)==1 and rejected==0
