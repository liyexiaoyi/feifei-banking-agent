import test from 'node:test'
import assert from 'node:assert/strict'
import {loadComponent,elements,textContent} from './component-test-loader.mjs'
const m=await loadComponent('BirthdayFlowCard.tsx').catch(()=>null)
const id='a'.repeat(32)
function tree(p={}){assert(m?.default,'birthday flow reference card missing');return m.default({flowId:id,blocked:false,onOpen(){},...p})}
test('chat reference renders no cached financial status and opens only on explicit click',()=>{const seen=[];const t=tree({onOpen:x=>seen.push(x)});assert.equal(seen.length,0);assert.match(textContent(t),/生日安排/);const b=elements(t,'button').find(x=>textContent(x)==='查看生日安排');assert(b&&!b.props.disabled);b.props.onClick();assert.deepEqual(seen,[id]);assert.doesNotMatch(textContent(t),/付款成功|预订成功|已送达|预算已预留/);assert(!elements(t,'button').some(x=>/确认|验证/.test(textContent(x))))})
for(const bad of [null,'','../other','op_'+id,'<script>','b'.repeat(31)])test('invalid or missing reference is inert '+String(bad),()=>{let opened=0;const t=tree({flowId:bad,onOpen:()=>opened++});for(const b of elements(t,'button')){assert(b.props.disabled);b.props.onClick?.()}assert.equal(opened,0);assert.match(textContent(t),/核对|无法|未找到/);assert(!textContent(t).includes('<script>'))})
test('global busy gate blocks even a direct reference button callback',()=>{let n=0;const b=elements(tree({blocked:true,onOpen:()=>n++}),'button')[0];assert(b?.props.disabled);b.props.onClick();assert.equal(n,0)})
