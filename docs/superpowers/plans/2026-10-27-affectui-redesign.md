# TinyStudio AffectUI Redesign Implementation Plan

> **For agentic workers:** Execute through the user-selected Agent Team workflow with bounded ownership, parent integration and independent verifier checkpoints. The user explicitly approved all implementation work on 2026-10-27.

**Goal:** Replace every TinyStudio interface with a coherent, reusable AffectUI-based component/block/template system while retaining all product operations.

**Architecture:** Public AffectUI controls feed controlled TinyStudio adapters. Seven compositional templates own presentation; existing controllers retain requests, identity, uploads, conversations and evidence. One destination registry drives navigation and assistant navigation actions.

**Tech Stack:** React 19, TypeScript, Vite, existing React Aria and ECharts; no new application framework.

**Spec:** Approved source direction and concepts are in `/Users/emmanuelakinwale/.codex/visualizations/2026/10/28/01a17fce-94c7-7171-baea-3f8934c29b31/affectui-source-adoption.md`; complete scope in sibling `tinystudio-redesign-plan.md`.

## Constraints and ownership

- Existing tracked modifications and untracked frontend are foreign baseline. Captured 140 source files outside checkout before edits; never reset/stage the whole tree. Implementation approval does not imply publication.
- Keep routes, permissions, scope leases, abort/stale handling, upload phases, streaming/sync/conflicts, evidence registration, and modal focus semantics.
- Reuse public MIT sources with notices; do not claim or retrieve Pro code without access.
- Quiet gray rail, inset white work surface, 44px desktop table rows, compact controls; dark/system appearance and touch adaptation. Real units/scales and visible missingness in visualizations.
- Parent: app shell/navigation, global styling, catalog, Laboratory/interpretability integration and final review.
- Foundation worker: `components/affectui/**`, `components/studio/{primitives,templates}.tsx`, template CSS/tests.
- Data worker: `features/data/**` only.
- Workspace worker: `features/{workspace,assistant}/**` only.

## Review focus

- Switching projects while requests/streams run must not show prior-project data.
- Closing navigation/inspectors must restore useful focus; browser Back preserves location state.
- Large tables and long labels must remain usable at narrow widths and zoom.
- Uploaded bytes must not be mistaken for imported/indexed ready data.
- Selection, filters and chart evidence must agree, including missing and partial results.

## Task 1 — Reusable controls and templates

- [x] Bring public Clean button/filter/segmented source into owned vendor folder, retain MIT and source hashes.
- [x] Adapt existing Button API and implement seven slot templates: CollectionTemplate, DetailTemplate, AnalysisTemplate, RunTemplate, ConversationTemplate, SettingsTemplate, SetupTemplate. Props: children, toolbar?, summary?, inspector? ReactNode; className?, label? string.
- [x] Preserve disabled/loading/default type and controlled state. Test callbacks, focus/labels and responsive DOM presence.
- [x] Build interactive illustrative catalog examples with network isolation intact.

## Task 2 — Shell and navigation

- [x] Centralize destinations and role visibility; reuse for sidebar, search and assistant actions.
- [x] Use the sidebar as desktop navigation and phone section sheets at narrow widths; remove duplicate Pages, app history controls and automatically opened page tabs. Preserve scoped scroll position.
- [x] Verify mobile navigation, theme, project changes, tasks and assistant access.

## Task 3 — Complete route migration

- [x] Data: datasets/detail, repositories, training/job, evaluations/detail, metrics.
- [x] Workspace: models, playground, traces, projects, keys, stats, settings, users, assistant page/dock.
- [x] Analytical: Laboratory definition/run/replay/trends and interpretability captures/comparisons.
- [ ] Non-route: auth/setup, recovery, loading/not found, confirmations and task overlays.
- [x] Use actual template slots and retained action/evidence blocks; no fake live data or hidden actions.

## Task 4 — Integrated validation

- [x] Pre-change baseline: 44 frontend files / 330 tests passed (15.07s).
- [x] Run focused changed suites, then integrated typecheck/test/build.
- [ ] Browser-check populated catalog and authenticated routes where accessible; desktop/narrow/light/dark, focus, state transitions and charts. Distinguish fixtures from live service evidence.
- [x] Independent Agent Team verifier reviews owned diff against captured baseline and actual evidence. Resolve material findings before claiming completion.
- [ ] Record remaining runtime/publication limitations; commit/push only with authority and clean ownership boundaries.

## Task 5 — Modular workspaces and contextual assistant

- [x] Remove duplicate global page menus, history arrows and automatic page tabs.
- [x] Split Laboratory authoring, capabilities, transfers and run inspection into exclusive panes with retained draft state.
- [x] Replace the rejected persistent inspector with task-local experiment selection, run selection and transfer history.
- [x] Move the assistant to a persistent bottom-right launcher with an on-demand conversation and compact composer.
- [x] Connect reusable table controls to the existing component action registry and revoke inactive-pane operations.
- [x] Verify desktop/phone geometry, draft retention, keyboard dismissal, tests, build and source integration.
- [x] Obtain independent review of the settled correction increment.
- [ ] Coordinate migration ownership and deploy the reviewed correction to the authenticated hosted frontend.
- [ ] Verify final hosted assets and actual navigation, Laboratory panes and assistant behavior after rollout.

