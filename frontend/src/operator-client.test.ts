import test from 'node:test'
import assert from 'node:assert/strict'

const module = await import('./operator-client.ts').catch(() => null)
const clone = value => JSON.parse(JSON.stringify(value))
const original = {id:'case_demo',plan_id:'plan_demo',handoff_id:'local_demo',status:'QUEUED',version:1,
 simulated:true,created_at:1,updated_at:1,reply:'',replayed:false,
 snapshot:{plan_id:'plan_demo',plan_version:3,observed_at:1,operations:[{id:'op_demo',type:'transfer',status:'CANCELLED',amount_cents:30000,payee_name:'妈妈',payee_phone:'138****0001'}]},
 current_facts:{plan_id:'plan_demo',plan_version:3,observed_at:2,operations:[{id:'op_demo',type:'transfer',status:'UNKNOWN'}]}}
const gate = () => ({async check(){return true},async requireReady(){return 'bound-build-identity'},block(){}})
const json = (body,status=200) => new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}})
const present = () => {assert.ok(module,'operator-client.ts must implement the independent clients');return module}

test('operator initialization only reads its session and never opens a demo bank session',async()=>{
 const calls=[]
 const client=present().createOperatorClient(gate(),async(path,options)=>{calls.push({path,options});return json({error:{code:'OPERATOR_SESSION_REQUIRED'}},401)})
 assert.equal(await client.initialize(),false)
 assert.deepEqual(calls.map(x=>[x.path,x.options?.method||'GET']),[['/api/operator/session','GET']])
})

test('operator writes use independent CSRF, explicit key, build identity and no automatic retry',async()=>{
 const calls=[]
 const client=present().createOperatorClient(gate(),async(path,options={})=>{
  calls.push({path,options})
  if(path==='/api/operator/session')return json({csrf:'operator-csrf',operator_id:'local-demo-operator',expires_at:3000,simulated:true})
  throw new TypeError('lost response')
 })
 await client.login('input-credential')
 await assert.rejects(()=>client.claim('case_demo',1,'same-request-key'))
 assert.equal(calls.length,2)
 const write=calls[1];assert.equal(write.path,'/api/operator/cases/case_demo/claim')
 const headers=new Headers(write.options.headers)
 assert.equal(headers.get('X-CSRF-Token'),'operator-csrf');assert.equal(headers.get('X-Build-Identity'),'bound-build-identity')
 assert.deepEqual(JSON.parse(write.options.body),{expected_version:1,request_key:'same-request-key'})
 assert.equal(write.options.credentials,'same-origin')
 await assert.rejects(()=>client.claim('case_demo',1,'same-request-key'))
 assert.equal(calls.length,3);assert.equal(calls[2].options.body,write.options.body)
})

test('operator logout uses DELETE, and authorization errors never create a user session',async()=>{
 const calls=[]
 const client=present().createOperatorClient(gate(),async(path,options={})=>{
  calls.push({path,options})
  if(options.method==='DELETE')return json({revoked:true,simulated:true})
  if(path==='/api/operator/session')return json({csrf:'operator-csrf',operator_id:'local-demo-operator',expires_at:3000,simulated:true})
  return json({error:{code:'OPERATOR_SESSION_REQUIRED',message:'请登录演示坐席。'}},401)
 })
 await client.login('input-credential');await assert.rejects(()=>client.list());await client.logout()
 assert.equal(calls.at(-1).options.method,'DELETE')
 assert(!calls.some(x=>x.path==='/api/session'))
})

test('case parsing preserves pending bank facts and rejects unmasked or expanded data',()=>{
 const parse=present().parseOperatorCase
 assert.equal(parse(clone(original)).current_facts.operations[0].status,'UNKNOWN')
 const fullPhone=clone(original);fullPhone.snapshot.operations[0].payee_phone='13800000001';assert.throws(()=>parse(fullPhone))
 const expanded=clone(original);expanded.current_facts.operations.push({id:'op_unshared',type:'transfer',status:'SUCCEEDED'});assert.throws(()=>parse(expanded))
 const wrongPlan=clone(original);wrongPlan.snapshot.plan_id='plan_other';assert.throws(()=>parse(wrongPlan))
 const invalidAmount=clone(original);invalidAmount.snapshot.operations[0].amount_cents=1.5;assert.throws(()=>parse(invalidAmount))
 const cancelled=clone(original);cancelled.status='CANCELLED';cancelled.version=2;assert.throws(()=>parse(cancelled))
 cancelled.snapshot=null;cancelled.current_facts=null;assert.equal(parse(cancelled).status,'CANCELLED')
})

test('user lookup is read-only and unchecked consent sends no request',async()=>{
 const calls=[]
 const client=present().createUserCaseClient(async(path,body)=>{calls.push({path,body});return {case:null}})
 assert.equal(await client.lookup('plan_demo'),null)
 assert.deepEqual(calls,[{path:'/plans/plan_demo/operator-case',body:undefined}])
 await assert.rejects(async()=>client.share('plan_demo',3,false,'share-request-key'))
 assert.equal(calls.length,1)
})

test('user share binds plan version, withdrawal binds case version and original keys are kept',async()=>{
 const calls=[]
 const client=present().createUserCaseClient(async(path,body)=>{calls.push({path,body});return clone(original)})
 await client.share('plan_demo',3,true,'share-request-key')
 await client.withdraw('case_demo',1,'withdraw-request-key')
 assert.deepEqual(calls,[{path:'/plans/plan_demo/operator-case',body:{expected_version:3,confirmed:true,request_key:'share-request-key'}},
  {path:'/operator-cases/case_demo/withdraw',body:{expected_version:1,request_key:'withdraw-request-key'}}])
})


test('share preview reads only the exact plan and validates minimal facts',async()=>{
 const calls=[]
 const client=present().createUserCaseClient(async(path,body)=>{calls.push({path,body});return {preview:clone(original.snapshot)}})
 const result=await client.preview('plan_demo')
 assert.equal(result.plan_id,'plan_demo')
 assert.deepEqual(calls,[{path:'/plans/plan_demo/operator-case/preview',body:undefined}])
})
