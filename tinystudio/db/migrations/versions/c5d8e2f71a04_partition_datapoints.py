"""Hash-partition datapoints by dataset and store per-dataset bucket counts.

PostgreSQL cannot convert a table to a partitioned table in place, so the rows are copied into a new
partitioned table and the old one is dropped. Hash partitioning (16 partitions) keeps the partition count
fixed however many datasets a team creates, needs no DDL when a dataset is created, and every dataset query
filters on ``dataset_id`` so the planner prunes to one partition. The primary key becomes
``(dataset_id, id)`` because a partitioned table's keys must include the partition key; positions become
unique per dataset (renumbered here in their existing order).

Revision ID: c5d8e2f71a04
Revises: b34cf024e2a1
"""
from __future__ import annotations

from alembic import op

revision = "c5d8e2f71a04"
down_revision = "b34cf024e2a1"
branch_labels = None
depends_on = None

PARTITIONS = 16
COLUMNS = "id, dataset_id, position, messages, bucket, labels, source, rating, rejected, created"


def upgrade() -> None:
    op.execute("ALTER TABLE datapoints RENAME TO datapoints_legacy")
    op.execute("ALTER TABLE datapoints_legacy RENAME CONSTRAINT datapoints_pkey TO datapoints_legacy_pkey")
    op.execute("ALTER TABLE datapoints_legacy RENAME CONSTRAINT datapoints_dataset_id_fkey TO datapoints_legacy_dataset_id_fkey")
    op.execute("ALTER INDEX ix_datapoints_dataset_bucket RENAME TO ix_datapoints_legacy_bucket")
    op.execute("ALTER INDEX ix_datapoints_dataset_position RENAME TO ix_datapoints_legacy_position")
    op.execute(
        """
        CREATE TABLE datapoints (
            dataset_id varchar(32) NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
            id bigint NOT NULL DEFAULT nextval('datapoints_id_seq'::regclass),
            position integer NOT NULL,
            messages jsonb NOT NULL,
            bucket varchar(16) NOT NULL,
            labels jsonb NOT NULL,
            source varchar(200),
            rating double precision,
            rejected text,
            created timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT datapoints_pkey PRIMARY KEY (dataset_id, id)
        ) PARTITION BY HASH (dataset_id)
        """
    )
    for i in range(PARTITIONS):
        op.execute(f"CREATE TABLE datapoints_p{i:02d} PARTITION OF datapoints FOR VALUES WITH (MODULUS {PARTITIONS}, REMAINDER {i})")
    op.execute("CREATE UNIQUE INDEX ix_datapoints_dataset_position ON datapoints (dataset_id, position)")
    op.execute("CREATE INDEX ix_datapoints_dataset_bucket ON datapoints (dataset_id, bucket, position)")
    op.execute(
        f"""
        INSERT INTO datapoints ({COLUMNS})
        SELECT id, dataset_id, (row_number() OVER (PARTITION BY dataset_id ORDER BY position, id) - 1)::int,
               messages, bucket, labels, source, rating, rejected, created
        FROM datapoints_legacy
        """
    )
    op.execute("ALTER SEQUENCE datapoints_id_seq OWNED BY datapoints.id")
    op.execute("DROP TABLE datapoints_legacy")
    op.execute("ANALYZE datapoints")  # autovacuum does not analyze partitioned parents

    op.execute("ALTER TABLE datasets ADD COLUMN counts jsonb NOT NULL DEFAULT '{}'::jsonb")
    op.execute(
        """
        UPDATE datasets d SET counts = COALESCE(
            (SELECT jsonb_object_agg(s.bucket, s.n) FROM (SELECT bucket, count(*) AS n FROM datapoints WHERE dataset_id = d.id GROUP BY bucket) s),
            '{}'::jsonb)
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE datasets DROP COLUMN counts")
    op.execute("ALTER TABLE datapoints RENAME TO datapoints_partitioned")
    op.execute("ALTER TABLE datapoints_partitioned RENAME CONSTRAINT datapoints_pkey TO datapoints_partitioned_pkey")
    op.execute("ALTER INDEX ix_datapoints_dataset_bucket RENAME TO ix_datapoints_partitioned_bucket")
    op.execute("ALTER INDEX ix_datapoints_dataset_position RENAME TO ix_datapoints_partitioned_position")
    op.execute(
        """
        CREATE TABLE datapoints (
            id bigint NOT NULL DEFAULT nextval('datapoints_id_seq'::regclass),
            dataset_id varchar(32) NOT NULL,
            position integer NOT NULL,
            messages jsonb NOT NULL,
            bucket varchar(16) NOT NULL,
            labels jsonb NOT NULL,
            source varchar(200),
            rating double precision,
            rejected text,
            created timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT datapoints_pkey PRIMARY KEY (id),
            CONSTRAINT datapoints_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES datasets(id) ON DELETE CASCADE
        )
        """
    )
    op.execute(f"INSERT INTO datapoints ({COLUMNS}) SELECT {COLUMNS} FROM datapoints_partitioned")
    op.execute("ALTER SEQUENCE datapoints_id_seq OWNED BY datapoints.id")
    op.execute("DROP TABLE datapoints_partitioned")
    op.execute("CREATE INDEX ix_datapoints_dataset_bucket ON datapoints (dataset_id, bucket)")
    op.execute("CREATE INDEX ix_datapoints_dataset_position ON datapoints (dataset_id, position)")
