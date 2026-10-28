"""Small, auditable CPU language experiments with portable local checkpoints.

These models are initialized locally and train every parameter. Checkpoints use
PyTorch's restricted weights-only loader and contain no executable Python objects.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import random
from pathlib import Path
from typing import Any, Callable


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def artifact(path: Path, role: str, media_type: str = "application/json") -> dict:
    return {"name": path.name, "path": str(path), "media_type": media_type,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "role": role}


def resolved_inputs(spec: dict, exclude_roles: set[str] | None = None, *, visibilities=("model",)) -> list[dict]:
    """Bind resolved local paths to the revision's expected artifact hashes."""
    paths = spec.get("input_paths", [])
    inputs = spec["definition"].get("inputs", [])
    if inputs:
        result = []
        for index, item in enumerate(inputs):
            if visibilities is not None and item.get("visibility", "model") not in visibilities:
                continue
            if visibilities == ("model",) and item.get("role") in {"validation", "test", "final_test", "truth"}:
                continue
            if item.get("role") in (exclude_roles or set()):
                continue
            raw = paths.get(item["artifact_id"]) if isinstance(paths, dict) else paths[index]
            path = raw.get("path") if isinstance(raw, dict) else raw
            if not path:
                raise ValueError(f"input path missing for {item['artifact_id']}")
            result.append({"path": path, "sha256": item["sha256"], "role": item.get("role", "source"), "visibility": item.get("visibility", "model")})
        return result
    values = list(paths.values()) if isinstance(paths, dict) else paths
    bindings = [value if isinstance(value, dict) else {"path": value} for value in values]
    return [value for value in bindings if visibilities is None or value.get("visibility", "model") in visibilities]


def evaluator_validation_inputs(spec: dict) -> list[dict]:
    """Validation is a separate trusted binding; final-test/truth are never resolved."""
    bindings = []
    paths = spec.get("evaluation_input_paths", {})
    for item in spec["definition"].get("inputs", []):
        if item.get("role") != "validation":
            continue
        if item.get("visibility") != "evaluation":
            raise ValueError("validation role requires evaluation visibility")
        raw = paths.get(item["artifact_id"])
        if not raw:
            raise ValueError("server has not supplied the evaluator-only validation binding")
        path = raw.get("path") if isinstance(raw, dict) else raw
        bindings.append({**item, "path": str(path)})
    return bindings


