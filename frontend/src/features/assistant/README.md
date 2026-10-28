# Assistant integration contracts

`AssistantProvider` belongs below `StudioProvider` and above route content and
`AssistantWidget`. `AssistantPage` and the widget share one controller per user
and project. The widget is absent on the full assistant route. Composer drafts
remain React-controlled across remote history updates; no token or reply update
replaces the textarea element.

`ConversationController` saves the user turn with its optimistic database
revision before starting `/api/assistant/chat`. Provider completion is saved by
`ConversationTurn` on the server. An interrupted listener does not claim to
cancel a server operation: the controller polls for a newer durable revision.
Conflicting unsaved local edits require an explicit reload/discard decision.
All current API-listed model IDs are displayed unchanged. Claude presets remain
in the backend catalog. Provider failures do not switch the selected model.

Every turn captures the current route and committed GET evidence from the
`StudioEvidenceRegistry`, even without a manual pick. The capture filters route,
project and account ownership and excludes credential APIs and private pages.
Raw DOM text and form values are not scraped. An exact selected record coexists
with those page sources. Context, draft text, preferences and file IDs are copied
before refresh/save begins, so later navigation cannot mix route versions into
the turn; a new draft typed during saving remains intact. Request compaction adds
explicit omission markers and leaves saved history and picked evidence unchanged.

`FileUploadField` wraps the actual free BoardUI FileUpload source installed by
CLI 0.5.8. Its integration patch removes demonstration upload timers and random
progress, adds controlled multiple selection, accept/disabled/label props, and
hides inactive upload copy from assistive technology. Selection is separate from
transfer. `UploadController` receives actual XHR byte events from `ApiClient`:
100% bytes is still importing until the server returns. A single multipart batch
has aggregate byte progress. Unknown totals remain indeterminate. Cancellation
rejects late completion and reports that admitted server work may continue.

AgentThinking is the installed free BoardUI component. Tool operations,
attachment metadata and search records are TinyStudio presentation composed
with ordinary semantic controls. Agent Progress, Composer Attachments and Web
Search Pro source has **not** been installed or claimed; the user authorized free BoardUI plus these custom TinyStudio components on
2026-10-26. No paid source dependency remains. Component license/provenance is retained under
`components/boardui` by the foundation owner.

Private uploads use `/api/p/{slug}/assistant/attachments`: config, multipart POST,
metadata GET and DELETE. Limit: 10 MiB/file and eight files/turn. Raw bytes use the
existing private artifact store with per-project/user keys. PostgreSQL contains
bounded extraction (40,000 characters/file) and metadata; conversation JSON
contains metadata and IDs, not bytes or extracted text. Scanned/image files are
rejected. Server-injected extracted context remains untrusted and bounded.

Native search was checked against installed OpenAI 3.36.0 and Anthropic 2.1.0
SDK signatures and their primary documentation on 2026-10-26:

- https://developers.openai.com/api/docs/guides/tools-web-search — Responses
  native `web_search`, real search-call state, URL citation annotations and
  `include=["web_search_call.action.sources"]` for genuine returned sources even
  when the answer has no citation annotation.
- https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool —
  native `web_search_20250305`, server-tool-use queries, search results and
  in-band failure records. Provider/model rejection remains visible; no provider
  or model is silently substituted.

Targeted tests exercise real selection/removal/rejection, transport/import phase
separation, cancellation, stale-scope reads, dirty conflicts, stream framing,
Strict Mode composer focus/caret stability, private file ownership, extraction
bounds, old snapshot compatibility, native search tool requests and source
sanitization. SDK boundaries are mocked in source tests; these are not evidence
of live provider execution or a database migration on the running deployment.

Live acceptance: upload a named disposable text fixture, send it with a picked
rendered evaluation record, confirm observed tool/search records with a configured
provider, reload in an independent authenticated tab, and inspect the same
message metadata/citations. Attempt the same attachment ID as another user and
project; it must fail. Switch project during a pending stream/upload; old data
must not appear. Delete the fixture upload through the explicit confirmation.

## Provider-only verification, 2026-10-26

