import { $, $$ } from "../ui/dom.js?v=82e74d4b5ecb";
import { esc, fmtPct, pctClass } from "../ui/format.js?v=82e74d4b5ecb";

// Owns this feature's page rendering and dialogs; application services stay at explicit boundaries.
export function createTracesPages({ state, http, feedback, dialogs, shell, context, ensureOverview }) {
  const api = (...args) => http.api(...args);
  const post = (...args) => http.post(...args);
  const del = (...args) => http.del(...args);
  const toast = (...args) => feedback.toast(...args);
  const guard = (...args) => feedback.guard(...args);
  const modal = (...args) => dialogs.modal(...args);
  const layout = (...args) => shell.layout(...args);
  const dataContext = (...args) => context.dataContext(...args);
  const assertRender = (...args) => context.assertRender(...args);

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

  return { pageTraces };
}
