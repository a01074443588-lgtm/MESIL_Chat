from pathlib import Path

from app.image_variants import ensure_image_thumbnail, thumbnail_path
from PIL import Image


def test_thumbnail_is_small_webp_and_reused(tmp_path: Path):
    source = tmp_path / "large.jpg"
    Image.new("RGB", (2400, 1600), "white").save(source, format="JPEG", quality=90)
    target = thumbnail_path(tmp_path, source.name)

    first = ensure_image_thumbnail(source, target)
    first_mtime = first.stat().st_mtime_ns
    second = ensure_image_thumbnail(source, target)

    assert first == second
    assert second.stat().st_mtime_ns == first_mtime
    assert second.stat().st_size < source.stat().st_size
    with Image.open(second) as thumbnail:
        assert thumbnail.format == "WEBP"
        assert max(thumbnail.size) <= 720
