import test from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {loadComponent,elements,textContent} from './component-test-loader.mjs'
const loaded=await loadComponent('InvestmentProfilePanel.tsx').catch(()=>null)
const Panel=loaded?.default
const ids=['loss_capacity','liquidity','horizon','experience','income_stability','attitude']
const survey={survey_version:'demo-risk-v1',validity_days:180,simulated:true,disclaimer:'队伍自定义演示，不是官方测评。',questions:ids.map((id,i)=>({id,text:'问题'+(i+1),choices:[0,1,2,3].map(index=>({index,label:'选项'+index,points:index}))}))}
const none={status:'NONE',profile:null,simulated:true,replayed:false,receipt:null}
const comparison={products:[{id:'demo_cash',name:'演示产品甲',nav_cents:100,quote_version:1,risk_level:1,minimum_cents:100,recommended_holding_days:1,eligible:null,reason:'先完成演示测评',simulated:true}],assessment:{status:'NONE',profile:null,simulated:true},policy_version:'demo-products-v1',policy_fingerprint:'a'.repeat(64),simulated:true,disclaimer:'仅比较，不创建交易。'}
const props={survey,view:none,comparison,loading:false,busy:false,blocked:false,error:'',notice:'',active:true,step:0,answers:{},consent:false,inputId:'question-unit',onStart(){},onCancel(){},onAnswer(){},onBack(){},onNext(){},onConsent(){},onSubmit(){},onRefresh(){}}
function tree(changes={}){assert.ok(Panel,'InvestmentProfilePanel.tsx is not implemented');return Panel({...props,...changes})}
function submitButton(t){const result=elements(t,'button').find(b=>/提交.*测评/.test(textContent(b)));assert.ok(result,'explicit assessment submit button');return result}

test('one question at a time starts without a selected answer',()=>{
 const t=tree();assert.equal(elements(t,'select').length,1)
 assert.equal(elements(t,'select')[0].props.value,'')
 assert.match(textContent(t),/问题1/);assert.doesNotMatch(textContent(t),/问题2/)
 assert.equal(elements(t,'input').filter(i=>i.props.type==='checkbox').some(i=>i.props.checked),false)
})

test('all six answers and separate explicit consent are required',()=>{
 const all=Object.fromEntries(ids.map(id=>[id,0]))
 const partial={...all};delete partial.attitude
 assert.equal(submitButton(tree({step:5,answers:partial,consent:true})).props.disabled,true)
 assert.equal(submitButton(tree({step:5,answers:all})).props.disabled,true)
 assert.equal(submitButton(tree({step:5,answers:all,consent:true})).props.disabled,false)
 for(const flags of [{busy:true},{blocked:true}])assert.equal(submitButton(tree({step:5,answers:all,consent:true,...flags})).props.disabled,true)
})

test('blank choice remains missing and zero is an explicit valid answer',()=>{
 const seen=[];const t=tree({onAnswer:(id,value)=>seen.push([id,value])})
 const choice=elements(t,'select')[0]
 choice.props.onChange({target:{value:''}});choice.props.onChange({target:{value:'0'}})
 assert.deepEqual(seen,[[ids[0],null],[ids[0],0]])
})

test('failed reads hide stale private results and product details',()=>{
 const t=tree({active:false,error:'读取失败，请先刷新。'})
 assert.match(textContent(t),/读取失败/)
 assert.doesNotMatch(textContent(t),/演示产品甲/)
 assert(elements(t,'button').some(b=>/刷新|核对/.test(textContent(b))))
})

test('comparison cannot trigger financial callbacks and uses plain demo language',()=>{
 let writes=0;const t=tree({active:false,onSubmit:()=>writes++})
 assert.equal(writes,0)
 assert.match(textContent(t),/演示产品甲/)
 assert(!elements(t,'button').some(b=>/申购|赎回|购买/.test(textContent(b))))
 assert.doesNotMatch(textContent(t),/MFA|CAS|demo-risk-v1/)
})

test('server cap reasons are explained without rendering arbitrary cap objects',()=>{
 const profile={version:1,survey_version:'demo-risk-v1',answers:Object.fromEntries(ids.map(id=>[id,0])),score:0,level:1,contributions:[],caps:[{id:'loss_capacity',level:1,reason:'不能承受亏损，演示级别上限为1级'},{internal:'hidden'}],created_at:1,expires_at:100}
 const t=tree({active:false,view:{...none,status:'VALID',profile}})
 assert.match(textContent(t),/不能承受亏损/)
 assert.doesNotMatch(textContent(t),/hidden|loss_capacity|\[object Object\]/)
})

test('a failed read retains the submission uncertainty notice but hides stale products',()=>{
 const t=tree({active:false,error:'读取失败',notice:'提交可能已完成，请先核对。'})
 assert.match(textContent(t),/读取失败/)
 assert.match(textContent(t),/提交可能已完成/)
 assert.doesNotMatch(textContent(t),/演示产品甲/)
})
