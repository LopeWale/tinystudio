"""Generate the example dataset: natural-language smart-home requests -> strict JSON commands.

A deliberately narrow domain so a 135M-parameter model can learn it in a
couple of minutes on a laptop, and so the ``json_valid`` and ``exact_match``
metrics give an unambiguous before/after signal.

    python examples/make_home_commands.py > examples/home_commands.jsonl
"""

from __future__ import annotations

import json
import random

SYSTEM = "You convert smart-home requests into one JSON command with keys action, device, room and optional value. Reply with JSON only."

ROOMS = ["kitchen", "bedroom", "living room", "office", "bathroom", "hallway", "garage"]
DEVICES = {
    "lights": ["lights", "lamp", "light"],
    "thermostat": ["thermostat", "heating", "temperature"],
    "blinds": ["blinds", "shades", "curtains"],
    "speaker": ["speaker", "music", "sound"],
    "fan": ["fan", "ceiling fan"],
}
TEMPLATES = {
    ("turn_on", "lights"): ["turn on the {d} in the {r}", "{r} {d} on please", "can you switch on the {r} {d}", "lights on in the {r}", "it's dark in the {r}, {d} on"],
    ("turn_off", "lights"): ["turn off the {d} in the {r}", "{r} {d} off", "kill the {d} in the {r}", "switch off {r} {d}", "no more {d} in the {r}"],
    ("set", "lights"): ["dim the {r} {d} to {v} percent", "set {r} {d} to {v}%", "{r} {d} at {v} percent", "make the {d} in the {r} {v}%"],
    ("set", "thermostat"): ["set the {r} {d} to {v} degrees", "{r} {d} {v}", "make it {v} degrees in the {r}", "warm the {r} up to {v}"],
    ("turn_off", "thermostat"): ["turn off the {d} in the {r}", "{r} {d} off", "stop heating the {r}"],
    ("open", "blinds"): ["open the {d} in the {r}", "{r} {d} up", "let some light into the {r}, open the {d}", "raise the {r} {d}"],
    ("close", "blinds"): ["close the {d} in the {r}", "{r} {d} down", "shut the {d} in the {r}", "lower the {r} {d}"],
    ("turn_on", "speaker"): ["play {d} in the {r}", "turn on the {r} {d}", "start the {d} in the {r}"],
    ("turn_off", "speaker"): ["stop the {d} in the {r}", "turn off the {r} {d}", "quiet in the {r}, {d} off"],
    ("set", "speaker"): ["set {r} {d} volume to {v}", "{r} {d} volume {v} percent", "turn the {r} {d} to {v}%"],
    ("turn_on", "fan"): ["turn on the {d} in the {r}", "{r} {d} on", "it's hot in the {r}, start the {d}"],
    ("turn_off", "fan"): ["turn off the {d} in the {r}", "{r} {d} off", "stop the {d} in the {r}"],
    ("set", "fan"): ["set the {r} {d} to speed {v}", "{r} {d} speed {v}", "put the {d} in the {r} on {v}"],
}


def main(count: int = 240, seed: int = 7) -> None:
    rng = random.Random(seed)
    seen: set[str] = set()
    rows = []
    keys = list(TEMPLATES)
    while len(rows) < count:
        action, device = rng.choice(keys)
        room = rng.choice(ROOMS)
        alias = rng.choice(DEVICES[device])
        value = None
        if action == "set":
            value = rng.choice([10, 20, 25, 30, 40, 50, 60, 70, 75, 80, 90, 100]) if device != "thermostat" else rng.choice([17, 18, 19, 20, 21, 22, 23, 24])
            if device == "fan":
                value = rng.choice([1, 2, 3])
        text = rng.choice(TEMPLATES[(action, device)]).format(d=alias, r=room, v=value)
        if text in seen:
            continue
        seen.add(text)
        command: dict[str, object] = {"action": action, "device": device, "room": room}
        if value is not None:
            command["value"] = value
        rows.append(
            {
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": text},
                    {"role": "assistant", "content": json.dumps(command, separators=(",", ":"))},
                ],
                "labels": [device, action],
            }
        )
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    main()
