# Routes

Captured 2026-10-27T23:43:08.381443+00:00 from the current shared working tree. Local discovery only: application source is frozen. No application edits, remote calls, live-data/credential requests, deployments, branches, worktrees or commits were performed by this init. These discovery files are not an instruction to upload the entire bundle. No `.superdesign/resume.json` is created.

## Router and layout

React Router HashRouter uses paths as hash destinations (for example `#/datasets`). All 21 page components below are in authenticated Shell with WorkspaceBar, shared AssistantWidget, Suspense and route error boundary. Server permissions are not inferred from navigation visibility.

| Path | Component | File | Layout and purpose |
| --- | --- | --- | --- |
| `/datasets` | `DatasetsPage` | `frontend/src/features/data/datasets.tsx` | Shell — Dataset inventory/search/check/inspect/compare/create/import. |
| `/datasets/:name` | `DatasetPage` | `frontend/src/features/data/datasets.tsx` | Shell — Datapoint buckets/pagination, independent checked set, messages/compare, labels/splits/snapshots/quality/cleaning/enrichment/training. |
| `/repositories` | `RepositoriesPage` | `frontend/src/features/data/repositories.tsx` | Shell — Document collections, indexed metadata, add/index/query/compare and confirmed removal. |
| `/lab` | `LabPage` | `frontend/src/features/lab/index.tsx` | Shell — Typed graph editing/configuration/preflight/runs/uploads. |
| `/finetuning` | `FinetuningPage` | `frontend/src/features/data/training.tsx` | Shell — Training-job inventory and creation. |
| `/finetuning/:job` | `JobPage` | `frontend/src/features/data/training.tsx` | Shell — Job experiments/checkpoints, configuration and training inspection. |
| `/evaluations` | `EvaluationsPage` | `frontend/src/features/data/evaluations.tsx` | Shell — Evaluation inventory/setup/run actions. |
| `/evaluations/:name` | `EvaluationPage` | `frontend/src/features/data/evaluations.tsx` | Shell — Leaderboards/distributions and source/reference/prediction case inspection. |
| `/metrics` | `MetricsPage` | `frontend/src/features/data/metrics.tsx` | Shell — Metric definitions/configuration. |
| `/assistant` | `AssistantPage` | `frontend/src/features/assistant/index.tsx` | Shell — Project conversation, provider/model controls, evidence/attachments/activity and recoverable composer. |
| `/interp` | `InterpretabilityPage` | `frontend/src/features/interpretability/index.tsx` | Shell — Capture inventory and interpretability setup. |
| `/interp/c/:capture` | `CapturePage` | `frontend/src/features/interpretability/index.tsx` | Shell — Capture analytics and token/layer/neuron/provenance-aware activation/sign/gate views. |
| `/interp/d/:comparison` | `ComparisonPage` | `frontend/src/features/interpretability/index.tsx` | Shell — Cross-capture analytical comparison. |
| `/models` | `ModelsPage` | `frontend/src/features/workspace/models.tsx` | Shell — Inventory/inspect/compare/load/unload; Hub search/download/cache/token/upload; inference links. |
| `/playground` | `PlaygroundPage` | `frontend/src/features/workspace/playground.tsx` | Shell — Conversation sessions, model/instructions/context, streamed replies and trace inspection. |
| `/traces` | `TracesPage` | `frontend/src/features/workspace/traces.tsx` | Shell — Inference traces/compare/score/correction/promotion/confirmed deletion. |
| `/projects` | `ProjectsPage` | `frontend/src/features/workspace/projects.tsx` | Shell — Project inventory/lifecycle. |
| `/api-keys` | `KeysPage` | `frontend/src/features/workspace/keys.tsx` | Shell — API-key creation, one-time reveal, revocation. |
| `/stats` | `StatsPage` | `frontend/src/features/workspace/stats.tsx` | Shell — Usage/operational statistics. |
| `/settings` | `SettingsPage` | `frontend/src/features/workspace/settings.tsx` | Shell — Saved baseline/draft configuration and configured credentials/save recovery. |
| `/users` | `UsersPage` | `frontend/src/features/workspace/users.tsx` | Shell — User/role administration; admin navigation check is separate from server authorization. |

