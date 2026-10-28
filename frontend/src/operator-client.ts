// Independent user/operator case clients. Pure module: no storage, logging, retry or bank session.
import { parseCasePreview, parseOperatorCase } from './operator-dto.ts'
import type { CaseFacts, OperatorCase } from './operator-dto.ts'
export type { CaseStatus, BankStatus, FactOperation, CaseFacts, OperatorCase } from './operator-dto.ts'
export { parseCasePreview, parseOperatorCase } from './operator-dto.ts'

export type UserApi = (path: string, body?: unknown) => Promise<unknown>
export interface DeploymentGate {
  check(): Promise<boolean>
  requireReady(fresh: boolean): Promise<string>
  block(): void
}
type Fetcher = (path: string, init?: RequestInit) => Promise<Response>

export class OperatorClientError extends Error {
  readonly code: string
  readonly status: number
  constructor(message: string, code: string, status: number) {
    super(message)
    this.name = 'OperatorClientError'
    this.code = code
    this.status = status
  }
}

const ID = /^[A-Za-z0-9_-]{1,128}$/
const KEY = /^[A-Za-z0-9_-]{8,64}$/
const MAX_REPLY = 500

function bad(field: string): never {
  throw new OperatorClientError(`请求参数无效：${field}`, 'INVALID_INPUT', 0)
}
function checkId(v: unknown, f: string): string {
  if (typeof v !== 'string' || !ID.test(v)) bad(f)
  return v
}
function checkKey(v: unknown): string {
  if (typeof v !== 'string' || !KEY.test(v)) bad('request_key')
  return v
}
function checkVersion(v: unknown, min: number): number {
  if (typeof v !== 'number' || !Number.isSafeInteger(v) || v < min) bad('expected_version')
  return v
}
function obj(raw: unknown): Record<string, unknown> {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) throw new TypeError('invalid response')
  return raw as Record<string, unknown>
}
function bindCase(c: OperatorCase, caseId?: string, planId?: string): OperatorCase {
  if (caseId !== undefined && c.id !== caseId) throw new TypeError('case id mismatch')
  if (planId !== undefined && c.plan_id !== planId) throw new TypeError('case plan mismatch')
  return c
}

export function createUserCaseClient(api: UserApi) {
  const planPath = (plan: string) => `/plans/${encodeURIComponent(checkId(plan, 'plan_id'))}/operator-case`
  const casePath = (id: string) => `/operator-cases/${encodeURIComponent(checkId(id, 'case_id'))}`
  return {
    async lookup(plan: string): Promise<OperatorCase | null> {
      const r = obj(await api(planPath(plan)))
      return r.case === null ? null : bindCase(parseOperatorCase(r.case), undefined, plan)
    },
    async preview(plan: string): Promise<CaseFacts> {
      const p = parseCasePreview(obj(await api(`${planPath(plan)}/preview`)).preview)
      if (p.plan_id !== plan) throw new TypeError('preview plan mismatch')
      return p
    },
    async get(id: string): Promise<OperatorCase> {
      return bindCase(parseOperatorCase(await api(casePath(id))), id)
    },
    async share(plan: string, version: number, confirmed: boolean, key: string): Promise<OperatorCase> {
      const path = planPath(plan)
      const body = { expected_version: checkVersion(version, 0), confirmed: true, request_key: checkKey(key) }
      if (confirmed !== true) bad('confirmed')
      return bindCase(parseOperatorCase(await api(path, body)), undefined, plan)
    },
    async withdraw(id: string, version: number, key: string): Promise<OperatorCase> {
      const path = `${casePath(id)}/withdraw`
      const body = { expected_version: checkVersion(version, 1), request_key: checkKey(key) }
      return bindCase(parseOperatorCase(await api(path, body)), id)
    },
  }
}

