import * as React from 'react';
import type { Account, Operation } from './api';
import type { BankCard } from './cards';
import './cards.css';

export type VirtualCardsPanelProps = {
  accounts: Account[] | null;
  cards: BankCard[] | null;
  applications: Operation[] | null;
  loading: boolean;
  busy: boolean;
  locked?: boolean;
  error: string;
  onPrepare: (account: Account) => void;
  onReceipt: (id: string) => void;
  onRefresh: () => void;
};

const virtualTypes = new Set([
  'virtual_card_create',
]);
const inactiveStatuses = new Set(['CANCELLED', 'EXPIRED', 'FAILED']);
const statusLabels: Record<string, string> = {
  WAITING_CONFIRMATION: '等待确认', WAITING_VERIFICATION: '等待验证', READY: '等待办理',
  UNKNOWN: '结果待核实', EXECUTING: '办理结果待核实', MANUAL_REVIEW: '待本地核对', SUCCEEDED: '已完成',
};
const buttonStyle: React.CSSProperties = { minHeight: 48 };

export default function VirtualCardsPanel({
  accounts, cards, applications, loading, busy, locked=false, error,
  onPrepare, onReceipt, onRefresh,
}: VirtualCardsPanelProps): React.ReactElement {
  let content: React.ReactNode;
  if (error) {
    content = <p role="alert">当前数据不可用：{error}</p>;
  } else if (loading) {
    content = <p role="status">正在加载当前账户、卡片及申请…</p>;
  } else if (accounts === null || cards === null || applications === null) {
    const missing = [
      accounts === null && '账户', cards === null && '卡片',
      applications === null && '申请',
    ].filter(Boolean).join('、');
    content = <p role="status">{missing}数据不可用，请刷新后重试。</p>;
  } else if (accounts.length === 0) {
    content = <p role="status">暂无账户。</p>;
  } else {
    content = accounts.map((account) => {
      const exists = cards.some((card) =>
        card.id.startsWith('vcard_') && card.account_id === account.id);
      const application = applications.find((item) =>
        virtualTypes.has(String(item.type)) && item.payload.account_id === account.id &&
        !inactiveStatuses.has(String(item.status)));
      const status = application ? String(application.status) : '';
      const label = Object.prototype.hasOwnProperty.call(statusLabels, status)
        ? statusLabels[status] : '结果待核实';
      return (
        <article className="bank-cards__card" key={account.id}>
          <h3 className="bank-cards__name">{account.name}</h3>
          <p className="bank-cards__number">{account.masked_number}</p>
          {exists ? <p>已有虚拟演示卡</p> : application ? (
            <div>
              <p>已有申请：{label}</p>
              <button className="bank-cards__refresh" type="button" style={buttonStyle} disabled={busy}
                onClick={() => onReceipt(application.id)}>核对已有申请</button>
            </div>
          ) : (
            <button className="bank-cards__action" type="button" style={buttonStyle} disabled={busy || locked}
              onClick={() => onPrepare(account)}>准备申请虚拟卡</button>
          )}
        </article>
      );
    });
  }
  return (
    <section className="bank-cards" aria-label="申请虚拟演示卡" aria-busy={loading || busy}>
      <header className="bank-cards__header">
        <h2 className="bank-cards__title">申请虚拟演示卡</h2>
        <button className="bank-cards__refresh" type="button" style={buttonStyle} disabled={busy || loading}
          onClick={() => onRefresh()}>刷新</button>
      </header>
      <p className="bank-cards__notice">本机模拟，每账户1张(本地演示限制)，不生成可支付真实卡、不改余额，准备后需独立确认。</p>
      <div className="bank-cards__list">{content}</div>
    </section>
  );
}
