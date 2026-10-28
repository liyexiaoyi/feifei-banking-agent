import type {Schedule} from './scheduled-client.ts'
import type {ScheduledState} from './scheduled-controller.ts'
import './scheduled.css'

export type ScheduledPanelProps={state:ScheduledState,blocked:boolean,locked:boolean,payeeId:string,amount:string,due:string,consent:boolean,autoEnabled:boolean,onPayee(v:string):void,onAmount(v:string):void,onDue(v:string):void,onConsent(v:boolean):void,onPrepare():void,onRefresh():void,onCheck():void,onStartNew():void,onCancel(s:Schedule):void,onOpen(id:string,s:Schedule):void,onDrive():void,onAuto(v:boolean):void}

const STATUS:Record<string,string>={AUTHORIZING:'等待确认授权',ACTIVE:'已授权，尚未付款',CHILD_PENDING:'等待确认付款',CHILD_IN_FLIGHT:'付款结果待核对',SUCCEEDED:'模拟付款已完成',NOT_PAID:'本次未付款，不会自动补付',CANCELLED:'已取消'}
const OBSERVED:Record<string,string>={AUTH_EXPIRED:'授权已过期',MISSED_GRACE:'已超过执行时间，先核对',UNKNOWN:'付款结果待核对',EXECUTING:'付款结果待核对',MANUAL_REVIEW:'付款结果待核对'}
const PENDING_OBS=['UNKNOWN','EXECUTING','MANUAL_REVIEW']

export function validCents(text:string):boolean{
  const m=/^(0|[1-9]\d{0,6})(?:\.(\d{1,2}))?$/.exec(text.trim())
  if(!m)return false
  const c=Number(m[1])*100+Number((m[2]||'').padEnd(2,'0'))
  return Number.isSafeInteger(c)&&c>=1&&c<=100000000
}
export function validDue(text:string):boolean{
  const m=/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(text)
  if(!m)return false
  const [y,mo,d,h,mi,s]=[m[1],m[2],m[3],m[4],m[5],m[6]||'0'].map(Number)
  const t=new Date(Date.UTC(y,mo-1,d,h,mi,s))
  return t.getUTCFullYear()===y&&t.getUTCMonth()===mo-1&&t.getUTCDate()===d&&h<24&&mi<60&&s<60
}
export function yuan(c:number):string{return Math.floor(c/100)+'.'+String(c%100).padStart(2,'0')+' 元'}
export function chinaTime(iso:string):string{return iso.slice(0,10)+' '+iso.slice(11,16)+'（北京时间）'}

const AUTH_END:Record<string,string>={CANCELLED:'授权已取消，本次未付款',EXPIRED:'授权已过期',FAILED:'授权未生效',REJECTED:'授权未生效'}
const CHILD_END:Record<string,string>={CANCELLED:'付款已取消，本次未付款',EXPIRED:'付款确认已过期，本次未付款'}
function label(s:Schedule):string{
  if(s.status==='AUTHORIZING'&&AUTH_END[s.observed_status])return AUTH_END[s.observed_status]
  if(String(s.status).startsWith('CHILD_')&&CHILD_END[s.observed_status])return CHILD_END[s.observed_status]
  if(String(s.status).startsWith('CHILD_')&&s.observed_status==='SUCCEEDED')return '模拟付款已完成'
  if(String(s.status).startsWith('CHILD_')&&s.observed_status==='WAITING_VERIFICATION')return '等待付款验证'
  return OBSERVED[s.observed_status]||STATUS[s.status]||'状态待核对'
}
const CHILD_OK=['WAITING_CONFIRMATION','WAITING_VERIFICATION','READY']
function cancellable(s:Schedule):boolean{
  if(PENDING_OBS.includes(s.observed_status)||AUTH_END[s.observed_status]||CHILD_END[s.observed_status])return false
  if(s.status==='CHILD_PENDING')return CHILD_OK.includes(s.observed_status)
  return (s.status==='ACTIVE'||s.status==='AUTHORIZING')&&!s.child_operation_id
}
function settled(s:any):boolean{
  return !!s&&['ACTIVE','SUCCEEDED','NOT_PAID','CANCELLED'].includes(s.status)&&!PENDING_OBS.includes(s.observed_status)&&(s.status!=='ACTIVE'||!s.child_operation_id)||(!!s&&s.status==='AUTHORIZING'&&!!AUTH_END[s.observed_status]&&!s.child_operation_id)
}

