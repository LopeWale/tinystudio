"""Hugging Face Hub: search, download, cache inventory, token, and upload.

Thin wrappers over ``huggingface_hub`` (2.x) so the web interface, the CLI,
and the assistant tools share one behaviour. Downloads run as CLI tasks so
their progress shows in the task panel and survives page reloads.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from huggingface_hub import HfApi, get_token, login, scan_cache_dir, snapshot_download
from huggingface_hub.errors import GatedRepoError, HfHubHTTPError, RepositoryNotFoundError

from .project import ProjectError

EXPAND = ["safetensors", "gated", "likes", "downloads", "lastModified", "library_name", "tags", "pipeline_tag", "cardData"]
NON_TRANSFORMERS_TAGS = {"gguf", "mlx", "onnx", "tflite", "coreml"}


def _params(info: Any) -> int | None:
    safetensors = getattr(info, "safetensors", None)
    total = getattr(safetensors, "total", None) if safetensors is not None else None
    return int(total) if total else None


def _license(info: Any) -> str | None:
    card = getattr(info, "card_data", None)
    if card is not None:
        value = card.get("license") if hasattr(card, "get") else getattr(card, "license", None)
        if value:
            return str(value)
    for tag in getattr(info, "tags", None) or []:
        if tag.startswith("license:"):
            return tag.split(":", 1)[1]
    return None


def describe(info: Any) -> dict[str, Any]:
    tags = set(getattr(info, "tags", None) or [])
    params = _params(info)
    return {
        "id": info.id,
        "parameters": params,
        "params_b": round(params / 1e9, 3) if params else None,
        "license": _license(info),
        "gated": getattr(info, "gated", None) or False,
        "downloads": getattr(info, "downloads", None),
        "likes": getattr(info, "likes", None),
        "library": getattr(info, "library_name", None),
        "pipeline_tag": getattr(info, "pipeline_tag", None),
        "last_modified": str(getattr(info, "last_modified", "") or ""),
        "transformers_loadable": getattr(info, "library_name", None) == "transformers",  # extra formats (onnx, gguf) do not stop transformers loading
        "format_tags": sorted(tags & NON_TRANSFORMERS_TAGS),
        "mlx": "mlx" in tags or info.id.lower().startswith("mlx-community/"),
        "url": f"https://huggingface.co/{info.id}",
    }


def search(query: str, limit: int = 15, max_params_b: float | None = None, author: str | None = None) -> list[dict[str, Any]]:
    """Text-generation models sorted by downloads; the parameter filter is applied client-side from safetensors metadata."""
    api = HfApi()
    fetch = max(limit * 4, 40) if max_params_b else limit
    try:
        found = api.list_models(search=query or None, author=author, pipeline_tag="text-generation", sort="downloads", limit=fetch, expand=EXPAND)
        rows = [describe(info) for info in found]
    except HfHubHTTPError as cause:
        raise ProjectError(f"Hugging Face Hub search failed: {cause}") from cause
    if max_params_b:
        rows = [r for r in rows if r["params_b"] is None or r["params_b"] <= max_params_b]
    return rows[:limit]


def model(repo_id: str) -> dict[str, Any]:
    api = HfApi()
    try:
        info = api.model_info(repo_id, expand=EXPAND)
    except RepositoryNotFoundError as cause:
        raise ProjectError(f"{repo_id} does not exist on the Hub (or is private without a token)") from cause
    except GatedRepoError as cause:
        raise ProjectError(f"{repo_id} is gated: accept its terms on huggingface.co and save a token") from cause
    except HfHubHTTPError as cause:
        raise ProjectError(f"Hugging Face Hub error: {cause}") from cause
    return describe(info)


def download(repo_id: str, revision: str | None = None, progress: Any = print) -> dict[str, Any]:
    """Download the transformers-loadable files of a model repository into the Hub cache."""
    api = HfApi()
    try:
        info = api.model_info(repo_id, revision=revision, files_metadata=True)
    except RepositoryNotFoundError as cause:
        raise ProjectError(f"{repo_id} does not exist on the Hub (or is private without a token)") from cause
    except GatedRepoError as cause:
        raise ProjectError(f"{repo_id} is gated: accept its terms on https://huggingface.co/{repo_id} and save a token (tinystudio hub login)") from cause
    except HfHubHTTPError as cause:
        raise ProjectError(f"Hugging Face Hub error: {cause}") from cause
    files = [s.rfilename for s in info.siblings or []]
    has_safetensors = any(f.endswith(".safetensors") for f in files)
    ignore = ["*.gguf", "*.onnx", "*.msgpack", "*.h5", "*.tflite", "*.ot", "*.mlpackage/*", "*.ckpt"]
    if has_safetensors:
        ignore.append("*.bin")  # avoid downloading duplicate pickle weights
    size = sum((s.size or 0) for s in info.siblings or [] if not any(Path(s.rfilename).match(p) for p in ignore))
    progress(f"{repo_id}: {len(files)} files, about {size / 1e6:,.0f} MB to fetch")
    try:
        path = snapshot_download(repo_id, revision=revision, ignore_patterns=ignore)
    except GatedRepoError as cause:
        raise ProjectError(f"{repo_id} is gated: accept its terms and save a token") from cause
    except HfHubHTTPError as cause:
        raise ProjectError(f"download failed: {cause}") from cause
    progress(f"downloaded to {path}")
    return {"repo_id": repo_id, "path": str(path), "bytes": size, "transformers_loadable": (Path(path) / "config.json").exists()}


def cached_models() -> list[dict[str, Any]]:
    """Models in the local Hub cache with sizes and whether transformers can load them."""
    try:
        info = scan_cache_dir()
    except Exception as cause:  # noqa: BLE001 - a missing cache directory is not an error for the page
        if "not" in str(cause).lower() or isinstance(cause, FileNotFoundError):
            return []
        raise
    rows = []
    for repo in info.repos:
        if repo.repo_type != "model":
            continue
        revisions = sorted(repo.revisions, key=lambda r: r.last_modified or 0, reverse=True)
        latest = revisions[0] if revisions else None
        snapshot = Path(latest.snapshot_path) if latest else None
        is_mlx = repo.repo_id.lower().startswith("mlx-community/") or "-mlx" in repo.repo_id.lower()
        has_weights = bool(snapshot and (snapshot / "config.json").exists() and any(p.suffix in (".safetensors", ".bin") for p in snapshot.iterdir()))
        rows.append(
            {
                "id": repo.repo_id,
                "size_bytes": repo.size_on_disk,
                "size_gb": round(repo.size_on_disk / 1e9, 2),
                "last_accessed": repo.last_accessed,
                "revisions": len(repo.revisions),
                "transformers_loadable": has_weights and not is_mlx,  # MLX repos hold MLX-quantized safetensors that transformers cannot load
                "mlx": is_mlx and has_weights,
                "path": str(snapshot) if snapshot else None,
            }
        )
    rows.sort(key=lambda r: r["last_accessed"] or 0, reverse=True)
    return rows


def delete_cached(repo_id: str) -> dict[str, Any]:
    info = scan_cache_dir()
    for repo in info.repos:
        if repo.repo_id == repo_id and repo.repo_type == "model":
            strategy = info.delete_revisions(*[r.commit_hash for r in repo.revisions])
            freed = strategy.expected_freed_size
            strategy.execute()
            return {"deleted": repo_id, "freed_bytes": freed}
    raise ProjectError(f"{repo_id} is not in the cache")


def token_status() -> dict[str, Any]:
    token = get_token()
    masked = f"{token[:6]}…{token[-3:]}" if token and len(token) > 10 else ("set" if token else None)
    return {"configured": bool(token), "masked": masked, "source": "HF_TOKEN env" if os.environ.get("HF_TOKEN") else ("token file" if token else None)}


def save_token(token: str) -> dict[str, Any]:
    token = token.strip()
    if not token.startswith("hf_"):
        raise ProjectError("Hugging Face tokens start with hf_")
    try:
        login(token=token, add_to_git_credential=False, skip_if_logged_in=False)
        who = HfApi().whoami(token=token)
    except HfHubHTTPError as cause:
        raise ProjectError(f"the token was rejected: {cause}") from cause
    return {"configured": True, "user": who.get("name"), "type": who.get("type")}


def upload(folder: Path, repo_id: str, repo_type: str = "model", private: bool = True, message: str = "Upload from tinystudio") -> dict[str, Any]:
    if not folder.is_dir():
        raise ProjectError(f"{folder} is not a directory")
    api = HfApi()
    try:
        api.create_repo(repo_id, repo_type=repo_type, private=private, exist_ok=True)
        commit = api.upload_folder(repo_id=repo_id, folder_path=str(folder), repo_type=repo_type, commit_message=message)
    except HfHubHTTPError as cause:
        raise ProjectError(f"upload failed: {cause}") from cause
    return {"repo_id": repo_id, "repo_type": repo_type, "url": f"https://huggingface.co/{'datasets/' if repo_type == 'dataset' else ''}{repo_id}", "commit": getattr(commit, "oid", None)}


def free_disk_gb(path: Path | None = None) -> float:
    usage = shutil.disk_usage(path or Path.home())
    return round(usage.free / 1e9, 1)
