/** Partial reads may fail; history recovery must still finish before an intent commits. */
import type { Audit, Bill, ChatResult, MemoryNote, Message, Operation, State, Subscription } from './api'
import type { ToolGroup } from './recovery'

export type Reader=<T>(path:string)=>Promise<T>
export type WorkspaceLoad={state:State;bill:Bill|null;events:Audit[]|null;messages:Message[];memories:MemoryNote[]|null;subscriptions:Subscription[]|null;unavailableGroups:ToolGroup[];warnings:string[]}

export async function loadWorkspace(read:Reader,query:string,onState?:(state:State)=>void):Promise<WorkspaceLoad>{
  let state=await read<State>('/state')
  onState?.(state)
  const [bill,audit,history,notes,subscriptions]=await Promise.allSettled([
    read<Bill>('/bill?period='+state.business_date.slice(0,7)),
    read<{events:Audit[]}>('/audit'),
    read<{messages:Message[]}>('/history'),
    read<{notes:MemoryNote[]}>('/memories?query='+encodeURIComponent(query)),
    read<{subscriptions:Subscription[]}>('/subscriptions'),
  ])
  if(history.status==='rejected')throw history.reason
  const unavailableGroups:ToolGroup[]=[]
  const warnings:string[]=[]
  if(bill.status==='rejected')unavailableGroups.push('bill')
  if(subscriptions.status==='rejected'){
    const error=subscriptions.reason as {code?:string;status?:number}
    if(error.code!=='SUBSCRIPTION_CONSENT_REQUIRED')unavailableGroups.push('subscriptions')
  }
  if(audit.status==='rejected')warnings.push('操作记录暂未加载，现有记录仍需稍后核对。')
  if(notes.status==='rejected')warnings.push('记忆列表暂未加载，未将失败当作空笔记。')
  if(unavailableGroups.length||state.tools?.some(tool=>tool.state!=='READY')){
    state=await read<State>('/state')
    onState?.(state)
  }
  const present=new Set(state.operations.map(operation=>operation.id))
  const missing=new Set<string>()
  for(const message of history.value.messages){
    if(message.role!=='assistant')continue
    for(const card of (message.content as ChatResult).cards){
      if(card.type==='operation'&&!present.has(card.operation.id))missing.add(card.operation.id)
    }
  }
  const recovered=await Promise.all([...missing].map(id=>read<Operation>('/operations/'+encodeURIComponent(id))))
  state={...state,operations:[...state.operations,...recovered]}
  return {
    state,bill:bill.status==='fulfilled'?bill.value:null,
    events:audit.status==='fulfilled'?audit.value.events:null,
    messages:history.value.messages,memories:notes.status==='fulfilled'?notes.value.notes:null,
    subscriptions:subscriptions.status==='fulfilled'?subscriptions.value.subscriptions:null,
    unavailableGroups,warnings,
  }
}
/** Explicit retry renews an expired demo session, then reads; never replays a write. */
export async function retryWorkspaceSession(initialize:()=>Promise<void>,refresh:()=>Promise<void>){
  await initialize()
  await refresh()
}
