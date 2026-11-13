"""Owned infrastructure helpers: configuration and lifecycle, with no live stores."""
from __future__ import annotations

import importlib
import json
import subprocess
from urllib.parse import unquote, urlsplit

import pytest


IMAGE = "fixture/qualification@sha256:" + "a" * 64
RUNTIME = {"os": "Linux", "machine": "fixture-architecture", "python": "3.12.fixture",
           **{name: "fixture-version" for name in (
               "torch", "transformers", "peft", "trl", "bitsandbytes", "accelerate",
               "tokenizers", "safetensors", "datasets")}}


def helpers():
    return importlib.import_module("tests.support.environment")


def environment(monkeypatch, tmp_path, **settings):
    module = helpers()
    for name in ("TINYSTUDIO_TEST_QDRANT_URL", "TINYSTUDIO_TEST_QDRANT_API_KEY",
                 "TINYSTUDIO_TEST_CONTAINER_PROFILE", "TINYSTUDIO_TEST_TEMP_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TINYSTUDIO_TEST_TEMP_DIR", str(tmp_path))
    for name, value in settings.items():
        monkeypatch.setenv(name, str(value))
    return module, module.TestEnvironment.from_environment()


def selected_container(monkeypatch, tmp_path, **changes):
    profile = tmp_path / "container.json"
    profile.write_text(json.dumps({"image": IMAGE, "runtime": RUNTIME, **changes}))
    module, env = environment(monkeypatch, tmp_path, TINYSTUDIO_TEST_CONTAINER_PROFILE=profile)
    return module, env.require_container()


def test_test_environment_does_not_reuse_production_qdrant(monkeypatch, tmp_path):
    monkeypatch.setenv("TINYSTUDIO_QDRANT_URL", "https://production.invalid")
    monkeypatch.setenv("TINYSTUDIO_QDRANT_API_KEY", "production-secret")
    module, env = environment(monkeypatch, tmp_path)
    assert env.qdrant_url is None and env.qdrant_api_key is None
    assert env.temporary_parent == tmp_path
    with pytest.raises(ValueError, match="TINYSTUDIO_TEST_QDRANT_URL"):
        with env.qdrant_collection():
            pytest.fail("an unselected server must never be contacted")
    with pytest.raises(ValueError, match="TINYSTUDIO_TEST_CONTAINER_PROFILE"):
        env.require_container()


def test_test_environment_uses_standard_temporary_directory(monkeypatch, tmp_path):
    module, _ = environment(monkeypatch, tmp_path)
    monkeypatch.delenv("TINYSTUDIO_TEST_TEMP_DIR")
    monkeypatch.setattr(module.tempfile, "gettempdir", lambda: str(tmp_path / "standard"))
    assert module.TestEnvironment.from_environment().temporary_parent == tmp_path / "standard"


@pytest.mark.parametrize("changes, error", [
    ({"image": "fixture:latest"}, "immutable"),
    ({"runtime": {"os": "Linux"}}, "runtime"),
    ({"runtime": {**RUNTIME, "torch": 2}}, "runtime"),
    ({"runtme": RUNTIME}, "unknown"),
])
def test_container_profile_rejects_unqualified_or_mistyped_configuration(monkeypatch, tmp_path, changes, error):
    with pytest.raises(ValueError, match=error):
        selected_container(monkeypatch, tmp_path, **changes)


def test_container_runtime_requires_exact_recorded_identity_and_versions(monkeypatch, tmp_path):
    _, selected = selected_container(monkeypatch, tmp_path)
    selected.validate_runtime(dict(RUNTIME))
    for measured in ({**RUNTIME, "machine": "different"}, {**RUNTIME, "torch": "different"},
                     {**RUNTIME, "extra": "unrecorded"}, {k: v for k, v in RUNTIME.items() if k != "trl"}):
        with pytest.raises(ValueError, match="runtime"):
            selected.validate_runtime(measured)
    with pytest.raises(TypeError):
        selected.runtime["torch"] = "changed"


class FakeProcess:
    def __init__(self, *, exited=False, stubborn=False):
        self.exited, self.stubborn = exited, stubborn
        self.events = []

    def poll(self):
        return 1 if self.exited else None

    def terminate(self):
        self.events.append("terminate")

    def kill(self):
        self.events.append("kill")

    def wait(self, timeout):
        self.events.append(("wait", timeout))
        if self.stubborn:
            self.stubborn = False
            raise subprocess.TimeoutExpired("owned-redis", timeout)
        self.exited = True
        return 0


