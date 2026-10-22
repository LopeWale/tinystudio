"""Dataset analysis for a fine-tuning job: the helper LLM describes the task from a sample of the snapshot."""

from __future__ import annotations

from typing import Any

from tinystudio.core.data import last_user_message, reference_answer
from tinystudio.core.llm import LLMClient, LLMError
from tinystudio.core.project import now
from tinystudio.training.recommend import recommend

SECTIONS = ("task_summary", "task_characterization", "knowledge_requirements", "success_criteria", "edge_cases", "input_analysis", "output_analysis")

PROMPT = """You are analysing a dataset that will be used to fine-tune a small language model.
Goal of the model: {goal}
Training examples: {count} (a sample follows). Average example length: {avg_chars} characters.

Sample conversations:
{examples}

Write a concise analysis for the person configuring the fine-tuning run. Return JSON only with these keys, each a short paragraph or a list of short bullet strings:
{{
  "task_summary": "...",
  "task_characterization": "... (classification, extraction, generation, QA, transformation; single or multi-turn; domain)",
  "knowledge_requirements": ["..."],
  "success_criteria": ["..."],
  "edge_cases": ["..."],
  "input_analysis": "... (what the user turns look like: length, format, variability)",
  "output_analysis": "... (what the assistant turns look like: format, length, consistency)"
}}"""


def analyze(train_rows: list[dict[str, Any]], goal: str, client: LLMClient, sample: int = 8) -> dict[str, Any]:
    examples = "\n\n".join(f"USER: {last_user_message(row)}\nASSISTANT: {reference_answer(row)}" for row in train_rows[:sample])
    sizes = [sum(len(m["content"]) for m in row["messages"]) for row in train_rows] or [0]
    reply = client.chat_json([{"role": "user", "content": PROMPT.format(goal=goal or "(not stated)", count=len(train_rows), avg_chars=sum(sizes) // len(sizes), examples=examples)}], max_tokens=1600)
    if not isinstance(reply, dict):
        raise LLMError("analysis did not return an object")
    return {"created": now(), "analysis": {key: reply.get(key, "") for key in SECTIONS}, "recommendation": recommend(train_rows, goal)}
