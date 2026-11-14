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

Attempt roots must be existing absolute directories, with no symlink at the final
component. The runner resolves ancestor aliases before constructing input paths,
child arguments and isolation mounts. This keeps macOS `/var` aliases consistent
with the paths visible inside Linux containers without adding filesystem grants.

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

## Hardware discovery and acquisition

Install `tinystudio[hardware]` on the execution host for the optional pySerial,
PyModbus, python-can and Paho clients. The origin API and Cloudflare transport do
not need device access. Configure endpoints and device authority in the enrolled
runner's `profile_config.engine_grants`, using a `bindings` object keyed by the
operator's binding IDs. Definitions select `parameters.binding_id`; copying a
definition cannot grant device access or replace that host configuration.

`hardware.discover` runs an explicitly selected `serial.enumerate`,
`can.enumerate` or `sunspec.scan` method in operating mode `acquisition`. The
definition editor exposes these choices and a retained report view. Inspect the
parameter schemas through `tinystudio lab hardware-methods --project PROJECT`,
the assistant's read-only `lab_hardware_discovery_methods`, or
`GET /api/p/{slug}/lab/hardware/discovery-methods`. Reading this catalog performs
no device discovery. Submit an authored definition through the ordinary Lab
workflow to execute discovery on the chosen host.

Serial enumeration reads port metadata without opening a port. CAN detection
queries only the chosen, operator-authorized providers; some providers send
discovery traffic and provider threads can finish after a detection timeout.
SunSpec scanning requires explicit candidate base addresses, register kind and
TCP unit identifier. It follows the actual ID/length chain and end marker,
preserving unknown and repeated models. No fixed paper register address, model
schema, scale factor or writable-point assumption is supplied. The report keeps
complete, partial, not-found, unavailable, failed and cancelled outcomes distinct
from whether Lab execution itself ended successfully.

The discovery report retains requested and effective settings, dependency/source
identities, clocks and model addresses. Its trace sidecar records each SDK
callback buffer's direction, length, SHA256 and selected byte prefix. Receive
buffers can overlap as the SDK accumulates a fragmented response; they are not
disjoint socket receive chunks, physical wire packets, or proof of successful
sends. No transport stream is reconstructed from them. A truncated or zero-byte
prefix is disclosed. Neither a checksum nor structural discovery establishes
physical device identity, electrical suitability, safety or control stability.

Read-only acquisition engines are `acquisition.serial`, `acquisition.modbus`,
`acquisition.mqtt` and `acquisition.can`. Each requires an exact host binding with
matching `engine` and `read_only: true`. Serial opening additionally requires
`serial_open: true`; opening and closing a port can toggle control lines. Native
serial names and explicit `socket://`, `rfc2217://` and `loop://` endpoints are
supported. CAN requires an explicit interface and channel, including a native
SocketCAN interface when configured externally. MQTT requires an exact topic
and explicit TLS choice. No acquisition client issues actuator commands.

Serial bindings optionally select `framing: {"kind": "delimiter",
"delimiter_hex": "0a"}` (the newline default) or
`framing: {"kind": "fixed_bytes", "frame_bytes": 32}`. The size is authored,
rather than a platform frame-size rule. Prefix retention consumes the logical
frame boundary independently of its byte budget. Native timeouts can split a
frame into segments, retaining frame identity, offsets, byte hashes and explicit
continuation flags. A partial frame's tail cannot become a valid new measurement.

Modbus bindings select `tcp` or `rtu_over_tcp` framing. A direct `read` declares
`kind`, `device_id`, `address` and `count`. Alternatively it declares `kind`,
`device_id` and `sunspec: {scan: {...}, point: {...}}`. The scan explicitly supplies
`base_addresses` and `device_id`; the point supplies `model_id`, zero-based
`instance`, payload-relative `offset` and `count`. The scan's register kind and
device must match the read binding. A complete, unambiguous observed chain
resolves the measurement address at transport opening. The retained
`source-binding.json` records that resolution and discovery evidence. It does
not infer engineering units, scaling, writability or stability. A trace prefix
budget does not modify protocol traffic or replace decoded register values.
Measurement trace budgets apply across SDK callback receipts per direction for
each observation operation; discovery reports disclose their per-callback
prefix budget separately. These scopes count retained callback bytes, including
overlapping buffer content, rather than unique bytes on a socket.

Acquisition retains separate raw and processed JSONL and Parquet artifacts.
Raw rows preserve available wire bytes, scope, source identities and transport
metadata; processed rows distinguish numeric observations, frame-only captures,
missing data and decoding errors. `decode: "json_object"` retains structured
payloads for later field mapping without inventing a scalar. CAN defaults to
frame-only capture; numeric decoding requires an explicit payload mapping.
MQTT evidence is an application payload, and CAN evidence is a received frame's
payload and metadata, rather than a physical wire/CRC measurement.

Explicitly failed or cancelled acquisition can retain raw JSONL, a source-binding
report and an acquisition audit through the runner's verified audit-outcome path.
Raw observations are written before optional shadow inference. A receive result
that fails clock validation before normalization remains separately retained as
unprocessed evidence. The audit distinguishes raw and processed record counts
and reports native close errors; it does not establish complete collection or a
physical stop. Forced termination, lease loss and host failure can prevent normal
publication. The host spool described below retains available bytes for separate
recovery; arbitrary partial outputs never establish a successful experiment.

Acquisition parameters may select `evidence: {flush_records: 1, fsync: true}`
(the default). A positive `flush_records` selects a batch boundary; `null` or
`"on_close"` flushes at close, and `fsync: false` omits explicit file sync. The
audit distinguishes completed newline writes, Python-buffer flushes and actual
file-descriptor `fsync` acknowledgements, including pending records and failures.
These acknowledgements do not qualify storage hardware against power loss.
Observation availability marks decoding and validation before evidence commit
and optional inference; native read completion and prediction availability have
separate timestamps.

### Retained host evidence and archive recovery

Native hardware acquisition and discovery use a persistent host evidence spool
by default. Other engines can opt in. The operator profile selects
`evidence_spool: {enabled: true, root: "/operator/chosen/location", max_bytes: null,
retention: "keep"}`. `enabled: false` explicitly selects temporary execution.
`root` defaults to the configured host evidence location; shared path resolution
uses `RuntimePaths.laboratory_evidence_spool`, and the environment override is
`TINYSTUDIO_LAB_EVIDENCE_DIR`. With R2 storage, supply an explicit persistent
location through this override, the operator profile, or the runner work root:
the ordinary R2 data directory is a disposable cache. A container or Kubernetes
location also needs persistent operator-managed storage; configuring its name
does not verify a mount's durability.

