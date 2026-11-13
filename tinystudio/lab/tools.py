"""MCP and assistant methods are thin clients of laboratory HTTP services."""
from .client_paths import RuntimeApiPaths, HardwareApiPaths, path_identifier, runtime_definition
from .study_import_paths import StudyImportPaths

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

    def lab_runtime_methods() -> dict:
        """Inspect runtime qualification methods, required settings and evidence limits. No qualification runs."""
        return call("GET", RuntimeApiPaths().methods)

    def lab_hardware_discovery_methods() -> dict:
        """Inspect implemented discovery schemas; this does not enumerate or contact devices.

        Author hardware.discover with an explicit method/binding and acquisition
        mode using lab_define. The selected host must separately grant that
        exact source. Discovery establishes structure, not physical validation.
        """
        return call('GET', HardwareApiPaths().discovery_methods)

    def lab_runtime_define(name: str, parameters: dict, execution: dict, inputs: list[dict] | None = None) -> dict:
        """Save a runtime qualification definition from authored settings; no run starts.

        Inspect lab_runtime_methods first. The nf4.training method requires an
        explicit objective, device, dtype and fixture. Retain any authored
        nf4_execution, reload_comparison, training and fixture_config settings.
        Ask for missing choices; do not invent numerical tolerances or infer
        qualification from availability. Inputs are scoped immutable artifact
        references. Use lab_preflight, lab_submit and lab_run for execution and
        retained evidence inspection on the researcher's selected target.
        """
        return call("POST", RuntimeApiPaths().definitions,
                    json={"definition": runtime_definition(name, parameters, execution, inputs)})

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

    def lab_analysis_methods() -> dict:
        """Inspect observed-data analysis methods, explicit selection policies, assumptions and custom-code requirements. No analysis is run."""
        return call("GET", "/api/lab/statistics/analysis-methods")

    def lab_study_analysis_input(study_id: str, idempotency_key: str, revision: int | None = None,
                                 through_seq: int | None = None) -> dict:
        """Queue a retained study snapshot for analysis at an authored or current history boundary.

        This captures evidence only. Inspect the returned durable operation with
        lab_operation and reuse its retained snapshot artifact. Repeating the
        same key replays the original boundary even after more records arrive.
        """
        body = {"idempotency_key": idempotency_key}
        if revision is not None:
            body["revision"] = revision
        if through_seq is not None:
            body["through_seq"] = through_seq
        return call("POST", f"/api/lab/studies/{path_identifier(study_id)}/analysis-inputs", json=body)

    def lab_analysis_define(name: str, snapshot_artifact_id: str, parameters: dict, execution: dict,
                             custom_code_artifact_id: str | None = None,
                             extra_artifact_ids: list[str] | None = None) -> dict:
        """Save an observed-data analysis definition bound to retained evidence; no run starts.

        Inspect lab_analysis_methods and retain the researcher's record selection,
        outcome/condition/unit mapping, missingness and repeated-measurement
        policies, confidence and assumptions. Ask for missing choices. Custom
        Python requires retained code and an authored method name/version; extra
        inputs are explicit artifact references. Use existing lab_preflight,
        lab_submit and lab_run for execution and evidence inspection.
        """
        body = {"name": name, "snapshot_artifact_id": path_identifier(snapshot_artifact_id),
                "parameters": parameters, "execution": execution}
        if custom_code_artifact_id is not None:
            body["custom_code_artifact_id"] = path_identifier(custom_code_artifact_id)
        if extra_artifact_ids is not None:
            body["extra_artifact_ids"] = [path_identifier(value) for value in extra_artifact_ids]
        return call("POST", "/api/lab/statistics/analyses", json=body)

    def lab_study_analysis_result(study_id: str, run_id: str, idempotency_key: str, notes: str = "") -> dict:
        """Append a verified completed analysis link at its retained historical study revision.

        This records provenance and researcher notes. It does not execute the
        analysis, infer statistical n from ledger counts or replace corrections.
        """
        return call("POST", f"/api/lab/studies/{path_identifier(study_id)}/analysis-results",
                    json={"run_id": path_identifier(run_id), "idempotency_key": idempotency_key, "notes": notes})

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

    def lab_study_import_preview(study_id: str, artifact_id: str, revision: int, projection: dict) -> dict:
        """Preview explicit artifact rows and mappings without appending records or retaining artifacts.

        The researcher chooses rows, unit_id, condition, outcome data and
        number_encoding (json or decimal_strings). Preserve projection exactly;
        ask for missing choices instead of inventing them. POST performs
        deterministic reads only. Inspect its digest before separately choosing
        lab_study_import; preview never imports or starts execution.
        """
        return call('POST', StudyImportPaths(study_id).preview, json={
            'artifact_id': path_identifier(artifact_id), 'revision': revision, 'projection': projection})

    def lab_study_import(study_id: str, artifact_id: str, revision: int, projection: dict,
                         preview_digest: str, idempotency_key: str) -> dict:
        """Append explicitly chosen artifact rows using an inspected preview digest and stable import key.

        Retain the researcher's exact projection, unit_id, condition, outcome
        mapping and number_encoding. This writes observations and provenance;
        row counts do not establish scientific independence or sample sizes.
        """
        return call('POST', StudyImportPaths(study_id).apply, json={
            'artifact_id': path_identifier(artifact_id), 'revision': revision, 'projection': projection,
            'preview_digest': preview_digest, 'idempotency_key': idempotency_key})

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

    methods = [lab_catalog, lab_definitions, lab_runtime_methods, lab_runtime_define, lab_hardware_discovery_methods, lab_statistics_methods, lab_statistics_define,
               lab_analysis_methods, lab_study_analysis_input, lab_analysis_define, lab_study_analysis_result,
               lab_define, lab_preflight, lab_submit, lab_run, lab_events, lab_cancel, lab_retry, lab_targets, lab_export, lab_operation, lab_import, lab_recover, lab_operations, lab_operation_cancel, lab_operation_retry, lab_promote_dataset, lab_repository_input,
               lab_studies, lab_study, lab_study_save, lab_study_revisions, lab_study_records, lab_study_record, lab_study_manifest,
               lab_study_import_preview, lab_study_import]
    if include_data_bridges:
        methods += [lab_snapshot_input, lab_model_input]
    return [ToolSpec(f.__name__, f.__doc__, f, read_only=f in {lab_catalog, lab_definitions, lab_runtime_methods, lab_hardware_discovery_methods, lab_statistics_methods, lab_analysis_methods, lab_run, lab_events, lab_targets, lab_preflight, lab_operation, lab_operations,
                                                          lab_studies, lab_study, lab_study_revisions, lab_study_records, lab_study_manifest, lab_study_import_preview},
                     long_running=f in {lab_submit, lab_retry, lab_export, lab_import, lab_operation_retry, lab_promote_dataset, lab_repository_input, lab_study_analysis_input})
            for f in methods]
