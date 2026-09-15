from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import PurePath
import re
from typing import Any, Literal
from uuid import UUID
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from .config import settings
from .finalist_coded_synthetic import RESIDENT_CODE_RE, STAFF_CODE_RE
from .models import (
    BulkTransferBatch,
    BulkTransferItem,
    LoginSession,
    OrgUnit,
    RecipientRoom,
    Resident,
    RoomMembership,
    Staff,
    StaffJobCode,
    StaffPositionCode,
    User,
    utcnow,
)
from .services import record_audit


XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_XLSX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 1000
SHEET_GUIDE = "작성 안내"
SHEET_DATA = "입력 자료"
SHEET_CHOICES = "선택값 안내"
ENTITY_TYPES = {"resident", "staff"}
SENSITIVE_HEADER_RE = re.compile(
    r"password|passwd|비밀번호|hash|해시|token|토큰|secret|비밀|api.?key|인증|endpoint|접속주소",
    re.IGNORECASE,
)

RESIDENT_HEADERS = [
    "내부_식별번호",
    "표시_코드",
    "현재_상태",
    "서비스_종류",
    "생활실_구역",
    "정렬_순서",
    "상태_변경일",
    "자료_버전",
]
STAFF_HEADERS = [
    "내부_식별번호",
    "표시_코드",
    "현재_상태",
    "직종",
    "직위",
    "상태_변경일",
    "자료_버전",
]

RESIDENT_STATUS = {"이용 중": "active", "이용 종료": "inactive"}
STAFF_STATUS = {"재직": "active", "퇴사": "retired"}
SERVICE_TYPES = {"시설": "facility", "주간보호": "daycare", "방문요양": "homecare"}
SERVICE_LABELS = {value: key for key, value in SERVICE_TYPES.items()}
JOB_PREFIX = {
    "요양보호사": "요보",
    "사회복지사": "사복",
    "간호사": "간호",
    "조리원": "조리",
    "영양사": "영양",
    "위생원": "위생",
    "운전원": "운전",
    "작업치료사": "작치",
    "시설장": "시설",
    "관리자": "관리자",
}


def _xml_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_xlsx(sheets: list[tuple[str, list[list[Any]]]]) -> bytes:
    """Create a conservative XLSX with inline strings and no executable content."""

    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        overrides = "".join(
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for index in range(1, len(sheets) + 1)
        )
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            f"{overrides}"
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '</Types>',
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>',
        )
        workbook_sheets = "".join(
            f'<sheet name="{_xml_escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
            for index, (name, _) in enumerate(sheets, 1)
        )
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets>{workbook_sheets}</sheets></workbook>',
        )
        relationships = "".join(
            f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
            for index in range(1, len(sheets) + 1)
        )
        relationships += (
            f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{relationships}</Relationships>",
        )
        archive.writestr(
            "xl/styles.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="2"><font><sz val="11"/><name val="맑은 고딕"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="맑은 고딕"/></font></fonts>'
            '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF557A17"/><bgColor indexed="64"/></patternFill></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs>'
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
            '</styleSheet>',
        )
        for sheet_index, (_, rows) in enumerate(sheets, 1):
            xml_rows: list[str] = []
            for row_index, row in enumerate(rows, 1):
                cells = []
                for column_index, raw_value in enumerate(row, 1):
                    column = ""
                    value = column_index
                    while value:
                        value, remainder = divmod(value - 1, 26)
                        column = chr(65 + remainder) + column
                    text_value = "" if raw_value is None else str(raw_value)
                    style = ' s="1"' if row_index == 1 else ""
                    cells.append(
                        f'<c r="{column}{row_index}" t="inlineStr"{style}><is><t xml:space="preserve">{_xml_escape(text_value)}</t></is></c>'
                    )
                xml_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
            archive.writestr(
                f"xl/worksheets/sheet{sheet_index}.xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
                '<sheetFormatPr defaultRowHeight="18"/><sheetData>'
                f'{"".join(xml_rows)}</sheetData></worksheet>',
            )
    return buffer.getvalue()


def _column_index(cell_reference: str) -> int:
    letters = "".join(char for char in cell_reference if char.isalpha()).upper()
    index = 0
    for char in letters:
        index = index * 26 + ord(char) - 64
    return max(index - 1, 0)


