import { useEffect, useRef, useState, type ReactNode } from 'react'
import { api } from './api.ts'
import { createBirthdayClient, parseBirthdayAmount, parseBirthdayDate, birthdayNeedsCheck, type BirthdayOperation, type BirthdayAction, type BirthdayArgs, type BirthdayIntent } from './birthday-client.ts'
import { createBirthdayController, type BirthdayState } from './birthday-controller.ts'
import BirthdayPanel from './BirthdayPanel.tsx'

export type BirthdaySectionProps = {
  blocked: boolean
  locked: boolean
  runMutation(task: () => Promise<void>): Promise<void>
  onStateRefresh?: () => Promise<void>
  renderOperation(op: BirthdayOperation, reload: () => Promise<void>, canExecute: boolean): ReactNode
  initialFlowId?: string | null
}
type Ctrl = ReturnType<typeof createBirthdayController>
type Opts = { accounts: string[]; business_date: string }

const EMPTY: BirthdayState = { data: null, selected: null, operation: null, pending: null, busy: true, needsCheck: false, invalidSession: false, error: '', notice: '', checked: false }

// Narrow read of SDK-parsed options; anything unexpected yields null (fail closed).
function readOpts(s: BirthdayState): Opts | null {
  const data = s.data
  return data ? { accounts: data.accounts.map(a => a.id), business_date: data.business_date } : null
}

function stageValid(stage: number, f: { recipient: string; eventDate: string; amount: string; accountId: string }, o: Opts | null): boolean {
  try {
    if (stage >= 0 && !f.recipient.trim()) return false
    if (stage >= 1) { if (!o) return false; const d = parseBirthdayDate(f.eventDate); if (!d || d < o.business_date) return false }
    if (stage >= 2 && parseBirthdayAmount(f.amount) === null) return false
    if (stage >= 3 && (!o || !o.accounts.includes(f.accountId))) return false
    return true
  } catch { return false }
}

