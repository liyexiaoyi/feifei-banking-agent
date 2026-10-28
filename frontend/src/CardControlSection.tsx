// Container for simulated card settings/spend. Secrets live only in controlled state, never stored or logged.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api'
import { createCardControlClient } from './card-control-client'
import type { CardInfo, CardIntent } from './card-control-client'
import { createCardControlController, parseCardAmount } from './card-control-controller'
import type { CardControlState, CardMode } from './card-control-controller'
import CardControlPanel from './CardControlPanel'
import './CardControlPanel.css'

type Controller = ReturnType<typeof createCardControlController>
type User = 'demo' | 'guest'
export interface CardControlSectionProps {
  cards: CardInfo[] | null; blocked: boolean; locked: boolean
  runMutation(task: () => Promise<void>): Promise<void>
  onPrepared: () => Promise<void>; onStateRefresh?: () => Promise<void>
}

const EMPTY: CardControlState = { bundle: null, receipt: null, pending: null, busy: false, needsCheck: false, error: '' }
const SESSION_ERR = '登录状态已变化或无法确认，请刷新页面后重新选择卡片'

async function readUser(): Promise<User> {
  const s = await api('/session') as { user?: unknown } | null
  const u = s && typeof s === 'object' ? s.user : undefined
  if (u !== 'demo' && u !== 'guest') throw new Error('session')
  return u
}

