from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import select

# Register the isolated AI tables before the application test lifespan calls
# Base.metadata.create_all(). The production integration point is documented by
# this module and does not require changing the shared models.py in this slice.
from app import ai_assist_models as _ai_assist_models  # noqa: F401
from app import models as _core_models  # noqa: F401
from app import ai_assist, codex_worker_client
from app import main as main_module
from app.ai_assist import (
    AiAssistRuntimeSettings,
    add_ai_turn,
    conversation_detail,
    create_ai_conversation,
    ensure_turn_shareable,
    process_ai_turn,
    process_ai_turn_background,
    recover_stale_ai_turns,
    share_ai_turn,
)
from app.ai_assist_models import (
    AiAssistProviderAttempt,
    AiAssistSource,
    AiAssistTurn,
)
from app.ai_assist_schemas import (
    AiAssistResult,
    AiAssistTaskRequest,
    AiEvidence,
)
from app.ai_provider_adapters import ProviderAdapterError
from app.ai_settings_store import default_ai_settings
from app.ai_system_schemas import AiRouteCandidate
from app.codex_worker_client import (
    CodexWorkerAnalysis,
    CodexWorkerClient,
    CodexWorkerHealth,
    CodexWorkerImage,
    CodexWorkerUnavailable,
    validate_loopback_base_url,
)
from app.database import SessionLocal
from app.local_ai import LocalAiError, RoomSummaryResult
from app.main import app
from app.models import (
    AttachmentTextExtraction,
    Message,
    MessageAttachment,
    MessageComment,
    Resident,
    Room,
    RoomMembership,
    Staff,
    User,
)


class UnavailableWorker:
    configured = True

    def analyze(self, **_kwargs):
        raise CodexWorkerUnavailable("시험용 연결 실패")


class SuccessfulWorker:
    configured = True

    def analyze(self, **_kwargs):
        return CodexWorkerAnalysis(
            result=AiAssistResult(
                answer="근거자료를 확인한 시험 답변입니다.",
                visible_text=None,
                evidence=[AiEvidence(source_no=1, statement="시험 원본 근거")],
                uncertainties=[],
                recommended_actions=["원문과 대조하세요."],
            ),
            provider="codex_cli",
            model="test-codex",
            prompt_version="test-v1",
            elapsed_ms=15,
        )


class NotConfiguredWorker:
    configured = False

    def analyze(self, **_kwargs):
        raise AssertionError("설정되지 않은 작업기를 호출하면 안 됩니다.")


class MustNotRunWorker:
    configured = True

    def analyze(self, **_kwargs):
        raise AssertionError("실제자료 차단 상태에서 작업기를 호출하면 안 됩니다.")


class RecordingImageWorker:
    configured = True

    def __init__(
        self,
        *,
        visible_text: str | None,
        answer: str = "이미지 내용을 확인했습니다.",
        uncertainties: list[str] | None = None,
        image_texts: list[dict] | None = None,
    ):
        self.visible_text = visible_text
        self.answer = answer
        self.uncertainties = uncertainties or []
        self.image_texts = image_texts or []
        self.calls: list[dict] = []

    def analyze(self, **kwargs):
        self.calls.append(kwargs)
        return CodexWorkerAnalysis(
            result=AiAssistResult(
                answer=self.answer,
                visible_text=self.visible_text,
                image_texts=self.image_texts,
                evidence=[AiEvidence(source_no=1, statement="시험 원본 근거")],
                uncertainties=self.uncertainties,
                recommended_actions=["원본과 이름을 담당자가 확인하세요."],
            ),
            provider="codex_cli",
            model="test-vision-model",
            prompt_version="test-image-v1",
            elapsed_ms=19,
        )


def _runtime(
    *,
    allow_external: bool,
    allow_external_image: bool = False,
) -> AiAssistRuntimeSettings:
    return AiAssistRuntimeSettings(
        allow_external_real_data=allow_external,
        allow_external_real_image_data=allow_external_image,
        worker_url="http://127.0.0.1:8767",
        worker_token="test-worker-token-that-is-longer-than-32-characters",
        worker_timeout_seconds=30,
    )


def _adaptive_plan_candidates() -> list[AiRouteCandidate]:
    return [
        AiRouteCandidate(
            provider="nvidia",
            model="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
            path="external",
            processing_location="external",
            external_transmission=True,
            role="primary",
            reason="비식별 외부 허용 이미지 빠른 1차 경로",
        ),
        AiRouteCandidate(
            provider="ollama",
            model="qwen3-vl:8b-instruct",
            path="internal",
            processing_location="internal",
            external_transmission=False,
            role="fallback",
            reason="민감·고신뢰 내부 이미지 경로",
        ),
        AiRouteCandidate(
            provider="rules",
            model="quick-period-summary-v1",
            path="rules",
            processing_location="rules",
            external_transmission=False,
            role="fallback",
            reason="실패폐쇄 규칙 경로",
        ),
    ]


def test_queued_turn_exposes_instant_rules_baseline_before_ai_enhancement():
    turn = SimpleNamespace(
        id=uuid4(),
        status="queued",
        task_type="summary",
        question="합성 기록을 정리해 주세요.",
        sources=[
            SimpleNamespace(
                ordinal=1,
                source_type="message",
                label="합성 시험 메시지",
                content_text="14:00 물 200mL 섭취 완료",
                source_meta={},
            )
        ],
        answer=None,
        visible_text=None,
        evidence=[],
        uncertainties=[],
        recommended_actions=[],
        result_payload=None,
        result_validated=False,
        provider=None,
        provider_model=None,
        prompt_version=None,
        elapsed_ms=None,
        error_code=None,
        error_message=None,
        contains_real_data=False,
        provider_attempts=[],
        created_at=ai_assist._now(),
        completed_at=None,
    )

    ai_assist._apply_instant_rules_baseline(turn)
    response = ai_assist.turn_response(turn)

    assert response.status == "queued"
    assert response.answer
    assert response.provider == "local_deterministic"
    assert response.processing_location == "rules"
    assert response.external_transmission is False
    assert response.enhancement_status == "pending"
    assert response.human_review_required is True
    assert turn.result_payload["instant_baseline"] is True


def test_summary_rules_baseline_extracts_completed_fact():
    result = ai_assist.deterministic_local_fallback(
        task_type="summary",
        sources=[
            ai_assist.AiAssistSourceSnapshot(
                source_no=1,
                source_type="message",
                label="합성 시설업무",
                text=(
                    "[BRIEFING-VERIFY-V1:general-facility-task] "
                    "합성 시설업무로 소화기 점검을 완료했습니다. "
                    "실제 어르신이나 운영 기록과 무관합니다."
                ),
            )
        ],
    )

    assert result.answer == (
        "원문에 완료로 기록된 내용: 합성 시설업무로 소화기 점검을 완료했습니다."
    )
    assert result.uncertainties == []
    assert "추가 확인" in result.recommended_actions[0]


def test_simple_completed_nonresident_summary_finishes_without_ai_attempt():
    with TestClient(app):
        with SessionLocal() as db:
            admin = db.scalar(select(User).where(User.username == "admin"))
            assert admin is not None and admin.staff is not None
            membership = db.scalar(
                select(RoomMembership).where(
                    RoomMembership.staff_id == admin.staff_id,
                    RoomMembership.left_at.is_(None),
                )
            )
            assert membership is not None
            message = Message(
                organization_id=admin.organization_id,
                room_id=membership.room_id,
                sender_id=admin.id,
                message_type="notice",
                body="합성 시설업무로 소화기 점검을 완료했습니다.",
                lifecycle_status="active",
                is_test_data=True,
            )
            db.add(message)
            db.commit()

            conversation = create_ai_conversation(
                db,
                admin,
                message.id,
                AiAssistTaskRequest(task_type="summary"),
                process_immediately=False,
            )
            turn = conversation.turns[0]
            attempts = list(
                db.scalars(
                    select(AiAssistProviderAttempt).where(
                        AiAssistProviderAttempt.turn_id == turn.id
                    )
                )
            )

            assert turn.status == "completed"
            assert turn.provider == "local_deterministic"
            assert turn.provider_model == "deterministic-rules-v1"
            assert turn.result_payload["fast_path"] == {
                "applied": True,
                "reason": "simple_completed_nonresident_summary",
                "ai_enhancement_skipped": True,
            }
            assert attempts == []
            assert "소화기 점검을 완료" in turn.answer

            care_message = Message(
                organization_id=admin.organization_id,
                room_id=membership.room_id,
                sender_id=admin.id,
                message_type="report",
                body=(
                    "합성 어르신의 혈압이 불명확해 간호팀 재측정과 후속 확인이 "
                    "예정되어 있습니다."
                ),
                lifecycle_status="active",
                is_test_data=True,
            )
            db.add(care_message)
            db.commit()
            care_conversation = create_ai_conversation(
                db,
                admin,
                care_message.id,
                AiAssistTaskRequest(task_type="summary"),
                process_immediately=False,
            )

            assert care_conversation.turns[0].status == "queued"
            assert "fast_path" not in (care_conversation.turns[0].result_payload or {})
            assert care_conversation.turns[0].answer.startswith(
                "원문에서 확인이 필요한 내용:"
            )


def _run_adaptive_image_route(monkeypatch, provider_results):
    attempts: list[SimpleNamespace] = []
    calls: list[str] = []
    source = SimpleNamespace(
        source_no=1,
        source_type="attachment",
        label="합성 이미지",
        text="14:00 물 200mL 섭취 완료, 16:00 상태 확인 예정",
        metadata={},
        included_in_prompt=False,
    )
    turn = SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        status="reasoning",
        task_type="image_text",
        question="합성 이미지의 글자를 읽어 주세요.",
        sources=[source],
    )

    class FakeDb:
        def commit(self):
            return None

    def begin_attempt(
        _db,
        _turn,
        *,
        provider,
        model,
        input_bytes,
        transmitted_external,
        attempt_status="started",
        error_code=None,
        error_message=None,
    ):
        attempt = SimpleNamespace(
            attempt_no=len(attempts) + 1,
            provider=provider,
            model_name=model,
            status=attempt_status,
            transmitted_external=transmitted_external,
            error_code=error_code,
            error_message=error_message,
            response_meta={},
        )
        attempts.append(attempt)
        return attempt

    def finish_attempt(
        _db,
        attempt,
        *,
        attempt_status,
        started,
        error_code=None,
        error_message=None,
        response_meta=None,
    ):
        attempt.status = attempt_status
        attempt.error_code = error_code
        attempt.error_message = error_message
        attempt.response_meta = response_meta or {}

    def generate(provider, *_args, **_kwargs):
        calls.append(provider)
        value = provider_results[provider]
        if isinstance(value, Exception):
            raise value
        return value, 10

    monkeypatch.setattr(ai_assist, "_processing_may_continue", lambda *_args: True)
    monkeypatch.setattr(ai_assist, "_begin_attempt", begin_attempt)
    monkeypatch.setattr(ai_assist, "_finish_attempt", finish_attempt)
    monkeypatch.setattr(ai_assist, "generate_ai_assist_result", generate)
    plan = SimpleNamespace(
        candidates=_adaptive_plan_candidates(),
        max_attempts=3,
        total_timeout_seconds=150,
    )
    analysis = ai_assist._execute_adaptive_ai_assist(
        FakeDb(),
        turn,
        settings_document=default_ai_settings(),
        plan=plan,
        sources=[source],
        images=[
            CodexWorkerImage(
                content=b"synthetic-image",
                mime_type="image/png",
                filename="synthetic.png",
                image_no=1,
            )
        ],
        input_bytes=123,
    )
    return analysis, attempts, calls