def read_documents(spec: dict, *, include_validation=True, forced_partition=None, min_documents=2) -> list[dict]:
    """Read model text while retaining partition, group and immutable source identity."""
    params = spec["definition"].get("parameters", {})
    supplied = params.get("texts", [])
    if not isinstance(supplied, list) or not all(isinstance(text, str) for text in supplied):
        raise ValueError("parameters.texts must be a list of strings")
    documents = [{"text": text, "source_record_id": f"inline:{index}"} for index, text in enumerate(supplied)]
    documents.extend(dict(row) for row in params.get("documents", []))
    for raw in resolved_inputs(spec, {"resume", "initial_weights", "teacher", "pretrained_model", "embedding_model"}):
        if raw.get("visibility", "model") != "model":
            raise ValueError("language learning inputs must be model-visible; truth/evaluation refs are excluded")
        path = Path(raw["path"])
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if raw.get("sha256") and digest != raw["sha256"]:
            raise ValueError(f"input checksum mismatch for {path.name}")
        if content.startswith(b"PAR1"):
            import pyarrow.parquet as pq
            import pyarrow as pa
            rows = pq.read_table(pa.BufferReader(content)).to_pylist()
        elif path.suffix in {".jsonl", ".ndjson"} or content.lstrip().startswith((b"{", b"[")):
            try:
                rows = json.loads(content)
            except json.JSONDecodeError:
                rows = [json.loads(line) for line in content.decode().splitlines() if line.strip()]
            if isinstance(rows, dict):
                rows = rows.get("records", rows.get("rows", [rows]))
        else:
            rows = [part for part in content.decode().splitlines() if part.strip()]
        for index, row in enumerate(rows):
            row = {"text": row} if isinstance(row, str) else dict(row)
            if row.get("visibility") == "truth":
                raise ValueError("truth evidence must not enter language-model text")
            if forced_partition:
                partition = row.get("partition", row.get("split", row.get("bucket")))
                if partition is not None and str(partition).lower() not in {"validation", "val", "dev", "eval"}:
                    raise ValueError("validation artifact contains non-validation partition rows")
                row.update(partition="validation", split="validation", visibility="evaluation")
            if row.get("visibility") == "truth" or any(key in row for key in ("ground_truth", "true_value", "injected_fault")):
                raise ValueError("truth evidence must not enter language-model text")
            row.setdefault("source_record_id", f"{digest}:{index}")
            documents.append(row)
    if include_validation:
        validation = evaluator_validation_inputs(spec)
        if validation:
            for row in documents:
                if row.get("partition", row.get("split", row.get("bucket"))) is None:
                    row["partition"] = "train"
            for binding in validation:
                view = {"definition": {"parameters": {}, "inputs": [{**binding, "role": "source", "visibility": "model"}]},
                        "input_paths": {binding["artifact_id"]: binding["path"]}}
                documents.extend(read_documents(view, include_validation=False, forced_partition="validation", min_documents=1))
    for row in documents:
        if not isinstance(row.get("text"), str) and isinstance(row.get("messages"), list):
            row["text"] = "\n".join(str(message.get("content", "")) for message in row["messages"])
        if not isinstance(row.get("text"), str):
            raise ValueError("training rows need text or messages")
        partition = row.get("partition", row.get("split", row.get("bucket")))
        if row.get("visibility") == "evaluation" and partition not in {"validation", "val", "test", "final_test", "final-test"}:
            raise ValueError("evaluation-visible language data requires an explicit held-out partition")
        if row.get("visibility") == "truth" or any(key in row for key in ("ground_truth", "true_value", "injected_fault")):
            raise ValueError("truth evidence must not enter language-model text")
    documents = [row for row in documents if isinstance(row.get("text"), str) and row["text"].strip()]
    if len(documents) < min_documents:
        raise ValueError("at least two nonempty documents are required for held-out validation")
    return documents


def read_texts(spec: dict) -> list[str]:
    """Convenience reader; training uses read_documents to preserve split provenance."""
    return [row["text"] for row in read_documents(spec)]


