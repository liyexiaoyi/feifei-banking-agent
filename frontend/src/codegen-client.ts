// 生成计算客户端：仅经由现有 api 包装器（含 CSRF/来源/部署配置）访问生成路由。
// 不自动重试，请求丢失后不自动查询；恢复由界面上的显式按钮触发。
// 运行回执复用 sandbox-client 的无损 i64 校验；结果只用 result_text 展示。
import { parseSandboxReceipt } from './sandbox-client.ts'
import type { Api, Receipt } from './sandbox-client.ts'

export type GenerationStatus = 'RESERVED' | 'SENT' | 'GENERATED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'SENT_UNKNOWN' | 'UNKNOWN'
export type Provenance = 'model' | 'fixture'
export type GenerationError = { code: string; message: string }
export type Generation = {
  id: string; generation_key: string; status: GenerationStatus; provenance: Provenance
  wat_sha256: string | null; arg_count: number; arg_names: string[]; error: GenerationError | null
  created_at: string; source_available: boolean
}
export type GenerationView = { generation: Generation; run: Receipt | null; simulated: true }
export type GenerationAvailability = { available: boolean; provenance: Provenance | null; simulated: true }
export type Expected = { key?: string; id?: string }

const BAD = '服务器响应格式无效，已拒绝显示'
const LOST = '请求未完成，生成与计算结果未知；请点击“核对结果”手动核实，切勿重复提交'
const READ_FAIL = '查询失败，请稍后手动重试'
const STATUSES: readonly string[] = ['RESERVED', 'SENT', 'GENERATED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'SENT_UNKNOWN', 'UNKNOWN']
const KEY_RE = /^[A-Za-z0-9_-]{8,100}$/
const ID_RE = /^cgen_[0-9a-f]{32}$/
const HASH_RE = /^[0-9a-f]{64}$/
const NAME_RE = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/
const CODE_RE = /^[A-Za-z0-9_.-]{1,64}$/

const fail = (m: string): never => { throw new Error(m) }
const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)

function checkDescription(d: unknown): asserts d is string {
  if (typeof d !== 'string' || d.trim() === '') fail('请填写计算说明')
  const s = d as string
  if (new TextDecoder().decode(new TextEncoder().encode(s)) !== s) fail('计算说明必须是有效文本')
  if ([...s].length > 300) fail('计算说明不能超过 300 字')
}

function checkArgs(args: unknown): asserts args is number[] {
  if (!Array.isArray(args) || args.length > 8) fail('参数最多 8 个')
  for (const a of args as unknown[]) if (typeof a !== 'number' || !Number.isSafeInteger(a)) fail('参数必须是安全整数')
}

function checkKey(key: unknown): asserts key is string {
  if (typeof key !== 'string' || !KEY_RE.test(key)) fail('请求键格式无效')
}

function checkId(id: unknown): asserts id is string {
  if (typeof id !== 'string' || !ID_RE.test(id)) fail('生成编号格式无效')
}

function parseError(e: unknown): GenerationError | null {
  if (e === null) return null
  if (!isObj(e) || typeof e.code !== 'string' || !CODE_RE.test(e.code)) return fail(BAD)
  if (typeof e.message !== 'string' || e.message.length > 500) return fail(BAD)
  return { code: e.code, message: e.message } // 仅保留固定字段；界面不渲染原始错误
}

