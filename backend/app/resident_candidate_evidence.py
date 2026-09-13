"""Read-time OCR candidate gate, using existing extraction metadata (no migration)."""
from hashlib import sha256
import re

from .ocr import is_pathological_handwriting_output, NO_TEXT_RESPONSES


def name_in_text(text, name):
    return bool(name and re.search(rf"(?<![가-힣A-Za-z0-9]){re.escape(name)}(?:\s*(?:어르신|님))?(?![가-힣A-Za-z0-9])", text or ""))


def visible_image_text(extraction):
    if extraction is None or extraction.status not in {"completed", "reviewed"}:
        return ""
    details = getattr(extraction, "suggestion_details", None) or []
    staff_text = next((row.get("staff_reviewed_text") for row in details if row.get("kind") == "resident_candidate_evidence" and row.get("staff_reviewed_text")), None)
    if extraction.status == "reviewed" or staff_text:
        text = extraction.reviewed_text if extraction.status == "reviewed" else staff_text
        return (text or "").strip() if not is_pathological_handwriting_output(text) else ""
    if any(row.get("output_blocked") for row in details):
        return ""
    raw = extraction.extracted_text or ""
    if raw.strip() in NO_TEXT_RESPONSES:
        return ""
    original = extraction.original_extracted_text or raw
    if is_pathological_handwriting_output(raw) or is_pathological_handwriting_output(original):
        return ""
    text = raw
    return text.strip() if not is_pathological_handwriting_output(text) else ""


def candidate_evidence(link, message):
    """Validate current text and attempt; never rely on a leftover link alone."""
    if link.source == "text_exact":
        return [{"source": "text_exact"}] if name_in_text(message.body, link.resident.display_name) else []
    evidence = []
    for attachment in message.attachments:
        source = "ocr_exact" if attachment.mime_type.startswith("image/") else "audio_transcript" if attachment.mime_type.startswith("audio/") else None
        if source != link.source:
            continue
        extraction = attachment.text_extraction
        if extraction is None:
            continue
        text = visible_image_text(extraction) if source == "ocr_exact" else ((extraction.reviewed_text or extraction.extracted_text or "").strip() if extraction.status in {"completed", "reviewed"} else "")
        if not text:
            continue
        binding = next((row for row in extraction.suggestion_details or [] if row.get("kind") == "resident_candidate_evidence"), None)
        if binding:
            if binding.get("source") != source or binding.get("text_sha256") != sha256(text.encode()).hexdigest():
                continue
            names = [row.get("matched_name", "") for row in binding.get("matches", []) if row.get("resident_id") == str(link.resident_id)]
            if not any(name_in_text(text, name) for name in names):
                continue
        elif not name_in_text(text, link.resident.display_name):
            # Legacy rows are checked against current displayed text without
            # modifying the stored link or old OCR attempt.
            continue
        evidence.append({"source": source, "attachment_id": str(attachment.id), "attempt_number": binding.get("attempt_number") if binding else None})
    return evidence


def resident_link_is_current(link, message):
    if link.status == "rejected":
        return False
    if link.status == "confirmed" and link.source == "audio_transcript":
        from .attachment_review_policy import attachment_evidence_text
        return any(name_in_text(attachment_evidence_text(attachment), link.resident.display_name)
            for attachment in message.attachments if attachment.mime_type.startswith("audio/"))
    if link.status == "confirmed" or link.source == "manual":
        return True
    # Existing text/audio matching behavior is unchanged; OCR is fail-closed.
    return link.source != "ocr_exact" or bool(candidate_evidence(link, message))


def current_message_resident(message):
    """A legacy primary pointer must not revive a rejected/unreviewed link."""
    resident = getattr(message, "resident", None)
    if resident is None:
        return None
    matching = [link for link in getattr(message, "resident_links", ())
        if link.resident_id == resident.id]
    if matching and not any(link.status == "confirmed" and resident_link_is_current(link, message)
                            for link in matching):
        return None
    return resident
