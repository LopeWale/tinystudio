# TinyStudio interface system

## Cumulative redesign contract — 2026-10-28

The objective is the complete TinyStudio frontend redesign. Feedback adds acceptance requirements to this objective; a Laboratory correction is one increment, not a replacement objective. Earlier rejected compositions remain recorded as history, not accepted designs.

| Requirement retained | What the user must be able to see and do |
| --- | --- |
| Affect UI before composition | Name the inspected component/block/screen and explain its hierarchy, proportions and interactions before adapting it. A matching component name or imported button does not establish fidelity. Distinguish public source adoption from visual reference to licensed screens. |
| Reusable foundations first | Shared controls, blocks and page templates serve every route family. Simple, dynamic and advanced tables may differ in capability while sharing behavior and presentation. |
| Desktop-first craft | Use available width for the active task and related comparison/detail, with readable compact controls. Adapt navigation and composition to smaller screens without imposing phone proportions on desktop. |
| Coherent navigation and flow | One primary page navigator; local navigation changes meaningful task context. Preserve drafts, selection and filters. Place each action with the object it affects, and expose the next useful action. |
| Modular work surfaces | Tabs, drawers, dialogs and disclosures have specific purposes. Do not move an unchanged long page behind a tab. Inspect every pane, including populated, empty, loading and failure states. |
| Interactive records and evidence | Reusable filtering, sorting, density, columns, selection and paging where appropriate. Linked charts and tables preserve source/run identity, units and qualifications; no invented measurements. |
| Omnipresent assistant | Bottom-right launcher on every page; on-demand conversation can inspect and operate registered component state, while preserving the user's context. |
| Real application delivery | Changes belong to actual frontend routes. Development fixtures support inspection but do not prove authenticated workflows, hosted publication or user visual acceptance. |

Before implementation, connect **user task → inspected Affect UI pattern → Apple/Laws of UX rationale → shared template → observable acceptance**. Before presenting an increment, check it against every row above; explicitly report remaining gaps. A new complaint must not silently remove an earlier requirement.

### Laws of UX research added to the existing references

