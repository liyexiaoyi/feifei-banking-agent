import test from 'node:test'
import assert from 'node:assert/strict'
const mod=await import('./scheduled-controller.ts').catch(()=>null)
const schedule={id:'sch_first',status:'ACTIVE',observed_status:'ACTIVE',version:2,amount_cents:123,account_id:'a_daily',account_name:'日常账户',payee_id:'p_mom',payee_name:'妈妈',payee_phone:'138****0001',due_at:'2026-10-28T13:00:00+08:00',grace_until:'2026-10-29T13:00:00+08:00',authorization_operation_id:'op_auth',child_operation_id:null,child_status:null,child_mode:null,outcome_code:null,reserved_cents:0,created_at:'2026-10-28T12:00:00+08:00',updated_at:'2026-10-28T12:01:00+08:00',cancelled_at:null,replayed:false,simulated:true,notice:'本地模拟'}
const data={schedules:[schedule],payees:[{id:'p_mom',name:'妈妈',masked_phone:'138****0001'}]},intent={payee_id:'p_mom',amount_cents:123,due_at:schedule.due_at}
function deferred(){let resolve:any;return {promise:new Promise<any>(r=>resolve=r),resolve:(x:any)=>resolve(x)}}
function setup(change:any={}){
 assert.ok(mod,'scheduled controller missing');const values=new Map<string,string>(),calls:any[]=[],states:any[]=[]
 const client={session:async()=>'demo',load:async()=>data,prepare:async(i:any,k:any)=>{calls.push(['prepare',i,k]);return {schedule,operation:{id:'op_auth'}}},lookup:async()=>schedule,get:async()=>schedule,cancel:async(s:any,k:any,c:any)=>{calls.push(['cancel',s,k,c]);return {...s,status:'CANCELLED',observed_status:'CANCELLED'}},runDue:async()=>{calls.push(['drive']);return {processed:[]}},readOperation:async()=>({id:'op_auth'}),...change.client}
 const storage=change.storage??{getItem:(k:string)=>values.get(k)??null,setItem:(k:string,v:string)=>values.set(k,v),removeItem:(k:string)=>values.delete(k)}
 const c=mod!.createScheduledController({user:'demo',client,storage,runMutation:change.runMutation??(async(fn:any)=>fn()),onChange:(s:any)=>states.push(s),keyFactory:()=> 'controller-schedule-key'})
 return {c,values,calls,states,key:mod!.pendingScheduleStorageKey('demo')}
}
test('load is read-only, ordinary empty form can prepare, write marker stores opaque reference only',async()=>{
 const h=setup();await h.c.load();assert.equal(h.c.getState().data.payees[0].id,'p_mom');assert.equal(h.calls.length,0)
 await h.c.prepare(intent);assert.equal(h.calls.length,1);assert.deepEqual(JSON.parse(h.values.get(h.key)!),{kind:'create',request_key:'controller-schedule-key'})
 assert(!JSON.stringify([...h.values]).includes('妈妈'));assert(!JSON.stringify([...h.values]).includes('amount'))
})
test('sync write gate blocks double click and read cannot invalidate in-flight write',async()=>{
 const d=deferred(),h=setup({client:{prepare:async()=>{h.calls.push(['prepare']);return d.promise}}});await h.c.load()
 const p=h.c.prepare(intent);for(let i=0;i<8;i++)await Promise.resolve();await h.c.prepare(intent);await h.c.load();assert.equal(h.calls.length,1)
 d.resolve({schedule,operation:{id:'op_auth'}});await p;assert.equal(h.c.getState().recovered.id,schedule.id);assert.equal(h.values.size,1)
})
test('global gate or storage failure cannot dispatch a POST or leave invented success',async()=>{
 for(const change of [{runMutation:async()=>{throw Error('busy')}},{storage:{getItem:()=>null,setItem:()=>{throw Error('disk')},removeItem:()=>{}}}]){
  const h=setup(change);await h.c.load();await h.c.prepare(intent);assert.equal(h.calls.length,0);assert.equal(h.c.getState().recovered,null)
 }
})
test('lost create response and 404 keep original marker and only check by GET',async()=>{
 const h=setup({client:{prepare:async()=>{h.calls.push(['prepare']);throw Error('lost')},lookup:async()=>{h.calls.push(['lookup']);throw Error('404')}}});await h.c.load();await h.c.prepare(intent);await h.c.check();await h.c.prepare(intent)
 assert.deepEqual(h.calls.map(x=>x[0]),['prepare','lookup']);assert.equal(h.c.getState().needsCheck,true);assert.equal(await h.c.startNew(true),false);assert.equal(h.values.size,1)
})
test('create recovery needs successful original lookup and explicit new draft, no auto POST',async()=>{
 const h=setup();h.values.set(h.key,JSON.stringify({kind:'create',request_key:'controller-schedule-key'}));await h.c.load();assert.equal(h.c.getState().recovered.id,schedule.id)
 assert.equal(await h.c.startNew(false),false);assert.equal(await h.c.startNew(true),true);assert.equal(h.values.size,0);assert.equal(h.calls.length,0)
})
test('session mismatch latches private state and preserves original owner marker',async()=>{
 let user='demo';const h=setup({client:{session:async()=>user}});await h.c.load();await h.c.prepare(intent);const before=h.values.get(h.key)
 user='guest';assert.equal(await h.c.startNew(true),false);assert.equal(h.c.getState().invalidSession,true);assert.equal(h.c.getState().data,null);assert.equal(h.c.getState().operation,null);assert.equal(h.values.get(h.key),before)
 user='demo';await h.c.load();assert.equal(h.c.getState().invalidSession,true)
})
test('a session change while GET is pending cannot publish another owner data',async()=>{
 let user='demo';const d=deferred(),h=setup({client:{session:async()=>user,load:async()=>d.promise}})
 const p=h.c.load();for(let i=0;i<8;i++)await Promise.resolve();user='guest';d.resolve(data);await p
 assert.equal(h.c.getState().invalidSession,true);assert.equal(h.c.getState().data,null)
})
test('cancel requires explicit confirmation, stores only identity, and does not retry unknown result',async()=>{
 const h=setup({client:{cancel:async()=>{h.calls.push(['cancel']);throw Error('lost')}}});await h.c.load();await h.c.cancel(schedule,false);assert.equal(h.calls.length,0)
 await h.c.cancel(schedule,true);assert.equal(h.calls.length,1);assert.deepEqual(JSON.parse(h.values.get(h.key)!),{kind:'cancel',request_key:'controller-schedule-key',schedule_id:schedule.id})
 await h.c.cancel(schedule,true);assert.equal(h.calls.length,1)
})
test('drive only runs explicitly; error persists opaque batch marker and check does not run it again',async()=>{
 const h=setup({client:{runDue:async()=>{h.calls.push(['drive']);throw Error('lost')}}});await h.c.load();assert.equal(h.calls.length,0)
 await h.c.drive();assert.equal(h.calls.length,1);assert.deepEqual(JSON.parse(h.values.get(h.key)!),{kind:'drive'})
 await h.c.check();assert.equal(h.calls.length,1);assert.equal(h.c.getState().needsCheck,true);assert.equal(await h.c.startNew(true),true);assert.equal(h.calls.length,1)
})
test('old reads and disposed controllers never publish stale private data',{timeout:3000},async()=>{
 const a=deferred(),b=deferred();let n=0;const h=setup({client:{load:()=>++n===1?a.promise:b.promise}})
 const x=h.c.load();for(let i=0;i<8;i++)await Promise.resolve();const y=h.c.load();for(let i=0;i<8;i++)await Promise.resolve();b.resolve({...data,schedules:[]});await y;a.resolve(data);await x
 assert.equal(h.c.getState().data.schedules.length,0);h.c.dispose();assert.equal(h.c.getState().data,null)
})
test('session change while POST is pending cannot publish former owner response',async()=>{
 let user='demo';const d=deferred(),h=setup({client:{session:async()=>user,prepare:async()=>{h.calls.push(['prepare']);return d.promise}}})
 await h.c.load();const p=h.c.prepare(intent);for(let i=0;i<10;i++)await Promise.resolve();assert.equal(h.calls.length,1)
 user='guest';d.resolve({schedule,operation:{id:'op_auth'}});await p
 assert.equal(h.c.getState().invalidSession,true);assert.equal(h.c.getState().recovered,null);assert.equal(h.c.getState().operation,null);assert.equal(h.values.size,1)
 assert(!h.states.some(s=>s.recovered?.payee_name==='妈妈'))
})
test('unavailable session clears all private state and remains closed until reload',async()=>{
 let fail=false;const h=setup({client:{session:async()=>{if(fail)throw Error('unavailable');return 'demo'}}});await h.c.load();fail=true;await h.c.load()
 assert.equal(h.c.getState().invalidSession,true);assert.equal(h.c.getState().data,null)
})
test('normal pending child can be explicitly cancelled but unresolved child cannot',async()=>{
 const s={...schedule,status:'CHILD_PENDING',observed_status:'WAITING_CONFIRMATION',child_operation_id:'op_child',child_status:'WAITING_CONFIRMATION',child_mode:'MANUAL'}
 const h=setup({client:{load:async()=>({...data,schedules:[s]})}});await h.c.load();await h.c.cancel(s,true);assert.equal(h.calls.length,1)
})
test('identified creation may transition to an explicit cancellation without a duplicate creation',async()=>{
 const h=setup();await h.c.load();await h.c.prepare(intent);await h.c.check();await h.c.cancel(schedule,true)
 assert.deepEqual(h.calls.map(x=>x[0]),['prepare','cancel']);assert.equal(JSON.parse(h.values.get(h.key)!).kind,'cancel')
})
test('unidentified original create cannot be replaced with a cancellation marker',async()=>{
 const h=setup({client:{lookup:async()=>{throw Error('404')}}});await h.c.load();await h.c.prepare(intent);await h.c.check();const before=h.values.get(h.key);await h.c.cancel(schedule,true)
 assert.equal(h.calls.length,1);assert.equal(h.values.get(h.key),before)
})
test('terminal original authorization permits explicit fresh draft even before schedule bookkeeping',async()=>{
 const s={...schedule,status:'AUTHORIZING',observed_status:'CANCELLED'}
 const h=setup({client:{load:async()=>({...data,schedules:[s]}),lookup:async()=>s}});h.values.set(h.key,JSON.stringify({kind:'create',request_key:'controller-schedule-key'}));await h.c.load()
 assert.equal(await h.c.startNew(true),true);assert.equal(h.values.size,0)
})
test('definite creation rejection permits correction without querying nonexistent original forever',async()=>{
 const h=setup({client:{prepare:async()=>{throw Object.assign(Error('definite'),{definiteSchedulePrepareRejection:true})}}});await h.c.load();await h.c.prepare(intent)
 assert.equal(h.values.size,0);assert.equal(h.c.getState().needsCheck,false);assert.equal(h.c.getState().recovered,null)
})
test('definite rejection cannot delete a former owner marker after session change',async()=>{
 let user='demo';const h=setup({client:{session:async()=>user,prepare:async()=>{user='guest';throw Object.assign(Error('definite'),{definiteSchedulePrepareRejection:true})}}});await h.c.load();await h.c.prepare(intent)
 assert.equal(h.c.getState().invalidSession,true);assert.equal(h.values.size,1)
})
