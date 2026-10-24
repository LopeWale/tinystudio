# Capability roadmap — research checkpoint, 2026-10-23

This is a researched implementation plan, not delivery of the four features. Preserve the requested order: vision-language images, visualization, assistant additions, Kubernetes. Resolve shared request-lifetime risks before product rollout; the isolated vision compatibility experiment can proceed independently. No production configuration or dependencies were changed during research.

## Current evidence

- Baseline: `master`, HEAD `77896f60dba42f97a5a59f258cc800e2d04132ff`. Existing assistant service/API/UI/tests and the new synchronization module belong to concurrent work. Do not overwrite them.
- Installed versions, read from this checkout's Python environment: datasets 5.1.0, transformers 5.20.2, TRL 1.16.0, torch 2.14.1, anthropic 2.1.0, qdrant-client 1.20.0; host architecture arm64. Broad lower bounds in pyproject.toml do not reproduce this combination.
- `services/datasets.py:286` builds the quality profile from `list(iter_rows(...))`. Moving this unchanged to a worker removes HTTP blocking but does not solve memory scaling.
- `core/data.py` validates text message content; `training/train.py` and `serving/runner.py` use causal-language models/tokenizers. Vision requires a coordinated contract through storage, training and serving.
- Postgres conversation snapshots and revision checks already exist in `services/assistant_conversations.py`; automatic sync is in progress. The earlier “assistant not started” status is stale. Current source inspection does not repeat its recorded live acceptance.
- Task SSE emits a keepalive after a 15-second subscription timeout. Assistant streaming emits keepalive events after five seconds waiting for its worker queue. Test actual delivery through the public route before changing either.
- Attempted a one-image datasets/PIL/Parquet round trip. It stopped at `ModuleNotFoundError: No module named 'PIL'`, before decoding or serialization. Neither image compatibility nor VLM training has passed. No package installation was attempted in the shared environment.
- Wolfram context lookup returned an internal tool error. No Wolfram computation or recommendation was obtained.

## 0. Shared reliability work

