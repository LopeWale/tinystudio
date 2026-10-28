import { useCallback, useMemo, useRef, useState, type MouseEvent } from "react";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { Shell } from "../app/App";
import { StudioContext, type StudioRuntime } from "../app/StudioContext";
import { EvidenceRegistry } from "../app/evidence";
import { PlaygroundProvider } from "../app/PlaygroundProvider";
import { AssistantProvider } from "../features/assistant";
import { Button, Modal } from "../components/studio/primitives";
import {
  createWorkspacePreviewApi,
  denyPreviewNetwork,
  previewRefusal,
} from "./workspace-preview-api";
import {
  previewOverview,
  previewProject,
  previewProjects,
} from "./workspace-preview-fixtures";
import "./workspace-preview.css";

/** A development-only illustrative harness around the actual application shell.
 * Memory routing keeps all navigation beneath the original #/__preview entry. */
export function WorkspacePreview({
  initialRoute = "/datasets",
}: {
  initialRoute?: string;
}) {
  if (!import.meta.env.DEV) return null;
  return (
    <MemoryRouter initialEntries={[initialRoute]}>
      <PreviewRuntime />
    </MemoryRouter>
  );
}
function PreviewRuntime() {
  const location = useLocation(),
    go = useNavigate();
  const [api] = useState(createWorkspacePreviewApi);
  const [evidence] = useState(() => new EvidenceRegistry());
  const [project, selectProject] = useState(previewProject);
  const [notice, setNotice] = useState("");
  const [tasksOpen, setTasksOpen] = useState(false);
  const route = location.pathname + location.search;
  const confirm = useCallback(async () => {
    setNotice(previewRefusal);
    return false;
  }, []);
  const evidenceScope = useRef("");
  if (evidenceScope.current !== `${project}:${route}`) {
    evidenceScope.current = `${project}:${route}`;
    evidence.clear();
  }
  const runtime = useMemo<StudioRuntime>(
    () => ({
      api,
      project,
      user: {
        id: "preview-user",
        name: "Sample reviewer",
        email: "reviewer@example.invalid",
        role: "admin",
      },
      overview: previewOverview,
      route,
      evidence,
      scopeKey: `illustrative:${project}`,
      projects: previewProjects,
      selectProject,
      refreshOverview: async () => {},
      refreshProjects: async () => {},
      navigate: (hash) => go(hash.replace(/^#/, "")),
      toast: (message) => setNotice(message),
      confirm,
      trackTask: () => setNotice(previewRefusal),
      showTasks: () => setTasksOpen(true),
      signOut: async () => {
        throw new Error(previewRefusal);
      },
      onSessionExpired: () => setNotice(previewRefusal),
    }),
    [api, project, route, evidence, go, confirm],
  );
  // Product detail/export links use native anchors. Keep local route links in
  // MemoryRouter and explicitly refuse links that would contact any server.
  const captureLink = (event: MouseEvent<HTMLDivElement>) => {
    const link = (event.target as Element).closest?.("a");
    const href = link?.getAttribute("href");
    if (!href) return;
    if (href.startsWith("#/")) {
      event.preventDefault();
      event.stopPropagation();
      go(href.slice(1));
    } else if (href === "#main-content") {
      event.preventDefault();
      document.getElementById("main-content")?.focus();
    } else if (/^(?:\/api\/|https?:|blob:|data:|\/\/)/i.test(href)) {
      event.preventDefault();
      event.stopPropagation();
      setNotice(previewRefusal);
    }
  };
  return (
    <StudioContext.Provider value={runtime}>
      <div
        className="illustrative-workspace-preview"
        onClickCapture={captureLink}
        onSubmitCapture={(event) => event.preventDefault()}
      >
        <div className="illustrative-preview-banner" role="note">
          Illustrative preview · no server connection
        </div>
        <AssistantProvider fetcher={denyPreviewNetwork}>
          <PlaygroundProvider fetcher={denyPreviewNetwork}>
            <Shell />
          </PlaygroundProvider>
        </AssistantProvider>
        {notice && (
          <div className="toast-region" aria-live="polite">
            <div className="toast">
              {notice}
              <Button
                size="sm"
                variant="ghost"
                aria-label="Dismiss preview notice"
                onClick={() => setNotice("")}
              >
                ×
              </Button>
            </div>
          </div>
        )}
        {tasksOpen && (
          <Modal
            open
            title="Illustrative background tasks"
            onClose={() => setTasksOpen(false)}
          >
            <p>No tasks execute in this preview.</p>
          </Modal>
        )}
      </div>
    </StudioContext.Provider>
  );
}
