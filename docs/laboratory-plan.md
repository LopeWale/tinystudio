# TinyStudio expansion implementation plan

## Delivery rules

- [x] Finish and accept increment A before advancing implementation to B.
- [ ] Finish and accept each subsequent increment before advancing to the next.
- [ ] Check off implementation tasks only after their stated checks pass; check off an increment only after its complete workflow passes independent review.
- [ ] Preserve existing application workflows and unrelated work throughout integration.
- [ ] Reuse existing services and components before adding alternatives.
- [ ] Keep architecture and operator guidance in `docs/laboratory.md` and `docs/architecture.md`.
- [ ] Keep implementation history and validation results in `CHANGELOG.md`.

## A. Contracts and compatibility

- [x] Assign requirement IDs and map the A–G capability portfolio.
- [x] Implement versioned definition, resolved specification, episode, artifact, dataset, retrieval, target and deployment contracts.
- [x] Implement separate run/attempt identities and immutable revision/hash contracts.
- [x] Implement additive laboratory table definitions and a schema upgrade fixture preserving an existing row.
- [x] Implement explicit input visibility and execution-profile contracts.
- [x] Complete the shared plugin lifecycle, input/output, units, dependency and target contracts.
- [x] Register laboratory models and the additive migration in the existing application.
- [x] Integrate compatibility adapters without changing existing Job, Experiment, dataset, snapshot, API or metric semantics.
- [x] Bind visual and code authoring to the same versioned definition and preserve domain-specific connection semantics.
- [x] Complete and review the adapter/method qualification matrix with fixture and compatible-target requirements.
- [x] Verify existing TinyStudio workflows against the integrated contracts and migration.
- [x] Accept A through independent review of the complete increment.

## B. Evidence and runners

- [x] Implement transactional dispatch, target credentials, leases, durable events, cancellation and explicit retry services.
- [x] Implement immutable source snapshots, input checksums and child runtime receipts.
- [x] Implement signal/replay executors and raw/derived evidence artifacts.
- [x] Implement checksum-verified project bundle export/import primitives.
- [x] Register the project-scoped laboratory API, CLI and MCP commands in their existing consumers.
- [x] Connect authoring, run inspection, evidence selection and export through the existing frontend.
- [x] Complete public episodes, pipelines, comparison, target and deployment API contracts.
- [x] Return durable identifiers for long operations and support user-selected schedules and resource budgets.
- [x] Verify publication of complete manifests last and retention of artifacts referenced by historical experiments.
- [x] Implement the RQ execution adapter while keeping laboratory state in PostgreSQL.
- [x] Recover existing evidence operations through CLI and MCP controls without duplicate admission.
- [x] Exercise serial native execution and portable evidence recovery over actual loopback HTTP.
- [x] Prove author → submit → execute → inspect → export locally through authenticated application controls.
- [x] Prove the same workflow on a separate remote execution host using outbound scoped HTTPS.
- [x] Verify reconnect, duplicate delivery, cancellation, partial uploads, runner loss and recovery through the integrated workflow.
- [x] Accept B through independent review of the complete increment.

## C. Data and representations

- [x] Integrate acquisition, recordings, clock alignment, profiling, cleaning, filtering, resampling, interpolation, imputation, feature extraction and labeling into one versioned pipeline workflow.
- [x] Verify known signals and injected gaps with independent expected outputs.
- [x] Preserve raw observations, units, missing masks, estimated-value markers, source identities and label sources through all transformations.
- [x] Integrate grouped episode/device/scenario partitions, temporal splits and final-test protection.
- [x] Integrate byte, numeric text, decoded protocol, engineering-state and hybrid representations of matched evidence.
- [x] Connect existing dataset/snapshot/training/RAG services without requiring duplicate manual imports.
- [x] Complete synchronized replay, topology, coverage and representation inspection with linked evidence selection.
- [x] Detect preprocessing, retrieval, reward and scenario-selection leakage; retain explicit overrides and qualification status.
- [x] Accept C through an authenticated data-to-training workflow and independent review.

