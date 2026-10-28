"""Deterministic bounded sources. Time is simulated seconds, never wall clock."""
from __future__ import annotations

import math
import random
from collections.abc import Iterator
from typing import Any


def number(value: Any, name: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise ValueError(f"{name} must be finite and >= {minimum}")
    return result


def integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    result = number(value, name)
    if not result.is_integer() or not minimum <= result <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
    return int(result)


def generate(engine: str, parameters: dict, seed: int) -> Iterator[dict]:
    count = integer(parameters.get("samples", 1000), "samples", 1, 1_000_000)
    episodes = integer(parameters.get("episodes", 1), "episodes", 1, 10000)
    if count * episodes > 1_000_000:
        raise ValueError("total samples exceed 1000000")
    dt = number(parameters.get("dt", 0.01), "dt", minimum=1e-9)
    amplitude = number(parameters.get("amplitude", 1), "amplitude")
    frequency = number(parameters.get("frequency", 1), "frequency", minimum=0)
    offset = number(parameters.get("offset", 0), "offset")
    phase = number(parameters.get("phase", 0), "phase")
    noise = number(parameters.get("noise", 0), "noise", minimum=0)
    missing = number(parameters.get("missing_probability", 0), "missing_probability", minimum=0)
    if missing > 1:
        raise ValueError("missing_probability must be <= 1")
    waveform = parameters.get("waveform", "sine")
    if waveform not in {"sine", "square", "sawtooth", "constant"}:
        raise ValueError(f"unsupported waveform: {waveform}")
    tau = number(parameters.get("time_constant", 1), "time_constant", minimum=1e-9)
    gain = number(parameters.get("plant_gain", 1), "plant_gain")
    kp = number(parameters.get("controller_gain", 1), "controller_gain")
    target = number(parameters.get("setpoint", 1), "setpoint")
    rng = random.Random(seed)
    for episode in range(episodes):
        state = number(parameters.get("initial_state", 0), "initial_state")
        for index in range(count):
            time = index * dt
            if engine == "simulation.plant":
                # Zero-order-held proportional controller, exact first-order integration.
                control = kp * (target - state)
                value = state
                state = gain * control + (state - gain * control) * math.exp(-dt / tau)
            else:
                angle = 2 * math.pi * frequency * time + phase
                shape = {"sine": lambda: math.sin(angle), "square": lambda: 1 if math.sin(angle) >= 0 else -1,
                         "sawtooth": lambda: 2 * ((angle / (2 * math.pi)) % 1) - 1,
                         "constant": lambda: 1}[waveform]()
                value = offset + amplitude * shape
                control = None
            value += rng.gauss(0, noise) if noise else 0
            observed = rng.random() >= missing
            record = {"record_id": f"e{episode}:r{index}", "episode_id": f"e{episode}",
                      "time": time, "available_at": time, "value": value if observed else None,
                      "units": str(parameters.get("units", "unitless")), "observed": observed,
                      "quality": "good" if observed else "missing"}
            if engine == "simulation.plant":
                record.update(control=control, setpoint=target)
            yield record
