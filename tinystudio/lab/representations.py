"""Explicit, immutable codecs for matched engineering observations.

Numeric text preserves the normalized binary float. Original source spellings
belong to retained acquisition artifacts. Binary wire data never passes through
a text decoder or a replacement character.
"""
from __future__ import annotations

import base64
import binascii
from bisect import bisect_right
from dataclasses import dataclass
import hashlib
import json

from .annotations import observation_checksum
from .learning import ByteTokenizer
from .partitioning import source_protection
from .signals import integer, number


OBSERVATION_FIELDS = ("time", "event_time", "decision_at", "timeline_basis", "available_at", "clock_domain", "value", "units",
                      "observed", "estimated", "missing_mask", "quality", "features", "field_units",
                      "future_aware", "value_origin", "estimation_method", "fit_availability", "availability_qualification")
KINDS = {"numeric_text", "raw_bytes", "decoded_protocol", "engineering_state", "hybrid"}
TRUTH_FIELDS = {"ground_truth", "true_value", "injected_fault"}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _checked_fields(value):
    if isinstance(value, dict):
        if TRUTH_FIELDS.intersection(value):
            raise ValueError("decoded model input cannot include truth fields")
        for child in value.values():
            _checked_fields(child)
    elif isinstance(value, list):
        for child in value:
            _checked_fields(child)
    return value


def _wire(row):
    values = []
    try:
        if "wire_hex" in row:
            value = row["wire_hex"]
            if not isinstance(value, str) or len(value) > 131072 or len(value) % 2 or any(char not in "0123456789abcdefABCDEF" for char in value):
                raise ValueError("wire_hex must contain complete hexadecimal octets")
            values.append(bytes.fromhex(value))
        if "wire_base64" in row:
            value = row["wire_base64"]
            if not isinstance(value, str) or len(value) > 87384:
                raise ValueError("wire_base64 exceeds the bounded wire evidence limit")
            values.append(base64.b64decode(value, validate=True))
    except (binascii.Error, TypeError, ValueError) as exc:
        raise ValueError("wire evidence must contain valid hexadecimal or strict base64 octets") from exc
    if not values:
        raise ValueError("raw_bytes requires recorded wire_hex or wire_base64 evidence")
    if any(value != values[0] for value in values):
        raise ValueError("wire encodings disagree")
    if len(values[0]) > 65536:
        raise ValueError("wire evidence exceeds the 64 KiB limit")
    return values[0]


