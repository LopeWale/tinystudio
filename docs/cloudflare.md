# Cloudflare deployment

TinyStudio can use private R2 storage and Cloudflare Access while its API, worker, PostgreSQL, Redis, and Qdrant run on the origin machine. Large artifact transfers use S3 directly. No Cloudflare Workers, Queues, Containers, or Vectorize are required.

Set `TINYSTUDIO_STORAGE_BACKEND=r2`, `R2_ENDPOINT_URL`, `R2_BUCKET_NAME`, `R2_REGION=auto`, `R2_ACCESS_KEY_ID`, and `R2_SECRET_ACCESS_KEY` in a private `.env`. The bucket credential needs Object Read & Write on this bucket only. With `local` (the default), existing database snapshots and local model artifacts keep working.

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
- `captures/PROJECT_ID/`: activation captures and comparisons. The existing bucket lifecycle expires this prefix after 30 days. Readers check remote availability before using a cache and report expired captures as unavailable.
- `uploads/PROJECT_ID/`: short-lived staging uploads. Successful dataset imports remove their staged object. Failed or abandoned uploads may need operator cleanup.

Objects remain private; no public R2 custom domain is needed. Browser CORS permits the public origin and the configured localhost development origins. Checkpoint history uses step-numbered keys, not bucket versioning. Existing artifacts are not bulk migrated; newly produced artifacts use the selected backend.

Source contracts: [Access JWT validation](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/), [R2 presigned URLs](https://developers.cloudflare.com/r2/api/s3/presigned-urls/), [R2 lifecycle](https://developers.cloudflare.com/r2/buckets/object-lifecycles/).
