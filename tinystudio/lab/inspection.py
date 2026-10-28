"""Bounded, checksum-qualified inspection of exact retained record identities."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import tempfile

from . import service as svc
from .annotations import observation_checksum
from .partitioning import partition_label

MAX_BYTES = 128 * 1024**2
MAX_RECORDS = 1_000_000
MAX_LINE = 1024**2
MAX_AXIS_VALUES = 256
AXES = ("fault_type", "fault_severity", "operating_range", "device_id", "scenario_id", "episode_id", "partition", "units")


def _nonfinite(value):
    raise ValueError("non-finite JSON number")


class _Retained:
    """Own the request's materialized evidence and cumulative scan budget."""

    def __init__(self, db, project, run_id):
        self.db, self.project = db, project
        self.run = svc.get_run(db, project, run_id)
        self.refs = {ref["id"]: ref for ref in (self.run.result or {}).get("artifacts", [])}
        self.bytes = self.records = 0

    def artifact(self, identity):
        if identity not in self.refs:
            raise svc.LabError("artifact is not retained by this run", 404)
        return svc.get_artifact(self.db, self.project, identity)

    def rows(self, artifact):
        self.bytes += artifact.size
        if self.bytes > MAX_BYTES:
            raise svc.LabError("inspection exceeds 128 MiB; export retained artifacts for offline inspection", 413)
        with tempfile.TemporaryDirectory(prefix="ts-lab-inspection-") as root:
            path = svc.materialize(artifact, Path(root))  # verifies authoritative checksum and size
            with path.open("rb") as stream:
                for index, line in enumerate(iter(lambda: stream.readline(MAX_LINE + 1), b"")):
                    self.records += 1
                    if self.records > MAX_RECORDS or len(line) > MAX_LINE:
                        raise svc.LabError("inspection record budget exceeded; export retained artifacts for offline inspection", 413)
                    try:
                        text = line.decode("utf-8")
                        row = json.loads(text, parse_constant=_nonfinite)
                    except (ValueError, UnicodeError) as exc:
                        raise svc.LabError("retained artifact contains invalid JSONL", 409) from exc
                    if not isinstance(row, dict):
                        raise svc.LabError("retained JSONL record must be an object", 409)
                    yield index, row, text

    def identity(self, artifact, index, row):
        return {"run_id": self.run.id, "artifact_id": artifact.id, "artifact_sha256": artifact.sha256,
                "row_index": index, "source_record_id": row.get("source_record_id"),
                "observation_sha256": observation_checksum(row)}


def representations(db, project, run_id, artifact_id, row_index):
    if isinstance(row_index, bool) or not isinstance(row_index, int) or not 0 <= row_index < MAX_RECORDS:
        raise svc.LabError("row index must be between 0 and 999999")
    retained = _Retained(db, project, run_id)
    artifact = retained.artifact(artifact_id)
    if not artifact.name.endswith(".jsonl") or artifact.role == "representation_evidence":
        raise svc.LabError("select an original or transformed JSONL observation")
    selected_row = next(((row, text) for index, row, text in retained.rows(artifact) if index == row_index), None)
    if selected_row is None:
        raise svc.LabError("retained row not found", 404)
    selected, selected_text = selected_row
    identity = retained.identity(artifact, row_index, selected)
    source_id = selected.get("source_record_id")
    matches, unqualified, scanned = [], 0, []
    response_bytes = 0
    for artifact_id in retained.refs:
        candidate = retained.artifact(artifact_id)
        if candidate.role != "representation_evidence":
            continue
        scanned.append(svc.artifact_dict(candidate))
        for index, row, _ in retained.rows(candidate):
            if source_id is None or row.get("source_record_id") != source_id:
                continue
            if not row.get("observation_sha256"):
                unqualified += 1
                continue
            if row["observation_sha256"] != identity["observation_sha256"]:
                continue
            response_bytes += len(json.dumps(row).encode())
            if len(matches) >= 256 or response_bytes > 16 * 1024**2:
                raise svc.LabError("matched representation response exceeds inspection budget; export artifacts", 413)
            matches.append({"artifact_id": candidate.id, "artifact_sha256": candidate.sha256,
                            "row_index": index, "record": row})
    return {"identity": identity, "observation": selected, "observation_jsonl": selected_text, "matches": matches,
            "legacy_unqualified_records": unqualified, "artifacts": scanned,
            "status": "matched" if matches else "unmatched",
            "qualification": "Exact source_record_id and canonical observation fingerprint only; legacy rows without fingerprints are unqualified."}


def _label(row, axis):
    value = row.get(axis)
    if value is None or value == "":
        return "Not recorded"
    # Preserve structured authored labels instead of inferring categories.
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True, separators=(",", ":"))


def coverage(db, project, run_id, row_axis="scenario_id", column_axis="partition"):
    if row_axis not in AXES or column_axis not in AXES:
        raise svc.LabError("unsupported coverage axis; choose " + ", ".join(AXES))
    retained = _Retained(db, project, run_id)
    counts, partitions = Counter(), Counter()
    labels = {row_axis: set(), column_axis: set()}
    cells, artifacts = {}, []
    response_bytes = 0
    for artifact_id in retained.refs:
        artifact = retained.artifact(artifact_id)
        if artifact.role != "transformed_dataset":
            continue
        artifacts.append(svc.artifact_dict(artifact))
        for index, row, _ in retained.rows(artifact):
            counts["records"] += 1
            counts["observed"] += row.get("observed") is True
            counts["estimated"] += row.get("estimated") is True
            mask = row.get("missing_mask")
            missing = (row.get("observed") is False or "value" in row and row["value"] is None
                       or mask is True or isinstance(mask, (list, dict)) and any(mask.values() if isinstance(mask, dict) else mask))
            counts["missing"] += bool(missing)
            counts["observation_status_not_recorded"] += not any(row.get(key) is True for key in ("observed", "estimated")) and not missing
            try:
                partition = partition_label(row, allow_truth=True, allow_unassigned=True)
            except ValueError:
                partition = "Invalid / conflicting"
            partitions[partition or "Not recorded"] += 1
            pair = (_label(row, row_axis), _label(row, column_axis))
            for axis, value in zip((row_axis, column_axis), pair):
                labels[axis].add(value)
                if len(labels[axis]) > MAX_AXIS_VALUES:
                    raise svc.LabError(f"coverage axis {axis} exceeds 256 values; choose a lower-cardinality axis or export", 413)
            cell = cells.setdefault(pair, {"row": pair[0], "column": pair[1], "count": 0, "sources": []})
            cell["count"] += 1
            if len(cell["sources"]) < 8:
                response_bytes += len(json.dumps(row).encode()) + 512
                if response_bytes > 16 * 1024**2:
                    raise svc.LabError("coverage evidence response exceeds 16 MiB; export artifacts", 413)
                cell["sources"].append({"identity": retained.identity(artifact, index, row), "record": row})
    # Bound links as well as scans: aggregate counts are complete, links are explicit samples.
    if len(json.dumps(list(cells.values()), default=str)) > 16 * 1024**2:
        raise svc.LabError("coverage evidence response exceeds 16 MiB; export artifacts", 413)
    return {"run_id": run_id, "row_axis": row_axis, "column_axis": column_axis,
            "rows": sorted(labels[row_axis]), "columns": sorted(labels[column_axis]),
            "counts": dict(counts), "partition_counts": dict(partitions),
            "cells": list(cells.values()), "artifacts": artifacts, "complete": True,
            "qualification": "All retained transformed rows scanned; observation flags may overlap. Up to eight exact source links per cell; counts are complete."}
