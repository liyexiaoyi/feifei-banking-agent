/**
 * TaskPlanView — 固定计划/DAG 展示组件（契约 v2，摘要 v3）
 *
 * 约定：
 * - 只通过 onControl 回调上报用户主动点击；组件内部不 fetch、不设计时器重试。
 * - PREPARED 表示准备完成，尚未付款；界面文案显式说明。
 * - HANDOFF 为本地核查记录，未联系真人。
 * - 不展示 token 与 action 参数；最多 6 个节点，展示依赖、状态、版本、暂停原因。
 * - business 为可选（兼容旧快照）；不从 PREPARED 节点猜测业务是否仍待确认。
 * - DONE 且 business.actionable 为真时只提供取消/本地核查，不提供暂停/继续；
 *   旧数据无 business 时 DONE 不提供任何按钮。
 * - business.attribution_incomplete 为真时（任意状态）提示存在未归属旧操作；
 *   整项取消不会处理这些操作，需用户在操作记录中逐笔核对。
 */
import React from 'react';
import {
  Pause,
  Play,
  HandMetal,
  XCircle,
  Clock,
  Loader2,
  ShieldCheck,
  CheckCircle2,
  Ban,
  AlertTriangle,
  Unlink,
  FileSearch,
} from 'lucide-react';
import type {
  LocalHandoffSummaryProps,
  NodeState,
  PlanStatus,
  TaskPlanNode,
  TaskPlanViewProps,
} from './task-plan';
import './task-plan.css';

const PLAN_STATUS_LABEL: Record<PlanStatus, string> = {
  PLANNING: '规划中',
  RUNNING: '执行中',
  PAUSED: '已暂停',
  DONE: '步骤已处理',
  HANDOFF: '本地核查',
  CANCELLED: '已取消',
};

const NODE_STATE_LABEL: Record<NodeState, string> = {
  PENDING: '等待',
  RUNNING: '执行中',
  PREPARED: '已准备',
  DONE: '完成',
  BLOCKED: '已阻止',
  FAILED: '失败',
  ORPHAN: '中断待核对',
};

const NODE_STATE_ICON: Record<NodeState, React.ReactNode> = {
  PENDING: <Clock size={14} aria-hidden />,
  RUNNING: <Loader2 size={14} className="tp-spin" aria-hidden />,
  PREPARED: <ShieldCheck size={14} aria-hidden />,
  DONE: <CheckCircle2 size={14} aria-hidden />,
  BLOCKED: <Ban size={14} aria-hidden />,
  FAILED: <AlertTriangle size={14} aria-hidden />,
  ORPHAN: <Unlink size={14} aria-hidden />,
};

const TERMINAL: ReadonlySet<PlanStatus> = new Set(['DONE', 'HANDOFF', 'CANCELLED']);

const kindLabel:Record<string,string>={wealth:'查询模拟理财',wealth_trade:'准备模拟理财申请',virtual_card_apply:'准备虚拟卡申请',bill:'查询账单',balance:'查询余额',transfer:'准备转账',subscriptions:'准备订阅查询',cancel_subscription:'准备取消续费',clarify:'补充信息',cards:'查询演示卡',card_status:'准备卡片申请',card_stop:'撤回卡片申请',card_correct:'更正申请卡片'};
const operationLabel:Record<string,string>={wealth_purchase:'模拟申购',wealth_redemption:'模拟赎回',virtual_card_create:'申请虚拟卡',transfer:'转账',subscription_query:'查询订阅',subscription_cancel:'取消续费',card_report_lost:'模拟挂失',card_restore:'模拟解挂'};
const operationStateLabel:Record<string,string>={SUCCEEDED:'已完成',CANCELLED:'已取消',EXPIRED:'已过期',WAITING_CONFIRMATION:'等待确认',WAITING_VERIFICATION:'等待验证',UNKNOWN:'结果待核对',FAILED:'执行失败'};
const reasonLabel:Record<string,string>={TOOL_FAILURE:'相关功能出现技术故障',PLANNING_FAILED:'本次规划未成功',PROCESS_INTERRUPTED:'执行曾中断，原调用结果需核对'};

function NodeRow({ node }: { node: TaskPlanNode }) {
  return (
    <li className={`tp-node tp-node--${node.state.toLowerCase()}`}>
      <div className="tp-node-head">
        <span className={`tp-node-state tp-state--${node.state.toLowerCase()}`}>
          {NODE_STATE_ICON[node.state]}
          <span>{NODE_STATE_LABEL[node.state]}</span>
        </span>
        <span className="tp-node-kind" title={kindLabel[node.kind]||'业务步骤'}>
          第 {node.idx+1} 步 · {kindLabel[node.kind]||'业务步骤'}
        </span>
      </div>
      <div className="tp-node-meta">
        <span>
          依赖：
          {node.deps.length > 0
            ? node.deps.map((d) => `第${d+1}步`).join('、')
            : '无'}
        </span>
        {node.state === 'PREPARED' && (
          <span className="tp-node-note">当时已准备，执行结果请核对回执</span>
        )}
        {node.error_code && (
          <span className="tp-node-error">错误码：{node.error_code}</span>
        )}
      </div>
    </li>
  );
}

