import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { ArrowDownLeft, ArrowRight, ArrowUp, ArrowUpRight, BadgeCheck, Bell, BookOpen, Check, CheckCheck, ChevronRight, Clock3, FileText, Fingerprint, LoaderCircle, RefreshCw, ShieldCheck, Sparkles, Wallet, X } from 'lucide-react'
import { api, ApiError, initialize, money, sessionContext } from './api'
import { deployment } from './deployment'
import type {DeploymentState} from './deployment'
import DeploymentGate from './DeploymentGate'
import SessionBoundaryGate, { isSessionBlocked } from './SessionBoundaryGate'
import IntegrationStatusCard from './IntegrationStatusCard'
import AssistantNavigation from './AssistantNavigation'
import './assistant-workspace.css'
import './experience-home.css'
import SafetyReferencePanel from './SafetyReferencePanel'
import { loadSafetyReferences } from './safety-reference'
import type { SafetyReferenceSnapshot } from './safety-reference'
import {loadIntegrationStatus} from './integrations'
import ModelSettings from './ModelSettings'
import {LatestRefresh} from './latest-refresh'
import type {WorkspaceLoad} from './workspace'
import type {IntegrationSnapshot} from './integrations'
import { RequestCoordinator } from './requests'
import AssistantWelcome from './AssistantWelcome'
import CardsPanel from './CardsPanel'
import VirtualCardsPanel from './VirtualCardsPanel'
import WealthPanel from './WealthPanel'
import InvestmentProfileSection from './InvestmentProfileSection'
import DraftReplaceDialog from './DraftReplaceDialog'
import PendingNavigator from './PendingNavigator'
import { draftDecision, isNearLatest } from './conversation-experience'
import './experience-panels.css'
import SandboxSection from './SandboxSection'
import CodegenSection from './CodegenSection'
import ChannelInboxSection from './ChannelInboxSection'
import AARequestsSection from './AARequestsSection'
import CardControlSection from './CardControlSection'
import CardOperationSummary from './CardOperationSummary'
import {ScheduledSection} from './ScheduledSection'
import ScheduledSummary from './ScheduledSummary'
import {BirthdaySection} from './BirthdaySection'
import BirthdaySummary from './BirthdaySummary'
import BirthdayFlowCard from './BirthdayFlowCard'
import {parseBirthdayOperation} from './birthday-client'
import type {BirthdayOperation} from './birthday-client'
import {parseScheduleOperation} from './scheduled-client'
import type {ScheduleOperation} from './scheduled-client'
import {parseCardOperation} from './card-control-client'
import type {CardOperation} from './card-control-client'
import UserCaseCard from './UserCaseCard'
import type {ProfileStatus} from './investment-profile'
import {loadWealth,findWealthIntent,mergeWealthOperations} from './wealth'
import type {WealthSnapshot,WealthInput} from './wealth'
import {findVirtualIntent,mergeVirtualOperations} from './virtual-applications'
import {loadCards,findCardIntent} from './cards'
import type {BankCard,CardAction,CardsLoad} from './cards'
import {operationCopy} from './operation-copy'
import {normalizeVerificationCode} from './verification-input'
import RecoveryBanner from './RecoveryBanner'
import ReadingModeControl from './ReadingModeControl'
import { readReadingMode, writeReadingMode } from './reading-mode'
import type { ReadingMode } from './reading-mode'
import BillMissing from './BillMissing'
import AnnualBillSection from './AnnualBillSection'
import RecurringRemindersSection from './RecurringRemindersSection'
import ReceiptCheck from './ReceiptCheck'
import type {Receipt} from './ReceiptCheck'
import { loadWorkspace,retryWorkspaceSession } from './workspace'
import {TaskPlanView,LocalHandoffSummary} from './TaskPlanView'
import HandoffProgressCard from './HandoffProgressCard'
import ChatCardsPanel from './ChatCardsPanel'
import type {TaskPlan,PlanControlAction} from './task-plan'
import { planNeedsAttention } from './task-plan';
import type { ToolGroup } from './recovery'
import type { Account, Audit, Bill, Card, ChatResult, MemoryNote, Message, Operation, State, Subscription } from './api'
import ConversationGoalPanel from './ConversationGoalPanel'
import type { ConversationGoalView } from './ConversationGoalPanel'
import GoalControlsPanel from './GoalControlsPanel'
import { goalOperationBinding } from './goal-operation-binding'
import { createGoalControlClient } from './goal-control-client'
import type { GoalControlAction, GoalControlView } from './goal-control-client'
import { createGoalControlController } from './goal-control-controller'
import type { GoalControlState } from './goal-control-controller'
type GoalResult=ChatResult&{goal:ConversationGoalView|null}
type GoalController=ReturnType<typeof createGoalControlController>
type GoalControlIntent={goal:GoalControlView;action:GoalControlAction}
const emptyGoalControl:GoalControlState={paused:[],busy:false,unknown:false,error:''}
function sessionOwnerKey(u:unknown):string{if(typeof u==='string')return u;const id=(u as {id?:unknown}|null)?.id;return typeof id==='string'||typeof id==='number'?String(id):''}

type View='home'|'bills'|'subscriptions'|'cards'|'wealth'|'audit'|'memory'|'tasks'|'compute'|'aa'|'scheduled'|'birthday'|'channel'
const labels:Record<string,string>={WAITING_CONFIRMATION:'等待确认',WAITING_VERIFICATION:'等待验证',SUCCEEDED:'已完成',CANCELLED:'已取消',EXPIRED:'已过期',FAILED:'执行失败',UNKNOWN:'结果待核实',MANUAL_REVIEW:'需要人工核对',EXECUTING:'执行中'}
const opName=(op:Operation)=>operationCopy(op.type).title
const categoryColors=['#547d69','#a9bc9e','#d6b988','#9cabc2','#c68d79']

