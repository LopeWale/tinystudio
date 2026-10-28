"""Optional headless scientific SVG/PDF export with honest gaps and units."""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

from .learning import artifact


def export_figures(records: list[dict], output_dir: Path):
    try:
        import matplotlib
        from matplotlib.figure import Figure
        from matplotlib.lines import Line2D
    except ImportError:
        return [], {"requested": True, "generated": False, "reason": "optional matplotlib is not installed"}
    if not records:
        raise ValueError("figure export needs retained records")
    by_units, series = defaultdict(list), defaultdict(list)
    for row in records:
        if any(key in row for key in ("ground_truth", "true_value", "injected_fault")):
            raise ValueError("model measurement figures must not silently include truth evidence")
        by_units[row["units"]].append(row)
        series[(row["units"], row["episode_id"], row.get("partition"), row.get("device_id"), row.get("signal_id"), row["clock_domain"])].append(row)
    if len(records) > 100000 or len(by_units) > 8 or len(series) > 64:
        raise ValueError("figure export exceeds bounded points/units/series limits")
    colors = {"train": "#2463a5", "validation": "#b5671c", "test": "#34754d"}
    output_dir.mkdir(parents=True, exist_ok=True)
    gaps = 0
    with matplotlib.rc_context({"svg.hashsalt": "tinystudio-scientific-v1", "svg.fonttype": "none", "font.size": 9}):
        figure = Figure(figsize=(9, max(3.6, 3.0 * len(by_units))))
        axes = figure.subplots(len(by_units), 1, squeeze=False).reshape(-1)
        for axis, units in zip(axes, sorted(by_units)):
            for key, rows in series.items():
                if key[0] != units:
                    continue
                ordered = sorted(rows, key=lambda row: row["time"])
                differences = [right["time"] - left["time"] for left, right in zip(ordered, ordered[1:])]
                nominal = statistics.median(differences) if differences else None
                times, values = [], []
                for index, row in enumerate(ordered):
                    if index and nominal and row["time"] - ordered[index - 1]["time"] > 1.5 * nominal:
                        times.append((row["time"] + ordered[index - 1]["time"]) / 2)
                        values.append(math.nan)
                        gaps += 1
                    times.append(row["time"])
                    values.append(math.nan if row["value"] is None else row["value"])
                color = colors.get(key[2], "#535353")
                axis.plot(times, values, color=color, linewidth=0.8, alpha=0.65,
                          label=f"{key[1]} / {key[2] or 'unassigned'}")
                measured = [row for row in ordered if row["value"] is not None and not row.get("estimated", False)]
                estimated = [row for row in ordered if row["value"] is not None and row.get("estimated", False)]
                missing = [row for row in ordered if row["value"] is None]
                if measured:
                    axis.scatter([row["time"] for row in measured], [row["value"] for row in measured], s=16, marker="o", color=color)
                if estimated:
                    axis.scatter([row["time"] for row in estimated], [row["value"] for row in estimated], s=24, marker="^", facecolors="none", edgecolors=color)
                if missing:
                    axis.scatter([row["time"] for row in missing], [0.04] * len(missing), transform=axis.get_xaxis_transform(), marker="x", color=color, s=24)
            domains = sorted({row["clock_domain"] for row in by_units[units]})
            clock_units = sorted({row.get("clock_units", "unspecified") for row in by_units[units]})
            axis.set_xlabel(f"Event time [{', '.join(clock_units)}]; clock: {', '.join(domains)}")
            axis.set_ylabel(f"Value [{units}]")
            axis.grid(True, alpha=0.2)
            handles, labels = axis.get_legend_handles_labels()
            handles.extend([Line2D([], [], color="#555", marker="o", linestyle="none", markersize=4),
                            Line2D([], [], color="#555", marker="^", markerfacecolor="none", linestyle="none", markersize=5),
                            Line2D([], [], color="#555", marker="x", linestyle="none", markersize=5)])
            labels.extend(["Measured", "Estimated/transformed", "Missing (axis baseline)"])
            axis.legend(handles, labels, loc="best", fontsize=7, ncol=2)
        measured_count = sum(row["value"] is not None and not row.get("estimated", False) for row in records)
        estimated_count = sum(row["value"] is not None and row.get("estimated", False) for row in records)
        missing_count = sum(row["value"] is None for row in records)
        future_count = sum(bool(row.get("future_aware", False)) for row in records)
        figure.suptitle("Retained scalar evidence by unit and partition")
        figure.text(0.01, 0.01, f"Measured: {measured_count}; estimated/transformed: {estimated_count}; missing: {missing_count}; future-aware: {future_count}.\nLines break at explicit missing values and detected irregular sampling gaps. No confidence interval or physical qualification is implied.", fontsize=7)
        figure.tight_layout(rect=(0, 0.09, 1, 0.95))
        svg, pdf = output_dir / "signals.svg", output_dir / "signals.pdf"
        figure.savefig(svg, format="svg", metadata={"Date": None, "Creator": "TinyStudio scientific export"})
        figure.savefig(pdf, format="pdf", metadata={"CreationDate": None, "ModDate": None, "Creator": "TinyStudio scientific export"})
        figure.clear()
    status = {"requested": True, "generated": True, "library": f"matplotlib {matplotlib.__version__}",
        "backend": "headless Figure SVG/PDF exporters", "records": len(records), "units": sorted(by_units),
        "clock_units": sorted({row.get("clock_units", "unspecified") for row in records}), "detected_sampling_gaps": gaps,
        "measured_records": measured_count, "estimated_records": estimated_count, "missing_records": missing_count,
        "future_aware_records": future_count, "record_digest": hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()}
    return [{**artifact(svg, "scientific_figure", "image/svg+xml"), "visibility": "evaluation"},
            {**artifact(pdf, "scientific_figure", "application/pdf"), "visibility": "evaluation"}], status
