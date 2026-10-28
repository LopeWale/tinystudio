import { createAppState } from "./core/state.js?v=c1757fa5eddd";
import { createApiClient } from "./core/api.js?v=c1757fa5eddd";
import { HashRouter } from "./core/router.js?v=c1757fa5eddd";
import { createProjectController, ProjectSetupCancelledError } from "./core/projects.js?v=c1757fa5eddd";
import { createAuthViews } from "./core/auth.js?v=c1757fa5eddd";
import { createTaskDrawer } from "./core/tasks.js?v=c1757fa5eddd";
import { createFeedback } from "./ui/feedback.js?v=c1757fa5eddd";
import * as dialogUI from "./ui/dialogs.js?v=c1757fa5eddd";
import { esc } from "./ui/format.js?v=c1757fa5eddd";
import { createShell } from "./ui/layout.js?v=c1757fa5eddd";
import { createAssistant } from "./assistant/index.js?v=c1757fa5eddd";
import { createRoutes } from "./routes.js?v=c1757fa5eddd";
import { createDatasetsPages } from "./pages/datasets.js?v=c1757fa5eddd";
import { createTrainingPages } from "./pages/training.js?v=c1757fa5eddd";
import { createEvaluationsPages } from "./pages/evaluations.js?v=c1757fa5eddd";
import { createMetricsPages } from "./pages/metrics.js?v=c1757fa5eddd";
import { createPlaygroundPages } from "./pages/playground.js?v=c1757fa5eddd";
import { createModelsPages } from "./pages/models.js?v=c1757fa5eddd";
import { createRepositoriesPages } from "./pages/repositories.js?v=c1757fa5eddd";
import { createSettingsPages } from "./pages/settings.js?v=c1757fa5eddd";
import { createUsersPages } from "./pages/users.js?v=c1757fa5eddd";
import { createProjectsPages } from "./pages/projects.js?v=c1757fa5eddd";
import { createTracesPages } from "./pages/traces.js?v=c1757fa5eddd";
import { createInterpretabilityPages } from "./pages/interpretability.js?v=c1757fa5eddd";
import { createStatsPages } from "./pages/stats.js?v=c1757fa5eddd";
import { createKeysPages } from "./pages/keys.js?v=c1757fa5eddd";

// This composition root is the only place that knows all features.
export function createApplication() {
  const state = createAppState(), feedback = createFeedback(), dialogs = { ...dialogUI };
  let assistant, auth, router, projects, ready = false;
  let started = false, disposed = false;
  function assertActive() { if (disposed) throw Object.assign(new Error("Application disposed"), { name: "StaleRenderError" }); }
  const pages = {};
  const http = createApiClient({
    getProject: () => state.project, getCapture: () => state.pageContext,
    assertRender: (...args) => assistant.context.assertRender(...args),
    recordApi: (...args) => assistant.context.recordApi(...args),
    onUnauthorized: () => {
      state.user = null; state.overview = null; state.tasks = []; destroy(); auth.showLogin();
    },
  });
  const overview = { async ensureOverview(force = false) {
    assertActive();
    if (!state.overview || force) {
      const next = await http.api("/api/overview");
      assertActive(); state.overview = next;
    }
    return state.overview;
  } };
  const ensureOverview = (...args) => overview.ensureOverview(...args);
  const navigation = { go: (...args) => router.go(...args), render: (...args) => router.render(...args) };
  const shell = createShell({ state, feedback, dialogs, http, navigation, selectProject: (...args) => projects.selectProject(...args), newProjectModal: (...args) => pages.projects.newProjectModal(...args) });
  const tasks = createTaskDrawer({ state, http, feedback, navigation });
  assistant = createAssistant({ state, http, feedback, dialogs, shell, navigation, ensureOverview, pollTasks: (...args) => tasks.pollTasks(...args) });
  projects = createProjectController({ state, http, dialogs, feedback, onProjectChange: () => { assistant.syncAssistantScope(); tasks.syncScope(); pages.playground?.syncScope(); if (ready) tasks.pollTasks(); } });
  auth = createAuthViews({ http });
  const common = { state, http, feedback, dialogs, shell, navigation, context: assistant.context, ensureOverview, showTasks: (...args) => tasks.showTasks(...args), pollTasks: (...args) => tasks.pollTasks(...args), queued: (...args) => tasks.queued(...args) };
  pages.datasets = createDatasetsPages({ ...common, holdTaskRefresh: (...args) => tasks.holdRefresh(...args), createJobDialog: (...args) => pages.training.createJobDialog(...args) });
  pages.training = createTrainingPages({ ...common, newEvaluationDialog: (...args) => pages.evaluations.newEvaluationDialog(...args), checkpointCaptureDialog: (...args) => pages.interpretability.checkpointCaptureDialog(...args) });
  pages.evaluations = createEvaluationsPages(common);
  pages.metrics = createMetricsPages(common);
  pages.playground = createPlaygroundPages(common);
  pages.models = createModelsPages(common);
  pages.repositories = createRepositoriesPages(common);
  pages.settings = createSettingsPages({ ...common, resetAssistantModels: () => { assistant.state.models = null; } });
  pages.users = createUsersPages(common);
  pages.projects = createProjectsPages({ ...common, selectProject: (...args) => projects.selectProject(...args), ensureProject: (...args) => projects.ensureProject(...args) });
  pages.traces = createTracesPages(common);
  pages.interpretability = createInterpretabilityPages(common);
  pages.stats = createStatsPages(common);
  pages.keys = createKeysPages(common);
  const routePages = Object.assign({}, ...Object.values(pages), { pageAssistant: (...args) => assistant.pageAssistant(...args) });
  const routes = createRoutes(routePages, navigation.go);
  router = new HashRouter({ routes,
    beforeRender: (hash) => { pages.playground.onRouteChange(hash); const view = assistant.beginRender(hash); dialogs.closeAll(); return view; },
    isCurrent: (view) => assistant.context.isCurrentRender(view),
    onError: (error) => { feedback.toast(error.message, true); shell.layout("Error", `<div class="error">${esc(error.message)}</div>`); },
    onLoading: (loading) => shell.setLoading(loading),
  });
  async function start() {
    if (started || disposed) return; started = true;
    try {
      const status = await http.api("/api/auth/status");
      if (disposed) return;
      if (status.setup_required) return auth.showSetup();
      if (!status.authenticated) return auth.showLogin();
      state.user = status.user;
      await projects.ensureProject(); if (disposed) return;
      await overview.ensureOverview(); if (disposed) return;
      router.start(); assistant.mountAssistant(); await router.render();
      if (disposed) return;
      ready = true; tasks.pollTasks();
    } catch (error) {
      if (disposed) return;
      if (error instanceof ProjectSetupCancelledError) {
        started = false;
        const card = auth.authPage("Create your first project", `<p class="page-description">A project keeps your datasets, models, and experiments together. Create one to open your workspace.</p><button class="btn primary auth-submit" id="resumeProjectSetup" type="button">Create project</button>`);
        card.querySelector("#resumeProjectSetup").onclick = () => start();
        return;
      }
      if (error.name !== "AuthenticationRequiredError" && error.name !== "StaleRenderError" && !disposed) { started = false; auth.showUnavailable(); }
    }
  }
  function destroy() { disposed = true; ready = false; projects.destroy(); state.pageContext = null; state.domContext = null; router.destroy(); assistant.destroy(); tasks.destroy(); pages.playground.destroy(); shell.destroy(); dialogs.closeAll(); }
  return { state, http, overview, navigation, shell, feedback, dialogs, projects, auth, tasks, assistant, pages, router, routes, start, destroy };
}
