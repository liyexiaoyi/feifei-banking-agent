import test from 'node:test'
import assert from 'node:assert/strict'
import {loadWealth,findWealthIntent,mergeWealthOperations,parseWealthInputAmount} from './wealth.ts'

const product={id:'demo_cash',name:'演示产品甲',nav_cents:100,quote_version:1,simulated:true}
const op={id:'op-wealth',type:'wealth_purchase',risk:'RED',status:'WAITING_CONFIRMATION',created_at:100,expires_at:400,payload:{account_id:'daily',account_name:'日常账户',product_id:product.id,product_name:product.name,action:'purchase',amount_cents:30000,units_1e4:3000000,nav_cents:100,quote_version:1,fee_cents:0,simulated:true},result:null}
const packet={products:[product],positions:[],applications:[op],simulated:true,valuation_note:'固定演示估值，不代表真实报价或预期收益。'}
const input={account_id:'daily',product_id:product.id,action:'purchase' as const,amount_cents:30000}
const position={account_id:'daily',account_name:'日常账户',product_id:product.id,product_name:product.name,units_1e4:3000000,cost_cents:30000,version:1,value_cents:30000,gain_cents:0,reserved_units_1e4:0,available_units_1e4:3000000,simulated:true}

test('wealth read failures and malformed application metadata never mean no holdings or pending requests',async()=>{
 assert.equal(await loadWealth(async()=>{throw new Error('private error')}),null)
 for(const raw of [null,{}, {...packet,simulated:false},{...packet,applications:undefined},{...packet,applications:[{...op,risk:'GREEN'}]},{...packet,products:[product,product]}])assert.equal(await loadWealth(async()=>raw),null)
 const empty=await loadWealth(async()=>({...packet,applications:[]}));assert.deepEqual(empty?.positions,[])
})

test('wealth DTOs retain only public selected data with exact integer valuation',async()=>{
 const raw={...packet,user_id:'hidden-user',products:[{...product,private_field:'drop'}],positions:[{...position,user_id:'drop'}],applications:[{...op,secret_field:'drop',payload:{...op.payload,confirmed:true}}]}
 const result=await loadWealth(async()=>raw);assert.ok(result);assert.equal(result.positions[0].value_cents,30000)
 assert.doesNotMatch(JSON.stringify(result),/secret_field|private_field|user_id|confirmed/)
 for(const changed of [{gain_cents:1},{available_units_1e4:1},{value_cents:1},{product_id:'other'},{units_1e4:true}])assert.equal(await loadWealth(async()=>({...packet,positions:[{...position,...changed}]})),null)
})

test('unresolved target applications block another prepare even if the amount changes',async()=>{
 for(const status of ['UNKNOWN','EXECUTING','MANUAL_REVIEW','FUTURE_STATE']){
  const snapshot=await loadWealth(async()=>({...packet,applications:[{...op,status}]}));assert.ok(snapshot)
  assert.equal(findWealthIntent(snapshot.applications,{...input,amount_cents:100})?.id,op.id)
 }
 const current=await loadWealth(async()=>packet);assert.equal(findWealthIntent(current!.applications,input)?.id,op.id)
 assert.equal(findWealthIntent(current!.applications,{...input,amount_cents:100})?.id,op.id)
 for(const status of ['SUCCEEDED','CANCELLED','EXPIRED','FAILED']){
  const current=await loadWealth(async()=>({...packet,applications:[{...op,status}]}))
  assert.equal(findWealthIntent(current!.applications,input),null)
 }
})

test('current records override a stale workspace and include an older unresolved request',async()=>{
 const current=await loadWealth(async()=>({...packet,applications:[{...op,status:'UNKNOWN'},{...op,id:'older'}]}))
 const merged=mergeWealthOperations([op],current!.applications)
 assert.deepEqual(merged.map(x=>[x.id,x.status]),[[op.id,'UNKNOWN'],['older','WAITING_CONFIRMATION']])
})

test('PC numeric amount entry is exact and never rounds ambiguous money',()=>{
 for(const [text,cents] of [['300',30000],['0.01',1],['300.50',30050],['１００．２５',10025],['1000000',100000000]] as const)assert.equal(parseWealthInputAmount(text),cents)
 for(const text of ['', '300多','300块五','300元5角','1,000','0','-1','1.001','1000000.01'])assert.equal(parseWealthInputAmount(text),null)
})

test('operation types must be strings rather than arrays that stringify like a known type',async()=>{
 const malformed={...op,type:['wealth_redemption'],payload:{...op.payload,action:'redeem'}}
 assert.equal(await loadWealth(async()=>({...packet,applications:[malformed]})),null)
})

test('unknown original application is the first recovery target even if a newer known pending exists',()=>{
 const known={...op,id:'newer-known'}
 const unknown={...op,id:'older-unknown',status:'UNKNOWN'}
 assert.equal(findWealthIntent([known,unknown],input)?.id,unknown.id)
})

test('a mismatched financial receipt cannot become a current application result',async()=>{
 const result={transaction_id:'tx-one',account_id:op.payload.account_id,product_id:op.payload.product_id,action:'purchase',amount_cents:30000,units_1e4:3000000,position_units_1e4:3000000,cost_cents:30000,balance_cents:100000,simulated:true,message:'模拟申购已完成'}
 assert.ok(await loadWealth(async()=>({...packet,applications:[{...op,status:'SUCCEEDED',result}]})))
 for(const change of [{account_id:'other'},{product_id:'other'},{action:'redeem'},{amount_cents:0},{units_1e4:1}])assert.equal(await loadWealth(async()=>({...packet,applications:[{...op,status:'SUCCEEDED',result:{...result,...change}}]})),null)
})
