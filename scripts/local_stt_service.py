from __future__ import annotations

import hmac
import os
from pathlib import Path
import subprocess
import tempfile
from threading import Lock

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
import numpy as np
import torch
from transformers import (
    AutoModelForSpeechSeq2Seq,
    AutoProcessor,
    pipeline as transformers_pipeline,
)
import transformers.pipelines.automatic_speech_recognition as asr_pipeline_module


# 이 설치환경의 torchcodec은 PyTorch와 맞지 않습니다. 음성은 위의 ffmpeg로
# 직접 PCM 배열로 변환하므로 transformers가 torchcodec을 불러올 필요가 없습니다.
asr_pipeline_module.is_torchcodec_available = lambda: False


MODEL_PATH = Path(os.environ["STT_LOCAL_MODEL_PATH"]).resolve()
SHARED_TOKEN = os.environ["STT_SHARED_TOKEN"]
MAX_BYTES = int(os.environ.get("MAX_ATTACHMENT_BYTES", str(30 * 1024 * 1024)))
MODEL_LABEL = os.environ.get("STT_MODEL", "whisper-small")
model_lock = Lock()
speech_pipeline = None

app = FastAPI(title="SMCODI local speech transcription", docs_url=None, redoc_url=None)


def load_pipeline():
    global speech_pipeline
    if speech_pipeline is not None:
        return speech_pipeline
    if not MODEL_PATH.is_dir():
        raise RuntimeError(f"Whisper 모델 폴더를 찾을 수 없습니다: {MODEL_PATH}")

    device = 0 if torch.cuda.is_available() else -1
    dtype = torch.float16 if torch.cuda.is_available() else torch.float32
    model = AutoModelForSpeechSeq2Seq.from_pretrained(
        MODEL_PATH,
        torch_dtype=dtype,
        low_cpu_mem_usage=True,
        use_safetensors=True,
        local_files_only=True,
    )
    if device >= 0:
        model.to("cuda:0")
    processor = AutoProcessor.from_pretrained(
        MODEL_PATH,
        local_files_only=True,
    )
    speech_pipeline = transformers_pipeline(
        "automatic-speech-recognition",
        model=model,
        tokenizer=processor.tokenizer,
        feature_extractor=processor.feature_extractor,
        torch_dtype=dtype,
        device=device,
        chunk_length_s=30,
        batch_size=8,
    )
    return speech_pipeline


def decode_audio(path: Path) -> np.ndarray:
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-ac",
            "1",
            "-ar",
            "16000",
            "pipe:1",
        ],
        check=False,
        capture_output=True,
        timeout=120,
    )
    if result.returncode != 0 or not result.stdout:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise HTTPException(
            status_code=422,
            detail=f"음성파일을 읽을 수 없습니다: {error[:300]}",
        )
    return np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0


@app.on_event("startup")
def warm_up_model() -> None:
    load_pipeline()


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "ok": True,
        "model": MODEL_LABEL,
        "loaded": speech_pipeline is not None,
        "cuda": torch.cuda.is_available(),
    }


@app.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    x_stt_token: str = Header(default=""),
) -> dict[str, str]:
    if not hmac.compare_digest(x_stt_token, SHARED_TOKEN):
        raise HTTPException(status_code=401, detail="올바르지 않은 음성 판독 연결입니다.")

    suffix = Path(file.filename or "audio.wav").suffix.lower() or ".wav"
    content = await file.read(MAX_BYTES + 1)
    if not content:
        raise HTTPException(status_code=422, detail="빈 음성파일입니다.")
    if len(content) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="음성파일 용량이 너무 큽니다.")

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as target:
            target.write(content)
            temp_path = Path(target.name)
        audio = decode_audio(temp_path)
        with model_lock:
            result = load_pipeline()(
                audio,
                generate_kwargs={"language": "korean", "task": "transcribe"},
            )
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    text = str(result.get("text") or "").strip()
    return {"text": text, "model": MODEL_LABEL}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("STT_LOCAL_HOST", "0.0.0.0"),
        port=int(os.environ.get("STT_LOCAL_PORT", "8766")),
        log_level="info",
    )
