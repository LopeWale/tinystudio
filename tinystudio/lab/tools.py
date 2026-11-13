"""MCP and assistant methods are thin clients of laboratory HTTP services."""
from .client_paths import path_identifier

def tool_specs(studio, ToolSpec, *, include_data_bridges=False):
    def call(method, path, **kwargs):
        project = getattr(studio, "project", None)
        if project is not None:
            path_identifier(project, maximum=80)
        return studio.call(method, path, **kwargs)

    def lab_catalog() -> dict:
        """Inspect implemented capabilities and actual execution prerequisites."""
        return call("GET", "/api/lab/catalog")

    def lab_definitions() -> list:
        """List project laboratory definitions and revisions."""
        return call("GET", "/api/lab/definitions")

    def lab_statistics_methods() -> dict:
        """Inspect statistical planning methods, goals, required inputs, assumptions and references. No calculation is run."""
        return call("GET", "/api/lab/statistics/methods")

    def lab_statistics_define(name: str, parameters: dict, execution: dict) -> dict:
        """Save a statistical planning definition using researcher-supplied assumptions and source notes.

        Inspect lab_statistics_methods first; ask for missing effects, variability,
        sidedness, alpha/power or precision instead of inventing them. This only
        saves a definition. Use lab_preflight, lab_submit and lab_run for the
        existing frozen execution/evidence workflow. Calculations are planning
        estimates, not measured results or guarantees.
        """
        return call("POST", "/api/lab/definitions", json={"definition": {
            "name": name, "engine": "statistics.plan", "parameters": parameters, "execution": execution}})

    def lab_define(definition: dict) -> dict:
        """Save a new versioned laboratory definition without executing it."""
        return call("POST", "/api/lab/definitions", json={"definition": definition})

    def lab_studies(offset: int = 0, limit: int = 100) -> list:
        """List project studies and their current protocol revisions; counts are bookkeeping, not independent sample accrual."""
        return call('GET', f'/api/lab/studies?offset={offset}&limit={limit}')

    def lab_study(study_id: str, revision: int | None = None) -> dict:
        """Read an authored study protocol and its digest, optionally at a historical revision."""
        suffix = f'?revision={revision}' if revision is not None else ''
        return call('GET', f'/api/lab/studies/{path_identifier(study_id)}'+suffix)

    def lab_study_save(study: dict, idempotency_key: str, study_id: str | None = None,
                       expected_revision: int | None = None) -> dict:
        """Create or revise a researcher-authored study; no run starts and no observations are invented.

        New studies require a stable idempotency key. Updating requires the last
        observed head revision; conflicts require reloading, not blind overwrite.
        Hypothesis, independent unit, conditions, outcomes, sampling/stopping
        rules, status and metadata remain researcher choices.
        """
        if study_id is None:
            return call('POST', '/api/lab/studies', json={'study': study, 'idempotency_key': idempotency_key})
        return call('PUT', f'/api/lab/studies/{path_identifier(study_id)}',
                    json={'study': study, 'expected_revision': expected_revision})

    def lab_study_revisions(study_id: str) -> list:
        """Inspect preserved protocol history with authenticated authorship and recording times."""
        return call('GET', f'/api/lab/studies/{path_identifier(study_id)}/revisions')

    def lab_study_records(study_id: str, after: int = 0) -> dict:
        """Read a page of append-only collection records pinned to their protocol revisions; continue using next_after."""
        return call('GET', f'/api/lab/studies/{path_identifier(study_id)}/records?after={after}')

    def lab_study_record(study_id: str, record: dict) -> dict:
        """Append an authored observation, note, deviation or evidence link with explicit protocol revision/idempotency key.

        Use real observed data and source identities. Negative/missing results
        are valid. Corrections reference supersedes; they do not erase history.
        Run/artifact references are resolved within the project and frozen.
        """
        return call('POST', f'/api/lab/studies/{path_identifier(study_id)}/records', json=record)

    def lab_study_manifest(study_id: str) -> dict:
        """Resolve a frozen study export boundary and authenticated JSONL download path. Referenced artifact bytes are external; use existing evidence exports for them."""
        return call('GET', f'/api/lab/studies/{path_identifier(study_id)}/manifest')

    def lab_preflight(definition: dict, target_id: str = "local") -> dict:
        """Check compatibility and actionable prerequisites without submitting or executing."""
        return call("POST", "/api/lab/preflight", json={"definition": definition, "target_id": target_id})

    def lab_submit(definition_id: str, revision: int, target_id: str, idempotency_key: str, not_before: str | None = None) -> dict:
        """Freeze a laboratory run and queue one execution attempt on the selected target."""
        body = {"definition_id": definition_id, "revision": revision,
                "target_id": target_id, "idempotency_key": idempotency_key}
        if not_before is not None:
            body["not_before"] = not_before
        return call("POST", "/api/lab/runs", json=body)

    def lab_run(run_id: str) -> dict:
        """Inspect a run, immutable spec, attempt states and published evidence manifest."""
        return call("GET", f"/api/lab/runs/{path_identifier(run_id)}")

    def lab_events(run_id: str, after: int = 0) -> dict:
        """Reconnect to durable run events after a sequence identifier."""
        return call("GET", f"/api/lab/runs/{path_identifier(run_id)}/events?after={after}")

    def lab_cancel(run_id: str) -> dict:
        """Request cancellation; running work ends only after runner acknowledgement or lease recovery."""
        return call("POST", f"/api/lab/runs/{path_identifier(run_id)}/cancel", json={})

    def lab_retry(run_id: str) -> dict:
        """Retry the unchanged frozen specification; uncertain physical effects cannot retry."""
        return call("POST", f"/api/lab/runs/{path_identifier(run_id)}/retry", json={})

    def lab_targets() -> list:
        """Inspect enrolled machines, last contact and declared capabilities."""
        return call("GET", "/api/lab/targets")

    def lab_export(run_id: str, idempotency_key: str) -> dict:
        """Create a durable export operation for a retained laboratory run."""
        return call("POST", f"/api/lab/runs/{path_identifier(run_id)}/exports", json={"idempotency_key": idempotency_key})

    def lab_operation(operation_id: str) -> dict:
        """Inspect a durable import or export operation and its retained result or error."""
        return call("GET", f"/api/lab/operations/{path_identifier(operation_id)}")

    def lab_operations() -> list:
        """List recent project operations to recover work after client disconnect."""
        return call("GET", "/api/lab/operations")

    def lab_operation_cancel(operation_id: str) -> dict:
        """Explicitly cancel a durable evidence operation; this is not read-only inspection."""
        return call("POST", f"/api/lab/operations/{path_identifier(operation_id)}/cancel", json={})

    def lab_operation_retry(operation_id: str) -> dict:
        """Explicitly retry a failed, lost or cancelled durable evidence operation."""
        return call("POST", f"/api/lab/operations/{path_identifier(operation_id)}/retry", json={})

    def lab_import(artifact_id: str, idempotency_key: str) -> dict:
        """Import an uploaded portable bundle through a durable laboratory operation."""
        return call("POST", "/api/lab/bundle-imports", json={"artifact_id": artifact_id, "idempotency_key": idempotency_key})

    def lab_recover(target_id: str) -> dict:
        """Recover expired target leases without replaying uncertain physical actions."""
        return call("POST", f"/api/lab/targets/{path_identifier(target_id)}/recover", json={})

    def lab_snapshot_input(snapshot: str) -> dict:
        """Retain an existing snapshot as immutable laboratory evidence with preserved splits."""
        return call("POST", "/api/lab/inputs/from-snapshot", json={"snapshot": snapshot})

    def lab_model_input(job_name: str, experiment_number: int) -> dict:
        """Copy completed full/merged model evidence without training, merging, or downloading a base model."""
        return call("POST", "/api/lab/inputs/from-model", json={"job_name": job_name, "experiment_number": experiment_number})

    def lab_promote_dataset(run_id: str, artifact_id: str, name: str, idempotency_key: str,
                            snapshot_name: str | None = None, min_training: int = 10) -> dict:
        """Queue explicit chat promotion and optional frozen snapshot; exclude final-test/truth. Does not train."""
        return call("POST", "/api/lab/datasets/from-run", json={"run_id": path_identifier(run_id),
            "artifact_id": path_identifier(artifact_id), "name": name, "idempotency_key": idempotency_key,
            "snapshot_name": snapshot_name, "min_training": min_training})

    def lab_repository_input(repository: str, idempotency_key: str) -> dict:
        """Queue an immutable, optimistically validated capture of an existing RAG repository; does not train."""
        return call("POST", "/api/lab/inputs/from-repository", json={"repository": repository, "idempotency_key": idempotency_key})

    methods = [lab_catalog, lab_definitions, lab_statistics_methods, lab_statistics_define, lab_define, lab_preflight, lab_submit, lab_run, lab_events, lab_cancel, lab_retry, lab_targets, lab_export, lab_operation, lab_import, lab_recover, lab_operations, lab_operation_cancel, lab_operation_retry, lab_promote_dataset, lab_repository_input,
               lab_studies, lab_study, lab_study_save, lab_study_revisions, lab_study_records, lab_study_record, lab_study_manifest]
    if include_data_bridges:
        methods += [lab_snapshot_input, lab_model_input]
    return [ToolSpec(f.__name__, f.__doc__, f, read_only=f in {lab_catalog, lab_definitions, lab_statistics_methods, lab_run, lab_events, lab_targets, lab_preflight, lab_operation, lab_operations,
                                                          lab_studies, lab_study, lab_study_revisions, lab_study_records, lab_study_manifest},
                     long_running=f in {lab_submit, lab_retry, lab_export, lab_import, lab_operation_retry, lab_promote_dataset, lab_repository_input})
            for f in methods]
