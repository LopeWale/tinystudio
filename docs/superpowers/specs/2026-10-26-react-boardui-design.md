# TinyStudio React and BoardUI migration

Status: implementation and acceptance. The user selected **migrate to React and
use BoardUI components**, then directed immediate parallel implementation in
the current checkout and branch on 2026-10-26. The user selected **free BoardUI
plus custom versions of the Pro patterns**. No paid source or activation is
used. The palette is neutral graphite; color identifies selection, status and
scientific data. Current validation and live acceptance are recorded in the
changelog rather than inferred from this design.

## Intent

Make TinyStudio a coherent model-development workspace where someone can find
a result, explore why it happened, attach its evidence to the assistant, and
continue working without losing project or conversation state. The immediate
visual failure is the selected neuron at `#/interp/c/smol-lora-1--commands?layer=15&neuron=433`:
opaque sign chips and repeated tokenizer fragments obscure the useful evidence
at a 390×844 viewport. The migration must improve that information hierarchy
as well as the underlying component architecture.

The reference inventory and verified current contracts are in
[`docs/ui-reference-study.md`](../../ui-reference-study.md). Marketing pages
provided visual direction; authenticated TinyStudio and its source provided
the actual data and behavior constraints.

## Goals and scope

1. Migrate every existing frontend route, dialog, and global surface to React.
   Preserve hash URLs, query parameters, authentication, current project,
   background tasks, settings, model operations, and ordinary keyboard paths.
2. Use real BoardUI source and its semantic tokens for shared controls,
   uploads, and assistant presentation. Keep provenance and applicable license
   notices. BoardUI is a source-component dependency, not a screenshot to copy.
3. Replace every browser file-picker surface with one reusable upload system.
4. Add linked neuron exploration and consistent chart/table/detail interaction
   across training, evaluation, datasets, and interpretability.
5. Present actual assistant work, attachments, and web sources using the
   requested component patterns. Preserve database persistence, automatic
   cross-device/tab sync, optimistic conflicts, untrusted context boundaries,
   and destructive-action gates.
6. Deliver a reusable component catalog with examples and contracts, and verify
   the built app through every live page.

This work changes the frontend and the small backend contracts needed for
attachments/search and static asset delivery. It does not change model-training
algorithms, vector storage, Cloudflare resources, access policy, or credentials.
The unrelated dataset-quality work already in this shared checkout remains
owned by its existing author. No commit, push, or paid purchase is implied.

## Approaches considered and decision

| Approach | Tradeoff | Decision |
| --- | --- | --- |
| Shared native modules with adapted reference patterns | Lowest migration cost and strong reuse of existing view code; does not adopt BoardUI's React components directly | Parent's original recommendation; user chose a different approach |
| React application with BoardUI and retained service contracts | Directly adopts the requested components and establishes a consistent component system; requires a complete route migration, a build step, and new acceptance evidence | **Selected by the user** |
| React islands mounted inside the current imperative pages | Allows early local adoption but introduces two view owners and two lifecycle systems | May be used only as a temporary development bridge with a removal gate; not the delivered architecture |

The tooling below implements the selected approach. Routine reversible
implementation choices fall within the user's execution instruction.

## Application architecture

Use React 19, TypeScript, Vite, Tailwind CSS 4, and BoardUI. Preserve hash routing
through React Router. Resolve compatible versions from primary documentation
and pin the installed versions in the lockfile. A Node process is needed for
development/builds; FastAPI continues to serve the production application.

Proposed source layout:

```text
frontend/
  src/
    app/                 composition, route registry, providers, errors
    components/boardui/  installed upstream components and provenance
    components/studio/  product wrappers, linked charts, reusable patterns
    controllers/        workspace, conversations, uploads, evidence ownership
    api/                typed contracts, scoped transport, streaming adapters
    features/           React pages and their feature-specific dialogs/hooks
    styles/             BoardUI tokens mapped to TinyStudio's visual identity
    catalog/            reusable component examples and states
  tests/
tinystudio/ui/dist/      generated production index, manifest, hashed assets
```

Components own presentation and local interaction. Controllers own durable
identity-scoped state, cancellation, concurrency, and transport orchestration.
Use functional React components; object-oriented controllers have focused
responsibilities and explicit dependencies rather than inheritance trees.

| Controller/boundary | Owned responsibility | Lifecycle contract |
| --- | --- | --- |
| Workspace session | Authenticated user, selected project, scope generation | Invalidate pending reads and project/account state on identity change |
| API client | Scoped URLs, cookies, CSRF header, error interpretation | Accept cancellation; never adopt a response from an obsolete scope |
| Page evidence registry | Route and rendered API records, selected item, sensitive-field exclusion | Register data on committed rendering; unregister on route unmount |
| Upload queue | Accepted files, per-file/overall phases, retries and cancellation | Dispose transfer/listener resources on close, navigation, or scope change |
| Conversation controller | Database revision, write queue, live reply, remote checks, draft ownership | Preserve conflict protection; dispose timers/channel/listeners exactly once |
| Evidence selection | Artifact/layer/neuron identity and linked plot/row/detail state | Cancel obsolete requests and reject late results before publication |

