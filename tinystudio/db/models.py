"""The operational schema. Rows and metadata live here; weights, checkpoints, and activation captures
live under the data directory and are referenced by relative path."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, Sequence, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


class Instance(Base):
    """One row: secrets shared by every process of this deployment."""

    __tablename__ = "instance"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    secret_key: Mapped[str] = mapped_column(String(128))
    internal_token: Mapped[str] = mapped_column(String(128))
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16), default="member")  # admin | member
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    prefix: Mapped[str] = mapped_column(String(16))
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    goal: Mapped[str] = mapped_column(Text, default="")
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class Setting(Base):
    """Global, admin-edited settings (credentials, helper LLM, defaults). One deployment, one set."""

    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_dataset_name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    origin: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    labels: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))  # datapoints per bucket, maintained on every write
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Datapoint(Base):
    """One chat datapoint. Hash-partitioned by dataset (16 partitions, see migration c5d8e2f71a04)."""

    __tablename__ = "datapoints"
    __table_args__ = (
        Index("ix_datapoints_dataset_position", "dataset_id", "position", unique=True),
        Index("ix_datapoints_dataset_bucket", "dataset_id", "bucket", "position"),
        {"postgresql_partition_by": "HASH (dataset_id)"},
    )
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), primary_key=True)
    id: Mapped[int] = mapped_column(BigInteger, Sequence("datapoints_id_seq"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
    messages: Mapped[list[Any]] = mapped_column(JSONB)
    bucket: Mapped[str] = mapped_column(String(16), default="uncategorized")
    labels: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    rejected: Mapped[str | None] = mapped_column(Text, nullable=True)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CleaningRun(Base):
    __tablename__ = "cleaning_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    before: Mapped[int] = mapped_column(Integer)
    after: Mapped[int] = mapped_column(Integer)
    removed: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    redacted_rows: Mapped[int] = mapped_column(Integer, default=0)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Snapshot(Base):
    __tablename__ = "snapshots"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_snapshot_name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    dataset_id: Mapped[str | None] = mapped_column(ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True)
    dataset_name: Mapped[str] = mapped_column(String(80))
    storage_prefix: Mapped[str | None] = mapped_column(Text, nullable=True)
    training_count: Mapped[int] = mapped_column(Integer)
    validation_count: Mapped[int] = mapped_column(Integer)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SnapshotRow(Base):
    __tablename__ = "snapshot_rows"
    __table_args__ = (Index("ix_snapshot_rows_snapshot_split", "snapshot_id", "split", "position"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("snapshots.id", ondelete="CASCADE"))
    split: Mapped[str] = mapped_column(String(8))  # train | val
    position: Mapped[int] = mapped_column(Integer)
    row: Mapped[dict[str, Any]] = mapped_column(JSONB)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_job_name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("snapshots.id", ondelete="SET NULL"), nullable=True)
    snapshot_name: Mapped[str] = mapped_column(String(80))
    goal: Mapped[str] = mapped_column(Text, default="")
    reasoning: Mapped[bool] = mapped_column(Boolean, default=False)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    experiments: Mapped[list[Experiment]] = relationship(back_populates="job", cascade="all, delete-orphan", order_by="Experiment.number")


class Experiment(Base):
    __tablename__ = "experiments"
    __table_args__ = (UniqueConstraint("job_id", "number", name="uq_experiment_number"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    alias: Mapped[str] = mapped_column(String(120))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(16), default="pending")  # pending | running | succeeded | failed
    status: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    artifact_dir: Mapped[str] = mapped_column(Text)  # relative to the data directory
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    job: Mapped[Job] = relationship(back_populates="experiments")


class ExperimentLog(Base):
    """Loss points and checkpoint records, one row each."""

    __tablename__ = "experiment_logs"
    __table_args__ = (Index("ix_experiment_logs_experiment", "experiment_id", "kind", "step"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))  # loss | checkpoint
    step: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Analysis(Base):
    __tablename__ = "analyses"
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Metric(Base):
    __tablename__ = "metrics"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_metric_name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(16))  # rubric | code | webhook
    description: Mapped[str] = mapped_column(Text, default="")
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # rules | check | webhook
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Evaluation(Base):
    __tablename__ = "evaluations"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_evaluation_name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    snapshot_name: Mapped[str] = mapped_column(String(80))
    models: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    metrics: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    datapoints: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(16), default="running")
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    leaderboard: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class EvalResult(Base):
    __tablename__ = "eval_results"
    __table_args__ = (Index("ix_eval_results_eval", "evaluation_id", "model", "metric"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    evaluation_id: Mapped[str] = mapped_column(ForeignKey("evaluations.id", ondelete="CASCADE"))
    datapoint: Mapped[int] = mapped_column(Integer)
    model: Mapped[str] = mapped_column(String(200))
    metric: Mapped[str] = mapped_column(String(80))
    score: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text, default="")
    prediction: Mapped[str] = mapped_column(Text, default="")
    reference: Mapped[str] = mapped_column(Text, default="")
    user: Mapped[str] = mapped_column(Text, default="")
    seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Trace(Base):
    __tablename__ = "traces"
    __table_args__ = (Index("ix_traces_project_time", "project_id", "time"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    model: Mapped[str] = mapped_column(String(200))
    messages: Mapped[list[Any]] = mapped_column(JSONB)
    response: Mapped[str] = mapped_column(Text, default="")
    seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    document_chunks: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)


class Repository(Base):
    """Document collection; chunks and vectors live in the vector store under ``collection``."""

    __tablename__ = "repositories"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_repository_name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, default="")
    embedding_model: Mapped[str] = mapped_column(String(200))
    dimension: Mapped[int | None] = mapped_column(Integer, nullable=True)
    collection: Mapped[str] = mapped_column(String(80))
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    documents: Mapped[list[Document]] = relationship(back_populates="repository", cascade="all, delete-orphan", order_by="Document.added")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("repository_id", "name", name="uq_document_name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    repository_id: Mapped[str] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(400))
    kind: Mapped[str] = mapped_column(String(16))
    chars: Mapped[int] = mapped_column(Integer, default=0)
    chunks: Mapped[int] = mapped_column(Integer, default=0)
    added: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    repository: Mapped[Repository] = relationship(back_populates="documents")


class InterpCapture(Base):
    """A capture or a before/after comparison; arrays and explanations live in artifact_dir."""

    __tablename__ = "interp_captures"
    __table_args__ = (UniqueConstraint("project_id", "kind", "name", name="uq_interp_name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # capture | diff
    name: Mapped[str] = mapped_column(String(160))
    model_id: Mapped[str] = mapped_column(String(300), default="")
    source: Mapped[str] = mapped_column(String(160), default="")
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    artifact_dir: Mapped[str] = mapped_column(Text)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AutopilotRun(Base):
    __tablename__ = "autopilot_runs"
    __table_args__ = (Index("ix_autopilot_project_name", "project_id", "name", "cycle"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(80))
    cycle: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Task(Base):
    """A unit of background work: queued through Redis, executed by a worker, visible to every process."""

    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_project_created", "project_id", "created"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String(40))
    args: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    state: Mapped[str] = mapped_column(String(16), default="queued")  # queued | running | succeeded | failed | stopped
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    rq_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    worker: Mapped[str | None] = mapped_column(String(120), nullable=True)
    log_tail: Mapped[list[Any]] = mapped_column(JSONB, default=list)


class AssistantConversation(Base):
    """A private snapshot; empty history retains a revision to prevent resurrection."""

    __tablename__ = "assistant_conversations"
    __table_args__ = (
        CheckConstraint("revision > 0", name="ck_assistant_conversation_revision"),
        Index("ix_assistant_conversations_user", "user_id"),
    )
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger)
    history: Mapped[list[Any]] = mapped_column(JSONB)
    provider: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(300))
    updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AssistantAttachment(Base):
    __tablename__ = 'assistant_attachments'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    name: Mapped[str] = mapped_column(String(255))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(500))
    extracted_text: Mapped[str] = mapped_column(Text)
    extracted_chars: Mapped[int] = mapped_column(Integer)


# Register additive laboratory tables in the same metadata used by migrations
# and existing maintenance/test consumers. The laboratory reuses Base above.
from tinystudio.lab import models as _lab_models  # noqa: E402,F401
