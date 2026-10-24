import { $, $$ } from "../ui/dom.js?v=82e74d4b5ecb";
import { esc } from "../ui/format.js?v=82e74d4b5ecb";
import { ICONS } from "../ui/layout.js?v=82e74d4b5ecb";

export function createAssistantView({ state, assistantState: ASSIST, http, feedback, dialogs, shell, conversation, context, chat, ensureOverview }) {
  const api = (...args) => http.api(...args);
  const guard = (...args) => feedback.guard(...args);
  const confirmModal = (...args) => dialogs.confirmModal(...args);
  const layout = (...args) => shell.layout(...args);
  const assistStatus = (...args) => conversation.assistStatus(...args);
  const assistSyncStatus = (...args) => conversation.assistSyncStatus(...args);
  const assistPreferences = (...args) => conversation.assistPreferences(...args);
  const assistSave = (...args) => conversation.assistSave(...args);
  const assistLoad = (...args) => conversation.assistLoad(...args);
  const assistEnqueue = (...args) => conversation.assistEnqueue(...args);
  const assistRequest = (...args) => conversation.assistRequest(...args);
  const assistSameSnapshot = (...args) => conversation.assistSameSnapshot(...args);
  const assistAdopt = (...args) => conversation.assistAdopt(...args);
  const syncAssistantScope = (...args) => conversation.syncAssistantScope(...args);
  const setAssistantPicking = (...args) => context.setAssistantPicking(...args);
  const captureContext = (...args) => context.captureContext(...args);
  const pageContextReady = (...args) => context.pageContextReady(...args);
  const snapshotPage = (...args) => context.snapshotPage(...args);
  const uiPerform = (...args) => context.uiPerform(...args);
  const pageText = (...args) => context.pageText(...args);
  const pickContext = (...args) => context.pickContext(...args);
  const assertRender = (...args) => context.assertRender(...args);
  const loadAssistantModels = (...args) => chat.loadAssistantModels(...args);
  let mountedHost = null;
  let bridge = null;
  const onKeyDown = (e) => { if ((e.metaKey || e.ctrlKey) && e.key === "k") { e.preventDefault(); toggleAssistant(); } };
function assistRefreshUI() {
  for (const el of $$("#asPersistence")) el.textContent = assistStatus();
  const blocked = !ASSIST.session?.loaded || ASSIST.busy || ASSIST.session?.dirty || ASSIST.session?.error;
  for (const el of $$("#asSend, #asReset, #asProvider, #asModel")) el.disabled = Boolean(blocked);
  for (const el of $$("#asRetry")) el.hidden = !ASSIST.session?.error || ASSIST.session?.conflict;
  for (const el of $$("#asReload")) {
    el.disabled = Boolean(ASSIST.busy || ASSIST.session?.saving);
    el.hidden = !(ASSIST.session?.error || ASSIST.session?.conflict || ASSIST.session?.dirty && !ASSIST.busy);
    el.textContent = ASSIST.session?.ownerMismatch ? "Reload to update account" : "Load saved version";
  }
  for (const el of $$("#asSyncStatus")) el.textContent = assistSyncStatus();
}
function assistRedraw() {
  const drawer = $("#assist .drawer"); if (drawer && ASSIST.open) renderAssistant(drawer);
  const page = $("#asPage"); if (page) renderAssistant(page, { full: true });
}
function assistScopeChanged() {
  const drawer = $("#assist .drawer");
  if (!drawer) return;
  drawer.hidden = !ASSIST.open;
  $("#assist .fab")?.classList.toggle("on", ASSIST.open);
  renderAssistant(drawer);
}
function md(text) {
  let s = esc(text);
  s = s.replace(/```([a-z]*)\n([\s\S]*?)```/g, (_, l, c) => `<pre class="mono">${c}</pre>`);
  s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>").replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>");
  s = s.replace(/^(?:[-*] )(.*)$/gm, "• $1");
  return s;
}
function bubbleHtml(m) {
  const calls = (m.calls || []).map((c) => `<details class="muted call"><summary><span class="mono">${esc(c.name)}</span>${c.kind === "ui" ? ' <span class="pill info">interface</span>' : ""}(${esc(JSON.stringify(c.input ?? {})).slice(0, 140)}) ${c.output === undefined ? '<span class="spinner"></span>' : ""}</summary>${c.output !== undefined ? `<pre class="mono">${esc(typeof c.output === "string" ? c.output : JSON.stringify(c.output, null, 1)).slice(0, 3000)}</pre>` : ""}</details>`).join("");
  return calls + (m.content ? `<div class="mdtext">${md(m.content)}</div>` : "");
}

