// Pure controlled panel for simulated card settings and spending. No hooks, type-only imports.
// Styles live in CardControlPanel.css; the parent section imports it.
import type { CardInfo, CardOperationStatus } from './card-control-client'
import type { CardControlState } from './card-control-controller'

export type CardMode = '' | 'spend' | 'lock' | 'unlock' | 'single' | 'daily' | 'credential'
export interface CardControlPanelProps {
  cards: CardInfo[] | null; cardId: string; mode: CardMode; state: CardControlState
  blocked: boolean; locked: boolean; amount: string; merchantId: string; newSecret: string; currentSecret: string
  onCard(id: string): void; onMode(mode: CardMode): void; onAmount(text: string): void; onMerchant(id: string): void
  onNewSecret(text: string): void; onCurrentSecret(text: string): void; onPrepare(): void; onRefresh(): void
  onCheck(): void; onStartNew(): void; onOpenOperation(): void
}

const TITLE = '交易设置与模拟消费'
const MODES: readonly CardMode[] = ['spend', 'lock', 'unlock', 'single', 'daily', 'credential']
const MODE_LABEL: Record<string, string> = { spend: '模拟消费', lock: '锁定交易', unlock: '解锁交易', single: '调整单笔限额', daily: '调整每日限额', credential: '设置或修改演示口令' }
const TERMINAL: readonly CardOperationStatus[] = ['SUCCEEDED', 'CANCELLED', 'EXPIRED', 'FAILED']
const STATUS_LABEL: Record<CardOperationStatus, string> = {
  WAITING_CONFIRMATION: '等待确认', WAITING_VERIFICATION: '等待验证', READY: '已就绪待执行', EXECUTING: '执行中',
  MANUAL_REVIEW: '人工复核中', UNKNOWN: '结果待核实', SUCCEEDED: '已完成', CANCELLED: '已取消', EXPIRED: '已过期', FAILED: '未成功'
}

// Duplicated pure decimal parser (no parseFloat); null for invalid, zero, negative, >2 decimals, out of range.
function cents(text: string, max = 100000000): number | null {
  const m = /^(0|[1-9]\d{0,8})(?:\.(\d{1,2}))?$/.exec(text)
  if (!m) return null
  const v = Number(m[1]) * 100 + Number((m[2] ?? '').padEnd(2, '0'))
  return v > 0 && v <= max ? v : null
}
const secretOk = (s: string) => /^[\x21-\x7e]{8,64}$/.test(s)
const yuan = (c: number) => `${Math.floor(c / 100)}.${String(c % 100).padStart(2, '0')} 元`

