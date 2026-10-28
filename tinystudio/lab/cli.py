"""Laboratory CLI uses the same authenticated HTTP contracts as the application."""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import httpx
import typer

from .runner import run as run_runner, validate_url
from .client_paths import path_identifier

app = typer.Typer(no_args_is_help=True, help="Portable experiment authoring, evidence and execution targets.")


def client():
    server = validate_url(os.environ.get("TINYSTUDIO_URL", "http://127.0.0.1:8000"))
    token = os.environ.get("TINYSTUDIO_API_KEY", "")
    if not token:
        raise typer.BadParameter("set TINYSTUDIO_API_KEY to a user API key")
    from tinystudio.integrations.tools import Studio
    return Studio(server, token=token).http


def identifier(value: str, *, maximum: int = 64) -> str:
    try:
        return path_identifier(value, maximum=maximum)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


def path(project: str, suffix: str) -> str:
    return "/api/p/"+identifier(project, maximum=80)+"/lab"+suffix


def call(method: str, project: str, suffix: str, body=None):
    with client() as http:
        response = http.request(method, path(project, suffix), json=body)
        if response.is_error:
            raise typer.BadParameter(f"server returned {response.status_code}: {response.text[:1000]}")
        return response.json()


def show(value):
    typer.echo(json.dumps(value, indent=2))


@app.command("define")
def define(file: Path, project: str = typer.Option(..., "--project", "-p")):
    """Save a versioned definition; does not start execution."""
    show(call("POST", project, "/definitions", {"definition": json.loads(file.read_text())}))


@app.command("submit")
def submit(definition_id: str, revision: int, project: str = typer.Option(..., "--project", "-p"),
           target: str = "local", idempotency_key: str | None = None, not_before: str | None = None):
    """Freeze inputs and submit a durable run to a selected target."""
    body = {"definition_id": definition_id, "revision": revision,
            "target_id": target, "idempotency_key": idempotency_key or uuid.uuid4().hex}
    if not_before is not None:
        body["not_before"] = not_before
    show(call("POST", project, "/runs", body))


@app.command("check")
def check(file: Path, project: str = typer.Option(..., "--project", "-p"), target: str = "local"):
    """Check target and input compatibility without creating or starting a run."""
    show(call("POST", project, "/preflight", {"definition": json.loads(file.read_text()), "target_id": target}))


@app.command("runs")
def runs(project: str = typer.Option(..., "--project", "-p")):
    show(call("GET", project, "/runs"))


@app.command("inspect")
def inspect(run_id: str, project: str = typer.Option(..., "--project", "-p")):
    show(call("GET", project, "/runs/"+identifier(run_id)))


@app.command("events")
def events(run_id: str, project: str = typer.Option(..., "--project", "-p"), after: int = 0):
    show(call("GET", project, f"/runs/{identifier(run_id)}/events?after={after}"))


@app.command("cancel")
def cancel(run_id: str, project: str = typer.Option(..., "--project", "-p")):
    show(call("POST", project, f"/runs/{identifier(run_id)}/cancel", {}))


@app.command("retry")
def retry(run_id: str, project: str = typer.Option(..., "--project", "-p")):
    """Retry the same frozen specification in a new execution attempt."""
    show(call("POST", project, f"/runs/{identifier(run_id)}/retry", {}))


@app.command("upload")
def upload(file: Path, project: str = typer.Option(..., "--project", "-p")):
    with client() as http, file.open("rb") as stream:
        response = http.post(path(project, "/inputs"), files={"file": (file.name, stream)})
        response.raise_for_status()
        show(response.json())


@app.command("snapshot-input")
def snapshot_input(snapshot: str, project: str = typer.Option(..., "--project", "-p")):
    """Copy an existing immutable snapshot into laboratory evidence with its original splits."""
    show(call("POST", project, "/inputs/from-snapshot", {"snapshot": snapshot}))


