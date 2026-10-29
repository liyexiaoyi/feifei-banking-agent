import test from 'node:test'
import assert from 'node:assert/strict'
import { draftDecision, isNearLatest, NEAR_LATEST_THRESHOLD } from './conversation-experience.ts'

test('empty or whitespace-only suggestion is ignored', () => {
  assert.equal(draftDecision('hello', ''), 'ignore')
  assert.equal(draftDecision('', '   \n\t'), 'ignore')
})

test('suggestion applies when current draft is empty or whitespace', () => {
  assert.equal(draftDecision('', '你好'), 'apply')
  assert.equal(draftDecision('  \n ', '你好'), 'apply')
})

test('suggestion applies when it matches the draft after trim', () => {
  assert.equal(draftDecision('  你好 ', '你好\n'), 'apply')
})

test('different non-empty draft needs confirmation', () => {
  assert.equal(draftDecision('我自己写的', '你好'), 'confirm')
})

test('only leading and trailing whitespace is normalized', () => {
  assert.equal(draftDecision('a b', 'a  b'), 'confirm')
  assert.equal(draftDecision('Hello', 'hello'), 'confirm')
})

test('threshold is inclusive at 72px from the bottom', () => {
  assert.equal(NEAR_LATEST_THRESHOLD, 72)
  assert.equal(isNearLatest({ scrollHeight: 1000, clientHeight: 400, scrollTop: 600 }), true)
  assert.equal(isNearLatest({ scrollHeight: 1000, clientHeight: 400, scrollTop: 528 }), true)
  assert.equal(isNearLatest({ scrollHeight: 1000, clientHeight: 400, scrollTop: 527 }), false)
})

test('overscroll past the bottom counts as near latest', () => {
  assert.equal(isNearLatest({ scrollHeight: 1000, clientHeight: 400, scrollTop: 650 }), true)
})

test('negative overscroll at the top clamps to zero', () => {
  assert.equal(isNearLatest({ scrollHeight: 1000, clientHeight: 400, scrollTop: -40 }), false)
  assert.equal(isNearLatest({ scrollHeight: 450, clientHeight: 400, scrollTop: -40 }), true)
})

test('content shorter than the viewport is near latest', () => {
  assert.equal(isNearLatest({ scrollHeight: 200, clientHeight: 400, scrollTop: 0 }), true)
  assert.equal(isNearLatest({ scrollHeight: 0, clientHeight: 0, scrollTop: 0 }), true)
})

test('NaN and Infinity fail closed', () => {
  assert.equal(isNearLatest({ scrollHeight: NaN, clientHeight: 400, scrollTop: 600 }), false)
  assert.equal(isNearLatest({ scrollHeight: 1000, clientHeight: 400, scrollTop: Infinity }), false)
  assert.equal(isNearLatest({ scrollHeight: 1000, clientHeight: -Infinity, scrollTop: 0 }), false)
})

test('negative heights and out-of-bound values fail closed', () => {
  assert.equal(isNearLatest({ scrollHeight: -1, clientHeight: 400, scrollTop: 0 }), false)
  assert.equal(isNearLatest({ scrollHeight: 1000, clientHeight: -5, scrollTop: 0 }), false)
  assert.equal(isNearLatest({ scrollHeight: 2e9, clientHeight: 400, scrollTop: 2e9 }), false)
})

test('malformed input fails closed', () => {
  assert.equal(isNearLatest(null as never), false)
  assert.equal(isNearLatest({ scrollHeight: '1000', clientHeight: 400, scrollTop: 600 } as never), false)
  assert.equal(isNearLatest({} as never), false)
})
