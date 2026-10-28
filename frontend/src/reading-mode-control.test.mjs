import test from 'node:test'
import assert from 'node:assert/strict'
import {loadComponent,elements,textContent} from './component-test-loader.mjs'
const { default: ReadingModeControl } = await loadComponent('ReadingModeControl.tsx')

for (const mode of ['normal', 'comfortable']) {
  test(`controlled buttons reflect ${mode} and emit only the chosen mode`, () => {
    const calls = []
    const tree = ReadingModeControl({ mode, onChange: (...args) => calls.push(args) })
    assert.equal(tree.props.role, 'group')
    assert.equal(tree.props['aria-label'], '阅读方式')
    const buttons = elements(tree, 'button')
    assert.equal(buttons.length, 2)
    assert.deepEqual(buttons.map(textContent), ['标准', '舒适大字'])
    assert.deepEqual(buttons.map(button => button.props['aria-pressed']), [mode === 'normal', mode === 'comfortable'])
    for (const button of buttons) {
      assert.equal(button.props.type, 'button')
      button.props.onClick({ type: 'click' })
    }
    assert.deepEqual(calls, [['normal'], ['comfortable']])
    assert.deepEqual(buttons.map(button => button.props['aria-pressed']), [mode === 'normal', mode === 'comfortable'])
  })
}

test('control neither accesses storage nor performs business requests', () => {
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, 'window')
  const originalFetch = Object.getOwnPropertyDescriptor(globalThis, 'fetch')
  const calls = []
  try {
    Object.defineProperty(globalThis, 'window', { configurable: true, get() { throw new Error('Unexpected browser access') } })
    Object.defineProperty(globalThis, 'fetch', { configurable: true, value() { throw new Error('Unexpected request') } })
    const tree = ReadingModeControl({ mode: 'normal', onChange: mode => calls.push(mode) })
    elements(tree, 'button')[1].props.onClick()
    const updated = ReadingModeControl({ mode: calls[0], onChange() {} })
    assert.deepEqual(elements(updated, 'button').map(button => button.props['aria-pressed']), [false, true])
    assert.deepEqual(calls, ['comfortable'])
  } finally {
    if (originalWindow) Object.defineProperty(globalThis, 'window', originalWindow)
    else delete globalThis.window
    if (originalFetch) Object.defineProperty(globalThis, 'fetch', originalFetch)
    else delete globalThis.fetch
  }
})
