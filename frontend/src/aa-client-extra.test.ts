import test from 'node:test'
import assert from 'node:assert/strict'
import {parseAAPreview,createAAClient} from './aa-client.ts'
const preview={total_cents:100,participant_count:2,include_self:false,shares:[{participant_id:'a',name:'甲',masked_phone:'****',is_self:false,amount_cents:50},{participant_id:'b',name:'乙',masked_phone:'138****0001',is_self:false,amount_cents:50}],self_share_cents:0,receivable_cents:100,simulated:true,notice:'未发送'}
test('legitimate short-contact mask is accepted and never exposed',()=>{assert.equal(parseAAPreview(preview).shares[0].masked_phone,'****')})
test('one-person server frame is rejected rather than shown as an AA split',()=>{assert.throws(()=>parseAAPreview({...preview,total_cents:50,participant_count:1,shares:[{...preview.shares[1],amount_cents:50}],receivable_cents:50}))})
test('reserved self ID and overlong participant IDs are rejected before any POST',async()=>{let calls=0;const c=createAAClient(async()=>{calls++;return preview});for(const ids of [['self'],['x'.repeat(65)]])await assert.rejects(()=>c.preview(100,ids,true));assert.equal(calls,0)})
test('duplicate server participant choices fail closed instead of making ambiguous checkboxes',async()=>{const p={id:'a',name:'甲',masked_phone:'138****0001'};const c=createAAClient(async path=>path==='/aa'?{requests:[],simulated:true}:{participants:[p,p],simulated:true});await assert.rejects(()=>c.load())})
