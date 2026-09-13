from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Organization, OrgUnit


APPROVED_ORGANIZATION_CATALOG_VERSION = "2026-08-02-v1"


@dataclass(frozen=True)
class ApprovedOrgUnit:
    internal_code: str
    unit_type: str
    name: str
    parent_code: str | None = None


APPROVED_ORGANIZATION_CATALOG = (
    ApprovedOrgUnit("business.facility", "business", "시설"),
    ApprovedOrgUnit("business.daycare", "business", "주간보호"),
    ApprovedOrgUnit("business.homecare", "business", "방문요양"),
    ApprovedOrgUnit(
        "department.facility.welfare", "department", "복지", "business.facility"
    ),
    ApprovedOrgUnit(
        "department.facility.medical", "department", "의료", "business.facility"
    ),
    ApprovedOrgUnit(
        "department.facility.care", "department", "요양", "business.facility"
    ),
    ApprovedOrgUnit(
        "department.facility.nutrition", "department", "영양", "business.facility"
    ),
    ApprovedOrgUnit(
        "department.daycare.welfare", "department", "복지", "business.daycare"
    ),
    ApprovedOrgUnit(
        "department.daycare.medical", "department", "의료", "business.daycare"
    ),
    ApprovedOrgUnit(
        "department.daycare.care", "department", "요양", "business.daycare"
    ),
    ApprovedOrgUnit(
        "department.daycare.nutrition", "department", "영양", "business.daycare"
    ),
    ApprovedOrgUnit(
        "department.homecare.welfare", "department", "복지", "business.homecare"
    ),
    ApprovedOrgUnit(
        "department.homecare.care", "department", "요양", "business.homecare"
    ),
    ApprovedOrgUnit("floor.facility.2", "floor", "2층", "business.facility"),
    ApprovedOrgUnit("floor.facility.3", "floor", "3층", "business.facility"),
    ApprovedOrgUnit("floor.facility.4", "floor", "4층", "business.facility"),
    ApprovedOrgUnit("floor.facility.5", "floor", "5층", "business.facility"),
)


class OrganizationCatalogError(RuntimeError):
    pass


def approved_organization_catalog_fingerprint() -> str:
    payload = {
        "version": APPROVED_ORGANIZATION_CATALOG_VERSION,
        "units": [
            {
                "internal_code": item.internal_code,
                "unit_type": item.unit_type,
                "name": item.name,
                "parent_code": item.parent_code,
            }
            for item in APPROVED_ORGANIZATION_CATALOG
        ],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def ensure_approved_organization_catalog(
    db: Session,
    organization: Organization,
) -> dict[str, OrgUnit]:
    """Create the approved real catalog idempotently and fail on drift."""

    existing_units = list(
        db.scalars(
            select(OrgUnit).where(OrgUnit.organization_id == organization.id)
        ).all()
    )
    by_code = {unit.internal_code: unit for unit in existing_units}
    approved_by_code: dict[str, OrgUnit] = {}

    for item in APPROVED_ORGANIZATION_CATALOG:
        parent = approved_by_code.get(item.parent_code) if item.parent_code else None
        if item.parent_code and parent is None:
            raise OrganizationCatalogError(
                f"승인 조직표의 부모 코드 순서가 잘못되었습니다: {item.internal_code}"
            )

        unit = by_code.get(item.internal_code)
        # 층/생활공간은 과거 설치본의 시작값일 뿐 기관 공통 기준정보가 아니다.
        # 기존 기관의 값은 그대로 보존하지만 새 기관에는 2~5층을 자동 생성하지 않고,
        # 관리자가 생활실·방 번호·구역을 직접 설정하도록 한다.
        if item.unit_type == "floor" and unit is None:
            continue
        if unit is None:
            duplicate_real = next(
                (
                    row
                    for row in existing_units
                    if not row.is_test_data
                    and row.unit_type == item.unit_type
                    and row.name == item.name
                    and row.parent_unit_id == (parent.id if parent else None)
                ),
                None,
            )
            if duplicate_real is not None:
                raise OrganizationCatalogError(
                    "같은 위치에 다른 코드의 실제 조직이 이미 있습니다: "
                    f"{item.internal_code}"
                )
            unit = OrgUnit(
                organization_id=organization.id,
                parent_unit_id=parent.id if parent else None,
                unit_type=item.unit_type,
                internal_code=item.internal_code,
                name=item.name,
                is_active=True,
                is_test_data=False,
            )
            db.add(unit)
            db.flush()
            existing_units.append(unit)
            by_code[item.internal_code] = unit

        expected_parent_id: UUID | None = parent.id if parent else None
        # 기존 설치본의 층은 이름 변경·현재 목록에서 제거할 수 있어야 하므로
        # 고정된 승인 속성과 비교하지 않는다. 내부 코드는 과거 관계 보존용이다.
        if item.unit_type == "floor":
            approved_by_code[item.internal_code] = unit
            continue

        actual = (
            unit.organization_id,
            unit.unit_type,
            unit.name,
            unit.parent_unit_id,
            unit.is_active,
            unit.is_test_data,
        )
        expected = (
            organization.id,
            item.unit_type,
            item.name,
            expected_parent_id,
            True,
            False,
        )
        if actual != expected:
            raise OrganizationCatalogError(
                "승인 조직 코드가 다른 속성으로 이미 사용 중입니다: "
                f"{item.internal_code}"
            )
        approved_by_code[item.internal_code] = unit

    approved_codes = {item.internal_code for item in APPROVED_ORGANIZATION_CATALOG}
    unexpected_real_teams = [
        unit
        for unit in existing_units
        if not unit.is_test_data
        and unit.is_active
        and unit.unit_type == "team"
        and unit.internal_code in approved_codes
    ]
    if unexpected_real_teams:
        raise OrganizationCatalogError("승인 조직표에는 실제 팀 조직이 없습니다.")

    return approved_by_code
