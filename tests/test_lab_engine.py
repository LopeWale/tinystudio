"""Pure lab tests: independent reference values, no database or queue fixtures."""
import hashlib
import json
import math
from pathlib import Path

import pytest

from tinystudio.lab.engine import ExecutionCancelled, execute
from tinystudio.lab.signals import number


def run(tmp_path, *, engine="synthetic.signal", parameters=None, pipeline=None, mode="offline", **extra):
    spec = {"definition": {"engine": engine, "seed": 17, "mode": mode,
                           "parameters": parameters or {"samples": 5, "dt": 0.25},
                           "pipeline": pipeline or [], **extra.pop("definition", {})}, **extra}
    result = execute(spec, tmp_path / "out", lambda event: None, lambda: False)
    rows = [json.loads(line) for line in (tmp_path / "out" / "processed.jsonl").read_text().splitlines()]
    return result, rows


def recorded(tmp_path, values, *, episodes=None, available=None):
    source = tmp_path / "recording.jsonl"
    source.write_text("".join(json.dumps({"record_id": f"r{i}", "episode_id": episodes[i] if episodes else "a",
                                         "time": i if not episodes else i % 2,
                                         "available_at": available[i] if available else i if not episodes else i % 2,
                                         "value": value, "units": "V"}) + "\n" for i, value in enumerate(values)))
    return {"engine": "recorded.replay", "input_paths": {"source": str(source)},
            "definition": {"inputs": [{"artifact_id": "source", "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "role": "source"}]}}


def test_sine_reference_and_deterministic_originals(tmp_path):
    result, rows = run(tmp_path)
    assert [r["value"] for r in rows] == pytest.approx([0, 1, 0, -1, 0], abs=1e-14)
    assert [r["time"] for r in rows] == [0, .25, .5, .75, 1]
    assert result["records"] == 5
    for artifact in result["artifacts"]:
        assert hashlib.sha256(Path(artifact["path"]).read_bytes()).hexdigest() == artifact["sha256"]
    second = tmp_path / "second"
    second.mkdir()
    _, again = run(second)
    assert again == rows


def test_negative_decimal_and_nonfinite_boundary():
    assert number("-0.125", "offset") == -.125
    for value in [True, None, "NaN", "inf", float("-inf")]:
        with pytest.raises(ValueError):
            number(value, "offset")


def test_raw_replay_bytes_unchanged_by_transform(tmp_path):
    kwargs = recorded(tmp_path, [1, None, 3])
    original = (tmp_path / "recording.jsonl").read_bytes()
    result, rows = run(tmp_path, pipeline=[{"kind": "scale_offset", "parameters": {"factor": -2, "offset": -.5}}], **kwargs)
    assert [r["value"] for r in rows] == [-2.5, None, -6.5]
    assert (tmp_path / "recording.jsonl").read_bytes() == original
    assert (tmp_path / "out" / "original-0.jsonl").read_bytes() == original
    raw = [json.loads(line) for line in (tmp_path / "out" / "raw.jsonl").read_text().splitlines()]
    assert [r["value"] for r in raw] == [1, None, 3]
    assert result["metrics"]["missing_records"] == 1


def test_filter_preserves_gaps_and_features_are_causal(tmp_path):
    _, rows = run(tmp_path, pipeline=[{"kind": "moving_average", "parameters": {"window": 2}},
                                     {"kind": "features", "parameters": {"window": 2}}], **recorded(tmp_path, [2, None, 6, 10]))
    assert [r["value"] for r in rows] == [2, None, 6, 8]
    assert rows[2]["features"]["mean"] == 6
    assert rows[3]["features"] == {"mean": 7, "rms": math.sqrt(50), "count": 2, "window_count": 2}


def test_imputation_resets_at_episode_boundary(tmp_path):
    _, rows = run(tmp_path, pipeline=[{"kind": "impute"}], **recorded(tmp_path, [2, None, None, 7], episodes=["a", "a", "b", "b"]))
    assert [r["value"] for r in rows] == [2, 2, None, 7]
    assert rows[1]["observed"] is False
    assert rows[1]["quality"] == "imputed"


def test_episode_split_cannot_leak_between_partitions(tmp_path):
    _, rows = run(tmp_path, parameters={"samples": 4, "episodes": 12}, pipeline=[{"kind": "split", "parameters": {"train": .5, "validation": .25}}])
    for episode in {r["episode_id"] for r in rows}:
        assert len({r["split"] for r in rows if r["episode_id"] == episode}) == 1
    assert len({r["split"] for r in rows}) >= 2


def test_offline_interpolation_tracks_future_availability(tmp_path):
    _, rows = run(tmp_path, pipeline=[{"kind": "resample", "parameters": {"dt": .5, "method": "linear"}}], **recorded(tmp_path, [2, 6]))
    assert [r["value"] for r in rows] == [2, 4, 6]
    assert rows[1]["time"] == .5
    assert rows[1]["available_at"] == 1
    assert rows[1]["source_record_ids"] == ["r0", "r1"]
    assert rows[1]["observed"] is False


def test_online_interpolation_rejected_and_hold_is_causal(tmp_path):
    with pytest.raises(ValueError, match="future"):
        run(tmp_path, mode="online", pipeline=[{"kind": "resample", "parameters": {"method": "linear"}}])
    other = tmp_path / "other"
    other.mkdir()
    _, rows = run(other, mode="online", pipeline=[{"kind": "resample", "parameters": {"dt": .5}}], **recorded(other, [2, 6]))
    assert [r["value"] for r in rows] == [2, 2, 6]
    assert rows[1]["available_at"] == .5


def test_missing_endpoints_never_invent_interpolation(tmp_path):
    _, rows = run(tmp_path, pipeline=[{"kind": "resample", "parameters": {"dt": .5, "method": "linear"}}], **recorded(tmp_path, [2, None, 6]))
    assert [r["value"] for r in rows] == [2, None, None, None, 6]


@pytest.mark.parametrize("representation", ["numeric", "text", "bytes", "state_tokens"])
def test_representation_does_not_destroy_scientific_value(tmp_path, representation):
    _, rows = run(tmp_path, parameters={"samples": 1, "waveform": "constant", "amplitude": -.125},
                  pipeline=[{"kind": "representation", "parameters": {"format": representation}}])
    assert rows[0]["value"] == -.125
    assert rows[0]["representation_type"] == representation
    if representation == "text":
        assert rows[0]["representation"] == "-0.125 unitless"
    if representation == "bytes":
        import base64
        assert base64.b64decode(rows[0]["representation"]) == b"-0.125"


def test_first_order_plant_reference(tmp_path):
    _, rows = run(tmp_path, engine="simulation.plant", parameters={"samples": 3, "dt": math.log(2), "setpoint": 1})
    assert [r["value"] for r in rows] == pytest.approx([0, .5, .5])
    assert [r["control"] for r in rows] == pytest.approx([1, .5, .5])


def test_cancel_and_unsupported_operations_are_explicit(tmp_path):
    with pytest.raises(ExecutionCancelled):
        execute({"definition": {"engine": "synthetic.signal"}}, tmp_path / "cancel", lambda event: None, lambda: True)
    with pytest.raises(ValueError, match="unsupported"):
        run(tmp_path, pipeline=[{"kind": "pretend_physics"}])


def test_hash_mismatch_and_time_regression_rejected(tmp_path):
    kwargs = recorded(tmp_path, [1, 2])
    kwargs["definition"]["inputs"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="checksum"):
        run(tmp_path, **kwargs)


def test_seeded_noise_and_missingness_are_reproducible(tmp_path):
    parameters = {"samples": 100, "noise": .2, "missing_probability": .3}
    _, first = run(tmp_path, parameters=parameters)
    other = tmp_path / "other"
    other.mkdir()
    _, second = run(other, parameters=parameters)
    assert first == second
    assert any(row["value"] is None for row in first)
    assert any(row["value"] is not None for row in first)


def test_parquet_retains_scalar_and_extra_fields(tmp_path):
    import pyarrow.parquet as pq
    _, rows = run(tmp_path, pipeline=[{"kind": "features"}])
    table = pq.read_table(tmp_path / "out" / "processed.parquet").to_pylist()
    assert len(table) == len(rows)
    assert table[1]["value"] == 1
    assert json.loads(table[1]["record_json"])["features"] == rows[1]["features"]


def test_time_regression_and_duplicate_record_ids_rejected(tmp_path):
    for index, mutate in enumerate((lambda rows: rows[1].update(time=-1), lambda rows: rows[1].update(record_id="r0"))):
        folder = tmp_path / str(index)
        folder.mkdir()
        kwargs = recorded(folder, [1, 2])
        source = folder / "recording.jsonl"
        rows = [json.loads(line) for line in source.read_text().splitlines()]
        mutate(rows)
        source.write_text("".join(json.dumps(row) + "\n" for row in rows))
        kwargs["definition"]["inputs"][0]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
        with pytest.raises(ValueError, match="increase|duplicate"):
            run(folder, **kwargs)


def test_delayed_observation_delays_filtered_availability(tmp_path):
    _, rows = run(tmp_path, pipeline=[{"kind": "moving_average", "parameters": {"window": 2}}],
                  **recorded(tmp_path, [2, 6], available=[5, 1]))
    assert rows[1]["value"] == 4
    assert rows[1]["available_at"] == 5


def test_catalog_never_marks_unimplemented_adapter_available():
    from tinystudio.lab.catalog import catalog
    manifests = catalog()
    assert {"synthetic.signal", "recorded.replay", "simulation.plant"} <= {
        item["id"] for item in manifests if item["implemented"]}
    assert all(not item["available"] for item in manifests if not item["implemented"])
    assert all(item["version"].startswith("sha256:") for item in manifests if item["implemented"])
