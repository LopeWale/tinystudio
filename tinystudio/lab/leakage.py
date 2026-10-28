"""Version 1 declarations for reward fitting and scenario selection evidence.

``rewards.evidence`` and each ``scenarios[i].selection_evidence`` are lists of
objects with an ``artifact_id`` from definition.inputs. Optional ``sha256`` must
match that binding. Optional partition/split/bucket, visibility/source_visibility,
and role/source_role describe the evidence used for this selection. Restrictions
from the declaration, binding and immutable artifact combine monotonically.

This is a provenance contract, not static analysis of arbitrary reward code,
scientific prose, artifact contents, or future executor behavior. An absent or
empty declaration never qualifies an experiment as free of leakage.
"""
from __future__ import annotations

from copy import deepcopy
import re

from .partitioning import PARTITION_FIELDS, partition_label, source_protection

_REFERENCE_FIELDS = frozenset({"artifact_id", "sha256", "visibility", "source_visibility", "role", "source_role", *PARTITION_FIELDS})
_PROTECTION_ORDER = {"model": 0, "evaluation": 1, "truth": 2}


def _dependency(reference: dict, bindings: dict, artifacts: dict, overrides: list, kind: str) -> dict:
    if not isinstance(reference, dict) or reference.keys() - _REFERENCE_FIELDS:
        raise ValueError("selection evidence must be an artifact reference with supported provenance fields")
    identity = reference.get("artifact_id")
    if not isinstance(identity, str) or not identity or identity not in bindings or identity not in artifacts:
        raise ValueError("selection evidence requires an artifact_id resolved from project-scoped immutable inputs")
    binding, artifact = bindings[identity], artifacts[identity]
    digest = binding.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or artifact.get("sha256") != digest:
        raise ValueError("selection evidence input digest does not match immutable artifact")
    if "sha256" in reference and reference["sha256"] != digest:
        raise ValueError("selection evidence digest does not match bound input")
    # Independent evaluation preserves strong restrictions even if a row says train.
    protections = [source_protection(value) for value in (reference, binding, artifact)]
    protection = max(protections, key=_PROTECTION_ORDER.__getitem__)
    partition = partition_label(reference, allow_truth=True, allow_unassigned=True)
    override = next((item for item in overrides if isinstance(item, dict)
                     and item.get("kind") == "leakage.use_heldout_" + kind
                     and item.get("artifact_id") == identity
                     and isinstance(item.get("reason"), str) and item["reason"].strip()), None)
    return {"kind": kind, "artifact_id": identity, "sha256": digest,
            "reference": deepcopy(reference), "binding": deepcopy(binding), "artifact": deepcopy(artifact),
            "partition": partition, "protection": protection,
            "override": deepcopy(override) if protection != "model" else None,
            "passed": protection == "model" or override is not None}


def selection_integrity(definition: dict, input_manifest: list[dict]) -> dict:
    """Audit explicit v1 dependencies against already validated, frozen inputs.

    Invalid/unresolved dependencies cannot be waived by a research override.
    Callers must enforce project scoping and artifact identity before supplying
    input_manifest; this function never opens authored paths or storage objects.
    """
    bindings = {item["artifact_id"]: item for item in definition.get("inputs", [])}
    artifacts = {item["id"]: item for item in input_manifest}
    overrides = definition.get("research_overrides", [])
    checks, dependencies, declarations = [], [], []
    selections = [("reward", "rewards.evidence", definition.get("rewards", {}), "evidence")]
    selections.extend(("scenario_selection", f"scenarios[{index}].selection_evidence", scenario, "selection_evidence")
                      for index, scenario in enumerate(definition.get("scenarios", [])))
    for kind, location, container, key in selections:
        declared = key in container
        declarations.append({"kind": kind, "location": location, "status": "declared_only" if declared else "not_declared"})
        if not declared:
            continue
        references = container[key]
        if not isinstance(references, list):
            checks.append({"code": "leakage." + kind, "location": location, "passed": False,
                           "message": location + " must be a list of artifact references"})
            continue
        for index, reference in enumerate(references):
            check = {"code": "leakage." + kind, "location": f"{location}[{index}]"}
            try:
                dependency = _dependency(reference, bindings, artifacts, overrides, kind)
            except ValueError as exc:
                check.update(passed=False, message=str(exc))
            else:
                dependencies.append({**dependency, "location": check["location"]})
                check.update(passed=dependency["passed"], artifact_id=dependency["artifact_id"],
                             message=("Declared selection provenance accepted; contents and arbitrary code remain unverified."
                                      if dependency["passed"] else
                                      f"{kind} uses held-out/evaluation/truth evidence; requires scoped leakage.use_heldout_{kind} research override"))
            checks.append(check)
    passed = all(check["passed"] for check in checks)
    qualification = ("rejected" if not passed else "research_only" if any(item["override"] for item in dependencies)
                     else "declared_only" if any(item["status"] == "declared_only" for item in declarations) else "unverified")
    return {"schema_version": 1, "scope": "declared_reward_and_scenario_selection", "passed": passed,
            "qualification": qualification, "declarations": declarations, "dependencies": dependencies, "checks": checks,
            "limitations": ["Artifact contents, arbitrary reward code and future data use are unverified; only explicit provenance declarations are audited.",
                            "Absent or empty declarations do not establish absence of leakage or scientific qualification."]}
