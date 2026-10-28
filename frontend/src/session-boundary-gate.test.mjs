import test from 'node:test'
import assert from 'node:assert/strict'
import {loadComponent,elements,textContent} from './component-test-loader.mjs'
const m=await loadComponent('SessionBoundaryGate.tsx').catch(()=>null)
function tree(onReload=()=>{}){assert(m?.default,'whole-workspace privacy gate absent');return m.default({onReload})}
test('gate contains no old account body and cannot claim original write never ran',()=>{const t=tree(),text=textContent(t);assert.match(text,/账户|核对/);assert.match(text,/记录|结果/);assert.doesNotMatch(text,/demo|guest|26,000|小一|未执行|未扣款/);assert.equal(elements(t,'textarea').length,0)})
test('only explicit native reload button runs callback and is keyboard sized',()=>{let n=0;const t=tree(()=>n++),bs=elements(t,'button');assert.equal(n,0);assert.equal(bs.length,1);assert.match(textContent(bs[0]),/重新加载页面/);assert.equal(bs[0].props.autoFocus,true);assert(Number(bs[0].props.style?.minHeight)>=48);bs[0].props.onClick();assert.equal(n,1)})
