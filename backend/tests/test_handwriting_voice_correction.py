from io import BytesIO
import wave
from uuid import UUID, uuid4

import httpx
from app import main as main_module
from app.database import SessionLocal
from app.handwriting_voice_correction import (
    build_handwriting_voice_comparison,
    correction_sentences,
)
from app import handwriting_voice_ai
from app.main import app
from app.models import (
    AttachmentCoordinateReview,
    AttachmentTextExtraction,
    HandwritingCorrectionApproval,
    MessageAttachment,
    MessageResidentLink,
    OcrCorrectionEvent,
    Resident,
    Staff,
    User,
)
from app.security import hash_password
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select


ORIGIN = {"origin": "http://testserver"}
OCR_TEXT = (
    "2월 3일 오후 2시 10분\n물 200mL 제공함\n"
    "처음에는 절반 정도 마심\n오후 4시에 다시 확인 예정\n"
    "오후 4시 5분 확인함\n총 200mL 섭취 완료"
)
TRANSCRIPT = (
    "2월 3일 오후 2시 10분에 물 200mL 제공했습니다. "
    "처음에는 절반 정도만 마셔서 오후 4시에 다시 확인했습니다. "
    "오후 4시 5분에 남은 물까지 마셔서 총 200mL 섭취를 완료했습니다."
)


def _image_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 32), "white").save(output, format="JPEG")
    return output.getvalue()


def _audio_bytes() -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16_000)
        target.writeframes(b"\x00\x00" * 1_600)
    return output.getvalue()


def _login_admin(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "AdminPass!234"},
        headers=ORIGIN,
    )
    assert response.status_code == 200, response.text


def _prepare_completed_pair(client: TestClient, monkeypatch):
    async def no_background_processing(_items):
        return None

    monkeypatch.setattr(
        main_module,
        "_run_new_attachment_processing_batch",
        no_background_processing,
    )
    room_id = client.get("/api/rooms").json()[0]["id"]
    response = client.post(
        f"/api/rooms/{room_id}/messages-with-files",
        data={"body": "비식별 손글씨 승인 회귀", "message_type": "chat", "report_image": "true"},
        files=[
            ("files", ("handwriting.jpg", _image_bytes(), "image/jpeg")),
            ("files", ("full-reading.wav", _audio_bytes(), "audio/wav")),
        ],
        headers=ORIGIN,
    )
    assert response.status_code == 201, response.text
    attachments = response.json()["attachments"]
    image_id = UUID(attachments[0]["id"])
    audio_id = UUID(attachments[1]["id"])
    with SessionLocal() as db:
        for attachment_id, text, provider, model in (
            (image_id, OCR_TEXT, "ollama", "qwen3-vl:8b-instruct"),
            (audio_id, TRANSCRIPT, "local_whisper_service", "whisper-small"),
        ):
            extraction = db.scalar(
                select(AttachmentTextExtraction).where(
                    AttachmentTextExtraction.attachment_id == attachment_id
                )
            )
            assert extraction is not None
            extraction.status = "completed"
            extraction.provider = provider
            extraction.model_name = model
            extraction.extracted_text = text
            extraction.original_extracted_text = text
        db.commit()
    return image_id, audio_id


def test_browser_recording_is_attached_to_same_message_and_queued_for_internal_stt(
    monkeypatch,
):
    monkeypatch.setattr(
        main_module,
        "_run_attachment_text_extraction",
        lambda _attachment_id: None,
    )
    with TestClient(app) as client:
        _login_admin(client)
        image_id, _audio_id = _prepare_completed_pair(client, monkeypatch)
        response = client.post(
            f"/api/attachments/{image_id}/voice-correction-recordings",
            data={"mode": "partial_correction"},
            files={"file": ("browser-recording.wav", _audio_bytes(), "audio/wav")},
            headers=ORIGIN,
        )
        assert response.status_code == 201, response.text
        payload = response.json()
        assert payload["mime_type"] == "audio/wav"
        assert payload["text_extraction"]["status"] == "pending"

        with SessionLocal() as db:
            image = db.get(MessageAttachment, image_id)
            recording = db.get(MessageAttachment, UUID(payload["id"]))
            assert image is not None
            assert recording is not None
            assert recording.message_id == image.message_id
            assert recording.uploader_id == image.uploader_id


def test_full_reading_comparison_preserves_critical_facts_and_locks_approval():
    result = build_handwriting_voice_comparison(
        initial_ocr=OCR_TEXT,
        transcript=TRANSCRIPT,
        image_evidence_ref="attachment:image",
        audio_evidence_ref="attachment:audio",
        image_provider="ollama",
        image_model="qwen3-vl:8b-instruct",
        audio_provider="local_whisper_service",
        audio_model="whisper-small",
    )

    facts = {item["category"]: item for item in result["critical_facts"]}
    assert facts["quantity"]["status"] == "same"
    assert facts["unit"]["status"] == "same"
    assert "200mL" in facts["quantity"]["image_values"]
    assert any("오후 4시" in value for value in facts["time"]["audio_values"])
    assert any("다시 확인" in value for value in facts["follow_up"]["audio_values"])
    assert facts["completion"]["status"] == "same"
    final_stage = result["stages"][-1]
    assert final_stage["status"] == "awaiting_staff_approval"
    assert final_stage["text"] is None
    assert result["safety"] == {
        "saved_before_approval": False,
        "official_record_written": False,
        "training_candidate_created": False,
        "external_transfer": False,
        "direct_typing_supported": True,
        "unsupported_fact_generation": False,
        "unmentioned_fields_unchanged": True,
        "story_hint_is_not_ground_truth": True,
    }