`max_bytes` is an optional logical-file-byte admission budget, not a filesystem
quota or sample-size limit. Existing oversized evidence remains inspectable and
exportable. Retention is recorded metadata (`"keep"` or nonnegative seconds);
this implementation does not automatically prune evidence. The private spool
also retains execution inputs and source files, so monitor its disk usage.

The initial public manifest is committed before execution. Confirmed process
cleanup removes the private execution context; unknown cleanup keeps it private.
A confirmed server acknowledgement is recorded separately from local cleanup.
Snapshots retain file hashes, byte lengths, newline counts and partial-tail byte
counts without repairing JSONL or interpreting those counts as observations.
An unreadable or interrupted entry appears as a listing diagnostic and does not
hide other retained attempts. A spool entry never grants authority to replay an
attempt.

Inspect and export on the device host without a user project credential:

```sh
tinystudio lab spool-list --root /operator/chosen/location
tinystudio lab spool-inspect SPOOL_KEY --root /operator/chosen/location
tinystudio lab spool-snapshot SPOOL_KEY --root /operator/chosen/location
tinystudio lab spool-export SPOOL_KEY capture.zip --manifest-sha256 SNAPSHOT_SHA256 --root /operator/chosen/location
```

Export checks the selected snapshot and fails if files change. It contains only
`manifest.json` and the declared `evidence/` files, preserving exact bytes.
It excludes private context, execution source, staged inputs and runtime logs.
Treat evidence content itself according to the experiment's data policy.

On the researcher client, use the ordinary project API credential and transport
(including configured Cloudflare Access settings):

```sh
tinystudio lab recover-archive capture.zip --project research --idempotency-key recovery-1 --preview-only
tinystudio lab recover-archive capture.zip --project research --idempotency-key recovery-1
tinystudio lab recovered-evidence RUN_ID --project research
```

The client verifies the archive and streams its files to project artifact storage.
The current project input endpoint accepts at most 512 MiB per file; larger
uploads return HTTP 413. The client obtains a server preview and saves an exact
attach request in a private durable journal beside the archive
(`.recovery-request.json`). `--request-file` selects
another journal location. Retain it and retry the same command and key after an
uncertain response. A prepared request is retried unchanged; a completed journal
returns the previously acknowledged receipt even if the archive is unavailable.
It is not a fresh check of server object availability. For an intentional
additional association, choose a different key and a separate `--request-file`;
an existing journal remains bound to its original request.

The server independently checks project/run/attempt/target/specification identity,
artifact hashes, sizes and JSONL byte facts before committing an append-only
`server.evidence_recovered` association. Recovery is labelled
`operator_recovered_evidence`; it does not renew a lease, change run state or
results, attest the runtime, establish a physical stop, or register a model.
The run page displays these receipts separately, including hashes, original
producer claims and verified byte downloads. Portable bundles carry the receipts,
producer manifests and evidence as historical operator-supplied material.

Collection choices `samples`, `duration`, `timeout`, `interval`, `stale_after`,
`max_frame_bytes` and `max_queue_frames` are authored parameters. A null sample
count or duration removes that stopping bound; zero samples or duration produces
empty evidence without opening a transport. Host `limits` can impose explicit
resource budgets. Requests exceeding those budgets fail before opening a device;
they are never silently cropped or treated as a statistical sample-size rule.
The run retains the selected plan and operator budgets separately.

These adapters support local protocol and emulator verification. Wokwi RFC2217
and Renode SocketCAN still require the simulator's own bridge/session setup;
their cloud/runtime availability is separate from a successful local transport
test. The `hardware.session` bridge below connects an authored supervisor to
parent-owned command authority; acquisition streams are not automatically joined
to a controller or model.

## Standalone command gateway

The operator-provisioned gateway runs separately from the Lab API and read-only
acquisition adapters. Lab `hardware.session` uses the parent-owned bridge
described below; neither route establishes end-to-end control qualification.
Standalone release,
cancellation and observed lease expiry follow an operator-selected lease policy;
an optional host watchdog checks that policy in the background. Host scheduling
and SDK waits provide no hard real-time, control-stability or physical-stop
guarantee.

The existing fenced protocol 1 remains available when the binding omits
`command_profile`. It uses typed `parameters` with operator-selected units and
limits. Its device application must implement persistent epochs, command-ID
deduplication, expiry checks and matching application acknowledgements. A serial
write, ordinary Modbus response or MQTT PUBACK cannot substitute for that
application protocol. Neither profile qualifies physical effects or device
fencing merely by accepting a binding.

Native profiles explicitly select `tinystudio.native-command-profile.v1` and
accept serial/SCPI or TCP writes and queries, ordinary Modbus register/coil
operations, MQTT publications, CAN frames or an operator-installed custom
adapter. Serial/TCP queries require selected delimiter or fixed-byte read framing.
Native payloads are validated against the selected operation and protocol ranges;
values are never clamped or converted into typed engineering controls. Byte data
uses explicit `utf8`, `hex` or `base64` encoding, with no appended delimiter or
newline. Device JSON/text inside `data.value` remains literal, including malformed
JSON for fault-injection experiments; the HTTP envelope itself rejects duplicate
keys and nonfinite numbers.

For an operator-chosen local MQTT broker and topic, a native binding can be:

```json
{
  "endpoint_id": "bench-mqtt",
  "operator_id": "bench-operator",
  "transport": {
    "kind": "mqtt", "host": "127.0.0.1", "port": 1883,
    "topic": "owned-bench/commands", "allow_dns": false, "tls": false
  },
  "command_profile": {
    "format": "tinystudio.native-command-profile.v1",
    "allowed_operations": ["mqtt.publish"],
    "max_request_bytes": null, "max_response_bytes": null,
    "max_capture_bytes": null, "max_identity_chars": null
  },
  "send_timeout": 2,
  "max_lease_seconds": null,
  "local_stop": null,
  "lease_policy": {
    "on_release": "revoke_only",
    "on_expiry": "revoke_only",
    "on_cancel": "local_stop",
    "watchdog_interval_seconds": null
  }
}
```

These values select one example endpoint and wait, rather than a recommended
device configuration. Profile byte budgets and identity-length budgets default to
`null` (Python `None`); there is no implicit scientific or sample-count ceiling.
Byte budgets can be zero. Request/response bounds reject excess traffic or
results; capture bounds retain a disclosed prefix and hash without changing the
authored command. `GET /command-profile` reports their precise byte scopes.
An optional positive `max_lease_seconds` bounds requested lease durations;
`null` still requires a positive acquired lease and an unexpired command.
`capability_claims` and any `ownership_key` remain operator claims.

The optional top-level `lease_policy` shown above gives the defaults: release
and expiry revoke host authority without a device write, while cancellation
selects `local_stop`, preserving the previous cancellation behavior. Each action
selects exactly `revoke_only` or `local_stop`; the latter uses the fixed
operator-configured stop payload, never a payload supplied by a release caller.
For a native binding with `local_stop: null`, a selected stop records
`not_configured` and sends nothing. Successful host revocation or stop delivery
does not establish the equipment's physical state.

