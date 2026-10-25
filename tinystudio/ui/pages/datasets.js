import { $, $$ } from "../ui/dom.js?v=82e74d4b5ecb";
import { esc, fmtDate } from "../ui/format.js?v=82e74d4b5ecb";
import { histogram as drawHistogram } from "../ui/charts.js?v=82e74d4b5ecb";

// Owns this feature's page rendering and dialogs; application services stay at explicit boundaries.
export function createDatasetsPages({ state, http, feedback, dialogs, shell, navigation, context, ensureOverview, showTasks, pollTasks, queued, createJobDialog }) {
  const api = (...args) => http.api(...args);
  const post = (...args) => http.post(...args);
  const put = (...args) => http.put(...args);
  const del = (...args) => http.del(...args);
  const scoped = (...args) => http.scoped(...args);
  const toast = (...args) => feedback.toast(...args);
  const guard = (...args) => feedback.guard(...args);
  const modal = (...args) => dialogs.modal(...args);
  const confirmModal = (...args) => dialogs.confirmModal(...args);
  const layout = (...args) => shell.layout(...args);
  const go = (...args) => navigation.go(...args);
  const dataContext = (...args) => context.dataContext(...args);
  const assertRender = (...args) => context.assertRender(...args);
  const histogram = (h, color, width, height) => drawHistogram(h, color, width, height, dataContext);

  async function pageDatasets() {
    const view = state.pageContext;
    await ensureOverview(true); assertRender(view);
    const datasets = await api("/api/datasets");
    assertRender(view);
    const content = layout("datasets", `
      <div class="page-head page-intro"><div class="page-heading"><span class="page-eyebrow">Data workspace</span><h1>Datasets</h1><p class="page-description">Prepare, review, and snapshot the data your models learn from.</p><span class="meta">${datasets.length} dataset${datasets.length === 1 ? "" : "s"}</span></div><div class="actions"><button class="btn primary" id="newDataset">+ Create Dataset</button></div></div>
      <div class="filter-bar"><label class="search-field" for="dsSearch"><span>Find a dataset</span><input id="dsSearch" type="search" placeholder="Search datasets by name"></label></div><div class="cards resource-grid" id="dsCards" aria-live="polite"></div>`, [{ label: "Datasets" }]);
    const draw = (q = "") => {
      const rows = datasets.filter((d) => d.name.toLowerCase().includes(q.trim().toLowerCase()));
      $("#dsCards", content).innerHTML = rows.length ? rows.map((d) => `<a ${dataContext("Dataset", d)} class="card resource-card clickable" data-ds="${esc(d.name)}" href="#/datasets/${encodeURIComponent(d.name)}"><h3>${esc(d.name)}</h3><div class="sub">${d.datapoints} datapoints · ${d.snapshot_count} snapshot${d.snapshot_count === 1 ? "" : "s"}${d.origin?.kind === "synthetic" ? ' · <span class="pill synthetic">Synthetic</span>' : ""}</div><div class="foot"><span>Created on: ${fmtDate(d.created)}</span><span class="arrow" aria-hidden="true">→</span></div></a>`).join("") : `<div class="empty empty-state"><div class="empty-copy"><b>${q.trim() ? "No matching datasets" : "No datasets yet"}</b><p>${q.trim() ? "Try another name or clear the search to see all datasets." : "Upload a JSONL file or generate examples from your documents to get started."}</p></div></div>`;
      $$("[data-ds]", content).forEach((c) => c.onclick = (event) => { if (event && (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)) return; if (event) event.preventDefault(); go(`#/datasets/${encodeURIComponent(c.dataset.ds)}`); });
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
          <div class="field"><label for="wName">Dataset name</label><input id="wName" value="${esc(body.dataset.name || "")}" placeholder="support-faq"></div>
          ${mode === "upload" ? `<div class="field"><label>JSONL file</label><div class="dropzone" id="drop" role="button" tabindex="0" aria-label="Choose source files">Drop a .jsonl file here or click to choose<br><small>one {"messages": [...]} object per line with system (optional), user, and assistant turns</small></div><div id="fileList" class="muted"></div></div>`
          : `<div class="row"><div class="field" style="flex:2"><label>Files</label><div class="dropzone" id="drop" role="button" tabindex="0" aria-label="Choose source files">Drop files (PDF, TXT, MD, HTML) or click to choose</div><div id="fileList" class="muted"></div></div><div class="field" style="flex:2"><label for="wUrls">Web page URLs (one per line)</label><textarea id="wUrls">${esc(body.dataset.urls || "")}</textarea></div></div>
             <div class="row"><div class="field"><label for="wPairs">Pairs per source</label><input id="wPairs" type="number" min="1" max="500" value="${body.dataset.pairs || 25}"></div><div class="field"><label for="wSystem">System prompt (optional, prepended to every datapoint)</label><input id="wSystem" value="${esc(body.dataset.system || "")}"></div></div>`}
          <div class="mfoot" style="padding:12px 0 0;border:0"><button class="btn" data-cancel>Cancel</button><button class="btn primary" data-next>${mode === "upload" ? "Create dataset" : "Next →"}</button></div>`;
        $$("[data-mode]", body).forEach((b) => b.onclick = () => { mode = b.dataset.mode; draw(); });
        const input = document.createElement("input"); input.type = "file"; input.multiple = mode !== "upload"; input.accept = mode === "upload" ? ".jsonl,.json" : ".pdf,.txt,.md,.markdown,.html,.htm";
        const listFiles = () => $("#fileList", body).textContent = files.map((f) => `${f.name} (${Math.round(f.size / 1024)} KB)`).join(", ");
        const drop = $("#drop", body);
        drop.onclick = () => input.click();
        drop.onkeydown = (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); input.click(); } };
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
          <div class="field"><label for="wRules">Rules &amp; constraints (one per line)</label><textarea id="wRules" placeholder="- write questions a customer would ask\n- answers must cite the policy name">${esc(body.dataset.rules || "")}</textarea></div>
          <div class="two"><div class="field"><label for="wQ">Question guidance</label><textarea id="wQ" placeholder="Ask a practical question about {EXTRACTED_TEXT}">${esc(body.dataset.q || "")}</textarea></div><div class="field"><label for="wA">Answer format</label><textarea id="wA" placeholder='Plain prose, or a JSON template such as {"answer": "...", "source": "..."}'>${esc(body.dataset.a || "")}</textarea></div></div>
          <div class="two"><div class="field"><label for="wEQ">Example questions (one per line)</label><textarea id="wEQ">${esc(body.dataset.eq || "")}</textarea></div><div class="field"><label for="wEA">Example answers (same order)</label><textarea id="wEA">${esc(body.dataset.ea || "")}</textarea></div></div>
          <div class="row"><div class="field"><label for="wTemp">Creativity level <span id="wTempV">${body.dataset.temp || 0.3}</span></label><input id="wTemp" type="range" min="0" max="1" step="0.01" value="${body.dataset.temp || 0.3}"></div><div class="field"><label for="wCurate">Curate: keep pairs rated at least (1–10, 0 = keep all)</label><input id="wCurate" type="number" min="0" max="10" step="0.5" value="${body.dataset.curate || 0}"></div></div>
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
      <div class="page-head page-intro"><div class="page-heading"><span class="page-eyebrow">Dataset workspace</span><h1>${esc(name)}</h1><div class="meta">Created on: ${fmtDate(info?.created)} ${info?.origin?.kind === "synthetic" ? '· <span class="pill synthetic">Synthetic</span>' : ""}</div></div>
        <div class="actions">
          <button class="btn primary" id="btnFinetune">Start finetuning</button>
          <button class="btn" id="btnEnrich">Enrich</button>
          <button class="btn" id="btnSnapshots">Snapshots</button>
          <button class="btn" id="btnSplit">Autosplit</button>
          <button class="btn" id="btnLabels">Labels</button>
          <button class="btn" id="btnQuality">Quality</button>
          <a class="btn" href="${scoped(`/api/datasets/${encodeURIComponent(name)}/download`)}" aria-label="Download dataset">Download</a>
          <button class="btn" id="btnAdd">Add datapoint</button>
          <button class="btn danger" id="btnDelete">Delete</button>
        </div></div>
      ${snaps.length ? "" : `<div class="pill info" style="margin-bottom:12px">No snapshots yet. Create one to run finetuning and evaluation.</div>`}
      <div class="tiles" id="tiles"></div>
      <div class="panel"><div class="head"><h2>Datapoints</h2><span class="muted" id="dpCount"></span><div class="right"><label class="field-label" for="bucketFilter">Bucket</label><select id="bucketFilter"><option value="">All buckets</option><option>uncategorized</option><option>training</option><option>validation</option></select></div></div><div id="dpTable"></div></div>`,
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
      $("#dpTable", content).innerHTML = `<div class="table-wrap"><table aria-label="Dataset records"><thead><tr><th scope="col">#</th><th scope="col">User message</th><th scope="col">Assistant response</th><th scope="col">Bucket</th><th scope="col">Labels</th><th scope="col">Data type</th></tr></thead><tbody>${page.map((r) => {
        const user = [...r.messages].reverse().find((m) => m.role === "user")?.content || "";
        return `<tr ${dataContext("Datapoint", r)} class="clickable" tabindex="0" role="button" aria-label="Edit datapoint ${r.index + 1}" data-i="${r.index}"><td class="mono muted">${r.index + 1}</td><td class="truncate">${esc(user)}</td><td class="truncate mono">${esc(r.messages[r.messages.length - 1].content)}</td><td><span class="pill">${r.bucket}</span></td><td class="muted">${esc((r.labels || []).filter((l) => l !== "synthetic" && l !== "augmented").join(", "))}</td><td>${r.source?.startsWith("synthetic") ? '<span class="pill synthetic">Synthetic</span>' : r.source?.startsWith("trace") ? '<span class="pill info">Trace</span>' : '<span class="pill">Uploaded</span>'}</td></tr>`;
      }).join("") || `<tr><td colspan="6" class="empty">No datapoints in this bucket.</td></tr>`}</tbody></table></div>
      <div class="pager"><span>Results ${rows.length ? offset + 1 : 0} - ${Math.min(offset + limit, rows.length)} of ${rows.length}</span><span class="spacer"></span><button class="btn sm" id="prev" aria-label="Previous page" ${offset === 0 ? "disabled" : ""}>‹</button><span class="cur">${Math.floor(offset / limit) + 1}</span><button class="btn sm" id="next" aria-label="Next page" ${offset + limit >= rows.length ? "disabled" : ""}>›</button></div>`;
      $("#prev", content).onclick = () => { offset -= limit; load(); };
      $("#next", content).onclick = () => { offset += limit; load(); };
      $$("tr[data-i]", content).forEach((tr) => { tr.onclick = () => showDatapoint(d.rows[Number(tr.dataset.i)], Number(tr.dataset.i)); tr.onkeydown = (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); tr.onclick(); } }; });
    });
    $("#bucketFilter", content).onchange = (e) => { filter = e.target.value; offset = 0; load(); };
    load();

    $("#btnFinetune", content).onclick = () => snaps.length ? createJobDialog(snaps[snaps.length - 1].name) : toast("create a snapshot first", true);
    $("#btnEnrich", content).onclick = () => {
      const m = modal({ title: "Enrich dataset", body: `<p class="muted">Paraphrase existing user turns with the helper model so the model sees more ways of asking. Split before you enrich if you want a clean validation set.</p><div class="field"><label for="enCount">Datapoints to generate</label><input id="enCount" type="number" min="1" max="2000" value="50"></div><div class="field"><label for="enTemp">Creativity level <span class="muted" id="enTempVal">0.8</span></label><input id="enTemp" type="range" min="0" max="1.5" step="0.1" value="0.8"></div><div class="field"><label for="enMore">Positive instructions (generate more of…)</label><input id="enMore" placeholder="terse voice commands, room names, numbers spelled out"></div><div class="field"><label for="enAvoid">Negative instructions (avoid…)</label><input id="enAvoid" placeholder="questions, chit-chat, requests outside the home"></div>`, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Enrich</button>` });
      $("#enTemp", m.el).oninput = () => { $("#enTempVal", m.el).textContent = $("#enTemp", m.el).value; };
      $("[data-cancel]", m.el).onclick = m.close;
      $("[data-ok]", m.el).onclick = guard(async () => { await post("/api/augment", { dataset: name, count: Number($("#enCount", m.el).value), temperature: Number($("#enTemp", m.el).value), instructions: $("#enMore", m.el).value, avoid: $("#enAvoid", m.el).value }); m.close(); showTasks(); pollTasks(); });
    };
    $("#btnSplit", content).onclick = () => {
      const m = modal({ title: "Autosplit dataset", body: `<p class="muted">Assign every uncategorized datapoint to training or validation. Existing assignments are kept.</p><div class="field"><label for="sp">Training percentage: <b id="spV">80</b>%</label><input id="sp" type="range" min="1" max="99" value="80"></div><label class="radio-card" id="synthCard"><input type="checkbox" id="allowSynth"><div><b>Allow synthetic data in validation</b><span>Off keeps generated rows out of the validation split so the score reflects real data.</span></div></label>`, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Split</button>` });
      $("#sp", m.el).oninput = (e) => $("#spV", m.el).textContent = e.target.value;
      $("[data-cancel]", m.el).onclick = m.close;
      $("[data-ok]", m.el).onclick = guard(async () => { const meta = await post(`/api/datasets/${encodeURIComponent(name)}/split`, { train_pct: Number($("#sp", m.el).value), synthetic_in_validation: $("#allowSynth", m.el).checked }); m.close(); if (queued(meta, "the split")) return; toast(`training ${meta.buckets.training} · validation ${meta.buckets.validation}`); load(); });
    };
    $("#btnSnapshots", content).onclick = () => snapshotsDialog(name, snaps, () => { pageDataset(name); });
    $("#btnLabels", content).onclick = () => labelsDialog(name, info?.labels || [], load);
    $("#btnQuality", content).onclick = () => qualityDialog(name, () => { ensureOverview(true).then(() => pageDataset(name)); });
    $("#btnAdd", content).onclick = () => {
      const m = modal({ title: "Add datapoint", body: `<div class="field"><label for="dpS">System (optional)</label><textarea id="dpS"></textarea></div><div class="field"><label for="dpU">User</label><textarea id="dpU"></textarea></div><div class="field"><label for="dpA">Assistant</label><textarea id="dpA"></textarea></div><div class="field"><label for="dpB">Bucket</label><select id="dpB"><option>uncategorized</option><option>training</option><option>validation</option></select></div>`, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Add</button>` });
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
      <div class="table-wrap"><table aria-label="Dataset records"><thead><tr><th scope="col">Name</th><th scope="col">Training</th><th scope="col">Validation</th><th scope="col">Created</th><th scope="col"><span class="visually-hidden">Actions</span></th></tr></thead><tbody>${snaps.map((s) => `<tr ${dataContext("Snapshot", s)}><td><b>${esc(s.name)}</b></td><td>${s.training_count}</td><td>${s.validation_count}</td><td>${fmtDate(s.created)}</td><td style="text-align:right"><a class="btn sm" href="${scoped(`/api/snapshots/${encodeURIComponent(s.name)}/download`)}">⬇ Download</a> <button class="btn sm danger" data-del="${esc(s.name)}">Delete</button></td></tr>`).join("") || `<tr><td colspan="5" class="muted">None yet.</td></tr>`}</tbody></table></div>
      <div class="row" style="margin-top:14px"><div class="field"><label for="snName">New snapshot name</label><input id="snName" placeholder="${esc(dataset)}-v${snaps.length + 1}"></div><button class="btn primary" id="snCreate">Create snapshot</button></div>`, footer: `<button class="btn" data-close>Close</button>` });
    $("[data-close]", m.el).onclick = m.close;
    $("#snCreate", m.el).onclick = guard(async () => { const nm = $("#snName", m.el).value.trim() || `${dataset}-v${snaps.length + 1}`; const s = await post("/api/snapshots", { dataset, name: nm }); m.close(); if (queued(s, `snapshot ${nm}`)) return; toast(`snapshot ${s.name}: ${s.training_count} / ${s.validation_count}`); after(); });
    $$("[data-del]", m.el).forEach((b) => b.onclick = () => confirmModal("Delete snapshot", `Delete snapshot "${b.dataset.del}"? Jobs that used it keep their results.`, async () => { await del(`/api/snapshots/${encodeURIComponent(b.dataset.del)}`); m.close(); after(); }));
  }
  function labelsDialog(dataset, labels, after) {
    const rows = labels.length ? labels.map((l) => ({ ...l })) : [{ name: "", description: "" }];
    const body = document.createElement("div");
    const m = modal({ title: "Labels", body, footer: `<button class="btn left" id="autoLabel">Auto-label with the helper LLM</button><button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Save labels</button>` });
    const draw = () => {
      body.innerHTML = `<p class="muted">Define a label vocabulary, then let the helper model assign one label per datapoint. Example: excellent, good, fair, poor.</p>` + rows.map((r, i) => `<div class="row"><div class="field" style="flex:1"><label>Name</label><input aria-label="Label ${i + 1} name" data-n="${i}" value="${esc(r.name)}"></div><div class="field" style="flex:2"><label>Description</label><input aria-label="Label ${i + 1} description" data-d="${i}" value="${esc(r.description)}"></div><button class="btn icon" aria-label="Remove label ${i + 1}" data-x="${i}">✕</button></div>`).join("") + `<button class="btn sm" id="addRow">+ Add label</button>`;
      $("#addRow", body).onclick = () => { collect(); rows.push({ name: "", description: "" }); draw(); };
      $$("[data-x]", body).forEach((b) => b.onclick = () => { collect(); rows.splice(Number(b.dataset.x), 1); draw(); });
    };
    const collect = () => rows.forEach((r, i) => { r.name = $(`[data-n="${i}"]`, body)?.value ?? r.name; r.description = $(`[data-d="${i}"]`, body)?.value ?? r.description; });
    draw();
    $("[data-cancel]", m.el).onclick = m.close;
    $("[data-ok]", m.el).onclick = guard(async () => { collect(); await put(`/api/datasets/${encodeURIComponent(dataset)}/labels`, { labels: rows.filter((r) => r.name.trim()) }); toast("labels saved"); m.close(); await ensureOverview(true); after(); });
    $("#autoLabel", m.el).onclick = guard(async () => { collect(); await put(`/api/datasets/${encodeURIComponent(dataset)}/labels`, { labels: rows.filter((r) => r.name.trim()) }); await post(`/api/datasets/${encodeURIComponent(dataset)}/auto-label`, {}); m.close(); showTasks(); pollTasks(); });
  }

  async function qualityDialog(dataset, after) {
    const body = document.createElement("div");
    body.innerHTML = `<div class="muted"><span class="spinner"></span> profiling…</div>`;
    const m = modal({ title: `Data quality · ${dataset}`, body, wide: true, footer: `<button class="btn" data-close>Close</button>` });
    $("[data-close]", m.el).onclick = m.close;
    const p = await api(`/api/datasets/${encodeURIComponent(dataset)}/quality`);
    const stat = (k, v) => `<div ${dataContext(k, {label:k,value:v})} class="tile"><div class="k">${k}</div><div class="v">${v}</div></div>`;
    const dist = (pairs) => pairs.length ? `<div class="table-wrap"><table aria-label="Dataset records">${pairs.map(([k, v]) => `<tr ${dataContext("Distribution entry", {label:k,value:v})}><td>${esc(k)}</td><td class="num">${v}</td><td style="width:40%"><div style="height:8px;border-radius:4px;background:var(--s1);width:${Math.max(2, 100 * v / pairs[0][1])}%"></div></td></tr>`).join("")}</table></div>` : `<div class="muted">none</div>`;
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
        <div class="row" style="margin-top:10px"><div class="field"><label for="clMin">Min total chars (short outliers below ${o.fences.low})</label><input id="clMin" type="number" placeholder="${o.fences.low}"></div><div class="field"><label for="clMax">Max total chars (long outliers above ${o.fences.high})</label><input id="clMax" type="number" placeholder="${o.fences.high}"></div><div class="field"><label for="clRating">Min rating (1–10)</label><input id="clRating" type="number" step="0.5" min="0" max="10"></div><div class="field"><label for="clDrop">Drop labels (comma separated)</label><input id="clDrop" placeholder="poor, off-topic"></div></div>
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

  return { pageDatasets, createDatasetWizard, pageDataset, showDatapoint, snapshotsDialog, labelsDialog, qualityDialog };
}