def test_difference_is_shown_without_selecting_one_conflicting_time():
    result = build_handwriting_voice_comparison(
        initial_ocr="오전 2시 10분 물 200mL 제공",
        transcript="오후 2시 20분 물 200mL 제공",
        image_evidence_ref="attachment:image",
        audio_evidence_ref="attachment:audio",
        image_provider="ollama",
        image_model="qwen3-vl:8b-instruct",
        audio_provider="local_whisper_service",
        audio_model="whisper-small",
    )
    time_fact = next(
        item for item in result["critical_facts"] if item["category"] == "time"
    )
    assert time_fact["status"] == "different"
    assert time_fact["requires_staff_confirmation"] is True
    assert result["stages"][-1]["text"] is None


def test_full_reading_keeps_natural_ocr_when_voice_contains_impossible_time():
    result = build_handwriting_voice_comparison(
        initial_ocr="오후 2시 10분 상태를 확인함",
        transcript="오후 52시 10분 상태를 확인함",
        image_evidence_ref="attachment:image",
        audio_evidence_ref="attachment:audio",
        image_provider="ollama",
        image_model="qwen3-vl:8b-instruct",
        audio_provider="local_whisper_service",
        audio_model="large-v3-turbo",
        audio_quality={"status": "good", "reading_pace": "normal"},
    )

    suggestion = result["stages"][2]["text"]
    assert suggestion == "오후 2시 10분 상태를 확인함"
    assert "52시" not in suggestion
    assert result["stages"][2]["provider"] == "local_evidence_fusion"


def test_full_reading_uses_voice_when_ocr_is_obviously_broken_and_context_agrees():
    result = build_handwriting_voice_comparison(
        initial_ocr="오후 4시 상태 확인 여자",
        transcript="오후 4시 상태 확인 예정",
        image_evidence_ref="attachment:image",
        audio_evidence_ref="attachment:audio",
        image_provider="ollama",
        image_model="qwen3-vl:8b-instruct",
        audio_provider="local_whisper_service",
        audio_model="large-v3-turbo",
        audio_quality={"status": "good", "reading_pace": "normal"},
    )

    suggestion = result["stages"][2]["text"]
    assert suggestion == "오후 4시 상태 확인 예정"
    assert "확인 여자" not in suggestion


def test_internal_refiner_can_make_a_grounded_natural_sentence():
    calls = 0

    def refine(**_kwargs):
        nonlocal calls
        calls += 1
        return {
            "final_text": "오후 4시 상태 확인 예정. 직원과 대화 후 표정이 안정됨.",
            "provider": "ollama",
            "model": "internal-test-model",
        }

    result = build_handwriting_voice_comparison(
        initial_ocr="오후 4시 상태 확인 여자. 직원과 대화 후 표정 안정.",
        transcript="오후 4시 상태 확인 예정. 직원과 대화 후 표정이 안정됐습니다.",
        image_evidence_ref="attachment:image",
        audio_evidence_ref="attachment:audio",
        image_provider="ollama",
        image_model="qwen3-vl:8b-instruct",
        audio_provider="local_whisper_service",
        audio_model="large-v3-turbo",
        ai_refiner=refine,
    )

    stage = result["stages"][2]
    assert calls == 1
    assert stage["text"] == "오후 4시 상태 확인 예정. 직원과 대화 후 표정이 안정됨."
    assert stage["provider"] == "ollama"
    assert stage["model"] == "internal-test-model"


def test_internal_refiner_rejects_an_unsupported_new_time():
    initial = "오후 4시 상태 확인 예정"
    result = build_handwriting_voice_comparison(
        initial_ocr=initial,
        transcript="오후 4시 상태 확인 예정",
        image_evidence_ref="attachment:image",
        audio_evidence_ref="attachment:audio",
        image_provider="ollama",
        image_model="qwen3-vl:8b-instruct",
        audio_provider="local_whisper_service",
        audio_model="large-v3-turbo",
        ai_refiner=lambda **_kwargs: {
            "final_text": "오후 8시 상태 확인 예정",
            "provider": "ollama",
            "model": "internal-test-model",
        },
    )

    stage = result["stages"][2]
    assert stage["text"] == initial
    assert stage["provider"] == "local_evidence_fusion"


