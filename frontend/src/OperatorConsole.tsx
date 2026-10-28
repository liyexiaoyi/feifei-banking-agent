// Standalone /operator console. Uses only the operator client; never the bank session API.
import { useEffect, useMemo, useRef, useState } from 'react'
import { createOperatorClient, OperatorClientError } from './operator-client.ts'
import type { OperatorCase } from './operator-client.ts'
import { OperatorPanel } from './OperatorPanels.tsx'
import { deployment } from './deployment'
import './operator-cases.css'

const UNCONFIRMED = '结果未确认，请先刷新核对。'

function status(e: unknown): number {
  return e instanceof OperatorClientError ? e.status : 0
}

export default function OperatorConsole() {
  const client = useMemo(() => createOperatorClient(deployment), [])
  const [session, setSession] = useState(false)
  const [cases, setCases] = useState<OperatorCase[]>([])
  const [selected, setSelected] = useState<OperatorCase | null>(null)
  const [reply, setReply] = useState('')
  const [credential, setCredential] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const busyRef = useRef(false)
  const gen = useRef(0)
  const alive = useRef(true)

  // Drop every piece of private state and invalidate in-flight reads.
  function clearPrivate() {
    gen.current++
    setSession(false); setCases([]); setSelected(null); setReply(''); setCredential(''); setLoading(false)
  }

  // Map failures to user-facing state. Returns true when the session ended.
  function fail(e: unknown, unconfirmed: boolean): boolean {
    const s = status(e)
    if (s === 401) { clearPrivate(); setError('坐席登录已失效，请重新登录。'); return true }
    if (s === 404) { setSelected(null); setReply(''); setError('案件已撤回或不可用，请刷新列表。') }
    else if (s === 503) setError('演示坐席未启用：当前部署未开启本机演示坐席功能。')
    else if (s === 429) setError('请求过于频繁，请一分钟后手动重试。')
    else setError(e instanceof OperatorClientError ? e.message : '数据异常，请刷新后重试。')
    if (unconfirmed) setNotice(UNCONFIRMED)
    return false
  }

  // GET-only refresh; clears old facts first and re-reads the selected case from the server.
  async function refresh(selectId?: string) {
    const g = ++gen.current
    setLoading(true); setError(''); setCases([]); setSelected(null)
    try {
      const list = await client.list()
      if (!alive.current || g !== gen.current) return
      setCases(list)
      if (selectId) {
        const c = await client.get(selectId)
        if (!alive.current || g !== gen.current) return
        setSelected(c)
      }
    } catch (e) {
      if (alive.current && g === gen.current) { setCases([]); setSelected(null); fail(e, false) }
    } finally {
      if (alive.current && g === gen.current) setLoading(false)
    }
  }

  // GET-only select; never trusts the stale list entry.
  async function select(c: OperatorCase) {
    if (busyRef.current) return
    const g = ++gen.current
    setLoading(true); setError(''); setSelected(null); setReply('')
    try {
      const fresh = await client.get(c.id)
      if (alive.current && g === gen.current) setSelected(fresh)
    } catch (e) {
      if (alive.current && g === gen.current) fail(e, false)
    } finally {
      if (alive.current && g === gen.current) setLoading(false)
    }
  }

  // Run one guarded action at a time; the sync ref blocks double clicks.
  async function guarded(fn: () => Promise<void>) {
    if (busyRef.current) return
    busyRef.current = true; setBusy(true)
    try { await fn() } finally {
      busyRef.current = false
      if (alive.current) setBusy(false)
    }
  }

  useEffect(() => {
    alive.current = true
    const g = gen.current
    client.initialize().then(ok => {
      if (!alive.current || g !== gen.current) return
      setLoading(false)
      if (ok) { setSession(true); void refresh() }
    }).catch(e => {
      if (alive.current && g === gen.current) { setLoading(false); fail(e, false) }
    })
    return () => { alive.current = false; gen.current++ }
  }, [client])

  const onLogin = () => guarded(async () => {
    const cred = credential
    setCredential('') // memory only; cleared before awaiting on success and failure
    setError(''); setNotice('')
    try {
      await client.login(cred)
      if (!alive.current) return
      setSession(true)
      await refresh()
    } catch (e) {
      if (alive.current) fail(e, false)
    }
  })

  const onLogout = () => guarded(async () => {
    setNotice('')
    try { await client.logout() } catch {
      if (alive.current) setNotice('退出结果未确认，已清除本页信息；如需确认请重新打开页面。')
    } finally {
      if (alive.current) { clearPrivate(); setError('') }
    }
  })

  // Shared once-only POST flow: fresh key and current version, no retry on failure.
  const act = (kind: 'claim' | 'reply') => guarded(async () => {
    const s = selected
    if (!s || loading || error) return
    if (kind === 'claim' && s.status !== 'QUEUED') return
    const text = reply
    if (kind === 'reply' && (s.status !== 'IN_REVIEW' || text.trim() === '' || text.length > 500)) return
    const key = crypto.randomUUID()
    setError(''); setNotice('')
    try {
      if (kind === 'claim') await client.claim(s.id, s.version, key)
      else await client.reply(s.id, s.version, text, key)
      if (!alive.current) return
      setReply('')
      await refresh(s.id)
    } catch (e) {
      if (alive.current) fail(e, true)
    }
  })

  return (
    <main className="oc-page">
      <header className="oc-header"><a href="/">返回银行演示</a></header>
      <OperatorPanel
        session={session} cases={cases} selected={selected} reply={reply} credential={credential}
        error={error} notice={notice} loading={loading} busy={busy}
        onCredential={setCredential} onReply={setReply} onLogin={onLogin} onLogout={onLogout}
        onSelect={c => void select(c)} onRefresh={() => void refresh()}
        onClaim={() => void act('claim')} onSubmit={() => void act('reply')}
      />
    </main>
  )
}
