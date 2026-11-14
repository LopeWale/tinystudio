"""Durable artifact transfer identities and fenced verification operations."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = 'j3f5a9078c61'
down_revision = 'i2e4f8067b50'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint('ck_lab_operation_kind', 'lab_operations', type_='check')
    op.create_check_constraint('ck_lab_operation_kind', 'lab_operations',
                              "kind IN ('export','import','data_bridge','artifact_verify')")
    op.create_table('lab_transfers',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('project_id', sa.String(32), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('idempotency_key', sa.String(120), nullable=False),
        sa.Column('request_digest', sa.String(64), nullable=False),
        sa.Column('intent', JSONB(), nullable=False), sa.Column('attempt_identity', JSONB(), nullable=True),
        sa.Column('policy', sa.Text(), nullable=False), sa.Column('geometry', JSONB(), nullable=False),
        sa.Column('storage_key', sa.String(500), nullable=False), sa.Column('size', sa.BigInteger(), nullable=False),
        sa.Column('state', sa.String(32), nullable=False), sa.Column('upload_id', sa.Text(), nullable=True),
        sa.Column('parts', JSONB(), nullable=False),
        sa.Column('operation_id', sa.String(32), sa.ForeignKey('lab_operations.id'), nullable=True),
        sa.Column('artifact_id', sa.String(32), sa.ForeignKey('lab_artifacts.id'), nullable=True),
        sa.Column('artifact_receipt', JSONB(), nullable=True), sa.Column('error_class', sa.String(120), nullable=True),
        sa.Column('created', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('project_id', 'idempotency_key', name='uq_lab_transfer_request'),
        sa.CheckConstraint('size >= 0', name='ck_lab_transfer_size'),
        sa.CheckConstraint("state IN ('admitted','initializing','initialization_uncertain','uploading','sealing',"
                           "'completion_uncertain','sealed','verifying','verified','aborting','abort_uncertain','aborted')",
                           name='ck_lab_transfer_state'))
    op.create_index('ix_lab_transfers_project_id', 'lab_transfers', ['project_id'])
    op.create_index('ix_lab_transfers_state', 'lab_transfers', ['state'])
    op.execute('''CREATE FUNCTION lab_transfer_immutable_identity() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF ROW(NEW.id,NEW.project_id,NEW.idempotency_key,NEW.request_digest,NEW.intent,NEW.attempt_identity,
             NEW.policy,NEW.geometry,NEW.storage_key,NEW.size,NEW.created)
         IS DISTINCT FROM ROW(OLD.id,OLD.project_id,OLD.idempotency_key,OLD.request_digest,OLD.intent,OLD.attempt_identity,
             OLD.policy,OLD.geometry,OLD.storage_key,OLD.size,OLD.created) THEN
        RAISE EXCEPTION 'lab transfer identity is immutable';
      END IF;
      IF (OLD.upload_id IS NOT NULL AND NEW.upload_id IS DISTINCT FROM OLD.upload_id)
         OR (OLD.operation_id IS NOT NULL AND NEW.operation_id IS DISTINCT FROM OLD.operation_id)
         OR (OLD.artifact_id IS NOT NULL AND ROW(NEW.artifact_id,NEW.artifact_receipt)
                                            IS DISTINCT FROM ROW(OLD.artifact_id,OLD.artifact_receipt)) THEN
        RAISE EXCEPTION 'retained lab transfer association is immutable';
      END IF;
      IF OLD.state IN ('sealing','completion_uncertain','sealed','verifying','verified','aborting','abort_uncertain','aborted')
         AND NEW.parts IS DISTINCT FROM OLD.parts THEN
        RAISE EXCEPTION 'sealed lab transfer parts are immutable';
      END IF;
      RETURN NEW;
    END $$''')
    op.execute('CREATE TRIGGER lab_transfer_identity BEFORE UPDATE ON lab_transfers '
               'FOR EACH ROW EXECUTE FUNCTION lab_transfer_immutable_identity()')


def downgrade():
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM lab_transfers) OR EXISTS "
               "(SELECT 1 FROM lab_operations WHERE kind='artifact_verify') THEN "
               "RAISE EXCEPTION 'retain artifact transfer history before downgrade'; END IF; END $$")
    op.drop_table('lab_transfers')
    op.execute('DROP FUNCTION lab_transfer_immutable_identity()')
    op.drop_constraint('ck_lab_operation_kind', 'lab_operations', type_='check')
    op.create_check_constraint('ck_lab_operation_kind', 'lab_operations', "kind IN ('export','import','data_bridge')")