class ByteTokenizer:
    """Lossless byte fallback with optional learned merges or domain lexemes."""
    def __init__(self, kind: str = "byte", merges: list[list[int]] | None = None,
                 vocabulary: list[str] | None = None):
        if not isinstance(kind, str) or kind not in {"byte", "bpe", "engineering"}:
            raise ValueError(f"unsupported checkpoint tokenizer kind {kind!r}")
        merges = [] if merges is None else merges
        vocabulary = [] if vocabulary is None else vocabulary
        if not isinstance(merges, list) or not isinstance(vocabulary, list):
            raise ValueError("tokenizer merges and vocabulary must be lists")
        for offset, pair in enumerate(merges):
            if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                or any(type(token) is not int or not 0 <= token < 256 + offset for token in pair)):
                raise ValueError("tokenizer merge must reference only earlier tokens")
        if any(not isinstance(word, str) or not word for word in vocabulary) or len(set(vocabulary)) != len(vocabulary):
            raise ValueError("tokenizer vocabulary must contain unique nonempty strings")
        if (kind != "bpe" and merges) or (kind != "engineering" and vocabulary) or (kind == "engineering" and not vocabulary):
            raise ValueError("tokenizer state conflicts with its kind")
        self._kind = kind
        self._merges = tuple(tuple(pair) for pair in merges)
        self._vocabulary = tuple(vocabulary)
        pieces = [bytes([index]) for index in range(256)]
        for left, right in self._merges:
            pieces.append(pieces[left] + pieces[right])
        pieces.extend(word.encode() for word in self._vocabulary)
        self._pieces = tuple(pieces)

    @classmethod
    def fit(cls, texts: list[str], kind: str, vocab_size: int = 320,
            vocabulary: list[str] | None = None) -> "ByteTokenizer":
        if not isinstance(kind, str):
            raise ValueError("representation kind must be a string")
        if kind in {"byte", "bytes", "byte_level"}:
            return cls()
        if kind in {"engineering", "domain", "engineering_vocabulary"}:
            if not isinstance(vocabulary, list) or not vocabulary or any(not isinstance(word, str) or not word for word in vocabulary):
                raise ValueError("engineering representation requires a nonempty vocabulary")
            words = sorted(set(vocabulary), key=lambda word: (-len(word.encode()), word))
            return cls("engineering", vocabulary=words)
        if kind not in {"bpe", "byte_bpe"}:
            raise ValueError(f"unsupported representation {kind!r}")
        sequences = [list(text.encode()) for text in texts]
        merges = []
        for token in range(256, min(max(256, int(vocab_size) - 1), 2048)):
            counts: dict[tuple[int, int], int] = {}
            for seq in sequences:
                for pair in zip(seq, seq[1:]):
                    counts[pair] = counts.get(pair, 0) + 1
            if not counts:
                break
            pair = min(counts, key=lambda item: (-counts[item], item))
            if counts[pair] < 2:
                break
            merges.append(list(pair))
            sequences = [cls._merge(seq, pair, token) for seq in sequences]
        return cls("bpe", merges=merges)

    @staticmethod
    def _merge(seq: list[int], pair: tuple[int, int], token: int) -> list[int]:
        result, index = [], 0
        while index < len(seq):
            if index + 1 < len(seq) and (seq[index], seq[index + 1]) == pair:
                result.append(token)
                index += 2
            else:
                result.append(seq[index])
                index += 1
        return result

    def encode(self, text: str) -> list[int]:
        raw = text.encode()
        if self.kind == "engineering":
            tokens, index = [], 0
            while index < len(raw):
                match = next(((256 + offset, word) for offset, word in enumerate(self.pieces[256:])
                              if raw.startswith(word, index)), None)
                if match:
                    tokens.append(match[0])
                    index += len(match[1])
                else:
                    tokens.append(raw[index])
                    index += 1
            return tokens
        seq = list(raw)
        for offset, pair in enumerate(self.merges):
            seq = self._merge(seq, tuple(pair), 256 + offset)
        return seq

    def decode(self, tokens: list[int]) -> str:
        return b"".join(self.pieces[token] for token in tokens if token != self.eos_id).decode(errors="replace")

    @property
    def kind(self):
        return self._kind

    @property
    def pieces(self):
        return self._pieces

    @property
    def eos_id(self):
        return len(self._pieces)

    @property
    def vocab_size(self):
        return self.eos_id + 1

    @property
    def merges(self):
        return [list(pair) for pair in self._merges]

    @property
    def vocabulary(self):
        return list(self._vocabulary)

    def state(self) -> dict:
        return {"kind": self.kind, "merges": self.merges, "vocabulary": self.vocabulary}


