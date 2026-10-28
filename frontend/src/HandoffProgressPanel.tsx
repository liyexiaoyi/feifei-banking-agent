import type { HandoffProgressView, HandoffStatus } from "./handoff-progress";

type Props = {
  view: HandoffProgressView | null;
  loading: boolean;
  busy: boolean;
  error: string;
  onRefresh: () => void;
  onUpdate: (s: Exclude<HandoffStatus, "OPEN">) => void;
};

const statusText: Record<string, string> = {
  OPEN: "未开始",
  IN_REVIEW: "本地标记核查中",
  CLOSED: "本地记录已关闭",
  UNKNOWN: "待核实",
  EXECUTING: "待核实",
  MANUAL_REVIEW: "待核实",
  PENDING: "待处理",
  COMPLETED: "已完成",
  SUCCEEDED: "已完成",
  FAILED: "未完成",
  WAITING_CONFIRMATION: "等待确认",
  WAITING_VERIFICATION: "等待验证",
  READY: "尚未执行",
  CANCELLED: "已取消",
  EXPIRED: "已过期",
};
const typeText: Record<string, string> = {
  transfer: "转账",
  subscription_query: "订阅查询",
  subscription_cancel: "关闭续费",
  card_report_lost: "卡片挂失",
  card_restore: "卡片解挂",
  virtual_card_create: "申请虚拟演示卡",
  wealth_purchase: "模拟申购",
  wealth_redemption: "模拟赎回",
};

export default function HandoffProgressPanel({
  view, loading, busy, error, onRefresh, onUpdate,
}: Props) {
  const disabled = busy || loading || Boolean(error);
  return (
    <section aria-label="本地核查进度" className="handoff-progress">
      <h2>本地核查记录（演示）</h2>
      <p>未联系任何人工，关闭记录不代表银行已处理。</p>
      <button type="button" disabled={busy||loading} onClick={onRefresh} style={{ minHeight: 48 }}>刷新核查进度</button>
      {loading && <p role="status">正在核对本地记录…</p>}
      {error && <p role="alert">{error}</p>}
      {!view && !error && !loading && <p>暂无本地核查记录。</p>}
      {view && <>
        <p aria-live="polite">{statusText[view.status] ?? "待核实"}</p>
        <h3>当前关联操作</h3>
        <ul>
          {view.operations.map((operation, index) => (
            <li key={index}>{typeText[String(operation.type)] ?? "本地操作"}：{statusText[String(operation.status)] ?? "待核实"}</li>
          ))}
        </ul>
        {view.events.length>0 && <><h3>本地进度记录</h3><ol>{view.events.map(event=><li key={event.id}>{statusText[event.from_status]} → {statusText[event.to_status]} · {new Date(event.created_at*1000).toLocaleString()}</li>)}</ol></>}
        {!error && view.status === "OPEN" && <>
          <button type="button" disabled={disabled} onClick={() => onUpdate("IN_REVIEW")} style={{ minHeight: 48 }}>标记为自行核查中</button>{" "}
          <button type="button" disabled={disabled} onClick={() => onUpdate("CLOSED")} style={{ minHeight: 48 }}>关闭本地记录</button>
        </>}
        {!error && view.status === "IN_REVIEW" && <button type="button" disabled={disabled} onClick={() => onUpdate("CLOSED")} style={{ minHeight: 48 }}>关闭本地记录</button>}
      </>}
    </section>
  );
}
