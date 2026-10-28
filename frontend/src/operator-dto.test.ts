import test from 'node:test'
import assert from 'node:assert/strict'
import {parseCasePreview,parseOperatorCase} from './operator-dto.ts'
const facts={plan_id:'p',plan_version:1,observed_at:1,operations:[{id:'o',type:'transfer',status:'UNKNOWN'}]}
const original={id:'c',plan_id:'p',handoff_id:'h',status:'QUEUED',version:1,simulated:true,created_at:1,updated_at:1,reply:'',replayed:false,snapshot:facts,current_facts:facts}
for(const status of ['WAITING_CONFIRMATION','WAITING_VERIFICATION','READY','EXECUTING','MANUAL_REVIEW','EXPIRED'])test('actual bank status '+status+' remains visible without guessed replacement',()=>{const f={...facts,operations:[{...facts.operations[0],status}]};assert.equal(parseCasePreview(f).operations[0].status,status)})
test('unshared current data and wrong type are rejected',()=>{for(const op of [{id:'foreign',type:'transfer',status:'UNKNOWN'},{id:'o',type:'wealth',status:'UNKNOWN'}])assert.throws(()=>parseOperatorCase({...original,current_facts:{...facts,operations:[op]}}))})
