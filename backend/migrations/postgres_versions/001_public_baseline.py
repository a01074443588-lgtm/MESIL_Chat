"""Independent fresh-install baseline. Never use on an existing Chat database."""
import json
from pathlib import Path

from alembic import op
from app.public_install_guard import PUBLIC_REVISION, inventory, require_empty_install
from app.config import settings

revision = PUBLIC_REVISION
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    objects, revisions = inventory(connection, settings.database_schema)
    require_empty_install(objects, revisions=revisions)
    statements = json.loads((Path(__file__).parents[1] / "sql/public_baseline.json").read_text(encoding="utf-8"))
    for statement in statements:
        # Statements are frozen individually; do not split PL/pgSQL at semicolons.
        op.execute(statement)


def downgrade():
    raise RuntimeError("DESTRUCTIVE_DOWNGRADE_DISABLED_RESTORE_APPROVED_BACKUP_INSTEAD")
