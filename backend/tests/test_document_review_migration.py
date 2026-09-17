from pathlib import Path
import importlib.util
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


def test_048_additive_upgrade_from_047_preserves_rows_and_retains_rollback_data(monkeypatch, migration_fixture):
    path=Path(__file__).parents[1]/'migrations/postgres_versions/048_attachment_text_review_revisions.py'
    spec=importlib.util.spec_from_file_location('document_review_migration',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    connection = migration_fixture
    # Only this fixture's empty revision table is removed; the suite's schema is untouched.
    connection.execute(text('DROP TABLE attachment_text_revisions'))
    before = connection.execute(text('SELECT to_jsonb(m)::text FROM staff_hub_messages m')).scalars().all()
    assert len(before) == 1
    op=Operations(MigrationContext.configure(connection));monkeypatch.setattr(module,'op',op)
    op.drop_constraint('attachment_text_extractions_status_check','attachment_text_extractions',type_='check')
    op.create_check_constraint('attachment_text_extractions_status_check','attachment_text_extractions',"status IN ('pending','processing','completed','failed','reviewed','no_text','not_required')")
    module.upgrade()
    assert connection.execute(text('SELECT to_jsonb(m)::text FROM staff_hub_messages m')).scalars().all() == before
    assert connection.execute(text('SELECT extracted_text FROM attachment_text_extractions')).scalar() == '합성 원문'
    assert connection.execute(text('SELECT count(*) FROM attachment_text_revisions')).scalar()==0
    connection.execute(text("UPDATE attachment_text_extractions SET status='unsupported'"))
    with pytest.raises(RuntimeError,match='compatible code rollback'):module.downgrade()
    assert connection.execute(text('SELECT status FROM attachment_text_extractions')).scalar() == 'unsupported'
