CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE auth_login_attempts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    username varchar(80) NOT NULL,
    client_key varchar(64) NOT NULL,
    attempted_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_login_attempt_client_time
    ON auth_login_attempts (client_key, attempted_at);
CREATE INDEX ix_auth_login_attempts_attempted_at
    ON auth_login_attempts (attempted_at);
CREATE INDEX ix_auth_login_attempts_username
    ON auth_login_attempts (username);
CREATE INDEX ix_auth_login_attempts_client_key
    ON auth_login_attempts (client_key);
CREATE INDEX ix_login_attempt_pair_time
    ON auth_login_attempts (username, client_key, attempted_at);

CREATE TABLE domain_modules (
    code varchar(80) PRIMARY KEY,
    name varchar(160) NOT NULL,
    data_owner varchar(160) NOT NULL,
    status varchar(30) NOT NULL DEFAULT 'prototype',
    sort_order integer NOT NULL DEFAULT 0,
    is_independently_deployable boolean NOT NULL DEFAULT true
);

CREATE TABLE organizations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    internal_code varchar(80) NOT NULL UNIQUE,
    name varchar(160) NOT NULL,
    service_type varchar(40) NOT NULL DEFAULT 'facility_care',
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_organizations_is_active ON organizations (is_active);

CREATE TABLE roles (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code varchar(80) NOT NULL UNIQUE,
    name varchar(100) NOT NULL,
    description text NOT NULL DEFAULT '',
    sort_order integer NOT NULL DEFAULT 0,
    is_assignable boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE staff_job_codes (
    code varchar(80) PRIMARY KEY,
    name varchar(100) NOT NULL UNIQUE,
    sort_order integer NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE organization_units (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL REFERENCES organizations(id),
    parent_unit_id uuid REFERENCES organization_units(id),
    unit_type varchar(30) NOT NULL,
    internal_code varchar(80) NOT NULL,
    name varchar(100) NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    is_test_data boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT organization_units_type_check
        CHECK (unit_type IN ('business', 'department', 'floor', 'team')),
    CONSTRAINT uq_organization_unit_code
        UNIQUE (organization_id, unit_type, internal_code)
);
CREATE INDEX ix_organization_units_unit_type
    ON organization_units (unit_type);
CREATE INDEX ix_organization_units_organization_id
    ON organization_units (organization_id);
CREATE INDEX ix_organization_units_active_type
    ON organization_units (organization_id, is_active, unit_type);
CREATE INDEX ix_organization_units_is_active
    ON organization_units (is_active);

CREATE TABLE staff (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL REFERENCES organizations(id),
    internal_code varchar(80) NOT NULL,
    display_name varchar(100) NOT NULL,
    job_title varchar(100) NOT NULL,
    employment_status varchar(20) NOT NULL DEFAULT 'active',
    is_test_data boolean NOT NULL DEFAULT false,
    is_active boolean NOT NULL DEFAULT true,
    terminated_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_staff_organization_code UNIQUE (organization_id, internal_code)
);
CREATE INDEX ix_staff_employment_status ON staff (employment_status);
CREATE INDEX ix_staff_is_active ON staff (is_active);
CREATE INDEX ix_staff_organization_id ON staff (organization_id);
CREATE INDEX ix_staff_organization_status
    ON staff (organization_id, employment_status);

CREATE TABLE rooms (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL REFERENCES organizations(id),
    internal_code varchar(80) NOT NULL,
    name varchar(100) NOT NULL,
    floor varchar(60),
    floor_unit_id uuid REFERENCES organization_units(id),
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_recipient_room_code
        UNIQUE (organization_id, internal_code)
);
CREATE INDEX ix_rooms_organization_id ON rooms (organization_id);
CREATE INDEX ix_rooms_floor_unit_id ON rooms (floor_unit_id);

CREATE TABLE users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL REFERENCES organizations(id),
    staff_id uuid UNIQUE REFERENCES staff(id),
    username varchar(80) NOT NULL,
    display_name varchar(100) NOT NULL,
    password_hash varchar(255) NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    can_process_records boolean NOT NULL DEFAULT false,
    must_change_password boolean NOT NULL DEFAULT false,
    password_changed_at timestamptz,
    last_login_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ix_users_username ON users (username);
CREATE INDEX ix_users_can_process_records ON users (can_process_records);
CREATE INDEX ix_users_organization_id ON users (organization_id);
CREATE INDEX ix_users_is_active ON users (is_active);

CREATE TABLE audit_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL REFERENCES organizations(id),
    actor_user_id uuid REFERENCES users(id),
    action varchar(80) NOT NULL,
    entity_type varchar(80) NOT NULL,
    entity_id uuid,
    before_data jsonb,
    after_data jsonb,
    source_ip inet,
    is_test_data boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_audit_events_organization_id
    ON audit_events (organization_id);
CREATE INDEX ix_audit_events_action ON audit_events (action);
CREATE INDEX ix_audit_created_action
    ON audit_events (organization_id, created_at, action);

CREATE TABLE auth_sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash varchar(64) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz,
    user_agent varchar(300),
    client_key varchar(64)
);
CREATE UNIQUE INDEX ix_auth_sessions_token_hash ON auth_sessions (token_hash);
CREATE INDEX ix_auth_sessions_client_key ON auth_sessions (client_key);
CREATE INDEX ix_auth_sessions_user_id ON auth_sessions (user_id);
CREATE INDEX ix_auth_sessions_expires_at ON auth_sessions (expires_at);

CREATE TABLE recipients (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL REFERENCES organizations(id),
    internal_code varchar(80) NOT NULL,
    display_name varchar(100) NOT NULL,
    status varchar(30) NOT NULL DEFAULT 'active',
    room_id uuid REFERENCES rooms(id),
    service_type varchar(30) NOT NULL,
    is_test_data boolean NOT NULL DEFAULT false,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_recipient_organization_code
        UNIQUE (organization_id, internal_code)
);
CREATE INDEX ix_recipients_organization_id ON recipients (organization_id);
CREATE INDEX ix_recipients_service_type ON recipients (service_type);
CREATE INDEX ix_recipients_active_room
    ON recipients (organization_id, is_active, room_id);
CREATE INDEX ix_recipients_room_id ON recipients (room_id);
CREATE INDEX ix_recipients_is_active ON recipients (is_active);

CREATE TABLE staff_hub_rooms (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL REFERENCES organizations(id),
    room_type varchar(30) NOT NULL,
    unit_id uuid REFERENCES organization_units(id),
    job_code varchar(80) REFERENCES staff_job_codes(code),
    name varchar(120) NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    is_test_data boolean NOT NULL DEFAULT false,
    created_by uuid REFERENCES users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT staff_hub_rooms_type_check
        CHECK (room_type IN ('all', 'business', 'department', 'floor', 'team', 'job', 'custom'))
);
CREATE INDEX ix_staff_hub_rooms_organization_id
    ON staff_hub_rooms (organization_id);
CREATE INDEX ix_staff_hub_rooms_active_type
    ON staff_hub_rooms (organization_id, is_active, room_type);
CREATE INDEX ix_staff_hub_rooms_room_type ON staff_hub_rooms (room_type);
CREATE INDEX ix_staff_hub_rooms_is_active ON staff_hub_rooms (is_active);
CREATE INDEX ix_staff_hub_rooms_unit_id ON staff_hub_rooms (unit_id);
CREATE UNIQUE INDEX uq_staff_hub_rooms_all
    ON staff_hub_rooms (organization_id)
    WHERE room_type = 'all' AND unit_id IS NULL AND job_code IS NULL;
CREATE UNIQUE INDEX uq_staff_hub_rooms_unit
    ON staff_hub_rooms (organization_id, unit_id)
    WHERE unit_id IS NOT NULL;
CREATE UNIQUE INDEX uq_staff_hub_rooms_job
    ON staff_hub_rooms (organization_id, job_code)
    WHERE room_type = 'job' AND job_code IS NOT NULL;

CREATE TABLE staff_job_assignments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL REFERENCES organizations(id),
    staff_id uuid NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    job_code varchar(80) NOT NULL REFERENCES staff_job_codes(code),
    job_title varchar(100) NOT NULL,
    position_title varchar(100),
    start_date date NOT NULL,
    end_date date,
    is_primary boolean NOT NULL DEFAULT true,
    note text NOT NULL DEFAULT '',
    created_by uuid REFERENCES users(id),
    updated_by uuid REFERENCES users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT staff_job_assignments_dates_check
        CHECK (end_date IS NULL OR end_date > start_date),
    CONSTRAINT uq_staff_job_assignment
        UNIQUE (staff_id, start_date, job_code)
);
CREATE INDEX ix_staff_job_assignments_staff_id
    ON staff_job_assignments (staff_id);
