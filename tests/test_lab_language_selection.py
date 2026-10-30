"""Authored intent must be executable before any corpus or checkpoint is opened."""
import copy
import subprocess
import sys

import pytest

from tinystudio.lab.learning_selection import validate_language_selection


def definition(**learning):
    return {"learning": learning, "parameters": {}, "inputs": []}


@pytest.mark.parametrize("change, message", [
    ({"architecture": {"hidden_size": 9, "heads": 2}}, "divisible"),
    ({"family": "gru", "architecture": {"heads": 2}}, "unsupported architecture"),
    ({"objective": "dpo"}, "unsupported learning objective"),
    ({"update_strategy": "qlora"}, "unsupported update_strategy"),
    ({"representation": {"kind": "bpe", "unknown": 1}}, "representation options"),
    ({"representation": {"kind": "unknown"}}, "unsupported representation"),
    ({"representation": {"kind": "engineering"}}, "nonempty vocabulary"),
    ({"representation": {"kind": "byte", "vocabulary": ["ignored"]}}, "vocabulary"),
    ({"device": "cuda"}, "actual runtime"),
    ({"representation": None}, "representation"),
    ({"leakage_policy": "strict"}, "learning.leakage_policy.*parameters.leakage_policy"),
])
def test_rejects_static_incompatibilities(change, message):
    with pytest.raises(ValueError, match=message):
        validate_language_selection(definition(**change))


@pytest.mark.parametrize("family, representation", [
    ("transformer", "byte"), ("gru", "bpe"),
    ("rnn", {"kind": "engineering", "vocabulary": ["voltage", "current"]}),
])
def test_scratch_combinations_preserve_editable_input(family, representation):
    authored = definition(family=family, representation=representation)
    before = copy.deepcopy(authored)
    assert validate_language_selection(authored)["initialization"] == "scratch"
    assert authored == before


@pytest.mark.parametrize("initialization, role", [("weights", "initial_weights"), ("resume", "resume")])
def test_partial_checkpoint_dimensions_are_not_compared_to_scratch_defaults(initialization, role):
    authored = definition(initialization=initialization, architecture={"hidden_size": 9})
    authored["inputs"] = [{"role": role}]
    assert validate_language_selection(authored)["initialization"] == initialization
    authored["learning"]["architecture"]["heads"] = 2
    with pytest.raises(ValueError, match="divisible"):
        validate_language_selection(authored)


def test_distillation_can_inherit_engineering_vocabulary_without_declaring_kind():
    authored = definition(objective="distillation", representation={"vocabulary": ["valve"]})
    authored["inputs"] = [{"role": "teacher"}]
    assert validate_language_selection(authored)["objective"] == "distillation"


def test_conflicting_representation_options_and_legacy_objective_are_explicit():
    authored = definition(representation={"kind": "bpe", "vocab_size": 280})
    authored["parameters"]["vocab_size"] = 290
    with pytest.raises(ValueError, match="conflicting representation vocab_size"):
        validate_language_selection(authored)
    authored = definition()
    authored["parameters"]["objective"] = "dpo"
    with pytest.raises(ValueError, match="parameters.objective.*learning.objective"):
        validate_language_selection(authored)


def test_legacy_representation_is_not_silently_ignored():
    authored = definition()
    authored["parameters"]["representation"] = "bpe"
    with pytest.raises(ValueError, match="parameters.representation.*learning.representation"):
        validate_language_selection(authored)


def test_empty_representation_uses_byte_for_scratch():
    assert validate_language_selection(definition(representation={}))['objective'] == 'causal_lm'


def test_retrieval_requires_explicit_composition():
    authored = definition()
    authored["retrieval"] = {"mode": "lexical"}
    with pytest.raises(ValueError, match="explicitly composed frozen retrieval"):
        validate_language_selection(authored)


def test_preflight_and_executor_reject_before_artifact_loading(monkeypatch, tmp_path):
    from tinystudio.lab import learning
    from tinystudio.lab.contracts import ExperimentDefinition
    from tinystudio.lab.preflight import execution_checks
    authored = ExperimentDefinition(name="invalid", engine="learning.language", execution={"profile": "trusted_native"},
        learning={"objective": "dpo"}).model_dump()
    checks = execution_checks(ExperimentDefinition.model_validate(authored), {})
    failure = next(row for row in checks if row["code"] == "model_initialization")
    assert not failure["passed"]
    def forbidden(*args, **kwargs):
        pytest.fail("invalid selection reached artifact loading")
    monkeypatch.setattr(learning, "load_checkpoint", forbidden)
    monkeypatch.setattr(learning, "read_documents", forbidden)
    with pytest.raises(ValueError) as error:
        learning.execute_learning({"definition": authored}, tmp_path, lambda _: None, lambda: False)
    assert str(error.value) == failure["message"]


def test_preflight_does_not_import_torch():
    result = subprocess.run([sys.executable, "-c", '''
import sys
from tinystudio.lab.contracts import ExperimentDefinition
from tinystudio.lab.preflight import execution_checks
definition = ExperimentDefinition(name="CPU language", engine="learning.language",
    execution={"profile": "trusted_native"})
checks = execution_checks(definition, {"engines": ["learning.language"], "runtime": {"python": "3.12"}})
assert all(check["passed"] for check in checks)
assert "torch" not in sys.modules
'''], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
