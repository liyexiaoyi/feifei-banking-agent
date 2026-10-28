import test from 'node:test'
import assert from 'node:assert/strict'
import {loadComponent,elements,textContent} from './component-test-loader.mjs'

const {default:Panel}=await loadComponent('VirtualCardsPanel.tsx')
const account={id:'hidden-account',name:'日常关联账户',masked_number:'DEMO 0826',balance_cents:10000,reserved_cents:0,available_cents:10000}
const card={id:'card_physical',account_id:account.id,name:'日常演示卡',masked_number:'DEMO 0826',status:'ACTIVE',version:9876,simulated:true}
const operation={id:'hidden-operation',type:'virtual_card_create',risk:'YELLOW',status:'UNKNOWN',created_at:100,expires_at:400,payload:{account_id:account.id,account_name:account.name,card_name:'虚拟演示卡',simulated:true},result:null}
const render=(extra={})=>Panel({accounts:[account],cards:[card],applications:[],loading:false,busy:false,error:'',onPrepare:()=>{},onReceipt:()=>{},onRefresh:()=>{},...extra})
const apply=tree=>elements(tree,'button').find(button=>textContent(button).includes('准备申请'))

test('a virtual application button only prepares the explicit current account',()=>{
 const calls=[];const tree=render({onPrepare:value=>calls.push(value)})
 assert.deepEqual(calls,[]);assert.ok(apply(tree))
 apply(tree).props.onClick();assert.deepEqual(calls,[account])
 const text=textContent(tree);assert.match(text,/模拟/);assert.match(text,/确认/)
 assert.doesNotMatch(text,/hidden-account|9876|已发卡成功/)
})

test('unknown application exposes a read-only receipt instead of a new apply button',()=>{
 const calls=[];const tree=render({applications:[operation],onReceipt:id=>calls.push(id)})
 assert.equal(apply(tree),undefined)
 assert.match(textContent(tree),/核实|核对/)
 const button=elements(tree,'button').find(b=>textContent(b).includes('已有申请'))
 assert.ok(button);button.props.onClick();assert.deepEqual(calls,[operation.id])
})

test('existing virtual card, unavailable data, loading, and errors never offer duplicate preparation',()=>{
 for(const extra of [{cards:[{...card,id:'vcard_one'}]},{applications:null},{cards:null},{accounts:null},{loading:true},{error:'查询失败'}]){
  assert.equal(apply(render(extra)),undefined)
 }
 assert.doesNotMatch(textContent(render({loading:true})),/日常关联账户/)
 assert.doesNotMatch(textContent(render({error:'查询失败'})),/日常关联账户/)
 assert.ok(elements(render({busy:true}),'button').every(b=>b.props.disabled))
})

test('user verification lock blocks virtual preparation but preserves receipt and read refresh',()=>{
 assert.ok(apply(render({locked:true})).props.disabled)
 const tree=render({locked:true,applications:[operation]})
 assert.ok(elements(tree,'button').every(b=>!b.props.disabled))
})
