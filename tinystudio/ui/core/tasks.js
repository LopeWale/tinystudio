import { $, $$ } from "../ui/dom.js?v=c1757fa5eddd";
import { esc } from "../ui/format.js?v=c1757fa5eddd";

export function createTaskDrawer({ state, http, feedback, navigation }) {
  let timer = null, disposed = false, scope = null, generation = 0, pending = null;
  const refreshHolds = new Set();
  let refreshPending = false;
  const scopeKey = () => `${state.user?.id ?? ""}:${state.project ?? ""}`;
  function syncScope() {
    const next = scopeKey();
    if (scope === next) return;
    scope = next; generation++;
    clearTimeout(timer); timer = null;
    pending?.controller.abort(); pending = null;
    refreshHolds.clear(); refreshPending = false;
    state.tasks = [];
  }
  function holdRefresh() {
    syncScope();
    const hold = {};
    refreshHolds.add(hold);
    return (refresh = true) => {
      if (!refreshHolds.delete(hold)) return;
      if (!refresh) refreshPending = false; // navigation already refreshes the page
      if (!disposed && !refreshHolds.size && refreshPending && scope === scopeKey()) {
        refreshPending = false; navigation.render();
      }
    };
  }
  function showTasks() { $("#tasks").hidden = false; }
  function queued(result, what) { if (!result || !result.queued) return false; feedback.toast(`${what} runs as a background task (large dataset); the page refreshes when it finishes`); showTasks(); pollTasks(); return true; }
  function pollTasks() {
    if (disposed) return Promise.resolve();
    syncScope();
    if (pending) return pending.promise;
    clearTimeout(timer); timer = null;
    const request = { generation, scope, controller: new AbortController() };
    pending = request;
    const current = () => !disposed && request.generation === generation && request.scope === scopeKey();
    const schedule = () => { if (current()) timer = setTimeout(pollTasks, 2500); };
    request.promise = (async () => {
    let tasks;
    try { tasks = await http.background("/api/tasks?include_result=false", { signal: request.controller.signal }); }
    catch (error) { if (error.name !== "AbortError" && error.name !== "AuthenticationRequiredError") schedule(); return; }
    if (!current()) return;
    const wasRunning = new Set(state.tasks.filter((t) => t.state === "running" || t.state === "queued").map((t) => t.id));
    state.tasks = tasks;
    const box = $("#tasks");
    box.innerHTML = `<div class="head"><h2>Tasks</h2><div class="right"><button class="btn sm" id="tHide">Hide</button></div></div>` + (tasks.length ? tasks.slice(0, 6).map((t) => `<div class="task"><span class="pill ${t.state}">${t.state}</span> <b>${esc(t.kind)}</b> <span class="muted mono" style="font-size:11px">${esc((t.started || t.created || "").slice(11, 19))}</span>${t.state === "running" || t.state === "queued" ? ` <button class="btn sm" style="float:right" data-stop="${t.id}">Stop</button>` : ""}<div class="mono muted" style="font-size:11px;word-break:break-all;margin-top:4px">${esc(JSON.stringify(t.args || {})).slice(0, 160)}</div><pre>${esc((t.log_tail || []).slice(-12).join("\n"))}</pre></div>`).join("") : `<div class="task muted">Nothing running.</div>`);
    $("#tHide", box).onclick = () => box.hidden = true;
    $$("[data-stop]", box).forEach((b) => b.onclick = feedback.guard(async () => { await http.post(`/api/tasks/${b.dataset.stop}/stop`, {}); pollTasks(); }));
    const running = tasks.some((t) => t.state === "running" || t.state === "queued");
    const finished = tasks.filter((t) => wasRunning.has(t.id) && t.state !== "running" && t.state !== "queued");
    if (finished.length) {
      feedback.toast("a task finished");
      // Profiling only produces a task result; refreshing would close its waiting dialog.
      if (finished.some((t) => t.kind !== "dataset.profile")) {
        if (refreshHolds.size) refreshPending = true;
        else navigation.render();
      }
    }
    if (running) { showTasks(); schedule(); }
    })().finally(() => { if (pending === request) pending = null; });
    return request.promise;
  }
  return { showTasks, pollTasks, queued, syncScope, holdRefresh, destroy() { disposed = true; generation++; refreshHolds.clear(); refreshPending = false; clearTimeout(timer); timer = null; pending?.controller.abort(); pending = null; } };
}
