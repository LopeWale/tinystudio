# Layouts

Captured 2026-10-27T23:43:08.381443+00:00 from the current shared working tree. Local discovery only: application source is frozen. No application edits, remote calls, live-data/credential requests, deployments, branches, worktrees or commits were performed by this init. These discovery files are not an instruction to upload the entire bundle. No `.superdesign/resume.json` is created.

Shared composition: DirectionProvider → App/HashRouter → ApplicationRoutes → StudioProvider → AssistantProvider → PlaygroundProvider → Shell. Shell renders WorkspaceBar, routed main content under RouteErrorBoundary/Suspense, and AssistantWidget. AuthView, Confirmation and Tasks live in App.tsx. `/__components` is a development-only alternative branch outside authenticated Shell. Current Shell renders a top location bar/open-work strip; unused legacy sidebar definitions in App.tsx do not establish a rendered sidebar.

## `frontend/src/main.tsx`

Complete root mount and CSS order; includes current visual-truth instrumentation import, which this init does not execute or change.

SHA-256: `9f6657a0743288706dc1634bee4e58c790cace9b9fc3e91314375b5c3714857b`

```tsx
import { installVisualTruthPatches } from './visual-truth.generated'
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./app/App";
import { DirectionProvider } from "./components/boardui/foundations/direction/direction";
import "./styles/globals.css";
import "./styles/studio.css";
import "./styles/workspace.css";
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <DirectionProvider locale="en-US">
      <App />
    </DirectionProvider>
  </StrictMode>,
);
import "./styles/workbench.css";


installVisualTruthPatches()
```

## `frontend/src/app/App.tsx`

Full shell/auth/global confirmation/background tasks/router composition.

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

## `frontend/src/app/WorkspaceBar.tsx`

Full project/location, Pages/Recent, open-work, appearance and task/assistant controls.

SHA-256: `ce4957eb778872eca4443f39bfd67bb0e0f9bccd9c31ef3e478d8a05d305e12e`

```tsx
import { useEffect, useMemo, useRef, useState } from 'react';
import { NavLink, useLocation, useNavigate } from 'react-router-dom';
import { RiArrowLeftLine, RiArrowRightLine, RiCloseLine, RiApps2Line, RiHistoryLine, RiChat3Line } from '@remixicon/react';
import { Button } from '../components/studio/primitives';
import { Select, SelectItem } from '../components/boardui/base/select/select';
import { useStudio } from './StudioContext';
import { NavigationSession } from '../controllers/navigation';
import { useAssistantDock } from '../features/assistant';
export const destinations = [
  ['Data', [['Datasets','/datasets'],['Repositories','/repositories']]],
  ['Build', [['Laboratory','/lab'],['Fine tuning','/finetuning']]],
  ['Evaluate', [['Evaluations','/evaluations'],['Metrics','/metrics']]],
  ['Explore', [['Interpretability','/interp'],['Models','/models'],['Playground','/playground'],['Traces','/traces']]],
  ['Workspace', [['Projects','/projects'],['API keys','/api-keys'],['Stats','/stats'],['Settings','/settings'],['Users','/users']]],
] as const;
export function locationTitle(route: string) {
  const path=route.split('?')[0];
  const dest=destinations.flatMap(([,items])=>[...items]).find(([,url])=>path===url || path.startsWith(url+'/'));
  const tail=path.split('/').filter(Boolean).at(-1);
  let name=tail || 'Workspace'; try{name=decodeURIComponent(name);}catch{}
  return dest ? path===dest[1] ? dest[0] : `${dest[0]} / ${name}` : 'Workspace';
}
export function WorkspaceBar({theme,onTheme}:{theme:string;onTheme:(value:string)=>void}) {
  const studio=useStudio(), location=useLocation(), navigate=useNavigate(), dock=useAssistantDock();
  const route=location.pathname+location.search;
  const session=useMemo(()=>new NavigationSession(studio.scopeKey,window.sessionStorage),[studio.scopeKey]);
  const [work,setWork]=useState(session.snapshot()), [menu,setMenu]=useState<'pages'|'recent'|null>(null);
  const bar=useRef<HTMLElement>(null);
  useEffect(()=>{
    if(route!=="/")session.visit(route,locationTitle(route));setWork(session.snapshot());setMenu(null);
    const main=document.getElementById('main-content');
    const restore=requestAnimationFrame(()=>{ if(main) main.scrollTop=session.scrollFor(route); });
    return ()=>{cancelAnimationFrame(restore);if(main)session.rememberScroll(route,main.scrollTop);};
  },[route,session]);
  useEffect(()=>{
    if(!menu)return;
    const close=(event:PointerEvent)=>{if(!bar.current?.contains(event.target as Node))setMenu(null);};
    const escape=(event:KeyboardEvent)=>{if(event.key==='Escape'){setMenu(null);bar.current?.querySelector<HTMLButtonElement>('[aria-expanded="true"]')?.focus();}};
    document.addEventListener('pointerdown',close);document.addEventListener('keydown',escape);
    return()=>{document.removeEventListener('pointerdown',close);document.removeEventListener('keydown',escape);};
  },[menu]);
  return <header ref={bar} className="workspace-bar">
    <div className="workspace-location">
      <a className="brand" href="#/datasets" aria-label="TinyStudio home">tinystudio</a>
      <div className="workspace-project"><Select aria-label="Current project" selectedKey={studio.project} onSelectionChange={key=>{if(key)studio.selectProject(String(key));}}>
        {studio.projects.map(p=><SelectItem id={p.slug} key={p.slug}>{p.name}</SelectItem>)}
      </Select></div>
      <div className="history-buttons"><Button variant="ghost" aria-label="Go back" title="Go back" onClick={()=>navigate(-1)}><RiArrowLeftLine size={16}/></Button><Button variant="ghost" aria-label="Go forward" title="Go forward" onClick={()=>navigate(1)}><RiArrowRightLine size={16}/></Button></div>
      <Button variant="ghost" aria-expanded={menu==='pages'} aria-controls="workspace-destinations" onClick={()=>setMenu(menu==='pages'?null:'pages')}><RiApps2Line size={16}/> Pages</Button>
      <span className="workspace-location-title">{locationTitle(route)}</span>
      <div className="workspace-global-actions">
        <Button variant="ghost" aria-expanded={menu==='recent'} onClick={()=>setMenu(menu==='recent'?null:'recent')}><RiHistoryLine size={16}/><span>Recent</span></Button>
        <Button variant="ghost" onClick={studio.showTasks}>Tasks</Button>
        <Button aria-expanded={dock.expanded} onClick={()=>dock.expanded?dock.collapse():dock.open()}><RiChat3Line size={16}/> Assistant</Button>
      </div>
    </div>
    <nav className="open-work" aria-label="Open work">
      {work.entries.map(entry=><span key={entry.route} className={entry.route===route?'work-tab current':'work-tab'}><NavLink to={entry.route} aria-current={entry.route===route?'page':undefined}>{entry.title}</NavLink><Button variant="ghost" aria-label={`Close ${entry.title}`} onClick={()=>{session.close(entry.route);const next=session.snapshot();setWork(next);if(entry.route===route)navigate(next.entries.at(-1)?.route||'/datasets');}}><RiCloseLine size={14}/></Button></span>)}
    </nav>
    {menu==='pages' && <div id="workspace-destinations" className="destination-popover">
      <nav aria-label="Main navigation">{destinations.map(([group,items])=><section key={group}><h2>{group}</h2>{items.filter(([label])=>label!=='Users'||studio.user?.role==='admin').map(([label,path])=><NavLink key={path} to={path}>{label}</NavLink>)}</section>)}</nav>
      <footer><label>Appearance <select aria-label="Appearance" value={theme} onChange={event=>onTheme(event.target.value)}><option value="light">Light</option><option value="dark">Dark</option><option value="system">System</option></select></label><span>{studio.user?.name||studio.user?.email}</span><Button variant="ghost" onClick={()=>studio.signOut().catch(e=>studio.toast(e.message,true))}>Sign out</Button></footer>
    </div>}
    {menu==='recent' && <div className="recent-popover"><h2>Return to your work</h2><p>Recent locations in this project · this device</p>{work.recent.map(entry=><NavLink key={entry.route} to={entry.route}>{entry.title}<span>Resume →</span></NavLink>)}</div>}
  </header>;
}
```