function row(s:Schedule,p:ScheduledPanelProps,off:boolean){
  const submit=(e:any)=>{e.preventDefault();if(new FormData(e.currentTarget).get('confirm')==='true')p.onCancel(s)}
  return <article key={s.id} className="sched-row" aria-label={'定时转账 '+s.id}>
    <p className="sched-status">{label(s)}</p>
    <p>{s.payee_name}（{s.payee_phone}） {yuan(s.amount_cents)}</p>
    <p>执行时间：{chinaTime(s.due_at)}</p>
    <div className="sched-actions">
      {s.authorization_operation_id?<button type="button" disabled={p.state.busy||p.blocked} onClick={()=>p.onOpen(s.authorization_operation_id,s)}>查看授权</button>:null}
      {s.child_operation_id?<button type="button" disabled={p.state.busy||p.blocked} onClick={()=>p.onOpen(s.child_operation_id as string,s)}>查看付款</button>:null}
    </div>
    {cancellable(s)?<form className="sched-cancel" onSubmit={submit}>
      <label><input type="checkbox" name="confirm" value="true" required disabled={off}/>我确认取消这笔定时授权</label>
      <button type="submit" disabled={off}>取消此定时授权</button>
    </form>:null}
  </article>
}

export default function ScheduledPanel(p:ScheduledPanelProps){
  const st=p.state
  const hidden=st.invalidSession||!!st.error
  const off=st.busy||p.blocked||p.locked||st.needsCheck||st.invalidSession
  const edit=off||!!st.pending
  const data=hidden?null:st.data
  const ready=!edit&&!!data&&p.payeeId!==''&&(data.payees||[]).some(x=>x.id===p.payeeId)&&validCents(p.amount)&&validDue(p.due)&&p.consent===true
  const sx:any=st
  const pk=sx.pending?.kind
  const eligible=pk==='create'?settled(sx.recovered):pk==='cancel'?['SUCCEEDED','NOT_PAID','CANCELLED'].includes(sx.recovered?.status)&&!PENDING_OBS.includes(sx.recovered?.observed_status):pk==='drive'?!!st.data:false
  const coff=(s:any)=>st.busy||p.blocked||st.invalidSession||((st.needsCheck||!!st.pending)&&!(sx.checked===true&&pk==='create'&&!st.error&&sx.recovered?.id===s.id&&sx.recovered?.version===s.version&&!PENDING_OBS.includes(s.observed_status)))
  const canNew=sx.checked===true&&!!st.pending&&!st.error&&!st.busy&&!p.blocked&&!st.invalidSession&&eligible
  return <section className="sched" aria-label="定时转账">
    <h2>定时转账</h2>
    <p className="sched-note">本地模拟，未连接真实银行、短信或IM。授权不是付款，授权后尚未付款，也不预留未来资金。到期时重新检查余额和风险，超过执行时间24小时不补付。</p>
    {st.invalidSession?<p role="alert">登录状态已变化，请刷新页面。</p>:null}
    {st.error?<p role="alert">{st.error}</p>:null}
    {st.needsCheck?<p role="status">上次请求结果未确认，请先只读核对原请求，不会重新发送。</p>:null}
    {hidden?null:<form className="sched-form" onSubmit={(e:any)=>{e.preventDefault();if(ready)p.onPrepare()}}>
      <label>选择收款人<select value={p.payeeId} disabled={edit} onChange={(e:any)=>p.onPayee(e.target.value)}>
        <option value="">请选择收款人</option>
        {(data?.payees||[]).map(x=><option key={x.id} value={x.id}>{x.name}（{x.masked_phone}）</option>)}
      </select></label>
      <label>金额（元）<input type="text" inputMode="decimal" value={p.amount} disabled={edit} onChange={(e:any)=>p.onAmount(e.target.value)}/></label>
      <label>执行时间（北京时间）<input type="datetime-local" value={p.due} disabled={edit} onChange={(e:any)=>p.onDue(e.target.value)}/></label>
      <label className="sched-check"><input type="checkbox" checked={p.consent} disabled={edit} onChange={(e:any)=>p.onConsent(e.target.checked)}/>我已核对时间、收款人和金额，先准备授权</label>
      <button type="submit" disabled={!ready}>准备定时授权</button>
    </form>}
    <div className="sched-actions">
      <button type="button" disabled={st.busy||p.blocked||st.invalidSession} onClick={()=>p.onRefresh()}>只读刷新</button>
      <button type="button" disabled={st.busy||p.blocked||st.invalidSession||!st.needsCheck} onClick={()=>p.onCheck()}>核对原请求</button>
      <button type="button" disabled={!canNew} onClick={()=>{if(canNew)p.onStartNew()}}>已核对，填写另一笔</button>
      <button type="button" disabled={off} onClick={()=>p.onDrive()}>检查到期任务</button>
    </div>
    <label className="sched-check"><input type="checkbox" checked={p.autoEnabled} disabled={!p.autoEnabled&&off} onChange={(e:any)=>p.onAuto(e.target.checked)}/>启用本页到期检查（仅此页打开时）</label>
    <p className="sched-note">默认关闭。开启后每5秒检查一次，关闭页面即停止。低风险的到期任务可能自动模拟付款，大额付款需要新的确认和验证。开启不代表确认其他任何操作。</p>
    {data?<div className="sched-list">{data.schedules.map(s=>row(s,p,coff(s)))}</div>:null}
  </section>
}
