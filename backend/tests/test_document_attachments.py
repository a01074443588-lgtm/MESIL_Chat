from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import struct
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from app.attachment_validation import (
    AttachmentValidationError,
    DOCUMENT_MIME_BY_EXTENSION,
    validate_uploaded_attachment,
)
from app.database import SessionLocal
from app import main as main_module
from app.main import app
from app.models import MessageAttachment


OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
END = 0xFFFFFFFE
FREE = 0xFFFFFFFF
FAT = 0xFFFFFFFD


def _zip(entries: dict[str, bytes], *, mimetype_stored: bool = False) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            archive.writestr(
                name,
                value,
                compress_type=ZIP_STORED if mimetype_stored and name == "mimetype" else ZIP_DEFLATED,
            )
    return output.getvalue()


def zip_document(extension: str) -> bytes:
    if extension == ".hwpx":
        return _zip(
            {
                "mimetype": b"application/hwp+zip",
                "version.xml": b"<version/>",
                "Contents/content.hpf": b"<package><manifest/><spine/></package>",
                "Contents/header.xml": b"<header/>",
                "Contents/section0.xml": b"<section><p/></section>",
            },
            mimetype_stored=True,
        )
    main = {
        ".xlsx": ("xl/workbook.xml", b"spreadsheetml workbook"),
        ".docx": ("word/document.xml", b"wordprocessingml document"),
        ".pptx": ("ppt/presentation.xml", b"presentationml presentation"),
    }[extension]
    return _zip(
        {
            "[Content_Types].xml": b"<Types>" + main[1] + b"</Types>",
            "_rels/.rels": b"<Relationships/>",
            main[0]: b"<root/>",
        }
    )


def _directory_entry(name: str, object_type: int, start: int = END, size: int = 0) -> bytes:
    entry = bytearray(128)
    encoded = (name + "\x00").encode("utf-16le")
    entry[: len(encoded)] = encoded
    struct.pack_into("<H", entry, 64, len(encoded))
    entry[66] = object_type
    entry[67] = 1
    struct.pack_into("<III", entry, 68, FREE, FREE, FREE)
    struct.pack_into("<I", entry, 116, start)
    struct.pack_into("<Q", entry, 120, size)
    return bytes(entry)


def ole_document(extension: str, *, macro: bool = False) -> bytes:
    streams = {
        ".xls": [("Workbook", b"\x09\x08\x08\x00")],
        ".doc": [("WordDocument", b"\xec\xa5")],
        ".ppt": [("PowerPoint Document", b"\x0f\x00\xe8\x03"), ("Current User", b"synthetic")],
        ".hwp": [("FileHeader", b"HWP Document File\x00"), ("DocInfo", b"synthetic")],
    }[extension]
    storages = ["BodyText"] if extension == ".hwp" else []
    if macro:
        storages.append("VBA")
    stream_sector_counts = [8 for _ in streams]
    directory_entries = 1 + len(streams) + len(storages)
    directory_sectors = (directory_entries + 3) // 4
    first_stream_sector = directory_sectors + 1
    stream_starts: list[int] = []
    cursor = first_stream_sector
    for count in stream_sector_counts:
        stream_starts.append(cursor)
        cursor += count
    sector_count = cursor
    assert sector_count <= 128

    header = bytearray(512)
    header[:8] = OLE_SIGNATURE
    struct.pack_into("<H", header, 24, 0x003E)
    struct.pack_into("<H", header, 26, 3)
    header[28:30] = b"\xfe\xff"
    struct.pack_into("<H", header, 30, 9)
    struct.pack_into("<H", header, 32, 6)
    struct.pack_into("<I", header, 40, 0)
    struct.pack_into("<I", header, 44, 1)
    struct.pack_into("<I", header, 48, 0)
    struct.pack_into("<I", header, 56, 4096)
    struct.pack_into("<I", header, 60, END)
    struct.pack_into("<I", header, 64, 0)
    struct.pack_into("<I", header, 68, END)
    struct.pack_into("<I", header, 72, 0)
    struct.pack_into("<109I", header, 76, 1, *([FREE] * 108))

    entries = [_directory_entry("Root Entry", 5)]
    for (name, _value), start in zip(streams, stream_starts, strict=True):
        entries.append(_directory_entry(name, 2, start, 4096))
    entries.extend(_directory_entry(name, 1) for name in storages)
    directory = b"".join(entries).ljust(directory_sectors * 512, b"\x00")

    fat_entries = [FREE] * 128
    for sector in range(directory_sectors):
        fat_entries[sector] = sector + 1 if sector + 1 < directory_sectors else END
    fat_entries[directory_sectors] = FAT
    for start, count in zip(stream_starts, stream_sector_counts, strict=True):
        for offset in range(count):
            fat_entries[start + offset] = start + offset + 1 if offset + 1 < count else END
    fat_sector = struct.pack("<128I", *fat_entries)
    stream_data = b"".join(value.ljust(4096, b"\x00") for _name, value in streams)
    return bytes(header) + directory + fat_sector + stream_data


