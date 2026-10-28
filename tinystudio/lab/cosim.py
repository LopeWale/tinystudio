"""Deterministic fixed-step composition of versioned built-in simulation components."""
from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
import hashlib
import heapq
import json
import math
from pathlib import Path
import random

from .engine import ExecutionCancelled, digest
from .signals import integer, number

COMPONENT_VERSION = "1"
KINDS = {"signal", "plant", "controller", "transport", "sensor"}
ALIASES = {"synthetic.signal": "signal", "simulation.plant": "plant", "controller.proportional": "controller",
           "transport.delay": "transport", "sensor.noise": "sensor"}


def _graph(definition):
    clocks = definition.get("clocks", {})
    dt = number(clocks.get("step", .1), "clock step", minimum=1e-9)
    tolerance = number(clocks.get("tolerance", 1e-9), "clock tolerance", minimum=0)
    if tolerance > dt * .01:
        raise ValueError("clock tolerance must be <= 1% of the master step")
    if clocks.get("hold", "zero_order") != "zero_order":
        raise ValueError("only explicit zero_order hold is supported")
    steps = integer(clocks.get("steps", 100), "steps", 1, 100000)
    components = {}
    for supplied in definition.get("components", []):
        component = deepcopy(supplied)
        component_id = component.get("id")
        if not isinstance(component_id, str) or not component_id or component_id in components:
            raise ValueError("components require unique nonempty string ids")
        kind = ALIASES.get(component.get("kind"), component.get("kind"))
        if kind not in KINDS:
            raise ValueError(f"unsupported component kind: {kind}")
        if str(component.get("version", COMPONENT_VERSION)) != COMPONENT_VERSION:
            raise ValueError("unsupported component version")
        p = component.setdefault("parameters", {})
        period = number(component.get("period", dt), "component period", minimum=dt)
        ratio = round(period / dt)
        if abs(period - ratio * dt) > tolerance:
            raise ValueError("component periods must be integer multiples of the master step")
        component.update(kind=kind, version=COMPONENT_VERSION, ticks=ratio, period=period)
        if kind == "transport":
            p["delay_steps"] = integer(p.get("delay_steps", 1), "delay_steps", 0, 100000)
        components[component_id] = component
    if not components or len(components) > 1000 or len(components) * steps > 1_000_000:
        raise ValueError("composition requires components and at most 1000000 component steps")
    inbound = defaultdict(dict)
    outgoing = defaultdict(list)
    degree = {key: 0 for key in components}
    for edge in definition.get("connections", []):
        if str(edge.get("version", COMPONENT_VERSION)) != COMPONENT_VERSION:
            raise ValueError("unsupported connection version")
        if edge.get("source_port", "output") != "output" or any(
                edge.get(key) for key in ("parameters", "transform", "solver", "delay")):
            raise ValueError("connections are scalar wires; use an explicit component for transforms or delays")
        source, target = edge.get("source"), edge.get("target")
        port = edge.get("port", edge.get("target_port", "input"))
        if source not in components or target not in components:
            raise ValueError("connection endpoint missing")
        if port in inbound[target]:
            raise ValueError("each target input port accepts one source")
        inbound[target][port] = source
        target_component = components[target]
        delayed = target_component["kind"] == "transport" and target_component["parameters"]["delay_steps"] > 0
        if target_component["kind"] != "plant" and not delayed:
            degree[target] += 1
            outgoing[source].append(target)
    for key, component in components.items():
        ports = set(inbound[key])
        allowed = {"reference", "measurement"} if component["kind"] == "controller" else {"input"}
        if ports - allowed or (component["kind"] == "signal" and ports):
            raise ValueError(f"unsupported input ports for {key}")
        required = {"measurement"} if component["kind"] == "controller" else {"input"} if component["kind"] in {"sensor", "transport", "plant"} else set()
        if not required <= ports:
            raise ValueError(f"missing required input ports for {key}: {sorted(required - ports)}")
    ready = [key for key, count in degree.items() if count == 0]
    heapq.heapify(ready)
    order = []
    while ready:
        key = heapq.heappop(ready)
        order.append(key)
        for target in outgoing[key]:
            degree[target] -= 1
            if degree[target] == 0:
                heapq.heappush(ready, target)
    if len(order) != len(components):
        raise ValueError("algebraic loop requires an explicit positive delay or dynamic plant; no implicit solver is supplied")
    return components, inbound, order, dt, steps, tolerance


