import test from 'node:test'
import assert from 'node:assert/strict'
const mod=await import('./aa-client.ts').catch(()=>null)
const preview={total_cents:100,participant_count:3,include_self:true,shares:[{participant_id:'self',name:'本人',masked_phone:'',is_self:true,amount_cents:34},{participant_id:'p_mom',name:'妈妈',masked_phone:'138****0001',is_self:false,amount_cents:33},{participant_id:'p_dad',name:'爸爸',masked_phone:'138****0002',is_self:false,amount_cents:33}],self_share_cents:34,receivable_cents:66,simulated:true,notice:'本地记录，未发送、未扣款、未核实到账。'}
const item={...preview,id:'aa_one',status:'ACTIVE',version:1,created_at:'2026-10-27T20:00:00+08:00',updated_at:'2026-10-27T20:00:00+08:00',replayed:false}
const requireMod=()=>{assert.ok(mod,'aa-client.ts must be implemented');return mod}

test('AA decoder verifies exact share sums, self and no false payment fields',()=>{
 const m=requireMod();const parsed=m.parseAARequest({...item,bank_status:'SUCCEEDED',transaction_id:'secret'})
 assert.equal(parsed.receivable_cents,66);assert.doesNotMatch(JSON.stringify(parsed),/SUCCEEDED|secret|bank_status|transaction_id/)
 for(const bad of [null,{...item,simulated:false},{...item,total_cents:101},{...item,self_share_cents:33},{...item,receivable_cents:100},{...item,participant_count:2},{...item,include_self:false},{...item,version:true},{...item,status:'SUCCEEDED'},{...item,shares:[...item.shares.slice(0,2),{...item.shares[2],participant_id:'p_mom'}]}])assert.throws(()=>m.parseAARequest(bad))
})
test('client reads own masked choices and requests and creates nothing on load',async()=>{
 const calls=[];const m=requireMod();const client=m.createAAClient(async(path,body)=>{calls.push([path,body]);return path==='/aa'?{requests:[item],simulated:true}:{participants:[{id:'p_mom',name:'妈妈',masked_phone:'138****0001'}],simulated:true}})
 const loaded=await client.load();assert.equal(loaded.requests[0].id,'aa_one');assert.equal(loaded.participants[0].id,'p_mom');assert.deepEqual(calls.map(c=>c[0]).sort(),['/aa','/aa/participants']);assert(calls.every(c=>c[1]===undefined))
})
test('incomplete or unconfirmed request sends nothing and unknown failure never retries',async()=>{
 const m=requireMod();let calls=0;const client=m.createAAClient(async()=>{calls++;throw new Error('lost')})
 await assert.rejects(()=>client.create(100,['p_mom','p_dad'],true,'one-key-001',false));assert.equal(calls,0)
 await assert.rejects(()=>client.create(100,['p_mom','p_dad'],true,'one-key-001',true));assert.equal(calls,1)
})
test('preview and cancellation bind explicit user parameters without user or financial authorization',async()=>{
 const m=requireMod();const seen=[];const client=m.createAAClient(async(path,body)=>{seen.push([path,body]);return path.endsWith('/cancel')?{...item,status:'CANCELLED',version:2}:preview})
 await client.preview(100,['p_mom','p_dad'],true);await client.cancel('aa_one',1,'aa-cancel-key')
 assert.deepEqual(seen,[['/aa/preview',{total_cents:100,participant_ids:['p_mom','p_dad'],include_self:true}],['/aa/aa_one/cancel',{expected_version:1,request_key:'aa-cancel-key'}]])
})
