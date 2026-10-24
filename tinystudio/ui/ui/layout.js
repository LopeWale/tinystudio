import { $ } from "./dom.js?v=82e74d4b5ecb";
import { esc } from "./format.js?v=82e74d4b5ecb";

export const ICONS = {
  datasets: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/></svg>',
  finetune: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="12" cy="18" r="2.5"/><path d="M8 7.5l3 8M16 7.5l-3 8M8.5 6h7"/></svg>',
  evals: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 12l3 3 5-6"/></svg>',
  play: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 5h16v11H9l-5 4z"/></svg>',
  models: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2l9 5-9 5-9-5 9-5z"/><path d="M3 12l9 5 9-5M3 17l9 5 9-5"/></svg>',
  keys: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="8" cy="12" r="4"/><path d="M12 12h9M18 12v3M15 12v2"/></svg>',
  stats: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></svg>',
  traces: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 12h4l3-8 4 16 3-8h4"/></svg>',
  docs: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 3h9l5 5v13H6z"/><path d="M14 3v6h6M9 13h7M9 17h7"/></svg>',
  assistant: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3l1.8 4.6L18 9l-4.2 1.4L12 15l-1.8-4.6L6 9l4.2-1.4z"/><path d="M5 17l.8 2 2 .8-2 .8L5 22l-.8-1.6-2-.8 2-.8z"/></svg>',
  settings: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3h0a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8v0a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>',
  interp: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 9V4M12 20v-5M9 12H3M21 12h-6M9.9 9.9L6 6M18 18l-3.9-3.9M14.1 9.9L18 6M6 18l3.9-3.9"/></svg>',
};

export function createShell({ state, feedback, dialogs, http, navigation, selectProject, newProjectModal }) {
  function sidebar(active) {
    const o = state.overview || {};
    const item = (hash, key, label, icon) => `<a href="${hash}" class="${active === key ? "active" : ""}">${ICONS[icon]}<span>${label}</span></a>`;
    return `<aside class="sidebar">
      <div class="brand"><div class="logo"><svg width="16" height="16" viewBox="0 0 32 32"><path d="M8 22 L13 12 L18 18 L24 9" fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg></div><div>tinystudio<small>${esc(o.project?.name || "")}</small></div></div>
      <div class="project-switch"><select id="projectSwitch" title="Project">${state.projects.map((pr) => `<option value="${esc(pr.slug)}" ${pr.slug === state.project ? "selected" : ""}>${esc(pr.name)}</option>`).join("")}<option value="__new__">+ New project…</option><option value="__all__">Manage projects…</option></select></div>
      <nav class="nav">
        ${item("#/datasets", "datasets", "Datasets", "datasets")}
        ${item("#/repositories", "repositories", "Repositories", "docs")}
        ${item("#/finetuning", "finetuning", "Finetuning", "finetune")}
        ${item("#/evaluations", "evaluations", "Evaluations", "evals")}
        <div class="sub">${item("#/evaluations", "evaluations-sub", "Eval", "evals").replace(ICONS.evals, "")}${item("#/metrics", "metrics", "Metrics", "evals").replace(ICONS.evals, "")}</div>
        ${item("#/playground", "playground", "Playground", "play")}
        ${item("#/assistant", "assistant", "Assistant", "assistant")}
        ${item("#/interp", "interp", "Interpretability", "interp")}
        <hr>
        ${item("#/models", "models", "Models", "models")}
        ${item("#/traces", "traces", "Traces", "traces")}
        ${item("#/api-keys", "keys", "API Keys", "keys")}
        ${item("#/stats", "stats", "Stats", "stats")}
        ${state.user?.role === "admin" ? item("#/settings", "settings", "Settings", "settings") : ""}
        ${state.user?.role === "admin" ? item("#/users", "users", "Users", "keys") : ""}
        <hr>
        <a href="/docs" target="_blank">${ICONS.docs}<span>API reference</span><span class="external">↗</span></a>
      </nav>
      <div class="foot"><div class="avatar">${esc((state.user?.name || state.user?.email || "t")[0].toUpperCase())}</div><div style="min-width:0"><div style="overflow:hidden;text-overflow:ellipsis">${esc(state.user?.name || state.user?.email || "")}</div><div class="mono" style="font-size:11px">${esc(state.user?.role || "")} · <a href="#" id="signOut">sign out</a></div></div></div>
    </aside>`;
  }
  function layout(active, contentHtml, crumbs = []) {
    const app = $("#app");
    app.innerHTML = `${sidebar(active)}<div class="main"><div class="topbar">${crumbs.map((c, i) => `${i ? '<span class="sep">›</span>' : ""}${c.href ? `<a href="${c.href}">${esc(c.label)}</a>` : `<span>${esc(c.label)}</span>`}`).join("")}<span class="spacer"></span><span id="llmStatus"></span></div><div class="content">${contentHtml}</div></div>`;
    state.domContext = state.pageContext;
    const sw = $("#projectSwitch", app);
    if (sw) sw.onchange = feedback.guard(async () => {
      if (sw.value === "__new__") { sw.value = state.project; return newProjectModal(); }
      if (sw.value === "__all__") { sw.value = state.project; return navigation.go("#/projects"); }
      await selectProject(sw.value); state.overview = null; navigation.go("#/datasets"); navigation.render();
    });
    const out = $("#signOut", app);
    if (out) out.onclick = feedback.guard(async (e) => { e.preventDefault(); const result = await http.post("/api/auth/logout", {}); if (result.logout_url) location.assign(result.logout_url); else location.reload(); });
    return $(".content", app);
  }
  return { sidebar, layout };
}
