from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.bulk_transfer import (
    RESIDENT_HEADERS,
    STAFF_HEADERS,
    build_xlsx,
    parse_xlsx,
)
from app.database import SessionLocal
from app.main import app
from app.models import BulkTransferBatch, BulkTransferItem, Resident, Staff


ORIGIN = {"origin": "http://testserver"}


def login_admin(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "AdminPass!234"},
        headers=ORIGIN,
    )
    assert response.status_code == 200, response.text


def workbook(entity: str, rows: list[list[str]]) -> bytes:
    headers = RESIDENT_HEADERS if entity == "resident" else STAFF_HEADERS
    return build_xlsx(
        [
            ("작성 안내", [["안내", "시험용 비식별 자료"]]),
            ("입력 자료", [headers, *rows]),
            ("선택값 안내", [["항목", "허용값"]]),
        ]
    )


def workbook_with_formula() -> bytes:
    source = build_xlsx(
        [
            ("작성 안내", [["안내"]]),
            ("입력 자료", [STAFF_HEADERS, ["", "요보94", "재직", "요양보호사", "", "", ""]]),
            ("선택값 안내", [["항목"]]),
        ]
    )
    output = BytesIO()
    with ZipFile(BytesIO(source)) as original, ZipFile(output, "w", ZIP_DEFLATED) as rewritten:
        for entry in original.infolist():
            content = original.read(entry.filename)
            if entry.filename == "xl/worksheets/sheet2.xml":
                content = content.replace(b"</c>", b"<f>1+1</f></c>", 1)
            rewritten.writestr(entry, content)
    return output.getvalue()


def current_row(client: TestClient, entity: str, code: str) -> list[str]:
    response = client.get(f"/api/admin/bulk-transfer/{entity}/current.xlsx", headers=ORIGIN)
    assert response.status_code == 200, response.text
    rows = parse_xlsx(response.content)["입력 자료"]
    return next(row for row in rows[1:] if row[1] == code)


