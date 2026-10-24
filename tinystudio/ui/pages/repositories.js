import { $, $$ } from "../ui/dom.js?v=82e74d4b5ecb";
import { esc } from "../ui/format.js?v=82e74d4b5ecb";

// Owns this feature's page rendering and dialogs; application services stay at explicit boundaries.
export function createRepositoriesPages({ state, http, feedback, dialogs, shell, context, ensureOverview, showTasks, pollTasks }) {
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

  return { pageRepositories };
}