def parse_xlsx(content: bytes) -> dict[str, list[list[str]]]:
    if len(content) > MAX_XLSX_BYTES:
        raise HTTPException(status_code=413, detail="엑셀 파일은 5MB 이하로 올려 주세요.")
    try:
        with ZipFile(BytesIO(content)) as archive:
            names = set(archive.namelist())
            if any(name.lower().endswith("vbaproject.bin") for name in names):
                raise HTTPException(status_code=422, detail="매크로가 포함된 파일은 올릴 수 없습니다. .xlsx 파일로 다시 저장해 주세요.")
            if any(name.startswith("xl/externalLinks/") for name in names):
                raise HTTPException(status_code=422, detail="외부 연결이 포함된 엑셀은 올릴 수 없습니다.")
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            rel_map = {item.attrib["Id"]: item.attrib["Target"] for item in rels}
            shared: list[str] = []
            if "xl/sharedStrings.xml" in names:
                shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                for item in shared_root:
                    shared.append("".join(node.text or "" for node in item.iter() if node.tag.endswith("}t")))
            parsed: dict[str, list[list[str]]] = {}
            for sheet in workbook.iter():
                if not sheet.tag.endswith("}sheet"):
                    continue
                name = sheet.attrib.get("name", "")
                relation_id = next((value for key, value in sheet.attrib.items() if key.endswith("}id")), "")
                target = rel_map.get(relation_id, "")
                normalized = str(PurePath("xl") / target).replace("\\", "/")
                if normalized not in names:
                    continue
                root = ET.fromstring(archive.read(normalized))
                if any(node.tag.endswith("}f") for node in root.iter()):
                    raise HTTPException(status_code=422, detail=f"'{name}' 시트에 수식이 있습니다. 수식을 값으로 바꾼 뒤 다시 올려 주세요.")
                rows: list[list[str]] = []
                for row in (node for node in root.iter() if node.tag.endswith("}row")):
                    values: list[str] = []
                    for cell in (node for node in row if node.tag.endswith("}c")):
                        position = _column_index(cell.attrib.get("r", "A1"))
                        while len(values) <= position:
                            values.append("")
                        cell_type = cell.attrib.get("t")
                        text = ""
                        if cell_type == "inlineStr":
                            text = "".join(node.text or "" for node in cell.iter() if node.tag.endswith("}t"))
                        else:
                            value_node = next((node for node in cell if node.tag.endswith("}v")), None)
                            if value_node is not None and value_node.text is not None:
                                if cell_type == "s":
                                    try:
                                        text = shared[int(value_node.text)]
                                    except (ValueError, IndexError):
                                        text = ""
                                else:
                                    text = value_node.text
                        values[position] = text.strip()
                    while values and values[-1] == "":
                        values.pop()
                    rows.append(values)
                parsed[name] = rows
            return parsed
    except HTTPException:
        raise
    except (BadZipFile, KeyError, ET.ParseError):
        raise HTTPException(status_code=422, detail="손상되었거나 올바른 .xlsx 파일이 아닙니다.")


