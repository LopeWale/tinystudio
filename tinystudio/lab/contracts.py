"""Versioned public laboratory contracts, shared by HTTP, CLI and runners."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def checksum(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1


class InputRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: str = Field(min_length=1, max_length=64)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    role: str = "source"
    visibility: Literal["model", "evaluation", "truth"] = "model"

    @model_validator(mode="after")
    def truth_boundary(self):
        if self.role in {"ground_truth", "truth", "final_test"} and self.visibility == "model":
            raise ValueError("truth and final-test artifacts require evaluation or truth visibility")
        return self


class ExecutionProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: Literal["trusted_native", "isolated"]
    backend: Literal["native", "container", "kubernetes", "rq"] = "native"
    max_seconds: int = Field(default=300, ge=1, le=86400)
    max_output_bytes: int = Field(default=536870912, ge=1, le=10737418240)
    requirements: dict[str, Any] = Field(default_factory=dict)
    grants: dict[str, Any] = Field(default_factory=dict)


class LegacyTrainingReference(BaseModel):
    """A source link to existing identities, never an instruction to mutate them."""
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(min_length=1, max_length=32)
    experiment_id: str | None = Field(default=None, min_length=1, max_length=32)
    experiment_number: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def one_experiment_selector(self):
        if self.experiment_id is not None and self.experiment_number is not None:
            raise ValueError("select an existing Experiment by id or number, not both")
        return self


class ExperimentDefinition(Contract):
    name: str = Field(min_length=1, max_length=200)
    domain: str = "signals"
    engine: str = Field(min_length=1, max_length=120)
    seed: int = Field(default=42, ge=0, le=2**32-1)
    mode: Literal["offline", "online"] = "offline"
    operating_mode: Literal["simulation", "replay", "acquisition", "shadow", "physical_control"] = "simulation"
    parameters: dict[str, Any] = Field(default_factory=dict)
    pipeline: list[dict[str, Any]] = Field(default_factory=list)
    inputs: list[InputRef] = Field(default_factory=list, max_length=128)
    components: list[dict[str, Any]] = Field(default_factory=list, max_length=10000)
    connections: list[dict[str, Any]] = Field(default_factory=list, max_length=20000)
    scenarios: list[dict[str, Any]] = Field(default_factory=list)
    clocks: dict[str, Any] = Field(default_factory=dict)
    objectives: dict[str, Any] = Field(default_factory=dict)
    learning: dict[str, Any] | None = None
    retrieval: dict[str, Any] | None = None
    rewards: dict[str, Any] = Field(default_factory=dict)
    evaluators: list[dict[str, Any]] = Field(default_factory=list)
    execution: ExecutionProfile
    research_overrides: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def portable(self):
        encoded = canonical(self.model_dump())
        if len(encoded) > 2 * 1024**2:
            raise ValueError("definition exceeds 2 MiB; store data in immutable input artifacts")
        # Authored files are references, never paths resolved with the server's authority.
        forbidden = {"input_paths", "evaluation_input_paths", "resume_from", "teacher_checkpoint", "password", "api_key", "access_token"}
        def walk(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in forbidden:
                        raise ValueError(f"{key} must be supplied by a scoped artifact or machine binding")
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
        walk(self.model_dump())
        ids = [item.get("id") for item in self.components]
        if any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)):
            raise ValueError("components require unique nonempty ids")
        for edge in self.connections:
            if edge.get("source") not in ids or edge.get("target") not in ids:
                raise ValueError("connection endpoints must refer to components")
        if len({item.artifact_id for item in self.inputs}) != len(self.inputs):
            raise ValueError("duplicate input artifact")
        reference = self.parameters.get("compatibility", {})
        if not isinstance(reference, dict):
            raise ValueError("compatibility references must be an object")
        if reference:
            LegacyTrainingReference.model_validate(reference)
        return self


class ResolvedRunSpec(Contract):
    definition: ExperimentDefinition
    definition_id: str
    revision: int = Field(ge=1)
    implementation: dict[str, Any]
    input_manifest: list[dict[str, Any]]
    runtime: dict[str, str]
    research_identity: str
    compatibility: dict[str, Any] = Field(default_factory=dict)
    schedule: dict[str, str] = Field(default_factory=dict)
    integrity: dict[str, Any] | None = None


class Episode(Contract):
    id: str
    run_id: str
    clock_domain: str
    observations: list[dict[str, Any]] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    messages: list[dict[str, Any]] = Field(default_factory=list)
    outcomes: dict[str, Any] = Field(default_factory=dict)
    evidence: list[InputRef] = Field(default_factory=list)
    truth_evidence: list[InputRef] = Field(default_factory=list)


class DatasetManifest(Contract):
    artifacts: list[InputRef]
    transformations: list[dict[str, Any]] = Field(default_factory=list)
    partitions: dict[str, list[str]] = Field(default_factory=dict)
    group_keys: list[str] = Field(default_factory=lambda: ["episode_id"])

    @model_validator(mode="after")
    def disjoint(self):
        seen = set()
        for name, records in self.partitions.items():
            if name not in {"train", "validation", "test"}:
                raise ValueError("partitions must be train, validation, test")
            if seen.intersection(records):
                raise ValueError("partition leakage: a source group occurs in multiple partitions")
            seen.update(records)
        return self


class ArtifactManifest(Contract):
    artifacts: list[InputRef]
    parent_artifacts: list[InputRef] = Field(default_factory=list)
    transformations: list[dict[str, Any]] = Field(default_factory=list)
    complete: bool


class RetrievalManifest(Contract):
    corpus: list[InputRef]
    index: InputRef
    method: Literal["lexical", "semantic", "hybrid", "structured", "episode"]
    parameters: dict[str, Any]
    embedding_model: InputRef | None = None


class ExecutionTargetManifest(Contract):
    id: str
    profiles: list[str]
    backends: list[str]
    engines: list[str]
    runtime: dict[str, str]
    implementation_digest: str


class DeploymentManifest(Contract):
    model: InputRef
    target: dict[str, Any]
    package: InputRef
    measurements: dict[str, Any]
    estimates: dict[str, Any] = Field(default_factory=dict)
    qualification: dict[str, Any]


def numeric_equal(actual: float, expected: float, *, atol: float = 0, rtol: float = 0) -> bool:
    """Typed checks preserve sign/decimal structure; no text normalization."""
    if isinstance(actual, bool) or isinstance(expected, bool) or atol < 0 or rtol < 0:
        return False
    return math.isfinite(actual) and math.isfinite(expected) and math.isclose(actual, expected, abs_tol=atol, rel_tol=rtol)
