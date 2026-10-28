import test from 'node:test'
import assert from 'node:assert/strict'
import {loadComponent,elements,textContent} from './component-test-loader.mjs'
const module=await loadComponent('CardControlPanel.tsx').catch(()=>null),Panel=module?.default
const card={id:'card_a_daily',account_id:'a_daily',name:'日常演示卡',masked_number:'DEMO ···· 0826',status:'ACTIVE',version:1,simulated:true}
const bundle={card,controls:{card_id:card.id,locked:false,single_limit_cents:2000000,daily_limit_cents:5000000,control_version:1,spent_today_cents:0,simulated:true},credential:{card_id:card.id,configured:false,credential_version:0,simulated:true,bank_connected:false},merchants:[{id:'demo_market',name:'演示超市',simulated:true,merchant_connected:false}]}
const state={bundle,receipt:null,pending:null,busy:false,needsCheck:false,error:''}
const props={cards:[card],cardId:card.id,mode:'spend',state,blocked:false,locked:false,amount:'1.00',merchantId:'demo_market',newSecret:'',currentSecret:'',onCard(){},onMode(){},onAmount(){},onMerchant(){},onNewSecret(){},onCurrentSecret(){},onPrepare(){},onRefresh(){},onCheck(){},onStartNew(){},onOpenOperation(){}}
function tree(change={}){assert.ok(Panel,'CardControlPanel.tsx must exist');return Panel({...props,...change})}
const find=(t,label)=>elements(t,'button').find(x=>textContent(x)===label)
test('preparation is explicit and never a payment or confirmation button',()=>{
 const t=tree();assert(find(t,'准备模拟消费'));assert(!elements(t,'button').some(x=>/立即付款|确认付款|已到账/.test(textContent(x))));assert.match(textContent(t),/未连接|本机模拟/)
})
test('busy, global gate, lock, uncertainty or invalid cents disables preparation',()=>{
 for(const change of [{blocked:true},{locked:true},{state:{...state,busy:true}},{state:{...state,needsCheck:true}},{amount:'1.001'},{amount:''}]){
  const t=tree(change),b=find(t,'准备模拟消费');assert(!b||b.props.disabled===true)
 }
})
test('credential secrets only appear in dedicated masked inputs and require existing secret when configured',()=>{
 const t=tree({mode:'credential',state:{...state,bundle:{...bundle,credential:{...bundle.credential,configured:true,credential_version:1}}},newSecret:'DemoNew_only-secret',currentSecret:''})
 const inputs=elements(t,'input').filter(x=>x.props.type==='password');assert.equal(inputs.length,2);assert(inputs.every(x=>x.props.autoComplete==='off'))
 assert.match(textContent(t),/真实银行卡密码/);assert(!textContent(t).includes('DemoNew_only-secret'));assert.equal(find(t,'准备修改演示口令').props.disabled,true)
})
test('lost status and independent transaction lock are visible in ordinary Chinese',()=>{
 const t=tree({state:{...state,bundle:{...bundle,card:{...card,status:'LOST'},controls:{...bundle.controls,locked:true}}}})
 assert.match(textContent(t),/已挂失/);assert.match(textContent(t),/交易锁/);assert.match(textContent(t),/锁定/)
})
test('read failure hides stale private card details and leaves a refresh action',()=>{
 const t=tree({state:{...state,error:'读取失败'}});assert(!textContent(t).includes('0826'));assert(elements(t,'button').some(x=>/刷新/.test(textContent(x))))
})
test('UNKNOWN cannot start new preparation or clear the pending reference',()=>{
 const t=tree({state:{...state,needsCheck:true,pending:{card_id:card.id,request_key:'pending-opaque-key',type:'card_spend'},receipt:{operation:{type:'card_spend',status:'UNKNOWN'},recorded_status:'UNKNOWN',observed_status:'UNKNOWN',read_only:true,simulated:true}}})
 assert.match(textContent(t),/待核实/);const b=find(t,'已核对，重新填写');assert(!b||b.props.disabled===true);assert(find(t,'核对原请求'));assert(!find(t,'准备模拟消费')||find(t,'准备模拟消费').props.disabled)
})

test('lost card can still change its independent transaction lock',()=>{
 const t=tree({mode:'unlock',state:{...state,bundle:{...bundle,card:{...card,status:'LOST'},controls:{...bundle.controls,locked:true}}}})
 assert.equal(find(t,'准备解锁交易').props.disabled,false)
})
test('control limits cannot exceed backend 5000000-cent maximum',()=>{
 const t=tree({mode:'daily',amount:'50000.01'});assert.equal(find(t,'准备调整每日限额').props.disabled,true)
})
test('observed expiry still offers an explicit new draft while recorded operation stays pending',()=>{
 const t=tree({state:{...state,needsCheck:true,pending:{card_id:card.id,request_key:'pending-opaque-key',type:'card_spend'},receipt:{operation:{type:'card_spend',status:'WAITING_CONFIRMATION'},recorded_status:'WAITING_CONFIRMATION',observed_status:'EXPIRED',read_only:true,simulated:true}}})
 assert.equal(find(t,'已核对，重新填写').props.disabled,false)
})

test('read buttons do not pretend to be actionable while the section global read gate is blocked',()=>{
 const failed=tree({blocked:true,state:{...state,error:'准备被拒绝'}})
 assert.equal(find(failed,'刷新卡片资料').props.disabled,true)
 const t=tree({blocked:true,state:{...state,needsCheck:true,pending:{card_id:card.id,request_key:'pending-opaque-key',type:'card_spend'},receipt:{operation:{type:'card_spend',status:'UNKNOWN'},recorded_status:'UNKNOWN',observed_status:'UNKNOWN',read_only:true,simulated:true}}})
 assert.equal(find(t,'刷新卡片资料').props.disabled,true);assert.equal(find(t,'核对原请求').props.disabled,true)
})

test('pending recovery keeps editable action fields disabled until explicit fresh-draft session check completes',()=>{
 const t=tree({state:{...state,needsCheck:true,pending:{card_id:card.id,request_key:'pending-opaque-key',type:'card_spend'}}})
 const selects=elements(t,'select');assert.equal(selects.find(x=>x.props.id==='cc-card').props.disabled,false)
 assert.equal(selects.find(x=>x.props.id==='cc-mode').props.disabled,true)
 assert.equal(selects.find(x=>x.props.id==='cc-merchant').props.disabled,true)
 assert(elements(t,'input').filter(x=>x.props.type!=='password').every(x=>x.props.disabled===true))
})