def test_adaptive_image_quality_failure_falls_back_once_to_qwen_vl(monkeypatch):
    qwen_result = AiAssistResult(
        answer="14:00 물 200mL 섭취 완료, 16:00 상태 확인 예정으로 판독했습니다.",
        visible_text="14:00 물 200mL 섭취 완료\n16:00 상태 확인 예정",
        evidence=[AiEvidence(source_no=1, statement="합성 이미지 판독")],
        uncertainties=[],
        recommended_actions=["원본 이미지를 사람이 확인하세요."],
    )
    analysis, attempts, calls = _run_adaptive_image_route(
        monkeypatch,
        {
            "nvidia": AiAssistResult(
                answer="확인",
                evidence=[],
                uncertainties=[],
                recommended_actions=[],
            ),
            "ollama": qwen_result,
        },
    )

    assert analysis is not None
    assert analysis.provider == "ollama"
    assert analysis.model == "qwen3-vl:8b-instruct"
    assert calls == ["nvidia", "ollama"]
    assert [attempt.status for attempt in attempts] == ["failed", "completed"]
    assert attempts[0].error_code == "invalid_response"


def test_adaptive_image_double_failure_ends_in_rules_without_retry_loop(monkeypatch):
    analysis, attempts, calls = _run_adaptive_image_route(
        monkeypatch,
        {
            "nvidia": ProviderAdapterError("timeout", "시험용 timeout"),
            "ollama": ProviderAdapterError(
                "provider_unavailable",
                "시험용 내부 경로 실패",
            ),
        },
    )

    assert analysis is not None
    assert analysis.provider == "local_deterministic"
    assert calls == ["nvidia", "ollama"]
    assert [attempt.status for attempt in attempts] == [
        "failed",
        "failed",
        "completed",
    ]
    assert [attempt.error_code for attempt in attempts[:2]] == [
        "timeout",
        "provider_unavailable",
    ]


def test_adaptive_image_placeholder_result_falls_through_to_rules(monkeypatch):
    analysis, attempts, calls = _run_adaptive_image_route(
        monkeypatch,
        {
            "nvidia": ProviderAdapterError("timeout", "시험용 timeout"),
            "ollama": AiAssistResult(
                answer="AiAssistResult",
                evidence=[AiEvidence(source_no=1, statement="합성 이미지")],
                uncertainties=[],
                recommended_actions=[],
            ),
        },
    )

    assert analysis is not None
    assert analysis.provider == "local_deterministic"
    assert calls == ["nvidia", "ollama"]
    assert [attempt.status for attempt in attempts] == [
        "failed",
        "failed",
        "completed",
    ]
    assert attempts[1].error_code == "invalid_response"


def test_image_rules_result_does_not_surface_internal_roster_sources():
    result = ai_assist.deterministic_local_fallback(
        task_type="image_text",
        sources=[
            ai_assist.AiAssistSourceSnapshot(
                source_no=1,
                source_type="message",
                label="합성 메시지",
                text="14:00 물 200mL 제공, 16:00 확인 예정",
            ),
            ai_assist.AiAssistSourceSnapshot(
                source_no=2,
                source_type="attachment",
                label="합성 이미지",
                text="14:00 WATER 200 ML, CHECK 16:00",
                metadata={"image_no": 1, "filename": "synthetic.png"},
            ),
            ai_assist.AiAssistSourceSnapshot(
                source_no=3,
                source_type="manual_context",
                label="현재 이용자 이름 명단",
                text="실제 명단 이름",
            ),
        ],
    )

    assert [item.source_no for item in result.evidence] == [1, 2]
    assert all("실제 명단 이름" not in item.statement for item in result.evidence)
    assert result.visible_text == "14:00 WATER 200 ML, CHECK 16:00"


def test_runtime_defaults_block_external_real_data(monkeypatch):
    monkeypatch.delenv("AI_ASSIST_ALLOW_EXTERNAL_REAL_DATA", raising=False)
    monkeypatch.delenv("CODEX_WORKER_SHARED_TOKEN", raising=False)

    runtime = AiAssistRuntimeSettings.from_env()

    assert runtime.allow_external_real_data is False
    assert runtime.build_worker_client().configured is False

    monkeypatch.setattr(ai_assist.app_settings, "ai_review_provider", "local")
    monkeypatch.setattr(
        ai_assist.app_settings,
        "ai_review_base_url",
        "http://localhost:11434",
    )
    assert ai_assist._local_ai_endpoint_is_safe() is True


def test_shared_image_result_prioritizes_a_long_original_reading():
    visible_text = "가" * 1200
    body = ai_assist._share_body(
        SimpleNamespace(
            visible_text=visible_text,
            answer="짧은 설명",
            uncertainties=["원본 확인 필요"],
            result_payload={"resident_name_candidates": []},
        )
    )

    assert len(body) <= 2000
    assert "가" * 900 in body
    assert "[AI 답변]" in body
    assert "[확인 필요]" in body


def test_legacy_carefor_resident_flag_is_not_treated_as_test_data():
    legacy_real_resident = SimpleNamespace(
        is_test_data=True,
        internal_code="SMCODI:carefor:daycare:legacy-id",
    )
    explicit_test_resident = SimpleNamespace(
        is_test_data=True,
        internal_code="AI-TEST-RESIDENT",
    )

    assert ai_assist._resident_is_explicit_test_data(legacy_real_resident) is False
    assert ai_assist._resident_is_explicit_test_data(explicit_test_resident) is True


def test_dev_real_staff_message_is_real_and_external_worker_is_blocked(monkeypatch):
    """A legacy DEV room flag must not make a real employee message exportable."""

    monkeypatch.setattr(ai_assist.app_settings, "ai_review_provider", "off")
    monkeypatch.setattr(main_module, "send_web_push_to_users", lambda *_a, **_k: 0)
    staff_id = None
    room_id = None
    original_staff_is_test = None
    original_room_is_test = None

    try:
        with TestClient(app) as client:
            login = client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "AdminPass!234"},
                headers={"origin": "http://testserver"},
            )
            assert login.status_code == 200, login.text

            with SessionLocal() as db:
                admin = db.scalar(select(User).where(User.username == "admin"))
                assert admin is not None and admin.staff is not None
                membership = db.scalar(
                    select(RoomMembership).where(
                        RoomMembership.staff_id == admin.staff_id,
                        RoomMembership.left_at.is_(None),
                    )
                )
                assert membership is not None
                staff_id = admin.staff.id
                room_id = membership.room_id
                original_staff_is_test = admin.staff.is_test_data
                original_room_is_test = membership.room.is_test_data

                # Old DEV rows can still carry room.is_test_data=True.  The
                # authenticated employee identity is the authoritative signal.
                admin.staff.is_test_data = False
                membership.room.is_test_data = True
                db.commit()

            sent = client.post(
                f"/api/rooms/{room_id}/messages",
                json={"body": "실제 직원 업무자료 분류 회귀시험"},
                headers={"origin": "http://testserver"},
            )
            assert sent.status_code == 201, sent.text

            with SessionLocal() as db:
                admin = db.scalar(select(User).where(User.username == "admin"))
                message = db.get(Message, sent.json()["id"])
                assert admin is not None
                assert message is not None
                assert message.is_test_data is False
                message.is_test_data = True
                assert (
                    main_module._interaction_is_test_data(admin, message=message)
                    is False
                )
                message.is_test_data = False
                db.flush()

                conversation = create_ai_conversation(
                    db,
                    admin,
                    message.id,
                    AiAssistTaskRequest(task_type="summary"),
                    runtime=_runtime(allow_external=False),
                    worker_client=MustNotRunWorker(),
                )
                turn = conversation.turns[0]
                assert turn.contains_real_data is True
                attempts = list(
                    db.scalars(
                        select(AiAssistProviderAttempt)
                        .where(AiAssistProviderAttempt.turn_id == turn.id)
                        .order_by(AiAssistProviderAttempt.attempt_no)
                    )
                )
                assert attempts[0].status == "skipped"
                assert attempts[0].error_code == "external_real_data_disabled"
                assert all(not attempt.transmitted_external for attempt in attempts)
    finally:
        if staff_id is not None and room_id is not None:
            with SessionLocal() as db:
                staff = db.get(Staff, staff_id)
                room = db.get(_core_models.Room, room_id)
                if staff is not None and original_staff_is_test is not None:
                    staff.is_test_data = original_staff_is_test
                if room is not None and original_room_is_test is not None:
                    room.is_test_data = original_room_is_test
                db.commit()


def test_codex_client_only_accepts_loopback_and_validates_schema():
    with pytest.raises(ValueError):
        validate_loopback_base_url("https://example.com:8767")
    with pytest.raises(ValueError):
        validate_loopback_base_url("http://192.0.2.10:8767")
    assert validate_loopback_base_url(
        "http://host.docker.internal:8767"
    ) == "http://host.docker.internal:8767"
    assert validate_loopback_base_url(
        "http://codex-worker:8767"
    ) == "http://codex-worker:8767"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        assert request.headers["X-Codex-Worker-Token"].startswith("test-worker")
        if request.url.path == "/health":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "service": "mesil-local-codex-worker",
                    "credential_ready": True,
                    "credential_mode": "test",
                    "busy": False,
                },
            )
        payload = json.loads(request.content)
        assert payload["mode"] == "question"
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "answer": "시험 답변",
                    "visible_text": None,
                    "evidence": [{"source_no": 1, "statement": "시험 근거"}],
                    "uncertainties": [],
                    "recommended_actions": [],
                },
                "provider": "codex_cli",
                "model": "test-codex",
                "prompt_version": "test-v1",
                "elapsed_ms": 12,
            },
        )

    client = CodexWorkerClient(
        base_url="http://127.0.0.1:8767",
        shared_token="test-worker-token-that-is-longer-than-32-characters",
        timeout_seconds=30,
        transport=httpx.MockTransport(handler),
    )
    health = client.health()
    assert health.credential_ready is True
    assert health.credential_mode == "test"
    assert health.busy is False
    analysis = client.analyze(
        task_type="question",
        question="시험 질문",
        sources=[
            ai_assist.AiAssistSourceSnapshot(
                source_no=1,
                source_type="manual_context",
                label="시험",
                text="시험 근거",
            )
        ],
    )

    assert analysis.result.answer == "시험 답변"
    assert analysis.model == "test-codex"


