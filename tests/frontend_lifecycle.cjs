// Native application-boundary checks. Run: node tests/frontend_lifecycle.cjs
const { loadFrontend } = require('./helpers/frontend-loader.cjs');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');

// Only selectors explicitly emitted by auth/task markup exist in this fixture.
class Element {
  constructor() { this.nodes = new Map(); this.dataset = {}; this.hidden = false; this.value = ''; this.textContent = ''; this.html = ''; }
  set innerHTML(html) {
    this.html = html; this.nodes.clear();
    for (const [, id] of html.matchAll(/\bid="([^"]+)"/g)) this.nodes.set('#' + id, new Element());
    if (html.includes('class="auth-card"')) {
      const card = new Element(); card.nodes = this.nodes; this.nodes.set('.auth-card', card);
    }
    this.stops = [...html.matchAll(/data-stop="([^"]+)"/g)].map(([, id]) => { const button = new Element(); button.dataset.stop = id; return button; });
  }
  get innerHTML() { return this.html; }
  querySelector(selector) { return this.nodes.get(selector) || null; }
  querySelectorAll(selector) { return selector === '[data-stop]' ? this.stops || [] : []; }
}
async function fixture(options = {}) {
  const appElement = new Element(), tasksElement = new Element();
  const timers = new Map(), cleared = [], listeners = new Map(), posts = [], requests = [];
  let timerId = 0, reloads = 0, adds = 0, removes = 0;
  let network = async () => { throw new Error('unexpected network request'); };
  const bodyClasses = new Set();
  const document = {
    body: { classList: { toggle(name, on) { if (on) bodyClasses.add(name); else bodyClasses.delete(name); } } },
    querySelector(selector) {
      if (selector === '#app') return appElement;
      if (selector === '#tasks') return tasksElement;
      if (selector === '#app .auth-card') return appElement.querySelector('.auth-card');
      return appElement.querySelector(selector);
    },
    querySelectorAll() { return []; }, addEventListener() {}, removeEventListener() {},
  };
  const storage = new Map();
  class ConversationSync { start() {} stop() {} notifySaved() {} requestCheck() {} }
  const context = vm.createContext({
    ConversationSync, document, FormData, AbortController, TextEncoder, URLSearchParams, console,
    location: { hash: '#/datasets', reload: () => { reloads++; } },
    window: {
      addEventListener(type, fn) { if (type === 'hashchange') adds++; if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn); },
      removeEventListener(type, fn) { if (type === 'hashchange') removes++; listeners.get(type)?.delete(fn); },
    },
    localStorage: { getItem: (key) => storage.get(key), setItem: (key, value) => storage.set(key, value) },
    setTimeout(fn, delay) { const id = ++timerId; timers.set(id, { fn, delay }); return id; },
    clearTimeout(id) { cleared.push(id); timers.delete(id); },
    fetch: (...args) => network(...args),
  });
  const app = await loadFrontend(context);
  const realApi = app.http.api, realBackground = app.http.background;
  app.http.api = async (path) => {
    requests.push(path);
    if (path === '/api/auth/status') return options.status;
    if (path === '/api/projects') return [{ slug: 'project-one', name: 'Project One' }];
    if (path === '/api/overview') return { project: { name: 'Project One' }, tree: { datasets: [] } };
    if (path === '/api/tasks?include_result=false') return options.tasks || [];
    throw new Error('unexpected API path: ' + path);
  };
  app.http.post = async (path, body) => { posts.push({ path, body: JSON.parse(JSON.stringify(body)) }); };
  app.http.background = (...args) => app.http.api(...args);
  const errors = [];
  app.feedback.toast = (...args) => errors.push(args);
  return { app, appElement, tasksElement, timers, cleared, requests, posts, errors, context, realApi, realBackground, setNetwork: (fn) => { network = fn; }, counts: () => ({ reloads, adds, removes }), listeners };
}
const submitEvent = () => ({ prevented: false, preventDefault() { this.prevented = true; } });

