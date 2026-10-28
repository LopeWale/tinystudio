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
  let timerId = 0, reloads = 0, adds = 0, removes = 0, syncStarts = 0;
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
  class ConversationSync { start() { syncStarts++; } stop() {} notifySaved() {} requestCheck() {} }
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
  const app = await loadFrontend(context, { transforms: options.transforms || {} });
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
  return { app, appElement, tasksElement, timers, cleared, requests, posts, errors, context, realApi, realBackground, setNetwork: (fn) => { network = fn; }, counts: () => ({ reloads, adds, removes, syncStarts }), listeners };
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

test('startup failure gives a visible retry instead of leaving a blank workspace', async () => {
  const f = await fixture();
  f.app.http.api = async () => { throw new Error('offline'); };
  await f.app.start();
  assert.match(f.appElement.innerHTML, /Connection unavailable/);
  const retry = f.appElement.querySelector('.auth-card').querySelector('#reloadApp');
  assert.equal(typeof retry.onclick, 'function'); retry.onclick();
  assert.equal(f.counts().reloads, 1); assert.equal(f.counts().adds, 0);
  f.app.destroy();
});

test('teardown during startup prevents a late auth response from mounting the app', async () => {
  const f = await fixture(); let release, mounts = 0;
  f.app.http.api = () => new Promise(resolve => { release = resolve; });
  f.app.assistant.mountAssistant = () => { mounts++; };
  const pending = f.app.start(); f.app.destroy();
  release({ authenticated: true, user: { id: 8 } }); await pending;
  assert.equal(mounts, 0); assert.equal(f.app.state.user, null);
  assert.equal(f.counts().adds, 0); assert.equal(f.timers.size, 0);
});


const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const authenticatedStatus = { setup_required: false, authenticated: true, user: { id: 8, role: 'admin' } };

// Disposal is a boundary before state writes and scope-controller starts, not just
// before routing. Both valid and empty late lists must be harmless.
for (const projects of [[{ slug: 'late-project', name: 'Late Project' }], []]) {
  test(`teardown rejects a late ${projects.length ? 'populated' : 'empty'} project response before any startup effect`, async () => {
    const f = await fixture({ status: authenticatedStatus });
    const response = deferred(), entered = deferred(); let modals = 0, mounts = 0;
    const api = f.app.http.api;
    f.app.http.api = (path) => {
      if (path !== '/api/projects') return api(path);
      f.requests.push(path); entered.resolve(); return response.promise;
    };
    f.app.dialogs.modal = () => { modals++; throw new Error('disposed application opened a project modal'); };
    f.app.assistant.mountAssistant = () => { mounts++; };
    const pending = f.app.start(); await entered.promise;
    f.app.destroy(); response.resolve(projects); await pending;
    assert.equal(f.app.state.project, null); assert.equal(f.app.state.projects.length, 0);
    assert.equal(f.app.state.overview, null); assert.equal(f.counts().syncStarts, 0);
    assert.equal(modals, 0); assert.equal(mounts, 0); assert.equal(f.counts().adds, 0);
    assert.equal(f.timers.size, 0); assert.deepEqual(f.requests, ['/api/auth/status', '/api/projects']);
    assert.deepEqual(f.errors, []); await f.app.start(); assert.equal(f.requests.length, 2);
  });
}

test('teardown rejects a delayed startup overview before caching or mounting', async () => {
  const f = await fixture({ status: authenticatedStatus });
  const response = deferred(), entered = deferred(); let mounts = 0;
  const api = f.app.http.api;
  f.app.http.api = (path) => {
    if (path !== '/api/overview') return api(path);
    f.requests.push(path); entered.resolve(); return response.promise;
  };
  f.app.assistant.mountAssistant = () => { mounts++; };
  const pending = f.app.start(); await entered.promise;
  assert.equal(f.app.state.project, 'project-one'); const starts = f.counts().syncStarts;
  f.app.destroy(); response.resolve({ project: { name: 'Late overview' }, tree: { datasets: [] } }); await pending;
  assert.equal(f.app.state.overview, null); assert.equal(f.app.state.project, 'project-one');
  assert.equal(f.counts().syncStarts, starts); assert.equal(mounts, 0); assert.equal(f.counts().adds, 0);
  assert.equal(f.timers.size, 0); assert.deepEqual(f.errors, []);
  assert.deepEqual(f.requests, ['/api/auth/status', '/api/projects', '/api/overview']);
  await assert.rejects(f.app.overview.ensureOverview(), { name: 'StaleRenderError' });
});

