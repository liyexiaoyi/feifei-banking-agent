import type {TaskPlan,LocalHandoff} from './task-plan'
import type { ToolHealth } from './recovery'
import type {BankCard} from './cards'
import type {Product,WealthSnapshot} from './wealth-types'
import {deployment,DeploymentController,DeploymentError} from './deployment.ts'
import {createSessionContext} from './session-context.ts'
export type Account={id:string;name:string;masked_number:string;balance_cents:number;reserved_cents:number;available_cents:number}
export type Subscription={id:string;merchant:string;amount_cents:number;next_charge:string;status:string;direct_cancel:number;description:string}
export type Operation={id:string;type:string;status:string;risk:string;created_at:number;expires_at:number;payload:{product_id?:string;product_name?:string;units_1e4?:number;nav_cents?:number;quote_version?:number;account_name?:string;payee_id?:string;payee_name?:string;payee_phone?:string;amount_cents?:number;fee_cents?:number;merchant?:string;scope?:string;effect?:string;proposed_cancel?:string;card_id?:string;account_id?:string;card_name?:string;masked_number?:string;action?:string;expected_version?:number;from_status?:string;to_status?:string;simulated?:boolean};result:null|{product_id?:string;action?:string;units_1e4?:number;position_units_1e4?:number;cost_cents?:number;balance_cents?:number;account_id?:string;masked_number?:string;transaction_id?:string;amount_cents?:number;subscriptions?:Subscription[];subscription_status?:string;card_id?:string;status?:string;version?:number;message?:string;simulated?:boolean};notice?:string;next_operation?:Operation}
export type Transaction={id:string;kind:string;amount_cents:number;category:string;merchant:string;occurred_at:string}
export type Bill={period:string;expense_cents:number;refund_cents:number;income_cents:number;net_expense_cents:number;previous_net_expense_cents:number;categories:{name:string;amount_cents:number}[];transactions:Transaction[];evidence_ids:string[]}
export type SafetyFinding={codes:string[];reasons:string[];advice:string;hold:boolean}
export type SafetyHold={id:string;codes:string[];reasons:string[];advice:string;active:boolean}
export type MemoryNote={id:string;content:string;source:'user_explicit';tags:string[];version:number;created_at:string;updated_at:string;related_ids:string[]}
export type Card={type:'birthday_flow';flow_id:string|null;simulated:true;merchant_connected:false}|{type:'wealth';wealth:WealthSnapshot}|{type:'wealth_selection';products:Product[];accounts:Account[];simulated:true}|{type:'virtual_accounts';accounts:Account[];simulated:true}|{type:'bank_cards';cards:BankCard[];simulated:true}|{type:'memory';action:'saved'|'recalled'|'removed';notes:MemoryNote[]}|{type:'safety';finding:SafetyFinding;hold_id:string|null}|{type:'bill';bill:Bill}|{type:'operation';operation:Operation}|{type:'balance';accounts:Account[]}|{type:'payees';selection_key?:string;safety_epoch?:string;candidates:{id:string;name:string;phone:string;remark:string}[];amount_cents:number}
export type ChatResult={message:string;cards:Card[];planner_mode:string;tasks:{type:string;state:string;reply_source?:'template'|'guarded'|'model_chat'|'rules_chat'}[]}
export type Message={id:number;role:'user'|'assistant';content:string|ChatResult;created_at:string}
export type State={name:string;accounts:Account[];operations:Operation[];business_date:string;locked:boolean;simulated:boolean;planner_mode:string;safety_hold:SafetyHold|null;safety_epoch:string;memory_count:number;tools:ToolHealth[];plans:TaskPlan[];handoffs:LocalHandoff[]}
export type Audit={id:number;operation_id:string|null;event:string;detail:string;created_at:string}

