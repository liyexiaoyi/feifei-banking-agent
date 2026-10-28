import test from 'node:test'
import assert from 'node:assert/strict'
import {HandoffProgressController} from './handoff-progress-controller.ts'

const target={plan_id:'plan-1',handoff_id:'local_case1'}
const open={...target,status:'OPEN',version:0,updated_at:1,simulated:true,contacted_human:false,operations:[],events:[],receipt:null,replayed:false}
const deferred=()=>{let resolve!:(v:unknown)=>void;const promise=new Promise(r=>resolve=r);return {promise,resolve}}
test('late read cannot overwrite current progress',async()=>{
 const slow=deferred();let reads=0
 const controller=new HandoffProgressController(target,async()=>++reads===1?slow.promise:open,()=>{})
 const pending=controller.refresh();await controller.refresh();
 slow.resolve({...open,version:99});await pending
 assert.equal(controller.state.view?.version,0);assert.equal(controller.state.error,'')
})
test('lost write response reconciles by GET and never auto-posts',async()=>{
 let posts=0,gets=0
 const controller=new HandoffProgressController(target,async(_path,body)=>{
  if(body){posts++;throw Error('lost response')}
  gets++;return open
 },()=>{},()=> 'request-key-1')
 await controller.refresh();await controller.update('IN_REVIEW')
 assert.equal(posts,1);assert.equal(gets,2);assert.equal(controller.state.view?.status,'OPEN')
 assert.match(controller.state.notice,/不会自动重试/)
})
test('failed reconciliation hides changes and concurrent clicks issue one request',async()=>{
 const pending=deferred();let posts=0,reads=0
 const controller=new HandoffProgressController(target,async(_path,body)=>{
  if(body){posts++;return pending.promise}
  if(++reads===1)return open
  throw Error('read unavailable')
 },()=>{},()=> 'request-key-1')
 await controller.refresh();const first=controller.update('IN_REVIEW');await controller.update('CLOSED')
 pending.resolve({});await first
 assert.equal(posts,1);assert.equal(controller.state.view,null);assert.match(controller.state.error,/核对/)
 await controller.update('CLOSED');assert.equal(posts,1)
})
test('disposed card cannot publish late state or send writes',async()=>{
 const pending=deferred();let changes=0
 const controller=new HandoffProgressController(target,async()=>pending.promise,()=>changes++)
 const first=controller.refresh();controller.dispose();const count=changes;pending.resolve(open);await first
 assert.equal(changes,count);await controller.update('CLOSED');assert.equal(changes,count)
})
