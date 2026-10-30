"""Authored policy intent must survive admission and execution without inference."""
from copy import deepcopy

import pytest

from tinystudio.lab.contracts import ExperimentDefinition
from tinystudio.lab.policy_selection import POLICY_ENGINES, validate_policy_selection
from tinystudio.lab.preflight import execution_checks


def definition(engine):
    return {"name": "selection", "engine": engine, "execution": {"profile": "trusted_native"},
            "learning": {}, "parameters": {}, "inputs": []}


def binding(role="resume", **changes):
    return {"artifact_id": "checkpoint", "sha256": "a" * 64, "role": role, **changes}


def invalid_selection(engine, case):
    authored = definition(engine)
    if case == "implicit_resume":
        authored["inputs"] = [binding()]
    elif case == "scratch_resume":
        authored["learning"]["initialization"] = "scratch"
        authored["inputs"] = [binding()]
    elif case == "missing_resume":
        authored["learning"]["initialization"] = "resume"
    elif case == "duplicate_resume":
        authored["learning"]["initialization"] = "resume"
        authored["inputs"] = [binding(), binding(artifact_id="other")]
    elif case == "evaluator_resume":
        authored["learning"]["initialization"] = "resume"
        authored["inputs"] = [binding(visibility="evaluation")]
    elif case == "retrieval":
        authored["retrieval"] = {"mode": "lexical"}
    elif case == "teacher":
        authored["inputs"] = [binding("teacher")]
    elif case == "embedding_model":
        authored["inputs"] = [binding("embedding_model")]
    elif case == "objective_conflict":
        authored["learning"]["objective"] = "ppo" if "rl" in engine else "regression"
        authored["parameters"]["objective"] = "dqn" if "rl" in engine else "classification"
    elif case == "representation_conflict":
        authored["learning"]["representation"] = "numeric"
        authored["parameters"]["representation"] = "tool_dsl"
    elif case == "family_conflict":
        authored["learning"]["family"] = "mlp"
        authored["parameters"]["family"] = "gru"
    return authored


CASES = ["implicit_resume", "scratch_resume", "missing_resume", "duplicate_resume", "evaluator_resume",
         "retrieval", "teacher", "embedding_model", "objective_conflict", "representation_conflict", "family_conflict"]


@pytest.mark.parametrize("engine", sorted(POLICY_ENGINES))
@pytest.mark.parametrize("case", CASES)
def test_invalid_authored_policy_choices_fail_admission_and_execution(tmp_path, engine, case):
    authored = invalid_selection(engine, case)
    with pytest.raises(ValueError):
        validate_policy_selection(authored)
    checks = execution_checks(ExperimentDefinition(**authored), {})
    assert next(row for row in checks if row["code"] == "policy_selection")["passed"] is False
    from tinystudio.lab.language_rl import execute_language_rl
    from tinystudio.lab.rl import execute_rl
    from tinystudio.lab.timeseries import execute_multimodal, execute_timeseries
    execute = {"learning.language_rl": execute_language_rl, "learning.timeseries": execute_timeseries,
               "learning.multimodal": execute_multimodal}.get(engine, execute_rl)
    with pytest.raises(ValueError):
        execute({"definition": authored}, tmp_path / "invalid", lambda row: None, lambda: False)
    assert not (tmp_path / "invalid").exists()


@pytest.mark.parametrize("engine", sorted(POLICY_ENGINES))
@pytest.mark.parametrize("initialization", ["scratch", "random", "from_scratch", "resume"])
def test_matching_checkpoint_selection_passes_pure_preflight_without_opening_artifacts(engine, initialization):
    authored = definition(engine)
    authored["learning"]["initialization"] = initialization
    if initialization == "resume":
        authored["inputs"] = [binding()]
    before = deepcopy(authored)
    assert validate_policy_selection(authored)["initialization"] == initialization
    checks = execution_checks(ExperimentDefinition(**authored), {})
    assert next(row for row in checks if row["code"] == "policy_selection")["passed"] is True
    assert authored == before


def test_semantically_equal_selector_aliases_do_not_conflict():
    authored = definition("learning.language_rl")
    authored["learning"] = {"family": {"kind": "tiny_transformer"}, "objective": {"kind": "ppo"}}
    authored["parameters"] = {"family": "transformer", "objective": "ppo", "algorithm": "ppo"}
    assert validate_policy_selection(authored)["family"] == "transformer"


@pytest.mark.parametrize("engine", ["learning.language_rl", "learning.control_rl", "learning.tool_rl"])
def test_environment_and_task_policies_reject_unused_source_bindings(engine):
    authored = definition(engine)
    authored["inputs"] = [binding("source")]
    with pytest.raises(ValueError, match="unused corpus input"):
        validate_policy_selection(authored)


def test_legacy_resume_configuration_is_verified_before_normalization():
    import hashlib
    import json
    from tinystudio.lab.policy_selection import matching_resume_configuration
    old = {"learning": {"objective": "ppo"}, "parameters": {"initialization": "scratch", "lr": 0.01}, "seed": 4}
    saved = {"configuration": old, "config_sha256": hashlib.sha256(json.dumps(old, sort_keys=True).encode()).hexdigest()}
    current = deepcopy(old)
    current["parameters"]["initialization"] = "resume"
    assert matching_resume_configuration(saved, current)
    current["parameters"]["lr"] = 0.02
    assert not matching_resume_configuration(saved, current)
    saved["configuration"]["parameters"]["lr"] = 0.02
    assert not matching_resume_configuration(saved, current)  # Corrupt metadata cannot bypass the original digest.
