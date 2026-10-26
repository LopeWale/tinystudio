import { $, $$ } from "../ui/dom.js?v=c96cb305d835";
import { esc } from "../ui/format.js?v=c96cb305d835";
import { ICONS } from "../ui/layout.js?v=c96cb305d835";

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
  let pickCollapsed = false;
  const onKeyDown = (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); toggleAssistant(); }
    else if (e.key === "Escape" && ASSIST.picking) { e.preventDefault(); setAssistantPicking(false); }
    else if (e.key === "Escape" && ASSIST.open && !$(".modal-back")) { e.preventDefault(); toggleAssistant(false); }
  };
function updateDrawerVisibility() {
  const visible = ASSIST.open && !pickCollapsed;
  const drawer = $("#assist .drawer"); if (drawer) drawer.hidden = !visible;
  $("#assist .fab")?.classList.toggle("on", ASSIST.open);
  $("#assist .fab")?.setAttribute?.("aria-expanded", String(visible));
  document.body?.classList.toggle("assistant-open", Boolean(visible));
  const picker = $("#assistantPicker"); if (picker) picker.hidden = !ASSIST.picking;
}
function assistPickingChanged(on) {
  pickCollapsed = Boolean(on && ASSIST.open);
  updateDrawerVisibility();
  if (!on && ASSIST.open) $("#asInput", $("#assist .drawer") || document)?.focus();
}
function assistSelectionChanged() {
  const drawer = $("#assist .drawer"); if (drawer && ASSIST.open) assistRenderSelection(drawer);
  const page = $("#asPage"); if (page) assistRenderSelection(page);
  if (ASSIST.open) $("#assist .drawer #asInput")?.focus();
}
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
  updateDrawerVisibility();
  renderAssistant(drawer, { preserveDraft: false });
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
  log.innerHTML = ASSIST.history.map((m) => m.role === "user" ? `<div class="bubble user">${esc(m.content)}</div>` : `<div class="bubble assistant">${bubbleHtml(m)}</div>`).join("") || `<div class="assistant-empty empty"><b>What would you like to explore?</b><p>Ask about your data, compare a result, or plan your next training run.</p><span class="hint">Include this page for context, or pick a row or chart to ask about it.</span></div>`;
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
function renderAssistant(container, { full = false, preserveDraft = true } = {}) {
  const previousInput = $("#asInput", container);
  const draft = preserveDraft && previousInput ? { value: previousInput.value, start: previousInput.selectionStart, end: previousInput.selectionEnd, focused: document.activeElement === previousInput } : null;
  container.innerHTML = `
    <div class="assistant-header">
      <div class="assistant-title"><h2>${full ? "Your conversation" : "Assistant"}</h2><span class="hint">${full ? "Explore, understand, and act on your project." : "A little help, wherever you work."}</span></div>
      <div class="row"><button class="btn small" id="asReset" title="Clear the conversation" ${ASSIST.busy || !ASSIST.session.loaded || ASSIST.session.dirty || ASSIST.session.error ? "disabled" : ""}>Clear chat</button>
      ${full ? "" : `<a class="btn small" href="#/assistant" aria-label="Open Assistant full page" title="Open full page">⤢</a><button class="btn small" id="asClose" aria-label="Close Assistant" title="Close Assistant (Esc)">✕</button>`}</div>
    </div>
    <div class="assistant-controls">
      <div class="field"><label for="asProvider">Provider</label><select id="asProvider">${assistantProviderOptions()}</select></div>
      <div class="field"><label for="asModel">Model</label><select id="asModel" title="Models from your provider's live catalog">${assistantModelOptions()}</select></div>
    </div>
    <div id="asModelNotice">${assistantModelNotice()}</div>
    <div class="assistant-context">
      <label class="chk" title="Include the route, visible content, fetched data, and your selection"><input type="checkbox" id="asAttach" ${ASSIST.attach ? "checked" : ""}> Include this page</label>
      <button class="btn small" id="asPick" aria-pressed="${Boolean(ASSIST.picking)}" title="Pick a row, chart, or panel to attach its data">${ASSIST.picking ? "Cancel pick" : "Pick data"}</button>
      <label class="chk" title="Allow interface changes and destructive tools for this session; deletions still require confirmation"><input type="checkbox" id="asDestructive" ${ASSIST.destructive ? "checked" : ""}> Allow actions / deletions</label>
    </div>
    <div class="assistant-selection" id="asSelection" hidden></div>
    <div class="assistant-status"><span id="asPersistence">${esc(assistStatus())}</span><span id="asSyncStatus" role="status" aria-live="polite">${esc(assistSyncStatus())}</span> <button class="btn small" id="asRetry" ${ASSIST.session.error && !ASSIST.session.conflict ? "" : "hidden"}>Retry save / load</button> <button class="btn small" id="asReload" hidden>Load saved version</button></div>
    <div class="chatlog ${full ? "" : "compact"}" id="asLog" role="log" aria-label="Assistant conversation" aria-live="polite" aria-relevant="additions text"></div>
    <div class="composer assistant-compose"><label class="sr-only" for="asInput">Message Assistant</label><textarea id="asInput" rows="3" aria-label="Message Assistant" placeholder="Ask about your project…"></textarea><button class="btn primary" id="asSend" ${ASSIST.busy || !ASSIST.session.loaded || ASSIST.session.dirty || ASSIST.session.error ? "disabled" : ""}>Send</button></div>
    <div class="assistant-shortcuts hint">Enter to send · Shift + Enter for a new line</div>
    <div class="muted mono" id="asUsage">${esc(ASSIST.usage || "")}</div>`;
  if (draft) { const input = $("#asInput", container); input.value = draft.value; if (draft.focused) input.focus(); input.setSelectionRange?.(draft.start, draft.end); }
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
  $("#asInput", container).onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } };
}

