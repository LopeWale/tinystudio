"""Frozen study inputs and researcher-selected observations/inference units."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import re
import statistics

from .analysis_methods import ALTERNATIVES, METHODS
from .contracts import canonical, checksum
from .engine import ExecutionCancelled
from .studies import FORMAT as TRACKING_FORMAT
from .study_contracts import finite_document

FORMAT = "tinystudio.analysis-selection.v1"
ANALYSIS_FORMAT = "tinystudio.study-analysis.v1"
CUSTOM_METHOD = "custom.python"
REPEATED_POLICIES = ("error", "mean", "median", "first", "last")
MISSING_POLICIES = ("error", "omit")
_MISSING = object()


def implementation_sha256():
    files = ("analysis.py", "analysis_selection.py", "analysis_methods.py", "analysis_process.py")
    return hashlib.sha256(b"".join(Path(__file__).with_name(name).read_bytes() for name in files)).hexdigest()


def request_schema(method):
    """Machine-readable authoring contract shared by every client catalog."""
    strings = {"type": "array", "items": {"type": "string", "minLength": 1}, "uniqueItems": True}
    properties = {"method": {"const": method}, "selection": {"type": "object", "additionalProperties": False,
        "required": ["mode"], "properties": {"mode": {"enum": ["all", "ids"]},
            **{key: deepcopy(strings) for key in ("record_ids", "exclude_ids", "kinds")}}},
        "notes": {}, "assumptions": {}, "input_sources": {}}
    required = ["method", "selection"]
    if method == CUSTOM_METHOD:
        properties.update(custom_method={"type": "object", "required": ["name", "version"],
            "properties": {"name": {"type": "string", "minLength": 1}, "version": {"type": "string", "minLength": 1},
                "assumptions": {"type": "array", "items": {"type": "string"}},
                "references": {"type": "array", "items": {"type": "string"}}}}, options={"type": "object"})
        required += ["custom_method"]
    else:
        properties.update(confidence={"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1},
            alternative={"enum": list(ALTERNATIVES)}, groups={"type": "array", "minItems": 2, "maxItems": 2},
            measurement_unit={"type": "string"}, repeated={"enum": list(REPEATED_POLICIES)}, missing={"enum": list(MISSING_POLICIES)},
            **{key: {"type": "string", "pattern": "^/", "description": "JSON Pointer into the retained record, with ~0 and ~1 escapes."}
               for key in ("outcome_path", "condition_path", "unit_path")})
        required += ["confidence", "alternative", "groups", "measurement_unit", "repeated", "missing", "outcome_path", "condition_path", "unit_path"]
        if method == "means.paired":
            properties["incomplete_pairs"] = {"enum": list(MISSING_POLICIES)}
            required += ["incomplete_pairs"]
    return {"type": "object", "additionalProperties": False, "required": required, "properties": properties}


@dataclass(frozen=True)
class AnalysisPaths:
    root: Path

    @property
    def observations(self):
        return self.root / "analysis_observations.jsonl"

    @property
    def selection(self):
        return self.root / "analysis_selection.jsonl"

    @property
    def units(self):
        return self.root / "analysis_units.jsonl"

    @property
    def report(self):
        return self.root / "statistical_analysis.json"

    @property
    def custom_input(self):
        return self.root / "analysis_custom_input.jsonl"

    @property
    def custom_output(self):
        return self.root / "analysis_custom_output.json"

    @property
    def custom_outputs(self):
        return self.root / "analysis_custom_outputs"


@dataclass(frozen=True)
class PreparedSelection:
    request: dict
    source: dict
    paths: AnalysisPaths
    group_a: list[float]
    group_b: list[float]
    counts: dict
    diagnostics: list[dict]


def _check(cancelled):
    if cancelled():
        raise ExecutionCancelled("study analysis cancelled")


def _pointer(document, path):
    current = document
    for token in path[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            current = current.get(token, _MISSING)
        elif isinstance(current, list) and re.fullmatch(r"0|[1-9][0-9]*", token) and int(token) < len(current):
            current = current[int(token)]
        else:
            return _MISSING
    return current


def normalize_request(parameters):
    if not isinstance(parameters, dict):
        raise ValueError("analysis parameters must be an object")
    request = deepcopy(parameters)
    common = {"method", "selection", "notes", "assumptions", "input_sources"}
    builtin = {"confidence", "alternative", "groups", "outcome_path", "condition_path", "unit_path",
               "measurement_unit", "repeated", "missing", "incomplete_pairs"}
    custom = {"custom_method", "options"}
    method = request.get("method")
    if not isinstance(method, str) or method not in METHODS and method != CUSTOM_METHOD:
        raise ValueError("analysis method is not installed; inspect the observed-data method catalog")
    allowed = common | (custom if method == CUSTOM_METHOD else builtin)
    if set(request) - allowed:
        raise ValueError("unknown analysis parameters: " + ", ".join(sorted(set(request)-allowed)))
    selected = request.get("selection")
    if not isinstance(selected, dict) or set(selected) - {"mode", "record_ids", "exclude_ids", "kinds"}:
        raise ValueError("an explicit observation selection is required")
    if selected.get("mode") not in {"all", "ids"}:
        raise ValueError("selection mode must explicitly select all records at the snapshot boundary or record ids")
    for key in ("record_ids", "exclude_ids", "kinds"):
        values = selected.setdefault(key, [])
        if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values) or len(set(values)) != len(values):
            raise ValueError(f"selection {key} must contain unique nonempty strings")
    if selected["mode"] == "all" and selected["record_ids"]:
        raise ValueError("all-record selection cannot also specify record ids")
    if method == CUSTOM_METHOD:
        descriptor = request.get("custom_method")
        if not isinstance(descriptor, dict) or any(not isinstance(descriptor.get(key), str) or not descriptor[key].strip() for key in ("name", "version")):
            raise ValueError("custom analysis must declare its method name and version")
        for key in ("assumptions", "references"):
            values = descriptor.get(key, [])
            if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                raise ValueError(f"custom method {key} must be an array of strings")
        if not isinstance(request.get("options", {}), dict):
            raise ValueError("custom analysis options must be an object")
    else:
        confidence = request.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 < confidence < 1:
            raise ValueError("confidence must be an explicit finite probability between zero and one")
        if request.get("alternative") not in ALTERNATIVES:
            raise ValueError("choose an explicit interval alternative")
        groups = request.get("groups")
        if not isinstance(groups, list) or len(groups) != 2 or canonical(groups[0]) == canonical(groups[1]):
            raise ValueError("the selected two-group method requires two distinct condition values")
        for key in ("outcome_path", "condition_path", "unit_path"):
            path = request.get(key)
            if not isinstance(path, str) or not path.startswith("/") or re.search(r"~(?![01])", path):
                raise ValueError(f"{key} must be a JSON Pointer")
        if not isinstance(request.get("measurement_unit"), str):
            raise ValueError("declare the outcome measurement unit; use an empty string for dimensionless quantities")
        if request.get("repeated") not in REPEATED_POLICIES:
            raise ValueError("choose an explicit repeated-measurement reduction policy")
        if request.get("missing") not in MISSING_POLICIES:
            raise ValueError("choose an explicit missing-value policy")
        if method == "means.paired" and request.get("incomplete_pairs") not in MISSING_POLICIES:
            raise ValueError("choose an explicit incomplete-pair policy")
        if method != "means.paired" and "incomplete_pairs" in request:
            raise ValueError("incomplete-pair policy applies only to paired analysis")
    finite_document(request)  # Preserve the shared scientific JSON number contract.
    return request


def validate_analysis_selection(definition):
    document = definition.model_dump() if hasattr(definition, "model_dump") else definition
    request = normalize_request(document.get("parameters"))
    if document.get("mode", "offline") != "offline" or document.get("pipeline"):
        raise ValueError("observed analysis consumes a frozen selection; retain preparation transformations in a separate curation run")
    refs = document.get("inputs", [])
    allowed = {"study_snapshot", "analysis_code", "analysis_auxiliary"} if request["method"] == CUSTOM_METHOD else {"study_snapshot"}
    if any(ref.get("role") not in allowed for ref in refs):
        raise ValueError("input role is not used by the selected analysis method")
    if sum(ref.get("role") == "study_snapshot" for ref in refs) != 1:
        raise ValueError("analysis requires exactly one frozen study snapshot input")
    if request["method"] == CUSTOM_METHOD and sum(ref.get("role") == "analysis_code" for ref in refs) != 1:
        raise ValueError("custom analysis requires exactly one retained analysis_code input")
    return request


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field in study snapshot")
        result[key] = value
    return result


def _tracking_records(path, source, cancelled):
    prefix = hashlib.sha256()
    full = hashlib.sha256()
    revisions, ids = {}, set()
    header, count, summary = None, 0, False
    with Path(path).open("rb") as stream:
        for raw in stream:
            _check(cancelled)
            if summary:
                raise ValueError("trailing bytes after study export summary")
            full.update(raw)
            document = json.loads(raw, object_pairs_hook=_object,
                parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON in study snapshot")))
            if not isinstance(document, dict):
                raise ValueError("study snapshot document must be an object")
            kind = document.get("kind")
            if header is None:
                if kind != "manifest" or document.get("format") != TRACKING_FORMAT:
                    raise ValueError("analysis requires a retained study tracking snapshot")
                if (not isinstance(document.get("study_id"), str) or not document["study_id"] or
                        type(document.get("revision")) is not int or document["revision"] < 1 or
                        type(document.get("through_seq")) is not int or document["through_seq"] < 0):
                    raise ValueError("invalid study snapshot boundary")
                header = document
            elif kind == "protocol" and count == 0:
                saved = document.get("snapshot")
                if not isinstance(saved, dict):
                    raise ValueError("invalid study protocol identity")
                number = saved.get("revision")
                if type(number) is not int or number != len(revisions)+1 or saved.get("study_id") != header["study_id"] or checksum(saved) != document.get("digest"):
                    raise ValueError("study protocol digest or identity mismatch")
                revisions[number] = document["digest"]
            elif kind == "record":
                record = document.get("record")
                if not isinstance(record, dict):
                    raise ValueError("invalid study record identity")
                body = {key: value for key, value in record.items() if key != "digest"}
                if checksum(body) != record.get("digest"):
                    raise ValueError("study record digest mismatch")
                if (not isinstance(record.get("id"), str) or not record["id"] or
                        record.get("study_id") != header["study_id"] or type(record.get("seq")) is not int or
                        record["seq"] != count+1 or record["id"] in ids):
                    raise ValueError("study record identity or sequence mismatch")
                if type(record.get("revision")) is not int or record.get("protocol_digest") != revisions.get(record["revision"]):
                    raise ValueError("study record protocol digest mismatch")
                if record.get("supersedes") is not None and (not isinstance(record["supersedes"], str) or record["supersedes"] not in ids):
                    raise ValueError("study correction must reference an earlier record identity")
                ids.add(record["id"])
                count += 1
                prefix.update(raw)
                yield record
                continue
            elif kind == "export_summary":
                if (document.get("sha256") != prefix.hexdigest() or count != header["through_seq"] or
                        type(document.get("records")) is not int or type(document.get("protocol_revisions")) is not int or
                        document.get("records") != count or document.get("protocol_revisions") != header["revision"] or
                        len(revisions) != header["revision"] or revisions.get(header["revision"]) != header.get("protocol_digest")):
                    raise ValueError("study snapshot checksum or history boundary mismatch")
                summary = True
                source.update({**header, "sha256": full.hexdigest()})
                continue
            else:
                raise ValueError("invalid study tracking snapshot document order")
            prefix.update(raw)
    if not summary:
        raise ValueError("study snapshot is missing its checksum summary")


def validate_snapshot(path, cancelled=lambda: False):
    """Verify every retained protocol, record, boundary and the full snapshot SHA."""
    source = {}
    for _ in _tracking_records(path, source, cancelled):
        pass
    return source


def prepare_selection(snapshot, parameters, directory, cancelled=lambda: False):
    request = normalize_request(parameters)
    paths = AnalysisPaths(Path(directory))
    paths.root.mkdir(parents=True, exist_ok=True)
    source, decisions, groups, selected_ids, supersessions, created = {}, [], {"a": {}, "b": {}}, set(), [], []
    requested = set(request["selection"]["record_ids"])
    excluded = set(request["selection"]["exclude_ids"])
    remaining = requested | excluded
    kinds = set(request["selection"]["kinds"])
    custom = request["method"] == CUSTOM_METHOD
    diagnostics, rounded = [], 0
    try:
        with paths.observations.open("xb") as retained:
            created.append(paths.observations)
            for record in _tracking_records(snapshot, source, cancelled):
                _check(cancelled)
                identity = record["id"]
                remaining.discard(identity)
                reason = ("not_selected" if request["selection"]["mode"] == "ids" and identity not in requested else
                          "explicit_exclusion" if identity in excluded else
                          "record_kind" if kinds and record.get("kind") not in kinds else None)
                decision = {"kind": "decision", "record_id": identity, "record_digest": record["digest"],
                            "selected": reason is None, "used": reason is None, "reason": reason}
                if reason is None and not custom:
                    condition = _pointer(record, request["condition_path"])
                    group = next((name for name, value in zip(("a", "b"), request["groups"])
                                  if condition is not _MISSING and canonical(condition) == canonical(value)), None)
                    unit = _pointer(record, request["unit_path"])
                    outcome = _pointer(record, request["outcome_path"])
                    reason = "other_condition" if group is None else "missing_unit" if unit is _MISSING or unit is None or unit == "" else None
                    value = None
                    if reason is None:
                        try:
                            if isinstance(outcome, bool) or outcome is _MISSING or outcome is None:
                                raise ValueError("not a numeric outcome")
                            value = float(outcome)
                            if not math.isfinite(value):
                                raise ValueError("not a finite outcome")
                        except (ValueError, TypeError, OverflowError):
                            reason = "missing_outcome"
                    if reason is not None and reason != "other_condition" and request["missing"] == "error":
                        raise ValueError(f"record {identity} has a {reason.replace('_', ' ')}")
                    if reason is None:
                        unit_key = canonical(unit).decode()
                        entries = groups[group].setdefault(unit_key, [])
                        if entries and request["repeated"] == "error":
                            raise ValueError("repeated observations require an explicit reduction choice")
                        entries.append({"record_id": identity, "value": value, "unit": unit})
                        conversion = {"source_type": type(outcome).__name__, "target": "float64", "rounded": False,
                                      "float64_hex": value.hex()}
                        if isinstance(outcome, (str, int)):
                            exact = Decimal(outcome)
                            conversion.update(source_decimal=str(exact), rounded=Decimal.from_float(value) != exact)
                        rounded += int(conversion["rounded"])
                        decision.update(group=group, unit_key=unit_key, value=value, numeric_conversion=conversion)
                    else:
                        decision.update(selected=False, used=False, reason=reason)
                if decision["selected"]:
                    retained.write(canonical(record)+b"\n")
                    selected_ids.add(identity)
                    if record.get("supersedes"):
                        supersessions.append(record["supersedes"])
                decisions.append(decision)
        if remaining:
            raise ValueError("selected or excluded record ids were not found in the snapshot")
        if rounded:
            diagnostics.append({"code": "float64_rounding", "records": rounded,
                "message": "Decimal outcomes were rounded for the built-in float64 method. Original values and exact conversion receipts are retained; custom methods can use different precision."})
        if any(identity in selected_ids for identity in supersessions):
            diagnostics.append({"code": "included_corrections", "message": "Both superseded and correcting records were explicitly included; no automatic replacement was applied."})
        if not custom and request["method"] == "means.paired":
            unpaired = set(groups["a"]) ^ set(groups["b"])
            if unpaired and request["incomplete_pairs"] == "error":
                raise ValueError("incomplete pairs require an explicit omission choice")
            for decision in decisions:
                if decision.get("unit_key") in unpaired:
                    decision.update(used=False, reason="incomplete_pair")
            for group in groups.values():
                for key in unpaired:
                    group.pop(key, None)
            if unpaired:
                diagnostics.append({"code": "incomplete_pairs_omitted", "message": "Incomplete pairs were omitted under the retained researcher policy.", "units": len(unpaired)})
        elif not custom and set(groups["a"]) & set(groups["b"]):
            diagnostics.append({"code": "overlapping_unit_ids", "message": "Selected independent groups share declared unit identifiers; group independence is a researcher assumption, not established by those IDs."})
        reduced = {"a": {}, "b": {}}
        used_ids = set()
        with paths.units.open("xb") as units:
            created.append(paths.units)
            for name, group in groups.items():
                for key, entries in group.items():
                    _check(cancelled)
                    values = [item["value"] for item in entries]
                    policy = request["repeated"]
                    if policy in {"error", "first", "last"}:
                        chosen = entries[-1:] if policy == "last" else entries[:1]
                        value = chosen[0]["value"]
                    else:
                        chosen = entries
                        ordered = sorted(values) if policy == "median" else values
                        value = statistics.mean(ordered[(len(ordered)-1)//2:len(ordered)//2+1]) if policy == "median" else statistics.mean(values)
                    ids = [item["record_id"] for item in chosen]
                    used_ids.update(ids)
                    reduced[name][key] = value
                    units.write(canonical({"group": name, "unit": entries[0]["unit"], "value": value,
                                           "reduction": policy, "record_ids": ids})+b"\n")
        for decision in decisions:
            if decision["selected"] and not custom and decision["used"] and decision["record_id"] not in used_ids:
                decision.update(used=False, reason="reduction_not_selected")
        counts = {"source_records": len(decisions), "selected_records": len(selected_ids),
                  "used_records": sum(row["used"] for row in decisions), "group_a": len(reduced["a"]), "group_b": len(reduced["b"])}
        with paths.selection.open("xb") as output:
            created.append(paths.selection)
            output.write(canonical({"kind": "manifest", "format": FORMAT, "source": source, "request": request,
                                     "request_sha256": checksum(request), "numeric_type": "float64" if not custom else "custom_defined"})+b"\n")
            for decision in decisions:
                _check(cancelled)
                output.write(canonical(decision)+b"\n")
            output.write(canonical({"kind": "selection_summary", "counts": counts, "diagnostics": diagnostics})+b"\n")
        keys = sorted(reduced["a"]) if request["method"] == "means.paired" else list(reduced["a"])
        group_a = [reduced["a"][key] for key in keys]
        group_b = [reduced["b"][key] for key in keys] if request["method"] == "means.paired" else list(reduced["b"].values())
        return PreparedSelection(request, source, paths, group_a, group_b, counts, diagnostics)
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise
