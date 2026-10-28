import './DeploymentGate.css';

type DeploymentGateProps = {
  checking: boolean;
  onRetry: () => void;
};

export default function DeploymentGate({ checking, onRetry }: DeploymentGateProps) {
  return (
    <div className="dg-wrap" role="alert" aria-busy={checking}>
      <div className="dg-card">
        <div className="dg-mark" aria-hidden="true">
          <span className="dg-mark-dot" />
        </div>
        <h1 className="dg-title">{checking ? '正在核对本机版本' : '页面与服务暂未匹配'}</h1>
        <p className="dg-subtitle">菲菲 · 本机部署核对</p>
        <p className="dg-desc">暂停操作，先检查构建与本机服务版本。</p>
        <p className="dg-desc dg-desc-warn">
          不会自动重发付款；如之前有处理中操作，版本恢复后先查回执。
        </p>
        <button
          type="button"
          className="dg-retry"
          onClick={onRetry}
          disabled={checking}
        >
          {checking ? '正在重新检查…' : '重新检查'}
        </button>
        <p className="dg-hint">草稿仅保留在当前页面，请先不要关闭</p>
      </div>
    </div>
  );
}
