from datetime import datetime,timezone,timedelta
from uuid import uuid4
from app.record_question import plan_question,answer_facts
from app.record_answer_quality import guarded_sentences

def test_hydration_adequacy_filters_irrelevant_polite_records_and_answers_the_question():
    person=uuid4();at=datetime(2026,9,9,tzinfo=timezone.utc)
    topics=[];water_id=uuid4()
    for topic,identifier,text in [('식사',water_id,'오전에 물 190ml를 제공했고 제공량을 기록했습니다.'),('건강',uuid4(),'배가 아프지는 않다고 말씀하셨습니다.'),('이동',uuid4(),'이동하실 때 불편한 점은 없다고 하셨습니다.')]:
        topics.append({'resident_id':person,'resident_name':'합성대상','topic':topic,'entries':[{'message_id':identifier,'occurred_at':at,'summary':text,'kind':'event','event_key':str(identifier)}]})
    question='수분 보충은 잘하고 계신가?'
    plan=plan_question(question,topics,resident_id=person)
    assert {r['message_id'] for r in plan['rule_facts']}=={water_id}
    assert {r['message_id'] for r in plan['facts']}=={water_id}
    answer=answer_facts(question,plan['rule_facts'],scope_count=plan['scope_count'])
    assert '판단하기 어렵' in answer['answer'] and '실제 마신 양' in answer['answer']
    assert '배가' not in answer['answer'] and '이동' not in answer['answer']
    assert len(answer['answer_sentences'])==3


def test_latest_water_quantity_links_offer_and_full_consumption_for_natural_phrasings():
    person=uuid4();start=datetime(2026,8,18,5,tzinfo=timezone.utc)
    event='synthetic-water-complete';older='synthetic-water-older'
    offered_id,consumed_id,older_id=uuid4(),uuid4(),uuid4()
    topics=[
        {'resident_id':person,'resident_name':'합성대상','topic':'건강','entries':[
            {'message_id':older_id,'occurred_at':start-timedelta(days=1),'summary':'물 150ml를 제공했고 전량 섭취를 확인했습니다.','kind':'event','event_key':older},
            {'message_id':offered_id,'occurred_at':start,'summary':'물 200ml를 제공했습니다.','kind':'event','event_key':event},
            {'message_id':consumed_id,'occurred_at':start+timedelta(hours=2),'summary':'제공한 물 200ml 전량 섭취를 확인했습니다.','kind':'followup','event_key':event},
        ]},
        {'resident_id':person,'resident_name':'합성대상','topic':'식사','entries':[
            {'message_id':uuid4(),'occurred_at':start+timedelta(hours=3),'summary':'식사 1/2 섭취함.','kind':'event','event_key':'synthetic-meal'},
        ]},
    ]
    questions=[
        '가장 최근에 드신 물의 양', '마지막으로 물을 얼마나 드셨는지',
        '최근 수분 섭취량', '최근 제공받은 물의 양', '물을 몇 ml 드셨는지',
    ]
    expected='가장 최근 기록에서는 8월 18일 오후 2시에 물 200ml를 제공했고, 오후 4시에 200ml 전량 섭취가 확인됐습니다.'
    for question in questions:
        plan=plan_question(question,topics,resident_id=person)
        answer=answer_facts(question,plan['rule_facts'],scope_count=plan['scope_count'])
        assert answer['answer']==expected
        assert answer['evidence_ids']==[offered_id,consumed_id]
        assert {fact['message_id'] for fact in plan['facts']}=={offered_id,consumed_id}


def test_latest_water_quantity_does_not_confirm_offer_without_full_consumption():
    person=uuid4();identifier=uuid4();at=datetime(2026,8,18,5,tzinfo=timezone.utc)
    topics=[{'resident_id':person,'resident_name':'합성대상','topic':'건강','entries':[
        {'message_id':identifier,'occurred_at':at,'summary':'물 200ml를 제공했습니다.','kind':'event','event_key':'synthetic-water-open'},
    ]}]
    plan=plan_question('가장 최근에 드신 물의 양',topics,resident_id=person)
    answer=answer_facts('가장 최근에 드신 물의 양',plan['rule_facts'],scope_count=plan['scope_count'])
    assert '전량 섭취가 확인' not in answer['answer']
    assert answer['evidence_ids']==[identifier]

def test_natural_paraphrase_allowed_but_offered_is_not_consumed_or_sufficient():
    records=[{'id':'S1','date':'2026-09-09 09:00','person':'인물1','text':'물 190ml를 제공했습니다. 기침 호소는 없었습니다.'}]
    def check(text):return guarded_sentences({'sentences':[{'text':text,'citations':['S1'],'role':'conclusion'}]},records,'수분 보충은 잘하고 계신가?')[0]
    assert check('물을 제공한 기록은 확인되지만, 실제 섭취 상태는 이 근거만으로 판단하기 어렵습니다.')
    assert not check('물을 190ml 마셨습니다.')
    assert not check('수분을 충분히 섭취했습니다.')
    assert not check('물을 290ml 제공했습니다.')
    assert not check('약 190mg을 투약했습니다.')
    assert not check('기침이 있었습니다.')
    assert not check('9월 8일 물을 제공했습니다.')
    assert not guarded_sentences({'sentences':[{'text':'물을 제공했습니다.','citations':['S999']}]},records,'최근에 어떠셨어?')[0]

