import test from 'node:test'
import assert from 'node:assert/strict'
import {loadComponent,elements,textContent} from './component-test-loader.mjs'
const m=await loadComponent('ScheduledPanel.tsx').catch(()=>null),summary=await loadComponent('ScheduledSummary.tsx').catch(()=>null)
const item={id:'sch_first',status:'ACTIVE',observed_status:'ACTIVE',amount_cents:123,payee_name:'妈妈',payee_phone:'138****0001',due_at:'2026-10-28T13:00:00+08:00',authorization_operation_id:'op_auth',child_operation_id:null}
const state={data:{schedules:[item],payees:[{id:'p_mom',name:'妈妈',masked_phone:'138****0001'}]},pending:null,recovered:null,operation:null,busy:false,needsCheck:false,invalidSession:false,error:''}
const props={state,blocked:false,locked:false,payeeId:'',amount:'',due:'',consent:false,autoEnabled:false,onPayee(){},onAmount(){},onDue(){},onConsent(){},onPrepare(){},onRefresh(){},onCheck(){},onStartNew(){},onCancel(){},onOpen(){},onDrive(){},onAuto(){}}
function tree(change={}){assert(m?.default,'ScheduledPanel missing');return m.default({...props,...change})}
const find=(t,label)=>elements(t,'button').find(x=>textContent(x)===label)
test('PC has explicit China time, masked payee, no defaults or implied payment',()=>{
 const t=tree();assert.match(textContent(t),/北京时间/);assert.match(textContent(t),/未连接|本地模拟/);assert.match(textContent(t),/尚未付款/)
 assert(find(t,'准备定时授权').props.disabled);assert(elements(t,'select').some(x=>x.props.value===''));assert(elements(t,'input').some(x=>x.props.type==='datetime-local'))
 assert.match(textContent(t),/关闭|仅此页打开/);assert(!elements(t,'input').some(x=>x.props.type==='checkbox'&&x.props.checked===true))
})
test('preparation requires local fields, explicit consent, free global gate, and resolved state',()=>{
 const ready={payeeId:'p_mom',amount:'1.23',due:'2026-10-28T13:00',consent:true}
 assert.equal(find(tree(ready),'准备定时授权').props.disabled,false)
 for(const x of [{blocked:true},{locked:true},{consent:false},{amount:'1.001'},{state:{...state,busy:true}},{state:{...state,needsCheck:true}},{state:{...state,invalidSession:true}}]){const t=tree({...ready,...x}),b=find(t,'准备定时授权');assert(!b||b.props.disabled===true)}
})
test('unknown child is visibly awaiting verification and cannot be cancelled',()=>{
 const t=tree({state:{...state,data:{...state.data,schedules:[{...item,status:'CHILD_IN_FLIGHT',observed_status:'UNKNOWN',child_status:'UNKNOWN',child_operation_id:'op_child'}]}}})
 assert.match(textContent(t),/待核对|待核实/);assert(!elements(t,'button').some(x=>/取消/.test(textContent(x))&&!x.props.disabled))
})
test('read errors and invalid session hide old recipient and masks',()=>{
 for(const x of [{error:'读取失败'},{invalidSession:true}]){const t=tree({state:{...state,...x}});assert(!textContent(t).includes('妈妈'));assert(!textContent(t).includes('138****0001'))}
})
test('expired or missed schedule never claims it will automatically pay',()=>{
 for(const observed_status of ['AUTH_EXPIRED','MISSED_GRACE']){const t=tree({state:{...state,data:{...state.data,schedules:[{...item,observed_status}]}}});assert.match(textContent(t),/过期|超过/);assert.doesNotMatch(textContent(t),/已授权，尚未付款/)}
})
test('authorization summary is never a money-transfer success receipt',()=>{
 assert(summary?.default,'ScheduledSummary missing')
 const op={id:'op_auth',type:'scheduled_transfer_authorize',status:'SUCCEEDED',risk:'RED',payload:{...item,account_name:'日常账户',due_at_iso:item.due_at},result:{status:'ACTIVE'}}
 const t=summary.default({op});assert.match(textContent(t),/授权/);assert.match(textContent(t),/尚未付款|不是付款/);assert.doesNotMatch(textContent(t),/转账成功|付款成功|已到账/)
})
test('recovered active create and checked driver allow explicit next round while keeping writes blocked',()=>{
 for(const x of [{pending:{kind:'create',request_key:'reference-key'},recovered:item},{pending:{kind:'drive'}}]){
  const t=tree({state:{...state,...x,needsCheck:true,checked:true}})
  assert.equal(find(t,'已核对，填写另一笔').props.disabled,false);assert.equal(find(t,'准备定时授权').props.disabled,true)
 }
})
test('unavailable private data cannot leave editable old money fields or permit writes',()=>{
 for(const x of [{error:'读取失败'},{invalidSession:true}]){
  const t=tree({payeeId:'p_mom',amount:'1.23',due:'2026-10-28T13:00',consent:true,state:{...state,...x}})
  assert.equal(elements(t,'select').length,0);assert(!elements(t,'input').some(i=>i.props.type==='datetime-local'||i.props.inputMode==='decimal'))
  const b=find(t,'准备定时授权');assert(!b||b.props.disabled)
 }
})
test('pending child has a real explicit cancellation form',()=>{
 const s={...item,status:'CHILD_PENDING',observed_status:'WAITING_CONFIRMATION',child_status:'WAITING_CONFIRMATION',child_operation_id:'op_child'}
 const t=tree({state:{...state,data:{...state.data,schedules:[s]}}})
 assert(elements(t,'button').some(b=>/取消/.test(textContent(b))&&!b.props.disabled));assert(elements(t,'input').some(i=>i.props.type==='checkbox'&&i.props.required))
})
test('recorded authorization cannot assert current nonpayment after its later child succeeds',()=>{
 assert(summary?.default);const t=summary.default({op:{id:'op_auth',type:'scheduled_transfer_authorize',status:'SUCCEEDED',risk:'RED',payload:{...item,account_name:'日常账户',due_at_iso:item.due_at},result:{status:'ACTIVE'}}})
 assert.doesNotMatch(textContent(t),/授权已记录，尚未付款/);assert.match(textContent(t),/不是付款|另查付款/)
})
test('identified unresolved draft has a safe explicit whole-schedule cancellation path',()=>{
 const s={...item,status:'AUTHORIZING',observed_status:'AUTHORIZING'}
 const t=tree({state:{...state,data:{...state.data,schedules:[s]},pending:{kind:'create',request_key:'reference-key'},recovered:s,needsCheck:true,checked:true}})
 assert(elements(t,'button').some(b=>/取消/.test(textContent(b))&&!b.props.disabled))
})
test('original authorization cancelled or unknown is labelled as such, not waiting confirmation',()=>{
 for(const observed_status of ['CANCELLED','UNKNOWN']){
  const t=tree({state:{...state,data:{...state.data,schedules:[{...item,status:'AUTHORIZING',observed_status}]}}})
  assert.doesNotMatch(textContent(t),/等待确认授权/);assert.match(textContent(t),observed_status==='CANCELLED'?/取消/:/待核对|待核实/)
 }
})
test('readonly observed child success overrides stale pending schedule label',()=>{
 const t=tree({state:{...state,data:{...state.data,schedules:[{...item,status:'CHILD_PENDING',observed_status:'SUCCEEDED',child_status:'SUCCEEDED',child_operation_id:'op_child'}]}}})
 assert.match(textContent(t),/模拟付款已完成/);assert.doesNotMatch(textContent(t),/等待确认付款/)
})
test('sensitive verification lock still allows explicit safe cancellation',()=>{
 const t=tree({locked:true});assert(elements(t,'button').some(b=>/取消/.test(textContent(b))&&!b.props.disabled))
})
