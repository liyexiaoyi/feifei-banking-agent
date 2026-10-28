import type { CaseFacts, BankStatus, OperatorCase, FactOperation } from './operator-dto'

const LABELS: Record<BankStatus, string> = {
  UNKNOWN: '结果待核实', WAITING_CONFIRMATION: '等待确认', WAITING_VERIFICATION: '等待验证',
  EXECUTING: '执行中', MANUAL_REVIEW: '需要核对', READY: '待执行', SUCCEEDED: '银行显示成功',
  FAILED: '银行显示失败', CANCELLED: '已取消', EXPIRED: '已过期',
}
const CASE_LABELS: Record<string, string> = { QUEUED: '排队中', IN_REVIEW: '核对中', REPLIED: '已回复', CANCELLED: '已撤回' }
const typeLabel = (t: string) => (t === 'transfer' ? '转账' : t === 'purchase' ? '购买' : '业务')
const money = (c?: number) => (c === undefined ? '' : `¥${(c / 100).toFixed(2)}`)
const DEMO_NOTE = '本机演示操作员不是真实银行客服，不能付款，也不能替您完成验证。'

function SharedRow(op: FactOperation, i: number) {
  return (
    <li key={i} className="oc-row">
      <span>{typeLabel(op.type)}</span> <span>{money(op.amount_cents)}</span>{' '}
      <span>{op.payee_name ?? op.product_name ?? ''}</span> <span>{op.payee_phone ?? op.masked_number ?? ''}</span>{' '}
      <span>{LABELS[op.status]}</span>
    </li>
  )
}

export function CaseFactsPanel({ snapshot, current }: { snapshot: CaseFacts | null; current: CaseFacts | null }) {
  return (
    <section className="oc-facts">
      <h3>分享时的资料</h3>
      {snapshot ? <ul>{snapshot.operations.map(SharedRow)}</ul> : <p>没有分享时的资料。</p>}
      <h3>当前核对结果</h3>
      {current ? (
        <ul>{current.operations.map((op, i) => <li key={i} className="oc-row">{typeLabel(op.type)}：{LABELS[op.status]}</li>)}</ul>
      ) : (
        <p className="oc-warn">暂时无法核对，请勿认为业务已成功。</p>
      )}
    </section>
  )
}

export interface UserCasePanelProps {
  preview: CaseFacts | null; caseView: OperatorCase | null; loading: boolean; busy: boolean; blocked: boolean
  error: string; notice: string; consent: boolean; onConsent: (v: boolean) => void
  onShare: () => void; onWithdraw: () => void; onRefresh: () => void
}

export function UserCasePanel(p: UserCasePanelProps) {
  const c = p.caseView
  const active = c !== null && c.status !== 'CANCELLED'
  const hide = !!p.error || p.loading || (c !== null && c.status === 'CANCELLED')
  const canShare = !!p.preview && p.consent && !p.loading && !p.busy && !p.blocked && !p.error && c === null
  return (
    <section className="oc-panel">
      <h2>请本机演示坐席帮忙核对</h2>
      <p>{DEMO_NOTE}</p>
      {p.notice && <p className="oc-warn" role="status">{p.notice}</p>}
      {p.error && <p className="oc-error" role="alert">{p.error}</p>}
      {c && <p>分享状态：{CASE_LABELS[c.status]}</p>}
      {!hide && (c ? CaseFactsPanel({ snapshot: c.snapshot, current: c.current_facts }) : p.preview && CaseFactsPanel({ snapshot: p.preview, current: null }))}
      {!hide && c && c.status === 'REPLIED' && (
        <div className="oc-reply"><h3>坐席回复</h3><p>{c.reply}</p><p className="oc-warn">此回复仅供参考，不代表银行业务已完成。</p></div>
      )}
      {!active && (
        <label className="oc-check">
          <input type="checkbox" checked={p.consent} disabled={p.busy} onChange={e => p.onConsent(e.currentTarget.checked)} />
          我同意把上面的脱敏资料分享给本机演示坐席
        </label>
      )}
      <div className="oc-actions">
        <button type="button" disabled={!canShare} onClick={p.onShare}>分享给本机演示坐席</button>
        {active && <button type="button" disabled={p.busy || p.blocked || p.loading || !!p.error} onClick={p.onWithdraw}>撤回分享</button>}
        <button type="button" disabled={p.busy || p.loading} onClick={p.onRefresh}>刷新</button>
      </div>
    </section>
  )
}

export interface OperatorPanelProps {
  session: boolean; loading: boolean; busy: boolean; error: string; notice: string
  credential: string; onCredential: (v: string) => void; onLogin: () => void; onLogout: () => void
  cases: OperatorCase[]; selected: OperatorCase | null; reply: string; onReply: (v: string) => void
  onSelect: (c: OperatorCase) => void; onRefresh: () => void; onClaim: () => void; onSubmit: () => void
}

export function OperatorPanel(p: OperatorPanelProps) {
  const head = (
    <>
      <h2>本机演示坐席</h2>
      <p>{DEMO_NOTE}</p>
      {p.notice && <p className="oc-warn" role="status">{p.notice}</p>}
      {p.error && <p className="oc-error" role="alert">{p.error}</p>}
    </>
  )
  if (!p.session) {
    return (
      <section className="oc-panel">
        {head}
        <label className="oc-field">演示口令
          <input type="password" autoComplete="off" value={p.credential} onChange={e => p.onCredential(e.currentTarget.value)} />
        </label>
        <div className="oc-actions">
          <button type="button" disabled={p.busy || p.loading || p.credential === ''} onClick={p.onLogin}>登录本机演示坐席</button>
        </div>
      </section>
    )
  }
  const locked = p.busy || p.loading || !!p.error
  const s = locked ? null : p.selected
  const text = p.reply.trim()
  return (
    <section className="oc-panel oc-operator">
      {head}
      <div className="oc-actions">
        <button type="button" disabled={p.busy || p.loading} onClick={p.onRefresh}>刷新列表</button>
        <button type="button" disabled={p.busy} onClick={p.onLogout}>退出登录</button>
      </div>
      <div className="oc-layout">
        <ul className="oc-list">
          {p.cases.map((c, i) => (
            <li key={i}>
              <button type="button" aria-pressed={p.selected === c} disabled={p.busy || p.loading} onClick={() => p.onSelect(c)}>
                第{i + 1}项：{CASE_LABELS[c.status]}
              </button>
            </li>
          ))}
        </ul>
        {s && (
          <div className="oc-detail">
            <p>状态：{CASE_LABELS[s.status]}</p>
            {s.status !== 'CANCELLED' && CaseFactsPanel({ snapshot: s.snapshot, current: s.current_facts })}
            {s.status === 'QUEUED' && <button type="button" onClick={p.onClaim}>接单核对</button>}
            {s.status === 'IN_REVIEW' && (
              <>
                <label className="oc-field">回复（纯文本，最多500字）
                  <textarea maxLength={500} value={p.reply} onChange={e => p.onReply(e.currentTarget.value)} />
                </label>
                <button type="button" disabled={text === '' || p.reply.length > 500} onClick={p.onSubmit}>提交回复</button>
              </>
            )}
            {s.status === 'REPLIED' && <p>{s.reply}</p>}
          </div>
        )}
      </div>
    </section>
  )
}