test('project creation already in flight cannot revive startup after its modal is disposed', async () => {
  const f = await fixture({ status: authenticatedStatus });
  const modalOpened = deferred(), created = deferred(); let options, button, closed = false, projectRequests = 0;
  const api = f.app.http.api;
  f.app.http.api = (path) => { if (path === '/api/projects') { projectRequests++; return Promise.resolve([]); } return api(path); };
  f.app.dialogs.modal = (configuration) => {
    options = configuration;
    const el = new Element(), name = new Element(), goal = new Element(); button = new Element();
    name.value = 'new-project'; goal.value = 'Support goal';
    el.nodes.set('#prName', name); el.nodes.set('#prGoal', goal); el.nodes.set('[data-ok]', button);
    modalOpened.resolve();
    return { el, close() { if (!closed) { closed = true; options.onClose('dismiss'); } } };
  };
  f.app.dialogs.closeAll = () => { if (options && !closed) { closed = true; options.onClose('navigation'); } };
  f.app.http.post = () => created.promise;
  const startup = f.app.start(); await modalOpened.promise;
  const creating = button.onclick(); f.app.destroy(); await startup;
  assert.equal(closed, true); assert.equal(f.app.state.project, null);
  created.resolve({ slug: 'created-too-late' }); await creating;
  assert.equal(projectRequests, 1, 'late create response must not trigger project selection');
  assert.equal(f.app.state.project, null); assert.equal(f.app.state.projects.length, 0);
  assert.equal(f.counts().syncStarts, 0); assert.equal(f.counts().adds, 0); assert.equal(f.timers.size, 0); assert.deepEqual(f.errors, []);
});

test('ordinary project switches remain available until the project controller is disposed', async () => {
  const f = await fixture(); f.app.state.user = { id: 8 };
  const available = [{ slug: 'one' }, { slug: 'two' }]; let requests = 0;
  f.app.http.api = async (path) => { assert.equal(path, '/api/projects'); requests++; return available; };
  assert.equal(await f.app.projects.selectProject('one'), 'one');
  assert.equal(await f.app.projects.selectProject('two'), 'two'); assert.equal(f.app.state.project, 'two');
  assert.equal(f.context.localStorage.getItem('tinystudio.project'), 'two');
  assert.equal(f.counts().syncStarts, 2, 'each completed live project change updates the assistant scope');
  f.app.destroy();
  await assert.rejects(f.app.projects.selectProject('one'), { name: 'StaleRenderError' });
  await assert.rejects(f.app.projects.ensureProject(), { name: 'StaleRenderError' });
  assert.equal(requests, 2); assert.equal(f.app.state.project, 'two'); assert.equal(f.counts().syncStarts, 2);
});


test('controlled fault: the project disposal assertions reject a removed post-await guard', async () => {
  const f = await fixture({ status: authenticatedStatus, transforms: {
    'core/projects.js': (source) => {
      const changed = source.replace('assertActive(); state.projects = next;', 'state.projects = next;');
      assert.notEqual(changed, source, 'fault must remove the actual post-response boundary'); return changed;
    },
  } });
  const entered = deferred(), response = deferred(), api = f.app.http.api;
  f.app.http.api = (path) => {
    if (path !== '/api/projects') return api(path);
    entered.resolve(); return response.promise;
  };
  const pending = f.app.start(); await entered.promise;
  f.app.destroy(); response.resolve([{ slug: 'violating-late-project' }]); await pending;
  assert.throws(() => assert.equal(f.app.state.project, null), { code: 'ERR_ASSERTION' });
});


for (const reason of ['escape', 'backdrop', 'dismiss']) {
  test(`first-project ${reason} dismissal exposes a setup action that recovers without reloading`, async () => {
    const f = await fixture({ status: authenticatedStatus });
    const opened = [deferred(), deferred()]; const modals = []; let available = [], mounts = 0, renders = 0;
    const api = f.app.http.api;
    f.app.http.api = (path) => {
      if (path !== '/api/projects') return api(path);
      f.requests.push(path); return Promise.resolve(available);
    };
    f.app.dialogs.modal = (configuration) => {
      const el = new Element(), name = new Element(), goal = new Element(), button = new Element();
      name.value = 'support-project'; goal.value = 'Support goal';
      el.nodes.set('#prName', name); el.nodes.set('#prGoal', goal); el.nodes.set('[data-ok]', button);
      const m = { configuration, button, closed: false, el, close(closeReason = 'dismiss') {
        if (!m.closed) { m.closed = true; configuration.onClose(closeReason); }
      } };
      modals.push(m); opened[modals.length - 1].resolve(); return m;
    };
    f.app.dialogs.closeAll = () => { for (const m of modals) m.close('navigation'); };
    f.app.http.post = async (path, body) => {
      assert.equal(path, '/api/projects'); assert.equal(body.name, 'support-project');
      available = [{ slug: 'support-project', name: 'Support Project' }]; return available[0];
    };
    f.app.assistant.mountAssistant = () => { mounts++; };
    f.app.router.render = async () => { renders++; };
    const initial = f.app.start(); await opened[0].promise; modals[0].close(reason); await initial;
    assert.match(f.appElement.innerHTML, /Create your first project/);
    assert.match(f.appElement.innerHTML, /Create one to open your workspace/);
    const resume = f.appElement.querySelector('#resumeProjectSetup');
    assert.equal(typeof resume?.onclick, 'function', 'dismissal must leave an actionable visible page');
    assert.equal(mounts, 0); assert.equal(renders, 0); assert.equal(f.counts().adds, 0); assert.equal(f.counts().reloads, 0);
    assert.equal(f.app.state.project, null); assert.equal(f.app.state.overview, null); assert.equal(f.tasksElement.hidden, true);
    const retry = resume.onclick(); await opened[1].promise;
    await modals[1].button.onclick(); await retry;
    assert.equal(f.app.state.project, 'support-project'); assert.equal(f.app.state.overview.project.name, 'Project One');
    assert.equal(mounts, 1); assert.equal(renders, 1); assert.equal(f.counts().adds, 1); assert.equal(f.counts().reloads, 0);
    assert.equal(modals[1].closed, true); assert.deepEqual(f.errors, []);
    assert.deepEqual(f.requests, ['/api/auth/status', '/api/projects', '/api/auth/status', '/api/projects', '/api/projects', '/api/overview', '/api/tasks?include_result=false']);
    f.app.destroy(); assert.equal(f.timers.size, 0);
  });
}

