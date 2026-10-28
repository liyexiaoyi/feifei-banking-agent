import test from 'node:test'
import assert from 'node:assert/strict'
const module=await import('./card-control-client.ts').catch(()=>null)
const card={id:'card_a_daily',account_id:'a_daily',name:'日常演示卡',masked_number:'DEMO ···· 0826',status:'ACTIVE',version:1,simulated:true}
const controls={card_id:card.id,locked:false,single_limit_cents:2000000,daily_limit_cents:5000000,control_version:1,spent_today_cents:0,simulated:true}
const credential={card_id:card.id,configured:false,credential_version:0,simulated:true,bank_connected:false}
const merchants={merchants:[{id:'demo_market',name:'演示超市',simulated:true,merchant_connected:false}],simulated:true}
const raw=()=>({cards:{cards:[{...card}],simulated:true},controls:{...controls},credential:{...credential},merchants:structuredClone(merchants)})
const basePayload={card_id:card.id,account_id:card.account_id,card_name:card.name,masked_number:card.masked_number,storage_kind:'physical',expected_card_version:1,expected_control_version:1,merchant_id:'demo_market',merchant_name:'演示超市',amount_cents:123,risk_epoch:'',simulated:true}
function op(type='card_spend',changes:Record<string,unknown>={}){return {id:'op_card_first',type,status:'WAITING_CONFIRMATION',risk:type==='card_spend'?'YELLOW':'RED',created_at:1,expires_at:301,payload:{...basePayload},result:null,...changes}}
function apiModule(){assert.ok(module,'card-control-client.ts must exist');return module}

