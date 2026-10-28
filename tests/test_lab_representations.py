"""Matched scientific evidence must not lose precision or fabricate protocol data."""
import base64
import json
import struct

import pytest

from tinystudio.lab.representations import RepresentationEncoder


def observation(**changes):
    return {"source_record_id": "capture:4", "record_id": "capture:4",
            "source_record_ids": ["capture:4"], "partition": "test",
            "time": 0.125, "available_at": 0.25, "clock_domain": "sensor",
            "value": -1.2345678901234567, "units": "V", "observed": True,
            "estimated": False, "missing_mask": {"value": False}, **changes}


def test_numeric_input_round_trips_normalized_float_and_negative_zero():
    encoder = RepresentationEncoder({"kind": "numeric_text"})
    for value in (-1.2345678901234567, -0.0, 1.0000000000000002, None):
        encoded = encoder.encode(observation(value=value))
        decoded = json.loads(encoded["model_input_text"])
        if value is None:
            assert decoded["value"] is None
        else:
            assert struct.pack("!d", decoded["value"]) == struct.pack("!d", value)
        assert decoded["units"] == "V" and decoded["missing_mask"] == {"value": False}
        assert bytes(encoded["token_ids"]) == encoded["model_input_text"].encode()
        assert encoded["loss"]["numeric_quantized"] is False
        assert encoded["partition"] == "test" and encoded["visibility"] == "evaluation"


def test_binary_input_preserves_non_utf8_octets_without_replacement():
    result = RepresentationEncoder({"kind": "raw_bytes"}).encode(observation(wire_hex="0080ff"))
    assert result["token_ids"] == [0, 128, 255]
    assert base64.b64decode(result["model_input_base64"], validate=True) == b"\x00\x80\xff"
    assert result["model_input_text"] is None
    assert result["encoding"] == "wire-bytes"
    assert result["loss"]["truncated"] is False


@pytest.mark.parametrize("wire", [{"wire_hex": "0"}, {"wire_hex": "00zz"},
                                   {"wire_base64": "AID/!"}, {"wire_hex": "00", "wire_base64": "/w=="}])
def test_malformed_or_conflicting_wire_encodings_are_rejected(wire):
    with pytest.raises(ValueError, match="wire"):
        RepresentationEncoder({"kind": "raw_bytes"}).encode(observation(**wire))


def test_numeric_serialization_is_never_mislabeled_as_protocol_bytes():
    with pytest.raises(ValueError, match="recorded wire"):
        RepresentationEncoder({"kind": "raw_bytes"}).encode(observation())


def test_truncation_requires_selection_and_records_actual_input():
    row = observation(wire_hex="0080ff")
    with pytest.raises(ValueError, match="token limit"):
        RepresentationEncoder({"kind": "raw_bytes", "max_tokens": 2}).encode(row)
    result = RepresentationEncoder({"kind": "raw_bytes", "max_tokens": 2,
                                    "truncation": "prefix"}).encode(row)
    assert result["token_ids"] == [0, 128]
    assert base64.b64decode(result["model_input_base64"]) == b"\x00\x80"
    assert result["full_token_count"] == 3 and result["token_count"] == 2
    assert result["loss"]["truncated"] is True and result["loss"]["omitted_tokens"] == 1
    assert result["source_record_ids"] == ["capture:4"]


def test_decoded_protocol_is_authored_evidence_and_retains_numeric_fields():
    fields = {"function": 3, "registers": [0, 32768, 65535], "signed_offset": -0.125}
    encoder = RepresentationEncoder({"kind": "decoded_protocol"})
    result = encoder.encode(observation(decoded_protocol=fields))
    assert json.loads(result["model_input_text"])["fields"] == fields
    with pytest.raises(ValueError, match="decoded"):
        encoder.encode(observation(wire_hex="03020001"))
    with pytest.raises(ValueError, match="truth"):
        encoder.encode(observation(decoded_protocol={"nested": {"ground_truth": "fault"}}))


def test_engineering_bins_are_explicit_immutable_and_use_stable_token_ids():
    config = {"kind": "engineering_state", "bins": {"boundaries": [0, 1],
              "labels": ["<low>", "<normal>", "<high>"], "units": "V"},
              "vocabulary": ["<normal>", "<high>", "<low>"]}
    encoder = RepresentationEncoder(config)
    config["bins"]["boundaries"][0] = -100
    result = encoder.encode(observation(value=-0.125))
    assert json.loads(result["model_input_text"])["state"] == "<low>"
    assert result["loss"]["numeric_quantized"] is True
    assert "value" in result["loss"]["excluded_fields"]
    assert any(token >= 256 for token in result["token_ids"])
    alternate = RepresentationEncoder({**encoder.specification, "vocabulary": ["<low>", "<high>", "<normal>"]})
    assert alternate.encode(observation(value=-0.125))["token_ids"] == result["token_ids"]
    assert alternate.specification_hash == encoder.specification_hash
    with pytest.raises(ValueError, match="units"):
        encoder.encode(observation(units="mV"))