def _branch(definition, branch_id, interventions, cancelled):
    branch = deepcopy(definition)
    by_id = {component["id"]: component for component in branch["components"]}
    for intervention in interventions:
        target = intervention.get("component_id")
        if target not in by_id or not isinstance(intervention.get("parameters"), dict):
            raise ValueError("interventions require a known component_id and parameter map")
        by_id[target].setdefault("parameters", {}).update(intervention["parameters"])
    components, inbound, order, dt, steps, tolerance = _graph(branch)
    seed = integer(branch.get("seed", 0), "seed", 0, 2**53)
    rngs = {key: random.Random(int.from_bytes(hashlib.sha256(f"{seed}:{key}".encode()).digest()[:8], "big")) for key in components}
    state = {key: number(component["parameters"].get("initial_state", 0), "initial_state")
             for key, component in components.items() if component["kind"] == "plant"}
    outputs = {}
    queues = defaultdict(deque)
    sent = {}
    transport_last = {}
    state_origins = {}
    for tick in range(steps):
        if cancelled():
            raise ExecutionCancelled("co-simulation cancelled")
        now = tick * dt
        for key in order:
            component = components[key]
            p, kind = component["parameters"], component["kind"]
            if tick % component["ticks"]:
                continue
            # Plant output is the current integrated state; its input is consumed after the decision phase.
            inputs = {} if kind == "plant" else {port: outputs[source] for port, source in sorted(inbound[key].items()) if source in outputs}
            if kind not in {"signal", "plant"}:
                missing = set(inbound[key]) - inputs.keys()
                if missing and not (kind == "transport" and p["delay_steps"] > 0):
                    raise ValueError(f"component {key} has no initial visible input: {sorted(missing)}")
            visible = {port: {"record_id": row["record_id"], "value": row["value"], "time": row["time"],
                              "available_at": row["available_at"], "source_time": row.get("source_time", row["time"])}
                       for port, row in inputs.items()}
            used = list(inputs.values())
            if kind == "signal":
                waveform = p.get("waveform", "constant")
                if waveform not in {"constant", "sine", "step"}:
                    raise ValueError("unsupported co-simulation signal waveform")
                amplitude = number(p.get("amplitude", 1), "amplitude")
                offset = number(p.get("offset", 0), "offset")
                shape = (math.sin(2 * math.pi * number(p.get("frequency", 1), "frequency", minimum=0) * now)
                         if waveform == "sine" else (1 if now >= number(p.get("step_time", 0), "step_time", minimum=0) else 0)
                         if waveform == "step" else 1)
                value = offset + amplitude * shape
            elif kind == "plant":
                value = state[key]
                used = [state_origins[key]] if key in state_origins else []
                visible = {"integrated_state": {field: state_origins[key][field]
                           for field in ("record_id", "value", "time", "available_at")}} if used else {}
            elif kind == "controller":
                reference = inputs.get("reference", {}).get("value", number(p.get("setpoint", 1), "setpoint"))
                value = number(p.get("gain", 1), "controller gain") * (reference - inputs["measurement"]["value"])
                low = number(p.get("minimum", -1e100), "controller minimum")
                high = number(p.get("maximum", 1e100), "controller maximum")
                if low > high:
                    raise ValueError("controller minimum exceeds maximum")
                value = min(high, max(low, value))
            elif kind == "sensor":
                value = inputs["input"]["value"] + number(p.get("bias", 0), "sensor bias")
                value += rngs[key].gauss(0, number(p.get("noise_std", 0), "noise_std", minimum=0))
            else:
                if p["delay_steps"] == 0:
                    chosen = inputs["input"]
                else:
                    while queues[key] and queues[key][0][0] <= tick:
                        transport_last[key] = queues[key].popleft()[1]
                    chosen = transport_last.get(key)
                freeze = number(p.get("freeze_after", 1e100), "freeze_after", minimum=0)
                if now >= freeze and key in outputs:
                    continue  # Retain original source and availability stamps so staleness is visible.
                value = chosen["value"] if chosen else number(p.get("initial_value", 0), "initial_value")
                used = [chosen] if chosen else []
                visible = {"input": {name: chosen.get(name) for name in ("record_id", "value", "time", "available_at")}} if chosen else {}
            if not math.isfinite(value):
                raise ValueError("co-simulation produced nonfinite output")
            if any(row["available_at"] > now + tolerance for row in used):
                raise ValueError("component attempted to consume a future observation")
            row = {"record_id": f"{branch_id}:{key}:{tick}", "episode_id": branch_id, "branch_id": branch_id,
                   "component_id": key, "component_kind": kind, "component_version": COMPONENT_VERSION,
                   "time": now, "available_at": now, "component_time": now, "value": value,
                   "units": str(p.get("units", "unitless")), "observed": True, "quality": "simulated",
                   "inputs_visible_before_decision": visible, "source_record_ids": [r["record_id"] for r in used],
                   "source_time": min((r.get("source_time", r["time"]) for r in used), default=now),
                   "interventions": interventions}
            outputs[key] = row
            yield row
        # Transport receives newly produced messages only after this tick's decisions.
        for key, component in sorted(components.items()):
            if component["kind"] == "transport" and component["parameters"]["delay_steps"] > 0:
                source = outputs.get(inbound[key]["input"])
                if source and sent.get(key) != source["record_id"]:
                    queues[key].append((tick + component["parameters"]["delay_steps"], source))
                    sent[key] = source["record_id"]
            elif component["kind"] == "plant":
                source = outputs.get(inbound[key]["input"])
                if source is None:
                    raise ValueError(f"plant {key} has no control input")
                p = component["parameters"]
                tau = number(p.get("time_constant", 1), "time_constant", minimum=1e-9)
                gain = number(p.get("gain", 1), "plant gain")
                equilibrium = gain * source["value"] + number(p.get("disturbance", 0), "plant disturbance")
                state[key] = equilibrium + (state[key] - equilibrium) * math.exp(-dt / tau)
                # The integration receipt preserves the action that produced the next physical state.
                receipt = {"record_id": f"{branch_id}:{key}:integration:{tick}", "episode_id": branch_id,
                       "branch_id": branch_id, "component_id": key, "component_kind": "plant_integration",
                       "time": now + dt, "available_at": now + dt, "component_time": now + dt,
                       "value": state[key], "units": str(p.get("units", "unitless")), "observed": True,
                       "quality": "simulated", "source_record_ids": [source["record_id"]],
                       "inputs_visible_before_decision": {"input": {field: source[field] for field in ("record_id", "value", "time", "available_at")}},
                       "interventions": interventions, "component_version": COMPONENT_VERSION}
                state_origins[key] = receipt
                yield receipt


