from datetime import date, datetime, timezone
from uuid import UUID

from app.record_aggregate import aggregate_question

START=date(2026,9,1);END=date(2026,9,9)
R1=UUID(int=1);R2=UUID(int=2)

def fact(text,number=1,resident=R1,day=5,root=None,kind='event'):
    return {'summary':text,'message_id':UUID(int=100+number),'resident_id':resident,
        'resident_name':{R1:'합성가',R2:'합성나',None:'대상 미지정'}[resident],
        'occurred_at':datetime(2026,9,day,1,number % 60,tzinfo=timezone.utc),
        'event_key':root or str(number),'kind':kind}

def run(facts,question='낙상 기록이 가장 많은 어르신은?',complete=True):
    return aggregate_question(question,facts,start_date=START,end_date=END,scope_complete=complete)

def test_ties_count_linked_records_once_and_keep_all_linked_evidence():
    rows=[fact('낙상 발생함.',1,root='incident'),fact('낙상 후 안정됨.',2,root='incident',kind='followup'),
          fact('이동 중 낙상 발생함.',3,resident=R2)]
    result=run(rows)
    assert result['aggregate']['status']=='complete'
    assert set(result['aggregate']['leaders'])=={str(R1),str(R2)}
    assert [r['record_count'] for r in result['aggregate']['counts']]==[1,1]
    assert set(result['evidence_ids'])=={UUID(int=101),UUID(int=102),UUID(int=103)}

def test_negative_and_planned_are_excluded_without_erasing_a_resolved_occurrence():
    rows=[fact('낙상 없음.',1),fact('낙상 예방 교육 예정.',2),fact('낙상 발생함.',3,root='x'),
          fact('현재 통증 없고 안정됨.',4,root='x',kind='followup')]
    assert run(rows)['aggregate']['counts'][0]['record_count']==1

def test_explicit_cancellation_removes_the_linked_event_not_another_event():
    rows=[fact('낙상 발생함.',1,root='x'),fact('이전 기록은 오기로 취소합니다.',2,root='x',kind='followup'),
          fact('침상 옆 낙상 발생함.',3,root='y')]
    result=run(rows)
    assert result['aggregate']['counts'][0]['record_count']==1
    assert result['evidence_ids']==[UUID(int=103)]

def test_complete_authorized_scope_is_not_truncated_to_the_model_candidate_budget():
    rows=[fact(f'위치 {i}에서 낙상 발생함.',i,resident=R1) for i in range(1,36)]
    rows += [fact('별도 위치에서 낙상 발생함.',40,resident=R2)]
    result=run(rows)
    assert result['aggregate']['counts'][0]['record_count']==35
    assert result['aggregate']['leaders']==[str(R1)]

def test_truncated_scope_never_has_counts_or_a_winner():
    result=run([fact('낙상 발생함.')],complete=False)
    assert result['processing_method']=='clarification'
    assert result['aggregate']['status']=='insufficient'
    assert result['aggregate']['counts']==[] and result['aggregate']['leaders']==[]
    assert result['error_type'] is None

def test_duplicate_unlinked_same_day_observations_are_not_assumed_separate_incidents():
    result=run([fact('낙상 발생함.',1),fact('낙상 발생함.',2)])
    assert 'duplicate_unlinked_records' in result['aggregate']['reason_codes']
    assert result['aggregate']['leaders']==[]

def test_missing_resident_and_uncertain_occurrence_cannot_produce_a_ranking():
    for rows,code in [([fact('낙상 발생함.',resident=None)],'resident_unassigned'),
                      ([fact('낙상 여부 확인 필요.')],'occurrence_uncertain')]:
        result=run(rows)
        assert code in result['aggregate']['reason_codes']
        assert result['aggregate']['leaders']==[]

def test_period_is_record_date_and_explicit_numeric_frequency_is_not_record_count():
    rows=[fact('낙상 2회 발생함.',1),fact('침상 옆 낙상 발생함.',2,day=10)]
    result=run(rows,'낙상은 몇 번 있었나요?')
    assert result['aggregate']['counts'][0]['record_count']==1
    assert result['evidence_ids']==[UUID(int=101)]
    assert '실제 발생 횟수' in result['limitation']

def test_ambiguous_behavior_is_normal_clarification_and_general_status_is_not_aggregate():
    result=run([fact('배회가 관찰됨.')],'가장 문제 행동이 많은 어르신은 누구인가?')
    assert result['processing_method']=='clarification'
    assert result['aggregate']['reason_codes']==['criterion_unspecified']
    assert result['error_type'] is None
    assert run([fact('낙상 발생함.')],'최근 상태가 어떠세요?') is None

def test_negative_for_another_symptom_does_not_cancel_the_requested_event():
    result=run([fact('통증 없음, 낙상 발생함.')])
    assert result['aggregate']['counts'][0]['record_count']==1

def test_corrected_non_occurrence_and_simultaneous_conflict_are_not_counted_as_occurrences():
    original=fact('낙상 발생함.',1,root='x')
    corrected=fact('정정: 낙상이 아님.',2,root='x',kind='followup')
    result=run([original,corrected])
    assert result['aggregate']['status']=='complete'
    assert result['aggregate']['counts']==[]
    same_time={**corrected,'occurred_at':original['occurred_at']}
    result=run([original,same_time])
    assert result['aggregate']['status']=='insufficient'
    assert 'source_order_ambiguous' in result['aggregate']['reason_codes']

def test_time_of_day_ranking_is_not_answered_with_an_unrequested_resident_ranking():
    result=run([fact('낙상 발생함.')],'낙상이 가장 많이 발생한 시간대는?')
    assert result['processing_method']=='clarification'
    assert result['aggregate']['leaders']==[]

def test_a_negative_consequence_does_not_turn_a_recorded_fall_into_no_occurrence():
    result=run([fact('낙상 후 외상 없음.')])
    assert result['aggregate']['counts'][0]['record_count']==1
