import type { AAParticipant, AAPreview, AARequest } from './aa-client'

export type SelfChoice = '' | 'yes' | 'no'
export type Props = {
  amount: string; participant_ids: string[]; selfChoice: SelfChoice
  participants: AAParticipant[]; preview: AAPreview | null; requests: AARequest[]
  loading: boolean; busy: boolean; blocked: boolean; error: string; notice: string; consent: boolean
  needsCheck?: boolean; checked?: boolean; created?: boolean; hint?: string
  onAmount: (v: string) => void; onParticipant: (id: string, checked: boolean) => void
  onSelfChoice: (c: SelfChoice) => void; onConsent: (v: boolean) => void
  onPreview: () => void; onCreate: () => void; onRefresh: () => void
  onCancel: (id: string, version: number) => void; onFresh?: () => void
}

const LOCAL = '仅本地记录：未发送、未扣款、未核实到账。'
const yuan = (c: number) => Math.floor(c / 100) + '.' + String(c % 100).padStart(2, '0')

export default function AARequestsPanel(p: Props) {
  const lock = p.busy || p.blocked || p.loading
  const frozen = lock || p.needsCheck === true
  const refresh = <button type='button' className='aa-btn' onClick={() => p.onRefresh()} disabled={p.busy || p.loading}>刷新核对</button>
  const status = <>
    {p.notice && <p role='status' className='aa-notice'>{p.notice}</p>}
    <p className='aa-local'>{LOCAL}</p>
  </>
  if (p.error) return (
    <section className='aa-panel' aria-labelledby='aa-title'>
      <h2 id='aa-title'>AA 拆分</h2>
      <p role='alert' className='aa-error'>{p.error}</p>
      {status}{refresh}
    </section>
  )
  return (
    <section className='aa-panel' aria-labelledby='aa-title' aria-busy={p.loading || p.busy}>
      <h2 id='aa-title'>AA 拆分</h2>
      {status}
      {p.hint && <p role='alert' className='aa-error'>{p.hint}</p>}
      <label className='aa-field'>总金额（元）
        <input className='aa-input' type='text' inputMode='decimal' autoComplete='off' value={p.amount} disabled={frozen}
          onChange={e => p.onAmount(e.currentTarget.value)} />
      </label>
      <fieldset className='aa-field' disabled={frozen}>
        <legend>参与人</legend>
        {p.participants.length === 0 && <p>暂无参与人</p>}
        {p.participants.map(x => (
          <label key={x.id} className='aa-check'>
            <input type='checkbox' checked={p.participant_ids.includes(x.id)}
              onChange={e => p.onParticipant(x.id, e.currentTarget.checked)} />
            <span>{x.name} {x.masked_phone}</span>
          </label>
        ))}
      </fieldset>
      <label className='aa-field'>本人是否分摊
        <select className='aa-input' value={p.selfChoice} disabled={frozen}
          onChange={e => p.onSelfChoice(e.currentTarget.value as SelfChoice)}>
          <option value=''>请选择</option>
          <option value='yes'>本人参与分摊</option>
          <option value='no'>本人不分摊</option>
        </select>
      </label>
      <div className='aa-actions'>
        <button type='button' className='aa-btn' onClick={() => p.onPreview()}
          disabled={frozen || p.selfChoice === '' || p.participant_ids.length === 0 || p.amount === ''}>预览AA拆分</button>
        {refresh}
      </div>
      {p.preview && (
        <div className='aa-preview'>
          <h3>拆分预览（总额 {yuan(p.preview.total_cents)} 元，{p.preview.participant_count} 人）</h3>
          <ul>{p.preview.shares.map(s => (
            <li key={s.participant_id}>{s.name} {s.masked_phone}：{yuan(s.amount_cents)} 元</li>
          ))}</ul>
          <p>本人 {yuan(p.preview.self_share_cents)} 元，应收 {yuan(p.preview.receivable_cents)} 元</p>
          <p>{p.preview.notice}</p>
        </div>
      )}
      <label className='aa-check'>
        <input type='checkbox' checked={p.consent} disabled={frozen || !p.preview || p.created === true}
          onChange={e => p.onConsent(e.currentTarget.checked)} />
        <span>我确认这只是本地记录，不会通知任何人或产生资金变动</span>
      </label>
      <div className='aa-actions'>
        <button type='button' className='aa-btn aa-primary' onClick={() => p.onCreate()}
          disabled={!p.consent || !p.preview || frozen || p.created === true}>生成本地AA请求</button>
        {p.needsCheck && (
          <button type='button' className='aa-btn' onClick={() => p.onFresh?.()}
            disabled={lock || p.checked !== true}>看过记录，重新填写</button>
        )}
      </div>
      <h3>本地记录</h3>
      {p.requests.length === 0 ? <p>暂无记录</p> : (
        <ul className='aa-list'>{p.requests.map(r => (
          <li key={r.id} className='aa-item'>
            <span>{r.status === 'ACTIVE' ? '有效' : '已取消'} · 总额 {yuan(r.total_cents)} 元 · 应收 {yuan(r.receivable_cents)} 元 · {r.created_at}</span>
            <span className='aa-local'>{LOCAL}</span>
            {r.status === 'ACTIVE' && (
              <button type='button' className='aa-btn' disabled={lock || p.needsCheck === true}
                onClick={() => p.onCancel(r.id, r.version)}>取消记录</button>
            )}
          </li>
        ))}</ul>
      )}
    </section>
  )
}
