"""Synthetic datapoints, curation, augmentation, and auto-labeling with a local helper LLM.

The generation flow follows the one Prem published in its fork of Meta's
synthetic-data-kit (ingest → create QA pairs → curate by rating → save) and
the knobs Studio's "Add dataset" wizard exposes: sources (files or web
pages), pairs per source, rules and constraints, question/answer guidance
with an ``{EXTRACTED_TEXT}`` placeholder, and a creativity level that maps to
the sampling temperature. Everything produced is tagged ``source:
"synthetic:..."`` so the split step can keep synthetic rows out of
validation.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from tinystudio.core.documents import DocumentError, _strip_html, chunk_text, extract_text, youtube_transcript, youtube_video_id
from tinystudio.core.llm import LLMClient, LLMError

PLACEHOLDER = "{EXTRACTED_TEXT}"


@dataclass
class SynthSpec:
    """What Studio's wizard collects, minus credits."""

    pairs_per_source: int = 25
    temperature: float = 0.3  # Studio's "creativity level" slider, 0.0-1.0
    goal: str = ""
    rules: list[str] = field(default_factory=list)
    question_format: str = ""  # free text; may contain {EXTRACTED_TEXT}
    answer_format: str = ""  # free text or a JSON template
    example_questions: list[str] = field(default_factory=list)
    example_answers: list[str] = field(default_factory=list)
    system_prompt: str | None = None
    curate_threshold: float = 0.0  # 0 disables rating; Prem's kit defaults to 7/10
    per_call: int = 5


SYNTH_PROMPT = """Create {count} question-answer pairs from this text for LLM training.
Goal of the assistant being trained: {goal}

Rules:
1. Questions must be about important facts in the text and sound like real users.
2. Answers must be directly supported by the text, self-contained, and in the assistant's intended style.
{extra_rules}
{guidance}
Return JSON only:
{{"pairs": [{{"question": "...", "answer": "..."}}]}}

Text:
<<<
{text}
>>>"""

RATING_PROMPT = """Rate each question-answer pair on a scale from 1-10, based on:
- Accuracy (0-3): factual correctness against the source text
- Relevance (0-2): relevance to the content and the goal "{goal}"
- Clarity (0-2): clear language
- Usefulness (0-3): value for model learning

Return JSON only, one object per pair in the same order:
{{"ratings": [{{"index": 0, "rating": 8}}, {{"index": 1, "rating": 5}}]}}

Source text:
<<<
{text}
>>>

QA pairs to rate:
{pairs}"""

AUGMENT_PROMPT = """Rewrite the following user request {count} different ways, keeping the exact same meaning so that the given answer still fits.
Vary wording, length, and tone (terse, polite, typo-ridden, formal).

USER REQUEST: {user}
ANSWER THAT MUST STILL FIT: {assistant}

Return JSON: {{"variants": ["...", "..."]}}"""

LABEL_PROMPT = """Assign exactly one label to each conversation below. Labels and their meaning:
{labels}

Return JSON only: {{"labels": [{{"index": 0, "label": "<label name>"}}, ...]}} covering every index.

Conversations:
{items}"""


@dataclass(frozen=True)
class Source:
    name: str
    text: str
    kind: str  # "file" | "url"


def load_sources(paths: list[Path], urls: list[str], timeout: float = 30.0) -> list[Source]:
    sources: list[Source] = []
    for path in paths:
        sources.append(Source(path.name, extract_text(path), "file"))
    for url in urls:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise DocumentError(f"{url}: only http(s) URLs are supported as web sources")
        if youtube_video_id(url):
            sources.append(Source(f"youtube:{youtube_video_id(url)}", youtube_transcript(url), "youtube"))
            continue
        try:
            response = httpx.get(url, follow_redirects=True, timeout=timeout, headers={"User-Agent": "tinystudio/0.1"})
            response.raise_for_status()
        except httpx.HTTPError as cause:
            raise DocumentError(f"{url}: {cause}") from cause
        content_type = response.headers.get("content-type", "")
        text = _strip_html(response.text) if "html" in content_type else response.text
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        sources.append(Source(f"{parsed.netloc}{parsed.path}".rstrip("/") or parsed.netloc, text, "url"))
    empty = [s.name for s in sources if not s.text]
    if empty:
        raise DocumentError("no text could be extracted from: " + ", ".join(empty))
    return sources