Adapt the existing non-DOM conversation scheduler and request-budget logic
where practical. React subscriptions use immutable cached snapshots and
`useSyncExternalStore`; development Strict Mode must not duplicate timers,
channels, uploads, or model requests. Feature modules receive typed boundaries
through composition. Do not create a universal page renderer that hides
feature behavior in strings or opaque configuration.

## Serving, packaging, and cutover

Build with Vite's `/ui/` asset base into `tinystudio/ui/dist`. During migration,
an explicitly selected development entry may serve the React build on the
same FastAPI origin. Production switches to the built React index and static
directory only after all route and behavior gates pass. Preserve `/api`, `/v1`,
`/docs`, downloads, and existing Cloudflare authentication.

The Python wheel must include the generated index, manifest, CSS, and every
referenced asset; it must not require npm at runtime. CI/release commands build
the frontend before packaging. A package test walks the emitted asset graph
and compares packaged bytes with the verified build. Build output must contain
no environment credentials. Missing assets produce an actionable failure,
rather than an apparently successful empty interface.

Record the current served release and preserve its assets for a controlled
rollback before cutover. Recheck checkout ownership and hashes before changing
overlapping files or restarting the project service. Keep the running live app
available during implementation. At completion every route renders React and
temporary legacy bridges have been removed from the production graph.

## Visual system and whole-app layout

Retain TinyStudio's purple accent, project identity, Light/Dark/System choices,
and dense scientific workspace character. Map them onto BoardUI semantic text,
surface, border, interaction, and chart tokens. Use readable body text and
tabular numbers; reserve monospace for identifiers and raw evidence. Avoid
nested decorative cards and controls that look interactive while disabled.

Use a shared application shell, grouped navigation, breadcrumbs, page heading,
primary action, filters, data region, and feedback patterns. On phones use
task-focused stacked content and a reachable selected-detail view. At wider
widths show linked views side by side. Preserve focus return, Escape handling,
modal containment, skip navigation, reduced motion, accessible names, and
theme contrast. Tables may scroll within their own region, but important
exploration must also be usable without navigating a wide table.

The route acceptance matrix follows the existing `tinystudio/ui/routes.js`:

| Group | Hash routes |
| --- | --- |
| Entry and data | `#/` → `#/datasets`, `#/datasets`, `#/datasets/:name`, `#/repositories` |
| Train and evaluate | `#/finetuning`, `#/finetuning/:job`, `#/evaluations`, `#/evaluations/:name`, `#/metrics` |
| Explore and serve | `#/playground` (including `?model=`), `#/assistant`, `#/interp`, `#/interp/c/:capture` and `#/interp/d/:comparison` (including queries), `#/models`, `#/traces` |
| Workspace | `#/projects`, `#/api-keys`, `#/stats`, `#/settings`, `#/users` |

Also cover sign-in, sign-out, first-user/project setup and recovery, the task
drawer, confirmation dialogs, and floating assistant. These are global states,
not additional route families. Repository documents and their search dialog
remain part of `#/repositories`.

## Interactive exploration

### Capture and selected neuron

Desktop: compact capture/layer controls, a layer overview plot, a searchable
neuron list, and the selected evidence view. Phone: overview/list and selected
detail are explicit views with a clear return action; a deep link opens the
selected detail directly. Keep layer, neuron, sort, and applicable filters in
the hash query so reload/back/share restore the same selection.

The overview uses maximum activation and token density from the existing layer
rows; color can encode echo share. Fetch the complete layer using the existing
paging contract. Label any partial result and exclude undefined coordinates
explicitly. Clicking or keyboard-selecting a plotted point or neuron row
updates one shared selection and the URL. The detail selection remains visible
when filtering the list excludes its row.

The selected detail contains:

- Layer/neuron identity, a short saved explanation when present, and four
  readable metrics: overall maximum, gate-open maximum, minimum, token density.
- Named gate/value regimes with sign notation available as supporting detail.
  Counts use retained top-k positive tokens, not the displayed example count.
  Gate/pole filters explain which retained evidence is being shown.
- Evidence tabs for **Examples**, **Labels**, **Explanation & tests**, and
  **Checkpoint drift** when data exists.
