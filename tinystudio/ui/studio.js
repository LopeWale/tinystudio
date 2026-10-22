// tinystudio web interface: a Studio-style single-page app over the local API.
// No build step. Hash routes, vanilla DOM, SVG charts.

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

const state = { overview: null, tasks: [], taskTimer: null, playground: { model: null, system: "", temperature: 0.2, history: [] } };

// ------------------------------------------------------------------ http
async function api(path, options = {}) {
  const response = await fetch(path, { headers: options.body instanceof FormData ? {} : { "content-type": "application/json" }, ...options });
  if (!response.ok) {
    let detail = response.statusText;
    try { const j = await response.json(); detail = j.detail ?? detail; } catch (_) {}
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  const type = response.headers.get("content-type") || "";
  return type.includes("json") ? response.json() : response.text();
}
const post = (path, body) => api(path, { method: "POST", body: body instanceof FormData ? body : JSON.stringify(body ?? {}) });
const put = (path, body) => api(path, { method: "PUT", body: JSON.stringify(body ?? {}) });
const del = (path) => api(path, { method: "DELETE" });

function toast(message, isError = false) {
  const el = document.createElement("div");
  el.className = "toast" + (isError ? " err" : ""); el.textContent = message;
  document.body.appendChild(el); setTimeout(() => el.remove(), isError ? 9000 : 3500);
}
const guard = (fn) => async (...args) => { try { return await fn(...args); } catch (e) { toast(e.message, true); } };

// ------------------------------------------------------------------ modal
function modal({ title, body, footer = "", wide = false, onMount }) {
  const back = document.createElement("div"); back.className = "modal-back";
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
];
function go(hash) { location.hash = hash; }
async function render() {
  const hash = location.hash || "#/";
  for (const [re, fn] of routes) {
    const m = hash.match(re);
    if (m) { try { await fn(m); } catch (e) { toast(e.message, true); layout("Error", `<div class="error">${esc(e.message)}</div>`); } return; }
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
};
function sidebar(active) {
  const o = state.overview || {};
  const item = (hash, key, label, icon) => `<a href="${hash}" class="${active === key ? "active" : ""}">${ICONS[icon]}<span>${label}</span></a>`;
  return `<aside class="sidebar">
    <div class="brand"><div class="logo"><svg width="16" height="16" viewBox="0 0 32 32"><path d="M8 22 L13 12 L18 18 L24 9" fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg></div><div>tinystudio<small>${esc(o.project?.name || "")}</small></div></div>
    <nav class="nav">
      ${item("#/datasets", "datasets", "Datasets", "datasets")}
      ${item("#/finetuning", "finetuning", "Finetuning", "finetune")}
      ${item("#/evaluations", "evaluations", "Evaluations", "evals")}
      <div class="sub">${item("#/evaluations", "evaluations-sub", "Eval", "evals").replace(ICONS.evals, "")}${item("#/metrics", "metrics", "Metrics", "evals").replace(ICONS.evals, "")}</div>
      ${item("#/playground", "playground", "Playground", "play")}
      ${item("#/assistant", "assistant", "Assistant", "assistant")}
      <hr>
      ${item("#/models", "models", "Models", "models")}
      ${item("#/traces", "traces", "Traces", "traces")}
      ${item("#/api-keys", "keys", "API Keys", "keys")}
      ${item("#/stats", "stats", "Stats", "stats")}
      <hr>
      <a href="/docs" target="_blank">${ICONS.docs}<span>API reference</span><span class="external">↗</span></a>
    </nav>
    <div class="foot"><div class="avatar">${esc((o.project?.name || "t")[0].toUpperCase())}</div><div><div>${esc(o.project?.name || "")}</div><div class="mono" style="font-size:11px">${esc(o.llm?.base_url ? "helper LLM on" : "no helper LLM")}</div></div></div>
  </aside>`;
}
function layout(active, contentHtml, crumbs = []) {
  const app = $("#app");
  app.innerHTML = `${sidebar(active)}<div class="main"><div class="topbar">${crumbs.map((c, i) => `${i ? '<span class="sep">›</span>' : ""}${c.href ? `<a href="${c.href}">${esc(c.label)}</a>` : `<span>${esc(c.label)}</span>`}`).join("")}<span class="spacer"></span><span id="llmStatus"></span></div><div class="content">${contentHtml}</div></div>`;
  return $(".content", app);
}
async function ensureOverview(force = false) {
  if (!state.overview || force) state.overview = await api("/api/overview");
  return state.overview;
}

// ------------------------------------------------------------------ datasets
async function pageDatasets() {
  await ensureOverview(true);
  const datasets = await api("/api/datasets");
  const content = layout("datasets", `
    <div class="page-head"><h1>Datasets</h1><span class="meta">${datasets.length} dataset${datasets.length === 1 ? "" : "s"}</span><div class="actions"><input id="dsSearch" placeholder="Search" style="width:220px"><button class="btn primary" id="newDataset">+ Create Dataset</button></div></div>
    <div class="cards" id="dsCards"></div>`, [{ label: "Datasets" }]);
  const draw = (q = "") => {
    const rows = datasets.filter((d) => d.name.toLowerCase().includes(q.toLowerCase()));
    $("#dsCards", content).innerHTML = rows.length ? rows.map((d) => `<div class="card clickable" data-ds="${esc(d.name)}"><h3>${esc(d.name)}</h3><div class="sub">${d.datapoints} datapoints · ${d.snapshot_count} snapshot${d.snapshot_count === 1 ? "" : "s"}${d.origin?.kind === "synthetic" ? ' · <span class="pill synthetic">Synthetic</span>' : ""}</div><div class="foot"><span>Created on: ${fmtDate(d.created)}</span><span class="arrow">→</span></div></div>`).join("") : `<div class="empty"><b>No datasets yet</b>Upload a JSONL file or generate one from your documents.</div>`;
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
          const form = new FormData(); form.append("name", body.dataset.name); form.append("file", files[0]);
          const meta = await post("/api/datasets/upload", form);
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
  await ensureOverview(true);
  let offset = 0; const limit = 15;
  const info = state.overview.tree.datasets.find((d) => d.name === name);
  const snaps = info?.snapshots || [];
  const content = layout("datasets", `
    <div class="page-head"><div><h1>${esc(name)}</h1><div class="meta">Created on: ${fmtDate(info?.created)} ${info?.origin?.kind === "synthetic" ? '· <span class="pill synthetic">Synthetic</span>' : ""}</div></div>
      <div class="actions">
        <button class="btn primary" id="btnFinetune">Start finetuning</button>
        <button class="btn" id="btnEnrich">✨ Enrich</button>
        <button class="btn" id="btnSnapshots">📸 Snapshots</button>
        <button class="btn" id="btnSplit">⚖ Autosplit</button>
        <button class="btn" id="btnLabels">🏷 Labels</button>
        <button class="btn" id="btnQuality">📊 Quality</button>
        <a class="btn" href="/api/datasets/${encodeURIComponent(name)}/download">⬇</a>
        <button class="btn" id="btnAdd">+</button>
        <button class="btn danger" id="btnDelete">Delete</button>
      </div></div>
    ${snaps.length ? "" : `<div class="pill info" style="margin-bottom:12px">No snapshots yet. Create one to run finetuning and evaluation.</div>`}
    <div class="tiles" id="tiles"></div>
    <div class="panel"><div class="head"><h2>Datapoints</h2><span class="muted" id="dpCount"></span><div class="right"><select id="bucketFilter"><option value="">All buckets</option><option>uncategorized</option><option>training</option><option>validation</option></select></div></div><div id="dpTable"></div></div>`,
    [{ label: "Datasets", href: "#/datasets" }, { label: name }]);
  let filter = "";
  const drawTiles = (meta) => {
    $("#tiles", content).innerHTML = [["uncategorized", "var(--text-3)"], ["training", "var(--s1)"], ["validation", "var(--s3)"]].map(([b, c]) => `<div class="tile ${filter === b ? "active" : ""}" data-b="${b}"><div class="k"><span class="dot" style="background:${c}"></span>${b[0].toUpperCase() + b.slice(1)}</div><div class="v">${meta.buckets[b]}</div></div>`).join("") + (meta.labels?.length ? `<div class="tile"><div class="k">Labels</div><div class="v" style="font-size:13px">${meta.labels.map((l) => esc(l.name)).join(", ")}</div></div>` : "");
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
      return `<tr class="clickable" data-i="${r.index}"><td class="mono muted">${r.index + 1}</td><td class="truncate">${esc(user)}</td><td class="truncate mono">${esc(r.messages[r.messages.length - 1].content)}</td><td><span class="pill">${r.bucket}</span></td><td class="muted">${esc((r.labels || []).filter((l) => l !== "synthetic" && l !== "augmented").join(", "))}</td><td>${r.source?.startsWith("synthetic") ? '<span class="pill synthetic">Synthetic</span>' : r.source?.startsWith("trace") ? '<span class="pill info">Trace</span>' : '<span class="pill">Uploaded</span>'}</td></tr>`;
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
    const m = modal({ title: "Enrich dataset", body: `<p class="muted">Paraphrase existing user turns with the helper model so the model sees more ways of asking. Split before you enrich if you want a clean validation set.</p><div class="field"><label>Datapoints to generate</label><input id="enCount" type="number" min="1" max="2000" value="50"></div>`, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Enrich</button>` });
    $("[data-cancel]", m.el).onclick = m.close;
    $("[data-ok]", m.el).onclick = guard(async () => { await post("/api/augment", { dataset: name, count: Number($("#enCount", m.el).value) }); m.close(); showTasks(); pollTasks(); });
  };
  $("#btnSplit", content).onclick = () => {
    const m = modal({ title: "Autosplit dataset", body: `<p class="muted">Assign every uncategorized datapoint to training or validation. Existing assignments are kept.</p><div class="field"><label>Training percentage: <b id="spV">80</b>%</label><input id="sp" type="range" min="1" max="99" value="80"></div><label class="radio-card" id="synthCard"><input type="checkbox" id="allowSynth"><div><b>Allow synthetic data in validation</b><span>Off keeps generated rows out of the validation split so the score reflects real data.</span></div></label>`, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Split</button>` });
    $("#sp", m.el).oninput = (e) => $("#spV", m.el).textContent = e.target.value;
    $("[data-cancel]", m.el).onclick = m.close;
    $("[data-ok]", m.el).onclick = guard(async () => { const meta = await post(`/api/datasets/${encodeURIComponent(name)}/split`, { train_pct: Number($("#sp", m.el).value), synthetic_in_validation: $("#allowSynth", m.el).checked }); toast(`training ${meta.buckets.training} · validation ${meta.buckets.validation}`); m.close(); load(); });
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
    <table><thead><tr><th>Name</th><th>Training</th><th>Validation</th><th>Created</th><th></th></tr></thead><tbody>${snaps.map((s) => `<tr><td><b>${esc(s.name)}</b></td><td>${s.training_count}</td><td>${s.validation_count}</td><td>${fmtDate(s.created)}</td><td style="text-align:right"><a class="btn sm" href="/api/snapshots/${encodeURIComponent(s.name)}/download">⬇ Download</a> <button class="btn sm danger" data-del="${esc(s.name)}">Delete</button></td></tr>`).join("") || `<tr><td colspan="5" class="muted">None yet.</td></tr>`}</tbody></table>
    <div class="row" style="margin-top:14px"><div class="field"><label>New snapshot name</label><input id="snName" placeholder="${esc(dataset)}-v${snaps.length + 1}"></div><button class="btn primary" id="snCreate">Create snapshot</button></div>`, footer: `<button class="btn" data-close>Close</button>` });
  $("[data-close]", m.el).onclick = m.close;
  $("#snCreate", m.el).onclick = guard(async () => { const nm = $("#snName", m.el).value.trim() || `${dataset}-v${snaps.length + 1}`; const s = await post("/api/snapshots", { dataset, name: nm }); toast(`snapshot ${s.name}: ${s.training_count} / ${s.validation_count}`); m.close(); after(); });
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
  return `<svg viewBox="0 0 ${width} ${height}" style="width:100%;height:${height}px">${bars}<text x="0" y="${height - 2}" font-size="10" fill="var(--text-3)" font-family="var(--mono)">${h.bins[0]}</text><text x="${width}" y="${height - 2}" font-size="10" fill="var(--text-3)" text-anchor="end" font-family="var(--mono)">${h.bins[h.bins.length - 1]}</text></svg>`;
}
async function qualityDialog(dataset, after) {
  const body = document.createElement("div");
  body.innerHTML = `<div class="muted"><span class="spinner"></span> profiling…</div>`;
  const m = modal({ title: `Data quality · ${dataset}`, body, wide: true, footer: `<button class="btn" data-close>Close</button>` });
  $("[data-close]", m.el).onclick = m.close;
  const p = await api(`/api/datasets/${encodeURIComponent(dataset)}/quality`);
  const stat = (k, v) => `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div></div>`;
  const dist = (pairs) => pairs.length ? `<table>${pairs.map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${v}</td><td style="width:40%"><div style="height:8px;border-radius:4px;background:var(--s1);width:${Math.max(2, 100 * v / pairs[0][1])}%"></div></td></tr>`).join("")}</table>` : `<div class="muted">none</div>`;
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
    ${d.examples.length ? `<div class="panel" style="margin-bottom:14px"><div class="head"><h2>Duplicate examples</h2></div><div class="body">${d.examples.map((g) => `<div class="mono" style="font-size:12px;margin-bottom:4px">${g.map(esc).join("  ·  ")}</div>`).join("")}${d.near_pairs.length ? `<div class="muted" style="margin-top:8px">Near duplicates (Jaccard over word n-grams ≥ 0.7): ${d.near_pairs.slice(0, 5).map((x) => `#${x.a + 1}≈#${x.b + 1} (${x.similarity})`).join(", ")}${d.near_pairs.length > 5 ? " …" : ""}</div>` : ""}</div></div>` : ""}
    <div class="panel"><div class="head"><h2>Clean</h2><span class="muted">Applies in place; every run is logged to cleaning.jsonl. Snapshots are not affected.</span></div><div class="body">
      <div class="chips" id="cleanOpts">
        <button data-k="dedupe_user">Drop exact duplicate user turns (${d.exact_user_rows})</button>
        <button data-k="dedupe_conversation">Drop exact duplicate conversations (${d.exact_conversation_rows})</button>
        <button data-k="drop_near_duplicates">Drop near duplicates (${d.near_pair_count} pairs)</button>
        <button data-k="drop_empty">Drop empty-like rows (${o.empty_like.length})</button>
        <button data-k="trim_whitespace">Trim whitespace</button>
        <button data-k="require_json_answer">Keep only JSON answers (${p.format.answers_json}/${p.count})</button>
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
    $("#clResult", body).textContent = `${r.before} → ${r.after} datapoints; removed ${JSON.stringify(r.removed)}`;
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
async function pageFinetuning() {
  await ensureOverview(true);
  const jobs = await api("/api/jobs");
  const content = layout("finetuning", `<div class="page-head"><h1>Finetuning</h1><div class="actions"><button class="btn primary" id="newJob">+ Create finetuning job</button></div></div><div class="cards">${jobs.map(({ job, experiments }) => {
    const states = experiments.map((e) => e.state);
    const label = !experiments.length ? ["Ready to start experiments", "ready"] : states.includes("running") ? ["Running", "running"] : states.every((s) => s === "pending") ? ["Ready to start experiments", "ready"] : states.some((s) => s === "pending") ? ["Partially run", "pending"] : states.every((s) => s === "succeeded") ? ["Completed", "completed"] : states.some((s) => s === "failed") ? ["Failed", "failed"] : ["Completed", "completed"];
    return `<div class="card clickable" data-job="${esc(job.name)}"><h3>${esc(job.name)}</h3><span class="pill ${label[1]}">${label[0]}</span><div class="sub" style="margin-top:8px">snapshot ${esc(job.snapshot)} · ${experiments.length} experiment${experiments.length === 1 ? "" : "s"}${job.reasoning ? " · reasoning" : ""}</div><div class="foot"><span>Created on: ${fmtDate(job.created)}</span><span class="arrow">→</span></div></div>`;
  }).join("") || `<div class="empty"><b>No finetuning jobs yet</b>Create one from a snapshot.</div>`}</div>`, [{ label: "Finetuning" }]);
  $("#newJob", content).onclick = () => createJobDialog();
  $$("[data-job]", content).forEach((c) => c.onclick = () => go(`#/finetuning/${encodeURIComponent(c.dataset.job)}`));
}

async function pageJob(name) {
  await ensureOverview(true);
  const detail = await api(`/api/jobs/${encodeURIComponent(name)}`);
  const { job, experiments, analysis } = detail;
  const configuring = !experiments.length || experiments.every((e) => e.state === "pending");
  const running = experiments.some((e) => e.state === "running");
  const headPill = running ? ["Running", "running"] : configuring ? ["Ready to start experiments", "ready"] : experiments.every((e) => e.state === "succeeded") ? ["Completed", "completed"] : experiments.some((e) => e.state === "failed") ? ["Failed", "failed"] : ["Partially run", "pending"];
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
    const drawDrafts = () => {
      left.innerHTML = drafts.map((d, i) => `<div class="exp"><div class="head"><span class="dot" style="background:${SERIES[i % SERIES.length]}"></span><h3>Experiment ${i + 1}</h3>${d.number ? `<span class="pill pending">pending · ft-${esc(name)}-${d.number}</span>` : `<span class="pill">draft</span>`}<div class="right">${d.number ? `<button class="btn sm danger" data-rm="${d.number}">Delete</button>` : `<button class="btn sm" data-drop="${i}">Remove</button>`}</div></div>
        ${d.number ? `<div class="kv"><div><div class="k">Base model</div><div class="v mono">${esc(d.base_model)}</div></div><div><div class="k">Training type</div><div class="v">${d.training_type === "lora" ? "LoRA training" : "Full fine-tuning"}</div></div><div><div class="k">Batch size × accumulation</div><div class="v">${d.batch_size} × ${d.gradient_accumulation_steps}</div></div><div><div class="k">Learning rate</div><div class="v">${d.learning_rate}</div></div><div><div class="k">Epochs</div><div class="v">${d.epochs}</div></div><div><div class="k">Max length</div><div class="v">${d.max_length}</div></div></div>`
        : `<div class="grid"><div class="field"><label>Base model</label><input list="catalogList" data-f="base_model" data-i="${i}" value="${esc(d.base_model)}"></div><div class="field"><label>Training type</label><select data-f="training_type" data-i="${i}"><option value="lora" ${d.training_type === "lora" ? "selected" : ""}>LoRA</option><option value="full" ${d.training_type === "full" ? "selected" : ""}>Full fine-tuning</option></select></div><div class="field"><label>Batch size</label><input type="number" min="1" max="64" data-f="batch_size" data-i="${i}" value="${d.batch_size}"></div><div class="field"><label>Learning rate</label><input type="number" step="any" data-f="learning_rate" data-i="${i}" value="${d.learning_rate}"></div><div class="field"><label>Number of epochs</label><input type="number" step="0.5" min="0.5" max="50" data-f="epochs" data-i="${i}" value="${d.epochs}"></div><div class="field"><label>Max length (tokens)</label><input type="number" data-f="max_length" data-i="${i}" value="${d.max_length}"></div><div class="field"><label>Gradient accumulation</label><input type="number" min="1" data-f="gradient_accumulation_steps" data-i="${i}" value="${d.gradient_accumulation_steps}"></div><div class="field"><label>LoRA rank / alpha</label><div class="row"><input type="number" data-f="lora_r" data-i="${i}" value="${d.lora_r}"><input type="number" data-f="lora_alpha" data-i="${i}" value="${d.lora_alpha}"></div></div></div>`}
        </div>`).join("") + `<datalist id="catalogList">${catalog.map((c) => `<option value="${esc(c.id)}">${c.params_b}B · ${esc(c.notes)}</option>`).join("")}</datalist>
        <div class="row"><button class="btn" id="addExp">+ Add experiment</button>${rec ? `<button class="btn" id="addRec">+ Add recommended experiment</button>` : ""}<span class="spacer" style="flex:1"></span><button class="btn primary" id="saveStart" ${drafts.length ? "" : "disabled"}>Save &amp; start experiments</button></div>`;
      const collect = () => $$("[data-f]", left).forEach((el) => { const d = drafts[Number(el.dataset.i)]; d[el.dataset.f] = el.type === "number" ? Number(el.value) : el.value; });
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
    const draftFrom = (option) => ({ base_model: option.base_model, training_type: option.training_type, epochs: option.hyperparameters.epochs, learning_rate: option.hyperparameters.learning_rate, batch_size: option.hyperparameters.batch_size, gradient_accumulation_steps: option.hyperparameters.gradient_accumulation_steps, max_length: option.hyperparameters.max_length, lora_r: option.hyperparameters.lora_r, lora_alpha: option.hyperparameters.lora_alpha, assistant_only_loss: false });
    if (!drafts.length && rec?.recommended) drafts.push(draftFrom(rec.recommended));
    drawDrafts();
    right.innerHTML = analysisPanel(analysis, name);
    wireAnalysis(right, name);
  } else {
    // ---- running / completed: experiment cards + training loss comparison
    left.innerHTML = experiments.map((e, i) => `<div class="exp"><div class="head"><span class="dot" style="background:${SERIES[i % SERIES.length]}"></span><h3>Experiment ${e.number}</h3>${e.state === "succeeded" ? `<a class="btn primary sm" href="#/playground?model=${encodeURIComponent(name + "/" + e.number)}">Test model</a><a class="btn sm icon" title="Download weights" href="/api/jobs/${encodeURIComponent(name)}/experiments/${e.number}/download">⬇</a>` : ""}<div class="right"><span class="pill ${e.state}">${e.state[0].toUpperCase() + e.state.slice(1)}</span>${e.state !== "running" ? `<button class="btn sm danger" data-rm="${e.number}">✕</button>` : ""}</div></div>
      <div class="kv"><div><div class="k">Base model ID</div><div class="v mono">${esc(e.config.base_model)}</div></div><div><div class="k">Batch size</div><div class="v">${e.config.batch_size} × ${e.config.gradient_accumulation_steps}</div></div><div><div class="k">Learning rate</div><div class="v">${e.config.learning_rate}</div></div><div><div class="k">Number of epochs</div><div class="v">${e.config.epochs}</div></div><div><div class="k">${e.config.training_type === "lora" ? "LoRA training" : "Full fine-tuning"}</div><div class="v">${e.config.training_type === "lora" ? `r=${e.config.lora_r} α=${e.config.lora_alpha}` : "all weights"}</div></div><div><div class="k">Result</div><div class="v">${e.state === "succeeded" ? `train loss ${fmtNum(e.train_loss)} · eval loss ${fmtNum(e.metrics?.eval_loss)} · ${e.seconds}s` : e.state === "failed" ? `<span class="error">${esc(e.error || "failed")}</span>` : e.state}</div></div></div>
      ${e.state === "succeeded" ? `<div style="margin-top:10px"><span class="alias">${esc(e.alias)} <button title="copy" data-copy="${esc(e.alias)}">⧉</button></span> <span class="muted" style="font-size:12px">· ${fmtParams(e.total_parameters)} parameters</span></div>` : ""}
      </div>`).join("");
    $$("[data-copy]", left).forEach((b) => b.onclick = () => navigator.clipboard?.writeText(b.dataset.copy).then(() => toast("alias copied")));
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
  return `<div class="panel"><div class="head"><h2>Dataset analysis</h2><div class="right"><button class="btn sm" id="analyze">${a ? "Re-analyze" : "Analyze with the helper LLM"}</button></div></div><div class="body accordion">${a ? [["Task summary", a.task_summary], ["Task characterization", a.task_characterization], ["Knowledge requirements", a.knowledge_requirements], ["Success criteria", a.success_criteria], ["Edge cases", a.edge_cases], ["Datapoints input analysis", a.input_analysis], ["Datapoints output analysis", a.output_analysis]].map(([t, v], i) => `<details ${i === 0 ? "open" : ""}><summary>${t}</summary><div class="inner">${list(v)}</div></details>`).join("") : `<p class="muted">Run the analysis to get a task summary, success criteria, edge cases, and a model recommendation for this snapshot. Needs the helper LLM.</p>`}
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
  container.innerHTML = `<svg viewBox="0 0 ${W} ${H}"><g class="grid">${yT.map((t) => `<line x1="${m.l}" x2="${W - m.r}" y1="${sy(t)}" y2="${sy(t)}"/>`).join("")}</g><g class="axis">${yT.map((t) => `<text x="${m.l - 8}" y="${sy(t) + 4}" text-anchor="end">${t.toFixed(2)}</text>`).join("")}${xT.map((t) => `<text x="${sx(t)}" y="${H - 20}" text-anchor="middle">${t}</text>`).join("")}<text class="axis-title" x="${(W + m.l) / 2}" y="${H - 4}" text-anchor="middle">Training steps</text><text class="axis-title" transform="translate(14 ${(H - m.b) / 2}) rotate(-90)" text-anchor="middle">Training loss</text></g>
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
  await ensureOverview(true);
  const evals = await api("/api/evals");
  const content = layout("evaluations", `<div class="page-head"><h1>Evaluations</h1><div class="actions"><button class="btn primary" id="newEval">+ Create evaluation</button></div></div><div class="cards">${evals.map((e) => `<div class="card clickable" data-ev="${esc(e.name)}"><h3>${esc(e.name)}</h3><span class="pill ${e.state === "succeeded" ? "completed" : e.state}">${e.state === "succeeded" ? "Completed" : e.state}</span><div class="sub" style="margin-top:8px">${e.models.length} model${e.models.length === 1 ? "" : "s"} · ${e.metrics.join(", ")} · ${e.datapoints} datapoints · snapshot ${esc(e.snapshot)}</div><div class="foot"><span>Created on: ${fmtDate(e.created)}</span><span class="arrow">→</span></div></div>`).join("") || `<div class="empty"><b>No evaluations yet</b>Compare your experiments against a base model on the validation split.</div>`}</div>`, [{ label: "Evaluations" }]);
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
  await ensureOverview(true);
  const { summary, rows } = await api(`/api/evals/${encodeURIComponent(name)}`);
  const metrics = summary.metrics, models = summary.models;
  let metric = metrics[0], offset = 0; const limit = 10;
  const byKey = {}; rows.forEach((r) => { byKey[`${r.datapoint}|${r.model}|${r.metric}`] = r; });
  const datapoints = [...new Set(rows.map((r) => r.datapoint))].sort((a, b) => a - b);
  const content = layout("evaluations", `
    <div class="page-head"><div><h1>${esc(name)} <span class="pill ${summary.state === "succeeded" ? "completed" : summary.state}">${summary.state === "succeeded" ? "Completed" : summary.state}</span></h1><div class="meta"><span>snapshot <b>${esc(summary.snapshot)}</b></span><span>· ${models.length} models selected</span><span>· ${summary.datapoints} datapoints</span>${summary.error ? `<span class="error">${esc(summary.error)}</span>` : ""}</div></div><div class="actions"><button class="btn danger" id="delEval">Delete</button></div></div>
    <div class="cards" style="margin-bottom:18px">${(summary.leaderboard || []).map((b) => `<div class="card"><div style="display:flex;align-items:center;gap:10px"><h3 style="flex:1;font-size:14px" class="mono">${esc(b.model)}</h3><span class="pct ${pctClass(b.overall ?? 0)}" style="font-size:15px">${fmtPct(b.overall)}</span></div>${metrics.map((mt) => `<div style="display:flex;justify-content:space-between;margin-top:6px"><span>${esc(mt)}</span><span class="pct ${pctClass(b.scores[mt] ?? 0)}">${fmtPct(b.scores[mt])}</span></div>`).join("")}</div>`).join("")}</div>
    <div class="chips" id="metricTabs" style="margin-bottom:12px">${metrics.map((mt) => `<button data-mt="${esc(mt)}" class="${mt === metric ? "on" : ""}">${esc(mt)}</button>`).join("")}</div>
    <div class="panel" style="margin-bottom:14px"><div class="head"><h2>Score distribution</h2><span class="muted">per model for the selected metric; 10 bins from 0% to 100%</span></div><div class="body" id="scoreHist"></div></div>
    <div class="panel" id="grid"></div>`, [{ label: "Evaluations", href: "#/evaluations" }, { label: name }]);
  const drawHist = () => {
    $("#scoreHist", content).innerHTML = `<div class="cards">${models.map((mo, i) => { const vals = datapoints.map((d) => byKey[`${d}|${mo}|${metric}`]?.score).filter((v) => v !== undefined); const counts = Array(10).fill(0); vals.forEach((v) => counts[Math.min(9, Math.floor(v * 10))]++); return `<div><div class="mono" style="font-size:12px;margin-bottom:4px">${esc(mo)}</div>${histogram({ bins: Array.from({ length: 11 }, (_, k) => k * 10), counts }, SERIES[i % SERIES.length], 320, 80)}</div>`; }).join("")}</div>`;
  };
  $("#delEval", content).onclick = () => confirmModal("Delete evaluation", `Delete evaluation "${name}"?`, async () => { await del(`/api/evals/${encodeURIComponent(name)}`); go("#/evaluations"); });
  const draw = () => {
    const page = datapoints.slice(offset, offset + limit);
    const avg = (model) => { const vals = datapoints.map((d) => byKey[`${d}|${model}|${metric}`]?.score).filter((v) => v !== undefined); return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null; };
    $("#grid", content).innerHTML = `<table><thead><tr><th>Datapoint (user message)</th>${models.map((mo) => `<th class="num mono">${esc(mo)}</th>`).join("")}</tr></thead><tbody>
      <tr class="avg"><td>● Average (${esc(metric)})</td>${models.map((mo) => `<td class="num"><span class="pct ${pctClass(avg(mo) ?? 0)}">${fmtPct(avg(mo))}</span></td>`).join("")}</tr>
      ${page.map((d) => { const any = models.map((mo) => byKey[`${d}|${mo}|${metric}`]).find(Boolean); return `<tr><td class="truncate">${esc(any?.user || `datapoint ${d}`)}</td>${models.map((mo) => { const r = byKey[`${d}|${mo}|${metric}`]; return `<td class="num">${r ? `<span class="pct ${pctClass(r.score)}" style="cursor:pointer" data-cell="${d}|${esc(mo)}">${fmtPct(r.score)}</span>` : "–"}</td>`; }).join("")}</tr>`; }).join("")}
      </tbody></table><div class="pager"><span>Results ${datapoints.length ? offset + 1 : 0} - ${Math.min(offset + limit, datapoints.length)} of ${datapoints.length}</span><span class="spacer"></span><button class="btn sm" id="prev" ${offset === 0 ? "disabled" : ""}>‹</button><span class="cur">${Math.floor(offset / limit) + 1}</span><button class="btn sm" id="next" ${offset + limit >= datapoints.length ? "disabled" : ""}>›</button></div>`;
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
    $$("[data-t]", body).forEach((b) => b.onclick = () => { tab = b.dataset.t; draw(); });
    $$("[data-mo]", body).forEach((b) => b.onclick = () => { current = b.dataset.mo; draw(); });
  };
  draw();
}

// ------------------------------------------------------------------ metrics
async function pageMetrics() {
  await ensureOverview(true);
  const metrics = await api("/api/metrics");
  const content = layout("metrics", `<div class="page-head"><h1>Metrics</h1><div class="actions"><button class="btn primary" id="newMetric">+ Create metric</button></div></div><div class="cards">${metrics.map((me) => `<div class="card clickable" data-me="${esc(me.name)}"><h3>${esc(me.name)}</h3><div class="sub">${esc(me.description || (me.kind === "code" ? `code check: ${me.check}` : me.kind === "webhook" ? `webhook: ${me.webhook.url}` : ""))}</div><div class="foot"><span>${me.kind === "rubric" ? `<span class="pill good">${me.rules.should.length} should</span> <span class="pill bad">${me.rules.should_not.length} should not</span>` : `<span class="pill">${me.kind}</span>`}</span><span class="arrow">→</span></div></div>`).join("")}</div>`, [{ label: "Evaluations", href: "#/evaluations" }, { label: "Metrics" }]);
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
  await ensureOverview(true);
  const models = await api("/api/models");
  const p = state.playground;
  if (preset) { p.model = preset; p.history = []; }
  if (!p.model) p.model = (models.find((m) => m.kind === "fine-tuned") || models[0])?.id || null;
  const content = layout("playground", `<div class="page-head"><h1>Playground</h1><span class="meta">Test base and fine-tuned models before you deploy them.</span></div>
    <div class="play"><div class="panel"><div class="head"><h2>Settings</h2></div><div class="body">
      <div class="field"><label>Model</label><select id="pgModel">${models.map((m) => `<option value="${esc(m.id)}" ${m.id === p.model ? "selected" : ""}>${esc(m.kind === "fine-tuned" ? m.alias : m.id)}${m.status === "up" ? " ●" : ""}</option>`).join("")}</select><div class="hint">● = loaded in memory. Others load on first message.</div></div>
      <div class="field"><label>System prompt</label><textarea id="pgSystem" placeholder="You are a helpful assistant.">${esc(p.system)}</textarea></div>
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
      const response = await fetch("/api/playground/chat", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ model: p.model, messages, temperature: p.temperature, max_tokens: 512 }) });
      if (!response.ok) { let d = response.statusText; try { d = (await response.json()).detail; } catch (_) {} throw new Error(d); }
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
      for (;;) {
        const { value, done } = await reader.read(); if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx; while ((idx = buffer.indexOf("\n\n")) >= 0) {
          const line = buffer.slice(0, idx).trim(); buffer = buffer.slice(idx + 2);
          if (!line.startsWith("data:")) continue;
          const payload = JSON.parse(line.slice(5));
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
  await ensureOverview(true);
  const [models, hubInfo] = await Promise.all([api("/api/models"), api("/api/hub/models")]);
  const content = layout("models", `<div class="page-head"><h1>Models</h1><span class="meta">${models.filter((m) => m.kind === "fine-tuned").length} fine-tuned · ${models.filter((m) => m.kind === "base").length} catalog · ${hubInfo.models.length} in the Hub cache · ${hubInfo.free_disk_gb} GB free</span></div>
    <div class="panel"><div class="head"><h2>Fine-tuned and catalog models</h2></div><table><thead><tr><th>Model</th><th>Kind</th><th>Base model</th><th class="num">Parameters</th><th>Status</th><th>Created</th><th></th></tr></thead><tbody>${models.map((m) => `<tr><td><b class="mono">${esc(m.alias)}</b>${m.kind === "fine-tuned" ? `<div class="muted mono" style="font-size:11px">${esc(m.id)}</div>` : `<div class="muted" style="font-size:12px">${esc(m.notes || "")}</div>`}</td><td><span class="pill ${m.kind === "fine-tuned" ? "info" : ""}">${m.kind}${m.training_type ? " · " + m.training_type : ""}</span></td><td class="mono">${esc(m.base_model)}</td><td class="num">${fmtParams(m.parameters)}</td><td><span class="pill ${m.status}">${m.status}</span></td><td>${fmtDate(m.created)}</td><td style="text-align:right;white-space:nowrap"><button class="btn sm" data-toggle="${esc(m.id)}" data-status="${m.status}">${m.status === "up" ? "Unload" : "Load"}</button> <a class="btn sm" href="#/playground?model=${encodeURIComponent(m.id)}">Test</a>${m.kind === "fine-tuned" ? ` <a class="btn sm icon" title="Download" href="/api/jobs/${encodeURIComponent(m.id.split("/")[0])}/experiments/${m.id.split("/")[1]}/download">⬇</a> <button class="btn sm" data-upload="${esc(m.id)}">Upload to Hub</button>` : ""}</td></tr>`).join("")}</tbody></table></div>
    <div class="panel" style="margin-top:16px"><div class="head"><h2>Hugging Face Hub</h2><span class="muted">token: ${hubInfo.token.configured ? `<span class="pill good">${esc(hubInfo.token.masked)}</span>` : '<span class="pill warn">not set (needed for gated models and uploads)</span>'}</span><div class="right"><input id="hfToken" placeholder="hf_… token" style="width:220px"><button class="btn sm" id="hfSave">Save token</button></div></div>
      <div class="body">
        <div class="row"><div class="field" style="flex:2"><label>Search text-generation models</label><input id="hubQ" placeholder="SmolLM2, Qwen2.5, gemma-3, phi…"></div><div class="field" style="max-width:150px"><label>Max parameters (B)</label><input id="hubMax" type="number" step="0.1" value="2"></div><button class="btn primary" id="hubSearch">Search</button></div>
        <div id="hubResults"></div>
        <h3 style="margin:16px 0 6px">Downloaded (local cache)</h3>
        <table><thead><tr><th>Repository</th><th class="num">Size</th><th>Loadable</th><th>Last used</th><th></th></tr></thead><tbody>${hubInfo.models.map((m) => `<tr><td class="mono">${esc(m.id)}</td><td class="num">${m.size_gb} GB</td><td>${m.transformers_loadable ? '<span class="pill good">transformers</span>' : m.mlx ? '<span class="pill info">MLX (helper LLM only)</span>' : '<span class="pill">other</span>'}</td><td>${m.last_accessed ? fmtDate(new Date(m.last_accessed * 1000).toISOString()) : "–"}</td><td style="text-align:right;white-space:nowrap">${m.transformers_loadable ? `<a class="btn sm" href="#/playground?model=${encodeURIComponent(m.id)}">Test</a> ` : ""}<button class="btn sm danger" data-hubdel="${esc(m.id)}">Remove</button></td></tr>`).join("") || `<tr><td colspan="5" class="muted">Nothing cached yet.</td></tr>`}</tbody></table>
      </div></div>
    <p class="muted" style="margin-top:12px">OpenAI-compatible API: <span class="mono">${location.origin}/v1/chat/completions</span> with <span class="mono">"model": "&lt;alias or id&gt;"</span>. Keys under API Keys gate this endpoint; the Playground is not gated. Any downloaded transformers model can be a base model in a fine-tuning job.</p>`, [{ label: "Models" }]);
  $$("[data-toggle]", content).forEach((b) => b.onclick = guard(async () => { b.disabled = true; b.innerHTML = '<span class="spinner"></span>'; await post(`/api/models/${slug(b.dataset.toggle)}/${b.dataset.status === "up" ? "down" : "up"}`, {}); pageModels(); }));
  $$("[data-upload]", content).forEach((b) => b.onclick = () => {
    const m = modal({ title: `Upload ${b.dataset.upload} to the Hub`, body: `<div class="field"><label>Repository id (user/name)</label><input id="upRepo" placeholder="you/my-tiny-model"></div><label class="radio-card"><input type="checkbox" id="upPrivate" checked><div><b>Private repository</b></div></label><label class="radio-card" style="margin-top:8px"><input type="checkbox" id="upMerge"><div><b>Merge LoRA into the base model first</b><span>Uploads full weights instead of an adapter.</span></div></label>`, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Upload</button>` });
    $("[data-cancel]", m.el).onclick = m.close;
    $("[data-ok]", m.el).onclick = guard(async () => { await post("/api/hub/upload", { kind: "experiment", name: b.dataset.upload, repo_id: $("#upRepo", m.el).value.trim(), private: $("#upPrivate", m.el).checked, merge: $("#upMerge", m.el).checked }); m.close(); showTasks(); pollTasks(); });
  });
  $("#hfSave", content).onclick = guard(async () => { const r = await post("/api/hub/token", { token: $("#hfToken", content).value }); toast(`logged in as ${r.user}`); pageModels(); });
  $$("[data-hubdel]", content).forEach((b) => b.onclick = () => confirmModal("Remove from cache", `Delete the local copy of ${b.dataset.hubdel}? It can be downloaded again.`, async () => { await del(`/api/hub/models/${b.dataset.hubdel}`); pageModels(); }, "Remove"));
  const search = guard(async () => {
    const q = $("#hubQ", content).value.trim(); const max = $("#hubMax", content).value;
    $("#hubResults", content).innerHTML = `<div class="muted"><span class="spinner"></span> searching the Hub…</div>`;
    const rows = await api(`/api/hub/search?q=${encodeURIComponent(q)}&limit=15${max ? `&max_params_b=${max}` : "&max_params_b="}`);
    $("#hubResults", content).innerHTML = rows.length ? `<table><thead><tr><th>Repository</th><th class="num">Params</th><th>License</th><th>Downloads</th><th>Loadable</th><th></th></tr></thead><tbody>${rows.map((r) => `<tr><td><a class="mono" href="${r.url}" target="_blank">${esc(r.id)}</a>${r.gated ? ' <span class="pill warn">gated</span>' : ""}</td><td class="num">${r.params_b ? r.params_b + "B" : "?"}</td><td>${esc(r.license || "?")}</td><td class="num">${(r.downloads || 0).toLocaleString()}</td><td>${r.transformers_loadable ? '<span class="pill good">transformers</span>' : `<span class="pill">${esc(r.format_tags.join(", ") || r.library || "?")}</span>`}</td><td style="text-align:right">${r.transformers_loadable ? `<button class="btn sm primary" data-dl="${esc(r.id)}">Download</button>` : ""}</td></tr>`).join("")}</tbody></table>` : `<div class="muted">No results.</div>`;
    $$("[data-dl]", content).forEach((b) => b.onclick = guard(async () => { await post("/api/hub/download", { repo_id: b.dataset.dl }); showTasks(); pollTasks(); toast(`downloading ${b.dataset.dl}`); }));
  });
  $("#hubSearch", content).onclick = search;
  $("#hubQ", content).onkeydown = (e) => { if (e.key === "Enter") search(); };
}

// ------------------------------------------------------------------ assistant
async function pageAssistant() {
  await ensureOverview(true);
  const st = await api("/api/assistant/status");
  const content = layout("assistant", `<div class="page-head"><h1>Assistant</h1><span class="meta">A frontier model that drives the same ${st.mcp.tools} tools as the MCP server: datasets, cleaning, synthetic data, fine-tuning, evaluation, serving.</span></div>
    <div class="play"><div>
      <div class="panel"><div class="head"><h2>Provider</h2></div><div class="body">
        <div class="field"><label>Model</label><select id="asProvider"><option value="anthropic" ${st.anthropic.available ? "" : "disabled"}>Claude · ${esc(st.anthropic.model)} ${st.anthropic.available ? "" : "(no credentials)"}</option><option value="openai" ${st.openai.available ? "" : "disabled"}>OpenAI · ${esc(st.openai.model || "set TINYSTUDIO_OPENAI_MODEL")} ${st.openai.available ? "" : "(no credentials)"}</option></select></div>
        ${!st.anthropic.available && !st.openai.available ? `<div class="pill warn" style="margin-bottom:10px">No API credentials found</div><p class="muted">Set <span class="mono">ANTHROPIC_API_KEY</span> (or run <span class="mono">ant auth login</span>) and/or <span class="mono">OPENAI_API_KEY</span> + <span class="mono">TINYSTUDIO_OPENAI_MODEL</span> in the shell that starts <span class="mono">tinystudio serve</span>, then restart it. Your keys never leave this machine except for the provider's own API.</p>` : `<p class="muted">Effort ${esc(st.anthropic.effort)} · refusal fallbacks on · tool calls run against this project.</p>`}
        <label class="radio-card"><input type="checkbox" id="asDestructive"><div><b>Allow deletions</b><span>Offer the delete tool to the assistant (it still asks before using it).</span></div></label>
        <button class="btn" id="asReset" style="margin-top:10px">Reset conversation</button>
      </div></div>
      <div class="panel" style="margin-top:16px"><div class="head"><h2>Use from Claude Code, Codex, Claude Desktop</h2></div><div class="body" style="font-size:12.5px">
        <p>tinystudio is an MCP server. Register it once and your coding assistant can run the whole pipeline in natural language:</p>
        <pre class="mono" style="white-space:pre-wrap;background:var(--panel-2);padding:10px;border-radius:8px">claude mcp add --scope user tinystudio -- ${esc(st.mcp.command)}</pre>
        <p class="muted">Codex: add <span class="mono">[mcp_servers.tinystudio]</span> with the same command and args to <span class="mono">~/.codex/config.toml</span>. Claude Desktop: add it under <span class="mono">mcpServers</span> in <span class="mono">claude_desktop_config.json</span>. Then ask, for example: "profile the commands dataset, drop duplicates, snapshot it, and train SmolLM2-360M with LoRA for 3 epochs".</p>
      </div></div>
    </div>
    <div class="panel" style="display:flex;flex-direction:column"><div class="head"><h2>Chat</h2><span class="muted mono" id="asUsage"></span></div><div class="chatlog" id="asLog"></div><div class="composer"><textarea id="asInput" placeholder="e.g. Clean the commands dataset, create a snapshot v2, and start a LoRA experiment on Qwen2.5-0.5B-Instruct"></textarea><button class="btn primary" id="asSend" ${!st.anthropic.available && !st.openai.available ? "disabled" : ""}>Send</button></div></div></div>`, [{ label: "Assistant" }]);
  if (!st.anthropic.available && st.openai.available) $("#asProvider", content).value = "openai";
  const history = state.assistant = state.assistant || [];
  const log = $("#asLog", content);
  const draw = () => { log.innerHTML = history.map((m) => m.role === "user" ? `<div class="bubble user">${esc(m.content)}</div>` : `<div class="bubble assistant" style="font-family:var(--font);font-size:13.5px">${m.html || esc(m.content)}</div>`).join("") || `<div class="empty"><b>Ask for anything in the pipeline</b>The assistant calls tools and shows every call and result here.</div>`; log.scrollTop = log.scrollHeight; };
  draw();
  $("#asReset", content).onclick = () => { history.length = 0; draw(); };
  const send = guard(async () => {
    const text = $("#asInput", content).value.trim(); if (!text) return;
    $("#asInput", content).value = "";
    history.push({ role: "user", content: text });
    const reply = { role: "assistant", content: "", html: "", calls: [] }; history.push(reply); draw();
    $("#asSend", content).disabled = true;
    const render = () => { reply.html = reply.calls.map((c) => `<details class="muted" style="margin:4px 0"><summary><span class="mono">${esc(c.name)}</span>(${esc(JSON.stringify(c.input)).slice(0, 160)}) ${c.output === undefined ? '<span class="spinner"></span>' : ""}</summary><pre class="mono" style="white-space:pre-wrap;max-height:200px;overflow:auto">${esc(c.output || "")}</pre></details>`).join("") + `<div class="msg">${esc(reply.content)}</div>`; draw(); };
    try {
      const response = await fetch("/api/assistant/chat", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ provider: $("#asProvider", content).value, allow_destructive: $("#asDestructive", content).checked, messages: history.slice(0, -1).map((m) => ({ role: m.role, content: m.content })) }) });
      if (!response.ok) { let d = response.statusText; try { d = (await response.json()).detail; } catch (_) {} throw new Error(d); }
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
      for (;;) {
        const { value, done } = await reader.read(); if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx; while ((idx = buffer.indexOf("\n\n")) >= 0) {
          const line = buffer.slice(0, idx).trim(); buffer = buffer.slice(idx + 2);
          if (!line.startsWith("data:")) continue;
          const ev = JSON.parse(line.slice(5));
          if (ev.type === "text") reply.content += ev.delta;
          else if (ev.type === "tool_call") reply.calls.push({ id: ev.id, name: ev.name, input: ev.input });
          else if (ev.type === "tool_result") { const c = reply.calls.find((x) => x.id === ev.id) || reply.calls[reply.calls.length - 1]; if (c) c.output = ev.output; pollTasks(); }
          else if (ev.type === "error") throw new Error(ev.message);
          else if (ev.type === "done") $("#asUsage", content).textContent = `${ev.model} · ${ev.usage.input_tokens} in / ${ev.usage.output_tokens} out`;
          render();
        }
      }
      if (!reply.content && !reply.calls.length) reply.content = "(no reply)";
      render();
    } catch (e) { if (!reply.content && !reply.calls.length) history.pop(); draw(); throw e; } finally { $("#asSend", content).disabled = false; }
  });
  $("#asSend", content).onclick = send;
  $("#asInput", content).onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } };
}

// ------------------------------------------------------------------ traces
async function pageTraces() {
  await ensureOverview(true);
  const traces = await api("/api/traces?limit=200");
  const datasets = state.overview.tree.datasets.filter((d) => !d.missing);
  const content = layout("traces", `<div class="page-head"><h1>Traces</h1><span class="meta">${traces.length} recent inferences</span><div class="actions"><select id="trDs">${datasets.map((d) => `<option>${esc(d.name)}</option>`).join("")}</select><button class="btn" id="trPromote">Add scored ≥ 0.9 to dataset</button></div></div>
    <div class="panel"><table><thead><tr><th>When · model</th><th>User message</th><th>Response</th><th>Score</th><th></th></tr></thead><tbody>${traces.map((t) => { const u = [...t.messages].reverse().find((m) => m.role === "user"); return `<tr><td class="mono muted" style="font-size:11.5px">${esc(t.time?.slice(0, 16).replace("T", " "))}<br>${esc(t.model)}${t.origin === "playground" ? "<br><span class='pill'>playground</span>" : ""}</td><td class="truncate">${esc(u?.content)}</td><td class="truncate mono">${esc(t.response)}${t.corrected ? `<div class="muted">corrected: ${esc(t.corrected)}</div>` : ""}</td><td>${t.score === null || t.score === undefined ? "–" : `<span class="pct ${pctClass(t.score)}">${fmtPct(t.score)}</span>`}</td><td style="white-space:nowrap;text-align:right"><button class="btn sm" data-s="${t.id}|1">👍</button> <button class="btn sm" data-s="${t.id}|0">👎</button> <button class="btn sm" data-fix="${t.id}">Fix</button> <button class="btn sm danger" data-del="${t.id}">✕</button></td></tr>`; }).join("") || `<tr><td colspan="5" class="empty">No traces yet. Chat in the Playground or call the API.</td></tr>`}</tbody></table></div>`, [{ label: "Traces" }]);
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

// ------------------------------------------------------------------ stats
async function pageStats() {
  await ensureOverview(true);
  const s = await api("/api/stats");
  const tile = (k, v) => `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div></div>`;
  const max = Math.max(1, ...s.requests_per_day.map(([, n]) => n));
  layout("stats", `<div class="page-head"><h1>Stats</h1></div>
    <div class="tiles">${tile("Datasets", s.datasets)}${tile("Datapoints", s.datapoints)}${tile("Snapshots", s.snapshots)}${tile("Jobs", s.jobs)}${tile("Experiments succeeded", s.experiments.succeeded || 0)}${tile("Training time", `${Math.round(s.training_seconds / 60)} min`)}${tile("Evaluations", s.evaluations)}${tile("Requests served", s.requests)}${tile("Tokens served", s.prompt_tokens + s.completion_tokens)}${tile("Scored traces", s.scored_traces)}</div>
    <div class="two"><div class="panel"><div class="head"><h2>Requests per day</h2></div><div class="body">${s.requests_per_day.length ? `<div class="bars">${s.requests_per_day.map(([d, n]) => `<div title="${d}: ${n}" style="height:${Math.max(4, (n / max) * 100)}%"></div>`).join("")}</div><div class="muted" style="display:flex;justify-content:space-between;font-size:11px;margin-top:4px"><span>${s.requests_per_day[0][0]}</span><span>${s.requests_per_day[s.requests_per_day.length - 1][0]}</span></div>` : `<div class="empty">No requests yet.</div>`}</div></div>
    <div class="panel"><div class="head"><h2>Requests per model</h2></div><div class="body"><table>${s.requests_per_model.map(([m, n]) => `<tr><td class="mono">${esc(m)}</td><td class="num">${n}</td></tr>`).join("") || `<tr><td class="muted">none</td></tr>`}</table><p class="muted" style="margin-top:10px">Loaded now: ${s.models_loaded.map(esc).join(", ") || "none"}</p></div></div></div>`, [{ label: "Stats" }]);
}

// ------------------------------------------------------------------ api keys
async function pageKeys() {
  await ensureOverview(true);
  const keys = await api("/api/keys");
  const content = layout("keys", `<div class="page-head"><h1>API Keys</h1><div class="actions"><button class="btn primary" id="newKey">+ Create API key</button></div></div>
    <div class="panel"><table><thead><tr><th>Name</th><th>Prefix</th><th>Created</th><th>Last used</th><th></th></tr></thead><tbody>${keys.map((k) => `<tr><td><b>${esc(k.name)}</b></td><td class="mono">${esc(k.prefix)}…</td><td>${fmtDate(k.created)}</td><td>${fmtDate(k.last_used)}</td><td style="text-align:right"><button class="btn sm danger" data-rev="${k.id}">Revoke</button></td></tr>`).join("") || `<tr><td colspan="5" class="empty">No keys. The /v1 endpoint is open on localhost until you create one.</td></tr>`}</tbody></table></div>
    <div class="panel" style="margin-top:16px"><div class="head"><h2>Using your key</h2></div><div class="body"><pre class="mono" style="white-space:pre-wrap">curl ${location.origin}/v1/chat/completions \\\n  -H "Authorization: Bearer ts-..." -H "content-type: application/json" \\\n  -d '{"model": "ft-&lt;job&gt;-&lt;n&gt;", "messages": [{"role": "user", "content": "hello"}]}'</pre><p class="muted">Works with the OpenAI SDKs: set <span class="mono">base_url</span> to <span class="mono">${location.origin}/v1</span>.</p></div></div>`, [{ label: "API Keys" }]);
  $("#newKey", content).onclick = () => {
    const m = modal({ title: "Create API key", body: `<div class="field"><label>Name</label><input id="kName" placeholder="my-app"></div>`, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Create</button>` });
    $("[data-cancel]", m.el).onclick = m.close;
    $("[data-ok]", m.el).onclick = guard(async () => {
      const r = await post("/api/keys", { name: $("#kName", m.el).value });
      m.close();
      modal({ title: "Your new API key", body: `<p>Copy it now; it is shown only once.</p><pre class="mono" style="background:var(--panel-2);padding:12px;border-radius:8px;word-break:break-all">${esc(r.key)}</pre>`, footer: `<button class="btn primary" data-done>Done</button>`, onMount: (el, close) => { $("[data-done]", el).onclick = () => { close(); pageKeys(); }; } });
    });
  };
  $$("[data-rev]", content).forEach((b) => b.onclick = () => confirmModal("Revoke key", "Revoke this key? Clients using it will get 401.", async () => { await del(`/api/keys/${b.dataset.rev}`); pageKeys(); }, "Revoke"));
}

// ------------------------------------------------------------------ tasks drawer
function showTasks() { $("#tasks").hidden = false; }
async function pollTasks() {
  let tasks;
  try { tasks = await api("/api/tasks"); } catch (_) { return; }
  const wasRunning = new Set(state.tasks.filter((t) => t.state === "running").map((t) => t.id));
  state.tasks = tasks;
  const box = $("#tasks");
  box.innerHTML = `<div class="head"><h2>Tasks</h2><div class="right"><button class="btn sm" id="tHide">Hide</button></div></div>` + (tasks.length ? tasks.slice(0, 6).map((t) => `<div class="task"><span class="pill ${t.state}">${t.state}</span> <b>${esc(t.kind)}</b> <span class="muted mono" style="font-size:11px">${esc(t.started.slice(11, 19))}</span>${t.state === "running" ? ` <button class="btn sm" style="float:right" data-stop="${t.id}">Stop</button>` : ""}<div class="mono muted" style="font-size:11px;word-break:break-all;margin-top:4px">tinystudio ${esc(t.command)}</div><pre>${esc(t.log_tail.join("\n"))}</pre></div>`).join("") : `<div class="task muted">Nothing running.</div>`);
  $("#tHide", box).onclick = () => box.hidden = true;
  $$("[data-stop]", box).forEach((b) => b.onclick = guard(async () => { await post(`/api/tasks/${b.dataset.stop}/stop`, {}); pollTasks(); }));
  const running = tasks.some((t) => t.state === "running");
  if (tasks.some((t) => wasRunning.has(t.id) && t.state !== "running")) { toast("a task finished"); render(); }
  clearTimeout(state.taskTimer);
  if (running) { showTasks(); state.taskTimer = setTimeout(pollTasks, 2500); }
}

// ------------------------------------------------------------------ boot
guard(async () => { await ensureOverview(); await render(); pollTasks(); })();
