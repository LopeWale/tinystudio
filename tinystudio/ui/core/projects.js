import { $ } from "../ui/dom.js?v=c96cb305d835";

const PROJECT_KEY = "tinystudio.project";
export function createProjectController({ state, http, dialogs, feedback, onProjectChange }) {
  async function selectProject(slug) {
    state.projects = await http.api("/api/projects");
    if (!state.projects.some((pr) => pr.slug === slug)) slug = state.projects[0]?.slug || null;
    state.project = slug; onProjectChange();
    try { if (slug) localStorage.setItem(PROJECT_KEY, slug); } catch (_) {}
    return slug;
  }
  async function ensureProject() {
    let wanted = state.project; try { wanted = wanted || localStorage.getItem(PROJECT_KEY); } catch (_) {}
    await selectProject(wanted);
    if (!state.project) {
      await new Promise((resolve) => {
        const m = dialogs.modal({ title: "Create your first project", body: `<p class="muted">A project holds datasets, snapshots, jobs, evaluations, traces, and repositories. You can add more later from the project menu.</p><div class="field"><label>Name</label><input id="prName" placeholder="support-bot"></div><div class="field"><label>Goal</label><textarea id="prGoal" placeholder="Answer questions about our product from the manual"></textarea></div>`, footer: `<button class="btn primary" data-ok>Create project</button>` });
        $("[data-ok]", m.el).onclick = feedback.guard(async () => { const pr = await http.post("/api/projects", { name: $("#prName", m.el).value.trim(), goal: $("#prGoal", m.el).value.trim() }); m.close(); await selectProject(pr.slug); resolve(); });
      });
    }
    return state.project;
  }
  return { selectProject, ensureProject };
}
