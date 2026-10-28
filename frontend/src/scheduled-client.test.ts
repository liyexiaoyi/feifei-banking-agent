import test from 'node:test'
import assert from 'node:assert/strict'
const mod=await import('./scheduled-client.ts').catch(()=>null)
function module(){assert.ok(mod,'typed scheduled client missing');return mod!}
export const schedule={id:'sch_first',status:'ACTIVE',observed_status:'ACTIVE',version:2,amount_cents:123,account_id:'a_daily',account_name:'日常账户',payee_id:'p_mom',payee_name:'妈妈',payee_phone:'138****0001',due_at:'2026-10-28T13:00:00+08:00',grace_until:'2026-10-29T13:00:00+08:00',authorization_operation_id:'op_authorize',child_operation_id:null,child_status:null,child_mode:null,outcome_code:null,reserved_cents:0,created_at:'2026-10-28T12:00:00+08:00',updated_at:'2026-10-28T12:01:00+08:00',cancelled_at:null,replayed:false,simulated:true,notice:'本地模拟；未连接银行；授权不是付款'}
export const auth={id:'op_authorize',type:'scheduled_transfer_authorize',status:'WAITING_CONFIRMATION',risk:'RED',created_at:1793160000,expires_at:1793160300,payload:{account_id:'a_daily',account_name:'日常账户',payee_id:'p_mom',payee_name:'妈妈',payee_phone:'138****0001',amount_cents:123,fee_cents:0,schedule_id:'sch_first',due_at_iso:'2026-10-28T13:00:00+08:00'},result:null,confirmed_at:null,executed_day:null}
export const bundle={schedules:[schedule],payees:[{id:'p_mom',name:'妈妈',masked_phone:'138****0001'}]}
const intent={payee_id:'p_mom',amount_cents:123,due_at:'2026-10-28T13:00:00+08:00'}
test('strict schedule whitelist never carries raw private fields to UI',()=>{
 const s=module().parseSchedule({...schedule,request_hash:'private-hash',request_key:'private-key',payee_phone_digest:'private-digest'})
 assert.equal(s.id,schedule.id);assert(!JSON.stringify(s).includes('private-'));assert.equal(s.reserved_cents,0)
})
test('schedule amount versions dates and child links fail closed',()=>{
 for(const x of [{amount_cents:true},{amount_cents:1.5},{version:0},{replayed:1},{simulated:false},{due_at:'2026-02-30T13:00:00+08:00'},{due_at:'2026-10-28T13:00:00'},{grace_until:'2026-10-30T13:00:00+08:00'},{child_operation_id:'op_child'},{payee_phone:'13800000001'},{status:'CONFIRMED'}])assert.throws(()=>module().parseSchedule({...schedule,...x}),JSON.stringify(x))
 assert.throws(()=>module().parseSchedule(schedule,'sch_foreign'))
})
test('decimal cents and China local datetime parsing do not guess or round',()=>{
 assert.equal(module().parseScheduledAmount('0.01'),1);assert.equal(module().parseScheduledAmount('1000000.00'),100000000)
 for(const x of ['','1.001','1e2','-1','1000000.01'])assert.equal(module().parseScheduledAmount(x),null)
 assert.equal(module().parseScheduledTime('2026-10-28T13:05'),'2026-10-28T13:05:00+08:00')
 for(const x of ['2026-02-30T13:00','2026-10-28T24:00','2026-10-28T13:00+08:00',''])assert.equal(module().parseScheduledTime(x),null)
})
test('authorization is always red, immutable, and a success result is not a payment',()=>{
 assert.equal(module().parseScheduleOperation(auth).type,auth.type)
 for(const x of [{risk:'YELLOW'},{payload:{...auth.payload,amount_cents:true}},{payload:{...auth.payload,fee_cents:1}},{payload:{...auth.payload,due_at_iso:'not-time'}}])assert.throws(()=>module().parseScheduleOperation({...auth,...x}))
 assert.throws(()=>module().parseScheduleOperation({...auth,payload:{...auth.payload,schedule_id:'sch_other'}},schedule))
 const result={schedule_id:'sch_first',status:'ACTIVE',amount_cents:123,due_at:schedule.due_at,simulated:true,message:'授权生效，未付款'}
 assert.equal(module().parseScheduleOperation({...auth,status:'SUCCEEDED',result}).result.status,'ACTIVE')
 assert.throws(()=>module().parseScheduleOperation({...auth,status:'SUCCEEDED',result:{...result,transaction_id:'fake-paid'}}))
})
test('load performs only GET and validates participants and duplicate ids',async()=>{
 const calls:any[]=[];const api=async(path:string,body?:unknown)=>{calls.push([path,body]);return path==='/scheduled-transfers'?{schedules:[schedule],simulated:true}:{participants:bundle.payees,simulated:true}}
 assert.equal((await module().createScheduledClient(api).load()).payees[0].id,'p_mom');assert(calls.every(x=>x[1]===undefined));assert(!calls.some(x=>x[0].includes('run-due')))
 await assert.rejects(()=>module().createScheduledClient(async p=>p==='/scheduled-transfers'?{schedules:[schedule,schedule],simulated:true}:{participants:bundle.payees,simulated:true}).load())
})
test('prepare sends one exact body, binds both response objects, never confirms or retries',async()=>{
 const calls:any[]=[];const c=module().createScheduledClient(async(path,body)=>{calls.push([path,body]);return {schedule:{...schedule,status:'AUTHORIZING',observed_status:'AUTHORIZING',version:1},operation:auth}})
 const z=await c.prepare(intent,'scheduled-client-key');assert.equal(z.operation.id,auth.id)
 assert.deepEqual(calls,[['/scheduled-transfers',{...intent,request_key:'scheduled-client-key'}]])
 await assert.rejects(()=>c.prepare({...intent,amount_cents:true},'scheduled-client-key'));assert.equal(calls.length,1)
 let n=0;await assert.rejects(()=>module().createScheduledClient(async()=>{n++;throw Error('lost response')}).prepare(intent,'scheduled-client-key'));assert.equal(n,1)
 await assert.rejects(()=>module().createScheduledClient(async()=>({schedule,operation:{...auth,payload:{...auth.payload,amount_cents:124}}})).prepare(intent,'scheduled-client-key'))
})
test('lookup is exact read-only, cancel binds version and explicit consent',async()=>{
 const calls:any[]=[];const c=module().createScheduledClient(async(path,body)=>{calls.push([path,body]);return schedule})
 await c.lookup('scheduled-client-key');assert.deepEqual(calls[0],['/scheduled-transfers/requests/scheduled-client-key',undefined])
 await assert.rejects(()=>c.cancel(schedule,'scheduled-cancel-key',false));assert.equal(calls.length,1)
 await c.cancel(schedule,'scheduled-cancel-key',true)
 assert.deepEqual(calls[1],['/scheduled-transfers/sch_first/cancel',{expected_version:2,request_key:'scheduled-cancel-key',confirmed:true}])
})
test('runDue is one strict POST only on request and validates result',async()=>{
 const calls:any[]=[];const result={checked_at:schedule.created_at,simulated:true,processed:[],next_due_at:schedule.due_at,notice:'仅此页打开时检查'}
 const c=module().createScheduledClient(async(p,b)=>{calls.push([p,b]);return result});assert.equal((await c.runDue()).processed.length,0)
 assert.deepEqual(calls,[['/scheduled-transfers/run-due',{}]])
 await assert.rejects(()=>module().createScheduledClient(async()=>({...result,processed:[{schedule_id:'sch_first',version:true,status:'ACTIVE',outcome_code:null,child_operation_id:null,action:'EXECUTED'}]})).runDue())
})
test('receipt parsing checks read-only binding and never treats a child as its authorization',async()=>{
 const receipt={operation:auth,recorded_status:auth.status,observed_status:auth.status,read_only:true,simulated:true,checked_at:schedule.created_at}
 const c=module().createScheduledClient(async()=>receipt);assert.equal((await c.readOperation(auth.id,schedule)).id,auth.id)
 await assert.rejects(()=>c.readOperation('op_foreign',schedule))
 await assert.rejects(()=>module().createScheduledClient(async()=>({...receipt,read_only:false})).readOperation(auth.id,schedule))
})
test('real Bank fractional timestamps and microsecond checked_at are accepted',async()=>{
 const op={...auth,created_at:auth.created_at+0.125,expires_at:auth.expires_at+0.125,confirmed_at:auth.created_at+0.25}
 assert.equal(module().parseScheduleOperation(op).created_at,op.created_at)
 const r={operation:op,recorded_status:op.status,observed_status:op.status,read_only:true,simulated:true,checked_at:'2026-10-28T12:00:00.125000+08:00'}
 assert.equal((await module().createScheduledClient(async()=>r).readOperation(op.id,schedule)).id,op.id)
})
test('child mode, run action, display identity and risk cannot drift from bindings',async()=>{
 assert.throws(()=>module().parseSchedule({...schedule,status:'CHILD_PENDING',observed_status:'WAITING_CONFIRMATION',child_operation_id:'op_child',child_status:'WAITING_CONFIRMATION',child_mode:'EVIL'}))
 for(const p of [{payee_name:'别人'},{payee_phone:'139****1111'},{account_name:'别的账户'}])assert.throws(()=>module().parseScheduleOperation({...auth,payload:{...auth.payload,...p}},schedule))
 const child={...auth,id:'op_child',type:'transfer',risk:'GREEN',payload:{...auth.payload}};delete child.payload.schedule_id;delete child.payload.due_at_iso
 assert.throws(()=>module().parseScheduleOperation(child,{...schedule,child_operation_id:'op_child',child_status:'WAITING_CONFIRMATION',child_mode:'MANUAL'}))
 const result={checked_at:schedule.created_at,simulated:true,processed:[{schedule_id:'sch_first',version:2,status:'ACTIVE',outcome_code:null,child_operation_id:null,action:'MADE_UP'}],next_due_at:null,notice:'本地模拟'}
 await assert.rejects(()=>module().createScheduledClient(async()=>result).runDue())
})
test('observed expiration cannot return an executable original pending card',async()=>{
 const r={operation:auth,recorded_status:auth.status,observed_status:'EXPIRED',read_only:true,simulated:true,checked_at:'2026-10-28T12:10:00+08:00'}
 const z=await module().createScheduledClient(async()=>r).readOperation(auth.id,schedule)
 assert.equal(z.status,'EXPIRED')
 await assert.rejects(()=>module().createScheduledClient(async()=>({...r,observed_status:'SUCCEEDED'})).readOperation(auth.id,schedule))
})
test('read-only original authorization outcomes remain visible without rewriting schedule status',()=>{
 for(const observed_status of ['CANCELLED','EXPIRED','UNKNOWN','EXECUTING','MANUAL_REVIEW'])assert.equal(module().parseSchedule({...schedule,status:'AUTHORIZING',observed_status}).observed_status,observed_status)
})
test('only authoritative creation rejections are safe to correct, with no raw message echo',async()=>{
 const c=module().createScheduledClient(async()=>{throw Object.assign(Error('private server body'),{code:'INVALID_DUE_AT',status:400})})
 await assert.rejects(()=>c.prepare(intent,'rejected-prepare-key'),(e:any)=>e.definiteSchedulePrepareRejection===true&&!e.message.includes('private server body'))
 for(const e of [Object.assign(Error('gateway'),{status:503,code:'INVALID_DUE_AT'}),Object.assign(Error('conflict'),{status:409,code:'IDEMPOTENCY_CONFLICT'}),Error('network')]){
  await assert.rejects(()=>module().createScheduledClient(async()=>{throw e}).prepare(intent,'rejected-prepare-key'),(x:any)=>x.definiteSchedulePrepareRejection!==true)
 }
})