## `frontend/src/app/RouteErrorBoundary.tsx`

Full shared route exception/retry/return rendering.

SHA-256: `8e2bc720a2da4dfaef500e507521db32f37f883925e8d1ced4f242ecd3a31fe7`

```tsx
import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button, Page, Panel } from "../components/studio/primitives";
import { RouteModuleError } from "./lazyRoute";
interface Props {
  children: ReactNode;
  resetKey: string;
  onError?: (error: Error, info: ErrorInfo) => void;
  onReload?: () => void;
}
interface State {
  error: Error | null;
  attempt: number;
}
/** A page render failure is contained below navigation and session ownership. */
export class RouteErrorBoundary extends Component<Props, State> {
  state: State = { error: null, attempt: 0 };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    this.props.onError?.(error, info);
  }
  componentDidUpdate(previous: Props) {
    if (previous.resetKey !== this.props.resetKey && this.state.error)
      this.setState({ error: null, attempt: this.state.attempt + 1 });
  }
  private retry = () =>
    this.setState(({ attempt }) => ({ error: null, attempt: attempt + 1 }));
  render() {
    if (this.state.error) {
      const moduleFailed = this.state.error instanceof RouteModuleError;
      return (
        <Page
          title={
            moduleFailed
              ? "This page could not load"
              : "This page could not render"
          }
          description="Your workspace is still open. You can choose another page from navigation."
        >
          <Panel>
            <p role="alert">
              {moduleFailed
                ? "Page code could not be downloaded. Check your connection, then reload this page. Reloading clears unsaved input."
                : "An unexpected interface error interrupted this page."}
            </p>
            {moduleFailed ? (
              <a
                href={window.location.href}
                onClick={(event) => {
                  event.preventDefault();
                  if (this.props.onReload) this.props.onReload();
                  else window.location.reload();
                }}
              >
                Reload this page
              </a>
            ) : (
              <Button variant="primary" onClick={this.retry}>
                Try this page again
              </Button>
            )}
            <a href="#/datasets" style={{ marginInlineStart: "1rem" }}>
              Open datasets
            </a>
          </Panel>
        </Page>
      );
    }
    return (
      <div key={this.state.attempt} className="contents">
        {this.props.children}
      </div>
    );
  }
}
```

## `frontend/src/app/StudioContext.tsx`

Full shared user/project auth scope and resource/provider lifecycle.

SHA-256: `692713f4005f4190cc35a3c4ab454f95ebbe75f9bb902863553872d74cbcba11`

