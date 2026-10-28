import test from 'node:test'
import assert from 'node:assert/strict'
import {loadComponent,elements,textContent} from './component-test-loader.mjs'
const {default:Summary}=await loadComponent('WealthSummary.tsx')
const op={id:'hidden-op',type:'wealth_purchase',risk:'RED',status:'UNKNOWN',created_at:1,expires_at:100,payload:{account_id:'hidden-account',account_name:'日常账户',product_id:'demo_cash',product_name:'演示产品甲',action:'purchase',amount_cents:30000,units_1e4:3000000,nav_cents:100,quote_version:98765,fee_cents:0,simulated:true},result:null}
const snapshot={products:[],positions:[],applications:[op],simulated:true,valuation_note:'固定演示估值'}
test('pending unknown has a text label and only an explicit read-only receipt callback',()=>{
 const calls=[];const tree=Summary({snapshot,busy:false,onReceipt:id=>calls.push(id)})
 assert.deepEqual(calls,[])
 const text=textContent(tree);assert.match(text,/核实|核对/);assert.match(text,/演示产品甲/);assert.match(text,/日常账户/);assert.doesNotMatch(text,/hidden-op|hidden-account|98765/)
 const buttons=elements(tree,'button');assert.equal(buttons.length,1);assert.match(textContent(buttons[0]),/核对/)
 buttons[0].props.onClick();assert.deepEqual(calls,[op.id])
 assert.ok(elements(Summary({snapshot,busy:true,onReceipt:()=>{}}),'button').every(b=>b.props.disabled))
})
test('zero holdings remains distinct from unknown pending applications',()=>{
 const text=textContent(Summary({snapshot,busy:false,onReceipt:()=>{}}))
 assert.match(text,/没有模拟持仓|暂无模拟持仓/);assert.match(text,/核实|核对/)
})
