import copy
import json
import math

import pytest

from tinystudio.lab.cosim import execute_cosim
from tinystudio.lab.engine import ExecutionCancelled


def execute(tmp_path, definition):
    spec = {"definition": {"engine": "system.cosim", "seed": 123, **definition}}
    before = copy.deepcopy(spec)
    result = execute_cosim(spec, tmp_path / "out", lambda event: None, lambda: False)
    assert spec == before
    rows = [json.loads(line) for line in (tmp_path / "out" / "processed.jsonl").read_text().splitlines()]
    return result, rows


def open_loop():
    return {"clocks": {"step": math.log(2), "steps": 3, "tolerance": 1e-9, "hold": "zero_order"},
            "components": [{"id": "u", "kind": "signal", "version": "1"},
                           {"id": "p", "kind": "plant", "version": "1"}],
            "connections": [{"source": "u", "target": "p"}]}


def closed_loop():
    return {"clocks": {"step": math.log(2), "steps": 5}, "components": [
        {"id": "p", "kind": "plant"}, {"id": "s", "kind": "sensor"},
        {"id": "t", "kind": "transport", "parameters": {"delay_steps": 1}},
        {"id": "c", "kind": "controller", "parameters": {"gain": 1, "setpoint": 1}}],
        "connections": [{"source": "p", "target": "s"}, {"source": "s", "target": "t"},
                        {"source": "t", "target": "c", "port": "measurement"},
                        {"source": "c", "target": "p"}]}


def values(rows, component, branch="baseline"):
    return [row["value"] for row in rows if row["component_id"] == component and row["component_kind"] != "plant_integration" and row["branch_id"] == branch]


def test_analytic_first_order_composition_and_lineage(tmp_path):
    result, rows = execute(tmp_path, open_loop())
    assert values(rows, "p") == pytest.approx([0, .5, .75])
    assert result["qualification"]["physical_validation"] is False
    plant_at_second = next(row for row in rows if row["record_id"] == "baseline:p:1")
    assert plant_at_second["source_record_ids"] == ["baseline:p:integration:0"]
    first_integration = next(row for row in rows if row["record_id"] == "baseline:p:integration:0")
    assert first_integration["source_record_ids"] == ["baseline:u:0"]


def test_delayed_feedback_has_known_response_and_visible_history(tmp_path):
    _, rows = execute(tmp_path, closed_loop())
    # dt=ln2: x_next=.5*x+.5*u; delayed measurement means u=[1,1,.5,.25,.375].
    assert values(rows, "p") == pytest.approx([0, .5, .75, .625, .4375])
    assert values(rows, "c") == pytest.approx([1, 1, .5, .25, .375])
    controller = next(row for row in rows if row["record_id"] == "baseline:c:2")
    assert controller["inputs_visible_before_decision"]["measurement"]["value"] == .5
    by_id = {row["record_id"]: row for row in rows}
    for row in rows:
        for source in row["source_record_ids"]:
            assert by_id[source]["available_at"] <= row["available_at"] + 1e-9


def test_interventions_distinguish_physical_sensor_and_transport_changes(tmp_path):
    definition = closed_loop()
    definition["scenarios"] = [
        {"id": "physical", "interventions": [{"component_id": "p", "parameters": {"disturbance": .2}}]},
        {"id": "sensor", "interventions": [{"component_id": "s", "parameters": {"bias": .2}}]},
        {"id": "transport", "interventions": [{"component_id": "t", "parameters": {"delay_steps": 2}}]},
    ]
    result, rows = execute(tmp_path, definition)
    assert result["metrics"]["branches"] == 4
    assert values(rows, "p", "physical")[1] == pytest.approx(.6)
    assert values(rows, "s", "sensor")[0] == .2
    assert values(rows, "p", "sensor")[1] == pytest.approx(.5)
    assert values(rows, "c", "transport")[:3] == [1, 1, 1]
    assert all(row["interventions"] for row in rows if row["branch_id"] != "baseline")


def test_multirate_hold_and_staleness_keep_original_timestamp(tmp_path):
    definition = closed_loop()
    definition["components"][2]["parameters"]["freeze_after"] = math.log(2) * 2
    _, rows = execute(tmp_path, definition)
    measurements = [row["inputs_visible_before_decision"]["measurement"] for row in rows if row["component_id"] == "c"]
    assert measurements[2]["record_id"] == measurements[4]["record_id"]
    assert measurements[4]["time"] == pytest.approx(math.log(2))
    assert measurements[4]["source_time"] < math.log(2) * 4


def test_algebraic_loop_rejected_but_explicit_delay_resolves_it(tmp_path):
    definition = {"clocks": {"steps": 3}, "components": [
        {"id": "a", "kind": "transport", "parameters": {"delay_steps": 0}},
        {"id": "b", "kind": "transport", "parameters": {"delay_steps": 0}}],
        "connections": [{"source": "a", "target": "b"}, {"source": "b", "target": "a"}]}
    with pytest.raises(ValueError, match="algebraic"):
        execute(tmp_path, definition)
    definition["components"][0]["parameters"]["delay_steps"] = 1
    _, rows = execute(tmp_path, definition)
    assert len(rows) == 6


def test_component_order_does_not_change_event_order_or_seed(tmp_path):
    definition = closed_loop()
    definition["components"][1]["parameters"] = {"noise_std": .1}
    _, rows = execute(tmp_path, definition)
    definition["components"].reverse()
    definition["connections"].reverse()
    other = tmp_path / "other"
    other.mkdir()
    _, again = execute(other, definition)
    assert rows == again


def test_period_validation_version_and_cancel(tmp_path):
    definition = open_loop()
    definition["components"][0]["period"] = math.log(2) * 1.5
    with pytest.raises(ValueError, match="integer multiples"):
        execute(tmp_path, definition)
    definition = open_loop()
    definition["components"][0]["version"] = "999"
    with pytest.raises(ValueError, match="version"):
        execute(tmp_path, definition)
    with pytest.raises(ExecutionCancelled):
        execute_cosim({"definition": {"engine": "system.cosim", **open_loop()}}, tmp_path / "cancel", lambda event: None, lambda: True)


def test_multirate_source_uses_zero_order_hold(tmp_path):
    definition = open_loop()
    dt = math.log(2)
    definition["clocks"]["steps"] = 4
    definition["components"][0].update(period=2*dt, parameters={"waveform": "step", "step_time": dt})
    _, rows = execute(tmp_path, definition)
    assert values(rows, "u") == [0, 1]
    assert values(rows, "p") == pytest.approx([0, 0, 0, .5])
    receipt = next(row for row in rows if row["record_id"] == "baseline:p:integration:1")
    assert receipt["source_record_ids"] == ["baseline:u:0"]