def _guidance(spec: SynthSpec, chunk: str) -> str:
    parts: list[str] = []
    if spec.question_format:
        parts.append("Question guidance: " + spec.question_format.replace(PLACEHOLDER, "the source text"))
    if spec.answer_format:
        parts.append("Answer format: " + spec.answer_format.replace(PLACEHOLDER, "the source text"))
    examples = list(zip(spec.example_questions, spec.example_answers))
    if examples:
        parts.append("Examples of the expected style:\n" + "\n".join(f"Q: {q}\nA: {a}" for q, a in examples[:3]))
    return ("\n".join(parts) + "\n") if parts else ""


def _rules(spec: SynthSpec) -> str:
    return "".join(f"{i}. {rule}\n" for i, rule in enumerate(spec.rules, start=3))


def synthesize(client: LLMClient, sources: list[Source], spec: SynthSpec, progress: Any = print) -> list[dict[str, Any]]:
    """Generate ``pairs_per_source`` grounded QA pairs for every source, then optionally curate."""
    rows: list[dict[str, Any]] = []
    for source in sources:
        chunks = chunk_text(source.text)
        if not chunks:
            progress(f"{source.name}: no text, skipped")
            continue
        wanted_total = spec.pairs_per_source
        rng = random.Random(len(source.text))
        order = list(range(len(chunks)))
        rng.shuffle(order)
        produced: list[dict[str, Any]] = []
        idle = 0
        position = 0
        while len(produced) < wanted_total and idle < max(3, len(order)):
            chunk = chunks[order[position % len(order)]]
            position += 1
            wanted = min(spec.per_call, wanted_total - len(produced))
            prompt = SYNTH_PROMPT.format(count=wanted, goal=spec.goal or "(not stated)", extra_rules=_rules(spec), guidance=_guidance(spec, chunk), text=chunk)
            try:
                reply = client.chat_json([{"role": "user", "content": prompt}], temperature=max(0.0, min(1.0, spec.temperature)))
            except LLMError as cause:
                progress(f"{source.name}: {cause}")
                idle += 1
                continue
            pairs = reply.get("pairs", []) if isinstance(reply, dict) else reply
            added = 0
            for pair in pairs if isinstance(pairs, list) else []:
                if not isinstance(pair, dict):
                    continue
                question = str(pair.get("question") or pair.get("user") or "").strip()
                answer = str(pair.get("answer") or pair.get("assistant") or "").strip()
                if not question or not answer:
                    continue
                produced.append({"question": question, "answer": answer, "chunk": chunk})
                added += 1
                if len(produced) >= wanted_total:
                    break
            idle = 0 if added else idle + 1
            progress(f"{source.name}: {len(produced)}/{wanted_total} pairs")
        if spec.curate_threshold > 0 and produced:
            produced = curate(client, produced, spec, progress)
        for item in produced:
            messages = ([{"role": "system", "content": spec.system_prompt}] if spec.system_prompt else []) + [
                {"role": "user", "content": item["question"]},
                {"role": "assistant", "content": item["answer"]},
            ]
            record: dict[str, Any] = {"messages": messages, "bucket": "uncategorized", "labels": ["synthetic"], "source": f"synthetic:{source.name}"}
            if "rating" in item:
                record["rating"] = item["rating"]
            rows.append(record)
    if not rows:
        raise LLMError("the helper model produced no usable pairs; check TINYSTUDIO_LLM_MODEL and the server log")
    return rows


def curate(client: LLMClient, items: list[dict[str, Any]], spec: SynthSpec, progress: Any = print) -> list[dict[str, Any]]:
    """Prem's curate step: rate each pair 1-10 and keep those at or above the threshold."""
    kept: list[dict[str, Any]] = []
    batch = 8
    for start in range(0, len(items), batch):
        group = items[start : start + batch]
        listing = "\n".join(f"[{i}] Q: {it['question']}\n    A: {it['answer']}" for i, it in enumerate(group))
        try:
            reply = client.chat_json([{"role": "user", "content": RATING_PROMPT.format(goal=spec.goal or "(not stated)", text=group[0]["chunk"], pairs=listing)}], temperature=0.1)
        except LLMError as cause:
            progress(f"rating skipped for {len(group)} pairs: {cause}")
            kept.extend(group)
            continue
        ratings = {int(r["index"]): float(r["rating"]) for r in (reply.get("ratings", []) if isinstance(reply, dict) else []) if isinstance(r, dict) and "index" in r and "rating" in r}
        for i, item in enumerate(group):
            rating = ratings.get(i)
            if rating is None:
                kept.append(item)
                continue
            item["rating"] = rating
            if rating >= spec.curate_threshold:
                kept.append(item)
    progress(f"curated: kept {len(kept)} of {len(items)} (threshold {spec.curate_threshold:g})")
    return kept