test('signed-out boot renders login once; failures remain visible and success reloads', async () => {
  const f = await fixture({ status: { setup_required: false, authenticated: false } });
  await f.app.start(); await f.app.start();
  assert.deepEqual(f.requests, ['/api/auth/status']);
  assert.equal(f.counts().adds, 0, 'signed-out pages must not register protected route handlers');
  assert.match(f.appElement.innerHTML, /Sign in/);
  assert.equal(f.tasksElement.hidden, true);
  const card = f.appElement.querySelector('.auth-card');
  card.querySelector('#lEmail').value = 'person@example.test';
  card.querySelector('#lPass').value = 'synthetic-password';
  const event = submitEvent();
  f.app.http.post = async () => { throw new Error('incorrect credentials'); };
  await card.querySelector('#loginForm').onsubmit(event);
  assert.equal(event.prevented, true);
  assert.equal(card.querySelector('#lErr').textContent, 'incorrect credentials');
  assert.equal(f.counts().reloads, 0);
  f.app.http.post = async (path, body) => { f.posts.push({ path, body: JSON.parse(JSON.stringify(body)) }); };
  await card.querySelector('#loginForm').onsubmit(submitEvent());
  assert.deepEqual(f.posts, [{ path: '/api/auth/login', body: { email: 'person@example.test', password: 'synthetic-password' } }]);
  assert.equal(f.counts().reloads, 1);
  f.app.destroy(); f.app.destroy();
  assert.equal(f.listeners.get('hashchange')?.size || 0, 0);
  assert.equal(f.counts().removes, 0);
});

test('setup-required boot renders admin setup and submits exact account fields', async () => {
  const f = await fixture({ status: { setup_required: true, authenticated: false } });
  await f.app.start();
  assert.deepEqual(f.requests, ['/api/auth/status']);
  assert.equal(f.appElement.querySelector('#loginForm'), null);
  assert.match(f.appElement.innerHTML, /First start: create the admin account/);
  const card = f.appElement.querySelector('.auth-card');
  card.querySelector('#sName').value = 'Admin'; card.querySelector('#sEmail').value = 'admin@example.test'; card.querySelector('#sPass').value = 'synthetic-password';
  f.app.http.post = async () => { throw new Error('email already exists'); };
  await card.querySelector('#setupForm').onsubmit(submitEvent());
  assert.equal(card.querySelector('#sErr').textContent, 'email already exists');
  assert.equal(f.counts().reloads, 0);
  f.app.http.post = async (path, body) => { f.posts.push({ path, body: JSON.parse(JSON.stringify(body)) }); };
  await card.querySelector('#setupForm').onsubmit(submitEvent());
  assert.deepEqual(f.posts, [{ path: '/api/auth/setup', body: { name: 'Admin', email: 'admin@example.test', password: 'synthetic-password' } }]);
  assert.equal(f.counts().reloads, 1);
  f.app.destroy();
});

test('authenticated boot establishes project and overview before mounting and rendering', async () => {
  const user = { id: 8, role: 'admin', name: 'Admin' };
  const f = await fixture({ status: { setup_required: false, authenticated: true, user }, tasks: [] });
  const phases = [];
  f.app.assistant.mountAssistant = () => { assert.equal(f.app.state.project, 'project-one'); assert.equal(f.app.state.overview.project.name, 'Project One'); phases.push('mount'); };
  f.app.router.render = async () => { assert.deepEqual(phases, ['mount']); phases.push('render'); };
  await f.app.start(); await f.app.start();
  assert.deepEqual(f.requests, ['/api/auth/status', '/api/projects', '/api/overview', '/api/tasks?include_result=false']);
  assert.equal(f.app.state.user, user);
  assert.deepEqual(phases, ['mount', 'render']);
  assert.match(f.tasksElement.innerHTML, /Nothing running/);
  f.app.destroy(); assert.equal(f.listeners.get('hashchange').size, 0);
});

