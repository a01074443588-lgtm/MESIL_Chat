from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from time import perf_counter

import torch
from nemotron_ocr.inference.pipeline_v2 import NemotronOCRV2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="로컬 Nemotron OCR v2 다국어판으로 승인된 표본을 판독합니다."
    )
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--merge-level",
        choices=["word", "sentence", "paragraph"],
        default="paragraph",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    missing = [path for path in args.images if not path.is_file()]
    if missing:
        raise SystemExit(f"입력 이미지를 찾을 수 없습니다: {len(missing)}개")
    args.output.mkdir(parents=True, exist_ok=True)

    load_started = perf_counter()
    ocr = NemotronOCRV2(model_dir=str(args.model_dir))
    model_load_seconds = round(perf_counter() - load_started, 2)
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()

    results: list[dict] = []
    for index, image_path in enumerate(args.images, start=1):
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        started = perf_counter()
        predictions = ocr(str(image_path), merge_level=args.merge_level)
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        elapsed = round(perf_counter() - started, 2)

        regions = [
            {
                "text": str(prediction.get("text", "")).strip(),
                "confidence": round(float(prediction.get("confidence", 0)), 6),
                "left": round(float(prediction.get("left", 0)), 6),
                "upper": round(float(prediction.get("upper", 0)), 6),
                "right": round(float(prediction.get("right", 0)), 6),
                "lower": round(float(prediction.get("lower", 0)), 6),
            }
            for prediction in predictions
            if str(prediction.get("text", "")).strip()
        ]
        texts = [region["text"] for region in regions]
        output_text = "\n".join(texts)
        confidences = [region["confidence"] for region in regions]
        unique_texts = set(texts)
        repetition_ratio = (
            round(1 - (len(unique_texts) / len(texts)), 4) if texts else 0
        )

        text_path = args.output / f"sample-{index:02d}.txt"
        regions_path = args.output / f"sample-{index:02d}.regions.json"
        text_path.write_text(output_text, encoding="utf-8")
        regions_path.write_text(
            json.dumps(regions, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        record = {
            "sample": index,
            "source_name": image_path.name,
            "sha256": sha256(image_path.read_bytes()).hexdigest(),
            "status": "completed" if texts else "empty",
            "elapsed_seconds": elapsed,
            "regions": len(regions),
            "characters": len(output_text),
            "lines": len(output_text.splitlines()),
            "korean_characters": sum("가" <= char <= "힣" for char in output_text),
            "average_confidence": (
                round(sum(confidences) / len(confidences), 4)
                if confidences
                else None
            ),
            "minimum_confidence": round(min(confidences), 4) if confidences else None,
            "repetition_ratio": repetition_ratio,
            "result_file": text_path.name,
            "regions_file": regions_path.name,
        }
        results.append(record)
        print(
            f"표본 {index}: {record['status']} · {elapsed}초 · "
            f"{len(regions)}영역 · {len(output_text)}자"
        )

    peak_memory_mb = (
        round(torch.cuda.max_memory_allocated() / 1024 / 1024, 1)
        if torch.cuda.is_available()
        else None
    )
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": "nemotron-ocr-v2",
        "variant": "v2_multilingual",
        "merge_level": args.merge_level,
        "model_load_seconds": model_load_seconds,
        "peak_cuda_allocated_mb": peak_memory_mb,
        "results": results,
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"모델 준비 {model_load_seconds}초 · "
        f"CUDA 최대 할당 {peak_memory_mb if peak_memory_mb is not None else '-'}MB"
    )


if __name__ == "__main__":
    main()
