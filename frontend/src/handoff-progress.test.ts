import test from 'node:test'
import assert from 'node:assert/strict'
import {parseHandoffProgress} from './handoff-progress.ts'

const target={plan_id:'plan-1',handoff_id:'local_case1'}
const event={id:'event-1',from_status:'OPEN',to_status:'CLOSED',from_version:0,to_version:1,created_at:10}
const view={...target,status:'CLOSED',version:1,updated_at:10,simulated:true,contacted_human:false,operations:[{id:'op-1',type:'transfer',status:'UNKNOWN'}],events:[event],receipt:null,replayed:false}

test('local progress validates target and keeps unknown independent of closure',()=>{
  assert.equal(parseHandoffProgress(view,target)?.operations[0].status,'UNKNOWN')
  assert.equal(parseHandoffProgress({...view,plan_id:'other'},target),null)
  assert.equal(parseHandoffProgress({...view,handoff_id:'other'},target),null)
})
test('progress rejects invalid flags, counters and financial states',()=>{
  for(const bad of [{contacted_human:true},{simulated:false},{version:true},{version:-1},{updated_at:NaN},{status:'SUCCEEDED'},{operations:[{id:'op-1',type:'transfer',status:'TRUST_ME'}]},{events:[]},{version:2}])
    assert.equal(parseHandoffProgress({...view,...bad},target),null)
})
test('progress validates ordered history and binds replay receipt to actual event',()=>{
  const receipt={event_id:event.id,...Object.fromEntries(Object.entries(event).filter(([key])=>key!=='id'))}
  assert.ok(parseHandoffProgress({...view,receipt,replayed:true},target))
  assert.equal(parseHandoffProgress({...view,receipt:{...receipt,event_id:'forged'},replayed:true},target),null)
  assert.equal(parseHandoffProgress({...view,replayed:true},target),null)
  assert.equal(parseHandoffProgress({...view,events:[{...event,from_status:'CLOSED'}]},target),null)
})
test('virtual OPEN is valid only at zero with no invented history',()=>{
  assert.ok(parseHandoffProgress({...view,status:'OPEN',version:0,events:[]},target))
  assert.equal(parseHandoffProgress({...view,status:'OPEN',version:0},target),null)
})
test('actual card loss and restore operation types can be tracked after handoff',()=>{
 for(const type of ['card_report_lost','card_restore'])
  assert.equal(parseHandoffProgress({...view,operations:[{id:'op-card',type,status:'CANCELLED'}]},target)?.operations[0].type,type)
 assert.equal(parseHandoffProgress({...view,operations:[{id:'op-card',type:'card_status',status:'CANCELLED'}]},target),null)
})
