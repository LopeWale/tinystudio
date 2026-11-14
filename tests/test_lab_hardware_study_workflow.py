"""Real loopback MQTT -> authenticated HTTP/native curation -> retained study.

The units here are explicitly authored simulator resets, with repeated ticks
averaged within each reset. Resetting is not proof of statistical independence.
Any interval applies only under the declared simulated-episode assumptions; this
test provides no physical stability, supervision or session-coordinator evidence.
"""
from __future__ import annotations

import base64
from collections import defaultdict
from copy import deepcopy
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import statistics

import pytest

from hardware_emulators import MqttTestDevice, OwnedMqttBroker
from tests.test_lab_analysis_http import wait_advertisement
from tests.test_lab_native_workflow import native_http, posted
from tinystudio.lab import operations
from tinystudio.lab.runner import Runner


UNIT = "fixture-unit"
TICKS_PER_RESET = 3
EPISODES = [
    {"label": "baseline-reset-1", "condition": "baseline", "initial_value": -1.25, "seed": 401},
    {"label": "baseline-reset-2", "condition": "baseline", "initial_value": -.75, "seed": 402},
    {"label": "intervention-reset-1", "condition": "intervention", "initial_value": .125, "seed": 501},
    {"label": "intervention-reset-2", "condition": "intervention", "initial_value": .625, "seed": 502},
]
EPISODES = [{**episode, "noise": .01, "sample_period": .03} for episode in EPISODES]
EXECUTION = {"profile": "trusted_native", "backend": "native", "max_seconds": 30}


@pytest.fixture
def owned_study_broker(tmp_path):
    binary = os.environ.get("TINYSTUDIO_TEST_MQTT_BROKER")
    if not binary:
        pytest.skip("hardware study MQTT workflow unqualified: set TINYSTUDIO_TEST_MQTT_BROKER to an owned Mosquitto executable")
    pytest.importorskip("paho.mqtt.client")
    with OwnedMqttBroker(binary, tmp_path / "study-broker") as broker:
        yield broker


def _get(fixture, suffix):
    response = fixture.http.get(fixture.prefix + suffix)
    assert response.status_code == 200, response.text
    return response.json()


def _download(fixture, artifact):
    response = fixture.http.get(fixture.prefix + "/artifacts/" + artifact["id"])
    assert response.status_code == 200, response.text
    assert len(response.content) == artifact["size"]
    assert hashlib.sha256(response.content).hexdigest() == artifact["sha256"]
    return response.content


def _rows(content):
    return [json.loads(line) for line in content.splitlines() if line.strip()]


def _run_saved(fixture, runner, target, saved, key):
    checked = posted(fixture.http, fixture.prefix + "/preflight",
                     json={"definition": saved["definition"], "target_id": target["id"]})
    assert checked["compatible"], json.dumps(checked, indent=2)
    submitted = posted(fixture.http, fixture.prefix + "/runs", json={
        "definition_id": saved["id"], "revision": saved["revision"],
        "target_id": target["id"], "idempotency_key": key})
    assert runner.run_once(run_id=submitted["id"])
    complete = _get(fixture, "/runs/" + submitted["id"])
    assert complete["state"] == "succeeded", complete
    assert complete["spec_digest"] == submitted["spec_digest"]
    contents = {artifact["name"]: _download(fixture, artifact)
                for artifact in complete["result"]["artifacts"]}
    return complete, contents


def _run_definition(fixture, runner, target, definition, key):
    saved = posted(fixture.http, fixture.prefix + "/definitions", json={"definition": definition})
    return _run_saved(fixture, runner, target, saved, key)