```tsx
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useId,
  type ReactNode,
} from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { createApiClient, type ApiClient } from "../api/client";
import { WorkspaceScope } from "../controllers/workspaceScope";
import { EvidenceRegistry, canCaptureApiEvidence } from "./evidence";
export type { ApiClient } from "../api/client";
export interface StudioRuntime {
  api: ApiClient;
  project: string;
  user: any;
  overview: any;
  refreshOverview(): Promise<void>;
  navigate(hash: string): void;
  toast(message: string, error?: boolean): void;
  confirm(message: string, title?: string): Promise<boolean>;
  trackTask(task: any): void;
  showTasks(): void;
  route: string;
  evidence: EvidenceRegistry;
  scopeKey: string;
  projects: any[];
  selectProject(project: string): void;
  refreshProjects(): Promise<void>;
  signOut(): Promise<void>;
  onSessionExpired(): void;
}
export const StudioContext = createContext<StudioRuntime | null>(null);
export function useStudio() {
  const runtime = useContext(StudioContext);
  if (!runtime) throw new Error("StudioProvider is required");
  return runtime;
}
export function useResource<T = any>(path: string | null) {
  const { api, scopeKey, route, project, user, evidence } = useStudio(),
    id = useId(),
    [version, reload] = useState(0),
    key = JSON.stringify([path, scopeKey, version]),
    [state, setState] = useState<{
      key: string;
      data: T | undefined;
      loading: boolean;
      error: Error | null;
    }>({ key, data: undefined, loading: !!path, error: null });
  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    setState({ key, data: undefined, loading: !!path, error: null });
    if (path)
      api
        .get<T>(path, controller.signal)
        .then((data) => {
          if (current) setState({ key, data, loading: false, error: null });
        })
        .catch((error) => {
          if (current && error.name !== "AbortError")
            setState({ key, data: undefined, loading: false, error });
        });
    return () => {
      current = false;
      controller.abort();
    };
  }, [api, path, scopeKey, version]);
  const current =
    state.key === key
      ? state
      : { data: undefined, loading: !!path, error: null };
  useEffect(() => {
    if (!path || current.data === undefined || !canCaptureApiEvidence(path))
      return;
    return evidence.register({
      id: "api:" + id,
      label: `GET ${path}`,
      data: current.data,
      route,
      project,
      user: String(user?.id ?? ""),
    });
  }, [path, current.data, evidence, id, route, project, user?.id]);
  return { ...current, reload: useCallback(() => reload((v) => v + 1), []) };
}
export function StudioProvider({
  children,
  authView,
  confirmation,
  taskView,
}: {
  children: ReactNode;
  authView: (props: any) => ReactNode;
  confirmation: (props: any) => ReactNode;
  taskView: (props: any) => ReactNode;
}) {
  const location = useLocation(),
    go = useNavigate(),
    route = location.pathname + location.search;
  const [scope] = useState(() => new WorkspaceScope()),
    [evidence] = useState(() => new EvidenceRegistry());
  const [user, setUser] = useState<any>(null),
    [project, setProject] = useState(""),
    [projects, setProjects] = useState<any[]>([]),
    [overview, setOverview] = useState<any>(null),
    [phase, setPhase] = useState("loading"),
    [authError, setAuthError] = useState(""),
    [retry, setRetry] = useState(0),
    [notices, setNotices] = useState<
      { id: number; message: string; error: boolean }[]
    >([]),
    [tasks, setTasks] = useState<any[]>([]),
    [tasksOpen, setTasksOpen] = useState(false),
    [pendingConfirm, setPendingConfirm] = useState<{
      message: string;
      title: string;
      resolve: (value: boolean) => void;
    } | null>(null);
  const confirmationRef = useRef<typeof pendingConfirm>(null);
  const settleConfirmation = useCallback((value: boolean) => {
    const pending = confirmationRef.current;
    confirmationRef.current = null;
    pending?.resolve(value);
    setPendingConfirm(null);
  }, []);
  const askConfirmation = useCallback(
    (message: string, title = "Confirm action") =>
      new Promise<boolean>((resolve) => {
        settleConfirmation(false);
        const pending = { message, title, resolve };
        confirmationRef.current = pending;
        setPendingConfirm(pending);
      }),
    [settleConfirmation],
  );
  const latest = useRef({ user, project });
  latest.current = { user, project };
  const scopeKey = `${user?.id ?? ""}:${project}`;
  scope.setIdentity(scopeKey);
  const evidenceKey = useRef("");
  if (evidenceKey.current !== scopeKey + ":" + route) {
    evidenceKey.current = scopeKey + ":" + route;
    evidence.clear();
  }
  const unauthorized = useCallback(() => {
    scope.invalidate();
    evidence.clear();
    latest.current = { user: null, project: "" };
    settleConfirmation(false);
    setUser(null);
    setProject("");
    setOverview(null);
    setTasks([]);
    setPhase("login");
  }, [scope, evidence, settleConfirmation]);
  const api = useMemo(
    () =>
      createApiClient({
        scope,
        getProject: () => latest.current.project,
        getUser: () => latest.current.user?.id,
        onUnauthorized: unauthorized,
      }),
    [scope, unauthorized],
  );
  const toast = useCallback(
    (message: string, error = false) =>
      setNotices((n) => [
        ...n.slice(-3),
        { id: Date.now() + Math.random(), message, error },
      ]),
    [],
  );
  const selectProject = useCallback(
    (slug: string) => {
      scope.setIdentity(`${latest.current.user?.id ?? ""}:${slug}`);
      evidence.clear();
      latest.current = { ...latest.current, project: slug };
      setProject(slug);
      setOverview(null);
      setTasks([]);
      settleConfirmation(false);
      try {
        localStorage.setItem("tinystudio.project", slug);
      } catch {}
    },
    [scope, evidence, settleConfirmation],
  );
  const refreshProjects = useCallback(async () => {
    const rows = await api.get<any[]>("/api/projects");
    setProjects(rows);
    if (!rows.some((p) => p.slug === latest.current.project)) {
      let wanted = "";
      try {
        wanted = localStorage.getItem("tinystudio.project") ?? "";
      } catch {}
      selectProject(
        rows.some((p) => p.slug === wanted) ? wanted : (rows[0]?.slug ?? ""),
      );
    }
  }, [api, selectProject]);
  const refreshOverview = useCallback(async () => {
    if (latest.current.project) setOverview(await api.get("/api/overview"));
  }, [api]);
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setPhase("loading");
    api
      .get<any>("/api/auth/status", controller.signal)
      .then((status) => {
        if (!active) return;
        setUser(status.user);
        setPhase(
          status.setup_required
            ? "setup"
            : status.authenticated
              ? "ready"
              : "login",
        );
      })
      .catch((error) => {
        if (active && error.name !== "AbortError") {
          setAuthError(error.message);
          setPhase("unavailable");
        }
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [api, retry]);
  useEffect(() => {
    if (phase !== "ready" || !user) return;
    refreshProjects().catch((error) => {
      if (error.name !== "AbortError") toast(error.message, true);
    });
  }, [phase, user?.id, refreshProjects, toast]);
  useEffect(() => {
    if (!project || phase !== "ready") return;
    refreshOverview().catch((error) => {
      if (error.name !== "AbortError") toast(error.message, true);
    });
  }, [project, phase, refreshOverview, toast]);
  useEffect(() => {
    if (!user || !project || phase !== "ready") return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    const poll = async () => {
      try {
        const rows = await api.get<any[]>(
          "/api/tasks?include_result=false",
          controller.signal,
        );
        if (!active) return;
        setTasks(rows);
      } catch (error: any) {
        if (error.name === "AuthenticationRequiredError") return;
      }
      if (active) timer = setTimeout(poll, 2500);
    };
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, [api, user?.id, project, phase, route]);
  useEffect(
    () => () => settleConfirmation(false),
    [route, scopeKey, settleConfirmation],
  );
  useEffect(
    () => () => {
      scope.invalidate();
      evidence.clear();
      settleConfirmation(false);
    },
    [scope, evidence, settleConfirmation],
  );
  const runtime: StudioRuntime = {
    onSessionExpired: unauthorized,
    api,
    project,
    user,
    overview,
    route,
    evidence,
    scopeKey,
    projects,
    selectProject,
    refreshProjects,
    refreshOverview,
    navigate: (hash) => {
      settleConfirmation(false);
      go(hash.replace(/^#/, ""));
    },
    toast,
    confirm: askConfirmation,
    trackTask: (task) => {
      if (task?.id)
        setTasks((rows) => [task, ...rows.filter((t) => t.id !== task.id)]);
      setTasksOpen(true);
    },
    showTasks: () => setTasksOpen(true),
    signOut: async () => {
      const result = await api.post<any>("/api/auth/logout");
      unauthorized();
      if (result.logout_url) window.location.assign(result.logout_url);
    },
  };
  const authenticate = async (body: unknown) => {
    setAuthError("");
    try {
      await api.post(
        `/api/auth/${phase === "setup" ? "setup" : "login"}`,
        body,
      );
      setRetry((v) => v + 1);
    } catch (error: any) {
      setAuthError(error.message);
    }
  };
  return (
    <StudioContext.Provider value={runtime}>
      {phase === "ready"
        ? project
          ? children
          : authView({
              phase: "project",
              error: authError,
              onSubmit: async (body: unknown) => {
                try {
                  const pr = await api.post<any>("/api/projects", body);
                  await refreshProjects();
                  selectProject(pr.slug);
                } catch (e: any) {
                  setAuthError(e.message);
                }
              },
              onRetry: () => void refreshProjects(),
            })
        : authView({
            phase,
            error: authError,
            onSubmit: authenticate,
            onRetry: () => setRetry((v) => v + 1),
          })}
      <div className="toast-region" aria-live="polite">
        {notices.map((n) => (
          <div className={n.error ? "toast error" : "toast"} key={n.id}>
            {n.message}
            <button
              aria-label="Dismiss notification"
              onClick={() =>
                setNotices((rows) => rows.filter((x) => x.id !== n.id))
              }
            >
              ×
            </button>
          </div>
        ))}
      </div>
      {pendingConfirm &&
        confirmation({
          open: true,
          title: pendingConfirm.title,
          message: pendingConfirm.message,
          onResolve: (value: boolean) => {
            settleConfirmation(value);
          },
        })}
      {taskView({
        open: tasksOpen,
        tasks,
        onClose: () => setTasksOpen(false),
        onStop: async (id: string) => {
          try {
            await api.post(`/api/tasks/${encodeURIComponent(id)}/stop`);
          } catch (e: any) {
            toast(e.message, true);
          }
        },
      })}
    </StudioContext.Provider>
  );
}
```

