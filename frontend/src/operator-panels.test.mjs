import test from 'node:test'
import assert from 'node:assert/strict'
import {loadComponent,elements,textContent} from './component-test-loader.mjs'
const m=await loadComponent('OperatorPanels.tsx').catch(()=>null)
const snapshot={plan_id:'p1',plan_version:3,observed_at:1,operations:[{id:'o1',type:'transfer',status:'CANCELLED',amount_cents:12345,payee_name:'妈妈',payee_phone:'138****0001'}]}
const current={plan_id:'p1',plan_version:3,observed_at:2,operations:[{id:'o1',type:'transfer',status:'UNKNOWN'}]}
const record={id:'case1',plan_id:'p1',handoff_id:'h1',status:'IN_REVIEW',version:2,simulated:true,created_at:1,updated_at:2,reply:'',snapshot,current_facts:current,replayed:false}
const required=()=>{assert(m,'OperatorPanels.tsx missing');return m}
const props={preview:snapshot,caseView:null,loading:false,busy:false,blocked:false,error:'',notice:'',consent:false,onConsent(){},onShare(){},onWithdraw(){},onRefresh(){}}
const user=changes=>required().UserCasePanel({...props,...changes})
const button=(t,name)=>{const b=elements(t,'button').find(b=>textContent(b)===name);assert(b,name);return b}
test('facts keep original amount separate from current UNKNOWN and unavailable current is not success',()=>{
 const t=required().CaseFactsPanel({snapshot,current})
 assert.match(textContent(t),/分享时/);assert.match(textContent(t),/当前/);assert.match(textContent(t),/结果待核实/);assert.match(textContent(t),/123\.45/)
 assert.doesNotMatch(textContent(required().CaseFactsPanel({snapshot,current:null})),/已完成/)
})
test('sharing requires explicit consent and ready preview, and withdrawal does not offer payment',()=>{
 assert.equal(button(user({}),'分享给本机演示坐席').props.disabled,true)
 assert.equal(button(user({consent:true}),'分享给本机演示坐席').props.disabled,false)
 for(const x of [{preview:null},{busy:true},{blocked:true},{loading:true}])assert.equal(button(user({consent:true,...x}),'分享给本机演示坐席').props.disabled,true)
 assert(!elements(user({caseView:record}),'button').some(b=>/付款|验证|确认转账/.test(textContent(b))))
})
test('withdrawn or read failure hides old amount and recipient, preserves warning',()=>{
 for(const x of [{caseView:{...record,status:'CANCELLED',snapshot:null,current_facts:null}},{caseView:record,error:'核对失败'}]){
  const text=textContent(user({...x,notice:'结果未确认，请先核对'}));assert.doesNotMatch(text,/妈妈|123\.45|138\*{4}0001/);assert.match(text,/结果未确认/)
 }
})
const operator={session:true,loading:false,busy:false,error:'',notice:'',credential:'',onCredential(){},onLogin(){},onLogout(){},cases:[record],selected:record,reply:'',onReply(){},onSelect(){},onRefresh(){},onClaim(){},onSubmit(){}}
test('operator login is separate password input and no private case is shown before authentication',()=>{
 const t=required().OperatorPanel({...operator,session:false,credential:'typed-test-secret'})
 assert.equal(elements(t,'input').find(i=>i.props.type==='password').props.value,'typed-test-secret')
 assert.doesNotMatch(textContent(t),/妈妈|138\*{4}0001/)
 assert.match(textContent(t),/本机演示/)
})
test('operator state permits only claim or nonempty reply as appropriate',()=>{
 const panel=changes=>required().OperatorPanel({...operator,...changes})
 assert.equal(!!button(panel({selected:{...record,status:'QUEUED'}}),'接单核对').props.disabled,false)
 assert.equal(button(panel({selected:record,reply:'核对原回执'}),'提交回复').props.disabled,false)
 for(const x of [{reply:''},{busy:true},{error:'无法核对'},{selected:{...record,status:'REPLIED'}}]){
  const t=panel({...x});const b=elements(t,'button').find(b=>textContent(b)==='提交回复');assert(!b||b.props.disabled)
 }
})
test('plain operator reply is escaped and always separate from bank outcome',()=>{
 const t=user({caseView:{...record,status:'REPLIED',reply:'<img src=x onerror=alert(1)>'}})
 assert.equal(elements(t,'img').length,0);assert.match(textContent(t),/不代表银行|不代表业务/)
})

test('snapshot shows its historical status as well as current uncertainty',()=>{assert.match(textContent(required().CaseFactsPanel({snapshot,current})),/已取消/)})
test('read error and withdrawal never allow sharing from hidden stale preview',()=>{for(const x of [{error:'核对失败'},{caseView:{...record,status:'CANCELLED',snapshot:null,current_facts:null}}])assert.equal(button(user({consent:true,...x}),'分享给本机演示坐席').props.disabled,true)})
test('withdraw controls respect global lock and unavailable state',()=>{for(const x of [{blocked:true},{loading:true},{error:'暂不可用'}]){const t=user({caseView:record,...x});const b=elements(t,'button').find(b=>textContent(b)==='撤回分享');assert(!b||b.props.disabled)}})
