"""Repositories: document collections with embeddings for retrieval-augmented chat, kept entirely on disk.

This is the local equivalent of the Repositories feature in Prem's 2024 platform (upload PDF/TXT/DOCX, index,
then chat with ``limit`` and ``similarity_threshold``). Layout under ``repositories/<name>/``:

- ``meta.json``: embedding model, dimension, the documents added and their chunk counts;
- ``chunks.jsonl``: one chunk per line (``id``, ``document``, ``text``);
- ``embeddings.npy``: unit-normalised float32 vectors, one row per chunk, in the same order.

Any Hugging Face feature-extraction model works as the embedder (search the Hub with task "embeddings"); vectors
are mean-pooled over the attention mask and normalised, which is what sentence-transformers models expect.
Retrieval is a cosine similarity over the whole matrix: fine for the tens of thousands of chunks a personal
corpus has, with no external vector database to run.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from tinystudio.core.documents import chunk_text
from tinystudio.core.project import SAFE_NAME, Project, ProjectError, now, read_json, write_json

DEFAULT_EMBEDDING_MODEL = os.environ.get("TINYSTUDIO_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
_EMBEDDERS: dict[tuple[str, str], "Embedder"] = {}


class RepositoryError(ProjectError):
    """A repository operation failed; the message names the repository or document."""


def repositories_dir(project: Project) -> Path:
    return project.root / "repositories"


def repository_dir(project: Project, name: str) -> Path:
    return repositories_dir(project) / name


def repository_meta(project: Project, name: str) -> dict[str, Any]:
    path = repository_dir(project, name) / "meta.json"
    if not path.exists():
        raise RepositoryError(f"repository {name!r} does not exist; create it with `tinystudio repo create {name}`")
    return read_json(path)


def list_repositories(project: Project) -> list[dict[str, Any]]:
    base = repositories_dir(project)
    if not base.exists():
        return []
    return [read_json(child / "meta.json") for child in sorted(base.iterdir()) if child.is_dir() and (child / "meta.json").exists()]


def create_repository(project: Project, name: str, description: str = "", embedding_model: str | None = None) -> dict[str, Any]:
    if not SAFE_NAME.match(name):
        raise RepositoryError("repository names may contain letters, digits, spaces, dots, dashes, and underscores")
    directory = repository_dir(project, name)
    if (directory / "meta.json").exists():
        raise RepositoryError(f"repository {name!r} already exists")
    directory.mkdir(parents=True, exist_ok=True)
    meta = {"name": name, "description": description, "embedding_model": embedding_model or os.environ.get("TINYSTUDIO_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL), "dimension": None, "documents": [], "chunk_count": 0, "created": now(), "updated": now()}
    write_json(directory / "meta.json", meta)
    (directory / "chunks.jsonl").touch()
    return meta


def delete_repository(project: Project, name: str) -> None:
    repository_meta(project, name)
    shutil.rmtree(repository_dir(project, name))


# ------------------------------------------------------------------ embeddings
class Embedder:
    """A feature-extraction model with mean pooling; cached per (model, device) inside one process."""

    def __init__(self, model_id: str, device: str | None = None) -> None:
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        import torch
        from transformers import AutoModel, AutoTokenizer

        from tinystudio.serving.runner import pick_device

        self.model_id = model_id
        self.device = device or pick_device()
        self.tokenizer = AutoTokenizer.from_pretrained(model_id)
        self.model = AutoModel.from_pretrained(model_id, dtype=torch.float32).eval().to(self.device)
        self.max_length = min(int(getattr(self.tokenizer, "model_max_length", 512) or 512), 512)

    def encode(self, texts: list[str], batch_size: int = 32) -> Any:
        import numpy as np
        import torch

        out = []
        with torch.inference_mode():
            for start in range(0, len(texts), batch_size):
                enc = self.tokenizer(texts[start : start + batch_size], padding=True, truncation=True, max_length=self.max_length, return_tensors="pt").to(self.device)
                hidden = self.model(**enc).last_hidden_state
                mask = enc["attention_mask"].unsqueeze(-1).to(hidden.dtype)
                pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp_min(1e-6)
                pooled = torch.nn.functional.normalize(pooled, dim=-1)
                out.append(pooled.float().cpu().numpy())
        return np.concatenate(out, axis=0) if out else np.zeros((0, self.model.config.hidden_size), dtype=np.float32)


def embedder(model_id: str, device: str | None = None) -> Embedder:
    from tinystudio.serving.runner import pick_device

    key = (model_id, device or pick_device())
    if key not in _EMBEDDERS:
        try:
            _EMBEDDERS[key] = Embedder(model_id, key[1])
        except OSError as cause:
            raise RepositoryError(f"embedding model {model_id!r} is not available: {cause}. Download one with `tinystudio hub search --task embeddings` and `tinystudio hub download MODEL`") from cause
    return _EMBEDDERS[key]


def release_embedders() -> None:
    _EMBEDDERS.clear()


# ------------------------------------------------------------------ documents
def _read_chunks(project: Project, name: str) -> list[dict[str, Any]]:
    path = repository_dir(project, name) / "chunks.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def _load_matrix(project: Project, name: str) -> Any:
    import numpy as np

    path = repository_dir(project, name) / "embeddings.npy"
    return np.load(path) if path.exists() else None


def add_documents(project: Project, name: str, paths: list[Path] | None = None, urls: list[str] | None = None, max_chars: int = 1200, overlap: int = 150, progress: Any = print) -> dict[str, Any]:
    """Extract text from files (txt/md/html/pdf/docx/pptx) and web pages or YouTube URLs, chunk, embed, and append."""
    import numpy as np

    from tinystudio.core.synth import load_sources

    meta = repository_meta(project, name)
    sources = load_sources(list(paths or []), list(urls or []))
    if not sources:
        raise RepositoryError("pass at least one file or URL")
    for source in sources:
        if len(source.text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise RepositoryError(f"{source.name} is larger than {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB of text")
    existing = {d["name"] for d in meta["documents"]}
    chunks = _read_chunks(project, name)
    matrix = _load_matrix(project, name)
    model = embedder(meta["embedding_model"])
    new_rows: list[dict[str, Any]] = []
    added = []
    for source in sources:
        if source.name in existing:
            progress(f"{source.name}: already in the repository; skipping (delete it first to re-index)")
            continue
        pieces = chunk_text(source.text, max_chars=max_chars, overlap=overlap)
        if not pieces:
            progress(f"{source.name}: no text extracted; skipping")
            continue
        start_id = len(chunks) + len(new_rows)
        for j, piece in enumerate(pieces):
            new_rows.append({"id": start_id + j, "document": source.name, "kind": source.kind, "text": piece})
        added.append({"name": source.name, "kind": source.kind, "chars": len(source.text), "chunks": len(pieces), "added": now()})
        progress(f"{source.name}: {len(pieces)} chunks")
    if not new_rows:
        return meta
    started = time.perf_counter()
    vectors = model.encode([r["text"] for r in new_rows])
    progress(f"embedded {len(new_rows)} chunks with {meta['embedding_model']} in {time.perf_counter() - started:.1f}s")
    matrix = vectors if matrix is None else np.concatenate([matrix, vectors], axis=0)
    directory = repository_dir(project, name)
    with (directory / "chunks.jsonl").open("a", encoding="utf-8") as handle:
        for row in new_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    np.save(directory / "embeddings.npy", matrix.astype(np.float32))
    meta["documents"].extend(added)
    meta["chunk_count"] = int(matrix.shape[0])
    meta["dimension"] = int(matrix.shape[1])
    meta["updated"] = now()
    write_json(directory / "meta.json", meta)
    return meta


def remove_document(project: Project, name: str, document: str) -> dict[str, Any]:
    """Drop one document's chunks and vectors."""
    import numpy as np

    meta = repository_meta(project, name)
    if document not in {d["name"] for d in meta["documents"]}:
        raise RepositoryError(f"document {document!r} is not in repository {name!r}")
    chunks = _read_chunks(project, name)
    matrix = _load_matrix(project, name)
    keep = [i for i, c in enumerate(chunks) if c["document"] != document]
    kept = [{**chunks[i], "id": j} for j, i in enumerate(keep)]
    directory = repository_dir(project, name)
    with (directory / "chunks.jsonl").open("w", encoding="utf-8") as handle:
        for row in kept:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    if matrix is not None:
        np.save(directory / "embeddings.npy", matrix[keep] if keep else np.zeros((0, matrix.shape[1]), dtype=np.float32))
    meta["documents"] = [d for d in meta["documents"] if d["name"] != document]
    meta["chunk_count"] = len(kept)
    meta["updated"] = now()
    write_json(directory / "meta.json", meta)
    return meta


