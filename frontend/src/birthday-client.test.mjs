import test from 'node:test'
import assert from 'node:assert/strict'
import {existsSync} from 'node:fs'
import {flow,bundle,budgetOp,linkedFlow,intent,opid} from './birthday-fixtures.mjs'

async function mod(){assert(existsSync(new URL('./birthday-client.ts',import.meta.url)),'birthday client not implemented');return import('./birthday-client.ts')}

test('real server verification and blocked graph states remain readable',async()=>{
 const m=await mod(),f=linkedFlow();f.status='BLOCKED';f.nodes[0].state=f.nodes[0].observed_state='WAITING_VERIFICATION';assert.equal(m.parseBirthdayFlow(f).status,'BLOCKED')
 for(const st of ['READY','PAUSED','EXPIRED']){const bad=flow();bad.nodes[1].state=st;assert.throws(()=>m.parseBirthdayFlow(bad))}
 for(const st of ['FAILED','RUNNING']){const bad=flow();bad.status=st;assert.throws(()=>m.parseBirthdayFlow(bad))}
 assert.equal(m.parseBirthdayKey('chat:birthday.test-01'),'chat:birthday.test-01')
})

test('operation parser rejects invented states and malformed unknown financial results',async()=>{
 const m=await mod(),op=budgetOp();op.status='RUNNING';assert.throws(()=>m.parseBirthdayOperation(op))
 op.status='UNKNOWN';op.result={budget_id:'bb_test',simulated:false,message:'假结果'};assert.throws(()=>m.parseBirthdayOperation(op))
 op.result={budget_id:'bb_test',simulated:true,message:'旧预留'};assert.equal(m.parseBirthdayOperation(op).status,'UNKNOWN')
})

