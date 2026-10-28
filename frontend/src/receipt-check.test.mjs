import test from 'node:test'
import assert from 'node:assert/strict'
import {loadComponent,elements,textContent} from './component-test-loader.mjs'
const receipt=status=>({operation:{id:'op-synthetic',type:'transfer',status,result:status==='SUCCEEDED'?{transaction_id:'tx-synthetic'}:null},recorded_status:status,observed_status:status,read_only:true,simulated:true,checked_at:'2026-10-26T02:00:00+08:00'})
const props={receipt:null,busy:false,error:'',onCheck:()=>{},onClose:()=>{}}

test('receipt has no invented payment result before checking',async()=>{
 const {default:ReceiptCheck}=await loadComponent('ReceiptCheck.tsx')
 const tree=ReceiptCheck(props);const text=textContent(tree)
 assert.match(text,/尚未核对/);assert.match(text,/只读/);assert.doesNotMatch(text,/原操作已完成|0\.00|核对一致/)
})

test('only authoritative succeeded receipt may say original operation completed',async()=>{
 const {default:ReceiptCheck}=await loadComponent('ReceiptCheck.tsx')
 for(const state of ['WAITING_CONFIRMATION','CANCELLED','EXPIRED','UNKNOWN','MANUAL_REVIEW']){
  const text=textContent(ReceiptCheck({...props,receipt:receipt(state)}))
  assert.doesNotMatch(text,/原操作已完成|人工审核中/)
 }
 assert.match(textContent(ReceiptCheck({...props,receipt:receipt('SUCCEEDED')})),/原操作已完成/)
})

test('query failure cannot present an older completion as the new result',async()=>{
 const {default:ReceiptCheck}=await loadComponent('ReceiptCheck.tsx')
 const text=textContent(ReceiptCheck({...props,receipt:receipt('SUCCEEDED'),error:'暂未查到最新状态'}))
 assert.match(text,/暂未查到最新状态/);assert.doesNotMatch(text,/原操作已完成|tx-synthetic/)
})

test('receipt requests happen on clicks only and never provide a confirmation action',async()=>{
 const {default:ReceiptCheck}=await loadComponent('ReceiptCheck.tsx');let calls=0
 const tree=ReceiptCheck({...props,onCheck:()=>calls++})
 assert.equal(calls,0);const buttons=elements(tree,'button')
 assert.deepEqual(buttons.map(b=>textContent(b)),['重新核对回执','关闭'])
 buttons[0].props.onClick();assert.equal(calls,1)
 assert.ok(elements(ReceiptCheck({...props,busy:true}),'button').every(b=>b.props.disabled))
})

test('wealth and virtual-card receipts show the known business and original public context',async()=>{
 const {default:ReceiptCheck}=await loadComponent('ReceiptCheck.tsx')
 for(const [type,label] of [['wealth_purchase','模拟申购'],['wealth_redemption','模拟赎回'],['virtual_card_create','申请虚拟卡']]){
  const data={...receipt('WAITING_VERIFICATION'),operation:{...receipt('WAITING_VERIFICATION').operation,type,payload:{account_name:'日常账户',product_name:'演示产品甲',card_name:'日常虚拟演示卡',amount_cents:30000,private_field:'drop'}}}
  const text=textContent(ReceiptCheck({...props,receipt:data}));assert.match(text,new RegExp(label));assert.match(text,/日常账户/);assert.doesNotMatch(text,/private_field|drop/)
  if(type.startsWith('wealth_')){assert.match(text,/演示产品甲/);assert.match(text,/300\.00/)}
 }
})

test('subscription monthly price is not mislabeled as a payment in a cancel receipt',async()=>{
 const {default:ReceiptCheck}=await loadComponent('ReceiptCheck.tsx')
 const data={...receipt('WAITING_CONFIRMATION'),operation:{...receipt('WAITING_CONFIRMATION').operation,type:'subscription_cancel',payload:{merchant:'流光视频',amount_cents:2500}}}
 const text=textContent(ReceiptCheck({...props,receipt:data}));assert.match(text,/流光视频/);assert.doesNotMatch(text,/25\.00|转账金额|申购金额/)
})