Collection increment, 2026-10-28: inspected the actual [Affect UI CRM screen](https://www.affectui.com/screens/crm/full) and [Data Table](https://www.affectui.com/components/data-table). Adapt the compact local command row and dominant collection surface, with search adjacent to view controls and selection actions shown when records are selected. These are visual references; the licensed CRM/Data Table implementation was not copied. Existing public Affect UI buttons, segmented control and Filter Menu remain the reusable primitives. TinyStudio keeps its own selection/comparison semantics and one primary navigator. Filters open in a bounded popover so adjustments retain the visible collection; Escape restores the trigger. Datasets and Repositories share this controlled toolbar, and their records region fills the available desktop workspace. This is an implementation increment, not full-page or hosted acceptance.

Read in Browser on 2026-10-28: all 30 principle detail pages listed at [Laws of UX](https://lawsofux.com/) and all eight articles listed in its [articles index](https://lawsofux.com/articles/). The supplied singular `lawofux.com` failed DNS resolution. This coverage excludes the paid book, store products and external linked studies. Re-read Apple's [Design principles](https://developer.apple.com/design/human-interface-guidelines/design-principles). These are design guides, not proof that a proposed TinyStudio screen works.

| Decision | Source rationale | TinyStudio application and check |
| --- | --- | --- |
| Group around the task and selected object | [Proximity](https://lawsofux.com/law-of-proximity/), [Common Region](https://lawsofux.com/law-of-common-region/), Apple hierarchy | Adapt the previously inspected Affect UI Evals relationship between identity, controls and evidence. A user should identify what is selected and what each action affects without reading distant cards. |
| Reuse predictable visual and interaction rules | [Jakob's Law](https://lawsofux.com/jakobs-law/), [Similarity](https://lawsofux.com/law-of-similarity/), Apple familiarity | Affect UI's table/filter/segmented-control patterns become shared variants, not separately styled copies. Exercise the same interaction across multiple consuming routes. |
| Reveal secondary complexity in context | [Tesler's Law article](https://lawsofux.com/articles/2024/teslers-law/), [Hick's Law](https://lawsofux.com/hicks-law/), Apple agency | Advanced settings remain accessible; optional explanation can collapse; a short import action can use a dialog. Do not force a wizard or hide essential work merely to shorten a page. |
| Preserve continuity across work areas | [Working Memory](https://lawsofux.com/working-memory/), [Flow](https://lawsofux.com/flow/), Apple context preservation | Keep the selected dataset/experiment/run and its relevant state through navigation, inspection and assistant use. Test returning to the same work, including failed saves. |
| Make the active task visually dominant | [Cognitive-load article](https://lawsofux.com/articles/2015/design-principles-for-reducing-cognitive-load/), [Von Restorff Effect](https://lawsofux.com/von-restorff-effect/), Apple craft | Use the inspected Affect UI Analytics/App Shell relationships for main work and subordinate detail. Spacing, typography and surfaces distinguish roles; a right panel appears because it helps the task, not just to fill width. |
| Validate beauty and usability separately | [Aesthetic-Usability Effect](https://lawsofux.com/aesthetic-usability-effect/), Apple real-world iteration | Inspect visual composition and complete the actual workflow. A screenshot, passing build or reusable component alone cannot establish acceptance. |

These interpretations do not prescribe a maximum of seven navigation items, ban scrolling, require every operation to be modal, or justify removing requested capabilities. Progress reflects real state; scientific inputs retain explicit validation. Reference examples are adapted to TinyStudio's tasks rather than copied indiscriminately.

User-requested direction: AffectUI components, blocks and screens, specifically the quiet rail, inset workspace, compact tabs/controls and fine table treatment observed in its full-screen CRM and Evals references. Use public source where available, preserve provenance, and implement original domain compositions where references are Pro-only.

Operate mode. Neutral gray/white/graphite surfaces, charcoal actions, restrained blue focus/selection, consistent semantic statuses. Desktop table rows target 44px with 14px/20px type; touch layouts use larger targets and readable list/detail composition. Light/Dark/System remain supported.

Build foundations → reusable blocks → seven templates → all route families. Existing controllers own identity, requests, permissions, upload phases, streaming and evidence. Templates are controlled presentation with slots, not a second application state layer.

Analytical views keep axes/units/keys, missing/partial state and selected evidence visible. The RL reference informs linked sampling/checkpoint inspection; it does not authorize invented measurements. Charts and labels remain editable data-bound UI.

Source concepts and full coverage: `/Users/emmanuelakinwale/.codex/visualizations/2026/10/28/01a17fce-94c7-7171-baea-3f8934c29b31/affectui-source-adoption.md`. User approval covers all implementation; routine adaptations do not require another approval.

## Reusable record views

`SimpleTable`, `DataTable` and `AdvancedTable` share the same semantic table renderer. Simple tables preserve supplied order and every record. Data tables add typed filters, sorting, column visibility and paging. Advanced tables expose density directly through AffectUI's actual [compact tray segmented control](https://www.affectui.com/components/segmented-control). Both interactive variants use its public [Filter Menu](https://www.affectui.com/components/filter-menu) and [Button](https://www.affectui.com/components/button) sources. The [Data Table](https://www.affectui.com/components/data-table) and [CRM screen](https://www.affectui.com/screens/crm/full) are visual references; Pro source is not included.

`RecordTableController` owns local view state and guards. Features provide typed values, stable row identity, exact action callbacks and evidence wrappers. External pagination explicitly limits filtering/sorting to the loaded server window. Inspection and selection controls cannot be hidden. Scientific tables may preserve domain-specific paging and chronological order.

At phone widths, four workspace sections open short single-column sheets; search is separate. Table columns retain readable widths and scroll within their container. Density changes use one segmented tray with labeled accessible options and 44px narrow-view targets, without page-level button sizing overriding its geometry.

## Real application versus development fixtures

Normal entry: `/` or `/ui/#/datasets`, connected to the API. The development-only `#/__preview` route mounts the same Shell and feature components with a closed local read-fixture API and refused writes. It is a visual/interaction harness, not a separate frontend, and is excluded from production. Preview checks do not establish authenticated workflow or deployment acceptance.

## Focused work areas and contextual controls

The Laboratory uses mutually exclusive work areas (Experiment, Runs, Capabilities, Import / export). Authoring has Parameters, Inputs, Execution, Visual topology and Definition JSON views; changing views retains the draft and pending input selection. The topology canvas is created on demand. Run inspection separates Replay, Records, Events and Dataset into retained panes. The user rejected the persistent inspector and generic form grid. Desktop authoring now uses shared `SettingsSection` rows with group descriptions on the left and controls on the right. Saved experiment selection belongs to the editor heading; run selection belongs to Runs. Inputs uses source selection beside the retained input library. Import / export places a compact importer beside operation history; capabilities gets the full content width. Optional engine prerequisites use disclosure.

This applies Apple's [layout guidance](https://developer.apple.com/design/human-interface-guidelines/layout) on grouping and progressive disclosure, [tab views](https://developer.apple.com/design/human-interface-guidelines/tab-views) on related mutually exclusive panes, and [design principles](https://developer.apple.com/design/human-interface-guidelines/design-principles) on simplicity, consistency and preserving context. These principles do not prohibit scrolling; they require hierarchy and appropriate presentation. AffectUI's [Settings & Billing](https://www.affectui.com/screens/settings-billing/full) General tab provides the explicit horizontal label/help-to-control composition; its Members and API keys tabs demonstrate wide collections with local actions. These are inspected public visual references, not copied Pro screen source. The first tabbed Laboratory composition did not faithfully apply those layouts and was rejected by the user; source control reuse did not establish visual acceptance. The revised composition still requires user visual acceptance and coordinated hosted validation.

The sidebar is the desktop page navigator. Phone section sheets serve the same role at small widths. Search is optional keyboard access; automatically opened page tabs, duplicate Pages menus and in-app history arrows are removed. Within-page tabs separate actual work areas.

The persistent bottom-right assistant launcher opens a nonmodal floating conversation, following the inspected [AffectUI Agent Chat](https://www.affectui.com/screens/agent-chat/full) composition. It reserves no page width. Conversation state remains mounted across close, navigation and evidence picking. Secondary model, upload and turn settings appear on demand. Shared interactive tables expose validated controller operations through the existing component registry; hidden retained panes revoke those operations without resetting table view state.

## Workflow correction — 2026-10-28

The user rejected the horizontal-settings revision as disconnected and visually jumbled. The failure is deeper than spacing: project utilities, an experiment definition, and a selected run are presented as peer categories. A settings-screen composition does not supply the Laboratory's task hierarchy. This correction supersedes the previous composition as the intended direction; it is a specification, not implemented or accepted UI.

The freshly inspected AffectUI [Evals screen](https://www.affectui.com/screens/evals/full) keeps one prompt/version in the header and makes its action, metrics, chart and cases refer to that object. Apply that relationship, not its example metrics or a literal copy of its screen.

- **Enter through the experiment collection.** Existing experiments are the primary content; creating one is the primary action. Import and project-wide capability/operation tools remain secondary, reachable utilities. Do not open a synthetic unsaved experiment by default and simultaneously offer another New experiment action.
- **Open one experiment context.** Its name, saved revision and draft state stay in a compact identity header. Returning to the collection and switching experiments preserve or explicitly resolve edits. Switching scope never silently discards a draft.
- **Configure a focused task.** Setup, input binding and execution settings share the experiment identity but each has a named work surface. Fields are grouped by the decision they support. Topology is primary for engines that actually execute a topology; generic retained topology and JSON are advanced authoring views, not equal navigation destinations for every adapter.
- **Review the exact execution intent.** Place target, schedule, compatibility result and the revision to submit together. Save changes while dirty; review/run the saved revision when eligible. Preserve the existing backend admission rules. Never label an unchecked target compatible, and never describe a draft as the revision that will execute.
- **Follow the run into its results.** Successful submission selects that run in the same experiment context. A run selector, status and evidence view form one local hierarchy. Charts/records/events and specialized inspectors share the selected run; its actual identity and retained qualifications remain visible. Scope runs by definition ID; do not infer a revision match from the currently open definition.
- **Give the right side a specific job.** Show properties for the selected component, an execution review, or source details for the selected observation. Otherwise the main task uses the space. No persistent unrelated context cards.

Visual distinction follows these responsibilities: restrained shell, clear experiment identity, one dominant active work surface, and a subordinate contextual surface only when needed. Section titles, spacing, separators and surface contrast reinforce those boundaries. Primary actions stay with the task they advance; utility controls do not occupy the same visual rank.

Acceptance is a user journey, not a count of tabs: open/create an experiment; identify its state and next action; edit and retain a draft while changing views; resolve save/check failures without losing work; review and submit the exact saved revision; arrive at that run's evidence; inspect a record and return without losing selection. Check multiple engines, absent inputs, existing runs, and empty/error states. Populated live qualification and user visual acceptance remain separate from source tests.

### Context and execution contracts

Independent planning review identified lifecycle constraints that must be resolved before implementation:

- One editor context owns the active definition ID, saved revision, draft, target, schedule, compatibility observation and selected run. Returning to the collection preserves that context. Opening another experiment with edits requires Save, Discard or Cancel; saving must succeed before switching. This does not imply multi-draft persistence that the application does not currently provide.
- An explicit switch resets selected-run identity and clears its contextual evidence. Carry the target only as a visible workspace preference if it remains available; clear the schedule and compatibility observation. Historical runs are associated using workspace and definition ID, never display name. A historical revision is shown only when retained authoritative evidence provides it; otherwise it is unknown.
- Action availability retains busy, parse validity, preparation validity, target, saved identity and conflict constraints. Opening execution review captures the definition ID/revision, target and schedule. Changes invalidate that review and require refresh before submission. A successful compatibility result is valid only for the checked definition/target; it is not a general readiness certificate.
- The first implementation increment is collection-to-experiment context, persistent identity, safe draft transitions and experiment-scoped runs. Preserve existing authoring surfaces and API handlers until this boundary is proven. Defer broader drawers/navigation reorganization to the next increment, rather than changing every interaction simultaneously.
- Falsification cases: two experiments with similar names; a dirty draft when switching; a failed save; a run from another definition; an old run with no revision metadata; a target disappearing; a schedule edited after review. Lost drafts, wrong-context evidence or an ambiguous submission identity fail the design.

The planning review requested these corrections; it did not approve visual quality or establish implementation readiness. Source handoff with the concurrent backend/release chat remains required before editing shared implementation files.

### Repository detail flow — 2026-10-28

Inspected [Affect UI Folder](https://www.affectui.com/screens/folder/full) and [Dropzone](https://www.affectui.com/components/dropzone) visually before this increment. Folder supplies the selected-container identity, Back action, local commands and dominant full-width contents. Dropzone supplies a bounded file target and concise format/status treatment. These are visual references, not copied licensed source; the implementation composes the existing DetailTemplate, AdvancedTable and installed BoardUI fields/upload.

A selected repository now owns the main workspace, rather than squeezing a document table into a 300px inspector. Back restores the collection search, density, pagination, selection and keyboard focus. The retained collection is hidden from accessibility and its table actions are unregistered while detail is active. Document search belongs to the repository, and table state is scoped to repository identity. Repository deletion sits behind management disclosure. Add documents presents files and URLs alongside one another on desktop; Query gives the question most width and keeps numerical limits adjacent. Closing/reopening a dialog invalidates old asynchronous results.

UX rationale: proximity/common region group commands with their content; progressive disclosure keeps secondary management out of the primary task; recognition and context preservation let users return without reconstructing their collection. This is an application of the previously reviewed Laws of UX and Apple modality guidance, not a claim that they prescribe this exact layout.

Evidence: actual shared route components inspected at 1280×720 with local fixtures, including detail, empty document search, create/import/query dialogs, keyboard management disclosure and Back focus/search restoration. Full frontend suite: 589 tests passed with two workers; production build passed. Authenticated mutations, hosted release, dark/narrow visual acceptance and full-route user acceptance remain open. A viewport override did not change the inspected tab dimensions; it was reset, and no narrower visual proof is claimed.
