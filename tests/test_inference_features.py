"""Offline tests for ingestion formats, tool-call parsing, JSON-schema validation, and refine-from resolution."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from tinystudio.core import data
from tinystudio.core.documents import DocumentError, extract_text, youtube_video_id
from tinystudio.core.project import Project, write_json
from tinystudio.serving.runner import parse_tool_calls, validate_schema
from tinystudio.training.train import TrainConfig, create_job, create_pending_experiment, parent_weights


def test_docx_and_pptx_extraction(tmp_path: Path):
    docx = tmp_path / "a.docx"
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr("word/document.xml", '<w:document><w:body><w:p><w:r><w:t>Hello</w:t></w:r><w:r><w:t xml:space="preserve"> world &amp; co</w:t></w:r></w:p><w:p><w:r><w:t>Second</w:t></w:r></w:p></w:body></w:document>')
    assert extract_text(docx) == "Hello world & co\n\nSecond"
    pptx = tmp_path / "b.pptx"
    with zipfile.ZipFile(pptx, "w") as z:
        z.writestr("ppt/slides/slide2.xml", "<p:sld><a:p><a:r><a:t>two</a:t></a:r></a:p></p:sld>")
        z.writestr("ppt/slides/slide1.xml", "<p:sld><a:p><a:r><a:t>one</a:t></a:r></a:p></p:sld>")
    assert extract_text(pptx) == "one\n\ntwo"
    bad = tmp_path / "c.docx"
    bad.write_bytes(b"not a zip")
    with pytest.raises(DocumentError, match="not a Word document"):
        extract_text(bad)
    with pytest.raises(DocumentError, match="unsupported document type"):
        extract_text(tmp_path / "x.xyz")


def test_youtube_ids():
    assert youtube_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1s") == "dQw4w9WgXcQ"
    assert youtube_video_id("https://youtu.be/abc123xyz") == "abc123xyz"
    assert youtube_video_id("https://youtube.com/shorts/short12345") == "short12345"
    assert youtube_video_id("https://example.com/watch?v=1") is None


def test_parse_tool_calls_formats():
    hermes = parse_tool_calls('text\n<tool_call>\n{"name": "get_weather", "arguments": {"city": "Paris"}}\n</tool_call>\n<tool_call>{"name":"b","arguments":"{\\"x\\":1}"}</tool_call>')
    assert [c["function"]["name"] for c in hermes] == ["get_weather", "b"]
    assert json.loads(hermes[0]["function"]["arguments"]) == {"city": "Paris"} and json.loads(hermes[1]["function"]["arguments"]) == {"x": 1}
    llama = parse_tool_calls('{"name": "lookup", "parameters": {"q": 1}}')
    assert llama[0]["function"]["name"] == "lookup" and llama[0]["type"] == "function" and llama[0]["id"].startswith("call_")
    assert parse_tool_calls("no calls here") == [] and parse_tool_calls('{"not": "a call"}') == []


def test_validate_schema_subset():
    schema = {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "string", "enum": ["x", "y"]}, "c": {"type": "array", "items": {"type": "number"}}}, "required": ["a", "b"], "additionalProperties": False}
    assert validate_schema({"a": 1, "b": "x", "c": [1, 2.5]}, schema) == []
    problems = validate_schema({"a": True, "b": "z", "c": ["no"], "d": 1}, schema)
    assert any("$.a" in p for p in problems) and any("$.b" in p for p in problems) and any("$.c[0]" in p for p in problems) and any("$.d" in p for p in problems)
    assert validate_schema({"b": "x"}, schema) == ["$.a: required"]


def test_parent_weights_resolution(tmp_path: Path):
    project = Project.create(tmp_path / "p", "p")
    data.save_dataset(project, "d", [data.validate_datapoint({"messages": [{"role": "user", "content": f"q{i}"}, {"role": "assistant", "content": f"a{i}"}]}) for i in range(12)])
    data.autosplit(project, "d", 90)
    data.create_snapshot(project, "d", "s1")
    create_job(project, "j", "s1")
    n = create_pending_experiment(project, "j", TrainConfig(base_model="HuggingFaceTB/SmolLM2-135M-Instruct"))
    with pytest.raises(Exception, match="has not succeeded"):
        parent_weights(project, f"ft-j-{n}")
    exp = project.job_dir("j") / "experiments" / str(n)
    write_json(exp / "status.json", {"state": "succeeded"})
    (exp / "model").mkdir()
    assert parent_weights(project, f"j/{n}") == exp / "model"
    with pytest.raises(Exception, match="does not exist"):
        parent_weights(project, "j/99")
    with pytest.raises(Exception, match="job/number"):
        parent_weights(project, "nonsense")
    assert TrainConfig(base_model="x", refinetune_from="ft-j-1").refinetune_from == "ft-j-1"
