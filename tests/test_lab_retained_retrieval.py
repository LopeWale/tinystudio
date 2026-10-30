"""A query consumes frozen corpus bytes, without rebuilding or losing lineage."""
import hashlib
import json
from pathlib import Path

import pytest

from tinystudio.lab import retrieval
from tinystudio.lab.retrieval_selection import validate_retrieval_selection


class Encoder:
    def __init__(self, binding=None):
        self.binding = binding

    def identity(self):
        return {"artifact_id": "embed", "sha256": "directory", "artifact_sha256": "archive",
                "dimension": 2, "pooling": "test unit vectors"}

    def encode(self, texts):
        return [[1.0, 0.0] if "pressure" in text else [0.0, 1.0] for text in texts]


def retained_spec(path, mode=None):
    params = {"operation": "query", "query": "pressure", "top_k": 1}
    if mode:
        params["mode"] = mode
    return {"definition": {"inputs": [{"artifact_id": "retained", "role": "retrieval_index",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}], "parameters": params},
        "input_paths": {"retained": str(path)}}


def run(spec, output):
    return retrieval.execute_retrieval(spec, output, lambda _: None, lambda: False)


@pytest.mark.parametrize("mode", ["lexical", "structured", "episode", "semantic", "hybrid"])
def test_retained_bytes_and_ranked_evidence_survive_renamed_download(tmp_path, monkeypatch, mode):
    dense = mode in {"semantic", "hybrid"}
    source = tmp_path / "source.json"
    source.write_text(json.dumps([{"text": "pressure alarm", "unit": "A"}, {"text": "cooling", "unit": "B"}]))
    built = retrieval.build_index([source], tmp_path / "build", mode, encoder=Encoder() if dense else None)
    original = Path(built["path"]).read_bytes()
    downloaded = tmp_path / "server-artifact-id.json"
    downloaded.write_bytes(original)
    source.unlink()
    spec = retained_spec(downloaded)
    spec["definition"]["parameters"]["filters"] = {"unit": "A"}
    if dense:
        spec["definition"]["inputs"].append({"artifact_id": "embed", "role": "embedding_model", "sha256": "archive"})
        real_resolve = retrieval.resolve_role
        monkeypatch.setattr(retrieval, "resolve_role", lambda spec, role, **kwargs:
            {"directory_sha256": "directory"} if role == "embedding_model" else real_resolve(spec, role, **kwargs))
        monkeypatch.setattr(retrieval, "SemanticEncoder", Encoder)
    monkeypatch.setattr(retrieval, "build_index", lambda *a, **kw: pytest.fail("query rebuilt retained corpus"))
    result = run(spec, tmp_path / "query")
    published = next(item for item in result["artifacts"] if item["role"] == "retrieval_index")
    assert Path(published["path"]).read_bytes() == original
    assert Path(published["path"]).parent == tmp_path / "query"
    trace = result["records"][0]
    assert trace["mode"] == mode
    assert trace["hits"][0]["content"] == {"text": "pressure alarm", "unit": "A"}
    assert trace["hits"][0]["id"] == json.loads(original)["documents"][0]["id"]
    if dense:
        assert trace["hits"][0]["semantic_score"] == 1.0
    assert trace["corpus_sha256"] == built["corpus_sha256"]
    assert result["qualification"]["origin_index"] == trace["trace"]["origin_index"] == {
        "artifact_id": "retained", "sha256": hashlib.sha256(original).hexdigest()}
    assert result["qualification"]["qualified"] is True


