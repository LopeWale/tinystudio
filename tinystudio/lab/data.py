"""Bounded immutable acquisition, causal scalar processing and leakage-aware datasets."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import random
import statistics
from copy import deepcopy
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Callable

from .compatibility import partition_snapshot
from .engine import ExecutionCancelled, _transform
from .evaluation import _quantity
from .learning import artifact, resolved_inputs
from .partitioning import isolation_groups, partition_labels, source_protection
from .clocks import CLOCK_FIELDS, clock_reference
from .signals import integer, number


PARTITIONS = ("train", "validation", "test")


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def _boolean(value, name):
    if isinstance(value, bool):
        return value
    if value in ("true", "True", "1", 1):
        return True
    if value in ("false", "False", "0", 0):
        return False
    raise ValueError(f"{name} must be a boolean")


def _read_source(data, format, max_rows):
    if format == "parquet":
        import pyarrow as pa
        import pyarrow.parquet as pq
        file = pq.ParquetFile(pa.BufferReader(data))
        if file.metadata.num_rows > max_rows or sum(file.metadata.row_group(index).total_byte_size for index in range(file.metadata.num_row_groups)) > 64 * 1024 ** 2:
            raise ValueError("Parquet decoded evidence exceeds fixture limits")
        count, decoded_bytes = 0, 0
        # Single-row batches also bound dictionary-expanded string/blob values.
        for batch in file.iter_batches(batch_size=1):
            count += batch.num_rows
            decoded_bytes += batch.nbytes
            if count > max_rows or batch.nbytes > 32 * 1024 ** 2 or decoded_bytes > 64 * 1024 ** 2:
                raise ValueError("Parquet source exceeds bounded row/batch limits")
            yield from batch.to_pylist()
    elif format == "csv":
        yield from csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
    elif format == "jsonl":
        for line in data.decode().splitlines():
            if line.strip():
                yield json.loads(line)
    elif format == "json":
        value = json.loads(data)
        rows = value.get("records", value.get("rows", [value])) if isinstance(value, dict) else value
        yield from rows
    else:
        raise ValueError("acquisition supports JSONL, JSON, CSV and Parquet")


def _format(data, requested=None):
    if requested:
        if requested not in {"jsonl", "json", "csv", "parquet"}:
            raise ValueError("unsupported acquisition format")
        return requested
    if data.startswith(b"PAR1"):
        return "parquet"
    if data.lstrip().startswith((b"{", b"[")):
        try:
            json.loads(data)
            return "json"
        except json.JSONDecodeError:
            return "jsonl"
    return "csv"


def acquire(spec, output_dir, cancelled):
    params = spec["definition"].get("parameters", {})
    maximum = integer(params.get("max_rows", 20000), "max_rows", 6, 100000)
    sources, records = [], []
    total_bytes = 0
    declared = spec["definition"].get("inputs", [])
    resolved = resolved_inputs(spec, visibilities=None)
    for index, binding in enumerate(resolved):
        item = declared[index] if declared else {"artifact_id": f"source-{index}"}
        if item.get("role", "source") not in {"source", "dataset", "recorded"}:
            raise ValueError("data acquisition accepts only source/dataset/recorded input roles")
        path = Path(binding["path"])
        if path.stat().st_size > 32 * 1024 ** 2:
            raise ValueError("individual source exceeds 32 MiB fixture import limit")
        data = path.read_bytes()
        total_bytes += len(data)
        if total_bytes > 64 * 1024 ** 2:
            raise ValueError("sources exceed 64 MiB total fixture import limit")
        digest = hashlib.sha256(data).hexdigest()
        if not binding.get("sha256") or digest != binding["sha256"]:
            raise ValueError("immutable source checksum mismatch")
        format = _format(data, params.get("formats", {}).get(item["artifact_id"], params.get("format")))
        frozen = output_dir / f"original-{index}.{format}"
        frozen.write_bytes(data)
        protection = {"visibility": binding.get("visibility", "model"), "role": binding.get("role", "source")}
        sources.append({"artifact_id": item["artifact_id"], "sha256": digest, "format": format,
                        "bytes": len(data), "path": str(frozen), **protection})
        for position, row in enumerate(_read_source(data, format, maximum - len(records))):
            if cancelled():
                raise ExecutionCancelled("data acquisition cancelled")
            if len(records) >= maximum:
                raise ValueError("data acquisition exceeds configured bounded row limit")
            if not isinstance(row, dict):
                raise ValueError("acquired records must be objects")
            if isinstance(row.get("record_json"), str):
                expanded = json.loads(row["record_json"])
                row = {**expanded, **{key: value for key, value in row.items() if key != "record_json"}}
            records.append({"source": item["artifact_id"], "source_sha256": digest,
                            "position": position, "record": row, **protection})
    inline = params.get("records", [])
    if inline:
        data = b"".join(_canonical(row) + b"\n" for row in inline)
        total_bytes += len(data)
        if len(data) > 32 * 1024 ** 2 or total_bytes > 64 * 1024 ** 2:
            raise ValueError("inline evidence exceeds bounded import limit")
        frozen = output_dir / "original-inline.jsonl"
        frozen.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        sources.append({"artifact_id": "inline", "sha256": digest, "format": "jsonl", "bytes": len(data), "path": str(frozen)})
        for index, row in enumerate(inline):
            if len(records) >= maximum or not isinstance(row, dict):
                raise ValueError("inline records must be bounded objects")
            records.append({"source": "inline", "source_sha256": digest, "position": index,
                            "record": dict(row), "visibility": "model", "role": "source"})
    if not records:
        raise ValueError("data pipeline requires immutable input artifacts or explicit inline records")
    return records, sources, maximum


def normalize(acquired, parameters, clocks):
    schema, cleaned, truths = parameters.get("schema", {}), [], []
    cleaning = parameters.get("cleaning", {})
    seen_ids = set()
    for item in acquired:
        truth = {key: value for key, value in item["record"].items() if key in {"ground_truth", "true_value", "injected_fault"}}
        original = {key: value for key, value in item["record"].items() if key not in truth}
        field = lambda name: original.get(schema.get(name, name))
        value = field("value")
        missing = value is None or value in ("", "NA", "N/A", "null", "None")
        if not missing:
            try:
                value = number(value, "value")
            except ValueError:
                if cleaning.get("invalid_values") != "missing":
                    raise
                missing, value = True, None
        if missing:
            value = None
        event = field("time")
        time_estimated = event is None or event == ""
        if time_estimated:
            if "sampling_interval" not in parameters:
                raise ValueError("missing event times require an explicit sampling_interval")
            event = item["position"] * number(parameters["sampling_interval"], "sampling_interval", minimum=1e-12)
        event = number(event, "event time")
        available = field("available_at")
        availability_assumed = available is None or available == ""
        available = event if availability_assumed else number(available, "available_at")
        if available < event:
            raise ValueError("available_at cannot precede its source event time")
        domain = str(field("clock_domain") or parameters.get("clock_domain", f"source:{item['source']}"))
        affine = clocks.get(domain)
        source_clock = {name: field(name) if field(name) is not None else parameters.get(name)
                        for name in CLOCK_FIELDS}
        source_clock.update(clock_domain=domain,
                            clock_units=source_clock["clock_units"] or "unspecified")
        clock_label = {"source_domain": domain, "source_time": event, "source_available_at": available,
                       "source_clock": source_clock, "basis": "unaligned source clock"}
        if affine:
            scale = number(affine.get("scale", 1), "clock scale", minimum=1e-12)
            offset = number(affine.get("offset", 0), "clock offset")
            if not affine.get("target"):
                raise ValueError("clock affine alignment requires an explicit target clock domain")
            event, available = scale * event + offset, scale * available + offset
            domain = str(affine["target"])
            clock_label.update(scale=scale, offset=offset, basis="authored affine alignment; calibration not independently measured")
        source_id = str(original.get("source_record_id") or f"{item['source']}:{item['position']}")
        if source_id in seen_ids:
            raise ValueError("source_record_id must remain unique across the acquisition")
        seen_ids.add(source_id)
        source_mask = original.get("missing_mask", {})
        if isinstance(source_mask, str):
            try:
                source_mask = json.loads(source_mask)
            except json.JSONDecodeError:
                source_mask = {}
        masked_missing = bool(source_mask.get("value", False)) if isinstance(source_mask, dict) else False
        observed = (not missing and not masked_missing) if field("observed") is None or field("observed") == "" else _boolean(field("observed"), "observed")
        if observed and (missing or masked_missing):
            raise ValueError("observed=true conflicts with a missing value")
        units = str(field("units") or parameters.get("units", "unitless"))
        protection = source_protection(original, visibility=item.get("visibility", "model"),
                                       role=item.get("role", "source"))
        row = {**original, "record_id": source_id, "source_record_id": source_id,
            "source_record_ids": [source_id], "source_artifact_id": item["source"], "source_sha256": item["source_sha256"],
            "source_visibility": protection, "source_role": original.get("source_role", item.get("role", "source")),
            "input_visibility": item.get("visibility", "model"), "input_role": item.get("role", "source"),
            "source_position": item["position"], "original_record_id": original.get("record_id"),
            "episode_id": str(field("episode_id") or f"source:{item['source']}"),
            "time": event, "available_at": available, "clock_domain": domain, "clock_alignment": clock_label,
            "clock_units": str((affine.get("units") or "unspecified") if affine else source_clock["clock_units"]),
            "time_estimated": time_estimated, "availability_assumed": availability_assumed,
            "value": value, "units": units, "units_assumed": not bool(field("units") or parameters.get("units")),
            "observed": observed, "quality": str(field("quality") or ("good" if observed else "missing")),
            "source_missing_mask": original.get("missing_mask", {"value": missing}), "missing_mask": {"value": missing},
            "estimated": not observed and value is not None,
            "estimation_method": None if observed or value is None else "provided estimate",
            "future_aware": _boolean(original.get("future_aware", False), "future_aware"),
            "value_origin": "measured" if observed else "missing" if value is None else "provided_estimate"}
        for name in CLOCK_FIELDS:
            if name in {"clock_domain", "clock_units"}:
                continue
            if affine and name not in {"capture_id", "acquisition_id"}:
                # Target coordinates are authored explicitly; source origin,
                # session and reset cannot qualify a newly aligned clock.
                row.pop(name, None)
                declared = affine.get(name)
            else:
                declared = source_clock[name]
            if declared is not None:
                row[name] = declared
        cleaned.append(row)
        if truth:
            truths.append({"source_record_id": source_id, "source_artifact_id": item["source"], "source_sha256": item["source_sha256"],
                "episode_id": row["episode_id"], "time": event, "available_at": available, "clock_domain": domain,
                "visibility": "truth", **truth})
    return cleaned, truths


def _series_key(row):
    return (row.get("partition"), row["episode_id"], row.get("device_id"), row.get("signal_id"), clock_reference(row)["key"])


def clean_series(records, parameters):
    groups, dropped = defaultdict(list), []
    for row in records:
        groups[_series_key(row)].append(row)
    result = []
    policy = parameters.get("cleaning", {}).get("duplicates", "error")
    if policy not in {"error", "first", "last"}:
        raise ValueError("duplicate policy must be error, first or last")
    for rows in groups.values():
        units = {row["units"] for row in rows}
        if len(units) != 1:
            raise ValueError("a scalar signal series cannot mix units before explicit conversion")
        ordered = sorted(rows, key=lambda row: (row["time"], row["source_record_id"]))
        kept = []
        for row in ordered:
            if kept and row["time"] == kept[-1]["time"]:
                if policy == "error":
                    raise ValueError("duplicate event time in a scalar signal series")
                discarded = row if policy == "first" else kept[-1]
                dropped.append({"source_record_id": discarded["source_record_id"], "reason": f"duplicate time: keep {policy}"})
                if policy == "last":
                    kept[-1] = row
            else:
                kept.append(row)
        result.extend(kept)
    return result, dropped


def partition_records(records, parameters, seed):
    policy = parameters.get("partition_policy") or {}
    split_kind = policy.get("strategy", "temporal")
    if split_kind not in {"grouped", "temporal"}:
        raise ValueError("partition strategy must be grouped or temporal")
    fields = policy.get("group_by")
    if fields is None:
        fields = [field for field in ("episode_id", "device_id", "scenario_id") if any(row.get(field) is not None for row in records)]
    validation = number(policy.get("validation", 0.2), "validation fraction", minimum=1e-12)
    train = number(policy.get("train", 0.6), "train fraction", minimum=1e-12)
    if train + validation >= 1:
        raise ValueError("train/validation/test fractions must all be positive")
    domain = {row["clock_domain"] for row in records}
    references = [clock_reference(row) for row in records]
    clocks = {reference["key"]: reference for reference in references}
    comparable = len(clocks) == 1 and all(reference["qualified"] for reference in references)
    if split_kind == "temporal" and not comparable:
        raise ValueError("temporal partitions require one qualified comparable clock coordinate; "
                         "select the grouped strategy or declare explicit clock alignment including "
                         "units, origin, session and reset identity")
    base = partition_snapshot(records, seed, validation, {"group_by": fields}, min_training_rows=1)
    by_id = {row["source_record_id"]: index for index, row in enumerate(records)}
    groups = [[by_id[value] for value in group] for group in base["groups"]]
    lower = lambda group: min(records[index]["time"] for index in group)
    upper = lambda group: max(records[index]["available_at"] for index in group)
    purge_seconds = number(policy.get("purge_seconds", 0), "purge_seconds", minimum=0)
    gap = purge_seconds
    if split_kind == "temporal" and gap:
        try:
            seconds, _, _ = _quantity(purge_seconds, "s")
            unit_seconds, dimension, _ = _quantity(1, references[0]["units"])
            if dimension != "time":
                raise ValueError("clock units do not describe time")
            gap = number(float(seconds / unit_seconds), "purge clock duration", minimum=0)
        except (ValueError, OverflowError) as exc:
            raise ValueError("purge_seconds requires supported time units; declare explicit clock alignment "
                             "to s, ms or us before applying a nonzero purge") from exc
    action = policy.get("boundary_action", "error")
    if action not in {"error", "purge"}:
        raise ValueError("temporal boundary_action must be error or purge")
    if split_kind == "grouped" and (gap or policy.get("boundaries") or action != "error"):
        raise ValueError("temporal boundary and purge controls require the temporal partition strategy")
    purged = []
    if base["strategy"] == "preserved_explicit_partitions":
        assignments = {index: name for name in PARTITIONS for index in base["partitions"][name]["indices"]}
        strategy = "preserved_explicit_grouped_temporal" if split_kind == "temporal" else "preserved_explicit_grouped"
    else:
        if split_kind == "grouped":
            ordered = list(groups)
            random.Random(seed).shuffle(ordered)
        else:
            ordered = sorted(groups, key=lambda group: (lower(group), upper(group), tuple(group)))
        if len(ordered) < 3:
            raise ValueError("three disjoint groups are required for train/validation/test")
        boundaries = policy.get("boundaries")
        if boundaries:
            end_train = number(boundaries["train_end"], "train_end")
            end_validation = number(boundaries["validation_end"], "validation_end")
            if end_train >= end_validation:
                raise ValueError("temporal boundaries must increase")
            assignments = {}
            for group in ordered:
                left, right = lower(group), upper(group)
                part = "train" if right < end_train - gap else "validation" if left >= end_train + gap and right < end_validation - gap else "test" if left >= end_validation + gap else None
                if part is None:
                    if action != "purge":
                        raise ValueError("a connected group crosses a temporal/availability boundary")
                    purged.extend(group)
                else:
                    assignments.update({index: part for index in group})
        else:
            train_groups = max(1, min(len(ordered) - 2, int(len(ordered) * train)))
            validation_groups = max(1, min(len(ordered) - train_groups - 1, int(len(ordered) * validation)))
            assignments = {index: "train" if position < train_groups else "validation" if position < train_groups + validation_groups else "test"
                           for position, group in enumerate(ordered) for index in group}
        strategy = "chronological_connected_groups" if split_kind == "temporal" else "seeded_connected_groups"
    # Availability, rather than only event time, governs whether information was
    # known before the next partition begins. Purge complete connected groups.
    for left, right in zip(PARTITIONS, PARTITIONS[1:]) if split_kind == "temporal" else ():
        earlier = [index for index, partition in assignments.items() if partition == left]
        later = [index for index, partition in assignments.items() if partition == right]
        if not earlier or not later:
            raise ValueError("each final partition must retain at least one connected group")
        start_later = min(records[index]["time"] for index in later)
        leaking = [group for group in groups if any(index in earlier for index in group) and upper(group) + gap >= start_later]
        if leaking and action != "purge":
            raise ValueError("partition availability overlaps a later temporal partition")
        for group in leaking:
            purged.extend(group)
            for index in group:
                assignments.pop(index, None)
    partitions = {name: [index for index, partition in assignments.items() if partition == name] for name in PARTITIONS}
    if any(not values for values in partitions.values()):
        raise ValueError("purging leaves an empty final partition")
    output = [{**row, "partition": assignments[index], "split": assignments[index]} for index, row in enumerate(records) if index in assignments]
    manifests = {}
    for name, indices in partitions.items():
        evidence = [{"source_record_id": records[index]["source_record_id"], "sha256": hashlib.sha256(_canonical(records[index])).hexdigest()} for index in indices]
        clock_bounds = {clock: {
            "event_time_min": min(records[index]["time"] for index in indices if references[index]["key"] == clock),
            "available_at_max": max(records[index]["available_at"] for index in indices if references[index]["key"] == clock)}
            for clock in sorted({references[index]["key"] for index in indices})}
        manifests[name] = {"records": evidence, "sha256": hashlib.sha256(_canonical(evidence)).hexdigest(),
            "clock_bounds": clock_bounds,
            "event_time_min": min(records[index]["time"] for index in indices) if comparable else None,
            "available_at_max": max(records[index]["available_at"] for index in indices) if comparable else None}
    overlap = {}
    for field in ("episode_id", "device_id", "scenario_id"):
        values = defaultdict(set)
        for row in output:
            if row.get(field) is not None:
                values[str(row[field])].add(row["partition"])
        overlap[field] = {value: sorted(parts) for value, parts in values.items() if len(parts) > 1}
    manifest = {"strategy": strategy, "clock_domain": next(iter(domain)) if comparable else None,
        "clock_domains": sorted(domain), "clock_references": clocks,
        "temporal_isolation": split_kind == "temporal", "group_by": fields,
        "group_isolation": "connected components for every configured field", "groups": base["groups"],
        "partitions": manifests, "overlap_report": overlap,
        "purged": [{"source_record_id": records[index]["source_record_id"], "reason": "temporal/availability boundary"} for index in sorted(set(purged))],
        "purge_seconds": purge_seconds, "purge_coordinate_duration": gap,
        "future_test_used_for_fitting": False}
    return output, manifest


def prepare_partitions(records, parameters, seed):
    """Preparing evidence does not implicitly request a training dataset."""
    declared = partition_labels(records, allow_truth=True, allow_unassigned=True)
    policy = parameters.get("partition_policy")
    if policy is not None and not isinstance(policy, dict):
        raise ValueError("partition_policy must be an object")
    if policy is not None and not isinstance(policy.get("enabled", True), bool):
        raise ValueError("partition_policy.enabled must be boolean")
    requested = policy is not None and policy.get("enabled", True)
    complete_dataset = set(declared) == set(PARTITIONS)
    if requested or complete_dataset:
        if any(source_protection(row) == "truth" for row in records):
            raise ValueError("truth-bound observations cannot be promoted into a model dataset")
        output, manifest = partition_records(records, parameters, seed)
        if any(row["partition"] == "train" and source_protection(row) != "model" for row in output):
            raise ValueError("evaluation-bound observations cannot be assigned to training")
        return output, {**manifest, "dataset_ready": True, "status": "validated"}

    fields = (policy or {}).get("group_by")
    if fields is None:
        fields = [name for name in ("episode_id", "device_id", "scenario_id")
                  if any(row.get(name) is not None for row in records)]
    if isinstance(fields, str):
        fields = [fields]
    groups = isolation_groups(records, fields) if declared and declared[0] is not None else []
    for group in groups:
        if len({declared[index] for index in group}) > 1:
            raise ValueError("isolation group overlaps declared partitions")
    output = [{**row, "partition": label or "unassigned", "split": label or "unassigned"}
              for row, label in zip(records, declared, strict=True)]
    return output, {
        "strategy": "preserved_declared_evidence" if groups else "not_requested",
        "status": "not_requested", "dataset_ready": False, "group_by": fields,
        "groups": [[records[index]["source_record_id"] for index in group] for group in groups],
        "partitions": {}, "purged": [], "overlap_report": {},
        "source_partition_labels": [{"source_record_id": row["source_record_id"],
            "declared": {key: row[key] for key in ("partition", "split", "bucket") if key in row}}
            for row in records],
        "future_test_used_for_fitting": False,
    }


def online_plan(parameters, stages):
    policy = parameters.get("online_policy", {})
    if not isinstance(policy, dict) or policy.keys() - {"allow_assumed_availability", "assumption_reason"}:
        raise ValueError("online_policy accepts an explicit availability assumption and reason")
    assumed = policy.get("allow_assumed_availability", False)
    if not isinstance(assumed, bool):
        raise ValueError("online availability assumption must be boolean")
    if assumed and (not isinstance(policy.get("assumption_reason"), str) or not policy["assumption_reason"].strip()):
        raise ValueError("assumed online availability requires a recorded assumption_reason")
    if parameters.get("cleaning", {}).get("duplicates", "error") != "error":
        raise ValueError("online replay rejects duplicate event times; offline duplicate replacement uses complete evidence")
    for stage in stages:
        if not isinstance(stage, dict) or not isinstance(stage.get("kind"), str):
            raise ValueError("online replay stages require an explicit operation kind")
        options = stage.get("parameters", {})
        if not isinstance(options, dict):
            raise ValueError("online replay stage parameters must be an object")
        kind = stage["kind"]
        if kind == "resample":
            raise ValueError("online replay is observation-triggered; use offline resampling until an independent replay horizon is defined")
        if kind == "standardize" or kind == "impute" and options.get("method") in {"mean", "median"}:
            raise ValueError("online replay cannot use batch training fits; select an available-history transform or offline fitting")
        if options.get("future_aware"):
            raise ValueError("online replay cannot select a future-aware transform")
    return policy


def arrival_records(records, policy):
    groups = defaultdict(list)
    for row in records:
        if row["future_aware"]:
            raise ValueError("online replay rejects future-aware source observations")
        if row["availability_assumed"] and not policy.get("allow_assumed_availability", False):
            raise ValueError("online replay requires recorded availability or an explicit qualified availability assumption")
        if row.get("availability_clock_domain", row["clock_alignment"]["source_domain"]) != row["clock_alignment"]["source_domain"]:
            raise ValueError("online availability clock must be aligned with the event clock before replay")
        groups[_series_key(row)].append(row)
    result = []
    for rows in groups.values():
        ordered = sorted(rows, key=lambda row: (row["available_at"], row["time"], row["source_record_id"]))
        for ordinal, row in enumerate(ordered):
            result.append({**row, "event_time": row["time"], "time": row["available_at"],
                "decision_at": row["available_at"], "timeline_basis": "availability", "arrival_ordinal": ordinal,
                "tie_order": "event_time_then_source_record_id"})
    return result


def assert_available_history(records, source_availability):
    for row in records:
        decision = row["decision_at"]
        if (row["available_at"] > decision or row["future_aware"]
                or any(source_availability[source] > decision for source in row["source_record_ids"])):
            raise ValueError("online replay transformation used information unavailable at its decision")


def transform_records(records, stages, mode, seed, maximum, *, dataset_ready=True):
    if len(stages) > 32:
        raise ValueError("data pipeline exceeds 32 bounded fixture stages")
    fits, lineage = [], {row["record_id"]: row["source_record_ids"] for row in records}
    source_availability = {row["source_record_id"]: row["available_at"] for row in records}
    if mode == "online":
        assert_available_history(records, source_availability)
    for stage_index, stage in enumerate(stages):
        kind, options = stage["kind"], stage.get("parameters", {})
        if kind == "split":
            raise ValueError("data.pipeline owns grouped temporal partitions; split stages are not allowed")
        if kind == "representation" and options.get("format") == "chat":
            continue
        if kind == "standardize" or kind == "impute" and options.get("method") in {"mean", "median"}:
            if not dataset_ready or not any(row.get("partition") == "train" for row in records):
                raise ValueError("training-only fitting requires validated training partition evidence")
            by_units = defaultdict(list)
            for row in records:
                if row["partition"] == "train" and row["value"] is not None:
                    by_units[row["units"]].append(row)
            fitted = {}
            for units, rows in by_units.items():
                values = [row["value"] for row in rows]
                references = {clock_reference(row)["key"]: clock_reference(row) for row in rows}
                availability = {}
                for row in rows:
                    reference = clock_reference(row)
                    entry = availability.setdefault(reference["key"], {"coordinate": reference["coordinate"],
                        "qualified": reference["qualified"], "available_at_max": row["available_at"]})
                    entry["available_at_max"] = max(entry["available_at_max"], row["available_at"])
                common = next(iter(references.values())) if len(references) == 1 else None
                comparable = common is not None and common["qualified"]
                fitted[units] = {"mean": statistics.mean(values), "median": statistics.median(values),
                    "std": statistics.pstdev(values) or 1.0,
                    "available_at": max(row["available_at"] for row in rows) if comparable else None,
                    "clock_domain": common["domain"] if comparable else None,
                    "clock_key": common["key"] if comparable else None,
                    "availability_by_clock": availability,
                    "source_record_ids": sorted({value for row in rows for value in row["source_record_ids"]})}
            for fit in fitted.values():
                fit["sha256"] = hashlib.sha256(_canonical(fit)).hexdigest()
            fits.append({"stage": stage_index, "kind": kind, "fit_partition": "train", "by_units": fitted})
            updated = []
            for original in records:
                row = dict(original)
                fit = fitted.get(row["units"])
                if fit is None:
                    raise ValueError("a held-out unit has no training-only fit evidence")
                if kind == "standardize" and row["value"] is not None:
                    row["value"] = (row["value"] - fit["mean"]) / fit["std"]
                    row["source_units"], row["units"] = row["units"], "standard_deviations"
                elif kind == "impute" and row["value"] is None:
                    row["value"] = fit[options["method"]]
                    row["quality"] = "imputed"
                else:
                    updated.append(row)
                    continue
                reference = clock_reference(row)
                comparable = reference["qualified"] and fit["clock_key"] == reference["key"]
                if comparable:
                    row["available_at"] = max(row["available_at"], fit["available_at"])
                row["fit_availability"] = {"clock_comparable": comparable,
                    "availability_by_clock": fit["availability_by_clock"],
                    "basis": "training-only fit; independent clock bounds are not a shared timestamp"}
                row["availability_qualification"] = "qualified_fit" if comparable else "observation_only; fitted-value availability is unknown"
                row["future_aware"] = row["future_aware"] or not comparable or row["available_at"] > row["time"]
                row["estimated"], row["estimation_method"] = True, f"training-only {kind}"
                row["value_origin"] = "normalized_measurement" if kind == "standardize" else "imputed"
                row["fit_dependencies"] = list(row.get("fit_dependencies", [])) + [{"stage": stage_index, "sha256": fit["sha256"], "fit_partition": "train"}]
                row["missing_mask"] = {"value": row["value"] is None}
                updated.append(row)
            records = updated
        else:
            if kind == "resample" and options.get("method", "forward_fill") == "linear" and options.get("future_aware") is not True:
                raise ValueError("linear interpolation requires an explicit future_aware=true label")
            groups = defaultdict(list)
            for row in records:
                groups[_series_key(row)].append(row)
            updated, lineage_entries = [], 0
            for rows in groups.values():
                rows = sorted(rows, key=lambda row: row["time"])
                if kind in {"scale", "offset", "scale_offset"} and options.get("units"):
                    if options.get("source_units") != rows[0]["units"]:
                        raise ValueError("unit conversion requires matching explicit source_units")
                window = deque(maxlen=integer(options.get("window", 5), "window", 1, min(maximum, 256)))
                donor = None
                dependencies = {}
                for row in rows:
                    window.append(row)
                    if kind in {"moving_average", "filter", "features"}:
                        dependencies[row["record_id"]] = sorted({source for value in window for source in value["source_record_ids"]})
                    elif kind == "impute":
                        if row["value"] is None and donor is not None and options.get("method", "forward_fill") == "forward_fill":
                            dependencies[row["record_id"]] = sorted(set(row["source_record_ids"] + donor["source_record_ids"]))
                        elif row["value"] is not None and row["observed"]:
                            donor = row
                for row in _transform(rows, stage, mode, seed):
                    refs = dependencies.get(row["record_id"], row.get("source_record_ids", [row["source_record_id"]]))
                    row["source_record_ids"] = sorted({source for ref in refs for source in lineage.get(ref, [ref])})
                    lineage_entries += len(row["source_record_ids"])
                    if len(row["source_record_ids"]) > 1024 or lineage_entries > 2_000_000:
                        raise ValueError("transformed lineage exceeds bounded fixture limits")
                    derived = kind in {"moving_average", "filter", "features", "scale", "offset", "scale_offset"} or row.get("quality") in {"imputed", "resampled"}
                    row["estimated"] = row.get("estimated", False) or derived
                    row["estimation_method"] = kind if derived else row.get("estimation_method")
                    if derived:
                        row["value_origin"] = "imputed" if row.get("quality") == "imputed" else "resampled_estimate" if row.get("quality") == "resampled" else "transformed_measurement"
                    if kind == "resample" and row.get("quality") == "resampled":
                        row["origin_source_record_id"] = row["source_record_id"]
                        row["source_record_id"] = row["record_id"]
                        row["future_aware"] = row["future_aware"] or options.get("method") == "linear"
                    row["missing_mask"] = {"value": row["value"] is None}
                    if kind == "features":
                        row["field_units"] = {**row.get("field_units", {}),
                            "features.mean": row["units"], "features.rms": row["units"],
                            "features.count": "records", "features.window_count": "records"}
                    if options.get("units"):
                        row["unit_conversion"] = {"from": options["source_units"], "to": options["units"],
                            "factor": options.get("factor", options.get("scale", 1)), "offset": options.get("offset", 0), "basis": "authored conversion"}
                    updated.append(row)
                    if len(updated) > maximum:
                        raise ValueError("transformed data exceeds configured bounded row limit")
            records = updated
        lineage = {row["record_id"]: row["source_record_ids"] for row in records}
        if mode == "online":
            assert_available_history(records, source_availability)
    return records, fits


def profile(records):
    units = defaultdict(list)
    for row in records:
        if row["value"] is not None:
            units[row["units"]].append(row["value"])
    groups = defaultdict(list)
    for row in records:
        groups[_series_key(row)].append(row)
    gaps = []
    for rows in groups.values():
        ordered = sorted(rows, key=lambda row: row["time"])
        differences = [right["time"] - left["time"] for left, right in zip(ordered, ordered[1:])]
        nominal = statistics.median(differences) if differences else None
        for left, right in zip(ordered, ordered[1:]):
            if nominal and right["time"] - left["time"] > 1.5 * nominal:
                gaps.append({"after_source_record_id": left["source_record_id"], "from": left["time"], "to": right["time"],
                             "duration": right["time"] - left["time"], "clock_domain": left["clock_domain"]})
    return {"records": len(records), "missing_values": sum(row["value"] is None for row in records),
        "estimated_records": sum(row["estimated"] for row in records), "future_aware_records": sum(row["future_aware"] for row in records),
        "units": {unit: {"count": len(values), "min": min(values), "max": max(values), "mean": statistics.mean(values), "std": statistics.pstdev(values)} for unit, values in units.items()},
        "partition_counts": dict(Counter(row.get("partition", "unassigned") for row in records)),
        "clock_domains": sorted({row["clock_domain"] for row in records}), "gap_count": len(gaps), "gaps": gaps[:1000]}


def _unchanged_observation(row, original):
    fields = ("time", "available_at", "value", "units", "observed", "missing_mask",
              "clock_domain", "estimated", "quality", "features", "source_record_ids")
    return original is not None and all(row.get(field) == original.get(field) for field in fields)


def invalidate_supervision(records, originals):
    by_id = {row["record_id"]: row for row in originals}
    for row in records:
        if "messages" in row and not _unchanged_observation(row, by_id.get(row["record_id"])):
            row.pop("messages")
            row["supervision_status"] = {"status": "invalidated", "reason": "observation changed",
                "original_source_record_id": row.get("origin_source_record_id", row["source_record_id"])}


def authored_chat_rows(records, originals, supervision=None):
    """Export authored supervision only for unchanged source observations."""
    from tinystudio.core.data import validate_datapoint
    by_id = {row["record_id"]: row for row in originals}
    result = []
    for row in records:
        mapping = (supervision or {}).get(row["record_id"])
        if mapping and "messages" in mapping:
            from .annotations import observation_checksum
            if (mapping["observation_sha256"] != observation_checksum(row)
                    or row.get("messages") != mapping["messages"]):
                raise ValueError("explicit supervision mapping no longer matches its reviewed observation")
            result.append({**row, "message_source_record_id": row["source_record_id"],
                           "message_origin": "explicit_annotation"})
            continue
        original = by_id.get(row["record_id"])
        if not _unchanged_observation(row, original):
            raise ValueError("chat export requires an explicit supervision mapping for changed or resampled observations")
        if not original.get("messages"):
            raise ValueError("chat export requires explicit authored messages; observations are not fabricated into Q&A")
        validate_datapoint({"messages": original["messages"]})
        result.append({**row, "messages": deepcopy(original["messages"]),
                       "message_source_record_id": original["source_record_id"],
                       "message_origin": "authored_source"})
    return result


def publication_visibility(records, *, training=False):
    protections = {source_protection(row) for row in records}
    if "truth" in protections:
        return "truth"
    return "model" if training and protections == {"model"} else "evaluation"


def export_representations(records, specifications, output_dir, *, dataset_ready, cancelled):
    from .representations import RepresentationEncoder

    if not isinstance(specifications, list) or len(specifications) > 8:
        raise ValueError("representations must be a list of at most eight explicit codecs")
    encoders = [RepresentationEncoder(specification) for specification in specifications]
    if len({encoder.specification["name"].casefold() for encoder in encoders}) != len(encoders):
        raise ValueError("representation names must be unique")
    artifacts, entries, total_bytes = [], [], 0
    for encoder in encoders:
        name = encoder.specification["name"]
        path = output_dir / f"representation-{name}.jsonl"
        token_count, truncated, quantized = 0, 0, 0
        with path.open("wb") as stream:
            for row in records:
                if cancelled():
                    raise ExecutionCancelled("representation export cancelled")
                encoded = encoder.encode(row)
                encoded["visibility"] = publication_visibility([row], training=dataset_ready and row["partition"] == "train")
                data = _canonical(encoded) + b"\n"
                total_bytes += len(data)
                if total_bytes > 128 * 1024 ** 2:
                    raise ValueError("matched representations exceed the 128 MiB export budget")
                stream.write(data)
                token_count += encoded["token_count"]
                truncated += encoded["loss"]["truncated"]
                quantized += encoded["loss"]["numeric_quantized"]
        item = {**artifact(path, "representation_evidence", "application/x-ndjson"),
                "visibility": publication_visibility(records)}
        artifacts.append(item)
        entries.append({"name": name, "specification": encoder.specification,
                        "specification_hash": encoder.specification_hash,
                        "artifact": {key: item[key] for key in ("name", "sha256", "visibility")},
                        "records": len(records), "token_count": token_count,
                        "truncated_records": truncated, "quantized_records": quantized})
    if entries:
        manifest = output_dir / "representations.json"
        manifest.write_bytes(_canonical({"schema_version": 1, "representations": entries,
            "matched_source_records_sha256": hashlib.sha256(_canonical([
                {"source_record_id": row["source_record_id"], "source_record_ids": row["source_record_ids"],
                 "partition": row["partition"]} for row in records])).hexdigest(),
            "fitting": "none; authored codec configurations only", "bytes": total_bytes}))
        artifacts.append({**artifact(manifest, "representation_manifest"),
                          "visibility": publication_visibility(records)})
    return artifacts


def execute_data(spec, output_dir: Path, emit: Callable[[dict], None], cancelled: Callable[[], bool]):
    definition, output_dir = spec["definition"], Path(output_dir)
    params = definition.get("parameters", {})
    mode = definition.get("mode", "offline")
    if mode not in {"offline", "online"}:
        raise ValueError("data.pipeline mode must be offline or online")
    stages = definition.get("pipeline", params.get("pipeline", []))
    online_policy = online_plan(params, stages) if mode == "online" else None
    output_dir.mkdir(parents=True, exist_ok=True)
    acquired, sources, maximum = acquire(spec, output_dir, cancelled)
    alignment = params.get("clock_alignment", definition.get("clocks", {}).get("alignment", {}))
    normalized, truths = normalize(acquired, params, alignment)
    # Original files retain every acquired row, including rows cleaning removes.
    truth_sources = {row["source_artifact_id"] for row in normalized if source_protection(row) == "truth"}
    truth_sources.update(row["source_artifact_id"] for row in truths)
    truth_sources.update(source["artifact_id"] for source in sources if source.get("visibility") == "truth")
    normalized, dropped = clean_series(normalized, params)
    raw_path = output_dir / "raw-normalized.jsonl"
    raw_path.write_bytes(b"".join(_canonical(row) + b"\n" for row in normalized))
    partitioned, manifest = prepare_partitions(normalized, params, int(definition.get("seed", 42)))
    partition_by_id = {row["source_record_id"]: row["partition"] for row in partitioned}
    for row in truths:
        row["partition"] = partition_by_id.get(row["source_record_id"], "purged_or_cleaned")
    manifest.update(source_artifacts=sources, cleaning_dropped=dropped,
        truth_source_record_ids=[row["source_record_id"] for row in truths], truth_fields_separated=True)
    prepared = arrival_records(partitioned, online_policy) if mode == "online" else partitioned
    transformed, fits = transform_records(prepared, stages, mode, int(definition.get("seed", 42)), maximum,
                                         dataset_ready=manifest["dataset_ready"])
    invalidate_supervision(transformed, normalized)
    from .annotations import apply_annotations
    transformed, supervision, annotation_manifest = apply_annotations(transformed, params.get("annotations", []))
    if cancelled():
        raise ExecutionCancelled("data pipeline cancelled")
    transformed_path = output_dir / "transformed.jsonl"
    transformed_path.write_bytes(b"".join(_canonical(row) + b"\n" for row in transformed))
    manifest["fitted_transforms"] = fits
    manifest["annotations"] = annotation_manifest
    online_status = None if mode == "offline" else {
        "timeline_basis": "availability", "event_times_retained": True,
        "tie_order": "event_time_then_source_record_id", "history": "ordered arrivals within each independent signal and partition",
        "assumed_availability_records": sum(row["availability_assumed"] for row in prepared),
        "arrival_measurements_qualified": not any(row["availability_assumed"] for row in prepared),
        "policy": online_policy, "live_acquisition": False, "real_time_scheduling": False,
        "resampling": "unsupported without an independently defined replay horizon"}
    manifest["processing"] = {"mode": mode, "online_replay": online_status}
    manifest["raw_sha256"] = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    manifest["transformed_sha256"] = hashlib.sha256(transformed_path.read_bytes()).hexdigest()
    manifest_path = output_dir / "partitions.json"
    manifest_path.write_bytes(_canonical(manifest))
    profile_path = output_dir / "profile.json"
    profiles = {"raw": profile(normalized), "transformed": profile(transformed)}
    profile_path.write_bytes(_canonical(profiles))
    artifacts = [{**artifact(Path(source["path"]), "original_evidence", "application/octet-stream"),
                  "visibility": "truth" if source["artifact_id"] in truth_sources else "evaluation"} for source in sources]
    artifacts.extend([artifact(raw_path, "normalized_original", "application/x-ndjson"),
        artifact(transformed_path, "transformed_dataset", "application/x-ndjson"),
        artifact(manifest_path, "partition_manifest"), artifact(profile_path, "data_profile")])
    for item in artifacts:
        item.setdefault("visibility", "evaluation")
        if any(source_protection(row) == "truth" for row in normalized):
            item["visibility"] = "truth"
    if truths:
        truth_path = output_dir / "truth.jsonl"
        truth_path.write_bytes(b"".join(_canonical(row) + b"\n" for row in truths))
        artifacts.append({**artifact(truth_path, "separated_truth", "application/x-ndjson"), "visibility": "truth"})
    for partition in PARTITIONS if manifest["dataset_ready"] else ():
        prepared_path = output_dir / f"prepared-{partition}.jsonl"
        prepared = [{**row, "visibility": "model" if partition == "train" else "evaluation"} for row in transformed if row["partition"] == partition]
        prepared_path.write_bytes(b"".join(_canonical(row) + b"\n" for row in prepared))
        artifacts.append({**artifact(prepared_path, f"prepared_{partition}", "application/x-ndjson"),
                          "visibility": "model" if partition == "train" else "evaluation"})
    representation = params.get("representation", "numeric")
    if representation == "chat" or any(stage["kind"] == "representation" and stage.get("parameters", {}).get("format") == "chat" for stage in stages):
        chats = authored_chat_rows(transformed, normalized, supervision)
        for partition in PARTITIONS if manifest["dataset_ready"] else ("evidence",):
            chat_path = output_dir / f"chat-{partition}.jsonl"
            examples = [{**row, "visibility": publication_visibility([row], training=partition == "train")}
                        for row in chats if partition == "evidence" or row["partition"] == partition]
            chat_path.write_bytes(b"".join(_canonical(row) + b"\n" for row in examples))
            artifacts.append({**artifact(chat_path, f"chat_{partition}", "application/x-ndjson"),
                              "visibility": publication_visibility(examples, training=partition == "train")})
        if manifest["dataset_ready"]:
            combined = output_dir / "chat-dataset.jsonl"
            combined.write_bytes(b"".join(_canonical({**row, "visibility": "model" if row["partition"] == "train"
                                                       else "evaluation"}) + b"\n" for row in chats))
            artifacts.append({**artifact(combined, "supervised_dataset", "application/x-ndjson"),
                              "visibility": "evaluation"})
    artifacts.extend(export_representations(transformed, params.get("representations", []), output_dir,
        dataset_ready=manifest["dataset_ready"], cancelled=cancelled))
    figure_status = {"requested": bool(params.get("figures")), "generated": False}
    if params.get("figures"):
        from .figures import export_figures
        figures, figure_status = export_figures(transformed, output_dir)
        artifacts.extend(figures)
    manifest["retained_artifacts"] = [{"name": item["name"], "sha256": item["sha256"], "role": item["role"],
        "visibility": item.get("visibility", "evaluation")} for item in artifacts if item["role"] != "partition_manifest"]
    manifest_path.write_bytes(_canonical(manifest))
    for item in artifacts:
        if item["role"] == "partition_manifest":
            item["sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    emit({"type": "data", "source_records": len(acquired), "output_records": len(transformed), "partitions": profiles["transformed"]["partition_counts"]})
    return {"records": len(transformed), "artifacts": artifacts,
        "metrics": {"source_records": len(acquired), "output_records": len(transformed), "dropped_records": len(dropped),
            "purged_records": len(manifest["purged"]), "missing_records": profiles["transformed"]["missing_values"],
            "partition_counts": profiles["transformed"]["partition_counts"]},
        "qualification": {"status": "completed", "acquisition": "immutable JSON/JSONL/CSV/Parquet imports",
            "processing_mode": mode, "online_replay": online_status,
            "dataset_ready": manifest["dataset_ready"],
            "authored_annotations": annotation_manifest["applied"],
            "source_metadata_retained": True, "raw_separate_from_transformed": True, "train_only_fits": True,
            "truth_fields_separated": True, "model_visible_partitions": ["train"] if manifest["dataset_ready"] else [],
            "causal": profiles["transformed"]["future_aware_records"] == 0, "figures": figure_status,
            "scale": {"max_rows": maximum, "source_bytes": 64 * 1024 ** 2, "per_source_bytes": 32 * 1024 ** 2, "pipeline_stages": 32, "window_records": 256,
                      "lineage_entries_per_stage": 2_000_000,
                      "implementation": "bounded in-memory scalar fixtures; no large-scale streaming qualification"}}}
