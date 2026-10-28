"""Immutable, project-bound laboratory model sources with owned load lifetimes."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import tempfile
import tarfile
import zipfile

from tinystudio.core.file_manifest import directory_manifest
from tinystudio.core.project import ProjectError
from tinystudio.lab.compatibility import extract_model_archive
from tinystudio.storage import project_key, storage

_ID = re.compile(r"[0-9a-f]{32}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_MAX_BUNDLE_BYTES = 8 * 1024 ** 3


def parse_laboratory_id(value: str) -> str | None:
    """Reserve only the lab: namespace, including malformed references."""
    if not isinstance(value, str) or not value.startswith("lab:"):
        return None
    artifact_id = value[4:]
    if not _ID.fullmatch(artifact_id):
        raise ProjectError("a laboratory model id must be lab:<32 lowercase hexadecimal characters>")
    return artifact_id


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _dependencies(directory: Path):
    # The local Transformers loader joins weight_map names to its model root
    # (no subfolder is passed by load_pretrained_output), not index.parent.
    from tinystudio.training.quantization import checked_file
    for index in directory.rglob("*.index.json"):
        document = json.loads(index.read_text())
        if not isinstance(document, dict):
            raise ValueError("retained weight index must be an object")
        weights = document.get("weight_map")
        if not isinstance(weights, dict) or not weights:
            raise ValueError("retained weight index requires a nonempty weight_map")
        for relative in weights.values():
            checked_file(directory, relative)


def _manifest(root: Path):
    from tinystudio.training.quantization import checked_file, retained_base_path
    manifest = json.loads((root / "pretrained.json").read_text())
    if not isinstance(manifest, dict):
        raise ValueError("retained pretrained manifest must be an object")
    strategy = manifest.get("training_type")
    expected = {"full": ("tinystudio.pretrained.v1", "model", "model"),
                "lora": ("tinystudio.pretrained.v1", "adapter", "adapter"),
                "qlora": ("tinystudio.pretrained.v2", "quantized_adapter", "adapter")}.get(strategy)
    if expected is None or (manifest.get("format"), manifest.get("output_kind"), manifest.get("trained_directory")) != expected or manifest.get("update_strategy") != strategy:
        raise ValueError("inconsistent retained pretrained format, output kind, training or update strategy")
    trained = root / expected[2]
    if directory_manifest(trained) != manifest.get("trained_files"):
        raise ValueError("retained trained files checksum mismatch")
    _dependencies(trained)
    if strategy != "qlora" and ("quantization" in manifest or (root / "quantized-base").exists() or (root / "nf4-policy.json").exists()):
        raise ValueError("quantized model cannot use a float model reload path")
    if strategy == "lora":
        source = root / "source-model"
        if directory_manifest(source) != manifest.get("source_files"):
            raise ValueError("retained source files checksum mismatch")
        _dependencies(source)
    elif strategy == "qlora":
        base = retained_base_path(root, manifest.get("quantization"))
        _dependencies(base)
        receipt = manifest.get("reload_probes")
        if not isinstance(receipt, dict) or not isinstance(receipt.get("inputs"), list) or len(receipt["inputs"]) != 3:
            raise ValueError("quantized output requires three retained reload probes")
        probe = checked_file(root, receipt.get("file"))
        if _sha(probe) != receipt.get("sha256"):
            raise ValueError("retained reload probe checksum mismatch")
    return manifest


@dataclass(frozen=True)
class RetainedModelSource:
    project_id: str
    artifact_id: str
    sha256: str
    size: int
    storage_key: str

    def __post_init__(self):
        if not isinstance(self.project_id, str) or not _ID.fullmatch(self.project_id) or not isinstance(self.artifact_id, str) or not _ID.fullmatch(self.artifact_id):
            raise ProjectError("invalid retained model project or artifact identity")
        if not isinstance(self.sha256, str) or not _SHA.fullmatch(self.sha256) or type(self.size) is not int or self.size <= 0:
            raise ProjectError("invalid retained model checksum or size")
        if self.storage_key != project_key(self.project_id, "lab", self.sha256[:2], self.sha256):
            raise ProjectError("retained model storage key does not match its project and checksum")

    @contextmanager
    def open(self):
        """Download, verify and extract; retain files only for this load's lifetime."""
        if self.size > _MAX_BUNDLE_BYTES:
            raise ProjectError("retained laboratory model archive exceeds the 8 GiB serving limit; publish a smaller model bundle")
        with tempfile.TemporaryDirectory(prefix="ts-retained-model-") as directory:
            root = Path(directory)
            archive = root / "bundle"
            try:
                storage().download_file(self.storage_key, archive)
                if archive.is_symlink() or not archive.is_file() or archive.stat().st_size != self.size or _sha(archive) != self.sha256:
                    raise ValueError("retained model archive checksum or size mismatch")
                extracted = extract_model_archive(archive, root / "extracted", max_bytes=_MAX_BUNDLE_BYTES)
                _manifest(extracted)
            except (ValueError, OSError, KeyError, TypeError, tarfile.TarError, zipfile.BadZipFile) as cause:
                raise ProjectError(f"invalid retained laboratory model: {cause}") from cause
            yield extracted
