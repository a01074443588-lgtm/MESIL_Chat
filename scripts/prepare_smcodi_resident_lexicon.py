from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "SMCODI가 내보낸 현재 어르신 명단에서 채팅 OCR용 로컬 명단을 만듭니다. "
            "원본 SMCODI 데이터베이스에는 연결하거나 쓰지 않습니다."
        )
    )
    parser.add_argument("input", type=Path, help="UTF-8 JSON 또는 CSV 내보내기")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/smcodi_residents.local.json"),
    )
    return parser.parse_args()


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "n", "no", "inactive"}


def _normalize(record: dict[str, Any], index: int) -> dict[str, Any] | None:
    display_name = str(record.get("display_name") or record.get("name") or "").strip()
    if not display_name:
        return None
    external_id = str(
        record.get("external_id")
        or record.get("internal_code")
        or record.get("id")
        or f"row-{index + 1}"
    ).strip()
    return {
        "external_id": external_id,
        "display_name": display_name,
        "service_type": str(record.get("service_type") or "").strip() or None,
        "floor": str(record.get("floor") or "").strip() or None,
        "room_name": str(record.get("room_name") or record.get("room") or "").strip()
        or None,
        "is_active": _as_bool(record.get("is_active", record.get("active"))),
    }


def _load_records(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    records = payload.get("residents", []) if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError("JSON은 배열 또는 residents 배열을 가진 객체여야 합니다.")
    return [record for record in records if isinstance(record, dict)]


def main() -> None:
    args = parse_args()
    if not args.input.is_file():
        raise SystemExit(f"입력 파일을 찾을 수 없습니다: {args.input}")
    normalized = [
        resident
        for index, record in enumerate(_load_records(args.input))
        if (resident := _normalize(record, index)) is not None
    ]
    payload = {
        "schema_version": 1,
        "source": "smcodi_read_only_export",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "residents": normalized,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"SMCODI 어르신 로컬 명단 준비 완료: {len(normalized)}명")


if __name__ == "__main__":
    main()
