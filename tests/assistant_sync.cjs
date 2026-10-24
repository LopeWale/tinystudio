// Browser-boundary integration: real scheduling, real studio reconciliation, fake HTTP/DOM.
// Run directly with: node --test tests/assistant_sync.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const { setImmediate: immediate } = require('node:timers/promises');
const root = path.resolve(__dirname, '..');
const controllerSource = fs.readFileSync(path.join(root, 'tinystudio/ui/assistant/conversation-sync.js'), 'utf8').replace('export class ConversationSync', 'class ConversationSync');
const studioSource = fs.readFileSync(path.join(root, 'tinystudio/ui/studio.js'), 'utf8')
  .replace(/^import \{ ConversationSync \} from .*;\s*$/m, '')
  .split('// ------------------------------------------------------------------ boot')[0];
const copy = value => JSON.parse(JSON.stringify(value));
const flush = async () => { await immediate(); await immediate(); };
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }
class Clock {
  time = 0; next = 1; tasks = new Map();
  setTimeout(fn, delay = 0) { const id = this.next++; this.tasks.set(id, { at: this.time + delay, fn }); return id; }
  clearTimeout(id) { this.tasks.delete(id); }
  async tick(duration = 0) {
    const end = this.time + duration;
    await flush();
    for (;;) {
      const entry = [...this.tasks].filter(([, task]) => task.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!entry) break;
      this.time = entry[1].at; this.tasks.delete(entry[0]); entry[1].fn(); await flush();
    }
    this.time = end; await flush();
  }
}
class Events {
  handlers = new Map();
  addEventListener(type, fn) { if (!this.handlers.has(type)) this.handlers.set(type, new Set()); this.handlers.get(type).add(fn); }
  removeEventListener(type, fn) { this.handlers.get(type)?.delete(fn); }
  emit(type) { for (const fn of [...this.handlers.get(type) || []]) fn(); }
  count(type) { return this.handlers.get(type)?.size || 0; }
}
class Element extends Events {
  constructor(id = '', document = null) {
    super(); this.id = id; this.document = document; this.children = new Map(); this.value = ''; this.textContent = '';
    this.disabled = false; this.hidden = false; this.scrollTop = 0; this.scrollHeight = 1200; this.clientHeight = 300;
    this.selectionStart = 0; this.selectionEnd = 0; this.writes = 0; this.dataset = {};
    this.classList = { toggle() {}, add() {}, remove() {} };
  }
  set innerHTML(value) {
    this.html = value; this.writes++;
    if (this.id === 'asPage') {
      // A complete redraw replaces children, as the browser does; preservation cannot pass accidentally.
      this.children.clear();
      for (const [, id] of value.matchAll(/\bid="([^"]+)"/g)) this.children.set(id, new Element(id, this.document));
    }
    if (this.id === 'asLog') this.lastElementChild = new Element('', this.document);
  }
  get innerHTML() { return this.html || ''; }
  querySelector(selector) { return this.children.get(selector.replace(/^#/, '')) || null; }
  querySelectorAll(selector) { return selector.split(',').map(part => this.querySelector(part.trim())).filter(Boolean); }
  focus() { this.document.activeElement = this; }
  setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
  appendChild() {}
  remove() {}
}
class Document extends Events {
  visibilityState = 'visible'; activeElement = null; page = null;
  constructor() { super(); this.body = new Element('body', this); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  querySelectorAll(selector) {
    if (!this.page) return [];
    return selector.split(',').map(part => {
      part = part.trim();
      if (part === '#asPage') return this.page;
      return part.startsWith('#') ? this.page.children.get(part.slice(1)) : null;
    }).filter(Boolean);
  }
  createElement() { return new Element('', this); }
  getElementById(id) { return this.querySelector(`#${id}`); }
}
class SharedMapDB {
  rows = new Map();
  key(user, project) { return JSON.stringify([user, project]); }
  get(user, project) { return copy(this.rows.get(this.key(user, project)) || { revision: 0, history: [], provider: 'anthropic', model: 'model-a' }); }
  put(user, project, snapshot) {
    const current = this.get(user, project);
    if (snapshot.revision !== current.revision) return { status: 409, detail: 'Conversation changed' };
    const next = { ...copy(snapshot), revision: current.revision + 1 };
    this.rows.set(this.key(user, project), next); return { status: 200, snapshot: copy(next) };
  }
}
class Stream {
  chunks = []; waiting = null; closed = false;
  getReader() { return this; }
  read() {
    if (this.chunks.length) return Promise.resolve({ value: this.chunks.shift(), done: false });
    if (this.closed) return Promise.resolve({ done: true });
    return new Promise(resolve => { this.waiting = resolve; });
  }
  event(value) {
    const chunk = new TextEncoder().encode(`data: ${JSON.stringify(value)}\n\n`);
    if (this.waiting) { const resolve = this.waiting; this.waiting = null; resolve({ value: chunk, done: false }); }
    else this.chunks.push(chunk);
  }
  end() { this.closed = true; if (this.waiting) { this.waiting({ done: true }); this.waiting = null; } }
}
function browser(db, { user = 'alice', project = 'demo', studio = studioSource, initialFetch = null, modelCatalog = null } = {}) {
  const clock = new Clock(), document = new Document(), window = new Events(), navigator = { onLine: true };
  const requests = [], storage = new Map(), faults = initialFetch ? [initialFetch] : [];
  let pause = null, chat = null, cookiePrincipal = user;
  const response = (status, body, etag, headers = {}) => ({ status, ok: status >= 200 && status < 300, statusText: String(status),
    headers: { get: name => name.toLowerCase() === 'etag' ? etag : name.toLowerCase() === 'content-type' ? 'application/json' : headers[name.toLowerCase()] ?? null },
    json: async () => copy(body), text: async () => JSON.stringify(body) });
  const context = vm.createContext({ console, window, document, navigator, BroadcastChannel: undefined,
    AbortController, TextDecoder, TextEncoder, FormData, URL, URLSearchParams,
    Date: class extends Date { static now() { return clock.time; } },
    setTimeout: clock.setTimeout.bind(clock), clearTimeout: clock.clearTimeout.bind(clock),
    localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
    location: { hash: '#/assistant', origin: 'http://test.invalid' },
    fetch: async (url, options = {}) => {
      // Authentication belongs to the server cookie, independently of stale frontend state.
      const owner = cookiePrincipal;
      const expectedOwner = options.headers?.['X-Conversation-Owner'];
      const ownerMismatch = expectedOwner !== undefined && expectedOwner !== owner;
      if (url === '/api/assistant/models') {
        requests.push({ url, user: owner, method: 'GET' });
        assert.ok(modelCatalog, 'a model catalog response must be explicitly configured');
        return response(200, modelCatalog);
      }
      if (url === '/api/assistant/chat') {
        if (ownerMismatch) return response(403, { detail: 'Conversation owner changed; sign in again' }, null, { 'x-conversation-owner-mismatch': 'true' });
        chat = new Stream(); requests.push({ url, user: owner, method: 'POST', signal: options.signal, body: JSON.parse(options.body) });
        return { ok: true, status: 200, body: chat };
      }
      const match = url.match(/^\/api\/p\/([^/]+)\/assistant\/conversation$/);
      assert.ok(match, `Unexpected HTTP request: ${url}`);
      const project = decodeURIComponent(match[1]);
      const request = { url, user: owner, project, method: options.method || 'GET', headers: options.headers, signal: options.signal };
      requests.push(request);
      if (ownerMismatch) {
        request.status = 403;
        return response(403, { detail: 'Conversation owner changed; sign in again' }, null, { 'x-conversation-owner-mismatch': 'true' });
      }
      if (request.method === 'PUT') {
        request.body = JSON.parse(options.body);
        const result = db.put(owner, project, request.body);
        return result.status === 200 ? response(200, result.snapshot, `"${result.snapshot.revision}"`) : response(409, { detail: result.detail });
      }
      const snapshot = db.get(owner, project), etag = `"${snapshot.revision}"`;
      const fault = faults.shift();
      if (fault === 'hang') {
        await new Promise((_, reject) => {
          const aborted = () => reject(Object.assign(new Error('Request aborted'), { name: 'AbortError' }));
          if (options.signal?.aborted) aborted();
          else options.signal?.addEventListener('abort', aborted, { once: true });
        });
      }
      if (fault === 'fail') { request.status = 503; return response(503, { detail: 'Temporary initial load failure' }); }
      const status = options.headers?.['If-None-Match'] === etag ? 304 : 200;
      request.status = status;
      if (pause && options.headers?.['If-None-Match']) {
        const barrier = pause; pause = null; barrier.request = request; await barrier.promise;
      }
      // Deliberately allow delayed responses after abort, exercising the consumer's late-result guards.
      return response(status, snapshot, etag);
    },
  });
  function run(source) { return vm.runInContext(source, context); }
  vm.runInContext(`${controllerSource}\n${studio}`, context, { filename: 'studio-sync-integration.js' });
  const b = {
    db, clock, document, window, navigator, requests, run,
    begin() {
      run(`state.user = { id: ${JSON.stringify(user)} }; state.project = ${JSON.stringify(project)}; syncAssistantScope();`);
      document.page = new Element('asPage', document);
      run('ASSIST.models = {anthropic:{available:true,models:[{id:"model-a"}]},openai:{available:false,models:[]}}; ASSIST.attach = false; renderAssistant(document.page, {full:true});');
      return b;
    },
    async ready() {
      b.begin(); await flush(); await clock.tick(); return b;
    },
    async save(content) {
      run(`ASSIST.history.push({role:'user',content:${JSON.stringify(content)}});`);
      await run('assistSave()'); await flush();
    },
    async clear() { run('ASSIST.session.history = []'); await run('assistSave()'); await flush(); },
    history() { return copy(run('ASSIST.history')); },
    get node() { return document.page; },
    get input() { return document.page.children.get('asInput'); },
    pauseNextGet() { pause = deferred(); return pause; },
    get cookieUser() { return cookiePrincipal; },
    set cookieUser(value) { cookiePrincipal = value; },
    get stream() { return chat; },
    stop() { run('assistantSync?.stop()'); },
  };
  return b;
}
async function pair(t) {
  const db = new SharedMapDB(), writer = await browser(db).ready(), reader = await browser(db).ready();
  t.after(() => { writer.stop(); reader.stop(); }); return { db, writer, reader };
}

test('independent devices receive saved turns and remote clear by polling while composer state survives', async t => {
  const { writer, reader } = await pair(t);
  const input = reader.input, page = reader.node, log = page.children.get('asLog');
  input.value = 'draft stays here'; input.focus(); input.setSelectionRange(3, 10); log.scrollTop = 170;
  const rootWrites = page.writes;
  await writer.save('sent from another device');
  assert.equal(reader.history().length, 0);
  await reader.clock.tick(3000);
  assert.equal(reader.history()[0].content, 'sent from another device');
  assert.match(log.innerHTML, /sent from another device/);
  assert.equal(reader.input, input); assert.equal(page.writes, rootWrites);
  assert.equal(input.value, 'draft stays here'); assert.equal(reader.document.activeElement, input);
  assert.deepEqual([input.selectionStart, input.selectionEnd, log.scrollTop], [3, 10, 170]);
  await writer.clear(); await reader.clock.tick(3000);
  assert.deepEqual(reader.history(), []); assert.doesNotMatch(log.innerHTML, /sent from another device/);
  assert.equal(reader.input, input); assert.equal(input.value, 'draft stays here');
  assert.equal(reader.requests.filter(r => r.method === 'PUT').length, 0, 'polling must never write back');
});

test('unchanged ETag produces 304 without transcript or composer redraw', async t => {
  const { reader } = await pair(t); const page = reader.node, log = page.children.get('asLog');
  const rootWrites = page.writes, logWrites = log.writes;
  await reader.clock.tick(9000);
  const gets = reader.requests.filter(r => r.method === 'GET');
  assert.equal(gets.at(-1).headers['If-None-Match'], '"0"'); assert.equal(gets.at(-1).status, 304);
  assert.equal(page.writes, rootWrites); assert.equal(log.writes, logWrites);
});

test('newer database snapshot retains dirty local messages and exposes conflict without a PUT', async t => {
  const { writer, reader } = await pair(t);
  reader.run('ASSIST.history.push({role:"assistant",content:"unsaved local answer"}); ASSIST.session.dirty = true; ASSIST.session.edits = 1;');
  await writer.save('different remote answer'); await reader.clock.tick(3000);
  assert.deepEqual(reader.history(), [{ role: 'assistant', content: 'unsaved local answer' }]);
  assert.equal(reader.run('ASSIST.session.conflict'), true);
  assert.match(reader.node.children.get('asPersistence').textContent, /Conflict:.*unsaved messages are retained/);
  assert.equal(reader.requests.filter(r => r.method === 'PUT').length, 0);
});

test('a GET already in flight cannot overwrite local edits', async t => {
  const { writer, reader } = await pair(t); await writer.save('remote while fetching');
  const delayed = reader.pauseNextGet(); await reader.clock.tick(3000);
  reader.run('ASSIST.history.push({role:"user",content:"local edit during GET"}); ASSIST.session.edits = 1; ASSIST.session.dirty = true;');
  delayed.resolve(); await flush();
  assert.equal(reader.history()[0].content, 'local edit during GET');
  assert.equal(reader.run('ASSIST.session.revision'), 0);
  // The next scheduled check observes the conflict, retaining the original local copy.
  await reader.clock.tick(3000); assert.equal(reader.run('ASSIST.session.conflict'), true);
});

test('real send stream suppresses background GETs and automatically catches up on completion', async t => {
  const { db, reader } = await pair(t);
  reader.input.value = 'start stream'; const sending = reader.node.children.get('asSend').onclick(); await flush();
  assert.ok(reader.stream); assert.equal(reader.run('ASSIST.session.busy && ASSIST.session.streaming'), true);
  const getCount = reader.requests.filter(r => r.method === 'GET').length;
  await reader.clock.tick(9000); assert.equal(reader.requests.filter(r => r.method === 'GET').length, getCount);
  reader.stream.event({ type: 'text', delta: 'streamed answer' }); await flush();
  const completed = db.put('alice', 'demo', { ...db.get('alice', 'demo'), history: reader.history() }).snapshot;
  reader.stream.event({ type: 'conversation_saved', revision: completed.revision });
  reader.stream.end(); await sending;
  assert.equal(reader.run('ASSIST.session.busy'), false);
  // A distinct later device write appears without manually requesting another check.
  const current = db.get('alice', 'demo');
  db.put('alice', 'demo', { ...current, history: [...current.history, { role: 'user', content: 'arrived as streaming completed' }] });
  await reader.clock.tick();
  assert.equal(reader.history().at(-1).content, 'arrived as streaming completed');
});

test('offline suppresses reads and reconnect catches up with remote writes', async t => {
  const { writer, reader } = await pair(t);
  reader.navigator.onLine = false; reader.window.emit('offline');
  const getCount = reader.requests.length; await writer.save('written while reader offline');
  reader.window.emit('focus'); await reader.clock.tick(60000); assert.equal(reader.requests.length, getCount);
  assert.match(reader.node.children.get('asSyncStatus').textContent, /Offline/);
  reader.navigator.onLine = true; reader.window.emit('online'); await reader.clock.tick();
  assert.equal(reader.history()[0].content, 'written while reader offline');
});

test('scope change aborts old controller and a delayed old GET cannot change the new project', async t => {
  const { db, writer, reader } = await pair(t); await writer.save('old project update');
  const delayed = reader.pauseNextGet(); await reader.clock.tick(3000); assert.equal(delayed.request.signal.aborted, false);
  db.put('alice', 'other', { revision: 0, history: [{ role: 'user', content: 'other project history' }], provider: 'anthropic', model: 'model-a' });
  reader.run('state.project = "other"; syncAssistantScope();'); await flush(); await reader.clock.tick();
  assert.equal(delayed.request.signal.aborted, true);
  const activeRequests = reader.requests.length;
  delayed.resolve(); await flush();
  assert.equal(reader.run('ASSIST.session.project'), 'other'); assert.equal(reader.history()[0].content, 'other project history');
  await reader.clock.tick(3000);
  assert.ok(reader.requests.slice(activeRequests).every(r => r.project === 'other'));
  reader.window.emit('pagehide'); const afterHide = reader.requests.length;
  await reader.clock.tick(30000); assert.equal(reader.requests.length, afterHide);
  assert.equal(reader.window.count('focus'), 0);
});

test('different users in the same project remain isolated and identity changes replace the controller', async t => {
  const db = new SharedMapDB(), alice = await browser(db, { user: 'alice' }).ready(), bob = await browser(db, { user: 'bob' }).ready();
  t.after(() => { alice.stop(); bob.stop(); });
  await alice.save('private alice content'); await bob.clock.tick(6000); assert.deepEqual(bob.history(), []);
  await bob.save('private bob content'); await alice.clock.tick(3000); assert.equal(alice.history()[0].content, 'private alice content');
  const delayed = alice.pauseNextGet(); await alice.clock.tick(3000);
  alice.cookieUser = 'bob';
  alice.run('state.user = {id:"bob"}; syncAssistantScope();'); await flush(); await alice.clock.tick();
  assert.equal(delayed.request.signal.aborted, true); delayed.resolve(); await flush();
  assert.deepEqual(alice.history(), [{ role: 'user', content: 'private bob content' }]);
  assert.equal(alice.run('ASSIST.session.user'), 'bob');
});

test('the automatic-update assertion detects a controlled missing-start wiring fault', async t => {
  const faulty = studioSource.replace('assistantSync.start();', '/* controlled fault: scheduler never starts */');
  assert.notEqual(faulty, studioSource, 'the controlled fault must modify the actual wiring');
  const db = new SharedMapDB(), writer = await browser(db).ready(), reader = await browser(db, { studio: faulty }).ready();
  t.after(() => { writer.stop(); reader.stop(); });
  await writer.save('must arrive automatically'); await reader.clock.tick(6000);
  assert.throws(() => assert.equal(reader.history()[0]?.content, 'must arrive automatically'), assert.AssertionError);
});

test('cookie account switches with stale frontend identity stop sync without adopting or writing another user history', async t => {
  const db = new SharedMapDB(), reader = await browser(db).ready(); t.after(() => reader.stop());
  db.put('bob', 'demo', { revision: 0, history: [{ role: 'user', content: 'private bob history' }], provider: 'anthropic', model: 'model-a' });
  reader.cookieUser = 'bob'; // state.user deliberately remains alice.
  await reader.clock.tick(3000);
  assert.equal(reader.run('state.user.id'), 'alice');
  assert.deepEqual(reader.history(), [], 'a stale frontend cannot adopt the new cookie principal history');
  assert.equal(reader.run('ASSIST.session.ownerMismatch'), true);
  assert.equal(reader.requests.at(-1).headers['X-Conversation-Owner'], 'alice');
  const count = reader.requests.length;
  await reader.clock.tick(60000); assert.equal(reader.requests.length, count, 'owner mismatch stops automatic polling');
  await assert.rejects(() => reader.save('must stay local'), /owner|sign in|account/i);
  assert.equal(reader.requests.filter(r => r.method === 'PUT').length, 0, 'known ownership mismatch must refuse PUT before HTTP');
  assert.deepEqual(db.get('bob', 'demo').history, [{ role: 'user', content: 'private bob history' }]);
});

test('initial hung load is bounded by AbortSignal timeout and automatically recovers', async t => {
  const db = new SharedMapDB(); db.put('alice', 'demo', { revision: 0, history: [{ role: 'user', content: 'initial recovered history' }], provider: 'anthropic', model: 'model-a' });
  const b = browser(db, { initialFetch: 'hang' }).begin(); t.after(() => b.stop());
  await b.clock.tick(); const first = b.requests[0];
  assert.ok(first.signal, 'the first conversation GET must already be controller-owned');
  assert.equal(first.signal.aborted, false); assert.equal(b.run('ASSIST.session.loaded'), false);
  await b.clock.tick(10000); assert.equal(first.signal.aborted, true, 'initial load timeout must abort the transport');
  await b.clock.tick(3000);
  assert.equal(b.run('ASSIST.session.loaded'), true); assert.equal(b.history()[0].content, 'initial recovered history');
});

test('scope switch and pagehide each abort the very first pending conversation GET', async t => {
  for (const action of ['scope', 'pagehide']) {
    const b = browser(new SharedMapDB(), { initialFetch: 'hang' }).begin(); t.after(() => b.stop());
    await b.clock.tick(); const first = b.requests[0]; assert.ok(first.signal, 'initial GET must be abortable');
    if (action === 'scope') b.run('state.project = "other"; syncAssistantScope();');
    else b.window.emit('pagehide');
    await flush(); assert.equal(first.signal.aborted, true, `${action} aborts initial request immediately`);
    if (action === 'scope') {
      await b.clock.tick(); assert.equal(b.run('ASSIST.session.project'), 'other'); assert.equal(b.run('ASSIST.session.loaded'), true);
    } else {
      const count = b.requests.length; await b.clock.tick(30000); assert.equal(b.requests.length, count);
      assert.equal(b.window.count('focus'), 0);
      b.window.emit('pageshow'); await b.clock.tick();
      assert.equal(b.run('ASSIST.session.loaded'), true, 'returning to the page retries the aborted initial load');
    }
  }
});

test('initial loading error and automatic recovery preserve an already rendered focused draft', async t => {
  const db = new SharedMapDB(); db.put('alice', 'demo', { revision: 0, history: [{ role: 'user', content: 'loaded after retry' }], provider: 'anthropic', model: 'model-a' });
  const b = browser(db, { initialFetch: 'fail' }).begin(); t.after(() => b.stop());
  const input = b.input, page = b.node, writes = page.writes;
  input.value = 'typing while history loads'; input.focus(); input.setSelectionRange(2, 9);
  await b.clock.tick();
  assert.equal(b.run('ASSIST.session.loaded'), false); assert.equal(b.run('ASSIST.session.syncState'), 'error');
  assert.equal(b.input, input, 'a load error cannot recreate the composer');
  assert.equal(input.value, 'typing while history loads'); assert.equal(b.document.activeElement, input);
  await b.clock.tick(3000);
  assert.equal(b.run('ASSIST.session.loaded'), true); assert.equal(b.history()[0].content, 'loaded after retry');
  assert.equal(b.input, input, 'automatic recovery cannot recreate the composer'); assert.equal(page.writes, writes);
  assert.equal(input.value, 'typing while history loads'); assert.equal(b.document.activeElement, input);
  assert.deepEqual([input.selectionStart, input.selectionEnd], [2, 9]);
  assert.match(page.children.get('asLog').innerHTML, /loaded after retry/);
});

test('loading the real model catalog updates provider availability and notices without replacing a focused composer', async t => {
  const modelCatalog = {
    anthropic: { available: false, models: [] },
    openai: { available: true, default: 'openai-default', models: [{ id: 'openai-default', name: 'Available OpenAI model' }] },
  };
  const b = await browser(new SharedMapDB(), { modelCatalog }).ready(); t.after(() => b.stop());
  b.run('ASSIST.models = null; renderAssistant(document.page, {full:true});');
  const input = b.input, page = b.node, rootWrites = page.writes;
  input.value = 'draft entered before model discovery'; input.focus(); input.setSelectionRange(6, 13);
  assert.match(page.innerHTML, /Loading model catalog/);
  assert.doesNotMatch(page.innerHTML, /No API credentials found|Claude \(no key\)|OpenAI \(no key\)/,
    'an unknown catalog is a loading state, not evidence of missing credentials');
  // Only the external HTTP boundary is faked: execute the real selection and incremental render paths.
  await b.run('loadAssistantModels().then(() => assistRenderRemote())');
  assert.ok(b.requests.some(request => request.url === '/api/assistant/models'));
  assert.equal(b.run('ASSIST.provider'), 'openai'); assert.equal(b.run('ASSIST.model'), 'openai-default');
  assert.equal(page.children.get('asModelNotice').innerHTML, '', 'successful discovery clears the loading or missing-key notice');
  const options = page.children.get('asProvider').innerHTML;
  const openai = options.match(/<option\b[^>]*value="openai"[^>]*>[^<]*<\/option>/)?.[0];
  const anthropic = options.match(/<option\b[^>]*value="anthropic"[^>]*>[^<]*<\/option>/)?.[0];
  assert.ok(openai); assert.match(openai, /selected/); assert.doesNotMatch(openai, /disabled|no key/);
  assert.ok(anthropic); assert.match(anthropic, /disabled/);
  assert.equal(page.children.get('asProvider').disabled, false);
  assert.match(page.children.get('asModel').innerHTML, /value="openai-default" selected>Available OpenAI model/);
  assert.equal(b.input, input); assert.equal(page.writes, rootWrites);
  assert.equal(input.value, 'draft entered before model discovery'); assert.equal(b.document.activeElement, input);
  assert.deepEqual([input.selectionStart, input.selectionEnd], [6, 13]);
});