function toggleAssistant(open) {
  ASSIST.open = open ?? !ASSIST.open;
  const drawer = $("#assist .drawer"); if (!drawer) return;
  updateDrawerVisibility();
  assistPreferences();
  if (ASSIST.open) {
    if (!$("#asInput", drawer)) renderAssistant(drawer);
    else assistRenderSelection(drawer);
    conversation.requestCheck(); $("#asInput", drawer)?.focus();
    guard(async () => { const session = ASSIST.session; await loadAssistantModels(); if (session === ASSIST.session) assistRenderRemote(); })();
  } else $("#assist .fab")?.focus();
}
function mountAssistant() {
  const host = document.getElementById("assist"); if (!host || host.dataset.mounted) return;
  host.dataset.mounted = "1";
  conversation.mount(); context.mount(); syncAssistantScope();
  host.innerHTML = `<button class="fab" aria-label="Open Assistant" aria-expanded="false" aria-controls="assistantDrawer" title="Assistant (⌘K / Ctrl+K)">${ICONS.assistant}</button><div class="assistant-picker" id="assistantPicker" role="status" hidden>Pick a row, chart, or panel. <button class="btn small" id="assistantCancelPick">Cancel</button></div><div class="drawer" id="assistantDrawer" role="complementary" aria-label="Assistant" hidden></div>`;
  $("#assist .fab").onclick = () => { if (ASSIST.picking) { setAssistantPicking(false); toggleAssistant(true); } else toggleAssistant(); };
  const cancelPick = $("#assistantCancelPick"); if (cancelPick) cancelPick.onclick = () => setAssistantPicking(false);
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
  const content = layout("assistant", `<div class="page-head"><div><span class="eyebrow">Project workspace</span><h1>Assistant</h1></div><span class="meta">Turn questions into your next step. Available on any page with ⌘K or Ctrl+K.</span></div>
    <div class="assistant-workspace">
      <div class="panel assist-page assistant-conversation" id="asPage"></div>
      <aside class="assistant-help" aria-label="Assistant guide">
        <details class="panel"><summary>Working with page context</summary><div class="body">
          <p><b>Include this page</b> shares the current route, visible content, fetched data, and text selection. Use <b>Pick data</b> to attach a specific row, chart, or panel.</p>
          <p>Ask the Assistant to explain results or plan a workflow. Turn on <b>Allow actions / deletions</b> when you want it to operate the interface or run tools. Destructive actions still ask for confirmation.</p>
          <p class="muted">Conversations are saved separately for each project. Provider credentials come from the server environment.</p>
        </div></details>
        <details class="panel"><summary>Connect your coding assistant</summary><div class="body">
          <p>TinyStudio exposes ${st.mcp.tools} tools through MCP. Connect Claude Code, Codex, or Claude Desktop to work with the same pipeline.</p>
          <pre class="mono">claude mcp add --scope user tinystudio -- ${esc(st.mcp.command)}</pre>
          <p class="muted">For Codex, configure <code>[mcp_servers.tinystudio]</code> in <code>~/.codex/config.toml</code> with the same command and arguments. Claude Desktop uses <code>mcpServers</code>.</p>
        </div></details>
      </aside>
    </div>`, [{ label: "Assistant" }]);
  if (ASSIST.open) toggleAssistant(false);
  renderAssistant($("#asPage", content), { full: true });
}

  function destroy() {
    document.removeEventListener("keydown", onKeyDown);
    document.body?.classList.toggle("assistant-open", false);
    pickCollapsed = false;
    if (bridge && window.tinystudio === bridge) delete window.tinystudio;
    bridge = null;
    if (mountedHost) { delete mountedHost.dataset.mounted; mountedHost.innerHTML = ""; mountedHost = null; }
  }
  return { assistPickingChanged, assistSelectionChanged, assistRefreshUI, assistRedraw, assistScopeChanged, md, bubbleHtml, assistantModelOptions, assistantProviderOptions, assistantModelNotice, assistDrawLog, assistRenderRemote, renderAssistant, toggleAssistant, mountAssistant, pageAssistant, destroy };
}
