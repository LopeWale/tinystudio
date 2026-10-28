"""Laboratory schema v1 DDL. Freeze when the first migration is released."""

STATEMENTS = (
    '\nCREATE TABLE lab_definitions (\n\tid VARCHAR(32) NOT NULL, \n\tproject_id VARCHAR(32) NOT NULL, \n\tname VARCHAR(200) NOT NULL, \n\trevision INTEGER NOT NULL, \n\tcreated TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE\n)\n\n',
    'CREATE INDEX ix_lab_definitions_project_id ON lab_definitions (project_id)',
    '\nCREATE TABLE lab_revisions (\n\tdefinition_id VARCHAR(32) NOT NULL, \n\trevision INTEGER NOT NULL, \n\tdefinition JSONB NOT NULL, \n\tdigest VARCHAR(64) NOT NULL, \n\tPRIMARY KEY (definition_id, revision), \n\tFOREIGN KEY(definition_id) REFERENCES lab_definitions (id) ON DELETE CASCADE\n)\n\n',
    '\nCREATE TABLE lab_targets (\n\tid VARCHAR(32) NOT NULL, \n\tproject_id VARCHAR(32) NOT NULL, \n\tname VARCHAR(200) NOT NULL, \n\ttoken_hash VARCHAR(64), \n\tcapabilities JSONB NOT NULL, \n\tlast_seen TIMESTAMP WITH TIME ZONE, \n\tenabled BOOLEAN NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_lab_target_name UNIQUE (project_id, name), \n\tFOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE\n)\n\n',
    'CREATE INDEX ix_lab_targets_project_id ON lab_targets (project_id)',
    '\nCREATE TABLE lab_runs (\n\tid VARCHAR(32) NOT NULL, \n\tproject_id VARCHAR(32) NOT NULL, \n\tdefinition_id VARCHAR(32) NOT NULL, \n\trevision INTEGER NOT NULL, \n\ttarget_id VARCHAR(32) NOT NULL, \n\tidempotency_key VARCHAR(120) NOT NULL, \n\tspec JSONB NOT NULL, \n\tspec_digest VARCHAR(64) NOT NULL, \n\tstate VARCHAR(32) NOT NULL, \n\tnext_seq INTEGER NOT NULL, \n\tcancel_requested BOOLEAN NOT NULL, \n\tresult JSONB, \n\terror TEXT, \n\tparent_run_id VARCHAR(32), \n\tjob_id VARCHAR(32), \n\texperiment_id VARCHAR(32), \n\tcreated TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_lab_run_request UNIQUE (project_id, idempotency_key), \n\tFOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, \n\tFOREIGN KEY(definition_id) REFERENCES lab_definitions (id), \n\tFOREIGN KEY(target_id) REFERENCES lab_targets (id), \n\tFOREIGN KEY(parent_run_id) REFERENCES lab_runs (id), \n\tFOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE SET NULL, \n\tFOREIGN KEY(experiment_id) REFERENCES experiments (id) ON DELETE SET NULL\n)\n\n',
    'CREATE INDEX ix_lab_runs_project_id ON lab_runs (project_id)',
    'CREATE INDEX ix_lab_runs_state ON lab_runs (state)',
    'CREATE INDEX ix_lab_runs_target_id ON lab_runs (target_id)',
    '\nCREATE TABLE lab_dispatch (\n\trun_id VARCHAR(32) NOT NULL, \n\tavailable TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (run_id), \n\tFOREIGN KEY(run_id) REFERENCES lab_runs (id) ON DELETE CASCADE\n)\n\n',
    'CREATE INDEX ix_lab_dispatch_available ON lab_dispatch (available)',
    '\nCREATE TABLE lab_attempts (\n\tid VARCHAR(32) NOT NULL, \n\trun_id VARCHAR(32) NOT NULL, \n\ttarget_id VARCHAR(32) NOT NULL, \n\tnumber INTEGER NOT NULL, \n\ttoken_hash VARCHAR(64) NOT NULL, \n\tlease_until TIMESTAMP WITH TIME ZONE NOT NULL, \n\tstate VARCHAR(32) NOT NULL, \n\truntime JSONB NOT NULL, \n\tresult_digest VARCHAR(64), \n\tcreated TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_lab_attempt_number UNIQUE (run_id, number), \n\tFOREIGN KEY(run_id) REFERENCES lab_runs (id) ON DELETE CASCADE, \n\tFOREIGN KEY(target_id) REFERENCES lab_targets (id)\n)\n\n',
    'CREATE INDEX ix_lab_attempts_run_id ON lab_attempts (run_id)',
    '\nCREATE TABLE lab_events (\n\trun_id VARCHAR(32) NOT NULL, \n\tseq INTEGER NOT NULL, \n\tkind VARCHAR(80) NOT NULL, \n\tpayload JSONB NOT NULL, \n\ttime TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (run_id, seq), \n\tFOREIGN KEY(run_id) REFERENCES lab_runs (id) ON DELETE CASCADE\n)\n\n',
    '\nCREATE TABLE lab_artifacts (\n\tid VARCHAR(32) NOT NULL, \n\tproject_id VARCHAR(32) NOT NULL, \n\tattempt_id VARCHAR(32), \n\tname VARCHAR(255) NOT NULL, \n\tsha256 VARCHAR(64) NOT NULL, \n\tsize BIGINT NOT NULL, \n\tmedia_type VARCHAR(120) NOT NULL, \n\tstorage_key VARCHAR(500) NOT NULL, \n\trole VARCHAR(64) NOT NULL, \n\tvisibility VARCHAR(16) NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, \n\tFOREIGN KEY(attempt_id) REFERENCES lab_attempts (id) ON DELETE CASCADE\n)\n\n',
    'CREATE INDEX ix_lab_artifacts_attempt_id ON lab_artifacts (attempt_id)',
    'CREATE INDEX ix_lab_artifacts_project_id ON lab_artifacts (project_id)',
    'CREATE INDEX ix_lab_artifacts_sha256 ON lab_artifacts (sha256)',
)

TABLE_NAMES = ('lab_definitions', 'lab_revisions', 'lab_targets', 'lab_runs', 'lab_dispatch', 'lab_attempts', 'lab_events', 'lab_artifacts')


def upgrade(op):
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade(op):
    for name in reversed(TABLE_NAMES):
        op.drop_table(name)
