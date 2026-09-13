from pathlib import Path

from pydantic import SecretStr

from app import stt
from app.ai_settings_store import default_ai_settings


class _Response:
    is_error = False

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, str]:
        return {"text": "송영 후 어르신 상태를 확인함"}


class _QualityResponse(_Response):
    def json(self):
        return {
            "text": "오후 2시에 물 200mL를 제공함",
            "model": "large-v3-turbo",
            "duration_seconds": 1.25,
            "normalization": {"duration_seconds": 4.5},
            "audio_quality": {
                "status": "good",
                "reading_pace": "normal",
                "message": "음성 전사가 준비됐습니다.",
                "reasons": [],
                "metrics": {"characters_per_second": 3.2},
            },
        }


def test_transcribe_audio_sends_bounded_context_without_changing_result(
    monkeypatch,
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "report.m4a"
    audio_path.write_bytes(b"audio")
    captured: dict[str, object] = {}

    def fake_post(url: str, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return _Response()

    monkeypatch.setattr(stt.settings, "stt_enabled", True)
    monkeypatch.setattr(stt.settings, "stt_shared_token", SecretStr("x" * 32))
    monkeypatch.setattr(stt.settings, "stt_service_url", "http://stt:8766")
    monkeypatch.setattr(
        stt,
        "load_ai_settings",
        lambda: (default_ai_settings(), False),
    )
    monkeypatch.setattr(stt.httpx, "post", fake_post)

    result = stt.transcribe_audio(
        audio_path,
        mime_type="audio/mp4",
        initial_prompt="가" * 6_100,
        hotwords="나" * 4_100,
    )

    assert result == "송영 후 어르신 상태를 확인함"
    assert captured["url"] == "http://stt:8766/transcribe"
    assert captured["data"] == {
        "initial_prompt": "가" * 6_000,
        "hotwords": "나" * 4_000,
    }
    assert captured["files"]["file"][0] == "report.m4a"
    assert captured["files"]["file"][2] == "audio/mp4"


def test_transcribe_audio_uses_protected_local_stt_provider(
    monkeypatch,
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "protected.wav"
    audio_path.write_bytes(b"audio")
    captured: dict[str, object] = {}
    document = default_ai_settings()
    providers = dict(document.providers)
    providers["local_stt"] = providers["local_stt"].model_copy(
        update={
            "enabled": True,
            "base_url": "http://stt-dev:8766",
            "timeout_seconds": 321,
        }
    )
    protected = document.model_copy(update={"providers": providers})

    def fake_post(url: str, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return _Response()

    monkeypatch.setattr(stt, "load_ai_settings", lambda: (protected, True))
    monkeypatch.setattr(stt.settings, "stt_enabled", False)
    monkeypatch.setattr(stt.settings, "stt_shared_token", SecretStr("x" * 32))
    monkeypatch.setattr(stt.httpx, "post", fake_post)

    result = stt.transcribe_audio(audio_path, mime_type="audio/wav")

    assert result == "송영 후 어르신 상태를 확인함"
    assert captured["url"] == "http://stt-dev:8766/transcribe"
    assert captured["timeout"] == 321


def test_transcribe_audio_with_metadata_preserves_internal_quality_signal(
    monkeypatch,
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "quality.wav"
    audio_path.write_bytes(b"audio")
    monkeypatch.setattr(stt.settings, "stt_enabled", True)
    monkeypatch.setattr(stt.settings, "stt_shared_token", SecretStr("x" * 32))
    monkeypatch.setattr(
        stt,
        "load_ai_settings",
        lambda: (default_ai_settings(), False),
    )
    monkeypatch.setattr(stt.httpx, "post", lambda *_args, **_kwargs: _QualityResponse())

    result = stt.transcribe_audio_with_metadata(audio_path, mime_type="audio/wav")

    assert result.text == "오후 2시에 물 200mL를 제공함"
    assert result.model == "large-v3-turbo"
    assert result.processing_seconds == 1.25
    assert result.normalization["duration_seconds"] == 4.5
    assert result.audio_quality["status"] == "good"
    assert result.audio_quality["reading_pace"] == "normal"


def test_transcribe_audio_hides_protected_url_and_decoder_error(
    monkeypatch,
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "broken.webm"
    audio_path.write_bytes(b"broken")

    class ErrorResponse:
        is_error = True
        status_code = 422

        def json(self):
            return {
                "detail": (
                    "Error parsing Opus packet header at "
                    "http://protected-internal-stt/transcribe"
                )
            }

    monkeypatch.setattr(stt.settings, "stt_enabled", True)
    monkeypatch.setattr(stt.settings, "stt_shared_token", SecretStr("x" * 32))
    monkeypatch.setattr(
        stt,
        "load_ai_settings",
        lambda: (default_ai_settings(), False),
    )
    monkeypatch.setattr(stt.httpx, "post", lambda *_args, **_kwargs: ErrorResponse())

    try:
        stt.transcribe_audio(audio_path, mime_type="audio/webm")
    except stt.SttError as exc:
        message = str(exc)
    else:
        raise AssertionError("손상 음성은 실패해야 합니다.")

    assert message == "내부 음성 받아쓰기 요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요."
    assert "http://" not in message
    assert "Opus" not in message
