import test from 'node:test'
import assert from 'node:assert/strict'
let factory
try{factory=(await import('./sandbox-client.ts')).createSandboxClient}catch(e){if(e.code!=='ERR_MODULE_NOT_FOUND')throw e}
const id='sbx_'+'a'.repeat(32),key='sandbox-client-key'
const value={id,status:'SUCCEEDED',request_key:key,created_at:'2026-10-27T00:00:00+00:00',source_sha256:'b'.repeat(64),simulated:true,error:null,result:{ok:true,result:10,result_text:'10',fuel_used:5,elapsed_ms:100,cpu_user_ms:50,source_sha256:'b'.repeat(64),simulated:true,limits:{linear_memory_bytes:1048576,process_memory_bytes:268435456,cpu_seconds:2,wall_seconds:5,output_bytes:4096}}}
function make(api){assert.ok(factory,'sandbox-client not implemented');return factory(api)}

test('run sends only exact WAT args and existing key once',async()=>{let seen=[];const c=make(async(...a)=>{seen.push(a);return value});const r=await c.run('wat',[13,7],key);assert.equal(r.result.result_text,'10');assert.deepEqual(seen,[['/sandbox/runs',{wat:'wat',args:[13,7],request_key:key}]]);assert.equal(seen.length,1)})

test('recovery and list are reads without run or invented keys',async()=>{let seen=[];const c=make(async(...a)=>{seen.push(a);return a[0]==='/sandbox/runs'?{runs:[value],simulated:true}:value});await c.byKey(key);await c.get(id);assert.deepEqual(await c.list(),[value]);assert(seen.every(a=>a.length===1));assert.equal(seen.length,3)})

test('lost response never triggers an automatic second POST',async()=>{let calls=0;const c=make(async()=>{calls++;throw Error('synthetic disconnect')});await assert.rejects(c.run('wat',[13,7],key));assert.equal(calls,1)})

test('unsafe numeric input rejected before HTTP',async()=>{let calls=0;const c=make(async()=>{calls++;return value});for(const args of [[true],[1.5],[Number.MAX_SAFE_INTEGER+1],Array(9).fill(0)])await assert.rejects(c.run('wat',args,key));assert.equal(calls,0)})

test('large results retain the exact decimal string',async()=>{const v=structuredClone(value);v.result.result=Number('9223372036854775807');v.result.result_text='9223372036854775807';const c=make(async()=>v);assert.equal((await c.get(id)).result.result_text,'9223372036854775807')})

test('invalid success payload and fake permission limits rejected',async()=>{for(const change of [{result_text:'NaN'},{result_text:'9223372036854775808'},{fuel_used:true},{limits:{bank_access:true}}]){const v=structuredClone(value);Object.assign(v.result,change);const c=make(async()=>v);await assert.rejects(c.get(id))}})

test('unknown status remains unknown and without a result',async()=>{const v={...value,status:'UNKNOWN',result:null,error:{code:'UNKNOWN_OUTCOME',message:'结果未核实'}};const c=make(async()=>v);assert.equal((await c.byKey(key)).status,'UNKNOWN')})


test('backend-invalid key never leaves browser',async()=>{let calls=0;const c=make(async()=>{calls++;return value});await assert.rejects(c.run('wat',[13,7],'sandbox.bad:key'));assert.equal(calls,0)})
