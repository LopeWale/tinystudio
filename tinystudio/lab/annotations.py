"""Authored labels and supervision bound to the exact observation reviewed."""
from __future__ import annotations

from copy import deepcopy

from tinystudio.core.data import validate_datapoint
from .contracts import canonical, checksum
from .signals import number
from .clocks import CLOCK_FIELDS


OBSERVATION_FIELDS = (
    "record_id", "source_record_id", "source_record_ids", "source_artifact_id", "source_sha256",
    "time", "event_time", "decision_at", "timeline_basis", "available_at", "clock_domain", "clock_units",
    "clock_alignment", "value", "units", "observed", "estimated", "missing_mask", "source_missing_mask",
    "future_aware", "quality", "features", "field_units", "fit_dependencies", "fit_availability", "availability_qualification",
    *CLOCK_FIELDS,
)
LABEL_KINDS = {"human", "simulator_assertion", "solver", "reviewed_teacher", "imported"}


def observation_checksum(row):
    return checksum({field: row[field] for field in OBSERVATION_FIELDS if field in row})


def apply_annotations(records, annotations):
    if not isinstance(annotations, list) or len(annotations) > 100000:
        raise ValueError("annotations must be a bounded list of explicit record mappings")
    output = [{**row, "observation_sha256": observation_checksum(row)} for row in records]
    by_id = {row["record_id"]: row for row in output}
    if len(by_id) != len(output):
        raise ValueError("annotations require unique transformed record identities")
    applied, entries = {}, []
    for annotation in annotations:
        if not isinstance(annotation, dict) or annotation.keys() - {
            "record_id", "observation_sha256", "labels", "messages", "label_source", "target_available_at"
        }:
            raise ValueError("annotation contains unsupported fields")
        identity = annotation.get("record_id")
        if not isinstance(identity, str) or identity not in by_id:
            raise ValueError("annotation references a missing transformed record")
        if identity in applied:
            raise ValueError("duplicate annotation for a transformed record")
        row = by_id[identity]
        if annotation.get("observation_sha256") != row["observation_sha256"]:
            raise ValueError("annotation observation checksum changed; review the transformed observation again")
        source = annotation.get("label_source")
        if (not isinstance(source, dict) or not isinstance(source.get("kind"), str) or source["kind"] not in LABEL_KINDS
                or any(not isinstance(source.get(field), str) or not source[field].strip() for field in ("actor", "reference"))):
            raise ValueError("annotation label source requires a declared kind, actor and evidence reference")
        if "labels" not in annotation and "messages" not in annotation:
            raise ValueError("annotation requires explicit labels or authored messages")
        if len(canonical(annotation)) > 65536:
            raise ValueError("one annotation exceeds the 64 KiB limit")
        if "labels" in annotation:
            if not isinstance(annotation["labels"], dict) or not annotation["labels"]:
                raise ValueError("annotation labels must be a nonempty object")
            row["labels"] = deepcopy(annotation["labels"])
            row["label_source"] = deepcopy(source)
        if "target_available_at" in annotation:
            row["target_available_at"] = number(annotation["target_available_at"], "annotation target availability")
        digest = checksum(annotation)
        if "messages" in annotation:
            validate_datapoint({"messages": annotation["messages"]})
            row["messages"] = deepcopy(annotation["messages"])
            row["message_label_source"] = deepcopy(source)
            row["supervision_status"] = {"status": "explicitly_mapped", "annotation_sha256": digest,
                                         "observation_sha256": row["observation_sha256"]}
        applied[identity] = deepcopy(annotation)
        entries.append({"record_id": identity, "observation_sha256": row["observation_sha256"],
                        "annotation_sha256": digest, "label_source": deepcopy(source),
                        "fields": [field for field in ("labels", "messages", "target_available_at") if field in annotation]})
    return output, applied, {"applied": len(applied), "entries": entries,
        "correctness": "authored label-source claims; not independent verification of target correctness"}
