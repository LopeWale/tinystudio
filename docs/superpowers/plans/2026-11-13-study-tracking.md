# Study Tracking Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans. Independent frontend implementation may proceed against the fixed HTTP contract; one coordinator owns Python/schema integration and final verification.

**Goal:** Track researcher-authored study protocols, collection records and frozen evidence associations through the existing Lab/UI/CLI/assistant workflows.

**Architecture:** Separate project-scoped study identity, immutable protocol revisions and append-only records. Reuse Lab evidence lookup/checksums, project authorization and HTTP clients; metadata-only tracking exports explicitly identify external evidence.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy/Alembic/PostgreSQL; React/BoardUI.

**Spec:** `docs/superpowers/specs/2026-11-13-study-tracking-design.md`.

## Global Constraints

- Researcher controls study assumptions, statuses and stopping rules. Unknown values stay absent; no paper-specific limits or inferred sample accrual.
- Existing run/definition/execution contracts stay compatible. Every record pins its protocol revision/digest; run outcome snapshots survive retry.
- Authenticated project scope, finite canonical JSON, caller idempotency and optimistic revision control apply at the service boundary.
- Bulk observations remain ordinary artifacts. The tracking manifest exports metadata and authored records; external evidence bytes remain explicitly external.

## Review Focus

- Stale sessions or simultaneous writers cannot overwrite revisions or allocate the same record sequence.
- Cross-project, corrupt or stale evidence cannot gain trusted provenance.
- Corrections and changed protocols preserve original observations, negative outcomes and source notes.
- Empty/invalid UI drafts, project switches and delayed responses cannot save stale data or display foreign evidence.
- Counts distinguish ledger records/declared IDs from independent analysis units; planning values impose no collection ceiling.

## Task 1: Durable service and migration

Files: `lab/study_contracts.py`, `lab/studies.py`, `lab/models.py`, additive migration; tests in `tests/test_lab_studies.py` and `tests/test_lab_studies_postgres.py`.

Interfaces: save_study(db,project,document,author_id,*,id_=None,expected=None,key=None); get/list study revisions; append_record(db,project,study_id,body,author_id); paginated records; tracking_manifest(db,project,study_id).

- [x] Write failing public-service tests for revisions, old-revision records, idempotency/conflicts, corrections, finite/custom data and frozen evidence.
- [x] Implement contracts, tables/migration and transactional admissions with canonical digests and scoped reference resolution.
- [x] Verify unit/service tests and actual PostgreSQL migration/concurrency where available; preserve existing migration sentinel rows.

## Task 2: HTTP/CLI/assistant vertical slice

Files: `lab/api.py`, `lab/cli.py`, `lab/tools.py`, entrypoint expectation tests and new HTTP/native workflow tests.

- [x] Write failing HTTP/auth/project/isolation/export and shared-tool/CLI tests.
- [x] Add scoped study CRUD-by-revision, collection append/list, history and manifest endpoints; expose the same operations through thin clients.
- [x] Exercise actual registered HTTP plus native calculation evidence, later protocol edits/run retry, reload and exported tracking manifest.

## Task 3: Existing Lab Studies interface

Files: `frontend/src/features/lab/studies.tsx`, focused tests, minimal `index.tsx` integration.

- [x] Write failing create/save/reopen/record/history/export, draft/error and scope-switch tests.
- [x] Add Studies area using existing BoardUI aliases, SettingsSection, DraftField and scoped resources. Conditions/outcomes/sampling/stopping/data remain editable JSON, with ordinary text fields for common identity/time inputs.
- [x] Verify focused study/ordinary Lab regressions, TypeScript, production build and real browser persistence workflow.

## Task 4: Documentation and final review

- [x] Document protocol/record/reference semantics and worked CLI/assistant/UI example; update current capability statements.
- [x] Run affected Python/frontend suites, migration/locking checks and frozen-build asset/browser checks; record remaining environment/baseline failures.
- [x] Obtain fresh whole-change review, correct material findings and publish a separate PR against current master after the statistical-planning prerequisite merges.
