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
        Index("ix_staff_hub_messages_room_created", "room_id", "created_at", "id"),
        Index("ix_staff_hub_messages_author_created", "author_user_id", "created_at"),
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
    is_test_data: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    room: Mapped[Room] = relationship()
    sender: Mapped[User] = relationship()
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
            "status IN ('pending', 'processing', 'completed', 'failed', 'reviewed')",
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
    requested_by: Mapped[User] = relationship(foreign_keys=[requested_by_id])
    reviewed_by: Mapped[User | None] = relationship(foreign_keys=[reviewed_by_id])


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
