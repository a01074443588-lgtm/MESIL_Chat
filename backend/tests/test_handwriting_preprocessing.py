from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from app.handwriting_preprocessing import prepare_handwriting_for_ocr


def _base_page() -> Image.Image:
    return Image.new("RGB", (900, 700), "white")


def _draw_front(draw: ImageDraw.ImageDraw, *, fill: int = 45) -> None:
    for row in range(5):
        top = 90 + row * 90
        draw.rectangle((90, top, 540, top + 13), fill=(fill, fill, fill))
        draw.rectangle((90, top, 108, top + 44), fill=(fill, fill, fill))


def test_safe_faint_show_through_uses_derivative_and_preserves_original(
    tmp_path: Path,
) -> None:
    image = _base_page()
    draw = ImageDraw.Draw(image)
    _draw_front(draw)
    for row in range(6):
        top = 55 + row * 92
        draw.line((360, top, 820, top + 35), fill=(198, 198, 198), width=5)
        draw.line((820, top + 35, 360, top + 70), fill=(205, 205, 205), width=4)
    source = tmp_path / "front-with-faint-back.png"
    image.save(source)
    before = source.read_bytes()

    decision = prepare_handwriting_for_ocr(
        source,
        derivative_dir=tmp_path / "derived",
    )

    assert source.read_bytes() == before
    assert decision.processed_path.is_file()
    assert decision.applied is True
    assert decision.selected_path == decision.processed_path
    assert decision.metrics.strong_foreground_retention is not None
    assert decision.metrics.strong_foreground_retention >= 0.94
    assert decision.metrics.weak_background_suppression is not None
    assert decision.metrics.weak_background_suppression >= 0.42
    assert decision.public_summary()["original_preserved"] is True


def test_faint_pencil_front_falls_back_to_original(tmp_path: Path) -> None:
    image = _base_page()
    draw = ImageDraw.Draw(image)
    _draw_front(draw, fill=168)
    source = tmp_path / "faint-pencil.png"
    image.save(source)

    decision = prepare_handwriting_for_ocr(
        source,
        derivative_dir=tmp_path / "derived",
    )

    assert decision.applied is False
    assert decision.requires_staff_review is True
    assert decision.selected_path == source.resolve()
    assert decision.reason == "faint_foreground_requires_staff_review"


def test_two_dark_sides_do_not_claim_safe_suppression(tmp_path: Path) -> None:
    image = _base_page()
    draw = ImageDraw.Draw(image)
    _draw_front(draw, fill=35)
    for row in range(5):
        top = 65 + row * 96
        draw.line((350, top, 830, top + 50), fill=(95, 95, 95), width=9)
    source = tmp_path / "two-dark-sides.png"
    image.save(source)

    decision = prepare_handwriting_for_ocr(
        source,
        derivative_dir=tmp_path / "derived",
    )

    assert decision.applied is False
    assert decision.requires_staff_review is True
    assert decision.selected_path == source.resolve()
    assert decision.reason in {
        "dark_two_sided_or_no_weak_background_gain",
        "no_safe_background_suppression_gain",
    }


def test_shadow_and_fold_keep_front_strokes_or_fall_back(tmp_path: Path) -> None:
    image = _base_page()
    draw = ImageDraw.Draw(image)
    for x in range(image.width):
        shade = max(145, 255 - int((x / image.width) * 90))
        draw.line((x, 0, x, image.height), fill=(shade, shade, shade))
    _draw_front(draw, fill=35)
    source = tmp_path / "shadow-fold.png"
    image.save(source)
    before = source.read_bytes()

    decision = prepare_handwriting_for_ocr(
        source,
        derivative_dir=tmp_path / "derived",
    )

    assert source.read_bytes() == before
    assert decision.metrics.strong_foreground_retention is not None
    if decision.applied:
        assert decision.metrics.strong_foreground_retention >= 0.94
    else:
        assert decision.selected_path == source.resolve()


def test_blue_ink_screenshot_crops_viewer_and_isolates_front_strokes(
    tmp_path: Path,
) -> None:
    image = Image.new("RGB", (600, 800), (238, 238, 238))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 599, 45), fill=(25, 25, 25))
    draw.rectangle((0, 46, 599, 750), fill=(242, 241, 237))
    for row in range(7):
        top = 90 + row * 55
        draw.line((75, top, 460, top + 7), fill=(48, 63, 135), width=7)
    for row in range(5):
        top = 470 + row * 42
        draw.text((250, top), "BACK SIDE 2026", fill=(170, 170, 170))
    draw.rectangle((0, 751, 599, 799), fill=(35, 35, 35))
    source = tmp_path / "blue-front-viewer.png"
    image.save(source)
    before = source.read_bytes()

    decision = prepare_handwriting_for_ocr(
        source,
        derivative_dir=tmp_path / "derived",
    )

    assert source.read_bytes() == before
    assert decision.applied is True
    assert decision.selected_variant == "blue_ink_isolated"
    assert decision.document_crop_applied is True
    assert decision.document_crop_path is not None
    assert decision.ink_mask_path is not None
    assert decision.metrics.estimated_front_line_count == 7
    assert decision.metrics.crop_height_ratio < 0.7
    with Image.open(decision.processed_path) as selected:
        assert selected.height < image.height * 0.7
        bottom_band = selected.crop((0, max(0, selected.height - 20), selected.width, selected.height))
        bottom_pixels = list(ImageOps.grayscale(bottom_band).getdata())
        assert sum(value < 100 for value in bottom_pixels) / len(bottom_pixels) < 0.15
