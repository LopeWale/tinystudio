import { $, $$ } from "../ui/dom.js?v=c1757fa5eddd";
import { esc, fmtPct, fmtDate, pctClass, SERIES } from "../ui/format.js?v=c1757fa5eddd";
import { histogram as drawHistogram } from "../ui/charts.js?v=c1757fa5eddd";

// Owns this feature's page rendering and dialogs; application services stay at explicit boundaries.
export function createEvaluationsPages({ state, http, feedback, dialogs, shell, navigation, context, ensureOverview, showTasks, pollTasks }) {
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
  const bindContext = (...args) => context.bindContext(...args);
  const assertRender = (...args) => context.assertRender(...args);
  const histogram = (h, color, width, height) => drawHistogram(h, color, width, height, dataContext);

  async function pageEvaluations() {
    const view = state.pageContext;
    await ensureOverview(true); assertRender(view);
    const evals = await api("/api/evals");
    assertRender(view);
    const content = layout("evaluations", `<div class="page-head page-intro"><div class="page-heading"><span class="page-eyebrow">Quality workspace</span><h1>Evaluations</h1><p class="page-description">Compare models on the same validation data and inspect the answers behind each score.</p></div><div class="actions"><button class="btn primary" id="newEval">+ Create evaluation</button></div></div><div class="cards resource-grid">${evals.map((e) => `<a ${dataContext("Evaluation", e)} class="card resource-card clickable" data-ev="${esc(e.name)}" href="#/evaluations/${encodeURIComponent(e.name)}"><h3>${esc(e.name)}</h3><span class="pill ${e.state === "succeeded" ? "completed" : e.state}">${e.state === "succeeded" ? "Completed" : e.state}</span><div class="sub resource-meta">${e.models.length} model${e.models.length === 1 ? "" : "s"} · ${e.metrics.map((metric) => esc(metric)).join(", ")} · ${e.datapoints} datapoints · snapshot ${esc(e.snapshot)}</div><div class="foot"><span>Created on: ${fmtDate(e.created)}</span><span class="arrow" aria-hidden="true">→</span></div></a>`).join("") || `<div class="empty empty-state"><div class="empty-copy"><b>No evaluations yet</b><p>Create an evaluation to compare experiments and base models on a snapshot’s validation split.</p></div></div>`}</div>`, [{ label: "Evaluations" }]);
    $("#newEval", content).onclick = () => newEvaluationDialog({});
    $$("[data-ev]", content).forEach((c) => c.onclick = (event) => { if (event && (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)) return; if (event) event.preventDefault(); go(`#/evaluations/${encodeURIComponent(c.dataset.ev)}`); });
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
    const modelPicker = () => `<div class="field"><label>Models to evaluate</label><div class="chips">${allModels.map((mo) => `<button data-m="${esc(mo.id)}" aria-pressed="${chosen.has(mo.id)}" class="${chosen.has(mo.id) ? "on" : ""}">${esc(mo.kind === "fine-tuned" ? mo.alias : mo.id.split("/").pop())}${mo.kind === "base" ? " (base)" : ""}</button>`).join("")}</div><div class="hint">Fine-tuned experiments and base models can be compared side by side.</div></div>`;
    const draw = () => {
      body.innerHTML = `<div class="tabs"><button class="${tab === "agentic" ? "on" : ""}" data-t="agentic">Agentic evaluation</button><button class="${tab === "byoe" ? "on" : ""}" data-t="byoe">Bring your own evals</button></div>
        <div class="row"><div class="field"><label for="evName">Name</label><input id="evName" placeholder="v1-compare"></div><div class="field"><label for="evSnap">Snapshot (validation split is used)</label><select id="evSnap">${snaps.map((s) => `<option value="${esc(s.name)}" ${s.name === snapshot ? "selected" : ""}>${esc(s.name)} — ${s.validation_count} validation rows</option>`).join("")}</select></div><div class="field" style="max-width:160px"><label for="evLimit">Limit rows (optional)</label><input id="evLimit" type="number" min="1"></div></div>
        ${modelPicker()}
        ${tab === "agentic" ? `<div class="field"><label>Selected metrics</label><div class="chips">${metrics.map((me) => `<button data-me="${esc(me.name)}" aria-pressed="${chosenMetrics.has(me.name)}" class="${chosenMetrics.has(me.name) ? "on" : ""}">${esc(me.name)} <span class="muted">${me.kind}</span></button>`).join("")}</div><div class="hint">Rubric metrics are judged by the helper LLM; code metrics need no model. <a href="#/metrics">Browse metrics</a></div></div>`
        : `<div class="two"><div><div class="field"><label for="byoeUrl">Evaluation URL</label><input id="byoeUrl" placeholder="http://127.0.0.1:9000/evaluate"></div><div class="field"><label for="byoeToken">URL token (optional)</label><input id="byoeToken" placeholder="sent as Authorization: Bearer"></div></div><div class="panel"><div class="head"><h2>Evaluation server setup</h2></div><div class="body" style="font-size:12.5px">Your server receives one POST per (datapoint, model):<pre class="mono" style="white-space:pre-wrap">{"datapoint": {"messages": [...system, user, assistant]},\n "prediction": "model answer",\n "model_name": "ft-job-1"}</pre>and returns<pre class="mono">{"score": 0.0 to 1.0, "reason": "optional"}</pre></div></div></div>`}`;
      $$("[data-t]", body).forEach((b) => b.onclick = () => { tab = b.dataset.t; draw(); });
      $$("[data-m]", body).forEach((b) => b.onclick = () => { chosen.has(b.dataset.m) ? chosen.delete(b.dataset.m) : chosen.add(b.dataset.m); b.classList.toggle("on"); b.setAttribute("aria-pressed", chosen.has(b.dataset.m)); });
      $$("[data-me]", body).forEach((b) => b.onclick = () => { chosenMetrics.has(b.dataset.me) ? chosenMetrics.delete(b.dataset.me) : chosenMetrics.add(b.dataset.me); b.classList.toggle("on"); b.setAttribute("aria-pressed", chosenMetrics.has(b.dataset.me)); });
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
      <div class="page-head page-intro"><div class="page-heading"><span class="page-eyebrow">Evaluation results</span><h1>${esc(name)} <span class="pill ${summary.state === "succeeded" ? "completed" : summary.state}">${summary.state === "succeeded" ? "Completed" : summary.state}</span></h1><div class="meta"><span>snapshot <b>${esc(summary.snapshot)}</b></span><span>· ${models.length} models selected</span><span>· ${summary.datapoints} datapoints</span>${summary.error ? `<span class="error">${esc(summary.error)}</span>` : ""}</div></div><div class="actions"><button class="btn danger" id="delEval">Delete</button></div></div>
      <div class="cards resource-grid" style="margin-bottom:18px">${(summary.leaderboard || []).map((b) => `<div class="card"><div style="display:flex;align-items:center;gap:10px"><h3 style="flex:1;font-size:14px" class="mono">${esc(b.model)}</h3><span class="pct ${pctClass(b.overall ?? 0)}" style="font-size:15px">${fmtPct(b.overall)}</span></div>${metrics.map((mt) => `<div style="display:flex;justify-content:space-between;margin-top:6px"><span>${esc(mt)}</span><span class="pct ${pctClass(b.scores[mt] ?? 0)}">${fmtPct(b.scores[mt])}</span></div>`).join("")}</div>`).join("")}</div>
      <div class="chips" id="metricTabs" style="margin-bottom:12px">${metrics.map((mt) => `<button data-mt="${esc(mt)}" aria-pressed="${mt === metric}" class="${mt === metric ? "on" : ""}">${esc(mt)}</button>`).join("")}</div>
      <div class="panel" style="margin-bottom:14px"><div class="head"><h2>Score distribution</h2><span class="muted">per model for the selected metric; 10 bins from 0% to 100%</span></div><div class="body" id="scoreHist"></div></div>
      <div class="panel" id="grid"></div>`, [{ label: "Evaluations", href: "#/evaluations" }, { label: name }]);
    $$(".cards > .card", content).forEach((el, i) => bindContext(el, `Evaluation leaderboard: ${summary.leaderboard[i]?.model}`, summary.leaderboard[i]));
    const drawHist = () => {
      $("#scoreHist", content).innerHTML = `<div class="cards resource-grid">${models.map((mo, i) => { const vals = datapoints.map((d) => byKey[`${d}|${mo}|${metric}`]?.score).filter((v) => v !== undefined); const counts = Array(10).fill(0); vals.forEach((v) => counts[Math.min(9, Math.floor(v * 10))]++); return `<div><div class="mono" style="font-size:12px;margin-bottom:4px">${esc(mo)}</div>${histogram({ bins: Array.from({ length: 11 }, (_, k) => k * 10), counts }, SERIES[i % SERIES.length], 320, 80)}</div>`; }).join("")}</div>`;
      const metricRows = rows.filter((r) => r.metric === metric);
      bindContext($("#scoreHist", content).closest(".panel"), `Score distribution: ${metric}`, { evaluation: name, metric, rows: metricRows });
      $$("#scoreHist svg", content).forEach((chart, i) => bindContext(chart, `Score distribution: ${models[i]} / ${metric}`, { evaluation: name, model: models[i], metric, rows: metricRows.filter((r) => r.model === models[i]) }));
    };
    $("#delEval", content).onclick = () => confirmModal("Delete evaluation", `Delete evaluation "${name}"?`, async () => { await del(`/api/evals/${encodeURIComponent(name)}`); go("#/evaluations"); });
    const draw = () => {
      const page = datapoints.slice(offset, offset + limit);
      const avg = (model) => { const vals = datapoints.map((d) => byKey[`${d}|${model}|${metric}`]?.score).filter((v) => v !== undefined); return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null; };
      $("#grid", content).innerHTML = `<div class="table-wrap"><table aria-label="Evaluation scores"><thead><tr><th scope="col">Datapoint (user message)</th>${models.map((mo) => `<th scope="col" class="num mono">${esc(mo)}</th>`).join("")}</tr></thead><tbody>
        <tr class="avg"><td>● Average (${esc(metric)})</td>${models.map((mo) => `<td class="num"><span class="pct ${pctClass(avg(mo) ?? 0)}">${fmtPct(avg(mo))}</span></td>`).join("")}</tr>
        ${page.map((d) => { const any = models.map((mo) => byKey[`${d}|${mo}|${metric}`]).find(Boolean); return `<tr><td class="truncate">${esc(any?.user || `datapoint ${d}`)}</td>${models.map((mo) => { const r = byKey[`${d}|${mo}|${metric}`]; return `<td class="num">${r ? `<button class="btn sm score-button pct ${pctClass(r.score)}" aria-label="Inspect score for datapoint ${d + 1}, ${esc(mo)}" data-cell="${d}|${esc(mo)}">${fmtPct(r.score)}</button>` : "–"}</td>`; }).join("")}</tr>`; }).join("")}
        </tbody></table></div><div class="pager"><span>Results ${datapoints.length ? offset + 1 : 0} - ${Math.min(offset + limit, datapoints.length)} of ${datapoints.length}</span><span class="spacer"></span><button class="btn sm" id="prev" aria-label="Previous page" ${offset === 0 ? "disabled" : ""}>‹</button><span class="cur">${Math.floor(offset / limit) + 1}</span><button class="btn sm" id="next" aria-label="Next page" ${offset + limit >= datapoints.length ? "disabled" : ""}>›</button></div>`;
      bindContext($("#grid", content), `Evaluation results: ${name} / ${metric}`, { evaluation: name, metric, rows: rows.filter((r) => r.metric === metric && page.includes(r.datapoint)) });
      $$("#grid tbody tr", content).forEach((el, i) => bindContext(el, i === 0 ? `Average: ${metric}` : `Datapoint ${page[i - 1]}`, { evaluation: name, metric, rows: rows.filter((r) => r.metric === metric && (i === 0 || r.datapoint === page[i - 1])) }));
      $$("[data-cell]", content).forEach((el) => { const [d, mo] = el.dataset.cell.split("|"); bindContext(el, `Datapoint ${d}: ${mo} / ${metric}`, byKey[`${d}|${mo}|${metric}`]); });
      $("#prev", content).onclick = () => { offset -= limit; draw(); };
      $("#next", content).onclick = () => { offset += limit; draw(); };
      $$("[data-cell]", content).forEach((c) => c.onclick = () => { const [d, mo] = c.dataset.cell.split("|"); evalDetails(Number(d), mo, models, metrics, byKey); });
    };
    $$("[data-mt]", content).forEach((b) => b.onclick = () => { metric = b.dataset.mt; $$("[data-mt]", content).forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", x === b); }); offset = 0; draw(); drawHist(); });
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

  return { pageEvaluations, newEvaluationDialog, pageEval, evalDetails };
}
