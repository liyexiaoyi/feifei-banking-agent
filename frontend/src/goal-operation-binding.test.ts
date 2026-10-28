import test from 'node:test'
import assert from 'node:assert/strict'
import {goalOperationBinding} from './goal-operation-binding.ts'
const g={id:'current',operation_ids:['op-current'],status:'ACTIVE',pause_gen:0}
const saved={id:'saved',operation_ids:['op-saved'],status:'PAUSED',paused:true,pause_gen:2}
test('binding includes saved paused goals when another goal is current',()=>{assert.equal(goalOperationBinding(g,[saved],'op-saved')?.id,'saved');assert.equal(goalOperationBinding(g,[saved],'op-current')?.id,'current')})
test('binding includes saved goal while explicit switch cleared current',()=>{assert.equal(goalOperationBinding(null,[saved],'op-saved')?.id,'saved');assert.equal(goalOperationBinding(null,[saved],'op-unrelated'),null)})
test('current binding takes precedence for same goal in saved list',()=>{assert.equal(goalOperationBinding({...saved,status:'ACTIVE'},[saved],'op-saved')?.status,'ACTIVE')})
