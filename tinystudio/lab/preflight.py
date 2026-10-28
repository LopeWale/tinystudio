"""Pure capability checks reused before submission and when leasing an attempt.

Availability is a target declaration, not a successful execution receipt.
"""
from __future__ import annotations

from .contracts import ExperimentDefinition


def backend_engines(capabilities: dict, backend: str) -> list[str]:
    if "engines_by_backend" in capabilities:
        mapping = capabilities["engines_by_backend"]
        return mapping.get(backend, []) if isinstance(mapping, dict) else []
    return capabilities.get("engines", []) if backend == "native" else []


def execution_checks(definition: ExperimentDefinition, capabilities: dict) -> list[dict]:
    execution = definition.execution
    backend, profile, engine = execution.backend, execution.profile, definition.engine
    checks = []

    def check(code, passed, message):
        checks.append({"code": code, "passed": bool(passed), "message": message})

    profiles = capabilities.get("profiles_by_backend", {"native": ["trusted_native"]})
    check("execution_profile", profile in profiles.get(backend, []),
          f"Selected target must advertise {backend}/{profile}; configure the runner before submission.")
    engines = backend_engines(capabilities, backend)
    check("engine", engine in engines,
          f"Selected target must advertise {engine} on {backend}; install its optional dependency pack or select another target.")
    runtime = capabilities.get("runtime_by_backend", {}).get(backend)
    if runtime is None and backend == "native":
        runtime = capabilities.get("runtime")
    check("runtime", bool(runtime), "Start the selected runner to publish its versioned runtime.")
    check("machine_authority", not execution.grants,
          "Machine grants belong in the runner operator configuration, never in portable definitions.")
    check("physical_control", definition.operating_mode != "physical_control",
          "Physical control requires run-to-gateway binding and qualification; this integration is not implemented.")
    check("coordinator", not (definition.components or definition.connections) or engine == "system.cosim",
          "Connected components require the system.cosim clock coordinator.")
    if engine == "learning.language":
        from .learning_axes import selector
        from .learning_initialization import validate_initialization_bindings
        learning, parameters = definition.learning or {}, definition.parameters
        try:
            initialization = selector(learning.get("initialization", parameters.get("initialization", "scratch")), "initialization")
            if ("initialization" in learning and "initialization" in parameters
                and initialization != selector(parameters["initialization"], "initialization")):
                raise ValueError("conflicting learning.initialization and parameters.initialization")
            validate_initialization_bindings(definition.model_dump(), initialization)
        except (ValueError, KeyError, TypeError) as exc:
            check("model_initialization", False, str(exc))
        else:
            check("model_initialization", True,
                  "Initialization bindings are declared consistently; checkpoint contents, architecture and tokenizer compatibility are verified at execution.")
    if engine == "learning.pretrained":
        from .learning_axes import pretrained_method
        try:
            method = pretrained_method(definition.learning or {}, definition.parameters)
            if definition.retrieval:
                raise ValueError("pretrained training requires explicitly composed frozen retrieval; a retrieval selection cannot be silently ignored")
        except (ValueError, KeyError, TypeError) as exc:
            check("learning_method", False, str(exc))
        else:
            check("learning_method", True,
                f"{method.objective}/{method.update_strategy} uses {method.data_format} data; source contents, device placement and optional dependencies are checked at execution.")
    if engine == "data.pipeline" and definition.mode == "online":
        from .data import online_plan
        try:
            online_plan(definition.parameters, definition.pipeline)
        except (ValueError, KeyError, TypeError) as exc:
            check("data_pipeline", False, str(exc))
        else:
            check("data_pipeline", True,
                  "Observation-triggered availability replay; source clocks and recorded availability are validated at execution.")
    if definition.operating_mode in {"acquisition", "shadow"}:
        check("acquisition_adapter", engine.startswith("acquisition."),
              "Acquisition and shadow modes require an acquisition adapter with an explicit operator binding.")
    if execution.requirements:
        try:
            limits = capabilities.get("resource_limits_by_backend", {}).get(backend)
            if limits is None:
                raise ValueError("target has not advertised enforceable resource limits for this backend")
            resource_selection(execution, limits, kubernetes_pids_limit=capabilities.get("kubernetes_pids_limit"))
        except ValueError as exc:
            check("resource_limits", False, str(exc))
        else:
            check("resource_limits", True, "Requested limits fit the operator-configured target allowance.")
    return checks


def mismatch(definition: dict, capabilities: dict) -> str | None:
    checks = execution_checks(ExperimentDefinition.model_validate(definition), capabilities)
    return next((item["message"] for item in checks if not item["passed"]), None)


def resource_selection(execution, limits, *, kubernetes_pids_limit=None):
    """Requested limits may narrow, never enlarge, the operator's allowance."""
    from .profiles import Limits
    requested = execution.requirements
    allowed = {"cpu", "memory_bytes", "pids"}
    if set(requested) - allowed:
        raise ValueError("supported resource requirements are cpu, memory_bytes and pids")
    if requested and (execution.backend == "native" or execution.profile != "isolated"):
        raise ValueError("hard resource requirements need an isolated container or Kubernetes backend")
    maximum = {"cpu": limits.get("cpu", 1), "memory_bytes": limits.get("memory_bytes", 512*1024**2), "pids": limits.get("pids", 64)}
    selected = {**maximum, **requested}
    Limits(**maximum).validate()
    Limits(**selected, wall_seconds=execution.max_seconds).validate()
    if any(selected[key] > maximum[key] for key in allowed):
        raise ValueError("requested resources exceed the operator-configured target allowance")
    if execution.backend == "kubernetes" and "pids" in requested and (kubernetes_pids_limit is None or kubernetes_pids_limit > selected["pids"]):
        raise ValueError("requested Kubernetes PID limit requires an attested node policy at or below that limit")
    return selected