function assistantModelOptions() {
  const models = ASSIST.models?.[ASSIST.provider]?.models || [];
  const options = models.map((m) => `<option value="${esc(m.id)}" ${m.id === ASSIST.model ? "selected" : ""}>${esc(m.name || m.id)}</option>`).join("");
  const unlisted = ASSIST.model && !models.some((m) => m.id === ASSIST.model) ? `<option value="${esc(ASSIST.model)}" selected>${esc(ASSIST.model)}</option>` : "";
  return options + unlisted || "<option value=''>no models listed</option>";
}
function assistantProviderOptions() {
  return [["anthropic", "Claude"], ["openai", "OpenAI"]].map(([id, label]) => {
    const available = ASSIST.models?.[id]?.available;
    return `<option value="${id}" ${ASSIST.provider === id ? "selected" : ""} ${available ? "" : "disabled"}>${label}${!ASSIST.models || available ? "" : " (no key)"}</option>`;
  }).join("");
}
function assistantModelNotice() {
  if (!ASSIST.models) return '<div class="note">Loading model catalog…</div>';
  const error = ASSIST.models[ASSIST.provider]?.error;
  const missing = !ASSIST.models.anthropic.available && !ASSIST.models.openai.available;
  return (error ? `<div class="note">${esc(error)}</div>` : "") + (missing ? '<div class="note">No API credentials found. Set <span class="mono">ANTHROPIC_API_KEY</span> and/or <span class="mono">OPENAI_API_KEY</span> before starting <span class="mono">tinystudio serve</span>.</div>' : "");
}
function assistDrawLog(log, { preservePosition = false } = {}) {
  const top = log.scrollTop, atBottom = log.scrollHeight - top - log.clientHeight < 40;
  log.innerHTML = ASSIST.history.map((m) => m.role === "user" ? `<div class="bubble user">${esc(m.content)}</div>` : `<div class="bubble assistant">${bubbleHtml(m)}</div>`).join("") || `<div class="muted" style="font-size:12.5px">I can see this page when "page" is ticked, read or operate the interface with the ui_* tools, and run every tinystudio tool: datasets, cleaning, synthetic data, training, evaluation, serving, interpretability. Try: "what is on this page?", "explain the top three neurons of layer 12", "start a LoRA job on the latest snapshot".</div>`;
  log.scrollTop = preservePosition && !atBottom ? top : log.scrollHeight;
}
function assistRenderRemote() {
  // Incoming revisions update the transcript and model controls, leaving the
  // composer node, draft, focus, text selection and page-context controls intact.
  for (const container of $$("#assist .drawer, #asPage")) {
    const log = $("#asLog", container); if (log) assistDrawLog(log, { preservePosition: true });
    const provider = $("#asProvider", container); if (provider) provider.innerHTML = assistantProviderOptions();
    const model = $("#asModel", container); if (model) model.innerHTML = assistantModelOptions();
    const notice = $("#asModelNotice", container); if (notice) notice.innerHTML = assistantModelNotice();
    const usage = $("#asUsage", container); if (usage) usage.textContent = ASSIST.usage || "";
  }
  assistRefreshUI();
}
function assistRenderSelection(container) {
  const slot = $("#asSelection", container); if (!slot) return;
  slot.hidden = !ASSIST.selection;
  slot.innerHTML = ASSIST.selection ? `Attached: ${esc(ASSIST.selection.label)} · ${esc(ASSIST.selection.source)} <button class="btn small" id="asUnpick">Remove</button>` : "";
  const remove = $("#asUnpick", slot);
  if (remove) remove.onclick = () => { ASSIST.selection = null; for (const current of $$("#assist .drawer, #asPage")) assistRenderSelection(current); };
}
function renderAssistant(container, { full = false } = {}) {
  container.innerHTML = `
    <div class="head">
      <select id="asProvider" title="Provider">${assistantProviderOptions()}</select>
      <select id="asModel" title="Model (from the provider's live model list)">${assistantModelOptions()}</select>
      <label class="chk" title="Send what this page shows (route, visible text, the data it fetched, your selection) with each message"><input type="checkbox" id="asAttach" ${ASSIST.attach ? "checked" : ""}> page</label>
      <label class="chk" title="Allow interface changes and destructive tools for this session; destructive actions still require confirmation"><input type="checkbox" id="asDestructive" ${ASSIST.destructive ? "checked" : ""}> actions / deletions</label>
      <span class="spacer" style="flex:1"></span>
      <button class="btn small" id="asPick" title="Click a row, chart, or panel to attach its structured data">Pick data</button>
      <button class="btn small" id="asReset" title="Clear the conversation" ${ASSIST.busy || !ASSIST.session.loaded || ASSIST.session.dirty || ASSIST.session.error ? "disabled" : ""}>clear</button>
      ${full ? "" : `<a class="btn small" href="#/assistant" title="Open the full page">⤢</a><button class="btn small" id="asClose" title="Close">✕</button>`}
    </div>
    <div id="asModelNotice">${assistantModelNotice()}</div>
    <div class="note" id="asSelection" hidden></div>
    <div class="note"><span id="asPersistence">${esc(assistStatus())}</span> · <span id="asSyncStatus" role="status" aria-live="polite">${esc(assistSyncStatus())}</span> <button class="btn small" id="asRetry" ${ASSIST.session.error && !ASSIST.session.conflict ? "" : "hidden"}>Retry save / load</button> <button class="btn small" id="asReload" hidden>Load saved version</button></div>
    <div class="chatlog ${full ? "" : "compact"}" id="asLog"></div>
    <div class="composer"><textarea id="asInput" placeholder="Ask about what you see, or ask for something to be done… (Enter to send, Shift+Enter for a new line)"></textarea><button class="btn primary" id="asSend" ${ASSIST.busy || !ASSIST.session.loaded || ASSIST.session.dirty || ASSIST.session.error ? "disabled" : ""}>Send</button></div>
    <div class="muted mono" id="asUsage" style="padding:4px 12px;font-size:11px">${esc(ASSIST.usage || "")}</div>`;
  const log = $("#asLog", container);
  const draw = () => assistDrawLog(log);
  draw(); assistRefreshUI(); assistRenderSelection(container);
  $("#asProvider", container).onchange = guard(async (e) => { const session = ASSIST.session; session.provider = e.target.value; session.model = null; await loadAssistantModels(); if (session !== ASSIST.session) return; await assistSave(session); if (session === ASSIST.session) renderAssistant(container, { full }); });
  $("#asModel", container).onchange = guard(async (e) => { ASSIST.model = e.target.value; await assistSave(); });
  $("#asAttach", container).onchange = (e) => { ASSIST.attach = e.target.checked; assistPreferences(); };
  $("#asDestructive", container).onchange = (e) => { ASSIST.destructive = e.target.checked; };
  $("#asPick", container).onclick = () => setAssistantPicking(!ASSIST.picking);
  $("#asRetry", container).onclick = guard(async () => {
    const session = ASSIST.session; if (session.busy || session.conflict) return;
    if (session.dirty) {
      // A failed response may have followed a successful commit. Reconcile before another CAS write.
      const saved = await assistEnqueue(session, () => assistRequest(session));
      if (assistSameSnapshot(session, saved)) assistAdopt(session, saved);
      else if (saved.revision !== session.revision) {
        session.error = "A newer conversation is available. Refresh to load it; your unsaved reply remains here until you choose.";
        session.conflict = true;
      } else if (session.streaming) {
        // A disconnected stream can still be running; a partial PUT would invalidate its final save.
        session.error = "The server has not confirmed a saved reply yet. Try again or refresh the conversation.";
      } else await assistSave(session);
    } else await assistLoad(session, { refresh: true });
    assistRenderRemote();
  });
  $("#asReload", container).onclick = guard(async () => {
    const session = ASSIST.session; if (session.busy || session.saving) return;
    if (session.ownerMismatch) { location.reload(); return; }
    if (session.dirty && !await confirmModal("Reload conversation?", "Discard the unsaved messages shown here and load the database conversation?", async () => {}, "Discard and reload")) return;
    await assistLoad(session, { refresh: true, discard: true });
  });
  $("#asReset", container).onclick = guard(async () => {
    const session = ASSIST.session;
    if (session.busy || !session.loaded || session.dirty || session.error) return;
    session.history = []; session.usage = ""; await assistSave(session); assistRedraw();
  });
  if ($("#asClose", container)) $("#asClose", container).onclick = () => toggleAssistant(false);
  const send = guard(async () => {
    const redrawReply = (reply) => { for (const currentLog of $$("#asLog")) { const last = currentLog.lastElementChild; if (last && reply) last.innerHTML = bubbleHtml(reply); currentLog.scrollTop = currentLog.scrollHeight; } };
    await chat.sendMessage({ text: $("#asInput", container).value.trim(), clearInput: () => { $("#asInput", container).value = ""; }, draw, redrawReply, onUsage: (usage) => $$("#asUsage").forEach((el) => { el.textContent = usage; }) });
  });
  $("#asSend", container).onclick = send;
  $("#asInput", container).onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } };
}