def test_resident_xlsx_preview_apply_is_selective_and_idempotent() -> None:
    with TestClient(app) as client:
        login_admin(client)
        floor = client.post(
            "/api/org-units",
            json={"unit_type": "floor", "name": "엑셀검증구역"},
            headers=ORIGIN,
        )
        assert floor.status_code == 201, floor.text
        created = client.post(
            "/api/admin/residents",
            json={"display_name": "어르0901", "service_type": "facility", "floor_id": floor.json()["id"]},
            headers=ORIGIN,
        )
        assert created.status_code == 201, created.text
        existing_id = created.json()["id"]
        exported = current_row(client, "resident", "어르0901")

        template = client.get("/api/admin/bulk-transfer/resident/template.xlsx", headers=ORIGIN)
        assert template.status_code == 200
        parsed_template = parse_xlsx(template.content)
        assert set(parsed_template) == {"작성 안내", "입력 자료", "선택값 안내"}
        assert all("비밀번호" not in header for header in parsed_template["입력 자료"][0])

        rows = [
            [*exported[:5], "91", "", exported[7]],
            ["", "어르0902", "이용 중", "시설", "엑셀검증구역", "92", "", ""],
            ["", "어르0903", "이용 중", "시설", "엑셀검증구역", "93", "", ""],
            ["", "어르0904", "이용 중", "시설", "엑셀검증구역", "94", "2026/08/30", ""],
            ["", "어르0905", "", "시설", "엑셀검증구역", "95", "", ""],
            ["", "어르0906", "이용 중", "시설", "엑셀검증구역", "96", "", ""],
            ["", "어르0906", "이용 중", "시설", "엑셀검증구역", "97", "", ""],
        ]
        with SessionLocal() as db:
            count_before = db.scalar(select(func.count()).select_from(Resident))
        preview = client.post(
            "/api/admin/bulk-transfer/resident/preview",
            files={"file": ("resident_fixture.xlsx", workbook("resident", rows), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            headers=ORIGIN,
        )
        assert preview.status_code == 201, preview.text
        body = preview.json()
        assert body["summary"]["update"] == 1
        assert body["summary"]["new"] == 2
        assert body["summary"]["error"] >= 2
        assert body["summary"]["conflict"] >= 2
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(Resident)) == count_before

        selected = [item["id"] for item in body["items"] if item["change_type"] in {"new", "update"} and not item["issues"]]
        applied = client.post(
            f"/api/admin/bulk-transfer/batches/{body['id']}/apply",
            json={"item_ids": selected},
            headers=ORIGIN,
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["apply_result"]["added"] == 2
        assert applied.json()["apply_result"]["updated"] == 1
        second = client.post(
            f"/api/admin/bulk-transfer/batches/{body['id']}/apply",
            json={"item_ids": selected},
            headers=ORIGIN,
        )
        assert second.status_code == 200, second.text
        with SessionLocal() as db:
            existing = db.get(Resident, existing_id)
            assert existing is not None and existing.sort_order == 91
            assert db.scalar(select(func.count()).select_from(Resident).where(Resident.display_name.in_(["어르0902", "어르0903"]))) == 2


def test_staff_xlsx_secrets_formula_stale_version_and_safe_apply() -> None:
    with TestClient(app) as client:
        login_admin(client)
        created = client.post(
            "/api/admin/staff-directory",
            json={"full_name": "요보91", "employee_code": "요보91"},
            headers=ORIGIN,
        )
        assert created.status_code == 201, created.text
        staff_id = created.json()["staff_id"]
        with SessionLocal() as db:
            staff = db.get(Staff, staff_id)
            staff.job_title = "요양보호사"
            db.commit()
        exported = current_row(client, "staff", "요보91")
        rows = [
            [exported[0], exported[1], "재직", "요양보호사", "", "", exported[6]],
            ["", "요보92", "재직", "요양보호사", "", "", ""],
            ["", "요보93", "재직", "요양보호사", "", "", ""],
            [exported[0], exported[1], "재직", "요양보호사", "", "", "2000-01-01T00:00:00+00:00"],
        ]
        preview = client.post(
            "/api/admin/bulk-transfer/staff/preview",
            files={"file": ("staff_fixture.xlsx", workbook("staff", rows), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            headers=ORIGIN,
        )
        assert preview.status_code == 201, preview.text
        body = preview.json()
        assert body["summary"]["new"] == 2
        assert body["summary"]["conflict"] >= 2
        selected = [item["id"] for item in body["items"] if item["change_type"] == "new" and not item["issues"]]
        applied = client.post(
            f"/api/admin/bulk-transfer/batches/{body['id']}/apply",
            json={"item_ids": selected},
            headers=ORIGIN,
        )
        assert applied.status_code == 200, applied.text
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(Staff).where(Staff.display_name.in_(["요보92", "요보93"]))) == 2
            assert db.get(Staff, staff_id) is not None

        bad_headers = [*STAFF_HEADERS, "비밀번호"]
        unsafe = build_xlsx([("작성 안내", [["안내"]]), ("입력 자료", [bad_headers, ["", "요보94", "재직", "요양보호사", "", "", "", "secret"]]), ("선택값 안내", [["항목"]])])
        blocked = client.post(
            "/api/admin/bulk-transfer/staff/preview",
            files={"file": ("unsafe.xlsx", unsafe, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            headers=ORIGIN,
        )
        assert blocked.status_code == 422
        assert "비밀번호" in blocked.json()["detail"]

        formula_blocked = client.post(
            "/api/admin/bulk-transfer/staff/preview",
            files={"file": ("formula.xlsx", workbook_with_formula(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            headers=ORIGIN,
        )
        assert formula_blocked.status_code == 422

        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(BulkTransferBatch)) >= 2
            assert db.scalar(select(func.count()).select_from(BulkTransferItem)) >= len(body["items"])
