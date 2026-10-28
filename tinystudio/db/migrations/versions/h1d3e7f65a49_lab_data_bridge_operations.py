"""Durable data promotion, preserving existing operation identities and rows."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "h1d3e7f65a49"
down_revision = "g0c2d6e54f38"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_lab_operation_kind", "lab_operations", type_="check")
    op.create_check_constraint("ck_lab_operation_kind", "lab_operations", "kind IN ('export', 'import', 'data_bridge')")
    op.add_column("lab_operations", sa.Column("error_detail", JSONB(), nullable=True))


def downgrade():
    # Never discard historical operation identities to satisfy an older schema.
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM lab_operations WHERE kind = 'data_bridge') THEN RAISE EXCEPTION 'retain data bridge operations before downgrade'; END IF; END $$")
    op.drop_column("lab_operations", "error_detail")
    op.drop_constraint("ck_lab_operation_kind", "lab_operations", type_="check")
    op.create_check_constraint("ck_lab_operation_kind", "lab_operations", "kind IN ('export', 'import')")
