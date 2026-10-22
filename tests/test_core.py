"""Pure logic: records, splits, cleaning, scoring, parsing, configuration helpers. No stores."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import row
from tinystudio.config import redacted
from tinystudio.core import data, quality
from tinystudio.core.documents import chunk_text, youtube_video_id
from tinystudio.core.llm import LLMError, extract_json
from tinystudio.core.project import ProjectError, check_name, slugify
from tinystudio.core.synth import augment_prompt
from tinystudio.evaluation import evaluate
from tinystudio.inference.engine import split_model_id
from tinystudio.serving.runner import parse_tool_calls, validate_schema
from tinystudio.settings import load_dotenv
from tinystudio.training.train import TrainConfig, _checkpoint_args, alias_for, parse_alias, parse_experiment_ref, preference_rows


def test_validate_datapoint_shapes():
    clean = data.validate_datapoint({"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}, {"role": "assistant", "content": "a"}], "labels": ["x"], "rating": 7, "rejected": "bad", "extra": 1})
    assert clean == {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}, {"role": "assistant", "content": "a"}], "bucket": "uncategorized", "labels": ["x"], "rating": 7.0, "rejected": "bad"}
    for bad, message in [({"messages": []}, "non-empty"), ({"messages": [{"role": "user", "content": "u"}]}, "last message"), ({"messages": [{"role": "assistant", "content": "a"}]}, "user message"), ({"messages": [{"role": "user", "content": "u"}, {"role": "system", "content": "s"}, {"role": "assistant", "content": "a"}]}, "only allowed first"), ({"messages": [{"role": "user", "content": "u"}, {"role": "assistant", "content": "a"}], "bucket": "nope"}, "bucket")]:
        with pytest.raises(data.DatapointError, match=message):
            data.validate_datapoint(bad)
    assert "rating" not in data.validate_datapoint({"messages": [{"role": "user", "content": "u"}, {"role": "assistant", "content": "a"}], "rating": "high"})


def test_assign_split_keeps_manual_buckets_and_synthetic_out_of_validation():
    rows = [row(f"q{i}", f"a{i}") for i in range(10)] + [row("s", "a", source="synthetic:x") for _ in range(5)]
    rows[0]["bucket"] = "validation"
    moves = data.assign_split(rows, training_pct=60, seed=1)
    assert 0 not in moves and len(moves) == 14
    assert all(rows[i].get("source", "").startswith("synthetic") is False for i, b in moves.items() if b == "validation")
    with pytest.raises(ProjectError):
        data.assign_split(rows, training_pct=0)
    assert data.bucket_counts(rows)["validation"] == 1


def test_jsonl_roundtrip(tmp_path: Path):
    path = tmp_path / "d.jsonl"
    data.write_jsonl(path, [row("a", "b"), row("c", "d")])
    assert len(list(data.read_jsonl(path))) == 2
    path.write_text("not json\n")
    with pytest.raises(data.DatapointError, match="invalid JSON"):
        list(data.read_jsonl(path))


def test_names_and_slugs():
    assert check_name("Strict JSON", "x") == "Strict JSON"
    with pytest.raises(ProjectError):
        check_name("../x", "x")
    assert slugify("Home Assistant v2!") == "home-assistant-v2"
    with pytest.raises(ProjectError):
        slugify("!!!")


def test_redact_pii_and_clean():
    rows = [row("email me at jane.doe@example.com or call +1 (415) 555-0199", "ok"), row("my card is 4111 1111 1111 1111 and ssn 123-45-6789", "noted"), row("set the thermostat to 22 in 2024", '{"action":"set","value":22,"year":2024}'), row("set the thermostat to 22 in 2024", "dup")]
    out, changed = quality.redact_pii(rows)
    assert changed == 2 and out[0]["messages"][0]["content"] == "email me at [EMAIL] or call [PHONE]" and out[1]["messages"][0]["content"] == "my card is [CARD] and ssn [ID]"
    kept, removed = quality.clean(rows, quality.CleanSpec(dedupe_user=True, require_json_answer=False))
    assert len(kept) == 3 and removed.get("duplicate_user") == 1
    profile = quality.profile(rows)
    assert profile["pii"]["emails"] == 1 and profile["count"] == 4


def test_augment_prompt_and_preferences():
    guided = augment_prompt(3, "lights off", "{}", instructions="terse voice commands", avoid="questions about weather")
    assert "Generate more of: terse voice commands" in guided and "Avoid: questions about weather" in guided
    pairs = preference_rows([row("q", "good", rejected="bad"), row("q", "same", rejected="same"), row("q", "a")])
    assert len(pairs) == 1 and pairs[0]["rejected"] == [{"role": "assistant", "content": "bad"}]


def test_train_config_aliases_and_checkpoints():
    cfg = TrainConfig(base_model="HuggingFaceTB/SmolLM2-135M-Instruct")
    assert cfg.lora_targets == ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]
    with pytest.raises(ProjectError):
        TrainConfig(base_model="x", training_type="nope")
    assert TrainConfig.from_dict({"base_model": "x", "unknown": 1}).base_model == "x"
    assert alias_for("my-job", 3) == "ft-my-job-3" and parse_alias("ft-my-job-3") == ("my-job", 3) and parse_alias("my-job/3") is None
    assert parse_experiment_ref("ft-my-job-3") == ("my-job", 3) and parse_experiment_ref("my-job/3") == ("my-job", 3)
    with pytest.raises(ProjectError):
        parse_experiment_ref("nope")
    assert _checkpoint_args(TrainConfig(base_model="x")) == {"save_strategy": "no"}
    assert _checkpoint_args(TrainConfig(base_model="x", checkpoint_every_steps=10, keep_checkpoints=3)) == {"save_strategy": "steps", "save_steps": 10, "save_total_limit": 3, "save_only_model": True}


def test_code_checks_and_judge_parsing():
    assert evaluate.exact_match("Hello, world!", "hello world") == 1.0
    assert 0 < evaluate.token_f1("the cat sat", "the cat") < 1
    assert evaluate.json_valid('```json\n{"a": 1}\n```', '{"a": 1}') == 1.0 and evaluate.json_valid("{bad", "{}") == 0.0
    assert evaluate.contains_reference("the answer is 42 obviously", "42") == 1.0
    assert evaluate._passed("false") is False and evaluate._passed("pass") is True and evaluate._passed(1) is True
    assert evaluate.score_one({"kind": "code", "check": "exact_match", "name": "m"}, row("q", "a"), "a", "m", None)[0] == 1.0
    with pytest.raises(ProjectError, match="helper LLM"):
        evaluate.score_one({"kind": "rubric", "rules": {"should": ["x"], "should_not": []}, "name": "r"}, row("q", "a"), "a", "m", None)


def test_extract_json_and_chunking():
    assert extract_json('Sure: ```json\n{"a": [1, 2]}\n``` done') == {"a": [1, 2]}
    assert extract_json("<think>hmm</think> {\"ok\": true} trailing") == {"ok": True}
    with pytest.raises(LLMError):
        extract_json("no json here")
    chunks = chunk_text("word " * 2000, max_chars=500, overlap=50)
    assert len(chunks) > 3 and all(len(c) <= 520 for c in chunks)
    assert youtube_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ" and youtube_video_id("https://example.com") is None


def test_tool_calls_and_schema_subset():
    calls = parse_tool_calls('<tool_call>{"name": "get_weather", "arguments": {"city": "Paris"}}</tool_call>')
    assert calls[0]["function"]["name"] == "get_weather" and json.loads(calls[0]["function"]["arguments"]) == {"city": "Paris"}
    assert parse_tool_calls("plain text") == []
    assert validate_schema({"a": 1}, {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a", "b"]}) == ["$.b: required"]
    assert validate_schema([1, "x"], {"type": "array", "items": {"type": "integer"}}) == ["$[1]: expected ['integer']"]


def test_model_id_scoping():
    assert split_model_id("home/job/3", None) == ("home", "job/3")
    assert split_model_id("home/job/3@40", None) == ("home", "job/3@40")
    assert split_model_id("home/ft-job-3", None) == ("home", "ft-job-3")
    assert split_model_id("HuggingFaceTB/SmolLM2-135M-Instruct", "home") == ("home", "HuggingFaceTB/SmolLM2-135M-Instruct")
    assert split_model_id("job/3", "home") == ("home", "job/3")


def test_dotenv_parsing_and_redaction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    env = tmp_path / ".env"
    env.write_text('export A_KEY="quoted value"\nB_KEY=plain # inline comment\n# comment\nC_KEY=\'single\'\nNOEQUALS\n')
    for key in ("A_KEY", "B_KEY", "C_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("B_KEY", "preset")
    loaded = load_dotenv([env, env])
    assert loaded == [env]
    import os

    assert os.environ["A_KEY"] == "quoted value" and os.environ["B_KEY"] == "preset" and os.environ["C_KEY"] == "single"
    assert redacted("postgresql+psycopg://user:secret@host:5432/db") == "postgresql+psycopg://user:***@host:5432/db"
    assert redacted("redis://host:6379/0") == "redis://host:6379/0"
