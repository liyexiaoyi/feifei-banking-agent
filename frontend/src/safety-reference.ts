// frontend/src/safety-reference.ts
// 待审草稿：仅解析数据，通过调用方注入的 request 加载。

const CODES = [
  'LOAN_ADVANCE_FEE',
  'REFUND_ADVANCE_PAYMENT',
  'FAKE_CANCELLATION_PAYMENT',
  'REMOTE_ACCESS',
  'TASK_RECHARGE',
  'SAFE_ACCOUNT',
  'SECRET_DISCLOSURE',
  'CREDIT_REPAIR_PAYMENT',
  'INVESTMENT_QUALIFICATION_PAYMENT',
  'REGULATORY_ACCOUNT_TRANSFER',
] as const;

type SafetyReferenceCode = (typeof CODES)[number];

export type SafetyReferenceCase = {
  id: string;
  title: string;
  publisher: string;
  url: string;
  archived_date: string;
};

export type SafetyReferenceItem = {
  code: SafetyReferenceCode;
  title: string;
  why: string;
  checks: string[];
  reference_kind: 'case_pattern' | 'rule_explanation';
  cases: SafetyReferenceCase[];
};

export type SafetyReferenceSnapshot = {
  schema_version: '1';
  reference_only: true;
  items: SafetyReferenceItem[];
};

const KNOWN_CODES: ReadonlySet<string> = new Set(CODES);
const ALLOWED_HOSTS: ReadonlySet<string> = new Set([
  'www.bypc.gov.cn',
  'www.huadu.gov.cn',
  'nxca.miit.gov.cn',
  'gaj.wuhan.gov.cn',
  'www.jiyuan.gov.cn',
  'gaj.pds.gov.cn',
]);

// 只接收具有精确字段集合的数据对象；拒绝访问器与额外字段。
// 读取描述符中的值，避免调用输入对象的 getter。
function exactRecord(
  value: unknown,
  fields: readonly string[],
): Record<string, unknown> | null {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    return null;
  }
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) return null;
  const keys = Reflect.ownKeys(value);
  if (keys.length !== fields.length) return null;
  if (keys.some(key => typeof key !== 'string' || !fields.includes(key))) {
    return null;
  }
  const result: Record<string, unknown> = Object.create(null);
  for (const field of fields) {
    const descriptor = Object.getOwnPropertyDescriptor(value, field);
    if (!descriptor || !descriptor.enumerable || !('value' in descriptor)) {
      return null;
    }
    result[field] = descriptor.value;
  }
  return result;
}

// 拒绝稀疏数组、额外属性和访问器元素，并生成新的数组。
function boundedArray(value: unknown, min: number, max: number): unknown[] | null {
  if (!Array.isArray(value)) return null;
  const lengthDescriptor = Object.getOwnPropertyDescriptor(value, 'length');
  if (!lengthDescriptor || !('value' in lengthDescriptor)) return null;
  const length: unknown = lengthDescriptor.value;
  if (typeof length !== 'number' || !Number.isInteger(length)) return null;
  if (length < min || length > max) return null;
  if (Reflect.ownKeys(value).length !== length + 1) return null;
  const result: unknown[] = [];
  for (let index = 0; index < length; index += 1) {
    const descriptor = Object.getOwnPropertyDescriptor(value, String(index));
    if (!descriptor || !descriptor.enumerable || !('value' in descriptor)) {
      return null;
    }
    result.push(descriptor.value);
  }
  return result;
}

function isText(value: unknown, maxLength: number): value is string {
  return typeof value === 'string'
    && value.length <= maxLength
    && value.trim().length > 0;
}

function isCode(value: unknown): value is SafetyReferenceCode {
  return typeof value === 'string' && KNOWN_CODES.has(value);
}

function isValidDate(value: unknown): value is string {
  if (typeof value !== 'string' || value.length !== 10) return false;
  if (!/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(value)) return false;
  const year = Number(value.slice(0, 4));
  const month = Number(value.slice(5, 7));
  const day = Number(value.slice(8, 10));
  if (year < 1 || month < 1 || month > 12 || day < 1) return false;
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  const limit = days[month - 1];
  return limit !== undefined && day <= limit;
}

