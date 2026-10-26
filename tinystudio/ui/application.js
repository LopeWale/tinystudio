import { createAppState } from "./core/state.js?v=c96cb305d835";
import { createApiClient } from "./core/api.js?v=c96cb305d835";
import { HashRouter } from "./core/router.js?v=c96cb305d835";
import { createProjectController } from "./core/projects.js?v=c96cb305d835";
import { createAuthViews } from "./core/auth.js?v=c96cb305d835";
import { createTaskDrawer } from "./core/tasks.js?v=c96cb305d835";
import { createFeedback } from "./ui/feedback.js?v=c96cb305d835";
import * as dialogUI from "./ui/dialogs.js?v=c96cb305d835";
import { $$ } from "./ui/dom.js?v=c96cb305d835";
import { esc } from "./ui/format.js?v=c96cb305d835";
import { createShell } from "./ui/layout.js?v=c96cb305d835";
import { createAssistant } from "./assistant/index.js?v=c96cb305d835";
import { createRoutes } from "./routes.js?v=c96cb305d835";
import { createDatasetsPages } from "./pages/datasets.js?v=c96cb305d835";
import { createTrainingPages } from "./pages/training.js?v=c96cb305d835";
import { createEvaluationsPages } from "./pages/evaluations.js?v=c96cb305d835";
import { createMetricsPages } from "./pages/metrics.js?v=c96cb305d835";
import { createPlaygroundPages } from "./pages/playground.js?v=c96cb305d835";
import { createModelsPages } from "./pages/models.js?v=c96cb305d835";
import { createRepositoriesPages } from "./pages/repositories.js?v=c96cb305d835";
import { createSettingsPages } from "./pages/settings.js?v=c96cb305d835";
import { createUsersPages } from "./pages/users.js?v=c96cb305d835";
import { createProjectsPages } from "./pages/projects.js?v=c96cb305d835";
import { createTracesPages } from "./pages/traces.js?v=c96cb305d835";
import { createInterpretabilityPages } from "./pages/interpretability.js?v=c96cb305d835";
import { createStatsPages } from "./pages/stats.js?v=c96cb305d835";
import { createKeysPages } from "./pages/keys.js?v=c96cb305d835";

// This composition root is the only place that knows all features.
export function createApplication() {
  const state = createAppState(), feedback = createFeedback(), dialogs = { ...dialogUI };
  let assistant, auth, router, projects, ready = false;
  const pages = {};
  const http = createApiClient({
    getProject: () => state.project, getCapture: () => state.pageContext,
    assertRender: (...args) => assistant.context.assertRender(...args),
    recordApi: (...args) => assistant.context.recordApi(...args),
    onUnauthorized: () => {
      state.user = null; state.overview = null; state.tasks = []; destroy(); auth.showLogin();
    },
  });
  const overview = { async ensureOverview(force = false) { if (!state.overview || force) state.overview = await http.api("/api/overview"); return state.overview; } };
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
  });
  let started = false;
  async function start() {
    if (started) return; started = true;
    const status = await http.api("/api/auth/status");
    if (status.setup_required) return auth.showSetup();
    if (!status.authenticated) return auth.showLogin();
    state.user = status.user;
    await projects.ensureProject(); await overview.ensureOverview();
    router.start(); assistant.mountAssistant(); await router.render(); ready = true; tasks.pollTasks();
  }
  function destroy() { ready = false; state.pageContext = null; state.domContext = null; router.destroy(); assistant.destroy(); tasks.destroy(); pages.playground.destroy(); dialogs.closeAll(); }
  return { state, http, overview, navigation, shell, feedback, dialogs, projects, auth, tasks, assistant, pages, router, routes, start, destroy };
}
