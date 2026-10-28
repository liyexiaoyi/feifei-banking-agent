import './IntegrationStatusCard.css';
import type { IntegrationSnapshot } from './integrations';

export interface IntegrationStatusCardProps {
  snapshot: IntegrationSnapshot | null;
  loading: boolean;
}

interface CellCopy {
  id: string;
  title: string;
  simulatedText: string;
}

const CELLS: readonly CellCopy[] = [
  { id: 'bank', title: '模拟银行', simulatedText: '模拟演示中' },
  { id: 'identity', title: '模拟验证', simulatedText: '模拟演示中' },
  { id: 'human', title: '真人客服', simulatedText: '模拟演示中' },
];

const TEXT_LOADING = '核对中';
const TEXT_UNKNOWN = '状态未知';
const TEXT_NOT_CONNECTED = '未接入';

function resolveCellText(
  cell: CellCopy,
  snapshot: IntegrationSnapshot | null,
  loading: boolean,
): string {
  if (loading) {
    return TEXT_LOADING;
  }
  if (snapshot === null) {
    return TEXT_UNKNOWN;
  }
  const capability = snapshot.capabilities.find((item) => item.id === cell.id);
  if (!capability) {
    return TEXT_UNKNOWN;
  }
  if (capability.status === 'simulated') {
    return cell.simulatedText;
  }
  if (capability.status === 'not_connected') {
    return TEXT_NOT_CONNECTED;
  }
  return TEXT_UNKNOWN;
}

export default function IntegrationStatusCard({
  snapshot,
  loading,
}: IntegrationStatusCardProps) {
  return (
    <section
      className="integration-status-card"
      role="status"
      aria-label="集成状态说明"
    >
      <h2 className="integration-status-card__heading">集成状态说明</h2>
      <ul className="integration-status-card__grid">
        {CELLS.map((cell) => (
          <li key={cell.id} className="integration-status-card__cell">
            <span className="integration-status-card__cell-title">
              {cell.title}
            </span>
            <span className="integration-status-card__cell-state">
              {resolveCellText(cell, snapshot, loading)}
            </span>
          </li>
        ))}
      </ul>
      <p className="integration-status-card__note">
        以上仅为状态说明，不构成任何授权，也不代表已开通真实服务。
      </p>
      {!loading && snapshot === null && (
        <details className="integration-status-card__details">
          <summary className="integration-status-card__details-summary">
            状态显示未知时该怎么办
          </summary>
          <p className="integration-status-card__details-body">
            当前无法确认集成状态。请先通过官方渠道核对实际情况，再继续使用本页面的其他功能。
          </p>
        </details>
      )}
    </section>
  );
}
