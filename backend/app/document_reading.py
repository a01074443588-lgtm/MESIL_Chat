"""Bounded local document text reading. Never executes scripts or remote links."""
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from time import monotonic
import xml.etree.ElementTree as ET

from .attachment_validation import (
    DOCUMENT_MIME_BY_EXTENSION, AttachmentValidationError,
    _validate_zip_document, _safe_zip, _read_small,
)

READABLE_EXTENSIONS = frozenset({".pdf", ".docx", ".txt", ".csv", ".hwpx", ".xlsx", ".pptx"})
DOCUMENT_MIME_TYPES = frozenset({"application/pdf", *DOCUMENT_MIME_BY_EXTENSION.values()})
MAX_DOCUMENT_PAGES = 40
MAX_DOCUMENT_CHARS = 100_000
MAX_XML_BYTES = 8 * 1024 * 1024
MIN_PDF_TEXT_CHARS = 24

ERROR_MESSAGES = {
    "unsupported_document": "현재는 문서 내용 읽기를 지원하지 않습니다.",
    "empty_document": "문서에서 글자를 찾지 못했습니다.",
    "encrypted_document": "암호가 설정된 문서는 읽을 수 없습니다. 암호를 해제한 사본을 올려 주세요.",
    "invalid_document": "파일 형식과 문서 구조가 일치하지 않습니다.",
    "corrupt_document": "문서가 손상되어 내용을 읽을 수 없습니다.",
    "document_limit": "문서 읽기 범위(40쪽·10만 글자)를 초과했습니다. 문서를 나누어 올려 주세요.",
    "document_timeout": "문서 읽기 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.",
    "page_read_failed": "문서의 일부 페이지를 읽지 못했습니다. 원본을 확인하고 다시 시도해 주세요.",
    "image_model_unavailable": "내부 이미지 모델에 연결하지 못했습니다. 잠시 후 다시 읽어 주세요.",
    "image_model_unsupported": "이미지 읽기를 지원하는 모델 설정이 필요합니다. 관리자에게 알려 주세요.",
    "image_model_call_failed": "이미지 모델의 응답을 받지 못했습니다. 잠시 후 다시 읽어 주세요.",
    "output_blocked": "읽은 내용이 비정상적으로 반복되어 표시하지 않았습니다. 원본을 확인하고 다시 읽어 주세요.",
}


class DocumentReadError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class DocumentReadResult:
    text: str
    locations: list[dict]
    ocr_page_count: int = 0


def document_extension(attachment):
    return Path(attachment.original_name).suffix.lower()


def read_scanned_page(path):
    """One bounded call per rendered PDF page, independent of photo band settings."""
    from .image_ocr_runtime import image_provider
    from .ocr import _ollama_extract_single
    from .config import settings
    if image_provider() == "stub" and settings.environment == "test":
        return "합성 문서 읽기 결과"
    if image_provider() != "ollama":
        raise DocumentReadError("image_model_unavailable")
    return _ollama_extract_single(path, room_name="문서 읽기", resident_name=None,
        prompt_override="이미지에 실제로 보이는 모든 글자와 숫자를 위에서 아래 순서대로 그대로 옮겨 적으세요. 요약·추측·교정·설명 없이 원문만 출력하세요. 글자가 없으면 [[NO_TEXT]]만 출력하세요.")


