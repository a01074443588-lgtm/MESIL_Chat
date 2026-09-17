import json
import pytest
from app import local_ai, record_narrative
from test_workdesk_central_summary import configure

ENTRIES=[
    {"number":1,"resident":"합성대상","body":"물 180ml를 제공했습니다. 마신 양은 기록하지 않았습니다."},
    {"number":2,"resident":"합성대상","body":"물 200ml를 제공했고 모두 마셨습니다. 불편은 없었습니다."},
    {"number":3,"resident":"합성대상","body":"물 200ml를 모두 마셨습니다. 불편은 없었습니다."},
]

def claim(text,citations,section="overview",resident="합성대상"):
    return dict(text=text,citations=citations,section=section,resident=resident)

GOOD=[claim("물 200ml를 모두 마셨으며 불편은 없었습니다.",[2,3]),
      claim("물 180ml를 제공했으나 마신 양은 기록하지 않았습니다.",[1],"completed")]

def run(monkeypatch,claims,verdict,entries=ENTRIES):
    configure(monkeypatch)
    calls=[]
    async def transport(client,base,path,body,timeout):
        if path=="/api/show":return {}
        if path=="/api/ps":return {"models":[{"name":"central-model","context_length":4096}]}
        calls.append(body)
        output={"claims":claims} if len(calls)==1 else {"supported":verdict}
        return {"model":"central-model","done":True,"message":{"content":json.dumps(output)}}
    monkeypatch.setattr(record_narrative,"local_request",transport)
    result=local_ai.summarize_room_messages(entries=entries,external_allowed=True,purpose="급여제공 기록",central_feature="document_text")
    return result,calls

def test_only_verified_claims_render_when_all_input_sources_remain_covered(monkeypatch):
    invented=claim("직원이 옆에서 직접 도와 드렸습니다.",[2,3],"completed")
    result,calls=run(monkeypatch,[*GOOD,invented],[True,True,False])
    assert result.provider=="ollama" and result.model=="central-model"
    assert "직원이" not in result.summary
    assert all(f"[{i}]" in result.summary for i in (1,2,3))
    assert all(title in result.summary for title in ("[한눈에 보기]","[먼저 확인]","[이미 한 일]","[다음 업무 제안]"))
    reviewed=json.loads(calls[1]["messages"][1]["content"])["items"]
    assert [row["number"] for row in reviewed[0]["records"]]==[2,3]
    assert [row["number"] for row in reviewed[1]["records"]]==[1]

def test_missing_input_source_after_rejection_is_not_a_complete_summary(monkeypatch):
    with pytest.raises(local_ai.LocalAiError,match="summary_evidence_incomplete"):
        run(monkeypatch,GOOD,[True,False])

def test_number_must_be_supported_by_cited_record_not_another_record(monkeypatch):
    bad=claim("물 200ml를 제공했습니다.",[1],"completed")
    result,calls=run(monkeypatch,[*GOOD,bad],[True,True])
    assert "물 200ml를 제공했습니다." not in result.summary
    assert len(json.loads(calls[1]["messages"][1]["content"])["items"])==2

def test_other_person_cannot_own_a_source_even_if_model_reviewer_would_accept(monkeypatch):
    with pytest.raises(local_ai.LocalAiError,match="summary_evidence_incomplete"):
        run(monkeypatch,[GOOD[0],claim(GOOD[1]["text"],[1],resident="다른대상")],[True])

@pytest.mark.parametrize("verdict",[[True], [True,True,True], ["true",True], True])
def test_review_shape_or_count_mismatch_never_silently_accepts(monkeypatch,verdict):
    with pytest.raises(local_ai.LocalAiError,match="review_format_invalid"):
        run(monkeypatch,GOOD,verdict)

def test_fifty_selected_sources_are_not_reduced_to_four_sentences_or_six_citations(monkeypatch):
    entries=[{"number":i,"resident":f"합성대상{i}","body":"물 200ml를 모두 마셨습니다."} for i in range(1,51)]
    claims=[claim("물 200ml를 모두 마셨습니다.",[i],resident=f"합성대상{i}") for i in range(1,51)]
    result,calls=run(monkeypatch,claims,[True]*50,entries)
    assert all(f"[{i}]" in result.summary for i in range(1,51))
    assert len(json.loads(calls[1]["messages"][1]["content"])["items"])==50
