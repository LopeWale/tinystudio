# React and BoardUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement task by task. User execution override: work in parallel in this session and checkout; never create a worktree, change branches, or recursively launch agents. The parent coordinates review and integration.

**Goal:** Deliver the React/BoardUI migration, interactive data exploration, reusable uploads and assistant presentation with existing functionality preserved and live acceptance.

**Architecture:** React pages compose a typed workspace/API boundary and focused lifecycle controllers. BoardUI supplies reusable presentation; real transfers and backend events supply activity. FastAPI serves packaged Vite output on the existing origin.

**Tech Stack:** React 19, TypeScript, Vite, Tailwind CSS 4, BoardUI, React Router, Vitest/Testing Library; existing FastAPI/PostgreSQL/R2 services.

**Spec:** `docs/superpowers/specs/2026-10-26-react-boardui-design.md`

## Global Constraints

- Same checkout and current branch throughout; no worktrees, branch changes, Git writes, commits or pushes by workers.
- Preserve all existing dirty source and unrelated dataset-quality work. HEAD was observed advancing externally to `fc39c64`; recheck before integrating.
- Never use advisor. Readers use Agent Team's restricted launcher; workers do not recursively delegate.
- User directed execution immediately and in parallel after choosing React; do not add another design/plan approval pause.
- The user selected free BoardUI plus custom versions of the Pro patterns. Use installed free source with its license and identify custom patterns accurately; no paid source or activation.
- Credentials remain server-side; preserve CSRF, account/project scope, optimistic conversation revisions, untrusted context and destructive gates.
- All 21 existing route families and global auth/setup/task/assistant states require React implementation and verification.
- Ten-hour execution window begins 2026-10-26 17:39 UTC; target completion before 2026-10-27 03:39 UTC. This is a planning deadline, not evidence of completion.

## Review Focus

- Account/project changes during any pending request must reject old results; test delayed reads, saves and streams.
- React Strict Mode must not duplicate timers, model requests, channels or transfers; test mount/unmount cleanup.
- A transferred file may still fail import/indexing; test explicit phase separation and retry/cancel semantics.
- Partial/undefined scientific data must keep accurate denominators and selection; test independently known fixtures.
- A wheel may omit hashed chunks even when local development works; test the production asset graph and packaged bytes.

## Shared Interfaces (freeze before feature integration)

Foundation owns `frontend/src/app/StudioContext.tsx`, exporting:

```ts
interface ApiClient {
  get<T = any>(path: string, signal?: AbortSignal): Promise<T>;
  post<T = any>(path: string, body?: unknown, signal?: AbortSignal): Promise<T>;
  put<T = any>(path: string, body?: unknown, signal?: AbortSignal): Promise<T>;
  patch<T = any>(path: string, body?: unknown, signal?: AbortSignal): Promise<T>;
  del<T = any>(path: string, signal?: AbortSignal): Promise<T>;
  upload<T = any>(path: string, body: FormData | File, options?: {
    method?: string; signal?: AbortSignal;
    onProgress?: (value: {loaded: number; total?: number}) => void;
    headers?: Record<string, string>; external?: boolean;
  }): Promise<T>;
}
interface StudioRuntime {
  api: ApiClient; project: string; user: any; overview: any;
  refreshOverview(): Promise<void>; navigate(hash: string): void;
  toast(message: string, error?: boolean): void;
  confirm(message: string, title?: string): Promise<boolean>;
  trackTask(task: any): void; showTasks(): void;
}
function useStudio(): StudioRuntime;
function useResource<T = any>(path: string | null): {
  data: T | undefined; loading: boolean; error: Error | null; reload(): void;
};
```

Resource reads automatically own route/account/project cancellation and record
only current rendered evidence. API mutations carry the same scope checks.
Additional fields may be added compatibly; coordinate changes with the parent.

Foundation owns `frontend/src/components/studio/primitives.tsx`:
`Page`, `Panel`, `Button`, `Field`, `EmptyState`, `Badge`, `Modal`, `Evidence`.
All accept `children` and `className`. `Page` accepts `title`, optional `description`
and `actions`. `Panel` accepts optional `title`/`actions`. `Button` accepts standard
button props plus `variant: primary|secondary|danger|ghost`, `size: sm|md` and
`loading`. `Field` accepts `label`, optional `hint`/`error`. `Modal` accepts
`open`, `title`, `onClose`, optional `footer`/`wide`. `Evidence` accepts `label`,
`data`, optional `as` (including `tr` or `svg`) and forwards element props.
Plain form/table elements may use shared semantic styles until wrappers exist;
do not create another cross-feature primitive library.

Assistant/upload worker owns `frontend/src/components/studio/uploads.tsx`:
`FileUploadField({label, accept, multiple, files: File[], onChange,
maxBytes?, disabled?})` and `UploadStatus({phase, progress?, error?, onCancel?,
onRetry?})`. Data worker imports this contract and does not implement a duplicate.

