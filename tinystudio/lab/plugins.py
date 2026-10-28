"""Versioned plugin declarations and qualification requirements.

Declarations describe contracts. They never turn installed dependencies or a
test-file reference into evidence that an execution target is qualified.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import Contract, ExperimentDefinition, InputRef


SCHEMAS = {
    "tinystudio.lab.definition.v1": ExperimentDefinition.model_json_schema(),
    "tinystudio.lab.input-ref.v1": InputRef.model_json_schema(),
    "tinystudio.lab.published-artifacts.v1": {
        "type": "array", "items": {"type": "object", "required": ["name", "sha256", "role", "media_type"],
            "properties": {"name": {"type": "string"}, "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                           "role": {"type": "string"}, "media_type": {"type": "string"}}}},
    "tinystudio.lab.chat-rows.v1": {
        "type": "array", "minItems": 1, "items": {"type": "object", "required": ["messages"],
            "properties": {"messages": {"type": "array", "minItems": 1, "items": {
                "type": "object", "required": ["role", "content"], "properties": {
                    "role": {"enum": ["system", "user", "assistant", "tool"]}, "content": {"type": "string"}}}}}}},
}


class PortContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    media_types: list[str] = Field(min_length=1)
    units: Literal["per_record", "dimensionless", "not_applicable", "artifact_defined"]
    binding: Literal["immutable_artifact", "definition", "manifest"]
    schema_ref: str
    role: str | None = None
    min_items: int = Field(default=0, ge=0, le=128)
    max_items: int = Field(default=1, ge=1, le=128)
    content_schema_ref: str | None = None
    filename_suffix: str | None = None
    required_when: str | None = None
    min_records: int | None = Field(default=None, ge=1)
    visibility: list[Literal["model", "evaluation", "truth"]] = Field(min_length=1)

    @model_validator(mode="after")
    def binding_contract(self):
        if self.min_items > self.max_items:
            raise ValueError("port minimum cardinality exceeds maximum")
        if self.schema_ref not in SCHEMAS or self.content_schema_ref is not None and self.content_schema_ref not in SCHEMAS:
            raise ValueError("port references an unknown versioned schema")
        if self.binding == "immutable_artifact" and not self.role:
            raise ValueError("artifact ports require an explicit binding role")
        return self


class DependencyContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    resolution: Literal["frozen_target_runtime", "operator_provisioned_executable"]


class TargetContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operating_systems: list[Literal["Darwin", "Linux"]] = Field(min_length=1)
    hardware: list[str] = Field(min_length=1)
    additional_requirements: list[str] = Field(default_factory=list)
    qualification_required: Literal[True] = True


class LifecycleContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preparation: Literal["verify_frozen_inputs_and_runtime"] = "verify_frozen_inputs_and_runtime"
    execution: Literal["attempt_process"] = "attempt_process"
    cancellation: Literal["terminate_owned_attempt"] = "terminate_owned_attempt"
    finalization: Literal["stop_processes_verify_outputs_publish_manifest"] = "stop_processes_verify_outputs_publish_manifest"
    pause: Literal["unsupported"] = "unsupported"
    continuation: Literal["new_attempt", "checkpoint_specific"] = "new_attempt"


class FixtureRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(pattern=r"^tests/test_lab_[a-z_]+\.py$")
    evidence_required: list[str] = Field(min_length=1)


class BindingGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    roles: list[str] = Field(min_length=1)
    min_items: int = Field(ge=0)
    max_items: int = Field(ge=1)

    @model_validator(mode="after")
    def bounded(self):
        if self.min_items > self.max_items or len(set(self.roles)) != len(self.roles):
            raise ValueError("binding groups require unique roles and coherent cardinality")
        return self


class PluginManifest(Contract):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
    kinds: list[Literal["source", "simulation", "clock", "transformation", "representation",
                        "trainer", "retriever", "evaluator", "deployment"]] = Field(min_length=1)
    implemented: bool
    implementation_version: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    inputs: list[PortContract]
    outputs: list[PortContract]
    binding_groups: list[BindingGroup] = Field(default_factory=list)
    dependencies: list[DependencyContract]
    targets: TargetContract
    lifecycle: LifecycleContract | None
    fixtures: list[FixtureRequirement]
    qualification: Literal["unqualified"] = "unqualified"
    selections_require_separate_qualification: Literal[True] = True

    @model_validator(mode="after")
    def coherent_declaration(self):
        if self.implemented and (self.implementation_version is None or self.lifecycle is None or not self.fixtures):
            raise ValueError("implemented plugins require a source version, lifecycle and qualification fixture")
        if not self.implemented and (self.lifecycle is not None or self.implementation_version is not None):
            raise ValueError("unimplemented plugins cannot advertise an executable lifecycle or source version")
        for ports in (self.inputs, self.outputs):
            if len({port.name for port in ports}) != len(ports):
                raise ValueError("plugin port names must be unique within each direction")
        if len(set(self.kinds)) != len(self.kinds):
            raise ValueError("plugin kinds must be unique")
        input_roles = {port.role for port in self.inputs if port.role}
        if any(set(group.roles) - input_roles for group in self.binding_groups):
            raise ValueError("binding groups must refer to declared artifact roles")
        return self


# Each entry identifies a compatible-target fixture requirement, not a test result.
METHODS = {
    "learning.language": (["trainer", "representation"], "learning.py", "learning"),
    "learning.pretrained": (["trainer"], "compatibility.py", "compatibility"),
    "learning.language_rl": (["trainer"], "language_rl.py", "language_rl"),
    "learning.timeseries": (["trainer"], "timeseries.py", "timeseries"),
    "learning.multimodal": (["trainer"], "timeseries.py", "timeseries"),
    "learning.control_rl": (["trainer"], "rl.py", "rl"),
    "learning.tool_rl": (["trainer"], "rl.py", "rl"),
    "retrieval.index": (["retriever"], "retrieval.py", "learning"),
    "system.cosim": (["simulation", "clock"], "cosim.py", "cosim"),
    "data.pipeline": (["transformation", "representation"], "data.py", "data"),
    "evaluation.compare": (["evaluator"], "research.py", "evaluation"),
    "deployment.benchmark": (["deployment"], "deployment.py", "deployment"),
}


def manifest_for(row: dict) -> dict:
    id_ = row["id"]
    implemented = bool(row["implemented"])
    if id_ in METHODS:
        kinds, source, fixture = METHODS[id_]
        version = "sha256:" + hashlib.sha256(Path(__file__).with_name(source).read_bytes()).hexdigest() if implemented else None
    else:
        kinds = ["source"] if id_ in {"synthetic.signal", "recorded.replay"} or id_.startswith("acquisition.") else ["simulation"]
        fixture = ("engine" if id_ in {"synthetic.signal", "recorded.replay", "simulation.plant"} else
                   "acquisition" if id_.startswith("acquisition.") else
                   "modelica" if id_ == "simulation.openmodelica" else
                   "adapters" if id_ in {"simulation.simpy", "optimization.ortools", "testing.hypothesis",
                                        "protocol.modbus", "protocol.mqtt", "simulation.boptest", "simulation.ngspice"} else "domain_adapters")
        version = row.get("version")
    dependencies = row.get("dependencies", []) or ([row["dependency"]] if row.get("dependency") else [])
    executable = id_ in {"embedded.wokwi_tinygo", "embedded.tinygo", "embedded.renode", "embedded.qemu",
                         "simulation.verilator", "simulation.openmodelica", "simulation.energyplus", "simulation.ngspice",
                         "network.mininet", "network.ns3", "robotics.gazebo", "simulation.matlab"}
    target_notes = ["Execution host must advertise the selected backend, profile and exact runtime."]
    if id_ == "learning.pretrained":
        target_notes.append("Each objective/update strategy requires separate qualification; QLoRA requires compatible CUDA or the pinned Linux ARM64 Python 3.12 CPU NF4 pack.")
    if row.get("requires_operator_binding") or executable:
        target_notes.append("Exact operator grants/bindings and provisioned dependencies are required; definitions cannot grant authority.")
    ports = input_contracts(id_)
    fixtures = [FixtureRequirement(path=f"tests/test_lab_{fixture}.py", evidence_required=[
        "Exact plugin source hash, input hashes and dependency/target versions.",
        "Actual execution of the selected operation on the compatible target; skipped/mocked execution remains unqualified.",
        "Independent outcome checks, cancellation and retained output manifest."])] if implemented else []
    if implemented and id_ == "learning.language":
        fixtures.append(FixtureRequirement(path="tests/test_lab_scratch_qualification.py", evidence_required=[
            "Actual registered-route/native-child execution for Transformer and GRU with byte, BPE and engineering representations.",
            "Independent parameter-count/update checks, held-out exclusion, transported checkpoint reload and inference artifacts.",
            "Each selected target requires its actual receipt; skipped/mocked execution remains unqualified."]))
        fixtures.append(FixtureRequirement(path="tests/test_lab_distillation_workflow.py", evidence_required=[
            "Actual retained-teacher to smaller cross-family student execution with byte, BPE and engineering tokenizers.",
            "Teacher immutability, student parameter updates, held-out exposure findings and independent transported-student inference.",
            "Each selected target requires its actual receipt; skipped/mocked execution remains unqualified."]))
    if implemented and id_ == "learning.pretrained":
        fixtures.append(FixtureRequirement(path="tests/test_lab_qlora.py", evidence_required=[
            "Real SFT, raw causal LM and DPO adapter optimization on the pinned Linux ARM64 CPU NF4 stack.",
            "Packed/nested quantization state, adapter precision, frozen reference and optimizer membership checks.",
            "Live-policy versus extracted-bundle reload equality and wrong-float-base/missing-state counterexamples.",
            "Actual compatible-target execution is required; skipped/mocked execution remains unqualified."]))
        fixtures.append(FixtureRequirement(path="tests/test_lab_qlora_workflow.py", evidence_required=[
            "Authenticated registered-app upload, preflight, submission and actual isolated Docker execution for each objective.",
            "Image-bound runtime, checksum-verified artifact transport and independent retained-policy reload probes.",
            "Actual compatible-target execution is required; skipped/mocked execution remains unqualified."]))
    manifest = PluginManifest(id=id_, kinds=kinds, implemented=implemented,
        implementation_version=version if implemented else None, inputs=ports,
        outputs=[PortContract(name="artifacts", media_types=["application/json"], units="artifact_defined",
                              binding="manifest", schema_ref="tinystudio.lab.published-artifacts.v1", min_items=1,
                              visibility=["model", "evaluation", "truth"])],
        binding_groups=[{"roles": ["model", "checkpoint"], "min_items": 1, "max_items": 1}] if id_ == "deployment.benchmark" else [],
        dependencies=[DependencyContract(name=name, resolution="operator_provisioned_executable" if executable else "frozen_target_runtime")
                      for name in dependencies],
        targets=TargetContract(operating_systems=["Linux"] if id_ in {"network.mininet", "network.ns3"} else ["Darwin", "Linux"],
                               hardware=["host_cpu"], additional_requirements=target_notes),
        lifecycle=LifecycleContract(continuation="checkpoint_specific" if id_.startswith("learning.") else "new_attempt") if implemented else None,
        fixtures=fixtures)
    return manifest.model_dump()


def input_contracts(id_: str) -> list[PortContract]:
    """Reuse domain role declarations; specify exact shared-service bindings."""
    from .domain_adapters import INPUT_ROLES
    ports = [PortContract(name="definition", media_types=["application/json"], units="not_applicable",
                          binding="definition", schema_ref="tinystudio.lab.definition.v1", min_items=1, visibility=["model"])]
    def add(role, *, required=False, maximum=1, suffix=None, schema=None, when=None, visibility=None, media=None, min_records=None):
        ports.append(PortContract(name=role, role=role, min_items=int(required), max_items=maximum,
            filename_suffix=suffix, content_schema_ref=schema, required_when=when, min_records=min_records,
            schema_ref="tinystudio.lab.input-ref.v1", media_types=media or ["application/octet-stream"],
            units="artifact_defined", binding="immutable_artifact", visibility=visibility or ["model"]))
    required = {
        "embedded.tinygo": {"source"}, "embedded.wokwi_tinygo": {"firmware", "diagram"},
        "embedded.renode": {"firmware", "platform"}, "embedded.qemu": {"firmware"},
        "simulation.fmi": {"fmu"}, "simulation.energyplus": {"model", "weather"}, "simulation.matlab": {"model"},
    }
    for role, suffix in INPUT_ROLES.get(id_, {}).items():
        add(role, required=role in required.get(id_, set()), suffix=suffix,
            when="uploaded model build/run" if id_ == "simulation.verilator" else None)
    if id_ == "simulation.openmodelica":
        add("model", required=True, suffix=".mo", media=["text/plain"])
    elif id_ == "recorded.replay":
        add("source", required=True, maximum=128, media=["application/x-ndjson"])
    elif id_ == "learning.pretrained":
        add("pretrained_model", required=True, media=["application/x-tar", "application/zip"])
        add("snapshot", required=True, schema="tinystudio.lab.chat-rows.v1", min_records=3,
            media=["application/json", "application/x-ndjson", "application/vnd.apache.parquet"])
        add("validation", visibility=["evaluation"], schema="tinystudio.lab.chat-rows.v1", min_records=1)
    elif id_ == "deployment.benchmark":
        add("model"); add("checkpoint")
    elif id_ == "evaluation.compare":
        for role in ("examples", "candidate", "baseline", "provenance"):
            add(role, visibility=["evaluation", "truth"], media=["application/json", "application/x-ndjson"])
    elif id_ == "data.pipeline":
        for role in ("source", "dataset", "recorded"):
            add(role, maximum=128, visibility=["model", "evaluation", "truth"],
                media=["application/json", "application/x-ndjson", "text/csv", "application/vnd.apache.parquet"])
    elif id_.startswith("acquisition."):
        add("telemetry", when="retained replay")
        add("shadow_model", when="operating_mode=shadow")
    elif id_.startswith("learning.") or id_ == "retrieval.index":
        add("source", maximum=128, media=["text/plain", "application/json", "application/x-ndjson", "application/vnd.apache.parquet"])
        if id_ != "retrieval.index":
            add("resume", when="initialization=resume")
        if id_ == "learning.language":
            add("initial_weights", when="initialization=weights")
            add("teacher", when="objective=distillation")
            add("validation", visibility=["evaluation"])
        if id_ == "retrieval.index":
            add("embedding_model", when="retrieval mode=semantic|hybrid")
        if id_ == "learning.control_rl":
            add("demonstrations", when="objective=dqn|behavioral_cloning")
    return ports


def qualification_matrix(rows: list[dict]) -> list[dict]:
    """Explicit pending requirements; receipts are produced by later execution."""
    return [{"plugin_id": row["id"], "implemented": row["implemented"],
             "implementation_version": row["manifest"]["implementation_version"],
             "target_requirements": row["manifest"]["targets"],
             "fixtures": row["manifest"]["fixtures"], "qualification": "unqualified"}
            for row in rows]
