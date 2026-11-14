"""Persistent evidence checks use private temporary folders and owned children."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import zipfile

import pytest

from tinystudio.lab.contracts import checksum
from tinystudio.lab.evidence_spool import EvidenceSpool, SpoolPolicy
from tinystudio.lab import evidence_spool as module


SECRET = "private-credential-sentinel-not-exportable"


def claim(identifier="attempt-one", engine="hardware.discover"):
    spec = {"research_identity": "run-one", "definition": {"engine": engine, "parameters": {"private": SECRET}},
            "implementation": {"version": 1, "files": {"lab/execution.py": "a" * 64}},
            "runtime": {"python": "3.12", "fixture-runtime": "1"}}
    return {"run_id": "run-one", "attempt_id": identifier, "lease_token": SECRET,
            "spec": spec, "spec_digest": checksum(spec)}


def spool(tmp_path, **settings):
    policy = SpoolPolicy.from_profile({"evidence_spool": settings}, "hardware.discover", tmp_path / "spool")
    return EvidenceSpool(policy)


def allocate(tmp_path, **settings):
    store = spool(tmp_path, **settings)
    entry = store.allocate(claim(), server="https://studio.example:8443", target_id="target-one")
    return store, entry


def write_evidence(entry, name="raw.jsonl", data=b'{"value":1}\n{"value":'):
    directory = entry.execution_root / "work/evidence"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def test_interrupted_initial_manifest_does_not_hide_healthy_entries(tmp_path,monkeypatch):
    store,healthy=allocate(tmp_path)
    def interrupted(entry,manifest):
        raise OSError('owned initial manifest failure')
    with monkeypatch.context() as patch:
        patch.setattr(module.SpoolAttempt,'_write_manifest',interrupted)
        with pytest.raises(OSError,match='owned initial manifest failure'):
            store.allocate(claim('interrupted'),server='https://studio.example:8443',target_id='target-one')
    malformed=store.allocate(claim('malformed'),server='https://studio.example:8443',target_id='target-one')
    manifest=json.loads(malformed.manifest_path.read_bytes())
    manifest['cleanup']['status']=[]
    malformed.manifest_path.write_text(json.dumps(manifest))
    rows=store.list_attempts()
    assert len(rows)==3
    assert next(row for row in rows if row['key']==healthy.key)['manifest']['origin']['attempt_id']=='attempt-one'
    damaged=next(row for row in rows if row['key'] not in {healthy.key,malformed.key})
    assert damaged['inspection']['status']=='unreadable'
    assert damaged['inspection']['error_type']=='FileNotFoundError'
    damaged=next(row for row in rows if row['key']==malformed.key)
    assert damaged['inspection']['status']=='unreadable'
    assert damaged['inspection']['error_type']=='TypeError'
    with pytest.raises(FileExistsError):
        store.allocate(claim('interrupted'),server='https://studio.example:8443',target_id='target-one')


@pytest.mark.parametrize("engine,enabled", [("acquisition.serial", True), ("acquisition.modbus", True),
                                          ("hardware.session", True), ("learning.language", False)])
def test_policy_enables_hardware_by_operational_default(engine, enabled, tmp_path):
    policy = SpoolPolicy.from_profile(None, engine, tmp_path / "spool")
    assert policy.enabled is enabled and policy.max_bytes is None and policy.retention == "keep"


def test_policy_choices_are_operator_configuration_not_scientific_caps(tmp_path):
    selected = tmp_path / "chosen"
    policy = SpoolPolicy.from_profile({"evidence_spool": {"enabled": True, "root": str(selected),
                                      "max_bytes": 12345, "retention": 3600}}, "learning.language", tmp_path / "unused")
    assert policy.root == selected and policy.max_bytes == 12345 and policy.retention == 3600
    disabled = SpoolPolicy.from_profile({"evidence_spool": {"enabled": False}}, "acquisition.can", selected)
    with pytest.raises(ValueError, match="disabled"):
        EvidenceSpool(disabled).allocate(claim(), server="https://studio.example", target_id="target-one")


@pytest.mark.parametrize("settings", [{"enabled": 1}, {"max_bytes": -1}, {"max_bytes": True},
                                     {"retention": -1}, {"retention": float("inf")}, {"root": "relative"}])
def test_invalid_policy_is_rejected(settings, tmp_path):
    with pytest.raises(ValueError):
        SpoolPolicy.from_profile({"evidence_spool": settings}, "hardware.discover", tmp_path / "spool")


def test_allocation_bootstraps_private_root_and_origin_before_any_execution(tmp_path):
    _, entry = allocate(tmp_path)
    assert entry.execution_root.is_dir()
    assert stat.S_IMODE(entry.execution_root.stat().st_mode) == 0o700
    manifest = entry.snapshot()["manifest"]
    assert manifest["origin"] == {"server": "https://studio.example:8443", "target_id": "target-one",
                                  "run_id": "run-one", "attempt_id": "attempt-one",
                                  "spec_digest": claim()["spec_digest"], "engine": "hardware.discover",
                                  "implementation_digest": checksum(claim()["spec"]["implementation"]),
                                  "runtime": claim()["spec"]["runtime"]}
    assert manifest["classification"] == "operator_host_evidence"
    assert manifest["outcome"]["verification"] == "unverified"
    assert manifest["cleanup"]["status"] == "unknown"
    assert SECRET not in entry.manifest_path.read_text()
    assert stat.S_IMODE(entry.manifest_path.stat().st_mode) == 0o600


def test_owned_child_abrupt_exit_preserves_partial_jsonl_and_unknown_publication(tmp_path):
    _, entry = allocate(tmp_path)
    program = '''import os,pathlib,sys
root=pathlib.Path(sys.argv[1]);p=root/'work/evidence';p.mkdir(parents=True)
with (p/'raw.jsonl').open('wb') as stream:
    stream.write(b'{"value":1}\\n{"value":');stream.flush();os.fsync(stream.fileno())
(root/'context.json').write_text(sys.argv[2]);os._exit(23)
'''
    child = subprocess.run([sys.executable, "-c", program, str(entry.execution_root), SECRET], check=False)
    assert child.returncode == 23
    snapshot = entry.snapshot()
    item = snapshot["files"][0]
    assert item["path"] == "evidence/raw.jsonl"
    assert item["jsonl"] == {"newline_count": 1, "tail_bytes": 9, "ends_with_newline": False,
                             "semantic_validation": "not_performed"}
    assert snapshot["manifest"]["cleanup"]["status"] == "unknown"
    assert snapshot["manifest"]["publication"]["status"] == "unknown"
    assert (entry.execution_root / "context.json").read_text() == SECRET
    assert (entry.execution_root / "work/evidence/raw.jsonl").read_bytes() == b'{"value":1}\n{"value":'


def test_export_includes_exact_evidence_and_manifest_excludes_private_material(tmp_path):
    _, entry = allocate(tmp_path)
    original = b'{"value":1}\n{"value":'
    write_evidence(entry, data=original)
    for name in ("context.json", "spec.json", "inputs/source.jsonl", "runtime/tinystudio/source.py",
                 "work/stdout.log", "work/stderr.log", "work/runtime-receipt.json", "work/result.json"):
        path = entry.execution_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(SECRET)
    snapshot = entry.snapshot()
    destination = tmp_path / "recovery.zip"
    receipt = entry.export(destination, expected_manifest_digest=snapshot["manifest_sha256"])
    assert receipt["sha256"] == hashlib.sha256(destination.read_bytes()).hexdigest()
    with zipfile.ZipFile(destination) as archive:
        assert set(archive.namelist()) == {"manifest.json", "evidence/raw.jsonl"}
        assert archive.read("manifest.json") == entry.manifest_path.read_bytes()
        assert archive.read("evidence/raw.jsonl") == original
        assert all(SECRET.encode() not in archive.read(name) for name in archive.namelist())
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600


def test_confirmed_cleanup_removes_only_context_and_uncertain_cleanup_keeps_it(tmp_path):
    _, entry = allocate(tmp_path)
    context = entry.execution_root / "context.json"
    context.write_text(SECRET)
    retained = write_evidence(entry)
    entry.mark_cleanup(False)
    assert context.is_file()
    entry.mark_cleanup(True)
    assert not context.exists() and retained.is_file()
    assert entry.snapshot()["manifest"]["cleanup"] == {"status": "confirmed", "private_context_removed": True}


def test_cleanup_does_not_follow_or_remove_a_symlinked_context(tmp_path):
    _, entry = allocate(tmp_path)
    outside = tmp_path / "outside-secret"
    outside.write_text(SECRET)
    (entry.execution_root / "context.json").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink|regular"):
        entry.mark_cleanup(True)
    assert outside.read_text() == SECRET
    assert entry.snapshot()["manifest"]["cleanup"]["status"] == "unknown"


def test_duplicate_allocation_cannot_overwrite_existing_evidence(tmp_path):
    store, entry = allocate(tmp_path)
    target = write_evidence(entry)
    before = entry.manifest_path.read_bytes()
    with pytest.raises(FileExistsError):
        store.allocate(claim(), server="https://studio.example:8443", target_id="target-one")
    assert target.read_bytes() and entry.manifest_path.read_bytes() == before


def test_quota_prevents_new_allocation_without_destroying_oversized_recovery(tmp_path):
    store, entry = allocate(tmp_path)
    write_evidence(entry, "large.bin", b"x" * 4096)
    limited = spool(tmp_path, max_bytes=1)
    with pytest.raises(ValueError, match="budget|quota"):
        limited.allocate(claim("attempt-two"), server="https://studio.example:8443", target_id="target-one")
    reopened = limited.open_attempt(entry.key)
    snapshot = reopened.snapshot()
    assert snapshot["manifest"]["quota"]["exceeded"] is True
    reopened.export(tmp_path / "oversized.zip", expected_manifest_digest=snapshot["manifest_sha256"])
    assert entry.execution_root.is_dir()


def test_too_small_initial_quota_allocates_no_attempt_folder(tmp_path):
    store = spool(tmp_path, max_bytes=1)
    with pytest.raises(ValueError, match="budget|quota"):
        store.allocate(claim(), server="https://studio.example", target_id="target-one")
    assert store.list_attempts() == []


@pytest.mark.parametrize("kind", ["file", "directory", "hardlink"])
def test_snapshot_rejects_evidence_links_without_reading_outside(tmp_path, kind):
    _, entry = allocate(tmp_path)
    write_evidence(entry)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text(SECRET)
    evidence = entry.execution_root / "work/evidence"
    if kind == "file":
        (evidence / "linked").symlink_to(outside / "secret")
    elif kind == "directory":
        (evidence / "linked").symlink_to(outside, target_is_directory=True)
    else:
        os.link(outside / "secret", evidence / "linked")
    with pytest.raises(ValueError, match="symlink|link|regular"):
        entry.snapshot()


def test_export_rejects_changed_source_bytes_and_removes_incomplete_archive(tmp_path):
    _, entry = allocate(tmp_path)
    target = write_evidence(entry)
    snapshot = entry.snapshot()
    target.write_bytes(b"different evidence")
    destination = tmp_path / "corrupt.zip"
    with pytest.raises(ValueError, match="changed|checksum|inventory"):
        entry.export(destination, expected_manifest_digest=snapshot["manifest_sha256"])
    assert not destination.exists()


def test_export_rejects_stale_manifest_digest_and_never_overwrites(tmp_path):
    _, entry = allocate(tmp_path)
    write_evidence(entry)
    snapshot = entry.snapshot()
    destination = tmp_path / "existing.zip"
    destination.write_bytes(b"existing bytes")
    with pytest.raises(FileExistsError):
        entry.export(destination, expected_manifest_digest=snapshot["manifest_sha256"])
    assert destination.read_bytes() == b"existing bytes"
    with pytest.raises(ValueError, match="manifest"):
        entry.export(tmp_path / "stale.zip", expected_manifest_digest="0" * 64)


def test_crash_listing_does_not_infer_cleanup_from_age_or_existing_evidence(tmp_path):
    store, entry = allocate(tmp_path)
    write_evidence(entry)
    rows = store.list_attempts()
    assert len(rows) == 1 and rows[0]["key"] == entry.key
    assert rows[0]["manifest"]["cleanup"]["status"] == "unknown"
    assert rows[0]["manifest"]["outcome"]["physical_stop"] == "unverified"


def test_server_ack_is_whitelisted_and_must_identify_the_exact_accepted_attempt(tmp_path):
    _, entry = allocate(tmp_path)
    with pytest.raises(ValueError):
        entry.record_server_ack({"accepted": False, "state": "succeeded"})
    with pytest.raises(ValueError):
        entry.record_server_ack({"accepted": True, "state": "failed", "attempt_id": "other-attempt"})
    entry.record_server_ack({"accepted": True, "state": "failed", "attempt_id": "attempt-one",
                             "token": SECRET, "result": {"secret": SECRET}})
    manifest = entry.snapshot()["manifest"]
    assert manifest["publication"]["status"] == "server_acknowledged"
    assert manifest["publication"]["server_state"] == "failed"
    assert manifest["outcome"]["verification"] == "unverified"
    assert SECRET not in entry.manifest_path.read_text()


def test_origin_rejects_credentials_and_claim_digest_corruption_before_allocation(tmp_path):
    store = spool(tmp_path)
    for server in ("https://user:password@studio.example", "https://studio.example?token=" + SECRET):
        with pytest.raises(ValueError):
            store.allocate(claim(), server=server, target_id="target-one")
    corrupted = claim()
    corrupted["spec"]["runtime"]["python"] = "changed"
    with pytest.raises(ValueError, match="digest|checksum"):
        store.allocate(corrupted, server="https://studio.example", target_id="target-one")
    assert store.list_attempts() == []


@pytest.mark.parametrize("mutation", ["unknown_private_field", "runtime_secret", "duplicate_inventory"])
def test_export_rejects_mutated_public_manifest_shape(tmp_path, mutation):
    _, entry = allocate(tmp_path)
    write_evidence(entry)
    value = entry.snapshot()["manifest"]
    if mutation == "unknown_private_field":
        value["context"] = {"token": SECRET}
    elif mutation == "runtime_secret":
        value["origin"]["runtime"]["token"] = SECRET
    else:
        value["files"].append(dict(value["files"][0]))
    encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    entry.manifest_path.write_bytes(encoded)
    with pytest.raises(ValueError, match="manifest|runtime|inventory"):
        entry.export(tmp_path / "invalid.zip", expected_manifest_digest=hashlib.sha256(encoded).hexdigest())
    assert not (tmp_path / "invalid.zip").exists()


def test_evidence_change_during_streaming_export_is_detected(tmp_path, monkeypatch):
    _, entry = allocate(tmp_path)
    target = write_evidence(entry, data=b"original bytes")
    snapshot = entry.snapshot()
    original = module._inventory_file
    def mutate(base, name, *, sink=None):
        result = original(base, name, sink=sink)
        if sink is not None:
            target.write_bytes(b"mutated after streamed read")
        return result
    monkeypatch.setattr(module, "_inventory_file", mutate)
    with pytest.raises(ValueError, match="changed"):
        entry.export(tmp_path / "changed.zip", expected_manifest_digest=snapshot["manifest_sha256"])
    assert not (tmp_path / "changed.zip").exists()


@pytest.mark.parametrize("data,count,tail,ends", [(b"", 0, 0, False), (b"not parsed", 0, 10, False),
                                               (b"a\nb\n", 2, 0, True), (b"a\nb", 1, 1, False)])
def test_jsonl_facts_are_exact_byte_facts_without_record_validation(tmp_path, data, count, tail, ends):
    _, entry = allocate(tmp_path)
    write_evidence(entry, data=data)
    facts = entry.snapshot()["files"][0]["jsonl"]
    assert facts == {"newline_count": count, "tail_bytes": tail, "ends_with_newline": ends,
                     "semantic_validation": "not_performed"}


def test_spool_root_and_manifest_updates_use_private_durable_files(tmp_path, monkeypatch):
    calls = []
    original = os.fsync
    def sync(descriptor):
        calls.append(stat.S_IFMT(os.fstat(descriptor).st_mode))
        return original(descriptor)
    monkeypatch.setattr(module.os, "fsync", sync)
    _, entry = allocate(tmp_path)
    write_evidence(entry)
    entry.snapshot()
    assert stat.S_IFREG in calls and stat.S_IFDIR in calls
    assert not list(entry.path.glob(".manifest-*.tmp"))
    insecure = tmp_path / "insecure"
    insecure.mkdir(mode=0o755)
    insecure.chmod(0o755)
    with pytest.raises(ValueError, match="0700"):
        EvidenceSpool(SpoolPolicy(True, insecure))


def test_server_ack_cannot_replace_an_existing_different_terminal_receipt(tmp_path):
    _, entry = allocate(tmp_path)
    entry.record_server_ack({"accepted": True, "state": "failed"})
    with pytest.raises(ValueError, match="acknowledgement|terminal"):
        entry.record_server_ack({"accepted": True, "state": "succeeded"})


def test_operator_root_and_export_parent_aliases_are_normalized_once(tmp_path):
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    alias = tmp_path / "operator-alias"
    alias.symlink_to(canonical, target_is_directory=True)
    store = spool(tmp_path, root=str(alias / "spool"))
    entry = store.allocate(claim(), server="https://studio.example", target_id="target-one")
    assert store.policy.root == canonical / "spool"
    assert entry.execution_root.is_relative_to(canonical)
    write_evidence(entry)
    snapshot = entry.snapshot()
    receipt = entry.export(alias / "recovery.zip", expected_manifest_digest=snapshot["manifest_sha256"])
    assert Path(receipt["path"]) == canonical / "recovery.zip"
    assert (canonical / "recovery.zip").is_file()


def test_operator_final_symlinks_and_traversal_are_rejected(tmp_path):
    actual = tmp_path / "actual-spool"
    actual.mkdir(mode=0o700)
    alias = tmp_path / "final-spool-alias"
    alias.symlink_to(actual, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        spool(tmp_path, root=str(alias))
    with pytest.raises(ValueError, match="traversal"):
        spool(tmp_path, root=str(tmp_path / "directory/../spool"))
    _, entry = allocate(tmp_path)
    write_evidence(entry)
    snapshot = entry.snapshot()
    target = tmp_path / "outside.zip"
    target.write_bytes(b"untouched")
    destination = tmp_path / "final-export-alias.zip"
    destination.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        entry.export(destination, expected_manifest_digest=snapshot["manifest_sha256"])
    assert target.read_bytes() == b"untouched"
    with pytest.raises(ValueError, match="traversal"):
        entry.export(tmp_path / "directory/../traversal.zip", expected_manifest_digest=snapshot["manifest_sha256"])
