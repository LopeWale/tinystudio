"""Portable, verified evidence; importing never executes or binds devices."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy import select

from . import service as svc
from .contracts import canonical, checksum
from .models import LabArtifact, LabAttempt, LabEvent


def _recovery_check(condition, message="inconsistent recovered evidence association"):
    if not condition:
        raise svc.LabError(message, 409)


def _recovery_events(manifest):
    from .recovered_evidence import EVENT_KIND
    events = manifest["run"].get("events", [])
    _recovery_check(isinstance(events, list) and all(isinstance(item, dict) for item in events),
                    "invalid retained recovery event history")
    return [item for item in events if item.get("kind") == EVENT_KIND]


def _recovery_associations(manifest, declared):
    """Anchor every portable recovery association to exactly one retained event."""
    from .recovered_evidence import CLASSIFICATION, validate_recovery_path
    events = _recovery_events(manifest)
    associations = manifest.get("recovered_evidence", [])
    _recovery_check(not events or "recovered_evidence" in manifest,
                    "recovery events require their complete recovered evidence associations")
    _recovery_check(isinstance(associations, list), "invalid recovered evidence associations")
    if events or "recovered_evidence" in manifest:
        sequences = [item.get("seq") for item in manifest["run"].get("events", [])]
        _recovery_check(all(type(value) is int and value > 0 for value in sequences)
                        and len(set(sequences)) == len(sequences), "recovery event sequence collides with retained history")
    event_map = {}
    for event in events:
        sequence = event.get("seq")
        _recovery_check(type(sequence) is int and sequence > 0 and sequence not in event_map,
                        "duplicate or invalid recovery event sequence")
        event_map[sequence] = event
    attempts = {item["id"] for item in manifest["run"]["attempts"]}
    origins = manifest.get("artifact_origins", {})
    seen, receipts, keys, referenced = set(), set(), set(), set()
    for association in associations:
        _recovery_check(isinstance(association, dict) and set(association) == {
            "event_seq", "receipt_artifact_id", "receipt_sha256", "source_attempt_id",
            "manifest_artifact_id", "manifest_sha256", "files"}, "invalid recovery association shape")
        _recovery_check(all(isinstance(association[key], str) and association[key] for key in
                            ("receipt_artifact_id", "receipt_sha256", "source_attempt_id", "manifest_artifact_id", "manifest_sha256")),
                        "invalid recovered evidence identity references")
        sequence = association["event_seq"]
        _recovery_check(type(sequence) is int and sequence in event_map and sequence not in seen,
                        "recovery association lacks its unique retained event")
        seen.add(sequence)
        payload = event_map[sequence].get("payload")
        _recovery_check(isinstance(payload, dict) and set(payload) == {"classification", "idempotency_key",
            "request_digest", "author_id", "source_attempt_id", "source_states", "manifest_artifact_id",
            "manifest_sha256", "receipt"}, "invalid retained recovery event payload")
        _recovery_check(payload["classification"] == CLASSIFICATION and
                        all(isinstance(payload[key], str) and payload[key] for key in
                            ("idempotency_key", "request_digest", "author_id")), "invalid recovery event identity")
        _recovery_check(payload["idempotency_key"] not in keys, "duplicate recovery publication identity")
        keys.add(payload["idempotency_key"])
        source = association["source_attempt_id"]
        _recovery_check(source in attempts and source == payload["source_attempt_id"],
                        "recovery source attempt differs from its event")
        receipt = declared.get(association["receipt_artifact_id"])
        _recovery_check(receipt is not None and receipt == payload["receipt"]
                        and receipt["sha256"] == association["receipt_sha256"]
                        and receipt["role"] == CLASSIFICATION and receipt.get("visibility") == "evaluation",
                        "recovered receipt metadata differs from its event")
        _recovery_check(receipt["id"] not in receipts, "duplicate recovered receipt association")
        receipts.add(receipt["id"])
        _recovery_check(origins.get(receipt["id"], {}).get("attempt_id") is None
                        and origins.get(receipt["id"], {}).get("kind") in {"input", "recovered_evidence"},
                        "recovered receipt must have unassigned attempt origin")
        producer = declared.get(association["manifest_artifact_id"])
        _recovery_check(producer is not None and producer["id"] == payload["manifest_artifact_id"]
                        and producer["sha256"] == payload["manifest_sha256"] == association["manifest_sha256"],
                        "recovery producer manifest differs from its event")
        _recovery_check(origins.get(producer["id"], {}).get("attempt_id") is None
                        and origins.get(producer["id"], {}).get("kind") in {"input", "recovered_evidence"},
                        "recovery producer manifest must have unassigned attempt origin")
        referenced.update((receipt["id"], producer["id"]))
        _recovery_check(isinstance(association["files"], list), "invalid recovered file associations")
        names = set()
        for ref in association["files"]:
            _recovery_check(isinstance(ref, dict) and set(ref) == {"name", "artifact_id", "sha256", "size"},
                            "invalid recovered file association")
            _recovery_check(all(isinstance(ref[key], str) and ref[key] for key in ("name", "artifact_id", "sha256")),
                            "invalid recovered file identity reference")
            item = declared.get(ref["artifact_id"])
            _recovery_check(item is not None and item["sha256"] == ref["sha256"]
                            and type(ref["size"]) is int and item["size"] == ref["size"],
                            "recovery file association differs from retained metadata")
            validate_recovery_path(ref["name"])
            _recovery_check(ref["name"].startswith("evidence/") and ref["name"] not in names,
                            "duplicate or escaped recovered file association")
            names.add(ref["name"])
            referenced.add(item["id"])
    _recovery_check(seen == set(event_map), "recovery event association is missing")
    _recovery_check({id_ for id_, origin in origins.items() if origin.get("kind") == "recovered_evidence"}
                    <= referenced, "orphan recovered evidence artifact origin")
    return associations


def _receipt(path):
    from .recovered_evidence import FORMAT, CLASSIFICATION, _unique
    def nonfinite(_):
        raise ValueError("nonfinite recovered receipt number")
    try:
        document = json.loads(path.read_bytes(), object_pairs_hook=_unique, parse_constant=nonfinite)
        _recovery_check(isinstance(document, dict) and set(document) == {"format", "classification", "source",
            "source_states", "source_states_observed_at", "manifest", "files", "producer_manifest", "qualification",
            "parser_policy", "digest", "author_id", "request_digest", "idempotency_key", "verified_at", "origin"},
            "invalid retained recovery receipt shape")
        _recovery_check(document["format"] == FORMAT and document["classification"] == CLASSIFICATION,
                        "invalid retained recovery receipt format")
        return document
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, svc.LabError):
            raise
        raise svc.LabError("invalid retained recovery receipt JSON", 409) from exc


def _recoveries(manifest, declared, paths, *, project=None):
    """Verify historical recovery receipts and byte identities before import."""
    from .recovered_evidence import (CLASSIFICATION, EVENT_KIND, RecoveryParserLimits,
                                     _manifest, _byte_facts, _qualification)
    associations = _recovery_associations(manifest, declared)
    events = {item["seq"]: item["payload"] for item in _recovery_events(manifest)}
    run = manifest["run"]
    for association in associations:
        document = _receipt(paths[association["receipt_sha256"]])
        payload = events[association["event_seq"]]
        source = document["source"]
        _recovery_check(isinstance(source, dict) and set(source) == {"project_id", "run_id", "attempt_id", "target_id", "spec_digest"}
                        and source["run_id"] == run["id"] and source["attempt_id"] == association["source_attempt_id"]
                        and source["target_id"] == run["target_id"] and source["spec_digest"] == run["spec_digest"]
                        and isinstance(source["project_id"], str) and source["project_id"]
                        and (project is None or source["project_id"] == project), "recovery receipt source identity differs")
        _recovery_check(all(document[key] == payload[key] for key in ("author_id", "request_digest", "idempotency_key")),
                        "recovery receipt differs from its publication event")
        _recovery_check(document["origin"] == {"kind": CLASSIFICATION, "server_record": {"run_id": run["id"],
            "kind": EVENT_KIND, "idempotency_key": payload["idempotency_key"]}}, "recovery receipt origin differs")
        producer = declared[association["manifest_artifact_id"]]
        _recovery_check(document["manifest"] == producer, "recovery producer metadata reference differs")
        producer_manifest = _manifest(paths[producer["sha256"]], SimpleNamespace(size=producer["size"]), RecoveryParserLimits())
        _recovery_check(document["producer_manifest"] == producer_manifest, "recovery producer manifest bytes differ")
        origin = producer_manifest["origin"]
        expected_origin = {"run_id": run["id"], "attempt_id": source["attempt_id"], "target_id": run["target_id"],
                           "spec_digest": run["spec_digest"], "engine": run["spec"]["definition"]["engine"],
                           "implementation_digest": checksum(run["spec"]["implementation"]), "runtime": run["spec"]["runtime"]}
        _recovery_check(all(origin[key] == value for key, value in expected_origin.items()), "recovery producer source identity differs")
        if producer_manifest["publication"]["status"] == "server_acknowledged":
            ack = producer_manifest["publication"]["ack"]
            _recovery_check(ack["run_id"] == run["id"] and ack["attempt_id"] == source["attempt_id"],
                            "recovery producer acknowledgement source differs")
        _recovery_check(document["qualification"] == _qualification(), "recovery receipt claims unsupported qualification")
        _recovery_check(isinstance(document["files"], list), "invalid recovery receipt files")
        producer_files = {item["path"]: item for item in producer_manifest["files"]}
        actual_refs = []
        for entry in document["files"]:
            _recovery_check(isinstance(entry, dict) and set(entry) == {"name", "artifact", "attempt_id", "verified_bytes"},
                            "invalid recovery receipt file shape")
            item = entry["artifact"]
            _recovery_check(isinstance(item, dict) and isinstance(item.get("id"), str) and declared.get(item["id"]) == item
                            and isinstance(entry["name"], str)
                            and (entry["attempt_id"] is None or isinstance(entry["attempt_id"], str)),
                            "recovered receipt file metadata differs from retained evidence")
            attribution = manifest["artifact_origins"][item["id"]]
            _recovery_check(entry["attempt_id"] in {None, source["attempt_id"]}
                            and attribution["attempt_id"] == entry["attempt_id"],
                            "recovery file attempt origin differs from its receipt")
            producer_file = producer_files.get(entry["name"])
            _recovery_check(producer_file is not None and producer_file["sha256"] == item["sha256"]
                            and producer_file["size"] == item["size"], "recovery receipt differs from producer file inventory")
            facts = _byte_facts(paths[item["sha256"]], "jsonl" in producer_file or
                                Path(entry["name"]).suffix.lower() in {".jsonl", ".ndjson"})
            _recovery_check((facts["sha256"], facts["size"]) == (item["sha256"], item["size"])
                            and facts == entry["verified_bytes"] and ("jsonl" not in producer_file or
                            producer_file["jsonl"] == facts["jsonl"]), "recovered receipt byte facts differ")
            actual_refs.append({"name": entry["name"], "artifact_id": item["id"], "sha256": item["sha256"], "size": item["size"]})
        _recovery_check(actual_refs == association["files"] and len(actual_refs) == len(producer_files)
                        and {item["name"] for item in actual_refs} == set(producer_files),
                        "recovered file association does not completely cover its receipt and producer")
        prepared_keys = {"format", "classification", "source", "manifest", "files", "producer_manifest", "qualification", "parser_policy"}
        _recovery_check(checksum({key: document[key] for key in prepared_keys}) == document["digest"],
                        "recovery receipt preview digest differs")
        request = {"source_attempt_id": source["attempt_id"], "target_id": source["target_id"], "spec_digest": source["spec_digest"],
                   "manifest": {"artifact_id": producer["id"], "sha256": producer["sha256"], "size": producer["size"]},
                   "files": actual_refs, "preview_digest": document["digest"], "idempotency_key": document["idempotency_key"]}
        _recovery_check(checksum(request) == document["request_digest"], "recovery receipt request digest differs")


def _collect_recovered(db, project, payload, artifacts, directory):
    """Resolve the committed event receipt first; it names all retained bytes."""
    from .recovered_evidence import CLASSIFICATION, _source
    associations = []
    for event in _recovery_events({"run": payload}):
        source_id = event.get("payload", {}).get("source_attempt_id")
        _recovery_check(isinstance(source_id, str), "invalid recovered source attempt reference")
        _source(db, project, payload["id"], {"source_attempt_id": source_id,
                "target_id": payload["target_id"], "spec_digest": payload["spec_digest"]})
        reference = event.get("payload", {}).get("receipt")
        _recovery_check(isinstance(reference, dict) and isinstance(reference.get("id"), str), "invalid recovered receipt event reference")
        row = svc.get_artifact(db, project, reference["id"])
        _recovery_check(svc.artifact_dict(row) == reference and row.attempt_id is None
                        and row.role == CLASSIFICATION and row.visibility == "evaluation", "recovered receipt metadata differs from committed event")
        artifacts[row.id] = row
        document = _receipt(svc.materialize(row, directory))
        try:
            references = [(document["manifest"], True), *[(item["artifact"], False) for item in document["files"]]]
            for item, producer in references:
                artifact = svc.get_artifact(db, project, item["id"])
                _recovery_check(svc.artifact_dict(artifact) == item and (artifact.attempt_id is None or
                                not producer and artifact.attempt_id == event["payload"]["source_attempt_id"]),
                                "recovered artifact metadata or attempt attribution changed")
                artifacts[artifact.id] = artifact
            associations.append({"event_seq": event["seq"], "receipt_artifact_id": row.id, "receipt_sha256": row.sha256,
                                 "source_attempt_id": event["payload"]["source_attempt_id"],
                                 "manifest_artifact_id": document["manifest"]["id"], "manifest_sha256": document["manifest"]["sha256"],
                                 "files": [{"name": item["name"], "artifact_id": item["artifact"]["id"],
                                            "sha256": item["artifact"]["sha256"], "size": item["artifact"]["size"]} for item in document["files"]]})
        except (KeyError, TypeError) as exc:
            raise svc.LabError("invalid recovered receipt artifact references", 409) from exc
    return associations


def _references(manifest):
    """Validate declared metadata and explicit attribution before storing rows."""
    run = manifest["run"]
    declared = {}
    if not isinstance(manifest.get("artifacts"), list) or len(manifest["artifacts"]) > 9998:
        raise svc.LabError("bundle artifact count exceeds limit")
    sizes = {}
    for item in manifest["artifacts"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] in declared:
            raise svc.LabError("duplicate or invalid bundle artifact identity")
        sha = item.get("sha256")
        if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise svc.LabError("invalid artifact checksum")
        if not isinstance(item.get("size"), int) or isinstance(item["size"], bool) or item["size"] < 0:
            raise svc.LabError("invalid artifact size")
        if sha in sizes and sizes[sha] != item["size"]:
            raise svc.LabError("bundle aliases declare inconsistent object sizes")
        sizes[sha] = item["size"]
        svc.validate_artifact_name(item.get("name"))
        if not all(isinstance(item.get(key), str) for key in ("id", "media_type", "role")):
            raise svc.LabError("invalid artifact metadata")
        declared[item["id"]] = item
    for ref in run["spec"]["definition"]["inputs"]:
        item = declared.get(ref["artifact_id"])
        if item is None or item["sha256"] != ref["sha256"]:
            raise svc.LabError("bundle input reference differs from retained evidence")
    _result_references(run.get("result"), declared)
    for item in run["spec"]["input_manifest"]:
        if declared.get(item["id"]) != item:
            raise svc.LabError("bundle manifest reference differs from retained evidence")
    if _recovery_events(manifest) and "recovered_evidence" not in manifest:
        raise svc.LabError("recovery events require complete recovered evidence associations")
    extended = "artifact_origins" in manifest or "attempt_outcomes" in manifest or "recovered_evidence" in manifest
    if not extended:
        return declared, False
    origins, outcomes = manifest.get("artifact_origins"), manifest.get("attempt_outcomes")
    if not isinstance(origins, dict) or set(origins) != set(declared) or not isinstance(outcomes, list):
        raise svc.LabError("bundle attribution must cover every declared artifact")
    attempts = {item["id"] for item in run["attempts"]}
    if len(attempts) != len(run["attempts"]):
        raise svc.LabError("duplicate bundle attempt identity")
    inputs = {ref["artifact_id"] for ref in run["spec"]["definition"]["inputs"]}
    for id_, origin in origins.items():
        if not isinstance(origin, dict) or set(origin) != {"kind", "attempt_id"}:
            raise svc.LabError("invalid artifact origin")
        kind, attempt = origin["kind"], origin["attempt_id"]
        if kind == "input":
            if id_ not in inputs or attempt is not None:
                raise svc.LabError("input origin differs from submitted inputs")
        elif kind in {"attempt_output", "attempt_outcome"}:
            if id_ in inputs or (attempt not in attempts and not (kind == "attempt_outcome" and attempt is None)):
                raise svc.LabError("artifact origin references an unknown attempt")
        elif kind == "recovered_evidence":
            if id_ in inputs or attempt is not None or "recovered_evidence" not in manifest:
                raise svc.LabError("invalid recovered evidence artifact origin")
        else:
            raise svc.LabError("unsupported artifact origin")
    if any(origins[id_]["kind"] != "input" for id_ in inputs):
        raise svc.LabError("submitted input origin is missing")
    event_refs = {(e["payload"].get("outcome_artifact_id"), e["payload"].get("attempt_id"),
                   e["payload"].get("state"), e["payload"].get("sha256"))
                  for e in run.get("events", []) if e.get("kind") == "server.outcome"}
    seen = set()
    for outcome in outcomes:
        if not isinstance(outcome, dict) or set(outcome) != {"artifact_id", "attempt_id", "state", "sha256"}:
            raise svc.LabError("invalid attempt outcome association")
        id_ = outcome["artifact_id"]
        if id_ in seen or id_ not in declared or outcome["sha256"] != declared[id_]["sha256"]:
            raise svc.LabError("duplicate or inconsistent outcome association")
        if origins[id_] != {"kind": "attempt_outcome", "attempt_id": outcome["attempt_id"]}:
            raise svc.LabError("outcome origin differs from association")
        if declared[id_]["role"] != "attempt_outcome" or (id_, outcome["attempt_id"], outcome["state"], outcome["sha256"]) not in event_refs:
            raise svc.LabError("outcome association lacks its retained terminal event")
        seen.add(id_)
    if seen != {id_ for id_, origin in origins.items() if origin["kind"] == "attempt_outcome"} or event_refs != {
            (item["artifact_id"], item["attempt_id"], item["state"], item["sha256"]) for item in outcomes}:
        raise svc.LabError("outcome association is missing")
    latest_attempt = run["attempts"][-1]["id"] if run["attempts"] else None
    _result_references(run.get("result"), declared, latest_attempt, origins)
    _recovery_associations(manifest, declared)
    return declared, True


def _result_references(result, declared, attempt_id=None, origins=None):
    if result is None:
        return
    if not isinstance(result, dict) or len(canonical(result)) > 1024**2:
        raise svc.LabError("invalid retained result")
    refs = result.get("artifacts", [])
    if not isinstance(refs, list):
        raise svc.LabError("invalid retained result artifacts")
    seen, names = set(), set()
    for item in refs:
        if not isinstance(item, dict) or declared.get(item.get("id")) != item:
            raise svc.LabError("bundle manifest reference differs from retained evidence")
        if item["id"] in seen or item["name"] in names:
            raise svc.LabError("duplicate retained result artifact")
        if origins is not None and origins[item["id"]] != {"kind": "attempt_output", "attempt_id": attempt_id}:
            raise svc.LabError("retained result references another attempt")
        seen.add(item["id"])
        names.add(item["name"])


def _outcomes(manifest, declared, paths):
    for association in manifest["attempt_outcomes"]:
        item = declared[association["artifact_id"]]
        if item["size"] > 1024**2 + 65536:
            raise svc.LabError("retained outcome exceeds limit")
        document = json.loads(paths[item["sha256"]].read_bytes())
        expected = {"schema_version": 1, "kind": "tinystudio.attempt_outcome", "run_id": manifest["run"]["id"],
                    "spec_digest": manifest["run"]["spec_digest"], "attempt_id": association["attempt_id"], "state": association["state"]}
        if not isinstance(document, dict) or any(document.get(key) != value for key, value in expected.items()):
            raise svc.LabError("retained outcome identity differs from association")
        if document["state"] not in svc.TERMINAL or (document.get("error") is not None and
                (not isinstance(document["error"], str) or len(document["error"]) > 8192)):
            raise svc.LabError("invalid retained outcome state or diagnostic")
        if document["attempt_id"] is not None:
            attempt = next(a for a in manifest["run"]["attempts"] if a["id"] == document["attempt_id"])
            if attempt["state"] != document["state"]:
                raise svc.LabError("retained outcome differs from terminal attempt")
        _result_references(document.get("result"), declared, document["attempt_id"], manifest["artifact_origins"])


def export_bundle(db, project: str, run_id: str, directory: Path) -> Path:
    run = svc.get_run(db, project, run_id)
    if run.state not in svc.TERMINAL:
        raise svc.LabError("only terminal runs can be exported", 409)
    payload = svc.run_dict(db, run)
    if checksum(run.spec) != run.spec_digest:
        raise svc.LabError("cannot export a corrupted run specification", 409)
    attempts = list(db.scalars(select(LabAttempt.id).where(LabAttempt.run_id == run.id)))
    refs = {item["artifact_id"] for item in run.spec["definition"]["inputs"]}
    outputs = list(db.scalars(select(LabArtifact).where(LabArtifact.attempt_id.in_(attempts))))
    artifacts = {a.id: a for a in outputs}
    artifacts.update({id_: svc.get_artifact(db, project, id_) for id_ in refs})
    outcomes = []
    for ref in svc.outcome_references(db, run.id):
        row = svc.get_artifact(db, project, ref["outcome_artifact_id"])
        if row.attempt_id != ref["attempt_id"] or row.sha256 != ref["sha256"]:
            raise svc.LabError("retained outcome artifact differs from terminal event", 409)
        artifacts[row.id] = row
        outcomes.append({"artifact_id": row.id, "attempt_id": row.attempt_id, "state": ref["state"], "sha256": row.sha256})
    outcome_ids = {item["artifact_id"] for item in outcomes}
    payload["events"] = [{"seq": e.seq, "kind": e.kind, "payload": e.payload, "time": e.time.isoformat()}
                          for e in db.scalars(select(LabEvent).where(LabEvent.run_id == run.id).order_by(LabEvent.seq))]
    recovered = _collect_recovered(db, project, payload, artifacts, directory)
    origins = {id_: {"kind": "input" if id_ in refs else "attempt_outcome" if id_ in outcome_ids else
                            "recovered_evidence" if artifact.attempt_id is None else "attempt_output",
                      "attempt_id": None if id_ in refs else artifact.attempt_id}
               for id_, artifact in artifacts.items()}
    manifest = {"schema_version": 1, "kind": "tinystudio.research_bundle", "run": payload,
                "artifacts": [svc.artifact_dict(a) for a in artifacts.values()],
                "artifact_origins": origins, "attempt_outcomes": outcomes}
    if recovered:
        manifest["recovered_evidence"] = recovered
    declared, _ = _references(manifest)
    paths = {}
    # Materialize every row even when bytes share a digest: each row's recorded
    # size and storage binding must independently match the verified object.
    for artifact in artifacts.values():
        paths[artifact.sha256] = svc.materialize(artifact, directory)
    _outcomes(manifest, declared, paths)
    _recoveries(manifest, declared, paths, project=project)
    bundle = directory / "research.zip"
    with ZipFile(bundle, "w", compression=ZIP_DEFLATED) as archive:
        for sha, path in paths.items():
            archive.write(path, f"objects/{sha}")
        archive.writestr("manifest.json", canonical(manifest))
        archive.writestr("manifest.sha256", checksum(manifest))
    return bundle


def import_bundle(db, project: str, bundle: Path, directory: Path) -> dict:
    """Restore original evidence and editable draft; attribution may be legacy unknown."""
    with ZipFile(bundle) as archive:
        infos = archive.infolist()
        if len(infos) > 10000 or sum(info.file_size for info in infos) > 10 * 1024**3:
            raise svc.LabError("bundle exceeds import limits")
        if len({i.filename for i in infos}) != len(infos):
            raise svc.LabError("duplicate bundle member")
        for info in infos:
            if info.filename not in {"manifest.json", "manifest.sha256"} and not (
                    info.filename.startswith("objects/") and len(info.filename) == 72
                    and all(c in "0123456789abcdef" for c in info.filename[8:])):
                raise svc.LabError("unexpected bundle member")
        if archive.getinfo("manifest.json").file_size > 16 * 1024**2:
            raise svc.LabError("bundle manifest exceeds limit")
        if archive.getinfo("manifest.sha256").file_size != 64:
            raise svc.LabError("bundle manifest checksum must contain exactly 64 bytes")
        manifest = json.loads(archive.read("manifest.json"))
        if archive.read("manifest.sha256").decode() != checksum(manifest):
            raise svc.LabError("bundle manifest checksum mismatch")
        if manifest.get("schema_version") != 1 or manifest.get("kind") != "tinystudio.research_bundle":
            raise svc.LabError("unsupported bundle version")
        run = manifest["run"]
        if checksum(run["spec"]) != run["spec_digest"]:
            raise svc.LabError("bundle run specification checksum mismatch")
        from .contracts import ResolvedRunSpec
        ResolvedRunSpec.model_validate(run["spec"])
        declared, extended = _references(manifest)
        if "recovered_evidence" in manifest and {item.filename for item in infos if item.filename.startswith("objects/")} != {
                "objects/" + item["sha256"] for item in declared.values()}:
            raise svc.LabError("recovered bundle has missing or orphan object members")
        paths = {}
        from .engine import digest
        for item in manifest["artifacts"]:
            sha = item["sha256"]
            member = f"objects/{sha}"
            if member not in archive.namelist():
                raise svc.LabError("bundle artifact missing")
            path = directory / sha
            if sha not in paths:
                try:
                    with archive.open(member) as source, path.open("wb") as output:
                        shutil.copyfileobj(source, output, 1024**2)
                    if digest(path) != sha:
                        raise svc.LabError("bundle artifact checksum mismatch")
                    paths[sha] = path
                except BaseException:
                    path.unlink(missing_ok=True)
                    raise
            if path.stat().st_size != item["size"]:
                path.unlink(missing_ok=True)
                raise svc.LabError("bundle artifact size mismatch")
        if extended:
            _outcomes(manifest, declared, paths)
            _recoveries(manifest, declared, paths)
        mapping = {}
        for item in manifest["artifacts"]:
            row = svc.store_artifact(db, project, paths[item["sha256"]], item["name"], item["media_type"],
                                     item["role"], visibility=item.get("visibility", "evaluation"))
            mapping[item["id"]] = row.id
        definition = dict(run["spec"]["definition"])
        definition["inputs"] = [{**ref, "artifact_id": mapping[ref["artifact_id"]]} for ref in definition["inputs"]]
        definition["execution"] = {**definition["execution"], "grants": {}}
        original = directory / "original-manifest.json"
        original.write_bytes(canonical(manifest))
        provenance = svc.store_artifact(db, project, original, "original-manifest.json", "application/json", "imported_history")
        return {"definition": svc.save_definition(db, project, definition), "historical_manifest": svc.artifact_dict(provenance),
                "artifact_mapping": mapping, "execution_started": False,
                "provenance": {"attempt_attribution": "recorded" if extended else "unknown_legacy_bundle",
                               **({"recovered_evidence": "historical_operator_supplied"} if manifest.get("recovered_evidence") else {})},
                "resume": "requires compatible checkpoint and explicit submission"}
