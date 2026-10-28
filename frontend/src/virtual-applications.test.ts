import test from 'node:test'
import assert from 'node:assert/strict'
import {parseVirtualApplications,findVirtualIntent,mergeVirtualOperations} from './virtual-applications.ts'

const account={id:'account-one',name:'日常账户',masked_number:'DEMO 0826',balance_cents:10000,reserved_cents:0,available_cents:10000}
const operation={id:'operation-one',type:'virtual_card_create',risk:'YELLOW',status:'WAITING_CONFIRMATION',created_at:100,expires_at:400,payload:{account_id:account.id,account_name:account.name,card_name:'日常账户虚拟演示卡',simulated:true},result:null}

test('unknown application data is never turned into an authoritative empty list',()=>{
 for(const input of [undefined,null,{},'bad',[{...operation,risk:'GREEN'}],[{...operation,type:'transfer'}],
  [{...operation,payload:{...operation.payload,simulated:false}}],[operation,operation]])assert.equal(parseVirtualApplications(input),null)
 assert.deepEqual(parseVirtualApplications([]),[])
})

test('only owned-account request data and public operation fields are preserved',()=>{
 const result=parseVirtualApplications([{...operation,private_extra:'drop',payload:{...operation.payload,confirmed:true,private_extra:'drop'}}])!
 assert.equal(result.length,1)
 assert.equal(findVirtualIntent(result,account)?.id,operation.id)
 assert.doesNotMatch(JSON.stringify(result),/private_extra|confirmed/)
 assert.equal(findVirtualIntent(result,{...account,id:'other-account'}),null)
})

test('unknown and successful original applications block new preparation until explicitly reviewed',()=>{
 for(const status of ['UNKNOWN','EXECUTING','MANUAL_REVIEW','SUCCEEDED','FUTURE_STATE']){
  const result=parseVirtualApplications([{...operation,status}])!
  assert.equal(findVirtualIntent(result,account)?.id,operation.id)
 }
 for(const status of ['CANCELLED','EXPIRED','FAILED'])assert.equal(findVirtualIntent(parseVirtualApplications([{...operation,status}]),account),null)
})

 test('current application records override stale snapshots and retain older unresolved applications',()=>{
 const old={...operation,status:'UNKNOWN'}
 const current={...operation,status:'SUCCEEDED'}
 const older={...old,id:'older-operation'}
 const transfer={...operation,id:'transfer',type:'transfer',risk:'RED'}
 const merged=mergeVirtualOperations([old,transfer],[current,older])
 assert.deepEqual(merged.map(op=>[op.id,op.status]),[[operation.id,'SUCCEEDED'],['transfer','WAITING_CONFIRMATION'],['older-operation','UNKNOWN']])
 assert.deepEqual(mergeVirtualOperations([old],null),[old])
 })
