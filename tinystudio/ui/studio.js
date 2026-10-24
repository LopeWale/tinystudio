// tinystudio web interface: a Studio-style single-page app over the local API.
// No build step. Hash routes, vanilla DOM, SVG charts.
import { ConversationSync } from "./assistant/conversation-sync.js?v=effaac73dfc3";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtPct = (v) => (v === null || v === undefined || Number.isNaN(v)) ? "–" : `${Math.round(v * 100)}%`;
const fmtNum = (v, d = 3) => (v === null || v === undefined || Number.isNaN(v)) ? "–" : Number(v).toFixed(d);
const fmtDate = (iso) => iso ? new Date(iso).toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "–";
const fmtParams = (n) => !n ? "–" : n >= 1e9 ? `${(n / 1e9).toFixed(2)}B` : `${Math.round(n / 1e6)}M`;
const pctClass = (v) => v >= 0.75 ? "hi" : v >= 0.4 ? "mid" : "lo";
const SERIES = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)", "var(--s5)", "var(--s6)"];
const slug = (id) => id.replace("/", "~");

const state = { overview: null, tasks: [], taskTimer: null, user: null, project: null, projects: [], playground: { model: null, system: "", temperature: 0.2, history: [] } };

// ------------------------------------------------------------------ http
// Project-scoped paths live under /api/p/<slug>; everything else (tasks, hub, settings, users, assistant) is global.
const PROJECT_PREFIXES = ["/api/overview", "/api/datasets", "/api/datapoints", "/api/augment", "/api/snapshots", "/api/recommend", "/api/jobs", "/api/metrics", "/api/evals", "/api/traces", "/api/stats", "/api/models", "/api/playground", "/api/repositories", "/api/interp", "/api/autopilot", "/api/catalog", "/api/artifacts"];
function scoped(path) {
  if (state.project && PROJECT_PREFIXES.some((pre) => path === pre || path.startsWith(pre + "/") || path.startsWith(pre + "?"))) return `/api/p/${encodeURIComponent(state.project)}` + path.slice(4);
  if (path === "/api/tasks" && state.project) return `/api/tasks?project=${encodeURIComponent(state.project)}`;
  return path;
}
function rawFetch(path, options = {}) {
  const headers = { "X-Requested-With": "tinystudio", ...(options.body instanceof FormData ? {} : { "content-type": "application/json" }), ...(options.headers || {}) };
  return fetch(scoped(path), { credentials: "same-origin", ...options, headers });
}
async function api(path, options = {}) {
  const pageCapture = state.pageContext;
  const response = await rawFetch(path, options);
  if (response.status === 401 && !path.startsWith("/api/auth/")) { showLogin(); throw new Error("sign in to continue"); }
  if (!response.ok) {
    let detail = response.statusText;
    try { const j = await response.json(); detail = j.detail ?? detail; } catch (_) {}
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  const type = response.headers.get("content-type") || "";
  if (!type.includes("json")) return response.text();
  const data = await response.json();
  if (pageCapture) assertRender(pageCapture);
  if (!options.method || options.method === "GET") recordApi(path, data, pageCapture);
  return data;
}
const post = (path, body) => api(path, { method: "POST", body: body instanceof FormData ? body : JSON.stringify(body ?? {}) });
const put = (path, body) => api(path, { method: "PUT", body: JSON.stringify(body ?? {}) });
const patch = (path, body) => api(path, { method: "PATCH", body: JSON.stringify(body ?? {}) });
// Large datasets are processed by a worker: the API answers 202 {queued, task} instead of the result.
function queued(result, what) { if (!result || !result.queued) return false; toast(`${what} runs as a background task (large dataset); the page refreshes when it finishes`); showTasks(); pollTasks(); return true; }
const del = (path) => api(path, { method: "DELETE" });

function toast(message, isError = false) {
  const el = document.createElement("div");
  el.className = "toast" + (isError ? " err" : ""); el.textContent = message;
  document.body.appendChild(el); setTimeout(() => el.remove(), isError ? 9000 : 3500);
}
const guard = (fn) => async (...args) => { try { return await fn(...args); } catch (e) { if (e.name !== "StaleRenderError") toast(e.message, true); } };

// ------------------------------------------------------------------ modal
function modal({ title, body, footer = "", wide = false, onMount, sensitive = false }) {
  const back = document.createElement("div"); back.className = "modal-back";
  if (sensitive) back.dataset.assistantSensitive = "true";
  back.innerHTML = `<div class="modal ${wide ? "wide" : ""}"><div class="mhead">${esc(title)}<button class="x" aria-label="close">✕</button></div><div class="mbody"></div>${footer ? `<div class="mfoot">${footer}</div>` : ""}</div>`;
  const bodyEl = $(".mbody", back);
  if (typeof body === "string") bodyEl.innerHTML = body; else bodyEl.appendChild(body);
  const close = () => back.remove();
  $(".x", back).onclick = close;
  back.addEventListener("click", (e) => { if (e.target === back) close(); });
  document.body.appendChild(back);
  if (onMount) onMount(back, close);
  return { el: back, close };
}
function confirmModal(title, text, action, label = "Delete") {
  return new Promise((resolve) => {
    const m = modal({ title, body: `<p>${esc(text)}</p>`, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary danger" data-ok>${esc(label)}</button>` });
    $("[data-cancel]", m.el).onclick = () => { m.close(); resolve(false); };
    $("[data-ok]", m.el).onclick = guard(async () => { await action(); m.close(); resolve(true); });
  });
}

// ------------------------------------------------------------------ router
const routes = [
  [/^#\/?$/, () => go("#/datasets")],
  [/^#\/datasets$/, () => pageDatasets()],
  [/^#\/repositories$/, () => pageRepositories()],
  [/^#\/datasets\/([^/]+)$/, (m) => pageDataset(decodeURIComponent(m[1]))],
  [/^#\/finetuning$/, () => pageFinetuning()],
  [/^#\/finetuning\/([^/]+)$/, (m) => pageJob(decodeURIComponent(m[1]))],
  [/^#\/evaluations$/, () => pageEvaluations()],
  [/^#\/evaluations\/([^/]+)$/, (m) => pageEval(decodeURIComponent(m[1]))],
  [/^#\/metrics$/, () => pageMetrics()],
  [/^#\/playground(?:\?model=(.+))?$/, (m) => pagePlayground(m[1] ? decodeURIComponent(m[1]) : null)],
  [/^#\/models$/, () => pageModels()],
  [/^#\/traces$/, () => pageTraces()],
  [/^#\/stats$/, () => pageStats()],
  [/^#\/api-keys$/, () => pageKeys()],
  [/^#\/assistant$/, () => pageAssistant()],
  [/^#\/settings$/, () => pageSettings()],
  [/^#\/users$/, () => pageUsers()],
  [/^#\/projects$/, () => pageProjects()],
  [/^#\/interp$/, () => pageInterp()],
  [/^#\/interp\/c\/([^/?]+)(?:\?(.*))?$/, (m) => pageCapture(decodeURIComponent(m[1]), new URLSearchParams(m[2] || ""))],
  [/^#\/interp\/d\/([^/?]+)(?:\?(.*))?$/, (m) => pageDiff(decodeURIComponent(m[1]), new URLSearchParams(m[2] || ""))],
];
function go(hash) { location.hash = hash; }
async function render() {
  syncAssistantScope();
  state.pageContext = { route: location.hash || "#/", project: state.project, api: [] };
  ASSIST.selection = null;
  uiRefs.clear();
  $$(".modal-back").forEach((el) => el.remove());
  const hash = location.hash || "#/";
  for (const [re, fn] of routes) {
    const m = hash.match(re);
    if (m) { try { await fn(m); } catch (e) { if (e.name === "StaleRenderError") return; toast(e.message, true); layout("Error", `<div class="error">${esc(e.message)}</div>`); } return; }
  }
  go("#/datasets");
}
window.addEventListener("hashchange", render);

// ------------------------------------------------------------------ layout
const ICONS = {
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
  if (sw) sw.onchange = guard(async () => {
    if (sw.value === "__new__") { sw.value = state.project; return newProjectModal(); }
    if (sw.value === "__all__") { sw.value = state.project; return go("#/projects"); }
    await selectProject(sw.value); state.overview = null; go("#/datasets"); render();
  });
  const out = $("#signOut", app);
  if (out) out.onclick = guard(async (e) => { e.preventDefault(); const result = await post("/api/auth/logout", {}); if (result.logout_url) location.assign(result.logout_url); else location.reload(); });
  return $(".content", app);
}
async function ensureOverview(force = false) {
  if (!state.overview || force) state.overview = await api("/api/overview");
  return state.overview;
}

// ------------------------------------------------------------------ datasets
async function pageDatasets() {
  const view = state.pageContext;
  await ensureOverview(true); assertRender(view);
  const datasets = await api("/api/datasets");
  assertRender(view);
  const content = layout("datasets", `
    <div class="page-head"><h1>Datasets</h1><span class="meta">${datasets.length} dataset${datasets.length === 1 ? "" : "s"}</span><div class="actions"><input id="dsSearch" placeholder="Search" style="width:220px"><button class="btn primary" id="newDataset">+ Create Dataset</button></div></div>
    <div class="cards" id="dsCards"></div>`, [{ label: "Datasets" }]);
  const draw = (q = "") => {
    const rows = datasets.filter((d) => d.name.toLowerCase().includes(q.toLowerCase()));
    $("#dsCards", content).innerHTML = rows.length ? rows.map((d) => `<div ${dataContext("Dataset", d)} class="card clickable" data-ds="${esc(d.name)}"><h3>${esc(d.name)}</h3><div class="sub">${d.datapoints} datapoints · ${d.snapshot_count} snapshot${d.snapshot_count === 1 ? "" : "s"}${d.origin?.kind === "synthetic" ? ' · <span class="pill synthetic">Synthetic</span>' : ""}</div><div class="foot"><span>Created on: ${fmtDate(d.created)}</span><span class="arrow">→</span></div></div>`).join("") : `<div class="empty"><b>No datasets yet</b>Upload a JSONL file or generate one from your documents.</div>`;
    $$("[data-ds]", content).forEach((c) => c.onclick = () => go(`#/datasets/${encodeURIComponent(c.dataset.ds)}`));
  };
  draw();
  $("#dsSearch", content).oninput = (e) => draw(e.target.value);
  $("#newDataset", content).onclick = () => createDatasetWizard();
}

function createDatasetWizard() {
  let step = 0, mode = "synthetic";
  const files = [];
  const body = document.createElement("div");
  const m = modal({ title: "Add dataset", body, wide: true });
  const draw = () => {
    const steps = `<div class="steps"><b>Step ${step + 1} of 3</b> · ${["Source", "Generation settings", "Summary"].map((s, i) => `<span style="${i === step ? "color:var(--text)" : ""}">${s}</span>`).join(" › ")}</div>`;
    if (step === 0) {
      body.innerHTML = `${steps}
        <div class="tabs"><button class="${mode === "synthetic" ? "on" : ""}" data-mode="synthetic">Synthetic data generation</button><button class="${mode === "upload" ? "on" : ""}" data-mode="upload">Upload JSONL</button></div>
        <div class="field"><label>Dataset name</label><input id="wName" value="${esc(body.dataset.name || "")}" placeholder="support-faq"></div>
        ${mode === "upload" ? `<div class="field"><label>JSONL file</label><div class="dropzone" id="drop">Drop a .jsonl file here or click to choose<br><small>one {"messages": [...]} object per line with system (optional), user, and assistant turns</small></div><div id="fileList" class="muted"></div></div>`
        : `<div class="row"><div class="field" style="flex:2"><label>Files</label><div class="dropzone" id="drop">Drop files (PDF, TXT, MD, HTML) or click to choose</div><div id="fileList" class="muted"></div></div><div class="field" style="flex:2"><label>Web page URLs (one per line)</label><textarea id="wUrls">${esc(body.dataset.urls || "")}</textarea></div></div>
           <div class="row"><div class="field"><label>Pairs per source</label><input id="wPairs" type="number" min="1" max="500" value="${body.dataset.pairs || 25}"></div><div class="field"><label>System prompt (optional, prepended to every datapoint)</label><input id="wSystem" value="${esc(body.dataset.system || "")}"></div></div>`}
        <div class="mfoot" style="padding:12px 0 0;border:0"><button class="btn" data-cancel>Cancel</button><button class="btn primary" data-next>${mode === "upload" ? "Create dataset" : "Next →"}</button></div>`;
      $$("[data-mode]", body).forEach((b) => b.onclick = () => { mode = b.dataset.mode; draw(); });
      const input = document.createElement("input"); input.type = "file"; input.multiple = mode !== "upload"; input.accept = mode === "upload" ? ".jsonl,.json" : ".pdf,.txt,.md,.markdown,.html,.htm";
      const listFiles = () => $("#fileList", body).textContent = files.map((f) => `${f.name} (${Math.round(f.size / 1024)} KB)`).join(", ");
      const drop = $("#drop", body);
      drop.onclick = () => input.click();
      input.onchange = () => { if (mode === "upload") files.length = 0; files.push(...input.files); listFiles(); };
      drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("on"); }; drop.ondragleave = () => drop.classList.remove("on");
      drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("on"); if (mode === "upload") files.length = 0; files.push(...e.dataTransfer.files); listFiles(); };
      listFiles();
      $("[data-cancel]", body).onclick = m.close;
      $("[data-next]", body).onclick = guard(async () => {
        body.dataset.name = $("#wName", body).value.trim();
        if (!body.dataset.name) throw new Error("give the dataset a name");
        if (mode === "upload") {
          if (!files.length) throw new Error("choose a JSONL file");
          const file = files[0];
          const storage = await api("/api/artifacts/config");
          let meta;
          if (storage.direct_upload) {
            const grant = await post("/api/artifacts/uploads", { filename: file.name, content_type: file.type || "application/octet-stream", size: file.size });
            const uploaded = await fetch(grant.url, { method: "PUT", body: file, headers: grant.headers, credentials: "omit" });
            if (!uploaded.ok) throw new Error(`Upload failed (${uploaded.status}); try again`);
            meta = await post("/api/datasets/from-object", { name: body.dataset.name, key: grant.key });
          } else {
            const form = new FormData(); form.append("name", body.dataset.name); form.append("file", file);
            meta = await post("/api/datasets/upload", form);
          }
          toast(`dataset ${meta.name}: ${meta.datapoints} datapoints`); m.close(); go(`#/datasets/${encodeURIComponent(meta.name)}`);
          return;
        }
        body.dataset.urls = $("#wUrls", body).value; body.dataset.pairs = $("#wPairs", body).value; body.dataset.system = $("#wSystem", body).value;
        if (!files.length && !body.dataset.urls.trim()) throw new Error("add at least one file or URL");
        step = 1; draw();
      });
    } else if (step === 1) {
      body.innerHTML = `${steps}
        <div class="field"><label>Rules &amp; constraints (one per line)</label><textarea id="wRules" placeholder="- write questions a customer would ask\n- answers must cite the policy name">${esc(body.dataset.rules || "")}</textarea></div>
        <div class="two"><div class="field"><label>Question guidance</label><textarea id="wQ" placeholder="Ask a practical question about {EXTRACTED_TEXT}">${esc(body.dataset.q || "")}</textarea></div><div class="field"><label>Answer format</label><textarea id="wA" placeholder='Plain prose, or a JSON template such as {"answer": "...", "source": "..."}'>${esc(body.dataset.a || "")}</textarea></div></div>
        <div class="two"><div class="field"><label>Example questions (one per line)</label><textarea id="wEQ">${esc(body.dataset.eq || "")}</textarea></div><div class="field"><label>Example answers (same order)</label><textarea id="wEA">${esc(body.dataset.ea || "")}</textarea></div></div>
        <div class="row"><div class="field"><label>Creativity level <span id="wTempV">${body.dataset.temp || 0.3}</span></label><input id="wTemp" type="range" min="0" max="1" step="0.01" value="${body.dataset.temp || 0.3}"></div><div class="field"><label>Curate: keep pairs rated at least (1–10, 0 = keep all)</label><input id="wCurate" type="number" min="0" max="10" step="0.5" value="${body.dataset.curate || 0}"></div></div>
        <div class="mfoot" style="padding:12px 0 0;border:0"><button class="btn left" data-back>← Back</button><button class="btn primary" data-next>Next →</button></div>`;
      $("#wTemp", body).oninput = (e) => $("#wTempV", body).textContent = e.target.value;
      $("[data-back]", body).onclick = () => { save(); step = 0; draw(); };
      const save = () => Object.assign(body.dataset, { rules: $("#wRules", body).value, q: $("#wQ", body).value, a: $("#wA", body).value, eq: $("#wEQ", body).value, ea: $("#wEA", body).value, temp: $("#wTemp", body).value, curate: $("#wCurate", body).value });
      $("[data-next]", body).onclick = () => { save(); step = 2; draw(); };
    } else {
      const urls = body.dataset.urls.split("\n").map((s) => s.trim()).filter(Boolean);
      const sources = files.length + urls.length, pairs = Number(body.dataset.pairs || 25);
      body.innerHTML = `${steps}
        <div class="callout"><div><div style="font-weight:600;font-size:17px">Ready to generate</div><div class="muted">Your synthetic dataset configuration is complete</div></div><div style="margin-left:auto;text-align:center;padding:8px 18px;border:1px solid var(--border);border-radius:10px;background:var(--panel)"><div class="muted" style="font-size:11px;letter-spacing:.5px">TOTAL PAIRS</div><div class="big">${sources * pairs}</div></div></div>
        <div class="panel" style="margin-bottom:12px"><div class="head"><h2>Data sources</h2><span class="muted">Input sources for synthetic data generation</span></div><div class="body"><div class="summary-row"><span>Files</span><b>${files.length}</b></div><div class="summary-row"><span>Web pages</span><b>${urls.length}</b></div><div class="summary-row"><span>Pairs per source</span><b>${pairs}</b></div></div></div>
        <div class="panel" style="margin-bottom:12px"><div class="head"><h2>Generation settings</h2></div><div class="body"><div class="summary-row"><span>Creativity level</span><b>${body.dataset.temp || 0.3}</b></div><div class="summary-row"><span>Rules &amp; constraints</span><b class="truncate" style="max-width:360px">${esc(body.dataset.rules?.split("\n")[0] || "none")}</b></div><div class="summary-row"><span>Curation threshold</span><b>${Number(body.dataset.curate || 0) > 0 ? body.dataset.curate + " / 10" : "off"}</b></div></div></div>
        <div class="panel"><div class="head"><h2>Cost</h2></div><div class="body"><div class="summary-row"><span>Helper model</span><b>${esc(state.overview?.llm?.model || "not configured")}</b></div><div class="summary-row"><span>Credits</span><b>0 — runs on your machine</b></div></div></div>
        <div class="mfoot" style="padding:12px 0 0;border:0"><button class="btn left" data-back>← Back</button><button class="btn primary" data-create>Create dataset</button></div>`;
      $("[data-back]", body).onclick = () => { step = 1; draw(); };
      $("[data-create]", body).onclick = guard(async () => {
        const form = new FormData();
        form.append("name", body.dataset.name); form.append("pairs_per_source", String(pairs)); form.append("temperature", String(body.dataset.temp || 0.3));
        form.append("rules", body.dataset.rules || ""); form.append("question_format", body.dataset.q || ""); form.append("answer_format", body.dataset.a || "");
        form.append("example_questions", body.dataset.eq || ""); form.append("example_answers", body.dataset.ea || ""); form.append("system_prompt", body.dataset.system || "");
        form.append("curate_threshold", String(body.dataset.curate || 0)); form.append("urls", urls.join("\n")); form.append("goal", state.overview?.project?.goal || "");
        files.forEach((f) => form.append("files", f));
        await post("/api/datasets/synthetic", form);
        m.close(); toast("generation started; watch the task panel"); showTasks(); pollTasks();
      });
    }
  };
  draw();
}

async function pageDataset(name) {
  const view = state.pageContext;
  await ensureOverview(true); assertRender(view);
  let offset = 0; const limit = 15;
  const info = state.overview.tree.datasets.find((d) => d.name === name);
  const snaps = info?.snapshots || [];
  assertRender(view);
  const content = layout("datasets", `
    <div class="page-head"><div><h1>${esc(name)}</h1><div class="meta">Created on: ${fmtDate(info?.created)} ${info?.origin?.kind === "synthetic" ? '· <span class="pill synthetic">Synthetic</span>' : ""}</div></div>
      <div class="actions">
        <button class="btn primary" id="btnFinetune">Start finetuning</button>
        <button class="btn" id="btnEnrich">✨ Enrich</button>
        <button class="btn" id="btnSnapshots">📸 Snapshots</button>
        <button class="btn" id="btnSplit">⚖ Autosplit</button>
        <button class="btn" id="btnLabels">🏷 Labels</button>
        <button class="btn" id="btnQuality">📊 Quality</button>
        <a class="btn" href="${scoped(`/api/datasets/${encodeURIComponent(name)}/download`)}">⬇</a>
        <button class="btn" id="btnAdd">+</button>
        <button class="btn danger" id="btnDelete">Delete</button>
      </div></div>
    ${snaps.length ? "" : `<div class="pill info" style="margin-bottom:12px">No snapshots yet. Create one to run finetuning and evaluation.</div>`}
    <div class="tiles" id="tiles"></div>
    <div class="panel"><div class="head"><h2>Datapoints</h2><span class="muted" id="dpCount"></span><div class="right"><select id="bucketFilter"><option value="">All buckets</option><option>uncategorized</option><option>training</option><option>validation</option></select></div></div><div id="dpTable"></div></div>`,
    [{ label: "Datasets", href: "#/datasets" }, { label: name }]);
  let filter = "";
  const drawTiles = (meta) => {
    $("#tiles", content).innerHTML = [["uncategorized", "var(--text-3)"], ["training", "var(--s1)"], ["validation", "var(--s3)"]].map(([b, c]) => `<div ${dataContext("Dataset bucket", {bucket:b,count:meta.buckets[b]})} class="tile ${filter === b ? "active" : ""}" data-b="${b}"><div class="k"><span class="dot" style="background:${c}"></span>${b[0].toUpperCase() + b.slice(1)}</div><div class="v">${meta.buckets[b]}</div></div>`).join("") + (meta.labels?.length ? `<div class="tile"><div class="k">Labels</div><div class="v" style="font-size:13px">${meta.labels.map((l) => esc(l.name)).join(", ")}</div></div>` : "");
    $$("[data-b]", content).forEach((t) => t.onclick = () => { filter = filter === t.dataset.b ? "" : t.dataset.b; $("#bucketFilter", content).value = filter; offset = 0; load(); });
  };
  const load = guard(async () => {
    const d = await api(`/api/datasets/${encodeURIComponent(name)}?offset=0&limit=100000`);
    drawTiles(d.meta);
    const rows = d.rows.map((r, i) => ({ ...r, index: i })).filter((r) => !filter || r.bucket === filter);
    const page = rows.slice(offset, offset + limit);
    $("#dpCount", content).textContent = `${rows.length} datapoints`;
    $("#dpTable", content).innerHTML = `<table><thead><tr><th>#</th><th>User message</th><th>Assistant response</th><th>Bucket</th><th>Labels</th><th>Data type</th></tr></thead><tbody>${page.map((r) => {
      const user = [...r.messages].reverse().find((m) => m.role === "user")?.content || "";
      return `<tr ${dataContext("Datapoint", r)} class="clickable" data-i="${r.index}"><td class="mono muted">${r.index + 1}</td><td class="truncate">${esc(user)}</td><td class="truncate mono">${esc(r.messages[r.messages.length - 1].content)}</td><td><span class="pill">${r.bucket}</span></td><td class="muted">${esc((r.labels || []).filter((l) => l !== "synthetic" && l !== "augmented").join(", "))}</td><td>${r.source?.startsWith("synthetic") ? '<span class="pill synthetic">Synthetic</span>' : r.source?.startsWith("trace") ? '<span class="pill info">Trace</span>' : '<span class="pill">Uploaded</span>'}</td></tr>`;
    }).join("") || `<tr><td colspan="6" class="empty">No datapoints in this bucket.</td></tr>`}</tbody></table>
    <div class="pager"><span>Results ${rows.length ? offset + 1 : 0} - ${Math.min(offset + limit, rows.length)} of ${rows.length}</span><span class="spacer"></span><button class="btn sm" id="prev" ${offset === 0 ? "disabled" : ""}>‹</button><span class="cur">${Math.floor(offset / limit) + 1}</span><button class="btn sm" id="next" ${offset + limit >= rows.length ? "disabled" : ""}>›</button></div>`;
    $("#prev", content).onclick = () => { offset -= limit; load(); };
    $("#next", content).onclick = () => { offset += limit; load(); };
    $$("tr[data-i]", content).forEach((tr) => tr.onclick = () => showDatapoint(d.rows[Number(tr.dataset.i)], Number(tr.dataset.i)));
  });
  $("#bucketFilter", content).onchange = (e) => { filter = e.target.value; offset = 0; load(); };
  load();

  $("#btnFinetune", content).onclick = () => snaps.length ? createJobDialog(snaps[snaps.length - 1].name) : toast("create a snapshot first", true);
  $("#btnEnrich", content).onclick = () => {
    const m = modal({ title: "Enrich dataset", body: `<p class="muted">Paraphrase existing user turns with the helper model so the model sees more ways of asking. Split before you enrich if you want a clean validation set.</p><div class="field"><label>Datapoints to generate</label><input id="enCount" type="number" min="1" max="2000" value="50"></div><div class="field"><label>Creativity level <span class="muted" id="enTempVal">0.8</span></label><input id="enTemp" type="range" min="0" max="1.5" step="0.1" value="0.8"></div><div class="field"><label>Positive instructions (generate more of…)</label><input id="enMore" placeholder="terse voice commands, room names, numbers spelled out"></div><div class="field"><label>Negative instructions (avoid…)</label><input id="enAvoid" placeholder="questions, chit-chat, requests outside the home"></div>`, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Enrich</button>` });
    $("#enTemp", m.el).oninput = () => { $("#enTempVal", m.el).textContent = $("#enTemp", m.el).value; };
    $("[data-cancel]", m.el).onclick = m.close;
    $("[data-ok]", m.el).onclick = guard(async () => { await post("/api/augment", { dataset: name, count: Number($("#enCount", m.el).value), temperature: Number($("#enTemp", m.el).value), instructions: $("#enMore", m.el).value, avoid: $("#enAvoid", m.el).value }); m.close(); showTasks(); pollTasks(); });
  };
  $("#btnSplit", content).onclick = () => {
    const m = modal({ title: "Autosplit dataset", body: `<p class="muted">Assign every uncategorized datapoint to training or validation. Existing assignments are kept.</p><div class="field"><label>Training percentage: <b id="spV">80</b>%</label><input id="sp" type="range" min="1" max="99" value="80"></div><label class="radio-card" id="synthCard"><input type="checkbox" id="allowSynth"><div><b>Allow synthetic data in validation</b><span>Off keeps generated rows out of the validation split so the score reflects real data.</span></div></label>`, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Split</button>` });
    $("#sp", m.el).oninput = (e) => $("#spV", m.el).textContent = e.target.value;
    $("[data-cancel]", m.el).onclick = m.close;
    $("[data-ok]", m.el).onclick = guard(async () => { const meta = await post(`/api/datasets/${encodeURIComponent(name)}/split`, { train_pct: Number($("#sp", m.el).value), synthetic_in_validation: $("#allowSynth", m.el).checked }); m.close(); if (queued(meta, "the split")) return; toast(`training ${meta.buckets.training} · validation ${meta.buckets.validation}`); load(); });
  };
  $("#btnSnapshots", content).onclick = () => snapshotsDialog(name, snaps, () => { pageDataset(name); });
  $("#btnLabels", content).onclick = () => labelsDialog(name, info?.labels || [], load);
  $("#btnQuality", content).onclick = () => qualityDialog(name, () => { ensureOverview(true).then(() => pageDataset(name)); });
  $("#btnAdd", content).onclick = () => {
    const m = modal({ title: "Add datapoint", body: `<div class="field"><label>System (optional)</label><textarea id="dpS"></textarea></div><div class="field"><label>User</label><textarea id="dpU"></textarea></div><div class="field"><label>Assistant</label><textarea id="dpA"></textarea></div><div class="field"><label>Bucket</label><select id="dpB"><option>uncategorized</option><option>training</option><option>validation</option></select></div>`, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Add</button>` });
    $("[data-cancel]", m.el).onclick = m.close;
    $("[data-ok]", m.el).onclick = guard(async () => {
      const messages = []; const s = $("#dpS", m.el).value.trim(); if (s) messages.push({ role: "system", content: s });
      messages.push({ role: "user", content: $("#dpU", m.el).value.trim() }, { role: "assistant", content: $("#dpA", m.el).value.trim() });
      await post("/api/datapoints", { dataset: name, messages, bucket: $("#dpB", m.el).value }); m.close(); toast("added"); load();
    });
  };
  $("#btnDelete", content).onclick = () => confirmModal("Delete dataset", `Delete "${name}" and its datapoints? Snapshots made from it are kept.`, async () => { await del(`/api/datasets/${encodeURIComponent(name)}`); go("#/datasets"); });
}
function showDatapoint(row, index) {
  modal({ title: `Datapoint ${index + 1}`, body: row.messages.map((m) => `<div style="margin-bottom:12px"><span class="tag ${m.role}">${m.role === "system" ? "System prompt" : m.role === "user" ? "User message" : "Assistant response"}</span><div class="msg" style="background:var(--panel-2);padding:10px 12px;border-radius:8px;margin-top:6px">${esc(m.content)}</div></div>`).join("") + `<div class="muted">bucket: ${row.bucket} · labels: ${esc((row.labels || []).join(", ") || "none")} · source: ${esc(row.source || "uploaded")}${row.rating !== undefined ? ` · rating ${row.rating}/10` : ""}</div>` });
}
function snapshotsDialog(dataset, snaps, after) {
  const m = modal({ title: "Snapshots", body: `<p class="muted">A snapshot freezes the current training and validation buckets. Finetuning and evaluation run on snapshots.</p>
    <table><thead><tr><th>Name</th><th>Training</th><th>Validation</th><th>Created</th><th></th></tr></thead><tbody>${snaps.map((s) => `<tr ${dataContext("Snapshot", s)}><td><b>${esc(s.name)}</b></td><td>${s.training_count}</td><td>${s.validation_count}</td><td>${fmtDate(s.created)}</td><td style="text-align:right"><a class="btn sm" href="${scoped(`/api/snapshots/${encodeURIComponent(s.name)}/download`)}">⬇ Download</a> <button class="btn sm danger" data-del="${esc(s.name)}">Delete</button></td></tr>`).join("") || `<tr><td colspan="5" class="muted">None yet.</td></tr>`}</tbody></table>
    <div class="row" style="margin-top:14px"><div class="field"><label>New snapshot name</label><input id="snName" placeholder="${esc(dataset)}-v${snaps.length + 1}"></div><button class="btn primary" id="snCreate">Create snapshot</button></div>`, footer: `<button class="btn" data-close>Close</button>` });
  $("[data-close]", m.el).onclick = m.close;
  $("#snCreate", m.el).onclick = guard(async () => { const nm = $("#snName", m.el).value.trim() || `${dataset}-v${snaps.length + 1}`; const s = await post("/api/snapshots", { dataset, name: nm }); m.close(); if (queued(s, `snapshot ${nm}`)) return; toast(`snapshot ${s.name}: ${s.training_count} / ${s.validation_count}`); after(); });
  $$("[data-del]", m.el).forEach((b) => b.onclick = () => confirmModal("Delete snapshot", `Delete snapshot "${b.dataset.del}"? Jobs that used it keep their results.`, async () => { await del(`/api/snapshots/${encodeURIComponent(b.dataset.del)}`); m.close(); after(); }));
}
function labelsDialog(dataset, labels, after) {
  const rows = labels.length ? labels.map((l) => ({ ...l })) : [{ name: "", description: "" }];
  const body = document.createElement("div");
  const m = modal({ title: "Labels", body, footer: `<button class="btn left" id="autoLabel">Auto-label with the helper LLM</button><button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Save labels</button>` });
  const draw = () => {
    body.innerHTML = `<p class="muted">Define a label vocabulary, then let the helper model assign one label per datapoint. Example: excellent, good, fair, poor.</p>` + rows.map((r, i) => `<div class="row"><div class="field" style="flex:1"><label>Name</label><input data-n="${i}" value="${esc(r.name)}"></div><div class="field" style="flex:2"><label>Description</label><input data-d="${i}" value="${esc(r.description)}"></div><button class="btn icon" data-x="${i}">✕</button></div>`).join("") + `<button class="btn sm" id="addRow">+ Add label</button>`;
    $("#addRow", body).onclick = () => { collect(); rows.push({ name: "", description: "" }); draw(); };
    $$("[data-x]", body).forEach((b) => b.onclick = () => { collect(); rows.splice(Number(b.dataset.x), 1); draw(); });
  };
  const collect = () => rows.forEach((r, i) => { r.name = $(`[data-n="${i}"]`, body)?.value ?? r.name; r.description = $(`[data-d="${i}"]`, body)?.value ?? r.description; });
  draw();
  $("[data-cancel]", m.el).onclick = m.close;
  $("[data-ok]", m.el).onclick = guard(async () => { collect(); await put(`/api/datasets/${encodeURIComponent(dataset)}/labels`, { labels: rows.filter((r) => r.name.trim()) }); toast("labels saved"); m.close(); await ensureOverview(true); after(); });
  $("#autoLabel", m.el).onclick = guard(async () => { collect(); await put(`/api/datasets/${encodeURIComponent(dataset)}/labels`, { labels: rows.filter((r) => r.name.trim()) }); await post(`/api/datasets/${encodeURIComponent(dataset)}/auto-label`, {}); m.close(); showTasks(); pollTasks(); });
}

// ------------------------------------------------------------------ data quality
function histogram(h, color = "var(--s1)", width = 320, height = 90) {
  if (!h || !h.counts || !h.counts.length) return `<div class="muted">no data</div>`;
  const max = Math.max(...h.counts, 1), n = h.counts.length, gap = 2, bw = (width - gap * (n - 1)) / n;
  const bars = h.counts.map((c, i) => `<rect x="${(i * (bw + gap)).toFixed(1)}" y="${(height - 14 - (c / max) * (height - 18)).toFixed(1)}" width="${bw.toFixed(1)}" height="${((c / max) * (height - 18)).toFixed(1)}" rx="2" fill="${color}"><title>${h.bins[i]}–${h.bins[i + 1] ?? h.bins[i]}: ${c}</title></rect>`).join("");
  return `<svg ${dataContext("Histogram bins and counts", h)} viewBox="0 0 ${width} ${height}" style="width:100%;height:${height}px">${bars}<text x="0" y="${height - 2}" font-size="10" fill="var(--text-3)" font-family="var(--mono)">${h.bins[0]}</text><text x="${width}" y="${height - 2}" font-size="10" fill="var(--text-3)" text-anchor="end" font-family="var(--mono)">${h.bins[h.bins.length - 1]}</text></svg>`;
}
async function qualityDialog(dataset, after) {
  const body = document.createElement("div");
  body.innerHTML = `<div class="muted"><span class="spinner"></span> profiling…</div>`;
  const m = modal({ title: `Data quality · ${dataset}`, body, wide: true, footer: `<button class="btn" data-close>Close</button>` });
  $("[data-close]", m.el).onclick = m.close;
  const p = await api(`/api/datasets/${encodeURIComponent(dataset)}/quality`);
  const stat = (k, v) => `<div ${dataContext(k, {label:k,value:v})} class="tile"><div class="k">${k}</div><div class="v">${v}</div></div>`;
  const dist = (pairs) => pairs.length ? `<table>${pairs.map(([k, v]) => `<tr ${dataContext("Distribution entry", {label:k,value:v})}><td>${esc(k)}</td><td class="num">${v}</td><td style="width:40%"><div style="height:8px;border-radius:4px;background:var(--s1);width:${Math.max(2, 100 * v / pairs[0][1])}%"></div></td></tr>`).join("")}</table>` : `<div class="muted">none</div>`;
  const o = p.outliers, d = p.duplicates;
  body.innerHTML = `
    <div class="tiles">${stat("Datapoints", p.count)}${stat("Approx. tokens", p.approx_tokens.total.toLocaleString())}${stat("Mean turns", p.turns.mean)}${stat("With system prompt", p.turns.with_system_prompt)}${stat("Exact duplicate user turns", d.exact_user_rows)}${stat("Near-duplicate pairs", d.near_pair_count)}${stat("Empty-like rows", o.empty_like.length)}${stat("Length outliers", o.too_short.length + o.too_long.length)}${stat("JSON answers", p.format.answers_json_pct + "%")}${p.ratings.count ? stat("Mean rating", p.ratings.mean) : ""}</div>
    <div class="two" style="margin-bottom:14px">
      <div class="panel"><div class="head"><h2>User message length (chars)</h2><span class="muted mono">median ${p.user_chars.summary.median} · p95 ${p.user_chars.summary.p95} · max ${p.user_chars.summary.max}</span></div><div class="body">${histogram(p.user_chars.histogram, "var(--s1)")}</div></div>
      <div class="panel"><div class="head"><h2>Assistant response length (chars)</h2><span class="muted mono">median ${p.answer_chars.summary.median} · p95 ${p.answer_chars.summary.p95} · max ${p.answer_chars.summary.max}</span></div><div class="body">${histogram(p.answer_chars.histogram, "var(--s3)")}</div></div>
    </div>
    <div class="two" style="margin-bottom:14px">
      <div class="panel"><div class="head"><h2>Labels</h2></div><div class="body">${dist(p.labels)}</div></div>
      <div class="panel"><div class="head"><h2>Sources and buckets</h2></div><div class="body">${dist(p.sources)}<div style="height:8px"></div>${dist(Object.entries(p.buckets))}${p.ratings.count ? `<div style="margin-top:10px"><div class="muted">Curation ratings (1–10)</div>${histogram(p.ratings.histogram, "var(--s4)", 320, 70)}</div>` : ""}</div></div>
    </div>
    ${d.examples.length ? `<div ${dataContext("Duplicate groups", d)} class="panel" style="margin-bottom:14px"><div class="head"><h2>Duplicate examples</h2></div><div class="body">${d.examples.map((g) => `<div class="mono" style="font-size:12px;margin-bottom:4px">${g.map(esc).join("  ·  ")}</div>`).join("")}${d.near_pairs.length ? `<div class="muted" style="margin-top:8px">Near duplicates (Jaccard over word n-grams ≥ 0.7): ${d.near_pairs.slice(0, 5).map((x) => `#${x.a + 1}≈#${x.b + 1} (${x.similarity})`).join(", ")}${d.near_pairs.length > 5 ? " …" : ""}</div>` : ""}</div></div>` : ""}
    <div ${dataContext("Dataset quality profile", p)} class="panel"><div class="head"><h2>Clean</h2><span class="muted">Applies in place; every run is logged to cleaning.jsonl. Snapshots are not affected.</span></div><div class="body">
      <div class="chips" id="cleanOpts">
        <button data-k="dedupe_user">Drop exact duplicate user turns (${d.exact_user_rows})</button>
        <button data-k="dedupe_conversation">Drop exact duplicate conversations (${d.exact_conversation_rows})</button>
        <button data-k="drop_near_duplicates">Drop near duplicates (${d.near_pair_count} pairs)</button>
        <button data-k="drop_empty">Drop empty-like rows (${o.empty_like.length})</button>
        <button data-k="trim_whitespace">Trim whitespace</button>
        <button data-k="require_json_answer">Keep only JSON answers (${p.format.answers_json}/${p.count})</button>
        <button data-k="redact_pii" title="Emails, card numbers, national ids, and phone numbers become [EMAIL] [CARD] [ID] [PHONE]; rows are kept">Redact PII (${p.pii.redactable_rows ?? 0} rows)</button>
      </div>
      <div class="row" style="margin-top:10px"><div class="field"><label>Min total chars (short outliers below ${o.fences.low})</label><input id="clMin" type="number" placeholder="${o.fences.low}"></div><div class="field"><label>Max total chars (long outliers above ${o.fences.high})</label><input id="clMax" type="number" placeholder="${o.fences.high}"></div><div class="field"><label>Min rating (1–10)</label><input id="clRating" type="number" step="0.5" min="0" max="10"></div><div class="field"><label>Drop labels (comma separated)</label><input id="clDrop" placeholder="poor, off-topic"></div></div>
      <div class="row"><button class="btn primary" id="clApply">Apply cleaning</button><span class="muted" id="clResult"></span></div>
    </div></div>`;
  const chosen = new Set();
  $$("#cleanOpts button", body).forEach((b) => b.onclick = () => { chosen.has(b.dataset.k) ? chosen.delete(b.dataset.k) : chosen.add(b.dataset.k); b.classList.toggle("on"); });
  $("#clApply", body).onclick = guard(async () => {
    const spec = Object.fromEntries([...chosen].map((k) => [k, true]));
    const num = (id) => { const v = $(id, body).value; return v === "" ? null : Number(v); };
    Object.assign(spec, { min_total_chars: num("#clMin"), max_total_chars: num("#clMax"), min_rating: num("#clRating"), drop_labels: $("#clDrop", body).value.split(",").map((s) => s.trim()).filter(Boolean) });
    if (!Object.keys(spec).some((k) => spec[k] === true) && spec.min_total_chars === null && spec.max_total_chars === null && spec.min_rating === null && !spec.drop_labels.length) throw new Error("pick at least one cleaning rule");
    const r = await post(`/api/datasets/${encodeURIComponent(dataset)}/clean`, spec);
    if (queued(r, "cleaning")) { $("#clResult", body).textContent = "queued as a background task"; return; }
    $("#clResult", body).textContent = `${r.before} → ${r.after} datapoints; removed ${JSON.stringify(r.removed)}${r.redacted_rows ? `; redacted PII in ${r.redacted_rows} rows` : ""}`;
    toast(`cleaned: ${r.before} → ${r.after}`); after();
  });
}

// ------------------------------------------------------------------ fine-tuning
function createJobDialog(defaultSnapshot = null) {
  const snaps = state.overview.tree.datasets.flatMap((d) => d.snapshots);
  if (!snaps.length) return toast("create a snapshot first", true);
  const m = modal({ title: "New finetuning job", body: `
    <div class="field"><label>Name</label><input id="jbName" placeholder="my-first-job"></div>
    <div class="field"><label>Snapshot</label><select id="jbSnap">${snaps.map((s) => `<option value="${esc(s.name)}" ${s.name === defaultSnapshot ? "selected" : ""}>${esc(s.name)} — ${esc(s.dataset)} (${s.training_count}/${s.validation_count})</option>`).join("")}</select></div>
    <div class="field"><label>Model type</label><label class="radio-card on" id="rcNon"><input type="radio" name="mt" value="non" checked><div><b>Non reasoning</b><span>Direct answers. Best for formats, extraction, classification, and domain Q&amp;A.</span></div></label><label class="radio-card" id="rcRea"><input type="radio" name="mt" value="rea"><div><b>Reasoning</b><span>Answers that think step by step. Needs a chat template with thinking support and longer sequences.</span></div></label></div>`,
    footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Create job</button>` });
  $$("input[name=mt]", m.el).forEach((r) => r.onchange = () => { $("#rcNon", m.el).classList.toggle("on", r.value === "non" && r.checked); $("#rcRea", m.el).classList.toggle("on", r.value === "rea" && r.checked); });
  $("[data-cancel]", m.el).onclick = m.close;
  $("[data-ok]", m.el).onclick = guard(async () => {
    const name = $("#jbName", m.el).value.trim(); if (!name) throw new Error("give the job a name");
    const job = await post("/api/jobs", { name, snapshot: $("#jbSnap", m.el).value, reasoning: $("input[name=mt]:checked", m.el).value === "rea" });
    m.close(); go(`#/finetuning/${encodeURIComponent(job.name)}`);
  });
}
function autopilotPanel(pilots) {
  if (!pilots.length) return "";
  return `<div class="panel" style="margin-bottom:14px"><div class="head"><h2>Autopilot</h2><span class="muted">continuous fine-tuning: traces → dataset → snapshot → refinetune → evaluate → winner</span></div><div class="body"><table><thead><tr><th>Name</th><th>Dataset</th><th>Cycles</th><th>Current winner</th><th>Last run</th><th></th></tr></thead><tbody>${pilots.map((p) => `<tr ${dataContext("Autopilot", p)}><td><b>${esc(p.name)}</b></td><td>${esc(p.dataset || "")}</td><td class="num">${p.cycles}</td><td class="mono">${esc(p.winner || "—")}</td><td class="muted">${p.last ? fmtDate(p.last) : "—"}</td><td><button class="btn small" data-ap="${esc(p.name)}">History</button> <button class="btn small primary" data-apnext="${esc(p.name)}" data-apds="${esc(p.dataset || "")}">Next cycle</button></td></tr>`).join("")}</tbody></table></div></div>`;
}

function autopilotDialog(preset = {}) {
  const datasets = state.overview.tree.datasets.filter((d) => !d.missing);
  if (!datasets.length) return toast("create a dataset first", true);
  const metrics = (state.overview.tree.metrics || state.overview.metrics || []).map((m) => (typeof m === "string" ? m : m.name));
  const catalog = state.overview.catalog || [];
  const m = modal({ title: preset.name ? `Autopilot · next cycle of ${preset.name}` : "Autopilot cycle", wide: true, body: `
    <p class="muted">One cycle: promote scored traces into the dataset → clean → split → snapshot <span class="mono">NAME-cN</span> → train job <span class="mono">NAME-cN</span> refined from the previous cycle's winner → evaluate against it → record the winner. Run it again whenever new traces or data arrive.</p>
    <div class="grid">
      <div class="field"><label>Autopilot name</label><input id="apName" value="${esc(preset.name || "")}" placeholder="home-loop" ${preset.name ? "readonly" : ""}></div>
      <div class="field"><label>Dataset</label><select id="apDs">${datasets.map((d) => `<option ${d.name === preset.dataset ? "selected" : ""}>${esc(d.name)}</option>`).join("")}</select></div>
      <div class="field"><label>Base model (first cycle only; later cycles refine the winner)</label><input id="apModel" list="apCatalog" value="${esc(catalog[0]?.id || "")}"><datalist id="apCatalog">${catalog.map((c) => `<option value="${esc(c.id)}">${c.params_b}B · ${esc(c.notes || "")}</option>`).join("")}</datalist></div>
      <div class="field"><label>Training type</label><select id="apType"><option value="lora">LoRA</option><option value="full">Full fine-tuning</option><option value="dpo">DPO (preference pairs)</option></select></div>
      <div class="field"><label>Epochs</label><input id="apEpochs" type="number" step="0.5" min="0.5" value="2"></div>
      <div class="field"><label>Promote traces scored ≥ (blank = none)</label><input id="apScore" type="number" step="0.1" min="0" max="1" placeholder="0.9"></div>
      <div class="field"><label>Metrics (first decides the winner)</label><select id="apMetrics" multiple size="4">${metrics.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join("")}</select></div>
      <div class="field"><label>Evaluate at most N validation rows (blank = all)</label><input id="apLimit" type="number" min="1" placeholder="all"></div>
    </div>
    <label class="check"><input type="checkbox" id="apPref"> Promote corrected traces as DPO preference pairs (correction = chosen, original = rejected)</label>
    <label class="check"><input type="checkbox" id="apClean" checked> Clean first (dedupe user turns, drop empty rows, trim whitespace)</label>`,
    footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Run cycle</button>` });
  $("#apType", m.el).onchange = () => { if ($("#apType", m.el).value === "dpo") $("#apPref", m.el).checked = true; };
  $("[data-cancel]", m.el).onclick = m.close;
  $("[data-ok]", m.el).onclick = guard(async () => {
    const name = $("#apName", m.el).value.trim(); if (!name) throw new Error("give the autopilot a name");
    const score = $("#apScore", m.el).value, limit = $("#apLimit", m.el).value;
    await post("/api/autopilot", { name, dataset: $("#apDs", m.el).value, base_model: $("#apModel", m.el).value.trim() || null, training_type: $("#apType", m.el).value, epochs: Number($("#apEpochs", m.el).value), min_trace_score: score === "" ? null : Number(score), preference: $("#apPref", m.el).checked, clean: $("#apClean", m.el).checked, metrics: [...$("#apMetrics", m.el).selectedOptions].map((o) => o.value), eval_limit: limit === "" ? null : Number(limit) });
    m.close(); toast(`autopilot ${name}: cycle started`); showTasks(); pollTasks();
  });
}

async function autopilotHistoryDialog(name) {
  const rows = await api(`/api/autopilot/${encodeURIComponent(name)}`);
  const cell = (r) => { const e = r.experiment || {}, ev = r.evaluation; return `<tr ${dataContext("Autopilot cycle", r)}><td class="num">${r.cycle}</td><td class="mono">${esc(r.snapshot?.name || "—")}<div class="muted">${r.snapshot?.training ?? "?"} / ${r.snapshot?.validation ?? "?"}</div></td><td class="mono">${esc(e.model || "—")} <span class="pill ${e.state === "succeeded" ? "completed" : e.state === "failed" ? "failed" : "pending"}">${esc(e.state || "?")}</span><div class="muted">from ${esc(r.parent || "base")}${r.promoted_traces ? ` · +${r.promoted_traces} traces` : ""}</div></td><td class="num">${e.eval_loss != null ? e.eval_loss.toFixed(4) : "—"}</td><td>${ev ? `<span class="mono">${esc(ev.primary_metric)}</span> ${ev.candidate ?? "—"} vs ${ev.baseline ?? "—"}` : "<span class='muted'>no metrics</span>"}</td><td class="mono"><b>${esc(r.winner || "—")}</b><div class="muted">${esc(r.decision || e.error || "")}</div></td></tr>`; };
  const m = modal({ title: `Autopilot · ${name}`, wide: true, body: `<table><thead><tr><th>Cycle</th><th>Snapshot (train / val)</th><th>Experiment</th><th>eval_loss</th><th>Evaluation</th><th>Winner</th></tr></thead><tbody>${rows.map(cell).join("") || `<tr><td colspan="6" class="muted">no cycles yet</td></tr>`}</tbody></table>`, footer: `<button class="btn" data-close>Close</button>` });
  $("[data-close]", m.el).onclick = m.close;
}

async function pageFinetuning() {
  const view = state.pageContext;
  await ensureOverview(true); assertRender(view);
  const jobs = await api("/api/jobs");
  const pilots = await api("/api/autopilot").catch(() => []);
  assertRender(view);
  const content = layout("finetuning", `<div class="page-head"><h1>Finetuning</h1><div class="actions"><button class="btn" id="autopilotBtn">Autopilot cycle</button><button class="btn primary" id="newJob">+ Create finetuning job</button></div></div>${autopilotPanel(pilots)}<div class="cards">${jobs.map(({ job, experiments }) => {
    const states = experiments.map((e) => e.state);
    const label = !experiments.length ? ["Ready to start experiments", "ready"] : states.includes("running") ? ["Running", "running"] : states.every((s) => s === "pending") ? ["Ready to start experiments", "ready"] : states.some((s) => s === "pending") ? ["Partially run", "pending"] : states.every((s) => s === "succeeded") ? ["Completed", "completed"] : states.some((s) => s === "failed") ? ["Failed", "failed"] : ["Completed", "completed"];
    return `<div ${dataContext("Finetuning job", {job,experiments})} class="card clickable" data-job="${esc(job.name)}"><h3>${esc(job.name)}</h3><span class="pill ${label[1]}">${label[0]}</span><div class="sub" style="margin-top:8px">snapshot ${esc(job.snapshot)} · ${experiments.length} experiment${experiments.length === 1 ? "" : "s"}${job.reasoning ? " · reasoning" : ""}</div><div class="foot"><span>Created on: ${fmtDate(job.created)}</span><span class="arrow">→</span></div></div>`;
  }).join("") || `<div class="empty"><b>No finetuning jobs yet</b>Create one from a snapshot.</div>`}</div>`, [{ label: "Finetuning" }]);
  $("#newJob", content).onclick = () => createJobDialog();
  $("#autopilotBtn", content).onclick = () => autopilotDialog();
  $$("[data-ap]", content).forEach((b) => b.onclick = () => autopilotHistoryDialog(b.dataset.ap));
  $$("[data-apnext]", content).forEach((b) => b.onclick = () => autopilotDialog({ name: b.dataset.apnext, dataset: b.dataset.apds }));
  $$("[data-job]", content).forEach((c) => c.onclick = () => go(`#/finetuning/${encodeURIComponent(c.dataset.job)}`));
}

async function pageJob(name) {
  const view = state.pageContext;
  await ensureOverview(true); assertRender(view);
  const detail = await api(`/api/jobs/${encodeURIComponent(name)}`);
  const { job, experiments, analysis } = detail;
  const configuring = !experiments.length || experiments.every((e) => e.state === "pending");
  const running = experiments.some((e) => e.state === "running");
  const headPill = running ? ["Running", "running"] : configuring ? ["Ready to start experiments", "ready"] : experiments.every((e) => e.state === "succeeded") ? ["Completed", "completed"] : experiments.some((e) => e.state === "failed") ? ["Failed", "failed"] : ["Partially run", "pending"];
  assertRender(view);
  const content = layout("finetuning", `
    <div class="page-head"><div><h1>${esc(name)} <span class="pill ${headPill[1]}">${headPill[0]}</span></h1><div class="meta"><span class="mono">${esc(job.snapshot)}</span><span>· dataset <a href="#/datasets/${encodeURIComponent(detail.snapshot?.dataset || "")}">${esc(detail.snapshot?.dataset || "?")}</a></span><span>· ${detail.snapshot?.training_count ?? "?"} training / ${detail.snapshot?.validation_count ?? "?"} validation</span>${job.reasoning ? "<span>· reasoning</span>" : ""}</div></div>
      <div class="actions">${!configuring && !running ? `<button class="btn" id="btnEval">Start evaluation</button>` : ""}${configuring ? `<button class="btn primary" id="btnStart" ${experiments.length ? "" : "disabled"}>Start experiments</button>` : ""}<button class="btn danger" id="btnDeleteJob">Delete job</button></div></div>
    <div class="wide-narrow"><div id="left"></div><div id="right"></div></div>`,
    [{ label: "Finetuning", href: "#/finetuning" }, { label: name }]);
  $("#btnDeleteJob", content).onclick = () => confirmModal("Delete job", `Delete job "${name}" and all its experiments and weights?`, async () => { await del(`/api/jobs/${encodeURIComponent(name)}`); go("#/finetuning"); });
  if ($("#btnEval", content)) $("#btnEval", content).onclick = () => newEvaluationDialog({ snapshot: job.snapshot, models: experiments.filter((e) => e.state === "succeeded").map((e) => `${name}/${e.number}`) });

  const left = $("#left", content), right = $("#right", content);
  let catalog = state.overview.catalog;
  try {
    const hubInfo = await api("/api/hub/models");
    const known = new Set(catalog.map((c) => c.id));
    catalog = [...catalog, ...hubInfo.models.filter((m) => m.transformers_loadable && !known.has(m.id)).map((m) => ({ id: m.id, params_b: "?", notes: `downloaded · ${m.size_gb} GB` }))];
  } catch (_) {}
  if (configuring) {
    // ---- configuration mode: editable experiment cards + analysis panel
    const drafts = experiments.map((e) => ({ number: e.number, ...e.config }));
    const rec = analysis?.recommendation || null;
    const finished = (await api("/api/models")).filter((m) => m.kind === "fine-tuned").map((m) => m.alias);
    const drawDrafts = () => {
      left.innerHTML = drafts.map((d, i) => `<div ${dataContext("Experiment configuration", d)} class="exp"><div class="head"><span class="dot" style="background:${SERIES[i % SERIES.length]}"></span><h3>Experiment ${i + 1}</h3>${d.number ? `<span class="pill pending">pending · ft-${esc(name)}-${d.number}</span>` : `<span class="pill">draft</span>`}<div class="right">${d.number ? `<button class="btn sm danger" data-rm="${d.number}">Delete</button>` : `<button class="btn sm" data-drop="${i}">Remove</button>`}</div></div>
        ${d.number ? `<div class="kv"><div><div class="k">Base model</div><div class="v mono">${esc(d.base_model)}</div></div><div><div class="k">Training type</div><div class="v">${d.training_type === "lora" ? "LoRA training" : d.training_type === "qlora" ? "QLoRA (4-bit)" : d.training_type === "dpo" ? "DPO alignment (LoRA)" : "Full fine-tuning"}</div></div><div><div class="k">Batch size × accumulation</div><div class="v">${d.batch_size} × ${d.gradient_accumulation_steps}</div></div><div><div class="k">Learning rate</div><div class="v">${d.learning_rate}</div></div><div><div class="k">Epochs</div><div class="v">${d.epochs}</div></div><div><div class="k">Max length</div><div class="v">${d.max_length}</div></div></div>`
        : `<div class="grid"><div class="field"><label>Base model</label><input list="catalogList" data-f="base_model" data-i="${i}" value="${esc(d.base_model)}"></div><div class="field"><label>Training type</label><select data-f="training_type" data-i="${i}"><option value="lora" ${d.training_type === "lora" ? "selected" : ""}>LoRA</option><option value="full" ${d.training_type === "full" ? "selected" : ""}>Full fine-tuning</option><option value="qlora" ${d.training_type === "qlora" ? "selected" : ""}>QLoRA (4-bit base; CUDA + bitsandbytes)</option><option value="dpo" ${d.training_type === "dpo" ? "selected" : ""}>DPO (preference pairs; datapoints need a rejected answer)</option></select></div><div class="field" style="grid-column:1/-1"><label>Refine from a finished experiment (optional)</label><select data-f="refinetune_from" data-i="${i}"><option value="">— start from the base model —</option>${finished.map((f) => `<option value="${esc(f)}" ${d.refinetune_from === f ? "selected" : ""}>${esc(f)}</option>`).join("")}</select></div><div class="field"><label>Batch size</label><input type="number" min="1" max="64" data-f="batch_size" data-i="${i}" value="${d.batch_size}"></div><div class="field"><label>Learning rate</label><input type="number" step="any" data-f="learning_rate" data-i="${i}" value="${d.learning_rate}"></div><div class="field"><label>Number of epochs</label><input type="number" step="0.5" min="0.5" max="50" data-f="epochs" data-i="${i}" value="${d.epochs}"></div><div class="field"><label>Max length (tokens)</label><input type="number" data-f="max_length" data-i="${i}" value="${d.max_length}"></div><div class="field"><label>Gradient accumulation</label><input type="number" min="1" data-f="gradient_accumulation_steps" data-i="${i}" value="${d.gradient_accumulation_steps}"></div><div class="field"><label>LoRA rank / alpha</label><div class="row"><input type="number" data-f="lora_r" data-i="${i}" value="${d.lora_r}"><input type="number" data-f="lora_alpha" data-i="${i}" value="${d.lora_alpha}"></div></div><div class="field"><label>Checkpoint every N steps (0 = off; for neuron drift)</label><input type="number" min="0" data-f="checkpoint_every_steps" data-i="${i}" value="${d.checkpoint_every_steps ?? 0}"></div><div class="field"><label>Keep newest K checkpoints (0 = all)</label><input type="number" min="0" data-f="keep_checkpoints" data-i="${i}" value="${d.keep_checkpoints ?? 0}"></div></div>`}
        </div>`).join("") + `<datalist id="catalogList">${catalog.map((c) => `<option value="${esc(c.id)}">${c.params_b}B · ${esc(c.notes)}</option>`).join("")}</datalist>
        <div class="row"><button class="btn" id="addExp">+ Add experiment</button>${rec ? `<button class="btn" id="addRec">+ Add recommended experiment</button>` : ""}<span class="spacer" style="flex:1"></span><button class="btn primary" id="saveStart" ${drafts.length ? "" : "disabled"}>Save &amp; start experiments</button></div>`;
      const collect = () => $$("[data-f]", left).forEach((el) => { const d = drafts[Number(el.dataset.i)]; d[el.dataset.f] = el.type === "number" ? Number(el.value) : (el.value || (el.dataset.f === "refinetune_from" ? null : el.value)); });
      $$('select[data-f="training_type"]', left).forEach((s) => s.onchange = () => { const lr = $(`input[data-f="learning_rate"][data-i="${s.dataset.i}"]`, left); if (s.value === "dpo" && Number(lr.value) >= 1e-4) lr.value = 0.000005; if (s.value !== "dpo" && Number(lr.value) <= 1e-5) lr.value = s.value === "full" ? 0.00005 : 0.0002; });
      $("#addExp", left).onclick = () => { collect(); const base = rec?.recommended || { base_model: catalog[0].id, training_type: "lora", hyperparameters: { epochs: 3, learning_rate: 2e-4, batch_size: 8, gradient_accumulation_steps: 2, max_length: 512, lora_r: 16, lora_alpha: 32 } }; drafts.push(draftFrom(base)); drawDrafts(); };
      if ($("#addRec", left)) $("#addRec", left).onclick = () => { collect(); const options = rec.options || []; const next = options[drafts.length % Math.max(1, options.length)] || rec.recommended; drafts.push(draftFrom(next)); drawDrafts(); };
      $$("[data-drop]", left).forEach((b) => b.onclick = () => { collect(); drafts.splice(Number(b.dataset.drop), 1); drawDrafts(); });
      $$("[data-rm]", left).forEach((b) => b.onclick = guard(async () => { await del(`/api/jobs/${encodeURIComponent(name)}/experiments/${b.dataset.rm}`); pageJob(name); }));
      const startAll = guard(async () => {
        collect();
        const fresh = drafts.filter((d) => !d.number);
        if (fresh.length) await post(`/api/jobs/${encodeURIComponent(name)}/experiments`, { experiments: fresh.map(({ number, ...cfg }) => cfg) });
        await post(`/api/jobs/${encodeURIComponent(name)}/start`, {});
        showTasks(); pollTasks(); pageJob(name);
      });
      $("#saveStart", left).onclick = startAll;
      if ($("#btnStart", content)) $("#btnStart", content).onclick = startAll;
    };
    const draftFrom = (option) => ({ base_model: option.base_model, training_type: option.training_type, epochs: option.hyperparameters.epochs, learning_rate: option.hyperparameters.learning_rate, batch_size: option.hyperparameters.batch_size, gradient_accumulation_steps: option.hyperparameters.gradient_accumulation_steps, max_length: option.hyperparameters.max_length, lora_r: option.hyperparameters.lora_r, lora_alpha: option.hyperparameters.lora_alpha, assistant_only_loss: false, refinetune_from: null, checkpoint_every_steps: 0, keep_checkpoints: 0 });
    if (!drafts.length && rec?.recommended) drafts.push(draftFrom(rec.recommended));
    drawDrafts();
    right.innerHTML = analysisPanel(analysis, name);
    wireAnalysis(right, name);
  } else {
    // ---- running / completed: experiment cards + training loss comparison
    left.innerHTML = experiments.map((e, i) => `<div ${dataContext("Experiment configuration", e)} class="exp"><div class="head"><span class="dot" style="background:${SERIES[i % SERIES.length]}"></span><h3>Experiment ${e.number}</h3>${e.state === "succeeded" ? `<a class="btn primary sm" href="#/playground?model=${encodeURIComponent(name + "/" + e.number)}">Test model</a><a class="btn sm icon" title="Download weights" href="${scoped(`/api/jobs/${encodeURIComponent(name)}/experiments/${e.number}/download`)}">⬇</a>${(e.checkpoints || []).length ? `<button class="btn sm" data-ckcap="${e.number}" title="Steps ${e.checkpoints.join(", ")}: capture every checkpoint on a dataset to see neuron drift on the Interpretability page">${e.checkpoints.length} checkpoint${e.checkpoints.length === 1 ? "" : "s"} · capture for drift</button>` : ""}` : ""}<div class="right"><span class="pill ${e.state}">${e.state[0].toUpperCase() + e.state.slice(1)}</span>${e.state !== "running" ? `<button class="btn sm danger" data-rm="${e.number}">✕</button>` : ""}</div></div>
      <div class="kv"><div><div class="k">Base model ID</div><div class="v mono">${esc(e.config.base_model)}</div></div><div><div class="k">Batch size</div><div class="v">${e.config.batch_size} × ${e.config.gradient_accumulation_steps}</div></div><div><div class="k">Learning rate</div><div class="v">${e.config.learning_rate}</div></div><div><div class="k">Number of epochs</div><div class="v">${e.config.epochs}</div></div><div><div class="k">${e.config.training_type === "lora" ? "LoRA training" : "Full fine-tuning"}</div><div class="v">${e.config.training_type === "lora" ? `r=${e.config.lora_r} α=${e.config.lora_alpha}` : "all weights"}</div></div><div><div class="k">Result</div><div class="v">${e.state === "succeeded" ? `train loss ${fmtNum(e.train_loss)} · eval loss ${fmtNum(e.metrics?.eval_loss)} · ${e.seconds}s` : e.state === "failed" ? `<span class="error">${esc(e.error || "failed")}</span>` : e.state}</div></div></div>
      ${e.state === "succeeded" ? `<div style="margin-top:10px"><span class="alias">${esc(e.alias)} <button title="copy" data-copy="${esc(e.alias)}">⧉</button></span> <span class="muted" style="font-size:12px">· ${fmtParams(e.total_parameters)} parameters</span></div>` : ""}
      </div>`).join("");
    $$("[data-copy]", left).forEach((b) => b.onclick = () => navigator.clipboard?.writeText(b.dataset.copy).then(() => toast("alias copied")));
    $$("[data-ckcap]", left).forEach((b) => b.onclick = guard(() => checkpointCaptureDialog(name, Number(b.dataset.ckcap))));
    $$("[data-rm]", left).forEach((b) => b.onclick = () => confirmModal("Delete experiment", `Delete experiment ${b.dataset.rm} and its weights?`, async () => { await del(`/api/jobs/${encodeURIComponent(name)}/experiments/${b.dataset.rm}`); pageJob(name); }));
    right.innerHTML = `<div class="panel"><div class="head"><h2>Training loss comparison</h2><div class="right"><label style="font-size:12px;display:flex;gap:6px;align-items:center"><input type="checkbox" id="showEval" checked> eval loss</label></div></div><div class="body"><div class="legend" id="legend"></div><div class="chart" id="lossChart"></div></div></div>` + (analysis ? `<div style="height:16px"></div>${analysisPanel(analysis, name)}` : "");
    const series = experiments.map((e, i) => ({ name: `Exp ${e.number}`, color: SERIES[i % SERIES.length], train: e.loss.filter((r) => r.loss !== undefined).map((r) => ({ x: r.step, y: r.loss })), eval: e.loss.filter((r) => r.eval_loss !== undefined).map((r) => ({ x: r.step, y: r.eval_loss })), on: true }));
    const redraw = () => drawLossChart($("#lossChart", right), series, $("#showEval", right).checked);
    $("#legend", right).innerHTML = series.map((s, i) => `<button data-s="${i}" class="${s.on ? "" : "off"}"><i style="background:${s.color}"></i>${esc(s.name)}</button>`).join("");
    $$("[data-s]", right).forEach((b) => b.onclick = () => { const s = series[Number(b.dataset.s)]; s.on = !s.on; b.classList.toggle("off", !s.on); redraw(); });
    $("#showEval", right).onchange = redraw;
    redraw();
    wireAnalysis(right, name);
    if (running) setTimeout(() => { if (location.hash === `#/finetuning/${encodeURIComponent(name)}`) pageJob(name); }, 4000);
  }
}
function analysisPanel(analysis, job) {
  const a = analysis?.analysis;
  const list = (v) => Array.isArray(v) ? `<ul>${v.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : `<p>${esc(v || "")}</p>`;
  return `<div ${dataContext("Dataset analysis", analysis)} class="panel"><div class="head"><h2>Dataset analysis</h2><div class="right"><button class="btn sm" id="analyze">${a ? "Re-analyze" : "Analyze with the helper LLM"}</button></div></div><div class="body accordion">${a ? [["Task summary", a.task_summary], ["Task characterization", a.task_characterization], ["Knowledge requirements", a.knowledge_requirements], ["Success criteria", a.success_criteria], ["Edge cases", a.edge_cases], ["Datapoints input analysis", a.input_analysis], ["Datapoints output analysis", a.output_analysis]].map(([t, v], i) => `<details ${i === 0 ? "open" : ""}><summary>${t}</summary><div class="inner">${list(v)}</div></details>`).join("") : `<p class="muted">Run the analysis to get a task summary, success criteria, edge cases, and a model recommendation for this snapshot. Needs the helper LLM.</p>`}
    ${analysis?.recommendation?.recommended ? `<details open><summary>Recommended experiment</summary><div class="inner"><b class="mono">${esc(analysis.recommendation.recommended.base_model)}</b> · ${analysis.recommendation.recommended.training_type} · ${analysis.recommendation.recommended.hyperparameters.epochs} epochs · lr ${analysis.recommendation.recommended.hyperparameters.learning_rate}<br><span class="muted">${esc(analysis.recommendation.recommended.why)}</span><br><span class="muted">${analysis.recommendation.dataset.training_count} rows on ${analysis.recommendation.host.memory_gib} GiB; budget ${analysis.recommendation.host.budget_gib} GiB</span></div></details>` : ""}
  </div></div>`;
}
function wireAnalysis(root, job) {
  const b = $("#analyze", root); if (!b) return;
  b.onclick = guard(async () => { await post(`/api/jobs/${encodeURIComponent(job)}/analyze`, {}); showTasks(); pollTasks(); toast("analysis started"); });
}
function drawLossChart(container, series, showEval) {
  const pts = series.filter((s) => s.on).flatMap((s) => [...s.train, ...(showEval ? s.eval : [])]);
  if (!pts.length) { container.innerHTML = `<div class="empty">No loss logged yet.</div>`; return; }
  const W = 720, H = 300, m = { l: 64, r: 20, t: 14, b: 40 };
  const x0 = 0, x1 = Math.max(...pts.map((p) => p.x)) || 1, y1 = Math.max(...pts.map((p) => p.y)) * 1.05 || 1;
  const sx = (v) => m.l + (v / x1) * (W - m.l - m.r), sy = (v) => m.t + (1 - v / y1) * (H - m.t - m.b);
  const path = (p) => p.map((q, i) => `${i ? "L" : "M"}${sx(q.x).toFixed(1)},${sy(q.y).toFixed(1)}`).join(" ");
  const yT = [0, .25, .5, .75, 1].map((f) => f * y1), xT = [0, .25, .5, .75, 1].map((f) => Math.round(f * x1));
  container.innerHTML = `<svg ${dataContext("Training loss series", {series: series.filter((s) => s.on).map((s) => ({name:s.name,train:s.train,eval:showEval?s.eval:[]})), showEval})} viewBox="0 0 ${W} ${H}"><g class="grid">${yT.map((t) => `<line x1="${m.l}" x2="${W - m.r}" y1="${sy(t)}" y2="${sy(t)}"/>`).join("")}</g><g class="axis">${yT.map((t) => `<text x="${m.l - 8}" y="${sy(t) + 4}" text-anchor="end">${t.toFixed(2)}</text>`).join("")}${xT.map((t) => `<text x="${sx(t)}" y="${H - 20}" text-anchor="middle">${t}</text>`).join("")}<text class="axis-title" x="${(W + m.l) / 2}" y="${H - 4}" text-anchor="middle">Training steps</text><text class="axis-title" transform="translate(14 ${(H - m.b) / 2}) rotate(-90)" text-anchor="middle">Training loss</text></g>
    ${series.map((s) => s.on ? `<path d="${path(s.train)}" fill="none" stroke="${s.color}" stroke-width="2"/>${showEval && s.eval.length ? `<path d="${path(s.eval)}" fill="none" stroke="${s.color}" stroke-width="2" stroke-dasharray="5 4"/>` : ""}` : "").join("")}
    <g>${series.map((s) => s.on ? [...s.train.map((p) => ({ ...p, l: `${s.name} train` })), ...(showEval ? s.eval.map((p) => ({ ...p, l: `${s.name} eval` })) : [])].map((p) => `<circle class="dot" cx="${sx(p.x)}" cy="${sy(p.y)}" r="4" fill="var(--panel)" stroke="${s.color}" stroke-width="2" opacity="0" data-l="step ${p.x} · ${p.l} ${p.y.toFixed(4)}"/>`).join("") : "").join("")}</g>
    <line id="xh" x1="0" x2="0" y1="${m.t}" y2="${H - m.b}" stroke="var(--text-3)" stroke-dasharray="3 3" opacity="0"/></svg><div class="tip"></div>`;
  const svg = $("svg", container), tip = $(".tip", container), xh = $("#xh", container), dots = $$(".dot", container);
  svg.onmousemove = (ev) => {
    const r = svg.getBoundingClientRect(); const px = ((ev.clientX - r.left) / r.width) * W;
    let best = null, bd = 1e9; for (const d of dots) { const dx = Math.abs(+d.getAttribute("cx") - px); if (dx < bd) { bd = dx; best = d; } }
    if (!best) return; const cx = best.getAttribute("cx");
    dots.forEach((d) => d.setAttribute("opacity", d.getAttribute("cx") === cx ? "1" : "0"));
    xh.setAttribute("x1", cx); xh.setAttribute("x2", cx); xh.setAttribute("opacity", "1");
    tip.style.display = "block"; tip.innerHTML = dots.filter((d) => d.getAttribute("cx") === cx).map((d) => esc(d.dataset.l)).join("<br>");
    tip.style.left = `${Math.min(ev.clientX - r.left + 12, r.width - 200)}px`; tip.style.top = `${ev.clientY - r.top - 10}px`;
  };
  svg.onmouseleave = () => { tip.style.display = "none"; xh.setAttribute("opacity", "0"); dots.forEach((d) => d.setAttribute("opacity", "0")); };
}

// ------------------------------------------------------------------ evaluations
async function pageEvaluations() {
  const view = state.pageContext;
  await ensureOverview(true); assertRender(view);
  const evals = await api("/api/evals");
  assertRender(view);
  const content = layout("evaluations", `<div class="page-head"><h1>Evaluations</h1><div class="actions"><button class="btn primary" id="newEval">+ Create evaluation</button></div></div><div class="cards">${evals.map((e) => `<div ${dataContext("Evaluation", e)} class="card clickable" data-ev="${esc(e.name)}"><h3>${esc(e.name)}</h3><span class="pill ${e.state === "succeeded" ? "completed" : e.state}">${e.state === "succeeded" ? "Completed" : e.state}</span><div class="sub" style="margin-top:8px">${e.models.length} model${e.models.length === 1 ? "" : "s"} · ${e.metrics.join(", ")} · ${e.datapoints} datapoints · snapshot ${esc(e.snapshot)}</div><div class="foot"><span>Created on: ${fmtDate(e.created)}</span><span class="arrow">→</span></div></div>`).join("") || `<div class="empty"><b>No evaluations yet</b>Compare your experiments against a base model on the validation split.</div>`}</div>`, [{ label: "Evaluations" }]);
  $("#newEval", content).onclick = () => newEvaluationDialog({});
  $$("[data-ev]", content).forEach((c) => c.onclick = () => go(`#/evaluations/${encodeURIComponent(c.dataset.ev)}`));
}
async function newEvaluationDialog({ snapshot = null, models = [] }) {
  await ensureOverview(true);
  const snaps = state.overview.tree.datasets.flatMap((d) => d.snapshots);
  if (!snaps.length) return toast("create a snapshot first", true);
  const allModels = await api("/api/models");
  const metrics = await api("/api/metrics");
  let tab = "agentic";
  const chosen = new Set(models), chosenMetrics = new Set(metrics.filter((m) => m.kind === "rubric").slice(0, 3).map((m) => m.name));
  const body = document.createElement("div");
  const m = modal({ title: "New evaluation", body, wide: true, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Create evaluation</button>` });
  const modelPicker = () => `<div class="field"><label>Models to evaluate</label><div class="chips">${allModels.map((mo) => `<button data-m="${esc(mo.id)}" class="${chosen.has(mo.id) ? "on" : ""}">${esc(mo.kind === "fine-tuned" ? mo.alias : mo.id.split("/").pop())}${mo.kind === "base" ? " (base)" : ""}</button>`).join("")}</div><div class="hint">Fine-tuned experiments and base models can be compared side by side.</div></div>`;
  const draw = () => {
    body.innerHTML = `<div class="tabs"><button class="${tab === "agentic" ? "on" : ""}" data-t="agentic">Agentic evaluation</button><button class="${tab === "byoe" ? "on" : ""}" data-t="byoe">Bring your own evals</button></div>
      <div class="row"><div class="field"><label>Name</label><input id="evName" placeholder="v1-compare"></div><div class="field"><label>Snapshot (validation split is used)</label><select id="evSnap">${snaps.map((s) => `<option value="${esc(s.name)}" ${s.name === snapshot ? "selected" : ""}>${esc(s.name)} — ${s.validation_count} validation rows</option>`).join("")}</select></div><div class="field" style="max-width:160px"><label>Limit rows (optional)</label><input id="evLimit" type="number" min="1"></div></div>
      ${modelPicker()}
      ${tab === "agentic" ? `<div class="field"><label>Selected metrics</label><div class="chips">${metrics.map((me) => `<button data-me="${esc(me.name)}" class="${chosenMetrics.has(me.name) ? "on" : ""}">${esc(me.name)} <span class="muted">${me.kind}</span></button>`).join("")}</div><div class="hint">Rubric metrics are judged by the helper LLM; code metrics need no model. <a href="#/metrics">Browse metrics</a></div></div>`
      : `<div class="two"><div><div class="field"><label>Evaluation URL</label><input id="byoeUrl" placeholder="http://127.0.0.1:9000/evaluate"></div><div class="field"><label>URL token (optional)</label><input id="byoeToken" placeholder="sent as Authorization: Bearer"></div></div><div class="panel"><div class="head"><h2>Evaluation server setup</h2></div><div class="body" style="font-size:12.5px">Your server receives one POST per (datapoint, model):<pre class="mono" style="white-space:pre-wrap">{"datapoint": {"messages": [...system, user, assistant]},\n "prediction": "model answer",\n "model_name": "ft-job-1"}</pre>and returns<pre class="mono">{"score": 0.0 to 1.0, "reason": "optional"}</pre></div></div></div>`}`;
    $$("[data-t]", body).forEach((b) => b.onclick = () => { tab = b.dataset.t; draw(); });
    $$("[data-m]", body).forEach((b) => b.onclick = () => { chosen.has(b.dataset.m) ? chosen.delete(b.dataset.m) : chosen.add(b.dataset.m); b.classList.toggle("on"); });
    $$("[data-me]", body).forEach((b) => b.onclick = () => { chosenMetrics.has(b.dataset.me) ? chosenMetrics.delete(b.dataset.me) : chosenMetrics.add(b.dataset.me); b.classList.toggle("on"); });
  };
  draw();
  $("[data-cancel]", m.el).onclick = m.close;
  $("[data-ok]", m.el).onclick = guard(async () => {
    const name = $("#evName", body).value.trim(); if (!name) throw new Error("give the evaluation a name");
    if (!chosen.size) throw new Error("pick at least one model");
    const limit = $("#evLimit", body).value ? Number($("#evLimit", body).value) : null;
    if (tab === "agentic") {
      if (!chosenMetrics.size) throw new Error("pick at least one metric");
      await post("/api/evals", { name, snapshot: $("#evSnap", body).value, models: [...chosen], metrics: [...chosenMetrics], limit });
    } else {
      const url = $("#byoeUrl", body).value.trim(); if (!url) throw new Error("enter the evaluation URL");
      await post("/api/evals/byoe", { name, snapshot: $("#evSnap", body).value, models: [...chosen], url, token: $("#byoeToken", body).value.trim() || null, limit });
    }
    m.close(); showTasks(); pollTasks(); go("#/evaluations");
  });
}
async function pageEval(name) {
  const view = state.pageContext;
  await ensureOverview(true); assertRender(view);
  const { summary, rows } = await api(`/api/evals/${encodeURIComponent(name)}`);
  const metrics = summary.metrics, models = summary.models;
  let metric = metrics[0], offset = 0; const limit = 10;
  const byKey = {}; rows.forEach((r) => { byKey[`${r.datapoint}|${r.model}|${r.metric}`] = r; });
  const datapoints = [...new Set(rows.map((r) => r.datapoint))].sort((a, b) => a - b);
  assertRender(view);
  const content = layout("evaluations", `
    <div class="page-head"><div><h1>${esc(name)} <span class="pill ${summary.state === "succeeded" ? "completed" : summary.state}">${summary.state === "succeeded" ? "Completed" : summary.state}</span></h1><div class="meta"><span>snapshot <b>${esc(summary.snapshot)}</b></span><span>· ${models.length} models selected</span><span>· ${summary.datapoints} datapoints</span>${summary.error ? `<span class="error">${esc(summary.error)}</span>` : ""}</div></div><div class="actions"><button class="btn danger" id="delEval">Delete</button></div></div>
    <div class="cards" style="margin-bottom:18px">${(summary.leaderboard || []).map((b) => `<div class="card"><div style="display:flex;align-items:center;gap:10px"><h3 style="flex:1;font-size:14px" class="mono">${esc(b.model)}</h3><span class="pct ${pctClass(b.overall ?? 0)}" style="font-size:15px">${fmtPct(b.overall)}</span></div>${metrics.map((mt) => `<div style="display:flex;justify-content:space-between;margin-top:6px"><span>${esc(mt)}</span><span class="pct ${pctClass(b.scores[mt] ?? 0)}">${fmtPct(b.scores[mt])}</span></div>`).join("")}</div>`).join("")}</div>
    <div class="chips" id="metricTabs" style="margin-bottom:12px">${metrics.map((mt) => `<button data-mt="${esc(mt)}" class="${mt === metric ? "on" : ""}">${esc(mt)}</button>`).join("")}</div>
    <div class="panel" style="margin-bottom:14px"><div class="head"><h2>Score distribution</h2><span class="muted">per model for the selected metric; 10 bins from 0% to 100%</span></div><div class="body" id="scoreHist"></div></div>
    <div class="panel" id="grid"></div>`, [{ label: "Evaluations", href: "#/evaluations" }, { label: name }]);
  $$(".cards > .card", content).forEach((el, i) => bindContext(el, `Evaluation leaderboard: ${summary.leaderboard[i]?.model}`, summary.leaderboard[i]));
  const drawHist = () => {
    $("#scoreHist", content).innerHTML = `<div class="cards">${models.map((mo, i) => { const vals = datapoints.map((d) => byKey[`${d}|${mo}|${metric}`]?.score).filter((v) => v !== undefined); const counts = Array(10).fill(0); vals.forEach((v) => counts[Math.min(9, Math.floor(v * 10))]++); return `<div><div class="mono" style="font-size:12px;margin-bottom:4px">${esc(mo)}</div>${histogram({ bins: Array.from({ length: 11 }, (_, k) => k * 10), counts }, SERIES[i % SERIES.length], 320, 80)}</div>`; }).join("")}</div>`;
    const metricRows = rows.filter((r) => r.metric === metric);
    bindContext($("#scoreHist", content).closest(".panel"), `Score distribution: ${metric}`, { evaluation: name, metric, rows: metricRows });
    $$("#scoreHist svg", content).forEach((chart, i) => bindContext(chart, `Score distribution: ${models[i]} / ${metric}`, { evaluation: name, model: models[i], metric, rows: metricRows.filter((r) => r.model === models[i]) }));
  };
  $("#delEval", content).onclick = () => confirmModal("Delete evaluation", `Delete evaluation "${name}"?`, async () => { await del(`/api/evals/${encodeURIComponent(name)}`); go("#/evaluations"); });
  const draw = () => {
    const page = datapoints.slice(offset, offset + limit);
    const avg = (model) => { const vals = datapoints.map((d) => byKey[`${d}|${model}|${metric}`]?.score).filter((v) => v !== undefined); return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null; };
    $("#grid", content).innerHTML = `<table><thead><tr><th>Datapoint (user message)</th>${models.map((mo) => `<th class="num mono">${esc(mo)}</th>`).join("")}</tr></thead><tbody>
      <tr class="avg"><td>● Average (${esc(metric)})</td>${models.map((mo) => `<td class="num"><span class="pct ${pctClass(avg(mo) ?? 0)}">${fmtPct(avg(mo))}</span></td>`).join("")}</tr>
      ${page.map((d) => { const any = models.map((mo) => byKey[`${d}|${mo}|${metric}`]).find(Boolean); return `<tr><td class="truncate">${esc(any?.user || `datapoint ${d}`)}</td>${models.map((mo) => { const r = byKey[`${d}|${mo}|${metric}`]; return `<td class="num">${r ? `<span class="pct ${pctClass(r.score)}" style="cursor:pointer" data-cell="${d}|${esc(mo)}">${fmtPct(r.score)}</span>` : "–"}</td>`; }).join("")}</tr>`; }).join("")}
      </tbody></table><div class="pager"><span>Results ${datapoints.length ? offset + 1 : 0} - ${Math.min(offset + limit, datapoints.length)} of ${datapoints.length}</span><span class="spacer"></span><button class="btn sm" id="prev" ${offset === 0 ? "disabled" : ""}>‹</button><span class="cur">${Math.floor(offset / limit) + 1}</span><button class="btn sm" id="next" ${offset + limit >= datapoints.length ? "disabled" : ""}>›</button></div>`;
    bindContext($("#grid", content), `Evaluation results: ${name} / ${metric}`, { evaluation: name, metric, rows: rows.filter((r) => r.metric === metric && page.includes(r.datapoint)) });
    $$("#grid tbody tr", content).forEach((el, i) => bindContext(el, i === 0 ? `Average: ${metric}` : `Datapoint ${page[i - 1]}`, { evaluation: name, metric, rows: rows.filter((r) => r.metric === metric && (i === 0 || r.datapoint === page[i - 1])) }));
    $$("[data-cell]", content).forEach((el) => { const [d, mo] = el.dataset.cell.split("|"); bindContext(el, `Datapoint ${d}: ${mo} / ${metric}`, byKey[`${d}|${mo}|${metric}`]); });
    $("#prev", content).onclick = () => { offset -= limit; draw(); };
    $("#next", content).onclick = () => { offset += limit; draw(); };
    $$("[data-cell]", content).forEach((c) => c.onclick = () => { const [d, mo] = c.dataset.cell.split("|"); evalDetails(Number(d), mo, models, metrics, byKey); });
  };
  $$("[data-mt]", content).forEach((b) => b.onclick = () => { metric = b.dataset.mt; $$("[data-mt]", content).forEach((x) => x.classList.toggle("on", x === b)); offset = 0; draw(); drawHist(); });
  draw();
  drawHist();
}
function evalDetails(d, model, models, metrics, byKey) {
  const first = models.map((mo) => metrics.map((mt) => byKey[`${d}|${mo}|${mt}`]).find(Boolean)).find(Boolean);
  let tab = "model", current = model;
  const body = document.createElement("div");
  const m = modal({ title: "Evaluation result details", body, wide: true, footer: `<button class="btn" data-close>Close</button>` });
  $("[data-close]", m.el).onclick = m.close;
  const draw = () => {
    body.innerHTML = `<div class="tabs"><button class="${tab === "datapoint" ? "on" : ""}" data-t="datapoint">Datapoint</button><button class="${tab === "model" ? "on" : ""}" data-t="model">Model results</button></div>` + (tab === "datapoint"
      ? `<span class="tag system">System prompt</span><div class="msg" style="background:var(--panel-2);padding:10px 12px;border-radius:8px;margin:6px 0 14px">${esc(first?.system || "(none)")}</div><span class="tag user">User message</span><div class="msg" style="background:var(--panel-2);padding:10px 12px;border-radius:8px;margin:6px 0 14px">${esc(first?.user || "")}</div><span class="tag assistant">Original assistant response</span><div class="msg" style="background:var(--panel-2);padding:10px 12px;border-radius:8px;margin:6px 0">${esc(first?.reference || "")}</div>`
      : `<div class="chips" style="margin-bottom:12px">${models.map((mo) => `<button data-mo="${esc(mo)}" class="${mo === current ? "on" : ""}">${esc(mo)}</button>`).join("")}</div>` + metrics.map((mt) => { const r = byKey[`${d}|${current}|${mt}`]; if (!r) return ""; const verdicts = r.verdicts || []; return `<div class="panel" style="margin-bottom:12px"><div class="head"><h2>${esc(mt)}</h2><span class="pct ${pctClass(r.score)}">${fmtPct(r.score)}</span><span class="muted">${esc(r.reason)}</span></div><div class="body">${verdicts.length ? `<div class="two"><div><div class="muted" style="margin-bottom:6px">LLM should follow</div><ul class="rules">${verdicts.filter((v) => v.kind === "should").map((v) => `<li><span class="mark ${v.pass ? "pass" : "fail"}">${v.pass ? "✓" : "✕"}</span><div>${esc(v.rule)}<div class="muted" style="font-size:12px">${esc(v.reason)}</div></div></li>`).join("")}</ul></div><div><div class="muted" style="margin-bottom:6px">LLM should not follow</div><ul class="rules">${verdicts.filter((v) => v.kind === "should_not").map((v) => `<li><span class="mark ${v.pass ? "pass" : "fail"}">${v.pass ? "✓" : "✕"}</span><div>${esc(v.rule)}<div class="muted" style="font-size:12px">${esc(v.reason)}</div></div></li>`).join("")}</ul></div></div>` : ""}<div style="margin-top:10px"><span class="tag assistant">Model response</span><div class="msg mono" style="background:var(--panel-2);padding:10px 12px;border-radius:8px;margin-top:6px">${esc(r.prediction)}</div></div></div></div>`; }).join(""));
    bindContext(body, `Evaluation datapoint ${d}: ${current}`, { datapoint: d, model: current, rows: metrics.map((mt) => byKey[`${d}|${current}|${mt}`]).filter(Boolean) });
    $$(".panel", body).forEach((el) => { const mt = $("h2", el)?.textContent; bindContext(el, `Datapoint ${d}: ${current} / ${mt}`, byKey[`${d}|${current}|${mt}`]); });
    $$("[data-t]", body).forEach((b) => b.onclick = () => { tab = b.dataset.t; draw(); });
    $$("[data-mo]", body).forEach((b) => b.onclick = () => { current = b.dataset.mo; draw(); });
  };
  draw();
}

// ------------------------------------------------------------------ metrics
async function pageMetrics() {
  const view = state.pageContext;
  await ensureOverview(true); assertRender(view);
  const metrics = await api("/api/metrics");
  assertRender(view);
  const content = layout("metrics", `<div class="page-head"><h1>Metrics</h1><div class="actions"><button class="btn primary" id="newMetric">+ Create metric</button></div></div><div class="cards">${metrics.map((me) => `<div ${dataContext("Metric", me)} class="card clickable" data-me="${esc(me.name)}"><h3>${esc(me.name)}</h3><div class="sub">${esc(me.description || (me.kind === "code" ? `code check: ${me.check}` : me.kind === "webhook" ? `webhook: ${me.webhook.url}` : ""))}</div><div class="foot"><span>${me.kind === "rubric" ? `<span class="pill good">${me.rules.should.length} should</span> <span class="pill bad">${me.rules.should_not.length} should not</span>` : `<span class="pill">${me.kind}</span>`}</span><span class="arrow">→</span></div></div>`).join("")}</div>`, [{ label: "Evaluations", href: "#/evaluations" }, { label: "Metrics" }]);
  $("#newMetric", content).onclick = () => createMetricDialog();
  $$("[data-me]", content).forEach((c) => c.onclick = () => editMetricDialog(metrics.find((x) => x.name === c.dataset.me)));
}
function createMetricDialog() {
  const snaps = state.overview.tree.datasets.flatMap((d) => d.snapshots);
  let kind = "rubric";
  const body = document.createElement("div");
  const m = modal({ title: "Create metric", body, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Create</button>` });
  const draw = () => {
    body.innerHTML = `<div class="tabs"><button class="${kind === "rubric" ? "on" : ""}" data-k="rubric">Rules (LLM judge)</button><button class="${kind === "code" ? "on" : ""}" data-k="code">Code check</button><button class="${kind === "webhook" ? "on" : ""}" data-k="webhook">Webhook</button></div>
      <div class="field"><label>Name</label><input id="mName" placeholder="Tone"></div>
      <div class="field"><label>Description</label><textarea id="mDesc" placeholder="What good looks like, e.g. answers are polite and never blame the customer."></textarea></div>
      ${kind === "rubric" ? `<div class="row"><div class="field"><label>Generate rules from a snapshot</label><select id="mSnap">${snaps.map((s) => `<option value="${esc(s.name)}">${esc(s.name)}</option>`).join("")}</select></div><button class="btn" id="genRules">✨ Leverage AI to create rules</button></div><div class="two"><div class="field"><label>Should follow (one per line)</label><textarea id="mShould"></textarea></div><div class="field"><label>Should not follow (one per line)</label><textarea id="mShouldNot"></textarea></div></div>`
      : kind === "code" ? `<div class="field"><label>Check</label><select id="mCheck"><option value="exact_match">exact match (normalized)</option><option value="json_valid">JSON valid and equal</option><option value="token_f1">token F1</option><option value="similarity">string similarity (LCS ratio)</option><option value="contains_reference">contains reference</option></select></div>`
      : `<div class="field"><label>Evaluation URL</label><input id="mUrl" placeholder="http://127.0.0.1:9000/evaluate"></div><div class="field"><label>Token (optional)</label><input id="mToken"></div>`}`;
    $$("[data-k]", body).forEach((b) => b.onclick = () => { kind = b.dataset.k; draw(); });
    if ($("#genRules", body)) $("#genRules", body).onclick = guard(async () => {
      const name = $("#mName", body).value.trim(); if (!name) throw new Error("name the metric first");
      if (!snaps.length) throw new Error("create a snapshot first");
      await post("/api/metrics/generate", { name, snapshot: $("#mSnap", body).value, description: $("#mDesc", body).value.trim() });
      m.close(); showTasks(); pollTasks(); toast("generating rules; the metric appears when the task finishes");
    });
  };
  draw();
  $("[data-cancel]", m.el).onclick = m.close;
  $("[data-ok]", m.el).onclick = guard(async () => {
    const name = $("#mName", body).value.trim(); if (!name) throw new Error("give the metric a name");
    const lines = (id) => ($(id, body)?.value || "").split("\n").map((s) => s.trim()).filter(Boolean);
    const payload = { name, kind, description: $("#mDesc", body).value.trim() };
    if (kind === "rubric") Object.assign(payload, { should: lines("#mShould"), should_not: lines("#mShouldNot") });
    if (kind === "code") payload.check = $("#mCheck", body).value;
    if (kind === "webhook") Object.assign(payload, { url: $("#mUrl", body).value.trim() });
    await post("/api/metrics", payload); m.close(); pageMetrics();
  });
}
function editMetricDialog(me) {
  if (!me) return;
  const should = [...(me.rules?.should || [])], shouldNot = [...(me.rules?.should_not || [])];
  const body = document.createElement("div");
  const m = modal({ title: me.name, body, wide: true, footer: `<button class="btn danger left" id="mDel">Delete metric</button><button class="btn" data-cancel>Cancel</button>${me.kind === "rubric" ? `<button class="btn primary" data-ok>Save rules</button>` : ""}` });
  const draw = () => {
    const list = (arr, key) => `<ul class="rules">${arr.map((r, i) => `<li><span class="mark ${key === "should" ? "pass" : "fail"}">${key === "should" ? "✓" : "✕"}</span><input data-k="${key}" data-i="${i}" value="${esc(r)}"><button class="btn sm icon" data-x="${key}:${i}">✕</button></li>`).join("")}</ul><button class="btn sm" data-add="${key}">+ Add rule</button>`;
    body.innerHTML = `<p class="muted">${esc(me.description || "")}</p>` + (me.kind === "rubric" ? `<div class="two"><div><div style="font-weight:600;margin-bottom:6px;color:var(--good)">LLM should follow</div>${list(should, "should")}</div><div><div style="font-weight:600;margin-bottom:6px;color:var(--bad)">LLM should not follow</div>${list(shouldNot, "should_not")}</div></div>` : me.kind === "code" ? `<p>Code check: <b class="mono">${esc(me.check)}</b></p>` : `<p>Webhook: <b class="mono">${esc(me.webhook.url)}</b></p>`);
    const collect = () => $$("[data-k]", body).forEach((el) => (el.dataset.k === "should" ? should : shouldNot)[Number(el.dataset.i)] = el.value);
    $$("[data-add]", body).forEach((b) => b.onclick = () => { collect(); (b.dataset.add === "should" ? should : shouldNot).push(""); draw(); });
    $$("[data-x]", body).forEach((b) => b.onclick = () => { collect(); const [k, i] = b.dataset.x.split(":"); (k === "should" ? should : shouldNot).splice(Number(i), 1); draw(); });
    body.collect = collect;
  };
  draw();
  $("[data-cancel]", m.el).onclick = m.close;
  $("#mDel", m.el).onclick = () => confirmModal("Delete metric", `Delete metric "${me.name}"?`, async () => { await del(`/api/metrics/${encodeURIComponent(me.name)}`); m.close(); pageMetrics(); });
  if ($("[data-ok]", m.el)) $("[data-ok]", m.el).onclick = guard(async () => { body.collect(); await put(`/api/metrics/${encodeURIComponent(me.name)}`, { should: should.filter(Boolean), should_not: shouldNot.filter(Boolean) }); m.close(); pageMetrics(); });
}

// ------------------------------------------------------------------ playground
async function pagePlayground(preset) {
  const view = state.pageContext;
  await ensureOverview(true); assertRender(view);
  const models = await api("/api/models");
  try { state.repositories = await api("/api/repositories"); } catch (_) { state.repositories = []; }
  const p = state.playground;
  if (preset) { p.model = preset; p.history = []; }
  if (!p.model) p.model = (models.find((m) => m.kind === "fine-tuned") || models[0])?.id || null;
  assertRender(view);
  const content = layout("playground", `<div class="page-head"><h1>Playground</h1><span class="meta">Test base and fine-tuned models before you deploy them.</span></div>
    <div class="play"><div class="panel"><div class="head"><h2>Settings</h2></div><div class="body">
      <div class="field"><label>Model</label><select id="pgModel">${models.map((m) => `<option value="${esc(m.id)}" ${m.id === p.model ? "selected" : ""}>${esc(m.kind === "fine-tuned" ? m.alias : m.id)}${m.status === "up" ? " ●" : ""}</option>`).join("")}</select><div class="hint">● = loaded in memory. Others load on first message.</div></div>
      <div class="field"><label>System prompt</label><textarea id="pgSystem" placeholder="You are a helpful assistant.">${esc(p.system)}</textarea></div>
      <div class="field"><label>Repositories (RAG): retrieved passages are added to the system prompt</label><select id="pgRepos" multiple size="3">${(state.repositories || []).map((r) => `<option value="${esc(r.name)}" ${(p.repositories || []).includes(r.name) ? "selected" : ""}>${esc(r.name)} (${r.chunk_count} chunks)</option>`).join("")}</select><div class="row"><div class="field"><label>Passages</label><input id="pgRepoLimit" type="number" min="1" max="20" value="${p.repoLimit ?? 3}"></div><div class="field"><label>Min similarity</label><input id="pgRepoThreshold" type="number" min="-1" max="1" step="0.05" value="${p.repoThreshold ?? 0}"></div></div></div>
      <div class="field"><label>Creativity level: <b id="pgTempV">${p.temperature}</b></label><input id="pgTemp" type="range" min="0" max="1" step="0.05" value="${p.temperature}"><div class="hint">0 = deterministic. The slider sets the sampling temperature.</div></div>
      <button class="btn" id="pgReset">Reset conversation</button>
    </div></div>
    <div class="panel" style="display:flex;flex-direction:column"><div class="head"><h2>Chat</h2><span class="muted mono" id="pgLatency"></span></div><div class="chatlog" id="pgLog"></div><div class="composer"><textarea id="pgInput" placeholder="Send a message"></textarea><button class="btn primary" id="pgSend">Send</button></div></div></div>`, [{ label: "Playground" }]);
  const log = $("#pgLog", content);
  const drawLog = () => { log.innerHTML = p.history.map((m) => `<div class="bubble ${m.role}">${esc(m.content)}</div>`).join("") || `<div class="empty"><b>Start a conversation</b>Pick a model and send a message.</div>`; log.scrollTop = log.scrollHeight; };
  drawLog();
  $("#pgModel", content).onchange = (e) => { p.model = e.target.value; };
  $("#pgSystem", content).oninput = (e) => { p.system = e.target.value; };
  $("#pgTemp", content).oninput = (e) => { p.temperature = Number(e.target.value); $("#pgTempV", content).textContent = e.target.value; };
  $("#pgReset", content).onclick = () => { p.history = []; drawLog(); };
  const send = guard(async () => {
    const text = $("#pgInput", content).value.trim(); if (!text || !p.model) return;
    $("#pgInput", content).value = "";
    p.history.push({ role: "user", content: text }); const reply = { role: "assistant", content: "" }; p.history.push(reply); drawLog();
    $("#pgSend", content).disabled = true;
    const messages = [...(p.system.trim() ? [{ role: "system", content: p.system.trim() }] : []), ...p.history.slice(0, -1)];
    try {
      p.repositories = [...$("#pgRepos", content).selectedOptions].map((o) => o.value); p.repoLimit = Number($("#pgRepoLimit", content).value) || 3; p.repoThreshold = Number($("#pgRepoThreshold", content).value) || 0;
      const response = await rawFetch("/api/playground/chat", { method: "POST", body: JSON.stringify({ model: p.model, messages, temperature: p.temperature, max_tokens: 512, repositories: p.repositories.length ? { ids: p.repositories, limit: p.repoLimit, similarity_threshold: p.repoThreshold } : null }) });
      if (!response.ok) { let d = response.statusText; try { d = (await response.json()).detail; } catch (_) {} throw new Error(d); }
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
      for (;;) {
        const { value, done } = await reader.read(); if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx; while ((idx = buffer.indexOf("\n\n")) >= 0) {
          const line = buffer.slice(0, idx).trim(); buffer = buffer.slice(idx + 2);
          if (!line.startsWith("data:")) continue;
          const payload = JSON.parse(line.slice(5));
          if (payload.document_chunks) { reply.content += "Retrieved: " + payload.document_chunks.map((c, i) => `[${i + 1}] ${c.document} (${c.similarity_score.toFixed(2)})`).join(", ") + "\n\n"; drawLog(); }
          if (payload.delta) { reply.content += payload.delta; drawLog(); }
          if (payload.error) throw new Error(payload.error);
          if (payload.done) $("#pgLatency", content).textContent = `${payload.seconds}s`;
        }
      }
    } catch (e) { p.history.pop(); drawLog(); throw e; } finally { $("#pgSend", content).disabled = false; }
  });
  $("#pgSend", content).onclick = send;
  $("#pgInput", content).onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } };
}

// ------------------------------------------------------------------ models + hugging face hub
async function pageModels() {
  const view = state.pageContext;
  await ensureOverview(true); assertRender(view);
  const [models, hubInfo] = await Promise.all([api("/api/models"), api("/api/hub/models")]);
  assertRender(view);
  const content = layout("models", `<div class="page-head"><h1>Models</h1><span class="meta">${models.filter((m) => m.kind === "fine-tuned").length} fine-tuned · ${models.filter((m) => m.kind === "base").length} catalog · ${hubInfo.models.length} in the Hub cache · ${hubInfo.free_disk_gb} GB free</span></div>
    <div class="panel"><div class="head"><h2>Fine-tuned and catalog models</h2></div><table><thead><tr><th>Model</th><th>Kind</th><th>Base model</th><th class="num">Parameters</th><th>Status</th><th>Created</th><th></th></tr></thead><tbody>${models.map((m) => `<tr ${dataContext("Model", m)}><td><b class="mono">${esc(m.alias)}</b>${m.kind === "fine-tuned" ? `<div class="muted mono" style="font-size:11px">${esc(m.id)}</div>` : `<div class="muted" style="font-size:12px">${esc(m.notes || "")}</div>`}</td><td><span class="pill ${m.kind === "fine-tuned" ? "info" : ""}">${m.kind}${m.training_type ? " · " + m.training_type : ""}</span></td><td class="mono">${esc(m.base_model)}</td><td class="num">${fmtParams(m.parameters)}</td><td><span class="pill ${m.status}">${m.status}</span></td><td>${fmtDate(m.created)}</td><td style="text-align:right;white-space:nowrap"><button class="btn sm" data-toggle="${esc(m.id)}" data-status="${m.status}">${m.status === "up" ? "Unload" : "Load"}</button> <a class="btn sm" href="#/playground?model=${encodeURIComponent(m.id)}">Test</a>${m.kind === "fine-tuned" ? ` <a class="btn sm icon" title="Download" href="/api/p/${encodeURIComponent(state.project)}/jobs/${encodeURIComponent(m.id.split("/")[0])}/experiments/${m.id.split("/")[1]}/download">⬇</a> <button class="btn sm" data-upload="${esc(m.id)}">Upload to Hub</button>` : ""}</td></tr>`).join("")}</tbody></table></div>
    <div class="panel" style="margin-top:16px"><div class="head"><h2>Hugging Face Hub</h2><span class="muted">token: ${hubInfo.token.configured ? `<span class="pill good">${esc(hubInfo.token.masked)}</span>` : '<span class="pill warn">not set (needed for gated models and uploads)</span>'}</span><div class="right"><input id="hfToken" placeholder="hf_… token" style="width:220px"><button class="btn sm" id="hfSave">Save token</button></div></div>
      <div class="body">
        <div class="row"><div class="field" style="flex:2"><label>Search the Hugging Face Hub (any open model)</label><input id="hubQ" placeholder="SmolLM2, Qwen2.5, gemma-3, phi, all-MiniLM…"></div><div class="field" style="max-width:170px"><label>Task</label><select id="hubTask"><option value="text-generation">text generation</option><option value="embeddings">embeddings (RAG)</option><option value="sentence-similarity">sentence similarity</option><option value="any">any</option></select></div><div class="field" style="max-width:150px"><label>Max parameters (B)</label><input id="hubMax" type="number" step="0.1" value="2"></div><button class="btn primary" id="hubSearch">Search</button></div>
        <div id="hubResults"></div>
        <h3 style="margin:16px 0 6px">Downloaded (local cache)</h3>
        <table><thead><tr><th>Repository</th><th class="num">Size</th><th>Loadable</th><th>Last used</th><th></th></tr></thead><tbody>${hubInfo.models.map((m) => `<tr ${dataContext("Model", m)}><td class="mono">${esc(m.id)}</td><td class="num">${m.size_gb} GB</td><td>${m.transformers_loadable ? '<span class="pill good">transformers</span>' : m.mlx ? '<span class="pill info">MLX (helper LLM only)</span>' : '<span class="pill">other</span>'}</td><td>${m.last_accessed ? fmtDate(new Date(m.last_accessed * 1000).toISOString()) : "–"}</td><td style="text-align:right;white-space:nowrap">${m.transformers_loadable ? `<a class="btn sm" href="#/playground?model=${encodeURIComponent(m.id)}">Test</a> ` : ""}<button class="btn sm danger" data-hubdel="${esc(m.id)}">Remove</button></td></tr>`).join("") || `<tr><td colspan="5" class="muted">Nothing cached yet.</td></tr>`}</tbody></table>
      </div></div>
    <p class="muted" style="margin-top:12px">OpenAI-compatible API: <span class="mono">${location.origin}/v1/chat/completions</span> with <span class="mono">"model": "&lt;alias or id&gt;"</span>. API keys (API Keys page) authenticate this endpoint; model ids are <span class="mono">${esc(state.project)}/&lt;job&gt;/&lt;n&gt;</span>. Any downloaded transformers model can be a base model in a fine-tuning job.</p>`, [{ label: "Models" }]);
  $$("[data-toggle]", content).forEach((b) => b.onclick = guard(async () => { b.disabled = true; b.innerHTML = '<span class="spinner"></span>'; await post(`/api/models/${slug(b.dataset.toggle)}/${b.dataset.status === "up" ? "down" : "up"}`, {}); pageModels(); }));
  $$("[data-upload]", content).forEach((b) => b.onclick = () => {
    const m = modal({ title: `Upload ${b.dataset.upload} to the Hub`, body: `<div class="field"><label>Repository id (user/name)</label><input id="upRepo" placeholder="you/my-tiny-model"></div><label class="radio-card"><input type="checkbox" id="upPrivate" checked><div><b>Private repository</b></div></label><label class="radio-card" style="margin-top:8px"><input type="checkbox" id="upMerge"><div><b>Merge LoRA into the base model first</b><span>Uploads full weights instead of an adapter.</span></div></label>`, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Upload</button>` });
    $("[data-cancel]", m.el).onclick = m.close;
    $("[data-ok]", m.el).onclick = guard(async () => { await post("/api/hub/upload", { project: state.project, kind: "experiment", name: b.dataset.upload, repo_id: $("#upRepo", m.el).value.trim(), private: $("#upPrivate", m.el).checked, merge: $("#upMerge", m.el).checked }); m.close(); showTasks(); pollTasks(); });
  });
  $("#hfSave", content).onclick = guard(async () => { const r = await post("/api/hub/token", { token: $("#hfToken", content).value }); toast(`logged in as ${r.user}`); pageModels(); });
  $$("[data-hubdel]", content).forEach((b) => b.onclick = () => confirmModal("Remove from cache", `Delete the local copy of ${b.dataset.hubdel}? It can be downloaded again.`, async () => { await del(`/api/hub/models/${b.dataset.hubdel}`); pageModels(); }, "Remove"));
  const search = guard(async () => {
    const q = $("#hubQ", content).value.trim(); const max = $("#hubMax", content).value;
    $("#hubResults", content).innerHTML = `<div class="muted"><span class="spinner"></span> searching the Hub…</div>`;
    const rows = await api(`/api/hub/search?q=${encodeURIComponent(q)}&limit=15${max ? `&max_params_b=${max}` : "&max_params_b="}&task=${encodeURIComponent($("#hubTask", content).value)}`);
    $("#hubResults", content).innerHTML = rows.length ? `<table><thead><tr><th>Repository</th><th class="num">Params</th><th>License</th><th>Downloads</th><th>Loadable</th><th></th></tr></thead><tbody>${rows.map((r) => `<tr ${dataContext("Result", r)}><td><a class="mono" href="${r.url}" target="_blank">${esc(r.id)}</a>${r.gated ? ' <span class="pill warn">gated</span>' : ""}</td><td class="num">${r.params_b ? r.params_b + "B" : "?"}</td><td>${esc(r.license || "?")}</td><td class="num">${(r.downloads || 0).toLocaleString()}</td><td>${r.transformers_loadable ? '<span class="pill good">transformers</span>' : `<span class="pill">${esc(r.format_tags.join(", ") || r.library || "?")}</span>`}</td><td style="text-align:right">${r.transformers_loadable ? `<button class="btn sm primary" data-dl="${esc(r.id)}">Download</button>` : ""}</td></tr>`).join("")}</tbody></table>` : `<div class="muted">No results.</div>`;
    $$("[data-dl]", content).forEach((b) => b.onclick = guard(async () => { await post("/api/hub/download", { repo_id: b.dataset.dl }); showTasks(); pollTasks(); toast(`downloading ${b.dataset.dl}`); }));
  });
  $("#hubSearch", content).onclick = search;
  $("#hubQ", content).onkeydown = (e) => { if (e.key === "Enter") search(); };
}

// ------------------------------------------------------------------ assistant
// ------------------------------------------------------------------ repositories (RAG)
async function pageRepositories() {
  const view = state.pageContext;
  await ensureOverview(true); assertRender(view);
  const repos = await api("/api/repositories");
  state.repositories = repos;
  assertRender(view);
  const content = layout("repositories", `<div class="page-head"><h1>Repositories</h1><span class="meta">document collections with embeddings; chat with them from the Playground or the API (<span class="mono">repositories</span> on /v1/chat/completions)</span><div class="actions"><button class="btn primary" id="newRepo">+ New repository</button></div></div>
    <div class="cards">${repos.map((r) => `<div ${dataContext("Repository", r)} class="card" data-repo="${esc(r.name)}"><h3>${esc(r.name)}</h3><div class="sub">${esc(r.description || "")}</div><div class="sub" style="margin-top:6px">${r.documents.length} document${r.documents.length === 1 ? "" : "s"} · ${r.chunk_count} chunks · <span class="mono">${esc(r.embedding_model)}</span></div>
      <div class="row" style="margin-top:10px"><button class="btn small" data-add="${esc(r.name)}">Add documents</button><button class="btn small" data-query="${esc(r.name)}">Query</button><button class="btn small danger" data-del="${esc(r.name)}">Delete</button></div>
      ${r.documents.length ? `<table style="margin-top:8px">${r.documents.map((d) => `<tr ${dataContext("Repository document", d)}><td>${esc(d.name)}</td><td class="muted">${esc(d.kind)}</td><td class="num">${d.chunks} chunks</td><td><button class="btn small" data-deldoc="${esc(r.name)}|${esc(d.name)}">✕</button></td></tr>`).join("")}</table>` : ""}
    </div>`).join("") || `<div class="empty"><b>No repositories yet</b>Create one, add PDFs, docs, pages, or videos, then chat with them.</div>`}</div>`, [{ label: "Repositories" }]);
  $("#newRepo", content).onclick = guard(async () => {
    let cached = [];
    try { cached = (await api("/api/hub/models")).models.map((m) => m.id); } catch (_) {}
    const m = modal({ title: "New repository", body: `<div class="field"><label>Name</label><input id="rpName" placeholder="manuals"></div><div class="field"><label>Description</label><input id="rpDesc"></div><div class="field"><label>Embedding model (any Hugging Face feature-extraction model; search the Models page with task "embeddings")</label><input id="rpModel" list="rpModels" value="sentence-transformers/all-MiniLM-L6-v2"><datalist id="rpModels">${cached.map((id) => `<option value="${esc(id)}">`).join("")}</datalist></div>`, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Create</button>` });
    $("[data-cancel]", m.el).onclick = m.close;
    $("[data-ok]", m.el).onclick = guard(async () => { const name = $("#rpName", m.el).value.trim(); if (!name) throw new Error("give the repository a name"); await post("/api/repositories", { name, description: $("#rpDesc", m.el).value, embedding_model: $("#rpModel", m.el).value.trim() || null }); m.close(); pageRepositories(); });
  });
  $$("[data-add]", content).forEach((b) => b.onclick = () => {
    const name = b.dataset.add;
    const m = modal({ title: `Add documents to ${name}`, body: `<div class="field"><label>Files (txt, md, html, pdf, docx, pptx)</label><input id="rpFiles" type="file" multiple></div><div class="field"><label>URLs (web pages or YouTube videos, one per line)</label><textarea id="rpUrls" placeholder="https://…"></textarea></div><p class="muted">Text is extracted, split into ~1,200-character chunks, embedded with the repository's model, and indexed as a background task.</p>`, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Index</button>` });
    $("[data-cancel]", m.el).onclick = m.close;
    $("[data-ok]", m.el).onclick = guard(async () => {
      const fd = new FormData();
      for (const f of $("#rpFiles", m.el).files) fd.append("files", f);
      fd.append("urls", $("#rpUrls", m.el).value);
      if (!$("#rpFiles", m.el).files.length && !$("#rpUrls", m.el).value.trim()) throw new Error("add at least one file or URL");
      await post(`/api/repositories/${encodeURIComponent(name)}/documents`, fd);
      m.close(); toast("indexing started"); showTasks(); pollTasks();
    });
  });
  $$("[data-query]", content).forEach((b) => b.onclick = () => {
    const name = b.dataset.query;
    const m = modal({ title: `Query ${name}`, wide: true, body: `<div class="row"><div class="field" style="flex:3"><label>Text</label><input id="rpQ" placeholder="how do I reset the thermostat?"></div><div class="field"><label>Passages</label><input id="rpLimit" type="number" min="1" max="50" value="5"></div><div class="field"><label>Min similarity</label><input id="rpThr" type="number" min="-1" max="1" step="0.05" value="0"></div></div><div id="rpResults" class="muted">results appear here</div>`, footer: `<button class="btn" data-close>Close</button><button class="btn primary" data-ok>Search</button>` });
    $("[data-close]", m.el).onclick = m.close;
    const run = guard(async () => { const rows = await post(`/api/repositories/${encodeURIComponent(name)}/query`, { text: $("#rpQ", m.el).value, limit: Number($("#rpLimit", m.el).value) || 5, similarity_threshold: Number($("#rpThr", m.el).value) || 0 }); $("#rpResults", m.el).innerHTML = rows.length ? `<table>${rows.map((r) => `<tr ${dataContext("Result", r)}><td class="num mono">${r.similarity_score.toFixed(3)}</td><td class="muted">${esc(r.document)}</td><td style="white-space:pre-wrap">${esc(r.text.slice(0, 600))}</td></tr>`).join("")}</table>` : `<div class="muted">no chunks above the threshold</div>`; });
    $("[data-ok]", m.el).onclick = run;
    $("#rpQ", m.el).onkeydown = (e) => { if (e.key === "Enter") run(); };
  });
  $$("[data-del]", content).forEach((b) => b.onclick = () => confirmModal("Delete repository", `Delete repository "${b.dataset.del}" and its index?`, async () => { await del(`/api/repositories/${encodeURIComponent(b.dataset.del)}`); pageRepositories(); }));
  $$("[data-deldoc]", content).forEach((b) => b.onclick = () => { const [repo, doc] = b.dataset.deldoc.split("|"); confirmModal("Remove document", `Remove "${doc}" from ${repo}?`, async () => { await del(`/api/repositories/${encodeURIComponent(repo)}/documents/${encodeURIComponent(doc)}`); pageRepositories(); }, "Remove"); });
}

// ------------------------------------------------------------------ settings (stored on the server, shared by everyone using this instance)
async function pageSettings() {
  const view = state.pageContext;
  await ensureOverview(true); assertRender(view);
  const [data, cfg] = await Promise.all([api("/api/settings"), api("/api/config")]);
  const groups = [
    ["anthropic", "Claude (assistant)", "Used by the ✦ assistant and the Assistant page. Any model the account lists becomes selectable.", "anthropic"],
    ["openai", "OpenAI (assistant)", "Same assistant, OpenAI models.", "openai"],
    ["helper", "Helper LLM", "Synthetic data, auto-labelling, rubric judging, neuron explanations and tests. Any OpenAI-compatible server: a local mlx_lm / llama-server / Ollama / vLLM, or a hosted API.", "helper"],
    ["rag", "Embeddings (repositories)", "The feature-extraction model new repositories embed with. Existing repositories keep theirs.", null],
    ["hub", "Hugging Face", "Token for gated models and uploads.", "hub"],
  ];
  const byGroup = (g) => data.settings.filter((s) => s.group === g);
  const field = (s) => `<div class="field"><label>${esc(s.label)} <span class="muted">(${s.source}${s.set ? "" : ", not set"})</span></label>${s.secret
    ? `<div class="row"><input type="password" data-key="${s.key}" placeholder="${s.set ? "•••••••• (saved; type to replace)" : "not set"}" autocomplete="new-password"><label class="chk"><input type="checkbox" data-clear="${s.key}"> clear</label></div>`
    : `<input data-key="${s.key}" value="${esc(s.value || "")}" placeholder="default">`}</div>`;
  const store = (what, title, detail) => `<div class="card"><h3>${title}</h3><div class="sub mono" style="word-break:break-all">${esc(detail)}</div><div class="row" style="margin-top:8px"><button class="btn small" data-test="${what}">Test connection</button><span class="muted" data-result="${what}"></span></div></div>`;
  assertRender(view);
  const content = layout("settings", `<div class="page-head"><div><h1>Settings</h1><div class="meta"><span>One set for this deployment, stored in PostgreSQL and applied to the API, the workers, the inference service, and the assistant. Admins only.${data.updated ? ` Last change ${fmtDate(data.updated)}.` : ""}</span></div></div><div class="actions"><button class="btn primary" id="stSave">Save</button></div></div>
    <div class="cards settings">${groups.map(([g, title, blurb, test]) => `<div class="card"><h3>${title}</h3><div class="sub">${blurb}</div><div style="margin-top:10px">${byGroup(g).map(field).join("")}</div>${test ? `<div class="row" style="margin-top:8px"><button class="btn small" data-test="${test}">Test connection</button><span class="muted" data-result="${test}"></span></div>` : ""}</div>`).join("")}
    ${store("database", "PostgreSQL", `${cfg.database_url} · ${cfg.database_version || "?"}`)}${store("redis", "Redis (queue)", cfg.redis_url)}${store("qdrant", "Qdrant (vectors)", cfg.qdrant_url)}${store("inference", "Inference", cfg.inference.mode === "remote" ? cfg.inference.url : "models load inside the API process")}
    <div class="card"><h3>Compute and data</h3><div class="sub">Device and dtype are per host (TINYSTUDIO_DEVICE / TINYSTUDIO_DTYPE on each worker and inference machine); this API host reports <span class="mono">${esc(cfg.device)} / ${esc(cfg.dtype)}</span>. Artifacts live in <span class="mono">${esc(cfg.data_dir)}</span>.</div></div></div>`, [{ label: "Settings" }]);
  $("#stSave", content).onclick = guard(async () => {
    const values = {};
    $$("[data-key]", content).forEach((el) => { const key = el.dataset.key; const spec = data.settings.find((s) => s.key === key); if (spec.secret) { if (el.value) values[key] = el.value; } else if ((el.value || "") !== (spec.value || "")) values[key] = el.value || null; });
    $$("[data-clear]", content).forEach((el) => { if (el.checked) values[el.dataset.clear] = null; });
    if (!Object.keys(values).length) return toast("nothing changed");
    await put("/api/settings", { values });
    ASSIST.models = null; state.overview = null;
    toast("settings saved"); pageSettings();
  });
  $$("[data-test]", content).forEach((b) => b.onclick = guard(async () => { const out = $(`[data-result="${b.dataset.test}"]`, content); out.textContent = "testing…"; const r = await post("/api/settings/test", { what: b.dataset.test }); out.innerHTML = `<span class="pill ${r.ok ? "good" : "bad"}">${r.ok ? "ok" : "failed"}</span> ${esc(r.detail)}`; }));
}

async function pageUsers() {
  const view = state.pageContext;
  await ensureOverview(true); assertRender(view);
  const users = await api("/api/users");
  assertRender(view);
  const content = layout("users", `<div class="page-head"><h1>Users</h1><span class="meta">${users.length} account${users.length === 1 ? "" : "s"}</span><div class="actions"><button class="btn primary" id="newUser">+ Add user</button></div></div>
    <div class="panel"><table><thead><tr><th>Email</th><th>Name</th><th>Role</th><th>Last login</th><th>Status</th><th></th></tr></thead><tbody>${users.map((u) => `<tr ${dataContext("User account", u)}><td><b>${esc(u.email)}</b></td><td>${esc(u.name)}</td><td><select data-role="${u.id}" ${u.id === state.user.id ? "disabled" : ""}><option ${u.role === "member" ? "selected" : ""}>member</option><option ${u.role === "admin" ? "selected" : ""}>admin</option></select></td><td>${fmtDate(u.last_login)}</td><td><span class="pill ${u.disabled ? "bad" : "good"}">${u.disabled ? "disabled" : "active"}</span></td><td style="text-align:right;white-space:nowrap"><button class="btn sm" data-pw="${u.id}">Set password</button> ${u.id !== state.user.id ? `<button class="btn sm" data-toggle-user="${u.id}" data-disabled="${u.disabled}">${u.disabled ? "Enable" : "Disable"}</button> <button class="btn sm danger" data-del-user="${u.id}">Delete</button>` : ""}</td></tr>`).join("")}</tbody></table></div>`, [{ label: "Users" }]);
  $("#newUser", content).onclick = () => {
    const m = modal({ title: "Add user", body: `<div class="field"><label>Email</label><input id="uEmail" type="email"></div><div class="field"><label>Name</label><input id="uName"></div><div class="field"><label>Password (8+ characters)</label><input id="uPass" type="password" autocomplete="new-password"></div><label class="chk"><input type="checkbox" id="uAdmin"> admin</label>`, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Create</button>` });
    $("[data-cancel]", m.el).onclick = m.close;
    $("[data-ok]", m.el).onclick = guard(async () => { await post("/api/users", { email: $("#uEmail", m.el).value, name: $("#uName", m.el).value, password: $("#uPass", m.el).value, role: $("#uAdmin", m.el).checked ? "admin" : "member" }); m.close(); pageUsers(); });
  };
  $$("[data-role]", content).forEach((s) => s.onchange = guard(async () => { await patch(`/api/users/${s.dataset.role}`, { role: s.value }); toast("role updated"); }));
  $$("[data-toggle-user]", content).forEach((b) => b.onclick = guard(async () => { await patch(`/api/users/${b.dataset.toggleUser}`, { disabled: b.dataset.disabled !== "true" }); pageUsers(); }));
  $$("[data-del-user]", content).forEach((b) => b.onclick = () => confirmModal("Delete user", "Delete this account and its API keys?", async () => { await del(`/api/users/${b.dataset.delUser}`); pageUsers(); }));
  $$("[data-pw]", content).forEach((b) => b.onclick = () => {
    const m = modal({ title: "Set password", body: `<div class="field"><label>New password</label><input id="pw" type="password" autocomplete="new-password"></div>`, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Save</button>` });
    $("[data-cancel]", m.el).onclick = m.close;
    $("[data-ok]", m.el).onclick = guard(async () => { await post(`/api/users/${b.dataset.pw}/password`, { password: $("#pw", m.el).value }); m.close(); toast("password set"); });
  });
}

async function pageProjects() {
  const view = state.pageContext;
  await ensureOverview(true); assertRender(view);
  const projects = await api("/api/projects");
  assertRender(view);
  const content = layout("projects", `<div class="page-head"><h1>Projects</h1><div class="actions"><button class="btn primary" id="newProject">+ New project</button></div></div>
    <div class="cards">${projects.map((pr) => `<div ${dataContext("Project", pr)} class="card ${pr.slug === state.project ? "" : "clickable"}" data-proj="${esc(pr.slug)}"><h3>${esc(pr.name)} ${pr.slug === state.project ? '<span class="pill info">current</span>' : ""}</h3><div class="sub">${esc(pr.goal || "")}</div><div class="foot"><span>${pr.datasets} datasets · ${pr.jobs} jobs · ${fmtDate(pr.created)}</span>${state.user.role === "admin" && projects.length > 1 ? `<button class="btn sm danger" data-del-proj="${esc(pr.slug)}">Delete</button>` : ""}</div></div>`).join("")}</div>`, [{ label: "Projects" }]);
  $("#newProject", content).onclick = newProjectModal;
  $$("[data-proj]", content).forEach((c) => c.onclick = guard(async (e) => { if (e.target.closest("button")) return; await selectProject(c.dataset.proj); state.overview = null; go("#/datasets"); render(); }));
  $$("[data-del-proj]", content).forEach((b) => b.onclick = (e) => { e.stopPropagation(); confirmModal("Delete project", `Delete project ${b.dataset.delProj} with all its datasets, jobs, weights, evaluations, traces, and repositories?`, async () => { await del(`/api/p/${encodeURIComponent(b.dataset.delProj)}`); if (state.project === b.dataset.delProj) state.project = null; await ensureProject(); pageProjects(); }); });
}

function newProjectModal() {
  const m = modal({ title: "New project", body: `<div class="field"><label>Name</label><input id="prName" placeholder="support-bot"></div><div class="field"><label>Goal (what the model should become good at)</label><textarea id="prGoal"></textarea></div>`, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Create</button>` });
  $("[data-cancel]", m.el).onclick = m.close;
  $("[data-ok]", m.el).onclick = guard(async () => { const pr = await post("/api/projects", { name: $("#prName", m.el).value.trim(), goal: $("#prGoal", m.el).value.trim() }); m.close(); await selectProject(pr.slug); state.overview = null; go("#/datasets"); render(); });
}

// ------------------------------------------------------------------ account and project selection
const PROJECT_KEY = "tinystudio.project";
async function selectProject(slug) {
  state.projects = await api("/api/projects");
  if (!state.projects.some((pr) => pr.slug === slug)) slug = state.projects[0]?.slug || null;
  state.project = slug;
  syncAssistantScope();
  try { if (slug) localStorage.setItem(PROJECT_KEY, slug); } catch (_) {}
  return slug;
}
async function ensureProject() {
  let wanted = state.project; try { wanted = wanted || localStorage.getItem(PROJECT_KEY); } catch (_) {}
  await selectProject(wanted);
  if (!state.project) {
    await new Promise((resolve) => {
      const m = modal({ title: "Create your first project", body: `<p class="muted">A project holds datasets, snapshots, jobs, evaluations, traces, and repositories. You can add more later from the project menu.</p><div class="field"><label>Name</label><input id="prName" placeholder="support-bot"></div><div class="field"><label>Goal</label><textarea id="prGoal" placeholder="Answer questions about our product from the manual"></textarea></div>`, footer: `<button class="btn primary" data-ok>Create project</button>` });
      $("[data-ok]", m.el).onclick = guard(async () => { const pr = await post("/api/projects", { name: $("#prName", m.el).value.trim(), goal: $("#prGoal", m.el).value.trim() }); m.close(); await selectProject(pr.slug); resolve(); });
    });
  }
  return state.project;
}
function authPage(title, body) {
  $("#app").innerHTML = `<div class="auth"><div class="auth-card"><div class="brand" style="padding:0 0 10px"><div class="logo"><svg width="16" height="16" viewBox="0 0 32 32"><path d="M8 22 L13 12 L18 18 L24 9" fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg></div><div>tinystudio<small>${esc(title)}</small></div></div>${body}</div></div>`;
  $("#tasks").hidden = true;
  return $("#app .auth-card");
}
function showLogin() {
  if ($("#loginForm")) return;
  const card = authPage("Sign in", `<form id="loginForm"><div class="field"><label>Email</label><input id="lEmail" type="email" autocomplete="username" required></div><div class="field"><label>Password</label><input id="lPass" type="password" autocomplete="current-password" required></div><button class="btn primary" style="width:100%;justify-content:center" type="submit">Sign in</button><div class="muted" id="lErr" style="margin-top:8px"></div></form>`);
  $("#loginForm", card).onsubmit = async (e) => {
    e.preventDefault();
    try { await post("/api/auth/login", { email: $("#lEmail", card).value, password: $("#lPass", card).value }); location.reload(); }
    catch (err) { $("#lErr", card).textContent = err.message; }
  };
}
function showSetup() {
  const card = authPage("First start: create the admin account", `<form id="setupForm"><div class="field"><label>Name</label><input id="sName"></div><div class="field"><label>Email</label><input id="sEmail" type="email" autocomplete="username" required></div><div class="field"><label>Password (8+ characters)</label><input id="sPass" type="password" autocomplete="new-password" required></div><button class="btn primary" style="width:100%;justify-content:center" type="submit">Create admin and continue</button><div class="muted" id="sErr" style="margin-top:8px"></div></form>`);
  $("#setupForm", card).onsubmit = async (e) => {
    e.preventDefault();
    try { await post("/api/auth/setup", { name: $("#sName", card).value, email: $("#sEmail", card).value, password: $("#sPass", card).value }); location.reload(); }
    catch (err) { $("#sErr", card).textContent = err.message; }
  };
}

// ------------------------------------------------------------------ assistant (available on every page)
const ASSIST = { open: false, models: null, attach: true, destructive: false, scope: null, selection: null, picking: false, session: null };
const assistantSessions = new Map();
let assistantSync = null;
const assistKey = () => `tinystudio.assistant.v2.${encodeURIComponent(state.user?.id || state.user?.email || "anonymous")}.${encodeURIComponent(state.project || "none")}`;
// Each scope owns its asynchronous work. Late responses can never change the active project.
for (const name of ["history", "provider", "model", "busy", "usage", "turn"]) Object.defineProperty(ASSIST, name, {
  get: () => ASSIST.session?.[name], set: (value) => { if (ASSIST.session) ASSIST.session[name] = value; },
});
function assistPreferences() {
  if (!ASSIST.scope) return;
  try { localStorage.setItem(`${ASSIST.scope}.preferences`, JSON.stringify({ attach: ASSIST.attach, open: ASSIST.open })); } catch (_) { /* preferences are optional */ }
}
function assistRefreshUI() {
  for (const el of $$("#asPersistence")) el.textContent = assistStatus();
  const blocked = !ASSIST.session?.loaded || ASSIST.busy || ASSIST.session?.dirty || ASSIST.session?.error;
  for (const el of $$("#asSend, #asReset, #asProvider, #asModel")) el.disabled = Boolean(blocked);
  for (const el of $$("#asRetry")) el.hidden = !ASSIST.session?.error || ASSIST.session?.conflict;
  for (const el of $$("#asReload")) {
    el.disabled = Boolean(ASSIST.busy || ASSIST.session?.saving);
    el.hidden = !(ASSIST.session?.error || ASSIST.session?.conflict || ASSIST.session?.dirty && !ASSIST.busy);
    el.textContent = ASSIST.session?.ownerMismatch ? "Reload to update account" : "Load saved version";
  }
  for (const el of $$("#asSyncStatus")) el.textContent = assistSyncStatus();
}
function assistSyncStatus() {
  if (ASSIST.session?.ownerMismatch) return "Sync paused · account changed";
  const sync = ASSIST.session?.syncState;
  if (sync === "offline") return "Offline · sync resumes when connected";
  if (sync === "error") return "Sync unavailable · retrying automatically";
  return "Syncing automatically";
}
function assistStatus() {
  const session = ASSIST.session;
  if (!session || !session.loaded && !session.error) return "Loading conversation…";
  if (session.error) return `${session.conflict ? "Conflict" : "Not saved"}: ${session.error}`;
  if (session.saving) return "Saving…";
  if (session.dirty) return "Unsaved conversation";
  return "Saved to database";
}
async function assistResponseError(session, response) {
  let detail = response.statusText;
  try { detail = (await response.json()).detail || detail; } catch (_) {}
  const error = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  if (response.headers?.get("x-conversation-owner-mismatch") === "true") {
    error.ownerMismatch = true; session.ownerMismatch = true; session.error = error.message; session.conflict = true;
    if (session === ASSIST.session) { assistantSync?.stop(); assistRefreshUI(); }
  }
  error.status = response.status;
  return error;
}
async function assistRequest(session, options = {}, { conditional = false } = {}) {
  if (session.ownerMismatch) throw Object.assign(new Error("The signed-in account changed. Reload the page before continuing."), { ownerMismatch: true, status: 403 });
  if (session.user !== (state.user?.id || state.user?.email)) throw new Error("Sign in as the conversation owner before retrying");
  const headers = { ...options.headers, "X-Conversation-Owner": String(session.user) };
  if (conditional && session.etag) headers["If-None-Match"] = session.etag;
  const response = await rawFetch(`/api/p/${encodeURIComponent(session.project)}/assistant/conversation`, { ...options, headers, cache: "no-store" });
  if (conditional && response.status === 304) return null;
  if (!response.ok) throw await assistResponseError(session, response);
  return { ...await response.json(), etag: response.headers?.get("etag") || null };
}
function assistEnqueue(session, work) {
  const pending = session.queue.catch(() => {}).then(work);
  session.queue = pending;
  return pending;
}
function assistSnapshot(session) {
  // Preserve the full conversation; request-size compaction is separate from storage.
  return JSON.parse(JSON.stringify({ revision: session.revision, history: session.history, provider: session.provider || "", model: session.model || "" }));
}
function assistSameSnapshot(session, snapshot) {
  const canonical = (value) => Array.isArray(value) ? value.map(canonical) : value && typeof value === "object" ? Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])])) : value;
  const content = (value) => ({ history: value.history, provider: value.provider || "", model: value.model || "" });
  return JSON.stringify(canonical(content(assistSnapshot(session)))) === JSON.stringify(canonical(content(snapshot)));
}
function assistAdopt(session, snapshot) {
  session.revision = snapshot.revision; session.history = snapshot.history;
  session.etag = snapshot.etag || null;
  session.provider = snapshot.provider || session.provider; session.model = snapshot.model || session.model;
  session.loaded = true; session.dirty = false; session.error = ""; session.conflict = false; session.pending = false; session.streaming = false;
}
function assistForgetLegacy(session) {
  try { localStorage.removeItem(session.key); } catch (_) { /* revision still prevents reimport */ }
}
function assistLoad(session = ASSIST.session, { refresh = false, discard = false, signal, automatic = false } = {}) {
  if (!session) return Promise.resolve();
  return assistEnqueue(session, async () => {
    if (session.busy || session.dirty && !discard && !session.pending || session.loaded && !refresh) return;
    try {
      const snapshot = await assistRequest(session, { signal });
      if (signal?.aborted) return;
      if (session.pending && snapshot.revision <= session.revision && !discard) return;
      if (snapshot.revision === 0 && !session.loaded && session.legacy?.history?.length) {
        session.revision = 0; session.history = session.legacy.history;
        session.provider = session.legacy.provider || session.provider; session.model = session.legacy.model || session.model;
        session.loaded = true; session.dirty = true;
        try {
          const imported = await assistRequest(session, { method: "PUT", body: JSON.stringify(assistSnapshot(session)), signal });
          if (signal?.aborted) return;
          assistAdopt(session, imported);
          if (ASSIST.session === session) assistantSync?.notifySaved(imported.revision);
        } catch (error) {
          if (error.status !== 409) throw error;
          const winner = await assistRequest(session, { signal });
          if (signal?.aborted) return;
          assistAdopt(session, winner);
        }
      } else assistAdopt(session, snapshot);
      session.legacy = null; assistForgetLegacy(session);
    } catch (error) {
      if (!signal?.aborted) { session.error = error.message; session.conflict = error.ownerMismatch === true || error.status === 409; }
      throw error;
    }
    finally { if (ASSIST.session === session && !signal?.aborted) { assistRefreshUI(); if (automatic) assistRenderRemote(); else assistRedraw(); } }
  });
}
function assistSave(session = ASSIST.session) {
  if (!session) return Promise.resolve();
  if (session.ownerMismatch) return Promise.reject(new Error("The signed-in account changed. Reload the page before saving."));
  session.dirty = true; session.edits = (session.edits || 0) + 1;
  assistPreferences();
  return assistEnqueue(session, async () => {
    if (!session.loaded) throw new Error("Conversation must load before saving");
    if (session.conflict) throw new Error("Reload the newer database conversation before continuing");
    session.saving = true; session.error = ""; assistRefreshUI();
    try {
      const edits = session.edits;
      const saved = await assistRequest(session, { method: "PUT", body: JSON.stringify(assistSnapshot(session)) });
      session.revision = saved.revision; session.etag = saved.etag; session.dirty = session.edits !== edits; session.conflict = false;
      if (ASSIST.session === session) assistantSync?.notifySaved(saved.revision);
    } catch (error) { session.error = error.message; session.conflict = error.ownerMismatch === true || error.status === 409; throw error; }
    finally { session.saving = false; assistRefreshUI(); }
  });
}
// Sync reads share the write queue, but never write or discard local work.
function assistCheckRemote(session, { signal } = {}) {
  const active = () => !signal?.aborted && session === ASSIST.session && session.key === assistKey();
  if (!active() || session.ownerMismatch) return Promise.resolve();
  if (!session.loaded) return assistLoad(session, { refresh: true, signal, automatic: true }).then(() => session.revision);
  return assistEnqueue(session, async () => {
    if (!active() || session.busy || session.saving) return session.revision;
    const edits = session.edits, revision = session.revision;
    const snapshot = await assistRequest(session, { signal }, { conditional: true });
    // The user can start editing while the GET is in flight.
    if (!active() || session.busy || session.saving || edits !== session.edits || revision !== session.revision || !snapshot) return session.revision;
    if (snapshot.revision < session.revision) return session.revision;
    if (session.dirty || session.conflict) {
      if (!session.conflict && assistSameSnapshot(session, snapshot)) {
        assistAdopt(session, snapshot); assistRefreshUI();
      } else if (snapshot.revision > session.revision) {
        session.conflict = true;
        session.error = "A newer conversation arrived. Your unsaved messages are retained; load the saved version when you are ready to discard them.";
        assistRefreshUI();
      }
      return session.revision;
    }
    session.etag = snapshot.etag;
    if (snapshot.revision > session.revision) {
      assistAdopt(session, snapshot); session.usage = "";
      assistRenderRemote();
    }
    return session.revision;
  });
}
function startAssistantSync(session = ASSIST.session) {
  assistantSync?.stop(); assistantSync = null;
  if (!session?.project || !state.user || session !== ASSIST.session || session.ownerMismatch) return;
  assistantSync = new ConversationSync({
    scope: session.key,
    check: (options) => assistCheckRemote(session, options),
    onStatus: ({ state: syncState }) => {
      if (session !== ASSIST.session) return;
      session.syncState = syncState; assistRefreshUI();
    },
  });
  assistantSync.start();
}
window.addEventListener("pagehide", () => assistantSync?.stop());
window.addEventListener("pageshow", () => startAssistantSync());
function assistRedraw() {
  const drawer = $("#assist .drawer"); if (drawer && ASSIST.open) renderAssistant(drawer);
  const page = $("#asPage"); if (page) renderAssistant(page, { full: true });
}
function syncAssistantScope() {
  const key = assistKey(); if (key === ASSIST.scope) return;
  assistantSync?.stop(); assistantSync = null;
  const previous = ASSIST.session;
  if (previous) {
    previous.turn?.controller.abort(); previous.turn = null; previous.busy = false;
    if (previous.loaded && previous.dirty && !previous.streaming) assistSave(previous).catch(() => {});
    if (previous.streaming) { previous.pending = true; previous.error ||= "Waiting for the server to save the interrupted reply."; }
  }
  ASSIST.scope = key; ASSIST.destructive = false; ASSIST.selection = null;
  let session = assistantSessions.get(key);
  if (!session) {
    let legacy = null; try { legacy = JSON.parse(localStorage.getItem(key) || "null"); } catch (_) {}
    session = { key, user: state.user?.id || state.user?.email, project: state.project, provider: "anthropic", model: null, history: [], revision: 0, loaded: false, dirty: false, saving: false, error: "", conflict: false, busy: false, turn: null, usage: "", queue: Promise.resolve(), legacy };
    assistantSessions.set(key, session);
  }
  ASSIST.session = session;
  try { const prefs = JSON.parse(localStorage.getItem(`${key}.preferences`) || "{}"); ASSIST.attach = prefs.attach ?? session.legacy?.attach ?? true; ASSIST.open = prefs.open ?? session.legacy?.open ?? false; } catch (_) { ASSIST.attach = true; ASSIST.open = false; }
  state.pageContext = null;
  const drawer = $("#assist .drawer"); if (drawer) { drawer.hidden = !ASSIST.open; $("#assist .fab")?.classList.toggle("on", ASSIST.open); renderAssistant(drawer); }
  if (session.project && state.user) startAssistantSync(session);
}
function safeContextData(data) {
  if (Array.isArray(data)) return data.map(safeContextData);
  if (data && typeof data === "object") return Object.fromEntries(Object.entries(data).map(([key, value]) => [key, /(?:password|secret|token|authorization|credential|api.?key)/i.test(key) ? "[redacted]" : safeContextData(value)]));
  return data;
}
// Capture only GET responses requested by this route and project render. Never retain credentials.
function recordApi(path, data, capture = state.pageContext) {
  if (!capture || capture !== state.pageContext || capture.project !== state.project || capture.route !== (location.hash || "#/")) return;
  if (!path.startsWith("/api/") || /^\/api\/(?:tasks|assistant|settings|auth|keys|users)(?:[/?]|$)/.test(path)) return;
  const record = { path, data: safeContextData(data) };
  capture.api = capture.api.filter((r) => r.path !== path).concat([record]);
}
function isCurrentRender(view) { return !!view && view === state.pageContext && view.project === state.project && view.route === (location.hash || "#/"); }
function pageContextReady() { return isCurrentRender(state.domContext); }
function assertRender(view) {
  if (!view || isCurrentRender(view)) return;
  const error = new Error("Page changed while loading; stale response discarded"); error.name = "StaleRenderError"; throw error;
}
const contextBindings = new WeakMap();
let contextSequence = 0;
function contextRecord(label, data) { return { label, source: "rendered-data", data: safeContextData(data), route: location.hash || "#/", project: state.project, user: state.user?.id, origin: state.pageContext }; }
function bindContext(el, label, data) { if (el) contextBindings.set(el, contextRecord(label, data)); }
// HTML renderers retain the exact object used to draw this row/chart, without serializing it into DOM attributes.
function dataContext(label, data) {
  const view = state.pageContext; if (!view) return "";
  const id = `ctx_${++contextSequence}`;
  (view.bindings ||= new Map()).set(id, contextRecord(label, data));
  return `data-context-id="${id}"`;
}
function bindingFor(el) { return contextBindings.get(el) || state.pageContext?.bindings?.get(el.dataset?.contextId); }
function validBinding(binding) { return binding && binding.origin === state.pageContext && binding.project === state.project && binding.user === state.user?.id; }
function publicBinding(binding) { const { origin, user, ...data } = binding; return { ...data, source_route: data.route, route: location.hash || "#/" }; }
function sensitiveElement(el) {
  if (el?.closest?.('[data-assistant-sensitive], input[type="password"]')) return true;
  const field = el?.closest?.("input, textarea");
  return !!field && /password|token|secret|credential|api.?key/i.test(`${field.id} ${field.name} ${labelFor(field)}`);
}
function safeSelection() {
  if (!pageContextReady()) return "";
  const selected = window.getSelection?.();
  if (!selected || location.hash === "#/api-keys" || sensitiveElement(selected.anchorNode?.parentElement) || sensitiveElement(selected.focusNode?.parentElement)) return "";
  for (let i = 0; i < (selected.rangeCount || 0); i++) {
    const range = selected.getRangeAt(i);
    if ($$('[data-assistant-sensitive]').some((el) => range.intersectsNode(el))) return "";
  }
  return String(selected).trim().slice(0, 2000);
}
function pickContext(target) {
  if (!pageContextReady()) return null;
  if (sensitiveElement(target) || location.hash === "#/api-keys") return null;
  const selected = target.closest?.("tr, table, svg, .panel, .card, .tile, [data-context-id]") || target;
  // Rows and charts never inherit an enclosing panel's broader records.
  const exactOnly = selected.matches?.("tr, svg");
  for (let el = target; el && el !== document.body; el = el.parentElement) {
    const binding = bindingFor(el);
    if (binding) return validBinding(binding) ? publicBinding(binding) : null;
    if (exactOnly && el === selected) break;
  }
  if (!exactOnly) {
    const children = [...(selected.querySelectorAll?.('[data-context-id], tr, svg') || [])].map(bindingFor).filter(validBinding);
    if (children.length) return { label: labelFor(selected), source: "rendered-data", route: location.hash, project: state.project, data: children.map(publicBinding) };
  }
  return { label: labelFor(selected).slice(0, 120), source: "unavailable", note: "No structured source is bound to this element. No page-wide records were attached.", route: location.hash, project: state.project, data: null };
}
function setAssistantPicking(on) {
  ASSIST.picking = on; document.body.classList.toggle("assistant-picking", on);
  const button = $("#asPick"); if (button) button.textContent = on ? "Cancel pick" : "Pick data";
}
document.addEventListener("click", (event) => {
  if (!ASSIST.picking || event.target.closest("#assist, #asPage")) return;
  const selected = pickContext(event.target); if (!selected) return;
  event.preventDefault(); event.stopImmediatePropagation(); ASSIST.selection = selected; setAssistantPicking(false);
  const drawer = $("#assist .drawer"); if (drawer && ASSIST.open) renderAssistant(drawer);
  const page = $("#asPage"); if (page) renderAssistant(page, { full: true });
}, true);

const uiRefs = new Map();
function visible(el) { if (!(el instanceof Element)) return false; if (el.closest("#assist, [data-assistant-sensitive], script, style, template")) return false; const cs = getComputedStyle(el); if (cs.display === "none" || cs.visibility === "hidden") return false; return el.getClientRects().length > 0; }
function labelFor(el) {
  const own = (el.getAttribute("aria-label") || el.getAttribute("title") || el.getAttribute("placeholder") || "").trim();
  if (own) return own;
  if (el.id) { const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (lab) return lab.innerText.trim(); }
  const field = el.closest(".field"); if (field && field.querySelector("label")) return field.querySelector("label").innerText.trim();
  const text = (el.innerText || el.value || "").trim().replace(/\s+/g, " ");
  return text.slice(0, 90) || el.name || el.id || el.tagName.toLowerCase();
}
function snapshotPage({ interactive_only = false, max_chars = 12000 } = {}) {
  uiRefs.clear();
  if (location.hash === "#/api-keys" || !state.user) return "Credential/account page omitted from assistant context.";
  if (!pageContextReady()) return "Page is loading; previous page content omitted.";
  const lines = [`route: ${location.hash || "#/"}`, `page: ${(document.querySelector("#app h1") || {}).innerText?.trim() || document.title}`];
  const roots = [...document.querySelectorAll(".modal-back .modal")];
  if (roots.length) lines.push("(a dialog is open; its elements come first)");
  const content = document.querySelector("#app .content"); if (content) roots.push(content);
  const seenText = new Set();
  const isInteractive = (el) => el.matches("button, a[href], input, select, textarea, [role=button], tr.clickable, .card.clickable, summary, [data-k]");
  const walk = (el, depth) => {
    if (!visible(el) || (el.matches("input, textarea") && (el.type === "password" || /password|token|secret|credential|api.?key/i.test(`${el.id} ${el.name} ${labelFor(el)}`))) || el.closest("#asPage")) return;
    if (isInteractive(el)) {
      const ref = `ref_${uiRefs.size + 1}`; uiRefs.set(ref, el); el.dataset.aref = ref;
      const tag = el.tagName.toLowerCase();
      let kind = tag === "a" ? "link" : tag === "tr" ? "row" : tag === "input" ? `input:${el.type || "text"}` : tag;
      let stateText = "";
      if (tag === "input" && (el.type === "checkbox" || el.type === "radio")) stateText = el.checked ? " [checked]" : " [unchecked]";
      else if (tag === "input" || tag === "textarea") stateText = el.value ? ` = ${JSON.stringify(String(el.value).slice(0, 80))}` : "";
      else if (tag === "select") stateText = ` = ${JSON.stringify(el.options[el.selectedIndex]?.text || "")} (options: ${[...el.options].slice(0, 12).map((o) => o.text).join(" / ")}${el.options.length > 12 ? " / …" : ""})`;
      else if (tag === "a") stateText = el.getAttribute("href") ? ` -> ${el.getAttribute("href")}` : "";
      if (el.disabled) stateText += " [disabled]";
      if (el.classList.contains("on") || el.classList.contains("active") || el.classList.contains("sel")) stateText += " [selected]";
      lines.push(`${"  ".repeat(Math.min(depth, 4))}${ref} | ${kind} | ${labelFor(el)}${stateText}`);
      if (tag !== "tr") return;
    }
    if (!interactive_only) {
      if (/^H[1-4]$/.test(el.tagName)) { lines.push(`${"#".repeat(Number(el.tagName[1]))} ${el.innerText.trim().replace(/\s+/g, " ").slice(0, 160)}`); return; }
      if (el.tagName === "TABLE") {
        const rows = [...el.querySelectorAll("tr")].slice(0, 40);
        for (const r of rows) { if (r.matches("tr.clickable")) walk(r, depth + 1); else { const t = [...r.children].map((c) => c.innerText.trim().replace(/\s+/g, " ").slice(0, 60)).join(" | "); if (t.trim()) lines.push(`${"  ".repeat(Math.min(depth, 4))}| ${t}`); } }
        if (el.querySelectorAll("tr").length > 40) lines.push(`  … ${el.querySelectorAll("tr").length - 40} more rows`);
        return;
      }
      if (el.children.length === 0 || el.matches("p, li, .muted, .meta, .k, .v, .sub, .note, .pill, .tile")) {
        const t = el.innerText.trim().replace(/\s+/g, " ");
        if (t && t.length > 1 && !seenText.has(t)) { seenText.add(t); lines.push(`${"  ".repeat(Math.min(depth, 4))}${t.slice(0, 300)}`); }
        if (el.matches(".tile, .pill, .note, .meta")) return;
      }
    }
    for (const child of el.children) walk(child, depth + 1);
  };
  roots.forEach((r) => walk(r, 0));
  let out = lines.join("\n");
  if (out.length > max_chars) out = out.slice(0, max_chars) + `\n[output truncated at ${max_chars} of ${out.length} chars; pass a larger max_chars or use interactive_only]`;
  return out;
}
function pageText(max = 20000) {
  if (location.hash === "#/api-keys" || !state.user) return "Credential/account page omitted from assistant context.";
  if (!pageContextReady()) return "Page is loading; previous page content omitted.";
  const parts = [...document.querySelectorAll(".modal-back:not([data-assistant-sensitive]) .modal")].map((m) => "DIALOG:\n" + m.innerText);
  const content = document.querySelector("#app .content"); if (content) { const copy = content.cloneNode(true); copy.querySelectorAll("#asPage, input[type=password], [data-assistant-sensitive]").forEach((el) => el.remove()); parts.push(copy.innerText || copy.textContent); }
  const text = parts.join("\n\n").replace(/\n{3,}/g, "\n\n");
  return text.length > max ? text.slice(0, max) + `\n[truncated at ${max} of ${text.length} chars]` : text;
}
function captureContext() {
  const ready = pageContextReady();
  return { route: location.hash || "#/", title: ready ? `${document.title} · ${(document.querySelector("#app h1") || {}).innerText?.trim() || ""}`.trim() : "Page loading", text: pageText(8000), project: state.project, api: ready ? state.pageContext.api : [], selection: ready ? safeSelection() : "", selected_data: ready && ASSIST.selection?.project === state.project && ASSIST.selection?.route === (location.hash || "#/") ? ASSIST.selection : null };
}

const settle = (ms = 700) => new Promise((r) => setTimeout(r, ms));
async function uiPerform(action, args = {}, { allowActions = false } = {}) {
  if (["click", "fill", "select"].includes(action) && !allowActions) throw new Error("Interface changes require the actions / deletions toggle for this turn.");
  const need = (ref) => { if (!pageContextReady()) throw new Error("Page is loading; read the page after it finishes"); if (location.hash === "#/settings") throw new Error("Settings must be changed directly by the user"); const el = uiRefs.get(ref); if (sensitiveElement(el)) throw new Error("Credential elements cannot be operated by the assistant"); if (!el || !el.isConnected) throw new Error(`element ${ref} not found; call ui_read_page again, the page may have re-rendered`); return el; };
  switch (action) {
    case "read_page": return snapshotPage(args);
    case "get_text": return pageText(args.max_chars || 20000);
    case "navigate": { let h = String(args.hash || ""); if (!h.startsWith("#")) h = "#" + (h.startsWith("/") ? h : "/" + h); if (location.hash === h) render(); else location.hash = h; await settle(1000); return snapshotPage({ interactive_only: false, max_chars: 8000 }); }
    case "click": { const el = need(args.ref); el.click(); await settle(800); return { clicked: args.ref, page: snapshotPage({ interactive_only: false, max_chars: 6000 }) }; }
    case "fill": { const el = need(args.ref); if (el.type === "password" || location.hash === "#/settings") throw new Error("Credential fields cannot be operated by the assistant"); if (!el.matches("input, textarea")) throw new Error(`${args.ref} is not an input`); el.focus(); el.value = String(args.value ?? ""); el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change", { bubbles: true })); return { filled: args.ref, value: el.value }; }
    case "select": { const el = need(args.ref); if (!el.matches("select")) throw new Error(`${args.ref} is not a select`); const want = String(args.value ?? ""); const opt = [...el.options].find((o) => o.value === want) || [...el.options].find((o) => o.text.trim().toLowerCase() === want.toLowerCase()) || [...el.options].find((o) => o.text.toLowerCase().includes(want.toLowerCase())); if (!opt) throw new Error(`no option matching ${JSON.stringify(want)}; options: ${[...el.options].map((o) => o.text).join(" / ")}`); el.value = opt.value; el.dispatchEvent(new Event("change", { bubbles: true })); await settle(500); return { selected: args.ref, value: opt.value, label: opt.text }; }
    default: throw new Error(`unknown interface action ${action}`);
  }
}

// Keep the complete JSON request comfortably below the server's 256,000-byte limit.
const requestBytes = (data) => new TextEncoder().encode(JSON.stringify(data)).length;
function boundedData(data, limit) {
  if (requestBytes(data) <= limit) return data;
  let strings = 4000, items = 40;
  const compact = (value, depth = 0) => {
    if (typeof value === "string") return value.length > strings ? value.slice(0, strings) + ` [omitted ${value.length - strings} characters]` : value;
    if (depth > 10) return { omission: "Nested data omitted beyond depth 10" };
    if (Array.isArray(value)) return value.slice(0, items).map((v) => compact(v, depth + 1)).concat(value.length > items ? [{ omission: `${value.length - items} additional items omitted` }] : []);
    if (value && typeof value === "object") {
      const pairs = Object.entries(value); const retained = Object.fromEntries(pairs.slice(0, items).map(([key, v]) => [key, compact(v, depth + 1)]));
      if (pairs.length > items) retained._omission = `${pairs.length - items} additional fields omitted`;
      return retained;
    }
    return value;
  };
  for (;;) {
    const result = compact(data);
    if (requestBytes(result) <= limit) return result;
    strings = Math.max(16, Math.floor(strings / 2)); items = Math.max(1, Math.floor(items / 2));
    if (strings === 16 && items === 1) return { omission: "Source exceeds this turn's context budget; inspect a narrower row or chart." };
  }
}
function prepareChatBody(body, project) {
  const context = body.context ? { ...body.context, api: [], omissions: [] } : null;
  if (context) {
    if (body.context.selected_data) {
      context.selected_data = boundedData(body.context.selected_data, 110000);
      if (requestBytes(context.selected_data) < requestBytes(body.context.selected_data)) context.omissions.push("Selected source was compacted; omission markers identify removed content.");
    }
    for (const record of body.context.api || []) {
      const retained = { path: record.path, data: boundedData(record.data, 12000) };
      if (requestBytes(context.api) + requestBytes(retained) > 60000) { context.omissions.push(`API source omitted for size: ${record.path}`); continue; }
      context.api.push(retained);
      if (requestBytes(retained.data) < requestBytes(record.data)) context.omissions.push(`API source compacted for size: ${record.path}`);
    }
  }
  const outgoing = { ...body, project, context, messages: body.messages.map((message) => ({ ...message })) };
  if (requestBytes(outgoing.messages.at(-1) || {}) > 60000) throw new Error("Message exceeds 60 KB; send a shorter request or pick a narrower data element.");
  let omitted = 0;
  while (requestBytes(outgoing) > 235000 && outgoing.messages.length > 1) { outgoing.messages.shift(); omitted++; }
  if (omitted) {
    outgoing.context ||= { route: location.hash, project, omissions: [] };
    outgoing.context.omissions ||= [];
    outgoing.context.omissions.push(`${omitted} older conversation messages omitted from this request for size. Stored history is unchanged.`);
  }
  if (requestBytes(outgoing) > 240000) throw new Error("This request exceeds the context budget; clear the conversation or select a narrower row.");
  return outgoing;
}

// --- streaming turn shared by the widget and the Assistant page
async function streamChat(body, onEvent, turn) {
  const response = await rawFetch("/api/assistant/chat", { method: "POST", headers: { "X-Conversation-Owner": String(turn.session.user) }, body: JSON.stringify(prepareChatBody(body, turn.project)), signal: turn.controller.signal });
  if (!response.ok) throw await assistResponseError(turn.session, response);
  const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
  for (;;) {
    const { value, done } = await reader.read(); if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx; while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const line = buffer.slice(0, idx).trim(); buffer = buffer.slice(idx + 2);
      if (!line.startsWith("data:")) continue;
      const ev = JSON.parse(line.slice(5));
      if (ev.type === "keepalive") continue;
      if (ev.type === "ui_request") {
        let result; try { result = turn.scope === assistKey() && ASSIST.turn === turn ? await uiPerform(ev.action, ev.args || {}, { allowActions: body.allow_destructive }) : { error: "Conversation changed; interface action cancelled" }; } catch (e) { result = { error: e.message }; }
        if (turn.scope !== assistKey() || ASSIST.turn !== turn) result = { error: "Conversation changed; interface result discarded" };
        onEvent({ type: "ui_action", action: ev.action, args: ev.args, result });
        post("/api/assistant/ui-result", { id: ev.id, result }).catch((e) => toast(`assistant: ${e.message}`, true));
        continue;
      }
      onEvent(ev);
    }
  }
}
function md(text) {
  let s = esc(text);
  s = s.replace(/```([a-z]*)\n([\s\S]*?)```/g, (_, l, c) => `<pre class="mono">${c}</pre>`);
  s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>").replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>");
  s = s.replace(/^(?:[-*] )(.*)$/gm, "• $1");
  return s;
}
function bubbleHtml(m) {
  const calls = (m.calls || []).map((c) => `<details class="muted call"><summary><span class="mono">${esc(c.name)}</span>${c.kind === "ui" ? ' <span class="pill info">interface</span>' : ""}(${esc(JSON.stringify(c.input ?? {})).slice(0, 140)}) ${c.output === undefined ? '<span class="spinner"></span>' : ""}</summary>${c.output !== undefined ? `<pre class="mono">${esc(typeof c.output === "string" ? c.output : JSON.stringify(c.output, null, 1)).slice(0, 3000)}</pre>` : ""}</details>`).join("");
  return calls + (m.content ? `<div class="mdtext">${md(m.content)}</div>` : "");
}

async function loadAssistantModels(force = false) {
  const session = ASSIST.session;
  if (!ASSIST.models || force) ASSIST.models = await api("/api/assistant/models");
  const prov = ASSIST.models;
  if (ASSIST.session !== session) return prov;
  if (!prov[ASSIST.provider]?.available) ASSIST.provider = prov.anthropic.available ? "anthropic" : prov.openai.available ? "openai" : ASSIST.provider;
  const ids = (prov[ASSIST.provider]?.models || []).map((m) => m.id);
  if (!ASSIST.model || (ids.length && !ids.includes(ASSIST.model))) ASSIST.model = prov[ASSIST.provider]?.default || ids[0] || null;
  return ASSIST.models;
}

function assistantModelOptions() {
  const models = ASSIST.models?.[ASSIST.provider]?.models || [];
  const options = models.map((m) => `<option value="${esc(m.id)}" ${m.id === ASSIST.model ? "selected" : ""}>${esc(m.name || m.id)}</option>`).join("");
  const unlisted = ASSIST.model && !models.some((m) => m.id === ASSIST.model) ? `<option value="${esc(ASSIST.model)}" selected>${esc(ASSIST.model)}</option>` : "";
  return options + unlisted || "<option value=''>no models listed</option>";
}
function assistantProviderOptions() {
  return [["anthropic", "Claude"], ["openai", "OpenAI"]].map(([id, label]) => {
    const available = ASSIST.models?.[id]?.available;
    return `<option value="${id}" ${ASSIST.provider === id ? "selected" : ""} ${available ? "" : "disabled"}>${label}${!ASSIST.models || available ? "" : " (no key)"}</option>`;
  }).join("");
}
function assistantModelNotice() {
  if (!ASSIST.models) return '<div class="note">Loading model catalog…</div>';
  const error = ASSIST.models[ASSIST.provider]?.error;
  const missing = !ASSIST.models.anthropic.available && !ASSIST.models.openai.available;
  return (error ? `<div class="note">${esc(error)}</div>` : "") + (missing ? '<div class="note">No API credentials found. Set <span class="mono">ANTHROPIC_API_KEY</span> and/or <span class="mono">OPENAI_API_KEY</span> before starting <span class="mono">tinystudio serve</span>.</div>' : "");
}
function assistDrawLog(log, { preservePosition = false } = {}) {
  const top = log.scrollTop, atBottom = log.scrollHeight - top - log.clientHeight < 40;
  log.innerHTML = ASSIST.history.map((m) => m.role === "user" ? `<div class="bubble user">${esc(m.content)}</div>` : `<div class="bubble assistant">${bubbleHtml(m)}</div>`).join("") || `<div class="muted" style="font-size:12.5px">I can see this page when "page" is ticked, read or operate the interface with the ui_* tools, and run every tinystudio tool: datasets, cleaning, synthetic data, training, evaluation, serving, interpretability. Try: "what is on this page?", "explain the top three neurons of layer 12", "start a LoRA job on the latest snapshot".</div>`;
  log.scrollTop = preservePosition && !atBottom ? top : log.scrollHeight;
}
function assistRenderRemote() {
  // Incoming revisions update the transcript and model controls, leaving the
  // composer node, draft, focus, text selection and page-context controls intact.
  for (const container of $$("#assist .drawer, #asPage")) {
    const log = $("#asLog", container); if (log) assistDrawLog(log, { preservePosition: true });
    const provider = $("#asProvider", container); if (provider) provider.innerHTML = assistantProviderOptions();
    const model = $("#asModel", container); if (model) model.innerHTML = assistantModelOptions();
    const notice = $("#asModelNotice", container); if (notice) notice.innerHTML = assistantModelNotice();
    const usage = $("#asUsage", container); if (usage) usage.textContent = ASSIST.usage || "";
  }
  assistRefreshUI();
}
function renderAssistant(container, { full = false } = {}) {
  container.innerHTML = `
    <div class="head">
      <select id="asProvider" title="Provider">${assistantProviderOptions()}</select>
      <select id="asModel" title="Model (from the provider's live model list)">${assistantModelOptions()}</select>
      <label class="chk" title="Send what this page shows (route, visible text, the data it fetched, your selection) with each message"><input type="checkbox" id="asAttach" ${ASSIST.attach ? "checked" : ""}> page</label>
      <label class="chk" title="Allow interface changes and destructive tools for this session; destructive actions still require confirmation"><input type="checkbox" id="asDestructive" ${ASSIST.destructive ? "checked" : ""}> actions / deletions</label>
      <span class="spacer" style="flex:1"></span>
      <button class="btn small" id="asPick" title="Click a row, chart, or panel to attach its structured data">Pick data</button>
      <button class="btn small" id="asReset" title="Clear the conversation" ${ASSIST.busy || !ASSIST.session.loaded || ASSIST.session.dirty || ASSIST.session.error ? "disabled" : ""}>clear</button>
      ${full ? "" : `<a class="btn small" href="#/assistant" title="Open the full page">⤢</a><button class="btn small" id="asClose" title="Close">✕</button>`}
    </div>
    <div id="asModelNotice">${assistantModelNotice()}</div>
    ${ASSIST.selection ? `<div class="note">Attached: ${esc(ASSIST.selection.label)} · ${esc(ASSIST.selection.source)} <button class="btn small" id="asUnpick">Remove</button></div>` : ""}
    <div class="note"><span id="asPersistence">${esc(assistStatus())}</span> · <span id="asSyncStatus" role="status" aria-live="polite">${esc(assistSyncStatus())}</span> <button class="btn small" id="asRetry" ${ASSIST.session.error && !ASSIST.session.conflict ? "" : "hidden"}>Retry save / load</button> <button class="btn small" id="asReload" hidden>Load saved version</button></div>
    <div class="chatlog ${full ? "" : "compact"}" id="asLog"></div>
    <div class="composer"><textarea id="asInput" placeholder="Ask about what you see, or ask for something to be done… (Enter to send, Shift+Enter for a new line)"></textarea><button class="btn primary" id="asSend" ${ASSIST.busy || !ASSIST.session.loaded || ASSIST.session.dirty || ASSIST.session.error ? "disabled" : ""}>Send</button></div>
    <div class="muted mono" id="asUsage" style="padding:4px 12px;font-size:11px">${esc(ASSIST.usage || "")}</div>`;
  const log = $("#asLog", container);
  const draw = () => assistDrawLog(log);
  draw(); assistRefreshUI();
  $("#asProvider", container).onchange = guard(async (e) => { const session = ASSIST.session; session.provider = e.target.value; session.model = null; await loadAssistantModels(); if (session !== ASSIST.session) return; await assistSave(session); if (session === ASSIST.session) renderAssistant(container, { full }); });
  $("#asModel", container).onchange = guard(async (e) => { ASSIST.model = e.target.value; await assistSave(); });
  $("#asAttach", container).onchange = (e) => { ASSIST.attach = e.target.checked; assistPreferences(); };
  $("#asDestructive", container).onchange = (e) => { ASSIST.destructive = e.target.checked; };
  $("#asPick", container).onclick = () => setAssistantPicking(!ASSIST.picking);
  if ($("#asUnpick", container)) $("#asUnpick", container).onclick = () => { ASSIST.selection = null; renderAssistant(container, { full }); };
  $("#asRetry", container).onclick = guard(async () => {
    const session = ASSIST.session; if (session.busy || session.conflict) return;
    if (session.dirty) {
      // A failed response may have followed a successful commit. Reconcile before another CAS write.
      const saved = await assistEnqueue(session, () => assistRequest(session));
      if (assistSameSnapshot(session, saved)) assistAdopt(session, saved);
      else if (saved.revision !== session.revision) {
        session.error = "A newer conversation is available. Refresh to load it; your unsaved reply remains here until you choose.";
        session.conflict = true;
      } else if (session.streaming) {
        // A disconnected stream can still be running; a partial PUT would invalidate its final save.
        session.error = "The server has not confirmed a saved reply yet. Try again or refresh the conversation.";
      } else await assistSave(session);
    } else await assistLoad(session, { refresh: true });
    assistRefreshUI(); assistRedraw();
  });
  $("#asReload", container).onclick = guard(async () => {
    const session = ASSIST.session; if (session.busy || session.saving) return;
    if (session.ownerMismatch) { location.reload(); return; }
    if (session.dirty && !await confirmModal("Reload conversation?", "Discard the unsaved messages shown here and load the database conversation?", async () => {}, "Discard and reload")) return;
    await assistLoad(session, { refresh: true, discard: true });
  });
  $("#asReset", container).onclick = guard(async () => {
    const session = ASSIST.session;
    if (session.busy || !session.loaded || session.dirty || session.error) return;
    session.history = []; session.usage = ""; await assistSave(session); assistRedraw();
  });
  if ($("#asClose", container)) $("#asClose", container).onclick = () => toggleAssistant(false);
  const send = guard(async () => {
    const session = ASSIST.session;
    const text = $("#asInput", container).value.trim();
    if (!text || !session.loaded || session.busy || session.dirty || session.error) return;
    await assistLoad(session);
    if (session !== ASSIST.session || !session.loaded || session.busy || session.dirty || session.error) return;
    $("#asInput", container).value = "";
    const turn = { scope: session.key, project: session.project, controller: new AbortController(), session };
    session.turn = turn; session.busy = true;
    session.history.push({ role: "user", content: text });
    draw(); assistRefreshUI();
    let reply, streamError;
    const active = () => session.turn === turn && ASSIST.session === session;
    const redraw = () => { if (!active()) return; for (const currentLog of $$("#asLog")) { const last = currentLog.lastElementChild; if (last && reply) last.innerHTML = bubbleHtml(reply); currentLog.scrollTop = currentLog.scrollHeight; } };
    try {
      // A durable user turn is the prerequisite to starting a model request.
      await assistSave(session);
      if (!active()) return;
      const body = { conversation_revision: session.revision, provider: session.provider, model: session.model, allow_destructive: ASSIST.destructive, with_ui: true, messages: session.history.map((m) => ({ role: m.role, content: m.content })), context: ASSIST.attach ? captureContext() : pageContextReady() && ASSIST.selection ? { route: location.hash, project: session.project, selected_data: ASSIST.selection } : null };
      reply = { role: "assistant", content: "", calls: [] }; session.history.push(reply); session.dirty = true; session.streaming = true; draw();
      await streamChat(body, (ev) => {
        if (!active()) return;
        if (ev.type === "text") reply.content += ev.delta;
        else if (ev.type === "tool_call") reply.calls.push({ id: ev.id, name: ev.name, input: ev.input, kind: ev.name.startsWith("ui_") ? "ui" : "tool" });
        else if (ev.type === "tool_result") { const c = reply.calls.find((x) => x.id === ev.id && x.output === undefined) || reply.calls[reply.calls.length - 1]; if (c) c.output = ev.output; pollTasks(); }
        else if (ev.type === "conversation_saved") { session.revision = ev.revision; session.etag = null; session.dirty = false; session.streaming = false; session.pending = false; session.error = ""; session.conflict = false; assistantSync?.notifySaved(ev.revision); assistRefreshUI(); }
        else if (ev.type === "conversation_error") { session.error = ev.message || "The database conversation changed"; session.streaming = false; session.pending = false; session.conflict = ev.conflict === true; session.dirty = true; assistRefreshUI(); }
        else if (ev.type === "error") streamError = new Error(ev.message);
        else if (ev.type === "done") { session.usage = `${ev.model} · ${ev.usage.input_tokens} in / ${ev.usage.output_tokens} out`; $$("#asUsage").forEach((el) => { el.textContent = session.usage; }); }
        redraw();
      }, turn);
      if (!active()) return;
      if (streamError) throw streamError;
      if (!reply.content && !reply.calls.length) reply.content = "(no reply)";
      redraw();
    } catch (error) {
      if (!active()) return;
      if (reply && !reply.content && !reply.calls.length) session.history.pop();
      draw(); if (error.name !== "AbortError") throw error;
    } finally {
      if (active()) {
        // Keep replies in memory on failure and disable send until saved or explicitly discarded.
        try { if (reply && session.dirty && !session.conflict) { session.error ||= "Reply has not been confirmed saved. Refresh to check the database, or retry saving."; } }
        finally { session.busy = false; session.turn = null; assistRefreshUI(); assistantSync?.requestCheck(); }
      }
    }
  });
  $("#asSend", container).onclick = send;
  $("#asInput", container).onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } };
}

function toggleAssistant(open) {
  ASSIST.open = open ?? !ASSIST.open;
  const drawer = $("#assist .drawer"); if (!drawer) return;
  drawer.hidden = !ASSIST.open;
  $("#assist .fab").classList.toggle("on", ASSIST.open);
  assistPreferences();
  if (ASSIST.open) {
    if (!$("#asInput", drawer)) renderAssistant(drawer);
    assistantSync?.requestCheck(); $("#asInput", drawer)?.focus();
    guard(async () => { const session = ASSIST.session; await loadAssistantModels(); if (session === ASSIST.session) assistRenderRemote(); })();
  }
}
function mountAssistant() {
  const host = document.getElementById("assist"); if (!host || host.dataset.mounted) return;
  host.dataset.mounted = "1";
  syncAssistantScope();
  host.innerHTML = `<button class="fab" title="Assistant (Claude / OpenAI) — ask about this page or have it operate the interface">${ICONS.assistant}</button><div class="drawer" hidden></div>`;
  $("#assist .fab").onclick = () => toggleAssistant();
  window.tinystudio = { snapshotPage, uiPerform, captureContext, pageText, ASSIST, toggleAssistant, pickContext };  // for scripts, tests, and the browser console
  document.addEventListener("keydown", (e) => { if ((e.metaKey || e.ctrlKey) && e.key === "k") { e.preventDefault(); toggleAssistant(); } });
  if (ASSIST.open && location.hash !== "#/assistant") toggleAssistant(true);
}

async function pageAssistant() {
  const view = state.pageContext;
  await ensureOverview(true); assertRender(view);
  const st = await api("/api/assistant/status");
  assistantSync?.requestCheck();
  await loadAssistantModels();
  assertRender(view);
  const content = layout("assistant", `<div class="page-head"><h1>Assistant</h1><span class="meta">A frontier model that sees the interface, operates it, and drives the same ${st.mcp.tools} tools as the MCP server. It is also available on every page from the ✦ button (⌘K).</span></div>
    <div class="play"><div>
      <div class="panel"><div class="head"><h2>How it sees the interface</h2></div><div class="body" style="font-size:12.5px">
        <p>With <b>page</b> ticked, every message carries the route, the visible text, the data the page fetched, and your text selection. The <span class="mono">ui_read_page</span> tool returns the live element tree with ref ids; <span class="mono">ui_navigate</span>, <span class="mono">ui_click</span>, <span class="mono">ui_fill</span>, and <span class="mono">ui_select</span> operate it in your tab. Catalog tools run on the server, so long actions show up in the task drawer.</p>
        <p class="muted">Models come from each provider's live model list; pick any Claude or OpenAI model your account can use. Assistant credentials are supplied through the server environment.</p>
      </div></div>
      <div class="panel" style="margin-top:16px"><div class="head"><h2>Use from Claude Code, Codex, Claude Desktop</h2></div><div class="body" style="font-size:12.5px">
        <p>tinystudio is an MCP server. Register it once and your coding assistant can run the whole pipeline in natural language:</p>
        <pre class="mono" style="white-space:pre-wrap;background:var(--panel-2);padding:10px;border-radius:8px">claude mcp add --scope user tinystudio -- ${esc(st.mcp.command)}</pre>
        <p class="muted">Codex: add <span class="mono">[mcp_servers.tinystudio]</span> with the same command and args to <span class="mono">~/.codex/config.toml</span> (Codex keeps one server process per thread; disable the entry when you are not using it). Claude Desktop: add it under <span class="mono">mcpServers</span>.</p>
      </div></div>
    </div>
    <div class="panel assist-page" id="asPage"></div></div>`, [{ label: "Assistant" }]);
  if (ASSIST.open) toggleAssistant(false);
  renderAssistant($("#asPage", content), { full: true });
}

// ------------------------------------------------------------------ traces
async function pageTraces() {
  const view = state.pageContext;
  await ensureOverview(true); assertRender(view);
  const traces = await api("/api/traces?limit=200");
  const datasets = state.overview.tree.datasets.filter((d) => !d.missing);
  assertRender(view);
  const content = layout("traces", `<div class="page-head"><h1>Traces</h1><span class="meta">${traces.length} recent inferences</span><div class="actions"><select id="trDs">${datasets.map((d) => `<option>${esc(d.name)}</option>`).join("")}</select><button class="btn" id="trPromote">Add scored ≥ 0.9 to dataset</button></div></div>
    <div class="panel"><table><thead><tr><th>When · model</th><th>User message</th><th>Response</th><th>Score</th><th></th></tr></thead><tbody>${traces.map((t) => { const u = [...t.messages].reverse().find((m) => m.role === "user"); return `<tr ${dataContext("Inference trace", t)}><td class="mono muted" style="font-size:11.5px">${esc(t.time?.slice(0, 16).replace("T", " "))}<br>${esc(t.model)}${t.origin === "playground" ? "<br><span class='pill'>playground</span>" : ""}</td><td class="truncate">${esc(u?.content)}</td><td class="truncate mono">${esc(t.response)}${t.corrected ? `<div class="muted">corrected: ${esc(t.corrected)}</div>` : ""}</td><td>${t.score === null || t.score === undefined ? "–" : `<span class="pct ${pctClass(t.score)}">${fmtPct(t.score)}</span>`}</td><td style="white-space:nowrap;text-align:right"><button class="btn sm" data-s="${t.id}|1">👍</button> <button class="btn sm" data-s="${t.id}|0">👎</button> <button class="btn sm" data-fix="${t.id}">Fix</button> <button class="btn sm danger" data-del="${t.id}">✕</button></td></tr>`; }).join("") || `<tr><td colspan="5" class="empty">No traces yet. Chat in the Playground or call the API.</td></tr>`}</tbody></table></div>`, [{ label: "Traces" }]);
  $$("[data-s]", content).forEach((b) => b.onclick = guard(async () => { const [id, s] = b.dataset.s.split("|"); await post(`/api/traces/${id}/score`, { score: Number(s) }); pageTraces(); }));
  $$("[data-del]", content).forEach((b) => b.onclick = guard(async () => { await del(`/api/traces/${b.dataset.del}`); pageTraces(); }));
  $$("[data-fix]", content).forEach((b) => b.onclick = () => {
    const t = traces.find((x) => x.id === b.dataset.fix);
    const m = modal({ title: "Correct this response", body: `<div class="field"><label>Corrected answer (used when the trace is promoted)</label><textarea id="fixText">${esc(t.corrected || t.response)}</textarea></div><div class="field"><label>Feedback (optional)</label><input id="fixNote"></div>`, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Save as correct</button>` });
    $("[data-cancel]", m.el).onclick = m.close;
    $("[data-ok]", m.el).onclick = guard(async () => { await post(`/api/traces/${t.id}/score`, { score: 1, corrected: $("#fixText", m.el).value, note: $("#fixNote", m.el).value }); m.close(); pageTraces(); });
  });
  $("#trPromote", content).onclick = guard(async () => { const r = await post("/api/traces/promote", { dataset: $("#trDs", content).value, min_score: 0.9 }); toast(`added ${r.promoted} datapoints to ${$("#trDs", content).value}`); });
}

// ------------------------------------------------------------------ interpretability
const flat = (t) => esc(String(t ?? "")).replace(/\n/g, "⏎");
const ctxHtml = (e) => `<span class="ctx">${flat(e.before)}<b class="tok">${flat(e.token)}</b>${flat(e.after)}</span>`;
const REGIME_NOTE = { "+/+": "open gate × positive value", "+/-": "open gate × negative value", "-/+": "closed gate × positive value (small negative leak)", "-/-": "closed gate × negative value: the double-negative echo (small positive)" };

async function modelChoices() {
  let finished = [];
  try { finished = (await finishedExperiments()).flatMap((e) => [e.id, ...e.checkpoints.map((s) => `${e.id}@${s}`)]); } catch (_) { /* jobs unavailable */ }
  const catalog = (state.overview?.catalog || []).map((c) => c.id);
  return [...new Set([...finished, ...catalog])];
}

function parseLayers(text) {
  const parts = String(text || "").split(",").map((x) => x.trim()).filter(Boolean);
  if (!parts.length || (parts.length === 1 && parts[0].toLowerCase() === "all")) return [];
  const layers = parts.map(Number);
  if (layers.some((n) => !Number.isInteger(n) || n < 0)) throw new Error("layers must be comma-separated non-negative integers, or 'all'");
  return [...new Set(layers)].sort((a, b) => a - b);
}

async function finishedExperiments() {
  const jobs = await api("/api/jobs");
  return jobs.flatMap(({ job, experiments }) => experiments.filter((e) => e.state === "succeeded").map((e) => ({ id: `${job.name}/${e.number}`, checkpoints: e.checkpoints || [], base: e.config?.base_model })));
}

async function captureDialog(preset = {}) {
  const datasets = (state.overview?.tree?.datasets || []).filter((d) => !d.missing);
  const snaps = (state.overview?.tree?.datasets || []).flatMap((d) => d.snapshots || []);
  if (!datasets.length && !snaps.length) return toast("create a dataset first", true);
  const choices = await modelChoices();
  const m = modal({ title: "New activation capture", body: `
    <p class="muted">Runs the dataset through the model once and records, for every MLP neuron of the chosen layers, its strongest positive and negative tokens with their gate/value signs, a gate-open top list, and per-datapoint maxima. SmolLM2-135M over 240 datapoints takes a few seconds per run.</p>
    <div class="field"><label>Model (job/number, job/number@step for a checkpoint, or a Hub id)</label><input id="cpModel" list="cpModels" value="${esc(preset.model || "")}" placeholder="smol-lora/1"><datalist id="cpModels">${choices.map((id) => `<option value="${esc(id)}">`).join("")}</datalist></div>
    <div class="row"><div class="field"><label>Source</label><select id="cpSource">${datasets.map((d) => `<option value="dataset:${esc(d.name)}">dataset ${esc(d.name)} (${d.datapoints})</option>`).join("")}${snaps.map((s) => `<option value="snapshot:${esc(s.name)}">snapshot ${esc(s.name)}</option>`).join("")}</select></div>
    <div class="field"><label>Layers (comma-separated, or all)</label><input id="cpLayers" value="${esc(preset.layers || "")}" placeholder="all"></div></div>
    <div class="row"><div class="field"><label>Top-k per neuron</label><input id="cpTopk" type="number" min="5" max="500" value="40"></div><div class="field"><label>Name (optional)</label><input id="cpName" placeholder="model--dataset"></div></div>`,
    footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Capture</button>` });
  $("[data-cancel]", m.el).onclick = m.close;
  $("[data-ok]", m.el).onclick = guard(async () => {
    const model = $("#cpModel", m.el).value.trim(); if (!model) throw new Error("pick a model");
    const [kind, src] = $("#cpSource", m.el).value.split(":");
    const layers = parseLayers($("#cpLayers", m.el).value);
    const body = { model, layers, topk: Number($("#cpTopk", m.el).value) || 40, name: $("#cpName", m.el).value.trim() || null };
    body[kind] = src;
    await post("/api/interp/captures", body);
    m.close(); toast("capture started"); showTasks(); pollTasks();
  });
}

async function diffDialog() {
  const datasets = (state.overview?.tree?.datasets || []).filter((d) => !d.missing);
  const experiments = await finishedExperiments();
  if (!experiments.length) return toast("no finished experiments to compare", true);
  const choices = await modelChoices();
  const m = modal({ title: "Compare base vs experiment", body: `
    <p class="muted">Runs the same tokens through the base weights and the experiment and reports, per neuron, the correlation of their activations, the relative change, and the datapoints and tokens that drove it. LoRA experiments toggle the adapter in place; full fine-tunes load both models.</p>
    <div class="field"><label>Experiment</label><select id="dfExp">${experiments.map((e) => `<option value="${esc(e.id)}">${esc(e.id)} (base ${esc(e.base || "?")})</option>`).join("")}</select></div>
    <div class="row"><div class="field"><label>Dataset</label><select id="dfDs">${datasets.map((d) => `<option>${esc(d.name)}</option>`).join("")}</select></div><div class="field"><label>Layers (comma-separated, or all)</label><input id="dfLayers" placeholder="all"></div></div>
    <div class="field"><label>Compare against (optional; default = the experiment's own base weights)</label><input id="dfBase" list="dfModels" placeholder="leave empty"><datalist id="dfModels">${choices.map((id) => `<option value="${esc(id)}">`).join("")}</datalist></div>`,
    footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Compare</button>` });
  $("[data-cancel]", m.el).onclick = m.close;
  $("[data-ok]", m.el).onclick = guard(async () => {
    const layers = parseLayers($("#dfLayers", m.el).value);
    await post("/api/interp/diffs", { experiment: $("#dfExp", m.el).value, dataset: $("#dfDs", m.el).value, layers, base: $("#dfBase", m.el).value.trim() || null });
    m.close(); toast("comparison started"); showTasks(); pollTasks();
  });
}

async function checkpointCaptureDialog(job, number) {
  const datasets = (state.overview?.tree?.datasets || []).filter((d) => !d.missing);
  const m = modal({ title: `Capture checkpoints of ${job}/${number}`, body: `<p class="muted">Captures every saved checkpoint plus the final weights on one dataset, so the neuron panel can show how a neuron's strongest examples drifted during training.</p><div class="row"><div class="field"><label>Dataset</label><select id="ckDs">${datasets.map((d) => `<option>${esc(d.name)}</option>`).join("")}</select></div><div class="field"><label>Layers</label><input id="ckLayers" placeholder="e.g. 8,12,16 or all"></div></div>`, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Capture</button>` });
  $("[data-cancel]", m.el).onclick = m.close;
  $("[data-ok]", m.el).onclick = guard(async () => {
    const layers = parseLayers($("#ckLayers", m.el).value);
    await post("/api/interp/checkpoint-captures", { job, number, dataset: $("#ckDs", m.el).value, layers });
    m.close(); toast("checkpoint captures started"); showTasks(); pollTasks();
  });
}

async function pageInterp() {
  const view = state.pageContext;
  await ensureOverview(true); assertRender(view);
  const data = await api("/api/interp");
  assertRender(view);
  const content = layout("interp", `<div class="page-head"><h1>Interpretability</h1><span class="meta">neurons · poles · sign regimes · explanations · before/after · drift</span><div class="actions"><button class="btn" id="newDiff">Compare base vs experiment</button><button class="btn primary" id="newCapture">+ New capture</button></div></div>
    <div class="panel" style="margin-bottom:14px"><div class="head"><h2>Captures</h2><span class="muted">per-neuron MLP activations of one model over one dataset</span></div><div class="body">${data.captures.length ? `<table><thead><tr><th>Name</th><th>Model</th><th>Source</th><th>Layers</th><th>Rows</th><th>Explained</th><th>Tested</th><th>Created</th><th></th></tr></thead><tbody>${data.captures.map((c) => `<tr ${dataContext("Activation capture", c)} class="clickable" data-cap="${esc(c.name)}"><td><b>${esc(c.name)}</b></td><td class="mono">${esc(c.model_id)}</td><td>${esc(c.source)}</td><td class="mono">${c.layers.length === c.num_layers ? "all " + c.num_layers : c.layers.join(", ")}</td><td class="num">${c.rows}</td><td class="num">${c.explained}</td><td class="num">${c.tested}</td><td class="muted">${fmtDate(c.created)}</td><td><button class="btn small danger" data-delcap="${esc(c.name)}">Delete</button></td></tr>`).join("")}</tbody></table>` : `<div class="empty"><b>No captures yet</b>Capture a model over a dataset to browse its neurons.</div>`}</div></div>
    <div class="panel"><div class="head"><h2>Before / after comparisons</h2><span class="muted">which neurons changed most between a base model and an experiment</span></div><div class="body">${data.comparisons.length ? `<table><thead><tr><th>Name</th><th>Experiment</th><th>Base</th><th>Source</th><th>Median r per layer</th><th></th></tr></thead><tbody>${data.comparisons.map((c) => `<tr ${dataContext("Model comparison", c)} class="clickable" data-diff="${esc(c.name)}"><td><b>${esc(c.name)}</b></td><td class="mono">${esc(c.experiment)}</td><td class="mono">${esc(c.base)}</td><td>${esc(c.source)}</td><td class="mono">${Object.entries(c.layer_summary || {}).map(([l, s]) => `L${l} ${s.median_r.toFixed(2)}`).join(" · ")}</td><td><button class="btn small danger" data-deldiff="${esc(c.name)}">Delete</button></td></tr>`).join("")}</tbody></table>` : `<div class="empty"><b>No comparisons yet</b>Compare an experiment against its base to rank changed neurons.</div>`}</div></div>`, [{ label: "Interpretability" }]);
  $("#newCapture", content).onclick = guard(() => captureDialog());
  $("#newDiff", content).onclick = guard(() => diffDialog());
  $$("[data-cap]", content).forEach((r) => r.onclick = (ev) => { if (ev.target.closest("button")) return; go(`#/interp/c/${encodeURIComponent(r.dataset.cap)}`); });
  $$("[data-diff]", content).forEach((r) => r.onclick = (ev) => { if (ev.target.closest("button")) return; go(`#/interp/d/${encodeURIComponent(r.dataset.diff)}`); });
  $$("[data-delcap]", content).forEach((b) => b.onclick = () => confirmModal("Delete capture", `Delete capture "${b.dataset.delcap}" and its explanations and tests?`, async () => { await del(`/api/interp/captures/${encodeURIComponent(b.dataset.delcap)}`); pageInterp(); }));
  $$("[data-deldiff]", content).forEach((b) => b.onclick = () => confirmModal("Delete comparison", `Delete comparison "${b.dataset.deldiff}"?`, async () => { await del(`/api/interp/diffs/${encodeURIComponent(b.dataset.deldiff)}`); pageInterp(); }));
}

function labelBars(pairs) {
  return pairs.length ? `<table>${pairs.map(([k, v]) => `<tr ${dataContext("Distribution entry", {label:k,value:v})}><td>${esc(k)}</td><td class="num">${Math.round(v * 100)}%</td><td style="width:45%"><div style="height:8px;border-radius:4px;background:var(--s1);width:${Math.max(2, v * 100)}%"></div></td></tr>`).join("")}</table>` : `<div class="muted">no labels</div>`;
}

function entryRows(entries, limit = 12) {
  if (!entries.length) return `<div class="muted">none</div>`;
  return `<div class="entries">${entries.slice(0, limit).map((e) => `<div ${dataContext("Neuron activation", e)} class="entry"><span class="mono val">${e.value.toFixed(2)}</span><span class="pill regime" title="${REGIME_NOTE[e.regime]} · g ${e.g.toFixed(2)}, v ${e.v.toFixed(2)}">${e.regime}</span>${e.first_token ? '<span class="pill warn" title="first token after the shared prefix">first</span>' : ""}${ctxHtml(e)}<span class="muted dp">dp ${e.datapoint}${e.labels?.length ? " · " + esc(e.labels.join(", ")) : ""}</span></div>`).join("")}</div>`;
}

let hideClosedPref = true;
async function neuronPanel(right, capture, meta, layer, neuron) {
  const tok = (right._tok = (right._tok || 0) + 1);
  right.innerHTML = `<div class="muted"><span class="spinner"></span> loading neuron ${neuron}…</div>`;
  let v;
  try { v = await api(`/api/interp/captures/${encodeURIComponent(capture)}/layers/${layer}/neurons/${neuron}?limit=20`); }
  catch (e) { if (right._tok === tok) right.innerHTML = `<div class="error">${esc(e.message)}</div>`; return; }
  if (right._tok !== tok) return;
  const s = v.stats;
  const hideClosed = hideClosedPref && v.gate_open.length > 0;
  const list = hideClosed ? v.gate_open : v.positives;
  const exp = v.explanation, t = v.test;
  const openMax = s.open_max == null ? "never open" : s.open_max.toFixed(3);
  right.innerHTML = `
    <div ${dataContext("Neuron activation detail", v)} class="panel ipanel"><div class="head"><h2>L${layer} · N${neuron}</h2><span class="muted mono">max ${s.max.toFixed(3)} · gate-open ${openMax} · min ${s.min.toFixed(3)} · token density ${(s.density * 100).toFixed(1)}%</span></div><div class="body">
      <div class="chips" style="margin-bottom:8px">${Object.entries(v.regimes).map(([r, n]) => `<button class="${r === "-/-" && s.echo_top ? "on" : ""}" title="${REGIME_NOTE[r]} · share of the top-${v.topk} positives" disabled>${r} <b>${n}</b></button>`).join("")}<button id="toggleClosed" class="${hideClosed ? "on" : ""}" title="The double-negative echo: closed gate (g < 0) times a negative value gives a positive activation that looks like the negative pole" ${v.gate_open.length ? "" : "disabled"}>${v.gate_open.length ? (hideClosed ? "hiding gate-closed" : "showing gate-closed") : "gate never opens"}</button></div>
      ${s.echo_top ? `<div class="note">The echo dominates this neuron's positive list: its gate-open maximum is ${s.open_max == null ? "absent" : "below half of its overall maximum"}. Read the negative pole as its job.</div>` : ""}
      ${s.first_token_peak ? `<div class="note">This neuron peaks on the first token after the shared prefix; first-token activations can be attention-sink artefacts.</div>` : ""}
      <h3>Positive pole${hideClosed ? " (gate open)" : ""}</h3>${entryRows(list)}
      <h3>Negative pole</h3>${entryRows(v.negatives, 8)}
      <h3>Share of the top-${v.topk} datapoints carrying each label${hideClosed ? " (gate open)" : ""}</h3>${labelBars(hideClosed ? v.label_share : v.label_share_all)}
    </div></div>
    <div ${dataContext("Neuron explanation and test", {explanation:exp,test:t})} class="panel ipanel"><div class="head"><h2>Explanation</h2><div class="right"><button class="btn small" id="btnExplain">${exp ? "Re-explain" : "Explain with helper LLM"}</button><button class="btn small primary" id="btnTest" ${exp ? "" : "title='Explains first, then tests'"}>Run blind-maximum + near-miss test</button></div></div><div class="body">
      ${exp ? `<p><b>${esc(exp.explanation)}</b></p><p class="muted">negative pole: ${esc(exp.negative_pole || "—")} · confidence ${exp.confidence} · ${esc(exp.helper)}${exp.gate_never_open ? " · gate never open, explained from the echo" : ""}</p>` : `<p class="muted">No explanation yet. The helper LLM reads the strongest contexts (token in bold) and writes a one-line, verb-like rule.</p>`}
      ${t ? `<div class="tiles" style="margin-top:8px"><div class="tile"><div class="k">Blind maximum</div><div class="v">${t.blind_best == null ? "—" : t.blind_best.toFixed(2) + "×"}</div><div class="muted">of the ${esc(t.reference || "corpus max")} ${(t.open_max ?? t.corpus_max).toFixed(2)}${t.beats_corpus ? " · beats it" : ""}</div></div><div class="tile"><div class="k">Positives strong</div><div class="v">${t.counts.positives_strong}/${t.positives.length}</div></div><div class="tile"><div class="k">Near misses strong</div><div class="v">${t.counts.near_misses_strong}/${t.near_misses.length}</div></div><div class="tile"><div class="k">Distant strong</div><div class="v">${t.counts.distant_strong}/${t.distant.length}</div></div><div class="tile"><div class="k">Verdict</div><div class="v"><span class="pill ${t.verdict === "win" ? "good" : t.verdict === "loss" ? "bad" : "warn"}">${t.verdict}</span></div></div></div>
        <details style="margin-top:8px"><summary class="muted">exchanges (strong = ≥ ${Math.round(t.strong_ratio * 100)}% of the ${esc(t.reference || "corpus max")}; ratio uses gate-open activations when the gate ever opened)</summary>${[["Blind maximum", t.blind], ["Should fire", t.positives], ["Near misses", t.near_misses], ["Distant", t.distant]].map(([h, arr]) => `<h3>${h}</h3><table>${arr.map((x) => `<tr ${dataContext("Neuron test exchange", x)}><td>${esc(x.text)}</td><td class="num mono">${x.ratio.toFixed(2)}×</td><td>${x.strong ? '<span class="pill good">strong</span>' : ""}${x.gate_open === false ? '<span class="pill warn" title="the overall max token had its gate closed">echo</span>' : ""}</td><td class="muted mono">${flat(x.token)}</td></tr>`).join("") || "<tr><td class='muted'>none</td></tr>"}</table>`).join("")}</details>` : ""}
    </div></div>
    <div class="panel ipanel" id="driftPanel" hidden><div class="head"><h2>Drift across checkpoints</h2><span class="muted">share of the strongest datapoints per label, per checkpoint</span></div><div class="body" id="driftBody"></div></div>`;
  $("#toggleClosed", right).onclick = guard(() => { hideClosedPref = !hideClosed; return neuronPanel(right, capture, meta, layer, neuron); });
  $("#btnExplain", right).onclick = guard(async () => { await post(`/api/interp/captures/${encodeURIComponent(capture)}/layers/${layer}/explain`, { neurons: [neuron] }); toast("explanation requested"); showTasks(); pollTasks(); });
  $("#btnTest", right).onclick = guard(async () => { await post(`/api/interp/captures/${encodeURIComponent(capture)}/layers/${layer}/test`, { neurons: [neuron] }); toast("test started"); showTasks(); pollTasks(); });
  const match = /^([^/@]+)\/(\d+)(?:@\d+)?$/.exec(meta.model_id);
  if (match) {
    let d = null;
    try { d = await api(`/api/interp/drift/${encodeURIComponent(match[1])}/${match[2]}?source=${encodeURIComponent(meta.source)}&layer=${layer}&neuron=${neuron}`); }
    catch (e) { if (!/no captures/.test(e.message)) toast(`drift: ${e.message}`, true); }
    if (right._tok !== tok || !d || d.steps.length < 2) return;
    $("#driftPanel", right).hidden = false;
    $("#driftBody", right).innerHTML = `<div class="muted" style="margin-bottom:6px">${esc(d.list === "gate_open" ? "gate-open" : "positive")} top-${d.topk} datapoint sets · labels present at the first checkpoint: ${esc(d.core_labels.join(", ") || "—")} · the first saved checkpoint is the baseline, not the base model</div><div class="drift">${d.steps.map((st) => `<div ${dataContext("Checkpoint drift", st)} class="step"><div class="k">${st.final ? "final" : "step " + st.step}</div><div class="mono muted">max ${st.max.toFixed(2)} · ${st.datapoints} datapoints · overlap vs final ${st.jaccard_final}${st.jaccard_prev != null ? " · vs prev " + st.jaccard_prev : ""}</div>${labelBars(st.label_share.slice(0, 5))}${st.new_labels.length ? `<div class="note">new label${st.new_labels.length === 1 ? "" : "s"} ${esc(st.new_labels.join(", "))}: ${st.offshoot_candidates.slice(0, 3).map((o) => esc(o.labels.join("/")) + " (" + flat(o.token) + ")").join(", ")}</div>` : ""}</div>`).join("")}</div>`;
  }
}

async function pageCapture(name, params) {
  const view = state.pageContext;
  await ensureOverview(true); assertRender(view);
  const meta = await api(`/api/interp/captures/${encodeURIComponent(name)}`);
  const layer = Number(params.get("layer") ?? meta.layers[Math.floor(meta.layers.length / 2)]);
  const sort = params.get("sort") || "max";
  const q = params.get("q") || "";
  let neuron = params.has("neuron") ? Number(params.get("neuron")) : null;
  const table = await api(`/api/interp/captures/${encodeURIComponent(name)}/layers/${layer}?sort=${encodeURIComponent(sort)}&limit=300&q=${encodeURIComponent(q)}`);
  const ls = meta.layer_stats?.[String(layer)] || {};
  assertRender(view);
  const content = layout("interp", `
    <div class="page-head"><div><h1>${esc(name)}</h1><div class="meta"><span class="mono">${esc(meta.model_id)}</span><span>· ${esc(meta.source)} · ${meta.rows} datapoints · ${meta.tokens.toLocaleString()} tokens</span><span>· shared prefix of ${meta.prefix_len ?? 0} tokens excluded</span></div></div>
      <div class="actions"><select id="layerSel">${meta.layers.map((l) => `<option value="${l}" ${l === layer ? "selected" : ""}>layer ${l}</option>`).join("")}</select><select id="sortSel">${["max", "min", "open_max", "echo_share", "density"].map((k) => `<option value="${k}" ${k === sort ? "selected" : ""}>sort by ${k.replace("_", " ")}</option>`).join("")}</select><input id="qBox" placeholder="filter by explanation" value="${esc(q)}" style="width:180px"><button class="btn" id="explainTop">Explain top 20</button></div></div>
    <div class="tiles" style="margin-bottom:12px"><div class="tile"><div class="k">Neurons</div><div class="v">${meta.neurons_per_layer}</div><div class="muted">${table.total > 300 ? "showing 300 of " + table.total : table.total + " listed"}</div></div><div class="tile"><div class="k">Median max</div><div class="v">${(ls.max_median ?? 0).toFixed(2)}</div></div><div class="tile"><div class="k">Echo dominates top</div><div class="v">${Math.round((ls.echo_top_share ?? 0) * 100)}%</div><div class="muted">gate-open max under half of max</div></div><div class="tile"><div class="k">Mean echo share</div><div class="v">${Math.round((ls.echo_share_mean ?? 0) * 100)}%</div><div class="muted">of top-${meta.topk} positives in −/−</div></div><div class="tile"><div class="k">First-token peaks</div><div class="v">${ls.first_token_peaks ?? 0}</div><div class="muted">neurons peaking right after the prefix</div></div><div class="tile"><div class="k">Never open / dead</div><div class="v">${ls.never_open ?? 0} / ${ls.near_dead ?? 0}</div></div></div>
    <div class="wide-narrow"><div id="left"><div class="panel"><div class="body" style="padding:0"><table class="neurons"><thead><tr><th>#</th><th>max</th><th>open</th><th>min</th><th>echo</th><th title="share of content tokens above 10% of the neuron's maximum">density</th><th>explanation</th><th>test</th></tr></thead><tbody>${table.rows.map((r) => `<tr ${dataContext("Neuron", r)} class="clickable ${r.neuron === neuron ? "sel" : ""}" data-n="${r.neuron}"><td class="mono">${r.neuron}</td><td class="num">${r.max.toFixed(2)}</td><td class="num">${r.open_max == null ? "—" : r.open_max.toFixed(2)}</td><td class="num">${r.min.toFixed(2)}</td><td class="num">${Math.round(r.echo_share * 100)}%${r.echo_top ? "*" : ""}</td><td class="num">${(r.density * 100).toFixed(1)}%</td><td class="truncate" style="max-width:260px">${esc(r.explanation || "")}</td><td>${r.verdict ? `<span class="pill ${r.verdict === "win" ? "good" : r.verdict === "loss" ? "bad" : "warn"}">${r.verdict}</span>` : ""}</td></tr>`).join("")}</tbody></table></div></div></div><div id="right"><div class="empty"><b>Pick a neuron</b>Click a row to see its poles, sign regimes, label shares, explanation, and tests.</div></div></div>`,
    [{ label: "Interpretability", href: "#/interp" }, { label: name }]);
  const nav = (changes) => { const p = new URLSearchParams(params); Object.entries(changes).forEach(([k, v]) => v == null || v === "" ? p.delete(k) : p.set(k, v)); go(`#/interp/c/${encodeURIComponent(name)}?${p}`); };
  $("#layerSel", content).onchange = (e) => nav({ layer: e.target.value, neuron: null });
  $("#sortSel", content).onchange = (e) => nav({ sort: e.target.value });
  $("#qBox", content).onchange = (e) => nav({ q: e.target.value });
  $("#explainTop", content).onclick = guard(async () => { await post(`/api/interp/captures/${encodeURIComponent(name)}/layers/${layer}/explain`, { top: 20, sort }); toast("explaining the top 20 neurons"); showTasks(); pollTasks(); });
  const right = $("#right", content);
  const select = (n) => { neuron = n; $$("tr[data-n]", content).forEach((tr) => tr.classList.toggle("sel", Number(tr.dataset.n) === n)); params.set("layer", layer); params.set("neuron", n); history.replaceState(null, "", `#/interp/c/${encodeURIComponent(name)}?${params}`); if (state.pageContext === view) view.route = location.hash; return neuronPanel(right, name, meta, layer, n); };
  $$("tr[data-n]", content).forEach((tr) => tr.onclick = guard(() => select(Number(tr.dataset.n))));
  if (neuron != null && Number.isInteger(neuron)) select(neuron);
}

async function pageDiff(name, params) {
  const view = state.pageContext;
  await ensureOverview(true); assertRender(view);
  const meta = await api(`/api/interp/diffs/${encodeURIComponent(name)}`);
  const layer = Number(params.get("layer") ?? meta.layers[meta.layers.length - 1]);
  const sort = params.get("sort") || "change";
  const data = await api(`/api/interp/diffs/${encodeURIComponent(name)}/layers/${layer}?limit=200&sort=${encodeURIComponent(sort)}`);
  const s = data.summary || {};
  const fr = (x) => (x == null ? "—" : x.toFixed(3));
  assertRender(view);
  const content = layout("interp", `
    <div class="page-head"><div><h1>${esc(meta.experiment)} <span class="muted">vs</span> ${esc(meta.base)}</h1><div class="meta"><span>${esc(meta.source)} · ${meta.rows} datapoints · ${meta.mode === "adapter_toggle" ? "adapter toggled in place" : "two models"} · shared prefix of ${meta.prefix_len ?? 0} tokens excluded</span></div></div>
      <div class="actions"><select id="layerSel">${meta.layers.map((l) => `<option value="${l}" ${l === layer ? "selected" : ""}>layer ${l} · median r ${fr(meta.layer_summary?.[String(l)]?.median_r)}</option>`).join("")}</select><select id="sortSel">${[["change", "sort by change (1 − r + relative change)"], ["r", "sort by lowest r"], ["rel_change", "sort by relative change"]].map(([k, l]) => `<option value="${k}" ${k === sort ? "selected" : ""}>${l}</option>`).join("")}</select></div></div>
    <div class="tiles" style="margin-bottom:12px"><div class="tile"><div class="k">Median correlation r</div><div class="v">${fr(s.median_r)}</div></div><div class="tile"><div class="k">Neurons with r &lt; 0.9</div><div class="v">${s.below_0_9 ?? 0}</div></div><div class="tile"><div class="k">r &lt; 0.5</div><div class="v">${s.below_0_5 ?? 0}</div></div><div class="tile"><div class="k">Constant on one side</div><div class="v">${s.constant ?? 0}</div><div class="muted">r undefined (dead or revived)</div></div><div class="tile"><div class="k">Median relative change</div><div class="v">${(s.rel_change_median ?? 0).toFixed(2)}</div></div></div>
    <div class="two" style="margin-bottom:12px"><div class="panel"><div class="head"><h2>Distribution of r across ${meta.neurons_per_layer} neurons</h2></div><div class="body">${s.hist ? histogram(s.hist, "var(--s2)", 420, 100) : ""}</div></div><div class="panel"><div class="head"><h2>Reading this</h2></div><div class="body muted">r is the Pearson correlation of a neuron's activations before and after fine-tuning over the same tokens (undefined when one side is constant); relative change is ‖after − before‖ / ‖before‖, so a neuron that only scaled still shows up; change = (1 − r) + min(relative change, 1). Click a neuron for the datapoints and tokens that moved it most. Shared-prefix tokens are excluded because causal attention makes them identical across datapoints.</div></div></div>
    <div class="wide-narrow"><div id="left"><div class="panel"><div class="head"><h2>Most changed neurons</h2></div><div class="body" style="padding:0"><table class="neurons"><thead><tr><th>#</th><th>change</th><th>r</th><th>rel. change</th><th>largest token change</th><th>context</th></tr></thead><tbody>${data.rows.map((r) => `<tr ${dataContext("Neuron comparison", r)} class="clickable" data-n="${r.neuron}"><td class="mono">${r.neuron}</td><td class="num">${r.change.toFixed(2)}</td><td class="num">${fr(r.r)}</td><td class="num">${r.rel_change.toFixed(2)}</td><td class="num mono">${r.before.toFixed(2)} → ${r.after.toFixed(2)}</td><td class="ctxcell">${flat(r.context)}</td></tr>`).join("")}</tbody></table></div></div></div><div id="right"><div class="empty"><b>Pick a neuron</b>See which datapoints drove its change.</div></div></div>`,
    [{ label: "Interpretability", href: "#/interp" }, { label: name }]);
  $("#layerSel", content).onchange = (e) => go(`#/interp/d/${encodeURIComponent(name)}?layer=${e.target.value}&sort=${encodeURIComponent(sort)}`);
  $("#sortSel", content).onchange = (e) => go(`#/interp/d/${encodeURIComponent(name)}?layer=${layer}&sort=${encodeURIComponent(e.target.value)}`);
  const right = $("#right", content);
  $$("tr[data-n]", content).forEach((tr) => tr.onclick = guard(async () => {
    const n = Number(tr.dataset.n);
    $$("tr[data-n]", content).forEach((x) => x.classList.toggle("sel", x === tr));
    const d = await api(`/api/interp/diffs/${encodeURIComponent(name)}/layers/${layer}/neurons/${n}`);
    const captures = (await api("/api/interp")).captures.filter((c) => c.model_id === meta.experiment && c.source === meta.source && c.layers.includes(layer));
    right.innerHTML = `<div ${dataContext("Neuron change detail", d)} class="panel ipanel"><div class="head"><h2>L${layer} · N${n}</h2><span class="muted mono">change ${d.change.toFixed(2)} · r ${fr(d.r)} · rel. change ${d.rel_change.toFixed(2)}</span></div><div class="body">
      <h3>Datapoints that drove the change</h3><table>${d.datapoints.map((x) => `<tr ${dataContext("Changed datapoint", x)}><td class="num mono">${x.delta_sum.toFixed(2)}</td><td>${esc(x.user)}</td><td class="muted">${esc((x.labels || []).join(", "))}</td></tr>`).join("") || "<tr><td class='muted'>none</td></tr>"}</table>
      <h3>Tokens with the largest change</h3><div class="entries">${d.tokens.map((x) => `<div class="entry"><span class="mono val">${x.before.toFixed(2)} → ${x.after.toFixed(2)}</span><span class="ctx">${flat(x.context)}</span><span class="muted dp">dp ${x.datapoint}</span></div>`).join("")}</div>
      ${captures.length ? `<p style="margin-top:10px"><a href="#/interp/c/${encodeURIComponent(captures[0].name)}?layer=${layer}&neuron=${n}">Open this neuron in capture ${esc(captures[0].name)} →</a></p>` : `<p class="muted" style="margin-top:10px">Capture ${esc(meta.experiment)} on ${esc(meta.source)} to browse this neuron's poles.</p>`}
    </div></div>`;
  }));
}

// ------------------------------------------------------------------ stats
async function pageStats() {
  const view = state.pageContext;
  await ensureOverview(true); assertRender(view);
  const s = await api("/api/stats");
  const tile = (k, v) => `<div ${dataContext(k, {label:k,value:v})} class="tile"><div class="k">${k}</div><div class="v">${v}</div></div>`;
  const max = Math.max(1, ...s.requests_per_day.map(([, n]) => n));
  assertRender(view);
  layout("stats", `<div class="page-head"><h1>Stats</h1></div>
    <div class="tiles">${tile("Datasets", s.datasets)}${tile("Datapoints", s.datapoints)}${tile("Snapshots", s.snapshots)}${tile("Jobs", s.jobs)}${tile("Experiments succeeded", s.experiments.succeeded || 0)}${tile("Training time", `${Math.round(s.training_seconds / 60)} min`)}${tile("Evaluations", s.evaluations)}${tile("Requests served", s.requests)}${tile("Tokens served", s.prompt_tokens + s.completion_tokens)}${tile("Scored traces", s.scored_traces)}</div>
    <div class="two"><div class="panel"><div class="head"><h2>Requests per day</h2></div><div class="body">${s.requests_per_day.length ? `<div ${dataContext("Requests per day", s.requests_per_day)} class="bars">${s.requests_per_day.map(([d, n]) => `<div title="${d}: ${n}" style="height:${Math.max(4, (n / max) * 100)}%"></div>`).join("")}</div><div class="muted" style="display:flex;justify-content:space-between;font-size:11px;margin-top:4px"><span>${s.requests_per_day[0][0]}</span><span>${s.requests_per_day[s.requests_per_day.length - 1][0]}</span></div>` : `<div class="empty">No requests yet.</div>`}</div></div>
    <div class="panel"><div class="head"><h2>Requests per model</h2></div><div class="body"><table>${s.requests_per_model.map(([m, n]) => `<tr ${dataContext("Requests per model", {model:m,requests:n})}><td class="mono">${esc(m)}</td><td class="num">${n}</td></tr>`).join("") || `<tr><td class="muted">none</td></tr>`}</table><p class="muted" style="margin-top:10px">Loaded now: ${s.models_loaded.map(esc).join(", ") || "none"}</p></div></div></div>`, [{ label: "Stats" }]);
}

// ------------------------------------------------------------------ api keys
async function pageKeys() {
  const view = state.pageContext;
  await ensureOverview(true); assertRender(view);
  const keys = await api("/api/keys");
  assertRender(view);
  const content = layout("keys", `<div class="page-head"><h1>API Keys</h1><div class="actions"><button class="btn primary" id="newKey">+ Create API key</button></div></div>
    <div class="panel"><table><thead><tr><th>Name</th><th>Prefix</th><th>Created</th><th>Last used</th><th></th></tr></thead><tbody>${keys.map((k) => `<tr><td><b>${esc(k.name)}</b></td><td class="mono">${esc(k.prefix)}…</td><td>${fmtDate(k.created)}</td><td>${fmtDate(k.last_used)}</td><td style="text-align:right"><button class="btn sm danger" data-rev="${k.id}">Revoke</button></td></tr>`).join("") || `<tr><td colspan="5" class="empty">No keys yet. Keys belong to your account and work on /v1 and /api (MCP clients use them).</td></tr>`}</tbody></table></div>
    <div class="panel" style="margin-top:16px"><div class="head"><h2>Using your key</h2></div><div class="body"><pre class="mono" style="white-space:pre-wrap">curl ${location.origin}/v1/chat/completions \\\n  -H "Authorization: Bearer ts-..." -H "content-type: application/json" \\\n  -d '{"model": "${esc(state.project)}/&lt;job&gt;/&lt;n&gt;", "messages": [{"role": "user", "content": "hello"}]}'</pre><p class="muted">Works with the OpenAI SDKs: set <span class="mono">base_url</span> to <span class="mono">${location.origin}/v1</span>. Model ids are <span class="mono">&lt;project&gt;/&lt;job&gt;/&lt;n&gt;</span>, or send the header <span class="mono">X-Project: ${esc(state.project)}</span> and use <span class="mono">&lt;job&gt;/&lt;n&gt;</span>.</p></div></div>`, [{ label: "API Keys" }]);
  $("#newKey", content).onclick = () => {
    const m = modal({ title: "Create API key", body: `<div class="field"><label>Name</label><input id="kName" placeholder="my-app"></div>`, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Create</button>` });
    $("[data-cancel]", m.el).onclick = m.close;
    $("[data-ok]", m.el).onclick = guard(async () => {
      const r = await post("/api/keys", { name: $("#kName", m.el).value });
      m.close();
      modal({ title: "Your new API key", sensitive: true, body: `<p>Copy it now; it is shown only once.</p><pre class="mono" style="background:var(--panel-2);padding:12px;border-radius:8px;word-break:break-all">${esc(r.key)}</pre>`, footer: `<button class="btn primary" data-done>Done</button>`, onMount: (el, close) => { $("[data-done]", el).onclick = () => { close(); pageKeys(); }; } });
    });
  };
  $$("[data-rev]", content).forEach((b) => b.onclick = () => confirmModal("Revoke key", "Revoke this key? Clients using it will get 401.", async () => { await del(`/api/keys/${b.dataset.rev}`); pageKeys(); }, "Revoke"));
}

// ------------------------------------------------------------------ tasks drawer
function showTasks() { $("#tasks").hidden = false; }
async function pollTasks() {
  let tasks;
  try { tasks = await api("/api/tasks"); } catch (_) { return; }
  const wasRunning = new Set(state.tasks.filter((t) => t.state === "running" || t.state === "queued").map((t) => t.id));
  state.tasks = tasks;
  const box = $("#tasks");
  box.innerHTML = `<div class="head"><h2>Tasks</h2><div class="right"><button class="btn sm" id="tHide">Hide</button></div></div>` + (tasks.length ? tasks.slice(0, 6).map((t) => `<div class="task"><span class="pill ${t.state}">${t.state}</span> <b>${esc(t.kind)}</b> <span class="muted mono" style="font-size:11px">${esc((t.started || t.created || "").slice(11, 19))}</span>${t.state === "running" || t.state === "queued" ? ` <button class="btn sm" style="float:right" data-stop="${t.id}">Stop</button>` : ""}<div class="mono muted" style="font-size:11px;word-break:break-all;margin-top:4px">${esc(JSON.stringify(t.args || {})).slice(0, 160)}</div><pre>${esc((t.log_tail || []).slice(-12).join("\n"))}</pre></div>`).join("") : `<div class="task muted">Nothing running.</div>`);
  $("#tHide", box).onclick = () => box.hidden = true;
  $$("[data-stop]", box).forEach((b) => b.onclick = guard(async () => { await post(`/api/tasks/${b.dataset.stop}/stop`, {}); pollTasks(); }));
  const running = tasks.some((t) => t.state === "running" || t.state === "queued");
  if (tasks.some((t) => wasRunning.has(t.id) && t.state !== "running" && t.state !== "queued")) { toast("a task finished"); render(); }
  clearTimeout(state.taskTimer);
  if (running) { showTasks(); state.taskTimer = setTimeout(pollTasks, 2500); }
}

// ------------------------------------------------------------------ boot
guard(async () => {
  const auth = await api("/api/auth/status");
  if (auth.setup_required) return showSetup();
  if (!auth.authenticated) return showLogin();
  state.user = auth.user;
  await ensureProject();
  await ensureOverview();
  mountAssistant();
  await render();
  pollTasks();
})();