def test_internal_refiner_keeps_conflicting_time_unresolved():
    result = build_handwriting_voice_comparison(
        initial_ocr="오후 2시 물 제공함",
        transcript="오후 4시 물 제공함",
        image_evidence_ref="attachment:image",
        audio_evidence_ref="attachment:audio",
        image_provider="ollama",
        image_model="qwen3-vl:8b-instruct",
        audio_provider="local_whisper_service",
        audio_model="large-v3-turbo",
        ai_refiner=lambda **_kwargs: {
            "final_text": "오후 4시 물 제공함",
            "provider": "ollama",
            "model": "internal-test-model",
        },
    )

    stage = result["stages"][2]
    assert stage["text"] == "[시간 확인 필요] 물 제공함"
    assert "오후 2시" not in stage["text"]
    assert "오후 4시" not in stage["text"]


def test_fraction_written_differently_matches_but_conflicting_fraction_is_blocked():
    same = build_handwriting_voice_comparison(
        initial_ocr="점심 1/2 섭취함",
        transcript="점심 2분의 1 섭취함",
        image_evidence_ref="attachment:image",
        audio_evidence_ref="attachment:audio",
        image_provider="ollama",
        image_model="qwen3-vl:8b-instruct",
        audio_provider="local_whisper_service",
        audio_model="large-v3-turbo",
    )
    same_quantity = next(
        item for item in same["critical_facts"] if item["category"] == "quantity"
    )
    assert same_quantity["status"] == "same"

    different = build_handwriting_voice_comparison(
        initial_ocr="점심 1/2 섭취함",
        transcript="점심 3분의 1 섭취함",
        image_evidence_ref="attachment:image",
        audio_evidence_ref="attachment:audio",
        image_provider="ollama",
        image_model="qwen3-vl:8b-instruct",
        audio_provider="local_whisper_service",
        audio_model="large-v3-turbo",
        ai_refiner=lambda **_kwargs: {
            "final_text": "점심 3분의 1 섭취함",
            "provider": "ollama",
            "model": "internal-test-model",
        },
    )
    assert different["stages"][2]["text"] == "점심 [수량 확인 필요] 섭취함"


def test_internal_ai_transport_is_internal_only_and_parses_contract(monkeypatch):
    monkeypatch.setattr(
        handwriting_voice_ai,
        "_internal_text_target",
        lambda: ("http://internal-ollama", "internal-test-model", 30),
    )
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "message": {
                    "content": '{"final_text":"오후 4시 확인 예정","source_rows":[1]}'
                }
            }

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return FakeResponse()

    result = handwriting_voice_ai.refine_handwriting_with_internal_ai(
        initial_ocr="오후 4시 확인 여자",
        transcript="오후 4시 확인 예정",
        deterministic_suggestion="오후 4시 확인 예정",
        alignments=[
            {
                "row_no": 1,
                "image_text": "오후 4시 확인 여자",
                "audio_text": "오후 4시 확인 예정",
                "status": "different",
                "similarity": 0.8,
            }
        ],
        audio_quality={"status": "good", "reasons": []},
        mode="full_reading",
        post=fake_post,
    )

    assert result is not None
    assert result["final_text"] == "오후 4시 확인 예정"
    assert result["external_transfer"] is False
    assert captured["url"] == "http://internal-ollama/api/chat"
    assert captured["kwargs"]["trust_env"] is False
    assert captured["kwargs"]["json"]["stream"] is False


def test_internal_ai_http_status_failure_returns_none(monkeypatch):
    monkeypatch.setattr(
        handwriting_voice_ai,
        "_internal_text_target",
        lambda: ("http://internal-ollama", "internal-test-model", 30),
    )

    class FailedResponse:
        def raise_for_status(self):
            request = httpx.Request("POST", "http://internal-ollama/api/chat")
            response = httpx.Response(502, request=request)
            raise httpx.HTTPStatusError(
                "internal provider unavailable",
                request=request,
                response=response,
            )

    result = handwriting_voice_ai.refine_handwriting_with_internal_ai(
        initial_ocr="오후 4시 확인 여자",
        transcript="오후 4시 확인 예정",
        deterministic_suggestion="오후 4시 확인 예정",
        alignments=[],
        audio_quality={"status": "good", "reasons": []},
        mode="full_reading",
        post=lambda *_args, **_kwargs: FailedResponse(),
    )

    assert result is None


def test_full_reading_marks_plausible_time_and_quantity_conflicts_for_staff():
    result = build_handwriting_voice_comparison(
        initial_ocr="오후 2시 물 100mL 제공함",
        transcript="오후 4시 물 200mL 제공함",
        image_evidence_ref="attachment:image",
        audio_evidence_ref="attachment:audio",
        image_provider="ollama",
        image_model="qwen3-vl:8b-instruct",
        audio_provider="local_whisper_service",
        audio_model="large-v3-turbo",
        audio_quality={"status": "good", "reading_pace": "normal"},
    )

    suggestion = result["stages"][2]["text"]
    assert "[시간 확인 필요]" in suggestion
    assert "[수량 확인 필요]" in suggestion
    assert "오후 2시" not in suggestion
    assert "오후 4시" not in suggestion
    assert "100mL" not in suggestion
    assert "200mL" not in suggestion