# ------------------------------------------------------------------ retrieval
def query(project: Project, name: str, text: str, limit: int = 5, similarity_threshold: float = 0.0) -> list[dict[str, Any]]:
    """The most similar chunks to ``text`` (cosine on unit vectors), at or above the threshold."""
    import numpy as np

    meta = repository_meta(project, name)
    matrix = _load_matrix(project, name)
    if matrix is None or matrix.shape[0] == 0:
        return []
    chunks = _read_chunks(project, name)
    vector = embedder(meta["embedding_model"]).encode([text])[0]
    scores = matrix @ vector
    order = np.argsort(-scores)[: max(1, limit)]
    return [{"repository": name, "chunk_id": int(i), "document": chunks[i]["document"], "text": chunks[i]["text"], "similarity_score": round(float(scores[i]), 4)} for i in order.tolist() if float(scores[i]) >= similarity_threshold]


def normalise_request(value: Any) -> list[dict[str, Any]]:
    """Accept Prem's shape ({"ids": [...], "limit", "similarity_threshold"}), a list of names, or a list of {name, limit, similarity_threshold}."""
    if not value:
        return []
    if isinstance(value, dict):
        names = value.get("ids") or value.get("names") or ([value["name"]] if value.get("name") else [])
        return [{"name": str(n), "limit": int(value.get("limit", 3)), "similarity_threshold": float(value.get("similarity_threshold", 0.0))} for n in names]
    out = []
    for item in value:
        if isinstance(item, str):
            out.append({"name": item, "limit": 3, "similarity_threshold": 0.0})
        elif isinstance(item, dict) and item.get("name"):
            out.append({"name": str(item["name"]), "limit": int(item.get("limit", 3)), "similarity_threshold": float(item.get("similarity_threshold", 0.0))})
    return out


def augment_messages(project: Project, messages: list[dict[str, Any]], repositories: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Retrieve chunks for the last user message and prepend them as context; returns (messages, chunks)."""
    requests = normalise_request(repositories)
    if not requests:
        return messages, []
    question = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    chunks: list[dict[str, Any]] = []
    for req in requests:
        chunks.extend(query(project, req["name"], question, req["limit"], req["similarity_threshold"]))
    if not chunks:
        return messages, []
    chunks.sort(key=lambda c: -c["similarity_score"])
    context = "\n\n".join(f"[{i + 1}] ({c['document']}) {c['text']}" for i, c in enumerate(chunks))
    instruction = "Use the following retrieved passages when they are relevant; cite them by number. If they do not contain the answer, say so.\n\n" + context
    out = [dict(m) for m in messages]
    if out and out[0]["role"] == "system":
        out[0] = {"role": "system", "content": out[0]["content"] + "\n\n" + instruction}
    else:
        out.insert(0, {"role": "system", "content": instruction})
    return out, chunks


def text_hash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()[:64]