class FakeRedis:
    def __init__(self):
        self.closed = False

    def ping(self):
        return True

    def close(self):
        self.closed = True


def fake_redis(monkeypatch, tmp_path, *, process=None):
    module, env = environment(monkeypatch, tmp_path)
    process, client, calls = process or FakeProcess(), FakeRedis(), []
    monkeypatch.setattr(module.RuntimeTools, "resolve", lambda self, name: "/selected/" + name)
    monkeypatch.setattr(module.subprocess, "Popen", lambda command, **kwargs: calls.append((command, kwargs)) or process)
    monkeypatch.setattr(module.Redis, "from_url", lambda url, **kwargs: calls.append((url, kwargs)) or client)
    return module, env, process, client, calls


def test_owned_redis_uses_selected_binary_and_private_socket_and_cleans_on_body_failure(monkeypatch, tmp_path):
    _, env, process, client, calls = fake_redis(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="body failed"):
        with env.owned_redis(tmp_path) as owned:
            socket = owned.socket
            assert owned.connection is client
            assert socket.parent.parent == tmp_path
            command, options = calls[0]
            assert command[0] == "/selected/redis_server"
            assert command[command.index("--port") + 1] == "0"
            assert command[command.index("--unixsocket") + 1] == str(socket)
            assert command[command.index("--appendonly") + 1] == "no"
            assert calls[1][0] == owned.url
            assert owned.url.startswith("unix://")
            raise RuntimeError("body failed")
    assert client.closed
    assert process.events == ["terminate", ("wait", 5)]
    assert options["stdout"].closed
    assert not socket.parent.exists()


def test_redis_launch_failure_releases_already_acquired_resources(monkeypatch, tmp_path):
    module, env = environment(monkeypatch, tmp_path)
    logs = []
    monkeypatch.setattr(module.RuntimeTools, "resolve", lambda self, name: "/selected/redis")
    def unavailable(command, **kwargs):
        logs.append(kwargs["stdout"])
        raise OSError("controlled launch failure")
    monkeypatch.setattr(module.subprocess, "Popen", unavailable)
    with pytest.raises(OSError, match="controlled launch failure"):
        with env.owned_redis(tmp_path):
            pytest.fail("launch failed")
    assert logs[0].closed
    assert not list(tmp_path.glob("ts-redis-*"))


def test_redis_early_exit_reaps_process_and_closes_client(monkeypatch, tmp_path):
    _, env, process, client, calls = fake_redis(monkeypatch, tmp_path, process=FakeProcess(exited=True))
    with pytest.raises(RuntimeError, match="Redis"):
        with env.owned_redis(tmp_path):
            pytest.fail("Redis exited before readiness")
    assert client.closed and process.events == [("wait", 5)]
    assert calls[0][1]["stdout"].closed


def test_redis_cleanup_escalates_only_its_owned_process(monkeypatch, tmp_path):
    _, env, process, _, _ = fake_redis(monkeypatch, tmp_path, process=FakeProcess(stubborn=True))
    with env.owned_redis(tmp_path):
        pass
    assert process.events == ["terminate", ("wait", 5), "kill", ("wait", 5)]


def test_redis_readiness_deadline_cleans_its_client_and_process(monkeypatch, tmp_path):
    module, env, process, client, calls = fake_redis(monkeypatch, tmp_path)
    monkeypatch.setattr(client, "ping", lambda: False)
    ticks = iter((0, 0, 6))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(module.time, "sleep", lambda duration: None)
    with pytest.raises(RuntimeError, match="did not initialize"):
        with env.owned_redis(tmp_path):
            pytest.fail("Redis never became ready")
    assert client.closed and process.events == ["terminate", ("wait", 5)]
    assert calls[0][1]["stdout"].closed
    assert not list(tmp_path.glob("ts-redis-*"))


def test_redis_socket_url_preserves_configured_directory_characters(monkeypatch, tmp_path):
    directory = tmp_path / "short # socket?"
    directory.mkdir()
    _, env, _, _, _ = fake_redis(monkeypatch, directory)
    with env.owned_redis(directory) as owned:
        assert unquote(urlsplit(owned.url).path) == str(owned.socket)
        assert urlsplit(owned.url).query == "db=0"


