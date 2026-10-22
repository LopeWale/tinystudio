"""Scoring: rubric judging with a helper LLM, deterministic code checks, webhooks, and the evaluation loop.

Storage is the services layer's job; these functions take rows and metric specs and report results through
callbacks so the same code runs under the worker, the CLI, and tests.
"""

from __future__ import annotations

import difflib
import json
import re
import statistics
from collections import Counter
from collections.abc import Callable
from typing import Any

import httpx

from tinystudio.core.data import last_user_message, prompt_messages, reference_answer
from tinystudio.core.llm import LLMClient, LLMError
from tinystudio.core.project import ProjectError

CODE_CHECKS = ("exact_match", "token_f1", "similarity", "json_valid", "contains_reference")


def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text)


def exact_match(prediction: str, reference: str) -> float:
    return 1.0 if _normalize(prediction) == _normalize(reference) else 0.0


def token_f1(prediction: str, reference: str) -> float:
    p, r = _normalize(prediction).split(), _normalize(reference).split()
    if not p or not r:
        return float(p == r)
    common = sum((Counter(p) & Counter(r)).values())
    if common == 0:
        return 0.0
    precision, recall = common / len(p), common / len(r)
    return 2 * precision * recall / (precision + recall)


def similarity(prediction: str, reference: str) -> float:
    return difflib.SequenceMatcher(None, _normalize(prediction), _normalize(reference)).ratio()


def json_valid(prediction: str, reference: str) -> float:
    """1 if the prediction parses as JSON; if the reference is JSON too, 1 only when both parse to equal values."""
    try:
        predicted = json.loads(_strip_fence(prediction))
    except json.JSONDecodeError:
        return 0.0
    try:
        expected = json.loads(_strip_fence(reference))
    except json.JSONDecodeError:
        return 1.0
    return 1.0 if predicted == expected else 0.5


def contains_reference(prediction: str, reference: str) -> float:
    return 1.0 if _normalize(reference) in _normalize(prediction) else 0.0


def _strip_fence(text: str) -> str:
    match = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    return (match.group(1) if match else text).strip()


CODE_FUNCTIONS: dict[str, Callable[[str, str], float]] = {"exact_match": exact_match, "token_f1": token_f1, "similarity": similarity, "json_valid": json_valid, "contains_reference": contains_reference}


RULE_PROMPT = """You design evaluation rubrics for a small language model that is being fine-tuned.
Goal of the model: {goal}
Metric being designed: {metric}

Here are example conversations from the training data (user request, then the ideal assistant answer):

{examples}

Write evaluation rules that a judge could check on a NEW answer to a similar request.
Return JSON: {{"should": ["..."], "should_not": ["..."]}} with 3-6 concise, checkable rules in each list.
Rules must describe properties of the answer (format, content, tone, length), not restate specific example answers."""


def generate_rules(train_rows: list[dict[str, Any]], client: LLMClient, goal: str = "", sample: int = 6, name: str = "", description: str = "") -> dict[str, list[str]]:
    examples = "\n\n".join(f"USER: {last_user_message(row)}\nASSISTANT: {reference_answer(row)}" for row in train_rows[:sample])
    metric = f"{name}: {description}" if description else (name or "(general quality)")
    reply = client.chat_json([{"role": "user", "content": RULE_PROMPT.format(goal=goal or "(not stated)", metric=metric, examples=examples)}])
    if not isinstance(reply, dict):
        raise LLMError("rule generation did not return an object")
    should = [str(r) for r in reply.get("should", []) if str(r).strip()]
    should_not = [str(r) for r in reply.get("should_not", []) if str(r).strip()]
    if not should and not should_not:
        raise LLMError("rule generation returned no rules")
    return {"should": should, "should_not": should_not}


JUDGE_PROMPT = """You are grading one answer from a small language model against a rubric.

CONVERSATION (everything the model saw):
{conversation}

REFERENCE ANSWER (what a good answer looks like; the model's answer may differ in wording):
{reference}

MODEL ANSWER (grade this):
{prediction}

RULES the answer SHOULD satisfy:
{should}

RULES the answer SHOULD NOT violate:
{should_not}

For every rule decide pass or fail, judging only the MODEL ANSWER. Return JSON:
{{"should": [{{"rule": "...", "pass": true, "reason": "..."}}], "should_not": [{{"rule": "...", "pass": true, "reason": "..."}}]}}
where "pass" for a should-not rule means the answer did NOT do the forbidden thing."""


def _passed(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("true", "pass", "passed", "yes", "y", "1", "ok")
    return False


def judge_rubric(client: LLMClient, rules: dict[str, list[str]], row: dict[str, Any], prediction: str) -> tuple[float, str, dict[str, Any]]:
    conversation = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in prompt_messages(row))
    numbered = lambda items: "\n".join(f"{i + 1}. {rule}" for i, rule in enumerate(items)) or "(none)"  # noqa: E731
    reply = client.chat_json([{"role": "user", "content": JUDGE_PROMPT.format(conversation=conversation, reference=reference_answer(row), prediction=prediction or "(empty)", should=numbered(rules.get("should", [])), should_not=numbered(rules.get("should_not", [])))}])
    verdicts: list[dict[str, Any]] = []
    for key in ("should", "should_not"):
        expected = rules.get(key, [])
        got = reply.get(key, []) if isinstance(reply, dict) else []
        for index, rule in enumerate(expected):
            verdict = got[index] if index < len(got) and isinstance(got[index], dict) else {}
            verdicts.append({"kind": key, "rule": rule, "pass": _passed(verdict.get("pass")), "reason": str(verdict.get("reason", "no verdict returned"))})
    total = len(verdicts)
    passed = sum(1 for v in verdicts if v["pass"])
    score = passed / total if total else 0.0
    failed = [v["rule"] for v in verdicts if not v["pass"]]
    reason = "all rules satisfied" if not failed else "failed: " + "; ".join(failed[:3])
    return score, reason, {"verdicts": verdicts}


