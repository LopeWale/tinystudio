"""Counterexamples for exact quantities, causal provenance and paired evaluation."""
from decimal import Decimal

import pytest

from tinystudio.lab.evaluation import (
    FORMAT, audit_leakage, compare_baselines, evaluate_examples,
    validate_numeric, validate_packet,
)


def failures(report):
    return {check["id"]: check for check in report["checks"] if not check["passed"]}


def test_numeric_preserves_sign_decimal_and_zero_without_text_normalization():
    assert not validate_numeric("-0.125", "0.125")["passed"]
    assert validate_numeric("-0.125", Decimal("-0.125"))["passed"]
    assert not validate_numeric("1.25", "125")["passed"]
    assert validate_numeric(0, "-0.000")["passed"]
    assert not validate_numeric("1,250", "1250")["passed"]
    assert not validate_numeric("−0.125", "-0.125")["passed"]
    assert not validate_numeric(True, 1)["passed"]
    assert not validate_numeric(float("nan"), 0)["passed"]
    assert not validate_numeric("Infinity", 0)["passed"]
    assert not validate_numeric("1e100000000", 1)["passed"]


def test_numeric_units_dimensions_affine_temperatures_and_intervals():
    assert validate_numeric({"value":"100", "unit":"cm"}, {"value":"1", "unit":"m"})["passed"]
    assert not validate_numeric(1, 1, actual_unit="m", expected_unit="s")["passed"]
    assert validate_numeric("0", "273.15", actual_unit="degC", expected_unit="K")["passed"]
    assert validate_numeric("32", "0", actual_unit="degF", expected_unit="degC")["passed"]
    assert validate_numeric("212", "100", actual_unit="degF", expected_unit="degC")["passed"]
    assert validate_numeric("1", "1.8", actual_unit="delta_degC", expected_unit="delta_degF")["passed"]
    assert not validate_numeric("1", "1", actual_unit="degC", expected_unit="delta_degC")["passed"]
    assert not validate_numeric("-273.16", "0", actual_unit="degC", expected_unit="K")["passed"]
    assert not validate_numeric("1", "1", actual_unit="C", expected_unit="degC")["passed"]


def test_tolerances_are_exact_and_absolute_temperature_tolerance_is_an_interval():
    assert validate_numeric("0.00000000000000000001", "0", absolute_tolerance="0.00000000000000000001")["passed"]
    assert not validate_numeric("0.00000000000000000002", "0", absolute_tolerance="0.00000000000000000001")["passed"]
    assert validate_numeric("0.5", "273.15", actual_unit="degC", expected_unit="K", absolute_tolerance="0.5")["passed"]
    assert not validate_numeric("0.6", "0", actual_unit="degC", expected_unit="degC", absolute_tolerance="0.5")["passed"]
    assert not validate_numeric(0, 0, absolute_tolerance=-1)["passed"]
    assert validate_numeric("101", "100", relative_tolerance=".01")["passed"]
    assert not validate_numeric("101.00000001", "100", relative_tolerance=".01")["passed"]


def test_protocol_exact_and_decoded_fields_preserve_punctuation_types_and_required_fields():
    assert not validate_packet(b"SET:-0.125\r\n", b"SET:0.125\r\n")["passed"]
    assert not validate_packet("A:B", "AB")["passed"]
    assert not validate_packet("SET\n", "SET\r\n")["passed"]
    assert not validate_packet(None, None)["passed"]
    assert validate_packet(b"\x01\x00", b"\x01\x00", decoded={"address":1,"value":0}, expected_decoded={"address":1,"value":0}, required_fields=["address","value"])["passed"]
    missing = validate_packet(b"\x01\x00", b"\x01\x00", decoded={"address":1}, expected_decoded={"address":1,"value":0}, required_fields=["value"])
    assert "packet:field:value" in failures(missing)
    assert not validate_packet({"ok":True}, {"ok":1})["passed"]
    assert not validate_packet("OK", "OK", required_fields=["sequence"])["passed"]


