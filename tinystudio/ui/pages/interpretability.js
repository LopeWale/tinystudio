import { $, $$ } from "../ui/dom.js?v=c1757fa5eddd";
import { esc, fmtDate } from "../ui/format.js?v=c1757fa5eddd";
import { histogram as drawHistogram } from "../ui/charts.js?v=c1757fa5eddd";

// Owns this feature's page rendering and dialogs; application services stay at explicit boundaries.
export function createInterpretabilityPages({ state, http, feedback, dialogs, shell, navigation, context, ensureOverview, showTasks, pollTasks }) {
  const api = (...args) => http.api(...args);
  const post = (...args) => http.post(...args);
  const del = (...args) => http.del(...args);
  const toast = (...args) => feedback.toast(...args);
  const guard = (...args) => feedback.guard(...args);
  const modal = (...args) => dialogs.modal(...args);
  const confirmModal = (...args) => dialogs.confirmModal(...args);
  const layout = (...args) => shell.layout(...args);
  const go = (...args) => navigation.go(...args);
  const dataContext = (...args) => context.dataContext(...args);
  const assertRender = (...args) => context.assertRender(...args);
  const histogram = (h, color, width, height) => drawHistogram(h, color, width, height, dataContext);

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
      <div class="field"><label for="cpModel">Model (job/number, job/number@step for a checkpoint, or a Hub id)</label><input id="cpModel" list="cpModels" value="${esc(preset.model || "")}" placeholder="smol-lora/1"><datalist id="cpModels">${choices.map((id) => `<option value="${esc(id)}">`).join("")}</datalist></div>
      <div class="row"><div class="field"><label for="cpSource">Source</label><select id="cpSource">${datasets.map((d) => `<option value="dataset:${esc(d.name)}">dataset ${esc(d.name)} (${d.datapoints})</option>`).join("")}${snaps.map((s) => `<option value="snapshot:${esc(s.name)}">snapshot ${esc(s.name)}</option>`).join("")}</select></div>
      <div class="field"><label for="cpLayers">Layers (comma-separated, or all)</label><input id="cpLayers" value="${esc(preset.layers || "")}" placeholder="all"></div></div>
      <div class="row"><div class="field"><label for="cpTopk">Top-k per neuron</label><input id="cpTopk" type="number" min="5" max="500" value="40"></div><div class="field"><label for="cpName">Name (optional)</label><input id="cpName" placeholder="model--dataset"></div></div>`,
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
      <div class="field"><label for="dfExp">Experiment</label><select id="dfExp">${experiments.map((e) => `<option value="${esc(e.id)}">${esc(e.id)} (base ${esc(e.base || "?")})</option>`).join("")}</select></div>
      <div class="row"><div class="field"><label for="dfDs">Dataset</label><select id="dfDs">${datasets.map((d) => `<option>${esc(d.name)}</option>`).join("")}</select></div><div class="field"><label for="dfLayers">Layers (comma-separated, or all)</label><input id="dfLayers" placeholder="all"></div></div>
      <div class="field"><label for="dfBase">Compare against (optional; default = the experiment's own base weights)</label><input id="dfBase" list="dfModels" placeholder="leave empty"><datalist id="dfModels">${choices.map((id) => `<option value="${esc(id)}">`).join("")}</datalist></div>`,
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
    const m = modal({ title: `Capture checkpoints of ${job}/${number}`, body: `<p class="muted">Captures every saved checkpoint plus the final weights on one dataset, so the neuron panel can show how a neuron's strongest examples drifted during training.</p><div class="row"><div class="field"><label for="ckDs">Dataset</label><select id="ckDs">${datasets.map((d) => `<option>${esc(d.name)}</option>`).join("")}</select></div><div class="field"><label for="ckLayers">Layers</label><input id="ckLayers" placeholder="e.g. 8,12,16 or all"></div></div>`, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Capture</button>` });
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
    const content = layout("interp", `<div class="page-head page-intro"><div class="page-heading"><span class="page-eyebrow">Inside the model</span><h1>Interpretability</h1><p class="sub">Explore neuron activations, test explanations, and compare how training changes a model.</p></div><div class="actions"><button class="btn" id="newDiff">Compare base vs experiment</button><button class="btn primary" id="newCapture">+ New capture</button></div></div>
      <div class="panel" style="margin-bottom:14px"><div class="head"><h2>Captures</h2><span class="muted">per-neuron MLP activations of one model over one dataset</span></div><div class="body table-wrap">${data.captures.length ? `<table aria-label="Activation captures"><thead><tr><th scope="col">Name</th><th scope="col">Model</th><th scope="col">Source</th><th scope="col">Layers</th><th scope="col">Rows</th><th scope="col">Explained</th><th scope="col">Tested</th><th scope="col">Created</th><th scope="col">Actions</th></tr></thead><tbody>${data.captures.map((c) => `<tr ${dataContext("Activation capture", c)} class="clickable" data-cap="${esc(c.name)}"><td><a href="#/interp/c/${encodeURIComponent(c.name)}"><b>${esc(c.name)}</b></a></td><td class="mono">${esc(c.model_id)}</td><td>${esc(c.source)}</td><td class="mono">${c.layers.length === c.num_layers ? "all " + c.num_layers : c.layers.join(", ")}</td><td class="num">${c.rows}</td><td class="num">${c.explained}</td><td class="num">${c.tested}</td><td class="muted">${fmtDate(c.created)}</td><td><button class="btn small danger" data-delcap="${esc(c.name)}">Delete</button></td></tr>`).join("")}</tbody></table>` : `<div class="empty-state"><h3>No captures yet</h3><p class="empty-copy">Capture a model over a dataset to explore its strongest neuron activations and explain their patterns.</p></div>`}</div></div>
      <div class="panel"><div class="head"><h2>Before / after comparisons</h2><span class="muted">which neurons changed most between a base model and an experiment</span></div><div class="body table-wrap">${data.comparisons.length ? `<table aria-label="Model comparisons"><thead><tr><th scope="col">Name</th><th scope="col">Experiment</th><th scope="col">Base</th><th scope="col">Source</th><th scope="col">Median r per layer</th><th scope="col">Actions</th></tr></thead><tbody>${data.comparisons.map((c) => `<tr ${dataContext("Model comparison", c)} class="clickable" data-diff="${esc(c.name)}"><td><a href="#/interp/d/${encodeURIComponent(c.name)}"><b>${esc(c.name)}</b></a></td><td class="mono">${esc(c.experiment)}</td><td class="mono">${esc(c.base)}</td><td>${esc(c.source)}</td><td class="mono">${Object.entries(c.layer_summary || {}).map(([l, s]) => `L${l} ${s.median_r.toFixed(2)}`).join(" · ")}</td><td><button class="btn small danger" data-deldiff="${esc(c.name)}">Delete</button></td></tr>`).join("")}</tbody></table>` : `<div class="empty-state"><h3>No comparisons yet</h3><p class="empty-copy">Compare a completed experiment against its base model to find the neurons that changed most.</p></div>`}</div></div>`, [{ label: "Interpretability" }]);
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
        ${t ? `<div class="tiles" style="margin-top:8px"><div class="tile metric-card"><div class="k">Blind maximum</div><div class="v">${t.blind_best == null ? "—" : t.blind_best.toFixed(2) + "×"}</div><div class="muted">of the ${esc(t.reference || "corpus max")} ${(t.open_max ?? t.corpus_max).toFixed(2)}${t.beats_corpus ? " · beats it" : ""}</div></div><div class="tile metric-card"><div class="k">Positives strong</div><div class="v">${t.counts.positives_strong}/${t.positives.length}</div></div><div class="tile metric-card"><div class="k">Near misses strong</div><div class="v">${t.counts.near_misses_strong}/${t.near_misses.length}</div></div><div class="tile metric-card"><div class="k">Distant strong</div><div class="v">${t.counts.distant_strong}/${t.distant.length}</div></div><div class="tile metric-card"><div class="k">Verdict</div><div class="v"><span class="pill ${t.verdict === "win" ? "good" : t.verdict === "loss" ? "bad" : "warn"}">${t.verdict}</span></div></div></div>
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
      <div class="page-head page-intro"><div class="page-heading"><span class="page-eyebrow">Interpretability</span><h1>${esc(name)}</h1><div class="meta"><span class="mono">${esc(meta.model_id)}</span><span>· ${esc(meta.source)} · ${meta.rows} datapoints · ${meta.tokens.toLocaleString()} tokens</span><span>· shared prefix of ${meta.prefix_len ?? 0} tokens excluded</span></div></div>
        <div class="actions filter-bar"><select id="layerSel" aria-label="Layer">${meta.layers.map((l) => `<option value="${l}" ${l === layer ? "selected" : ""}>layer ${l}</option>`).join("")}</select><select id="sortSel" aria-label="Sort neurons">${["max", "min", "open_max", "echo_share", "density"].map((k) => `<option value="${k}" ${k === sort ? "selected" : ""}>sort by ${k.replace("_", " ")}</option>`).join("")}</select><input id="qBox" type="search" aria-label="Filter neurons by explanation" placeholder="Filter by explanation" value="${esc(q)}" style="width:180px"><button class="btn" id="explainTop">Explain top 20</button></div></div>
      <div class="tiles" style="margin-bottom:12px"><div class="tile metric-card"><div class="k">Neurons</div><div class="v">${meta.neurons_per_layer}</div><div class="muted">${table.total > 300 ? "showing 300 of " + table.total : table.total + " listed"}</div></div><div class="tile metric-card"><div class="k">Median max</div><div class="v">${(ls.max_median ?? 0).toFixed(2)}</div></div><div class="tile metric-card"><div class="k">Echo dominates top</div><div class="v">${Math.round((ls.echo_top_share ?? 0) * 100)}%</div><div class="muted">gate-open max under half of max</div></div><div class="tile metric-card"><div class="k">Mean echo share</div><div class="v">${Math.round((ls.echo_share_mean ?? 0) * 100)}%</div><div class="muted">of top-${meta.topk} positives in −/−</div></div><div class="tile metric-card"><div class="k">First-token peaks</div><div class="v">${ls.first_token_peaks ?? 0}</div><div class="muted">neurons peaking right after the prefix</div></div><div class="tile metric-card"><div class="k">Never open / dead</div><div class="v">${ls.never_open ?? 0} / ${ls.near_dead ?? 0}</div></div></div>
      <div class="wide-narrow"><div id="left"><div class="panel"><div class="body table-wrap" style="padding:0"><table class="neurons" aria-label="Neuron activations"><thead><tr><th scope="col">#</th><th scope="col">max</th><th scope="col">open</th><th scope="col">min</th><th scope="col">echo</th><th scope="col" title="share of content tokens above 10% of the neuron's maximum">density</th><th scope="col">explanation</th><th scope="col">test</th></tr></thead><tbody>${table.rows.map((r) => `<tr ${dataContext("Neuron", r)} class="clickable ${r.neuron === neuron ? "sel" : ""}" data-n="${r.neuron}"><td class="mono"><button class="btn sm neuron-select" aria-label="Explore neuron ${r.neuron}">${r.neuron}</button></td><td class="num">${r.max.toFixed(2)}</td><td class="num">${r.open_max == null ? "—" : r.open_max.toFixed(2)}</td><td class="num">${r.min.toFixed(2)}</td><td class="num">${Math.round(r.echo_share * 100)}%${r.echo_top ? "*" : ""}</td><td class="num">${(r.density * 100).toFixed(1)}%</td><td class="truncate" style="max-width:260px">${esc(r.explanation || "")}</td><td>${r.verdict ? `<span class="pill ${r.verdict === "win" ? "good" : r.verdict === "loss" ? "bad" : "warn"}">${r.verdict}</span>` : ""}</td></tr>`).join("") || `<tr><td colspan="8"><div class="empty-state"><h3>No neurons match</h3><p class="empty-copy">Clear the explanation filter or choose another layer to continue exploring.</p></div></td></tr>`}</tbody></table></div></div></div><div id="right" role="region" aria-label="Selected neuron details" tabindex="0"><div class="empty-state"><h3>Explore a neuron</h3><p class="empty-copy">Select a neuron to see positive and negative examples, label shares, explanations, and test results.</p></div></div></div>`,
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
      <div class="page-head page-intro"><div class="page-heading"><span class="page-eyebrow">Interpretability</span><h1>${esc(meta.experiment)} <span class="muted">vs</span> ${esc(meta.base)}</h1><div class="meta"><span>${esc(meta.source)} · ${meta.rows} datapoints · ${meta.mode === "adapter_toggle" ? "adapter toggled in place" : "two models"} · shared prefix of ${meta.prefix_len ?? 0} tokens excluded</span></div></div>
        <div class="actions filter-bar"><select id="layerSel" aria-label="Layer">${meta.layers.map((l) => `<option value="${l}" ${l === layer ? "selected" : ""}>layer ${l} · median r ${fr(meta.layer_summary?.[String(l)]?.median_r)}</option>`).join("")}</select><select id="sortSel" aria-label="Sort neurons">${[["change", "sort by change (1 − r + relative change)"], ["r", "sort by lowest r"], ["rel_change", "sort by relative change"]].map(([k, l]) => `<option value="${k}" ${k === sort ? "selected" : ""}>${l}</option>`).join("")}</select></div></div>
      <div class="tiles" style="margin-bottom:12px"><div class="tile metric-card"><div class="k">Median correlation r</div><div class="v">${fr(s.median_r)}</div></div><div class="tile metric-card"><div class="k">Neurons with r &lt; 0.9</div><div class="v">${s.below_0_9 ?? 0}</div></div><div class="tile metric-card"><div class="k">r &lt; 0.5</div><div class="v">${s.below_0_5 ?? 0}</div></div><div class="tile metric-card"><div class="k">Constant on one side</div><div class="v">${s.constant ?? 0}</div><div class="muted">r undefined (dead or revived)</div></div><div class="tile metric-card"><div class="k">Median relative change</div><div class="v">${(s.rel_change_median ?? 0).toFixed(2)}</div></div></div>
      <div class="two" style="margin-bottom:12px"><div class="panel"><div class="head"><h2>Distribution of r across ${meta.neurons_per_layer} neurons</h2></div><div class="body">${s.hist ? histogram(s.hist, "var(--s2)", 420, 100) : `<div class="empty-state"><h3>No correlation distribution</h3><p class="empty-copy">No histogram is available for this layer.</p></div>`}</div></div><div class="panel"><div class="head"><h2>Reading this</h2></div><div class="body muted">r is the Pearson correlation of a neuron's activations before and after fine-tuning over the same tokens (undefined when one side is constant); relative change is ‖after − before‖ / ‖before‖, so a neuron that only scaled still shows up; change = (1 − r) + min(relative change, 1). Click a neuron for the datapoints and tokens that moved it most. Shared-prefix tokens are excluded because causal attention makes them identical across datapoints.</div></div></div>
      <div class="wide-narrow"><div id="left"><div class="panel"><div class="head"><h2>Most changed neurons</h2></div><div class="body table-wrap" style="padding:0"><table class="neurons" aria-label="Neuron changes after training"><thead><tr><th scope="col">#</th><th scope="col">change</th><th scope="col">r</th><th scope="col">rel. change</th><th scope="col">largest token change</th><th scope="col">context</th></tr></thead><tbody>${data.rows.map((r) => `<tr ${dataContext("Neuron comparison", r)} class="clickable" data-n="${r.neuron}"><td class="mono"><button class="btn sm neuron-select" aria-label="Explore neuron ${r.neuron}">${r.neuron}</button></td><td class="num">${r.change.toFixed(2)}</td><td class="num">${fr(r.r)}</td><td class="num">${r.rel_change.toFixed(2)}</td><td class="num mono">${r.before.toFixed(2)} → ${r.after.toFixed(2)}</td><td class="ctxcell">${flat(r.context)}</td></tr>`).join("") || `<tr><td colspan="6"><div class="empty-state"><h3>No neuron changes to show</h3><p class="empty-copy">Choose another layer to explore its comparison.</p></div></td></tr>`}</tbody></table></div></div></div><div id="right" role="region" aria-label="Selected neuron details" tabindex="0"><div class="empty-state"><h3>Explore a changed neuron</h3><p class="empty-copy">Select a neuron to see which datapoints and tokens drove its change.</p></div></div></div>`,
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

  return { modelChoices, parseLayers, finishedExperiments, captureDialog, diffDialog, checkpointCaptureDialog, pageInterp, labelBars, entryRows, neuronPanel, pageCapture, pageDiff };
}
