import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../tinystudio/ui/assistant/conversation-sync.js', import.meta.url), 'utf8');
const { ConversationSync } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
class Clock {
  time = 0; next = 1; tasks = new Map();
  now() { return this.time; }
  setTimeout(callback, delay) { const id = this.next++; this.tasks.set(id, { at: this.time + delay, callback }); return id; }
  clearTimeout(id) { this.tasks.delete(id); }
  async tick(duration = 0) {
    const end = this.time + duration;
    await flush();
    for (;;) {
      const task = [...this.tasks.entries()].filter(([, value]) => value.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!task) break;
      this.time = task[1].at; this.tasks.delete(task[0]); task[1].callback(); await flush();
    }
    this.time = end; await flush();
  }
}
class Events {
  handlers = new Map();
  addEventListener(type, fn) { if (!this.handlers.has(type)) this.handlers.set(type, new Set()); this.handlers.get(type).add(fn); }
  removeEventListener(type, fn) { this.handlers.get(type)?.delete(fn); }
  emit(type) { for (const fn of this.handlers.get(type) || []) fn(); }
  count() { return [...this.handlers.values()].reduce((sum, set) => sum + set.size, 0); }
}
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
function fixture(check = async () => 1, options = {}) {
  const clock = new Clock(), window = new Events(), document = new Events();
  document.visibilityState = 'visible';
  const navigator = { onLine: true }, statuses = [];
  const channels = [];
  class Channel {
    messages = []; closed = false;
    constructor(name) { this.name = name; channels.push(this); }
    postMessage(data) { this.messages.push(data); }
    close() { this.closed = true; }
    emit(data) { this.onmessage?.({ data }); }
  }
  const sync = new ConversationSync({ scope: 'user/project', check, onStatus: status => statuses.push(status),
    window, document, navigator, BroadcastChannel: Channel, scheduler: clock, ...options });
  return { sync, clock, window, document, navigator, statuses, channels };
}

test('polls every 3s visible, 15s hidden, catches up immediately on wake', async () => {
  let checks = 0;
  const f = fixture(async () => ++checks);
  f.sync.start(); f.sync.start(); await f.clock.tick(); assert.equal(checks, 1);
  await f.clock.tick(2999); assert.equal(checks, 1);
  await f.clock.tick(1); assert.equal(checks, 2);
  f.document.visibilityState = 'hidden'; f.document.emit('visibilitychange');
  await f.clock.tick(14999); assert.equal(checks, 2);
  await f.clock.tick(1); assert.equal(checks, 3);
  f.document.visibilityState = 'visible'; f.document.emit('visibilitychange'); await f.clock.tick();
  assert.equal(checks, 4); f.sync.stop(); assert.equal(f.clock.tasks.size, 0);
});

test('focus/pageshow bursts coalesce and no overlapping requests, at most one trailing check', async () => {
  const requests = []; let active = 0, maximum = 0;
  const f = fixture(() => { active++; maximum = Math.max(maximum, active); const d = deferred(); requests.push(d); return d.promise.finally(() => active--); });
  f.sync.start(); await f.clock.tick();
  for (let i = 0; i < 20; i++) { f.window.emit('focus'); f.window.emit('pageshow'); f.sync.requestCheck(); }
  await f.clock.tick(500); assert.equal(requests.length, 1);
  requests[0].resolve(1); await f.clock.tick(); assert.equal(requests.length, 2);
  requests[1].resolve(2); await f.clock.tick(); assert.equal(requests.length, 2);
  assert.equal(maximum, 1); f.sync.stop();
});

test('offline suppresses polling and wake requests until online', async () => {
  let checks = 0; const f = fixture(async () => ++checks);
  f.navigator.onLine = false; f.sync.start();
  f.window.emit('focus'); f.window.emit('pageshow'); await f.clock.tick(60000);
  assert.equal(checks, 0); assert.equal(f.statuses.at(-1).state, 'offline');
  f.navigator.onLine = true; f.window.emit('online'); await f.clock.tick(); assert.equal(checks, 1);
  f.navigator.onLine = false; f.window.emit('offline'); await f.clock.tick(60000); assert.equal(checks, 1); f.sync.stop();
});

test('temporary and authorization errors back off to 30s, visibility preserves backoff, recovery clears error', async () => {
  const times = []; let recover = false;
  const denied = Object.assign(new Error('Forbidden'), { status: 403 });
  const f = fixture(async () => { times.push(f.clock.now()); if (!recover) throw denied; return 4; });
  f.sync.start(); await f.clock.tick();
  assert.equal(f.statuses.at(-1).error, denied);
  await f.clock.tick(3000); await f.clock.tick(6000); await f.clock.tick(12000); await f.clock.tick(24000);
  assert.deepEqual(times, [0, 3000, 9000, 21000, 45000]);
  f.document.visibilityState = 'hidden'; f.document.emit('visibilitychange');
  f.document.visibilityState = 'visible'; f.document.emit('visibilitychange'); f.window.emit('focus');
  await f.clock.tick(29999); assert.equal(times.length, 5);
  recover = true; await f.clock.tick(1); assert.equal(times.at(-1), 75000); assert.equal(f.statuses.at(-1).state, 'idle');
  await f.clock.tick(3000); assert.equal(times.at(-1), 78000); f.sync.stop();
});

