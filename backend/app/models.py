from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    Column,
    text,
    event,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def uuid_pk() -> Mapped[UUID]:
    return mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )


JSON_DATA = JSON().with_variant(JSONB, "postgresql")
IP_ADDRESS = String(64).with_variant(INET(), "postgresql")


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[UUID] = uuid_pk()
    internal_code: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    service_type: Mapped[str] = mapped_column(String(40), default="facility_care")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class DomainModule(Base):
    __tablename__ = "domain_modules"

    code: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    data_owner: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(30), default="prototype")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_independently_deployable: Mapped[bool] = mapped_column(Boolean, default=True)


class OrgUnit(Base):
    __tablename__ = "organization_units"
    __table_args__ = (
        CheckConstraint(
            "unit_type IN ('business', 'department', 'floor', 'team')",
            name="organization_units_type_check",
        ),
        UniqueConstraint(
            "organization_id",
            "unit_type",
            "internal_code",
            name="uq_organization_unit_code",
        ),
        Index("ix_organization_units_active_type", "organization_id", "is_active", "unit_type"),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    parent_unit_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organization_units.id"), nullable=True
    )
    unit_type: Mapped[str] = mapped_column(String(30), index=True)
    internal_code: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_test_data: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    organization: Mapped[Organization] = relationship()
    parent: Mapped[OrgUnit | None] = relationship(remote_side=[id])

    @property
    def code(self) -> str:
        return self.internal_code