export function createOperatorClient(gate: DeploymentGate, fetcher: Fetcher = fetch) {
  const send = fetcher // captured once; later global changes are ignored
  let csrf = ''

  async function call(path: string, method: string, body?: unknown): Promise<unknown> {
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (method !== 'GET') {
      headers['X-Build-Identity'] = await gate.requireReady(true)
      headers['X-CSRF-Token'] = csrf
      if (body !== undefined) headers['Content-Type'] = 'application/json'
    }
    let res: Response
    try {
      res = await send(path, { method, headers, credentials: 'same-origin', redirect: 'error',
        body: body === undefined ? undefined : JSON.stringify(body) })
    } catch {
      throw new OperatorClientError('网络异常，结果未知。请刷新后确认，不会自动重试。', 'NETWORK_ERROR', 0)
    }
    const data: unknown = await res.json().catch(() => null)
    if (res.ok) return data
    const err = typeof data === 'object' && data !== null ? (data as { error?: { code?: unknown } }).error : undefined
    const code = typeof err?.code === 'string' ? err.code : 'REQUEST_FAILED'
    if (res.status === 401) {
      csrf = ''
      throw new OperatorClientError('坐席登录已失效，请重新登录。', code, 401)
    }
    if (code === 'DEPLOYMENT_MISMATCH') {
      gate.block()
      throw new OperatorClientError('页面版本已更新，请刷新后再操作。', code, res.status)
    }
    throw new OperatorClientError('请求未能完成，请稍后确认案件状态。', code, res.status)
  }

  function adopt(raw: unknown): void {
    const s = obj(raw)
    if (typeof s.csrf !== 'string' || s.csrf.length === 0 || s.csrf.length > 256) throw new TypeError('invalid csrf')
    checkId(s.operator_id, 'operator_id')
    if (typeof s.expires_at !== 'number' || !Number.isFinite(s.expires_at) || s.expires_at <= 0) throw new TypeError('invalid expires_at')
    if (s.simulated !== true) throw new TypeError('invalid simulated')
    csrf = s.csrf
  }
  function requireSession(): void {
    if (!csrf) throw new OperatorClientError('请先登录演示坐席。', 'OPERATOR_SESSION_REQUIRED', 401)
  }
  const casePath = (id: string) => `/api/operator/cases/${encodeURIComponent(checkId(id, 'case_id'))}`

  return {
    async initialize(): Promise<boolean> {
      if (!(await gate.check())) return false
      try {
        adopt(await call('/api/operator/session', 'GET'))
        return true
      } catch (e) {
        csrf = ''
        if (e instanceof OperatorClientError && e.status === 401) return false
        throw e
      }
    },
    async login(accessToken: string): Promise<void> {
      if (typeof accessToken !== 'string' || accessToken.length === 0 || accessToken.length > 512) bad('access_token')
      csrf = ''
      adopt(await call('/api/operator/session', 'POST', { access_token: accessToken }))
    },
    async logout(): Promise<void> {
      try {
        await call('/api/operator/session', 'DELETE')
      } finally {
        csrf = ''
      }
    },
    async list(): Promise<OperatorCase[]> {
      const raw = obj(await call('/api/operator/cases', 'GET')).cases
      if (!Array.isArray(raw)) throw new TypeError('invalid cases')
      const seen = new Set<string>()
      return raw.map(item => {
        const c = parseOperatorCase(item)
        if (seen.has(c.id)) throw new TypeError('duplicate case')
        seen.add(c.id)
        return c
      })
    },
    async get(id: string): Promise<OperatorCase> {
      return bindCase(parseOperatorCase(await call(casePath(id), 'GET')), id)
    },
    async claim(id: string, version: number, key: string): Promise<OperatorCase> {
      const path = `${casePath(id)}/claim`
      const body = { expected_version: checkVersion(version, 1), request_key: checkKey(key) }
      requireSession()
      return bindCase(parseOperatorCase(await call(path, 'POST', body)), id)
    },
    async reply(id: string, version: number, text: string, key: string): Promise<OperatorCase> {
      const path = `${casePath(id)}/reply`
      if (typeof text !== 'string' || text.trim().length === 0 || text.length > MAX_REPLY) bad('reply')
      const body = { expected_version: checkVersion(version, 1), reply: text, request_key: checkKey(key) }
      requireSession()
      return bindCase(parseOperatorCase(await call(path, 'POST', body)), id)
    },
  }
}
