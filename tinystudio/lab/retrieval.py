"""Immutable local evidence indexes and replayable lexical/structured/episode queries."""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Callable

from tinystudio.lab.learning import artifact, resolved_inputs
from tinystudio.lab.compatibility import directory_manifest, resolve_role
from tinystudio.lab.partitioning import source_protection


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _terms(text: str) -> list[str]:
    return re.findall(r"[\w]+", text.casefold())


class SemanticEncoder:
    """An existing repository Embedder bound to one verified immutable local model."""
    def __init__(self, binding: dict):
        if directory_manifest(binding["path"])["sha256"] != binding["directory_sha256"]:
            raise ValueError("embedding model checksum mismatch")
        from tinystudio.rag.repositories import Embedder
        self.binding = binding
        self.model = Embedder(binding["path"], "cpu")
        capacity = getattr(self.model.model.config, "max_position_embeddings", None) or getattr(self.model.model.config, "n_positions", None)
        if isinstance(capacity, int) and capacity > 0:
            self.model.max_length = min(self.model.max_length, capacity)
        if directory_manifest(binding["path"])["sha256"] != binding["directory_sha256"]:
            raise ValueError("embedding model changed while loading")

    def encode(self, texts: list[str]) -> list[list[float]]:
        import numpy as np
        vectors = self.model.encode(texts)
        if vectors.ndim != 2 or not np.isfinite(vectors).all():
            raise ValueError("embedding model returned non-finite or malformed vectors")
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        if (norms <= 0).any():
            raise ValueError("embedding model returned zero vectors")
        return (vectors / norms).tolist()

    def identity(self):
        return {"artifact_id": self.binding["artifact_id"], "sha256": self.binding["directory_sha256"], "artifact_sha256": self.binding["sha256"],
                "dimension": self.model.dimension, "pooling": "repository Embedder masked mean, L2 normalized"}


def _document_text(document):
    content = document["content"]
    return content["text"] if isinstance(content.get("text"), str) else json.dumps(content, ensure_ascii=False)


def _evidence_protection(metadata: dict) -> str:
    """Inspect declared evidence envelopes, never keywords in scientific prose.

    Arbitrary nested domain objects are content. These named envelopes carry
    evidence metadata and cannot weaken restrictions on their containing row.
    """
    protections = [source_protection(metadata)]
    if any(key in metadata for key in ("ground_truth", "true_value", "injected_fault")):
        protections.append("truth")
    for key in ("metadata", "provenance", "payload", "record", "raw_row", "binding", "resolved_source"):
        nested = metadata.get(key)
        if isinstance(nested, dict):
            protections.append(_evidence_protection(nested))
        elif isinstance(nested, list):
            protections.extend(_evidence_protection(row) for row in nested if isinstance(row, dict))
    return "truth" if "truth" in protections else "evaluation" if "evaluation" in protections else "model"


