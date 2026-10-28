import {createBirthdayRuntime, birthdayPendingKey} from './birthday-controller-runtime.ts'
import type {BirthdayState, BirthdayClientLike, BirthdayControllerOptions} from './birthday-controller-runtime.ts'
import {parseBirthdayIntent, parseBirthdayCommand, parseBirthdayKey} from './birthday-client.ts'
import type {BirthdayFlow, BirthdayPending, BirthdayAction} from './birthday-client.ts'
export {birthdayPendingKey}
export type {BirthdayState, BirthdayClientLike, BirthdayControllerOptions}

const READ_ERR = '读取失败，请稍后检查'
const UNKNOWN_ERR = '提交结果未知，请先检查原请求'
const REJECT_ERR = '请求被拒绝，可修改后重试'
const MARK_ERR = '本地记录异常，已阻止提交'
const ACTIONS: Record<string, string> = {cake: 'decide_cake', items: 'select_item'}

// UNKNOWN dominates: never infer payment or outcome from other fields.
function isUnknown(f: BirthdayFlow | null): boolean {
  if (!f) return true
  const x = f as any
  return x.status === 'UNKNOWN' || (Array.isArray(x.nodes) && x.nodes.some((n: any) => n?.state === 'UNKNOWN' || n?.observed_state === 'UNKNOWN'))
}

const IMMUTABLE = ['id', 'recipient_label', 'event_date', 'account_id', 'budget_cents']
function sameImmutable(a: any, b: any): boolean {
  return !!a && !!b && IMMUTABLE.every(k => a[k] === b[k])
}

