import test from 'node:test'
import assert from 'node:assert/strict'
const mod=await import('./card-control-controller.ts').catch(()=>null)
const card={id:'card_a_daily',account_id:'a_daily',name:'日常演示卡',masked_number:'DEMO ···· 0826',status:'ACTIVE',version:1,simulated:true}
const bundle={card,controls:{card_id:card.id,locked:false,single_limit_cents:2000000,daily_limit_cents:5000000,control_version:1,spent_today_cents:0,simulated:true},credential:{card_id:card.id,configured:false,credential_version:0,simulated:true,bank_connected:false},merchants:[{id:'demo_market',name:'演示超市',simulated:true,merchant_connected:false}]}
const operation={id:'op_controller_first',type:'card_spend',status:'WAITING_CONFIRMATION',risk:'YELLOW',created_at:1,expires_at:301,payload:{card_id:card.id,amount_cents:100,simulated:true},result:null}
const receipt=(status='UNKNOWN')=>({operation:{...operation,status},recorded_status:status,observed_status:status,read_only:true,simulated:true})
const spend={kind:'spend',merchant_id:'demo_market',amount_cents:100}
const defer=()=>{let resolve!:(v:any)=>void;const promise=new Promise<any>(r=>resolve=r);return {promise,resolve}}
function setup(overrides:any={}){
 assert.ok(mod,'card-control-controller.ts must exist')
 const values=new Map<string,string>(),changes:any[]=[],calls:any[]=[]
 const storage={getItem:(k:string)=>values.get(k)??null,setItem:(k:string,v:string)=>{values.set(k,v)},removeItem:(k:string)=>{values.delete(k)}}
 const client={load:async()=>structuredClone(bundle),prepare:async(...args:any[])=>{calls.push(args);return structuredClone(operation)},lookup:async()=>receipt()}
 const c=mod.createCardControlController({client,storage,user:'demo',cardId:card.id,runMutation:async(f:()=>Promise<void>)=>f(),onChange:(s:any)=>changes.push(s),keyFactory:()=> 'controller-key-first',...overrides})
 return {c,client,storage,values,changes,calls,key:mod.pendingCardStorageKey('demo',card.id)}
}

test('decimal amounts use exact cents and reject unsafe forms',()=>{
 assert.ok(mod)
 for(const [text,cents] of [['0.01',1],['1',100],['1.2',120],['1000000',100000000]] as const)assert.equal(mod.parseCardAmount(text),cents)
 for(const text of ['0','-1','1.234','1e3','Infinity','1000000.01',''])assert.equal(mod.parseCardAmount(text),null)
 assert.equal(mod.parseCardAmount('50000.01',5000000),null)
})
test('initial load has no writes and no guessed pending request',async()=>{
 const h=setup();await h.c.load();assert.equal(h.c.getState().needsCheck,false);assert.equal(h.c.getState().bundle.card.id,card.id);assert.equal(h.calls.length,0);assert.equal(h.values.size,0)
})
test('one prepare stores only opaque reference and blocks double-click or resending',async()=>{
 const d=defer(),calls:any[]=[];const h=setup({client:{load:async()=>bundle,prepare:async(...a:any[])=>{calls.push(a);return d.promise},lookup:async()=>receipt()}})
 await h.c.load();const first=h.c.prepare(spend);await Promise.resolve();await h.c.prepare(spend);assert.equal(calls.length,1)
 assert.deepEqual(JSON.parse(h.values.get(h.key)!),{card_id:card.id,request_key:'controller-key-first',type:'card_spend'})
 d.resolve(operation);await first;assert.equal(h.c.getState().receipt.operation.id,operation.id);assert.equal(h.c.getState().needsCheck,true)
})
test('gate rejection before callback creates no marker and sends nothing',async()=>{
 const h=setup({runMutation:async()=>{throw new Error('gate occupied')}});await h.c.load();await h.c.prepare(spend)
 assert.equal(h.calls.length,0);assert.equal(h.values.size,0);assert.equal(h.c.getState().needsCheck,false)
})
test('local invalid intent never persists marker or reaches API',async()=>{
 const h=setup();await h.c.load();await h.c.prepare({...spend,amount_cents:1.2});assert.equal(h.calls.length,0);assert.equal(h.values.size,0);assert.equal(h.c.getState().needsCheck,false)
})
test('storage failure prevents POST and fails closed',async()=>{
 const h=setup({storage:{getItem:()=>null,setItem:()=>{throw new Error('storage failed')},removeItem:()=>{}}});await h.c.load();await h.c.prepare(spend);assert.equal(h.calls.length,0);assert.ok(h.c.getState().error)
})
test('unknown POST result or 404 lookup is never automatically resent or cleared',async()=>{
 let posts=0,reads=0;const h=setup({client:{load:async()=>bundle,prepare:async()=>{posts++;throw new Error('lost response')},lookup:async()=>{reads++;throw new Error('404')}}})
 await h.c.load();await h.c.prepare(spend);assert.equal(posts,1);await h.c.check();await h.c.prepare(spend)
 assert.equal(posts,1);assert.equal(reads,1);assert.equal(h.c.getState().needsCheck,true);assert.equal(h.values.size,1);assert.equal(h.c.startNew(true),false)
})
test('restored UNKNOWN stays read-only; only observed terminal state plus explicit action permits new draft',async()=>{
 let status='UNKNOWN';const h=setup({client:{load:async()=>bundle,prepare:async()=>operation,lookup:async()=>receipt(status)}})
 h.values.set(h.key,JSON.stringify({card_id:card.id,request_key:'controller-key-first',type:'card_spend'}));await h.c.load()
 assert.equal(h.c.startNew(true),false);status='SUCCEEDED';await h.c.check();assert.equal(h.c.startNew(false),false);assert.equal(h.c.startNew(true),true);assert.equal(h.values.size,0);assert.equal(h.c.getState().needsCheck,false)
})
test('late read and disposed controller cannot publish old private state',async()=>{
 const a=defer(),b=defer();let n=0;const h=setup({client:{load:()=>++n===1?a.promise:b.promise,prepare:async()=>operation,lookup:async()=>receipt()}})
 const first=h.c.load(),second=h.c.load();b.resolve({...bundle,controls:{...bundle.controls,control_version:2}});await second;a.resolve(bundle);await first;assert.equal(h.c.getState().bundle.controls.control_version,2)
 h.c.dispose();assert.equal(h.c.getState().bundle,null)
})
test('pending marker cannot migrate across user/card and malformed marker never authorizes a new write',async()=>{
 const h=setup();assert.notEqual(h.key,mod!.pendingCardStorageKey('guest',card.id));assert.notEqual(h.key,mod!.pendingCardStorageKey('demo','card_a_saving'))
 h.values.set(h.key,JSON.stringify({card_id:'foreign_card',request_key:'controller-key-first',type:'card_spend'}));await h.c.load();assert.equal(h.c.getState().needsCheck,true);await h.c.prepare(spend);assert.equal(h.calls.length,0)
})
test('secret intent is used for one call but absent from controller state and storage',async()=>{
 const newSecret='OnlyDemo_new-private-123',oldSecret='OnlyDemo_old-private-123';const h=setup({client:{load:async()=>({...bundle,credential:{...bundle.credential,configured:true,credential_version:1}}),prepare:async()=>({...operation,type:'card_credential_change',risk:'RED'}),lookup:async()=>receipt()}})
 await h.c.load();await h.c.prepare({kind:'credential',new_secret:newSecret,current_secret:oldSecret})
 const publicText=JSON.stringify([h.c.getState(),h.changes,[...h.values.values()]])
 assert(!publicText.includes(newSecret)&&!publicText.includes(oldSecret))
})