def clean_records():
    return [{"source_id":"train-a", "partition":"train", "episode_id":"episode-train", "device_id":"device-train", "scenario_id":"scenario-train", "available_at":0},
            {"source_id":"test-a", "partition":"test", "episode_id":"episode-test", "device_id":"device-test", "scenario_id":"scenario-test", "available_at":1}]


def test_leakage_clean_split_stages_and_available_at_boundary():
    report = audit_leakage(clean_records(), stages=[
        {"id":"normalizer", "kind":"preprocess", "fit_on":["train"]},
        {"id":"lookup", "kind":"retrieval", "corpus":["train-a"]},
        {"id":"policy", "kind":"reward", "reward_sources":["train-a"]},
        {"id":"prediction", "kind":"inference", "inputs":["test-a"], "inference_time":1},
    ])
    assert report["format"] == FORMAT
    assert report["passed"]
    assert report["qualification"]["status"] == "qualified"
    assert len(report["metrics"]["provenance_sha256"]) == 64


@pytest.mark.parametrize("field", ["source_id","group_id","episode_id","device_id","scenario_id"])
def test_stable_id_overlap_is_detected_for_every_split_axis(field):
    rows = clean_records()
    for row in rows:
        row[field] = "shared"
    report = audit_leakage(rows)
    assert f"split:{field}" in failures(report)
    assert report["qualification"]["status"] == "failed"


@pytest.mark.parametrize("kind,key", [("preprocess","fit_on"), ("retrieval","corpus"), ("reward","reward_sources"), ("scenario_selection","scenario_sources")])
def test_heldout_answers_cannot_enter_learned_preprocessing_retrieval_rewards_or_scenario_selection(kind,key):
    rows = clean_records()
    rows[1]["role"] = "answer"
    result = audit_leakage(rows, stages=[{"id":"consumer", "kind":kind, key:["test-a"]}])
    assert "stage:consumer:" + key + ":heldout" in failures(result)
    assert result["qualification"]["status"] == "failed"


def test_inference_future_values_and_future_training_evidence_fail_causality():
    rows = clean_records()
    rows[1].update(time=0, available_at=10)
    report = audit_leakage(rows, stages=[{"id":"predict", "kind":"inference", "inputs":["test-a"], "inference_time":9}])
    assert "stage:predict:inputs:availability:test-a:0" in failures(report)
    # A fitted preprocessing artifact cannot use future training evidence either.
    rows[0]["available_at"] = 20
    report = audit_leakage(rows, stages=[{"id":"fit", "kind":"preprocess", "fit_on":["train"], "inference_time":9}])
    assert "stage:fit:fit_on:availability:train-a:0" in failures(report)


def test_missing_reference_timing_or_clock_domain_is_not_a_pass():
    rows = clean_records()
    del rows[1]["available_at"]
    assert not audit_leakage(rows, stages=[{"kind":"inference", "inputs":["test-a"], "inference_time":5}])["passed"]
    assert not audit_leakage(rows, stages=[{"kind":"retrieval", "corpus":["unknown-source"]}])["passed"]
    assert not audit_leakage(rows, stages=[{"kind":"retrieval"}])["passed"]
    rows[1].update(available_at=1, clock_domain="device-clock")
    assert not audit_leakage(rows, stages=[{"kind":"inference", "inputs":["test-a"], "inference_time":5, "clock_domain":"simulation"}])["passed"]


def test_explicit_override_is_research_only_and_does_not_erase_failed_check():
    rows = clean_records()
    rows[1]["episode_id"] = rows[0]["episode_id"]
    override = {"check_id":"split:episode_id", "reason":"Intentional within-episode ablation", "approved_by":"research-review"}
    result = audit_leakage(rows, research_overrides=[override])
    assert result["qualification"]["status"] == "research_only"
    assert not result["passed"]
    assert "split:episode_id" in failures(result)
    assert result["qualification"]["research_overrides"] == [override]
    assert result["qualification"]["blocking_checks"] == []
    assert not audit_leakage(rows, research_overrides=[{"check_id":"*", "reason":"ignore"}])["passed"]
    assert audit_leakage(rows, research_overrides=[{"check_id":"split:episode_id", "reason":"", "approved_by":"r"}])["qualification"]["status"] == "failed"