function toggleAssistant(open) {
  ASSIST.open = open ?? !ASSIST.open;
  const drawer = $("#assist .drawer"); if (!drawer) return;
  drawer.hidden = !ASSIST.open;
  $("#assist .fab").classList.toggle("on", ASSIST.open);
  assistPreferences();
  if (ASSIST.open) {
    if (!$("#asInput", drawer)) renderAssistant(drawer);
    else assistRenderSelection(drawer);
    conversation.requestCheck(); $("#asInput", drawer)?.focus();
    guard(async () => { const session = ASSIST.session; await loadAssistantModels(); if (session === ASSIST.session) assistRenderRemote(); })();
  }
}
function mountAssistant() {
  const host = document.getElementById("assist"); if (!host || host.dataset.mounted) return;
  host.dataset.mounted = "1";
  conversation.mount(); context.mount(); syncAssistantScope();
  host.innerHTML = `<button class="fab" title="Assistant (Claude / OpenAI) — ask about this page or have it operate the interface">${ICONS.assistant}</button><div class="drawer" hidden></div>`;
  $("#assist .fab").onclick = () => toggleAssistant();
  bridge = { snapshotPage, uiPerform, captureContext, pageText, ASSIST, toggleAssistant, pickContext };
  window.tinystudio = bridge;  // for scripts, tests, and the browser console
  document.addEventListener("keydown", onKeyDown);
  mountedHost = host;
  if (ASSIST.open && location.hash !== "#/assistant") toggleAssistant(true);
}

