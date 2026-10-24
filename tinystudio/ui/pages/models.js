import { $, $$ } from "../ui/dom.js?v=82e74d4b5ecb";
import { esc, fmtDate, fmtParams, slug } from "../ui/format.js?v=82e74d4b5ecb";

// Owns this feature's page rendering and dialogs; application services stay at explicit boundaries.
export function createModelsPages({ state, http, feedback, dialogs, shell, context, ensureOverview, showTasks, pollTasks }) {
  const api = (...args) => http.api(...args);
  const post = (...args) => http.post(...args);
  const del = (...args) => http.del(...args);
  const toast = (...args) => feedback.toast(...args);
  const guard = (...args) => feedback.guard(...args);
  const modal = (...args) => dialogs.modal(...args);
  const confirmModal = (...args) => dialogs.confirmModal(...args);
  const layout = (...args) => shell.layout(...args);
  const dataContext = (...args) => context.dataContext(...args);
  const assertRender = (...args) => context.assertRender(...args);

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

  return { pageModels };
}
