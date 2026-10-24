import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const source = await readFile(new URL('../tinystudio/ui/core/playground.js', import.meta.url), 'utf8');
const { PlaygroundSessions } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const encoder = new TextEncoder();
const sse = (...events) => new Response(events.map(event => `data: ${JSON.stringify(event)}\n\n`).join(''));
function fixture() {
  let scope = { user: 'account-one', project: 'one/alpha' }, handler = async () => sse({ delta: 'answer' }, { done: true, seconds: .5 });
  const calls = [], updates = [], latency = [];
  const sessions = new PlaygroundSessions({ getScope: () => scope, fetch: async (url, options) => { calls.push({ url, options }); return handler(url, options); } });
  let view = {};
  const attach = () => { view = {}; sessions.attach(view); return view; };
  attach(); sessions.configure({ model: 'model-one' });
  return { sessions, calls, updates, latency, attach, switchScope(value) { scope = value; sessions.syncScope(); attach(); }, setNetwork(fn) { handler = fn; }, send(text) { return sessions.send(text, { view, onUpdate: () => updates.push(sessions.snapshot().history), onLatency: seconds => latency.push(seconds) }); } };
}
function blockedStream() {
  let release, cancelled = 0;
  const reader = { read: () => new Promise(resolve => { release = resolve; }), cancel: async () => { cancelled++; release?.({ done: true }); }, releaseLock() {} };
  return { response: { ok: true, body: { getReader: () => reader } }, emit(event) { release({ value: encoder.encode(`data: ${JSON.stringify(event)}\n\n`), done: false }); }, finish() { release({ done: true }); }, get cancelled() { return cancelled; } };
}
const flush = () => new Promise(setImmediate);

test('request captures encoded project endpoint, isolated history and RAG configuration', async () => {
  const f = fixture();
  f.sessions.configure({ system: 'system instruction', temperature: .4, repositories: ['manual'], repoLimit: 2, repoThreshold: .5 });
  assert.equal(await f.send('question'), true);
  assert.equal(f.calls[0].url, '/api/p/one%2Falpha/playground/chat');
  assert.deepEqual(JSON.parse(f.calls[0].options.body), { model: 'model-one', messages: [{ role: 'system', content: 'system instruction' }, { role: 'user', content: 'question' }], temperature: .4, max_tokens: 512, repositories: { ids: ['manual'], limit: 2, similarity_threshold: .5 } });
  assert.deepEqual(f.sessions.snapshot().history, [{ role: 'user', content: 'question' }, { role: 'assistant', content: 'answer' }]);
  assert.deepEqual(f.latency, [.5]);
  const exposed = f.sessions.snapshot(); exposed.history[0].content = 'mutation'; exposed.repositories.push('other');
  assert.equal(f.sessions.snapshot().history[0].content, 'question'); assert.deepEqual(f.sessions.snapshot().repositories, ['manual']);
  f.sessions.destroy();
});

test('project histories and settings never enter another project request', async () => {
  const f = fixture(); await f.send('project one secret');
  f.switchScope({ user: 'account-one', project: 'two' });
  assert.deepEqual(f.sessions.snapshot().history, []); assert.equal(f.sessions.snapshot().model, null);
  f.sessions.configure({ model: 'model-two' }); await f.send('project two question');
  assert.equal(f.calls[1].url, '/api/p/two/playground/chat');
  assert.deepEqual(JSON.parse(f.calls[1].options.body).messages, [{ role: 'user', content: 'project two question' }]);
  f.switchScope({ user: 'account-one', project: 'one/alpha' });
  assert.equal(f.sessions.snapshot().model, 'model-one'); assert.equal(f.sessions.snapshot().history[0].content, 'project one secret');
  f.sessions.destroy();
});

test('repeated Send or Enter during streaming cannot start overlapping turns', async () => {
  const f = fixture(), stream = blockedStream(); f.setNetwork(async () => stream.response);
  const pending = f.send('first'); await flush();
  assert.equal(f.sessions.busy, true); assert.equal(await f.send('second'), false); assert.equal(f.calls.length, 1);
  stream.emit({ delta: 'partial' }); await flush(); assert.equal(f.sessions.snapshot().history[1].content, 'partial');
  stream.finish(); assert.equal(await pending, true); assert.equal(f.sessions.busy, false);
  f.sessions.destroy();
});

test('switching projects during a stream aborts, drops its incomplete turn and ignores late bytes', async () => {
  const f = fixture(), stream = blockedStream(); f.setNetwork(async () => stream.response);
  const pending = f.send('private prompt'); await flush();
  stream.emit({ delta: 'partial' }); await flush();
  const before = f.updates.length;
  f.switchScope({ user: 'account-one', project: 'two' });
  assert.equal(f.calls[0].options.signal.aborted, true); assert.equal(stream.cancelled, 1);
  assert.equal(await pending, false); assert.equal(f.updates.length, before); assert.deepEqual(f.sessions.snapshot().history, []);
  f.switchScope({ user: 'account-one', project: 'one/alpha' }); assert.deepEqual(f.sessions.snapshot().history, []);
  f.sessions.destroy();
});

test('navigation and reset cancel active work without detached view callbacks', async () => {
  for (const action of ['detach', 'reset', 'destroy']) {
    const f = fixture(), stream = blockedStream(); f.setNetwork(async () => stream.response);
    const pending = f.send('pending'); await flush(); const before = f.updates.length;
    f.sessions[action](); assert.equal(f.calls[0].options.signal.aborted, true); assert.equal(await pending, false);
    assert.equal(f.sessions.busy, false); assert.equal(f.updates.length, before);
    if (action !== 'destroy') assert.deepEqual(f.sessions.snapshot().history, []);
    f.sessions.destroy();
  }
});

test('failed responses remove only their own turn and leave the next request clean', async () => {
  const f = fixture(); await f.send('completed');
  f.setNetwork(async () => new Response(JSON.stringify({ detail: 'not ready' }), { status: 503 }));
  await assert.rejects(f.send('failed'), /not ready/); assert.equal(f.sessions.busy, false);
  assert.equal(f.sessions.snapshot().history.length, 2);
  f.setNetwork(async () => sse({ error: 'engine failure' }));
  await assert.rejects(f.send('failed in stream'), /engine failure/); assert.equal(f.sessions.snapshot().history.length, 2);
  f.setNetwork(async () => sse({ delta: 'recovered' })); await f.send('retry');
  assert.deepEqual(JSON.parse(f.calls.at(-1).options.body).messages.map(message => message.content), ['completed', 'answer', 'retry']);
  f.sessions.destroy();
});

test('account changes discard prior account sessions and cancellation before headers rejects late success', async () => {
  const f = fixture(); await f.send('old account'); let release;
  f.setNetwork(() => new Promise(resolve => { release = resolve; }));
  const pending = f.send('slow');
  f.switchScope({ user: 'account-two', project: 'one/alpha' }); release(sse({ delta: 'late' }));
  assert.equal(await pending, false); assert.deepEqual(f.sessions.snapshot().history, []);
  f.switchScope({ user: 'account-one', project: 'one/alpha' }); assert.deepEqual(f.sessions.snapshot().history, []);
  f.sessions.destroy();
});
