# Cloudflare deployment

TinyStudio can use private R2 storage and Cloudflare Access while its API, worker, PostgreSQL, Redis, and Qdrant run on the origin machine. Large artifact transfers use S3 directly. No Cloudflare Workers, Queues, Containers, or Vectorize are required.

Set `TINYSTUDIO_STORAGE_BACKEND=r2`, `R2_ENDPOINT_URL`, `R2_BUCKET_NAME`, `R2_REGION=auto`, `R2_ACCESS_KEY_ID`, and `R2_SECRET_ACCESS_KEY` in a private `.env`. The bucket credential needs Object Read & Write on this bucket only. With `local` (the default), existing database snapshots and local model artifacts keep working.

In R2 mode the bucket owns durable file artifacts. `TINYSTUDIO_DATA_DIR` is ignored;
`TINYSTUDIO_CACHE_DIR` optionally selects a disposable runtime workspace, otherwise
TinyStudio uses a bucket-namespaced directory in OS temporary storage. Model
loaders, extraction and frontend serving use reconstructed local files, with no
shared filesystem requirement between the API and workers. Each host connects
to the same PostgreSQL, Redis and Qdrant services and the same private bucket.
The config response reports the artifact backend/bucket separately from its cache.

Set `TINYSTUDIO_PUBLIC_URL=https://tinystudio.joatlabs.dev`, `CF_ACCESS_TEAM_DOMAIN=joatlabsholding.cloudflareaccess.com`, `CF_ACCESS_AUD`, and `TINYSTUDIO_CF_ACCESS_REQUIRED=1`. FastAPI independently verifies Access JWT signatures, issuer, audience, expiry, and application-token type. Missing and invalid assertions fail closed, including on loopback; only the constant `/healthz` liveness response is exempt. `/api/health` remains protected. Internal assistant requests forward the verified assertion together with the user's short-lived app credential.

Verified human emails map to existing enabled TinyStudio accounts and retain their roles. Access does not create administrators. The approved `tinystudio@joatlabs.dev` account has the member role. Machine service authentication still requires a TinyStudio API key. The application's existing keys inherit their user's permissions; they do not provide per-project permission scopes.

## Persistent origin processes on this Mac

```sh
.venv/bin/python scripts/services.py install tunnel api worker
.venv/bin/python scripts/services.py status tunnel api worker
```

These are per-user launchd agents under `~/Library/LaunchAgents/dev.joatlabs.tinystudio.*.plist`. They start at login and restart after exit. This is not a system boot daemon: the Mac must be awake and the user logged in. PostgreSQL, Redis and Qdrant must also be running (`docker compose up -d postgres redis qdrant`). Logs are in `~/Library/Logs/TinyStudio/`. The API listens on `127.0.0.1:8000`; the named remote-managed tunnel is its public entrance.

The launcher reads `TUNNEL_TOKEN` from the mode-0600 `.env`, puts only that credential in the connector environment, and keeps it out of argv and plist. API and worker child environments blank the tunnel and outbound Access service credentials. Compose likewise overrides these credentials for app containers. After changing code or `.env`, restart the affected agents:

```sh
launchctl kickstart -k gui/$(id -u)/dev.joatlabs.tinystudio.api
launchctl kickstart -k gui/$(id -u)/dev.joatlabs.tinystudio.worker
```

Use `scripts/services.py uninstall tunnel api worker` to remove only these services. Do not run `tinystudio dev` or another API on the same port while the agents are loaded.

## MCP and Python clients

An external client needs `TINYSTUDIO_URL` and `TINYSTUDIO_PUBLIC_URL` set to the public HTTPS origin, a `TINYSTUDIO_PROJECT` slug, a `TINYSTUDIO_API_KEY`, and the approved `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET`. The existing MCP command is:

```sh
.venv/bin/tinystudio mcp --url https://tinystudio.joatlabs.dev --project PROJECT_SLUG
```

Keep secrets in the client's environment, not command arguments. The one-year Access service token must be rotated before expiry. `Studio` attaches Access credentials only to the configured HTTPS origin and rejects redirects as API errors.

```python
import os
from tinystudio.integrations.tools import Studio

studio = Studio(os.environ['TINYSTUDIO_URL'], os.environ['TINYSTUDIO_PROJECT'], os.environ['TINYSTUDIO_API_KEY'])
studio.upload_dataset('my-data', '/absolute/path/data.jsonl')
print(studio.call('GET', '/api/datasets'))
```

`create_dataset_from_jsonl` in MCP transfers the file from the MCP client's machine. The browser's JSONL upload uses the same authorize → direct R2 PUT → validate/import flow. Signed upload URLs expire after 15 minutes and bind content length; imports are limited to 512 MiB. App and Access secrets never go to R2 or browser JavaScript. Imported rows are copied into PostgreSQL, so replaying a staging upload cannot mutate a dataset or snapshot.

## Storage layout and recovery

- `snapshots/PROJECT_ID/SNAPSHOT_ID/`: Parquet shards containing ordered JSON rows, plus a checksum manifest. A nullable database marker distinguishes these from legacy SQL-only snapshots. Remote snapshot failures do not silently fall back to SQL.
- `models/PROJECT_ID/EXPERIMENT_ID/`: immutable step-specific checkpoints and final weights, published before success is reported. Inference and refinetuning hydrate and verify missing local files. Weight downloads redirect to a signed R2 URL.
- `retained-captures/PROJECT_ID/`: new activation captures, comparisons, explanations and tests. This avoids the historical 30-day expiration policy on `captures/`. Stored prefixes remain readable; expired legacy captures are unavailable. Concurrent mutations across hosts are serialized using PostgreSQL advisory locks, including publication and metadata registration.
- `sources/PROJECT_ID/SHA256/FILENAME`: original JSONL dataset uploads and synthetic/repository source files, verified before task admission. Workers receive project-scoped object references and materialize verified temporary copies; API-host paths are never passed to remote workers. Dataset origins and task arguments retain the references. `GET /api/p/SLUG/artifacts/sources/SHA256/FILENAME` retrieves an original file.
- `tasks/TASK_ID/`: full task logs as immutable checksummed chunks and a last-published head manifest. Any API host can read full logs (`tail=0`) or a suffix; successful task completion requires final log publication. Progress publishes on initial/final flush, full 64-KiB chunks and a new event after two seconds. A hard process kill can lose the unpublished progress buffer; the durable head remains valid. Duplicate delivery cannot claim an already-running task.
- `uploads/PROJECT_ID/`: short-lived staging uploads. Successful dataset imports remove their staged object. Failed or abandoned uploads may need operator cleanup.

Objects remain private; no public R2 custom domain is needed. Browser CORS permits the public origin and the configured localhost development origins. Checkpoint history uses step-numbered keys, not bucket versioning. New experiments persist portable locations, retain their refinement base independently of an ancestor, and publish merged exports and available failure diagnostics. Imported experiment metadata commits only after publication. No migration of historical implementation/mock files is performed. Records lacking a remote publication report unavailable rather than silently using a host-local copy.

Hugging Face downloads and frontend release copies are reconstructible caches.
PostgreSQL owns application rows; Redis owns the queue; Qdrant owns retrieval
indexes. These services must be reachable from every participating host. R2
storage does not change their connection settings or the host running the API.
Bucket lifecycle settings must retain the durable prefixes above; object-only
credentials cannot inspect or change bucket-wide lifecycle rules.

Source contracts: [Access JWT validation](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/), [R2 presigned URLs](https://developers.cloudflare.com/r2/api/s3/presigned-urls/), [R2 lifecycle](https://developers.cloudflare.com/r2/buckets/object-lifecycles/).
