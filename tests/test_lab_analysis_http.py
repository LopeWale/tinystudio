"""Authenticated study snapshot -> real runner inference -> retained study result."""
import hashlib
import json
import time

from tests.test_lab_native_workflow import native_http, enroll, posted
from tests.test_lab_studies import protocol, record
from tests.test_lab_analysis_selection import parameters
from tinystudio.lab import operations
from tinystudio.lab.runner import Runner
from tests.test_lab_analysis_engine import custom_params


def wait_advertisement(fixture, target):
    # The fixture's request-scoped transaction finishes after response delivery.
    # Wait for the retained declaration, using the same user-visible read path.
    deadline = time.monotonic()+5
    while True:
        rows = fixture.http.get(fixture.prefix+"/targets").json()
        if any(row["id"] == target["id"] and row.get("capabilities") for row in rows):
            return
        if time.monotonic() >= deadline:
            raise AssertionError("runner advertisement was not retained")
        time.sleep(.01)


def test_registered_analysis_workflow_freezes_curated_observations_and_historical_result(native_http):
    f = native_http
    study = posted(f.http, f.prefix+"/studies", json={"study": protocol(), "idempotency_key": "analysis-study"})
    endpoint = f.prefix+"/studies/"+study["id"]
    for index, (unit, condition, value) in enumerate((("a1", "A", 1), ("a2", "A", 3), ("b1", "B", 2), ("b2", "B", 4))):
        posted(f.http, endpoint+"/records", json=record(key="observation-"+str(index), unit_id=unit, condition=condition,
            data={"response": value, "source": {"kind": "emulated", "emulator": "sil", "seed": 7}}))
    capture = posted(f.http, endpoint+"/analysis-inputs", json={"revision": 1, "through_seq": 4, "idempotency_key": "analysis-input"})
    operations.run_operation(capture["id"], f.factory)
    operation = f.http.get(f.prefix+"/operations/"+capture["id"]).json()
    assert operation["state"] == "succeeded", operation
    snapshot = operation["result"]["artifact"]
    methods = f.http.get(f.prefix+"/statistics/analysis-methods")
    assert methods.status_code == 200
    assert {row["id"] for row in methods.json()["methods"]} >= {"means.welch", "means.paired", "custom.python"}
    saved = posted(f.http, f.prefix+"/statistics/analyses", json={"name": "Observed emulated contrast", "snapshot_artifact_id": snapshot["id"],
        "parameters": parameters(groups=["A", "B"]), "execution": {"profile": "trusted_native", "backend": "native", "max_seconds": 30}})
    target, runner = enroll(f, "observed-analysis")
    try:
        wait_advertisement(f, target)
        checked = f.http.post(f.prefix+"/preflight", json={"definition": saved["definition"], "target_id": target["id"]})
        assert checked.status_code == 200 and checked.json()["compatible"], checked.text
        run = posted(f.http, f.prefix+"/runs", json={"definition_id": saved["id"], "revision": saved["revision"],
            "target_id": target["id"], "idempotency_key": "observed-analysis"})
        assert runner.run_once(run_id=run["id"])
    finally:
        runner.client.close()
    complete = f.http.get(f.prefix+"/runs/"+run["id"]).json()
    assert complete["state"] == "succeeded", complete
    artifact = next(row for row in complete["result"]["artifacts"] if row["role"] == "statistical_analysis")
    response = f.http.get(f.prefix+"/artifacts/"+artifact["id"])
    assert hashlib.sha256(response.content).hexdigest() == artifact["sha256"]
    report = json.loads(response.content)
    assert report["result"]["estimate"] == -1
    assert report["source"]["sha256"] == snapshot["sha256"]
    assert report["source"]["through_seq"] == 4
    assert report["result"]["counts"] == {"group_a": 2, "group_b": 2}
    changed = f.http.put(endpoint, json={"study": protocol(name="Later protocol"), "expected_revision": 1})
    assert changed.status_code == 200
    linked = posted(f.http, endpoint+"/analysis-results", json={"run_id": run["id"], "idempotency_key": "record-result", "notes": "Frozen first analysis"})
    assert linked["kind"] == "analysis_result" and linked["revision"] == 1
    assert linked["run_references"][0]["id"] == run["id"]
    assert posted(f.http, endpoint+"/analysis-results", json={"run_id": run["id"], "idempotency_key": "record-result", "notes": "Frozen first analysis"}) == linked
    assert f.http.get(f.restored+"/studies/"+study["id"]+"/manifest").status_code == 404


