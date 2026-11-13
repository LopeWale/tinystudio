# Study tracking

This is the next increment of the user-approved experimentation workbench roadmap, following statistical planning. The user said to proceed and to keep reviewing existing code while implementing. Continue with the existing Python/FastAPI/SQLAlchemy and React/BoardUI stack; no additional approval cycle or new scheduler is needed.

## Existing foundation and approach

Lab definitions/revisions already freeze executable configuration. Runs, attempts, reserved outcome events and artifacts preserve execution evidence; retries reuse a run identity and can change its current result. Shared HTTP tools, CLI transport, scoped React resources and checksum-verified storage are reusable.

Considered storing study metadata inside engine definitions, using a single versioned study document, or keeping a versioned protocol plus separately addressable collection records. Use the last approach: a study is independent of any engine, and recording observations must not require rewriting a growing protocol or reinterpret earlier observations under a changed protocol. Raw bulk data continues to use existing artifact uploads.

## User-visible outcome

- Create/reopen a project study with name, description, hypothesis, independent-unit description, arbitrary conditions/outcomes, sampling plan, stopping rules, researcher-defined status and additional metadata. Blank/unknown assumptions remain blank. No paper values, test, progression, stopping rule or sample count is imposed.
- Save protocol revisions with optimistic conflict detection and authenticated author/server time. Preserve every earlier revision and its digest.
- Append collection records against an explicitly selected protocol revision: arbitrary record kind, unit ID, condition, optional observation time, structured data/notes, existing run IDs and artifact IDs. Corrections can reference an earlier record; the original stays intact. Server recording time is distinct from observation time.
- Resolve project evidence server-side and freeze metadata/hashes. A run association records its spec digest, state/event position at linking, exact current result artifacts and retained outcome references. Later retries or study edits cannot rewrite these associations. Association is provenance, not verification of a scientific claim.
- Browse paginated records, protocol history and descriptive ledger counts. Records/declared unit IDs are bookkeeping, not inferred independent sample accrual or a success judgment.
- Export a checksum-bearing study tracking manifest containing protocols, records and external evidence identities. Clearly label that referenced artifact bytes are obtained through existing artifact/run exports; this manifest does not claim to include them or recreate live imported runs.
- Expose the same workflow in the Lab Studies area, CLI and shared assistant/MCP tools. The assistant supplies authored values and references; it does not invent observations or convert planning assumptions into measured results.

## Persistence and compatibility

Add LabStudy, LabStudyRevision and LabStudyRecord tables through a new migration after h1d3e7f65a49. Do not modify frozen migrations or existing run contracts. Study and append requests have caller idempotency keys; duplicate identical requests return the same identity, conflicting reuse returns 409. Study-row locking and expected_revision serialize revisions and record sequence assignment; historical revisions remain writable as record associations.

Study documents/individual records use existing-style 2 MiB JSON admission budgets, finite canonical JSON and exact safe integer revisions/sequences. These are transport/representation constraints, not study-size rules. Record lists paginate without a total collection cap. Structured data is researcher-authored and extensible; method-specific numerical validation remains in its calculation engine.

Revision/record digests cover complete frozen documents and association metadata. Reading/exporting verifies those digests and pinned protocol relationships. An interrupted append must not leave a partially linked record or advance sequence outside the transaction. A correction is an annotation, not a silent deletion or analytical exclusion.

## Acceptance and risk boundaries

Prove real authenticated HTTP create/edit/append/reopen/export; stale-save conflicts; idempotent recording; old-revision associations; concurrent revision/sequence admission; cross-project rejection; unchanged association snapshots after run retry; checksum/tampering rejection; negative/missing/custom records; and records exceeding an authored planned count without a platform prohibition. Frontend drafts survive errors, stale project/study responses are masked, failed saves retain edits, and ordinary experiment/learning controls keep working.

Use disposable PostgreSQL for actual migration/locking verification if available, otherwise state that gap explicitly. SQLite/native HTTP tests qualify their software path only. No deployment, physical action, broader statistical-analysis engine or campaign scheduler is included in this increment.
