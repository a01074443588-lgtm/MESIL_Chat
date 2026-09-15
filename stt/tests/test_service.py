from __future__ import annotations

from dataclasses import dataclass
import io
import importlib
import os
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import wave

from fastapi.testclient import TestClient


os.environ.setdefault("STT_SHARED_TOKEN", "test-local-stt-token-123456789")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
service = importlib.import_module("app")


@dataclass
class Segment:
    text: str


class FakeModel:
    def transcribe(self, *_args, **_kwargs):
        return iter([Segment(" 첫 문장 "), Segment("둘째 문장")]), SimpleNamespace(
            language="ko"
        )


class RecordingModel(FakeModel):
    def __init__(self):
        self.kwargs = {}

    def transcribe(self, *_args, **kwargs):
        self.kwargs = kwargs
        return super().transcribe()


class CudaFloat16Model(FakeModel):
    model = SimpleNamespace(
        device="cuda",
        compute_type="float16",
        device_index=[0],
    )


def wav_bytes(*, amplitude: int = 1_000, seconds: float = 0.25) -> bytes:
    output = io.BytesIO()
    sample_count = int(16_000 * seconds)
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16_000)
        target.writeframes(
            b"".join(struct.pack("<h", amplitude) for _ in range(sample_count))
        )
    return output.getvalue()


