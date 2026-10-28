import test from 'node:test'
import assert from 'node:assert/strict'
import {loadComponent,elements,textContent} from './component-test-loader.mjs'
const {default:Panel}=await loadComponent('RecurringRemindersPanel.tsx')
const candidate={merchant:'周期商户',amount_cents:1500,occurrences:2,evidence_ids:['private-1','private-2'],last_seen:'2026-10-27T10:00:00+08:00',rule:'相邻25—35天',verified:false}
const note={id:'rem_private',merchant:'续费商户',amount_cents:2500,charge_date:'2026-10-30',acknowledged:false,version:98765,simulated:true}
const props={enabled:true,candidates:[candidate],reminders:[note],loading:false,error:'',needsConsent:false,busy:false,onRefresh:()=>{},onAck:()=>{},onAuthorize:()=>{}}

test('candidate has no cancellation action and reminder offers only explicit acknowledgement',()=>{
 const calls=[];const tree=Panel({...props,onAck:(id,version)=>calls.push([id,version])})
 const text=textContent(tree);assert.match(text,/周期商户/);assert.match(text,/续费商户/);assert.match(text,/2026-10-30/);assert.match(text,/25\.00/)
 assert.doesNotMatch(text,/rem_private|private-1|98765/)
 assert.equal(calls.length,0)
 const buttons=elements(tree,'button');assert.ok(!buttons.some(b=>/取消|退款|扣款|删除/.test(textContent(b))))
 const ack=buttons.find(b=>/我已看过/.test(textContent(b)));assert.ok(ack);ack.props.onClick();assert.deepEqual(calls,[['rem_private',98765]])
})

test('expired consent, loading and failure hide stale sensitive reminder data',()=>{
 for(const change of [{enabled:false},{needsConsent:true},{loading:true},{error:'信息暂时无法核对'}]){
  const text=textContent(Panel({...props,...change}));assert.doesNotMatch(text,/续费商户|2026-10-30|25\.00/)
 }
 const unauthorized=textContent(Panel({...props,enabled:false}));assert.match(unauthorized,/确认|授权/)
 const unavailable=textContent(Panel({...props,error:'信息暂时无法核对'}));assert.doesNotMatch(unavailable,/暂无提醒/)
})

test('known empty is explicit; global busy and already-seen reminders have no repeat write action',()=>{
 assert.match(textContent(Panel({...props,candidates:[],reminders:[]})),/暂无|没有/)
 for(const modified of [{busy:true},{loading:true},{reminders:[{...note,acknowledged:true}]}]){
  const ack=elements(Panel({...props,...modified}),'button').filter(b=>/我已看过/.test(textContent(b)))
  assert.ok(ack.length===0||ack.every(b=>b.props.disabled))
 }
})