- Example cards leading with readable user text, activation, active token,
  labels, and datapoint identity. Expand a card for exact token context,
  numeric gate/value, position, bucket, and raw tokenizer boundaries. Keep
  positive/negative color meaning consistent and provide a signed legend.
- Label bars show shares of distinct datapoints among the retained pole;
  repeated tokens do not increase a datapoint's weight. Display the actual
  denominator and distinguish it from the whole corpus.
- First-token and echo warnings sit next to the affected interpretation and
  remain visible without overwhelming the examples.

Use existing retained token contexts; do not imply the API contains arbitrary
per-token activation histories. Explain/Test controls retain their real
background jobs and costs. Browsing a neuron does not launch a helper model.

### Comparisons and checkpoint drift

Link correlation/change plots, filtering, the neuron list, and before/after
token evidence through one selection. Preserve undefined correlation for
constant activations. Both capture and comparison requests use a selection
generation and abort signal; earlier responses cannot overwrite a new neuron,
layer, route, project, or account. Show checkpoint drift as an interactive
step sequence tied to label overlap and retained examples, with its consistent
gate-open/positive-pole fallback described.

### Other pages

Reuse chart interaction primitives: consistent colors/legends, hover and
keyboard details, a visible selection, data attachment, and reset controls.
Training loss plots link to the selected run/step/checkpoint; evaluation
distributions filter the actual result table and selected model/metric;
dataset quality views lead to the matching examples where the API supports
that query. A plot without underlying records must say what its selection
represents and must not offer a misleading drilldown.

## Upload system

Install and wrap BoardUI File Upload. Use one `UploadField` and `UploadQueue`
contract across JSONL import, synthetic source files, repository documents,
and assistant attachments. Expose accepted types, limits, single/multiple
policy, removal, validation errors, progress, cancel, retry, and completion.
Keep URL inputs independent. A new source-mode selection keeps compatible
files and explains rejected ones instead of silently reusing an incompatible
file list.

The storage adapter supplies real bytes transferred where measurable, using
an upload-capable browser transport. Unknown totals show an indeterminate
state. R2 requests omit app cookies/Access credentials and use the granted
headers. Same-origin multipart requests retain CSRF, cookies, and project
scope. Distinguish **selected → uploading → importing/indexing → ready** from
**failed/cancelled**; 100% transferred does not imply import success. Avoid
per-file percentages when the server receives a single multipart batch and
only aggregate transfer progress is known.

Preserve the dataset R2 512 MiB limit and existing multipart contracts. New
attachment limits come from their server config and are shown before transfer.
Cancellation cannot undo a server operation already admitted; report that
state clearly and retain task controls when a background task exists.

## Assistant components and capability contracts

Use the same React assistant components in the floating panel and full-page
conversation. Keep one active composer, draft, model selection, and turn per
user/project. Remote updates and activity events patch state without replacing
the focused textarea. Retain all listed model IDs and existing Claude presets;
capability errors do not silently select a different model/provider.

### Thinking and progress

BoardUI Agent Thinking displays that a request is active, with elapsed time and
reduced-motion support. Agent Progress displays observed tool/UI operations,
not invented model reasoning. Steps enter running/completed/failed states from
real stream events, remain inspectable after completion, and can be collapsed.
Unknown future step counts remain unknown. No simulated execution percentages.

### Composer attachments

BoardUI attachment tiles represent actual selected page data and uploaded
files, with type/name/status/removal. Retain source route, project, user, and
data version. A stale pick is rejected before sending. Persist attachment
metadata with the user message so another device can inspect what was sent.

For files, add a private per-user/project attachment record and API, reuse the
existing artifact store and extraction facilities, and negotiate supported
formats/limits through a config response. Keep bytes outside the conversation
JSON; store attachment IDs, display metadata, and bounded extraction/evidence
references. Initial support covers formats the backend can actually extract.
Offer image input only when the chosen backend declares real image support;
otherwise reject it with an explanation. Do not silently accept unsupported
files. Combined message, page, selection, and extracted-file context stays
within the existing request budget with explicit omission markers. New
attachment records require ownership tests and a database migration.

### Web-search trail

Add an explicit search capability to the assistant request and server adapters.
Use the configured provider's documented native search where supported; no
additional search-provider credential is assumed. The selected model's
unsupported-search response is shown clearly. Never silently switch models
or fabricate a successful search.

Normalize actual search start/query/result/completion events and citations
into a small typed source-trail contract. Persist the resulting display
records with the assistant reply. Show only genuine returned sources and
validate outbound link schemes. Keep search results, extracted files, page
context, and tool outputs as untrusted data rather than instructions. The
delete tool, generic UI mutation, and existing confirmation gates retain their
current opt-in requirements.

## BoardUI provenance and license decision

