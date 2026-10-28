// AA 客户端：仅本地记录，不发送消息、不收款、不转账。所有网络经注入的 api，无重试、无存储。
export type AAParticipant = { id: string; name: string; masked_phone: string }
export type AAShare = { participant_id: string; name: string; masked_phone: string; is_self: boolean; amount_cents: number }
export type AAPreview = {
  total_cents: number; participant_count: number; include_self: boolean; shares: AAShare[]
  self_share_cents: number; receivable_cents: number; simulated: true; notice: string
}
export type AARequest = AAPreview & {
  id: string; status: 'ACTIVE' | 'CANCELLED'; version: number
  created_at: string; updated_at: string; replayed: boolean
}
type Api = (path: string, body?: unknown) => Promise<unknown>

const fail = (msg: string): never => { throw new Error(msg) }
const obj = (v: unknown, what: string): Record<string, unknown> =>
  v !== null && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : fail(what + '格式无效')
const str = (v: unknown, what: string): string => typeof v === 'string' ? v : fail(what + '无效')
const nonEmpty = (v: unknown, what: string): string => { const s = str(v, what); return s.length > 0 ? s : fail(what + '不能为空') }
const bool = (v: unknown, what: string): boolean => typeof v === 'boolean' ? v : fail(what + '必须为布尔值')
const int = (v: unknown, what: string, min: number, max = Number.MAX_SAFE_INTEGER): number =>
  typeof v === 'number' && Number.isSafeInteger(v) && v >= min && v <= max ? v : fail(what + '超出范围')
const MASKED = /^(?:\d{3}\*{4}\d{4}|\*{4})$/
const masked = (v: unknown): string => { const s = str(v, '手机号'); return MASKED.test(s) ? s : fail('手机号未脱敏') }
const date = (v: unknown, what: string): string => {
  const s = nonEmpty(v, what); return Number.isNaN(Date.parse(s)) ? fail(what + '日期无效') : s
}
const KEY = /^[A-Za-z0-9_-]{8,64}$/

export function parseAAParticipant(v: unknown): AAParticipant {
  const o = obj(v, '参与人')
  return { id: nonEmpty(o.id, '参与人编号'), name: str(o.name, '参与人姓名'), masked_phone: masked(o.masked_phone) }
}

export function parseAAPreview(v: unknown): AAPreview {
  const o = obj(v, 'AA 预览')
  if (o.simulated !== true) fail('必须为模拟记录')
  const total = int(o.total_cents, '总金额', 1, 100000000)
  const includeSelf = bool(o.include_self, '是否包含本人')
  const count = int(o.participant_count, '人数', 2, 20)
  if (!Array.isArray(o.shares) || o.shares.length !== count) fail('分摊人数不一致')
  const base = Math.floor(total / count), rem = total % count
  const seen = new Set<string>()
  let sum = 0
  const shares = (o.shares as unknown[]).map((raw, i): AAShare => {
    const s = obj(raw, '分摊项')
    const pid = nonEmpty(s.participant_id, '分摊参与人')
    if (seen.has(pid)) fail('分摊参与人重复')
    seen.add(pid)
    const isSelf = bool(s.is_self, '本人标记')
    const amount = int(s.amount_cents, '分摊金额', 0)
    if (amount !== base + (i < rem ? 1 : 0)) fail('分摊顺序或金额不正确')
    const self = includeSelf && i === 0
    if (isSelf !== self || (self && pid !== 'self') || (!self && pid === 'self')) fail('本人分摊不一致')
    const phone = self ? (s.masked_phone === '' ? '' : fail('本人手机号无效')) : masked(s.masked_phone)
    sum += amount
    return { participant_id: pid, name: str(s.name, '姓名'), masked_phone: phone, is_self: isSelf, amount_cents: amount }
  })
  if (sum !== total) fail('分摊总额不一致')
  const mine = includeSelf ? shares[0].amount_cents : 0
  if (int(o.self_share_cents, '本人金额', 0) !== mine) fail('本人金额不一致')
  if (int(o.receivable_cents, '应收金额', 0) !== total - mine) fail('应收金额不一致')
  return {
    total_cents: total, participant_count: count, include_self: includeSelf, shares,
    self_share_cents: mine, receivable_cents: total - mine, simulated: true, notice: str(o.notice, '提示')
  }
}

export function parseAARequest(v: unknown): AARequest {
  const o = obj(v, 'AA 记录')
  const p = parseAAPreview(o)
  const status = o.status === 'ACTIVE' || o.status === 'CANCELLED' ? o.status : fail('状态无效')
  return {
    ...p, id: nonEmpty(o.id, '记录编号'), status, version: int(o.version, '版本', 1),
    created_at: date(o.created_at, '创建时间'), updated_at: date(o.updated_at, '更新时间'),
    replayed: bool(o.replayed, '重放标记')
  }
}

function checkInput(total: unknown, ids: unknown, self: unknown) {
  int(total, '总金额', 1, 100000000)
  bool(self, '是否包含本人')
  if (!Array.isArray(ids) || ids.some(x => typeof x !== 'string' || x.length === 0 || x.length > 64 || x === 'self')) fail('参与人列表无效')
  const list = ids as string[]
  if (new Set(list).size !== list.length) fail('参与人重复')
  const count = list.length + (self ? 1 : 0)
  if (list.length < 1 || count < 2 || count > 20) fail('人数需在 2 到 20 之间')
  return { total_cents: total as number, participant_ids: [...list], include_self: self as boolean }
}
const checkKey = (k: unknown): string => typeof k === 'string' && KEY.test(k) ? k : fail('请求密钥无效')
const checkId = (id: unknown): string => nonEmpty(id, '记录编号')
const simulatedList = (v: unknown, key: string): unknown[] => {
  const o = obj(v, '列表')
  if (o.simulated !== true) fail('必须为模拟记录')
  return Array.isArray(o[key]) ? o[key] as unknown[] : fail('列表格式无效')
}

export function createAAClient(api: Api) {
  return {
    async load(): Promise<{ participants: AAParticipant[]; requests: AARequest[] }> {
      const [r, p] = await Promise.all([api('/aa'), api('/aa/participants')])
      const participants = simulatedList(p, 'participants').map(parseAAParticipant)
      const requests = simulatedList(r, 'requests').map(parseAARequest)
      if (new Set(participants.map(x => x.id)).size !== participants.length) fail('参与人重复')
      if (new Set(requests.map(x => x.id)).size !== requests.length) fail('记录重复')
      return { participants, requests }
    },
    async preview(total: number, ids: string[], self: boolean): Promise<AAPreview> {
      return parseAAPreview(await api('/aa/preview', checkInput(total, ids, self)))
    },
    async create(total: number, ids: string[], self: boolean, key: string, confirmed: boolean): Promise<AARequest> {
      const body = { ...checkInput(total, ids, self), request_key: checkKey(key) }
      if (confirmed !== true) fail('需要明确确认后才能创建')
      return parseAARequest(await api('/aa', { ...body, confirmed: true }))
    },
    async cancel(id: string, version: number, key: string): Promise<AARequest> {
      const body = { expected_version: int(version, '版本', 1), request_key: checkKey(key) }
      return parseAARequest(await api('/aa/' + encodeURIComponent(checkId(id)) + '/cancel', body))
    },
    async get(id: string): Promise<AARequest> {
      return parseAARequest(await api('/aa/' + encodeURIComponent(checkId(id))))
    }
  }
}