@pytest.mark.parametrize("mutate,match", [
    (lambda d: d["parameters"].update(operation="other"), "operation"),
    (lambda d: d["parameters"].update(operation=[]), "operation"),
    (lambda d: d["parameters"].update(mode={}), "mode"),
    (lambda d: d["parameters"].update(top_k=True), "top_k"),
    (lambda d: d["parameters"].update(top_k="5"), "top_k"),
    (lambda d: d["parameters"].update(queries="pressure"), "query or queries"),
    (lambda d: d["parameters"].update(query=123), "queries"),
    (lambda d: d["parameters"].update(filters=[]), "filters"),
    (lambda d: d["parameters"].update(records=[]), "corpus"),
    (lambda d: d.update(inputs=[]), "exactly one"),
    (lambda d: d["inputs"].append({"role": "source"}), "corpus"),
    (lambda d: d["inputs"][0].update(visibility="evaluation"), "model-visible"),
    (lambda d: d["inputs"].append(dict(d["inputs"][0])), "exactly one"),
    (lambda d: d.update(retrieval={"mode": "episode"}, parameters={"operation": "query", "mode": "lexical"}), "conflicting"),
    (lambda d: d["parameters"].update(operation="build"), "build"),
])
def test_authored_query_conflicts_fail_without_output(tmp_path, mutate, match):
    index = retrieval.build_index([], tmp_path / "build", records=[{"text": "pressure"}])
    spec = retained_spec(Path(index["path"]))
    mutate(spec["definition"])
    with pytest.raises(ValueError, match=match):
        run(spec, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_tamper_mode_mismatch_and_no_hits(tmp_path):
    index = retrieval.build_index([], tmp_path / "build", records=[{"text": "pressure"}])
    path = Path(index["path"])
    spec = retained_spec(path, "episode")
    with pytest.raises(ValueError, match="mode must match"):
        run(spec, tmp_path / "bad-mode")
    spec = retained_spec(path)
    spec["definition"]["parameters"]["query"] = "missing"
    assert run(spec, tmp_path / "empty")["records"][0]["hits"] == []
    path.write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        run(spec, tmp_path / "tampered")


@pytest.mark.parametrize("mutate,match", [
    (lambda p: p.update(mode="unsupported"), "mode"),
    (lambda p: p.update(documents=[]), "nonempty"),
    (lambda p: p["documents"][0].update(embedding=[1.0]), "dimension"),
    (lambda p: p["documents"][0].update(embedding=[float("nan"), 0]), "finite"),
    (lambda p: p["documents"][0].update(embedding=[2.0, 0]), "unit"),
    (lambda p: p["documents"][0]["content"].update(visibility="truth"), "protection"),
    (lambda p: p.update(restricted_evidence=[{}]), "restricted"),
    (lambda p: p.update(research_override={"kind": "anything"}), "restricted"),
])
def test_loaded_index_invariants_fail_even_with_authentic_artifact_hash(tmp_path, mutate, match):
    index = retrieval.build_index([], tmp_path / "build", "semantic", records=[{"text": "pressure"}], encoder=Encoder())
    payload = json.loads(Path(index["path"]).read_bytes())
    mutate(payload)
    path = tmp_path / "download.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match=match):
        run(retained_spec(path), tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_restricted_qualification_and_original_override_survive_query(tmp_path):
    override = {"kind": "retrieval.include_restricted_evidence", "reason": "research"}
    index = retrieval.build_index([], tmp_path / "build", records=[{"text": "pressure", "partition": "test"}], research_override=override)
    result = run(retained_spec(Path(index["path"])), tmp_path / "out")
    assert result["qualification"]["qualified"] is False
    assert result["records"][0]["trace"]["research_override"] == override


def test_dense_wrong_identity_and_short_query_vector_fail(tmp_path, monkeypatch):
    index = retrieval.build_index([], tmp_path / "build", "semantic", records=[{"text": "pressure"}], encoder=Encoder())
    wrong = Encoder()
    wrong.identity = lambda: {**Encoder().identity(), "sha256": "wrong"}
    with pytest.raises(ValueError, match="must match"):
        retrieval.query_index(index["path"], "pressure", encoder=wrong)
    short = Encoder()
    short.encode = lambda _: [[1.0]]
    with pytest.raises(ValueError, match="dimension"):
        retrieval.query_index(index["path"], "pressure", encoder=short)
    scaled = Encoder()
    scaled.encode = lambda _: [[2.0, 0.0]]
    with pytest.raises(ValueError, match="unit"):
        retrieval.query_index(index["path"], "pressure", encoder=scaled)


def test_default_build_mode_does_not_ignore_an_embedding_selection():
    with pytest.raises(ValueError, match="requires semantic/hybrid"):
        validate_retrieval_selection({"parameters": {}, "inputs": [{"role": "embedding_model"}]})


def test_build_rejects_missing_document_vectors_before_zip_truncation(tmp_path):
    short = Encoder()
    short.encode = lambda _: [[1.0, 0.0]]
    with pytest.raises(ValueError, match="document count"):
        retrieval.build_index([], tmp_path / "out", "semantic", records=[{"text": "pressure"}, {"text": "cooling"}], encoder=short)
    assert not (tmp_path / "out").exists()


def test_source_restrictions_cannot_be_hidden_by_replacing_document_provenance(tmp_path):
    source = tmp_path / "truth.json"
    source.write_text(json.dumps({"visibility": "truth", "rows": [{"text": "pressure"}]}))
    override = {"kind": "retrieval.include_restricted_evidence", "reason": "research"}
    index = retrieval.build_index([source], tmp_path / "build", research_override=override)
    payload = json.loads(Path(index["path"]).read_bytes())
    payload["documents"][0]["provenance"]["metadata"] = {}
    payload["documents"][0]["provenance"]["protection"] = "model"
    payload.update(restricted_evidence=[], research_override=None)
    renamed = tmp_path / "retained.json"
    renamed.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="source metadata"):
        run(retained_spec(renamed), tmp_path / "out")


def test_documents_must_keep_their_original_content_identity(tmp_path):
    index = retrieval.build_index([], tmp_path / "build", records=[{"text": "pressure"}])
    payload = json.loads(Path(index["path"]).read_bytes())
    payload["documents"][0]["content"]["text"] = "substituted"
    renamed = tmp_path / "retained.json"
    renamed.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="document identity"):
        run(retained_spec(renamed), tmp_path / "out")
