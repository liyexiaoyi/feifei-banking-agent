/**
 * task-plan.ts — 固定计划/DAG 契约 v2 前端类型
 * 与 PlanStore 公开字段对齐；前端只读展示，不含 token / action 参数。
 */

export type PlanStatus =
  | 'PLANNING'
  | 'RUNNING'
  | 'PAUSED'
  | 'DONE'
  | 'HANDOFF'
  | 'CANCELLED';

export type NodeState =
  | 'PENDING'
  | 'RUNNING'
  | 'PREPARED'
  | 'DONE'
  | 'BLOCKED'
  | 'FAILED'
  | 'ORPHAN';

/** 展示层节点：契约明确只展示 kind/deps/state/error_code，绝不展示 token 与 action 参数 */
export interface TaskPlanNode {
  idx: number;
  kind: string;
  deps: number[];
  state: NodeState;
  error_code: string | null;
}

/**
 * 待处理业务摘要（v3，读接口新增）。
 * 可选以兼容旧快照；旧数据无 business 时不得从 PREPARED 节点猜测仍有业务待确认。
 * - pending_ops: 未过期的等待确认/验证/就绪操作笔数
 * - awaiting_selection: 存在待选收款人的准备节点
 * - actionable: DONE 计划是否仍允许取消/本地核查
 * - attribution_incomplete: 存在未能归属到本计划的旧操作（持久迁移问题），
 *   整项取消不会处理这些操作，需用户在操作记录中逐笔核对
 */
export interface TaskPlanBusiness {
  pending_ops: number;
  awaiting_selection: boolean;
  actionable: boolean;
  attribution_incomplete?: boolean;
}

export interface TaskPlan {
  id: string;
  request_key: string;
  risk_epoch: string;
  version: number;
  status: PlanStatus;
  reason: string | null;
  nodes: TaskPlanNode[];
  business?: TaskPlanBusiness;
}

/** 终止/暂停原因（由后端 reason 给出，前端仅展示原样文案） */
export type PlanControlAction = 'pause' | 'resume' | 'handoff' | 'cancel';

export interface TaskPlanViewProps {
  plan: TaskPlan;
  busy: boolean;
  /** 仅主动点击触发；组件内部不 fetch、不自动重试 */
  onControl: (action: PlanControlAction) => void;
}

/** 本地核查交接摘要：模拟记录，未联系真人 */
export interface LocalHandoffStep {
  idx: number;
  kind: string;
  state: NodeState;
}

export interface LocalHandoffOperation {
  id: string;
  type: string;
  status: string;
}

export interface LocalHandoff {
  id: string;
  plan_id: string;
  created_at: number;
  reason: string | null;
  steps: LocalHandoffStep[];
  operations: LocalHandoffOperation[];
  checklist: string[];
  simulated: true;
  contacted_human: false;
}

export interface LocalHandoffSummaryProps {
  summary: LocalHandoff;
}

/**
 * 计划是否需要用户关注（纯函数，无副作用）。
 * 满足任一条件即返回 true：
 * - business.attribution_incomplete === true（存在未归属旧操作，任意状态下都提示）
 * - 状态为 PAUSED / RUNNING / PLANNING
 * - 状态为 DONE 且 business.actionable === true
 * 旧 DONE 快照无 business 时返回 false，不得从节点状态猜测。
 */
export function planNeedsAttention(plan: TaskPlan): boolean {
  if (plan.business?.attribution_incomplete === true) {
    return true;
  }
  switch (plan.status) {
    case 'PAUSED':
    case 'RUNNING':
    case 'PLANNING':
      return true;
    case 'DONE':
      return plan.business?.actionable === true;
    default:
      return false;
  }
}
