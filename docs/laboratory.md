# TinyStudio laboratory

The laboratory extends the existing application. Definitions, runs and evidence have their own identities; existing datasets, training Jobs, Experiments, metrics and inference APIs retain their contracts. This document is an implementation map, not a claim that every target is qualified.

The current accepted delivery boundary is A–C: authored definitions, frozen runs, scoped execution, retained evidence and the local data-to-training handoff. D–G remain unaccepted work; source files, catalog entries and roadmap rows for those phases do not establish delivery. The implementation task list is [laboratory-plan.md](laboratory-plan.md). Implementation history and validation results belong in [CHANGELOG.md](../CHANGELOG.md).

## Workflow requirements

Reuse existing project identity, uploads, evidence selection, training services and frontend components before adding another mechanism. The ordinary workflow should read as: choose evidence, configure an experiment, check the target, run, inspect the outcome, compare, and export. Technical configuration belongs beside the decision it affects, with advanced controls disclosed when needed.

Keep authored settings, frozen submitted settings and measured outcomes visibly distinct. Explain missing prerequisites with a concrete next step; never silently change the selected method or target. Retrying, restarting from weights and exact continuation are different actions. Cancellation and recovery must describe what actually stopped and what remains uncertain.

Navigation and the omnipresent assistant are shared application components. Laboratory features must expose scoped state and supported actions through the existing application patterns instead of creating another assistant destination or duplicating controls. Component reuse and source tests alone do not establish usability: acceptance still requires the team's actual authenticated workflow.

## Requirements and delivery map

Rows D–G describe future acceptance boundaries, including any existing experimental source.

| Requirement | Contract and acceptance | Implementation boundary |
| --- | --- | --- |
| LAB-A01 | Versioned definitions and immutable submitted specifications | `lab/contracts.py`, revision rows and checksums; explicit execution profile required |
| LAB-A02 | Existing training identities survive | Additive laboratory tables and optional Job/Experiment foreign keys; pretrained wrapper calls the existing trainer |
| LAB-A03 | Capability portfolio stays visible | Adapter/method catalog reports implementation, dependencies and qualification separately |
| LAB-B01 | Durable submit, claim, events, cancellation, retry | PostgreSQL transactional dispatch, target-first lease locking, run/attempt separation; no Redis credentials on runners |
| LAB-B02 | Raw and derived evidence remain distinct | Content-addressed objects; manifests published after uploads; original hashes and record identities retained |
| LAB-B03 | Portable outbound execution | Scoped target token, HTTPS except loopback, frozen source package, runtime receipt, transfer heartbeats and complete process-group cleanup |
| LAB-C01 | Data preparation preserves scientific meaning | Explicit units, missingness, estimated values, observation availability, causal versus future-aware transforms |
| LAB-C02 | Independent partitions and retrieval | Grouped/temporal split contracts, final-test exclusion, immutable retrieval indexes and lineage audits |
| LAB-C03 | Interconnected evidence views | Replay, topology, coverage, representations, paired baselines and deployment evidence in the existing React frontend |
| LAB-D01 | From-scratch and pretrained language methods | Scratch Transformer/GRU and byte/BPE/engineering vocabulary; existing full/LoRA/QLoRA/DPO through immutable model/snapshot inputs |
| LAB-D02 | Distillation, retrieval, numeric and multimodal models | Separate learning/retrieval contracts; actual teacher/student logits, lexical/dense/hybrid indexes, forecasting and fusion |
| LAB-D03 | Tool and environment learning | Token-level PPO/GRPO over a constrained tool language, separate control/offline/imitation policies, optional genuine TorchRL EnvBase |
| LAB-E01 | Domain engines | Immutable input adapters for named firmware, physics, network, energy, scheduling and robotics tools; compatible runtime fixtures required |
| LAB-E02 | Isolation and physical authority | Explicit target profiles; endpoint ownership epochs, command identities, uncertain outcomes and local-stop/recovery contracts |
| LAB-F01 | Combined experiments | Explicit master clocks, component periods, holds and delays; algebraic-loop rejection; paired intervention branches |
| LAB-G01 | Portability and measurement | Checksum-verified bundles; checkpoint-based continuation distinguished from reruns; measured host inference reports |
| LAB-G02 | Integrated qualification | Real authenticated UI, recovery/fault tests, existing workflow regressions and independent review; live hardware/cloud/cluster evidence required separately |

