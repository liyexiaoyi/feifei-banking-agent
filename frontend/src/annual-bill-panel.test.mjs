import test from 'node:test'
import assert from 'node:assert/strict'
import {loadComponent,elements,textContent} from './component-test-loader.mjs'
const {default:Panel}=await loadComponent('AnnualBillPanel.tsx')
const props={year:'2026',report:null,loading:false,error:'',onYearChange:()=>{},onRefresh:()=>{}}
test('unavailable, loading and zero-year data are separate states with simple Chinese',()=>{
 const waiting=textContent(Panel({...props,loading:true}));assert.match(waiting,/读取|查询|加载/);assert.doesNotMatch(waiting,/没有流水/)
 const broken=textContent(Panel({...props,error:'年度账单暂不可用'}));assert.match(broken,/暂不可用/);assert.doesNotMatch(broken,/没有流水/)
 assert.ok(elements(Panel({...props,loading:true}),'button').every(b=>b.props.disabled))
})
test('explicit retry and year selection are read-only callbacks',()=>{
 let calls=0,year='';const tree=Panel({...props,error:'读取失败',onRefresh:()=>calls++,onYearChange:v=>year=v})
 assert.equal(calls,0)
 const button=elements(tree,'button').find(b=>/刷新|重试|重新/.test(textContent(b)));assert.ok(button);button.props.onClick();assert.equal(calls,1)
 const input=[...elements(tree,'select'),...elements(tree,'input')][0];assert.ok(input);input.props.onChange({target:{value:'2025'}});assert.equal(year,'2025')
 assert.doesNotMatch(textContent(tree),/MFA|CAS|重发转账/)
})

test('evidence uses Chinese transaction labels and selection stays available during loading',()=>{
 const input=elements(Panel({...props,loading:true}),'input')[0];assert.ok(!input.props.disabled)
 const report={year:'2026',period:'2026',simulated:true,expense_cents:100000,refund_cents:0,income_cents:0,net_expense_cents:100000,previous_net_expense_cents:0,categories:[],months:[],evidence_ids:['t'],transactions:[{id:'t',kind:'EXPENSE',amount_cents:100000,merchant:'商户甲',category:'其他',occurred_at:'2026-01-01T10:00:00+08:00'}],flags:[{type:'large_expense',message:'请核对',evidence_ids:['t']}]}
 const text=textContent(Panel({...props,report}));assert.match(text,/待核查/);assert.match(text,/商户甲/);assert.match(text,/支出/);assert.doesNotMatch(text,/EXPENSE/)
 for(const changed of [{loading:true},{error:'读取失败'},{year:'2025'}])assert.doesNotMatch(textContent(Panel({...props,report,...changed})),/商户甲/)
})
