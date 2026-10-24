import { $, $$ } from "../ui/dom.js?v=82e74d4b5ecb";
import { esc, fmtNum, fmtDate, fmtParams, SERIES } from "../ui/format.js?v=82e74d4b5ecb";

// Owns this feature's page rendering and dialogs; application services stay at explicit boundaries.
export function createTrainingPages({ state, http, feedback, dialogs, shell, navigation, context, ensureOverview, showTasks, pollTasks, newEvaluationDialog, checkpointCaptureDialog }) {
  const api = (...args) => http.api(...args);
  const post = (...args) => http.post(...args);
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

  return { createJobDialog, autopilotPanel, autopilotDialog, autopilotHistoryDialog, pageFinetuning, pageJob, analysisPanel, wireAnalysis, drawLossChart };
}