## Other branches and shared surfaces

- `/` redirects to `/datasets` with replace.
- `*` renders Page not found inside Shell and links to datasets.
- `/__components` exists only in development; `frontend/src/app/ComponentCatalogRoute.tsx` provides illustrative examples outside authenticated Shell, not live data.
- AuthView in App.tsx renders loading/unavailable/setup/sign-in/first-project states from StudioProvider; these are not independent URL routes.
- Confirmation/background Tasks are global App.tsx surfaces. Dataset/import/quality/setup/Hub upload/trace correction/admin confirmations are feature dialogs, not omitted routes.
- AssistantWidget is mounted on every authenticated page as well as the `/assistant` destination.

## `frontend/vite.config.ts`

Full Vite aliases, base path, build output, local proxy/test config and existing visual-truth plugin declaration; not executed by init.

SHA-256: `6b2d17c27ad5a00e0f292740ee156070230b170e84cc71cdeb641299ee878baf`

```ts
import { visualTruthSourcePlugin } from 'visual-truth/vite'
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath, URL } from "node:url";
export default defineConfig({
  base: "/ui/",
  envDir: false,
  plugins: [visualTruthSourcePlugin(), react(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  build: { outDir: "../tinystudio/ui/dist", emptyOutDir: true, manifest: true },
  server: {
    proxy: { "/api": "http://127.0.0.1:8000", "/v1": "http://127.0.0.1:8000" },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    restoreMocks: true,
  },
});
```

## `frontend/src/app/App.tsx`

Complete actual router configuration required by INIT; duplication with layouts.md is intentional.

SHA-256: `dc37ec3eb65393226cf56dc7a557baaccbc84a66c292aab977e10ec3df0403a9`