## Execution model

### Contract ownership

`tinystudio/lab/contracts.py` defines the versioned portable research contracts. `tinystudio/lab/plugins.py` defines plugin declarations shared by the capability catalog and qualification matrix. The catalog declares nine boundaries: sources, simulation engines, clocks, transformations, representations, trainers, retrievers, evaluators and deployments. Each implemented entry carries its source version, input/output envelope, unit policy, dependency resolution, target requirements, lifecycle contract and fixture requirements.

Ports reference versioned schemas exposed in the catalog's `contract_schemas` registry. Artifact ports declare role, cardinality, accepted media types, visibility and conditional requirements. Native domain roles and filename suffixes reuse the executors' existing `INPUT_ROLES` declarations. Shared services additionally declare model archives, snapshots, evaluation artifacts and checkpoint alternatives. Content schemas and record minima describe structured inputs such as conversation snapshots; selected executors enforce their complete content and operation constraints. `artifact_defined` means numerical units must come from that artifact's record/schema metadata; it does not authorize guessing or conversion. Visibility declarations grant no access by themselves; project authorization and executor visibility rules still apply.

The runner owns attempt preparation, process cancellation and output publication. A plugin cannot grant itself filesystem, network, credential or device access. Pause is unsupported. Checkpoint-specific continuation requires the selected method's compatible checkpoint; a declaration never establishes exact continuation. Qualification requires an actual receipt for the exact method selections, source, dependencies and target. The matrix deliberately remains unqualified when only fixture requirements are present.

The operational database and laboratory share one SQLAlchemy metadata registry. Migration `f9b1c5d43e27` adds laboratory tables after `e8a0b4c32d16`; `g0c2d6e54f38` adds durable evidence operations. Revision `h1d3e7f65a49` adds the data-bridge operation kind and structured operation errors without changing existing identities. Downgrade refuses to discard historical data-bridge operations. Later schema changes require new revisions. Legacy Job/Experiment links identify source experiments, preserve their identities and metric semantics, and freeze referenced configuration without writing their training state.

Definition revisions are saved separately; submission commits the resolved specification, run and dispatch row together. An execution host advertises its installed implementation and dependency versions, then claims a leased attempt. It receives only that attempt's input artifacts and publication authority. Every event has a durable run sequence; clients reconnect using `events --after LAST_SEQUENCE` (HTTP responses include `next_after` and `has_more`). Optional timezone-aware `not_before` is frozen in the submission schedule, normalized to UTC, and enforced before claim; a client disconnect does not remove it.

The runner copies and verifies the submitted source manifest into an attempt-owned package before importing experiment code. Local artifact paths are separate execution context. The child receives an allowlisted environment and an attempt-owned home, without inherited application or cloud credentials. Trusted native execution is still trusted host code; it is not an isolation claim.

A cancelled running attempt stays running until the worker acknowledges termination or recovery marks its lease lost. Lost nonphysical attempts require an explicit retry; retries retain the same specification. Unknown physical outcomes require reconciliation and cannot be retried automatically. Receiving the same completed payload is idempotent. This does not promise exactly-once physical effects.

Terminal results supplied to completion, including failed or cancelled partial results, resolve artifact references to canonical metadata from the same project and attempt. Referenced bytes must pass SHA256 and size verification before publication. Each retained attempt outcome is an `attempt-outcome.json` artifact; the reserved `server.outcome` event carries its small identity/checksum reference rather than embedding the full result in the timeline.

Content-addressed publication reads back and verifies stored bytes before publishing metadata. An already valid object is reused without rewriting it; missing or verified-corrupt objects may be repaired. Redelivery of an identical active-attempt upload preserves its artifact identity and does not charge its bytes twice, even when the original upload exhausted the output budget. Different content, role, visibility or size under the same attempt filename is rejected.

## Operator workflow

Use the normal API key and project for authoring and inspection. Enroll a separate project-target credential for outbound execution, then run the runner in its own terminal:

```sh
export TINYSTUDIO_URL=https://studio.example.org
export TINYSTUDIO_API_KEY=your-user-api-key
tinystudio lab enroll workstation --project research --credential-file runner.json
tinystudio runner --credential-file runner.json
```