test('timeout aborts a hanging transport and retries; late results cannot reset state', async () => {
  let signal; const slow = deferred(); let checks = 0;
  const f = fixture(({ signal: current }) => { checks++; signal = current; return checks === 1 ? slow.promise : Promise.resolve(2); });
  f.sync.start(); await f.clock.tick(); const firstSignal = signal;
  await f.clock.tick(10000); assert.equal(firstSignal.aborted, true);
  assert.equal(f.statuses.at(-1).error.name, 'TimeoutError');
  await f.clock.tick(3000); assert.equal(checks, 2); assert.equal(f.statuses.at(-1).state, 'idle');
  const statusCount = f.statuses.length; slow.resolve(99); await flush(); assert.equal(f.statuses.length, statusCount); f.sync.stop();
});

test('stop removes lifecycle resources, aborts pending work and ignores late old-scope results after restart', async () => {
  const old = deferred(); const signals = []; let checks = 0;
  const f = fixture(({ signal }) => { signals.push(signal); return ++checks === 1 ? old.promise : Promise.resolve(2); });
  f.sync.start(); await f.clock.tick(); const oldChannel = f.channels[0];
  f.sync.stop(); assert.equal(signals[0].aborted, true); assert.equal(oldChannel.closed, true);
  assert.equal(f.window.count() + f.document.count(), 0); assert.equal(f.clock.tasks.size, 0);
  const statuses = f.statuses.length;
  f.window.emit('focus'); await f.clock.tick(30000); assert.equal(checks, 1);
  assert.equal(f.statuses.length, statuses);
  f.sync.start(); await f.clock.tick(); assert.equal(checks, 2);
  const restartedStatuses = f.statuses.length;
  old.resolve(99); await flush(); assert.equal(f.statuses.length, restartedStatuses);
  await f.clock.tick(3000); assert.equal(checks, 3); f.sync.stop();
});

test('401 errors remain available to the caller and cannot stop recovery scheduling', async () => {
  let checks = 0;
  const denied = Object.assign(new Error('Sign in required'), { status: 401 });
  const f = fixture(async () => { if (++checks === 1) throw denied; return 2; });
  f.sync.start(); await f.clock.tick(); assert.equal(f.statuses.at(-1).error.status, 401);
  await f.clock.tick(3000); assert.equal(checks, 2); assert.equal(f.statuses.at(-1).state, 'idle'); f.sync.stop();
});

test('channel invalidations filter scope and revisions; only actual saves broadcast metadata', async () => {
  let checks = 0; const f = fixture(async () => { checks++; return 3; });
  f.sync.start(); await f.clock.tick(); const channel = f.channels[0];
  assert.deepEqual(channel.messages, []);
  channel.emit({ scope: 'someone/else', revision: 4 }); channel.emit({ scope: 'user/project', revision: 3 });
  channel.emit({ scope: 'user/project', revision: 2 }); await f.clock.tick(); assert.equal(checks, 1);
  channel.emit({ scope: 'user/project', revision: 4 }); channel.emit({ scope: 'user/project', revision: 4 });
  await f.clock.tick(); assert.equal(checks, 2); assert.deepEqual(channel.messages, []);
  f.sync.notifySaved(5); f.sync.notifySaved(5); assert.deepEqual(channel.messages, [{ scope: 'user/project', revision: 5 }]);
  f.sync.stop(); channel.emit({ scope: 'user/project', revision: 6 }); await f.clock.tick(); assert.equal(checks, 2);
});

test('polling works when BroadcastChannel is unavailable or constructor throws', async () => {
  for (const BroadcastChannel of [null, class { constructor() { throw new Error('blocked'); } }]) {
    let checks = 0; const f = fixture(async () => ++checks, { BroadcastChannel });
    f.sync.start(); await f.clock.tick(3000); assert.equal(checks, 2); f.sync.notifySaved(3); f.sync.stop();
  }
});

test('an intentionally violating scope filter is detected by the same observable assertion', async () => {
  const faulty = source.replace('data?.scope === this.#scope && this.#remember(data.revision)', 'this.#remember(data.revision)');
  assert.notEqual(faulty, source);
  const { ConversationSync: FaultySync } = await import(`data:text/javascript;base64,${Buffer.from(faulty).toString('base64')}`);
  let checks = 0, channel; const clock = new Clock();
  const sync = new FaultySync({ scope: 'user/project', check: async () => ++checks, scheduler: clock,
    window: new Events(), document: { visibilityState: 'visible' }, navigator: { onLine: true },
    BroadcastChannel: class { constructor() { channel = this; } close() {} } });
  sync.start(); await clock.tick(); channel.onmessage({ data: { scope: 'wrong/project', revision: 99 } }); await clock.tick();
  assert.throws(() => assert.equal(checks, 1), assert.AssertionError); sync.stop();
});
