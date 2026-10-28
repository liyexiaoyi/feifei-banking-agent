import test from 'node:test'
import assert from 'node:assert/strict'
import { READING_MODE_STORAGE_KEY, readReadingMode, writeReadingMode } from './reading-mode.ts'
import type { ReadingMode } from './reading-mode.ts'

function memoryStorage() {
  const values = new Map<string, string>([['ff.session', 'untouched'], ['ff.preferences', 'original']])
  return {
    values,
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value) }
  }
}

test('Node without window defaults to normal and safely skips persistence', () => {
  assert.equal(typeof window, 'undefined')
  assert.equal(readReadingMode(), 'normal')
  assert.equal(writeReadingMode('comfortable'), false)
})

test('only exact supported stored values are accepted', () => {
  const storage = memoryStorage()
  for (const value of [null, '', 'large', 'COMFORTABLE', ' comfortable ', '{"mode":"comfortable"}', 'normal', 'comfortable']) {
    storage.values.delete(READING_MODE_STORAGE_KEY)
    if (value !== null) storage.values.set(READING_MODE_STORAGE_KEY, value)
    const before = [...storage.values]
    assert.equal(readReadingMode(() => storage), value === 'comfortable' ? 'comfortable' : 'normal')
    assert.deepEqual([...storage.values], before)
  }
})

test('throwing localStorage getter and throwing reads are contained', () => {
  const host = { get localStorage(): never { throw new Error('access denied') } }
  assert.equal(readReadingMode(() => host.localStorage), 'normal')
  assert.equal(writeReadingMode('comfortable', () => host.localStorage), false)
  assert.equal(readReadingMode(() => ({ getItem() { throw new Error('read denied') }, setItem() {} })), 'normal')
})

test('explicit selection persists only the reading key', () => {
  const storage = memoryStorage()
  for (const mode of ['comfortable', 'normal'] as const) {
    assert.equal(writeReadingMode(mode, () => storage), true)
    assert.equal(readReadingMode(() => storage), mode)
    assert.deepEqual([...storage.values], [
      ['ff.session', 'untouched'], ['ff.preferences', 'original'], [READING_MODE_STORAGE_KEY, mode]
    ])
  }
  const before = [...storage.values]
  assert.equal(writeReadingMode('invalid' as ReadingMode, () => storage), false)
  assert.deepEqual([...storage.values], before)
})

test('failed persistence never throws or prevents subsequent selections', () => {
  const storage = { getItem: () => null, setItem() { throw new Error('quota exceeded') } }
  for (const mode of ['comfortable', 'normal'] as const) {
    assert.equal(writeReadingMode(mode, () => storage), false)
  }
})