function parseGeneration(g: unknown, ex: Expected): Generation {
  if (!isObj(g)) return fail(BAD)
  const { id, generation_key: key, status, provenance, arg_count: count, arg_names: names, created_at, source_available: avail } = g
  if (typeof id !== 'string' || !ID_RE.test(id) || (ex.id !== undefined && id !== ex.id)) return fail(BAD)
  if (typeof key !== 'string' || !KEY_RE.test(key) || (ex.key !== undefined && key !== ex.key)) return fail(BAD)
  if (typeof status !== 'string' || !STATUSES.includes(status)) return fail(BAD)
  if (provenance !== 'model' && provenance !== 'fixture') return fail(BAD)
  const w = g.wat_sha256
  const hash: string | null = w === null ? null : typeof w === 'string' && HASH_RE.test(w) ? w : fail(BAD)
  if (typeof count !== 'number' || !Number.isSafeInteger(count) || count < 0 || count > 8) return fail(BAD)
  if (!Array.isArray(names) || names.length > 8) return fail(BAD)
  if (!names.every(n => typeof n === 'string' && NAME_RE.test(n)) || new Set(names).size !== names.length) return fail(BAD)
  // 源码哈希存在后，参数名必须与参数个数一致；生成前允许空列表
  if (hash !== null && names.length !== count) return fail(BAD)
  if (typeof created_at !== 'string' || created_at === '' || typeof avail !== 'boolean') return fail(BAD)
  if (hash === null && avail) return fail(BAD)
  const error = parseError(g.error)
  return {
    id, generation_key: key, status: status as GenerationStatus, provenance, wat_sha256: hash,
    arg_count: count, arg_names: (names as string[]).slice(), error, created_at, source_available: avail,
  }
}

export function parseGenerationView(raw: unknown, expected: Expected = {}): GenerationView {
  if (!isObj(raw) || raw.simulated !== true) return fail(BAD)
  const generation = parseGeneration(raw.generation, expected)
  const run = raw.run === null ? null : parseSandboxReceipt(raw.run, {})
  // 关联运行必须来自同一源码；跨源证据一律拒绝
  if (run !== null && (generation.wat_sha256 === null || run.source_sha256 !== generation.wat_sha256)) return fail(BAD)
  if (generation.status === 'SUCCEEDED') {
    if (run === null || run.status !== 'SUCCEEDED' || run.result === null || generation.error !== null) return fail(BAD)
  } else if (generation.status === 'FAILED' && run !== null && run.status !== 'FAILED') return fail(BAD)
  // 非成功生成即使附带只读运行回执，状态也保持不变；UNKNOWN 不会被提升为成功
  return { generation, run, simulated: true }
}

async function once(f: () => Promise<unknown>, msg: string): Promise<unknown> {
  try { return await f() } catch { throw new Error(msg) } // 不回显原始错误或载荷，不重试
}

export function createCodegenClient(api: Api) {
  return {
    async status(): Promise<GenerationAvailability> {
      const raw = await once(() => api('/sandbox/generation-status'), READ_FAIL)
      if (!isObj(raw) || raw.simulated !== true || typeof raw.available !== 'boolean') return fail(BAD)
      const p = raw.provenance
      if (raw.available ? p !== 'model' && p !== 'fixture' : p !== null) return fail(BAD)
      return { available: raw.available, provenance: p as Provenance | null, simulated: true }
    },
    async generate(description: string, args: number[], key: string, consent: boolean): Promise<GenerationView> {
      if (consent !== true) fail('请先明确同意后再生成计算')
      checkDescription(description); checkArgs(args); checkKey(key)
      const body = { intent: { kind: 'calc', description, args: args.slice() }, generation_key: key, consent: true }
      return parseGenerationView(await once(() => api('/sandbox/generate', body), LOST), { key })
    },
    async byKey(key: string): Promise<GenerationView> {
      checkKey(key)
      return parseGenerationView(await once(() => api('/sandbox/generate/by-key/' + encodeURIComponent(key)), READ_FAIL), { key })
    },
    async get(id: string): Promise<GenerationView> {
      checkId(id)
      return parseGenerationView(await once(() => api('/sandbox/generations/' + id), READ_FAIL), { id })
    },
    async list(): Promise<GenerationView[]> {
      const raw = await once(() => api('/sandbox/generations'), READ_FAIL)
      if (!isObj(raw) || raw.simulated !== true || !Array.isArray(raw.generations)) return fail(BAD)
      return raw.generations.map(g => parseGenerationView(g, {}))
    },
  }
}
