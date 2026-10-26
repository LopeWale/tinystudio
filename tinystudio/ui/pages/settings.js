import { $, $$ } from "../ui/dom.js?v=c96cb305d835";
import { esc, fmtDate } from "../ui/format.js?v=c96cb305d835";

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
    const field = (s) => `<div class="field"><label for="setting-${esc(s.key)}">${esc(s.label)}</label><div class="setting-status">${esc(s.source)}${s.set ? " · configured" : " · not set"}</div>${s.secret
      ? `<div class="row"><input id="setting-${esc(s.key)}" type="password" data-key="${s.key}" placeholder="${s.set ? "•••••••• (saved; type to replace)" : "not set"}" autocomplete="new-password"><label class="chk"><input type="checkbox" data-clear="${s.key}"> Clear saved value</label></div>`
      : `<input id="setting-${esc(s.key)}" data-key="${s.key}" value="${esc(s.value || "")}" placeholder="default">`}</div>`;
    const store = (what, title, detail) => `<div class="card"><h3>${title}</h3><div class="sub mono infrastructure-path">${esc(detail)}</div><div class="row" style="margin-top:8px"><button class="btn small" data-test="${what}">Test connection</button><span class="muted" role="status" data-result="${what}"></span></div></div>`;
    assertRender(view);
    const content = layout("settings", `<div class="page-head page-intro"><div class="page-heading"><span class="page-eyebrow">Administration</span><h1>Settings</h1><div class="meta"><span>Configure providers for this deployment. Changes apply to the API, workers, inference service, and assistant. Admins only.${data.updated ? ` Last change ${fmtDate(data.updated)}.` : ""}</span></div></div><div class="actions"><button class="btn primary" id="stSave">Save changes</button></div></div>
      <section aria-labelledby="providerSettings"><div class="section-intro"><h2 id="providerSettings">Models & providers</h2><p class="sub">Saved secrets stay hidden. Enter a new value to replace one, or select Clear saved value.</p></div><div class="cards settings">${groups.map(([g, title, blurb, test]) => `<div class="card"><h3>${title}</h3><div class="sub">${blurb}</div><div style="margin-top:10px">${byGroup(g).map(field).join("")}</div>${test ? `<div class="row" style="margin-top:8px"><button class="btn small" data-test="${test}">Test connection</button><span class="muted" role="status" data-result="${test}"></span></div>` : ""}</div>`).join("")}
      </div></section><section aria-labelledby="infrastructureSettings"><div class="section-intro"><h2 id="infrastructureSettings">Infrastructure</h2><p class="sub">Read-only deployment connections. Test each service to check availability.</p></div><div class="cards settings">${store("database", "PostgreSQL", `${cfg.database_url} · ${cfg.database_version || "?"}`)}${store("redis", "Redis (queue)", cfg.redis_url)}${store("qdrant", "Qdrant (vectors)", cfg.qdrant_url)}${store("inference", "Inference", cfg.inference.mode === "remote" ? cfg.inference.url : "models load inside the API process")}
      <div class="card"><h3>Compute and data</h3><div class="sub">Device and dtype are per host (TINYSTUDIO_DEVICE / TINYSTUDIO_DTYPE on each worker and inference machine); this API host reports <span class="mono">${esc(cfg.device)} / ${esc(cfg.dtype)}</span>. Artifacts live in <span class="mono">${esc(cfg.data_dir)}</span>.</div></div></div></section>`, [{ label: "Settings" }]);
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