def _version(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _guide_rows(entity_type: str) -> list[list[str]]:
    subject = "어르신" if entity_type == "resident" else "직원"
    ended = "이용 종료" if entity_type == "resident" else "퇴사"
    return [
        [f"{subject} 정보 엑셀 작성 안내", "설명"],
        ["필수 항목", "표시 코드, 현재 상태와 종류별 필수 항목을 입력합니다."],
        ["선택 항목", "생활실·구역, 직위처럼 해당하지 않는 항목은 비워둘 수 있습니다."],
        ["날짜", "날짜 항목이 추가되는 경우 YYYY-MM-DD 형식으로 작성합니다."],
        ["기존 자료 수정", "현재 자료 파일의 내부 식별번호와 자료 버전을 그대로 둔 채 수정합니다."],
        ["신규 자료 추가", "내부 식별번호와 자료 버전을 비우고 새 행을 추가합니다."],
        [ended, f"행을 지우지 말고 현재 상태를 '{ended}'로 바꿉니다. 기존 기록은 보존됩니다."],
        ["이름으로 찾지 않음", "동명이인과 잘못된 덮어쓰기를 막기 위해 이름만으로 기존 사람을 찾지 않습니다."],
        ["적용 절차", "업로드만으로 저장되지 않습니다. 변경 내용을 확인하고 선택한 행만 최종 적용합니다."],
        ["비밀번호", "직원 계정 비밀번호와 인증정보는 엑셀에서 입력하거나 변경할 수 없습니다."],
    ]


def workbook_template(entity_type: str, *, choices: dict[str, list[str]]) -> bytes:
    headers = RESIDENT_HEADERS if entity_type == "resident" else STAFF_HEADERS
    example = (
        ["", "어르0007", "이용 중", "시설", choices.get("생활실_구역", [""])[0] if choices.get("생활실_구역") else "", "70", "", ""]
        if entity_type == "resident"
        else ["", "요보03", "재직", "요양보호사", choices.get("직위", [""])[0] if choices.get("직위") else "", "", ""]
    )
    choice_rows = [["항목", "허용값"]]
    for key, values in choices.items():
        for index, value in enumerate(values):
            choice_rows.append([key if index == 0 else "", value])
    return build_xlsx(
        [
            (SHEET_GUIDE, _guide_rows(entity_type)),
            (SHEET_DATA, [headers, example]),
            (SHEET_CHOICES, choice_rows),
        ]
    )


def current_workbook(db: Session, *, organization_id: UUID, entity_type: str, choices: dict[str, list[str]]) -> bytes:
    if entity_type == "resident":
        rows = db.scalars(
            select(Resident)
            .options(selectinload(Resident.room).selectinload(RecipientRoom.floor_unit))
            .where(Resident.organization_id == organization_id)
            .order_by(Resident.display_name, Resident.id)
        ).all()
        data = [RESIDENT_HEADERS] + [
            [
                str(row.id),
                row.display_name,
                "이용 중" if row.is_active and row.status == "active" else "이용 종료",
                SERVICE_LABELS.get(row.service_type, row.service_type),
                row.floor.name if row.floor else "",
                str(row.sort_order),
                "",
                _version(row.updated_at),
            ]
            for row in rows
        ]
    else:
        rows = db.scalars(
            select(Staff)
            .where(Staff.organization_id == organization_id, Staff.deleted_at.is_(None))
            .order_by(Staff.display_name, Staff.id)
        ).all()
        data = [STAFF_HEADERS] + [
            [
                str(row.id),
                row.display_name,
                "재직" if row.is_active and row.employment_status == "active" else "퇴사",
                row.job_title,
                row.position_title or "",
                row.terminated_at.date().isoformat() if row.terminated_at else "",
                _version(row.updated_at),
            ]
            for row in rows
        ]
    choice_rows = [["항목", "허용값"]]
    for key, values in choices.items():
        for index, value in enumerate(values):
            choice_rows.append([key if index == 0 else "", value])
    return build_xlsx([(SHEET_GUIDE, _guide_rows(entity_type)), (SHEET_DATA, data), (SHEET_CHOICES, choice_rows)])


def available_choices(db: Session, *, organization_id: UUID, entity_type: str) -> dict[str, list[str]]:
    if entity_type == "resident":
        units = db.scalars(
            select(OrgUnit).where(
                OrgUnit.organization_id == organization_id,
                OrgUnit.unit_type == "floor",
                OrgUnit.is_active.is_(True),
            ).order_by(OrgUnit.name)
        ).all()
        return {
            "현재_상태": list(RESIDENT_STATUS),
            "서비스_종류": list(SERVICE_TYPES),
            "생활실_구역": [unit.name for unit in units],
        }
    jobs = db.scalars(select(StaffJobCode).where(StaffJobCode.is_active.is_(True)).order_by(StaffJobCode.sort_order)).all()
    positions = db.scalars(
        select(StaffPositionCode).where(
            StaffPositionCode.organization_id == organization_id,
            StaffPositionCode.is_active.is_(True),
        ).order_by(StaffPositionCode.sort_order)
    ).all()
    return {
        "현재_상태": list(STAFF_STATUS),
        "직종": [job.name for job in jobs],
        "직위": [position.name for position in positions],
    }


def _issue(code: str, message: str, field: str | None = None) -> dict[str, Any]:
    return {"code": code, "message": message, "field": field}


def _status_date(raw: str, row_number: int, issues: list[dict[str, Any]]) -> str | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        issues.append(_issue("invalid_date", f"입력 자료 {row_number}행의 상태 변경일은 YYYY-MM-DD 형식으로 입력해 주세요.", "상태_변경일"))
        return None


def _next_staff_code(db: Session, organization_id: UUID, job_title: str, reserved: set[str]) -> str:
    prefix = JOB_PREFIX.get(job_title, "요보")
    existing = set(db.scalars(select(Staff.display_name).where(Staff.organization_id == organization_id)).all()) | reserved
    for number in range(1, 100):
        candidate = f"{prefix}{number:02d}"
        if candidate not in existing:
            return candidate
    raise HTTPException(status_code=409, detail="새 직원 코드를 자동으로 만들 수 없습니다. 관리자에게 문의해 주세요.")


def _next_resident_code(db: Session, organization_id: UUID, reserved: set[str]) -> str:
    existing = set(db.scalars(select(Resident.display_name).where(Resident.organization_id == organization_id)).all()) | reserved
    for number in range(1, 10000):
        candidate = f"어르{number:04d}"
        if candidate not in existing:
            return candidate
    raise HTTPException(status_code=409, detail="새 어르신 코드를 자동으로 만들 수 없습니다. 관리자에게 문의해 주세요.")


def create_preview(
    db: Session,
    *,
    actor: User,
    entity_type: Literal["resident", "staff"],
    original_name: str,
    content: bytes,
) -> BulkTransferBatch:
    if entity_type not in ENTITY_TYPES:
        raise HTTPException(status_code=404, detail="지원하지 않는 자료 종류입니다.")
    if not original_name.lower().endswith(".xlsx"):
        raise HTTPException(status_code=422, detail=".xlsx 형식의 엑셀 파일만 올릴 수 있습니다.")
    workbook = parse_xlsx(content)
    missing_sheets = [name for name in (SHEET_GUIDE, SHEET_DATA, SHEET_CHOICES) if name not in workbook]
    if missing_sheets:
        raise HTTPException(status_code=422, detail=f"필수 시트가 없습니다: {', '.join(missing_sheets)}")
    rows = workbook[SHEET_DATA]
    if not rows:
        raise HTTPException(status_code=422, detail="'입력 자료' 시트가 비어 있습니다.")
    if len(rows) - 1 > MAX_ROWS:
        raise HTTPException(status_code=422, detail=f"한 번에 최대 {MAX_ROWS}행까지 올릴 수 있습니다.")
    expected = RESIDENT_HEADERS if entity_type == "resident" else STAFF_HEADERS
    headers = rows[0]
    sensitive = [header for header in headers if SENSITIVE_HEADER_RE.search(header)]
    if sensitive:
        raise HTTPException(status_code=422, detail="비밀번호·키·토큰·접속주소는 엑셀에 포함할 수 없습니다.")
    missing = [header for header in expected if header not in headers]
    unexpected = [header for header in headers if header not in expected]
    if missing or unexpected:
        parts = []
        if missing:
            parts.append(f"필수 열 누락: {', '.join(missing)}")
        if unexpected:
            parts.append(f"허용되지 않은 열: {', '.join(unexpected)}")
        raise HTTPException(status_code=422, detail=" / ".join(parts))
    header_index = {header: headers.index(header) for header in expected}
    normalized_rows: list[dict[str, str]] = []
    for raw in rows[1:]:
        if not any(value.strip() for value in raw):
            continue
        normalized_rows.append({header: raw[index].strip() if index < len(raw) else "" for header, index in header_index.items()})

    id_counts = Counter(row["내부_식별번호"] for row in normalized_rows if row["내부_식별번호"])
    code_counts = Counter(row["표시_코드"] for row in normalized_rows if row["표시_코드"])
    reserved_codes = set(code_counts)
    choices = available_choices(db, organization_id=actor.organization_id, entity_type=entity_type)
    batch = BulkTransferBatch(
        organization_id=actor.organization_id,
        created_by_id=actor.id,
        entity_type=entity_type,
        status="preview",
        original_name=PurePath(original_name).name[:255],
        file_sha256=sha256(content).hexdigest(),
        reference_at=utcnow(),
        summary={},
        is_test_data=settings.environment != "production",
    )
    db.add(batch)
    db.flush()
    summary: Counter[str] = Counter()

    for offset, row in enumerate(normalized_rows, 2):
        issues: list[dict[str, Any]] = []
        entity_id: UUID | None = None
        current: Resident | Staff | None = None
        raw_id = row["내부_식별번호"]
        if raw_id:
            try:
                entity_id = UUID(raw_id)
            except ValueError:
                issues.append(_issue("invalid_id", f"입력 자료 {offset}행의 내부 식별번호 형식이 올바르지 않습니다.", "내부_식별번호"))
            if id_counts[raw_id] > 1:
                issues.append(_issue("duplicate_id", f"입력 자료 {offset}행의 내부 식별번호가 파일 안에서 중복되었습니다.", "내부_식별번호"))
            if entity_id is not None:
                model = Resident if entity_type == "resident" else Staff
                current = db.scalar(select(model).where(model.id == entity_id, model.organization_id == actor.organization_id))
                if current is None:
                    issues.append(_issue("unknown_id", f"입력 자료 {offset}행의 내부 식별번호를 현재 자료에서 찾을 수 없습니다.", "내부_식별번호"))
        if row["표시_코드"] and code_counts[row["표시_코드"]] > 1:
            issues.append(_issue("duplicate_code", f"입력 자료 {offset}행의 표시 코드가 파일 안에서 중복되었습니다.", "표시_코드"))

        payload: dict[str, Any]
        snapshot: dict[str, Any] | None = None
        change_type = "error" if issues else "unchanged"
        if entity_type == "resident":
            code = row["표시_코드"] or _next_resident_code(db, actor.organization_id, reserved_codes)
            reserved_codes.add(code)
            status_value = RESIDENT_STATUS.get(row["현재_상태"])
            service_type = SERVICE_TYPES.get(row["서비스_종류"])
            if not RESIDENT_CODE_RE.fullmatch(code):
                issues.append(_issue("invalid_code", f"입력 자료 {offset}행의 표시 코드는 '어르0001' 형식이어야 합니다.", "표시_코드"))
            if status_value is None:
                issues.append(_issue("invalid_status", f"입력 자료 {offset}행의 현재 상태는 '이용 중' 또는 '이용 종료'여야 합니다.", "현재_상태"))
            if service_type is None:
                issues.append(_issue("invalid_service", f"입력 자료 {offset}행의 서비스 종류를 선택값 안내에서 골라 주세요.", "서비스_종류"))
            living_space = row["생활실_구역"]
            allowed_living_spaces = set(choices.get("생활실_구역", []))
            if isinstance(current, Resident) and current.floor:
                allowed_living_spaces.add(current.floor.name)
            if living_space and living_space not in allowed_living_spaces:
                issues.append(_issue("invalid_living_space", f"입력 자료 {offset}행의 생활실·구역을 현재 선택값에서 찾을 수 없습니다.", "생활실_구역"))
            try:
                sort_order = int(float(row["정렬_순서"] or "0"))
            except ValueError:
                sort_order = 0
                issues.append(_issue("invalid_sort_order", f"입력 자료 {offset}행의 정렬 순서는 숫자로 입력해 주세요.", "정렬_순서"))
            payload = {"display_code": code, "status": status_value, "service_type": service_type, "living_space": living_space, "sort_order": sort_order, "status_date": _status_date(row["상태_변경일"], offset, issues), "version": row["자료_버전"]}
            if isinstance(current, Resident):
                snapshot = {"display_code": current.display_name, "status": "active" if current.is_active else "inactive", "service_type": current.service_type, "living_space": current.floor.name if current.floor else "", "sort_order": current.sort_order, "status_date": None, "version": _version(current.updated_at)}
            elif db.scalar(select(Resident.id).where(Resident.organization_id == actor.organization_id, Resident.display_name == code)) is not None:
                issues.append(_issue("existing_code", f"입력 자료 {offset}행의 표시 코드는 이미 사용 중입니다. 이름이 같아도 자동으로 기존 자료를 수정하지 않습니다.", "표시_코드"))
        else:
            job_title = row["직종"]
            code = row["표시_코드"] or _next_staff_code(db, actor.organization_id, job_title, reserved_codes)
            reserved_codes.add(code)
            status_value = STAFF_STATUS.get(row["현재_상태"])
            if not STAFF_CODE_RE.fullmatch(code):
                issues.append(_issue("invalid_code", f"입력 자료 {offset}행의 표시 코드는 직군 약칭과 두 자리 번호 형식이어야 합니다.", "표시_코드"))
            if status_value is None:
                issues.append(_issue("invalid_status", f"입력 자료 {offset}행의 현재 상태는 '재직' 또는 '퇴사'여야 합니다.", "현재_상태"))
            allowed_jobs = set(choices.get("직종", []))
            if isinstance(current, Staff) and current.job_title:
                allowed_jobs.add(current.job_title)
            if job_title not in allowed_jobs:
                issues.append(_issue("invalid_job", f"입력 자료 {offset}행의 직종을 선택값 안내에서 골라 주세요.", "직종"))
            position = row["직위"]
            allowed_positions = set(choices.get("직위", []))
            if isinstance(current, Staff) and current.position_title:
                allowed_positions.add(current.position_title)
            if position and position not in allowed_positions:
                issues.append(_issue("invalid_position", f"입력 자료 {offset}행의 직위를 현재 선택값에서 찾을 수 없습니다.", "직위"))
            payload = {"display_code": code, "status": status_value, "job_title": job_title, "position_title": position or None, "status_date": _status_date(row["상태_변경일"], offset, issues), "version": row["자료_버전"]}
            if isinstance(current, Staff):
                snapshot = {"display_code": current.display_name, "status": "active" if current.is_active and current.employment_status == "active" else "retired", "job_title": current.job_title, "position_title": current.position_title, "status_date": current.terminated_at.date().isoformat() if current.terminated_at else None, "version": _version(current.updated_at)}
            elif db.scalar(select(Staff.id).where(Staff.organization_id == actor.organization_id, Staff.display_name == code, Staff.deleted_at.is_(None))) is not None:
                issues.append(_issue("existing_code", f"입력 자료 {offset}행의 표시 코드는 이미 사용 중입니다. 이름이 같아도 자동으로 기존 자료를 수정하지 않습니다.", "표시_코드"))

        if current is not None and snapshot is not None:
            if not row["자료_버전"]:
                issues.append(_issue("missing_version", f"입력 자료 {offset}행은 기존 자료 수정이므로 자료 버전이 필요합니다.", "자료_버전"))
            elif row["자료_버전"] != snapshot["version"]:
                issues.append(_issue("stale_version", f"입력 자료 {offset}행은 내려받은 뒤 현재 자료가 바뀌었습니다. 최신 파일을 다시 받아 확인해 주세요.", "자료_버전"))
            if payload["display_code"] != snapshot["display_code"]:
                issues.append(_issue("code_change_review", f"입력 자료 {offset}행은 기존 코드를 바꾸려 합니다. 코드 연결 보존을 위해 직접 확인이 필요합니다.", "표시_코드"))
        if issues:
            change_type = "conflict" if any(issue["code"] in {"duplicate_id", "duplicate_code", "stale_version", "code_change_review"} for issue in issues) else "error"
        elif current is None:
            change_type = "new" if payload["status"] == "active" else "review"
            if payload["status"] != "active":
                issues.append(_issue("new_inactive_review", f"입력 자료 {offset}행은 새 자료인데 종료 상태입니다. 직원이 직접 확인해 주세요.", "현재_상태"))
        elif payload["status"] != snapshot["status"] and payload["status"] in {"inactive", "retired"}:
            change_type = "deactivate"
        elif any(payload.get(key) != snapshot.get(key) for key in payload if key not in {"version", "status_date"}):
            change_type = "update"
        else:
            change_type = "unchanged"

        item = BulkTransferItem(
            batch_id=batch.id,
            organization_id=actor.organization_id,
            row_number=offset,
            entity_id=entity_id,
            display_code=payload.get("display_code"),
            change_type=change_type,
            status="pending",
            current_snapshot=snapshot,
            incoming_payload=payload,
            issues=issues,
        )
        db.add(item)
        summary[change_type] += 1

    batch.summary = dict(summary)
    record_audit(db, actor_id=actor.id, action="bulk_transfer.previewed", target_type="bulk_transfer_batch", target_id=batch.id, details={"entity_type": entity_type, "row_count": len(normalized_rows), "summary": dict(summary), "raw_file_stored": False})
    db.commit()
    return db.scalar(select(BulkTransferBatch).options(selectinload(BulkTransferBatch.items)).where(BulkTransferBatch.id == batch.id))


def get_batch(db: Session, *, actor: User, batch_id: UUID) -> BulkTransferBatch:
    batch = db.scalar(
        select(BulkTransferBatch)
        .options(selectinload(BulkTransferBatch.items))
        .where(BulkTransferBatch.id == batch_id, BulkTransferBatch.organization_id == actor.organization_id)
    )
    if batch is None:
        raise HTTPException(status_code=404, detail="엑셀 변경 미리보기를 찾을 수 없습니다.")
    return batch


def _room_for_space(db: Session, organization_id: UUID, service_type: str, name: str) -> RecipientRoom | None:
    if service_type == "homecare":
        return None
    floor = None
    if name:
        floor = db.scalar(select(OrgUnit).where(OrgUnit.organization_id == organization_id, OrgUnit.unit_type == "floor", OrgUnit.name == name, OrgUnit.is_active.is_(True)))
    if service_type == "facility" and floor is None:
        raise ValueError("시설 어르신의 생활실·구역을 찾을 수 없습니다.")
    room_name = floor.name if floor else "주간보호 미지정"
    room = db.scalar(select(RecipientRoom).where(RecipientRoom.organization_id == organization_id, RecipientRoom.floor_unit_id == (floor.id if floor else None), RecipientRoom.name == room_name))
    if room is None:
        room = RecipientRoom(organization_id=organization_id, internal_code=f"SMCODI-ROOM-{sha256(f'{service_type}|{name}'.encode()).hexdigest()[:24]}", name=room_name, floor=floor.name if floor else None, floor_unit_id=floor.id if floor else None, is_active=True)
        db.add(room)
        db.flush()
    return room


def apply_batch(db: Session, *, actor: User, batch_id: UUID, item_ids: list[UUID]) -> tuple[BulkTransferBatch, list[UUID]]:
    batch = get_batch(db, actor=actor, batch_id=batch_id)
    selected = set(item_ids)
    if not selected:
        raise HTTPException(status_code=422, detail="적용할 행을 한 개 이상 선택해 주세요.")
    unknown = selected - {item.id for item in batch.items}
    if unknown:
        raise HTTPException(status_code=422, detail="선택한 행 중 현재 미리보기에 없는 항목이 있습니다.")
    if batch.status == "applied":
        return batch, []

    result = Counter(requested=len(selected))
    force_logout_user_ids: list[UUID] = []
    now = utcnow()
    for item in sorted(batch.items, key=lambda value: value.row_number):
        if item.id not in selected:
            continue
        if item.status == "applied":
            result["unchanged"] += 1
            continue
        if item.change_type not in {"new", "update", "deactivate"} or item.issues:
            item.status = "failed"
            result["failed"] += 1
            continue
        payload = item.incoming_payload
        try:
            if batch.entity_type == "resident":
                resident = db.scalar(select(Resident).where(Resident.id == item.entity_id, Resident.organization_id == actor.organization_id).with_for_update()) if item.entity_id else None
                if item.change_type == "new":
                    room = _room_for_space(db, actor.organization_id, payload["service_type"], payload.get("living_space") or "")
                    resident = Resident(organization_id=actor.organization_id, internal_code=f"DEV-FINALIST-RESIDENT-{payload['display_code'].removeprefix('어르')}" if settings.database_schema == "smcodi_finalist" else f"BULK:{sha256((payload['display_code'] + str(now)).encode()).hexdigest()[:20]}", display_name=payload["display_code"], status="active", room_id=room.id if room else None, service_type=payload["service_type"], sort_order=payload["sort_order"], is_test_data=settings.environment != "production", is_active=True)
                    db.add(resident)
                    db.flush()
                    item.entity_id = resident.id
                    result["added"] += 1
                elif resident is None:
                    raise ValueError("현재 어르신 자료를 찾을 수 없습니다.")
                elif item.current_snapshot and _version(resident.updated_at) != item.current_snapshot.get("version"):
                    raise ValueError("미리보기 이후 어르신 자료가 변경되었습니다. 최신 자료를 다시 내려받아 확인해 주세요.")
                elif item.change_type == "deactivate":
                    resident.status = "inactive"
                    resident.is_active = False
                    result["status_changed"] += 1
                else:
                    room = _room_for_space(db, actor.organization_id, payload["service_type"], payload.get("living_space") or "")
                    resident.service_type = payload["service_type"]
                    resident.room_id = room.id if room else None
                    resident.sort_order = payload["sort_order"]
                    result["updated"] += 1
                target_id = resident.id
                before = item.current_snapshot
            else:
                staff = db.scalar(select(Staff).where(Staff.id == item.entity_id, Staff.organization_id == actor.organization_id).with_for_update()) if item.entity_id else None
                if item.change_type == "new":
                    staff = Staff(organization_id=actor.organization_id, internal_code=payload["display_code"], display_name=payload["display_code"], job_title=payload["job_title"], position_title=payload.get("position_title"), employment_status="active", is_active=True, is_test_data=settings.environment != "production")
                    db.add(staff)
                    db.flush()
                    item.entity_id = staff.id
                    result["added"] += 1
                elif staff is None:
                    raise ValueError("현재 직원 자료를 찾을 수 없습니다.")
                elif item.current_snapshot and _version(staff.updated_at) != item.current_snapshot.get("version"):
                    raise ValueError("미리보기 이후 직원 자료가 변경되었습니다. 최신 자료를 다시 내려받아 확인해 주세요.")
                elif item.change_type == "deactivate":
                    if staff.id == actor.staff_id:
                        raise ValueError("현재 로그인한 관리자 자신은 퇴사 처리할 수 없습니다.")
                    staff.employment_status = "retired"
                    staff.is_active = False
                    status_date = payload.get("status_date")
                    staff.terminated_at = datetime.combine(date.fromisoformat(status_date), time.min, tzinfo=timezone.utc) if status_date else now
                    login_user = db.scalar(select(User).where(User.staff_id == staff.id, User.organization_id == actor.organization_id))
                    if login_user is not None:
                        login_user.is_active = False
                        force_logout_user_ids.append(login_user.id)
                        for session in db.scalars(select(LoginSession).where(LoginSession.user_id == login_user.id, LoginSession.revoked_at.is_(None))).all():
                            session.revoked_at = now
                    for membership in db.scalars(select(RoomMembership).where(RoomMembership.staff_id == staff.id, RoomMembership.left_at.is_(None))).all():
                        membership.left_at = now
                    result["status_changed"] += 1
                else:
                    staff.job_title = payload["job_title"]
                    staff.position_title = payload.get("position_title")
                    result["updated"] += 1
                target_id = staff.id
                before = item.current_snapshot
            item.status = "applied"
            item.applied_at = now
            record_audit(
                db,
                actor_id=actor.id,
                action="bulk_transfer.row_applied",
                target_type=batch.entity_type,
                target_id=target_id,
                details={
                    "batch_id": str(batch.id),
                    "row_number": item.row_number,
                    "change_type": item.change_type,
                    "previous_values": before,
                    "new_values": payload,
                },
            )
        except ValueError as error:
            item.status = "failed"
            item.issues = [*item.issues, _issue("apply_failed", str(error))]
            result["failed"] += 1

    pending_applicable = any(item.status != "applied" and item.change_type in {"new", "update", "deactivate"} and not item.issues for item in batch.items)
    batch.status = "partially_applied" if pending_applicable else "applied"
    batch.applied_at = now if batch.status == "applied" else None
    result["not_selected"] = sum(1 for item in batch.items if item.status == "pending")
    batch.apply_result = dict(result)
    record_audit(db, actor_id=actor.id, action="bulk_transfer.applied", target_type="bulk_transfer_batch", target_id=batch.id, details={"entity_type": batch.entity_type, "result": dict(result), "selected_item_ids": [str(value) for value in selected]})
    db.commit()
    return get_batch(db, actor=actor, batch_id=batch.id), force_logout_user_ids


def error_workbook(batch: BulkTransferBatch) -> bytes:
    headers = ["시트", "행", "표시_코드", "처리_구분", "고칠_항목", "오류_내용"]
    rows = [headers]
    for item in sorted(batch.items, key=lambda value: value.row_number):
        if not item.issues and item.status != "failed":
            continue
        if item.issues:
            for issue in item.issues:
                rows.append([SHEET_DATA, item.row_number, item.display_code or "", item.change_type, issue.get("field") or "", issue.get("message") or "확인이 필요합니다."])
        else:
            rows.append([SHEET_DATA, item.row_number, item.display_code or "", item.change_type, "", "적용하지 못했습니다."])
    return build_xlsx([("오류 항목", rows), (SHEET_GUIDE, [["오류 파일 안내", "오류 내용을 고친 뒤 원래 예제 또는 현재 자료 파일에 반영해 다시 올려 주세요."]])])