export function TaskPlanView({ plan, busy, onControl }: TaskPlanViewProps) {
  const nodes = plan.nodes.slice(0, 6);
  const terminal = TERMINAL.has(plan.status);
  const business = plan.business;
  const canPause = plan.status === 'RUNNING' || plan.status === 'PLANNING';
  const canResume = plan.status === 'PAUSED' && !plan.nodes.some(n=>n.state==='ORPHAN'||n.state==='RUNNING') && (plan.nodes.length>0||plan.reason==='PLANNING_FAILED');
  // DONE 计划只在 business 明确 actionable 时允许取消/本地核查；不提供暂停/继续
  const doneActionable = plan.status === 'DONE' && business?.actionable === true;
  const showHandoffCancel = !terminal || doneActionable;
  const donePendingOps = doneActionable && business ? business.pending_ops : 0;
  const doneAwaitingSelection = doneActionable && business ? business.awaiting_selection : false;
  // 持久迁移问题：存在未能归属到本计划的旧操作，任意状态下都提示，不影响按钮控制
  const attributionIncomplete = business?.attribution_incomplete === true;

  return (
    <section className="tp-plan" aria-label={`任务计划 ${plan.id}`}>
      <header className="tp-plan-head">
        <div className="tp-plan-title">
          <span className={`tp-plan-status tp-plan-status--${plan.status.toLowerCase()}`}>
            {PLAN_STATUS_LABEL[plan.status]}
          </span>
          <span className="tp-plan-id" title={plan.id}>
            计划 {plan.id}
          </span>
        </div>
        <dl className="tp-plan-meta">
          <div>
            <dt>版本</dt>
            <dd>v{plan.version}</dd>
          </div>

        </dl>
      </header>

      {plan.status === 'PAUSED' && plan.reason && (
        <p className="tp-plan-reason" role="status">
          暂停原因：{reasonLabel[plan.reason]||'请核对任务与操作记录'}
        </p>
      )}

      {attributionIncomplete && (
        <p className="tp-plan-reason" role="status">
          部分旧操作未能关联到此任务，请在操作记录中逐笔核对或取消；整项取消不会处理这些操作。
        </p>
      )}

      {doneActionable && (donePendingOps > 0 || doneAwaitingSelection) && (
        <p className="tp-plan-reason" role="status">
          {donePendingOps > 0 && (
            <span>仍有 {donePendingOps} 笔待确认或验证。</span>
          )}
          {doneAwaitingSelection && (
            <span>存在待选收款人。</span>
          )}
        </p>
      )}

      <ol className="tp-node-list">
        {nodes.map((n) => (
          <NodeRow key={n.idx} node={n} />
        ))}
      </ol>

      <div className="tp-actions">
        {canPause && (
          <button
            type="button"
            className="tp-btn"
            disabled={busy}
            onClick={() => onControl('pause')}
          >
            <Pause size={15} aria-hidden />
            暂停
          </button>
        )}
        {canResume && (
          <button
            type="button"
            className="tp-btn tp-btn--primary"
            disabled={busy}
            onClick={() => onControl('resume')}
          >
            <Play size={15} aria-hidden />
            继续
          </button>
        )}
        {showHandoffCancel && (
          <>
            <button
              type="button"
              className="tp-btn"
              disabled={busy}
              onClick={() => onControl('handoff')}
            >
              <HandMetal size={15} aria-hidden />
              本地核查
            </button>
            <button
              type="button"
              className="tp-btn tp-btn--danger"
              disabled={busy}
              onClick={() => onControl('cancel')}
            >
              <XCircle size={15} aria-hidden />
              取消
            </button>
          </>
        )}
      </div>

      <p className="tp-plan-footnote">
        步骤显示当时的处理进度，尚未确认的操作仍需单独确认；当前执行结果以业务回执为准。
      </p>
    </section>
  );
}

export function LocalHandoffSummary({ summary }: LocalHandoffSummaryProps) {
  return (
    <section className="tp-handoff" aria-label="本地核查交接摘要">
      <header className="tp-handoff-head">
        <FileSearch size={16} aria-hidden />
        <div>
          <h3 className="tp-handoff-title">本地核查记录</h3>
          <p className="tp-handoff-sub">
            交接编号 {summary.id} · 计划 {summary.plan_id} ·{' '}
            {new Date(summary.created_at * 1000).toLocaleString()}
          </p>
        </div>
      </header>

      <p className="tp-handoff-banner" role="status">
        本记录为本地核查摘要，未联系真人客服或任何人工机构。
      </p>

      {summary.reason && (
        <p className="tp-handoff-reason">核查原因：{reasonLabel[summary.reason]||'用户请求核查'}</p>
      )}

      {summary.steps.length > 0 && (
        <div className="tp-handoff-block">
          <h4 className="tp-handoff-h">节点快照</h4>
          <ul className="tp-handoff-list">
            {summary.steps.map((s) => (
              <li key={s.idx}>
                第 {s.idx+1} 步 · {kindLabel[s.kind]||'业务步骤'} — {NODE_STATE_LABEL[s.state] ?? s.state}
              </li>
            ))}
          </ul>
        </div>
      )}

      {summary.operations.length > 0 && (
        <div className="tp-handoff-block">
          <h4 className="tp-handoff-h">交接时的操作快照</h4>
          <ul className="tp-handoff-list">
            {summary.operations.map((op) => (
              <li key={op.id}>
                {op.id} · {operationLabel[op.type]||'业务操作'} · {operationStateLabel[op.status]||'请核对回执'}
              </li>
            ))}
          </ul>
        </div>
      )}

      {summary.checklist.length > 0 && (
        <div className="tp-handoff-block">
          <h4 className="tp-handoff-h">核查清单</h4>
          <ul className="tp-handoff-list tp-handoff-checklist">
            {summary.checklist.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

export default TaskPlanView;
