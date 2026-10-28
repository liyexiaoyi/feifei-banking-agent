import { FileWarning, RefreshCw } from 'lucide-react'

export type BillMissingProps = {
  period?: string
  busy: boolean
  onRetry: () => void
}

export function BillMissing({ period, busy, onRetry }: BillMissingProps) {
  return (
    <div className="bill-missing" role="status" aria-live="polite">
      <FileWarning className="bill-missing-icon" aria-hidden="true" size={20} />
      <p className="bill-missing-text">
        {period ? `${period}的账单` : '账单'}暂时不可用，可以稍后重试或先核对已有回执。
        这不代表本期没有支出：您的账户流水与历史回执仍然可以正常核对，未完成的操作不会被自动重试。
      </p>
      <button
        type="button"
        className="bill-missing-retry"
        style={{ minHeight: 44, minWidth: 44 }}
        disabled={busy}
        onClick={() => onRetry()}
      >
        <RefreshCw size={16} aria-hidden="true" />
        重试
      </button>
    </div>
  )
}

export default BillMissing
