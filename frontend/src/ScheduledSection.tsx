import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { api } from './api.ts'
import { createScheduledClient, parseScheduledAmount, parseScheduledTime, type Schedule, type ScheduleOperation } from './scheduled-client.ts'
import { createScheduledController, type ScheduledState } from './scheduled-controller.ts'
import ScheduledPanel from './ScheduledPanel.tsx'

export type ScheduledSectionProps = {
  blocked: boolean
  locked: boolean
  runMutation(task: () => Promise<void>): Promise<void>
  onStateRefresh?: () => Promise<void>
  renderOperation(op: ScheduleOperation, onRefresh: () => Promise<void>): ReactNode
}
type Ctrl = ReturnType<typeof createScheduledController>

const EMPTY: ScheduledState = { data: null, pending: null, recovered: null, operation: null, busy: true, needsCheck: false, invalidSession: false, error: '', checked: false }
const AUTO_MS = 5000

export function ScheduledSection(props: ScheduledSectionProps) {
  const propsRef = useRef(props); propsRef.current = props
  const [state, setState] = useState<ScheduledState>(EMPTY)
  const [sessionBad, setSessionBad] = useState(false)
  const [payeeId, setPayeeId] = useState('')
  const [amount, setAmount] = useState('')
  const [due, setDue] = useState('')
  const [consent, setConsent] = useState(false)
  const [auto, setAuto] = useState(false)
  const ctrlRef = useRef<Ctrl | null>(null)
  const genRef = useRef(0)
  const stateRef = useRef(state); stateRef.current = state
  const tickRef = useRef(false) // sync latch: one manual/auto drive at a time
  const ownCycleRef = useRef(false) // needsCheck during own successful auto cycle is normal
  const autoRef = useRef(auto); autoRef.current = auto

  const resetFields = () => { setPayeeId(''); setAmount(''); setDue(''); setConsent(false) }
  const autoOff = () => { autoRef.current = false; setAuto(false) }
  const invalid = sessionBad || state.invalidSession
  const busyGate = () => !ctrlRef.current || invalid || stateRef.current.busy || tickRef.current || propsRef.current.blocked
  const lockGate = () => busyGate() || propsRef.current.locked

  // Lifecycle: one client/controller per mount generation; stale callbacks never publish.
  useEffect(() => {
    const g = ++genRef.current
    const live = () => genRef.current === g
    const client = createScheduledClient(api)
    let ctrl: Ctrl | null = null
    void (async () => {
      let user: 'demo' | 'guest'
      try { user = await client.session() } catch { if (live()) { setSessionBad(true); autoOff() } return }
      if (!live()) return
      ctrl = createScheduledController({
        client, storage: window.localStorage, user,
        runMutation: task => propsRef.current.runMutation(task),
        onChange: s => { if (!live()) return; stateRef.current = s; setState(s) },
      })
      ctrlRef.current = ctrl
      const ok = await ctrl.load()
      if (live() && !ok) autoOff()
    })()
    return () => {
      genRef.current++
      ctrl?.dispose()
      if (ctrlRef.current === ctrl) ctrlRef.current = null
      tickRef.current = false
    }
  }, [])

  // Fail closed: any invalid/error/unknown state turns auto off and clears private fields.
  useEffect(() => {
    if (invalid) { autoOff(); resetFields(); return }
    if (state.error || (state.needsCheck && !ownCycleRef.current)) autoOff()
  }, [invalid, state.error, state.needsCheck])

  const drive = useCallback(async (fromAuto: boolean) => {
    const c = ctrlRef.current
    if (!c || lockGate()) return
    tickRef.current = true
    ownCycleRef.current = fromAuto
    let good = false
    try {
      const ok = await c.drive() // performs its own GET load after POST
      const s = c.getState()
      if (ok && !s.invalidSession && !s.error && s.data && s.checked) good = await c.startNew(true)
      if (good) await propsRef.current.onStateRefresh?.().catch(() => undefined)
    } catch { good = false } finally {
      tickRef.current = false
      ownCycleRef.current = false
      if (!good) autoOff()
    }
  }, [])

  useEffect(() => {
    if (!auto || invalid) return
    const g = genRef.current
    const id = window.setInterval(() => {
      if (genRef.current !== g || !autoRef.current || document.visibilityState !== 'visible') return
      if (tickRef.current || stateRef.current.busy || propsRef.current.blocked || propsRef.current.locked) return
      void drive(true)
    }, AUTO_MS)
    return () => window.clearInterval(id)
  }, [auto, invalid, drive])

  const onPrepare = async () => {
    autoOff()
    const c = ctrlRef.current
    if (!c || lockGate() || !consent || !payeeId) return
    const amount_cents = parseScheduledAmount(amount)
    const due_at = parseScheduledTime(due)
    if (amount_cents === null || due_at === null) return
    tickRef.current = true
    try { if (await c.prepare({ payee_id: payeeId, amount_cents, due_at })) resetFields() } finally { tickRef.current = false }
  }
  const onCancel = async (s: Schedule) => {
    autoOff()
    const c = ctrlRef.current
    if (!c || busyGate()) return // cancel allowed while locked for safety
    tickRef.current = true
    try { await c.cancel(s, true); if (!await c.check()) autoOff() } finally { tickRef.current = false }
  }
  const onRefresh = async () => {
    const c = ctrlRef.current
    if (!c || busyGate()) return
    if (!await c.load()) autoOff()
  }
  const onCheck = async () => {
    const c = ctrlRef.current
    if (!c || busyGate()) return
    if (!await c.check()) autoOff()
  }
  const onStartNew = async () => {
    const c = ctrlRef.current
    if (!c || busyGate()) return
    if (await c.startNew(true)) resetFields()
  }
  const onOpen = async (id: string, s: Schedule) => {
    const c = ctrlRef.current
    if (!c || busyGate()) return
    if (!await c.openOperation(id, s)) autoOff()
  }
  // After the shared OperationCard acts: read-only refresh, re-find owned schedule, reopen op.
  const rendererRefresh = useCallback(async () => {
    const c = ctrlRef.current, op = stateRef.current.operation
    if (!c) return
    const opId = op?.id
    const sid = stateRef.current.data?.schedules.find(x => x.authorization_operation_id === opId || x.child_operation_id === opId)?.id
      ?? stateRef.current.recovered?.id
    const ok = await c.load()
    if (!ok) autoOff()
    else if (opId && sid) {
      const s = c.getState()
      const found = [...(s.data?.schedules ?? []), ...(s.recovered ? [s.recovered] : [])].find(x => x.id === sid)
      if (found && (found.authorization_operation_id === opId || found.child_operation_id === opId)) {
        if (!await c.openOperation(opId, found)) autoOff()
      }
    }
    await propsRef.current.onStateRefresh?.().catch(() => undefined)
  }, [])

  if (invalid) {
    return (
      <section aria-label="定时转账" className="sched">
        <p role="alert">登录状态已变化或无法确认，请刷新页面后再操作。</p>
        <button type="button" onClick={() => window.location.reload()}>刷新页面</button>
      </section>
    )
  }

  return (
    <>
      <ScheduledPanel
        state={state} blocked={props.blocked} locked={props.locked}
        payeeId={payeeId} amount={amount} due={due} consent={consent} autoEnabled={auto}
        onPayee={setPayeeId} onAmount={setAmount} onDue={setDue} onConsent={setConsent}
        onPrepare={() => void onPrepare()}
        onRefresh={() => void onRefresh()}
        onCheck={() => void onCheck()}
        onStartNew={() => void onStartNew()}
        onCancel={s => void onCancel(s)}
        onOpen={(id, s) => void onOpen(id, s)}
        onDrive={() => void drive(false)}
        onAuto={v => { if (!v) { autoOff(); return } if (!lockGate() && !stateRef.current.needsCheck && !stateRef.current.error) setAuto(true) }}
      />
      {state.operation ? props.renderOperation(state.operation, rendererRefresh) : null}
    </>
  )
}