def test_low_quality_voice_is_not_adopted_as_ground_truth():
    initial = "오후 2시 10분 물 200mL 제공함"
    result = build_handwriting_voice_comparison(
        initial_ocr=initial,
        transcript="오후 2시 50분 물 900mL 제공함",
        image_evidence_ref="attachment:image",
        audio_evidence_ref="attachment:audio",
        image_provider="ollama",
        image_model="qwen3-vl:8b-instruct",
        audio_provider="local_whisper_service",
        audio_model="large-v3-turbo",
        audio_quality={
            "status": "low",
            "reading_pace": "fast",
            "message": "음성이 작거나 불분명합니다. 원본과 다른 부분을 확인하거나 다시 녹음해 주세요.",
            "reasons": ["fast_reading"],
        },
    )

    assert result["stages"][2]["text"] == initial
    assert result["audio_quality"]["status"] == "low"
    assert "다시 녹음" in result["audio_quality"]["message"]


def test_partial_correction_changes_only_explicit_one_to_one_fields():
    initial = "오후 2시 물 100mL 제공함. 이동 시 부축함."
    result = build_handwriting_voice_comparison(
        initial_ocr=initial,
        transcript="시간은 오후 4시, 물은 200mL입니다.",
        image_evidence_ref="attachment:image",
        audio_evidence_ref="attachment:audio",
        image_provider="ollama",
        image_model="qwen3-vl:8b-instruct",
        audio_provider="local_whisper_service",
        audio_model="whisper-small",
        mode="partial_correction",
    )

    suggestion = result["stages"][2]["text"]
    assert suggestion == "오후 4시 물 200mL 제공함. 이동 시 부축함."
    fields = {item["category"]: item for item in result["changed_fields"]}
    assert fields["time"]["status"] == "proposed"
    assert fields["quantity"]["status"] == "proposed"
    assert fields["unit"]["status"] == "unchanged"
    assert "이동 시 부축함" in suggestion
    assert result["safety"]["unmentioned_fields_unchanged"] is True


def test_partial_correction_does_not_choose_between_multiple_existing_times():
    initial = "오후 2시 물 제공함. 오후 4시 다시 확인 예정."
    result = build_handwriting_voice_comparison(
        initial_ocr=initial,
        transcript="시간은 오후 6시입니다.",
        image_evidence_ref="attachment:image",
        audio_evidence_ref="attachment:audio",
        image_provider="ollama",
        image_model="qwen3-vl:8b-instruct",
        audio_provider="local_whisper_service",
        audio_model="whisper-small",
        mode="partial_correction",
    )

    time_change = next(
        item for item in result["changed_fields"] if item["category"] == "time"
    )
    assert time_change["status"] == "needs_confirmation"
    assert result["stages"][2]["text"] == initial


def test_story_hint_cannot_create_unsupported_core_or_sensitive_facts():
    initial = "오후 2시 물 200mL 제공함. 오후 4시 다시 확인 예정."
    result = build_handwriting_voice_comparison(
        initial_ocr=initial,
        transcript=(
            "홍길동 어르신에게 혈압약을 드렸고 오후 6시에 섭취 완료했습니다."
        ),
        image_evidence_ref="attachment:image",
        audio_evidence_ref="attachment:audio",
        image_provider="ollama",
        image_model="qwen3-vl:8b-instruct",
        audio_provider="local_whisper_service",
        audio_model="whisper-small",
        mode="story_hint",
    )

    suggestion = result["stages"][2]["text"]
    assert suggestion == "오후 2시 물 200mL 제공함. 오후 4시 다시 확인 예정."
    assert "홍길동" not in suggestion
    assert "혈압약" not in suggestion
    assert "오후 6시" not in suggestion
    assert "완료" not in suggestion
    blocked = {
        item["category"]
        for item in result["changed_fields"]
        if item["status"] == "blocked"
    }
    assert {"time", "completion", "name", "medication"}.issubset(blocked)
    assert result["summary"]["blocked_field_count"] >= 4
    assert result["safety"]["story_hint_is_not_ground_truth"] is True


