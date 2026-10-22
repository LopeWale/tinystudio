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