def test_codex_client_sends_ordered_multi_image_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert "image" not in payload
        assert [item["image_no"] for item in payload["images"]] == [1, 2]
        assert [item["filename"] for item in payload["images"]] == [
            "첫 장.png",
            "둘째 장.jpg",
        ]
        assert [item["mime_type"] for item in payload["images"]] == [
            "image/png",
            "image/jpeg",
        ]
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "answer": "두 이미지를 확인했습니다.",
                    "visible_text": "[이미지 1 · 첫 장.png]\n첫 장",
                    "image_texts": [
                        {"image_no": 1, "filename": "첫 장.png", "text": "첫 장"},
                        {"image_no": 2, "filename": "둘째 장.jpg", "text": "둘째 장"},
                    ],
                    "evidence": [],
                    "uncertainties": [],
                    "recommended_actions": [],
                },
                "provider": "codex_cli",
                "model": "test-codex",
                "prompt_version": "test-v1",
                "elapsed_ms": 12,
            },
        )

    client = CodexWorkerClient(
        base_url="http://127.0.0.1:8767",
        shared_token="test-worker-token-that-is-longer-than-32-characters",
        timeout_seconds=30,
        transport=httpx.MockTransport(handler),
    )
    result = client.analyze(
        task_type="image_text",
        question="두 장을 읽어 주세요.",
        sources=[],
        images=[
            CodexWorkerImage(b"png", "image/png", "첫 장.png", 1),
            CodexWorkerImage(b"jpeg", "image/jpeg", "둘째 장.jpg", 2),
        ],
    )

    assert [item.image_no for item in result.result.image_texts] == [1, 2]


def test_codex_client_waits_for_busy_worker(monkeypatch: pytest.MonkeyPatch):
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(
                429,
                json={"ok": False, "code": "worker_busy"},
            )
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "answer": "시험 답변",
                    "visible_text": None,
                    "image_texts": [],
                    "evidence": [],
                    "uncertainties": [],
                    "recommended_actions": [],
                },
                "provider": "codex_cli",
                "model": "test-codex",
                "prompt_version": "test-v1",
                "elapsed_ms": 12,
            },
        )

    monkeypatch.setattr(codex_worker_client, "sleep", lambda _seconds: None)
    client = CodexWorkerClient(
        base_url="http://127.0.0.1:8767",
        shared_token="test-worker-token-that-is-longer-than-32-characters",
        timeout_seconds=30,
        transport=httpx.MockTransport(handler),
    )

    result = client.analyze(
        task_type="question",
        question="시험 질문",
        sources=[],
    )

    assert calls == 3
    assert result.result.answer == "시험 답변"


@pytest.mark.parametrize("image_numbers", [[1], [1, 1], [1, 3]])
def test_multi_image_result_rejects_missing_duplicate_and_out_of_range_numbers(
    image_numbers,
):
    analysis = CodexWorkerAnalysis(
        result=AiAssistResult(
            answer="불완전 시험 결과",
            visible_text="불완전 판독",
            image_texts=[
                {
                    "image_no": image_no,
                    "filename": f"{index}.png",
                    "text": f"{index}번 판독",
                }
                for index, image_no in enumerate(image_numbers, 1)
            ],
            evidence=[],
            uncertainties=[],
            recommended_actions=[],
        ),
        provider="codex_cli",
        model="test-codex",
        prompt_version="test-v2",
        elapsed_ms=1,
    )

    with pytest.raises(ai_assist.IncompleteMultiImageResult):
        ai_assist._validate_worker_image_texts(
            analysis,
            task_type="image_text",
            expected_image_count=2,
        )


def test_multi_image_completeness_guard_preserves_single_and_explain_compatibility():
    analysis = CodexWorkerAnalysis(
        result=AiAssistResult(
            answer="기존 호환 결과",
            visible_text="단일 이미지 판독",
            image_texts=[],
            evidence=[],
            uncertainties=[],
            recommended_actions=[],
        ),
        provider="codex_cli",
        model="test-codex",
        prompt_version="test-v1",
        elapsed_ms=1,
    )

    ai_assist._validate_worker_image_texts(
        analysis,
        task_type="image_text",
        expected_image_count=1,
    )
    ai_assist._validate_worker_image_texts(
        analysis,
        task_type="image_explain",
        expected_image_count=2,
    )