```tsx
import { WorkspaceBar } from "./WorkspaceBar";
import {
  RiDatabase2Line,
  RiFolder3Line,
  RiFlaskLine,
  RiBarChartLine,
  RiCpuLine,
  RiChat3Line,
  RiSettings3Line,
  RiKey2Line,
  RiGroupLine,
  RiMenuFoldLine,
  RiMenuUnfoldLine,
  RiMenuLine,
  RiCloseLine,
} from "@remixicon/react";
import { Suspense, useEffect, useState, type FormEvent } from "react";
import {
  HashRouter,
  NavLink,
  Navigate,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import { StudioProvider, useStudio } from "./StudioContext";
import { useComponentAction } from "../features/assistant/actions";
import { RouteErrorBoundary } from "./RouteErrorBoundary";
import { PlaygroundProvider } from "./PlaygroundProvider";
import { lazyRoute } from "./lazyRoute";
import {
  Button,
  Field,
  Modal,
  Page,
  EmptyState,
} from "../components/studio/primitives";
import {
  AssistantPage,
  AssistantWidget,
  AssistantProvider,
} from "../features/assistant";
const LabPage = lazyRoute(() =>
  import("../features/lab").then((module) => module.LabPage),
);
const DatasetsPage = lazyRoute(() =>
  import("../features/data").then((module) => module.DatasetsPage),
);
const DatasetPage = lazyRoute(() =>
  import("../features/data").then((module) => module.DatasetPage),
);
const RepositoriesPage = lazyRoute(() =>
  import("../features/data").then((module) => module.RepositoriesPage),
);
const FinetuningPage = lazyRoute(() =>
  import("../features/data").then((module) => module.FinetuningPage),
);
const JobPage = lazyRoute(() =>
  import("../features/data").then((module) => module.JobPage),
);
const EvaluationsPage = lazyRoute(() =>
  import("../features/data").then((module) => module.EvaluationsPage),
);
const EvaluationPage = lazyRoute(() =>
  import("../features/data").then((module) => module.EvaluationPage),
);
const MetricsPage = lazyRoute(() =>
  import("../features/data").then((module) => module.MetricsPage),
);
const InterpretabilityPage = lazyRoute(() =>
  import("../features/interpretability").then(
    (module) => module.InterpretabilityPage,
  ),
);
const CapturePage = lazyRoute(() =>
  import("../features/interpretability").then((module) => module.CapturePage),
);
const ComparisonPage = lazyRoute(() =>
  import("../features/interpretability").then(
    (module) => module.ComparisonPage,
  ),
);
const ModelsPage = lazyRoute(() =>
  import("../features/workspace").then((module) => module.ModelsPage),
);
const PlaygroundPage = lazyRoute(() =>
  import("../features/workspace").then((module) => module.PlaygroundPage),
);
const TracesPage = lazyRoute(() =>
  import("../features/workspace").then((module) => module.TracesPage),
);
const ProjectsPage = lazyRoute(() =>
  import("../features/workspace").then((module) => module.ProjectsPage),
);
const KeysPage = lazyRoute(() =>
  import("../features/workspace").then((module) => module.KeysPage),
);
const StatsPage = lazyRoute(() =>
  import("../features/workspace").then((module) => module.StatsPage),
);
const SettingsPage = lazyRoute(() =>
  import("../features/workspace").then((module) => module.SettingsPage),
);
const UsersPage = lazyRoute(() =>
  import("../features/workspace").then((module) => module.UsersPage),
);
const DevelopmentCatalog = import.meta.env.DEV
  ? lazyRoute(() =>
      import("./ComponentCatalogRoute").then(
        (module) => module.ComponentCatalogRoute,
      ),
    )
  : null;
function AuthView({ phase, error, onSubmit, onRetry }: any) {
  const [pending, setPending] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.currentTarget));
    setPending(true);
    try {
      await onSubmit(data);
    } finally {
      setPending(false);
    }
  }
  return (
    <main className="auth-layout">
      <div className="auth-card">
        <a className="brand" href="#/">
          ◈ tinystudio
        </a>
        {phase === "loading" ? (
          <p role="status">Opening workspace…</p>
        ) : phase === "unavailable" ? (
          <>
            <h1>Connection unavailable</h1>
            <p>{error || "Check your connection and try again."}</p>
            <Button onClick={onRetry}>Try again</Button>
          </>
        ) : (
          <>
            <h1>
              {phase === "setup"
                ? "Create the admin account"
                : phase === "project"
                  ? "Create your first project"
                  : "Sign in"}
            </h1>
            <p className="muted">
              {phase === "project"
                ? "A project keeps your datasets, jobs, evaluations and conversations together."
                : "Continue to your model workspace."}
            </p>
            <form onSubmit={submit}>
              {(phase === "setup" || phase === "project") && (
                <Field label="Name">
                  <input
                    name="name"
                    required={phase === "project"}
                    autoComplete="name"
                  />
                </Field>
              )}
              {phase === "project" ? (
                <Field label="Goal">
                  <textarea name="goal" />
                </Field>
              ) : (
                <>
                  <Field label="Email">
                    <input
                      type="email"
                      name="email"
                      autoComplete="username"
                      required
                    />
                  </Field>
                  <Field
                    label={
                      phase === "setup"
                        ? "Password (8+ characters)"
                        : "Password"
                    }
                  >
                    <input
                      type="password"
                      name="password"
                      minLength={phase === "setup" ? 8 : undefined}
                      autoComplete={
                        phase === "setup" ? "new-password" : "current-password"
                      }
                      required
                    />
                  </Field>
                </>
              )}
              {error && (
                <p role="alert" className="error">
                  {error}
                </p>
              )}
              <Button variant="primary" type="submit" loading={pending}>
                {phase === "setup"
                  ? "Create admin and continue"
                  : phase === "project"
                    ? "Create project"
                    : "Sign in"}
              </Button>
            </form>
          </>
        )}
      </div>
    </main>
  );
}
function Confirmation({ open, title, message, onResolve }: any) {
  return (
    <Modal
      open={open}
      title={title}
      onClose={() => onResolve(false)}
      footer={
        <>
          <Button onClick={() => onResolve(false)}>Cancel</Button>
          <Button variant="danger" onClick={() => onResolve(true)}>
            Confirm
          </Button>
        </>
      }
    >
      <p>{message}</p>
    </Modal>
  );
}
function Tasks({ open, tasks, onClose, onStop }: any) {
  if (!open) return null;
  return (
    <aside className="task-drawer" aria-label="Background tasks">
      <header className="panel-heading">
        <h2>Background tasks</h2>
        <Button
          variant="ghost"
          onClick={onClose}
          aria-label="Close background tasks"
        >
          ×
        </Button>
      </header>
      {!tasks.length ? (
        <EmptyState>No background tasks.</EmptyState>
      ) : (
        tasks.map((task: any) => (
          <article className="task-item" key={task.id}>
            <div className="actions">
              <strong>{task.kind}</strong>
              <span className="badge">{task.state}</span>
              {["queued", "running"].includes(task.state) && (
                <Button size="sm" onClick={() => onStop(task.id)}>
                  Stop
                </Button>
              )}
            </div>
            <pre>{(task.log_tail ?? []).slice(-12).join("\n")}</pre>
            {task.error && <p role="alert">{task.error}</p>}
          </article>
        ))
      )}
    </aside>
  );
}
const links = [
  [
    "Data",
    [
      ["Datasets", "/datasets"],
      ["Repositories", "/repositories"],
    ],
  ],
  [
    "Train & evaluate",
    [
      ["Laboratory", "/lab"],
      ["Fine tuning", "/finetuning"],
      ["Evaluations", "/evaluations"],
      ["Metrics", "/metrics"],
    ],
  ],
  [
    "Explore & serve",
    [
      ["Interpretability", "/interp"],
      ["Models", "/models"],
      ["Playground", "/playground"],
      ["Traces", "/traces"],
    ],
  ],
  [
    "Workspace",
    [
      ["Projects", "/projects"],
      ["API keys", "/api-keys"],
      ["Stats", "/stats"],
      ["Settings", "/settings"],
      ["Users", "/users"],
    ],
  ],
] as const;
const navigationIcons: Record<string, typeof RiDatabase2Line> = {
  "/datasets": RiDatabase2Line,
  "/repositories": RiFolder3Line,
  "/lab": RiFlaskLine,
  "/finetuning": RiCpuLine,
  "/evaluations": RiFlaskLine,
  "/metrics": RiBarChartLine,
  "/interp": RiFlaskLine,
  "/models": RiCpuLine,
  "/playground": RiChat3Line,
  "/traces": RiBarChartLine,
  "/projects": RiFolder3Line,
  "/api-keys": RiKey2Line,
  "/stats": RiBarChartLine,
  "/settings": RiSettings3Line,
  "/users": RiGroupLine,
};
type Appearance = "light" | "dark" | "system";
const APPEARANCE_KEY = "tinystudio.appearance";
const REACT_THEME_KEY = "tinystudio.theme";
const validAppearance = (value: string | null): value is Appearance =>
  value === "light" || value === "dark" || value === "system";
/** Preserve the native shell's canonical preference before the React alias. */
export function resolveAppearance(
  canonical: string | null,
  react: string | null,
): Appearance {
  return validAppearance(canonical)
    ? canonical
    : validAppearance(react)
      ? react
      : "system";
}
export function Shell() {
  const studio = useStudio(),
    location = useLocation(),
    [navOpen, setNavOpen] = useState(false),
    [navCollapsed, setNavCollapsed] = useState(false),
    [theme, setTheme] = useState(() => {
      const read = (key: string) => {
        try {
          return localStorage.getItem(key);
        } catch {
          return null;
        }
      };
      return resolveAppearance(read(APPEARANCE_KEY), read(REACT_THEME_KEY));
    });
  useEffect(() => setNavOpen(false), [location.pathname]);
  useEffect(() => {
    const media = matchMedia("(prefers-color-scheme: dark)"),
      apply = () =>
        document.documentElement.classList.toggle(
          "dark",
          theme === "dark" || (theme === "system" && media.matches),
        );
    apply();
    media.addEventListener("change", apply);
    for (const key of [APPEARANCE_KEY, REACT_THEME_KEY]) {
      try {
        localStorage.setItem(key, theme);
      } catch {}
    }
    return () => media.removeEventListener("change", apply);
  }, [theme]);
  useComponentAction({
    id: "workspace.navigate",
    label: "Open a workspace page",
    kind: "navigate",
    state: { page: location.pathname },
    options: links
      .flatMap<readonly [string, string]>(([, items]) => items)
      .filter(([label]) => label !== "Users" || studio.user?.role === "admin")
      .map(([label, value]) => ({ label, value })),
    run: (value) => {
      if (value) studio.navigate(`#${value}`);
    },
  });
  return (
    <div
      className="studio-shell workbench-shell"
      data-workspace={
        location.pathname === "/playground" ? "conversation" : undefined
      }
    >
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <WorkspaceBar theme={theme} onTheme={(value) => setTheme(resolveAppearance(value, null))} />
      <main id="main-content" tabIndex={-1} aria-label="Workspace content">
        <RouteErrorBoundary resetKey={studio.scopeKey + studio.route}>
          <Suspense
            fallback={
              <Page title="Loading page">
                <p role="status">Opening this workspace page…</p>
              </Page>
            }
          >
            <Routes key={studio.scopeKey}>
              <Route path="/" element={<Navigate to="/datasets" replace />} />
              <Route path="/datasets" element={<DatasetsPage />} />
              <Route path="/lab" element={<LabPage />} />
              <Route path="/datasets/:name" element={<DatasetPage />} />
              <Route path="/repositories" element={<RepositoriesPage />} />
              <Route path="/finetuning" element={<FinetuningPage />} />
              <Route path="/finetuning/:job" element={<JobPage />} />
              <Route path="/evaluations" element={<EvaluationsPage />} />
              <Route path="/evaluations/:name" element={<EvaluationPage />} />
              <Route path="/metrics" element={<MetricsPage />} />
              <Route path="/assistant" element={<AssistantPage />} />
              <Route path="/interp" element={<InterpretabilityPage />} />
              <Route path="/interp/c/:capture" element={<CapturePage />} />
              <Route
                path="/interp/d/:comparison"
                element={<ComparisonPage />}
              />
              <Route path="/models" element={<ModelsPage />} />
              <Route path="/playground" element={<PlaygroundPage />} />
              <Route path="/traces" element={<TracesPage />} />
              <Route path="/projects" element={<ProjectsPage />} />
              <Route path="/api-keys" element={<KeysPage />} />
              <Route path="/stats" element={<StatsPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/users" element={<UsersPage />} />
              <Route
                path="*"
                element={
                  <Page title="Page not found">
                    <NavLink to="/datasets">Open datasets</NavLink>
                  </Page>
                }
              />
            </Routes>
          </Suspense>
        </RouteErrorBoundary>
      </main>
      <AssistantWidget />
    </div>
  );
}
function ApplicationRoutes() {
  const location = useLocation();
  if (
    import.meta.env.DEV &&
    location.pathname === "/__components" &&
    DevelopmentCatalog
  )
    return (
      <RouteErrorBoundary resetKey="ILLUSTRATIVE_CATALOG">
        <Suspense
          fallback={
            <Page title="Loading illustrative catalog">
              <p role="status">Opening local component examples…</p>
            </Page>
          }
        >
          <DevelopmentCatalog />
        </Suspense>
      </RouteErrorBoundary>
    );
  return (
    <StudioProvider
      authView={(props) => <AuthView {...props} />}
      confirmation={(props) => <Confirmation {...props} />}
      taskView={(props) => <Tasks {...props} />}
    >
      <AssistantProvider>
        <PlaygroundProvider>
          <Shell />
        </PlaygroundProvider>
      </AssistantProvider>
    </StudioProvider>
  );
}
export function App() {
  return (
    <HashRouter>
      <ApplicationRoutes />
    </HashRouter>
  );
}
```

