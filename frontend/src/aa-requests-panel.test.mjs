import test from 'node:test'
import assert from 'node:assert/strict'
import {loadComponent,elements,textContent} from './component-test-loader.mjs'
const module=await loadComponent('AARequestsPanel.tsx').catch(()=>null);const Panel=module?.default
const preview={total_cents:100,participant_count:3,include_self:true,shares:[{participant_id:'self',name:'本人',masked_phone:'',is_self:true,amount_cents:34},{participant_id:'p_mom',name:'妈妈',masked_phone:'138****0001',is_self:false,amount_cents:33},{participant_id:'p_dad',name:'爸爸',masked_phone:'138****0002',is_self:false,amount_cents:33}],self_share_cents:34,receivable_cents:66,simulated:true,notice:'仅本地，未发送、未扣款、未核实到账。'}
const props={amount:'1.00',participant_ids:['p_mom','p_dad'],selfChoice:'yes',participants:[{id:'p_mom',name:'妈妈',masked_phone:'138****0001'},{id:'p_dad',name:'爸爸',masked_phone:'138****0002'}],preview,requests:[],loading:false,busy:false,blocked:false,error:'',notice:'',consent:false,onAmount(){},onParticipant(){},onSelfChoice(){},onConsent(){},onPreview(){},onCreate(){},onCancel(){},onRefresh(){}}
const tree=changes=>{assert.ok(Panel,'AARequestsPanel.tsx must be implemented');return Panel({...props,...changes})}
const create=t=>{const b=elements(t,'button').find(b=>/生成本地.*请求/.test(textContent(b)));assert(b);return b}

test('local AA request needs preview plus explicit separate confirmation',()=>{
 assert.equal(create(tree({})).props.disabled,true)
 assert.equal(create(tree({consent:true})).props.disabled,false)
 for(const change of [{preview:null},{busy:true},{blocked:true}])assert.equal(create(tree({consent:true,...change})).props.disabled,true)
})
test('no guessed self inclusion and participant masks disambiguate names',()=>{
 const t=tree({selfChoice:'',participant_ids:[],preview:null})
 const select=elements(t,'select')[0];assert.equal(select.props.value,'')
 assert.match(textContent(t),/138\*{4}0001/)
 assert.equal(elements(t,'button').find(b=>/预览.*拆分/.test(textContent(b))).props.disabled,true)
})
test('no local record is displayed as sent, received or a bank transfer',()=>{
 const t=tree({requests:[{...preview,id:'aa_one',status:'ACTIVE',version:1,created_at:'2026-10-27',updated_at:'2026-10-27',replayed:false}]})
 assert.match(textContent(t),/未发送/);assert.match(textContent(t),/未核实到账/)
 assert(!elements(t,'button').some(b=>/转账|发送|扣款|到账/.test(textContent(b))))
})
test('failure hides stale participants and requests while retaining uncertain status',()=>{
 const t=tree({error:'读取失败',notice:'本次请求可能已保存，请先核对。'})
 assert.match(textContent(t),/可能已保存/);assert.doesNotMatch(textContent(t),/138\*{4}0001|妈妈/)
 assert(elements(t,'button').some(b=>/刷新|核对/.test(textContent(b))))
})
