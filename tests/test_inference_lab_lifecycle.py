"""Unit lifecycle qualification: fake DB/weights boundaries, real engine/threading."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, replace
from threading import Event
from types import SimpleNamespace

import pytest

from tinystudio.core.project import ProjectError
from tinystudio.inference import engine as inference


ARTIFACT = "12345678901234567890123456789012"  # all digits is still a valid artifact ID
MODEL = f"lab:{ARTIFACT}"


@dataclass(frozen=True)
class Source:
    project_id: str = "project-a"
    artifact_id: str = ARTIFACT
    sha256: str = "a" * 64
    size: int = 10
    storage_key: str = "retained/model.tar"


@pytest.fixture
def harness(monkeypatch):
    state = SimpleNamespace(source=Source(), resolves=[], runners=[], construct=None, project_ids={"a": "project-a", "b": "project-b"})

    @contextmanager
    def fake_session():
        yield object()

    def resolve(db, project, model_id):
        state.resolves.append((project.slug if project else None, model_id))
        if model_id.startswith("lab:"):
            if project is None or state.source is None:
                raise ProjectError("accepted laboratory binding is unavailable")
            if project.id != state.source.project_id:
                raise ProjectError("laboratory project binding changed")
        return SimpleNamespace(model_id=model_id, retained_source=state.source if model_id.startswith("lab:") else None)

    class Runner:
        def __init__(self, resolved):
            self.resolved = resolved
            self.retired = False
            state.runners.append(self)
            if state.construct:
                state.construct()

        def unload(self):
            self.retired = True

    monkeypatch.setattr(inference, "session", fake_session)
    monkeypatch.setattr(inference.models_svc, "resolve", resolve)
    monkeypatch.setattr(inference.projects_svc, "get_project", lambda db, slug: SimpleNamespace(id=state.project_ids[slug], slug=slug))
    monkeypatch.setattr(inference, "ModelRunner", Runner)
    state.engine = inference.LocalEngine()
    return state


def test_laboratory_identity_is_scoped_and_all_digit_ids_are_valid(harness):
    eng = harness.engine
    assert eng.key("a", MODEL) == f"a/{MODEL}"
    assert eng.key("a", f"a/{MODEL}") == f"a/{MODEL}"
    assert eng.load(MODEL, "a") is eng.load(f"a/{MODEL}", "a")
    assert len(harness.runners) == 1
    assert len(harness.resolves) == 3  # initial, publication, cached binding


@pytest.mark.parametrize("action", ["load", "unload", "key"])
def test_qualified_scope_mismatch_rejected_before_any_resolution(harness, action):
    eng = harness.engine
    runner = eng.load(MODEL, "a")
    before = len(harness.resolves)
    with pytest.raises(ProjectError):
        getattr(eng, action)(f"a/{MODEL}", "b") if action != "key" else eng.key("b", f"a/{MODEL}")
    assert len(harness.resolves) == before
    assert eng.loaded[f"a/{MODEL}"] is runner
    assert not runner.retired


def test_qualified_laboratory_id_requires_explicit_matching_project(harness):
    with pytest.raises(ProjectError):
        harness.engine.load(f"a/{MODEL}")
    assert harness.resolves == []
    assert harness.runners == []


@pytest.mark.parametrize("bad_id", ["lab:bad", "lab:" + "A" * 32, "lab:" + "1" * 31, "a/lab:bad"])
def test_malformed_reserved_ids_never_reach_resolver(harness, bad_id):
    with pytest.raises(ProjectError):
        harness.engine.load(bad_id, "a")
    assert harness.resolves == []


def test_cache_hit_revalidates_and_retires_revoked_binding(harness):
    runner = harness.engine.load(MODEL, "a")
    harness.source = None
    with pytest.raises(ProjectError):
        harness.engine.load(MODEL, "a")
    assert runner.retired
    assert harness.engine.loaded == {}


@pytest.mark.parametrize("change", [{"sha256": "b" * 64}, {"storage_key": "replacement.tar"}, {"project_id": "replacement-project"}])
def test_cache_hit_rejects_changed_descriptor_including_project_identity(harness, change):
    runner = harness.engine.load(MODEL, "a")
    harness.source = replace(harness.source, **change)
    if "project_id" in change:
        harness.project_ids["a"] = change["project_id"]
    with pytest.raises(ProjectError):
        harness.engine.load(MODEL, "a")
    assert runner.retired
    assert harness.engine.loaded == {}


def test_listings_revalidate_and_remove_revoked_laboratory_entry(harness):
    runner = harness.engine.load(MODEL, "a")
    assert harness.engine.loaded_ids("a") == {MODEL}
    assert harness.engine.loaded_ids("b") == set()
    assert harness.engine.loaded_ids() == set()
    harness.source = None
    assert harness.engine.loaded_ids("a") == set()
    assert runner.retired
    assert harness.engine.loaded == {}


def observe_next_operation(eng):
    """Schedule a queued request deterministically; this does not replace engine behavior."""
    queued = Event()
    identity = eng._identities[f"a/{MODEL}"]
    original = identity.operation_lock

    class ObservedLock:
        def __enter__(self):
            queued.set()
            original.acquire()

        def __exit__(self, *args):
            original.release()

    identity.operation_lock = ObservedLock()
    return queued


def test_concurrent_identical_loads_construct_once(harness):
    entered, release = Event(), Event()

    def construct():
        entered.set()
        assert release.wait(5)

    harness.construct = construct
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(harness.engine.load, MODEL, "a")
        assert entered.wait(5)
        queued = observe_next_operation(harness.engine)
        second = pool.submit(harness.engine.load, MODEL, "a")
        assert queued.wait(5)
        release.set()
        assert first.result(5) is second.result(5)
    assert len(harness.runners) == 1


def test_unload_fences_in_progress_construction_and_allows_new_request(harness):
    entered, release = Event(), Event()

    def construct():
        entered.set()
        assert release.wait(5)

    harness.construct = construct
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(harness.engine.load, MODEL, "a")
        assert entered.wait(5)
        harness.engine.unload(MODEL, "a")
        release.set()
        with pytest.raises(ProjectError):
            pending.result(5)
    assert harness.engine.loaded == {}
    assert harness.runners[0].retired
    harness.construct = None
    assert harness.engine.load(MODEL, "a") is harness.runners[1]


def test_unload_also_cancels_load_queued_before_unload(harness):
    first_entered, release_first = Event(), Event()

    def construct():
        first_entered.set()
        assert release_first.wait(5)

    harness.construct = construct
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(harness.engine.load, MODEL, "a")
        assert first_entered.wait(5)
        queued = observe_next_operation(harness.engine)
        second = pool.submit(harness.engine.load, MODEL, "a")
        assert queued.wait(5)
        harness.engine.unload(MODEL, "a")
        release_first.set()
        for result in (first, second):
            with pytest.raises(ProjectError):
                result.result(5)
    assert len(harness.runners) == 1
    assert harness.runners[0].retired
    assert harness.engine.loaded == {}


@pytest.mark.parametrize("change", [None, Source(sha256="b" * 64)])
def test_binding_revoked_or_changed_during_construction_cannot_publish(harness, change):
    entered, release = Event(), Event()

    def construct():
        entered.set()
        assert release.wait(5)

    harness.construct = construct
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(harness.engine.load, MODEL, "a")
        assert entered.wait(5)
        harness.source = change
        release.set()
        with pytest.raises(ProjectError):
            pending.result(5)
    assert harness.runners[0].retired
    assert harness.engine.loaded == {}


def test_hub_cache_and_legacy_project_ids_remain_usable(harness):
    eng = harness.engine
    hub = eng.load("org/model", "a")
    assert eng.load("org/model", "b") is hub
    assert eng.key("a", "job/3") == "a/job/3"
    assert eng.key("a", "ft-job-3") == "a/ft-job-3"
    assert eng.load("job/3", "a") is eng.load("a/job/3", "a")
    assert eng.loaded_ids("b") == {"org/model"}
    eng.unload("org/model", "b")
    assert hub.retired


def test_bare_id_in_other_project_cannot_reuse_or_unload_cached_runner(harness):
    eng = harness.engine
    runner = eng.load(MODEL, "a")
    with pytest.raises(ProjectError):
        eng.load(MODEL, "b")
    eng.unload(MODEL, "b")
    assert not runner.retired
    assert eng.loaded[f"a/{MODEL}"] is runner
    assert len(harness.runners) == 1


def test_retirement_does_not_hold_engine_dictionary_or_identity_state_lock(harness):
    eng = harness.engine
    runner = eng.load(MODEL, "a")
    retiring, release = Event(), Event()

    def unload():
        retiring.set()
        assert release.wait(5)
        runner.retired = True

    runner.unload = unload
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(eng.unload, MODEL, "a")
        assert retiring.wait(5)
        try:
            assert eng.loaded == {}
            # An unrelated identity remains usable while retirement waits for generation.
            unrelated = pool.submit(eng.load, "org/model", "a").result(5)
            assert not unrelated.retired
        finally:
            release.set()
        pending.result(5)


def test_models_listing_hides_other_projects_and_revoked_laboratory_entry(harness, monkeypatch):
    monkeypatch.setattr(inference.models_svc, "deployable_ids", lambda db, project: [MODEL] if harness.source is not None and project.id == harness.source.project_id else [])
    eng = harness.engine
    runner = eng.load(MODEL, "a")
    assert eng.models("a", []) == [{"id": MODEL, "object": "model", "owned_by": "tinystudio", "status": "up"}]
    assert eng.models("b", []) == []
    assert eng.models(None, []) == []
    harness.source = None
    assert eng.models("a", []) == []
    assert runner.retired


@pytest.mark.parametrize("value, project, expected", [
    ("home/job/3", None, ("home", "job/3")),
    ("home/job/3@40", None, ("home", "job/3@40")),
    ("home/ft-job-3", None, ("home", "ft-job-3")),
    ("HuggingFaceTB/SmolLM2-135M-Instruct", "home", ("home", "HuggingFaceTB/SmolLM2-135M-Instruct")),
    ("job/3", "home", ("home", "job/3")),
])
def test_legacy_split_contract_remains_compatible(value, project, expected):
    assert inference.split_model_id(value, project) == expected


@pytest.mark.parametrize("model", ["a/job/3", "a/ft-job-3"])
def test_qualified_legacy_identity_cannot_override_supplied_project(harness, model):
    with pytest.raises(ProjectError):
        harness.engine.load(model, "b")
    with pytest.raises(ProjectError):
        harness.engine.unload(model, "b")
    assert harness.resolves == []


def test_constructor_failure_leaves_identity_retryable(harness):
    def fail():
        raise OSError("weights unavailable")

    harness.construct = fail
    with pytest.raises(ProjectError, match="weights unavailable"):
        harness.engine.load(MODEL, "a")
    assert harness.engine.loaded == {}
    harness.construct = None
    assert harness.engine.load(MODEL, "a") is harness.runners[-1]