def test_comparison_endpoint_reads_existing_results_without_saving(monkeypatch):
    async def no_background_processing(_items):
        return None

    monkeypatch.setattr(
        main_module,
        "_run_new_attachment_processing_batch",
        no_background_processing,
    )
    with TestClient(app) as client:
        _login_admin(client)
        room_id = client.get("/api/rooms").json()[0]["id"]
        response = client.post(
            f"/api/rooms/{room_id}/messages-with-files",
            data={"body": "비식별 손글씨 전체 읽기 비교", "message_type": "chat", "report_image": "true"},
            files=[
                ("files", ("handwriting.jpg", _image_bytes(), "image/jpeg")),
                ("files", ("full-reading.wav", _audio_bytes(), "audio/wav")),
            ],
            headers=ORIGIN,
        )
        assert response.status_code == 201, response.text
        attachments = response.json()["attachments"]
        image_id = UUID(attachments[0]["id"])
        audio_id = UUID(attachments[1]["id"])

        with SessionLocal() as db:
            for attachment_id, text, provider, model in (
                (image_id, OCR_TEXT, "ollama", "qwen3-vl:8b-instruct"),
                (
                    audio_id,
                    TRANSCRIPT,
                    "local_whisper_service",
                    "whisper-small",
                ),
            ):
                extraction = db.scalar(
                    select(AttachmentTextExtraction).where(
                        AttachmentTextExtraction.attachment_id == attachment_id
                    )
                )
                assert extraction is not None
                extraction.status = "completed"
                extraction.provider = provider
                extraction.model_name = model
                extraction.extracted_text = text
                extraction.original_extracted_text = text
            before = db.scalar(select(func.count(OcrCorrectionEvent.id)))
            db.commit()

        comparison = client.get(
            f"/api/attachments/{image_id}/voice-correction-comparison",
            params={"audio_attachment_id": str(audio_id)},
        )
        assert comparison.status_code == 200, comparison.text
        payload = comparison.json()
        assert payload["status"] == "awaiting_staff_approval"
        assert payload["stages"][-1]["text"] is None
        assert payload["safety"]["external_transfer"] is False

        with SessionLocal() as db:
            after = db.scalar(select(func.count(OcrCorrectionEvent.id)))
            assert after == before


def test_comparison_endpoint_returns_rules_first_then_optional_internal_refinement(
    monkeypatch,
):
    calls = 0

    def refine(**_kwargs):
        nonlocal calls
        calls += 1
        return {
            "final_text": OCR_TEXT.replace("제공함", "제공했습니다"),
            "provider": "ollama",
            "model": "internal-test-model",
        }

    monkeypatch.setattr(main_module, "refine_handwriting_with_internal_ai", refine)
    with TestClient(app) as client:
        _login_admin(client)
        image_id, audio_id = _prepare_completed_pair(client, monkeypatch)
        route = f"/api/attachments/{image_id}/voice-correction-comparison"

        immediate = client.get(
            route,
            params={"audio_attachment_id": str(audio_id), "refine": "false"},
        )
        assert immediate.status_code == 200, immediate.text
        assert calls == 0
        assert immediate.json()["stages"][2]["provider"] == "local_evidence_fusion"

        refined = client.get(
            route,
            params={"audio_attachment_id": str(audio_id), "refine": "true"},
        )
        assert refined.status_code == 200, refined.text
        assert calls == 1
        assert refined.json()["stages"][2]["provider"] == "ollama"