def test_prescription_is_not_taking_and_a_future_plan_is_not_completion():
    records=[
        {'id':'S1','date':'2026-09-09 09:00','person':'인물1','text':'진료 후 약을 처방받았습니다.'},
        {'id':'S2','date':'2026-09-09 10:00','person':'인물1','text':'오후 2시에 교육을 진행할 예정입니다.'},
    ]
    assert not guarded_sentences(
        {'sentences':[{'text':'약을 복용했습니다.','citations':['S1'],'role':'conclusion'}]},
        records,'약은 드셨어?')[0]
    assert not guarded_sentences(
        {'sentences':[{'text':'오후 2시에 교육을 완료했습니다.','citations':['S2'],'role':'conclusion'}]},
        records,'교육은 했어?')[0]
    assert guarded_sentences(
        {'sentences':[{'text':'오후 2시에 교육이 예정되어 있습니다.','citations':['S2'],'role':'conclusion'}]},
        records,'교육 일정 알려줘')[0]

def test_citations_are_server_normalized_only_to_ids_in_the_current_evidence_set():
    records=[
        {'id':'S1','date':'2026-09-08 09:00','person':'인물1','text':'식사 1/2 섭취했습니다.'},
        {'id':'S2','date':'2026-09-09 09:00','person':'인물1','text':'식사 3/4 섭취했습니다.'},
    ]
    diagnostics={}
    valid,rejected=guarded_sentences({'sentences':[{'text':'식사량은 이전보다 늘었습니다.','citations':['S1_S2'],'role':'progress'}]},records,'전보다 달라진 게 있어?',diagnostics)
    assert rejected==0 and valid[0].citations==['S1','S2']
    assert diagnostics=={'citation_normalized':1}
    assert not guarded_sentences({'sentences':[{'text':'식사량은 늘었습니다.','citations':['S1_S999'],'role':'progress'}]},records,'전보다 달라진 게 있어?')[0]

def test_date_time_reformat_and_fraction_spacing_are_verified_against_source():
    records=[{'id':'S1','date':'2026-09-03 18:00','person':'인물1','text':'오후 6시에 식사 1/2 섭취함. 물 180ml 제공함.'}]
    assert guarded_sentences({'sentences':[{'text':'2026년 9월 3일 오후 6시에 식사 1 / 2 섭취했고 물 180ml를 제공했습니다.','citations':['S1']}]},records,'최근에 어떠셨어?')[0]
    assert guarded_sentences({'sentences':[{'text':'2026년 9월 3일 오후 6시에 식사 1／2 섭취했고 물 180ml를 제공했습니다.','citations':['S1']}]},records,'최근에 어떠셨어?')[0]
    assert not guarded_sentences({'sentences':[{'text':'2026년 9월 4일 오후 6시에 식사 1 / 2 섭취했습니다.','citations':['S1']}]},records,'최근에 어떠셨어?')[0]
    assert not guarded_sentences({'sentences':[{'text':'2026년 9월 3일 오후 7시에 식사 1 / 2 섭취했습니다.','citations':['S1']}]},records,'최근에 어떠셨어?')[0]

def test_event_time_is_verified_against_the_cited_original_not_only_created_at():
    records=[
        {'id':'S1','date':'2026-09-03 09:00','person':'인물1','text':'오전 10시에 교육을 진행했습니다.'},
        {'id':'S2','date':'2026-09-03 11:00','person':'인물1','text':'오전 11시에 상태를 확인했습니다.'},
    ]
    valid,_=guarded_sentences(
        {'sentences':[{'text':'9월 3일 오전 10시에 교육을 진행했습니다.','citations':['S1']}]},
        records,
        '교육은 언제 했어?',
    )
    assert len(valid)==1
    # A time written in another record cannot support this sentence when it is
    # not one of the sentence's citations.
    assert not guarded_sentences(
        {'sentences':[{'text':'9월 3일 오전 11시에 교육을 진행했습니다.','citations':['S1']}]},
        records,
        '교육은 언제 했어?',
    )[0]
    assert not guarded_sentences(
        {'sentences':[{'text':'9월 3일 오전 9시에 교육을 진행했습니다.','citations':['S1']}]},
        records,
        '교육은 언제 했어?',
    )[0]
    no_event_time=[{'id':'S3','date':'2026-09-03 18:00','person':'인물1','text':'교육을 진행했습니다.'}]
    assert not guarded_sentences(
        {'sentences':[{'text':'9월 3일 18:00에 교육을 진행했습니다.','citations':['S3']}]},
        no_event_time,'교육은 언제 했어?')[0]

def test_explicit_event_date_takes_precedence_over_the_message_created_date():
    records=[{'id':'S1','date':'2026-09-03 09:00','person':'인물1','text':'9월 2일에 교육을 진행했습니다.'}]
    assert guarded_sentences(
        {'sentences':[{'text':'9월 2일에 교육을 진행했습니다.','citations':['S1']}]},
        records,'교육은 언제 했어?')[0]
    assert not guarded_sentences(
        {'sentences':[{'text':'9월 3일에 교육을 진행했습니다.','citations':['S1']}]},
        records,'교육은 언제 했어?')[0]

def test_internal_person_and_source_markers_do_not_become_care_numbers_or_ui_text():
    records=[{'id':'S1','date':'2026-09-03 18:00','person':'인물1','text':'식사 1/2 섭취함.'}]
    diagnostics={}
    valid,rejected=guarded_sentences({'sentences':[{'text':'인물1의 S1 기록에는 식사 1/2 섭취했습니다.','citations':['S1']}]},records,'최근에 어떠셨어?',diagnostics)
    assert rejected==0 and len(valid)==1 and 'S1' not in valid[0].text
    assert diagnostics['source_marker_removed']==1