The remote runner uses outbound HTTPS and its scoped token; it needs no PostgreSQL, Redis or object-store credentials. Plain HTTP is permitted only on loopback. Enrollment requires an administrator and creates a new mode `0600` file. The loader requires an owned regular nonsymlink file. Keep it outside research bundles. Its optional `access` object contains `client_id`, `client_secret` and `public_url` for Cloudflare Access; the existing Studio client sends those credentials only to the matching approved HTTPS origin. Without that object, the normal `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET` and `TINYSTUDIO_PUBLIC_URL` environment guard applies.

The long-lived runner reconnects after transient claim transport errors and HTTP 408/429/500/502/503/504, with delays from one to thirty seconds. A successful claim resets the delay. Authentication, configuration and active-attempt failures remain explicit; `--once` reports the original claim failure. A lost claim response conveys no execution authority to the client. Inspect and recover the retained attempt before explicitly retrying its run.

For a container-mounted credential, inspect the file owner as presented inside the guest and select a matching process user. The owned-file and `0600` checks still apply. A Docker guest running a trusted-native backend is a distinct target receipt; qualification of the laboratory's isolated/container execution profile requires its own fixtures.

Author and inspect through the same authenticated HTTP contracts:

```sh
tinystudio lab define experiment.json --project research
tinystudio lab check experiment.json --target TARGET_ID --project research
tinystudio lab submit DEFINITION_ID 1 --target TARGET_ID --project research
# Optional durable UTC eligibility time:
tinystudio lab submit DEFINITION_ID 1 --target TARGET_ID --project research --not-before 2027-01-01T12:00:00Z
tinystudio lab inspect RUN_ID --project research
tinystudio lab events RUN_ID --after 0 --project research
tinystudio lab cancel RUN_ID --project research
tinystudio lab recover TARGET_ID --project research
tinystudio lab retry RUN_ID --project research
```

Cancellation requests termination; a running attempt remains running until acknowledged or reconciled after lease expiry. `recover` marks expired nonphysical attempts lost and uncertain physical attempts `recovery_required`. Inspect that result before retrying. Retry explicitly creates a new attempt of the same frozen specification, including its schedule; recovery never automatically replays uncertain physical actions. Capability mismatches remain actionable errors rather than changing the chosen runtime.

### Dispatch and RQ workers

The API owns a separate laboratory dispatcher process for its lifetime. It polls committed work, uses bounded database/delivery waits, and shuts down its owned process group. A separate `tinystudio worker` consumes the existing RQ queue; the dispatcher does not execute experiments or bundle transfers itself. PostgreSQL owns run/attempt/operation truth, while Redis carries IDs. Queue outages leave committed work pending. Killing an RQ job alone does not prove experiment-process cleanup; use laboratory cancellation and inspect the lease/recovery state.

Evidence import/export operations use that worker without an RQ target credential. To also deliver experiment runs whose frozen backend is `rq`, set `TINYSTUDIO_LAB_RQ_CREDENTIAL_FILE` on the API/dispatcher and RQ worker hosts to an enrolled credential for that exact target. These store-connected hosts need their normal deployment PostgreSQL/Redis settings. The RQ worker loads credentials locally; job arguments and results contain no credentials or credential-file paths. Its only supported RQ profile is trusted native host execution; RQ is delivery, not isolation. Configure the target explicitly:

```json
{
  "server": "https://studio.example.org",
  "target_id": "TARGET_ID",
  "token": "ENROLLED_TARGET_TOKEN",
  "profile_config": {
    "schema_version": 1,
    "backends": {"rq": {"enabled": true, "profiles": ["trusted_native"]}}
  }
}
```

Keep this file mode `0600`. The adapter defaults an omitted `profile_config` to that same RQ policy; a supplied configuration must enable exactly this profile. Add the optional `access` object described above when required. The dispatcher advertises its configured target capabilities over the same authenticated HTTP transport before delivery; the worker claims the exact selected run and independently checks its runtime. Provision matching source and dependencies on dispatcher and RQ worker hosts. Missing RQ metadata can be redelivered, and duplicate delivery still requires a valid PostgreSQL claim. Worker source and dependency versions must match the frozen specification. `tinystudio lab dispatch --once` performs one explicit delivery pass; the API already owns the normal polling process.

### Durable evidence operations

```sh
tinystudio lab export RUN_ID research.zip --project research
tinystudio lab import research.zip --project restored
tinystudio lab operations --project research
tinystudio lab operation OPERATION_ID --project research
# Recover an already completed export without requesting another export:
tinystudio lab operation OPERATION_ID --output recovered.zip --project research
tinystudio lab operation OPERATION_ID --cancel --project research
tinystudio lab operation OPERATION_ID --retry --project research
```

