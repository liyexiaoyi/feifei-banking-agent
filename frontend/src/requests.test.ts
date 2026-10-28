import test from 'node:test'
import assert from 'node:assert/strict'
import { RequestCoordinator } from './requests.ts'

test('a committed request with a failed refresh retries the same key',async()=>{
  const requests=new RequestCoordinator()
  const keys:string[]=[]
  const send=async(key:string)=>{keys.push(key);return {id:'op_1'}}
  await assert.rejects(requests.run('transfer',send,async()=>{throw Error('refresh lost')}))
  const result=await requests.run('transfer',send,async()=>{})
  assert.equal(result.id,'op_1')
  assert.equal(keys.length,2)
  assert.equal(keys[0],keys[1])
})

test('a newly intended request after success gets a new key',async()=>{
  const requests=new RequestCoordinator()
  const keys:string[]=[]
  const send=async(key:string)=>{keys.push(key);return key}
  await requests.run('same text',send,async()=>{})
  await requests.run('same text',send,async()=>{})
  assert.notEqual(keys[0],keys[1])
})

test('two simultaneous clicks share one network request',async()=>{
  const requests=new RequestCoordinator()
  let calls=0
  let release:()=>void=()=>{}
  const waiting=new Promise<void>(resolve=>{release=resolve})
  const send=async()=>{calls++;await waiting;return 'same-operation'}
  const first=requests.run('same selection',send,async()=>{})
  const second=requests.run('same selection',send,async()=>{})
  release()
  assert.deepEqual(await Promise.all([first,second]),['same-operation','same-operation'])
  assert.equal(calls,1)
})

test('a restored selection uses its original durable key',async()=>{
  const keys:string[]=[]
  for(let i=0;i<2;i++){
    const requests=new RequestCoordinator()
    await requests.run('selection',async key=>{keys.push(key);return key},async()=>{},'selection-original')
  }
  assert.deepEqual(keys,['selection-original','selection-original'])
})
