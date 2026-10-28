import type {WealthSnapshot} from './wealth-types'
import {parseWealthData} from './wealth-data.ts'
import {parseWealthOperations} from './wealth-operations.ts'
export type {Product,Position,WealthInput,WealthSnapshot} from './wealth-types'
export {findWealthIntent,mergeWealthOperations} from './wealth-operations.ts'

export async function loadWealth(api:(path:string)=>Promise<unknown>):Promise<WealthSnapshot|null>{
 try{
  const raw=await api('/wealth')
  const data=parseWealthData(raw)
  if(!data||!raw||typeof raw!=='object')return null
  const applications=parseWealthOperations((raw as Record<string,unknown>).applications)
  return applications===null?null:{...data,applications}
 }catch{return null}
}

export function parseWealthInputAmount(text:string):number|null{
 const matched=/^([0-9]+)(?:\.([0-9]{1,2}))?$/.exec(text.normalize('NFKC').trim())
 if(!matched)return null
 const cents=Number(matched[1])*100+Number((matched[2]||'').padEnd(2,'0'))
 return Number.isSafeInteger(cents)&&cents>0&&cents<=100000000?cents:null
}
