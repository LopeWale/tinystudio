"""Attempt-owned, offline inference with exact prompts and reserved context space."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import pickle
from pathlib import Path
import re
import tarfile
import tempfile
from typing import Callable

from .compatibility import extract_model_archive, load_pretrained_output
from .engine import ExecutionCancelled
from .learning import load_checkpoint


class ModelSession:
    """Own a verified model snapshot, tensors and extracted files until close.

    No chat formatting is added. HF tokenizers retain their special-token policy;
    an empty scratch prompt uses EOS as its explicit one-token seed. Returned
    generated IDs include a terminating EOS when the model emits one.
    """

    def __init__(self, model_path: Path, expected_sha256: str, work_dir: Path, device: str = "cpu"):
        self._temporary = None
        self._model = self._tokenizer = None
        self._closed = False
        self._metadata = {}
        try:
            if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
                raise ValueError("expected_sha256 must be a lowercase SHA256 digest")
            if not isinstance(device, str) or not device:
                raise ValueError("requested device must be explicit")
            source = Path(model_path)
            if source.is_symlink() or not source.is_file():
                raise ValueError("model_path must be a regular local model artifact")
            from tinystudio.training.runtime import TrainingPlacement
            self._placement = TrainingPlacement.resolve(device, "float32")
            work_dir = Path(work_dir)
            work_dir.mkdir(parents=True, exist_ok=True)
            self._temporary = tempfile.TemporaryDirectory(prefix="model-session-", dir=work_dir)
            owned = Path(self._temporary.name)
            snapshot = owned / "artifact"
            checksum = hashlib.sha256()
            with source.open("rb") as incoming, snapshot.open("xb") as outgoing:
                for block in iter(lambda: incoming.read(1024 * 1024), b""):
                    checksum.update(block)
                    outgoing.write(block)
            if checksum.hexdigest() != expected_sha256:
                raise ValueError("model artifact checksum mismatch")
            self._load(snapshot, owned, expected_sha256)
            self._model.eval()
            self._placement.check_model(self._model)
            for buffer in self._model.buffers():
                self._placement.check_device(buffer.device)
            self._metadata.update(sha256=expected_sha256, device=self._placement.device,
                                  model_devices=sorted(self._placement.model_devices),
                                  model_dtypes=sorted(self._placement.model_dtypes),
                                  context_capacity=self._capacity, network_downloads=False)
        except BaseException:
            self.close()
            raise

    def _load(self, snapshot: Path, owned: Path, expected_sha256: str):
        import torch
        state = None
        # Restricted Torch checkpoints are ZIPs, so probe before archive extraction.
        if not tarfile.is_tarfile(snapshot):
            try:
                state = torch.load(snapshot, map_location="cpu", weights_only=True)
            except (RuntimeError, ValueError, EOFError, KeyError, TypeError, pickle.UnpicklingError):
                pass
        self._scratch = isinstance(state, dict) and state.get("format") == "tinystudio.learning.v1"
        if self._scratch:
            self._model, self._tokenizer, state = load_checkpoint(snapshot, expected_sha256=expected_sha256)
            self._capacity = state["architecture"]["context"]
            self._model.to(self._placement.device)
            self._metadata = {"kind": "scratch_language", "provenance": {
                "format": state["format"], "architecture": state["architecture"],
                "training_corpus_sha256": state.get("corpus_sha256")}}
            return
        extracted = extract_model_archive(snapshot, owned / "unpacked")
        root = extracted
        while not (root / "pretrained.json").is_file() and not (root / "config.json").is_file():
            children = list(root.iterdir())
            if len(children) != 1 or not children[0].is_dir():
                raise ValueError("model archive must contain one local HF model or retained bundle")
            root = children[0]
        if (root / "pretrained.json").is_file():
            from tinystudio.serving.retained import _manifest
            manifest = _manifest(root)
            self._model, self._tokenizer, _ = load_pretrained_output(root, device=self._placement.device)
            quantized = manifest["training_type"] == "qlora"
            self._metadata = {"kind": "pretrained_language", "provenance": manifest}
        else:
            from transformers import AutoModelForCausalLM, AutoTokenizer
            from tinystudio.core.file_manifest import directory_manifest
            from tinystudio.serving.retained import _dependencies
            _dependencies(root)
            self._tokenizer = AutoTokenizer.from_pretrained(root, local_files_only=True, trust_remote_code=False)
            self._model = AutoModelForCausalLM.from_pretrained(root, local_files_only=True,
                                                              trust_remote_code=False, dtype=torch.float32)
            quantized = False
            self._metadata = {"kind": "pretrained_language", "provenance": {
                "format": "huggingface.causal_lm", "model_files": directory_manifest(root)}}
        if not quantized:
            self._model.to(self._placement.device)
        # Tokenizer model_max_length can be an unlimited sentinel. Require a
        # finite bound declared by the actual loaded model configuration.
        capacities = [getattr(self._model.config, name, None) for name in
                      ("max_position_embeddings", "n_positions", "max_seq_len", "max_sequence_length", "seq_length")]
        capacities = [value for value in capacities if type(value) is int and 0 < value < 10**9]
        if not capacities:
            raise ValueError("model context capacity is unknown")
        self._capacity = min(capacities)

    @property
    def metadata(self) -> dict:
        self._require_open()
        return deepcopy(self._metadata)

    def _require_open(self):
        if self._closed:
            raise RuntimeError("model session is closed")

    def __enter__(self):
        self._require_open()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def close(self):
        """Drop tensors before removing files used by the loaded model."""
        self._closed = True
        self._model = self._tokenizer = None
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None

    def generate(self, prompt: str, max_new_tokens: int, cancelled: Callable[[], bool] = lambda: False) -> dict:
        self._require_open()
        try:
            return self._generate(prompt, max_new_tokens, cancelled)
        except BaseException:
            self.close()
            raise

    def _generate(self, prompt: str, max_new_tokens: int, cancelled: Callable[[], bool]) -> dict:
        import torch
        def check_cancelled():
            if cancelled():
                raise ExecutionCancelled("model generation cancelled")
        check_cancelled()
        if not isinstance(prompt, str):
            raise ValueError("prompt must be a string")
        if type(max_new_tokens) is not int or max_new_tokens < 0:
            raise ValueError("max_new_tokens must be a nonnegative integer")
        if self._scratch:
            ids = self._tokenizer.encode(prompt) or [self._tokenizer.eos_id]
        else:
            encoded = self._tokenizer(prompt, return_tensors="pt", add_special_tokens=True, truncation=False)
            ids = encoded["input_ids"][0].tolist()
            if not ids:
                raise ValueError("HF prompt tokenization must contain at least one token")
        if len(ids) + max_new_tokens > self._capacity:
            raise ValueError("prompt plus max_new_tokens exceeds model context capacity")
        generated = []
        if max_new_tokens:
            with torch.inference_mode():
                if self._scratch:
                    current = list(ids)
                    for _ in range(max_new_tokens):
                        check_cancelled()
                        batch = torch.tensor([current], dtype=torch.long, device=self._placement.device)
                        token = int(self._model(batch)[0, -1].argmax())
                        check_cancelled()
                        generated.append(token)
                        if token == self._tokenizer.eos_id:
                            break
                        current.append(token)
                else:
                    from transformers import GenerationConfig, StoppingCriteria, StoppingCriteriaList
                    class CheckCancellation(StoppingCriteria):
                        def __call__(self, input_ids, scores, **kwargs):
                            check_cancelled()
                            return False
                    encoded = {key: value.to(self._placement.device) for key, value in encoded.items()}
                    self._placement.check_batch(encoded)
                    config = GenerationConfig(do_sample=False, num_beams=1, max_new_tokens=max_new_tokens,
                        eos_token_id=self._model.generation_config.eos_token_id,
                        pad_token_id=self._tokenizer.pad_token_id if self._tokenizer.pad_token_id is not None
                        else self._tokenizer.eos_token_id)
                    check_cancelled()
                    output = self._model.generate(**encoded, generation_config=config,
                        stopping_criteria=StoppingCriteriaList([CheckCancellation()]))
                    generated = output[0, len(ids):].tolist()
        check_cancelled()
        completion = (self._tokenizer.decode(generated) if self._scratch else
                      self._tokenizer.decode(generated, skip_special_tokens=True))
        return {"prompt": prompt, "input_token_ids": ids, "prompt_tokens": len(ids),
                "token_ids": generated, "completion": completion, "generated_tokens": len(generated),
                "context_capacity": self._capacity, "max_new_tokens": max_new_tokens}