export function CardControlSection(props: CardControlSectionProps) {
  const client = useMemo(() => createCardControlClient(api), [])
  const [cardId, setCardId] = useState('')
  const [mode, setMode] = useState<CardMode>('')
  const [amount, setAmount] = useState('')
  const [merchantId, setMerchantId] = useState('')
  const [newSecret, setNewSecret] = useState('')
  const [currentSecret, setCurrentSecret] = useState('')
  const [state, setState] = useState<CardControlState>(EMPTY)
  const propsRef = useRef(props); propsRef.current = props
  const genRef = useRef(0)
  const ctrlRef = useRef<Controller | null>(null)
  const userRef = useRef<User | null>(null)
  const stateRef = useRef(state); stateRef.current = state
  // Session-invalid latch: once set, this instance never renders card data/inputs again.
  const latchRef = useRef(false), refreshAskedRef = useRef(false), aliveRef = useRef(true)
  const [latched, setLatched] = useState(false)
  useEffect(() => { aliveRef.current = true; return () => { aliveRef.current = false } }, [])

  const clearSecrets = () => { setNewSecret(''); setCurrentSecret('') }
  const resetFields = () => { setMode(''); setAmount(''); setMerchantId(''); clearSecrets() }
  const gated = () => latchRef.current || stateRef.current.busy || propsRef.current.blocked || propsRef.current.locked

  // Session mismatch/error: drop controller and private state; marker stays in storage.
  const invalidate = useCallback((g: number) => {
    if (genRef.current !== g || latchRef.current) return
    latchRef.current = true // set before any clearing so the cardId effect cannot erase it
    genRef.current++
    ctrlRef.current?.dispose(); ctrlRef.current = null; userRef.current = null
    resetFields(); setCardId(''); setState({ ...EMPTY, error: SESSION_ERR }); setLatched(true)
    // Read-only parent sync, at most once; no retry, no marker change.
    if (!refreshAskedRef.current && aliveRef.current) {
      refreshAskedRef.current = true
      void propsRef.current.onStateRefresh?.().catch(() => undefined)
    }
  }, [])

  const sessionOk = useCallback(async (g: number): Promise<boolean> => {
    try {
      const u = await readUser()
      if (genRef.current !== g) return false
      if (u !== userRef.current) { invalidate(g); return false }
      return true
    } catch { invalidate(g); return false }
  }, [invalidate])

  useEffect(() => {
    const g = ++genRef.current
    clearSecrets()
    if (latchRef.current) return () => { genRef.current++ }
    setState(EMPTY)
    if (!cardId) return () => { genRef.current++ }
    let ctrl: Controller | null = null
    void (async () => {
      let user: User
      try { user = await readUser() } catch { if (genRef.current === g) invalidate(g); return }
      if (genRef.current !== g) return
      userRef.current = user
      ctrl = createCardControlController({
        client, storage: window.sessionStorage, user, cardId,
        onChange: s => { if (genRef.current === g) { setState(s); if (s.error) clearSecrets() } },
        runMutation: task => propsRef.current.runMutation(async () => {
          if (propsRef.current.blocked || propsRef.current.locked) throw new Error('blocked')
          if (!(await sessionOk(g))) throw new Error('session')
          await task()
        }),
      })
      ctrlRef.current = ctrl
      setState(ctrl.getState())
      await ctrl.load()
    })()
    return () => { genRef.current++; ctrl?.dispose(); if (ctrlRef.current === ctrl) ctrlRef.current = null }
  }, [cardId, client, invalidate, sessionOk])

  const withCtrl = async (fn: (c: Controller) => Promise<unknown>) => {
    const c = ctrlRef.current, g = genRef.current
    if (!c || latchRef.current || stateRef.current.busy || propsRef.current.blocked) return
    if (!(await sessionOk(g)) || ctrlRef.current !== c) return
    await fn(c)
  }

  const buildIntent = (ns: string, cs: string): CardIntent | null => {
    const b = stateRef.current.bundle
    if (!b) return null
    if (mode === 'spend') { const v = parseCardAmount(amount); return v === null || !merchantId ? null : { kind: 'spend', merchant_id: merchantId, amount_cents: v } }
    if (mode === 'lock') return { kind: 'control', action: 'lock', value: true }
    if (mode === 'unlock') return { kind: 'control', action: 'unlock', value: false }
    if (mode === 'single' || mode === 'daily') {
      const v = parseCardAmount(amount)
      return v === null ? null : { kind: 'control', action: mode === 'single' ? 'set_single_limit' : 'set_daily_limit', value: v }
    }
    if (mode === 'credential') return b.credential.configured ? { kind: 'credential', new_secret: ns, current_secret: cs } : { kind: 'credential', new_secret: ns }
    return null
  }

  const onPrepare = async () => {
    const c = ctrlRef.current, g = genRef.current
    if (!c || gated()) { clearSecrets(); return }
    const intent = buildIntent(newSecret, currentSecret)
    clearSecrets()
    if (!intent) return
    const op = await c.prepare(intent).catch(() => null)
    if (genRef.current !== g || ctrlRef.current !== c) return
    if (op) { resetFields(); await propsRef.current.onPrepared() }
    else if (!c.getState().needsCheck) await propsRef.current.onStateRefresh?.().catch(() => undefined)
  }

  const local = (fn: () => void) => { if (!latchRef.current && !stateRef.current.busy && !propsRef.current.blocked) fn() }

  if (latched) {
    // Private region: no parent card list, names, masks or inputs.
    return (
      <section className="cc-panel" aria-label="卡片设置">
        <p className="cc-error" role="alert">{SESSION_ERR}。为保护隐私，卡片资料和已填写的内容都已隐藏。</p>
        <button type="button" className="cc-button cc-secondary" style={{ minHeight: 44 }} onClick={() => window.location.reload()}>刷新页面</button>
      </section>
    )
  }

  return (
    <CardControlPanel
      cards={props.cards} cardId={cardId} mode={mode} state={state}
      blocked={props.blocked} locked={props.locked} amount={amount} merchantId={merchantId}
      newSecret={newSecret} currentSecret={currentSecret}
      onCard={id => local(() => { resetFields(); setCardId(id) })}
      onMode={m => local(() => { setMode(m); setAmount(''); setMerchantId(''); clearSecrets() })}
      onAmount={t => local(() => setAmount(t))}
      onMerchant={id => local(() => setMerchantId(id))}
      onNewSecret={t => local(() => setNewSecret(t))}
      onCurrentSecret={t => local(() => setCurrentSecret(t))}
      onPrepare={() => { void onPrepare() }}
      onRefresh={() => { void withCtrl(c => c.load()) }}
      onCheck={() => { void withCtrl(c => c.check()) }}
      onStartNew={() => { void withCtrl(async c => { if (c.startNew(true)) resetFields() }) }}
      onOpenOperation={() => { if (ctrlRef.current) void propsRef.current.onPrepared() }}
    />
  )
}

export default CardControlSection
