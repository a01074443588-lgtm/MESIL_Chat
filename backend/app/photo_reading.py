"""Photo OCR intent and display state; ordinary photos have no extraction row."""


def photo_reading_status(attachment):
    if not attachment.mime_type.startswith("image/"):
        return None
    extraction = attachment.text_extraction
    if extraction is None:
        return "general"
    if extraction.status == "failed" and any(
        row.get("error_type") == "no_text_detected"
        for row in extraction.suggestion_details or []
    ):
        return "no_text"
    if extraction.status == "reviewed":
        return "completed"
    return extraction.status


def resident_review_metadata(message):
    # Lightweight attachment-response tests and a few internal projections use
    # message-shaped objects that intentionally omit optional JSON metadata.
    # Treat those exactly like a persisted message with no resident review yet.
    data = getattr(message, "extra_data", None) or {}
    value = data.get("resident_review", {}) if isinstance(data, dict) else {}
    return value if isinstance(value, dict) else {}