export default function App(){
  const [readingMode,setReadingMode]=useState<ReadingMode>(()=>readReadingMode())
  function changeReadingMode(mode:ReadingMode){
    setReadingMode(mode)
    writeReadingMode(mode)
  }
  const [deploymentState,setDeploymentState]=useState<DeploymentState>(deployment.state)
  const [sessionSnapshot,setSessionSnapshot]=useState(()=>sessionContext.snapshot())
  const [integrationStatus,setIntegrationStatus]=useState<IntegrationSnapshot|null>(null)
  const [integrationLoading,setIntegrationLoading]=useState(true)
  const [reference,setReference]=useState<SafetyReferenceSnapshot|null>(null)
  const [referenceLoading,setReferenceLoading]=useState(true)
  const refreshes=useRef(new LatestRefresh())
  const [view,setView]=useState<View>('home')
  const [state,setState]=useState<State|null>(null)
  const [bill,setBill]=useState<Bill|null>(null)
  const [selectedBill,setSelectedBill]=useState<Bill|null>(null)
  const [subs,setSubs]=useState<Subscription[]|null>(null)
  const [bankCards,setBankCards]=useState<BankCard[]|null>(null)
  const [bankCardsLoading,setBankCardsLoading]=useState(true)
  const [bankCardsError,setBankCardsError]=useState('')
  const [virtualApps,setVirtualApps]=useState<Operation[]|null>(null)
  const [virtualError,setVirtualError]=useState('')
  const [wealthSnapshot,setWealthSnapshot]=useState<WealthSnapshot|null>(null)
  const [wealthLoading,setWealthLoading]=useState(true)
  const [wealthError,setWealthError]=useState('')
  const [investmentStatus,setInvestmentStatus]=useState<ProfileStatus|null>(null)
  const [events,setEvents]=useState<Audit[]>([])
  const [messages,setMessages]=useState<Message[]>([])
  const [memories,setMemories]=useState<MemoryNote[]>([])
  const [memoryQuery,setMemoryQuery]=useState('')
  const [editingMemory,setEditingMemory]=useState('')
  const [memoryDraft,setMemoryDraft]=useState('')
  const [text,setText]=useState('')
  // Suggested-reply candidate awaiting explicit confirm; memory only, scoped to owner#epoch.
  const [draftCandidate,setDraftCandidate]=useState<{text:string;scope:string}|null>(null)
  const [hasUnread,setHasUnread]=useState(false)
  const followLatestRef=useRef(true)
  const messageCountRef=useRef(0)
  const locateFrameRef=useRef(0)
  const savedScrollRef=useRef(0)
  const [birthdayFlowId,setBirthdayFlowId]=useState<string|null>(null)
  const [goal,setGoal]=useState<ConversationGoalView|null>(null)
  const [sending,setSending]=useState(false)
  const [busy,setBusy]=useState('')
  const mutationOwner=useRef<symbol|null>(null)
  const goalControlRef=useRef<GoalController|null>(null)
  const goalControlsReadyRef=useRef(false)
  const goalControlStateRef=useRef<GoalControlState>(emptyGoalControl)
  const goalControlIntentRef=useRef<GoalControlIntent|null>(null)
  const goalControlAgreedRef=useRef(false)
  const [goalControlState,setGoalControlStateValue]=useState<GoalControlState>(emptyGoalControl)
  const [goalControlsReady,setGoalControlsReadyValue]=useState(false)
  const [goalControlIntent,setGoalControlIntentValue]=useState<GoalControlIntent|null>(null)
  const [goalControlAgreed,setGoalControlAgreedValue]=useState(false)
  // Only the explicit goal-control confirm may bypass the control gate.
  function acquireMutation(bypassControlGate=false){
    if(mutationOwner.current!==null)return null
    const control=goalControlStateRef.current
    if(!bypassControlGate&&(!goalControlsReadyRef.current||control.busy||control.unknown||goalControlIntentRef.current!==null))return null
    const token=Symbol()
    mutationOwner.current=token
    return token
  }
  function releaseMutation(token:symbol){
    if(mutationOwner.current!==token)return false
    mutationOwner.current=null
    return true
  }
  async function runProfileMutation(task:()=>Promise<void>){
    const token=acquireMutation()
    if(token===null)throw new Error('已有操作正在处理，请先核对。')
    setBusy('investment-profile')
    try{await task()}finally{if(releaseMutation(token))setBusy('')}
  }
  const [error,setError]=useState('')
  const [notice,setNotice]=useState('')
  const [loading,setLoading]=useState(true)
  const [unavailableGroups,setUnavailableGroups]=useState<ToolGroup[]>([])
  const [receiptId,setReceiptId]=useState('')
  const [receipt,setReceipt]=useState<Receipt|null>(null)
  const [receiptBusy,setReceiptBusy]=useState(false)
  const [receiptError,setReceiptError]=useState('')
  const receiptSequence=useRef(0)
  const receiptPanel=useRef<HTMLDivElement>(null)
  const conversation=useRef<HTMLDivElement>(null)
  const input=useRef<HTMLTextAreaElement>(null)
  const requests=useRef(new RequestCoordinator())

  function publishGoalControl(next:GoalControlState){goalControlStateRef.current=next;setGoalControlStateValue(next)}
  function setGoalControlsReady(v:boolean){goalControlsReadyRef.current=v;setGoalControlsReadyValue(v)}
  function clearGoalControlIntent(){goalControlIntentRef.current=null;goalControlAgreedRef.current=false;setGoalControlIntentValue(null);setGoalControlAgreedValue(false)}
  function goalControlScope(){const s=sessionContext.snapshot();if(s.status!=='owned')throw new Error('GOAL_CONTROL_SCOPE');return {owner:sessionOwnerKey(s.owner),epoch:s.epoch}}
  function findGoalControlTarget(id:string):GoalControlView|null{const current=goal as GoalControlView|null;if(current?.id===id)return current;return goalControlStateRef.current.paused.find(g=>g.id===id)??null}
  function sameGoalControlTarget(a:GoalControlView,b:GoalControlView|null){return !!b&&b.id===a.id&&b.version===a.version&&b.status===a.status&&b.pause_gen===a.pause_gen}
  function requestGoalControl(target:GoalControlView,action:GoalControlAction){
    const control=goalControlStateRef.current
    if(mutationOwner.current!==null||busy||sending||!goalControlsReadyRef.current||control.busy||control.unknown||target.status==='UNKNOWN')return
    goalControlIntentRef.current={goal:target,action};goalControlAgreedRef.current=false
    setGoalControlIntentValue({goal:target,action});setGoalControlAgreedValue(false)
  }
  function agreeGoalControl(v:boolean){if(!goalControlIntentRef.current)return;goalControlAgreedRef.current=v;setGoalControlAgreedValue(v)}
  async function confirmGoalControl(){
    const intent=goalControlIntentRef.current,agreed=goalControlAgreedRef.current,controller=goalControlRef.current
    const control=controller?.state()
    const target=intent?findGoalControlTarget(intent.goal.id):null
    if(!intent||!agreed||!controller||!control||!target||!goalControlsReadyRef.current||control.busy||control.unknown||target.status==='UNKNOWN'||!sameGoalControlTarget(intent.goal,target)||busy||sending){clearGoalControlIntent();return}
    const token=acquireMutation(true)
    if(token===null){clearGoalControlIntent();return}
    const epoch=sessionContext.snapshot().epoch
    setBusy('goal-control');setError('');setNotice('')
    try{
      sessionContext.assertCurrent(epoch)
      if(goalControlRef.current!==controller||mutationOwner.current!==token)return
      try{await controller.submit(target,intent.action,true)}catch{/* controller already shows the denied or unknown outcome */}
      sessionContext.assertCurrent(epoch)
      if(goalControlRef.current!==controller||mutationOwner.current!==token)return
      await refresh()
    }catch{setError('目标状态暂未确认，请点“核对”后再决定。')}finally{clearGoalControlIntent();if(releaseMutation(token))setBusy('')}
  }
  async function checkGoalControl(){
    const controller=goalControlRef.current
    if(!controller||controller.state().busy)return
    const epoch=sessionContext.snapshot().epoch
    try{await controller.check();if(goalControlRef.current===controller&&!controller.state().unknown&&!controller.state().error)setGoalControlsReady(true)}catch{/* stays unknown */}
    if(goalControlRef.current!==controller||sessionContext.snapshot().epoch!==epoch)return
    await refresh().catch(()=>{})
  }

  async function refresh(){
    await refreshes.current.run<State,[WorkspaceLoad,IntegrationSnapshot|null,SafetyReferenceSnapshot|null,CardsLoad,WealthSnapshot|null,{goal:ConversationGoalView|null}]>(
      async()=>{
        // Probe identity before and after; no partial publish until the after-check passes.
        await api<unknown>('/session')
        const loaded=await Promise.all([loadWorkspace(api,memoryQuery),loadIntegrationStatus(api),loadSafetyReferences(api),loadCards(api),loadWealth(api),api<{goal:ConversationGoalView|null}>('/goals/current')])
        await api<unknown>('/session')
        return loaded
      },{
        start:()=>{setGoal(null);setIntegrationStatus(null);setIntegrationLoading(true);setReference(null);setReferenceLoading(true);setBankCardsLoading(true);setVirtualApps(null);setVirtualError('');setWealthSnapshot(null);setWealthLoading(true);setWealthError('')},
        progress:setState,
        success:([next,status,nextReference,nextCards,nextWealth,nextGoal])=>{
          setGoal(nextGoal?.goal??null)
          setWealthSnapshot(nextWealth);setWealthError(nextWealth?'':'暂时无法核对模拟理财，请刷新。')
          setBankCards(nextCards.cards);setBankCardsError(nextCards.error)
          setVirtualApps(nextCards.applications??null);setVirtualError(nextCards.applicationError||'')
          setIntegrationStatus(status)
          setReference(nextReference)
          setState({...next.state,operations:mergeWealthOperations(mergeVirtualOperations(next.state.operations,nextCards.applications??null),nextWealth?.applications??null)});setBill(next.bill);setSubs(next.subscriptions);setMessages(next.messages)
          setUnavailableGroups(next.unavailableGroups)
          if(next.events!==null)setEvents(next.events)
          if(next.memories!==null)setMemories(next.memories)
          if(next.warnings.length)setNotice(next.warnings.join(' '))
        },
        failure:()=>{setGoal(null);setBill(null);setSubs(null);setUnavailableGroups(['bill','subscriptions']);setReference(null);setBankCards(null);setBankCardsError('暂时无法核对卡片信息。');setVirtualApps(null);setWealthSnapshot(null);setWealthError('暂时无法核对模拟理财，请刷新。')},
        finish:()=>{setIntegrationLoading(false);setReferenceLoading(false);setBankCardsLoading(false);setWealthLoading(false)},
      })
    // Read-only saved-goals reload (GET only); publish readiness only for the unchanged controller/owner/epoch.
    const controller=goalControlRef.current
    const scope0=sessionContext.snapshot()
    if(!controller||scope0.status!=='owned'||controller.state().busy)return
    const scopeKey=sessionOwnerKey(scope0.owner)+'#'+scope0.epoch
    const current=()=>{const s=sessionContext.snapshot();return goalControlRef.current===controller&&s.status==='owned'&&sessionOwnerKey(s.owner)+'#'+s.epoch===scopeKey}
    setGoalControlsReady(false)
    try{await controller.load()}catch{
      if(current())publishGoalControl({...goalControlStateRef.current,busy:false,unknown:true,error:goalControlStateRef.current.error||'目标操作状态暂时无法核对，请点“核对”。'})
      return
    }
    if(current()&&!controller.state().unknown&&!controller.state().error)setGoalControlsReady(true)
  }

  async function retryWorkspace(){
    if(busy)return
    const token=acquireMutation()
    if(token===null)return
    setBusy('workspace-retry');setError('')
    try{await retryWorkspaceSession(initialize,refresh)}catch(error){setError((error as Error).message)}finally{if(releaseMutation(token))setBusy('')}
  }

  async function checkReceipt(id=receiptId){
    if(!id)return
    const sequence=++receiptSequence.current
    setReceiptId(id);setReceipt(null);setReceiptError('');setReceiptBusy(true)
    try{
      const result=await api<Receipt>('/operations/'+encodeURIComponent(id)+'/receipt')
      if(sequence===receiptSequence.current)setReceipt(result)
    }catch(error){if(sequence===receiptSequence.current)setReceiptError((error as Error).message)}
    finally{if(sequence===receiptSequence.current)setReceiptBusy(false)}
  }
  function closeReceipt(){receiptSequence.current++;setReceiptId('');setReceipt(null);setReceiptError('');setReceiptBusy(false)}
  useEffect(()=>{if(receiptId)receiptPanel.current?.scrollIntoView({behavior:'smooth',block:'start'})},[receiptId])

  async function controlPlan(plan:TaskPlan,action:PlanControlAction){
    if(busy)return
    const token=acquireMutation()
    if(token===null)return
    setBusy('plan-'+plan.id);setError('');setNotice('')
    try{
      await api('/plans/'+plan.id+'/'+action,{expected_version:plan.version})
      await refresh()
      if(action==='handoff'){setView('tasks');setNotice('已保存本地核查摘要，尚未联系真人客服。关联未执行操作已取消。')}
      else if(action==='pause'||action==='cancel')setNotice('任务已停止，关联未执行操作已取消。继续时会核对原记录，取消的操作不会重新付款。')
      else setNotice('已继续核对原计划，敏感操作仍需单独确认。')
    }catch(error){setError((error as Error).message);await refresh().catch(()=>{})}finally{if(releaseMutation(token))setBusy('')}
  }

  async function recoverTool(group:ToolGroup){
    if(busy||sending)return
    const token=acquireMutation()
    if(token===null)return
    setBusy('recover-'+group);setError('')
    try{
      await api('/tools/'+group+'/recover',{})
      await refresh()
      setNotice(group==='bill'?'已重新核对账单查询状态。':'已允许下一次恢复尝试。请核对原操作，再明确重试；不会自动确认或付款。')
    }catch(error){setError((error as Error).message);await refresh().catch(()=>{})}finally{if(releaseMutation(token))setBusy('')}
  }
  useEffect(()=>deployment.subscribe(setDeploymentState),[])
  useLayoutEffect(()=>{
    const unsubscribe=sessionContext.subscribe(()=>setSessionSnapshot(sessionContext.snapshot()))
    setSessionSnapshot(sessionContext.snapshot())
    return unsubscribe
  },[])
  useEffect(()=>{initialize().then(()=>refresh()).catch(e=>setError(e.message)).finally(()=>setLoading(false))},[])
  const goalControlScopeKey=sessionSnapshot.status==='owned'?sessionOwnerKey(sessionSnapshot.owner)+'#'+sessionSnapshot.epoch:''
  useEffect(()=>{
    clearGoalControlIntent();publishGoalControl(emptyGoalControl);setGoalControlsReady(false)
    if(!goalControlScopeKey)return
    const epoch=sessionContext.snapshot().epoch
    const storage={getItem:(k:string)=>window.sessionStorage.getItem(k),setItem:(k:string,v:string)=>window.sessionStorage.setItem(k,v),removeItem:(k:string)=>window.sessionStorage.removeItem(k)}
    const controller:GoalController=createGoalControlController({client:createGoalControlClient(api),storage,getScope:goalControlScope,onChange:next=>{if(goalControlRef.current===controller&&sessionContext.snapshot().epoch===epoch)publishGoalControl(next)}})
    goalControlRef.current=controller
    controller.load().then(()=>{if(goalControlRef.current===controller&&!controller.state().unknown&&!controller.state().error)setGoalControlsReady(true)}).catch(()=>{if(goalControlRef.current===controller)publishGoalControl({...goalControlStateRef.current,busy:false,unknown:true,error:goalControlStateRef.current.error||'目标操作状态暂时无法核对，请点“核对”。'})})
    return ()=>{controller.dispose();if(goalControlRef.current===controller)goalControlRef.current=null}
  },[goalControlScopeKey])
  useEffect(()=>{const i=goalControlIntentRef.current;if(i&&!sameGoalControlTarget(i.goal,findGoalControlTarget(i.goal.id)))clearGoalControlIntent()},[goal,goalControlState])
  function currentScope(){const s=sessionContext.snapshot();return s.status==='owned'?sessionOwnerKey(s.owner)+'#'+s.epoch:''}
  function prefersReducedMotion(){return !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches}
  useEffect(()=>{setDraftCandidate(null);setHasUnread(false);followLatestRef.current=true;savedScrollRef.current=0;return ()=>cancelAnimationFrame(locateFrameRef.current)},[goalControlScopeKey])
  useEffect(()=>{
    const grew=messages.length>messageCountRef.current
    messageCountRef.current=messages.length
    if(!followLatestRef.current){if(grew&&messages[messages.length-1]?.role==='assistant')setHasUnread(true);return}
    const scope=goalControlScopeKey
    const frame=requestAnimationFrame(()=>{const el=conversation.current;if(el&&followLatestRef.current&&currentScope()===scope)el.scrollTop=el.scrollHeight})
    return ()=>cancelAnimationFrame(frame)
  },[messages.length,sending,goalControlScopeKey])
  useEffect(()=>{
    if(view!=='home')return
    const scope=goalControlScopeKey
    const frame=requestAnimationFrame(()=>{const el=conversation.current;if(!el||currentScope()!==scope)return;el.scrollTop=followLatestRef.current?el.scrollHeight:savedScrollRef.current})
    return ()=>cancelAnimationFrame(frame)
  },[view,goalControlScopeKey])
  function onConversationScroll(){
    const el=conversation.current
    if(!el)return
    const top=el.scrollTop
    savedScrollRef.current=Number.isFinite(top)?Math.max(0,top):0
    const near=isNearLatest({scrollHeight:el.scrollHeight,scrollTop:el.scrollTop,clientHeight:el.clientHeight})
    followLatestRef.current=near
    if(near)setHasUnread(false)
  }
  function jumpToLatest(){
    followLatestRef.current=true;setHasUnread(false)
    const el=conversation.current
    if(!el)return
    el.scrollTo({top:el.scrollHeight,behavior:prefersReducedMotion()?'auto':'smooth'})
    el.focus({preventScroll:true})
  }
  function requestExample(value:string){
    const scope=currentScope()
    if(!scope||busy||sending||mutationOwner.current!==null||typeof value!=='string')return
    if(value.length>2000){setNotice('这条示例过长，无法填入，请直接输入问题。');return}
    setView('home')
    const decision=draftDecision(text,value)
    if(decision==='ignore')return
    if(decision==='confirm'){setDraftCandidate({text:value,scope});return}
    setDraftCandidate(null);setText(current=>draftDecision(current,value)==='apply'?value:current);input.current?.focus()
  }
  function confirmExample(){
    const c=draftCandidate
    if(!c)return
    if(c.scope!==currentScope()){setDraftCandidate(null);return}
    if(busy||sending||mutationOwner.current!==null)return
    setDraftCandidate(null);setView('home');setText(c.text)
    requestAnimationFrame(()=>input.current?.focus())
  }
  function cancelExample(){setDraftCandidate(null);requestAnimationFrame(()=>input.current?.focus())}
  function canRestoreSendFocus(){
    const a=document.activeElement
    if(!a||a===document.body||a===input.current)return true
    return a instanceof HTMLButtonElement&&a.type==='submit'&&!!input.current&&a.form!==null&&a.form===input.current.form
  }
  // Navigation only: no financial API calls.
  function locateOperation(id:string){
    const scope=currentScope()
    if(!scope||typeof id!=='string'||!id||!state?.operations.some(o=>o.id===id))return
    setView('home')
    cancelAnimationFrame(locateFrameRef.current)
    locateFrameRef.current=requestAnimationFrame(()=>{locateFrameRef.current=requestAnimationFrame(()=>{
      if(currentScope()!==scope)return
      const el=document.querySelector<HTMLElement>('[data-operation-id="'+CSS.escape(id)+'"]')
      if(!el)return
      el.scrollIntoView({block:'center',behavior:prefersReducedMotion()?'auto':'smooth'})
      el.focus({preventScroll:true})
    })})
  }

  async function send(value=text,choice?:{id:string;version:number}){
    if(!value.trim()||sending)return
    const token=acquireMutation()
    if(token===null)return
    const sendScope=currentScope()
    setView('home');setText('');setError('');setSending(true)
    const optimistic:Message={id:Date.now(),role:'user',content:value,created_at:new Date().toISOString()}
    setMessages(previous=>[...previous,optimistic])
    try{await requests.current.run('chat:'+(choice?choice.id+':'+choice.version+':':'')+value,key=>api<ChatResult>('/chat',choice?{text:value,request_key:key,goal_mode:true,goal_id:choice.id,goal_version:choice.version}:{text:value,request_key:key,goal_mode:true}),refresh)}catch(e){setError((e as Error).message);setMessages(previous=>previous.filter(m=>m.id!==optimistic.id));if(currentScope()===sendScope)setText(current=>current.trim()===''?value:current)}finally{if(releaseMutation(token)){setSending(false);if(currentScope()===sendScope&&followLatestRef.current&&canRestoreSendFocus())input.current?.focus()}}
  }
  async function mutate(op:Operation,action:'confirm'|'cancel'|'verify',code?:string){
    if(state?.locked&&action!=='cancel'){setNotice('敏感操作暂时锁定，请稍后刷新核对。');return}
    const token=acquireMutation()
    if(token===null)return
    setBusy(op.id);setError('');setNotice('')
    try{
      const result=await api<Operation>('/operations/'+op.id+'/'+action,action==='verify'?{code}:{});
      if(result.notice)setNotice(result.notice)
      else if(result.next_operation)setNotice('订阅查询已完成，请继续核对取消续费卡片。')
      else if(result.status==='SUCCEEDED')setNotice(opName(result)+'已完成，可核对业务回执。')
      // Only an actual user confirm/verify that SUCCEEDED for an op bound to the current goal advances it, once.
      const bound=action!=='cancel'&&result.status==='SUCCEEDED'&&goal?.status==='ACTIVE'&&goal.operation_ids.includes(op.id)?goal:null
      if(!bound||!await advanceGoal(bound,token))await refresh()
    }catch(e){setError((e as Error).message);await refresh().catch(()=>{})}finally{if(releaseMutation(token))setBusy('')}
  }
  // Caller must already hold the global mutation owner; never acquires.
  async function advanceGoal(g:ConversationGoalView,token:symbol):Promise<boolean>{
    const epoch=sessionContext.snapshot().epoch
    const fresh=(await api<{goal:ConversationGoalView|null}>('/goals/current')).goal
    sessionContext.assertCurrent(epoch)
    if(mutationOwner.current!==token)return false
    if(!fresh||fresh.id!==g.id||fresh.version!==g.version||fresh.status!=='ACTIVE'||fresh.phase!=='READY'||fresh.next_action!=='CONTINUE')return false
    const result=await requests.current.run('goal:'+fresh.id+':'+fresh.version,key=>api<GoalResult>('/goals/'+encodeURIComponent(fresh.id)+'/continue',{expected_version:fresh.version,request_key:key}),refresh)
    sessionContext.assertCurrent(epoch)
    if(mutationOwner.current!==token)return true
    if(result.goal?.status==='COMPLETED'){setGoal(result.goal);setNotice('本机模拟目标已完成，可核对操作回执。')}
    return true
  }
  async function continueGoal(){
    if(!goal||busy||sending)return
    const token=acquireMutation()
    if(token===null)return
    setBusy('goal');setError('');setNotice('')
    try{await advanceGoal(goal,token)}catch{setError('目标进度暂未确认，请点击核对刷新后再决定。');await refresh().catch(()=>{})}finally{if(releaseMutation(token))setBusy('')}
  }
  function chooseGoal(id:string){
    const choice=goal?.choices.find(c=>c.id===id)
    if(!goal||!choice)return
    void send(choice.label,{id:goal.id,version:goal.version})
  }
  async function prepareSubscription(sub:Subscription){
    if(busy||sending||state?.locked)return
    const token=acquireMutation()
    if(token===null)return
    setBusy(sub.id);setError('')
    try{await requests.current.run('cancel:'+sub.id,key=>api<Operation>('/subscriptions/cancel/prepare',{subscription_id:sub.id,request_key:key}),refresh,'cancel-'+sub.id);setView('home');setNotice('取消操作已准备，请在待处理卡片中确认。')}catch(e){setError((e as Error).message)}finally{if(releaseMutation(token))setBusy('')}
  }

  async function prepareBankCard(card:BankCard,action:CardAction){
    if(busy||sending||state?.locked||bankCardsLoading||bankCardsError)return
    const existing=findCardIntent(state?.operations||[],card,action)
    if(existing){setError('');setView('home');setNotice('已有这张卡的操作记录，请先核对原操作与回执，不会另行准备。');return}
    const token=acquireMutation()
    if(token===null)return
    setBusy(card.id);setError('');setNotice('')
    try{
      const result=await requests.current.run('card:'+card.id+':'+card.version+':'+action,
        key=>api<Operation>('/cards/prepare',{card_id:card.id,action,expected_version:card.version,request_key:key}),refresh)
      setView('home')
      setNotice(result.status==='SUCCEEDED'?'已找到原操作回执，请核对结果。':'卡片操作已准备，尚未执行。请核对待处理卡片，再确认与模拟验证。')
    }catch(e){
      const instruction=e instanceof ApiError&&e.status===409?'原请求存在冲突，请刷新核对卡片及已有操作，不会自动重发。':'本次结果需要核对，请先查看已有操作，不要另行重复提交。'
      setError((e instanceof ApiError?e.message:'暂时未收到完整操作结果。')+' '+instruction)
      setNotice('')
      await refresh().catch(()=>{})
      setView('home')
    }finally{if(releaseMutation(token))setBusy('')}
  }
  async function prepareVirtualCard(account:Account){
    if(busy||sending||state?.locked||bankCardsLoading||bankCardsError||virtualError||virtualApps===null)return
    const existing=findVirtualIntent(virtualApps,account)
    if(existing){setView('home');setNotice('已有申请，请先核对原操作。');await checkReceipt(existing.id);return}
    const token=acquireMutation()
    if(token===null)return
    setBusy('virtual-'+account.id);setError('');setNotice('')
    try{
      const result=await requests.current.run('virtual:'+account.id,
        key=>api<Operation>('/cards/virtual/prepare',{account_id:account.id,request_key:key}),refresh)
      setView('home');setNotice(result.status==='SUCCEEDED'?'已找到原申请，请核对回执。':'申请已准备，尚未生成虚拟卡。请核对关联账户，再独立确认。')
    }catch(e){
      setError((e instanceof ApiError?e.message:'暂时未收到完整申请结果。')+' 请刷新核对已有申请，不要重复提交。')
      await refresh().catch(()=>{});setView('home')
    }finally{if(releaseMutation(token))setBusy('')}
  }
  async function prepareWealth(input:WealthInput){
    if(busy||sending||state?.locked||wealthLoading||wealthError||!wealthSnapshot||!state)return
    const existing=findWealthIntent(wealthSnapshot.applications,input)
    if(existing){setError('');setView('home');setNotice('已有相关申请，请先核对原记录，不重复准备。');await checkReceipt(existing.id);return}
    if(input.action==='purchase'&&investmentStatus!=='VALID'){setNotice('请在模拟理财中完成并核对当前演示测评，再准备申购。');setView('wealth');return}
    const token=acquireMutation()
    if(token===null)return
    setBusy('wealth-'+input.account_id);setError('');setNotice('')
    try{
      const result=await requests.current.run('wealth:'+input.account_id+':'+input.product_id+':'+input.action+':'+input.amount_cents,
        key=>api<Operation>('/wealth/prepare',{...input,request_key:key,safety_epoch:state.safety_epoch}),refresh)
      setView('home');setNotice(result.status==='SUCCEEDED'?'已找到原模拟理财回执，请核对结果。':'模拟理财申请已准备，尚未成交。请核对待处理卡片，再独立确认和模拟验证。')
    }catch(e){
      setError((e instanceof ApiError?e.message:'暂时未收到完整申请结果。')+' 请先刷新核对原申请，不要重复提交。')
      await refresh().catch(()=>{});setView('home')
    }finally{if(releaseMutation(token))setBusy('')}
  }
  async function choosePayee(id:string,cents:number,selectionKey:string,epoch:string){
    const token=acquireMutation()
    if(token===null)return
    setError('');setBusy(id)
    try{await requests.current.run('payee:'+selectionKey,key=>api<Operation>('/transfers/prepare',{payee_id:id,amount_cents:cents,request_key:key,safety_epoch:epoch}),refresh,'select-'+selectionKey);setNotice('收款人已选定，请核对待处理转账卡片。')}catch(e){setError((e as Error).message)}finally{if(releaseMutation(token))setBusy('')}
  }

  async function endSuspiciousRequest(){
    const hold=state?.safety_hold
    if(!hold||busy||sending)return
    const token=acquireMutation()
    if(token===null)return
    setBusy(hold.id);setError('')
    try{await api('/safety/end',{hold_id:hold.id});await refresh();setNotice('这次可疑请求已结束。旧转账不会恢复，新的业务请重新提出。')}
    catch(e){setError((e as Error).message);await refresh().catch(()=>{})}finally{if(releaseMutation(token))setBusy('')}
  }

  async function changeMemory(note:MemoryNote,action:'update'|'forget'){
    const token=acquireMutation()
    if(token===null)return
    setBusy(note.id);setError('');setNotice('')
    try{
      await api('/memories/'+note.id+'/'+action,{expected_version:note.version,...(action==='update'?{content:memoryDraft}:{})})
      setEditingMemory('');await refresh()
      setNotice(action==='update'?'记忆已更新，旧内容不再参与检索。':'已移出这条记忆，后续不再检索。聊天记录仍可保留。')
    }catch(e){setError((e as Error).message);if(e instanceof ApiError&&e.code==='MEMORY_CONFLICT')setEditingMemory('');await refresh().catch(()=>{})}finally{if(releaseMutation(token))setBusy('')}
  }

  async function searchMemory(){
    setError('')
    try{setMemories((await api<{notes:MemoryNote[]}>('/memories?query='+encodeURIComponent(memoryQuery))).notes)}catch(e){setError((e as Error).message)}
  }

  const total=state?.accounts.reduce((sum,a)=>sum+a.balance_cents,0)||0
  const pending=state?.operations.filter(o=>['WAITING_CONFIRMATION','WAITING_VERIFICATION','UNKNOWN','EXECUTING'].includes(o.status))||[]
  const hasGoalControlContext:boolean=!!goal||goalControlState.paused.length>0||goalControlState.unknown||!!goalControlState.error||!!goalControlIntent
  const hasTaskRail:boolean=hasGoalControlContext||pending.length>0||(state?.plans||[]).some(planNeedsAttention)
  const net=bill?.net_expense_cents||0
  const previous=bill?.previous_net_expense_cents||0
  const delta=previous?Math.round((net-previous)/previous*100):null
  const title={home:'银行助手',bills:'账单洞察',subscriptions:'订阅与代扣',cards:'卡片管理',wealth:'模拟理财',audit:'操作记录',memory:'记忆与偏好',tasks:'任务进度',compute:'计算参考',aa:'AA分摊',scheduled:'定时转账',birthday:'生日安排',channel:'渠道消息'}[view]
  const detailBill=selectedBill||bill

  const goalOpAllowed=(id:string)=>goal?.status==='ACTIVE'&&(goal.phase==='WAITING_CONFIRMATION'||goal.phase==='WAITING_VERIFICATION')&&goal.operation_ids.includes(id)
  const birthdayGoal=goal?.status==='ACTIVE'&&/birthday/i.test(goal.kind)
  // Latest chat position per operation (stable messageId:cardIndex, not object identity)
  const latestOpPosition=new Map<string,string>()
  for(const m of messages){if(m.role!=='assistant')continue;(m.content as ChatResult).cards.forEach((c,i)=>{if(c.type==='operation')latestOpPosition.set(c.operation.id,m.id+':'+i)})}
  const inlineOps=new Set(latestOpPosition.keys())
  // Any op already rendered in chat (goal-bound or ordinary) is actionable only there; aside shows the rest
  const sidePending=pending.filter(op=>!inlineOps.has(op.id))
  function renderCard(card:Card,index:number,messageId?:Message['id']){
    if(card.type==='birthday_flow')return <BirthdayFlowCard key={index} flowId={card.flow_id} blocked={!!busy||sending} onOpen={id=>{setBirthdayFlowId(id);setView('birthday')}}/>
    if(card.type==='wealth'||card.type==='wealth_selection')return <WealthPanel purchaseAllowed={investmentStatus==='VALID'} locked={!!state?.locked} key={index} snapshot={wealthSnapshot} accounts={state?.accounts??null} loading={wealthLoading} busy={!!busy||sending} error={wealthError} onPrepare={input=>{void prepareWealth(input)}} onReceipt={id=>{void checkReceipt(id)}} onRefresh={()=>{void refresh()}}/>
    if(card.type==='virtual_accounts')return <VirtualCardsPanel locked={!!state?.locked} key={index} accounts={state?.accounts??null} cards={bankCards} applications={virtualApps} loading={bankCardsLoading} busy={!!busy||sending} error={bankCardsError||virtualError} onPrepare={account=>{void prepareVirtualCard(account)}} onReceipt={id=>{void checkReceipt(id)}} onRefresh={()=>{void refresh()}}/>
    if(card.type==='bank_cards')return <ChatCardsPanel key={index} cards={bankCards} loading={bankCardsLoading} error={bankCardsError} onRefresh={()=>{void refresh()}}/>
    if(card.type==='memory')return <section className="memory-receipt" key={index}><b><BookOpen size={16}/>{card.action==='saved'?'已记下你的偏好':card.action==='removed'?'已移出记忆':'你明确留下的笔记'}</b>{card.notes.map(note=><p key={note.id}>{note.content}</p>)}{card.notes.length===0&&<p>没有可展示的相关笔记。</p>}<small>笔记可参考，交易结果以银行记录为准。</small><button className="text-button" onClick={()=>setView('memory')}>管理记忆与偏好 <ArrowRight size={14}/></button></section>
    if(card.type==='safety')return <section className={'safety-card '+(card.finding.hold?'safety-card--paused':'')} key={index} aria-label="业务安全提示"><b><ShieldCheck size={16}/>{card.finding.hold?'本次请求发现风险线索':'安全提示'}</b><ul>{card.finding.reasons.map(reason=><li key={reason}>{reason}</li>)}</ul><p>{card.finding.advice}</p><SafetyReferencePanel codes={card.finding.codes} reference={reference} loading={referenceLoading} error={!referenceLoading&&reference===null}/>{card.hold_id&&<small>{state?.safety_hold?.id===card.hold_id?'转账暂停中，请核对上方安全提示。':'该风险请求已结束或被更新，旧转账不会恢复。'}</small>}</section>
    if(card.type==='operation'){
      const current=state?.operations.find(o=>o.id===card.operation.id)||card.operation
      // Only the latest occurrence of an operation in the chat may render an executable card
      if(messageId!==undefined&&latestOpPosition.get(current.id)!==messageId+':'+index)return <div className="receipt" key={index}><BadgeCheck size={14}/>{opName(current)}已在下方最新消息中更新，请在最新卡片中核对。</div>
      const birthdayOp=current.type==='birthday_budget_reserve'||current.type==='birthday_order'
      const boundGoal=goalOperationBinding(goal as GoalControlView|null,goalControlState.paused,current.id)
      const goalBound=!!boundGoal
      const controlSettled=goalControlsReady&&!goalControlState.unknown&&!goalControlState.error
      return <OperationCard key={goalBound?current.id+':g:'+(boundGoal?.pause_gen??0)+':'+(boundGoal?.status??''):current.id} op={current} readOnly={goalBound&&(boundGoal?.status!=='ACTIVE'||!goalOpAllowed(current.id))} birthdayContext={birthdayOp} birthdayActions={birthdayOp&&goalOpAllowed(current.id)} busy={!!busy||sending||!controlSettled} locked={!!state?.locked} onAction={mutate} onReceipt={checkReceipt}/>
    }
    if(card.type==='bill')return <div className="inline-bill" key={index}><div className="inline-bill-head"><span><FileText size={16}/> {card.bill.period} 账单摘要</span><BadgeCheck size={17}/></div><div className="inline-bill-total">¥ {money(card.bill.net_expense_cents)}<small>净支出</small></div><div className="mini-categories">{card.bill.categories.slice(0,3).map((c,i)=><span key={c.name}><i style={{background:categoryColors[i]}}/>{c.name}<b>¥{money(c.amount_cents)}</b></span>)}</div><button className="text-button" onClick={()=>{setSelectedBill(card.bill);setView('bills')}}>查看完整账单 <ArrowUpRight size={14}/></button></div>
    if(card.type==='payees')return <div className="payee-card" key={index}><b>请选择具体收款人</b>{card.candidates.map(p=><button key={p.id} disabled={!!busy||sending||!!state?.safety_hold} onClick={()=>choosePayee(p.id,card.amount_cents,card.selection_key||'legacy-'+index,card.safety_epoch||'')}><span>{p.name} · {p.remark}<small>{p.phone}</small></span><ChevronRight size={16}/></button>)}</div>
    return <div className="inline-bill" key={index}>{card.accounts.map(a=><div className="balance-row" key={a.id}>{a.name}<b>¥{money(a.balance_cents)}</b></div>)}</div>
  }

  if(isSessionBlocked(sessionSnapshot))return <SessionBoundaryGate onReload={()=>window.location.reload()}/>

  if(deploymentState!=='ready')return <DeploymentGate checking={deploymentState==='checking'} onRetry={()=>{void retryWorkspace()}}/>

  return <div className={'app-shell assistant-workspace reading-'+readingMode+(view==='home'?' workspace-home '+(hasTaskRail?'has-task-rail':'no-task-rail')+(messages.length>0?' has-messages':' empty-conversation'):' workspace-reference')}>
    <a className="ff-skip-link" href="#workspace-main" onClick={e=>{e.preventDefault();document.getElementById('workspace-main')?.focus()}}>跳到主要内容</a>
    {draftCandidate&&<DraftReplaceDialog suggestion={draftCandidate.text} blocked={!!busy||sending} onConfirm={confirmExample} onCancel={cancelExample}/>}
    <aside className="sidebar">
      <a className="brand" href="#" onClick={e=>{e.preventDefault();setView('home')}}><span className="brand-mark" aria-hidden="true">FF</span><span>FF Agent<small>菲菲 · 银行业务助手</small></span></a>
      
      <AssistantNavigation currentView={view} pendingCount={pending.length} onNavigate={value=>{setView(value as View);if(value==='bills')setSelectedBill(null)}}/>
      <div className="sidebar-note"><ShieldCheck size={22}/><b>每一步，都由你掌握</b><p>查询自动完成<br/>敏感操作确认后执行</p><span>本机模拟银行环境</span></div>
      <div className="profile"><div className="avatar" aria-hidden="true">{(state?.name||'').slice(0,1)}</div><div><b>{state?.name||'模拟账户'}</b><small>个人模拟账户</small></div></div>
    </aside>
    <div className="main-shell">
      <header className="topbar"><div className="breadcrumb">个人空间 <ChevronRight size={13}/> <span>{title}</span></div><div className="topbar-right"><ModelSettings blocked={!!busy||sending}/><ReadingModeControl mode={readingMode} onChange={changeReadingMode}/><span className="environment"><i/> 模拟银行</span><button aria-label="刷新工作台" className="icon-button" disabled={!!busy} onClick={()=>{void retryWorkspace()}}><RefreshCw size={17}/></button><button aria-label="查看待处理操作" className="icon-button" onClick={()=>{if(pending.length){locateOperation(pending[0].id)}else{setView('home');setNotice('当前没有待处理操作。')}}}><Bell size={18}/>{pending.length>0&&<i className="notification-dot"/>}</button></div></header>
      <main id="workspace-main" tabIndex={-1}>
        <details className="integration-details"><summary>本机模拟 · 未连接真实银行、商户与身份验证</summary><IntegrationStatusCard snapshot={integrationStatus} loading={integrationLoading}/></details>
        {(error||notice)&&<div className={error?'banner error':'banner'} role={error?'alert':'status'}><span>{error||notice}</span><button aria-label="关闭提示" onClick={()=>{setError('');setNotice('')}}><X size={16}/></button></div>}
        {state?.locked&&<section className="panel verification-lock" role="status" aria-label="敏感操作暂时锁定"><h2>敏感操作暂时锁定</h2><p>模拟验证码连续输错，敏感操作已暂停。账单、余额和持仓仍可查询；请稍后刷新核对锁定状态。</p><button className="outline-button" style={{minHeight:48}} disabled={!!busy||sending} onClick={()=>{void refresh()}}>刷新锁定状态</button></section>}
        {state&&<RecoveryBanner tools={state.tools||[]} busy={!!busy||sending} onProbe={recoverTool} onRefresh={()=>{void retryWorkspace()}} onHandoff={()=>{setView('audit');setNotice('已打开本人操作记录供核查。此原型尚未连接外部人工客服，不会自动恢复或重发转账。')}}/>}{state?.safety_hold&&<section className="safety-banner" role="alert" aria-label="转账风险暂停"><div className="safety-banner__copy"><h2><ShieldCheck size={19}/>先停一下，核实这条请求</h2><p>{state.safety_hold.reasons.join(' ')} 相关待处理转账已取消，账单和本人订阅管理仍可使用。</p><small>{state.safety_hold.advice}</small><SafetyReferencePanel codes={state.safety_hold.codes} reference={reference} loading={referenceLoading} error={!referenceLoading&&reference===null}/></div><div className="safety-banner__action"><button className="safety-end-btn" disabled={!!busy||sending} onClick={endSuspiciousRequest}>{busy===state.safety_hold.id?'正在结束…':'结束这次可疑请求'}</button><span>结束不会恢复旧转账。新业务需重新确认。</span></div></section>}
        {view==='home'?<h1 className="ff-visually-hidden">今天想办什么？</h1>:<section className="page-intro"><div><h1>{title}</h1><p>{view==='bills'?'看清钱的去向，数字都有据可查。':view==='subscriptions'?'让每一项续费，都出于你的选择。':view==='cards'?'核对卡片状态，管理交易设置，准备本机模拟消费。':view==='memory'?'只记下你明确留下的偏好，随时查看和修改。':view==='tasks'?'保留每一步的原计划，中断后先核对，再继续。':'从请求到回执，保留可核对的操作轨迹。'}</p></div><span className="date-label"><Clock3 size={14}/>{state?.business_date||'正在连接'}</span></section>}
        {receiptId&&<div ref={receiptPanel}><ReceiptCheck receipt={receipt} busy={receiptBusy} error={receiptError} onCheck={()=>{void checkReceipt()}} onClose={closeReceipt}/></div>}
        {loading?<div className="loading"><LoaderCircle className="spin"/> 正在连接本机模拟银行…</div>:!state?<section className="panel permission-empty"><ShieldCheck size={30}/><h2>暂时无法连接模拟账户</h2><p>余额和账单尚未读取，请重新连接后核对。</p><button className="outline-button" disabled={!!busy} onClick={()=>{void retryWorkspace()}}>重新连接</button></section>:<>
          {view!=='home'&&<section className="metrics"><div className="metric asset"><div className="metric-label"><Wallet size={17}/> 模拟账户总余额 <span>CNY</span></div><div className="metric-value">¥ {money(total)}</div><div className="metric-foot"><span>日常与储蓄账户</span><span>{state?.accounts.length||0} 个账户 <ArrowUpRight size={13}/></span></div><div className="card-orbit one"/><div className="card-orbit two"/></div><div className="metric"><div className="metric-label"><ArrowUpRight size={17}/> 本月净支出</div><div className="metric-value">{bill?<>¥ {money(net)}</>:'—'}</div><div className="metric-foot"><span className="trend"><ArrowDownLeft size={13}/>{!bill?'账单暂不可用':delta===null?'暂无上月对比':`较上月 ${delta>0?'+':''}${delta}%`}</span><span>含退款抵扣</span></div></div><div className="metric"><div className="metric-label"><ShieldCheck size={17}/> 授权与安全</div><div className="metric-value security-value">{pending.length}<small>项待确认</small></div><div className="metric-foot"><span><i className="status-dot"/>{state?.safety_hold?'转账因风险暂停':state?.locked?'敏感操作暂时锁定':'权限规则正常运行'}</span><span>查看后再决定</span></div></div></section>}
          {view==='home'&&<div className="dashboard-grid workspace-home"><section className="conversation-panel panel" aria-label="对话">{messages.length>0&&<div className="panel-header"><div><span className="assistant-symbol" aria-hidden="true"><Sparkles size={17}/></span><h2>和菲菲对话</h2></div><span className="mode-label">{state?.planner_mode==='model'?'模型规划模式':'本地规则演示'}</span></div>}<div className="conversation" ref={conversation} onScroll={onConversationScroll} tabIndex={0} role="log" aria-label="对话记录">
            {messages.length===0?<AssistantWelcome name={state.name||''} disabled={!!busy||sending} onDraft={requestExample}/>:messages.map(m=><div key={m.id} className={'message '+m.role}>{m.role==='assistant'&&<span className="message-avatar"><Sparkles size={14}/></span>}<div className="message-body">{m.role==='user'?<p>{m.content as string}</p>:<><div className="assistant-name">菲菲 <span>FF Agent</span></div><p>{(m.content as ChatResult).message}</p>{(m.content as ChatResult).cards.map((c,i)=>renderCard(c,i,m.id))}</>}</div></div>)}
            {sending&&<div className="thinking" role="status"><Sparkles size={15}/><span>正在理解需求并检查业务规则</span><LoaderCircle className="spin" size={14}/></div>}
          </div>{hasUnread&&<div className="reading-jump"><button type="button" onClick={jumpToLatest}>回到最新消息</button></div>}<form className="composer" onSubmit={e=>{e.preventDefault();send()}}><div className="composer-input"><textarea ref={input} aria-label="输入银行业务需求" value={text} onChange={e=>setText(e.target.value)} placeholder="说说你想做什么，例如：看看这个月钱花在哪儿" rows={2} maxLength={2000} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();send()}}}/><div className="composer-actions"><span><ShieldCheck size={13}/> 敏感操作需要你的确认</span><button aria-label="发送需求" type="submit" disabled={!!busy||sending||!text.trim()}>{sending?<LoaderCircle size={18} className="spin"/>:<ArrowUp size={19}/>}</button></div></div><div className="composer-caption">{state?.planner_mode==='model'?'模型只规划任务，执行权限由后端规则控制。':'当前为本地规则演示模式，可体验查账、订阅与转账流程。'}</div></form></section>
          {hasTaskRail&&<aside className="insights-column" aria-label="任务与待办"><div className="ff-rail-heading"><h2>任务与待办</h2>{pending.length>0&&<span>{pending.length} 项待处理</span>}</div><PendingNavigator items={pending.map(op=>({id:op.id,title:opName(op),status:op.status,amount_cents:(op as unknown as {payload?:{amount_cents?:number}}).payload?.amount_cents}))} blocked={!!busy||sending} onLocate={locateOperation}/>{pending.length>0&&sidePending.length===0&&<p className="ff-rail-note">待确认操作在对话的最新卡片中，请在对话中核对。</p>}<ConversationGoalPanel goal={goal} busy={!!busy||sending} onChoice={chooseGoal} onContinue={()=>{void continueGoal()}} onCheck={()=>{void refresh().catch(()=>{})}}/>{hasGoalControlContext&&<GoalControlsPanel current={goal as GoalControlView|null} ready={goalControlsReady} state={{...goalControlState,busy:goalControlState.busy||!!busy||sending}} intent={goalControlIntent} agreed={goalControlAgreed} onRequest={requestGoalControl} onAgree={agreeGoalControl} onCancel={clearGoalControlIntent} onConfirm={()=>{void confirmGoalControl()}} onCheck={()=>{void checkGoalControl()}}/>}{(state.plans||[]).some(planNeedsAttention)&&<section className="panel task-teaser"><h2>有任务需要核对</h2><p>查看原计划、待确认操作和中断位置，再决定下一步。</p><button className="outline-button" onClick={()=>setView('tasks')}>查看任务进度 <ArrowRight size={14}/></button></section>}{sidePending.length>0&&<section className="pending-section"><div className="section-title"><h2>待你决定</h2><span>{sidePending.length}</span></div>{sidePending.map((op,index)=>renderCard({type:'operation',operation:op},index))}</section>}<div className="small-note"><Fingerprint size={18}/><p>你的决定才是最终授权。<br/>账单直接查询，敏感操作先确认。</p></div></aside>}</div>}
          {view==='aa'&&<AARequestsSection key={state?.name} blocked={!!busy||sending} runMutation={runProfileMutation}/>}
          {view==='scheduled'&&<ScheduledSection key={state?.name} blocked={!!busy||sending} locked={!!state?.locked} runMutation={runProfileMutation} onStateRefresh={refresh} renderOperation={(op,reload)=><OperationCard op={op as unknown as Operation} busy={!!busy||sending} locked={!!state?.locked} onAction={async(o,a,c)=>{await mutate(o,a,c);await reload()}} onReceipt={checkReceipt}/>}/>}
          {view==='birthday'&&state&&birthdayGoal&&<p className="panel" role="status">生日安排正在对话中进行，请在对话中继续。</p>}{view==='birthday'&&state&&<BirthdaySection key={state.name+':'+(birthdayFlowId||'')} initialFlowId={birthdayFlowId} blocked={!!busy||sending||birthdayGoal} locked={!!state.locked} runMutation={runProfileMutation} renderOperation={(op,reload,canExecute)=><OperationCard op={op as unknown as Operation} birthdayContext birthdayActions={canExecute} busy={!!busy||sending} locked={!!state.locked||!canExecute} onAction={async(o,a,c)=>{await mutate(o,a,c);await reload()}} onReceipt={checkReceipt}/>}/>}
          {view==='compute'&&<><CodegenSection key={'ai-'+state?.name} blocked={!!busy||sending} runMutation={runProfileMutation}/><SandboxSection key={state?.name} blocked={!!busy||sending} runMutation={runProfileMutation}/></>}
          {view==='channel'&&<ChannelInboxSection blocked={!!busy||sending} onDraft={requestExample}/>}
          {view==='bills'&&state&&<AnnualBillSection key={state.name+'-'+(detailBill?.period.length===4?detailBill.period:state.business_date.slice(0,4))} initialYear={detailBill?.period.length===4?detailBill.period:state.business_date.slice(0,4)}/> }
          {view==='bills'&&!detailBill&&<section className="panel details-panel"><BillMissing period={state?.business_date.slice(0,7)} busy={!!busy} onRetry={()=>{void retryWorkspace()}}/></section>}{view==='bills'&&detailBill&&<section className="panel details-panel"><div className="section-title"><h2>{detailBill.period} 交易明细</h2><span className="mode-label">{detailBill.evidence_ids.length} 条模拟流水</span></div><div className="bill-overview"><div><small>消费支出</small><b>¥ {money(detailBill.expense_cents)}</b></div><div><small>退款入账</small><b>¥ {money(detailBill.refund_cents)}</b></div><div><small>收入</small><b>¥ {money(detailBill.income_cents)}</b></div></div><div className="table-wrap"><table><thead><tr><th>商户 / 对象</th><th>分类</th><th>日期</th><th>金额</th></tr></thead><tbody>{detailBill.transactions.map(t=><tr key={t.id}><td><span className="merchant-icon">{['INCOME','REFUND','INVESTMENT_REDEMPTION'].includes(t.kind)?<ArrowDownLeft size={16}/>:<ArrowUpRight size={16}/>}</span>{t.merchant}</td><td><span className="table-tag">{t.category}</span></td><td>{t.occurred_at.slice(0,10)}</td><td className={['INCOME','REFUND','INVESTMENT_REDEMPTION'].includes(t.kind)?'credit':''}>{['INCOME','REFUND','INVESTMENT_REDEMPTION'].includes(t.kind)?'+':t.kind==='INTERNAL'?'':'−'} ¥ {money(t.amount_cents)}</td></tr>)}</tbody></table></div></section>}
          {view==='subscriptions'&&<RecurringRemindersSection enabled={subs!==null} busy={!!busy||sending} onConsentExpired={()=>setSubs(null)} onAuthorize={()=>{setView('home');void send('看看有哪些订阅')}}/>}
          {view==='subscriptions'&&<section className="panel details-panel"><div className="section-title"><h2>我的自动续费</h2><span className="mode-label">查询授权有效期 30 分钟</span></div>{unavailableGroups.includes('subscriptions')?<div className="permission-empty"><ShieldCheck size={30}/><h3>订阅信息暂时不可用</h3><p>这不代表没有订阅。可以先核对已有操作回执，再重试查询。</p><button className="outline-button" disabled={!!busy} onClick={()=>{void retryWorkspace()}}>重新核对状态</button></div>:subs?<div className="subscription-list">{subs.map((sub,i)=><article key={sub.id}><div className="subscription-logo" style={{background:categoryColors[i%5]}}>{sub.merchant.slice(0,1)}</div><div className="subscription-info"><h3>{sub.merchant}<span className={sub.status==='ACTIVE'?'table-tag':'table-tag cancelled'}>{sub.status==='ACTIVE'?'续费中':'已取消'}</span></h3><p>{sub.description}</p><small>{sub.status==='ACTIVE'?`预计下次扣费 ${sub.next_charge}`:'后续自动续费已停止'}</small></div><div className="subscription-price">¥{money(sub.amount_cents)}<small>/ 月</small></div><button className="outline-button" disabled={!!busy||sending||sub.status!=='ACTIVE'||!!state?.locked} onClick={()=>prepareSubscription(sub)}>{sub.status!=='ACTIVE'?<><Check size={14}/> 已取消</>:sub.direct_cancel?'准备取消':'查看取消方式'}</button></article>)}</div>:<div className="permission-empty"><ShieldCheck size={36}/><h3>查询代扣信息，需要你的确认</h3><p>确认后可以查看订阅商户、扣费金额与续费日期。</p><button className="primary-button" disabled={!!busy||sending} onClick={()=>send('看看有哪些订阅')}>准备查询 <ArrowRight size={16}/></button></div>}</section>}
          {view==='wealth'&&<><InvestmentProfileSection key={state?.name} blocked={!!busy||sending} runMutation={runProfileMutation} onStatus={setInvestmentStatus}/><WealthPanel purchaseAllowed={investmentStatus==='VALID'} locked={!!state?.locked} snapshot={wealthSnapshot} accounts={state?.accounts??null} loading={wealthLoading} busy={!!busy||sending} error={wealthError} onPrepare={input=>{void prepareWealth(input)}} onReceipt={id=>{void checkReceipt(id)}} onRefresh={()=>{void refresh()}}/></>}
          {view==='cards'&&<><CardControlSection key={state.name} cards={bankCardsLoading||bankCardsError?null:bankCards} blocked={!!busy||sending||bankCardsLoading||!!bankCardsError} locked={!!state.locked} runMutation={runProfileMutation} onStateRefresh={refresh} onPrepared={async()=>{await refresh();setView('home');setNotice('请核对原操作详情和回执，再明确决定是否执行。')}}/><CardsPanel locked={!!state?.locked} cards={bankCards} loading={bankCardsLoading} busy={!!busy||sending} error={bankCardsError} onRefresh={()=>{void refresh()}} onPrepare={(card,action)=>{void prepareBankCard(card,action)}}/><VirtualCardsPanel locked={!!state?.locked} accounts={state?.accounts??null} cards={bankCards} applications={virtualApps} loading={bankCardsLoading} busy={!!busy||sending} error={bankCardsError||virtualError} onPrepare={account=>{void prepareVirtualCard(account)}} onReceipt={id=>{void checkReceipt(id)}} onRefresh={()=>{void refresh()}}/></>}
          {view==='audit'&&<section className="panel details-panel"><div className="section-title"><h2>最近操作轨迹</h2><span className="mode-label">按业务事件记录</span></div>{events.length===0?<p className="empty-copy">完成查询或准备操作后，会在这里留下记录。</p>:<div className="audit-list">{events.map(event=><article key={event.id}><span className="audit-icon"><ShieldCheck size={17}/></span><div><b>{event.detail}</b><small>{event.event}{event.operation_id?' · '+event.operation_id.slice(0,15):''}</small></div><time>{new Date(event.created_at).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit'})}</time></article>)}</div>}</section>}
          {view==='tasks'&&<section className="tasks-workspace"><div className="section-title"><h2>最近任务计划</h2><button className="outline-button" disabled={!!busy} onClick={()=>{void retryWorkspace()}}>核对进度</button></div>{(state.plans||[]).length===0?<section className="panel permission-empty"><CheckCheck size={28}/><h3>还没有业务计划</h3><p>查账、准备转账等请求会留下可核对的任务步骤。</p></section>:(state.plans||[]).map(plan=><TaskPlanView key={plan.id} plan={plan} busy={!!busy||sending} onControl={action=>{void controlPlan(plan,action)}}/>)}{(state.handoffs||[]).map(summary=><div key={summary.id}><LocalHandoffSummary summary={summary}/><HandoffProgressCard summary={summary} blocked={!!busy}/><UserCaseCard summary={summary} api={api} blocked={!!busy||sending} runMutation={runProfileMutation}/></div>)}</section>}
          {view==='memory'&&<section className="panel details-panel memory-panel"><div className="section-title"><h2>我的偏好与笔记</h2><span className="mode-label">{state?.memory_count||0} 条明确保存</span></div><p className="memory-note">这里只保存你明确要求记住的内容，可随时修改。笔记用于理解偏好，交易事实仍以银行记录为准。</p><form className="memory-search" onSubmit={e=>{e.preventDefault();searchMemory()}}><input aria-label="查找记忆" value={memoryQuery} onChange={e=>setMemoryQuery(e.target.value)} maxLength={100} placeholder="按内容或主题查找，例如：账单"/><button className="outline-button" type="submit">查找</button></form>{memories.length===0?<div className="memory-empty"><BookOpen size={30}/><h3>{memoryQuery?'没有找到相关笔记':'还没有明确保存的记忆'}</h3><p>在对话里说“记住：我喜欢按周查看账单”，就能留下笔记。</p><button className="outline-button" onClick={()=>requestExample('记住：')}>去对话里记一条 <ArrowRight size={14}/></button></div>:<div className="memory-list">{memories.map(note=><article className="memory-card" key={note.id}><div className="memory-card__head"><span><BookOpen size={15}/> 偏好笔记</span><span className="memory-badge">你明确提供</span></div>{editingMemory===note.id?<><textarea aria-label="编辑记忆内容" value={memoryDraft} onChange={e=>setMemoryDraft(e.target.value)} maxLength={300} rows={3}/><div className="memory-actions"><button className="outline-button" disabled={!!busy} onClick={()=>setEditingMemory('')}>取消编辑</button><button className="primary-button" disabled={!!busy||!memoryDraft.trim()} onClick={()=>changeMemory(note,'update')}>保存修改</button></div></>:<><p>{note.content}</p><div className="memory-tags">{note.tags.map(tag=><span key={tag}>{tag}</span>)}{note.related_ids.length>0&&<small>与 {note.related_ids.length} 条笔记主题相关</small>}</div><div className="memory-card__foot"><small>更新于 {new Date(note.updated_at).toLocaleString('zh-CN')} · 第 {note.version} 版</small><div className="memory-actions"><button disabled={!!busy} onClick={()=>{setEditingMemory(note.id);setMemoryDraft(note.content)}}>编辑</button><button disabled={!!busy} onClick={()=>changeMemory(note,'forget')}>移出记忆</button></div></div></>}</article>)}</div>}<p className="memory-footnote">最多展示 50 条匹配笔记。移出后助手不再检索该笔记，聊天记录仍可保留。</p></section>}
          <footer><span>FF Agent · 本机模拟</span></footer>
        </>}
      </main>
    </div>
  </div>
}

function OperationCard({op,busy,locked=false,readOnly=false,birthdayContext=false,birthdayActions=false,onAction,onReceipt}:{op:Operation;busy:boolean;locked?:boolean;readOnly?:boolean;birthdayContext?:boolean;birthdayActions?:boolean;onReceipt:(id:string)=>void;onAction:(op:Operation,action:'confirm'|'verify'|'cancel',code?:string)=>Promise<void>}){
  const [code,setCode]=useState('')
  const codeId=useId()
  const copy=operationCopy(op.type)
  const cardType=op.type==='card_spend'||op.type==='card_controls_update'||op.type==='card_credential_change'?op.type:null
  let cardOperation:CardOperation|null=null
  if(cardType&&typeof op.payload.card_id==='string'){
    try{cardOperation=parseCardOperation(op,op.payload.card_id,cardType)}catch{/* only the original receipt is available */}
  }
  const scheduledType=op.type==='scheduled_transfer_authorize'
  let scheduledOperation:ScheduleOperation|null=null
  if(scheduledType){try{scheduledOperation=parseScheduleOperation(op)}catch{/* malformed authorization remains receipt-only */}}
  const birthdayType=op.type==='birthday_budget_reserve'||op.type==='birthday_order'
  let birthdayOperation:BirthdayOperation|null=null
  if(birthdayType&&birthdayContext){try{birthdayOperation=parseBirthdayOperation(op)}catch{/* malformed birthday operation remains receipt-only */}}
  const recognized=(cardType===null||cardOperation!==null)&&(!scheduledType||scheduledOperation!==null)&&(!birthdayType||birthdayOperation!==null)
  const isPending=!readOnly&&op.status==='WAITING_CONFIRMATION'&&copy.kind!=='unsupported'&&recognized&&(!birthdayType||birthdayActions)
  const verifying=!readOnly&&op.status==='WAITING_VERIFICATION'&&copy.kind!=='unsupported'&&recognized&&(!birthdayType||birthdayActions)
  const success=op.status==='SUCCEEDED'&&recognized
  return <div className={'operation-card '+(op.risk==='RED'?'red ':'')+(success?'completed':'')} data-operation-id={op.id} tabIndex={-1}><div className="operation-head"><span>{success?<CheckCheck size={16}/>:op.risk==='RED'?<Fingerprint size={16}/>:<ShieldCheck size={16}/>} {opName(op)}</span><em>{((cardType&&cardOperation===null)||(scheduledType&&scheduledOperation===null)||(birthdayType&&birthdayOperation===null))?'资料待核对':labels[op.status]||op.status}</em></div>{birthdayType?(birthdayContext?<BirthdaySummary op={birthdayOperation}/>:<p>请在“生日安排”中核对当前流程和详情，再确认本机模拟操作。</p>):scheduledType?<ScheduledSummary op={scheduledOperation}/>:op.type==='transfer'?<><div className="operation-amount">¥ {money(op.payload.amount_cents||0)}</div><div className="operation-details"><span>收款人<b>{op.payload.payee_name} <small>{op.payload.payee_phone}</small></b></span><span>付款账户<b>{op.payload.account_name}</b></span><span>手续费<b>¥ {money(op.payload.fee_cents||0)}</b></span></div></>:copy.kind==='wealth'?<><div className="operation-amount">¥ {money(op.payload.amount_cents||0)}</div><div className="operation-details"><span>演示产品<b>{op.payload.product_name}</b></span><span>关联账户<b>{op.payload.account_name}</b></span><span>本次份额<b>{(op.payload.units_1e4||0)/10000} 份</b></span><span>费用<b>¥ {money(op.payload.fee_cents||0)}</b></span><span>每份演示估值<b>¥ {money(op.payload.nav_cents||0)}</b></span></div><p>仅本机模拟。固定演示估值不代表真实报价或预期收益；确认后仍需模拟身份验证。</p></>:cardType?<CardOperationSummary op={cardOperation}/>:copy.kind==='virtual_card'?<><h3>{op.payload.card_name||'虚拟演示卡'}</h3><div className="operation-details"><span>关联账户<b>{op.payload.account_name}</b></span></div><p>仅本机模拟，每账户限一张演示虚拟卡；确认后生成，不改变余额。</p></>:copy.kind==='card'?<><h3>{op.payload.card_name||'演示银行卡'}</h3><div className="operation-details"><span>卡片尾号<b>{op.payload.masked_number||'需重新核对'}</b></span><span>状态变化<b>{op.payload.from_status==='ACTIVE'?'可用':op.payload.from_status==='LOST'?'已挂失':'需核对'} → {op.payload.to_status==='LOST'?'挂失':op.payload.to_status==='ACTIVE'?'可用':'需核对'}</b></span></div><p>仅模拟卡片状态操作，不改变余额、不退款。确认后仍需模拟身份核验。</p></>:op.type==='subscription_cancel'?<><h3>{op.payload.merchant}</h3><p>{op.payload.effect}</p><div className="operation-details"><span>取消费用<b>¥ {money(op.payload.fee_cents||0)}</b></span></div></>:op.type==='subscription_query'?<><h3>本人订阅与代扣</h3><p>查看商户、金额及续费时间，授权有效期 30 分钟。{op.payload.proposed_cancel?`查询后会准备 ${op.payload.proposed_cancel} 的取消确认，仍需单独确认。`:""}</p></>:<p>暂不支持这类业务，请只核对原回执，不在此执行。</p>}{success&&<div className="receipt"><BadgeCheck size={14}/>{op.result?.transaction_id?'模拟流水 '+op.result.transaction_id.slice(0,15):(copy.kind==='card'||copy.kind==='virtual_card')?(op.result?.message||copy.successLabel):op.type==='subscription_cancel'?'后续续费已停止':op.type==='subscription_query'?'查询已完成，可查看订阅列表':copy.successLabel}</div>}{isPending&&<><div className="operation-warning">{op.risk==='RED'?(copy.verificationReason||'此操作需要核对身份')+'，确认后还需完成模拟验证。':'操作尚未执行，请核对详情后确认。'}</div><div className="operation-actions"><button className="outline-button" disabled={busy} onClick={()=>onAction(op,'cancel')}>暂不操作</button><button className="primary-button" disabled={busy||locked} onClick={()=>onAction(op,'confirm')}>{busy?<LoaderCircle className="spin" size={14}/>:<Check size={14}/>}确认{opName(op)}</button></div></>}{verifying&&<div className="verification"><label htmlFor={codeId}>模拟验证 · 演示验证码 246810</label><div><input id={codeId} disabled={busy||locked} aria-label="模拟验证码" type="text" inputMode="numeric" maxLength={6} autoComplete="off" value={code} onChange={e=>setCode(normalizeVerificationCode(e.target.value))} placeholder="输入 6 位演示验证码"/><button className="primary-button" disabled={busy||locked||code.length!==6} onClick={()=>onAction(op,'verify',code)}>{busy?<LoaderCircle className="spin" size={14}/>:<Fingerprint size={15}/>}验证</button></div><button className="text-button" disabled={busy} onClick={()=>onAction(op,'cancel')}>取消本次操作</button></div>}<button className="text-button" onClick={()=>onReceipt(op.id)}>核对业务回执 <ArrowUpRight size={13}/></button><div className="operation-id">操作编号 {op.id.slice(0,15)}</div></div>
}
