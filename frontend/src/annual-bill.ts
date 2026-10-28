// 年度账单：纯数据校验与加载控制（无 React / 无依赖）。视图由独立任务实现。

export type FlagType = 'large_expense' | 'possible_duplicate'
export type AnnualTransaction = { id: string; kind: string; amount_cents: number; category: string; merchant: string; occurred_at: string }
export type AnnualCategory = { name: string; amount_cents: number }
export type AnnualMonth = { period: string; expense_cents: number; refund_cents: number; income_cents: number; net_expense_cents: number; transaction_count: number }
export type AnnualFlag = { type: FlagType; message: string; evidence_ids: string[] }
export type AnnualBill = {
  year: string
  period: string
  simulated: true
  expense_cents: number
  refund_cents: number
  income_cents: number
  net_expense_cents: number
  previous_net_expense_cents: number
  categories: AnnualCategory[]
  transactions: AnnualTransaction[]
  evidence_ids: string[]
  flags: AnnualFlag[]
  months: AnnualMonth[]
}
export type AnnualBillState = { year: string | null; report: AnnualBill | null; loading: boolean; error: string | null }
export type AnnualBillReader = (path: string) => Promise<unknown>

export const INVALID_YEAR_MESSAGE = '年份无效，请选择1900至2100之间的年份'
export const LOAD_FAILED_MESSAGE = '年度账单暂时无法加载，请稍后重试'

export function isValidYear(year: unknown): year is string {
  if (typeof year !== 'string' || !/^[0-9]{4}$/.test(year)) return false
  const n = Number(year)
  return n >= 1900 && n <= 2100
}

const fail = (): never => { throw new Error('invalid') }
const check = (ok: boolean): void => { if (!ok) fail() }
const rec = (v: unknown): Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : fail()
const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : fail())
const str = (v: unknown): string => (typeof v === 'string' && v.length > 0 ? v : fail())
const text = (v: unknown): string => (typeof v === 'string' ? v : fail())
const cents = (v: unknown, signed = false): number =>
  typeof v === 'number' && Number.isSafeInteger(v) && (signed || v >= 0) ? v : fail()
const safeSum = (values: number[]): number =>
  values.reduce((acc, n) => { const s = acc + n; check(Number.isSafeInteger(s)); return s }, 0)
const DATE_TIME = /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}/
function validCalendarTime(value: string): boolean {
  if (!DATE_TIME.test(value) || Number.isNaN(Date.parse(value))) return false
  const month = Number(value.slice(5,7)), day = Number(value.slice(8,10))
  return month >= 1 && month <= 12 && day >= 1 && day <= new Date(Date.UTC(Number(value.slice(0,4)),month,0)).getUTCDate()
}