Export prints its durable operation ID, polls for completion and verifies the downloaded ZIP before publishing the local file. Import uploads an immutable artifact, requests background import and returns its operation ID. Both survive client disconnects; find IDs with `lab operations` and reconnect with `lab operation`. `operation --output` downloads only an existing succeeded export; it never admits another export. Both download paths check streamed SHA256 and byte count against the retained artifact manifest, use an exclusive `.partial` file, and publish without overwriting an existing output, including one created while downloading.

An export wait timeout leaves the server operation active. States are `queued`, `running`, `succeeded`, `failed`, `lost` and `cancelled`; failed/lost/cancelled work requires explicit retry. Cancellation fences publication but may leave unreferenced immutable objects. An expired worker cannot publish partial import definitions or success. MCP exposes read-only `lab_operations` and `lab_operation` separately from explicit mutating `lab_operation_cancel` and `lab_operation_retry`; inspection never cancels or retries work.

All following routes are project-authorized under `/api/p/{slug}/lab`:

| Action | HTTP contract |
| --- | --- |
| Request export | `POST /runs/{run_id}/exports` with `idempotency_key`; returns `202` and operation ID |
| Request import | `POST /bundle-imports` with uploaded `artifact_id` and `idempotency_key`; returns `202` and operation ID |
| List operations | `GET /operations`; compact project history |
| Reconnect or recover | `GET /operations/{id}`, `POST /operations/{id}/cancel`, `POST /operations/{id}/retry` |
| Download completed export | `GET /operations/{id}/download`; available only after succeeded export |

Admission is not completion. Export requires a terminal run and pins its evidence identity; changes before execution cause failure rather than silently exporting a different run. Import verifies retained checksums, restores evidence and an editable definition, clears machine grants and never starts execution or binds devices. Exact checkpoint continuation still requires compatible architecture, tokenizer, data, optimizer and runtime conditions; importing a bundle alone is not a resume.

Inspect raw evidence through `lab inspect`, sequenced events and the run's artifact references. Authenticated `GET /api/p/{slug}/lab/artifacts/{id}` downloads an artifact; `/runs/{id}/records?artifact=NAME&offset=0&limit=1000` pages supported JSONL records. A bundle contains `manifest.json`, `manifest.sha256` and content-addressed `objects/<sha256>`; inspect the ZIP and original manifest independently of derived views. Record pagination and bundle inspection do not qualify C data preparation or visualization.

Bundle manifests include `artifact_origins` and `attempt_outcomes`, associating inputs, attempt outputs and retained outcome artifacts with their original attempt identities and terminal event references. Import validates those associations and preserves the original manifest beside the artifact mapping. Older bundles lacking these associations remain readable with `provenance.attempt_attribution = unknown_legacy_bundle`; import does not invent attribution or reconstruct execution attempts.


Example definition:

```json
{
  "schema_version": 1,
  "name": "Noisy sensor evidence",
  "domain": "signals",
  "engine": "synthetic.signal",
  "seed": 42,
  "mode": "online",
  "operating_mode": "simulation",
  "parameters": {"samples": 1000, "dt": 0.01, "frequency": 1, "amplitude": 1, "noise": 0.05},
  "execution": {"profile": "trusted_native", "backend": "native", "max_seconds": 60}
}
```

Uploads become immutable `{artifact_id, sha256, role}` references. Replay inputs use `source`; pretrained runs use `pretrained_model` and `snapshot`; retrieval uses `embedding_model` for a dense backend; checkpoints use `resume`, `teacher`, `model` or `checkpoint` according to the selected executor. Model directories are transported as bounded tar/zip artifacts with verified inner file manifests. Authored host paths and credentials are rejected.

## Data preparation contracts

The actual Lab parameter view uses shared application templates and controls for
field mappings, clocks, cleaning, ordered transformations, optional datasets and
matched representations. New `data.pipeline` definitions prepare evidence without
automatically requesting partitions or chats. Incomplete numeric/JSON drafts
remain editable but block saving, compatibility checks and submission. Advanced
settings share the visual definition; unknown fields and custom options survive.