def test_staff_approval_is_append_only_idempotent_and_separate_from_records(
    monkeypatch,
):
    with TestClient(app) as client:
        _login_admin(client)
        image_id, audio_id = _prepare_completed_pair(client, monkeypatch)
        with SessionLocal() as db:
            approval_count_before = db.scalar(
                select(func.count(HandwritingCorrectionApproval.id))
            )
        comparison = client.get(
            f"/api/attachments/{image_id}/voice-correction-comparison",
            params={"audio_attachment_id": str(audio_id)},
        )
        assert comparison.status_code == 200, comparison.text
        suggestion = next(
            stage["text"]
            for stage in comparison.json()["stages"]
            if stage["stage"] == "ai_correction_suggestion"
        )
        proposed = correction_sentences(suggestion)

        def sentence_payload(final_values=None):
            finals = final_values or proposed
            return [
                {
                    "sentence_no": index,
                    "source_text": value,
                    "proposed_text": value,
                    "final_text": finals[index - 1],
                    "action": (
                        "accepted" if finals[index - 1] == value else "edited"
                    ),
                    "approved": True,
                    "evidence_refs": [
                        f"attachment:{image_id}",
                        f"attachment:{audio_id}",
                    ],
                }
                for index, value in enumerate(proposed, start=1)
            ]

        partial_request = {
            "audio_attachment_id": str(audio_id),
            "mode": "full_reading",
            "idempotency_key": str(uuid4()),
            "sentences": sentence_payload()[:2],
            "conflicts_confirmed": True,
        }
        partial = client.post(
            f"/api/attachments/{image_id}/voice-correction-approvals",
            json=partial_request,
            headers=ORIGIN,
        )
        assert partial.status_code == 409, partial.text
        with SessionLocal() as db:
            assert db.scalar(select(func.count(HandwritingCorrectionApproval.id))) == approval_count_before

        first_key = str(uuid4())
        first_request = {
            "audio_attachment_id": str(audio_id),
            "mode": "full_reading",
            "idempotency_key": first_key,
            "sentences": sentence_payload(),
            "conflicts_confirmed": True,
        }
        first = client.post(
            f"/api/attachments/{image_id}/voice-correction-approvals",
            json=first_request,
            headers=ORIGIN,
        )
        assert first.status_code == 201, first.text
        first_payload = first.json()
        assert first_payload["status"] == "approved"
        assert first_payload["current_revision"] == 1
        assert len(first_payload["versions"]) == 1
        assert first_payload["versions"][0]["approved_final_text"] == "\n".join(
            proposed
        )
        assert first_payload["versions"][0]["initial_ocr"] == OCR_TEXT
        assert first_payload["versions"][0]["audio_or_explanation_text"] == TRANSCRIPT
        assert first_payload["versions"][0]["mode"] == "full_reading"
        assert first_payload["versions"][0]["evidence_refs"] == [
            f"attachment:{image_id}",
            f"attachment:{audio_id}",
        ]
        assert first_payload["versions"][0]["official_record_saved"] is False
        assert first_payload["versions"][0]["training_data_adopted"] is False
        assert first_payload["versions"][0]["external_transfer_allowed"] is False

        repeated = client.post(
            f"/api/attachments/{image_id}/voice-correction-approvals",
            json=first_request,
            headers=ORIGIN,
        )
        assert repeated.status_code == 201, repeated.text
        assert len(repeated.json()["versions"]) == 1

        edited = list(proposed)
        edited[-1] = f"{edited[-1]} 직원 확인"
        second_request = {
            "audio_attachment_id": str(audio_id),
            "mode": "full_reading",
            "idempotency_key": str(uuid4()),
            "sentences": sentence_payload(edited),
            "conflicts_confirmed": True,
            "supersedes_approval_id": first_payload["versions"][0]["id"],
        }
        second = client.post(
            f"/api/attachments/{image_id}/voice-correction-approvals",
            json=second_request,
            headers=ORIGIN,
        )
        assert second.status_code == 201, second.text
        second_payload = second.json()
        assert second_payload["current_revision"] == 2
        assert len(second_payload["versions"]) == 2
        assert second_payload["versions"][0]["approved_final_text"] == "\n".join(
            proposed
        )
        assert second_payload["versions"][1]["approved_final_text"] == "\n".join(
            edited
        )
        assert second_payload["versions"][1]["sentence_decisions"][-1][
            "action"
        ] == "edited"
        assert second_payload["versions"][1]["sentence_decisions"][-1][
            "final_text"
        ] == edited[-1]
        assert second_payload["versions"][1]["supersedes_approval_id"] == (
            first_payload["versions"][0]["id"]
        )

        with SessionLocal() as db:
            rows = list(
                db.scalars(
                    select(HandwritingCorrectionApproval).where(
                        HandwritingCorrectionApproval.image_attachment_id == image_id
                    ).order_by(
                        HandwritingCorrectionApproval.revision
                    )
                )
            )
            assert len(rows) == 2
            assert rows[0].approved_final_text != rows[1].approved_final_text
            assert rows[0].official_record_saved is False
            assert rows[0].training_data_adopted is False
            assert rows[0].external_transfer_allowed is False


def test_voice_approval_uses_staff_final_text_to_confirm_multiple_residents(monkeypatch):
    with TestClient(app) as client:
        _login_admin(client)
        image_id, audio_id = _prepare_completed_pair(client, monkeypatch)
        with SessionLocal() as db:
            image = db.get(MessageAttachment, image_id)
            residents = [
                Resident(
                    organization_id=image.organization_id,
                    internal_code=f"VOICE-FINAL-{uuid4().hex[:8]}",
                    display_name=f"합성확정{suffix}{uuid4().hex[:5]}",
                    service_type="facility",
                    is_test_data=True,
                )
                for suffix in ("가", "나")
            ]
            db.add_all(residents);db.commit()
            resident_ids=[resident.id for resident in residents]
            names=[resident.display_name for resident in residents]
            message_id=image.message_id
        final_text=f"{names[0]} 어르신과 {names[1]} 어르신을 함께 확인했습니다."
        saved=client.post(
            f"/api/attachments/{image_id}/voice-correction-approvals",
            json={
                "audio_attachment_id":str(audio_id),
                "mode":"full_reading",
                "idempotency_key":str(uuid4()),
                "conflicts_confirmed":True,
                "sentences":[{
                    "sentence_no":1,
                    "source_text":OCR_TEXT,
                    "proposed_text":"합성 최종 제안",
                    "final_text":final_text,
                    "action":"edited",
                    "approved":True,
                    "evidence_refs":[f"attachment:{image_id}",f"attachment:{audio_id}"],
                }],
            },
            headers=ORIGIN,
        )
        assert saved.status_code==201,saved.text
        assert saved.json()["resident_link_revision"]>=1
        with SessionLocal() as db:
            image=db.get(MessageAttachment,image_id)
            assert image.text_extraction.status=="reviewed"
            assert image.text_extraction.reviewed_text==final_text
            links=list(db.scalars(select(MessageResidentLink).where(
                MessageResidentLink.message_id==message_id,
                MessageResidentLink.status=="confirmed",
            )))
            assert {link.resident_id for link in links}==set(resident_ids)


