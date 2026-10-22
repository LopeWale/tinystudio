"""Dataset quality: statistics for the "before training" view and the cleaning operations behind it.

Everything here is deterministic and needs no model. ``profile`` computes the
numbers the quality page draws (length histograms, bucket/label/source
distributions, duplicates, outliers, rating distribution, format checks) and
``clean`` applies the operations a user picks (dedupe, drop outliers, trim
whitespace, drop empty, filter by rating or label, require JSON answers).
Every cleaning run is written to ``datasets/<name>/cleaning.jsonl`` so the
change is traceable.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from tinystudio.core.data import BUCKETS, last_user_message, reference_answer
from tinystudio.core.project import now

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)")
URL = re.compile(r"https?://\S+")
# Redaction uses stricter shapes than detection so plain numbers in answers (prices, years, JSON values) survive.
PHONE_STRICT = re.compile(r"(?<![\w.])(?:\+?\d{1,3}[ .-]?)?(?:\(\d{2,4}\)[ .-]?|\d{2,4}[ .-])\d{3,4}[ .-]?\d{3,4}(?![\w.])")
NATIONAL_ID = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")  # US SSN shape
CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,16}(?<=\d)(?!\d)")
REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = ((EMAIL, "[EMAIL]"), (CARD, "[CARD]"), (NATIONAL_ID, "[ID]"), (PHONE_STRICT, "[PHONE]"))


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _fingerprint(text: str) -> str:
    return hashlib.sha1(_normalize(text).encode("utf-8")).hexdigest()[:16]


def _shingles(text: str) -> set[str]:
    """Word n-grams: bigrams for short texts (under 8 words), trigrams otherwise."""
    words = _normalize(text).split()
    n = 2 if len(words) < 8 else 3
    if len(words) < n:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}


def _histogram(values: list[int], bins: int = 12) -> dict[str, Any]:
    if not values:
        return {"bins": [], "counts": []}
    lo, hi = min(values), max(values)
    if lo == hi:
        return {"bins": [lo, hi + 1], "counts": [len(values)]}
    width = max(1, math.ceil((hi - lo + 1) / bins))
    edges = [lo + i * width for i in range(bins + 1)]
    counts = [0] * bins
    for v in values:
        index = min(bins - 1, (v - lo) // width)
        counts[index] += 1
    return {"bins": edges, "counts": counts}


def _summary(values: list[int]) -> dict[str, float | int]:
    if not values:
        return {"min": 0, "median": 0, "mean": 0, "p95": 0, "max": 0}
    ordered = sorted(values)
    return {
        "min": ordered[0],
        "median": statistics.median(ordered),
        "mean": round(statistics.fmean(ordered), 1),
        "p95": ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))],
        "max": ordered[-1],
    }


def _looks_json(text: str) -> bool:
    try:
        json.loads(text)
        return True
    except (json.JSONDecodeError, TypeError):
        return False


def profile(rows: list[dict[str, Any]], near_duplicate_threshold: float = 0.7, max_near_pairs: int = 200) -> dict[str, Any]:
    """Compute everything the quality page shows for one dataset."""
    user_lengths = [len(last_user_message(r)) for r in rows]
    answer_lengths = [len(reference_answer(r)) for r in rows]
    total_chars = [sum(len(m["content"]) for m in r["messages"]) for r in rows]
    turns = [len(r["messages"]) for r in rows]
    with_system = sum(1 for r in rows if r["messages"][0]["role"] == "system")
    system_prompts = Counter(r["messages"][0]["content"] for r in rows if r["messages"][0]["role"] == "system")

    # exact duplicates on the user turn and on the whole conversation
    by_user: dict[str, list[int]] = {}
    by_conversation: dict[str, list[int]] = {}
    for i, r in enumerate(rows):
        by_user.setdefault(_fingerprint(last_user_message(r)), []).append(i)
        by_conversation.setdefault(_fingerprint(json.dumps(r["messages"], sort_keys=True)), []).append(i)
    duplicate_user_groups = [ids for ids in by_user.values() if len(ids) > 1]
    duplicate_conversation_groups = [ids for ids in by_conversation.values() if len(ids) > 1]

    # near duplicates on the user turn (Jaccard over word trigrams), bounded for big sets
    near_pairs: list[dict[str, Any]] = []
    if len(rows) <= 3000:
        shingles = [_shingles(last_user_message(r)) for r in rows]
        index: dict[str, list[int]] = {}
        for i, sh in enumerate(shingles):
            for s in sh:
                index.setdefault(s, []).append(i)
        candidates: set[tuple[int, int]] = set()
        for ids in index.values():
            if 1 < len(ids) <= 50:
                for a in ids:
                    for b in ids:
                        if a < b:
                            candidates.add((a, b))
        for a, b in sorted(candidates):
            sa, sb = shingles[a], shingles[b]
            if not sa or not sb:
                continue
            jaccard = len(sa & sb) / len(sa | sb)
            if jaccard >= near_duplicate_threshold and _fingerprint(last_user_message(rows[a])) != _fingerprint(last_user_message(rows[b])):
                near_pairs.append({"a": a, "b": b, "similarity": round(jaccard, 3)})
                if len(near_pairs) >= max_near_pairs:
                    break

    # outliers by length (IQR fences on total characters)
    ordered = sorted(total_chars)
    q1 = ordered[len(ordered) // 4] if ordered else 0
    q3 = ordered[(3 * len(ordered)) // 4] if ordered else 0
    iqr = q3 - q1
    low_fence, high_fence = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    too_short = [i for i, c in enumerate(total_chars) if c < max(20, low_fence)]
    too_long = [i for i, c in enumerate(total_chars) if c > high_fence and iqr > 0]
    empty_like = [i for i, r in enumerate(rows) if len(reference_answer(r).strip()) < 2 or len(last_user_message(r).strip()) < 2]

    answers_json = sum(1 for r in rows if _looks_json(reference_answer(r)))
    ratings = [r["rating"] for r in rows if isinstance(r.get("rating"), (int, float))]
    pii = {
        "emails": sum(1 for r in rows for m in r["messages"] if EMAIL.search(m["content"])),
        "phones": sum(1 for r in rows for m in r["messages"] if PHONE.search(m["content"])),
        "urls": sum(1 for r in rows for m in r["messages"] if URL.search(m["content"])),
        "ids": sum(1 for r in rows for m in r["messages"] if NATIONAL_ID.search(m["content"])),
        "cards": sum(1 for r in rows for m in r["messages"] if CARD.search(m["content"])),
        "redactable_rows": sum(1 for r in rows if any(rx.search(m["content"]) for m in r["messages"] for rx, _ in REDACTIONS)),
    }
    labels = Counter(l for r in rows for l in r.get("labels", []))
    sources = Counter((r.get("source") or "uploaded").split(":")[0] for r in rows)
    buckets = Counter(r["bucket"] for r in rows)

    return {
        "count": len(rows),
        "buckets": {b: buckets.get(b, 0) for b in BUCKETS},
        "labels": labels.most_common(30),
        "sources": sources.most_common(10),
        "turns": {"mean": round(statistics.fmean(turns), 2) if turns else 0, "max": max(turns, default=0), "with_system_prompt": with_system, "distinct_system_prompts": len(system_prompts)},
        "user_chars": {"summary": _summary(user_lengths), "histogram": _histogram(user_lengths)},
        "answer_chars": {"summary": _summary(answer_lengths), "histogram": _histogram(answer_lengths)},
        "total_chars": {"summary": _summary(total_chars), "histogram": _histogram(total_chars)},
        "approx_tokens": {"total": sum(total_chars) // 4, "per_row_mean": round(statistics.fmean(total_chars) / 4, 1) if total_chars else 0},
        "duplicates": {
            "exact_user_groups": len(duplicate_user_groups),
            "exact_user_rows": sum(len(g) - 1 for g in duplicate_user_groups),
            "exact_conversation_groups": len(duplicate_conversation_groups),
            "exact_conversation_rows": sum(len(g) - 1 for g in duplicate_conversation_groups),
            "near_pairs": near_pairs[:50],
            "near_pair_count": len(near_pairs),
            "examples": [[last_user_message(rows[i]) for i in g[:3]] for g in duplicate_user_groups[:5]],
        },
        "outliers": {"too_short": too_short[:200], "too_long": too_long[:200], "empty_like": empty_like[:200], "fences": {"low": round(max(20, low_fence)), "high": round(high_fence)}},
        "format": {"answers_json": answers_json, "answers_json_pct": round(100 * answers_json / len(rows), 1) if rows else 0},
        "ratings": {"count": len(ratings), "histogram": _histogram([int(round(x)) for x in ratings], bins=10) if ratings else {"bins": [], "counts": []}, "mean": round(statistics.fmean(ratings), 2) if ratings else None},
        "pii": pii,
        "generated": now(),
    }


@dataclass
class CleanSpec:
    dedupe_user: bool = False
    dedupe_conversation: bool = False
    drop_near_duplicates: bool = False
    near_duplicate_threshold: float = 0.7
    drop_empty: bool = False
    trim_whitespace: bool = False
    min_total_chars: int | None = None
    max_total_chars: int | None = None
    min_rating: float | None = None
    require_json_answer: bool = False
    drop_labels: list[str] = field(default_factory=list)
    keep_labels: list[str] = field(default_factory=list)
    drop_indices: list[int] = field(default_factory=list)
    redact_pii: bool = False  # replace emails, card numbers, national ids, and phone numbers with placeholders (rows are kept)


def redact_pii(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Replace PII-shaped spans in every message (and a preference row's `rejected` text) with placeholders. Returns (rows, changed_rows)."""

    def scrub(text: str) -> str:
        for pattern, placeholder in REDACTIONS:
            text = pattern.sub(placeholder, text)
        return text

    out: list[dict[str, Any]] = []
    changed = 0
    for row in rows:
        messages = [{**m, "content": scrub(m["content"])} for m in row["messages"]]
        rejected = scrub(row["rejected"]) if isinstance(row.get("rejected"), str) else None
        touched = any(a["content"] != b["content"] for a, b in zip(messages, row["messages"])) or (rejected is not None and rejected != row["rejected"])
        if touched:
            changed += 1
            out.append({**row, "messages": messages, **({"rejected": rejected} if rejected is not None else {})})
        else:
            out.append(row)
    return out, changed


