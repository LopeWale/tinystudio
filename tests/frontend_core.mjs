// Exercise the shipped ES modules without introducing a bundler or a package-wide module mode.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../tinystudio/ui');
const sources = new Map();
function moduleUrl(path) {
  if (!sources.has(path)) {
    const source = readFileSync(path, 'utf8').replace(/from\s+(["'])(\.\.?\/[^"']+)\1/g, (_, quote, dep) => `from ${quote}${moduleUrl(resolve(dirname(path), dep.split('?')[0]))}${quote}`);
    sources.set(path, `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
  }
  return sources.get(path);
}
const load = (path) => import(moduleUrl(resolve(root, path)));
const { createApiClient } = await load('core/api.js');
const { createAppState } = await load('core/state.js');
const format = await load('ui/format.js');
const { histogram } = await load('ui/charts.js');
const state = createAppState();
const other = createAppState();
state.tasks.push('one');
assert.deepEqual(other.tasks, [], 'independent app instances must not share read-model data');
assert.equal(other.pageContext, null);
assert.equal(other.domContext, null);

let project = 'a/b ?';
let capture = { route: '#/datasets' };
let unauthorized = 0;
const calls = [], records = [];
let response = () => new Response('{"name":"ok"}', { headers: { 'content-type': 'application/json' } });
const client = createApiClient({
  getProject: () => project, getCapture: () => capture,
  assertRender: (view) => { if (view !== capture) { const e = new Error('stale'); e.name = 'StaleRenderError'; throw e; } },
  recordApi: (...args) => records.push(args), onUnauthorized: () => { unauthorized++; },
  fetch: async (...args) => { calls.push(args); return response(); },
});
assert.equal(client.scoped('/api/datasets?a=1'), '/api/p/a%2Fb%20%3F/datasets?a=1');
assert.equal(client.scoped('/api/datasets/foo'), '/api/p/a%2Fb%20%3F/datasets/foo');
assert.equal(client.scoped('/api/datasets-other'), '/api/datasets-other');
assert.equal(client.scoped('/api/tasks'), '/api/tasks?project=a%2Fb%20%3F');
assert.equal(client.scoped('/api/tasks?all=1'), '/api/tasks?all=1');
assert.equal(client.scoped('/api/hub/models'), '/api/hub/models');
await client.api('/api/datasets');
assert.equal(calls.at(-1)[1].credentials, 'same-origin');
assert.equal(calls.at(-1)[1].headers['X-Requested-With'], 'tinystudio');
assert.deepEqual(records.at(-1), ['/api/datasets', { name: 'ok' }, capture]);
const recordCount = records.length;
await client.post('/api/datasets', { name: 'new' });
assert.equal(calls.at(-1)[1].body, '{"name":"new"}');
assert.equal(records.length, recordCount, 'mutations must not enter assistant GET context');
const form = new FormData(); form.append('file', 'contents');
await client.post('/api/datasets', form);
assert.equal(calls.at(-1)[1].body, form);
assert.equal('content-type' in calls.at(-1)[1].headers, false, 'browser must supply the multipart boundary');
await client.rawFetch('/api/settings', { headers: { 'content-type': 'text/plain', custom: 'value' } });
assert.equal(calls.at(-1)[1].headers['content-type'], 'text/plain');
project = null;
assert.equal(client.scoped('/api/datasets'), '/api/datasets');
response = () => new Response('{"detail":{"reason":"denied"}}', { status: 403, headers: { 'content-type': 'application/json' } });
await assert.rejects(client.api('/api/datasets'), { message: '{"reason":"denied"}' });
response = () => new Response('bad response', { status: 500, statusText: 'Server failure' });
await assert.rejects(client.api('/api/datasets'), { message: 'Server failure' });
response = () => new Response('', { status: 401 });
await assert.rejects(client.api('/api/datasets'), { message: 'sign in to continue' });
assert.equal(unauthorized, 1);
await assert.rejects(client.api('/api/auth/login'));
assert.equal(unauthorized, 1, 'a failed login must not recursively reopen login');
response = () => { capture = { route: '#/models' }; return new Response('{}', { headers: { 'content-type': 'application/json' } }); };
await assert.rejects(client.api('/api/datasets'), { name: 'StaleRenderError' });
assert.equal(records.length, recordCount, 'stale responses must not enter current page context');
response = () => new Response('plain result');
assert.equal(await client.api('/api/settings'), 'plain result');
const originalRaw = client.rawFetch;
client.rawFetch = async () => new Response('{"stub":true}', { headers: { 'content-type': 'application/json' } });
assert.deepEqual(await client.api('/api/settings'), { stub: true }, 'client boundary remains replaceable');
client.rawFetch = originalRaw;

assert.equal(format.esc('<&"\''), '&lt;&amp;&quot;&#39;');
assert.equal(format.fmtPct(null), '–');
assert.equal(format.fmtPct(.456), '46%');
assert.equal(format.fmtNum(NaN), '–');
assert.equal(format.fmtNum(2, 2), '2.00');
assert.equal(format.fmtParams(1.5e9), '1.50B');
assert.equal(format.fmtParams(125e6), '125M');
assert.equal(format.pctClass(.75), 'hi');
assert.equal(format.pctClass(.4), 'mid');
assert.equal(format.pctClass(.399), 'lo');
assert.equal(format.slug('vendor/model'), 'vendor~model');
assert.equal(histogram(null), '<div class="muted">no data</div>');
const chart = histogram({ bins: [0, 10, 20], counts: [2, 4] }, 'red', 100, 50, (label) => `data-label="${label}"`);
assert.match(chart, /data-label="Histogram bins and counts"/);
assert.match(chart, /<title>0–10: 2<\/title>/);
assert.match(chart, /height="16.0"/);
assert.match(chart, /height="32.0"/);

// A deliberately small DOM boundary verifies mounting, escaping and lifecycle callbacks.
class Node {
  constructor() { this.dataset = {}; this.children = []; this.listeners = {}; this.nodes = new Map(); }
  set innerHTML(value) { this.html = value; for (const sel of ['.mbody', '.x', '[data-cancel]', '[data-ok]']) if (value.includes(sel === '.mbody' ? 'mbody' : sel === '.x' ? 'class="x"' : sel.slice(1, -1))) this.nodes.set(sel, new Node()); }
  get innerHTML() { return this.html; }
  querySelector(sel) { return this.nodes.get(sel); }
  appendChild(node) { this.children.push(node); node.parent = this; }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  remove() { this.removed = true; }
}
globalThis.document = { body: new Node(), createElement: () => new Node() };
const { modal, confirmModal } = await load('ui/dialogs.js');
let mounted;
const child = new Node();
const dialog = modal({ title: '<script>', body: child, sensitive: true, wide: true, onMount: (...args) => { mounted = args; } });
assert.match(dialog.el.innerHTML, /&lt;script&gt;/);
assert.match(dialog.el.innerHTML, /modal wide/);
assert.equal(dialog.el.dataset.assistantSensitive, 'true');
assert.equal(dialog.el.querySelector('.mbody').children[0], child);
assert.equal(mounted[0], dialog.el);
assert.equal(mounted[1], dialog.close);
dialog.el.listeners.click({ target: child });
assert.equal(dialog.el.removed, undefined, 'body clicks keep the modal mounted');
dialog.el.listeners.click({ target: dialog.el });
assert.equal(dialog.el.removed, true);
let acted = 0;
const confirmation = confirmModal('Delete', 'A < B', async () => { acted++; });
const confirm = document.body.children.at(-1);
assert.match(confirm.querySelector('.mbody').innerHTML, /A &lt; B/);
await confirm.querySelector('[data-ok]').onclick();
assert.equal(await confirmation, true);
assert.equal(acted, 1);
assert.equal(confirm.removed, true);
const cancellation = confirmModal('Delete', 'text', async () => { acted++; });
document.body.children.at(-1).querySelector('[data-cancel]').onclick();
assert.equal(await cancellation, false);
assert.equal(acted, 1);
const { createFeedback, guard, toast } = await load('ui/feedback.js');
const feedback = createFeedback();
const feedbackErrors = [];
feedback.toast = (...args) => feedbackErrors.push(args);
await feedback.guard(async () => { throw new Error('injected error'); })();
assert.deepEqual(feedbackErrors, [['injected error', true]], 'guard must use its application feedback boundary');
const isolatedFeedback = createFeedback();
assert.equal(isolatedFeedback.toast, toast, 'feedback substitution must not affect other applications');
const realTimeout = globalThis.setTimeout;
const timers = [];
globalThis.setTimeout = (fn, delay) => { timers.push({ fn, delay }); };
try {
  toast('saved');
  assert.equal(document.body.children.at(-1).className, 'toast');
  assert.equal(timers.at(-1).delay, 3500);
  timers.at(-1).fn();
  assert.equal(document.body.children.at(-1).removed, true);
  assert.equal(await guard(async (n) => n * 2)(4), 8);
  const before = document.body.children.length;
  const stale = new Error('obsolete'); stale.name = 'StaleRenderError';
  await guard(async () => { throw stale; })();
  assert.equal(document.body.children.length, before);
  await guard(async () => { throw new Error('visible error'); })();
  assert.equal(document.body.children.at(-1).textContent, 'visible error');
  assert.equal(document.body.children.at(-1).className, 'toast err');
  assert.equal(timers.at(-1).delay, 9000);
  const failed = confirmModal('Delete', 'text', async () => { throw new Error('denied'); });
  const failedModal = document.body.children.at(-1);
  await failedModal.querySelector('[data-ok]').onclick();
  assert.equal(failedModal.removed, undefined, 'a failed action leaves confirmation open to recover');
  failedModal.querySelector('[data-cancel]').onclick();
  assert.equal(await failed, false);
} finally { globalThis.setTimeout = realTimeout; }

const { createShell } = await load('ui/layout.js');
const app = new Node();
Object.defineProperty(app, 'innerHTML', {
  set(html) { this.html = html; for (const sel of ['#projectSwitch', '#signOut', '.content']) this.nodes.set(sel, new Node()); },
  get() { return this.html; },
});
document.querySelector = (sel) => sel === '#app' ? app : null;
state.user = { role: 'admin', name: 'A&B' };
state.projects = [{ slug: 'one', name: 'One <Project>' }];
state.project = 'one';
state.pageContext = { route: '#/datasets' };
const moves = [];
let newProject = 0, selected;
const navigation = { go: (hash) => moves.push(hash), render: () => moves.push('render') };
const shell = createShell({ state, feedback: { guard }, dialogs: {}, http: { post: async () => ({ logout_url: '/logout' }) }, navigation,
  selectProject: async (slug) => { selected = slug; state.project = slug; },
  newProjectModal: () => { newProject++; },
});
assert.match(shell.sidebar('datasets'), /href="#\/datasets" class="active"/);
assert.match(shell.sidebar('datasets'), /One &lt;Project&gt;/);
assert.match(shell.sidebar('datasets'), /A&amp;B/);
assert.match(shell.sidebar('datasets'), /href="#\/settings"/);
state.user.role = 'member';
assert.doesNotMatch(shell.sidebar('datasets'), /href="#\/settings"/);
const content = shell.layout('datasets', '<p>body</p>', [{ label: '<Home>' }]);
assert.equal(content, app.querySelector('.content'));
assert.match(app.innerHTML, /&lt;Home&gt;/);
assert.equal(state.domContext, state.pageContext);
const switcher = app.querySelector('#projectSwitch');
switcher.value = '__new__'; await switcher.onchange();
assert.equal(newProject, 1);
assert.equal(switcher.value, 'one');
switcher.value = '__all__'; await switcher.onchange();
assert.equal(moves.at(-1), '#/projects');
state.overview = { old: true };
switcher.value = 'two'; await switcher.onchange();
assert.equal(selected, 'two');
assert.equal(state.overview, null);
assert.deepEqual(moves.slice(-2), ['#/datasets', 'render']);
let logout;
globalThis.location = { assign: (url) => { logout = url; } };
let prevented = false;
await app.querySelector('#signOut').onclick({ preventDefault: () => { prevented = true; } });
assert.equal(prevented, true);
assert.equal(logout, '/logout');
console.log('frontend core: API, state, formats, charts, feedback, dialogs and shell contracts passed');