Cloudflare documents a 125-second proxy read timeout; this does not establish a 125-second maximum lifetime for every active stream. Its Tunnel origin settings are a separate configuration boundary. Source: [connection limits](https://developers.cloudflare.com/fundamentals/reference/connection-limits/).

Make quality profiling a durable task on the existing worker system with immediate task-ID response, cancellation, progress and cached results keyed by immutable dataset revision. Use incremental aggregates; label sampled/approximate duplicate analysis explicitly. Benchmark row width and algorithm costs, peak memory, cold/warm caches and elapsed time at increasing sizes. The million-row estimate is currently unmeasured.

Acceptance: compare direct-origin and authenticated public-route behavior for (a) no initial response for longer than 125 seconds, (b) a stream with events every 5–15 seconds lasting at least three minutes, (c) a long silent gap after headers, and (d) disconnect/reconnect during a real task. Record observed timeout layers, event gaps, completion and replay behavior. Keep fixtures disposable; do not restart another session's services. Human Access sign-in remains a separate acceptance step; this research did not inspect the browser.

For eval results, traces and experiment logs, first inspect actual tables, access patterns, retention and volume. Evaluate time partitions for retention-heavy traces/logs and owner-key partitioning for evaluation lookups; do not copy the datapoint strategy blindly. Plan constraint/foreign-key changes, migration locking, rollback and pruning checks. PostgreSQL partitioned unique constraints must include the partition key: [PostgreSQL 18 partitioning](https://www.postgresql.org/docs/18/ddl-partitioning.html). Runtime database version and workload still need confirmation.

## 1. Images and vision-language models

The first gate must be a VLM image-plus-question-to-answer job, not an image classifier. The researcher established text-only product paths; its classification proposal was not adopted because it does not satisfy this objective.

Use a disposable environment with the observed package versions plus image dependencies, then lock the combination that actually passes. Test `datasets.Image` bytes and paths, decode on/off, and a Parquet round trip independently. Include grayscale/RGBA, corrupt bytes, EXIF orientation and missing assets. The documented Image feature is an input representation, not proof of TinyStudio compatibility: [image loading](https://huggingface.co/docs/datasets/image_load).

Candidate first model: [SmolVLM-256M-Instruct](https://huggingface.co/HuggingFaceTB/SmolVLM-256M-Instruct), selected for a small compatibility experiment, not promised MPS support or production quality. Record model revision, processor, device, dtype and attention backend. Use a current multimodal loader supported by the installed Transformers version; the model card contains examples from different API generations.

[TRL SFT documentation](https://huggingface.co/docs/trl/sft_trainer) supports VLM datasets with `image` or `images` columns and warns that truncation can remove image tokens. Begin with no token truncation and bounded image dimensions/sequence sizes. Check the actual label mask and image placeholders. Assistant-only loss has template and version requirements; do not assume a flag guarantees correct labels.

Gate: 8–16 owned/generated image-question-answer examples, a few optimizer steps, finite loss/gradients and a changed trainable parameter; save processor plus adapter/model, unload, reload and infer with image input. Compare identical prompts with different images and inspect pixel tensors so a text-only path cannot silently pass. Require saved and reloaded trainable tensors to match; fail if updated weights are lost. Add a controlled removed/replaced-image case and require a detectable change in model inputs and image-conditioned logits, not merely nonempty pixel tensors or fluent output. This proves wiring only; evaluate usefulness on held-out real images separately. Explicitly report CPU versus MPS versus CUDA; CPU success does not prove GPU compatibility.

Product design after gate:

1. Versioned multimodal messages with typed text and image asset references; preserve legacy text datasets. Postgres owns identity and permissions; local/R2 artifact storage owns original bytes and thumbnails.
2. Server-verified SHA-256, MIME decoding, byte/pixel limits, scoped upload completion and thumbnail jobs. Deduplication must preserve owner authorization. Preserve original bytes; record orientation-normalized derivatives separately.
3. Snapshots pin immutable asset hashes and processor/model revisions; training resolves verified assets to local files or bytes. Do not store expiring signed URLs as durable dataset values. Cleanup must respect snapshot references.
4. A modality-aware training/serving boundary selects processor, model loader and collator without forcing the text path to accept image tensors. Carry assets through evaluation, traces, exports, retries and UI previews.
5. Browser acceptance: upload → preview → snapshot → train → model reload → image question → output/trace. Reject corrupt/oversized/unauthorized assets; verify cold-cache R2 restoration and unchanged text training.

## 2. Interactive visualization

Use a Vite-built visualization entry inside the existing FastAPI UI, preserving auth, navigation, project context and assistant selection. Build static assets for production; Vite is not a second production server. [Vite backend integration](https://vite.dev/guide/backend-integration) documents manifests for backend-served assets.

Start with bounded, authorized repository-vector samples, a cached server-side 2D projection and Canvas/WebGL points with selection, filtering and source preview. Store point IDs, collection/embedding revision, projection algorithm, seed and sample membership. Do not download all vectors to the browser. Qdrant's [distance matrix API](https://qdrant.tech/documentation/concepts/explore/) returns sampled sparse similarities, not ready-made stable 2D coordinates. Check the deployed server version before using that endpoint; client version alone is insufficient. Use a measured PCA baseline before adding UMAP; cache coordinates to avoid arbitrary layout changes on reload.

Experiment comparisons must align dataset snapshot, evaluation set, metric definition and step units. Token heatmaps must identify the quantity, layer, tokenization and normalization; activation magnitude is not automatically causal importance. Preserve Unicode/token offsets and mark truncation. Accept when selecting a point opens its correct source, cross-project access fails, comparison values match stored evaluations, and heatmap cells match captured values with explicit missing-data handling. Set rendering budgets from measured representative sample sizes.

## 3. Assistant additions

Extend the existing Postgres conversation/CAS service. Decide explicitly whether “shared conversations” means the same user across devices (already under active development) or collaboration between different users (requires conversation identity, membership, roles and revocation). Do not treat project membership as automatic permission to read someone's private chat.

Use one internal attachment record for screenshots/uploads/page captures, with local/R2 content and provider-specific handles. [Anthropic Files API](https://platform.claude.com/docs/en/build-with-claude/files) supports reusable file IDs, but files are workspace-scoped rather than user-scoped. Keep provider IDs server-side behind authorization; verify installed SDK operations and file-type/model support. Text/CSV/document handling needs explicit conversion or a supported tool path; uploading a file alone does not make every format a vision input. Keep the OpenAI provider path explicit rather than silently treating it as Anthropic.

Page capture should offer structured page context and, where needed, a visible screenshot preview that the user can remove before sending. Preserve the current selected-card context. Distinguish rendered DOM capture from browser/OS screen capture and avoid capturing secret fields.

The [Anthropic memory tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool) requests client-side operations; TinyStudio must implement storage. Use scoped logical `/memories` paths backed by Postgres records, with traversal rejection, size limits, revision checks and visible edit/delete controls. Conversation history and curated memory are separate records with independent retention.

Cost limits belong in the server-side turn lifecycle: reserve a conservative maximum before each provider call, atomically coordinate concurrent tabs/turns, reconcile returned usage, include cached/tool charges where applicable, and stop further calls at the budget boundary. Unknown pricing fails closed for a hard monetary limit. Output-token caps and 40 tool iterations are not a monetary budget. Test cancellation, retry charging, concurrent reservations, provider errors and near-limit turns. Admit only calls whose bounded maximum fits; explain any upstream billing uncertainty rather than promising zero overshoot without evidence.

## 4. Kubernetes

Keep the native Mac path. GPU Operator is a cluster dependency for compatible NVIDIA nodes, not an Apple-GPU enablement mechanism: [NVIDIA prerequisites](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html). Confirm target cluster, supported node OS/driver/runtime, storage and GPU quota before deployment.

Chart responsibilities: API Deployment, scheduler/reconciler, migration Job, optional inference Deployment and one batch Job per training attempt, plus scoped service accounts, resource limits and secrets references. A product training task may have multiple attempts. Persist task/attempt/Job UID mappings; use idempotent launch and artifact publication. Kubernetes may start a workload more than once: [Job behavior](https://kubernetes.io/docs/concepts/workloads/controllers/job/). Set retry, deadline and cleanup policies; retain logs/results before Job TTL deletion. Reconcile cancellation, node loss and controller restart against durable task state. Do not let the API's current startup orphan recovery incorrectly fail live cluster Jobs.

Keep GPU Operator, Gateway controller and state-store operators outside the application release lifecycle. [Gateway API installation](https://gateway-api.sigs.k8s.io/guides/) requires both CRDs and a controller. Add Gateway resources only for a chosen controller; preserve Access/origin auth and the intended Tunnel route. Store operators are not one generic dependency: choose PostgreSQL/Qdrant/Redis deployment and backup arrangements after evaluating recovery requirements; external managed stores are a valid initial configuration. Selection remains open.

Acceptance: real NVIDIA scheduling and training, duplicate reconciliation without duplicate publication, restart/node-loss/cancel recovery, R2 cold restore, store backup/restore and public authenticated UI access. Helm rendering or a local CPU cluster does not establish GPU acceptance.

## Next executable increment

The image gate is first: isolated dependency/image round-trip check, then the small VLM optimizer/save/reload/inference experiment. No full vision UI build should be committed before those gates. Reliability work can proceed independently without modifying the ongoing assistant sync changes. The remaining features above are planned, not implemented or runtime-qualified.
