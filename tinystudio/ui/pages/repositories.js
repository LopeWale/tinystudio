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
    const content = layout("repositories", `<div class="page-head page-intro"><div class="page-heading"><span class="page-eyebrow">Knowledge workspace</span><h1>Repositories</h1><p class="page-description">Build searchable document collections to ground answers in your own knowledge.</p><span class="meta">Use indexed collections in the Playground or with the API.</span></div><div class="actions"><button class="btn primary" id="newRepo">+ New repository</button></div></div>
      <div class="cards resource-grid">${repos.map((r) => `<div ${dataContext("Repository", r)} class="card resource-card" data-repo="${esc(r.name)}"><h3>${esc(r.name)}</h3><div class="sub">${esc(r.description || "")}</div><div class="sub resource-meta">${r.documents.length} document${r.documents.length === 1 ? "" : "s"} · ${r.chunk_count} chunks · <span class="mono">${esc(r.embedding_model)}</span></div>
        <div class="page-toolbar"><button class="btn sm primary" data-add="${esc(r.name)}">Add documents</button><button class="btn sm" data-query="${esc(r.name)}">Query</button><button class="btn sm danger" data-del="${esc(r.name)}">Delete</button></div>
        ${r.documents.length ? `<div class="table-wrap"><table aria-label="Documents in ${esc(r.name)}"><thead><tr><th scope="col">Document</th><th scope="col">Format</th><th scope="col" class="num">Chunks</th><th scope="col"><span class="visually-hidden">Actions</span></th></tr></thead><tbody>${r.documents.map((d) => `<tr ${dataContext("Repository document", d)}><td>${esc(d.name)}</td><td class="muted">${esc(d.kind)}</td><td class="num">${d.chunks} chunks</td><td><button class="btn sm danger" aria-label="Remove ${esc(d.name)}" data-deldoc="${esc(r.name)}|${esc(d.name)}">Remove</button></td></tr>`).join("")}</tbody></table></div>` : `<p class="muted">No documents indexed yet. Add files or URLs to make this collection searchable.</p>`}
      </div>`).join("") || `<div class="empty empty-state"><div class="empty-copy"><b>No repositories yet</b><p>Create a repository, add documents or URLs, then use the indexed knowledge in the Playground.</p></div></div>`}</div>`, [{ label: "Repositories" }]);
    $("#newRepo", content).onclick = guard(async () => {
      let cached = [];
      try { cached = (await api("/api/hub/models")).models.map((m) => m.id); } catch (_) {}
      const m = modal({ title: "New repository", body: `<div class="field"><label for="rpName">Name</label><input id="rpName" placeholder="manuals"></div><div class="field"><label for="rpDesc">Description</label><input id="rpDesc"></div><div class="field"><label for="rpModel">Embedding model (any Hugging Face feature-extraction model; search the Models page with task "embeddings")</label><input id="rpModel" list="rpModels" value="sentence-transformers/all-MiniLM-L6-v2"><datalist id="rpModels">${cached.map((id) => `<option value="${esc(id)}">`).join("")}</datalist></div>`, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Create</button>` });
      $("[data-cancel]", m.el).onclick = m.close;
      $("[data-ok]", m.el).onclick = guard(async () => { const name = $("#rpName", m.el).value.trim(); if (!name) throw new Error("give the repository a name"); await post("/api/repositories", { name, description: $("#rpDesc", m.el).value, embedding_model: $("#rpModel", m.el).value.trim() || null }); m.close(); pageRepositories(); });
    });
    $$("[data-add]", content).forEach((b) => b.onclick = () => {
      const name = b.dataset.add;
      const m = modal({ title: `Add documents to ${name}`, body: `<div class="field"><label for="rpFiles">Files (txt, md, html, pdf, docx, pptx)</label><input id="rpFiles" type="file" multiple></div><div class="field"><label for="rpUrls">URLs (web pages or YouTube videos, one per line)</label><textarea id="rpUrls" placeholder="https://…"></textarea></div><p class="muted">Text is extracted, split into ~1,200-character chunks, embedded with the repository's model, and indexed as a background task.</p>`, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Index</button>` });
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
      const m = modal({ title: `Query ${name}`, wide: true, body: `<div class="row"><div class="field" style="flex:3"><label for="rpQ">Text</label><input id="rpQ" placeholder="how do I reset the thermostat?"></div><div class="field"><label for="rpLimit">Passages</label><input id="rpLimit" type="number" min="1" max="50" value="5"></div><div class="field"><label for="rpThr">Min similarity</label><input id="rpThr" type="number" min="-1" max="1" step="0.05" value="0"></div></div><div id="rpResults" aria-live="polite" class="muted">Search for a question or phrase to inspect the most relevant passages.</div>`, footer: `<button class="btn" data-close>Close</button><button class="btn primary" data-ok>Search</button>` });
      $("[data-close]", m.el).onclick = m.close;
      const run = guard(async () => { const rows = await post(`/api/repositories/${encodeURIComponent(name)}/query`, { text: $("#rpQ", m.el).value, limit: Number($("#rpLimit", m.el).value) || 5, similarity_threshold: Number($("#rpThr", m.el).value) || 0 }); $("#rpResults", m.el).innerHTML = rows.length ? `<div class="table-wrap"><table aria-label="Repository search results"><thead><tr><th scope="col">Similarity</th><th scope="col">Document</th><th scope="col">Passage</th></tr></thead><tbody>${rows.map((r) => `<tr ${dataContext("Result", r)}><td class="num mono">${r.similarity_score.toFixed(3)}</td><td class="muted">${esc(r.document)}</td><td style="white-space:pre-wrap">${esc(r.text.slice(0, 600))}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty empty-state"><div class="empty-copy"><b>No matching passages</b><p>Try a different search or lower the minimum similarity.</p></div></div>`; });
      $("[data-ok]", m.el).onclick = run;
      $("#rpQ", m.el).onkeydown = (e) => { if (e.key === "Enter") run(); };
    });
    $$("[data-del]", content).forEach((b) => b.onclick = () => confirmModal("Delete repository", `Delete repository "${b.dataset.del}" and its index?`, async () => { await del(`/api/repositories/${encodeURIComponent(b.dataset.del)}`); pageRepositories(); }));
    $$("[data-deldoc]", content).forEach((b) => b.onclick = () => { const [repo, doc] = b.dataset.deldoc.split("|"); confirmModal("Remove document", `Remove "${doc}" from ${repo}?`, async () => { await del(`/api/repositories/${encodeURIComponent(repo)}/documents/${encodeURIComponent(doc)}`); pageRepositories(); }, "Remove"); });
  }

  return { pageRepositories };
}