def build_index(paths: list[str | Path | dict], output_dir: Path, mode: str = "lexical",
                records: list[dict] | None = None, encoder: SemanticEncoder | None = None, research_override: dict | None = None) -> dict:
    if mode not in {"lexical", "structured", "episode", "semantic", "hybrid"}:
        raise ValueError(f"unsupported local retrieval mode {mode!r}")
    documents, sources = [], []
    for raw in paths:
        path = Path(raw["path"] if isinstance(raw, dict) else raw)
        content = path.read_bytes()
        source_hash = hashlib.sha256(content).hexdigest()
        if isinstance(raw, dict) and raw.get("sha256") and source_hash != raw["sha256"]:
            raise ValueError(f"input checksum mismatch for {path.name}")
        binding = dict(raw) if isinstance(raw, dict) else {"path": str(path)}
        binding["path"] = str(path)
        # Validate binding labels even when an empty source has no row to inspect.
        _evidence_protection(binding)
        source_metadata = {}
        source = {"path": str(path), "sha256": source_hash, "bytes": len(content), "binding": binding}
        sources.append(source)
        if content.startswith(b"PAR1"):
            import pyarrow.parquet as pq
            import pyarrow as pa
            rows = pq.read_table(pa.BufferReader(content)).to_pylist()
        elif path.suffix in {".jsonl", ".ndjson"}:
            rows = [json.loads(line) for line in content.decode().splitlines() if line.strip()]
        elif path.suffix == ".json" or content.lstrip().startswith((b"{", b"[")):
            try:
                rows = json.loads(content)
            except json.JSONDecodeError:
                rows = [json.loads(line) for line in content.decode().splitlines() if line.strip()]
            if isinstance(rows, dict):
                if "records" in rows or "rows" in rows:
                    source_metadata = {key: value for key, value in rows.items() if key not in {"records", "rows"}}
                    rows = rows.get("records", rows.get("rows"))
                else:
                    rows = [rows]
        else:
            rows = [{"text": line} for line in content.decode().splitlines() if line.strip()]
        _evidence_protection(source_metadata)
        source["metadata"] = source_metadata
        for number, row in enumerate(rows):
            documents.append({"source": str(path), "source_sha256": source_hash, "row": number,
                              "provenance": {"binding": binding, "metadata": source_metadata},
                              "content": row if isinstance(row, dict) else {"text": str(row)}})
    for number, row in enumerate(records or []):
        documents.append({"source": "inline", "source_sha256": hashlib.sha256(_canonical(records)).hexdigest(),
                          "row": number, "content": row if isinstance(row, dict) else {"text": str(row)}})
    if not documents:
        raise ValueError("retrieval requires at least one evidence record")
    restricted = []
    for document in documents:
        protection = _evidence_protection({"record": document["content"],
                                           "provenance": document.get("provenance", {})})
        document.setdefault("provenance", {})["protection"] = protection
        if protection != "model":
            restricted.append({"source": document["source"], "row": document["row"],
                               "reasons": [f"restricted {protection} evidence"],
                               "provenance": document["provenance"]})
        document["id"] = hashlib.sha256(_canonical(document)).hexdigest()
    if restricted:
        if not (isinstance(research_override, dict)
                and research_override.get("kind") == "retrieval.include_restricted_evidence"
                and isinstance(research_override.get("reason"), str) and research_override["reason"].strip()):
            raise ValueError("retrieval corpus contains final-test/truth/evaluation evidence; an explicit recorded research override is required")
    payload = {"format": "tinystudio.retrieval.v1", "mode": mode, "sources": sources, "documents": documents,
               "restricted_evidence": restricted, "research_override": research_override if restricted else None}
    if mode in {"semantic", "hybrid"}:
        if encoder is None:
            raise ValueError("semantic/hybrid retrieval requires an immutable local embedding_model artifact")
        vectors = encoder.encode([_document_text(document) for document in documents])
        for document, vector in zip(documents, vectors):
            document["embedding"] = vector
        payload["embedding_model"] = encoder.identity()
    digest = hashlib.sha256(_canonical(payload)).hexdigest()
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"index-{digest}.json"
    serialized = _canonical(payload)
    if target.exists():
        if target.read_bytes() != serialized:
            raise ValueError("immutable index collision or altered content")
    else:
        with target.open("xb") as handle:
            handle.write(serialized)
    return {"path": str(target), "corpus_sha256": digest, "documents": len(documents), "mode": mode, "research_only": bool(restricted)}