## Task 6 — Desktop composition correction

- [x] Inspect AffectUI General, Members and API keys tab layouts, then inspect every Laboratory authoring/work-area tab.
- [x] Reuse grouped setting rows, compact uploads and task-local collections in the real Laboratory feature. Preserve callbacks, draft lifetimes and scientific/data consumers.
- [x] Review rendered desktop Parameters, Inputs, Execution, topology, JSON, run replay/records/events, Capabilities and Import / export. Exercise draft retention and inspect empty/error states. Coverage and representation lack local fixtures; populated acceptance remains with the concurrent backend runtime review.
- [x] Run focused checks, production build and independent source review. Repair and regress the review's pending-save engine-switch finding; corrective review passed. This does not establish hosted or user visual acceptance.
- [ ] Coordinate release with the backend owner and verify the hosted result; source validation is not visual acceptance.

## Task 7 — Cumulative full-route acceptance

For each item: inspect the named Affect UI reference, apply a shared template with justified adaptations, review every pane in populated/empty/loading/failure states, exercise the authenticated journey, and verify desktop, keyboard, responsive and hosted behavior. References below are review targets, not claims of completed inspection. Keep decisions in DESIGN.md and findings in CHANGELOG.md; earlier checked implementation tasks do not mark these acceptance tasks complete.

- [ ] Shell: sidebar, search, project switcher, tasks, profile and adaptive navigation. Affect UI App Shell Workspace; Apple familiarity/context.
- [ ] Datasets collection: search, filters, density, columns, selection, comparison, inspection and upload/synthetic creation. Affect UI CRM/Data Table/Filter Menu/Segmented Control.
- [ ] Dataset detail: records, snapshots, datapoint inspection/editing, comparison, quality/curation and job setup. Affect UI CRM/Evals; proximity and working memory. 2026-10-28: Records/Snapshots peer panes, one bucket filter, separate snapshot creation and shared selection/table widths implemented; desktop light/dark and narrow fixture review, 594-test suite passed. Add/enrich/labels/split/snapshot fields and Quality Overview/Distributions/Cleaning now use reusable grouped controls and task-specific panes; payload/lifecycle tests and desktop/narrow fixture inspection added. Shared active-tab clarity is applied to the real components. Job setup, full failure-state visuals, remaining chart accessibility, authenticated/hosted/user acceptance remain open.
- [ ] Repositories: collection, comparison, selected repository, documents, search and create/import/management dialogs. Affect UI CRM/Folder/Dropzone; contextual grouping. 2026-10-28 local increment: full-width document detail, retained collection state, scoped dialog responses, compact create/import/query. Desktop fixture review and 589-test frontend suite passed; authenticated mutations, hosted and responsive/dark visual acceptance remain open.
- [ ] Fine tuning collection and job detail: setup, selection, status/actions, charts, logs and evidence. Affect UI Agent Run/Evals; flow and honest feedback.
- [ ] Evaluations collection and detail: creation, model comparison, cases and score distributions. Affect UI Evals; selected-object hierarchy.
- [ ] Metrics: definitions/editor, collection, deterministic/evaluator checks. Affect UI CRM/Settings; progressive disclosure.
- [ ] Laboratory: experiment collection/context, all authoring panes including preparation and language learning, execution review, every run evidence pane, capabilities/transfers/operations. Affect UI Evals/Agent Run/Settings; DESIGN.md context contracts and coordinated shared-file ownership.
- [ ] Interpretability overview/capture/comparison: selection, plots, filters, neuron evidence and comparison controls. Affect UI Analytics/Evals and supplied RL visualization; linked identity, units and qualifications.
- [ ] Models: collection, discovery/download, serving, Hub controls/activity. Affect UI App Directory/Integrations; meaningful task grouping.
- [ ] Playground: configuration, conversation, streaming, history and recovery. Affect UI Agent Chat; context and expert access.
- [ ] Traces: inference collection, selected messages/response, review/correction and promotion. Affect UI Agent Ledger/Evals; scoped actions.
- [ ] Projects: directory, selected details and creation. Affect UI Folders/Project Card; consistent collection behavior.
- [ ] API keys: collection, creation, one-time secret and revocation. Affect UI Settings & Billing API keys; explicit action scope.
- [ ] Stats: totals, activity, model breakdown and selected day. Affect UI Analytics; truthful comparison.
- [ ] Settings: every group, infrastructure, save/loading/failure. Affect UI Settings & Billing; grouped decisions.
- [ ] Users: directory and administration dialogs. Affect UI Settings & Billing Members; roles and action scope.
- [ ] Assistant: launcher across all routes, conversation/model/attachment controls, evidence picking, component actions and standalone route compatibility. Affect UI Agent Chat/agent components; agency and context.
- [ ] Access/setup/not-found/error/loading surfaces: clear state and recovery. Affect UI Onboarding; Apple agency.