@app.command("model-input")
def model_input(job_name: str, experiment_number: int, project: str = typer.Option(..., "--project", "-p")):
    """Copy already available full/merged weights; never starts a merge or model download."""
    show(call("POST", project, "/inputs/from-model", {"job_name": job_name, "experiment_number": experiment_number}))


@app.command("promote-dataset")
def promote_dataset(run_id: str, artifact_id: str, name: str, project: str = typer.Option(..., "--project", "-p"),
                    idempotency_key: str = typer.Option(...), snapshot_name: str | None = None,
                    min_training: int = typer.Option(10, min=1, max=10000)):
    """Promote explicit chat examples, preserving lineage and excluding final-test/truth rows."""
    show(call("POST", project, "/datasets/from-run", {"run_id": identifier(run_id), "artifact_id": identifier(artifact_id),
        "name": name, "idempotency_key": idempotency_key, "snapshot_name": snapshot_name, "min_training": min_training}))


@app.command("repository-input")
def repository_input(repository: str, project: str = typer.Option(..., "--project", "-p"),
                     idempotency_key: str = typer.Option(...)):
    """Capture an existing RAG repository as retained Lab input without uploading it again."""
    show(call("POST", project, "/inputs/from-repository", {"repository": repository, "idempotency_key": idempotency_key}))


def output_paths(output: Path) -> Path:
    if output.exists() or output.is_symlink():
        raise typer.BadParameter("output already exists")
    temporary = output.with_name(output.name+".partial")
    if temporary.exists() or temporary.is_symlink():
        raise FileExistsError("partial export already exists")
    return temporary


