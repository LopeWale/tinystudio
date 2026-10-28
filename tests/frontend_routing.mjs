// URL compatibility and lifecycle contracts for the shipped native modules.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const load = async (path) => import(`data:text/javascript;base64,${Buffer.from(await readFile(new URL(path, import.meta.url), 'utf8')).toString('base64')}`);
const { HashRouter } = await load('../tinystudio/ui/core/router.js');
const { createRoutes } = await load('../tinystudio/ui/routes.js');

// Independently enumerated public URLs retained from the pre-refactor interface.
const contracts = [
  ['#/', 'home', 'go', ['#/datasets']],
  ['#/datasets', 'datasets', 'pageDatasets', []],
  ['#/repositories', 'repositories', 'pageRepositories', []],
  ['#/datasets/review%20set%2Fone', 'dataset', 'pageDataset', ['review set/one']],
  ['#/finetuning', 'finetuning', 'pageFinetuning', []],
  ['#/finetuning/job%20%26%20one', 'job', 'pageJob', ['job & one']],
  ['#/evaluations', 'evaluations', 'pageEvaluations', []],
  ['#/evaluations/eval%2Fone', 'evaluation', 'pageEval', ['eval/one']],
  ['#/metrics', 'metrics', 'pageMetrics', []],
  ['#/playground', 'playground', 'pagePlayground', [null]],
  ['#/models', 'models', 'pageModels', []],
  ['#/traces', 'traces', 'pageTraces', []],
  ['#/stats', 'stats', 'pageStats', []],
  ['#/api-keys', 'keys', 'pageKeys', []],
  ['#/assistant', 'assistant', 'pageAssistant', []],
  ['#/settings', 'settings', 'pageSettings', []],
  ['#/users', 'users', 'pageUsers', []],
  ['#/projects', 'projects', 'pageProjects', []],
  ['#/interp', 'interpretability', 'pageInterp', []],
  ['#/interp/c/capture%2Fone?layer=12&neuron=4&tag=a+b&tag=c%2Fd', 'capture', 'pageCapture', ['capture/one', [['layer', '12'], ['neuron', '4'], ['tag', 'a b'], ['tag', 'c/d']]]],
  ['#/interp/d/diff%20one?left=a%2Fb&right=x%26y', 'diff', 'pageDiff', ['diff one', [['left', 'a/b'], ['right', 'x&y']]]],
];
const pageNames = ['pageDatasets', 'pageRepositories', 'pageDataset', 'pageFinetuning', 'pageJob', 'pageEvaluations', 'pageEval', 'pageMetrics', 'pagePlayground', 'pageModels', 'pageTraces', 'pageStats', 'pageKeys', 'pageAssistant', 'pageSettings', 'pageUsers', 'pageProjects', 'pageInterp', 'pageCapture', 'pageDiff'];
function inventory() {
  const calls = [];
  const pages = Object.fromEntries(pageNames.map((name) => [name, (...args) => calls.push([name, args.map((arg) => arg instanceof URLSearchParams ? [...arg] : arg)])]));
  return { calls, routes: createRoutes(pages, (...args) => calls.push(['go', args])) };
}
function harness(routes, hash = '#/datasets') {
  const location = { hash }, listeners = new Set(), errors = [], captures = [];
  let current;
  let additions = 0, removals = 0;
  const window = {
    addEventListener(type, fn) { assert.equal(type, 'hashchange'); additions++; listeners.add(fn); },
    removeEventListener(type, fn) { assert.equal(type, 'hashchange'); removals++; listeners.delete(fn); },
  };
  const router = new HashRouter({ routes, location, window,
    beforeRender: (route) => { current = { route }; captures.push(current); return current; },
    isCurrent: (capture) => current === capture,
    onError: (error) => errors.push(error),
  });
  return { router, location, errors, captures, listeners, counts: () => [additions, removals], dispatch: async () => { await Promise.all([...listeners].map((fn) => fn())); } };
}

test('all 21 public route families retain distinct dispatch and decoded arguments', async (t) => {
  assert.equal(contracts.length, 21);
  for (const [hash, routeName, pageName, args] of contracts) {
    await t.test(hash, async () => {
      const { routes, calls } = inventory();
      const matches = routes.filter(({ pattern }) => pattern.test(hash));
      assert.equal(matches.length, 1, 'a URL must dispatch exactly one page');
      assert.equal(matches[0].name, routeName);
      const { router, errors } = harness(routes, hash);
      await router.render();
      assert.deepEqual(errors, []);
      assert.deepEqual(calls, [[pageName, args]]);
    });
  }
});

