from dataclasses import replace

from app.finals_readiness import (
    FinalsReadinessSnapshot,
    _contains_sensitive_audit_key,
    endpoint_scope,
    evaluate_snapshot,
)


def ready_snapshot() -> FinalsReadinessSnapshot:
    return FinalsReadinessSnapshot(
        environment="development",
        reviewer_access_enabled=False,
        ai_review_external_enabled=False,
        allow_external_real_data=False,
        allow_external_real_image_data=False,
        ocr_endpoint_scope="internal",
        stt_enabled=True,
        stt_endpoint_scope="local",
        ai_review_endpoint_scope="internal",
        real_staff_count=0,
        real_resident_count=0,
        real_room_count=0,
        real_message_count=0,
        test_audit_count=12,
        sensitive_audit_event_count=0,
        active_test_processor_count=1,
        active_test_processor_test_room_link_count=2,
        active_test_processor_real_room_link_count=0,
        presentation_message_count=8,
        presentation_non_test_message_count=0,
        presentation_real_author_message_count=0,
        presentation_non_test_resident_count=0,
    )


def test_ready_snapshot_passes() -> None:
    report = evaluate_snapshot(ready_snapshot())

    assert report.overall == "PASS"
    assert all(finding.status == "PASS" for finding in report.findings)


def test_external_real_image_and_mixed_database_fail() -> None:
    report = evaluate_snapshot(
        replace(
            ready_snapshot(),
            allow_external_real_image_data=True,
            real_staff_count=3,
            real_message_count=5,
        )
    )

    failed_codes = {
        finding.code for finding in report.findings if finding.status == "FAIL"
    }
    assert report.overall == "FAIL"
    assert "EXTERNAL_REAL_DATA_BLOCKED" in failed_codes
    assert "ISOLATED_PSEUDONYM_DATASET" in failed_codes


def test_presentation_scope_rejects_real_links_and_authors() -> None:
    report = evaluate_snapshot(
        replace(
            ready_snapshot(),
            active_test_processor_real_room_link_count=1,
            presentation_real_author_message_count=2,
        )
    )

    failed_codes = {
        finding.code for finding in report.findings if finding.status == "FAIL"
    }
    assert "PRESENTATION_ROOM_BOUNDARY" in failed_codes
    assert "PRESENTATION_CONTENT_BOUNDARY" in failed_codes


def test_endpoint_scope_keeps_local_hosts_and_rejects_public_hosts() -> None:
    assert endpoint_scope("http://127.0.0.1:11434") == "local"
    assert endpoint_scope("http://host.docker.internal:8766") == "local"
    assert endpoint_scope("http://x370d:11434") == "internal"
    assert endpoint_scope("https://integrate.api.nvidia.com/v1") == "external"
    assert endpoint_scope("not-a-url") == "invalid"


def test_sensitive_audit_key_detection_never_needs_values() -> None:
    assert _contains_sensitive_audit_key({"safe": {"staff_name": "비공개"}})
    assert _contains_sensitive_audit_key([{"original_name": "비공개"}])
    assert not _contains_sensitive_audit_key(
        {"action": "message_opened", "counts": {"message": 2}}
    )
