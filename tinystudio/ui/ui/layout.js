import { $ } from "./dom.js?v=c1757fa5eddd";
import { esc } from "./format.js?v=c1757fa5eddd";

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

const APPEARANCE_KEY = "tinystudio.appearance";
const PAGE_NAMES = { datasets: "Datasets", repositories: "Repositories", finetuning: "Finetuning", evaluations: "Evaluations", metrics: "Metrics", playground: "Playground", assistant: "Assistant", interp: "Interpretability", models: "Models", traces: "Traces", keys: "API Keys", stats: "Stats", settings: "Settings", users: "Users", projects: "Projects" };

export function createShell({ state, feedback, dialogs, http, navigation, selectProject, newProjectModal }) {
  let detach = () => {}, observer = null, preferred = "system", loading = false;
  try { preferred = localStorage.getItem(APPEARANCE_KEY) || "system"; } catch (_) {}
  function applyAppearance(value) {
    preferred = ["light", "dark"].includes(value) ? value : "system";
    const root = document.documentElement;
    if (root) { if (preferred === "system") delete root.dataset.theme; else root.dataset.theme = preferred; }
  }
  applyAppearance(preferred);
  function sidebar(active) {
    const item = (hash, key, label, icon) => `<a href="${hash}" class="${active === key ? "active" : ""}" ${active === key ? 'aria-current="page"' : ""}>${ICONS[icon]}<span>${label}</span></a>`;
    const group = (label, links) => `<div class="nav-label">${label}</div>${links.join("")}`;
    return `<aside class="sidebar" id="workspaceNav" aria-label="Workspace navigation" data-open="false">
      <div class="brand"><div class="logo" aria-hidden="true"><svg width="16" height="16" viewBox="0 0 32 32"><path d="M8 22 L13 12 L18 18 L24 9" fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg></div><div>tinystudio<small>Your model workspace</small></div><button type="button" class="btn icon nav-close" id="navClose" aria-label="Close navigation">×</button></div>
      <div class="project-switch"><label for="projectSwitch">Current project</label><select id="projectSwitch" title="Project">${state.projects.map((pr) => `<option value="${esc(pr.slug)}" ${pr.slug === state.project ? "selected" : ""}>${esc(pr.name)}</option>`).join("")}<option value="__new__">+ New project…</option><option value="__all__">Manage projects…</option></select></div>
      <nav class="nav" aria-label="Main navigation">
        ${group("Build", [item("#/datasets", "datasets", "Datasets", "datasets"), item("#/repositories", "repositories", "Repositories", "docs")])}
        ${group("Train & evaluate", [item("#/finetuning", "finetuning", "Finetuning", "finetune"), item("#/evaluations", "evaluations", "Evaluations", "evals"), item("#/metrics", "metrics", "Metrics", "evals")])}
        ${group("Explore", [item("#/playground", "playground", "Playground", "play"), item("#/assistant", "assistant", "Assistant", "assistant"), item("#/interp", "interp", "Interpretability", "interp")])}
        ${group("Serve & improve", [item("#/models", "models", "Models", "models"), item("#/traces", "traces", "Traces", "traces")])}
        ${group("Workspace", [item("#/projects", "projects", "Projects", "docs"), item("#/api-keys", "keys", "API Keys", "keys"), item("#/stats", "stats", "Stats", "stats"), ...(state.user?.role === "admin" ? [item("#/settings", "settings", "Settings", "settings"), item("#/users", "users", "Users", "keys")] : [])])}
        <hr><a href="/docs" target="_blank" rel="noopener">${ICONS.docs}<span>API reference</span><span class="external" aria-hidden="true">↗</span></a>
      </nav>
      <div class="foot"><div class="avatar" aria-hidden="true">${esc((state.user?.name || state.user?.email || "t")[0].toUpperCase())}</div><div class="account-info"><div class="account-name">${esc(state.user?.name || state.user?.email || "")}</div><div class="account-role">${esc(state.user?.role || "")} · <a href="#" id="signOut">sign out</a></div></div></div>
    </aside>`;
  }
  function setLoading(value) {
    loading = value;
    const app = $("#app"), progress = $("#routeProgress");
    app?.setAttribute?.("aria-busy", String(loading));
    if (progress) progress.hidden = !loading;
  }
  function enhanceTables(content) {
    for (const table of content.querySelectorAll?.("table") || []) {
      let region = table.parentElement;
      if (!region?.classList?.contains("table-wrap")) {
        region = document.createElement("div"); region.className = "table-wrap";
        table.parentElement.insertBefore(region, table); region.appendChild(table);
      }
      region.setAttribute("role", "region");
      region.setAttribute("aria-label", table.querySelector("caption")?.textContent?.trim() || table.getAttribute("aria-label") || "Data table");
      region.setAttribute("tabindex", "0");
    }
  }
  function destroy() { detach(); detach = () => {}; observer?.disconnect(); observer = null; setLoading(false); }
  function layout(active, contentHtml, crumbs = []) {
    detach(); detach = () => {}; observer?.disconnect(); observer = null;
    const app = $("#app"), title = PAGE_NAMES[active] || active;
    const trail = crumbs.length ? crumbs : [{ label: title }];
    app.innerHTML = `<a class="skip-link" href="#mainContent" id="skipContent">Skip to content</a>${sidebar(active)}<button type="button" class="nav-backdrop" id="navBackdrop" data-open="false" tabindex="-1" aria-hidden="true"></button><div class="main"><header class="topbar"><button type="button" class="btn icon nav-toggle" id="navToggle" aria-label="Open navigation" aria-controls="workspaceNav" aria-expanded="false">☰</button><nav class="breadcrumbs" aria-label="Breadcrumb">${trail.map((c, i) => `${i ? '<span class="sep" aria-hidden="true">/</span>' : ""}${c.href ? `<a href="${c.href}">${esc(c.label)}</a>` : `<span aria-current="page">${esc(c.label)}</span>`}`).join("")}</nav><span class="spacer"></span><span class="workspace-label">${esc(state.overview?.project?.name || state.project || "Workspace")}</span><label class="sr-only" for="appearanceSelect">Appearance</label><select id="appearanceSelect" class="appearance-control" title="Appearance"><option value="system" ${preferred === "system" ? "selected" : ""}>System</option><option value="light" ${preferred === "light" ? "selected" : ""}>Light</option><option value="dark" ${preferred === "dark" ? "selected" : ""}>Dark</option></select><span id="llmStatus"></span></header><div class="route-progress" id="routeProgress" role="status" aria-label="Loading page" hidden></div><main class="content" id="mainContent" tabindex="-1">${contentHtml}</main></div>`;
    state.domContext = state.pageContext;
    setLoading(loading);
    let open = false;
    const menu = $("#workspaceNav", app), toggle = $("#navToggle", app), backdrop = $("#navBackdrop", app), content = $(".content", app);
    function setNav(value, restore = false) {
      open = value;
      if (menu) menu.dataset.open = String(open);
      if (backdrop) backdrop.dataset.open = String(open);
      toggle?.setAttribute?.("aria-expanded", String(open));
      if (open) $("#navClose", app)?.focus?.(); else if (restore) toggle?.focus?.();
    }
    if (toggle) toggle.onclick = () => setNav(!open);
    const close = $("#navClose", app); if (close) close.onclick = () => setNav(false, true);
    if (backdrop) backdrop.onclick = () => setNav(false, true);
    const onKeyDown = (event) => {
      if (!open) return;
      if (globalThis.matchMedia && !matchMedia("(max-width: 1000px)").matches) { setNav(false); return; }
      if (event.key === "Escape") { event.preventDefault(); setNav(false, true); }
      if (event.key === "Tab") {
        const focusable = [...menu.querySelectorAll('a[href], button, select')].filter((el) => !el.disabled && el.getClientRects().length);
        const first = focusable[0], last = focusable.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener?.("keydown", onKeyDown);
    detach = () => document.removeEventListener?.("keydown", onKeyDown);
    for (const link of menu?.querySelectorAll?.('a[href]') || []) link.addEventListener("click", () => setNav(false));
    const skip = $("#skipContent", app); if (skip) skip.onclick = (event) => { event.preventDefault(); content.focus(); };
    const appearance = $("#appearanceSelect", app); if (appearance) appearance.onchange = (event) => { applyAppearance(event.target.value); try { localStorage.setItem(APPEARANCE_KEY, preferred); } catch (_) {} };
    const sw = $("#projectSwitch", app);
    if (sw) sw.onchange = feedback.guard(async () => {
      setNav(false);
      if (sw.value === "__new__") { sw.value = state.project; return newProjectModal(); }
      if (sw.value === "__all__") { sw.value = state.project; return navigation.go("#/projects"); }
      await selectProject(sw.value); state.overview = null; navigation.go("#/datasets"); navigation.render();
    });
    const out = $("#signOut", app);
    if (out) out.onclick = feedback.guard(async (event) => { event.preventDefault(); const result = await http.post("/api/auth/logout", {}); if (result.logout_url) location.assign(result.logout_url); else location.reload(); });
    if (content) {
      enhanceTables(content);
      if (globalThis.MutationObserver) { observer = new MutationObserver(() => enhanceTables(content)); observer.observe(content, { childList: true, subtree: true }); }
    }
    document.title = `${title} · tinystudio`;
    return content;
  }
  return { sidebar, layout, setLoading, destroy };
}