@dataclass(frozen=True, init=False)
class RepresentationEncoder:
    """Own validated codec configuration; never fit on the observations encoded."""

    _configuration: str

    def __init__(self, specification):
        if isinstance(specification, str):
            specification = {"kind": specification}
        if not isinstance(specification, dict):
            raise ValueError("representation specification must be an object or kind")
        unknown = specification.keys() - {"kind", "name", "max_tokens", "truncation", "vocabulary",
                                           "field", "bins", "description_field"}
        if unknown:
            raise ValueError(f"unsupported representation option: {sorted(unknown)[0]}")
        config = json.loads(_json(specification))
        if config.get("kind") not in KINDS:
            raise ValueError("unsupported representation kind")
        name = config.setdefault("name", config["kind"])
        if not isinstance(name, str) or not name or len(name) > 64 or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in name):
            raise ValueError("representation name must be a short identifier")
        if "max_tokens" in config:
            config["max_tokens"] = integer(config["max_tokens"], "max_tokens", 1, 65536)
        if config.setdefault("truncation", "error") not in {"error", "prefix"}:
            raise ValueError("representation truncation must be error or prefix")
        vocabulary = config.get("vocabulary", [])
        if (not isinstance(vocabulary, list) or len(vocabulary) > 1024
                or any(not isinstance(word, str) or not word or len(word.encode()) > 256 for word in vocabulary)):
            raise ValueError("representation vocabulary must contain bounded nonempty lexemes")
        config["vocabulary"] = sorted(set(vocabulary), key=lambda word: (-len(word.encode()), word))
        if config["kind"] == "raw_bytes" and vocabulary:
            raise ValueError("raw_bytes uses fixed octet token IDs; text vocabulary does not apply")
        if "field" in config and config["kind"] != "decoded_protocol":
            raise ValueError("field applies only to decoded_protocol")
        if "description_field" in config and config["kind"] != "hybrid":
            raise ValueError("description_field applies only to hybrid")
        for field in ("field", "description_field"):
            if field in config and (not isinstance(config[field], str) or not config[field] or config[field] in TRUTH_FIELDS):
                raise ValueError("representation field must name non-truth source evidence")
        if config["kind"] == "engineering_state":
            bins = config.get("bins")
            if not isinstance(bins, dict) or bins.keys() - {"boundaries", "labels", "units", "missing_label"}:
                raise ValueError("engineering_state requires explicit bin boundaries, labels and units")
            boundaries, labels = bins.get("boundaries"), bins.get("labels")
            if not isinstance(boundaries, list) or len(boundaries) > 256:
                raise ValueError("engineering bin boundaries must be a bounded list")
            boundaries = [number(value, "bin boundary") for value in boundaries]
            if any(left >= right for left, right in zip(boundaries, boundaries[1:])):
                raise ValueError("engineering bin boundaries must strictly increase")
            if (not isinstance(labels, list) or len(labels) != len(boundaries) + 1
                    or any(not isinstance(label, str) or not label or len(label) > 128 for label in labels)
                    or len(set(labels)) != len(labels)):
                raise ValueError("engineering bins require one unique label per interval")
            if not isinstance(bins.get("units"), str) or not bins["units"]:
                raise ValueError("engineering bins require explicit units")
            missing = bins.setdefault("missing_label", "<missing>")
            if not isinstance(missing, str) or not missing or len(missing) > 128:
                raise ValueError("engineering missing label must be bounded text")
            bins["boundaries"] = boundaries
        elif "bins" in config:
            raise ValueError("bins apply only to engineering_state representations")
        object.__setattr__(self, "_configuration", _json(config))

    @property
    def specification(self):
        return json.loads(self._configuration)

    @property
    def specification_hash(self):
        return hashlib.sha256(self._configuration.encode()).hexdigest()

    def encode(self, row):
        config = self.specification
        kind = config["kind"]
        observation = _checked_fields({field: row[field] for field in OBSERVATION_FIELDS if field in row})
        quantized = kind == "engineering_state"
        if kind == "raw_bytes":
            raw = _wire(row)
            tokenizer = None
            tokens = list(raw)
        else:
            if kind == "numeric_text":
                payload = observation
            elif kind == "decoded_protocol":
                fields = row.get(config.get("field", "decoded_protocol"))
                if not isinstance(fields, dict) or not fields:
                    raise ValueError("decoded_protocol requires recorded decoded fields")
                payload = {"protocol": row.get("protocol"), "fields": _checked_fields(fields)}
            elif kind == "engineering_state":
                bins = config["bins"]
                if row.get("units") != bins["units"]:
                    raise ValueError("engineering bin units do not match the observation")
                value = row.get("value")
                label = bins["missing_label"] if value is None else bins["labels"][bisect_right(bins["boundaries"], number(value, "state value"))]
                payload = {"state": label, "units": bins["units"]}
            else:
                payload = {"observation": observation}
                if "wire_hex" in row or "wire_base64" in row:
                    payload["wire_hex"] = _wire(row).hex()
                if "decoded_protocol" in row:
                    payload["decoded_protocol"] = _checked_fields(row["decoded_protocol"])
                if config.get("description_field"):
                    description = row.get(config["description_field"])
                    if not isinstance(description, str):
                        raise ValueError("hybrid description requires authored source text")
                    payload["description"] = description
            text = _json(payload)
            raw = text.encode()
            if len(raw) > 65536:
                raise ValueError("one representation exceeds the 64 KiB evidence limit")
            tokenizer = ByteTokenizer("engineering", vocabulary=config["vocabulary"]) if config["vocabulary"] else ByteTokenizer()
            tokens = tokenizer.encode(text)
        if len(raw) > 65536:
            raise ValueError("one representation exceeds the 64 KiB evidence limit")
        full_count, full_sha = len(tokens), hashlib.sha256(raw).hexdigest()
        maximum = config.get("max_tokens", 65536)
        if full_count > maximum and config["truncation"] == "error":
            raise ValueError("representation exceeds its token limit; select explicit prefix truncation or a larger budget")
        tokens = tokens[:maximum]
        actual = bytes(tokens) if tokenizer is None else b"".join(tokenizer.pieces[token] for token in tokens)
        try:
            display = actual.decode("utf-8")
        except UnicodeDecodeError:
            display = None
        return {
            "schema_version": 1, "observation_sha256": observation_checksum(row),
            "representation_id": config["name"], "representation_kind": kind,
            "specification_hash": self.specification_hash,
            "source_record_id": row["source_record_id"],
            "source_record_ids": list(row.get("source_record_ids", [row["source_record_id"]])),
            "partition": row.get("partition", "unassigned"), "visibility": source_protection(row),
            "time": row.get("time"), "available_at": row.get("available_at"), "clock_domain": row.get("clock_domain"),
            "observation": observation, "model_input_text": display,
            "model_input_base64": base64.b64encode(actual).decode("ascii"),
            "encoding": "wire-bytes" if kind == "raw_bytes" else "utf8-json",
            "token_ids": tokens, "token_count": len(tokens), "full_token_count": full_count,
            "input_sha256": hashlib.sha256(actual).hexdigest(), "untruncated_sha256": full_sha,
            "loss": {"truncated": full_count > maximum, "omitted_tokens": full_count - len(tokens),
                     "numeric_quantized": quantized,
                     "excluded_fields": [field for field in OBSERVATION_FIELDS if field in observation]
                                        if kind in {"raw_bytes", "decoded_protocol"} else
                                        [field for field in OBSERVATION_FIELDS if field in observation and field != "units"]
                                        if quantized else []},
        }