Free components, including File Upload and Agent Thinking, are available
through the official CLI/public source. Agent Progress, Composer Attachments,
and Web Search are advertised as Pro. The user is being asked whether Pro is
available. With Pro, activate locally through the official workflow and install
the actual blocks. Do not put the license in chat, the bundle, or Git.

If the user chooses free components plus custom Pro-pattern implementations,
record those components as TinyStudio-owned and composed from free BoardUI
primitives. Do not call them installed Pro components. This choice does not
remove the requested activity, attachment, or source-trail behavior. Do not
purchase a license or bypass access restrictions.

Maintain a provenance file containing component origin, installed CLI/version
or source revision, license, and deliberate local integration changes. Patch
demonstration-only timers/progress behind the product adapter if necessary so
real transfer and event state controls the UI.

## Reusable artifact and Visual Truth

Expose a development component catalog using the same product components and
tokens, with upload/error/cancel, activity/complete/failure, attachment, search,
and linked-evidence states. Include concise usage examples and TypeScript
contracts; it is an app development artifact and never a customer data export.

Once a React entry exists, Visual Truth's editor can apply to a local preview.
Use the reported selector/viewport as the baseline, then inspect the durable
source change and real rendered result. Do not deploy the editor or change
production content through a transient browser patch.

Template Creator manages personal reference-backed artifact-template skills.
The supplied websites are not a supported reference type. If a supported
Product Design/ImageGen PNG or other supported artifact is selected later,
package it through Template Creator's actual script. The component catalog
does not by itself establish creation of a personal artifact-template skill.

## Delivery increments and ownership

The user's execution instruction authorized the bounded implementation plan
in `../plans/2026-10-26-react-boardui.md`. Parent owns integration, shared tokens, serving, and
release gates. Agent Team workers can own disjoint feature groups after the
typed contracts are established. No recursive team launch and no advisor.

Suggested coherent increments: (1) React/build/BoardUI foundation and controller
adapters; (2) shared uploads and assistant; (3) neuron/comparison exploration;
(4) remaining feature pages and dialogs; (5) package, live acceptance, and
cutover. Each increment gets independent review appropriate to its scope;
publication requires its own existing or newly granted authority.

## Acceptance and evidence

Tests derive from user-visible objectives and existing API contracts. Existing
native-renderer tests are historical baselines, not proof that React works.
Port their behavioral expectations to React tests before retiring replaced
view checks; retain independent service/backend tests.

| Objective | Required discriminating evidence |
| --- | --- |
| Existing workflows survive | Route inventory parity; real controls, forms, dialogs, loading/empty/error states on all list/detail/admin routes |
| Selection is reliable | Plot/row/deep-link resolve to the same neuron; delayed earlier responses cannot replace newer selection or another scope |
| Evidence is scientifically honest | Undefined correlation, echo, gate-open poles, first-token warnings, retained count/label denominators and truncation checked against independent fixtures/API records |
| Uploads are real | File rejection/removal, measured or indeterminate progress, import/index phases, transfer and server failures, cancel/retry, direct R2 and multipart paths |
| Assistant remains safe and durable | Real-key live turn, persistence on another session, automatic sync, dirty/stream conflict, account/project change, preserved draft/focus, secret redaction, untrusted context and delete/UI mutation gates |
| Search/attachments work | Real supported-provider search and linked source records; server ownership checks; attachment reload/resume and unsupported-format/model handling |
| Lifecycle survives React | Strict Mode mount/unmount, navigation during uploads/requests/streams, modal focus return, stopped timers/listeners/channels |
| Distribution is deployable | Production build, type checks, packaged asset graph/bytes, same-origin auth, no bundled keys, verified served release after cutover |

Use exact 390×844 reproduction plus desktop 1440×900, iPad 1024×1366, and phone
440×956. Verify all route families in the live app and inspect representative
interactions/data in each; viewport scans alone do not prove behavior. Use
disposable named fixtures for destructive/write paths and preserve production
research. Save a concise before/after screenshot and one real assistant
transcript reviewing the open evaluation. Report parent-run checks and
independent reader findings separately; unavailable reader runtimes remain
unverified. Record concise results in `CHANGELOG.md`.

## Primary implementation references

- [BoardUI installation and supported stack](https://www.boardui.com/installation)
- [BoardUI public source and license](https://github.com/BoardUI/boardui)
- [React external store subscriptions](https://react.dev/reference/react/useSyncExternalStore)
- [React Router routing](https://reactrouter.com/start/declarative/routing)
- [Vite backend integration](https://vite.dev/guide/backend-integration)
- [Tailwind Vite integration](https://tailwindcss.com/docs/installation/using-vite)

Provider search/image details must be checked against the installed SDK and
current official provider documentation during implementation before those
adapters are written. Supported catalog IDs alone do not establish capability.
