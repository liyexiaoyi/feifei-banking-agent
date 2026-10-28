import test from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { loadComponent,elements,textContent } from './component-test-loader.mjs'

const plan=(status='PAUSED',nodeState='FAILED')=>({id:'plan-one',request_key:'fixed',risk_epoch:'opaque-not-for-display',version:3,status,reason:'TOOL_FAILURE',nodes:[{idx:0,kind:'transfer',deps:[],state:nodeState,error_code:null}]})

test('plan prepared text cannot imply payment and hides action/token details',async()=>{
 const {TaskPlanView}=await loadComponent('TaskPlanView.tsx')
 const value=plan('DONE','PREPARED');value.nodes[0].token='hidden-secret';value.nodes[0].action={amount_cents:9876543}
 const html=renderToStaticMarkup(React.createElement(TaskPlanView,{plan:value,busy:false,onControl:()=>assert.fail('no auto actions')}))
 assert.match(html,/已准备/);assert.match(html,/执行结果请核对回执/);assert.doesNotMatch(html,/hidden-secret|9876543|opaque-not-for-display/)
 assert.match(html,/转账/);assert.doesNotMatch(html,/>transfer</)
})

test('continue is explicit and callbacks respect busy',async()=>{
 const {TaskPlanView}=await loadComponent('TaskPlanView.tsx');const calls=[]
 const props={plan:plan(),busy:false,onControl:a=>calls.push(a)}
 const tree=TaskPlanView(props);assert.deepEqual(calls,[])
 const button=elements(tree,'button').find(b=>textContent(b).includes('继续'))
 assert.ok(button);button.props.onClick();assert.deepEqual(calls,['resume'])
 assert.ok(elements(TaskPlanView({...props,busy:true}),'button').every(b=>b.props.disabled))
})

test('orphan or in-flight work offers local review without a resume button',async()=>{
 const {TaskPlanView}=await loadComponent('TaskPlanView.tsx')
 for(const state of ['ORPHAN','RUNNING']){
  const tree=TaskPlanView({plan:plan('PAUSED',state),busy:false,onControl:()=>{}})
  assert.ok(!elements(tree,'button').some(b=>textContent(b).includes('继续')))
  assert.ok(elements(tree,'button').some(b=>textContent(b).includes('核查')))
 }
})

test('local handoff explicitly states no human contact or financial execution',async()=>{
 const {LocalHandoffSummary}=await loadComponent('TaskPlanView.tsx')
 const summary={id:'local-1',plan_id:'plan-one',created_at:1792946000,reason:'TOOL_FAILURE',steps:[],operations:[],checklist:['先核对回执'],simulated:true,contacted_human:false}
 const html=renderToStaticMarkup(React.createElement(LocalHandoffSummary,{summary}))
 assert.match(html,/本地核查/);assert.match(html,/未联系/);assert.doesNotMatch(html,/已报警|已联系真人|已付款/)
})

test('done preparation with pending business offers explicit stop controls without resume',async()=>{
 const {TaskPlanView}=await loadComponent('TaskPlanView.tsx');const calls=[]
 const value={...plan('DONE','PREPARED'),business:{pending_ops:1,awaiting_selection:false,actionable:true}}
 const tree=TaskPlanView({plan:value,busy:false,onControl:a=>calls.push(a)})
 assert.match(textContent(tree),/1\s*笔待确认或验证/)
 const buttons=elements(tree,'button')
 assert.ok(!buttons.some(b=>/继续|暂停/.test(textContent(b))))
 const cancel=buttons.find(b=>textContent(b).includes('取消'));assert.ok(cancel)
 cancel.props.onClick();assert.deepEqual(calls,['cancel'])
 assert.ok(buttons.some(b=>textContent(b).includes('核查')))
 assert.ok(elements(TaskPlanView({plan:value,busy:true,onControl:()=>{}}),'button').every(b=>b.props.disabled))
})

test('selection summary uses server business facts and allows local handoff',async()=>{
 const {TaskPlanView}=await loadComponent('TaskPlanView.tsx')
 const value={...plan('DONE','PREPARED'),business:{pending_ops:0,awaiting_selection:true,actionable:true}}
 const tree=TaskPlanView({plan:value,busy:false,onControl:()=>{}})
 assert.match(textContent(tree),/待选收款人/)
 assert.ok(elements(tree,'button').some(b=>textContent(b).includes('核查')))
 assert.doesNotMatch(textContent(tree),/0笔待确认|已经付款|已接通/)
})

test('old snapshots and fully settled done plans do not infer actionability from prepared nodes',async()=>{
 const {TaskPlanView}=await loadComponent('TaskPlanView.tsx')
 for(const business of [undefined,{pending_ops:0,awaiting_selection:false,actionable:false}]){
  const value={...plan('DONE','PREPARED'),business}
  const tree=TaskPlanView({plan:value,busy:false,onControl:()=>assert.fail('no automatic action')})
  assert.equal(elements(tree,'button').length,0)
  assert.doesNotMatch(textContent(tree),/1笔待确认|待选收款人/)
 }
})

test('unresolved legacy attribution is visible without claiming batch cancellation coverage',async()=>{
 const {TaskPlanView}=await loadComponent('TaskPlanView.tsx')
 const value={...plan('DONE','PREPARED'),business:{pending_ops:0,awaiting_selection:false,actionable:false,attribution_incomplete:true}}
 const tree=TaskPlanView({plan:value,busy:false,onControl:()=>assert.fail('no automatic action')})
 assert.match(textContent(tree),/逐笔核对/)
 assert.match(textContent(tree),/整项取消不会处理/)
 assert.equal(elements(tree,'button').length,0)
})

test('home task attention includes pending done work and unresolved legacy records',async()=>{
 const {planNeedsAttention}=await import('./task-plan.ts')
 assert.equal(typeof planNeedsAttention,'function')
 assert.equal(planNeedsAttention(plan('DONE','PREPARED')),false)
 assert.equal(planNeedsAttention({...plan('DONE','PREPARED'),business:{pending_ops:1,awaiting_selection:false,actionable:true}}),true)
 assert.equal(planNeedsAttention({...plan('CANCELLED','PREPARED'),business:{pending_ops:0,awaiting_selection:false,actionable:false,attribution_incomplete:true}}),true)
 assert.equal(planNeedsAttention(plan()),true)
})