test('bundle validates owned identity/version and strips unrelated private response fields',()=>{
 const m=apiModule(),value=raw();(value.cards.cards[0] as any).user_id='private';
 const b=m.parseCardBundle(value,card.id)
 assert.equal(b.card.id,card.id);assert.equal(b.credential.credential_version,0);assert.equal(b.controls.control_version,1)
 assert(!('user_id' in b.card));assert.notStrictEqual(b.controls,value.controls)
})
test('bundle fails closed on stale/foreign identity, bad cents and inconsistent credential flag',()=>{
 const m=apiModule()
 const variants=[(x:any)=>x.controls.card_id='foreign',(x:any)=>x.credential.card_id='foreign',(x:any)=>x.cards.cards[0].version=true,(x:any)=>x.controls.single_limit_cents=1.2,(x:any)=>x.controls.single_limit_cents=5000001,(x:any)=>x.controls.locked=1,(x:any)=>x.credential.configured=true,(x:any)=>x.credential.bank_connected=true,(x:any)=>x.merchants.merchants[0].merchant_connected=true]
 for(const edit of variants){const x=raw();edit(x);assert.throws(()=>m.parseCardBundle(x,card.id))}
})
test('operation parser binds card/type/strict cents and rejects unconfirmed fake result',()=>{
 const m=apiModule();assert.equal(m.parseCardOperation(op(),card.id,'card_spend').payload.amount_cents,123)
 for(const value of [op('card_spend',{payload:{...basePayload,card_id:'foreign'}}),op('transfer'),op('card_spend',{payload:{...basePayload,amount_cents:true}}),op('card_spend',{status:'PENDING'}),op('card_spend',{status:'SUCCEEDED',result:null})])assert.throws(()=>m.parseCardOperation(value,card.id,'card_spend'))
})
test('credential operation is fixed red and contains public challenge reference only',()=>{
 const m=apiModule(),payload={card_id:card.id,account_id:card.account_id,card_name:card.name,masked_number:card.masked_number,storage_kind:'physical',expected_card_version:1,expected_credential_version:0,challenge_id:'cc_opaque',risk_epoch:'',simulated:true}
 const good=op('card_credential_change',{payload});assert.equal(m.parseCardOperation(good,card.id,'card_credential_change').risk,'RED')
 for(const value of [{...good,risk:'YELLOW'},{...good,payload:{...payload,new_secret:'NeverPersist_demo-secret'}},{...good,payload:{...payload,salt:'private'}},{...good,payload:{...payload,expected_credential_version:true}}])assert.throws(()=>m.parseCardOperation(value,card.id,'card_credential_change'))
})
test('load performs only reads and returns a fresh validated selected-card bundle',async()=>{
 const m=apiModule(),calls:{path:string;body:unknown}[]=[],r=raw()
 const client=m.createCardControlClient(async(path:string,body?:unknown)=>{calls.push({path,body});return ({'/cards':r.cards,['/cards/'+card.id+'/controls']:r.controls,['/cards/'+card.id+'/credential']:r.credential,'/card-spend/merchants':r.merchants} as Record<string,unknown>)[path]})
 assert.equal((await client.load(card.id)).card.id,card.id);assert.equal(calls.length,4);assert(calls.every(x=>x.body===undefined))
})
test('prepare spend uses selected versions, exact cents and key without confirmation',async()=>{
 const m=apiModule(),calls:any[]=[];const client=m.createCardControlClient(async(path:string,body?:unknown)=>{calls.push({path,body});return op()})
 await client.prepare(m.parseCardBundle(raw(),card.id),{kind:'spend',merchant_id:'demo_market',amount_cents:123},'pc-card-spend-first')
 assert.deepEqual(calls,[{path:'/cards/'+card.id+'/spend',body:{merchant_id:'demo_market',amount_cents:123,expected_card_version:1,expected_control_version:1,request_key:'pc-card-spend-first'}}])
 assert(!calls.some(x=>/confirm|verify/.test(x.path)))
})
test('prepare rejects amount or response drift before it can bind a wrong user decision',async()=>{
 const m=apiModule(),calls:any[]=[];const client=m.createCardControlClient(async(path:string,body?:unknown)=>{calls.push({path,body});return op('card_spend',{payload:{...basePayload,amount_cents:999}})})
 const b=m.parseCardBundle(raw(),card.id)
 await assert.rejects(client.prepare(b,{kind:'spend',merchant_id:'demo_market',amount_cents:1.2},'pc-invalid-cents'))
 assert.equal(calls.length,0)
 await assert.rejects(client.prepare(b,{kind:'spend',merchant_id:'demo_market',amount_cents:123},'pc-response-drift'))
 assert.equal(calls.length,1)
})
test('lookup recovers exact request using GET only and never retries POST on failure',async()=>{
 const m=apiModule(),calls:any[]=[];const client=m.createCardControlClient(async(path:string,body?:unknown)=>{calls.push({path,body});return {operation:op(),recorded_status:'WAITING_CONFIRMATION',observed_status:'WAITING_CONFIRMATION',read_only:true,simulated:true}})
 assert.equal((await client.lookup(card.id,'pc-card-spend-first','card_spend')).operation.id,'op_card_first')
 assert.deepEqual(calls,[{path:'/cards/'+card.id+'/requests/pc-card-spend-first',body:undefined}])
 const failed=m.createCardControlClient(async(path:string,body?:unknown)=>{calls.push({path,body});throw new Error('404 lookup unknown')})
 await assert.rejects(failed.lookup(card.id,'pc-card-spend-first','card_spend'))
 assert(calls.every(x=>x.body===undefined))
})
test('opaque pending markers reject extra secret/body/CSRF fields',()=>{
 const m=apiModule(),good={card_id:card.id,request_key:'pc-card-spend-first',type:'card_spend'}
 assert.deepEqual(m.parsePendingCardRequest(good),good)
 for(const extra of [{body:{new_secret:'private'}},{csrf:'private'},{new_secret:'private'},{type:'transfer'},{request_key:'invalid key'}])assert.throws(()=>m.parsePendingCardRequest({...good,...extra}))
})

