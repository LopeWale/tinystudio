"""Offline tests for PII redaction, augmentation instructions, preference pairs, DPO config, and autopilot bookkeeping."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tinystudio.core import data

from tinystudio.core import quality
from tinystudio.training.autopilot import _append, base_model_of, last_winner, list_histories, next_cycle, read_history
from tinystudio.core.project import Project, ProjectError, write_json
from tinystudio.core.synth import augment_prompt
from tinystudio.training.train import TrainConfig, create_job, create_pending_experiment, preference_rows
from tinystudio.core.traces import append_trace, promote


def row(user: str, answer: str, **extra):
    return data.validate_datapoint({"messages": [{"role": "user", "content": user}, {"role": "assistant", "content": answer}], **extra})


def test_redact_pii_replaces_identifiers_but_keeps_plain_numbers():
    rows = [
        row("email me at jane.doe@example.com or call +1 (415) 555-0199", "ok"),
        row("my card is 4111 1111 1111 1111 and ssn 123-45-6789", "noted"),
        row("set the thermostat to 22 in 2024", '{"action":"set","value":22,"year":2024}'),
    ]
    out, changed = quality.redact_pii(rows)
    assert changed == 2
    assert out[0]["messages"][0]["content"] == "email me at [EMAIL] or call [PHONE]"
    assert out[1]["messages"][0]["content"] == "my card is [CARD] and ssn [ID]"
    assert out[2] is rows[2]
    profile = quality.profile(rows)
    assert profile["pii"]["emails"] == 1 and profile["pii"]["cards"] == 1 and profile["pii"]["ids"] == 1 and profile["pii"]["redactable_rows"] == 2


def test_apply_cleaning_records_redaction(tmp_path: Path):
    project = Project.create(tmp_path / "p", "p")
    data.save_dataset(project, "d", [row("mail a@b.co", "x"), row("plain", "y")])
    result = quality.apply_cleaning(project, "d", quality.CleanSpec(redact_pii=True))
    assert result["redacted_rows"] == 1 and result["after"] == 2
    assert data.load_dataset(project, "d")[0]["messages"][0]["content"] == "mail [EMAIL]"
    assert json.loads((project.dataset_dir("d") / "cleaning.jsonl").read_text().splitlines()[-1])["spec"]["redact_pii"] is True


def test_augment_prompt_carries_positive_and_negative_instructions():
    plain = augment_prompt(3, "lights off", "{}")
    assert "Generate more of" not in plain and "Avoid" not in plain
    guided = augment_prompt(3, "lights off", "{}", instructions="terse voice commands", avoid="questions about weather")
    assert "Generate more of: terse voice commands" in guided and "Avoid: questions about weather" in guided
    assert guided.index("Avoid:") < guided.index("Return JSON")


def test_preference_rows_and_dpo_config():
    kept = row("q", "good answer", rejected="bad answer")
    assert kept["rejected"] == "bad answer"
    same = row("q", "same", rejected="same")
    plain = row("q", "a")
    pairs = preference_rows([kept, same, plain])
    assert pairs == [{"prompt": [{"role": "user", "content": "q"}], "chosen": [{"role": "assistant", "content": "good answer"}], "rejected": [{"role": "assistant", "content": "bad answer"}]}]
    assert TrainConfig(base_model="x", training_type="dpo").dpo_beta == 0.1
    assert TrainConfig(base_model="x", training_type="qlora").training_type == "qlora"  # allowed; checked for CUDA + bitsandbytes at run time
    with pytest.raises(ProjectError, match="training_type must be"):
        TrainConfig(base_model="x", training_type="nope")


def test_promote_preference_pairs_only_corrected_traces(tmp_path: Path):
    project = Project.create(tmp_path / "p", "p")
    data.save_dataset(project, "d", [row("seed", "seed answer")])
    append_trace(project, {"id": "t1", "model": "m", "messages": [{"role": "user", "content": "lights"}], "response": "wrong", "corrected": "right", "score": 1})
    append_trace(project, {"id": "t2", "model": "m", "messages": [{"role": "user", "content": "fan"}], "response": "fine", "score": 1})
    append_trace(project, {"id": "t3", "model": "m", "messages": [{"role": "user", "content": "door"}], "response": "x", "corrected": "x"})
    assert promote(project, "d", preference=True) == 1
    rows = data.load_dataset(project, "d")
    pair = next(r for r in rows if r.get("rejected"))
    assert pair["messages"][-1]["content"] == "right" and pair["rejected"] == "wrong" and "preference" in pair["labels"]
    assert promote(project, "d", min_score=1) == 2  # the plain path still works and keeps corrections as the answer
    with pytest.raises(ProjectError, match="no corrected traces"):
        promote(Project.create(tmp_path / "q", "q"), "d", preference=True)


def test_autopilot_bookkeeping(tmp_path: Path):
    project = Project.create(tmp_path / "p", "p")
    assert read_history(project, "ap") == [] and list_histories(project) == [] and next_cycle(project, "ap") == 1
    data.save_dataset(project, "d", [row(f"q{i}", f"a{i}") for i in range(12)])
    data.autosplit(project, "d", 90)
    data.create_snapshot(project, "d", "ap-c1")
    create_job(project, "ap-c1", "ap-c1")
    n = create_pending_experiment(project, "ap-c1", TrainConfig(base_model="HuggingFaceTB/SmolLM2-135M-Instruct"))
    assert next_cycle(project, "ap") == 2  # the job directory for cycle 1 already exists
    _append(project, "ap", {"cycle": 1, "name": "ap", "dataset": "d", "winner": f"ap-c1/{n}", "finished": "t", "experiment": {"state": "succeeded"}})
    _append(project, "ap", {"cycle": 2, "name": "ap", "dataset": "d", "winner": None, "experiment": {"state": "failed"}})
    history = read_history(project, "ap")
    assert last_winner(history) == f"ap-c1/{n}" and base_model_of(project, f"ap-c1/{n}") == "HuggingFaceTB/SmolLM2-135M-Instruct"
    assert list_histories(project)[0] == {"name": "ap", "cycles": 2, "dataset": "d", "winner": None, "last": None}
    assert next_cycle(project, "ap") == 3
    with pytest.raises(ProjectError, match="config.json"):
        base_model_of(project, "ap-c1/9")
