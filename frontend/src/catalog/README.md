# TinyStudio development component catalog

`ComponentCatalogPage` is exported by `frontend/src/catalog/index.tsx`. The parent application may expose it through a development-only route. It is intentionally absent from production navigation and must not be used as a customer-data export or evidence of successful provider work.

All records, byte counters, states, attachments and source links are visibly **illustrative**. Controls change local React state. Selecting files retains browser `File` references only; this page calls no API, starts no model, uploads no bytes, and instantiates no background controller. There are no fake transport timers. The free Thinking example disables its elapsed timer and uses a manually toggled CSS sparkle indicator.

## Interactive AffectUI template gallery

`ReusableTemplateGallery` in `templates.tsx` renders the actual shared
`CollectionTemplate`, `DetailTemplate`, `AnalysisTemplate`, `RunTemplate`,
`SettingsTemplate`, `SetupTemplate`, and `ConversationTemplate`. Their controls
import the adapted AffectUI Button, controlled FilterMenu and SegmentedControl.
The public source license and exact provenance remain under
`components/affectui/LICENSE` and `components/affectui/provenance.json`.
The CRM and Evals references guide composition; no licensed Pro screen source
is included.

The gallery adds interactive local examples before the existing catalog:

- Collection search and kind filters, independent selection and inspection.
- Exact record navigation with synchronized goal and metadata.
- Authored daily request counts on a fixed 0–5 request scale, a seven-day or
  three-day window, and selectable zero counts. These are not model landscapes.
- Explicit failure/success run fixtures and selected stage evidence.
- Validated local settings drafts, save and reset.
- A setup description/review flow that retains drafts and creates no project.
- A local message composer that appends user text without generating a reply.

Switching templates unmounts and resets the previous example. Every template
names its illustrative scope, and all actions stay inside component state.
`templates.test.tsx` checks filters/recovery, exact selected values, zero counts,
failed-stage consequences, validation/draft retention, and absence of HTTP calls.

## Source and license

These are actual installed free BoardUI components, imported from their source:

- `components/boardui/base/buttons/button`: `Button`
- `components/boardui/base/input/input`: `Input` (React Aria label/input/hint associations)
- `components/boardui/application/agent-thinking/agent-thinking`: `AgentThinking`
- `components/boardui/base/file-upload/file-upload`: used internally by the production `FileUploadField`

The official pinned CLI installation and MIT license are retained in `components/boardui/PROVENANCE.md` and `components/boardui/LICENSE`. No Pro license, activation or source is used.

`UploadStatus`, `NeuronMap`, `LabelBars`, `AssistantActivity`, `AssistantAttachments` and `AssistantSources` are TinyStudio-owned product patterns. The assistant patterns provide requested activity/attachment/search-source presentation using free components and the product visual system. They are not represented as installed BoardUI Pro blocks.

## Stable consumer examples

```tsx
import { Button } from '../components/boardui/base/buttons/button';
import { Input } from '../components/boardui/base/input/input';
import { AgentThinking } from '../components/boardui/application/agent-thinking/agent-thinking';

<Input label="Name" value={name} onChange={setName} hint="Visible field guidance" />
<Button variant="secondary" onClick={onInspect}>Inspect</Button>
<AgentThinking label="Request active" variant="stars" showTimer={false} />
```

`Button` supports `primary | secondary | ghost | danger`, standard button events and disabled state. `Input` owns accessible labeling through React Aria and uses `onChange(value: string)`. `AgentThinking` accepts `variant: wave | spin | stars | infinity`, `label`, `tone`, `shimmer`, and `showTimer`. In production it describes request activity rather than hidden reasoning.

```tsx
<FileUploadField
  label="Source files" accept=".txt,.md" multiple
  files={files} onChange={setFiles} maxBytes={1048576}
/>
<UploadStatus
  phase="uploading" progress={{ loaded: 256, total: 1024 }}
  onCancel={cancel} onRetry={retry}
/>
```

The shared upload contract accepts controlled `File[]`, optional accepted types, multiple-file policy, byte limit and disabled state. It validates/rejects selection and allows removal. `UploadStatus` supports `selected | uploading | importing | indexing | ready | failed | cancelled`. Progress is `{loaded, total?}` from actual transport or a numeric percentage when a consumer has an independently measured value. Unknown totals remain indeterminate. Reaching the transfer total does not imply that server import/indexing succeeded. Cancellation cannot undo an admitted server task.

```tsx
<NeuronMap
  label="Layer overview" rows={rows}
  selected={neuron} onSelect={setNeuron}
  xLabel="Token density" yLabel="Maximum activation"
/>
<LabelBars
  pairs={labelShares} denominator={distinctDatapoints}
  selected={label} onSelect={setLabel}
/>
```

Map rows have `{neuron: number, x: number | null, y: number | null}` plus evidence fields. Undefined/nonfinite coordinates are omitted explicitly. Click/Enter/Space select a neuron; arrow/Home/End keys move focus. A selected record can remain in the detail view even when it has no plotted point. `LabelBars` accepts `[label, share][]`; `share` is a fraction with the distinct retained-datapoint denominator passed separately. Counts must be computed from unique datapoint identities before presentation; repeated tokens do not increase weight. Several labels can belong to the same datapoint.

```tsx
<AssistantActivity calls={calls} searches={searches} uiRequests={uiRequests} />
<AssistantAttachments
  attachments={attachments} onRemove={removeFromDraft}
  onDelete={requestPrivateFileDeletion} disabled={busy}
/>
<AssistantSources sources={actualReturnedSources} />
```

Types `ToolCall`, `Attachment`, `Message`, and `Source` come from `controllers/conversation.ts`. Activity phase derives from actual returned call output/error; missing output means running. Sources permit HTTP(S) links only. Attachments show byte and extracted/retained-character counts. Removing from a draft and deleting a stored private upload are separate caller-owned operations; real deletion still requires the production confirmation/ownership boundary. The catalog implements both callbacks as local example removal and explicitly says no stored file was deleted.

## Ownership and lifecycle

The catalog uses ordinary declarative React state because it has no durable identity or resources. Product lifecycle responsibilities remain with focused owners:

- `UploadController<T>` owns one aggregate transfer, abort/generation checks and transport/import distinction. Dispose it on unmount/scope change.
- `ConversationController` owns identity-scoped revision/history/draft/write/stream/sync transitions. Display components never own transport or persistence.
- `WorkspaceScope` and the API client reject obsolete account/project requests.
- `EvidenceRegistry` owns current rendered data registration; `Evidence` wrappers register and unregister on mount/unmount. Catalog labels identify their data as illustrative.

Do not move those controllers into component examples or duplicate their state machines. Product consumers provide real observed events; catalog controls provide clearly labeled example state.

## Verification

Run `npm --prefix frontend test -- src/catalog/catalog.test.tsx`. Fixtures assert local cancel/retry/import transitions, accepted-file removal, linked exact neuron identity, undefined-point omission, label denominator, attachment removal, and safe source rendering. A network spy ensures these examples issue no HTTP request. This is component behavior evidence; it does not establish live upload/provider behavior or whole-app viewport acceptance.

## Actual application harness

`#/__preview` uses `WorkspacePreview` to mount the real application Shell and route components against explicit illustrative read fixtures. It does not copy feature screens. All writes, uploads, downloads, unknown reads and direct assistant/model transports are refused. The normal `/ui/#/datasets` entry retains real authentication and API providers. Neither development catalog nor preview is bundled in production.

The collection example includes `SimpleTable`, `DataTable` and `AdvancedTable`; production routes import those same variants. Source values control sorting/filtering independently of formatted cell content, and protected actions retain their record identity.
