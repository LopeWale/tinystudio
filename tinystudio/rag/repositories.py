"""Embeddings and retrieval helpers for repositories. Metadata lives in PostgreSQL, vectors in Qdrant
(see :mod:`tinystudio.services.repositories`); this module holds the model-side pieces.

Any Hugging Face feature-extraction model works as the embedder (search the Hub with task "embeddings");
vectors are mean-pooled over the attention mask and normalised, which is what sentence-transformers models expect.
"""

from __future__ import annotations

import os
from typing import Any

from tinystudio.core.project import ProjectError

DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
_EMBEDDERS: dict[tuple[str, str], "Embedder"] = {}


class RepositoryError(ProjectError):
    """A repository operation failed; the message names the repository or document."""


def default_embedding_model() -> str:
    return os.environ.get("TINYSTUDIO_EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL


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
        self.dimension = int(self.model.config.hidden_size)

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
        return np.concatenate(out, axis=0) if out else np.zeros((0, self.dimension), dtype=np.float32)


def embedder(model_id: str, device: str | None = None) -> Embedder:
    from tinystudio.serving.runner import pick_device

    key = (model_id, device or pick_device())
    if key not in _EMBEDDERS:
        try:
            _EMBEDDERS[key] = Embedder(model_id, key[1])
        except OSError as cause:
            raise RepositoryError(f"embedding model {model_id!r} is not available: {cause}. Search the Hub with task embeddings and download one") from cause
    return _EMBEDDERS[key]


def release_embedders() -> None:
    _EMBEDDERS.clear()


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


def build_context(messages: list[dict[str, Any]], chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prepend retrieved passages to the system prompt (creating one when missing)."""
    if not chunks:
        return messages
    context = "\n\n".join(f"[{i + 1}] ({c['document']}) {c['text']}" for i, c in enumerate(chunks))
    instruction = "Use the following retrieved passages when they are relevant; cite them by number. If they do not contain the answer, say so.\n\n" + context
    out = [dict(m) for m in messages]
    if out and out[0]["role"] == "system":
        out[0] = {"role": "system", "content": out[0]["content"] + "\n\n" + instruction}
    else:
        out.insert(0, {"role": "system", "content": instruction})
    return out