`data.pipeline` accepts retained JSON/JSONL, CSV and optional Parquet evidence through `source`, `recorded` or `dataset` bindings. Original files remain separate from normalized and transformed records. Preparation alone does not request a training dataset. An explicit `parameters.partition_policy` requests one; a complete existing train/validation/test assignment is validated and preserved. Partially declared, conflicting or unknown labels fail. Source-level evaluation/truth restrictions cannot be promoted by cleaning, alias changes or the existing training serializer. Training-only fitted transforms require a validated training partition.

Partition strategy `temporal` (the default) orders whole connected groups and checks event/availability boundaries in one comparable clock, with explicitly selected purge controls. Strategy `grouped` allocates connected groups with the frozen seed, allowing independent episode clocks that restart. It reports no temporal-isolation guarantee, rejects temporal purge controls and retains per-clock bounds. In both strategies, any shared configured episode/device/scenario field connects records into one group; declared assignments cannot split it. The overlap report also exposes repeated dimensions outside the selected grouping policy.

Temporal comparability requires the full canonical coordinate: domain, units,
origin, session and any declared reset identity. Equal domain labels alone do not
qualify it. Grouped manifests key `clock_bounds` by canonical identity, with
`clock_references` resolving each key; incomparable coordinates have no aggregate
numeric bounds. An affine alignment retains the original `source_clock` in its
provenance and takes target qualifiers explicitly from the mapping's `units`,
`clock_origin`, `clock_session` and optional `clock_reset_id` (their documented
aliases also apply). Source qualifiers never silently qualify a different target.
`purge_seconds` is a duration in seconds; `purge_coordinate_duration` records its
converted value in the clock's units. Nonzero purges currently support the shared
time registry's `s`, `ms` and `us`; other units require explicit alignment first.

### Promote retained chats into existing training data

The actual Laboratory run-evidence view can create an existing TinyStudio chat
dataset and optionally freeze a training snapshot. It uses
`POST /api/p/{slug}/lab/datasets/from-run` with `run_id`, `artifact_id`, `name`,
`idempotency_key`, optional `snapshot_name`, and `min_training` (default 10,
integer 1–10,000). This returns HTTP 202 with a durable `data_bridge` operation.
Inspect, cancel or explicitly retry it through the normal operation endpoints
or history. `error_detail.status` and `error_detail.code` distinguish a deferred
limit failure from successful admission. Repeating unchanged intent with the
same key returns the original operation even after success; changed intent
requires a new key. Retry retains the admitted source fingerprints.

The operation freezes the completed run and retained artifact identity, size,
hash, role and visibility. It promotes only explicit authored chats, excludes
final-test/truth rows, and retains their artifacts and full source mapping in a
provenance manifest. A `supervised_dataset` is the pipeline's explicitly
partitioned mixed container: row partitions and source restrictions remain
authoritative. Other evaluation-bound artifacts cannot become training data by
claiming train row labels. No numerical observations are fabricated into Q&A.

Dataset rows, optional snapshot references and successful operation publication
share one fenced transaction. Persisted provenance, excluded evidence and
snapshot shards are read back with checksum verification before publication.
Persisted snapshot row counts and canonical content hashes must also match the
promoted dataset; an internally consistent storage manifest alone is insufficient.
Cancellation, lease loss or failed snapshot creation rolls back database work;
unreferenced immutable storage objects may remain. Promotion has bounded
10,000-row/16-MiB JSON capacity, independent of HTTP request time. It does not
claim streaming or large-batch support and never starts model training.

The same contract is exposed by `tinystudio lab promote-dataset RUN ARTIFACT NAME
--project SLUG --idempotency-key KEY [--snapshot-name NAME]` and the mutating,
long-running `lab_promote_dataset` tool. Existing dataset and training pages
consume the resulting dataset/snapshot without another upload. Snapshot/model
export bridge routes remain opt-in synchronous code and are not enabled in the
normal application. This source contract requires its additive migration before
running the new server. Local C workflow acceptance passed; publication remains
a separate installation step recorded in the changelog.

### Existing retrieval repositories as retained inputs

`POST /api/p/{slug}/lab/inputs/from-repository` accepts `repository` and
`idempotency_key`, returning a durable `data_bridge` operation. The corresponding
CLI is `tinystudio lab repository-input NAME --project SLUG --idempotency-key KEY`;
MCP exposes the mutating, long-running `lab_repository_input` operation. The
actual Inputs view offers capture and attachment using existing controls. Only
the returned corpus binding attaches; vector evidence and the manifest remain
separately inspectable.

