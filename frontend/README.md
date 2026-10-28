# TinyStudio frontend

The application uses React 19, TypeScript, Vite, Tailwind CSS 4 and installed free
BoardUI source. FastAPI serves the built application on its existing origin;
Node is a development/build dependency, not a Python service runtime dependency.
Direct dependency versions and resolved transitive versions are pinned in
`package.json` and `package-lock.json`.

## Development and the reusable catalog

From the repository root:

```sh
npm ci --prefix frontend
npm run dev --prefix frontend -- --port 5173
```

Open [the development application](http://127.0.0.1:5173/ui/#/datasets). Vite binds
to `127.0.0.1`; its `/api` and `/v1` proxy targets the local API on port 8000.
Ordinary application routes still require the backend's configured authentication,
including Cloudflare Access where enabled. Development does not bypass that gate.
Vite has `envDir: false`: do not copy server credentials into frontend files or
introduce `VITE_` secrets.

Open [the local component catalog](http://127.0.0.1:5173/ui/#/__components) to
inspect reusable examples without an account. This **development-only** route
renders outside the authenticated providers. Its runtime has no authenticated
user, uses an `ILLUSTRATIVE_LOCAL_ONLY` project identity and an isolated evidence
registry, and rejects all API/transport operations. Examples change local React
state; they start no model requests, uploads or task polling. Browser requests
for Vite's own HTML/modules are still necessary to load the development page.
Catalog records, activity, byte counters and source trails are visibly
illustrative. The catalog branch and module are excluded from production builds;
it is not a customer-data export or evidence that a provider operation succeeded.
See [catalog contracts and examples](src/catalog/README.md).

## Checks and production build

```sh
npm run typecheck --prefix frontend
npm test --prefix frontend
npm run format:check --prefix frontend
npm run build --prefix frontend
```

`format` writes authored source formatting; `format:check` only checks it.
Upstream BoardUI components and installed upstream theme styles are excluded.
Coordinate formatting in this shared checkout rather than changing another
worker's active files.

The build performs TypeScript checking and emits the HTML, manifest, CSS, hashed
entry and route chunks into `tinystudio/ui/dist/` with `/ui/` as the asset base.
Data, interpretability and workspace routes are loaded lazily. The assistant and
session providers remain available across navigation. A visible loading state
covers pending page code; a failed lazy import offers a full reload because
React caches the rejected import. Ordinary page render failures have a local
retry and do not remove workspace navigation.

From the repository root, the packaging checks are:

```sh
python scripts/check_react_assets.py
python -m build --wheel
python -m pytest --noconftest -q tests/test_frontend_release.py
```

Use the project's Python environment. The Hatch hook in
`scripts/build_frontend_hook.py` requires an already complete frontend build. It
validates the graph before packaging, rejects assets changed during packaging,
and compares the wheel's asset set and bytes against that build. It does not
invoke npm or download dependencies. `check_react_assets.py --wheel <wheel-path>`
can independently check an existing wheel. An unbuilt or incomplete application
returns an actionable 503 rather than silently serving the historical native UI.

## Ownership and lifecycle

| Boundary | Responsibility |
| --- | --- |
| `app/StudioContext.tsx` | Auth/setup recovery, selected project, overview, task surface, confirmations and typed feature composition. Route subtrees reset on account/project changes. |
| `controllers/workspaceScope.ts` (`WorkspaceScope`) | Account/project request leases, abort signals and rejection of late responses even when transport cancellation is ignored. Route changes do not revoke durable conversation saves. |
| `api/client.ts` | Project URLs, same-origin cookies and CSRF header, expected conversation owner, errors and measured XHR upload progress. External presigned transfers omit application credentials. |
| `useResource` and `app/evidence.ts` | Reads keyed by API path and workspace; unchanged data survives query-only neuron selection. Committed safe GET data and rendered evidence are registered under the current route and removed on teardown. Sensitive endpoints/fields are excluded or visibly redacted; bounded context has explicit omission markers. |
| `controllers/conversation.ts` (`ConversationController`) and `controllers/conversation-sync.js` (`ConversationSync`) | Conversation revisions, queued writes, dirty/live-turn conflicts, scoped polling/tab notifications and explicit resource cleanup. They preserve the database as durable authority. |
| `controllers/playground.ts` (`PlaygroundController`) and `app/PlaygroundProvider.tsx` | One active inference stream, immutable observed snapshots, cancellation/reset and completed history across ordinary routes; workspace identity changes reset the session. |
| `controllers/uploads.ts` (`UploadController` / `UploadBatchController`) | Selected files, actual transfers, aggregate progress, cancel/retry and distinct importing/indexing/ready states. Transferred bytes alone do not prove server import success. |
| `features/interpretability/selection.ts` (`EvidenceSelection`) | Selected artifact request ownership, cancellation and synchronous masking of results from an obsolete selection key. Plot captions resolve interaction identities against current rows rather than retaining old measurements. |
| `app/ModalStack` | One dialog stack, top-dialog keyboard/focus ownership, background isolation, exact scroll restoration and focus return. React Aria popovers portal inside the owning dialog and consume Escape before the parent. |

The classes above encapsulate identity, mutable transitions or resource
lifetimes. React components own declarative presentation and local interaction;
pure scientific transformations remain functions. The architecture does not use
component inheritance or a universal string-based page renderer.

## Serving and retained releases

On application construction, `tinystudio/frontend_release.py` validates the
built graph and snapshots its bytes under
`<TINYSTUDIO_DATA_DIR>/frontend-releases/<content-digest>/`. It verifies per-file
SHA256 records, stages and publishes the release under a publication lock, and
rejects source changes observed during copying. Reusing the same release checks
cached bytes rather than rewriting them. Symlinks, incomplete or corrupt inputs,
and a changed asset with an unchanged hashed URL are rejected.

A running application serves its captured directory, so Vite's later
`emptyOutDir` build cannot remove its active chunks. A restart selects a complete
new build. Prior verified hashed assets remain available for old tabs; fallback
never serves an old index, manifest or arbitrary prior file. If the current build
is missing, the index stays 503 while retained hashed chunks can still load.

The helper never prunes release directories or automatically rolls back to an
old index. Retention therefore consumes disk until an operator manages it. A
rollback requires selecting a complete prior build and restarting the service.
The cache isolates serving from builds; it does not prevent a privileged user
from editing cache files. Integrity is checked at startup, not continuously on
every static response. Explicit `ui_directory` test overrides retain the native
compatibility-serving behavior.

## Components, palette and notices

Installed BoardUI source and deliberate adaptations are identified in
[`PROVENANCE.md`](src/components/boardui/PROVENANCE.md); the original
[MIT license](src/components/boardui/LICENSE) is retained. No Pro components or
license activation are implied by TinyStudio's custom activity, attachment and
source-trail patterns. The actual installed Remix Icon package declares its own
Remix Icon License 1.0; it is not labeled Apache-2.0 here.

[`public/THIRD_PARTY_NOTICES.txt`](public/THIRD_PARTY_NOTICES.txt) carries the
BoardUI copyright/license and installed runtime notices into Vite's output.
Shared styles use BoardUI semantic tokens with neutral graphite/white and
charcoal surfaces. Blue indicates selection/focus; scientific and status colors
retain their separate meaning. Light/Dark/System remain user controls.

All active React upload consumers use `FileUploadField`: dataset JSONL/source
modes, repository documents, assistant attachments and catalog examples. The
only authored React `type="file"` is inside the installed/adapted BoardUI upload
component. Historical native dataset/repository inputs remain under
`tinystudio/ui/` for explicit compatibility use; they are not part of the
production React entry graph.

## Evidence limits

The source checks cover cancellation, stale responses, revision conflicts,
scoped evidence, partial scientific data, modal/portal focus, upload phases,
route loading failures and controlled provider boundaries. Pure temporary-file
tests cover snapshot races, corruption, reuse and retained chunks. These checks
do not establish browser positioning, physical-device behavior, live provider
success, Cloudflare acceptance, container deployment or every route's end-to-end
workflow. Those require separate current served-build and user-path evidence.