test('birthday money and dates require explicit exact values',async()=>{
 const m=await mod()
 for(const [s,n] of [['0.01',1],['1000',100000],['123.45',12345],['1000000',100000000]])assert.equal(m.parseBirthdayAmount(s),n)
 for(const s of ['0','1.001','1e3','1,000','一千来块','-1','Infinity','1000000.01'])assert.equal(m.parseBirthdayAmount(s),null)
 assert.equal(m.parseBirthdayDate('2024-02-29'),'2024-02-29')
 for(const s of ['2026-02-29','2026-02-30','明天','2026-1-2','2026-13-01'])assert.equal(m.parseBirthdayDate(s),null)
})
test('flow parser preserves recorded vs observed facts but unknown dominates',async()=>{
 const m=await mod(),f=flow();assert.deepEqual(m.parseBirthdayFlow(f),f)
 f.status='COMPLETED';f.cake_choice=false;for(const n of f.nodes)n.state=n.observed_state=([3,5].includes(n.idx)?'SKIPPED':'SUCCEEDED');f.nodes[0].operation_id=opid;f.nodes[4].operation_id='op_'+'c'.repeat(32);f.nodes[4].observed_state='UNKNOWN';assert.equal(m.birthdayNeedsCheck(m.parseBirthdayFlow(f)),true)
})
for(const damage of ['cents_bool','version_float','duplicate_node','wrong_type','fake_simulated','secret','extra_price','duplicate_op'])test('birthday flow rejects '+damage,async()=>{
 const m=await mod(),f=flow()
 if(damage==='cents_bool')f.budget_cents=true
 if(damage==='version_float')f.version=1.5
 if(damage==='duplicate_node')f.nodes[6].idx=5
 if(damage==='wrong_type')f.nodes[4].type='transfer'
 if(damage==='fake_simulated')f.simulated=false
 if(damage==='secret')f.token_hash='not-public'
 if(damage==='extra_price')f.nodes[1].price_cents=30000
 if(damage==='duplicate_op')f.nodes[0].operation_id=f.nodes[4].operation_id=opid
 assert.throws(()=>m.parseBirthdayFlow(f))
})
test('strict client load uses only birthday reads and returns owned options',async()=>{
 const m=await mod(),b=bundle(),calls=[]
 const c=m.createBirthdayClient(async(p,body)=>{calls.push([p,body]);if(p==='/session')return {user_id:'demo'};if(p==='/birthday/flows')return {flows:b.flows,simulated:true,merchant_connected:false};if(p==='/birthday/catalog')return {items:b.items};if(p==='/birthday/options')return {accounts:b.accounts,business_date:b.business_date,simulated:true,merchant_connected:false};throw Error(p)})
 assert.equal(await c.session(),'demo');assert.deepEqual(await c.load(),b);assert(calls.every(([,body])=>body===undefined));assert(!calls.some(([p])=>p==='/state'))
})
test('create and command send only explicit fields and never confirm',async()=>{
 const m=await mod(),calls=[],f=flow()
 const c=m.createBirthdayClient(async(p,b)=>{calls.push([p,b]);return {flow:f,simulated:true,merchant_connected:false}})
 await c.create(intent(),'birthday-create-test')
 assert.deepEqual(calls[0],['/birthday/flows',{...intent(),request_key:'birthday-create-test'}])
 await c.command(f,'cake',{cake:false},'birthday-cake-test')
 assert.deepEqual(calls[1],['/birthday/flows/'+f.id+'/cake',{cake:false,expected_version:1,request_key:'birthday-cake-test'}])
 await assert.rejects(c.command(f,'cake',{cake:0},'birthday-bad-cake'))
 await assert.rejects(c.command(f,'advance',{status:'SUCCEEDED'},'birthday-bad-state'))
 assert.equal(calls.length,2);assert(calls.every(([p])=>!/confirm|verify/.test(p)))
})
test('client validates immutable returned intent and mismatched lookup identity',async()=>{
 const m=await mod(),f=flow();f.budget_cents=1
 const c=m.createBirthdayClient(async()=>({flow:f,simulated:true,merchant_connected:false}))
 await assert.rejects(c.create(intent(),'birthday-mismatch-test'))
})
test('fractional Bank epochs and receipt ISO parse without weakening binding',async()=>{
 const m=await mod(),f=linkedFlow(),op=budgetOp(),calls=[]
 const c=m.createBirthdayClient(async(p,b)=>{calls.push([p,b]);return {operation:op,read_only:true,simulated:true,recorded_status:op.status,observed_status:op.status,checked_at:'2026-10-28T12:00:00.123456+08:00'}})
 assert.equal(m.parseBirthdayOperation(op,f).created_at,op.created_at)
 assert.equal((await c.readOperation(op.id,f)).id,op.id)
 await assert.rejects(c.readOperation('op_unrelated',f));assert.equal(calls.length,1)
 op.payload.amount_cents=1;assert.throws(()=>m.parseBirthdayOperation(op,f))
})
test('late Bank success cannot promote a flow node already UNKNOWN',async()=>{
 const m=await mod(),f=linkedFlow(),op=budgetOp();f.nodes[0].state=f.nodes[0].observed_state='UNKNOWN';op.status='SUCCEEDED';op.result={budget_id:'bb_'+'b'.repeat(32),simulated:true,message:'模拟预留，未扣款'}
 const c=m.createBirthdayClient(async()=>({operation:op,read_only:true,simulated:true,recorded_status:'SUCCEEDED',observed_status:'SUCCEEDED',checked_at:'2026-10-28T12:00:01+08:00'}))
 assert.equal((await c.readOperation(op.id,f)).status,'UNKNOWN')
})
test('HTTP unknown is not relabelled as a definite rejection',async()=>{
 const m=await mod()
 for(const [status,code,definite] of [[503,'TOOL_UNAVAILABLE',false],[409,'KEY_CONFLICT',false],[409,'VERSION_CONFLICT',true],[403,'CSRF_REJECTED',true]]){
  const c=m.createBirthdayClient(async()=>{const e=Object.assign(Error('server'),{status,code});throw e})
  await assert.rejects(c.create(intent(),'birthday-error-test'),e=>Boolean(e.definiteBirthdayRejection)===definite)
 }
})