test('controller calls the implemented client signatures exactly',async()=>{
 const readArgs:any[]=[],h=setup({client:{load:async()=>bundle,prepare:async(...args:any[])=>{readArgs.push(['prepare',...args]);return operation},lookup:async(...args:any[])=>{readArgs.push(['lookup',...args]);return receipt()}}})
 await h.c.load();await h.c.prepare(spend);await h.c.check()
 assert.deepEqual(readArgs,[['prepare',bundle,spend,'controller-key-first'],['lookup',card.id,'controller-key-first','card_spend']])
})
test('read refresh hides stale bundle and blocks preparation until it finishes',async()=>{
 const d=defer();let hold=false,posts=0;const h=setup({client:{load:async()=>hold?d.promise:bundle,prepare:async()=>{posts++;return operation},lookup:async()=>receipt()}})
 await h.c.load();hold=true;const p=h.c.load();assert.equal(h.c.getState().busy,true);assert.equal(h.c.getState().bundle,null);await h.c.prepare(spend);assert.equal(posts,0);d.resolve(bundle);await p;assert.equal(h.c.getState().busy,false)
})
test('read during an active write cannot invalidate or replace that write',async()=>{
 const d=defer();let loads=0;const h=setup({client:{load:async()=>{loads++;return bundle},prepare:async()=>d.promise,lookup:async()=>receipt()}})
 await h.c.load();const p=h.c.prepare(spend);await Promise.resolve();await h.c.load();assert.equal(loads,1);d.resolve(operation);await p;assert.equal(h.c.getState().receipt.operation.id,operation.id)
})
test('lost-card transaction unlock remains independent from restoring lost status',async()=>{
 let posts=0;const h=setup({client:{load:async()=>({...bundle,card:{...card,status:'LOST'},controls:{...bundle.controls,locked:true}}),prepare:async()=>{posts++;return {...operation,type:'card_controls_update',risk:'RED'}},lookup:async()=>receipt()}})
 await h.c.load();await h.c.prepare({kind:'control',action:'unlock',value:false});assert.equal(posts,1)
})
test('over-5000000-cent control value never creates a marker',async()=>{
 const h=setup();await h.c.load();await h.c.prepare({kind:'control',action:'set_daily_limit',value:5000001});assert.equal(h.calls.length,0);assert.equal(h.values.size,0)
})
test('authoritative pre-creation rejection can be corrected without treating it as an unknown payment',async()=>{
 const h=setup({client:{load:async()=>bundle,prepare:async()=>{throw Object.assign(new Error('rejected'),{definiteCardPrepareRejection:true})},lookup:async()=>receipt()}})
 await h.c.load();await h.c.prepare(spend);assert.equal(h.values.size,0);assert.equal(h.c.getState().needsCheck,false);await h.c.load();assert.equal(h.c.getState().error,'')
})
