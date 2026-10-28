# Interactive exploration and shared component reference study

Research checkpoint, 2026-10-26. This records inspected references and current
contracts; it does not establish an approved design or implemented behavior.

## Reference observations

- [Together Post-Training](https://www.together.ai/post-training) presents the
  training loop as distinct stages and makes comparing candidate adapters a
  separate decision. Its public illustrations are product communication,
  not evidence of an authenticated training dashboard.
- [Autoresearch](https://autoresearch.sfcompute.com/) connects runs, samples,
  logs, metrics, and checkpoint branches. The relevant lesson for TinyStudio is
  keeping evidence connected to the run and checkpoint that produced it.
  Only its public page was inspected.
- [MLflow chart documentation](https://docs.databricks.com/aws/en/mlflow/visualize-runs)
  documents run selection, consistent chart colors, hover details, sorting,
  filtering, and parameter/metric comparisons. These are useful interactions
  to evaluate against TinyStudio's existing data, rather than decorative charts.
- [BoardUI File Upload](https://www.boardui.com/components/file-upload) pairs a
  keyboard-accessible drop surface with accepted-type/size guidance, rejection
  feedback, and a transfer indicator. Its demonstration's 8 MB limit is not a
  TinyStudio requirement.
- [BoardUI Agent Thinking](https://www.boardui.com/components/agent-thinking)
  gives an in-progress request a compact status and elapsed timer above the
  composer. Such a status does not disclose model reasoning.
- [BoardUI Agent Progress](https://www.boardui.com/components/agent-progress)
  presents collapsible steps and active/completed states. TinyStudio's actual
  stream events must determine those states; predicted steps or percentages
  would misrepresent execution.
- [BoardUI Composer Attachments](https://www.boardui.com/components/composer-attachments)
  uses removable attachment tiles above the prompt. Its documentation explicitly
  distinguishes the simulated demonstration queue from externally supplied
  transfer progress.
- [BoardUI Web Search](https://www.boardui.com/components/web-search) connects
  query/page activity to linked results. TinyStudio needs real search results
  before it can populate such a trail.

BoardUI documents React components. Agent Progress, Composer Attachments, and
Web Search are Pro components; their public previews were inspected without
accessing license-gated source. TinyStudio currently uses native ES modules.
After this study, the user selected migration to React with BoardUI components.
The full design is in
[`docs/superpowers/specs/2026-10-26-react-boardui-design.md`](superpowers/specs/2026-10-26-react-boardui-design.md).
That selection does not establish installation, licensing, or implementation.

## Current upload inventory

| Surface | Existing contract | Presentation gaps |
| --- | --- | --- |
| Dataset wizard, Upload JSONL | One `.jsonl` or `.json` file; direct private R2 PUT and object import when configured, API multipart otherwise | Custom drop area; no file removal, client rejection summary, transfer progress, or cancellation |
| Dataset wizard, synthetic sources | Multiple PDF, TXT, Markdown, or HTML files plus independent URL input; multipart submission followed by a background task | Appended names in plain text; no removable queue or upload/task phase distinction |
| Repository document dialog | Multiple files and URLs; multipart submission followed by indexing | Plain native input; the documented extraction formats are not reflected in `accept`; no shared queue |
| Model Upload to Hub | Publishes weights already on the server through `/api/hub/upload` | This is a background publication action, not a browser file picker; retain its target/privacy/merge controls |

Owning files: `tinystudio/ui/pages/datasets.js`,
`tinystudio/ui/pages/repositories.js`, `tinystudio/ui/pages/models.js`, and
`tinystudio/ui/core/api.js`. The R2 authorization/import contract in
`tinystudio/api/routes/artifacts.py` limits a staged object to 512 MiB. This is
not evidence of an equivalent limit on every multipart route. Existing fetch
calls expose no byte-progress callbacks.

## Neuron evidence and limitations

The reported capture is `smol-lora-1--commands`, layer 15, neuron 433. The live
page was inspected with a 390×844 layout, including the selected detail panel.
Its presentation combines four disabled sign-count chips, a gate filter,
warnings, twenty displayed activation examples, and label shares inside a
second independently scrolling region. Tokenizer boundary markers dominate
the readable examples. These are information-hierarchy problems even when
the page has no document-wide overflow.

`tinystudio/interp/neurons.py` already returns user text, token position,
before/token/after context, activation, gate, value, sign regime, labels,
bucket, and first-token flags. Capture detail distinguishes overall positive,
negative, and gate-open positive poles. Regime counts describe the retained
top-k positive tokens. Label shares use the distinct datapoints represented
in the retained pole; multiple tokens from one datapoint do not increase its
weight. They do not describe the whole dataset. The interface must keep those
denominators distinct.

The existing capture table supports paging up to 2,000 rows per request and
the current page requests 300. Comparisons request 200 rows and include
correlation, relative change, combined change, and token-delta context.
Existing fields can support linked summary plots over explicitly identified
rows. They do not provide arbitrary per-token activation traces. Showing a
full-layer plot requires fetching the complete layer, preserving undefined
values, and disclosing any remaining subset or filtering.

Capture details reject stale neuron responses. Comparison details currently
lack the equivalent per-selection guard between their asynchronous reads;
that needs an implementation regression check.

## Assistant contracts and reusable artifact requirements

The drawer and full-page assistant share a renderer. They currently accept
text plus sanitized route/API/selected-data context. Binary composer uploads
and genuine web-search events were not found in the current contracts.
Streaming supplies text, tool calls/results, UI actions, conversation-save
events, errors, and completion. Database messages persist text and bounded
tool displays under per-user/project revision protection.

A reusable component artifact must document its public inputs, owned state,
events, transport boundary, accessibility behavior, and teardown. Upload
selection, upload transport, assistant activity, attachment presentation,
and linked evidence selection have different ownership boundaries. Reuse
must preserve each surface's file policy and API behavior. Object-oriented
controllers are appropriate for owned state and lifecycle; pure numeric and
formatting transforms need no artificial class hierarchy.

Acceptance must include real transfer failure/cancellation, keyboard file
selection/removal, stable composer draft/focus during streaming and sync,
truthful source/progress states, project isolation, stale-response rejection,
and linked chart/row/detail selection. Preserve untrusted page context and
destructive-tool gates. Verify the recorded 390×844 case and wider layouts
through the actual app. Source tests alone do not establish visual acceptance.

## Workflow availability

Agent Team completed independent read-only inventories of uploads/assistant
contracts and interpretability contracts. Its restricted verifier returned
PASS for this research checkpoint only, with live/external observations and
Git ownership labeled as parent-supplied. The parent separately checked the
2,000-row ceiling in `tinystudio/api/routes/interp.py:123–125`. No advisor was
used. The user selected the React/BoardUI approach and directed immediate
parallel implementation, then selected free BoardUI with custom Pro patterns.
Visual Truth's React editor can apply to the React entry;
the supplied selector, viewport, and comment remain the visual brief.
ZzzOps reports a missing approved project
policy, so no goal queue was executed. Template Creator requires a supported
reference artifact; the supplied web URLs are research references, not a
supported template input. No personal artifact-template skill was created.

## Layout reference validation — October 27, 2026

Research only. The user stopped the proposed Lab reorganization pending this
study. The previously suggested four-section navigation is not an approved or
validated information architecture. No layout replacement was applied in this
checkpoint. This section records inspected patterns and verification criteria;
implementation history belongs in `CHANGELOG.md`.

### Source observations

| Inspected primary reference | Observed pattern | Implication to evaluate in TinyStudio |
| --- | --- | --- |
| [Apple: Layout](https://developer.apple.com/design/human-interface-guidelines/layout) | Hierarchy, alignment, related groups, progressive disclosure, and adaptation to available space. | Establish the primary task and related context before choosing containers. Preserve access to functions as the viewport changes. |
| [Apple: Split views](https://developer.apple.com/design/human-interface-guidelines/split-views) | Adjacent panes represent navigation and selected content; useful auxiliary panes can be hidden and restored. | Evaluate a selected-object canvas with optional context. Keep the selected identity evident in both navigator and detail. |
| [Apple: Tab views](https://developer.apple.com/design/human-interface-guidelines/tab-views) | Tabs exchange closely related, self-contained content in the same area. | Tabs are one tool for related views, not a reason to turn every module into another tab. |
| [Apple: Toolbars](https://developer.apple.com/design/human-interface-guidelines/toolbars) | Commands are grouped by their relationship to the current content and frequency of use. | Keep current-task actions close to the content they affect; distinguish them from navigation. Native SwiftUI/AppKit APIs and system-managed overflow are not web implementations. |
| [Affect UI: Settings & Billing](https://www.affectui.com/screens/settings-billing) | Related settings have a local selector. Billing combines a broad usage summary with adjacent plan/payment information. | A configuration task needs grouped settings and appropriate local navigation; it does not need a permanent experiment canvas. |
| [Affect UI: Analytics](https://www.affectui.com/screens/analytics) | A compact action row, summary metrics, a prominent chart, and adjacent breakdown; secondary comparisons use smaller regions. | Prioritize the evidence being analyzed. Give related comparison/detail a deliberate region instead of assigning every panel equal weight. |
| [Affect UI: App Shell Workspace](https://www.affectui.com/screens/app-shell-workspace) | A central work area sits between navigation and a supplemental work panel. The documented narrow layout turns side areas into drawers. | Separate persistent navigation, active work, and supplemental tools. Panel visibility must preserve working context. |
| [Affect UI: Evals](https://www.affectui.com/screens/evals) | A selected prompt/version, metrics, chart and review detail share one analysis context; test-case filtering changes the relevant collection. | Evaluation detail should follow the selected run/case. Distinguish collection filtering from changing the task. |

Apple's published Markdown articles were read directly. Affect UI observations
come from rendered public previews and their visible documentation, not merely
search snippets or screenshots in search results. The Evals Fail filter was
operated in the demonstration. The previews do not establish licensed source
installation, accessibility completeness, or TinyStudio behavior. The Evals
documentation says its right column moves below the table under 1000 px; this
study has not independently verified that responsive transition.

### Current application diagnosis

`features/lab/index.tsx` mounts the portable bundle controls, experiment editor,
capability catalogue, definition/run lists and selected run evidence together.
`RunEvidence` then mounts the event table, replay, artifact selector and record
table together. This has no clear distinction between the active task and its
supporting tools. Making buttons smaller or adding another row of tabs cannot
establish that distinction by itself.

The task relationships to resolve before another layout edit are:

| User task | Primary content | Context needed at the same time |
| --- | --- | --- |
| Author a system | The system definition, expressed as the appropriate domain canvas or code | Selected component/connection properties and the current saved revision |
| Configure execution | The selected experiment's target, method, budget and compatibility | Prerequisite failures and the exact revision to submit |
| Inspect evidence | The selected episode's interactive signals or 3D evidence | Selected observation, units, source identities, relevant events and available history |
| Compare outcomes | Aligned cases, trajectories or metrics | Shared filters, comparison identities and evaluation qualifications |
| Manage supporting resources | The selected input, capability or transfer operation | Its provenance, compatibility or durable operation state |

This is a workflow inventory, not a decision about page names or tab counts.
The 3D reference is separately
[Reinforcement-learning-visualization](https://github.com/yannisEF/Reinforcement-learning-visualization).
Its `savedVignette.py` renders distinct direction ribbons, reward height,
comparison-policy markers and adjustable transparency. Those plotting semantics
do not determine the surrounding application navigation.

### Reuse and state boundaries

The repository already contains `SetupTemplate`, `RunTemplate`, `AnalysisTemplate`,
`SettingsTemplate`, `InspectionWorkspace`, `WorkspaceDisclosure`, `RecordsInspector`
and `RecordsScroll`. BoardUI supplies the existing controls, and the evidence
registry connects selected component state to the omnipresent assistant. Reuse
these boundaries before creating another layout or component family. Existing
templates provide slots; their presence alone does not prove suitable resizing,
focus behavior or responsive pane transitions.

Independent source review identified state constraints that a layout must honor:
input selection/upload currently lives inside `InputArtifacts`; accepted
operations outlive their browser observation; import completion can replace an
intervening draft; and replay selection, camera and playback live below the run
controller. Moving or conditionally removing a panel can therefore change
behavior even when the parent draft remains intact. These are verification
requirements, not evidence that the proposed navigation already preserves state.

### Verification before layout acceptance

- Identify the active task, selection and action scope in each candidate
  composition, then review the actual application with representative retained
  evidence. A standalone chart harness cannot qualify the application workflow.
- Make the primary working content reachable without scrolling through
  unrelated administration. Keep useful adjacent detail tied to the selection.
- Preserve draft changes, file selections, active uploads, accepted operation
  identities and graph context while navigating. Separate hidden playback from
  intentionally running background execution.
- Verify keyboard navigation, visible selection, focus restoration, readable
  text at enlarged sizes, and coherent pane transitions at wide, intermediate
  and narrow available widths. Preserve equivalent functions across layouts.
- Validate scientific graph semantics independently: explicit axes/units,
  clock compatibility, missingness, estimates, exact source picking and camera
  ownership. Three-dimensional appearance does not establish those properties.

The current Lab was opened as a built React application against retained data
through the existing Cloudflare path, using a temporary loopback preview with
server-side credentials and API mutations denied. This establishes application
access for inspection, not password-login coverage, a deployed C release, a
selected-run 3D acceptance pass, or approval of a replacement layout.
