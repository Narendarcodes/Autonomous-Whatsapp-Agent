"""baseline — full schema from current models.

Adoption notes:
- Fresh database: creates every table/index defined by app.models.
- Existing deployment (schema predates Alembic): this migration is a NO-OP;
  mark history instead with `alembic stamp head` after first checkout.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-08-25
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("users"):
        # Existing deployment adopted mid-flight; schema already in place.
        return
    from app.db.database import Base
    import app.models.models  # noqa: F401 — populate metadata

    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("users"):
        return
    from app.db.database import Base
    import app.models.models  # noqa: F401

    Base.metadata.drop_all(bind=bind)
