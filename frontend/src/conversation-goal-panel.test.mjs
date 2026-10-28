import test from 'node:test'
import assert from 'node:assert/strict'
import {loadComponent,elements,textContent} from './component-test-loader.mjs'
const m=await loadComponent('ConversationGoalPanel.tsx').catch(()=>null)
const goal={id:'goal-owned',version:1,kind:'birthday',status:'ACTIVE',phase:'CHOOSING',title:'给妈妈安排生日',question:'选哪束花？',summary:['预算 500.00 元','不要蛋糕'],choices:[{id:'flowers_simple',label:'简约花束',price_cents:9900},{id:'flowers_large',label:'温馨花束',price_cents:19900}],steps:[{label:'读取花品目录',state:'OBSERVED'}],next_action:'CHOOSE',operation_ids:[]}
const noop=()=>{}
function tree(p={}){assert(m?.default,'conversation goal panel missing');return m.default({goal,busy:false,onChoice:noop,onContinue:noop,onCheck:noop,...p})}
const button=(t,s)=>elements(t,'button').find(b=>textContent(b).includes(s))
test('current goal asks one question and shows authoritative priced choices inline',()=>{let id='';const t=tree({onChoice:x=>id=x});assert.match(textContent(t),/当前目标/);assert.match(textContent(t),/妈妈/);assert.match(textContent(t),/99.00/);assert.match(textContent(t),/本机模拟|本地模拟/);button(t,'简约花束').props.onClick();assert.equal(id,'flowers_simple');assert.equal(elements(t,'form').length,0)})
test('unknown goal never offers continuation cancellation or completion',()=>{let writes=0,reads=0;const t=tree({goal:{...goal,status:'UNKNOWN',next_action:'CONTINUE'},onContinue:()=>writes++,onChoice:()=>writes++,onCheck:()=>reads++});assert.match(textContent(t),/核对/);assert(!elements(t,'button').some(b=>/继续|选择|取消|付款|确认执行/.test(textContent(b))&&!b.props.disabled));button(t,'核对原请求').props.onClick();assert.equal(reads,1);assert.equal(writes,0)})
test('unknown observation overrides completed goal label',()=>{const t=tree({goal:{...goal,status:'COMPLETED',steps:[{label:'付款结果',state:'UNKNOWN'}]}});assert.match(textContent(t),/核对/);assert.doesNotMatch(textContent(t),/目标已完成|安排已完成/)})
test('waiting financial confirmation does not gain a generic continue button',()=>{for(const phase of ['WAITING_CONFIRMATION','WAITING_VERIFICATION']){const t=tree({goal:{...goal,phase,next_action:'CONTINUE',choices:[],operation_ids:['owned-operation']}});assert(!elements(t,'button').some(b=>/继续下一步/.test(textContent(b))&&!b.props.disabled));assert.doesNotMatch(textContent(t),/已付款|付款成功/)}})
test('ready continuation is manual and blocked while busy',()=>{let calls=0;const p={goal:{...goal,phase:'READY',next_action:'CONTINUE',choices:[]},onContinue:()=>calls++};const t=tree(p);assert.equal(calls,0);button(t,'继续下一步').props.onClick();assert.equal(calls,1);assert.equal(button(tree({...p,busy:true}),'继续下一步').props.disabled,true)})
test('invalid price never becomes an executable choice',()=>{for(const price_cents of [true,-1,NaN,1.1,9007199254740992]){const t=tree({goal:{...goal,choices:[{id:'bad',label:'错误报价',price_cents}]}});assert(!elements(t,'button').some(b=>textContent(b).includes('错误报价')&&!b.props.disabled))}})
test('empty goal never displays a previous customers details',()=>{const t=tree({goal:null});assert.doesNotMatch(textContent(t),/妈妈|500.00|99.00/);assert.equal(elements(t,'button').length,0)})
test('ordinary text confirm is not an interface exposed by goal panel',()=>{const t=tree();assert.equal(elements(t,'input').length,0);assert(!elements(t,'button').some(b=>/^确认执行|^验证/.test(textContent(b))))})

test('subscription goal accepts a real zero cost relationship and labels its query consent accurately',()=>{
  const free=tree({goal:{...goal,kind:'spending_subscriptions',choices:[{id:'sub_free',label:'免费试用',price_cents:0}]}})
  assert.equal(button(free,'免费试用')?.props.disabled,false)
  const query=tree({goal:{...goal,kind:'spending_subscriptions',phase:'WAITING_CONFIRMATION',next_action:'WAIT',choices:[]}})
  assert.doesNotMatch(textContent(query),/等待你在操作卡中确认金额/)
  assert.match(textContent(query),/操作卡/)
})