def clean(rows: list[dict[str, Any]], spec: CleanSpec) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Return (kept rows, counts of rows removed per reason). Order is preserved; first occurrence wins."""
    removed: Counter[str] = Counter()
    kept: list[dict[str, Any]] = []
    seen_user: set[str] = set()
    seen_conv: set[str] = set()
    kept_shingles: list[set[str]] = []
    drop_set = set(spec.drop_indices)
    for i, row in enumerate(rows):
        if i in drop_set:
            removed["selected"] += 1
            continue
        if spec.trim_whitespace:
            row = {**row, "messages": [{**m, "content": m["content"].strip()} for m in row["messages"]]}
        user, answer = last_user_message(row), reference_answer(row)
        total = sum(len(m["content"]) for m in row["messages"])
        if spec.drop_empty and (len(user.strip()) < 2 or len(answer.strip()) < 2):
            removed["empty"] += 1
            continue
        if spec.min_total_chars is not None and total < spec.min_total_chars:
            removed["too_short"] += 1
            continue
        if spec.max_total_chars is not None and total > spec.max_total_chars:
            removed["too_long"] += 1
            continue
        if spec.min_rating is not None and isinstance(row.get("rating"), (int, float)) and row["rating"] < spec.min_rating:
            removed["low_rating"] += 1
            continue
        if spec.require_json_answer and not _looks_json(answer):
            removed["not_json"] += 1
            continue
        labels = set(row.get("labels", []))
        if spec.drop_labels and labels & set(spec.drop_labels):
            removed["dropped_label"] += 1
            continue
        if spec.keep_labels and not (labels & set(spec.keep_labels)):
            removed["label_not_kept"] += 1
            continue
        if spec.dedupe_user:
            fp = _fingerprint(user)
            if fp in seen_user:
                removed["duplicate_user"] += 1
                continue
            seen_user.add(fp)
        if spec.dedupe_conversation:
            fp = _fingerprint(json.dumps(row["messages"], sort_keys=True))
            if fp in seen_conv:
                removed["duplicate_conversation"] += 1
                continue
            seen_conv.add(fp)
        if spec.drop_near_duplicates:
            sh = _shingles(user)
            if sh and any(len(sh & other) / len(sh | other) >= spec.near_duplicate_threshold for other in kept_shingles if other):
                removed["near_duplicate"] += 1
                continue
            kept_shingles.append(sh)
        kept.append(row)
    return kept, dict(removed)
