import test from 'node:test'
import assert from 'node:assert/strict'
import { loadComponent, elements, textContent } from './component-test-loader.mjs'

const base = {
  consecutive_failures: 0,
  retry_after_seconds: 0,
  probe_available: false,
  needs_handoff: false,
  version: 'v1',
}
const health = (over) => ({ ...base, group: 'bill', state: 'READY', ...over })

test('RecoveryBanner: 卡片管理有明确文本，探测绑定卡片功能', async () => {
  const { RecoveryBanner } = await loadComponent('RecoveryBanner.tsx')
  const probes=[]
  const tree=RecoveryBanner({tools:[health({group:'cards',state:'PAUSED',probe_available:true})],
    busy:false,onProbe:group=>probes.push(group),onHandoff:()=>{}})
  assert.match(textContent(tree),/卡片管理/)
  const button=elements(tree,'button')[0]
  button.props.onClick()
  assert.deepEqual(probes,['cards'])
})

test('RecoveryBanner: 全部 READY 时不渲染任何内容', async () => {
  const { RecoveryBanner } = await loadComponent('RecoveryBanner.tsx')
  const tree = RecoveryBanner({
    tools: [health({ group: 'planner' }), health({ group: 'bill' })],
    busy: false,
    onProbe: () => assert.fail('READY 不应触发探测'),
    onHandoff: () => {},
  })
  assert.equal(tree, null)
})

test('RecoveryBanner: PAUSED 且冷却未结束(probe_available=false)时不提供探测入口', async () => {
  const { RecoveryBanner } = await loadComponent('RecoveryBanner.tsx')
  let probes = 0
  const tree = RecoveryBanner({
    tools: [health({ state: 'PAUSED', retry_after_seconds: 45, probe_available: false })],
    busy: false,
    onProbe: () => { probes += 1 },
    onHandoff: () => {},
  })
  assert.equal(elements(tree, 'button').length, 0, '冷却中不应渲染探测按钮')
  assert.equal(probes, 0, '冷却中不应触发探测')
  const text = textContent(tree)
  assert.match(text, /上次核对时尚需约 45 秒/)
  assert.match(text, /账单查询/)
})

test('RecoveryBanner: 可探测时仅点击触发一次回调，busy 时禁用', async () => {
  const { RecoveryBanner } = await loadComponent('RecoveryBanner.tsx')
  const calls = []
  const props = {
    tools: [health({ state: 'PAUSED', probe_available: true })],
    busy: false,
    onProbe: (g) => calls.push(g),
    onHandoff: () => assert.fail('PAUSED 不应触发人工核查'),
  }
  const tree = RecoveryBanner(props)
  const [btn] = elements(tree, 'button')
  assert.ok(btn, '应渲染探测按钮')
  assert.equal(btn.props.disabled, false)
  assert.equal(calls.length, 0, '未点击不应回调')
  btn.props.onClick()
  assert.deepEqual(calls, ['bill'], '一次点击只回调一次且带上分组')
  const busyTree = RecoveryBanner({ ...props, busy: true })
  const [busyBtn] = elements(busyTree, 'button')
  assert.equal(busyBtn.props.disabled, true, 'busy 时按钮禁用')
})

test('RecoveryBanner: PROBING 按钮禁用，CHECK_REQUIRED 只有人工核查入口', async () => {
  const { RecoveryBanner } = await loadComponent('RecoveryBanner.tsx')
  const probing = RecoveryBanner({
    tools: [health({ state: 'PROBING' })],
    busy: false,
    onProbe: () => assert.fail('PROBING 不应触发探测'),
    onHandoff: () => {},
  })
  const [spinBtn] = elements(probing, 'button')
  assert.equal(spinBtn.props.disabled, true)
  assert.match(textContent(probing), /同一时间只检查一次/)

  const handoffs = []
  const check = RecoveryBanner({
    tools: [health({ state: 'CHECK_REQUIRED', needs_handoff: true })],
    busy: false,
    onProbe: () => assert.fail('CHECK_REQUIRED 不应有探测回调'),
    onHandoff: (g) => handoffs.push(g),
  })
  const buttons = elements(check, 'button')
  assert.equal(buttons.length, 1, '只应有一个按钮')
  assert.match(textContent(buttons[0]), /查看核查记录/)
  assert.match(textContent(check), /历史记录和回执仍可正常查看/)
  buttons[0].props.onClick()
  assert.deepEqual(handoffs, ['bill'])
})

test('BillMissing: 显示暂不可用而非零支出，重试一次点击仅回调一次，busy 禁用', async () => {
  const mod = await loadComponent('BillMissing.tsx')
  const BillMissing = mod.default
  assert.equal(typeof BillMissing, 'function', '应有默认导出')
  let retries = 0
  const tree = BillMissing({ period: '2024年6月', busy: false, onRetry: () => { retries += 1 } })
  assert.equal(tree.props['aria-live'], 'polite')
  assert.equal(tree.props.className, 'bill-missing')
  const text = textContent(tree)
  assert.match(text, /暂时不可用/)
  assert.match(text, /回执仍然可以正常核对/)
  assert.doesNotMatch(text, /¥\s*0|0\.00/, '不应把缺失渲染成零支出金额')
  assert.match(text, /2024年6月/)
  const [btn] = elements(tree, 'button')
  assert.equal(btn.props.disabled, false)
  assert.equal(btn.props.style.minHeight, 44)
  assert.equal(retries, 0, '未点击不应回调')
  btn.props.onClick()
  assert.equal(retries, 1, '一次点击仅回调一次')
  const busyTree = BillMissing({ busy: true, onRetry: () => { retries += 1 } })
  const [busyBtn] = elements(busyTree, 'button')
  assert.equal(busyBtn.props.disabled, true, 'busy 时禁用重试')
  assert.equal(retries, 1, '禁用后不应再有新回调')
})


test('cooldown refresh is an explicit callback and disabled while busy',async()=>{
 const {RecoveryBanner}=await loadComponent('RecoveryBanner.tsx');const calls=[]
 const props={tools:[health({state:'PAUSED',retry_after_seconds:20})],busy:false,onProbe:()=>assert.fail('no automatic probe'),onHandoff:()=>{},onRefresh:g=>calls.push(g)}
 const tree=RecoveryBanner(props)
 assert.deepEqual(calls,[])
 const buttons=elements(tree,'button');assert.equal(buttons.length,1)
 assert.match(textContent(buttons[0]),/重新核对状态/)
 buttons[0].props.onClick();assert.deepEqual(calls,['bill'])
 assert.ok(elements(RecoveryBanner({...props,busy:true}),'button')[0].props.disabled)
})
