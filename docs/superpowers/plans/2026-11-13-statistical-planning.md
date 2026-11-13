# Statistical Planning Implementation Plan

> **For agentic workers:** Execute the tasks against the accepted statistics-first build proposal and experimentation principles. Independent frontend work may run concurrently after the report/API contract is fixed; integrate and review the complete result.

**Goal:** Configure sample-size, power, detectable-effect and precision planning, retain reproducible calculation evidence, and reopen it through the existing Lab, CLI and assistant workflows.

**Architecture:** Add `statistics.plan` to the existing catalog/preflight/runner/artifact lifecycle. Keep dependency-free selection and method metadata in `statistics_selection.py`, numerical execution and report publication in `statistics.py`, and use the current React Lab rather than a separate application. Methods are versioned registry entries; this initial collection does not impose universal study rules.

**Tech Stack:** Existing Python 3.12/FastAPI/PostgreSQL/artifact runner and React/BoardUI, optional SciPy statistics dependency.

**Spec:** `docs/experimentation-principles.md` and the statistics-first build proposal accepted in conversation on 2026-11-13.

## Global Constraints

- Reuse definitions, immutable runs, scoped project authority, artifact checksums and shared assistant/MCP tools; preserve existing API contracts.
- Researcher selects alpha, power, effect, variability, independent unit, sidedness and attrition. Never invent missing assumptions.
- Four initial methods: means.independent, means.paired, proportions.independent, proportion.precision. Means use noncentral-t planning under normal/equal-variance assumptions; proportions use explicitly labeled normal approximations.
- Editable examples are examples, not observed data. Every retained result is a planning estimate, not a guarantee or experimental outcome.
- No new scientific sample-count caps. Numeric representability and existing runner resource/definition budgets remain explicit operational constraints.
- Statistical methods expose assumptions/references/version and applicable fields. Reject unsupported or ignored settings; preserve authored request and source notes.

## Review Focus

- Missing/NaN/bool inputs and one-sided effects in the wrong direction cannot produce plausible sample counts.
- Unequal allocation/attrition rounding must preserve the target power and the distinction between pairs, observations and recruitment units.
- Sensitivity is frozen and recomputable; changed definitions cannot rewrite a submitted calculation.
- Cancellation, missing dependencies and tampered reports cannot be displayed as completed calculations.
- Cross-project or stale UI responses must not expose another study's evidence.

## Task 1: Numerical planning and retained executor

Files: create `tinystudio/lab/statistics_selection.py`, `tinystudio/lab/statistics.py`, `tests/test_lab_statistics.py`.
Interfaces: `method_catalog() -> dict`, `validate_statistics_selection(definition: dict) -> dict`, `calculate(parameters: dict, cancelled=...) -> dict`, `execute_statistics(spec, output_dir, emit, cancelled) -> dict`.

- [x] Write failing independent-reference and invalid-input tests, run and observe missing functionality.
- [x] Implement metadata/strict selections, noncentral-t and normal planning, fixed-sample power/effect/precision, integer allocation, attrition and sensitivity.
- [x] Publish versioned statistical_plan artifact with original request, method/source/runtime identity, normalized inputs and per-scenario results; verify cancellation and immutable input behavior.
- [x] Run the numerical and executor tests against independent references.

## Task 2: Existing platform integration

Files: modify `lab/service.py`, `lab/plugins.py`, `lab/preflight.py`, `lab/execution.py`, `lab/api.py`, `lab/tools.py`, `pyproject.toml`; create integration/client tests and example definition.

- [x] Write failing engine/catalog/preflight and shared tool-route tests.
- [x] Register engine and optional SciPy requirement, metadata route, dependency checks and assistant definition helper. Execution uses existing submit/inspect/export lifecycle and generic CLI.
- [x] Exercise actual registered HTTP/native-child execution, saved definition edit/frozen run/reload/export, project separation, artifact receipts and invalid preflight.

## Task 3: Lab authoring and evidence UI

Files: frontend Lab definition/template/index and focused new statistics components/tests, owned by frontend worker.

- [x] Add failing editor/report tests for catalog-driven field applicability, missing input, sensitivity, changed selection and checksum failure.
- [x] Integrate editable planning template/editor, retained report loader and result/assumption/sensitivity/recipe evidence into current Lab.
- [x] Run focused UI tests, typecheck and production build; exercise connected authoring/run/reopen flow.

## Task 4: Documentation and integrated verification

- [x] Document exact methods/assumptions and worked example; record planning estimates versus real outcomes and remaining method coverage.
- [x] Verify Python integration, frontend build and real route/child behavior; attempt repository suites and record environmental or pre-existing failures without reclassifying skips as success.
- [x] Fresh whole-change review, resolve material findings and verify the integrated result. Preserve prior docs PR and keep feature work on its own branch.
