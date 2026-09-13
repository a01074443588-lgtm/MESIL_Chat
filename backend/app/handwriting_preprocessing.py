"""Local, non-destructive handwriting cleanup with conservative safety gates.

The original upload is never modified. A colour-aware path may crop a captured
viewer down to a blue-ink document region and isolate only the front ink. Other
ink colours keep the grayscale suppression and original fallback contract.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageChops, ImageFilter, ImageOps, UnidentifiedImageError


ANALYSIS_MAX_SIZE = (960, 960)
BLUE_INK_MIN_PIXELS = 96
BLUE_INK_MIN_SPAN_RATIO = 0.18


@dataclass(frozen=True)
class HandwritingPreprocessMetrics:
    strong_foreground_pixels: int
    weak_background_pixels: int
    strong_foreground_retention: float | None
    weak_background_suppression: float | None
    original_ink_ratio: float
    processed_ink_ratio: float
    blue_ink_pixels: int = 0
    blue_ink_retention: float | None = None
    crop_height_ratio: float = 1.0
    estimated_front_line_count: int | None = None


@dataclass(frozen=True)
class HandwritingPreprocessDecision:
    source_path: Path
    processed_path: Path
    selected_path: Path
    applied: bool
    requires_staff_review: bool
    reason: str
    metrics: HandwritingPreprocessMetrics
    document_crop_path: Path | None = None
    ink_mask_path: Path | None = None
    selected_variant: str = "original"
    input_label: str = "원본"
    document_crop_applied: bool = False

    def public_summary(self) -> dict[str, object]:
        """Return path-free metadata safe for APIs and audit artifacts."""

        return {
            "applied": self.applied,
            "requires_staff_review": self.requires_staff_review,
            "reason": self.reason,
            "selected_variant": self.selected_variant,
            "input_label": self.input_label,
            "document_crop_applied": self.document_crop_applied,
            "metrics": asdict(self.metrics),
            "original_preserved": True,
            "external_transfer": False,
        }


def _normalized_candidate(image: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(image)
    radius = max(7.0, min(gray.size) / 42.0)
    background = gray.filter(ImageFilter.GaussianBlur(radius=radius))
    local_difference = ImageChops.subtract(background, gray)
    local_ink = local_difference.point(
        lambda value: 255
        if value <= 48
        else max(0, 255 - min(255, (value - 48) * 6))
    )
    strong_source = gray.point(lambda value: value if value <= 118 else 255)
    candidate = ImageChops.darker(local_ink, strong_source)
    return ImageOps.autocontrast(candidate, cutoff=(0.2, 0.2))


def _blue_ink_mask(image: Image.Image) -> Image.Image:
    """Detect saturated blue foreground without treating grayscale UI as ink."""

    pixels = []
    for red, green, blue in image.getdata():
        saturation = max(red, green, blue) - min(red, green, blue)
        is_blue_ink = (
            blue - red >= 12
            and blue - green >= 4
            and saturation >= 28
            and max(red, green, blue) <= 248
        )
        pixels.append(255 if is_blue_ink else 0)
    mask = Image.new("L", image.size, 0)
    mask.putdata(pixels)
    return mask


def _estimated_line_count(mask: Image.Image) -> int | None:
    width, height = mask.size
    row_threshold = max(3, width // 220)
    active_rows = []
    raw = mask.load()
    for y in range(height):
        if sum(1 for x in range(width) if raw[x, y] >= 128) >= row_threshold:
            active_rows.append(y)
    if not active_rows:
        return None

    groups = 1
    previous = active_rows[0]
    for row in active_rows[1:]:
        if row - previous > max(5, height // 160):
            groups += 1
        previous = row
    return groups


def _blue_document_region(
    image: Image.Image,
) -> tuple[Image.Image, Image.Image, tuple[int, int, int, int], int] | None:
    mask = _blue_ink_mask(image)
    bbox = mask.getbbox()
    if bbox is None:
        return None
    blue_pixels = sum(1 for value in mask.getdata() if value >= 128)
    left, top, right, bottom = bbox
    width, height = image.size
    if (
        blue_pixels < BLUE_INK_MIN_PIXELS
        or (right - left) / max(1, width) < BLUE_INK_MIN_SPAN_RATIO
        or (bottom - top) / max(1, height) < 0.08
    ):
        return None

    pad_x = max(12, width // 25)
    pad_y = max(10, height // 45)
    crop_box = (
        max(0, left - pad_x),
        max(0, top - pad_y),
        min(width, right + pad_x),
        min(height, bottom + pad_y),
    )
    return image.crop(crop_box), mask.crop(crop_box), crop_box, blue_pixels


def _isolated_colour_candidate(image: Image.Image, mask: Image.Image) -> Image.Image:
    expanded_mask = mask.filter(ImageFilter.MaxFilter(3))
    return Image.composite(
        image,
        Image.new("RGB", image.size, "white"),
        expanded_mask,
    )


def _quality_metrics(
    original: Image.Image,
    processed: Image.Image,
    *,
    foreground_mask: Image.Image | None = None,
    blue_ink_pixels: int = 0,
    crop_height_ratio: float = 1.0,
    estimated_front_line_count: int | None = None,
) -> HandwritingPreprocessMetrics:
    original_small = ImageOps.grayscale(original.copy())
    original_small.thumbnail(ANALYSIS_MAX_SIZE, Image.Resampling.LANCZOS)
    processed_small = ImageOps.grayscale(processed).resize(
        original_small.size,
        Image.Resampling.LANCZOS,
    )
    mask_small = (
        foreground_mask.resize(original_small.size, Image.Resampling.NEAREST)
        if foreground_mask is not None
        else None
    )

    original_pixels = original_small.tobytes()
    processed_pixels = processed_small.tobytes()
    mask_pixels = mask_small.tobytes() if mask_small is not None else None
    total = max(1, len(original_pixels))

    strong = 0
    strong_retained = 0
    weak = 0
    weak_suppressed = 0
    original_ink = 0
    processed_ink = 0
    for index, (source_value, processed_value) in enumerate(
        zip(original_pixels, processed_pixels, strict=True)
    ):
        foreground = (
            mask_pixels[index] >= 128
            if mask_pixels is not None
            else source_value <= 118
        )
        if foreground:
            strong += 1
            if processed_value <= 230:
                strong_retained += 1
        elif 158 <= source_value <= 226:
            weak += 1
            if processed_value >= 242:
                weak_suppressed += 1
        if source_value <= 226:
            original_ink += 1
        if processed_value <= 226:
            processed_ink += 1

    retention = round(strong_retained / strong, 4) if strong else None
    return HandwritingPreprocessMetrics(
        strong_foreground_pixels=strong,
        weak_background_pixels=weak,
        strong_foreground_retention=retention,
        weak_background_suppression=(
            round(weak_suppressed / weak, 4) if weak else None
        ),
        original_ink_ratio=round(original_ink / total, 4),
        processed_ink_ratio=round(processed_ink / total, 4),
        blue_ink_pixels=blue_ink_pixels,
        blue_ink_retention=retention if foreground_mask is not None else None,
        crop_height_ratio=round(crop_height_ratio, 4),
        estimated_front_line_count=estimated_front_line_count,
    )


def _decision_reason(metrics: HandwritingPreprocessMetrics) -> tuple[bool, bool, str]:
    retention = metrics.strong_foreground_retention
    suppression = metrics.weak_background_suppression
    if metrics.strong_foreground_pixels < 48:
        return False, True, "faint_foreground_requires_staff_review"
    if retention is None or retention < 0.94:
        return False, True, "foreground_loss_requires_original"
    if metrics.weak_background_pixels < 48 or suppression is None:
        return False, True, "dark_two_sided_or_no_weak_background_gain"
    if suppression < 0.42:
        return False, True, "no_safe_background_suppression_gain"
    if metrics.processed_ink_ratio > max(0.24, metrics.original_ink_ratio * 1.35):
        return False, True, "processed_noise_increase_requires_original"
    return True, False, "safe_bleed_through_suppression"


def _save_png(image: Image.Image, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        image.save(temporary, format="PNG", optimize=True)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_handwriting_for_ocr(
    source_path: Path,
    *,
    derivative_dir: Path,
    derivative_name: str | None = None,
) -> HandwritingPreprocessDecision:
    """Create local derivatives and select one only when the gate is safe."""

    source = source_path.resolve()
    destination_root = derivative_dir.resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    name = derivative_name or f"{source.stem}.bleed-suppressed.png"
    destination = (destination_root / Path(name).name).resolve()
    if destination.parent != destination_root:
        raise ValueError("올바르지 않은 손글씨 정제본 경로입니다.")
    base = destination.stem
    crop_destination = destination_root / f"{base}.document-crop.png"
    mask_destination = destination_root / f"{base}.ink-mask.png"

    try:
        with Image.open(source) as opened:
            original = ImageOps.exif_transpose(opened).convert("RGB")
            colour_region = _blue_document_region(original)
            if colour_region is not None:
                document_crop, ink_mask, crop_box, blue_pixels = colour_region
                processed = _isolated_colour_candidate(document_crop, ink_mask)
                line_count = _estimated_line_count(ink_mask)
                metrics = _quality_metrics(
                    document_crop,
                    processed,
                    foreground_mask=ink_mask,
                    blue_ink_pixels=blue_pixels,
                    crop_height_ratio=(
                        (crop_box[3] - crop_box[1]) / max(1, original.height)
                    ),
                    estimated_front_line_count=line_count,
                )
                safe_colour_gate = (
                    (metrics.blue_ink_retention or 0) >= 0.98
                    and (
                        metrics.weak_background_pixels < 48
                        or (metrics.weak_background_suppression or 0) >= 0.70
                    )
                    and (line_count or 0) >= 2
                )
            else:
                document_crop = None
                ink_mask = None
                processed = _normalized_candidate(original)
                metrics = _quality_metrics(original, processed)
                safe_colour_gate = False
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError("손글씨 이미지를 열 수 없습니다.") from exc

    _save_png(processed, destination)
    if document_crop is not None and ink_mask is not None:
        _save_png(document_crop, crop_destination)
        _save_png(ink_mask, mask_destination)

    if colour_region is not None and safe_colour_gate:
        return HandwritingPreprocessDecision(
            source_path=source,
            processed_path=destination,
            selected_path=destination,
            applied=True,
            requires_staff_review=False,
            reason="safe_blue_ink_document_isolation",
            metrics=metrics,
            document_crop_path=crop_destination,
            ink_mask_path=mask_destination,
            selected_variant="blue_ink_isolated",
            input_label="문서 영역의 파란 필기 정제본",
            document_crop_applied=True,
        )

    applied, requires_staff_review, reason = _decision_reason(metrics)
    return HandwritingPreprocessDecision(
        source_path=source,
        processed_path=destination,
        selected_path=destination if applied else source,
        applied=applied,
        requires_staff_review=requires_staff_review,
        reason=reason,
        metrics=metrics,
        document_crop_path=(crop_destination if document_crop is not None else None),
        ink_mask_path=(mask_destination if ink_mask is not None else None),
        selected_variant="bleed_suppressed" if applied else "original",
        input_label="이면지 억제 정제본" if applied else "원본(안전 복귀)",
        document_crop_applied=False,
    )