class FakeQdrant:
    def __init__(self, **kwargs):
        self.configuration, self.calls, self.collections = kwargs, [], set()
        self.closed = False

    def create_collection(self, name, **kwargs):
        self.calls.append(("create", name, kwargs))
        self.collections.add(name)

    def collection_exists(self, name):
        return name in self.collections

    def delete_collection(self, name):
        self.calls.append(("delete", name))
        self.collections.remove(name)

    def close(self):
        self.closed = True


def test_qdrant_collection_uses_explicit_server_and_removes_only_owned_collection(monkeypatch, tmp_path):
    module, env = environment(monkeypatch, tmp_path, TINYSTUDIO_TEST_QDRANT_URL="https://test.invalid",
                              TINYSTUDIO_TEST_QDRANT_API_KEY="test-key")
    clients = []
    monkeypatch.setattr(module, "QdrantClient", lambda **kwargs: clients.append(FakeQdrant(**kwargs)) or clients[-1])
    with pytest.raises(RuntimeError, match="body failed"):
        with env.qdrant_collection(prefix="owned_", dimension=2) as owned:
            client, name = owned.client, owned.name
            assert name.startswith("owned_")
            assert client.configuration == {"url": "https://test.invalid", "api_key": "test-key", "timeout": 10}
            client.collections.add("foreign")
            raise RuntimeError("body failed")
    assert client.collections == {"foreign"} and client.closed
    assert client.calls[-1] == ("delete", name)


def test_qdrant_creation_failure_still_closes_client_and_reconciles_own_collection(monkeypatch, tmp_path):
    module, env = environment(monkeypatch, tmp_path, TINYSTUDIO_TEST_QDRANT_URL="https://test.invalid")
    client = FakeQdrant()
    def uncertain(name, **kwargs):
        client.collections.add(name)
        raise RuntimeError("controlled uncertain creation")
    monkeypatch.setattr(client, "create_collection", uncertain)
    monkeypatch.setattr(module, "QdrantClient", lambda **kwargs: client)
    with pytest.raises(RuntimeError, match="controlled uncertain creation"):
        with env.qdrant_collection():
            pytest.fail("creation failed")
    assert client.closed and client.collections == set()


@pytest.mark.parametrize("url", [":memory:", "path:/tmp/local", "not-a-server"])
def test_server_qualification_does_not_substitute_qdrant_local_mode(monkeypatch, tmp_path, url):
    _, env = environment(monkeypatch, tmp_path, TINYSTUDIO_TEST_QDRANT_URL=url)
    with pytest.raises(ValueError, match="server"):
        with env.qdrant_collection():
            pytest.fail("requires a real selected server")


class ContainerEngine:
    """A retained container even after a successful/failed client attachment."""

    identity = "b" * 64

    def __init__(self, *, failure=None, returncode=0, foreign=False, foreign_image=False, absent=False,
                 removal_fails=False, wrong_absence=False):
        self.failure, self.returncode, self.foreign = failure, returncode, foreign
        self.foreign_image = foreign_image
        self.absent, self.removal_fails, self.wrong_absence = absent, removal_fails, wrong_absence
        self.commands, self.live = [], False

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if command[1] == "image":
            return subprocess.CompletedProcess(command, 0, json.dumps([IMAGE]), "")
        if command[1] == "run":
            self.name = command[command.index("--name") + 1]
            self.label = command[command.index("--label") + 1] if "--label" in command else ""
            self.live = not self.absent
            if self.failure:
                raise self.failure
            return subprocess.CompletedProcess(command, self.returncode, json.dumps(RUNTIME), "controlled attachment failure")
        if command[1] == "rm":
            assert command[-1] == self.identity, "cleanup must remove an immutable ID, not a name"
            assert not self.foreign, "foreign ownership must never reach removal"
            if not self.removal_fails:
                self.live = False
            return subprocess.CompletedProcess(command, 1 if self.removal_fails else 0, "", "controlled removal failure")
        assert command[1] == "inspect"
        if not self.live:
            selector = "foreign-identity" if self.wrong_absence else command[-1]
            return subprocess.CompletedProcess(command, 1, "", "Error: No such object: " + selector)
        label_name, _, owner = self.label.partition("=")
        value = [{"Id": self.identity, "Config": {"Image": "foreign/image" if self.foreign_image else IMAGE,
                  "Labels": {label_name: "foreign" if self.foreign else owner}}}]
        return subprocess.CompletedProcess(command, 0, json.dumps(value), "")