export default function CardControlPanel(p: CardControlPanelProps) {
  const { state } = p
  if (state.error) {
    return (
      <section className="cc-panel" aria-label={TITLE}>
        <h2 className="cc-title">{TITLE}</h2>
        <p className="cc-error" role="alert">{state.error}。当前卡片资料未能读取，旧资料已隐藏。</p>
        <button type="button" className="cc-button cc-secondary" onClick={p.onRefresh} disabled={state.busy || p.blocked}>刷新卡片资料</button>
      </section>
    )
  }
  const b = state.bundle, c = b?.controls, cred = b?.credential
  const lost = b?.card.status === 'LOST'
  const selectOff = state.busy || p.blocked
  const formOff = selectOff || state.needsCheck || state.pending !== null
  const recorded = state.receipt?.recorded_status
  const shown = state.receipt?.observed_status ?? recorded
  const terminal = !!shown && TERMINAL.includes(shown)
  const amountCents = cents(p.amount, p.mode === 'single' || p.mode === 'daily' ? 5000000 : undefined)

  let valid = false
  if (b && c && cred) {
    switch (p.mode) {
      case 'spend': valid = !lost && !c.locked && amountCents !== null && b.merchants.some(m => m.id === p.merchantId)
        && amountCents <= c.single_limit_cents && c.spent_today_cents + amountCents <= c.daily_limit_cents; break
      case 'lock': valid = !c.locked; break
      case 'unlock': valid = c.locked; break
      case 'single': valid = amountCents !== null && amountCents <= c.daily_limit_cents; break
      case 'daily': valid = amountCents !== null && amountCents >= c.single_limit_cents; break
      case 'credential': valid = !lost && secretOk(p.newSecret) && (!cred.configured || secretOk(p.currentSecret)); break
    }
  }
  const canPrepare = valid && !!b && !state.busy && !p.blocked && !p.locked && !state.needsCheck && !state.pending
  const prepLabel = p.mode === 'spend' ? '准备模拟消费' : p.mode === 'lock' ? '准备锁定交易' : p.mode === 'unlock' ? '准备解锁交易'
    : p.mode === 'single' ? '准备调整单笔限额' : p.mode === 'daily' ? '准备调整每日限额'
    : cred?.configured ? '准备修改演示口令' : '准备设置演示口令'
  const amountField = (label: string) => (
    <div className="cc-field">
      <label htmlFor="cc-amount">{label}</label>
      <input id="cc-amount" className="cc-input" inputMode="decimal" value={p.amount} aria-describedby="cc-amount-hint"
        onChange={e => p.onAmount(e.currentTarget.value)} disabled={formOff} />
      <p id="cc-amount-hint" className="cc-hint">填写正数，最多两位小数。</p>
    </div>
  )

  return (
    <section className="cc-panel" aria-label={TITLE}>
      <h2 className="cc-title">{TITLE}</h2>
      <p className="cc-notice">这里全部是本机模拟，未连接真实银行或商户，不会产生真实付款。</p>
      <div className="cc-field">
        <label htmlFor="cc-card">选择演示卡</label>
        <select id="cc-card" className="cc-input" value={p.cardId} disabled={selectOff} onChange={e => p.onCard(e.currentTarget.value)}>
          <option value="">请选择一张演示卡</option>
          {(p.cards ?? []).map(k => <option key={k.id} value={k.id}>{k.name}</option>)}
        </select>
      </div>
      {b && c && (
        <dl className="cc-facts">
          <div><dt>卡片</dt><dd>{b.card.name}（{b.card.masked_number}）</dd></div>
          <div><dt>挂失状态</dt><dd>{lost ? '已挂失' : '正常，未挂失'}</dd></div>
          <div><dt>交易锁</dt><dd>{c.locked ? '已锁定，暂停消费' : '未锁定'}</dd></div>
          <div><dt>单笔限额</dt><dd>{yuan(c.single_limit_cents)}</dd></div>
          <div><dt>每日限额</dt><dd>{yuan(c.daily_limit_cents)}</dd></div>
          <div><dt>今日已消费</dt><dd>{yuan(c.spent_today_cents)}</dd></div>
        </dl>
      )}
      {state.pending && (
        <div className="cc-pending" role="status">
          <p>已有一笔请求：{shown ? STATUS_LABEL[shown] : '结果待核实'}。{terminal ? '请核对后再重新填写。' : '请先核对原请求，此时不能再准备新的操作。'}</p>
          <div className="cc-row">
            <button type="button" className="cc-button cc-secondary" onClick={p.onCheck} disabled={state.busy || p.blocked}>核对原请求</button>
            <button type="button" className="cc-button cc-secondary" onClick={p.onOpenOperation} disabled={state.busy || !state.receipt}>查看原操作</button>
            <button type="button" className="cc-button cc-secondary" onClick={p.onStartNew} disabled={!terminal || state.busy || p.blocked}>已核对，重新填写</button>
          </div>
        </div>
      )}
      {b && c && cred && (
        <div className="cc-form">
          <div className="cc-field">
            <label htmlFor="cc-mode">这次要做什么</label>
            <select id="cc-mode" className="cc-input" value={p.mode} disabled={formOff} onChange={e => p.onMode(e.currentTarget.value as CardMode)}>
              <option value="">请选择</option>
              {MODES.map(m => <option key={m} value={m}>{MODE_LABEL[m]}</option>)}
            </select>
          </div>
          {p.mode === 'spend' && (<>
            <div className="cc-field">
              <label htmlFor="cc-merchant">演示商户</label>
              <select id="cc-merchant" className="cc-input" value={p.merchantId} disabled={formOff} onChange={e => p.onMerchant(e.currentTarget.value)}>
                <option value="">请选择演示商户</option>
                {b.merchants.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
              </select>
            </div>
            {amountField('金额（元）')}
            {(lost || c.locked) && <p className="cc-hint">卡片已挂失或交易已锁定，暂不能模拟消费。</p>}
          </>)}
          {(p.mode === 'lock' || p.mode === 'unlock') && (
            <p className="cc-text">{p.mode === 'lock' ? (c.locked ? '交易锁已是锁定状态。' : '锁定后这张卡暂停模拟消费，挂失状态不受影响。') : (c.locked ? '解锁后未挂失的卡可恢复模拟消费。解锁不解除挂失，挂失卡仍不能消费。' : '交易锁当前未锁定。')}</p>
          )}
          {p.mode === 'single' && amountField('新限额（元）')}
          {p.mode === 'daily' && amountField('新限额（元）')}
          {p.mode === 'credential' && (<>
            <p className="cc-text">演示口令为 8 到 64 位英文字母、数字或符号，仅用于本机演示，请不要输入真实银行卡密码。</p>
            {cred.configured && (
              <div className="cc-field">
                <label htmlFor="cc-current">当前演示口令</label>
                <input id="cc-current" className="cc-input" type="password" autoComplete="off" value={p.currentSecret} disabled={formOff} onChange={e => p.onCurrentSecret(e.currentTarget.value)} />
              </div>
            )}
            <div className="cc-field">
              <label htmlFor="cc-new">新演示口令</label>
              <input id="cc-new" className="cc-input" type="password" autoComplete="off" value={p.newSecret} disabled={formOff} onChange={e => p.onNewSecret(e.currentTarget.value)} />
            </div>
          </>)}
          {p.mode && (<>
            <p className="cc-hint">{p.mode === 'spend' || p.mode === 'credential' ? '这一步只是准备请求，不会立即执行。确认后按需完成演示验证。' : '这一步只是准备请求，不会立即执行。之后需在原操作中确认，交易设置每次都要完成验证。'}</p>
            <button type="button" className="cc-button cc-primary" onClick={p.onPrepare} disabled={!canPrepare}>{prepLabel}</button>
          </>)}
        </div>
      )}
      {!b && p.cardId && <p className="cc-text">正在等待卡片资料。</p>}
      <button type="button" className="cc-button cc-secondary cc-refresh" onClick={p.onRefresh} disabled={state.busy || p.blocked}>刷新卡片资料</button>
    </section>
  )
}