def download_export(project: str, operation: dict, output: Path) -> None:
    """Recover bytes from one completed operation, without admitting new work."""
    import hashlib
    import re

    operation_id = identifier(operation.get("id"))
    if operation.get("kind") != "export" or operation.get("state") != "succeeded":
        raise typer.BadParameter("download requires a succeeded export operation")
    expected = (operation.get("result") or {}).get("artifact") or {}
    size, sha256 = expected.get("size"), expected.get("sha256")
    if (type(size) is not int or size < 0 or not isinstance(sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", sha256)):
        raise typer.BadParameter("completed export has an invalid artifact manifest")
    temporary = output.with_name(output.name+".partial")
    if temporary.exists() or temporary.is_symlink():
        raise FileExistsError("partial export already exists")
    owned = False
    digest, received = hashlib.sha256(), 0
    try:
        with client() as http, http.stream("GET", path(project, f"/operations/{operation_id}/download")) as response:
            response.raise_for_status()
            with temporary.open("xb") as stream:
                owned = True
                for block in response.iter_bytes():
                    received += len(block)
                    if received > size:
                        raise typer.BadParameter("downloaded export does not match its completed artifact manifest")
                    digest.update(block)
                    stream.write(block)
        if received != size or digest.hexdigest() != sha256:
            raise typer.BadParameter("downloaded export does not match its completed artifact manifest")
        # Hard-link publication is exclusive even if another writer wins after preflight.
        os.link(temporary, output)
    finally:
        if owned:
            temporary.unlink(missing_ok=True)
    typer.echo(str(output.resolve()))


@app.command("export")
def export(run_id: str, output: Path, project: str = typer.Option(..., "--project", "-p")):
    """Create an export operation and download its verified portable ZIP."""
    run_id = identifier(run_id)
    identifier(project, maximum=80)
    output_paths(output)
    operation = call("POST", project, f"/runs/{run_id}/exports", {"idempotency_key": uuid.uuid4().hex})
    typer.echo(f"Export operation {operation['id']}", err=True)
    download_export(project, wait_operation(project, operation), output)


@app.command("import")
def import_project(file: Path, project: str = typer.Option(..., "--project", "-p")):
    with client() as http, file.open("rb") as stream:
        response = http.post(path(project, "/inputs"), files={"file": (file.name, stream)})
        response.raise_for_status()
        artifact = response.json()
    show(call("POST", project, "/bundle-imports", {"artifact_id": artifact["id"], "idempotency_key": uuid.uuid4().hex}))


def wait_operation(project: str, value: dict, maximum_seconds: float = 600) -> dict:
    import time
    deadline = time.monotonic()+maximum_seconds
    while value["state"] in {"queued", "running"}:
        if time.monotonic() >= deadline:
            raise typer.BadParameter(f"operation {value['id']} continues on the server; inspect with tinystudio lab operation {value['id']} --project {project}")
        time.sleep(.5)
        value = call("GET", project, "/operations/"+identifier(value["id"]))
    if value["state"] != "succeeded":
        raise typer.BadParameter(f"operation {value['id']} ended as {value['state']}: {value.get('error') or 'inspect and recover explicitly'}")
    return value


@app.command("operations")
def operations(project: str = typer.Option(..., "--project", "-p")):
    """Find recent durable operations after client disconnect."""
    show(call("GET", project, "/operations"))


@app.command("operation")
def operation(id_: str, project: str = typer.Option(..., "--project", "-p"), cancel: bool = False, retry: bool = False,
              output: Path | None = None):
    """Inspect, explicitly recover, or download existing durable evidence work."""
    id_ = identifier(id_)
    if cancel and retry:
        raise typer.BadParameter("choose cancel or retry, not both")
    if output is not None and (cancel or retry):
        raise typer.BadParameter("download cannot be combined with cancel or retry")
    if output is not None:
        output_paths(output)
        value = call("GET", project, "/operations/"+id_)
        download_export(project, value, output)
        return
    suffix = "/cancel" if cancel else "/retry" if retry else ""
    show(call("POST" if suffix else "GET", project, "/operations/"+id_+suffix, {} if suffix else None))


@app.command("targets")
def targets(project: str = typer.Option(..., "--project", "-p")):
    show(call("GET", project, "/targets"))


@app.command("enroll")
def enroll(name: str, credential_file: Path = typer.Option(...), project: str = typer.Option(..., "--project", "-p")):
    """Create a scoped target credential in a private file. Requires project admin access."""
    fd = os.open(credential_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            response = call("POST", project, "/targets", {"name": name})
            json.dump({"server": os.environ.get("TINYSTUDIO_URL", "http://127.0.0.1:8000"),
                       "target_id": response["id"], "token": response["token"]}, stream)
    except BaseException:
        credential_file.unlink(missing_ok=True)
        raise
    typer.echo(f"Credential saved to {credential_file}. Run: tinystudio runner --credential-file {credential_file}")


@app.command("recover")
def recover(target_id: str, project: str = typer.Option(..., "--project", "-p")):
    """Mark expired attempts lost; never automatically replay physical actions."""
    show(call("POST", project, f"/targets/{identifier(target_id)}/recover", {}))


def runner(credential_file: Path = typer.Option(...), once: bool = False):
    """Execute leased laboratory attempts over outbound HTTPS using a scoped credential."""
    from .rq_adapter import load_credentials
    credential = load_credentials(credential_file)
    run_runner(credential["server"], credential["target_id"], credential["token"], once=once,
               profile_config=credential.get("profile_config"), access=credential.get("access"))


@app.command("dispatch")
def dispatch(once: bool = False):
    """Deliver committed evidence operations and configured RQ runs; PostgreSQL owns state."""
    import time
    from .dispatch import tick
    while True:
        show(tick())
        if once:
            return
        time.sleep(2)


def register(root: typer.Typer, *, include_data_bridges: bool = False):
    from copy import copy

    registered = copy(app)
    bridge_commands = {"snapshot-input", "model-input"}
    registered.registered_commands = [command for command in app.registered_commands
                                      if include_data_bridges or command.name not in bridge_commands]
    root.add_typer(registered, name="lab")
    root.command("runner")(runner)
