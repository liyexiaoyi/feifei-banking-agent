import type { Product, Position } from './wealth-types.ts';

type ParsedWealthData = {
  products: Product[];
  positions: Position[];
  simulated: true;
  valuation_note: string;
};
const record = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v);
const text = (v: unknown): v is string =>
  typeof v === 'string' && v.length >= 1 && v.length <= 300;
const integer = (v: unknown): v is number =>
  typeof v === 'number' && Number.isSafeInteger(v);
const nonnegative = (v: unknown): v is number => integer(v) && v >= 0;
const positive = (v: unknown): v is number => integer(v) && v > 0;

export function parseWealthData(value: unknown): ParsedWealthData | null {
  try {
    if (!record(value)) return null;
    const { products: rawProducts, positions: rawPositions, simulated, valuation_note } = value;
    if (simulated !== true || typeof valuation_note !== 'string' ||
        valuation_note.length < 1 || valuation_note.length > 500 ||
        !Array.isArray(rawProducts) || rawProducts.length > 1000 ||
        !Array.isArray(rawPositions) || rawPositions.length > 1000) return null;
    const products: Product[] = [];
    const positions: Position[] = [];
    const byId = new Map<string, Product>();
    const seen = new Map<string, Set<string>>();
    for (const raw of rawProducts) {
      if (!record(raw)) return null;
      const { id, name, nav_cents, quote_version, simulated: flag } = raw;
      if (!text(id) || !text(name) || !positive(nav_cents) ||
          !positive(quote_version) || flag !== true || byId.has(id)) return null;
      const product: Product = { id, name, nav_cents, quote_version, simulated: true };
      products.push(product);
      byId.set(id, product);
    }
    for (const raw of rawPositions) {
      if (!record(raw)) return null;
      const { account_id, account_name, product_id, product_name, units_1e4,
        cost_cents, version, value_cents, gain_cents, reserved_units_1e4,
        available_units_1e4, simulated: flag } = raw;
      if (!text(account_id) || !text(account_name) || !text(product_id) ||
          !text(product_name) || !nonnegative(units_1e4) ||
          !nonnegative(cost_cents) || !positive(version) ||
          !nonnegative(value_cents) || !integer(gain_cents) ||
          !nonnegative(reserved_units_1e4) || !nonnegative(available_units_1e4) ||
          flag !== true) return null;
      const product = byId.get(product_id);
      if (!product || product.name !== product_name ||
          seen.get(account_id)?.has(product_id) ||
          available_units_1e4 !== units_1e4 - reserved_units_1e4 ||
          gain_cents !== value_cents - cost_cents ||
          BigInt(value_cents) !== BigInt(units_1e4) * BigInt(product.nav_cents) / BigInt(10000)) return null;
      let accountProducts = seen.get(account_id);
      if (!accountProducts) {
        accountProducts = new Set<string>();
        seen.set(account_id, accountProducts);
      }
      accountProducts.add(product_id);
      positions.push({ account_id, account_name, product_id, product_name,
        units_1e4, cost_cents, version, value_cents, gain_cents,
        reserved_units_1e4, available_units_1e4, simulated: true });
    }
    return { products, positions, simulated: true, valuation_note };
  } catch {
    return null;
  }
}
