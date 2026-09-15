from __future__ import annotations

from contextlib import asynccontextmanager
import hmac
import math
import os
from pathlib import Path
import re
import tempfile
from threading import Lock
from time import monotonic
from typing import Any, Iterator
import wave

import av

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from faster_whisper import WhisperModel


MODEL_NAME = os.environ.get("STT_MODEL", "large-v3-turbo").strip()
MODEL_ROOT = Path(os.environ.get("STT_MODEL_ROOT", "/models")).resolve()
SHARED_TOKEN = os.environ.get("STT_SHARED_TOKEN", "")
MAX_BYTES = int(os.environ.get("MAX_ATTACHMENT_BYTES", str(30 * 1024 * 1024)))
MAX_INITIAL_PROMPT_CHARS = 6_000
MAX_HOTWORDS_CHARS = 4_000
DEVICE = os.environ.get("STT_DEVICE", "cpu").strip()
COMPUTE_TYPE = os.environ.get("STT_COMPUTE_TYPE", "int8").strip()
CPU_THREADS = int(os.environ.get("STT_CPU_THREADS", "16"))
NUM_WORKERS = int(os.environ.get("STT_NUM_WORKERS", "1"))
BEAM_SIZE = int(os.environ.get("STT_BEAM_SIZE", "5"))
LANGUAGE = os.environ.get("STT_LANGUAGE", "ko").strip() or "ko"
LOCAL_FILES_ONLY = os.environ.get("STT_LOCAL_FILES_ONLY", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
VAD_FILTER = os.environ.get("STT_VAD_FILTER", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

_model: WhisperModel | None = None
_model_lock = Lock()
_inference_lock = Lock()
_REPEATED_PHRASE = re.compile(r"(?P<phrase>[가-힣A-Za-z0-9]{2,20})(?:\s+\1){2,}")


class AudioNormalizationError(ValueError):
    """A user-recoverable local audio decoding or quality failure."""

    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


def _normalize_audio_to_wav(source_path: Path, target_path: Path) -> dict[str, object]:
    """Decode browser audio and write a bounded 16 kHz mono PCM WAV.

    Browser MediaRecorder containers vary by Android/Chrome version.  The
    Whisper decoder must never receive a mislabeled or partly concatenated
    container directly, so every accepted format crosses this local gate.
    """

    try:
        container = av.open(str(source_path), mode="r")
    except Exception as exc:
        raise AudioNormalizationError(
            "damaged",
            "녹음 파일 형식을 확인하지 못했습니다. 다시 녹음해 주세요.",
        ) from exc

    total_samples = 0
    peak_amplitude = 0
    squared_sum = 0.0
    silence_samples = 0
    clipped_samples = 0
    try:
        audio_stream = next(
            (stream for stream in container.streams if stream.type == "audio"),
            None,
        )
        if audio_stream is None:
            raise AudioNormalizationError(
                "damaged",
                "녹음 파일에서 소리 정보를 찾지 못했습니다. 다시 녹음해 주세요.",
            )
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16_000)
        with wave.open(str(target_path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16_000)

            for decoded in container.decode(audio_stream):
                for frame in resampler.resample(decoded):
                    samples = frame.to_ndarray().reshape(-1)
                    if samples.size == 0:
                        continue
                    peak_amplitude = max(
                        peak_amplitude,
                        int(max(abs(int(samples.min())), abs(int(samples.max())))),
                    )
                    wide = samples.astype("int32", copy=False)
                    absolute = abs(wide)
                    squared_sum += float((wide * wide).sum())
                    silence_samples += int((absolute <= 327).sum())
                    clipped_samples += int((absolute >= 32_700).sum())
                    output.writeframes(samples.astype("<i2", copy=False).tobytes())
                    total_samples += int(samples.size)
            for frame in resampler.resample(None):
                samples = frame.to_ndarray().reshape(-1)
                if samples.size == 0:
                    continue
                peak_amplitude = max(
                    peak_amplitude,
                    int(max(abs(int(samples.min())), abs(int(samples.max())))),
                )
                wide = samples.astype("int32", copy=False)
                absolute = abs(wide)
                squared_sum += float((wide * wide).sum())
                silence_samples += int((absolute <= 327).sum())
                clipped_samples += int((absolute >= 32_700).sum())
                output.writeframes(samples.astype("<i2", copy=False).tobytes())
                total_samples += int(samples.size)
    except AudioNormalizationError:
        raise
    except Exception as exc:
        raise AudioNormalizationError(
            "damaged",
            "녹음 파일을 안전한 음성 형식으로 바꾸지 못했습니다. 다시 녹음해 주세요.",
        ) from exc
    finally:
        container.close()

    if total_samples < 1_600:
        raise AudioNormalizationError(
            "empty",
            "녹음된 내용이 너무 짧습니다. 한 문장 이상 다시 녹음해 주세요.",
        )
    if peak_amplitude < 64:
        raise AudioNormalizationError(
            "silent",
            "녹음에서 들리는 말소리를 찾지 못했습니다. 마이크를 확인하고 다시 녹음해 주세요.",
        )
    rms_amplitude = math.sqrt(squared_sum / total_samples)
    rms_dbfs = 20 * math.log10(max(rms_amplitude, 1) / 32_768)
    return {
        "normalized": True,
        "sample_rate": 16_000,
        "channels": 1,
        "duration_seconds": round(total_samples / 16_000, 3),
        "peak_ratio": round(peak_amplitude / 32_768, 4),
        "rms_dbfs": round(rms_dbfs, 2),
        "silence_ratio": round(silence_samples / total_samples, 4),
        "clipped_ratio": round(clipped_samples / total_samples, 4),
    }


def classify_audio_quality(
    *,
    text: str,
    normalization: dict[str, object],
    segment_metrics: dict[str, float | None],
) -> dict[str, object]:
    """Return a small staff-facing quality gate plus non-secret diagnostics."""

    duration = float(normalization.get("duration_seconds") or 0)
    character_count = len(re.sub(r"\s+", "", text))
    characters_per_second = character_count / duration if duration > 0 else 0
    if characters_per_second > 5.5:
        reading_pace = "fast"
    elif 0 < characters_per_second < 1.2:
        reading_pace = "slow"
    elif characters_per_second > 0:
        reading_pace = "normal"
    else:
        reading_pace = "unknown"

    reasons: list[str] = []
    if float(normalization.get("rms_dbfs") or -120) < -38:
        reasons.append("low_volume")
    if float(normalization.get("silence_ratio") or 0) > 0.68:
        reasons.append("high_silence")
    if float(normalization.get("clipped_ratio") or 0) > 0.02:
        reasons.append("clipped_audio")
    average_log_probability = segment_metrics.get("average_log_probability")
    if average_log_probability is not None and average_log_probability < -1.0:
        reasons.append("low_transcription_confidence")
    average_no_speech_probability = segment_metrics.get(
        "average_no_speech_probability"
    )
    if (
        average_no_speech_probability is not None
        and average_no_speech_probability > 0.6
    ):
        reasons.append("speech_uncertain")
    if _REPEATED_PHRASE.search(text):
        reasons.append("repeated_transcript")
    if reading_pace == "fast":
        reasons.append("fast_reading")

    status = "low" if reasons else "good"
    return {
        "status": status,
        "reading_pace": reading_pace,
        "message": (
            "음성이 작거나 불분명합니다. 원본과 다른 부분을 확인하거나 다시 녹음해 주세요."
            if status == "low"
            else "음성 전사가 준비됐습니다."
        ),
        "reasons": reasons,
        "metrics": {
            "audio_duration_seconds": round(duration, 3),
            "characters_per_second": round(characters_per_second, 3),
            "rms_dbfs": normalization.get("rms_dbfs"),
            "silence_ratio": normalization.get("silence_ratio"),
            "clipped_ratio": normalization.get("clipped_ratio"),
            **segment_metrics,
        },
    }


def _validate_settings() -> None:
    if not MODEL_NAME:
        raise RuntimeError("STT_MODEL is required")
    if len(SHARED_TOKEN) < 24:
        raise RuntimeError("STT_SHARED_TOKEN must contain at least 24 characters")
    if MAX_BYTES < 1024:
        raise RuntimeError("MAX_ATTACHMENT_BYTES is too small")
    if CPU_THREADS < 1 or NUM_WORKERS < 1 or BEAM_SIZE < 1:
        raise RuntimeError("STT worker settings must be positive")
    if DEVICE not in {"cpu", "cuda"}:
        raise RuntimeError("STT_DEVICE must be cpu or cuda")


def _runtime_engine() -> dict[str, object]:
    engine = getattr(_model, "model", None)
    actual_device = getattr(engine, "device", None)
    actual_compute_type = getattr(engine, "compute_type", None)
    return {
        "device": str(actual_device) if actual_device is not None else None,
        "compute_type": (
            str(actual_compute_type) if actual_compute_type is not None else None
        ),
        "runtime_verified": bool(
            actual_device is not None
            and str(actual_device) == DEVICE
            and actual_compute_type is not None
            and (
                str(actual_compute_type) == COMPUTE_TYPE
                or (
                    DEVICE == "cpu"
                    and COMPUTE_TYPE == "int8"
                    and str(actual_compute_type).startswith("int8")
                )
            )
        ),
    }


def _require_requested_gpu_runtime() -> None:
    runtime = _runtime_engine()
    if DEVICE == "cuda" and runtime["device"] != "cuda":
        raise RuntimeError("WhisperModel did not load on the requested CUDA device")
    if (
        DEVICE == "cuda"
        and COMPUTE_TYPE == "float16"
        and runtime["compute_type"] != "float16"
    ):
        raise RuntimeError("WhisperModel did not load with the requested float16 compute type")


def load_model() -> WhisperModel:
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is None:
            MODEL_ROOT.mkdir(parents=True, exist_ok=True)
            _model = WhisperModel(
                MODEL_NAME,
                device=DEVICE,
                compute_type=COMPUTE_TYPE,
                cpu_threads=CPU_THREADS,
                num_workers=NUM_WORKERS,
                download_root=str(MODEL_ROOT),
                local_files_only=LOCAL_FILES_ONLY,
            )
            _require_requested_gpu_runtime()
    return _model


@asynccontextmanager
async def lifespan(_: FastAPI) -> Iterator[None]:
    _validate_settings()
    load_model()
    yield


app = FastAPI(
    title="MESIL_Chat local Whisper service",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict[str, object]:
    runtime = _runtime_engine()
    return {
        "ok": True,
        "model": MODEL_NAME,
        "loaded": _model is not None,
        "device": runtime["device"],
        "compute_type": runtime["compute_type"],
        "runtime_verified": runtime["runtime_verified"],
        "configured_device": DEVICE,
        "configured_compute_type": COMPUTE_TYPE,
        "num_workers": NUM_WORKERS,
    }


def _collect_text(
    path: Path,
    *,
    initial_prompt: str | None = None,
    hotwords: str | None = None,
) -> tuple[str, Any, dict[str, float | None]]:
    prompt = (initial_prompt or "").strip() or None
    word_hints = (hotwords or "").strip() or None
    # faster-whisper accepts either an initial prompt or hotwords, but some
    # versions reject both together. The prompt already contains the same
    # resident names and domain terms, so prefer it when both are supplied.
    if prompt is not None:
        word_hints = None
    segments, info = load_model().transcribe(
        str(path),
        language=LANGUAGE,
        task="transcribe",
        beam_size=BEAM_SIZE,
        vad_filter=VAD_FILTER,
        condition_on_previous_text=True,
        word_timestamps=False,
        initial_prompt=prompt,
        hotwords=word_hints,
    )
    texts: list[str] = []
    log_probabilities: list[float] = []
    no_speech_probabilities: list[float] = []
    compression_ratios: list[float] = []
    for segment in segments:
        segment_text = str(segment.text).strip()
        if segment_text:
            texts.append(segment_text)
        for values, attribute in (
            (log_probabilities, "avg_logprob"),
            (no_speech_probabilities, "no_speech_prob"),
            (compression_ratios, "compression_ratio"),
        ):
            value = getattr(segment, attribute, None)
            if isinstance(value, (int, float)):
                values.append(float(value))
    text = " ".join(texts).strip()
    segment_metrics: dict[str, float | None] = {
        "average_log_probability": (
            round(sum(log_probabilities) / len(log_probabilities), 4)
            if log_probabilities
            else None
        ),
        "average_no_speech_probability": (
            round(sum(no_speech_probabilities) / len(no_speech_probabilities), 4)
            if no_speech_probabilities
            else None
        ),
        "maximum_compression_ratio": (
            round(max(compression_ratios), 4) if compression_ratios else None
        ),
    }
    return text, info, segment_metrics


@app.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    initial_prompt: str = Form(default="", max_length=MAX_INITIAL_PROMPT_CHARS),
    hotwords: str = Form(default="", max_length=MAX_HOTWORDS_CHARS),
    x_stt_token: str = Header(default=""),
) -> dict[str, object]:
    if not hmac.compare_digest(x_stt_token, SHARED_TOKEN):
        raise HTTPException(status_code=401, detail="올바르지 않은 음성 판독 연결입니다.")

    suffix = Path(file.filename or "audio").suffix.lower() or ".audio"
    content = await file.read(MAX_BYTES + 1)
    if not content:
        raise HTTPException(status_code=422, detail="빈 음성파일입니다.")
    if len(content) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="음성파일 용량이 너무 큽니다.")

    temp_path: Path | None = None
    normalized_path: Path | None = None
    normalization: dict[str, object] | None = None
    started_at = monotonic()
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as target:
            target.write(content)
            temp_path = Path(target.name)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as target:
            normalized_path = Path(target.name)
        try:
            normalization = _normalize_audio_to_wav(temp_path, normalized_path)
            with _inference_lock:
                text, info, segment_metrics = _collect_text(
                    normalized_path,
                    initial_prompt=initial_prompt.strip(),
                    hotwords=hotwords.strip(),
                )
        except AudioNormalizationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail="음성 받아쓰기를 완료하지 못했습니다. 다시 녹음하거나 관리자에게 알려 주세요.",
            ) from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        if normalized_path is not None:
            normalized_path.unlink(missing_ok=True)

    if not text:
        raise HTTPException(
            status_code=422,
            detail="음성에서 확인할 수 있는 말소리를 찾지 못했습니다.",
        )

    audio_quality = classify_audio_quality(
        text=text,
        normalization=normalization or {},
        segment_metrics=segment_metrics,
    )
    return {
        "text": text,
        "model": MODEL_NAME,
        "language": getattr(info, "language", LANGUAGE),
        "duration_seconds": round(monotonic() - started_at, 3),
        "normalization": normalization,
        "audio_quality": audio_quality,
    }