def test_image_text_automatically_analyzes_all_message_images_and_persists_each(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(ai_assist.app_settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr(ai_assist.app_settings, "ai_review_provider", "off")
    monkeypatch.setattr(ai_assist, "_local_ocr_endpoint_is_safe", lambda: True)
    monkeypatch.setattr(
        ai_assist,
        "extract_report_text",
        lambda *_args, **_kwargs: "같은 AI 작업 중 생성된 임시 로컬 판독",
    )

    with TestClient(app):
        with SessionLocal() as db:
            admin = db.scalar(select(User).where(User.username == "admin"))
            assert admin is not None and admin.staff_id is not None
            membership = db.scalar(
                select(RoomMembership).where(
                    RoomMembership.staff_id == admin.staff_id
                )
            )
            assert membership is not None
            message = Message(
                organization_id=admin.organization_id,
                room_id=membership.room_id,
                sender_id=admin.id,
                message_type="file",
                body="두 장짜리 보고서",
                lifecycle_status="active",
                is_test_data=False,
            )
            db.add(message)
            db.flush()
            created_at = ai_assist._now()
            first_content = b"first-image"
            second_content = b"second-image"
            first = MessageAttachment(
                organization_id=admin.organization_id,
                message_id=message.id,
                uploader_id=admin.id,
                storage_key=f"multi-first-{uuid4().hex}.png",
                original_name="보고서 앞면.png",
                mime_type="image/png",
                size_bytes=len(first_content),
                created_at=created_at,
            )
            second = MessageAttachment(
                organization_id=admin.organization_id,
                message_id=message.id,
                uploader_id=admin.id,
                storage_key=f"multi-second-{uuid4().hex}.jpg",
                original_name="보고서 뒷면.jpg",
                mime_type="image/jpeg",
                size_bytes=len(second_content),
                created_at=created_at + timedelta(milliseconds=1),
            )
            db.add_all([first, second])
            db.flush()
            first_stub_text = "개발용 최초 자동 판독"
            db.add(
                AttachmentTextExtraction(
                    organization_id=admin.organization_id,
                    attachment_id=first.id,
                    status="completed",
                    provider="stub",
                    model_name="dev-stub",
                    extracted_text=first_stub_text,
                    original_extracted_text=first_stub_text,
                    reviewed_text=None,
                    requested_by_id=admin.id,
                    completed_at=created_at,
                )
            )
            db.commit()
            (tmp_path / first.storage_key).write_bytes(first_content)
            (tmp_path / second.storage_key).write_bytes(second_content)

            worker = RecordingImageWorker(
                visible_text="worker combined text is normalized by backend",
                image_texts=[
                    {
                        "image_no": 1,
                        "filename": "보고서 앞면.png",
                        "text": "앞면 판독문",
                    },
                    {
                        "image_no": 2,
                        "filename": "보고서 뒷면.jpg",
                        "text": "뒷면 판독문",
                    },
                ],
            )
            conversation = create_ai_conversation(
                db,
                admin,
                message.id,
                AiAssistTaskRequest(
                    task_type="image_text",
                    # Even when the UI sends one anchor, every image in this
                    # message must be included automatically.
                    attachment_id=second.id,
                ),
                runtime=_runtime(
                    allow_external=False,
                    allow_external_image=True,
                ),
                worker_client=worker,
            )

            # Real care images never cross the external worker gate, even when
            # the legacy image exception flag is present. Ordered attachment
            # loading remains independently covered without transmitting it.
            assert len(worker.calls) == 0
            images = ai_assist._load_image_attachments([first, second])
            assert [item.image_no for item in images] == [1, 2]
            assert [item.filename for item in images] == [
                "보고서 앞면.png",
                "보고서 뒷면.jpg",
            ]
            assert [item.content for item in images] == [
                first_content,
                second_content,
            ]
            response = conversation_detail(db, admin, conversation.id).turns[0]
            assert response.visible_text == (
                "[이미지 1 · 보고서 앞면.png]\n개발용 최초 자동 판독\n\n"
                "[이미지 2 · 보고서 뒷면.jpg]\n"
                "같은 AI 작업 중 생성된 임시 로컬 판독"
            )
            attempts = list(
                db.scalars(
                    select(AiAssistProviderAttempt).where(
                        AiAssistProviderAttempt.turn_id == conversation.turns[0].id
                    )
                )
            )
            assert attempts[0].error_code == "external_real_data_disabled"
            assert all(not attempt.transmitted_external for attempt in attempts)
            first_extraction = db.scalar(
                select(AttachmentTextExtraction).where(
                    AttachmentTextExtraction.attachment_id == first.id
                )
            )
            second_extraction = db.scalar(
                select(AttachmentTextExtraction).where(
                    AttachmentTextExtraction.attachment_id == second.id
                )
            )
            assert first_extraction is not None
            assert second_extraction is not None
            assert first_extraction.extracted_text == first_stub_text
            assert second_extraction.extracted_text == "같은 AI 작업 중 생성된 임시 로컬 판독"
            assert first_extraction.original_extracted_text == first_stub_text
            assert second_extraction.original_extracted_text == "같은 AI 작업 중 생성된 임시 로컬 판독"
            assert first_extraction.status == "completed"
            assert second_extraction.status == "completed"
            assert first_extraction.completed_at > created_at

            # A local fallback may complete only when it has separate text for
            # every image. One missing extraction must remain a retryable fail.
            second_extraction.status = "failed"
            second_extraction.extracted_text = None
            second_extraction.original_extracted_text = None
            second_extraction.reviewed_text = None
            db.commit()
            fallback_conversation = create_ai_conversation(
                db,
                admin,
                message.id,
                AiAssistTaskRequest(
                    task_type="image_text",
                    attachment_id=second.id,
                ),
                runtime=_runtime(
                    allow_external=False,
                    allow_external_image=True,
                ),
                worker_client=NotConfiguredWorker(),
            )
            fallback_turn = fallback_conversation.turns[0]
            assert fallback_turn.status == "failed"
            assert fallback_turn.result_validated is False
            assert fallback_turn.error_code == "incomplete_multi_image_result"


def test_user_confirmed_deidentified_image_omits_real_rosters_but_is_auditable():
    with TestClient(app):
        with SessionLocal() as db:
            admin = db.scalar(select(User).where(User.username == "admin"))
            assert admin is not None and admin.staff_id is not None
            membership = db.scalar(
                select(RoomMembership).where(
                    RoomMembership.staff_id == admin.staff_id,
                    RoomMembership.left_at.is_(None),
                )
            )
            assert membership is not None
            message = Message(
                organization_id=admin.organization_id,
                room_id=membership.room_id,
                sender_id=admin.id,
                message_type="file",
                body="합성 비식별 이미지 시험",
                lifecycle_status="active",
                is_test_data=False,
            )
            db.add(message)
            db.flush()
            attachment = MessageAttachment(
                organization_id=admin.organization_id,
                message_id=message.id,
                uploader_id=admin.id,
                storage_key=f"confirmed-deidentified-{uuid4().hex}.png",
                original_name="synthetic.png",
                mime_type="image/png",
                size_bytes=16,
            )
            db.add(attachment)
            db.flush()
            db.add(
                AttachmentTextExtraction(
                    organization_id=admin.organization_id,
                    attachment_id=attachment.id,
                    status="completed",
                    provider="stub",
                    model_name="dev-stub",
                    extracted_text="TIME 14:00 WATER 200 ML FOLLOW-UP PENDING",
                    original_extracted_text=(
                        "TIME 14:00 WATER 200 ML FOLLOW-UP PENDING"
                    ),
                    requested_by_id=admin.id,
                    completed_at=ai_assist._now(),
                )
            )
            db.commit()

            conversation = create_ai_conversation(
                db,
                admin,
                message.id,
                AiAssistTaskRequest(
                    task_type="image_text",
                    attachment_id=attachment.id,
                    deidentified_confirmed_by_user=True,
                ),
                process_immediately=False,
            )
            turn = conversation.turns[0]
            assert turn.contains_real_data is False
            assert all(
                source.label
                not in {"현재 이용자 이름 명단", "현재 재직·휴직 직원 이름 명단"}
                for source in turn.sources
            )
            message_source = next(
                source for source in turn.sources if source.source_type == "message"
            )
            assert (
                message_source.source_meta["deidentified_confirmed_by_user"]
                is True
            )
            assert (
                message_source.source_meta["service_context_source"]
                == "user_deidentified_confirmation"
            )
            assert message_source.source_meta["service_context_notice"] == (
                "비식별 승인 자료이므로 실제 이용자·직원 명단은 포함하지 않았습니다."
            )


def test_share_guard_rejects_incomplete_results():
    turn = AiAssistTurn(
        organization_id=uuid4(),
        conversation_id=uuid4(),
        requested_by_user_id=uuid4(),
        sequence_no=1,
        task_type="summary",
        question="시험",
        contains_real_data=True,
        status="validating",
        result_validated=False,
    )

    with pytest.raises(HTTPException) as error:
        ensure_turn_shareable(turn)

    assert error.value.status_code == 409


def test_ai_service_enforces_owner_room_gate_fallback_primary_and_share(monkeypatch):
    observed_statuses: list[str] = []
    local_external_flags: list[bool] = []
    original_transition = ai_assist.transition_ai_turn

    def recording_transition(db, turn, new_status):
        observed_statuses.append(new_status)
        return original_transition(db, turn, new_status)

    def successful_local_summary(*, entries, external_allowed=False, purpose=None):
        del purpose
        local_external_flags.append(external_allowed)
        assert entries[0]["request"] == "원본 메시지와 근거자료를 요약해 주세요."
        assert "낙상 위험" in entries[0]["body"]
        return RoomSummaryResult(
            summary="로컬 모델이 원문 근거를 정리했습니다. [1]",
            provider="ollama",
            model="test-local-model",
            elapsed_ms=7,
        )

    monkeypatch.setattr(ai_assist, "transition_ai_turn", recording_transition)
    monkeypatch.setattr(
        ai_assist,
        "summarize_room_messages",
        successful_local_summary,
    )
    # 구 설정에서 외부 AI가 켜져 있어도 새 AI Assist 허용값이 false이면
    # 첫 로컬 경로가 Nemotron을 절대 열지 않는지 함께 검증합니다.
    monkeypatch.setattr(ai_assist.app_settings, "ai_review_provider", "chain")
    monkeypatch.setattr(ai_assist.app_settings, "ai_review_external_enabled", True)

    with TestClient(app):
        with SessionLocal() as db:
            admin = db.scalar(select(User).where(User.username == "admin"))
            assert admin is not None and admin.staff_id is not None
            membership = db.scalar(
                select(RoomMembership).where(
                    RoomMembership.staff_id == admin.staff_id,
                    RoomMembership.left_at.is_(None),
                )
            )
            assert membership is not None
            message = Message(
                organization_id=admin.organization_id,
                room_id=membership.room_id,
                sender_id=admin.id,
                message_type="chat",
                body="낙상 위험 확인이 필요한 시험 원본 메시지",
                lifecycle_status="active",
                is_test_data=True,
            )
            db.add(message)
            db.commit()
            db.refresh(message)

            outsider_staff = Staff(
                organization_id=admin.organization_id,
                internal_code=f"ai-outsider-{uuid4().hex[:8]}",
                display_name="AI 권한 시험 직원",
                job_title="시험",
                employment_status="active",
                is_test_data=True,
                is_active=True,
            )
            db.add(outsider_staff)
            db.flush()
            outsider = User(
                organization_id=admin.organization_id,
                staff_id=outsider_staff.id,
                username=f"ai-outsider-{uuid4().hex[:8]}",
                display_name="AI 권한 시험 직원",
                password_hash="test-only-password-hash",
                is_active=True,
            )
            db.add(outsider)
            db.commit()

            with pytest.raises(HTTPException) as room_error:
                create_ai_conversation(
                    db,
                    outsider,
                    message.id,
                    AiAssistTaskRequest(task_type="summary"),
                    runtime=_runtime(allow_external=False),
                    worker_client=UnavailableWorker(),
                )
            assert room_error.value.status_code == 403

            conversation = create_ai_conversation(
                db,
                admin,
                message.id,
                AiAssistTaskRequest(task_type="summary"),
                runtime=_runtime(allow_external=False),
                worker_client=UnavailableWorker(),
            )
            detail = conversation_detail(db, admin, conversation.id)
            assert detail.status == "completed"
            assert detail.turns[0].provider == "ollama"
            assert detail.turns[0].model == "test-local-model"
            assert local_external_flags == [False]
            assert observed_statuses[:6] == [
                "preparing",
                "extracting",
                "searching",
                "reasoning",
                "validating",
                "completed",
            ]
            first_turn = db.get(AiAssistTurn, detail.turns[0].id)
            attempts = list(
                db.scalars(
                    select(AiAssistProviderAttempt)
                    .where(AiAssistProviderAttempt.turn_id == first_turn.id)
                    .order_by(AiAssistProviderAttempt.attempt_no)
                )
            )
            # 시험자료는 실제자료 허용 플래그가 꺼져 있어도 Codex 작업기를
            # 시험할 수 있으므로, 연결 실패 뒤 로컬 경로로 안전하게 전환됩니다.
            assert first_turn.contains_real_data is False
            assert [item.status for item in attempts] == ["failed", "completed"]
            assert [item.transmitted_external for item in attempts] == [True, False]

            with pytest.raises(HTTPException) as owner_error:
                conversation_detail(db, outsider, conversation.id)
            assert owner_error.value.status_code == 403

            shared = share_ai_turn(db, admin, first_turn.id)
            shared_again = share_ai_turn(db, admin, first_turn.id)
            assert shared.created is True
            assert shared_again.created is False
            assert shared_again.message_id == shared.message_id
            shared_message = db.get(Message, shared.message_id)
            assert shared_message is not None
            assert "직원 확인 필요" in shared_message.body
            assert shared_message.extra_data["ai_assist"]["result_validated"] is True

            successful = add_ai_turn(
                db,
                admin,
                conversation.id,
                AiAssistTaskRequest(task_type="question", question="핵심 위험은?"),
                runtime=_runtime(allow_external=True),
                worker_client=SuccessfulWorker(),
            )
            successful_detail = conversation_detail(db, admin, successful.id)
            latest = successful_detail.turns[-1]
            assert latest.provider == "codex_cli"
            assert latest.model == "test-codex"
            assert latest.answer == "근거자료를 확인한 시험 답변입니다."
            latest_turn = db.get(AiAssistTurn, latest.id)
            latest_attempts = list(
                db.scalars(
                    select(AiAssistProviderAttempt).where(
                        AiAssistProviderAttempt.turn_id == latest_turn.id
                    )
                )
            )
            assert len(latest_attempts) == 1
            assert latest_attempts[0].status == "completed"
            assert latest_attempts[0].transmitted_external is True
            context_sources = list(
                db.scalars(
                    select(AiAssistSource)
                    .where(
                        AiAssistSource.turn_id == latest_turn.id,
                        AiAssistSource.source_type == "manual_context",
                    )
                    .order_by(AiAssistSource.ordinal)
                )
            )
            assert context_sources
            assert "이전 질문" in context_sources[0].content_text
            assert "로컬 모델이 원문 근거를 정리했습니다" in (
                context_sources[0].content_text or ""
            )

            def unavailable_local_summary(
                *, entries, external_allowed=False, purpose=None
            ):
                del entries, purpose
                local_external_flags.append(external_allowed)
                raise LocalAiError("시험용 로컬 AI 연결 실패")

            monkeypatch.setattr(
                ai_assist,
                "summarize_room_messages",
                unavailable_local_summary,
            )

            unavailable = add_ai_turn(
                db,
                admin,
                conversation.id,
                AiAssistTaskRequest(task_type="risk_check"),
                runtime=_runtime(allow_external=True),
                worker_client=UnavailableWorker(),
            )
            unavailable_detail = conversation_detail(db, admin, unavailable.id)
            assert unavailable_detail.turns[-1].provider == "local_deterministic"
            failed_attempts = list(
                db.scalars(
                    select(AiAssistProviderAttempt)
                    .where(
                        AiAssistProviderAttempt.turn_id
                        == unavailable_detail.turns[-1].id
                    )
                    .order_by(AiAssistProviderAttempt.attempt_no)
                )
            )
            assert [item.status for item in failed_attempts] == [
                "failed",
                "failed",
                "failed",
                "completed",
            ]
            assert [item.transmitted_external for item in failed_attempts] == [
                True,
                False,
                True,
                False,
            ]
            assert local_external_flags == [False, False, True]

            queued = create_ai_conversation(
                db,
                admin,
                message.id,
                AiAssistTaskRequest(task_type="summary"),
                process_immediately=False,
            )
            queued_detail = conversation_detail(db, admin, queued.id)
            assert queued_detail.status == "queued"
            queued_turn_id = queued_detail.turns[0].id

        process_ai_turn_background(queued_turn_id)

        with SessionLocal() as verification_db:
            verified_admin = verification_db.scalar(
                select(User).where(User.username == "admin")
            )
            background_detail = conversation_detail(
                verification_db,
                verified_admin,
                queued.id,
            )
            assert background_detail.status == "completed"
            assert background_detail.turns[0].provider == "local_deterministic"
            assert local_external_flags == [False, False, True, False]


def test_confirmed_record_history_requires_record_permission(monkeypatch):
    imported = 0

    def fake_import(*_args, **_kwargs):
        nonlocal imported
        imported += 1
        return SimpleNamespace(
            search_confirmed_records_for_ai=lambda **_search_kwargs: []
        )

    monkeypatch.setattr(ai_assist.importlib, "import_module", fake_import)
    turn = SimpleNamespace(organization_id=uuid4(), question="낙상 이력", sources=[])
    conversation = SimpleNamespace(room_id=uuid4(), message_id=uuid4())

    ordinary = SimpleNamespace(role="staff", can_process_records=False)
    ai_assist._append_optional_confirmed_record_sources(
        None,
        turn,
        conversation,
        ordinary,
    )
    assert imported == 0

    processor = SimpleNamespace(role="staff", can_process_records=True)
    ai_assist._append_optional_confirmed_record_sources(
        None,
        turn,
        conversation,
        processor,
    )
    assert imported == 1

    admin = SimpleNamespace(role="admin", can_process_records=False)
    ai_assist._append_optional_confirmed_record_sources(
        None,
        turn,
        conversation,
        admin,
    )
    assert imported == 2


def test_ordinary_staff_cannot_open_or_create_ai_confirmation():
    temporary_password = "StaffTemp!234"
    final_password = "StaffFinal!567"
    username = f"staff-ai-{uuid4().hex[:8]}"

    with TestClient(app) as admin_client:
        login = admin_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "AdminPass!234"},
            headers={"origin": "http://testserver"},
        )
        assert login.status_code == 200, login.text
        created = admin_client.post(
            "/api/employees",
            json={
                "username": username,
                "full_name": "AI확인 일반직원",
                "password": temporary_password,
                "role": "staff",
                "can_process_records": False,
                "job_code": "caregiver",
            },
            headers={"origin": "http://testserver"},
        )
        assert created.status_code == 201, created.text
        staff_id = created.json()["id"]

        with SessionLocal() as db:
            staff = db.get(User, staff_id)
            assert staff is not None and staff.staff_id is not None
            self_membership = db.scalar(
                select(RoomMembership)
                .join(Room, Room.id == RoomMembership.room_id)
                .where(
                    RoomMembership.staff_id == staff.staff_id,
                    RoomMembership.left_at.is_(None),
                    Room.kind == "self",
                )
            )
            assert self_membership is not None
            message = Message(
                organization_id=staff.organization_id,
                room_id=self_membership.room_id,
                sender_id=staff.id,
                message_type="chat",
                body="일반 직원 AI 확인 권한 차단 시험",
                lifecycle_status="active",
                is_test_data=True,
            )
            db.add(message)
            db.flush()
            attachment = MessageAttachment(
                organization_id=staff.organization_id,
                message_id=message.id,
                uploader_id=staff.id,
                storage_key=f"test/{uuid4().hex}.jpg",
                original_name="ordinary-staff-review.jpg",
                mime_type="image/jpeg",
                size_bytes=128,
                upload_ordinal=0,
            )
            db.add(attachment)
            db.flush()
            db.add(
                AttachmentTextExtraction(
                    organization_id=staff.organization_id,
                    attachment_id=attachment.id,
                    status="reviewed",
                    provider="test-private-ocr",
                    model_name="test-private-model",
                    extracted_text="직원에게 노출되면 안 되는 최초 판독문",
                    original_extracted_text="직원에게 노출되면 안 되는 원본 판독문",
                    suggested_text="직원에게 노출되면 안 되는 교정 제안",
                    reviewed_text="관리자가 확인한 최종 판독문",
                    error_message="직원에게 노출되면 안 되는 기술 오류",
                    requested_by_id=staff.id,
                    reviewed_by_id=staff.id,
                )
            )
            db.add(
                MessageComment(
                    organization_id=staff.organization_id,
                    message_id=message.id,
                    author_id=staff.id,
                    body="일반 직원에게 노출되면 안 되는 관리자 코멘트",
                    is_test_data=True,
                )
            )
            db.commit()
            db.refresh(message)
            message_id = message.id

    with TestClient(app) as staff_client:
        login = staff_client.post(
            "/api/auth/login",
            json={"username": username, "password": temporary_password},
            headers={"origin": "http://testserver"},
        )
        assert login.status_code == 200, login.text
        changed = staff_client.post(
            "/api/auth/password",
            json={
                "current_password": temporary_password,
                "new_password": final_password,
            },
            headers={"origin": "http://testserver"},
        )
        assert changed.status_code == 200, changed.text

        latest = staff_client.get(
            f"/api/messages/{message_id}/ai-conversations/latest"
        )
        assert latest.status_code == 403, latest.text
        create = staff_client.post(
            f"/api/messages/{message_id}/ai-conversations",
            json={"task_type": "summary"},
            headers={"origin": "http://testserver"},
        )
        assert create.status_code == 403, create.text

        detail = staff_client.get(f"/api/messages/{message_id}")
        assert detail.status_code == 200, detail.text
        payload = detail.json()
        assert payload["comments"] == []
        assert payload["message"]["comment_count"] == 0
        assert payload["message"]["latest_comment"] is None
        extraction = payload["message"]["attachments"][0]["text_extraction"]
        assert extraction["latest_confirmed_text"] == "관리자가 확인한 최종 판독문"
        assert extraction["extracted_text"] is None
        assert extraction["original_extracted_text"] is None
        assert extraction["suggested_text"] is None
        assert extraction["reviewed_text"] is None
        assert extraction["provider"] == ""
        assert extraction["model_name"] == ""
        assert extraction["error_message"] is None

        add_comment = staff_client.post(
            f"/api/messages/{message_id}/comments",
            json={"body": "직접 URL 우회 시도"},
            headers={"origin": "http://testserver"},
        )
        assert add_comment.status_code == 403, add_comment.text
        mark_comments_read = staff_client.post(
            f"/api/messages/{message_id}/comments/read",
            json={},
            headers={"origin": "http://testserver"},
        )
        assert mark_comments_read.status_code == 403, mark_comments_read.text


