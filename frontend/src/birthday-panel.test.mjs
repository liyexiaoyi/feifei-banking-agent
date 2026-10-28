import test from 'node:test'
import assert from 'node:assert/strict'
import {loadComponent,elements,textContent} from './component-test-loader.mjs'
import {flow,bundle,budgetOp,linkedFlow} from './birthday-fixtures.mjs'
const m=await loadComponent('BirthdayPanel.tsx').catch(()=>null),summary=await loadComponent('BirthdaySummary.tsx').catch(()=>null)
const state={data:bundle(),selected:null,operation:null,pending:null,busy:false,needsCheck:false,invalidSession:false,error:'',notice:'',checked:false}
const noop=()=>{}
const props={state,blocked:false,locked:false,stage:0,recipient:'',eventDate:'',amount:'',accountId:'',consent:false,onRecipient:noop,onDate:noop,onAmount:noop,onAccount:noop,onConsent:noop,onNext:noop,onBack:noop,onStart:noop,onSelect:noop,onCommand:noop,onOpen:noop,onRefresh:noop,onCheck:noop,onAcknowledge:noop}
function tree(p={}){assert(m?.default,'BirthdayPanel missing');return m.default({...props,...p})}
const button=(t,s)=>elements(t,'button').find(b=>textContent(b)===s)
test('birthday wizard asks one field at a time without guessed recipient account or money',()=>{
 for(const stage of [0,1,2,3]){const t=tree({stage});assert.equal(elements(t,stage===3?'select':'input').filter(e=>e.props.type!=='checkbox').length,1);assert.match(textContent(t),/本机模拟|本地模拟/)}
 assert.equal(button(tree(),'下一步').props.disabled,true)
})
test('start requires all exact fields owned account and explicit summary consent',()=>{
 const ready={stage:4,recipient:'妈妈',eventDate:'2026-10-29',amount:'1000',accountId:'a_daily',consent:true}
 const t=tree(ready);assert.equal(button(t,'确认启动安排').props.disabled,false);assert.match(textContent(t),/妈妈/);assert.match(textContent(t),/2026-10-29/);assert.match(textContent(t),/1,000|1000/);assert.match(textContent(t),/2866/)
 for(const d of [{consent:false},{amount:'一千来块'},{accountId:'foreign'},{locked:true},{blocked:true},{state:{...state,needsCheck:true}}]){const b=button(tree({...ready,...d}),'确认启动安排');assert(!b||b.props.disabled)}
})
test('private fields and stale flow disappear on failed read or changed session',()=>{
 for(const d of [{error:'读取失败'},{invalidSession:true}]){const t=tree({recipient:'秘密称呼',stage:4,state:{...state,selected:flow(),...d}});assert(!textContent(t).includes('秘密称呼'));assert(!textContent(t).includes('妈妈'));assert(!textContent(t).includes('2866'));assert.equal(elements(t,'input').length,0)}
})
test('unknown beats completed and prevents new preparation',()=>{
 const f=linkedFlow();f.status='COMPLETED';f.nodes[0].observed_state='UNKNOWN';const t=tree({state:{...state,selected:f}});assert.match(textContent(t),/待核对/);assert.doesNotMatch(textContent(t),/安排已完成/);assert(!elements(t,'button').some(b=>/准备|继续/.test(textContent(b))&&!b.props.disabled))
})
test('each financial operation can be opened but no flow command claims payment',()=>{
 const f=linkedFlow(),t=tree({state:{...state,selected:f}});assert(elements(t,'button').some(b=>/查看.*预算|查看.*预留/.test(textContent(b))));assert.match(textContent(t),/未连接.*商户|商户未连接/);assert.doesNotMatch(textContent(t),/已送达|鲜花已预订/)
})
test('budget success is a reserve and UNKNOWN cannot inherit a success label',()=>{
 assert(summary?.default,'BirthdaySummary missing');const op=budgetOp();op.status='SUCCEEDED';op.result={budget_id:'bb_test',simulated:true,message:'预留'};const t=summary.default({op});assert.match(textContent(t),/预留/);assert.match(textContent(t),/未扣款/);assert.doesNotMatch(textContent(t),/付款成功|已送达/)
 op.status='UNKNOWN';assert.match(textContent(summary.default({op})),/待核对/)
})
test('bad birthday summary cannot show an amount or executable approval',()=>{
 assert(summary?.default,'BirthdaySummary missing');const op=budgetOp();op.payload.amount_cents=true;const t=summary.default({op});assert.match(textContent(t),/核对|无效/);assert.equal(elements(t,'button').length,0);assert.doesNotMatch(textContent(t),/1000/)
})
test('real pending catalog nodes expose choices only after confirmed budget and explicit cake decision',()=>{
 const f=linkedFlow();f.nodes[0].observed_state='SUCCEEDED';f.nodes[1].state=f.nodes[1].observed_state='SUCCEEDED';f.cake_choice=false
 const t=tree({state:{...state,selected:f}});assert(elements(t,'button').some(b=>/选择 简约花束/.test(textContent(b))&&!b.props.disabled));assert(!elements(t,'button').some(b=>/选择 小蛋糕/.test(textContent(b))&&!b.props.disabled))
 f.cake_choice=true;assert(elements(tree({state:{...state,selected:f}}),'button').some(b=>/选择 小蛋糕/.test(textContent(b))&&!b.props.disabled))
 f.nodes[0].observed_state='WAITING_CONFIRMATION';assert(!elements(tree({state:{...state,selected:f}}),'button').some(b=>/选择 简约花束/.test(textContent(b))&&!b.props.disabled))
})
test('unresolved original request disables wizard navigation and fields',()=>{
 const t=tree({recipient:'妈妈',state:{...state,needsCheck:true,pending:{kind:'create',request_key:'birthday-original-key'}}});assert(button(t,'下一步').props.disabled);assert(elements(t,'input').every(i=>i.props.disabled))
})
test('new wizard cannot start while a selected financial flow is UNKNOWN',()=>{const f=linkedFlow();f.nodes[0].observed_state='UNKNOWN';const t=tree({stage:4,recipient:'妈妈',eventDate:'2026-10-29',amount:'1000',accountId:'a_daily',consent:true,state:{...state,selected:f}});assert(button(t,'确认启动安排').props.disabled)})
test('normal birthday view exposes a read-only refresh without a business command',()=>{let reads=0,writes=0;const b=button(tree({onRefresh:()=>reads++,onCommand:()=>writes++}),'只读刷新');assert(b);b.props.onClick();assert.equal(reads,1);assert.equal(writes,0)})

test('resume approval is consumed before a second submission and cannot survive another pause',()=>{
 const f=flow();f.status='PAUSED';const calls=[];const t=tree({state:{...state,selected:f},onCommand:(...x)=>calls.push(x)});
 const form=elements(t,'form').find(x=>textContent(x).includes('我确认继续此安排'));assert(form);
 const checkbox={checked:true,disabled:false};const event={preventDefault(){},currentTarget:{elements:{namedItem:()=>checkbox}}};
 form.props.onSubmit(event);form.props.onSubmit(event);assert.equal(calls.length,1);assert.equal(checkbox.checked,false);
});
