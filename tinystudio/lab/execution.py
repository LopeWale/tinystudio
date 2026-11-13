"""Credential-free execution entrypoint, bound to a verified source snapshot."""
from __future__ import annotations

import json
import sys
from pathlib import Path, PurePosixPath

from .contracts import checksum


def verify_runtime(spec: dict, package_root: Path) -> dict:
    """Recheck actual executing source bytes and the interpreter's runtime identity."""
    import hashlib
    from .service import runtime_versions

    package_root = package_root.resolve()
    expected = spec["implementation"]
    files = expected.get("files", {})
    actual = {}
    for name, expected_digest in files.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name or relative.suffix != ".py":
            raise ValueError("unsafe implementation manifest path")
        path = package_root.joinpath(*relative.parts)
        if path.is_symlink() or not path.resolve().is_relative_to(package_root) or not path.is_file():
            raise ValueError("implementation snapshot file is missing or escapes its root")
        value = hashlib.sha256(path.read_bytes()).hexdigest()
        if value != expected_digest:
            raise ValueError("executing implementation checksum mismatch")
        actual[name] = value
    actual_paths = {path.relative_to(package_root).as_posix() for path in package_root.rglob("*.py")}
    if actual_paths != set(files):
        raise ValueError("implementation snapshot file set differs from its manifest")
    for name, module in list(sys.modules.items()):
        if name == "tinystudio" or name.startswith("tinystudio."):
            filename = getattr(module, "__file__", None)
            if filename and not Path(filename).resolve().is_relative_to(package_root):
                raise ValueError("loaded TinyStudio module escapes the implementation snapshot")
    runtime = runtime_versions()
    if runtime != spec["runtime"]:
        raise ValueError("executing runtime differs from the frozen dependency manifest")
    return {"format": "tinystudio.execution-receipt.v1", "spec_digest": checksum(spec),
            "implementation_digest": checksum({**expected, "files": actual}),
            "runtime": runtime, "source_root": str(package_root)}


def execute(spec, output_dir, emit, cancelled, *, context=None):
    # Engines receive an isolated execution view. Frozen caller data is never mutated.
    view = json.loads(json.dumps(spec, allow_nan=False))
    context = context or {}
    view["input_paths"] = dict(context.get("input_paths", {}))
    # Authored grants are never promoted to runner authority, including direct use.
    view["_runtime_grants"] = dict(context.get("_runtime_grants", {}))
    view["_runtime_secrets"] = dict(context.get("_runtime_secrets", {}))
    # Keep public execution provenance distinct from authority and secret data.
    profile = context.get("profile_manifest", {})
    profile = profile if isinstance(profile, dict) else {}
    view["_runtime_context"] = {"spec_digest": context.get("spec_digest"), "profile": {
        key: profile[key] for key in ("backend", "execution_backend", "profile", "image", "target_os",
            "limits", "qualification", "required_attestations", "network_default", "service_account_token") if key in profile}}
    engine = view["definition"]["engine"]
    if engine.startswith("learning."):
        visible = [ref for ref in view["definition"].get("inputs", []) if ref.get("visibility", "model") == "model"]
        validation = [ref for ref in view["definition"].get("inputs", []) if engine.startswith("learning.")
                      and ref.get("visibility") == "evaluation" and ref.get("role") == "validation"]
        view["evaluation_input_paths"] = {ref["artifact_id"]: view["input_paths"][ref["artifact_id"]] for ref in validation}
        view["definition"]["inputs"] = visible+validation
        view["input_paths"] = {ref["artifact_id"]: view["input_paths"][ref["artifact_id"]] for ref in visible}
    if engine in {"synthetic.signal", "recorded.replay", "simulation.plant"}:
        from .engine import execute as run
    elif engine == "learning.language":
        from .learning import execute_learning as run
    elif engine == "retrieval.index":
        from .retrieval import execute_retrieval as run
    elif engine == "learning.timeseries":
        from .timeseries import execute_timeseries as run
    elif engine == "learning.multimodal":
        from .timeseries import execute_multimodal as run
    elif engine in {"learning.control_rl", "learning.tool_rl"}:
        from .rl import execute_rl as run
    elif engine == "learning.language_rl":
        from .language_rl import execute_language_rl as run
    elif engine == "system.cosim":
        from .cosim import execute_cosim as run
    elif engine == "learning.pretrained":
        from .compatibility import execute_pretrained as run
    elif engine == "deployment.benchmark":
        from .deployment import execute_deployment as run
    elif engine == "evaluation.compare":
        from .research import execute_evaluation as run
    elif engine == "statistics.plan":
        from .statistics import execute_statistics as run
    elif engine == "statistics.analyze":
        from .analysis import execute_analysis as run
    elif engine == "runtime.qualify":
        from .runtime_probe import execute_runtime_probe as run
    elif engine == "hardware.discover":
        from .hardware_discovery_engine import execute_hardware_discovery as run
    elif engine == "data.pipeline":
        from .data import execute_data as run
    elif engine == "simulation.openmodelica":
        from .modelica import execute_modelica as run
    elif engine in {"acquisition.serial", "acquisition.modbus", "acquisition.mqtt", "acquisition.can"}:
        from .acquisition import execute_acquisition as run
    else:
        from .domain_adapters import CLI_TARGETS, PYTHON_TARGETS
        if (engine in CLI_TARGETS or engine in PYTHON_TARGETS) and (engine != "simulation.verilator" or view["definition"].get("inputs")):
            from .domain_adapters import execute_domain as run
        else:
            from .adapters import execute_adapter as run
    result = run(view, output_dir, emit, cancelled)
    if isinstance(result.get("records"), list):
        rows = result["records"]
        if not any(item["name"] == "processed.jsonl" for item in result["artifacts"]):
            from .engine import digest
            target = output_dir / "processed.jsonl"
            with target.open("w") as stream:
                for row in rows:
                    if cancelled():
                        from .engine import ExecutionCancelled
                        raise ExecutionCancelled("cancelled while publishing record evidence")
                    stream.write(json.dumps(row, allow_nan=False)+"\n")
            result["artifacts"].append({"name": target.name, "path": str(target), "sha256": digest(target),
                                        "media_type": "application/x-ndjson", "role": "derived_records"})
        result["record_count"] = len(rows)
        result["records"] = len(rows)
    if (engine in {"learning.timeseries", "learning.multimodal", "learning.control_rl", "learning.tool_rl", "learning.language_rl"} or
            engine == "evaluation.compare" and view["definition"].get("parameters", {}).get("operation") == "matrix"):
        # The common record sidecar is created after the executor returns. Keep
        # the aggregate last in the actual runner upload order as well.
        result["artifacts"].sort(key=lambda item: item.get("role") in {"independent_evaluation", "heldout_tool_evaluation"})
    if engine == "deployment.benchmark":
        result["deployment_tradeoffs"] = {"measurements": result["metrics"], "qualification": result["qualification"]}
    if spec["definition"].get("research_overrides"):
        result["qualification"] = {**result.get("qualification", {}), "research_only": True,
                                  "overrides": spec["definition"]["research_overrides"]}
    return result


