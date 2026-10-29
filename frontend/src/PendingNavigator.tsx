/**
 * Pending item navigator for FF Agent (菲菲).
 * Pure render function: no hooks, no icons, no network or storage access.
 * It only locates items. Confirming or cancelling happens elsewhere.
 */

export interface PendingItem {
  id: string
  title: string
  status: string
  amount_cents?: number
}

export interface PendingNavigatorProps {
  items: readonly PendingItem[]
  blocked: boolean
  onLocate: (id: string) => void
}

interface PendingRow {
  id: string
  title: string
  state: string
  amount: string | null
}

const ID_PATTERN = /^[A-Za-z0-9_.:-]{1,100}$/
const MAX_TITLE_CHARS = 120
const MAX_AMOUNT_CENTS = 100_000_000
const FALLBACK_TITLE = '业务事项'
const UNSAFE_TITLE_CHARS = /[\u0000-\u001f\u007f-\u009f\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff]/g

function stateLabel(status: unknown): string | null {
  switch (status) {
    case 'WAITING_CONFIRMATION':
      return '等待确认'
    case 'WAITING_VERIFICATION':
      return '等待验证'
    case 'UNKNOWN':
      return '结果待核对'
    case 'EXECUTING':
      return '处理中'
    default:
      // Unsupported statuses are excluded. There is no fallback label.
      return null
  }
}

function plainTitle(title: unknown): string {
  if (typeof title !== 'string') return FALLBACK_TITLE
  const cleaned = title.replace(UNSAFE_TITLE_CHARS, ' ').replace(/\s+/g, ' ').trim()
  if (cleaned === '') return FALLBACK_TITLE
  return Array.from(cleaned).slice(0, MAX_TITLE_CHARS).join('')
}

function formatAmount(cents: unknown): string | null {
  if (typeof cents !== 'number' || !Number.isSafeInteger(cents)) return null
  if (cents <= 0 || cents > MAX_AMOUNT_CENTS) return null
  const fraction = cents % 100
  const yuan = (cents - fraction) / 100
  return `￥${yuan}.${String(fraction).padStart(2, '0')}`
}

function readField(item: object, key: string): unknown {
  try {
    return (item as Record<string, unknown>)[key]
  } catch {
    return undefined
  }
}

function normalize(items: unknown): PendingRow[] {
  if (!Array.isArray(items)) return []
  const seen = new Set<string>()
  const rows: PendingRow[] = []
  for (const raw of items) {
    if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) continue
    const id = readField(raw, 'id')
    if (typeof id !== 'string' || !ID_PATTERN.test(id) || seen.has(id)) continue
    // The first entry claims the id. Later duplicates never render, even if the first entry is dropped.
    seen.add(id)
    const state = stateLabel(readField(raw, 'status'))
    if (state === null) continue
    rows.push({
      id,
      title: plainTitle(readField(raw, 'title')),
      state,
      amount: formatAmount(readField(raw, 'amount_cents')),
    })
  }
  return rows
}

export function PendingNavigator(props: PendingNavigatorProps) {
  const input = (props ?? {}) as Partial<PendingNavigatorProps>
  const rows = normalize(input.items)
  const isBlocked = input.blocked !== false
  const onLocate = input.onLocate

  return (
    <nav className='pending-nav' aria-label='待处理事项'>
      {rows.length === 0 ? (
        <p className='pending-nav__empty'>暂无待处理事项</p>
      ) : (
        <ul className='pending-nav__list'>
          {rows.map((row) => (
            <li key={row.id} className='pending-nav__row'>
              <span className='pending-nav__title'>{row.title}</span>
              <span className='pending-nav__state'>{row.state}</span>
              {row.amount !== null ? <span className='pending-nav__amount'>{row.amount}</span> : null}
              <button
                type='button'
                className='pending-nav__button'
                disabled={isBlocked}
                aria-label={`定位 ${row.title} ${row.state}`}
                onClick={() => {
                  if (isBlocked || typeof onLocate !== 'function') return
                  onLocate(row.id)
                }}
              >
                定位
              </button>
            </li>
          ))}
        </ul>
      )}
    </nav>
  )
}

export default PendingNavigator
