from __future__ import annotations

from pathlib import Path

import pytest

from tinystudio import evaluate
from tinystudio.documents import chunk_text, extract_text
from tinystudio.llm import LLMError, extract_json
from tinystudio.project import Project, ProjectError


def test_code_metrics():
    assert evaluate.exact_match("Hello, World!", "hello world") == 1.0
    assert evaluate.exact_match("Hello", "world") == 0.0
    assert evaluate.token_f1("the cat sat", "the cat") == pytest.approx(0.8)
    assert 0.5 < evaluate.similarity("turn on kitchen lights", "turn on the kitchen lights") < 1.0
    assert evaluate.json_valid('{"a":1}', '{"a": 1}') == 1.0
    assert evaluate.json_valid('```json\n{"a":2}\n```', '{"a": 1}') == 0.5
    assert evaluate.json_valid("not json", "{}") == 0.0
    assert evaluate.json_valid('{"a":1}', "free text reference") == 1.0
    assert evaluate.contains_reference("The answer is 42, obviously.", "the answer is 42") == 1.0


def test_extract_json_handles_fences_prose_and_think_blocks():
    assert extract_json('Sure! ```json\n{"pairs": [1]}\n```') == {"pairs": [1]}
    assert extract_json('<think>hmm</think>[1, 2]') == [1, 2]
    assert extract_json('prefix {"a": {"b": 1}} trailing words') == {"a": {"b": 1}}
    with pytest.raises(LLMError):
        extract_json("no json here")


def test_create_metric_validation(tmp_path: Path):
    project = Project.create(tmp_path / "p", "p")
    with pytest.raises(ProjectError, match="at least one"):
        evaluate.create_metric(project, "empty", "rubric")
    with pytest.raises(ProjectError, match="--check"):
        evaluate.create_metric(project, "c", "code", check="nope")
    metric = evaluate.create_metric(project, "fmt", "code", check="json_valid")
    assert evaluate.load_metric(project, "fmt") == metric
    with pytest.raises(ProjectError, match="already exists"):
        evaluate.create_metric(project, "fmt", "code", check="json_valid")


def test_rubric_scoring_is_ratio_of_rules():
    class FakeClient:
        def chat_json(self, messages, **_):
            return {"should": [{"rule": "a", "pass": True}, {"rule": "b", "pass": False}], "should_not": [{"rule": "c", "pass": True}]}

    row = {"messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "ref"}]}
    score, reason, detail = evaluate.judge_rubric(FakeClient(), {"should": ["a", "b"], "should_not": ["c"]}, row, "pred")
    assert score == pytest.approx(2 / 3)
    assert "failed: b" in reason
    assert len(detail["verdicts"]) == 3


def test_documents_text_and_chunks(tmp_path: Path):
    html = tmp_path / "doc.html"
    html.write_text("<html><style>x{}</style><body><h1>Title</h1><p>One &amp; two</p><script>bad()</script></body></html>", encoding="utf-8")
    text = extract_text(html)
    assert "Title" in text and "One & two" in text and "bad()" not in text
    long = "\n\n".join(f"paragraph {i} " + "word " * 50 for i in range(30))
    chunks = chunk_text(long, max_chars=1000, overlap=100)
    assert len(chunks) > 3 and all(len(c) <= 1000 for c in chunks)
