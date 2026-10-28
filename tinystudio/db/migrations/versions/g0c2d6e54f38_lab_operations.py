"""Durable project-scoped evidence operations, additive to existing lab tables."""
from alembic import op

revision = "g0c2d6e54f38"
down_revision = "f9b1c5d43e27"
branch_labels = None
depends_on = None

STATEMENTS = ("\nCREATE TABLE lab_operations (\n\tid VARCHAR(32) NOT NULL, \n\tproject_id VARCHAR(32) NOT NULL, \n\tkind VARCHAR(16) NOT NULL, \n\tidempotency_key VARCHAR(120) NOT NULL, \n\tpayload JSONB NOT NULL, \n\tpayload_digest VARCHAR(64) NOT NULL, \n\tstate VARCHAR(16) NOT NULL, \n\tgeneration INTEGER NOT NULL, \n\tclaim_token VARCHAR(64), \n\tlease_until TIMESTAMP WITH TIME ZONE, \n\tdispatch_after TIMESTAMP WITH TIME ZONE, \n\tcreated TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tfinished TIMESTAMP WITH TIME ZONE, \n\tresult JSONB, \n\terror TEXT, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_lab_operation_request UNIQUE (project_id, idempotency_key), \n\tCONSTRAINT ck_lab_operation_kind CHECK (kind IN ('export', 'import')), \n\tCONSTRAINT ck_lab_operation_state CHECK (state IN ('queued', 'running', 'succeeded', 'failed', 'lost', 'cancelled')), \n\tCONSTRAINT ck_lab_operation_generation CHECK (generation >= 0), \n\tFOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE\n)\n\n", 'CREATE INDEX ix_lab_operations_project_id ON lab_operations (project_id)', 'CREATE INDEX ix_lab_operations_state ON lab_operations (state)', "CREATE FUNCTION lab_operation_immutable_identity() RETURNS trigger LANGUAGE plpgsql AS $$\nBEGIN\n  IF ROW(NEW.id, NEW.project_id, NEW.kind, NEW.idempotency_key, NEW.payload, NEW.payload_digest, NEW.created)\n     IS DISTINCT FROM ROW(OLD.id, OLD.project_id, OLD.kind, OLD.idempotency_key, OLD.payload, OLD.payload_digest, OLD.created) THEN\n    RAISE EXCEPTION 'lab operation identity is immutable';\n  END IF;\n  RETURN NEW;\nEND\n$$", 'CREATE TRIGGER lab_operation_identity BEFORE UPDATE ON lab_operations FOR EACH ROW EXECUTE FUNCTION lab_operation_immutable_identity()')


def upgrade():
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade():
    op.drop_table("lab_operations")
    op.execute("DROP FUNCTION lab_operation_immutable_identity()")
