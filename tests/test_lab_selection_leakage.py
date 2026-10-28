"""Selection provenance is declared evidence, never a claim about arbitrary code."""
from copy import deepcopy

import pytest

from tinystudio.lab.leakage import selection_integrity


DIGEST = "a" * 64


def declared(kind="reward", **metadata):
    ref = {"artifact_id": "source", **metadata}
    return {"inputs": [{"artifact_id": "source", "sha256": DIGEST, "visibility": "model", "role": "source"}],
            "rewards": {"evidence": [ref]} if kind == "reward" else {},
            "scenarios": [{"selection_evidence": [ref]}] if kind == "scenario_selection" else []}


def manifest(**metadata):
    return [{"id": "source", "sha256": DIGEST, "visibility": "model", "role": "source", **metadata}]


@pytest.mark.parametrize("kind", ["reward", "scenario_selection"])
@pytest.mark.parametrize("label", ["eval", "validation", "dev", "testing", "held-out", "final_test", "ground-truth"])
def test_declared_protected_selection_is_rejected(kind, label):
    report = selection_integrity(declared(kind, partition=label), manifest())
    assert report["passed"] is False
    assert report["checks"][0]["code"] == "leakage." + kind


@pytest.mark.parametrize("layer", ["binding", "artifact"])
@pytest.mark.parametrize("metadata", [{"visibility": "evaluation"}, {"role": "heldout"}, {"role": "ground_truth"}])
def test_training_row_cannot_weaken_binding_or_artifact(layer, metadata):
    definition = declared(partition="train")
    artifacts = manifest()
    (definition["inputs"][0] if layer == "binding" else artifacts[0]).update(metadata)
    assert selection_integrity(definition, artifacts)["passed"] is False


@pytest.mark.parametrize("ref", [{"artifact_id": "unknown"}, {"artifact_id": "source", "sha256": "b" * 64},
                                  {"partition": "train"}, {"artifact_id": "source", "partition": "future"},
                                  {"artifact_id": "source", "partition": "train", "split": "test"},
                                  {"artifact_id": "source", "visibility": "hidden"}])
def test_bad_identity_or_labels_cannot_be_overridden(ref):
    definition = declared()
    definition["rewards"]["evidence"] = [ref]
    definition["research_overrides"] = [{"kind": "leakage.use_heldout_reward", "artifact_id": ref.get("artifact_id"), "reason": "research"}]
    report = selection_integrity(definition, manifest())
    assert report["passed"] is False
    assert report["qualification"] != "research_only"


def test_known_reference_requires_trusted_matching_manifest():
    for artifacts in ([], manifest(sha256="b" * 64)):
        assert selection_integrity(declared(), artifacts)["passed"] is False


@pytest.mark.parametrize("kind", ["reward", "scenario_selection"])
def test_exact_scoped_override_is_frozen_and_does_not_claim_qualification(kind):
    definition = declared(kind, partition="test")
    override = {"kind": "leakage.use_heldout_" + kind, "artifact_id": "source", "reason": "bounded research comparison"}
    definition["research_overrides"] = [override]
    artifacts = manifest()
    report = selection_integrity(definition, artifacts)
    frozen = deepcopy(report)
    assert report["passed"] is True
    assert report["qualification"] == "research_only"
    assert report["dependencies"][0]["override"] == override
    assert report["dependencies"][0]["artifact"]["sha256"] == DIGEST
    override["reason"] = "changed"
    artifacts[0]["visibility"] = "truth"
    assert report == frozen


@pytest.mark.parametrize("override", [{"kind": "input.visibility_override", "artifact_id": "source", "reason": "research"},
                                      {"kind": "leakage.use_heldout_reward", "artifact_id": "other", "reason": "research"},
                                      {"kind": "leakage.use_heldout_reward", "artifact_id": "source", "reason": " "}])
def test_unrelated_or_empty_override_does_not_authorize_selection(override):
    definition = declared(partition="test")
    definition["research_overrides"] = [override]
    assert selection_integrity(definition, manifest())["passed"] is False


def test_absent_and_empty_declarations_are_unverified_and_do_not_block_analytic_work():
    definition = {"rewards": {"formula": "1 if success else 0"}, "scenarios": [{"name": "synthetic", "selected_partition": "test"}]}
    report = selection_integrity(definition, [])
    assert report["passed"] is True
    assert report["declarations"][0]["status"] == "not_declared"
    assert report["declarations"][1]["status"] == "not_declared"
    assert report["qualification"] == "unverified"
    definition["rewards"]["evidence"] = []
    assert selection_integrity(definition, [])["declarations"][0]["status"] == "declared_only"


@pytest.mark.parametrize("evidence", [None, {}, "source", [None], ["source"], [{"artifact_id": "source", "partition_typo": "test"}]])
def test_malformed_explicit_contract_fails(evidence):
    definition = declared()
    definition["rewards"]["evidence"] = evidence
    assert selection_integrity(definition, manifest())["passed"] is False


def test_safe_declared_selection_retains_canonical_partition_without_claiming_dataflow_proof():
    report = selection_integrity(declared(partition="training"), manifest())
    assert report["passed"] is True
    assert report["qualification"] == "declared_only"
    assert report["dependencies"][0]["partition"] == "train"
    assert "contents" in report["limitations"][0]


# Existing fixture owns a new UUID PostgreSQL database and drops only that DB.
from test_lab_foundation import database  # noqa: E402,F401


