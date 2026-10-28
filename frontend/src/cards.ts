import type {Operation} from './api'
import {parseVirtualApplications} from './virtual-applications.ts'

export type CardAction='report_lost'|'restore'
export type BankCard={id:string;account_id:string;name:string;masked_number:string;
  status:'ACTIVE'|'LOST';version:number;simulated:true}
export type CardsLoad={cards:BankCard[]|null;error:string;applications?:Operation[]|null;applicationError?:string}
type Reader=(path:string)=>Promise<unknown>

export async function loadCards(api:Reader):Promise<CardsLoad>{
  try{
    const value=await api('/cards') as {cards?:unknown;simulated?:unknown;virtual_applications?:unknown}
    if(!value||value.simulated!==true||!Array.isArray(value.cards)||value.cards.length>1000)throw new Error('invalid cards')
    const ids=new Set<string>()
    const cards=value.cards.map((item:unknown)=>{
      if(!item||typeof item!=='object')throw new Error('invalid card')
      const row=item as Record<string,unknown>
      for(const key of ['id','account_id','name','masked_number'])
        if(typeof row[key]!=='string'||!row[key].length||row[key].length>300)throw new Error('invalid field')
      if((row.status!=='ACTIVE'&&row.status!=='LOST')||row.simulated!==true||
        !Number.isSafeInteger(row.version)||(row.version as number)<1||ids.has(row.id as string))throw new Error('invalid state')
      ids.add(row.id as string)
      return {id:row.id,account_id:row.account_id,name:row.name,masked_number:row.masked_number,
        status:row.status,version:row.version,simulated:true} as BankCard
    })
    if(Object.prototype.hasOwnProperty.call(value,'virtual_applications')){
      const applications=parseVirtualApplications(value.virtual_applications)
      return {cards,error:'',applications,applicationError:applications===null?'暂时无法核对虚拟卡申请，请刷新。':''}
    }
    return {cards,error:''}
  }catch{
    return {cards:null,error:'卡片信息暂时不可用，请刷新核对。'}
  }
}

/** Recover a matching server record after refresh instead of creating a new operation. */
export function findCardIntent(operations:Operation[],card:BankCard,action:CardAction):Operation|null{
  const type=action==='report_lost'?'card_report_lost':'card_restore'
  return operations.find(op=>op.type===type&&op.payload.card_id===card.id&&
    op.payload.action===action&&op.payload.expected_version===card.version&&
    !['CANCELLED','EXPIRED','FAILED'].includes(op.status))||null
}
