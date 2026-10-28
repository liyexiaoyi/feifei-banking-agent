import { useEffect, useRef, useState } from 'react'
import { api, requestKey } from './api'
import { createAAClient, type AAParticipant, type AAPreview, type AARequest } from './aa-client'
import AARequestsPanel, { type SelfChoice } from './AARequestsPanel'
import './aa-requests.css'

type SectionProps = { blocked: boolean; runMutation?: (task: () => Promise<void>) => Promise<void> }
const client = createAAClient((path, body) => api<unknown>(path, body))
const UNCERTAIN = '本次请求可能已保存，请先核对。'
const msg = (e: unknown) => e instanceof Error && e.message ? e.message : '请求未完成，请稍后重试。'

// 金额解析：纯字符串处理，不经过浮点运算
export function parseAmount(s: string): number | null {
  const m = /^(\d{1,7})(?:\.(\d{1,2}))?$/.exec(s.trim())
  if (!m) return null
  const cents = Number(m[1]) * 100 + Number((m[2] ?? '').padEnd(2, '0'))
  return Number.isSafeInteger(cents) && cents >= 1 && cents <= 100000000 ? cents : null
}

export default function AARequestsSection({ blocked, runMutation }: SectionProps) {
  const [amount, setAmount] = useState('')
  const [ids, setIds] = useState<string[]>([])
  const [selfChoice, setSelf] = useState<SelfChoice>('')
  const [consent, setConsent] = useState(false)
  const [bound, setBound] = useState<{ fp: string; epoch: number; data: AAPreview } | null>(null)
  const [participants, setParticipants] = useState<AAParticipant[]>([])
  const [requests, setRequests] = useState<AARequest[]>([])
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [hint, setHint] = useState('')
  const [notice, setNotice] = useState('')
  const [needsCheck, setNeedsCheck] = useState(false)
  const [checked, setChecked] = useState(false)
  const [created, setCreated] = useState(false)
  const alive = useRef(true), gen = useRef(0), epoch = useRef(0)
  const busyRef = useRef(false), uncertain = useRef(false)

  const cents = parseAmount(amount)
  const fp = cents !== null && selfChoice !== '' ? cents + '|' + ids.join(',') + '|' + selfChoice : null
  const preview = bound && fp !== null && bound.fp === fp && bound.epoch === epoch.current ? bound.data : null

  async function load() {
    const g = ++gen.current
    setLoading(true)
    try {
      const d = await client.load()
      if (!alive.current || g !== gen.current) return
      setParticipants(d.participants); setRequests(d.requests); setError('')
      if (uncertain.current) setChecked(true)
    } catch (e) {
      if (!alive.current || g !== gen.current) return
      setParticipants([]); setRequests([]); setBound(null); setConsent(false); setChecked(false)
      setError(msg(e))
    } finally {
      if (alive.current && g === gen.current) setLoading(false)
    }
  }

  useEffect(() => {
    alive.current = true
    void load()
    return () => { alive.current = false; gen.current++ }
  }, [])

  // 同步门闩：防止连点；mutation 时经 runMutation 执行
  async function gate(task: () => Promise<void>, mutation: boolean): Promise<boolean> {
    if (busyRef.current || blocked) return false
    busyRef.current = true; setBusy(true)
    try { await (mutation && runMutation ? runMutation(task) : task()); return true }
    finally { busyRef.current = false; if (alive.current) setBusy(false) }
  }

  function edit(apply: () => void) {
    if (uncertain.current || busyRef.current) return
    epoch.current++
    apply(); setBound(null); setConsent(false); setCreated(false); setHint('')
  }

  function onPreview() {
    if (uncertain.current || cents === null || selfChoice === '' || fp === null) { setHint('请填写有效金额（最多两位小数）并选择本人是否分摊'); return }
    const e = epoch.current, f = fp, list = [...ids], self = selfChoice === 'yes'
    void gate(async () => {
      try {
        const data = await client.preview(cents, list, self)
        if (alive.current && e === epoch.current) { setBound({ fp: f, epoch: e, data }); setHint('') }
      } catch (err) {
        if (alive.current && e === epoch.current) { setBound(null); setHint(msg(err)) }
      }
    }, false)
  }

  async function onCreate() {
    if (!consent || !preview || !bound || created || uncertain.current || cents === null) return
    const key = requestKey(), list = [...ids], self = selfChoice === 'yes'
    try {
      await gate(async () => {
        const r = await client.create(cents, list, self, key, true)
        if (!alive.current) return
        setRequests(l => [r, ...l.filter(x => x.id !== r.id)])
        epoch.current++; setBound(null); setConsent(false); setCreated(true)
        setNotice('已生成本地记录，未发送、未扣款、未核实到账。')
      }, true)
    } catch {
      if (!alive.current) return
      uncertain.current = true
      setNeedsCheck(true); setChecked(false); setConsent(false); setNotice(UNCERTAIN)
    }
  }

  async function onCancel(id: string, version: number) {
    if (uncertain.current) return
    let ran = false
    try {
      ran = await gate(async () => { await client.cancel(id, version, requestKey()) }, true)
    } catch {
      ran = true
      if (alive.current) setNotice('取消操作可能已保存，请先核对。')
    }
    if (ran && alive.current) await load()
  }

  function onRefresh() { if (!busyRef.current) void load() }

  function onFresh() {
    if (!uncertain.current || !checked || busyRef.current || error) return
    uncertain.current = false; epoch.current++
    setAmount(''); setIds([]); setSelf(''); setConsent(false); setBound(null)
    setCreated(false); setNeedsCheck(false); setChecked(false); setNotice(''); setHint('')
  }

  return <AARequestsPanel
    amount={amount} participant_ids={ids} selfChoice={selfChoice} participants={participants}
    preview={preview} requests={requests} loading={loading} busy={busy} blocked={blocked}
    error={error} notice={notice} consent={consent} needsCheck={needsCheck} checked={checked}
    created={created} hint={hint}
    onAmount={v => edit(() => setAmount(v))}
    onParticipant={(id, on) => edit(() => setIds(l => on ? (l.includes(id) ? l : [...l, id]) : l.filter(x => x !== id)))}
    onSelfChoice={c => edit(() => setSelf(c))}
    onConsent={v => { if (!uncertain.current && preview) setConsent(v) }}
    onPreview={onPreview} onCreate={() => void onCreate()} onRefresh={onRefresh}
    onCancel={(id, v) => void onCancel(id, v)} onFresh={onFresh}
  />
}
