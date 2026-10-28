"""Streaming scientific lab execution; originals and derivatives are separate artifacts."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import shutil
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

from .signals import generate, integer, number


class ExecutionCancelled(RuntimeError):
    """The caller cancelled execution; partial output must not be published."""


def digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def _replay(spec: dict, output_dir: Path, cancelled: Callable[[], bool]) -> Iterator[dict]:
    definition = spec["definition"]
    paths = spec.get("input_paths", {})
    inputs = definition.get("inputs", [])
    if not inputs:
        raise ValueError("recorded.replay requires an immutable input artifact")
    count = 0
    for source_index, item in enumerate(inputs):
        artifact_id = item["artifact_id"]
        resolved = paths.get(artifact_id) if isinstance(paths, dict) else paths[source_index]
        if isinstance(resolved, dict):
            resolved = resolved.get("path")
        if not resolved:
            raise ValueError(f"input path missing for {artifact_id}")
        source = Path(resolved)
        if digest(source) != item["sha256"]:
            raise ValueError(f"input checksum mismatch for {artifact_id}")
        # Copy once and replay the frozen copy so later source mutations cannot alter the run.
        frozen = output_dir / f"original-{source_index}.jsonl"
        shutil.copyfile(source, frozen)
        if digest(frozen) != item["sha256"]:
            raise ValueError("input changed while copying")
        with frozen.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if cancelled():
                    raise ExecutionCancelled("lab run cancelled")
                if not line.strip():
                    continue
                count += 1
                if count > 1_000_000:
                    raise ValueError("replay exceeds 1000000 records")
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError(f"input line {line_number} must be an object")
                record = dict(record)
                record.setdefault("record_id", f"{artifact_id}:{line_number}")
                record.setdefault("episode_id", artifact_id)
                record.setdefault("units", "unitless")
                record["time"] = number(record.get("time"), "time")
                record["available_at"] = number(record.get("available_at", record["time"]), "available_at")
                record.setdefault("observed", record.get("value") is not None)
                if not isinstance(record["observed"], bool):
                    raise ValueError("observed must be boolean")
                if record.get("value") is not None:
                    record["value"] = number(record["value"], "value")
                else:
                    record["value"] = None
                record.setdefault("quality", "good" if record["observed"] else "missing")
                if not isinstance(record["units"], str) or not isinstance(record["quality"], str):
                    raise ValueError("units and quality must be strings")
                if record["observed"] and record["value"] is None:
                    raise ValueError("observed record cannot have a missing value")
                if not isinstance(record["episode_id"], str) or not isinstance(record["record_id"], str):
                    raise ValueError("record_id and episode_id must be strings")
                yield record


def _episodes(records: Iterable[dict]) -> Iterator[dict]:
    closed = set()
    episode = None
    last_time = -math.inf
    seen_ids = set()
    for record in records:
        if record["record_id"] in seen_ids:
            raise ValueError("duplicate record_id")
        seen_ids.add(record["record_id"])
        if record["episode_id"] != episode:
            if episode is not None:
                closed.add(episode)
            episode = record["episode_id"]
            if episode in closed:
                raise ValueError("episodes must be contiguous")
            last_time = -math.inf
        if record["time"] <= last_time:
            raise ValueError("time must strictly increase within an episode")
        if record["available_at"] < record["time"]:
            raise ValueError("available_at cannot precede event time")
        last_time = record["time"]
        yield record


def _transform(records: Iterable[dict], stage: dict, mode: str, seed: int) -> Iterator[dict]:
    kind = stage["kind"]
    p = stage.get("parameters", {})
    if kind not in {"scale", "offset", "scale_offset", "moving_average", "filter", "impute", "features", "split", "representation", "resample"}:
        raise ValueError(f"unsupported pipeline operation: {kind}")
    window = integer(p.get("window", 5), "window", 1, 100000)
    factor = number(p.get("factor", p.get("scale", 1)), "factor")
    offset = number(p.get("offset", 0), "offset")
    threshold = number(p.get("threshold", 0), "threshold")
    interval = number(p.get("dt", 0.1), "resample dt", minimum=1e-9)
    method = p.get("method", "forward_fill")
    if kind in {"filter", "moving_average"} and p.get("method", "moving_average") != "moving_average":
        raise ValueError("only causal moving_average filtering is supported")
    if kind == "impute" and method not in {"forward_fill", "constant"}:
        raise ValueError("imputation supports forward_fill or constant only")
    if kind == "resample" and method not in {"forward_fill", "linear"}:
        raise ValueError("resample supports forward_fill or linear")
    if kind == "resample" and mode == "online" and method == "linear":
        raise ValueError("online linear interpolation would use future observations")
    representation = p.get("format", "numeric")
    if kind == "representation" and representation not in {"numeric", "text", "bytes", "state_tokens"}:
        raise ValueError("unsupported representation")
    train = number(p.get("train", 0.8), "train", minimum=0)
    validation = number(p.get("validation", 0.1), "validation", minimum=0)
    if kind == "split" and train + validation > 1:
        raise ValueError("train and validation fractions exceed 1")
    history = deque(maxlen=window)
    availability = deque(maxlen=window)
    episode = None
    previous = None
    previous_observed = None
    next_grid = None
    output_count = 0
    for original in records:
        row = dict(original)
        if row["episode_id"] != episode:
            episode = row["episode_id"]
            history.clear()
            availability.clear()
            previous = previous_observed = None
            next_grid = row["time"]
        value = row["value"]
        if kind in {"scale", "offset", "scale_offset"} and value is not None:
            row["value"] = value * (1 if kind == "offset" else factor) + (0 if kind == "scale" else offset)
            if "units" in p:
                row["units"] = str(p["units"])
        elif kind in {"moving_average", "filter", "features"}:
            history.append(value)
            availability.append(row["available_at"])
            row["available_at"] = max(availability)
            valid = [v for v in history if v is not None]
            if kind == "features":
                row["features"] = {"mean": sum(valid) / len(valid) if valid else None,
                                   "rms": math.sqrt(sum(v*v for v in valid) / len(valid)) if valid else None,
                                   "count": len(valid), "window_count": len(history)}
            else:
                # A gap stays a gap; filtering must not silently become imputation.
                row["value"] = sum(valid) / len(valid) if value is not None and valid else None
        elif kind == "impute":
            if value is None:
                if method == "constant":
                    row["value"] = number(p.get("value", 0), "impute value")
                elif previous_observed is not None:
                    row["value"] = previous_observed["value"]
                    row["available_at"] = max(row["available_at"], previous_observed["available_at"])
                if row["value"] is not None:
                    row["quality"] = "imputed"
            elif row["observed"]:
                previous_observed = row
        elif kind == "split":
            hashed = hashlib.sha256(f"{seed}:{episode}".encode()).digest()
            bucket = int.from_bytes(hashed[:8], "big") / 2**64
            row["split"] = "train" if bucket < train else "validation" if bucket < train + validation else "test"
        elif kind == "representation":
            row["representation_type"] = representation
            if representation == "numeric":
                row["representation"] = value
            elif representation == "text":
                row["representation"] = "missing" if value is None else f"{value:.12g} {row['units']}"
            elif representation == "bytes":
                # JSON transport uses explicitly labelled base64 of UTF-8 numeric text.
                row["representation"] = base64.b64encode(json.dumps(value, allow_nan=False).encode()).decode()
                row["representation_encoding"] = "base64:utf8-json"
            else:
                row["representation"] = "<missing>" if value is None else "<high>" if value >= threshold else "<low>"
        elif kind == "resample":
            if previous is None:
                previous = row
                yield row
                next_grid += interval
                continue
            while next_grid <= row["time"] + interval * 1e-9:
                output_count += 1
                if output_count > 1_000_000:
                    raise ValueError("resampling exceeds 1000000 records")
                if abs(next_grid - row["time"]) <= interval * 1e-9:
                    result = dict(row)
                else:
                    result = dict(previous)
                    result.update(record_id=f"{previous['record_id']}@{next_grid:.12g}", time=next_grid,
                                  observed=False, quality="resampled", source_record_ids=[previous["record_id"]])
                    if method == "linear":
                        result["source_record_ids"].append(row["record_id"])
                        result["available_at"] = max(next_grid, row["available_at"], previous["available_at"])
                        result["value"] = (None if value is None or previous["value"] is None else
                                           previous["value"] + (value - previous["value"]) *
                                           (next_grid - previous["time"]) / (row["time"] - previous["time"]))
                    else:
                        result["available_at"] = max(next_grid, previous["available_at"])
                yield result
                next_grid += interval
            previous = row
            continue
        if row.get("value") is not None and not math.isfinite(row["value"]):
            raise ValueError("transformation produced a nonfinite value")
        yield row


def execute(spec: dict, output_dir: Path, emit: Callable[[dict], None], cancelled: Callable[[], bool]) -> dict:
    """Execute a frozen resolved specification without mutating it or its inputs."""
    definition = spec["definition"]
    engine = definition["engine"]
    mode = definition.get("mode", "offline")
    if mode not in {"offline", "online"}:
        raise ValueError("mode must be offline or online")
    seed = integer(definition.get("seed", 0), "seed", 0, 2**53)
    if engine not in {"synthetic.signal", "recorded.replay", "simulation.plant"}:
        raise ValueError(f"engine is not implemented: {engine}")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise ValueError("output directory must be empty")
    raw_count = 0
    def originals():
        nonlocal raw_count
        source = (_replay(spec, output_dir, cancelled) if engine == "recorded.replay" else
                  generate(engine, definition.get("parameters", {}), seed))
        with (output_dir / "raw.jsonl").open("w", encoding="utf-8") as stream:
            for row in _episodes(source):
                if cancelled():
                    raise ExecutionCancelled("lab run cancelled")
                stream.write(json.dumps(row, allow_nan=False, sort_keys=True) + "\n")
                raw_count += 1
                yield row
    records = originals()
    for stage in definition.get("pipeline", []):
        records = _transform(records, stage, mode, seed)
    count = missing = 0
    with (output_dir / "processed.jsonl").open("w", encoding="utf-8") as stream:
        for row in records:
            if cancelled():
                raise ExecutionCancelled("lab run cancelled")
            stream.write(json.dumps(row, allow_nan=False, sort_keys=True) + "\n")
            count += 1
            if count > 1_000_000:
                raise ValueError("output exceeds 1000000 records")
            missing += row["value"] is None
            if count % 256 == 0:
                emit({"event": "progress", "records": count, "source_records": raw_count})
    if count == 0:
        raise ValueError("source produced no records")
    # A stable scalar schema keeps Parquet queryable; record_json retains stage-specific fields.
    import pyarrow as pa
    import pyarrow.parquet as pq
    schema = pa.schema([("record_id", pa.string()), ("episode_id", pa.string()),
                        ("time", pa.float64()), ("available_at", pa.float64()),
                        ("value", pa.float64()), ("units", pa.string()),
                        ("observed", pa.bool_()), ("quality", pa.string()),
                        ("record_json", pa.string())])
    for name in ("raw", "processed"):
        with pq.ParquetWriter(output_dir / f"{name}.parquet", schema) as writer:
            with (output_dir / f"{name}.jsonl").open(encoding="utf-8") as source:
                chunk = []
                for line in source:
                    if cancelled():
                        raise ExecutionCancelled("lab run cancelled")
                    parsed = json.loads(line)
                    chunk.append({**{field.name: parsed.get(field.name) for field in schema if field.name != "record_json"},
                                  "record_json": line.rstrip("\n")})
                    if len(chunk) == 1024:
                        writer.write_table(pa.Table.from_pylist(chunk, schema=schema))
                        chunk.clear()
                if chunk:
                    writer.write_table(pa.Table.from_pylist(chunk, schema=schema))
    artifacts = []
    for path in sorted(output_dir.iterdir()):
        artifacts.append({"name": path.name, "path": str(path), "media_type": "application/vnd.apache.parquet" if path.suffix == ".parquet" else "application/x-ndjson",
                          "sha256": digest(path), "role": "derived" if path.stem == "processed" else "original"})
    emit({"event": "progress", "records": count, "source_records": raw_count})
    return {"records": count, "artifacts": artifacts,
            "metrics": {"source_records": raw_count, "output_records": count, "missing_records": missing},
            "qualification": {"engine": engine, "mode": mode, "seed": seed, "causal": mode == "online",
                              "limits": {"source_records": 1000000, "resampled_records": 1000000},
                              "physical_validation": False}}