def build_model(architecture: dict, vocab_size: int):
    """Construct a causal model without fetching any pretrained assets."""
    import torch
    from torch import nn
    if not isinstance(architecture.get("family"), str) or architecture.get("family") not in {"transformer", "gru"}:
        raise ValueError("unsupported checkpoint architecture family")

    class CausalLanguageModel(nn.Module):
        def __init__(self):
            super().__init__()
            hidden = architecture["hidden_size"]
            self.embedding = nn.Embedding(vocab_size, hidden)
            self.family = architecture["family"]
            if self.family == "transformer":
                self.position = nn.Embedding(architecture["context"], hidden)
                layer = nn.TransformerEncoderLayer(hidden, architecture["heads"],
                    dim_feedforward=hidden * 4, dropout=0.0, batch_first=True, activation="gelu")
                self.core = nn.TransformerEncoder(layer, architecture["layers"], enable_nested_tensor=False)
            else:
                self.core = nn.GRU(hidden, hidden, architecture["layers"], batch_first=True)
            self.head = nn.Linear(hidden, vocab_size)

        def forward(self, tokens):
            value = self.embedding(tokens)
            if self.family == "transformer":
                size = tokens.shape[1]
                value = value + self.position(torch.arange(size, device=tokens.device))
                mask = torch.ones(size, size, device=tokens.device, dtype=torch.bool).triu(1)
                value = self.core(value, mask=mask)
            else:
                value, _ = self.core(value)
            return self.head(value)

    return CausalLanguageModel().float()


def load_checkpoint(path: str | Path, *, expected_sha256: str | None = None):
    import torch
    content = Path(path).read_bytes()
    if expected_sha256 is not None and hashlib.sha256(content).hexdigest() != expected_sha256:
        raise ValueError("checkpoint input checksum mismatch")
    state = torch.load(io.BytesIO(content), map_location="cpu", weights_only=True)
    if state.get("format") != "tinystudio.learning.v1":
        raise ValueError("unsupported learning checkpoint format")
    from .learning_initialization import validate_architecture
    validate_architecture(state["architecture"])
    tokenizer = ByteTokenizer(**state["tokenizer"])
    model = build_model(state["architecture"], tokenizer.vocab_size)
    model.load_state_dict(state["model"])
    model.eval()
    return model, tokenizer, state


