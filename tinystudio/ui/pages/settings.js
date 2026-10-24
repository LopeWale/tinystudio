import { $, $$ } from "../ui/dom.js?v=82e74d4b5ecb";
import { esc, fmtDate } from "../ui/format.js?v=82e74d4b5ecb";

// Owns this feature's page rendering and dialogs; application services stay at explicit boundaries.
export function createSettingsPages({ state, http, feedback, shell, context, ensureOverview, resetAssistantModels }) {
  const api = (...args) => http.api(...args);
  const post = (...args) => http.post(...args);
  const put = (...args) => http.put(...args);
  const toast = (...args) => feedback.toast(...args);
  const guard = (...args) => feedback.guard(...args);
  const layout = (...args) => shell.layout(...args);
  const assertRender = (...args) => context.assertRender(...args);

  async function pageSettings() {
    const view = state.pageContext;
    await ensureOverview(true); assertRender(view);
    const [data, cfg] = await Promise.all([api("/api/settings"), api("/api/config")]);
    const groups = [
      ["anthropic", "Claude (assistant)", "Used by the ✦ assistant and the Assistant page. Any model the account lists becomes selectable.", "anthropic"],
      ["openai", "OpenAI (assistant)", "Same assistant, OpenAI models.", "openai"],
      ["helper", "Helper LLM", "Synthetic data, auto-labelling, rubric judging, neuron explanations and tests. Any OpenAI-compatible server: a local mlx_lm / llama-server / Ollama / vLLM, or a hosted API.", "helper"],
      ["rag", "Embeddings (repositories)", "The feature-extraction model new repositories embed with. Existing repositories keep theirs.", null],
      ["hub", "Hugging Face", "Token for gated models and uploads.", "hub"],
    ];
    const byGroup = (g) => data.settings.filter((s) => s.group === g);
    const field = (s) => `<div class="field"><label>${esc(s.label)} <span class="muted">(${s.source}${s.set ? "" : ", not set"})</span></label>${s.secret
      ? `<div class="row"><input type="password" data-key="${s.key}" placeholder="${s.set ? "•••••••• (saved; type to replace)" : "not set"}" autocomplete="new-password"><label class="chk"><input type="checkbox" data-clear="${s.key}"> clear</label></div>`
      : `<input data-key="${s.key}" value="${esc(s.value || "")}" placeholder="default">`}</div>`;
    const store = (what, title, detail) => `<div class="card"><h3>${title}</h3><div class="sub mono" style="word-break:break-all">${esc(detail)}</div><div class="row" style="margin-top:8px"><button class="btn small" data-test="${what}">Test connection</button><span class="muted" data-result="${what}"></span></div></div>`;
    assertRender(view);
    const content = layout("settings", `<div class="page-head"><div><h1>Settings</h1><div class="meta"><span>One set for this deployment, stored in PostgreSQL and applied to the API, the workers, the inference service, and the assistant. Admins only.${data.updated ? ` Last change ${fmtDate(data.updated)}.` : ""}</span></div></div><div class="actions"><button class="btn primary" id="stSave">Save</button></div></div>
      <div class="cards settings">${groups.map(([g, title, blurb, test]) => `<div class="card"><h3>${title}</h3><div class="sub">${blurb}</div><div style="margin-top:10px">${byGroup(g).map(field).join("")}</div>${test ? `<div class="row" style="margin-top:8px"><button class="btn small" data-test="${test}">Test connection</button><span class="muted" data-result="${test}"></span></div>` : ""}</div>`).join("")}
      ${store("database", "PostgreSQL", `${cfg.database_url} · ${cfg.database_version || "?"}`)}${store("redis", "Redis (queue)", cfg.redis_url)}${store("qdrant", "Qdrant (vectors)", cfg.qdrant_url)}${store("inference", "Inference", cfg.inference.mode === "remote" ? cfg.inference.url : "models load inside the API process")}
      <div class="card"><h3>Compute and data</h3><div class="sub">Device and dtype are per host (TINYSTUDIO_DEVICE / TINYSTUDIO_DTYPE on each worker and inference machine); this API host reports <span class="mono">${esc(cfg.device)} / ${esc(cfg.dtype)}</span>. Artifacts live in <span class="mono">${esc(cfg.data_dir)}</span>.</div></div></div>`, [{ label: "Settings" }]);
    $("#stSave", content).onclick = guard(async () => {
      const values = {};
      $$("[data-key]", content).forEach((el) => { const key = el.dataset.key; const spec = data.settings.find((s) => s.key === key); if (spec.secret) { if (el.value) values[key] = el.value; } else if ((el.value || "") !== (spec.value || "")) values[key] = el.value || null; });
      $$("[data-clear]", content).forEach((el) => { if (el.checked) values[el.dataset.clear] = null; });
      if (!Object.keys(values).length) return toast("nothing changed");
      await put("/api/settings", { values });
      resetAssistantModels(); state.overview = null;
      toast("settings saved"); pageSettings();
    });
    $$("[data-test]", content).forEach((b) => b.onclick = guard(async () => { const out = $(`[data-result="${b.dataset.test}"]`, content); out.textContent = "testing…"; const r = await post("/api/settings/test", { what: b.dataset.test }); out.innerHTML = `<span class="pill ${r.ok ? "good" : "bad"}">${r.ok ? "ok" : "failed"}</span> ${esc(r.detail)}`; }));
  }

  return { pageSettings };
}
