"""Offline tests for repositories (RAG) on a tiny random BERT embedder; no downloads."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tinystudio.core.project import Project, ProjectError
from tinystudio.rag import repositories as repo

os.environ.setdefault("HF_HUB_OFFLINE", "1")
TOKENIZER = "HuggingFaceTB/SmolLM2-135M-Instruct"


def _tokenizer_cached() -> bool:
    try:
        from transformers import AutoTokenizer

        AutoTokenizer.from_pretrained(TOKENIZER)
        return True
    except Exception:  # noqa: BLE001
        return False


needs_tokenizer = pytest.mark.skipif(not _tokenizer_cached(), reason="SmolLM2 tokenizer not in the local Hub cache")


def tiny_embedder_dir(tmp_path: Path) -> Path:
    import torch
    from transformers import AutoTokenizer, BertConfig, BertModel

    tok = AutoTokenizer.from_pretrained(TOKENIZER)
    cfg = BertConfig(vocab_size=len(tok), hidden_size=32, intermediate_size=64, num_hidden_layers=2, num_attention_heads=4, max_position_embeddings=512)
    torch.manual_seed(1)
    out = tmp_path / "tiny-embedder"
    BertModel(cfg).save_pretrained(out)
    tok.save_pretrained(out)
    return out


def test_request_shapes_and_augment_without_repositories(tmp_path: Path):
    assert repo.normalise_request(None) == []
    assert repo.normalise_request({"ids": ["a", "b"], "limit": 2, "similarity_threshold": 0.5}) == [{"name": "a", "limit": 2, "similarity_threshold": 0.5}, {"name": "b", "limit": 2, "similarity_threshold": 0.5}]
    assert repo.normalise_request(["a", {"name": "b", "limit": 9}]) == [{"name": "a", "limit": 3, "similarity_threshold": 0.0}, {"name": "b", "limit": 9, "similarity_threshold": 0.0}]
    project = Project.create(tmp_path / "p", "p")
    messages = [{"role": "user", "content": "hi"}]
    assert repo.augment_messages(project, messages, None) == (messages, [])
    with pytest.raises(ProjectError, match="does not exist"):
        repo.augment_messages(project, messages, ["nope"])


@needs_tokenizer
def test_create_add_query_remove(tmp_path: Path):
    project = Project.create(tmp_path / "p", "p")
    model_dir = tiny_embedder_dir(tmp_path)
    meta = repo.create_repository(project, "docs", "manuals", str(model_dir))
    assert meta["chunk_count"] == 0 and meta["embedding_model"] == str(model_dir)
    with pytest.raises(ProjectError, match="already exists"):
        repo.create_repository(project, "docs")
    with pytest.raises(ProjectError, match="names may contain"):
        repo.create_repository(project, "../x")
    a = tmp_path / "thermostat.txt"
    a.write_text("The thermostat keeps the room at the set temperature.\n\n" + ("Heating schedules can be set per room. " * 40) + "\n\nCooling mode uses the fan.")
    b = tmp_path / "lights.md"
    b.write_text("# Lights\n\nDim the lights to a percentage. Lights can be grouped by room.")
    meta = repo.add_documents(project, "docs", [a, b], [], max_chars=300, overlap=40, progress=lambda *_: None)
    assert [d["name"] for d in meta["documents"]] == ["thermostat.txt", "lights.md"] and meta["chunk_count"] >= 3 and meta["dimension"] == 32
    assert (repo.repository_dir(project, "docs") / "embeddings.npy").exists()
    hits = repo.query(project, "docs", "set the room temperature", limit=3)
    assert 1 <= len(hits) <= 3 and all(-1 <= h["similarity_score"] <= 1 for h in hits) and hits == sorted(hits, key=lambda h: -h["similarity_score"])
    assert repo.query(project, "docs", "anything", limit=3, similarity_threshold=1.01) == []
    # the same file is not indexed twice
    meta2 = repo.add_documents(project, "docs", [a], [], progress=lambda *_: None)
    assert meta2["chunk_count"] == meta["chunk_count"]
    # retrieval augments the system prompt and reports the chunks
    messages, chunks = repo.augment_messages(project, [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "how do I dim the lights?"}], {"ids": ["docs"], "limit": 2})
    assert len(chunks) == 2 and messages[0]["role"] == "system" and messages[0]["content"].startswith("Be brief.") and "[1]" in messages[0]["content"] and messages[1]["content"] == "how do I dim the lights?"
    messages, chunks = repo.augment_messages(project, [{"role": "user", "content": "lights?"}], ["docs"])
    assert messages[0]["role"] == "system" and len(messages) == 2
    meta = repo.remove_document(project, "docs", "lights.md")
    assert [d["name"] for d in meta["documents"]] == ["thermostat.txt"] and meta["chunk_count"] == len(repo._read_chunks(project, "docs"))
    with pytest.raises(ProjectError, match="not in repository"):
        repo.remove_document(project, "docs", "lights.md")
    assert repo.list_repositories(project)[0]["name"] == "docs"
    repo.delete_repository(project, "docs")
    assert repo.list_repositories(project) == []
    repo.release_embedders()
