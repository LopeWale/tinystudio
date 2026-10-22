from __future__ import annotations

from pathlib import Path

from tinystudio.core import data

from tinystudio.core import quality
from tinystudio.core.project import Project


def row(user: str, answer: str, **extra):
    return data.validate_datapoint({"messages": [{"role": "user", "content": user}, {"role": "assistant", "content": answer}], **extra})


def test_profile_counts_duplicates_outliers_and_formats():
    rows = [row("turn on the lights", '{"a":1}'), row("Turn on the  lights", '{"a":1}'), row("turn on the lights in the kitchen please", "{}"), row("turn on the lights in the kitchen now", "{}"), row("x", "y"), row("completely different request here", "plain text " * 50, rating=9)]
    p = quality.profile(rows)
    assert p["count"] == 6
    assert p["duplicates"]["exact_user_groups"] == 1 and p["duplicates"]["exact_user_rows"] == 1
    assert p["duplicates"]["near_pair_count"] >= 1
    assert 4 in p["outliers"]["empty_like"]
    assert p["format"]["answers_json"] == 4
    assert p["ratings"]["count"] == 1 and p["ratings"]["mean"] == 9
    assert p["user_chars"]["histogram"]["counts"] and sum(p["user_chars"]["histogram"]["counts"]) == 6


def test_clean_applies_each_rule_once_in_order(tmp_path: Path):
    rows = [row("a question", "an answer"), row("A question", "an answer"), row("  spaced  ", "ok"), row("short", "x"), row("json please", '{"k": 1}'), row("rated", "meh", rating=3), row("labelled", "a labelled answer", labels=["drop-me"])]
    kept, removed = quality.clean(rows, quality.CleanSpec(dedupe_user=True, trim_whitespace=True, drop_empty=True, min_rating=5, require_json_answer=False, drop_labels=["drop-me"]))
    assert removed == {"duplicate_user": 1, "empty": 1, "low_rating": 1, "dropped_label": 1}
    assert [r["messages"][0]["content"] for r in kept] == ["a question", "spaced", "json please"]
    kept, removed = quality.clean(rows, quality.CleanSpec(require_json_answer=True))
    assert len(kept) == 1 and removed["not_json"] == 6
    kept, removed = quality.clean(rows, quality.CleanSpec(drop_indices=[0, 1]))
    assert removed == {"selected": 2} and len(kept) == 5


def test_apply_cleaning_writes_history(tmp_path: Path):
    project = Project.create(tmp_path / "p", "p")
    data.save_dataset(project, "d", [row("q", "a"), row("q", "a"), row("other", "b")])
    result = quality.apply_cleaning(project, "d", quality.CleanSpec(dedupe_user=True))
    assert result["before"] == 3 and result["after"] == 2 and result["removed"] == {"duplicate_user": 1}
    assert (project.dataset_dir("d") / "cleaning.jsonl").read_text().count("\n") == 1
    assert data.dataset_meta(project, "d")["datapoints"] == 2
