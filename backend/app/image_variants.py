from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps


THUMBNAIL_MAX_SIZE = (720, 720)


def thumbnail_path(upload_dir: Path, storage_key: str) -> Path:
    root = upload_dir.resolve()
    thumbnail_dir = (root / ".thumbnails").resolve()
    if thumbnail_dir.parent != root:
        raise ValueError("올바르지 않은 미리보기 경로입니다.")
    return thumbnail_dir / f"{storage_key}.webp"


def ensure_image_thumbnail(source: Path, destination: Path) -> Path:
    source = source.resolve()
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if (
        destination.is_file()
        and destination.stat().st_mtime_ns >= source.stat().st_mtime_ns
    ):
        return destination

    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened)
            image.thumbnail(THUMBNAIL_MAX_SIZE, Image.Resampling.LANCZOS)
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGB")
            image.save(temporary, format="WEBP", quality=78, method=4)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination
