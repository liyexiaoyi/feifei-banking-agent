import test from 'node:test'
import assert from 'node:assert/strict'
const m=await import('./investment-profile.ts').catch(()=>null)
const ids=['loss_capacity','liquidity','horizon','experience','income_stability','attitude']
const answers=Object.fromEntries(ids.map(id=>[id,3]))
const view={status:'VALID',simulated:true,replayed:false,receipt:null,profile:{version:1,survey_version:'demo-risk-v1',answers,score:18,level:5,
 contributions:ids.map(id=>({id,index:3,points:3})),caps:[],created_at:1,expires_at:15552001}}
const survey={survey_version:'demo-risk-v1',validity_days:180,simulated:true,disclaimer:'队伍自定义演示，不是官方测评。',
 questions:ids.map(id=>({id,text:'请选择本题答案',choices:[0,1,2,3].map(index=>({index,label:'选项'+index,points:index}))}))}
const assessment={status:'VALID',simulated:true,profile:{version:1,level:5,survey_version:'demo-risk-v1',expires_at:15552001}}
const compared={assessment,policy_version:'demo-products-v1',policy_fingerprint:'a'.repeat(64),simulated:true,disclaimer:'仅演示，比较不创建申请。',
 products:[{id:'demo_cash',name:'演示产品甲',nav_cents:100,quote_version:1,risk_level:1,minimum_cents:100,recommended_holding_days:1,eligible:true,reason:'演示级别匹配',simulated:true}]}
const clone=value=>JSON.parse(JSON.stringify(value))
const module=()=>{assert.ok(m,'investment-profile.ts implementation required');return m}

test('load reads questionnaire, own view and comparison without creating a transaction',async()=>{
 const calls=[]
 const c=module().createInvestmentClient(async(path,body)=>{calls.push({path,body});return clone(path.endsWith('questionnaire')?survey:path==='/wealth/compare'?compared:view)})
 const result=await c.load();assert.equal(result.view.profile.level,5)
 assert.deepEqual(calls.map(x=>x.path).sort(),['/investment-profile','/investment-profile/questionnaire','/wealth/compare'].sort())
 assert(calls.every(x=>x.body===undefined))
 assert(!('answer' in result.survey.questions[0]))
})

test('unchecked or incomplete assessment sends nothing',async()=>{
 let count=0;const c=module().createInvestmentClient(async()=>{count++;return view})
 await assert.rejects(async()=>c.submit(answers,'demo-risk-v1',0,'profile-client-key',false))
 await assert.rejects(async()=>c.submit({experience:3},'demo-risk-v1',0,'profile-client-key',true))
 assert.equal(count,0)
})

test('submission binds explicit answers, version, consent and caller key without retry',async()=>{
 const calls=[];const c=module().createInvestmentClient(async(path,body)=>{calls.push({path,body});throw new Error('response unknown')})
 await assert.rejects(()=>c.submit(answers,'demo-risk-v1',0,'profile-client-key',true))
 assert.deepEqual(calls,[{path:'/investment-profile',body:{answers,survey_version:'demo-risk-v1',expected_version:0,request_key:'profile-client-key',confirmed:true}}])
})

test('malformed profiles cannot become a valid risk result',()=>{
 const parse=module().parseProfileView
 assert.equal(parse(clone(view)).profile.level,5)
 for(const change of [x=>x.profile.level=0,x=>x.profile.answers.horizon=true,x=>x.profile.version=true,x=>x.status='GUESSED',x=>x.profile.expires_at=0]){
  const value=clone(view);change(value);assert.throws(()=>parse(value))
 }
 assert.equal(parse({status:'NONE',profile:null,simulated:true,replayed:false,receipt:null}).profile,null)
})

test('comparison cannot invent eligibility without a valid personal assessment',()=>{
 const parse=module().parseComparison
 assert.equal(parse(clone(compared)).products[0].eligible,true)
 const unknown=clone(compared);unknown.assessment={status:'NONE',profile:null,simulated:true};assert.throws(()=>parse(unknown))
 unknown.products[0].eligible=null;assert.equal(parse(unknown).products[0].eligible,null)
 const bad=clone(compared);bad.products[0].minimum_cents=1.5;assert.throws(()=>parse(bad))
})

test('questionnaire is complete and client load rejects mixed profile versions',async()=>{
 const parse=module().parseQuestionnaire
 assert.equal(parse(clone(survey)).questions.length,6)
 const bad=clone(survey);bad.questions.pop();assert.throws(()=>parse(bad))
 const mismatch=clone(compared);mismatch.assessment.profile.version=2
 const c=module().createInvestmentClient(async path=>clone(path.endsWith('questionnaire')?survey:path==='/wealth/compare'?mismatch:view))
 await assert.rejects(()=>c.load())
})

test('replay receipts cannot contradict the displayed current profile',()=>{
 const parse=module().parseProfileView
 for(const change of [x=>{x.replayed=true;x.receipt=null},x=>{x.status='NONE';x.profile=null;x.receipt={submitted_version:1}},x=>{x.replayed=true;x.receipt={submitted_version:2}},x=>{x.replayed=false;x.receipt={submitted_version:1}}]){
  const value=clone(view);change(value);assert.throws(()=>parse(value))
 }
 const valid=clone(view);valid.replayed=true;valid.receipt={submitted_version:1}
 assert.equal(parse(valid).receipt.submitted_version,1)
})
