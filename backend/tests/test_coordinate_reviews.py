from app.coordinate_reviews import (
    CoordinateLineLocation,
    CoordinatePlacementEvidence,
    build_coordinate_bootstrap_regions,
    coordinate_box_corners,
    coordinate_template_key,
    editable_coordinate_text,
    enrich_status_correction_pairs,
    prepare_coordinate_regions_for_editor,
)
from app.schemas import CoordinateBox, CoordinateRegion


def test_editor_removes_uncertainty_marker_but_keeps_review_metadata() -> None:
    assert editable_coordinate_text("가나다 (?)") == ("가나다", True)
    assert editable_coordinate_text("가나다 (?) · 라마바 (?)") == (
        "가나다 · 라마바",
        True,
    )
    assert editable_coordinate_text("확인") == ("확인", False)

    prepared = prepare_coordinate_regions_for_editor(
        [
            CoordinateRegion(
                client_id="candidate-1",
                bbox=CoordinateBox(left=0.1, top=0.1, width=0.3, height=0.1),
                raw_text="가다나",
                corrected_text="가나다 (?)",
                role="name",
            )
        ]
    )

    assert prepared[0].corrected_text == "가나다"
    assert prepared[0].review_required is True


def test_bootstrap_uses_real_normalized_bbox_without_changing_source_text() -> None:
    raw = "가상 이름\n식사량 확인"
    confirmed = "가상 이름\n식사량 확인 완료"

    regions = build_coordinate_bootstrap_regions(
        raw_text=raw,
        confirmed_text=confirmed,
        suggestion_details=[
            {
                "recognized": "가상 이름",
                "candidate": "가상 이름",
                "region_bbox_normalized": [100, 200, 400, 260],
            }
        ],
    )

    assert regions[0].bbox.model_dump() == {
        "left": 0.1,
        "top": 0.2,
        "width": 0.30000000000000004,
        "height": 0.06,
        "rotation_degrees": 0,
    }
    assert regions[0].source == "ocr_bbox"
    assert regions[0].raw_text == "가상 이름"
    assert regions[1].source == "legacy_alignment"
    assert regions[1].placement_status == "needs_position"
    assert regions[1].raw_text == "식사량 확인"
    assert regions[1].corrected_text == "식사량 확인 완료"
    assert raw == "가상 이름\n식사량 확인"
    assert confirmed == "가상 이름\n식사량 확인 완료"


def test_bootstrap_rejects_invalid_bbox_and_keeps_line_for_manual_placement() -> None:
    regions = build_coordinate_bootstrap_regions(
        raw_text="일반 문장",
        confirmed_text="직원이 고친 일반 문장",
        suggestion_details=[
            {
                "recognized": "일반 문장",
                "candidate": "임의 후보",
                "region_bbox_normalized": [900, 100, 1200, 150],
            }
        ],
    )

    assert len(regions) == 1
    assert regions[0].role == "general"
    assert regions[0].placement_status == "needs_position"
    assert regions[0].raw_text == "일반 문장"
    assert regions[0].corrected_text == "직원이 고친 일반 문장"


def test_bootstrap_places_existing_lines_from_auto_locator_without_rewriting() -> None:
    raw = "첫째 줄\n둘째 줄\n셋째 줄"
    confirmed = "첫째 줄\n직원이 고친 둘째 줄\n셋째 줄"

    regions = build_coordinate_bootstrap_regions(
        raw_text=raw,
        confirmed_text=confirmed,
        suggestion_details=[],
        auto_locations=[
            CoordinateLineLocation(
                line_index=1,
                bbox=CoordinateBox(left=0.1, top=0.2, width=0.5, height=0.05),
            ),
            CoordinateLineLocation(
                line_index=2,
                bbox=CoordinateBox(left=0.1, top=0.3, width=0.7, height=0.05),
            ),
        ],
    )

    assert regions[0].source == "auto_locator"
    assert regions[0].placement_status == "auto"
    assert regions[1].source == "auto_locator"
    assert regions[1].raw_text == "둘째 줄"
    assert regions[1].corrected_text == "직원이 고친 둘째 줄"
    assert regions[2].placement_status == "needs_position"
    assert raw == "첫째 줄\n둘째 줄\n셋째 줄"