test('real Bank view metadata is allowed but kept out of the sanitized operation',()=>{
 const m=apiModule(),actual=op('card_spend',{confirmed_at:null,executed_day:null})
 const parsed=m.parseCardOperation(actual,card.id,'card_spend')
 assert.equal(parsed.id,actual.id);assert(!('confirmed_at' in parsed))
})
test('real successful card controls result uses flat fields rather than an invented after wrapper',()=>{
 const m=apiModule(),before={locked:false,single_limit_cents:2000000,daily_limit_cents:5000000},after={...before,locked:true}
 const payload={card_id:card.id,account_id:card.account_id,card_name:card.name,masked_number:card.masked_number,storage_kind:'physical',expected_control_version:1,action:'lock',before,after,simulated:true}
 const actual=op('card_controls_update',{status:'SUCCEEDED',confirmed_at:2,executed_day:'2026-10-28',payload,result:{card_id:card.id,...after,control_version:2,simulated:true}})
 assert.equal(m.parseCardOperation(actual,card.id,'card_controls_update').result?.control_version,2)
})
test('real lookup includes checked_at and observed expiry while original state remains pending',async()=>{
 const m=apiModule(),receipt={operation:op('card_spend',{confirmed_at:null,executed_day:null}),recorded_status:'WAITING_CONFIRMATION',observed_status:'EXPIRED',read_only:true,simulated:true,checked_at:'2026-10-28T12:06:00+08:00'}
 const c=m.createCardControlClient(async()=>receipt)
 const result=await c.lookup(card.id,'pc-real-lookup-expired','card_spend')
 assert.equal(result.operation.status,'WAITING_CONFIRMATION');assert.equal(result.observed_status,'EXPIRED')
 for(const bad of [{...receipt,recorded_status:'SUCCEEDED'},{...receipt,observed_status:'SUCCEEDED'}])await assert.rejects(m.createCardControlClient(async()=>bad).lookup(card.id,'pc-bad-lookup-binding','card_spend'))
})
test('punctuation keys cannot pass local validation and get sent to the API',async()=>{
 const m=apiModule(),calls:any[]=[],c=m.createCardControlClient(async(...args:any[])=>{calls.push(args);return op()})
 for(const key of ['card+plus-key','card.with.dot']){
  assert.throws(()=>m.parsePendingCardRequest({card_id:card.id,request_key:key,type:'card_spend'}))
  await assert.rejects(c.prepare(m.parseCardBundle(raw(),card.id),{kind:'spend',merchant_id:'demo_market',amount_cents:123},key))
 }
 assert.equal(calls.length,0)
})

test('control prepare body uses the real API expected_version key',async()=>{
 const m=apiModule(),calls:any[]=[],before={locked:false,single_limit_cents:2000000,daily_limit_cents:5000000},after={...before,locked:true}
 const actual=op('card_controls_update',{payload:{card_id:card.id,account_id:card.account_id,card_name:card.name,masked_number:card.masked_number,storage_kind:'physical',expected_control_version:1,action:'lock',before,after,simulated:true}})
 const c=m.createCardControlClient(async(path:string,body?:unknown)=>{calls.push({path,body});return actual})
 await c.prepare(m.parseCardBundle(raw(),card.id),{kind:'control',action:'lock',value:true},'pc-control-real-body')
 assert.deepEqual(calls,[{path:'/cards/'+card.id+'/controls',body:{action:'lock',value:true,expected_version:1,request_key:'pc-control-real-body'}}])
})

test('only authoritative pre-create business rejection is distinguished from transport or duplicate-key uncertainty',async()=>{
 const m=apiModule(),r=raw();r.credential.configured=true;r.credential.credential_version=1
 const bundle=m.parseCardBundle(r,card.id),intent={kind:'credential',new_secret:'OnlyDemo_new-secret-123',current_secret:'OnlyDemo_old-secret-123'}
 for(const [code,status,definite] of [['INVALID_VERIFICATION',403,true],['SENSITIVE_LOCKED',423,true],['REQUEST_ALREADY_EXISTS',409,false],['IDEMPOTENCY_CONFLICT',409,false],['TOOL_UNAVAILABLE',503,false],['UNKNOWN',524,false]] as const){
  const client=m.createCardControlClient(async()=>{throw Object.assign(new Error(intent.new_secret),{code,status})})
  let error:any;try{await client.prepare(bundle,intent,'pc-rejection-classification')}catch(e){error=e}
  assert.ok(error);assert.equal(error.definiteCardPrepareRejection===true,definite);assert(!String(error).includes(intent.new_secret))
 }
})
