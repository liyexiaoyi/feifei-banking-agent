import test from 'node:test'
import assert from 'node:assert/strict'
import {loadComponent,elements,textContent} from './component-test-loader.mjs'

const m=await loadComponent('PendingNavigator.tsx').catch(()=>null)
function render(props={}){assert(m?.default,'pending navigator missing');return m.default({items:[],blocked:false,onLocate:()=>{},...props})}
function spy(){const calls=[];const fn=id=>{calls.push(id)};fn.calls=calls;return fn}
function nodes(tree){if(Array.isArray(tree))return tree.flatMap(nodes);if(!tree||typeof tree!=='object')return [];return [tree,...nodes(tree.props?.children)]}
const rows=t=>elements(t,'li')
const buttons=t=>elements(t,'button')
const spanOf=(row,cls)=>elements(row,'span').find(s=>s.props.className===cls)
const amounts=t=>elements(t,'span').filter(s=>s.props.className==='pending-nav__amount')
const base=[
  {id:'task-1',title:'房租转账',status:'WAITING_CONFIRMATION',amount_cents:250000},
  {id:'task.2',title:'信用卡还款',status:'WAITING_VERIFICATION'},
  {id:'task:3',title:'水电缴费',status:'UNKNOWN'},
  {id:'task_4',title:'基金申购',status:'EXECUTING'}
]

test('named navigation shows the true state of each supported item',()=>{
  const t=render({items:base})
  assert.equal(t.type,'nav')
  assert.equal(t.props['aria-label'],'待处理事项')
  assert.equal(t.props.className,'pending-nav')
  const lists=elements(t,'ul');assert.equal(lists.length,1);assert.equal(lists[0].props.className,'pending-nav__list')
  const r=rows(t);assert.equal(r.length,4)
  for(const row of r)assert.equal(row.props.className,'pending-nav__row')
  assert.deepEqual(r.map(x=>textContent(spanOf(x,'pending-nav__state'))),['等待确认','等待验证','结果待核对','处理中'])
  assert.deepEqual(buttons(t).map(b=>b.props['aria-label']),['定位 房租转账 等待确认','定位 信用卡还款 等待验证','定位 水电缴费 结果待核对','定位 基金申购 处理中'])
})

test('rendering never calls onLocate',()=>{
  const f=spy()
  render({items:base,onLocate:f})
  render({items:base,onLocate:f,blocked:true})
  render({items:[],onLocate:f})
  assert.deepEqual(f.calls,[])
})

test('clicking a locate button reports only that exact item id',()=>{
  const f=spy();const t=render({items:base,onLocate:f})
  for(const b of buttons(t)){assert.equal(b.props.type,'button');assert.equal(b.props.className,'pending-nav__button');assert.equal(b.props.disabled,false);assert.equal(textContent(b),'定位')}
  const b=buttons(t).find(x=>x.props['aria-label'].includes('信用卡还款'))
  assert(b)
  b.props.onClick()
  assert.deepEqual(f.calls,['task.2'])
})

test('blocked navigation disables buttons and ignores direct handler calls',()=>{
  for(const blocked of [true,undefined,null,'false',0,1]){
    const f=spy();const bs=buttons(render({items:base,blocked,onLocate:f}))
    assert.equal(bs.length,4)
    for(const b of bs){assert.equal(b.props.disabled,true);b.props.onClick()}
    assert.deepEqual(f.calls,[],`blocked=${String(blocked)}`)
  }
})

test('unknown and unsupported statuses never offer confirm or other authorized actions',()=>{
  const f=spy()
  const t=render({onLocate:f,items:[
    {id:'u1',title:'转账',status:'UNKNOWN'},
    {id:'u2',title:'撤销',status:'CANCELLED'},
    {id:'u3',title:'授权',status:'waiting_confirmation'},
    {id:'u4',title:'空',status:''},
    {id:'u5',title:'原型',status:'toString'},
    {id:'u6',title:'缺失'}
  ]})
  assert.equal(rows(t).length,1)
  const bs=buttons(t);assert.equal(bs.length,1)
  assert.equal(textContent(bs[0]),'定位')
  assert.doesNotMatch(bs[0].props['aria-label'],/确认|取消|撤销|执行|授权|批准|支付/)
  assert.doesNotMatch(textContent(t),/待确认|撤销|授权|原型|缺失/)
  const allowed=new Set(['type','className','disabled','aria-label','onClick','children'])
  for(const k of Object.keys(bs[0].props))assert(allowed.has(k),`unexpected button prop ${k}`)
  for(const n of nodes(t))for(const k of Object.keys(n.props??{}))assert.doesNotMatch(k,/confirm|cancel|approve|execute/i)
  bs[0].props.onClick()
  assert.deepEqual(f.calls,['u1'])
})

