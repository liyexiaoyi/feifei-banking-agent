import type { ToolHealth, ToolGroup } from './recovery'
import { AlertCircle, RefreshCw, LifeBuoy, Wrench } from 'lucide-react'
import './recovery-banner.css'

export type RecoveryBannerProps = {
  tools: ToolHealth[]
  busy: boolean
  onProbe: (group: ToolGroup) => void
  onHandoff: (group: ToolGroup) => void
  onRefresh?: (group: ToolGroup) => void
}

const GROUP_LABEL: Record<ToolGroup, string> = {
  planner: '智能规划',
  bill: '账单查询',
  transfer: '转账',
  subscriptions: '订阅管理',
  cards: '卡片管理',
  wealth: '模拟理财',
  codegen: 'AI计算',
}

export function RecoveryBanner({ tools, busy, onProbe, onHandoff, onRefresh }: RecoveryBannerProps) {
  const affected = tools.filter((t) => t.state !== 'READY')
  if (affected.length === 0) return null

  return (
    <div className="recovery-banner" role="status" aria-live="polite">
      {affected.map((t) => {
        const name = GROUP_LABEL[t.group]
        if (t.state === 'PAUSED') {
          const canTry = t.probe_available && !busy && !t.needs_handoff
          const coolingDown = !t.probe_available
          const canRefresh = coolingDown && !!onRefresh && !busy
          return (
            <div key={t.group} className="recovery-item">
              <div className="recovery-text">
                <Wrench className="recovery-icon" aria-hidden="true" size={18} />
                <p>
                  <strong>{name}</strong>暂时出了点技术问题，已暂时停止该功能的调用。
                  {t.probe_available
                    ? '现在可以尝试恢复，本次恢复不会自动重试业务。'
                    : `上次核对时尚需约 ${Math.max(1, Math.ceil(t.retry_after_seconds))} 秒，可稍后重新核对状态。`}
                </p>
              </div>
              {t.probe_available && (
                <button
                  type="button"
                  className="recovery-btn"
                  disabled={!canTry}
                  onClick={() => onProbe(t.group)}
                >
                  <RefreshCw size={16} aria-hidden="true" />
                  尝试恢复
                </button>
              )}
              {coolingDown && onRefresh && (
                <button
                  type="button"
                  className="recovery-btn"
                  disabled={!canRefresh}
                  onClick={() => onRefresh(t.group)}
                >
                  <RefreshCw size={16} aria-hidden="true" />
                  重新核对状态
                </button>
              )}
            </div>
          )
        }
        if (t.state === 'PROBING') {
          return (
            <div key={t.group} className="recovery-item">
              <div className="recovery-text">
                <Wrench className="recovery-icon" aria-hidden="true" size={18} />
                <p>
                  <strong>{name}</strong>正在检查恢复情况，同一时间只检查一次，请稍候。
                </p>
              </div>
              <button type="button" className="recovery-btn" disabled>
                <RefreshCw size={16} aria-hidden="true" className="recovery-spin" />
                检查中
              </button>
            </div>
          )
        }
        return (
          <div key={t.group} className="recovery-item recovery-item--check">
            <div className="recovery-text">
              <AlertCircle className="recovery-icon" aria-hidden="true" size={18} />
              <p>
                <strong>{name}</strong>原调用结果尚需核查，当前不能继续调用。您的历史记录和回执仍可正常查看，未执行的操作可以取消。
              </p>
            </div>
            <button
              type="button"
              className="recovery-btn recovery-btn--handoff"
              disabled={busy}
              onClick={() => onHandoff(t.group)}
            >
              <LifeBuoy size={16} aria-hidden="true" />
              查看核查记录
            </button>
          </div>
        )
      })}
    </div>
  )
}

export default RecoveryBanner
