// Database conversation lifecycle contract. Run: node tests/assistant_persistence.cjs
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = readFileSync('tinystudio/ui/studio.js', 'utf8').split('// ------------------------------------------------------------------ boot')[0].replace(/^import .*;\n/gm, '');
// Persistence contracts isolate scheduling; automatic integration uses the real
// controller in assistant_sync.cjs.
class ConversationSync { start() {} stop() {} notifySaved() {} requestCheck() {} }
const clone = value => JSON.parse(JSON.stringify(value));
function browser(database = new Map(), storage = new Map()) {
  const requests = []; let fault = null, before = null;
  const elements = new Map();
  const node = () => ({innerHTML:'',value:'',disabled:false,hidden:false,querySelector(sel) { if (!elements.has(sel)) elements.set(sel,node()); return elements.get(sel); },querySelectorAll:()=>[]});
  const container = node();
  const document = {querySelector:()=>null,querySelectorAll:()=>[],addEventListener(){}};
  let context;
  const fetch = async (url,options={}) => {
    const user = vm.runInContext('state.user.id',context);
    const key = `${user}:${url}`;
    requests.push({url,method:options.method || 'GET',body:options.body && JSON.parse(options.body)});
    if (before) await before(url,options);
    if (fault) throw new Error(fault);
    const current = database.get(key) || {revision:0,history:[],provider:'',model:'',updated:null};
    let status = 200, result = current;
    if (options.method === 'PUT') {
      const body = JSON.parse(options.body);
      if (body.revision !== current.revision) { status = 409; result = {detail:'Conversation changed on another device'}; }
      else { result = {...body,revision:current.revision+1,updated:'2026-10-23T12:00:00Z'}; database.set(key,clone(result)); }
    }
    return {ok:status===200,status,statusText:'failed',json:async()=>clone(result)};
  };
  context = vm.createContext({ConversationSync,document,window:{addEventListener(){}},location:{hash:'#/datasets'},fetch,FormData,AbortController,TextEncoder,console,setTimeout,clearTimeout,
    localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},container});
  vm.runInContext(source,context);
  const run = code => vm.runInContext(code,context);
  run(`state.user={id:1}; state.project='alpha'; syncAssistantScope();`);
  return {run,requests,elements,storage,database, fault:value=>fault=value, before:value=>before=value};
}
(async () => {
  const db = new Map(), local = new Map();
  const first = browser(db,local); await first.run('assistLoad()');
  await first.run(`ASSIST.history = Array.from({length:52},(_,i)=>({role:i%2?'assistant':'user',content:'message '+i})); assistSave();`);
  assert.equal([...db.values()][0].history.length,52,'DB history must not silently truncate at the legacy 40-message limit');
  assert.ok([...local.values()].every(value=>!('history' in JSON.parse(value))),'new localStorage writes contain preferences only');
  const fresh = browser(db,new Map()); await fresh.run('assistLoad()');
  assert.equal(fresh.run('ASSIST.history[51].content'),'message 51','a fresh browser context restores the database conversation');
  // Concurrent devices start with the same revision; stale writes must preserve both newer DB and unsaved local reply.
  await first.run(`ASSIST.history.push({role:'assistant',content:'newer first browser'}); assistSave();`);
  await assert.rejects(fresh.run(`ASSIST.history.push({role:'assistant',content:'unsaved second browser'}); assistSave();`),/changed/);
  assert.equal(fresh.run('ASSIST.session.conflict'),true);
  assert.match(fresh.run('assistStatus()'),/Conflict/);
  assert.equal(fresh.run('ASSIST.history.at(-1).content'),'unsaved second browser');
  assert.equal([...db.values()][0].history.at(-1).content,'newer first browser');
  await fresh.run('assistLoad(ASSIST.session,{refresh:true})');
  assert.equal(fresh.run('ASSIST.history.at(-1).content'),'unsaved second browser','ordinary refresh never discards dirty replies');
  await fresh.run('assistLoad(ASSIST.session,{refresh:true,discard:true})');
  assert.equal(fresh.run('ASSIST.history.at(-1).content'),'newer first browser');
  // Failed writes must not claim saved or lose local edits; retry uses the same CAS revision.
  first.fault('offline');
  await assert.rejects(first.run(`ASSIST.history.push({role:'user',content:'offline request'}); assistSave();`),/offline/);
  assert.equal(first.run('ASSIST.session.dirty'),true); assert.match(first.run('assistStatus()'),/Not saved/);
  first.fault(null); await first.run('assistSave()'); assert.equal(first.run('assistStatus()'),'Saved to database');
  // Migration only happens into a genuinely empty revision, and clearing leaves a tombstone revision.
  const legacyKey = 'tinystudio.assistant.v2.1.alpha';
  const legacy = new Map([[legacyKey,JSON.stringify({history:[{role:'user',content:'legacy question'}],provider:'openai'})]]);
  const migration = browser(new Map(),legacy); await migration.run('assistLoad()');
  assert.equal(migration.run('ASSIST.history[0].content'),'legacy question'); assert.equal(legacy.has(legacyKey),false);
  await migration.run('ASSIST.history=[]; assistSave()');
  legacy.set(legacyKey,JSON.stringify({history:[{role:'user',content:'stale legacy copy'}]}));
  const afterClear = browser(migration.database,legacy); await afterClear.run('assistLoad()');
  assert.equal(afterClear.run('ASSIST.history.length'),0,'old local history cannot resurrect after clear');
  assert.equal(afterClear.requests.filter(r=>r.method==='PUT').length,0,'an existing DB revision never imports local history');
  const race = browser(new Map(),new Map([[legacyKey,JSON.stringify({history:[{role:'user',content:'losing import'}]})]]));
  let raced = false;
  race.before(async(url,options) => { if(options.method === 'PUT' && !raced) { raced=true; race.database.set(`1:${url}`,{revision:1,history:[{role:'user',content:'other device won'}],provider:'openai',model:'model',updated:'now'}); } });
  await race.run('assistLoad()');
  assert.equal(race.run('ASSIST.history[0].content'),'other device won','migration CAS conflict adopts the winning DB snapshot');
  // A delayed previous-project GET may complete, but must never change the new active conversation.
  let release; const gate = new Promise(resolve=>release=resolve);
  const delayed = browser(db); await delayed.run('assistLoad()');
  delayed.before(async(url,options)=>{ if(url.includes('/alpha/') && !options.method) await gate; });
  const pending = delayed.run('assistLoad(ASSIST.session,{refresh:true})');
  await new Promise(setImmediate);
  delayed.run(`state.project='beta'; syncAssistantScope();`); await delayed.run('assistLoad()');
  release(); await pending;
  assert.equal(delayed.run('ASSIST.scope.endsWith(".beta")'),true); assert.equal(delayed.run('ASSIST.history.length'),0);
  // Actual send handler must await a successful user-only save before invoking the model.
  const sending = browser(); await sending.run('assistLoad()');
  sending.run(`globalThis.streams=0; streamChat=async(body,onEvent)=>{ streams++; globalThis.sentBody=body; onEvent({type:'text',delta:'durable answer'}); onEvent({type:'conversation_saved',revision:2}); }; renderAssistant(container);`);
  sending.elements.get('#asInput').value = 'question';
  await sending.elements.get('#asSend').onclick();
  assert.equal(sending.run('streams'),1); assert.equal(sending.run('sentBody.conversation_revision'),1);
  assert.equal(sending.requests.find(r=>r.method==='PUT').body.history.length,1,'persist the user prompt before the stream, never an empty reply');
  assert.equal(sending.requests.filter(r=>r.method==='PUT').length,1,'server saved final reply must not receive a redundant PUT');
  assert.equal(sending.run('assistStatus()'),'Saved to database');
  const failingSend = browser(); await failingSend.run('assistLoad()');
  failingSend.run(`toast=()=>{}; globalThis.streams=0; streamChat=async()=>{streams++}; renderAssistant(container);`);
  failingSend.fault('database offline'); failingSend.elements.get('#asInput').value='must survive';
  await failingSend.elements.get('#asSend').onclick();
  assert.equal(failingSend.run('streams'),0,'failed prompt persistence prevents model invocation');
  assert.equal(failingSend.run('ASSIST.history[0].content'),'must survive'); assert.match(failingSend.run('assistStatus()'),/Not saved/);
  // A finished worker's transient DB failure offers retry and persists the retained completed reply.
  const transient = browser(); await transient.run('assistLoad()');
  transient.run(`toast=()=>{}; streamChat=async(body,onEvent)=>{ onEvent({type:'text',delta:'retained final answer'}); onEvent({type:'conversation_error',conflict:false,message:'temporary database failure'}); }; renderAssistant(container);`);
  transient.elements.get('#asInput').value='transient question';
  await transient.elements.get('#asSend').onclick();
  assert.equal(transient.run('ASSIST.session.streaming'),false);
  assert.equal(transient.run('ASSIST.session.pending'),false);
  assert.equal(transient.run('ASSIST.session.conflict'),false,'transient persistence failure must remain retryable');
  assert.equal(transient.run('ASSIST.session.dirty'),true);
  assert.equal(transient.run('ASSIST.history.at(-1).content'),'retained final answer');
  const beforeRetry = transient.requests.length;
  await transient.elements.get('#asRetry').onclick();
  assert.deepEqual(transient.requests.slice(beforeRetry).map(r=>r.method),['GET','PUT'],'retry reconciles the DB revision before writing');
  assert.equal(transient.run('assistStatus()'),'Saved to database');
  assert.equal([...transient.database.values()][0].history.at(-1).content,'retained final answer');
  // A true concurrent revision conflict cannot be retried into an overwrite.
  const conflict = browser(); await conflict.run('assistLoad()');
  conflict.run(`streamChat=async(body,onEvent)=>{ onEvent({type:'text',delta:'keep this unsaved answer'}); onEvent({type:'conversation_error',conflict:true,message:'another device changed the conversation'}); }; renderAssistant(container);`);
  conflict.elements.get('#asInput').value='conflicting question';
  await conflict.elements.get('#asSend').onclick();
  const beforeConflictRetry = conflict.requests.length;
  await conflict.elements.get('#asRetry').onclick();
  assert.equal(conflict.requests.length,beforeConflictRetry,'true conflicts cannot issue another write through Retry');
  assert.equal(conflict.run('ASSIST.session.conflict'),true);
  assert.equal(conflict.run('ASSIST.history.at(-1).content'),'keep this unsaved answer');
  // A completed commit whose stream acknowledgment was lost is recognized without a redundant write.
  const ambiguous = browser(); await ambiguous.run('assistLoad()');
  ambiguous.run(`toast=()=>{}; streamChat=async(body,onEvent)=>{ onEvent({type:'text',delta:'committed answer'}); throw new Error('connection lost before save acknowledgment'); }; renderAssistant(container);`);
  ambiguous.elements.get('#asInput').value='ambiguous commit question';
  await ambiguous.elements.get('#asSend').onclick();
  assert.equal(ambiguous.run('ASSIST.session.dirty'),true);
  const beforePendingRetry = ambiguous.requests.length;
  await ambiguous.elements.get('#asRetry').onclick();
  assert.deepEqual(ambiguous.requests.slice(beforePendingRetry).map(r=>r.method),['GET'],'unconfirmed worker at the same revision must not receive a partial PUT');
  assert.equal(ambiguous.run('ASSIST.session.dirty'),true);
  assert.equal(ambiguous.run('ASSIST.session.conflict'),false);
  const committed = JSON.parse(ambiguous.run('JSON.stringify(assistSnapshot(ASSIST.session))'));
  committed.revision++; committed.updated='now';
  ambiguous.database.set('1:/api/p/alpha/assistant/conversation',committed);
  const beforeAmbiguousRetry = ambiguous.requests.length;
  await ambiguous.elements.get('#asRetry').onclick();
  assert.deepEqual(ambiguous.requests.slice(beforeAmbiguousRetry).map(r=>r.method),['GET'],'matching committed contents only adopt the existing revision');
  assert.equal(ambiguous.run('ASSIST.session.revision'),2);
  assert.equal(ambiguous.run('assistStatus()'),'Saved to database');
  // A changed revision with different data remains a conflict even after a retryable worker failure.
  const newer = browser(); await newer.run('assistLoad()');
  newer.run(`streamChat=async(body,onEvent)=>{ onEvent({type:'text',delta:'local retained reply'}); onEvent({type:'conversation_error',conflict:false,message:'database unavailable'}); }; renderAssistant(container);`);
  newer.elements.get('#asInput').value='question'; await newer.elements.get('#asSend').onclick();
  newer.database.set('1:/api/p/alpha/assistant/conversation',{revision:2,history:[{role:'user',content:'other device conversation'}],provider:'openai',model:'other',updated:'now'});
  const beforeNewerRetry = newer.requests.length;
  await newer.elements.get('#asRetry').onclick();
  assert.deepEqual(newer.requests.slice(beforeNewerRetry).map(r=>r.method),['GET']);
  assert.equal(newer.run('ASSIST.session.conflict'),true);
  assert.equal(newer.run('ASSIST.history.at(-1).content'),'local retained reply');
  console.log('assistant persistence: fresh browser, full history, migration, clear, CAS conflict, offline retry, delayed scope and durable-send ordering passed');
})().catch(error=>{console.error(error);process.exitCode=1});
