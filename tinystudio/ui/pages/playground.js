import { $, $$ } from "../ui/dom.js?v=82e74d4b5ecb";
import { esc } from "../ui/format.js?v=82e74d4b5ecb";
import { PlaygroundSessions } from "../core/playground.js?v=82e74d4b5ecb";

// Owns this feature's page rendering and dialogs; application services stay at explicit boundaries.
export function createPlaygroundPages({ state, http, feedback, shell, context, ensureOverview }) {
  const api = (...args) => http.api(...args);
  const rawFetch = (...args) => http.rawFetch(...args);
  const guard = (...args) => feedback.guard(...args);
  const layout = (...args) => shell.layout(...args);
  const assertRender = (...args) => context.assertRender(...args);
  const sessions = new PlaygroundSessions({ getScope: () => ({ user: state.user?.id, project: state.project }), fetch: rawFetch });

  async function pagePlayground(preset) {
    const view = state.pageContext;
    await ensureOverview(true); assertRender(view);
    const models = await api("/api/models");
    try { state.repositories = await api("/api/repositories"); } catch (_) { state.repositories = []; }
    if (preset) { sessions.reset(); sessions.configure({ model: preset }); }
    if (!sessions.snapshot().model) sessions.configure({ model: (models.find((m) => m.kind === "fine-tuned") || models[0])?.id || null });
    const p = sessions.snapshot();
    assertRender(view);
    const content = layout("playground", `<div class="page-head"><h1>Playground</h1><span class="meta">Test base and fine-tuned models before you deploy them.</span></div>
      <div class="play"><div class="panel"><div class="head"><h2>Settings</h2></div><div class="body">
        <div class="field"><label>Model</label><select id="pgModel">${models.map((m) => `<option value="${esc(m.id)}" ${m.id === p.model ? "selected" : ""}>${esc(m.kind === "fine-tuned" ? m.alias : m.id)}${m.status === "up" ? " ●" : ""}</option>`).join("")}</select><div class="hint">● = loaded in memory. Others load on first message.</div></div>
        <div class="field"><label>System prompt</label><textarea id="pgSystem" placeholder="You are a helpful assistant.">${esc(p.system)}</textarea></div>
        <div class="field"><label>Repositories (RAG): retrieved passages are added to the system prompt</label><select id="pgRepos" multiple size="3">${(state.repositories || []).map((r) => `<option value="${esc(r.name)}" ${(p.repositories || []).includes(r.name) ? "selected" : ""}>${esc(r.name)} (${r.chunk_count} chunks)</option>`).join("")}</select><div class="row"><div class="field"><label>Passages</label><input id="pgRepoLimit" type="number" min="1" max="20" value="${p.repoLimit ?? 3}"></div><div class="field"><label>Min similarity</label><input id="pgRepoThreshold" type="number" min="-1" max="1" step="0.05" value="${p.repoThreshold ?? 0}"></div></div></div>
        <div class="field"><label>Creativity level: <b id="pgTempV">${p.temperature}</b></label><input id="pgTemp" type="range" min="0" max="1" step="0.05" value="${p.temperature}"><div class="hint">0 = deterministic. The slider sets the sampling temperature.</div></div>
        <button class="btn" id="pgReset">Reset conversation</button>
      </div></div>
      <div class="panel" style="display:flex;flex-direction:column"><div class="head"><h2>Chat</h2><span class="muted mono" id="pgLatency"></span></div><div class="chatlog" id="pgLog"></div><div class="composer"><textarea id="pgInput" placeholder="Send a message"></textarea><button class="btn primary" id="pgSend">Send</button></div></div></div>`, [{ label: "Playground" }]);
    const log = $("#pgLog", content);
    sessions.attach(view);
    const drawLog = () => { if (!sessions.isView(view)) return; log.innerHTML = sessions.snapshot().history.map((m) => `<div class="bubble ${m.role}">${esc(m.content)}</div>`).join("") || `<div class="empty"><b>Start a conversation</b>Pick a model and send a message.</div>`; log.scrollTop = log.scrollHeight; };
    drawLog();
    $("#pgModel", content).onchange = (e) => { sessions.configure({ model: e.target.value }); };
    $("#pgSystem", content).oninput = (e) => { sessions.configure({ system: e.target.value }); };
    $("#pgTemp", content).oninput = (e) => { sessions.configure({ temperature: Number(e.target.value) }); $("#pgTempV", content).textContent = e.target.value; };
    $("#pgReset", content).onclick = () => { sessions.reset(); drawLog(); $("#pgSend", content).disabled = false; };
    const send = guard(async () => {
      const text = $("#pgInput", content).value.trim(); if (!text || sessions.busy || !sessions.snapshot().model || !sessions.isView(view)) return;
      $("#pgInput", content).value = "";
      $("#pgSend", content).disabled = true;
      try {
        sessions.configure({ repositories: [...$("#pgRepos", content).selectedOptions].map((o) => o.value), repoLimit: Number($("#pgRepoLimit", content).value) || 3, repoThreshold: Number($("#pgRepoThreshold", content).value) || 0 });
        await sessions.send(text, { view, onUpdate: drawLog, onLatency: (seconds) => { if (sessions.isView(view)) $("#pgLatency", content).textContent = `${seconds}s`; } });
      } catch (error) { if (sessions.isView(view) && !$("#pgInput", content).value) $("#pgInput", content).value = text; throw error; }
      finally { if (sessions.isView(view)) $("#pgSend", content).disabled = sessions.busy; }
    });
    $("#pgSend", content).onclick = send;
    $("#pgInput", content).onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } };
  }

  return { pagePlayground, syncScope: () => sessions.syncScope(), onRouteChange: () => sessions.detach(), destroy: () => sessions.destroy() };
}