test('invalid items, ids and statuses are excluded and duplicate ids render once',()=>{
  const f=spy()
  const longOk='a'.repeat(100)
  const items=[null,undefined,'task-1',42,[],{},
    {id:'ok-1',title:'首个',status:'EXECUTING'},
    {id:'ok-1',title:'重复',status:'UNKNOWN'},
    {id:'has space',title:'空格',status:'EXECUTING'},
    {id:'',title:'空编号',status:'EXECUTING'},
    {id:'a'.repeat(101),title:'超长',status:'EXECUTING'},
    {id:longOk,title:'长编号',status:'EXECUTING'},
    {id:'<img>',title:'标签',status:'EXECUTING'},
    {id:'中文',title:'中文编号',status:'EXECUTING'},
    {id:'a/b',title:'斜杠',status:'EXECUTING'},
    {id:7,title:'数字编号',status:'EXECUTING'},
    {id:'ok-2',title:'未知状态',status:'DONE'},
    {id:'ok-3',title:'原型状态',status:'__proto__'},
    {id:'dup',title:'先无效',status:'DONE'},
    {id:'dup',title:'后有效',status:'EXECUTING'}
  ]
  const t=render({items,onLocate:f})
  assert.equal(rows(t).length,2)
  assert.equal(buttons(t).length,2)
  const text=textContent(t)
  assert.match(text,/首个/);assert.match(text,/长编号/)
  assert.doesNotMatch(text,/重复|空格|空编号|超长|标签|中文编号|斜杠|数字编号|未知状态|原型状态|先无效|后有效/)
  for(const b of buttons(t))b.props.onClick()
  assert.deepEqual(f.calls,['ok-1',longOk])
  for(const bad of [null,undefined,'items',{length:1,0:base[0]}]){const e=render({items:bad});assert.equal(rows(e).length,0);assert.equal(buttons(e).length,0)}
})

test('titles render as plain text with a numberless fallback',()=>{
  const t=render({items:[
    {id:'t1',title:'<b>工资</b>',status:'EXECUTING'},
    {id:'t2',title:'   ',status:'EXECUTING'},
    {id:'t3',title:42,status:'EXECUTING'},
    {id:'t4',status:'EXECUTING'}
  ]})
  const r=rows(t)
  assert.deepEqual(r.map(x=>textContent(spanOf(x,'pending-nav__title'))),['<b>工资</b>','业务事项','业务事项','业务事项'])
  assert.equal(elements(t,'b').length,0)
  for(const n of nodes(t))assert.equal(n.props?.dangerouslySetInnerHTML,undefined)
  for(const row of r.slice(1))assert.doesNotMatch(textContent(row),/\d/)
})

test('amount shows only positive safe integer cents up to the cap as yuan',()=>{
  const valid=[[250000,'￥2500.00'],[5,'￥0.05'],[1,'￥0.01'],[123456,'￥1234.56'],[100000000,'￥1000000.00']]
  for(const [cents,expected] of valid){
    const t=render({items:[{id:'m1',title:'款项',status:'WAITING_CONFIRMATION',amount_cents:cents}]})
    const a=amounts(t);assert.equal(a.length,1);assert.equal(textContent(a[0]),expected)
    assert.equal((textContent(t).match(/￥/g)||[]).length,1)
  }
  const invalid=[0,-0,-1,1.5,0.1+0.2,Infinity,-Infinity,NaN,100000001,Number.MAX_SAFE_INTEGER,Number.MAX_SAFE_INTEGER+2,'100',100n,null,undefined,true,{valueOf(){return 100}}]
  for(const cents of invalid){
    const t=render({items:[{id:'m2',title:'款项',status:'WAITING_CONFIRMATION',amount_cents:cents}]})
    assert.equal(rows(t).length,1)
    assert.equal(amounts(t).length,0)
    const text=textContent(t)
    assert.doesNotMatch(text,/￥|NaN|Infinity|\d/,`amount ${String(cents)}`)
  }
  const mixed=render({items:[base[0],{...base[1],amount_cents:-5},{...base[2],amount_cents:999}]})
  assert.deepEqual(amounts(mixed).map(textContent),['￥2500.00','￥9.99'])
})
