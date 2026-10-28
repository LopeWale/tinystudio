import { $, $$ } from "../ui/dom.js?v=c1757fa5eddd";

// Context belongs to the active render, never to a prior route or account.
export function createContext({ state, assistantState: ASSIST, navigation, redraw, onPickingChange = () => {}, onSelectionChanged = null }) {
  const renderAssistant = (...args) => redraw(...args);
function safeContextData(data) {
  if (Array.isArray(data)) return data.map(safeContextData);
  if (data && typeof data === "object") return Object.fromEntries(Object.entries(data).map(([key, value]) => [key, /(?:password|secret|token|authorization|credential|api.?key)/i.test(key) ? "[redacted]" : safeContextData(value)]));
  return data;
}
// Capture only GET responses requested by this route and project render. Never retain credentials.
function recordApi(path, data, capture = state.pageContext) {
  if (!capture || capture !== state.pageContext || capture.project !== state.project || capture.route !== (location.hash || "#/")) return;
  if (!path.startsWith("/api/") || /^\/api\/(?:tasks|assistant|settings|auth|keys|users)(?:[/?]|$)/.test(path)) return;
  const record = { path, data: safeContextData(data) };
  capture.api = capture.api.filter((r) => r.path !== path).concat([record]);
}
function isCurrentRender(view) { return !!view && view === state.pageContext && view.project === state.project && view.route === (location.hash || "#/"); }
function pageContextReady() { return isCurrentRender(state.domContext); }
function assertRender(view) {
  if (!view || isCurrentRender(view)) return;
  const error = new Error("Page changed while loading; stale response discarded"); error.name = "StaleRenderError"; throw error;
}
const contextBindings = new WeakMap();
let contextSequence = 0;
function contextRecord(label, data) { return { label, source: "rendered-data", data: safeContextData(data), route: location.hash || "#/", project: state.project, user: state.user?.id, origin: state.pageContext }; }
function bindContext(el, label, data) { if (el) contextBindings.set(el, contextRecord(label, data)); }
// HTML renderers retain the exact object used to draw this row/chart, without serializing it into DOM attributes.
function dataContext(label, data) {
  const view = state.pageContext; if (!view) return "";
  const id = `ctx_${++contextSequence}`;
  (view.bindings ||= new Map()).set(id, contextRecord(label, data));
  return `data-context-id="${id}"`;
}
function bindingFor(el) { return contextBindings.get(el) || state.pageContext?.bindings?.get(el.dataset?.contextId); }
function validBinding(binding) { return binding && binding.origin === state.pageContext && binding.project === state.project && binding.user === state.user?.id; }
function publicBinding(binding) { const { origin, user, ...data } = binding; return { ...data, source_route: data.route, route: location.hash || "#/" }; }
function sensitiveElement(el) {
  if (el?.closest?.('[data-assistant-sensitive], input[type="password"]')) return true;
  const field = el?.closest?.("input, textarea");
  return !!field && /password|token|secret|credential|api.?key/i.test(`${field.id} ${field.name} ${labelFor(field)}`);
}
function safeSelection() {
  if (!pageContextReady()) return "";
  const selected = window.getSelection?.();
  if (!selected || location.hash === "#/api-keys" || sensitiveElement(selected.anchorNode?.parentElement) || sensitiveElement(selected.focusNode?.parentElement)) return "";
  for (let i = 0; i < (selected.rangeCount || 0); i++) {
    const range = selected.getRangeAt(i);
    if ($$('[data-assistant-sensitive]').some((el) => range.intersectsNode(el))) return "";
  }
  return String(selected).trim().slice(0, 2000);
}
function pickContext(target) {
  if (!pageContextReady()) return null;
  if (sensitiveElement(target) || location.hash === "#/api-keys") return null;
  const selected = target.closest?.("tr, table, svg, .panel, .card, .tile, [data-context-id]") || target;
  // Rows and charts never inherit an enclosing panel's broader records.
  const exactOnly = selected.matches?.("tr, svg");
  for (let el = target; el && el !== document.body; el = el.parentElement) {
    const binding = bindingFor(el);
    if (binding) return validBinding(binding) ? publicBinding(binding) : null;
    if (exactOnly && el === selected) break;
  }
  if (!exactOnly) {
    const children = [...(selected.querySelectorAll?.('[data-context-id], tr, svg') || [])].map(bindingFor).filter(validBinding);
    if (children.length) return { label: labelFor(selected), source: "rendered-data", route: location.hash, project: state.project, data: children.map(publicBinding) };
  }
  return { label: labelFor(selected).slice(0, 120), source: "unavailable", note: "No structured source is bound to this element. No page-wide records were attached.", route: location.hash, project: state.project, data: null };
}
function setAssistantPicking(on) {
  ASSIST.picking = on; document.body.classList.toggle("assistant-picking", on);
  for (const button of $$("#asPick")) { button.textContent = on ? "Cancel pick" : "Pick data"; button.setAttribute?.("aria-pressed", String(on)); }
  onPickingChange(on);
}
const onPick = (event) => {
  if (!ASSIST.picking || event.target.closest("#assist, #asPage")) return;
  const selected = pickContext(event.target); if (!selected) return;
  event.preventDefault(); event.stopImmediatePropagation(); ASSIST.selection = selected; setAssistantPicking(false);
  if (onSelectionChanged) onSelectionChanged(selected);
  else {
    const drawer = $("#assist .drawer"); if (drawer && ASSIST.open) renderAssistant(drawer);
    const page = $("#asPage"); if (page) renderAssistant(page, { full: true });
  }
};

const uiRefs = new Map();
function visible(el) { if (!(el instanceof Element)) return false; if (el.closest("#assist, [data-assistant-sensitive], script, style, template")) return false; const cs = getComputedStyle(el); if (cs.display === "none" || cs.visibility === "hidden") return false; return el.getClientRects().length > 0; }
function labelFor(el) {
  const own = (el.getAttribute("aria-label") || el.getAttribute("title") || el.getAttribute("placeholder") || "").trim();
  if (own) return own;
  if (el.id) { const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (lab) return lab.innerText.trim(); }
  const field = el.closest(".field"); if (field && field.querySelector("label")) return field.querySelector("label").innerText.trim();
  const text = (el.innerText || el.value || "").trim().replace(/\s+/g, " ");
  return text.slice(0, 90) || el.name || el.id || el.tagName.toLowerCase();
}
function snapshotPage({ interactive_only = false, max_chars = 12000 } = {}) {
  uiRefs.clear();
  if (location.hash === "#/api-keys" || !state.user) return "Credential/account page omitted from assistant context.";
  if (!pageContextReady()) return "Page is loading; previous page content omitted.";
  const lines = [`route: ${location.hash || "#/"}`, `page: ${(document.querySelector("#app h1") || {}).innerText?.trim() || document.title}`];
  const roots = [...document.querySelectorAll(".modal-back .modal")];
  if (roots.length) lines.push("(a dialog is open; its elements come first)");
  const content = document.querySelector("#app .content"); if (content) roots.push(content);
  const seenText = new Set();
  const isInteractive = (el) => el.matches("button, a[href], input, select, textarea, [role=button], tr.clickable, .card.clickable, summary, [data-k]");
  const walk = (el, depth) => {
    if (!visible(el) || (el.matches("input, textarea") && (el.type === "password" || /password|token|secret|credential|api.?key/i.test(`${el.id} ${el.name} ${labelFor(el)}`))) || el.closest("#asPage")) return;
    if (isInteractive(el)) {
      const ref = `ref_${uiRefs.size + 1}`; uiRefs.set(ref, el); el.dataset.aref = ref;
      const tag = el.tagName.toLowerCase();
      let kind = tag === "a" ? "link" : tag === "tr" ? "row" : tag === "input" ? `input:${el.type || "text"}` : tag;
      let stateText = "";
      if (tag === "input" && (el.type === "checkbox" || el.type === "radio")) stateText = el.checked ? " [checked]" : " [unchecked]";
      else if (tag === "input" || tag === "textarea") stateText = el.value ? ` = ${JSON.stringify(String(el.value).slice(0, 80))}` : "";
      else if (tag === "select") stateText = ` = ${JSON.stringify(el.options[el.selectedIndex]?.text || "")} (options: ${[...el.options].slice(0, 12).map((o) => o.text).join(" / ")}${el.options.length > 12 ? " / …" : ""})`;
      else if (tag === "a") stateText = el.getAttribute("href") ? ` -> ${el.getAttribute("href")}` : "";
      if (el.disabled) stateText += " [disabled]";
      if (el.classList.contains("on") || el.classList.contains("active") || el.classList.contains("sel")) stateText += " [selected]";
      lines.push(`${"  ".repeat(Math.min(depth, 4))}${ref} | ${kind} | ${labelFor(el)}${stateText}`);
      if (tag !== "tr") return;
    }
    if (!interactive_only) {
      if (/^H[1-4]$/.test(el.tagName)) { lines.push(`${"#".repeat(Number(el.tagName[1]))} ${el.innerText.trim().replace(/\s+/g, " ").slice(0, 160)}`); return; }
      if (el.tagName === "TABLE") {
        const rows = [...el.querySelectorAll("tr")].slice(0, 40);
        for (const r of rows) { if (r.matches("tr.clickable")) walk(r, depth + 1); else { const t = [...r.children].map((c) => c.innerText.trim().replace(/\s+/g, " ").slice(0, 60)).join(" | "); if (t.trim()) lines.push(`${"  ".repeat(Math.min(depth, 4))}| ${t}`); } }
        if (el.querySelectorAll("tr").length > 40) lines.push(`  … ${el.querySelectorAll("tr").length - 40} more rows`);
        return;
      }
      if (el.children.length === 0 || el.matches("p, li, .muted, .meta, .k, .v, .sub, .note, .pill, .tile")) {
        const t = el.innerText.trim().replace(/\s+/g, " ");
        if (t && t.length > 1 && !seenText.has(t)) { seenText.add(t); lines.push(`${"  ".repeat(Math.min(depth, 4))}${t.slice(0, 300)}`); }
        if (el.matches(".tile, .pill, .note, .meta")) return;
      }
    }
    for (const child of el.children) walk(child, depth + 1);
  };
  roots.forEach((r) => walk(r, 0));
  let out = lines.join("\n");
  if (out.length > max_chars) out = out.slice(0, max_chars) + `\n[output truncated at ${max_chars} of ${out.length} chars; pass a larger max_chars or use interactive_only]`;
  return out;
}
function pageText(max = 20000) {
  if (location.hash === "#/api-keys" || !state.user) return "Credential/account page omitted from assistant context.";
  if (!pageContextReady()) return "Page is loading; previous page content omitted.";
  const parts = [...document.querySelectorAll(".modal-back:not([data-assistant-sensitive]) .modal")].map((m) => "DIALOG:\n" + m.innerText);
  const content = document.querySelector("#app .content"); if (content) { const copy = content.cloneNode(true); copy.querySelectorAll("#asPage, input[type=password], [data-assistant-sensitive]").forEach((el) => el.remove()); parts.push(copy.innerText || copy.textContent); }
  const text = parts.join("\n\n").replace(/\n{3,}/g, "\n\n");
  return text.length > max ? text.slice(0, max) + `\n[truncated at ${max} of ${text.length} chars]` : text;
}
function captureContext() {
  const ready = pageContextReady();
  return { route: location.hash || "#/", title: ready ? `${document.title} · ${(document.querySelector("#app h1") || {}).innerText?.trim() || ""}`.trim() : "Page loading", text: pageText(8000), project: state.project, api: ready ? state.pageContext.api : [], selection: ready ? safeSelection() : "", selected_data: ready && ASSIST.selection?.project === state.project && ASSIST.selection?.route === (location.hash || "#/") ? ASSIST.selection : null };
}

const settle = (ms = 700) => new Promise((r) => setTimeout(r, ms));
async function uiPerform(action, args = {}, { allowActions = false } = {}) {
  if (["click", "fill", "select"].includes(action) && !allowActions) throw new Error("Interface changes require the actions / deletions toggle for this turn.");
  const need = (ref) => { if (!pageContextReady()) throw new Error("Page is loading; read the page after it finishes"); if (location.hash === "#/settings") throw new Error("Settings must be changed directly by the user"); const el = uiRefs.get(ref); if (sensitiveElement(el)) throw new Error("Credential elements cannot be operated by the assistant"); if (!el || !el.isConnected) throw new Error(`element ${ref} not found; call ui_read_page again, the page may have re-rendered`); return el; };
  switch (action) {
    case "read_page": return snapshotPage(args);
    case "get_text": return pageText(args.max_chars || 20000);
    case "navigate": { let h = String(args.hash || ""); if (!h.startsWith("#")) h = "#" + (h.startsWith("/") ? h : "/" + h); if (location.hash === h) navigation.render(); else location.hash = h; await settle(1000); return snapshotPage({ interactive_only: false, max_chars: 8000 }); }
    case "click": { const el = need(args.ref); el.click(); await settle(800); return { clicked: args.ref, page: snapshotPage({ interactive_only: false, max_chars: 6000 }) }; }
    case "fill": { const el = need(args.ref); if (el.type === "password" || location.hash === "#/settings") throw new Error("Credential fields cannot be operated by the assistant"); if (!el.matches("input, textarea")) throw new Error(`${args.ref} is not an input`); el.focus(); el.value = String(args.value ?? ""); el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change", { bubbles: true })); return { filled: args.ref, value: el.value }; }
    case "select": { const el = need(args.ref); if (!el.matches("select")) throw new Error(`${args.ref} is not a select`); const want = String(args.value ?? ""); const opt = [...el.options].find((o) => o.value === want) || [...el.options].find((o) => o.text.trim().toLowerCase() === want.toLowerCase()) || [...el.options].find((o) => o.text.toLowerCase().includes(want.toLowerCase())); if (!opt) throw new Error(`no option matching ${JSON.stringify(want)}; options: ${[...el.options].map((o) => o.text).join(" / ")}`); el.value = opt.value; el.dispatchEvent(new Event("change", { bubbles: true })); await settle(500); return { selected: args.ref, value: opt.value, label: opt.text }; }
    default: throw new Error(`unknown interface action ${action}`);
  }
}


  let mounted = false;
  function mount() { if (mounted) return; mounted = true; document.addEventListener("click", onPick, true); }
  function destroy() { document.removeEventListener("click", onPick, true); mounted = false; setAssistantPicking(false); uiRefs.clear(); }
  return { safeContextData, recordApi, isCurrentRender, pageContextReady, assertRender, contextRecord, bindContext, dataContext, bindingFor, validBinding, publicBinding, sensitiveElement, safeSelection, pickContext, setAssistantPicking, visible, labelFor, snapshotPage, pageText, captureContext, uiPerform, clearRefs: () => uiRefs.clear(), mount, destroy };
}