test('root, playground model and optional interpretability query contracts', async () => {
  const { routes, calls } = inventory();
  const { router, location } = harness(routes);
  const cases = [
    ['', 'go', ['#/datasets']], ['#', 'go', ['#/datasets']],
    ['#/playground?model=vendor%2Fmodel%20one', 'pagePlayground', ['vendor/model one']],
    ['#/interp/c/only-name', 'pageCapture', ['only-name', []]],
    ['#/interp/d/only-name?', 'pageDiff', ['only-name', []]],
  ];
  for (const [hash, page, args] of cases) { calls.length = 0; location.hash = hash; await router.render(); assert.deepEqual(calls, [[page, args]]); }
});

test('unknown and malformed URLs navigate to datasets without invoking a page', async () => {
  const { routes, calls } = inventory();
  const { router, location } = harness(routes);
  for (const hash of ['#/unknown', '#/datasets/one/extra', '#/dataset', '#/models-extra', '#/interp/c/', '#/interp/d/a/b', '#/playground?model=']) {
    location.hash = hash; await router.render();
    assert.equal(location.hash, '#/datasets', hash);
    assert.deepEqual(calls, [], 'fallback waits for hash navigation before rendering a page');
  }
});

test('hash listeners start once, detach, and can restart without duplicate rendering', async () => {
  let rendered = 0;
  const h = harness([{ pattern: /^#\/datasets$/, run: () => { rendered++; } }]);
  h.router.start(); h.router.start();
  assert.deepEqual(h.counts(), [1, 0]);
  await h.dispatch(); assert.equal(rendered, 1);
  h.router.destroy(); h.router.destroy();
  assert.deepEqual(h.counts(), [1, 1]);
  await h.dispatch(); assert.equal(rendered, 1, 'destroyed router must not react to navigation');
  h.router.start(); await h.dispatch(); assert.equal(rendered, 2);
  h.router.destroy(); assert.equal(h.listeners.size, 0);
});

test('normal render captures before the page and reports current failures once', async () => {
  const failure = new Error('page failed');
  const h = harness([{ pattern: /^#\/datasets$/, run: () => { assert.equal(h.captures.at(-1).route, '#/datasets'); throw failure; } }]);
  await h.router.render();
  assert.deepEqual(h.errors, [failure]);
  assert.equal(h.captures.length, 1);
  h.router.go('#/models'); assert.equal(h.location.hash, '#/models');
});

test('StaleRenderError never replaces the current page with an error', async () => {
  const stale = Object.assign(new Error('stale response'), { name: 'StaleRenderError' });
  const h = harness([{ pattern: /^#\/datasets$/, run: () => { throw stale; } }]);
  await h.router.render(); assert.deepEqual(h.errors, []);
});

test('an older delayed generic failure cannot clobber a successfully rendered destination', async () => {
  let rejectOlder;
  const events = [];
  const h = harness([
    { pattern: /^#\/datasets$/, run: () => new Promise((_, reject) => { rejectOlder = reject; }) },
    { pattern: /^#\/models$/, run: () => { events.push('models rendered'); } },
  ]);
  const older = h.router.render();
  h.router.go('#/models'); await h.router.render();
  rejectOlder(new Error('old request failed')); await older;
  assert.deepEqual(events, ['models rendered']);
  assert.deepEqual(h.errors, []);
  assert.equal(h.captures.at(-1).route, '#/models');
});

test('loading follows the active render and a late older route cannot hide the current indicator', async () => {
  const location = { hash: '#/first' }, transitions = []; let current, firstDone, secondDone;
  const router = new HashRouter({ location, window: {},
    routes: [
      { pattern: /^#\/first$/, run: () => new Promise(resolve => { firstDone = resolve; }) },
      { pattern: /^#\/second$/, run: () => new Promise(resolve => { secondDone = resolve; }) },
    ],
    beforeRender: () => (current = {}), isCurrent: view => view === current,
    onError: error => { throw error; }, onLoading: value => transitions.push(value),
  });
  const first = router.render(); assert.deepEqual(transitions, [true]);
  location.hash = '#/second'; const second = router.render();
  firstDone(); await first; assert.deepEqual(transitions, [true, true]);
  secondDone(); await second; assert.deepEqual(transitions, [true, true, false]);
});
