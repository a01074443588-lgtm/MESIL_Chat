from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.database import SessionLocal
from app.main import _org_unit_responses, app
from app.models import Organization, OrgUnit, StaffJobCode, StaffPositionCode
from app.organization_catalog import (
    APPROVED_ORGANIZATION_CATALOG,
    OrganizationCatalogError,
    ensure_approved_organization_catalog,
)


def test_approved_organization_catalog_is_complete_and_idempotent() -> None:
    with TestClient(app):
        with SessionLocal() as db:
            organization = db.scalar(select(Organization).limit(1))
            assert organization is not None

            first = ensure_approved_organization_catalog(db, organization)
            db.flush()
            second = ensure_approved_organization_catalog(db, organization)
            db.flush()

            expected_fixed_codes = {
                item.internal_code
                for item in APPROVED_ORGANIZATION_CATALOG
                if item.unit_type != "floor"
            }
            assert set(first) == set(second) == expected_fixed_codes
            assert len(first) == 13
            assert all(first[code].id == second[code].id for code in first)
            assert db.scalar(
                select(func.count())
                .select_from(OrgUnit)
                .where(
                    OrgUnit.organization_id == organization.id,
                    OrgUnit.is_test_data.is_(False),
                    OrgUnit.internal_code.in_(expected_fixed_codes),
                )
            ) == 13
            assert db.scalar(
                select(func.count())
                .select_from(OrgUnit)
                .where(
                    OrgUnit.organization_id == organization.id,
                    OrgUnit.is_test_data.is_(False),
                    OrgUnit.unit_type == "team",
                )
            ) == 0
            assert db.scalar(
                select(func.count())
                .select_from(OrgUnit)
                .where(
                    OrgUnit.organization_id == organization.id,
                    OrgUnit.unit_type == "floor",
                    OrgUnit.internal_code.in_(
                        {
                            item.internal_code
                            for item in APPROVED_ORGANIZATION_CATALOG
                            if item.unit_type == "floor"
                        }
                    ),
                )
            ) == 0

            business_codes = {
                "business.facility",
                "business.daycare",
                "business.homecare",
            }
            assert all(first[code].parent_unit_id is None for code in business_codes)
            for item in APPROVED_ORGANIZATION_CATALOG:
                if item.internal_code in first and item.parent_code is not None:
                    assert first[item.internal_code].parent_unit_id == first[item.parent_code].id

            responses = {
                response.code: response
                for response in _org_unit_responses(db, list(first.values()))
            }
            assert responses["business.facility"].parent_unit_id is None
            assert responses["department.facility.welfare"].parent_unit_name == "시설"
            assert responses["department.daycare.welfare"].parent_unit_name == "주간보호"
            assert responses["department.homecare.care"].parent_unit_name == "방문요양"


def test_approved_organization_catalog_rejects_code_drift() -> None:
    with TestClient(app):
        with SessionLocal() as db:
            organization = Organization(
                internal_code=f"catalog-drift-{uuid4().hex}",
                name="조직표 충돌 시험기관",
                service_type="facility_care",
            )
            db.add(organization)
            db.flush()
            db.add(
                OrgUnit(
                    organization_id=organization.id,
                    unit_type="business",
                    internal_code="business.facility",
                    name="다른 시설명",
                    is_active=True,
                    is_test_data=False,
                )
            )
            db.flush()

            with pytest.raises(OrganizationCatalogError):
                ensure_approved_organization_catalog(db, organization)
            db.rollback()


def test_facility_director_is_seeded_as_job_not_position() -> None:
    with TestClient(app):
        with SessionLocal() as db:
            organization = db.scalar(select(Organization).limit(1))
            assert organization is not None

            job = db.get(StaffJobCode, "facility_director")
            assert job is not None
            assert job.name == "시설장"
            assert job.is_active is True

            assert db.scalar(
                select(StaffPositionCode).where(
                    StaffPositionCode.organization_id == organization.id,
                    StaffPositionCode.name == "시설장",
                )
            ) is None
