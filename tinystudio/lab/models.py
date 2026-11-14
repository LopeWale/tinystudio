"""Additive laboratory tables; existing Job and Experiment identities remain intact."""
from datetime import datetime
import json
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, ForeignKeyConstraint, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from tinystudio.db.models import Base, new_id
from .contracts import canonical

DOC = JSON().with_variant(JSONB, "postgresql")


class StudyDocument(TypeDecorator):
    """Preserve canonical JSON numbers/signs; JSONB normalizes their spelling."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return canonical(value).decode('utf-8') if value is not None else None

    def process_result_value(self, value, dialect):
        return json.loads(value) if value is not None else None


class LabDefinition(Base):
    __tablename__ = "lab_definitions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LabRevision(Base):
    __tablename__ = "lab_revisions"
    definition_id: Mapped[str] = mapped_column(ForeignKey("lab_definitions.id", ondelete="CASCADE"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    definition: Mapped[dict[str, Any]] = mapped_column(DOC)
    digest: Mapped[str] = mapped_column(String(64))


class LabTarget(Base):
    __tablename__ = "lab_targets"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_lab_target_name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    capabilities: Mapped[dict[str, Any]] = mapped_column(DOC, default=dict)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class LabRun(Base):
    __tablename__ = "lab_runs"
    __table_args__ = (UniqueConstraint("project_id", "idempotency_key", name="uq_lab_run_request"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    definition_id: Mapped[str] = mapped_column(ForeignKey("lab_definitions.id"))
    revision: Mapped[int] = mapped_column(Integer)
    target_id: Mapped[str] = mapped_column(ForeignKey("lab_targets.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(120))
    spec: Mapped[dict[str, Any]] = mapped_column(DOC)
    spec_digest: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    next_seq: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(DOC, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_run_id: Mapped[str | None] = mapped_column(ForeignKey("lab_runs.id"), nullable=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    experiment_id: Mapped[str | None] = mapped_column(ForeignKey("experiments.id", ondelete="SET NULL"), nullable=True)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LabDispatch(Base):
    __tablename__ = "lab_dispatch"
    run_id: Mapped[str] = mapped_column(ForeignKey("lab_runs.id", ondelete="CASCADE"), primary_key=True)
    available: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class LabAttempt(Base):
    __tablename__ = "lab_attempts"
    __table_args__ = (UniqueConstraint("run_id", "number", name="uq_lab_attempt_number"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("lab_runs.id", ondelete="CASCADE"), index=True)
    target_id: Mapped[str] = mapped_column(ForeignKey("lab_targets.id"))
    number: Mapped[int] = mapped_column(Integer)
    token_hash: Mapped[str] = mapped_column(String(64))
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(32), default="running")
    runtime: Mapped[dict[str, Any]] = mapped_column(DOC)
    result_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LabEvent(Base):
    __tablename__ = "lab_events"
    run_id: Mapped[str] = mapped_column(ForeignKey("lab_runs.id", ondelete="CASCADE"), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict[str, Any]] = mapped_column(DOC)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LabArtifact(Base):
    __tablename__ = "lab_artifacts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    attempt_id: Mapped[str | None] = mapped_column(ForeignKey("lab_attempts.id", ondelete="CASCADE"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size: Mapped[int] = mapped_column(BigInteger)
    media_type: Mapped[str] = mapped_column(String(120))
    storage_key: Mapped[str] = mapped_column(String(500))
    role: Mapped[str] = mapped_column(String(64), default="source")
    visibility: Mapped[str] = mapped_column(String(16), default="model")


TABLES = [LabDefinition.__table__, LabRevision.__table__, LabTarget.__table__, LabRun.__table__,
          LabDispatch.__table__, LabAttempt.__table__, LabEvent.__table__, LabArtifact.__table__]


from sqlalchemy import CheckConstraint


class LabOperation(Base):
    """A durable project-scoped evidence operation and its current fenced attempt."""
    __tablename__ = "lab_operations"
    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key", name="uq_lab_operation_request"),
        CheckConstraint("kind IN ('export', 'import', 'data_bridge', 'artifact_verify')", name="ck_lab_operation_kind"),
        CheckConstraint("state IN ('queued', 'running', 'succeeded', 'failed', 'lost', 'cancelled')", name="ck_lab_operation_state"),
        CheckConstraint("generation >= 0", name="ck_lab_operation_generation"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    idempotency_key: Mapped[str] = mapped_column(String(120))
    payload: Mapped[dict[str, Any]] = mapped_column(DOC)
    payload_digest: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatch_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(DOC, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    error_detail: Mapped[dict[str, Any] | None] = mapped_column(DOC, nullable=True)


TABLES.append(LabOperation.__table__)


class LabTransfer(Base):
    """Immutable transfer intent; provider uncertainty is never an artifact ACK."""
    __tablename__ = 'lab_transfers'
    __table_args__ = (
        UniqueConstraint('project_id', 'idempotency_key', name='uq_lab_transfer_request'),
        CheckConstraint("state IN ('admitted','initializing','initialization_uncertain','uploading','sealing',"
                        "'completion_uncertain','sealed','verifying','verified','aborting','abort_uncertain','aborted')",
                        name='ck_lab_transfer_state'),
        CheckConstraint('size >= 0', name='ck_lab_transfer_size'),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(120))
    request_digest: Mapped[str] = mapped_column(String(64))
    intent: Mapped[dict[str, Any]] = mapped_column(DOC)
    attempt_identity: Mapped[dict[str, Any] | None] = mapped_column(DOC, nullable=True)
    policy: Mapped[dict[str, Any]] = mapped_column(StudyDocument())
    geometry: Mapped[dict[str, Any]] = mapped_column(DOC)
    storage_key: Mapped[str] = mapped_column(String(500))
    size: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(32), default='admitted', index=True)
    upload_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    parts: Mapped[list[dict[str, Any]]] = mapped_column(DOC, default=list)
    operation_id: Mapped[str | None] = mapped_column(ForeignKey('lab_operations.id'), nullable=True)
    artifact_id: Mapped[str | None] = mapped_column(ForeignKey('lab_artifacts.id'), nullable=True)
    artifact_receipt: Mapped[dict[str, Any] | None] = mapped_column(DOC, nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


TABLES.append(LabTransfer.__table__)


class LabStudy(Base):
    __tablename__ = 'lab_studies'
    __table_args__ = (UniqueConstraint('project_id', 'idempotency_key', name='uq_lab_study_request'),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    name: Mapped[str] = mapped_column(String(200))
    revision: Mapped[int] = mapped_column(BigInteger, default=1)
    next_seq: Mapped[int] = mapped_column(BigInteger, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(120))
    request_digest: Mapped[str] = mapped_column(String(64))
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LabStudyRevision(Base):
    __tablename__ = 'lab_study_revisions'
    study_id: Mapped[str] = mapped_column(ForeignKey('lab_studies.id', ondelete='CASCADE'), primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(StudyDocument())
    digest: Mapped[str] = mapped_column(String(64))


class LabStudyRecord(Base):
    __tablename__ = 'lab_study_records'
    __table_args__ = (
        ForeignKeyConstraint(['study_id', 'revision'], ['lab_study_revisions.study_id', 'lab_study_revisions.revision'], ondelete='CASCADE'),
        UniqueConstraint('study_id', 'seq', name='uq_lab_study_record_sequence'),
        UniqueConstraint('study_id', 'idempotency_key', name='uq_lab_study_record_request'),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    study_id: Mapped[str] = mapped_column(String(32), index=True)
    revision: Mapped[int] = mapped_column(BigInteger)
    seq: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(120))
    unit_id: Mapped[str] = mapped_column(String(500))
    idempotency_key: Mapped[str] = mapped_column(String(120))
    request_digest: Mapped[str] = mapped_column(String(64))
    record: Mapped[dict[str, Any]] = mapped_column(StudyDocument())
    digest: Mapped[str] = mapped_column(String(64))


TABLES.extend([LabStudy.__table__, LabStudyRevision.__table__, LabStudyRecord.__table__])
