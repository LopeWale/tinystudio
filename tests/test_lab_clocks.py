import json
from pathlib import Path

import pytest

from tinystudio.lab.clocks import clock_reference

FIXTURE = Path(__file__).resolve().parents[1] / "frontend/src/features/lab/clock-contract.v1.json"


def test_clock_coordinate_contract_matches_independent_shared_cases():
    fixture = json.loads(FIXTURE.read_text())
    results = {}
    for case in fixture["cases"]:
        if case.get("invalid"):
            with pytest.raises(ValueError, match="conflicting clock"):
                clock_reference(case["row"], case["scope"])
            continue
        value = clock_reference(case["row"], case["scope"])
        assert value["qualified"] is case["qualified"]
        results[case["id"]] = value
    for left, right in fixture["compatible_pairs"]:
        assert results[left]["key"] == results[right]["key"]
    for left, right in fixture["incompatible_pairs"]:
        assert results[left]["key"] != results[right]["key"]