class StaffJobCode(Base):
    __tablename__ = "staff_job_codes"

    code: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    sort_order: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StaffPositionCode(Base):
    __tablename__ = "staff_position_codes"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "internal_code",
            name="uq_staff_position_organization_code",
        ),
        UniqueConstraint(
            "organization_id",
            "name",
            name="uq_staff_position_organization_name",
        ),
        Index(
            "ix_staff_position_organization_active",
            "organization_id",
            "is_active",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    internal_code: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(100))
    sort_order: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Staff(Base):
    __tablename__ = "staff"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "internal_code", name="uq_staff_organization_code"
        ),
        Index("ix_staff_organization_status", "organization_id", "employment_status"),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    internal_code: Mapped[str] = mapped_column(String(80))
    display_name: Mapped[str] = mapped_column(String(100))
    job_title: Mapped[str] = mapped_column(String(100))
    position_title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    employment_status: Mapped[str] = mapped_column(
        String(20), default="active", index=True
    )
    is_test_data: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    terminated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    organization: Mapped[Organization] = relationship()
    organization_assignments: Mapped[list[StaffOrganizationAssignment]] = relationship(
        back_populates="staff",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    job_assignments: Mapped[list[StaffJobAssignment]] = relationship(
        back_populates="staff",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    source_accounts: Mapped[list[StaffSourceAccount]] = relationship(
        back_populates="staff",
        lazy="select",
    )
    service_periods: Mapped[list[StaffServicePeriod]] = relationship(
        back_populates="staff",
        lazy="select",
    )
    service_assignments: Mapped[list[StaffServiceAssignment]] = relationship(
        back_populates="staff",
        lazy="selectin",
    )

    def current_unit(self, unit_type: str) -> OrgUnit | None:
        for assignment in self.organization_assignments:
            if assignment.unit_type == unit_type and assignment.end_date is None:
                return assignment.unit
        return None

    def current_job(self) -> StaffJobAssignment | None:
        for assignment in self.job_assignments:
            if assignment.is_primary and assignment.end_date is None:
                return assignment
        return None


class StaffSourceAccount(Base):
    __tablename__ = "staff_source_accounts"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "source_system",
            "service_type",
            "external_id",
            name="uq_staff_source_account_identity",
        ),
        Index(
            "ix_staff_source_accounts_staff_status",
            "staff_id",
            "employment_status",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    staff_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("staff.id"), nullable=True, index=True
    )
    source_system: Mapped[str] = mapped_column(String(30), default="carefor")
    service_type: Mapped[str] = mapped_column(String(30))
    external_id: Mapped[str] = mapped_column(String(80))
    display_name_snapshot: Mapped[str] = mapped_column(String(100))
    job_name_snapshot: Mapped[str] = mapped_column(String(100), default="")
    employment_status: Mapped[str] = mapped_column(String(20), default="active")
    source_status: Mapped[str] = mapped_column(String(50), default="")
    latest_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    latest_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    staff: Mapped[Staff | None] = relationship(back_populates="source_accounts")
    service_periods: Mapped[list[StaffServicePeriod]] = relationship(
        back_populates="source_account",
        cascade="all, delete-orphan",
    )
    service_assignments: Mapped[list[StaffServiceAssignment]] = relationship(
        back_populates="source_account",
    )


class StaffServicePeriod(Base):
    __tablename__ = "staff_service_periods"
    __table_args__ = (
        CheckConstraint(
            "end_date IS NULL OR end_date > start_date",
            name="staff_service_periods_dates_check",
        ),
        UniqueConstraint(
            "source_account_id",
            "start_date",
            name="uq_staff_service_period_account_start",
        ),
        Index(
            "ix_staff_service_periods_staff_dates",
            "staff_id",
            "start_date",
            "end_date",
        ),
        Index(
            "uq_staff_service_periods_open_account",
            "source_account_id",
            unique=True,
            postgresql_where=text("end_date IS NULL"),
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    staff_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff.id"), index=True
    )
    source_account_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_source_accounts.id", ondelete="CASCADE"), index=True
    )
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    employment_status: Mapped[str] = mapped_column(String(20), default="active")
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    staff: Mapped[Staff] = relationship(back_populates="service_periods")
    source_account: Mapped[StaffSourceAccount] = relationship(
        back_populates="service_periods"
    )
    service_assignments: Mapped[list[StaffServiceAssignment]] = relationship(
        back_populates="service_period"
    )


class StaffServiceAssignment(Base):
    """A service-specific organization and job assignment for one staff member.

    Legacy test accounts keep using ``StaffOrganizationAssignment`` and
    ``StaffJobAssignment``. Carefor-backed staff can have one current row for
    each facility/daycare/homecare service without collapsing those roles into
    a single login-account assignment.
    """

    __tablename__ = "staff_service_assignments"
    __table_args__ = (
        CheckConstraint(
            "service_type IN ('facility', 'daycare', 'homecare')",
            name="staff_service_assignments_service_type_check",
        ),
        CheckConstraint(
            "end_date IS NULL OR end_date > start_date",
            name="staff_service_assignments_dates_check",
        ),
        CheckConstraint(
            "assignment_basis IN ('carefor_auto', 'admin_confirmed', 'legacy_manual')",
            name="staff_service_assignments_basis_check",
        ),
        UniqueConstraint(
            "service_period_id",
            "start_date",
            name="uq_staff_service_assignment_period_start",
        ),
        Index(
            "ix_staff_service_assignments_staff_dates",
            "staff_id",
            "service_type",
            "start_date",
            "end_date",
        ),
        Index(
            "uq_staff_service_assignments_open_service",
            "staff_id",
            "service_type",
            unique=True,
            postgresql_where=text("end_date IS NULL"),
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    staff_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff.id"), index=True
    )
    source_account_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_source_accounts.id"), index=True
    )
    service_period_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_service_periods.id"), index=True
    )
    service_type: Mapped[str] = mapped_column(String(30), index=True)
    business_unit_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organization_units.id"), nullable=True
    )
    department_unit_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organization_units.id"), nullable=True
    )
    floor_unit_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organization_units.id"), nullable=True
    )
    team_unit_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organization_units.id"), nullable=True
    )
    job_code: Mapped[str | None] = mapped_column(
        ForeignKey("staff_job_codes.code"), nullable=True
    )
    job_title_snapshot: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    position_code_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("staff_position_codes.id"), nullable=True
    )
    position_title_snapshot: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    assignment_basis: Mapped[str] = mapped_column(
        String(30), default="carefor_auto"
    )
    source_batch_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("staff_sync_batches.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str] = mapped_column(Text, default="")
    is_test_data: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    staff: Mapped[Staff] = relationship(back_populates="service_assignments")
    source_account: Mapped[StaffSourceAccount] = relationship(
        back_populates="service_assignments"
    )
    service_period: Mapped[StaffServicePeriod] = relationship(
        back_populates="service_assignments"
    )
    business: Mapped[OrgUnit | None] = relationship(
        foreign_keys=[business_unit_id]
    )
    department: Mapped[OrgUnit | None] = relationship(
        foreign_keys=[department_unit_id]
    )
    floor: Mapped[OrgUnit | None] = relationship(foreign_keys=[floor_unit_id])
    team: Mapped[OrgUnit | None] = relationship(foreign_keys=[team_unit_id])
    job: Mapped[StaffJobCode | None] = relationship(foreign_keys=[job_code])
    position: Mapped[StaffPositionCode | None] = relationship(
        foreign_keys=[position_code_id]
    )
    source_batch: Mapped[StaffSyncBatch | None] = relationship(
        foreign_keys=[source_batch_id]
    )


class StaffOrganizationAssignment(Base):
    __tablename__ = "staff_organization_assignments"
    __table_args__ = (
        CheckConstraint(
            "unit_type IN ('business', 'department', 'floor', 'team')",
            name="staff_organization_assignments_type_check",
        ),
        CheckConstraint(
            "end_date IS NULL OR end_date > start_date",
            name="staff_organization_assignments_dates_check",
        ),
        Index(
            "ix_staff_organization_assignments_lookup",
            "organization_id",
            "staff_id",
            "unit_type",
            "end_date",
        ),
        Index(
            "uq_staff_organization_assignments_open_type",
            "staff_id",
            "unit_type",
            unique=True,
            postgresql_where=text("end_date IS NULL"),
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    staff_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff.id", ondelete="CASCADE"), index=True
    )
    unit_id: Mapped[UUID] = mapped_column(
        ForeignKey("organization_units.id"), index=True
    )
    unit_type: Mapped[str] = mapped_column(String(30))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    is_test_data: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    staff: Mapped[Staff] = relationship(back_populates="organization_assignments")
    unit: Mapped[OrgUnit] = relationship()


class StaffJobAssignment(Base):
    __tablename__ = "staff_job_assignments"
    __table_args__ = (
        CheckConstraint(
            "end_date IS NULL OR end_date > start_date",
            name="staff_job_assignments_dates_check",
        ),
        UniqueConstraint(
            "staff_id", "start_date", "job_code", name="uq_staff_job_assignment"
        ),
        Index("ix_staff_job_assignments_history", "staff_id", "start_date", "end_date"),
        Index(
            "uq_staff_job_assignments_open_primary",
            "staff_id",
            unique=True,
            postgresql_where=text("is_primary = true AND end_date IS NULL"),
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    staff_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff.id", ondelete="CASCADE"), index=True
    )
    job_code: Mapped[str] = mapped_column(ForeignKey("staff_job_codes.code"))
    job_title: Mapped[str] = mapped_column(String(100))
    position_title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    staff: Mapped[Staff] = relationship(back_populates="job_assignments")
    job: Mapped[StaffJobCode] = relationship()


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_assignable: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


user_roles = Table(
    "user_roles",
    Base.metadata,
    Column(
        "user_id",
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "role_id",
        Uuid(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    staff_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("staff.id"), nullable=True, unique=True
    )
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    can_process_records: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    organization: Mapped[Organization] = relationship()
    staff: Mapped[Staff | None] = relationship()
    roles: Mapped[list[Role]] = relationship(secondary=user_roles, lazy="selectin")

    @property
    def full_name(self) -> str:
        return self.staff.display_name if self.staff else self.display_name

    @property
    def role(self) -> str:
        codes = {role.code for role in self.roles}
        return "admin" if "admin" in codes else "staff"

    @property
    def employment_status(self) -> str:
        return self.staff.employment_status if self.staff else (
            "active" if self.is_active else "retired"
        )

    @property
    def employee_code(self) -> str | None:
        return self.staff.internal_code if self.staff else None

    @property
    def terminated_at(self) -> datetime | None:
        return self.staff.terminated_at if self.staff else None

    @property
    def business(self) -> OrgUnit | None:
        return self.staff.current_unit("business") if self.staff else None

    @property
    def department(self) -> OrgUnit | None:
        return self.staff.current_unit("department") if self.staff else None

    @property
    def floor(self) -> OrgUnit | None:
        return self.staff.current_unit("floor") if self.staff else None

    @property
    def team(self) -> OrgUnit | None:
        return self.staff.current_unit("team") if self.staff else None

    @property
    def job_assignment(self) -> StaffJobAssignment | None:
        return self.staff.current_job() if self.staff else None

    @property
    def job_code(self) -> str | None:
        assignment = self.job_assignment
        return assignment.job_code if assignment else None

    @property
    def job_name(self) -> str | None:
        assignment = self.job_assignment
        return assignment.job.name if assignment else None

    @property
    def position_title(self) -> str | None:
        if self.staff is None:
            return None
        if self.staff.position_title:
            return self.staff.position_title
        assignment = self.job_assignment
        return assignment.position_title if assignment else None


class PushSubscription(Base):
    __tablename__ = "staff_hub_push_subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "endpoint_hash",
            name="uq_staff_hub_push_subscriptions_endpoint_hash",
        ),
        Index(
            "ix_staff_hub_push_subscriptions_user_active",
            "user_id",
            "is_active",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    login_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("auth_sessions.id", ondelete="CASCADE"), index=True
    )
    endpoint: Mapped[str] = mapped_column(Text)
    endpoint_hash: Mapped[str] = mapped_column(String(64))
    p256dh: Mapped[str] = mapped_column(Text)
    auth: Mapped[str] = mapped_column(Text)
    expiration_time: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class MobilePushDevice(Base):
    __tablename__ = "staff_hub_mobile_push_devices"
    __table_args__ = (
        UniqueConstraint(
            "installation_id",
            name="uq_staff_hub_mobile_push_devices_installation_id",
        ),
        Index(
            "ix_staff_hub_mobile_push_devices_user_active",
            "user_id",
            "is_active",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    login_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("auth_sessions.id", ondelete="CASCADE"), index=True
    )
    installation_id: Mapped[str] = mapped_column(String(80))
    platform: Mapped[str] = mapped_column(String(20), default="android")
    token: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    app_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class VoiceCallInvitation(Base):
    __tablename__ = "voice_call_invitations"
    __table_args__ = (
        Index(
            "ix_voice_call_invitations_org_state_expires",
            "organization_id",
            "state",
            "expires_at",
        ),
    )

    # 클라이언트가 만든 통화 식별번호를 그대로 기본키로 사용한다. 통화
    # 초대는 짧게 유지하지만 프로세스 메모리가 지워져도 미응답 상태를
    # 복구할 수 있도록 서버 DB에 보존한다.
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    room_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_hub_rooms.id", ondelete="CASCADE"), index=True
    )
    caller_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    call_mode: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20), default="ringing", index=True)
    member_count: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class VoiceCallParticipant(Base):
    __tablename__ = "voice_call_participants"
    __table_args__ = (
        UniqueConstraint(
            "call_id",
            "user_id",
            name="uq_voice_call_participants_call_user",
        ),
        Index(
            "ix_voice_call_participants_user_state",
            "user_id",
            "state",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    call_id: Mapped[UUID] = mapped_column(
        ForeignKey("voice_call_invitations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    participant_role: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20), default="ringing", index=True)
    # 네이티브 거절 동작용 원문 토큰은 FCM에 한 번만 전달하고 DB에는
    # SHA-256 해시만 저장한다. 로그인 세션이나 사용자 정보는 넣지 않는다.
    action_token_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class RecipientRoom(Base):
    __tablename__ = "rooms"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "internal_code", name="uq_recipient_room_code"
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    internal_code: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(100))
    floor: Mapped[str | None] = mapped_column(String(60), nullable=True)
    floor_unit_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organization_units.id"), nullable=True, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    floor_unit: Mapped[OrgUnit | None] = relationship()


class Resident(Base):
    __tablename__ = "recipients"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "internal_code", name="uq_recipient_organization_code"
        ),
        Index("ix_recipients_active_room", "organization_id", "is_active", "room_id"),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    internal_code: Mapped[str] = mapped_column(String(80))
    display_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default="active")
    room_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("rooms.id"), nullable=True, index=True
    )
    service_type: Mapped[str] = mapped_column(String(30), index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, index=True)
    is_test_data: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    room: Mapped[RecipientRoom | None] = relationship()

    @property
    def floor(self) -> OrgUnit | None:
        return self.room.floor_unit if self.room else None

    @property
    def floor_id(self) -> UUID | None:
        return self.room.floor_unit_id if self.room else None


class ResidentCarePlanningState(Base):
    __tablename__ = "resident_care_planning_states"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "resident_id",
            "document_type",
            name="uq_resident_care_planning_document",
        ),
        Index(
            "ix_resident_care_planning_resident",
            "organization_id",
            "resident_id",
        ),
        Index(
            "ix_resident_care_planning_due",
            "organization_id",
            "next_due_date",
        ),
        CheckConstraint(
            "document_type IN ('cognitive_function_assessment', "
            "'fall_risk_assessment', 'pressure_ulcer_risk_assessment', "
            "'needs_assessment', 'long_term_care_service_plan')",
            name="resident_care_planning_document_type_check",
        ),
        CheckConstraint(
            "status IN ('not_started', 'draft', 'needs_review', 'confirmed')",
            name="resident_care_planning_status_check",
        ),
        CheckConstraint(
            "cycle_months >= 1 AND cycle_months <= 24",
            name="resident_care_planning_cycle_check",
        ),
        CheckConstraint("version > 0", name="resident_care_planning_version_check"),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    resident_id: Mapped[UUID] = mapped_column(
        ForeignKey("recipients.id", ondelete="RESTRICT"), index=True
    )
    document_type: Mapped[str] = mapped_column(String(60), index=True)
    assessment_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    next_due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    cycle_months: Mapped[int] = mapped_column(Integer, default=6)
    reassess_on_state_change: Mapped[bool] = mapped_column(Boolean, default=True)
    state_change_triggered: Mapped[bool] = mapped_column(Boolean, default=False)
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="not_started", index=True)
    evidence_refs: Mapped[list[str]] = mapped_column(JSON_DATA, default=list)
    known_facts: Mapped[list[str]] = mapped_column(JSON_DATA, default=list)
    questions_required: Mapped[list[str]] = mapped_column(JSON_DATA, default=list)
    professional_review_fields: Mapped[list[str]] = mapped_column(
        JSON_DATA, default=list
    )
    author_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    author_verified_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    author_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_test_data: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    resident: Mapped[Resident] = relationship()
    author_verified_by: Mapped[User | None] = relationship()


