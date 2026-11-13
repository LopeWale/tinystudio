# Study tracking

Studies organize researcher-authored protocols and collected evidence across the Laboratory's existing engines. They use the same project authorization, database and HTTP clients as definitions/runs. Creating a study or recording evidence does not execute a run, select a statistical test or make a physical command.

## Protocols and collection records

A protocol contains a name, description, hypothesis, independent-unit description, conditions, outcomes, sampling plan, stopping rules, status and additional metadata. Only a nonempty name is required; other unknown assumptions remain empty. Conditions/outcomes are JSON arrays of simple labels or structured descriptions; plans/rules/metadata are extensible JSON objects. Status is editable text and can be blank, not a mandatory state machine. No sample count, power target, stopping rule, result direction or paper constraint is enforced by study tracking.

Saving an edit creates a new immutable protocol revision with a SHA-256 digest, authenticated user ID and server recording time. Updating requires `expected_revision` equal to the current head. A conflict leaves the existing revision intact; reload and reconcile the draft before saving. Historical protocols can be reopened and explicitly saved as a new revision.

Collection records form a separately ordered, append-only ledger. Each record selects an existing protocol revision and retains its digest, even when a newer protocol exists. Common fields are record kind, unit ID, condition, optional observation time and structured data. Kind/condition/unit IDs are researcher labels; the platform does not infer independence, pairing or group membership from them. A note, deviation, planning reference, missing observation, negative result or inconclusive outcome is valid evidence.

Corrections set `supersedes` to an earlier record in the same study. Both records remain visible. This relationship does not silently delete, exclude or choose an analytical value. Later analysis must declare how corrections, missingness and exclusions are handled.

Ledger counts report all records and distinct nonempty declared unit IDs, including corrections and superseded entries. They are **bookkeeping, not analysis sample sizes**. Repeated observations, paired data, clusters and overlapping conditions require their own independence rules. More records or units than an authored plan remain recordable.

## Times, values and bulk data

`recorded_at` is the server's authenticated recording time. Optional `observed_at` requires an ISO8601 timestamp with an explicit timezone; the authored string, including sub-microsecond precision and offset, is preserved. Device ticks, simulation times, unknown offsets and other raw clocks can be retained in `data` with their clock domain and units; no wall-clock conversion is invented.

JSON numbers must be finite. Use `null` with an explicit missingness reason rather than NaN/Infinity. Integers outside the exact JSON/browser range `±(2**53-1)` must use decimal strings to preserve their value. This is a representation boundary, not a scientific study-size limit; supported floating-point magnitudes remain usable. Study payloads use canonical JSON storage, and the study editor's numeric codec preserves scientific floating-point values and signed zero through copying, formatting and HTTP serialization. Use decimal strings or raw artifacts when exact decimal precision is required. Individual protocol/record documents use a 2 MiB transport budget; record lists paginate without a total study record cap. Retain larger raw streams, tables and acquisition files through the existing artifact upload mechanism and link their IDs.

## Evidence associations

Records can link project run IDs and artifact IDs. The server resolves their identities within the current project and verifies retained object bytes against the stored SHA-256 and size before admitting a new association. Missing/corrupt objects fail admission; client-authored hashes are not substituted for stored custody metadata. Slow verification occurs before study/run write locks. Standalone artifact links do not imply that a file is a run output. Source notes and observation contents remain researcher assertions, distinct from authenticated authorship and retained execution evidence.

A run link freezes its specification digest, captured state/error/event position, exact result artifact identities and retained outcome references through that event position. Capture time is distinct from the final record's server time. A retry can reuse the same run ID and change its live result, so a bare run ID is not proof of a fixed observed outcome. Existing study links stay unchanged after retry. Artifact links preserve name, checksum, size, role, visibility, byte-verification time and actual attempt/run origin when available. A repeated idempotency key returns its original historical association, even if the source later expires. Storage keys and credentials are not exposed in the study receipt.

A [statistical-planning](statistical-planning.md) receipt can be linked as a planning record. Its calculated counts and sensitivity assumptions do not become measured observations or a collection ceiling. Any existing engine's evidence can be linked; the study is not tied to a particular method or domain.

## Lab, CLI and assistant

Open **Laboratory → Studies** to create/reopen a protocol, inspect its history, select a revision and append records. The study directory paginates beyond the first 100 entries. Common identifiers use ordinary text fields; scientific design/data fields remain editable JSON. Drafts survive failed requests and optimistic conflicts. A network failure with an unknown outcome retains the request key/body for a safe retry; starting a different request uses a new key. A study/project switch must not display stale response data.

The [illustrative protocol](../examples/study-protocol.json) and [record request](../examples/study-record.json) contain no measured result. Replace them with the actual study's choices and observations.

```bash
tinystudio lab study-save examples/study-protocol.json --project research --idempotency-key protocol-request-001
tinystudio lab studies --project research
tinystudio lab study-inspect STUDY_ID --project research
tinystudio lab study-save revised-protocol.json --study-id STUDY_ID --expected-revision 1 --project research
tinystudio lab study-revisions STUDY_ID --project research
tinystudio lab study-record STUDY_ID actual-record.json --project research --idempotency-key collection-request-001
tinystudio lab study-records STUDY_ID --after 0 --limit 100 --project research
tinystudio lab study-export STUDY_ID tracking.jsonl --project research
```

Creation and record requests use caller idempotency keys. Repeating identical inputs with the same key returns the original identity; reusing the key for different inputs returns 409. New CLI requests generate a key if one is not supplied. For retries after a lost response, supply the original key and body. Protocol edits instead use explicit optimistic revision comparison.

The shared assistant/MCP tools are `lab_studies(offset=0, limit=100)`, `lab_study`, `lab_study_save`, `lab_study_revisions`, `lab_study_records`, `lab_study_record` and `lab_study_manifest`. They call the same authenticated HTTP services. The assistant should preserve actual source data and unknowns; language-model prose is not evidence of an observation, a sample size or a study conclusion.

## Tracking export and verification

`GET /api/p/<project>/lab/studies/<id>/manifest` captures the protocol/record high-water boundary and provides a download path pinned to that boundary. Subsequent appends do not enter that snapshot. The export is streaming JSONL (`tinystudio.study-tracking.v1`): a manifest header, each protocol snapshot/digest, each record/digest, and an `export_summary` footer. The footer's SHA-256 covers every preceding byte, including newlines; the HTTP `X-Content-SHA256` header covers the complete file. The CLI verifies that full-file checksum before exclusive publication of the download.

The file includes authored collection data and **external evidence identities, not referenced artifact bytes**. Obtain source files through existing artifact/run exports. Foreign run IDs are historical provenance, not recreated live execution identities. Study import or run recreation is not implied by this tracking format. Revision/record checksums are verified when reading/exporting; corruption and incomplete histories fail explicitly.

New tables are additive after migration `h1d3e7f65a49`; existing definitions, runs and artifacts retain their identities. Downgrade refuses to discard nonempty study history. Test the software path with:

```bash
python -m pytest tests/test_lab_studies.py tests/test_lab_studies_http.py
TINYSTUDIO_STUDY_TEST_DATABASE_URL=postgresql+psycopg://USER@HOST/DB python -m pytest tests/test_lab_studies_postgres.py
```

The PostgreSQL fixture creates/drops only its uniquely named databases on the explicitly supplied server. It verifies real migration preservation, competing saves, concurrent sequence allocation, duplicate admission and guarded downgrade. SQLite/native HTTP tests exercise their own disposable stores and real runner child processes. Broader statistical analysis and campaign automation remain later milestones.
