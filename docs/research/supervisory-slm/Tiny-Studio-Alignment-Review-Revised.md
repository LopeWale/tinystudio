# Tiny Studio: paper alignment and experimental implementation review

Review date: 12 November 2026. Revised after the user clarified the product direction.

**Product direction:** Tiny Studio is a general experimentation workbench. The paper is an editable example study, not a source of platform-wide limits. Provide configurable composition, tracking/data custody and extensible statistical planning/analysis, including sample-size and power calculation with assistant support. Researchers choose mechanisms, bounds, models, baselines, sample counts and inference/decision rules. Assumptions must be recorded, not imposed.

**Document corrections completed:** corrected PDF, lab guide and interactive Explorer are now under `/workspace/tinystudio/docs/research/supervisory-slm/`. Findings about stale original attachment content below describe the reviewed originals; use the corrected editions for subsequent work. Technical runtime findings remain observations at the reviewed commit. The five selected corrections did not change laboratory execution behavior.

Repository: [LopeWale/tinystudio](https://github.com/LopeWale/tinystudio), default branch `master`, commit `0be1cfac0117102218cd4577307dff70c350a7ad` (7 November 2026). Findings apply to that commit, not to an unreviewed local branch or deployed instance. The repository predates the supplied 11 November lab guide. This revision adds corrected research documents and product principles locally; runtime application source was not changed. Nothing was committed or pushed.

## 1. Overall assessment

**Tiny Studio has a substantial foundation for managing this research, but it does not yet implement the supervisory control system needed to test the paper end to end.** The central gap is composition: model inference, scientific data preparation, simulations, device acquisition, command authority, and evaluations exist as separate capabilities. The paper requires those capabilities to execute together in a causally timed, independently protected physical control loop.

This is a meaningful distinction from saying the repository lacks training or experimentation features. It already has immutable experiment submissions, scoped runners, retained artifacts, checksums, raw/derived data separation, partitions, model learning, distillation, evaluation, and linked scientific views. Reuse those. The general product need is extensible experiment composition, mechanism/adaptor interfaces, data tracking and statistical computation. A supervisory-control study can then be authored using those mechanisms; paper-specific parameters and protocols must remain user-owned configuration.

At the reviewed commit:

- A laboratory `physical_control` run is explicitly rejected.
- The co-simulator supports five built-in scalar component types, with no language-model, compiled-controller, solver, or policy-bus component.
- Engineering-state representations are stateless bins rather than the paper's hysteresis-bounded HTM.
- A standalone command gateway implements useful authority and delivery semantics, but is neither the paper's compiled guardrail nor integrated with laboratory physical execution.
- Modbus/MQTT protocol fixtures are not a cross-protocol supervisory gateway.
- There is no checked-in C/C++ Tier 1 firmware, PSO/GA supervisory implementation, CANopen/SunSpec stack, semantic diary, or PV experiment suite.
- General prediction evaluation does not implement the six hypotheses' physical metrics, trial matrices, or statistical acceptance rules.

**None of H1–H6 can currently be established end to end using the repository alone.** Several pieces can be exercised and extended immediately. This review does not establish whether a separately deployed installation has additional capabilities or evidence.

## 2. Documents reviewed and how to use them

The PDF defines six research hypotheses and the intended three-tier architecture. The lab guide supplies concrete protocols, bench choices, baselines, counts, and proposed acceptance criteria. The HTML Explorer is an explanatory visualization: its JavaScript illustrates selected thresholds and diagrams, rather than executing a research testbed or a model.

Use the corrected PDF as the architectural claim, the lab guide as a draft experimental specification, and the Explorer as educational material. Resolve the discrepancies below before generating training targets or test fixtures. The paper contains proposals and example mechanisms, not experimental results establishing them.

### Corrections and unresolved experimental specifications

1. **Explorer checksum and interlock behavior are stale.** Its `BYTES` array at line 809 ends in `C5 C4`; its text calls that valid. An independent CRC-16/Modbus calculation for `01 03 00 20 00 02` gives `C5 C1`, consistent with the supplied PDF and guide. The Explorer's default `reject` mode at lines 694–710 drops an out-of-range current and labels that the §3.3 pipeline; the corrected PDF instead specifies clamp-and-commit for valid numeric out-of-range values and drop/fallback for malformed output. Do not copy either stale behavior into acceptance tests.

2. **SunSpec controls require model discovery.** As the guide recognizes, the paper's fixed “40021 Active Power Limit” address is not a portable SunSpec control location. Discover the model chain, locate Model 123, apply scale factors and enable controls, and declare the chosen base-address convention. Distinguish Modbus RTU CRC-framed ADUs from Modbus TCP MBAP framing; the paper's eight-byte example is RTU, not a TCP ADU.

3. **A prompt cannot guarantee safety.** “Maintain cell safety above all else” is an experimental policy instruction, not a deterministic priority mechanism or stability proof. H2 should measure whether the model proposes the right arbitration, while Tier 1 independently protects the plant. Retain both proposal violations and committed-output violations so a protective interlock cannot hide a failed cognitive policy.

4. **Clamps do not prove closed-loop stability.** Individually valid gains can destabilize a plant; H1's additional harmful-but-in-range class correctly exposes this. Add a separately specified state-dependent safety mechanism, validated gain region, parameter slew constraints and/or runtime assurance monitor where needed. Record this as an extension beyond the paper's simple clamp, and run clamp-only versus strengthened-interlock ablations.

5. **H1's “zero” and “instantaneous” claims need bounded definitions.** Zero failures in 1,800 injections is finite empirical evidence; the rule-of-three upper bound is about 0.17% under its assumptions. It is not a universal guarantee. Distinguish fault occurrence, fault detection, and fallback application. A watchdog of 1.5 supervisory periods cannot provide one-control-period fallback from the beginning of an undetected stall. Its requirement can be one period *after detection*, with a separately reported detection delay. A 1 kHz current observer also cannot exclude every microsecond excursion without additional instrumentation/bandwidth analysis.

6. **The HTM table is incomplete as an executable state machine.** The paper specifies voltage `stable` within ±0.5 V/s and spikes above +5 or below −5 V/s, leaving intermediate trends unspecified. Temperature derivatives between equilibrium and runaway also need semantics. Define transition equality, cold-state exits, missing/stale signals, initialization, reset, timestamp gaps, and overlapping tokens. Retain the guide's voltage deadband as a guide-authored choice. Specify FFT windowing, leakage correction, fundamental amplitude, and THD calculation: 2,048 samples at 200 kS/s do not contain an integer number of 10 kHz cycles.

7. **Resolve dwell semantics and test expectations.** Three observed high samples spaced 100 ms apart can span 200 ms from the first high sample; the guide allows 300 ms ±100 ms. Freeze the sampling/entry convention. A signal that alternates above/below the threshold may never satisfy entry dwell, so “enters once” in a hover test requires an explicit initial critical excursion or initial critical state. Separate magnitude chatter from genuine derivative/noise changes.

8. **“16-bit IDs” do not mean 16-bit language-model tokens.** Binary fieldbus IDs need a versioned dictionary and framing. The model's tokenizer may split a readable state label across several tokens. Measure wire bytes and actual model prompt token counts separately. Three uint16 IDs are six payload bytes before timestamps, framing, dictionary versions and reliability overhead.

9. **The pre-cooling mechanism may be falsified.** The guide already points out that reduced electrical extraction can leave more absorbed solar energy as heat. Treat this as a testable physical question, not a behavior to reward in training labels. Distinguish panel heating from reduced converter/inverter losses. Net energy over the entire intervention and recovery window is the relevant yield outcome.

10. **Two H6 bench details need revision.** Three panels in series do not generally offer three independent string-current measurements for locating a weak panel: they share the series path, subject to bypass branches. Use per-panel voltage, known bypass state, separate characterization or additional sensing to make the fault identifiable. Also, three specified panels have nominal summed open-circuit voltage about 60.9 V, conflicting with the guide's shared “every bus at 24 V or less” rule. Redesign that test or explicitly specify and independently review a different rated rig before implementation. This is an inconsistency in the supplied experiment specification, not a discovered repository behavior.

11. **Some chapter statistics are still drafts.** H1 needs a specified non-inferiority estimand and confidence-bound rule; a median comparison alone is not a complete non-inferiority procedure. H4's GA branch has no comparably complete acceptance protocol. H6 paired-day and arm-swap designs need clear matching and sufficient independent days. Define primary endpoints, missing trials, multiplicity and stopping rules in advance. Do not use thousands of correlated ticks as thousands of independent trials.

12. **Latency feasibility must be measured early.** The guide's illustrative 290 prompt tokens/s and 33 generated tokens/s imply about 2.55 s for a 300-token prompt plus a 50-token response, before transport or validation. Those rates are for a particular model/quantization and are not a measurement of this repository or the guide's Q4_K_M setup. They show why 100 ms–1 s should be tested, not assumed. Use short structured outputs and cached context where supported; report a slower working cadence as a limitation of H1 on that target.

## 3. What the repository already provides

References below use the inspected revision. Paths identify implementation boundaries, not evidence that every deployment or optional backend is qualified.

| Area | Existing implementation | Relevance and limitation |
| --- | --- | --- |
| Product and services | `api/`, `services/`, `db/`, `worker/`, `inference/`, `core/`, `training/` | Existing projects, accounts, datasets, snapshots, jobs, serving and provenance should remain the product backbone. Generic model development is useful but is not a supervisory controller. |
| Frozen experiments | `lab/contracts.py:70`, `lab/service.py:270`, `lab/models.py` | Versioned definitions, immutable resolved specs, revisions, inputs, attempts and events are an appropriate research record. `objectives` is extensible authored data; it is not an implemented Tier 3 objective optimizer. |
| Execution and authority | `lab/runner.py`, `lab/execution.py:47`, `lab/profiles.py`, `lab/dispatch.py` | Scoped execution, source/runtime verification, cancellation, lease recovery and optional isolation. This is job scheduling, not a hard-real-time loop. |
| Source identity | `lab/service.py:55`, `lab/execution.py:11` | Current source manifest covers Python files. Extend it to C sources, headers, firmware, model dictionaries and other new runtime assets. |
| Data preparation | `lab/data.py`, `lab/clocks.py`, `lab/partitioning.py`, `lab/research_partitions.py`, `lab/leakage.py` | Retained originals, explicit units/missingness/availability, grouped and temporal partitions, exposure checks. These are strong building blocks for unbiased experiments. |
| Representations | `lab/representations.py:71` | Numeric text, raw bytes, pre-decoded protocol, engineering-state bins and hybrid input. No stateful dwell/deadband HTM, derivative/FFT decomposition or uint16 wire dictionary. |
| Simulation | `lab/signals.py:37`, `lab/cosim.py:17`, `lab/domain_adapters.py` | First-order scalar plant, proportional controller, delayed transport, sensors, fixed-step branches, plus optional external engines. No integrated buck/thermal/PV/multi-node supervisory testbed. |
| Acquisition | `lab/acquisition.py:114`, `:137`, `:175`, `:312` | Serial, Modbus and MQTT telemetry with host timestamps, raw payloads and non-actuating shadow results. Default shadow predictor at `:272` is a scalar numeric checkpoint path. |
| Command gateway | `lab/gateway.py:139`, `lab/gateway_api.py:81`, `lab/ownership.py` | Useful standalone endpoint ownership, epochs, command identity, expiry, typed limits, journaling, local stop, acknowledgements and explicit recovery. Physical fencing remains unqualified; no laboratory run binding. |
| Training | `training/train.py`, `lab/compatibility.py`, `lab/learning.py`, `lab/distillation.py` | Full/LoRA/QLoRA/DPO and scratch learning; actual temperature-scaled KL distillation for supported retained teachers. Existing frozen teacher path uses the local scratch checkpoint/tokenizer contract, not arbitrary large-teacher-to-Qwen logit distillation. |
| Serving/deployment | `serving/runner.py:249`, `lab/model_session.py`, `lab/deployment.py:202` | Hugging Face and retained model inference, JSON checking/retries, CPU benchmark and portable Python package. No integrated GGUF/llama.cpp export and target supervisory runtime. |
| Evaluation | `lab/evaluation.py:118`, `:170`, `:284`, `lab/evaluation_matrix.py`, `lab/control_evaluation.py` | Numeric and packet checks, leakage, paired scalar predictions, model/affine method comparisons, bounded control trajectories. No H1–H6 campaigns or physical/statistical acceptance engine. |
| UI | `frontend/src/features/lab/`, `frontend/src/api/`, `app/StudioContext.tsx` | Authoring, run inspection, replay, clocks, representations, baselines, scientific plots and shared assistant evidence. Extend this production React route rather than the historical `tinystudio/ui/pages` implementation. |

The repository's own `docs/laboratory.md` explicitly treats A–C as accepted boundaries and D–G as unaccepted work, even where experimental source exists. Preserve its distinction among implementation, availability, actual execution and qualification. The catalog's `implemented: true` is not a claim that H1–H6 have been delivered.

## 4. Concrete code-to-paper misalignments

### 4.1 Physical execution is deliberately unavailable

[`lab/preflight.py:37`](https://github.com/LopeWale/tinystudio/blob/0be1cfac0117102218cd4577307dff70c350a7ad/tinystudio/lab/preflight.py#L37) fails `physical_control`; [`lab/service.py:329`](https://github.com/LopeWale/tinystudio/blob/0be1cfac0117102218cd4577307dff70c350a7ad/tinystudio/lab/service.py#L329) rejects submission because no qualified endpoint binding exists. Adding a UI toggle or deleting this rejection would not supply the missing system.

Needed: a run-bound local supervisor session, trusted endpoint grants, connection to the existing gateway's ownership protocol, retained command/acknowledgement records, and device-side enforcement. Expose an explicit run-to-endpoint mechanism with operator-provisioned authority and visible capability status. Do not infer a blanket physical-experiment restriction from this paper. Device-specific operational protections and authentication remain separate from researcher-selected study assumptions.

### 4.2 The co-simulator cannot host the proposed architecture

[`lab/cosim.py:17`](https://github.com/LopeWale/tinystudio/blob/0be1cfac0117102218cd4577307dff70c350a7ad/tinystudio/lab/cosim.py#L17) allows `signal`, `plant`, `controller`, `transport`, `sensor`. Its controller is proportional; its plant is scalar first-order dynamics. At lines 214–223 it requires offline simulation, rejects external input artifacts/pipelines and has no hardware/acquisition gateway. External engine adapters run as separate bounded operations, not as model-coupled components of this graph.

Needed: a supervisory coordinator with independent Tier 1, telemetry, inference and policy clocks; real causal delivery of completed inference; compiled controller and vector plant components; a model component; policy and solver services; and explicit external engine coupling if used. Existing delay, availability, algebraic-loop and record-identity rules should carry forward.

At a 20 kHz control rate, a ten-minute trial contains 12 million Tier 1 ticks. The current graph's 100,000-step limit and one-million-record/component-work bounds are unsuitable for a full high-rate campaign. Keep every control integration step while logging supervisory rows, faults and triggered high-rate windows efficiently. Never downsample the control computation merely to fit the viewer.

### 4.3 The gateway is valuable but has a different contract from the guardrail

[`lab/gateway.py:59`](https://github.com/LopeWale/tinystudio/blob/0be1cfac0117102218cd4577307dff70c350a7ad/tinystudio/lab/gateway.py#L59) validates typed bounds in Python and rejects out-of-range values; it permits a nonempty subset of allowlisted fields. That is a reasonable generic command contract, but differs from the guide's complete-frame COMMIT/CLAMPED/DROPPED semantics. `ModbusMailboxTransport` at line 510 is a custom JSON mailbox, not a SunSpec Model 123 write API.

Needed: a separate strict supervisory proposal schema and shared compiled interlock. Do not globally turn the generic gateway's rejection into clamping. Layer domain-specific clamp-and-commit before the existing authority boundary and revalidate on Tier 1. The gateway remains useful for ownership, freshness and recovery; it cannot replace local control safety or watchdog execution.

### 4.4 Engineering-state bins are not H3

[`lab/representations.py:145`](https://github.com/LopeWale/tinystudio/blob/0be1cfac0117102218cd4577307dff70c350a7ad/tinystudio/lab/representations.py#L145) encodes engineering state using `bisect_right` over the current value. It has no persisted per-signal state or entry dwell. `decoded_protocol` expects already decoded fields; it is not a protocol parser.

A direct probe using 44.9, 45.1 °C alternation produces nine transitions in ten samples. That is expected from this implementation and incompatible with the proposed filtered HTM if mistaken for it. Add a distinct stateful HTM transform and wire codec; keep stateless bins available as the H3 baseline.

### 4.5 Present JSON serving changes the failure/timing experiment

[`serving/runner.py:249`](https://github.com/LopeWale/tinystudio/blob/0be1cfac0117102218cd4577307dff70c350a7ad/tinystudio/serving/runner.py#L249) implements JSON mode through instruction, extraction, validation and up to three generation attempts, not constrained decoding. On success it returns reserialized JSON. That is useful for normal serving, but a supervisor must preserve every original attempt and include retries in latency; H1 needs faults to reach the interlock rather than be silently repaired upstream.

Needed: an explicit research inference policy: single attempt for fault handling, separate constrained-output arms, raw response bytes, parsing verdict, all timing, no unbounded retries and rejection of late results. Support llama.cpp/GGUF as a pinned backend, with capabilities distinct from the existing Hugging Face backend.

### 4.6 Generic evaluations cannot score the whole control decision

The standard generation-case loader in `lab/evaluation_generation.py:75` normalizes expected answers as numeric quantities. The method matrix executes model methods or authored affine formulas, and `compare_baselines` calculates matched scalar MAE. `validate_packet` can compare supplied structured fields, but that does not automatically make the model-generation matrix a structured control-decision evaluator.

Needed: a versioned control/solver/protocol action record and validators for required fields, enums, safety priority, state transition, deadlines, delivery, policy age, actual committed state, and plant outcomes. Add a campaign evaluator above these record-level validators. Keep the existing numeric/packet and lineage evaluators as reusable checks.

### 4.7 Learning fixtures are not the paper's control hierarchy

`lab/rl.py:94` controls a bounded position/velocity fixture with three actions; its multi-agent mode averages actions into one shared actuator. The PD held-out comparison in `lab/control_evaluation.py` is real execution within that fixture, but not PID parameter synthesis, independent energy nodes, global-envelope arbitration or PSO/GA supervision. Likewise, time-series forecasting is not a supervisory SLM.

Do not count these paths as implemented H2/H4. They can support future auxiliary predictors or ablations; they do not have to be expanded before the core six-hypothesis testbed works.

## 5. Editable example-study mechanisms and candidate endpoints

| Hypothesis | Current reusable pieces | What must be implemented | Guide's proposed evidence (researcher-configurable) |
| --- | --- | --- | --- |
| H1: cognitive/deterministic separation | Frozen runs, timing benchmark, gateway, faults/cancellation infrastructure | Shared C PID/interlock/frame codec/watchdog, buck plant, asynchronous SLM supervisor, state-dependent safety assessment, STM32 port and observers | 1,800 class 1–5 injections per model configuration, separately reported 100 harmful-in-range cases, fallback detection/application timing, committed and physical limits; 50 paired model/baseline load-step sequences |
| H2: local/global arbitration | Prompts, scoped projects/runners, acquisition, clock/provenance, paired evaluation foundations | Independent node sessions, thermal plant, policy envelopes/TTL/heartbeat/reconnect, local fallback, oracle and rule baseline | 360 randomized ten-minute trials; zero forbidden committed heater duty, model proposal safety reported separately, ≥95% oracle energy on prescribed aggressive cases, bounded dropout/recovery and Wilson agreement interval |
| H3: HTM | Raw signals, availability-aware transforms, representation inspection | Derivative and THD features, complete token table, dwell/deadband state, uint16 dictionary/frame codec, send-on-change and resynchronization | Six guide scenarios, 30 repeats/arm; chatter and detection latency, component independence, actual wire bytes; 500 paired raw/token decision cases with non-inferiority scoring |
| H4: runtime solver supervision | Artifact-backed engines, model sessions, paired cases and branch provenance | PV shaded-string model, PSO, rule supervisor, SLM bounds/fitness/solver switching, annealing; 50-node battery GA objective/dynamics/constraints | Fixed 100-development/400-final shading split; success within 1% global optimum, evaluations and full wall-clock cost, paired tests against both rule and plain solver; separately preregister GA endpoints |
| H5: cross-layer protocols | Offline Modbus/MQTT packets, live read-only acquisition, standalone journaled writes | RTU/TCP parser/CRC, SunSpec discovery, CANopen/SDO, MQTT dual-output plan, link metrics/eBPF path, routing, loss-aware compression and semantic diary | Six independent subreports: 1,000 frames, 200 demand responses, 50+50 diagnoses, 100 reroute ramps, 1,000 compression records, 2,000 legitimate/200 injected writes, 20 blackout runs; emulated versus hardware results separated |
| H6: energy mechanisms | General energy engines and retained evidence | pvlib-based weather/panel/shading/tracker models, thermal/IV instrumentation, actuator/readout drivers, daily crossover protocols, overhead accounting | Per-mechanism report; bypass 40 cases, cloudy/clear tracking and backtracking day campaigns, thermal falsification trial; EMI explicitly untested until a separate calibrated inverter rig exists |

### H1 details that are essential

Build `guardrail.c`, `pid.c`, `frame.c`, and watchdog/control-session logic as a platform-independent core compiled into a PC SIL library and STM32 firmware. Use strict proposal parsing at Tier 2 and a fixed binary command at Tier 1. Validate length, version, identity, sequence/freshness, fixed-point overflow and CRC before atomic register changes. JSON NaN/type/missing-field errors belong to the proposal parser; a fixed-point frame cannot itself contain an IEEE NaN.

Freeze the complete parameter set, scaling, byte order, CRC variant and covered bytes, enum widths, sequence wrap rules, tick wrap/reset handling, validity lifetime, fallback gains and recovery conditions. The guide's 32-byte format is not yet a complete specification; choose a layout that actually accommodates all selected fields. Log proposed, clamped and committed values separately.

The MCU keeps controlling when the model, Python process, gateway, database or network stops. Watchdog processing must not wait for any of those systems. Treat parameter updates as atomic snapshots, handle PID integral/derivative state on fallback, and avoid discontinuous unsafe transfer. Round 1 measures simulated behavior; hardware interrupt budgets, ADC behavior and scope-observed boundary compliance remain Round 2 evidence.

### H2 details that are essential

A Policy Envelope needs identity, version, source, target scope, creation/expiry semantics, objective weights and constraints. A heartbeat must establish current authority, not merely deliver an old retained MQTT message. Reject stale/reordered envelopes and define reconnection behavior. Keep Tier 3 coordination separate from the local supervisor's survival mode.

The rule baseline in this chapter already solves its stated deterministic decision table. Matching it is evidence that model arbitration works on this task, not that a model is necessary. Richer hidden-context or ambiguous degradation experiments can be added as separately declared extensions. For energy comparisons, run independent baseline/oracle plants under the same initial conditions and disturbance schedule: evaluating a different action on the model arm's altered trajectory is not a clean counterfactual yield measurement.

### H3 details that are essential

Keep state per signal/node/session; reset only at explicit boundaries. Retain feature inputs and window timestamps, exact transition reasons, source observations and token dictionary version. Feature derivatives must use elapsed time and available history. Separate fast local safety sensing from intentionally delayed semantic state transitions.

Use lossless framing for safety fields and explicit data-quality states. Delta transmission requires initial snapshots, sequence numbers and periodic recovery snapshots; otherwise one lost state-change packet can leave Tier 3 permanently wrong. Include this overhead in compression results.

### H4 details that are essential

The model proposes solver bounds and settings; deterministic code validates them and the numerical optimizer performs the search. Bound restrictions can exclude the real optimum, so record optimum coverage and recovery. Preserve solver state and compare all arms with matched seeds, numerical budgets, stopping criteria and underlying plant curves.

Count prompt construction, queueing, every model call and solver-switch cost in time-to-success. Include timeouts and unsuccessful trials rather than comparing only successful survivors. Compare bounding-only, weighting-only and switching-only arms if attributing the mechanism. Freeze the evaluation objective even when an arm changes search fitness weights. For the GA branch add battery SOC dynamics, power/thermal/reserve constraints, tariff inputs, 24-hour objective, hard feasibility checks and meaningful classical scheduling baselines; the existing OR-Tools knapsack fixture is not this scheduler.

### H5 details that are essential

Parsing frames, validating checksums, discovering SunSpec models and encoding CANopen SDOs should be deterministic. The model interprets context or proposes an authorized plan. A 200-command test needs evidence of both endpoint applications and proper scale factors, not simply two generated byte strings. Define partial-application behavior: a Modbus success plus CAN timeout is a partially applied plan, not full success or an instruction to blindly resend both.

Collect obtainable link metrics first, and label them honestly. eBPF cannot by itself observe analog cable impedance or encrypted application plaintext. Hardware transceiver measurements, driver counters and application instrumentation provide those distinct sources. Test the eBPF-specific claim separately; user-space counters alone establish a narrower result.

For compression compare identical records as JSON, gzip JSON, CBOR and intent encoding. Preserve safety quantities exactly using residual numeric fields or another lossless representation; coarse semantic states cannot reconstruct exact original temperatures. Record encoded bytes, framing/dictionary overhead, decode failures, semantic accuracy and compute cost separately. A model-generated reconstruction is not inherently lossless.

The Semantic Diary needs durable timestamps, critical events, sequence/ACK state, bounded compaction and idempotent replay. It is not simply a prose summary or the existing command journal. Compare it with a real persistent QoS 1 session and evaluate state at the guide's checkpoints.

For spoof detection use the guide's simple “foreign write” rule. If it equals the model, report that. Proactive rerouting is supported only relative to an observed precursor; reaction to synthetic corruption does not establish physical EMI prediction.

### H6 details that are essential

Record weather inputs by publication/availability time, not hindsight weather. Freeze panel parameters, geometry, ground coverage ratio, albedo and controller configuration. Use a fixed reference panel and daily arm swaps with explicit matched analysis. Irradiance captured through an Isc integral is not automatically harvested electrical energy; report that narrower metric separately from measured Wh. Include servo, cooling, inference, communications and converter energy when claiming net gain.

Resolve the series-string instrumentation and voltage-limit issues before the bypass campaign. The bench only proxies some proposed mechanisms: masked panels do not establish micro-crack diagnosis or sub-string reconfiguration; two-row tracking does not establish arbitrary farm-scale coordination. EMI cancellation, EMI suppression and recoverable useful bus energy are separate quantities and need a different rig. Keep mechanism 5 untested here.

## 6. Example supervisory-study architecture

Tiny Studio supplies general control-plane, composition, tracking and calculation mechanisms. The following architecture illustrates the supervisory study; it is not a mandatory architecture for every experiment. In this example, a local runtime owns each node session and Tier 1 owns deterministic actuation.

```text
Tiny Studio: protocol registration, data/model lineage, campaign scheduling, evidence
       | frozen experiment + model/dictionary/firmware/calibration identities
       v
Tier 3 service: objective calculation -> versioned Policy Envelopes / heartbeats
       |                              ^ compact node state / diary
       v                              |
Tier 2 node runtime: telemetry -> HTM -> bounded context -> asynchronous SLM
       |                                              |
       |                              validated solver directives -> PSO/GA*
       |                                              |
       +---------------- proposed controller parameters
                                      |
                       compiled guardrail and typed proposal validation
                                      |
                       gateway authority / sequence / freshness boundary
                                      |
Tier 1: frame revalidation -> atomic parameter update -> PID / MPPT -> plant
        independent watchdog / local safety / fallback -> telemetry + tick echo

* Local PSO is Tier 2; farm GA is Tier 3, matching the paper.
```

Inference must not stall telemetry capture or Tier 1 progress. Use bounded queues, one owned in-flight decision per node initially, stale-result rejection and explicit overload degradation. In SIL, apply a model reply only at its simulated completion time; computing it synchronously and pretending it existed at the start of a tick would invalidate latency and safety findings.

### Proposed implementation locations

These are proposed additions, not existing modules:

- `firmware/tier1/core/`: C PID, guardrail, frame codec, watchdog and deterministic state machine.
- `firmware/tier1/ports/`: PC SIL and specific STM32 board integrations.
- `tinystudio/lab/supervisory/`: session, model backends, proposals, HTM, policies, fault injection and event records.
- `tinystudio/lab/plants/`: thermal RC, averaged buck, shaded PV, tracker and network disturbance models.
- `tinystudio/lab/solvers/`: PSO, annealing bridge, GA and rule supervisors.
- `tinystudio/lab/protocols/`: deterministic RTU/TCP, SunSpec, CANopen, MQTT plans and diary.
- `tinystudio/lab/campaigns/`: registered protocols, trial expansion, blinded partitions, statistics and reports.
- `examples/supervisory/`: versioned H1–H6 experiment manifests and small reproducible fixtures.

Register new engines through the existing catalog/plugins/execution/preflight contracts, with matching schemas, input roles, runtime identities and target qualification. Prefer a dedicated supervisory engine first rather than claiming the generic scalar graph supports arbitrary external engine integration. Generalize shared scheduling only where the implementation actually demonstrates it.

Extend the current source manifest to non-Python assets and pin compiler flags, compiler/llama.cpp releases, model revisions/hashes, tokenizer/chat template, quantization recipe, calibration revisions and firmware build IDs. A firmware filename or model label is insufficient identity.

## 7. Candidate sequence for this example study

| Order | Deliverable | Dependencies | Completion gate |
| --- | --- | --- | --- |
| 1 | Corrected experiment specification and versioned action/telemetry/event schemas | Document review | Canonical corrected examples, complete HTM state table, frame layout, hypothesis endpoints, baselines and accepted proxies; protocol hash frozen before scoring |
| 2 | Shared C Tier 1 and PC SIL harness | 1 | Deterministic replay, strict parser/frame checks, atomic state, all drop/clamp/watchdog cases and independently checked plant outputs; same core buildable for target MCU |
| 3 | HTM and thermal/buck plants | 1–2 | H3 scenarios and feature independence; units, availability, hysteresis and reset behavior verified; raw and semantic records both retained |
| 4 | Local SLM supervisor and pinned llama.cpp backend | 2–3 | Actual 0.5B and 1.5B inference, raw single-attempt traces, latency sweep, crash/stall/late-result behavior, continued Tier 1 execution under all model failures |
| 5 | Tier 3 envelopes and multi-node arbitration | 3–4 | Real broker dropout/reconnection, independently evolving nodes, deterministic rule/oracle arms, randomized 360-trial H2 campaign |
| 6 | Experimental dataset builder and model development path | 3–5 | Training examples derived from attributable episodes, held-out campaigns excluded, model reload and quantized deployment parity checked, unchanged final-test definitions |
| 7 | PV/solver and battery-scheduling experiments | 4–6 | Plain/rule/model H4 arms on 400 untouched final patterns, true-GMPP reference, counted inference overhead; separately specified GA study |
| 8 | Protocol and cross-layer experiments | 3–5 | Six independent H5 subreports; actual dual-write application receipts, exact safety-field recovery, diary replay and fair rule/queue/compression baselines |
| 9 | Qualified HIL integration | 2–5, relevant 7–8 parts | Board-specific compiled core, instrumented real timing, read-only then shadow then bounded actuation, gateway run binding, local stop and uncertain-outcome recovery; only then admit that physical endpoint |
| 10 | Outdoor H6 and separately scoped EMI work | 7, 9, corrected rig specification | Real calibrations, arm swaps/weather/reference controls, independent day-level analysis and net-energy accounting; unavailable mechanisms explicitly untested |
| 11 | Integrated UI, reproducible evidence export and independent qualification | Starts with 1; completes after relevant campaigns | Authenticated user can configure, preflight, run, cancel, inspect proposals/commits/plant results, compare arms and export a checksum-verifiable paper evidence bundle |

The guide's experiment order is **H1 → H3 → H2 → H4 → H5 → H6**. The implementation dependencies explain why HTM work is brought forward without changing the intended safety gate. Start with Node B for the first physical extension only after its Round 1 gates pass, as the guide recommends.

**For the supervisory use case, one useful demonstration is an H1/H3/H2 software-in-the-loop vertical slice built from configurable mechanisms.** It should run one model-supervised node with the shared C controller, stateful HTM, thermal/buck plant, independently timed model responses, real policy dropout, explicit baseline and retained report. This establishes the missing architecture before adding the broad protocol/solar portfolio.

## 8. Model and dataset lifecycle, from source to experiment

1. **Register the study.** Freeze hypothesis revision, physical claim versus proxy, trial matrix, seeds, train/development/final partitions, baseline controllers, endpoint/calibration configuration, statistical decision rule and failure handling.
2. **Capture source evidence.** Import simulation episodes and later real telemetry, controller actions, policy envelopes and operator interventions. Retain raw physical values, clock/tick identity, quality, outcomes and source hashes. Add datasheets/register maps and reviewed domain constraints as attributable context.
3. **Produce HTM sequences and supervised examples.** Construct state/context/action records from available history. Record baseline/oracle actions and outcome-derived labels. Review ambiguous or physically questionable examples. Never label a speculative paper mechanism “correct” solely because the paper proposes it.
4. **Partition by independent episode, device, scenario and time where appropriate.** Keep overlapping windows in the same split. Fit normalization, token dictionaries and retrieval indexes only on permitted data. Preserve the existing lineage and final-test restrictions.
5. **Establish the untrained baseline first.** Run the guide's Qwen 0.5B and 1.5B quantized instruct models without specialization. Then compare the same model family after SFT/LoRA and optional distillation. Fine-tuning is an experimental factor, not a substitute for proving the original architecture works.
6. **Train using existing Tiny Studio services.** Add the domain example builder and bridges rather than a second training system. Existing scratch distillation is useful, but arbitrary teacher/student tokenizers require explicit alignment or sequence-level teacher labeling. Full pruning, architectural GQA/MLA changes or ELF research are not prerequisites for testing H1–H6.
7. **Export and qualify the deployed model.** Add GGUF conversion/quantization where supported, with conversion logs and tokenizer/template identity. Current QLoRA merged export explicitly refuses unqualified merging; do not bypass that path. A safe first GGUF specialization route is a supported float/LoRA merge followed by validated conversion. Evaluate resulting deployed outputs, safety error rate, memory and cadence rather than assuming training-format equivalence.
8. **Freeze model/prompt/dictionary artifacts and run final SIL campaigns.** Include naive/rule/classical baselines, ablations, malicious/malformed/late outputs and resource contention. Do not tune on final-test failures; changes create a new study revision with a new final evaluation protocol.
9. **Cross the hardware gate.** Refit plant parameters from calibration data, freeze the updated model, verify board firmware and clocks, then repeat applicable tests with independent observers. Report simulation and physical results separately.
10. **Generate the paper evidence.** Each hypothesis/subclaim gets a supported/not-supported/inconclusive/untested outcome, effect size, uncertainty, exact denominator and links to original traces. Successful job execution is separate from experimental support.

## 9. Evidence schema, scoring and product work

A retained supervisory decision should bind at least:

```text
study/protocol/revision, campaign/trial/arm/seed, node and endpoint
source/plant/controller/firmware/calibration/model/dictionary/prompt identities
Tier 1 tick/reset, host monotonic time, source event time and availability
telemetry window and quality, derived features, current tokens and transition reasons
envelope ID/age/expiry and heartbeat health
inference queued/start/end, raw response, every retry and token counts
proposal parsing result, proposed action, guardrail verdict/reason and bounded action
command ID/epoch/sequence/expiry, send/ack/application time and actual committed values
controller state/fallback/watchdog status, independent observed plant outputs
fault injection identity in evaluator-only evidence, baseline pairing and missingness
```

Raw high-rate samples can be separate binary/Parquet artifacts referenced by decision records. Use existing content-addressed storage and views; do not put full-rate telemetry into every API event or prompt. Keep evaluator-only injection truth outside the model context, including within nested metadata.

Add campaign-level evaluators for:

- H1 physical/committed violations, proposed violations, fallback worst-case and latency distribution, ITAE/overshoot/settling, non-inferiority and separate harmful-in-range outcomes.
- H2 priority agreement, forbidden duty, independent-arm energy, temperature exposure, dropout and reconnection timing.
- H3 transitions, detection delay, component confusion, transmitted bytes including resync, model prompt tokens and paired information-loss test.
- H4 success rate, local-optimum trapping, optimum excluded by bounds, feasibility, evaluations and time-to-success including failed/time-limited cases.
- H5 correctness/delivery, cause confusion, critical-message loss/deadline, compressed bytes plus exact reconstruction, attack precision/recall/false positives, diary time/state fidelity.
- H6 per-mechanism electrical/irradiance/temperature outcomes and overhead-adjusted energy.

Implement exact/binomial intervals as appropriate, Wilson agreement intervals, paired McNemar, Wilcoxon, Fisher's exact test and non-inferiority confidence rules. Freeze the unit of independence and primary endpoint. Report effect sizes and intervals, not only p-values. Time-series blocks and day-level weather dependence require paired/block-aware analysis, not tick-level pseudoreplication.

Extend the existing React Laboratory with H1–H6 templates, campaign expansion and completion accounting, explicit emulation/HIL/outdoor evidence labels, and a synchronized view of telemetry → token transition → model proposal → interlock verdict → committed action → physical outcome. Show proposal failures even when the interlock saves the plant. Add policy-age/dropout and solver-state views, hypothesis acceptance reports and artifact export. The browser and assistant are experiment interfaces; they must not own a safety heartbeat or high-rate control loop.

## 10. Verification performed and limits

The repository was cloned locally and its scientific runtime, data/model paths, gateway, protocol adapters, execution/preflight, evaluation contracts, frontend Laboratory structures and accompanying architecture/laboratory documentation were inspected. Focus was on code that affects the paper's claims; this was not a separate security audit, a claim of line-by-line review of every UI component, or a deployment qualification.

Focused test results and direct probes are retained alongside this report. Test counts and environment limitations are detailed in `verification-summary.md`. The baseline environment lacked project ML/test packages; selected dependencies were installed into a separate `/workspace/review-venv`, not into repository source. Dependency versions differ from the repository's previously recorded development stack, so these runs are scoped compatibility evidence, not reproduction of its entire historical qualification.

Direct read-only probes confirmed:

- `physical_control` preflight fails with the stated missing integration.
- `slm` is rejected as a co-simulation component kind.
- The existing engineering bins alternate on every 44.9/45.1 °C crossing.
- The offline Modbus fixture emits a response PDU and declares no RTU/TCP transport.
- The gateway rejects an over-limit current and accepts a partial parameter update; it is not the guide's strict complete-frame clamp kernel.
- An ordinary structured heater action is not a scalar numeric-evaluation target.
- The correct example RTU CRC is `C5 C1`; the Explorer's displayed bytes end in `C5 C4`.

No full application/database/Redis stack, authenticated browser workflow, actual Qwen model download/inference, Pi benchmark, STM32 execution, live fieldbus, eBPF capture, solar campaign or physical actuation was performed. Existing tests or historical documentation do not substitute for those future experiments. The report recommends what to implement; it does not claim those implementations or hypothesis outcomes already exist.

## 11. Revised product recommendation

Keep Tiny Studio's existing data, model, execution and evidence infrastructure. Prioritize general, extensible mechanisms for experiment composition, collection/tracking and auditable statistical planning and analysis. Provide open adapter/component/calculation interfaces, researcher-owned configuration and editable examples. The supervisory paper can demonstrate these tools without dictating their limits or the permitted mechanisms.

Sample-size and power planning deserve an explicit before-experiment workflow: choose the design and independent unit; record effect/variance or rates, alpha, power or interval precision, pairing/clustering and attrition; compute with a named, versioned method; retain inputs, sources, assumptions, sensitivity and rounding. The assistant should help configure and execute the calculation, and link to its receipt instead of inventing missing parameters. A sample count quoted in the lab guide is not itself a sample-size calculation.

The selected five document corrections are complete in the companion editions. The statistical workbench and new general mechanisms are requirements for subsequent implementation, not changes claimed by this editorial task.

The research succeeds if the platform can produce a credible negative result as readily as a positive one. “The guardrail protected the plant,” “the model improved the decision,” “the solver improved yield,” and “the whole deployed system saved net energy” must remain separate conclusions.
