// Request budgets and streaming own the turn; persistence owns durable revisions.
export function createChat({ state, assistantState: ASSIST, http, feedback, conversation, context, refresh, pollTasks }) {
  const api = (...args) => http.api(...args);
  const rawFetch = (...args) => http.rawFetch(...args);
  const post = (...args) => http.post(...args);
  const assistKey = (...args) => conversation.assistKey(...args);
  const assistResponseError = (...args) => conversation.assistResponseError(...args);
  const assistLoad = (...args) => conversation.assistLoad(...args);
  const assistSave = (...args) => conversation.assistSave(...args);
  const uiPerform = (...args) => context.uiPerform(...args);
  const captureContext = (...args) => context.captureContext(...args);
  const pageContextReady = (...args) => context.pageContextReady(...args);
  const toast = (...args) => feedback.toast(...args);
  const assistRefreshUI = () => refresh();
// Keep the complete JSON request comfortably below the server's 256,000-byte limit.
const requestBytes = (data) => new TextEncoder().encode(JSON.stringify(data)).length;
function boundedData(data, limit) {
  if (requestBytes(data) <= limit) return data;
  let strings = 4000, items = 40;
  const compact = (value, depth = 0) => {
    if (typeof value === "string") return value.length > strings ? value.slice(0, strings) + ` [omitted ${value.length - strings} characters]` : value;
    if (depth > 10) return { omission: "Nested data omitted beyond depth 10" };
    if (Array.isArray(value)) return value.slice(0, items).map((v) => compact(v, depth + 1)).concat(value.length > items ? [{ omission: `${value.length - items} additional items omitted` }] : []);
    if (value && typeof value === "object") {
      const pairs = Object.entries(value); const retained = Object.fromEntries(pairs.slice(0, items).map(([key, v]) => [key, compact(v, depth + 1)]));
      if (pairs.length > items) retained._omission = `${pairs.length - items} additional fields omitted`;
      return retained;
    }
    return value;
  };
  for (;;) {
    const result = compact(data);
    if (requestBytes(result) <= limit) return result;
    strings = Math.max(16, Math.floor(strings / 2)); items = Math.max(1, Math.floor(items / 2));
    if (strings === 16 && items === 1) return { omission: "Source exceeds this turn's context budget; inspect a narrower row or chart." };
  }
}
function prepareChatBody(body, project) {
  const context = body.context ? { ...body.context, api: [], omissions: [] } : null;
  if (context) {
    if (body.context.selected_data) {
      context.selected_data = boundedData(body.context.selected_data, 110000);
      if (requestBytes(context.selected_data) < requestBytes(body.context.selected_data)) context.omissions.push("Selected source was compacted; omission markers identify removed content.");
    }
    for (const record of body.context.api || []) {
      const retained = { path: record.path, data: boundedData(record.data, 12000) };
      if (requestBytes(context.api) + requestBytes(retained) > 60000) { context.omissions.push(`API source omitted for size: ${record.path}`); continue; }
      context.api.push(retained);
      if (requestBytes(retained.data) < requestBytes(record.data)) context.omissions.push(`API source compacted for size: ${record.path}`);
    }
  }
  const outgoing = { ...body, project, context, messages: body.messages.map((message) => ({ ...message })) };
  if (requestBytes(outgoing.messages.at(-1) || {}) > 60000) throw new Error("Message exceeds 60 KB; send a shorter request or pick a narrower data element.");
  let omitted = 0;
  while (requestBytes(outgoing) > 235000 && outgoing.messages.length > 1) { outgoing.messages.shift(); omitted++; }
  if (omitted) {
    outgoing.context ||= { route: location.hash, project, omissions: [] };
    outgoing.context.omissions ||= [];
    outgoing.context.omissions.push(`${omitted} older conversation messages omitted from this request for size. Stored history is unchanged.`);
  }
  if (requestBytes(outgoing) > 240000) throw new Error("This request exceeds the context budget; clear the conversation or select a narrower row.");
  return outgoing;
}

// --- streaming turn shared by the widget and the Assistant page
async function streamChat(body, onEvent, turn) {
  const response = await rawFetch("/api/assistant/chat", { method: "POST", headers: { "X-Conversation-Owner": String(turn.session.user) }, body: JSON.stringify(prepareChatBody(body, turn.project)), signal: turn.controller.signal });
  if (!response.ok) throw await assistResponseError(turn.session, response);
  const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
  for (;;) {
    const { value, done } = await reader.read(); if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx; while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const line = buffer.slice(0, idx).trim(); buffer = buffer.slice(idx + 2);
      if (!line.startsWith("data:")) continue;
      const ev = JSON.parse(line.slice(5));
      if (ev.type === "keepalive") continue;
      if (ev.type === "ui_request") {
        let result; try { result = turn.scope === assistKey() && ASSIST.turn === turn ? await uiPerform(ev.action, ev.args || {}, { allowActions: body.allow_destructive }) : { error: "Conversation changed; interface action cancelled" }; } catch (e) { result = { error: e.message }; }
        if (turn.scope !== assistKey() || ASSIST.turn !== turn) result = { error: "Conversation changed; interface result discarded" };
        onEvent({ type: "ui_action", action: ev.action, args: ev.args, result });
        post("/api/assistant/ui-result", { id: ev.id, result }).catch((e) => toast(`assistant: ${e.message}`, true));
        continue;
      }
      onEvent(ev);
    }
  }
}
async function loadAssistantModels(force = false) {
  const session = ASSIST.session;
  if (!ASSIST.models || force) ASSIST.models = await api("/api/assistant/models");
  const prov = ASSIST.models;
  if (ASSIST.session !== session) return prov;
  if (!prov[ASSIST.provider]?.available) ASSIST.provider = prov.anthropic.available ? "anthropic" : prov.openai.available ? "openai" : ASSIST.provider;
  const ids = (prov[ASSIST.provider]?.models || []).map((m) => m.id);
  if (!ASSIST.model || (ids.length && !ids.includes(ASSIST.model))) ASSIST.model = prov[ASSIST.provider]?.default || ids[0] || null;
  return ASSIST.models;
}

