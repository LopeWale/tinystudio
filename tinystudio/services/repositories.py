"""Repositories: document collections for retrieval-augmented chat. Metadata in PostgreSQL, chunks and vectors in Qdrant."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tinystudio import vectors
from tinystudio.core.documents import chunk_text
from tinystudio.core.project import check_name, iso
from tinystudio.db import session
from tinystudio.db.models import Document, Project, Repository
from tinystudio.rag.repositories import MAX_DOCUMENT_BYTES, RepositoryError, build_context, default_embedding_model, embedder, normalise_request


def document_dict(doc: Document) -> dict[str, Any]:
    return {"id": doc.id, "name": doc.name, "kind": doc.kind, "chars": doc.chars, "chunks": doc.chunks, "added": iso(doc.added)}


def repository_dict(repo: Repository) -> dict[str, Any]:
    docs = [document_dict(d) for d in repo.documents]
    return {"id": repo.id, "name": repo.name, "description": repo.description, "embedding_model": repo.embedding_model, "dimension": repo.dimension, "collection": repo.collection, "documents": docs, "chunk_count": sum(d["chunks"] for d in docs), "created": iso(repo.created), "updated": iso(repo.updated)}


def get_repository(db: Session, project: Project, name: str) -> Repository:
    repo = db.scalar(select(Repository).where(Repository.project_id == project.id, Repository.name == name))
    if repo is None:
        raise RepositoryError(f"repository {name!r} does not exist; create it first")
    return repo


def list_repositories(db: Session, project: Project) -> list[dict[str, Any]]:
    return [repository_dict(r) for r in db.scalars(select(Repository).where(Repository.project_id == project.id).order_by(Repository.created))]


def create_repository(db: Session, project: Project, name: str, description: str = "", embedding_model: str | None = None) -> dict[str, Any]:
    check_name(name, "repository")
    if db.scalar(select(Repository.id).where(Repository.project_id == project.id, Repository.name == name)) is not None:
        raise RepositoryError(f"repository {name!r} already exists")
    repo = Repository(project_id=project.id, name=name, description=description, embedding_model=embedding_model or default_embedding_model(), collection="")
    db.add(repo)
    db.flush()
    repo.collection = f"ts_{repo.id}"
    db.flush()
    return repository_dict(repo)


def delete_repository(db: Session, project: Project, name: str) -> None:
    repo = get_repository(db, project, name)
    vectors.drop_collection(repo.collection)
    db.delete(repo)
    db.flush()


def add_documents(repository_id: str, paths: list[Path] | None = None, urls: list[str] | None = None, max_chars: int = 1200, overlap: int = 150, progress: Any = print) -> dict[str, Any]:
    """Extract text, chunk, embed, and upsert into the repository's collection (worker job; opens its own sessions)."""
    from tinystudio.core.synth import load_sources

    with session() as db:
        repo = db.get(Repository, repository_id)
        if repo is None:
            raise RepositoryError("repository does not exist")
        existing = {d.name: d for d in repo.documents}
        model_id, collection, name = repo.embedding_model, repo.collection, repo.name
        existing_names = set(existing)
    sources = load_sources(list(paths or []), list(urls or []))
    if not sources:
        raise RepositoryError("pass at least one file or URL")
    for source in sources:
        if len(source.text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise RepositoryError(f"{source.name} is larger than {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB of text")
    model = embedder(model_id)
    vectors.ensure_collection(collection, model.dimension)
    added = []
    for source in sources:
        if source.name in existing_names:
            progress(f"{source.name}: already in the repository; skipping (delete it first to re-index)")
            continue
        pieces = chunk_text(source.text, max_chars=max_chars, overlap=overlap)
        if not pieces:
            progress(f"{source.name}: no text extracted; skipping")
            continue
        with session() as db:
            doc = Document(repository_id=repository_id, name=source.name, kind=source.kind, chars=len(source.text), chunks=0)
            db.add(doc)
            db.flush()
            doc_id = doc.id
        started = time.perf_counter()
        matrix = model.encode(pieces)
        points = [{"id": vectors.point_id(collection, doc_id, j), "vector": matrix[j].tolist(), "payload": {"text": piece, "document_id": doc_id, "document": source.name, "kind": source.kind, "index": j}} for j, piece in enumerate(pieces)]
        vectors.upsert(collection, points)
        with session() as db:
            doc = db.get(Document, doc_id)
            assert doc is not None
            doc.chunks = len(pieces)
            repo = db.get(Repository, repository_id)
            assert repo is not None
            repo.dimension = model.dimension
        added.append(source.name)
        progress(f"{source.name}: {len(pieces)} chunks embedded with {model_id} in {time.perf_counter() - started:.1f}s")
    with session() as db:
        repo = db.get(Repository, repository_id)
        assert repo is not None
        return {**repository_dict(repo), "added": added}


def remove_document(db: Session, project: Project, name: str, document: str) -> dict[str, Any]:
    repo = get_repository(db, project, name)
    doc = next((d for d in repo.documents if d.name == document), None)
    if doc is None:
        raise RepositoryError(f"document {document!r} is not in repository {name!r}")
    vectors.delete_document(repo.collection, doc.id)
    db.delete(doc)
    db.flush()
    db.refresh(repo)
    return repository_dict(repo)


def chunks(db: Session, project: Project, name: str, document: str | None = None, limit: int = 50, offset: Any = None) -> dict[str, Any]:
    """A page of stored chunks (for inspection)."""
    repo = get_repository(db, project, name)
    doc_id = None
    if document:
        doc = next((d for d in repo.documents if d.name == document), None)
        if doc is None:
            raise RepositoryError(f"document {document!r} is not in repository {name!r}")
        doc_id = doc.id
    if vectors.count(repo.collection) == 0:
        return {"chunks": [], "next": None}
    hits, next_offset = vectors.scroll(repo.collection, doc_id, limit, offset)
    return {"chunks": [{"id": h.id, "document": h.document, "index": h.index, "text": h.text} for h in hits], "next": str(next_offset) if next_offset is not None else None}


def query(db: Session, project: Project, name: str, text: str, limit: int = 5, similarity_threshold: float = 0.0) -> list[dict[str, Any]]:
    """The most similar chunks to ``text`` (cosine), at or above the threshold."""
    repo = get_repository(db, project, name)
    if repo.dimension is None or vectors.count(repo.collection) == 0:
        return []
    vector = embedder(repo.embedding_model).encode([text])[0].tolist()
    hits = vectors.search(repo.collection, vector, limit=max(1, limit), score_threshold=similarity_threshold if similarity_threshold > 0 else None)
    return [{"repository": name, "chunk_id": h.id, "document": h.document, "text": h.text, "similarity_score": round(h.score, 4)} for h in hits if h.score >= similarity_threshold]


def augment_messages(db: Session, project: Project, messages: list[dict[str, Any]], repositories: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Retrieve chunks for the last user message and prepend them as context; returns (messages, chunks)."""
    requests = normalise_request(repositories)
    if not requests:
        return messages, []
    question = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    found: list[dict[str, Any]] = []
    for req in requests:
        found.extend(query(db, project, req["name"], question, req["limit"], req["similarity_threshold"]))
    if not found:
        return messages, []
    found.sort(key=lambda c: -c["similarity_score"])
    return build_context(messages, found), found
