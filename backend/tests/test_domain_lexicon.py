from app.domain_lexicon import (
    build_speech_context,
    hangul_similarity,
    load_long_term_care_lexicon,
    matching_situations,
    term_corrections,
)


def test_versioned_lexicon_has_required_terms_and_situations() -> None:
    lexicon = load_long_term_care_lexicon()
    canonical = {term.canonical for term in lexicon.terms}
    situation_ids = {situation.id for situation in lexicon.situations}

    assert lexicon.schema_version == 1
    assert {
        "송영",
        "기저귀",
        "손톱",
        "발톱",
        "체위변경",
        "뉴케어",
        "아침식사",
        "점심식사",
        "저녁식사",
    } <= canonical
    assert {"nail_hygiene", "elimination_care", "attendance_transport"} <= (
        situation_ids
    )


def test_speech_context_prioritizes_roster_and_domain_words_without_facts() -> None:
    pack = build_speech_context(
        resident_names=["가나다", "라마바", "가나다"],
        service_context="daycare",
    )

    assert pack.resident_names == ("가나다", "라마바")
    assert "주간보호 한국어 업무보고" in pack.initial_prompt
    assert "가나다" in pack.hotwords
    assert "기저귀" in pack.hotwords
    assert "사복" in pack.hotwords
    assert "뉴케어" in pack.hotwords
    assert "손톱깍이" not in pack.hotwords
    assert len(pack.initial_prompt) <= 6_000
    assert len(pack.hotwords) <= 4_000


def test_matching_situations_only_returns_evidence_triggered_phrases() -> None:
    matches = matching_situations("목욕 후 발톱이 길어 보였습니다")

    assert matches
    assert matches[0]["id"] == "nail_hygiene"
    assert "발톱" in matches[0]["matched_terms"]
    assert "새 사실은 추가하지 않음" in matches[0]["safety_rule"]
    assert matching_situations("특별한 변화 없음") == []


def test_hangul_jamo_similarity_and_confirmed_standard_spelling() -> None:
    corrections = dict(term_corrections())

    assert hangul_similarity("발통", "발톱") > 0.7
    assert corrections["손톱깍이"] == "손톱깎이"
    assert corrections["깍아드림"] == "깎아드림"
    assert corrections["사복"] == "사회복지사"
    assert corrections["아침 식사"] == "아침식사"
