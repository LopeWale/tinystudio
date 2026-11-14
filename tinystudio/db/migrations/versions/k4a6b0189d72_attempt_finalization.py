"""Add durable attempt finalization to the existing operation journal."""
from alembic import op

revision = 'k4a6b0189d72'
down_revision = 'j3f5a9078c61'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint('ck_lab_operation_kind', 'lab_operations', type_='check')
    op.create_check_constraint('ck_lab_operation_kind', 'lab_operations',
        "kind IN ('export','import','data_bridge','artifact_verify','attempt_finalize')")


def downgrade():
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM lab_operations WHERE kind='attempt_finalize') THEN "
               "RAISE EXCEPTION 'retain attempt finalization history before downgrade'; END IF; END $$")
    op.drop_constraint('ck_lab_operation_kind', 'lab_operations', type_='check')
    op.create_check_constraint('ck_lab_operation_kind', 'lab_operations',
                              "kind IN ('export','import','data_bridge','artifact_verify')")