def _xml(archive, name):
    try:
        data = _read_small(archive, name, MAX_XML_BYTES)
        # Also reject UTF-16 declarations: the upload validator checks UTF-8.
        check = data.replace(b"\x00", b"").upper()
        if b"<!DOCTYPE" in check or b"<!ENTITY" in check:
            raise DocumentReadError("invalid_document")
        return ET.fromstring(data)
    except (ET.ParseError, KeyError, AttachmentValidationError):
        raise DocumentReadError("invalid_document") from None


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def read_document(path, extension, *, ocr_page=None) -> DocumentReadResult:
    path = Path(path)
    extension = extension.lower()
    if extension not in READABLE_EXTENSIONS:
        raise DocumentReadError("unsupported_document")
    if not path.stat().st_size:
        raise DocumentReadError("empty_document")
    if path.stat().st_size > 30 * 1024 * 1024:
        raise DocumentReadError("document_limit")
    started = monotonic()
    sections, locations = [], []
    char_count, ocr_count = 0, 0

    def add(text, **location):
        nonlocal char_count
        text = text.strip()
        if monotonic() - started > 150:
            raise DocumentReadError("document_timeout")
        start = char_count + (2 if sections else 0)
        char_count = start + len(text)
        if char_count > MAX_DOCUMENT_CHARS:
            raise DocumentReadError("document_limit")
        if text:
            sections.append(text)
            locations.append({**location, "start": start, "end": char_count})

    if extension in {".txt", ".csv"}:
        raw = path.read_bytes()
        for encoding in ("utf-8-sig", "utf-16" if raw[:2] in {b"\xff\xfe", b"\xfe\xff"} else "cp949"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeError:
                text = None
        if text is None or "\x00" in text:
            raise DocumentReadError("invalid_document")
        add(text, method="text", line_start=1, line_end=len(text.splitlines()))
    elif extension == ".pdf":
        from pypdf import PdfReader
        import pypdfium2 as pdfium
        import logging
        # Malformed document warnings may contain source fragments; keep them local.
        parser_logger = logging.getLogger("pypdf")
        old_disabled = parser_logger.disabled
        parser_logger.disabled = True
        try:
            reader = PdfReader(BytesIO(path.read_bytes()), strict=True)
            if reader.is_encrypted:
                raise DocumentReadError("encrypted_document")
            if len(reader.pages) > MAX_DOCUMENT_PAGES:
                raise DocumentReadError("document_limit")
            with TemporaryDirectory(prefix="mesil-document-") as temp:
                renderer = None
                try:
                    for number, page in enumerate(reader.pages, 1):
                        if float(page.mediabox.width) * float(page.mediabox.height) > 20_000_000:
                            raise DocumentReadError("document_limit")
                        text = page.extract_text() or ""
                        method = "text"
                        if len(re.sub(r"\s", "", text)) < MIN_PDF_TEXT_CHARS:
                            if ocr_page is None:
                                raise DocumentReadError("page_read_failed")
                            renderer = renderer or pdfium.PdfDocument(str(path))
                            rendered_page = renderer[number - 1]
                            scale = min(2.0, 2400 / max(rendered_page.get_size()))
                            bitmap = rendered_page.render(scale=scale)
                            try:
                                target = Path(temp) / f"page-{number}.png"
                                bitmap.to_pil().save(target)
                                try:
                                    text = ocr_page(target) or ""
                                except DocumentReadError:
                                    raise
                                except Exception as error:
                                    from .image_ocr_runtime import safe_image_failure
                                    code, _ = safe_image_failure(error)
                                    if code == "no_text_detected":
                                        text = ""
                                    else:
                                        raise DocumentReadError(code if code in ERROR_MESSAGES else "page_read_failed") from None
                            finally:
                                bitmap.close()
                                rendered_page.close()
                            method = "image_text"
                            ocr_count += 1
                        add(text, page=number, method=method)
                finally:
                    if renderer is not None:
                        renderer.close()
        except DocumentReadError:
            raise
        except Exception:
            raise DocumentReadError("corrupt_document") from None
        finally:
            parser_logger.disabled = old_disabled
    else:
        content = path.read_bytes()
        try:
            _validate_zip_document(extension, content)
            archive, names = _safe_zip(content)
        except AttachmentValidationError:
            raise DocumentReadError("invalid_document") from None
        with archive:
            # No relationships are followed: external entities and embeddings are never opened.
            if extension == ".docx":
                root = _xml(archive, "word/document.xml")
                for number, paragraph in enumerate((x for x in root.iter() if _local(x.tag) == "p"), 1):
                    add("".join(x.text or "" for x in paragraph.iter() if _local(x.tag) == "t"), paragraph=number, method="document_text")
            elif extension == ".xlsx":
                shared = []
                if "xl/sharedStrings.xml" in names:
                    shared = ["".join(x.text or "" for x in item.iter() if _local(x.tag) == "t")
                        for item in _xml(archive, "xl/sharedStrings.xml") if _local(item.tag) == "si"]
                sheets = sorted(n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
                if len(sheets) > MAX_DOCUMENT_PAGES:
                    raise DocumentReadError("document_limit")
                for sheet in sheets:
                    for row in (x for x in _xml(archive, sheet).iter() if _local(x.tag) == "row"):
                        cells = []
                        for cell in row:
                            value = "".join(x.text or "" for x in cell.iter() if _local(x.tag) in {"v", "t"})
                            if cell.get("t") == "s":
                                try: value = shared[int(value)]
                                except (ValueError, IndexError): raise DocumentReadError("invalid_document") from None
                            # Cached values only; formulas are never evaluated.
                            cells.append(value)
                        add(" | ".join(cells), part=sheet, row=row.get("r"), method="document_text")
            else:
                pattern = r"Contents/section\d+\.xml" if extension == ".hwpx" else r"ppt/slides/slide\d+\.xml"
                parts = sorted(n for n in names if re.fullmatch(pattern, n))
                if len(parts) > MAX_DOCUMENT_PAGES:
                    raise DocumentReadError("document_limit")
                for part in parts:
                    add("\n".join(x.text or "" for x in _xml(archive, part).iter() if _local(x.tag) == "t"), part=part, method="document_text")
    return DocumentReadResult("\n\n".join(sections), locations, ocr_count)