def query_index(path: str | Path, query: str, top_k: int = 5, filters: dict | None = None,
                encoder: SemanticEncoder | None = None) -> dict:
    path = Path(path)
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if path.stem != f"index-{digest}":
        raise ValueError("retrieval index digest does not match immutable filename")
    payload = json.loads(data)
    if payload.get("format") != "tinystudio.retrieval.v1":
        raise ValueError("unsupported retrieval index format")
    if not 1 <= top_k <= 100:
        raise ValueError("top_k must be between 1 and 100")
    filters = filters or {}
    documents = payload["documents"]
    term_counts = [Counter(_terms(json.dumps(document["content"], ensure_ascii=False))) for document in documents]
    query_terms = sorted(set(_terms(query)))
    frequencies = {term: sum(term in counts for counts in term_counts) for term in query_terms}
    lengths = [sum(counts.values()) for counts in term_counts]
    average = sum(lengths) / len(lengths) or 1
    semantic = payload["mode"] in {"semantic", "hybrid"}
    query_vector = None
    if semantic:
        if encoder is None or encoder.identity() != payload["embedding_model"]:
            raise ValueError("query embedding model must match the immutable index")
        query_vector = encoder.encode([query])[0]
    ranked = []
    for document, counts, length in zip(documents, term_counts, lengths):
        if any(document["content"].get(key) != value for key, value in filters.items()):
            continue
        score = 0.0
        for term in query_terms:
            count = counts[term]
            idf = math.log(1 + (len(documents) - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
            score += idf * count * 2.2 / (count + 1.2 * (0.25 + 0.75 * length / average))
        semantic_score = sum(left * right for left, right in zip(query_vector, document["embedding"])) if semantic else None
        if semantic or score > 0 or (not query_terms and filters):
            ranked.append({**{key: value for key, value in document.items() if key != "embedding"},
                "score": semantic_score if payload["mode"] == "semantic" else score,
                "lexical_score": score, "semantic_score": semantic_score})
    if payload["mode"] == "hybrid":
        dense = sorted(ranked, key=lambda row: (-row["semantic_score"], row["id"]))
        sparse = sorted([row for row in ranked if row["lexical_score"] > 0], key=lambda row: (-row["lexical_score"], row["id"]))
        dense_ranks = {row["id"]: rank for rank, row in enumerate(dense, 1)}
        sparse_ranks = {row["id"]: rank for rank, row in enumerate(sparse, 1)}
        for row in ranked:
            row["score"] = 1 / (60 + dense_ranks[row["id"]]) + (1 / (60 + sparse_ranks[row["id"]]) if row["id"] in sparse_ranks else 0)
    ranked.sort(key=lambda row: (-row["score"], row["id"]))
    hits = ranked[:top_k]
    return {"query": query, "filters": filters, "top_k": top_k, "mode": payload["mode"],
            "scoring": "reciprocal-rank-fusion-k=60" if payload["mode"] == "hybrid" else "cosine" if semantic else "bm25-k1=1.2-b=0.75",
            "corpus_sha256": digest, "hits": hits,
            "trace": {"candidate_documents": len(documents), "matching_documents": len(ranked),
                      "query_terms": query_terms, "selected_ids": [hit["id"] for hit in hits],
                      "embedding_model": payload.get("embedding_model"), "query_embedding": query_vector,
                      "research_only": bool(payload.get("restricted_evidence")), "research_override": payload.get("research_override")}}


def execute_retrieval(spec: dict, output_dir: Path, emit: Callable[[dict], None],
                      cancelled: Callable[[], bool]) -> dict:
    definition = spec["definition"]
    params = definition.get("parameters", {})
    mode = params.get("mode", (definition.get("retrieval") or {}).get("mode", "lexical"))
    if cancelled():
        return {"records": [], "artifacts": [], "metrics": {}, "qualification": {"status": "cancelled"}}
    if any(key in params for key in {"embedding_model", "embedding_model_path"}):
        raise ValueError("embedding model sources must use immutable input artifact roles")
    binding = resolve_role(spec, "embedding_model", directory=True, extract_dir=output_dir / "embedding-input") if mode in {"semantic", "hybrid"} else None
    encoder = SemanticEncoder(binding) if binding else None
    declared = definition.get("inputs", [])
    # Retrieval must inspect every frozen corpus binding, including restricted
    # ones. Filtering model-only inputs first both hid restrictions and broke
    # positional correspondence with the declared roles.
    all_paths = resolved_inputs(spec, visibilities=None)
    if declared:
        paths = []
        supplied = spec.get("input_paths", {})
        for number, (item, path) in enumerate(zip(declared, all_paths)):
            if item.get("role") == "embedding_model":
                continue
            raw = supplied.get(item["artifact_id"]) if isinstance(supplied, dict) else supplied[number]
            paths.append({**item, **path, "resolved_source": raw if isinstance(raw, dict) else {}})
    else:
        paths = [path for path in all_paths if path.get("role") != "embedding_model"]
    override = next((item for item in definition.get("research_overrides", [])
                     if item.get("kind") == "retrieval.include_restricted_evidence"), None)
    index = build_index(paths, output_dir, mode, params.get("records"), encoder=encoder, research_override=override)
    queries = params.get("queries", [params.get("query", "")])
    traces = []
    for query in queries:
        if cancelled():
            break
        row = query_index(index["path"], query, int(params.get("top_k", 5)), params.get("filters"), encoder=encoder)
        traces.append(row)
        emit({"type": "retrieval", "query": query, "hits": len(row["hits"])})
    trace_path = output_dir / "queries.json"
    trace_path.write_text(json.dumps(traces, indent=2, ensure_ascii=False))
    return {"records": traces, "artifacts": [artifact(Path(index["path"]), "retrieval_index"),
            artifact(trace_path, "retrieval_trace")],
            "metrics": {"indexed_documents": index["documents"], "queries": len(traces),
                        "corpus_sha256": index["corpus_sha256"]},
            "qualification": {"status": "completed" if len(traces) == len(queries) else "cancelled",
                "immutable_corpus": True, "mode": mode, "generation": False,
                "scope": "local existing Embedder with retained dense vectors and lexical RRF" if encoder else "local lexical scoring with exact structured filters and preserved episode records",
                "embedding_model_sha256": binding["directory_sha256"] if binding else None,
                "research_only": index["research_only"],
                "qualified": not index["research_only"]}}
