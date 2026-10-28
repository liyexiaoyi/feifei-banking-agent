import type { BirthdayState } from './birthday-controller.ts'
import type { BirthdayAction, BirthdayArgs, BirthdayFlow, BirthdayNode, BirthdayItem } from './birthday-client.ts'
import './birthday.css'

export interface BirthdayPanelProps {
  state: BirthdayState; blocked: boolean; locked: boolean; stage: number
  recipient: string; eventDate: string; amount: string; accountId: string; consent: boolean
  onRecipient: (v: string) => void; onDate: (v: string) => void; onAmount: (v: string) => void
  onAccount: (v: string) => void; onConsent: (v: boolean) => void
  onNext: () => void; onBack: () => void; onStart: () => void; onSelect: (id: string) => void
  onCommand: (action: BirthdayAction, args: BirthdayArgs) => void; onOpen: (opid: string) => void
  onRefresh: () => void; onCheck: () => void; onAcknowledge: () => void
}

const NOTE = '本机模拟：未连接任何商户，不会真实付款、订货或配送。'
const NODE_STATE: Record<string, string> = { PENDING: '未开始', READY: '可进行', RUNNING: '处理中', WAITING_CONFIRMATION: '等待确认', SUCCEEDED: '已完成', FAILED: '未成功', SKIPPED: '已跳过', PAUSED: '已暂停', CANCELLED: '已取消', EXPIRED: '已过期', WAITING_VERIFICATION: '等待验证', BLOCKED: '当前步骤受阻', UNKNOWN: '待核对' }
const FLOW_STATUS: Record<string, string> = { ACTIVE: '进行中', RUNNING: '处理中', PAUSED: '已暂停', COMPLETED: '已完成（本机模拟）', CANCELLED: '已取消', FAILED: '未成功', EXPIRED: '已过期', BLOCKED: '当前步骤受阻', WAITING_VERIFICATION: '等待验证', UNKNOWN: '待核对' }

