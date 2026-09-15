from uuid import uuid4

from app.search_context import (
    isolate_search_entries_for_resident,
    resident_specific_search_text,
)


def _shared_entry(selected_id, other_id):
    message_id = uuid4()
    return message_id, {
        "number": 1,
        "message_id": message_id,
        "created_at": "2026-08-12T00:00:00+00:00",
        "sender": "시험 직원",
        "resident": "선택 대상",
        "resident_names": ["선택 대상", "다른 대상"],
        "resident_refs": [
            {"id": selected_id, "name": "선택 대상"},
            {"id": other_id, "name": "다른 대상"},
        ],
        "body": "선택 대상: 식사량을 확인함.\n다른 대상: 외부 진료를 다녀옴.",
        "comments": [
            "선택 대상: 다음 근무자가 식사량을 재확인해 주세요.",
            "다른 대상: 진료 기록을 확인해 주세요.",
            "담당자가 확인했습니다.",
        ],
        "attachment_text": (
            "[이미지 판독문 · report-one.jpg]\n"
            "선택 대상: 오전 활동에 참여함.\n"
            "다른 대상: 오후 활동을 쉬었음.\n\n"
            "[음성 받아쓰기 · report-two.wav]\n"
            "선택 대상: 저녁 식사를 완료함.\n"
            "다른 대상: 저녁 식사를 거부함."
        ),
        "source_label": "대화 · 이미지 판독문 · 음성 받아쓰기",
        "action_status": None,
    }


def test_selected_resident_context_excludes_other_paragraphs_from_all_sources():
    selected_id = uuid4()
    other_id = uuid4()
    message_id, entry = _shared_entry(selected_id, other_id)

    isolated = isolate_search_entries_for_resident(
        [entry],
        resident_id=selected_id,
    )

    assert len(isolated) == 1
    selected = isolated[0]
    assert selected["message_id"] == message_id
    assert selected["resident_refs"] == [{"id": selected_id, "name": "선택 대상"}]
    assert selected["resident_names"] == ["선택 대상"]
    assert "식사량을 확인" in selected["body"]
    assert "외부 진료" not in selected["body"]
    assert selected["comments"] == [
        "선택 대상: 다음 근무자가 식사량을 재확인해 주세요."
    ]
    assert "[이미지 판독문 · report-one.jpg]" in selected["attachment_text"]
    assert "오전 활동에 참여" in selected["attachment_text"]
    assert "오후 활동을 쉬었음" not in selected["attachment_text"]
    assert "[음성 받아쓰기 · report-two.wav]" in selected["attachment_text"]
    assert "저녁 식사를 완료" in selected["attachment_text"]
    assert "저녁 식사를 거부" not in selected["attachment_text"]


def test_unlinked_message_is_not_sent_for_selected_resident_summary():
    selected_id = uuid4()
    other_id = uuid4()
    _, entry = _shared_entry(selected_id, other_id)

    assert (
        isolate_search_entries_for_resident(
            [entry],
            resident_id=uuid4(),
        )
        == []
    )


def test_single_resident_keeps_reviewed_ocr_or_audio_text_unchanged():
    selected_id = uuid4()
    message_id = uuid4()
    entry = {
        "message_id": message_id,
        "resident_refs": [{"id": selected_id, "name": "선택 대상"}],
        "resident_names": ["선택 대상"],
        "body": "사진을 첨부했습니다.",
        "comments": [],
        "attachment_text": "[이미지 판독문 · checked.jpg]\n직원이 확정한 판독문",
    }

    isolated = isolate_search_entries_for_resident(
        [entry],
        resident_id=selected_id,
        known_resident_names=["선택 대상", "다른 대상"],
    )

    assert isolated[0]["message_id"] == message_id
    assert isolated[0]["attachment_text"] == entry["attachment_text"]
    assert isolated[0]["context_isolated"] is False


def test_single_confirmed_resident_still_excludes_other_active_resident_spans():
    selected_id = uuid4()
    message_id = uuid4()
    entry = {
        "message_id": message_id,
        "resident_refs": [{"id": selected_id, "name": "선택 대상"}],
        "resident_names": ["선택 대상"],
        "body": "선택 대상: 식사를 완료함.\n다른 대상: 식사를 거부함.",
        "comments": [
            "선택 대상: 다음 근무자가 수분량을 확인해 주세요.\n"
            "다른 대상: 진료 기록을 확인해 주세요."
        ],
        "attachment_text": (
            "[이미지 판독문 · checked.jpg]\n"
            "선택 대상: 오전 활동에 참여함.\n"
            "다른 대상: 오후 활동을 쉬었음."
        ),
    }

    isolated = isolate_search_entries_for_resident(
        [entry],
        resident_id=selected_id,
        known_resident_names=["선택 대상", "다른 대상"],
    )

    assert len(isolated) == 1
    selected = isolated[0]
    assert selected["message_id"] == message_id
    provider_text = "\n".join(
        [selected["body"], selected["attachment_text"], *selected["comments"]]
    )
    assert "식사를 완료" in provider_text
    assert "수분량을 확인" in provider_text
    assert "오전 활동에 참여" in provider_text
    assert "식사를 거부" not in provider_text
    assert "진료 기록" not in provider_text
    assert "오후 활동을 쉬었음" not in provider_text
    assert selected["context_isolated"] is True


def test_other_only_derived_text_does_not_keep_generic_attachment_entry():
    selected_id = uuid4()
    entry = {
        "message_id": uuid4(),
        "resident_refs": [{"id": selected_id, "name": "선택 대상"}],
        "resident_names": ["선택 대상"],
        "body": "사진을 첨부했습니다.",
        "comments": ["다른 대상: 진료 기록을 확인해 주세요."],
        "attachment_text": (
            "[이미지 판독문 · checked.jpg]\n다른 대상: 외부 진료를 다녀옴."
        ),
    }

    assert isolate_search_entries_for_resident(
        [entry],
        resident_id=selected_id,
        known_resident_names=["선택 대상", "다른 대상"],
    ) == []


def test_shared_generic_body_is_kept_but_unscoped_reply_is_not_assigned():
    selected_id = uuid4()
    other_id = uuid4()
    _, entry = _shared_entry(selected_id, other_id)
    entry["body"] = "두 분과 함께 안전교육을 진행했습니다."
    entry["comments"] = ["확인했습니다."]
    entry["attachment_text"] = ""

    isolated = isolate_search_entries_for_resident([entry], resident_id=selected_id)

    assert isolated[0]["body"] == "두 분과 함께 안전교육을 진행했습니다."
    assert isolated[0]["comments"] == []


def test_name_match_does_not_use_a_short_name_inside_a_longer_name_token():
    result = resident_specific_search_text(
        "가상하나: 선택 기록\n가상하나둘: 다른 기록",
        target_name="가상하나",
        resident_names=["가상하나", "가상하나둘"],
    )

    assert "선택 기록" in result
    assert "다른 기록" not in result


def test_name_match_accepts_ocr_text_without_space_before_honorific():
    result = resident_specific_search_text(
        "선택대상어르신: 오전 식사를 완료함.\n다른대상어르신: 식사를 거부함.",
        target_name="선택대상",
        resident_names=["선택대상", "다른대상"],
    )

    assert "오전 식사를 완료" in result
    assert "식사를 거부" not in result