def test_audio_summary_uses_all_staff_reviewed_transcripts_in_file_order():
    with TestClient(app):
        with SessionLocal() as db:
            admin = db.scalar(select(User).where(User.username == "admin"))
            assert admin is not None and admin.staff_id is not None
            membership = db.scalar(
                select(RoomMembership).where(
                    RoomMembership.staff_id == admin.staff_id,
                    RoomMembership.left_at.is_(None),
                )
            )
            assert membership is not None

            message = Message(
                organization_id=admin.organization_id,
                room_id=membership.room_id,
                sender_id=admin.id,
                message_type="file",
                body="여러 음성 종합 정리 시험",
                lifecycle_status="active",
                is_test_data=True,
            )
            db.add(message)
            db.flush()

            attachments = [
                MessageAttachment(
                    organization_id=admin.organization_id,
                    message_id=message.id,
                    uploader_id=admin.id,
                    storage_key=f"test/{uuid4().hex}.wav",
                    original_name=name,
                    mime_type="audio/wav",
                    size_bytes=44,
                    upload_ordinal=index,
                )
                for index, name in enumerate(("첫번째.wav", "두번째.wav"), 1)
            ]
            db.add_all(attachments)
            db.flush()
            for index, attachment in enumerate(attachments, 1):
                db.add(
                    AttachmentTextExtraction(
                        organization_id=admin.organization_id,
                        attachment_id=attachment.id,
                        status="reviewed" if index == 1 else "pending",
                        reviewed_text="첫 번째 음성의 확인된 받아쓰기" if index == 1 else None,
                        reviewed_by_id=admin.id if index == 1 else None,
                        reviewed_at=ai_assist._now() if index == 1 else None,
                        provider="test",
                        model_name="test-stt",
                        extracted_text=(
                            "첫 번째 음성의 확인된 받아쓰기"
                            if index == 1
                            else None
                        ),
                        original_extracted_text=(
                            "첫 번째 음성의 확인된 받아쓰기"
                            if index == 1
                            else None
                        ),
                        requested_by_id=admin.id,
                    )
                )
            db.commit()

            pending_task = AiAssistTaskRequest(task_type="audio_summary")
            selected_attachments = ai_assist._attachments_for_task(
                db,
                message,
                pending_task,
            )
            assert len(selected_attachments) == 2
            assert ai_assist._attachment_extracted_text(db, attachments[1].id) is None
            with pytest.raises(HTTPException) as pending_error:
                create_ai_conversation(
                    db,
                    admin,
                    message.id,
                    pending_task,
                    process_immediately=False,
                )
            assert pending_error.value.status_code == 409
            assert "두번째.wav" in pending_error.value.detail
            assert "첫번째.wav" not in pending_error.value.detail

            second_extraction = db.scalar(
                select(AttachmentTextExtraction).where(
                    AttachmentTextExtraction.attachment_id == attachments[1].id
                )
            )
            assert second_extraction is not None
            second_extraction.status = "reviewed"
            second_extraction.reviewed_text = "두 번째 음성의 확인된 받아쓰기"
            second_extraction.reviewed_by_id = admin.id
            second_extraction.reviewed_at = ai_assist._now()
            second_extraction.extracted_text = "두 번째 음성의 확인된 받아쓰기"
            second_extraction.original_extracted_text = "두 번째 음성의 확인된 받아쓰기"
            db.commit()

            conversation = create_ai_conversation(
                db,
                admin,
                message.id,
                AiAssistTaskRequest(task_type="audio_summary"),
                process_immediately=False,
            )
            sources = list(
                db.scalars(
                    select(AiAssistSource)
                    .where(AiAssistSource.turn_id == conversation.turns[0].id)
                    .order_by(AiAssistSource.ordinal)
                )
            )
            audio_sources = [
                source for source in sources if source.source_type == "attachment"
            ]
            assert [source.label for source in audio_sources] == [
                "첨부 음성 1/2 · 첫번째.wav",
                "첨부 음성 2/2 · 두번째.wav",
            ]
            assert [source.content_text for source in audio_sources] == [
                "첫 번째 음성의 확인된 받아쓰기",
                "두 번째 음성의 확인된 받아쓰기",
            ]
            assert [source.source_meta["audio_no"] for source in audio_sources] == [
                1,
                2,
            ]
            context_categories = {
                source.source_meta.get("work_category")
                for source in sources
                if source.source_type == "manual_context"
            }
            assert "active_resident_roster" in context_categories
            assert "long_term_care_context" in context_categories
            domain_source = next(
                source
                for source in sources
                if source.source_meta.get("work_category")
                == "long_term_care_context"
            )
            assert "safety_rule" in (domain_source.content_text or "")