def augment_prompt(count: int, user: str, assistant: str, instructions: str = "", avoid: str = "") -> str:
    """Studio's augmentation form: a creativity level plus positive (generate more of) and negative (avoid) instructions."""
    prompt = AUGMENT_PROMPT.format(count=count, user=user, assistant=assistant)
    extra = []
    if instructions.strip():
        extra.append(f"Generate more of: {instructions.strip()}")
    if avoid.strip():
        extra.append(f"Avoid: {avoid.strip()}")
    if extra:
        prompt = prompt.replace("\nReturn JSON:", "\n" + "\n".join(extra) + "\n\nReturn JSON:")
    return prompt


def augment(client: LLMClient, rows: list[dict[str, Any]], count: int, variants_per_row: int = 3, seed: int = 0, progress: Any = print, instructions: str = "", avoid: str = "", temperature: float = 0.8) -> list[dict[str, Any]]:
    if not rows:
        raise LLMError("nothing to augment")
    rng = random.Random(seed)
    produced: list[dict[str, Any]] = []
    attempts = 0
    while len(produced) < count and attempts < 4 * count:
        attempts += 1
        row = rng.choice(rows)
        user = next(m["content"] for m in reversed(row["messages"]) if m["role"] == "user")
        assistant = row["messages"][-1]["content"]
        try:
            reply = client.chat_json([{"role": "user", "content": augment_prompt(variants_per_row, user, assistant, instructions, avoid)}], temperature=temperature)
        except LLMError as cause:
            progress(f"skipping one: {cause}")
            continue
        variants = reply.get("variants", []) if isinstance(reply, dict) else reply
        for variant in variants if isinstance(variants, list) else []:
            text = str(variant).strip()
            if not text or text == user:
                continue
            messages = [dict(m) for m in row["messages"]]
            for message in reversed(messages):
                if message["role"] == "user":
                    message["content"] = text
                    break
            produced.append({"messages": messages, "bucket": "uncategorized", "labels": list(row.get("labels", [])) + ["augmented"], "source": "synthetic:augment"})
            if len(produced) >= count:
                break
        progress(f"{len(produced)}/{count} variants")
    return produced


def auto_label(client: LLMClient, rows: list[dict[str, Any]], labels: list[dict[str, str]], progress: Any = print, batch: int = 10) -> list[dict[str, Any]]:
    """Assign one of the defined labels to every row (Studio's auto-labeling)."""
    if not labels:
        raise LLMError("define labels first")
    names = {label["name"] for label in labels}
    listing = "\n".join(f"- {label['name']}: {label.get('description', '')}" for label in labels)
    for start in range(0, len(rows), batch):
        group = rows[start : start + batch]
        items = "\n\n".join(f"[{i}] USER: {next(m['content'] for m in reversed(r['messages']) if m['role'] == 'user')}\nASSISTANT: {r['messages'][-1]['content']}" for i, r in enumerate(group))
        try:
            reply = client.chat_json([{"role": "user", "content": LABEL_PROMPT.format(labels=listing, items=items)}], temperature=0.0)
        except LLMError as cause:
            progress(f"labeling skipped for {len(group)} rows: {cause}")
            continue
        for entry in reply.get("labels", []) if isinstance(reply, dict) else []:
            if not isinstance(entry, dict):
                continue
            try:
                index = int(entry["index"])
            except (KeyError, TypeError, ValueError):
                continue
            label = str(entry.get("label", "")).strip()
            if 0 <= index < len(group) and label in names:
                existing = [l for l in group[index].get("labels", []) if l not in names]
                group[index]["labels"] = existing + [label]
        progress(f"labeled {min(start + batch, len(rows))}/{len(rows)}")
    return rows