## `frontend/src/app/PlaygroundProvider.tsx`

Full shared Playground controller lifetime across routes.

SHA-256: `5ea7425e698264da0b5f977d1023b55c32cc7ec536595a3c64713871fff2098d`

```tsx
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  type ReactNode,
} from "react";
import { PlaygroundController } from "../controllers/playground";
import { useStudio } from "./StudioContext";
const PlaygroundContext = createContext<PlaygroundController | null>(null);
export function PlaygroundProvider({ children }: { children: ReactNode }) {
  const studio = useStudio();
  const controller = useMemo(
    () =>
      new PlaygroundController({
        project: studio.project,
        user: String(studio.user?.id ?? ""),
        onUnauthorized: () => studio.onSessionExpired(),
      }),
    [studio.project, studio.user?.id],
  );
  useEffect(() => () => controller.deactivate(), [controller]);
  return (
    <PlaygroundContext.Provider value={controller}>
      {children}
    </PlaygroundContext.Provider>
  );
}
export function usePlaygroundController() {
  const controller = useContext(PlaygroundContext);
  if (!controller) throw new Error("PlaygroundProvider is required");
  return controller;
}
```

## `frontend/src/components/boardui/foundations/direction/direction.tsx`

Root DirectionProvider and direction hooks.

SHA-256: `244ca97a8e35825a8e2ef4e953f27c49c6e49b5c5b9ea38e393024e9d1e8fcd6`

