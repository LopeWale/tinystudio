// Invoke shipped page factories through the application graph. No server, model, or database is contacted.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { loadFrontend } = require('./helpers/frontend-loader.cjs');

// Small DOM for page contracts: selectors resolve only elements present in rendered HTML.
// It intentionally supplies no auto-created selectors or fallback API properties.
const decode = text => text.replace(/&(amp|lt|gt|quot|#39|nbsp);/g, (_, key) => ({amp:'&',lt:'<',gt:'>',quot:'"','#39':"'",nbsp:' '})[key]);
const voids = new Set(['input','br','hr','img','meta','link']);
class Element {
  constructor(tag = 'div', attrs = {}) { this.tagName = tag.toLowerCase(); this.attrs = attrs; this.children = []; this.dataset = {}; this.style = {}; this.listeners = {}; for (const [key,value] of Object.entries(attrs)) if (key.startsWith('data-')) this.dataset[key.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())] = value; this.disabled = 'disabled' in attrs; this.checked = 'checked' in attrs; this.hidden = 'hidden' in attrs; this.scrollHeight = 100; }
  get id() { return this.attrs.id; }
  get className() { return this.attrs.class || ''; }
  set className(value) { this.attrs.class = value; }
  get classList() { return { add: value => this.classList.toggle(value,true), remove: value => this.classList.toggle(value,false), contains: value => this.className.split(/\s+/).includes(value), toggle: (value,on) => { const classes = new Set(this.className.split(/\s+/).filter(Boolean)); on ??= !classes.has(value); on ? classes.add(value) : classes.delete(value); this.className = [...classes].join(' '); return on; } }; }
  set innerHTML(html) {
    this.children = []; const stack = [this];
    for (const token of String(html).match(/<\/?[a-zA-Z][^>"']*(?:(?:"[^"]*"|'[^']*')[^>"']*)*>|[^<]+|</g) || []) {
      if (!token.startsWith('<') || token === '<') { stack.at(-1).children.push(decode(token)); continue; }
      const tag = /^<\/?([\w-]+)/.exec(token)[1].toLowerCase();
      if (token.startsWith('</')) { const i = stack.map(node=>node.tagName).lastIndexOf(tag); if (i > 0) stack.length = i; continue; }
      const attrs = {}; const attributes = token.slice(tag.length+1, token.endsWith('/>') ? -2 : -1);
      for (const match of attributes.matchAll(/([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s]+)))?/g)) attrs[match[1]] = decode(match[2] ?? match[3] ?? match[4] ?? '');
      const child = new Element(tag,attrs); stack.at(-1).appendChild(child); if (!voids.has(tag) && !token.endsWith('/>')) stack.push(child);
    }
  }
  get innerHTML() { return this.children.map(node => typeof node === 'string' ? node : `<${node.tagName}>${node.innerHTML}</${node.tagName}>`).join(''); }
  get textContent() { return this.children.map(node => typeof node === 'string' ? node : node.textContent).join(''); }
  set textContent(value) { this.children = [String(value)]; }
  get value() { if (this._value !== undefined) return this._value; if (this.tagName === 'select') return (this.querySelector('option[selected]') || this.querySelector('option'))?.value ?? ''; return this.attrs.value ?? (this.tagName === 'option' || this.tagName === 'textarea' ? this.textContent : ''); }
  set value(value) { this._value = String(value); }
  get selectedOptions() { return this.querySelectorAll('option').filter(node => 'selected' in node.attrs); }
  appendChild(child) { this.children.push(child); child.parentElement = this; return child; }
  remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(node=>node!==this); }
  addEventListener(type, handler) { this.listeners[type] = handler; }
  removeEventListener(type) { delete this.listeners[type]; }
  getAttribute(key) { return this.attrs[key] ?? null; }
  setAttribute(key,value) { this.attrs[key] = String(value); }
  matches(selector) {
    return selector.split(',').some(part => {
      part = part.trim();
      const attrs = [...part.matchAll(/\[([^\]=]+)(?:=["']?([^\]"']*)["']?)?\]/g)];
      if (attrs.some(([,key,value]) => !(key in this.attrs) || value !== undefined && this.attrs[key] !== value)) return false;
      part = part.replace(/\[[^\]]+\]/g,'');
      const tag = /^[\w-]+/.exec(part)?.[0]; if (tag && this.tagName !== tag) return false;
      const id = /#([\w-]+)/.exec(part)?.[1]; if (id && this.id !== id) return false;
      return [...part.matchAll(/\.([\w-]+)/g)].every(([,name])=>this.classList.contains(name));
    });
  }
  closest(selector) { for (let node=this;node;node=node.parentElement) if (node.matches(selector)) return node; return null; }
  querySelectorAll(selector) {
    const result = [];
    const groups = selector.split(',').map(group=>group.trim().replace(/\s*>\s*/g,'>').split(/\s+|(?=>)|(?<=>)/).filter(Boolean));
    const matchChain = (node,parts) => { if (!node || !node.matches(parts.at(-1))) return false; if (parts.length===1) return true; if (parts.at(-2)==='>') return matchChain(node.parentElement,parts.slice(0,-2)); for(let parent=node.parentElement;parent;parent=parent.parentElement) if(matchChain(parent,parts.slice(0,-1))) return true; return false; };
    const walk = node => { for (const child of node.children) if (child instanceof Element) { if (groups.some(parts=>matchChain(child,parts))) result.push(child); walk(child); } }; walk(this); return result;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

// Literal response shapes: services/datasets.py row_dict/counts, services/jobs.py
// job_dict/experiment_dict, services/evals.py eval_dict/result_dict, API routes
// data/training/serving/system/interp and interp/neurons.py read models.
const created = '2026-10-23T12:00:00Z';
const snapshot = { name:'support-v1', dataset:'support', training_count:1, validation_count:1, created };
const dataset = { name:'support', datapoints:2, snapshot_count:1, created, snapshots:[snapshot], labels:[] };
const datapoint = { messages:[{role:'user',content:'Reset thermostat?'},{role:'assistant',content:'Hold reset.'}], bucket:'training', labels:[], source:'uploaded' };
const config = {base_model:'vendor/tiny',training_type:'lora',batch_size:2,gradient_accumulation_steps:1,learning_rate:.0002,epochs:2,max_length:128,lora_r:8,lora_alpha:16};
const job = {name:'support-job',snapshot:snapshot.name,reasoning:false,created};
const experiment = {number:1,state:'succeeded',config,alias:'ft-support-job-1',loss:[{step:1,loss:1},{step:2,eval_loss:.5}],train_loss:.6,metrics:{eval_loss:.5},seconds:12,total_parameters:135e6,checkpoints:[]};
const model = {id:'support-job/1',alias:'ft-support-job-1',kind:'fine-tuned',base_model:'vendor/tiny',parameters:135e6,status:'down',created};
const metric = {name:'Exact',kind:'code',check:'exact_match',description:'Matches the reference'};
const evaluation = {name:'compare-v1',state:'succeeded',snapshot:snapshot.name,models:[model.id],metrics:['Exact'],datapoints:1,created,leaderboard:[{model:model.id,overall:.75,scores:{Exact:.75}}]};
const evalRow = {datapoint:0,model:model.id,metric:'Exact',score:.75,user:'Reset thermostat?',prediction:'Hold reset.',reference:'Hold reset.',reason:'Matched'};
const capture = {name:'support-capture',model_id:'vendor/tiny',source:'dataset:support',layers:[0],num_layers:1,rows:2,tokens:20,topk:5,neurons_per_layer:8,explained:0,tested:0,created,layer_stats:{'0':{max_median:2}}};
const comparison = {name:'support-diff',experiment:model.id,base:'vendor/tiny',source:'dataset:support',layers:[0],rows:2,neurons_per_layer:8,mode:'adapter_toggle',layer_summary:{'0':{median_r:.8}}};
const neuron = {neuron:3,max:2,open_max:2,min:-1,echo_share:0,echo_top:false,density:.1,explanation:'reset commands'};
const change = {neuron:3,change:.4,r:.8,rel_change:.2,before:1,after:2,context:'reset'};
const overview = {project:{name:'Test project'},tree:{datasets:[dataset],metrics:[metric]},catalog:[{id:'vendor/tiny',params_b:.135,notes:'small model'}]};
const responses = new Map([
 ['/api/overview',overview], ['/api/datasets',[dataset]], ['/api/datasets/support?offset=0&limit=100000',{meta:{buckets:{uncategorized:0,training:1,validation:1},labels:[]},rows:[datapoint,{...datapoint,bucket:'validation'}]}],
 ['/api/jobs',[{job,experiments:[experiment]}]], ['/api/jobs/support-job',{job,experiments:[experiment],snapshot,analysis:null}], ['/api/jobs/draft',{job:{...job,name:'draft'},experiments:[],snapshot,analysis:null}], ['/api/autopilot',[]],
 ['/api/evals',[evaluation]], ['/api/evals/compare-v1',{summary:evaluation,rows:[evalRow]}], ['/api/metrics',[metric]], ['/api/models',[model]],
 ['/api/hub/models',{models:[],free_disk_gb:40,token:{configured:false}}], ['/api/repositories',[{name:'manuals',description:'Product manual',embedding_model:'vendor/embed',chunk_count:1,documents:[{name:'manual.txt',kind:'txt',chunks:1}]}]],
 ['/api/settings',{settings:[],updated:null}], ['/api/config',{database_url:'postgres://redacted',database_version:'17',redis_url:'redis://local',qdrant_url:'http://local',inference:{mode:'in-process'},device:'cpu',dtype:'float32',data_dir:'/data'}],
 ['/api/users',[{id:1,email:'admin@example.test',name:'Admin',role:'admin',disabled:false,last_login:created}]], ['/api/projects',[{slug:'test',name:'Test project',goal:'support',datasets:1,jobs:1,created}]],
 ['/api/traces?limit=200',[{id:'trace1',time:created,model:model.id,messages:datapoint.messages,response:'Hold reset.',score:.9}]],
 ['/api/stats',{datasets:1,datapoints:2,snapshots:1,jobs:1,experiments:{succeeded:1},training_seconds:12,evaluations:1,requests:2,prompt_tokens:10,completion_tokens:5,scored_traces:1,requests_per_day:[['2026-10-23',2]],requests_per_model:[[model.id,2]],models_loaded:[]}],
 ['/api/keys',[]], ['/api/interp',{captures:[capture],comparisons:[comparison]}], ['/api/interp/captures/support-capture',capture],
 ['/api/interp/captures/support-capture/layers/0?sort=max&limit=300&q=',{total:1,rows:[neuron]}],
 ['/api/interp/captures/support-capture/layers/0/neurons/3?limit=20',{stats:{max:2,min:-1,density:.1,open_max:2},gate_open:[],positives:[],negatives:[],regimes:{},topk:5,label_share:[],label_share_all:[],explanation:null,test:null}],
 ['/api/interp/diffs/support-diff',comparison], ['/api/interp/diffs/support-diff/layers/0?limit=200&sort=change',{summary:{median_r:.8,rel_change_median:.2,hist:{bins:[0,1],counts:[1]}},rows:[change]}],
 ['/api/interp/diffs/support-diff/layers/0/neurons/3',{...change,datapoints:[{delta_sum:2,user:'Reset thermostat?',labels:[]}],tokens:[{before:1,after:2,context:'reset',datapoint:0}]}],
 ['/api/assistant/status',{mcp:{tools:42,command:'tinystudio mcp --url https://test.invalid'}}],
 ['/api/assistant/models',{anthropic:{available:false,models:[]},openai:{available:true,default:'test-model',models:[{id:'test-model',name:'Test model'}]}}],
]);

(async () => {
  const body = new Element('body');
  const document = {body,createElement:tag=>new Element(tag),querySelector:sel=>body.querySelector(sel),querySelectorAll:sel=>body.querySelectorAll(sel),addEventListener(){},removeEventListener(){}};
  class ConversationSync {start(){} stop(){} requestCheck(){} notifySaved(){}}
  const context = vm.createContext({document,Element,ConversationSync,window:{addEventListener(){},removeEventListener(){}},location:{hash:'#/datasets',origin:'https://test.invalid'},history:{replaceState(_state,_title,hash){context.location.hash=hash;}},localStorage:{getItem(){return null;},setItem(){},removeItem(){}},FormData,AbortController,TextEncoder,TextDecoder,URLSearchParams,console,setTimeout,clearTimeout,fetch:async()=>{throw new Error('Unexpected network access');}});
  const app = await loadFrontend(context);
  app.state.user = {id:1,role:'admin'}; app.state.project = 'test';
  const errors = [], requests = [];
  app.feedback.toast = (message,isError) => { if(isError) errors.push(message); };
  app.http.api = async path => { requests.push(path); assert.ok(responses.has(path),`Missing explicit API fixture: ${path}`); return structuredClone(responses.get(path)); };
  // Only the network transport and shell mount are replaced. Page logic,
  // actual dialogs, context capture and cross-feature callbacks stay native.
  let content;
  app.shell.layout = (_active,html) => { content = new Element(); content.innerHTML = html; body.children=[];body.appendChild(content);app.state.domContext=app.state.pageContext;return content; };
  const flush = () => new Promise(setImmediate);
  const control = (selector,event='onclick') => { const element=content.querySelector(selector);assert.ok(element,`Missing rendered control ${selector}`);assert.equal(typeof element[event],'function',`Unwired ${selector}.${event}`);return element; };
  const binding = selector => { const element=content.querySelector(selector);assert.ok(element,selector);const record=app.assistant.context.bindingFor(element);assert.ok(record,`Missing structured context on ${selector}`);return record; };
  let rendered = 0;
  async function page(hash,fn,heading,controls=[]) {
    context.location.hash=hash;app.assistant.beginRender(hash);await fn();await flush();assert.deepEqual(errors,[],'Render surfaced an error');assert.ok(content.querySelector('h1')?.textContent.includes(heading),`Expected ${heading}`);for(const [selector,event] of controls)control(selector,event);rendered++;
  }
  await page('#/datasets',()=>app.pages.datasets.pageDatasets(),'Datasets',[['#newDataset'],['#dsSearch','oninput'],['[data-ds]']]);
  assert.equal(binding('[data-ds]').data.name,'support');
  control('#dsSearch','oninput').oninput({target:{value:'missing'}});assert.match(content.textContent,/No datasets yet/);
  control('#dsSearch','oninput').oninput({target:{value:'support'}});control('[data-ds]').onclick();assert.equal(context.location.hash,'#/datasets/support');
  await page('#/datasets/support',()=>app.pages.datasets.pageDataset('support'),'support',[['#btnFinetune'],['#btnQuality'],['#bucketFilter','onchange'],['tr[data-i]']]);
  assert.equal(binding('tr[data-i]').data.messages[0].content,'Reset thermostat?');assert.match(content.textContent,/Hold reset\./);
  control('#btnFinetune').onclick();assert.match(body.textContent,/New finetuning job/,'Dataset action must invoke the training feature');
  await page('#/finetuning',()=>app.pages.training.pageFinetuning(),'Finetuning',[['#newJob'],['#autopilotBtn'],['[data-job]']]);
  assert.equal(binding('[data-job]').data.experiments[0].state,'succeeded');
  await page('#/finetuning/support-job',()=>app.pages.training.pageJob('support-job'),'support-job',[['#btnEval'],['#btnDeleteJob'],['#showEval','onchange']]);
  assert.match(content.textContent,/Training loss comparison/);assert.equal(binding('.exp').data.number,1);assert.equal(binding('#lossChart svg').data.series[0].train[0].y,1);
  await control('#btnEval').onclick();await flush();assert.match(body.textContent,/New evaluation/,'Completed job opens the evaluation feature');
  await page('#/finetuning/draft',()=>app.pages.training.pageJob('draft'),'draft',[['#addExp'],['#saveStart'],['#btnStart']]);
  control('#addExp').onclick();assert.ok(content.querySelector('input[data-f="base_model"]'));assert.match(content.textContent,/Experiment 1/);
  await page('#/evaluations',()=>app.pages.evaluations.pageEvaluations(),'Evaluations',[['#newEval'],['[data-ev]']]);
  assert.equal(binding('[data-ev]').data.name,'compare-v1');
  await page('#/evaluations/compare-v1',()=>app.pages.evaluations.pageEval('compare-v1'),'compare-v1',[['#delEval'],['[data-mt]'],['[data-cell]']]);
  assert.equal(binding('[data-cell]').data.score,.75);assert.equal(binding('#scoreHist svg').data.rows[0].prediction,'Hold reset.');assert.match(content.textContent,/75%/);
  control('[data-cell]').onclick();assert.match(body.textContent,/Evaluation result details/);
  await page('#/metrics',()=>app.pages.metrics.pageMetrics(),'Metrics',[['#newMetric'],['[data-me]']]);assert.equal(binding('[data-me]').data.name,'Exact');
  await page('#/playground',()=>app.pages.playground.pagePlayground(null),'Playground',[['#pgSend'],['#pgReset'],['#pgModel','onchange']]);assert.match(content.textContent,/ft-support-job-1/);assert.match(content.textContent,/manuals/);
  await page('#/models',()=>app.pages.models.pageModels(),'Models',[['[data-toggle]'],['#hubSearch'],['#hfSave']]);assert.equal(binding('tr[data-context-id]').data.id,model.id);
  await page('#/repositories',()=>app.pages.repositories.pageRepositories(),'Repositories',[['#newRepo'],['[data-add]'],['[data-query]']]);assert.equal(binding('[data-repo]').data.chunk_count,1);assert.match(content.textContent,/manual.txt/);
  await page('#/settings',()=>app.pages.settings.pageSettings(),'Settings',[['#stSave'],['[data-test]']]);assert.match(content.textContent,/PostgreSQL/);
  await page('#/users',()=>app.pages.users.pageUsers(),'Users',[['#newUser'],['[data-role]','onchange'],['[data-pw]']]);assert.equal(binding('tr[data-context-id]').data.email,'admin@example.test');
  await page('#/projects',()=>app.pages.projects.pageProjects(),'Projects',[['#newProject'],['[data-proj]']]);assert.equal(binding('[data-proj]').data.slug,'test');
  await page('#/traces',()=>app.pages.traces.pageTraces(),'Traces',[['#trPromote'],['[data-s]'],['[data-fix]']]);assert.equal(binding('tr[data-context-id]').data.score,.9);
  await page('#/stats',()=>app.pages.stats.pageStats(),'Stats');assert.equal(binding('.tile').data.value,1);assert.match(content.textContent,/Requests per day/);
  await page('#/api-keys',()=>app.pages.keys.pageKeys(),'API Keys',[['#newKey']]);assert.match(content.textContent,/No keys yet/);assert.match(content.textContent,/test\/</);
  await page('#/interp',()=>app.pages.interpretability.pageInterp(),'Interpretability',[['#newCapture'],['#newDiff'],['[data-cap]'],['[data-diff]']]);assert.equal(binding('[data-cap]').data.name,capture.name);
  await page('#/interp/c/support-capture',()=>app.pages.interpretability.pageCapture(capture.name,new URLSearchParams()),capture.name,[['#explainTop'],['#layerSel','onchange'],['tr[data-n]']]);assert.equal(binding('tr[data-n]').data.neuron,3);await control('tr[data-n]').onclick();assert.match(content.textContent,/L0 · N3/);assert.equal(binding('.ipanel').data.stats.max,2);
  await page('#/interp/d/support-diff',()=>app.pages.interpretability.pageDiff(comparison.name,new URLSearchParams()),model.id,[['#layerSel','onchange'],['#sortSel','onchange'],['tr[data-n]']]);assert.equal(binding('tr[data-n]').data.r,.8);await control('tr[data-n]').onclick();assert.match(content.textContent,/Datapoints that drove the change/);assert.equal(binding('.ipanel').data.datapoints[0].delta_sum,2);
  // Assistant persistence/streaming have separate suites; render the full page here too.
  app.http.rawFetch=async()=>({ok:true,json:async()=>({revision:0,history:[],provider:'openai',model:'test-model',updated:null}),headers:{get:()=>null}});
  await app.assistant.conversation.assistLoad();
  await page('#/assistant',()=>app.assistant.pageAssistant(),'Assistant',[['#asSend'],['#asReset'],['#asPick']]);assert.match(content.textContent,/Saved to database/);
  assert.deepEqual(errors,[]);assert.ok(requests.includes('/api/evals/compare-v1'));app.destroy();
  console.log(`frontend pages: ${rendered} native page/list/detail branches, controls, cross-feature dialogs and structured data bindings passed`);
})().catch(error=>{console.error(error);process.exitCode=1;});