def pair(source, episode, prediction, expected=0, partition="test", unit=None):
    return {"source_id":source,"episode_id":episode,"prediction":prediction,"expected":expected,"partition":partition, **({"unit":unit} if unit else {})}


def test_matched_baselines_report_example_weighted_and_episode_metrics_separately():
    candidate = [pair("a","short",0),pair("b","long",3),pair("c","long",3)]
    baseline = [pair("c","long",2),pair("a","short",2),pair("b","long",2)]
    result = compare_baselines(candidate,baseline)
    assert result["passed"]
    assert result["metrics"]["aggregate"]["candidate_mae"] == "2"
    assert result["metrics"]["aggregate"]["baseline_mae"] == "2"
    assert result["metrics"]["episode_macro_candidate_mae"] == "1.5"
    assert result["metrics"]["episode_macro_baseline_mae"] == "2"
    assert result["metrics"]["episode_macro_candidate_minus_baseline_mae"] == "-0.5"
    assert [row["source_id"] for row in result["metrics"]["per_example"]] == ["a", "b", "c"]
    assert {row["episode_id"]:row["candidate_mae"] for row in result["metrics"]["per_episode"]} == {"short":"0","long":"3"}


@pytest.mark.parametrize("mutate", [
    lambda rows: rows.pop(),
    lambda rows: rows.append(dict(rows[0])),
    lambda rows: rows[0].update(expected=1),
    lambda rows: rows[0].update(partition="train"),
    lambda rows: rows[0].update(episode_id="other-episode"),
])
def test_unmatched_duplicated_training_or_different_truth_is_not_a_valid_comparison(mutate):
    baseline = [pair("a","e1",1),pair("b","e2",1)]
    mutate(baseline)
    result = compare_baselines([pair("a","e1",0),pair("b","e2",0)],baseline)
    assert not result["passed"]
    assert "aggregate" not in result["metrics"]


def test_comparison_converts_units_but_rejects_mixed_dimensions():
    result = compare_baselines([pair("a","e",100,unit="cm")],[pair("a","e",2,unit="m")])
    assert result["passed"]
    assert result["metrics"]["aggregate"]["candidate_mae"] == "1"
    mixed = [pair("a","e",1,unit="m"),pair("b","e2",1,unit="s")]
    assert not compare_baselines(mixed,mixed)["passed"]


def test_composed_evaluation_keeps_independent_failures_and_provenance_qualification():
    result = evaluate_examples([
        {"source_id":"test-a", "actual":"-0.125", "expected":"0.125"},
        {"source_id":"test-b", "validator":"packet", "actual":"A:B", "expected":"AB"},
    ], provenance=clean_records())
    assert not result["passed"]
    assert result["metrics"]["passed_examples"] == 0
    assert "example:0:numeric:value" in failures(result)
    assert "example:1:packet:exact" in failures(result)
    assert result["provenance"]["passed"]
    assert not evaluate_examples([{"actual":0,"expected":0}], provenance=[])["passed"]


def test_malformed_overrides_stages_and_validator_options_fail_closed():
    rows = clean_records()
    assert not audit_leakage(rows, stages=[None])["passed"]
    assert not audit_leakage(rows, research_overrides=[{"check_id":[], "reason":"r", "approved_by":"r"}])["passed"]
    assert not evaluate_examples([{"actual":0,"expected":0,"options":{"unknown_option":True}}],provenance=rows)["passed"]
    assert not evaluate_examples([None],provenance=rows)["passed"]


def test_unknown_stage_semantics_and_empty_named_partitions_do_not_claim_lineage():
    assert not audit_leakage(clean_records(),stages=[{"kind":"retrival","inputs":["test-a"]}])["passed"]
    assert not audit_leakage(clean_records(),stages=[{"kind":"preprocess","fit_on":["validation"]}])["passed"]
