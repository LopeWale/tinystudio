"""Portable, verified evidence; importing never executes or binds devices."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy import select

from . import service as svc
from .contracts import canonical, checksum
from .models import LabArtifact, LabAttempt, LabEvent


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
    extended = "artifact_origins" in manifest or "attempt_outcomes" in manifest
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
    origins = {id_: {"kind": "input" if id_ in refs else "attempt_outcome" if id_ in outcome_ids else "attempt_output",
                      "attempt_id": None if id_ in refs else artifact.attempt_id}
               for id_, artifact in artifacts.items()}
    payload["events"] = [{"seq": e.seq, "kind": e.kind, "payload": e.payload, "time": e.time.isoformat()}
                          for e in db.scalars(select(LabEvent).where(LabEvent.run_id == run.id).order_by(LabEvent.seq))]
    manifest = {"schema_version": 1, "kind": "tinystudio.research_bundle", "run": payload,
                "artifacts": [svc.artifact_dict(a) for a in artifacts.values()],
                "artifact_origins": origins, "attempt_outcomes": outcomes}
    declared, _ = _references(manifest)
    paths = {}
    # Materialize every row even when bytes share a digest: each row's recorded
    # size and storage binding must independently match the verified object.
    for artifact in artifacts.values():
        paths[artifact.sha256] = svc.materialize(artifact, directory)
    _outcomes(manifest, declared, paths)
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
                "provenance": {"attempt_attribution": "recorded" if extended else "unknown_legacy_bundle"},
                "resume": "requires compatible checkpoint and explicit submission"}
