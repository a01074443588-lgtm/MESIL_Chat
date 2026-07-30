"""실행 중인 SQLite 데이터베이스를 온라인 백업합니다."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import zipfile
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="SMCODI 채팅방 SQLite 백업")
    parser.add_argument(
        "--source",
        type=Path,
        default=PROJECT_ROOT / "data" / "smcodi_chat.db",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "backups",
    )
    args = parser.parse_args()

    source = args.source.resolve()
    output_dir = args.output_dir.resolve()
    if not source.exists():
        raise SystemExit(f"데이터베이스를 찾을 수 없습니다: {source}")
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    database_backup = output_dir / f"smcodi_chat_{stamp}.db"
    uploads_backup = output_dir / f"uploads_{stamp}.zip"
    manifest_path = output_dir / f"backup_{stamp}.json"

    source_db = sqlite3.connect(source)
    target_db = sqlite3.connect(database_backup)
    try:
        source_db.backup(target_db)
        integrity = target_db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise SystemExit(f"백업 무결성 검사 실패: {integrity}")
    finally:
        target_db.close()
        source_db.close()

    uploads_dir = PROJECT_ROOT / "data" / "uploads"
    upload_files = [path for path in uploads_dir.rglob("*") if path.is_file()]
    with zipfile.ZipFile(uploads_backup, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in upload_files:
            archive.write(path, path.relative_to(uploads_dir))

    manifest = {
        "created_at": datetime.now().astimezone().isoformat(),
        "database_source": str(source),
        "database_backup": database_backup.name,
        "database_sha256": sha256(database_backup),
        "uploads_backup": uploads_backup.name,
        "upload_file_count": len(upload_files),
        "integrity_check": integrity,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(database_backup)
    print(manifest_path)


if __name__ == "__main__":
    main()