def test_hybrid_exposes_matched_observation_protocol_and_authored_description():
    row = observation(wire_hex="0080ff", decoded_protocol={"register": 65535},
                      description="Recorded reply", ground_truth="must not appear")
    result = RepresentationEncoder({"kind": "hybrid", "description_field": "description"}).encode(row)
    payload = json.loads(result["model_input_text"])
    assert payload["observation"]["value"] == row["value"]
    assert payload["wire_hex"] == "0080ff"
    assert payload["decoded_protocol"] == {"register": 65535}
    assert payload["description"] == "Recorded reply"
    assert "must not appear" not in result["model_input_text"]
    assert result["source_record_id"] == row["source_record_id"]


def test_encoder_does_not_silently_accept_misspelled_configuration():
    with pytest.raises(ValueError, match="unsupported representation option"):
        RepresentationEncoder({"kind": "numeric_text", "max_token": 2})


def test_pipeline_exports_five_representations_from_identical_pinned_records(tmp_path):
    import hashlib
    from tinystudio.lab.data import execute_data
    rows = []
    for partition, episode, start in [("train", "A", 0), ("validation", "B", 10), ("test", "C", 20)]:
        for index, value in enumerate((-1.2345678901234567, 0.125)):
            rows.append(observation(source_record_id=f"{episode}:{index}", episode_id=episode,
                                    time=start + index, available_at=start + index, partition=partition,
                                    value=value, wire_hex="0080ff", decoded_protocol={"register": 65535},
                                    description="Recorded reply"))
    codecs = ["numeric_text", "raw_bytes", "decoded_protocol", "hybrid",
              {"kind": "engineering_state", "bins": {"boundaries": [0], "labels": ["<low>", "<high>"],
                                                        "units": "V"}, "vocabulary": ["<low>", "<high>"]}]
    result = execute_data({"definition": {"engine": "data.pipeline", "mode": "offline",
                          "parameters": {"records": rows, "representations": codecs,
                                         "partition_policy": {"strategy": "grouped"}}}},
                         tmp_path, lambda event: None, lambda: False)
    manifest = json.loads((tmp_path / "representations.json").read_text())
    assert len(manifest["representations"]) == 5
    expected = [(row["source_record_id"], row["partition"]) for row in rows]
    for item in manifest["representations"]:
        path = tmp_path / item["artifact"]["name"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["artifact"]["sha256"]
        actual = [json.loads(line) for line in path.read_text().splitlines()]
        assert [(row["source_record_id"], row["partition"]) for row in actual] == expected
        assert {row["partition"] for row in actual if row["visibility"] == "model"} == {"train"}
        assert item["records"] == 6 and item["truncated_records"] == 0
        if item["name"] == "raw_bytes":
            assert all(row["token_ids"] == [0, 128, 255] for row in actual)
    retained = json.loads((tmp_path / "partitions.json").read_text())["retained_artifacts"]
    assert any(item["role"] == "representation_manifest" for item in retained)
    assert len([item for item in result["artifacts"] if item["role"] == "representation_evidence"]) == 5


def test_pipeline_rejects_unavailable_representation_instead_of_fabricating_wire(tmp_path):
    from tinystudio.lab.data import execute_data
    with pytest.raises(ValueError, match="recorded wire"):
        execute_data({"definition": {"engine": "data.pipeline", "mode": "offline",
                     "parameters": {"records": [{**observation(), "partition": "unassigned"}],
                                    "representations": ["raw_bytes"]}}},
                     tmp_path, lambda event: None, lambda: False)


@pytest.mark.parametrize("codecs", [["numeric_text"] * 9,
    [{"kind": "numeric_text", "name": "View"}, {"kind": "raw_bytes", "name": "view"}]])
def test_export_rejects_ambiguous_names_and_excessive_codecs(tmp_path, codecs):
    from tinystudio.lab.data import export_representations
    with pytest.raises(ValueError, match="eight|unique"):
        export_representations([observation()], codecs, tmp_path, dataset_ready=False, cancelled=lambda: False)
    assert not (tmp_path / "representations.json").exists()


def test_cancelled_representation_export_does_not_publish_a_complete_manifest(tmp_path):
    from tinystudio.lab.data import export_representations
    from tinystudio.lab.engine import ExecutionCancelled
    with pytest.raises(ExecutionCancelled, match="cancelled"):
        export_representations([observation()], ["numeric_text"], tmp_path,
                               dataset_ready=False, cancelled=lambda: True)
    assert not (tmp_path / "representations.json").exists()
