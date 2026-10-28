import { WorkspaceBar } from "./WorkspaceBar";
import { destinations } from "./navigation";
import { lazy, Suspense, useEffect, useState, type FormEvent } from "react";
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
const DevelopmentWorkspacePreview = import.meta.env.DEV
  ? lazy(() =>
      import("../catalog/WorkspacePreview").then((module) => ({
        default: module.WorkspacePreview,
      })),
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
    options: destinations
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
      <WorkspaceBar
        theme={theme}
        onTheme={(value) => setTheme(resolveAppearance(value, null))}
      />
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
  if (
    import.meta.env.DEV &&
    window.location.hash.split("?")[0] === "#/__preview" &&
    DevelopmentWorkspacePreview
  ) {
    const initialRoute =
      new URLSearchParams(window.location.hash.split("?")[1] || "").get(
        "screen",
      ) || "/datasets";
    return (
      <RouteErrorBoundary resetKey="ILLUSTRATIVE_WORKSPACE">
        <Suspense fallback={<Page title="Loading illustrative workspace" />}>
          <DevelopmentWorkspacePreview initialRoute={initialRoute} />
        </Suspense>
      </RouteErrorBoundary>
    );
  }
  return (
    <HashRouter>
      <ApplicationRoutes />
    </HashRouter>
  );
}
