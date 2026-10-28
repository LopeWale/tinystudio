"""Real autoregressive policy updates and whitelisted tool execution, without downloads."""
import hashlib
import json

import pytest
import torch

from tinystudio.lab.language_rl import CallLanguage, execute_language_rl, generate_tool_call, load_language_rl_checkpoint, token_statistics


def spec(objective="grpo", family="gru", **parameters):
    return {"definition": {"seed": 29, "learning": {"objective": objective, "family": family},
        "parameters": {"steps": 2, "group_size": 8, "hidden_size": 8, "update_epochs": 2, **parameters}}}


@pytest.mark.parametrize("algorithm,family", [("grpo", "gru"), ("ppo", "gru"), ("grpo", "transformer")])
def test_actual_multitoken_policy_updates_execute_traced_tools(tmp_path, algorithm, family):
    result = execute_language_rl(spec(algorithm, family), tmp_path, lambda row: None, lambda: False)
    assert result["metrics"]["parameters_changed"]
    assert result["metrics"]["reload_generation_equal"]
    assert result["metrics"]["sampled_decision_tokens"] == 2 * 8 * 3
    assert any(record["model_gradient_norm"] > 0 for record in result["records"])
    rows = [json.loads(line) for line in (tmp_path / "language-trajectories.jsonl").read_text().splitlines()]
    assert len(rows) == 16
    language = CallLanguage()
    for row in rows:
        name, arguments = language.parse(row["generated_text"])
        assert row["tool"]["name"] == name and row["tool"]["arguments"] == arguments
        assert len(row["generated_token_ids"]) == len(row["generation_log_probs"]) == 8
        assert sum(row["sampled_token_mask"]) == 3
        assert row["reward"] == pytest.approx(sum(row["reward_components"].values()))
        assert row["policy_version"]
    assert len({row["policy_version"] for row in rows}) == 2
    evaluation = json.loads((tmp_path / "heldout-evaluation.json").read_text())
    training_tasks = {(row["task"]["operation"], row["task"]["left"], row["task"]["right"]) for row in rows}
    heldout_tasks = {(row["task"]["operation"], row["task"]["left"], row["task"]["right"]) for row in evaluation["final"]}
    assert training_tasks.isdisjoint(heldout_tasks)
    assert result["qualification"]["token_level_autoregressive_policy"]


def test_multiple_sampled_argument_tokens_have_actual_model_gradients(tmp_path):
    execute_language_rl(spec(), tmp_path, lambda row: None, lambda: False)
    model, values, _, _ = load_language_rl_checkpoint(tmp_path / "language-policy.pt")
    row = json.loads((tmp_path / "language-trajectories.jsonl").read_text().splitlines()[0])
    chosen, _, _ = token_statistics(model, values, row)
    parameters = list(model.parameters())
    for position in (1, 3, 5):
        gradients = torch.autograd.grad(chosen[position], parameters, retain_graph=True)
        assert sum(float(gradient.abs().sum()) for gradient in gradients) > 0
    fixed_gradients = torch.autograd.grad(chosen[0], parameters)
    assert sum(float(gradient.abs().sum()) for gradient in fixed_gradients) == 0


@pytest.mark.parametrize("algorithm", ["ppo", "grpo"])
def test_token_language_resume_matches_uninterrupted_policy(tmp_path, algorithm):
    execute_language_rl(spec(algorithm, steps=4), tmp_path / "whole", lambda row: None, lambda: False)
    execute_language_rl(spec(algorithm, steps=2), tmp_path / "first", lambda row: None, lambda: False)
    path = tmp_path / "first/language-policy.pt"
    resumed = spec(algorithm, steps=4)
    resumed["definition"]["inputs"] = [{"artifact_id": "resume-1", "role": "resume", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}]
    resumed["input_paths"] = {"resume-1": str(path)}
    result = execute_language_rl(resumed, tmp_path / "resumed", lambda row: None, lambda: False)
    expected = load_language_rl_checkpoint(tmp_path / "whole/language-policy.pt")[3]
    actual = load_language_rl_checkpoint(tmp_path / "resumed/language-policy.pt")[3]
    assert all(torch.equal(value, expected["model"][key]) for key, value in actual["model"].items())
    assert all(torch.equal(value, expected["value_head"][key]) for key, value in actual["value_head"].items())
    assert [row["step"] for row in result["records"]] == [3, 4]
    assert generate_tool_call(path, {"operation": "add", "left": 4, "right": 1})["generated_text"].startswith("CALL ")