Provision binding and token-descriptor files as operator-owned regular files,
mode `0600`, without symbolic or hard links, and the canonical journal root as
operator-owned mode `0700`. The token descriptor contains exactly `endpoint_id`,
`operator_id`, `token_sha256` and an explicit `permissions` list. Keep the bearer
secret with the calling operator. Use one canonical local journal root for an
endpoint; locks in that root do not coordinate copied journals on other roots or
hosts. With operator-chosen paths, start the standalone listener using:

```sh
python -m tinystudio.lab.gateway_cli \
  --binding-file ./gateway/binding.json \
  --token-file ./gateway/token.json \
  --journal-root ./gateway/state
```

The default listener is loopback. Other listeners require `--binding-mode tls`
with certificate/key, or `reverse-proxy` with trusted proxy addresses and
external TLS termination; endpoint bearer authentication still applies.
`--config-max-bytes N` and `--request-max-bytes N` select optional private-file
and HTTP-body budgets; absent explicit selections, the corresponding
`TINYSTUDIO_GATEWAY_CONFIG_BYTES` and `TINYSTUDIO_GATEWAY_HTTP_BYTES` environment
settings apply, defaulting to `None`. Zero is a valid budget; no fixed 64 KiB
configuration or request limit is imposed. Importable `GatewayFilePolicy`,
`GatewayRequestPolicy`, `GatewayStoragePaths` and `GatewayApiPaths` live in
`tinystudio.lab.gateway_configuration`; `NativeCommandProfile` lives in
`tinystudio.lab.command_profiles`. Lease policy and watchdog classes are
`GatewayLeasePolicy` in `tinystudio.lab.gateway_lease_policy` and
`GatewayWatchdog` in `tinystudio.lab.gateway_watchdog`.

Device credentials belong in a separate private `--runtime-secrets-file`, not the
public binding or payload. MQTT supports `username`/`password` and TLS fields
`ca_certs`, `certfile`, `keyfile`, `key_password`; TCP supports those TLS fields
plus `server_hostname`. TLS credentials require selecting TLS in the binding.
Other built-in native drivers and the default legacy driver reject nonempty
runtime secrets. An injected transport factory used with a credentials file must
explicitly accept the `runtime_secrets` keyword; custom native adapter
constructors receive `binding` and `runtime_secrets` separately. An incompatible
factory signature fails before listener startup.

Authenticated `GET /state`, `/journal` and `/command-profile` inspect the host
journal and selected profile; `/command-profile` requires the token's `state`
permission and exposes the read-only payload schema, digest, budgets and
qualification scope. State also exposes the effective `lease_policy` and
`lease_watchdog` status. `GET /lease-events` uses `journal` permission and
`offset`/`limit` paging to inspect lease actions and their retained receipts. `POST /acquire`
selects `endpoint_id`, `owner` and positive `ttl`; retain its returned `epoch` and
lease expiry. `POST /heartbeat` renews that owner/epoch. Each `POST /commands`
supplies `endpoint_id`, `owner`, `epoch`, a new `command_id`, absolute wall-clock
`expires_at` within the lease, and exactly one of legacy `parameters` or native
`payload`. For the binding above, one payload is:

```json
{
  "operation": "mqtt.publish",
  "data": {"encoding": "base64", "value": "AAH/DQo="},
  "qos": 1,
  "retain": false
}
```

| Native receipt status | Recorded scope |
| --- | --- |
| `delivered_unverified` | Transport/adapter reports delivery; application outcome unverified |
| `protocol_accepted` | Protocol acceptance report, such as MQTT QoS acknowledgement |
| `response_received` | A protocol response was received, without proving the requested physical effect |
| `application_reported` | Custom adapter application claim, retained as `adapter_claim` |

Built-in receipts keep `device_application_confirmed: false`; every native
receipt keeps `physical_outcome: "unverified"`. Custom-adapter file hashes describe
module bytes observed after import; they do not attest the executed code.
A matching completed command ID
returns its retained receipt without another delivery, even after its lease or
command expiry. This authenticated lookup requires the same immutable request,
including owner, epoch, expiry and payload. Reusing the ID for different intent
fails. Pending, unknown or consumed IDs never authorize automatic replay.

`POST /release` requires an explicit token `release` permission and a body with
`endpoint_id`, stable `request_id`, `owner` and positive, strictly integer `epoch`
(not a boolean). New releases require the exact current lease. An identical
completed request returns its immutable lifecycle receipt without revoking or stopping a
later owner; a changed owner/epoch under the same request ID fails. A clean
release can return the host to `idle` only after in-flight work has settled and
there are no pending/unknown commands or unresolved lease actions. Inspect the
receipt's `host_state`; a completed action alone does not imply availability.
Expiry, cancellation and uncertain outcomes remain `recovery_required`.

Lifecycle receipts use `tinystudio.gateway-lease-event.v1`, retain the exact
lease identity and termination action, and reference the stop command and its
delivery receipt when one exists. They keep `physical_outcome: "unverified"`.
Ownership revocation commits together with its lifecycle intent; the action is
durably claimed before a stop can be sent. After restart, a committed but
unclaimed pending action can be resumed by a watchdog tick. An action already
marked executing becomes unknown and is never automatically replayed.

`POST /stop` uses the bound operator and a stable `request_id`, revokes host
authority and attempts the exact configured `local_stop` payload. With
`local_stop: null`, it records `not_configured` and sends no device stop.
Neither outcome proves that equipment stopped. In `recovery_required`, explicit
`POST /reconcile` evidence must include `acknowledge_unverified_outcomes: true`.
Native reconciliation records an `operator_attestation`, advances the host epoch
and consumes uncertain command IDs while preserving receipts and unknown
physical outcomes. It supersedes old unresolved lease actions before a new
lease can be acquired. It performs no command replay or physical-state
verification.

A positive finite `watchdog_interval_seconds` starts an ASGI-owned watchdog;
`null` creates no background thread. With the watchdog disabled, gateway calls
can still observe expiry, and library callers can invoke `watchdog_tick()` to
process pending actions. The worker waits the selected interval before its first
tick and after each completed tick. This is a host polling cadence, not a
hard deadline, SDK timeout guarantee or hardware timer that survives a gateway
process outage. Default expiry behavior still performs no device stop unless
the operator selects `on_expiry: "local_stop"`. That action selection alone does
not enable a background worker; a watchdog or explicit library tick must run it.

Library `GatewayWatchdog.close(timeout=None)` requests shutdown and joins the
worker before returning. An optional nonnegative caller-selected timeout bounds
that join; the returned status reports `alive: true` if a tick is still running.
The API uses the default join before closing the gateway journal. A blocked
native call can therefore delay shutdown; a requested shutdown is not evidence
of physical stopping.

