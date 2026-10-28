"""A: plugin declarations remain explicit and cannot manufacture qualification."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from tinystudio.lab.plugins import PluginManifest
from tinystudio.lab.service import catalog


def test_shared_authoring_fixture_retains_all_versioned_fields_and_port_semantics():
    from tinystudio.lab.contracts import ExperimentDefinition

    raw = json.loads((Path(__file__).parent / "fixtures/lab_definition_v1.json").read_text())
    definition = ExperimentDefinition.model_validate(raw)
    assert definition.model_dump() == raw
    assert definition.connections[0]["port"] == "input"
    assert definition.clocks["hold"] == "zero_order"
    with pytest.raises(ValueError):
        ExperimentDefinition.model_validate({**raw, "schema_version": 2})


def test_all_catalog_entries_have_versioned_contracts_and_pending_fixture_requirements():
    portfolio = catalog()
    rows = portfolio["adapters"] + portfolio["methods"]
    assert len({row["id"] for row in rows}) == len(rows)
    assert {kind for row in rows for kind in row["manifest"]["kinds"]} == {
        "source", "simulation", "clock", "transformation", "representation",
        "trainer", "retriever", "evaluator", "deployment"}
    root = Path(__file__).resolve().parents[1]
    requirements = {item["plugin_id"]: item for item in portfolio["qualification_matrix"]}
    assert set(requirements) == {row["id"] for row in rows}
    for row in rows:
        manifest = PluginManifest.model_validate(row["manifest"])
        assert manifest.id == row["id"] and manifest.implemented == row["implemented"]
        assert manifest.qualification == requirements[manifest.id]["qualification"] == "unqualified"
        assert manifest.targets.qualification_required
        assert all(port.units for port in manifest.inputs + manifest.outputs)
        if row["implemented"]:
            assert manifest.lifecycle.pause == "unsupported"
            assert manifest.lifecycle.cancellation == "terminate_owned_attempt"
            assert manifest.fixtures
            for fixture in manifest.fixtures:
                assert (root / fixture.path).is_file()
                assert any("skipped/mocked" in criterion for criterion in fixture.evidence_required)
        else:
            assert manifest.implementation_version is None and manifest.lifecycle is None


@pytest.mark.parametrize("alter", [
    lambda row: row.update(schema_version=2),
    lambda row: row.update(qualification="qualified"),
    lambda row: row.update(implementation_version=None),
    lambda row: row.update(lifecycle=None),
    lambda row: row.update(fixtures=[]),
    lambda row: row["inputs"].append(deepcopy(row["inputs"][0])),
    lambda row: row["fixtures"][0].update(path="../outside.py"),
    lambda row: row["outputs"][0].update(units="silently guessed"),
])
def test_invalid_or_overstated_plugin_contracts_reject(alter):
    row = deepcopy(catalog()["adapters"][0]["manifest"])
    alter(row)
    with pytest.raises(ValueError):
        PluginManifest.model_validate(row)


def test_platform_and_selection_requirements_do_not_hide_catalog_options():
    portfolio = catalog()
    rows = {row["id"]: row for row in portfolio["adapters"] + portfolio["methods"]}
    assert rows["network.mininet"]["manifest"]["targets"]["operating_systems"] == ["Linux"]
    assert "learning.pretrained" in rows
    requirement = rows["learning.pretrained"]["manifest"]
    assert requirement["selections_require_separate_qualification"] is True
    assert any("QLoRA" in item for item in requirement["targets"]["additional_requirements"])
    # A catalog entry's presence or an installed dependency is not qualification.
    assert all(item["qualification"] == "unqualified" for item in portfolio["qualification_matrix"])


def test_missing_optional_implementation_preserves_the_rest_of_the_catalog(monkeypatch):
    exists, read_bytes = Path.exists, Path.read_bytes
    def optional_missing(path):
        return False if path.name in {"timeseries.py", "rl.py"} else exists(path)
    def guarded_read(path):
        if path.name in {"timeseries.py", "rl.py"}:
            pytest.fail("catalog must not read a source it already declared absent")
        return read_bytes(path)
    monkeypatch.setattr(Path, "exists", optional_missing)
    monkeypatch.setattr(Path, "read_bytes", guarded_read)
    rows = {row["id"]: row for row in catalog()["methods"]}
    for name in ("learning.timeseries", "learning.multimodal", "learning.control_rl", "learning.tool_rl"):
        assert rows[name]["implemented"] is False
        assert rows[name]["available"] is False
        assert rows[name]["manifest"]["implementation_version"] is None
        assert rows[name]["manifest"]["lifecycle"] is None
    assert rows["learning.language"]["implemented"] is True


def test_returned_schema_documents_cannot_mutate_later_catalogs():
    first = catalog()
    first["contract_schemas"]["tinystudio.lab.definition.v1"]["title"] = "caller mutation"
    assert catalog()["contract_schemas"]["tinystudio.lab.definition.v1"]["title"] == "ExperimentDefinition"


def test_pretrained_binding_declarations_agree_with_the_existing_resolver(tmp_path):
    import hashlib
    from tinystudio.lab.compatibility import resolve_role, _rows
    from tinystudio.lab.plugins import SCHEMAS, input_contracts

    ports = {port.role: port for port in input_contracts("learning.pretrained") if port.role}
    assert (ports["pretrained_model"].min_items, ports["pretrained_model"].max_items) == (1, 1)
    assert ports["pretrained_model"].visibility == ["model"]
    assert ports["snapshot"].min_records == 3 and ports["validation"].min_records == 1
    assert "messages" in SCHEMAS[ports["snapshot"].content_schema_ref]["items"]["required"]
    file = tmp_path / "snapshot.json"
    file.write_text(json.dumps([{"messages": [{"role": "user", "content": "pinned"}]}] * 3))
    ref = {"artifact_id": "one", "role": "snapshot", "sha256": hashlib.sha256(file.read_bytes()).hexdigest(), "visibility": "model"}
    spec = {"definition": {"inputs": [ref]}, "input_paths": {"one": str(file)}}
    assert len(_rows(resolve_role(spec, "snapshot"))) == ports["snapshot"].min_records
    for references, message in (([], "required"), ([ref, ref], "exactly one"),
                                ([{**ref, "visibility": "truth"}], "model-visible")):
        spec["definition"]["inputs"] = references
        with pytest.raises(ValueError, match=message):
            resolve_role(spec, "snapshot")


@pytest.mark.parametrize("first", ["tinystudio.db.models", "tinystudio.lab.models"])
def test_fresh_import_orders_register_all_laboratory_tables(first):
    import subprocess
    import sys
    source = f"import {first}; from tinystudio.db.models import Base; from tinystudio.lab.models import TABLES; assert {{t.name for t in TABLES}} <= set(Base.metadata.tables)"
    subprocess.run([sys.executable, "-c", source], check=True, timeout=15)