export class ApiError extends Error{
  code:string;status:number
  constructor(code:string,message:string,status:number){super(message);this.code=code;this.status=status}
}
const OWNER='X-FF-Session-Owner'
const FAILED='请求未完成，请检查输入或稍后重试。'
const isOwner=(v:unknown):v is 'demo'|'guest'=>v==='demo'||v==='guest'
export function createApiClient(gate:DeploymentController,fetcher:typeof fetch=fetch,session=createSessionContext()){
  let csrf=''
  const fail=(epoch:number):never=>{session.block();session.assertCurrent(epoch);throw new Error('SESSION_CHANGED')}
  // Private sentinel: an unsent automatic default POST was superseded by a legitimate first adoption at the same epoch.
  const superseded=new Error('BOOTSTRAP_SUPERSEDED')
  const guard=(epoch:number,autoOnlyUnknown:boolean)=>{session.assertCurrent(epoch);if(autoOnlyUnknown&&session.snapshot().status!=='unknown')throw superseded}
  async function request<T>(path:string,body:unknown,autoOnlyUnknown=false):Promise<T>{
    const epoch=session.snapshot().epoch
    const startedOwned=session.snapshot().status==='owned'
    guard(epoch,autoOnlyUnknown)
    const identity=await gate.requireReady(body!==undefined).catch((error:unknown)=>{session.assertCurrent(epoch);throw error})
    guard(epoch,autoOnlyUnknown)
    const open=body===undefined&&(path==='/health'||path.startsWith('/health?'))
    let response:Response,data:any
    try{response=await fetcher('/api'+path,{method:body===undefined?'GET':'POST',credentials:'same-origin',redirect:'error',headers:body===undefined?{}:{'Content-Type':'application/json','X-CSRF-Token':csrf,'X-Build-Identity':identity},body:body===undefined?undefined:JSON.stringify(body)})}catch(error){session.assertCurrent(epoch);throw error}
    const owner=open?null:response.headers.get(OWNER)
    const expired=!open&&response.status===401&&(startedOwned||isOwner(owner))
    if(owner!==null){if(isOwner(owner))session.observe(owner);else session.block()}
    session.assertCurrent(epoch)
    if(expired)fail(epoch)
    try{data=await response.json()}catch(error){session.assertCurrent(epoch);throw error}
    session.assertCurrent(epoch)
    const proven=isOwner(owner)
    if(!response.ok){
      if(data?.error?.code==='DEPLOYMENT_MISMATCH'){gate.block();throw new DeploymentError()}
      if(expired)fail(epoch)
      throw new ApiError(data?.error?.code||'REQUEST_FAILED',((open||proven)&&data?.error?.message)||FAILED,response.status)
    }
    if(!open&&(!proven||(path==='/session'&&(data?.user!==owner||typeof data?.csrf!=='string'))))fail(epoch)
    return data as T
  }
  function api<T>(path:string,body?:unknown):Promise<T>{return request<T>(path,body)}
  async function bootstrap(){
    const epoch=session.snapshot().epoch
    session.assertCurrent(epoch)
    const ready=await gate.check().catch((error:unknown)=>{session.assertCurrent(epoch);throw error})
    session.assertCurrent(epoch)
    if(!ready)throw new DeploymentError()
    session.assertCurrent(epoch)
    let data:{csrf:string;user:string}
    try{data=await api<{csrf:string;user:string}>('/session')}catch(error){
      if(!(error instanceof ApiError)||error.status!==401)throw error
      session.assertCurrent(epoch)
      try{data=await request<{csrf:string;user:string}>('/session',{user:'demo'},true)}catch(error){
        if(error!==superseded)throw error
        session.assertCurrent(epoch)
        // Confirm the newly adopted owner and csrf once; owner proof is enforced inside api.
        data=await api<{csrf:string;user:string}>('/session')
      }
    }
    session.assertCurrent(epoch)
    csrf=data.csrf
  }
  // Per-client coalescing: concurrent callers share one handshake; settle clears the promise only.
  let pending:Promise<void>|null=null
  function initialize():Promise<void>{
    if(!pending){const run=bootstrap();pending=run;const clear=()=>{if(pending===run)pending=null};run.then(clear,clear)}
    return pending
  }
  return {api,initialize,session}
}
const client=createApiClient(deployment)
export const api=client.api
export const initialize=client.initialize
export const sessionContext=client.session
export const requestKey=()=>crypto.randomUUID()
export const money=(cents:number)=>new Intl.NumberFormat('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}).format(cents/100)