Capture pins project-scoped metadata and preserves document, point, chunk and
original payload identities. Two bounded raw Qdrant scans and fresh metadata
fingerprints must agree. Pages check worker authority, and every output is
checksum-read before fenced publication. Stable string record IDs have separate
structured `source_identity`; original payloads remain in `metadata`. Limits are
10,000 dense points, 1 MiB per point and 64 MiB of serialized evidence. Incomplete
ingestion, unsupported layouts, identity/count mismatches and observed mutation
fail explicitly.

This is an immutable, optimistically validated capture of observed stability,
not an atomic database/vector-store snapshot. PostgreSQL locks cannot fence
Qdrant writers. Dense points and collection configuration are retained; HNSW
reconstruction, original encoder weights and semantic-query replay remain
unqualified. Unlabeled chunks remain unassigned. Retained consumers resolve IDs
and checksums without consulting the repository. Canonical restrictions in
bindings, rows and named provenance envelopes remain effective; explicit
retrieval overrides retain research-only/unqualified status.

### Linked inspection and leakage provenance

`GET /api/p/{slug}/lab/runs/{id}/representations?artifact_id=ID&row=N` identifies
an exact retained artifact and absolute row. Canonical observation fingerprints
and source identities must match before the inspector displays stored codecs,
literal JSONL, input bytes/text, tokens, hashes, truncation and precision loss.
Historical encodings without fingerprints remain explicitly unqualified. Equal
timestamps alone never establish a match.

`GET /api/p/{slug}/lab/runs/{id}/coverage?row_axis=scenario_id&column_axis=partition`
scans complete transformed artifacts, independently of the chart's loaded window.
Axes include fault type/severity, operating range, device, scenario, episode,
partition and units; missing metadata stays explicit. The shared ECharts heatmap
and table carry exact source links. Out-of-window selections display retained
evidence without pretending to move the chart pin. Inspection is bounded to
128 MiB, one million records and 256 values per axis, with explicit errors.
Selection/project changes reject stale responses; malformed responses show errors.

Reward and scenario-selection provenance uses explicit v1 declarations:
`rewards.evidence` and `scenarios[i].selection_evidence` list artifacts already
present in `inputs`. Each requires `artifact_id`; optional hashes, canonical
partition aliases, visibility and roles cannot weaken binding/artifact protection.
Held-out use requires the corresponding scoped override
`leakage.use_heldout_reward` or `leakage.use_heldout_scenario_selection`, its
artifact ID and a nonempty reason. Unknown references, hashes and contradictory
labels cannot be waived. Preflight reports the audit and new resolved specs
freeze it in `integrity`; historical raw specs/retry identities remain unchanged.
This audits declared provenance, not artifact contents, arbitrary code or future
use. Absent declarations do not prove absence of leakage or qualify a method.

`parameters.representations` selects up to eight named codecs on the same transformed observations. For example:

```json
[
  "numeric_text",
  "raw_bytes",
  "decoded_protocol",
  {"kind": "engineering_state", "bins": {"boundaries": [0, 1], "labels": ["<low>", "<normal>", "<high>"], "units": "V"}, "vocabulary": ["<low>", "<normal>", "<high>"]},
  {"kind": "hybrid", "description_field": "description"}
]
```

Numeric text retains the normalized floating-point value; the original file separately preserves its original decimal spelling. This follows Python's [floating-point representation](https://docs.python.org/3.12/tutorial/floatingpoint.html) and [JSON](https://docs.python.org/3.12/library/json.html) semantics. Raw bytes require recorded `wire_hex` or strictly validated `wire_base64`; numeric JSON is never substituted for a packet. Decoded protocol inputs require recorded `decoded_protocol` fields. Hybrids combine retained observations with available wire/decoded fields and an explicitly selected authored description. Engineering bins require explicit units and boundaries, with no fitting on held-out data.

Each representation records actual token IDs, input bytes, hashes, omitted fields and quantization. `max_tokens` rejects excess by default; selecting `truncation: "prefix"` records the actual retained prefix and omitted token count. Binary payloads never use replacement-character text decoding. Each record is limited to 64 KiB before tokenization; the combined export budget is 128 MiB. `representations.json` binds codec specifications, matched record identities and checksummed artifacts. Local C acceptance exercised these representations against the same retained observations, including linked coverage and exact record inspection.