for (const boundary of ['creation', 'selection', 'creation-failure']) {
  test(`dismissed first-project ${boundary} cannot overwrite a successful setup retry`, async () => {
    const f = await fixture({ status: authenticatedStatus });
    const opened = [deferred(), deferred()], oldResult = deferred(), selecting = deferred();
    const modals = []; let available = [], postCalls = 0, projectReads = 0, heldSelection = false;
    const api = f.app.http.api;
    f.app.http.api = path => {
      if (path === '/api/projects') {
        projectReads++;
        if (boundary === 'selection' && postCalls === 1 && !heldSelection) {
          heldSelection = true; selecting.resolve(); return oldResult.promise;
        }
        return Promise.resolve(available);
      }
      if (path === '/api/overview') return Promise.resolve({ project: { name: f.app.state.project }, tree: { datasets: [] } });
      return api(path);
    };
    f.app.http.post = async path => {
      assert.equal(path, '/api/projects'); postCalls++;
      if (postCalls === 1) return boundary === 'selection' ? { slug: 'dismissed-project' } : oldResult.promise;
      available = [{ slug: 'retry-project' }]; return available[0];
    };
    f.app.dialogs.modal = configuration => {
      const el = new Element(), name = new Element(), goal = new Element(), button = new Element();
      name.value = 'test-project'; goal.value = 'Test goal';
      el.nodes.set('#prName', name); el.nodes.set('#prGoal', goal); el.nodes.set('[data-ok]', button);
      const m = { el, button, closed: false, close(reason = 'dismiss') { if (!m.closed) { m.closed = true; configuration.onClose(reason); } } };
      modals.push(m); opened[modals.length - 1].resolve(); return m;
    };
    f.app.dialogs.closeAll = () => modals.forEach(m => m.close('navigation'));
    f.app.assistant.mountAssistant = () => {};
    f.app.router.render = async () => { f.app.assistant.beginRender('#/datasets'); };
    const startup = f.app.start(); await opened[0].promise;
    const oldSubmit = modals[0].button.onclick();
    await modals[0].button.onclick(); assert.equal(postCalls, 1, 'duplicate submits share one pending creation');
    if (boundary === 'selection') await selecting.promise;
    modals[0].close('escape'); await startup;
    assert.match(f.appElement.innerHTML, /Create your first project/);
    const retry = f.appElement.querySelector('#resumeProjectSetup').onclick(); await opened[1].promise;
    await modals[1].button.onclick(); await retry;
    assert.equal(f.app.state.project, 'retry-project'); assert.equal(f.app.state.overview.project.name, 'retry-project');
    const capture = f.app.state.pageContext, starts = f.counts().syncStarts, reads = projectReads;
    assert.ok(capture); assert.equal(capture.project, 'retry-project');
    if (boundary === 'creation-failure') oldResult.reject(new Error('dismissed request failed'));
    else oldResult.resolve(boundary === 'selection' ? [{ slug: 'dismissed-project' }, ...available] : { slug: 'dismissed-project' });
    await oldSubmit;
    assert.equal(f.app.state.project, 'retry-project'); assert.equal(f.app.state.overview.project.name, 'retry-project');
    assert.equal(f.app.state.projects.length, 1); assert.equal(f.app.state.projects[0].slug, 'retry-project');
    assert.equal(f.context.localStorage.getItem('tinystudio.project'), 'retry-project');
    assert.equal(f.app.state.pageContext, capture); assert.equal(f.counts().syncStarts, starts); assert.equal(projectReads, reads);
    assert.deepEqual(f.errors, []); assert.equal(postCalls, 2); f.app.destroy();
  });
}