class NewAdmissionDraftRecord(Base):
    __tablename__ = "new_admission_drafts"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "resident_id",
            "case_ref",
            name="uq_new_admission_draft_case",
        ),
        Index(
            "ix_new_admission_drafts_resident",
            "organization_id",
            "resident_id",
        ),
        CheckConstraint(
            "processing_scope IN ('internal_only', 'deidentified_dev')",
            name="new_admission_draft_scope_check",
        ),
        CheckConstraint(
            "external_transfer_allowed = false",
            name="new_admission_draft_external_transfer_check",
        ),
        CheckConstraint(
            "status IN ('draft', 'needs_confirmation')",
            name="new_admission_draft_status_check",
        ),
        CheckConstraint(
            "current_revision > 0",
            name="new_admission_draft_current_revision_check",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    resident_id: Mapped[UUID] = mapped_column(
        ForeignKey("recipients.id", ondelete="RESTRICT"), index=True
    )
    case_ref: Mapped[str] = mapped_column(String(80))
    processing_scope: Mapped[str] = mapped_column(String(30))
    external_transfer_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    current_revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="needs_confirmation")
    is_test_data: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    resident: Mapped[Resident] = relationship()
    created_by: Mapped[User] = relationship()


class NewAdmissionDraftRevision(Base):
    __tablename__ = "new_admission_draft_revisions"
    __table_args__ = (
        UniqueConstraint(
            "draft_id",
            "revision",
            name="uq_new_admission_draft_revision",
        ),
        Index(
            "ix_new_admission_draft_revisions_history",
            "draft_id",
            "revision",
        ),
        CheckConstraint(
            "revision > 0",
            name="new_admission_draft_revision_number_check",
        ),
        CheckConstraint(
            "supersedes_revision IS NULL OR supersedes_revision < revision",
            name="new_admission_draft_supersedes_check",
        ),
        CheckConstraint(
            "status IN ('draft', 'needs_confirmation')",
            name="new_admission_draft_revision_status_check",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    draft_id: Mapped[UUID] = mapped_column(
        ForeignKey("new_admission_drafts.id", ondelete="RESTRICT"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    supersedes_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(30))
    bundle_payload: Mapped[dict[str, Any]] = mapped_column(JSON_DATA)
    field_evidence_refs: Mapped[dict[str, list[str]]] = mapped_column(JSON_DATA)
    field_changes: Mapped[list[dict[str, Any]]] = mapped_column(JSON_DATA)
    field_confirmations: Mapped[dict[str, dict[str, Any]]] = mapped_column(JSON_DATA)
    created_by_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    draft: Mapped[NewAdmissionDraftRecord] = relationship()
    created_by: Mapped[User] = relationship()


class AssessmentEvidenceLedgerEntry(Base):
    __tablename__ = "assessment_evidence_ledger_entries"
    __table_args__ = (
        UniqueConstraint(
            "draft_id",
            "source_ref",
            name="uq_assessment_evidence_ledger_source",
        ),
        Index(
            "ix_assessment_evidence_ledger_resident_date",
            "organization_id",
            "resident_id",
            "document_date",
        ),
        CheckConstraint(
            "external_transfer_allowed = false",
            name="assessment_evidence_ledger_external_transfer_check",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    resident_id: Mapped[UUID] = mapped_column(
        ForeignKey("recipients.id", ondelete="RESTRICT"), index=True
    )
    draft_id: Mapped[UUID] = mapped_column(
        ForeignKey("new_admission_drafts.id", ondelete="CASCADE"), index=True
    )
    source_ref: Mapped[str] = mapped_column(String(120))
    document_kind: Mapped[str] = mapped_column(String(100))
    document_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_type: Mapped[str] = mapped_column(String(60))
    organization_role: Mapped[str] = mapped_column(String(60))
    reference_locator: Mapped[str] = mapped_column(String(500))
    raw_extracted_text: Mapped[str] = mapped_column(Text)
    normalized_facts: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_DATA, default=list
    )
    verification_state: Mapped[str] = mapped_column(String(50))
    conflict_groups: Mapped[list[str]] = mapped_column(JSON_DATA, default=list)
    staff_review_required: Mapped[bool] = mapped_column(Boolean, default=False)
    linked_fields: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_DATA, default=list
    )
    external_transfer_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_test_data: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    draft: Mapped[NewAdmissionDraftRecord] = relationship()
    resident: Mapped[Resident] = relationship()
    created_by: Mapped[User] = relationship()


class ResidentAssessmentCycle(Base):
    __tablename__ = "resident_assessment_cycles"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "resident_id",
            "cycle_number",
            name="uq_resident_assessment_cycle_number",
        ),
        Index(
            "ix_resident_assessment_cycles_resident",
            "organization_id",
            "resident_id",
            "cycle_number",
        ),
        CheckConstraint(
            "reason IN ('new_admission', 'periodic_reassessment', "
            "'state_change', 'care_plan_change', 'staff_review')",
            name="resident_assessment_cycle_reason_check",
        ),
        CheckConstraint(
            "source_provider IN ('staff_manual', 'carefor_readonly')",
            name="resident_assessment_cycle_provider_check",
        ),
        CheckConstraint(
            "status IN ('draft', 'needs_confirmation')",
            name="resident_assessment_cycle_status_check",
        ),
        CheckConstraint(
            "cycle_number > 0 AND current_revision > 0",
            name="resident_assessment_cycle_numbers_check",
        ),
        CheckConstraint(
            "external_transfer_allowed = false",
            name="resident_assessment_cycle_external_transfer_check",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    resident_id: Mapped[UUID] = mapped_column(
        ForeignKey("recipients.id", ondelete="RESTRICT"), index=True
    )
    reason: Mapped[str] = mapped_column(String(40), index=True)
    cycle_number: Mapped[int] = mapped_column(Integer)
    assessment_date: Mapped[date] = mapped_column(Date)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_provider: Mapped[str] = mapped_column(String(30), default="staff_manual")
    previous_cycle_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("resident_assessment_cycles.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    current_revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="needs_confirmation")
    external_transfer_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_test_data: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    resident: Mapped[Resident] = relationship(foreign_keys=[resident_id])
    previous_cycle: Mapped[ResidentAssessmentCycle | None] = relationship(
        remote_side="ResidentAssessmentCycle.id",
        foreign_keys=[previous_cycle_id],
    )
    created_by: Mapped[User] = relationship()


class ResidentAssessmentCycleRevision(Base):
    __tablename__ = "resident_assessment_cycle_revisions"
    __table_args__ = (
        UniqueConstraint(
            "cycle_id",
            "revision",
            name="uq_resident_assessment_cycle_revision",
        ),
        Index(
            "ix_resident_assessment_cycle_revisions_history",
            "cycle_id",
            "revision",
        ),
        CheckConstraint(
            "revision > 0",
            name="resident_assessment_cycle_revision_number_check",
        ),
        CheckConstraint(
            "supersedes_revision IS NULL OR supersedes_revision < revision",
            name="resident_assessment_cycle_revision_supersedes_check",
        ),
        CheckConstraint(
            "status IN ('draft', 'needs_confirmation')",
            name="resident_assessment_cycle_revision_status_check",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    cycle_id: Mapped[UUID] = mapped_column(
        ForeignKey("resident_assessment_cycles.id", ondelete="RESTRICT"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    supersedes_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(30))
    baseline_sources: Mapped[list[dict[str, Any]]] = mapped_column(JSON_DATA)
    current_facts: Mapped[list[dict[str, Any]]] = mapped_column(JSON_DATA)
    comparison_items: Mapped[list[dict[str, Any]]] = mapped_column(JSON_DATA)
    selected_plan_candidate_keys: Mapped[list[str]] = mapped_column(JSON_DATA)
    confirmation_questions: Mapped[list[dict[str, Any]]] = mapped_column(JSON_DATA)
    field_changes: Mapped[list[dict[str, Any]]] = mapped_column(JSON_DATA)
    created_by_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    cycle: Mapped[ResidentAssessmentCycle] = relationship()
    created_by: Mapped[User] = relationship()


class ResidentSyncBatch(Base):
    __tablename__ = "recipient_sync_batches"
    __table_args__ = (
        Index(
            "ix_recipient_sync_batches_org_created",
            "organization_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    source: Mapped[str] = mapped_column(
        String(60), default="smcodi_read_only_export"
    )
    original_name: Mapped[str] = mapped_column(String(180))
    file_sha256: Mapped[str] = mapped_column(String(64), index=True)
    source_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(30), default="preview", index=True)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON_DATA, default=dict)
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    applied_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])
    applied_by: Mapped[User | None] = relationship(foreign_keys=[applied_by_id])
    items: Mapped[list[ResidentSyncItem]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="ResidentSyncItem.created_at",
    )


class ResidentSyncItem(Base):
    __tablename__ = "recipient_sync_items"
    __table_args__ = (
        UniqueConstraint(
            "batch_id", "external_id", name="uq_recipient_sync_item_external_id"
        ),
        Index(
            "ix_recipient_sync_items_batch_status",
            "batch_id",
            "status",
            "change_type",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("recipient_sync_batches.id", ondelete="CASCADE"), index=True
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    external_id: Mapped[str] = mapped_column(String(80))
    change_type: Mapped[str] = mapped_column(String(30), index=True)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    current_resident_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("recipients.id"), nullable=True, index=True
    )
    incoming_payload: Mapped[dict[str, Any]] = mapped_column(JSON_DATA)
    current_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSON_DATA, nullable=True
    )
    conflict_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    batch: Mapped[ResidentSyncBatch] = relationship(back_populates="items")
    current_resident: Mapped[Resident | None] = relationship()


class StaffSyncBatch(Base):
    __tablename__ = "staff_sync_batches"
    __table_args__ = (
        Index(
            "ix_staff_sync_batches_org_created",
            "organization_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    source: Mapped[str] = mapped_column(String(60), default="carefor_read_only_capture")
    original_name: Mapped[str] = mapped_column(String(180))
    file_sha256: Mapped[str] = mapped_column(String(64), index=True)
    source_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(30), default="preview", index=True)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON_DATA, default=dict)
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])
    items: Mapped[list[StaffSyncItem]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="StaffSyncItem.created_at",
    )
    review_drafts: Mapped[list[StaffSyncReviewDraft]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="StaffSyncReviewDraft.created_at",
    )


class StaffSyncItem(Base):
    __tablename__ = "staff_sync_items"
    __table_args__ = (
        UniqueConstraint(
            "batch_id", "source_key", name="uq_staff_sync_item_source_key"
        ),
        Index(
            "ix_staff_sync_items_batch_status",
            "batch_id",
            "status",
            "change_type",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_sync_batches.id", ondelete="CASCADE"), index=True
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    source_key: Mapped[str] = mapped_column(String(140))
    service_type: Mapped[str] = mapped_column(String(30))
    external_id: Mapped[str] = mapped_column(String(80))
    change_type: Mapped[str] = mapped_column(String(30), index=True)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    current_staff_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("staff.id"), nullable=True, index=True
    )
    incoming_payload: Mapped[dict[str, Any]] = mapped_column(JSON_DATA)
    current_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSON_DATA, nullable=True
    )
    conflict_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    batch: Mapped[StaffSyncBatch] = relationship(back_populates="items")
    current_staff: Mapped[Staff | None] = relationship()


class StaffSyncReviewDraft(Base):
    __tablename__ = "staff_sync_review_drafts"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "candidate_key",
            name="uq_staff_sync_review_draft_candidate",
        ),
        CheckConstraint(
            "completion_status IN ('draft', 'complete')",
            name="staff_sync_review_drafts_completion_status_check",
        ),
        CheckConstraint(
            "revision >= 1",
            name="staff_sync_review_drafts_revision_check",
        ),
        Index(
            "ix_staff_sync_review_drafts_org_batch",
            "organization_id",
            "batch_id",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_sync_batches.id", ondelete="CASCADE"), index=True
    )
    candidate_key: Mapped[str] = mapped_column(String(40))
    decision_payload: Mapped[dict[str, Any]] = mapped_column(JSON_DATA, default=dict)
    completion_status: Mapped[str] = mapped_column(
        String(20), default="draft", index=True
    )
    completion_issues: Mapped[list[str]] = mapped_column(JSON_DATA, default=list)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    updated_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    batch: Mapped[StaffSyncBatch] = relationship(back_populates="review_drafts")
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])
    updated_by: Mapped[User] = relationship(foreign_keys=[updated_by_id])