Online processing is an **observation-triggered replay** of recorded availability. Event time remains in `event_time` and the original evidence; transformed `time` and `decision_at` use the explicitly labeled availability timeline. Within each signal and partition, history follows `(available_at, event_time, source_record_id)`, with a recorded ordinal and synthetic tie policy. This preserves the distinction between occurrence and processing described in the [Apache Beam programming guide](https://beam.apache.org/documentation/programming-guide/#watermarks-and-late-data); TinyStudio does not use Beam or claim real-time scheduling.

Online replay requires comparable event/availability clocks and recorded availability. Selecting `online_policy.allow_assumed_availability: true` also requires `assumption_reason`; its qualification identifies assumed rather than measured arrivals. Duplicate replacement, future-aware sources, batch fitted transforms and resampling are rejected online. Resampling requires a separately defined replay horizon before online support; offline resampling and explicitly labeled future-aware interpolation remain selectable. Availability and donor dependencies are checked after each online stage. Live acquisition and physical-control qualification belong to E.

Chat export accepts explicit authored messages for unchanged observations. A transform that changes an observation removes copied supervision from every export and records its invalidation, while preserving it in original evidence. New or changed observations need an explicit supervision mapping; preparation does not invent questions or answers.

Every transformed record exposes `observation_sha256`, binding its retained source, scientific values, timestamps, units, masks and dependencies. `parameters.annotations` maps `record_id` and that checksum to explicit `labels` and/or `messages`, with a `label_source` declaring `kind`, `actor` and evidence `reference`. A changed observation, missing record, duplicate annotation or missing source is rejected. Label kinds are human, simulator assertion, solver, reviewed teacher or imported; these are authored provenance claims, not independent proof that a target is correct. Optional target availability is retained separately from observation input. These mappings are versioned with the definition and recorded in the manifest. Software qualification uses disposable fixtures; users select and run their own research studies.

## Language initialization

`learning.language` separates three operations. Scratch initialization constructs a
Transformer or GRU and fits a byte, byte-BPE or engineering-vocabulary tokenizer
using training records only. `learning.initialization: "weights"` requires one
model-visible `initial_weights` artifact: it inherits the saved architecture,
weights and tokenizer, creates a fresh optimizer and seeded sampler, and permits
a new corpus and split. `learning.initialization: "resume"` requires one `resume`
artifact and restores optimizer and RNG state for the identical corpus, split,
objective, learning rate, CPU backend and PyTorch version. Resume after a weights
restart retains the original weight-parent identity and separately records the
immediate continuation checkpoint checksum.

Checkpoint modes restore the tokenizer before constructing sequence windows;
they never refit its merges on a replacement corpus. Explicit architecture and
representation settings must agree with the inherited checkpoint. Family,
tokenizer kind, merge references and vocabulary are validated before model
construction. Existing v1 checkpoints remain loadable; new provenance fields
are additive. A `teacher` artifact is separate and required only for
distillation. Checkpoint paths cannot be supplied in portable parameters.

Conflicting or unused checkpoint bindings fail. In particular, an older edited
definition declaring `scratch` while supplying a `resume` artifact must explicitly
select `resume` before a new submission; it no longer silently resumes. The
compatibility check validates declared mode/role consistency without opening
checkpoint files. Actual content, architecture and tokenizer compatibility are
checked by the executor. Reports distinguish fresh versus restored optimizer
state and actual initialization; successful weight restart is not reported as
training from random weights.

The actual Laboratory language editor uses the shared settings sections, form
controls and validated structured drafts. Invalid local drafts gate save,
compatibility and submission. Switching to GRU with retained Transformer heads,
or switching representations with retained vocabulary settings, requires an
explicit conflict resolution. Advanced fields and immutable bindings remain in
the same versioned definition used by the code view.

Omitted model dimensions in checkpoint modes are displayed as inherited rather
than filled with scratch defaults. Only explicit dimensions participate in
cross-field checks before checkpoint resolution. Retained inputs identify their
frozen originating definition, revision and run, so same-named checkpoints can
be distinguished before attaching them; later definition edits do not relabel
their origin. Evaluation visibility restrictions apply to inline text/message
documents and imported records alike.

`tests/test_lab_scratch_qualification.py` exercises the complete Transformer/GRU
× byte/BPE/engineering matrix through authenticated application routes and real
native child processes. Its CPU float32 checks independently count parameters,
detect unchanged embeddings/core/output weights, exclude held-out data from
tokenizer fitting, and reload transported checkpoints after the child exits.
Generation artifacts remain bound to the checkpoint checksum. This qualifies
the tested small macOS ARM64/PyTorch 2.14.1 combinations; it establishes no model
usefulness, accelerator compatibility or qualification of an unexecuted target.
Catalog entries continue to require an actual target receipt.

## Qualification boundaries

A/B source, PostgreSQL/RQ, legacy regression, package and authenticated workflow evidence are recorded separately in the changelog. B is accepted for native macOS and an independently installed Linux ARM runner inside Docker Desktop's [Linux VM](https://docs.docker.com/desktop/features/networking/), sharing the physical Mac with the native Darwin origin. Execution uses public Cloudflare HTTPS, scoped target and origin-bound Access credentials, frozen source/runtime receipts and API-only artifact transfer. The runner receives its scoped credential rather than a host checkout, Docker socket or store credentials. Independent source and acceptance reviews inspected the implementation, tests and documented boundaries; execution receipts were parent-supplied. Separate-computer restore/resume belongs to G; E's execution-isolation qualification remains open.

C is accepted through the local authenticated React workflow: retained acquisition, versioned preparation, independently checked gaps and partitions, five matched representations, linked replay/topology/coverage/3D inspection, retained repository retrieval after source deletion, checksum-verified export, and explicit dataset/snapshot promotion into an existing training job without reloading. The qualification job has zero experiments. Independent source and acceptance reviews passed with execution results supplied by the parent. D–G remain unaccepted; the single-source topology viewer fixture does not qualify multi-engine physics.

The serial native HTTP fixture in `tests/test_lab_native_workflow.py` uses actual loopback sockets, a real native child process, temporary SQLite metadata and local object storage. It exercises frozen replay inputs, CLI recovery of an existing export, bundle import and identical upload redelivery after exhausting the budget. Evidence operations use an explicit worker-function call rather than RQ delivery. This fixture does not establish PostgreSQL/RQ delivery, container execution, separate-host execution or deployed browser qualification; those have separate acceptance boundaries.


- Source tests, fixture execution, live service integration, a second physical computer, and physical-device evidence are distinct results.
- The built-in plant and graph coordinator are bounded scalar research models. They do not establish the fidelity of an arbitrary pump, motor or control system.
- A catalog entry or an installed executable does not qualify a domain adapter. Optional engines require their exact input fixture, dependency version and execution target.
- Tool-language PPO/GRPO uses a documented constrained arithmetic-call language. The independent categorical bandit backend is labeled separately. Neither establishes unrestricted agent capability.
- Time-series and fusion models are small PyTorch research paths, with input-size and supported-modality contracts. Random local embedding fixtures establish execution and provenance, not semantic quality.
- The legacy pretrained trainer does not provide exact optimizer continuation. Scratch/checkpoint methods declare their own continuation guarantees.
- Desktop/gateway benchmarks report measured inference timing and process-lifetime peak RSS with its actual scope. Model RAM, MCU flash and energy are not invented from file size or host measurements.
- Physical adapters, externally hosted engines, isolated/container execution profiles and clusters require their own compatible-target fixtures. B's same-Mac Docker runner receipt does not qualify these E/G capabilities. An unavailable or skipped fixture stays unqualified.

## Development and recovery checks

Laboratory backend checks run with `--noconftest`; pure boundary tests use fakes or temporary local stores, while PostgreSQL integration fixtures create separately owned UUID databases. They do not use the application's shared test database. `tests/test_lab_foundation.py` creates a fresh `tinystudio_lab_test_<uuid>` PostgreSQL database, applies the pre-laboratory migration chain, checks an additive upgrade and preserves a pre-existing row, then removes only that owned database. It requires local PostgreSQL database-creation permission.

```sh
.venv/bin/python -m pytest --noconftest tests/test_lab_operations.py tests/test_lab_rq_adapter.py tests/test_lab_dispatch.py
cd frontend
npm test
npm run typecheck
```

These commands exercise source and fixture checks; Docker execution and authenticated workflow receipts are recorded separately. Keep optional engines in separately pinned environments. Qualification environments used during development are disposable and are not deployment dependencies. Before enabling a runner, install and lock the selected engine pack on that host, enroll it, and inspect its advertised runtime. Capability mismatches do not silently choose a fallback or mutate a submitted specification.