DOCUMENT_FIXTURES = {
    ".xlsx": lambda: zip_document(".xlsx"),
    ".xls": lambda: ole_document(".xls"),
    ".hwp": lambda: ole_document(".hwp"),
    ".hwpx": lambda: zip_document(".hwpx"),
    ".docx": lambda: zip_document(".docx"),
    ".doc": lambda: ole_document(".doc"),
    ".pptx": lambda: zip_document(".pptx"),
    ".ppt": lambda: ole_document(".ppt"),
    ".txt": lambda: "합성 업무 문서입니다.\n".encode(),
    ".csv": lambda: "항목,값\n합성,1\n".encode(),
}


@pytest.mark.parametrize(
    ("filename", "mime_type", "content"),
    [
        ("image.jpg", "image/jpeg", b"\xff\xd8\xffsynthetic"),
        ("image.png", "image/png", b"\x89PNG\r\n\x1a\nsynthetic"),
        ("image.webp", "image/webp", b"RIFF\x04\x00\x00\x00WEBP"),
        ("audio.wav", "audio/wav", b"RIFF\x04\x00\x00\x00WAVE"),
        ("video.mp4", "video/mp4", b"\x00\x00\x00\x18ftypmp42"),
        ("document.pdf", "application/pdf", b"%PDF-1.4\n%%EOF"),
    ],
)
def test_existing_image_audio_video_and_pdf_signatures_remain_supported(filename, mime_type, content):
    result = validate_uploaded_attachment(filename, mime_type, content)
    assert result.mime_type == mime_type
    assert result.is_document is False


@pytest.mark.parametrize("extension", list(DOCUMENT_FIXTURES))
def test_document_signatures_and_korean_filename(extension):
    content = DOCUMENT_FIXTURES[extension]()
    declared = "application/octet-stream" if extension in {".hwp", ".hwpx"} else DOCUMENT_MIME_BY_EXTENSION[extension]
    result = validate_uploaded_attachment(f"합성 업무자료{extension}", declared, content)
    assert result.original_name == f"합성 업무자료{extension}"
    assert result.mime_type == DOCUMENT_MIME_BY_EXTENSION[extension]
    assert result.is_document is True


@pytest.mark.parametrize(
    ("filename", "content", "mime_type"),
    [
        ("empty.txt", b"", "text/plain"),
        ("program.exe", b"MZsynthetic", "application/octet-stream"),
        ("program.exe.txt", b"synthetic", "text/plain"),
        ("archive.zip", b"PK\x03\x04", "application/zip"),
        ("macro.xlsm", zip_document(".xlsx"), "application/vnd.ms-excel.sheet.macroEnabled.12"),
        ("macro.docm", zip_document(".docx"), "application/vnd.ms-word.document.macroEnabled.12"),
        ("macro.pptm", zip_document(".pptx"), "application/vnd.ms-powerpoint.presentation.macroEnabled.12"),
        ("fake.xlsx", _zip({"ordinary.txt": b"not a workbook"}), "application/zip"),
        ("fake.hwpx", _zip({"ordinary.txt": b"not hwpx"}), "application/octet-stream"),
        ("fake.doc", ole_document(".xls"), "application/octet-stream"),
        ("macro.xls", ole_document(".xls", macro=True), "application/vnd.ms-excel"),
    ],
)
def test_rejects_empty_executable_disguised_macro_and_wrong_structure(filename, content, mime_type):
    with pytest.raises(AttachmentValidationError):
        validate_uploaded_attachment(filename, mime_type, content)


