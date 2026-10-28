import test from 'node:test'
import assert from 'node:assert/strict'
import {loadCards,findCardIntent} from './cards.ts'
import type {BankCard} from './cards.ts'
import type {Operation} from './api.ts'

const card:BankCard={id:'card_a_daily',account_id:'a_daily',name:'日常演示卡',
  masked_number:'DEMO ···· 0826',status:'ACTIVE',version:1,simulated:true}
const response=(value:unknown)=>async()=>value
const operation=(status:string,changes:Record<string,unknown>={}):Operation=>({id:'op_one',type:'card_report_lost',
  status,risk:'RED',created_at:1,expires_at:999,payload:{card_id:card.id,action:'report_lost',expected_version:1,...changes},result:null})

test('卡片查询只采用已核对的模拟字段，不把额外资料带进界面',async()=>{
  const paths:string[]=[]
  const result=await loadCards(async path=>{paths.push(path);return {cards:[{...card,pan:'untrusted-extra'}],simulated:true}})
  assert.deepEqual(paths,['/cards'])
  assert.deepEqual(result,{cards:[card],error:''})
  assert.equal('pan' in result.cards![0],false)
})

test('空卡片列表与网络失败分别显示，不将失败伪装成无卡',async()=>{
  assert.deepEqual(await loadCards(response({cards:[],simulated:true})),{cards:[],error:''})
  const result=await loadCards(async()=>{throw new Error('private diagnostics')})
  assert.equal(result.cards,null)
  assert.ok(result.error)
  assert.doesNotMatch(result.error,/private diagnostics/)
})

test('未知状态、非演示数据、重复卡和无效版本均不能成为可操作卡',async()=>{
  for(const rows of [[{...card,status:'PENDING'}],[{...card,simulated:false}],
    [{...card,version:0}],[{...card,version:1.5}],[card,card]]){
    const result=await loadCards(response({cards:rows,simulated:true}))
    assert.equal(result.cards,null)
    assert.ok(result.error)
  }
  assert.equal((await loadCards(response({cards:[card],simulated:false}))).cards,null)
})

test('页面重新读取已有待核验/未知/成功操作后复用原记录，不准备第二份',()=>{
  for(const status of ['WAITING_CONFIRMATION','WAITING_VERIFICATION','UNKNOWN','EXECUTING','MANUAL_REVIEW','SUCCEEDED']){
    const op=operation(status)
    assert.equal(findCardIntent([op],card,'report_lost'),op)
  }
})

test('读取操作的版本、卡片和动作必须与用户当前选择一致',()=>{
  for(const changes of [{card_id:'foreign'},{action:'restore'},{expected_version:2}])
    assert.equal(findCardIntent([operation('WAITING_CONFIRMATION',changes)],card,'report_lost'),null)
  assert.equal(findCardIntent([{...operation('UNKNOWN'),type:'transfer'}],card,'report_lost'),null)
})

test('明确取消、过期或失败的旧操作不妨碍用户重新发起',()=>{
  for(const status of ['CANCELLED','EXPIRED','FAILED'])
    assert.equal(findCardIntent([operation(status)],card,'report_lost'),null)
})

test('未识别的已有状态仍先核对，不自动创建新请求',()=>{
  const op=operation('FUTURE_UNRECONCILED_STATE')
  assert.equal(findCardIntent([op],card,'report_lost'),op)
})

test('invalid application metadata disables virtual application while retaining current physical cards',async()=>{
 const result=await loadCards(response({cards:[card],simulated:true,virtual_applications:'invalid'}))
 assert.deepEqual(result.cards,[card])
 assert.equal(result.applications,null)
 assert.ok(result.applicationError)
 const empty=await loadCards(response({cards:[card],simulated:true,virtual_applications:[]}))
 assert.deepEqual(empty.applications,[])
 assert.equal(empty.applicationError,'')
})
