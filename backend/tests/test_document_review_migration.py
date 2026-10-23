from pathlib import Path
import importlib.util
import json
import pytest
from sqlalchemy import text


def test_fresh_baseline_rejects_nonempty_schema_and_preserves_document_rows(migration_fixture):
    from app.public_install_guard import require_empty_install
    connection = migration_fixture
    before = connection.execute(text('SELECT to_jsonb(m)::text FROM staff_hub_messages m')).scalars().all()
    assert len(before) == 1
    with pytest.raises(RuntimeError, match='NEW_INSTALL_REQUIRES_EMPTY_SCHEMA'):
        require_empty_install(['staff_hub_messages'])
    assert connection.execute(text('SELECT to_jsonb(m)::text FROM staff_hub_messages m')).scalars().all() == before
    assert connection.execute(text('SELECT extracted_text FROM attachment_text_extractions')).scalar() == '합성 원문'
    connection.execute(text("UPDATE attachment_text_extractions SET status='unsupported'"))
    path = Path(__file__).parents[1]/'migrations/postgres_versions/001_public_baseline.py'
    spec = importlib.util.spec_from_file_location('public_baseline_review',path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    with pytest.raises(RuntimeError, match='DESTRUCTIVE_DOWNGRADE_DISABLED'):
        module.downgrade()
    assert connection.execute(text('SELECT status FROM attachment_text_extractions')).scalar() == 'unsupported'
