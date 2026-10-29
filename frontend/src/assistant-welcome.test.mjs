import test from 'node:test'
import assert from 'node:assert/strict'
import {loadComponent,elements,textContent} from './component-test-loader.mjs'

const m=await loadComponent('AssistantWelcome.tsx').catch(()=>null)
const EXAMPLES=['查看本月账单','看看有哪些订阅','给妈妈转300元','妈妈生日预算500元帮我安排']
const fakeEvent={preventDefault(){},stopPropagation(){}}

function render(p={}){
  assert(m?.default,'assistant welcome missing')
  const calls=[]
  const t=m.default({name:'菲菲',disabled:false,onDraft:(...a)=>{calls.push(a)},...p})
  return {t,calls}
}
function nodes(t){
  if(Array.isArray(t))return t.flatMap(nodes)
  if(!t||typeof t!=='object')return []
  return [t,...nodes(t.props?.children)]
}
function buttonFor(t,text){
  const found=elements(t,'button').filter(b=>textContent(b).includes(text))
  assert.equal(found.length,1,'expected exactly one button for '+text)
  return found[0]
}

test('rendering never calls onDraft, enabled or disabled',()=>{
  for(const disabled of [false,true]){
    const {t,calls}=render({disabled})
    assert(elements(t,'button').length>0)
    assert.equal(calls.length,0)
  }
})

test('each of the four examples drafts its exact text once, only after click',()=>{
  for(const text of EXAMPLES){
    const {t,calls}=render()
    const b=buttonFor(t,text)
    assert.equal(calls.length,0)
    b.props.onClick(fakeEvent)
    assert.deepEqual(calls,[[text]])
  }
  assert.equal(elements(render().t,'button').length,4)
})

test('disabled marks every button disabled and direct onClick does not draft',()=>{
  const {t,calls}=render({disabled:true})
  const buttons=elements(t,'button')
  assert.equal(buttons.length,4)
  for(const b of buttons){
    assert.equal(b.props.disabled,true)
    b.props.onClick?.(fakeEvent)
  }
  assert.equal(calls.length,0)
  for(const b of elements(render({disabled:false}).t,'button'))assert.equal(b.props.disabled,false)
})

test('every button is type=button so nothing auto-submits a form',()=>{
  const buttons=elements(render().t,'button')
  assert.equal(buttons.length,4)
  for(const b of buttons)assert.equal(b.props.type,'button')
})

test('supplied display name is rendered as escaped text, never raw HTML',()=>{
  const name='<b onclick="x()">菲菲</b>&'
  const {t}=render({name})
  assert(textContent(t).includes(name))
  assert.equal(elements(t,'b').length,0)
  for(const p of [{},{name}])for(const n of nodes(render(p).t))assert.equal(n.props?.dangerouslySetInnerHTML,undefined)
})

test('greeting uses the supplied nickname without invented identity or gender',()=>{
  const greetingOf=p=>{
    const {t}=render(p)
    const heads=elements(t,'h3')
    assert.equal(heads.length,1,'expected exactly one greeting heading')
    assert.doesNotMatch(textContent(t),/小一|先生|女士|小姐/)
    return textContent(heads[0])
  }
  const named=greetingOf({name:'菲菲'})
  assert.equal(named,'你好，菲菲')
  assert.doesNotMatch(named,/小一|先生|女士|小姐/)
  const empty=greetingOf({name:''})
  assert.equal(empty,'你好')
  assert.doesNotMatch(empty,/小一|菲菲|先生|女士|小姐/)
})

test('accessible region and examples group are labelled and contain the examples',()=>{
  const {t}=render()
  const all=nodes(t)
  const region=all.find(n=>n.props?.['aria-label']==='开始对话'&&(n.type==='section'||n.props?.role==='region'))
  assert(region,'region labelled 开始对话 missing')
  const group=nodes(region).find(n=>n.props?.role==='group'&&n.props?.['aria-label']==='试试这样说')
  assert(group,'group labelled 试试这样说 missing')
  const inGroup=elements(group,'button').map(textContent).join('|')
  for(const text of EXAMPLES)assert(inGroup.includes(text),text+' not in group')
})

test('hint says drafts need explicit send and no button sends or pays',()=>{
  const {t,calls}=render()
  assert(textContent(t).includes('先填写，发送后再处理'))
  for(const b of elements(t,'button'))assert.doesNotMatch(textContent(b),/发送|支付|付款|确认/)
  buttonFor(t,'给妈妈转300元').props.onClick(fakeEvent)
  assert.deepEqual(calls,[['给妈妈转300元']])
})
