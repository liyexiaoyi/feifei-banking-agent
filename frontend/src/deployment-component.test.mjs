import test from 'node:test'
import assert from 'node:assert/strict'
import {loadComponent,elements,textContent} from './component-test-loader.mjs'

test('deployment gate has only manual check, fixed copy, busy state and no side effects',async()=>{
  const {default:Gate}=await loadComponent('DeploymentGate.tsx')
  let retries=0
  for(const checking of [true,false]){
    const tree=Gate({checking,onRetry:()=>retries++})
    assert.equal(tree.props.role,'alert')
    assert.equal(tree.props['aria-busy'],checking)
    const buttons=elements(tree,'button')
    assert.equal(buttons.length,1)
    assert.equal(buttons[0].props.disabled,checking)
    assert.equal(retries,0)
    assert.match(textContent(tree),/不会自动重发付款/)
    assert.match(textContent(tree),checking?/正在核对本机版本/:/页面与服务暂未匹配/)
  }
  elements(Gate({checking:false,onRetry:()=>retries++}),'button')[0].props.onClick()
  assert.equal(retries,1)
})