def test_bootstrap_locations_follow_displayed_lines_when_review_inserts_a_line() -> (
    None
):
    regions = build_coordinate_bootstrap_regions(
        raw_text="첫째 줄\n셋째 줄",
        confirmed_text="첫째 줄\nOCR에서 빠진 둘째 줄\n셋째 줄",
        suggestion_details=[],
        auto_locations=[
            CoordinateLineLocation(
                line_index=2,
                bbox=CoordinateBox(left=0.2, top=0.3, width=0.5, height=0.05),
            )
        ],
    )

    assert len(regions) == 3
    assert regions[1].corrected_text == "OCR에서 빠진 둘째 줄"
    assert regions[1].source == "auto_locator"
    assert regions[1].bbox.top == 0.3
    assert regions[2].placement_status == "needs_position"


def test_confirmed_history_reuses_only_same_template_section_role_and_text() -> None:
    details = [
        {
            "kind": "versioned_ocr_pipeline",
            "matched": True,
            "profile_id": "synthetic.daily-check",
            "profile_version": "1.2.0",
        },
        {
            "kind": "versioned_name_slot",
            "line_index": 1,
            "section_id": "meal_name_list",
        },
    ]
    assert coordinate_template_key(details) == "synthetic.daily-check@1.2.0"
    evidence = [
        CoordinatePlacementEvidence(
            document_template="synthetic.daily-check@1.2.0",
            section_role="meal_name_list",
            role="name",
            corrected_text="가나다",
            bbox=CoordinateBox(left=0.62, top=0.18, width=0.16, height=0.05),
        )
    ]

    regions = build_coordinate_bootstrap_regions(
        raw_text="가나다\n일반 문장",
        confirmed_text="가나다\n일반 문장",
        suggestion_details=details,
        placement_evidence=evidence,
    )

    assert regions[0].role == "name"
    assert regions[0].section_role == "meal_name_list"
    assert regions[0].source == "confirmed_history"
    assert regions[0].bbox.left == 0.62
    assert regions[1].role == "general"
    assert regions[1].source == "legacy_alignment"
    assert regions[1].placement_status == "needs_position"


def test_confirmed_history_never_rewrites_or_reuses_conflicting_positions() -> None:
    details = [
        {
            "kind": "versioned_ocr_pipeline",
            "matched": True,
            "profile_id": "synthetic.daily-check",
            "profile_version": "1.2.0",
        },
        {
            "kind": "versioned_name_slot",
            "line_index": 1,
            "section_id": "meal_name_list",
        },
    ]
    evidence = [
        CoordinatePlacementEvidence(
            document_template="synthetic.daily-check@1.2.0",
            section_role="meal_name_list",
            role="name",
            corrected_text="가나다",
            bbox=CoordinateBox(left=0.1, top=0.1, width=0.2, height=0.05),
        ),
        CoordinatePlacementEvidence(
            document_template="synthetic.daily-check@1.2.0",
            section_role="meal_name_list",
            role="name",
            corrected_text="가나다",
            bbox=CoordinateBox(left=0.6, top=0.2, width=0.2, height=0.05),
        ),
    ]

    regions = build_coordinate_bootstrap_regions(
        raw_text="가나다",
        confirmed_text="가나다",
        suggestion_details=details,
        placement_evidence=evidence,
    )

    assert regions[0].raw_text == "가나다"
    assert regions[0].corrected_text == "가나다"
    assert regions[0].source == "legacy_alignment"
    assert regions[0].placement_status == "needs_position"


