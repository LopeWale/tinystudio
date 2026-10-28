import { $, $$ } from "../ui/dom.js?v=c1757fa5eddd";
import { esc } from "../ui/format.js?v=c1757fa5eddd";

// Owns this feature's page rendering and dialogs; application services stay at explicit boundaries.
export function createMetricsPages({ state, http, feedback, dialogs, shell, context, ensureOverview, showTasks, pollTasks }) {
  const api = (...args) => http.api(...args);
  const post = (...args) => http.post(...args);
  const put = (...args) => http.put(...args);
  const del = (...args) => http.del(...args);
  const toast = (...args) => feedback.toast(...args);
  const guard = (...args) => feedback.guard(...args);
  const modal = (...args) => dialogs.modal(...args);
  const confirmModal = (...args) => dialogs.confirmModal(...args);
  const layout = (...args) => shell.layout(...args);
  const dataContext = (...args) => context.dataContext(...args);
  const assertRender = (...args) => context.assertRender(...args);

  async function pageMetrics() {
    const view = state.pageContext;
    await ensureOverview(true); assertRender(view);
    const metrics = await api("/api/metrics");
    assertRender(view);
    const content = layout("metrics", `<div class="page-head page-intro"><div class="page-heading"><span class="page-eyebrow">Evaluation criteria</span><h1>Metrics</h1><p class="page-description">Define what a good answer looks like with rules, code checks, or your own evaluator.</p></div><div class="actions"><button class="btn primary" id="newMetric">+ Create metric</button></div></div><div class="cards resource-grid">${metrics.map((me) => `<div ${dataContext("Metric", me)} class="card resource-card clickable" role="button" tabindex="0" data-me="${esc(me.name)}"><h3>${esc(me.name)}</h3><div class="sub">${esc(me.description || (me.kind === "code" ? `code check: ${me.check}` : me.kind === "webhook" ? `webhook: ${me.webhook.url}` : ""))}</div><div class="foot"><span>${me.kind === "rubric" ? `<span class="pill good">${me.rules.should.length} should</span> <span class="pill bad">${me.rules.should_not.length} should not</span>` : `<span class="pill">${me.kind}</span>`}</span><span class="arrow">→</span></div></div>`).join("") || `<div class="empty empty-state"><div class="empty-copy"><b>No metrics yet</b><p>Create a metric to define the rules or checks for your next evaluation.</p></div></div>`}</div>`, [{ label: "Evaluations", href: "#/evaluations" }, { label: "Metrics" }]);
    $("#newMetric", content).onclick = () => createMetricDialog();
    $$("[data-me]", content).forEach((c) => { c.onclick = () => editMetricDialog(metrics.find((x) => x.name === c.dataset.me)); c.onkeydown = (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); c.onclick(); } }; });
  }
  function createMetricDialog() {
    const snaps = state.overview.tree.datasets.flatMap((d) => d.snapshots);
    let kind = "rubric";
    const body = document.createElement("div");
    const m = modal({ title: "Create metric", body, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Create</button>` });
    const draw = () => {
      body.innerHTML = `<div class="tabs"><button class="${kind === "rubric" ? "on" : ""}" data-k="rubric">Rules (LLM judge)</button><button class="${kind === "code" ? "on" : ""}" data-k="code">Code check</button><button class="${kind === "webhook" ? "on" : ""}" data-k="webhook">Webhook</button></div>
        <div class="field"><label for="mName">Name</label><input id="mName" placeholder="Tone"></div>
        <div class="field"><label for="mDesc">Description</label><textarea id="mDesc" placeholder="What good looks like, e.g. answers are polite and never blame the customer."></textarea></div>
        ${kind === "rubric" ? `<div class="row"><div class="field"><label for="mSnap">Generate rules from a snapshot</label><select id="mSnap">${snaps.map((s) => `<option value="${esc(s.name)}">${esc(s.name)}</option>`).join("")}</select></div><button class="btn" id="genRules">Generate rules with AI</button></div><div class="two"><div class="field"><label for="mShould">Should follow (one per line)</label><textarea id="mShould"></textarea></div><div class="field"><label for="mShouldNot">Should not follow (one per line)</label><textarea id="mShouldNot"></textarea></div></div>`
        : kind === "code" ? `<div class="field"><label for="mCheck">Check</label><select id="mCheck"><option value="exact_match">exact match (normalized)</option><option value="json_valid">JSON valid and equal</option><option value="token_f1">token F1</option><option value="similarity">string similarity (LCS ratio)</option><option value="contains_reference">contains reference</option></select></div>`
        : `<div class="field"><label for="mUrl">Evaluation URL</label><input id="mUrl" placeholder="http://127.0.0.1:9000/evaluate"></div><div class="field"><label for="mToken">Token (optional)</label><input id="mToken"></div>`}`;
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
      const list = (arr, key) => `<ul class="rules">${arr.map((r, i) => `<li><span class="mark ${key === "should" ? "pass" : "fail"}">${key === "should" ? "✓" : "✕"}</span><input aria-label="${key === 'should' ? 'Should follow' : 'Should not follow'} rule ${i + 1}" data-k="${key}" data-i="${i}" value="${esc(r)}"><button class="btn sm icon" aria-label="Remove rule ${i + 1}" data-x="${key}:${i}">✕</button></li>`).join("")}</ul><button class="btn sm" data-add="${key}">+ Add rule</button>`;
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

  return { pageMetrics, createMetricDialog, editMetricDialog };
}
