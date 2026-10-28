import { $, $$ } from "../ui/dom.js?v=c1757fa5eddd";
import { esc } from "../ui/format.js?v=c1757fa5eddd";

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
    const tile = (k, v) => `<div ${dataContext(k, {label:k,value:v})} class="tile metric-card"><div class="k">${k}</div><div class="v">${typeof v === "number" ? v.toLocaleString() : v}</div></div>`;
    const max = Math.max(1, ...s.requests_per_day.map(([, n]) => n));
    assertRender(view);
    layout("stats", `<div class="page-head page-intro"><div class="page-heading"><span class="page-eyebrow">Workspace activity</span><h1>Stats</h1><p class="sub">Follow your data, training, evaluation, and serving activity in one place.</p></div></div>
      <div class="tiles">${tile("Datasets", s.datasets)}${tile("Datapoints", s.datapoints)}${tile("Snapshots", s.snapshots)}${tile("Jobs", s.jobs)}${tile("Experiments succeeded", s.experiments.succeeded || 0)}${tile("Training time", `${Math.round(s.training_seconds / 60)} min`)}${tile("Evaluations", s.evaluations)}${tile("Requests served", s.requests)}${tile("Tokens served", s.prompt_tokens + s.completion_tokens)}${tile("Scored traces", s.scored_traces)}</div>
      <div class="two"><div class="panel"><div class="head"><h2>Requests per day</h2></div><div class="body">${s.requests_per_day.length ? `<div ${dataContext("Requests per day", s.requests_per_day)} class="bars">${s.requests_per_day.map(([d, n]) => `<div role="img" aria-label="${esc(d)}: ${n} requests" title="${esc(d)}: ${n}" style="height:${Math.max(4, (n / max) * 100)}%"></div>`).join("")}</div><div class="muted chart-range"><span>${s.requests_per_day[0][0]}</span><span>${s.requests_per_day[s.requests_per_day.length - 1][0]}</span></div>` : `<div class="empty-state"><h3>No requests yet</h3><p class="empty-copy">Serve a model in the Playground or through the API to see daily activity.</p></div>`}</div></div>
      <div class="panel"><div class="head"><h2>Requests per model</h2></div><div class="body table-wrap"><table aria-label="Requests served per model"><thead><tr><th scope="col">Model</th><th scope="col" class="num">Requests</th></tr></thead><tbody>${s.requests_per_model.map(([m, n]) => `<tr ${dataContext("Requests per model", {model:m,requests:n})}><td class="mono">${esc(m)}</td><td class="num">${n}</td></tr>`).join("") || `<tr><td colspan="2"><div class="empty-state"><h3>No model requests yet</h3><p class="empty-copy">Model usage will appear after the first inference.</p></div></td></tr>`}</tbody></table><p class="muted" style="margin-top:10px">Models loaded now: ${s.models_loaded.map(esc).join(", ") || "none"}</p></div></div></div>`, [{ label: "Stats" }]);
  }

  return { pageStats };
}
