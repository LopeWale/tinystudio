import { ConversationSync } from "./conversation-sync.js?v=82e74d4b5ecb";

// Each project/account session owns its revision queue and asynchronous work.
export function createConversations({ state, http, presentation }) {
  const rawFetch = (...args) => http.rawFetch(...args);
  const assistRefreshUI = () => presentation.refresh();
  const assistRenderRemote = () => presentation.remote();
const ASSIST = { open: false, models: null, attach: true, destructive: false, scope: null, selection: null, picking: false, session: null };
const assistantSessions = new Map();
let assistantSync = null;
const assistKey = () => `tinystudio.assistant.v2.${encodeURIComponent(state.user?.id || state.user?.email || "anonymous")}.${encodeURIComponent(state.project || "none")}`;
// Each scope owns its asynchronous work. Late responses can never change the active project.
for (const name of ["history", "provider", "model", "busy", "usage", "turn"]) Object.defineProperty(ASSIST, name, {
  get: () => ASSIST.session?.[name], set: (value) => { if (ASSIST.session) ASSIST.session[name] = value; },
});
function assistPreferences() {
  if (!ASSIST.scope) return;
  try { localStorage.setItem(`${ASSIST.scope}.preferences`, JSON.stringify({ attach: ASSIST.attach, open: ASSIST.open })); } catch (_) { /* preferences are optional */ }
}
function assistSyncStatus() {
  if (ASSIST.session?.ownerMismatch) return "Sync paused · account changed";
  const sync = ASSIST.session?.syncState;
  if (sync === "offline") return "Offline · sync resumes when connected";
  if (sync === "error") return "Sync unavailable · retrying automatically";
  return "Syncing automatically";
}
function assistStatus() {
  const session = ASSIST.session;
  if (!session || !session.loaded && !session.error) return "Loading conversation…";
  if (session.error) return `${session.conflict ? "Conflict" : "Not saved"}: ${session.error}`;
  if (session.saving) return "Saving…";
  if (session.dirty) return "Unsaved conversation";
  return "Saved to database";
}
async function assistResponseError(session, response) {
  let detail = response.statusText;
  try { detail = (await response.json()).detail || detail; } catch (_) {}
  const error = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  if (response.headers?.get("x-conversation-owner-mismatch") === "true") {
    error.ownerMismatch = true; session.ownerMismatch = true; session.error = error.message; session.conflict = true;
    if (session === ASSIST.session) { assistantSync?.stop(); assistRefreshUI(); }
  }
  error.status = response.status;
  return error;
}
async function assistRequest(session, options = {}, { conditional = false } = {}) {
  if (session.ownerMismatch) throw Object.assign(new Error("The signed-in account changed. Reload the page before continuing."), { ownerMismatch: true, status: 403 });
  if (session.user !== (state.user?.id || state.user?.email)) throw new Error("Sign in as the conversation owner before retrying");
  const headers = { ...options.headers, "X-Conversation-Owner": String(session.user) };
  if (conditional && session.etag) headers["If-None-Match"] = session.etag;
  const response = await rawFetch(`/api/p/${encodeURIComponent(session.project)}/assistant/conversation`, { ...options, headers, cache: "no-store" });
  if (conditional && response.status === 304) return null;
  if (!response.ok) throw await assistResponseError(session, response);
  return { ...await response.json(), etag: response.headers?.get("etag") || null };
}
function assistEnqueue(session, work) {
  const pending = session.queue.catch(() => {}).then(work);
  session.queue = pending;
  return pending;
}
function assistSnapshot(session) {
  // Preserve the full conversation; request-size compaction is separate from storage.
  return JSON.parse(JSON.stringify({ revision: session.revision, history: session.history, provider: session.provider || "", model: session.model || "" }));
}
function assistSameSnapshot(session, snapshot) {
  const canonical = (value) => Array.isArray(value) ? value.map(canonical) : value && typeof value === "object" ? Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])])) : value;
  const content = (value) => ({ history: value.history, provider: value.provider || "", model: value.model || "" });
  return JSON.stringify(canonical(content(assistSnapshot(session)))) === JSON.stringify(canonical(content(snapshot)));
}
function assistAdopt(session, snapshot) {
  session.revision = snapshot.revision; session.history = snapshot.history;
  session.etag = snapshot.etag || null;
  session.provider = snapshot.provider || session.provider; session.model = snapshot.model || session.model;
  session.loaded = true; session.dirty = false; session.error = ""; session.conflict = false; session.pending = false; session.streaming = false;
  session.importing = false; session.legacy = null; assistForgetLegacy(session);
}
function assistForgetLegacy(session) {
  try { localStorage.removeItem(session.key); } catch (_) { /* revision still prevents reimport */ }
}
function assistLoad(session = ASSIST.session, { refresh = false, discard = false, signal } = {}) {
  if (!session) return Promise.resolve();
  return assistEnqueue(session, async () => {
    if (session.busy || session.dirty && !discard && !session.pending || session.loaded && !refresh) return;
    try {
      const snapshot = await assistRequest(session, { signal });
      if (signal?.aborted) return;
      if (session.pending && snapshot.revision <= session.revision && !discard) return;
      if (snapshot.revision === 0 && !session.loaded && session.legacy?.history?.length) {
        session.revision = 0; session.history = session.legacy.history;
        session.provider = session.legacy.provider || session.provider; session.model = session.legacy.model || session.model;
        session.loaded = true; session.dirty = true; session.importing = true;
        try {
          const imported = await assistRequest(session, { method: "PUT", body: JSON.stringify(assistSnapshot(session)), signal });
          if (signal?.aborted) return;
          assistAdopt(session, imported);
          if (ASSIST.session === session) assistantSync?.notifySaved(imported.revision);
        } catch (error) {
          if (error.status !== 409) throw error;
          const winner = await assistRequest(session, { signal });
          if (signal?.aborted) return;
          assistAdopt(session, winner);
        }
      } else assistAdopt(session, snapshot);
    } catch (error) {
      if (!signal?.aborted) { session.error = error.message; session.conflict = error.ownerMismatch === true || error.status === 409; }
      throw error;
    }
    finally {
      if (signal?.aborted && session.importing && session.dirty && !session.ownerMismatch) {
        session.error ||= "Browser history import has not been confirmed saved. Retry will check the database before saving again.";
      }
      if (ASSIST.session === session && (!signal?.aborted || session.importing && session.dirty)) {
        assistRenderRemote();
      }
    }
  });
}
function assistSave(session = ASSIST.session) {
  if (!session) return Promise.resolve();
  if (session.ownerMismatch) return Promise.reject(new Error("The signed-in account changed. Reload the page before saving."));
  session.dirty = true; session.edits = (session.edits || 0) + 1;
  assistPreferences();
  return assistEnqueue(session, async () => {
    if (!session.loaded) throw new Error("Conversation must load before saving");
    if (session.conflict) throw new Error("Reload the newer database conversation before continuing");
    session.saving = true; session.error = ""; assistRefreshUI();
    try {
      const edits = session.edits;
      const saved = await assistRequest(session, { method: "PUT", body: JSON.stringify(assistSnapshot(session)) });
      session.revision = saved.revision; session.etag = saved.etag; session.dirty = session.edits !== edits; session.conflict = false;
      session.importing = false; session.legacy = null; assistForgetLegacy(session);
      if (ASSIST.session === session) assistantSync?.notifySaved(saved.revision);
    } catch (error) { session.error = error.message; session.conflict = error.ownerMismatch === true || error.status === 409; throw error; }
    finally { session.saving = false; assistRefreshUI(); }
  });
}
// Sync reads share the write queue, but never write or discard local work.
function assistCheckRemote(session, { signal } = {}) {
  const active = () => !signal?.aborted && session === ASSIST.session && session.key === assistKey();
  if (!active() || session.ownerMismatch) return Promise.resolve();
  if (!session.loaded) return assistLoad(session, { refresh: true, signal }).then(() => session.revision);
  return assistEnqueue(session, async () => {
    if (!active() || session.busy || session.saving) return session.revision;
    const edits = session.edits, revision = session.revision;
    const snapshot = await assistRequest(session, { signal }, { conditional: true });
    // The user can start editing while the GET is in flight.
    if (!active() || session.busy || session.saving || edits !== session.edits || revision !== session.revision || !snapshot) return session.revision;
    if (snapshot.revision < session.revision) return session.revision;
    if (session.dirty || session.conflict) {
      if (!session.conflict && assistSameSnapshot(session, snapshot)) {
        assistAdopt(session, snapshot); assistRenderRemote();
      } else if (snapshot.revision > session.revision) {
        session.conflict = true;
        session.error = "A newer conversation arrived. Your unsaved messages are retained; load the saved version when you are ready to discard them.";
        assistRefreshUI();
      }
      return session.revision;
    }
    session.etag = snapshot.etag;
    if (snapshot.revision > session.revision) {
      assistAdopt(session, snapshot); session.usage = "";
      assistRenderRemote();
    }
    return session.revision;
  });
}
function startAssistantSync(session = ASSIST.session) {
  assistantSync?.stop(); assistantSync = null;
  if (!session?.project || !state.user || session !== ASSIST.session || session.ownerMismatch) return;
  assistantSync = new ConversationSync({
    scope: session.key,
    check: (options) => assistCheckRemote(session, options),
    onStatus: ({ state: syncState }) => {
      if (session !== ASSIST.session) return;
      session.syncState = syncState; assistRefreshUI();
    },
  });
  assistantSync.start();
}
function syncAssistantScope() {
  const key = assistKey(); if (key === ASSIST.scope) return;
  assistantSync?.stop(); assistantSync = null;
  const previous = ASSIST.session;
  if (previous) {
    previous.turn?.controller.abort(); previous.turn = null; previous.busy = false;
    if (previous.loaded && previous.dirty && !previous.streaming) assistSave(previous).catch(() => {});
    if (previous.streaming) { previous.pending = true; previous.error ||= "Waiting for the server to save the interrupted reply."; }
  }
  ASSIST.scope = key; ASSIST.destructive = false; ASSIST.selection = null;
  let session = assistantSessions.get(key);
  if (!session) {
    let legacy = null; try { legacy = JSON.parse(localStorage.getItem(key) || "null"); } catch (_) {}
    session = { key, user: state.user?.id || state.user?.email, project: state.project, provider: "anthropic", model: null, history: [], revision: 0, loaded: false, dirty: false, saving: false, error: "", conflict: false, busy: false, turn: null, usage: "", queue: Promise.resolve(), legacy };
    assistantSessions.set(key, session);
  }
  ASSIST.session = session;
  try { const prefs = JSON.parse(localStorage.getItem(`${key}.preferences`) || "{}"); ASSIST.attach = prefs.attach ?? session.legacy?.attach ?? true; ASSIST.open = prefs.open ?? session.legacy?.open ?? false; } catch (_) { ASSIST.attach = true; ASSIST.open = false; }
  state.pageContext = null;
  presentation.scopeChanged();
  if (session.project && state.user) startAssistantSync(session);
}

  const onPageHide = () => assistantSync?.stop();
  const onPageShow = () => startAssistantSync();
  let mounted = false;
  function mount() {
    if (mounted) return; mounted = true;
    window.addEventListener("pagehide", onPageHide);
    window.addEventListener("pageshow", onPageShow);
    if (ASSIST.session) startAssistantSync();
  }
  function destroy() {
    window.removeEventListener("pagehide", onPageHide);
    window.removeEventListener("pageshow", onPageShow); mounted = false;
    assistantSync?.stop(); assistantSync = null;
    for (const session of assistantSessions.values()) {
      session.turn?.controller.abort(); session.turn = null; session.busy = false;
      if (session.streaming) { session.pending = true; session.error ||= "Waiting for the server to save the interrupted reply."; }
    }
  }
  return { state: ASSIST, assistKey, assistPreferences, assistSyncStatus, assistStatus, assistResponseError, assistRequest, assistEnqueue, assistSnapshot, assistSameSnapshot, assistAdopt, assistForgetLegacy, assistLoad, assistSave, assistCheckRemote, startAssistantSync, syncAssistantScope, mount, destroy, requestCheck: () => assistantSync?.requestCheck(), notifySaved: (revision) => assistantSync?.notifySaved(revision) };
}