test('running tasks poll, stop through API, and refresh once on completion', async () => {
  const f = await fixture({ tasks: [{ id: 'task-7', state: 'running', kind: 'clean', args: { dataset: '<one>' }, log_tail: ['working'] }] });
  let renders = 0;
  f.app.navigation.render = () => { renders++; };
  await f.app.tasks.pollTasks();
  assert.equal(f.tasksElement.hidden, false);
  assert.match(f.tasksElement.innerHTML, /&lt;one&gt;/);
  assert.equal(f.timers.size, 1);
  assert.equal([...f.timers.values()][0].delay, 2500);
  f.tasksElement.querySelector('#tHide').onclick(); assert.equal(f.tasksElement.hidden, true);
  f.app.http.api = async (path) => { assert.equal(path, '/api/tasks?include_result=false'); return [{ id: 'task-7', state: 'succeeded', kind: 'clean' }]; };
  await f.tasksElement.querySelectorAll('[data-stop]')[0].onclick();
  await new Promise(setImmediate);
  assert.deepEqual(f.posts, [{ path: '/api/tasks/task-7/stop', body: {} }]);
  assert.equal(renders, 1);
  assert.deepEqual(f.errors, [['a task finished']]);
  assert.equal(f.timers.size, 0, 'completed tasks must stop polling');
  await f.app.tasks.pollTasks(); assert.equal(renders, 1, 'completed state must not repeatedly refresh the page');
  f.app.destroy();
});

test('completed quality profiles keep the waiting dialog open without refreshing the page', async () => {
  const f = await fixture({ tasks: [{ id: 'profile-1', state: 'running', kind: 'dataset.profile' }] });
  let renders = 0;
  f.app.navigation.render = () => { renders++; };
  await f.app.tasks.pollTasks();
  f.app.http.api = async () => [{ id: 'profile-1', state: 'succeeded', kind: 'dataset.profile' }];
  await f.app.tasks.pollTasks();
  assert.equal(renders, 0);
  assert.deepEqual(f.errors, [['a task finished']]);
  assert.equal(f.timers.size, 0);
  f.app.destroy();
});

test('quality dialog defers concurrent mutation refresh until it closes, exactly once', async () => {
  const tasks = [{id:'profile',state:'running',kind:'dataset.profile'}, {id:'eval',state:'running',kind:'eval.run'}];
  const f = await fixture({tasks});
  let renders = 0;
  f.app.navigation.render = () => { renders++; };
  const release = f.app.tasks.holdRefresh();
  await f.app.tasks.pollTasks();
  f.app.http.api = async () => tasks.map(t => ({...t,state:'succeeded'}));
  await f.app.tasks.pollTasks();
  assert.equal(renders, 0, 'mixed completion must preserve the quality dialog');
  assert.equal(f.timers.size, 0, 'refresh waits for dialog closure, not repeated polling');
  release();
  assert.equal(renders, 1, 'mutation completion must eventually refresh');
  release();
  await f.app.tasks.pollTasks();
  assert.equal(renders, 1, 'release and repeated terminal polls cannot duplicate refresh');
  f.app.destroy();
});

for (const finish of ['navigation', 'project-change', 'destroy']) {
  test(`deferred task refresh is discarded on ${finish}`, async () => {
    const f = await fixture({tasks:[{id:'eval',state:'running',kind:'eval.run'}]});
    let renders = 0;
    f.app.navigation.render = () => { renders++; };
    const release = f.app.tasks.holdRefresh();
    await f.app.tasks.pollTasks();
    f.app.http.api = async () => [{id:'eval',state:'succeeded',kind:'eval.run'}];
    await f.app.tasks.pollTasks();
    if (finish === 'navigation') release(false);
    if (finish === 'project-change') { f.app.state.project='another'; f.app.tasks.syncScope(); }
    if (finish === 'destroy') f.app.destroy();
    release();
    assert.equal(renders, 0);
    f.app.destroy();
  });
}

test('destroy clears pending task timer and ignores a late in-flight response', async () => {
  const f = await fixture({ tasks: [{ id: 'late', state: 'queued', kind: 'eval' }] });
  await f.app.tasks.pollTasks(); assert.equal(f.timers.size, 1);
  const before = f.tasksElement.innerHTML;
  let release;
  f.app.http.api = () => new Promise((resolve) => { release = resolve; });
  const pending = f.app.tasks.pollTasks();
  f.app.destroy(); assert.equal(f.timers.size, 0);
  release([{ id: 'late', state: 'succeeded', kind: 'eval' }]); await pending;
  assert.equal(f.tasksElement.innerHTML, before, 'disposed task controller must not change detached UI');
  assert.equal(f.app.state.tasks[0].state, 'queued');
  assert.equal(f.timers.size, 0);
  assert.deepEqual(f.errors, []);
});