class StaffApplicationRun(Base):
    __tablename__ = "staff_application_runs"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "batch_id",
            "plan_fingerprint",
            "change_fingerprint",
            name="uq_staff_application_run_plan",
        ),
        CheckConstraint(
            "status IN ('prepared', 'backup_verified', 'restore_verified', "
            "'evidence_ready', 'stale')",
            name="staff_application_runs_status_check",
        ),
        Index(
            "ix_staff_application_runs_org_created",
            "organization_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_sync_batches.id", ondelete="RESTRICT"), index=True
    )
    manifest_schema_version: Mapped[int] = mapped_column(Integer, default=1)
    plan_schema_version: Mapped[int] = mapped_column(Integer, default=2)
    plan_fingerprint: Mapped[str] = mapped_column(String(64))
    change_fingerprint: Mapped[str] = mapped_column(String(64))
    manifest_fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    organization_catalog_version: Mapped[str] = mapped_column(String(40))
    organization_catalog_fingerprint: Mapped[str] = mapped_column(String(64))
    leave_access_policy_version: Mapped[str] = mapped_column(String(40))
    leave_access_policy_fingerprint: Mapped[str] = mapped_column(String(64))
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DATA)
    plan_summary: Mapped[dict[str, Any]] = mapped_column(JSON_DATA)
    entry_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="prepared", index=True)
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    batch: Mapped[StaffSyncBatch] = relationship()
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])
    entries: Mapped[list[StaffApplicationEntry]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="StaffApplicationEntry.ordinal",
    )
    backup_proof: Mapped[StaffApplicationBackupProof | None] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        uselist=False,
    )
    restore_receipt: Mapped[StaffApplicationRestoreReceipt | None] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        uselist=False,
    )


