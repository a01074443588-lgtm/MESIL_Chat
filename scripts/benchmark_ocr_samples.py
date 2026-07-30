from __future__ import annotations

import argparse
import json
import sys
from tempfile import TemporaryDirectory
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from time import perf_counter

from PIL import Image, ImageOps


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.config import settings  # noqa: E402
from app.ocr import OcrError, extract_report_text  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="로컬 OCR로 가명 또는 승인된 손글씨 표본을 판독합니다."
    )
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "output" / "ocr-sample-results",
    )
    parser.add_argument(
        "--model",
        default=settings.ocr_model,
        help="시험할 로컬 Ollama 비전 모델 이름",
    )
    parser.add_argument(
        "--bands",
        type=int,
        default=1,
        choices=range(1, 5),
        help="세로로 긴 사진을 나눠 판독할 구간 수(1~4)",
    )
    return parser.parse_args()


def extract_in_bands(image_path: Path, *, bands: int) -> str:
    if bands == 1:
        return extract_report_text(
            image_path,
            room_name="손글씨 OCR 성능시험",
            resident_name=None,
        )
    with TemporaryDirectory(prefix="smcodi-ocr-") as temporary:
        image = ImageOps.exif_transpose(Image.open(image_path)).convert("RGB")
        width, height = image.size
        overlap = max(24, height // 50)
        texts: list[str] = []
        for index in range(bands):
            top = max(0, (height * index // bands) - (overlap if index else 0))
            bottom = min(
                height,
                (height * (index + 1) // bands)
                + (overlap if index < bands - 1 else 0),
            )
            crop_path = Path(temporary) / f"band-{index + 1}.jpg"
            image.crop((0, top, width, bottom)).save(
                crop_path,
                format="JPEG",
                quality=92,
            )
            try:
                text = extract_report_text(
                    crop_path,
                    room_name="손글씨 OCR 성능시험",
                    resident_name=None,
                )
            except OcrError as exc:
                if str(exc) == "이미지에서 판독된 글이 없습니다.":
                    continue
                raise
            texts.append(text)
        if not texts:
            raise OcrError("이미지에서 판독된 글이 없습니다.")
        return "\n".join(texts)


def main() -> None:
    args = parse_args()
    settings.ocr_model = args.model
    # 성능시험 스크립트가 직접 구간을 만들므로 앱 기본 구간 분할을 중복 적용하지 않는다.
    settings.ocr_image_bands = 1
    missing = [path for path in args.images if not path.is_file()]
    if missing:
        raise SystemExit(f"입력 이미지를 찾을 수 없습니다: {len(missing)}개")
    args.output.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    for index, image_path in enumerate(args.images, start=1):
        started = perf_counter()
        digest = sha256(image_path.read_bytes()).hexdigest()
        record: dict = {
            "sample": index,
            "sha256": digest,
            "source_name": image_path.name,
        }
        try:
            text = extract_in_bands(image_path, bands=args.bands)
        except OcrError as exc:
            record.update(
                {
                    "status": "failed",
                    "error": str(exc),
                    "elapsed_seconds": round(perf_counter() - started, 2),
                }
            )
        else:
            output_path = args.output / f"sample-{index:02d}.txt"
            output_path.write_text(text, encoding="utf-8")
            korean_count = sum("가" <= character <= "힣" for character in text)
            record.update(
                {
                    "status": "completed",
                    "elapsed_seconds": round(perf_counter() - started, 2),
                    "characters": len(text),
                    "lines": len(text.splitlines()),
                    "korean_characters": korean_count,
                    "result_file": output_path.name,
                }
            )
        results.append(record)
        print(
            f"표본 {index}: {record['status']} · "
            f"{record['elapsed_seconds']}초 · "
            f"{record.get('characters', 0)}자"
        )
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": "ollama",
        "model": args.model,
        "bands": args.bands,
        "results": results,
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