async function pageAssistant() {
  const view = state.pageContext;
  await ensureOverview(true); assertRender(view);
  const st = await api("/api/assistant/status");
  conversation.requestCheck();
  await loadAssistantModels();
  assertRender(view);
  const content = layout("assistant", `<div class="page-head"><h1>Assistant</h1><span class="meta">A frontier model that sees the interface, operates it, and drives the same ${st.mcp.tools} tools as the MCP server. It is also available on every page from the ✦ button (⌘K).</span></div>
    <div class="play"><div>
      <div class="panel"><div class="head"><h2>How it sees the interface</h2></div><div class="body" style="font-size:12.5px">
        <p>With <b>page</b> ticked, every message carries the route, the visible text, the data the page fetched, and your text selection. The <span class="mono">ui_read_page</span> tool returns the live element tree with ref ids; <span class="mono">ui_navigate</span>, <span class="mono">ui_click</span>, <span class="mono">ui_fill</span>, and <span class="mono">ui_select</span> operate it in your tab. Catalog tools run on the server, so long actions show up in the task drawer.</p>
        <p class="muted">Models come from each provider's live model list; pick any Claude or OpenAI model your account can use. Assistant credentials are supplied through the server environment.</p>
      </div></div>
      <div class="panel" style="margin-top:16px"><div class="head"><h2>Use from Claude Code, Codex, Claude Desktop</h2></div><div class="body" style="font-size:12.5px">
        <p>tinystudio is an MCP server. Register it once and your coding assistant can run the whole pipeline in natural language:</p>
        <pre class="mono" style="white-space:pre-wrap;background:var(--panel-2);padding:10px;border-radius:8px">claude mcp add --scope user tinystudio -- ${esc(st.mcp.command)}</pre>
        <p class="muted">Codex: add <span class="mono">[mcp_servers.tinystudio]</span> with the same command and args to <span class="mono">~/.codex/config.toml</span> (Codex keeps one server process per thread; disable the entry when you are not using it). Claude Desktop: add it under <span class="mono">mcpServers</span>.</p>
      </div></div>
    </div>
    <div class="panel assist-page" id="asPage"></div></div>`, [{ label: "Assistant" }]);
  if (ASSIST.open) toggleAssistant(false);
  renderAssistant($("#asPage", content), { full: true });
}

  function destroy() {
    document.removeEventListener("keydown", onKeyDown);
    if (bridge && window.tinystudio === bridge) delete window.tinystudio;
    bridge = null;
    if (mountedHost) { delete mountedHost.dataset.mounted; mountedHost.innerHTML = ""; mountedHost = null; }
  }
  return { assistRefreshUI, assistRedraw, assistScopeChanged, md, bubbleHtml, assistantModelOptions, assistantProviderOptions, assistantModelNotice, assistDrawLog, assistRenderRemote, renderAssistant, toggleAssistant, mountAssistant, pageAssistant, destroy };
}
