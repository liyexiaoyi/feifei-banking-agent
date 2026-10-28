// 渠道收件箱客户端：只读 GET，经注入的 api，无重试、无存储、无直接网络。
// 文本为不可信外部内容，只作纯文本，不执行、不授权任何操作。
export type ChannelStatus = { enabled: boolean; protocol: 'FF_LOCAL_DEMO_V1'; simulated: true; official_connected: false }
export type ChannelMessage = {
  id: string; channel_label: '本机演示渠道'; text: string; received_at: string; expires_at: string
  untrusted_text: true; effect_authorized: false; simulated: true
}
export type ChannelInbox = { items: ChannelMessage[]; simulated: true; official_connected: false }
type Api = (path: string, body?: unknown) => Promise<unknown>

const fail = (msg: string): never => { throw new Error(msg) }
const obj = (v: unknown, what: string): Record<string, unknown> =>
  v !== null && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : fail(what + '格式无效')
const lit = <T>(v: unknown, expected: T, what: string): T => v === expected ? expected : fail(what + '标志无效')
const bool = (v: unknown, what: string): boolean => typeof v === 'boolean' ? v : fail(what + '必须为布尔值')
const ID = /^inbox_[0-9a-f]{32}$/
const inboxId = (v: unknown): string => typeof v === 'string' && ID.test(v) ? v : fail('消息编号无效')
const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/
const isoTime = (v: unknown, what: string): number => {
  if (typeof v !== 'string' || !ISO.test(v)) return fail(what + '日期无效')
  const t = Date.parse(v)
  return Number.isNaN(t) ? fail(what + '日期无效') : t
}
const LABEL = '本机演示渠道' as const
const PROTOCOL = 'FF_LOCAL_DEMO_V1' as const
const MAX_TEXT = 2000
const MAX_ITEMS = 50

export function parseChannelStatus(v: unknown): ChannelStatus {
  const o = obj(v, '渠道状态')
  return {
    enabled: bool(o.enabled, '启用状态'),
    protocol: lit(o.protocol, PROTOCOL, '协议'),
    simulated: lit(o.simulated, true as const, '模拟'),
    official_connected: lit(o.official_connected, false as const, '官方连接'),
  }
}

export function parseChannelMessage(v: unknown): ChannelMessage {
  const o = obj(v, '渠道消息')
  const text = typeof o.text === 'string' && o.text.length >= 1 && o.text.length <= MAX_TEXT ? o.text : fail('消息文本无效')
  const r = isoTime(o.received_at, '接收时间')
  const e = isoTime(o.expires_at, '过期时间')
  if (!(e > r)) fail('过期时间必须晚于接收时间')
  return {
    id: inboxId(o.id),
    channel_label: lit(o.channel_label, LABEL, '渠道名称'),
    text,
    received_at: o.received_at as string,
    expires_at: o.expires_at as string,
    untrusted_text: lit(o.untrusted_text, true as const, '不可信文本'),
    effect_authorized: lit(o.effect_authorized, false as const, '执行授权'),
    simulated: lit(o.simulated, true as const, '模拟'),
  }
}

export function parseChannelInbox(v: unknown): ChannelInbox {
  const o = obj(v, '收件箱')
  if (!Array.isArray(o.items)) fail('收件箱列表无效')
  const raw = o.items as unknown[]
  if (raw.length > MAX_ITEMS) fail('收件箱条目过多')
  const seen = new Set<string>()
  const items = raw.map(parseChannelMessage)
  for (const m of items) { if (seen.has(m.id)) fail('消息编号重复'); seen.add(m.id) }
  return {
    items,
    simulated: lit(o.simulated, true as const, '模拟'),
    official_connected: lit(o.official_connected, false as const, '官方连接'),
  }
}

export function createChannelClient(api: Api) {
  return {
    // 仅 GET：只传路径，不传 body。
    async status(): Promise<ChannelStatus> { return parseChannelStatus(await api('/channel/status')) },
    async list(): Promise<ChannelInbox> { return parseChannelInbox(await api('/channel/inbox')) },
    async get(id: string): Promise<ChannelMessage> {
      const safe = inboxId(id) // 请求前校验，防路径注入
      const m = parseChannelMessage(await api('/channel/inbox/' + safe))
      if (m.id !== safe) fail('返回消息编号不匹配')
      return m
    },
  }
}
