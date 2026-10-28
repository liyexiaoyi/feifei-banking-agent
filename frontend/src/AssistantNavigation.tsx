import './assistant-navigation.css'

export interface AssistantNavigationProps {
  currentView: string
  pendingCount: number
  onNavigate: (view: string) => void
}

interface NavItem { readonly id: string; readonly label: string }

// Primary conversation-first entries, always visible.
const PRIMARY: readonly NavItem[] = Object.freeze([
  { id: 'home', label: '和助手聊' },
  { id: 'tasks', label: '任务进度' },
])

// Existing views kept as secondary references inside the closed disclosure.
const REFERENCES: readonly NavItem[] = Object.freeze([
  { id: 'bills', label: '账单明细' },
  { id: 'subscriptions', label: '订阅清单' },
  { id: 'cards', label: '卡片资料' },
  { id: 'wealth', label: '理财资料' },
  { id: 'aa', label: 'AA记录' },
  { id: 'scheduled', label: '定时安排' },
  { id: 'birthday', label: '生日记录' },
  { id: 'compute', label: '计算参考' },
  { id: 'channel', label: '渠道消息' },
  { id: 'audit', label: '操作记录' },
  { id: 'memory', label: '偏好与笔记' },
])

// Explicit allow-list: only these view IDs are ever passed to onNavigate.
const ALLOWED_VIEWS: readonly string[] = Object.freeze([
  'home', 'tasks', 'bills', 'subscriptions', 'cards', 'wealth', 'aa',
  'scheduled', 'birthday', 'compute', 'channel', 'audit', 'memory',
])

const NOTE = '先说你想办的事，资料随时核对。涉及金额的确认仍在操作卡中由你完成。'

// Only a real positive integer count is shown; anything else shows nothing.
function validCount(v: unknown): number {
  return typeof v === 'number' && Number.isSafeInteger(v) && v > 0 ? v : 0
}

export default function AssistantNavigation(p: AssistantNavigationProps) {
  const current = typeof p.currentView === 'string' ? p.currentView : ''
  const count = validCount(p.pendingCount)
  const go = (id: string) => {
    if (ALLOWED_VIEWS.includes(id) && typeof p.onNavigate === 'function') p.onNavigate(id)
  }
  const activeRef = REFERENCES.find(r => r.id === current)

  const renderButton = (item: NavItem, variant: 'primary' | 'reference') => {
    const active = item.id === current
    return (
      <button
        type="button"
        className={'assistant-navigation__button assistant-navigation__button--' + variant + (active ? ' is-current' : '')}
        aria-current={active ? 'page' : undefined}
        onClick={() => go(item.id)}
      >
        <span className="assistant-navigation__label">{item.label}</span>
        {item.id === 'home' && count > 0 && (
          <span className="assistant-navigation__count">{count} 项待处理</span>
        )}
      </button>
    )
  }

  return (
    <nav className="assistant-navigation" aria-label="助手导航">
      <ul className="assistant-navigation__primary">
        {PRIMARY.map(item => <li key={item.id}>{renderButton(item, 'primary')}</li>)}
      </ul>
      <p className="assistant-navigation__note">{NOTE}</p>
      <details className="assistant-navigation__more">
        <summary className="assistant-navigation__summary">
          <span>更多参考</span>
          {activeRef && <span className="assistant-navigation__summary-current">当前：{activeRef.label}</span>}
        </summary>
        <ul className="assistant-navigation__references">
          {REFERENCES.map(item => <li key={item.id}>{renderButton(item, 'reference')}</li>)}
        </ul>
      </details>
    </nav>
  )
}