CREATE INDEX ix_staff_job_assignments_history
    ON staff_job_assignments (staff_id, start_date, end_date);
CREATE INDEX ix_staff_job_assignments_organization_id
    ON staff_job_assignments (organization_id);
CREATE UNIQUE INDEX uq_staff_job_assignments_open_primary
    ON staff_job_assignments (staff_id)
    WHERE is_primary = true AND end_date IS NULL;

CREATE TABLE staff_organization_assignments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL REFERENCES organizations(id),
    staff_id uuid NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    unit_id uuid NOT NULL REFERENCES organization_units(id),
    unit_type varchar(30) NOT NULL,
    start_date date NOT NULL,
    end_date date,
    note text NOT NULL DEFAULT '',
    is_test_data boolean NOT NULL DEFAULT false,
    created_by uuid REFERENCES users(id),
    updated_by uuid REFERENCES users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT staff_organization_assignments_type_check
        CHECK (unit_type IN ('business', 'department', 'floor', 'team')),
    CONSTRAINT staff_organization_assignments_dates_check
        CHECK (end_date IS NULL OR end_date > start_date)
);
CREATE INDEX ix_staff_organization_assignments_lookup
    ON staff_organization_assignments
    (organization_id, staff_id, unit_type, end_date);
CREATE INDEX ix_staff_organization_assignments_staff_id
    ON staff_organization_assignments (staff_id);