def test_health_and_transcribe_contract(monkeypatch):
    monkeypatch.setattr(service, "_model", FakeModel())
    with TestClient(service.app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["ok"] is True
        assert health.json()["loaded"] is True
        assert health.json()["runtime_verified"] is False

        denied = client.post(
            "/transcribe",
            files={"file": ("voice.wav", b"not-empty", "audio/wav")},
        )
        assert denied.status_code == 401

        accepted = client.post(
            "/transcribe",
            headers={"X-STT-Token": os.environ["STT_SHARED_TOKEN"]},
            files={"file": ("voice.wav", wav_bytes(), "audio/wav")},
        )
        assert accepted.status_code == 200
        assert accepted.json()["text"] == "첫 문장 둘째 문장"
        assert accepted.json()["language"] == "ko"
        assert accepted.json()["normalization"] == {
            "normalized": True,
            "sample_rate": 16000,
            "channels": 1,
            "duration_seconds": 0.25,
            "peak_ratio": 0.0305,
            "rms_dbfs": -30.31,
            "silence_ratio": 0.0,
            "clipped_ratio": 0.0,
        }
        assert accepted.json()["audio_quality"]["reading_pace"] == "fast"
        assert accepted.json()["audio_quality"]["status"] == "low"
        assert "다시 녹음" in accepted.json()["audio_quality"]["message"]


def test_health_reports_loaded_engine_not_configured_labels(monkeypatch):
    monkeypatch.setattr(service, "_model", CudaFloat16Model())
    monkeypatch.setattr(service, "DEVICE", "cuda")
    monkeypatch.setattr(service, "COMPUTE_TYPE", "float16")

    with TestClient(service.app) as client:
        health = client.get("/health").json()

    assert health["device"] == "cuda"
    assert health["compute_type"] == "float16"
    assert health["runtime_verified"] is True
    assert health["configured_device"] == "cuda"
    assert health["configured_compute_type"] == "float16"
    assert health["num_workers"] == 1


def test_cuda_float16_startup_rejects_silent_cpu_fallback(monkeypatch):
    monkeypatch.setattr(service, "DEVICE", "cuda")
    monkeypatch.setattr(service, "COMPUTE_TYPE", "float16")
    monkeypatch.setattr(
        service,
        "_model",
        SimpleNamespace(
            model=SimpleNamespace(device="cpu", compute_type="float32")
        ),
    )

    try:
        service._require_requested_gpu_runtime()
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("A CUDA request must not report a CPU engine as ready")

    assert "CUDA" in message


def test_empty_audio_is_rejected(monkeypatch):
    monkeypatch.setattr(service, "_model", FakeModel())
    with TestClient(service.app) as client:
        response = client.post(
            "/transcribe",
            headers={"X-STT-Token": os.environ["STT_SHARED_TOKEN"]},
            files={"file": ("voice.wav", b"", "audio/wav")},
        )
        assert response.status_code == 422


def test_silent_audio_is_rejected_with_recovery_message(monkeypatch):
    monkeypatch.setattr(service, "_model", FakeModel())
    with TestClient(service.app) as client:
        response = client.post(
            "/transcribe",
            headers={"X-STT-Token": os.environ["STT_SHARED_TOKEN"]},
            files={"file": ("silent.wav", wav_bytes(amplitude=0), "audio/wav")},
        )

    assert response.status_code == 422
    assert "마이크를 확인하고 다시 녹음" in response.json()["detail"]


def test_damaged_webm_is_rejected_without_decoder_details(monkeypatch):
    monkeypatch.setattr(service, "_model", FakeModel())
    with TestClient(service.app) as client:
        response = client.post(
            "/transcribe",
            headers={"X-STT-Token": os.environ["STT_SHARED_TOKEN"]},
            files={"file": ("damaged.webm", b"not-a-webm-container", "audio/webm")},
        )

    assert response.status_code == 422
    assert "다시 녹음" in response.json()["detail"]
    assert "Opus" not in response.text
    assert "http://" not in response.text


def test_supported_webm_crosses_normalization_gate_once(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "_model", FakeModel())
    calls: list[tuple[str, str]] = []

    def fake_normalize(source_path: Path, target_path: Path):
        calls.append((source_path.suffix, target_path.suffix))
        target_path.write_bytes(wav_bytes())
        return {
            "normalized": True,
            "sample_rate": 16_000,
            "channels": 1,
            "duration_seconds": 0.25,
        }

    monkeypatch.setattr(service, "_normalize_audio_to_wav", fake_normalize)
    with TestClient(service.app) as client:
        response = client.post(
            "/transcribe",
            headers={"X-STT-Token": os.environ["STT_SHARED_TOKEN"]},
            files={"file": ("voice.webm", b"\x1a\x45\xdf\xa3webm", "audio/webm")},
        )

    assert response.status_code == 200
    assert calls == [(".webm", ".wav")]


def test_initial_prompt_takes_priority_when_hotwords_are_also_sent(monkeypatch):
    model = RecordingModel()
    monkeypatch.setattr(service, "_model", model)
    with TestClient(service.app) as client:
        response = client.post(
            "/transcribe",
            headers={"X-STT-Token": os.environ["STT_SHARED_TOKEN"]},
            files={"file": ("voice.wav", wav_bytes(), "audio/wav")},
            data={
                "initial_prompt": "long-term-care context",
                "hotwords": "resident names, care terms",
            },
        )

    assert response.status_code == 200
    assert model.kwargs["initial_prompt"] == "long-term-care context"
    assert model.kwargs["hotwords"] is None


def test_same_sentence_pace_classification_compares_fast_normal_and_slow():
    text = "오후 2시에 물 200밀리리터를 제공했습니다"
    base = {
        "rms_dbfs": -24.0,
        "silence_ratio": 0.1,
        "clipped_ratio": 0.0,
    }
    segment_metrics = {
        "average_log_probability": -0.2,
        "average_no_speech_probability": 0.05,
        "maximum_compression_ratio": 1.2,
    }
    fast = service.classify_audio_quality(
        text=text,
        normalization={**base, "duration_seconds": 2.0},
        segment_metrics=segment_metrics,
    )
    normal = service.classify_audio_quality(
        text=text,
        normalization={**base, "duration_seconds": 5.0},
        segment_metrics=segment_metrics,
    )
    slow = service.classify_audio_quality(
        text=text,
        normalization={**base, "duration_seconds": 18.0},
        segment_metrics=segment_metrics,
    )

    assert [fast["reading_pace"], normal["reading_pace"], slow["reading_pace"]] == [
        "fast",
        "normal",
        "slow",
    ]
    assert fast["status"] == "low"
    assert normal["status"] == "good"
    assert slow["status"] == "good"
