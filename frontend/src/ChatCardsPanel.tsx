import type { BankCard } from './cards';
import './cards.css';

type ChatCardsPanelProps = {
  cards: BankCard[] | null;
  loading: boolean;
  error: string;
  onRefresh: () => void;
};

function statusLabel(status: string): string {
  if (status === 'ACTIVE') return '可用';
  if (status === 'LOST') return '已挂失';
  return '待核对';
}

export default function ChatCardsPanel({
  cards,
  loading,
  error,
  onRefresh,
}: ChatCardsPanelProps) {
  return (
    <section className="bank-cards" aria-label="当前卡片清单">
      <header className="bank-cards__header">
        <h2 className="bank-cards__title">模拟银行卡</h2>
        <button
          className="bank-cards__refresh"
          type="button"
          disabled={loading}
          onClick={onRefresh}
        >
          {loading ? '正在刷新…' : '刷新卡片'}
        </button>
      </header>
      <p className="bank-cards__notice">
        仅展示当前模拟卡片信息；刷新仅查询状态，不办理挂失。
      </p>
      <div aria-live="polite" aria-busy={loading}>
        {error ? (
          <p className="bank-cards__error" role="alert">{error}</p>
        ) : loading ? (
          <p className="bank-cards__message">正在查询当前卡片…</p>
        ) : cards === null ? (
          <p className="bank-cards__message">尚未查询卡片，请点击“刷新卡片”。</p>
        ) : cards.length === 0 ? (
          <p className="bank-cards__message">当前没有卡片。</p>
        ) : (
          <ul className="bank-cards__list">
            {cards.map((card, index) => (
              <li className="bank-cards__card" key={index}>
                <span className="bank-cards__name">{card.name}</span>
                <span className="bank-cards__number">{card.masked_number}</span>
                <span className="bank-cards__state">{statusLabel(card.status)}</span>
                <span className="bank-cards__badge">模拟</span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <p className="bank-cards__notice">
        请在输入框说明卡片名称或末四位。
      </p>
    </section>
  );
}
