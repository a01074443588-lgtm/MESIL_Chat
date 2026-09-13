"""Fail-closed validation for chat attachments.

The module identifies files from their safe filename, declared MIME type and
container signature.  It never extracts document text and never invokes AI.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePath
import re
import struct
import unicodedata
from zipfile import BadZipFile, ZipFile


class AttachmentValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedAttachment:
    original_name: str
    extension: str
    mime_type: str
    is_document: bool


MEDIA_BY_EXTENSION = {
    ".jpg": ("image/jpeg",),
    ".jpeg": ("image/jpeg",),
    ".png": ("image/png",),
    ".webp": ("image/webp",),
    ".mp3": ("audio/mpeg",),
    ".wav": ("audio/wav", "audio/x-wav"),
    ".m4a": ("audio/mp4", "audio/x-m4a"),
    ".aac": ("audio/aac",),
    ".ogg": ("audio/ogg",),
    ".webm": ("audio/webm", "video/webm"),
    ".mp4": ("video/mp4",),
    ".mov": ("video/quicktime",),
    ".pdf": ("application/pdf",),
}

DOCUMENT_MIME_BY_EXTENSION = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".hwp": "application/vnd.hancom.hwp",
    ".hwpx": "application/vnd.hancom.hwpx",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".ppt": "application/vnd.ms-powerpoint",
    ".txt": "text/plain",
    ".csv": "text/csv",
}

DOCUMENT_MIME_ALIASES = {
    ".xlsx": {DOCUMENT_MIME_BY_EXTENSION[".xlsx"], "application/zip", "application/octet-stream"},
    ".xls": {DOCUMENT_MIME_BY_EXTENSION[".xls"], "application/octet-stream"},
    ".hwp": {
        DOCUMENT_MIME_BY_EXTENSION[".hwp"],
        "application/x-hwp",
        "application/haansofthwp",
        "application/octet-stream",
    },
    ".hwpx": {
        DOCUMENT_MIME_BY_EXTENSION[".hwpx"],
        "application/hwp+zip",
        "application/zip",
        "application/octet-stream",
    },
    ".docx": {DOCUMENT_MIME_BY_EXTENSION[".docx"], "application/zip", "application/octet-stream"},
    ".doc": {DOCUMENT_MIME_BY_EXTENSION[".doc"], "application/octet-stream"},
    ".pptx": {DOCUMENT_MIME_BY_EXTENSION[".pptx"], "application/zip", "application/octet-stream"},
    ".ppt": {DOCUMENT_MIME_BY_EXTENSION[".ppt"], "application/octet-stream"},
    ".txt": {"text/plain", "application/octet-stream"},
    ".csv": {"text/csv", "application/csv", "text/plain", "application/vnd.ms-excel", "application/octet-stream"},
}

MACRO_EXTENSIONS = {".xlsm", ".docm", ".pptm"}
DISGUISE_EXTENSIONS = {
    ".exe", ".com", ".bat", ".cmd", ".ps1", ".js", ".jse", ".vbs",
    ".vbe", ".wsf", ".sh", ".py", ".php", ".jar", ".msi", ".scr",
    ".dll", ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz",
    ".html", ".htm", ".svg", *MACRO_EXTENSIONS,
    *MEDIA_BY_EXTENSION.keys(), *DOCUMENT_MIME_BY_EXTENSION.keys(),
}

SUPPORTED_FORMATS_KO = (
    "JPG·PNG·WEBP, MP3·WAV·M4A·WEBM·OGG·AAC, MP4·WEBM·MOV, PDF, "
    "Excel(XLSX·XLS), 한글(HWP·HWPX), Word(DOCX·DOC), "
    "PowerPoint(PPTX·PPT), TXT·CSV"
)


def safe_original_filename(value: str | None) -> str:
    raw = unicodedata.normalize("NFC", str(value or "").strip())
    raw = re.split(r"[\\/]", raw)[-1]
    raw = "".join("_" if ord(ch) < 32 or ch in '<>:"|?*' else ch for ch in raw)
    raw = raw.strip(" .")[:255]
    if not raw or raw in {".", ".."}:
        raise AttachmentValidationError("파일 이름을 확인해 주세요.")
    return raw


def _extension_and_name(filename: str | None) -> tuple[str, str]:
    name = safe_original_filename(filename)
    suffixes = [suffix.lower() for suffix in PurePath(name).suffixes]
    extension = suffixes[-1] if suffixes else ""
    if extension in MACRO_EXTENSIONS:
        raise AttachmentValidationError("매크로 문서(XLSM·DOCM·PPTM)는 첨부할 수 없습니다.")
    if extension not in MEDIA_BY_EXTENSION and extension not in DOCUMENT_MIME_BY_EXTENSION:
        raise AttachmentValidationError(f"지원하지 않는 파일 형식입니다. 지원 형식: {SUPPORTED_FORMATS_KO}")
    if any(suffix in DISGUISE_EXTENSIONS for suffix in suffixes[:-1]):
        raise AttachmentValidationError("이중 확장자로 위장된 파일은 첨부할 수 없습니다.")
    return extension, name


def _media_signature(mime_type: str, content: bytes) -> bool:
    if mime_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if mime_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if mime_type == "image/webp":
        return content.startswith(b"RIFF") and content[8:12] == b"WEBP"
    if mime_type in {"audio/wav", "audio/x-wav"}:
        return content.startswith(b"RIFF") and content[8:12] == b"WAVE"
    if mime_type in {"audio/mp4", "audio/x-m4a", "video/mp4", "video/quicktime"}:
        return len(content) >= 12 and content[4:8] == b"ftyp"
    if mime_type in {"audio/webm", "video/webm"}:
        return content.startswith(b"\x1a\x45\xdf\xa3")
    if mime_type == "audio/ogg":
        return content.startswith(b"OggS")
    if mime_type == "audio/mpeg":
        return content.startswith(b"ID3") or (len(content) > 1 and content[0] == 0xFF and content[1] & 0xE0 == 0xE0)
    if mime_type == "audio/aac":
        return len(content) > 1 and content[0] == 0xFF and content[1] & 0xF0 == 0xF0
    if mime_type == "application/pdf":
        return content.startswith(b"%PDF-")
    return False


def _safe_zip(content: bytes) -> tuple[ZipFile, set[str]]:
    if not content.startswith(b"PK\x03\x04"):
        raise AttachmentValidationError("문서 ZIP 시그니처가 올바르지 않습니다.")
    try:
        archive = ZipFile(BytesIO(content))
        entries = archive.infolist()
    except (BadZipFile, OSError):
        raise AttachmentValidationError("파일의 문서 구조를 확인할 수 없습니다.") from None
    if not entries or len(entries) > 4096:
        archive.close()
        raise AttachmentValidationError("파일의 문서 구조가 올바르지 않습니다.")
    names: set[str] = set()
    total = 0
    for entry in entries:
        name = entry.filename
        normalized = name.replace("\\", "/")
        if (
            not name
            or "\x00" in name
            or normalized.startswith("/")
            or re.match(r"^[A-Za-z]:", normalized)
            or ".." in normalized.split("/")
            or normalized in names
            or entry.flag_bits & 0x1
            or (entry.external_attr >> 16) & 0o170000 == 0o120000
            or PurePath(normalized).suffix.lower() in {
                '.exe', '.dll', '.com', '.bat', '.cmd', '.ps1', '.js', '.vbs', '.sh', '.py', '.jar', '.msi', '.scr',
            }
        ):
            archive.close()
            raise AttachmentValidationError("파일의 압축 문서 구조가 안전하지 않습니다.")
        names.add(normalized)
        total += entry.file_size
        if entry.file_size > 128 * 1024 * 1024:
            archive.close()
            raise AttachmentValidationError("문서 내부 항목이 너무 큽니다.")
        if entry.compress_size and entry.file_size > 1024 * 1024 and entry.file_size / entry.compress_size > 300:
            archive.close()
            raise AttachmentValidationError("비정상적으로 압축된 문서는 첨부할 수 없습니다.")
    if total > 256 * 1024 * 1024:
        archive.close()
        raise AttachmentValidationError("압축을 푼 문서의 전체 크기가 너무 큽니다.")
    return archive, names


def _read_small(archive: ZipFile, name: str, maximum: int = 2 * 1024 * 1024) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > maximum:
        raise AttachmentValidationError("문서 구조 항목이 너무 큽니다.")
    try:
        value = archive.read(info)
    except (BadZipFile, OSError, RuntimeError, NotImplementedError):
        raise AttachmentValidationError("문서 내부 항목을 확인할 수 없습니다.") from None
    if b"<!DOCTYPE" in value.upper() or b"<!ENTITY" in value.upper():
        raise AttachmentValidationError("외부 정의가 포함된 문서는 첨부할 수 없습니다.")
    return value


def _validate_zip_document(extension: str, content: bytes) -> None:
    archive, names = _safe_zip(content)
    try:
        lowered = {name.lower() for name in names}
        if any(name.endswith("vbaproject.bin") or name.startswith("scripts/") for name in lowered):
            raise AttachmentValidationError("매크로나 스크립트가 포함된 문서는 첨부할 수 없습니다.")
        if extension == ".hwpx":
            required = {"mimetype", "version.xml", "Contents/content.hpf", "Contents/header.xml"}
            if not required.issubset(names) or not any(re.fullmatch(r"Contents/section\d+\.xml", name) for name in names):
                raise AttachmentValidationError("HWPX 필수 문서 구조를 찾을 수 없습니다.")
            marker = _read_small(archive, "mimetype", 128).strip().lower()
            if marker not in {b"application/hwp+zip", b"application/vnd.hancom.hwpx"}:
                raise AttachmentValidationError("HWPX 파일 형식 정보가 올바르지 않습니다.")
            for name in ("version.xml", "Contents/content.hpf", "Contents/header.xml"):
                if not _read_small(archive, name).lstrip().startswith(b"<"):
                    raise AttachmentValidationError("HWPX XML 구조가 올바르지 않습니다.")
            return
        required_by_extension = {
            ".xlsx": "xl/workbook.xml",
            ".docx": "word/document.xml",
            ".pptx": "ppt/presentation.xml",
        }
        main_part = required_by_extension[extension]
        required = {"[Content_Types].xml", "_rels/.rels", main_part}
        if not required.issubset(names):
            raise AttachmentValidationError("Office 문서의 필수 ZIP 구조를 찾을 수 없습니다.")
        content_types = _read_small(archive, "[Content_Types].xml").lower()
        if b"macroenabled" in content_types or b"vba" in content_types:
            raise AttachmentValidationError("매크로가 포함된 문서는 첨부할 수 없습니다.")
        expected_tokens = {
            ".xlsx": (b"spreadsheetml", b"workbook"),
            ".docx": (b"wordprocessingml", b"document"),
            ".pptx": (b"presentationml", b"presentation"),
        }[extension]
        if not all(token in content_types for token in expected_tokens):
            raise AttachmentValidationError("확장자와 Office 문서 종류가 일치하지 않습니다.")
        if not _read_small(archive, main_part).lstrip().startswith(b"<"):
            raise AttachmentValidationError("Office 문서 XML 구조가 올바르지 않습니다.")
    finally:
        archive.close()


_FREESECT = 0xFFFFFFFF
_ENDOFCHAIN = 0xFFFFFFFE
_FATSECT = 0xFFFFFFFD
_DIFSECT = 0xFFFFFFFC


class _CompoundFile:
    def __init__(self, content: bytes):
        if len(content) < 512 or not content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            raise AttachmentValidationError("OLE 문서 시그니처가 올바르지 않습니다.")
        self.content = content
        if content[28:30] != b"\xfe\xff":
            raise AttachmentValidationError("OLE 바이트 순서가 올바르지 않습니다.")
        self.sector_size = 1 << struct.unpack_from("<H", content, 30)[0]
        self.mini_sector_size = 1 << struct.unpack_from("<H", content, 32)[0]
        if self.sector_size not in {512, 4096} or self.mini_sector_size != 64:
            raise AttachmentValidationError("지원하지 않는 OLE 섹터 구조입니다.")
        self.sector_count = (len(content) - 512) // self.sector_size
        if self.sector_count <= 0 or 512 + self.sector_count * self.sector_size != len(content):
            raise AttachmentValidationError("OLE 문서 크기가 섹터 구조와 일치하지 않습니다.")
        self.first_directory = struct.unpack_from("<I", content, 48)[0]
        self.mini_cutoff = struct.unpack_from("<I", content, 56)[0]
        self.first_minifat = struct.unpack_from("<I", content, 60)[0]
        self.minifat_count = struct.unpack_from("<I", content, 64)[0]
        fat_count = struct.unpack_from("<I", content, 44)[0]
        fat_ids = [value for value in struct.unpack_from("<109I", content, 76) if value < _DIFSECT]
        next_difat = struct.unpack_from("<I", content, 68)[0]
        difat_count = struct.unpack_from("<I", content, 72)[0]
        seen_difat: set[int] = set()
        for _ in range(min(difat_count, self.sector_count)):
            if next_difat >= self.sector_count or next_difat in seen_difat:
                raise AttachmentValidationError("OLE DIFAT 연결이 올바르지 않습니다.")
            seen_difat.add(next_difat)
            block = self._sector(next_difat)
            values = struct.unpack(f"<{self.sector_size // 4}I", block)
            fat_ids.extend(value for value in values[:-1] if value < _DIFSECT)
            next_difat = values[-1]
        fat_ids = fat_ids[:fat_count]
        if len(fat_ids) != fat_count or any(value >= self.sector_count for value in fat_ids):
            raise AttachmentValidationError("OLE FAT 구조가 올바르지 않습니다.")
        fat_bytes = b"".join(self._sector(value) for value in fat_ids)
        self.fat = struct.unpack(f"<{len(fat_bytes) // 4}I", fat_bytes)
        directory = self._regular_chain(self.first_directory, maximum=8 * 1024 * 1024)
        self.entries: dict[str, tuple[int, int, int]] = {}
        self.root: tuple[int, int, int] | None = None
        for offset in range(0, len(directory) - 127, 128):
            entry = directory[offset : offset + 128]
            name_length = struct.unpack_from("<H", entry, 64)[0]
            object_type = entry[66]
            if object_type not in {1, 2, 5} or name_length < 2 or name_length > 64 or name_length % 2:
                continue
            try:
                name = entry[: name_length - 2].decode("utf-16le")
            except UnicodeDecodeError:
                raise AttachmentValidationError("OLE 항목 이름이 올바르지 않습니다.") from None
            start = struct.unpack_from("<I", entry, 116)[0]
            size = struct.unpack_from("<Q", entry, 120)[0]
            value = (start, size, object_type)
            if object_type == 5:
                self.root = value
            else:
                self.entries[name.casefold()] = value
        if self.root is None:
            raise AttachmentValidationError("OLE 루트 저장소를 찾을 수 없습니다.")
        self.minifat: tuple[int, ...] = ()
        self.ministream = b""
        if self.minifat_count and self.first_minifat < _DIFSECT:
            raw = self._regular_chain(self.first_minifat, maximum=16 * 1024 * 1024)
            raw = raw[: self.minifat_count * self.sector_size]
            self.minifat = struct.unpack(f"<{len(raw) // 4}I", raw)
        root_start, root_size, _ = self.root
        if root_size and root_start < _DIFSECT:
            self.ministream = self._regular_chain(root_start, maximum=64 * 1024 * 1024)[:root_size]

    def _sector(self, number: int) -> bytes:
        if number >= self.sector_count:
            raise AttachmentValidationError("OLE 섹터 범위를 벗어났습니다.")
        start = 512 + number * self.sector_size
        return self.content[start : start + self.sector_size]

    def _regular_chain(self, start: int, maximum: int) -> bytes:
        output = bytearray()
        seen: set[int] = set()
        current = start
        while current != _ENDOFCHAIN:
            if current >= self.sector_count or current >= len(self.fat) or current in seen:
                raise AttachmentValidationError("OLE 섹터 연결이 올바르지 않습니다.")
            seen.add(current)
            output.extend(self._sector(current))
            if len(output) > maximum:
                raise AttachmentValidationError("OLE 내부 항목이 너무 큽니다.")
            current = self.fat[current]
        return bytes(output)

    def names(self) -> set[str]:
        return set(self.entries)

    def stream(self, name: str, maximum: int = 4096) -> bytes:
        entry = self.entries.get(name.casefold())
        if entry is None or entry[2] != 2:
            return b""
        start, actual_size, _ = entry
        read_size = min(actual_size, maximum)
        if actual_size < self.mini_cutoff:
            if not self.minifat or not self.ministream:
                return b""
            output = bytearray()
            seen: set[int] = set()
            current = start
            while current != _ENDOFCHAIN and len(output) < read_size:
                if current >= len(self.minifat) or current in seen:
                    raise AttachmentValidationError("OLE 미니 섹터 연결이 올바르지 않습니다.")
                seen.add(current)
                begin = current * self.mini_sector_size
                output.extend(self.ministream[begin : begin + self.mini_sector_size])
                current = self.minifat[current]
            return bytes(output[:read_size])
        chain_limit = min(max(actual_size, self.sector_size), 32 * 1024 * 1024)
        return self._regular_chain(start, maximum=chain_limit)[:read_size]


def _validate_ole_document(extension: str, content: bytes) -> None:
    compound = _CompoundFile(content)
    names = compound.names()
    if {"vba", "macros", "_vba_project_cur"}.intersection(names):
        raise AttachmentValidationError("매크로나 스크립트가 포함된 문서는 첨부할 수 없습니다.")
    required = {
        ".xls": {"workbook"},
        ".doc": {"worddocument"},
        ".ppt": {"powerpoint document", "current user"},
        ".hwp": {"fileheader", "docinfo", "bodytext"},
    }[extension]
    if extension == ".xls" and "workbook" not in names and "book" in names:
        required = {"book"}
    if not required.issubset(names):
        raise AttachmentValidationError("확장자에 맞는 OLE 문서 항목을 찾을 수 없습니다.")
    if extension == ".doc" and not compound.stream("WordDocument", 2).startswith(b"\xec\xa5"):
        raise AttachmentValidationError("Word 문서 헤더가 올바르지 않습니다.")
    if extension == ".xls":
        workbook = compound.stream(next(name for name in ("Workbook", "Book") if name.casefold() in names), 8)
        if workbook[:2] not in {b"\x09\x00", b"\x09\x02", b"\x09\x04", b"\x09\x08"}:
            raise AttachmentValidationError("Excel 문서 헤더가 올바르지 않습니다.")
    if extension == ".hwp" and not compound.stream("FileHeader", 32).startswith(b"HWP Document File"):
        raise AttachmentValidationError("한글 문서 헤더가 올바르지 않습니다.")


def _validate_text_document(content: bytes) -> None:
    if content.startswith((b"MZ", b"\x7fELF", b"PK\x03\x04", b"\xd0\xcf\x11\xe0", b"%PDF-")):
        raise AttachmentValidationError("텍스트 파일로 위장된 바이너리 파일은 첨부할 수 없습니다.")
    decoded: str | None = None
    encodings = ["utf-8-sig"]
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings.insert(0, "utf-16")
    encodings.append("cp949")
    for encoding in encodings:
        try:
            decoded = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if decoded is None or not decoded.strip():
        raise AttachmentValidationError("읽을 수 있는 텍스트가 없는 파일입니다.")
    controls = sum(1 for char in decoded if ord(char) < 32 and char not in "\t\r\n\f")
    if controls > max(2, len(decoded) // 100):
        raise AttachmentValidationError("텍스트 파일에 허용되지 않는 제어 문자가 포함되어 있습니다.")


def validate_uploaded_attachment(
    filename: str | None,
    declared_mime_type: str | None,
    content: bytes,
) -> ValidatedAttachment:
    extension, name = _extension_and_name(filename)
    declared = str(declared_mime_type or "").split(";", 1)[0].strip().lower()
    if extension in MEDIA_BY_EXTENSION:
        allowed = MEDIA_BY_EXTENSION[extension]
        if declared not in allowed or not _media_signature(declared, content):
            raise AttachmentValidationError("파일 내용과 표시된 형식이 일치하지 않습니다.")
        storage_extension = ".jpg" if extension == ".jpeg" else extension
        return ValidatedAttachment(name, storage_extension, declared, False)
    if declared not in DOCUMENT_MIME_ALIASES[extension]:
        raise AttachmentValidationError("파일의 MIME 형식과 확장자가 일치하지 않습니다.")
    if extension in {".xlsx", ".docx", ".pptx", ".hwpx"}:
        _validate_zip_document(extension, content)
    elif extension in {".xls", ".doc", ".ppt", ".hwp"}:
        _validate_ole_document(extension, content)
    else:
        _validate_text_document(content)
    return ValidatedAttachment(name, extension, DOCUMENT_MIME_BY_EXTENSION[extension], True)


def is_download_only_document(mime_type: str) -> bool:
    return mime_type in set(DOCUMENT_MIME_BY_EXTENSION.values())