def execute_cosim(spec, output_dir, emit, cancelled):
    definition = deepcopy(spec["definition"])
    if definition.get("engine") != "system.cosim":
        raise ValueError("co-simulation requires engine system.cosim")
    if definition.get("mode", "offline") != "offline":
        raise ValueError("fixed-step co-simulation currently supports offline mode only")
    if definition.get("operating_mode", "simulation") != "simulation":
        raise ValueError("co-simulation has no hardware or acquisition gateway")
    if definition.get("inputs") or definition.get("pipeline"):
        raise ValueError("co-simulation currently supports built-in graph components, not external scripts or pipelines")
    resolved_components, _, _, dt, steps, tolerance = _graph(definition)
    definition["components"] = [{key: value for key, value in component.items() if key != "ticks"}
                                for _, component in sorted(resolved_components.items())]
    definition["clocks"] = {"step": dt, "steps": steps, "hold": "zero_order", "tolerance": tolerance}
    definition["connections"] = [{**edge, "version": COMPONENT_VERSION,
                                  "port": edge.get("port", edge.get("target_port", "input"))}
                                 for edge in definition.get("connections", [])]
    branches = [{"id": "baseline", "interventions": []}, *definition.get("scenarios", [])]
    ids = [item.get("id") for item in branches]
    if any(not isinstance(key, str) or not key for key in ids) or len(ids) != len(set(ids)) or len(ids) > 64:
        raise ValueError("branch ids must be unique nonempty strings, with at most 64 branches")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise ValueError("output directory must be empty")
    frozen = output_dir / "composition.json"
    frozen.write_text(json.dumps(definition, sort_keys=True, allow_nan=False))
    path = output_dir / "processed.jsonl"
    count = 0
    with path.open("w") as stream:
        for branch in branches:
            for row in _branch(definition, branch["id"], branch.get("interventions", []), cancelled):
                count += 1
                if count > 1_000_000:
                    raise ValueError("co-simulation output exceeds 1000000 records")
                stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
                if count % 256 == 0:
                    emit({"event": "progress", "records": count, "branch": branch["id"]})
    emit({"event": "progress", "records": count, "branches": len(branches)})
    return {"records": count, "artifacts": [{"name": item.name, "path": str(item), "sha256": digest(item),
             "media_type": "application/x-ndjson" if item == path else "application/json",
             "role": "derived" if item == path else "configuration"} for item in (frozen, path)],
            "metrics": {"branches": len(branches), "records": count},
            "qualification": {"engine": "system.cosim", "component_version": COMPONENT_VERSION,
                              "source_version": "sha256:" + digest(Path(__file__)), "physical_validation": False,
                              "clock": {"step": dt, "steps": steps, "hold": "zero_order", "tolerance": tolerance,
                                        "tolerance_scope": "clock_alignment_and_availability"}}}