def test_whole_document_edit_saves_without_sentence_count_or_coordinate_block(
    monkeypatch,
):
    with TestClient(app) as client:
        _login_admin(client)
        image_id, audio_id = _prepare_completed_pair(client, monkeypatch)
        comparison = client.get(
            f"/api/attachments/{image_id}/voice-correction-comparison",
            params={"audio_attachment_id": str(audio_id)},
        )
        assert comparison.status_code == 200, comparison.text
        proposal = next(
            stage["text"]
            for stage in comparison.json()["stages"]
            if stage["stage"] == "ai_correction_suggestion"
        )
        edited = "오후 2시 10분 물 200mL 제공. 오후 4시 상태 확인 예정."

        def request(final_text: str, *, supersedes: str | None = None):
            return {
                "audio_attachment_id": str(audio_id),
                "mode": "full_reading",
                "idempotency_key": str(uuid4()),
                "sentences": [
                    {
                        "sentence_no": 1,
                        "source_text": OCR_TEXT,
                        "proposed_text": proposal,
                        "final_text": final_text,
                        "action": "edited" if final_text != proposal else "accepted",
                        "approved": True,
                        "evidence_refs": [
                            f"attachment:{image_id}",
                            f"attachment:{audio_id}",
                        ],
                        "coordinate_region_ids": [],
                    }
                ],
                "conflicts_confirmed": True,
                "supersedes_approval_id": supersedes,
            }

        first = client.post(
            f"/api/attachments/{image_id}/voice-correction-approvals",
            json=request(edited),
            headers=ORIGIN,
        )
        assert first.status_code == 201, first.text
        first_version = first.json()["versions"][0]
        assert first_version["approved_final_text"] == edited
        assert first_version["confidence_state"]["approval_scope"] == "whole_document"
        assert first_version["confidence_state"]["confirmed_coordinate_region_count"] == 0
        assert first_version["sentence_decisions"][0]["source_text"] == OCR_TEXT

        revised = edited.replace("상태 확인 예정", "섭취 상태 확인 예정")
        second = client.post(
            f"/api/attachments/{image_id}/voice-correction-approvals",
            json=request(revised, supersedes=first_version["id"]),
            headers=ORIGIN,
        )
        assert second.status_code == 201, second.text
        history = client.get(
            f"/api/attachments/{image_id}/voice-correction-approvals",
            params={
                "audio_attachment_id": str(audio_id),
                "mode": "full_reading",
            },
        )
        assert history.status_code == 200, history.text
        versions = history.json()["versions"]
        assert [item["approved_final_text"] for item in versions] == [edited, revised]
        assert all(item["official_record_saved"] is False for item in versions)
        assert all(item["training_data_adopted"] is False for item in versions)
        assert all(item["external_transfer_allowed"] is False for item in versions)


def test_staff_approval_preserves_only_confirmed_coordinate_evidence(monkeypatch):
    with TestClient(app) as client:
        _login_admin(client)
        image_id, _audio_id = _prepare_completed_pair(client, monkeypatch)
        proposed = correction_sentences(OCR_TEXT)
        confirmed_region_id = "confirmed-line-1"
        unconfirmed_region_id = "needs-position-line-2"

        with SessionLocal() as db:
            image = db.get(MessageAttachment, image_id)
            owner = db.scalar(select(User).where(User.username == "admin"))
            extraction = db.scalar(
                select(AttachmentTextExtraction).where(
                    AttachmentTextExtraction.attachment_id == image_id
                )
            )
            assert image is not None
            assert owner is not None
            assert extraction is not None
            db.add(
                AttachmentCoordinateReview(
                    organization_id=owner.organization_id,
                    attachment_id=image_id,
                    extraction_id=extraction.id,
                    version_number=1,
                    image_width=1000,
                    image_height=1400,
                    editor_version="voice-correction-link-1",
                    document_template=None,
                    regions=[
                        {
                            "client_id": confirmed_region_id,
                            "bbox": {
                                "left": 0.1,
                                "top": 0.1,
                                "width": 0.6,
                                "height": 0.08,
                                "rotation_degrees": 0,
                            },
                            "raw_text": proposed[0],
                            "corrected_text": proposed[0],
                            "role": "general",
                            "position_confidence": 1,
                            "review_required": False,
                            "placement_status": "confirmed",
                            "source": "manual",
                        },
                        {
                            "client_id": unconfirmed_region_id,
                            "bbox": {
                                "left": 0.1,
                                "top": 0.25,
                                "width": 0.6,
                                "height": 0.08,
                                "rotation_degrees": 0,
                            },
                            "raw_text": proposed[1],
                            "corrected_text": proposed[1],
                            "role": "general",
                            "position_confidence": 0,
                            "review_required": True,
                            "placement_status": "needs_position",
                            "source": "manual",
                        },
                    ],
                    editor_id=owner.id,
                )
            )
            db.commit()

        def approval_sentences(region_id: str):
            return [
                {
                    "sentence_no": index,
                    "source_text": sentence,
                    "proposed_text": sentence,
                    "final_text": sentence,
                    "action": "accepted",
                    "approved": True,
                    "evidence_refs": [f"attachment:{image_id}"],
                    "coordinate_region_ids": [region_id] if index == 1 else [],
                }
                for index, sentence in enumerate(proposed, start=1)
            ]

        saved = client.post(
            f"/api/attachments/{image_id}/voice-correction-approvals",
            json={
                "mode": "direct_typing",
                "idempotency_key": str(uuid4()),
                "sentences": approval_sentences(confirmed_region_id),
            },
            headers=ORIGIN,
        )
        assert saved.status_code == 201, saved.text
        version = saved.json()["versions"][0]
        decision = version["sentence_decisions"][0]
        assert decision["coordinate_region_ids"] == [confirmed_region_id]
        assert decision["coordinate_evidence"][0]["placement_status"] == "confirmed"
        assert decision["coordinate_evidence"][0]["raw_text"] == proposed[0]
        assert version["confidence_state"]["confirmed_coordinate_region_count"] == 1

        blocked = client.post(
            f"/api/attachments/{image_id}/voice-correction-approvals",
            json={
                "mode": "direct_typing",
                "idempotency_key": str(uuid4()),
                "sentences": approval_sentences(unconfirmed_region_id),
            },
            headers=ORIGIN,
        )
        assert blocked.status_code == 409, blocked.text
        assert "확인되지 않은 위치" in blocked.json()["detail"]


