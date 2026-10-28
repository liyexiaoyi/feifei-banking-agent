// Pure presentational panel for the demo investment profile. No hooks, no API, no runtime imports.
// The controller owns state, focus (fieldset tabIndex=-1), request keys and all writes.
import type { Answers, Comparison, ProfileView, QuestionId, Questionnaire } from './investment-profile'

export interface InvestmentProfilePanelProps {
  survey: Questionnaire | null; view: ProfileView | null; comparison: Comparison | null
  loading: boolean; busy: boolean; blocked: boolean; error: string; notice: string
  active: boolean; step: number; answers: Partial<Answers>; consent: boolean; inputId: string
  onStart(): void; onCancel(): void; onAnswer(id: QuestionId, value: number | null): void
  onBack(): void; onNext(): void; onConsent(value: boolean): void; onSubmit(): void; onRefresh(): void
}

const root = { fontSize: 18, lineHeight: 1.6, maxWidth: 960 }
const btn = { minHeight: 48, minWidth: 48, fontSize: 18, padding: '8px 20px', marginRight: 12, cursor: 'pointer' }
const field = { minHeight: 48, fontSize: 18, minWidth: 320, padding: '4px 8px' }
const cell = { border: '1px solid #888', padding: 8, fontSize: 18, textAlign: 'left' as const }
const ok = (v: unknown): v is number => typeof v === 'number' && Number.isInteger(v) && v >= 0 && v <= 3
const cny = (c: number) => new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'CNY' }).format(c / 100)
const day = (t: number) => new Date(t < 1e12 ? t * 1000 : t).toLocaleDateString('zh-CN')
const parseChoice = (raw: string): number | null => {
  if (raw === '') return null
  const n = Number(raw)
  return ok(n) ? n : null
}
const eligibleText = (e: boolean | null) => e === null ? '需先完成有效的演示测评' : e ? '符合队伍演示规则' : '不符合队伍演示规则'
const DISCLAIMER = '本测评和产品均为模拟数据，规则由队伍自定义，不是官方风险测评，不构成投资建议，也不承诺任何收益。'