function parseStrict(value: unknown, year: string): AnnualBill {
  check(isValidYear(year))
  const r = rec(value)
  check(r.year === year && r.period === year && r.simulated === true)

  const transactions = list(r.transactions).map((item): AnnualTransaction => {
    const t = rec(item)
    const tx = { id: str(t.id), kind: str(t.kind), amount_cents: cents(t.amount_cents), category: text(t.category), merchant: text(t.merchant), occurred_at: str(t.occurred_at) }
    check(/^[A-Z_]+$/.test(tx.kind) && tx.occurred_at.startsWith(year + '-') && validCalendarTime(tx.occurred_at))
    return tx
  })
  const ids = new Set(transactions.map((t) => t.id))
  check(ids.size === transactions.length)
  const refs = (v: unknown): string[] => {
    const result = list(v).map((id) => { const s = str(id); check(ids.has(s)); return s })
    check(new Set(result).size === result.length)
    return result
  }
  const evidence = refs(r.evidence_ids)
  check(evidence.length === transactions.length)
  const kindTotal = (kind: string): number => safeSum(transactions.filter((t) => t.kind === kind).map((t) => t.amount_cents))

  const expense = cents(r.expense_cents)
  const refund = cents(r.refund_cents)
  const income = cents(r.income_cents)
  const net = cents(r.net_expense_cents, true)
  const previous = cents(r.previous_net_expense_cents, true)
  check(net === expense - refund && expense === kindTotal('EXPENSE') && refund === kindTotal('REFUND') && income === kindTotal('INCOME'))

  const byCategory = new Map<string, number>()
  for (const t of transactions) {
    if (t.kind === 'EXPENSE') byCategory.set(t.category, safeSum([byCategory.get(t.category) ?? 0, t.amount_cents]))
  }
  const categories = list(r.categories).map((item): AnnualCategory => {
    const c = rec(item)
    const cat = { name: text(c.name), amount_cents: cents(c.amount_cents) }
    check(byCategory.get(cat.name) === cat.amount_cents)
    return cat
  })
  check(new Set(categories.map((c) => c.name)).size === categories.length && categories.length === byCategory.size)

  const months = list(r.months).map((item, i): AnnualMonth => {
    const m = rec(item)
    const month = { period: str(m.period), expense_cents: cents(m.expense_cents), refund_cents: cents(m.refund_cents), income_cents: cents(m.income_cents), net_expense_cents: cents(m.net_expense_cents, true), transaction_count: cents(m.transaction_count) }
    check(month.period === year + '-' + String(i + 1).padStart(2, '0') && month.net_expense_cents === month.expense_cents - month.refund_cents)
    const rows = transactions.filter(t => t.occurred_at.slice(0,7) === month.period)
    check(month.transaction_count === rows.length)
    for (const [kind,field] of [['EXPENSE','expense_cents'],['REFUND','refund_cents'],['INCOME','income_cents']] as const) {
      check(month[field] === safeSum(rows.filter(t => t.kind === kind).map(t => t.amount_cents)))
    }
    return month
  })
  check(months.length === 12)
  const monthTotal = (key: Exclude<keyof AnnualMonth, 'period'>): number => safeSum(months.map((m) => m[key]))
  check(monthTotal('expense_cents') === expense && monthTotal('refund_cents') === refund && monthTotal('income_cents') === income && monthTotal('net_expense_cents') === net && monthTotal('transaction_count') === transactions.length)

  const flags = list(r.flags).map((item): AnnualFlag => {
    const f = rec(item)
    check(f.type === 'large_expense' || f.type === 'possible_duplicate')
    const evidence = refs(f.evidence_ids)
    check(evidence.length > 0)
    return { type: f.type as FlagType, message: str(f.message), evidence_ids: evidence }
  })

  return { year, period: year, simulated: true, expense_cents: expense, refund_cents: refund, income_cents: income, net_expense_cents: net, previous_net_expense_cents: previous, categories, transactions, evidence_ids: evidence, flags, months }
}

/** 严格校验并白名单重建年度账单；任何不一致返回 null。 */
export function parseAnnualBill(value: unknown, expectedYear: string): AnnualBill | null {
  try {
    return parseStrict(value, expectedYear)
  } catch {
    return null
  }
}

/** 年度账单加载控制：切换即清旧数据，generation 丢弃迟到响应，dispose 后静默。 */
export class AnnualBillController {
  state: AnnualBillState = { year: null, report: null, loading: false, error: null }
  readonly #read: AnnualBillReader
  readonly #onChange: (state: AnnualBillState) => void
  #generation = 0
  #disposed = false

  constructor(read: AnnualBillReader, onChange: (state: AnnualBillState) => void) {
    this.#read = read
    this.#onChange = onChange
  }

  async load(year: string): Promise<void> {
    if (this.#disposed) return
    const generation = ++this.#generation
    if (!isValidYear(year)) {
      this.#set({ year, report: null, loading: false, error: INVALID_YEAR_MESSAGE })
      return
    }
    this.#set({ year, report: null, loading: true, error: null })
    let report: AnnualBill | null = null
    try {
      report = parseAnnualBill(await this.#read('/bill/annual?year=' + encodeURIComponent(year)), year)
    } catch {
      report = null // 不暴露私有诊断文本
    }
    if (this.#disposed || generation !== this.#generation) return
    this.#set(report ? { year, report, loading: false, error: null } : { year, report: null, loading: false, error: LOAD_FAILED_MESSAGE })
  }

  dispose(): void {
    this.#disposed = true
    this.#generation++
  }

  #set(next: AnnualBillState): void {
    this.state = next
    this.#onChange(next)
  }
}