CREATE INDEX ix_staff_organization_assignments_organization_id
    ON staff_organization_assignments (organization_id);
CREATE INDEX ix_staff_organization_assignments_unit_id
    ON staff_organization_assignments (unit_id);
CREATE UNIQUE INDEX uq_staff_organization_assignments_open_type
    ON staff_organization_assignments (staff_id, unit_type)
    WHERE end_date IS NULL;

CREATE TABLE user_roles (
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id uuid NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, role_id)
);

CREATE TABLE staff_hub_messages (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL REFERENCES organizations(id),
    room_id uuid NOT NULL REFERENCES staff_hub_rooms(id) ON DELETE CASCADE,
    author_user_id uuid NOT NULL REFERENCES users(id),
    message_type varchar(30) NOT NULL DEFAULT 'chat',
    body text NOT NULL,
    recipient_id uuid REFERENCES recipients(id),
    resident_ref varchar(100),
    metadata jsonb,
    is_test_data boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    edited_at timestamptz,
    CONSTRAINT staff_hub_messages_body_check
        CHECK (length(btrim(body)) BETWEEN 1 AND 2000)
);
CREATE INDEX ix_staff_hub_messages_room_id ON staff_hub_messages (room_id);
CREATE INDEX ix_staff_hub_messages_organization_id
    ON staff_hub_messages (organization_id);
CREATE INDEX ix_staff_hub_messages_message_type
    ON staff_hub_messages (message_type);
CREATE INDEX ix_staff_hub_messages_resident_ref
    ON staff_hub_messages (resident_ref);
CREATE INDEX ix_staff_hub_messages_author_user_id
    ON staff_hub_messages (author_user_id);
CREATE INDEX ix_staff_hub_messages_author_created
    ON staff_hub_messages (author_user_id, created_at);
CREATE INDEX ix_staff_hub_messages_room_created
    ON staff_hub_messages (room_id, created_at, id);
CREATE INDEX ix_staff_hub_messages_recipient_id
    ON staff_hub_messages (recipient_id);

CREATE TABLE staff_hub_room_memberships (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL REFERENCES organizations(id),
    room_id uuid NOT NULL REFERENCES staff_hub_rooms(id) ON DELETE CASCADE,
    staff_id uuid NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    membership_source varchar(20) NOT NULL DEFAULT 'auto',
    joined_at timestamptz NOT NULL DEFAULT now(),
    left_at timestamptz,
    last_read_message_id uuid,
    created_by uuid REFERENCES users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT staff_hub_room_memberships_source_check
        CHECK (membership_source IN ('auto', 'manual'))
);
CREATE INDEX ix_staff_hub_room_memberships_organization_id
    ON staff_hub_room_memberships (organization_id);