export function BirthdaySection(props: BirthdaySectionProps) {
  const propsRef = useRef(props); propsRef.current = props
  const [state, setState] = useState<BirthdayState>(EMPTY)
  const stateRef = useRef(state); stateRef.current = state
  const [sessionBad, setSessionBad] = useState(false)
  const [stage, setStage] = useState(0)
  const [recipient, setRecipient] = useState('')
  const [eventDate, setEventDate] = useState('')
  const [amount, setAmount] = useState('')
  const [accountId, setAccountId] = useState('')
  const [consent, setConsent] = useState(false)
  const ctrlRef = useRef<Ctrl | null>(null)
  const genRef = useRef(0)

  const resetForm = () => { setStage(0); setRecipient(''); setEventDate(''); setAmount(''); setAccountId(''); setConsent(false) }
  const invalid = sessionBad || state.invalidSession
  const readGate = () => !ctrlRef.current || sessionBad || stateRef.current.invalidSession || stateRef.current.busy
  const lockGate = () => readGate() || propsRef.current.blocked || propsRef.current.locked

  useEffect(() => {
    const g = ++genRef.current
    const live = () => genRef.current === g
    const client = createBirthdayClient(api)
    // Captured once at mount; only a strict lowercase-hex 32 id is used.
    const initialRaw = propsRef.current.initialFlowId
    const initialId = typeof initialRaw === 'string' && /^[0-9a-f]{32}$/.test(initialRaw) ? initialRaw : null
    let ctrl: Ctrl | null = null
    void (async () => {
      let user: 'demo' | 'guest'
      try { user = await client.session() } catch { if (live()) setSessionBad(true); return }
      if (!live()) return
      ctrl = createBirthdayController({
        client, storage: window.localStorage, user,
        runMutation: task => propsRef.current.runMutation(task),
        onChange: s => { if (!live()) return; stateRef.current = s; setState(s) },
      })
      ctrlRef.current = ctrl
      const loaded = await ctrl.load()
      if (!loaded || !initialId || !live() || ctrlRef.current !== ctrl) return
      // Pending marker recovery already selected the original reference; keep its proof.
      if (ctrl.getState().pending) return
      await ctrl.select(initialId)
    })()
    return () => { genRef.current++; ctrl?.dispose(); if (ctrlRef.current === ctrl) ctrlRef.current = null }
  }, [])

  // Invalid session or read error: drop private draft.
  useEffect(() => { if (invalid || state.error) resetForm() }, [invalid, state.error])

  const field = (set: (v: string) => void) => (v: string) => { set(v); setConsent(false) }
  const opts = readOpts(state)
  const fields = { recipient, eventDate, amount, accountId }

  const onNext = () => { if (!lockGate() && !stateRef.current.needsCheck && !stateRef.current.pending && stage < 4 && stageValid(stage, fields, readOpts(stateRef.current))) { setStage(stage + 1); setConsent(false) } }
  const onBack = () => { if (!lockGate() && !stateRef.current.needsCheck && !stateRef.current.pending && stage > 0) { setStage(stage - 1); setConsent(false) } }
  const onStart = () => {
    const c = ctrlRef.current
    if (!c || lockGate() || stage !== 4 || !consent || !stageValid(3, fields, readOpts(stateRef.current))) return
    let intent: BirthdayIntent
    const day = parseBirthdayDate(eventDate), cents = parseBirthdayAmount(amount)
    if (day === null || cents === null) return
    intent = { recipient_label: recipient.trim(), event_date: day, account_id: accountId, budget_cents: cents }
    void c.create(intent).then(ok => { if (ok && ctrlRef.current === c) resetForm() })
  }
  const onSelect = (id: string) => { if (!readGate()) void ctrlRef.current?.select(id) }
  const onOpen = (opid: string) => { if (!readGate()) void ctrlRef.current?.openOperation(opid) }
  const onCommand = (action: BirthdayAction, args: BirthdayArgs) => { if (!lockGate()) void ctrlRef.current?.command(action, args) }
  const onCheck = () => { if (!readGate()) void ctrlRef.current?.check() }
  const onAcknowledge = () => { if (!lockGate()) void ctrlRef.current?.startNew(true) }

  // Read-only refresh: reload, reselect same flow, reopen same op; never reuse stale op.
  const reload = async () => {
    const c = ctrlRef.current
    if (!c || readGate()) return
    const g = genRef.current
    const flowId = stateRef.current.selected?.id ?? null
    const opId = stateRef.current.operation?.id ?? null
    const live = () => genRef.current === g && ctrlRef.current === c
    const ok = await c.load()
    if (!ok || !live()) return
    const current = c.getState()
    // Key recovery already selected the original reference; selecting again drops its proof.
    if (current.pending) {
      if (opId && current.selected?.nodes.some(n => n.operation_id === opId) && live()) await c.openOperation(opId)
    } else if (flowId) {
      const selected = await c.select(flowId)
      if (selected && live() && opId) await c.openOperation(opId)
    }
    if (live()) await propsRef.current.onStateRefresh?.().catch(() => undefined)
  }
  const onRefresh = () => { void reload() }

  const sel = state.selected
  const canExecute = !!sel && !invalid && sel.status === 'ACTIVE' && !birthdayNeedsCheck(sel) && !state.error && !state.pending && !state.needsCheck && !state.busy && !props.blocked && !props.locked

  return (
    <section aria-label="生日安排">
      <BirthdayPanel
        state={invalid ? { ...state, data: null, selected: null, operation: null, invalidSession: true } : state}
        blocked={props.blocked} locked={props.locked} stage={stage}
        recipient={recipient} eventDate={eventDate} amount={amount} accountId={opts?.accounts.includes(accountId) || !accountId ? accountId : ''} consent={consent}
        onRecipient={field(setRecipient)} onDate={field(setEventDate)} onAmount={field(setAmount)} onAccount={field(setAccountId)}
        onConsent={v => setConsent(v === true)} onNext={onNext} onBack={onBack} onStart={onStart}
        onSelect={onSelect} onCommand={onCommand} onOpen={onOpen}
        onRefresh={onRefresh} onCheck={onCheck} onAcknowledge={onAcknowledge}
      />
      {state.operation && sel && !state.error && !invalid ? props.renderOperation(state.operation, reload, canExecute) : null}
    </section>
  )
}