@pytest.mark.parametrize('case', ['executable_prefix', 'traversal', 'embedded_executable', 'bomb'])
def test_office_zip_safety_boundaries(case):
    content = zip_document('.xlsx')
    if case == 'executable_prefix':
        content = b'MZ' + content
    else:
        output = BytesIO(content)
        with ZipFile(output, 'a', ZIP_DEFLATED) as archive:
            if case == 'traversal': archive.writestr('../outside.txt', b'synthetic')
            elif case == 'embedded_executable': archive.writestr('xl/embedded.exe', b'MZsynthetic')
            else: archive.writestr('xl/bomb.xml', b'0' * (2 * 1024 * 1024))
        content = output.getvalue()
    with pytest.raises(AttachmentValidationError):
        validate_uploaded_attachment('synthetic.xlsx', DOCUMENT_MIME_BY_EXTENSION['.xlsx'], content)


def _login(client: TestClient, username: str = "admin", password: str = "AdminPass!234") -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
        headers={"origin": "http://testserver"},
    )
    assert response.status_code == 200


def test_all_document_types_upload_download_hash_headers_and_local_read_queue():
    with TestClient(app) as client:
        _login(client)
        room_id = client.get("/api/rooms").json()[0]["id"]
        for extension, factory in DOCUMENT_FIXTURES.items():
            original = factory()
            declared = "application/octet-stream" if extension in {".hwp", ".hwpx"} else DOCUMENT_MIME_BY_EXTENSION[extension]
            response = client.post(
                f"/api/rooms/{room_id}/messages-with-files",
                data={"body": "합성 문서 첨부 시험"},
                files={"files": (f"한글 파일명{extension}", original, declared)},
                headers={"origin": "http://testserver"},
            )
            assert response.status_code == 201, response.text
            attachment = response.json()["attachments"][0]
            assert attachment["original_name"] == f"한글 파일명{extension}"
            assert attachment["text_extraction"]["status"] == "pending"
            assert attachment["text_extraction"]["provider"] == "internal_document"
            downloaded = client.get(attachment["download_url"])
            assert downloaded.status_code == 200
            assert sha256(downloaded.content).hexdigest() == sha256(original).hexdigest()
            assert downloaded.headers["x-content-type-options"] == "nosniff"
            assert downloaded.headers["content-disposition"].lower().startswith("attachment;")
            assert "filename*=utf-8''" in downloaded.headers["content-disposition"].lower()
            with SessionLocal() as db:
                stored = db.scalar(select(MessageAttachment).where(MessageAttachment.id == attachment["id"]))
                assert stored is not None
                assert stored.storage_key != stored.original_name
                assert stored.storage_key.endswith(extension)


def test_invalid_document_does_not_create_message_or_file():
    with TestClient(app) as client:
        _login(client)
        room_id = client.get("/api/rooms").json()[0]["id"]
        before = client.get(f"/api/rooms/{room_id}/messages").json()
        response = client.post(
            f"/api/rooms/{room_id}/messages-with-files",
            data={"body": "저장되면 안 되는 합성 문서"},
            files={"files": ("fake.xlsx", b"MZsynthetic", "application/octet-stream")},
            headers={"origin": "http://testserver"},
        )
        assert response.status_code == 422
        after = client.get(f"/api/rooms/{room_id}/messages").json()
        assert len(after) == len(before)


@pytest.mark.parametrize(
    ("filename", "content", "mime_type"),
    [
        ("empty.txt", b"", "text/plain"),
        ("program.exe", b"MZsynthetic", "application/octet-stream"),
        ("program.exe.txt", b"synthetic", "text/plain"),
        ("macro.xlsm", zip_document(".xlsx"), "application/vnd.ms-excel.sheet.macroEnabled.12"),
    ],
)
def test_invalid_uploads_are_rejected_without_creating_a_message(filename, content, mime_type):
    with TestClient(app) as client:
        _login(client)
        room_id = client.get("/api/rooms").json()[0]["id"]
        before = len(client.get(f"/api/rooms/{room_id}/messages").json())
        response = client.post(
            f"/api/rooms/{room_id}/messages-with-files",
            data={"body": "차단되어야 하는 합성 파일"},
            files={"files": (filename, content, mime_type)},
            headers={"origin": "http://testserver"},
        )
        assert response.status_code == 422
        assert len(client.get(f"/api/rooms/{room_id}/messages").json()) == before


