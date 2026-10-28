import type { CardOperation } from './card-control-client';

// Pure presentational summary. Only whitelisted fields are read; never dumps payload.
type Rec = Record<string, unknown>;
const rec = (v: unknown): Rec | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Rec) : null);
const str = (v: unknown): string => (typeof v === 'string' && v.length <= 80 ? v : '');
const cents = (v: unknown): number | null => (typeof v === 'number' && Number.isSafeInteger(v) && v >= 0 ? v : null);
function yuan(v: unknown): string {
  const c = cents(v);
  if (c === null) return '未知';
  const whole = Math.floor(c / 100).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return `${whole}.${String(c % 100).padStart(2, '0')}元`;
}
const lockText = (v: unknown): string => (v === true ? '已锁定' : v === false ? '未锁定' : '未知');
const ACTIONS: Record<string, string> = {
  lock: '锁定交易',
  unlock: '解锁交易',
  set_single_limit: '调整单笔限额',
  set_daily_limit: '调整每日限额',
};

function Receipt() {
  return <div className="operation-details"><p>无法核对这项操作，请查看原回执。</p></div>;
}

export default function CardOperationSummary({ op }: { op: CardOperation | null }) {
  const p = op ? rec((op as { payload?: unknown }).payload) : null;
  if (!op || !p) return <Receipt />;
  const card = `${str(p.card_name) || '演示卡'}（${str(p.masked_number) || '卡号未知'}）`;
  switch (op.type) {
    case 'card_spend':
      return (
        <div className="operation-details">
          <p>演示商户：{str(p.merchant_name) || '未知'}</p>
          <p>卡片：{card}</p>
          <p>金额：{yuan(p.amount_cents)}</p>
          <p>这是本地模拟消费，只在演示环境记录，不会产生真实扣款。</p>
        </div>
      );
    case 'card_controls_update': {
      const b = rec(p.before), a = rec(p.after), act = ACTIONS[str(p.action)];
      if (!b || !a || !act) return <Receipt />;
      return (
        <div className="operation-details">
          <p>卡片：{card}</p>
          <p>要做的事：{act}</p>
          <p>交易锁：{lockText(b.locked)} → {lockText(a.locked)}</p>
          <p>单笔限额：{yuan(b.single_limit_cents)} → {yuan(a.single_limit_cents)}</p>
          <p>每日限额：{yuan(b.daily_limit_cents)} → {yuan(a.daily_limit_cents)}</p>
          <p>这是模拟设置，只影响演示环境。</p>
        </div>
      );
    }
    case 'card_credential_change':
      return (
        <div className="operation-details">
          <p>卡片：{card}</p>
          <p>要做的事：设置或修改演示口令。</p>
          <p>演示口令只用于本模拟环境，不是真实银行卡密码。</p>
        </div>
      );
    default:
      return <Receipt />;
  }
}
