import test from 'node:test'
import assert from 'node:assert/strict'
import { loadWorkspace } from './workspace.ts'

const fail=(status,code)=>Object.assign(new Error('controlled failure'),{status,code})
const state=()=>({name:'测试用户',accounts:[],operations:[],business_date:'2026-10-26',locked:false,simulated:true,planner_mode:'rules',safety_hold:null,safety_epoch:'',memory_count:0,tools:[]})
const bill={period:'2026-10',expense_cents:12300,refund_cents:0,income_cents:0,net_expense_cents:12300,previous_net_expense_cents:0,categories:[],transactions:[],evidence_ids:[]}
function reader(overrides={}){
  const calls=[]
  const routes={'/state':state,'/bill?period=2026-10':bill,'/audit':{events:[]},'/history':{messages:[]},'/memories?query=':{notes:[]},'/subscriptions':fail(403,'SUBSCRIPTION_CONSENT_REQUIRED'),...overrides}
  return {calls,read:async path=>{
    calls.push(path)
    assert.ok(path in routes,'Unexpected path '+path)
    const value=routes[path]
    if(value instanceof Error)throw value
    return typeof value==='function'?value():value
  }}
}

test('workspace loads independent data and distinguishes missing subscription consent',async()=>{
  const source=reader();const updates=[]
  const result=await loadWorkspace(source.read,'',s=>updates.push(s))
  assert.equal(result.bill.net_expense_cents,12300)
  assert.equal(result.subscriptions,null)
  assert.deepEqual(result.unavailableGroups,[])
  assert.equal(updates.length,1)
})

test('bill failure is missing data, refreshes health, and preserves history',async()=>{
  let states=0
  const source=reader({'/bill?period=2026-10':fail(503,'TOOL_PAUSED'),'/state':()=>({...state(),tools:states++?[{group:'bill',state:'PAUSED'}]:[]})})
  const result=await loadWorkspace(source.read,'')
  assert.equal(result.bill,null)
  assert.equal(result.state.tools[0].state,'PAUSED')
  assert.deepEqual(result.unavailableGroups,['bill'])
  assert.deepEqual(result.messages,[])
  assert.equal(source.calls.filter(p=>p==='/state').length,2)
})

test('history failure rejects synchronization instead of reporting an empty history',async()=>{
  const error=fail(503,'HISTORY_FAILED')
  const source=reader({'/history':error})
  await assert.rejects(loadWorkspace(source.read,''),e=>e===error)
})

test('failed recovery of a historical operation rejects synchronization',async()=>{
  const error=fail(503,'RECEIPT_FAILED')
  const message={id:1,role:'assistant',content:{message:'历史回执',cards:[{type:'operation',operation:{id:'op-old'}}],tasks:[]},created_at:''}
  const source=reader({'/history':{messages:[message]},'/operations/op-old':error})
  await assert.rejects(loadWorkspace(source.read,''),e=>e===error)
})

test('subscription technical outage differs from a permission request',async()=>{
  const source=reader({'/subscriptions':fail(503,'TOOL_PAUSED')})
  const result=await loadWorkspace(source.read,'')
  assert.deepEqual(result.unavailableGroups,['subscriptions'])
  assert.equal(result.subscriptions,null)
  assert.equal(result.bill.net_expense_cents,12300)
})

test('audit and notes errors are unavailable rather than authoritative empty results',async()=>{
  const source=reader({'/audit':fail(503,'AUDIT_FAILED'),'/memories?query=':fail(503,'NOTES_FAILED')})
  const result=await loadWorkspace(source.read,'')
  assert.equal(result.events,null)
  assert.equal(result.memories,null)
  assert.equal(result.warnings.length,2)
})


test('successful explicit read refreshes a previously paused health snapshot',async()=>{
  let reads=0
  const source=reader({'/state':()=>({...state(),tools:[{group:'bill',state:reads++?'READY':'PAUSED'}]})})
  const result=await loadWorkspace(source.read,'')
  assert.equal(result.bill.net_expense_cents,12300)
  assert.equal(result.state.tools[0].state,'READY')
  assert.equal(source.calls.filter(p=>p==='/state').length,2)
})
