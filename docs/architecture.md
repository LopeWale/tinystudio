# Architecture

tinystudio is a control plane plus workers. Each store does one job, so a long
training run never slows a page load and a vector search never competes with a
transaction.

| Component | Runs | Holds or does |
| --- | --- | --- |
| `api` (`tinystudio serve`) | FastAPI, uvicorn, port 8000 | web interface, `/api`, `/v1` (OpenAI-compatible), authentication, task creation; applies migrations at start |
| `worker` (`tinystudio worker`) | RQ worker, one job at a time | training (SFT, LoRA, QLoRA, DPO), evaluations, synthetic data, labelling, repository indexing, interpretability captures and tests, autopilot cycles, Hub downloads and uploads |
| `inference` (`tinystudio inference`) | FastAPI, port 8001 | models in memory, streaming generation, traces; the API proxies to it. Unset `TINYSTUDIO_INFERENCE_URL` and the API loads models itself (single machine) |
| PostgreSQL | `postgres:18` | every row: accounts, projects, datasets and datapoints, snapshots, jobs and experiments, loss points, metrics, evaluations and results, traces, repository and document metadata, capture metadata, autopilot records, tasks, settings |
| Redis | `redis:8` | the job queue and the progress channel (`tinystudio:task:<id>`) the API relays as server-sent events |
| Qdrant | `qdrant/qdrant:v1.20` | one collection per repository: chunk text and embedding, document id, position; cosine distance; payload index on `document_id` |
| data volume | `TINYSTUDIO_DATA_DIR` | weights, adapters, checkpoints, merged models, activation captures (`.npz`), uploads, full task logs, the Hugging Face cache |

## Rows and bytes

PostgreSQL keeps rows; everything large lives in the artifact store (`TINYSTUDIO_STORAGE_BACKEND`: a local
directory, or private R2 through `docs/cloudflare.md`).

- **Datapoints** are hash-partitioned by `dataset_id` into 16 partitions (`datapoints_p00` to `_p15`). The
  primary key is `(dataset_id, id)` and `(dataset_id, position)` is unique, so every dataset query prunes to one
  partition and the partition count stays fixed however many datasets the team creates; no DDL runs when a
  dataset is created or deleted. Writes use `COPY`. Each `datasets` row keeps per-bucket `counts`, updated in
  the same transaction as the write, so list pages and stats never count rows. Every write locks its dataset
  row, which serializes appends and keeps the counters exact.
- **Reads** page with a keyset cursor: `GET /api/p/<slug>/datasets/<name>?after=<next_after>&limit=N`; `offset`
  still serves numbered pages. Whole-dataset reads (exports, cleaning, profiling, snapshots) stream in batches
  of 5,000 rows, and downloads stream on their own database session.
- **Snapshots** are frozen as zstd Parquet shards of 10,000 rows with a checksum manifest in the artifact store,
  under `snapshots/<project id>/<snapshot id>/`; the `snapshots` row keeps the counts and the prefix. Freezing
  holds a shared lock on the dataset so no writer changes the buckets mid-copy. Snapshots made before this
  layout keep their rows in `snapshot_rows` and still load.
- **Long operations:** cleaning, autosplit and snapshot creation run inside the request up to
  `TINYSTUDIO_SYNC_ROW_LIMIT` rows (default 100,000) and above it return 202 with a worker task, because
  Cloudflare closes a proxied request that has not answered within 125 seconds.
- **Not partitioned yet:** `eval_results`, `traces`, `experiment_logs`. They are append-only and indexed by
  their parent; partition them by time or parent when they reach tens of millions of rows.

## Request paths

- **Browser.** Sign in sets a signed, HttpOnly session cookie. Every
  state-changing request from the page carries `X-Requested-With: tinystudio`
  (a CSRF guard). Project-scoped routes live under `/api/p/<slug>/...`; the
  page rewrites its calls based on the project picked in the sidebar.
- **API keys.** `Authorization: Bearer ts-...` authenticates `/api` and `/v1`.
  Keys belong to users. MCP clients and OpenAI SDKs use them. `/v1` model ids
  are `<project>/<job>/<n>`, or `<job>/<n>` with an `X-Project` header.
- **Assistant.** The in-app assistant runs on the API and calls the API back
  over the loopback address as the signed-in user, with a signed service token
  that lives one hour.
- **Conversation sync.** PostgreSQL owns each user's project conversation and
  its compare-and-swap revision. Conditional GETs authenticate every read, use
  an identity-scoped ETag, and select only the revision for unchanged checks.
  Private responses use `no-store`. The frontend's `assistant/conversation-sync.js`
  owns timers, cancellation, backoff, browser lifecycle listeners and tab
  invalidations. Its injected check callback reconciles snapshots through the
  conversation's write queue; it never overwrites a dirty reply or live stream.
  Remote updates patch the transcript and model controls without recreating the
  composer. Three-second visible polling provides device synchronization;
  metadata-only tab notifications reduce same-origin latency. Hidden tabs check
  every 15 seconds while running, then catch up on focus/visibility, pageshow,
  and online events. See the browser contracts for
  [BroadcastChannel](https://developer.mozilla.org/en-US/docs/Web/API/Broadcast_Channel_API),
  [page visibility](https://developer.mozilla.org/en-US/docs/Web/API/Page_Visibility_API),
  and [conditional reads](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/If-None-Match).
- **Tasks.** The API inserts a `tasks` row and enqueues an RQ job whose id is
  the task id. The worker marks the row running, streams lines to the log file
  and the Redis channel, keeps the last 40 lines on the row, and records the
  result or the error. Stop cancels a queued job or kills the running work
  horse, then marks the task and its experiment stopped.

## Scaling

- Add workers on more machines: point them at the same PostgreSQL, Redis,
  Qdrant, and data volume (NFS or an object-storage mount). Each worker runs
  one job at a time; the queue spreads the rest.
- Put inference on the GPU box and the API anywhere (`TINYSTUDIO_INFERENCE_URL`).
- PostgreSQL and Qdrant scale on their own terms (replicas, Qdrant's
  distributed mode with `QDRANT__SERVICE__API_KEY` and internal auth enforced
  in 1.20).
- The API is stateless apart from in-process model memory when inference runs
  inside it; run it behind HTTPS with `TINYSTUDIO_SECURE_COOKIES=1`.

## Migrations and imports

`tinystudio db upgrade` applies Alembic migrations (the API does it at start).
`tinystudio db revision "message"` autogenerates one from model changes during
development. `tinystudio import-project <dir>` moves a pre-database project
directory into the stores; weights are hard-linked into the data directory
when both sit on one filesystem.
