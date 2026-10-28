import test from 'node:test'
import assert from 'node:assert/strict'
import {normalizeVerificationCode} from './verification-input.ts'
test('ordinary ASCII verification digits stay unchanged',()=>{assert.equal(normalizeVerificationCode('246810'),'246810')})
test('fullwidth numeric input becomes visible ordinary digits without changing the code',()=>{assert.equal(normalizeVerificationCode('２４６８１０'),'246810')})
test('the field retains only at most six visible ASCII digits',()=>{assert.equal(normalizeVerificationCode('ab２４xy'),'24');assert.equal(normalizeVerificationCode('1234567'),'123456')})