```tsx
"use client";

import type { HTMLAttributes, ReactNode } from "react";
import { I18nProvider, useLocale } from "react-aria-components";
import { cx } from "@/components/boardui/utils/cx";

export type Direction = "ltr" | "rtl";

export interface DirectionProviderProps extends Omit<HTMLAttributes<HTMLDivElement>, "dir" | "lang" | "children"> {
  /** BCP 47 language tag. Arabic and Hebrew resolve to RTL; Chinese to LTR. */
  locale: string;
  children: ReactNode;
}

/** Keeps CSS direction, React Aria keyboard behavior and portalled menus in sync.
 * Use at the app root, or nest providers for regions in another language.
 * The default display: contents boundary preserves the surrounding layout.
 */
export function DirectionProvider({ locale, ...props }: DirectionProviderProps) {
  return (
    <I18nProvider locale={locale}>
      <DirectionBoundary {...props} />
    </I18nProvider>
  );
}

function DirectionBoundary({ children, className, ...props }: Omit<DirectionProviderProps, "locale">) {
  const { locale, direction } = useLocale();
  return <div {...props} lang={locale} dir={direction} className={cx("contents", className)}>{children}</div>;
}

/** Also works inside a React Aria I18nProvider. */
export function useDirection(): Direction {
  return useLocale().direction;
}
```

## `frontend/src/features/assistant/index.tsx`

Full AssistantProvider, AssistantWidget, AssistantPage and dock/history/composer/evidence picking; same actual file implements shared layout and route.

SHA-256: `780c8c4d2eb388d54b5a8271e3c5473ae3cbe2dff93cf5ca1bb37c7eeea50a01`

