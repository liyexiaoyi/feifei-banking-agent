import './conversation-goal.css'

export interface ConversationGoalChoice { id: string; label: string; price_cents: number }
export interface ConversationGoalStep { label: string; state: string }
export interface ConversationGoalView {
  id: string; version: number; kind: string; status: string; phase: string
  title: string; question: string; summary: string[]
  choices: ConversationGoalChoice[]; steps: ConversationGoalStep[]
  next_action: string; operation_ids: string[]
}
export interface ConversationGoalPanelProps {
  goal: ConversationGoalView | null; busy: boolean
  onChoice: (id: string) => void; onContinue: () => void; onCheck: () => void
}

const NOTE = '本机模拟：这里只显示目标进度，不连接商户，不会真实付款、订货或配送。涉及金额的确认只在对应的操作卡中进行。'
const STATUS: Record<string, string> = { ACTIVE: '进行中', PAUSED: '已暂停', COMPLETED: '已完成（本机模拟）', CANCELLED: '已取消', FAILED: '未成功', EXPIRED: '已过期', BLOCKED: '当前步骤受阻', UNKNOWN: '待核对' }
const STEP: Record<string, string> = { PENDING: '未开始', READY: '可进行', RUNNING: '处理中', OBSERVED: '已读取', WAITING_CONFIRMATION: '等待确认', WAITING_VERIFICATION: '等待验证', SUCCEEDED: '已完成', FAILED: '未成功', SKIPPED: '已跳过', PAUSED: '已暂停', CANCELLED: '已取消', EXPIRED: '已过期', BLOCKED: '受阻', UNKNOWN: '待核对' }

const isText = (v: unknown): v is string => typeof v === 'string'
const RENEWAL_NOTE = '本机模拟：这里只显示目标进度。取消只会停止后续续费，不会退款；真实银行和商户尚未接入。'
const validCents = (v: unknown, free = false): v is number => typeof v === 'number' && Number.isSafeInteger(v) && (free ? v >= 0 : v > 0)
function money(c: number): string {
  const y = String(Math.floor(c / 100)).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return y + '.' + String(c % 100).padStart(2, '0') + ' 元'
}
function wellFormed(g: ConversationGoalView): boolean {
  return isText(g.id) && g.id !== '' && Number.isSafeInteger(g.version) && isText(g.status) && isText(g.phase)
    && isText(g.title) && isText(g.question) && isText(g.next_action)
    && Array.isArray(g.summary) && Array.isArray(g.choices) && Array.isArray(g.steps) && Array.isArray(g.operation_ids)
}
const stepUnknown = (s: unknown): boolean => !s || typeof s !== 'object' || (s as ConversationGoalStep).state === 'UNKNOWN'

export default function ConversationGoalPanel(p: ConversationGoalPanelProps) {
  const g = p.goal
  if (!g || typeof g !== 'object') return null
  const renewal = g.kind === 'spending_subscriptions'
  const head = (<><h2 id="cg-title">当前目标</h2><p className="cg-note">{renewal ? RENEWAL_NOTE : NOTE}</p></>)
  if (!wellFormed(g)) {
    return (<section className="cg" aria-labelledby="cg-title">{head}
      <p className="cg-alert" role="alert">目标信息无效，不能继续。请核对原请求。</p>
      <div className="cg-actions"><button type="button" disabled={p.busy} onClick={() => p.onCheck()}>核对原请求</button></div>
    </section>)
  }
  const unknown = g.status === 'UNKNOWN' || g.steps.some(stepUnknown)
  const known = Object.prototype.hasOwnProperty.call(STATUS, g.status)
  const waiting = g.phase === 'WAITING_CONFIRMATION' || g.phase === 'WAITING_VERIFICATION'
  const canWrite = !p.busy && !unknown && known && g.status === 'ACTIVE' && !waiting
  const canChoose = canWrite && g.next_action === 'CHOOSE'
  const canContinue = canWrite && g.phase === 'READY' && g.next_action === 'CONTINUE'
  const showContinue = g.next_action === 'CONTINUE' && !waiting && !unknown
  const statusLabel = unknown ? '待核对' : STATUS[g.status] ?? '待核对'
  const summary = g.summary.filter(isText)
  return (<section className="cg" aria-labelledby="cg-title">{head}
    <h3 className="cg-goal">{g.title}</h3>
    <p className="cg-status">状态：{statusLabel}</p>
    {unknown && <p className="cg-alert" role="alert">有结果待核对。核对前不会继续、选择或取消，也不能视为已完成。请先核对原请求。</p>}
    {!unknown && g.phase === 'WAITING_CONFIRMATION' && <p className="cg-hint" role="status">{renewal ? '等待你在操作卡中确认查询授权或取消事项。这里不会代为确认。' : '等待你在操作卡中确认金额。这里不会代为确认。'}</p>}
    {!unknown && g.phase === 'WAITING_VERIFICATION' && <p className="cg-hint" role="status">等待在操作卡中完成验证。这里不会代为验证。</p>}
    {!unknown && !waiting && g.status === 'PAUSED' && <p className="cg-hint">目标已暂停，这里不提供继续操作。</p>}
    {!unknown && !known && <p className="cg-alert" role="alert">目标状态无法识别，不能继续。</p>}
    {g.question !== '' && <p className="cg-question">{g.question}</p>}
    {summary.length > 0 && (<ul className="cg-summary">{summary.map((x, i) => <li key={i}>{x}</li>)}</ul>)}
    {g.choices.length > 0 && (<ul className="cg-choices">{g.choices.map((c, i) => {
      if (!c || typeof c !== 'object' || !isText(c.id) || c.id === '' || !isText(c.label)) return <li key={i} className="cg-hint">有一项选项信息无效，未提供选择。</li>
      if (!validCents(c.price_cents, renewal)) return <li key={i} className="cg-hint">{c.label}：报价无效，需核对，未提供选择。</li>
      const id = c.id
      return (<li key={i}><button type="button" disabled={!canChoose} onClick={() => { if (canChoose) p.onChoice(id) }}>{'选择 ' + c.label + ' ' + money(c.price_cents)}</button></li>)
    })}</ul>)}
    {g.steps.length > 0 && (<ol className="cg-steps">{g.steps.map((s, i) => {
      const st = s && typeof s === 'object' ? s : null
      const label = st && isText(st.label) ? st.label : '未知步骤'
      const state = !st || st.state === 'UNKNOWN' ? '待核对' : STEP[st.state] ?? '待核对'
      return <li key={i}><span>{label}：</span><strong>{state}</strong></li>
    })}</ol>)}
    {g.operation_ids.length > 0 && <p className="cg-hint">{renewal ? '相关查询授权或取消事项请在操作卡中查看，确认只在操作卡中进行。' : '相关预留或付款申请请在操作卡中查看，金额确认只在操作卡中进行。'}</p>}
    {(showContinue || unknown || !known) && (<div className="cg-actions">
      {showContinue && <button type="button" disabled={!canContinue} onClick={() => { if (canContinue) p.onContinue() }}>继续下一步</button>}
      {(unknown || !known) && <button type="button" disabled={p.busy} onClick={() => p.onCheck()}>核对原请求</button>}
    </div>)}
  </section>)
}