const jsonResponse = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'content-type': 'application/json' } });
test('expired routed session keeps login visible and tears down authenticated work', async () => {
  const f = await fixture();
  f.app.state.user = { id: 8 }; f.app.state.project = 'one';
  f.app.http.api = f.realApi; f.app.http.background = f.realBackground;
  f.setNetwork(async () => jsonResponse({ detail: 'expired session' }, 401));
  f.app.router.start();
  await f.app.router.render();
  assert.match(f.appElement.innerHTML, /Sign in/);
  assert.ok(f.appElement.querySelector('#loginForm'));
  assert.equal(f.app.state.user, null);
  assert.equal(f.listeners.get('hashchange').size, 0);
  assert.deepEqual(f.errors, []);
  assert.equal(f.timers.size, 0);
  f.app.destroy();
});

test('real task transport survives navigation, deduplicates polling and retries transient errors', async () => {
  const f = await fixture();
  f.app.state.user = { id: 8 }; f.app.state.project = 'one';
  f.app.http.background = f.realBackground;
  f.app.assistant.beginRender('#/datasets');
  let release; const urls = [];
  f.setNetwork((url) => { urls.push(url); return new Promise(resolve => { release = resolve; }); });
  const pending = f.app.tasks.pollTasks();
  assert.equal(f.app.tasks.pollTasks(), pending, 'overlapping wakes must share the same request');
  f.context.location.hash = '#/stats'; f.app.assistant.beginRender('#/stats');
  release(jsonResponse([{ id: 'task', state: 'running', kind: 'eval' }])); await pending;
  assert.deepEqual(urls, ['/api/tasks?include_result=false&project=one']);
  assert.equal(f.app.state.tasks[0].state, 'running', 'a route change must not discard background state');
  assert.equal(f.timers.size, 1);
  assert.equal(f.app.state.pageContext.api.length, 0, 'background records must not enter page context');
  f.setNetwork(async () => { throw new Error('connection interrupted'); });
  await f.app.tasks.pollTasks();
  assert.equal(f.timers.size, 1, 'transient failures must schedule recovery');
  assert.equal([...f.timers.values()][0].delay, 2500);
  f.setNetwork(async () => jsonResponse([]));
  await f.app.tasks.pollTasks();
  assert.equal(f.timers.size, 0);
  f.app.destroy();
});

test('task scope changes abort old work and reject a late response from another project', async () => {
  const f = await fixture();
  f.app.state.user = { id: 8 }; f.app.state.project = 'one';
  f.app.http.background = f.realBackground;
  let release, signal;
  f.setNetwork((_url, options) => { signal = options.signal; return new Promise(resolve => { release = resolve; }); });
  const pending = f.app.tasks.pollTasks();
  f.app.state.project = 'two'; f.app.tasks.syncScope();
  assert.equal(signal.aborted, true);
  release(jsonResponse([{ id: 'foreign', state: 'running', kind: 'eval' }])); await pending;
  assert.equal(f.app.state.tasks.length, 0);
  assert.equal(f.tasksElement.innerHTML, '');
  assert.equal(f.timers.size, 0);
  f.app.destroy();
});

test('a delayed successful page response cannot replace sign-in after a background 401', async () => {
  const f = await fixture(); f.app.state.user = { id: 8 }; f.app.state.project = 'one';
  f.app.http.api = f.realApi; f.app.http.background = f.realBackground;
  let release;
  f.setNetwork(async (url) => {
    if (url.endsWith('/overview')) return new Promise(resolve => { release = resolve; });
    if (url.startsWith('/api/tasks?include_result=false')) return jsonResponse({ detail: 'expired' }, 401);
    return jsonResponse([]);
  });
  let protectedRenders = 0;
  f.app.shell.layout = () => { protectedRenders++; f.appElement.innerHTML = 'protected page'; return new Element(); };
  const page = f.app.router.render(); await new Promise(setImmediate);
  await assert.rejects(f.app.http.background('/api/tasks?include_result=false'), { name: 'AuthenticationRequiredError' });
  assert.ok(f.appElement.querySelector('#loginForm'));
  release(jsonResponse({ tree: { datasets: [] } })); await page;
  assert.ok(f.appElement.querySelector('#loginForm'), 'authentication recovery must survive an older success');
  assert.equal(protectedRenders, 0);
  assert.equal(f.app.state.pageContext, null); assert.equal(f.app.state.domContext, null);
  assert.deepEqual(f.errors, []); f.app.destroy();
});