Verification uses owned software endpoints, native loopback/emulator fixtures and
controlled failures. It establishes no physical-device, Wokwi/Renode session,
mounted-volume power-loss or end-to-end Lab control qualification.

## Hardware session supervisors

`hardware.session` runs a recorded decision stream or an authored Python
supervisor through an attempt-scoped IPC client. It requires native POSIX
execution with profile `trusted_native`. This is trusted code under the same
host UID, not process or credential isolation. The parent owns the gateway,
lease renewal, attempt fencing and cleanup. The child receives an attempt-scoped
submission capability, without operator gateway credentials or lease-management
APIs in its supervisor context.

Submitted native operations are sent to the selected binding in every operating
mode. The authored supervisor controls which proposals it submits or retains
without sending; the mode label itself does not redirect traffic to a simulator.

On the execution host, provision `profile_config.engine_grants.hardware.session`
with `native_execution: true` and a `bindings` map. Each binding contains a
native `gateway_binding` from the standalone reference and an absolute
operator-chosen `state_directory`. Optional `lease_seconds` and
`renew_interval_seconds` default to 30 and 5 seconds; they are host choices,
not scientific requirements. `max_request_bytes` and `max_response_bytes` are
optional IPC frame-body budgets, defaulting to `null`. `evidence` selects the
parent's `RecordWritePolicy`. For example, with operator-chosen identities,
broker and state directory:

```json
{
  "native_execution": true,
  "bindings": {
    "chosen-session": {
      "gateway_binding": {
        "endpoint_id": "bench-mqtt",
        "operator_id": "bench-operator",
        "transport": {
          "kind": "mqtt", "host": "127.0.0.1", "port": 1883,
          "topic": "owned-bench/commands", "allow_dns": false, "tls": false
        },
        "command_profile": {"format": "tinystudio.native-command-profile.v1"},
        "local_stop": null
      },
      "state_directory": "/operator/chosen/session-state",
      "lease_seconds": 30,
      "renew_interval_seconds": 5,
      "max_request_bytes": null,
      "max_response_bytes": null,
      "evidence": {"flush_records": 1, "fsync": true}
    }
  }
}
```

Keep SDK credentials in the separate host configuration
`profile_config.engine_secrets.hardware.session`, shaped as
`{"bindings": {"chosen-session": {"username": "OPERATOR_SUPPLIED", "password": "OPERATOR_SUPPLIED"}}}`
for MQTT authentication when selected. `HardwareSessionBinding.select_runtime_secrets`
selects only that binding's credentials for the parent transport. They are not
authored definition parameters and are not supplied in the child context.
Gateway and IPC byte budgets cover different scopes; neither is a dataset or
decision-count limit.

In the Lab authoring view choose **Hardware session**, enter the operator binding
ID and attach the supervisor source in Inputs. The optional `supervisor` role
fills a genuinely new unique attachment into the selector; the backend accepts
the explicit artifact ID regardless of its role. Generic parameter JSON remains
editable for both supervisor kinds. A Python selection uses:

```json
{
  "binding_id": "chosen-session",
  "supervisor": {
    "kind": "python", "artifact_id": "UPLOADED_SOURCE_ID", "callable": "program"
  },
  "supervisor_parameters": {},
  "evidence": {"flush_records": 1, "fsync": true},
  "ipc_timeout_seconds": null,
  "artifact_visibility": "evaluation"
}
```

Replace the source ID with its attached frozen input reference and SHA-256.
Recorded supervisors instead select
`{"kind": "recorded", "artifact_id": "UPLOADED_SOURCE_ID"}` and consume JSONL
rows containing exactly `decision_id`, native `payload` and positive finite
`valid_for_seconds`. There is no fixed row count. Python source can have a
downloaded filename without `.py`; the engine copies and verifies its bytes
before loading the selected callable, without timestamp-based bytecode reuse.
It invokes `program(session, context)`. The public context contains authored
`definition`, `seed`, arbitrary `supervisor_parameters`, frozen `inputs`, a
hash-verified `input_paths` map, `output_dir` and the public `profile`.

The supervisor can load a chosen frozen model from `context["input_paths"]`, run
rules, model inference or software-in-the-loop logic, and issue native queries
for observations through `session.submit(decision_id, payload,
valid_for_seconds=...)`. Commands use that same method; `.profile` is a defensive
public description. The client exposes no acquire, heartbeat, release,
reconciliation or stop authority. There is no implicit controller, prompt
priority, prose-to-JSON conversion, model retry, value clamp or stability
guarantee. Python returns `None` or a finite JSON object; its report and any
returned records/metrics are retained without inferring observation validity.

`evidence` selects local submission/receipt flush and fsync policy independently
of the parent's policy; `ipc_timeout_seconds` is positive finite or `null` for
no selected client timeout. Success artifacts use the authored
`artifact_visibility` (`model`, `evaluation` or `truth`, default `evaluation`).
Failed/cancelled local outputs remain audit-only evaluation evidence for later
explicit curation. Visibility never rewrites raw source, decisions or receipts.
The engine rechecks the retained supervisor source after execution. A changed
regular file is failure evidence with its actual hash and `verified: false`;
missing or nonregular source is recorded, with unsafe replacements removed only
from the engine's known source slot so other audit files remain exportable.
The engine retains the source, raw supervisor report, local timed
submission/receipt JSONL and manifest. The parent independently retains admission,
delivery and lease metadata and freezes its evidence after child cleanup.
Parent cleanup preserves the first observed termination cause: normal completion
selects `on_release`, server/manual cancellation and the selected wall deadline
select `on_cancel`, and lost lease authority selects `on_expiry`. Known elapsed
deadlines are observed before accepting a normal-exit cause. Cleanup uses the
exact owner and lease epoch, including an ordinary command's durably recorded
revocation; it never replays an uncertain stop or stops a newer owner's lease.
File fsync acknowledgements and transport receipts do not prove physical effects,
power-loss durability or stopping.

Owned verification exercised serial loopback, the actual native Runner child
and local IPC. It establishes no physical-device, Wokwi/Renode session or CUDA
qualification. The same definition remains authorable through existing Lab JSON,
CLI and tools; target capabilities and explicit operator bindings still apply.

## Data preparation contracts

The actual Lab parameter view uses shared application templates and controls for
field mappings, clocks, cleaning, ordered transformations, optional datasets and
matched representations. New `data.pipeline` definitions prepare evidence without
automatically requesting partitions or chats. Incomplete numeric/JSON drafts
remain editable but block saving, compatibility checks and submission. Advanced
settings share the visual definition; unknown fields and custom options survive.

