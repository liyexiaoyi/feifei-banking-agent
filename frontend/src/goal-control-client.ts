import type { ConversationGoalView } from './ConversationGoalPanel'

export type GoalControlAction = 'pause' | 'resume' | 'switch'
export type GoalControlView = ConversationGoalView & { paused?: boolean; pause_gen?: number; reason?: string | null }
export type CommandRef = { goal_id: string; action: GoalControlAction; recorded: true }
export type GoalControlResult = { command: CommandRef; goal: GoalControlView | null }
export type GoalControlApi = <T>(path: string, body?: unknown) => Promise<T>
export interface GoalControlClient {
  list(): Promise<GoalControlView[]>
  lookup(key: string): Promise<CommandRef>
  run(goal: GoalControlView, action: GoalControlAction, key: string, confirmed: boolean): Promise<GoalControlResult>
}

const ACTIONS = new Set(['pause', 'resume', 'switch'])
const STATUSES = new Set(['ACTIVE', 'PAUSED', 'COMPLETED', 'CANCELLED', 'FAILED', 'EXPIRED', 'BLOCKED', 'UNKNOWN'])
const STEP_STATES = new Set(['PENDING', 'READY', 'RUNNING', 'OBSERVED', 'WAITING_CONFIRMATION', 'WAITING_VERIFICATION', 'SUCCEEDED', 'FAILED', 'SKIPPED', 'PAUSED', 'CANCELLED', 'EXPIRED', 'BLOCKED', 'UNKNOWN'])
// Raw secret-bearing keys must never appear in a read-only authority view.
const SECRET_KEYS = new Set(['password', 'passwd', 'csrf', 'csrf_token', 'token', 'secret', 'otp', 'code', 'verification_code', 'pin', 'cvv'])
const ID = /^[A-Za-z0-9_.:-]{1,128}$/
const GOAL_ID = /^[A-Za-z0-9_.:-]{1,64}$/
const KEY = /^[A-Za-z0-9_-]{8,100}$/

const fail = (): never => { throw new Error('INVALID_GOAL_CONTROL_RESPONSE') }
function obj(v: unknown): Record<string, unknown> {
  if (!v || typeof v !== 'object' || Array.isArray(v)) fail()
  const o = v as Record<string, unknown>
  for (const k of Object.keys(o)) if (SECRET_KEYS.has(k.toLowerCase())) fail()
  return o
}
const text = (v: unknown, max: number, nonEmpty = false): string =>
  typeof v === 'string' && v.length <= max && (!nonEmpty || v.trim() !== '') ? v : fail()
const id = (v: unknown): string => typeof v === 'string' && ID.test(v) ? v : fail()
const goalId = (v: unknown): string => typeof v === 'string' && GOAL_ID.test(v) ? v : fail()
const int = (v: unknown, min = 0): number =>
  typeof v === 'number' && Number.isSafeInteger(v) && v >= min ? v : fail()
function arr<T>(v: unknown, max: number, f: (x: unknown) => T): T[] {
  if (!Array.isArray(v) || v.length > max) fail()
  return (v as unknown[]).map(f)
}

export const isAction = (v: unknown): v is GoalControlAction => typeof v === 'string' && ACTIONS.has(v)
export const isControlKey = (v: unknown): v is string => typeof v === 'string' && KEY.test(v)

export function validateGoalView(v: unknown): GoalControlView {
  const g = obj(v)
  const status = text(g.status, 32, true)
  if (!STATUSES.has(status)) fail()
  const view: GoalControlView = {
    id: goalId(g.id), version: int(g.version, 1), kind: text(g.kind, 64, true), status,
    phase: text(g.phase, 64), title: text(g.title, 200, true), question: text(g.question, 500),
    summary: arr(g.summary, 20, s => text(s, 300)),
    choices: arr(g.choices, 100, c => { const o = obj(c); return { id: id(o.id), label: text(o.label, 200, true), price_cents: int(o.price_cents) } }),
    steps: arr(g.steps, 64, s => { const o = obj(s); const state = text(o.state, 32, true); if (!STEP_STATES.has(state)) fail(); return { label: text(o.label, 200, true), state } }),
    next_action: text(g.next_action, 64), operation_ids: arr(g.operation_ids, 64, id),
  }
  if (g.paused !== undefined) { if (typeof g.paused !== 'boolean') fail(); view.paused = g.paused as boolean }
  if (g.pause_gen !== undefined) view.pause_gen = int(g.pause_gen)
  if (g.reason !== undefined) view.reason = g.reason === null ? null : text(g.reason, 200)
  return view
}

export function validateCommand(v: unknown): CommandRef {
  const c = obj(v)
  if (c.recorded !== true || !isAction(c.action)) fail()
  return { goal_id: goalId(c.goal_id), action: c.action as GoalControlAction, recorded: true }
}

export function createGoalControlClient(api: GoalControlApi): GoalControlClient {
  return {
    async list() {
      const d = obj(await api<unknown>('/goal-controls/paused'))
      return arr(d.goals, 100, validateGoalView)
    },
    async lookup(key) {
      if (!isControlKey(key)) fail()
      const d = obj(await api<unknown>('/goal-controls/commands/' + encodeURIComponent(key)))
      return validateCommand(d.command)
    },
    async run(goal, action, key, confirmed) {
      if (confirmed !== true || !isAction(action) || !isControlKey(key)) throw new Error('GOAL_CONTROL_INVALID_REQUEST')
      const g = validateGoalView(goal)
      // Body is exactly the strict contract; no goal text or other fields are sent.
      const d = obj(await api<unknown>('/goal-controls/' + encodeURIComponent(g.id) + '/' + action,
        { expected_version: g.version, request_key: key, confirmed: true }))
      const command = validateCommand(d.command)
      if (command.goal_id !== g.id || command.action !== action) fail()
      if (d.goal !== null && d.goal === undefined) fail()
      return { command, goal: d.goal === null ? null : validateGoalView(d.goal) }
    },
  }
}
