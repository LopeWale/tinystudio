from tinystudio.lab.contracts import ExperimentDefinition
from tinystudio.lab.preflight import execution_checks


def body(**changes):
    return ExperimentDefinition(name="fixture", engine="synthetic.signal", execution={"profile": "trusted_native"}, **changes)


def test_engine_available_elsewhere_does_not_authorize_selected_backend():
    caps = {"engines": ["synthetic.signal"], "engines_by_backend": {"native": [], "container": ["synthetic.signal"]},
            "runtime": {"python": "3.12"}, "profiles_by_backend": {"native": ["trusted_native"]}}
    checks = {c["code"]: c for c in execution_checks(body(), caps)}
    assert checks["engine"]["passed"] is False
    assert checks["execution_profile"]["passed"] is True
    del caps["engines_by_backend"]["native"]
    checks = {c["code"]: c for c in execution_checks(body(), caps)}
    assert checks["engine"]["passed"] is False


def test_legacy_native_manifest_is_not_a_container_capability():
    definition = body()
    caps = {"engines": ["synthetic.signal"], "runtime": {"python": "3.12"}}
    assert all(c["passed"] for c in execution_checks(definition, caps))
    definition.execution.backend = "container"
    checks = {c["code"]: c for c in execution_checks(definition, caps)}
    assert not checks["engine"]["passed"]
    assert not checks["runtime"]["passed"]


def test_physical_and_acquisition_modes_do_not_silently_run_synthetic():
    caps = {"engines": ["synthetic.signal"], "runtime": {"python": "3.12"}}
    for mode, code in (("physical_control", "physical_control"), ("acquisition", "acquisition_adapter"), ("shadow", "acquisition_adapter")):
        checks = {c["code"]: c for c in execution_checks(body(operating_mode=mode), caps)}
        assert checks[code]["passed"] is False


def test_requested_limits_narrow_operator_allowance_without_silent_fallback():
    import pytest
    from tinystudio.lab.contracts import ExecutionProfile
    from tinystudio.lab.preflight import resource_selection

    policy = ExecutionProfile(profile="isolated", backend="container", requirements={"cpu": .5, "memory_bytes": 32*1024**2, "pids": 8})
    limits = resource_selection(policy, {"cpu": 2, "memory_bytes": 512*1024**2, "pids": 64})
    assert limits == {"cpu": .5, "memory_bytes": 33554432, "pids": 8}
    for requirements in ({"cpu": 3}, {"cpu": True}, {"unrecognized": 1}, {"memory_bytes": 1}):
        policy.requirements = requirements
        with pytest.raises(ValueError):
            resource_selection(policy, {"cpu": 2})
    policy.requirements = {"pids": 8}
    policy.backend = "kubernetes"
    with pytest.raises(ValueError, match="attested"):
        resource_selection(policy, {}, kubernetes_pids_limit=16)
    assert resource_selection(policy, {}, kubernetes_pids_limit=8)["pids"] == 8
    policy.backend = "native"
    with pytest.raises(ValueError, match="container"):
        resource_selection(policy, {})


def test_runner_places_selected_limits_in_actual_container_launch_arguments(tmp_path):
    from types import SimpleNamespace
    from tinystudio.lab.runner import Runner
    from tinystudio.lab.contracts import ExecutionProfile

    for name in ("source", "inputs", "work"):
        (tmp_path / name).mkdir()
    policy = ExecutionProfile(profile="isolated", backend="container",
                              requirements={"cpu": .5, "memory_bytes": 32*1024**2, "pids": 8})
    spec = {"definition": {"execution": policy.model_dump()}, "runtime": {"os": "Linux"}}
    plan = Runner._profile(None, spec, {"attempt_id": "limited"}, tmp_path,
        SimpleNamespace(root=tmp_path / "source"), {"image": "fixture@sha256:"+"a"*64}, ["python", "-V"])
    argv = plan.argv
    assert argv[argv.index("--cpus")+1] == "0.5"
    assert argv[argv.index("--memory")+1] == "33554432"
    assert argv[argv.index("--pids-limit")+1] == "8"


def test_runner_places_selected_limits_in_kubernetes_job_resources(tmp_path):
    import json
    from types import SimpleNamespace
    from tinystudio.lab.runner import Runner
    from tinystudio.lab.contracts import ExecutionProfile

    for name in ("source", "inputs", "work"):
        (tmp_path / name).mkdir()
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("fixture identity only")
    policy = ExecutionProfile(profile="isolated", backend="kubernetes", requirements={"cpu": .5, "memory_bytes": 32*1024**2, "pids": 8})
    spec = {"definition": {"execution": policy.model_dump()}, "runtime": {"os": "Linux"}}
    config = {"image": "fixture@sha256:"+"a"*64, "kube_context": "qualification", "kubeconfig": str(kubeconfig),
              "storage_claim": "qualification", "grants": {"network_policy_enforced": True, "kubernetes_pids_limit": 8}}
    plan = Runner._profile(None, spec, {"attempt_id": "limited"}, tmp_path, SimpleNamespace(root=tmp_path / "source"), config, ["python", "-V"])
    job = next(item for item in json.loads(plan.manifests_json)["items"] if item["kind"] == "Job")
    resources = job["spec"]["template"]["spec"]["containers"][0]["resources"]
    assert resources["limits"] == {"cpu": "0.5", "memory": "33554432"}
    assert resources["requests"] == resources["limits"]