`data.pipeline` accepts retained JSON/JSONL, CSV and optional Parquet evidence through `source`, `recorded` or `dataset` bindings. Original files remain separate from normalized and transformed records. Preparation alone does not request a training dataset. An explicit `parameters.partition_policy` requests one; a complete existing train/validation/test assignment is validated and preserved. Partially declared, conflicting or unknown labels fail. Source-level evaluation/truth restrictions cannot be promoted by cleaning, alias changes or the existing training serializer. Training-only fitted transforms require a validated training partition.

Field mappings accept existing flat source-column names or explicit selectors:
`{"pointer": "/decoded_payload/temperature"}` follows RFC 6901, and
`{"literal": "researcher-selected label"}` supplies a constant without stringifying
its value. Missing pointer targets fail unless the mapping explicitly chooses
`"missing": "null"`. The source editor preserves structured selectors and
advanced mapping fields.

`parameters.resources` selects optional `max_rows`, `source_bytes`,
`per_source_bytes`, `pipeline_stages` and `window_records` budgets. Omitted choices
inherit the execution host's explicit limits, or remain unbounded when the host
has selected none. Explicit null cannot bypass a finite host budget. A budget
failure is an execution/resource error, rather than scientific validation, and
does not silently discard source rows. The resource receipt records selected
limits, operator limits and actual usage. Decoding, sorting, grouping, lineage
and transformations currently use memory; removing fixed dataset ceilings does
not establish out-of-core execution.

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

### Import curated observations into a study

Retain acquisition bytes and curate their decoded records with `data.pipeline`.
The study editor can then preview an explicit JSONL artifact projection and
import its selected rows. Choose the protocol revision, physical source lines,
unit IDs, conditions and named data fields. Repeated units remain repeated;
counts describe rows and do not establish independence or sample size.