class StaffApplicationEntry(Base):
    __tablename__ = "staff_application_entries"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "ordinal", name="uq_staff_application_entry_ordinal"
        ),
        UniqueConstraint(
            "run_id", "entry_key", name="uq_staff_application_entry_key"
        ),
        CheckConstraint(
            "entity_type IN ('staff', 'source_account', 'service_period', "
            "'service_assignment')",
            name="staff_application_entries_entity_type_check",
        ),
        CheckConstraint(
            "action IN ('create', 'update')",
            name="staff_application_entries_action_check",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_application_runs.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    entry_key: Mapped[str] = mapped_column(String(140))
    entity_type: Mapped[str] = mapped_column(String(40), index=True)
    action: Mapped[str] = mapped_column(String(20))
    row_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    natural_key: Mapped[dict[str, Any]] = mapped_column(JSON_DATA)
    before_state: Mapped[dict[str, Any] | None] = mapped_column(
        JSON_DATA, nullable=True
    )
    after_state: Mapped[dict[str, Any]] = mapped_column(JSON_DATA)
    before_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    after_fingerprint: Mapped[str] = mapped_column(String(64))
    dependency_keys: Mapped[list[str]] = mapped_column(JSON_DATA, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[StaffApplicationRun] = relationship(back_populates="entries")


class StaffApplicationBackupProof(Base):
    __tablename__ = "staff_application_backup_proofs"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_staff_application_backup_proof_run"),
    )

    id: Mapped[UUID] = uuid_pk()
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_application_runs.id", ondelete="CASCADE"), index=True
    )
    evidence_filename: Mapped[str] = mapped_column(String(220))
    evidence_sha256: Mapped[str] = mapped_column(String(64))
    dump_filename: Mapped[str] = mapped_column(String(220))
    dump_bytes: Mapped[int] = mapped_column(BigInteger)
    dump_sha256: Mapped[str] = mapped_column(String(64))
    database_schema: Mapped[str] = mapped_column(String(80))
    alembic_revision: Mapped[str] = mapped_column(String(80))
    protected_state_fingerprint: Mapped[str] = mapped_column(String(64))
    protected_tables: Mapped[list[dict[str, Any]]] = mapped_column(JSON_DATA)
    source_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON_DATA)
    verified_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[StaffApplicationRun] = relationship(back_populates="backup_proof")
    verified_by: Mapped[User] = relationship(foreign_keys=[verified_by_id])


class StaffApplicationRestoreReceipt(Base):
    __tablename__ = "staff_application_restore_receipts"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_staff_application_restore_receipt_run"),
    )

    id: Mapped[UUID] = uuid_pk()
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_application_runs.id", ondelete="CASCADE"), index=True
    )
    receipt_filename: Mapped[str] = mapped_column(String(220))
    receipt_sha256: Mapped[str] = mapped_column(String(64))
    dump_sha256: Mapped[str] = mapped_column(String(64))
    restored_schema: Mapped[str] = mapped_column(String(80))
    restored_alembic_revision: Mapped[str] = mapped_column(String(80))
    restored_state_fingerprint: Mapped[str] = mapped_column(String(64))
    restored_tables: Mapped[list[dict[str, Any]]] = mapped_column(JSON_DATA)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cleanup_succeeded: Mapped[bool] = mapped_column(Boolean)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON_DATA)
    verified_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[StaffApplicationRun] = relationship(back_populates="restore_receipt")
    verified_by: Mapped[User] = relationship(foreign_keys=[verified_by_id])


