/** Only local case metadata. Closing a case cannot reconcile a payment. */
export type HandoffStatus='OPEN'|'IN_REVIEW'|'CLOSED'
export type HandoffEvent={id:string;from_status:HandoffStatus;to_status:Exclude<HandoffStatus,'OPEN'>;from_version:number;to_version:number;created_at:number}
export type HandoffReceipt=Omit<HandoffEvent,'id'>&{event_id:string}
export type HandoffProgressView={handoff_id:string;plan_id:string;status:HandoffStatus;version:number;updated_at:number;simulated:true;contacted_human:false;operations:{id:string;type:string;status:string}[];events:HandoffEvent[];receipt:HandoffReceipt|null;replayed:boolean}
export type HandoffTarget={handoff_id:string;plan_id:string}
type Obj=Record<string,unknown>
const obj=(v:unknown):v is Obj=>!!v&&typeof v==='object'&&!Array.isArray(v)
const id=(v:unknown):v is string=>typeof v==='string'&&/^[A-Za-z0-9_-]{1,150}$/.test(v)
const integer=(v:unknown):v is number=>Number.isSafeInteger(v)&&Number(v)>=0
const time=(v:unknown):v is number=>typeof v==='number'&&Number.isFinite(v)&&v>=0
const status=(v:unknown):v is HandoffStatus=>v==='OPEN'||v==='IN_REVIEW'||v==='CLOSED'
const moves=(from:unknown,to:unknown)=>from==='OPEN'&&(to==='IN_REVIEW'||to==='CLOSED')||from==='IN_REVIEW'&&to==='CLOSED'
const opStates=new Set(['WAITING_CONFIRMATION','WAITING_VERIFICATION','READY','SUCCEEDED','CANCELLED','EXPIRED','FAILED','UNKNOWN','EXECUTING','MANUAL_REVIEW'])
const opTypes=new Set(['transfer','subscription_query','subscription_cancel','card_report_lost','card_restore','virtual_card_create','wealth_purchase','wealth_redemption'])

export function parseHandoffProgress(raw:unknown,target:HandoffTarget):HandoffProgressView|null{
 if(!obj(raw)||!id(raw.handoff_id)||raw.handoff_id!==target.handoff_id||raw.plan_id!==target.plan_id||!id(raw.plan_id)||!status(raw.status)||!integer(raw.version)||raw.version>2||!time(raw.updated_at)||raw.simulated!==true||raw.contacted_human!==false||typeof raw.replayed!=='boolean'||!Array.isArray(raw.operations)||raw.operations.length>1000||!Array.isArray(raw.events)||raw.events.length!==raw.version)return null
 const operations:HandoffProgressView['operations']=[],opIds=new Set<string>()
 for(const op of raw.operations){
  if(!obj(op)||!id(op.id)||opIds.has(op.id)||typeof op.type!=='string'||!opTypes.has(op.type)||typeof op.status!=='string'||!opStates.has(op.status))return null
  opIds.add(op.id);operations.push({id:op.id,type:op.type,status:op.status})
 }
 const events:HandoffEvent[]=[],eventIds=new Set<string>()
 let previous:HandoffStatus='OPEN'
 for(const [index,event] of raw.events.entries()){
  if(!obj(event)||!id(event.id)||eventIds.has(event.id)||event.from_status!==previous||!status(event.to_status)||event.to_status==='OPEN'||!moves(previous,event.to_status)||event.from_version!==index||event.to_version!==index+1||!time(event.created_at))return null
  events.push({id:event.id,from_status:previous,to_status:event.to_status,from_version:index,to_version:index+1,created_at:event.created_at});eventIds.add(event.id);previous=event.to_status
 }
 if(previous!==raw.status||events.length&&raw.updated_at!==events.at(-1)!.created_at)return null
 let receipt:HandoffReceipt|null=null
 if(raw.receipt!==null){
  if(!obj(raw.receipt))return null
  const r=raw.receipt,event=events.find(e=>e.id===r.event_id)
  if(!event||(['from_status','to_status','from_version','to_version','created_at'] as const).some(k=>r[k]!==event[k])||!raw.replayed&&event!==events.at(-1))return null
  const {id:event_id,...rest}=event;receipt={event_id,...rest}
 }
 if(raw.replayed&&!receipt)return null
 return {handoff_id:raw.handoff_id,plan_id:raw.plan_id,status:raw.status,version:raw.version,updated_at:raw.updated_at,simulated:true,contacted_human:false,operations,events,receipt,replayed:raw.replayed}
}