async function sendMessage({ text, clearInput, draw, redrawReply, onUsage }) {
    const session = ASSIST.session;
    if (!text || !session.loaded || session.busy || session.dirty || session.error) return;
    await assistLoad(session);
    if (session !== ASSIST.session || !session.loaded || session.busy || session.dirty || session.error) return;
    clearInput();
    const turn = { scope: session.key, project: session.project, controller: new AbortController(), session };
    session.turn = turn; session.busy = true;
    session.history.push({ role: "user", content: text });
    draw(); assistRefreshUI();
    let reply, streamError;
    const active = () => session.turn === turn && ASSIST.session === session;
    const redraw = () => { if (active()) redrawReply(reply); };
    try {
      // A durable user turn is the prerequisite to starting a model request.
      await assistSave(session);
      if (!active()) return;
      const body = { conversation_revision: session.revision, provider: session.provider, model: session.model, allow_destructive: ASSIST.destructive, with_ui: true, messages: session.history.map((m) => ({ role: m.role, content: m.content })), context: ASSIST.attach ? captureContext() : pageContextReady() && ASSIST.selection ? { route: location.hash, project: session.project, selected_data: ASSIST.selection } : null };
      reply = { role: "assistant", content: "", calls: [] }; session.history.push(reply); session.dirty = true; session.streaming = true; draw();
      await chat.streamChat(body, (ev) => {
        if (!active()) return;
        if (ev.type === "text") reply.content += ev.delta;
        else if (ev.type === "tool_call") reply.calls.push({ id: ev.id, name: ev.name, input: ev.input, kind: ev.name.startsWith("ui_") ? "ui" : "tool" });
        else if (ev.type === "tool_result") { const c = reply.calls.find((x) => x.id === ev.id && x.output === undefined) || reply.calls[reply.calls.length - 1]; if (c) c.output = ev.output; pollTasks(); }
        else if (ev.type === "conversation_saved") { session.revision = ev.revision; session.etag = null; session.dirty = false; session.streaming = false; session.pending = false; session.error = ""; session.conflict = false; conversation.notifySaved(ev.revision); assistRefreshUI(); }
        else if (ev.type === "conversation_error") { session.error = ev.message || "The database conversation changed"; session.streaming = false; session.pending = false; session.conflict = ev.conflict === true; session.dirty = true; assistRefreshUI(); }
        else if (ev.type === "error") streamError = new Error(ev.message);
        else if (ev.type === "done") { session.usage = `${ev.model} · ${ev.usage.input_tokens} in / ${ev.usage.output_tokens} out`; onUsage(session.usage); }
        redraw();
      }, turn);
      if (!active()) return;
      if (streamError) throw streamError;
      if (!reply.content && !reply.calls.length) reply.content = "(no reply)";
      redraw();
    } catch (error) {
      if (!active()) return;
      if (reply && !reply.content && !reply.calls.length) session.history.pop();
      draw(); if (error.name !== "AbortError") throw error;
    } finally {
      if (active()) {
        // Keep replies in memory on failure and disable send until saved or explicitly discarded.
        try { if (reply && session.dirty && !session.conflict) { session.error ||= "Reply has not been confirmed saved. Refresh to check the database, or retry saving."; } }
        finally { session.busy = false; session.turn = null; assistRefreshUI(); conversation.requestCheck(); }
      }
    }
}

  const chat = { requestBytes, boundedData, prepareChatBody, streamChat, loadAssistantModels, sendMessage };
  return chat;
}
