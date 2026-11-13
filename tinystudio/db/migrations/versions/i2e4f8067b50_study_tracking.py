"""Add project studies, immutable protocol snapshots and collection records."""
from alembic import op
import sqlalchemy as sa

revision = 'i2e4f8067b50'
down_revision = 'h1d3e7f65a49'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('lab_studies',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('project_id', sa.String(32), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('revision', sa.BigInteger(), nullable=False),
        sa.Column('next_seq', sa.BigInteger(), nullable=False),
        sa.Column('idempotency_key', sa.String(120), nullable=False),
        sa.Column('request_digest', sa.String(64), nullable=False),
        sa.Column('created', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('project_id', 'idempotency_key', name='uq_lab_study_request'))
    op.create_index('ix_lab_studies_project_id', 'lab_studies', ['project_id'])
    op.create_table('lab_study_revisions',
        sa.Column('study_id', sa.String(32), sa.ForeignKey('lab_studies.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('revision', sa.BigInteger(), primary_key=True),
        sa.Column('snapshot', sa.Text(), nullable=False), sa.Column('digest', sa.String(64), nullable=False))
    op.create_table('lab_study_records',
        sa.Column('id', sa.String(32), primary_key=True), sa.Column('study_id', sa.String(32), nullable=False),
        sa.Column('revision', sa.BigInteger(), nullable=False), sa.Column('seq', sa.BigInteger(), nullable=False),
        sa.Column('kind', sa.String(120), nullable=False), sa.Column('unit_id', sa.String(500), nullable=False),
        sa.Column('idempotency_key', sa.String(120), nullable=False), sa.Column('request_digest', sa.String(64), nullable=False),
        sa.Column('record', sa.Text(), nullable=False), sa.Column('digest', sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(['study_id', 'revision'], ['lab_study_revisions.study_id', 'lab_study_revisions.revision'], ondelete='CASCADE'),
        sa.UniqueConstraint('study_id', 'seq', name='uq_lab_study_record_sequence'),
        sa.UniqueConstraint('study_id', 'idempotency_key', name='uq_lab_study_record_request'))
    op.create_index('ix_lab_study_records_study_id', 'lab_study_records', ['study_id'])


def downgrade():
    # Keep research history explicit; an operator must export/remove it before
    # choosing an older schema. Never erase it as a side effect of downgrade.
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM lab_studies) THEN RAISE EXCEPTION 'retain study tracking history before downgrade'; END IF; END $$")
    op.drop_table('lab_study_records')
    op.drop_table('lab_study_revisions')
    op.drop_table('lab_studies')