Feature route components read parameters with React Router. Data exports from
`frontend/src/features/data/index.ts`: `DatasetsPage`, `DatasetPage`,
`RepositoriesPage`, `FinetuningPage`, `JobPage`, `EvaluationsPage`,
`EvaluationPage`, `MetricsPage`. Assistant exports `AssistantPage`,
`AssistantWidget`. Parent owns interpretation and workspace feature exports.

---

### Task 1: Foundation, actual BoardUI and scoped application lifecycle

**Owner:** foundation worker. **Files:** `frontend/package*.json`, TypeScript/Vite/Vitest config, `frontend/src/app/**`, `frontend/src/api/**`, `frontend/src/controllers/workspace*`, `frontend/src/components/boardui/**`, `frontend/src/components/studio/primitives.tsx`, `frontend/src/styles/**`, entry HTML/TSX and foundation tests. Do not touch old UI or backend serving.

- [x] Read current API/auth/project/task contracts and establish shared interfaces above first; notify parent when stable.
- [x] Add objective tests for stale account/project results, auth recovery, modal focus, and Strict Mode cleanup; demonstrate relevant failure before implementation.
- [x] Install pinned React/build/test stack and free BoardUI via official source/CLI, preserving provenance and licenses. Never expose `.env` to Vite.
- [x] Implement shell, sign-in/setup recovery, project selection, scoped API/evidence registry, theme, tasks, and route wiring. Feature imports may wait for their workers; no fake functional pages.
- [x] Run type checks and meaningful foundation tests. Report commands and actual results, including dependencies still missing.

### Task 2: Data, training and evaluation route migration

**Owner:** data worker. **Files:** `frontend/src/features/data/**` and its tests/styles only. Consume Task 1 boundaries and upload contract.

- [x] Map every existing control/API call in datasets, repositories, training, evaluations and metrics into a parity checklist.
- [x] Test dataset upload/synthetic mode state, quality-task polling, URL/file submission, evaluation metric/filter/detail selection and job configuration with independently specified fixtures.
- [x] Port all eight route components and dialogs to React, including pagination/search, background tasks, snapshots, model/job setup and real linked charts. Preserve large dataset quality behavior.
- [x] Use shared uploads and evidence bindings; maintain request cancellation, failure feedback and destructive confirmations.
- [x] Run feature tests/type checks when foundation is available; report any unsupported API drilldown explicitly.

### Task 3: Shared uploads and durable assistant/search

**Owner:** assistant worker. **Files:** `frontend/src/features/assistant/**`, `frontend/src/components/studio/uploads.tsx`, upload/conversation controllers and tests; new attachment backend modules/migration/tests; existing assistant integration/conversation schema only as required. Coordinate existing `system.py` edits with parent.

- [x] Inspect current stream/persistence/scheduler and primary provider docs before extending capability contracts.
- [x] Test real upload progress vs import phase, rejection/removal/cancellation, dirty/stream conversation conflicts, project/account isolation, stable composer focus, redaction and deletion gates.
- [x] Implement shared BoardUI upload field/queue, real transport adapter and assistant Thinking/Progress/Attachments/Search presentation. No fabricated work percentages or citations.
- [x] Add actual private attachment ownership/extraction/persistence and supported-provider native search contracts; capability failures must be visible. Preserve every listed model ID and the existing presets.
- [x] Reuse existing revision/sync rules and verify reload/remote update behavior. Use the user-approved free BoardUI and custom patterns; no paid-source dependency remains.
- [x] Run targeted React/backend tests; report a reproducible live-check path to parent without exposing keys.

### Task 4: Interpretability and remaining workspace routes

**Owner:** parent, then available worker for bounded workspace pages. **Files:** `frontend/src/features/interpretability/**`, `frontend/src/features/workspace/**`, reusable plot components and objective tests.

- [x] Implement linked layer plots, neuron selection/deep links, readable regime/metric/evidence tabs, token drilldown, labels and checkpoint drift.
- [x] Test delayed earlier comparison responses, absent/undefined values, distinct-datapoint label denominators and exact row/plot/detail identity.
- [x] Port models, Playground, traces, projects, API keys, stats, settings and users with original functionality and shared components.
- [x] Verify every route against the source parity checklist; remove any temporary legacy view bridge from production.

### Task 5: Mockup/catalog, packaging, independent review and live cutover

**Owner:** parent/integration. **Files:** component catalog, backend static serving, package configuration, asset tests, release docs/changelog; only task-owned hunks.

- [x] Show the accepted visual companion mockup without presenting it as a working production app. Build reusable examples from the actual product components after integration.
- [ ] Independently review coherent increments through the restricted Agent Team verifier. Investigate failures before edits; no advisor.
- [ ] Run integrated React tests, type checking/build, relevant Python tests and packaged asset graph/byte checks. Old native tests do not establish React acceptance.
- [ ] Preserve the old served release for rollback, cut over only after local gates pass, and verify the actual served build.
- [ ] Check all 21 route families and global states live at 390×844 and wider layouts. Verify upload paths, sync across independent tabs, picking, and a real-key evaluation-review transcript.
- [ ] Record exact results, screenshots, remaining limitations and final Git status; do not stage, commit, push, switch branches or create worktrees.