class Room(Base):
    __tablename__ = "staff_hub_rooms"
    __table_args__ = (
        CheckConstraint(
            "room_type IN ('all', 'business', 'department', 'floor', 'team', 'job', 'custom', 'self')",
            name="staff_hub_rooms_type_check",
        ),
        Index("ix_staff_hub_rooms_active_type", "organization_id", "is_active", "room_type"),
        Index(
            "uq_staff_hub_rooms_all",
            "organization_id",
            unique=True,
            postgresql_where=text(
                "room_type = 'all' AND unit_id IS NULL AND job_code IS NULL"
            ),
        ),
        Index(
            "uq_staff_hub_rooms_unit",
            "organization_id",
            "unit_id",
            unique=True,
            postgresql_where=text("unit_id IS NOT NULL"),
        ),
        Index(
            "uq_staff_hub_rooms_job",
            "organization_id",
            "job_code",
            unique=True,
            postgresql_where=text("room_type = 'job' AND job_code IS NOT NULL"),
        ),
        Index(
            "uq_staff_hub_rooms_self",
            "organization_id",
            "owner_staff_id",
            unique=True,
            postgresql_where=text(
                "room_type = 'self' AND owner_staff_id IS NOT NULL"
            ),
        ),
        Index(
            "ix_staff_hub_rooms_custom_owner",
            "organization_id",
            "owner_staff_id",
            postgresql_where=text(
                "room_type = 'custom' AND owner_staff_id IS NOT NULL"
            ),
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    kind: Mapped[str] = mapped_column("room_type", String(30), index=True)
    scope_unit_id: Mapped[UUID | None] = mapped_column(
        "unit_id", ForeignKey("organization_units.id"), nullable=True, index=True
    )
    resident_scope_unit_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organization_units.id"), nullable=True, index=True
    )
    job_code: Mapped[str | None] = mapped_column(
        ForeignKey("staff_job_codes.code"), nullable=True
    )
    owner_staff_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("staff.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    resident_scope: Mapped[str] = mapped_column(String(30), default="all")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_test_data: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[UUID | None] = mapped_column(
        "created_by", ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    scope_unit: Mapped[OrgUnit | None] = relationship(foreign_keys=[scope_unit_id])
    resident_scope_unit: Mapped[OrgUnit | None] = relationship(
        foreign_keys=[resident_scope_unit_id]
    )
    job: Mapped[StaffJobCode | None] = relationship()
    owner_staff: Mapped[Staff | None] = relationship(foreign_keys=[owner_staff_id])


class RoomMembership(Base):
    __tablename__ = "staff_hub_room_memberships"
    __table_args__ = (
        CheckConstraint(
            "membership_source IN ('auto', 'manual')",
            name="staff_hub_room_memberships_source_check",
        ),
        Index("ix_staff_hub_room_memberships_staff", "staff_id", "joined_at", "left_at"),
        Index(
            "uq_staff_hub_room_memberships_active",
            "room_id",
            "staff_id",
            unique=True,
            postgresql_where=text("left_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    room_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_hub_rooms.id", ondelete="CASCADE"), index=True
    )
    staff_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column("membership_source", String(20), default="auto")
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_read_message_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    room: Mapped[Room] = relationship()
    staff: Mapped[Staff] = relationship()


class RoomMembershipOverride(Base):
    __tablename__ = "staff_hub_room_membership_overrides"
    __table_args__ = (
        CheckConstraint(
            "override_action IN ('include', 'exclude')",
            name="staff_hub_room_membership_overrides_action_check",
        ),
        UniqueConstraint(
            "room_id",
            "staff_id",
            name="uq_staff_hub_room_membership_override",
        ),
        Index(
            "ix_staff_hub_room_membership_overrides_staff",
            "staff_id",
            "override_action",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    room_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_hub_rooms.id", ondelete="CASCADE"), index=True
    )
    staff_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[str] = mapped_column("override_action", String(20))
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    room: Mapped[Room] = relationship()
    staff: Mapped[Staff] = relationship()


class MessageResidentLink(Base):
    __tablename__ = "staff_hub_message_recipient_links"
    __table_args__ = (
        CheckConstraint(
            "source IN ('manual', 'text_exact', 'ocr_exact', 'audio_transcript')",
            name="staff_hub_message_recipient_links_source_check",
        ),
        CheckConstraint(
            "status IN ('candidate', 'confirmed', 'rejected')",
            name="staff_hub_message_recipient_links_status_check",
        ),
        UniqueConstraint(
            "message_id",
            "recipient_id",
            name="uq_staff_hub_message_recipient_link",
        ),
        Index(
            "ix_staff_hub_message_recipient_links_status",
            "organization_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_hub_messages.id", ondelete="CASCADE"),
        index=True,
    )
    resident_id: Mapped[UUID] = mapped_column(
        "recipient_id",
        ForeignKey("recipients.id"),
        index=True,
    )
    source: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default="candidate")
    reviewed_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    message: Mapped[Message] = relationship(back_populates="resident_links")
    resident: Mapped[Resident] = relationship()
    reviewed_by: Mapped[User | None] = relationship()


class Message(Base):
    __tablename__ = "staff_hub_messages"
    __table_args__ = (
        CheckConstraint(
            "length(btrim(body)) BETWEEN 1 AND 2000",
            name="staff_hub_messages_body_check",
        ),
        CheckConstraint(
            "lifecycle_status IN ('active', 'recalled')",
            name="staff_hub_messages_lifecycle_status_check",
        ),
        Index("ix_staff_hub_messages_room_created", "room_id", "created_at", "id"),
        Index("ix_staff_hub_messages_author_created", "author_user_id", "created_at"),
        Index("ix_staff_hub_messages_lifecycle", "lifecycle_status", "created_at"),
        Index("ix_staff_hub_messages_recall_group", "recall_group_id"),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    room_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_hub_rooms.id", ondelete="CASCADE"), index=True
    )
    sender_id: Mapped[UUID] = mapped_column(
        "author_user_id", ForeignKey("users.id"), index=True
    )
    message_type: Mapped[str] = mapped_column(String(30), default="chat", index=True)
    body: Mapped[str] = mapped_column(Text)
    resident_id: Mapped[UUID | None] = mapped_column(
        "recipient_id", ForeignKey("recipients.id"), nullable=True, index=True
    )
    resident_ref: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    extra_data: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSON_DATA, nullable=True
    )
    lifecycle_status: Mapped[str] = mapped_column(
        String(20), default="active"
    )
    recalled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    recalled_by_id: Mapped[UUID | None] = mapped_column(
        "recalled_by_user_id", ForeignKey("users.id"), nullable=True, index=True
    )
    recall_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    recall_group_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    is_test_data: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    room: Mapped[Room] = relationship()
    sender: Mapped[User] = relationship(foreign_keys=[sender_id])
    recalled_by: Mapped[User | None] = relationship(foreign_keys=[recalled_by_id])
    resident: Mapped[Resident | None] = relationship()
    resident_links: Mapped[list[MessageResidentLink]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        order_by="MessageResidentLink.created_at",
    )
    attachments: Mapped[list[MessageAttachment]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        order_by="MessageAttachment.created_at",
    )
    comments: Mapped[list[MessageComment]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        order_by="MessageComment.created_at",
    )
    action_item: Mapped[ActionItem | None] = relationship(
        back_populates="source_message",
        uselist=False,
        cascade="all, delete-orphan",
    )


class MessageAttachment(Base):
    __tablename__ = "attachments"
    __table_args__ = (
        Index("ix_attachments_entity", "organization_id", "entity_type", "entity_id"),
        Index("ix_attachments_entity_upload_ordinal", "entity_id", "upload_ordinal"),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    owner_module_code: Mapped[str] = mapped_column(
        ForeignKey("domain_modules.code"), default="staff_hub"
    )
    entity_type: Mapped[str] = mapped_column(
        String(80), default="staff_hub_message"
    )
    message_id: Mapped[UUID] = mapped_column(
        "entity_id",
        ForeignKey("staff_hub_messages.id", ondelete="CASCADE"),
        index=True,
    )
    uploader_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    storage_key: Mapped[str] = mapped_column(String(200), unique=True)
    original_name: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column("content_type", String(120))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    upload_ordinal: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    message: Mapped[Message] = relationship(back_populates="attachments")
    uploader: Mapped[User] = relationship()
    text_extraction: Mapped[AttachmentTextExtraction | None] = relationship(
        back_populates="attachment",
        cascade="all, delete-orphan",
        uselist=False,
    )


class AttachmentTextExtraction(Base):
    __tablename__ = "attachment_text_extractions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed', 'reviewed', 'no_text', 'not_required', 'unsupported')",
            name="attachment_text_extractions_status_check",
        ),
        Index(
            "ix_attachment_text_extractions_status_created",
            "status",
            "created_at",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    attachment_id: Mapped[UUID] = mapped_column(
        ForeignKey("attachments.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(30), default="pending")
    provider: Mapped[str] = mapped_column(String(40))
    model_name: Mapped[str] = mapped_column(String(120))
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_service_context: Mapped[str | None] = mapped_column(
        String(30), nullable=True
    )
    suggestion_details: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON_DATA,
        nullable=True,
    )
    reviewed_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    visual_signature: Mapped[list[float] | None] = mapped_column(
        JSON_DATA,
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    reviewed_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    attachment: Mapped[MessageAttachment] = relationship(
        back_populates="text_extraction"
    )
    attempts: Mapped[list[AttachmentTextExtractionAttempt]] = relationship(
        back_populates="extraction",
        cascade="all, delete-orphan",
        order_by="AttachmentTextExtractionAttempt.attempt_number",
    )
    requested_by: Mapped[User] = relationship(foreign_keys=[requested_by_id])
    reviewed_by: Mapped[User | None] = relationship(foreign_keys=[reviewed_by_id])


class AttachmentTextExtractionAttempt(Base):
    __tablename__ = "attachment_text_extraction_attempts"
    __table_args__ = (
        UniqueConstraint(
            "extraction_id",
            "attempt_number",
            name="uq_attachment_text_extraction_attempt_number",
        ),
        Index(
            "ix_attachment_text_extraction_attempts_attachment",
            "attachment_id",
            "attempt_number",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    extraction_id: Mapped[UUID] = mapped_column(
        ForeignKey("attachment_text_extractions.id", ondelete="CASCADE"),
        index=True,
    )
    attachment_id: Mapped[UUID] = mapped_column(
        ForeignKey("attachments.id", ondelete="CASCADE"),
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30))
    provider: Mapped[str] = mapped_column(String(40))
    model_name: Mapped[str] = mapped_column(String(120))
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_service_context: Mapped[str | None] = mapped_column(
        String(30), nullable=True
    )
    suggestion_details: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON_DATA,
        nullable=True,
    )
    reviewed_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    reviewed_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    archived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    extraction: Mapped[AttachmentTextExtraction] = relationship(
        back_populates="attempts"
    )
    requested_by: Mapped[User] = relationship(foreign_keys=[requested_by_id])
    reviewed_by: Mapped[User | None] = relationship(foreign_keys=[reviewed_by_id])


class AttachmentTextRevision(Base):
    """Append-only automatic results and staff finals; no historical backfill."""
    __tablename__ = "attachment_text_revisions"
    __table_args__ = (
        UniqueConstraint("extraction_id", "revision", name="uq_attachment_text_revision"),
        CheckConstraint("kind IN ('automatic', 'staff_review')", name="attachment_text_revision_kind_check"),
    )
    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    attachment_id: Mapped[UUID] = mapped_column(ForeignKey("attachments.id", ondelete="RESTRICT"), index=True)
    extraction_id: Mapped[UUID] = mapped_column(ForeignKey("attachment_text_extractions.id", ondelete="RESTRICT"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(30))
    text_content: Mapped[str] = mapped_column(Text)
    text_sha256: Mapped[str] = mapped_column(String(64))
    original_file_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider: Mapped[str] = mapped_column(String(40))
    model_name: Mapped[str] = mapped_column(String(120))
    source_metadata: Mapped[dict[str, Any]] = mapped_column(JSON_DATA, default=dict)
    selected_resident_ids: Mapped[list[str]] = mapped_column(JSON_DATA, default=list)
    reviewed_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


@event.listens_for(AttachmentTextRevision, "before_update")
@event.listens_for(AttachmentTextRevision, "before_delete")
def _preserve_attachment_text_revision(*_args, **_kwargs):
    raise RuntimeError("Attachment text revisions are immutable; append a new staff review.")


class AttachmentCoordinateReview(Base):
    """Append-only image text placement review created by the visual editor."""

    __tablename__ = "attachment_coordinate_reviews"
    __table_args__ = (
        UniqueConstraint(
            "attachment_id",
            "version_number",
            name="uq_attachment_coordinate_review_version",
        ),
        CheckConstraint(
            "image_width > 0 AND image_height > 0",
            name="attachment_coordinate_reviews_image_size_check",
        ),
        Index(
            "ix_attachment_coordinate_reviews_attachment_version",
            "attachment_id",
            "version_number",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    attachment_id: Mapped[UUID] = mapped_column(
        ForeignKey("attachments.id", ondelete="CASCADE"), index=True
    )
    extraction_id: Mapped[UUID] = mapped_column(
        ForeignKey("attachment_text_extractions.id", ondelete="CASCADE"), index=True
    )
    version_number: Mapped[int] = mapped_column(Integer)
    image_width: Mapped[int] = mapped_column(Integer)
    image_height: Mapped[int] = mapped_column(Integer)
    editor_version: Mapped[str] = mapped_column(String(40))
    document_template: Mapped[str | None] = mapped_column(String(80), nullable=True)
    regions: Mapped[list[dict[str, Any]]] = mapped_column(JSON_DATA, default=list)
    editor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    editor: Mapped[User] = relationship(foreign_keys=[editor_id])


class OcrQualityRegistryEntry(Base):
    """Immutable DEV audit of whether an OCR source is safe for a future use."""

    __tablename__ = "ocr_quality_registry_entries"
    __table_args__ = (
        CheckConstraint(
            "primary_status IN ('rules_usable', 'visual_learning_candidate', "
            "'evaluation_candidate', 'hold', 'excluded', 'hold_or_excluded')",
            name="ocr_quality_registry_entries_status_check",
        ),
        CheckConstraint(
            "member_primary_status IS NULL OR member_primary_status IN "
            "('rules_usable', 'visual_learning_candidate', "
            "'evaluation_candidate', 'hold', 'excluded')",
            name="ocr_quality_registry_entries_member_status_check",
        ),
        CheckConstraint(
            "bundle_size IS NULL OR bundle_size > 0",
            name="ocr_quality_registry_entries_bundle_size_check",
        ),
        UniqueConstraint(
            "assessment_run_id",
            "attachment_id",
            name="uq_ocr_quality_registry_run_attachment",
        ),
        Index(
            "ix_ocr_quality_registry_entries_org_created",
            "organization_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    assessment_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    attachment_id: Mapped[UUID] = mapped_column(
        ForeignKey("attachments.id", ondelete="RESTRICT"), index=True
    )
    extraction_id: Mapped[UUID] = mapped_column(
        ForeignKey("attachment_text_extractions.id", ondelete="RESTRICT"),
        index=True,
    )
    policy_version: Mapped[str] = mapped_column(String(40), default="quality-v1")
    bundle_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    bundle_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    member_primary_status: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )
    primary_status: Mapped[str] = mapped_column(String(40))
    rules_usable: Mapped[bool] = mapped_column(Boolean, default=False)
    visual_learning_candidate: Mapped[bool] = mapped_column(Boolean, default=False)
    evaluation_candidate: Mapped[bool] = mapped_column(Boolean, default=False)
    holdout_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    reasons: Mapped[list[str]] = mapped_column(JSON_DATA, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OcrCorrectionMemory(Base):
    __tablename__ = "staff_hub_ocr_correction_memories"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "recognized_text",
            "corrected_text",
            name="uq_staff_hub_ocr_correction_memory",
        ),
        Index(
            "ix_staff_hub_ocr_correction_memories_lookup",
            "organization_id",
            "occurrence_count",
            "updated_at",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    recognized_text: Mapped[str] = mapped_column(String(80))
    corrected_text: Mapped[str] = mapped_column(String(80))
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)
    last_reviewed_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    last_reviewed_by: Mapped[User] = relationship()


class OcrCorrectionEvent(Base):
    __tablename__ = "staff_hub_ocr_correction_events"
    __table_args__ = (
        CheckConstraint(
            "decision IN "
            "('keep_raw', 'apply_candidate', 'direct_edit', 'needs_review')",
            name="staff_hub_ocr_correction_events_decision_check",
        ),
        Index(
            "ix_staff_hub_ocr_correction_events_org_confirmed",
            "organization_id",
            "confirmed",
            "created_at",
        ),
        Index(
            "ix_staff_hub_ocr_correction_events_extraction",
            "extraction_id",
            "created_at",
        ),
        Index(
            "ix_staff_hub_ocr_correction_events_coordinate_review",
            "coordinate_review_id",
            unique=True,
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    extraction_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("attachment_text_extractions.id", ondelete="SET NULL"),
        nullable=True,
    )
    attachment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("attachments.id", ondelete="SET NULL"),
        nullable=True,
    )
    coordinate_review_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("attachment_coordinate_reviews.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("staff_hub_messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_writer_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(30))
    raw_text: Mapped[str] = mapped_column(Text)
    corrected_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    correction_pairs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_DATA,
        default=list,
    )
    content_type: Mapped[str] = mapped_column(String(40), default="general")
    context_text: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(40))
    model_name: Mapped[str] = mapped_column(String(120))
    visual_signature: Mapped[list[float] | None] = mapped_column(
        JSON_DATA,
        nullable=True,
    )
    selected_candidate_id: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
    )
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    reviewed_by: Mapped[User] = relationship(foreign_keys=[reviewed_by_id])


class HandwritingCorrectionApproval(Base):
    """Append-only staff approval of one handwriting correction draft.

    This table is deliberately separate from OCR learning events and official
    care documents.  A row is created only after every sentence is approved.
    """

    __tablename__ = "handwriting_correction_approvals"
    __table_args__ = (
        UniqueConstraint(
            "approval_group_id",
            "revision",
            name="uq_handwriting_correction_approval_revision",
        ),
        UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_handwriting_correction_approval_idempotency",
        ),
        CheckConstraint(
            "mode IN ('direct_typing', 'full_reading', 'partial_correction', 'story_hint')",
            name="handwriting_correction_approval_mode_check",
        ),
        CheckConstraint(
            "status = 'approved'",
            name="handwriting_correction_approval_status_check",
        ),
        CheckConstraint(
            "revision > 0",
            name="handwriting_correction_approval_revision_check",
        ),
        CheckConstraint(
            "official_record_saved = false AND training_data_adopted = false "
            "AND external_transfer_allowed = false",
            name="handwriting_correction_approval_safety_check",
        ),
        Index(
            "ix_handwriting_correction_approvals_image_history",
            "organization_id",
            "image_attachment_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    approval_group_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    supersedes_approval_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("handwriting_correction_approvals.id", ondelete="RESTRICT"),
        nullable=True,
    )
    image_attachment_id: Mapped[UUID] = mapped_column(
        ForeignKey("attachments.id", ondelete="RESTRICT"), index=True
    )
    audio_attachment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("attachments.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    mode: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="approved")
    initial_ocr: Mapped[str] = mapped_column(Text)
    audio_or_explanation_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_final_text: Mapped[str] = mapped_column(Text)
    sentence_decisions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_DATA, default=list
    )
    evidence_refs: Mapped[list[str]] = mapped_column(JSON_DATA, default=list)
    confidence_state: Mapped[dict[str, Any]] = mapped_column(JSON_DATA, default=dict)
    conflicts_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_by_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    idempotency_key: Mapped[str] = mapped_column(String(64))
    is_test_data: Mapped[bool] = mapped_column(Boolean, default=False)
    official_record_saved: Mapped[bool] = mapped_column(Boolean, default=False)
    training_data_adopted: Mapped[bool] = mapped_column(Boolean, default=False)
    external_transfer_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    approved_by: Mapped[User] = relationship(foreign_keys=[approved_by_id])
    supersedes: Mapped[HandwritingCorrectionApproval | None] = relationship(
        remote_side="HandwritingCorrectionApproval.id",
        foreign_keys=[supersedes_approval_id],
    )


class MessageReadReceipt(Base):
    __tablename__ = "staff_hub_message_read_receipts"
    __table_args__ = (
        UniqueConstraint("message_id", "user_id", name="uq_staff_hub_message_read_user"),
        Index("ix_staff_hub_message_receipts_message_read", "message_id", "read_at"),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_hub_messages.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    is_test_data: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped[User] = relationship(foreign_keys=[user_id])


class MessageComment(Base):
    __tablename__ = "staff_hub_message_comments"
    __table_args__ = (
        Index("ix_staff_hub_message_comments_message", "message_id", "created_at"),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_hub_messages.id", ondelete="CASCADE"), index=True
    )
    author_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    is_test_data: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    author: Mapped[User] = relationship()
    message: Mapped[Message] = relationship(back_populates="comments")


class MessageThreadView(Base):
    __tablename__ = "staff_hub_message_thread_views"
    __table_args__ = (
        UniqueConstraint(
            "message_id",
            "user_id",
            name="uq_staff_hub_message_thread_view_user",
        ),
        Index(
            "ix_staff_hub_message_thread_views_lookup",
            "message_id",
            "user_id",
            "last_viewed_at",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_hub_messages.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    last_viewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ActionItem(Base):
    __tablename__ = "staff_hub_action_items"
    __table_args__ = (
        CheckConstraint(
            "action_type IN ('handover', 'cooperation', 'confirmation')",
            name="staff_hub_action_items_type_check",
        ),
        CheckConstraint(
            "priority IN ('normal', 'important', 'urgent')",
            name="staff_hub_action_items_priority_check",
        ),
        CheckConstraint(
            "status IN ('assigned', 'acknowledged', 'in_progress', 'completed')",
            name="staff_hub_action_items_status_check",
        ),
        Index(
            "ix_staff_hub_action_items_assignee_status",
            "assignee_user_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_staff_hub_action_items_unit_status",
            "assignee_unit_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    source_message_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_hub_messages.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    action_type: Mapped[str] = mapped_column(String(30), index=True)
    assignee_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    assignee_unit_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organization_units.id"), nullable=True, index=True
    )
    priority: Mapped[str] = mapped_column(String(20), default="normal", index=True)
    status: Mapped[str] = mapped_column(String(30), default="assigned", index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_test_data: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    source_message: Mapped[Message] = relationship(back_populates="action_item")
    assignee_user: Mapped[User | None] = relationship(
        foreign_keys=[assignee_user_id]
    )
    assignee_unit: Mapped[OrgUnit | None] = relationship()
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])


class RoomDigest(Base):
    __tablename__ = "staff_hub_room_digests"
    __table_args__ = (
        UniqueConstraint(
            "room_id",
            "period_start",
            "period_end",
            name="uq_staff_hub_room_digest_period",
        ),
        Index(
            "ix_staff_hub_room_digests_period",
            "organization_id",
            "period_start",
            "period_end",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    room_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_hub_rooms.id", ondelete="CASCADE"), index=True
    )
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    comment_count: Mapped[int] = mapped_column(Integer, default=0)
    resident_count: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str] = mapped_column(Text, default="")
    major_points: Mapped[list[dict[str, Any]]] = mapped_column(JSON_DATA, default=list)
    document_counts: Mapped[dict[str, int]] = mapped_column(JSON_DATA, default=dict)
    risk_counts: Mapped[dict[str, int]] = mapped_column(JSON_DATA, default=dict)
    source_message_ids: Mapped[list[str]] = mapped_column(JSON_DATA, default=list)
    generator: Mapped[str] = mapped_column(String(80), default="prototype-room-digest-v1")
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    room: Mapped[Room] = relationship()


class FieldCareBriefingHistory(Base):
    """Append-only DEV briefing history, separate from official care records."""

    __tablename__ = "field_care_briefing_histories"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "scope_key",
            "revision",
            name="uq_field_care_briefing_history_revision",
        ),
        CheckConstraint(
            "revision > 0",
            name="field_care_briefing_history_revision_check",
        ),
        CheckConstraint(
            "official_record_saved = false",
            name="field_care_briefing_history_official_record_check",
        ),
        Index(
            "ix_field_care_briefing_histories_recent",
            "organization_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    created_by_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    scope_key: Mapped[str] = mapped_column(String(64), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    period_start: Mapped[date] = mapped_column(Date, index=True)
    period_end: Mapped[date] = mapped_column(Date, index=True)
    room_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("staff_hub_rooms.id", ondelete="RESTRICT"), nullable=True
    )
    resident_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("recipients.id", ondelete="RESTRICT"), nullable=True
    )
    filters: Mapped[dict[str, Any]] = mapped_column(JSON_DATA, default=dict)
    overall_summary: Mapped[str] = mapped_column(Text)
    daily_care_references: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_DATA, default=list
    )
    consultation_references: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_DATA, default=list
    )
    evidence_refs: Mapped[list[str]] = mapped_column(JSON_DATA, default=list)
    response_payload: Mapped[dict[str, Any]] = mapped_column(JSON_DATA)
    care_reference_count: Mapped[int] = mapped_column(Integer, default=0)
    consultation_reference_count: Mapped[int] = mapped_column(Integer, default=0)
    generator: Mapped[str] = mapped_column(String(120))
    is_test_data: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    official_record_saved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])


class WorkItem(Base):
    __tablename__ = "staff_hub_processing_items"
    __table_args__ = (
        Index("ix_staff_hub_processing_status_created", "status", "created_at"),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    source_message_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_hub_messages.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    resident_id: Mapped[UUID | None] = mapped_column(
        "recipient_id", ForeignKey("recipients.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    source_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DATA)
    document_types: Mapped[list[str] | None] = mapped_column(JSON_DATA, nullable=True)
    processing_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    handled_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    ai_state: Mapped[str] = mapped_column(String(30), default="not_requested")
    ai_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON_DATA, nullable=True)
    ai_generator: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ai_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confirmed_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON_DATA, nullable=True
    )
    confirmed_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_test_data: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    source_message: Mapped[Message] = relationship()
    resident: Mapped[Resident | None] = relationship()
    handled_by: Mapped[User | None] = relationship(foreign_keys=[handled_by_id])
    confirmed_by: Mapped[User | None] = relationship(foreign_keys=[confirmed_by_id])


class WorkItemDocumentDraft(Base):
    __tablename__ = "staff_hub_work_item_document_drafts"
    __table_args__ = (
        UniqueConstraint(
            "work_item_id",
            "document_type",
            "version",
            name="uq_work_item_document_draft_version",
        ),
        Index(
            "ix_work_item_document_drafts_current",
            "work_item_id",
            "is_current",
        ),
        CheckConstraint(
            "document_type IN "
            "('care_service_record', 'nursing_log', 'consultation_log', "
            "'physical_restraint_log', 'program_log')",
            name="work_item_document_drafts_type_check",
        ),
        CheckConstraint(
            "status IN ('draft', 'approved', 'not_used')",
            name="work_item_document_drafts_status_check",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    work_item_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_hub_processing_items.id", ondelete="CASCADE"),
        index=True,
    )
    document_type: Mapped[str] = mapped_column(String(50), index=True)
    content: Mapped[str] = mapped_column(Text)
    verification_questions: Mapped[list[str]] = mapped_column(JSON_DATA, default=list)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    generator: Mapped[str] = mapped_column(String(120), default="prototype-rule-v1")
    change_request: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    approved_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    work_item: Mapped[WorkItem] = relationship()
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_id])
    approved_by: Mapped[User | None] = relationship(foreign_keys=[approved_by_id])


class LoginSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    client_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    impersonated_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    admin_conversation_access_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    user: Mapped[User] = relationship(foreign_keys=[user_id])


class LoginAttempt(Base):
    __tablename__ = "auth_login_attempts"
    __table_args__ = (
        Index("ix_login_attempt_pair_time", "username", "client_key", "attempted_at"),
        Index("ix_login_attempt_client_time", "client_key", "attempted_at"),
    )

    id: Mapped[UUID] = uuid_pk()
    username: Mapped[str] = mapped_column(String(80), index=True)
    client_key: Mapped[str] = mapped_column(String(64), index=True)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_created_action", "organization_id", "created_at", "action"),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    actor_id: Mapped[UUID | None] = mapped_column(
        "actor_user_id", ForeignKey("users.id"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(80), index=True)
    target_type: Mapped[str] = mapped_column("entity_type", String(80))
    target_id: Mapped[UUID | None] = mapped_column(
        "entity_id", Uuid(as_uuid=True), nullable=True
    )
    before_data: Mapped[dict[str, Any] | None] = mapped_column(JSON_DATA, nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(
        "after_data", JSON_DATA, nullable=True
    )
    source_ip: Mapped[str | None] = mapped_column(IP_ADDRESS, nullable=True)
    is_test_data: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BulkTransferBatch(Base):
    """Short-lived, privacy-safe preview for an administrator XLSX transfer."""

    __tablename__ = "bulk_transfer_batches"
    __table_args__ = (
        CheckConstraint(
            "entity_type IN ('resident', 'staff')",
            name="bulk_transfer_batch_entity_type_check",
        ),
        CheckConstraint(
            "status IN ('preview', 'partially_applied', 'applied')",
            name="bulk_transfer_batch_status_check",
        ),
        Index("ix_bulk_transfer_batch_recent", "organization_id", "created_at"),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(30), default="preview", index=True)
    original_name: Mapped[str] = mapped_column(String(255))
    file_sha256: Mapped[str] = mapped_column(String(64))
    reference_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    summary: Mapped[dict[str, Any]] = mapped_column(JSON_DATA, default=dict)
    apply_result: Mapped[dict[str, Any] | None] = mapped_column(JSON_DATA, nullable=True)
    is_test_data: Mapped[bool] = mapped_column(Boolean, default=False)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    items: Mapped[list[BulkTransferItem]] = relationship(
        back_populates="batch", cascade="all, delete-orphan", lazy="selectin"
    )


class BulkTransferItem(Base):
    __tablename__ = "bulk_transfer_items"
    __table_args__ = (
        UniqueConstraint("batch_id", "row_number", name="uq_bulk_transfer_batch_row"),
        Index("ix_bulk_transfer_item_batch_status", "batch_id", "status"),
    )

    id: Mapped[UUID] = uuid_pk()
    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("bulk_transfer_batches.id", ondelete="CASCADE"), index=True
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    row_number: Mapped[int] = mapped_column(Integer)
    entity_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    display_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    change_type: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    current_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON_DATA, nullable=True)
    incoming_payload: Mapped[dict[str, Any]] = mapped_column(JSON_DATA)
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSON_DATA, default=list)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    batch: Mapped[BulkTransferBatch] = relationship(back_populates="items")
