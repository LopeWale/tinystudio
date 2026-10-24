// Offline browser-state contract checks. Run: node tests/assistant_frontend.cjs
const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const storage = new Map();
const database = new Map();
const requests = [];
const fetch = async (path, options = {}) => {
  requests.push({path, options});
  const key = `${run("state.user.id")}:${path}`;
  const current = database.get(key) || {revision:0,history:[],provider:"",model:"",updated:null};
  let result = current, status = 200;
  if (options.method === "PUT") {
    const body = JSON.parse(options.body);
    if (body.revision !== current.revision) { status = 409; result = {detail:"Conversation changed on another device"}; }
    else { result = {...body,revision:current.revision+1,updated:"2026-10-23T12:00:00Z"}; database.set(key,result); }
  }
  return {ok:status === 200,status,statusText:"error",json:async()=>structuredClone(result)};
};
const document = { addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; } };
// This suite isolates existing context/persistence behavior. The real scheduler
// and its integration run in conversation_sync.mjs and assistant_sync.cjs.
class ConversationSync { start() {} stop() {} notifySaved() {} requestCheck() {} }
const context = vm.createContext({ ConversationSync, document, window: { addEventListener() {} }, location: { hash: '#/evaluations/a' }, localStorage: { getItem: (k) => storage.get(k), setItem: (k, v) => storage.set(k, v), removeItem: (k) => storage.delete(k) }, fetch, FormData, AbortController, TextEncoder, console, setTimeout, clearTimeout });
const source = readFileSync('tinystudio/ui/studio.js', 'utf8').split('// ------------------------------------------------------------------ boot')[0].replace(/^import .*;\n/gm, '');
vm.runInContext(source, context);
const run = (s) => vm.runInContext(s, context);
(async () => {
await run(`state.user = {id: 1}; state.project = 'first'; syncAssistantScope(); assistLoad();`);
await run(`ASSIST.history.push({role:'user',content:'first private history'}); assistSave();`);
run(`ASSIST.history.push({role:'assistant',content:'unsaved final part'}); ASSIST.session.dirty = true; ASSIST.destructive = true; ASSIST.turn = {controller: new AbortController()}; globalThis.oldSignal = ASSIST.turn.controller.signal; state.project = 'second'; syncAssistantScope();`);
assert.equal(run('oldSignal.aborted'), true, 'switching projects must cancel the previous turn');
assert.equal(run('ASSIST.history.length'), 0, 'new project must not inherit conversation');
assert.equal(run('ASSIST.destructive'), false, 'actions require opting in after a scope switch');
await run('assistLoad()');
await run(`ASSIST.history.push({role:'user',content:'second'}); assistSave();`);
await run(`state.project = 'first'; syncAssistantScope(); assistLoad();`);
assert.equal(run('ASSIST.history[1].content'), 'unsaved final part', 'scope switch saves the latest in-flight history');
assert.equal(run('ASSIST.history[0].content'), 'first private history', 'returning to a project restores its own history');
await run('state.user = {id: 2}; syncAssistantScope(); assistLoad();');
assert.equal(run('ASSIST.history.length'), 0, 'different users must not share project conversations');
run(`state.pageContext = { route: location.hash, project: state.project, api: [] }; globalThis.firstCapture = state.pageContext; recordApi('/api/evals/a', {rows:[{score:0.25,api_key:'private',nested:{authorization:'Bearer secret'}}]}, firstCapture);`);
assert.equal(run('state.pageContext.api[0].data.rows[0].score'), 0.25, 'API context preserves structured numbers');
assert.equal(run('state.pageContext.api[0].data.rows[0].api_key'), '[redacted]');
assert.equal(run('state.pageContext.api[0].data.rows[0].nested.authorization'), '[redacted]');
run(`location.hash = '#/datasets'; state.pageContext = { route: location.hash, project: state.project, api: [] }; recordApi('/api/evals/a', {rows:['late response']}, firstCapture); recordApi('/api/settings', {password:'private'});`);
assert.equal(run('state.pageContext.api.length'), 0, 'late previous-route responses and settings must never be captured');
run(`recordApi('/api/datasets', [{name:'visible'}]);`);
assert.equal(run('state.pageContext.api[0].data[0].name'), 'visible');

run(`ASSIST.models = {anthropic:{available:true,models:[{id:'claude-a'}],default:'claude-a'},openai:{available:true,models:[{id:'openai-one'}],default:'openai-one'}}; ASSIST.provider = 'openai'; ASSIST.model = null;`);
  await run("loadAssistantModels()");
  assert.equal(run("ASSIST.model"), "openai-one", "cached provider selection must normalize the model actually sent");
  run("location.hash = '#/api-keys'");
  assert.match(run("snapshotPage()"), /omitted/);
  assert.match(run("pageText()"), /omitted/);
  run("location.hash = '#/datasets'; state.domContext = state.pageContext;");
  run("globalThis.boundElement = {parentElement:null}; bindContext(boundElement, 'Specific result', {score:0.125,prediction:'actual response'});");
  assert.equal(run("pickContext(boundElement).data.score"), 0.125);
  assert.equal(run("pickContext(boundElement).source"), "rendered-data");
  for (const action of ['click', 'fill', 'select']) {
    await assert.rejects(run(`uiPerform('${action}', {ref:'anything'})`), /require the actions/, `${action} must be denied before touching an element`);
  }
  // A retained source binding must never be relabelled as a different project's data.
  run("globalThis.oldBound = {parentElement:null}; bindContext(oldBound, 'Project one result', {score:0.99}); globalThis.oldView = state.pageContext; state.project = 'switched'; state.pageContext = {route:location.hash,project:state.project,api:[]};");
  assert.equal(run('pickContext(oldBound)'), null);
  assert.throws(() => run('assertRender(oldView)'), /stale response/);
  // A real delayed evaluation response must be rejected before it reaches layout.
  run(`globalThis.keepOverview = ensureOverview; globalThis.keepRawFetch = rawFetch; globalThis.keepLayout = layout;
    ensureOverview = async () => ({}); globalThis.layoutCalls = 0; layout = () => { layoutCalls++; throw new Error('stale layout called'); };
    rawFetch = () => new Promise((resolve) => { globalThis.releaseEvaluation = () => resolve({ok:true,headers:{get:()=> 'application/json'},json:async()=>({summary:{metrics:[],models:[]},rows:[]})}); });`);
  const delayed = run("pageEval('delayed-project-a')");
  await new Promise(setImmediate);
  run("state.project = 'next'; state.pageContext = {route:location.hash,project:state.project,api:[]}; releaseEvaluation();");
  await assert.rejects(delayed, {name:'StaleRenderError'});
  assert.equal(run('layoutCalls'), 0);
  run('ensureOverview = keepOverview; rawFetch = keepRawFetch; layout = keepLayout;');
  run('state.domContext = state.pageContext;');
  // Credentials in a retained dialog remain excluded after leaving the key route.
  run(`globalThis.secretNode = {closest: (selector) => selector.includes('data-assistant-sensitive') ? {} : null};
    window.getSelection = () => ({anchorNode:{parentElement:secretNode},focusNode:{parentElement:secretNode},toString:()=> 'synthetic-secret'});`);
  assert.equal(run('safeSelection()'), '');
  assert.equal(run('pickContext(secretNode)'), null);
  run(`window.getSelection = () => ({rangeCount:1,getRangeAt:()=>({intersectsNode:()=>true}),toString:()=> 'all body including synthetic-secret'});
    document.querySelectorAll = (selector) => selector === '[data-assistant-sensitive]' ? [secretNode] : [];`);
  assert.equal(run('safeSelection()'), '', 'selection spanning a sensitive dialog must be excluded even if endpoints are outside');
  run('window.getSelection = () => null; document.querySelectorAll = () => [];');
  // Full UTF-8 outgoing requests remain below the endpoint limit for realistic large datasets.
  run(`globalThis.bigContext = {route:location.hash,project:state.project,text:'visible page',selected_data:{label:'chosen row',source:'rendered-data',data:{id:7,prediction:'selected answer'}},api:[{path:'/api/dataset/large',data:{rows:Array.from({length:10000},(_,id)=>({id,text:'界'.repeat(200)}))}}]};
    globalThis.outgoing = prepareChatBody({messages:Array.from({length:50},(_,i)=>({role:i%2?'assistant':'user',content:'界'.repeat(3000)})),context:bigContext},state.project);`);
  assert.ok(run('requestBytes(outgoing)') <= 240000);
  assert.equal(run('outgoing.context.selected_data.data.id'), 7, 'selected exact row takes priority over broad API records');
  assert.ok(run('outgoing.context.omissions.length') > 0);
  assert.match(run('JSON.stringify(outgoing.context.api)'), /omitt/);
  // Source HTML bindings preserve the complete structured record instead of visible cell text.
  run("globalThis.attribute = dataContext('Model row', {id:'model-z',parameters:1234567}); globalThis.modelRow = {dataset:{contextId:attribute.match(/ctx_\\d+/)[0]},parentElement:null};");
  assert.equal(run('pickContext(modelRow).data.parameters'), 1234567);
  // Visible conversation redraw must also happen when the global models cache is empty.
  run(`globalThis.keepRenderAssistant = renderAssistant; globalThis.visibleHistory = null; renderAssistant = () => { visibleHistory = ASSIST.history.map(m=>m.content).join('|'); };
    globalThis.drawer = {}; document.querySelector = (selector) => selector === '#assist .drawer' ? drawer : null;
    ASSIST.models = null; syncAssistantScope();`);
  await run('assistLoad()');
  await run(`ASSIST.history = [{role:'user',content:'saved visible conversation'}]; assistSave();`);
  await run(`globalThis.savedProject = state.project; state.project = 'temporary'; syncAssistantScope(); state.project = savedProject; syncAssistantScope(); assistLoad();`);
  assert.equal(run('visibleHistory'), 'saved visible conversation');
  run('renderAssistant = keepRenderAssistant; document.querySelector = () => null; state.pageContext = {route:location.hash,project:state.project,api:[]}; state.domContext=state.pageContext;');
  // Construct both dialogs: picker bindings must reference their own view's data.
  run(`
    document.createElement = () => ({innerHTML:'', querySelectorAll:() => [], querySelector:() => ({})});
    modal = ({body}) => { globalThis.dialogBody = body; return {el:{querySelector:() => ({})}, close() {}}; };
    ensureOverview = async () => { state.overview = {tree:{datasets:[{snapshots:[{name:'validation',validation_count:2}]}]}}; };
    api = async (path) => path === '/api/models' ? [{id:'base/example',kind:'base'}] : [{name:'accuracy',kind:'rubric'}];
  `);
  await run('newEvaluationDialog({})');
  assert.match(run('dialogBody.innerHTML'), /Selected metrics/, 'new evaluation must build with snapshot/model/metric data');
  run("evalDetails(7, 'model-a', ['model-a'], ['accuracy'], {'7|model-a|accuracy':{datapoint:7,model:'model-a',metric:'accuracy',score:0.75,prediction:'answer'}})");
  assert.equal(run('pickContext(dialogBody).data.rows[0].score'), 0.75, 'result detail modal must bind its actual evaluation rows');
  // During a delayed destination load, old DOM/modals/title/selection are never relabelled.
  run(`
    globalThis.oldContent = {innerText:'PRIVATE OLD PROJECT DATA',cloneNode:()=>({innerText:'PRIVATE OLD PROJECT DATA',querySelectorAll:()=>[]})};
    document.querySelector = (selector) => selector === '#app h1' ? {innerText:'Old project heading'} : selector === '#app .content' ? oldContent : null;
    document.querySelectorAll = (selector) => selector.includes('.modal-back') ? [{innerText:'PRIVATE OLD MODAL'}] : [];
    window.getSelection = () => ({toString:()=> 'PRIVATE OLD SELECTION'});
    state.domContext = state.pageContext; state.project = 'destination'; state.pageContext = {route:location.hash,project:state.project,api:[{path:'/api/new',data:{new:true}}]};
  `);
  assert.equal(run('captureContext().title'), 'Page loading');
  assert.match(run('captureContext().text'), /loading/);
  assert.equal(run('captureContext().api.length'), 0);
  assert.equal(run('captureContext().selection'), '');
  assert.equal(run('captureContext().selected_data'), null);
  assert.match(run('snapshotPage()'), /loading/);
  assert.equal(run('pickContext(oldContent)'), null);
  assert.doesNotMatch(run('JSON.stringify(captureContext())'), /PRIVATE|Old project heading/);
  run('document.querySelector = () => null; document.querySelectorAll = () => []; window.getSelection = () => null;');
  // Exercise the actual completed-job rendering branch, including its source binding.
  run(`
    globalThis.makeTree = () => { const children = new Map(); return {innerHTML:'',querySelectorAll:()=>[],querySelector:(selector)=>{ if (!children.has(selector)) children.set(selector,makeTree()); return children.get(selector); }}; };
    globalThis.jobTree = makeTree(); layout = () => jobTree;
    state.pageContext = {route:location.hash,project:state.project,api:[]}; state.domContext = state.pageContext;
    ensureOverview = async () => { state.overview = {catalog:[],tree:{datasets:[]}}; };
    api = async (path) => path.startsWith('/api/jobs/') ? {job:{name:'completed',snapshot:'validation'},experiments:[{number:1,state:'succeeded',config:{base_model:'base/example',training_type:'full'},loss:[],metrics:{eval_loss:0.5},checkpoints:[]}]} : {models:[]};
  `);
  await run("pageJob('completed')");
  assert.match(run("jobTree.querySelector('#left').innerHTML"), /Experiment 1/);
  assert.equal(run("[...state.pageContext.bindings.values()].find((binding)=>binding.label==='Experiment configuration').data.number"), 1);
  console.log('assistant frontend: scope isolation, cancellation, structured page context, secret redaction and action gate passed');
})().catch((error) => { console.error(error); process.exitCode = 1; });
