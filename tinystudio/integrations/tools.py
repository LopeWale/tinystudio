"""The capability catalog shared by the MCP server and the in-app assistant.

Every tool is a thin call to the tinystudio API (the same ``/api`` the web interface uses), scoped to one
project and authenticated with an API key, so an assistant and the page share one queue and one database.
Long actions return a task id immediately; ``get_task`` polls it. Functions carry docstrings and type hints because both
the MCP SDK and the Anthropic SDK derive the tool schema from them.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx


class StudioError(RuntimeError):
    """An expected failure reported back to the assistant as a tool error."""


PROJECT_PREFIXES = ("/api/overview", "/api/datasets", "/api/datapoints", "/api/augment", "/api/snapshots", "/api/recommend", "/api/jobs", "/api/metrics", "/api/evals", "/api/traces", "/api/stats", "/api/models", "/api/playground", "/api/repositories", "/api/interp", "/api/autopilot", "/api/catalog", "/api/artifacts")


def scoped(path: str, project: str | None) -> str:
    """Project-scoped paths live under /api/p/<slug>; global ones (tasks, hub, settings) stay as they are."""
    if project and path.startswith(PROJECT_PREFIXES):
        return f"/api/p/{project}" + path[4:]
    return path


class Studio:
    """HTTP client for a tinystudio server: one project, one credential (API key or service token)."""

    def __init__(self, base_url: str, project: str | None = None, token: str | None = None, *,
                 cf_access_client_id: str | None = None, cf_access_client_secret: str | None = None,
                 public_url: str | None = None, access_assertion: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.project = project
        headers = {"X-Requested-With": "tinystudio"}
        explicit_credentials = cf_access_client_id is not None or cf_access_client_secret is not None
        client_id = cf_access_client_id if cf_access_client_id is not None else os.environ.get("CF_ACCESS_CLIENT_ID", "")
        client_secret = cf_access_client_secret if cf_access_client_secret is not None else os.environ.get("CF_ACCESS_CLIENT_SECRET", "")
        if bool(client_id) != bool(client_secret):
            raise ValueError("Cloudflare Access client ID and secret must both be supplied")
        approved = public_url or os.environ.get("TINYSTUDIO_PUBLIC_URL", "")
        target = urlsplit(self.base_url)
        expected = urlsplit(approved)
        approved_origin = (target.scheme == expected.scheme == "https" and bool(expected.hostname)
                           and (target.hostname, target.port or 443) == (expected.hostname, expected.port or 443)
                           and not target.username and not target.password and not expected.username and not expected.password
                           and not target.query and not target.fragment and not expected.query and not expected.fragment
                           and target.path in ("", "/") and expected.path in ("", "/"))
        if client_id and approved_origin:
            headers["CF-Access-Client-Id"] = client_id
            headers["CF-Access-Client-Secret"] = client_secret
        elif client_id and explicit_credentials:
            raise ValueError("Cloudflare credentials require the configured HTTPS public origin")
        if access_assertion:
            if (target.hostname not in ("127.0.0.1", "::1", "localhost") or target.scheme not in ("http", "https")
                    or target.username or target.password or target.path not in ("", "/") or target.query or target.fragment):
                raise ValueError("forwarded Access assertions require a loopback origin")
            headers["Cf-Access-Jwt-Assertion"] = access_assertion
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if project:
            headers["X-Project"] = project
        self.http = httpx.Client(base_url=self.base_url, timeout=120.0, headers=headers, follow_redirects=False)

    def ensure_running(self) -> None:
        try:
            self.http.get("/api/health").raise_for_status()
        except httpx.HTTPError as cause:
            raise StudioError(f"tinystudio server is not reachable at {self.base_url}: {cause}. Start it with `tinystudio serve` (or docker compose up)") from cause

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        if not path.startswith("/") or path.startswith("//") or urlsplit(path).scheme or "\\" in path:
            raise ValueError("Studio API calls require a relative API path")
        if kwargs.pop("follow_redirects", False):
            raise ValueError("Studio does not forward credentials through redirects")
        response = self.http.request(method, scoped(path, self.project), follow_redirects=False, **kwargs)
        if 300 <= response.status_code < 400:
            raise StudioError("tinystudio redirected the API request; check Cloudflare Access credentials")
        if response.status_code == 401:
            raise StudioError("tinystudio rejected the credentials: pass an API key (TINYSTUDIO_API_KEY; create one under API Keys)")
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise StudioError(f"{method} {path} -> {response.status_code}: {detail}")
        if "json" not in response.headers.get("content-type", ""):
            raise StudioError("tinystudio returned a non-JSON API response; check the URL and Cloudflare Access credentials")
        return response.json()

    def upload_dataset(self, name: str, path: str) -> Any:
        """Transfer a local JSONL file, directly to R2 when the server supports it."""
        source = Path(path).expanduser()
        size = source.stat().st_size
        capabilities = self.call("GET", "/api/artifacts/config")
        if capabilities.get("direct_upload"):
            upload = self.call("POST", "/api/artifacts/uploads", json={"filename": source.name, "content_type": "application/x-ndjson", "size": size})
            destination = urlsplit(upload["url"])
            if (destination.scheme != "https" or not destination.hostname or not destination.hostname.endswith(".r2.cloudflarestorage.com")
                    or destination.username or destination.password or destination.port not in (None, 443) or destination.fragment):
                raise StudioError("server returned an invalid R2 upload destination")
            # A separate client deliberately carries neither application nor Access credentials.
            with source.open("rb") as data, httpx.Client(timeout=120.0, follow_redirects=False) as uploader:
                upload_headers = {key: value for key, value in upload.get("headers", {}).items() if key.lower() == "content-type"}
                upload_headers["Content-Length"] = str(size)
                try:
                    response = uploader.put(upload["url"], content=data, headers=upload_headers)
                except httpx.HTTPError:
                    raise StudioError("R2 upload could not complete; request another upload URL and retry") from None
                if not 200 <= response.status_code < 300:
                    raise StudioError(f"R2 upload failed ({response.status_code})")
            return self.call("POST", "/api/datasets/from-object", json={"name": name, "key": upload["key"]})
        with source.open("rb") as data:
            return self.call("POST", "/api/datasets/upload", data={"name": name}, files={"file": (source.name, data, "application/x-ndjson")})


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    func: Callable[..., Any]
    read_only: bool = False
    destructive: bool = False
    long_running: bool = False


def tool_specs(studio: Studio) -> list[ToolSpec]:
    """Build the catalog bound to one server."""

    def get_overview() -> dict[str, Any]:
        """Project overview: name, goal, datasets with snapshots and jobs, metrics, evaluations, base-model catalog, helper LLM."""
        return studio.call("GET", "/api/overview")

    def list_datasets() -> list[dict[str, Any]]:
        """List datasets with datapoint counts, buckets, labels, and snapshots."""
        return studio.call("GET", "/api/datasets")

    def get_datapoints(dataset: str, offset: int = 0, limit: int = 20) -> dict[str, Any]:
        """Read datapoints of a dataset (paged). Each row has messages, bucket, labels, source, optional rating."""
        return studio.call("GET", f"/api/datasets/{dataset}", params={"offset": offset, "limit": limit})

    def profile_dataset(dataset: str) -> dict[str, Any]:
        """Data-quality profile of a dataset: length stats and histograms, duplicates, near-duplicates, outliers, label/bucket/source distributions, JSON-answer share, ratings, PII-like patterns."""
        return studio.call("GET", f"/api/datasets/{dataset}/quality")

    def list_jobs() -> list[dict[str, Any]]:
        """List fine-tuning jobs with their experiments (state, config, losses)."""
        return studio.call("GET", "/api/jobs")

    def get_job(job: str) -> dict[str, Any]:
        """Job detail: experiments with a loss-curve summary, the dataset analysis, and the snapshot."""
        detail = studio.call("GET", f"/api/jobs/{job}")
        for exp in detail.get("experiments", []):
            loss = exp.get("loss", [])
            exp["loss"] = {"points": len(loss), "first": loss[0] if loss else None, "last": loss[-1] if loss else None}
        return detail

    def recommend(snapshot: str) -> dict[str, Any]:
        """Base-model and hyperparameter recommendation for a snapshot on this machine."""
        return studio.call("GET", f"/api/recommend/{snapshot}")

    def list_metrics() -> list[dict[str, Any]]:
        """List metrics (rubric rules, code checks, webhooks)."""
        return studio.call("GET", "/api/metrics")

    def list_evals() -> list[dict[str, Any]]:
        """List evaluations with their leaderboards."""
        return studio.call("GET", "/api/evals")

    def get_eval(name: str, worst: int = 10) -> dict[str, Any]:
        """Evaluation detail: leaderboard plus the lowest-scoring rows (prediction, reference, reason, rule verdicts)."""
        detail = studio.call("GET", f"/api/evals/{name}")
        rows = sorted(detail.get("rows", []), key=lambda r: r.get("score", 0))[:worst]
        return {"summary": detail["summary"], "worst_rows": rows}

    def list_models() -> list[dict[str, Any]]:
        """List deployable models: fine-tuned experiments (alias, base, parameters, up/down) and base models."""
        return studio.call("GET", "/api/models")

    def list_tasks() -> list[dict[str, Any]]:
        """List background tasks (synthetic generation, fine-tuning, evaluation, analysis, downloads) with state and log tail."""
        return studio.call("GET", "/api/tasks")

    def get_task(task_id: str) -> dict[str, Any]:
        """State and log tail of one task. Poll until state is succeeded or failed."""
        return studio.call("GET", f"/api/tasks/{task_id}")

    def list_traces(limit: int = 20) -> list[dict[str, Any]]:
        """Recent traces: served prompts, responses, latency, scores."""
        return studio.call("GET", "/api/traces", params={"limit": limit})

    def get_stats() -> dict[str, Any]:
        """Usage statistics: counts, training time, requests and tokens served."""
        return studio.call("GET", "/api/stats")

    def search_hub_models(query: str, limit: int = 15, max_params_billion: float | None = 2.0, task: str = "text-generation") -> list[dict[str, Any]]:
        """Search the Hugging Face Hub for text-generation models (sorted by downloads) with parameter counts, license, gated flag, and whether transformers can load them. Defaults to models up to 2B parameters. task: text-generation (default), embeddings (feature-extraction, for RAG repositories), sentence-similarity, or any."""
        return studio.call("GET", "/api/hub/search", params={"q": query, "limit": limit, "max_params_b": max_params_billion, "task": task})

    def list_hub_cache() -> list[dict[str, Any]]:
        """Models already downloaded into the local Hugging Face cache, with sizes."""
        return studio.call("GET", "/api/hub/models")

    def create_dataset_from_jsonl(name: str, path: str) -> dict[str, Any]:
        """Create a dataset from a JSONL file on disk (one {"messages": [...]} per line; system optional; last turn assistant)."""
        return studio.upload_dataset(name, path)

    def create_synthetic_dataset(name: str, files: list[str] | None = None, urls: list[str] | None = None, pairs_per_source: int = 25, temperature: float = 0.3, rules: list[str] | None = None, question_format: str = "", answer_format: str = "", system_prompt: str = "", curate_threshold: float = 0.0, goal: str = "") -> dict[str, Any]:
        """Generate a synthetic dataset from local files (pdf, txt, md, html) and/or web page URLs with the helper LLM. Returns a task; poll get_task. pairs_per_source QA pairs per file or URL; temperature 0-1 is the creativity level; curate_threshold 1-10 keeps only pairs the helper rates at or above it (0 keeps all)."""
        return studio.call("POST", "/api/datasets/synthetic-spec", json={"name": name, "files": files or [], "urls": urls or [], "pairs_per_source": pairs_per_source, "temperature": temperature, "rules": rules or [], "question_format": question_format, "answer_format": answer_format, "system_prompt": system_prompt or None, "curate_threshold": curate_threshold, "goal": goal})

    def add_datapoint(dataset: str, user: str, assistant: str, system: str = "", bucket: str = "uncategorized") -> dict[str, Any]:
        """Append one datapoint (system optional, user, assistant) to a dataset."""
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}, {"role": "assistant", "content": assistant}]
        return studio.call("POST", "/api/datapoints", json={"dataset": dataset, "messages": messages, "bucket": bucket})

    def augment_dataset(dataset: str, count: int = 50, instructions: str = "", avoid: str = "", temperature: float = 0.8) -> dict[str, Any]:
        """Enrich a dataset: paraphrase user turns with the helper LLM to add variety. `instructions` says what to generate more of, `avoid` what to leave out, `temperature` is the creativity level (0-1.5). Returns a task."""
        return studio.call("POST", "/api/augment", json={"dataset": dataset, "count": count, "instructions": instructions, "avoid": avoid, "temperature": temperature})

    def clean_dataset(dataset: str, dedupe_user: bool = False, dedupe_conversation: bool = False, drop_near_duplicates: bool = False, drop_empty: bool = False, trim_whitespace: bool = False, min_total_chars: int | None = None, max_total_chars: int | None = None, min_rating: float | None = None, require_json_answer: bool = False, drop_labels: list[str] | None = None, keep_labels: list[str] | None = None, redact_pii: bool = False) -> dict[str, Any]:
        """Clean a dataset in place: drop exact or near duplicates, empty rows, length outliers, low-rated rows, non-JSON answers, or rows with given labels; trim whitespace; redact_pii replaces emails, card numbers, national ids, and phone numbers with placeholders. Returns removed counts per reason; the run is logged."""
        return studio.call("POST", f"/api/datasets/{dataset}/clean", json={"dedupe_user": dedupe_user, "dedupe_conversation": dedupe_conversation, "drop_near_duplicates": drop_near_duplicates, "drop_empty": drop_empty, "trim_whitespace": trim_whitespace, "min_total_chars": min_total_chars, "max_total_chars": max_total_chars, "min_rating": min_rating, "require_json_answer": require_json_answer, "drop_labels": drop_labels or [], "keep_labels": keep_labels or [], "redact_pii": redact_pii})

    def split_dataset(dataset: str, train_pct: int = 80, synthetic_in_validation: bool = False) -> dict[str, Any]:
        """Autosplit uncategorized datapoints into training/validation. Existing assignments are kept."""
        return studio.call("POST", f"/api/datasets/{dataset}/split", json={"train_pct": train_pct, "synthetic_in_validation": synthetic_in_validation})

    def set_labels(dataset: str, labels: list[dict[str, str]]) -> dict[str, Any]:
        """Define the label vocabulary of a dataset: a list of {"name": ..., "description": ...}."""
        return studio.call("PUT", f"/api/datasets/{dataset}/labels", json={"labels": labels})

    def auto_label_dataset(dataset: str) -> dict[str, Any]:
        """Assign one defined label per datapoint with the helper LLM. Returns a task."""
        return studio.call("POST", f"/api/datasets/{dataset}/auto-label")

    def create_snapshot(dataset: str, name: str) -> dict[str, Any]:
        """Freeze the dataset's training and validation buckets into an immutable snapshot (needs at least 10 training rows)."""
        return studio.call("POST", "/api/snapshots", json={"dataset": dataset, "name": name})

    def create_job(name: str, snapshot: str, goal: str = "", reasoning: bool = False) -> dict[str, Any]:
        """Create a fine-tuning job on a snapshot. Then add_experiment and start_job."""
        return studio.call("POST", "/api/jobs", json={"name": name, "snapshot": snapshot, "goal": goal, "reasoning": reasoning})

    def add_experiment(job: str, base_model: str, training_type: str = "lora", epochs: float = 3, learning_rate: float = 2e-4, batch_size: int = 8, gradient_accumulation_steps: int = 2, max_length: int = 512, lora_r: int = 16, lora_alpha: int = 32, assistant_only_loss: bool = False, refinetune_from: str | None = None, checkpoint_every_steps: int = 0, keep_checkpoints: int = 0) -> dict[str, Any]:
        """Add a pending experiment to a job. training_type 'lora', 'full', or 'dpo' (preference alignment on datapoints that carry a `rejected` answer; use learning_rate ~5e-6; no QLoRA on Apple GPUs). Use recommend() for defaults; any Hub model id that transformers can load works as base_model. checkpoint_every_steps=N saves weights-only checkpoints for neuron drift (capture_activations(checkpoints=True)); refinetune_from = 'job/number' or 'ft-job-number' continues from that finished experiment's weights."""
        return studio.call("POST", f"/api/jobs/{job}/experiments", json={"experiments": [{"base_model": base_model, "training_type": training_type, "epochs": epochs, "learning_rate": learning_rate, "batch_size": batch_size, "gradient_accumulation_steps": gradient_accumulation_steps, "max_length": max_length, "lora_r": lora_r, "lora_alpha": lora_alpha, "assistant_only_loss": assistant_only_loss, "refinetune_from": refinetune_from, "checkpoint_every_steps": checkpoint_every_steps, "keep_checkpoints": keep_checkpoints}]})

    def start_job(job: str) -> dict[str, Any]:
        """Train every pending experiment of a job, one after another. Returns a task; poll get_task, then get_job for losses."""
        return studio.call("POST", f"/api/jobs/{job}/start")

    def analyze_job(job: str) -> dict[str, Any]:
        """Ask the helper LLM for the dataset analysis of a job (task summary, success criteria, edge cases, recommendation). Returns a task."""
        return studio.call("POST", f"/api/jobs/{job}/analyze")

    def create_metric(name: str, kind: str = "rubric", should: list[str] | None = None, should_not: list[str] | None = None, check: str | None = None, url: str | None = None, description: str = "") -> dict[str, Any]:
        """Create a metric. kind 'rubric' needs should/should_not rules (judged by the helper LLM); 'code' needs check in exact_match|json_valid|token_f1|similarity|contains_reference; 'webhook' needs a url that returns {score, reason}."""
        return studio.call("POST", "/api/metrics", json={"name": name, "kind": kind, "should": should or [], "should_not": should_not or [], "check": check, "url": url, "description": description})

    def generate_metric(name: str, snapshot: str, description: str = "") -> dict[str, Any]:
        """Let the helper LLM write should/should-not rules for a metric from a snapshot and a description. Returns a task."""
        return studio.call("POST", "/api/metrics/generate", json={"name": name, "snapshot": snapshot, "description": description})

    def run_eval(name: str, snapshot: str, models: list[str], metrics: list[str], limit: int | None = None, max_new_tokens: int = 256) -> dict[str, Any]:
        """Evaluate models (job/number, ft-job-number, or base ids) on a snapshot's validation split with the given metrics. Returns a task; then get_eval."""
        return studio.call("POST", "/api/evals", json={"name": name, "snapshot": snapshot, "models": models, "metrics": metrics, "limit": limit, "max_new_tokens": max_new_tokens})

    def download_hub_model(repo_id: str, revision: str | None = None) -> dict[str, Any]:
        """Download a Hugging Face model repository into the local cache so it can be used as a base model. Returns a task; gated repos need a saved HF token."""
        return studio.call("POST", "/api/hub/download", json={"repo_id": repo_id, "revision": revision})

    def set_model_state(model: str, up: bool = True) -> dict[str, Any]:
        """Load a model into memory (or unload it) so chat and the OpenAI-compatible endpoint answer faster."""
        return studio.call("POST", f"/api/models/{model.replace('/', '~')}/{'up' if up else 'down'}")

    def chat(model: str, user: str, system: str = "", temperature: float = 0.0, max_tokens: int = 256, repositories: list[str] | None = None, repository_limit: int = 3, similarity_threshold: float = 0.0) -> dict[str, Any]:
        """Send one chat turn to a model and return its reply (logged as a trace). repositories=[names] retrieves chunks from those repositories first (RAG) and returns them as document_chunks."""
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
        text: list[str] = []
        body: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if repositories:
            body["repositories"] = {"ids": repositories, "limit": repository_limit, "similarity_threshold": similarity_threshold}
        chunks: list[dict[str, Any]] = []
        with studio.http.stream("POST", scoped("/api/playground/chat", studio.project), json=body, timeout=600.0) as response:
            if response.status_code >= 400:
                raise StudioError(f"chat failed: {response.read().decode()[:300]}")
            for line in response.iter_lines():
                if line.startswith("data:"):
                    payload = json.loads(line[5:])
                    if payload.get("error"):
                        raise StudioError(payload["error"])
                    if payload.get("document_chunks"):
                        chunks = payload["document_chunks"]
                    text.append(payload.get("delta", ""))
        return {"model": model, "reply": "".join(text).strip(), **({"document_chunks": chunks} if chunks else {})}

    def score_trace(trace_id: str, score: float, corrected: str | None = None, note: str = "") -> dict[str, Any]:
        """Score a trace 0-1 and optionally attach a corrected answer; promote_traces turns scored traces into datapoints."""
        return studio.call("POST", f"/api/traces/{trace_id}/score", json={"score": score, "corrected": corrected, "note": note})

    def promote_traces(dataset: str, min_score: float | None = 0.9, preference: bool = False) -> dict[str, Any]:
        """Add traces scored at or above min_score to a dataset as new uncategorized datapoints. With preference=True only corrected traces are used and stored as DPO pairs (correction = chosen, original response = rejected) for training_type "dpo"."""
        return studio.call("POST", "/api/traces/promote", json={"dataset": dataset, "min_score": min_score, "preference": preference})

    def run_autopilot_cycle(name: str, dataset: str, base_model: str | None = None, training_type: str = "lora", epochs: float = 2.0, min_trace_score: float | None = None, preference: bool = False, metrics: list[str] | None = None, train_pct: int = 85, learning_rate: float | None = None) -> dict[str, Any]:
        """Run one continuous fine-tuning cycle: promote scored traces into the dataset, clean, split, snapshot <name>-cN, train job <name>-cN refined from the previous cycle's winner (base_model on the first cycle), evaluate it against the previous winner with the given metrics (the first decides), and record the winner. Returns a task; read the outcome with get_autopilot_history."""
        return studio.call("POST", "/api/autopilot", json={"name": name, "dataset": dataset, "base_model": base_model, "training_type": training_type, "epochs": epochs, "min_trace_score": min_trace_score, "preference": preference, "metrics": metrics or [], "train_pct": train_pct, "learning_rate": learning_rate})

    def list_captures() -> dict[str, Any]:
        """Interpretability captures (per-neuron MLP activation summaries of a model over a dataset) and before/after comparisons, with their models, sources, layers, and counts of explained and tested neurons."""
        return studio.call("GET", "/api/interp")

    def list_neurons(capture: str, layer: int, sort: str = "max", limit: int = 50, query: str = "") -> dict[str, Any]:
        """Neuron table of one captured layer: max, min, gate-open max, echo share (share of the top activations in the double-negative gate/value regime), density, explanation, and test verdict; sort by max, min, open_max, echo_share, or density; query filters by explanation text."""
        return studio.call("GET", f"/api/interp/captures/{capture}/layers/{layer}", params={"sort": sort, "limit": limit, "q": query})

    def get_neuron(capture: str, layer: int, neuron: int, limit: int = 12) -> dict[str, Any]:
        """One neuron's strongest positive, negative, and gate-open contexts (token in brackets with its datapoint, position, gate g, value v, and sign regime), regime counts, label shares, latest explanation, and latest test."""
        return studio.call("GET", f"/api/interp/captures/{capture}/layers/{layer}/neurons/{neuron}", params={"limit": limit})

    def get_comparison(comparison: str, layer: int, neuron: int | None = None, limit: int = 30) -> dict[str, Any]:
        """Before/after comparison results: without neuron, the most-changed neurons of a layer (lowest activation correlation r) and the layer summary; with neuron, the datapoints and tokens that drove that neuron's change."""
        if neuron is None:
            return studio.call("GET", f"/api/interp/diffs/{comparison}/layers/{layer}", params={"limit": limit})
        return studio.call("GET", f"/api/interp/diffs/{comparison}/layers/{layer}/neurons/{neuron}")

    def get_neuron_drift(job: str, number: int, source: str, layer: int, neuron: int) -> dict[str, Any]:
        """How one neuron's strongest examples move across an experiment's checkpoints (needs captures of job/number@step and job/number on the same dataset or snapshot): label shares, Jaccard overlap between steps, and offshoot candidates whose labels were absent at the first checkpoint."""
        return studio.call("GET", f"/api/interp/drift/{job}/{number}", params={"source": source, "layer": layer, "neuron": neuron})

    def capture_activations(model: str, dataset: str | None = None, snapshot: str | None = None, layers: list[int] | None = None, topk: int = 40, checkpoints: bool = False, name: str | None = None) -> dict[str, Any]:
        """Record per-neuron MLP activations of a model (job/number, ft-job-number, job/number@step, model dir, or Hub id) over a dataset or snapshot for the given layers (empty = all): top-k positive and negative tokens with gate/value signs, gate-open top-k, per-datapoint maxima. With checkpoints=True and model=job/number, captures every saved checkpoint plus the final weights for drift. Returns a task."""
        if checkpoints:
            from tinystudio.training.train import parse_alias

            ref = model.partition("@")[0]
            alias = parse_alias(ref)
            job, number = alias if alias else (ref.partition("/")[0], ref.partition("/")[2])
            if not str(number).isdigit():
                raise StudioError(f"checkpoints=True needs model as job/number or ft-job-number, got {model!r}")
            return studio.call("POST", "/api/interp/checkpoint-captures", json={"job": job, "number": int(number), "dataset": dataset, "snapshot": snapshot, "layers": layers or [], "topk": topk})
        return studio.call("POST", "/api/interp/captures", json={"model": model, "dataset": dataset, "snapshot": snapshot, "layers": layers or [], "topk": topk, "name": name})

    def explain_neurons(capture: str, layer: int, neurons: list[int] | None = None, top: int = 0, sort: str = "max") -> dict[str, Any]:
        """Ask the helper LLM for one-line explanations of neurons from their strongest contexts: given neurons, or the top-N unexplained neurons of the layer by sort. Returns a task."""
        return studio.call("POST", f"/api/interp/captures/{capture}/layers/{layer}/explain", json={"neurons": neurons or [], "top": top, "sort": sort})

    def test_neurons(capture: str, layer: int, neurons: list[int]) -> dict[str, Any]:
        """Run the blind-maximum test (sentences written from the explanation alone, scored against the corpus maximum) and the near-miss test (5 positives, 5 near misses, 5 distant texts; strong = 30% of the corpus maximum) for neurons; explains first when needed. Returns a task."""
        return studio.call("POST", f"/api/interp/captures/{capture}/layers/{layer}/test", json={"neurons": neurons})

    def compare_models(experiment: str, dataset: str | None = None, snapshot: str | None = None, base: str | None = None, layers: list[int] | None = None, name: str | None = None) -> dict[str, Any]:
        """Compare per-neuron activations of an experiment against its base model (or another model) on the same tokens: per-neuron correlation, relative change, and the datapoints and tokens that drove each change. Returns a task."""
        return studio.call("POST", "/api/interp/diffs", json={"experiment": experiment, "base": base, "dataset": dataset, "snapshot": snapshot, "layers": layers or [], "name": name})

    def list_repositories() -> list[dict[str, Any]]:
        """Repositories (document collections with embeddings for retrieval-augmented chat): documents, chunk counts, embedding model."""
        return studio.call("GET", "/api/repositories")

    def query_repository(repository: str, text: str, limit: int = 5, similarity_threshold: float = 0.0) -> list[dict[str, Any]]:
        """Retrieve the chunks most similar to a text from a repository (cosine similarity on the stored embeddings)."""
        return studio.call("POST", f"/api/repositories/{repository}/query", json={"text": text, "limit": limit, "similarity_threshold": similarity_threshold})

    def create_repository(name: str, description: str = "", embedding_model: str | None = None) -> dict[str, Any]:
        """Create an empty repository; embedding_model is any Hugging Face feature-extraction model (default all-MiniLM-L6-v2)."""
        return studio.call("POST", "/api/repositories", json={"name": name, "description": description, "embedding_model": embedding_model})

    def add_repository_documents(repository: str, paths: list[str] | None = None, urls: list[str] | None = None) -> dict[str, Any]:
        """Index local files (txt, md, html, pdf, docx, pptx) and web or YouTube URLs into a repository: extract, chunk, embed. Returns a task."""
        return studio.call("POST", f"/api/repositories/{repository}/documents", data={"paths": "\n".join(paths or []), "urls": "\n".join(urls or [])})

    def get_autopilot_history(name: str | None = None) -> Any:
        """Cycles of one autopilot (snapshot, experiment, evaluation, winner, decision per cycle), or a summary of every autopilot when name is omitted."""
        return studio.call("GET", f"/api/autopilot/{name}" if name else "/api/autopilot")

    def stop_task(task_id: str) -> dict[str, Any]:
        """Stop a running task (fine-tuning, evaluation, generation, download)."""
        return studio.call("POST", f"/api/tasks/{task_id}/stop")

    def delete(kind: str, name: str) -> dict[str, Any]:
        """Delete a dataset, snapshot, job, experiment (job/number), metric, evaluation, repository, capture, or comparison. Irreversible."""
        routes = {"dataset": "/api/datasets/{}", "snapshot": "/api/snapshots/{}", "job": "/api/jobs/{}", "metric": "/api/metrics/{}", "evaluation": "/api/evals/{}", "repository": "/api/repositories/{}", "capture": "/api/interp/captures/{}", "comparison": "/api/interp/diffs/{}"}
        if kind == "experiment":
            job, _, number = name.partition("/")
            return studio.call("DELETE", f"/api/jobs/{job}/experiments/{number}")
        if kind not in routes:
            raise StudioError(f"kind must be one of {sorted(routes) + ['experiment']}")
        return studio.call("DELETE", routes[kind].format(name))

    read_only = [get_overview, list_datasets, get_datapoints, profile_dataset, list_jobs, get_job, recommend, list_metrics, list_evals, get_eval, list_models, list_tasks, get_task, list_traces, get_stats, search_hub_models, list_hub_cache, get_autopilot_history, list_captures, list_neurons, get_neuron, get_comparison, get_neuron_drift, list_repositories, query_repository]
    long_running = [create_synthetic_dataset, augment_dataset, auto_label_dataset, start_job, analyze_job, generate_metric, run_eval, download_hub_model, run_autopilot_cycle, capture_activations, explain_neurons, test_neurons, compare_models, add_repository_documents]
    mutating = [create_dataset_from_jsonl, add_datapoint, clean_dataset, split_dataset, set_labels, create_snapshot, create_job, add_experiment, create_metric, set_model_state, chat, score_trace, promote_traces, stop_task, create_repository]
    specs = [ToolSpec(f.__name__, (f.__doc__ or "").strip(), f, read_only=True) for f in read_only]
    specs += [ToolSpec(f.__name__, (f.__doc__ or "").strip(), f, long_running=True) for f in long_running]
    specs += [ToolSpec(f.__name__, (f.__doc__ or "").strip(), f) for f in mutating]
    specs.append(ToolSpec("delete", (delete.__doc__ or "").strip(), delete, destructive=True))
    return specs