```tsx
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { useLocation } from "react-router-dom";
import { useStudio } from "../../app/StudioContext";
import { AgentThinking } from "../../components/boardui/application/agent-thinking/agent-thinking";
import { FileUploadField, UploadStatus } from "../../components/studio/uploads";
import {
  ConversationController,
  type Attachment,
  type Message,
  type DeepReadonly,
} from "../../controllers/conversation";
import {
  UploadController,
  UploadBatchController,
} from "../../controllers/uploads";
import { Button } from "../../components/studio/primitives";
import { Select, SelectItem } from "@/components/boardui/base/select/select";
import { Checkbox } from "@/components/boardui/base/checkbox/checkbox";
import { Textarea } from "@/components/boardui/base/textarea/textarea";
import "./assistant.css";
import { cx } from "@/components/boardui/utils/cx";
import { AssistantScrollArea } from "./scrollArea";
import { AssistantMessageContent } from "./messageContent";
import { capturePageContext } from "./context";
import { AssistantUiBridge } from "./uiBridge";
import {
  AssistantActivity,
  AssistantAttachments,
  AssistantSources,
} from "./display";
export {
  AssistantActivity,
  AssistantAttachments,
  AssistantSources,
} from "./display";

import { ComponentActionRegistry } from "../../controllers/componentActions";
import { ComponentActionsProvider } from "./actions";
export { useComponentAction } from "./actions";
const DockContext = createContext({
  expanded: false,
  open: () => {},
  collapse: () => {},
});
export const useAssistantDock = () => useContext(DockContext);
const Context = createContext<ConversationController | null>(null);
const ResearchRouteContext = createContext("/evaluations");
export function AssistantProvider({ children }: { children: ReactNode }) {
  const studio = useStudio();
  const [expanded, setExpanded] = useState(false);
  const registry = useMemo(
    () =>
      new ComponentActionRegistry((message, title) =>
        studio.confirm(message, title),
      ),
    [studio.confirm],
  );
  registry.setScope({
    user: String(studio.user?.id || ""),
    project: studio.project,
    route: studio.route,
  });
  const latestStudio = useRef(studio);
  latestStudio.current = studio;
  const identity = String(studio.user?.id || "");
  const project = studio.project;
  const lastResearchRoute = useRef("/evaluations");
  if (
    studio.route &&
    !/^\/(?:assistant|settings|api-keys|users)(?:[/?]|$)/.test(studio.route)
  )
    lastResearchRoute.current = studio.route;
  const controller = useMemo(() => {
    const bridge = new AssistantUiBridge({
      navigate: (hash) => latestStudio.current.navigate(hash),
      actions: registry,
      evidence: studio.evidence,
    });
    return new ConversationController({
      api: studio.api,
      user: identity,
      project,
      captureContext: () => {
        const current = latestStudio.current;
        return capturePageContext({
          evidence: current.evidence,
          route: current.route,
          project: current.project,
          user: String(current.user?.id || ""),
          title: document.title,
        });
      },
      validateSelection: (selection) =>
        studio.evidence
          .snapshot()
          .some(
            (record) =>
              record.id === selection.id &&
              record.project === project &&
              record.user === identity &&
              record.route === selection.route &&
              JSON.stringify(record.data) === JSON.stringify(selection.data),
          ),
      onUi: (action, args, allowed) => bridge.perform(action, args, allowed),
    });
  }, [studio.api, identity, project, registry]);
  useEffect(() => {
    if (identity && project) controller.start();
    return () => controller.stop();
  }, [controller, identity, project]);
  return (
    <ComponentActionsProvider registry={registry}>
      <DockContext.Provider
        value={{
          expanded,
          open: () => setExpanded(true),
          collapse: () => setExpanded(false),
        }}
      >
        <Context.Provider value={controller}>
          <ResearchRouteContext.Provider value={lastResearchRoute.current}>
            {children}
          </ResearchRouteContext.Provider>
        </Context.Provider>
      </DockContext.Provider>
    </ComponentActionsProvider>
  );
}
function useConversation() {
  const controller = useContext(Context);
  if (!controller)
    throw new Error("AssistantProvider must wrap the studio routes");
  return {
    controller,
    state: useSyncExternalStore(controller.subscribe, controller.getSnapshot),
  };
}
export function AssistantPage() {
  const studio = useStudio(),
    dock = useAssistantDock(),
    researchRoute = useContext(ResearchRouteContext);
  useEffect(() => {
    dock.open();
    studio.navigate("#" + researchRoute);
  }, []);
  return null;
}
export function AssistantWidget() {
  const dock = useAssistantDock(),
    studio = useStudio();
  const [picking, setPicking] = useState(false),
    [cancelPick, setCancelPick] = useState(0);
  return (
    <aside
      className="assistant-widget"
      aria-label="Workspace assistant"
      data-expanded={dock.expanded && !picking}
      data-picking={picking}
      data-assistant-surface
    >
      <div className="assistant-dock-header">
        <div className="assistant-dock-title">
          <strong>Assistant</strong>
          <span className="text-caption-1-regular">{studio.project}</span>
        </div>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          aria-expanded={dock.expanded && !picking}
          onClick={() =>
            picking
              ? setCancelPick((v) => v + 1)
              : dock.expanded
                ? dock.collapse()
                : dock.open()
          }
        >
          {picking
            ? "Cancel pick"
            : dock.expanded
              ? "Collapse assistant"
              : "Assistant"}
        </Button>
      </div>
      {picking && (
        <p className="assistant-pick-status" role="status">
          Pick a data row or chart. Escape cancels.
        </p>
      )}
      <div
        className="assistant-dock-body"
        hidden={picking || !dock.expanded}
        style={picking || !dock.expanded ? { display: "none" } : undefined}
      >
        <AssistantConversation
          compact
          expanded={dock.expanded}
          onPickingChange={setPicking}
          cancelPick={cancelPick}
        />
      </div>
    </aside>
  );
}

function Elapsed({ startedAt }: { startedAt: number }) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [startedAt]);
  return (
    <span aria-label="Request elapsed time">
      {Math.max(0, Math.floor((now - startedAt) / 1000))}s
    </span>
  );
}
function MessageView({ message }: { message: DeepReadonly<Message> }) {
  return (
    <article className={cx("assistant-message", message.role)}>
      <strong>{message.role === "user" ? "You" : "Assistant"}</strong>
      <AssistantMessageContent role={message.role} content={message.content} />
      <AssistantAttachments attachments={message.attachments || []} />
      {message.selected_data != null && (
        <details>
          <summary>Picked page evidence</summary>
          <pre className="text-body-2-regular">
            {JSON.stringify(message.selected_data, null, 2)}
          </pre>
        </details>
      )}
      <AssistantActivity calls={message.calls} searches={message.searches} />
      <AssistantSources sources={message.sources || []} />
    </article>
  );
}

export function AssistantConversation({
  onPickingChange,
  cancelPick = 0,
  compact = false,
  expanded = true,
}: {
  onPickingChange?: (picking: boolean) => void;
  cancelPick?: number;
  compact?: boolean;
  expanded?: boolean;
} = {}) {
  const studio = useStudio(),
    { controller, state } = useConversation();
  const [models, setModels] = useState<any>(null),
    [config, setConfig] = useState<any>(null),
    [files, setFiles] = useState<File[]>([]),
    [upload, setUpload] = useState<any>(null),
    [picking, setPicking] = useState(false),
    [uploadBusy, setUploadBusy] = useState(false);
  const composer = useRef<HTMLTextAreaElement>(null);
  const researchRoute = useContext(ResearchRouteContext);
  const caret = useRef<{ start: number; end: number } | null>(null),
    restoreComposer = useRef(false);
  const exitPicking = () => {
    restoreComposer.current = true;
    setPicking(false);
    onPickingChange?.(false);
  };
  const beginPicking = () => {
    caret.current = composer.current
      ? {
          start: composer.current.selectionStart,
          end: composer.current.selectionEnd,
        }
      : null;
    setPicking(true);
    onPickingChange?.(true);
  };
  useEffect(() => {
    if (!picking && restoreComposer.current) {
      restoreComposer.current = false;
      const textarea = composer.current;
      textarea?.focus();
      if (textarea && caret.current)
        textarea.setSelectionRange(caret.current.start, caret.current.end);
    }
  }, [picking]);
  useEffect(() => {
    if (cancelPick) exitPicking();
  }, [cancelPick]);
  const mounted = useRef(false);
  const route = useLocation();
  const uploadOwner = useMemo(
    () => new UploadController<Attachment>(setUpload),
    [controller],
  );
  const uploadBatch = useMemo(
    () => new UploadBatchController(uploadOwner),
    [uploadOwner],
  );
  useEffect(() => {
    mounted.current = true;
    const abort = new AbortController();
    void studio.api
      .get("/api/assistant/models", abort.signal)
      .then(setModels)
      .catch((e) => {
        if (!abort.signal.aborted) studio.toast(e.message, true);
      });
    void studio.api
      .get(
        `/api/p/${encodeURIComponent(studio.project)}/assistant/attachments/config`,
        abort.signal,
      )
      .then(setConfig)
      .catch(() => {});
    return () => {
      mounted.current = false;
      abort.abort();
      uploadBatch.dispose();
    };
  }, [studio.api, studio.project, uploadBatch]);
  useEffect(() => {
    if (!picking) return;
    const pick = (event: MouseEvent) => {
      const target = event.target as Element;
      if (target.closest("[data-assistant-surface],.assistant-page")) return;
      const selected = studio.evidence.pick(target);
      if (!selected) {
        studio.toast(
          "No current structured evidence is bound to that element",
          true,
        );
        return;
      }
      if (
        selected.project !== studio.project ||
        selected.user !== String(studio.user.id)
      )
        return;
      event.preventDefault();
      event.stopImmediatePropagation();
      controller.setOptions({
        selection: { ...selected, source_route: selected.route },
      });
      exitPicking();
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        exitPicking();
      }
    };
    document.addEventListener("click", pick, true);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("click", pick, true);
      document.removeEventListener("keydown", escape);
    };
  }, [picking, studio, controller]);
  useEffect(() => {
    let controller: AbortController | undefined;
    const reload = () => {
      controller?.abort();
      controller = new AbortController();
      void studio.api
        .get("/api/assistant/models", controller.signal)
        .then(setModels)
        .catch((error) => {
          if (error.name !== "AbortError") studio.toast(error.message, true);
        });
    };
    window.addEventListener("tinystudio:assistant-models-changed", reload);
    return () => {
      controller?.abort();
      window.removeEventListener("tinystudio:assistant-models-changed", reload);
    };
  }, [studio.api]);
  useEffect(() => {
    if (picking) exitPicking();
  }, [controller, route.pathname, route.search]);
  const providers = models
    ? Object.entries(models).filter(
        ([, value]: any) =>
          value && typeof value === "object" && Array.isArray(value.models),
      )
    : [];
  const selected = models?.[state.provider];
  const attach = async () => {
    if (uploadBatch.active || !files.length) return;
    const room = 8 - controller.getSnapshot().attachments.length;
    if (room <= 0) {
      studio.toast("At most 8 files per turn", true);
      return;
    }
    setUploadBusy(true);
    try {
      const result = await uploadBatch.run(
        files.slice(0, room),
        (file, signal, onProgress) => {
          const body = new FormData();
          body.append("file", file);
          return studio.api.upload<Attachment>(
            `/api/p/${encodeURIComponent(studio.project)}/assistant/attachments`,
            body,
            { signal, onProgress },
          );
        },
        (attachment) => {
          if (mounted.current)
            controller.setAttachments([
              ...controller.getSnapshot().attachments,
              attachment,
            ]);
        },
      );
      if (mounted.current)
        setFiles([...result.remaining, ...files.slice(room)]);
    } finally {
      if (mounted.current) setUploadBusy(false);
    }
  };
  const clear = async () => {
    if (
      await studio.confirm(
        "Clear this saved conversation? This affects your conversation in this project on every device.",
        "Clear conversation",
      )
    )
      try {
        await controller.clear();
      } catch (error) {
        studio.toast((error as Error).message, true);
      }
  };
  return (
    <div
      className="assistant-conversation text-body-regular"
      data-assistant-surface
    >
      <div className="assistant-settings">
        <div className="assistant-controls">
          <div className="assistant-select">
            <span className="text-body-medium">Provider</span>
            <Select
              aria-label="Provider"
              selectedKey={state.provider}
              isDisabled={state.busy}
              onSelectionChange={(key) =>
                controller.setOptions({ provider: String(key), model: "" })
              }
            >
              {(providers.length
                ? providers
                : [
                    ["anthropic", {}],
                    ["openai", {}],
                  ]
              ).map(([id, value]: any) => (
                <SelectItem
                  id={id}
                  key={id}
                  textValue={`${id}${value.available === false ? " · unavailable" : ""}`}
                >
                  {id}
                  {value.available === false ? " · unavailable" : ""}
                </SelectItem>
              ))}
            </Select>
          </div>
          <div className="assistant-select">
            <span className="text-body-medium">Model</span>
            <Select
              aria-label="Model"
              selectedKey={state.model || "__default__"}
              isDisabled={state.busy}
              onSelectionChange={(key) =>
                controller.setOptions({
                  model: key === "__default__" ? "" : String(key),
                })
              }
            >
              <SelectItem
                id="__default__"
                textValue={
                  selected?.default
                    ? `Provider default · ${selected.default}`
                    : "Provider default"
                }
              >
                {selected?.default
                  ? `Provider default · ${selected.default}`
                  : "Provider default"}
              </SelectItem>
              {(selected?.models || []).map((model: any) => (
                <SelectItem
                  id={model.id}
                  key={model.id}
                  textValue={model.name || model.id}
                >
                  {model.name || model.id}
                </SelectItem>
              ))}
            </Select>
          </div>
          <Button
            type="button"
            disabled={state.busy}
            onClick={() => void clear()}
          >
            Clear
          </Button>
        </div>
      </div>
      {selected?.error && <p role="alert">{selected.error}</p>}
      <p
        className="assistant-save text-caption-1-regular text-text-tertiary"
        role="status"
      >
        {!state.loaded
          ? "Loading conversation…"
          : state.busy
            ? "Request active"
            : state.dirty
              ? "Unsaved reply"
              : `Saved · revision ${state.revision}`}{" "}
        ·{" "}
        {state.sync === "error"
          ? "Sync unavailable; retrying"
          : state.sync === "offline"
            ? "Offline"
            : "Syncing automatically"}
      </p>
      {state.executionError && (
        <div role="alert" className="text-body-regular text-text-error-primary">
          {state.executionError}
        </div>
      )}
      {state.error && (
        <div role="alert">
          {state.error}
          <Button
            type="button"
            disabled={state.busy}
            onClick={() =>
              void (async () => {
                if (
                  !state.dirty ||
                  (await studio.confirm(
                    "Discard the unsaved local reply and reload the database conversation?",
                  ))
                )
                  await controller.refresh(true);
              })().catch((e) => studio.toast(e.message, true))
            }
          >
            Reload saved conversation
          </Button>
          {state.dirty && !state.conflict && !state.busy && (
            <Button
              type="button"
              onClick={() =>
                void controller
                  .save()
                  .catch((e) => studio.toast(e.message, true))
              }
            >
              Retry save
            </Button>
          )}
        </div>
      )}
      <AssistantScrollArea
        className="assistant-history"
        aria-live="polite"
        hidden={compact && !expanded}
        style={compact && !expanded ? { display: "none" } : undefined}
      >
        {state.history.length ? (
          state.history.map((message, i) => (
            <MessageView key={i} message={message} />
          ))
        ) : (
          <p>
            Ask about your models, datasets or an evaluation. Pick rendered
            evidence to include its exact source records.
          </p>
        )}
        {state.busy && (
          <div className="assistant-controls">
            <AgentThinking label="Request active" />
            {state.startedAt && <Elapsed startedAt={state.startedAt} />}
          </div>
        )}
        <AssistantActivity
          uiRequests={state.steps.filter((step) => step.type === "ui_request")}
        />
      </AssistantScrollArea>
      {state.usage && (
        <p className="assistant-save text-caption-1-regular text-text-tertiary">
          {state.usage.model} · {state.usage.input_tokens.toLocaleString()}{" "}
          input / {state.usage.output_tokens.toLocaleString()} output tokens
        </p>
      )}
      <div className="assistant-composer">
        <div className="assistant-options">
          <span className="assistant-context-route" title={studio.route}>
            Context · {studio.route || "/"}
          </span>
          {route.pathname === "/assistant" ? (
            <Button
              type="button"
              onClick={() => studio.navigate("#" + researchRoute)}
            >
              Return to research to pick data
            </Button>
          ) : (
            <Button
              type="button"
              aria-pressed={picking}
              onClick={() => (picking ? exitPicking() : beginPicking())}
            >
              {picking ? "Cancel pick" : "Pick page data"}
            </Button>
          )}
          <details className="assistant-turn-options">
            <summary>Turn options</summary>
            <Checkbox
              isSelected={state.webSearch}
              isDisabled={state.busy}
              onChange={(webSearch) => controller.setOptions({ webSearch })}
            >
              Provider web search
            </Checkbox>
            <Checkbox
              isSelected={state.allowActions}
              isDisabled={state.busy}
              onChange={(allowActions) =>
                controller.setOptions({ allowActions })
              }
            >
              Allow actions / deletions this turn
            </Checkbox>
          </details>
        </div>
        {route.pathname === "/assistant" && (
          <p className="text-caption-1-regular text-text-tertiary">
            On your research page, open Assistant and choose Pick page data to
            attach a row or chart.
          </p>
        )}
        {state.selection && (
          <div className="assistant-attachment text-caption-1-regular">
            Picked: {state.selection.label}
            <Button
              type="button"
              onClick={() => controller.setOptions({ selection: null })}
            >
              Remove evidence
            </Button>
          </div>
        )}
        <AssistantAttachments
          attachments={state.attachments}
          disabled={state.busy}
          onRemove={(id) =>
            controller.setAttachments(
              state.attachments.filter((attachment) => attachment.id !== id),
            )
          }
          onDelete={(id) =>
            void (async () => {
              const attachment = state.attachments.find(
                (item) => item.id === id,
              );
              if (
                attachment &&
                (await studio.confirm(
                  `Delete the private upload ${attachment.name}? Saved conversations retain its display metadata.`,
                ))
              ) {
                await studio.api.del(
                  `/api/p/${encodeURIComponent(studio.project)}/assistant/attachments/${id}`,
                );
                controller.setAttachments(
                  controller
                    .getSnapshot()
                    .attachments.filter((item) => item.id !== id),
                );
              }
            })().catch((error) => studio.toast(error.message, true))
          }
        />

        {config && (
          <details>
            <summary>
              Attach files · {Math.round(config.max_bytes / 1048576)} MiB
              maximum each
            </summary>
            <FileUploadField
              label="Assistant files"
              accept={config.accept}
              files={files}
              multiple
              maxBytes={config.max_bytes}
              onChange={setFiles}
              disabled={state.busy || uploadBusy}
            />
            <Button
              type="button"
              disabled={!files.length || state.busy || uploadBusy}
              onClick={() => void attach()}
            >
              Upload selected files
            </Button>
            {upload && (
              <UploadStatus
                {...upload}
                onCancel={() => uploadBatch.cancel()}
                onRetry={uploadBusy ? undefined : () => void attach()}
              />
            )}
          </details>
        )}
        <Textarea
          label="Message"
          ref={composer}
          value={state.draft}
          rows={3}
          onChange={(draft) => controller.setDraft(draft)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
              event.preventDefault();
              void controller
                .send()
                .catch((error) => studio.toast(error.message, true));
            }
          }}
        />
        <div className="assistant-send">
          {state.busy ? (
            <Button type="button" onClick={() => controller.cancel()}>
              Stop listening
            </Button>
          ) : (
            <Button
              type="button"
              variant="primary"
              disabled={!state.loaded || state.dirty || !state.draft.trim()}
              onClick={() =>
                void controller
                  .send()
                  .catch((error) => studio.toast(error.message, true))
              }
            >
              Send
            </Button>
          )}
          <span>⌘ / Ctrl + Enter</span>
        </div>
      </div>
    </div>
  );
}
```

