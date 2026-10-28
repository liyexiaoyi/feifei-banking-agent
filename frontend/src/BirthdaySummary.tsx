import type { BirthdayOperation } from './birthday-operation.ts'

const BAD = '操作数据无效，请稍后核对。'

type Fields = { amount: string; account: string; date: string; gift: string | null }

// 纯字符串真日历校验，不依赖宿主时区
function realDate(s: unknown): s is string {
  if (typeof s !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(s)) return false
  const y = Number(s.slice(0, 4)), mo = Number(s.slice(5, 7)), d = Number(s.slice(8, 10))
  if (y < 1900 || mo < 1 || mo > 12 || d < 1) return false
  const leap = (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0
  return d <= [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mo - 1]
}

function money(c: number): string {
  const y = String(Math.floor(c / 100)).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return y + '.' + String(c % 100).padStart(2, '0') + '元'
}

function str(v: unknown): string | null {
  return typeof v === 'string' && v.trim() !== '' ? v : null
}

function fields(op: BirthdayOperation): Fields | null {
  const p = op.payload
  if (!p || typeof p !== 'object' || Array.isArray(p)) return null
  const c = p.amount_cents
  if (typeof c !== 'number' || !Number.isSafeInteger(c) || c < 1 || c > 100000000) return null
  if (p.simulated !== true) return null
  const account = str(p.account_name)
  const date = p.event_date
  if (!account || !realDate(date)) return null
  let gift: string | null = null
  if (op.type === 'birthday_order') {
    if (p.merchant_connected !== false) return null
    const name = str(p.item_name)
    if (!name || (p.gift !== 'flowers' && p.gift !== 'cake')) return null
    gift = (p.gift === 'cake' ? '蛋糕' : '鲜花') + ' · ' + name
  }
  return { amount: money(c), account, date, gift }
}

function valid(op: BirthdayOperation | null): op is BirthdayOperation {
  return !!op && typeof op === 'object' && typeof op.id === 'string' &&
    (op.type === 'birthday_budget_reserve' || op.type === 'birthday_order') &&
    (op.risk === 'YELLOW' || op.risk === 'RED') && typeof op.status === 'string'
}

function statusText(op: BirthdayOperation): string {
  const budget = op.type === 'birthday_budget_reserve'
  switch (op.status) {
    case 'SUCCEEDED':
      return budget ? '仅预算已预留，未扣款。' : '仅本机模拟付款已记录，未连接真实商户，不代表预订或送达。'
    case 'WAITING_CONFIRMATION':
      return budget ? '等待确认，尚未预留。' : '等待确认，模拟付款尚未进行。'
    case 'WAITING_VERIFICATION':
      return '等待验证，尚未完成，请勿重复操作。'
    case 'READY':
      return budget ? '尚未预留，请核对后处理。' : '模拟付款尚未完成，请核对后处理。'
    case 'FAILED':
      return '操作未成功，请核对账户记录。'
    case 'CANCELLED':
      return '操作已取消，请核对账户记录。'
    case 'EXPIRED':
      return '操作已过期，请核对账户记录。'
    case 'UNKNOWN':
      return '结果待核对，请勿重复操作。'
    default:
      return '结果待核对，请勿重复操作。'
  }
}

export default function BirthdaySummary({ op }: { op: BirthdayOperation | null }) {
  if (!valid(op)) return <section><p>{BAD}</p></section>
  const f = fields(op)
  if (!f) return <section><p>{BAD}</p></section>
  const budget = op.type === 'birthday_budget_reserve'
  return (
    <section>
      <h3>{budget ? '预算预留' : '生日模拟付款'}</h3>
      {f.gift ? <p>礼物：{f.gift}</p> : null}
      <p>{budget ? '金额' : '价格'}：{f.amount}</p>
      <p>账户：{f.account}</p>
      <p>日期：{f.date}</p>
      <p>{statusText(op)}</p>
    </section>
  )
}