def test_authored_simulator_episodes_reach_curated_atomic_import_and_retained_analysis(
        native_http, owned_study_broker):
    f, broker = native_http, owned_study_broker
    bindings, topics = {}, {}
    for episode in EPISODES:
        label = episode["label"]
        topics[label] = {name: f"owned-study/{label}/{name}" for name in ("command", "ack", "telemetry")}
        bindings[label] = {"engine": "acquisition.mqtt", "read_only": True,
            "endpoint": {"host": broker.host, "port": broker.port, "topic": topics[label]["telemetry"], "tls": False},
            "source_clock": {"domain": "owned-simulator-host-unix", "kind": "unix_seconds", "synchronized": False},
            "limits": {"max_samples": TICKS_PER_RESET, "max_duration": 3, "max_timeout": 1,
                       "max_frame_bytes": 4096, "max_queue_frames": 64}}
    protocol = {"name": "Owned MQTT simulated episode contrast", "status": "software-fixture",
        "description": "Arbitrary diagnostic values; numerical workflow evidence for authored simulated episodes only.",
        "hypothesis": "Compare the two authored conditions across simulator reset episodes.",
        "independent_unit": "One researcher-declared simulator reset episode; ticks are repeated measurements, and resetting alone does not prove independence.",
        "conditions": [{"id": "baseline"}, {"id": "intervention"}],
        "outcomes": [{"id": "response", "unit": UNIT}],
        "sampling_plan": {"episodes_per_condition": 2, "ticks_per_episode": TICKS_PER_RESET,
                          "within_episode_reduction": "mean"},
        "metadata": {"episodes": deepcopy(EPISODES), "independence_proven": False,
            "inference_scope": "Authored simulated episodes under the declared independent-episode assumption only; no physical population inference.",
            "simulator_source_sha256": hashlib.sha256(Path(inspect.getfile(MqttTestDevice)).read_bytes()).hexdigest(),
            "broker_binary_sha256": hashlib.sha256(broker.binary.read_bytes()).hexdigest(),
            "physical_stability_qualified": False}}
    study = posted(f.http, f.prefix + "/studies", json={"study": protocol, "idempotency_key": "mqtt-episode-study"})
    study_path = "/studies/" + study["id"]
    target = posted(f.http, f.prefix + "/targets", json={"name": "owned-mqtt-study-native"})
    runner = Runner(f.url, target["id"], target["token"], heartbeat_interval=.1,
        profile_config={"schema_version": 1, "backends": {},
                        "engine_grants": {"acquisition.mqtt": {"bindings": bindings}}})
    acquisitions, processed_by_episode = {}, {}
    try:
        runner.advertise()
        wait_advertisement(f, target)
        assert runner.client.get(f.prefix + "/studies").status_code == 401
        for episode in EPISODES:
            label = episode["label"]
            initial = {"diagnostic": {"value": episode["initial_value"], "unit": UNIT}}
            with MqttTestDevice(broker, topics[label], label, initial, stop_parameters=initial,
                    telemetry_parameter="diagnostic", sample_period=episode["sample_period"],
                    noise=episode["noise"], seed=episode["seed"]) as simulated:
                assert simulated.parameters == initial and simulated.outcomes == {}
                definition = {"name": "Capture " + label, "engine": "acquisition.mqtt", "domain": "hardware",
                    "seed": episode["seed"], "mode": "online", "operating_mode": "acquisition",
                    "parameters": {"binding_id": label, "samples": TICKS_PER_RESET, "duration": 3,
                        "timeout": 1, "max_frame_bytes": 4096, "max_queue_frames": 64, "units": UNIT,
                        "decode": "json", "value_key": "value", "source_id_key": "record_id",
                        "timestamp_key": "source_timestamp", "fixture_configuration": deepcopy(episode)},
                    "execution": EXECUTION}
                run, contents = _run_definition(f, runner, target, definition, "capture-" + label)
                assert simulated.received_requests == [] and simulated.effects == []
            raw, processed = _rows(contents["raw.jsonl"]), _rows(contents["processed.jsonl"])
            assert len(raw) == len(processed) == TICKS_PER_RESET
            assert run["result"]["metrics"]["actuator_writes"] == 0
            assert run["result"]["qualification"]["physical_validation"] is False
            assert run["spec"]["definition"]["parameters"]["fixture_configuration"] == episode
            artifacts = {item["name"]: item for item in run["result"]["artifacts"]}
            acquisitions[run["id"]] = {"run": run, "episode": episode, "artifacts": artifacts, "contents": contents}
            processed_by_episode[run["id"]] = processed
            for source, row in zip(raw, processed):
                payload = base64.b64decode(source["wire_base64"], validate=True)
                decoded = json.loads(payload)
                assert source["wire_scope"] == "mqtt_application_payload"
                assert source["transport_metadata"]["wire_sha256"] == hashlib.sha256(payload).hexdigest()
                assert source["transport_metadata"]["original_wire_bytes"] == len(payload)
                assert source["decoded_payload"] == decoded
                assert decoded["clock"]["source_timestamp"] == "host_unix_time"
                assert decoded["clock"]["simulation"] == "host-paced discrete ticks"
                assert decoded["physical_stability_qualified"] is False
                assert row["source_identity"] == source["source_identity"] == decoded["record_id"]
                assert row["source_timestamp"] == source["source_timestamp"] == decoded["source_timestamp"]
                assert row["episode_id"] == run["id"] and row["units"] == UNIT
                assert row["value"] == decoded["value"]
                assert abs(row["value"] - episode["initial_value"]) <= episode["noise"]
            posted(f.http, f.prefix + study_path + "/records", json={"revision": 1,
                "idempotency_key": "reset-configuration-" + label, "kind": "episode_configuration",
                "unit_id": run["id"], "condition": episode["condition"], "run_ids": [run["id"]],
                "artifact_ids": [artifacts["raw.jsonl"]["id"], artifacts["processed.jsonl"]["id"]],
                "data": {"fixture_configuration": episode, "units": UNIT, "independence_proven": False}})

        assert len(processed_by_episode) == 4
        curated_by_id = {}
        for condition in ("baseline", "intervention"):
            episodes = [item for item in acquisitions.values() if item["episode"]["condition"] == condition]
            refs = [{"artifact_id": item["artifacts"]["processed.jsonl"]["id"],
                     "sha256": item["artifacts"]["processed.jsonl"]["sha256"], "role": "source", "visibility": "evaluation"}
                    for item in episodes]
            curated, contents = _run_definition(f, runner, target, {
                "name": "Curate captured " + condition, "engine": "data.pipeline", "mode": "offline",
                "inputs": refs, "parameters": {"format": "jsonl", "cleaning": {"duplicates": "error"},
                    "resources": {"max_rows": 6, "source_bytes": 1024 ** 2, "per_source_bytes": 1024 ** 2}},
                "pipeline": [], "execution": EXECUTION}, "curate-" + condition)
            assert curated["result"]["qualification"]["dataset_ready"] is False
            artifact = next(item for item in curated["result"]["artifacts"] if item["role"] == "transformed_dataset")
            content = contents[artifact["name"]]
            curated_by_id[artifact["id"]] = (artifact, content, _rows(content))
            assert len(curated_by_id[artifact["id"]][2]) == 6
            for index, captured in enumerate(episodes):
                assert contents[f"original-{index}.jsonl"] == captured["contents"]["processed.jsonl"]
            for row in curated_by_id[artifact["id"]][2]:
                original = processed_by_episode[row["episode_id"]][row["source_position"]]
                source = acquisitions[row["episode_id"]]["artifacts"]["processed.jsonl"]
                assert row["source_artifact_id"] == source["id"] and row["source_sha256"] == source["sha256"]
                assert row["source_identity"] == original["source_identity"]
                assert row["value"] == original["value"] and row["units"] == original["units"] == UNIT
            projection = {"selection": {"all": True}, "number_encoding": "decimal_strings",
                "unit_id": {"pointer": "/episode_id"}, "condition": {"literal": condition},
                "data": {"response": {"pointer": "/value"}, "units": {"pointer": "/units"},
                    "source_identity": {"pointer": "/source_identity"}, "source_timestamp": {"pointer": "/source_timestamp"},
                    "acquisition_artifact_id": {"pointer": "/source_artifact_id"},
                    "acquisition_sha256": {"pointer": "/source_sha256"},
                    "acquisition_row": {"pointer": "/source_position"}}}
            body = {"artifact_id": artifact["id"], "revision": 1, "projection": projection}
            before = _get(f, study_path)["last_sequence"]
            preview = posted(f.http, f.prefix + study_path + "/observation-imports/preview", json=body)
            assert preview["summary"]["count"] == 6 and preview["source"]["sha256"] == artifact["sha256"]
            assert _get(f, study_path)["last_sequence"] == before
            exact = {**body, "preview_digest": preview["digest"], "idempotency_key": "import-" + condition}
            rejected = f.http.post(f.prefix + study_path + "/observation-imports", json={**exact, "preview_digest": "0" * 64})
            assert rejected.status_code == 409, rejected.text
            assert _get(f, study_path)["last_sequence"] == before
            applied = posted(f.http, f.prefix + study_path + "/observation-imports", json=exact)
            assert applied["count"] == 6 and applied["last_sequence"] - applied["first_sequence"] == 5
            assert _get(f, study_path)["last_sequence"] == before + 7
            replay = posted(f.http, f.prefix + study_path + "/observation-imports", json=exact)
            assert replay["replayed"] is True and replay["receipt"]["id"] == applied["receipt"]["id"]
            assert _get(f, study_path)["last_sequence"] == before + 7

        records = _get(f, study_path + "/records")["records"]
        observations = [row for row in records if row["kind"] == "observation"]
        assert len(records) == 18 and len(observations) == 12
        assert {row["condition"] for row in observations} == {"baseline", "intervention"}
        assert {row["unit_id"] for row in observations} == set(processed_by_episode)
        for observed in observations:
            provenance = observed["data"]["_source_projection"]
            artifact, content, rows = curated_by_id[provenance["artifact_id"]]
            line = provenance["line"]
            assert provenance["artifact_sha256"] == artifact["sha256"]
            assert provenance["raw_line_sha256"] == hashlib.sha256(content.splitlines(keepends=True)[line - 1]).hexdigest()
            assert observed["artifact_references"][0]["sha256"] == artifact["sha256"]
            assert observed["data"]["source_identity"] == rows[line - 1]["source_identity"]
            assert observed["data"]["units"] == UNIT

        boundary = _get(f, study_path)["last_sequence"]
        capture = posted(f.http, f.prefix + study_path + "/analysis-inputs", json={
            "revision": 1, "through_seq": boundary, "idempotency_key": "captured-episode-analysis-input"})
        assert operations.run_operation(capture["id"], f.factory)["state"] == "succeeded"
        operation = _get(f, "/operations/" + capture["id"])
        assert operation["state"] == "succeeded", operation
        snapshot = operation["result"]["artifact"]
        snapshot_content = _download(f, snapshot)
        assert next(row for row in _rows(snapshot_content) if row["kind"] == "protocol")["snapshot"]["study"]["metadata"]["episodes"] == EPISODES
        parameters = {"method": "means.welch", "confidence": .95, "alternative": "two-sided",
            "selection": {"mode": "all", "record_ids": [], "exclude_ids": [], "kinds": ["observation"]},
            "outcome_path": "/data/response", "condition_path": "/condition", "unit_path": "/unit_id",
            "measurement_unit": UNIT, "repeated": "mean", "missing": "error", "groups": ["baseline", "intervention"],
            "assumptions": ["Independent simulator reset episodes are a researcher-declared assumption, not established by resetting.",
                "Ticks are repeated measurements within an episode and are averaged before inference.",
                "The interval is scoped to these authored simulated episodes; no physical population or stability claim."]}
        saved = posted(f.http, f.prefix + "/statistics/analyses", json={"name": "Authored reset-episode contrast",
            "snapshot_artifact_id": snapshot["id"], "parameters": parameters, "execution": EXECUTION})
        analysis, contents = _run_saved(f, runner, target, saved, "analyze-reset-episode-means")
        report = json.loads(contents["statistical_analysis.json"])
        assert report["source"]["sha256"] == snapshot["sha256"] and report["source"]["through_seq"] == boundary
        assert report["request"] == parameters
        assert report["result"]["counts"] == {"group_a": 2, "group_b": 2}
        assert report["selection"]["counts"]["selected_records"] == 12
        assert report["qualification"]["independence"] == "researcher_declared"
        units = _rows(contents["analysis_units.jsonl"])
        assert len(units) == 4
        record_ids_by_unit = defaultdict(set)
        for observed in observations:
            record_ids_by_unit[observed["unit_id"]].add(observed["id"])
        expected_means = {unit: statistics.mean(row["value"] for row in rows)
                          for unit, rows in processed_by_episode.items()}
        for unit in units:
            assert unit["reduction"] == "mean"
            assert set(unit["record_ids"]) == record_ids_by_unit[unit["unit"]]
            assert len(unit["record_ids"]) == TICKS_PER_RESET
            assert unit["value"] == pytest.approx(expected_means[unit["unit"]])
        groups = [[expected_means[identity] for identity, item in acquisitions.items()
                   if item["episode"]["condition"] == condition] for condition in ("baseline", "intervention")]
        means = [sum(group) / len(group) for group in groups]
        variances = [sum((value - mean) ** 2 for value in group) / (len(group) - 1)
                     for group, mean in zip(groups, means)]
        assert all(variance > 0 for variance in variances)
        assert report["result"]["estimate"] == pytest.approx(means[0] - means[1])
        assert report["result"]["standard_error"] == pytest.approx(math.sqrt(sum(variance / 2 for variance in variances)))
        assert report["result"]["interval"]["lower"] is not None
        assert report["result"]["interval"]["upper"] is not None
        retained = _rows(contents["analysis_observations.jsonl"])
        assert {row["id"] for row in retained} == {row["id"] for row in observations}
        linked = posted(f.http, f.prefix + study_path + "/analysis-results", json={
            "run_id": analysis["id"], "idempotency_key": "retained-reset-episode-analysis",
            "notes": "Software workflow on authored simulated episodes; no physical qualification."})
        assert linked["kind"] == "analysis_result" and linked["run_references"][0]["id"] == analysis["id"]
        assert linked["data"]["analysis"]["counts"]["group_a"] == 2
        assert linked["data"]["analysis"]["counts"]["group_b"] == 2
        assert f.http.get(f.restored + study_path + "/records").status_code == 404
    finally:
        runner.client.close()
