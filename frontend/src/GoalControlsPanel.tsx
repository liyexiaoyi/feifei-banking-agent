import './goal-controls.css'
import type { KeyboardEvent } from 'react'
import type { GoalControlAction, GoalControlView } from './goal-control-client'
import type { GoalControlState } from './goal-control-controller'

export interface GoalControlsPanelProps {
  current: GoalControlView | null
  state: GoalControlState
  intent: { goal: GoalControlView; action: GoalControlAction } | null
  agreed: boolean
  onRequest: (goal: GoalControlView, action: GoalControlAction) => void
  onAgree: (v: boolean) => void
  onCancel: () => void
  onConfirm: () => void
  onCheck: () => void
  ready?: boolean
}

const NOTE = '暂停不会退款，已有预留不会因为暂停而释放。恢复不会自动确认或付款，请重新核对操作卡。'
const AGREE = '我已核对，按上述方式处理目标'
const MAX_SAVED = 100
const TERMINAL = ['COMPLETED', 'CANCELLED', 'FAILED', 'EXPIRED']
const STATUS: Record<string, string> = { ACTIVE: '进行中', PAUSED: '已暂停', UNKNOWN: '待核对' }
const VERB: Record<string, string> = { pause: '暂停目标', resume: '恢复目标', switch: '切换到新需求' }
const EXPLAIN: Record<string, string> = {
  pause: '暂停后目标停止推进，可以稍后从已保存列表恢复。',
  resume: '恢复后目标从暂停处继续，所有需要确认的步骤仍要重新确认。',
  switch: '切换后当前目标会保存为暂停状态，然后开始新的需求。',
}

const isText = (v: unknown): v is string => typeof v === 'string'
function wellFormed(g: unknown): g is GoalControlView {
  if (!g || typeof g !== 'object') return false
  const x = g as GoalControlView
  return isText(x.id) && x.id !== '' && Number.isSafeInteger(x.version) && isText(x.status) && isText(x.title)
    && Array.isArray(x.summary) && Array.isArray(x.steps)
}
function stepsBlocked(g: GoalControlView): boolean {
  return g.steps.some(s => !s || typeof s !== 'object' || s.state === 'UNKNOWN' || s.state === 'RUNNING')
}
function riskChanged(g: GoalControlView): boolean {
  const r = g as unknown as Record<string, unknown>
  return r.pause_reason === 'RISK_CHANGED' || r.reason === 'RISK_CHANGED'
}
function canPause(g: GoalControlView): boolean { return g.status === 'ACTIVE' && g.paused === false }
function canResume(g: GoalControlView): boolean { return g.status === 'PAUSED' && g.paused === true && !riskChanged(g) }
function canSwitch(g: GoalControlView): boolean { return canPause(g) || (g.status === 'PAUSED' && (g.paused === true || riskChanged(g))) }

function trap(e: KeyboardEvent<HTMLDivElement>, onCancel: () => void) {
  if (e.key === 'Escape') { e.preventDefault(); onCancel(); return }
  if (e.key !== 'Tab') return
  const list = Array.from(e.currentTarget.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled])'))
  if (list.length === 0) { e.preventDefault(); return }
  const first = list[0], last = list[list.length - 1]
  const active = e.currentTarget.ownerDocument.activeElement
  if (e.shiftKey && active === first) { e.preventDefault(); last.focus() }
  else if (!e.shiftKey && active === last) { e.preventDefault(); first.focus() }
}

