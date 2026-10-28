import test from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {loadComponent} from './component-test-loader.mjs'
const {default:Panel}=await loadComponent('HandoffProgressPanel.tsx')
const view={handoff_id:'SECRET-ID',plan_id:'SECRET-PLAN',status:'CLOSED',version:1,updated_at:1,simulated:true,contacted_human:false,operations:[{id:'SECRET-OP',type:'transfer',status:'UNKNOWN'}],events:[],receipt:null,replayed:false}
const props={view,loading:false,busy:false,error:'',onRefresh(){},onUpdate(){}}
test('closed local record still warns unknown and never exposes technical identifiers',()=>{
 const html=renderToStaticMarkup(React.createElement(Panel,props))
 assert.match(html,/待核实/);assert.match(html,/不代表银行已处理/);assert.match(html,/未联系/)
 assert.doesNotMatch(html,/SECRET|客服已接手|可继续转账/)
 assert.doesNotMatch(html,/>关闭本地记录</)
})
test('failed progress read cannot show enabled change controls',()=>{
 const html=renderToStaticMarkup(React.createElement(Panel,{...props,view:null,error:'暂时无法核对'}))
 assert.match(html,/暂时无法核对/)
 assert.doesNotMatch(html,/>标记为自行核查中</)
 assert.match(html,/刷新/)
})
test('open case has explicit keyboard buttons and busy disables changes',()=>{
 const html=renderToStaticMarkup(React.createElement(Panel,{...props,view:{...view,status:'OPEN',version:0},busy:true}))
 assert.match(html,/标记为自行核查中/);assert.match(html,/关闭本地记录/)
 assert.match(html,/disabled/);assert.match(html,/aria-live/)
})
test('current status is clearly separated from immutable handoff snapshot',async()=>{
 const html=renderToStaticMarkup(React.createElement(Panel,props))
 assert.match(html,/当前关联操作/)
 const {LocalHandoffSummary}=await loadComponent('TaskPlanView.tsx')
 const snapshot={id:'local-case',plan_id:'plan-1',created_at:1,reason:null,steps:[],operations:[{id:'op-1',type:'transfer',status:'CANCELLED'}],checklist:[],simulated:true,contacted_human:false}
 assert.match(renderToStaticMarkup(React.createElement(LocalHandoffSummary,{summary:snapshot})),/交接时的操作快照/)
})
test('card progress uses actual loss and restore business names',()=>{
 for(const [type,name] of [['card_report_lost','卡片挂失'],['card_restore','卡片解挂']]){
  const html=renderToStaticMarkup(React.createElement(Panel,{...props,view:{...view,operations:[{id:'hidden-card-op',type,status:'CANCELLED'}]}}))
  assert.match(html,new RegExp(name+'：已取消'))
 }
})
