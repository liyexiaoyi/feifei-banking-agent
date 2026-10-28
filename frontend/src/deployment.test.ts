import test from 'node:test'
import assert from 'node:assert/strict'
import { DeploymentController } from './deployment.ts'
import { createApiClient } from './api.ts'
import {retryWorkspaceSession} from './workspace.ts'

const meta={schema:1,backend_id:'a'.repeat(64),frontend_id:'b'.repeat(64),assets_id:'c'.repeat(64)}
const health=()=>({service:'ff-agent',status:'ok',deployment:{...meta,build_state:'ready'}})
const json=(data:unknown,status=200,owner?:string)=>new Response(JSON.stringify(data),{status,headers:owner?{'X-FF-Session-Owner':owner}:{}})

test('browser native fetch must not be called with the controller as receiver',async()=>{
 const calls:string[]=[]
 async function browserLikeFetch(this:unknown,url:RequestInfo|URL){
  if(this!==undefined&&this!==globalThis)throw new TypeError('Illegal invocation')
  calls.push(String(url))
  return json(String(url)==='/build-meta.json'?meta:health())
 }
 const gate=new DeploymentController(meta,browserLikeFetch)
 assert.equal(await gate.check(),true)
 assert.deepEqual(calls,['/build-meta.json','/api/health'])
 assert.equal(gate.state,'ready')
})

test('loaded bundle cannot adopt newer metadata; initialization makes zero business requests',async()=>{
  const calls:string[]=[]
  const fetcher=async(url:RequestInfo|URL)=>{calls.push(String(url));return json({...meta,frontend_id:'d'.repeat(64)})}
  const gate=new DeploymentController(meta,fetcher)
  const client=createApiClient(gate,fetcher)
  await assert.rejects(client.initialize(),/版本/)
  assert.deepEqual(calls,['/build-meta.json'])
  assert.equal(gate.state,'blocked')
})

test('legacy health, missing metadata, malformed payload, timeout and unknown schema fail closed',async()=>{
  for(const bad of [{service:'ff-agent'},null,[],{...health(),deployment:{...meta,schema:2,build_state:'ready'}}]){
    const calls:string[]=[]
    const fetcher=async(url:RequestInfo|URL)=>{calls.push(String(url));return json(String(url).includes('build-meta')?meta:bad)}
    const gate=new DeploymentController(meta,fetcher)
    await assert.rejects(createApiClient(gate,fetcher).initialize())
    assert.equal(calls.some(url=>/session|state|chat/.test(url)),false)
  }
  for(const fetcher of [async()=>json(null,404),async()=>{throw new Error('timeout')}]){
    const gate=new DeploymentController(meta,fetcher)
    assert.equal(await gate.check(),false)
  }
})

test('fresh check before writes; mismatch blocks send and manual check never replays it',async()=>{
  const calls:{url:string;init?:RequestInit}[]=[]
  let current=health()
  const fetcher=async(url:RequestInfo|URL,init?:RequestInit)=>{
    calls.push({url:String(url),init})
    if(url==='/build-meta.json')return json(meta)
    if(url==='/api/health')return json(current)
    return json({csrf:'token',user:'demo'},200,'demo')
  }
  const gate=new DeploymentController(meta,fetcher)
  const client=createApiClient(gate,fetcher)
  await client.initialize()
  current={...health(),deployment:{...meta,backend_id:'d'.repeat(64),build_state:'ready'}}
  await assert.rejects(client.api('/chat',{text:'transfer'}))
  assert.equal(gate.state,'blocked')
  assert.equal(calls.filter(c=>c.url==='/api/chat').length,0)
  current=health()
  const before=calls.length
  await assert.rejects(client.api('/state'))
  await assert.rejects(client.api('/chat',{text:'not explicitly unlocked'}))
  assert.equal(calls.length,before,'blocked state requires a manual compatibility check')
  assert.equal(await gate.check(),true)
  assert.equal(calls.filter(c=>c.url==='/api/chat').length,0)
  await client.api('/chat',{text:'explicit new action'})
  const write=calls.find(c=>c.url==='/api/chat')!
  assert.equal((write.init?.headers as Record<string,string>)['X-Build-Identity'],meta.backend_id+':'+meta.frontend_id)
  assert.equal(write.init?.redirect,'error','307/308 must never replay the POST through browser redirects')
})

test('server-side 409 locks UI; no automatic retry',async()=>{
  let posts=0
  const fetcher=async(url:RequestInfo|URL,init?:RequestInit)=>{
    if(url==='/build-meta.json')return json(meta)
    if(url==='/api/health')return json(health())
    if(init?.method==='POST'){posts++;return json({error:{code:'DEPLOYMENT_MISMATCH',message:'raw private detail'}},409)}
    return json({csrf:'token',user:'demo'},200,'demo')
  }
  const gate=new DeploymentController(meta,fetcher)
  const client=createApiClient(gate,fetcher)
  await client.initialize()
  await assert.rejects(client.api('/chat',{}),e=>e instanceof Error && !e.message.includes('raw private'))
  assert.equal(gate.state,'blocked')
  assert.equal(posts,1)
})

test('an older in-flight check cannot unlock a subsequent server rejection',async()=>{
  let resolveHealth!:(value:Response)=>void
  const fetcher=async(url:RequestInfo|URL)=>url==='/build-meta.json'?json(meta):await new Promise<Response>(resolve=>{resolveHealth=resolve})
  const gate=new DeploymentController(meta,fetcher)
  const check=gate.check()
  while(!resolveHealth)await new Promise(resolve=>setImmediate(resolve))
  gate.block()
  resolveHealth(json(health()))
  assert.equal(await check,false)
  assert.equal(gate.state,'blocked')
})

test('known expired identity is hidden rather than automatically reopened, preserving original caller marker',async()=>{
  let expired=false,sessionPosts=0
  const draft={text:'给妈妈转300元'}
  const fetcher=async(url:RequestInfo|URL,init?:RequestInit)=>{
    if(url==='/build-meta.json')return json(meta)
    if(url==='/api/health')return json(health())
    if(url==='/api/session' && init?.method==='POST'){sessionPosts++;expired=false;return json({csrf:'new',user:'demo'},200,'demo')}
    if(url==='/api/session')return expired?json({error:{code:'SESSION_REQUIRED'}},401):json({csrf:'old',user:'demo'},200,'demo')
    return json({name:'demo'})
  }
  const gate=new DeploymentController(meta,fetcher)
  const client=createApiClient(gate,fetcher)
  await client.initialize() // App already has a workspace snapshot.
  expired=true
  await assert.rejects(retryWorkspaceSession(client.initialize,async()=>{await client.api('/state')}),error=>(error as {code?:string}).code==='SESSION_CHANGED')
  assert.equal(sessionPosts,0)
  assert.equal(draft.text,'给妈妈转300元')
})
