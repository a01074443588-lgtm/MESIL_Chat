"""명시적 확인 후 SQLite 백업을 복원합니다."""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def integrity_check(path: Path) -> None:
    database = sqlite3.connect(path)
    try:
        result = database.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        database.close()
    if result != "ok":
        raise SystemExit(f"복원 원본 무결성 검사 실패: {result}")


def main() -> None:
    parser = argparse.ArgumentParser(description="SMCODI 채팅방 SQLite 복원")
    parser.add_argument("backup", type=Path)
    parser.add_argument(
        "--target",
        type=Path,
        default=PROJECT_ROOT / "data" / "smcodi_chat.db",
    )
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()

    backup = args.backup.resolve()
    target = args.target.resolve()
    if args.confirm != "RESTORE":
        raise SystemExit("복원하려면 --confirm RESTORE를 정확히 입력해야 합니다.")
    if not backup.is_file():
        raise SystemExit(f"백업 파일을 찾을 수 없습니다: {backup}")
    integrity_check(backup)

    print(f"복원 원본: {backup}")
    print(f"덮어쓸 대상: {target}")
    print("실행 중인 채팅 서버를 먼저 종료해야 합니다.")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        safety_dir = PROJECT_ROOT / "data" / "backups"
        safety_dir.mkdir(parents=True, exist_ok=True)
        safety_path = safety_dir / (
            f"pre_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        )
        shutil.copy2(target, safety_path)
        print(f"복원 전 안전사본: {safety_path}")

    temporary_target = target.with_suffix(target.suffix + ".restore.tmp")
    if temporary_target.exists():
        temporary_target.unlink()
    source_db = sqlite3.connect(backup)
    target_db = sqlite3.connect(temporary_target)
    try:
        source_db.backup(target_db)
    finally:
        target_db.close()
        source_db.close()
    integrity_check(temporary_target)
    temporary_target.replace(target)
    print("복원을 완료했습니다.")


if __name__ == "__main__":
    main()