def _create_staff(admin: TestClient, label: str) -> tuple[str, str, str]:
    token = uuid4().hex[:8]
    username = f"doc-{label}-{token}"
    temporary = "SyntheticPass!234"
    ready = "SyntheticReady!234"
    created = admin.post(
        "/api/employees",
        json={
            "username": username,
            "full_name": f"합성 직원 {label}",
            "password": temporary,
            "role": "staff",
            "can_process_records": False,
        },
        headers={"origin": "http://testserver"},
    )
    assert created.status_code == 201, created.text
    employee_id = created.json()["id"]
    client = TestClient(app)
    _login(client, username, temporary)
    changed = client.post(
        "/api/auth/password",
        json={"current_password": temporary, "new_password": ready},
        headers={"origin": "http://testserver"},
    )
    assert changed.status_code == 200, changed.text
    client.close()
    return employee_id, username, ready


def test_other_authorized_employee_downloads_same_hash_and_outsider_gets_403():
    admin = TestClient(app)
    authorized = TestClient(app)
    outsider = TestClient(app)
    try:
        _login(admin)
        first_id, first_username, first_password = _create_staff(admin, "가")
        second_id, second_username, second_password = _create_staff(admin, "나")
        _third_id, third_username, third_password = _create_staff(admin, "다")
        _login(authorized, second_username, second_password)
        _login(outsider, third_username, third_password)
        room = admin.post(
            "/api/rooms/custom",
            json={"name": f"합성 문서방 {uuid4().hex[:6]}", "member_ids": [first_id, second_id]},
            headers={"origin": "http://testserver"},
        )
        assert room.status_code == 201, room.text
        uploader = TestClient(app)
        try:
            _login(uploader, first_username, first_password)
            original = zip_document(".xlsx")
            sent = uploader.post(
                f"/api/rooms/{room.json()['id']}/messages-with-files",
                data={"body": "합성 권한 시험"},
                files={
                    "files": (
                        "합성 근무표.xlsx",
                        original,
                        DOCUMENT_MIME_BY_EXTENSION[".xlsx"],
                    )
                },
                headers={"origin": "http://testserver"},
            )
            assert sent.status_code == 201, sent.text
            url = sent.json()["attachments"][0]["download_url"]
            allowed = authorized.get(url)
            assert allowed.status_code == 200
            assert sha256(allowed.content).hexdigest() == sha256(original).hexdigest()
            assert outsider.get(url).status_code == 403
        finally:
            uploader.close()
    finally:
        admin.close()
        authorized.close()
        outsider.close()


def test_per_file_and_total_size_limits_are_unchanged(monkeypatch):
    with TestClient(app) as client:
        _login(client)
        room_id = client.get("/api/rooms").json()[0]["id"]
        monkeypatch.setattr(main_module.settings, "max_attachment_bytes", 64)
        too_large = client.post(
            f"/api/rooms/{room_id}/messages-with-files",
            data={"body": "합성 파일당 제한"},
            files={"files": ("large.txt", b"a" * 65, "text/plain")},
            headers={"origin": "http://testserver"},
        )
        assert too_large.status_code == 413
        monkeypatch.setattr(main_module.settings, "max_attachment_bytes", 1024)
        monkeypatch.setattr(main_module.settings, "max_attachments_total_bytes", 20)
        too_many_bytes = client.post(
            f"/api/rooms/{room_id}/messages-with-files",
            data={"body": "합성 전체 제한"},
            files=[
                ("files", ("first.txt", b"synthetic-1", "text/plain")),
                ("files", ("second.txt", b"synthetic-2", "text/plain")),
            ],
            headers={"origin": "http://testserver"},
        )
        assert too_many_bytes.status_code == 413
