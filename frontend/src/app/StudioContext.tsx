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
