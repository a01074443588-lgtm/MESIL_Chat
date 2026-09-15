import pytest

from app.ai_help_questions import (
    apply_general_guidance_boundaries,
    classify_ai_help_question,
)


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("기저귀는 언제 교체해야 하나요?", "general_guidance"),
        ("기저귀 케어의 원칙과 유의사항을 알려줘.", "general_guidance"),
        ("돌봄 기록 작성 방법과 주의사항을 알려줘.", "general_guidance"),
        ("낙상 예방 기록은 어떻게 작성하나요?", "general_guidance"),
        ("어르신 낙상 기록은 어떻게 작성해야 하나요?", "general_guidance"),
        ("보호자에게 상태를 설명할 때 주의할 점은?", "general_guidance"),
        ("응급상황에는 어떻게 대응해야 하나요?", "general_guidance"),
        ("투약 변경을 결정해 줘.", "general_guidance"),
        ("최신 공단 기준을 확정해 줘.", "general_guidance"),
        ("어르0004의 최근 근황을 기록에서 찾아줘.", "record_search"),
        ("어르0004의 최근 근황을 알려줘.", "record_search"),
        ("어르0001의 최근 보고는 뭐가 있어?", "record_search"),
        ("어르0001의 최근 기록을 알려줘.", "record_search"),
        ("어르0001에 관해 최근 뭐가 보고됐어?", "record_search"),
        ("최근에 어르신 중 급격한 변화가 있는 분이 계신가?", "record_search"),
        ("최근 상태가 달라진 어르신과 근거를 알려줘.", "record_search"),
        ("최근 상태가 급격히 변한 어르신이 누가 있는가?", "record_search"),
        ("지난 7일간 평소와 다른 상태가 보고된 어르신이 있어?", "record_search"),
        ("어르신 중 식사량이 줄었다고 보고된 분은 누구야?", "record_search"),
        ("지난 7일간 어르0002의 식사 기록을 보여줘.", "record_search"),
        ("지난 7일 기록을 보여줘.", "record_search"),
        ("오늘 작성된 낙상 관련 대화를 검색해줘.", "record_search"),
        ("어르0003에 대해 몇 번 보고됐는지 알려줘.", "record_search"),
        ("어르0002가 언제 식사했다고 기록됐나요?", "record_search"),
        ("어르0001의 현재 상태를 알려줘.", "record_search"),
        ("지난 7일간 어르0002의 기저귀 교체 기록을 보여줘.", "record_search"),
        ("오늘 작성된 낙상 예방 대화를 검색해줘.", "record_search"),
        ("어르0002의 낙상 예방 기록이 있는지 찾아줘.", "record_search"),
        ("이번 주 기저귀 교체 기록이 몇 건인지 알려줘.", "record_search"),
        ("기저귀는 일반적으로 언제 교체해야 하나요?", "general_guidance"),
        ("기저귀 교체 방법과 주의사항을 알려줘.", "general_guidance"),
        ("낙상 예방 방법을 알려줘.", "general_guidance"),
        ("낙상 예방 대화를 기록할 때 주의사항을 알려줘.", "general_guidance"),
        ("돌봄 기록 작성 방법과 원칙을 알려줘.", "general_guidance"),
        ("어르신의 급격한 상태 변화가 있을 때 어떻게 대응해야 하나요?", "general_guidance"),
        ("어르신 식사량 감소를 관찰할 때 주의사항은?", "general_guidance"),
        ("어르신 기저귀 케어 시 왜 가림막을 쳐야 하는지 알려줘.", "general_guidance"),
        ("기저귀 케어에서 가림막을 사용하는 이유를 알려줘.", "general_guidance"),
        ("어르신 목욕 지원 시 문을 닫는 목적은 무엇인가요?", "general_guidance"),
        ("돌봄 과정에서 사생활 보호가 필요한 이유를 알려줘.", "general_guidance"),
        ("지난 7일간 어르0002 기저귀 케어 때 가림막을 사용한 기록을 보여줘.", "record_search"),
        ("오늘 작성된 사생활 보호 관련 대화를 검색해줘.", "record_search"),
        ("어르0002가 왜 넘어졌는지 기록에서 찾아줘.", "record_search"),
        ("어르0002가 왜 넘어졌는지 단정해줘.", "clarification"),
        ("이 이미지를 설명해 주세요.", "attachment_guidance"),
        ("첨부 문서의 핵심 내용을 알려줘.", "attachment_guidance"),
        ("올린 파일을 읽고 요약해 줘.", "attachment_guidance"),
        ("도와줘", "clarification"),
        ("이건 어때?", "clarification"),
    ],
)
def test_ai_help_question_routing_requires_semantic_intent(question, expected):
    assert classify_ai_help_question(question) == expected


