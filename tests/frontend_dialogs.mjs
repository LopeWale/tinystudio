// Expectations: WAI-ARIA APG modal-dialog keyboard interaction and TinyStudio's
// confirmation contract: cancellation always settles, action failure permits retry.
// https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
createRequire(import.meta.url)('./helpers/frontend-loader.cjs').ensureModuleRuntime();

// A small DOM with actual parentage, parsed elements, focus events, inert inheritance,
// visibility and listeners. Missing selectors return null; no synthetic controls.
class Element {
  constructor(document, tag, attributes = {}) {
    this.ownerDocument = document; this.tagName = tag; this.attributes = attributes;
    this.children = []; this.dataset = {}; this.listeners = new Map(); this.disabled = 'disabled' in attributes;
    this.textContent = ''; this.onclick = null;
  }
  get className() { return this.attributes.class || ''; }
  set className(value) { this.attributes.class = value; }
  get classList() { return { contains: (name) => this.className.split(/\s+/).includes(name) }; }
  hasAttribute(name) { return name in this.attributes; }
  getAttribute(name) { return this.attributes[name] ?? null; }
  get inert() { return this.hasAttribute('inert'); }
  set inert(value) { if (value) this.attributes.inert = ''; else delete this.attributes.inert; }
  get tabIndex() { return this.hasAttribute('tabindex') ? Number(this.attributes.tabindex) : /^(button|input|select|textarea)$/.test(this.tagName) || this.hasAttribute('href') || this.attributes.contenteditable === 'true' ? 0 : -1; }
  get isConnected() { return this === this.ownerDocument.body || !!this.parentElement?.isConnected; }
  contains(other) { return other === this || this.children.some((child) => child.contains(other)); }
  appendChild(child) { child.remove(); this.children.push(child); child.parentElement = this; return child; }
  remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter((child) => child !== this); this.parentElement = null; }
  matches(selector) {
    return selector.split(',').some((part) => {
      part = part.trim();
      const not = /:not\(([^)]+)\)/.exec(part);
      if (not && this.matches(not[1])) return false;
      part = part.replace(/:not\([^)]+\)/g, '');
      const tag = /^[a-z]+/.exec(part)?.[0];
      if (tag && tag !== this.tagName) return false;
      const cls = /\.([\w-]+)/.exec(part)?.[1];
      if (cls && !this.classList.contains(cls)) return false;
      const id = /^#([\w-]+)$/.exec(part)?.[1];
      if (id && this.getAttribute('id') !== id) return false;
      return [...part.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)].every(([, name, value]) => this.hasAttribute(name) && (value === undefined || this.getAttribute(name) === value));
    });
  }
  closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; }
  querySelectorAll(selector) { return this.children.flatMap((child) => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  getClientRects() { return this.isConnected && !this.closest('[hidden]') ? [{}] : []; }
  focus() {
    if (!this.isConnected || this.disabled || this.closest('[inert]') || this.closest('[hidden]')) return;
    this.ownerDocument.activeElement = this;
    this.ownerDocument.dispatch('focusin', { target: this });
  }
  addEventListener(type, fn) { if (!this.listeners.has(type)) this.listeners.set(type, new Set()); this.listeners.get(type).add(fn); }
  removeEventListener(type, fn) { this.listeners.get(type)?.delete(fn); }
  async click(target = this) {
    if (this.disabled || this.closest('[inert]')) return;
    const event = { target };
    await this.onclick?.(event);
    for (const fn of this.listeners.get('click') || []) await fn(event);
  }
  set innerHTML(html) {
    this.children = []; const stack = [this];
    for (const token of String(html).match(/<[^>]+>|[^<]+/g) || []) {
      if (!token.startsWith('<')) { stack.at(-1).textContent += token; continue; }
      if (token.startsWith('</')) { stack.pop(); continue; }
      const tag = /^<([a-z]+)/.exec(token)[1]; const attrs = {};
      for (const [, key, value] of token.slice(tag.length + 1, -1).matchAll(/([\w-]+)(?:="([^"]*)")?/g)) attrs[key] = value || '';
      const child = new Element(this.ownerDocument, tag, attrs); stack.at(-1).appendChild(child);
      if (!['input', 'br', 'hr', 'img'].includes(tag)) stack.push(child);
    }
  }
}
class Document {
  constructor() { this.body = new Element(this, 'body'); this.activeElement = this.body; this.listeners = new Map(); }
  createElement(tag) { return new Element(this, tag); }
  addEventListener(type, fn) { if (!this.listeners.has(type)) this.listeners.set(type, new Set()); this.listeners.get(type).add(fn); }
  removeEventListener(type, fn) { this.listeners.get(type)?.delete(fn); }
  dispatch(type, event) { for (const fn of this.listeners.get(type) || []) fn(event); }
  key(key, shiftKey = false) { const event = { key, shiftKey, prevented: false, preventDefault() { this.prevented = true; }, stopPropagation() {} }; this.dispatch('keydown', event); return event; }
  listenerCount() { return [...this.listeners.values()].reduce((sum, listeners) => sum + listeners.size, 0); }
}
async function harness(transform = (source) => source) {
  const document = new Document(), timers = [], modules = new Map();
  const context = vm.createContext({ document, setTimeout: (fn) => timers.push(fn) });
  async function load(url) {
    const name = url.href;
    if (!modules.has(name)) {
      const source = await readFile(url, 'utf8');
      modules.set(name, new vm.SourceTextModule(name.endsWith('/dialogs.js') ? transform(source) : source, { context, identifier: name }));
    }
    return modules.get(name);
  }
  const module = await load(new URL('../tinystudio/ui/ui/dialogs.js', import.meta.url));
  await module.link((specifier, parent) => load(new URL(specifier.split('?')[0], parent.identifier)));
  await module.evaluate();
  const page = document.createElement('main'); document.body.appendChild(page);
  const opener = document.createElement('button'); page.appendChild(opener); opener.focus();
  return { document, page, opener, timers, ...module.namespace };
}
const controls = (el) => ({ cancel: el.querySelector('[data-cancel]'), ok: el.querySelector('[data-ok]'), x: el.querySelector('.x') });
const lastDialog = (document) => document.body.querySelectorAll('.modal-back').at(-1);

// Each test uses a fresh module controller and document to expose resource leakage.
test('dialog announces title, isolates background and implements Tab, Shift+Tab, Escape and focus return', async () => {
  const h = await harness();
  const fixed = h.document.createElement('aside'); fixed.inert = true; h.document.body.appendChild(fixed);
  const m = h.modal({ title: 'Edit dataset', sensitive: true, body: '<input id="name"><button hidden>Hidden</button><button disabled>Disabled</button>', footer: '<button data-cancel>Cancel</button><button data-ok>Save</button>' });
  const { cancel, ok, x } = controls(m.el), dialog = m.el.querySelector('.modal');
  assert.equal(dialog.getAttribute('role'), 'dialog'); assert.equal(dialog.getAttribute('aria-modal'), 'true');
  assert.ok(m.el.querySelector('#' + dialog.getAttribute('aria-labelledby')));
  assert.equal(m.el.dataset.assistantSensitive, 'true'); assert.equal(h.document.activeElement, cancel);
  assert.equal(h.page.inert, true); assert.equal(fixed.inert, true);
  ok.focus(); assert.equal(h.document.key('Tab').prevented, true); assert.equal(h.document.activeElement, x);
  assert.equal(h.document.key('Tab', true).prevented, true); assert.equal(h.document.activeElement, ok);
  x.focus(); assert.equal(h.document.key('Tab').prevented, false, 'ordinary Tab uses native document order');
  // External programmatic focus must be redirected even if it bypasses native inert.
  h.document.activeElement = h.opener; h.document.dispatch('focusin', { target: h.opener }); assert.equal(h.document.activeElement, cancel);
  h.document.key('Escape'); assert.equal(m.el.isConnected, false); assert.equal(h.document.activeElement, h.opener);
  assert.equal(h.page.inert, false); assert.equal(fixed.inert, true); assert.equal(h.document.listenerCount(), 0);
  m.close(); assert.equal(h.document.listenerCount(), 0);
});

test('nested dialogs constrain the top modal, then restore parent and original opener', async () => {
  const h = await harness();
  const parent = h.modal({ title: 'Parent', body: '<button id="child">Open child</button>' });
  const parentButton = parent.el.querySelector('#child'); parentButton.focus();
  const child = h.modal({ title: 'Child', body: '<input autofocus>' });
  assert.equal(parent.el.inert, true); assert.equal(h.document.listenerCount(), 2);
  h.document.key('Escape'); assert.equal(child.el.isConnected, false); assert.equal(parent.el.inert, false); assert.equal(h.document.activeElement, parentButton);
  parent.close(); assert.equal(h.document.activeElement, h.opener); assert.equal(h.document.listenerCount(), 0);
});

test('closing a parent underneath a child preserves focus and eventual return target', async () => {
  const h = await harness(); const parent = h.modal({ title: 'Parent', body: '<button>Child</button>' });
  const child = h.modal({ title: 'Child', body: '<input>' }); const focused = h.document.activeElement;
  parent.close(); assert.equal(h.document.activeElement, focused); assert.equal(h.page.inert, true);
  child.close(); assert.equal(h.document.activeElement, h.opener); assert.equal(h.document.listenerCount(), 0);
});

test('all confirmation dismissal paths resolve false, including navigation with nested dialogs', async () => {
  for (const dismiss of ['escape', 'backdrop', 'x', 'cancel', 'navigation']) {
    const h = await harness(); let called = 0;
    const result = h.confirmModal('Delete?', 'Cannot undo', () => { called++; }); const back = lastDialog(h.document);
    if (dismiss === 'escape') h.document.key('Escape');
    if (dismiss === 'backdrop') await back.click();
    if (dismiss === 'x') await controls(back).x.click();
    if (dismiss === 'cancel') await controls(back).cancel.click();
    if (dismiss === 'navigation') { h.modal({ title: 'Nested', body: '<button>Nested</button>' }); h.closeAll(); }
    assert.equal(await result, false, dismiss); assert.equal(called, 0); assert.equal(h.document.listenerCount(), 0); assert.equal(h.page.inert, false);
  }
});

test('failure keeps confirmation open, reports error, permits retry and prevents concurrent action', async () => {
  const h = await harness(); let calls = 0, finish;
  const result = h.confirmModal('Delete?', 'Cannot undo', () => { calls++; if (calls === 1) throw new Error('Delete failed'); return new Promise((resolve) => { finish = resolve; }); });
  const back = lastDialog(h.document), { ok } = controls(back);
  await ok.click(); assert.equal(back.isConnected, true); assert.equal(ok.disabled, false);
  assert.equal(h.document.body.querySelector('.toast').textContent, 'Delete failed');
  const pending = ok.click(); assert.equal(ok.disabled, true);
  await ok.onclick(); assert.equal(calls, 2, 'even direct duplicate handler invocation cannot submit twice');
  finish(); await pending; assert.equal(await result, true); assert.equal(back.isConnected, false); assert.equal(h.document.listenerCount(), 0);
});

test('dismissal while an action is pending cannot later turn cancellation into success', async () => {
  const h = await harness(); let finish;
  const result = h.confirmModal('Delete?', 'Cannot undo', () => new Promise((resolve) => { finish = resolve; }));
  const pending = controls(lastDialog(h.document)).ok.click(); h.closeAll();
  assert.equal(await result, false); finish(); await pending; assert.equal(h.document.listenerCount(), 0); assert.equal(h.document.activeElement, h.opener);
});

test('mount failure cleans resources and a dialog without controls retains keyboard focus', async () => {
  const h = await harness();
  assert.throws(() => h.modal({ title: 'Failure', body: '', onMount() { throw new Error('Mount failed'); } }), /Mount failed/);
  assert.equal(h.document.listenerCount(), 0); assert.equal(h.page.inert, false);
  const m = h.modal({ title: 'Read only', body: '' });
  m.el.querySelector('.x').disabled = true;
  h.document.key('Tab'); assert.equal(h.document.activeElement, m.el.querySelector('.modal'));
  h.closeAll(); assert.equal(h.document.listenerCount(), 0);
});

// Prove the Escape acceptance assertion rejects a known keyboard regression.
// Only the isolated VM source changes; the shipped module is never mutated.
test('controlled fault: Escape closure assertion detects a missing dismissal', async () => {
  const h = await harness((source) => {
    const changed = source.replace("entry.close('escape');", "/* controlled missing Escape close */");
    assert.notEqual(changed, source, 'fault must affect the actual Escape branch');
    return changed;
  });
  const m = h.modal({ title: 'Fault control', body: '' });
  h.document.key('Escape');
  assert.throws(() => assert.equal(m.el.isConnected, false), { code: 'ERR_ASSERTION' });
  h.closeAll(); assert.equal(h.document.listenerCount(), 0);
});
