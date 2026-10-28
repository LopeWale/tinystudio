"""Private assistant attachment metadata and bounded extraction."""
from alembic import op
import sqlalchemy as sa
revision = 'e8a0b4c32d16'
down_revision = 'd7e9f3a21b05'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('assistant_attachments',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('project_id', sa.String(32), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.String(32), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False), sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False), sa.Column('storage_key', sa.String(500), nullable=False),
        sa.Column('extracted_text', sa.Text(), nullable=False), sa.Column('extracted_chars', sa.Integer(), nullable=False))
    op.create_index('ix_assistant_attachments_project_id', 'assistant_attachments', ['project_id'])
    op.create_index('ix_assistant_attachments_user_id', 'assistant_attachments', ['user_id'])


def downgrade():
    op.drop_table('assistant_attachments')