def test_generated_tool_errors_are_actual_and_retained(tmp_path):
    result = execute_language_rl(spec(steps=1, group_size=64, tasks=[{"operation": "add", "left": 2, "right": 0}]),
        tmp_path, lambda row: None, lambda: False)
    rows = [json.loads(line) for line in (tmp_path / "language-trajectories.jsonl").read_text().splitlines()]
    errors = [row for row in rows if row["tool"]["error"]]
    assert result["metrics"]["tool_errors"] == len(errors) > 0
    assert all(row["tool"]["name"] == "divide" and row["tool"]["arguments"]["right"] == 0 for row in errors)
    assert all(row["reward_components"]["tool_error"] < 0 for row in errors)


def test_grammar_rejects_unwhitelisted_and_executable_text():
    language = CallLanguage(["add"])
    assert language.parse("CALL add ( 2 , 3 ) END") == ("add", {"left": 2, "right": 3})
    for text in ("CALL divide ( 2 , 3 ) END", "CALL add ( 2 , 3 ) END; __import__('os')", "CALL shell ( 2 , 3 ) END"):
        with pytest.raises(ValueError, match="whitelisted"):
            language.parse(text)


def test_training_cannot_consume_heldout_task_and_cancellation_preserves_state(tmp_path):
    bad = spec(tasks=[{"operation": "add", "left": 5, "right": 2}])
    with pytest.raises(ValueError, match="overlap"):
        execute_language_rl(bad, tmp_path / "bad", lambda row: None, lambda: False)
    rows = []
    result = execute_language_rl(spec(steps=4), tmp_path / "partial", rows.append, lambda: len(rows) == 1)
    assert result["qualification"]["status"] == "cancelled"
    assert load_language_rl_checkpoint(tmp_path / "partial/language-policy.pt")[3]["step"] == 1


def test_collected_logprobs_match_same_autoregressive_policy():
    from tinystudio.lab.learning import build_model
    from tinystudio.lab.language_rl import sample_call
    torch.manual_seed(5)
    language = CallLanguage()
    model = build_model({"family": "gru", "hidden_size": 8, "layers": 1, "heads": 2, "context": 32}, len(language.tokens))
    values = torch.nn.Linear(len(language.tokens), 1)
    task = {"operation": "add", "left": 2, "right": 3}
    generator = torch.Generator().manual_seed(4)
    row = sample_call(model, values, language, task, generator)
    chosen, _, _ = token_statistics(model, values, row)
    assert torch.allclose(chosen.detach(), torch.tensor(row["generation_log_probs"]), atol=1e-6, rtol=1e-6)
    assert all(row["generation_log_probs"][index] == 0 for index in (0, 2, 4, 6, 7))
    with pytest.raises(ValueError, match="whitelist"):
        CallLanguage([])


def test_nested_token_model_architecture_and_strategy_contract(tmp_path):
    experiment = spec(family="transformer", steps=1, group_size=4)
    experiment["definition"]["parameters"].pop("hidden_size")
    experiment["definition"]["learning"]["architecture"] = {"hidden_size": 12, "layers": 2, "heads": 3, "context": 24}
    execute_language_rl(experiment, tmp_path, lambda row: None, lambda: False)
    model, _, _, state = load_language_rl_checkpoint(tmp_path / "language-policy.pt")
    assert model.embedding.embedding_dim == 12 and model.position.num_embeddings == 24
    assert len(model.core.layers) == 2 and model.core.layers[0].self_attn.num_heads == 3
    assert state["architecture"]["layers"] == 2
    experiment["definition"]["parameters"]["hidden_size"] = 8
    with pytest.raises(ValueError, match="conflicting"):
        execute_language_rl(experiment, tmp_path / "conflict", lambda row: None, lambda: False)
    experiment["definition"]["learning"]["update_strategy"] = "lora"
    with pytest.raises(ValueError, match="unsupported update_strategy"):
        execute_language_rl(experiment, tmp_path / "strategy", lambda row: None, lambda: False)
