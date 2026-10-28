import {sanitizeEvidence} from '../app/evidence';
export type ComponentActionKind='navigate'|'filter'|'select'|'open'|'mutate'|'delete';
export interface ComponentActionScope {user:string;project:string;route:string}
export interface ComponentActionDefinition {
 id:string;label:string;kind:ComponentActionKind;state?:unknown;
 options?:readonly {value:string;label:string}[];disabled?:boolean;
 run:(value?:string,signal?:AbortSignal)=>unknown|Promise<unknown>;
}
interface Entry {ref:string;definition:ComponentActionDefinition;active:boolean;generation:number;flights:Set<AbortController>}
const kinds=new Set<ComponentActionKind>(['navigate','filter','select','open','mutate','delete']);
const privateRoute=(route:string)=>/^\/(?:api-keys|settings|users)(?:[/?]|$)/.test(route.replace(/^#/,''));
/** Owns committed component handlers; model-visible records never contain executable callbacks. */
export class ComponentActionRegistry {
 private scope:ComponentActionScope={user:'',project:'',route:''};private generation=0;private sequence=0;private entries=new Map<string,Entry>();
 constructor(private confirm:(message:string,title?:string)=>Promise<boolean>=async()=>false){}
 setScope(scope:ComponentActionScope){const normalized={...scope,route:scope.route.replace(/^#/,'')};if(JSON.stringify(normalized)===JSON.stringify(this.scope))return;this.scope=normalized;this.generation++;for(const entry of this.entries.values())this.revoke(entry);this.entries.clear();}
 private revoke(entry:Entry){entry.active=false;for(const controller of entry.flights)controller.abort();entry.flights.clear();}
 register(definition:ComponentActionDefinition){
  if(!kinds.has(definition.kind)||!definition.id||!definition.label||typeof definition.run!=='function')throw new TypeError('A component action requires an explicit kind, identity, label and handler');
  const entry:Entry={ref:`action_${this.generation}_${++this.sequence}`,definition:{...definition,options:definition.options?.map(option=>({...option}))},active:true,generation:this.generation,flights:new Set()};
  if(this.scope.user&&this.scope.project&&!privateRoute(this.scope.route))this.entries.set(entry.ref,entry);else entry.active=false;
  return {update:(next:ComponentActionDefinition)=>{if(!entry.active||entry.generation!==this.generation)return;if(next.id!==definition.id||next.kind!==definition.kind)throw new Error('Component action identity/kind cannot change during registration');entry.definition={...next,options:next.options?.map(option=>({...option}))};},dispose:()=>{this.revoke(entry);if(this.entries.get(entry.ref)===entry)this.entries.delete(entry.ref);}};
 }
 inspect(){return {scope:{...this.scope},untrusted:true,actions:Array.from(this.entries.values()).filter(entry=>entry.active).map(({ref,definition})=>({ref,id:definition.id,label:definition.label,kind:definition.kind,disabled:!!definition.disabled,state:sanitizeEvidence(definition.state),options:definition.options?.slice(0,100).map(option=>({...option})),omitted_options:Math.max(0,(definition.options?.length||0)-100)}))};}
 async execute(ref:unknown,value:unknown,allowDestructive:boolean){
  if(typeof ref!=='string'||!/^action_\d+_\d+$/.test(ref))throw new Error('Use an action ref returned by ui_list_actions');
  if(value!==undefined&&value!==null&&typeof value!=='string')throw new Error('Component action value must be a string');
  if(typeof value==='string'&&value.length>4000)throw new Error('Component action value exceeds 4000 characters');
  const entry=this.entries.get(ref);if(!entry?.active||entry.generation!==this.generation)throw new Error('Component action is stale; inspect the current components again');
  const definition=entry.definition;if(definition.disabled)throw new Error('This component action is currently disabled');
  const input=value===null?undefined:value as string|undefined;
  if(definition.options&&!definition.options.some(option=>option.value===input))throw new Error('Choose an allowed component value from ui_list_actions');
  const controller=new AbortController();entry.flights.add(controller);const origin={...this.scope};
  try {
   if(definition.kind==='mutate'||definition.kind==='delete'){
    if(!allowDestructive)throw new Error('This action requires the actions / deletions opt-in for this turn');
    if(!await this.confirm(`${definition.label}? This changes saved workspace data.`,definition.kind==='delete'?'Confirm deletion':'Confirm workspace change'))throw new Error('Component action cancelled by the user');
    if(!entry.active||controller.signal.aborted||entry.definition!==definition)throw new Error('Component changed while waiting for confirmation');
   }
   const output=await definition.run(input,controller.signal);
   if(this.scope.user!==origin.user||this.scope.project!==origin.project)throw new Error('Account or project changed; component result discarded');
   if(controller.signal.aborted||!entry.active)return {performed:definition.label,status:'view_changed',note:'The view changed. Inspect current component state before the next action.'};
   return {performed:definition.label,status:'completed',result:sanitizeEvidence(output),scope:{...this.scope}};
  }finally{entry.flights.delete(controller);}
 }
}
