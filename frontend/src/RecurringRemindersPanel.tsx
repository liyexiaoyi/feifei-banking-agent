import type { Candidate, Reminder } from './recurring-reminders'
import './recurring-reminders.css'

type Props = {
  enabled: boolean
  candidates: Candidate[] | null
  reminders: Reminder[] | null
  loading: boolean
  error: string
  needsConsent: boolean
  busy: boolean
  onRefresh: () => void
  onAck: (id: string, version: number) => void
  onAuthorize: () => void
}

// 金额按本地人民币格式显示，例如 2500 分显示为 ¥25.00
const money = new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'CNY' })
const yuan = (cents: number) => money.format(cents / 100)
const day = (iso: string) => iso.slice(0, 10)

export default function RecurringRemindersPanel(p: Props) {
  const canAct = !p.busy && !p.loading
  const allowed = p.enabled && !p.needsConsent
  // 加载中或出错时不展示任何旧数据
  const ready = !p.loading && !p.error

  return (
    <section className="panel details-panel recurring-reminders-panel" aria-busy={p.loading} aria-label="周期扣费与续费提醒">
      <h2>周期扣费与续费提醒</h2>
      <button type="button" className="outline-button" disabled={!canAct || p.needsConsent} onClick={p.onRefresh}>刷新核对</button>
      <p className="recurring-note">以下内容均为模拟数据，仅供查看。</p>

      {p.loading && <p role="status">正在加载，请稍候。</p>}
      {p.error && (
        <div role="alert">
          <p>{p.error}</p>
          <button type="button" className="outline-button" disabled={!canAct} onClick={p.onRefresh}>
            重新刷新
          </button>
        </div>
      )}

      {ready && (
        <>
          <h3>可能的周期扣费</h3>
          <p className="recurring-note">根据记录推测，可能是房租、会员等周期消费，并非已确认的订阅。</p>
          {p.candidates === null ? (
            <p>周期扣费信息尚未读取。</p>
          ) : p.candidates.length === 0 ? (
            <p>暂无周期扣费记录。</p>
          ) : (
            <ul className="recurring-list">
              {p.candidates.map((c, i) => (
                <li key={c.evidence_ids.join('|') || String(i)}>
                  <b>{c.merchant}</b>
                  <span>金额：{yuan(c.amount_cents)}</span>
                  <span>出现 {c.occurrences} 次</span>
                  <span>最近一次：{day(c.last_seen)}</span>
                  <span>判断依据：{c.rule}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      <h3>本机续费提醒</h3>
      {!allowed ? (
        <div className="recurring-consent">
          <p>查看续费提醒前，需要您先确认授权。</p>
          <button type="button" className="outline-button" disabled={!canAct} onClick={p.onAuthorize}>
            确认后查看提醒
          </button>
        </div>
      ) : ready && (
        p.reminders === null ? (
          <p>续费提醒尚未读取。</p>
        ) : p.reminders.length === 0 ? (
          <p>暂无续费提醒。</p>
        ) : (
          <ul className="recurring-list">
            {p.reminders.map((r) => (
              <li key={r.id}>
                <b>{r.merchant}</b>
                <span>金额：{yuan(r.amount_cents)}</span>
                <span>扣费日：{r.charge_date}</span>
                {r.acknowledged ? (
                  <span className="recurring-seen">已看过</span>
                ) : (
                  <button type="button" className="outline-button" disabled={!canAct} onClick={() => p.onAck(r.id, r.version)}>
                    我已看过
                  </button>
                )}
              </li>
            ))}
          </ul>
        )
      )}

      <p className="recurring-note">提醒只在本机页面打开时每 60 秒检查一次，没有连接短信或商户。</p>
    </section>
  )
}