@pytest.fixture
def registered(database, tmp_path, monkeypatch):
    import uuid
    from tinystudio.db.models import Project
    from tinystudio.lab import service as svc
    from tinystudio.storage import ArtifactStorage

    monkeypatch.setattr(svc, "storage", lambda: ArtifactStorage(root=tmp_path / "objects"))
    with database.begin() as db:
        project = Project(name="Selection provenance", slug="selection-" + uuid.uuid4().hex)
        other = Project(name="Other", slug="selection-other-" + uuid.uuid4().hex)
        db.add_all([project, other])
        db.flush()
        project_id, other_id = project.id, other.id
    return database, project_id, other_id, tmp_path


def registered_definition(db, project, root, *, kind="reward", override=False, **metadata):
    from tinystudio.lab import service as svc
    path = root / "source.json"
    path.write_text('{"rows": [{"partition": "test", "value": 1}]}')
    artifact = svc.store_artifact(db, project, path, "source.json", "application/json", **metadata)
    ref = {"artifact_id": artifact.id, "sha256": artifact.sha256, "visibility": artifact.visibility}
    definition = {"name": "Selection", "engine": "synthetic.signal", "execution": {"profile": "trusted_native"}, "inputs": [ref],
                  "rewards": {"evidence": [{"artifact_id": artifact.id}]} if kind == "reward" else {},
                  "scenarios": [{"selection_evidence": [{"artifact_id": artifact.id}]}] if kind == "scenario_selection" else []}
    if override:
        definition["research_overrides"] = [{"kind": "leakage.use_heldout_" + kind, "artifact_id": artifact.id, "reason": "controlled research"}]
    return definition, artifact


@pytest.mark.parametrize("kind", ["reward", "scenario_selection"])
def test_registered_preflight_and_submit_enforce_protected_selection(registered, kind):
    from tinystudio.lab import service as svc
    factory, project, _, root = registered
    with factory.begin() as db:
        definition, _ = registered_definition(db, project, root, kind=kind, visibility="evaluation")
        report = svc.preflight(db, project, definition)
        assert report["compatible"] is False
        assert report["integrity"]["passed"] is False
        saved = svc.save_definition(db, project, definition)
        with pytest.raises(svc.LabError, match="held-out/evaluation/truth"):
            svc.submit(db, project, saved["id"], 1, "local", "selection-reject")


def test_registered_scoped_override_freezes_audit_and_retry_preserves_it(registered):
    from tinystudio.lab import service as svc
    from tinystudio.lab.contracts import checksum
    factory, project, _, root = registered
    with factory.begin() as db:
        definition, artifact = registered_definition(db, project, root, override=True, visibility="truth")
        saved = svc.save_definition(db, project, definition)
        run = svc.submit(db, project, saved["id"], 1, "local", "selection-research")
        frozen_spec, digest = deepcopy(run.spec), run.spec_digest
        assert frozen_spec["integrity"]["qualification"] == "research_only"
        assert frozen_spec["integrity"]["dependencies"][0]["artifact"]["visibility"] == "truth"
        # Authored revision changes and later registry metadata cannot rewrite an admitted run.
        svc.save_definition(db, project, {**definition, "rewards": {}}, id_=saved["id"], expected=1)
        artifact.role = "changed-after-admission"
        run.state = "failed"
        # Terminal execution has consumed its original queued dispatch.
        from tinystudio.lab.models import LabDispatch
        db.delete(db.get(LabDispatch, run.id))
        db.flush()
        retried = svc.retry(db, project, run.id)
        assert retried.spec == frozen_spec
        assert retried.spec_digest == digest == checksum(frozen_spec)
        again = svc.submit(db, project, saved["id"], 1, "local", "selection-research")
        assert again.id == run.id
        assert again.spec == frozen_spec


def test_registered_unresolved_cross_project_and_bad_hash_evidence_cannot_be_waived(registered):
    from tinystudio.lab import service as svc
    factory, project, other, root = registered
    with factory.begin() as db:
        definition, artifact = registered_definition(db, other, root, override=True, visibility="evaluation")
        assert svc.preflight(db, project, definition)["compatible"] is False
        saved = svc.save_definition(db, project, definition)
        with pytest.raises(svc.LabError, match="artifact not found"):
            svc.submit(db, project, saved["id"], 1, "local", "wrong-project")
        definition["inputs"][0]["sha256"] = "f" * 64
        assert svc.preflight(db, other, definition)["compatible"] is False
        saved = svc.save_definition(db, other, definition)
        with pytest.raises(svc.LabError, match="input digest"):
            svc.submit(db, other, saved["id"], 1, "local", "wrong-hash")


def test_existing_raw_spec_retry_remains_byte_for_byte_unchanged_without_integrity(registered):
    from tinystudio.lab import service as svc
    from tinystudio.lab.contracts import checksum
    factory, project, _, root = registered
    with factory.begin() as db:
        definition, _ = registered_definition(db, project, root)
        saved = svc.save_definition(db, project, definition)
        run = svc.submit(db, project, saved["id"], 1, "local", "legacy-selection")
        raw = deepcopy(run.spec)
        raw.pop("integrity")
        run.spec, run.spec_digest, run.state = raw, checksum(raw), "failed"
        from tinystudio.lab.models import LabDispatch
        db.delete(db.get(LabDispatch, run.id))
        db.flush()
        original = deepcopy(raw)
        retried = svc.retry(db, project, run.id)
        assert retried.spec == original
        assert "integrity" not in retried.spec
        assert retried.spec_digest == checksum(original)