def test_coordinate_box_keeps_legacy_json_compatible_and_exposes_corners() -> None:
    box = CoordinateBox.model_validate(
        {"left": 0.2, "top": 0.3, "width": 0.4, "height": 0.2}
    )

    assert box.rotation_degrees == 0
    assert tuple(
        (round(x, 6), round(y, 6)) for x, y in coordinate_box_corners(box)
    ) == (
        (0.2, 0.3),
        (0.6, 0.3),
        (0.6, 0.5),
        (0.2, 0.5),
    )


def test_coordinate_box_accepts_in_bounds_rotation_and_rejects_corner_overflow() -> (
    None
):
    box = CoordinateBox(
        left=0.25,
        top=0.25,
        width=0.5,
        height=0.2,
        rotation_degrees=30,
    )
    assert box.rotation_degrees == 30
    assert len(coordinate_box_corners(box)) == 4

    try:
        CoordinateBox(
            left=0,
            top=0,
            width=0.3,
            height=0.1,
            rotation_degrees=15,
        )
    except ValueError as exc:
        assert "이미지 범위를 벗어났습니다" in str(exc)
    else:
        raise AssertionError("회전 모서리가 이미지 밖이면 거부되어야 합니다.")


def test_confirmed_history_treats_rotation_as_part_of_the_position() -> None:
    details = [
        {
            "kind": "versioned_ocr_pipeline",
            "matched": True,
            "profile_id": "synthetic.daily-check",
            "profile_version": "1.2.0",
        },
        {
            "kind": "versioned_name_slot",
            "line_index": 1,
            "section_id": "meal_name_list",
        },
    ]
    evidence = [
        CoordinatePlacementEvidence(
            document_template="synthetic.daily-check@1.2.0",
            section_role="meal_name_list",
            role="name",
            corrected_text="가나다",
            bbox=CoordinateBox(
                left=0.2,
                top=0.2,
                width=0.3,
                height=0.05,
                rotation_degrees=-4,
            ),
        ),
        CoordinatePlacementEvidence(
            document_template="synthetic.daily-check@1.2.0",
            section_role="meal_name_list",
            role="name",
            corrected_text="가나다",
            bbox=CoordinateBox(
                left=0.2,
                top=0.2,
                width=0.3,
                height=0.05,
                rotation_degrees=4,
            ),
        ),
    ]

    regions = build_coordinate_bootstrap_regions(
        raw_text="가나다",
        confirmed_text="가나다",
        suggestion_details=details,
        placement_evidence=evidence,
    )

    assert regions[0].source == "legacy_alignment"
    assert regions[0].placement_status == "needs_position"


def test_only_status_coordinate_edits_are_enriched_for_future_reuse() -> None:
    regions = [
        CoordinateRegion(
            client_id="status-1",
            bbox=CoordinateBox(left=0.1, top=0.1, width=0.3, height=0.08),
            raw_text="문안",
            corrected_text="확인",
            role="status",
            section_role="status_checklist",
            document_template="synthetic.daily-check@1.0.0",
        ),
        CoordinateRegion(
            client_id="general-1",
            bbox=CoordinateBox(left=0.1, top=0.3, width=0.5, height=0.08),
            raw_text="문안 상태를 기록함",
            corrected_text="확인 상태를 기록함",
            role="general",
            section_role="narrative",
        ),
    ]
    pairs = [
        {"recognized_text": "문안", "corrected_text": "확인"},
        {
            "recognized_text": "문안 상태를 기록함",
            "corrected_text": "확인 상태를 기록함",
        },
    ]

    enriched = enrich_status_correction_pairs(
        pairs,
        regions=regions,
        document_template="fallback-template@1.0.0",
    )

    assert enriched[0] == {
        "recognized_text": "문안",
        "corrected_text": "확인",
        "content_type": "status",
        "section": "status_checklist",
        "layout_role": "status_cell",
        "column_role": "status",
        "document_template": "synthetic.daily-check@1.0.0",
    }
    assert enriched[1] == pairs[1]
