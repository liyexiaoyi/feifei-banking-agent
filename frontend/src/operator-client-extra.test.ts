import test from 'node:test'
import assert from 'node:assert/strict'
import {createUserCaseClient} from './operator-client.ts'
test('invalid request key format or length is rejected without sending user data',async()=>{let n=0;const c=createUserCaseClient(async()=>{n++;throw Error('network')});for(const key of ['key:bad-value','x'.repeat(65)])await assert.rejects(()=>c.share('plan1',1,true,key));assert.equal(n,0)})