function isSourceUrl(value: unknown): value is string {
  if (typeof value !== 'string' || !/^https:\/\//i.test(value)) return false;
  // 拒绝 URL 解析器可能忽略或归一化的原始空白、控制字符和反斜杠。
  if (/[\s\u0000-\u001f\u007f\\]/u.test(value)) return false;
  // 同时拒绝空 query/hash 分隔符，例如 /case? 或 /case#。
  if (value.includes('?') || value.includes('#')) return false;
  const authority = value.slice(8).split('/')[0];
  // 包括空 userinfo，例如 https://@www.huadu.gov.cn/case。
  if (!authority || authority.includes('@')) return false;
  try {
    const parsed = new URL(value);
    return parsed.protocol === 'https:'
      && parsed.port === ''
      && parsed.username === ''
      && parsed.password === ''
      && parsed.search === ''
      && parsed.hash === ''
      && ALLOWED_HOSTS.has(parsed.hostname);
  } catch {
    return false;
  }
}

function parseCase(value: unknown): SafetyReferenceCase | null {
  const record = exactRecord(value, ['id', 'title', 'publisher', 'url', 'archived_date']);
  if (!record) return null;
  const { id, title, publisher, url, archived_date } = record;
  if (typeof id !== 'string' || id.length !== 3 || !/^C[0-9]{2}$/.test(id)) {
    return null;
  }
  if (!isText(title, 200) || !isText(publisher, 200)) return null;
  if (!isSourceUrl(url) || !isValidDate(archived_date)) return null;
  return { id, title, publisher, url, archived_date };
}

function parseItem(value: unknown): SafetyReferenceItem | null {
  const record = exactRecord(value, [
    'code', 'title', 'why', 'checks', 'reference_kind', 'cases',
  ]);
  if (!record) return null;
  const { code, title, why, reference_kind } = record;
  if (!isCode(code) || !isText(title, 500) || !isText(why, 500)) return null;
  if (reference_kind !== 'case_pattern' && reference_kind !== 'rule_explanation') {
    return null;
  }
  const rawChecks = boundedArray(record.checks, 1, 6);
  const rawCases = boundedArray(record.cases, 0, 4);
  if (rawChecks === null || rawCases === null) return null;
  if (reference_kind === 'rule_explanation' && rawCases.length !== 0) return null;
  if (reference_kind === 'case_pattern' && rawCases.length === 0) return null;

  const checks: string[] = [];
  for (const check of rawChecks) {
    if (!isText(check, 300)) return null;
    checks.push(check);
  }
  const cases: SafetyReferenceCase[] = [];
  for (const rawCase of rawCases) {
    const parsed = parseCase(rawCase);
    if (parsed === null) return null;
    cases.push(parsed);
  }
  return { code, title, why, checks, reference_kind, cases };
}

export function parseSafetyReferences(value: unknown): SafetyReferenceSnapshot | null {
  try {
    const record = exactRecord(value, ['schema_version', 'reference_only', 'items']);
    if (!record || record.schema_version !== '1' || record.reference_only !== true) {
      return null;
    }
    const rawItems = boundedArray(record.items, CODES.length, CODES.length);
    if (rawItems === null) return null;
    const seen = new Set<SafetyReferenceCode>();
    const items: SafetyReferenceItem[] = [];
    for (const rawItem of rawItems) {
      const item = parseItem(rawItem);
      if (item === null || seen.has(item.code)) return null;
      seen.add(item.code);
      items.push(item);
    }
    if (seen.size !== CODES.length) return null;
    return { schema_version: '1', reference_only: true, items };
  } catch {
    // 包括异常对象、失效 Proxy 等引起的读取错误。
    return null;
  }
}

export async function loadSafetyReferences(
  request: (path: string) => Promise<unknown>,
  timeoutMs: number = 1500,
): Promise<SafetyReferenceSnapshot | null> {
  if (!Number.isFinite(timeoutMs)
    || !Number.isInteger(timeoutMs)
    || timeoutMs < 1
    || timeoutMs > 3000) {
    return null;
  }

  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const timeout = new Promise<null>(resolve => {
      timer = setTimeout(() => resolve(null), timeoutMs);
    });
    const value = await Promise.race([
      request('/safety/references'),
      timeout,
    ]);
    return parseSafetyReferences(value);
  } catch {
    return null;
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}
