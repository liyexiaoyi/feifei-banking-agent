import test from 'node:test'
import assert from 'node:assert/strict'
import {LatestRefresh} from './latest-refresh.ts'

function deferred(){
  let resolve!:(value:string)=>void, reject!:(error:Error)=>void
  const promise=new Promise<string>((yes,no)=>{resolve=yes;reject=no})
  return {promise,resolve,reject}
}
function effects(events:string[]){return {
  start:()=>events.push('start'),progress:(value:string)=>events.push('progress:'+value),
  success:(value:string)=>events.push('success:'+value),failure:(error:unknown)=>events.push('failure:'+(error as Error).message),
  finish:()=>events.push('finish'),
}}
test('late progress, success and finally cannot overwrite a newer pending refresh',async()=>{
  const coordinator=new LatestRefresh(),events:string[]=[]
  const old=deferred(),fresh=deferred()
  let publishOld!:(value:string)=>void
  const first=coordinator.run<string,string>(publish=>{publishOld=publish;return old.promise},effects(events))
  const second=coordinator.run<string,string>(publish=>{publish('fresh');return fresh.promise},effects(events))
  publishOld('outdated')
  old.resolve('old workspace');await first
  assert.deepEqual(events,['start','start','progress:fresh'])
  fresh.resolve('fresh workspace');await second
  assert.deepEqual(events.slice(-2),['success:fresh workspace','finish'])
})
test('an outdated failure still rejects its caller but cannot degrade the latest successful state',async()=>{
  const coordinator=new LatestRefresh(),events:string[]=[]
  const old=deferred(),problem=new Error('old read failed')
  const first=coordinator.run<string,string>(()=>old.promise,effects(events))
  await coordinator.run<string,string>(async()=> 'latest receipt',effects(events))
  const failure=assert.rejects(first,error=>error===problem)
  old.reject(problem);await failure
  assert.deepEqual(events,['start','start','success:latest receipt','finish'])
})
test('current failure is disclosed and rethrown without automatic retry',async()=>{
  const coordinator=new LatestRefresh(),events:string[]=[]
  const problem=new Error('current read failed');let calls=0
  await assert.rejects(coordinator.run<string,string>(async()=>{calls++;throw problem},effects(events)),error=>error===problem)
  assert.equal(calls,1)
  assert.deepEqual(events,['start','failure:current read failed','finish'])
})
