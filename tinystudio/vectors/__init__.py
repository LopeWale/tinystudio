"""Vector store: Qdrant holds repository chunks (text + embedding + document payload), one collection per repository.

The operational database keeps repository and document metadata; nothing about vectors is stored there.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from typing import Any

from tinystudio.config import config

_lock = threading.Lock()
_client: Any = None


@dataclass(frozen=True)
class Hit:
    id: str
    score: float
    text: str
    document_id: str
    document: str
    kind: str
    index: int


def client() -> Any:
    """One Qdrant client per process. ``TINYSTUDIO_QDRANT_URL=:memory:`` runs the client's local mode (tests)."""
    global _client
    with _lock:
        if _client is None:
            from qdrant_client import QdrantClient

            url = config().qdrant_url
            if url == ":memory:":
                _client = QdrantClient(":memory:")
            elif url.startswith("path:"):
                _client = QdrantClient(path=url[5:])
            else:
                _client = QdrantClient(url=url, api_key=config().qdrant_api_key, timeout=60)
        return _client


def reset() -> None:
    global _client
    with _lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:  # noqa: BLE001
                pass
        _client = None


def ping() -> bool:
    try:
        client().get_collections()
        return True
    except Exception:  # noqa: BLE001
        return False


def point_id(collection: str, document_id: str, index: int) -> str:
    """Stable UUID per chunk so re-indexing a document overwrites instead of duplicating."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"tinystudio/{collection}/{document_id}/{index}"))


def ensure_collection(name: str, dimension: int) -> None:
    """Create the collection (cosine) and its payload index if missing; dimension mismatches are an error."""
    from qdrant_client import models

    qc = client()
    if qc.collection_exists(name):
        info = qc.get_collection(name)
        params = info.config.params.vectors
        size = params.size if hasattr(params, "size") else None
        if size is not None and size != dimension:
            raise ValueError(f"collection {name} stores {size}-dimensional vectors; the embedding model produces {dimension}")
        return
    qc.create_collection(name, vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE))
    qc.create_payload_index(name, field_name="document_id", field_schema=models.PayloadSchemaType.KEYWORD)


def drop_collection(name: str) -> None:
    qc = client()
    if qc.collection_exists(name):
        qc.delete_collection(name)


def upsert(name: str, points: list[dict[str, Any]], batch: int = 256) -> int:
    """points: {id, vector, payload}. Returns the count written. The last batch waits so the data is searchable on return."""
    from qdrant_client import models

    qc = client()
    total = 0
    for start in range(0, len(points), batch):
        chunk = points[start : start + batch]
        qc.upsert(name, points=[models.PointStruct(id=p["id"], vector=p["vector"], payload=p["payload"]) for p in chunk], wait=start + batch >= len(points))
        total += len(chunk)
    return total


def delete_document(name: str, document_id: str) -> None:
    from qdrant_client import models

    client().delete(name, points_selector=models.FilterSelector(filter=models.Filter(must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))])), wait=True)


def search(name: str, vector: list[float], limit: int = 5, score_threshold: float | None = None, document_ids: list[str] | None = None) -> list[Hit]:
    from qdrant_client import models

    query_filter = None
    if document_ids:
        query_filter = models.Filter(must=[models.FieldCondition(key="document_id", match=models.MatchAny(any=document_ids))])
    result = client().query_points(name, query=vector, limit=limit, score_threshold=score_threshold if score_threshold is not None and score_threshold > -1 else None, query_filter=query_filter, with_payload=True)
    return [_hit(p) for p in result.points]


def scroll(name: str, document_id: str | None = None, limit: int = 100, offset: Any = None) -> tuple[list[Hit], Any]:
    from qdrant_client import models

    scroll_filter = models.Filter(must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))]) if document_id else None
    points, next_offset = client().scroll(name, scroll_filter=scroll_filter, limit=limit, offset=offset, with_payload=True, with_vectors=False)
    return [_hit(p) for p in points], next_offset


def count(name: str) -> int:
    qc = client()
    if not qc.collection_exists(name):
        return 0
    return int(qc.count(name, exact=True).count)


def _hit(point: Any) -> Hit:
    payload = point.payload or {}
    return Hit(id=str(point.id), score=float(getattr(point, "score", 0.0) or 0.0), text=str(payload.get("text", "")), document_id=str(payload.get("document_id", "")), document=str(payload.get("document", "")), kind=str(payload.get("kind", "")), index=int(payload.get("index", 0)))
