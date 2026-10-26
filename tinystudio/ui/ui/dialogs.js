import { $ } from "./dom.js?v=c96cb305d835";
import { esc } from "./format.js?v=c96cb305d835";
import { guard } from "./feedback.js?v=c96cb305d835";

const focusSelector = 'a[href],button,input:not([type="hidden"]),select,textarea,[tabindex],[contenteditable="true"]';

// One owner for keyboard listeners, stacked dialogs, and the original inert state.
class ModalController {
  #stack = [];
  #background = new Map();
  #nextId = 0;

  titleId() { return `modal-title-${++this.#nextId}`; }
  isTop(entry) { return this.#stack.at(-1) === entry; }

  focusable(dialog) {
    return [...dialog.querySelectorAll(focusSelector)].filter((el) => !el.disabled && el.tabIndex >= 0 && !el.closest('[hidden],[inert]') && el.getClientRects().length);
  }

  focus(entry) {
    const candidates = this.focusable(entry.dialog);
    const preferred = candidates.find((el) => el.hasAttribute('autofocus'))
      || candidates.find((el) => el.hasAttribute('data-cancel'))
      || candidates.find((el) => !el.classList.contains('x')) || candidates[0];
    (preferred || entry.dialog).focus();
  }

  keydown = (event) => {
    const entry = this.#stack.at(-1);
    if (!entry) return;
    if (event.key === 'Escape') {
      event.preventDefault(); event.stopPropagation(); entry.close('escape');
    } else if (event.key === 'Tab') {
      const candidates = this.focusable(entry.dialog);
      const index = candidates.indexOf(document.activeElement);
      if (!candidates.length || index < 0 || (!event.shiftKey && index === candidates.length - 1) || (event.shiftKey && index === 0)) {
        event.preventDefault();
        (candidates[event.shiftKey ? candidates.length - 1 : 0] || entry.dialog).focus();
      }
    }
  };

  focusin = (event) => {
    const entry = this.#stack.at(-1);
    if (entry && !entry.dialog.contains(event.target)) this.focus(entry);
  };

  syncBackground() {
    const top = this.#stack.at(-1);
    for (const child of document.body.children) {
      if (!this.#background.has(child)) this.#background.set(child, child.inert);
      child.inert = child !== top?.back;
    }
    if (!top) {
      for (const [child, inert] of this.#background) child.inert = inert;
      this.#background.clear();
      document.removeEventListener('keydown', this.keydown, true);
      document.removeEventListener('focusin', this.focusin, true);
    }
  }

  open(entry) {
    if (!this.#stack.length) {
      document.addEventListener('keydown', this.keydown, true);
      document.addEventListener('focusin', this.focusin, true);
    }
    this.#stack.push(entry);
    document.body.appendChild(entry.back);
    this.syncBackground();
  }

  close(entry, reason) {
    const index = this.#stack.indexOf(entry);
    if (index < 0) return;
    const wasTop = index === this.#stack.length - 1;
    this.#stack.splice(index, 1);
    // A child can outlive a programmatically closed parent.
    for (const child of this.#stack) {
      if (entry.back.contains(child.opener)) child.opener = entry.opener;
    }
    entry.back.removeEventListener('click', entry.backdrop);
    $('.x', entry.back).onclick = null;
    entry.back.remove();
    this.syncBackground();
    if (wasTop) {
      const top = this.#stack.at(-1);
      if (entry.opener?.isConnected && !entry.opener.closest('[inert]') && (!top || top.dialog.contains(entry.opener))) entry.opener.focus();
      else if (top) this.focus(top);
    }
    entry.onClose?.(reason);
  }

  closeAll() {
    while (this.#stack.length) this.#stack.at(-1).close('navigation');
  }
}
const controller = new ModalController();

export function closeAll() { controller.closeAll(); }

export function modal({ title, body, footer = "", wide = false, onMount, onClose, sensitive = false }) {
  const back = document.createElement('div'); back.className = 'modal-back';
  if (sensitive) back.dataset.assistantSensitive = 'true';
  const titleId = controller.titleId();
  back.innerHTML = `<div class="modal ${wide ? 'wide' : ''}" role="dialog" aria-modal="true" aria-labelledby="${titleId}" tabindex="-1"><div class="mhead"><span id="${titleId}">${esc(title)}</span><button class="x" aria-label="Close dialog">✕</button></div><div class="mbody"></div>${footer ? `<div class="mfoot">${footer}</div>` : ''}</div>`;
  const bodyEl = $('.mbody', back);
  if (typeof body === 'string') bodyEl.innerHTML = body; else bodyEl.appendChild(body);
  const entry = { back, dialog: $('.modal', back), opener: document.activeElement, onClose };
  const close = (reason = 'dismiss') => controller.close(entry, reason);
  entry.close = close;
  entry.backdrop = (event) => { if (event.target === back && controller.isTop(entry)) close('backdrop'); };
  $('.x', back).onclick = () => close('dismiss');
  back.addEventListener('click', entry.backdrop);
  controller.open(entry);
  try {
    if (onMount) onMount(back, close);
    if (controller.isTop(entry) && !entry.dialog.contains(document.activeElement)) controller.focus(entry);
  } catch (error) { close('mount-error'); throw error; }
  return { el: back, close };
}

export function confirmModal(title, text, action, label = 'Delete') {
  return new Promise((resolve) => {
    let pending = false, succeeded = false, closed = false;
    const m = modal({ title, body: `<p>${esc(text)}</p>`, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary danger" data-ok>${esc(label)}</button>`, onClose: () => { closed = true; resolve(succeeded); } });
    $('[data-cancel]', m.el).onclick = () => m.close('cancel');
    const ok = $('[data-ok]', m.el);
    ok.onclick = guard(async () => {
      if (pending || closed) return;
      pending = true; ok.disabled = true;
      try {
        await action();
        if (!closed) { succeeded = true; m.close('action'); }
      } finally { pending = false; ok.disabled = false; }
    });
  });
}