def test_custom_method_runs_only_after_operator_grant_with_retained_code_auxiliary_and_output(native_http):
    f = native_http
    study = posted(f.http, f.prefix+"/studies", json={"study": protocol(), "idempotency_key": "custom-study"})
    endpoint = f.prefix+"/studies/"+study["id"]
    posted(f.http, endpoint+"/records", json=record(key="observation", data={"response": "1.25"}))
    capture = posted(f.http, endpoint+"/analysis-inputs", json={"idempotency_key": "custom-input"})
    operations.run_operation(capture["id"], f.factory)
    operation = f.http.get(f.prefix+"/operations/"+capture["id"]).json()
    assert operation["state"] == "succeeded", operation
    code_bytes = b'''import decimal,json,pathlib,sys
header=json.loads(sys.stdin.readline())
records=[json.loads(line)["record"] for line in sys.stdin]
scale=decimal.Decimal(pathlib.Path(header["inputs"][0]["path"]).read_text())
value=sum(decimal.Decimal(row["data"]["response"]) for row in records)*scale
pathlib.Path("exact.txt").write_text(str(value))
print(json.dumps({"exact_sum":str(value),"artifacts":[{"path":"exact.txt","media_type":"text/plain"}]}))
'''
    code = posted(f.http, f.prefix+"/inputs", files={"file": ("method.py", code_bytes, "text/x-python")})
    auxiliary = posted(f.http, f.prefix+"/inputs", files={"file": ("scale.dat", b"2", "application/octet-stream")})
    saved = posted(f.http, f.prefix+"/statistics/analyses", json={"name": "Exact decimal custom method",
        "snapshot_artifact_id": operation["result"]["artifact"]["id"], "custom_code_artifact_id": code["id"],
        "extra_artifact_ids": [auxiliary["id"]], "parameters": custom_params(),
        "execution": {"profile": "trusted_native", "backend": "native", "max_seconds": 30}})
    target, denied = enroll(f, "custom-analysis")
    try:
        wait_advertisement(f, target)
        checked = posted(f.http, f.prefix+"/preflight", json={"definition": saved["definition"], "target_id": target["id"]})
        assert not checked["compatible"]
    finally:
        denied.client.close()
    runner = Runner(f.url, target["id"], target["token"], heartbeat_interval=.1,
        profile_config={"schema_version": 1, "backends": {}, "engine_grants": {"statistics.analyze": {"native_execution": True}}})
    try:
        runner.advertise()
        checked = posted(f.http, f.prefix+"/preflight", json={"definition": saved["definition"], "target_id": target["id"]})
        assert checked["compatible"], checked
        run = posted(f.http, f.prefix+"/runs", json={"definition_id": saved["id"], "revision": saved["revision"],
            "target_id": target["id"], "idempotency_key": "custom-analysis"})
        assert runner.run_once(run_id=run["id"])
    finally:
        runner.client.close()
    complete = f.http.get(f.prefix+"/runs/"+run["id"]).json()
    assert complete["state"] == "succeeded", complete
    artifacts = {row["role"]: row for row in complete["result"]["artifacts"]}
    response = f.http.get(f.prefix+"/artifacts/"+artifacts["statistical_analysis"]["id"])
    report = response.json()
    assert hashlib.sha256(response.content).hexdigest() == artifacts["statistical_analysis"]["sha256"]
    assert report["result"]["custom_output"]["exact_sum"] == "2.50"
    assert report["custom_execution"]["code_sha256"] == code["sha256"]
    assert report["custom_execution"]["auxiliary_inputs"][0]["sha256"] == auxiliary["sha256"]
    assert f.http.get(f.prefix+"/artifacts/"+artifacts["analysis_custom"]["id"]).text == "2.50"
    linked = posted(f.http, endpoint+"/analysis-results", json={"run_id": run["id"], "idempotency_key": "custom-result"})
    assert linked["data"]["analysis"]["method"]["id"] == "custom.python"