export default function InvestmentProfilePanel(p: InvestmentProfilePanelProps) {
  const notice = p.notice ? <p role="status">{p.notice}</p> : null
  if (p.error) return (
    <section style={root} aria-label="演示风险测评">
      {notice}
      <p role="alert">{p.error}</p>
      <button type="button" style={btn} onClick={p.onRefresh} disabled={p.busy}>刷新并重新核对</button>
    </section>
  )
  if (p.loading || !p.survey || !p.view) return (
    <section style={root} aria-label="演示风险测评" aria-busy={p.loading}>
      <p role="status">{p.loading ? '正在读取演示测评信息，请稍候。' : '暂时没有测评信息。'}</p>
      {p.loading ? null : <button type="button" style={btn} onClick={p.onRefresh}>刷新</button>}
    </section>
  )
  const survey = p.survey, view = p.view, total = survey.questions.length
  const textOf = (id: QuestionId) => survey.questions.find(q => q.id === id)

  if (p.active) {
    const step = Math.min(Math.max(Number.isInteger(p.step) ? p.step : 0, 0), total - 1)
    const q = survey.questions[step]
    const cur = p.answers[q.id]
    const last = step === total - 1
    const allDone = total === 6 && survey.questions.every(x => ok(p.answers[x.id]))
    const canSubmit = allDone && p.consent === true && !p.loading && !p.busy && !p.blocked
    return (
      <section style={root} aria-label="演示风险测评">
        {notice}
        <fieldset tabIndex={-1} style={{ border: '1px solid #888', padding: 16 }}>
          <legend>{`第 ${step + 1} 题，共 ${total} 题`}</legend>
          <label htmlFor={p.inputId} style={{ display: 'block', marginBottom: 8 }}>{q.text}</label>
          <select id={p.inputId} style={field} value={ok(cur) ? String(cur) : ''} disabled={p.busy}
            onChange={(e: { target: { value: string } }) => p.onAnswer(q.id, parseChoice(e.target.value))}>
            <option value="">请选择</option>
            {q.choices.map(c => <option key={c.index} value={String(c.index)}>{c.label}</option>)}
          </select>
        </fieldset>
        {last ? (
          <p>
            <label style={{ display: 'inline-flex', alignItems: 'center', minHeight: 48, gap: 8 }}>
              <input type="checkbox" style={{ width: 24, height: 24 }} checked={p.consent === true} disabled={p.busy}
                onChange={(e: { target: { checked: boolean } }) => p.onConsent(e.target.checked === true)} />
              我已确认以上答案，并知道这只是队伍自定义的模拟测评
            </label>
          </p>
        ) : null}
        <p>
          <button type="button" style={btn} onClick={p.onBack} disabled={step === 0 || p.busy}>上一题</button>
          {last
            ? <button type="button" style={btn} onClick={p.onSubmit} disabled={!canSubmit}>提交演示测评</button>
            : <button type="button" style={btn} onClick={p.onNext} disabled={!ok(cur) || p.busy}>下一题</button>}
          <button type="button" style={btn} onClick={p.onCancel} disabled={p.busy}>取消</button>
        </p>
        <p>{DISCLAIMER}</p>
      </section>
    )
  }

  const prof = view.profile
  const caps = prof ? prof.caps.flatMap(c => {
    const reason = typeof c === 'string' ? c : c && typeof c === 'object' && 'reason' in c ? c.reason : null
    return typeof reason === 'string' && reason.trim() ? [reason] : []
  }) : []
  const startDisabled = p.busy || p.blocked
  return (
    <section style={root} aria-label="演示风险测评">
      {notice}
      <h2 style={{ fontSize: 24 }}>演示风险测评</h2>
      {view.status === 'NONE' ? (
        <div>
          <p>您还没有完成演示测评。新的演示申购需要先完成测评；赎回不受影响。</p>
          <button type="button" style={btn} onClick={p.onStart} disabled={startDisabled}>开始演示测评</button>
        </div>
      ) : view.status === 'EXPIRED' ? (
        <div>
          <p>您的演示测评已过期，请更新后再进行新的演示申购；赎回不受影响。</p>
          <button type="button" style={btn} onClick={p.onStart} disabled={startDisabled}>更新演示测评</button>
        </div>
      ) : prof ? (
        <div>
          <p>{`当前演示风险等级：${prof.level} 级（共 5 级），总分 ${prof.score} 分，有效期至 ${day(prof.expires_at)}。`}</p>
          <p>各题得分说明：</p>
          <ul>
            {prof.contributions.map((c, i) => {
              const q = textOf(c.id)
              const label = q?.choices[c.index]?.label
              return <li key={i}>{`${q ? q.text : `第 ${i + 1} 题`}：${label ?? '已作答'}，得 ${c.points} 分`}</li>
            })}
          </ul>
          {caps.length ? <div><p>等级调整说明：</p><ul>{caps.map((c, i) => <li key={i}>{c}</li>)}</ul></div> : null}
          <button type="button" style={btn} onClick={p.onStart} disabled={startDisabled}>重新进行演示测评</button>
        </div>
      ) : null}
      {p.comparison ? (
        <div>
          <h3 style={{ fontSize: 22 }}>演示产品比较（只读）</h3>
          <table style={{ borderCollapse: 'collapse' }}>
            <thead><tr>{['产品', '风险等级', '起购金额', '示意持有天数', '是否符合', '说明'].map(h => <th key={h} scope="col" style={cell}>{h}</th>)}</tr></thead>
            <tbody>
              {p.comparison.products.map(x => (
                <tr key={x.id}>
                  <td style={cell}>{x.name}</td>
                  <td style={cell}>{`${x.risk_level} 级`}</td>
                  <td style={cell}>{cny(x.minimum_cents)}</td>
                  <td style={cell}>{`${x.recommended_holding_days} 天（仅为示意，不是赎回锁定期）`}</td>
                  <td style={cell}>{eligibleText(x.eligible)}</td>
                  <td style={cell}>{x.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p>{p.comparison.disclaimer}</p>
        </div>
      ) : null}
      <p>{survey.disclaimer}</p>
      <p>{DISCLAIMER}</p>
      <button type="button" style={btn} onClick={p.onRefresh} disabled={p.busy}>刷新</button>
    </section>
  )
}
