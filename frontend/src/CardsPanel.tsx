import type { BankCard, CardAction } from './cards';
import './cards.css';

export type Props = {
  cards: BankCard[] | null;
  loading: boolean;
  busy: boolean;
  locked?: boolean;
  error: string;
  onRefresh: () => void;
  onPrepare: (card: BankCard, action: CardAction) => void;
};

export default function CardsPanel({
  cards, loading, busy, locked=false, error, onRefresh, onPrepare,
}: Props) {
  const disabled = loading || busy;

  return (
    <section className="bank-cards" aria-label="模拟银行卡" aria-busy={disabled}>
      <header className="bank-cards__header">
        <div>
          <p className="bank-cards__eyebrow">模拟银行 · 演示卡服务</p>
          <h2 className="bank-cards__title">我的演示卡</h2>
        </div>
        <button
          className="bank-cards__refresh"
          type="button"
          disabled={disabled}
          onClick={onRefresh}
        >
          刷新卡片
        </button>
      </header>

      <p className="bank-cards__notice">
        全部卡片及操作均为模拟，不涉及真实银行卡。点击准备按钮时尚未执行挂失或解挂，
        需后续明确确认及模拟验证后才会执行。模拟挂失不退款、不改变余额。
      </p>
      {busy && <p className="bank-cards__message" role="status">正在处理，请稍候…</p>}

      {error ? (
        <p className="bank-cards__error" role="alert">卡片核对失败：{error}。请刷新后重试。</p>
      ) : loading ? (
        <p className="bank-cards__message" role="status">正在核对卡片信息…</p>
      ) : cards === null ? (
        <p className="bank-cards__message" role="status">尚未获取卡片信息，请刷新核对。</p>
      ) : cards.length === 0 ? (
        <p className="bank-cards__message" role="status">暂无演示卡。</p>
      ) : (
        <ul className="bank-cards__list">
          {cards.map((card) => {
            const active = card.status === 'ACTIVE';
            const lost = card.status === 'LOST';
            const action: CardAction = active ? 'report_lost' : 'restore';
            const label = active ? '准备挂失' : '准备解挂';
            return (
              <li className="bank-cards__card" key={card.id}>
                <div className="bank-cards__card-heading">
                  <h3 className="bank-cards__name">{card.name}</h3>
                  <span className="bank-cards__badge">模拟卡</span>
                </div>
                <p className="bank-cards__number">卡号（掩码）：{card.masked_number}</p>
                <p className="bank-cards__state">
                  状态：<strong>{active ? '可用' : lost ? '已挂失' : '状态未知，请刷新核对'}</strong>
                </p>
                {(active || lost) && <button
                  className="bank-cards__action"
                  type="button"
                  disabled={disabled || locked}
                  aria-label={`${label}：${card.name}，${card.masked_number}`}
                  onClick={() => onPrepare(card, action)}
                >
                  {label}
                </button>}
                <p className="bank-cards__hint">仅发起准备，后续仍需确认及模拟验证。</p>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
