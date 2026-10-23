"""Fresh-install identity guard. No schema reset, conversion or implicit stamp."""
from sqlalchemy import inspect, text

PUBLIC_REVISION = "001_chat_public_20261022"


def require_empty_install(objects, public_conflicts=(), revisions=()):
    if set(objects) - {"alembic_version"} or public_conflicts or revisions:
        raise RuntimeError("NEW_INSTALL_REQUIRES_EMPTY_SCHEMA")


def require_public_identity(revisions):
    if tuple(revisions) != (PUBLIC_REVISION,):
        raise RuntimeError("PUBLIC_SCHEMA_IDENTITY_REQUIRED")


def inventory(connection, schema):
    if connection.dialect.name != "postgresql":
        raise RuntimeError("POSTGRESQL_REQUIRED")
    objects = connection.execute(text(
        "SELECT c.relname FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = :schema AND c.relkind IN ('r','p','v','m','S','f')"
    ), {"schema": schema}).scalars().all()
    revisions = []
    if "alembic_version" in objects:
        quoted = connection.dialect.identifier_preparer.quote_schema(schema)
        revisions = connection.execute(text(
            f"SELECT version_num FROM {quoted}.alembic_version"
        )).scalars().all()
    return objects, revisions


def guard_migration(connection, schema, application_tables):
    objects, revisions = inventory(connection, schema)
    inspector = inspect(connection)
    public_objects = set(inspector.get_table_names(schema="public"))
    public_objects.update(inspector.get_view_names(schema="public"))
    conflicts = public_objects.intersection(application_tables) if schema != "public" else set()
    if revisions:
        require_public_identity(revisions)
        if conflicts:
            raise RuntimeError("PUBLIC_SEARCH_PATH_CONFLICT")
        return
    require_empty_install(objects, conflicts, revisions)


def guard_runtime(connection, schema, application_tables):
    objects, revisions = inventory(connection, schema)
    require_public_identity(revisions)
    guard_migration(connection, schema, application_tables)
    if set(application_tables) - set(objects):
        raise RuntimeError("PUBLIC_SCHEMA_INCOMPLETE")