def container_engine(monkeypatch, tmp_path, **settings):
    module, selected = selected_container(monkeypatch, tmp_path)
    engine = ContainerEngine(**settings)
    monkeypatch.setattr(module.RuntimeTools, "resolve", lambda self, name: "/selected/docker")
    monkeypatch.setattr(module.subprocess, "run", engine)
    return selected, engine


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("owned", 1), KeyboardInterrupt()])
def test_container_interruption_removes_only_verified_owned_identity(monkeypatch, tmp_path, failure):
    selected, engine = container_engine(monkeypatch, tmp_path, failure=failure)
    with pytest.raises(type(failure)):
        selected.python("pass", timeout=1)
    command = engine.commands[0]
    owned_name = command[command.index("--name") + 1]
    assert owned_name.startswith("tinystudio-d-proof-")
    assert engine.label == "tinystudio.test.owner=" + owned_name.removeprefix("tinystudio-d-proof-")
    assert engine.commands[1:] == [
        ["/selected/docker", "inspect", owned_name],
        ["/selected/docker", "rm", "--force", engine.identity],
        ["/selected/docker", "inspect", engine.identity]]
    assert "--pull=never" in command
    assert command[command.index("--network") + 1] == "none"
    assert not engine.live


def test_nonzero_container_client_exit_still_cleans_and_confirms_owned_absence(monkeypatch, tmp_path):
    selected, engine = container_engine(monkeypatch, tmp_path, returncode=125)
    with pytest.raises(AssertionError, match="attachment failure"):
        selected.python("pass")
    assert not engine.live
    assert engine.commands[-1] == ["/selected/docker", "inspect", engine.identity]


@pytest.mark.parametrize("failure", [None, subprocess.TimeoutExpired("owned", 1)])
def test_ambiguous_container_ownership_never_deletes_a_foreign_resource(monkeypatch, tmp_path, failure):
    selected, engine = container_engine(monkeypatch, tmp_path, foreign=True, failure=failure)
    with pytest.raises(RuntimeError, match="ownership"):
        selected.python("pass")
    assert engine.live
    assert not any(command[1] == "rm" for command in engine.commands)


def test_container_image_mismatch_cannot_authorize_cleanup_even_with_matching_owner(monkeypatch, tmp_path):
    selected, engine = container_engine(monkeypatch, tmp_path, foreign_image=True)
    with pytest.raises(RuntimeError, match="ownership"):
        selected.python("pass")
    assert engine.live
    assert not any(command[1] == "rm" for command in engine.commands)


def test_uncertain_container_cleanup_is_not_reported_as_success(monkeypatch, tmp_path):
    selected, engine = container_engine(monkeypatch, tmp_path, removal_fails=True)
    with pytest.raises(RuntimeError, match="cleanup"):
        selected.python("pass")
    assert engine.live


def test_container_absence_error_must_identify_its_exact_owned_resource(monkeypatch, tmp_path):
    selected, _ = container_engine(monkeypatch, tmp_path, wrong_absence=True)
    with pytest.raises(RuntimeError, match="absence"):
        selected.python("pass")


def test_cancelled_container_creation_absent_at_owned_name_does_not_remove_anything(monkeypatch, tmp_path):
    selected, engine = container_engine(monkeypatch, tmp_path, absent=True,
                                        failure=subprocess.TimeoutExpired("owned", 1))
    with pytest.raises(subprocess.TimeoutExpired):
        selected.python("pass")
    assert not any(command[1] == "rm" for command in engine.commands)


def test_container_measurement_checks_selected_digest_and_exact_runtime(monkeypatch, tmp_path):
    selected, engine = container_engine(monkeypatch, tmp_path)
    assert selected.measure_runtime() == RUNTIME
    assert engine.commands[0][:4] == ["/selected/docker", "image", "inspect", IMAGE]
    assert engine.commands[1][-4:] == [IMAGE, "python", "-c", selected.runtime_script]
    assert not engine.live


def test_foreign_image_identity_never_starts_a_container(monkeypatch, tmp_path):
    module, selected = selected_container(monkeypatch, tmp_path)
    commands = []
    monkeypatch.setattr(module.RuntimeTools, "resolve", lambda self, name: "/selected/docker")
    monkeypatch.setattr(module.subprocess, "run", lambda command, **kwargs:
                        commands.append(command) or subprocess.CompletedProcess(command, 0, "[]", ""))
    with pytest.raises(ValueError, match="digest"):
        selected.measure_runtime()
    assert len(commands) == 1
