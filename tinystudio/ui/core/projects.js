import { $ } from "../ui/dom.js?v=c1757fa5eddd";

const PROJECT_KEY = "tinystudio.project";
export class ProjectSetupCancelledError extends Error {
  constructor() { super("Create a project to open your workspace"); this.name = "ProjectSetupCancelledError"; }
}
class ProjectSetupAttempt {
  #active = true;
  #pending = false;
  get active() { return this.#active; }
  cancel() { this.#active = false; }
  assertCurrent = () => { if (!this.#active) throw Object.assign(new Error("Project setup attempt ended"), { name: "StaleRenderError" }); };
  begin() { this.assertCurrent(); if (this.#pending) return false; this.#pending = true; return true; }
  finish() { this.#pending = false; }
}
export function createProjectController({ state, http, dialogs, feedback, onProjectChange }) {
  let disposed = false;
  const cancelled = () => Object.assign(new Error("Project controller disposed"), { name: "StaleRenderError" });
  function assertActive() { if (disposed) throw cancelled(); }
  async function selectProject(slug, assertSelection = () => {}) {
    assertActive(); assertSelection();
    const next = await http.api("/api/projects");
    assertSelection();
    assertActive(); state.projects = next;
    if (!state.projects.some((pr) => pr.slug === slug)) slug = state.projects[0]?.slug || null;
    state.project = slug; onProjectChange();
    try { if (slug) localStorage.setItem(PROJECT_KEY, slug); } catch (_) {}
    return slug;
  }
  async function ensureProject() {
    assertActive();
    let wanted = state.project; try { wanted = wanted || localStorage.getItem(PROJECT_KEY); } catch (_) {}
    await selectProject(wanted);
    assertActive();
    if (!state.project) {
      await new Promise((resolve, reject) => {
        const attempt = new ProjectSetupAttempt();
        const m = dialogs.modal({ onClose: () => { attempt.cancel(); reject(disposed ? cancelled() : new ProjectSetupCancelledError()); }, title: "Create your first project", body: `<p class="muted">A project holds datasets, snapshots, jobs, evaluations, traces, and repositories. You can add more later from the project menu.</p><div class="field"><label for="prName">Name</label><input id="prName" placeholder="support-bot"></div><div class="field"><label for="prGoal">Goal</label><textarea id="prGoal" placeholder="Answer questions about our product from the manual"></textarea></div>`, footer: `<button class="btn primary" data-ok>Create project</button>` });
        const button = $("[data-ok]", m.el);
        button.onclick = feedback.guard(async () => {
          assertActive(); if (!attempt.begin()) return;
          button.disabled = true;
          try {
            const pr = await http.post("/api/projects", { name: $("#prName", m.el).value.trim(), goal: $("#prGoal", m.el).value.trim() });
            assertActive(); attempt.assertCurrent();
            await selectProject(pr.slug, attempt.assertCurrent);
            assertActive(); attempt.assertCurrent(); resolve(); m.close();
          } catch (error) { if (attempt.active) throw error; }
          finally { attempt.finish(); button.disabled = false; }
        });
      });
    }
    assertActive(); return state.project;
  }
  return { selectProject, ensureProject, destroy() { disposed = true; } };
}
