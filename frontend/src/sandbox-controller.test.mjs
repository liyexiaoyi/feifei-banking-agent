import test from 'node:test'
import assert from 'node:assert/strict'
let factory;try{factory=(await import('./sandbox-controller.ts')).createSandboxController}catch(e){if(e.code!=='ERR_MODULE_NOT_FOUND')throw e}
const id='sbx_'+'a'.repeat(32),hash='b'.repeat(64),key='sandbox-controller-key'
const receipt={id,status:'SUCCEEDED',request_key:key,source_sha256:hash,created_at:'2026-10-27T00:00:00Z',result:{ok:true,result:10,result_text:'10'},error:null,simulated:true}
function setup(overrides={}){assert.ok(factory,'sandbox-controller missing');let calls=[],map=new Map(),last;const storage={getItem:k=>map.get(k)??null,setItem:(k,v)=>map.set(k,v),removeItem:k=>map.delete(k)};const client={run:async(...a)=>{calls.push(['POST',...a]);return receipt},byKey:async k=>{calls.push(['GET',k]);return receipt},list:async()=>{calls.push(['LIST']);return []},...overrides};const ctrl=factory({client,storage,user:'demo',sourceHash:hash,wat:'example-wat',keyFactory:()=>key,onChange:v=>{last=v}});return {ctrl,calls,map,get last(){return last}}}

test('load is readonly and no implicit new computation',async()=>{const e=setup();await e.ctrl.load();assert.deepEqual(e.calls,[['LIST']]);assert.equal(e.last.needsCheck,false)})

test('double click is one run and terminal replay requires newRound',async()=>{let release;const e=setup({run:async(...a)=>{e.calls.push(['POST',...a]);await new Promise(r=>release=r);return receipt}});await e.ctrl.load();const first=e.ctrl.run('13','7');const second=e.ctrl.run('13','7');await new Promise(setImmediate);assert.equal(e.calls.filter(c=>c[0]==='POST').length,1);release();await Promise.all([first,second]);await e.ctrl.run('13','7');assert.equal(e.calls.filter(c=>c[0]==='POST').length,1);assert.equal(e.map.size,0)})

test('lost response retains only owner-bound key, reload checks it without a POST',async()=>{const e=setup({run:async()=>{throw Error('synthetic disconnect')}});await e.ctrl.load();await e.ctrl.run('13','7');assert.equal(e.last.needsCheck,true);assert.equal(e.map.get('ff-sandbox-pending:demo'),key);assert(![...e.map.values()].some(v=>/13|example-wat|csrf/.test(v)));await e.ctrl.check();assert.equal(e.calls.filter(c=>c[0]==='GET').length,1);assert.equal(e.last.receipt.status,'SUCCEEDED');assert.equal(e.map.size,0)})

test('mount recovery uses pending key rather than an unrelated newer list result',async()=>{const e=setup();e.map.set('ff-sandbox-pending:demo',key);await e.ctrl.load();assert.deepEqual(e.calls,[['GET',key]]);assert.equal(e.last.receipt.id,id)})

test('404 lookup is uncertainty, not permission to run again',async()=>{const e=setup({byKey:async()=>{throw Error('not found')}});e.map.set('ff-sandbox-pending:demo',key);await e.ctrl.load();assert.equal(e.last.needsCheck,true);e.ctrl.newRound();await e.ctrl.run('13','7');assert.equal(e.calls.filter(c=>c[0]==='POST').length,0);assert.equal(e.map.size,1)})

test('unknown receipt blocks a new round and never resubmits',async()=>{const e=setup({byKey:async()=>({...receipt,status:'UNKNOWN',result:null})});e.map.set('ff-sandbox-pending:demo',key);await e.ctrl.load();e.ctrl.newRound();await e.ctrl.run('13','7');assert.equal(e.last.needsCheck,true);assert.equal(e.calls.filter(c=>c[0]==='POST').length,0);assert.equal(e.map.size,1)})

test('storing pending metadata must succeed before POST',async()=>{const e=setup();await e.ctrl.load();e.map.set=()=>{throw Error('storage disabled')};await e.ctrl.run('13','7');assert.equal(e.calls.filter(c=>c[0]==='POST').length,0)})

test('history of another code hash never becomes this average result',async()=>{const e=setup({list:async()=>[{...receipt,source_sha256:'c'.repeat(64)}]});await e.ctrl.load();assert.equal(e.last.receipt,null);assert.equal(e.last.needsCheck,false)})

test('only safe decimal integer input can reach HTTP',async()=>{const e=setup();await e.ctrl.load();for(const text of ['','1.5','9007199254740992','NaN'])await e.ctrl.run(text,'7');assert.equal(e.calls.filter(c=>c[0]==='POST').length,0)})


test('overlong generated key cannot create a pending marker or reach HTTP',async()=>{const e=setup();await e.ctrl.load();const calls=[];const map=new Map();const c=factory({client:{run:async()=>{calls.push('POST');return receipt},byKey:async()=>receipt,list:async()=>[]},storage:{getItem:k=>map.get(k)??null,setItem:(k,v)=>map.set(k,v),removeItem:k=>map.delete(k)},user:'demo',sourceHash:hash,wat:'wat',keyFactory:()=>'k'.repeat(101),onChange(){}});await c.load();await c.run('13','7');assert.equal(calls.length,0);assert.equal(map.size,0)})

test('uncertain history pins its key for exact future checks',async()=>{const e=setup({list:async()=>[{...receipt,status:'UNKNOWN',result:null}]});await e.ctrl.load();assert.equal(e.map.get('ff-sandbox-pending:demo'),key);assert.equal(e.last.needsCheck,true)})
