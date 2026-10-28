import type { Operation, Account } from "./api";

type JsonObject = Record<string, unknown>;
type CleanResult = {
  card_id?: string;
  account_id?: string;
  status?: string;
  version?: number;
  masked_number?: string;
  message?: string;
  simulated?: boolean;
};

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isText(value: unknown, max = Infinity): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= max;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function parseResult(value: unknown): CleanResult | null | undefined {
  if (value === null) return null;
  if (!isObject(value)) return undefined;
  const result: CleanResult = {};
  const stringKeys = [
    "card_id", "account_id", "status", "masked_number", "message",
  ] as const;
  for (const key of stringKeys) {
    if (!Object.prototype.hasOwnProperty.call(value, key)) continue;
    const field = value[key];
    if (typeof field !== "string") return undefined;
    result[key] = field;
  }
  if (Object.prototype.hasOwnProperty.call(value, "version")) {
    if (!isFiniteNumber(value.version)) return undefined;
    result.version = value.version;
  }
  if (Object.prototype.hasOwnProperty.call(value, "simulated")) {
    if (typeof value.simulated !== "boolean") return undefined;
    result.simulated = value.simulated;
  }
  return result;
}

export function parseVirtualApplications(value: unknown): Operation[] | null {
  if (!Array.isArray(value) || value.length > 1000) return null;
  const operations: Operation[] = [];
  const ids = new Set<string>();
  for (const item of value) {
    if (!isObject(item) || !isText(item.id) || ids.has(item.id)) return null;
    if (item.type !== "virtual_card_create" || item.risk !== "YELLOW") return null;
    if (!isText(item.status, 60)) return null;
    if (!isFiniteNumber(item.created_at) || !isFiniteNumber(item.expires_at)) return null;
    const payload = item.payload;
    if (!isObject(payload) || payload.simulated !== true) return null;
    if (!isText(payload.account_id, 300) || !isText(payload.account_name, 300) ||
        !isText(payload.card_name, 300)) return null;
    const result = parseResult(item.result);
    if (result === undefined) return null;
    ids.add(item.id);
    operations.push({
      id: item.id,
      type: "virtual_card_create",
      risk: "YELLOW",
      status: item.status,
      created_at: item.created_at,
      expires_at: item.expires_at,
      payload: {
        account_id: payload.account_id,
        account_name: payload.account_name,
        card_name: payload.card_name,
        simulated: true,
      },
      result,
    } as Operation);
  }
  return operations;
}

export function findVirtualIntent(ops: Operation[] | null, account: Account): Operation | null {
  if (ops === null) return null;
  for (const op of ops) {
    if (op.type !== "virtual_card_create") continue;
    if (!isObject(op.payload) || op.payload.account_id !== account.id) continue;
    if (op.status === "CANCELLED" || op.status === "EXPIRED" || op.status === "FAILED") continue;
    return op;
  }
  return null;
}

/** Current server applications override the truncated workspace snapshot. */
export function mergeVirtualOperations(snapshot:Operation[], applications:Operation[]|null):Operation[]{
 if(applications===null)return snapshot
 const current=new Map(applications.map(op=>[op.id,op]))
 const merged=snapshot.map(op=>current.get(op.id)||op)
 const seen=new Set(snapshot.map(op=>op.id))
 return [...merged,...applications.filter(op=>!seen.has(op.id))]
}