export default function GoalControlsPanel(p: GoalControlsPanelProps) {
  const st = p.state
  const notReady = p.ready === false
  const lock = !st || st.busy === true || st.unknown === true || notReady
  const cur = p.current
  const curOk = cur === null || wellFormed(cur)
  const curUnknown = cur !== null && (!curOk || cur.status === 'UNKNOWN' || stepsBlocked(cur as GoalControlView))
  const frozen = lock || curUnknown || p.intent !== null
  const showCheck = (st && st.unknown === true) || curUnknown || notReady || (st && isText(st.error) && st.error !== '')
  const otherOpen = cur !== null && (!curOk || !TERMINAL.includes((cur as GoalControlView).status))
  const saved = (st && Array.isArray(st.paused) ? st.paused : []).filter(wellFormed).slice(0, MAX_SAVED)

  const pauseDis = frozen || !cur || !curOk || !canPause(cur)
  const resumeCurDis = frozen || !cur || !curOk || !canResume(cur)
  const switchDis = frozen || !cur || !curOk || !canSwitch(cur)
  const req = (dis: boolean, g: GoalControlView | null, a: GoalControlAction) => () => { if (dis || !g) return; p.onRequest(g, a) }

  const it = p.intent
  const itOk = it !== null && wellFormed(it.goal) && isText(it.action) && VERB[it.action] !== undefined
  const itBlocked = !itOk || it === null || it.goal.status === 'UNKNOWN' || stepsBlocked(it.goal as GoalControlView)
  const confirmDis = !p.agreed || lock || curUnknown || itBlocked

  return (
    <section className="goal-controls" role="region" aria-labelledby="goal-controls-h">
      <h3 id="goal-controls-h">目标管理</h3>
      <p className="gc-note">{NOTE}</p>
      {st && isText(st.error) && st.error !== '' ? <p className="gc-alert" role="alert">操作没有完成，请稍后核对当前目标。</p> : null}
      {cur === null ? <p className="gc-hint">当前没有进行中的目标。</p>
        : !curOk ? <p className="gc-alert">当前目标信息不完整，暂时不能操作。</p>
        : <div className="gc-current">
            <p className="gc-title">当前目标：{cur.title}</p>
            <p className="gc-status">状态：{STATUS[cur.status] ?? '暂不支持操作'}</p>
          </div>}
      {curUnknown || (st && st.unknown) ? <p className="gc-hint">上一次请求的结果待核对，核对完成前不能进行新的操作。</p> : null}
      <div className="gc-actions">
        {cur !== null && curOk && !curUnknown ? <>
          <button type="button" disabled={pauseDis} onClick={req(pauseDis, cur, 'pause')}>暂停目标</button>
          {cur.status === 'PAUSED' ? <button type="button" disabled={resumeCurDis} onClick={req(resumeCurDis, cur, 'resume')}>恢复目标</button> : null}
          <button type="button" disabled={switchDis} onClick={req(switchDis, cur, 'switch')}>切换到新需求</button>
        </> : null}
        {showCheck ? <button type="button" disabled={st && st.busy === true} onClick={() => { if (st && st.busy === true) return; p.onCheck() }}>核对原请求</button> : null}
      </div>
      {cur !== null && curOk && riskChanged(cur) ? <p className="gc-hint">情况有变化，这个目标不能直接恢复，请切换到新需求。</p> : null}
      {saved.length > 0 ? <div className="gc-saved">
        <h4>已保存的目标</h4>
        {otherOpen ? <p className="gc-hint">请先切换当前目标，再恢复已保存的目标。</p> : null}
        <ul>
          {saved.map(g => {
            const dis = frozen || otherOpen || !canResume(g)
            return <li key={g.id}>
              <span className="gc-title">{g.title}</span>
              {riskChanged(g) ? <span className="gc-hint">（情况有变化，不能恢复）</span> : null}
              <button type="button" disabled={dis} onClick={req(dis, g, 'resume')}>恢复</button>
            </li>
          })}
        </ul>
      </div> : null}
      {it !== null ? <div className="gc-modal" role="dialog" aria-modal="true" aria-labelledby="goal-controls-q" onKeyDown={e => trap(e, p.onCancel)}>
        {itOk ? <>
          <p id="goal-controls-q" className="gc-question">确定要{VERB[it.action]}：{it.goal.title}？</p>
          <ul className="gc-summary">{it.goal.summary.filter(isText).map((s, i) => <li key={i}>{s}</li>)}</ul>
          <p>{EXPLAIN[it.action]}</p>
        </> : <p id="goal-controls-q" className="gc-question">这个请求的信息不完整，不能确认。</p>}
        <p className="gc-note">{NOTE}</p>
        <label className="gc-agree">
          <input type="checkbox" autoFocus checked={p.agreed} disabled={lock || !itOk} onChange={e => p.onAgree(e.currentTarget.checked)} />
          <span>{AGREE}</span>
        </label>
        <div className="gc-actions">
          <button type="button" disabled={confirmDis} onClick={() => { if (confirmDis) return; p.onConfirm() }}>确认</button>
          <button type="button" className="gc-secondary" onClick={() => p.onCancel()}>取消返回</button>
        </div>
      </div> : null}
    </section>
  )
}
