"""Frozen teacher ownership, token-mean loss and bounded exposure evidence."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import re

from .contracts import checksum
from .signals import number
from tinystudio.training.quantization import state_digest

EVIDENCE_FORMAT = "tinystudio.training-evidence.v1"
OVERLAP_CHECK = "teacher:heldout_training_overlap"


def training_evidence(documents, indices):
    """Exact fingerprints of this optimization partition, never a coverage claim."""
    return {"format": EVIDENCE_FORMAT, "scope": "current_run_training_partition",
            "records": [{"source_record_id": row.get("source_record_id"),
                         "record_sha256": checksum(row),
                         "text_sha256": hashlib.sha256(row["text"].encode()).hexdigest()}
                        for index in indices for row in [documents[index]]]}


def audit_teacher_exposure(teacher_state, documents, partitions, research_overrides=()):
    """Positive exact matches are evidence; no matches cannot establish clean data."""
    evidence = teacher_state.get("training_evidence")
    records = []
    if evidence is not None:
        if (not isinstance(evidence, dict) or evidence.get("format") != EVIDENCE_FORMAT
            or evidence.get("scope") != "current_run_training_partition" or not isinstance(evidence.get("records"), list)):
            raise ValueError("invalid teacher training-evidence contract")
        records = evidence["records"]
        if any(not isinstance(row, dict) or any(not re.fullmatch(r"[0-9a-f]{64}", str(row.get(key, "")))
               for key in ("record_sha256", "text_sha256")) for row in records):
            raise ValueError("invalid teacher training-evidence fingerprints")
    manifest = teacher_state.get("partition_manifest", {})
    if not isinstance(manifest, dict):
        raise ValueError("invalid teacher partition-evidence contract")
    partitions_evidence = manifest.get("partitions", {})
    training_partition = partitions_evidence.get("train", {}) if isinstance(partitions_evidence, dict) else None
    legacy = training_partition.get("records", []) if isinstance(training_partition, dict) else None
    if not isinstance(legacy, list) or any(not isinstance(row, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256", ""))) for row in legacy):
        raise ValueError("invalid teacher partition-evidence fingerprints")
    legacy_hashes = {row.get("sha256") for row in legacy if isinstance(row, dict)}
    text_hashes = {row["text_sha256"] for row in records}
    row_hashes = {row["record_sha256"] for row in records} | legacy_hashes
    matches = []
    for partition, key in (("validation", "validation"), ("test", "excluded_test")):
        for index in partitions.get(key, []):
            row = documents[index]
            text_sha = hashlib.sha256(row["text"].encode()).hexdigest()
            kind = "exact_training_text" if text_sha in text_hashes else "exact_training_record" if checksum(row) in row_hashes else None
            if kind:
                matches.append({"student_index": index, "partition": partition, "kind": kind,
                                "source_record_id": row.get("source_record_id"), "text_sha256": text_sha})
    # Use the existing explicit-check override contract; unrelated checks remain
    # owned by their evaluators rather than being consumed or discarded here.
    from .evaluation import _report
    overrides = [row for row in research_overrides if isinstance(row, dict)
                 and str(row.get("check_id", "")).startswith("teacher:")]
    report = _report([{"id": OVERLAP_CHECK, "passed": not matches,
                       "kind": "teacher_heldout_exposure", "matches": matches}], research_overrides=overrides)
    if report["qualification"]["status"] == "qualified":
        report["qualification"]["status"] = "unverified"
    report.update(scope="known teacher training fingerprints versus current student held-out documents",
                  coverage="unknown", matches=matches,
                  limitations=["Absence of matches does not establish clean teacher or inherited-tokenizer exposure.",
                               "Legacy complete-row hashes cannot detect the same text with changed metadata.",
                               "Upstream checkpoints and tokenizer fitting history may not be fully recorded."])
    return report


def distillation_loss(student_logits, teacher_logits, targets, temperature, alpha):
    """CE + temperature-scaled KL, averaged over flattened token rows."""
    import torch
    from torch.nn import functional as F
    temperature, alpha = number(temperature, "temperature"), number(alpha, "distillation_alpha")
    if temperature <= 0 or not 0 <= alpha <= 1:
        raise ValueError("distillation needs positive temperature and alpha in [0, 1]")
    if (student_logits.ndim != 2 or teacher_logits.shape != student_logits.shape
        or targets.ndim != 1 or targets.shape[0] != student_logits.shape[0]
        or not student_logits.shape[0] or student_logits.shape[1] < 2):
        raise ValueError("distillation requires equal [tokens, vocabulary] logits and [tokens] targets")
    if (not student_logits.is_floating_point() or not teacher_logits.is_floating_point()
        or targets.dtype != torch.long or targets.device != student_logits.device
        or teacher_logits.device != student_logits.device
        or (targets < 0).any() or (targets >= student_logits.shape[1]).any()):
        raise ValueError("distillation requires floating logits and valid integer token targets on one device")
    if not torch.isfinite(student_logits).all() or not torch.isfinite(teacher_logits).all():
        raise ValueError("distillation logits must be finite")
    ce = F.cross_entropy(student_logits, targets)
    kl = F.kl_div(F.log_softmax(student_logits / temperature, dim=-1),
                  F.log_softmax(teacher_logits.detach() / temperature, dim=-1),
                  reduction="batchmean", log_target=True) * temperature ** 2
    loss = (1 - alpha) * ce + alpha * kl
    if not torch.isfinite(loss):
        raise ValueError("distillation produced non-finite loss")
    return {"loss": loss, "cross_entropy": ce, "distillation_kl": kl}


class FrozenTeacher:
    """Own one verified teacher through load, student-only optimization and seal."""
    def __init__(self, model, tokenizer, state, binding):
        import torch
        if any(value.is_floating_point() and not torch.isfinite(value).all() for value in model.state_dict().values()):
            raise ValueError("teacher state must contain finite parameters and buffers")
        self._model, self._tokenizer = model, tokenizer
        self._state = {key: deepcopy(state[key]) for key in ("training_evidence", "partition_manifest") if key in state}
        self._binding = dict(binding)
        self._architecture = dict(state["architecture"])
        configuration = state.get("tokenizer_configuration")
        self._configuration = deepcopy(configuration if configuration is not None else {"vocab_size": tokenizer.vocab_size})
        if (not isinstance(self._configuration, dict)
            or type(self._configuration.get("vocab_size")) is not int
            or self._configuration["vocab_size"] < tokenizer.vocab_size):
            raise ValueError("invalid teacher tokenizer configuration")
        self._model.eval()
        for parameter in self._model.parameters():
            parameter.requires_grad_(False)
        self._before = state_digest(self._model)
        self._optimizer, self._student_ids, self._receipt = None, None, None
        self._calls, self._tokens = 0, 0

    @classmethod
    def load(cls, binding):
        from .learning import load_checkpoint
        from tinystudio.training.runtime import training_session
        with training_session():
            model, tokenizer, state = load_checkpoint(binding["path"], expected_sha256=binding["sha256"])
        return cls(model, tokenizer, state, binding)

    @property
    def tokenizer(self):
        from .learning import ByteTokenizer
        return ByteTokenizer(**deepcopy(self._tokenizer.state()))

    @property
    def configuration(self):
        return deepcopy(self._configuration)

    @property
    def architecture(self):
        return dict(self._architecture)

    def exposure(self, documents, partitions, research_overrides=()):
        return audit_teacher_exposure(self._state, documents, partitions, research_overrides)

    def validate_student(self, tokenizer, context):
        if tokenizer.state() != self._tokenizer.state() or context > self._architecture["context"]:
            raise ValueError("teacher and student must share tokenizer and compatible context")

    def _active(self):
        if self._receipt is not None:
            raise ValueError("teacher is finished")
        if self._model.training or any(parameter.requires_grad or parameter.grad is not None for parameter in self._model.parameters()):
            raise ValueError("teacher must remain frozen in evaluation mode without gradients")

    def bind_optimizer(self, optimizer, student):
        self._active()
        expected = {id(parameter) for parameter in student.parameters() if parameter.requires_grad}
        actual = [id(parameter) for group in optimizer.param_groups for parameter in group["params"]]
        teacher_ids = {id(parameter) for parameter in self._model.parameters()}
        if not expected or len(actual) != len(set(actual)) or set(actual) != expected or teacher_ids & set(actual):
            raise ValueError("distillation optimizer must contain exactly student parameters and exclude teacher")
        self._optimizer, self._student_ids = optimizer, expected

    def logits(self, tokens):
        import torch
        self._active()
        if self._optimizer is None:
            raise ValueError("bind the student optimizer before querying the teacher")
        with torch.no_grad():
            result = self._model(tokens)
        self._calls += 1
        self._tokens += tokens.numel()
        return result

    def finish(self):
        if self._receipt is not None:
            return deepcopy(self._receipt)
        self._active()
        if self._optimizer is None:
            raise ValueError("teacher has no verified student optimizer")
        actual = [id(parameter) for group in self._optimizer.param_groups for parameter in group["params"]]
        if len(actual) != len(set(actual)) or set(actual) != self._student_ids:
            raise ValueError("distillation optimizer membership changed")
        after = state_digest(self._model)
        if after != self._before:
            raise ValueError("teacher parameters or buffers changed during distillation")
        if hashlib.sha256(Path(self._binding["path"]).read_bytes()).hexdigest() != self._binding["sha256"]:
            raise ValueError("immutable teacher artifact changed during distillation")
        self._receipt = {"format": "tinystudio.distillation.teacher.v1",
            "artifact_id": self._binding.get("artifact_id"), "sha256": self._binding["sha256"],
            "architecture": self.architecture, "tokenizer_configuration": self.configuration,
            "actual_vocabulary_size": self._tokenizer.vocab_size, "tokenizer_sha256": checksum(self._tokenizer.state()),
            "state_before_sha256": self._before, "state_after_sha256": after,
            "optimizer_excluded": True, "teacher_mode": "eval_no_grad", "forward_calls": self._calls,
            "tokens_queried": self._tokens, "parameters": sum(parameter.numel() for parameter in self._model.parameters())}
        self._model, self._optimizer = None, None
        return deepcopy(self._receipt)
