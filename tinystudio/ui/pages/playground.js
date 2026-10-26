import { $, $$ } from "../ui/dom.js?v=c96cb305d835";
import { esc } from "../ui/format.js?v=c96cb305d835";
import { PlaygroundSessions } from "../core/playground.js?v=c96cb305d835";

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
    const content = layout("playground", `<div class="page-head"><div><span class="eyebrow">Experiment workspace</span><h1>Playground</h1></div><span class="meta">Try a prompt. Compare a response. Find the model that fits.</span></div>
      <div class="play playground-workspace"><div class="panel playground-settings"><div class="head"><div><h2>Model &amp; instructions</h2><span class="hint">Shape this conversation</span></div></div><div class="body">
        <div class="field"><label for="pgModel">Model</label><select id="pgModel">${models.length ? models.map((m) => `<option value="${esc(m.id)}" ${m.id === p.model ? "selected" : ""}>${esc(m.kind === "fine-tuned" ? m.alias : m.id)}${m.status === "up" ? " ●" : ""}</option>`).join("") : '<option value="">No models available</option>'}</select><div class="hint">● Loaded in memory. Other models load on your first message.</div></div>
        <div class="field"><label for="pgSystem">System prompt</label><textarea id="pgSystem" rows="4" placeholder="You are a helpful assistant.">${esc(p.system)}</textarea></div>
        <div class="field"><label for="pgRepos">Knowledge repositories</label><div class="hint">Retrieved passages add relevant context to your system prompt.</div><select id="pgRepos" aria-describedby="pgReposHint" multiple size="3">${(state.repositories || []).map((r) => `<option value="${esc(r.name)}" ${(p.repositories || []).includes(r.name) ? "selected" : ""}>${esc(r.name)} (${r.chunk_count} chunks)</option>`).join("")}</select><div class="hint" id="pgReposHint">${state.repositories.length ? "Use ⌘ or Ctrl to select multiple repositories." : "Create a repository to add reference material to your prompts."}</div><div class="row"><div class="field"><label for="pgRepoLimit">Passages</label><input id="pgRepoLimit" type="number" min="1" max="20" value="${p.repoLimit ?? 3}"></div><div class="field"><label for="pgRepoThreshold">Min similarity</label><input id="pgRepoThreshold" type="number" min="-1" max="1" step="0.05" value="${p.repoThreshold ?? 0}"></div></div></div>
        <div class="field"><label for="pgTemp">Temperature <b id="pgTempV">${p.temperature}</b></label><input id="pgTemp" type="range" min="0" max="1" step="0.05" value="${p.temperature}"><div class="hint">Lower values favor focused responses; higher values add variety.</div></div>
        <button class="btn" id="pgReset">Reset conversation</button>
      </div></div>
      <div class="panel playground-chat"><div class="head"><div><h2>Conversation</h2><span class="hint">Test your model in a real exchange</span></div><span class="muted mono" id="pgLatency" role="status" aria-live="polite"></span></div><div class="chatlog" id="pgLog" role="log" aria-label="Playground conversation" aria-live="polite" aria-relevant="additions text"></div><div class="composer"><label class="sr-only" for="pgInput">Message your model</label><textarea id="pgInput" rows="3" aria-label="Message your model" placeholder="Try a question or paste a prompt…"></textarea><button class="btn primary" id="pgSend" ${p.model ? "" : "disabled"}>Send</button></div><div class="assistant-shortcuts hint">Enter to send · Shift + Enter for a new line</div></div></div>`, [{ label: "Playground" }]);
    const log = $("#pgLog", content);
    sessions.attach(view);
    const drawLog = () => { if (!sessions.isView(view)) return; log.innerHTML = sessions.snapshot().history.map((m) => `<div class="bubble ${m.role}">${esc(m.content)}</div>`).join("") || `<div class="empty"><b>${sessions.snapshot().model ? "Your next experiment starts here" : "Add a model to get started"}</b><p>${sessions.snapshot().model ? "Choose your settings, then send a prompt to see how your model responds." : "Open Models to download a base model or use a trained checkpoint."}</p></div>`; log.scrollTop = log.scrollHeight; };
    drawLog();
    $("#pgModel", content).onchange = (e) => { sessions.configure({ model: e.target.value }); $("#pgSend", content).disabled = sessions.busy || !sessions.snapshot().model; };
    $("#pgSystem", content).oninput = (e) => { sessions.configure({ system: e.target.value }); };
    $("#pgTemp", content).oninput = (e) => { sessions.configure({ temperature: Number(e.target.value) }); $("#pgTempV", content).textContent = e.target.value; };
    $("#pgReset", content).onclick = () => { sessions.reset(); drawLog(); $("#pgSend", content).disabled = !sessions.snapshot().model; };
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
    $("#pgInput", content).onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } };
  }

  return { pagePlayground, syncScope: () => sessions.syncScope(), onRouteChange: () => sessions.detach(), destroy: () => sessions.destroy() };
}
