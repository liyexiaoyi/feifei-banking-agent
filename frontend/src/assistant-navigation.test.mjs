import test from 'node:test'
import assert from 'node:assert/strict'
import {loadComponent,elements,textContent} from './component-test-loader.mjs'
const m=await loadComponent('AssistantNavigation.tsx').catch(()=>null)
function tree(p={}){assert(m?.default,'assistant navigation missing');return m.default({currentView:'home',pendingCount:0,onNavigate:()=>{},...p})}
test('conversation and progress are primary and thirteen modules are not coequal navigation',()=>{const t=tree(),details=elements(t,'details');assert.equal(details.length,1);assert(!details[0].props.open);assert.match(textContent(details[0]),/更多参考/);assert.match(textContent(details[0]),/账单|卡片/);assert(!elements(details[0],'button').some(x=>textContent(x)==='和助手聊'));assert(elements(t,'button').some(x=>textContent(x).includes('和助手聊')))})
test('reference choice is explicit and never executes a business operation',()=>{let v='';const t=tree({onNavigate:x=>v=x});assert.equal(v,'');const b=elements(t,'button').find(x=>textContent(x).includes('账单'));assert(b);b.props.onClick();assert.equal(v,'bills')})
test('active conversation exposes current page semantics and original FF copy',()=>{const t=tree();assert(elements(t,'button').some(x=>x.props['aria-current']==='page'&&textContent(x).includes('和助手聊')));assert.doesNotMatch(textContent(t),/CordiBank|pi 路线|Cordis/)})
test('pending count is an operation count not fabricated goal percentage',()=>{const t=tree({pendingCount:3});assert.match(textContent(t),/3/);assert.doesNotMatch(textContent(t),/100%|已完成全部/);assert.doesNotMatch(textContent(tree({pendingCount:NaN})),/NaN/)})
