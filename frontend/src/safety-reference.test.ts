import test from 'node:test'
import assert from 'node:assert/strict'
import {parseSafetyReferences,loadSafetyReferences} from './safety-reference.ts'

const codes=['LOAN_ADVANCE_FEE','REFUND_ADVANCE_PAYMENT','FAKE_CANCELLATION_PAYMENT','REMOTE_ACCESS','TASK_RECHARGE','SAFE_ACCOUNT','SECRET_DISCLOSURE','CREDIT_REPAIR_PAYMENT','INVESTMENT_QUALIFICATION_PAYMENT','REGULATORY_ACCOUNT_TRANSFER']
const fixture=()=>({schema_version:'1',reference_only:true,items:codes.map(code=>({code,title:'风险说明',why:'用于核对线索',checks:['独立打开官方应用核实'],reference_kind:'rule_explanation',cases:[]}))})

test('parser accepts only complete reference data and returns fresh copies',()=>{
 const value=fixture(),parsed=parseSafetyReferences(value)
 assert.ok(parsed);value.items[0].checks.push('changed')
 assert.equal(parsed.items[0].checks.length,1)
 for(const patch of [{schema_version:2},{reference_only:false},{items:[]},{items:[...fixture().items.slice(1),fixture().items[1]]}])assert.equal(parseSafetyReferences({...fixture(),...patch}),null)
})

test('parser rejects unsafe case sources and invented risk codes',()=>{
 for(const url of ['javascript:alert(1)','http://www.huadu.gov.cn/case','https://evil.example/case','https://user:pass@www.huadu.gov.cn/case','https://www.huadu.gov.cn:9999/case']){
  const value:any=fixture();value.items[0].reference_kind='case_pattern';value.items[0].cases=[{id:'C01',title:'归档案例',publisher:'公开机构',url,archived_date:'2026-10-25'}]
  assert.equal(parseSafetyReferences(value),null)
 }
 const value:any=fixture();value.items[0].code='UNKNOWN';assert.equal(parseSafetyReferences(value),null)
})

test('new reviewed source hosts are allowed exactly and cannot widen source trust',()=>{
 const hosts=['gaj.wuhan.gov.cn','www.jiyuan.gov.cn','gaj.pds.gov.cn']
 const check=(url:string)=>{
  const value:any=fixture()
  value.items[5].reference_kind='case_pattern'
  value.items[5].cases=[{id:'C09',title:'已核对的公开报道',publisher:'公开机构',url,archived_date:'2026-10-26'}]
  return parseSafetyReferences(value)
 }
 for(const host of hosts){
  assert.ok(check(`https://${host}/case.html`),host)
  for(const url of [`https://${host}.evil.example/case.html`,`https://evil.${host}/case.html`,`https://${host}@evil.example/case.html`,`http://${host}/case.html`,`https://${host}:8443/case.html`,`https://${host}/case.html?next=evil`,`https://${host}/case.html#fake`]){
   assert.equal(check(url),null,url)
  }
 }
 assert.equal(check('https://www.other.gov.cn/case.html'),null)
})

test('load is read-only and failure does not reuse a prior successful reference',async()=>{
 const paths:string[]=[]
 assert.ok(await loadSafetyReferences(async(path:string)=>{paths.push(path);return fixture()}))
 assert.equal(await loadSafetyReferences(async(path:string)=>{paths.push(path);throw Error('unavailable')}),null)
 assert.deepEqual(paths,['/safety/references','/safety/references'])
})

test('a reference request that never settles cannot hold workspace loading indefinitely',async()=>{
 let finish:(value:unknown)=>void=()=>{}
 const request=new Promise<unknown>(resolve=>{finish=resolve})
 let guardTimer:ReturnType<typeof setTimeout>|undefined
 try{
  const result=await Promise.race([
   loadSafetyReferences(()=>request,10),
   new Promise(resolve=>{guardTimer=setTimeout(()=>resolve('blocked'),80)}),
  ])
  assert.equal(result,null)
  finish(fixture())
  assert.equal(result,null)
 }finally{clearTimeout(guardTimer)}
})