def test_conflict_requires_explicit_confirmation_and_other_user_cannot_revise(
    monkeypatch,
):
    with TestClient(app) as owner_client:
        _login_admin(owner_client)
        image_id, audio_id = _prepare_completed_pair(owner_client, monkeypatch)
        comparison = owner_client.get(
            f"/api/attachments/{image_id}/voice-correction-comparison",
            params={"audio_attachment_id": str(audio_id)},
        ).json()
        proposal = correction_sentences(
            next(
                stage["text"]
                for stage in comparison["stages"]
                if stage["stage"] == "ai_correction_suggestion"
            )
        )
        sentences = [
            {
                "sentence_no": index,
                "source_text": value,
                "proposed_text": value,
                "final_text": value,
                "action": "accepted",
                "approved": True,
                "evidence_refs": [],
            }
            for index, value in enumerate(proposal, start=1)
        ]
        blocked = owner_client.post(
            f"/api/attachments/{image_id}/voice-correction-approvals",
            json={
                "audio_attachment_id": str(audio_id),
                "mode": "full_reading",
                "idempotency_key": str(uuid4()),
                "sentences": sentences,
                "conflicts_confirmed": False,
            },
            headers=ORIGIN,
        )
        assert blocked.status_code == 409, blocked.text

        saved = owner_client.post(
            f"/api/attachments/{image_id}/voice-correction-approvals",
            json={
                "audio_attachment_id": str(audio_id),
                "mode": "full_reading",
                "idempotency_key": str(uuid4()),
                "sentences": sentences,
                "conflicts_confirmed": True,
            },
            headers=ORIGIN,
        )
        assert saved.status_code == 201, saved.text
        saved_id = saved.json()["versions"][0]["id"]

        with SessionLocal() as db:
            owner = db.scalar(select(User).where(User.username == "admin"))
            assert owner is not None
            other_staff = Staff(
                organization_id=owner.organization_id,
                internal_code=f"handwriting-approver-{uuid4().hex[:8]}",
                display_name="다른 가상 관리자",
                job_title="시험",
                employment_status="active",
                is_test_data=True,
                is_active=True,
            )
            db.add(other_staff)
            db.flush()
            other = User(
                organization_id=owner.organization_id,
                staff_id=other_staff.id,
                username=f"other_{uuid4().hex[:8]}",
                display_name="다른 가상 관리자",
                password_hash=hash_password("OtherPass!234"),
                is_active=True,
                can_process_records=True,
            )
            other.roles = list(owner.roles)
            db.add(other)
            db.commit()
            other_username = other.username

        with TestClient(app) as other_client:
            login = other_client.post(
                "/api/auth/login",
                json={"username": other_username, "password": "OtherPass!234"},
                headers=ORIGIN,
            )
            assert login.status_code == 200, login.text
            forbidden = other_client.post(
                f"/api/attachments/{image_id}/voice-correction-approvals",
                json={
                    "audio_attachment_id": str(audio_id),
                    "mode": "full_reading",
                    "idempotency_key": str(uuid4()),
                    "sentences": sentences,
                    "conflicts_confirmed": True,
                    "supersedes_approval_id": saved_id,
                },
                headers=ORIGIN,
            )
            assert forbidden.status_code == 403, forbidden.text

        with SessionLocal() as db:
            assert (
                db.scalar(
                    select(func.count(HandwritingCorrectionApproval.id)).where(
                        HandwritingCorrectionApproval.image_attachment_id == image_id
                    )
                )
                == 1
            )
