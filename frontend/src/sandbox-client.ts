// 沙箱客户端：仅经由现有 api 包装器（含 CSRF/来源/部署配置）访问已有路由。
// 不自动重试，不在 POST 丢失后自动 GET；恢复由界面上的显式按钮触发。
// 展示数值一律使用经校验的 result_text，绝不用 Number 显示 i64。

export const PROVIDER_AVERAGE_WAT = '(module\n  (func (export "run") (param $a i64) (param $b i64) (result i64)\n    local.get $a\n    local.get $b\n    i64.add\n    i64.const 2\n    i64.div_s))\n'

export type Status = 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'UNKNOWN'
export type Limits = { linear_memory_bytes: 1048576; process_memory_bytes: 268435456; cpu_seconds: 2; wall_seconds: 5; output_bytes: 4096 }
export type Result = { ok: true; result: number; result_text: string; fuel_used: number; elapsed_ms: number; cpu_user_ms: number; source_sha256: string; simulated: true; limits: Limits }
export type Receipt = { id: string; status: Status; result: Result | null; error: { code: string; message: string } | null; simulated: true; request_key: string; source_sha256: string; created_at: string }
export type Api = (path: string, body?: unknown) => Promise<unknown>

const BAD = '服务器响应格式无效，已拒绝显示'
const LOST = '请求未完成，运行结果未知；请点击“按请求键查询”手动核实，切勿重复提交'
const READ_FAIL = '查询失败，请稍后手动重试'
const STATUSES: readonly string[] = ['RUNNING', 'SUCCEEDED', 'FAILED', 'UNKNOWN']
const KEY_RE = /^[A-Za-z0-9_-]{8,100}$/
const ID_RE = /^sbx_[0-9a-f]{32}$/
const HASH_RE = /^[0-9a-f]{64}$/
const INT_RE = /^(0|-?[1-9][0-9]*)$/
const I64_MIN = BigInt('-9223372036854775808')
const I64_MAX = BigInt('9223372036854775807')
const LIMITS: Limits = { linear_memory_bytes: 1048576, process_memory_bytes: 268435456, cpu_seconds: 2, wall_seconds: 5, output_bytes: 4096 }
const LIMIT_KEYS = Object.keys(LIMITS) as (keyof Limits)[]

const fail = (m: string): never => { throw new Error(m) }
const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const nonNeg = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0
const has = (o: object, k: string) => Object.prototype.hasOwnProperty.call(o, k)

function checkWat(wat: unknown): asserts wat is string {
  if (typeof wat !== 'string' || wat.trim() === '') fail('WAT 不能为空')
  const bytes = new TextEncoder().encode(wat as string)
  if (new TextDecoder().decode(bytes) !== wat) fail('WAT 必须是有效的 UTF-8 文本')
  if (bytes.length > 32768) fail('WAT 不能超过 32768 字节')
}

function checkArgs(args: unknown): asserts args is number[] {
  if (!Array.isArray(args) || args.length > 8) fail('参数最多 8 个')
  for (const a of args as unknown[]) if (typeof a !== 'number' || !Number.isSafeInteger(a)) fail('参数必须是安全整数')
}

function checkKey(key: unknown): asserts key is string {
  if (typeof key !== 'string' || !KEY_RE.test(key)) fail('请求键格式无效')
}

function checkId(id: unknown): asserts id is string {
  if (typeof id !== 'string' || !ID_RE.test(id)) fail('运行编号格式无效')
}

function parseResult(v: unknown, hash: string): Result {
  if (!isObj(v) || v.ok !== true || v.simulated !== true || v.source_sha256 !== hash) return fail(BAD)
  const t = v.result_text
  if (typeof t !== 'string' || t.length > 20 || !INT_RE.test(t)) return fail(BAD)
  const big = BigInt(t)
  if (big < I64_MIN || big > I64_MAX) return fail(BAD)
  // result 数值可能被 JSON.parse 舍入，仅做类型检查，不用于显示
  if (typeof v.result !== 'number' || !Number.isInteger(v.result)) return fail(BAD)
  if (!nonNeg(v.fuel_used) || v.fuel_used > 100000 || !nonNeg(v.elapsed_ms) || !nonNeg(v.cpu_user_ms)) return fail(BAD)
  const l = v.limits
  if (!isObj(l) || Object.keys(l).length !== LIMIT_KEYS.length || !LIMIT_KEYS.every(k => has(l, k) && l[k] === LIMITS[k])) return fail(BAD)
  return { ok: true, result: v.result, result_text: t, fuel_used: v.fuel_used, elapsed_ms: v.elapsed_ms, cpu_user_ms: v.cpu_user_ms, source_sha256: hash, simulated: true, limits: { ...LIMITS } }
}

function parseReceipt(v: unknown, expect: { id?: string; key?: string }): Receipt {
  if (!isObj(v) || v.simulated !== true) return fail(BAD)
  const { id, status, request_key, source_sha256, created_at, error } = v
  if (typeof id !== 'string' || !ID_RE.test(id) || (expect.id !== undefined && id !== expect.id)) return fail(BAD)
  if (typeof status !== 'string' || !STATUSES.includes(status)) return fail(BAD)
  if (typeof request_key !== 'string' || !KEY_RE.test(request_key) || (expect.key !== undefined && request_key !== expect.key)) return fail(BAD)
  if (typeof source_sha256 !== 'string' || !HASH_RE.test(source_sha256) || typeof created_at !== 'string' || created_at === '') return fail(BAD)
  let err: Receipt['error'] = null
  if (error !== null) {
    if (!isObj(error) || typeof error.code !== 'string' || typeof error.message !== 'string') return fail(BAD)
    err = { code: error.code, message: error.message }
  }
  let result: Result | null = null
  if (status === 'SUCCEEDED') {
    if (err !== null) return fail(BAD)
    result = parseResult(v.result, source_sha256)
  } else if (v.result !== null) return fail(BAD) // 非成功状态不得携带结果；UNKNOWN 保持 UNKNOWN
  return { id, status: status as Status, result, error: err, simulated: true, request_key, source_sha256, created_at }
}

export function parseSandboxReceipt(raw: unknown, expected: { id?: string; key?: string } = {}): Receipt {
  return parseReceipt(raw, expected) // 仅复用既有校验，不改变旧客户端行为
}

async function once(f: () => Promise<unknown>, msg: string): Promise<unknown> {
  try { return await f() } catch { throw new Error(msg) } // 不回显原始错误或载荷，不重试
}

export function createSandboxClient(api: Api) {
  return {
    async run(wat: string, args: number[], request_key: string): Promise<Receipt> {
      checkWat(wat); checkArgs(args); checkKey(request_key)
      const body = { wat, args: args.slice(), request_key }
      return parseReceipt(await once(() => api('/sandbox/runs', body), LOST), { key: request_key })
    },
    async byKey(key: string): Promise<Receipt> {
      checkKey(key)
      return parseReceipt(await once(() => api('/sandbox/by-key/' + encodeURIComponent(key)), READ_FAIL), { key })
    },
    async get(id: string): Promise<Receipt> {
      checkId(id)
      return parseReceipt(await once(() => api('/sandbox/runs/' + id), READ_FAIL), { id })
    },
    async list(): Promise<Receipt[]> {
      const raw = await once(() => api('/sandbox/runs'), READ_FAIL)
      if (!isObj(raw) || raw.simulated !== true || !Array.isArray(raw.runs)) return fail(BAD)
      return raw.runs.map(r => parseReceipt(r, {}))
    },
  }
}