// 金额：最多两位小数的十进制字符串 -> 整数分（1..1e8），拒绝科学计数/逗号/模糊文字
function toCents(s: string): number | null {
  const m = /^(0|[1-9]\d{0,6})(?:\.(\d{1,2}))?$/.exec(s.trim())
  if (!m) return null
  const c = Number(m[1]) * 100 + Number((m[2] || '').padEnd(2, '0'))
  return c >= 1 && c <= 100000000 ? c : null
}
// 日期：纯字符串真日历校验，不依赖宿主时区
function realDate(s: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return false
  const y = Number(s.slice(0, 4)), mo = Number(s.slice(5, 7)), d = Number(s.slice(8, 10))
  if (y < 1900 || mo < 1 || mo > 12 || d < 1) return false
  const leap = (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0
  return d <= [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mo - 1]
}
function money(c: number): string {
  const y = String(Math.floor(c / 100)).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return y + '.' + String(c % 100).padStart(2, '0') + ' 元'
}
const shown = (n: BirthdayNode): string => (n.state === 'UNKNOWN' || n.observed_state === 'UNKNOWN' ? 'UNKNOWN' : n.observed_state)
const flowUnknown = (f: BirthdayFlow): boolean => f.status === 'UNKNOWN' || f.nodes.some(n => shown(n) === 'UNKNOWN')
const flowLabel = (f: BirthdayFlow): string => (flowUnknown(f) ? '待核对' : FLOW_STATUS[f.status] ?? '待核对')
const giftName = (n: BirthdayNode): string => (n.gift === 'cake' ? '蛋糕' : '鲜花')
function nodeLabel(n: BirthdayNode): string {
  if (n.type === 'birthday_budget') return '预算预留'
  if (n.type === 'birthday_choice') return '是否需要蛋糕'
  if (n.type === 'birthday_catalog') return '选择' + giftName(n)
  if (n.type === 'birthday_order') return giftName(n) + '付款（本机模拟）'
  return '安排汇总'
}
function nodeState(n: BirthdayNode): string {
  const st = shown(n)
  if (st === 'SUCCEEDED' && n.type === 'birthday_budget') return '已预留，未扣款'
  if (st === 'SUCCEEDED' && n.type === 'birthday_order') return '本机模拟已记录'
  return NODE_STATE[st] ?? '待核对'
}
// 确认勾选只能使用一次：未勾选或已禁用一律拒绝；通过后立即清除勾选，再发出异步命令
function confirmed(e: { preventDefault(): void; currentTarget: HTMLFormElement }): boolean {
  e.preventDefault()
  const c = e.currentTarget.elements.namedItem('confirm') as unknown as HTMLInputElement | null
  if (!c || c.disabled === true || c.checked !== true) return false
  c.checked = false
  return true
}
// 确认表单按安排 id/版本/状态绑定；新的暂停会重建表单，不继承之前的勾选
function confirmKey(kind: string, f: BirthdayFlow): string {
  const v = (f as unknown as { version?: unknown }).version
  return [kind, f.id, typeof v === 'number' || typeof v === 'string' ? String(v) : '', String(f.status)].join(':')
}

export default function BirthdayPanel(p: BirthdayPanelProps) {
  const s = p.state, data = s.data
  const head = (<><h2 id="bd-title">生日安排</h2><p className="birthday-note">{NOTE}</p></>)
  if (s.invalidSession || s.error || !data) {
    const msg = s.invalidSession ? '登录状态已变化，请重新登录后再查看生日安排。' : s.error || '正在读取生日安排…'
    return (<section className="birthday" aria-labelledby="bd-title">{head}
      <p className="birthday-alert" role="alert">{msg}</p>
      <div className="birthday-actions"><button type="button" disabled={s.busy || s.invalidSession} onClick={() => p.onRefresh()}>只读刷新</button></div>
    </section>)
  }
  const f = s.selected, pend = s.pending
  const bizOk = realDate(data.business_date)
  const rec = p.recipient.trim()
  const acct = data.accounts.find(a => a.id === p.accountId)
  const cents = toCents(p.amount)
  const ok = [rec.length >= 1 && rec.length <= 40 && !/[\u0000-\u001f\u007f<>]/.test(rec), bizOk && realDate(p.eventDate) && p.eventDate >= data.business_date, cents !== null, !!acct]
  const upTo = (i: number) => ok.slice(0, Math.min(i, 3) + 1).every(Boolean)
  const unknown = !!f && flowUnknown(f)
  const gate = s.busy || p.locked || p.blocked || !!pend || s.needsCheck || unknown
  const canStart = upTo(3) && p.consent && !gate
  const canWrite = !!f && !gate && !unknown
  const active = !!f && f.status === 'ACTIVE' && canWrite
  const exc = !!f && unknown && s.checked && !s.busy && !p.locked && !p.blocked && !!pend && pend.kind === 'command' && pend.flow_id === f.id
  const canCancel = !!f && ['ACTIVE', 'PAUSED', 'BLOCKED', 'UNKNOWN'].includes(f.status as string) && (canWrite || exc)
  const canAck = s.checked && !!pend && !!f && !s.busy && !p.blocked && !p.locked && !unknown && (pend.kind === 'create' || pend.flow_id === f.id)
  const items = (n: BirthdayNode): BirthdayItem[] => data.items.filter(i => i.gift === n.gift)
  const budgetOk = !!f && f.nodes.some(x => x.type === 'birthday_budget' && shown(x) === 'SUCCEEDED')
  // 选品仅在预算已观测预留、是否要蛋糕已确认、对应付款节点尚未发起时可选；允许已选后重选（报价过期重报价）
  const catalogOpen = (n: BirthdayNode): boolean => {
    if (!f || !budgetOk) return false
    const cs = shown(n)
    if (cs !== 'PENDING' && cs !== 'SUCCEEDED') return false
    const choice = f.nodes.find(x => x.idx === 1 && x.type === 'birthday_choice')
    if (!choice || shown(choice) !== 'SUCCEEDED') return false
    if (!(n.gift === 'flowers' || (n.gift === 'cake' && f.cake_choice === true))) return false
    const order = f.nodes.find(x => x.type === 'birthday_order' && x.gift === n.gift)
    return !!order && shown(order) === 'PENDING' && !order.operation_id
  }
  const masked = (id: string) => data.accounts.find(a => a.id === id)?.masked_number ?? '本人账户'
  const st = p.stage
  return (<section className="birthday" aria-labelledby="bd-title">{head}
    {(pend || s.needsCheck) && (<div className="birthday-alert" role="alert">
      <p>有一项请求的结果待核对。核对前不会发起新的准备或付款。{s.checked ? '已找到原请求，请确认后再继续。' : ''}</p>
      <div className="birthday-actions">
        <button type="button" disabled={s.busy} onClick={() => p.onRefresh()}>只读刷新</button>
        <button type="button" disabled={s.busy || !pend} onClick={() => p.onCheck()}>核对原请求</button>
        <button type="button" disabled={!canAck} onClick={() => p.onAcknowledge()}>已核对，继续此安排</button>
      </div></div>)}
    {!pend && !s.needsCheck && <div className="birthday-actions"><button type="button" disabled={s.busy || p.blocked} onClick={() => p.onRefresh()}>只读刷新</button></div>}
    {s.notice && <p className="birthday-note" role="status">{s.notice}</p>}
    <h3>我的安排</h3>
    {data.flows.length === 0 ? <p>暂无生日安排。</p> : (<ul className="birthday-list">{data.flows.map(x => (
      <li key={x.id}><button type="button" aria-pressed={f?.id === x.id} disabled={s.busy} onClick={() => p.onSelect(x.id)}>{x.recipient_label + ' · ' + x.event_date + ' · ' + flowLabel(x)}</button></li>))}</ul>)}
    {f && (<div className="birthday-flow">
      <h3>{f.recipient_label} 的生日安排</h3>
      <p className="birthday-status">状态：{flowLabel(f)}{unknown ? '（部分结果不确定，不能视为已付款或已完成）' : ''}</p>
      <dl className="birthday-dl"><dt>日期</dt><dd>{f.event_date}</dd><dt>预算</dt><dd>{money(f.budget_cents)}</dd><dt>账户</dt><dd>{masked(f.account_id)}</dd><dt>蛋糕</dt><dd>{f.cake_choice === null ? '尚未决定' : f.cake_choice ? '需要' : '不需要'}</dd></dl>
      <ol className="birthday-nodes">{f.nodes.map(n => (<li key={n.idx} className="birthday-row">
        <span>{nodeLabel(n)}：</span><strong>{nodeState(n)}</strong>
        {n.item_name && <span>（{n.item_name}{typeof n.price_cents === 'number' ? '，' + money(n.price_cents) : ''}）</span>}
        {n.operation_id && (n.type === 'birthday_budget' || n.type === 'birthday_order') && (
          <button type="button" disabled={s.busy} onClick={() => p.onOpen(n.operation_id as string)}>{n.type === 'birthday_budget' ? '查看预算预留' : '查看' + giftName(n) + '付款'}</button>)}
        {n.type === 'birthday_catalog' && catalogOpen(n) && (<div className="birthday-actions">{items(n).map(i => (
          <button type="button" key={i.item_id} disabled={!active} onClick={() => p.onCommand('items', { gift: i.gift, item_id: i.item_id })}>{'选择 ' + i.name + ' ' + money(i.price_cents)}</button>))}</div>)}
      </li>))}</ol>
      {f.cake_choice === null && f.status === 'ACTIVE' && (<div className="birthday-actions" role="group" aria-label="是否需要蛋糕">
        <button type="button" disabled={!active || !budgetOk} onClick={() => p.onCommand('cake', { cake: true })}>需要蛋糕</button>
        <button type="button" disabled={!active || !budgetOk} onClick={() => p.onCommand('cake', { cake: false })}>不要蛋糕</button></div>)}
      <div className="birthday-actions">
        <button type="button" disabled={!active} onClick={() => p.onCommand('advance', {})}>准备下一步</button>
        <button type="button" disabled={!active} onClick={() => p.onCommand('pause', {})}>暂停安排</button>
      </div>
      <p className="birthday-hint">准备下一步只生成预留或付款申请，需在操作卡中另行确认，不会自动付款。</p>
      <form key={confirmKey('resume', f)} className="birthday-confirm" onSubmit={e => { if (confirmed(e)) p.onCommand('resume', { confirmed: true }) }}>
        <label className="birthday-check"><input type="checkbox" name="confirm" required disabled={!canWrite || f.status !== 'PAUSED'} />我确认继续此安排</label>
        <button type="submit" disabled={!canWrite || f.status !== 'PAUSED'}>继续安排</button></form>
      <form key={confirmKey('cancel', f)} className="birthday-confirm" onSubmit={e => { if (confirmed(e)) p.onCommand('cancel', { confirmed: true }) }}>
        <label className="birthday-check"><input type="checkbox" name="confirm" required disabled={!canCancel} />我确认取消尚未执行的部分；已发生或待核对的部分保留核对，不会自动退款</label>
        <button type="submit" disabled={!canCancel}>取消未执行部分</button></form>
    </div>)}
    <h3>新建安排（第 {Math.min(st, 4) + 1} / 5 步）</h3>
    <div className="birthday-form">
      {st === 0 && (<label htmlFor="bd-recipient">收礼人称呼（只填称呼，不填地址或电话）<input id="bd-recipient" type="text" autoComplete="off" maxLength={40} disabled={gate} value={p.recipient} onChange={e => p.onRecipient(e.currentTarget.value)} /></label>)}
      {st === 1 && (<label htmlFor="bd-date">生日日期（不早于业务日期 {data.business_date}）<input id="bd-date" type="date" min={data.business_date} disabled={gate} value={p.eventDate} onChange={e => p.onDate(e.currentTarget.value)} /></label>)}
      {st === 2 && (<label htmlFor="bd-amount">预算（元，最多两位小数）<input id="bd-amount" type="text" inputMode="decimal" autoComplete="off" disabled={gate} value={p.amount} onChange={e => p.onAmount(e.currentTarget.value)} /></label>)}
      {st === 3 && (<label htmlFor="bd-account">付款账户（本人账户）<select id="bd-account" disabled={gate} value={p.accountId} onChange={e => p.onAccount(e.currentTarget.value)}>
        <option value="">请选择账户</option>{data.accounts.map(a => <option key={a.id} value={a.id}>{a.name + ' ' + a.masked_number}</option>)}</select></label>)}
      {st < 4 && p.stage >= 0 && !ok[st] && <p className="birthday-hint">请填写有效内容后进入下一步。</p>}
      {st >= 4 && (<div className="birthday-summary">
        <dl className="birthday-dl"><dt>收礼人</dt><dd>{rec}</dd><dt>日期</dt><dd>{p.eventDate}</dd><dt>预算</dt><dd>{cents === null ? '无效' : money(cents)}</dd><dt>账户</dt><dd>{acct ? acct.name + ' ' + acct.masked_number : '未选择有效账户'}</dd></dl>
        <label className="birthday-check"><input type="checkbox" disabled={gate} checked={p.consent} onChange={e => p.onConsent(e.currentTarget.checked)} />我已核对以上信息，确认启动本机模拟安排（不会自动付款）</label>
        <button type="button" disabled={!canStart} onClick={() => p.onStart()}>确认启动安排</button></div>)}
      <div className="birthday-actions">
        <button type="button" disabled={st <= 0 || gate} onClick={() => p.onBack()}>上一步</button>
        {st < 4 && <button type="button" disabled={!upTo(st) || gate} onClick={() => p.onNext()}>下一步</button>}
      </div>
    </div>
  </section>)
}
