import { $ } from "./dom.js?v=82e74d4b5ecb";
import { esc } from "./format.js?v=82e74d4b5ecb";
import { guard } from "./feedback.js?v=82e74d4b5ecb";

export function modal({ title, body, footer = "", wide = false, onMount, sensitive = false }) {
  const back = document.createElement("div"); back.className = "modal-back";
  if (sensitive) back.dataset.assistantSensitive = "true";
  back.innerHTML = `<div class="modal ${wide ? "wide" : ""}"><div class="mhead">${esc(title)}<button class="x" aria-label="close">✕</button></div><div class="mbody"></div>${footer ? `<div class="mfoot">${footer}</div>` : ""}</div>`;
  const bodyEl = $(".mbody", back);
  if (typeof body === "string") bodyEl.innerHTML = body; else bodyEl.appendChild(body);
  const close = () => back.remove();
  $(".x", back).onclick = close;
  back.addEventListener("click", (e) => { if (e.target === back) close(); });
  document.body.appendChild(back);
  if (onMount) onMount(back, close);
  return { el: back, close };
}
export function confirmModal(title, text, action, label = "Delete") {
  return new Promise((resolve) => {
    const m = modal({ title, body: `<p>${esc(text)}</p>`, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary danger" data-ok>${esc(label)}</button>` });
    $("[data-cancel]", m.el).onclick = () => { m.close(); resolve(false); };
    $("[data-ok]", m.el).onclick = guard(async () => { await action(); m.close(); resolve(true); });
  });
}
