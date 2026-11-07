## 2026-10-30 — Laboratory workflow revalidation

Rendered and read [Experiment Card](https://www.affectui.com/components/experiment-card), including use/non-use guidance, API, keyboard, accessibility, motion and responsive sections. Inspected its clean dark preview. Its compact identity/status header and subordinate comparison are useful hierarchy references, but its winner/metric/relative-change semantics require measured variants. Saved TinyStudio experiment definitions do not supply these measurements. Do not use the component merely because its name includes “experiment.” This is a licensed visual reference, not adopted Pro source; interaction coverage in this pass is visual inspection only.

Read the entire rendered [Apple Layout guidance](https://developer.apple.com/design/human-interface-guidelines/layout), including hierarchy, adaptability, safe areas and platform considerations. Apply importance ordering, alignment, related-item grouping, progressive disclosure and recognizable resize behavior to the web workspace. Native platform-specific dimensions and Liquid Glass implementation are not web requirements. This reading does not establish UI compliance.

Actual localhost Laboratory remains inconsistent with the recorded experiment-first context contract. The added experiment-context regression reproduces edited-draft loss on switching saved definitions. Resolve lifecycle ownership before moving the same unsafe editor behind a new collection surface.

## Repository retrieval presentation — 2026-10-30

Rendered and exercised [Cited Answer](https://www.affectui.com/components/cited-answer), Clean dark preview: source chip opens an inset quote region; quiet domain/date metadata sits above a distinct title, exact quote, and subordinate actions. Documentation specifies finished answers with claim-to-source links and a handful of sources. TinyStudio repository queries return raw ranked passages without a generated answer, so this component is **not adopted wholesale**. Adapt only source/passage hierarchy within the already reviewed shared DataTable: document first, compact numeric score, readable wrapping passage, bounded scrolling and attached paging. No generated claims, source URLs or confidence percentages are invented; no Pro source copied.

Source/build tests pass. Actual authenticated local query against an empty repository returns the new separated empty heading/help. Subsequent actual-local browser checks used18 explicitly generated passages with real offline embeddings: desktop, shorter desktop, paging, keyboard vertical/horizontal scrolling and narrow layout were inspected. Narrow control stacking and compressed passage columns were corrected. This seed does not prove the separate upload/worker-indexing lifecycle; light-mode populated review remains open. Full Repository acceptance remains open.

## Folders rendered review and repository empty states — 2026-10-30

Read Folders at https://www.affectui.com/screens/files and inspected its full dark desktop rendering: section-level search/sort/view/actions, compact folder rows/cards with separated identity/metadata, and a separate template area. Searching for a missing folder shows a centered title and subordinate query explanation. Adapted that separation through optional heading/actions in TinyStudio's shared EmptyState. Repositories retain their real table/document contracts; no template/sharing features or Pro source copied. Actual local document-empty, import-opening and search-recovery states checked; full populated and mutation journeys remain unverified. The guessed /screens/folders URL returned404; the documented /screens/files URL is authoritative.

## Quality content-sizing follow-up — 2026-10-30

Revisited rendered KPI Board and its documented limitations. The grouping reference does not justify trend charts for a static quality profile. Removed the forced760px dialog height; content now sizes naturally up to its viewport cap, with stationary navigation and independently scrolling long content. Desktop Overview557px/Cleaning646px and short-desktop/narrow scroll reachability verified in the actual local application. This supersedes the fixed-height Overview gap noted below; complete state/assistant and visual acceptance remain open.

## Quality validation follow-up — 2026-10-30

Light desktop and narrow Cleaning inspection now supplements the earlier dark desktop check. Invalid numeric ranges receive inline correction feedback and disable Apply; actual local correction restores the action without submitting a mutation. This closes the recorded threshold-validation defect, not the full Quality acceptance gate. Overview's unused lower dialog area and complete state/assistant checks remain open. See CHANGELOG.md for exact verification boundaries.

## Quality Cleaning reference application — 2026-10-30

Reopened the rendered Affect UI Settings & Billing full screen and selected General. Its explanation/control columns, compact fields and shared action footer were inspected visually. Adapted that hierarchy using TinyStudio's existing SettingsSection: rule choices grouped on the right, numeric limits in one desktop row, free-text labels separate. No Pro source copied. Actual local desktop state/selection checks and remaining acceptance gaps are recorded in CHANGELOG.md. This corrects the tall thresholds described in the earlier baseline; it does not establish full Quality acceptance.

## Quality distribution composition follow-up — 2026-10-30

Subsequent domain correction: verified core.quality._histogram pads to its requested bin count. Quality now opts into plotting only the first through last occupied bins while retaining internal empty intervals and every original bin in exact counts/evidence. Actual desktop lengths40/41 and ratings5–10 now fill their plot; ratings11–14 remain explicitly zero in the exact table. This supersedes the trailing-empty-domain defect noted below. It does not infer a universally enforced rating scale or alter backend data. Other Quality and full-route acceptance gates remain open.

The local implementation now replaces the stacked Distributions content described below with a selected measure/chart-or-table region beside a selected composition table. This adapts the previously inspected Breakdown Chart control/evidence grouping and KPI Board primary/supporting proportions; it does not copy Pro source or imply unsupported record-level slicing. Labels explicitly disclose overlapping counts. Buckets remain mutually exclusive counts.

Actual 1440×900 dark review confirms both panels fit in the visible pane; exact counts use a bounded scroll region. At760px the layout stacks within the dialog. Choice retention, count-table access and non-mutation have focused test coverage. The chart still wastes domain on trailing empty bins; rating bounds and other Quality panes remain unresolved. This supersedes the earlier statement that composition work is wholly unimplemented, but not the outstanding visual/interaction/release acceptance gates. See CHANGELOG.md for scoped validation and the pending independent-review transfer approval.

## Actual quality-pane geometry and keyboard audit — 2026-10-30

Authenticated local server58543, Support examples, 1440×900 dark. This is an unresolved-defect baseline, not acceptance:

- Distributions has562px usable scroll height and1027px content. The two length panels are368px high; categorical panels start atviewport y604 and ratings at y828, below the pane bottom748. Opening the first exact-bin disclosure increases content to1259px. Keeping details accessible must not require extending the whole comparison surface this way.
- Rendered charts expose34 sequential focusable bins:12 per length chart plus10 ratings bins.24 bins have zero count (20 length tails and4 rating tails). Tab from40 focuses41 and visibly reports74observations/89.2%, so the exact-value interaction works, but keyboard traversal includes excessive empty tails. Raw evidence should remain available independently of a concise visualization.
- Overview has a four-stat row and two aligned panels but leaves a large unused lower area inside its fixed-height dialog. Cleaning has two side-by-side panels, with all four thresholds stacked in the right panel; numeric fields span the same width as the free-text label field. Apply cleaning is enabled with no rules, though source validation rejects that state before confirmation or POST. This is an affordance issue, not evidence of an unguarded mutation; no cleaning was submitted.
- ArrowRight on Overview selected Distributions with visible focus. Escape closed the dialog and returned focus to Quality. No mutation, draft input, profile refresh, backend change or release occurred. Baseline screenshot: /private/tmp/tinystudio-quality-distributions-before.png.

## Catalog and chart interaction follow-up — 2026-10-30

- Refreshed live sidebar now lists **71 components**, 15 screens, 11 blocks, 24 illustrations and 8 motion items: **129 references**. Compared the actual component links with this register; the missing item was Scheduled Runs. Prior 70/128 figures below describe the earlier snapshot.
- [Breakdown Chart](https://www.affectui.com/components/breakdown-chart), Clean dark: inspected chart/table/legend hierarchy. Keyboard Tab from Backlog focused Todo, displayed its total36 and segment values12/11/7/5/1, and highlighted its table row. Toggling Urgent off changed total212 to200 and Backlog26 to23; the Urgent column remained visibly dimmed with its original values. Show all restored212. This distinction between visible-series totals and original evidence must be explicit in an adaptation; overlapping Dataset labels must not be treated as a partition. Narrow/reduced-motion behavior remains unverified. No protected source copied.
- [Scheduled Runs](https://www.affectui.com/components/scheduled-runs): read all public use/non-use, API, keyboard, accessibility, motion and responsive guidance; inspected Clean dark. Compact backlog sits beside a day/session grid, with labeled capacity meters and contextual card details. Manual returned automatically placed tasks to the backlog while booked items stayed. Clicking an unscheduled task exposed its detail below the card; Escape dismissed it. This is session-capacity planning, not a generic run history or queue. Usage percentages, splitting and automatic placement require real scheduling contracts; the demo does not establish TinyStudio support. Documentation describes two-card session capacity, minimum split pieces, past-session exclusion and callbacks for scheduling/placing/moving. Full keyboard placement, dragging, light/narrow and reduced-motion states remain unverified. Pro visual reference only; no implementation selected.
- Current Dataset quality source confirms that Distributions renders two length plots, then three categorical tables, then ratings in one scrolling sequence. The existing separate Overview/Distributions/Cleaning views do not resolve that internal composition problem. Any replacement must retain original aggregate evidence, distinguish overlapping labels from buckets, show units and meaningful domains, and keep exact bin data accessible. Chart presentation changes remain unimplemented and the required independent review remains pending transfer approval; these observations are not visual acceptance.

## Analytical reference comparison and Dataset quality inspection — 2026-10-29

Fresh rendered review, including full public use/non-use, API, keyboard, accessibility, motion and responsive guidance:

- [KPI Board](https://www.affectui.com/blocks/kpi-board): three shallow trend cards above a roughly two-thirds overview/one-third focus region. The shared range selector is above the group; the segment selector belongs inside the overview because it only affects that plot. Changed Last 30 days to Last 7 days and observed the linked totals/series transition. These animated demo values are not application evidence. The composition is for related time series, not length histograms or overlapping label counts. Light desktop inspected; hover, dark, narrow and reduced-motion behavior remain unverified.
- [Breakdown Chart](https://www.affectui.com/components/breakdown-chart), Clean light: one bounded card contains title/total, three aligned measure/slice/segment selectors, plot, exact-value table and legend. Changed Priority segmentation to None: the table simplified to status/count, the six category totals remained 26/36/34/13/96/7 (212 total), and the stacked bars became single-series bars. The selected/hovered Canceled bar and its table row were visibly linked. This provides a useful chart-to-evidence relationship, but its record-level multidimensional slicing cannot be implied by an aggregate-only API. Dark, narrow and complete keyboard flow remain unverified.
- [Compact Breakdown](https://www.affectui.com/components/compact-breakdown), Clean light: approximately 360px card with compact heading/period control, dominant total, thin proportional bar, ranked label/value/share rows and a quiet separated footer. Clicked 30d, then Right to 90d; the radio selection and accessible total changed. Its automatic demo transitions can briefly show intermediate rolling values; no claim of exact animation timing or final sum validation. This is suitable only for mutually exclusive parts of a total. Dataset buckets qualify; multi-label counts do not. No Pro source copied.

Actual authenticated local Dataset quality at 1440×900, Support examples (83 records): user-length bins 40=9 and 41=74; assistant-length bins 46=9 and 47=74. Remaining displayed bins are zero, so axis endpoints beyond observed maxima are not extra observations. However, excessive trailing empty intervals reduce visual usefulness. The rating plot displays 5–14 although the UI describes a 1–10 scale; 11–14 are empty. This is an unresolved domain/presentation defect, not evidence of ratings above ten. Both support and account labels have count83, so rendering those as shares summing to 100% would be false. Training66 + validation17 + uncategorized0 is a true partition. Quality distributions remain vertically stacked with the rating plot below the fold; no full quality-dialog visual acceptance is claimed.

Dataset paging advisor completed with changes required to the initial plan: bucket totals must come from meta.buckets, scope resets must precede requests, transient loads/errors cannot drive Fit/recovery, successful total shrink needs recovery, and local filter/sort scope must remain visible. A bounded shared-table implementation is being validated separately; this research entry does not establish its acceptance.

## Traces pagination follow-up — 2026-10-29

The controlled shared-table pagination increment now has actual local-server and focused test evidence recorded in CHANGELOG.md. This supersedes the earlier statement that Traces server pagination is wholly unimplemented; independent follow-up passed the scoped pagination correction. It does not establish full route acceptance. A fresh 1440×900 screenshot still shows excessive date/model wrapping, uneven queue column allocation and weak inspector metadata hierarchy. A subsequent presentation increment now uses two-line model/time identity, narrower selection/score columns and grouped inspector metadata/source/response. Actual 1440px light/dark and 390px scroll checks are recorded in CHANGELOG.md; the independent bounded review passed. This improves those defects without establishing whole-route or user visual acceptance.

Reopened Apple's rendered Layout guidance after the user's latest correction. Its visual hierarchy, deliberate grouping, control/content distinction and progressive disclosure requirements remain acceptance criteria for the entire application. Merely placing sections behind tabs or recording a reference URL is insufficient. All cumulative route acceptance boxes remain open.

## Traces review composition — 2026-10-29

Inspected the rendered Clean [Review Center](https://www.affectui.com/components/review-center) and its public behavior documentation, including selecting a different queue item. The reference separates a compact queue from the selected item, groups identity/status above content, and keeps decisions together beneath it. This is a Pro visual reference, not adopted source. Its demo autonomously changed counts during inspection; those transitions are not evidence of TinyStudio behavior. Risk/confidence, undo and approval semantics are not applicable to inference scoring unless actual APIs supply them.

Re-read [Apple Layout](https://developer.apple.com/design/human-interface-guidelines/layout) in the rendered browser: align related elements, distinguish controls from content, group related functions, progressively disclose complexity, and adapt to available space. [Law of Proximity](https://lawsofux.com/law-of-proximity/) supports associating review decisions with the selected response. These principles justify a focused review inspector, not a claim that all vertical layouts are wrong.

Actual authenticated Traces at 1440×900 showed four repeated mutation buttons per row, taller rows when the inspector opens, compressed density labels, and an inspector with correction but no scoring or next-record controls. The correction keeps the existing shared collection/inspection templates and real score/correction/delete contracts, groups decisions in the selected inspector, and preserves full response access. Server-window pagination remains a separate unresolved requirement; moving actions does not establish table paging or whole-route acceptance.

## Settings control and state review — 2026-10-29

Reopened the rendered [Settings & Billing General reference](https://www.affectui.com/screens/settings-billing/full), switched from Billing to General, and inspected its horizontal label/help-to-control rows, separated decisions and common save/discard footer. This is a licensed visual reference, not adopted Pro source. Actual TinyStudio Settings review covered Claude, OpenAI, Helper LLM, Embeddings, Hugging Face and Infrastructure at 1440×900. The existing composition follows that structure; control suitability and result hierarchy remain separate concerns. Claude effort was free text despite its documented low/medium/high choices. Infrastructure results mixed inline and wrapped plain text, and a long populated result pushed the footer into the floating assistant's footprint. Corrections and validation are tracked in CHANGELOG.md. At 390×844 the six sections reflowed without document-width overflow; this observation alone does not establish complete responsive acceptance.

# Affect UI complete catalog review register

## Playground composition review — 2026-10-29

Freshly inspected the rendered Clean [Chat Composer](https://www.affectui.com/components/chat-composer) preview and its complete public use/non-use, keyboard, accessibility, motion and responsive documentation, plus the rendered [Agent Chat full screen](https://www.affectui.com/screens/agent-chat/full). Both are licensed visual references; no Pro implementation was copied. The composer groups prompt entry, model choice and send within one bordered surface. The screen gives conversation the main region and groups optional context in an adjacent inspector. TinyStudio retains only supported model, retrieval, configuration and send/cancel actions; demo voice, attachments, app connections and approvals are not adopted.

Freshly read [Apple Layout](https://developer.apple.com/design/human-interface-guidelines/layout) in Browser: use alignment and region boundaries to express relationships, disclose secondary complexity, preserve functionality across window sizes, and check clipping at extremes. This supports a contextual settings inspector and a coherent composer. The user's explicit bottom-composer requirement remains controlling despite the article's general macOS bottom-edge caution; the composer must remain visible inside the viewport and clear of the assistant launcher.

Before correction, authenticated Playground at 1440×900 showed separate conversation/model/composer bands and a modifier-key native repository list. Configuration, prompt and generation remain owned by the existing PlaygroundController. Reference inspection does not establish implementation or whole-page acceptance; validation is recorded in CHANGELOG.md.

## Stats composition review — 2026-10-29

Inspected the rendered Clean preview and documentation for [KPI Chart](https://www.affectui.com/components/kpi-chart) and the Inset [Analytics screen](https://www.affectui.com/screens/analytics). These are Pro visual references, not copied source. KPI Chart places its title and headline directly above a shallow plot with quiet grid lines and first/middle/last date labels. Its documented keyboard model uses arrows to inspect points and Escape to dismiss the readout. Analytics distributes four compact summaries across the content width, then aligns the dominant plot with a narrower breakdown, using fine boundaries and tight gutters. The public preview and linked full-screen Analytics page were visually inspected. Full-screen observation confirmed a four-card summary row, roughly two-thirds plot/one-third breakdown, and a three-panel secondary row. No interactive acceptance of the reference screen is claimed.

Actual TinyStudio Stats at 1440×900 showed left-concentrated totals, a tall almost-solid bar chart, an unnecessary full-width day selector, and inventory below the viewport. Its midpoint label rounded 3.5 to 4 without changing grid geometry. The backend returns recorded daily counts and the ten most frequent models; it does not supply per-day request drilldown or comparison deltas. The adaptation must retain discrete counts and truthful units, avoid invented sparklines/trends, label the model scope accurately, and keep missing values distinct from zero. Implementation and browser acceptance are recorded separately in CHANGELOG.md.

## Remaining blocks, screen composition and agent controls — 2026-10-29

Evidence boundary: the following records consolidate primary-documentation reads and the bounded read-only agent-component research returned during this review. They are not implementation or acceptance claims. Except App Directory, these items have no new rendered or exercised evidence in this increment. Cached documentation dates range from October 17 to October 29; live rendering takes precedence where it differs. The separate screen researcher had incomplete retrieval, so its catalog-only results do not advance statuses.

### Operational screen compositions

- [App Shell Inset](https://www.affectui.com/screens/app-shell): a full-viewport application frame with a 272px default sidebar (220–380px resize range) and optional 360px right panel (280–560px). Keyboard-focusable separators support arrows, Shift and Home/End. It is intended for internal/SaaS/assistant applications, not marketing or deep navigation trees. The useful contract is independent workspace and inspector sizing, not merely a rounded outer card. TinyStudio must preserve usable main-content width when an inspector opens. Documentation read; resizing and focus not exercised.
- [Agent Run](https://www.affectui.com/screens/agent-run): a single autonomous run with ordered steps, tool calls and approval pauses, plus a draft/diff panel. Stable step identities, arguments/results, duration and approval state matter. It is not a many-run list or chat replacement. Its default auto-run demonstration must not be used as real execution evidence: actual backend events must own status, approval and completion. Full-height regions scroll independently; below 1000px the secondary panel layers over the main view. Keyboard approval and diff actions require real permission checks. Documentation read; no run exercised.
- [Evals](https://www.affectui.com/screens/evals): comparison of prompt/model versions against a fixed suite, with test rows and a prompt diff. This differs from monitoring an individual production request. Keyboard row activation and diff controls are documented; the right column moves below the table under 1000px. Shell props do not establish an evaluation engine integration. TinyStudio consumers still need real suite identity, comparable results and accurate metric semantics.
- [Analytics](https://www.affectui.com/screens/analytics): an overview of key figures, channel charts and breakdowns. The reference explicitly distinguishes overview use from dense, rapidly updating monitoring. Its documented shell API does not provide aggregation semantics. Reuse composition only after deciding whether TinyStudio values are counts, rates, distributions or time series; do not sum unrelated metrics.

### Remaining blocks

- [App Directory](https://www.affectui.com/blocks/app-directory): integration discovery with category navigation, search, connection-state filtering and a card grid. Appropriate for a moderate integration catalog; two or three choices need a shorter selector, hundreds need server search/paging. At desktop width the reference uses an outer gray tray, an inset white surface, one search/filter toolbar, a category rail and three card columns. Below 820px categories become chips with two columns; below 560px one column. **Observed at 1280×720 in an isolated browser tab:** Filter opened an anchored menu over the cards; choosing Connected collapsed it, changed its label, reduced Essentials from eight to one and left Slack visible. This verifies the local filter interaction, not connecting an integration, full-height layout, keyboard focus return or responsive states.
- [Logo Marquee](https://www.affectui.com/blocks/logo-marquee): a moving trusted-by strip for many logos; three to five logos are better static. It is unsuitable for choices users need to inspect and compare. Duplicate loop copies are hidden/inert; linked logos must remain keyboard reachable and pause appropriately. Reduced motion uses a static wrapping arrangement. Documented one/two rows, hover pause, grayscale/brand treatment and touch scrubbing are not yet visually verified. No operational TinyStudio consumer selected.
- [Testimonial Wall](https://www.affectui.com/blocks/testimonial-wall): a landing/pricing composition for eight or more quotes; not searchable reviews or operational records. Three/two/one columns respond at 740/480px. Semantic quotations, hidden loop duplicates, pause on focus and a static reduced-motion arrangement are integral. No operational TinyStudio consumer selected; do not invent testimonials to fill space.
- [Pricing Plans](https://www.affectui.com/blocks/pricing-plans): up to three plans with monthly/yearly selection and expandable features. More plans require a comparison table; one-time charges should not inherit a billing-period toggle. Radio semantics, inaccessible collapsed content and explicitly announced excluded features matter. Columns become a single column below 860px. No authority or requirement to introduce billing is implied by reviewing this reference.
- [Invoice & Credit Note](https://www.affectui.com/blocks/invoice-credit): two layered document surfaces with invoice/credit selection, line selection and bounded credit amounts. For a small billing interaction, not a print/PDF replacement or a huge line-item table. The inactive sheet is inert; amount controls remain bounded by actual line totals. Below 560px party details stack and some rate/tax detail is reduced. The initially guessed invoice-credit-note URL returned 404; the exact catalog URL above resolved. No TinyStudio billing workflow selected.
- [Agent Gallery](https://www.affectui.com/blocks/agent-gallery): a feature-gallery accordion with gradient panels, one expanded agent, triggers/steps/skills/guardrails and narrow-screen peek behavior. It is a marketing/explanation pattern, not an operational run viewer or side-by-side comparison. A few alternatives fit tabs better. Delegated documentation review only.

### Agent control responsibilities

These five component notes derive from the returned restricted read-only researcher report. They require parent visual and interaction inspection before selection. Pro visibility does not establish permission to copy protected source.

- [Agent Graph](https://www.affectui.com/components/agent-graph): a small network of agents, tools and users with calls and a selected-node detail surface. Stable identities and real call direction/time/status are required. Not a large topology, timeline or single-call display. Keyboard nodes/chips, Escape focus restoration and a text alternative must accompany the graphic; status cannot rely on color.
- [Agent Lanes](https://www.affectui.com/components/agent-lanes): recent per-agent work over time, including handoffs and approval pauses. Not a multi-run inventory or long-range scheduler. Task start/end/status and a common clock are necessary. Critically, omitted approval handlers permit demonstration state changes; actual TinyStudio approval must come from the server, not an optimistic visual simulation.
- [Agent Steering](https://www.affectui.com/components/agent-steering): an active plan with queued guidance for the next step, immediate interruption and pause/resume controls. Not pre-run planning or a one-off approval. Stable plan/note state and callbacks describe an interface, not executor capability. Queue, interrupt, newline and clear keyboard actions must preserve the distinction between queued intent and accepted execution.
- [Autonomy Dial](https://www.affectui.com/components/autonomy-dial): three levels with always-ask pins. Pins override broad autonomy; the documented levels distinguish asking for everything, allowing safe actions and allowing unpinned actions. Radio/toggle semantics and keyboard arrows are documented. UI selection must never expand backend authorization or silently remove required approvals.
- [Agent Plan](https://www.affectui.com/components/agent-plan): editable ordered steps followed by runtime progress and retry. Not hidden reasoning, a personal to-do list or a single approval. Stable step IDs, edited order, errors and durations are part of the contract. Keyboard rename/add/remove/reorder and narrow layouts are documented; edits must reach the actual submitted plan.
- Agent Gallery is recorded with blocks above; its explanation-oriented purpose is distinct from the operational controls.

That earlier researcher only confirmed catalog-level descriptions for Agent Cursors, Agent Findings, Agent Handoff, Agent Inbox, Agent Ledger, Agent Queue and Agent Tree, and omitted Agent Monitor. The subsequent parent documentation review is recorded under agent activity and change contracts below; it supersedes those discovery-only statuses. In particular, a ledger-like appearance must not imply that real operations are reversible, and an inbox-like appearance must not imply that approval/retry APIs exist.

### Apple interaction constraints carried into selection

- [Popovers](https://developer.apple.com/design/human-interface-guidelines/popovers): transient, anchored access to a small related set of functions; avoid obscuring essential content and cascading popovers. A multi-selection operation may remain open. Nonmodal outside dismissal should preserve work; cancellation semantics must be explicit. Apply the interaction rationale to the web rather than claiming iOS compact-sheet behavior is a universal web requirement.
- [Segmented controls](https://developer.apple.com/design/human-interface-guidelines/segmented-controls): closely related choices with explicit grouping and selection, comparable labels and appropriately limited counts. Do not mix actions with selected-state choices. Apple's macOS guidance distinguishes main-window tab views from segmented toolbar/inspector controls: using segmented controls everywhere is not compliance.


Scope: the entire TinyStudio interface, every route and section, reusable foundations, components, blocks and templates. This register supplements DESIGN.md and the full redesign checklist; it does not replace either or narrow the goal.

## Evidence and status rules

On 2026-10-29 the parent inspected the live https://www.affectui.com/components catalog in Safari. Its sidebar enumerated 69 components, 11 blocks, 15 screens, 24 illustrations and 8 motion items (127 total). A fresh detail-page reload then enumerated **70** components, adding **Agent Monitor**, for **128** total items. The initial catalog tab was older than the refreshed detail page. The earlier web index listed only 68 components: **Plan vs Actual** accounts for the difference. Use the rendered catalog as the current discovery inventory.

Discovery is not in-depth review. Rows below deliberately start at **discovered**. Earlier reviews and source adoption recorded in DESIGN.md remain historical evidence; reconcile each with its exact reference and inspected states before advancing this register. Do not infer source licensing from visual availability. Pro previews are visual references unless separately licensed; do not copy protected implementation.

For each item, add its exact detail URL, composition observations (hierarchy, proportions, grouping, typography), exercised interactions, reviewed states, relevant TinyStudio consumers, and limitations. Review all inventory items before final selection; a not-applicable disposition needs an explicit reason, not silent exclusion. Then record implementation and actual-route acceptance separately. Static illustrations need visual/placement/reduced-motion consideration rather than invented interactive tests.

| Status | Evidence required |
| --- | --- |
| Discovered | Live catalog entry observed |
| Read | Detail description and documented behavior read |
| Visual | Rendered component/block/screen examined |
| Exercised | Relevant controls used and resulting states observed |
| Selected / not applicable | Rationale after comparing alternatives; all app consumers considered |
| Implemented | Shared source and consuming routes identified |
| Accepted | Actual-route visual and interaction proof, with remaining hosted/user gates explicit |

## Components (71)

| Item | Review status | Findings / evidence |
| --- | --- | --- |
| AI Answer | Read | See complete remaining catalog documentation review |
| Chat Composer | Read | See detailed continuation notes |
| Thinking State | Read | See complete remaining catalog documentation review |
| Writing Assistant | Read | See complete remaining catalog documentation review |
| Thinking Loader | Read | See complete remaining catalog documentation review |
| Combobox | Read | See detailed continuation notes |
| Command Menu | Read | See controls and feedback review |
| Date Range Picker | Read | See controls and feedback review |
| Dropzone | Read | See controls and feedback review |
| OTP Input | Read | See complete remaining catalog documentation review |
| Segmented Control | Read | See detailed continuation notes |
| Stepper Form | Read; light Workspace visual; partial interaction | See detailed note below |
| Toaster | Read | See controls and feedback review |
| Usage Meter | Read | See complete remaining catalog documentation review |
| Agent Cursors | Read | See agent activity and change contracts below |
| Agent Findings | Read | See agent activity and change contracts below |
| Agent Graph | Read | See remaining blocks, screen composition and agent controls review |
| Agent Handoff | Read | See agent activity and change contracts below |
| Agent Inbox | Read | See agent activity and change contracts below |
| Agent Lanes | Read | See remaining blocks, screen composition and agent controls review |
| Agent Ledger | Read | See agent activity and change contracts below |
| Agent Monitor | Read; Clean light visual | See agent activity and change contracts below |
| Agent Queue | Read | See agent activity and change contracts below |
| Agent Steering | Read | See remaining blocks, screen composition and agent controls review |
| Agent Tree | Read | See agent activity and change contracts below |
| Autonomy Dial | Read | See remaining blocks, screen composition and agent controls review |
| Cited Answer | Read | See complete remaining catalog documentation review |
| Compact Breakdown | Read; Clean light visual; pointer/keyboard period selection exercised | See analytical reference comparison |
| Conflict Radar | Read | See context, execution and decision pattern review |
| Context Map | Read | See context, execution and decision pattern review |
| Context Window | Read | See context, execution and decision pattern review |
| Decision Tree | Read | See context, execution and decision pattern review |
| Delegate Board | Read | See complete remaining catalog documentation review |
| Experiment Card | Read; Clean light visual; metric and variant selection exercised | See controls and feedback review |
| Fallback Chain | Read | See context, execution and decision pattern review |
| Fan-out Grid | Read | See context, execution and decision pattern review |
| Integration Picker | Read | See complete remaining catalog documentation review |
| Integration Settings | Read | See complete remaining catalog documentation review |
| KPI Chart | Read | See complete remaining catalog documentation review |
| Memory Panel | Read | See complete remaining catalog documentation review |
| Message Actions | Read | See complete remaining catalog documentation review |
| Model Picker | Read | See complete remaining catalog documentation review |
| Parallel Attempts | Read | See complete remaining catalog documentation review |
| Permission Request | Read | See context, execution and decision pattern review |
| Plan vs Actual | Read | See complete remaining catalog documentation review |
| Profile Menu | Read | See controls and feedback review |
| Project Card | Read | See complete remaining catalog documentation review |
| Prompt Library | Read | See complete remaining catalog documentation review |
| Scheduled Runs | Read; Clean dark visual; Manual and detail/Escape exercised | See 2026-10-30 follow-up; real session planning required, no implementation selected |
| Review Center | Read; partial desktop visual | See context, execution and decision pattern review |
| Tips Panel | Read | See complete remaining catalog documentation review |
| Tool Call Card | Read | See complete remaining catalog documentation review |
| Trace Waterfall | Read | See detailed continuation notes |
| Approval Card | Read | See context, execution and decision pattern review |
| Breakdown Chart | Read; Clean light visual; segment selection exercised | See analytical reference comparison |
| Button | Read | See complete remaining catalog documentation review |
| Data Table | Read; Clean light visual; Tool sorting exercised | See detailed continuation notes |
| Filter Menu | Read; Clean light visual; checkbox exercised | See controls and feedback review |
| Reaction Bar | Read | See complete remaining catalog documentation review |
| Smart Recommendation | Read | See complete remaining catalog documentation review |
| Agent Plan | Read | See remaining blocks, screen composition and agent controls review |
| Billing Schedule | Read | See complete remaining catalog documentation review |
| Change Summary | Read | See complete remaining catalog documentation review |
| Comments Panel | Read | See complete remaining catalog documentation review |
| Email Draft | Read | See complete remaining catalog documentation review |
| Progress Tracker | Read | See complete remaining catalog documentation review |
| Comment Thread | Read | See complete remaining catalog documentation review |
| Inline Diff | Read | See complete remaining catalog documentation review |
| Setup Guide | Read | See complete remaining catalog documentation review |
| Decision Inbox | Read | See context, execution and decision pattern review |
| Notification Stack | Read | See controls and feedback review |

## Blocks (11)

| Item | Review status | Findings / evidence |
| --- | --- | --- |
| Client Context | Read | See detailed continuation notes |
| Invoice & Credit Note | Read | See remaining blocks, screen composition and agent controls review |
| Logo Marquee | Read | See remaining blocks, screen composition and agent controls review |
| Metrics Dashboard | Read; visual; partial interaction | See detailed continuation notes |
| Pricing Plans | Read | See remaining blocks, screen composition and agent controls review |
| Task Board | Read | See detailed continuation notes |
| Testimonial Wall | Read | See remaining blocks, screen composition and agent controls review |
| AI Workspace | Read | See detailed continuation notes |
| App Directory | Read; desktop visual; partial filter interaction | See remaining blocks review; connection and responsive states unverified |
| KPI Board | Read; light desktop visual; range selection exercised | See analytical reference comparison |
| Agent Gallery | Read | See remaining blocks, screen composition and agent controls review |

## Screens (15)

| Item | Review status | Findings / evidence |
| --- | --- | --- |
| App Shell Flat | Read | See remaining screen documentation and settings composition |
| App Shell Inset | Read | See remaining blocks, screen composition and agent controls review |
| App Shell Workspace Flat | Read | See remaining screen documentation and settings composition |
| App Shell Workspace Inset | Read | See remaining screen documentation and settings composition |
| Assistant Home | Read | See remaining screen documentation and settings composition |
| Folders | Read | See remaining screen documentation and settings composition |
| Folder | Read | See remaining screen documentation and settings composition |
| Integrations | Read | See remaining screen documentation and settings composition |
| Agent Chat | Read | See remaining screen documentation and settings composition |
| CRM | Read | See remaining screen documentation and settings composition |
| Analytics | Read | See remaining blocks, screen composition and agent controls review |
| Agent Run | Read | See remaining blocks, screen composition and agent controls review |
| Settings & Billing | Read; desktop visual; four tabs inspected | See remaining screen documentation and settings composition |
| Onboarding | Read | See remaining screen documentation and settings composition |
| Evals | Read | See remaining blocks, screen composition and agent controls review |

## Illustrations (24)

| Item | Review status | Findings / evidence |
| --- | --- | --- |
| Cover Flow | Read | See complete remaining catalog documentation review |
| Frost Cloud | Read | See complete remaining catalog documentation review |
| Glow Cloud | Read | See complete remaining catalog documentation review |
| Glow Keyboard | Read | See complete remaining catalog documentation review |
| Integration Beam | Read | See complete remaining catalog documentation review |
| Logo Cluster | Read | See complete remaining catalog documentation review |
| Model Wheel | Read | See complete remaining catalog documentation review |
| Project Flow | Read | See complete remaining catalog documentation review |
| DACH Region | Read | See complete remaining catalog documentation review |
| Empty Calendar | Read | See complete remaining catalog documentation review |
| Integration Flow | Read | See complete remaining catalog documentation review |
| Voice Orb | Read | See complete remaining catalog documentation review |
| Tab Overload | Read | See complete remaining catalog documentation review |
| World Map Continental | Read | See complete remaining catalog documentation review |
| Ghosted Chat | Read | See complete remaining catalog documentation review |
| Lead Sourcing | Read | See complete remaining catalog documentation review |
| Enrichment Orbit | Read | See complete remaining catalog documentation review |
| Outreach Branches | Read | See complete remaining catalog documentation review |
| Glass Folder | Read | See complete remaining catalog documentation review |
| Side Folder | Read | See complete remaining catalog documentation review |
| Team Invite | Read | See complete remaining catalog documentation review |
| Layer Stack | Read | See complete remaining catalog documentation review |
| Agent Team | Read | See complete remaining catalog documentation review |
| Integration Grid | Read | See complete remaining catalog documentation review |

## Motion (8)

| Item | Review status | Findings / evidence |
| --- | --- | --- |
| Blur Crossfade | Read | See complete remaining catalog documentation review |
| Gradient Floor | Read | See complete remaining catalog documentation review |
| Gradient Text | Read | See complete remaining catalog documentation review |
| Grid Background | Read | See complete remaining catalog documentation review |
| Number Ticker | Read | See complete remaining catalog documentation review |
| Shimmer Loader | Read | See complete remaining catalog documentation review |
| Text Reveal | Read | See complete remaining catalog documentation review |
| Tilt Card | Read | See complete remaining catalog documentation review |

## Current observations that constrain the review

- Apple Layout was read in the native browser: https://developer.apple.com/design/human-interface-guidelines/layout. Its guidance distinguishes controls from content, uses alignment/grouping to communicate hierarchy, and supports progressive disclosure and adaptable layouts. It does not ban scrolling or prescribe placing every form in a modal. Desktop and smaller-window compositions must retain functionality and recognizable context.
- The live Stepper Form detail navigation returned a blank surface on the first attempt; reload was initiated. This is not visual inspection or completed interaction evidence. Do not adopt a wizard based only on its name.
- Parent checkout inspection at the start of this increment found clean Git status and HEAD 400e19f, newer than the prior research checkpoint. CHANGELOG.md now records contextual Laboratory authoring dialogs and 804-test verification by intervening work. These are source-record claims to reconcile against actual rendering; this register does not assert that those changes solve the user-reported composition problems.

## Stepper Form detail review — partial, not adoption approval

Reference: https://www.affectui.com/components/stepper-form. Read usage, non-usage, API, keyboard, accessibility, motion and responsive sections in the rendered accessibility tree. Its explicit intended role is a sequential setup with dependent answers and a final review. Freely editable settings and very short forms should use other patterns. This is a key distinction for all TinyStudio creation/settings surfaces; a component name alone cannot choose the flow.

Observed interaction: Continue moved the prefilled Workspace step to Team; the completed Workspace became a return button and the current-step announcement updated. No email entered and no completion submitted. Documentation describes retained values, inline errors, focus movement and reduced-motion behavior; those remain documented claims, not tested states.

Visual limitation: the dark preview region remained blank in two screenshots despite the accessible form and successful state transition. Therefore typography/proportion fidelity, light/dark variants, validation, Back retention, final review and submission are still unverified. Do not mark this item visually reviewed or reuse its layout from imagination.

Follow-up: switching the demo to light and using Back rendered the Workspace card. Observed a bounded white form card, a compact four-part progress strip, heading plus subordinate explanation, aligned field labels, and a separated footer with trailing primary action. Back restored the existing prefilled name/URL and focused the name field. This only establishes prefilled-value retention, not edited-value persistence. The narrow form is appropriate to a bounded setup step, not a sizing model for desktop analytical workspaces. Dark rendering, other steps, validation and completion remain open.

## Detailed continuation notes — 2026-10-29

These notes consolidate the preceding browser review and documentation reads. Acquisition dates identify fetched source versions, not proof of current rendered behavior. No item in this increment is implementation or application acceptance evidence. Final selection remains open until alternatives across the inventory are reviewed.

### Data Table

Source: https://www.affectui.com/components/data-table

Status: Read; Clean light visual; Tool sorting exercised on 2026-10-29. Rendered preview uses compact rows, subtle column-header surfaces and separators, restrained category badges, and a compact bottom action row. Clicking Tool sorted the five observed records to Figma, Framer, Linear, Loom, Vercel. Selecting Framer replaced the footer Add row action with 1 selected, Delete and Clear; Clear restored the unchecked state. No destructive reference action was exercised. Editable spreadsheet interactions, cell/range selection, copy/paste and undo remain unexercised. The documentation explicitly calls for added virtualization, filters and server paging for thousands of rows; this preview is not a server-pagination implementation. Preserve column widths with internal horizontal overflow rather than crushing content. Pro visual reference only; no protected source adopted.

### Metrics Dashboard

Source: https://www.affectui.com/blocks/metrics-dashboard

Status: Read; visual; partial interaction. Native light preview: four equal-height KPI cards, one dominant chart, two aligned ranked lists. Exercised Orders, Bar chart and Compare previous; selection and bars updated. Supporting lists remained labelled Revenue: do not assume every region follows the active KPI. Documentation counts missing values as zero and can confirm export without a handler; neither behavior is acceptable for missing scientific evidence or an unperformed TinyStudio export. Keyboard and narrow states not exercised.

### AI Workspace

Source: https://www.affectui.com/blocks/ai-workspace

Status: Read. Full assistant application with sidebar, projects and conversations. Documentation explicitly recommends Chat Composer alone for a corner widget. This is not a suitable whole-shell transplant for the omnipresent TinyStudio assistant. Native documentation read; preview interaction and visual review remain pending.

### Segmented Control

Source: https://www.affectui.com/components/segmented-control

Status: Read. Two to six immediate choices; distinguish radio-group selection from tabs controlling panels. Roving focus, arrows, Home/End and visible active state. Long labels or many options need another pattern. Current visual review pending; documentation acquired October 27.

### Combobox

Source: https://www.affectui.com/components/combobox

Status: Read. Searchable long option lists, single or multiple selections; short lists can expose choices directly. Not an action menu. Preserve input focus and announce active options, with wrapping selected chips. Current visual review pending; documentation acquired October 17.

### Chat Composer

Source: https://www.affectui.com/components/chat-composer

Status: Read. Compact message entry with model/tool controls. Dictation and loading examples include demo behavior: preserve real TinyStudio provider and component-state integrations. Enter submits, Shift+Enter adds a line; picker has independent keyboard handling. Current visual review pending; documentation acquired October 27.

### KPI Board

Source: https://www.affectui.com/blocks/kpi-board

Status: Read. Related trends with overview and focus regions. Range changes all data; overview selection changes only that region. Documentation sums series for headline values; rates, latency and loss require their own justified aggregation. Decorative charts cannot be the sole evidence inspector. Current visual review pending; documentation acquired October 17.

### Client Context

Source: https://www.affectui.com/blocks/client-context

Status: Read. Entity context with summary, scoped tabs and timeline. Useful grouping reference, but no pagination for hundreds of events and synchronization belongs to the consuming application. Current visual review pending; documentation acquired October 17.

### Task Board

Source: https://www.affectui.com/blocks/task-board

Status: Read. Status lanes, quick add and detail panel suit human-managed tasks. TinyStudio execution status is server-owned: dragging a card cannot imply changing an actual run state. Keyboard status buttons are an alternative to drag. Current visual review pending; documentation acquired October 17.

### Compact Breakdown

Source: https://www.affectui.com/components/compact-breakdown

Status: Read. One total and its ranked parts, approximately seven or fewer; not a time trend. Stable item IDs preserve identity across periods. Focused rows provide the same highlight as hover; text exposes values and shares while the bar is decorative. Candidate for additive categories only, not unrelated quality measures. Current visual review pending; documentation acquired October 29.

### Trace Waterfall

Source: https://www.affectui.com/components/trace-waterfall

Status: Read. Nested spans show elapsed time, cost and tokens; tree navigation and a focused span dialog preserve context. Times are milliseconds relative to run start; own costs/tokens must not be pre-summed before child aggregation. Retried calls retain separate IDs and failed history. Requires actual span evidence, not invented timing from an unrelated trace table. Current visual review pending; documentation acquired October 28.

Earlier Experiment Card output was truncated; the subsequent complete documentation read below resolves that read gap. Clean light preview review is recorded in the Experiment Card section below.

## Controls and feedback review — 2026-10-29

Fetched documentation below was read through usage, API, keyboard, accessibility and responsive guidance where provided. Native preview proof is explicitly limited to Filter Menu. Current parent HEAD is 85859e8; the previously observed foreign CSS change is no longer dirty. Parent changed only this register and CHANGELOG.md in this increment.

### Experiment Card

Source: https://www.affectui.com/components/experiment-card

Status: Read; Clean light visual and partial interaction on October 29, 2026. Rendered compact card places title/status/day at top, metric choices immediately above aligned variant rows, and the selected variant action in a separated footer. Switched to Conversion; settled values were 4.2%, 4.7%, 4.0%, with B +11.9%. Selecting B revealed visitors/confidence and Ship B; no shipping action was exercised. The first screenshot captured rolling values mid-transition, so it is not numeric evidence; the settled accessible state was checked separately. Keyboard, dark and narrow states remain unverified. Documentation acquired October 29, 2026. Compact comparison of up to approximately five variants measured on the same metrics. Not an authoring form, not a time-series display. The header button opens details; metrics and variants are radio groups; Space selects a variant and Escape closes with focus return. A decorative mini-chart repeats values available in rows. Candidate for evaluation/model comparisons, with observed metric evidence and explicit action semantics; do not equate an apparent leader with a statistically established winner. Pro marker remains in the native catalog: retrieved usage examples do not establish implementation licensing.

### Filter Menu

Source: https://www.affectui.com/components/filter-menu

Status: Read; Clean light visual; checkbox exercised. Documentation acquired October 29, 2026. Grouped predefined facets with removable chips and Clear all. Not free-text or numeric-range search. Current native Clean variant documents a 400px maximum with single-line chips and +N overflow, versus fetched text describing 420px and wrapping: prefer the explicitly observed variant, without assuming identical geometry across styles. Visual: white compact panel, heading/action on one row, chips separated from disclosure groups by a hairline, right-aligned counts, blue checked boxes, quiet keyboard footer. Clicked New lead: checkbox changed to checked, removable chip appeared, Stage changed from 1 filter to 2 filters. Native preview displays the panel itself, not a trigger/popover integration; overlay positioning, outside dismissal, Escape and focus restoration still require TinyStudio integration proof. User requires table filters to overlay rather than displace rows. Remaining keyboard, chip-removal, Clear all and narrow states untested.

### Toaster

Source: https://www.affectui.com/components/toaster

Status: Read. Documentation acquired October 27, 2026. Short action outcomes and nonblocking failures. Field errors belong beside inputs; required decisions need a dialog/banner; long-running tasks need a progress view. Promise notifications reflect actual asynchronous completion. Timers pause during focus/hover and hidden tabs; F8 reaches notifications, Escape dismisses and returns focus. Must avoid collision with the omnipresent assistant and table pagination. Current visual/interaction review pending.

### Dropzone

Source: https://www.affectui.com/components/dropzone

Status: Read; Clean light visual and simulated retry exercised October 29, 2026. Rendered card is bounded to about 520px with a shallow drop target, compact separated file rows, file-type icons, subordinate size/progress, per-file error and retry, and a quiet aggregate footer. Demo began with two completed files, one connection failure and one size rejection. Retry on Budget-2027.xlsx restarted progress and settled at 3 of 4 uploaded; rejected Product-Demo.mp4 remained rejected. This was built-in simulated data, not an actual upload. Keyboard, dark and narrow states remain unverified. Documentation acquired October 27, 2026. Multiple file imports with per-file progress, rejection, retry and removal. A lone avatar needs a lighter picker; long transfers need a separate progress panel. onUpload is simulated when omitted, so TinyStudio must bind real upload progress and failures. File type/size hints and named retry/remove actions are part of the pattern. The documented 520px maximum is a component proportion, not a reason to stretch every import field across a desktop workspace. Current visual/interaction review pending.

### Command Menu

Source: https://www.affectui.com/components/command-menu

Status: Read. Documentation acquired October 17, 2026. Keyboard shortcut access to many actions while visible primary navigation remains available. Not another permanently visible duplicate page navigator. Grouped results, active option, arrow navigation; Escape clears then closes. Candidate command registry must preserve page permissions and real action handlers; no unconfigured assistant fallback. Current visual/interaction review pending.

### Date Range Picker

Source: https://www.affectui.com/components/date-range-picker

Status: Read. Documentation acquired October 29, 2026. Range filtering with presets and optional comparison; changes apply explicitly, Escape cancels. Calendar grid supports arrows, PageUp/Down and focus return. Unsuitable for single-date entry or ranges spanning many years. TinyStudio time boundaries and timezone semantics must come from the data contract, not demo defaults. Compare defaults to previous period in the reference; do not show comparison evidence unless available. Current visual/interaction review pending.

### Notification Stack

Source: https://www.affectui.com/components/notification-stack

Status: Read. Documentation acquired October 29, 2026. Persistent small set of announcements, approximately three or four maximum; larger histories belong in an inbox. Front card alone is focusable; dismissal reveals the next. Distinct from transient Toaster and blocking approval dialogs. Do not use a decorative stack to hide actionable run failures. Current visual/interaction review pending.

### Profile Menu

Source: https://www.affectui.com/components/profile-menu

Status: Read. Documentation acquired October 29, 2026. Compact identity and account actions; optional setup progress. Not a nested navigation hierarchy or a long onboarding flow. Arrow/Home/End menu navigation; progress should reflect actual completed setup if present. Account and logout handlers must preserve existing authentication semantics. Current visual/interaction review pending.

## Context, execution and decision pattern review — 2026-10-29

Ten primary detail pages were read through their public documentation. All visual and actual-app gates remain open except the explicitly limited Review Center observations. Pro documentation is not licensed source access. These distinctions constrain later selection across the entire application; they do not narrow the redesign scope.

### Context Window

Source: https://www.affectui.com/components/context-window

Current request token composition, at most about six distinguishable parts, beside a composer or session inspector; not monthly billing. A bounded 360px card uses a decorative cell field plus accessible numeric rows and expandable items. Provider/tokenizer counts are inputs; item sums must not be counted again. Without real callbacks removal and compaction are local demonstrations. Preserve locked parts and only show confirmed compacted totals. Reduced motion removes movement; narrow layout changes cell count and labels.

### Context Map

Source: https://www.affectui.com/components/context-map

Ordered token-sized tiles explain detailed context and before/after compaction; not persistent memory or execution timing. Stable IDs/order, actual token limits and retained before-state are necessary. Selected tiles pin details; Escape dismisses. Tiny tiles can lose their labels, so text access and keyboard reachability need actual verification before adoption. The documented map keeps height while tiles resize; below 480px it shortens. No actual compaction capability is implied.

### Decision Tree

Source: https://www.affectui.com/components/decision-tree

Two to four consequential choices, optionally one follow-up level, make alternative effects inspectable. Not a yes/no approval, permission grant or review of completed work. Use concrete effects and at most one recommended branch; callback results must reflect actual execution. Wide layout runs left to right, below 640px top to bottom. Branch buttons, numbered shortcuts, focusable consequence notes and an Other instruction field are documented. Shortcuts require scoped focus and must not trigger while editing unrelated text.

### Conflict Radar

Source: https://www.affectui.com/components/conflict-radar

Agent columns and file rows expose overlapping concurrent code edits with per-conflict radio choices. Not a competition where one attempt wins, a timeline or a single-agent change log. Merge is available only after conflicts are settled and agents stop working, but the server must revalidate these conditions. Controlled resolutions should represent real selections; omitted controlled state only stores UI picks. Text names conflict status; narrow layouts reduce column labels. No current TinyStudio consumer selected.

### Fallback Chain

Source: https://www.affectui.com/components/fallback-chain

An ordered sequence of model/provider attempts for one request; not simultaneous candidate comparison or a complete run timeline. Real waiting/trying/failed/answered/skipped statuses and durations explain why one provider answered. Retry appears after all fail; it requires a real handler. Decorative paths accompany an ordered textual list and focusable tooltips. Below 520px the horizontal diagram becomes a tree. Do not invent routing attempts from a single response.

### Fan-out Grid

Source: https://www.affectui.com/components/fan-out-grid

A batch overview for hundreds of tasks, emphasizing progress, retries and failures. Not a few agents or a single-run timeline. Stable task IDs persist across retries, attempts increase, and start/end/now are milliseconds relative to the job. ETA needs at least three completed tasks plus defensible worker concurrency; unknown ETA should stay unknown. Single grid tab stop with arrow/Home/End navigation, pinned details and state-filter buttons. Container width determines wrapping; below 520px cells and metric placement change.

### Permission Request

Source: https://www.affectui.com/components/permission-request

A scoped access decision with concrete targets, effects and once/timed/always duration; not OAuth or broad initial autonomy settings. Card width is bounded at 400px, preview initially shows three rows, and resolved state collapses to a line. Destructive requests exclude Always. The component only asks: backend enforcement must check identity, granted action, expiry and revocation. Null decision means permission absent. Dialog/radio semantics, Enter/arrow/Escape shortcuts and narrow touch controls are documented, not exercised.

### Approval Card

Source: https://www.affectui.com/components/approval-card

One pending consequential tool action with preview/details and approve/deny/edit controls, not many-request triage. React 19 documented. Promise rejection keeps approval pending and busy controls retain focus. Always allow defaults on in the reference; TinyStudio must omit it unless supported by existing authorization policy. Hover/focus pauses the demo countdown, which must not override authoritative server expiry. Below 400px actions share a row; truncated descriptions/values must not hide material consequences without a way to inspect them.

### Decision Inbox

Source: https://www.affectui.com/components/decision-inbox

A small queue of quick binary decisions with inline confirmation and folded rows; not complex forms or passive notifications. Stable IDs and real decision callbacks are required. Heading count and confirmations are live regions; folded content is inert, Tab/Enter/Space activate choices. Reference confirmation animation alone cannot establish server acceptance or successful completion. Public installation example requires React 19; source adoption/licensing still needs its own check.

### Review Center

Source: https://www.affectui.com/components/review-center

A fixed-height review queue plus selected proposal, with risk/age ordering, evidence, edits, rejection reasons and skip. It is distinct from background task status, simple yes/no questions, permission grants and branching choices. Real IDs, waiting timestamps, actual evidence and supported decision/undo semantics are required; do not fabricate confidence or reversibility. Under 640px queue moves above the detail; under 420px metadata wraps and diffs scroll. Keyboard actions A/E/R/S/J/K/F/Z and focus transfer are documented, not exercised. Live Clean light preview at 1280×720 showed a narrow left queue, wider right proposal, compact metadata/progress header, highlighted uncertain spans and a stable bottom action row. The demo automatically advanced from 24 reviewed to an empty 33-of-33 state without approval clicks; this establishes visual populated/empty observations only, not successful approval, filtering, editing, undo or keyboard behavior.

## Remaining screen documentation and settings composition — 2026-10-29

Read the eleven remaining screen detail pages, completing documentation reads for all fifteen screen entries. This is not complete visual review: only Settings & Billing gained new desktop screenshots/tab interaction in this increment. Screen APIs are often shell-only; do not infer functioning backend workflows from demo content. All remain unaccepted in TinyStudio.

### App Shell Flat

Source: https://www.affectui.com/screens/app-shell-flat

Edge-to-edge frame for dense tables, boards and editors; the inset alternative is for a distinct card-like content surface. Shares page classes/props with Inset. Optional header can be disabled, avoiding repeated titles/actions. Full viewport; sidebar becomes an overlay drawer under 900px. This supports choosing framing by content rather than nesting every page in extra cards.

### App Shell Workspace Flat

Source: https://www.affectui.com/screens/app-shell-workspace-flat

Three-region shell for a conversation/editor plus real tools, sources or inspector. Not appropriate when content needs full width. Default sidebar272px and panel360px have bounded resize ranges, keyboard separators and double-click reset. Collapsed work area is inert, with an explicit reopen action. Below900px right panel becomes a dismissible drawer. Demo terminal/browser suggestions must not become dead controls.

### App Shell Workspace Inset

Source: https://www.affectui.com/screens/app-shell-workspace

Same workspace responsibilities and resize/drawer behavior as Flat, with inset visual framing. The right region must contain contextual work; an always-present empty region wastes space. Use panel/onOpen with real handlers, not a decorative side column. Current rendered resize and keyboard behavior remain untested.

### Assistant Home

Source: https://www.affectui.com/screens/assistant-home

Prompt-first landing page intended for products where chat is the primary action. Explicitly unsuitable when chat is secondary. Therefore it is not a reason to replace TinyStudio operational navigation with a chat home. Documented API is shell navigation/workspace/identity, not an assistant backend contract.

### Folders

Source: https://www.affectui.com/screens/files

Card overview for document/knowledge areas or projects. Explicitly not thousands of files; use virtualized records for that scale. Whole-container height and ordinary labelled sidebar controls are documented. Folder-card aesthetics do not determine TinyStudio dataset pagination or search semantics.

### Folder

Source: https://www.affectui.com/screens/folder

One collection detail paired with Folders; folder identity drives heading/breadcrumb/files. Not deep nested trees. Preserve collection context across overview/detail and real row actions. Only shell-level props documented: no inferred loading, permission or pagination implementation.

### Integrations

Source: https://www.affectui.com/screens/integrations

Marketplace/settings overview of connected apps. Fewer than six apps should use a simpler list. The visual screen cannot supply OAuth, access policy or connection health. Shell API documents navigation and identity; actual error/loading and connection flows require separate implementation.

### Agent Chat

Source: https://www.affectui.com/screens/agent-chat

Main copilot conversation leading to a concrete draft/report/task, not lightweight support chat. Toolbar actions and tabs are labelled controls; full-height container documented. It is not a direct replacement for the globally available corner assistant. Real conversation persistence, tools and selected component state remain application responsibilities.

### CRM

Source: https://www.affectui.com/screens/crm

Editable spreadsheet-like record collection, not a read-only report. This distinction matters when comparing an interactive dataset editor with a run/evaluation inventory. Documentation offers shell API and labelled toolbar/tab actions, not full server collection semantics. Rendered table behavior remains to inspect.

### Settings & Billing

Source: https://www.affectui.com/screens/settings-billing

Workspace, Members, Billing and API keys are distinct tasks; public pricing and payment entry belong elsewhere. Documentation provides initial tab plus shell props, real invoice headers and named downloads. At1280×720, inspected Billing then clicked General, Members and API keys, verifying selected-tab and content changes. General uses three horizontally aligned label/help and control rows, fine separators, a white inset form and quiet footer containing Discard/Save. Members uses avatar/name/email as one identity unit, roles and row menus aligned right, with Invite on the heading row. API keys uses left name/date/last-use hierarchy, masked monospaced values aligned right and compact copy/revoke controls. Billing combines a wide two-column usage module, paired plan/payment cards, then lower plans/invoices requiring scroll. Do not import billing features or force all reference content into one viewport. Save, invite, role changes, key creation/copy/revoke, billing mutations, keyboard and narrow states were not exercised.

### Onboarding

Source: https://www.affectui.com/screens/onboarding

Skippable/resumable initial setup, not a single setting or a long required form (Stepper Form is the alternative). Each step is a form with Enter submit; current checklist step and progressbar carry semantics. Wide layout places checklist beside card, narrow below. defaultStep marks preceding steps complete in the demo; actual product progress must derive from persisted work, not that default. Completion callback collects values but does not prove tools connected or invitations sent.


## Agent activity and change contracts — 2026-10-29

Evidence: full public detail documentation for these eight references was retrieved and read, including use/non-use, API, keyboard, accessibility, motion and responsive sections. This advances documentation coverage only. Agent Monitor additionally received a Clean/light visual inspection at 1280×720; the remaining seven have no new visual or interaction evidence. No protected source was copied and no application implementation is claimed.

### Different operational questions need different surfaces

- [Agent Findings](https://www.affectui.com/components/agent-findings) answers what parallel checks have found: agents show current step and completed/total counts; newest-first findings carry severity, location and time. It is not a parent/child tree or historical timeline. Agent buttons filter the feed, with pressed state and Enter/Space activation; severity also needs text and the feed announces updates politely. The default feed shows four rows before bounded overflow. Below 480px counts/locations disappear, so a real detail action must preserve their availability. Default status inferred from completed counts must not override authoritative run status. TinyStudio candidate: ongoing quality/audit findings, only where such an event stream actually exists; not a replacement for the quality distribution report.
- [Agent Handoff](https://www.affectui.com/components/agent-handoff) explains a sequential relay and the context passed or lost. It is not parallel scheduling. Stable ordered agents, from/to handoffs, typed file/source/fact/decision/note items and explicit lost-item reasons are essential. Assignment is forward-only; restore and assign callbacks must execute actual operations rather than merely move cards. Keyboard pickup/assignment and double-tap alternatives supplement dragging. Cards bound task text to one line and handoff text to three, with column-edge fades; smaller layouts simplify status below 760px and become vertical below 620px. Candidate only for real delegated handoffs with retained provenance; do not invent context-loss evidence.
- [Agent Monitor](https://www.affectui.com/components/agent-monitor) keeps one agent's current step, time, cost and approval in view while another task remains primary. Full tool I/O belongs in Trace Waterfall; concurrent agents belong in Lanes/Tree; a simple thinking indication needs less UI. State explicitly distinguishes idle/running/waiting/paused/finished/failed/cancelled. Steps have stable IDs, queued/running/done/failed states and optional tool/detail/output. Start/end timestamps and paused duration determine the clock; cost is USD, not an arbitrary score. Completed steps occupy three rows before internal scrolling; rows and time bars open step details. Allow/decline, pause/resume/cancel, retry and full-log callbacks require real runtime support. Keyboard Enter/Escape approval shortcuts apply when the card has focus; an open detail dialog takes Escape first and returns focus to its row. The 340px maximum-width reference truncates long commands: any TinyStudio approval adaptation must provide the full command for inspection. **Observed:** the Clean/light preview uses a compact white card on a quiet tray; identity, cost and status share its header, current work has a lightly inset well, then a thin time strip, completed-step rows and a subordinate footer. Its demo automatically progressed from Running to Needs you without an action from this reviewer. That progression is not execution or approval proof. Folding, detail dialogs, dark mode, keyboard and narrow behavior remain unexercised.
- [Agent Inbox](https://www.affectui.com/components/agent-inbox) organizes a few dozen background jobs into needs-attention, running and done groups. Hundreds of jobs need a searchable/paged collection; a single approval or detailed plan needs another pattern. Waiting and failed jobs belong to Needs You. Preserve stable ordering within groups and distinguish active start time from finished elapsed duration. Without callbacks the example can update status and timers locally; real TinyStudio handlers must bind approval/rejection/retry to controlled server state. At 620px the three-column board becomes a list and its mode switch disappears; documented minimum width is 300px. Closed expanded content is inert; Escape returns focus and counts are politely announced. Candidate: task drawer or operational inbox, not a second global navigation system.

### Collaboration, history and capacity are separate contracts

- [Agent Cursors](https://www.affectui.com/components/agent-cursors) shows real concurrent document edits, suggestions and author identity. A single rewrite, file diff or delegation tree needs a simpler or different reference. Stable block IDs and offsets against current text govern cursors, selections, comments and suggestions; accepting a suggestion shifts dependent offsets. Accept/reject/all callbacks must modify the real document and resolve concurrent changes. Cursor pointers are decorative; actual content remains semantic and suggestion dialogs/follow controls keyboard reachable. Measured positions reflow; comment windows flip at edges, but cursor flags can clip at the right edge. Below 520px margins tighten and counts disappear. Bounded height and bottom fade require actual scroll testing. Do not fabricate collaborative activity to decorate the assistant.
- [Agent Ledger](https://www.affectui.com/components/agent-ledger) is an after-change audit with before/after values and undo dependencies, not preapproval or execution timing. Each entry identifies action, target, system, timestamp, change and dependencies. Reversibility defaults true in the reference; consumers must explicitly disable it where unsupported. Controlled undone IDs and undo/redo callbacks include reversible dependents, but do not establish atomic rollback. Focusing Undo reveals the dependency consequence. Rows keep time, flags and actions within a fixed-height arrangement; below 480px numbers move, system text hides and icon actions remain available on touch. Candidate: reversible assistant edits backed by real inverse operations, never a promise that every model or server action can be undone.
- [Agent Queue](https://www.affectui.com/components/agent-queue) shows backlog, concurrency, throughput and backpressure. It is not a trace, manual assignment board or simple result inbox. Jobs retain enqueue/start/end times, worker, actual progress or labelled estimate, attempt limits and errors, all against a common clock. Retry keeps the same job ID, increments its attempt and clears the prior worker/start fields. Pause, promote, cancel and concurrency changes require backend support; the reference's 1–8 default range is not a server capability. Its two-minute history chart requires retained finished jobs or explicit totals; a loaded slice cannot be represented as complete history. Roving focus, arrow/Home/End navigation, pinned detail, Escape return and a text chart summary are documented. The queue/worker/result arrangement stacks below 600px and controls reorganize below 440px. Candidate only where actual scheduling capacity and queue actions are exposed.
- [Agent Tree](https://www.affectui.com/components/agent-tree) explains orchestrator/child ownership, branch cost and blocked/failed branches. It is not chronological history, a single-agent plan or manual assignment. Stable hierarchical IDs and explicit queued/running/waiting/done/failed/stopped states are required. Each node supplies its own cost; branch totals add descendants, so pre-summed costs would double-count. Unlike Queue, a failed retry is represented as a new child ID while the failed attempt remains: the shared UI must not force both references into one retry identity contract. Fold state is controlled, and approve/reject/retry/dismiss actions must update real execution state. Tree semantics, expanded state, focus-managed dialogs and live totals are integral. First-level branches sit alongside one another above 560px; narrow views indent them. Deep-tree fit remains untested. Candidate only for actual hierarchical execution evidence.

These distinctions constrain later selection across the full interface; they neither add unsupported product capabilities nor declare the existing assistant/Laboratory compositions acceptable.


## Complete remaining catalog documentation review — 2026-10-29

Evidence boundary: parent retrieved and read the complete public documentation bodies for the following32components,24illustrations and8motion patterns, including available API, keyboard, accessibility, responsive and motion sections. These are documentation-described compositions, not newly observed renders, exercised controls, source-adoption permission or acceptance. Pro installation/usage remains inaccessible without the appropriate license. Some extracts are older than the live catalog; verify selected patterns live before implementation. The delegated illustration reader failed to access the site and contributed no substantive findings; parent retrieval succeeded separately.

Coverage correction: the prior reported65read entries included the evidence-definition table row. The actual prior catalog count was64. This increment adds64, bringing all128catalog items to documentation-read or higher; visual/interaction review and final selection remain incomplete.

### Remaining component contracts

- **[AI Answer](https://www.affectui.com/components/ai-answer)** — Short sourced assistant answers, with streaming and follow-ups; not casual chat or long code/documents. Sources precede the answer; citations connect to previews and source cards. Answer syntax uses one-based source indices, so stable source ordering is essential. Stop must cancel the real request; regenerate must supply a new answer rather than replay an animation. Busy state defers screen-reader reading until completion; copy/feedback changes are polite. Source chips work with focus and Escape, and touch opens previews. Container maximum 640px; sources scroll sideways when narrow. Reduced motion reveals text immediately. Candidate for sourced assistant responses, not every message.
- **[Thinking State](https://www.affectui.com/components/thinking-state)** — Collapsible trail of real searching/reading/drafting activity for longer waits; not subsecond work or measurable progress. Header remains while its timeline and source chips collapse; excess sources use +N. The documented stepMs/onDone/loop API plays a timed sequence: animation completion cannot establish execution completion. Header and source controls support keyboard, with expanded state and polite updates. About 300px minimum, tighter narrow padding, larger touch targets; reduced motion removes staged animation. Candidate only with an adaptation driven by actual events, not fabricated reasoning.
- **[Writing Assistant](https://www.affectui.com/components/writing-assistant)** — Compact rewrite/draft helper, maximum 380px; not a long conversation with tools, files and model settings. Suggestions submit messages; field grows to four lines before scrolling. Enter sends and Shift+Enter adds a line; new messages announce politely, mic is a labelled toggle. Default respond uses a sample writer and speech may simulate a take when recognition is unavailable. Neither is acceptable as a working TinyStudio response or dictation fallback. Optional voice requires the actual service and consent; hide unsupported actions. Reduced motion stops the glow and shows replies immediately. Retrieved page footer is older than the current catalog, so live behavior needs rechecking.
- **[Thinking Loader](https://www.affectui.com/components/thinking-loader)** — One short inline phase label for prompt-to-first-token or real pipeline events, not page loading or a detailed source trail. Plain/pill variants are 26/32px tall. Controlled phase disables its timer; default timed phases and elapsed-since-mount text cannot imply real tool activity or compute duration. Use real events, then replace with the streamed response. Status is announced once; decorative spinner/duplicate labels are hidden. Two-to-four-word labels stay on one line. Reduced motion stops spinner/shimmer and switches labels immediately; avoid flashing for very short waits.
- **[OTP Input](https://www.affectui.com/components/otp-input)** — Short visible verification codes, not hidden passwords/PINs or long free text. One real input backs all visual boxes, supporting paste, autofill, numeric keyboard, arrows/Home/End and replacement. Controlled idle/verifying/error/success state must follow the authentication response; completion only admits a verification request. Default clearOnError and resend countdown need reconciliation with actual retry/rate-limit rules. Errors use invalid/describedby and live feedback; verifying locks entry. Six boxes fit 320px and underlying 16px input avoids mobile zoom. Reduced motion makes shake/morph immediate. No new authentication flow is implied.
- **[Usage Meter](https://www.affectui.com/components/usage-meter)** — Current-period credits/tokens/minutes with additive category usage and subordinate limits; not long-term trends or many thin categories. Use roughly five categories or aggregate Other with disclosure. Total is capacity; null limits mean unlimited, not unknown. Period changes must supply matching usage/limits/daily series; future daily nulls differ from zero. Reset time, units and real limits must be authoritative. Meters have text values; arrows traverse periods/categories/days, Escape dismisses readouts, thresholds are announced beyond color. Maximum 460px, stacked controls below 400px, works at320px; reduced motion stops count/width animation. No upgrade/top-up action without an actual billing capability.
- **[Cited Answer](https://www.affectui.com/components/cited-answer)** — Finished retrieval-backed claim checking, distinct from streaming AI Answer and casual chat. Segments cite stable source IDs; source order supplies numbers. The component does not retrieve pages: quote must be the actual supporting passage. Unknown citation IDs are silently skipped by the reference, so TinyStudio must validate rather than conceal missing provenance. Chips open a bounded inline preview; +N holds overflow. Keyboard traverses markers/chips and Escape returns focus. Closed preview is inert, focus mirrors hover. Maximum460/minimum300px, touch targets grow; reduced motion removes crossfades. Do not treat automatic shared-word highlighting as evidence of entailment.
- **[Delegate Board](https://www.affectui.com/components/delegate-board)** — Assignment and capacity balancing for actual agents, not live chronological activity or approval. Unassigned tasks and agent queues show minutes, rates and expected availability; >3 queued chips collapse into +N. onAssign and controlled mode must invoke actual scheduling. The uncontrolled Auto demo assigns every second to the least-busy agent; do not adopt that as a qualified executor. Estimates/rates are not measured durations/costs. Enter/arrows/number shortcuts and Escape provide alternatives to drag; handoffs announce politely. Maximum720px; below560px the task pile moves above agents; reduced motion removes glides. No current scheduler capability inferred.
- **[Integration Picker](https://www.affectui.com/components/integration-picker)** — Search-and-connect step inside a settings dialog or command menu; a full marketplace needs a page, two or three choices a simple list. Connected/available list updates an adjacent detail area; controlled callbacks must perform actual connection/authentication and reflect failures. Default sample services are not supported integrations. Combobox/list active option and labelled switch, arrows, Enter, search-clear then Escape close, and back from empty search are documented. Below560px list stacks above details; below400px capabilities hide and need another accessible route. Reduced motion stops tile/ring animation. Candidate for moderately sized service selection only.
- **[Integration Settings](https://www.affectui.com/components/integration-settings)** — One connected service with explicit save/discard, not an entire long settings page. Header identity/toggle precedes key/address/time-zone fields; changing values exposes a save row and disabling folds settings away. defaultValues is initial state, not proof of later synchronization; save/toggle timing must match TinyStudio semantics. Without onGenerateKey a random demo key appears: never use it as a credential. Address invalid state links to hint; key creation announces copy reminder; Escape closes time-zone menu. Reduced motion removes folding. Docs give dark-mode behavior but no detailed narrow-layout contract, which requires live inspection.
- **[KPI Chart](https://www.affectui.com/components/kpi-chart)** — Headline metric plus short-period series, roughly <=6 lines; hundreds of points require zoom/sampling. Explicit formatted value, labels, series and delta are caller-owned. Reference colors positive delta green and negative red, which is wrong for loss/latency unless adapted. Default empty treatment includes all-zero non-percent series: TinyStudio must distinguish measured zero from missing data. Chart is focusable, arrows inspect points and Escape closes tooltip; live tooltip announces values. Maximum460px, touch scrubbing, reduced-motion line/tooltip changes. Not a general scientific plot replacement.
- **[Memory Panel](https://www.affectui.com/components/memory-panel)** — Persistent assistant memories people can correct/pause/forget, not current-conversation context or general account settings. Categories/search bound a scrolling360px list with contextual edit actions and footer Undo. Controlled onChange must persist real state; local forget/restore and enabled switch do not establish backend deletion or personalization disablement. Undo defaults5s and requires real restoration semantics. Enter saves, Shift+Enter newline, Escape cancels; labelled actions, radio chips and inert filtered rows preserve access. Maximum380/minimum300px, touch actions always visible; category counts disappear below340px. No explicit motion section was supplied, so reduced-motion behavior remains unverified.
- **[Message Actions](https://www.affectui.com/components/message-actions)** — Compact actions under completed assistant messages, not user messages or streaming output. Copy/regenerate/feedback/speak plus version arrows belong to the specific message and version. Feedback may fire twice for down+reason: upsert rather than duplicate. Read-aloud animation is timed without controlled speaking/onSpeak and does not produce speech. New versions, actual TTS and cancellation must be connected. Quiet actions become visible on focus/touch; toggles report pressed state, Escape closes reason form and returns focus, version ends disable arrows. About220px with switcher; reasons wrap, Send moves below320px. Reduced motion leaves static bars.
- **[Model Picker](https://www.affectui.com/components/model-picker)** — A few models in composer toolbar with short tradeoffs and two or three settings; large catalogs need searchable Combobox/Command Menu. Menu320px, bounded by viewport minus32px, opens above a bottom composer and must escape clipping. Controlled model/thinking/effort/search values must match available backend capabilities. Fixed latency multipliers by effort are illustrative estimates, not provider measurements; do not display as facts. Listbox arrows/Home/End, Enter select, Tab settings and Escape focus return; closed menu inert. Touch rows enlarge; reduced motion removes rolling metrics and fills. No invented quality/cost scores.
- **[Parallel Attempts](https://www.affectui.com/components/parallel-attempts)** — Comparable best-of-N attempts for one task, not fan-out jobs or one trace. Stable rows show approach, status, progress and comparable score; failed attempt retains stopped progress. Default selects highest finished score automatically: use explicit controlled picked state when human acceptance is required. Scores must share an evaluation and direction, and progress must be measured or qualified. Finished choices are radio controls with names/scores; bars are decorative. Wide name/bar/score share a row; below560px bar moves below and approach hides. Reduced motion removes transitions. No automatic deployment of a winner.
- **[Plan vs Actual](https://www.affectui.com/components/plan-vs-actual)** — Dependency-aware expected versus actual run timing, not edit-plan UI, cost attribution or concurrent-agent lanes. Ordered steps carry planned duration and dependencies; actual start/end/now are milliseconds relative to the same run origin. No deps means planned start0. Missing end is running, missing start/end waiting; unknown estimates must not be invented. Rows keep height; focus reveals dependency links and accessible text carries time/late/early independent of color. Under560px names narrow and alternate axis labels/key/icons drop. Reduced motion stops glides. Candidate only when real plan estimates and dependencies exist.
- **[Project Card](https://www.affectui.com/components/project-card)** — Quick summary plus small inspection/edit, not long forms or growing rows in dense tables. Header summarizes title/progress/status/due; expansion reveals <=about12 subtasks, members and an action. Hover growth needs explicit space or overlay; not a generic records template. Controlled task/status callbacks must persist changes, assignee-name matching needs stable adapter identity. Enter/Space opens; tasks checkbox, people toggles, status radio; Escape closes inner options first and collapsed content is inert. Max320px, tightens below290px; touch taps open with visible assignees, reduced motion immediate. Persistent details need a static view.
- **[Prompt Library](https://www.affectui.com/components/prompt-library)** — Reusable small variable templates alongside a composer; not commands or freewriting. More than about6 variables need a conventional form. Search/list and variable preview preserve values per template; onUse emits resolved text/template/values only after required fields fill. Enter advances missing fields then uses; use stays focusable with aria-disabled and missing-count explanation. Selected option, labelled fields, polite feedback and inert leaving preview matter. Max640px, list above below540px, copy icon below360px. Token count is four-characters-per-token estimate; exact limits require tokenizer. Reduced motion avoids crossfades.
- **[Tips Panel](https://www.affectui.com/components/tips-panel)** — Independent feature discovery or what-is-new tips; not dependent steps or one announcement. Multi-column list of short actions/done states, with optional close; action completion needs actual success rather than a local celebratory state. Three/two/one columns at panel560/380px and default420px max-height with scrolling. Real buttons, keyboard activation, list semantics and polite counter/status. Reduced motion removes stagger/pop. Candidate for optional contextual help, not occupying the primary data workspace.
- **[Tool Call Card](https://www.affectui.com/components/tool-call-card)** — Real tool-call arguments/results in assistant or developer run views; ordinary end users often need a one-line summary plus optional log. Group calls by assistant turn so cards do not bury the answer. Map call events1:1 and use actual timestamps/duration; redact secrets before props. Group duration summing is not elapsed wall time for overlapping calls. Closed bodies inert, labelled expanded buttons, live status, copy labels and visible error text. maxCodeHeight220 exposes Show all but still renders the whole payload: truncate/page large data upstream. Error auto-open respects manual toggling only in uncontrolled mode. About320px minimum, wrapped code, reduced-motion static.
- **[Breakdown Chart](https://www.affectui.com/components/breakdown-chart)** — Categorical distribution from records, not time trends. Pick measure/slice/segment; field absent counts records, supplied field sums it. About12bars/8segments maximum; do not sum rates or imply a loaded sample is the full population. Bars/legend link to a numeric table, giving both keyboard focus and non-color evidence; pickers support arrows/Escape. Stable dimension order and real units are required. Reduced motion stops restacking; detailed narrow behavior beyond dark mode is undocumented. Candidate for dataset quality counts, with explicit scope and accurate bin semantics.
- **[Button](https://www.affectui.com/components/button)** — Actions use native buttons; route navigation uses links, selected views use tabs/segments. Primary dark, secondary hairline, ghost and destructive have distinct roles; documented24/28/32px desktop heights are source proportions, not blanket touch sizes. Loading keeps label readable and focusable with aria-busy while ignoring clicks, requiring duplicate-request protection in controller. Icon-only needs a name; Enter/Space activate. Reduced motion stops sweep and width/content animation. Existing public adaptation must be reconciled rather than duplicated.
- **[Reaction Bar](https://www.affectui.com/components/reaction-bar)** — Public-count feedback toggles, not confirmation or consequential operations. Starting count/active state and onReact describe a local toggle; persistence and failure reconciliation remain caller-owned. Buttons expose count and pressed state; animated digits are decorative, keyboard Enter/Space works. Reduced motion removes icon/count motion; no detailed narrow contract supplied. Do not substitute generic likes for scientific scoring or permission decisions.
- **[Smart Recommendation](https://www.affectui.com/components/smart-recommendation)** — Reasoned preferred next step with alternatives, not choices with no justified favorite. First option is recommended; signal0–3 needs an evidence definition, not fabricated confidence. Expandable reason and radio-menu alternatives keep choice contextual. Accept/undo callbacks require actual operations and reversibility. Heading expanded state, verbal signal, Enter/Space and Escape menu dismissal; reduced motion stops reason swaps. Detailed narrow layout absent. Candidate only with explainable recommendations and preserved user agency.
- **[Billing Schedule](https://www.affectui.com/components/billing-schedule)** — Fixed-price one-off/recurring line scheduling, not usage-tier pricing or simple plan selection. Phase/date/duration and product/discount/minimum lines emit a full schedule. Financial arithmetic/persistence are not proved by a visual table. Labelled line controls, table headers/listbox menus, polite totals; Enter confirms price/Escape closes. Max560px; fields stack and Repeats hides on narrow screens, requiring retained discoverability. Reduced motion removes inserts/morphs. No TinyStudio billing scope introduced.
- **[Change Summary](https://www.affectui.com/components/change-summary)** — Short narrative metric recap beside authoritative charts, not exact historical comparison or >about6metrics. Description-list rows pair metric with change; tone good/bad/neutral is separate from arrow direction, appropriate when decreasing loss is good. Caller supplies periods and justified values. Decorative arrow duplicates text; reduced motion removes row stagger. Detailed responsive behavior is not specified beyond dark mode. Candidate for concise comparison summary, not chart replacement.
- **[Comments Panel](https://www.affectui.com/components/comments-panel)** — Object-bound comment sidebar; long nested discussion belongs in Comment Thread. Tabs/sort separate comments, bottom composer adds them; /edit text makes a runnable edit. Comments are untrusted content: text prefix alone must not grant action permission. onRunEdit promise settlement must reflect actual result, with errors retained. Filters are tabs, sorting radio menu, resolve toggle, semantic times/live additions. Keyboard traversal and Enter submit; reduced motion removes insertion/spinner/menu animation. Narrow layout requires visual proof.
- **[Email Draft](https://www.affectui.com/components/email-draft)** — Review of an agent-written outbound message, not long authoring or unreviewed automatic send. Record identity, recipient, tone, editable body and attachment precede Send/Discard. First contact is preselected; require real recipient/attachment and actual delivery outcome. Displayed Sent/Undo cannot promise recall after external delivery. Escape leaves editing/menu; command+Enter sends while editing. Recipient listbox, tone pressed states, labelled editor/status. Max520px; narrow hides address/shortcut, so recipient remains inspectable. No motion section supplied. No new messaging scope authorized by adopting the card.
- **[Progress Tracker](https://www.affectui.com/components/progress-tracker)** — Short independently completable onboarding list, not sequential prerequisites or >about8steps. Thin progress bar, stable one-line rows and count; labels truncate. Steps expose checkboxes and bar count. onChange must persist actual completion; autoPlay ticks and resets as a demo and must be off in operational UI. Any click/key stops autoplay. Enter/Space toggle; reduced motion removes bar/ring/count animation. Not a scientific execution progress indicator.
- **[Comment Thread](https://www.affectui.com/components/comment-thread)** — One conversation tied to a cell/selection/record, not a feed or global assistant shell. Messages carry author/time/text; assistant status lines disclose details; composer Enter submits and onReply may return actual async reply. Preserve anchor/object identity across navigation and reconcile persistence/failure, not just append local text. Expanded buttons, inert hidden details, polite messages and keyboard traversal. Reduced motion removes fades/shimmer/folding; detailed narrow behavior is not documented.
- **[Inline Diff](https://www.affectui.com/components/inline-diff)** — Word-level finished prose edits requiring explicit decisions, not code diffs, live typing or near-total rewrites. Pending changes retain original words; onChange fires each decision/undo and onDone final result, so draft updates must not prematurely publish. Toolbar navigates pending changes; Y/Enter accepts, N/Backspace rejects, Escape exits. Focus moves to next change or Undo; hidden toolbar inert and decisions announced. Color supplemented by strike/add semantics. Popover stays inside card at320px, arrows disappear below400px, touch controls grow. Retrieved accessibility markup loses some HTML tags; exact element semantics and reduced-motion implementation need source/live verification.
- **[Setup Guide](https://www.affectui.com/components/setup-guide)** — Grouped sequential onboarding with prerequisite unlocks, not unordered tasks or one short checklist. Foldable corner panel, group headers, progress and completed task states; actual prerequisite completion must own onChange state. Keyboard Tab/Enter/Space, expanded groups, inert closed panels and disabled locked tasks. Completion folds group/opens next; reduced motion removes movement. Detailed narrow sizing absent. Not a reason to force every Laboratory editor into a wizard.

### Motion contracts

- **[Blur Crossfade](https://www.affectui.com/motion/blur-crossfade)** — Local status/label/panel swap, not route transitions or rapid live values. ID triggers a420ms default swap; height/both/none morph. Outgoing copy inert/aria-hidden; external live region only where meaningful. Reduced motion instant; clipping occurs during swap, so focus/shadow behavior requires verification. Candidate for brief state continuity, never delaying a critical result.
- **[Gradient Floor](https://www.affectui.com/motion/gradient-floor)** — One optional hero/prompt backdrop, not long body copy or repeated backgrounds. Two blurred color/light layers anchored to bottom, explicit container height; speed0 static. Decorative layers hidden, text must remain above bright area with contrast. Animation pauses offscreen and reduced-motion stops it. Avoid in dense operational surfaces.
- **[Gradient Text](https://www.affectui.com/motion/gradient-text)** — Rare short brand heading accent, not body text or multiple competing highlights. Real selectable text with hidden gradient duplicate; color contrast remains caller responsibility. Inherits typography, one entry sweep plus slow blobs, reduced-motion static. Not a functional status indicator.
- **[Grid Background](https://www.affectui.com/motion/grid-background)** — Quiet technical header/empty-state backdrop, explicitly not dense text/tables or several moving backgrounds. Decorative pointer-transparent layer under interactive content; cells48px by default, speed0 static, edge fade follows container. Transform-only drift and once-visible reveal stop under reduced motion. A chart grid must instead represent actual axes; this is decoration.
- **[Number Ticker](https://www.affectui.com/motion/number-ticker)** — Occasionally changing headline counts/prices, not dense numeric tables or high-frequency updates. Intl formatting/locale and actual value caller-owned; tabular digits inherit typography. Animated columns hidden while final formatted value announces once. Default900ms count-in from0 must not be read as data history; reduced-motion immediate. Candidate only when it improves change recognition without obscuring exact values.
- **[Shimmer Loader](https://www.affectui.com/motion/shimmer-loader)** — Content-shaped placeholder or genuine waiting label, not sub300ms flashes or measurable progress. Skeleton dimensions should match arriving layout; skeletons aria-hidden and surrounding region busy, text remains readable. Shared viewport sweep avoids independent noise; reduced-motion static. Prevent collapsing/resizing the collection during loading.
- **[Text Reveal](https://www.affectui.com/motion/text-reveal)** — Short optional introductory headlines, not urgent instructions, long passages or live data. Semantic wrapper plus full hidden sentence; animated words preserve wrapping and are hidden from assistive tech. Once-visible stagger/default700ms pieces; reduced-motion immediate. No use for table rows, form labels or results users need immediately.
- **[Tilt Card](https://www.affectui.com/motion/tilt-card)** — Single showcase object, not card grids or control/text-heavy surfaces. Decorative glare adds no tab stops; focus has a small lift. Pointer rAF transform/opacity, touch no tilt, reduced-motion no tilt/glare. Avoid moving operational controls beneath the pointer.

### Illustration contracts

- **[Cover Flow](https://www.affectui.com/illustrations/cover-flow)** — 6–20 visual items for sequential inspection, not text-heavy comparison or large libraries. Center cover/caption with receding side covers; controlled index and onSelect distinct. Arrows/Home/End navigate, Enter selects; slides labelled, off-center and reflections hidden, caption polite. Horizontal drag/flick keeps vertical page scrolling. Scales only to half size; reduced motion jumps. Candidate for visual artifact browsing only after checking side-by-side needs.
- **[Frost Cloud](https://www.affectui.com/illustrations/frost-cloud)** — Frosted translucent mascot for assistant introduction/empty state, not a functional control or <48px icon. Blue/orange palette may conflict with product identity. Pointer-follow/blink/float and click bounce are decorative, one labelled image. Square/centered cloud<=320px, dark sky variant; offscreen pauses, reduced-motion still except click reactions. Not a status or recording indicator.
- **[Glow Cloud](https://www.affectui.com/illustrations/glow-cloud)** — Bright colored body/white glow counterpart to Frost Cloud, same mascot use and <48px limitation. Labelled image; click only animates. Blue/orange brand dependence, pointer/blink/float, offscreen pause and reduced-motion still except click. Square or explicit-height sky, cloud<=320px. No execution state inferred from movement.
- **[Glow Keyboard](https://www.affectui.com/illustrations/glow-keyboard)** — Interactive promotional scene, explicitly not real Chat Composer. Demo types prompts and invokes onSubmit just like a user: never wire default autoplay to actual model calls. Focus stops demo; real labelled field, drawn keyboard hidden. 11:6 scaling makes legends tiny below360px. Onscreen-only animation, reduced-motion stops demo but allows typing. No operational consumer selected.
- **[Integration Beam](https://www.affectui.com/illustrations/integration-beam)** — Illustrated hub between <=4tools per side, not saved integration settings. Toggle tiles pause/reconnect only the scene; callbacks for real connection absent. Labelled group/buttons/live connected count are demo state. Focus/Enter/Space mirror pointer, beams hidden. Measured layout switches to vertical below460px. No explicit motion/reduced-motion section retrieved; verify before any decorative use. Must not imply actual connected services.
- **[Logo Cluster](https://www.affectui.com/illustrations/logo-cluster)** — Decorative ecosystem bubbles around brand, not readable/clickable integration list; supplied logos are examples. One labelled image, inner bubbles hidden; square up to420px and dark variant. Documentation supplies no motion/reduced-motion contract. Candidate only for truthful optional explanation, not supported-service inventory.
- **[Model Wheel](https://www.affectui.com/illustrations/model-wheel)** — Provider-choice feature illustration, not an actual model picker: spinning reports no selection. Cylinder shows about7rows, at least7models keep it full. Image label hides rows from assistive tech; hover pauses, touch/manual spin remains. Fixed7row height with container width; offscreen pause, reduced-motion disables autoplay but manual spin persists. Brand marks require appropriate use. No operational selection control.
- **[Project Flow](https://www.affectui.com/illustrations/project-flow)** — Decorative task/client/role workflow scene, explicitly not working components. Labelled image with hidden internals; fixed2312×1644 artboard scales without reflow, text poor below320px. Theme explicit and edge fade must match background. No detailed motion/reduced-motion section supplied. Cannot serve as a real execution topology.
- **[DACH Region](https://www.affectui.com/illustrations/region-dach)** — Simplified regional service-area illustration, not exact geography or data map. Controlled region/menu covers six named regions; current label follows selection. Listbox arrows/Home/End/Enter, Escape focus return, Tab dismissal. Transparent540:440 scale, menu shrinks to60%, embedded maps~155KB. Reduced-motion stops border loop/morph. No geography requirement established for TinyStudio.
- **[Empty Calendar](https://www.affectui.com/illustrations/empty-calendar)** — Decorative empty-pipeline/meeting illustration, not scheduling UI. Stacked placeholder cards; one labelled image, hover is decoration. 3:2 container scaling, transparent/light-dark. Reduced-motion stops shimmer/glow and makes hover immediate. No calendar or scheduling capability implied.
- **[Integration Flow](https://www.affectui.com/illustrations/integration-flow)** — Promotional incoming-tool flow, not connected integration pills. At least5items recommended; fixed2400×1490stage scales without reflow, text weak below320px. Single labelled image, internals hidden; background/fade configurable despite warmcoral default. No detailed reduced-motion contract supplied. Real connector inventory must remain functional and truthful.
- **[Voice Orb](https://www.affectui.com/illustrations/voice-orb)** — Voice-mode phase visualization, not tiny inline status or static decoration. Controlled idle/listening/thinking/speaking and real0–1level required: missing level generates synthetic movement. Canvas decorative with live phase label; onPress makes named keyboard button. Size default220, <96loses detail; pauses offscreen/background, reduced-motion distinct static frames. Candidate only for actual voice workflow, not microphone evidence from animation.
- **[Tab Overload](https://www.affectui.com/illustrations/tab-overload)** — Pain-point illustration about context switching, not actual tabs/browser content. Placeholder windows, masked right/bottom edges; decorative hover highlight, one labelled image. 3:2 scaling and dark mode; reduced-motion stops shimmer and switches highlight immediately. Older :has support fallback keeps front highlighted. Do not recreate redundant navigation using this visual.
- **[World Map Continental](https://www.affectui.com/illustrations/world-map)** — Continent focus illustration, not country values or precise borders. Controlled highlighted continent with accessible menu/label; listbox keyboard and focus return. Simplified merged Natural Earth shapes,540:242scale; menu may extend below small map. Reduced-motion static highlight. No scientific geospatial chart use.
- **[Ghosted Chat](https://www.affectui.com/illustrations/ghosted-chat)** — Fixed unanswered-outreach scene, explicitly not real chat; text poor below220px. Image label hides bubbles; hover adds a decorative thumbs-down, not user feedback. Fixed360×240artboard scales without reflow. Reduced-motion stops shine and makes hover instant, dark mode provided. No assistant conversation template use.
- **[Lead Sourcing](https://www.affectui.com/illustrations/lead-sourcing)** — Illustration of collecting from a few sources; logo examples must not imply unsupported integrations. Labelled image hides connected tiles; colored line shine, reduced-motion staticgrayline. Transparent3:2scale/light-dark. No live ingestion-status use.
- **[Enrichment Orbit](https://www.affectui.com/illustrations/enrichment-orbit)** — Decorative enrichment/sync concept, not measured progress and unsuitable near concentration-heavy content. Two counter-rotating comets and three orbiting nodes, labelled image with hidden internals. Transparent3:2scale/light-dark; reduced-motion freezes staggered nodes. No fabricated model/data activity.
- **[Outreach Branches](https://www.affectui.com/illustrations/outreach-branches)** — Illustrated one-to-many messaging, not actual delivery evidence; replace unsupported logos. Labelled image hides lines/logos, three staggered pulses. Transparent3:2scale/light-dark; reduced-motion hides pulses leavinggraylines. Does not authorize outbound messaging.
- **[Glass Folder](https://www.affectui.com/illustrations/glass-folder)** — Decorative documents/knowledge-base empty-state candidate, not real files. Frosted pocket and placeholder documents respond to hover; one image label, no keyboard task. Transparent3:2scale but explicitly designed for light surfaces, so unsuitable as-is for required dark theme. Reduced-motion instant hover.
- **[Side Folder](https://www.affectui.com/illustrations/side-folder)** — Sideways-folder alternative for document empty states, not real files. Pocket hinges and pages slide right on hover; image label hides internals. Transparent3:2scale, light-only design; reduced-motion instant. Compare with Glass Folder visually before selection and supply a suitable dark treatment.
- **[Team Invite](https://www.affectui.com/illustrations/team-invite)** — Decorative three-avatar invitation scene, not real team roster or invitation status. Two existing/one invited avatars required; label derives names, internals hidden, nothing interactive. 4.4s simulated drag/join loop; reduced-motion freezes waiting state. Transparent3:2scale/light-dark. Real user creation needs a form and actual server success.
- **[Layer Stack](https://www.affectui.com/illustrations/layer-stack)** — Explains <=5platform layers, not application navigation. Vertical tablist selects decorative layer separation; arrows and one tab stop, stack hidden. Autoplay pauses hover/focus and stops after choice; reduced-motion no autoplay. 3:2container scaling may shrink text. Candidate only for optional explanation; do not replace real section navigation.
- **[Agent Team](https://www.affectui.com/illustrations/agent-team)** — Exactly3agent role feature illustration, not live agent status/orgchart. Prompt/types/send animation and appearing agents are simulated. Required three members, limited tool marks; labelled image, allinternals hidden and noninteractive. 3:2scale/light-dark; reduced-motion shows final team. No execution graph use.
- **[Integration Grid](https://www.affectui.com/illustrations/integration-grid)** — Decorative ecosystem of <=22logos, not clickable integrations. 5/7/9columns, central productmark, excesslogos discarded. Singleimage label lists integrations, tiles hidden. Wide1520:560scale, fadededgefills; hover lift removed under reduced-motion, darkmode. Only truthful supported relationships may be advertised.

Selection boundary: the whole inventory has now been read at documentation level, but no wholesale adoption is implied. Decorative examples must not masquerade as controls or evidence. Final choices require actual render comparison and functional fit across all route families, with licensed/public source provenance retained.