Two bounded real OpenAI `gpt-4.1-mini` requests used a public Cloudflare R2 pricing
query, 600 maximum output tokens and one native search call each. Catalog/UI
tools were absent; no project or conversation records were accessed or changed.
The first request exposed missing source records when an answer contained a
plain URL without URL-citation annotations. The adapter was corrected to request
the documented source include path, with a regression test. The second request
returned search ID `ws_0ad84dd3c137c315006adfdf3348e887d18d626022a5468f9d`, observed
`in_progress → searching → completed`, 12 actual source URLs and 55 output
tokens. Its exact returned Cloudflare R2 pricing URL resolved with HTTP 200 to
`developers.cloudflare.com/r2/pricing/`. This verifies the provider adapter only;
it is not proof of the assistant UI, database persistence or evaluation review.

The component catalog imports the actual production `AssistantActivity`,
`AssistantAttachments` and `AssistantSources` exports from `display.tsx`.

## Reviewed frontend recovery paths

Local provider/model edits have an explicit version separate from remote history
and survive automatic sync until acknowledged by a write. Execution errors remain
visible after the database saves a partial turn and after subsequent refreshes.
The frozen request is validated before the durable user-message write. New draft
text entered during refresh/save is retained, including its caret and focus.

An upload batch owns its complete transfer sequence. Cancellation prevents later
files from starting; failed/cancelled files remain selected for retry, while
accepted files are removed from the pending list and never retried implicitly.
Duplicate batch starts are disabled and rejected by the batch controller.

Phone picking collapses the assistant drawer while keeping its composer and
picker listener mounted. Choosing evidence, Escape or Cancel restores the same
textarea, draft, caret and focus. The full assistant page provides an explicit
return to the last research route rather than starting a picker on itself.

The assistant uses installed free BoardUI Select, Checkbox, Textarea and Button
components. Custom display patterns use semantic colors, composite typography
and progressive scroll fades. These remain TinyStudio-owned patterns, with no
paid source or license involved.

Saved streams announce `conversation_admitted` with the committed claim revision;
a final `conversation_error` repeats that same turn's revision. The controller
keeps this CAS base for retrying an unsaved generated reply. It never promotes a
different writer's revision, and a newer database writer still causes 409.
Clean remote preferences adopt a meaningful empty model as Provider default.
Outstanding local provider/model choices remain separate until acknowledged.

Published conversation snapshots are deeply readonly and recursively frozen.
The controller copies incoming server objects, selections and attachment arrays;
caller mutation cannot bypass revision tracking or notifications. Already-owned
frozen subtrees are shared across updates, including token deltas, so snapshots
retain stable identity between publications without copying the entire history.

## Safe assistant message formatting

`AssistantMessageContent({role, content})` is the shared production and catalog
renderer. User messages remain literal text. Assistant replies lazy-load the
Markdown parser and support emphasis, code, lists and GFM tables. The companion
`AssistantMarkdownSample` export in `messageContent.sample.tsx` contains only
illustrative public fixture data; it imports that same production renderer.

Pinned dependencies: `react-markdown@10.1.0` and `remark-gfm@4.0.1`, both MIT.
Primary sources checked on 2026-10-27:

- https://github.com/remarkjs/react-markdown — React element rendering without
  `dangerouslySetInnerHTML`, `skipHtml`, custom components and URL transforms;
  installed package metadata declares React and React types >=18.
- https://github.com/remarkjs/remark-gfm — GFM tables, lists, autolinks and other
  extensions implemented as a remark plugin.

The product sets `skipHtml`, uses an explicit allowed-element list, replaces
images with non-fetching text placeholders, and accepts only explicit HTTP(S)
links without URL credentials or control characters. Links use `noopener
noreferrer`; relative/app URLs and other schemes remain noninteractive text.
No raw-HTML plugin is installed. A message-local formatting boundary preserves
current plain text if the lazy module or renderer fails, leaving the composer
and other conversation displays mounted.

Installed license/notice texts for the two packages' runtime dependency closure
are appended to `frontend/public/THIRD_PARTY_NOTICES.txt`, retaining previous
blocks verbatim. An isolated temporary Vite build confirmed the parser is a
separate 157,924-byte dynamic chunk (about 47.34 KB gzip), not a static entry
import. This is source/build evidence; the parent owns the final served-release
reload and stored-history formatting check.
