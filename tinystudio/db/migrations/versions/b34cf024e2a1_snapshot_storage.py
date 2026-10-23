"""Record remote snapshot ownership without changing legacy database snapshots."""
from alembic import op
import sqlalchemy as sa

revision = "b34cf024e2a1"
down_revision = "a119289aeff9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("snapshots", sa.Column("storage_prefix", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("snapshots", "storage_prefix")
