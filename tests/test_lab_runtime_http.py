"""Authenticated selected runtime probe through its real native attempt."""
import hashlib
import json

import pytest

from tests.test_lab_native_workflow import native_http, posted, enroll
from tests.test_lab_analysis_http import wait_advertisement
from tinystudio.training.runtime_compatibility import RuntimeCompatibility


@pytest.mark.skipif(not RuntimeCompatibility.inspect("cpu", "float32").eligible,
                    reason="optional CPU QLoRA prerequisites absent; no runtime qualification claimed")
@pytest.mark.parametrize("objective", ["sft", "causal_lm", "dpo"])
def test_selected_runtime_qualification_has_verified_authentic_native_measurement(native_http, objective):
    f = native_http
    methods = f.http.get(f.prefix+"/runtime/methods")
    assert methods.status_code == 200
    assert methods.json()["methods"][0]["id"] == "nf4.training"
    definition = {"name": "Measured native "+objective, "engine": "runtime.qualify", "domain": "runtime", "seed": 31,
        "parameters": {"method": "nf4.training", "objective": objective, "device": "cpu", "dtype": "float32",
                       "fixture": "tiny_llama", "nf4_execution": {"cpu_inference": "portable"}},
        "execution": {"profile": "trusted_native", "backend": "native", "max_seconds": 60}}
    saved = posted(f.http, f.prefix+"/definitions", json={"definition": definition})
    target, runner = enroll(f, "nf4-"+objective)
    try:
        wait_advertisement(f, target)
        check = posted(f.http, f.prefix+"/preflight", json={"definition": saved["definition"], "target_id": target["id"]})
        assert check["compatible"], check
        assert check["qualification"] == "unqualified"
        run = posted(f.http, f.prefix+"/runs", json={"definition_id": saved["id"], "revision": saved["revision"],
            "target_id": target["id"], "idempotency_key": "measure-"+objective})
        assert runner.run_once(run_id=run["id"])
    finally:
        runner.client.close()
    completed = f.http.get(f.prefix+"/runs/"+run["id"]).json()
    assert completed["state"] == "succeeded", completed.get("error") or completed
    artifact = next(row for row in completed["result"]["artifacts"] if row["role"] == "runtime_probe")
    response = f.http.get(f.prefix+"/artifacts/"+artifact["id"])
    assert hashlib.sha256(response.content).hexdigest() == artifact["sha256"]
    report = json.loads(response.content)
    assert report["format"] == "tinystudio.runtime-probe.v1" and report["status"] == "verified", report
    assert report["qualification"]["qualified"] is True
    scope = report["qualification"]["scope"]
    assert scope["objective"] == objective and scope["device"] == "cpu" and scope["dtype"] == "float32"
    assert scope["nf4_execution"] == {"cpu_inference": "portable"}
    assert scope["generalizes_to_other_models"] is False
    assert report["evidence"]["optimization"]["adapter_changed"] is True
    assert report["evidence"]["optimization"]["frozen_base_verified"] is True
    assert report["evidence"]["runtime_verification"]["status"] == "passed"
    assert all(row["status"] == "completed" for row in report["stages"])
    assert f.http.get(f.restored+"/runs/"+run["id"]).status_code == 404
