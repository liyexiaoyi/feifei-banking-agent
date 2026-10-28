import type {Operation} from './api'
import type {WealthInput} from './wealth-types'
const record=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v)
const text=(v:unknown):v is string=>typeof v==='string'&&v.length>0&&v.length<=300
const positive=(v:unknown):v is number=>typeof v==='number'&&Number.isSafeInteger(v)&&v>0
const finite=(v:unknown):v is number=>typeof v==='number'&&Number.isFinite(v)
export function parseWealthOperations(value:unknown):Operation[]|null{
 if(!Array.isArray(value)||value.length>1000)return null
 const ids=new Set<string>(),out:Operation[]=[]
 for(const row of value){
  if(!record(row)||!text(row.id)||ids.has(row.id)||!(row.type==='wealth_purchase'||row.type==='wealth_redemption')||row.risk!=='RED'||!text(row.status)||row.status.length>60||!finite(row.created_at)||!finite(row.expires_at)||!record(row.payload))return null
  const p=row.payload
  if(!text(p.account_id)||!text(p.account_name)||!text(p.product_id)||!text(p.product_name)||p.simulated!==true||p.fee_cents!==0||p.action!==(row.type==='wealth_purchase'?'purchase':'redeem'))return null
  if(!positive(p.amount_cents)||p.amount_cents>100000000||!positive(p.units_1e4)||!positive(p.nav_cents)||!positive(p.quote_version)||BigInt(p.amount_cents)*BigInt(10000)!==BigInt(p.units_1e4)*BigInt(p.nav_cents))return null
  let result:Operation['result']=null
  if(row.result!==null){
   if(!record(row.result))return null
   const clean:Record<string,unknown>={}
   for(const key of ['transaction_id','account_id','product_id','action','message']){
    if(key in row.result){if(!text(row.result[key]))return null;clean[key]=row.result[key]}
   }
   for(const key of ['amount_cents','units_1e4','position_units_1e4','cost_cents','balance_cents']){
    if(key in row.result){const n=row.result[key];if(typeof n!=='number'||!Number.isSafeInteger(n)||n<0)return null;clean[key]=n}
   }
   if('simulated' in row.result){if(row.result.simulated!==true)return null;clean.simulated=true}
   for(const key of ['account_id','product_id','action','amount_cents','units_1e4']){
    if(key in clean&&clean[key]!==p[key])return null
   }
   result=clean
  }
  ids.add(row.id)
  out.push({id:row.id,type:row.type as string,risk:'RED',status:row.status,created_at:row.created_at,expires_at:row.expires_at,payload:{account_id:p.account_id,account_name:p.account_name,product_id:p.product_id,product_name:p.product_name,action:p.action as string,amount_cents:p.amount_cents,units_1e4:p.units_1e4,nav_cents:p.nav_cents,quote_version:p.quote_version,fee_cents:0,simulated:true},result})
 }
 return out
}
export function findWealthIntent(ops:Operation[]|null,input:WealthInput):Operation|null{
 const target=ops?.filter(op=>op.type===(input.action==='purchase'?'wealth_purchase':'wealth_redemption')&&op.payload.account_id===input.account_id&&op.payload.product_id===input.product_id&&op.payload.action===input.action&&!['SUCCEEDED','CANCELLED','EXPIRED','FAILED'].includes(op.status))||[]
 return target.find(op=>!['WAITING_CONFIRMATION','WAITING_VERIFICATION','READY'].includes(op.status))||target[0]||null
}
export function mergeWealthOperations(snapshot:Operation[],apps:Operation[]|null):Operation[]{
 if(apps===null)return snapshot
 const map=new Map(apps.map(op=>[op.id,op])),seen=new Set(snapshot.map(op=>op.id))
 return [...snapshot.map(op=>map.get(op.id)||op),...apps.filter(op=>!seen.has(op.id))]
}
