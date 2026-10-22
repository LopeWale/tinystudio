"""Repositories: PostgreSQL metadata, Qdrant (local mode) chunks and vectors, a tiny random BERT embedder."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import needs_tokenizer, tiny_bert_dir
from tinystudio import vectors
from tinystudio.core.project import ProjectError
from tinystudio.rag.repositories import normalise_request, release_embedders
from tinystudio.services import repositories as repo


def test_request_shapes_and_augment_without_repositories(db, project):
    assert normalise_request(None) == []
    assert normalise_request({"ids": ["a", "b"], "limit": 2, "similarity_threshold": 0.5}) == [{"name": "a", "limit": 2, "similarity_threshold": 0.5}, {"name": "b", "limit": 2, "similarity_threshold": 0.5}]
    assert normalise_request(["a", {"name": "b", "limit": 9}]) == [{"name": "a", "limit": 3, "similarity_threshold": 0.0}, {"name": "b", "limit": 9, "similarity_threshold": 0.0}]
    messages = [{"role": "user", "content": "hi"}]
    assert repo.augment_messages(db, project, messages, None) == (messages, [])
    with pytest.raises(ProjectError, match="does not exist"):
        repo.augment_messages(db, project, messages, ["nope"])


@needs_tokenizer
def test_create_add_query_remove(db, project, tmp_path: Path):
    model_dir = tiny_bert_dir(tmp_path)
    meta = repo.create_repository(db, project, "docs", "manuals", str(model_dir))
    assert meta["chunk_count"] == 0 and meta["embedding_model"] == str(model_dir) and meta["collection"].startswith("ts_")
    with pytest.raises(ProjectError, match="already exists"):
        repo.create_repository(db, project, "docs")
    with pytest.raises(ProjectError):
        repo.create_repository(db, project, "../x")
    db.commit()
    a = tmp_path / "thermostat.txt"
    a.write_text("The thermostat keeps the room at the set temperature.\n\n" + ("Heating schedules can be set per room. " * 40) + "\n\nCooling mode uses the fan.")
    b = tmp_path / "lights.md"
    b.write_text("# Lights\n\nDim the lights to a percentage. Lights can be grouped by room.")
    repository_id = repo.get_repository(db, project, "docs").id
    result = repo.add_documents(repository_id, [a, b], [], max_chars=300, overlap=40, progress=lambda *_: None)
    assert [d["name"] for d in result["documents"]] == ["thermostat.txt", "lights.md"] and result["chunk_count"] >= 3 and result["dimension"] == 32
    db.expire_all()
    assert vectors.count(result["collection"]) == result["chunk_count"]
    hits = repo.query(db, project, "docs", "set the room temperature", limit=3)
    assert 1 <= len(hits) <= 3 and all(-1 <= h["similarity_score"] <= 1 for h in hits) and hits == sorted(hits, key=lambda h: -h["similarity_score"])
    assert repo.query(db, project, "docs", "anything", limit=3, similarity_threshold=1.01) == []
    again = repo.add_documents(repository_id, [a], [], progress=lambda *_: None)
    assert again["chunk_count"] == result["chunk_count"] and again["added"] == []
    messages, chunks = repo.augment_messages(db, project, [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "how do I dim the lights?"}], {"ids": ["docs"], "limit": 2})
    assert len(chunks) == 2 and messages[0]["content"].startswith("Be brief.") and "[1]" in messages[0]["content"] and messages[1]["content"] == "how do I dim the lights?"
    page = repo.chunks(db, project, "docs", document="lights.md", limit=10)
    assert len(page["chunks"]) == 1 and page["chunks"][0]["document"] == "lights.md"
    after = repo.remove_document(db, project, "docs", "lights.md")
    assert [d["name"] for d in after["documents"]] == ["thermostat.txt"] and vectors.count(after["collection"]) == after["chunk_count"]
    with pytest.raises(ProjectError, match="not in repository"):
        repo.remove_document(db, project, "docs", "lights.md")
    repo.delete_repository(db, project, "docs")
    assert repo.list_repositories(db, project) == [] and vectors.count(after["collection"]) == 0
    release_embedders()