def main():
    spec_path, output, context_path = (Path(arg) for arg in sys.argv[1:4])
    spec = json.loads(spec_path.read_text())
    context = json.loads(context_path.read_text())
    if checksum(spec) != context["spec_digest"]:
        raise ValueError("frozen specification checksum mismatch in child")
    package = Path(__file__).resolve().parents[1]
    if package != Path(context["source_root"]).resolve():
        raise ValueError("execution imported a different implementation root")
    receipt = verify_runtime(spec, package)
    receipt["context_digest"] = checksum(context)
    receipt["profile_manifest"] = context["profile_manifest"]
    output.mkdir()

    def emit(item):
        serialized = json.dumps(item, allow_nan=False)
        if len(serialized.encode()) > 65535:
            raise ValueError("executor event exceeds 64 KiB")
        with (output.parent / "events.jsonl").open("a") as stream:
            stream.write(serialized + "\n")

    evidence_exit = None
    try:
        result = execute(spec, output, emit, lambda: (output.parent / "cancel").exists(), context=context)
    except Exception as exc:
        # Keep successful execution compatible with minimal source fixtures that
        # have no cancellation class. Only the engine's explicit exception can
        # opt into this separate audit outcome; arbitrary partial files cannot.
        from . import engine
        if not isinstance(getattr(exc, "evidence_result", None), dict):
            raise
        from .execution_outcome import (CANCELLED_EVIDENCE_EXIT, FAILED_EVIDENCE_EXIT,
                                        RetainedExecutionError, cancelled_evidence_outcome,
                                        failed_evidence_outcome)
        if isinstance(exc, getattr(engine, "ExecutionCancelled", ())):
            outcome = cancelled_evidence_outcome()
            evidence_exit = CANCELLED_EVIDENCE_EXIT
        elif isinstance(exc, RetainedExecutionError):
            outcome = failed_evidence_outcome()
            evidence_exit = FAILED_EVIDENCE_EXIT
        else:
            raise
        result = json.loads(json.dumps(exc.evidence_result, allow_nan=False))
        result["execution_outcome"] = outcome
        if evidence_exit == FAILED_EVIDENCE_EXIT:
            result["execution_error"] = f"{type(exc).__name__}: {exc}"[:8192]
    final_receipt = verify_runtime(spec, package)
    if any(receipt[key] != final_receipt[key] for key in final_receipt):
        raise ValueError("runtime identity changed during execution")
    if evidence_exit is not None:
        receipt["outcome"] = outcome
        receipt["result_digest"] = checksum(result)
    (output.parent / "runtime-receipt.json").write_text(json.dumps(receipt, allow_nan=False))
    (output.parent / "result.json").write_text(json.dumps(result, allow_nan=False))
    if evidence_exit is not None:
        raise SystemExit(evidence_exit)


if __name__ == "__main__":
    main()