CREATE INDEX ix_staff_hub_room_memberships_staff
    ON staff_hub_room_memberships (staff_id, joined_at, left_at);
CREATE INDEX ix_staff_hub_room_memberships_staff_id
    ON staff_hub_room_memberships (staff_id);
CREATE INDEX ix_staff_hub_room_memberships_room_id
    ON staff_hub_room_memberships (room_id);
CREATE UNIQUE INDEX uq_staff_hub_room_memberships_active
    ON staff_hub_room_memberships (room_id, staff_id)
    WHERE left_at IS NULL;

CREATE TABLE attachments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL REFERENCES organizations(id),
    owner_module_code varchar(80) NOT NULL REFERENCES domain_modules(code),
    entity_type varchar(80) NOT NULL DEFAULT 'staff_hub_message',
    entity_id uuid NOT NULL REFERENCES staff_hub_messages(id) ON DELETE CASCADE,
    uploader_id uuid NOT NULL REFERENCES users(id),
    storage_key varchar(200) NOT NULL UNIQUE,
    original_name varchar(255) NOT NULL,
    content_type varchar(120) NOT NULL,
    size_bytes bigint NOT NULL,
    sha256 varchar(64),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_attachments_organization_id ON attachments (organization_id);
CREATE INDEX ix_attachments_entity
    ON attachments (organization_id, entity_type, entity_id);
CREATE INDEX ix_attachments_entity_id ON attachments (entity_id);

CREATE TABLE staff_hub_message_comments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL REFERENCES organizations(id),
    message_id uuid NOT NULL REFERENCES staff_hub_messages(id) ON DELETE CASCADE,
    author_id uuid NOT NULL REFERENCES users(id),
    body text NOT NULL,
    is_test_data boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_staff_hub_message_comments_message_id
    ON staff_hub_message_comments (message_id);
CREATE INDEX ix_staff_hub_message_comments_message
    ON staff_hub_message_comments (message_id, created_at);
CREATE INDEX ix_staff_hub_message_comments_organization_id
    ON staff_hub_message_comments (organization_id);

CREATE TABLE staff_hub_message_read_receipts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL REFERENCES organizations(id),
    message_id uuid NOT NULL REFERENCES staff_hub_messages(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    read_at timestamptz NOT NULL DEFAULT now(),
    is_test_data boolean NOT NULL DEFAULT false,
    CONSTRAINT uq_staff_hub_message_read_user UNIQUE (message_id, user_id)
);
CREATE INDEX ix_staff_hub_message_read_receipts_message_id
    ON staff_hub_message_read_receipts (message_id);
CREATE INDEX ix_staff_hub_message_read_receipts_organization_id
    ON staff_hub_message_read_receipts (organization_id);
CREATE INDEX ix_staff_hub_message_receipts_message_read
    ON staff_hub_message_read_receipts (message_id, read_at);
CREATE INDEX ix_staff_hub_message_read_receipts_user_id
    ON staff_hub_message_read_receipts (user_id);

CREATE TABLE staff_hub_processing_items (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL REFERENCES organizations(id),
    source_message_id uuid NOT NULL
        REFERENCES staff_hub_messages(id) ON DELETE CASCADE,
    recipient_id uuid NOT NULL REFERENCES recipients(id),
    status varchar(30) NOT NULL DEFAULT 'pending',
    document_types jsonb,
    processing_notes text,
    handled_by_id uuid REFERENCES users(id),
    ai_state varchar(30) NOT NULL DEFAULT 'not_requested',
    ai_payload jsonb,
    is_test_data boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_staff_hub_processing_items_organization_id
    ON staff_hub_processing_items (organization_id);
CREATE INDEX ix_staff_hub_processing_items_recipient_id
    ON staff_hub_processing_items (recipient_id);
CREATE UNIQUE INDEX ix_staff_hub_processing_items_source_message_id
    ON staff_hub_processing_items (source_message_id);
CREATE INDEX ix_staff_hub_processing_status_created
    ON staff_hub_processing_items (status, created_at);
CREATE INDEX ix_staff_hub_processing_items_status
    ON staff_hub_processing_items (status);