## D. Learning methods and retrieval

- [x] Integrate scratch Transformer and recurrent models with byte, subword and engineering-vocabulary tokenizers.
- [ ] Support independent architecture, initialization, representation, objective, update strategy, retrieval and environment selections with compatibility checks.
- [ ] Integrate continued pretraining, supervised full/LoRA/QLoRA training, DPO and distillation.
- [ ] Integrate language/tool PPO/GRPO methods with recorded actual tool interactions and independent evaluation.
- [ ] Integrate time-series and multimodal PyTorch models.
- [ ] Integrate offline/online control RL, imitation and multi-agent learning through TorchRL-compatible environments.
- [ ] Compose lexical, semantic, hybrid, structured and episode retrieval with frozen corpora/indexes.
- [ ] Verify intended parameter updates and save/unload/reload/inference for every learning method on a compatible target.
- [ ] Compare conventional, base, specialized, LoRA, RAG and combined methods on matched held-out cases.
- [ ] Complete baseline comparison views while retaining existing loss and neuron inspection.
- [ ] Accept D through complete research workflows and independent review.

## E. Domain laboratories

- [ ] Complete and qualify Wokwi/TinyGo, Renode, QEMU and Verilator adapters.
- [ ] Complete and qualify OpenModelica/FMI, BOPTEST, EnergyPlus, ngspice, pandapower and PyBaMM adapters.
- [ ] Complete and qualify Mininet, ns-3, Modbus and MQTT adapters.
- [ ] Complete and qualify SimPy, OR-Tools, Hypothesis, Gazebo and custom-environment adapters.
- [ ] Connect existing licensed MATLAB/Simulink installations through qualified adapters.
- [ ] Integrate component/pin/wiring/bus authoring, firmware build/run/debug, serial and logic inspection.
- [ ] Integrate equations, block diagrams, state machines, controllers, disturbances, faults and parameter sweeps.
- [ ] Integrate network topology, decoded protocol fields and packet/message/staleness timelines.
- [ ] Integrate bounded live acquisition and non-actuating shadow evaluation with explicit bindings.
- [ ] Integrate physical command authority, epochs, command identities, expiry, limits, local stop and uncertain-outcome recovery at the gateway boundary.
- [ ] Enforce selected native/container/Kubernetes isolation grants and resource budgets; prove denied access and authorized grants on compatible hosts.
- [ ] Accept E with compatible-target fixtures, explicit qualification status and independent review.

## F. Combined research

- [ ] Integrate multi-engine synchronization with clocks, step sizes, event ordering, hold/interpolation policies and tolerances.
- [ ] Verify cross-engine fixtures and reject unsupported algebraic loops or require explicit delays.
- [ ] Separate system feedback connections from execution dependencies in authoring and execution.
- [ ] Integrate cooperating specialists with local and system objectives.
- [ ] Integrate branchable runs, matched interventions and lineage across changed experimental meaning.
- [ ] Integrate sensor placement/sampling studies, fidelity comparisons and automated scenario discovery.
- [ ] Accept F through combined-engine experiments and independent review.

## G. Integrated qualification and delivery

- [ ] Prove shared-server access and portable project restore across separate computers.
- [ ] Distinguish and verify exact continuation, restart from weights and rerun.
- [ ] Package target-specific inference and deployment compatibility reports.
- [ ] Measure desktop, gateway and supported controller RAM, flash, latency and energy where available; label unmeasured values and simulator estimates.
- [ ] Complete deployment tradeoff views with actual measured evidence.
- [ ] Verify authenticated desktop/tablet/phone workflows, keyboard navigation and recovery.
- [ ] Verify the full existing TinyStudio regression suite after integration.
- [ ] Deliver separately locked optional dependency packs, packaging and operational documentation.
- [ ] Complete independent review and accept the full A–G expansion.
