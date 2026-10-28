import hashlib
import importlib.util
from pathlib import Path

import pytest

from tinystudio.lab.figures import export_figures


AVAILABLE = bool(importlib.util.find_spec("matplotlib"))


def fixture_rows():
    return [{"record_id": str(index), "source_record_id": str(index), "episode_id": "A", "partition": "train",
        "clock_domain": "reference", "clock_units": "s", "time": time, "value": value, "units": "Pa",
        "estimated": index == 3, "future_aware": index == 3} for index, (time, value) in
        enumerate([(0, 0), (1, 1), (2, None), (5, 5), (6, 6)])]


def test_optional_figure_boundary_is_explicit(tmp_path):
    if not AVAILABLE:
        artifacts, status = export_figures(fixture_rows(), tmp_path)
        assert artifacts == []
        assert status["generated"] is False and "matplotlib" in status["reason"]


@pytest.mark.skipif(not AVAILABLE, reason="optional matplotlib not installed in this runtime")
def test_actual_svg_pdf_export_labels_units_estimation_and_sampling_gaps(tmp_path, monkeypatch):
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "mpl-cache"))
    artifacts, status = export_figures(fixture_rows(), tmp_path)
    assert status["generated"]
    assert status["measured_records"] == 3
    assert status["estimated_records"] == status["missing_records"] == status["future_aware_records"] == 1
    assert status["detected_sampling_gaps"] == 1
    assert status["clock_units"] == ["s"]
    assert (tmp_path / "signals.pdf").read_bytes().startswith(b"%PDF")
    svg = (tmp_path / "signals.svg").read_text()
    assert "Value [Pa]" in svg and "Event time [s]" in svg
    assert "Measured: 3" in svg and "future-aware: 1" in svg
    assert all(item["visibility"] == "evaluation" for item in artifacts)
    assert all(hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest() == item["sha256"] for item in artifacts)


@pytest.mark.skipif(not AVAILABLE, reason="optional matplotlib not installed in this runtime")
def test_figures_refuse_unseparated_truth(tmp_path, monkeypatch):
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "mpl-cache"))
    rows = fixture_rows()
    rows[0]["true_value"] = 99
    with pytest.raises(ValueError, match="truth"):
        export_figures(rows, tmp_path)