export function createBirthdayController(options: BirthdayControllerOptions) {
  const rt = createBirthdayRuntime(options)
  const client = (): any => options.client
  const newKey = (): string => parseBirthdayKey(options.keyFactory ? options.keyFactory() : crypto.randomUUID())

  // Read-only proof of the original key. clear=true only for the mutation's own success path.
  async function prove(m: BirthdayPending, clear: boolean, g: number | null): Promise<boolean> {
    const mk = m as any
    const r: any = await rt.call(() => client().lookup(mk.request_key))
    const data: any = await rt.call(() => client().load())
    if (!rt.isLive() || (g !== null && !rt.current(g))) return false
    const want = mk.kind === 'create' ? 'create' : (ACTIONS[mk.action] ?? mk.action)
    const match = r && r.flow && r.action === want && (mk.kind === 'create' || r.flow.id === mk.flow_id)
    const found: BirthdayFlow | null = match ? (data?.flows?.find((f: any) => f.id === r.flow.id) ?? null) : null
    if (!found) {
      rt.patch({data, selected: null, pending: m, needsCheck: true, checked: false, error: UNKNOWN_ERR})
      return false
    }
    // Lookup and owned list must agree on immutable intent; otherwise unresolved.
    if (!sameImmutable(r.flow, found)) {
      rt.patch({data, selected: null, pending: m, needsCheck: true, checked: false, error: UNKNOWN_ERR})
      return false
    }
    // Either side UNKNOWN: never clear marker or promote status. UI copy carries UNKNOWN.
    if (isUnknown(found) || isUnknown(r.flow)) {
      const shown = {...(found as any), status: 'UNKNOWN'} as BirthdayFlow
      if (clear) { rt.patch({data, selected: shown, pending: m, needsCheck: true, checked: false, error: UNKNOWN_ERR}); return false }
      rt.patch({data, selected: shown, pending: m, needsCheck: true, checked: true, error: '', notice: '待核对'})
      return true
    }
    if (!clear) {
      rt.patch({data, selected: found, pending: m, needsCheck: true, checked: true, error: '', notice: '已找到原请求，请确认后再开始新操作'})
      return true
    }
    if (!rt.clearMarker(m)) { rt.patch({needsCheck: true, error: MARK_ERR}); return false }
    rt.patch({data, selected: found, pending: null, needsCheck: false, checked: true, error: '', notice: '已确认提交成功'})
    return true
  }

  // Caller must already hold beginWrite(). Runs inside the global mutation gate.
  async function mutate(m: BirthdayPending, expected: BirthdayPending | null, post: () => Promise<unknown>): Promise<boolean> {
    let ok = false
    try {
      await options.runMutation(async () => {
        if (!(await rt.guard()) || !rt.isLive()) return
        const cur = rt.marker()
        if (JSON.stringify(cur) !== JSON.stringify(expected)) { rt.patch({error: MARK_ERR}); return }
        rt.saveMarker(m) // throws on storage failure, before any POST
        rt.patch({needsCheck: true, pending: m, checked: false, error: '', notice: ''})
        try { await rt.call(post) } catch (e) {
          if (e instanceof Error && (e as any).definiteBirthdayRejection === true && rt.isLive() && (await rt.guard())) {
            if (rt.clearMarker(m)) rt.patch({pending: null, needsCheck: false, error: REJECT_ERR})
            return
          }
          if (rt.isLive()) rt.patch({error: UNKNOWN_ERR})
          return
        }
        try { ok = await prove(m, true, null) } catch { if (rt.isLive()) rt.patch({error: UNKNOWN_ERR}) }
      })
    } catch { if (rt.isLive()) rt.patch({error: MARK_ERR}) } finally { rt.endWrite() }
    return ok && rt.isLive()
  }

  function writable(): boolean {
    const s = rt.getState()
    return rt.isLive() && !!s.data && !s.error && !s.pending && !s.needsCheck && !s.busy
  }

  async function load(): Promise<boolean> {
    const g = rt.beginRead()
    if (g < 0) return false
    rt.patch({data: null, selected: null, operation: null, checked: false, notice: ''})
    let m: BirthdayPending | null = null
    try { m = rt.marker() } catch { rt.patch({needsCheck: true, pending: null, error: MARK_ERR}); return false }
    try {
      if (m) return await prove(m, false, g)
      const data: any = await rt.call(() => client().load())
      if (!rt.current(g)) return false
      rt.patch({data, pending: null, needsCheck: false, error: ''})
      return true
    } catch {
      if (rt.current(g)) rt.patch({data: null, selected: null, operation: null, checked: false, needsCheck: true, error: m ? UNKNOWN_ERR : READ_ERR})
      return false
    }
  }

  async function select(id: string): Promise<boolean> {
    const g = rt.beginRead()
    if (g < 0) return false
    rt.patch({selected: null, operation: null, checked: false}) // proof bound to original selection; marker kept
    try {
      const f: any = await rt.call(() => client().get(id))
      if (!rt.current(g) || !f) return false
      rt.patch({selected: f, operation: null})
      return true
    } catch { if (rt.current(g)) rt.patch({selected: null, operation: null, error: READ_ERR}); return false }
  }

  async function create(intent: unknown): Promise<boolean> {
    let i: any, key: string
    try { i = parseBirthdayIntent(intent); key = newKey() } catch { return false }
    const cs = rt.getState().selected
    if (cs && isUnknown(cs)) return false
    if (!writable() || !rt.beginWrite()) return false
    const m = {kind: 'create', request_key: key} as unknown as BirthdayPending
    return mutate(m, null, () => client().create(i, key))
  }

  async function command(action: BirthdayAction, args: unknown): Promise<boolean> {
    const s = rt.getState(), sel = s.selected as any
    if (!sel) return false
    let c: any, key: string
    try { c = parseBirthdayCommand(sel, action, args); key = newKey() } catch { return false }
    const p = s.pending as any
    // Explicit cancel of the checked original reference only; evaluated before the UNKNOWN block.
    const cancel = c.action === 'cancel' && (args as any)?.confirmed === true && s.checked === true && !s.error && !s.busy && !!p && (p.kind === 'create' || (p.kind === 'command' && p.flow_id === sel.id))
    if (!cancel && isUnknown(sel)) return false
    if (!(cancel || writable()) || !rt.beginWrite()) return false
    const m = {kind: 'command', request_key: key, flow_id: sel.id, action: c.action} as unknown as BirthdayPending
    return mutate(m, cancel ? s.pending : null, () => client().command(sel, c.action, c.args, key))
  }

  async function openOperation(id: string): Promise<boolean> {
    const sel = rt.getState().selected as any
    const g = rt.beginRead()
    if (g < 0 || !sel) return false
    rt.patch({operation: null})
    try {
      const f: any = await rt.call(() => client().get(sel.id))
      if (!rt.current(g)) return false
      if (!f || f.id !== sel.id) throw Error('unrelated')
      // Read-only: UNKNOWN flows may be inspected, no action approval implied.
      const op: any = await rt.call(() => client().readOperation(id, f))
      if (!rt.current(g)) return false
      if (!op || (op.flow_id !== undefined && op.flow_id !== f.id)) throw Error('unrelated')
      rt.patch({selected: f, operation: op})
      return true
    } catch { if (rt.current(g)) rt.patch({operation: null, needsCheck: true, error: READ_ERR}); return false }
  }

  async function startNew(confirmed: boolean): Promise<boolean> {
    const s = rt.getState()
    if (confirmed !== true || !s.checked || !s.pending || !s.selected || s.error || s.busy || isUnknown(s.selected)) return false
    if (!rt.beginWrite()) return false
    try {
      // Recheck session before touching the owner marker; switch keeps it.
      if (!(await rt.guard()) || !rt.isLive()) return false
      const s2 = rt.getState()
      if (s2.checked !== true || s2.error || !s2.selected || isUnknown(s2.selected) || JSON.stringify(s2.pending) !== JSON.stringify(s.pending)) return false
      const cur = rt.marker()
      if (!cur || JSON.stringify(cur) !== JSON.stringify(s.pending) || !rt.clearMarker(cur)) return false
      rt.patch({pending: null, needsCheck: false, checked: false, notice: '已确认，可开始新操作'})
      return true
    } catch { if (rt.isLive()) rt.patch({error: MARK_ERR}); return false } finally { rt.endWrite() }
  }

  return {load, check: load, select, create, command, openOperation, startNew, getState: () => rt.getState(), dispose: () => rt.dispose()}
}
