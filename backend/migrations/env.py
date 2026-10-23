"""Fresh-install migration environment; not a legacy database upgrade path."""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from app import ai_analysis, ai_assist_models, confirmed_records, models, smcodi_outbox_models
from app.config import settings
from app.database import Base
from app.public_install_guard import guard_migration

config = context.config
config.set_main_option("sqlalchemy.url", settings.sqlalchemy_database_url.replace("%", "%%"))
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def run_migrations_online():
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}),
                                    prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        # This check precedes schema/version-table creation and all application DDL.
        guard_migration(connection, settings.database_schema, target_metadata.tables)
        quoted = connection.dialect.identifier_preparer.quote_schema(settings.database_schema)
        connection.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {quoted}")
        connection.exec_driver_sql(f"SET search_path TO {quoted}, public")
        connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata,
                          version_table_schema=settings.database_schema, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    # Offline SQL cannot prove that the destination is empty or has this identity.
    raise RuntimeError("NEW_INSTALL_OFFLINE_EXECUTION_DISABLED")
else:
    run_migrations_online()