def test_known_resident_name_plus_recent_state_remains_a_record_search():
    assert (
        classify_ai_help_question(
            "가상어르신의 최근 상태를 알려줘.",
            known_resident_names=["가상어르신"],
        )
        == "record_search"
    )


@pytest.mark.parametrize(
    "question",
    [
        "어르0002의 최근 7일 상태와 근거를 알려줘.",
        "어르0002 최근 상태를 알려줘.",
        "어르0002의 최근 근황은 어때?",
        "어르0002의 오늘 기록을 보여줘.",
        "최근 어르0002에게 달라진 점이 있어?",
        "어르0002 관련 근거 기록을 찾아줘.",
    ],
)
def test_specific_resident_record_context_takes_priority_over_general_guidance(question):
    """A concrete resident, record scope/context and lookup request must search records."""
    assert classify_ai_help_question(question) == "record_search"


@pytest.mark.parametrize(
    "question",
    [
        "어르신의 상태를 확인하는 방법을 알려줘.",
        "어르신 상태변화 관찰 시 주의사항은 무엇이야?",
        "기저귀 케어 시 가림막을 쳐야 하는 이유를 알려줘.",
        "낙상 예방을 위해 직원이 확인할 점을 알려줘.",
    ],
)
def test_general_care_questions_without_a_specific_resident_stay_general(question):
    assert classify_ai_help_question(question) == "general_guidance"


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("요양원의 정의는?", "general_guidance"),
        ("요양원과 요양병원의 차이는?", "general_guidance"),
        ("어르신 기저귀 케어 시 왜 가림막을 쳐야 하나요?", "general_guidance"),
        ("낙상 예방을 위해 직원이 확인할 사항을 알려줘.", "general_guidance"),
        ("욕창 예방의 기본 원칙은?", "general_guidance"),
        ("치매 어르신과 대화하는 방법을 알려줘.", "general_guidance"),
        ("야간 순회가 필요한 이유는?", "general_guidance"),
        ("보호자에게 상태를 설명할 때 주의사항은?", "general_guidance"),
        ("우리 시설의 급여종류는 뭐야?", "general_guidance"),
        ("시설 평가지표 6번의 내용은 뭐야?", "general_guidance"),
        ("지난 7일간 어르0002의 기저귀 교체 기록을 보여줘.", "record_search"),
        ("오늘 작성된 낙상 예방 대화를 검색해줘.", "record_search"),
        ("이번 주 어르0002의 식사 기록은 몇 건이야?", "record_search"),
        ("어르0004의 최근 근황을 기록에서 찾아줘.", "record_search"),
        ("이 이미지를 설명해 주세요.", "attachment_guidance"),
        ("첨부 문서의 핵심을 알려줘.", "attachment_guidance"),
        ("도와줘.", "clarification"),
        ("이건 어때?", "clarification"),
        ("그거 해줘.", "clarification"),
        ("   ", "clarification"),
        ("어르0002가 왜 넘어졌는지 단정해줘.", "clarification"),
    ],
)
def test_ai_help_routes_only_specialized_or_truly_ambiguous_questions_away_from_general(
    question,
    expected,
):
    """Catches a return-to-keyword-allowlisting regression in the default route."""
    assert classify_ai_help_question(question) == expected


def test_general_guidance_does_not_invent_facility_specific_information():
    bounded = apply_general_guidance_boundaries(
        "우리 시설의 급여종류는 뭐야?",
        "방문요양과 주야간보호를 운영합니다.",
    )
    assert "확인할 수 없습니다" in bounded
    assert "내부 등록정보" in bounded
    assert "방문요양과 주야간보호를 운영합니다" not in bounded


def test_general_guidance_requires_the_indicator_year_and_edition():
    bounded = apply_general_guidance_boundaries(
        "시설 평가지표 6번의 내용은 뭐야?",
        "평가지표 6번은 특정 항목입니다.",
    )
    assert "연도" in bounded
    assert "평가판본" in bounded
    assert "공식 평가매뉴얼" in bounded
    assert "특정 항목입니다" not in bounded