def test_real_data_is_blocked_audio_waits_and_unsafe_local_url_is_skipped(
    monkeypatch,
):
    monkeypatch.setattr(ai_assist.app_settings, "ai_review_provider", "chain")
    monkeypatch.setattr(ai_assist.app_settings, "ai_review_external_enabled", True)

    with TestClient(app):
        with SessionLocal() as db:
            admin = db.scalar(select(User).where(User.username == "admin"))
            assert admin is not None and admin.staff_id is not None
            membership = db.scalar(
                select(RoomMembership).where(
                    RoomMembership.staff_id == admin.staff_id,
                    RoomMembership.left_at.is_(None),
                )
            )
            assert membership is not None

            real_message = Message(
                organization_id=admin.organization_id,
                room_id=membership.room_id,
                sender_id=admin.id,
                message_type="chat",
                body="실제자료 외부전송 차단 시험",
                lifecycle_status="active",
                is_test_data=False,
            )
            db.add(real_message)
            db.commit()
            db.refresh(real_message)

            monkeypatch.setattr(ai_assist.app_settings, "ai_review_provider", "off")
            conversation = create_ai_conversation(
                db,
                admin,
                real_message.id,
                AiAssistTaskRequest(task_type="summary"),
                runtime=_runtime(allow_external=False),
                worker_client=MustNotRunWorker(),
            )
            turn = conversation.turns[0]
            assert turn.contains_real_data is True
            attempts = list(
                db.scalars(
                    select(AiAssistProviderAttempt)
                    .where(AiAssistProviderAttempt.turn_id == turn.id)
                    .order_by(AiAssistProviderAttempt.attempt_no)
                )
            )
            assert attempts[0].status == "skipped"
            assert attempts[0].error_code == "external_real_data_disabled"
            assert all(not attempt.transmitted_external for attempt in attempts)

            audio_message = Message(
                organization_id=admin.organization_id,
                room_id=membership.room_id,
                sender_id=admin.id,
                message_type="file",
                body="음성 첨부",
                lifecycle_status="active",
                is_test_data=True,
            )
            db.add(audio_message)
            db.flush()
            audio = MessageAttachment(
                organization_id=admin.organization_id,
                message_id=audio_message.id,
                uploader_id=admin.id,
                storage_key=f"test/{uuid4().hex}.wav",
                original_name="시험.wav",
                mime_type="audio/wav",
                size_bytes=44,
            )
            db.add(audio)
            db.commit()
            with pytest.raises(HTTPException) as pending_error:
                create_ai_conversation(
                    db,
                    admin,
                    audio_message.id,
                    AiAssistTaskRequest(
                        task_type="audio_summary",
                        attachment_id=audio.id,
                    ),
                )
            assert pending_error.value.status_code == 409
            assert "변환" in pending_error.value.detail

            unsafe_message = Message(
                organization_id=admin.organization_id,
                room_id=membership.room_id,
                sender_id=admin.id,
                message_type="chat",
                body="안전하지 않은 로컬 주소 시험",
                lifecycle_status="active",
                is_test_data=True,
            )
            db.add(unsafe_message)
            db.commit()
            db.refresh(unsafe_message)
            monkeypatch.setattr(ai_assist.app_settings, "ai_review_provider", "chain")
            monkeypatch.setattr(
                ai_assist.app_settings,
                "ai_review_base_url",
                "http://example.com:11434",
            )
            monkeypatch.setattr(
                ai_assist,
                "summarize_room_messages",
                lambda **_kwargs: (_ for _ in ()).throw(
                    AssertionError("외부 로컬 LLM 주소를 호출하면 안 됩니다.")
                ),
            )
            unsafe_conversation = create_ai_conversation(
                db,
                admin,
                unsafe_message.id,
                AiAssistTaskRequest(task_type="summary"),
                runtime=_runtime(allow_external=False),
                worker_client=NotConfiguredWorker(),
            )
            unsafe_turn = unsafe_conversation.turns[0]
            assert unsafe_turn.provider == "local_deterministic"
            unsafe_attempts = list(
                db.scalars(
                    select(AiAssistProviderAttempt)
                    .where(AiAssistProviderAttempt.turn_id == unsafe_turn.id)
                    .order_by(AiAssistProviderAttempt.attempt_no)
                )
            )
            assert any(
                attempt.error_code == "unsafe_local_ai_url"
                and attempt.status == "skipped"
                for attempt in unsafe_attempts
            )


def test_recalled_message_redacts_results_and_stale_turns_recover():
    with TestClient(app):
        with SessionLocal() as db:
            admin = db.scalar(select(User).where(User.username == "admin"))
            assert admin is not None and admin.staff_id is not None
            membership = db.scalar(
                select(RoomMembership).where(
                    RoomMembership.staff_id == admin.staff_id,
                    RoomMembership.left_at.is_(None),
                )
            )
            assert membership is not None
            message = Message(
                organization_id=admin.organization_id,
                room_id=membership.room_id,
                sender_id=admin.id,
                message_type="chat",
                body="회수 전 AI 시험 원문",
                lifecycle_status="active",
                is_test_data=True,
            )
            db.add(message)
            db.commit()
            db.refresh(message)

            conversation = create_ai_conversation(
                db,
                admin,
                message.id,
                AiAssistTaskRequest(task_type="summary"),
                process_immediately=False,
            )
            turn = conversation.turns[0]
            message.lifecycle_status = "recalled"
            db.commit()
            process_ai_turn(
                db,
                turn,
                runtime=_runtime(allow_external=False),
                worker_client=MustNotRunWorker(),
            )
            db.refresh(turn)
            assert turn.status == "cancelled"
            detail = conversation_detail(db, admin, conversation.id)
            assert detail.status == "cancelled"
            assert detail.turns[0].answer is None
            assert detail.turns[0].question is None

            stale_message = Message(
                organization_id=admin.organization_id,
                room_id=membership.room_id,
                sender_id=admin.id,
                message_type="chat",
                body="재시작 복구 시험",
                lifecycle_status="active",
                is_test_data=True,
            )
            db.add(stale_message)
            db.commit()
            db.refresh(stale_message)
            stale_conversation = create_ai_conversation(
                db,
                admin,
                stale_message.id,
                AiAssistTaskRequest(task_type="summary"),
                process_immediately=False,
            )
            stale_turn = stale_conversation.turns[0]
            stale_turn.updated_at = ai_assist._now() - timedelta(minutes=30)
            db.commit()
            assert recover_stale_ai_turns(db, stale_after_minutes=10) == 1
            db.refresh(stale_turn)
            assert stale_turn.status == "failed"
            assert stale_turn.error_code == "ai_assist_interrupted_by_restart"


