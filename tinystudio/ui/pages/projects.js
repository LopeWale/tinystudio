import { $, $$ } from "../ui/dom.js?v=c96cb305d835";
import { esc, fmtDate, slug } from "../ui/format.js?v=c96cb305d835";

// Owns this feature's page rendering and dialogs; application services stay at explicit boundaries.
export function createProjectsPages({ state, http, feedback, dialogs, shell, navigation, context, ensureOverview, selectProject, ensureProject }) {
  const api = (...args) => http.api(...args);
  const post = (...args) => http.post(...args);
  const del = (...args) => http.del(...args);
  const guard = (...args) => feedback.guard(...args);
  const modal = (...args) => dialogs.modal(...args);
  const confirmModal = (...args) => dialogs.confirmModal(...args);
  const layout = (...args) => shell.layout(...args);
  const go = (...args) => navigation.go(...args);
  const render = (...args) => navigation.render(...args);
  const dataContext = (...args) => context.dataContext(...args);
  const assertRender = (...args) => context.assertRender(...args);

  async function pageProjects() {
    const view = state.pageContext;
    await ensureOverview(true); assertRender(view);
    const projects = await api("/api/projects");
    assertRender(view);
    const content = layout("projects", `<div class="page-head page-intro"><div class="page-heading"><span class="page-eyebrow">Your workspace</span><h1>Projects</h1><p class="sub">Keep each model goal together with its datasets, experiments, and results.</p></div><div class="actions"><button class="btn primary" id="newProject">+ New project</button></div></div>
      <div class="resource-grid">${projects.map((pr) => `<div ${dataContext("Project", pr)} class="card resource-card" data-proj="${esc(pr.slug)}"><h3><a class="project-open" href="#/datasets" data-open-proj="${esc(pr.slug)}">${esc(pr.name)}</a> ${pr.slug === state.project ? '<span class="pill info">current</span>' : ""}</h3><p class="sub">${esc(pr.goal || "Add a goal to describe what this model should become good at.")}</p><div class="resource-meta"><span>${pr.datasets} datasets</span><span>${pr.jobs} jobs</span></div><div class="foot"><span>Created ${fmtDate(pr.created)}</span>${state.user.role === "admin" && projects.length > 1 ? `<button class="btn sm danger" data-del-proj="${esc(pr.slug)}">Delete</button>` : ""}</div></div>`).join("") || `<div class="empty-state"><h3>No projects yet</h3><p class="empty-copy">Create a project and give your model a goal to work toward.</p></div>`}</div>`, [{ label: "Projects" }]);
    $("#newProject", content).onclick = newProjectModal;
    $$("[data-proj]", content).forEach((c) => c.onclick = guard(async (e) => { if (e.target.closest("button")) return; await selectProject(c.dataset.proj); state.overview = null; go("#/datasets"); render(); }));
    $$("[data-open-proj]", content).forEach((a) => a.onclick = guard(async (e) => { e.preventDefault(); e.stopPropagation(); await selectProject(a.dataset.openProj); state.overview = null; go("#/datasets"); render(); }));
    $$("[data-del-proj]", content).forEach((b) => b.onclick = (e) => { e.stopPropagation(); confirmModal("Delete project", `Delete project ${b.dataset.delProj} with all its datasets, jobs, weights, evaluations, traces, and repositories?`, async () => { await del(`/api/p/${encodeURIComponent(b.dataset.delProj)}`); if (state.project === b.dataset.delProj) state.project = null; await ensureProject(); pageProjects(); }); });
  }

  function newProjectModal() {
    const m = modal({ title: "New project", body: `<div class="field"><label for="prName">Name</label><input id="prName" placeholder="support-bot"></div><div class="field"><label for="prGoal">Goal (what the model should become good at)</label><textarea id="prGoal"></textarea></div>`, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Create</button>` });
    $("[data-cancel]", m.el).onclick = m.close;
    $("[data-ok]", m.el).onclick = guard(async () => { const pr = await post("/api/projects", { name: $("#prName", m.el).value.trim(), goal: $("#prGoal", m.el).value.trim() }); m.close(); await selectProject(pr.slug); state.overview = null; go("#/datasets"); render(); });
  }

  return { pageProjects, newProjectModal };
}