def generate_from_checkpoint(path: str | Path, prompt: str, max_new_tokens: int = 32) -> dict:
    """Greedy CPU inference from a newly constructed, reloaded model."""
    import torch
    if not 0 <= max_new_tokens <= 1024:
        raise ValueError("max_new_tokens must be between 0 and 1024")
    model, tokenizer, state = load_checkpoint(path)
    ids = tokenizer.encode(prompt) or [tokenizer.eos_id]
    generated = []
    with torch.no_grad():
        for _ in range(max_new_tokens):
            batch = torch.tensor([ids[-state["architecture"]["context"]:]], dtype=torch.long)
            token = int(model(batch)[0, -1].argmax())
            if token == tokenizer.eos_id:
                break
            ids.append(token)
            generated.append(token)
    return {"prompt": prompt, "completion": tokenizer.decode(generated), "token_ids": generated,
            "checkpoint_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}


def _windows(texts: list[str], tokenizer: ByteTokenizer, context: int):
    import torch
    examples = []
    for text in texts:
        ids = tokenizer.encode(text) + [tokenizer.eos_id]
        for offset in range(0, len(ids) - 1, context):
            chunk = ids[offset:offset + context + 1]
            examples.append((torch.tensor([chunk[:-1]]), torch.tensor(chunk[1:])))
    return examples


def execute_learning(spec: dict, output_dir: Path, emit: Callable[[dict], None],
                     cancelled: Callable[[], bool]) -> dict:
    import torch
    from torch.nn import functional as F

    definition = spec["definition"]
    params = dict(definition.get("parameters", {}))
    learning = definition.get("learning") or {}
    from .learning_axes import architecture as requested_architecture, runtime_axes, selector, learning_rate
    axes = runtime_axes(learning, params, strategies={"adamw", "full", "full_parameter", "full_finetune"},
        default_strategy="adamw", initializations={"scratch", "from_scratch", "random", "weights", "resume"})
    initialization = axes["initialization"]
    from .learning_initialization import checkpoint_binding, inherited_configuration
    binding, teacher_binding = checkpoint_binding(spec, initialization)
    saved = None
    restored_model = restored_tokenizer = None
    if binding:
        # Model construction consumes RNG, so keep validation outside the training RNG scope.
        with torch.random.fork_rng(devices=[]):
            restored_model, restored_tokenizer, saved = load_checkpoint(binding["path"], expected_sha256=binding["sha256"])
        inherited_configuration(learning, params, saved["architecture"], restored_tokenizer,
                                saved.get("tokenizer_configuration"))
    objective = selector(learning.get("objective", "causal_lm"), "objective")
    if objective not in {"causal_lm", "next_token", "cross_entropy", "distillation"}:
        raise ValueError(f"unsupported learning objective {objective!r}")
    family = selector(learning.get("family", params.get("family", saved["architecture"]["family"] if saved else "transformer")), "family")
    aliases = {"tiny_transformer": "transformer", "decoder_transformer": "transformer", "rnn": "gru"}
    family = aliases.get(family, family)
    if "family" in learning and "family" in params and aliases.get(selector(params["family"], "family"), selector(params["family"], "family")) != family:
        raise ValueError("conflicting family axes")
    if family not in {"transformer", "gru"}:
        raise ValueError(f"unsupported scratch language family {family!r}")
    fields = {"hidden_size": (32, 4, 512), "layers": (1, 1, 8), "context": (32, 2, 512)}
    if family == "transformer":
        fields["heads"] = (2, 1, 16)
    if saved is not None:
        fields = {name: (saved["architecture"][name], low, high) for name, (_, low, high) in fields.items()}
    architecture = {"family": family, **requested_architecture(learning, params, fields)}
    if family == "transformer" and architecture["hidden_size"] % architecture["heads"]:
        raise ValueError("hidden_size must be divisible by heads")
    if saved is not None:
        architecture = dict(saved["architecture"])
    if "batch_size" in params and params["batch_size"] != 1:
        raise ValueError("scratch language backend currently supports batch_size=1 only")
    seed = int(definition.get("seed", 42))
    documents = read_documents(spec)
    texts = [row["text"] for row in documents]
    validation_fraction = float(params.get("validation_fraction", 0.2))
    if not 0 < validation_fraction < 1:
        raise ValueError("validation_fraction must be between zero and one")
    from .compatibility import partition_snapshot
    partitions = partition_snapshot(documents, seed, validation_fraction, params.get("leakage_policy"), min_training_rows=1)
    val_ids, train_ids = partitions["validation"], partitions["train"]
    representation = learning.get("representation", "byte")
    rep_options = representation if isinstance(representation, dict) else {}
    representation = rep_options.get("kind", "byte") if isinstance(representation, dict) else representation
    tokenizer = restored_tokenizer or ByteTokenizer.fit([texts[index] for index in train_ids], representation,
        int(rep_options.get("vocab_size", params.get("vocab_size", 320))),
        rep_options.get("vocabulary", params.get("vocabulary")))
    if saved is not None:
        tokenizer_configuration = saved.get("tokenizer_configuration") or {"vocab_size": tokenizer.vocab_size}
    else:
        tokenizer_configuration = {"vocab_size": int(rep_options.get("vocab_size", params.get("vocab_size", 320)))
                                   if tokenizer.kind == "bpe" else tokenizer.vocab_size}
    corpus_digest = _digest(documents)
    train = _windows([texts[index] for index in train_ids], tokenizer, architecture["context"])
    validation = _windows([texts[index] for index in val_ids], tokenizer, architecture["context"])
    steps = int(params.get("steps", params.get("max_steps", max(1, int(float(params.get("epochs", 1)) * len(train))))))
    if not 1 <= steps <= 100000:
        raise ValueError("steps must be between 1 and 100000")
    lr = learning_rate(params, 0.003)
    if not math.isfinite(lr) or not 0 < lr <= 1:
        raise ValueError("learning rate must be finite and between zero and one")
    temperature = float(params.get("temperature", 2.0))
    alpha = float(params.get("distillation_alpha", 0.5))
    if not math.isfinite(temperature) or temperature <= 0 or not 0 <= alpha <= 1:
        raise ValueError("distillation needs positive temperature and alpha in [0, 1]")
    teacher_path = teacher_binding["path"] if teacher_binding else None
    teacher_digest = teacher_binding["sha256"] if teacher_binding else None
    distillation = {"temperature": temperature, "alpha": alpha}
    if initialization == "resume":
        if (saved["corpus_sha256"] != corpus_digest or saved["split"] != {"train": train_ids, "validation": val_ids}
            or saved["objective"] != objective or saved["learning_rate"] != lr
            or saved["backend"] != "cpu" or saved["torch_version"] != str(torch.__version__)
            or saved["teacher_sha256"] != teacher_digest or saved["distillation"] != distillation):
            raise ValueError("resume checkpoint does not match corpus, split, architecture, tokenizer, objective or learning rate")
        if steps <= saved["step"]:
            raise ValueError("resume target steps must exceed checkpoint step")
    lineage = (saved.get("lineage") or {"initialization": "scratch", "parent_checkpoint_sha256": None}) if initialization == "resume" else {
        "initialization": initialization, "parent_checkpoint_sha256": binding["sha256"] if binding else None}
    for source in (binding, teacher_binding):
        if source and Path(source["path"]).resolve() == (output_dir / "checkpoint.pt").resolve():
            raise ValueError("output checkpoint must not overwrite an immutable input artifact")
    output_dir.mkdir(parents=True, exist_ok=True)
    previous_threads = torch.get_num_threads()
    # fork_rng restores the shared worker's RNG state after this local experiment.
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(seed)
        torch.set_num_threads(1)
        try:
            model = restored_model if restored_model is not None else build_model(architecture, tokenizer.vocab_size)
            restored_model = None
            optimizer = torch.optim.AdamW(model.parameters(), lr=lr)
            generator = torch.Generator().manual_seed(seed + 1)
            start = 0
            if initialization == "resume":
                optimizer.load_state_dict(saved["optimizer"])
                generator.set_state(saved["sampler_rng"])
                torch.set_rng_state(saved["torch_rng"])
                start = saved["step"]
            initial = {key: value.detach().clone() for key, value in model.state_dict().items()}
            teacher = None
            if objective == "distillation":
                training_rng = torch.get_rng_state()
                teacher, teacher_tokenizer, teacher_state = load_checkpoint(teacher_path, expected_sha256=teacher_digest)
                torch.set_rng_state(training_rng)
                if teacher_tokenizer.state() != tokenizer.state() or teacher_state["architecture"]["context"] < architecture["context"]:
                    raise ValueError("teacher and student must share tokenizer and compatible context")
                for parameter in teacher.parameters():
                    parameter.requires_grad_(False)
            records = []
            model.eval()
            def evaluate():
                total, tokens = 0.0, 0
                with torch.no_grad():
                    for x, y in validation:
                        total += float(F.cross_entropy(model(x).reshape(-1, tokenizer.vocab_size), y, reduction="sum"))
                        tokens += y.numel()
                return total / tokens
            initial_validation = evaluate()
            completed = start
            model.train()
            for step in range(start, steps):
                if cancelled():
                    break
                x, y = train[int(torch.randint(len(train), (), generator=generator))]
                optimizer.zero_grad(set_to_none=True)
                logits = model(x).reshape(-1, tokenizer.vocab_size)
                ce = F.cross_entropy(logits, y)
                loss = ce
                if teacher is not None:
                    with torch.no_grad():
                        teacher_logits = teacher(x).reshape(-1, tokenizer.vocab_size)
                    kl = F.kl_div(F.log_softmax(logits / temperature, dim=-1),
                        F.softmax(teacher_logits / temperature, dim=-1), reduction="batchmean") * temperature ** 2
                    loss = (1 - alpha) * ce + alpha * kl
                if not torch.isfinite(loss):
                    raise ValueError("training produced non-finite loss")
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
                completed = step + 1
                row = {"step": completed, "loss": float(loss.detach()), "cross_entropy": float(ce.detach())}
                if teacher is not None:
                    row["distillation_kl"] = float(kl.detach())
                records.append(row)
                emit({"type": "metric", **row})
            model.eval()
            final_validation = evaluate()
            changed = any(not torch.equal(initial[key], value) for key, value in model.state_dict().items())
            state = {"format": "tinystudio.learning.v1", "backend": "cpu", "torch_version": str(torch.__version__),
                     "architecture": architecture, "tokenizer": tokenizer.state(), "model": model.state_dict(),
                     "tokenizer_configuration": tokenizer_configuration, "lineage": lineage,
                     "initialization": initialization, "parent_checkpoint_sha256": lineage["parent_checkpoint_sha256"],
                     "optimizer_state_restored": initialization == "resume",
                     "initialization_checkpoint_sha256": binding["sha256"] if binding else None,
                     "optimizer": optimizer.state_dict(), "torch_rng": torch.get_rng_state(),
                     "sampler_rng": generator.get_state(), "seed": seed, "step": completed,
                     "corpus_sha256": corpus_digest, "split": {"train": train_ids, "validation": val_ids},
                     "partition_manifest": partitions,
                     "objective": objective, "learning_rate": lr,
                     "teacher_sha256": teacher_digest, "distillation": distillation}
            checkpoint = output_dir / "checkpoint.pt"
            torch.save(state, checkpoint)
            probe = train[0][0]
            with torch.no_grad():
                expected = model(probe).clone()
            del optimizer, model
            reloaded, _, _ = load_checkpoint(checkpoint)
            with torch.no_grad():
                reload_equal = torch.equal(expected, reloaded(probe))
            sample = generate_from_checkpoint(checkpoint, params.get("prompt", texts[train_ids[0]][:24]), int(params.get("max_new_tokens", 16)))
            sample_path = output_dir / "generation.json"
            sample_path.write_text(json.dumps(sample, indent=2))
            metrics = {"steps": completed, "parameters": sum(p.numel() for p in reloaded.parameters()),
                       "initial_validation_loss": initial_validation, "validation_loss": final_validation,
                       "validation_perplexity": math.exp(min(final_validation, 80)), "parameters_changed": changed,
                       "reload_logits_equal": reload_equal, "training_documents": len(train_ids),
                       "validation_documents": len(val_ids), "excluded_test_documents": len(partitions["excluded_test"]), "corpus_sha256": corpus_digest}
            manifest = output_dir / "learning.json"
            manifest.write_text(json.dumps({"architecture": architecture, "tokenizer": tokenizer.state(),
                "metrics": metrics, "split": state["split"], "partitions": partitions, "records": records}, indent=2))
            return {"records": records, "metrics": metrics,
                    "artifacts": [artifact(checkpoint, "checkpoint", "application/octet-stream"),
                                  artifact(manifest, "training_manifest"), artifact(sample_path, "generation")],
                    "qualification": {"status": "cancelled" if completed < steps else "completed",
                        "backend": "cpu", "dtype": "float32", "from_scratch": initialization in {"scratch", "from_scratch", "random"},
                        "initialization": initialization, "lineage": lineage,
                        "parent_checkpoint_sha256": lineage["parent_checkpoint_sha256"],
                        "initialization_checkpoint_sha256": binding["sha256"] if binding else None,
                        "optimizer_state_restored": initialization == "resume", "parameters_changed": changed,
                        "save_unload_reload": reload_equal, "held_out_validation": True,
                        "partition_strategy": partitions["strategy"], "group_isolation": partitions["group_isolation"],
                        "final_test_excluded_from_training_and_validation": True,
                        "resume_scope": "identical corpus/configuration and CPU PyTorch version"}}
        finally:
            torch.set_num_threads(previous_threads)