def test_latest_cancel_share_and_config_responses_are_not_cached(monkeypatch):
    monkeypatch.setattr(ai_assist, "send_web_push_to_users", lambda *_a, **_k: None)
    configured_but_unready_worker = SimpleNamespace(
        configured=True,
        health=lambda: CodexWorkerHealth(
            credential_ready=False,
            credential_mode="missing",
            busy=False,
        ),
    )
    monkeypatch.setattr(
        ai_assist.AiAssistRuntimeSettings,
        "from_env",
        classmethod(
            lambda _cls: SimpleNamespace(
                allow_external_real_data=True,
                allow_external_real_image_data=True,
                build_worker_client=lambda: configured_but_unready_worker,
            )
        ),
    )
    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "AdminPass!234"},
            headers={"origin": "http://testserver"},
        )
        assert login.status_code == 200
        with SessionLocal() as db:
            admin = db.scalar(select(User).where(User.username == "admin"))
            membership = db.scalar(
                select(RoomMembership).where(
                    RoomMembership.staff_id == admin.staff_id,
                    RoomMembership.left_at.is_(None),
                )
            )
            message = Message(
                organization_id=admin.organization_id,
                room_id=membership.room_id,
                sender_id=admin.id,
                message_type="chat",
                body="최신 AI 대화 API 시험",
                lifecycle_status="active",
                is_test_data=True,
            )
            db.add(message)
            db.commit()
            db.refresh(message)
            empty_latest = client.get(
                f"/api/messages/{message.id}/ai-conversations/latest"
            )
            assert empty_latest.status_code == 200
            assert empty_latest.json() is None
            assert empty_latest.headers["cache-control"] == "private, no-store"
            conversation = create_ai_conversation(
                db,
                admin,
                message.id,
                AiAssistTaskRequest(task_type="summary"),
                process_immediately=False,
            )
            turn = conversation.turns[0]
            message_id = message.id
            conversation_id = conversation.id
            turn_id = turn.id

        for path in (
            f"/api/messages/{message_id}/ai-conversations/latest",
            f"/api/ai-conversations/{conversation_id}",
            f"/api/ai-turns/{turn_id}",
            "/api/ai-assist/config",
        ):
            response = client.get(path)
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "private, no-store"
            if path == "/api/ai-assist/config":
                assert response.json()["worker_configured"] is True
                assert response.json()["codex_ready"] is False
                assert response.json()["external_real_data_enabled"] is False
                assert response.json()["external_real_image_data_enabled"] is False

        cancelled = client.post(
            f"/api/ai-turns/{turn_id}/cancel",
            headers={"origin": "http://testserver"},
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"
        assert cancelled.headers["cache-control"] == "private, no-store"

        with SessionLocal() as db:
            admin = db.scalar(select(User).where(User.username == "admin"))
            share_conversation = create_ai_conversation(
                db,
                admin,
                message_id,
                AiAssistTaskRequest(task_type="summary"),
                process_immediately=False,
            )
            share_turn = share_conversation.turns[0]
            share_turn.status = "completed"
            share_turn.answer = "공유할 검증 결과"
            share_turn.result_validated = True
            share_turn.share_ready_at = ai_assist._now()
            share_conversation.status = "completed"
            db.commit()
            share_turn_id = share_turn.id

        shared = client.post(
            f"/api/ai-turns/{share_turn_id}/share",
            headers={"origin": "http://testserver"},
        )
        assert shared.status_code == 200, shared.text
        assert shared.json()["created"] is True
        assert shared.headers["cache-control"] == "private, no-store"
        shared_again = client.post(
            f"/api/ai-turns/{share_turn_id}/share",
            headers={"origin": "http://testserver"},
        )
        assert shared_again.status_code == 200, shared_again.text
        assert shared_again.json()["created"] is False


def test_image_text_preserves_original_builds_scoped_candidates_and_reuses_context(
    monkeypatch,
    tmp_path,
):
    original_visible_text = "가상상갥 어르신 혈압 130/80"
    monkeypatch.setattr(ai_assist.app_settings, "ai_review_provider", "off")
    monkeypatch.setattr(ai_assist.app_settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr(ai_assist, "_local_ocr_endpoint_is_safe", lambda: True)
    monkeypatch.setattr(
        ai_assist,
        "extract_report_text",
        lambda *_args, **_kwargs: original_visible_text,
    )

    with TestClient(app):
        with SessionLocal() as db:
            admin = db.scalar(select(User).where(User.username == "admin"))
            assert admin is not None

            actor_staff = Staff(
                organization_id=admin.organization_id,
                internal_code=f"ai-image-staff-{uuid4().hex[:8]}",
                display_name="이미지 시험 직원",
                job_title="시험",
                employment_status="active",
                is_test_data=True,
                is_active=True,
            )
            db.add(actor_staff)
            db.flush()
            actor = User(
                organization_id=admin.organization_id,
                staff_id=actor_staff.id,
                username=f"ai-image-user-{uuid4().hex[:8]}",
                display_name="이미지 시험 직원",
                password_hash="test-only-password-hash",
                is_active=True,
            )
            db.add(actor)
            db.flush()
            room = Room(
                organization_id=admin.organization_id,
                kind="custom",
                owner_staff_id=actor_staff.id,
                name="AI 이미지 시험방",
                resident_scope="daycare",
                is_active=True,
                is_test_data=True,
                created_by_id=actor.id,
            )
            db.add(room)
            db.flush()
            db.add(
                RoomMembership(
                    organization_id=admin.organization_id,
                    room_id=room.id,
                    staff_id=actor_staff.id,
                    source="manual",
                    created_by=actor.id,
                )
            )
            daycare_resident = Resident(
                organization_id=admin.organization_id,
                internal_code=f"ai-daycare-resident-{uuid4().hex[:8]}",
                display_name="가상갥",
                status="active",
                service_type="daycare",
                sort_order=1,
                is_test_data=False,
                is_active=True,
            )
            off_scope_resident = Resident(
                organization_id=admin.organization_id,
                internal_code=f"ai-facility-resident-{uuid4().hex[:8]}",
                display_name="시설전용이름",
                status="active",
                service_type="facility",
                sort_order=1,
                is_test_data=False,
                is_active=True,
            )
            excluded_fake_staff = Staff(
                organization_id=admin.organization_id,
                internal_code=f"ai-fake-staff-{uuid4().hex[:8]}",
                display_name="가상 직원(가명)",
                job_title="시험",
                employment_status="active",
                is_test_data=True,
                is_active=True,
            )
            included_real_staff = Staff(
                organization_id=admin.organization_id,
                internal_code=f"ai-real-staff-{uuid4().hex[:8]}",
                display_name="실제 명단 직원",
                job_title="사회복지사",
                employment_status="active",
                is_test_data=False,
                is_active=True,
            )
            db.add_all(
                [
                    daycare_resident,
                    off_scope_resident,
                    excluded_fake_staff,
                    included_real_staff,
                ]
            )
            message = Message(
                organization_id=admin.organization_id,
                room_id=room.id,
                sender_id=actor.id,
                message_type="file",
                body="이미지 판독 시험",
                lifecycle_status="active",
                is_test_data=True,
            )
            db.add(message)
            db.flush()
            attachment = MessageAttachment(
                organization_id=admin.organization_id,
                message_id=message.id,
                uploader_id=actor.id,
                storage_key=f"missing-{uuid4().hex}.jpg",
                original_name="시험.jpg",
                mime_type="image/jpeg",
                size_bytes=10,
            )
            db.add(attachment)
            db.commit()
            (tmp_path / attachment.storage_key).write_bytes(b"0123456789")

            first_worker = RecordingImageWorker(
                visible_text=original_visible_text,
                answer="혈압 기록이 포함된 이미지입니다.",
                uncertainties=["이름과 수치를 다시 확인해야 합니다."],
            )
            conversation = create_ai_conversation(
                db,
                actor,
                message.id,
                AiAssistTaskRequest(
                    task_type="image_text",
                    attachment_id=attachment.id,
                    service_context="daycare",
                ),
                runtime=_runtime(
                    allow_external=False,
                    allow_external_image=True,
                ),
                worker_client=first_worker,
            )
            first_turn = conversation.turns[0]
            first_response = conversation_detail(db, actor, conversation.id).turns[0]
            assert first_response.visible_text == original_visible_text
            assert first_response.service_context == "daycare"
            assert first_response.service_context_source == "request"
            assert len(first_response.resident_name_candidates) == 1
            candidate = first_response.resident_name_candidates[0]
            assert candidate.recognized == "가상상갥"
            assert candidate.candidate == "가상갥"
            assert candidate.resident_id == daycare_resident.id
            assert candidate.service_type == "daycare"
            assert candidate.confidence > 0.8

            assert first_worker.calls == []
            first_attempts = list(
                db.scalars(
                    select(AiAssistProviderAttempt).where(
                        AiAssistProviderAttempt.turn_id == first_turn.id
                    )
                )
            )
            assert first_attempts[0].error_code == "external_real_data_disabled"
            assert all(not attempt.transmitted_external for attempt in first_attempts)
            worker_sources = list(first_turn.sources)
            source_dump = json.dumps(
                [
                    {
                        "source_type": source.source_type,
                        "label": source.label,
                        "text": source.content_text,
                        "metadata": source.source_meta,
                    }
                    for source in worker_sources
                ],
                ensure_ascii=False,
            )
            assert "현재 이용자 이름 명단" in source_dump
            assert "가상갥" in source_dump
            assert "시설전용이름" not in source_dump
            assert "현재 재직·휴직 직원 이름 명단" in source_dump
            assert "실제 명단 직원" in source_dump
            assert "이미지 시험 직원" not in source_dump
            assert "가상 직원(가명)" not in source_dump
            assert worker_sources[0].source_meta["service_context"] == "daycare"

            extraction = db.scalar(
                select(AttachmentTextExtraction).where(
                    AttachmentTextExtraction.attachment_id == attachment.id
                )
            )
            assert extraction is not None
            assert extraction.status == "completed"
            assert extraction.extracted_text == original_visible_text
            assert extraction.original_extracted_text == original_visible_text
            assert extraction.reviewed_text is None

            same_conversation_rerun = add_ai_turn(
                db,
                actor,
                conversation.id,
                AiAssistTaskRequest(
                    task_type="image_text",
                    attachment_id=attachment.id,
                    service_context="daycare",
                ),
                runtime=_runtime(
                    allow_external=False,
                    allow_external_image=True,
                ),
                worker_client=MustNotRunWorker(),
            )
            rerun_turn = db.scalar(
                select(AiAssistTurn)
                .where(AiAssistTurn.conversation_id == same_conversation_rerun.id)
                .order_by(AiAssistTurn.sequence_no.desc())
                .limit(1)
            )
            assert rerun_turn is not None
            rerun_attempts = list(
                db.scalars(
                    select(AiAssistProviderAttempt).where(
                        AiAssistProviderAttempt.turn_id == rerun_turn.id
                    )
                )
            )
            assert rerun_attempts[0].error_code == "external_real_data_disabled"
            db.refresh(extraction)
            assert extraction.extracted_text == original_visible_text
            assert extraction.original_extracted_text == original_visible_text

            followup = add_ai_turn(
                db,
                actor,
                conversation.id,
                AiAssistTaskRequest(
                    task_type="question",
                    question="주간보호 명단과 매칭해줘",
                ),
                runtime=_runtime(allow_external=False),
                worker_client=MustNotRunWorker(),
            )
            followup_turn = db.scalar(
                select(AiAssistTurn)
                .where(AiAssistTurn.conversation_id == followup.id)
                .order_by(AiAssistTurn.sequence_no.desc())
                .limit(1)
            )
            assert followup_turn is not None
            assert followup_turn.contains_real_data is True
            followup_sources = list(
                db.scalars(
                    select(AiAssistSource).where(
                        AiAssistSource.turn_id == followup_turn.id,
                        AiAssistSource.source_type == "manual_context",
                    )
                )
            )
            followup_context = "\n".join(
                source.content_text or "" for source in followup_sources
            )
            assert "이전 원본 판독문" in followup_context
            assert original_visible_text in followup_context
            assert "서버 내부 명단 대조 후보" in followup_context
            assert "가상상갥 → 가상갥" in followup_context
            followup_attempts = list(
                db.scalars(
                    select(AiAssistProviderAttempt).where(
                        AiAssistProviderAttempt.turn_id == followup_turn.id
                    )
                )
            )
            assert followup_attempts[0].error_code == "external_real_data_disabled"
            assert all(not attempt.transmitted_external for attempt in followup_attempts)

            allowed_followup_worker = RecordingImageWorker(
                visible_text=None,
                answer="선택 이미지와 주간보호 명단을 함께 확인했습니다.",
            )
            allowed_followup = add_ai_turn(
                db,
                actor,
                conversation.id,
                AiAssistTaskRequest(
                    task_type="question",
                    question="선택 이미지의 이름 후보를 다시 설명해줘",
                ),
                runtime=_runtime(
                    allow_external=False,
                    allow_external_image=True,
                ),
                worker_client=allowed_followup_worker,
            )
            allowed_followup_turn = db.scalar(
                select(AiAssistTurn)
                .where(AiAssistTurn.conversation_id == allowed_followup.id)
                .order_by(AiAssistTurn.sequence_no.desc())
                .limit(1)
            )
            assert allowed_followup_turn is not None
            assert allowed_followup_turn.contains_real_data is True
            assert len(allowed_followup_worker.calls) == 0
            allowed_source_dump = json.dumps(
                [
                    {
                        "source_type": source.source_type,
                        "label": source.label,
                        "text": source.content_text,
                        "metadata": source.source_meta,
                    }
                    for source in allowed_followup_turn.sources
                ],
                ensure_ascii=False,
            )
            assert original_visible_text in allowed_source_dump
            assert "가상갥" in allowed_source_dump

            fallback_external_flags: list[bool] = []

            def selective_image_fallback(
                *,
                entries,
                external_allowed=False,
                purpose=None,
            ):
                del entries, purpose
                fallback_external_flags.append(external_allowed)
                if not external_allowed:
                    raise LocalAiError("시험용 로컬 모델 실패")
                return RoomSummaryResult(
                    summary="선택 이미지 범위의 외부 대체 결과입니다. [1]",
                    provider="nvidia",
                    model="test-nemotron",
                    elapsed_ms=9,
                )

            monkeypatch.setattr(ai_assist.app_settings, "ai_review_provider", "chain")
            monkeypatch.setattr(
                ai_assist.app_settings,
                "ai_review_external_enabled",
                True,
            )
            monkeypatch.setattr(
                ai_assist,
                "summarize_room_messages",
                selective_image_fallback,
            )
            fallback_followup = add_ai_turn(
                db,
                actor,
                conversation.id,
                AiAssistTaskRequest(
                    task_type="risk_check",
                    question="선택 이미지에서 확인할 위험은?",
                ),
                runtime=_runtime(
                    allow_external=False,
                    allow_external_image=True,
                ),
                worker_client=UnavailableWorker(),
            )
            fallback_turn = db.scalar(
                select(AiAssistTurn)
                .where(AiAssistTurn.conversation_id == fallback_followup.id)
                .order_by(AiAssistTurn.sequence_no.desc())
                .limit(1)
            )
            assert fallback_turn is not None
            assert fallback_turn.provider == "local_deterministic"
            assert fallback_external_flags == [False]
            fallback_attempts = list(
                db.scalars(
                    select(AiAssistProviderAttempt)
                    .where(AiAssistProviderAttempt.turn_id == fallback_turn.id)
                    .order_by(AiAssistProviderAttempt.attempt_no)
                )
            )
            assert all(not attempt.transmitted_external for attempt in fallback_attempts)
            monkeypatch.setattr(ai_assist.app_settings, "ai_review_provider", "off")
            monkeypatch.setattr(
                ai_assist.app_settings,
                "ai_review_external_enabled",
                False,
            )

            blocked_history = add_ai_turn(
                db,
                actor,
                conversation.id,
                AiAssistTaskRequest(
                    task_type="history_search",
                    question="관련된 과거 대화를 모두 찾아줘",
                ),
                runtime=_runtime(
                    allow_external=False,
                    allow_external_image=True,
                ),
                worker_client=MustNotRunWorker(),
            )
            blocked_history_turn = db.scalar(
                select(AiAssistTurn)
                .where(AiAssistTurn.conversation_id == blocked_history.id)
                .order_by(AiAssistTurn.sequence_no.desc())
                .limit(1)
            )
            assert blocked_history_turn is not None
            history_attempts = list(
                db.scalars(
                    select(AiAssistProviderAttempt).where(
                        AiAssistProviderAttempt.turn_id
                        == blocked_history_turn.id
                    )
                )
            )
            assert history_attempts[0].error_code == "external_real_data_disabled"
            assert all(not attempt.transmitted_external for attempt in history_attempts)

            shared = share_ai_turn(db, actor, first_turn.id)
            shared_message = db.get(Message, shared.message_id)
            assert shared_message is not None
            assert len(shared_message.body) <= 2000
            assert "원본 판독문" in shared_message.body
            assert original_visible_text in shared_message.body
            assert "기존 텍스트 추출 결과" in shared_message.body
            assert "전문 AI의 이미지·문맥 판단은 수행되지 않았습니다." in shared_message.body
            assert "이름 확인 후보 · 확정 아님" in shared_message.body
            assert "가상상갥 → 가상갥" in shared_message.body

            extraction.status = "reviewed"
            extraction.original_extracted_text = "최초 자동 판독"
            extraction.extracted_text = "검토 전 자동 판독"
            extraction.reviewed_text = "직원이 확정한 판독문"
            db.commit()
            second_worker = RecordingImageWorker(visible_text="두 번째 자동 판독")
            create_ai_conversation(
                db,
                actor,
                message.id,
                AiAssistTaskRequest(
                    task_type="image_text",
                    attachment_id=attachment.id,
                    service_context="daycare",
                ),
                runtime=_runtime(
                    allow_external=False,
                    allow_external_image=True,
                ),
                worker_client=second_worker,
            )
            assert second_worker.calls == []
            db.refresh(extraction)
            assert extraction.status == "reviewed"
            assert extraction.original_extracted_text == "최초 자동 판독"
            assert extraction.extracted_text == "검토 전 자동 판독"
            assert extraction.reviewed_text == "직원이 확정한 판독문"

            room.resident_scope = "all"
            db.commit()
            unresolved_worker = RecordingImageWorker(
                visible_text=original_visible_text
            )
            unresolved = create_ai_conversation(
                db,
                actor,
                message.id,
                AiAssistTaskRequest(
                    task_type="image_text",
                    attachment_id=attachment.id,
                ),
                runtime=_runtime(
                    allow_external=False,
                    allow_external_image=True,
                ),
                worker_client=unresolved_worker,
            )
            unresolved_response = conversation_detail(
                db,
                actor,
                unresolved.id,
            ).turns[0]
            assert unresolved_response.service_context is None
            # The reviewed local text no longer contains a resident name. The
            # blocked external worker must not invent or restore a candidate.
            assert unresolved_response.resident_name_candidates == []
            assert "함께 확인" in (
                unresolved_response.service_context_notice or ""
            )
            unresolved_turn = unresolved.turns[0]
            assert unresolved_worker.calls == []
            unresolved_source_dump = json.dumps(
                [
                    {
                        "source_type": source.source_type,
                        "label": source.label,
                        "text": source.content_text,
                        "metadata": source.source_meta,
                    }
                    for source in unresolved_turn.sources
                ],
                ensure_ascii=False,
            )
            assert "[시설]" in unresolved_source_dump
            assert "[주간보호]" in unresolved_source_dump
            assert "시설전용이름" in unresolved_source_dump

            room.is_test_data = False
            db.commit()
            guarded = create_ai_conversation(
                db,
                actor,
                message.id,
                AiAssistTaskRequest(task_type="summary"),
                runtime=_runtime(
                    allow_external=False,
                    allow_external_image=True,
                ),
                worker_client=MustNotRunWorker(),
            )
            guarded_turn = guarded.turns[0]
            assert message.is_test_data is True
            assert guarded_turn.contains_real_data is True

            audio_message = Message(
                organization_id=admin.organization_id,
                room_id=room.id,
                sender_id=actor.id,
                message_type="file",
                body="선택 이미지 예외로 열리면 안 되는 음성",
                lifecycle_status="active",
                is_test_data=True,
            )
            db.add(audio_message)
            db.flush()
            audio_attachment = MessageAttachment(
                organization_id=admin.organization_id,
                message_id=audio_message.id,
                uploader_id=actor.id,
                storage_key=f"audio-{uuid4().hex}.wav",
                original_name="실제음성.wav",
                mime_type="audio/wav",
                size_bytes=44,
            )
            db.add(audio_attachment)
            db.flush()
            db.add(
                AttachmentTextExtraction(
                    organization_id=admin.organization_id,
                    attachment_id=audio_attachment.id,
                    status="completed",
                    provider="test",
                    model_name="test-whisper",
                    extracted_text="실제 음성 변환문",
                    original_extracted_text="실제 음성 변환문",
                    reviewed_text="실제 음성 변환문",
                    reviewed_by_id=actor.id,
                    reviewed_at=ai_assist._now(),
                    requested_by_id=actor.id,
                    completed_at=ai_assist._now(),
                )
            )
            db.commit()
            blocked_audio = create_ai_conversation(
                db,
                actor,
                audio_message.id,
                AiAssistTaskRequest(
                    task_type="audio_summary",
                    attachment_id=audio_attachment.id,
                ),
                runtime=_runtime(
                    allow_external=False,
                    allow_external_image=True,
                ),
                worker_client=MustNotRunWorker(),
            )
            audio_attempts = list(
                db.scalars(
                    select(AiAssistProviderAttempt).where(
                        AiAssistProviderAttempt.turn_id == blocked_audio.turns[0].id
                    )
                )
            )
            assert audio_attempts[0].error_code == "external_real_data_disabled"
            assert all(not attempt.transmitted_external for attempt in audio_attempts)

            # An image shared without pre-requested OCR gets one safe local OCR
            # pass when the user explicitly asks AI to read its text.
            room.is_test_data = True
            local_message = Message(
                organization_id=admin.organization_id,
                room_id=room.id,
                sender_id=actor.id,
                message_type="file",
                body="일반 사진 공유 후 판독",
                lifecycle_status="active",
                is_test_data=True,
            )
            db.add(local_message)
            db.flush()
            local_storage_key = f"local-{uuid4().hex}.jpg"
            local_attachment = MessageAttachment(
                organization_id=admin.organization_id,
                message_id=local_message.id,
                uploader_id=actor.id,
                storage_key=local_storage_key,
                original_name="일반사진.jpg",
                mime_type="image/jpeg",
                size_bytes=4,
            )
            db.add(local_attachment)
            db.commit()
            (tmp_path / local_storage_key).write_bytes(b"test")
            monkeypatch.setattr(ai_assist.app_settings, "upload_dir", str(tmp_path))
            monkeypatch.setattr(ai_assist.app_settings, "ocr_provider", "stub")
            monkeypatch.setattr(
                ai_assist,
                "extract_report_text",
                lambda *_args, **_kwargs: "로컬 OCR 최초 판독문",
            )
            local_conversation = create_ai_conversation(
                db,
                actor,
                local_message.id,
                AiAssistTaskRequest(
                    task_type="image_text",
                    attachment_id=local_attachment.id,
                    service_context="daycare",
                ),
                runtime=_runtime(allow_external=False),
                worker_client=NotConfiguredWorker(),
            )
            local_response = conversation_detail(
                db,
                actor,
                local_conversation.id,
            ).turns[0]
            assert local_response.visible_text == "로컬 OCR 최초 판독문"
            local_extraction = db.scalar(
                select(AttachmentTextExtraction).where(
                    AttachmentTextExtraction.attachment_id == local_attachment.id
                )
            )
            assert local_extraction is not None
            assert local_extraction.status == "completed"
            assert local_extraction.original_extracted_text == "로컬 OCR 최초 판독문"
