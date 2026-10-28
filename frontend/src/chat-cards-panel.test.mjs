import test from 'node:test'
import assert from 'node:assert/strict'
import {loadComponent,elements,textContent} from './component-test-loader.mjs'

const {default:ChatCardsPanel}=await loadComponent('ChatCardsPanel.tsx')
const card={id:'private-card-id',account_id:'private-account-id',version:987654,name:'日常演示卡',masked_number:'DEMO ···· 0826',status:'ACTIVE',simulated:true}
const render=(extra={})=>ChatCardsPanel({cards:null,loading:false,error:'',onRefresh:()=>{},...extra})

test('chat card query is read only, exposes an explicit refresh, and never starts banking',()=>{
 const calls=[]
 const tree=render({cards:[card],onRefresh:()=>calls.push('refresh')})
 assert.deepEqual(calls,[])
 const buttons=elements(tree,'button')
 assert.equal(buttons.length,1)
 assert.match(textContent(buttons[0]),/刷新|核对/)
 buttons[0].props.onClick()
 assert.deepEqual(calls,['refresh'])
 const text=textContent(tree)
 assert.match(text,/模拟/);assert.match(text,/0826/);assert.match(text,/可用/)
 assert.doesNotMatch(text,/private-card-id|private-account-id|987654|挂失成功|解挂成功/)
})

test('error and loading cannot display supplied stale card records as current',()=>{
 for(const extra of [{loading:true},{error:'查询失败'}]){
  const text=textContent(render({cards:[card],...extra}))
  assert.doesNotMatch(text,/日常演示卡|0826/)
  assert.doesNotMatch(text,/暂无演示卡/)
 }
 assert.ok(elements(render({loading:true}),'button').every(b=>b.props.disabled))
 assert.notEqual(textContent(render()),textContent(render({cards:[]})))
})

test('lost and unknown states use clear text and have no action button',()=>{
 assert.match(textContent(render({cards:[{...card,status:'LOST'}]})),/已挂失/)
 const tree=render({cards:[{...card,status:'FUTURE_STATUS'}]})
 assert.match(textContent(tree),/未知|核对/)
 assert.equal(elements(tree,'button').length,1)
})
