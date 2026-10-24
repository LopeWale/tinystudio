import { $, $$ } from "../ui/dom.js?v=82e74d4b5ecb";
import { esc, fmtDate } from "../ui/format.js?v=82e74d4b5ecb";

// Owns this feature's page rendering and dialogs; application services stay at explicit boundaries.
export function createKeysPages({ state, http, feedback, dialogs, shell, context, ensureOverview }) {
  const api = (...args) => http.api(...args);
  const post = (...args) => http.post(...args);
  const del = (...args) => http.del(...args);
  const guard = (...args) => feedback.guard(...args);
  const modal = (...args) => dialogs.modal(...args);
  const confirmModal = (...args) => dialogs.confirmModal(...args);
  const layout = (...args) => shell.layout(...args);
  const assertRender = (...args) => context.assertRender(...args);

  async function pageKeys() {
    const view = state.pageContext;
    await ensureOverview(true); assertRender(view);
    const keys = await api("/api/keys");
    assertRender(view);
    const content = layout("keys", `<div class="page-head"><h1>API Keys</h1><div class="actions"><button class="btn primary" id="newKey">+ Create API key</button></div></div>
      <div class="panel"><table><thead><tr><th>Name</th><th>Prefix</th><th>Created</th><th>Last used</th><th></th></tr></thead><tbody>${keys.map((k) => `<tr><td><b>${esc(k.name)}</b></td><td class="mono">${esc(k.prefix)}…</td><td>${fmtDate(k.created)}</td><td>${fmtDate(k.last_used)}</td><td style="text-align:right"><button class="btn sm danger" data-rev="${k.id}">Revoke</button></td></tr>`).join("") || `<tr><td colspan="5" class="empty">No keys yet. Keys belong to your account and work on /v1 and /api (MCP clients use them).</td></tr>`}</tbody></table></div>
      <div class="panel" style="margin-top:16px"><div class="head"><h2>Using your key</h2></div><div class="body"><pre class="mono" style="white-space:pre-wrap">curl ${location.origin}/v1/chat/completions \\\n  -H "Authorization: Bearer ts-..." -H "content-type: application/json" \\\n  -d '{"model": "${esc(state.project)}/&lt;job&gt;/&lt;n&gt;", "messages": [{"role": "user", "content": "hello"}]}'</pre><p class="muted">Works with the OpenAI SDKs: set <span class="mono">base_url</span> to <span class="mono">${location.origin}/v1</span>. Model ids are <span class="mono">&lt;project&gt;/&lt;job&gt;/&lt;n&gt;</span>, or send the header <span class="mono">X-Project: ${esc(state.project)}</span> and use <span class="mono">&lt;job&gt;/&lt;n&gt;</span>.</p></div></div>`, [{ label: "API Keys" }]);
    $("#newKey", content).onclick = () => {
      const m = modal({ title: "Create API key", body: `<div class="field"><label>Name</label><input id="kName" placeholder="my-app"></div>`, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Create</button>` });
      $("[data-cancel]", m.el).onclick = m.close;
      $("[data-ok]", m.el).onclick = guard(async () => {
        const r = await post("/api/keys", { name: $("#kName", m.el).value });
        m.close();
        modal({ title: "Your new API key", sensitive: true, body: `<p>Copy it now; it is shown only once.</p><pre class="mono" style="background:var(--panel-2);padding:12px;border-radius:8px;word-break:break-all">${esc(r.key)}</pre>`, footer: `<button class="btn primary" data-done>Done</button>`, onMount: (el, close) => { $("[data-done]", el).onclick = () => { close(); pageKeys(); }; } });
      });
    };
    $$("[data-rev]", content).forEach((b) => b.onclick = () => confirmModal("Revoke key", "Revoke this key? Clients using it will get 401.", async () => { await del(`/api/keys/${b.dataset.rev}`); pageKeys(); }, "Revoke"));
  }

  return { pageKeys };
}