A projection requires `selection: {"all": true}` or an explicit
`selection: {"lines": [1, 3]}` list, plus `unit_id`, `condition`, `data`, and
`number_encoding`. Each field selector is `{"pointer": "/your/field"}` or
`{"literal": "your value"}`. Pointers follow [RFC 6901](https://www.rfc-editor.org/rfc/rfc6901).
`observed_at` is optional; `kind` defaults to `observation` and accepts authored
record kinds. Choose `decimal_strings` to preserve source numeric tokens
exactly as strings, or `json` for native JSON numbers with disclosed binary64
rounding. Literal selector values retain their authored types.

`tinystudio lab study-import-preview STUDY_ID REQUEST.json --project PROJECT`
reads a request containing `artifact_id`, `revision`, and `projection`. Preview
writes no ledger records or artifacts. It validates the complete source and
returns the exact source hash, projected-row digest, normalized mapping,
implementation identity, and up to five display examples. All selected rows
contribute to the count and digest. The imported record retains its physical
source line, raw-line hash, decoded-row hash, source artifact hash and projection
digest. Bulk source verification happens once per import.

`study-import` accepts that request plus `preview_digest` and an
`idempotency_key`. Observations and their receipt publish in one database
transaction. Retry an uncertain response with the unchanged request and key;
a completed retry returns its original receipt, including after external source
loss. A different key represents an intentional additional import. A changed
source, mapping, or protocol fingerprint requires a new preview. Researcher
record kinds remain open; the importer reserves its internal publication IDs.
The assistant exposes the same two steps through `lab_study_import_preview`
and `lab_study_import`. Shared route objects supply the service/client paths;
the study response supplies action links to the browser.

Freeze the resulting study boundary for `statistics.analyze`, then explicitly
select the outcome, groups, units, repetitions/missingness policy and inference
method. The original artifact, imported ledger rows and analysis selection
remain separate inspectable evidence.

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

## Retained retrieval queries

The existing `retrieval.index` engine supports `parameters.operation: "build"`
(the default) and `"query"`. Laboratory authoring exposes both operations through
the existing experiment dialog. Query requires one model-visible
`retrieval_index` artifact binding and rejects fresh corpus inputs or inline
records. It inherits the retained index mode when mode is omitted; an explicitly
selected mode must match. Semantic/hybrid queries also require the original
immutable embedding-model binding.

The runner authenticates the downloaded artifact, validates its document
identities, source metadata, restrictions and vector dimensions/normalization,
then republishes the identical index bytes from its output directory. Query
traces retain `origin_index` with the original artifact ID and SHA, the corpus
digest and original hit identities. New queries never refit embeddings or rebuild
the corpus. A retained research override remains visible and unqualified.
Retrieval receives all declared corpus bindings so restricted evidence is checked
instead of silently disappearing at dispatch; model-training visibility rules
remain separate. This retrieval operation produces evidence, not model-generated
answers. Independent model/retrieval composition remains part of D.

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
compatibility check validates authored language selections without opening
checkpoint files or importing the training runtime. The same validator runs at
execution: unsupported objectives and updates, conflicting family/dimension or
representation settings, and provably invalid Transformer dimensions fail
before training. Omitted checkpoint dimensions and teacher/checkpoint tokenizer
settings remain inherited; their actual content compatibility is checked by the
executor. Nonempty retrieval selections on this language trainer are rejected
until explicitly composed with frozen retrieval evidence; a compatible engine
does not imply that it consumes every authored field. Reports distinguish fresh
versus restored optimizer state and actual initialization; successful weight restart is not reported as
training from random weights.

The actual Laboratory language editor uses the shared settings sections, form
controls and validated structured drafts. Invalid local drafts gate save,
compatibility and submission. Switching to GRU with retained Transformer heads,
or switching representations with retained vocabulary settings, requires an
explicit conflict resolution. Advanced fields and immutable bindings remain in
the same versioned definition used by the code view.

## Retained-teacher language distillation

Select `learning.language`, the student's Transformer or GRU architecture, and
`objective: "distillation"`. In Inputs, attach a retained
`tinystudio.learning.v1` checkpoint with the `teacher` role and model visibility.
The input library includes completed-run checkpoints with their originating
definition/revision/run, so a teacher does not need to be downloaded and uploaded
again. The student can use a different family and smaller dimensions, with context
no larger than the teacher's supported context.

For scratch/random students, **From teacher** in Representation inherits the
teacher's frozen byte, BPE or engineering tokenizer. The explicit choice removes
supported representation/vocabulary constraints while preserving architecture,
inputs and unrelated settings. Explicit representation, BPE fitting budget and
engineering vocabulary fields remain compatibility constraints; they never fit a
new tokenizer during distillation. A BPE fitting budget is distinct from its
actual token count. Weights/resume students retain their own checkpoint tokenizer
and must match the teacher exactly. Unsupported retained custom tokenizer fields
remain visible as configuration errors rather than being discarded.

The loss is `(1-alpha)*CE + alpha*T^2*KL(teacher/T || student/T)`, averaged over
flattened token rows. Temperature must be finite and positive, and alpha between
zero and one. This follows the [PyTorch distillation tutorial](https://docs.pytorch.org/tutorials/beginner/knowledge_distillation_tutorial.html)
with [token-row batchmean reduction](https://docs.pytorch.org/docs/2.14/generated/torch.nn.functional.kl_div.html).
The frozen teacher runs in evaluation mode without gradients. Its full tensor
state, input-file checksum and exclusion from the student optimizer are checked
before publishing even a cancelled checkpoint. Teacher/tokenizer/context/schema
errors fail before constructing the optimizer or creating an output directory.
In-process calls reuse the existing serialized training context for RNG and
thread restoration. Student checkpoints contain their own inference dependencies;
the teacher reference remains provenance, not an inference-time file requirement.

Retained metric rows carry an `optimizer_step` clock with `step` units and a
separate session identity. The existing interactive replay/trend workbench can
therefore inspect loss, cross-entropy and distillation KL in 2D or 3D. These are
algorithm-step trajectories, not physical timing measurements or loss landscapes.
Exact resume retains the recorded step clock and continues its step indices;
weight restart creates a new session.

New checkpoints retain current-training-partition text and complete-record hashes.
Teacher matches against student validation/final-test documents block training
under `teacher:heldout_training_overlap`. An intentional contamination control
can use the existing `research_overrides` entry with that exact `check_id`, a
nonempty `reason` and `approved_by`; the finding remains recorded and execution
qualification is `research_only`. Legacy complete-row hashes can establish exact
record matches, but cannot detect unchanged text after metadata changes. Inline
record IDs alone never establish a cross-corpus match. Missing matches do not
prove clean teacher weights or inherited tokenizer history: coverage remains
`unknown` and the exposure report remains `unverified`.

Checkpoint, downloadable training manifest and qualification output retain the
teacher identity, unchanged state hashes, tokenizer identity/size, actual forward
counts and separate exposure report. Existing held-out flags explicitly concern
`current_student_optimizer_only`. Exact distillation resume also pins teacher
hash, temperature, alpha and loss semantics; older checkpoints lacking the new
loss contract require a restart from weights rather than a claimed exact resume.
Software qualification does not establish that a distilled student outperforms
its teacher or baseline. This path accepts TinyStudio language checkpoints;
other teacher formats require their own trainer adapter and qualification.

## Pretrained objectives and weight updates

`learning.pretrained` uses the existing TRL/PEFT trainer with two independent
axes: `learning.objective` selects `sft`, `causal_lm` or `dpo`;
`learning.update_strategy` selects `full`, `lora` or `qlora`. Legacy
`parameters.training_type: "dpo"` and `update_strategy: "dpo"` retain LoRA DPO
semantics. Conflicting explicit aliases fail. Editing either selector in the
actual Lab form canonicalizes both axes and preserves the other selection.

SFT uses `messages`. Continued pretraining uses `text` strings, including their
original whitespace and Unicode, with `parameters.data_format: "raw_text"`
(inferred from `causal_lm` when omitted). It tokenizes without chat templates or
automatic special tokens, appends EOS if absent, then truncates on the right at
the selected context length. Raw UTF-8 hashes, token hashes, EOS insertion and
truncation counts appear in the manifest. Assistant-only loss is incompatible
with this format. The original snapshot remains retained; final-test rows are
excluded from optimization and validation. DPO validates every pair: a prompt,
a final assistant response and a distinct nonempty `rejected` string are required.
Malformed pairs fail instead of being dropped.

Full DPO loads a separate, frozen reference from the same immutable model.
LoRA DPO uses the starting model with adapters disabled as its reference; an
already adapter-bearing starting model requires an explicitly retained merged
model first. The executor checks unchanged reference parameters and optimizer
separation, then records the reference hashes. The output manifest identifies
`objective`, `update_strategy` and `output_kind` independently so full DPO reloads
as a full model. Existing v1 manifests and legacy Job/Experiment configuration
remain readable.

Device and dtype are selected independently. Explicit run fields override their
respective machine defaults; an omitted dtype still inherits the configured
dtype. A CPU run that selects bf16 retains that choice. Actual kernel and trainer
behavior is established by measurement. The trainer checks actual policy/reference
and prepared-batch placement and records observed dtypes.
The optional `qlora` package extra installs bitsandbytes into the chosen
environment; installation leaves runtime compatibility unprobed.

`runtime.qualify` measures NF4 training and retained reload on the selected Lab
target/backend/profile. Inspect `tinystudio lab runtime-methods`, then author an
explicit objective, device, dtype and fixture with `runtime-define`. Existing
preflight, submit and inspect commands execute and retain the measurement. The
deterministic local fixture records its model, tokenizer, data, seed and settings;
`fixture=retained` uses supplied immutable model/snapshot artifacts. A completed
probe may report unavailable prerequisites, failed measurements or verified
compatibility. Verification applies only to its recorded selection.

NF4 execution choices live in `NF4ExecutionPolicy`. The default preserves library
behavior. An explicitly selected `nf4_execution.cpu_inference=portable` disables
the discoverable bitsandbytes CPU packing optimization and records that setting.
Frozen-base checks still apply. Reload comparisons default to identical dtype and
bytes; an `allclose` policy requires explicit `rtol` and `atol` and retains measured
errors. Runtime versions are provenance, and the optional baseline in
`requirements/lab/qlora-linux-arm64-py312.txt` remains available for reproducing
that environment. Native hosts and configured containers need their own actual
probe evidence; a successful host probe does not qualify a container.
Image Python commands resolve inside that image. The runner uses the shared
`RuntimeTools.container_python` setting (`TINYSTUDIO_CONTAINER_PYTHON`) or an
explicit profile `python` override, without substituting a host interpreter path.

New QLoRA outputs use `tinystudio.pretrained.v2` / `quantized_adapter`: they retain
the packed NF4 base, nested quantization state, adapter, tokenizer, checksums,
observed precision and three live-policy prediction probes. Reload verifies
retained files, quantization state, precision and actual placement, then measures
prediction agreement using the declared comparison policy. A retained policy
follows the bundle into later serving; an explicit loader policy overrides it.
Undeclared policies use bitwise equality. Different runtime metadata is recorded
alongside actual verification. The original floating-point base is never
substituted. Legacy job serving, merged export and refinement reject these
outputs until those consumers have a compatible quantized execution path;
durable configuration/status markers cover running and failed checkpoints too.
Missing retained state is an error. Existing nonquantized v1 outputs remain
readable. Training-time retrieval selections remain unsupported on this trainer
and fail visibly. Frozen retrieval is independently composable with retained
model generation through `evaluation.compare`, including the matched-method
matrix described below.

Native runners execute these operations in child processes. In-process trainer
calls are serialized while they own PyTorch thread and RNG state, which is
restored on normal and exceptional exits. Cancellation is checked at optimizer
steps, progress/log callbacks, before saving and before/after bundle publication;
cancelled results expose no partial model artifacts. Pretrained output currently
supports weight restart, not exact optimizer continuation. These local software
fixtures establish execution behavior, not model quality or hardware portability.

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

### Frozen model and retrieval evaluation

The existing `evaluation.compare` engine retains `operation=precomputed` as its default. Select **Generate and evaluate** in the Laboratory experiment dialog to use `operation=generate`. Bind exactly one model/checkpoint artifact and a `cases` artifact with evaluation or truth visibility. Optional `retrieval_index` and original `embedding_model` bindings reuse the immutable index; `compare_retrieval=true` requires an index and runs the same model on both arms of each case. All model and index bindings remain model-visible; expected answers do not enter the generation session.

Cases use a versioned JSON envelope:

```json
{"format":"tinystudio.evaluation-cases.v1","cases":[{"source_id":"sensor-case-1","episode_id":"episode-7","partition":"test","prompt":"Report pressure as a decimal in Pa.","query":"pressure reference","expected":"-12.75","unit":"Pa"}]}
```

This mode currently scores numeric cases. It preserves the entire completion and uses the existing strict numeric/unit validators; prose, malformed numbers and empty responses remain failed cases rather than being stripped or omitted. A paired report compares numeric errors on the same held-out identities. A failed prediction does not mean the execution failed, and scientific qualification does not require the retrieval arm to outperform the baseline.

Generation parameters are `max_new_tokens` (1–512), explicit `device` (CPU by default), and `compare_retrieval`. Optional `retrieval` settings are `top_k`, exact `filters`, and `fields` (default `["text"]`) identifying the content fields permitted in the prompt. Each prompt uses the recorded `tinystudio.evidence-question.v1` format. Selected fields are JSON-encoded in retrieval order; no document metadata or expected answers are automatically serialized into the prompt. Missing selected fields fail explicitly. The baseline uses the same format with an empty evidence section. Queries default to the case prompt and must be nonempty.

The attempt-owned model session reuses existing scratch and Hugging Face/retained full/adapter loaders, verifies the outer artifact and retained manifests, loads locally, and releases tensors and extracted files on completion or failure. It records exact input/output token IDs, effective context capacity and output reservation. Unknown context capacity and prompt-plus-output overflow fail; evidence and questions are never silently truncated. Incompatible devices or retained quantization runtimes fail without fallback. A supported loader is not qualification of every model family or hardware target.

The run publishes unchanged index bytes, query traces, prediction records and `generation-evaluation.json`. Known retrieval restrictions and overlaps with case source/episode identities remain visible and research-only. Missing lineage and overlap with the model's training corpus are explicitly not established; a frozen index is not a claim of leakage-free evaluation. Existing run records, artifacts and the baseline comparison evidence expose these results. QLoRA generation qualification is scoped to actual selected-runtime receipts. `tests/test_lab_qlora.py` exercises native CPU SFT, continued-pretraining and DPO retention, live-logit replay and serving reload with the explicitly selected NF4 execution policy. `tests/test_lab_qlora_workflow.py` requires its separately configured container profile for the registered generation path. Each runtime and profile needs its own completed evidence. Context overflow and cancellation clean up owned session resources. This does not qualify another device/runtime or establish useful model accuracy. Broader text/policy metrics and complete D application acceptance remain separate gates.

## Numeric partitions and control environments

The actual Laboratory Parameters editor provides grouped controls for control policies, time-series models and numeric/text fusion. Control settings select PPO, offline DQN or behavioral cloning, native or TorchRL execution, the bounded environment/reward, MLP dimensions and independent evaluation seeds. Forecast settings select the supported family, context, horizon and target feature; fusion settings select regression/classification, modalities and target key. Source evidence and checkpoints attach through the existing Inputs workflow. Uncommon parameters remain in Definition JSON. These controls expose the existing learners; they do not qualify a physical controller or a general image/audio encoder.

Incomplete numeric/JSON edits remain in their original editor and disable Save, compatibility checks and submission. Dismissing a newly created saved experiment with further edits retains that editor instead of remounting it as a clean saved page. Existing aliases and unsupported retained values remain visible for explicit correction; selecting a runtime does not silently fall back to another target's capabilities.

Forecasting, fusion and offline-policy readers retain immutable source identities and inherited custody. Explicit partition aliases use the shared partition contract; a test/truth row never becomes a training example or a fitted-normalization row. Explicit partitions are preserved, and detected episode/device/scenario connections cannot cross grouped partitions. `parameters.group_by` can add isolation fields. Separate `validation` inputs require evaluation visibility and remain validation data. Offline policies retain those partitions and learn only from training transitions; their independent outcome comparison uses the declared control environment, not a claim of predictive accuracy on withheld transition labels.

Forecast windows remain within one episode. Unlabelled single-series input retains a chronological holdout; explicit `parameters.temporal_split: true` permits train→validation→test chronology inside an episode, including causal training history for validation. Test/truth observations never enter window context or targets. Future-to-training reuse, reversed time, incomplete group metadata and ambiguous partition labels are rejected. Prediction artifacts include target source identities and partition labels. Classification records pair predicted/target labels, retain explicit target indices, and include the class mapping for the legacy encoded target array. Existing checkpoints remain readable for inference; exact continuation requires matching protected corpus/split semantics, so earlier checkpoints with different partition construction are rejected rather than silently changing their experiment.

Numeric and fusion runs additionally publish `numeric-evaluation.json` (`tinystudio.numeric-evaluation.v1`) after their original prediction and training artifacts. Forecasts compare each validation window against its last context target-feature value repeated across the forecast horizon. Fusion regression uses per-output training-target means; classification uses the most frequent canonical training label, breaking ties lexically. Training means and majority labels are conventional baselines described by [DummyRegressor](https://scikit-learn.org/stable/modules/generated/sklearn.dummy.DummyRegressor.html) and [DummyClassifier](https://scikit-learn.org/stable/modules/generated/sklearn.dummy.DummyClassifier.html); this implementation uses the existing runtime, without adding scikit-learn. Baseline fitting never uses validation, final-test or truth targets. Retained context IDs, fitting IDs/counts, checkpoint/corpus hashes and case identities make that distinction inspectable.

Numeric scores use the learner's float32 target domain; `raw_target_json` separately preserves original source values and label types. Existing classification semantics use `str(target)`, so numeric `1` and string `"1"` are one canonical learned class, not two. MAE/MSE are unweighted means across cases and output components, with separate per-component metrics; overlapping forecast windows remain separate cases. Units are not established by this numeric training contract, so the report does not assign physical meaning to a mixed-component aggregate. Counts, widths, source identities, inference outputs and computed metrics must be valid and finite; retained raw-input inference must match the reloaded validation path. Cancellation can retain valid measured outputs while comparison completion remains false.

The existing baseline view loads only the checksummed numeric comparison artifact. It provides alternate Plot/Records views, output or horizon selection, point and keyboard inspection, and assistant evidence selection. Numeric points use explicit validation-case ordinal coordinates, without inventing temporal continuity; classification preserves quoted labels in a table. Historical prediction-only runs remain downloadable but do not acquire fabricated baselines. These are validation comparisons, not final-test qualification or a promise of model superiority.

Control, offline DQN and imitation select `parameters.environment: {"kind":"control","runtime":"native"}` or `runtime: "torchrl"`. The latter requires TorchRL and TensorDict on the chosen target and executes the real EnvBase reset/step contract. Owned sessions close on completion/error and checkpoint the environment configuration, dynamics, observation and RNG. Conflicting learner/environment observations in a resume checkpoint are rejected before restoration. Unsupported environment choices fail admission; no runtime fallback is inferred.

After reload, control policies and a quantized PD controller execute in separate environments with matched held-out seeds. `parameters.evaluation.seeds` supplies 1–64 unique nonnegative seeds excluding the training seed; the default uses training seed +1000003/+1000004. The baseline applies `target - position - 2 * velocity` with a ±0.05 neutral deadband. `control-evaluation.json` retains actual transitions, independently calculated tracking errors/constraint violations, rewards, paired initial states and unchanged model-state hashes. Cancellation produces an incomplete comparison. These results qualify the bounded local control fixture; they do not establish physical performance or require the policy to beat the controller.

The retained control comparison opens an interactive **Trajectories** view. Choose one episode/seed and compare policy/baseline positions and their recorded targets, or absolute tracking error. The horizontal axis counts completed transitions (`step + 1`); physical units are not recorded by this fixture. Each point describes the shared plant state after the action, including for joint multi-agent actions. Click a point or use arrow keys/Home/End to inspect its original transition and select it as assistant evidence. **Records** is an alternate table in the same workspace, preserving the selected evidence. Missing steps break lines without interpolation; invalid numbers, unsafe identities and ambiguous duplicate transitions are excluded from the graph and remain inspectable in Records. Pair counts describe retained step coverage, not a performance qualification. Zoom resets when changing episode/seed or metric and stays intact during point selection.

### Matched method comparisons

In the Laboratory experiment dialog, select **Independent evaluation / paired comparison → Compare methods on shared cases**. Add 2–16 named methods, choose the comparison baseline explicitly, and attach one frozen `cases` artifact. Model methods select an attached `model` or `checkpoint`; several methods can reuse the same artifact. Enable retrieval per method to compare base, specialized, adapter, retrieval and combined configurations against identical case identities. All enabled methods use the same frozen index and retrieval configuration. Method labels are names, not evidence of model type: the retained report records the actual model manifest and hashes.

A conventional method executes the authored affine formula `intercept + Σ(coefficient × feature)`. It does not fit coefficients or infer units. Coefficient keys must exactly match every case's feature keys. Features and coefficients use raw scalar coordinates, and the output is interpreted in each case's explicitly declared unit. For example, coefficients `{"reading":"-2"}` and intercept `"0.25"` produce `"-2.25"` from `{"reading":"1.25"}`. Other conventional algorithms require their own implementation and qualification.

The existing `tinystudio.evaluation-cases.v1` JSON envelope accepts an optional `features` object. Feature values, coefficients and intercepts must be decimal **strings**, bounded to 256 characters, 128 significant digits and an adjusted exponent within ±128; there may be 1–32 named features. These strings avoid JSON floating-point loss. Affine multiplication/addition and output serialization are exact for the accepted finite decimals. Matrix expected answers accept decimal strings, exact JSON integers or typed quantities with those values; JSON floating-point expected values are rejected. Existing feature-free generation prompts remain unchanged. When features are present, every model receives the same sorted JSON feature block alongside its authored prompt; expected answers and evaluator metadata remain outside that projection.

The portable operation is `parameters.operation: "matrix"`, with `methods`, `baseline_method`, `max_new_tokens` and `device`. Each model method has `id`, `label`, `kind: "model"`, `artifact_id` and an explicit `retrieval` boolean. Each conventional method has `kind: "affine"`, `coefficients` and `intercept` instead. Preflight checks references, roles, visibility and declared dependencies. Execution verifies actual model/index/case contents, placement and context capacity. Conventional-only matrices do not require Torch; model methods load sequentially through the existing owned inference session.

The versioned `tinystudio.method-matrix.v1` aggregate retains complete method receipts, predictions, exact case identities/hashes, per-method evaluations and comparisons against the selected baseline. Child evaluation reports retain distinct artifact names and the `method_evaluation` role; the aggregate alone uses `independent_evaluation` and is uploaded after record sidecars. Full evidence stays in artifacts; the run response carries a compact summary. Records retain an exact-string `expected_display` alongside the untouched original target so large JSON integers remain readable in browser clients. Per-method evaluation examples carry both source and episode identities; individual answer correctness is separate from whether a baseline/candidate pair can be compared. Invalid model completions remain failed cases, and invalid/missing pairs suppress comparable aggregate metrics. A matrix is complete only with successful run execution and exact method/case coverage. Cancellation, an uploaded file, or a method label cannot establish qualification or neural superiority. The existing precomputed, single-model and control reports remain readable in the same comparison view.

## Retained tool-policy comparisons

Both categorical tool-bandit PPO/GRPO and autoregressive tool-language PPO/GRPO
retain a versioned `tinystudio.tool-evaluation.v1` comparison. Each authored
held-out task is compared with the **incoming policy before this attempt's
updates** and the final policy loaded from the retained checkpoint. On resume,
the incoming baseline is the resumed policy, not the original random model.
The tasks are frozen with the definition. Duplicate held-out tasks and overlap
with training tasks are rejected using operation and numeric operand identity.
The bounded local arithmetic tool registry remains the execution boundary.

The report binds the checkpoint checksum and retains actual tool names,
arguments, results, errors and policy versions. Requested-operation correctness
is measured independently of the training reward. Greedy evaluation runs without
gradients, verifies unchanged weights, and restores module modes and RNG state.
A cancelled evaluation retains unavailable cases as missing outcomes and cannot
claim a complete comparison. Accuracy denominators count executed cases; missing
cases are not counted as correct, wrong, or tool failures. Wrong answers remain
valid evaluation evidence and do not make a successful execution fail.

The existing baseline-comparison view loads the checksummed retained report,
shows matched incoming/final calls and outcomes, and exposes the selected original
trace through the shared evidence inspector and assistant selection. Historical
unversioned tool reports remain downloadable but are not silently promoted to
qualified comparisons. Training-update records never stand in for held-out tool
outcomes. The existing language artifact retains its `initial`, `final` and
`evaluation_policy` fields; the versioned comparison envelope is additive.
