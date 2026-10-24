import { createAppState } from "./core/state.js?v=82e74d4b5ecb";
import { createApiClient } from "./core/api.js?v=82e74d4b5ecb";
import { HashRouter } from "./core/router.js?v=82e74d4b5ecb";
import { createProjectController } from "./core/projects.js?v=82e74d4b5ecb";
import { createAuthViews } from "./core/auth.js?v=82e74d4b5ecb";
import { createTaskDrawer } from "./core/tasks.js?v=82e74d4b5ecb";
import { createFeedback } from "./ui/feedback.js?v=82e74d4b5ecb";
import * as dialogUI from "./ui/dialogs.js?v=82e74d4b5ecb";
import { $$ } from "./ui/dom.js?v=82e74d4b5ecb";
import { esc } from "./ui/format.js?v=82e74d4b5ecb";
import { createShell } from "./ui/layout.js?v=82e74d4b5ecb";
import { createAssistant } from "./assistant/index.js?v=82e74d4b5ecb";
import { createRoutes } from "./routes.js?v=82e74d4b5ecb";
import { createDatasetsPages } from "./pages/datasets.js?v=82e74d4b5ecb";
import { createTrainingPages } from "./pages/training.js?v=82e74d4b5ecb";
import { createEvaluationsPages } from "./pages/evaluations.js?v=82e74d4b5ecb";
import { createMetricsPages } from "./pages/metrics.js?v=82e74d4b5ecb";
import { createPlaygroundPages } from "./pages/playground.js?v=82e74d4b5ecb";
import { createModelsPages } from "./pages/models.js?v=82e74d4b5ecb";
import { createRepositoriesPages } from "./pages/repositories.js?v=82e74d4b5ecb";
import { createSettingsPages } from "./pages/settings.js?v=82e74d4b5ecb";
import { createUsersPages } from "./pages/users.js?v=82e74d4b5ecb";
import { createProjectsPages } from "./pages/projects.js?v=82e74d4b5ecb";
import { createTracesPages } from "./pages/traces.js?v=82e74d4b5ecb";
import { createInterpretabilityPages } from "./pages/interpretability.js?v=82e74d4b5ecb";
import { createStatsPages } from "./pages/stats.js?v=82e74d4b5ecb";
import { createKeysPages } from "./pages/keys.js?v=82e74d4b5ecb";

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
  pages.datasets = createDatasetsPages({ ...common, createJobDialog: (...args) => pages.training.createJobDialog(...args) });
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
    beforeRender: (hash) => { pages.playground.onRouteChange(hash); const view = assistant.beginRender(hash); $$(".modal-back").forEach((el) => el.remove()); return view; },
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
  function destroy() { ready = false; state.pageContext = null; state.domContext = null; router.destroy(); assistant.destroy(); tasks.destroy(); pages.playground.destroy(); }
  return { state, http, overview, navigation, shell, feedback, dialogs, projects, auth, tasks, assistant, pages, router, routes, start, destroy };
}
