import test from 'node:test'
import assert from 'node:assert/strict'
import { loadComponent, elements, textContent } from './component-test-loader.mjs'

const { default: CardsPanel } = await loadComponent('CardsPanel.tsx')
const active = {
  id: 'active-1', account_id: 'account-1', name: '演示卡甲',
  masked_number: '•••• 1234', status: 'ACTIVE', version: 1, simulated: true
}
const lost = {
  id: 'lost-1', account_id: 'account-1', name: '演示卡乙',
  masked_number: '•••• 5678', status: 'LOST', version: 2, simulated: true
}
const noop = () => {}
function render(overrides = {}) {
  return CardsPanel({
    cards: null, loading: false, busy: false, error: '',
    onRefresh: noop, onPrepare: noop, ...overrides
  })
}
const buttons = tree => elements(tree, 'button')
const buttonWithText = (tree, label) =>
  buttons(tree).find(button => textContent(button).includes(label))

test('an unrecognized card state does not offer restore or loss actions', () => {
  const tree=render({cards:[{...active,status:'UNRECONCILED'}]})
  assert.equal(buttonWithText(tree,'准备挂失'),undefined)
  assert.equal(buttonWithText(tree,'准备解挂'),undefined)
  assert.match(textContent(tree),/状态.*未知|核对状态/)
})

test('card display omits internal identifiers and raw version counters', () => {
  const text=textContent(render({cards:[{...active,id:'internal-card-9876',account_id:'internal-account-9876',version:987654}]}))
  assert.doesNotMatch(text,/internal-card-9876|internal-account-9876|987654/)
})

test('distinguishes loading, unknown cards, and an empty demo-card list', () => {
  const loading = textContent(render({ loading: true }))
  assert.match(loading, /正在核对/)
  assert.doesNotMatch(loading, /暂无演示卡/)

  assert.doesNotMatch(textContent(render({ cards: null })), /暂无演示卡/)
  assert.match(textContent(render({ cards: [] })), /暂无演示卡/)
})

test('an error does not expose card actions from stale cards', () => {
  const tree = render({ cards: [active], error: '演示错误' })
  assert.match(textContent(tree), /演示错误/)
  assert.equal(buttonWithText(tree, '准备挂失'), undefined)
  assert.equal(buttonWithText(tree, '准备解挂'), undefined)
})

test('shows card identity and status; actions prepare the matching simulation', () => {
  const calls = []
  const tree = render({
    cards: [active, lost],
    onPrepare: (card, action) => calls.push([card, action])
  })
  const text = textContent(tree)
  assert.match(text, /演示卡甲/)
  assert.match(text, /•••• 1234/)
  assert.match(text, /可用/)
  assert.match(text, /演示卡乙/)
  assert.match(text, /•••• 5678/)
  assert.match(text, /挂失/)
  assert.match(text, /模拟/)
  assert.match(text, /尚未执行/)
  assert.match(text, /后续.*确认/)
  assert.match(text, /模拟验证/)
  assert.match(text, /不退款|不会退款/)
  assert.match(text, /余额/)

  assert.deepEqual(calls, [])
  const reportButton = buttonWithText(tree, '准备挂失')
  const restoreButton = buttonWithText(tree, '准备解挂')
  assert.ok(reportButton?.props?.onClick)
  assert.ok(restoreButton?.props?.onClick)
  reportButton.props.onClick()
  restoreButton.props.onClick()
  assert.deepEqual(calls, [[active, 'report_lost'], [lost, 'restore']])
})

test('busy and loading disable writes and refresh', () => {
  let refreshCalls = 0
  const onRefresh = () => { refreshCalls += 1 }
  const normal = render({ cards: [], onRefresh })
  const refreshButton = buttons(normal).find(button => {
    const before = refreshCalls
    if (typeof button.props?.onClick === 'function') button.props.onClick()
    return refreshCalls > before
  })
  assert.ok(refreshButton)
  assert.equal(refreshCalls, 1)
  const refreshText = textContent(refreshButton)

  for (const state of [{ busy: true }, { loading: true }]) {
    const tree = render({ cards: [active], ...state })
    const writes = buttons(tree).filter(button =>
      ['准备挂失', '准备解挂'].some(label => textContent(button).includes(label))
    )
    if (state.busy) assert.ok(writes.length > 0)
    assert.ok(writes.every(button => button.props.disabled === true))

    const refreshTree = render({ cards: [], onRefresh, ...state })
    const disabledRefresh = buttons(refreshTree).find(
      button => textContent(button) === refreshText
    )
    assert.ok(disabledRefresh)
    assert.equal(disabledRefresh.props.disabled, true)
  }
})

test('user verification lock blocks card preparation while read refresh stays available',()=>{
 const tree=render({cards:[active],locked:true})
 const action=elements(tree,'button').find(b=>textContent(b).includes('准备挂失'))
 const refresh=elements(tree,'button').find(b=>textContent(b).includes('刷新卡片'))
 assert.ok(action.props.disabled);assert.equal(refresh.props.disabled,false)
})
