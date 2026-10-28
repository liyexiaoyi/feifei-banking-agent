import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { api } from './api'
import { createChannelClient } from './channel-client'
import type { ChannelMessage, ChannelStatus } from './channel-client'
import './channel-inbox.css'

interface Props { blocked: boolean; onDraft: (text: string) => void }

type SessionUser = 'demo' | 'guest'

const LOAD_ERROR = '暂时无法读取渠道消息，请点击“刷新渠道消息”重试。'
const READ_ERROR = '暂时无法核对这条消息，列表已清除，请点击“刷新渠道消息”重新读取。'
const BLOCKED_NOTICE = '当前有其他操作待处理，暂时不能带到聊天。'
const DRAFT_NOTICE = '已放入聊天输入框，尚未发送。请先核对内容，再自己决定是否发送。'

const dateFormat = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', hour12: false,
})

function formatDate(iso: string): string {
  const time = Date.parse(iso)
  return Number.isFinite(time) ? `${dateFormat.format(time)}（北京时间）` : '时间未知'
}

// Reads the current server session; only the built-in demo/guest users are accepted.
async function readSessionUser(): Promise<SessionUser> {
  const session = await api<{ user?: unknown }>('/session')
  const user = session?.user
  if (user !== 'demo' && user !== 'guest') throw new Error('unsupported session')
  return user
}

export default function ChannelInboxSection({ blocked, onDraft }: Props) {
  const client = useMemo(() => createChannelClient(api), [])
  const headingId = useId()
  const alive = useRef(false), epoch = useRef(0), busyRef = useRef(false)
  const ownerRef = useRef<SessionUser | null>(null)
  const blockedRef = useRef(blocked), onDraftRef = useRef(onDraft)
  const [status, setStatus] = useState<ChannelStatus | null>(null)
  const [items, setItems] = useState<ChannelMessage[]>([])
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)

  useLayoutEffect(() => {
    blockedRef.current = blocked
    onDraftRef.current = onDraft
  }, [blocked, onDraft])

  const clearPrivate = useCallback(() => {
    ownerRef.current = null
    setItems([])
    setStatus(null)
  }, [])

  const load = useCallback(async () => {
    if (!alive.current || busyRef.current) return
    busyRef.current = true
    const generation = ++epoch.current
    const current = () => alive.current && generation === epoch.current
    ownerRef.current = null
    setItems([]); setStatus(null); setError(''); setNotice(''); setBusy(true)
    try {
      const before = await readSessionUser()
      if (!current()) return
      const [nextStatus, inbox] = await Promise.all([client.status(), client.list()])
      if (!current()) return
      const after = await readSessionUser()
      if (!current()) return
      if (after !== before) throw new Error('session changed')
      ownerRef.current = before
      setStatus(nextStatus)
      setItems(nextStatus.enabled ? inbox.items : [])
    } catch {
      if (current()) { clearPrivate(); setError(LOAD_ERROR) }
    } finally {
      if (current()) { busyRef.current = false; setBusy(false) }
    }
  }, [client, clearPrivate])

  useEffect(() => {
    alive.current = true
    busyRef.current = false
    void load() // Read-only: never chats, prepares, confirms or verifies.
    return () => {
      alive.current = false
      epoch.current++
      busyRef.current = false
      ownerRef.current = null
    }
  }, [load])

  const takeToChat = useCallback(async (id: string) => {
    if (!alive.current || busyRef.current || blockedRef.current) return
    const owner = ownerRef.current
    if (!owner) return
    busyRef.current = true
    const generation = ++epoch.current
    const current = () => alive.current && generation === epoch.current
    setNotice(''); setBusy(true)
    try {
      const before = await readSessionUser()
      if (!current()) return
      if (before !== owner || ownerRef.current !== owner) throw new Error('session changed')
      if (blockedRef.current) { setNotice(BLOCKED_NOTICE); return }
      const message = await client.get(id)
      if (!current()) return
      const after = await readSessionUser()
      if (!current()) return
      if (after !== owner || ownerRef.current !== owner) throw new Error('session changed')
      if (message.id !== id || message.untrusted_text !== true || message.effect_authorized !== false) {
        throw new Error('unexpected message')
      }
      if (blockedRef.current) { setNotice(BLOCKED_NOTICE); return }
      onDraftRef.current(message.text) // Draft only; sending stays a separate user click.
      setNotice(DRAFT_NOTICE)
    } catch {
      if (current()) { clearPrivate(); setError(READ_ERROR) }
    } finally {
      if (current()) { busyRef.current = false; setBusy(false) }
    }
  }, [client, clearPrivate])

  const enabled = status?.enabled === true
  const actionsDisabled = busy || blocked || error !== '' || !enabled
  let disabledReason = ''
  if (busy) disabledReason = '正在读取，请稍候。'
  else if (error) disabledReason = '读取失败，暂不能带到聊天。'
  else if (blocked) disabledReason = BLOCKED_NOTICE

  return (
    <section className="channel-inbox" aria-labelledby={headingId} aria-busy={busy}>
      <h2 id={headingId}>渠道消息</h2>
      <div className="channel-inbox__notice">
        <p className="channel-inbox__badge">外部APP、IM尚未连接</p>
        <p className="channel-inbox__badge">{enabled ? '本机演示已启用' : '本机演示未启用'}</p>
        <p>消息仅为待核对文字，尚未执行；不要发送密码或验证码。</p>
      </div>
      <div className="channel-inbox__toolbar">
        <button type="button" onClick={() => { void load() }} disabled={busy}>刷新渠道消息</button>
        {busy && <p role="status">正在读取…</p>}
      </div>
      {error && <p className="channel-inbox__error" role="alert">提示：{error}</p>}
      {notice && <p className="channel-inbox__status" role="status">{notice}</p>}
      {!busy && !error && enabled && items.length === 0 && <p>暂时没有渠道消息。</p>}
      {items.length > 0 && (
        <ul className="channel-inbox__list">
          {items.map(item => (
            <li key={item.id} className="channel-inbox__item">
              <p className="channel-inbox__label">{item.channel_label}（待核对，未执行）</p>
              <p className="channel-inbox__text">{item.text}</p>
              <p className="channel-inbox__meta">
                <span>收到：<time dateTime={item.received_at}>{formatDate(item.received_at)}</time></span>
                <span>到期：<time dateTime={item.expires_at}>{formatDate(item.expires_at)}</time></span>
              </p>
              <button type="button" disabled={actionsDisabled} onClick={() => { void takeToChat(item.id) }}>
                带到聊天，先核对
              </button>
              {actionsDisabled && disabledReason && <p className="channel-inbox__hint">{disabledReason}</p>}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