def score_webhook(url: str, row: dict[str, Any], prediction: str, model_name: str, token: str | None = None) -> tuple[float, str]:
    payload = {"datapoint": {"messages": row["messages"]}, "prediction": prediction, "model_name": model_name}
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        response = httpx.post(url, json=payload, timeout=60, headers=headers)
        response.raise_for_status()
        body = response.json()
        score = float(body["score"])
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as cause:
        raise ProjectError(f"webhook {url} failed: {cause}") from cause
    if not 0.0 <= score <= 1.0:
        raise ProjectError(f"webhook {url} returned score {score}; expected 0..1")
    return score, str(body.get("reason", ""))


def score_one(metric: dict[str, Any], row: dict[str, Any], prediction: str, model_id: str, client: LLMClient | None) -> tuple[float, str, dict[str, Any]]:
    kind = metric["kind"]
    if kind == "code":
        function = CODE_FUNCTIONS[metric["check"]]
        score = function(prediction, reference_answer(row))
        return score, f"{metric['check']}={score:.3f}", {}
    if kind == "rubric":
        if client is None:
            raise ProjectError("rubric metrics need the helper LLM; configure it in Settings")
        return judge_rubric(client, metric["rules"], row, prediction)
    if kind == "webhook":
        score, reason = score_webhook(metric["webhook"]["url"], row, prediction, model_id, metric["webhook"].get("token"))
        return score, reason, {}
    raise ProjectError(f"unknown metric kind {kind!r}")


def evaluate_rows(rows: list[dict[str, Any]], model_ids: list[str], metrics: list[dict[str, Any]], load_runner: Callable[[str], Any], client: LLMClient | None, max_new_tokens: int, on_result: Callable[[dict[str, Any]], None], progress: Callable[[str], None] = print) -> list[dict[str, Any]]:
    """Generate and score every (datapoint, model, metric); ``on_result`` receives each row; returns the leaderboard."""
    if any(m["kind"] == "rubric" for m in metrics) and client is None:
        raise ProjectError("rubric metrics need a judge: configure the helper LLM in Settings")
    scores: dict[str, dict[str, list[float]]] = {m: {metric["name"]: [] for metric in metrics} for m in model_ids}
    for model_id in model_ids:
        progress(f"loading {model_id}")
        runner = load_runner(model_id)
        try:
            for index, row in enumerate(rows):
                generated = runner.generate(prompt_messages(row), max_new_tokens=max_new_tokens, temperature=0.0)
                prediction = generated["content"]
                for metric in metrics:
                    score, reason, detail = score_one(metric, row, prediction, model_id, client)
                    scores[model_id][metric["name"]].append(score)
                    on_result({"datapoint": index, "model": model_id, "metric": metric["name"], "score": round(score, 4), "reason": reason, "prediction": prediction, "reference": reference_answer(row), "user": last_user_message(row), "seconds": generated["seconds"], "detail": detail})
                progress(f"  {model_id} {index + 1}/{len(rows)} " + " ".join(f"{m['name']}={scores[model_id][m['name']][-1]:.2f}" for m in metrics))
        finally:
            runner.unload()
    leaderboard = []
    for model_id in model_ids:
        per_metric = {metric: round(statistics.fmean(values), 4) if values else None for metric, values in scores[model_id].items()}
        overall = [v for v in per_metric.values() if v is not None]
        leaderboard.append({"model": model_id, "scores": per_metric, "overall": round(statistics.fmean(overall), 4) if overall else None})
    leaderboard.sort(key=lambda entry: -(entry["overall"] or 0))
    return leaderboard


BUILTIN_METRICS: dict[str, dict[str, Any]] = {
    "Conciseness": {
        "description": "The answer says what is needed and nothing more.",
        "should": ["Answer the question directly in the first sentence", "Keep to the length the question warrants", "Use plain wording without filler phrases", "Stop once the question is answered"],
        "should_not": ["Repeat the question back", "Add unrelated background or caveats", "Pad the answer with pleasantries", "Restate the same point twice"],
    },
    "Correctness": {
        "description": "The answer is factually right and consistent with the reference.",
        "should": ["State facts that agree with the reference answer", "Include every element the reference treats as essential", "Use the correct names, numbers, and units", "Follow the format the reference uses"],
        "should_not": ["Contradict the reference answer", "Omit an essential element of the reference", "Introduce incorrect numbers or names", "Answer a different question than the one asked"],
    },
    "Hallucination": {
        "description": "The answer does not invent facts that the conversation or reference does not support.",
        "should": ["Ground every claim in the conversation or the reference", "Say when something is unknown rather than guessing", "Keep entities and figures traceable to the input", "Stay within the scope of the question"],
        "should_not": ["Invent names, dates, numbers, or sources", "Assert details the input does not contain", "Fabricate capabilities or outcomes", "Present speculation as fact"],
    },
}
