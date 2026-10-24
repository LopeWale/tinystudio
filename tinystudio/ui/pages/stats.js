import { $, $$ } from "../ui/dom.js?v=82e74d4b5ecb";
import { esc } from "../ui/format.js?v=82e74d4b5ecb";

// Owns this feature's page rendering and dialogs; application services stay at explicit boundaries.
export function createStatsPages({ state, http, shell, context, ensureOverview }) {
  const api = (...args) => http.api(...args);
  const layout = (...args) => shell.layout(...args);
  const dataContext = (...args) => context.dataContext(...args);
  const assertRender = (...args) => context.assertRender(...args);

  async function pageStats() {
    const view = state.pageContext;
    await ensureOverview(true); assertRender(view);
    const s = await api("/api/stats");
    const tile = (k, v) => `<div ${dataContext(k, {label:k,value:v})} class="tile"><div class="k">${k}</div><div class="v">${v}</div></div>`;
    const max = Math.max(1, ...s.requests_per_day.map(([, n]) => n));
    assertRender(view);
    layout("stats", `<div class="page-head"><h1>Stats</h1></div>
      <div class="tiles">${tile("Datasets", s.datasets)}${tile("Datapoints", s.datapoints)}${tile("Snapshots", s.snapshots)}${tile("Jobs", s.jobs)}${tile("Experiments succeeded", s.experiments.succeeded || 0)}${tile("Training time", `${Math.round(s.training_seconds / 60)} min`)}${tile("Evaluations", s.evaluations)}${tile("Requests served", s.requests)}${tile("Tokens served", s.prompt_tokens + s.completion_tokens)}${tile("Scored traces", s.scored_traces)}</div>
      <div class="two"><div class="panel"><div class="head"><h2>Requests per day</h2></div><div class="body">${s.requests_per_day.length ? `<div ${dataContext("Requests per day", s.requests_per_day)} class="bars">${s.requests_per_day.map(([d, n]) => `<div title="${d}: ${n}" style="height:${Math.max(4, (n / max) * 100)}%"></div>`).join("")}</div><div class="muted" style="display:flex;justify-content:space-between;font-size:11px;margin-top:4px"><span>${s.requests_per_day[0][0]}</span><span>${s.requests_per_day[s.requests_per_day.length - 1][0]}</span></div>` : `<div class="empty">No requests yet.</div>`}</div></div>
      <div class="panel"><div class="head"><h2>Requests per model</h2></div><div class="body"><table>${s.requests_per_model.map(([m, n]) => `<tr ${dataContext("Requests per model", {model:m,requests:n})}><td class="mono">${esc(m)}</td><td class="num">${n}</td></tr>`).join("") || `<tr><td class="muted">none</td></tr>`}</table><p class="muted" style="margin-top:10px">Loaded now: ${s.models_loaded.map(esc).join(", ") || "none"}</p></div></div></div>`, [{ label: "Stats" }]);
  }

  return { pageStats };
}
